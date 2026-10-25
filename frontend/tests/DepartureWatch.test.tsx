import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { DepartureWatch } from '../src/DepartureWatch'

afterEach(() => vi.unstubAllGlobals())

test('explicit opt in, persisted notice recovery and cancellation', async () => {
  const watch = { id: 'w', saved_id: 's', status: 'pending', checked_at: null, message: '' }
  const fetcher = vi.fn().mockResolvedValueOnce({ ok: true, json: async () => [] })
    .mockResolvedValueOnce({ ok: true, json: async () => watch })
    .mockResolvedValueOnce({ ok: true })
  vi.stubGlobal('fetch', fetcher)
  render(<DepartureWatch savedId="s" scheduled />)
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1))
  fireEvent.click(screen.getByRole('button', { name: '开启出发前守护' }))
  await screen.findByText('等待检查')
  fireEvent.click(screen.getByRole('button', { name: '关闭守护' }))
  await screen.findByText('已关闭')
  expect(fetcher.mock.calls[1][1].method).toBe('POST')
  expect(fetcher.mock.calls[2][1].method).toBe('DELETE')
})

test('missing time cannot enable and server failures are visible', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false }))
  render(<DepartureWatch savedId="relative" scheduled={false} />)
  expect(screen.getByRole('button', { name: '开启出发前守护' })).toBeDisabled()
  expect(await screen.findByRole('alert')).toHaveTextContent('暂时不可用')
})

test('single journey can supply Beijing departure time without altering saved trip', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce({ ok: true, json: async () => [] })
    .mockResolvedValueOnce({ ok: true, json: async () => ({ id: 'w', saved_id: 'single:s', status: 'pending', message: '' }) })
  vi.stubGlobal('fetch', fetcher)
  render(<DepartureWatch savedId="s" scheduled={false} single />)
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1))
  expect(screen.getByRole('button', { name: '开启出发前守护' })).toBeDisabled()
  fireEvent.change(screen.getByLabelText('守护出发时间（北京时间）'), { target: { value: '2030-01-01T09:00' } })
  fireEvent.click(screen.getByRole('button', { name: '开启出发前守护' }))
  await screen.findByText('等待检查')
  expect(fetcher.mock.calls[1][0]).toContain('/api/itineraries/s/watch')
  expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({ departure_at: '2030-01-01T09:00:00+08:00' })
})

test('legacy single record asks for replanning and cannot opt in', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => [] }))
  render(<DepartureWatch savedId="old" scheduled={false} single eligible={false} />)
  expect(screen.getByText(/旧记录缺少完整规划信息/)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '开启出发前守护' })).toBeDisabled()
})
