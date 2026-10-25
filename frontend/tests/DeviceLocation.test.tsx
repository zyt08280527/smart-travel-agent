import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { DeviceLocation } from '../src/DeviceLocation'
import { DiscoveryWorkspace } from '../src/DiscoveryWorkspace'

afterEach(() => vi.unstubAllGlobals())
const place = { display_name: '当前位置：深圳南山区某路', latitude: 22.53, longitude: 113.93, city_code: '440300', provider_place_id: null, location_source: 'device', location_accuracy_m: 25, location_captured_at: '2030-01-01T01:00:00Z' }
function gps() {
  let success!: PositionCallback
  let failure!: PositionErrorCallback
  const get = vi.fn((s: PositionCallback, e: PositionErrorCallback) => { success = s; failure = e })
  vi.stubGlobal('isSecureContext', true)
  vi.stubGlobal('navigator', { geolocation: { getCurrentPosition: get } })
  return { get, ok: (accuracy = 25) => success({ coords: { latitude: 22.53, longitude: 113.93, accuracy }, timestamp: Date.parse(place.location_captured_at) } as GeolocationPosition), fail: () => failure({ code: 1 } as GeolocationPositionError) }
}

test('explicit click, confirmation and location used as recommendation origin', async () => {
  const position = gps()
  const fetcher = vi.fn(async (url: string) => ({ ok: true, json: async () => url.endsWith('/device-location') ? place : { candidates: [], excluded: [], limitations: [] } }))
  vi.stubGlobal('fetch', fetcher)
  render(<DiscoveryWorkspace onPlan={vi.fn()} />)
  expect(position.get).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '使用当前位置' }))
  expect(position.get.mock.calls[0][2]).toEqual({ enableHighAccuracy: true, timeout: 10000, maximumAge: 0 })
  await act(async () => position.ok())
  await screen.findByRole('button', { name: '确认使用此位置' })
  expect(screen.getByRole('button', { name: '查找可考虑的景点' })).toBeDisabled()
  expect(screen.queryByText(/已确认出发点/)).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '确认使用此位置' }))
  expect(screen.getByText(/已确认出发点：当前位置/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '查找可考虑的景点' }))
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2))
  const call = fetcher.mock.calls[1] as unknown as [string, RequestInit]
  expect(JSON.parse(call[1].body as string).origin).toEqual(place)
})

test('coarse accuracy and insecure context use manual fallback without sending coordinates', async () => {
  const position = gps()
  const fetcher = vi.fn()
  vi.stubGlobal('fetch', fetcher)
  const view = render(<DeviceLocation active resetKey="manual" onConfirm={vi.fn()} onPending={vi.fn()} />)
  fireEvent.click(screen.getByRole('button', { name: '使用当前位置' }))
  await act(async () => position.ok(1000))
  expect(screen.getByRole('alert')).toHaveTextContent('精度不足')
  expect(fetcher).not.toHaveBeenCalled()
  view.unmount()
  vi.stubGlobal('isSecureContext', false)
  render(<DeviceLocation active resetKey="manual" onConfirm={vi.fn()} onPending={vi.fn()} />)
  fireEvent.click(screen.getByRole('button', { name: '使用当前位置' }))
  expect(screen.getByRole('alert')).toHaveTextContent('安全连接')
  expect(position.get).toHaveBeenCalledTimes(1)
})

test('manual edit invalidates late address response', async () => {
  const position = gps()
  let finish!: (value: unknown) => void
  vi.stubGlobal('fetch', vi.fn(() => new Promise(resolve => { finish = resolve })))
  const confirm = vi.fn(), pending = vi.fn()
  const view = render(<DeviceLocation active resetKey="old" onConfirm={confirm} onPending={pending} />)
  fireEvent.click(screen.getByRole('button', { name: '使用当前位置' }))
  act(() => { position.ok() })
  view.rerender(<DeviceLocation active resetKey="new manual location" onConfirm={confirm} onPending={pending} />)
  await act(async () => finish({ ok: true, json: async () => place }))
  expect(screen.queryByRole('button', { name: '确认使用此位置' })).not.toBeInTheDocument()
  expect(confirm).not.toHaveBeenCalled()
})

test('denial keeps manual origin, cancelled and closed callbacks cannot replace it', async () => {
  const position = gps()
  const confirm = vi.fn(), pending = vi.fn()
  const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => place })
  vi.stubGlobal('fetch', fetcher)
  const view = render(<DeviceLocation active resetKey="manual" onConfirm={confirm} onPending={pending} />)
  fireEvent.click(screen.getByRole('button', { name: '使用当前位置' }))
  act(() => position.fail())
  expect(screen.getByRole('alert')).toHaveTextContent('授权被拒绝')
  fireEvent.click(screen.getByRole('button', { name: '使用当前位置' }))
  fireEvent.click(screen.getByRole('button', { name: '取消定位' }))
  await act(async () => position.ok())
  expect(fetcher).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '使用当前位置' }))
  view.rerender(<DeviceLocation active={false} resetKey="manual" onConfirm={confirm} onPending={pending} />)
  await act(async () => position.ok())
  expect(confirm).not.toHaveBeenCalled()
  expect(fetcher).not.toHaveBeenCalled()
})
