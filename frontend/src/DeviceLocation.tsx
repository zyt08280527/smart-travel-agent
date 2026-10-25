import { useCallback, useEffect, useRef, useState } from 'react'
import { API_BASE_URL } from './api'
import type { MultiStopPlace } from './multiStopApi'

export function DeviceLocation({ active, resetKey, onConfirm, onPending }: {
  active: boolean; resetKey: string; onConfirm: (place: MultiStopPlace) => void; onPending: (pending: boolean) => void
}) {
  const [busy, setBusy] = useState(false)
  const [pending, setPending] = useState<MultiStopPlace | null>(null)
  const [error, setError] = useState('')
  const generation = useRef(0)
  const controller = useRef<AbortController | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const stop = useCallback(() => {
    generation.current++; controller.current?.abort()
    if (timer.current) clearTimeout(timer.current)
  }, [])
  useEffect(() => {
    stop()
    setBusy(false); setPending(null); setError(''); onPending(false)
  }, [active, resetKey, onPending, stop])
  useEffect(() => stop, [stop])
  function cancel() {
    stop()
    setBusy(false); setPending(null); setError(''); onPending(false)
  }
  function locate() {
    cancel()
    if (!window.isSecureContext) { setError('浏览器定位需要安全连接，请使用本机 localhost 或 HTTPS，也可手动选择出发点。'); return }
    if (!navigator.geolocation) { setError('当前浏览器不支持定位，请手动选择出发点。'); return }
    const run = ++generation.current
    const abort = new AbortController(); controller.current = abort
    setBusy(true); onPending(true)
    function fail(message: string) {
      if (run !== generation.current) return
      generation.current++; abort.abort()
      if (timer.current) clearTimeout(timer.current)
      setBusy(false); onPending(false); setError(message)
    }
    timer.current = setTimeout(() => fail('定位或地址解析超时，请重试或手动选择出发点。'), 25000)
    try {
      navigator.geolocation.getCurrentPosition(async position => {
        if (run !== generation.current) return
        const { latitude, longitude, accuracy } = position.coords
        if (![latitude, longitude, accuracy, position.timestamp].every(Number.isFinite) || accuracy <= 0 || accuracy > 500) {
          fail('定位精度不足（需500米以内），请重试或手动选择出发点。'); return
        }
        try {
          const response = await fetch(`${API_BASE_URL}/api/places/device-location`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: abort.signal,
            body: JSON.stringify({ latitude, longitude, accuracy_m: accuracy, captured_at: new Date(position.timestamp).toISOString() }),
          })
          const data = await response.json()
          if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '定位信息无效，请重新定位或手动选择。')
          if (run !== generation.current) return
          if (timer.current) clearTimeout(timer.current)
          setPending(data); setBusy(false)
        } catch (reason) { fail(reason instanceof Error ? reason.message : '地址解析失败，请手动选择出发点。') }
      }, reason => fail(reason.code === 1 ? '定位授权被拒绝，原起点未改变，可手动选择出发点。' : reason.code === 3 ? '定位超时，请重试或手动选择出发点。' : '暂时无法获取位置，请重试或手动选择出发点。'),
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 })
    } catch { fail('浏览器定位暂时不可用，请手动选择出发点。') }
  }
  return <div aria-label="设备定位">
    <button type="button" disabled={!active || busy} onClick={locate}>{busy ? '正在定位…' : '使用当前位置'}</button>
    <p>经你授权后获取一次位置，并发送到服务端及高德解析地址；确认后才替换起点。</p>
    {(busy || pending) && <button type="button" onClick={cancel}>取消定位</button>}
    {pending && <div role="status">
      <p>{pending.display_name}</p><p>定位精度约{Math.round(pending.location_accuracy_m!)}米 · {new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'short', timeStyle: 'short' }).format(new Date(pending.location_captured_at!))}（北京时间）</p>
      <button type="button" onClick={() => { onConfirm(pending); setPending(null); onPending(false) }}>确认使用此位置</button>
    </div>}
    {error && <p role="alert">{error}</p>}
  </div>
}
