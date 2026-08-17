/**
 * Der Test, der gefehlt hat.
 *
 * `verbunden` war ein `computed` über die Closure-Variable `ws` – für Vue
 * unsichtbar, also einmal ausgewertet und für immer `false`. Folge: „Sprechen"
 * und das Textfeld erschienen nie, obwohl der WebSocket längst offen war. Der
 * Durchstich über `ws_probe.py` prüfte den Kanal am Browser vorbei und konnte
 * das nicht sehen.
 *
 * Hier läuft die Zustandsmaschine gegen einen nachgebildeten WebSocket.
 */

import { defineComponent, h, nextTick } from 'vue'
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useVoiceSession, verteilePegel } from './useVoiceSession'

class FakeWebSocket {
  static letzte: FakeWebSocket | null = null
  static OPEN = 1
  static CLOSED = 3

  readyState = 0
  binaryType = ''
  gesendet: string[] = []

  onopen: (() => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  onmessage: ((event: { data: unknown }) => void) | null = null

  constructor(public url: string) {
    FakeWebSocket.letzte = this
  }

  oeffnen() {
    this.readyState = FakeWebSocket.OPEN
    this.onopen?.()
  }

  empfangen(payload: unknown) {
    this.onmessage?.({ data: typeof payload === 'string' ? payload : JSON.stringify(payload) })
  }

  send(daten: string) {
    this.gesendet.push(daten)
  }

  close() {
    this.readyState = FakeWebSocket.CLOSED
    this.onclose?.()
  }
}

/** Führt ein Composable in einem echten Komponentenkontext aus. */
function inKomponente<T>(fabrik: () => T): T {
  let ergebnis!: T
  mount(
    defineComponent({
      setup() {
        ergebnis = fabrik()
        return () => h('div')
      },
    }),
  )
  return ergebnis
}

beforeEach(() => {
  FakeWebSocket.letzte = null
  vi.stubGlobal('WebSocket', FakeWebSocket)
  // Der Audiokontext wird beim Start angelegt; hier genügt eine Attrappe.
  vi.stubGlobal(
    'AudioContext',
    class {
      state = 'running'
      resume = vi.fn()
      close = vi.fn()
      createBufferSource = vi.fn()
      createAnalyser = vi.fn()
      decodeAudioData = vi.fn()
    },
  )
})

describe('useVoiceSession', () => {
  it('meldet die offene Verbindung an die Oberfläche', async () => {
    const sitzung = inKomponente(() => useVoiceSession())
    expect(sitzung.verbunden.value).toBe(false)

    const starten = sitzung.starten()
    FakeWebSocket.letzte!.oeffnen()
    await starten
    await nextTick()

    // Ohne diese Zeile blieben „Sprechen" und das Textfeld unsichtbar.
    expect(sitzung.verbunden.value).toBe(true)
  })

  it('meldet das Ende der Verbindung', async () => {
    const sitzung = inKomponente(() => useVoiceSession())
    const starten = sitzung.starten()
    FakeWebSocket.letzte!.oeffnen()
    await starten

    FakeWebSocket.letzte!.close()
    await nextTick()

    expect(sitzung.verbunden.value).toBe(false)
    expect(sitzung.status.value).toBe('beendet')
  })

  it('übernimmt die Begrüßung aus dem ready-Rahmen', async () => {
    const sitzung = inKomponente(() => useVoiceSession())
    const starten = sitzung.starten()
    FakeWebSocket.letzte!.oeffnen()
    await starten

    FakeWebSocket.letzte!.empfangen({
      type: 'ready',
      conversation_id: 'abc-123',
      greeting: 'Musterhaus Verwaltung, hier ist Resa.',
    })
    await nextTick()

    expect(sitzung.conversationId.value).toBe('abc-123')
    expect(sitzung.beitraege.value).toHaveLength(1)
    expect(sitzung.beitraege.value[0].sprecher).toBe('resa')
  })

  it('sendet einen Gesprächszug und zeigt ihn im Verlauf', async () => {
    const sitzung = inKomponente(() => useVoiceSession())
    const starten = sitzung.starten()
    FakeWebSocket.letzte!.oeffnen()
    await starten

    sitzung.senden('Bei mir steht Wasser im Keller')
    await nextTick()

    expect(JSON.parse(FakeWebSocket.letzte!.gesendet[0])).toEqual({
      type: 'user_text',
      text: 'Bei mir steht Wasser im Keller',
    })
    expect(sitzung.beitraege.value.at(-1)?.sprecher).toBe('anrufer')
    expect(sitzung.status.value).toBe('denkt')
  })

  it('setzt Textdeltas zu einer Antwort zusammen', async () => {
    const sitzung = inKomponente(() => useVoiceSession())
    const starten = sitzung.starten()
    FakeWebSocket.letzte!.oeffnen()
    await starten

    const ws = FakeWebSocket.letzte!
    ws.empfangen({ type: 'ready', conversation_id: 'x', greeting: 'Hallo.' })
    ws.empfangen({ type: 'assistant_delta', text: 'Guten ' })
    ws.empfangen({ type: 'assistant_delta', text: 'Tag.' })
    ws.empfangen({ type: 'turn_done' })
    await nextTick()

    const letzter = sitzung.beitraege.value.at(-1)!
    expect(letzter.text).toBe('Guten Tag.')
    expect(sitzung.beitraege.value[0].text).toBe('Hallo.')
    expect(letzter.laufend).toBe(false)
    expect(sitzung.status.value).toBe('bereit')
  })

  it('zeigt eine Fehlermeldung des Servers an', async () => {
    const sitzung = inKomponente(() => useVoiceSession())
    const starten = sitzung.starten()
    FakeWebSocket.letzte!.oeffnen()
    await starten

    FakeWebSocket.letzte!.empfangen({
      type: 'error',
      detail: 'Für diese Stunde ist das Limit an Gesprächen erreicht.',
    })
    await nextTick()

    expect(sitzung.fehler.value).toContain('Limit')
    expect(sitzung.status.value).toBe('fehler')
  })

  it('macht die Alarmierung im Verlauf sichtbar', async () => {
    const sitzung = inKomponente(() => useVoiceSession())
    const starten = sitzung.starten()
    FakeWebSocket.letzte!.oeffnen()
    await starten

    FakeWebSocket.letzte!.empfangen({
      type: 'tool',
      name: 'escalate_emergency',
      signals: ['escalated'],
    })
    await nextTick()

    const systemhinweis = sitzung.beitraege.value.find((b) => b.sprecher === 'system')
    expect(systemhinweis?.text).toContain('Notdienst')
  })
})


describe('verteilePegel', () => {
  it('bespielt jeden Balken, auch bei wenigen Frequenzbändern', () => {
    // Der eigentliche Fehler: 32 Bänder, 28 Balken, fester Faktor 2 – die
    // hintere Hälfte griff ins Leere und stand dauerhaft still.
    const daten = new Uint8Array(32).fill(200)
    const pegel = verteilePegel(daten, 28)

    expect(pegel).toHaveLength(28)
    expect(pegel.every((p) => p > 0.06)).toBe(true)
  })

  it('lässt bei Stille alle Balken auf dem Ruhewert', () => {
    const pegel = verteilePegel(new Uint8Array(64).fill(0), 28)
    expect(pegel.every((p) => p === 0.06)).toBe(true)
  })

  it('bildet laute Bänder auf hohe Balken ab und leise auf niedrige', () => {
    const daten = new Uint8Array(64)
    daten.fill(220, 0, 32) // vordere Hälfte laut
    daten.fill(10, 32, 64) // hintere Hälfte leise

    const pegel = verteilePegel(daten, 28)
    const vorne = pegel.slice(0, 12)
    const hinten = pegel.slice(16)

    expect(Math.min(...vorne)).toBeGreaterThan(Math.max(...hinten))
  })

  it('überschreitet nie den Maximalwert', () => {
    const pegel = verteilePegel(new Uint8Array(64).fill(255), 28)
    expect(Math.max(...pegel)).toBe(1)
  })
})
