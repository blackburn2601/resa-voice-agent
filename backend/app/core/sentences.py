"""Satzgrenzen im Textstrom.

Claudes Antwort kommt zeichenweise. Auf den ganzen Text zu warten, bevor die
Sprachausgabe beginnt, verdoppelt die gefühlte Wartezeit – also wird jeder
fertige Satz sofort weitergereicht.

Naives Trennen am Punkt geht auf Deutsch schief: "Lindenstraße 14." ist kein
Satzende, "z. B." auch nicht. Beides kommt in diesem Anwendungsfall ständig vor.
"""

import re

# Abkürzungen, nach denen ein Punkt keinen Satz beendet.
ABBREVIATIONS = {
    "z",
    "b",
    "bzw",
    "ca",
    "evtl",
    "ggf",
    "inkl",
    "max",
    "min",
    "nr",
    "u",
    "a",
    "usw",
    "vgl",
    "z.b",
    "d.h",
    "u.a",
    "s",
    "str",
    "tel",
    "hr",
    "fr",
    "dr",
    "prof",
    "ing",
    "abs",
    "art",
    "bzgl",
    "etc",
    "geb",
    "gem",
    "lt",
    "mtl",
    "sog",
    "uvm",
    "zzgl",
    "eg",
    "og",
    "ug",
}

_BOUNDARY = re.compile(r"([.!?…])([\"')\]]*)(\s+|$)")

# Kürzere Bruchstücke lohnen keinen eigenen Aufruf an die Sprachausgabe – der
# Mehraufwand pro Anfrage wiegt den Zeitgewinn nicht auf.
MIN_CHARS = 16


def _is_boundary(text: str, end: int) -> bool:
    """Prüft, ob der Punkt an Position `end-1` wirklich einen Satz beendet."""
    before = text[:end].rstrip(".!?…\"')]")
    if not before:
        return False

    last = before.rsplit(None, 1)[-1] if before.split() else ""
    token = last.strip("(\"'").lower().rstrip(".")

    # "14." – eine Ordnungszahl oder Hausnummer, kein Satzende.
    if token.isdigit():
        return False
    if token in ABBREVIATIONS:
        return False
    # Einzelner Buchstabe mit Punkt: Initiale wie "A. Kaiser".
    if len(token) == 1 and token.isalpha():
        return False
    return True


class SentenceBuffer:
    """Sammelt Deltas und gibt fertige Sätze heraus."""

    def __init__(self, min_chars: int = MIN_CHARS) -> None:
        self._buffer = ""
        self._min_chars = min_chars

    def feed(self, delta: str) -> list[str]:
        self._buffer += delta
        return self._extract()

    def _extract(self) -> list[str]:
        out: list[str] = []
        while True:
            match = None
            for candidate in _BOUNDARY.finditer(self._buffer):
                if not _is_boundary(self._buffer, candidate.end(2)):
                    continue
                # Zu kurz: nicht den Puffer umbauen, sondern zur *nächsten*
                # Grenze weitergehen. Andernfalls fände die nächste Runde
                # dieselbe Stelle wieder – eine Endlosschleife.
                if len(self._buffer[: candidate.end(2)].strip()) < self._min_chars:
                    continue
                match = candidate
                break
            if match is None:
                break

            sentence = self._buffer[: match.end(2)].strip()
            self._buffer = self._buffer[match.end() :]
            if sentence:
                out.append(sentence)
        return out

    def flush(self) -> str:
        """Gibt den Rest heraus – am Ende des Streams."""
        rest = self._buffer.strip()
        self._buffer = ""
        return rest

    @property
    def pending(self) -> str:
        return self._buffer
