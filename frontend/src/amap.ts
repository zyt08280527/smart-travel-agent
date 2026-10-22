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
    result: { info?: string; locations?: { getLng: () => number; getLat: () => number }[] },
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
export async function convertRoute(sdk: AmapSdk, points: MapPoint[]): Promise<Position[]> {
  const positions: Position[] = []
  for (let offset = 0; offset < points.length; offset += 40) {
    const batch = points.slice(offset, offset + 40)
    const converted = await new Promise<Position[]>((resolve, reject) => {
      const timer = window.setTimeout(() => reject(new Error('路线地图转换超时，请重试。')), 10000)
      sdk.convertFrom(batch.map((p) => [p.longitude, p.latitude]), 'gps', (status, result) => {
        clearTimeout(timer)
        if (status !== 'complete' || result.info?.toLowerCase() !== 'ok'
          || result.locations?.length !== batch.length) {
          reject(new Error('路线地图暂时不可用，仍可查看路线详情。'))
          return
        }
        resolve(result.locations.map((p) => [p.getLng(), p.getLat()]))
      })
    })
    positions.push(...converted)
  }
  return positions
}
