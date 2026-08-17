/**
 * Live-Strom vom Server.
 *
 * Ein Fall, der auftaucht, während noch gesprochen wird, ist der Moment, der
 * die Anwendung erklärt. Die Ansichten hängen sich an `stand` und laden neu,
 * sobald sich etwas ändert.
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useEvents = defineStore('events', () => {
  const stand = ref(0)
  const verbunden = ref(false)
  let quelle: EventSource | null = null

  function verbinden() {
    if (quelle) return
    quelle = new EventSource('/api/events', { withCredentials: true })
    quelle.onopen = () => (verbunden.value = true)
    quelle.onmessage = () => (stand.value += 1)
    quelle.onerror = () => {
      verbunden.value = false
      // EventSource verbindet von selbst neu; kein eigener Wiederholmechanismus.
    }
  }

  function trennen() {
    quelle?.close()
    quelle = null
    verbunden.value = false
  }

  return { stand, verbunden, verbinden, trennen }
})
