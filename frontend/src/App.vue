<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'

import LadeSchirm from '@/components/LadeSchirm.vue'
import NavIcon from '@/components/NavIcon.vue'
import { useAuth } from '@/stores/auth'
import { useEvents } from '@/stores/events'

const auth = useAuth()
const events = useEvents()
const route = useRoute()
const router = useRouter()

const thema = ref<'dark' | 'light'>(
  (localStorage.getItem('resa-thema') as 'dark' | 'light') ?? 'dark',
)
// Auf schmalen Geräten liegt die Leiste als Schublade über dem Inhalt.
const schubladeOffen = ref(false)

function themaUmschalten() {
  thema.value = thema.value === 'dark' ? 'light' : 'dark'
}

watch(
  thema,
  (wert) => {
    document.documentElement.dataset.theme = wert
    localStorage.setItem('resa-thema', wert)
  },
  { immediate: true },
)

// Der Live-Strom hängt an der Anmeldung, nicht an einer einzelnen Ansicht –
// so bleibt er beim Wechseln zwischen den Seiten bestehen.
watch(
  () => auth.user,
  (user) => (user ? events.verbinden() : events.trennen()),
  { immediate: true },
)

// Nach einem Wechsel schließt sich die Schublade von selbst.
watch(() => route.fullPath, () => (schubladeOffen.value = false))

onMounted(() => auth.pruefen())

async function abmelden() {
  await auth.abmelden()
  router.push({ name: 'anmeldung' })
}

const navigation = [
  { name: 'konsole', label: 'Konsole' },
  { name: 'gespraeche', label: 'Anrufe' },
  { name: 'wissen', label: 'Wissensbasis' },
  { name: 'einstellungen', label: 'Einstellungen' },
] as const
</script>

<template>
  <div class="huelle" :class="{ mitLeiste: auth.user }">
    <template v-if="auth.user">
      <button
        class="schubladenknopf"
        type="button"
        :aria-expanded="schubladeOffen"
        aria-label="Navigation"
        @click="schubladeOffen = !schubladeOffen"
      >
        <span class="strich" />
        <span class="strich" />
        <span class="strich" />
      </button>

      <div v-if="schubladeOffen" class="schleier" @click="schubladeOffen = false" />

      <aside class="leiste" :class="{ offen: schubladeOffen }">
        <RouterLink :to="{ name: 'konsole' }" class="marke-resa">
          <span class="punkt" :class="{ live: events.verbunden }" />
          <span>Resa</span>
        </RouterLink>

        <nav>
          <RouterLink
            v-for="eintrag in navigation"
            :key="eintrag.name"
            :to="{ name: eintrag.name }"
            class="navlink"
            :class="{ aktiv: route.name === eintrag.name }"
          >
            <NavIcon :name="eintrag.name" />
            <span>{{ eintrag.label }}</span>
          </RouterLink>
        </nav>

        <div class="fuss">
          <button
            class="navlink knopfartig"
            type="button"
            @click="themaUmschalten"
          >
            <span class="glyphe">{{ thema === 'dark' ? '☀' : '☾' }}</span>
            <span>{{ thema === 'dark' ? 'Hell' : 'Dunkel' }}</span>
          </button>

          <div class="konto">
            <span class="still" :title="auth.user.email">{{ auth.user.email }}</span>
            <button class="knopf schlicht" type="button" @click="abmelden">Abmelden</button>
          </div>
        </div>
      </aside>
    </template>

    <main>
      <RouterView v-if="auth.geprueft" />
    </main>

    <LadeSchirm />
  </div>
</template>

<style scoped>
.huelle {
  min-height: 100%;
}

/* Nur angemeldet gibt es eine Leiste – die öffentliche Konsole steht für sich. */
.huelle.mitLeiste {
  display: grid;
  grid-template-columns: 232px minmax(0, 1fr);
}

.leiste {
  position: sticky;
  top: 0;
  height: 100vh;
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
  padding: 1.15rem 0.85rem;
  background: var(--flaeche-1);
  border-right: 1px solid var(--rand);
}

.marke-resa {
  display: flex;
  align-items: center;
  gap: 0.55rem;
  padding: 0.35rem 0.6rem;
  font-weight: 700;
  font-size: 1.1rem;
  letter-spacing: -0.02em;
  color: var(--text);
  text-decoration: none;
}
.marke-resa:hover {
  text-decoration: none;
}

/* Der Punkt zeigt, ob der Live-Strom steht – ohne eigene Statuszeile. */
.punkt {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--text-still);
  transition: background 0.3s ease;
  flex: none;
}
.punkt.live {
  background: var(--ruhig);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--ruhig) 22%, transparent);
}

nav {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}

.navlink {
  display: flex;
  align-items: center;
  gap: 0.7rem;
  padding: 0.6rem 0.6rem;
  border-radius: var(--radius);
  color: var(--text-leise);
  font-size: 0.92rem;
  font-weight: 500;
  text-decoration: none;
  border: none;
  background: transparent;
  width: 100%;
  text-align: left;
  cursor: pointer;
}
.navlink:hover {
  color: var(--text);
  background: var(--flaeche-2);
  text-decoration: none;
}
.navlink.aktiv {
  color: var(--text);
  background: var(--flaeche-3);
  /* Schmale Markierung links statt Vollfläche: ruhiger, und die aktive Seite
     bleibt auch bei gleicher Flächenfarbe erkennbar. */
  box-shadow: inset 2px 0 0 var(--ruhig);
}

.glyphe {
  width: 19px;
  text-align: center;
  flex: none;
}

.fuss {
  margin-top: auto;
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
  padding-top: 0.85rem;
  border-top: 1px solid var(--rand);
}

.konto {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
  padding: 0 0.6rem;
}
.konto .still {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.konto .knopf {
  align-self: flex-start;
  padding-left: 0;
}

main {
  min-width: 0;
}

/* --- Schublade auf schmalen Geräten ----------------------------------- */

.schubladenknopf {
  display: none;
  position: fixed;
  top: 0.7rem;
  left: 0.7rem;
  z-index: 30;
  width: 44px;
  height: 44px;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  border: 1px solid var(--rand);
  border-radius: var(--radius);
  background: var(--flaeche-1);
  cursor: pointer;
}

.strich {
  width: 18px;
  height: 2px;
  border-radius: 1px;
  background: var(--text);
}

.schleier {
  display: none;
  position: fixed;
  inset: 0;
  z-index: 18;
  background: rgb(0 0 0 / 0.5);
}

@media (max-width: 860px) {
  .huelle.mitLeiste {
    grid-template-columns: minmax(0, 1fr);
  }

  .schubladenknopf {
    display: flex;
  }

  .schleier {
    display: block;
  }

  .leiste {
    position: fixed;
    top: 0;
    left: 0;
    z-index: 20;
    width: 250px;
    transform: translateX(-100%);
    transition: transform 0.2s ease;
    box-shadow: var(--spur-hoch);
  }
  .leiste.offen {
    transform: translateX(0);
  }

  /* Platz für den Schubladenknopf, damit er keine Überschrift verdeckt. */
  main {
    padding-top: 3.4rem;
  }
}

@media (prefers-reduced-motion: reduce) {
  .leiste {
    transition: none;
  }
}
</style>
