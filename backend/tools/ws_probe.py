"""Durchstich über den WebSocket – ohne Browser.

Meldet sich an, führt ein Gespräch und misst, wann der erste Ton da ist. Das
ist die Zahl, die im Gespräch zählt: nicht wie lange die ganze Antwort braucht,
sondern wann Resa anfängt zu sprechen.

    python tools/ws_probe.py "Bei mir im Keller steht Wasser"
"""

import asyncio
import json
import pathlib
import sys
import time

import httpx
import websockets

BASE = "http://api:8000"
WS = "ws://api:8000/ws/web"
EMAIL = "dev@edv-wagner.com"
PASSWORD = "resa-demo-2026"
OUT = pathlib.Path("/app/data/ws-probe")


async def main(turns: list[str]) -> int:
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as http:
        response = await http.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
        if response.status_code != 200:
            print(f"Anmeldung fehlgeschlagen: {response.status_code} {response.text}")
            return 1
        cookie = response.cookies.get("resa_session")
        print(f"Angemeldet als {EMAIL}")

    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.mp3"):
        old.unlink()

    async with websockets.connect(
        WS, additional_headers={"Cookie": f"resa_session={cookie}"}, max_size=16 * 1024 * 1024
    ) as ws:
        pending_speech: dict | None = None
        started = time.monotonic()
        first_audio: float | None = None
        turn_index = 0

        async def drain_until_done() -> None:
            nonlocal pending_speech, first_audio
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=120)
                if isinstance(raw, bytes):
                    if pending_speech is None:
                        print("  ! Audio ohne Ankündigung")
                        continue
                    if first_audio is None:
                        first_audio = time.monotonic() - started
                        print(f"\n  ⏱  erster Ton nach {first_audio * 1000:.0f} ms")
                    name = OUT / f"{turn_index:02d}-{pending_speech['seq']:02d}.mp3"
                    name.write_bytes(raw)
                    flag = "cache" if pending_speech.get("cached") else "neu"
                    print(f"  ♪ [{flag}] {len(raw):>6} B  {pending_speech['text'][:60]}")
                    pending_speech = None
                    continue

                msg = json.loads(raw)
                kind = msg["type"]
                if kind == "ready":
                    print(f"Gespräch {msg['conversation_id']}")
                elif kind == "assistant_delta":
                    print(msg["text"], end="", flush=True)
                elif kind == "speech":
                    pending_speech = msg
                elif kind == "speech_failed":
                    print(f"\n  ! Sprachausgabe: {msg['detail']}")
                elif kind == "tool":
                    print(f"\n  [{msg['name']} → {msg['signals']}]")
                elif kind == "turn_done":
                    return
                elif kind == "error":
                    print(f"\n  FEHLER: {msg['detail']}")
                    return

        # Begrüßung abwarten (kommt ohne turn_done)
        try:
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=60)
                if isinstance(raw, bytes):
                    if pending_speech:
                        (OUT / "00-greeting.mp3").write_bytes(raw)
                        flag = "cache" if pending_speech.get("cached") else "neu"
                        print(f"  ♪ [{flag}] Begrüßung, {len(raw)} B")
                        pending_speech = None
                    break
                msg = json.loads(raw)
                if msg["type"] == "ready":
                    print(f"Gespräch {msg['conversation_id']}")
                elif msg["type"] == "speech":
                    pending_speech = msg
        except TimeoutError:
            print("Keine Begrüßung erhalten")

        for index, text in enumerate(turns, start=1):
            turn_index = index
            print(f"\n\nAnrufer: {text}")
            print("Resa:    ", end="", flush=True)
            started = time.monotonic()
            first_audio = None
            await ws.send(json.dumps({"type": "user_text", "text": text}))
            await drain_until_done()

        await ws.send(json.dumps({"type": "end"}))

    print(f"\n\nAudiodateien: {OUT}")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:] or [
        "Guten Abend, bei mir im Keller steht Wasser, das kommt aus der Heizung!",
        "Kaiser, Anna Kaiser, Lindenstraße vierzehn in Köln.",
    ]
    sys.exit(asyncio.run(main(args)))
