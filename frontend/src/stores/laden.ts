/**
 * Globaler Ladezustand.
 *
 * Zwei Eigenheiten, die den Unterschied zwischen „hilfreich" und „nervös"
 * ausmachen:
 *
 *  - **Verzögertes Einblenden.** Ein Wechsel, der 80 ms dauert, soll keinen
 *    Vollbildschirm aufblitzen lassen. Der Schirm erscheint erst, wenn es
 *    wirklich dauert.
 *  - **Mindeststandzeit.** Erscheint er doch, bleibt er kurz stehen. Sonst
 *    zuckt er sichtbar und wirkt wie ein Fehler.
 *
 * Gezählt wird, nicht geschaltet: Mehrere gleichzeitige Vorgänge sollen sich
 * nicht gegenseitig ausblenden.
 */

import { defineStore } from 'pinia'
import { ref } from 'vue'

export const EINBLENDE_VERZOEGERUNG = 180
export const MINDEST_STANDZEIT = 320

export const useLaden = defineStore('laden', () => {
  const offen = ref(0)
  const sichtbar = ref(false)
  const text = ref<string | null>(null)

  let einblendeUhr: ReturnType<typeof setTimeout> | null = null
  let sichtbarSeit = 0

  function starten(beschriftung?: string) {
    offen.value += 1
    if (beschriftung) text.value = beschriftung
    if (offen.value > 1 || sichtbar.value || einblendeUhr) return

    einblendeUhr = setTimeout(() => {
      einblendeUhr = null
      // In der Zwischenzeit fertig geworden – dann gar nicht erst zeigen.
      if (offen.value === 0) return
      sichtbar.value = true
      sichtbarSeit = Date.now()
    }, EINBLENDE_VERZOEGERUNG)
  }

  function beenden() {
    offen.value = Math.max(0, offen.value - 1)
    if (offen.value > 0) return

    if (einblendeUhr) {
      clearTimeout(einblendeUhr)
      einblendeUhr = null
    }
    if (!sichtbar.value) {
      text.value = null
      return
    }

    const stand = Date.now() - sichtbarSeit
    const rest = Math.max(0, MINDEST_STANDZEIT - stand)
    setTimeout(() => {
      // Nur ausblenden, wenn inzwischen nichts Neues begonnen hat.
      if (offen.value === 0) {
        sichtbar.value = false
        text.value = null
      }
    }, rest)
  }

  /** Klammert einen Vorgang – auch wenn er scheitert. */
  async function waehrend<T>(aufgabe: () => Promise<T>, beschriftung?: string): Promise<T> {
    starten(beschriftung)
    try {
      return await aufgabe()
    } finally {
      beenden()
    }
  }

  return { sichtbar, text, offen, starten, beenden, waehrend }
})
