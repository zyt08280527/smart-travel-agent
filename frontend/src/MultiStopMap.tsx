import { useEffect, useRef, useState } from 'react'
import { convertRoute, loadAmap } from './amap'
import type { AmapInstance } from './amap'
import type { MultiPlan, Stop } from './multiStopApi'

export function MultiStopMap({ plan, stops, activeLeg }: { plan: MultiPlan; stops: Stop[]; activeLeg: number }) {
  const container = useRef<HTMLDivElement>(null)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    let cancelled = false
    let map: AmapInstance | undefined
    setError('正在加载多段地图…')
    async function draw() {
      try {
        const sdk = await loadAmap()
        const points = await convertRoute(sdk, stops.map((stop) => stop.point))
        const paths = await Promise.all(plan.evaluation.legs.map(({ leg }) =>
          leg.option.geometry.length >= 2 ? convertRoute(sdk, leg.option.geometry) : Promise.resolve(null)))
        if (cancelled || !container.current) return
        map = new sdk.Map(container.current, { center: points[0], zoom: 12, viewMode: '2D', scrollWheel: false })
        const overlays = paths.flatMap((path, index) => path ? [new sdk.Polyline({
          path, strokeColor: index === activeLeg ? '#2563eb' : '#94a3b8',
          strokeWeight: index === activeLeg ? 7 : 4, strokeOpacity: index === activeLeg ? 1 : 0.65,
        })] : [])
        points.forEach((point, index) => overlays.push(new sdk.Marker({ position: point,
          title: stops[index].name, label: { content: `第 ${index + 1} 站`, direction: 'top' } })))
        map.add(overlays)
        map.setFitView(overlays, true, [35, 35, 35, 35], 16)
        setError(paths.some((path) => !path) ? '部分路线缺少轨迹，仅展示已有轨迹和站点。' : '')
      } catch (cause) {
        if (!cancelled) setError(cause instanceof Error ? cause.message : '地图加载失败')
        map?.destroy()
        map = undefined
      }
    }
    void draw()
    return () => { cancelled = true; map?.destroy() }
  }, [plan, stops, activeLeg, attempt])
  return <div className="route-map" aria-label="多站点路线地图">
    <div className="route-map-canvas" ref={container} />
    {error && <div className="route-map-status" role="status">{error}
      <button type="button" onClick={() => setAttempt((value) => value + 1)}>重试地图</button>
    </div>}
    <p className="route-map-caption">查询时路线快照 · 高德地图 · 蓝色为当前分段</p>
  </div>
}
