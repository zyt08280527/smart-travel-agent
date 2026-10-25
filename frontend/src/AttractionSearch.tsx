import { useEffect, useRef, useState } from 'react'
import { AttractionInfo } from './AttractionInfo'
import { searchMultiPlaces } from './multiStopApi'
import type { MultiStopPlace } from './multiStopApi'

export function AttractionSearch({ destination }: { destination: string }) {
  return <DestinationSearch key={destination} destination={destination} />
}

function DestinationSearch({ destination }: { destination: string }) {
  const [query, setQuery] = useState(destination)
  const [choices, setChoices] = useState<MultiStopPlace[]>([])
  const [selected, setSelected] = useState<MultiStopPlace | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [attribution, setAttribution] = useState('')
  const generation = useRef(0)
  useEffect(() => () => { generation.current++ }, [])
  async function search() {
    const run = ++generation.current
    setSelected(null); setChoices([]); setError(''); setAttribution('')
    if (!query.trim()) { setError('请输入城市及具体景点名称。'); return }
    setLoading(true)
    try {
      const result = await searchMultiPlaces(query.trim())
      if (run !== generation.current) return
      if (result.provider !== 'amap') { setError('当前搜索来源暂不支持景点库匹配。'); return }
      setChoices(result.places); setAttribution(result.attribution)
      if (!result.places.length) setError('未找到地点，请补充城市或馆区名称。')
    } catch {
      if (run === generation.current) setError('地点查询失败，请稍后重试。')
    } finally { if (run === generation.current) setLoading(false) }
  }
  return <details className="attraction-info">
    <summary>查询目的地参观信息</summary>
    <p>搜索并点选要查看的具体馆区。此处选择仅用于参观信息查询，路线目的地沿用原方案。</p>
    <label>景点城市及名称<input value={query} onChange={(event) => {
      generation.current++; setQuery(event.target.value); setChoices([]); setSelected(null); setLoading(false); setError(''); setAttribution('')
    }} /></label>
    <button type="button" disabled={loading} onClick={() => void search()}>搜索参观地点</button>
    {loading && <p role="status">地点查询中…</p>}
    {error && <p role="alert">{error}</p>}
    {choices.length > 0 && <ul>{choices.map((place, index) => <li key={`${place.provider_place_id}-${index}`}>
      <button type="button" disabled={!place.city_code || !place.provider_place_id} onClick={() => { setSelected(place); setChoices([]) }}>{place.display_name}{!place.city_code || !place.provider_place_id ? '（地点信息不完整）' : ''}</button>
    </li>)}</ul>}
    {attribution && <small>地点来源：{attribution}</small>}
    {selected && <><p>查看馆区：{selected.display_name}</p><AttractionInfo place={selected} /></>}
  </details>
}
