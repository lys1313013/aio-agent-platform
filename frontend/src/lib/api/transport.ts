import { tokenStorage } from '../auth';
import { assertAuthGeneration, getAuthGeneration, getAuthSignal, onAuthSessionChange } from '../authSession';
import type { TokenPair } from '../types';

export const API_BASE = '/api';

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = 'ApiError';
  }
}

/** Credentials and response bodies belong to the identity that started the request. */
async function fetchWithAuthScope(input: RequestInfo | URL, options: RequestInit): Promise<Response> {
  const generation = getAuthGeneration();
  const signal = options.signal ? AbortSignal.any([options.signal, getAuthSignal()]) : getAuthSignal();
  const response = await fetch(input, { ...options, signal });
  assertAuthGeneration(generation);
  signal.throwIfAborted();
  const bodyMethods = new Set(['json', 'text', 'blob', 'arrayBuffer', 'formData']);
  return new Proxy(response, {
    get(target, property) {
      const value = Reflect.get(target, property, target);
      if (typeof value !== 'function') return value;
      if (!bodyMethods.has(String(property))) return value.bind(target);
      return async (...args: unknown[]) => {
        const result: unknown = await value.apply(target, args);
        assertAuthGeneration(generation);
        signal.throwIfAborted();
        return result;
      };
    },
  });
}

let refreshPromise: Promise<boolean> | null = null;
const AUTH_PATHS = ['/auth/login', '/auth/register', '/auth/refresh', '/auth/logout'];
onAuthSessionChange(() => { refreshPromise = null; });

function isTokenExpiringSoon(token: string | null): boolean {
  if (!token) return true;
  try {
    const payload = JSON.parse(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')));
    return typeof payload.exp !== 'number' || payload.exp - Date.now() / 1000 < 60;
  } catch {
    return true;
  }
}

async function refreshAccessToken(): Promise<boolean> {
  if (refreshPromise) return refreshPromise;
  const refreshToken = tokenStorage.getRefresh();
  if (!refreshToken) return false;
  const generation = getAuthGeneration();
  const pending = (async () => {
    try {
      const response = await fetchWithAuthScope(`${API_BASE}/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: refreshToken }),
      });
      if (!response.ok) return false;
      const data: TokenPair = await response.json();
      assertAuthGeneration(generation);
      if (tokenStorage.getRefresh() !== refreshToken) {
        throw new DOMException('登录凭据已变更', 'AbortError');
      }
      tokenStorage.set(data.access_token, data.refresh_token);
      return true;
    } catch (error) {
      assertAuthGeneration(generation);
      if (error instanceof DOMException && error.name === 'AbortError') throw error;
      return false;
    }
  })();
  refreshPromise = pending;
  try {
    return await pending;
  } finally {
    if (refreshPromise === pending) refreshPromise = null;
  }
}

function forceLogout(): never {
  tokenStorage.clear();
  window.location.href = '/login';
  throw new ApiError(401, 'Session expired');
}

function detailMessage(detail: unknown): string | undefined {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map(detailMessage).filter(Boolean).join('; ') || undefined;
  if (detail && typeof detail === 'object') {
    const value = detail as Record<string, unknown>;
    return detailMessage(value.message ?? value.msg ?? value.detail);
  }
  return undefined;
}

async function responseError(response: Response, generation: number): Promise<ApiError> {
  let message = response.statusText || `HTTP ${response.status}`;
  try {
    const body: unknown = await response.json();
    message = detailMessage(body) || message;
  } catch {
    // Non-JSON bodies are common for proxy errors; identity errors still escape below.
  }
  assertAuthGeneration(generation);
  return new ApiError(response.status, message);
}

export interface RequestPolicy {
  /** A 401 is rejected by auth before execution. Never retry transport failures. */
  authRetry?: 'unauthorized' | 'never';
}

/** One authenticated transport for JSON, multipart, downloads and initial SSE responses. */
export async function apiFetch(
  path: string,
  options: RequestInit = {},
  policy: RequestPolicy = {},
): Promise<Response> {
  const generation = getAuthGeneration();
  const signal = options.signal ? AbortSignal.any([options.signal, getAuthSignal()]) : getAuthSignal();
  const isAuthPath = AUTH_PATHS.some((authPath) => path === authPath || path.startsWith(`${authPath}?`));
  signal.throwIfAborted();
  if (!isAuthPath && isTokenExpiringSoon(tokenStorage.getAccess())) {
    const refreshed = await refreshAccessToken();
    assertAuthGeneration(generation);
    if (!refreshed && tokenStorage.getRefresh()) forceLogout();
  }

  assertAuthGeneration(generation);
  signal.throwIfAborted();
  const headers = new Headers(options.headers);
  // Multipart boundaries and binary content types belong to the browser/caller.
  if (typeof options.body === 'string' && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  const token = tokenStorage.getAccess();
  if (token) headers.set('Authorization', `Bearer ${token}`);
  let response = await fetchWithAuthScope(`${API_BASE}${path}`, { ...options, headers, signal });

  if (response.status === 401 && !isAuthPath) {
    const refreshed = await refreshAccessToken();
    assertAuthGeneration(generation);
    signal.throwIfAborted();
    if (!refreshed) forceLogout();
    if (policy.authRetry === 'never') {
      // The caller decides whether a mutation or task-start is safe to repeat.
      throw new ApiError(401, '登录凭据已更新，请重试本次操作');
    }
    headers.set('Authorization', `Bearer ${tokenStorage.getAccess() || ''}`);
    response = await fetchWithAuthScope(`${API_BASE}${path}`, { ...options, headers, signal });
    if (response.status === 401) forceLogout();
  }
  if (!response.ok) throw await responseError(response, generation);
  return response;
}

export async function request<T>(path: string, options: RequestInit = {}, policy: RequestPolicy = {}): Promise<T> {
  const response = await apiFetch(path, options, policy);
  return response.status === 204 ? undefined as T : response.json();
}

export function uploadForm<T>(path: string, body: FormData): Promise<T> {
  return request<T>(path, { method: 'POST', body }, { authRetry: 'never' });
}

export async function downloadBlob(path: string): Promise<Blob> {
  return (await apiFetch(path)).blob();
}

export function queryString(query: object): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== '') params.set(key, String(value));
  }
  const value = params.toString();
  return value ? `?${value}` : '';
}
