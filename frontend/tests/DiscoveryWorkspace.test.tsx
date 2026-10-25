import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { DiscoveryWorkspace } from '../src/DiscoveryWorkspace'
import { searchMultiPlaces } from '../src/multiStopApi'
import type { MultiRequest } from '../src/multiStopApi'

vi.mock('../src/multiStopApi', () => ({ searchMultiPlaces: vi.fn() }))
vi.mock('../src/AttractionInfo', () => ({ AttractionInfo: () => <span>参观事实卡</span> }))
const origin = { display_name: '深圳出发点', latitude: 22.5, longitude: 114, city_code: '440300', provider_place_id: 'origin-poi' }
const request: MultiRequest = {
  city_code: '440300', city_name: '深圳', max_transport_cost_yuan: 10,
  departure_time: { departure_at: '2026-10-24T09:00:00+08:00', timezone: 'Asia/Shanghai', precision: 'exact', source_text: '测试' },
  arrival_deadline: { arrival_by: '2026-10-24T13:00:00+08:00', buffer_minutes: 0, timezone: 'Asia/Shanghai', precision: 'exact', source_text: '测试' },
  preferences: { priority: 'balanced', can_drive: false },
  stops: ['origin', 'visit', 'return'].map((id, index) => ({ stop_id: id, name: index === 1 ? '试点馆区' : origin.display_name, city_code: '440300', point: { latitude: 22.5, longitude: 114 }, provider_place_id: index === 1 ? 'venue-poi' : origin.provider_place_id, stay_minutes: index === 1 ? 120 : 0 })),
}
const result = {
  candidates: [{ card: { attraction_id: 'venue', name: '试点馆区' }, request, totals: { elapsed_s: 10000 }, estimated_transport_and_ticket_yuan: 10, reasons: ['时间核算通过'], warnings: ['开放安排待核实'] }],
  excluded: [], limitations: ['仅限现有馆区'],
}
const response = (data: unknown) => ({ ok: true, json: async () => data })
afterEach(() => vi.unstubAllGlobals())

async function selectOrigin() {
  vi.mocked(searchMultiPlaces).mockReset().mockResolvedValue({ provider: 'amap', places: [origin], attribution: '高德' })
  fireEvent.change(screen.getByLabelText('深圳出发地点'), { target: { value: '出发点' } })
  fireEvent.click(screen.getByRole('button', { name: '搜索出发地点' }))
  fireEvent.click(await screen.findByRole('button', { name: origin.display_name }))
  fireEvent.change(screen.getByLabelText('出发时间（北京时间）'), { target: { value: '2026-10-24T09:00' } })
}

test('确认出发点与停留后筛选，选择候选完整带入规划，编辑条件使旧结果失效', async () => {
  const fetcher = vi.fn().mockResolvedValue(response(result))
  vi.stubGlobal('fetch', fetcher)
  const onPlan = vi.fn()
  render(<DiscoveryWorkspace onPlan={onPlan} />)
  await selectOrigin()
  fireEvent.change(screen.getByLabelText('交通与普通门票预算（元，可选）'), { target: { value: '10' } })
  fireEvent.click(screen.getByRole('button', { name: '查找可考虑的景点' }))
  const choose = await screen.findByRole('button', { name: '规划去这里：试点馆区' })
  fireEvent.click(choose)
  expect(onPlan).toHaveBeenCalledWith(request)
  const body = JSON.parse(fetcher.mock.calls[0][1].body)
  expect(body.origin.provider_place_id).toBe('origin-poi')
  expect(body.stay_minutes).toBe(120)
  expect(body.budget_yuan).toBe(10)
  expect(body.preferences.can_drive).toBe(false)
  fireEvent.change(screen.getByLabelText('确认游览停留分钟'), { target: { value: '90' } })
  expect(screen.queryByRole('button', { name: '规划去这里：试点馆区' })).not.toBeInTheDocument()
})

test('条件改变后迟到推荐不能覆盖，严格开放返回空候选与具体原因', async () => {
  let resolveOld!: (value: unknown) => void
  const fetcher = vi.fn().mockReturnValueOnce(new Promise((resolve) => { resolveOld = resolve })).mockResolvedValue(response({ candidates: [], excluded: [{ attraction_id: 'venue', name: '试点馆区', reason: '缺少指定日期开放核验' }], limitations: [] }))
  vi.stubGlobal('fetch', fetcher)
  render(<DiscoveryWorkspace onPlan={vi.fn()} />)
  await selectOrigin()
  fireEvent.click(screen.getByRole('button', { name: '查找可考虑的景点' }))
  await waitFor(() => expect(fetcher).toHaveBeenCalledOnce())
  fireEvent.click(screen.getByLabelText('仅接受已核实指定日期开放的候选'))
  await act(async () => resolveOld(response(result)))
  expect(screen.queryByRole('button', { name: '规划去这里：试点馆区' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '查找可考虑的景点' }))
  await screen.findByText(/暂无能满足当前条件/)
  fireEvent.click(screen.getByText('未入选原因'))
  expect(screen.getByText(/缺少指定日期开放核验/)).toBeInTheDocument()
})

test('未点选出发点不请求推荐', () => {
  const fetcher = vi.fn()
  vi.stubGlobal('fetch', fetcher)
  render(<DiscoveryWorkspace onPlan={vi.fn()} />)
  fireEvent.click(screen.getByRole('button', { name: '查找可考虑的景点' }))
  expect(screen.getByRole('alert')).toHaveTextContent('请点选出发地点')
  expect(fetcher).not.toHaveBeenCalled()
})

test('天气提示模式提交明确策略并展示未知，切换模式使旧报告失效', async () => {
  const data = { ...result, candidates: result.candidates.map((candidate) => ({
    ...candidate, weather: { plan_id: 'sample-plan', queried_at: '2026-10-23T12:00:00Z', limitations: ['仅覆盖到达采样点'], stops: [{
      stop_id: 'visit', name: '馆区到达点', target_at: '2026-10-24T01:30:00Z', status: 'unavailable', message: '小时预报缺失', warnings: [],
    }] },
  })) }
  const fetcher = vi.fn().mockResolvedValue(response(data))
  vi.stubGlobal('fetch', fetcher)
  render(<DiscoveryWorkspace onPlan={vi.fn()} />)
  await selectOrigin()
  fireEvent.change(screen.getByLabelText('本次探索的天气处理'), { target: { value: 'inform' } })
  fireEvent.click(screen.getByRole('button', { name: '查找可考虑的景点' }))
  fireEvent.click(await screen.findByText('本次探索的逐站天气'))
  expect(screen.getByText('天气未知：小时预报缺失')).toBeInTheDocument()
  expect(JSON.parse(fetcher.mock.calls[0][1].body).weather_policy).toBe('inform')
  fireEvent.change(screen.getByLabelText('本次探索的天气处理'), { target: { value: 'exclude_signals' } })
  expect(screen.queryByText('本次探索的逐站天气')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '规划去这里：试点馆区' })).not.toBeInTheDocument()
})

test('天气排除模式空结果说明原因，不自动放宽到提示模式', async () => {
  const fetcher = vi.fn().mockResolvedValue(response({ candidates: [], excluded: [{ attraction_id: 'venue', name: '试点馆区', reason: '天气未知，未通过筛选' }], limitations: ['采样点不代表全程'] }))
  vi.stubGlobal('fetch', fetcher)
  render(<DiscoveryWorkspace onPlan={vi.fn()} />)
  await selectOrigin()
  fireEvent.change(screen.getByLabelText('本次探索的天气处理'), { target: { value: 'exclude_signals' } })
  fireEvent.click(screen.getByRole('button', { name: '查找可考虑的景点' }))
  await screen.findByText(/暂无能满足当前条件/)
  fireEvent.click(screen.getByText('未入选原因'))
  expect(screen.getByText(/天气未知，未通过筛选/)).toBeInTheDocument()
  expect(screen.getByLabelText('本次探索的天气处理')).toHaveValue('exclude_signals')
  expect(JSON.parse(fetcher.mock.calls[0][1].body).weather_policy).toBe('exclude_signals')
})
