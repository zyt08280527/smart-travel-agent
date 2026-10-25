import { useCallback, useEffect, useRef, useState } from 'react'
import { API_BASE_URL } from './api'
import type { SavedItinerary } from './api'
import type { SavedMulti } from './multiStopApi'
import { Modal } from './Modal'

type Notice = { id: string; title: string; kind: 'single' | 'multi'; status: string; checked_at: string | null; departure_at: string; message: string; unread: boolean; notification_token: string }
export type NoticeDetail = { kind: 'single'; snapshot: SavedItinerary; replan_prompt: string } | { kind: 'multi'; snapshot: SavedMulti; replan_prompt: string }
const labels: Record<string, string> = { pending: '等待检查或重试', checking: '正在检查', completed: '检查完成', failed: '检查未完成', expired: '出发时间已过', cancelled: '已关闭' }
const modes: Record<string, string> = { walking: '步行', driving: '驾车', transit: '公共交通' }
const time = (value: string) => new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'short', timeStyle: 'short' }).format(new Date(value))
async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, options)
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(typeof body.detail === 'string' ? body.detail : '行程提醒暂时不可用')
  }
  return response.status === 204 ? undefined as T : response.json()
}

function Snapshot({ detail }: { detail: NoticeDetail }) {
  if (detail.kind === 'single') return <section aria-label="原保存行程只读预览">
    <h3>{detail.snapshot.title}</h3><p>{detail.snapshot.origin} → {detail.snapshot.destination}</p>
    <p>{modes[detail.snapshot.travel_mode]} · 保存时预计{Math.round(detail.snapshot.duration_s / 60)}分钟</p>
    {detail.snapshot.notes && <p>{detail.snapshot.notes}</p>}
    <p>这是原保存摘要，未刷新路线，也未改变当前行程。</p>
  </section>
  const saved = detail.snapshot
  const plan = saved.draft.result.selection.plans.find(item => item.plan_id === saved.selected_plan_id)
  return <section aria-label="原保存行程只读预览">
    <h3>{saved.draft.result.request.stops.map(stop => stop.name).join(' → ')}</h3>
    {plan?.evaluation.legs.map((leg, index) => <p key={index}>
      第{index + 1}段：{saved.draft.result.request.stops[index].name} → {saved.draft.result.request.stops[index + 1].name}
      {' · '}{modes[leg.leg.option.mode]} · {Math.round(leg.leg.option.duration_s / 60)}分钟
    </p>)}
    {saved.draft.result.request.stops.filter(stop => stop.stay_minutes).map(stop => <p key={stop.stop_id}>{stop.name}停留{stop.stay_minutes}分钟</p>)}
    <p>这是原保存的选定方案，未刷新路线，也未改变当前行程。</p>
  </section>
}

export function NotificationCenter({ disabled, onReplan }: { disabled: boolean; onReplan: (detail: NoticeDetail) => Promise<void> }) {
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState<Notice[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [preview, setPreview] = useState<NoticeDetail | null>(null)
  const generation = useRef(0)
  const invalidate = useCallback(() => { generation.current++ }, [])
  const disabledRef = useRef(disabled)
  disabledRef.current = disabled
  const refresh = useCallback(async () => {
    const version = ++generation.current
    try {
      const rows = await request<Notice[]>('/api/departure-watches')
      if (!Array.isArray(rows)) throw new Error('提醒数据格式不正确，请刷新重试')
      if (version === generation.current) { setItems(rows); setError('') }
    } catch (reason) { if (version === generation.current) setError(reason instanceof Error ? reason.message : '读取失败') }
  }, [])
  useEffect(() => {
    void refresh()
    const timer = window.setInterval(() => void refresh(), 30000)
    return () => { invalidate(); window.clearInterval(timer) }
  }, [refresh, invalidate])
  async function action(item: Notice, kind: 'read' | 'view' | 'cancel' | 'replan') {
    setBusy(true); setError(''); generation.current++
    try {
      if (kind === 'read') await request(`/api/departure-watches/${item.id}/read`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ notification_token: item.notification_token }) })
      else if (kind === 'cancel') await request(`/api/departure-watches/${item.id}`, { method: 'DELETE' })
      else {
        const detail = await request<NoticeDetail>(`/api/departure-watches/${item.id}`)
        if (kind === 'view') setPreview(detail)
        else {
          if (disabledRef.current) throw new Error('当前对话正在处理中，请稍后重新发起规划')
          setOpen(false); setPreview(null); await onReplan(detail)
        }
      }
      await refresh()
    } catch (reason) { setError(reason instanceof Error ? reason.message : '操作失败') }
    finally { setBusy(false) }
  }
  const unread = items.filter(item => item.unread).length
  return <>
    <button type="button" onClick={() => { setOpen(true); setPreview(null); void refresh() }}>行程提醒{unread > 0 ? `（${unread}条未读）` : ''}{error && !open ? '（读取失败）' : ''}</button>
    <Modal open={open} title="行程提醒" onClose={() => { setOpen(false); setPreview(null) }}>
      <p>北京时间 · 每30秒更新。查看和已读不会修改原行程；重新规划由你主动发起。</p>
      {error && <p role="alert">{error}</p>}
      <button type="button" disabled={busy} onClick={() => void refresh()}>刷新提醒</button>
      {!items.length && !error && <p>暂无守护任务。保存行程后可主动开启出发前守护。</p>}
      {items.map(item => <article key={item.id} aria-label={item.title}>
        <h3>{item.title}{item.unread ? ' · 未读' : ''}</h3>
        <p>{item.kind === 'single' ? '单段行程' : '多站点行程'} · {labels[item.status]} · 出发：{time(item.departure_at)}</p>
        {item.checked_at && <p>检查时间：{time(item.checked_at)}</p>}
        {item.message && <p>{item.message}</p>}
        {item.unread && <button disabled={busy} type="button" onClick={() => void action(item, 'read')}>标为已读</button>}
        <button disabled={busy} type="button" onClick={() => void action(item, 'view')}>查看原行程</button>
        <button disabled={busy || disabled} type="button" onClick={() => void action(item, 'replan')}>发起重新规划</button>
        {['pending', 'checking'].includes(item.status) && <button disabled={busy} type="button" onClick={() => void action(item, 'cancel')}>关闭守护</button>}
      </article>)}
      {preview && <Snapshot detail={preview} />}
    </Modal>
  </>
}
