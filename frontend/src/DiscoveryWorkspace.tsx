import { useEffect, useRef, useState } from 'react'
import { API_BASE_URL } from './api'
import type { PlanningPreferences } from './api'
import { searchMultiPlaces } from './multiStopApi'
import type { MultiRequest, MultiStopPlace } from './multiStopApi'
import { AttractionInfo } from './AttractionInfo'
import { DeviceLocation } from './DeviceLocation'
import { WeatherReportView } from './MultiStopWeather'
import type { WeatherReport } from './MultiStopWeather'
import type { DiscoveryCriteria, DiscoveryHistoryInfo } from './discoveryApi'

export type DiscoveryCandidate = {
  card: { attraction_id: string; name: string; summary?: string | null }
  request: MultiRequest
  totals: { elapsed_s: number; travel_s?: number; stay_s?: number; walking_distance_m: number | null; transfer_count: number | null }
  estimated_transport_and_ticket_yuan: number | null
  reasons: string[]; warnings: string[]
  route_sources?: string[]; route_fetched_at?: string | null
  weather?: WeatherReport | null
}
export type DiscoveryResult = { candidates: DiscoveryCandidate[]; excluded: { attraction_id: string; name: string; reason: string }[]; limitations: string[]; history?: DiscoveryHistoryInfo | null }

export function DiscoveryWorkspace({ onPlan, onStart, onResult, onError, criteria, historyRevision = 0, active = true }: {
  active?: boolean
  onPlan: (request: MultiRequest) => void
  onStart?: (summary: string) => string | void
  onResult?: (result: DiscoveryResult, request: DiscoveryCriteria) => void
  onError?: (message: string) => void
  criteria?: DiscoveryCriteria
  historyRevision?: number
}) {
  const [query, setQuery] = useState('')
  const [origin, setOrigin] = useState<MultiStopPlace | null>(null)
  const [places, setPlaces] = useState<MultiStopPlace[]>([])
  const [departure, setDeparture] = useState('')
  const [minutes, setMinutes] = useState('240')
  const [stay, setStay] = useState('120')
  const [budget, setBudget] = useState('')
  const [walking, setWalking] = useState('')
  const [transfers, setTransfers] = useState('')
  const [driving, setDriving] = useState('false')
  const [priority, setPriority] = useState<PlanningPreferences['priority']>('balanced')
  const [confirmedOpen, setConfirmedOpen] = useState(false)
  const [weatherPolicy, setWeatherPolicy] = useState<'none' | 'inform' | 'exclude_signals'>('none')
  const [busy, setBusy] = useState(false)
  const [searching, setSearching] = useState(false)
  const [locationPending, setLocationPending] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<DiscoveryResult | null>(null)
  const generation = useRef(0)
  const searchGeneration = useRef(0)
  const controller = useRef<AbortController | null>(null)
  useEffect(() => {
    if (!criteria) return
    generation.current++; searchGeneration.current++; controller.current?.abort()
    setQuery(criteria.origin.display_name); setOrigin(criteria.origin); setPlaces([])
    setDeparture(criteria.departure_at ? new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(new Date(criteria.departure_at)).replace(' ', 'T') : '')
    setMinutes(String(criteria.available_minutes)); setStay(String(criteria.stay_minutes))
    setBudget(criteria.budget_yuan == null ? '' : String(criteria.budget_yuan))
    setWalking(criteria.preferences.max_walking_distance_m == null ? '' : String(criteria.preferences.max_walking_distance_m))
    setTransfers(criteria.preferences.max_transfer_count == null ? '' : String(criteria.preferences.max_transfer_count))
    setDriving(criteria.preferences.can_drive == null ? '' : String(criteria.preferences.can_drive))
    setPriority(criteria.preferences.priority); setConfirmedOpen(criteria.require_confirmed_open)
    setWeatherPolicy(criteria.weather_policy); setBusy(false); setSearching(false); setError(''); setResult(null)
  }, [criteria])
  useEffect(() => () => { generation.current++; searchGeneration.current++; controller.current?.abort() }, [])
  function invalidate() {
    generation.current++; controller.current?.abort(); setResult(null); setBusy(false); setError('')
  }
  async function search() {
    invalidate(); setPlaces([]); setOrigin(null)
    const run = ++searchGeneration.current
    if (!query.trim()) { setError('请输入深圳的出发地点。'); return }
    setSearching(true)
    try {
      const data = await searchMultiPlaces(`深圳 ${query.trim()}`)
      if (run !== searchGeneration.current) return
      if (data.provider !== 'amap') throw new Error('unsupported provider')
      setPlaces(data.places)
      if (!data.places.length) setError('未找到出发地点，请换个关键词。')
    } catch { if (run === searchGeneration.current) setError('出发地点搜索失败，请稍后重试。') }
    finally { if (run === searchGeneration.current) setSearching(false) }
  }
  async function recommend() {
    invalidate()
    if (locationPending) { setError('请确认或取消当前定位，再获取推荐。'); return }
    if (!origin) { setError('请点选出发地点。'); return }
    if (!departure && weatherPolicy !== 'none') { setError('按到达时刻查询天气需要出发时间，也可选择不查询天气。'); return }
    const run = generation.current
    const active = new AbortController(); controller.current = active
    const submittedCriteria: DiscoveryCriteria = {
      origin, departure_at: departure ? `${departure}:00+08:00` : null,
      available_minutes: Number(minutes), stay_minutes: Number(stay),
      budget_yuan: budget === '' ? null : Number(budget), require_confirmed_open: confirmedOpen,
      weather_policy: weatherPolicy,
      excluded_attraction_ids: criteria?.excluded_attraction_ids ?? [],
      preferences: { priority, can_drive: driving === '' ? null : driving === 'true', max_walking_distance_m: walking === '' ? null : Number(walking), max_transfer_count: transfers === '' ? null : Number(transfers) },
    }
    setBusy(true)
    const historyThread = onStart?.(`请帮我选目的地：从${origin.display_name}出发并返回，往返共可用 ${minutes} 分钟，游览停留 ${stay} 分钟${budget ? `，交通与普通门票预算 ${budget} 元` : ''}；${departure ? `${departure.replace('T', ' ')} 出发` : '暂未确定出发时间'}。`)
    try {
      const response = await fetch(`${API_BASE_URL}/api/discovery/recommend`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: active.signal,
        body: JSON.stringify({ ...submittedCriteria, ...(historyThread ? { thread_id: historyThread, expected_revision: historyRevision } : {}) }),
      })
      const data = await response.json()
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '请检查可用时间、停留及预算等输入。')
      if (run === generation.current && !active.signal.aborted) { setResult(data as DiscoveryResult); onResult?.(data as DiscoveryResult, submittedCriteria) }
    } catch (cause) { if (run === generation.current && !active.signal.aborted) { const text = cause instanceof Error ? cause.message : '推荐失败，请稍后重试。'; setError(text); onError?.(text) } }
    finally { if (run === generation.current) setBusy(false) }
  }
  return <section className="multi-workspace discovery-form" aria-label="目的地推荐条件">
    <h2>帮我选目的地</h2>
    <p>使用现有三个博物馆馆区，核算从出发点往返一个景点的交通与停留。开放安排需另行确认。</p>
    <label>深圳出发地点<input value={query} onChange={(event) => { invalidate(); searchGeneration.current++; setSearching(false); setQuery(event.target.value); setOrigin(null); setPlaces([]) }} /></label>
    <button type="button" disabled={searching} onClick={() => void search()}>搜索出发地点</button>
    {searching && <p role="status">搜索出发地点中…</p>}
    <ul>{places.map((place, index) => <li key={`${place.provider_place_id}-${index}`}><button type="button" disabled={place.city_code !== '440300' || !place.provider_place_id} onClick={() => { invalidate(); setOrigin(place); setPlaces([]) }}>{place.display_name}{place.city_code !== '440300' || !place.provider_place_id ? '（暂不可选）' : ''}</button></li>)}</ul>
    <DeviceLocation active={active && !busy && !searching} resetKey={`${query}|${origin?.provider_place_id ?? ''}|${origin?.location_captured_at ?? ''}`} onPending={setLocationPending}
      onConfirm={(place) => { invalidate(); searchGeneration.current++; setSearching(false); setPlaces([]); setOrigin(place); setQuery(place.display_name) }} />
    {origin && <p>已确认出发点：{origin.display_name}（{origin.location_source === 'device' ? '设备定位，地址由高德解析' : '地点来源：高德地图'}）</p>}
    <div className="multi-layout"><div>
      <label>出发时间（北京时间）<input type="datetime-local" value={departure} onChange={(event) => { invalidate(); setDeparture(event.target.value) }} /></label>
      <p>出发时间可选，留空仅核算往返耗时。当前比较三个深圳博物馆馆区，暂不提供兴趣或同行者筛选。</p>
      <label>往返总可用分钟<input type="number" min="30" max="720" value={minutes} onChange={(event) => { invalidate(); setMinutes(event.target.value) }} /></label>
      <label>确认游览停留分钟<input type="number" min="15" max="480" value={stay} onChange={(event) => { invalidate(); setStay(event.target.value) }} /></label>
      <label>交通与普通门票预算（元，可选）<input type="number" min="0" step="0.01" value={budget} onChange={(event) => { invalidate(); setBudget(event.target.value) }} /></label>
      <label>往返最多步行米数<input type="number" min="0" value={walking} onChange={(event) => { invalidate(); setWalking(event.target.value) }} /></label>
      <label>往返最多换乘次数<input type="number" min="0" value={transfers} onChange={(event) => { invalidate(); setTransfers(event.target.value) }} /></label>
      <label>目的地推荐驾车条件<select value={driving} onChange={(event) => { invalidate(); setDriving(event.target.value) }}><option value="false">不能驾车</option><option value="true">可以驾车</option><option value="">未确定</option></select></label>
      <label>路线偏好<select value={priority} onChange={(event) => { invalidate(); setPriority(event.target.value as PlanningPreferences['priority']) }}>{Object.entries({ balanced: '综合推荐', fastest: '时间优先', cheapest: '省钱优先', least_walking: '少步行', fewest_transfers: '少换乘', subway_first: '地铁优先' }).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label><input type="checkbox" checked={confirmedOpen} onChange={(event) => { invalidate(); setConfirmedOpen(event.target.checked) }} />仅接受已核实指定日期开放的候选</label>
      <label>本次探索的天气处理<select value={weatherPolicy} onChange={(event) => { invalidate(); setWeatherPolicy(event.target.value as typeof weatherPolicy) }}>
        <option value="none">不查询天气</option><option value="inform">只补充逐站天气提示</option><option value="exclude_signals">排除采样点有提示或天气未知的候选</option>
      </select></label>
      {weatherPolicy !== 'none' && <p>检查本次推荐路线各站的到达时刻，不覆盖全程和全部停留，也不保证安全；天气查询可能增加等待时间。</p>}
      <button type="button" disabled={busy || searching || locationPending} onClick={() => void recommend()}>{busy ? '正在核算往返路线…' : '查找可考虑的景点'}</button>
    </div><div>
      {error && <p role="alert">{error}</p>}
      {result && !onResult && <DiscoveryResults result={result} onPlan={(item) => onPlan(item.request)} />}
    </div></div>
  </section>
}

export function DiscoveryResults({ result, onPlan, disabled = false }: {
  result: DiscoveryResult; onPlan: (candidate: DiscoveryCandidate) => void; disabled?: boolean
}) {
  return <section aria-label="目的地候选">
      <h3>可考虑的目的地</h3><p>先选择目的地，再生成完整往返路线；以下耗时包含交通与停留。</p>
        {!result.candidates.length && <p>暂无能满足当前条件的候选。可查看原因或调整条件；不会自动放宽限制。</p>}
        {result.candidates.map((item) => {
          const stop = item.request.stops[1]
          return <article className="result-card" key={item.card.attraction_id}>
            <h3>{item.card.name}</h3><p>{item.card.summary}</p>
            <p>预计往返总耗时：{Math.round(item.totals.elapsed_s / 60)} 分钟 · 停留 {stop.stay_minutes} 分钟 · 交通约 {Math.round((item.totals.elapsed_s - stop.stay_minutes * 60) / 60)} 分钟</p>
            {item.reasons.map((reason) => <p key={reason}>{reason}</p>)}
            <p>交通与普通门票合计：{item.estimated_transport_and_ticket_yuan == null ? '未知' : `约 ${item.estimated_transport_and_ticket_yuan} 元`}</p>
            <p>往返步行：{item.totals.walking_distance_m == null ? '未知' : `${Math.round(item.totals.walking_distance_m)} 米`} · 换乘：{item.totals.transfer_count == null ? '未知' : `${item.totals.transfer_count} 次`}</p>
            {item.route_sources?.length ? <p><small>路线来源：{item.route_sources.join('；')}{item.route_fetched_at && ` · 查询时间：${new Date(item.route_fetched_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })}`}</small></p> : null}
            {item.warnings.map((warning) => <p key={warning}>{warning}</p>)}
            {item.weather && <details><summary>本次探索的逐站天气</summary><WeatherReportView report={item.weather} /></details>}
            <AttractionInfo place={{ display_name: stop.name, ...stop.point, city_code: stop.city_code, provider_place_id: stop.provider_place_id }} />
            <button type="button" disabled={disabled} onClick={() => onPlan(item)}>规划去这里：{item.card.name}</button>
          </article>
        })}
        {result.excluded.length > 0 && <details><summary>未入选原因</summary>{result.excluded.map((item) => <p key={item.attraction_id}>{item.name}：{item.reason}</p>)}</details>}
        {result.limitations.map((note) => <p key={note}><small>{note}</small></p>)}
  </section>
}
