import { createRouter, createWebHistory } from 'vue-router'

import { useAuth } from '@/stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/anmeldung', name: 'anmeldung', component: () => import('@/views/LoginView.vue') },
    // Öffentlich: Wer anruft, hat keine Zugangsdaten.
    {
      path: '/',
      name: 'konsole',
      component: () => import('@/views/ConsoleView.vue'),
      meta: { oeffentlich: true },
    },
    { path: '/anrufe', name: 'gespraeche', component: () => import('@/views/ConversationsView.vue') },
    {
      path: '/anrufe/:id',
      name: 'gespraech',
      component: () => import('@/views/ConversationDetailView.vue'),
    },
    { path: '/wissen', name: 'wissen', component: () => import('@/views/KnowledgeView.vue') },
    {
      path: '/einstellungen',
      name: 'einstellungen',
      component: () => import('@/views/SettingsView.vue'),
    },
    { path: '/:pfad(.*)*', redirect: { name: 'konsole' } },
  ],
})

router.beforeEach(async (nach) => {
  const auth = useAuth()
  // Beim ersten Aufruf ist noch unbekannt, ob eine Sitzung besteht – das Cookie
  // ist httpOnly, also fragen wir den Server.
  if (!auth.geprueft) await auth.pruefen()

  if (!nach.meta.oeffentlich && nach.name !== 'anmeldung' && !auth.user) {
    return { name: 'anmeldung', query: { weiter: nach.fullPath } }
  }
  if (nach.name === 'anmeldung' && auth.user) {
    return { name: 'gespraeche' }
  }
  return true
})

export default router
