import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, test, vi } from 'vitest'
import { MultiStopWorkspace } from '../src/MultiStopWorkspace'
import { getMultiDraft, listMultiStop, planMultiStop, saveMultiStop, searchMultiPlaces, selectMultiStop } from '../src/multiStopApi'
import type { MultiDraft, MultiPlan } from '../src/multiStopApi'

vi.mock('../src/multiStopApi', () => ({ getMultiDraft: vi.fn(), listMultiStop: vi.fn(), planMultiStop: vi.fn(), saveMultiStop: vi.fn(), searchMultiPlaces: vi.fn(), selectMultiStop: vi.fn() }))
vi.mock('../src/MultiStopMap', () => ({ MultiStopMap: ({ plan }: { plan: MultiPlan }) => <div data-testid="multi-map" data-plan={plan.plan_id} /> }))
beforeEach(() => vi.clearAllMocks())

test('不填时间也能提交、选择和保存完整方案，不显示具体到离站或到达天气', async () => {
  const draft = fixture()
  draft.result.request.departure_time = null
  for (const plan of draft.result.selection.plans) {
    plan.evaluation.timing_mode = 'relative'
    for (const stop of plan.evaluation.stops) {
      stop.physical_arrival_at = stop.arrival_at = stop.departure_at = null
    }
  }
  vi.mocked(listMultiStop).mockResolvedValue([])
  vi.mocked(planMultiStop).mockReset().mockResolvedValue(draft)
  vi.mocked(saveMultiStop).mockResolvedValue({ itinerary_id: 'relative-save', draft, selected_plan_id: 'chosen', saved_at: draft.created_at })
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
  const user = userEvent.setup()
  render(<MultiStopWorkspace initialRequest={draft.result.request} />)
  expect(screen.getByLabelText('出发时间')).not.toBeRequired()
  await user.click(screen.getByRole('button', { name: '规划完整行程' }))
  await screen.findByText('当前选择的行程顺序与停留')
  expect(vi.mocked(planMultiStop).mock.calls[0][0].departure_time).toBeNull()
  expect(screen.queryByText(/到达 10\//)).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /查询.*天气/ })).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: /方案 2/ }))
  await user.click(screen.getByRole('button', { name: '确认保存当前完整行程' }))
  expect(confirm).toHaveBeenCalledWith(expect.stringContaining('预计总耗时：100 分钟'))
  expect(saveMultiStop).toHaveBeenCalledWith('draft-1', 'chosen')
})

function fixture(): MultiDraft {
  const stops = ['起点', '办事点', '终点'].map((name, index) => ({ stop_id: String(index), name, city_code: '440300', point: { latitude: 22.5, longitude: 114 }, stay_minutes: index === 1 ? 40 : 0 }))
  const plan = (id: string): MultiPlan => ({ plan_id: id, reasons: [], evaluation: {
    status: 'feasible', stops: stops.map((stop) => ({ ...stop, physical_arrival_at: '2026-10-24T10:00:00+08:00', arrival_at: '2026-10-24T10:00:00+08:00', departure_at: '2026-10-24T10:40:00+08:00', waiting_s: 0, stay_s: stop.stay_minutes * 60 })), legs: [], limitations: [],
    totals: { elapsed_s: 6000, travel_s: 3600, waiting_s: 0, stay_s: 2400, walking_distance_m: 500, transfer_count: 1, total_cost_yuan: null, known_cost_subtotal_yuan: 5 },
  } })
  return { draft_id: 'draft-1', created_at: '2026-10-22T10:00:00+08:00', result: { request: { city_code: '440300', city_name: '深圳', stops, departure_time: { departure_at: '2026-10-24T09:00:00+08:00', timezone: 'Asia/Shanghai', precision: 'exact', source_text: '9点' }, preferences: { priority: 'balanced' } }, selection: { status: 'feasible', recommended_plan_id: 'recommended', plans: [plan('recommended'), plan('chosen')], issue_examples: [], limitations: ['查询时快照'] } } }
}

test('去哪玩请求保留往返地点、停留、截止与交通预算，独立缓存不覆盖原工作区', async () => {
  vi.mocked(listMultiStop).mockResolvedValue([])
  const draft = fixture()
  const request = { ...draft.result.request, max_transport_cost_yuan: 10 }
  sessionStorage.setItem('multi-stop-workspace', '原工作区缓存')
  const user = userEvent.setup()
  render(<MultiStopWorkspace initialRequest={request} />)
  expect(screen.getByLabelText('全程交通费用上限（元，可选）')).toHaveValue(10)
  expect(screen.getByLabelText('第 2 站停留分钟')).toHaveValue(40)
  vi.mocked(planMultiStop).mockResolvedValue({ ...draft, result: { ...draft.result, request } })
  await user.click(screen.getByRole('button', { name: '规划完整行程' }))
  await waitFor(() => expect(vi.mocked(planMultiStop).mock.calls.at(-1)?.[0].max_transport_cost_yuan).toBe(10))
  expect(sessionStorage.getItem('multi-stop-workspace')).toBe('原工作区缓存')
  expect(sessionStorage.getItem('discovery-multi-stop-workspace')).toBeTruthy()
})

test('选择非推荐完整方案后地图、保存和重开一致；修改后须重规划', async () => {
  const draft = fixture()
  vi.mocked(listMultiStop).mockResolvedValue([])
  vi.mocked(getMultiDraft).mockResolvedValue(draft)
  vi.mocked(saveMultiStop).mockResolvedValue({ itinerary_id: 'saved', draft, selected_plan_id: 'chosen', saved_at: draft.created_at })
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  const user = userEvent.setup()
  const view = render(<MultiStopWorkspace initialDraftId="draft-1" />)
  await waitFor(() => expect(screen.getByTestId('multi-map')).toHaveAttribute('data-plan', 'recommended'))
  await user.click(screen.getByRole('button', { name: /方案 2/ }))
  expect(screen.getByTestId('multi-map')).toHaveAttribute('data-plan', 'chosen')
  await user.click(screen.getByRole('button', { name: '确认保存当前完整行程' }))
  expect(saveMultiStop).toHaveBeenCalledWith('draft-1', 'chosen')
  await user.click(await screen.findByRole('button', { name: /重新打开：/ }))
  expect(screen.getByTestId('multi-map')).toHaveAttribute('data-plan', 'chosen')
  await user.clear(screen.getByLabelText('第 2 站停留分钟'))
  await user.type(screen.getByLabelText('第 2 站停留分钟'), '60')
  expect(screen.getByRole('button', { name: '确认保存当前完整行程' })).toBeDisabled()
  expect(screen.getByText(/设置已修改/)).toBeInTheDocument()
  expect(screen.getByTestId('multi-map')).toHaveAttribute('data-plan', 'chosen')
  view.unmount()
  render(<MultiStopWorkspace />)
  expect(screen.getByTestId('multi-map')).toHaveAttribute('data-plan', 'chosen')
  expect(screen.getByRole('button', { name: '确认保存当前完整行程' })).toBeDisabled()
})

test('地点逐一确认后提交；不自动选第一个搜索结果，失败保留原结果', async () => {
  vi.mocked(listMultiStop).mockResolvedValue([])
  const draft = fixture()
  vi.mocked(planMultiStop).mockResolvedValue(draft)
  vi.mocked(searchMultiPlaces).mockImplementation(async (query) => ({ places: [{ display_name: query, latitude: 22.5, longitude: 114, city_code: '440300' }], attribution: '高德地图' }))
  const user = userEvent.setup()
  render(<MultiStopWorkspace />)
  await user.type(screen.getByLabelText('搜索城市'), '深圳')
  await user.type(screen.getByLabelText('出发时间'), '2026-10-24T09:00')
  for (let index = 1; index <= 3; index++) {
    await user.type(screen.getByLabelText(`第 ${index} 站地点`), `地点${index}`)
    await user.click(screen.getByRole('button', { name: `搜索第 ${index} 站` }))
    const choice = await screen.findByRole('button', { name: `深圳 地点${index}` })
    expect(screen.queryByText(`已确认：深圳 地点${index}`)).not.toBeInTheDocument()
    await user.click(choice)
  }
  await user.click(screen.getByRole('button', { name: '规划完整行程' }))
  await waitFor(() => expect(planMultiStop).toHaveBeenCalledOnce())
  expect(vi.mocked(planMultiStop).mock.calls[0][0].stops).toHaveLength(3)
  await screen.findByTestId('multi-map')
  vi.mocked(planMultiStop).mockRejectedValueOnce(new Error('路线服务失败'))
  await user.click(screen.getByRole('button', { name: '重新规划完整行程' }))
  expect(await screen.findByText('路线服务失败')).toBeInTheDocument()
  expect(screen.getByTestId('multi-map')).toHaveAttribute('data-plan', 'recommended')
})

test('最多三个中间点，允许删除但保留至少一个', async () => {
  vi.mocked(listMultiStop).mockResolvedValue([])
  const user = userEvent.setup()
  render(<MultiStopWorkspace />)
  await user.click(screen.getByRole('button', { name: '添加中间点' }))
  await user.click(screen.getByRole('button', { name: '添加中间点' }))
  expect(screen.getByRole('button', { name: '添加中间点' })).toBeDisabled()
  await user.click(screen.getByRole('button', { name: '删除中间点 2' }))
  await user.click(screen.getByRole('button', { name: '删除中间点 1' }))
  expect(screen.queryByRole('button', { name: /删除中间点/ })).not.toBeInTheDocument()
  expect(screen.getAllByLabelText(/第 \d 站地点/)).toHaveLength(3)
})

test('分段切换等待服务端成功后更新，失败时保留原选择和地图', async () => {
  const draft = fixture()
  const option = { mode: 'transit' as const, status: 'available', geometry: [], duration_s: 600, attribution: 'fixture', cost_yuan: 2, walking_distance_m: 100, transfer_count: 0 }
  const leg = (id: string) => ({ candidate_id: id, origin_stop_id: '0', destination_stop_id: '1', fetched_at: draft.created_at, option })
  const second = { ...leg('bus'), origin_stop_id: '1', destination_stop_id: '2' }
  draft.result.segments = [{ candidates: [leg('bus'), leg('rail')] }, { candidates: [second] }]
  draft.result.selection.plans.forEach((plan) => { plan.evaluation.legs = [leg('bus'), second].map((value, index) => ({ leg_index: index, leg: value, departure_at: draft.created_at, arrival_at: draft.created_at })) })
  const changed = structuredClone(draft)
  changed.draft_id = 'changed'
  changed.result.selection.selected_plan_id = 'chosen'
  changed.result.selection.plans[1].evaluation.legs[0].leg = leg('rail')
  vi.mocked(listMultiStop).mockResolvedValue([])
  vi.mocked(getMultiDraft).mockResolvedValue(draft)
  vi.mocked(selectMultiStop).mockResolvedValueOnce(changed).mockRejectedValueOnce(new Error('该组合超出时间限制'))
  const user = userEvent.setup()
  render(<MultiStopWorkspace initialDraftId="draft-1" />)
  const select = await screen.findByLabelText('第 1 段路线选择')
  await user.selectOptions(select, 'rail')
  await waitFor(() => expect(screen.getByTestId('multi-map')).toHaveAttribute('data-plan', 'chosen'))
  expect(selectMultiStop).toHaveBeenCalledWith('draft-1', ['rail', 'bus'])
  await user.selectOptions(screen.getByLabelText('第 1 段路线选择'), 'bus')
  expect(await screen.findByText('该组合超出时间限制')).toBeInTheDocument()
  expect(screen.getByLabelText('第 1 段路线选择')).toHaveValue('rail')
  expect(screen.getByTestId('multi-map')).toHaveAttribute('data-plan', 'chosen')
})

test('打开另一份草案后，较早的规划响应不能覆盖新草案', async () => {
  const original = fixture()
  const fresh = structuredClone(original)
  fresh.draft_id = 'fresh'
  fresh.result.selection.recommended_plan_id = 'fresh-plan'
  fresh.result.selection.plans[0].plan_id = 'fresh-plan'
  vi.mocked(listMultiStop).mockResolvedValue([])
  vi.mocked(getMultiDraft).mockReset().mockResolvedValueOnce(original).mockResolvedValueOnce(fresh)
  let finish!: (draft: MultiDraft) => void
  vi.mocked(planMultiStop).mockReset().mockReturnValueOnce(new Promise((resolve) => { finish = resolve }))
  const user = userEvent.setup()
  const view = render(<MultiStopWorkspace initialDraftId="old" />)
  await screen.findByTestId('multi-map')
  await user.click(screen.getByRole('button', { name: '重新规划完整行程' }))
  await waitFor(() => expect(planMultiStop).toHaveBeenCalledOnce())
  view.rerender(<MultiStopWorkspace initialDraftId="fresh" />)
  await waitFor(() => expect(screen.getByTestId('multi-map')).toHaveAttribute('data-plan', 'fresh-plan'))
  await act(async () => { finish(original) })
  expect(screen.getByTestId('multi-map')).toHaveAttribute('data-plan', 'fresh-plan')
})

test('可行方案的其他候选失败原因单独折叠，不冒充当前方案错误', async () => {
  const draft = fixture()
  draft.result.selection.issue_examples = [{ code: 'route_unavailable', message: '其他公共交通候选不可用' }]
  vi.mocked(listMultiStop).mockResolvedValue([])
  vi.mocked(getMultiDraft).mockResolvedValue(draft)
  render(<MultiStopWorkspace initialDraftId="draft-1" />)
  await screen.findByTestId('multi-map')
  const details = screen.getByText('其他候选未采用的原因').closest('details')
  expect(details).not.toHaveAttribute('open')
  expect(details).toHaveTextContent('其他公共交通候选不可用')
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '确认保存当前完整行程' })).toBeEnabled()
})

test('同名候选须显式区分，跨城点禁止规划，城市缺失不可选', async () => {
  vi.mocked(listMultiStop).mockResolvedValue([])
  vi.mocked(planMultiStop).mockReset()
  vi.mocked(searchMultiPlaces).mockResolvedValue({ attribution: '高德地图', places: [
    { display_name: '人民公园，深圳地址', city_code: '440300', latitude: 22.5, longitude: 114 },
    { display_name: '人民公园，广州地址', city_code: '440100', latitude: 23.1, longitude: 113.3 },
    { display_name: '人民公园，城市未知', city_code: null, latitude: 22.5, longitude: 114 },
  ] })
  const user = userEvent.setup()
  render(<MultiStopWorkspace />)
  await user.type(screen.getByLabelText('搜索城市'), '深圳')
  await user.type(screen.getByLabelText('出发时间'), '2026-10-24T09:00')
  for (let index = 1; index <= 3; index++) {
    await user.type(screen.getByLabelText(`第 ${index} 站地点`), '人民公园')
    await user.click(screen.getByRole('button', { name: `搜索第 ${index} 站` }))
    expect(await screen.findByRole('button', { name: /人民公园，城市未知/ })).toBeDisabled()
    expect(screen.getByLabelText(`第 ${index} 站地点`)).toHaveValue('人民公园')
    await user.click(screen.getByRole('button', { name: index === 2 ? '人民公园，广州地址' : '人民公园，深圳地址' }))
  }
  await user.click(screen.getByRole('button', { name: '规划完整行程' }))
  expect(await screen.findByText('首版仅支持同城行程，请确认所有地点在同一城市。')).toBeInTheDocument()
  expect(planMultiStop).not.toHaveBeenCalled()
})

test('五站提交保留三个中间点和停留，不将同一POI的两次到访合并', async () => {
  vi.mocked(listMultiStop).mockResolvedValue([])
  vi.mocked(planMultiStop).mockReset().mockResolvedValue(fixture())
  vi.mocked(searchMultiPlaces).mockImplementation(async (query) => ({ attribution: '高德地图', places: [
    { display_name: query, city_code: '440300', latitude: 22.5, longitude: 114, provider_place_id: 'same-poi' },
  ] }))
  const user = userEvent.setup()
  render(<MultiStopWorkspace />)
  await user.type(screen.getByLabelText('搜索城市'), '深圳')
  await user.type(screen.getByLabelText('出发时间'), '2026-10-24T09:00')
  await user.click(screen.getByRole('button', { name: '添加中间点' }))
  await user.click(screen.getByRole('button', { name: '添加中间点' }))
  for (let index = 1; index <= 5; index++) {
    await user.type(screen.getByLabelText(`第 ${index} 站地点`), index === 5 ? '地点1' : `地点${index}`)
    await user.click(screen.getByRole('button', { name: `搜索第 ${index} 站` }))
    await user.click(await screen.findByRole('button', { name: `深圳 地点${index === 5 ? 1 : index}` }))
    if (index > 1 && index < 5) {
      await user.clear(screen.getByLabelText(`第 ${index} 站停留分钟`))
      await user.type(screen.getByLabelText(`第 ${index} 站停留分钟`), String(index * 10))
    }
  }
  await user.click(screen.getByRole('button', { name: '规划完整行程' }))
  await waitFor(() => expect(planMultiStop).toHaveBeenCalledOnce())
  const request = vi.mocked(planMultiStop).mock.calls[0][0]
  expect(request.stops.map((stop) => stop.name)).toEqual(['深圳 地点1', '深圳 地点2', '深圳 地点3', '深圳 地点4', '深圳 地点1'])
  expect(request.stops.map((stop) => stop.stay_minutes)).toEqual([0, 20, 30, 40, 0])
  expect(new Set(request.stops.map((stop) => stop.stop_id)).size).toBe(5)
})

test('同城重开草案时丢弃旧地点搜索的延迟响应', async () => {
  const draft = fixture()
  vi.mocked(listMultiStop).mockResolvedValue([])
  vi.mocked(getMultiDraft).mockReset().mockResolvedValue(draft)
  let finish!: (value: Awaited<ReturnType<typeof searchMultiPlaces>>) => void
  vi.mocked(searchMultiPlaces).mockReset().mockReturnValueOnce(new Promise((resolve) => { finish = resolve }))
  const user = userEvent.setup()
  const view = render(<MultiStopWorkspace initialDraftId="first" />)
  await screen.findByTestId('multi-map')
  await user.click(screen.getByRole('button', { name: '搜索第 1 站' }))
  expect(await screen.findByText('搜索中…')).toBeInTheDocument()
  view.rerender(<MultiStopWorkspace initialDraftId="reopened" />)
  await waitFor(() => expect(getMultiDraft).toHaveBeenCalledWith('reopened'))
  await waitFor(() => expect(screen.queryByText('搜索中…')).not.toBeInTheDocument())
  await act(async () => finish({ attribution: '高德地图', places: [
    { display_name: '旧搜索地点', city_code: '440300', latitude: 22.5, longitude: 114 },
  ] }))
  expect(screen.queryByRole('button', { name: '旧搜索地点' })).not.toBeInTheDocument()
  expect(screen.getByLabelText('第 1 站地点')).toHaveValue('起点')
})
