"""Werkzeugschleife des Agenten – mit gefälschtem Anthropic-Client.

Ohne API-Schlüssel ist das der einzige Weg, die Schleife zu prüfen: dass
Werkzeuge ausgeführt werden, dass ihre Ergebnisse als *eine* Nachricht
zurückgehen und dass der Verlauf danach vollständig gespeichert wird.
"""

import copy
from types import SimpleNamespace

import pytest

from app.core import agent as agent_module
from app.core import tools as tools_module
from app.core.agent import TextDelta, ToolInvoked, TurnComplete, stream_turn
from app.core.conversation import ConversationService
from app.core.tools import ToolContext
from app.db.models import OnCallContact


class _Block(SimpleNamespace):
    def model_dump(self, **_: object) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


def _text_block(text: str) -> _Block:
    return _Block(type="text", text=text)


def _tool_block(block_id: str, name: str, payload: dict) -> _Block:
    return _Block(type="tool_use", id=block_id, name=name, input=payload)


def _delta(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        type="content_block_delta", delta=SimpleNamespace(type="text_delta", text=text)
    )


class _FakeStream:
    def __init__(self, events: list, final: SimpleNamespace) -> None:
        self._events = events
        self._final = final

    async def __aenter__(self) -> "_FakeStream":
        return self

    async def __aexit__(self, *_: object) -> bool:
        return False

    async def __aiter__(self):
        for event in self._events:
            yield event

    async def get_final_message(self) -> SimpleNamespace:
        return self._final


class _FakeClient:
    """Gibt je Runde eine vorbereitete Antwort zurück und merkt sich die Anfragen."""

    def __init__(self, rounds: list[tuple[list, SimpleNamespace]]) -> None:
        self._rounds = rounds
        self.requests: list[dict] = []
        self.messages = SimpleNamespace(stream=self._stream)

    def _stream(self, **kwargs):
        # Tiefe Kopie: `messages` ist über die Runden hinweg dieselbe Liste und
        # wird fortlaufend erweitert – ohne Kopie sähe jede aufgezeichnete
        # Anfrage den Endzustand.
        self.requests.append(copy.deepcopy(kwargs))
        events, final = self._rounds.pop(0)
        return _FakeStream(events, final)


@pytest.fixture
def fake_client(monkeypatch):
    def install(rounds: list[tuple[list, SimpleNamespace]]) -> _FakeClient:
        client = _FakeClient(rounds)
        monkeypatch.setattr(agent_module, "_client", lambda: client)
        return client

    return install


@pytest.fixture(autouse=True)
def _offline_search(monkeypatch):
    """Die Wissenssuche wird hier nicht geprüft – und die Tests sollen nicht an
    einer erreichbaren Ollama-Instanz hängen."""

    async def fake_search(query: str, limit: int = 4):
        return []

    monkeypatch.setattr(tools_module.retrieve, "search", fake_search)


async def test_einfacher_zug_streamt_und_speichert(session, fake_client):
    fake_client(
        [
            (
                [_delta("Guten "), _delta("Tag.")],
                SimpleNamespace(content=[_text_block("Guten Tag.")], stop_reason="end_turn"),
            )
        ]
    )

    service = ConversationService(session)
    conversation = await service.start()

    events = [e async for e in service.handle_turn(conversation.id, "Hallo")]

    deltas = [e.text for e in events if isinstance(e, TextDelta)]
    assert deltas == ["Guten ", "Tag."]

    done = events[-1]
    assert isinstance(done, TurnComplete)
    assert done.text == "Guten Tag."

    history = await service._history(conversation.id)
    assert [m["role"] for m in history] == ["user", "assistant"]


async def test_werkzeugaufruf_wird_ausgefuehrt_und_zurueckgespielt(session, fake_client):
    """Runde 1 ruft ein Werkzeug, Runde 2 formuliert die Antwort."""
    session.add(OnCallContact(name="Bereitschaft", email="notdienst@example.de"))
    await session.flush()

    client = fake_client(
        [
            (
                [],
                SimpleNamespace(
                    content=[
                        _tool_block(
                            "tu_1",
                            "escalate_emergency",
                            {
                                "kurzbeschreibung": "Wasseraustritt im Keller",
                                "zusammenfassung": "Wasser läuft aus der Heizung",
                                "gefahr_im_verzug": False,
                            },
                        )
                    ],
                    stop_reason="tool_use",
                ),
            ),
            (
                [_delta("Der Notdienst ist informiert.")],
                SimpleNamespace(
                    content=[_text_block("Der Notdienst ist informiert.")],
                    stop_reason="end_turn",
                ),
            ),
        ]
    )

    service = ConversationService(session)
    conversation = await service.start()

    events = [
        e async for e in service.handle_turn(conversation.id, "Bei mir im Keller steht Wasser")
    ]

    invoked = [e for e in events if isinstance(e, ToolInvoked)]
    assert [e.name for e in invoked] == ["escalate_emergency"]

    # Die zweite Anfrage muss das tool_result enthalten – sonst weiß das Modell
    # nicht, ob die Alarmierung geklappt hat.
    zweite_runde = client.requests[1]["messages"]
    letzte = zweite_runde[-1]
    assert letzte["role"] == "user"
    assert letzte["content"][0]["type"] == "tool_result"
    assert letzte["content"][0]["tool_use_id"] == "tu_1"

    # Vier Nachrichten: Begrüßung, Nutzer, tool_use, tool_result, Antwort
    history = await service._history(conversation.id)
    assert [m["role"] for m in history] == ["user", "assistant", "user", "assistant"]


async def test_mehrere_werkzeuge_gehen_in_einer_nachricht_zurueck(session, fake_client):
    """Getrennte tool_result-Nachrichten gewöhnen dem Modell parallele Aufrufe ab."""
    client = fake_client(
        [
            (
                [],
                SimpleNamespace(
                    content=[
                        _tool_block("tu_1", "search_knowledge", {"query": "Bürozeiten"}),
                        _tool_block("tu_2", "search_knowledge", {"query": "Notdienst"}),
                    ],
                    stop_reason="tool_use",
                ),
            ),
            (
                [_delta("Fertig.")],
                SimpleNamespace(content=[_text_block("Fertig.")], stop_reason="end_turn"),
            ),
        ]
    )

    service = ConversationService(session)
    conversation = await service.start()
    [e async for e in service.handle_turn(conversation.id, "Wann habt ihr auf?")]

    letzte = client.requests[1]["messages"][-1]
    assert len(letzte["content"]) == 2
    assert {b["tool_use_id"] for b in letzte["content"]} == {"tu_1", "tu_2"}


async def test_systemprompt_ist_cachebar_markiert(session, fake_client):
    client = fake_client(
        [
            (
                [_delta("Ja.")],
                SimpleNamespace(content=[_text_block("Ja.")], stop_reason="end_turn"),
            )
        ]
    )

    ctx = ToolContext(session=session, conversation_id=1)
    history = [{"role": "user", "content": [{"type": "text", "text": "Hallo"}]}]
    [e async for e in stream_turn(history, ctx)]

    system = client.requests[0]["system"]
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    assert client.requests[0]["thinking"] == {"type": "adaptive"}
    assert client.requests[0]["extra_body"]["output_config"]["effort"] == "low"


async def test_schleife_bricht_nach_obergrenze_ab(session, fake_client):
    """Ein Modell, das endlos Werkzeuge ruft, darf das Gespräch nicht aufhängen."""
    endlos = [
        (
            [],
            SimpleNamespace(
                content=[_tool_block(f"tu_{i}", "search_knowledge", {"query": "x"})],
                stop_reason="tool_use",
            ),
        )
        for i in range(agent_module.MAX_TOOL_ROUNDS)
    ]
    fake_client(endlos)

    service = ConversationService(session)
    conversation = await service.start()
    events = [e async for e in service.handle_turn(conversation.id, "Hallo")]

    assert isinstance(events[-1], TurnComplete)
