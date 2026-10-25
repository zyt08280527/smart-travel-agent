import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, test, vi } from 'vitest'
import { MultiStopMap } from '../src/MultiStopMap'
import { convertRoute, loadAmap } from '../src/amap'
import type { AmapSdk } from '../src/amap'
import type { MultiPlan, Stop } from '../src/multiStopApi'

vi.mock('../src/amap', () => ({ convertRoute: vi.fn(), loadAmap: vi.fn() }))
const add = vi.fn(), destroy = vi.fn()
const polyline = vi.fn(function (options: object) { return options })
const marker = vi.fn(function (options: object) { return options })
const sdk = { Map: vi.fn(function () { return { add, destroy, setFitView: vi.fn() } }), Polyline: polyline, Marker: marker } as unknown as AmapSdk
const stops: Stop[] = [0, 1, 2].map((index) => ({ stop_id: String(index), name: `站点${index}`, city_code: '440300', stay_minutes: 0, point: { latitude: 22.5 + index / 100, longitude: 114 } }))
function plan(missing = false) {
  return { evaluation: { legs: [0, 1].map((index) => ({ leg: { option: { geometry: missing && index === 1 ? [] : [stops[index].point, stops[index + 1].point] } } })) } } as unknown as MultiPlan
}
beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(loadAmap).mockResolvedValue(sdk)
  vi.mocked(convertRoute).mockImplementation(async (_sdk, points) => points.map((point) => [point.longitude, point.latitude]))
})
test('所有分段分别绘制、所有站点标记，切换高亮段清理旧图', async () => {
  const result = plan()
  const view = render(<MultiStopMap plan={result} stops={stops} activeLeg={0} />)
  await waitFor(() => expect(add).toHaveBeenCalledTimes(1))
  expect(polyline).toHaveBeenCalledTimes(2)
  expect(marker).toHaveBeenCalledTimes(3)
  expect(polyline.mock.calls[0][0]).toMatchObject({ strokeColor: '#2563eb' })
  expect(polyline.mock.calls[1][0]).toMatchObject({ strokeColor: '#94a3b8' })
  view.rerender(<MultiStopMap plan={result} stops={stops} activeLeg={1} />)
  await waitFor(() => expect(add).toHaveBeenCalledTimes(2))
  expect(destroy).toHaveBeenCalledTimes(1)
  expect(polyline.mock.calls[3][0]).toMatchObject({ strokeColor: '#2563eb' })
  view.unmount()
  expect(destroy).toHaveBeenCalledTimes(2)
})
test('缺失轨迹不补画站点间直线，仍保留站点和提示', async () => {
  render(<MultiStopMap plan={plan(true)} stops={stops} activeLeg={0} />)
  expect(await screen.findByText('部分路线缺少轨迹，仅展示已有轨迹和站点。')).toBeInTheDocument()
  expect(polyline).toHaveBeenCalledTimes(1)
  expect(marker).toHaveBeenCalledTimes(3)
})
