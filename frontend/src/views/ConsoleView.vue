<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'

import { useUmgebung } from '@/composables/useUmgebung'
import { useVoiceSession } from '@/composables/useVoiceSession'
import { useAuth } from '@/stores/auth'

const sitzung = useVoiceSession()
const { umgebung, befund } = useUmgebung()
const auth = useAuth()
const technikOffen = ref(false)
const eingabe = ref('')
const verlaufEl = ref<HTMLElement | null>(null)

const statusTexte: Record<string, string> = {
  bereit: 'Bereit',
  verbindet: 'Verbinde …',
  hoert: 'Ich höre zu – noch einmal tippen, wenn du fertig bist',
  denkt: 'Resa denkt nach …',
  spricht: 'Resa spricht – antippen zum Unterbrechen',
  fehler: 'Es ist etwas schiefgelaufen',
  beendet: 'Gespräch beendet',
}

const knopfBeschriftung = computed(() => {
  switch (sitzung.status.value) {
    case 'hoert':
      return 'Fertig'
    case 'spricht':
      return 'Unterbrechen'
    default:
      return 'Sprechen'
  }
})

watch(
  () => sitzung.beitraege.value.map((b) => b.text).join('|') + sitzung.zwischentext.value,
  async () => {
    await nextTick()
    verlaufEl.value?.scrollTo({ top: verlaufEl.value.scrollHeight, behavior: 'smooth' })
  },
)

function absenden() {
  if (!eingabe.value.trim()) return
  sitzung.senden(eingabe.value)
  eingabe.value = ''
}

function mikrofon() {
  // `zuhoeren()` unterbricht selbst, falls Resa noch spricht. Ein Tippen soll
  // immer bedeuten „ich rede jetzt", nicht nur „sei still".
  sitzung.zuhoeren()
}
</script>

<template>
  <div class="konsole">
    <section class="karte buehne">
      <header class="kopf">
        <div>
          <p class="augenbraue">Musterhaus Verwaltung</p>
          <h1>Ihr Anliegen, jederzeit.</h1>
        </div>
        <RouterLink v-if="!auth.user" :to="{ name: 'anmeldung' }" class="still anmeldung">
          Team-Login
        </RouterLink>
      </header>

      <p v-if="!sitzung.verbunden.value && sitzung.status.value !== 'beendet'" class="lede leise">
        Resa nimmt Ihr Anliegen auf, auch außerhalb der Bürozeiten. Bei einem Notfall –
        Wasser, Gas, Heizungsausfall – alarmiert sie sofort die Bereitschaft.
      </p>

      <div class="wellenform" aria-hidden="true">
        <span
          v-for="(wert, i) in sitzung.pegel.value"
          :key="i"
          class="balken"
          :class="{ aktiv: sitzung.hoertZu.value }"
          :style="{ transform: `scaleY(${wert})` }"
        />
      </div>

      <p class="status" role="status">{{ statusTexte[sitzung.status.value] }}</p>

      <p v-if="sitzung.fehler.value" class="hinweisbox fehler">{{ sitzung.fehler.value }}</p>

      <div v-if="befund" class="hinweisbox" :class="{ fehler: befund.schwere === 'blockiert' }">
        <strong>{{ befund.text }}</strong>
        <p class="rat">{{ befund.rat }}</p>
      </div>

      <div class="steuerung">
        <button
          v-if="!sitzung.verbunden.value"
          class="knopf haupt gross"
          type="button"
          @click="sitzung.starten()"
        >
          Gespräch beginnen
        </button>

        <template v-else>
          <button
            v-if="sitzung.spracherkennungMoeglich"
            class="knopf gross"
            :class="{
              warn: sitzung.status.value === 'spricht',
              haupt: sitzung.status.value === 'hoert',
            }"
            type="button"
            :disabled="sitzung.status.value === 'denkt'"
            @click="mikrofon"
          >
            {{ knopfBeschriftung }}
          </button>
          <button class="knopf schlicht" type="button" @click="sitzung.beenden()">
            Beenden
          </button>
        </template>
      </div>

      <div ref="verlaufEl" class="verlauf" role="log" aria-live="polite">
        <p v-if="!sitzung.beitraege.value.length" class="still mitte">
          Noch nichts gesagt.
        </p>
        <div
          v-for="beitrag in sitzung.beitraege.value"
          :key="beitrag.id"
          class="blase"
          :class="beitrag.sprecher"
        >
          {{ beitrag.text }}<span v-if="beitrag.laufend" class="punkt" />
        </div>
        <!-- Zwischenergebnis der Erkennung: zeigt sofort, dass das Mikrofon
             tatsächlich etwas aufnimmt. -->
        <div v-if="sitzung.zwischentext.value" class="blase anrufer vorlaeufig">
          {{ sitzung.zwischentext.value }}
        </div>
      </div>

      <details v-if="umgebung" class="technik" :open="technikOffen">
        <summary class="still">Technische Prüfung</summary>
        <ul class="pruefliste">
          <li :class="umgebung.sichererKontext ? 'ja' : 'nein'">
            Sicherer Kontext: {{ umgebung.sichererKontext ? 'ja' : 'nein' }}
          </li>
          <li :class="umgebung.medienGeraete ? 'ja' : 'nein'">
            Mikrofon-Schnittstelle: {{ umgebung.medienGeraete ? 'vorhanden' : 'fehlt' }}
          </li>
          <li :class="umgebung.erkennung ? 'ja' : 'nein'">
            Spracherkennung: {{ umgebung.erkennung ? 'vorhanden' : 'fehlt' }}
          </li>
          <li>Mikrofon-Berechtigung: {{ umgebung.mikrofonRecht }}</li>
          <li class="mono">{{ umgebung.herkunft }}</li>
        </ul>
      </details>

      <form v-if="sitzung.verbunden.value" class="eingabe" @submit.prevent="absenden">
        <input
          v-model="eingabe"
          class="feld"
          type="text"
          enterkeyhint="send"
          placeholder="… oder hier tippen"
          :disabled="sitzung.status.value === 'denkt'"
        />
        <button class="knopf" type="submit" :disabled="!eingabe.trim()">Senden</button>
      </form>
    </section>
  </div>
</template>

<style scoped>
.konsole {
  display: grid;
  place-items: start center;
  padding: 1.5rem 0.85rem 2.5rem;
}

.buehne {
  width: 100%;
  max-width: 720px;
  padding: 1.6rem;
  display: flex;
  flex-direction: column;
  gap: 1.05rem;
}

.kopf {
  display: flex;
  align-items: start;
  justify-content: space-between;
  gap: 1rem;
}

.augenbraue {
  font-size: 0.75rem;
  letter-spacing: 0.09em;
  text-transform: uppercase;
  color: var(--text-still);
  margin-bottom: 0.35rem;
}

.anmeldung {
  white-space: nowrap;
}

.lede {
  max-width: 52ch;
}

.wellenform {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 3px;
  height: 60px;
}

.balken {
  width: 4px;
  height: 100%;
  border-radius: 2px;
  background: linear-gradient(180deg, var(--ruhig), var(--hinweis));
  transform: scaleY(0.06);
  transform-origin: center;
  transition: transform 90ms ease-out;
}
.balken.aktiv {
  background: linear-gradient(180deg, var(--dringend), var(--ruhig));
}

.status {
  text-align: center;
  color: var(--text-leise);
  font-size: 0.9rem;
  min-height: 1.4em;
}

.steuerung {
  display: flex;
  gap: 0.6rem;
  justify-content: center;
  flex-wrap: wrap;
}

/* Große Trefferflächen: Auf dem Handy wird hier mit dem Daumen bedient. */
.knopf.gross {
  min-height: 52px;
  min-width: 168px;
  font-size: 1.02rem;
}

.verlauf {
  max-height: 46vh;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
  padding: 0.85rem;
  background: var(--flaeche-0);
  border: 1px solid var(--rand);
  border-radius: var(--radius);
  min-height: 130px;
  overscroll-behavior: contain;
}

.mitte {
  margin: auto;
}

.blase {
  padding: 0.6rem 0.85rem;
  border-radius: var(--radius);
  max-width: 86%;
  white-space: pre-wrap;
}

.blase.resa {
  background: var(--flaeche-2);
  border: 1px solid var(--rand);
  align-self: flex-start;
  border-bottom-left-radius: var(--radius-s);
}

.blase.anrufer {
  background: var(--hinweis-schwach);
  align-self: flex-end;
  border-bottom-right-radius: var(--radius-s);
}

.blase.vorlaeufig {
  opacity: 0.6;
  border: 1px dashed var(--hinweis);
  background: transparent;
}

.blase.system {
  align-self: center;
  background: transparent;
  border: 1px dashed var(--rand-stark);
  color: var(--text-leise);
  font-size: 0.82rem;
  padding: 0.3rem 0.7rem;
}

.punkt {
  display: inline-block;
  width: 7px;
  height: 7px;
  margin-left: 3px;
  border-radius: 50%;
  background: var(--ruhig);
  animation: pulsieren 1s ease-in-out infinite;
}

@keyframes pulsieren {
  0%,
  100% {
    opacity: 0.25;
  }
  50% {
    opacity: 1;
  }
}

.rat {
  margin-top: 0.35rem;
  font-weight: 400;
}

.technik summary {
  cursor: pointer;
  font-size: 0.8rem;
}

.pruefliste {
  list-style: none;
  padding: 0.6rem 0 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  font-size: 0.82rem;
  color: var(--text-leise);
}
.pruefliste .ja {
  color: var(--ruhig);
}
.pruefliste .nein {
  color: var(--notfall);
  font-weight: 600;
}

.eingabe {
  display: flex;
  gap: 0.5rem;
}

/* 16px verhindert, dass iOS beim Fokus in das Feld hineinzoomt. */
.eingabe .feld {
  font-size: 16px;
}

@media (max-width: 560px) {
  .buehne {
    padding: 1.1rem;
    border-radius: var(--radius);
  }
  .kopf {
    flex-direction: row;
    align-items: center;
  }
  h1 {
    font-size: 1.25rem;
  }
  .knopf.gross {
    flex: 1 1 auto;
    min-width: 0;
  }
  .verlauf {
    max-height: 50vh;
  }
}
</style>
