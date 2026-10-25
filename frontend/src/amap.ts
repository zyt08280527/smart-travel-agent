import type { MapPoint } from './api'

type Position = [number, number]
export type AmapOverlay = object
export type AmapInstance = {
  add: (overlays: AmapOverlay[]) => void
  setFitView: (overlays: AmapOverlay[], immediately: boolean, padding: number[], maxZoom: number) => void
  destroy: () => void
}
export type AmapSdk = {
  Map: new (container: HTMLElement, options: Record<string, unknown>) => AmapInstance
  Polyline: new (options: Record<string, unknown>) => AmapOverlay
  Marker: new (options: Record<string, unknown>) => AmapOverlay
  convertFrom: (points: Position[], type: 'gps', callback: (
    status: string,
    result: string | { info?: string; locations?: { getLng: () => number; getLat: () => number }[] },
  ) => void) => void
}

declare global {
  interface Window {
    AMap?: AmapSdk
    _AMapSecurityConfig?: { serviceHost?: string; securityJsCode?: string }
    __travelAmapReady?: () => void
  }
}

let loading: Promise<AmapSdk> | undefined

export function loadAmap(): Promise<AmapSdk> {
  if (window.AMap) return Promise.resolve(window.AMap)
  if (loading) return loading
  const key = import.meta.env.VITE_AMAP_JS_KEY?.trim()
  const serviceHost = import.meta.env.VITE_AMAP_SERVICE_HOST?.trim()
  const securityCode = import.meta.env.VITE_AMAP_SECURITY_JS_CODE?.trim()
  if (!key || (!serviceHost && !securityCode)) {
    return Promise.reject(new Error('地图尚未配置，仍可查看路线详情。'))
  }
  window._AMapSecurityConfig = serviceHost
    ? { serviceHost }
    : { securityJsCode: securityCode }

  loading = new Promise<AmapSdk>((resolve, reject) => {
    const script = document.createElement('script')
    const finish = (error?: Error) => {
      clearTimeout(timer)
      delete window.__travelAmapReady
      script.onerror = null
      if (error || !window.AMap) {
        script.remove()
        reject(error ?? new Error('地图加载失败，请稍后重试。'))
      } else {
        resolve(window.AMap)
      }
    }
    const timer = window.setTimeout(() => finish(new Error('地图加载超时，请稍后重试。')), 15000)
    window.__travelAmapReady = () => finish()
    script.onerror = () => finish(new Error('地图加载失败，请检查网络后重试。'))
    script.async = true
    const params = new URLSearchParams({ v: '2.0', key, callback: '__travelAmapReady' })
    script.src = `https://webapi.amap.com/maps?${params}`
    document.head.appendChild(script)
  }).catch((error: unknown) => {
    loading = undefined
    throw error
  })
  return loading
}

// The API and saved v1 routes expose WGS84; AMap renders GCJ-02.
// Official convertFrom accepts at most 40 pairs per request.
type ConversionState = { tail: Promise<void>; nextAt: number; cache: Map<string, Position> }
const conversions = new WeakMap<AmapSdk, ConversionState>()
const pointKey = (point: MapPoint) => `${point.longitude},${point.latitude}`

export async function convertRoute(sdk: AmapSdk, points: MapPoint[]): Promise<Position[]> {
  let state = conversions.get(sdk)
  if (!state) {
    state = { tail: Promise.resolve(), nextAt: 0, cache: new Map() }
    conversions.set(sdk, state)
  }
  const shared = state
  const positions: Position[] = []
  for (let offset = 0; offset < points.length; offset += 40) {
    const batch = points.slice(offset, offset + 40)
    // Share both the queue and cache across all maps/legs. Redrawing a selected
    // leg must not burst many coordinate requests against the same JS key.
    const task = shared.tail.then(async () => {
      const missing = [...new Map(batch.filter((point) => !shared.cache.has(pointKey(point)))
        .map((point) => [pointKey(point), point])).values()]
      if (missing.length) {
        const delay = shared.nextAt - Date.now()
        if (delay > 0) await new Promise<void>((resolve) => window.setTimeout(resolve, delay))
        shared.nextAt = Date.now() + 1100
        const converted = await new Promise<Position[]>((resolve, reject) => {
          const timer = window.setTimeout(() => reject(new Error('路线地图转换超时，请重试。')), 10000)
          sdk.convertFrom(missing.map((p) => [p.longitude, p.latitude]), 'gps', (status, result) => {
            clearTimeout(timer)
            if (typeof result === 'string' || status !== 'complete' || result.info?.toLowerCase() !== 'ok'
              || result.locations?.length !== missing.length) {
              const info = typeof result === 'string' ? result : result.info
              const code = typeof info === 'string' && /^[A-Z][A-Z_]{1,63}$/.test(info)
                ? `（${info}）` : ''
              reject(new Error(`路线地图暂时不可用${code}，仍可查看路线详情。`))
              return
            }
            resolve(result.locations.map((p) => [p.getLng(), p.getLat()]))
          })
        })
        missing.forEach((point, index) => shared.cache.set(pointKey(point), converted[index]))
      }
      const result = batch.map((point) => shared.cache.get(pointKey(point))!)
      // Bound the in-memory cache without storing coordinates in browser storage.
      while (shared.cache.size > 20000) shared.cache.delete(shared.cache.keys().next().value!)
      return result
    })
    shared.tail = task.then(() => undefined, () => undefined)
    const converted = await task
    positions.push(...converted)
  }
  return positions
}
