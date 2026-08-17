/**
 * Das Gespräch im Browser.
 *
 * Drei Dinge laufen hier zusammen:
 *
 *  1. Spracherkennung über die Web Speech API. Kostenlos und ohne Audio auf dem
 *     Server – dafür browserabhängig. Fehlt sie, bleibt die Texteingabe, und
 *     das Gespräch funktioniert trotzdem.
 *  2. Der WebSocket, über den Text und Audio getrennt eintreffen: erst ein
 *     JSON-Rahmen mit dem Satz, dann der zugehörige MP3-Frame.
 *  3. Eine Wiedergabe-Warteschlange. Sätze kommen schneller herein, als sie
 *     gesprochen werden – ohne Warteschlange würden sie sich überlagern.
 *
 * Die Wellenform zeigt einen echten Pegel: beim Zuhören den des Mikrofons,
 * beim Sprechen den des Wiedergabesignals. Der Mikrofonstrom wird einmal je
 * Gespräch geholt und bis zum Ende behalten – so gibt es nur eine
 * Berechtigungsabfrage und keine Verzögerung bei jedem Antippen.
 *
 * Lässt sich das Mikrofon nicht öffnen, läuft ersatzweise eine Animation. Sie
 * ist ausdrücklich als Lebenszeichen gedacht, nicht als Messung.
 */

import { computed, onBeforeUnmount, ref } from 'vue'

export type Sprecher = 'resa' | 'anrufer' | 'system'

export interface Beitrag {
  id: number
  sprecher: Sprecher
  text: string
  laufend?: boolean
}

type Status = 'bereit' | 'verbindet' | 'hoert' | 'denkt' | 'spricht' | 'fehler' | 'beendet'

interface SpeechRecognitionLike extends EventTarget {
  lang: string
  interimResults: boolean
  continuous: boolean
  maxAlternatives: number
  start(): void
  stop(): void
  abort(): void
  onresult: ((event: any) => void) | null
  onerror: ((event: any) => void) | null
  onend: (() => void) | null
  onstart: (() => void) | null
  onspeechend: (() => void) | null
}

function erkennungKlasse(): (new () => SpeechRecognitionLike) | null {
  const w = window as any
  return w.SpeechRecognition || w.webkitSpeechRecognition || null
}

/** Fehlermeldungen der Web Speech API – ohne Übersetzung bleibt der Nutzer ratlos. */
const ERKENNUNGSFEHLER: Record<string, string> = {
  'no-speech': 'Ich habe nichts gehört. Tippe auf Sprechen und rede direkt danach los.',
  aborted: 'Die Aufnahme wurde unterbrochen. Bitte noch einmal versuchen.',
  'audio-capture': 'Kein Mikrofon gefunden. Prüfe, ob eines angeschlossen und freigegeben ist.',
  'not-allowed':
    'Der Zugriff aufs Mikrofon wurde abgelehnt. Erlaube ihn in den Website-Einstellungen des Browsers.',
  'service-not-allowed': 'Der Browser erlaubt die Spracherkennung hier nicht.',
  network:
    'Die Spracherkennung braucht eine Internetverbindung – der Browser schickt das Audio an seinen eigenen Dienst.',
  'language-not-supported': 'Deutsch wird von der Spracherkennung dieses Browsers nicht unterstützt.',
}

/** Verteilt Frequenzbänder auf die Balken der Wellenform.
 *
 * Ausgelagert, weil es reine Rechnung ist – und weil hier ein Fehler saß: Ein
 * fester Faktor (`daten[i * 2]`) griff bei 32 Bändern und 28 Balken über das
 * Ende des Feldes hinaus, sodass die hintere Hälfte dauerhaft stillstand.
 */
export function verteilePegel(daten: Uint8Array, anzahl: number, verstaerkung = 2.2): number[] {
  const schritt = Math.max(1, Math.floor(daten.length / anzahl))
  const out: number[] = []
  for (let i = 0; i < anzahl; i++) {
    const von = i * schritt
    const bis = Math.min(von + schritt, daten.length)
    let summe = 0
    for (let j = von; j < bis; j++) summe += daten[j]
    const mittel = bis > von ? summe / (bis - von) : 0
    // Sprache liegt selten nahe am Maximum; ohne Verstärkung bliebe der
    // Ausschlag kaum sichtbar.
    out.push(Math.max(0.06, Math.min(1, (mittel / 255) * verstaerkung)))
  }
  return out
}


export function useVoiceSession() {
  const status = ref<Status>('bereit')
  const beitraege = ref<Beitrag[]>([])
  const fehler = ref<string | null>(null)
  const conversationId = ref<string | null>(null)
  const pegel = ref<number[]>(new Array(28).fill(0.06))
  const zwischentext = ref('')
  const spracherkennungMoeglich = erkennungKlasse() !== null

  let ws: WebSocket | null = null
  let recognition: SpeechRecognitionLike | null = null
  let naechsteId = 1
  let laufenderResaBeitrag: Beitrag | null = null

  // Audio
  let audioCtx: AudioContext | null = null
  let quelle: AudioBufferSourceNode | null = null
  const warteschlange: ArrayBuffer[] = []
  let spielt = false
  let erwarteterSatz: { text: string } | null = null

  // Wellenform
  let analyser: AnalyserNode | null = null
  let animation = 0
  let ziertakt = 0
  let mikroStrom: MediaStream | null = null
  let mikroKnoten: AnalyserNode | null = null

  // Bewusst ein Ref, kein computed: `ws` ist eine gewöhnliche Closure-
  // Variable und damit für Vue unsichtbar. Ein computed darüber hätte keine
  // reaktive Abhängigkeit, würde einmal ausgewertet und bliebe für immer
  // false – die Oberfläche käme nie aus dem Nicht-verbunden-Zustand heraus.
  const verbunden = ref(false)
  const hoertZu = computed(() => status.value === 'hoert')

  function beitragHinzufuegen(sprecher: Sprecher, text: string, laufend = false): Beitrag {
    const beitrag: Beitrag = { id: naechsteId++, sprecher, text, laufend }
    beitraege.value.push(beitrag)
    return beitrag
  }

  // --- Wiedergabe --------------------------------------------------------

  function ctx(): AudioContext {
    if (!audioCtx) audioCtx = new AudioContext()
    return audioCtx
  }

  async function naechstesAbspielen() {
    if (spielt) return
    const daten = warteschlange.shift()
    if (!daten) {
      if (status.value === 'spricht') status.value = 'bereit'
      zierStoppen()
      return
    }

    spielt = true
    status.value = 'spricht'
    try {
      // Auf iOS ist der Kontext nach einem Wechsel in den Hintergrund
      // angehalten; ohne resume() bliebe die Wiedergabe stumm.
      if (ctx().state === 'suspended') await ctx().resume()

      const puffer = await ctx().decodeAudioData(daten)
      quelle = ctx().createBufferSource()
      quelle.buffer = puffer

      const analyse = ctx().createAnalyser()
      analyse.fftSize = 128
      quelle.connect(analyse)
      analyse.connect(ctx().destination)
      wellenformVon(analyse)

      quelle.onended = () => {
        spielt = false
        quelle = null
        void naechstesAbspielen()
      }
      quelle.start()
    } catch {
      spielt = false
      void naechstesAbspielen()
    }
  }

  function wiedergabeStoppen() {
    warteschlange.length = 0
    erwarteterSatz = null
    if (quelle) {
      try {
        quelle.onended = null
        quelle.stop()
      } catch {
        /* bereits beendet */
      }
      quelle = null
    }
    spielt = false
    wellenformStoppen()
  }

  // --- Wellenform --------------------------------------------------------

  function wellenformVon(knoten: AnalyserNode) {
    zierStoppen()
    analyser = knoten
    const daten = new Uint8Array(knoten.frequencyBinCount)
    cancelAnimationFrame(animation)

    const anzahl = pegel.value.length
    const zeichnen = () => {
      if (!analyser) return
      analyser.getByteFrequencyData(daten)
      pegel.value = verteilePegel(daten, anzahl)
      animation = requestAnimationFrame(zeichnen)
    }
    zeichnen()
  }

  /** Holt den Mikrofonstrom und hängt einen Analyser daran.
   *
   * Der Strom bleibt fürs ganze Gespräch offen: Ihn bei jedem Antippen neu zu
   * holen kostete spürbar Zeit und – je nach Browser – eine erneute Abfrage.
   */
  async function mikrofonVorbereiten(): Promise<boolean> {
    if (mikroKnoten) return true
    if (!navigator.mediaDevices?.getUserMedia) return false
    try {
      mikroStrom = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch {
      return false
    }
    const knoten = ctx().createAnalyser()
    // 128 statt 64: ergibt 64 Bänder für 28 Balken, also mehr als genug.
    knoten.fftSize = 128
    // Ohne Glättung zappelt die Anzeige; mit zu viel wirkt sie träge.
    knoten.smoothingTimeConstant = 0.55
    ctx().createMediaStreamSource(mikroStrom).connect(knoten)
    mikroKnoten = knoten
    return true
  }

  function mikrofonFreigeben() {
    mikroStrom?.getTracks().forEach((t) => t.stop())
    mikroStrom = null
    mikroKnoten = null
  }

  /** Ersatz, wenn kein Mikrofonpegel zu bekommen ist – Lebenszeichen, keine Messung. */
  function zierStarten() {
    zierStoppen()
    const start = performance.now()
    ziertakt = window.setInterval(() => {
      const t = (performance.now() - start) / 240
      pegel.value = pegel.value.map(
        (_, i) => 0.12 + 0.34 * Math.abs(Math.sin(t + i * 0.45)) * (0.6 + 0.4 * Math.random()),
      )
    }, 90)
  }

  function zierStoppen() {
    if (ziertakt) {
      clearInterval(ziertakt)
      ziertakt = 0
    }
  }

  function wellenformStoppen() {
    cancelAnimationFrame(animation)
    zierStoppen()
    analyser = null
    pegel.value = pegel.value.map(() => 0.06)
    // Der Mikrofonstrom bleibt bestehen – er gilt fürs ganze Gespräch.
  }

  // --- WebSocket ---------------------------------------------------------

  function verbinden(): Promise<void> {
    return new Promise((aufloesen, ablehnen) => {
      const schema = location.protocol === 'https:' ? 'wss' : 'ws'
      ws = new WebSocket(`${schema}://${location.host}/ws/web`)
      ws.binaryType = 'arraybuffer'
      status.value = 'verbindet'

      ws.onopen = () => {
        verbunden.value = true
        aufloesen()
      }
      ws.onerror = () => {
        verbunden.value = false
        status.value = 'fehler'
        if (!fehler.value) fehler.value = 'Die Verbindung zum Server ist fehlgeschlagen.'
        ablehnen(new Error('websocket'))
      }
      ws.onclose = () => {
        verbunden.value = false
        if (status.value !== 'fehler') status.value = 'beendet'
        wiedergabeStoppen()
      }
      ws.onmessage = (event) => nachricht(event)
    })
  }

  function nachricht(event: MessageEvent) {
    if (event.data instanceof ArrayBuffer) {
      if (!erwarteterSatz) return
      erwarteterSatz = null
      warteschlange.push(event.data)
      void naechstesAbspielen()
      return
    }

    const msg = JSON.parse(event.data)
    switch (msg.type) {
      case 'ready':
        conversationId.value = msg.conversation_id
        beitragHinzufuegen('resa', msg.greeting)
        status.value = 'bereit'
        break

      case 'assistant_delta':
        if (!laufenderResaBeitrag) laufenderResaBeitrag = beitragHinzufuegen('resa', '', true)
        laufenderResaBeitrag.text += msg.text
        break

      case 'speech':
        erwarteterSatz = { text: msg.text }
        break

      case 'speech_failed':
        beitragHinzufuegen('system', `Sprachausgabe nicht möglich: ${msg.detail}`)
        break

      case 'tool':
        if (msg.signals?.includes('escalated')) {
          beitragHinzufuegen('system', 'Notdienst wurde alarmiert.')
        } else if (msg.signals?.includes('escalation_failed')) {
          beitragHinzufuegen('system', 'Alarmierung fehlgeschlagen.')
        } else if (msg.signals?.includes('intake_logged')) {
          beitragHinzufuegen('system', 'Fall wurde erfasst.')
        }
        break

      case 'turn_done':
        if (laufenderResaBeitrag) laufenderResaBeitrag.laufend = false
        laufenderResaBeitrag = null
        // Zurück in einen bedienbaren Zustand, sofern nicht gerade Audio läuft
        // oder zugehört wird. Vorher wurde nur aus 'denkt' zurückgesetzt – jeder
        // andere Zustand blieb hängen und die Oberfläche war blockiert.
        if (status.value !== 'spricht' && status.value !== 'hoert') {
          status.value = 'bereit'
        }
        break

      case 'interrupted':
        wiedergabeStoppen()
        if (laufenderResaBeitrag) laufenderResaBeitrag.laufend = false
        laufenderResaBeitrag = null
        if (status.value !== 'hoert') status.value = 'bereit'
        break

      case 'error':
        fehler.value = msg.detail
        status.value = 'fehler'
        break
    }
  }

  // --- Steuerung ---------------------------------------------------------

  async function starten() {
    fehler.value = null
    beitraege.value = []
    // Den Audiokontext im Klick anlegen: Mobile Browser erlauben Tonausgabe
    // nur, wenn sie aus einer Nutzeraktion hervorgeht.
    try {
      if (ctx().state === 'suspended') await ctx().resume()
    } catch {
      /* wird beim ersten Abspielen erneut versucht */
    }
    try {
      await verbinden()
    } catch {
      // Die Meldung steht bereits in `fehler`; hier nur verhindern, dass die
      // Rejection unbehandelt aus dem Klick-Handler fliegt.
    }
  }

  function senden(text: string) {
    const sauber = text.trim()
    if (!sauber || !ws || ws.readyState !== WebSocket.OPEN) return
    beitragHinzufuegen('anrufer', sauber)
    zwischentext.value = ''
    laufenderResaBeitrag = null
    status.value = 'denkt'
    ws.send(JSON.stringify({ type: 'user_text', text: sauber }))
  }

  function unterbrechen() {
    wiedergabeStoppen()
    if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'interrupt' }))
  }

  function beenden() {
    zuhoerenStoppen()
    wiedergabeStoppen()
    mikrofonFreigeben()
    if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'end' }))
    ws?.close()
    ws = null
    verbunden.value = false
    status.value = 'beendet'
  }

  function zuhoeren() {
    const Impl = erkennungKlasse()
    if (!Impl) {
      fehler.value =
        'Dieser Browser unterstützt keine Spracherkennung. Nutze das Textfeld – Resa antwortet trotzdem mit Stimme.'
      return
    }
    // Läuft die Erkennung bereits, gilt das Antippen als „fertig gesprochen“.
    if (recognition) {
      recognition.stop()
      return
    }
    // Spricht Resa noch, ist das Antippen eine Unterbrechung.
    if (spielt) unterbrechen()

    fehler.value = null
    zwischentext.value = ''

    const erkenner = new Impl()
    recognition = erkenner
    erkenner.lang = 'de-DE'
    // Zwischenergebnisse anzeigen: Sonst sieht niemand, ob überhaupt etwas
    // ankommt – genau das machte den Fehler vorher unsichtbar.
    erkenner.interimResults = true
    erkenner.continuous = false
    erkenner.maxAlternatives = 1

    let endgueltig = ''

    erkenner.onstart = () => {
      status.value = 'hoert'
      void mikrofonVorbereiten().then((ok) => {
        // Nur anhängen, wenn immer noch zugehört wird – die Erkennung kann
        // in der Zwischenzeit geendet haben.
        if (status.value !== 'hoert') return
        if (ok && mikroKnoten) wellenformVon(mikroKnoten)
        else zierStarten()
      })
    }

    erkenner.onresult = (event: any) => {
      let vorlaeufig = ''
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const treffer = event.results[i]
        if (treffer.isFinal) endgueltig += treffer[0].transcript
        else vorlaeufig += treffer[0].transcript
      }
      zwischentext.value = (endgueltig + vorlaeufig).trim()
    }

    erkenner.onerror = (event: any) => {
      const code = event?.error ?? 'unbekannt'
      // 'no-speech' nach einem erkannten Satz ist normal und keine Meldung wert.
      if (!(code === 'no-speech' && endgueltig.trim())) {
        fehler.value = ERKENNUNGSFEHLER[code] ?? `Spracherkennung fehlgeschlagen (${code}).`
      }
    }

    erkenner.onend = () => {
      recognition = null
      wellenformStoppen()
      const text = endgueltig.trim() || zwischentext.value.trim()
      zwischentext.value = ''
      if (text) {
        senden(text)
      } else if (status.value === 'hoert') {
        status.value = 'bereit'
      }
    }

    try {
      erkenner.start()
    } catch (ausnahme) {
      recognition = null
      fehler.value = `Die Spracherkennung ließ sich nicht starten: ${
        ausnahme instanceof Error ? ausnahme.message : ausnahme
      }`
      status.value = 'bereit'
    }
  }

  function zuhoerenStoppen() {
    if (recognition) {
      recognition.onend = null
      recognition.abort()
      recognition = null
    }
    zwischentext.value = ''
    wellenformStoppen()
  }

  onBeforeUnmount(() => {
    zuhoerenStoppen()
    wiedergabeStoppen()
    mikrofonFreigeben()
    ws?.close()
    void audioCtx?.close()
  })

  return {
    status,
    beitraege,
    fehler,
    pegel,
    zwischentext,
    conversationId,
    verbunden,
    hoertZu,
    spracherkennungMoeglich,
    starten,
    senden,
    zuhoeren,
    unterbrechen,
    beenden,
  }
}
