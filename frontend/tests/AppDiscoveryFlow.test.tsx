import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import App from '../src/App'
import { getConversation, getConversations, getHealth, getItineraries, streamChat } from '../src/api'
import type { ConversationDetail, PlanningResultCard } from '../src/api'
import { getMultiDraft, listMultiStop, planMultiStop, saveMultiStop, searchMultiPlaces } from '../src/multiStopApi'
import type { MultiDraft, MultiRequest } from '../src/multiStopApi'

function stubDiscoveryFetch(fetcher: (url: string, options?: RequestInit) => unknown) {
  vi.stubGlobal('fetch', (url: string, options?: RequestInit) =>
    url.endsWith('/api/departure-watches')
      ? Promise.resolve({ ok: true, status: 200, json: async () => [] })
      : fetcher(url, options))
}

vi.mock('../src/api', async (original) => ({ ...await original<typeof import('../src/api')>(), getConversation: vi.fn(), getHealth: vi.fn(), getConversations: vi.fn(), getItineraries: vi.fn(), streamChat: vi.fn() }))
vi.mock('../src/multiStopApi', async (original) => ({ ...await original<typeof import('../src/multiStopApi')>(), searchMultiPlaces: vi.fn(), planMultiStop: vi.fn(), getMultiDraft: vi.fn(), listMultiStop: vi.fn(), saveMultiStop: vi.fn() }))
vi.mock('../src/MultiStopMap', () => ({ MultiStopMap: () => <div>完整两段地图</div> }))
vi.mock('../src/AttractionInfo', () => ({ AttractionInfo: () => null }))
const origin = { display_name: '深圳出发点', city_code: '440300', provider_place_id: 'origin-poi', latitude: 22.5, longitude: 114 }
const request: MultiRequest = { city_code: '440300', departure_time: null, max_elapsed_minutes: 240, preferences: { priority: 'balanced', can_drive: false }, stops: ['出发点', '试点馆区', '出发点'].map((name, index) => ({ stop_id: String(index), name, city_code: '440300', point: { latitude: 22.5, longitude: 114 }, stay_minutes: index === 1 ? 120 : 0 })) }
const result = { candidates: [{ card: { attraction_id: 'venue', name: '试点馆区', summary: '测试介绍' }, request, totals: { elapsed_s: 8400, walking_distance_m: 200, transfer_count: 0 }, estimated_transport_and_ticket_yuan: 4, reasons: ['往返预计140分钟'], warnings: ['开放安排待核实'] }], excluded: [], limitations: ['仅比较现有三个馆区'] }
const at = '2026-10-25T09:00:00+08:00'
const draft: MultiDraft = { draft_id: 'chosen-draft', created_at: at, result: { request, selection: { status: 'feasible', recommended_plan_id: 'round-trip', issue_examples: [], limitations: [], plans: [{ plan_id: 'round-trip', reasons: [], evaluation: { timing_mode: 'relative', status: 'feasible', limitations: [], totals: { elapsed_s: 8400, travel_s: 1200, stay_s: 7200, waiting_s: 0, walking_distance_m: 200, transfer_count: 0, total_cost_yuan: 4, known_cost_subtotal_yuan: 4 }, stops: request.stops.map((stop) => ({ ...stop, arrival_at: null, physical_arrival_at: null, departure_at: null, waiting_s: 0, stay_s: stop.stay_minutes * 60 })), legs: [0, 1].map((index) => ({ leg_index: index, arrival_at: null, departure_at: null, leg: { origin_stop_id: String(index), destination_stop_id: String(index + 1), candidate_id: `leg-${index}`, fetched_at: at, option: { mode: 'transit', duration_s: 600, walking_distance_m: 100, transfer_count: 0, cost_yuan: 2, attribution: '测试', geometry: [] } } })) } }] } } }

function savedDiscovery(thread: string, title: string): ConversationDetail {
  return {
    conversation: { thread_id: thread, title, status: 'ready', created_at: at, updated_at: at },
    messages: [{ message_id: 'user-saved', thread_id: thread, role: 'user', content: '服务端已确认推荐条件', cards: [], created_at: at },
      { message_id: 'assistant-saved', thread_id: thread, role: 'assistant', content: '服务端保存的推荐说明', cards: [], created_at: at }],
    discovery: { request: { origin, departure_at: null, available_minutes: 240, stay_minutes: 120, budget_yuan: 100, require_confirmed_open: false, weather_policy: 'none', preferences: { priority: 'balanced', can_drive: false } }, result, revision: 2, anchor_message_id: 'assistant-saved', pending_changes: { available_minutes: 120 }, notice: '总时长待确认，仍是上一次结果。' },
  }
}

test('无本地缓存也能从后端历史恢复推荐及澄清，切换会话不混用条件，较新路线优先', async () => {
  const first = savedDiscovery('thread-a', '推荐A')
  const second = savedDiscovery('thread-b', '推荐B')
  second.discovery = { ...second.discovery!, revision: 1, pending_changes: null, notice: '', request: { ...second.discovery!.request, available_minutes: 180, stay_minutes: 30, budget_yuan: 5 } }
  const newer = savedDiscovery('thread-c', '后来有路线的推荐C')
  const planning: PlanningResultCard = { type: 'planning', origin_name: '新起点', destination_name: '新终点', recommended_mode: 'walking', ranked_options: [], preferences: { priority: 'balanced' }, reused_previous_data: false, route_refreshed: false, used_stale_snapshot: false, refresh_failed: false }
  newer.messages.push({ message_id: 'new-route', thread_id: 'thread-c', role: 'assistant', content: '已规划新路线', cards: [planning], created_at: at })
  vi.mocked(getConversations).mockResolvedValue([first.conversation, second.conversation, newer.conversation])
  vi.mocked(getConversation).mockImplementation(async (thread) => thread === 'thread-a' ? first : thread === 'thread-b' ? second : newer)
  const fetcher = vi.fn().mockImplementation(async (_url, options) => {
    const body = JSON.parse(options.body)
    expect(body.thread_id).toBe('thread-a')
    expect(body.expected_revision).toBe(2)
    expect(body.pending_changes).toEqual({ available_minutes: 120 })
    return { ok: true, json: async () => ({ status: 'updated', request: { ...first.discovery!.request, available_minutes: 120, stay_minutes: 60 }, pending_changes: null, result, answer: '重新核算完成', history: { thread_id: 'thread-a', revision: 3, assistant_content: '从服务端恢复后继续澄清完成' } }) }
  })
  stubDiscoveryFetch(fetcher)
  render(<App />)
  fireEvent.click(screen.getByRole('button', { name: /历史会话/ }))
  fireEvent.click(await screen.findByRole('button', { name: /^推荐A/ }))
  await screen.findByText('服务端保存的推荐说明')
  expect(screen.getByRole('button', { name: '规划去这里：试点馆区' })).toBeDisabled()
  await send('停留改成60分钟')
  await screen.findByText('从服务端恢复后继续澄清完成')
  expect(screen.getByRole('button', { name: '规划去这里：试点馆区' })).toBeEnabled()
  fireEvent.click(screen.getByRole('button', { name: /^推荐B/ }))
  await waitFor(() => expect(JSON.parse(sessionStorage.getItem('smart-travel-agent-session')!).threadId).toBe('thread-b'))
  fireEvent.click(screen.getByRole('button', { name: '帮我选目的地', exact: true }))
  const modal = screen.getByRole('dialog', { name: '帮我选目的地' })
  expect(within(modal).getByLabelText('往返总可用分钟')).toHaveValue(180)
  expect(within(modal).getByLabelText('确认游览停留分钟')).toHaveValue(30)
  expect(within(modal).getByLabelText('交通与普通门票预算（元，可选）')).toHaveValue(5)
  fireEvent.click(screen.getByRole('button', { name: '关闭帮我选目的地' }))
  fireEvent.click(screen.getByRole('button', { name: /^后来有路线的推荐C/ }))
  await waitFor(() => expect(JSON.parse(sessionStorage.getItem('smart-travel-agent-session')!).threadId).toBe('thread-c'))
  expect(screen.queryByRole('button', { name: '规划去这里：试点馆区' })).not.toBeInTheDocument()
  expect(JSON.parse(sessionStorage.getItem('smart-travel-agent-session')!).currentJourney.card.destination_name).toBe('新终点')
})

test('历史恢复服务端手选方案优先于草案默认推荐，改选失败可重试同步，再打开仍一致', async () => {
  const history = savedDiscovery('thread-selected', '已选行程')
  history.messages[1].cards = [{ type: 'multi_stop', draft_id: 'old-draft', stop_names: ['出发点', '试点馆区', '出发点'], status: 'feasible' }]
  history.discovery = { ...history.discovery!, stage: 'journey', pending_changes: null, notice: '', draft_id: draft.draft_id, plan_id: 'other-plan', selected_attraction_id: 'venue', revision: 4 }
  const expanded = { ...draft, result: { ...draft.result, selection: { ...draft.result.selection, plans: [...draft.result.selection.plans, { ...draft.result.selection.plans[0], plan_id: 'other-plan' }] } } }
  vi.mocked(getMultiDraft).mockResolvedValue(expanded)
  vi.mocked(getConversations).mockResolvedValue([history.conversation])
  vi.mocked(getConversation).mockImplementation(async () => history)
  const fetcher = vi.fn().mockRejectedValueOnce(new Error('同步失败')).mockImplementation(async (url, options) => {
    expect(url).toContain('/discovery/selection')
    const body = JSON.parse(options.body)
    expect(body.expected_revision).toBe(4)
    expect(body.plan_id).toBe('round-trip')
    history.discovery!.plan_id = 'round-trip'; history.discovery!.revision = 5
    return { ok: true, json: async () => ({ thread_id: 'thread-selected', revision: 5 }) }
  })
  stubDiscoveryFetch(fetcher)
  let view = render(<App />)
  fireEvent.click(screen.getByRole('button', { name: /历史会话/ }))
  fireEvent.click(await screen.findByRole('button', { name: /^已选行程/ }))
  await screen.findByText('完整两段地图')
  expect(getMultiDraft).toHaveBeenCalledWith('chosen-draft')
  expect(screen.getByRole('button', { name: /方案 2/ })).toHaveAttribute('aria-pressed', 'true')
  fireEvent.click(screen.getByRole('button', { name: /方案 1/ }))
  await screen.findByRole('button', { name: '重试同步当前选择' })
  fireEvent.click(screen.getByRole('button', { name: '重试同步当前选择' }))
  await waitFor(() => expect(JSON.parse(sessionStorage.getItem('smart-travel-agent-session')!).discoveryConversation.expected_revision).toBe(5))
  view.unmount(); sessionStorage.clear()
  view = render(<App />)
  fireEvent.click(screen.getByRole('button', { name: /历史会话/ }))
  fireEvent.click(await screen.findByRole('button', { name: /^已选行程/ }))
  await screen.findByText('完整两段地图')
  expect(screen.getByRole('button', { name: /方案 1/ })).toHaveAttribute('aria-pressed', 'true')
  view.unmount()
})

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(getHealth).mockResolvedValue({ status: 'ok', tools: [] })
  vi.mocked(getConversations).mockResolvedValue([])
  vi.mocked(getItineraries).mockResolvedValue([])
  vi.mocked(searchMultiPlaces).mockResolvedValue({ provider: 'amap', places: [origin], attribution: '高德' })
  vi.mocked(listMultiStop).mockResolvedValue([])
  vi.mocked(planMultiStop).mockResolvedValue(draft)
  vi.mocked(getMultiDraft).mockResolvedValue(draft)
})
afterEach(() => vi.unstubAllGlobals())

async function submit() {
  fireEvent.click(screen.getByRole('button', { name: '帮我选目的地', exact: true }))
  const modal = screen.getByRole('dialog', { name: '帮我选目的地' })
  fireEvent.change(within(modal).getByLabelText('深圳出发地点'), { target: { value: '出发点' } })
  fireEvent.click(within(modal).getByRole('button', { name: '搜索出发地点' }))
  fireEvent.click(await within(modal).findByRole('button', { name: origin.display_name }))
  fireEvent.click(within(modal).getByRole('button', { name: '查找可考虑的景点' }))
}

async function send(text: string) {
  fireEvent.change(screen.getByLabelText('输入你的出行问题'), { target: { value: text } })
  await waitFor(() => expect(screen.getByRole('button', { name: '发送问题' })).toBeEnabled())
  fireEvent.click(screen.getByRole('button', { name: '发送问题' }))
}

test('连续修改预算、澄清时长、刷新恢复待确认条件，换目的地后同步右侧和弹窗', async () => {
  let calls = 0
  const fetcher = vi.fn().mockImplementation(async (url: string, options) => {
    const body = JSON.parse(options.body)
    if (url.endsWith('/recommend')) return { ok: true, json: async () => result }
    calls++
    const baseline = body.request
    if (calls === 1) {
      expect(body.message).toBe('预算改成50元')
      return { ok: true, json: async () => ({ status: 'updated', request: { ...baseline, budget_yuan: 50 }, pending_changes: null, result, answer: '预算已更新为50元' }) }
    }
    if (calls === 2) {
      expect(baseline.budget_yuan).toBe(50)
      expect(body.message).toBe('只有两小时')
      return { ok: true, json: async () => ({ status: 'clarification', request: baseline, pending_changes: { available_minutes: 120 }, result: null, answer: '两小时与停留120分钟冲突，请补充停留。' }) }
    }
    if (calls === 3) {
      expect(body.pending_changes).toEqual({ available_minutes: 120 })
      expect(body.message).toBe('停留改成60分钟')
      expect(baseline.budget_yuan).toBe(50)
      expect(baseline.origin).toEqual(origin)
      expect(baseline.preferences.can_drive).toBe(false)
      expect(baseline.departure_at).toBeNull()
      return { ok: true, json: async () => ({ status: 'updated', request: { ...baseline, available_minutes: 120, stay_minutes: 60 }, pending_changes: null, result, answer: '已按120分钟和60分钟重新核算' }) }
    }
    expect(body.message).toBe('换一个目的地')
    expect(body.candidate_ids).toEqual(['venue'])
    expect(baseline.available_minutes).toBe(120)
    return { ok: true, json: async () => ({ status: 'updated', request: { ...baseline, excluded_attraction_ids: ['venue'] }, pending_changes: null, result: { candidates: [], excluded: [{ attraction_id: 'venue', name: '试点馆区', reason: '用户已排除' }], limitations: [] }, answer: '当前没有符合条件的其他馆区，不自动放宽限制。' }) }
  })
  stubDiscoveryFetch(fetcher)
  let view = render(<App />)
  await submit()
  await screen.findByRole('button', { name: '规划去这里：试点馆区' })
  await send('预算改成50元')
  await screen.findByText(/预算已更新为50元/)
  await send('只有两小时')
  await screen.findByText(/两小时与停留120分钟冲突/)
  expect(screen.getByRole('button', { name: '规划去这里：试点馆区' })).toBeDisabled()
  view.unmount()
  view = render(<App />)
  expect(screen.getByRole('button', { name: '规划去这里：试点馆区' })).toBeDisabled()
  await send('停留改成60分钟')
  await screen.findByText(/已按120分钟和60分钟重新核算/)
  expect(screen.getByRole('button', { name: '规划去这里：试点馆区' })).toBeEnabled()
  fireEvent.click(screen.getByRole('button', { name: '帮我选目的地', exact: true }))
  const modal = screen.getByRole('dialog', { name: '帮我选目的地' })
  expect(within(modal).getByLabelText('交通与普通门票预算（元，可选）')).toHaveValue(50)
  expect(within(modal).getByLabelText('往返总可用分钟')).toHaveValue(120)
  expect(within(modal).getByLabelText('确认游览停留分钟')).toHaveValue(60)
  fireEvent.click(within(modal).getByRole('button', { name: '关闭帮我选目的地' }))
  await send('换一个目的地')
  await screen.findByText(/当前没有符合条件的其他馆区/)
  expect(screen.queryByRole('button', { name: '规划去这里：试点馆区' })).not.toBeInTheDocument()
  expect(streamChat).not.toHaveBeenCalled()
  expect(planMultiStop).not.toHaveBeenCalled()
  expect(saveMultiStop).not.toHaveBeenCalled()
})

test('重新推荐失败不覆盖旧条件和结果，取消待修改可重新采用旧结果', async () => {
  let failing = true
  const fetcher = vi.fn().mockImplementation(async (url: string, options) => {
    if (url.endsWith('/recommend')) return { ok: true, json: async () => result }
    if (failing) throw new Error('测试网络错误')
    const body = JSON.parse(options.body)
    expect(body.message).toBe('取消修改')
    expect(body.request.budget_yuan).toBeNull()
    return { ok: true, json: async () => ({ status: 'cancelled', request: body.request, pending_changes: null, result: null, answer: '已取消修改，保留原条件。' }) }
  })
  stubDiscoveryFetch(fetcher)
  render(<App />)
  await submit()
  await screen.findByRole('button', { name: '规划去这里：试点馆区' })
  await send('预算改成50元')
  await screen.findByText(/本次修改未应用：测试网络错误/)
  expect(screen.getByRole('button', { name: '规划去这里：试点馆区' })).toBeDisabled()
  failing = false
  await send('取消修改')
  await screen.findByText('已取消修改，保留原条件。')
  expect(screen.getByRole('button', { name: '规划去这里：试点馆区' })).toBeEnabled()
  expect(planMultiStop).not.toHaveBeenCalled()
})

test.each(['button', 'chat'])('弹窗提交回到对话，右侧展示候选，%s 选定后生成完整往返并带上下文继续聊天', async (choice) => {
  let complete!: (value: unknown) => void
  const fetcher = vi.fn().mockReturnValue(new Promise((resolve) => { complete = resolve }))
  stubDiscoveryFetch(fetcher)
  let view = render(<App />)
  expect(screen.queryByRole('button', { name: '单段出行' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '多站点行程', exact: true })).not.toBeInTheDocument()
  await submit()
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(screen.getByLabelText('输入你的出行问题')).toBeDisabled()
  expect(JSON.parse(fetcher.mock.calls[0][1].body).departure_at).toBeNull()
  await act(async () => complete({ ok: true, json: async () => result }))
  const right = document.querySelector('.journey-workspace') as HTMLElement
  expect(within(right).getByRole('heading', { name: '试点馆区' })).toBeInTheDocument()
  expect(within(right).getByText(/预计往返总耗时：140 分钟/)).toBeInTheDocument()
  expect(document.querySelector('.message-list .result-card')).toBeNull()
  expect(planMultiStop).not.toHaveBeenCalled()
  expect(saveMultiStop).not.toHaveBeenCalled()
  view.unmount()
  view = render(<App />)
  expect(screen.getByRole('button', { name: '规划去这里：试点馆区' })).toBeInTheDocument()
  if (choice === 'button') fireEvent.click(screen.getByRole('button', { name: '规划去这里：试点馆区' }))
  else {
    fireEvent.change(screen.getByLabelText('输入你的出行问题'), { target: { value: '去试点馆区' } })
    await waitFor(() => expect(screen.getByRole('button', { name: '发送问题' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: '发送问题' }))
  }
  await screen.findByText('完整两段地图')
  expect(planMultiStop).toHaveBeenCalledWith(request)
  expect(screen.queryByRole('button', { name: '规划去这里：试点馆区' })).not.toBeInTheDocument()
  expect(screen.getByRole('heading', { name: '第 2 段：试点馆区 → 出发点' })).toBeInTheDocument()
  await waitFor(() => expect(screen.getByLabelText('输入你的出行问题')).toBeEnabled())
  vi.mocked(streamChat).mockImplementation(async (_message, thread, emit) => { emit({ type: 'final', thread_id: thread!, answer: '继续调整完整行程' }) })
  fireEvent.change(screen.getByLabelText('输入你的出行问题'), { target: { value: '改为少步行' } })
  fireEvent.click(screen.getByRole('button', { name: '发送问题' }))
  await waitFor(() => expect(streamChat).toHaveBeenCalled())
  expect(vi.mocked(streamChat).mock.calls[0].slice(3)).toEqual(['chosen-draft', 'round-trip'])
  view.unmount()
})

test('生成路线失败保留候选供重试，不自动保存；弹窗关闭不发起推荐', async () => {
  const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => result })
  stubDiscoveryFetch(fetcher)
  render(<App />)
  fireEvent.click(screen.getByRole('button', { name: '帮我选目的地', exact: true }))
  fireEvent.click(screen.getByRole('button', { name: '关闭帮我选目的地' }))
  expect(fetcher).not.toHaveBeenCalled()
  expect(screen.getByLabelText('输入你的出行问题')).toBeInTheDocument()
  await submit()
  const button = await screen.findByRole('button', { name: '规划去这里：试点馆区' })
  vi.mocked(planMultiStop).mockRejectedValueOnce(new Error('测试路线超时'))
  fireEvent.click(button)
  await screen.findByText(/测试路线超时/)
  expect(button).toBeEnabled()
  expect(saveMultiStop).not.toHaveBeenCalled()
  fireEvent.click(button)
  await screen.findByText('完整两段地图')
})
