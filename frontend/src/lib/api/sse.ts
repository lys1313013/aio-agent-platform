import { assertAuthGeneration, getAuthGeneration, getAuthSignal, scopeAuthCallback } from '../authSession';
import { ApiError, apiFetch } from './transport';

export type StreamEvent = Record<string, unknown>;

/** Shared JSON SSE decoder. A reader and every buffered event share one auth lifetime. */
export async function readJsonEvents<T>(
  response: Response,
  onEvent: (event: T) => void,
  signal?: AbortSignal,
): Promise<void> {
  const generation = getAuthGeneration();
  const scopedSignal = signal ? AbortSignal.any([signal, getAuthSignal()]) : getAuthSignal();
  scopedSignal.throwIfAborted();
  if (!response.body) throw new ApiError(response.status, '响应缺少事件流');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let data: string[] = [];
  let eventName = '';
  let complete = false;

  const dispatch = () => {
    if (data.length) {
      let value: unknown;
      try {
        value = JSON.parse(data.join('\n'));
      } catch {
        data = [];
        eventName = '';
        return;
      }
      assertAuthGeneration(generation);
      scopedSignal.throwIfAborted();
      if (value && typeof value === 'object' && !Array.isArray(value)) {
        if (eventName && !('type' in value)) value = { ...value, type: eventName };
        onEvent(value as T);
      }
    }
    data = [];
    eventName = '';
  };
  const line = (value: string) => {
    if (!value) { dispatch(); return; }
    if (value.startsWith(':')) return;
    const colon = value.indexOf(':');
    const field = colon < 0 ? value : value.slice(0, colon);
    const content = colon < 0 ? '' : value.slice(colon + 1).replace(/^ /, '');
    if (field === 'data') data.push(content);
    else if (field === 'event') eventName = content;
  };
  const flushLines = (eof = false) => {
    while (true) {
      const end = buffer.search(/[\r\n]/);
      if (end < 0) break;
      if (!eof && buffer[end] === '\r' && end === buffer.length - 1) break;
      const width = buffer.slice(end, end + 2) === '\r\n' ? 2 : 1;
      const value = buffer.slice(0, end);
      buffer = buffer.slice(end + width);
      line(value);
    }
    if (eof) {
      if (buffer) line(buffer);
      buffer = '';
      dispatch();
    }
  };
  const cancel = () => { void reader.cancel().catch(() => {}); };
  scopedSignal.addEventListener('abort', cancel, { once: true });
  try {
    while (true) {
      const result = await reader.read();
      assertAuthGeneration(generation);
      scopedSignal.throwIfAborted();
      if (result.done) break;
      buffer += decoder.decode(result.value, { stream: true });
      flushLines();
    }
    buffer += decoder.decode();
    flushLines(true);
    complete = true;
  } finally {
    scopedSignal.removeEventListener('abort', cancel);
    if (!complete) await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

export function openJsonEventStream(
  path: string,
  onEvent: (event: StreamEvent) => void,
  options: RequestInit = {},
  transportError: 'error' | 'transport_error' = 'transport_error',
): AbortController {
  const controller = new AbortController();
  const signal = AbortSignal.any([controller.signal, getAuthSignal(), ...(options.signal ? [options.signal] : [])]);
  const receive = scopeAuthCallback((event: StreamEvent) => {
    if (!signal.aborted) onEvent(event);
  });
  void (async () => {
    const response = await apiFetch(path, { ...options, signal }, { authRetry: 'never' });
    await readJsonEvents(response, receive, signal);
    receive({ type: 'closed' });
  })().catch((error: unknown) => {
    if (error instanceof DOMException && error.name === 'AbortError') return;
    const message = error instanceof Error ? error.message : '网络请求失败';
    receive({ type: error instanceof ApiError ? 'error' : transportError, message });
    if (error instanceof ApiError) receive({ type: 'closed' });
  });
  return controller;
}
