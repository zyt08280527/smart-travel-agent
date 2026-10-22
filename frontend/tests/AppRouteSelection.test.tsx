import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, test, vi } from 'vitest'
import App from '../src/App'
import { getHealth, getConversation, getConversations, getItineraries, getJourneyVersions, streamChat } from '../src/api'
import type { PlanningResultCard, MapPoint } from '../src/api'

vi.mock('../src/api', async (importOriginal) => ({
  ...await importOriginal<typeof import('../src/api')>(),
  getHealth: vi.fn(), getConversation: vi.fn(), getConversations: vi.fn(), getItineraries: vi.fn(), streamChat: vi.fn(),
  getJourneyVersions: vi.fn(),
}))
vi.mock('../src/RouteMap', () => ({
  RouteMap: ({ geometry, mode }: { geometry: MapPoint[]; mode: string }) => (
    <div data-testid="route-map" data-mode={mode} data-path={JSON.stringify(geometry)} />
  ),
}))

test('点击具体候选后原卡片的地图与保存摘要一起更新，不追加第二张地图', async () => {
  vi.mocked(getHealth).mockResolvedValue({ status: 'ok', tools: [] })
  vi.mocked(getConversations).mockResolvedValue([])
  vi.mocked(getItineraries).mockResolvedValue([])
  const candidates: PlanningResultCard['route_candidates'] = [
    { plan_id: 'driving:0', mode: 'driving', title: '驾车路线', selected: false, recommended: true, duration_s: 1000,
      geometry: [{ longitude: 114, latitude: 22 }, { longitude: 114.1, latitude: 22.1 }] },
    { plan_id: 'transit:0', mode: 'transit', title: '地铁专线', selected: false, recommended: false, duration_s: 2000, transit_candidate_index: 0,
      geometry: [{ longitude: 114.2, latitude: 22 }, { longitude: 114.3, latitude: 22.1 }] },
    { plan_id: 'walking:0', mode: 'walking', title: '步行路线', selected: false, recommended: false, duration_s: 3000,
      geometry: [{ longitude: 114.4, latitude: 22 }, { longitude: 114.5, latitude: 22.1 }] },
  ]
  vi.mocked(streamChat).mockImplementation(async (message, _thread, emit) => {
    const selected = message === '选择公共交通候选1' ? candidates[1]
      : message === '选择步行方案' ? candidates[2] : candidates[0]
    emit({ type: 'run_started', thread_id: 'selection-test' })
    emit({ type: 'result_card', thread_id: 'selection-test', card: {
      type: 'planning', origin_name: '起点', destination_name: '终点', recommended_mode: 'driving',
      selected_mode: selected.mode, selected_plan_id: selected.plan_id, recommended_plan_id: 'driving:0',
      preferences: { priority: 'balanced' }, reused_previous_data: true, route_refreshed: false,
      used_stale_snapshot: false, refresh_failed: false, ranked_options: [], route_candidates: candidates,
    } })
    emit({ type: 'final', thread_id: 'selection-test', answer: '方案已更新' })
  })
  const user = userEvent.setup()
  const view = render(<App />)
  await user.type(screen.getByLabelText('输入你的出行问题'), '规划路线')
  await user.click(screen.getByRole('button', { name: '发送问题' }))
  await waitFor(() => expect(screen.getByTestId('route-map')).toHaveAttribute('data-mode', 'driving'))
  const originalMap = screen.getByTestId('route-map')
  expect(document.querySelector('.journey-workspace')).toContainElement(originalMap)
  expect(document.querySelector('.message-list')).not.toContainElement(originalMap)
  await user.click(screen.getByRole('button', { name: '公共交通', exact: true }))
  await waitFor(() => expect(originalMap).toHaveAttribute('data-mode', 'transit'))
  expect(originalMap).toHaveAttribute('data-path', JSON.stringify(candidates[1].geometry))
  expect(screen.getAllByTestId('route-map')).toHaveLength(1)
  expect(screen.getByText('当前方案：地铁专线')).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: '地铁专线' })).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '步行', exact: true }))
  await waitFor(() => expect(originalMap).toHaveAttribute('data-mode', 'walking'))
  expect(originalMap).toHaveAttribute('data-path', JSON.stringify(candidates[2].geometry))
  expect(screen.getAllByTestId('route-map')).toHaveLength(1)
  await user.click(screen.getByRole('button', { name: '全部方案' }))
  await user.click(screen.getByRole('button', { name: '选择行程方案 地铁专线' }))
  await waitFor(() => expect(originalMap).toHaveAttribute('data-mode', 'transit'))
  const cached = JSON.parse(sessionStorage.getItem('smart-travel-agent-session')!)
  expect(cached.currentJourney.card.selected_plan_id).toBe('transit:0')
  // Updating the live journey must not rewrite the original response snapshot.
  const originalResponse = cached.messages.find((message: { cards?: unknown[] }) => message.cards?.length)
  expect(originalResponse.cards[0].selected_plan_id).toBe('driving:0')
  view.unmount()
  render(<App />)
  expect(screen.getByTestId('route-map')).toHaveAttribute('data-mode', 'transit')
  vi.mocked(streamChat).mockImplementationOnce(async (_message, _thread, emit) => {
    emit({ type: 'final', thread_id: 'selection-test', answer: '请确认保存当前路线' })
  })
  await user.click(screen.getByRole('button', { name: '保存以上行程' }))
  expect(vi.mocked(streamChat).mock.calls.at(-1)?.slice(0, 2))
    .toEqual(['保存当前选择的行程', 'selection-test'])
  expect(screen.getByTestId('route-map')).toHaveAttribute('data-mode', 'transit')
  vi.mocked(streamChat).mockRejectedValueOnce(new Error('切换失败'))
  await user.click(screen.getByRole('button', { name: '步行', exact: true }))
  expect(await screen.findByText('请求失败：切换失败')).toBeInTheDocument()
  expect(screen.getByTestId('route-map')).toHaveAttribute('data-mode', 'transit')
  vi.mocked(getJourneyVersions).mockResolvedValue([{
    version_id: 'trusted-old-version', stale: true, card: originalResponse.cards[0],
  }])
  const callsBeforePreview = vi.mocked(streamChat).mock.calls.length
  await user.click(screen.getByRole('button', { name: '方案版本', exact: true }))
  await user.click(await screen.findByRole('button', { name: '查看方案 1（已过期或时效未知）' }))
  expect(vi.mocked(streamChat)).toHaveBeenCalledTimes(callsBeforePreview)
  expect(JSON.parse(sessionStorage.getItem('smart-travel-agent-session')!).currentJourney.card.selected_plan_id)
    .toBe('transit:0')
  await user.click(screen.getByRole('button', { name: '采用此方案' }))
  expect(vi.mocked(streamChat).mock.calls.at(-1)?.slice(0, 2))
    .toEqual(['采用历史方案 trusted-old-version', 'selection-test'])
  await waitFor(() => expect(screen.getByTestId('route-map')).toHaveAttribute('data-mode', 'driving'))
  expect(screen.queryByLabelText('历史方案只读预览')).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '新建对话' }))
  expect(screen.queryByTestId('route-map')).not.toBeInTheDocument()
  expect(JSON.parse(sessionStorage.getItem('smart-travel-agent-session')!).currentJourney).toBeNull()
})

test('从后端重新打开历史会话时恢复其最后的当前路线', async () => {
  const conversation = { thread_id: 'stored-trip', title: '历史出行规划', status: 'ready' as const,
    created_at: '2026-10-22T01:00:00Z', updated_at: '2026-10-22T02:00:00Z' }
  const card: PlanningResultCard = {
    type: 'planning', origin_name: '起点', destination_name: '终点', recommended_mode: 'transit',
    selected_mode: 'transit', preferences: { priority: 'balanced' }, reused_previous_data: true,
    route_refreshed: false, used_stale_snapshot: false, refresh_failed: false,
    ranked_options: [{ mode: 'transit', total_score: 80, duration_s: 1000 }],
  }
  vi.mocked(getHealth).mockResolvedValue({ status: 'ok', tools: [] })
  vi.mocked(getItineraries).mockResolvedValue([])
  vi.mocked(getConversations).mockResolvedValue([conversation])
  vi.mocked(getConversation).mockResolvedValue({ conversation, messages: [{
    message_id: 'stored-plan', thread_id: 'stored-trip', role: 'assistant', content: '历史路线',
    cards: [card], created_at: '2026-10-22T02:00:00Z',
  }] })
  const user = userEvent.setup()
  render(<App />)
  await user.click(await screen.findByRole('button', { name: /历史会话.*展开/ }))
  await user.click((await screen.findByText('历史出行规划')).closest('button')!)
  expect(await screen.findByText('当前方案：公共交通路线')).toBeInTheDocument()
  const cached = JSON.parse(sessionStorage.getItem('smart-travel-agent-session')!)
  expect(cached.currentJourney.threadId).toBe('stored-trip')
  expect(cached.currentJourney.card.selected_mode).toBe('transit')
})

test('手机端在对话与行程之间切换保留选择，地图仅在行程面板挂载', async () => {
  vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }))
  const card: PlanningResultCard = {
    type: 'planning', origin_name: '起点', destination_name: '终点', recommended_mode: 'transit',
    selected_mode: 'transit', preferences: { priority: 'balanced' }, reused_previous_data: true,
    route_refreshed: false, used_stale_snapshot: false, refresh_failed: false,
    ranked_options: [{ mode: 'transit', total_score: 80, duration_s: 1000,
      geometry: [{ longitude: 114, latitude: 22 }, { longitude: 114.1, latitude: 22.1 }] }],
  }
  sessionStorage.setItem('smart-travel-agent-session', JSON.stringify({
    threadId: 'mobile-trip', pendingActions: [], currentJourney: { threadId: 'mobile-trip', card },
    messages: [{ id: 'mobile-message', role: 'assistant', content: '路线原始回答', cards: [card] }],
  }))
  vi.mocked(getHealth).mockResolvedValue({ status: 'ok', tools: [] })
  vi.mocked(getConversations).mockResolvedValue([])
  vi.mocked(getItineraries).mockResolvedValue([])
  const user = userEvent.setup()
  const view = render(<App />)
  try {
    expect(screen.queryByTestId('route-map')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '当前行程', exact: true, hidden: true }))
    expect(screen.getByTestId('route-map')).toHaveAttribute('data-mode', 'transit')
    expect(document.querySelector('.workspace-layout')).toHaveAttribute('data-active-panel', 'journey')
    await user.click(screen.getByRole('button', { name: '对话', exact: true, hidden: true }))
    expect(screen.queryByTestId('route-map')).not.toBeInTheDocument()
    expect(JSON.parse(sessionStorage.getItem('smart-travel-agent-session')!).currentJourney.card.selected_mode).toBe('transit')
  } finally { view.unmount(); vi.unstubAllGlobals() }
})
