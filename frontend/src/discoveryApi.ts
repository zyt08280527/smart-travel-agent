import { API_BASE_URL } from './api'
import type { PlanningPreferences } from './api'
import type { MultiStopPlace } from './multiStopApi'
import type { MultiDraft } from './multiStopApi'
import type { DiscoveryResult } from './DiscoveryWorkspace'

export type DiscoveryCriteria = {
  origin: MultiStopPlace
  departure_at: string | null
  available_minutes: number
  stay_minutes: number
  budget_yuan: number | null
  require_confirmed_open: boolean
  weather_policy: 'none' | 'inform' | 'exclude_signals'
  preferences: PlanningPreferences
  excluded_attraction_ids?: string[]
}
export type DiscoveryChanges = Partial<Pick<DiscoveryCriteria,
  'budget_yuan' | 'available_minutes' | 'stay_minutes' | 'excluded_attraction_ids'> & PlanningPreferences>
export type DiscoveryConversation = {
  request: DiscoveryCriteria
  candidate_ids: string[]
  selected_attraction_id?: string | null
  pending_changes?: DiscoveryChanges | null
  thread_id?: string
  expected_revision?: number
}
export type DiscoveryHistoryInfo = { thread_id: string; revision: number; assistant_content?: string | null }
export type DiscoverySnapshot = {
  request: DiscoveryCriteria; result: DiscoveryResult; pending_changes?: DiscoveryChanges | null
  notice?: string; stage?: 'candidates' | 'journey'; selected_attraction_id?: string | null
  draft_id?: string | null; plan_id?: string | null; revision: number; anchor_message_id?: string | null
}
export type DiscoveryRefinement = {
  status: 'updated' | 'clarification' | 'not_applicable' | 'cancelled'
  request: DiscoveryCriteria
  pending_changes: DiscoveryChanges | null
  result: DiscoveryResult | null
  answer: string
  history?: DiscoveryHistoryInfo | null
}

async function historyRequest<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  const data = await response.json()
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '会话同步失败，请重新打开历史对话后继续。')
  return data as T
}
export const chooseDiscoveryFromHistory = (context: DiscoveryConversation, attractionId: string, message?: string) =>
  historyRequest<{ draft: MultiDraft; history: DiscoveryHistoryInfo }>(`/api/conversations/${context.thread_id}/discovery/choose`, { attraction_id: attractionId, expected_revision: context.expected_revision, message })
export const syncDiscoverySelection = (thread: string, revision: number, draft: string, plan: string) =>
  historyRequest<DiscoveryHistoryInfo>(`/api/conversations/${thread}/discovery/selection`, { expected_revision: revision, draft_id: draft, plan_id: plan })

export async function refineDiscovery(context: DiscoveryConversation, message: string): Promise<DiscoveryRefinement> {
  const response = await fetch(`${API_BASE_URL}/api/discovery/refine`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...context, message }),
  })
  const value = await response.json()
  if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : '推荐条件更新失败，请检查输入后重试。')
  return value as DiscoveryRefinement
}

export function isDiscoveryEdit(message: string): boolean {
  return /预算|(?:可用|往返|总共|总时|只有|仅有|我有|只剩|停留|游览|参观).*?(?:小时|分钟)|换(?:一)?个|换掉|排除|不去|不要推荐|别推荐|少步行|少换乘|地铁优先|省钱优先|时间优先|综合推荐|不能开车|不能驾车|可以驾车|可以开车|最多步行|最多换乘|取消步行|取消换乘|取消修改|放弃修改|重新推荐|再推荐|恢复(?:全部|所有)候选/.test(message)
}
