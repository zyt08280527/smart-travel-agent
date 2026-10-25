import { API_BASE_URL } from './api'
import type { MapPoint, PlanningPreferences } from './api'

export type MultiStopPlace = MapPoint & { display_name: string; city_code: string | null; provider_place_id?: string | null; location_source?: 'device' | null; location_accuracy_m?: number | null; location_captured_at?: string | null }
export type Stop = { stop_id: string; name: string; city_code: string; point: MapPoint; provider_place_id?: string | null; stay_minutes: number; earliest_arrival_at?: string | null; latest_arrival_at?: string | null }
export type MultiRequest = {
  city_code: string; city_name?: string | null; stops: Stop[]
  departure_time?: { departure_at: string; timezone: string; precision: 'exact' | 'now'; source_text: string } | null
  arrival_deadline?: { arrival_by: string; timezone: string; precision: 'exact'; source_text: string; buffer_minutes: number } | null
  preferences: PlanningPreferences
  max_transport_cost_yuan?: number | null
  max_elapsed_minutes?: number | null
}
export type MultiOption = { mode: 'walking' | 'driving' | 'transit'; status?: string; geometry: MapPoint[]; duration_s: number; attribution: string; cost_yuan: number | null; walking_distance_m: number | null; transfer_count: number | null }
export type MultiLeg = { origin_stop_id: string; destination_stop_id: string; candidate_id: string; fetched_at: string; option: MultiOption; transit_details?: { legs: { line_name?: string | null; instruction?: string | null }[] } | null; route_details?: { steps: { instruction?: string | null }[] } | null }
export type MultiPlan = {
  plan_id: string
  evaluation: {
    status: string
    timing_mode?: 'relative' | 'scheduled'
    stops: { stop_id: string; name: string; physical_arrival_at: string | null; arrival_at: string | null; departure_at: string | null; waiting_s: number; stay_s: number }[]
    legs: { leg_index: number; leg: MultiLeg; departure_at: string | null; arrival_at: string | null }[]
    totals: { elapsed_s: number; travel_s: number; waiting_s: number; stay_s: number; walking_distance_m: number | null; transfer_count: number | null; total_cost_yuan: number | null; known_cost_subtotal_yuan: number }
    limitations: string[]
  }
  reasons: string[]
}
export type MultiDraft = { draft_id: string; created_at: string; result: { request: MultiRequest; segments?: { candidates: MultiLeg[] }[]; selection: { status: string; recommended_plan_id: string | null; selected_plan_id?: string | null; plans: MultiPlan[]; issue_examples: { code: string; message: string }[]; limitations: string[] } } }
export type SavedMulti = { itinerary_id: string; draft: MultiDraft; selected_plan_id: string; saved_at: string }

async function request<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, body === undefined ? undefined : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}))
    throw new Error(typeof payload.detail === 'string' ? payload.detail : '请求未完成，请检查输入后重试。')
  }
  return response.json() as Promise<T>
}
export const searchMultiPlaces = (query: string) => request<{ places: MultiStopPlace[]; attribution: string; provider?: 'amap' | 'openstreetmap' }>(`/api/places?query=${encodeURIComponent(query)}`)
export const planMultiStop = (body: MultiRequest) => request<MultiDraft>('/api/multi-stop/plan', body)
export const getMultiDraft = (id: string) => request<MultiDraft>(`/api/multi-stop/drafts/${encodeURIComponent(id)}`)
export const saveMultiStop = (id: string, planId: string) => request<SavedMulti>(`/api/multi-stop/drafts/${encodeURIComponent(id)}/save`, { selected_plan_id: planId })
export const listMultiStop = () => request<SavedMulti[]>('/api/multi-stop/itineraries')
export const selectMultiStop = (id: string, candidateIds: string[]) => request<MultiDraft>(`/api/multi-stop/drafts/${encodeURIComponent(id)}/select`, { candidate_ids: candidateIds })
