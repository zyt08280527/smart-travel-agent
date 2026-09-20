import { useEffect, useMemo } from 'react'
import {
  CircleMarker,
  MapContainer,
  Polyline,
  TileLayer,
  Tooltip,
  useMap,
} from 'react-leaflet'
import type { LatLngBoundsExpression, LatLngExpression } from 'leaflet'
import 'leaflet/dist/leaflet.css'

import type { MapPoint } from './api'

type RouteMapProps = {
  geometry: MapPoint[]
  mode: 'driving' | 'walking' | 'transit'
  label: string
}

const MODE_COLORS = {
  driving: '#2563eb',
  walking: '#16a34a',
  transit: '#7c3aed',
}

function FitRouteBounds({ positions }: { positions: LatLngExpression[] }) {
  const map = useMap()

  useEffect(() => {
    if (positions.length < 2) {
      return
    }
    map.fitBounds(positions as LatLngBoundsExpression, {
      padding: [24, 24],
      maxZoom: 16,
    })
  }, [map, positions])

  return null
}

export function RouteMap({ geometry, mode, label }: RouteMapProps) {
  const positions = useMemo<LatLngExpression[]>(
    () => geometry.map((point) => [point.latitude, point.longitude]),
    [geometry],
  )
  if (positions.length < 2) {
    return null
  }

  const color = MODE_COLORS[mode]
  const start = positions[0]
  const end = positions[positions.length - 1]

  return (
    <div className="route-map" aria-label={`${label}地图`}>
      <MapContainer
        center={start}
        zoom={13}
        scrollWheelZoom={false}
        className="route-map-canvas"
      >
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        <Polyline positions={positions} pathOptions={{ color, weight: 6 }} />
        <CircleMarker center={start} radius={7} pathOptions={{ color, fillOpacity: 1 }}>
          <Tooltip permanent direction="top">起点</Tooltip>
        </CircleMarker>
        <CircleMarker center={end} radius={7} pathOptions={{ color, fillOpacity: 1 }}>
          <Tooltip permanent direction="top">终点</Tooltip>
        </CircleMarker>
        <FitRouteBounds positions={positions} />
      </MapContainer>
      <p className="route-map-caption">
        路线轨迹为查询时快照；底图 © OpenStreetMap contributors
      </p>
    </div>
  )
}
