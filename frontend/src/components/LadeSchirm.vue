<script setup lang="ts">
/**
 * Vollbild-Ladeanzeige.
 *
 * Das Motiv ist bewusst dieselbe Wellenform wie in der Gesprächskonsole – die
 * Anwendung hört zu und spricht, also soll auch das Warten so aussehen und
 * nicht wie ein beliebiger Kreisel.
 *
 * Ob und wann sie erscheint, entscheidet der Store; diese Komponente stellt
 * nur dar.
 */
import { useLaden } from '@/stores/laden'

const laden = useLaden()

// Feste, ungleichmäßige Verzögerungen: Ein exakt gleichmäßiger Lauf wirkt
// mechanisch, ein zufälliger unruhig.
const takte = [0, 0.12, 0.24, 0.36, 0.24, 0.12, 0]
</script>

<template>
  <Transition name="verblassen">
    <div
      v-if="laden.sichtbar"
      class="schirm"
      role="status"
      aria-live="polite"
      aria-busy="true"
    >
      <div class="mitte">
        <div class="wellenform" aria-hidden="true">
          <span
            v-for="(verzug, i) in takte"
            :key="i"
            class="balken"
            :style="{ animationDelay: `${verzug}s` }"
          />
        </div>
        <p class="wort">{{ laden.text ?? 'Einen Moment' }}</p>
      </div>
    </div>
  </Transition>
</template>

<style scoped>
.schirm {
  position: fixed;
  inset: 0;
  z-index: 100;
  display: grid;
  place-items: center;
  background: var(--flaeche-0);
}

.mitte {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1.4rem;
}

.wellenform {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 56px;
}

.balken {
  width: 6px;
  height: 100%;
  border-radius: 3px;
  background: linear-gradient(180deg, var(--ruhig), var(--hinweis));
  transform: scaleY(0.18);
  transform-origin: center;
  animation: atmen 1.15s ease-in-out infinite;
}

@keyframes atmen {
  0%,
  100% {
    transform: scaleY(0.18);
    opacity: 0.55;
  }
  45% {
    transform: scaleY(1);
    opacity: 1;
  }
}

.wort {
  font-size: 0.88rem;
  color: var(--text-still);
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.verblassen-enter-active,
.verblassen-leave-active {
  transition: opacity 0.22s ease;
}
.verblassen-enter-from,
.verblassen-leave-to {
  opacity: 0;
}

@keyframes blinzeln {
  0%,
  100% {
    opacity: 0.3;
  }
  50% {
    opacity: 1;
  }
}

/* Bei reduzierter Bewegung wird nichts mehr verschoben – aber es bleibt ein
   Lebenszeichen. Ein völlig starrer Ladeschirm sieht eingefroren aus, und
   reines Auf- und Abblenden löst keine vestibulären Reize aus.

   Die beiden !important sind nötig, weil base.css unter dieser Einstellung
   pauschal jede Animationsdauer auf 0,01 ms setzt. */
@media (prefers-reduced-motion: reduce) {
  .balken {
    transform: scaleY(0.55);
    animation-name: blinzeln !important;
    animation-duration: 2s !important;
  }
  .verblassen-enter-active,
  .verblassen-leave-active {
    transition: none;
  }
}
</style>
