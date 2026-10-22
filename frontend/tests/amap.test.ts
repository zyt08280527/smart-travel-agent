import { afterEach, expect, it, vi } from 'vitest'
import { convertRoute, loadAmap } from '../src/amap'
import type { AmapSdk } from '../src/amap'

afterEach(() => { vi.unstubAllEnvs() })

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
