import { useEffect, useState } from 'react'
import { API_BASE_URL } from './api'

type Watch = { id: string; saved_id: string; status: string; checked_at: string | null; message: string }
const labels: Record<string, string> = { pending: '等待检查', checking: '正在检查', completed: '检查完成', failed: '检查未完成', expired: '出发时间已过', cancelled: '已关闭' }

export function DepartureWatch({ savedId, scheduled, single = false, eligible = true }: { savedId: string; scheduled: boolean; single?: boolean; eligible?: boolean }) {
  const [watch, setWatch] = useState<Watch | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [departure, setDeparture] = useState('')
  useEffect(() => {
    let active = true
    async function refresh() {
      try {
        const response = await fetch(`${API_BASE_URL}/api/departure-watches`)
        if (!response.ok) throw new Error('守护状态暂时不可用')
        const rows: Watch[] = await response.json()
        if (active) { setWatch(rows.find(row => row.saved_id === (single ? `single:${savedId}` : savedId)) ?? null); setError('') }
      } catch { if (active) setError('守护状态暂时不可用') }
    }
    void refresh()
    const timer = window.setInterval(() => void refresh(), 30000)
    return () => { active = false; window.clearInterval(timer) }
  }, [savedId, single])
  async function change() {
    setBusy(true); setError('')
    try {
      const cancel = watch && ['pending', 'checking'].includes(watch.status)
      const body = single ? { departure_at: !scheduled && departure ? `${departure}:00+08:00` : null } : undefined
      const response = await fetch(`${API_BASE_URL}${cancel ? `/api/departure-watches/${watch.id}` : single ? `/api/itineraries/${encodeURIComponent(savedId)}/watch` : `/api/multi-stop/saved/${savedId}/watch`}`, { method: cancel ? 'DELETE' : 'POST', ...(!cancel && single ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}) })
      if (!response.ok) {
        const body = await response.json()
        throw new Error(body.detail ?? '操作未完成')
      }
      setWatch(cancel ? { ...watch, status: 'cancelled' } : await response.json())
    } catch (reason) { setError(reason instanceof Error ? reason.message : '操作未完成') }
    finally { setBusy(false) }
  }
  const cancellable = watch && ['pending', 'checking'].includes(watch.status)
  return <div aria-label="出发前守护">
    <p>出发前守护：出发前30分钟检查一次路线和到达采样点天气，结果在此显示。需服务运行；不会自动修改行程。</p>
    {!eligible ? <p>旧记录缺少完整规划信息，请重新规划并保存后开启守护。</p> : !scheduled && !watch && (single ? <label>守护出发时间（北京时间）<input type="datetime-local" value={departure} onChange={event => setDeparture(event.target.value)} /></label> : <p>请先补充出发时间并重新规划保存，再开启守护。</p>)}
    {watch && <p role="status">{labels[watch.status]}{watch.checked_at && ` · ${new Date(watch.checked_at).toLocaleString()}`} {watch.message}</p>}
    {(cancellable || !watch) && <button type="button" disabled={busy || (!cancellable && (!eligible || (!scheduled && (!single || !departure))))} onClick={() => void change()}>{cancellable ? '关闭守护' : '开启出发前守护'}</button>}
    {error && <p role="alert">{error}</p>}
  </div>
}
