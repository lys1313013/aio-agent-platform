import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { apiFetch, chatApi, graphKnowledgeApi, petsApi, skillsApi, workspacesApi } from '../api';
import { tokenStorage } from '../auth';

const access = (name: string) => `x.${btoa(JSON.stringify({ sub: 'same-user', name, exp: Math.floor(Date.now() / 1000) + 3600 }))}.x`;
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'Content-Type': 'application/json' },
});

beforeEach(() => {
  tokenStorage.clear();
  tokenStorage.set(access('old'), 'refresh-old');
  vi.stubGlobal('fetch', vi.fn());
});
afterEach(() => { vi.unstubAllGlobals(); tokenStorage.clear(); });

describe('shared authenticated transport', () => {
  it('preserves one retry for a JSON mutation rejected by authentication', async () => {
    const requests: Array<{ url: string; auth: string | null; body: BodyInit | null | undefined }> = [];
    vi.mocked(fetch).mockImplementation(async (url, options) => {
      requests.push({ url: String(url), auth: new Headers(options?.headers).get('Authorization'), body: options?.body });
      if (String(url).endsWith('/auth/refresh')) return json({ access_token: access('new'), refresh_token: 'refresh-new' });
      return requests.length === 1 ? json({ detail: 'expired' }, 401) : json({ saved: true });
    });
    const response = await apiFetch('/settings/profile', { method: 'PUT', body: '{"name":"changed"}' });
    expect(await response.json()).toEqual({ saved: true });
    expect(requests.map((item) => item.url)).toEqual(['/api/settings/profile', '/api/auth/refresh', '/api/settings/profile']);
    expect(requests[0].auth).toBe(`Bearer ${access('old')}`);
    expect(requests[2].auth).toBe(`Bearer ${access('new')}`);
    expect(requests[2].body).toBe(requests[0].body);
  });

  it('does not repeat a mutation when the network outcome is unknown', async () => {
    vi.mocked(fetch).mockRejectedValue(new TypeError('network interrupted'));
    await expect(apiFetch('/chat/runs/run/stop', { method: 'POST' })).rejects.toThrow('network interrupted');
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it.each([
    ['chat', (file: File) => chatApi.uploadAttachment(file, 'session'), '/api/chat/attachments'],
    ['knowledge', (file: File) => graphKnowledgeApi.uploadDocument('kb', file), '/api/admin/graph-knowledge-bases/kb/documents/upload'],
    ['skills', (file: File) => skillsApi.importFromZip(file), '/api/skills/import'],
    ['workspace', (file: File) => workspacesApi.uploadFile('workspace', 'dir/a.txt', file), '/api/workspaces/workspace/files/content?path=dir%2Fa.txt'],
    ['pets', (file: File) => petsApi.upload(file, { idle: 0 }), '/api/pets/packages'],
  ] as const)('%s uploads use auth without overriding multipart boundaries', async (_name, upload, path) => {
    vi.mocked(fetch).mockResolvedValue(json({ ok: true }));
    const file = new File(['contents'], 'a.txt');
    await upload(file);
    const [url, options] = vi.mocked(fetch).mock.calls[0];
    expect(url).toBe(path);
    expect(new Headers(options?.headers).get('Authorization')).toBe(`Bearer ${access('old')}`);
    expect(new Headers(options?.headers).has('Content-Type')).toBe(false);
    expect(options?.body).toBeInstanceOf(FormData);
    expect((options?.body as FormData).get('file')).toBe(file);
  });

  it('refreshes credentials but never automatically replays a multipart upload', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(json({ detail: 'expired' }, 401))
      .mockResolvedValueOnce(json({ access_token: access('new'), refresh_token: 'refresh-new' }));
    await expect(chatApi.uploadAttachment(new File(['a'], 'a.png'))).rejects.toMatchObject({ status: 401 });
    expect(vi.mocked(fetch).mock.calls.map(([url]) => url)).toEqual(['/api/chat/attachments', '/api/auth/refresh']);
    expect(tokenStorage.getRefresh()).toBe('refresh-new');
  });

  it.each([
    [{ detail: 'not allowed' }, 'not allowed'],
    [{ detail: { message: 'conflict' } }, 'conflict'],
    [{ detail: [{ msg: 'field required' }, { msg: 'invalid value' }] }, 'field required; invalid value'],
  ])('normalizes API error details consistently for multipart requests', async (body, message) => {
    vi.mocked(fetch).mockResolvedValue(json(body, 422));
    await expect(skillsApi.importFromZip(new File(['a'], 'a.zip'))).rejects.toMatchObject({ status: 422, message });
  });

  it('rejects a slow download body after an identity change', async () => {
    let finish!: (value: Blob) => void;
    const response = json({});
    const reading = vi.spyOn(response, 'blob').mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    vi.mocked(fetch).mockResolvedValue(response);
    const pending = workspacesApi.downloadFile('id', 'file.txt');
    const rejected = expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    await vi.waitFor(() => expect(reading).toHaveBeenCalled());
    tokenStorage.clear();
    finish(new Blob(['old private data']));
    await rejected;
  });

  it('does not fetch or refresh for an already cancelled request', async () => {
    const controller = new AbortController();
    controller.abort();
    await expect(apiFetch('/data', { signal: controller.signal })).rejects.toMatchObject({ name: 'AbortError' });
    expect(fetch).not.toHaveBeenCalled();
  });

  it('rejects a slow response body when the caller cancels after headers arrive', async () => {
    const controller = new AbortController();
    let finish!: (value: unknown) => void;
    const response = json({});
    vi.spyOn(response, 'json').mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    vi.mocked(fetch).mockResolvedValue(response);
    const scopedResponse = await apiFetch('/data', { signal: controller.signal });
    const pending = scopedResponse.json();
    const rejected = expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    controller.abort();
    finish({ outdated: true });
    await rejected;
  });
});
