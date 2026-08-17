"""Satzgrenzen – die deutschen Sonderfälle sind hier der eigentliche Inhalt."""

from app.core.sentences import SentenceBuffer


def _feed_all(text: str, chunk: int = 3) -> tuple[list[str], str]:
    """Simuliert das zeichenweise Eintreffen der Deltas."""
    buffer = SentenceBuffer()
    out: list[str] = []
    for i in range(0, len(text), chunk):
        out.extend(buffer.feed(text[i : i + chunk]))
    return out, buffer.flush()


def test_einfache_saetze_werden_getrennt():
    sentences, rest = _feed_all(
        "Das Büro ist bis sechzehn Uhr besetzt. Kann ich Ihnen sonst helfen?"
    )
    assert sentences == [
        "Das Büro ist bis sechzehn Uhr besetzt.",
        "Kann ich Ihnen sonst helfen?",
    ]
    assert rest == ""


def test_saetze_gehen_sofort_raus_nicht_erst_beim_flush():
    """Ein abgeschlossener Satz soll die Sprachausgabe sofort erreichen, auch
    wenn der Stream noch weiterläuft."""
    buffer = SentenceBuffer()
    assert buffer.feed("Ich habe den Notdienst alarmiert.") == ["Ich habe den Notdienst alarmiert."]
    assert buffer.pending == ""


def test_hausnummer_wird_bewusst_nicht_getrennt():
    """'Lindenstraße 14. Stimmt das so?' – der Punkt beendet Hausnummer *und*
    Satz zugleich. Das ist ohne Kontext nicht entscheidbar.

    Falsch zu trennen zerschneidet eine Adresse hörbar mitten im Sprechen;
    nicht zu trennen kostet nur einen größeren Sprachbaustein. Also lieber
    zusammenlassen.
    """
    sentences, rest = _feed_all("Ich notiere Lindenstraße 14. Stimmt das so?")
    assert sentences == ["Ich notiere Lindenstraße 14. Stimmt das so?"]
    assert rest == ""


def test_hausnummer_mitten_im_satz_trennt_nicht_vorzeitig():
    sentences, _ = _feed_all(
        "Ich notiere Lindenstraße 14. Die Kollegen melden sich morgen bei Ihnen."
    )
    assert sentences[0].startswith("Ich notiere Lindenstraße 14.")


def test_abkuerzung_beendet_keinen_satz():
    sentences, rest = _feed_all("Das gilt z. B. für Wasserschäden im Keller. Alles klar?")
    assert len(sentences) == 1
    assert sentences[0] == "Das gilt z. B. für Wasserschäden im Keller."
    assert rest == "Alles klar?"


def test_initiale_beendet_keinen_satz():
    sentences, _ = _feed_all("Ich verbinde Sie mit A. Kaiser von der Bereitschaft. Moment bitte.")
    assert sentences[0] == "Ich verbinde Sie mit A. Kaiser von der Bereitschaft."


def test_frage_und_ausruf_trennen():
    sentences, rest = _feed_all(
        "Ist jemand verletzt? Bitte gehen Sie sofort aus dem Raum! Ich alarmiere den Notdienst."
    )
    assert sentences == [
        "Ist jemand verletzt?",
        "Bitte gehen Sie sofort aus dem Raum!",
        "Ich alarmiere den Notdienst.",
    ]
    assert rest == ""


def test_sehr_kurze_saetze_werden_zusammengefasst():
    """Einzelne Wörter lohnen keinen eigenen Aufruf an die Sprachausgabe."""
    sentences, rest = _feed_all("Ja. Gerne. Ich nehme das für Sie auf und melde es weiter.")
    assert all(len(s) >= 16 for s in sentences), sentences


def test_flush_gibt_rest_ohne_satzzeichen():
    buffer = SentenceBuffer()
    assert buffer.feed("Einen Moment bitte") == []
    assert buffer.flush() == "Einen Moment bitte"
    assert buffer.flush() == ""


def test_anfuehrungszeichen_nach_dem_punkt():
    sentences, _ = _feed_all('Sie sagten "es tropft aus der Decke." Ich notiere das so.')
    assert sentences[0].endswith('"') or sentences[0].endswith(".")


def test_reihenfolge_bleibt_erhalten():
    text = (
        "Ich habe den Notdienst alarmiert. "
        "Er ist per E-Mail informiert. "
        "Wie lautet Ihre Rückrufnummer?"
    )
    sentences, rest = _feed_all(text, chunk=1)
    assert sentences + ([rest] if rest else []) == [
        "Ich habe den Notdienst alarmiert.",
        "Er ist per E-Mail informiert.",
        "Wie lautet Ihre Rückrufnummer?",
    ]
