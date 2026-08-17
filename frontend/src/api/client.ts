/**
 * Zugriff auf die API.
 *
 * Das Sitzungstoken liegt in einem httpOnly-Cookie – der Browser schickt es von
 * selbst mit. Im Frontend gibt es deshalb bewusst nirgends ein Token zu
 * verwalten oder zu speichern.
 */

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message)
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api${path}`, {
    credentials: 'same-origin',
    ...init,
  })

  if (response.status === 204) return undefined as T

  const text = await response.text()
  const data = text ? JSON.parse(text) : null

  if (!response.ok) {
    const detail =
      (data && typeof data.detail === 'string' && data.detail) ||
      `Die Anfrage ist fehlgeschlagen (${response.status}).`
    throw new ApiError(response.status, detail)
  }
  return data as T
}

function json<T>(path: string, method: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method,
    headers: { 'content-type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
}

// --- Typen ---------------------------------------------------------------

export type Urgency = 'notfall' | 'dringend' | 'normal'

export interface User {
  email: string
  role: string
}

export interface ConversationItem {
  id: string
  started_at: string
  ended_at: string | null
  status: string
  caller_name: string | null
  category: string | null
  urgency: Urgency | null
  summary: string | null
  escalated: boolean
  incomplete: boolean
}

export interface TranscriptMessage {
  seq: number
  role: string
  content: string
  created_at: string
}

export interface Intake {
  caller_name: string
  category: string
  summary: string
  property_address: string | null
  urgency: Urgency
  occurred_since: string | null
  danger_to_life: boolean
  callback_requested: boolean
  preferred_callback_time: string | null
  caller_phone: string | null
  incomplete: boolean
}

export interface EscalationAttempt {
  channel: string
  status: string
  detail: string | null
  attempted_at: string
}

export interface Escalation {
  triggered_at: string
  reason: string
  summary: string
  status: string
  attempts: EscalationAttempt[]
}

export interface ConversationDetail extends ConversationItem {
  messages: TranscriptMessage[]
  intake: Intake | null
  escalation: Escalation | null
}

export interface Stats {
  gespraeche: number
  notfaelle: number
  alarmierungen: number
  laufend: number
}

export interface KnowledgeDocument {
  id: number
  title: string
  source_type: string
  filename: string | null
  chunks: number
  created_at: string
  indexed_at: string | null
}

export interface IndexStatus {
  documents: number
  chunks: number
  vectors: number
  pending: number
  failed: number
  in_sync: boolean
}

export interface Contact {
  id: number
  name: string
  email: string
  phone: string | null
  active: boolean
  created_at: string
}

export interface Health {
  status: string
  checks: Record<string, { ok: boolean; detail: string }>
  config: Record<string, boolean>
}

// --- Aufrufe -------------------------------------------------------------

export const api = {
  login: (email: string, password: string) =>
    json<User>('/auth/login', 'POST', { email, password }),
  logout: () => json<void>('/auth/logout', 'POST'),
  me: () => request<User>('/auth/me'),

  health: () => request<Health>('/health'),
  stats: () => request<Stats>('/conversations/stats'),

  conversations: (params: { urgency?: Urgency; status?: string } = {}) => {
    const query = new URLSearchParams()
    if (params.urgency) query.set('urgency', params.urgency)
    if (params.status) query.set('status', params.status)
    const suffix = query.toString() ? `?${query}` : ''
    return request<ConversationItem[]>(`/conversations${suffix}`)
  },
  conversation: (id: string) => request<ConversationDetail>(`/conversations/${id}`),

  documents: () => request<KnowledgeDocument[]>('/knowledge/documents'),
  indexStatus: () => request<IndexStatus>('/knowledge/status'),
  reindex: () => json<IndexStatus>('/knowledge/reindex', 'POST'),
  createFaq: (question: string, answer: string) =>
    json<KnowledgeDocument>('/knowledge/faq', 'POST', { question, answer }),
  deleteDocument: (id: number) => request<void>(`/knowledge/documents/${id}`, { method: 'DELETE' }),
  upload: (file: File) => {
    const body = new FormData()
    body.append('file', file)
    return request<KnowledgeDocument>('/knowledge/upload', { method: 'POST', body })
  },

  contacts: () => request<Contact[]>('/settings/on-call'),
  createContact: (payload: { name: string; email: string; phone?: string | null }) =>
    json<Contact>('/settings/on-call', 'POST', payload),
  activateContact: (id: number) => json<Contact>(`/settings/on-call/${id}/activate`, 'POST'),
  deleteContact: (id: number) =>
    request<void>(`/settings/on-call/${id}`, { method: 'DELETE' }),
}
