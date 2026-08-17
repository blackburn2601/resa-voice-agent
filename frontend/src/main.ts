import { createPinia } from 'pinia'
import { createApp } from 'vue'

import App from './App.vue'
import router from './router'
import './styles/base.css'

const app = createApp(App).use(createPinia()).use(router)

// Erst montieren, wenn die erste Route samt ihrem Nachlade-Bündel steht –
// sonst blitzt zwischen Start-Anzeige und fertiger Seite ein leerer Rahmen auf.
router.isReady().then(() => {
  app.mount('#app')

  const start = document.getElementById('start')
  if (!start) return
  start.classList.add('weg')
  // Nach dem Ausblenden aus dem Dokument nehmen, damit nichts darüberliegt.
  start.addEventListener('transitionend', () => start.remove(), { once: true })
  // Falls die Übergangsanimation unterdrückt ist, greift der Rückfall.
  setTimeout(() => start.remove(), 400)
})
