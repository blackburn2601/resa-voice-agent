<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { api, type ConversationItem, type Stats, type Urgency } from '@/api/client'
import { useEvents } from '@/stores/events'

const router = useRouter()
const events = useEvents()

const gespraeche = ref<ConversationItem[]>([])
const kennzahlen = ref<Stats | null>(null)
const filter = ref<Urgency | 'alle'>('alle')
const laedt = ref(true)
const fehler = ref<string | null>(null)

async function laden() {
  try {
    const [liste, zahlen] = await Promise.all([
      api.conversations(filter.value === 'alle' ? {} : { urgency: filter.value }),
      api.stats(),
    ])
    gespraeche.value = liste
    kennzahlen.value = zahlen
    fehler.value = null
  } catch (ausnahme) {
    fehler.value = ausnahme instanceof Error ? ausnahme.message : 'Laden fehlgeschlagen.'
  } finally {
    laedt.value = false
  }
}

onMounted(laden)
watch(filter, laden)
// Neue Fälle erscheinen während des Gesprächs – ohne Neuladen der Seite.
watch(() => events.stand, laden)

/* Notfälle stehen oben. Wer diese Liste öffnet, sucht zuerst danach – nicht
   nach dem, was zufällig zuletzt eingegangen ist. */
const sortiert = computed(() => {
  const rang: Record<string, number> = { notfall: 0, dringend: 1, normal: 2 }
  return [...gespraeche.value].sort((a, b) => {
    const ra = rang[a.urgency ?? 'normal'] ?? 3
    const rb = rang[b.urgency ?? 'normal'] ?? 3
    if (ra !== rb) return ra - rb
    return b.started_at.localeCompare(a.started_at)
  })
})

const filterOptionen: Array<{ wert: Urgency | 'alle'; label: string }> = [
  { wert: 'alle', label: 'Alle' },
  { wert: 'notfall', label: 'Notfälle' },
  { wert: 'dringend', label: 'Dringend' },
  { wert: 'normal', label: 'Normal' },
]

function zeit(iso: string) {
  return new Date(iso).toLocaleString('de-DE', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}
</script>

<template>
  <div class="seite">
    <header class="kopf">
      <h1>Erfasste Anrufe</h1>
      <div class="filter">
        <button
          v-for="option in filterOptionen"
          :key="option.wert"
          class="knopf"
          :class="{ haupt: filter === option.wert }"
          type="button"
          @click="filter = option.wert"
        >
          {{ option.label }}
        </button>
      </div>
    </header>

    <div v-if="kennzahlen" class="kennzahlen">
      <div class="karte zahl">
        <span class="wert mono">{{ kennzahlen.gespraeche }}</span>
        <span class="still">Gespräche</span>
      </div>
      <div class="karte zahl" :class="{ betont: kennzahlen.notfaelle > 0 }">
        <span class="wert mono">{{ kennzahlen.notfaelle }}</span>
        <span class="still">Notfälle</span>
      </div>
      <div class="karte zahl">
        <span class="wert mono">{{ kennzahlen.alarmierungen }}</span>
        <span class="still">Alarmierungen</span>
      </div>
      <div class="karte zahl">
        <span class="wert mono">{{ kennzahlen.laufend }}</span>
        <span class="still">Laufend</span>
      </div>
    </div>

    <p v-if="fehler" class="hinweisbox fehler">{{ fehler }}</p>

    <section class="karte">
      <p v-if="laedt" class="leer still">Wird geladen …</p>
      <p v-else-if="!sortiert.length" class="leer still">
        Noch keine Anrufe erfasst. Starte in der Konsole ein Gespräch.
      </p>

      <div v-else class="tabellenrahmen">
        <table class="liste">
          <thead>
            <tr>
              <th>Zeit</th>
              <th>Anrufer</th>
              <th>Kategorie</th>
              <th>Dringlichkeit</th>
              <th>Zusammenfassung</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="eintrag in sortiert"
              :key="eintrag.id"
              :class="{ notfallzeile: eintrag.urgency === 'notfall' }"
              @click="router.push({ name: 'gespraech', params: { id: eintrag.id } })"
            >
              <td class="mono nichtumbrechen">{{ zeit(eintrag.started_at) }}</td>
              <td>
                {{ eintrag.caller_name ?? '—' }}
                <span v-if="eintrag.incomplete" class="marke dringend">unvollständig</span>
              </td>
              <td class="nichtumbrechen">{{ eintrag.category ?? '—' }}</td>
              <td>
                <span v-if="eintrag.urgency" class="marke" :class="eintrag.urgency">
                  {{ eintrag.urgency }}
                </span>
                <span v-else class="still">—</span>
              </td>
              <td class="zusammenfassung">{{ eintrag.summary ?? '—' }}</td>
              <td>
                <span v-if="eintrag.escalated" class="marke notfall" title="Notdienst alarmiert">
                  alarmiert
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>

<style scoped>
.seite {
  max-width: var(--breite);
  margin: 0 auto;
  padding: 1.75rem 1rem 3rem;
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
}

.kopf {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  flex-wrap: wrap;
}

.filter {
  display: flex;
  gap: 0.35rem;
  flex-wrap: wrap;
}
.filter .knopf {
  padding: 0.35rem 0.75rem;
  font-size: 0.85rem;
}

.kennzahlen {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 0.75rem;
}

.zahl {
  padding: 0.9rem 1.1rem;
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
}
.zahl.betont {
  border-color: var(--notfall);
  background: var(--notfall-schwach);
}

.wert {
  font-size: 1.7rem;
  font-weight: 700;
  letter-spacing: -0.02em;
}

.leer {
  padding: 2.5rem 1rem;
  text-align: center;
}

.notfallzeile {
  background: color-mix(in srgb, var(--notfall) 7%, transparent);
}
.notfallzeile:hover {
  background: color-mix(in srgb, var(--notfall) 12%, transparent) !important;
}

.nichtumbrechen {
  white-space: nowrap;
}

.zusammenfassung {
  max-width: 40ch;
  color: var(--text-leise);
}
</style>
