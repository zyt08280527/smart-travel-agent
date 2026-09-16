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
  options?: PlanningTransitCandidate[]
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

export type PlanningPreferences = {
  priority:
    | 'balanced'
    | 'fastest'
    | 'cheapest'
    | 'least_walking'
    | 'fewest_transfers'
  transit_strategy?:
    | 'recommended'
    | 'subway_first'
    | 'fewest_transfers'
    | 'least_walking'
  can_drive?: boolean | null
  max_walking_distance_m?: number | null
  max_transfer_count?: number | null
}

export type PlanningOption = {
  mode: 'driving' | 'walking' | 'transit'
  total_score: number
  duration_s: number
  cost_yuan?: number | null
  walking_distance_m?: number | null
  transfer_count?: number | null
  latest_departure_at?: string | null
}

export type PlanningExcludedOption = {
  mode: 'driving' | 'walking' | 'transit'
  reason: string
}

export type PlanningVariant = {
  priority: PlanningPreferences['priority']
  recommended_mode: 'driving' | 'walking' | 'transit'
  ranked_options: PlanningOption[]
}

export type PlanningTransitCandidate = {
  candidate_index: number
  selected: boolean
  duration_s: number
  cost_yuan?: number | null
  walking_distance_m: number
  transfer_count: number
  line_names: string[]
  legs?: PlanningTransitLeg[]
}

export type PlanningTransitLeg = {
  mode: 'walking' | 'bus' | 'subway' | 'railway' | 'taxi'
  distance_m: number
  duration_s?: number | null
  instruction?: string | null
  line_name?: string | null
  departure_stop?: string | null
  arrival_stop?: string | null
  via_stop_count?: number | null
}

export type PlanningResultCard = {
  type: 'planning'
  origin_name: string
  destination_name: string
  recommended_mode: 'driving' | 'walking' | 'transit'
  previous_recommended_mode?: 'driving' | 'walking' | 'transit' | null
  reused_previous_data: boolean
  route_refreshed: boolean
  used_stale_snapshot: boolean
  refresh_failed: boolean
  route_snapshot_at?: string | null
  arrival_by?: string | null
  arrival_buffer_minutes?: number | null
  preferences: PlanningPreferences
  ranked_options: PlanningOption[]
  recommendation_variants?: PlanningVariant[]
  unavailable_options: PlanningExcludedOption[]
  transit_candidates?: PlanningTransitCandidate[]
  selected_transit_candidate_index?: number | null
}

export type ResultCard =
  | WeatherResultCard
  | RouteResultCard
  | TransitResultCard
  | PlaceResultCard
  | PlanningResultCard

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
