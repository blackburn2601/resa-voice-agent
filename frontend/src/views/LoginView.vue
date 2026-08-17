<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useAuth } from '@/stores/auth'

const auth = useAuth()
const route = useRoute()
const router = useRouter()

const email = ref('')
const passwort = ref('')
const fehler = ref<string | null>(null)
const laeuft = ref(false)

async function absenden() {
  fehler.value = null
  laeuft.value = true
  try {
    await auth.anmelden(email.value, passwort.value)
    const weiter = route.query.weiter
    router.push(typeof weiter === 'string' ? weiter : { name: 'konsole' })
  } catch (ausnahme) {
    fehler.value = ausnahme instanceof Error ? ausnahme.message : 'Anmeldung fehlgeschlagen.'
  } finally {
    laeuft.value = false
  }
}
</script>

<template>
  <div class="schirm">
    <form class="karte formular" @submit.prevent="absenden">
      <div class="titel">
        <p class="augenbraue">Musterhaus Verwaltung</p>
        <h1>Resa</h1>
        <p class="leise">Anmeldung für die Anrufübersicht</p>
      </div>

      <div>
        <label class="beschriftung" for="email">E-Mail-Adresse</label>
        <input
          id="email"
          v-model="email"
          class="feld"
          type="email"
          autocomplete="username"
          required
          autofocus
        />
      </div>

      <div>
        <label class="beschriftung" for="passwort">Passwort</label>
        <input
          id="passwort"
          v-model="passwort"
          class="feld"
          type="password"
          autocomplete="current-password"
          required
        />
      </div>

      <p v-if="fehler" class="hinweisbox fehler">{{ fehler }}</p>

      <button class="knopf haupt" type="submit" :disabled="laeuft">
        {{ laeuft ? 'Einen Moment …' : 'Anmelden' }}
      </button>
    </form>
  </div>
</template>

<style scoped>
.schirm {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 1.5rem;
}

.formular {
  width: 100%;
  max-width: 380px;
  padding: 2rem;
  display: flex;
  flex-direction: column;
  gap: 1.1rem;
}

.titel h1 {
  font-size: 2rem;
  letter-spacing: -0.03em;
  margin: 0.15rem 0 0.35rem;
}

.augenbraue {
  font-size: 0.72rem;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--text-still);
}
</style>
