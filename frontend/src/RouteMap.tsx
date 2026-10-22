import { useEffect, useRef, useState } from 'react'

import type { MapPoint } from './api'
import { convertRoute, loadAmap } from './amap'
import type { AmapInstance } from './amap'

type RouteMapProps = {
  geometry: MapPoint[]
  mode: 'driving' | 'walking' | 'transit'
  label: string
}

const MODE_COLORS = { driving: '#2563eb', walking: '#16a34a', transit: '#7c3aed' }

export function RouteMap({ geometry, mode, label }: RouteMapProps) {
  const container = useRef<HTMLDivElement>(null)
  const [status, setStatus] = useState('正在加载地图…')
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    if (geometry.length < 2 || !container.current) return
    const element = container.current
    let cancelled = false
    let map: AmapInstance | undefined
    setStatus('正在加载地图…')

    async function draw() {
      try {
        const sdk = await loadAmap()
        if (cancelled) return
        const positions = await convertRoute(sdk, geometry)
        if (cancelled) return
        map = new sdk.Map(element, {
          center: positions[0], zoom: 13, viewMode: '2D', scrollWheel: false,
        })
        const overlays = [
          new sdk.Polyline({ path: positions, strokeColor: MODE_COLORS[mode], strokeWeight: 6 }),
          new sdk.Marker({ position: positions[0], title: '起点', label: { content: '起点', direction: 'top' } }),
          new sdk.Marker({ position: positions[positions.length - 1], title: '终点', label: { content: '终点', direction: 'top' } }),
        ]
        map.add(overlays)
        map.setFitView(overlays, true, [30, 30, 30, 30], 16)
        setStatus('')
      } catch (error) {
        if (cancelled) return
        map?.destroy()
        map = undefined
        setStatus(error instanceof Error ? error.message : '地图加载失败，请重试。')
      }
    }
    void draw()
    return () => {
      cancelled = true
      map?.destroy()
    }
  }, [geometry, mode, attempt])

  if (geometry.length < 2) return null

  return (
    <div className="route-map" aria-label={`${label}地图`}>
      <div ref={container} className="route-map-canvas" />
      {status && <div className="route-map-status" role="status">
        <span>{status}</span>
        {status !== '正在加载地图…' && <button type="button" onClick={() => setAttempt((value) => value + 1)}>重试地图</button>}
      </div>}
      <p className="route-map-caption">路线轨迹为查询时快照；底图由高德地图提供</p>
    </div>
  )
}
