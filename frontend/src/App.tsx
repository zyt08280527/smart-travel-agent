import { useCallback, useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import ReactMarkdown from 'react-markdown'
import {
  decideApproval,
  deleteConversation,
  deleteItinerary,
  getConversation,
  getConversations,
  getHealth,
  getItineraries,
  streamChat,
} from './api'
import type {
  ApprovalDecision,
  ConversationSummary,
  PendingAction,
  PlaceResultCard,
  PlanningResultCard,
  PlanningTransitCandidate,
  ResultCard,
  RouteResultCard,
  SavedItinerary,
  TransitResultCard,
  WeatherResultCard,
} from './api'
import { RouteMap } from './RouteMap'
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

type ReplanComparison = {
  original: SavedItinerary
  current: PlanningResultCard
}

function isLatestRouteChoiceCard(
  messages: ChatMessage[],
  messageIndex: number,
  cardIndex: number,
): boolean {
  for (let index = messageIndex; index < messages.length; index += 1) {
    const cards = messages[index].cards ?? []
    const startIndex = index === messageIndex ? cardIndex + 1 : 0
    if (cards.slice(startIndex).some(
      (card) => card.type === 'planning' || card.type === 'transit',
    )) {
      return false
    }
  }
  return true
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
  list_itineraries: '已保存行程查询',
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

const TRAVEL_MODE_LABELS: Record<SavedItinerary['travel_mode'], string> = {
  driving: '驾车',
  walking: '步行',
  transit: '公共交通',
}

function SavedItineraryItem({
  itinerary,
  onDelete,
  onReplan,
  deleteDisabled = false,
  replanDisabled = false,
}: {
  itinerary: SavedItinerary
  onDelete?: (itinerary: SavedItinerary) => void
  onReplan?: (itinerary: SavedItinerary) => void
  deleteDisabled?: boolean
  replanDisabled?: boolean
}) {
  const [isExpanded, setIsExpanded] = useState(false)
  return (
    <article className="saved-itinerary-item">
      <button
        type="button"
        className="saved-itinerary-summary"
        aria-expanded={isExpanded}
        onClick={() => setIsExpanded((current) => !current)}
      >
        <span className="saved-itinerary-title">
          <strong>{itinerary.title}</strong>
          <span>{TRAVEL_MODE_LABELS[itinerary.travel_mode]}</span>
        </span>
      </button>
      <p>{itinerary.origin} → {itinerary.destination}</p>
      <p>
        {formatDistance(itinerary.distance_m)} ·{' '}
        {formatDuration(itinerary.duration_s)}
      </p>
      <time dateTime={itinerary.saved_at}>
        {formatPlanningDateTime(itinerary.saved_at)} 保存
      </time>
      {isExpanded && (
        <div className="saved-itinerary-details">
          <p>
            时长依据：
            {itinerary.duration_basis === 'traffic_aware_estimate'
              ? '查询时交通状况估计'
              : '静态预计，不含实时路况'}
          </p>
          {itinerary.notes && <p>备注：{itinerary.notes}</p>}
          <div className="saved-itinerary-actions">
            {onReplan && (
              <button
                type="button"
                onClick={() => onReplan(itinerary)}
                disabled={replanDisabled}
              >
                按当前情况重新规划
              </button>
            )}
            {onDelete && (
              <button
                type="button"
                className="saved-itinerary-delete"
                onClick={() => onDelete(itinerary)}
                disabled={deleteDisabled}
              >
                {deleteDisabled ? '正在删除……' : '删除行程'}
              </button>
            )}
          </div>
        </div>
      )}
    </article>
  )
}

function ReplanComparisonCard({ comparison }: { comparison: ReplanComparison }) {
  const { original, current } = comparison
  const currentOption = current.ranked_options.find(
    (option) => option.mode === current.recommended_mode,
  )
  if (!currentOption) {
    return null
  }
  const durationDifference = currentOption.duration_s - original.duration_s
  const differenceLabel = Math.abs(durationDifference) < 30
    ? '预计时长基本不变'
    : durationDifference > 0
      ? `比保存时慢 ${formatDuration(durationDifference)}`
      : `比保存时快 ${formatDuration(Math.abs(durationDifference))}`

  return (
    <section className="replan-comparison" aria-labelledby="replan-title">
      <h2 id="replan-title">重新规划对比</h2>
      <p>{original.origin} → {original.destination}</p>
      <div className="comparison-grid">
        <article>
          <h3>保存时方案</h3>
          <strong>{TRAVEL_MODE_LABELS[original.travel_mode]}</strong>
          <span>{formatDuration(original.duration_s)}</span>
          <span>{formatDistance(original.distance_m)}</span>
        </article>
        <article>
          <h3>当前推荐</h3>
          <strong>{TRAVEL_MODE_LABELS[current.recommended_mode]}</strong>
          <span>{formatDuration(currentOption.duration_s)}</span>
          <span>{differenceLabel}</span>
        </article>
      </div>
      {original.travel_mode !== current.recommended_mode && (
        <p className="comparison-change">
          推荐方式已从 {TRAVEL_MODE_LABELS[original.travel_mode]} 变为{' '}
          {TRAVEL_MODE_LABELS[current.recommended_mode]}。
        </p>
      )}
      <p className="result-card-time">
        当前结果是重新查询后的路线快照，不是持续更新的实时导航。
      </p>
    </section>
  )
}

function ItineraryListCard({ itineraries }: { itineraries: SavedItinerary[] }) {
  return (
    <section className="result-card itinerary-list-card">
      <h4>已保存行程</h4>
      {itineraries.length === 0 ? (
        <p>目前还没有已保存的行程。</p>
      ) : (
        <div className="saved-itinerary-list">
          {itineraries.map((itinerary) => (
            <SavedItineraryItem
              key={itinerary.itinerary_id}
              itinerary={itinerary}
            />
          ))}
        </div>
      )}
    </section>
  )
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

      <RouteMap
        geometry={card.geometry ?? []}
        mode={card.mode}
        label={modeLabel}
      />

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

function TransitCard({
  card,
  onSelectTransitCandidate,
  selectionDisabled,
}: {
  card: TransitResultCard
  onSelectTransitCandidate?: (candidateIndex: number) => void
  selectionDisabled?: boolean
}) {
  const costText =
    card.cost_yuan == null ? '未提供' : `¥${card.cost_yuan.toFixed(2)}`
  const lineNames = card.line_names ?? []
  const lineText =
    lineNames.length > 0
      ? lineNames.join(' → ')
      : '线路名称未提供'
  const selectedCandidate = card.options?.find((candidate) => candidate.selected)
    ?? card.options?.[0]

  return (
    <section className="result-card transit-card" aria-label="公共交通路线">
      <div className="result-card-heading">
        <div>
          <p className="result-card-kicker">路线规划</p>
          <h4>公共交通</h4>
          <p>
            展示第 {(selectedCandidate?.candidate_index ?? 0) + 1} 个方案，
            共 {card.option_count} 个候选
          </p>
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

      <RouteMap
        geometry={selectedCandidate?.geometry ?? []}
        mode="transit"
        label="公共交通路线"
      />

      <div className="transit-lines">
        <span>乘坐线路</span>
        <strong>{lineText}</strong>
      </div>
      {(card.options?.length ?? 0) > 0 && (
        <div className="planning-transit-candidates">
          <div className="planning-transit-heading">
            <strong>公共交通候选</strong>
            <small>共 {card.options?.length} 条具体路线</small>
          </div>
          <ol>
            {card.options?.map((candidate) => (
              <TransitCandidateView
                key={candidate.candidate_index}
                candidate={candidate}
                disabled={selectionDisabled}
                onSelect={onSelectTransitCandidate}
              />
            ))}
          </ol>
        </div>
      )}
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

const TRANSIT_STRATEGY_LABELS = {
  recommended: '综合推荐',
  subway_first: '地铁优先',
  fewest_transfers: '少换乘',
  least_walking: '少步行',
}

const TRANSIT_LEG_LABELS = {
  walking: '步行',
  bus: '公交',
  subway: '地铁',
  railway: '铁路',
  taxi: '出租车',
}

function TransitCandidateView({
  candidate,
  badges = [],
  disabled = false,
  onSelect,
}: {
  candidate: PlanningTransitCandidate
  badges?: string[]
  disabled?: boolean
  onSelect?: (candidateIndex: number) => void
}) {
  const lineNames = candidate.line_names ?? []
  const summary = (
    <>
      <span className="planning-transit-title">
        <strong>候选 {candidate.candidate_index + 1}</strong>
        <span className="planning-transit-badges">
          {badges.map((badge) => <em key={badge}>{badge}</em>)}
          {candidate.selected && <em>当前首选</em>}
        </span>
      </span>
      <span className="planning-transit-lines">
        {lineNames.length > 0
          ? lineNames.join(' → ')
          : '线路信息未提供'}
      </span>
      <small>
        {formatDuration(candidate.duration_s)} · 费用
        {' '}{candidate.cost_yuan != null
          ? `${candidate.cost_yuan.toFixed(0)} 元`
          : '待确认'}
        {' '}· 步行 {formatDistance(candidate.walking_distance_m)}
        {' '}· 换乘 {candidate.transfer_count} 次
      </small>
    </>
  )

  return (
    <li className={candidate.selected ? 'selected' : ''}>
      {onSelect ? (
        <button
          type="button"
          className="planning-transit-option"
          aria-label={`选择公共交通候选 ${candidate.candidate_index + 1}`}
          aria-pressed={candidate.selected}
          disabled={disabled || candidate.selected}
          onClick={() => onSelect(candidate.candidate_index)}
        >
          {summary}
        </button>
      ) : (
        <div className="planning-transit-option planning-transit-option-readonly">
          {summary}
        </div>
      )}
      {(candidate.legs?.length ?? 0) > 0 && (
        <details className="planning-transit-details">
          <summary>查看完整路线</summary>
          <ol aria-label={`候选 ${candidate.candidate_index + 1} 路线步骤`}>
            {candidate.legs?.map((leg, legIndex) => (
              <li key={`${candidate.candidate_index}-${legIndex}`}>
                <span className={`transit-leg-mode ${leg.mode}`}>
                  {TRANSIT_LEG_LABELS[leg.mode]}
                </span>
                <div>
                  <strong>
                    {leg.line_name
                      ?? (leg.mode === 'walking'
                        ? leg.instruction || '步行接驳'
                        : TRANSIT_LEG_LABELS[leg.mode])}
                  </strong>
                  {leg.departure_stop && leg.arrival_stop && (
                    <span>
                      {leg.departure_stop} → {leg.arrival_stop}
                      {leg.via_stop_count != null
                        ? ` · 途经 ${leg.via_stop_count} 站`
                        : ''}
                    </span>
                  )}
                  <small>
                    {formatDistance(leg.distance_m)}
                    {leg.duration_s != null
                      ? ` · ${formatDuration(leg.duration_s)}`
                      : ''}
                  </small>
                </div>
              </li>
            ))}
          </ol>
        </details>
      )}
    </li>
  )
}

const MODE_SELECTION_MESSAGES = {
  driving: '选择驾车方案',
  transit: '选择公共交通方案',
  walking: '选择步行方案',
}

const TRANSIT_STRATEGY_MESSAGES = {
  recommended: '公共交通综合推荐',
  subway_first: '公共交通改为地铁优先',
  fewest_transfers: '公共交通改为少换乘',
  least_walking: '公共交通改为少步行',
}

const PRIORITY_MESSAGES = {
  balanced: '调整为均衡考虑',
  fastest: '调整为优先速度',
  cheapest: '调整为优先省钱',
  least_walking: '调整为优先少步行',
  fewest_transfers: '调整为优先少换乘',
}

type PlanningMode = keyof typeof MODE_LABELS
type PlanningPriority = keyof typeof PRIORITY_LABELS
type TransitStrategy = keyof typeof TRANSIT_STRATEGY_LABELS

function formatPlanningDateTime(value: string): string {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) {
    return value
  }
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(parsed)
}

function PlanningCard({
  card,
  onSelectMode,
  onSelectPriority,
  onSelectTransitStrategy,
  onSelectTransitCandidate,
  onSaveItinerary,
  strategyChangeDisabled,
  saveDisabled,
}: {
  card: PlanningResultCard
  onSelectMode?: (mode: PlanningMode) => void
  onSelectPriority?: (priority: PlanningPriority) => void
  onSelectTransitStrategy?: (strategy: TransitStrategy) => void
  onSelectTransitCandidate?: (candidateIndex: number) => void
  onSaveItinerary?: () => void
  strategyChangeDisabled?: boolean
  saveDisabled?: boolean
}) {
  const unavailableOptions = card.unavailable_options ?? []
  const selectedMode = card.selected_mode ?? null
  const selectedRoute = card.ranked_options.find(
    (option) => option.mode === selectedMode,
  )
  const serverSelectedTransitCandidateIndex =
    card.selected_transit_candidate_index
    ?? card.transit_candidates?.find((candidate) => candidate.selected)
      ?.candidate_index
    ?? null
  const [selectedTransitCandidateIndex, setSelectedTransitCandidateIndex] =
    useState<number | null>(serverSelectedTransitCandidateIndex)

  useEffect(() => {
    setSelectedTransitCandidateIndex(serverSelectedTransitCandidateIndex)
  }, [serverSelectedTransitCandidateIndex])

  const selectedTransitCandidate = card.transit_candidates?.find(
    (candidate) => candidate.candidate_index === selectedTransitCandidateIndex,
  )
  const selectedDuration = selectedMode === 'transit'
    ? selectedTransitCandidate?.duration_s ?? selectedRoute?.duration_s
    : selectedRoute?.duration_s
  const selectedCost = selectedMode === 'transit'
    ? selectedTransitCandidate?.cost_yuan ?? selectedRoute?.cost_yuan
    : selectedRoute?.cost_yuan
  const selectedWalkingDistance = selectedMode === 'transit'
    ? selectedTransitCandidate?.walking_distance_m
      ?? selectedRoute?.walking_distance_m
    : selectedRoute?.walking_distance_m
  const selectedTransferCount = selectedMode === 'transit'
    ? selectedTransitCandidate?.transfer_count ?? selectedRoute?.transfer_count
    : selectedRoute?.transfer_count
  const constraintLabels: string[] = []
  if (card.preferences.can_drive === false) {
    constraintLabels.push('不能驾车')
  } else if (card.preferences.can_drive === true) {
    constraintLabels.push('可以驾车')
  }
  if (card.preferences.max_walking_distance_m != null) {
    constraintLabels.push(
      `最多步行 ${formatDistance(card.preferences.max_walking_distance_m)}`,
    )
  }
  if (card.preferences.max_transfer_count != null) {
    constraintLabels.push(`最多换乘 ${card.preferences.max_transfer_count} 次`)
  }

  const transitCandidates = card.transit_candidates ?? []
  const minimumTransitDuration = transitCandidates.length > 0
    ? Math.min(...transitCandidates.map((candidate) => candidate.duration_s))
    : null
  const pricedTransitCandidates = transitCandidates.filter(
    (candidate) => candidate.cost_yuan != null,
  )
  const minimumTransitCost = pricedTransitCandidates.length > 0
    ? Math.min(...pricedTransitCandidates.map((candidate) => candidate.cost_yuan!))
    : null
  const minimumTransitWalking = transitCandidates.length > 0
    ? Math.min(...transitCandidates.map((candidate) => candidate.walking_distance_m))
    : null
  const minimumTransitTransfers = transitCandidates.length > 0
    ? Math.min(...transitCandidates.map((candidate) => candidate.transfer_count))
    : null
  const selectedTransitGeometry =
    (selectedTransitCandidate?.geometry?.length ?? 0) >= 2
      ? selectedTransitCandidate?.geometry ?? []
      : selectedRoute?.geometry ?? []

  function transitCandidateBadges(candidate: PlanningTransitCandidate): string[] {
    const badges: string[] = []
    if (candidate.duration_s === minimumTransitDuration) {
      badges.push('最快')
    }
    if (candidate.cost_yuan != null && candidate.cost_yuan === minimumTransitCost) {
      badges.push('最省钱')
    }
    if (candidate.walking_distance_m === minimumTransitWalking) {
      badges.push('步行最少')
    }
    if (candidate.transfer_count === minimumTransitTransfers) {
      badges.push('换乘最少')
    }
    return badges
  }

  const previousModeLabel = card.previous_recommended_mode == null
    ? null
    : MODE_LABELS[card.previous_recommended_mode]
  const recommendationChanged = previousModeLabel != null
    && card.previous_recommended_mode !== card.recommended_mode

  return (
    <section className="result-card planning-card" aria-label="综合出行推荐">
      <div className="result-card-heading">
        <div>
          <p className="result-card-kicker">
            {card.used_stale_snapshot
              ? '降级路线建议'
              : card.route_refreshed
              ? '路线已刷新'
              : card.reused_previous_data
                ? '本地重新评分'
                : '综合出行推荐'}
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
            <span>系统推荐</span>
            <strong>{MODE_LABELS[card.recommended_mode]}</strong>
          </>
        )}
      </div>

      {card.arrival_by != null && (
        <div className="planning-arrival" aria-label="到达时间安排">
          <span>目标到达 {formatPlanningDateTime(card.arrival_by)}</span>
          {card.arrival_buffer_minutes != null && (
            <small>已预留 {card.arrival_buffer_minutes} 分钟缓冲</small>
          )}
        </div>
      )}

      <section className="planning-trip-preferences" aria-label="本次出行偏好">
        <div className="planning-preference-group-heading">
          <div>
            <strong>本次出行偏好</strong>
            <small>同时影响出行方式推荐与具体路线排序</small>
          </div>
          <span>当前：{PRIORITY_LABELS[card.preferences.priority]}</span>
        </div>
        <div className="planning-variant-tabs" aria-label="设置本次出行偏好">
          {(Object.entries(PRIORITY_LABELS) as Array<
            [PlanningPriority, string]
          >).map(([priority, label]) => (
            <button
              type="button"
              key={priority}
              className={priority === card.preferences.priority ? 'active' : ''}
              aria-pressed={priority === card.preferences.priority}
              disabled={
                strategyChangeDisabled
                || priority === card.preferences.priority
              }
              onClick={() => onSelectPriority?.(priority)}
            >
              {label}
            </button>
          ))}
        </div>
        {constraintLabels.length > 0 && (
          <div className="planning-preferences" aria-label="本次出行限制">
            {constraintLabels.map((label) => (
              <span key={label}>{label}</span>
            ))}
          </div>
        )}
        <details className="planning-advanced-preferences">
          <summary>高级设置</summary>
          <div>
            <span>公共交通倾向</span>
            <div
              className="planning-transit-strategies"
              aria-label="设置公共交通倾向"
            >
              {(Object.entries(TRANSIT_STRATEGY_LABELS) as Array<
                [TransitStrategy, string]
              >).map(([strategy, label]) => {
                const activeStrategy = card.preferences.transit_strategy
                  ?? 'recommended'
                return (
                  <button
                    type="button"
                    key={strategy}
                    className={strategy === activeStrategy ? 'active' : ''}
                    aria-pressed={strategy === activeStrategy}
                    disabled={strategyChangeDisabled || strategy === activeStrategy}
                    onClick={() => onSelectTransitStrategy?.(strategy)}
                  >
                    {label}
                  </button>
                )
              })}
            </div>
          </div>
        </details>
      </section>

      <div className="planning-step-heading">
        <strong>1. 选择出行方式</strong>
        <small>推荐仅供参考，请确认最终采用的方式</small>
      </div>
      <ol className="planning-ranking planning-mode-selection">
        {card.ranked_options.map((option, index) => (
          <li
            key={option.mode}
            className={selectedMode === option.mode ? 'selected' : ''}
          >
            <strong>
              {index + 1}. {MODE_LABELS[option.mode]}
              {option.mode === card.recommended_mode && <em>系统推荐</em>}
              {option.mode === selectedMode && <em>已选择</em>}
            </strong>
            <span>
              {option.total_score.toFixed(1)} 分 · {formatDuration(option.duration_s)}
            </span>
            {option.latest_departure_at != null && (
              <small className="planning-latest-departure">
                最晚 {formatPlanningDateTime(option.latest_departure_at)} 出发
              </small>
            )}
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
            <button
              type="button"
              aria-label={`选择${MODE_LABELS[option.mode]}方案`}
              disabled={strategyChangeDisabled || selectedMode === option.mode}
              onClick={() => onSelectMode?.(option.mode)}
            >
              {selectedMode === option.mode ? '已选择' : '选择此方式'}
            </button>
          </li>
        ))}
      </ol>

      {selectedMode == null && (
        <p className="planning-selection-hint">
          请先选择一种出行方式，再细化方案并保存。
        </p>
      )}

      {selectedMode === 'transit'
        && (card.transit_candidates?.length ?? 0) > 0 && (
        <div className="planning-transit-candidates">
          <div className="planning-transit-heading">
            <strong>2. 选择具体公共交通路线</strong>
            <small>点击候选即可切换首选路线，地图会同步更新</small>
          </div>
          {selectedTransitCandidate && (
            <div className="planning-selected-route-map">
              <div className="planning-selected-route-label">
                <strong>
                  当前首选：候选 {selectedTransitCandidate.candidate_index + 1}
                </strong>
                <span>
                  {(selectedTransitCandidate.line_names?.length ?? 0) > 0
                    ? selectedTransitCandidate.line_names?.join(' → ')
                    : '公共交通路线'}
                </span>
              </div>
              <RouteMap
                geometry={selectedTransitGeometry}
                mode="transit"
                label={`公共交通候选 ${selectedTransitCandidate.candidate_index + 1}`}
              />
            </div>
          )}
          <ol>
            {card.transit_candidates?.map((candidate) => {
              const displayedCandidate = {
                ...candidate,
                selected:
                  candidate.candidate_index === selectedTransitCandidateIndex,
              }
              return (
                <TransitCandidateView
                  key={candidate.candidate_index}
                  candidate={displayedCandidate}
                  badges={transitCandidateBadges(candidate)}
                  disabled={strategyChangeDisabled}
                  onSelect={(candidateIndex) => {
                    setSelectedTransitCandidateIndex(candidateIndex)
                    onSelectTransitCandidate?.(candidateIndex)
                  }}
                />
              )
            })}
          </ol>
        </div>
      )}

      {selectedMode != null && selectedMode !== 'transit' && (
        <RouteMap
          geometry={selectedRoute?.geometry ?? []}
          mode={selectedMode}
          label={`${MODE_LABELS[selectedMode]}已选路线`}
        />
      )}

      {selectedMode != null
        && onSaveItinerary
        && selectedRoute
        && selectedDuration != null && (
        <div className="planning-save-summary" aria-label="待保存方案">
          <div className="planning-step-heading">
            <strong>
              {selectedMode === 'transit' ? '3' : '2'}. 待保存方案
            </strong>
            <small>审批时会再次展示同一方案</small>
          </div>
          <h5>{MODE_LABELS[selectedMode]}</h5>
          {selectedMode === 'transit' && selectedTransitCandidate && (
            <p>
              候选 {selectedTransitCandidate.candidate_index + 1}
              {(selectedTransitCandidate.line_names?.length ?? 0) > 0
                ? ` · ${selectedTransitCandidate.line_names?.join(' → ')}`
                : ''}
            </p>
          )}
          <p>
            {formatDuration(selectedDuration)}
            {selectedCost != null
              ? ` · 费用 ${selectedCost.toFixed(0)} 元`
              : ''}
            {selectedWalkingDistance != null
              ? ` · 步行 ${formatDistance(selectedWalkingDistance)}`
              : ''}
            {selectedTransferCount != null
              ? ` · 换乘 ${selectedTransferCount} 次`
              : ''}
          </p>
          <div className="planning-save-actions">
            <button
              type="button"
              onClick={onSaveItinerary}
              disabled={saveDisabled}
            >
              保存以上行程
            </button>
            <small>保存前需要你批准执行。</small>
          </div>
        </div>
      )}

      {unavailableOptions.length > 0 && (
        <div className="planning-exclusions">
          <strong>未参与推荐</strong>
          <ul>
            {unavailableOptions.map((option) => (
              <li key={`${option.mode}-${option.reason}`}>
                {MODE_LABELS[option.mode]}：{option.reason}
              </li>
            ))}
          </ul>
        </div>
      )}

      {card.used_stale_snapshot && (
        <p className="planning-stale-warning" role="alert">
          路线刷新失败，当前使用
          {card.route_snapshot_at != null
            ? ` ${formatPlanningDateTime(card.route_snapshot_at)} 的`
            : '上一轮'}
          过期快照重新评分，仅供临时参考。
        </p>
      )}
      {!card.used_stale_snapshot && card.route_refreshed && (
        <p className="result-card-time">
          路线与天气已自动重新查询
          {card.route_snapshot_at != null
            ? `，路线数据更新于 ${formatPlanningDateTime(card.route_snapshot_at)}`
            : ''}
          。
        </p>
      )}
      {!card.used_stale_snapshot
        && !card.route_refreshed
        && card.reused_previous_data && (
        <p className="result-card-time">
          复用
          {card.route_snapshot_at != null
            ? ` ${formatPlanningDateTime(card.route_snapshot_at)} 的`
            : '上一轮'}
          路线与天气快照，本轮未重新请求外部服务。
        </p>
      )}
      {!card.route_refreshed
        && !card.used_stale_snapshot
        && !card.reused_previous_data
        && card.route_snapshot_at != null && (
        <p className="result-card-time">
          路线数据更新于 {formatPlanningDateTime(card.route_snapshot_at)}。
        </p>
      )}
    </section>
  )
}

function ResultCardView({
  card,
  onSelectMode,
  onSelectPriority,
  onSelectTransitStrategy,
  onSelectTransitCandidate,
  onSaveItinerary,
  strategyChangeDisabled,
  saveDisabled,
}: {
  card: ResultCard
  onSelectMode?: (mode: PlanningMode) => void
  onSelectPriority?: (priority: PlanningPriority) => void
  onSelectTransitStrategy?: (strategy: TransitStrategy) => void
  onSelectTransitCandidate?: (candidateIndex: number) => void
  onSaveItinerary?: () => void
  strategyChangeDisabled?: boolean
  saveDisabled?: boolean
}) {
  if (card.type === 'weather') {
    return <WeatherCard card={card} />
  }
  if (card.type === 'route') {
    return <RouteCard card={card} />
  }
  if (card.type === 'transit') {
    return (
      <TransitCard
        card={card}
        onSelectTransitCandidate={onSelectTransitCandidate}
        selectionDisabled={strategyChangeDisabled}
      />
    )
  }
  if (card.type === 'place') {
    return <PlaceCard card={card} />
  }
  if (card.type === 'planning') {
    return (
      <PlanningCard
        card={card}
        onSelectMode={onSelectMode}
        onSelectPriority={onSelectPriority}
        onSelectTransitStrategy={onSelectTransitStrategy}
        onSelectTransitCandidate={onSelectTransitCandidate}
        onSaveItinerary={onSaveItinerary}
        strategyChangeDisabled={strategyChangeDisabled}
        saveDisabled={saveDisabled}
      />
    )
  }
  if (card.type === 'itinerary_list') {
    return <ItineraryListCard itineraries={card.itineraries} />
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
  const [historyExpanded, setHistoryExpanded] = useState(false)
  const [isLoadingHistory, setIsLoadingHistory] = useState(false)
  const [historyError, setHistoryError] = useState('')
  const [itineraries, setItineraries] = useState<SavedItinerary[]>([])
  const [itinerariesExpanded, setItinerariesExpanded] = useState(false)
  const [isLoadingItineraries, setIsLoadingItineraries] = useState(false)
  const [itineraryError, setItineraryError] = useState('')
  const [deletingItineraryId, setDeletingItineraryId] = useState('')
  const [replanningItinerary, setReplanningItinerary] =
    useState<SavedItinerary | null>(null)
  const [replanComparison, setReplanComparison] =
    useState<ReplanComparison | null>(null)

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

  const refreshItineraries = useCallback(async () => {
    setIsLoadingItineraries(true)
    try {
      const savedItineraries = await getItineraries()
      setItineraries(savedItineraries)
      setItineraryError('')
    } catch (error) {
      const errorText = error instanceof Error ? error.message : '未知错误'
      setItineraryError(errorText)
    } finally {
      setIsLoadingItineraries(false)
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
    void refreshItineraries()
  }, [refreshConversations, refreshItineraries])

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
    setReplanningItinerary(null)
    setReplanComparison(null)
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

  async function handleDeleteItinerary(itinerary: SavedItinerary) {
    const confirmed = window.confirm(
      `确定删除已保存行程“${itinerary.title}”吗？\n此操作无法撤销。`,
    )
    if (!confirmed) {
      return
    }

    setDeletingItineraryId(itinerary.itinerary_id)
    setItineraryError('')
    try {
      await deleteItinerary(itinerary.itinerary_id)
      await refreshItineraries()
    } catch (error) {
      const errorText = error instanceof Error ? error.message : '未知错误'
      setItineraryError(errorText)
    } finally {
      setDeletingItineraryId('')
    }
  }

  async function handleReplanItinerary(itinerary: SavedItinerary) {
    handleNewConversation()
    setReplanningItinerary(itinerary)
    const prompt = (
      `请基于当前天气和查询时路线，重新规划从“${itinerary.origin}”到`
      + `“${itinerary.destination}”的出行方案。请比较驾车、步行和公共交通，`
      + '推荐当前更合适的一种方式；这次只重新规划，不要保存行程。'
    )
    await sendUserMessage(prompt, false, itinerary, true)
  }

  async function sendUserMessage(
    rawMessage: string,
    clearComposer = false,
    replanSource: SavedItinerary | null = replanningItinerary,
    forceNewThread = false,
  ) {
    const trimmedMessage = rawMessage.trim()
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
    if (clearComposer) {
      setMessage('')
    }
    setIsSending(true)
    setChatError('')
    setActivityText('Agent 正在分析问题……')
    setPendingActions([])

    try {
      await streamChat(
        trimmedMessage,
        forceNewThread ? undefined : (threadId || undefined),
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
            if (toolName === 'list_itineraries') {
              void refreshItineraries()
            }
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
            const resultCard = streamEvent.card as ResultCard
            if (
              resultCard.type === 'planning'
              && replanSource != null
            ) {
              setReplanComparison({
                original: replanSource,
                current: resultCard,
              })
              setReplanningItinerary(null)
            }
            setMessages((currentMessages) =>
              currentMessages.map((currentMessage) =>
                currentMessage.id === assistantMessageId
                  ? {
                      ...currentMessage,
                      cards: resultCard.type === 'planning'
                        ? [
                            ...(currentMessage.cards ?? []).filter(
                              (card) => card.type !== 'planning',
                            ),
                            resultCard,
                          ]
                        : [
                            ...(currentMessage.cards ?? []),
                            resultCard,
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

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    await sendUserMessage(message, true)
  }

  async function handleDecision(decision: ApprovalDecision) {
    if (!threadId || isDeciding) {
      return
    }

    setIsDeciding(true)
    setChatError('')
    const isItinerarySaveApproval = pendingActions.some(
      (action) => action.name === 'save_itinerary',
    )

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
      if (
        decision === 'approve'
        && result.status === 'completed'
        && isItinerarySaveApproval
      ) {
        await refreshItineraries()
        setItinerariesExpanded(true)
      }
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
          <div className="sidebar-section">
            <div className="history-heading">
              <h2 id="history-title">
                <button
                  type="button"
                  className="sidebar-section-toggle"
                  aria-label={`历史会话（${conversations.length}）${historyExpanded ? '收起' : '展开'}`}
                  aria-expanded={historyExpanded}
                  aria-controls="history-content"
                  onClick={() => setHistoryExpanded((expanded) => !expanded)}
                >
                  <span>历史会话</span>
                  <small>{conversations.length}</small>
                  <span aria-hidden="true">{historyExpanded ? '收起' : '展开'}</span>
                </button>
              </h2>
              {historyExpanded && (
                <button
                  type="button"
                  className="button-secondary history-refresh"
                  onClick={() => void refreshConversations()}
                  disabled={isLoadingHistory}
                >
                  刷新
                </button>
              )}
            </div>

            {historyExpanded && (
              <div id="history-content" className="sidebar-section-body">
                {historyError && (
                  <p role="alert">历史加载失败：{historyError}</p>
                )}
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
              </div>
            )}
          </div>

          <div className="saved-itinerary-panel sidebar-section">
            <div className="history-heading">
              <h2 id="saved-itinerary-title">
                <button
                  type="button"
                  className="sidebar-section-toggle"
                  aria-label={`已保存行程（${itineraries.length}）${itinerariesExpanded ? '收起' : '展开'}`}
                  aria-expanded={itinerariesExpanded}
                  aria-controls="saved-itinerary-content"
                  onClick={() =>
                    setItinerariesExpanded((expanded) => !expanded)}
                >
                  <span>已保存行程</span>
                  <small>{itineraries.length}</small>
                  <span aria-hidden="true">
                    {itinerariesExpanded ? '收起' : '展开'}
                  </span>
                </button>
              </h2>
              {itinerariesExpanded && (
                <button
                  type="button"
                  className="button-secondary history-refresh"
                  onClick={() => void refreshItineraries()}
                  disabled={isLoadingItineraries}
                >
                  刷新
                </button>
              )}
            </div>
            {itinerariesExpanded && (
              <div
                id="saved-itinerary-content"
                className="sidebar-section-body"
              >
                {itineraryError && (
                  <p role="alert">行程加载失败：{itineraryError}</p>
                )}
                {itineraries.length === 0 && !itineraryError && (
                  <p className="history-empty">还没有已保存的行程。</p>
                )}
                <div
                  className="saved-itinerary-list"
                  aria-labelledby="saved-itinerary-title"
                >
                  {itineraries.map((itinerary) => (
                    <SavedItineraryItem
                      key={itinerary.itinerary_id}
                      itinerary={itinerary}
                      onDelete={(selected) =>
                        void handleDeleteItinerary(selected)}
                      onReplan={(selected) =>
                        void handleReplanItinerary(selected)}
                      deleteDisabled={
                        deletingItineraryId === itinerary.itinerary_id
                      }
                      replanDisabled={
                        isSending || isDeciding || pendingActions.length > 0
                      }
                    />
                  ))}
                </div>
              </div>
            )}
          </div>
        </aside>

        <div className="content-column">
      {replanningItinerary && (
        <section className="replan-progress" aria-live="polite">
          <h2>正在重新规划</h2>
          <p>
            正在根据当前天气和路线重新评估“
            {replanningItinerary.title}”。
          </p>
        </section>
      )}
      {replanComparison && (
        <ReplanComparisonCard comparison={replanComparison} />
      )}
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

      <section className="chat-section" aria-labelledby="chat-title">
        <h2 id="chat-title">和 Agent 对话</h2>

        {chatError && <p role="alert">请求失败：{chatError}</p>}

        {activityText && (
          <p className="agent-activity" role="status">
            {activityText}
          </p>
        )}

        <div className="message-list" aria-live="polite">
          {messages.map((chatMessage, messageIndex) => (
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
              {chatMessage.cards?.map((card, index) => {
                const cardIsLatest = !['planning', 'transit'].includes(card.type)
                  || isLatestRouteChoiceCard(messages, messageIndex, index)
                return (
                  <ResultCardView
                    key={`${card.type}-${index}`}
                    card={card}
                    strategyChangeDisabled={
                      isSending
                      || pendingActions.length > 0
                      || !cardIsLatest
                    }
                    onSelectMode={(mode) => {
                      void sendUserMessage(MODE_SELECTION_MESSAGES[mode])
                    }}
                    onSelectPriority={(priority) => {
                      void sendUserMessage(PRIORITY_MESSAGES[priority])
                    }}
                    onSelectTransitStrategy={(strategy) => {
                      void sendUserMessage(TRANSIT_STRATEGY_MESSAGES[strategy])
                    }}
                    onSelectTransitCandidate={(candidateIndex) => {
                      void sendUserMessage(
                        `选择公共交通候选${candidateIndex + 1}`,
                      )
                    }}
                    onSaveItinerary={cardIsLatest ? () => {
                      void sendUserMessage('保存当前选择的行程')
                    } : undefined}
                    saveDisabled={
                      isSending
                      || pendingActions.length > 0
                    }
                  />
                )
              })}
            </article>
          ))}
        </div>

        <form className="chat-composer" onSubmit={handleSubmit}>
          <label htmlFor="message">继续和 Agent 对话</label>
          <div className="chat-composer-row">
            <textarea
              id="message"
              aria-label="输入你的出行问题"
              value={message}
              onChange={(event) => setMessage(event.target.value)}
              placeholder="输入出行问题或补充偏好……"
              rows={3}
              disabled={isSending || isDeciding || pendingActions.length > 0}
            />
            <button
              type="submit"
              aria-label="发送问题"
              disabled={
                isSending || !message.trim() || connectionState !== 'connected'
                || isDeciding
                || pendingActions.length > 0
              }
            >
              {isSending ? 'Agent 正在处理……' : '发送'}
            </button>
          </div>
        </form>

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
