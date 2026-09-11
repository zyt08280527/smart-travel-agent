const API_BASE_URL = (
  import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000'
).replace(/\/$/, '')

export type HealthResponse = {
  status: 'ok'
  tools: string[]
}

export type ApprovalDecision = 'approve' | 'reject'

export type PendingAction = {
  name: string
  args: Record<string, unknown>
  description: string
  allowed_decisions: ApprovalDecision[]
}

export type AgentResponse = {
  status: 'completed' | 'approval_required'
  thread_id: string
  answer: string | null
  pending_actions: PendingAction[]
}

export type AgentStreamEventType =
  | 'run_started'
  | 'assistant_delta'
  | 'tool_requested'
  | 'tool_completed'
  | 'result_card'
  | 'approval_required'
  | 'final'
  | 'error'
  | 'done'

export type WeatherResultCard = {
  type: 'weather'
  city: string
  country?: string
  admin1?: string
  temperature_c: number
  apparent_temperature_c: number
  precipitation_mm: number
  wind_speed_kmh: number
  condition: string
  observed_at: string
}

export type RouteResultCard = {
  type: 'route'
  mode: 'driving' | 'walking'
  distance_m: number
  duration_s: number
  duration_basis?: 'static_without_live_traffic' | 'traffic_aware_estimate'
  tolls_yuan?: number | null
  taxi_cost_yuan?: number | null
  traffic_lights?: number | null
  restriction?: 0 | 1 | null
  traffic_status_counts?: Record<string, number>
  step_count: number
  attribution: string
}

export type TransitResultCard = {
  type: 'transit'
  option_count: number
  distance_m: number
  duration_s: number
  walking_distance_m: number
  cost_yuan?: number | null
  transfer_count: number
  line_names: string[]
  attribution: string
}

export type PlaceCandidateCard = {
  display_name: string
  latitude: number
  longitude: number
  category?: string
  place_type?: string
}

export type PlaceResultCard = {
  type: 'place'
  query: string
  places: PlaceCandidateCard[]
  attribution: string
}

export type ResultCard =
  | WeatherResultCard
  | RouteResultCard
  | TransitResultCard
  | PlaceResultCard

export type ConversationStatus = 'ready' | 'approval_required' | 'error'

export type ConversationSummary = {
  thread_id: string
  title: string
  status: ConversationStatus
  created_at: string
  updated_at: string
}

export type StoredConversationMessage = {
  message_id: string
  thread_id: string
  role: 'user' | 'assistant'
  content: string
  cards: ResultCard[]
  created_at: string
}

export type ConversationDetail = {
  conversation: ConversationSummary
  messages: StoredConversationMessage[]
}

export type AgentStreamEvent = {
  type: AgentStreamEventType
  thread_id: string
  delta?: string
  tool_name?: string
  tool_args?: Record<string, unknown>
  tool_call_id?: string
  card?: ResultCard
  pending_actions?: PendingAction[]
  answer?: string
  error?: string
}

async function requestJson<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, options)
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`)
  }
  return (await response.json()) as T
}

export function getHealth(): Promise<HealthResponse> {
  return requestJson<HealthResponse>('/health')
}

export function getConversations(): Promise<ConversationSummary[]> {
  return requestJson<ConversationSummary[]>('/api/conversations')
}

export function getConversation(threadId: string): Promise<ConversationDetail> {
  return requestJson<ConversationDetail>(
    `/api/conversations/${encodeURIComponent(threadId)}`,
  )
}

export async function deleteConversation(threadId: string): Promise<void> {
  const response = await fetch(
    `${API_BASE_URL}/api/conversations/${encodeURIComponent(threadId)}`,
    { method: 'DELETE' },
  )
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`)
  }
}

export function sendChat(
  message: string,
  threadId?: string,
): Promise<AgentResponse> {
  return requestJson<AgentResponse>('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      message,
      ...(threadId ? { thread_id: threadId } : {}),
    }),
  })
}

function emitNdjsonLine(
  line: string,
  onEvent: (event: AgentStreamEvent) => void,
) {
  const normalizedLine = line.trim()
  if (!normalizedLine) {
    return
  }
  onEvent(JSON.parse(normalizedLine) as AgentStreamEvent)
}

export async function streamChat(
  message: string,
  threadId: string | undefined,
  onEvent: (event: AgentStreamEvent) => void,
): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      message,
      ...(threadId ? { thread_id: threadId } : {}),
    }),
  })
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`)
  }
  if (!response.body) {
    throw new Error('浏览器没有提供可读取的流式响应')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { value, done } = await reader.read()
    if (done) {
      break
    }

    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''
    for (const line of lines) {
      emitNdjsonLine(line, onEvent)
    }
  }

  buffer += decoder.decode()
  emitNdjsonLine(buffer, onEvent)
}

export function decideApproval(
  threadId: string,
  decision: ApprovalDecision,
  message?: string,
): Promise<AgentResponse> {
  return requestJson<AgentResponse>(`/api/threads/${threadId}/approval`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ decision, message }),
  })
}
