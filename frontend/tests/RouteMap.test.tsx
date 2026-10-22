import { act, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { RouteMap } from '../src/RouteMap'
import { convertRoute, loadAmap } from '../src/amap'
import type { AmapSdk } from '../src/amap'

vi.mock('../src/amap', () => ({ loadAmap: vi.fn(), convertRoute: vi.fn() }))

const destroy = vi.fn()
const add = vi.fn()
const setFitView = vi.fn()
const mapConstructor = vi.fn(function () { return { destroy, add, setFitView } })
const polyline = vi.fn(function (options: object) { return options })
const sdk = {
  Map: mapConstructor, Polyline: polyline,
  Marker: vi.fn(function (options: object) { return options }),
} as unknown as AmapSdk
const route = [{ latitude: 22.5, longitude: 114 }, { latitude: 22.6, longitude: 114.1 }]

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(loadAmap).mockResolvedValue(sdk)
  vi.mocked(convertRoute).mockResolvedValue([[114.005, 22.497], [114.105, 22.597]])
})

describe('高德路线地图', () => {
  it('绘制转换后的路线，切换候选后清理旧图并更新轨迹', async () => {
    const view = render(<RouteMap geometry={route} mode="driving" label="驾车" />)
    await waitFor(() => expect(add).toHaveBeenCalledTimes(1))
    expect(polyline).toHaveBeenLastCalledWith(expect.objectContaining({
      path: [[114.005, 22.497], [114.105, 22.597]], strokeColor: '#2563eb',
    }))
    vi.mocked(convertRoute).mockResolvedValue([[114.2, 22.5], [114.3, 22.6]])
    view.rerender(<RouteMap geometry={[...route]} mode="transit" label="公共交通" />)
    await waitFor(() => expect(add).toHaveBeenCalledTimes(2))
    expect(destroy).toHaveBeenCalledTimes(1)
    expect(polyline).toHaveBeenLastCalledWith(expect.objectContaining({
      path: [[114.2, 22.5], [114.3, 22.6]], strokeColor: '#7c3aed',
    }))
    view.unmount()
    expect(destroy).toHaveBeenCalledTimes(2)
  })

  it('加载失败显示可恢复提示', async () => {
    vi.mocked(loadAmap).mockRejectedValue(new Error('地图加载失败，请重试。'))
    render(<RouteMap geometry={route} mode="walking" label="步行" />)
    expect(await screen.findByText('地图加载失败，请重试。')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重试地图' })).toBeInTheDocument()
    expect(mapConstructor).not.toHaveBeenCalled()
  })

  it('过期的异步转换不会覆盖刚选中的路线', async () => {
    let finishOld!: (positions: [number, number][]) => void
    vi.mocked(convertRoute).mockImplementationOnce(() => new Promise((resolve) => { finishOld = resolve }))
    const view = render(<RouteMap geometry={route} mode="driving" label="旧方案" />)
    await waitFor(() => expect(convertRoute).toHaveBeenCalledTimes(1))
    view.rerender(<RouteMap geometry={[...route]} mode="transit" label="新方案" />)
    await waitFor(() => expect(mapConstructor).toHaveBeenCalledTimes(1))
    await act(async () => { finishOld([[1, 2], [3, 4]]) })
    expect(mapConstructor).toHaveBeenCalledTimes(1)
    expect(polyline).toHaveBeenLastCalledWith(expect.objectContaining({ strokeColor: '#7c3aed' }))
  })
})
