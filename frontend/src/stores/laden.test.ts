/**
 * Der Ladeschirm ist nur dann eine Hilfe, wenn er sich zurückhält.
 *
 * Die beiden Regeln – verzögertes Einblenden und Mindeststandzeit – sind reine
 * Zeitlogik und brechen lautlos. Deshalb hier mit gestellter Uhr geprüft.
 */

import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { EINBLENDE_VERZOEGERUNG, MINDEST_STANDZEIT, useLaden } from './laden'

beforeEach(() => {
  setActivePinia(createPinia())
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('useLaden', () => {
  it('zeigt bei kurzen Vorgängen gar nichts', () => {
    const laden = useLaden()
    laden.starten()
    vi.advanceTimersByTime(EINBLENDE_VERZOEGERUNG - 40)
    laden.beenden()
    vi.advanceTimersByTime(1000)

    // Ein Wechsel in 140 ms darf keinen Vollbildschirm aufblitzen lassen.
    expect(laden.sichtbar).toBe(false)
  })

  it('erscheint, wenn es wirklich dauert', () => {
    const laden = useLaden()
    laden.starten()
    vi.advanceTimersByTime(EINBLENDE_VERZOEGERUNG + 10)

    expect(laden.sichtbar).toBe(true)
  })

  it('bleibt nach dem Erscheinen kurz stehen', () => {
    const laden = useLaden()
    laden.starten()
    vi.advanceTimersByTime(EINBLENDE_VERZOEGERUNG + 10)
    expect(laden.sichtbar).toBe(true)

    laden.beenden()
    // Sofortiges Ausblenden würde sichtbar zucken.
    vi.advanceTimersByTime(MINDEST_STANDZEIT - 60)
    expect(laden.sichtbar).toBe(true)

    vi.advanceTimersByTime(120)
    expect(laden.sichtbar).toBe(false)
  })

  it('zählt gleichzeitige Vorgänge, statt zu schalten', () => {
    const laden = useLaden()
    laden.starten()
    laden.starten()
    vi.advanceTimersByTime(EINBLENDE_VERZOEGERUNG + 10)

    laden.beenden()
    vi.advanceTimersByTime(MINDEST_STANDZEIT + 100)
    // Der zweite Vorgang läuft noch – ausblenden wäre falsch.
    expect(laden.sichtbar).toBe(true)

    laden.beenden()
    vi.advanceTimersByTime(MINDEST_STANDZEIT + 100)
    expect(laden.sichtbar).toBe(false)
  })

  it('übernimmt eine Beschriftung und räumt sie wieder ab', () => {
    const laden = useLaden()
    laden.starten('Index wird abgeglichen')
    vi.advanceTimersByTime(EINBLENDE_VERZOEGERUNG + 10)
    expect(laden.text).toBe('Index wird abgeglichen')

    laden.beenden()
    vi.advanceTimersByTime(MINDEST_STANDZEIT + 100)
    expect(laden.text).toBeNull()
  })

  it('beendet auch, wenn die Aufgabe scheitert', async () => {
    const laden = useLaden()
    vi.useRealTimers()

    await expect(
      laden.waehrend(async () => {
        throw new Error('kaputt')
      }),
    ).rejects.toThrow('kaputt')

    // Ohne finally bliebe der Zähler stehen und der Schirm für immer offen.
    expect(laden.offen).toBe(0)
  })

  it('bleibt nicht hängen, wenn ein neuer Vorgang beim Ausblenden beginnt', () => {
    const laden = useLaden()
    laden.starten()
    vi.advanceTimersByTime(EINBLENDE_VERZOEGERUNG + 10)
    laden.beenden()

    // Noch während der Mindeststandzeit startet der nächste Wechsel.
    vi.advanceTimersByTime(50)
    laden.starten()
    vi.advanceTimersByTime(MINDEST_STANDZEIT + 200)

    expect(laden.sichtbar).toBe(true)
  })
})
