import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { NotificationCenter } from '../src/NotificationCenter'

afterEach(() => vi.unstubAllGlobals())
const notice = { id: 'w', title: '起点 → 终点', kind: 'single', status: 'completed', checked_at: '2030-01-01T01:00:00Z', departure_at: '2030-01-01T02:00:00Z', message: '增加约20分钟', unread: true, notification_token: 'a'.repeat(64) }
const detail = { kind: 'single', snapshot: { title: '原行程', origin: '起点', destination: '终点', travel_mode: 'walking', duration_s: 1200 }, replan_prompt: '保留原条件重新规划，不要保存' }

test('badge, read receipt, immutable preview and explicit replan', async () => {
  let read = false
  const fetcher = vi.fn(async (url: string, _options?: RequestInit) => {
    if (url.endsWith('/read')) { read = true; return { ok: true, status: 204 } }
    if (url.endsWith('/w')) return { ok: true, status: 200, json: async () => detail }
    return { ok: true, status: 200, json: async () => [{ ...notice, unread: !read }] }
  })
  vi.stubGlobal('fetch', fetcher)
  const replan = vi.fn().mockResolvedValue(undefined)
  render(<NotificationCenter disabled={false} onReplan={replan} />)
  fireEvent.click(await screen.findByRole('button', { name: '行程提醒（1条未读）' }))
  const dialog = screen.getByRole('dialog', { name: '行程提醒' })
  expect(within(dialog).getByText('增加约20分钟')).toBeInTheDocument()
  fireEvent.click(within(dialog).getByRole('button', { name: '查看原行程' }))
  await screen.findByRole('region', { name: '原保存行程只读预览' })
  expect(replan).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '标为已读' }))
  await waitFor(() => expect(screen.queryByRole('button', { name: '标为已读' })).not.toBeInTheDocument())
  const call = fetcher.mock.calls.find(([, options]) => options?.method === 'POST')!
  expect(JSON.parse(call[1]!.body as string)).toEqual({ notification_token: notice.notification_token })
  fireEvent.click(screen.getByRole('button', { name: '发起重新规划' }))
  await waitFor(() => expect(replan).toHaveBeenCalledWith(detail))
})

test('cancel, stale read failure and read-only multi-leg preview', async () => {
  let cancelled = false
  const multi = { ...notice, kind: 'multi', status: 'pending' }
  const snapshot = { selected_plan_id: 'chosen', draft: { result: {
    request: { stops: [{ name: 'A', stop_id: 'a', stay_minutes: 0 }, { name: 'B', stop_id: 'b', stay_minutes: 60 }, { name: 'C', stop_id: 'c', stay_minutes: 0 }] },
    selection: { plans: [{ plan_id: 'chosen', evaluation: { legs: [{ leg: { option: { mode: 'walking', duration_s: 600 } } }, { leg: { option: { mode: 'transit', duration_s: 1200 } } }] } }] },
  } } }
  vi.stubGlobal('fetch', vi.fn(async (url: string, options?: RequestInit) => {
    if (url.endsWith('/read')) return { ok: false, status: 409, json: async () => ({ detail: '提醒已有新结果' }) }
    if (options?.method === 'DELETE') { cancelled = true; return { ok: true, status: 204 } }
    if (url.endsWith('/w')) return { ok: true, status: 200, json: async () => ({ kind: 'multi', snapshot }) }
    return { ok: true, status: 200, json: async () => [{ ...multi, status: cancelled ? 'cancelled' : 'pending' }] }
  }))
  render(<NotificationCenter disabled onReplan={vi.fn()} />)
  fireEvent.click(await screen.findByRole('button', { name: '行程提醒（1条未读）' }))
  expect(screen.getByRole('button', { name: '发起重新规划' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: '标为已读' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('提醒已有新结果')
  fireEvent.click(screen.getByRole('button', { name: '查看原行程' }))
  await screen.findByText(/第2段：B → C/)
  expect(screen.getByText('B停留60分钟')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '关闭守护' }))
  await waitFor(() => expect(screen.queryByRole('button', { name: '关闭守护' })).not.toBeInTheDocument())
})
