import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { apiFetch, request, sessionsApi } from './api';
import { tokenStorage } from './auth';

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => { resolve = yes; });
  return { promise, resolve };
}
const access = (user: string, expired = false) => `x.${btoa(JSON.stringify({ sub: user, exp: expired ? 1 : Math.floor(Date.now() / 1000) + 3600 }))}.x`;
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

beforeEach(() => { tokenStorage.clear(); vi.stubGlobal('fetch', vi.fn()); });
afterEach(() => { vi.unstubAllGlobals(); tokenStorage.clear(); });

describe('request identity isolation', () => {
  it('rejects late refresh without restoring the logged-out identity', async () => {
    const late = deferred<Response>();
    tokenStorage.set(access('A', true), 'refresh-A');
    vi.mocked(fetch).mockReturnValueOnce(late.promise);
    const pending = apiFetch('/settings/profile');
    const rejected = expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    tokenStorage.clear();
    tokenStorage.set(access('B'), 'refresh-B');
    late.resolve(response({ access_token: access('A'), refresh_token: 'rotated-A' }));
    await rejected;
    expect(tokenStorage.getRefresh()).toBe('refresh-B');
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('does not let an old refresh clear the new in-flight refresh deduplication', async () => {
    const old = deferred<Response>();
    const current = deferred<Response>();
    vi.mocked(fetch).mockReturnValueOnce(old.promise).mockReturnValueOnce(current.promise).mockImplementation(async () => response({ ok: true }));
    tokenStorage.set(access('A', true), 'refresh-A');
    const a = apiFetch('/a');
    const rejected = expect(a).rejects.toMatchObject({ name: 'AbortError' });
    tokenStorage.clear();
    tokenStorage.set(access('B', true), 'refresh-B');
    const b = apiFetch('/b');
    old.resolve(response({ access_token: access('A'), refresh_token: 'rotated-A' }));
    await rejected;
    const b2 = apiFetch('/b2');
    expect(fetch).toHaveBeenCalledTimes(2);
    current.resolve(response({ access_token: access('B'), refresh_token: 'rotated-B' }));
    await Promise.all([b, b2]);
    expect(tokenStorage.getRefresh()).toBe('rotated-B');
    expect(vi.mocked(fetch).mock.calls.filter(([url]) => url === '/api/auth/refresh')).toHaveLength(2);
  });

  it('discards a slow response body even if headers arrived before logout', async () => {
    tokenStorage.set(access('A'), 'refresh-A');
    const body = deferred<unknown>();
    const result = response({});
    const reading = vi.spyOn(result, 'json').mockReturnValue(body.promise);
    vi.mocked(fetch).mockResolvedValue(result);
    const pending = request('/private-data');
    const rejected = expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    await vi.waitFor(() => expect(reading).toHaveBeenCalled());
    tokenStorage.clear();
    tokenStorage.set(access('B'), 'refresh-B');
    body.resolve({ secret: 'A' });
    await rejected;
    expect(tokenStorage.getRefresh()).toBe('refresh-B');
  });

  it('does not retry an old 401 using the new identity', async () => {
    tokenStorage.set(access('A'), 'refresh-A');
    const late = deferred<Response>();
    vi.mocked(fetch).mockReturnValueOnce(late.promise);
    const pending = apiFetch('/private-write', { method: 'POST' });
    const rejected = expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    tokenStorage.clear();
    tokenStorage.set(access('B'), 'refresh-B');
    late.resolve(response({}, 401));
    await rejected;
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('does not cache a failed refresh when no refresh credential exists', async () => {
    vi.mocked(fetch).mockImplementation(async (url) => url === '/api/auth/refresh'
      ? response({ access_token: access('B'), refresh_token: 'rotated-B' }) : response({}));
    await apiFetch('/initial');
    tokenStorage.set(access('B', true), 'refresh-B');
    await apiFetch('/later');
    expect(tokenStorage.getRefresh()).toBe('rotated-B');
  });

  it('aborts old SSE and drops buffered callbacks after an identity change', async () => {
    tokenStorage.set(access('A'), 'refresh-A');
    let source!: ReadableStreamDefaultController<Uint8Array>;
    const stream = new ReadableStream<Uint8Array>({ start(controller) { source = controller; } });
    vi.mocked(fetch).mockResolvedValue(new Response(stream));
    const receive = vi.fn();
    const controller = sessionsApi.watchEvents('old-session', receive);
    await vi.waitFor(() => expect(fetch).toHaveBeenCalled());
    const signal = vi.mocked(fetch).mock.calls[0][1]?.signal;
    tokenStorage.clear();
    source.enqueue(new TextEncoder().encode('data: {"type":"text","content":"A secret"}\n\n'));
    source.close();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(signal?.aborted).toBe(true);
    expect(receive).not.toHaveBeenCalled();
    controller.abort();
  });
});
