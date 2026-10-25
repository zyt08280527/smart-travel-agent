import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { MultiStopWeather } from '../src/MultiStopWeather'

const response = (data: unknown) => ({ ok: true, json: async () => data })
const report = (planId: string) => ({
  plan_id: planId, queried_at: '2026-10-23T09:00:00Z', limitations: ['不改变路线排序'],
  stops: [
    { stop_id: 'visit', name: '目的地', target_at: '2026-10-24T01:10:00Z', forecast_at: '2026-10-24T01:00:00Z', status: 'forecast', temperature_c: 30, apparent_temperature_c: 36, precipitation_mm: 2, wind_speed_kmh: 10, condition: '小雨', warnings: ['留意户外步行安排'] },
    { stop_id: 'return', name: '返程点', target_at: '2026-10-24T03:10:00Z', status: 'unavailable', message: '预报未覆盖', warnings: [] },
  ],
})
afterEach(() => vi.unstubAllGlobals())

test('按需查询当前方案，区分目标/预报时刻，未知不显示为晴天', async () => {
  const fetcher = vi.fn().mockResolvedValue(response(report('selected')))
  vi.stubGlobal('fetch', fetcher)
  render(<MultiStopWeather draftId="draft" planId="selected" />)
  expect(fetcher).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '查询或刷新逐站天气' }))
  await screen.findByText('预报时刻前一小时累计降水：2 毫米')
  expect(screen.getByText('天气未知：预报未覆盖')).toBeInTheDocument()
  expect(screen.getByText(/匹配预报时刻/)).toHaveTextContent('小雨')
  expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ selected_plan_id: 'selected' })
  expect(fetcher.mock.calls[0][0]).toContain('/drafts/draft/weather')
})

test('切换方案隔离旧天气及旧请求，失败可重试且不显示旧天气', async () => {
  let resolveOld!: (value: unknown) => void
  const fetcher = vi.fn().mockReturnValueOnce(new Promise((resolve) => { resolveOld = resolve })).mockRejectedValueOnce(new Error('offline')).mockResolvedValue(response(report('new')))
  vi.stubGlobal('fetch', fetcher)
  const view = render(<MultiStopWeather draftId="draft" planId="old" />)
  fireEvent.click(screen.getByRole('button', { name: '查询或刷新逐站天气' }))
  view.rerender(<MultiStopWeather draftId="draft" planId="new" />)
  await act(async () => resolveOld(response(report('old'))))
  expect(screen.queryByText('目的地')).not.toBeInTheDocument()
  expect(fetcher.mock.calls[0][1].signal.aborted).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: '查询或刷新逐站天气' }))
  await screen.findByRole('alert')
  fireEvent.click(screen.getByRole('button', { name: '查询或刷新逐站天气' }))
  await screen.findByText('目的地')
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})
