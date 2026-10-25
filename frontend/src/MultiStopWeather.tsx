import { useEffect, useRef, useState } from 'react'
import { API_BASE_URL } from './api'

type StopWeather = {
  stop_id: string; name: string; target_at: string
  status: 'forecast' | 'unavailable' | 'past' | 'out_of_range'
  forecast_at: string | null; temperature_c: number | null; apparent_temperature_c: number | null
  precipitation_mm: number | null; wind_speed_kmh: number | null; condition: string | null
  warnings: string[]; message: string | null
}
export type WeatherReport = { plan_id: string; queried_at: string; stops: StopWeather[]; limitations: string[] }
const time = (value: string) => new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })

export function MultiStopWeather({ draftId, planId }: { draftId: string; planId: string }) {
  // Key the body by both IDs so changing a plan cannot display an old report,
  // even before effect cleanup has aborted its pending HTTP response.
  return <WeatherPanel key={`${draftId}:${planId}`} draftId={draftId} planId={planId} />
}

function WeatherPanel({ draftId, planId }: { draftId: string; planId: string }) {
  const [report, setReport] = useState<WeatherReport | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const controller = useRef<AbortController | null>(null)
  useEffect(() => () => controller.current?.abort(), [])
  async function load() {
    controller.current?.abort()
    const active = new AbortController(); controller.current = active
    setBusy(true); setError(''); setReport(null)
    try {
      const response = await fetch(`${API_BASE_URL}/api/multi-stop/drafts/${encodeURIComponent(draftId)}/weather`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: active.signal,
        body: JSON.stringify({ selected_plan_id: planId }),
      })
      const data = await response.json()
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '天气查询失败，请稍后重试。')
      if (!active.signal.aborted) {
        if (data.plan_id !== planId) throw new Error('天气结果与当前方案不匹配，请重新查询。')
        setReport(data as WeatherReport)
      }
    } catch (cause) { if (!active.signal.aborted) setError(cause instanceof Error ? cause.message : '天气查询失败，请稍后重试。') }
    finally { if (!active.signal.aborted) setBusy(false) }
  }
  return <section aria-label="逐站天气提示">
    <h3>逐站天气提示</h3>
    <p>按当前方案预计到达时刻查询小时预报。提示独立展示，不改变路线排序。</p>
    <button type="button" disabled={busy} onClick={() => void load()}>{busy ? '查询逐站天气中…' : '查询或刷新逐站天气'}</button>
    {error && <p role="alert">{error}</p>}
    {report && <WeatherReportView report={report} />}
  </section>
}

export function WeatherReportView({ report }: { report: WeatherReport }) {
  return <>
      <p><small>来源：<a href="https://open-meteo.com/" target="_blank" rel="noopener noreferrer">Open-Meteo 小时预报</a> · 查询时间 {time(report.queried_at)}（北京时间）</small></p>
      <ol>{report.stops.map((stop) => <li key={stop.stop_id}>
        <strong>{stop.name}</strong><p>预计到达：{time(stop.target_at)}</p>
        {stop.status === 'forecast' ? <>
          <p>匹配预报时刻：{stop.forecast_at ? time(stop.forecast_at) : '未知'} · {stop.condition}</p>
          <p>气温 {stop.temperature_c ?? '未知'} °C · 体感 {stop.apparent_temperature_c ?? '未知'} °C · 风速 {stop.wind_speed_kmh ?? '未知'} km/h</p>
          <p>预报时刻前一小时累计降水：{stop.precipitation_mm ?? '未知'} 毫米</p>
          {stop.warnings.length ? stop.warnings.map((warning) => <p key={warning}>{warning}</p>) : <p>本次数据未触发产品提示阈值，不代表没有天气风险。</p>}
        </> : <p>天气未知：{stop.message ?? '暂无可用预报。'}</p>}
      </li>)}</ol>
      {report.limitations.map((note) => <p key={note}><small>{note}</small></p>)}
    </>
}
