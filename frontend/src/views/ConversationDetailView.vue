<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import { api, type ConversationDetail } from '@/api/client'

const route = useRoute()
const gespraech = ref<ConversationDetail | null>(null)
const fehler = ref<string | null>(null)
const laedt = ref(true)

onMounted(async () => {
  try {
    gespraech.value = await api.conversation(String(route.params.id))
  } catch (ausnahme) {
    fehler.value = ausnahme instanceof Error ? ausnahme.message : 'Laden fehlgeschlagen.'
  } finally {
    laedt.value = false
  }
})

function zeit(iso: string) {
  return new Date(iso).toLocaleString('de-DE', { dateStyle: 'medium', timeStyle: 'short' })
}

const kanalNamen: Record<string, string> = { email: 'E-Mail', sms: 'SMS' }
const zustellNamen: Record<string, string> = {
  delivered: 'zugestellt',
  failed: 'fehlgeschlagen',
  skipped: 'nicht aktiv',
}
</script>

<template>
  <div class="seite">
    <RouterLink :to="{ name: 'gespraeche' }" class="zurueck">← Zur Übersicht</RouterLink>

    <p v-if="laedt" class="still">Wird geladen …</p>
    <p v-else-if="fehler" class="hinweisbox fehler">{{ fehler }}</p>

    <template v-else-if="gespraech">
      <header class="kopf">
        <div>
          <h1>{{ gespraech.caller_name ?? 'Ohne Namen' }}</h1>
          <p class="still mono">{{ zeit(gespraech.started_at) }} · {{ gespraech.id }}</p>
        </div>
        <div class="marken">
          <span v-if="gespraech.urgency" class="marke" :class="gespraech.urgency">
            {{ gespraech.urgency }}
          </span>
          <span v-if="gespraech.escalated" class="marke notfall">alarmiert</span>
          <span v-if="gespraech.incomplete" class="marke dringend">unvollständig</span>
          <span class="marke normal">{{ gespraech.status }}</span>
        </div>
      </header>

      <div class="raster">
        <section class="karte block">
          <h2>Erfasste Angaben</h2>
          <dl v-if="gespraech.intake" class="daten">
            <dt>Kategorie</dt>
            <dd>{{ gespraech.intake.category }}</dd>

            <dt>Anliegen</dt>
            <dd>{{ gespraech.intake.summary }}</dd>

            <dt>Objekt</dt>
            <dd>{{ gespraech.intake.property_address ?? 'nicht genannt' }}</dd>

            <dt>Seit</dt>
            <dd>{{ gespraech.intake.occurred_since ?? 'nicht genannt' }}</dd>

            <dt>Rückruf</dt>
            <dd>
              <template v-if="gespraech.intake.callback_requested">
                gewünscht{{
                  gespraech.intake.preferred_callback_time
                    ? ` – ${gespraech.intake.preferred_callback_time}`
                    : ''
                }}
              </template>
              <template v-else>nicht gewünscht</template>
            </dd>

            <dt>Telefon</dt>
            <dd class="mono">{{ gespraech.intake.caller_phone ?? 'nicht genannt' }}</dd>

            <dt>Gefahr im Verzug</dt>
            <dd>{{ gespraech.intake.danger_to_life ? 'ja' : 'nein' }}</dd>
          </dl>
          <p v-else class="still">
            Für dieses Gespräch wurde nichts erfasst – es wurde vermutlich abgebrochen.
          </p>
        </section>

        <section v-if="gespraech.escalation" class="karte block alarm">
          <h2>Alarmierung</h2>
          <p class="still">{{ zeit(gespraech.escalation.triggered_at) }}</p>
          <p class="anlass">{{ gespraech.escalation.reason }}</p>

          <ul class="versuche">
            <li v-for="(versuch, i) in gespraech.escalation.attempts" :key="i">
              <span class="marke" :class="versuch.status === 'delivered' ? 'ruhig' : 'normal'">
                {{ kanalNamen[versuch.channel] ?? versuch.channel }}
              </span>
              <span :class="{ gescheitert: versuch.status === 'failed' }">
                {{ zustellNamen[versuch.status] ?? versuch.status }}
              </span>
              <span class="still">{{ versuch.detail }}</span>
            </li>
          </ul>
        </section>

        <section class="karte block weit">
          <h2>Gesprächsverlauf</h2>
          <div class="verlauf">
            <div
              v-for="nachricht in gespraech.messages"
              :key="nachricht.seq"
              class="blase"
              :class="nachricht.role === 'assistant' ? 'resa' : 'anrufer'"
            >
              <span class="wer still">
                {{ nachricht.role === 'assistant' ? 'Resa' : 'Anrufer' }}
              </span>
              {{ nachricht.content }}
            </div>
          </div>
        </section>
      </div>
    </template>
  </div>
</template>

<style scoped>
.seite {
  max-width: var(--breite);
  margin: 0 auto;
  padding: 1.75rem 1rem 3rem;
  display: flex;
  flex-direction: column;
  gap: 1.1rem;
}

.zurueck {
  color: var(--text-leise);
  font-size: 0.9rem;
}

.kopf {
  display: flex;
  justify-content: space-between;
  align-items: start;
  gap: 1rem;
  flex-wrap: wrap;
}

.marken {
  display: flex;
  gap: 0.4rem;
  flex-wrap: wrap;
}

.raster {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 1rem;
  align-items: start;
}

.block {
  padding: 1.25rem;
}
.block h2 {
  margin-bottom: 0.75rem;
}
.weit {
  grid-column: 1 / -1;
}

.alarm {
  border-color: var(--notfall);
}

.anlass {
  margin: 0.5rem 0 0.75rem;
  font-weight: 500;
}

.daten {
  display: grid;
  grid-template-columns: minmax(110px, auto) 1fr;
  gap: 0.5rem 1rem;
  font-size: 0.92rem;
}
.daten dt {
  color: var(--text-still);
  font-size: 0.82rem;
}

.versuche {
  list-style: none;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  font-size: 0.88rem;
}
.versuche li {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  flex-wrap: wrap;
}
.gescheitert {
  color: var(--notfall);
  font-weight: 600;
}

.verlauf {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}

.blase {
  padding: 0.65rem 0.9rem;
  border-radius: var(--radius);
  max-width: 70ch;
  white-space: pre-wrap;
}
.blase.resa {
  background: var(--flaeche-2);
  border: 1px solid var(--rand);
}
.blase.anrufer {
  background: var(--hinweis-schwach);
  align-self: flex-end;
}

.wer {
  display: block;
  font-size: 0.72rem;
  text-transform: uppercase;
  letter-spacing: 0.07em;
  margin-bottom: 0.15rem;
}
</style>
