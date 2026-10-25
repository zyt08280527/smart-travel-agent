import { useEffect, useState } from 'react'
import { API_BASE_URL } from './api'
import type { MultiStopPlace } from './multiStopApi'

type Source = { title: string; url: string; checked_at: string }
type Fact = { kind: 'reservation' | 'ticket' | 'opening'; status: 'verified' | 'unknown' | 'expired' | 'conflicting'; value: string | null; scope: string; sources: Source[]; expires_at: string | null }
type Card = { name: string; official_url: string; facts: Fact[]; suggested_stay_minutes: [number, number] | null; stay_note: string; category?: 'museum' | 'park' | 'gallery' | null; summary?: string | null; summary_source?: Source | null }
const labels = { reservation: '预约要求', ticket: '参考票价', opening: '开放信息' }
const safeUrl = (url: string) => /^https:\/\//i.test(url) ? url : undefined

export function AttractionInfo({ place }: { place: MultiStopPlace }) {
  const [result, setResult] = useState<{ key: string; card?: Card; error?: boolean } | null>(null)
  const [open, setOpen] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const key = JSON.stringify(place)
  useEffect(() => {
    if (!open) return
    const controller = new AbortController()
    setResult(null)
    void fetch(`${API_BASE_URL}/api/attractions/lookup`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: key, signal: controller.signal,
    }).then(async (response) => {
      if (!response.ok) throw new Error('lookup failed')
      const payload = await response.json() as { card: Card | null }
      if (!controller.signal.aborted) setResult({ key, card: payload.card ?? undefined })
    }).catch(() => { if (!controller.signal.aborted) setResult({ key, error: true }) })
    return () => controller.abort()
  }, [key, open, attempt])
  const current = result?.key === key ? result : null
  return <details className="attraction-info" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>景点参观信息</summary>
    {open && !current && <p role="status">读取中…</p>}
    {current?.error && <div><p role="alert">参观信息暂时无法读取，请稍后重试。</p><button type="button" onClick={() => setAttempt((value) => value + 1)}>重试参观信息</button></div>}
    {current && !current.error && !current.card && <p>该地点尚未纳入已核验景点库，预约、票价和开放信息待核实。</p>}
    {current?.card && <section aria-label="景点信息卡">
      <h4>{current.card.name}</h4>
      {current.card.category && <small>{{ museum: '博物馆', park: '公园', gallery: '美术馆' }[current.card.category]}</small>}
      {current.card.summary && <p>{current.card.summary}</p>}
      {current.card.summary_source && <p><a href={safeUrl(current.card.summary_source.url)} target="_blank" rel="noopener noreferrer">简介来源：{current.card.summary_source.title}</a> · 查阅日期 {current.card.summary_source.checked_at.slice(0, 10)}</p>}
      {current.card.facts.map((fact) => <div key={fact.kind}>
        <p>{labels[fact.kind]}：{fact.status === 'verified' && fact.value ? fact.value : '待核实'}{fact.status === 'conflicting' ? '（来源信息存在差异）' : fact.status === 'expired' ? '（核验已过期）' : ''}</p>
        <small>{fact.scope}</small>
        {fact.sources.map((source) => <p key={source.url}><a href={safeUrl(source.url)} target="_blank" rel="noopener noreferrer">{source.title}</a> · 查阅日期 {source.checked_at.slice(0, 10)}</p>)}
        {fact.status === 'verified' && fact.expires_at && <small>待复核日期：{new Date(fact.expires_at).toLocaleDateString('zh-CN', { timeZone: 'Asia/Shanghai' })}</small>}
      </div>)}
      {current.card.suggested_stay_minutes && <p>建议游览 {current.card.suggested_stay_minutes.join('–')} 分钟。{current.card.stay_note}</p>}
      <a href={safeUrl(current.card.official_url)} target="_blank" rel="noopener noreferrer">查看官方参观入口</a>
      <p><small>以上为已查阅的参观规则，临时调整请以官方最新通知为准。</small></p>
    </section>}
  </details>
}
