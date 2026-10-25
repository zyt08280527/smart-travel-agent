import { afterEach, expect, it, vi } from 'vitest'
import { convertRoute, loadAmap } from '../src/amap'
import type { AmapSdk } from '../src/amap'

afterEach(() => { vi.unstubAllEnvs(); vi.useRealTimers() })

it('未配置 JS Key 时返回明确错误，不发起脚本请求', async () => {
  vi.stubEnv('VITE_AMAP_JS_KEY', '')
  await expect(loadAmap()).rejects.toThrow('地图尚未配置')
  expect(document.querySelector('script[src*="webapi.amap.com"]')).toBeNull()
})

it('将 WGS84 按经纬顺序以每批不超过 40 个点转换，保留所有点顺序', async () => {
  const convertFrom = vi.fn((points, _type, callback) => callback('complete', {
    info: 'ok', locations: points.map(([lng, lat]: [number, number]) => ({
      getLng: () => lng + 0.005, getLat: () => lat - 0.003,
    })),
  }))
  const sdk = { convertFrom } as unknown as AmapSdk
  const points = Array.from({ length: 81 }, (_, i) => ({ longitude: 114 + i / 1000, latitude: 22.5 }))
  const positions = await convertRoute(sdk, points)
  expect(convertFrom.mock.calls.map((call) => call[0].length)).toEqual([40, 40, 1])
  expect(positions).toHaveLength(81)
  expect(positions[0]).toEqual([114.005, 22.497])
  expect(convertFrom.mock.calls[0][1]).toBe('gps')
})

it('转换返回空结果时拒绝绘制，避免将 WGS84 直接当作高德坐标', async () => {
  const sdk = { convertFrom: (_points: unknown, _type: unknown, callback: (status: string, result: object) => void) => {
    callback('complete', { info: 'ok', locations: [] })
  } } as unknown as AmapSdk
  await expect(convertRoute(sdk, [{ longitude: 114, latitude: 22.5 }])).rejects.toThrow('路线地图暂时不可用')
})

it('展示供应商的安全错误码，兼容字符串错误且不透出任意响应', async () => {
  const convertFrom = vi.fn((_points, _type, callback) => callback('error', 'INVALID_USER_SCODE'))
  const sdk = { convertFrom } as unknown as AmapSdk
  const points = [{ longitude: 114, latitude: 22.5 }]
  await expect(convertRoute(sdk, points)).rejects.toThrow('INVALID_USER_SCODE')
  convertFrom.mockImplementation((_points, _type, callback) => callback('error', 'https://example.test/?key=private'))
  await expect(convertRoute(sdk, points)).rejects.toThrow('路线地图暂时不可用，仍可查看路线详情。')
})

it('并发分段共享限速队列，切换地图时复用相同坐标且保留重复点顺序', async () => {
  vi.useFakeTimers()
  const starts: number[] = []
  const convertFrom = vi.fn((points, _type, callback) => {
    starts.push(Date.now())
    callback('complete', { info: 'ok', locations: points.map(([lng, lat]: [number, number]) => ({ getLng: () => lng, getLat: () => lat })) })
  })
  const sdk = { convertFrom } as unknown as AmapSdk
  const a = { longitude: 114, latitude: 22.5 }
  const b = { longitude: 114.1, latitude: 22.6 }
  const first = convertRoute(sdk, [a, a])
  const second = convertRoute(sdk, [a, b])
  await vi.runAllTimersAsync()
  expect(await first).toEqual([[114, 22.5], [114, 22.5]])
  expect(await second).toEqual([[114, 22.5], [114.1, 22.6]])
  expect(starts[1] - starts[0]).toBeGreaterThanOrEqual(1100)
  expect(convertFrom.mock.calls.map((call) => call[0].length)).toEqual([1, 1])
  await expect(convertRoute(sdk, [b, a])).resolves.toEqual([[114.1, 22.6], [114, 22.5]])
  expect(convertFrom).toHaveBeenCalledTimes(2)
})
