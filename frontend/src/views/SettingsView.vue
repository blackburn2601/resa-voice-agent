<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { api, type Contact, type Health } from '@/api/client'

const kontakte = ref<Contact[]>([])
const zustand = ref<Health | null>(null)
const meldung = ref<{ art: 'ok' | 'fehler'; text: string } | null>(null)
const laeuft = ref(false)

const name = ref('')
const email = ref('')
const telefon = ref('')

const aktiver = computed(() => kontakte.value.find((k) => k.active) ?? null)

async function laden() {
  const [liste, health] = await Promise.all([
    api.contacts(),
    api.health().catch(() => null),
  ])
  kontakte.value = liste
  zustand.value = health
}

onMounted(laden)

async function mit(aufgabe: () => Promise<unknown>, erfolg: string) {
  laeuft.value = true
  meldung.value = null
  try {
    await aufgabe()
    await laden()
    meldung.value = { art: 'ok', text: erfolg }
    return true
  } catch (ausnahme) {
    meldung.value = {
      art: 'fehler',
      text: ausnahme instanceof Error ? ausnahme.message : 'Fehlgeschlagen.',
    }
    return false
  } finally {
    laeuft.value = false
  }
}

async function anlegen() {
  const erfolg = await mit(
    () =>
      api.createContact({
        name: name.value.trim(),
        email: email.value.trim(),
        phone: telefon.value.trim() || null,
      }),
    'Bereitschaft gesetzt.',
  )
  if (erfolg) {
    name.value = ''
    email.value = ''
    telefon.value = ''
  }
}

const dienstNamen: Record<string, string> = {
  postgres: 'PostgreSQL',
  qdrant: 'Qdrant',
  embeddings: 'Embeddings',
}
</script>

<template>
  <div class="seite">
    <h1>Einstellungen</h1>

    <p v-if="!aktiver" class="hinweisbox fehler">
      Es ist keine Bereitschaft hinterlegt. Bei einem Notfall kann Resa niemanden alarmieren –
      und sie wird das der anrufenden Person auch so sagen.
    </p>

    <p v-if="meldung" class="hinweisbox" :class="{ fehler: meldung.art === 'fehler' }">
      {{ meldung.text }}
    </p>

    <div class="raster">
      <section class="karte block">
        <h2>Bereitschaft</h2>
        <p class="still">
          Wer hier aktiv ist, bekommt bei einem Notfall sofort eine E-Mail – rund um die Uhr.
        </p>

        <ul v-if="kontakte.length" class="kontakte">
          <li v-for="kontakt in kontakte" :key="kontakt.id" :class="{ aktiv: kontakt.active }">
            <div>
              <strong>{{ kontakt.name }}</strong>
              <span class="still">{{ kontakt.email }}</span>
              <span v-if="kontakt.phone" class="still mono">{{ kontakt.phone }}</span>
            </div>
            <div class="aktionen">
              <span v-if="kontakt.active" class="marke ruhig">aktiv</span>
              <button
                v-else
                class="knopf schlicht"
                type="button"
                :disabled="laeuft"
                @click="mit(() => api.activateContact(kontakt.id), 'Bereitschaft gewechselt.')"
              >
                aktivieren
              </button>
              <button
                v-if="!kontakt.active"
                class="knopf schlicht"
                type="button"
                :disabled="laeuft"
                @click="mit(() => api.deleteContact(kontakt.id), 'Entfernt.')"
              >
                entfernen
              </button>
            </div>
          </li>
        </ul>

        <form class="formular" @submit.prevent="anlegen">
          <div class="feldgruppe">
            <label class="beschriftung" for="name">Name</label>
            <input id="name" v-model="name" class="feld" type="text" required />
          </div>
          <div class="feldgruppe">
            <label class="beschriftung" for="email">E-Mail</label>
            <input id="email" v-model="email" class="feld" type="email" required />
          </div>
          <div class="feldgruppe">
            <label class="beschriftung" for="telefon">Telefon (optional)</label>
            <input id="telefon" v-model="telefon" class="feld" type="tel" />
          </div>
          <button class="knopf haupt" type="submit" :disabled="laeuft">
            Als Bereitschaft setzen
          </button>
        </form>
      </section>

      <section v-if="zustand" class="karte block">
        <h2>Systemzustand</h2>
        <ul class="dienste">
          <li v-for="(pruefung, schluessel) in zustand.checks" :key="schluessel">
            <span class="marke" :class="pruefung.ok ? 'ruhig' : 'notfall'">
              {{ pruefung.ok ? 'ok' : 'Fehler' }}
            </span>
            <strong>{{ dienstNamen[schluessel] ?? schluessel }}</strong>
            <span class="still">{{ pruefung.detail }}</span>
          </li>
        </ul>

        <h3>Zugänge</h3>
        <ul class="dienste">
          <li v-for="(gesetzt, schluessel) in zustand.config" :key="schluessel">
            <span class="marke" :class="gesetzt ? 'ruhig' : 'dringend'">
              {{ gesetzt ? 'gesetzt' : 'fehlt' }}
            </span>
            <strong>{{ schluessel }}</strong>
          </li>
        </ul>
        <p v-if="!zustand.config.elevenlabs" class="still">
          Ohne ElevenLabs-Schlüssel bleibt Resa stumm – Text funktioniert weiterhin.
        </p>
      </section>
    </div>
  </div>
</template>

<style scoped>
.seite {
  max-width: var(--breite);
  margin: 0 auto;
  padding: 1.75rem 1rem 3rem;
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.raster {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 1rem;
  align-items: start;
}

.block {
  padding: 1.25rem;
  display: flex;
  flex-direction: column;
  gap: 0.85rem;
}

.kontakte,
.dienste {
  list-style: none;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.kontakte li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  padding: 0.7rem 0.85rem;
  border: 1px solid var(--rand);
  border-radius: var(--radius);
  flex-wrap: wrap;
}
.kontakte li.aktiv {
  border-color: var(--ruhig);
  background: var(--ruhig-schwach);
}
.kontakte li div:first-child {
  display: flex;
  flex-direction: column;
}

.aktionen {
  display: flex;
  align-items: center;
  gap: 0.35rem;
}

.dienste li {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  font-size: 0.9rem;
  flex-wrap: wrap;
}

.formular {
  display: flex;
  flex-direction: column;
  gap: 0.7rem;
  padding-top: 0.5rem;
  border-top: 1px solid var(--rand);
}

.feldgruppe {
  display: flex;
  flex-direction: column;
}

h3 {
  margin-top: 0.5rem;
  color: var(--text-leise);
}
</style>
