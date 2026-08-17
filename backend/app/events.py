"""Live-Benachrichtigung ans Dashboard.

Ein Fall, der auftaucht, *während* noch gesprochen wird, ist der überzeugendste
Moment der Anwendung. Dafür genügt ein einfacher Verteiler im Prozess –
Broker-Infrastruktur wäre hier Selbstzweck.

Bei mehreren Workern sähe jeder nur seine eigenen Ereignisse. Sobald das
relevant wird, tritt hier Postgres LISTEN/NOTIFY an die Stelle des Verteilers;
die Schnittstelle bleibt dieselbe.
"""

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable

logger = logging.getLogger(__name__)

# Langsame Abnehmer sollen den Verteiler nicht aufhalten; volle Puffer verlieren
# lieber ein Ereignis, als den Gesprächszug zu blockieren.
QUEUE_SIZE = 64


class Broadcaster:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()

    async def publish(self, payload: dict) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                logger.debug("Abnehmer hängt hinterher, Ereignis verworfen")

    @contextlib.asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


broadcaster = Broadcaster()


async def sse_stream(
    queue: asyncio.Queue,
    getrennt: Callable[[], Awaitable[bool]] | None = None,
    heartbeat: float = 25.0,
) -> AsyncIterator[str]:
    """Formt Ereignisse als Server-Sent Events.

    Der Herzschlag hält Verbindungen offen, die sonst von Proxys nach kurzer
    Stille gekappt werden – und dient zugleich als Taktgeber, um zu prüfen, ob
    der Client noch da ist.

    Ohne diese Prüfung liefe der Generator ewig weiter: Der Server wartet beim
    Herunterfahren auf offene Verbindungen, und ein Neustart bliebe hängen.
    """
    yield ": verbunden\n\n"
    while True:
        try:
            payload = await asyncio.wait_for(queue.get(), timeout=heartbeat)
        except TimeoutError:
            if getrennt is not None and await getrennt():
                return
            yield ": ping\n\n"
            continue
        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
