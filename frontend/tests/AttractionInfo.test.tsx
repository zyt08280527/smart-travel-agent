import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { AttractionInfo } from '../src/AttractionInfo'

const place = { display_name: '深圳博物馆金田路馆', city_code: '440300', provider_place_id: 'B02F38IRTR', latitude: 22.54639, longitude: 114.0568 }
const card = {
  name: '深圳博物馆金田路馆（历史民俗）', official_url: 'https://mobile.shenzhenmuseum.com/guide',
  suggested_stay_minutes: [120, 240], stay_note: '产品建议，不会自动修改行程停留时间。',
  facts: [
    { kind: 'reservation', status: 'verified', value: '普通入馆无需提前预约', scope: '普通参观', expires_at: '2026-10-30T09:12:00Z', sources: [{ title: '官方预约须知', url: 'https://mobile.shenzhenmuseum.com/guide', checked_at: '2026-10-23T09:12:00Z' }] },
    { kind: 'ticket', status: 'expired', value: '不应显示的旧票价', scope: '普通参观', sources: [] },
    { kind: 'opening', status: 'conflicting', value: '不应显示的开馆时间', scope: '需核对', sources: [] },
  ],
}
const response = (payload: unknown) => ({ ok: true, json: async () => payload })
function expand(container: HTMLElement) {
  const details = container.querySelector('details')!
  details.open = true
  fireEvent(details, new Event('toggle'))
}

afterEach(() => vi.unstubAllGlobals())

test('按需查询并展示逐字段来源，冲突与过期内容不可作为结论', async () => {
  const fetcher = vi.fn().mockResolvedValue(response({ status: 'matched', card }))
  vi.stubGlobal('fetch', fetcher)
  const { container } = render(<AttractionInfo place={place} />)
  expect(fetcher).not.toHaveBeenCalled()
  expand(container)
  await screen.findByRole('region', { name: '景点信息卡' })
  expect(screen.getByText(/普通入馆无需提前预约/)).toBeInTheDocument()
  expect(screen.getByText(/来源信息存在差异/)).toBeInTheDocument()
  expect(screen.getByText(/核验已过期/)).toBeInTheDocument()
  expect(screen.queryByText(/不应显示/)).not.toBeInTheDocument()
  expect(screen.getByRole('link', { name: '官方预约须知' })).toHaveAttribute('href', 'https://mobile.shenzhenmuseum.com/guide')
  expect(screen.getByText(/产品建议，不会自动修改/)).toBeInTheDocument()
  expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual(place)
})

test('未收录与请求失败分别展示，失败可重试', async () => {
  const fetcher = vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValue(response({ status: 'not_found', card: null }))
  vi.stubGlobal('fetch', fetcher)
  const { container } = render(<AttractionInfo place={place} />)
  expand(container)
  await screen.findByRole('alert')
  fireEvent.click(screen.getByRole('button', { name: '重试参观信息' }))
  await screen.findByText(/尚未纳入已核验景点库/)
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})

test('地点变化后旧响应不能覆盖新地点的信息', async () => {
  let resolveOld!: (value: unknown) => void
  const old = new Promise((resolve) => { resolveOld = resolve })
  const fetcher = vi.fn().mockReturnValueOnce(old).mockResolvedValue(response({ status: 'not_found', card: null }))
  vi.stubGlobal('fetch', fetcher)
  const view = render(<AttractionInfo place={place} />)
  expand(view.container)
  await waitFor(() => expect(fetcher).toHaveBeenCalledOnce())
  view.rerender(<AttractionInfo place={{ ...place, provider_place_id: 'other-campus' }} />)
  await screen.findByText(/尚未纳入已核验景点库/)
  await act(async () => resolveOld(response({ status: 'matched', card })))
  expect(screen.queryByRole('region', { name: '景点信息卡' })).not.toBeInTheDocument()
  expect(fetcher.mock.calls[0][1].signal.aborted).toBe(true)
})
