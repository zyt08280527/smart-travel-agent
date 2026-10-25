import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, test, vi } from 'vitest'
import App from '../src/App'
import { getHealth, getConversations, getItineraries, streamChat } from '../src/api'
import { getMultiDraft, listMultiStop } from '../src/multiStopApi'
import type { MultiDraft, MultiPlan } from '../src/multiStopApi'

vi.mock('../src/api', async (original) => ({
  ...await original<typeof import('../src/api')>(),
  getHealth: vi.fn(), getConversations: vi.fn(), getItineraries: vi.fn(), streamChat: vi.fn(),
}))
vi.mock('../src/multiStopApi', async (original) => ({
  ...await original<typeof import('../src/multiStopApi')>(), getMultiDraft: vi.fn(), listMultiStop: vi.fn(),
}))
vi.mock('../src/MultiStopMap', () => ({ MultiStopMap: ({ plan }: { plan: MultiPlan }) => <div data-testid="all-legs-map">地图共 {plan.evaluation.legs.length} 段</div> }))

test('聊天多站点结果完整显示在右侧，第二段直接可见，刷新恢复且保留聊天输入', async () => {
  vi.mocked(getHealth).mockResolvedValue({ status: 'ok', tools: [] })
  vi.mocked(getConversations).mockResolvedValue([])
  vi.mocked(getItineraries).mockResolvedValue([])
  vi.mocked(listMultiStop).mockResolvedValue([])
  const names = ['深圳大学粤海校区', '深圳人才公园', '深圳湾公园']
  const at = '2026-10-24T10:00:00+08:00'
  const stops = names.map((name, index) => ({ stop_id: String(index), name, city_code: '440300', point: { latitude: 22.5, longitude: 114 }, stay_minutes: index === 1 ? 60 : 0 }))
  const plan: MultiPlan = { plan_id: 'complete', reasons: [], evaluation: {
    status: 'feasible', limitations: [], stops: stops.map((stop) => ({ ...stop, physical_arrival_at: at, arrival_at: at, departure_at: at, waiting_s: 0, stay_s: stop.stay_minutes * 60 })),
    legs: [0, 1].map((index) => ({ leg_index: index, departure_at: at, arrival_at: at, leg: {
      origin_stop_id: String(index), destination_stop_id: String(index + 1), candidate_id: `leg-${index}`, fetched_at: at,
      option: { mode: 'transit', duration_s: 1200, cost_yuan: 2, walking_distance_m: 100, transfer_count: 0, geometry: [], attribution: '测试路线' },
      transit_details: { legs: [{ line_name: index === 0 ? '第一段公交线' : '第二段地铁线' }] },
    } })),
    totals: { elapsed_s: 6000, travel_s: 2400, stay_s: 3600, waiting_s: 0, walking_distance_m: 200, transfer_count: 0, total_cost_yuan: 4, known_cost_subtotal_yuan: 4 },
  } }
  const draft: MultiDraft = { draft_id: 'multi-chat', created_at: at, result: {
    request: { city_code: '440300', stops, departure_time: { departure_at: at, timezone: 'Asia/Shanghai', precision: 'exact', source_text: '明天十点' }, preferences: { priority: 'balanced' } },
    selection: { status: 'feasible', recommended_plan_id: 'complete', plans: [plan], limitations: [], issue_examples: [] },
  } }
  vi.mocked(getMultiDraft).mockResolvedValue(draft)
  vi.mocked(streamChat).mockImplementation(async (_message, _thread, emit) => {
    emit({ type: 'run_started', thread_id: 'multi-thread' })
    emit({ type: 'result_card', thread_id: 'multi-thread', card: { type: 'multi_stop', draft_id: draft.draft_id, stop_names: names, status: 'feasible' } })
    emit({ type: 'final', thread_id: 'multi-thread', answer: '已生成完整行程。' })
  })
  const user = userEvent.setup()
  const view = render(<App />)
  await user.type(screen.getByLabelText('输入你的出行问题'), names.join(' → '))
  await user.click(screen.getByRole('button', { name: '发送问题' }))
  await screen.findByText('第二段地铁线')
  const right = document.querySelector('.journey-workspace') as HTMLElement
  const left = document.querySelector('.message-list') as HTMLElement
  expect(within(right).getByRole('heading', { name: `第 1 段：${names[0]} → ${names[1]}` })).toBeInTheDocument()
  expect(within(right).getByRole('heading', { name: `第 2 段：${names[1]} → ${names[2]}` })).toBeInTheDocument()
  expect(within(right).getByText(/停留 60 分钟，/)).toBeInTheDocument()
  expect(within(right).getByTestId('all-legs-map')).toHaveTextContent('地图共 2 段')
  expect(left.querySelector('.multi-workspace')).toBeNull()
  expect(screen.getByLabelText('输入你的出行问题')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '保存以上行程' })).not.toBeInTheDocument()
  view.unmount()
  render(<App />)
  await screen.findByText('第二段地铁线')
  expect(screen.getByLabelText('输入你的出行问题')).toBeInTheDocument()
})
