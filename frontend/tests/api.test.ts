import { describe, expect, test, vi } from 'vitest'

import {
  deleteConversation,
  getConversation,
  getConversations,
  streamChat,
} from '../src/api'
import type { AgentStreamEvent } from '../src/api'

function chunkedResponse(text: string, cutPoints: number[]): Response {
  const bytes = new TextEncoder().encode(text)
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      let start = 0
      for (const end of cutPoints) {
        controller.enqueue(bytes.slice(start, end))
        start = end
      }
      controller.enqueue(bytes.slice(start))
      controller.close()
    },
  })
  return new Response(stream, {
    status: 200,
    headers: { 'Content-Type': 'application/x-ndjson' },
  })
}

describe('streamChat', () => {
  test('parses NDJSON split across arbitrary byte chunks', async () => {
    const lines = [
      {
        type: 'run_started',
        thread_id: 'thread-1',
      },
      {
        type: 'assistant_delta',
        thread_id: 'thread-1',
        delta: '深圳天气',
      },
      {
        type: 'final',
        thread_id: 'thread-1',
        answer: '深圳当前天气为阴天。',
      },
      {
        type: 'done',
        thread_id: 'thread-1',
      },
    ] satisfies AgentStreamEvent[]
    const ndjson = `${lines.map((line) => JSON.stringify(line)).join('\n')}\n`
    const fetchMock = vi.fn().mockResolvedValue(
      chunkedResponse(ndjson, [5, 31, 64, 91, 137]),
    )
    vi.stubGlobal('fetch', fetchMock)

    const received: AgentStreamEvent[] = []
    await streamChat('深圳天气', undefined, (event) => {
      received.push(event)
    })

    expect(received).toEqual(lines)
    expect(fetchMock).toHaveBeenCalledOnce()
    const request = fetchMock.mock.calls[0]
    expect(request[0]).toBe('http://127.0.0.1:8000/api/chat/stream')
    expect(JSON.parse(request[1].body)).toEqual({ message: '深圳天气' })
  })

  test('throws for a non-success HTTP response', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response('', { status: 503 })),
    )

    await expect(
      streamChat('深圳天气', undefined, () => undefined),
    ).rejects.toThrow('HTTP 503')
  })
})

describe('conversation history API', () => {
  test('requests the list and one encoded conversation detail path', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response('[]', {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            conversation: {
              thread_id: 'thread/with slash',
              title: '测试',
              status: 'ready',
              created_at: '2026-07-29T12:00:00Z',
              updated_at: '2026-07-29T12:00:00Z',
            },
            messages: [],
          }),
          {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          },
        ),
      )
    vi.stubGlobal('fetch', fetchMock)

    await getConversations()
    await getConversation('thread/with slash')

    expect(fetchMock.mock.calls[0][0]).toBe(
      'http://127.0.0.1:8000/api/conversations',
    )
    expect(fetchMock.mock.calls[1][0]).toBe(
      'http://127.0.0.1:8000/api/conversations/thread%2Fwith%20slash',
    )
  })

  test('deletes one conversation without trying to parse a 204 body', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(null, { status: 204 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await deleteConversation('thread-delete')

    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:8000/api/conversations/thread-delete',
      { method: 'DELETE' },
    )
  })
})
