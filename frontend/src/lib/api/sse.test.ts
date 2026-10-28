import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { readJsonEvents } from './sse';
import { chatApi, petsApi, sessionsApi } from '../api';
import { tokenStorage } from '../auth';

const bytes = (text: string) => new TextEncoder().encode(text);
const access = () => `x.${btoa(JSON.stringify({ sub: 'user', exp: Math.floor(Date.now() / 1000) + 3600 }))}.x`;
function response(text: string, byteChunks = false) {
  return new Response(new ReadableStream<Uint8Array>({ start(controller) {
    const data = bytes(text);
    if (byteChunks) for (const value of data) controller.enqueue(new Uint8Array([value]));
    else controller.enqueue(data);
    controller.close();
  } }), { headers: { 'Content-Type': 'text/event-stream' } });
}
beforeEach(() => { tokenStorage.clear(); tokenStorage.set(access(), 'refresh'); vi.stubGlobal('fetch', vi.fn()); });
afterEach(() => { vi.unstubAllGlobals(); tokenStorage.clear(); });

describe('JSON SSE reader', () => {
  it('handles UTF-8 and CRLF split across byte chunks, multi-line data, comments and EOF', async () => {
    const stream = response(': heartbeat\r\nevent: text_delta\r\ndata:{\r\ndata: "content":"中文"}\r\n\r\ndata:invalid\n\ndata:{"type":"done"}', true);
    const receive = vi.fn();
    await readJsonEvents(stream, receive);
    expect(receive.mock.calls.map(([event]) => event)).toEqual([
      { type: 'text_delta', content: '中文' }, { type: 'done' },
    ]);
    expect(stream.body?.locked).toBe(false);
  });

  it('supports standalone CR separators and an event without a space after data:', async () => {
    const receive = vi.fn();
    await readJsonEvents(response('data:{"type":"first"}\r\rdata: {"type":"second"}\r\r', true), receive);
    expect(receive.mock.calls.map(([event]) => event.type)).toEqual(['first', 'second']);
  });

  it('cancels its reader and drops events buffered after local cancellation', async () => {
    const cancelled = vi.fn();
    const controller = new AbortController();
    const stream = new Response(new ReadableStream<Uint8Array>({
      start(source) { source.enqueue(bytes('data:{"type":"one"}\n\ndata:{"type":"two"}\n\n')); },
      cancel: cancelled,
    }));
    const receive = vi.fn(() => controller.abort());
    await expect(readJsonEvents(stream, receive, controller.signal)).rejects.toMatchObject({ name: 'AbortError' });
    expect(receive).toHaveBeenCalledTimes(1);
    expect(cancelled).toHaveBeenCalledTimes(1);
    expect(stream.body?.locked).toBe(false);
  });

  it('unblocks a pending read when the login identity changes', async () => {
    const cancelled = vi.fn();
    const stream = new Response(new ReadableStream<Uint8Array>({ cancel: cancelled }));
    const receive = vi.fn();
    const pending = readJsonEvents(stream, receive);
    const rejected = expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    tokenStorage.clear();
    await rejected;
    expect(cancelled).toHaveBeenCalledTimes(1);
    expect(receive).not.toHaveBeenCalled();
    expect(stream.body?.locked).toBe(false);
  });

  it('propagates callback errors instead of silently treating them as malformed JSON', async () => {
    const stream = response('data:{"type":"done"}\n\n');
    await expect(readJsonEvents(stream, () => { throw new Error('callback failed'); })).rejects.toThrow('callback failed');
    expect(stream.body?.locked).toBe(false);
  });
});

describe('stream API adapters', () => {
  it.each(['chat', 'channel', 'pet'] as const)('%s shares the decoder and closes exactly once at EOF', async (kind) => {
    vi.mocked(fetch).mockResolvedValue(response('data:{"type":"text_delta",\r\ndata:"content":"tail"}', true));
    const receive = vi.fn();
    if (kind === 'chat') chatApi.stream({ message: 'hi' }, receive);
    else if (kind === 'channel') sessionsApi.watchEvents('id', receive);
    else petsApi.bubble('id', receive);
    await vi.waitFor(() => expect(receive.mock.calls.map(([event]) => event.type)).toEqual(['text_delta', 'closed']));
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('reports a failed live connection without replaying the task-start POST', async () => {
    let source!: ReadableStreamDefaultController<Uint8Array>;
    vi.mocked(fetch).mockResolvedValue(new Response(new ReadableStream({ start(controller) { source = controller; } })));
    const receive = vi.fn();
    chatApi.stream({ message: 'hi' }, receive);
    await vi.waitFor(() => expect(fetch).toHaveBeenCalled());
    source.enqueue(bytes('data:{"type":"text_delta","content":"partial"}\n\n'));
    await vi.waitFor(() => expect(receive).toHaveBeenCalledWith({ type: 'text_delta', content: 'partial' }));
    source.error(new Error('connection lost'));
    await vi.waitFor(() => expect(receive).toHaveBeenCalledWith({ type: 'transport_error', message: 'connection lost' }));
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('preserves the pet JSON fallback without opening a reader', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({ fallback: true, text: 'hello', quota_exceeded: true }), {
      headers: { 'Content-Type': 'application/json' },
    }));
    const receive = vi.fn();
    petsApi.bubble('id', receive);
    await vi.waitFor(() => expect(receive).toHaveBeenCalledWith({ type: 'fallback', fallback: true, text: 'hello', quota_exceeded: true }));
  });
});
