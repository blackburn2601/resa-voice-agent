/**
 * Prüft, ob der Browser überhaupt sprechen *und* zuhören kann.
 *
 * Der Grund für dieses Modul: Ein Zertifikatsfehler macht die Seite für Chrome
 * zu einem unsicheren Kontext – auch nach „Trotzdem fortfahren". Tonausgabe
 * läuft dann weiter, aber `navigator.mediaDevices` fehlt und die
 * Spracherkennung verweigert den Dienst, ohne dass ein Fehler sichtbar wird.
 * Von außen sieht das aus, als sei das Gespräch abgebrochen.
 *
 * Diese Prüfung benennt die Ursache, statt den Nutzer raten zu lassen.
 */

import { onMounted, ref } from 'vue'

export interface Umgebung {
  sichererKontext: boolean
  medienGeraete: boolean
  erkennung: boolean
  mikrofonRecht: PermissionState | 'unbekannt'
  herkunft: string
}

export interface Befund {
  schwere: 'blockiert' | 'hinweis'
  text: string
  rat: string
}

function erkennungVorhanden(): boolean {
  const w = window as any
  return Boolean(w.SpeechRecognition || w.webkitSpeechRecognition)
}

export function useUmgebung() {
  const umgebung = ref<Umgebung | null>(null)
  const befund = ref<Befund | null>(null)

  async function pruefen() {
    let recht: PermissionState | 'unbekannt' = 'unbekannt'
    try {
      // Nicht überall vorhanden – Safari kennt den Namen 'microphone' nicht.
      const status = await navigator.permissions?.query({
        name: 'microphone' as PermissionName,
      })
      if (status) recht = status.state
    } catch {
      recht = 'unbekannt'
    }

    const u: Umgebung = {
      sichererKontext: window.isSecureContext,
      medienGeraete: Boolean(navigator.mediaDevices),
      erkennung: erkennungVorhanden(),
      mikrofonRecht: recht,
      herkunft: location.origin,
    }
    umgebung.value = u

    if (!u.sichererKontext) {
      befund.value = {
        schwere: 'blockiert',
        text: 'Diese Seite gilt dem Browser als unsicher, deshalb ist das Mikrofon gesperrt.',
        rat: location.protocol === 'https:'
          ? 'Das passiert bei einem selbstsignierten Zertifikat: Auch nach „Trotzdem fortfahren" bleibt das Mikrofon blockiert. Öffne die Seite über http://localhost:5173 – localhost gilt immer als sicher – oder hinterlege ein gültiges Zertifikat.'
          : 'Rufe die Seite über localhost oder über HTTPS mit gültigem Zertifikat auf.',
      }
      return
    }

    if (!u.erkennung) {
      befund.value = {
        schwere: 'hinweis',
        text: 'Dieser Browser bietet keine Spracherkennung.',
        rat: 'Chrome, Edge und Safari können es; Firefox nicht. Über das Textfeld funktioniert das Gespräch trotzdem, und Resa antwortet weiterhin mit Stimme.',
      }
      return
    }

    if (u.mikrofonRecht === 'denied') {
      befund.value = {
        schwere: 'blockiert',
        text: 'Der Zugriff aufs Mikrofon ist für diese Seite abgelehnt.',
        rat: 'Klicke links in der Adressleiste auf das Schloss- oder Reglersymbol und erlaube das Mikrofon, dann die Seite neu laden.',
      }
      return
    }

    befund.value = null
  }

  onMounted(pruefen)

  return { umgebung, befund, pruefen }
}
