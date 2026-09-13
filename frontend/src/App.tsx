import { useCallback, useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import ReactMarkdown from 'react-markdown'
import {
  decideApproval,
  deleteConversation,
  getConversation,
  getConversations,
  getHealth,
  streamChat,
} from './api'
import type {
  ApprovalDecision,
  ConversationSummary,
  PendingAction,
  PlaceResultCard,
  PlanningResultCard,
  ResultCard,
  RouteResultCard,
  TransitResultCard,
  WeatherResultCard,
} from './api'
import './App.css'

type ConnectionState = 'loading' | 'connected' | 'error'

type ChatMessage = {
  id: string
  role: 'user' | 'assistant'
  content: string
  cards?: ResultCard[]
}

type StoredSession = {
  messages: ChatMessage[]
  threadId: string
  pendingActions: PendingAction[]
}

const SESSION_STORAGE_KEY = 'smart-travel-agent-session'

const TOOL_LABELS: Record<string, string> = {
  query_current_weather: '实时天气查询',
  search_places: '地点搜索',
  resolve_route_endpoints: '起终点解析',
  plan_driving_route: '驾车路线规划',
  plan_walking_route: '步行路线规划',
  plan_transit_route: '公共交通路线规划',
  recommend_travel_plan: '综合出行推荐',
  save_itinerary: '行程保存',
}

function loadStoredSession(): StoredSession {
  const emptySession: StoredSession = {
    messages: [],
    threadId: '',
    pendingActions: [],
  }
  const storedValue = sessionStorage.getItem(SESSION_STORAGE_KEY)
  if (!storedValue) {
    return emptySession
  }

  try {
    const parsed = JSON.parse(storedValue) as Partial<StoredSession>
    return {
      messages: Array.isArray(parsed.messages) ? parsed.messages : [],
      threadId: typeof parsed.threadId === 'string' ? parsed.threadId : '',
      pendingActions: Array.isArray(parsed.pendingActions)
        ? parsed.pendingActions
        : [],
    }
  } catch {
    return emptySession
  }
}

function formatActionValue(value: unknown): string {
  if (value === null || value === undefined) {
    return '—'
  }
  if (typeof value === 'object') {
    return JSON.stringify(value)
  }
  return String(value)
}

function WeatherCard({ card }: { card: WeatherResultCard }) {
  const region = [card.country, card.admin1].filter(Boolean).join(' · ')

  return (
    <section className="result-card weather-card" aria-label={`${card.city}天气`}>
      <div className="result-card-heading">
        <div>
          <p className="result-card-kicker">实时天气</p>
          <h4>{card.city}</h4>
          {region && <p>{region}</p>}
        </div>
        <div className="weather-temperature">
          {card.temperature_c.toFixed(1)}°C
        </div>
      </div>

      <dl className="result-card-metrics">
        <div>
          <dt>天气</dt>
          <dd>{card.condition}</dd>
        </div>
        <div>
          <dt>体感温度</dt>
          <dd>{card.apparent_temperature_c.toFixed(1)}°C</dd>
        </div>
        <div>
          <dt>降水</dt>
          <dd>{card.precipitation_mm.toFixed(1)} mm</dd>
        </div>
        <div>
          <dt>风速</dt>
          <dd>{card.wind_speed_kmh.toFixed(1)} km/h</dd>
        </div>
      </dl>

      <p className="result-card-time">观测时间：{card.observed_at}</p>
    </section>
  )
}

function formatDistance(distanceM: number): string {
  if (distanceM >= 1000) {
    return `${(distanceM / 1000).toFixed(1)} 公里`
  }
  return `${Math.round(distanceM)} 米`
}

function formatDuration(durationS: number): string {
  const totalMinutes = Math.round(durationS / 60)
  const hours = Math.floor(totalMinutes / 60)
  const minutes = totalMinutes % 60
  if (hours > 0) {
    return `${hours} 小时 ${minutes} 分钟`
  }
  return `${totalMinutes} 分钟`
}

function RouteCard({ card }: { card: RouteResultCard }) {
  const modeLabel = card.mode === 'driving' ? '驾车路线' : '步行路线'
  const trafficAware = card.duration_basis === 'traffic_aware_estimate'
  const trafficSummary = Object.entries(card.traffic_status_counts ?? {})
    .map(([status, count]) => `${status} ${count} 段`)
    .join(' · ')

  return (
    <section className="result-card route-card" aria-label={modeLabel}>
      <div className="result-card-heading">
        <div>
          <p className="result-card-kicker">路线规划</p>
          <h4>{modeLabel}</h4>
        </div>
      </div>

      <dl className="result-card-metrics">
        <div>
          <dt>总距离</dt>
          <dd>{formatDistance(card.distance_m)}</dd>
        </div>
        <div>
          <dt>{trafficAware ? '交通感知预计时长' : '静态预计时长'}</dt>
          <dd>{formatDuration(card.duration_s)}</dd>
        </div>
        <div>
          <dt>导航步骤</dt>
          <dd>{card.step_count} 步</dd>
        </div>
        {card.tolls_yuan != null && (
          <div>
            <dt>道路通行费</dt>
            <dd>¥{card.tolls_yuan.toFixed(2)}</dd>
          </div>
        )}
        {card.taxi_cost_yuan != null && (
          <div>
            <dt>出租车估价</dt>
            <dd>¥{card.taxi_cost_yuan.toFixed(2)}</dd>
          </div>
        )}
        {card.traffic_lights != null && (
          <div>
            <dt>红绿灯</dt>
            <dd>{card.traffic_lights} 个</dd>
          </div>
        )}
      </dl>

      {trafficSummary && (
        <p className="result-card-notice">查询时路况：{trafficSummary}</p>
      )}
      <p className="result-card-notice">
        {trafficAware
          ? '预计时长包含查询时交通状况，但不是持续更新的实时导航，出发前请再次确认。'
          : '预计时长不包含实时路况，出发前请使用实时导航确认。'}
      </p>
      <p className="result-card-time">数据来源：{card.attribution}</p>
    </section>
  )
}

function TransitCard({ card }: { card: TransitResultCard }) {
  const costText =
    card.cost_yuan == null ? '未提供' : `¥${card.cost_yuan.toFixed(2)}`
  const lineText =
    card.line_names.length > 0
      ? card.line_names.join(' → ')
      : '线路名称未提供'

  return (
    <section className="result-card transit-card" aria-label="公共交通路线">
      <div className="result-card-heading">
        <div>
          <p className="result-card-kicker">路线规划</p>
          <h4>公共交通</h4>
          <p>展示第 1 个方案，共 {card.option_count} 个候选</p>
        </div>
      </div>

      <dl className="result-card-metrics">
        <div>
          <dt>总距离</dt>
          <dd>{formatDistance(card.distance_m)}</dd>
        </div>
        <div>
          <dt>静态预计时长</dt>
          <dd>{formatDuration(card.duration_s)}</dd>
        </div>
        <div>
          <dt>步行距离</dt>
          <dd>{formatDistance(card.walking_distance_m)}</dd>
        </div>
        <div>
          <dt>换乘次数</dt>
          <dd>{card.transfer_count} 次</dd>
        </div>
        <div>
          <dt>费用</dt>
          <dd>{costText}</dd>
        </div>
      </dl>

      <div className="transit-lines">
        <span>乘坐线路</span>
        <strong>{lineText}</strong>
      </div>
      <p className="result-card-notice">
        静态方案不包含实时班次、车辆位置或到站信息，出发前请向运营方或实时导航确认。
      </p>
      <p className="result-card-time">数据来源：{card.attribution}</p>
    </section>
  )
}

function PlaceCard({ card }: { card: PlaceResultCard }) {
  return (
    <section className="result-card place-card" aria-label="地点搜索结果">
      <div className="result-card-heading">
        <div>
          <p className="result-card-kicker">地点搜索</p>
          <h4>{card.query}</h4>
          <p>找到 {card.places.length} 个候选地点</p>
        </div>
      </div>

      {card.places.length > 0 ? (
        <ol className="place-candidates">
          {card.places.map((place, index) => {
            const tags = [place.category, place.place_type]
              .filter(Boolean)
              .join(' · ')
            return (
              <li key={`${place.latitude}-${place.longitude}-${index}`}>
                <strong>{place.display_name}</strong>
                <span>
                  纬度 {place.latitude.toFixed(6)} · 经度{' '}
                  {place.longitude.toFixed(6)}
                </span>
                {tags && <small>{tags}</small>}
              </li>
            )
          })}
        </ol>
      ) : (
        <p className="result-card-notice">没有找到候选地点。</p>
      )}

      <p className="result-card-time">数据来源：{card.attribution}</p>
    </section>
  )
}

const MODE_LABELS = {
  driving: '驾车',
  transit: '公共交通',
  walking: '步行',
}

const PRIORITY_LABELS = {
  balanced: '均衡考虑',
  fastest: '优先速度',
  cheapest: '优先省钱',
  least_walking: '优先少步行',
  fewest_transfers: '优先少换乘',
}

function PlanningCard({ card }: { card: PlanningResultCard }) {
  const availableVariants = card.recommendation_variants?.length
    ? card.recommendation_variants
    : [{
        priority: card.preferences.priority,
        recommended_mode: card.recommended_mode,
        ranked_options: card.ranked_options,
      }]
  const [selectedPriority, setSelectedPriority] = useState(
    card.preferences.priority,
  )
  const selectedVariant = availableVariants.find(
    (variant) => variant.priority === selectedPriority,
  ) ?? availableVariants[0]
  const preferenceLabels = [PRIORITY_LABELS[card.preferences.priority]]
  if (card.preferences.can_drive === false) {
    preferenceLabels.push('不能驾车')
  } else if (card.preferences.can_drive === true) {
    preferenceLabels.push('可以驾车')
  }
  if (card.preferences.max_walking_distance_m != null) {
    preferenceLabels.push(
      `最多步行 ${formatDistance(card.preferences.max_walking_distance_m)}`,
    )
  }
  if (card.preferences.max_transfer_count != null) {
    preferenceLabels.push(`最多换乘 ${card.preferences.max_transfer_count} 次`)
  }

  const previousModeLabel = card.previous_recommended_mode == null
    ? null
    : MODE_LABELS[card.previous_recommended_mode]
  const recommendationChanged =
    selectedPriority === card.preferences.priority
    &&
    previousModeLabel != null
    && card.previous_recommended_mode !== card.recommended_mode

  return (
    <section className="result-card planning-card" aria-label="综合出行推荐">
      <div className="result-card-heading">
        <div>
          <p className="result-card-kicker">
            {card.reused_previous_data ? '本地重新评分' : '综合出行推荐'}
          </p>
          <h4>{card.origin_name} → {card.destination_name}</h4>
        </div>
      </div>

      <div className="planning-recommendation">
        {recommendationChanged ? (
          <>
            <span>推荐变化</span>
            <strong>
              {previousModeLabel} → {MODE_LABELS[card.recommended_mode]}
            </strong>
          </>
        ) : (
          <>
            <span>
              {selectedPriority === card.preferences.priority
                ? '当前推荐'
                : '该偏好推荐'}
            </span>
            <strong>{MODE_LABELS[selectedVariant.recommended_mode]}</strong>
          </>
        )}
      </div>

      <div className="planning-variant-tabs" aria-label="切换推荐偏好">
        {availableVariants.map((variant) => (
          <button
            type="button"
            key={variant.priority}
            className={variant.priority === selectedPriority ? 'active' : ''}
            aria-pressed={variant.priority === selectedPriority}
            onClick={() => setSelectedPriority(variant.priority)}
          >
            {PRIORITY_LABELS[variant.priority]}
          </button>
        ))}
      </div>

      <div className="planning-preferences" aria-label="当前偏好">
        {preferenceLabels.map((label) => (
          <span key={label}>{label}</span>
        ))}
      </div>

      <ol className="planning-ranking">
        {selectedVariant.ranked_options.map((option, index) => (
          <li key={option.mode}>
            <strong>{index + 1}. {MODE_LABELS[option.mode]}</strong>
            <span>
              {option.total_score.toFixed(1)} 分 · {formatDuration(option.duration_s)}
            </span>
            {(option.cost_yuan != null
              || option.walking_distance_m != null
              || option.transfer_count != null) && (
              <small>
                {option.cost_yuan != null
                  ? `费用 ${option.cost_yuan.toFixed(0)} 元`
                  : ''}
                {option.cost_yuan != null
                  && (option.walking_distance_m != null
                    || option.transfer_count != null) ? ' · ' : ''}
                {option.walking_distance_m != null
                  ? `步行 ${formatDistance(option.walking_distance_m)}`
                  : ''}
                {option.walking_distance_m != null
                  && option.transfer_count != null ? ' · ' : ''}
                {option.transfer_count != null
                  ? `换乘 ${option.transfer_count} 次`
                  : ''}
              </small>
            )}
          </li>
        ))}
      </ol>

      {card.unavailable_options.length > 0 && (
        <div className="planning-exclusions">
          <strong>未参与推荐</strong>
          <ul>
            {card.unavailable_options.map((option) => (
              <li key={`${option.mode}-${option.reason}`}>
                {MODE_LABELS[option.mode]}：{option.reason}
              </li>
            ))}
          </ul>
        </div>
      )}

      {card.reused_previous_data && (
        <p className="result-card-time">
          复用上一轮路线与天气快照，本轮未重新请求外部服务。
        </p>
      )}
    </section>
  )
}

function ResultCardView({ card }: { card: ResultCard }) {
  if (card.type === 'weather') {
    return <WeatherCard card={card} />
  }
  if (card.type === 'route') {
    return <RouteCard card={card} />
  }
  if (card.type === 'transit') {
    return <TransitCard card={card} />
  }
  if (card.type === 'place') {
    return <PlaceCard card={card} />
  }
  if (card.type === 'planning') {
    return <PlanningCard card={card} />
  }
  return null
}

function App() {
  const [initialSession] = useState<StoredSession>(loadStoredSession)
  const [connectionState, setConnectionState] =
    useState<ConnectionState>('loading')
  const [tools, setTools] = useState<string[]>([])
  const [errorMessage, setErrorMessage] = useState('')
  const [message, setMessage] = useState('')
  const [messages, setMessages] = useState<ChatMessage[]>(
    initialSession.messages,
  )
  const [threadId, setThreadId] = useState(initialSession.threadId)
  const [isSending, setIsSending] = useState(false)
  const [isDeciding, setIsDeciding] = useState(false)
  const [chatError, setChatError] = useState('')
  const [activityText, setActivityText] = useState('')
  const [pendingActions, setPendingActions] = useState<PendingAction[]>(
    initialSession.pendingActions,
  )
  const [conversations, setConversations] = useState<ConversationSummary[]>([])
  const [isLoadingHistory, setIsLoadingHistory] = useState(false)
  const [historyError, setHistoryError] = useState('')

  const refreshConversations = useCallback(async () => {
    try {
      const history = await getConversations()
      setConversations(history)
      setHistoryError('')
    } catch (error) {
      const errorText = error instanceof Error ? error.message : '未知错误'
      setHistoryError(errorText)
    }
  }, [])

  useEffect(() => {
    async function loadHealth() {
      try {
        const data = await getHealth()
        setTools(data.tools)
        setConnectionState('connected')
      } catch (error) {
        const message = error instanceof Error ? error.message : '未知错误'
        setErrorMessage(message)
        setConnectionState('error')
      }
    }

    void loadHealth()
    void refreshConversations()
  }, [refreshConversations])

  useEffect(() => {
    const storedSession: StoredSession = {
      messages,
      threadId,
      pendingActions,
    }
    sessionStorage.setItem(
      SESSION_STORAGE_KEY,
      JSON.stringify(storedSession),
    )
  }, [messages, threadId, pendingActions])

  function handleNewConversation() {
    setMessages([])
    setThreadId('')
    setPendingActions([])
    setMessage('')
    setChatError('')
    setActivityText('')
  }

  async function handleLoadConversation(selectedThreadId: string) {
    if (isSending || isDeciding || pendingActions.length > 0) {
      return
    }

    setIsLoadingHistory(true)
    setHistoryError('')
    setChatError('')
    setActivityText('')
    try {
      const detail = await getConversation(selectedThreadId)
      setMessages(
        detail.messages.map((storedMessage) => ({
          id: storedMessage.message_id,
          role: storedMessage.role,
          content: storedMessage.content,
          cards: storedMessage.cards,
        })),
      )
      setThreadId(detail.conversation.thread_id)
      setPendingActions([])
    } catch (error) {
      const errorText = error instanceof Error ? error.message : '未知错误'
      setHistoryError(errorText)
    } finally {
      setIsLoadingHistory(false)
    }
  }

  async function handleDeleteConversation(
    conversation: ConversationSummary,
  ) {
    const confirmed = window.confirm(
      `确定删除会话“${conversation.title}”吗？\n删除后消息和 Agent 上下文都无法恢复。`,
    )
    if (!confirmed) {
      return
    }

    setIsLoadingHistory(true)
    setHistoryError('')
    try {
      await deleteConversation(conversation.thread_id)
      if (conversation.thread_id === threadId) {
        handleNewConversation()
      }
      await refreshConversations()
    } catch (error) {
      const errorText = error instanceof Error ? error.message : '未知错误'
      setHistoryError(errorText)
    } finally {
      setIsLoadingHistory(false)
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()

    const trimmedMessage = message.trim()
    if (!trimmedMessage || isSending || pendingActions.length > 0) {
      return
    }

    const userMessage: ChatMessage = {
      id: crypto.randomUUID(),
      role: 'user',
      content: trimmedMessage,
    }
    const assistantMessageId = crypto.randomUUID()
    const assistantMessage: ChatMessage = {
      id: assistantMessageId,
      role: 'assistant',
      content: '',
    }

    setMessages((currentMessages) => [
      ...currentMessages,
      userMessage,
      assistantMessage,
    ])
    setMessage('')
    setIsSending(true)
    setChatError('')
    setActivityText('Agent 正在分析问题……')
    setPendingActions([])

    try {
      await streamChat(
        trimmedMessage,
        threadId || undefined,
        (streamEvent) => {
          setThreadId(streamEvent.thread_id)

          if (streamEvent.type === 'tool_requested') {
            const toolName = streamEvent.tool_name ?? '未知工具'
            const label = TOOL_LABELS[toolName] ?? toolName
            setActivityText(`正在调用：${label}……`)
          } else if (streamEvent.type === 'tool_completed') {
            const toolName = streamEvent.tool_name ?? '工具'
            const label = TOOL_LABELS[toolName] ?? toolName
            setActivityText(`${label}已完成，Agent 正在生成回答……`)
          } else if (
            streamEvent.type === 'assistant_delta'
            && streamEvent.delta
          ) {
            setActivityText('Agent 正在生成回答……')
            setMessages((currentMessages) =>
              currentMessages.map((currentMessage) =>
                currentMessage.id === assistantMessageId
                  ? {
                      ...currentMessage,
                      content: currentMessage.content + streamEvent.delta,
                    }
                  : currentMessage,
              ),
            )
          } else if (
            streamEvent.type === 'result_card'
            && streamEvent.card
          ) {
            setMessages((currentMessages) =>
              currentMessages.map((currentMessage) =>
                currentMessage.id === assistantMessageId
                  ? {
                      ...currentMessage,
                      cards: [
                        ...(currentMessage.cards ?? []),
                        streamEvent.card as ResultCard,
                      ],
                    }
                  : currentMessage,
              ),
            )
          } else if (streamEvent.type === 'final') {
            setMessages((currentMessages) =>
              currentMessages.map((currentMessage) =>
                currentMessage.id === assistantMessageId
                  ? {
                      ...currentMessage,
                      content:
                        streamEvent.answer
                        ?? 'Agent 已完成，但没有返回文字回答。',
                    }
                  : currentMessage,
              ),
            )
            void refreshConversations()
          } else if (streamEvent.type === 'approval_required') {
            setPendingActions(streamEvent.pending_actions ?? [])
            setMessages((currentMessages) =>
              currentMessages.map((currentMessage) =>
                currentMessage.id === assistantMessageId
                  ? {
                      ...currentMessage,
                      content: 'Agent 已暂停，正在等待你审批操作。',
                    }
                  : currentMessage,
              ),
            )
            void refreshConversations()
          } else if (streamEvent.type === 'error') {
            setChatError(streamEvent.error ?? 'Agent 执行失败')
          } else if (streamEvent.type === 'done') {
            setActivityText('')
          }
        },
      )
    } catch (error) {
      const errorText = error instanceof Error ? error.message : '未知错误'
      setChatError(errorText)
    } finally {
      setIsSending(false)
      setActivityText('')
    }
  }

  async function handleDecision(decision: ApprovalDecision) {
    if (!threadId || isDeciding) {
      return
    }

    setIsDeciding(true)
    setChatError('')

    try {
      const result = await decideApproval(
        threadId,
        decision,
        decision === 'reject' ? '用户在 Web 页面拒绝了该操作。' : undefined,
      )

      const decisionMessage: ChatMessage = {
        id: crypto.randomUUID(),
        role: 'user',
        content: decision === 'approve' ? '已批准该操作。' : '已拒绝该操作。',
      }
      const assistantMessage: ChatMessage = {
        id: crypto.randomUUID(),
        role: 'assistant',
        content:
          result.status === 'approval_required'
            ? 'Agent 还有操作需要你审批。'
            : (result.answer ?? 'Agent 已完成，但没有返回文字回答。'),
      }

      setPendingActions(result.pending_actions)
      setMessages((currentMessages) => [
        ...currentMessages,
        decisionMessage,
        assistantMessage,
      ])
      await refreshConversations()
    } catch (error) {
      const errorText = error instanceof Error ? error.message : '未知错误'
      setChatError(errorText)
    } finally {
      setIsDeciding(false)
    }
  }

  return (
    <main>
      <header>
        <div className="header-topline">
          <p>Smart Travel Agent</p>
          {(messages.length > 0 || threadId) && (
            <button
              type="button"
              className="button-secondary"
              onClick={handleNewConversation}
              disabled={pendingActions.length > 0 || isDeciding}
            >
              新建对话
            </button>
          )}
        </div>
        <h1>智能出行助手</h1>
        <p>天气、地点搜索和多方式路线规划，由 Agent 自动调用工具完成。</p>
      </header>

      <div className="app-layout">
        <aside className="history-panel" aria-labelledby="history-title">
          <div className="history-heading">
            <h2 id="history-title">历史会话</h2>
            <button
              type="button"
              className="button-secondary history-refresh"
              onClick={() => void refreshConversations()}
              disabled={isLoadingHistory}
            >
              刷新
            </button>
          </div>

          {historyError && <p role="alert">历史加载失败：{historyError}</p>}
          {conversations.length === 0 && !historyError && (
            <p className="history-empty">还没有已保存的会话。</p>
          )}
          <nav aria-label="历史会话列表">
            <ul className="history-list">
              {conversations.map((conversation) => (
                <li className="history-row" key={conversation.thread_id}>
                  <button
                    type="button"
                    className={
                      conversation.thread_id === threadId
                        ? 'history-item history-item-active'
                        : 'history-item'
                    }
                    onClick={() =>
                      void handleLoadConversation(conversation.thread_id)}
                    disabled={
                      isLoadingHistory
                      || isSending
                      || isDeciding
                      || pendingActions.length > 0
                      || conversation.status === 'approval_required'
                    }
                  >
                    <strong>{conversation.title}</strong>
                    <span>
                      {conversation.status === 'ready'
                        ? '已完成'
                        : conversation.status === 'approval_required'
                          ? '等待审批'
                          : '执行失败'}
                    </span>
                  </button>
                  <button
                    type="button"
                    className="history-delete"
                    aria-label={`删除会话：${conversation.title}`}
                    title="删除会话"
                    onClick={() =>
                      void handleDeleteConversation(conversation)}
                    disabled={isLoadingHistory || isSending || isDeciding}
                  >
                    删除
                  </button>
                </li>
              ))}
            </ul>
          </nav>
        </aside>

        <div className="content-column">
      <section aria-labelledby="backend-status-title">
        <h2 id="backend-status-title">后端连接状态</h2>

        {connectionState === 'loading' && <p>正在连接 FastAPI 后端……</p>}

        {connectionState === 'connected' && (
          <>
            <p>后端已连接，当前加载 {tools.length} 个 Agent 工具。</p>
            <ul>
              {tools.map((tool) => (
                <li key={tool}>{tool}</li>
              ))}
            </ul>
          </>
        )}

        {connectionState === 'error' && (
          <p role="alert">连接失败：{errorMessage}</p>
        )}
      </section>

      <section aria-labelledby="chat-title">
        <h2 id="chat-title">和 Agent 对话</h2>

        <form onSubmit={handleSubmit}>
          <label htmlFor="message">输入你的出行问题</label>
          <textarea
            id="message"
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            placeholder="例如：深圳现在天气怎么样？"
            rows={4}
            disabled={isSending || isDeciding || pendingActions.length > 0}
          />
          <button
            type="submit"
            disabled={
              isSending || !message.trim() || connectionState !== 'connected'
              || isDeciding
              || pendingActions.length > 0
            }
          >
            {isSending ? 'Agent 正在处理……' : '发送问题'}
          </button>
        </form>

        {chatError && <p role="alert">请求失败：{chatError}</p>}

        {activityText && (
          <p className="agent-activity" role="status">
            {activityText}
          </p>
        )}

        <div className="message-list" aria-live="polite">
          {messages.map((chatMessage) => (
            <article
              key={chatMessage.id}
              className={`message message-${chatMessage.role}`}
            >
              <h3>{chatMessage.role === 'user' ? '你' : 'Agent'}</h3>
              <div className="message-content">
                {chatMessage.role === 'assistant' ? (
                  <ReactMarkdown>{chatMessage.content}</ReactMarkdown>
                ) : (
                  chatMessage.content
                )}
              </div>
              {chatMessage.cards?.map((card, index) => (
                <ResultCardView
                  key={`${card.type}-${index}`}
                  card={card}
                />
              ))}
            </article>
          ))}
        </div>

        {threadId && <p>当前会话 ID：{threadId}</p>}
      </section>

      {pendingActions.length > 0 && (
        <section aria-labelledby="approval-title">
          <h2 id="approval-title">操作等待审批</h2>

          {pendingActions.map((action, index) => (
            <article className="approval-card" key={`${action.name}-${index}`}>
              <h3>{action.name}</h3>
              <p>{action.description}</p>

              <dl>
                {Object.entries(action.args).map(([name, value]) => (
                  <div key={name}>
                    <dt>{name}</dt>
                    <dd>{formatActionValue(value)}</dd>
                  </div>
                ))}
              </dl>

              <div className="approval-actions">
                {action.allowed_decisions.includes('approve') && (
                  <button
                    type="button"
                    onClick={() => void handleDecision('approve')}
                    disabled={isDeciding}
                  >
                    {isDeciding ? '正在处理……' : '批准执行'}
                  </button>
                )}
                {action.allowed_decisions.includes('reject') && (
                  <button
                    type="button"
                    className="button-secondary"
                    onClick={() => void handleDecision('reject')}
                    disabled={isDeciding}
                  >
                    拒绝执行
                  </button>
                )}
              </div>
            </article>
          ))}
        </section>
      )}
        </div>
      </div>
    </main>
  )
}

export default App
