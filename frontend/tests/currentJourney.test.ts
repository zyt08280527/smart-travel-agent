import { expect, test } from 'vitest'
import type { PlanningResultCard } from '../src/api'
import { latestJourney, preservePlanningOrder, restoreJourney } from '../src/currentJourney'

const driving: PlanningResultCard = {
  type: 'planning', origin_name: '起点', destination_name: '终点', recommended_mode: 'driving',
  selected_plan_id: 'driving:0', preferences: { priority: 'balanced' }, ranked_options: [],
  reused_previous_data: false, route_refreshed: false, used_stale_snapshot: false, refresh_failed: false,
}
const walking: PlanningResultCard = { ...driving, selected_mode: 'walking', selected_plan_id: 'walking:0' }
const messages = [{ id: 'original', cards: [driving] }, { id: 'text-only' }]

test('旧版会话从最后一个路线结果恢复，普通文字回复不清空路线', () => {
  expect(restoreJourney(undefined, messages, 'thread-a')?.card).toBe(driving)
  expect(latestJourney([...messages, { id: 'updated', cards: [walking] }], 'thread-a')?.card).toBe(walking)
})

test('独立当前行程优先于未改写的历史消息快照', () => {
  const current = { threadId: 'thread-a', card: walking, anchor: { messageId: 'original', cardIndex: 0 } }
  expect(restoreJourney(current, messages, 'thread-a')?.card).toBe(walking)
  expect(messages[0].cards?.[0]).toBe(driving)
})

test('拒绝复用其他会话的当前行程', () => {
  const current = { threadId: 'thread-a', card: walking, anchor: { messageId: 'original', cardIndex: 0 } }
  expect(restoreJourney(current, messages, 'thread-b')?.card).toBe(driving)
  expect(restoreJourney(current, [], 'thread-b')).toBeNull()
  expect(latestJourney(messages, '')).toBeNull()
})

test('当前行程恢复不再依赖聊天展示锚点或历史数组位置', () => {
  const current = { threadId: 'thread-a', card: walking, anchor: { messageId: 'missing', cardIndex: 0 } }
  expect(restoreJourney(current, [], 'thread-a')?.card).toBe(walking)
})

test('新的多站点结果不会恢复成更早的单段路线', () => {
  const latest = latestJourney([...messages, { id: 'multi', cards: [{ type: 'multi_stop', draft_id: 'draft', stop_names: ['A', 'B', 'C'], status: 'feasible' }] }], 'thread-a')
  expect(latest?.card.type).toBe('multi_stop')
  expect(restoreJourney(latest, messages, 'thread-a')?.card).toBe(latest?.card)
})

test('手动选择保留原显示顺序，而刷新或新目的地采用新顺序，不改写原快照', () => {
  const first = { plan_id: 'driving:0', mode: 'driving' as const, title: '驾车', selected: true, recommended: true, duration_s: 100 }
  const second = { ...first, plan_id: 'walking:0', mode: 'walking' as const, title: '步行', selected: false, recommended: false }
  const previous = { ...driving, route_candidates: [first, second] }
  const incoming = { ...walking, route_candidates: [second, first] }
  expect(preservePlanningOrder(previous, incoming).route_candidates?.map((candidate) => candidate.plan_id)).toEqual(['driving:0', 'walking:0'])
  expect(incoming.route_candidates.map((candidate) => candidate.plan_id)).toEqual(['walking:0', 'driving:0'])
  expect(preservePlanningOrder(previous, { ...incoming, route_refreshed: true }).route_candidates).toBe(incoming.route_candidates)
  expect(preservePlanningOrder(previous, { ...incoming, destination_name: '新终点' }).route_candidates).toBe(incoming.route_candidates)
})
