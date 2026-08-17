import { defineStore } from 'pinia'
import { ref } from 'vue'

import { api, ApiError, type User } from '@/api/client'

export const useAuth = defineStore('auth', () => {
  const user = ref<User | null>(null)
  const geprueft = ref(false)

  async function pruefen() {
    try {
      user.value = await api.me()
    } catch {
      user.value = null
    } finally {
      geprueft.value = true
    }
  }

  async function anmelden(email: string, passwort: string) {
    try {
      user.value = await api.login(email, passwort)
    } catch (fehler) {
      user.value = null
      throw fehler instanceof ApiError ? fehler : new Error('Anmeldung fehlgeschlagen.')
    }
  }

  async function abmelden() {
    await api.logout().catch(() => undefined)
    user.value = null
  }

  return { user, geprueft, pruefen, anmelden, abmelden }
})
