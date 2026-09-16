import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, test, vi } from 'vitest'

import App from '../src/App'
import type { AgentStreamEvent } from '../src/api'
import {
  decideApproval,
  deleteConversation,
  getConversation,
  getConversations,
  getHealth,
  streamChat,
} from '../src/api'

vi.mock('../src/api', async (importOriginal) => {
  const original = await importOriginal<typeof import('../src/api')>()
  return {
    ...original,
    decideApproval: vi.fn(),
    deleteConversation: vi.fn(),
    getConversation: vi.fn(),
    getConversations: vi.fn(),
    getHealth: vi.fn(),
    streamChat: vi.fn(),
  }
})

const getHealthMock = vi.mocked(getHealth)
const deleteConversationMock = vi.mocked(deleteConversation)
const getConversationsMock = vi.mocked(getConversations)
const getConversationMock = vi.mocked(getConversation)
const streamChatMock = vi.mocked(streamChat)
const decideApprovalMock = vi.mocked(decideApproval)

const WEATHER_CARD = {
  type: 'weather' as const,
  city: '深圳',
  country: '中国',
  admin1: '广东',
  temperature_c: 25.1,
  apparent_temperature_c: 29.5,
  precipitation_mm: 0,
  wind_speed_kmh: 10.6,
  condition: '阴天',
  observed_at: '2026-07-28T21:45',
}

describe('App streaming chat', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    getHealthMock.mockResolvedValue({
      status: 'ok',
      tools: ['query_current_weather'],
    })
    getConversationsMock.mockResolvedValue([])
    streamChatMock.mockImplementation(async (_message, _threadId, onEvent) => {
      const events: AgentStreamEvent[] = [
        { type: 'run_started', thread_id: 'thread-weather' },
        {
          type: 'result_card',
          thread_id: 'thread-weather',
          card: WEATHER_CARD,
        },
        {
          type: 'assistant_delta',
          thread_id: 'thread-weather',
          delta: '临时模型片段',
        },
        {
          type: 'final',
          thread_id: 'thread-weather',
          answer: '确定性最终回答',
        },
        { type: 'done', thread_id: 'thread-weather' },
      ]
      for (const event of events) {
        onEvent(event)
      }
    })
  })

  test('renders the final answer and weather card from stream events', async () => {
    const user = userEvent.setup()
    render(<App />)

    await screen.findByText('后端已连接，当前加载 1 个 Agent 工具。')
    await user.type(
      screen.getByLabelText('输入你的出行问题'),
      '深圳现在天气怎么样？',
    )
    await user.click(screen.getByRole('button', { name: '发送问题' }))

    await screen.findByText('确定性最终回答')
    expect(screen.queryByText('临时模型片段')).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '深圳' })).toBeInTheDocument()
    expect(screen.getByText('25.1°C')).toBeInTheDocument()
    expect(screen.getByText('阴天')).toBeInTheDocument()
    expect(screen.getByText('当前会话 ID：thread-weather')).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.queryByRole('status')).not.toBeInTheDocument()
    })
  })

  test('renders traffic-aware driving details from a route card', async () => {
    streamChatMock.mockImplementation(async (_message, _threadId, onEvent) => {
      const events: AgentStreamEvent[] = [
        { type: 'run_started', thread_id: 'thread-driving' },
        {
          type: 'result_card',
          thread_id: 'thread-driving',
          card: {
            type: 'route',
            mode: 'driving',
            distance_m: 14869,
            duration_s: 1687,
            duration_basis: 'traffic_aware_estimate',
            tolls_yuan: 0,
            taxi_cost_yuan: 38,
            traffic_lights: 4,
            restriction: 0,
            traffic_status_counts: { 畅通: 115, 缓行: 6 },
            step_count: 20,
            attribution: '驾车路线数据来源：高德地图 Web服务 API',
          },
        },
        {
          type: 'final',
          thread_id: 'thread-driving',
          answer: '已完成驾车路线规划。',
        },
        { type: 'done', thread_id: 'thread-driving' },
      ]
      for (const event of events) {
        onEvent(event)
      }
    })

    const user = userEvent.setup()
    render(<App />)
    await screen.findByText('后端已连接，当前加载 1 个 Agent 工具。')
    await user.type(screen.getByLabelText('输入你的出行问题'), '规划驾车路线')
    await user.click(screen.getByRole('button', { name: '发送问题' }))

    expect(await screen.findByText('交通感知预计时长')).toBeInTheDocument()
    expect(screen.getByText('¥38.00')).toBeInTheDocument()
    expect(screen.getByText(/查询时路况/)).toHaveTextContent(
      '畅通 115 段 · 缓行 6 段',
    )
  })

  test('renders every direct public-transport candidate and its legs', async () => {
    streamChatMock.mockImplementation(async (_message, _threadId, onEvent) => {
      const events: AgentStreamEvent[] = [
        { type: 'run_started', thread_id: 'thread-transit' },
        {
          type: 'result_card',
          thread_id: 'thread-transit',
          card: {
            type: 'transit',
            option_count: 2,
            distance_m: 15103,
            duration_s: 2820,
            walking_distance_m: 839,
            cost_yuan: 4,
            transfer_count: 0,
            line_names: ['地铁5号线'],
            options: [
              {
                candidate_index: 0,
                selected: true,
                duration_s: 2820,
                cost_yuan: 4,
                walking_distance_m: 839,
                transfer_count: 0,
                line_names: ['地铁5号线'],
                legs: [{
                  mode: 'subway',
                  distance_m: 14000,
                  duration_s: 2200,
                  line_name: '地铁5号线',
                  departure_stop: '粤海门',
                  arrival_stop: '塘朗',
                  via_stop_count: 4,
                }],
              },
              {
                candidate_index: 1,
                selected: false,
                duration_s: 3000,
                cost_yuan: 5,
                walking_distance_m: 600,
                transfer_count: 1,
                line_names: ['地铁1号线', '地铁5号线'],
                legs: [],
              },
            ],
            attribution: '公交路线数据来源：高德地图 Web服务 API',
          },
        },
        {
          type: 'final',
          thread_id: 'thread-transit',
          answer: '已完成公共交通路线规划。',
        },
        { type: 'done', thread_id: 'thread-transit' },
      ]
      for (const event of events) {
        onEvent(event)
      }
    })

    const user = userEvent.setup()
    render(<App />)
    await screen.findByText('后端已连接，当前加载 1 个 Agent 工具。')
    await user.type(screen.getByLabelText('输入你的出行问题'), '规划地铁路线')
    await user.click(screen.getByRole('button', { name: '发送问题' }))

    expect(await screen.findByText('公共交通候选')).toBeInTheDocument()
    expect(screen.getByText('候选 2')).toBeInTheDocument()
    await user.click(screen.getByText('查看完整路线'))
    expect(screen.getByText('粤海门 → 塘朗 · 途经 4 站')).toBeInTheDocument()
    await user.click(screen.getByRole('button', {
      name: '选择公共交通候选 2',
    }))
    await waitFor(() => {
      expect(streamChatMock).toHaveBeenLastCalledWith(
        '选择公共交通候选2',
        'thread-transit',
        expect.any(Function),
      )
    })
  })

  test('renders preference changes and exclusions from a planning card', async () => {
    streamChatMock.mockImplementation(async (_message, _threadId, onEvent) => {
      const events: AgentStreamEvent[] = [
        { type: 'run_started', thread_id: 'thread-replan' },
        {
          type: 'result_card',
          thread_id: 'thread-replan',
          card: {
            type: 'planning',
            origin_name: '粤海校区',
            destination_name: '丽湖校区',
            recommended_mode: 'transit',
            previous_recommended_mode: 'driving',
            reused_previous_data: true,
            route_refreshed: false,
            used_stale_snapshot: false,
            refresh_failed: false,
            arrival_by: '2026-09-14T09:00:00+08:00',
            arrival_buffer_minutes: 15,
            preferences: {
              priority: 'cheapest',
              transit_strategy: 'subway_first',
              can_drive: false,
              max_walking_distance_m: 1000,
              max_transfer_count: 1,
            },
            ranked_options: [
              {
                mode: 'transit',
                total_score: 84,
                duration_s: 2700,
                walking_distance_m: 800,
                transfer_count: 1,
              },
              {
                mode: 'walking',
                total_score: 38,
                duration_s: 9000,
                walking_distance_m: 12500,
                transfer_count: 0,
              },
            ],
            recommendation_variants: [
              {
                priority: 'balanced',
                recommended_mode: 'transit',
                ranked_options: [
                  {
                    mode: 'transit',
                    total_score: 80,
                    duration_s: 2700,
                    latest_departure_at: '2026-09-14T08:00:00+08:00',
                    cost_yuan: 5,
                    walking_distance_m: 800,
                    transfer_count: 1,
                  },
                  {
                    mode: 'walking',
                    total_score: 45,
                    duration_s: 9000,
                    cost_yuan: 0,
                    walking_distance_m: 12500,
                    transfer_count: 0,
                  },
                ],
              },
              {
                priority: 'cheapest',
                recommended_mode: 'transit',
                ranked_options: [
                  {
                    mode: 'transit',
                    total_score: 84,
                    duration_s: 2700,
                    latest_departure_at: '2026-09-14T08:00:00+08:00',
                    cost_yuan: 5,
                    walking_distance_m: 800,
                    transfer_count: 1,
                  },
                  {
                    mode: 'walking',
                    total_score: 38,
                    duration_s: 9000,
                    cost_yuan: 0,
                    walking_distance_m: 12500,
                    transfer_count: 0,
                  },
                ],
              },
              {
                priority: 'fewest_transfers',
                recommended_mode: 'walking',
                ranked_options: [
                  {
                    mode: 'walking',
                    total_score: 89,
                    duration_s: 9000,
                    cost_yuan: 0,
                    walking_distance_m: 12500,
                    transfer_count: 0,
                  },
                  {
                    mode: 'transit',
                    total_score: 70,
                    duration_s: 2700,
                    cost_yuan: 5,
                    walking_distance_m: 800,
                    transfer_count: 1,
                  },
                ],
              },
            ],
            transit_candidates: [
              {
                candidate_index: 0,
                selected: false,
                duration_s: 3000,
                cost_yuan: 4,
                walking_distance_m: 300,
                transfer_count: 0,
                line_names: ['地铁5号线'],
                legs: [
                  {
                    mode: 'walking',
                    distance_m: 300,
                    duration_s: 240,
                    instruction: '步行至大学城站',
                  },
                  {
                    mode: 'subway',
                    distance_m: 7700,
                    duration_s: 1500,
                    line_name: '地铁5号线',
                    departure_stop: '大学城站',
                    arrival_stop: '西丽站',
                    via_stop_count: 2,
                  },
                ],
              },
              {
                candidate_index: 1,
                selected: true,
                duration_s: 1800,
                cost_yuan: 7,
                walking_distance_m: 900,
                transfer_count: 1,
                line_names: ['M176路', '地铁1号线'],
              },
            ],
            selected_transit_candidate_index: 1,
            unavailable_options: [
              {
                mode: 'driving',
                reason: '用户明确表示不能驾车',
              },
            ],
          },
        },
        {
          type: 'final',
          thread_id: 'thread-replan',
          answer: '已根据新约束重新推荐。',
        },
        { type: 'done', thread_id: 'thread-replan' },
      ]
      for (const event of events) {
        onEvent(event)
      }
    })

    const user = userEvent.setup()
    render(<App />)
    await screen.findByText('后端已连接，当前加载 1 个 Agent 工具。')
    await user.type(screen.getByLabelText('输入你的出行问题'), '如果不能开车呢')
    await user.click(screen.getByRole('button', { name: '发送问题' }))

    expect(await screen.findByText('本地重新评分')).toBeInTheDocument()
    expect(screen.getByText('驾车 → 公共交通')).toBeInTheDocument()
    expect(screen.getAllByText('优先省钱')).toHaveLength(2)
    expect(screen.getByText('不能驾车')).toBeInTheDocument()
    expect(screen.getByText(/目标到达.*9\/14.*09:00/)).toBeInTheDocument()
    expect(screen.getByText('已预留 15 分钟缓冲')).toBeInTheDocument()
    expect(screen.getByText(/最晚.*9\/14.*08:00.*出发/)).toBeInTheDocument()
    expect(screen.getByText('最多步行 1.0 公里')).toBeInTheDocument()
    expect(screen.getByText('1. 公共交通')).toBeInTheDocument()
    expect(screen.getByText('公共交通候选')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '地铁优先' }))
      .toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByText('已比较 2 条具体路线')).toBeInTheDocument()
    await user.click(screen.getAllByText('查看完整路线').at(-2)!)
    expect(screen.getByText('步行至大学城站')).toBeInTheDocument()
    expect(screen.getByText('大学城站 → 西丽站 · 途经 2 站'))
      .toBeInTheDocument()
    expect(screen.getByText('7.7 公里 · 25 分钟')).toBeInTheDocument()
    expect(screen.getByText('候选 2')).toBeInTheDocument()
    expect(screen.getByText('M176路 → 地铁1号线')).toBeInTheDocument()
    expect(screen.getByText('当前路线')).toBeInTheDocument()
    expect(screen.getByText('30 分钟 · 费用 7 元 · 步行 900 米 · 换乘 1 次'))
      .toBeInTheDocument()
    expect(screen.getByText('驾车：用户明确表示不能驾车')).toBeInTheDocument()
    expect(screen.getByText(/复用上一轮路线与天气快照/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '优先少换乘' }))
    expect(screen.getByText('该偏好推荐')).toBeInTheDocument()
    expect(screen.getAllByText('步行').length).toBeGreaterThanOrEqual(2)
    expect(screen.getByText('1. 步行')).toBeInTheDocument()
    expect(screen.getByText('费用 0 元 · 步行 12.5 公里 · 换乘 0 次'))
      .toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '少步行' }))
    expect(streamChatMock.mock.calls.at(-1)?.[0])
      .toBe('公共交通改为少步行')

    const candidateButtons = screen.getAllByRole('button', {
      name: '选择公共交通候选 1',
    })
    expect(candidateButtons[0]).toBeDisabled()
    expect(candidateButtons.at(-1)).not.toBeDisabled()
    await user.click(candidateButtons.at(-1)!)
    expect(streamChatMock.mock.calls.at(-1)?.[0])
      .toBe('选择公共交通候选1')
  })

  test('renders an explicit warning for a stale route fallback', async () => {
    streamChatMock.mockImplementation(async (_message, _threadId, onEvent) => {
      const events: AgentStreamEvent[] = [
        { type: 'run_started', thread_id: 'thread-stale-fallback' },
        {
          type: 'result_card',
          thread_id: 'thread-stale-fallback',
          card: {
            type: 'planning',
            origin_name: '粤海校区',
            destination_name: '丽湖校区',
            recommended_mode: 'transit',
            reused_previous_data: true,
            route_refreshed: false,
            used_stale_snapshot: true,
            refresh_failed: true,
            route_snapshot_at: '2026-09-14T10:00:00+08:00',
            preferences: { priority: 'balanced', can_drive: false },
            ranked_options: [
              {
                mode: 'transit',
                total_score: 80,
                duration_s: 2700,
                walking_distance_m: 800,
                transfer_count: 1,
              },
            ],
            unavailable_options: [],
          },
        },
        {
          type: 'final',
          thread_id: 'thread-stale-fallback',
          answer: '当前使用过期路线快照。',
        },
        { type: 'done', thread_id: 'thread-stale-fallback' },
      ]
      for (const event of events) {
        onEvent(event)
      }
    })

    const user = userEvent.setup()
    render(<App />)
    await screen.findByText('后端已连接，当前加载 1 个 Agent 工具。')
    await user.type(screen.getByLabelText('输入你的出行问题'), '不能开车呢')
    await user.click(screen.getByRole('button', { name: '发送问题' }))

    expect(await screen.findByText('降级路线建议')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent(
      /路线刷新失败.*9\/14.*10:00.*过期快照.*仅供临时参考/,
    )
  })

  test.each([
    {
      decision: 'approve' as const,
      buttonName: '批准执行',
      userText: '已批准该操作。',
      decisionMessage: undefined,
    },
    {
      decision: 'reject' as const,
      buttonName: '拒绝执行',
      userText: '已拒绝该操作。',
      decisionMessage: '用户在 Web 页面拒绝了该操作。',
    },
  ])(
    'resumes an approval-required stream with $decision',
    async ({ decision, buttonName, userText, decisionMessage }) => {
      streamChatMock.mockImplementation(
        async (_message, _threadId, onEvent) => {
          const events: AgentStreamEvent[] = [
            { type: 'run_started', thread_id: 'thread-approval' },
            {
              type: 'tool_requested',
              thread_id: 'thread-approval',
              tool_name: 'save_itinerary',
              tool_args: { title: '前端审批测试' },
              tool_call_id: 'call-save',
            },
            {
              type: 'approval_required',
              thread_id: 'thread-approval',
              pending_actions: [
                {
                  name: 'save_itinerary',
                  args: { title: '前端审批测试' },
                  description: '请确认是否保存以下行程。',
                  allowed_decisions: ['approve', 'reject'],
                },
              ],
            },
            { type: 'done', thread_id: 'thread-approval' },
          ]
          for (const event of events) {
            onEvent(event)
          }
        },
      )
      decideApprovalMock.mockResolvedValue({
        status: 'completed',
        thread_id: 'thread-approval',
        answer: `审批完成：${decision}`,
        pending_actions: [],
      })

      const user = userEvent.setup()
      render(<App />)
      await screen.findByText('后端已连接，当前加载 1 个 Agent 工具。')
      await user.type(screen.getByLabelText('输入你的出行问题'), '保存行程')
      await user.click(screen.getByRole('button', { name: '发送问题' }))

      await screen.findByRole('heading', { name: '操作等待审批' })
      expect(screen.getByText('Agent 已暂停，正在等待你审批操作。'))
        .toBeInTheDocument()
      expect(screen.getByText('前端审批测试')).toBeInTheDocument()

      await user.click(screen.getByRole('button', { name: buttonName }))

      await screen.findByText(`审批完成：${decision}`)
      expect(decideApprovalMock).toHaveBeenCalledWith(
        'thread-approval',
        decision,
        decisionMessage,
      )
      expect(screen.getByText(userText)).toBeInTheDocument()
      expect(
        screen.queryByRole('heading', { name: '操作等待审批' }),
      ).not.toBeInTheDocument()
    },
  )

  test('restores and clears a structured session from sessionStorage', async () => {
    sessionStorage.setItem(
      'smart-travel-agent-session',
      JSON.stringify({
        messages: [
          {
            id: 'stored-assistant',
            role: 'assistant',
            content: '已恢复的天气回答',
            cards: [WEATHER_CARD],
          },
        ],
        threadId: 'stored-thread',
        pendingActions: [],
      }),
    )

    const user = userEvent.setup()
    render(<App />)

    expect(screen.getByText('已恢复的天气回答')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '深圳' })).toBeInTheDocument()
    expect(screen.getByText('当前会话 ID：stored-thread')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '新建对话' }))

    expect(screen.queryByText('已恢复的天气回答')).not.toBeInTheDocument()
    expect(screen.queryByText('当前会话 ID：stored-thread'))
      .not.toBeInTheDocument()
  })

  test('loads a persisted conversation and its result cards', async () => {
    getConversationsMock.mockResolvedValue([
      {
        thread_id: 'stored-history-thread',
        title: '深圳现在天气怎么样？',
        status: 'ready',
        created_at: '2026-07-29T12:00:00Z',
        updated_at: '2026-07-29T12:01:00Z',
      },
    ])
    getConversationMock.mockResolvedValue({
      conversation: {
        thread_id: 'stored-history-thread',
        title: '深圳现在天气怎么样？',
        status: 'ready',
        created_at: '2026-07-29T12:00:00Z',
        updated_at: '2026-07-29T12:01:00Z',
      },
      messages: [
        {
          message_id: 'stored-user-message',
          thread_id: 'stored-history-thread',
          role: 'user',
          content: '深圳现在天气怎么样？',
          cards: [],
          created_at: '2026-07-29T12:00:00Z',
        },
        {
          message_id: 'stored-assistant-message',
          thread_id: 'stored-history-thread',
          role: 'assistant',
          content: '这是从数据库恢复的天气回答。',
          cards: [WEATHER_CARD],
          created_at: '2026-07-29T12:01:00Z',
        },
      ],
    })

    const user = userEvent.setup()
    render(<App />)

    const historyTitle = await screen.findByText('深圳现在天气怎么样？')
    const historyButton = historyTitle.closest('button')
    expect(historyButton).not.toBeNull()
    await user.click(historyButton as HTMLButtonElement)

    expect(getConversationMock).toHaveBeenCalledWith('stored-history-thread')
    expect(
      await screen.findByText('这是从数据库恢复的天气回答。'),
    ).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '深圳' })).toBeInTheDocument()
    expect(
      screen.getByText('当前会话 ID：stored-history-thread'),
    ).toBeInTheDocument()
  })

  test('deletes history only after explicit confirmation', async () => {
    const summary = {
      thread_id: 'delete-thread',
      title: '准备删除的会话',
      status: 'ready' as const,
      created_at: '2026-07-29T12:00:00Z',
      updated_at: '2026-07-29T12:01:00Z',
    }
    getConversationsMock
      .mockResolvedValueOnce([summary])
      .mockResolvedValueOnce([])
    deleteConversationMock.mockResolvedValue()
    const confirmMock = vi
      .spyOn(window, 'confirm')
      .mockReturnValueOnce(false)
      .mockReturnValueOnce(true)

    const user = userEvent.setup()
    render(<App />)
    const deleteButton = await screen.findByRole('button', {
      name: '删除会话：准备删除的会话',
    })

    await user.click(deleteButton)
    expect(deleteConversationMock).not.toHaveBeenCalled()

    await user.click(deleteButton)
    await waitFor(() => {
      expect(deleteConversationMock).toHaveBeenCalledWith('delete-thread')
    })
    await waitFor(() => {
      expect(screen.queryByText('准备删除的会话')).not.toBeInTheDocument()
    })
    expect(confirmMock).toHaveBeenCalledTimes(2)
  })
})
