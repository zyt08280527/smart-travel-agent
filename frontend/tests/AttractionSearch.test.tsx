import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { expect, test, vi } from 'vitest'
import { AttractionSearch } from '../src/AttractionSearch'
import { searchMultiPlaces } from '../src/multiStopApi'

vi.mock('../src/multiStopApi', () => ({ searchMultiPlaces: vi.fn() }))
vi.mock('../src/AttractionInfo', () => ({ AttractionInfo: ({ place }: { place: { provider_place_id: string } }) => <p>景点卡：{place.provider_place_id}</p> }))
const place = { display_name: '深圳博物馆同心路馆', provider_place_id: 'B0LKCH16U2', city_code: '440300', latitude: 22.54529, longitude: 114.09611 }
const result = { provider: 'amap' as const, attribution: '高德地图', places: [place] }

test('单段目的地查询须明确点选馆区，不在渲染时搜索或自动选择', async () => {
  vi.mocked(searchMultiPlaces).mockReset().mockResolvedValue(result)
  render(<AttractionSearch destination="深圳博物馆" />)
  expect(searchMultiPlaces).not.toHaveBeenCalled()
  fireEvent.click(screen.getByText('查询目的地参观信息'))
  fireEvent.click(screen.getByRole('button', { name: '搜索参观地点' }))
  const choice = await screen.findByRole('button', { name: place.display_name })
  expect(screen.queryByText(/景点卡：/)).not.toBeInTheDocument()
  fireEvent.click(choice)
  expect(screen.getByText('景点卡：B0LKCH16U2')).toBeInTheDocument()
  expect(searchMultiPlaces).toHaveBeenCalledWith('深圳博物馆')
})

test('编辑查询或切换目的地时隔离旧候选和旧响应', async () => {
  let resolveOld!: (value: typeof result) => void
  vi.mocked(searchMultiPlaces).mockReset().mockReturnValueOnce(new Promise((resolve) => { resolveOld = resolve }))
  const view = render(<AttractionSearch destination="旧目的地" />)
  fireEvent.click(screen.getByText('查询目的地参观信息'))
  fireEvent.click(screen.getByRole('button', { name: '搜索参观地点' }))
  fireEvent.change(screen.getByLabelText('景点城市及名称'), { target: { value: '新关键词' } })
  await act(async () => resolveOld(result))
  expect(screen.queryByRole('button', { name: place.display_name })).not.toBeInTheDocument()
  vi.mocked(searchMultiPlaces).mockResolvedValue(result)
  fireEvent.click(screen.getByRole('button', { name: '搜索参观地点' }))
  fireEvent.click(await screen.findByRole('button', { name: place.display_name }))
  expect(screen.getByText(/景点卡：/)).toBeInTheDocument()
  view.rerender(<AttractionSearch destination="新目的地" />)
  expect(screen.queryByText(/景点卡：/)).not.toBeInTheDocument()
  expect(screen.getByLabelText('景点城市及名称')).toHaveValue('新目的地')
})

test('来源不支持或搜索失败时不展示可选择候选', async () => {
  vi.mocked(searchMultiPlaces).mockReset().mockResolvedValueOnce({ ...result, provider: 'openstreetmap' }).mockRejectedValueOnce(new Error('offline'))
  render(<AttractionSearch destination="深圳" />)
  fireEvent.click(screen.getByText('查询目的地参观信息'))
  fireEvent.click(screen.getByRole('button', { name: '搜索参观地点' }))
  await screen.findByText(/当前搜索来源暂不支持/)
  expect(screen.queryByRole('button', { name: place.display_name })).not.toBeInTheDocument()
  await waitFor(() => expect(screen.getByRole('button', { name: '搜索参观地点' })).toBeEnabled())
  fireEvent.click(screen.getByRole('button', { name: '搜索参观地点' }))
  await screen.findByText(/地点查询失败/)
})
