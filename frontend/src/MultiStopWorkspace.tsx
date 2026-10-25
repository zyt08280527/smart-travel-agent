import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { getMultiDraft, listMultiStop, planMultiStop, saveMultiStop, searchMultiPlaces, selectMultiStop } from './multiStopApi'
import type { MultiDraft, MultiRequest, MultiStopPlace, SavedMulti } from './multiStopApi'
import type { PlanningPreferences } from './api'
import { MultiStopMap } from './MultiStopMap'
import { AttractionInfo } from './AttractionInfo'
import { MultiStopWeather } from './MultiStopWeather'
import { DepartureWatch } from './DepartureWatch'
import './MultiStopWorkspace.css'

type Row = { id: string; query: string; place: MultiStopPlace | null; stay: string; earliest: string; latest: string }
type Form = { city: string; departure: string; deadline: string; buffer: string; priority: PlanningPreferences['priority']; driving: string; walking: string; transfers: string; rows: Row[]; transportBudget?: string; elapsedBudget?: string }
const newRow = (): Row => ({ id: crypto.randomUUID(), query: '', place: null, stay: '0', earliest: '', latest: '' })
const emptyForm = (): Form => ({ city: '', departure: '', deadline: '', buffer: '15', priority: 'balanced', driving: '', walking: '', transfers: '', rows: [newRow(), newRow(), newRow()] })
const labels = { balanced: '综合推荐', fastest: '时间优先', cheapest: '省钱优先', least_walking: '少步行', fewest_transfers: '少换乘', subway_first: '地铁优先' }
const iso = (value: string) => `${value}:00+08:00`
const time = (value: string) => new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(new Date(value))
function localInput(value?: string | null): string {
  if (!value) return ''
  return new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(new Date(value)).replace(' ', 'T')
}
function fromRequest(request: MultiRequest): Form {
  return { city: request.city_name ?? '', departure: localInput(request.departure_time?.departure_at), deadline: localInput(request.arrival_deadline?.arrival_by), buffer: String(request.arrival_deadline?.buffer_minutes ?? 15), priority: request.preferences.priority, driving: request.preferences.can_drive == null ? '' : String(request.preferences.can_drive), walking: request.preferences.max_walking_distance_m == null ? '' : String(request.preferences.max_walking_distance_m), transfers: request.preferences.max_transfer_count == null ? '' : String(request.preferences.max_transfer_count), rows: request.stops.map((stop) => ({ id: stop.stop_id, query: stop.name, place: { display_name: stop.name, ...stop.point, city_code: stop.city_code, provider_place_id: stop.provider_place_id }, stay: String(stop.stay_minutes), earliest: localInput(stop.earliest_arrival_at), latest: localInput(stop.latest_arrival_at) })) }
}

function PlacePicker({ row, city, index, disabled, onChange }: { row: Row; city: string; index: number; disabled: boolean; onChange: (row: Row) => void }) {
  const [choices, setChoices] = useState<MultiStopPlace[]>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const generation = useRef(0)
  useEffect(() => { generation.current++; setChoices([]); setLoading(false); setError('') }, [city, row.id, row.query, row.place])
  useEffect(() => () => { generation.current++ }, [])
  async function search() {
    if (!city.trim() || !row.query.trim()) { setError('请填写城市和地点关键词。'); return }
    const run = ++generation.current
    setLoading(true); setError(''); setChoices([])
    try {
      const result = await searchMultiPlaces(`${city.trim()} ${row.query.trim()}`)
      if (run !== generation.current) return
      setChoices(result.places)
      if (!result.places.length) setError('未找到地点，请换个关键词。')
    } catch (cause) {
      if (run === generation.current) setError(cause instanceof Error ? cause.message : '搜索失败')
    } finally { if (run === generation.current) setLoading(false) }
  }
  return <div className="multi-place-picker">
    <label>第 {index + 1} 站地点<input value={row.query} disabled={disabled} onChange={(event) => {
      generation.current++; setChoices([]); setLoading(false); setError('')
      onChange({ ...row, query: event.target.value, place: null })
    }} /></label>
    <button type="button" disabled={disabled || loading} onClick={() => void search()}>搜索第 {index + 1} 站</button>
    {row.place && <><p>已确认：{row.place.display_name}</p><AttractionInfo place={row.place} /></>}
    {loading && <p role="status">搜索中…</p>}
    {error && <p role="alert">{error}</p>}
    {choices.length > 0 && <div className="multi-place-results">{choices.map((place, candidate) => <button key={candidate} type="button" disabled={disabled || !place.city_code} onClick={() => {
      generation.current++; setChoices([]); onChange({ ...row, query: place.display_name, place })
    }}>{place.display_name}{!place.city_code && '（城市信息缺失，暂不可选）'}</button>)}<small>地点数据来源：高德地图</small></div>}
  </div>
}

function fromBudgetedRequest(request: MultiRequest): Form {
  return { ...fromRequest(request), elapsedBudget: request.max_elapsed_minutes == null ? '' : String(request.max_elapsed_minutes), transportBudget: request.max_transport_cost_yuan == null ? '' : String(request.max_transport_cost_yuan) }
}

export function MultiStopWorkspace({ initialDraftId, initialRequest, embedded = false, externalDisabled = false, onOpenDraft, onContextChange, initialPlanId }: { initialDraftId?: string | null; initialPlanId?: string | null; initialRequest?: MultiRequest | null; embedded?: boolean; externalDisabled?: boolean; onOpenDraft?: (draft: MultiDraft) => void; onContextChange?: (context: { draft_id: string; plan_id: string }) => void }) {
  const storageKey = embedded ? `multi-stop-result:${initialDraftId}` : initialRequest ? 'discovery-multi-stop-workspace' : 'multi-stop-workspace'
  const preferredPlan = useRef(initialPlanId)
  preferredPlan.current = initialPlanId
  const [editing, setEditing] = useState(!embedded)
  const [restoredChoice] = useState(() => {
    if (!embedded) return null
    try { return JSON.parse(sessionStorage.getItem(storageKey) ?? 'null')?.selected as string | null }
    catch { return null }
  })
  const [initial] = useState(() => {
    if (initialRequest || embedded) return null
    try { return JSON.parse(sessionStorage.getItem('multi-stop-workspace') ?? 'null') as { form: Form; draft: MultiDraft | null; selected: string; planned: string } | null } catch { return null }
  })
  const [form, setForm] = useState<Form>(() => initialRequest ? fromBudgetedRequest(initialRequest) : initial?.form?.rows?.length ? initial.form : emptyForm())
  const [draft, setDraft] = useState<MultiDraft | null>(initial?.draft ?? null)
  const [selected, setSelected] = useState(initial?.selected ?? '')
  const [planned, setPlanned] = useState(initial?.planned ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [saved, setSaved] = useState<SavedMulti[]>([])
  const [savedError, setSavedError] = useState('')
  const [activeLeg, setActiveLeg] = useState(0)
  const contextCallback = useRef(onContextChange)
  contextCallback.current = onContextChange
  const openCallback = useRef(onOpenDraft)
  openCallback.current = onOpenDraft
  useEffect(() => { if (draft && selected) contextCallback.current?.({ draft_id: draft.draft_id, plan_id: selected }) }, [draft, selected])
  const alive = useRef(true)
  const generation = useRef(0)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  useEffect(() => { sessionStorage.setItem(storageKey, JSON.stringify({ form, draft, selected, planned })) }, [form, draft, selected, planned, storageKey])
  useEffect(() => {
    let cancelled = false
    void listMultiStop().then((items) => { if (!cancelled) setSaved(items) }).catch(() => { if (!cancelled) setSavedError('已保存的多站点行程读取失败，可稍后刷新。') })
    return () => { cancelled = true }
  }, [])
  const openDraft = useCallback((value: MultiDraft, planId?: string) => {
    const restored = fromBudgetedRequest(value.result.request)
    setDraft(value); setForm(restored); setPlanned(JSON.stringify(restored)); setActiveLeg(0)
    setSelected(planId ?? value.result.selection.selected_plan_id ?? value.result.selection.recommended_plan_id ?? '')
    setError(''); setNotice('当前查看的是已生成的路线快照，重新规划才会刷新路线。')
    openCallback.current?.(value)
  }, [])
  useEffect(() => {
    if (!initialDraftId) return
    generation.current++
    let cancelled = false
    setBusy(true)
    void getMultiDraft(initialDraftId).then((value) => { if (!cancelled) openDraft(value, [preferredPlan.current, restoredChoice].find((id) => id && value.result.selection.plans.some((plan) => plan.plan_id === id)) ?? undefined) }).catch(() => { if (!cancelled) setError('草案读取失败，请重新规划。') }).finally(() => { if (!cancelled) setBusy(false) })
    return () => { cancelled = true }
  }, [initialDraftId, restoredChoice, openDraft])
  const dirty = draft != null && planned !== JSON.stringify(form)
  const current = draft?.result.selection.plans.find((plan) => plan.plan_id === selected)
  const setField = <K extends keyof Form>(key: K, value: Form[K]) => setForm((previous) => ({ ...previous, [key]: value }))
  function updateRow(index: number, row: Row) { setForm((previous) => ({ ...previous, rows: previous.rows.map((value, number) => number === index ? row : value) })) }
  async function plan(event: FormEvent) {
    event.preventDefault(); setError(''); setNotice('')
    if (form.rows.some((row) => !row.place?.city_code)) { setError('请搜索并逐一确认所有地点。'); return }
    const code = form.rows[0].place!.city_code!
    if (form.rows.some((row) => row.place!.city_code !== code)) { setError('首版仅支持同城行程，请确认所有地点在同一城市。'); return }
    const body: MultiRequest = {
      city_code: code, city_name: form.city || null,
      max_elapsed_minutes: form.elapsedBudget ? Number(form.elapsedBudget) : null,
      max_transport_cost_yuan: form.transportBudget == null || form.transportBudget === '' ? null : Number(form.transportBudget),
      departure_time: form.departure ? { departure_at: iso(form.departure), timezone: 'Asia/Shanghai', precision: 'exact', source_text: form.departure } : null,
      arrival_deadline: form.deadline ? { arrival_by: iso(form.deadline), timezone: 'Asia/Shanghai', precision: 'exact', source_text: form.deadline, buffer_minutes: Number(form.buffer) } : null,
      preferences: { priority: form.priority, can_drive: form.driving === '' ? null : form.driving === 'true', max_walking_distance_m: form.walking === '' ? null : Number(form.walking), max_transfer_count: form.transfers === '' ? null : Number(form.transfers) },
      stops: form.rows.map((row, index) => ({ stop_id: row.id, name: row.place!.display_name, city_code: code, point: { latitude: row.place!.latitude, longitude: row.place!.longitude }, provider_place_id: row.place!.provider_place_id, stay_minutes: index > 0 && index < form.rows.length - 1 ? Number(row.stay) : 0, earliest_arrival_at: row.earliest ? iso(row.earliest) : null, latest_arrival_at: row.latest ? iso(row.latest) : null })),
    }
    if (externalDisabled) return
    const run = ++generation.current
    setBusy(true)
    try {
      const result = await planMultiStop(body)
      if (!alive.current || run !== generation.current) return
      setDraft(result); setSelected(result.result.selection.recommended_plan_id ?? ''); setPlanned(JSON.stringify(form)); setActiveLeg(0)
      onOpenDraft?.(result)
    } catch (cause) { if (alive.current && run === generation.current) setError(cause instanceof Error ? cause.message : '规划失败，原方案仍保留。') }
    finally { if (alive.current && run === generation.current) setBusy(false) }
  }
  async function save() {
    if (!draft || !current || dirty || busy || externalDisabled) return
    const arrival = current.evaluation.stops.at(-1)?.arrival_at
    if (!window.confirm(`确认保存当前选择的完整行程？\n${draft.result.request.stops.map((stop) => stop.name).join(' → ')}\n${arrival ? `预计到达：${time(arrival)}` : `预计总耗时：${Math.round(current.evaluation.totals.elapsed_s / 60)} 分钟（未指定出发时间）`}`)) return
    const run = ++generation.current
    setBusy(true); setError('')
    try {
      const record = await saveMultiStop(draft.draft_id, selected)
      if (!alive.current || run !== generation.current) return
      setSaved((items) => [record, ...items.filter((item) => item.itinerary_id !== record.itinerary_id)])
      setNotice('已保存完整行程，可从下方“已保存多站点行程”重新打开。')
    } catch (cause) { if (alive.current && run === generation.current) setError(cause instanceof Error ? cause.message : '保存失败') }
    finally { if (alive.current && run === generation.current) setBusy(false) }
  }
  async function changeLeg(index: number, candidateId: string) {
    if (!draft || !current || busy || dirty || externalDisabled) return
    const ids = current.evaluation.legs.map((item, position) => position === index ? candidateId : item.leg.candidate_id)
    const run = ++generation.current
    setBusy(true); setError(''); setNotice('')
    try {
      const value = await selectMultiStop(draft.draft_id, ids)
      if (!alive.current || run !== generation.current) return
      setDraft(value); setSelected(value.result.selection.selected_plan_id ?? value.result.selection.recommended_plan_id ?? '')
      setNotice(`已切换第 ${index + 1} 段，后续时间线和全程限制已重新校验。`)
    } catch (cause) { if (alive.current && run === generation.current) setError(cause instanceof Error ? cause.message : '分段切换失败，原方案仍保留。') }
    finally { if (alive.current && run === generation.current) setBusy(false) }
  }
  return <section className={`multi-workspace${embedded ? ' multi-embedded' : ''}`} aria-label="完整多站点行程">
    <header><h2>多站点行程</h2><p>同城、固定顺序，支持 1–3 个中间点。所有时间均为北京时间。</p></header>
    {embedded && <button type="button" disabled={busy || externalDisabled} onClick={() => setEditing((value) => !value)}>{editing ? '收起行程条件' : '编辑行程条件'}</button>}
    <div className="multi-layout"><form hidden={!editing} onSubmit={(event) => void plan(event)}>
      <fieldset disabled={busy || externalDisabled}><legend>地点和时间</legend>
        <label>搜索城市<input value={form.city} placeholder="例如深圳" onChange={(event) => setForm((previous) => ({ ...previous, city: event.target.value, rows: previous.rows.map((row) => ({ ...row, place: null })) }))} /></label>
        <label>出发时间<input type="datetime-local" value={form.departure} onChange={(event) => setField('departure', event.target.value)} /></label>
        <p>出发时间可选；留空只规划路线和预计耗时。校验到达时间窗或截止时需填写。</p>
        {form.rows.map((row, index) => <div className="multi-stop-editor" key={row.id}>
          <h3>{index === 0 ? '起点' : index === form.rows.length - 1 ? '终点' : `中间点 ${index}`}</h3>
          <PlacePicker row={row} city={form.city} index={index} disabled={busy} onChange={(value) => updateRow(index, value)} />
          {index > 0 && index < form.rows.length - 1 && <>
            <label>第 {index + 1} 站停留分钟<input type="number" min="0" max="1440" required value={row.stay} onChange={(event) => updateRow(index, { ...row, stay: event.target.value })} /></label>
            <label>第 {index + 1} 站最早到达<input type="datetime-local" value={row.earliest} onChange={(event) => updateRow(index, { ...row, earliest: event.target.value })} /></label>
            <label>第 {index + 1} 站最晚到达<input type="datetime-local" value={row.latest} onChange={(event) => updateRow(index, { ...row, latest: event.target.value })} /></label>
            {form.rows.length > 3 && <button type="button" onClick={() => setField('rows', form.rows.filter((_, position) => position !== index))}>删除中间点 {index}</button>}
          </>}
        </div>)}
        <button type="button" disabled={form.rows.length >= 5} onClick={() => setField('rows', [...form.rows.slice(0, -1), newRow(), form.rows.at(-1)!])}>添加中间点</button>
        <label>终点最晚到达（可选）<input type="datetime-local" value={form.deadline} onChange={(event) => setField('deadline', event.target.value)} /></label>
        <label>到达缓冲分钟<input type="number" min="0" max="180" required value={form.buffer} onChange={(event) => setField('buffer', event.target.value)} /></label>
        <p>提前到站会等待；最晚到达限制开始停留，不代表必须在该时间前离开。</p>
      </fieldset>
      <fieldset disabled={busy || externalDisabled}><legend>本次偏好和限制</legend>
        <label>出行偏好<select value={form.priority} onChange={(event) => setField('priority', event.target.value as Form['priority'])}>{Object.entries(labels).map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select></label>
        <label>驾车条件<select value={form.driving} onChange={(event) => setField('driving', event.target.value)}><option value="">未指定</option><option value="true">可以驾车</option><option value="false">不能驾车</option></select></label>
        <label>全程最多步行米数<input type="number" min="0" value={form.walking} onChange={(event) => setField('walking', event.target.value)} /></label>
        <label>全程最多换乘次数<input type="number" min="0" step="1" value={form.transfers} onChange={(event) => setField('transfers', event.target.value)} /></label>
        <label>全程交通费用上限（元，可选）<input type="number" min="0" step="0.01" value={form.transportBudget ?? ''} onChange={(event) => setField('transportBudget', event.target.value)} /></label>
        <label>全程总可用分钟（可选）<input type="number" min="1" max="10080" value={form.elapsedBudget ?? ''} onChange={(event) => setField('elapsedBudget', event.target.value)} /></label>
        {initialRequest && <p>从“去哪玩”带入的交通预算已扣除筛选时的普通门票估计；不含餐饮、停车等费用，门票变化请核实后调整预算。</p>}
      </fieldset>
      <button type="submit" disabled={busy || externalDisabled}>{busy ? '正在处理…' : draft ? '重新规划完整行程' : '规划完整行程'}</button>
    </form><div className="multi-results" aria-live="polite">
      {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
      {dirty && <p>设置已修改，请重新规划后再保存；下方仍为上一个方案。</p>}
      {busy && !draft && <p role="status">正在读取完整多站点方案…</p>}
      {!draft && <p>确认地点和时间后，整段时间线、候选方案及地图会在这里展示。</p>}
      {draft && <>
        <h3>完整行程方案</h3>
        {draft.result.selection.status !== 'feasible' && <p role="alert">{draft.result.selection.status === 'infeasible' ? '没有满足当前限制的完整方案，请调整时间或限制。' : '路线或指标数据不足，暂时无法确认完整行程可行。'}</p>}
        {draft.result.selection.issue_examples.length > 0 && (draft.result.selection.status === 'feasible'
          ? <details><summary>其他候选未采用的原因</summary>{draft.result.selection.issue_examples.map((issue, index) => <p key={index}>{issue.message}</p>)}</details>
          : draft.result.selection.issue_examples.map((issue, index) => <p key={index}>{issue.message}</p>))}
        {draft.result.selection.plans.map((plan, index) => <button type="button" disabled={busy || externalDisabled} key={plan.plan_id} aria-pressed={selected === plan.plan_id} onClick={() => { setSelected(plan.plan_id); setActiveLeg(0) }}>
          方案 {index + 1} · {Math.round(plan.evaluation.totals.elapsed_s / 60)} 分钟 · {plan.evaluation.totals.total_cost_yuan == null ? '总费用未知' : `${plan.evaluation.totals.total_cost_yuan} 元`}
          {plan.plan_id === draft.result.selection.recommended_plan_id && ' · 系统推荐'}{plan.plan_id === selected && ' · 当前选择'}
        </button>)}
        {current && <>
          <h3>行程概览</h3>
          <p>{draft.result.request.stops.map((stop) => stop.name).join(' → ')} · 共 {current.evaluation.legs.length} 段</p>
          <p>全程步行：{current.evaluation.totals.walking_distance_m == null ? '未知' : `${Math.round(current.evaluation.totals.walking_distance_m)} 米`} · 换乘：{current.evaluation.totals.transfer_count == null ? '未知' : `${current.evaluation.totals.transfer_count} 次`}</p>
          <p>等待 {Math.round(current.evaluation.totals.waiting_s / 60)} 分钟 · 停留 {Math.round(current.evaluation.totals.stay_s / 60)} 分钟{current.evaluation.totals.total_cost_yuan == null && ` · 已知费用小计 ${current.evaluation.totals.known_cost_subtotal_yuan} 元（不是总费用）`}</p>
          {!draft.result.request.departure_time && <p>未指定出发时间，仅展示预计耗时；公共交通按当前查询快照参考，不校验到达时间或截止。</p>}
          <h3>{draft.result.request.departure_time ? '当前选择的时间线' : '当前选择的行程顺序与停留'}</h3><ol className="multi-timeline">{current.evaluation.stops.map((stop, index) => <li key={stop.stop_id}>
            <strong>{index + 1}. {stop.name}</strong>{stop.physical_arrival_at && <p>到达 {time(stop.physical_arrival_at)}{stop.waiting_s > 0 && `，等待 ${Math.round(stop.waiting_s / 60)} 分钟`}</p>}
            {stop.stay_s > 0 && <p>停留 {stop.stay_s / 60} 分钟{stop.departure_at && `，${time(stop.departure_at)} 离开`}</p>}
          </li>)}</ol>
          <h3>分段路线</h3>{current.evaluation.legs.map(({ leg, departure_at, arrival_at }, index) => <div key={index} className="multi-leg">
            <h4>第 {index + 1} 段：{draft.result.request.stops.find((stop) => stop.stop_id === leg.origin_stop_id)?.name} → {draft.result.request.stops.find((stop) => stop.stop_id === leg.destination_stop_id)?.name}</h4>
            {draft.result.segments?.[index] && <label>第 {index + 1} 段路线选择<select value={leg.candidate_id} disabled={busy || dirty || externalDisabled} onChange={(event) => void changeLeg(index, event.target.value)}>
              {draft.result.segments[index].candidates.filter((candidate) => candidate.option.status !== 'failed' && candidate.option.status !== 'unavailable').map((candidate, number) => <option key={candidate.candidate_id} value={candidate.candidate_id}>
                {{ driving: '驾车', walking: '步行', transit: '公共交通' }[candidate.option.mode]} · {Math.round(candidate.option.duration_s / 60)} 分钟 · {candidate.transit_details?.legs.map((part) => part.line_name).filter(Boolean).join(' / ') || `候选 ${number + 1}`}
              </option>)}
            </select></label>}
            <button type="button" aria-pressed={activeLeg === index} onClick={() => setActiveLeg(index)}>第 {index + 1} 段 · {{ driving: '驾车', transit: '公共交通', walking: '步行' }[leg.option.mode]} · {departure_at && arrival_at ? `${time(departure_at)}—${time(arrival_at)}` : `预计 ${Math.round(leg.option.duration_s / 60)} 分钟`}</button>
            <p>用时 {Math.round(leg.option.duration_s / 60)} 分钟 · 费用 {leg.option.cost_yuan == null ? '未知' : `${leg.option.cost_yuan} 元`} · 步行 {leg.option.walking_distance_m == null ? '未知' : `${leg.option.walking_distance_m} 米`}</p>
            <p>{leg.option.attribution} · 查询于 {time(leg.fetched_at)}</p>
              {leg.transit_details?.legs.map((item, number) => <p key={number}>{item.line_name ?? item.instruction ?? '接驳交通段'}</p>)}
              {!!leg.route_details?.steps.length && <details><summary>查看第 {index + 1} 段导航步骤</summary>{leg.route_details.steps.map((step, number) => step.instruction && <p key={number}>{step.instruction}</p>)}</details>}
          </div>)}
          <MultiStopMap plan={current} stops={draft.result.request.stops} activeLeg={activeLeg} />
          {!dirty && !busy && draft.result.request.departure_time && <MultiStopWeather draftId={draft.draft_id} planId={current.plan_id} />}
          {[...new Set([...draft.result.selection.limitations, ...current.evaluation.limitations])].map((text) => <p key={text} className="multi-limitation">{text}</p>)}
          <button type="button" disabled={busy || dirty || externalDisabled} onClick={() => void save()}>确认保存当前完整行程</button>
        </>}
      </>}
      <section aria-label="已保存多站点行程"><h3>已保存多站点行程</h3>
        {savedError && <p role="alert">{savedError}</p>}
        {!saved.length && !savedError && <p>暂无已保存的多站点行程。</p>}
        {saved.map((record) => <div key={record.itinerary_id}><button type="button" disabled={busy || externalDisabled} onClick={() => openDraft(record.draft, record.selected_plan_id)}>
          重新打开：{record.draft.result.request.stops.map((stop) => stop.name).join(' → ')} · {time(record.saved_at)}
        </button><DepartureWatch savedId={record.itinerary_id} scheduled={!!record.draft.result.request.departure_time} /></div>)}
      </section>
    </div></div>
  </section>
}
