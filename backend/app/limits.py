"""Missbrauchsschutz für den öffentlichen Gesprächskanal.

Die Konsole ist ohne Anmeldung erreichbar – anders ergäbe sie keinen Sinn,
niemand bekommt vor einem Anruf Zugangsdaten. Damit ist sie aber ein Endpunkt,
der bei jedem Aufruf Geld kostet: Anthropic pro Token, ElevenLabs pro Zeichen.

Die Grenzen unten sind bewusst großzügig für echte Nutzung und eng genug, dass
ein Skript nicht in Minuten ein Guthaben leerräumt. Der Zähler lebt im Prozess;
mit mehreren Workern gehörte er nach Redis.
"""

import time
from collections import defaultdict, deque
from dataclasses import dataclass

# Je Herkunft (IP)
MAX_SESSIONS_PER_HOUR = 12
MAX_PARALLEL_SESSIONS = 3

# Je Gespräch
MAX_TURNS = 30
MAX_TEXT_LENGTH = 1000

# Insgesamt, als Notbremse gegen verteilte Zugriffe
MAX_GLOBAL_PARALLEL = 25


@dataclass(slots=True)
class Denied:
    reason: str


class SessionLimiter:
    def __init__(self) -> None:
        self._starts: dict[str, deque[float]] = defaultdict(deque)
        self._parallel: dict[str, int] = defaultdict(int)
        self._global = 0

    def _prune(self, key: str, now: float) -> None:
        fenster = self._starts[key]
        while fenster and now - fenster[0] > 3600:
            fenster.popleft()

    def acquire(self, key: str) -> Denied | None:
        now = time.monotonic()
        self._prune(key, now)

        if self._global >= MAX_GLOBAL_PARALLEL:
            return Denied(
                "Gerade sind sehr viele Gespräche gleichzeitig aktiv. "
                "Bitte versuche es in ein paar Minuten erneut."
            )
        if self._parallel[key] >= MAX_PARALLEL_SESSIONS:
            return Denied(
                "Aus deinem Netz laufen bereits mehrere Gespräche gleichzeitig. "
                "Bitte beende eines davon."
            )
        if len(self._starts[key]) >= MAX_SESSIONS_PER_HOUR:
            return Denied(
                "Für diese Stunde ist das Limit an Gesprächen erreicht. "
                "Bitte später noch einmal versuchen."
            )

        self._starts[key].append(now)
        self._parallel[key] += 1
        self._global += 1
        return None

    def release(self, key: str) -> None:
        self._parallel[key] = max(0, self._parallel[key] - 1)
        self._global = max(0, self._global - 1)

    @property
    def active(self) -> int:
        return self._global


limiter = SessionLimiter()
