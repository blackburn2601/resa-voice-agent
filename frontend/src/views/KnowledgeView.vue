<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { api, type IndexStatus, type KnowledgeDocument } from '@/api/client'
import { useLaden } from '@/stores/laden'

const laden = useLaden()
const dokumente = ref<KnowledgeDocument[]>([])
const zustand = ref<IndexStatus | null>(null)
const meldung = ref<{ art: 'ok' | 'fehler'; text: string } | null>(null)
const laeuft = ref(false)

const frage = ref('')
const antwort = ref('')
const dateiFeld = ref<HTMLInputElement | null>(null)

async function neuLaden() {
  const [liste, status] = await Promise.all([api.documents(), api.indexStatus()])
  dokumente.value = liste
  zustand.value = status
}

onMounted(neuLaden)

/** `schirm` nur für Vorgänge, die spürbar dauern – Einbetten und Indizieren
 *  brauchen Sekunden, eine reine Knopfsperre wirkt da wie ein Hänger. */
async function mit<T>(aufgabe: () => Promise<T>, erfolg: string, schirm?: string) {
  laeuft.value = true
  meldung.value = null
  if (schirm) laden.starten(schirm)
  try {
    await aufgabe()
    await neuLaden()
    meldung.value = { art: 'ok', text: erfolg }
  } catch (ausnahme) {
    meldung.value = {
      art: 'fehler',
      text: ausnahme instanceof Error ? ausnahme.message : 'Fehlgeschlagen.',
    }
  } finally {
    if (schirm) laden.beenden()
    laeuft.value = false
  }
}

function faqAnlegen() {
  const f = frage.value.trim()
  const a = antwort.value.trim()
  if (!f || !a) return
  void mit(
    () => api.createFaq(f, a),
    'Eintrag angelegt und indiziert.',
    'Eintrag wird eingebettet',
  ).then(() => {
    if (meldung.value?.art === 'ok') {
      frage.value = ''
      antwort.value = ''
    }
  })
}

function hochladen(event: Event) {
  const datei = (event.target as HTMLInputElement).files?.[0]
  if (!datei) return
  void mit(() => api.upload(datei), `${datei.name} aufgenommen.`, 'Dokument wird eingelesen').then(() => {
    if (dateiFeld.value) dateiFeld.value.value = ''
  })
}

function loeschen(dokument: KnowledgeDocument) {
  if (!confirm(`„${dokument.title}" aus der Wissensbasis entfernen?`)) return
  void mit(() => api.deleteDocument(dokument.id), 'Entfernt.')
}

function neuIndizieren() {
  void mit(() => api.reindex(), 'Abgeglichen.', 'Index wird abgeglichen')
}

const quellNamen: Record<string, string> = {
  seed: 'Repo',
  upload: 'Upload',
  faq: 'FAQ',
}
</script>

<template>
  <div class="seite">
    <header class="kopf">
      <h1>Wissensbasis</h1>
      <button class="knopf" type="button" :disabled="laeuft" @click="neuIndizieren">
        Index abgleichen
      </button>
    </header>

    <p class="leise erklaerung">
      Woraus Resa ihre Auskünfte zieht. Was hier nicht steht, erfindet sie nicht – sie sagt
      dann offen, dass sie es nicht weiß, und bietet einen Rückruf an.
    </p>

    <div v-if="zustand" class="karte zustand" :class="{ abweichung: !zustand.in_sync }">
      <div>
        <span class="wert mono">{{ zustand.documents }}</span>
        <span class="still">Dokumente</span>
      </div>
      <div>
        <span class="wert mono">{{ zustand.chunks }}</span>
        <span class="still">Abschnitte</span>
      </div>
      <div>
        <span class="wert mono">{{ zustand.vectors }}</span>
        <span class="still">Vektoren</span>
      </div>
      <div v-if="zustand.pending || zustand.failed">
        <span class="wert mono">{{ zustand.pending + zustand.failed }}</span>
        <span class="still">offen</span>
      </div>
      <span class="marke" :class="zustand.in_sync ? 'ruhig' : 'dringend'">
        {{ zustand.in_sync ? 'synchron' : 'Abweichung' }}
      </span>
    </div>

    <p v-if="meldung" class="hinweisbox" :class="{ fehler: meldung.art === 'fehler' }">
      {{ meldung.text }}
    </p>

    <div class="raster">
      <section class="karte block">
        <h2>Frage und Antwort ergänzen</h2>
        <p class="still">
          Frage und Antwort werden gemeinsam abgelegt – beim Suchen trifft die Frage,
          gebraucht wird die Antwort.
        </p>
        <div class="feldgruppe">
          <label class="beschriftung" for="frage">Frage</label>
          <input
            id="frage"
            v-model="frage"
            class="feld"
            type="text"
            placeholder="Darf ich in der Wohnung einen Hund halten?"
          />
        </div>
        <div class="feldgruppe">
          <label class="beschriftung" for="antwort">Antwort</label>
          <textarea id="antwort" v-model="antwort" class="feld" rows="5" />
        </div>
        <button
          class="knopf haupt"
          type="button"
          :disabled="laeuft || !frage.trim() || !antwort.trim()"
          @click="faqAnlegen"
        >
          Eintrag anlegen
        </button>
      </section>

      <section class="karte block">
        <h2>Dokument hochladen</h2>
        <p class="still">
          Hausordnung, Mietvertrags-Anlagen, Notdienstpläne. Erlaubt sind PDF, DOCX, Markdown
          und Text bis 20 MB.
        </p>
        <input
          ref="dateiFeld"
          class="feld datei"
          type="file"
          accept=".pdf,.docx,.md,.txt"
          :disabled="laeuft"
          @change="hochladen"
        />
      </section>
    </div>

    <section class="karte">
      <div class="tabellenrahmen">
        <table class="liste">
          <thead>
            <tr>
              <th>Titel</th>
              <th>Quelle</th>
              <th>Abschnitte</th>
              <th>Indiziert</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="dokument in dokumente" :key="dokument.id" class="ohnezeiger">
              <td>
                {{ dokument.title }}
                <span v-if="dokument.filename" class="still mono">{{ dokument.filename }}</span>
              </td>
              <td>
                <span class="marke normal">{{ quellNamen[dokument.source_type] }}</span>
              </td>
              <td class="mono">{{ dokument.chunks }}</td>
              <td>
                <span v-if="dokument.indexed_at" class="marke ruhig">ja</span>
                <span v-else class="marke dringend">offen</span>
              </td>
              <td>
                <button
                  class="knopf schlicht"
                  type="button"
                  :disabled="laeuft"
                  @click="loeschen(dokument)"
                >
                  Entfernen
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-if="!dokumente.length" class="leer still">Noch nichts hinterlegt.</p>
    </section>
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

.kopf {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  flex-wrap: wrap;
}

.erklaerung {
  max-width: 70ch;
}

.zustand {
  display: flex;
  align-items: center;
  gap: 2rem;
  padding: 1rem 1.25rem;
  flex-wrap: wrap;
}
.zustand.abweichung {
  border-color: var(--dringend);
}
.zustand > div {
  display: flex;
  flex-direction: column;
}
.wert {
  font-size: 1.35rem;
  font-weight: 700;
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
  gap: 0.75rem;
}

.feldgruppe {
  display: flex;
  flex-direction: column;
}

textarea.feld {
  resize: vertical;
  font-family: inherit;
}

.datei {
  padding: 0.5rem;
}

.ohnezeiger {
  cursor: default;
}
.ohnezeiger:hover {
  background: transparent;
}

td .still {
  display: block;
  font-size: 0.78rem;
}

.leer {
  padding: 2rem 1rem;
  text-align: center;
}
</style>
