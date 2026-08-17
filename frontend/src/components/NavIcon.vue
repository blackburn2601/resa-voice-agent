<script setup lang="ts">
/**
 * Symbole für die Seitenleiste, als Inline-SVG.
 *
 * Bewusst keine Symbolschrift und kein Fremd-Paket: Die Content-Security-Policy
 * erlaubt nur eigene Quellen, und vier Pfade wiegen weniger als jede Bibliothek.
 */
defineProps<{ name: 'konsole' | 'gespraeche' | 'wissen' | 'einstellungen' }>()
</script>

<template>
  <svg
    class="symbol"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.75"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <!-- Mikrofon -->
    <template v-if="name === 'konsole'">
      <rect x="9" y="2.5" width="6" height="11" rx="3" />
      <path d="M5.5 11a6.5 6.5 0 0 0 13 0" />
      <path d="M12 17.5V21" />
    </template>

    <!-- Liste mit Markierung -->
    <template v-else-if="name === 'gespraeche'">
      <path d="M9 6h11M9 12h11M9 18h7" />
      <circle cx="4.5" cy="6" r="1.4" />
      <circle cx="4.5" cy="12" r="1.4" />
      <circle cx="4.5" cy="18" r="1.4" />
    </template>

    <!-- Aufgeschlagenes Buch -->
    <template v-else-if="name === 'wissen'">
      <path d="M12 6.5C10.5 5 8.5 4.3 4 4.3v13c4.5 0 6.5.7 8 2.2 1.5-1.5 3.5-2.2 8-2.2v-13c-4.5 0-6.5.7-8 2.2Z" />
      <path d="M12 6.5v13" />
    </template>

    <!-- Regler -->
    <template v-else>
      <path d="M4 7h9M17 7h3M4 17h3M11 17h9" />
      <circle cx="15" cy="7" r="2.2" />
      <circle cx="9" cy="17" r="2.2" />
    </template>
  </svg>
</template>

<style scoped>
.symbol {
  width: 19px;
  height: 19px;
  flex: none;
}
</style>
