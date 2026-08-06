const ACCESS_KEY = 'aio_access_token';
const REFRESH_KEY = 'aio_refresh_token';

export function decodeJwtPayload(token: string): { role?: string; sub?: string } | null {
  try {
    const payload = token.split('.')[1];
    const decoded = atob(payload.replace(/-/g, '+').replace(/_/g, '/'));
    return JSON.parse(decoded);
  } catch {
    return null;
  }
}

export function getUserRole(): string | null {
  const token = localStorage.getItem(ACCESS_KEY);
  if (!token) return null;
  const payload = decodeJwtPayload(token);
  return payload?.role ?? null;
}

export const tokenStorage = {
  getAccess(): string | null {
    return localStorage.getItem(ACCESS_KEY);
  },

  getRefresh(): string | null {
    return localStorage.getItem(REFRESH_KEY);
  },

  set(access: string, refresh: string): void {
    localStorage.setItem(ACCESS_KEY, access);
    localStorage.setItem(REFRESH_KEY, refresh);
  },

  clear(): void {
    localStorage.removeItem(ACCESS_KEY);
    localStorage.removeItem(REFRESH_KEY);
  },

  hasTokens(): boolean {
    return !!localStorage.getItem(ACCESS_KEY) && !!localStorage.getItem(REFRESH_KEY);
  },
};

/**
 * Attach the current access token to a chat-attachment image URL so an
 * `<img>` tag (which cannot set an Authorization header) can load it. The
 * backend `/api/public/images/{key}` endpoint validates the token and the
 * key's ownership. Non-public URLs are returned unchanged.
 */
export function withImageAuth(url: string): string {
  if (!url.includes('/api/public/images/')) return url;
  const token = localStorage.getItem(ACCESS_KEY);
  if (!token) return url;
  const sep = url.includes('?') ? '&' : '?';
  return `${url}${sep}token=${encodeURIComponent(token)}`;
}
