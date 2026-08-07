import { tokenStorage } from './auth';
import type {
  TokenPair,
  Agent,
  AgentStats,
  Session,
  SessionDetail,
  ChatAttachment,
  ChatResponse,
  ChatRequest,
  Memory,
  MemoryLayer,
  MemoryListResponse,
  MemorySearchResult,
  Skill,
  SkillListResponse,
  SkillSearchResult,
  SkillsShImportResult,
  SkillsShResolveResult,
  SkillsShSearchItem,
  SkillVersion,
  SkillFile,
  ConfirmationRequest,
  ConfirmationResponse,
  RemoteTool,
  RemoteToolCreate,
  RemoteToolUpdate,
  RemoteToolTestResult,
  CronJob,
  CronJobListResponse,
  CronJobRunListResponse,
  Channel,
  ChannelCreate,
  ChannelUpdate,
  ChannelBinding,
  PetPackage,
  PetVisibility,
  UserPet,
  CommandMeta,
  ModelMeta,
} from './types';

const API_BASE = '/api';

/** Custom error for API failures */
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

// ---- Token refresh logic ----

/** Shared promise to deduplicate concurrent refresh calls */
let refreshPromise: Promise<boolean> | null = null;

/** Paths that should NOT trigger auto-refresh (to avoid infinite loops) */
const AUTH_PATHS = ['/auth/login', '/auth/register', '/auth/refresh', '/auth/logout'];

/**
 * Check whether a JWT access token is expired or about to expire.
 * Returns true if the token expires within the next 60 seconds.
 */
function isTokenExpiringSoon(token: string | null): boolean {
  if (!token) return true;
  try {
    const payload = JSON.parse(atob(token.split('.')[1]));
    const exp = payload.exp as number | undefined;
    if (!exp) return true;
    // Refresh if token expires within 60 seconds
    return exp - Math.floor(Date.now() / 1000) < 60;
  } catch {
    return true;
  }
}

/**
 * Call the refresh endpoint to get a new token pair.
 * Concurrent callers share a single in-flight request.
 */
async function refreshAccessToken(): Promise<boolean> {
  if (refreshPromise) return refreshPromise;

  refreshPromise = (async () => {
    const refreshToken = tokenStorage.getRefresh();
    if (!refreshToken) return false;

    try {
      const resp = await fetch(`${API_BASE}/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: refreshToken }),
      });

      if (!resp.ok) return false;

      const data: TokenPair = await resp.json();
      tokenStorage.set(data.access_token, data.refresh_token);
      return true;
    } catch {
      return false;
    } finally {
      refreshPromise = null;
    }
  })();

  return refreshPromise;
}

/** Force logout when refresh fails */
function forceLogout(): never {
  tokenStorage.clear();
  window.location.href = '/login';
  throw new ApiError(401, 'Session expired');
}

/** Fetch wrapper with automatic JWT injection and token refresh */
async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const isAuthPath = AUTH_PATHS.some((p) => path.startsWith(p));

  // Proactively refresh if access token is about to expire
  if (!isAuthPath && isTokenExpiringSoon(tokenStorage.getAccess())) {
    const refreshed = await refreshAccessToken();
    if (!refreshed && tokenStorage.getRefresh()) {
      // Refresh token exists but refresh failed — session is truly over
      forceLogout();
    }
  }

  const token = tokenStorage.getAccess();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...((options.headers as Record<string, string>) || {}),
  };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  let resp = await fetch(`${API_BASE}${path}`, { ...options, headers });

  // Token expired or invalid — attempt refresh + retry once
  if (resp.status === 401 && !isAuthPath) {
    const refreshed = await refreshAccessToken();
    if (refreshed) {
      // Retry with the new access token
      const newToken = tokenStorage.getAccess();
      headers['Authorization'] = `Bearer ${newToken}`;
      resp = await fetch(`${API_BASE}${path}`, { ...options, headers });
    }
    // Still 401 after refresh — give up
    if (resp.status === 401) {
      forceLogout();
    }
  }

  if (!resp.ok) {
    let message = resp.statusText;
    try {
      const body = await resp.json();
      message = body.detail || message;
    } catch {
      /* ignore parse error */
    }
    throw new ApiError(resp.status, message);
  }

  if (resp.status === 204) return undefined as T;
  return resp.json();
}

// ---- Auth ----

export const authApi = {
  register(username: string, email: string, password: string, tenantName?: string) {
    return request<TokenPair>('/auth/register', {
      method: 'POST',
      body: JSON.stringify({ username, email, password, tenant_name: tenantName }),
    });
  },

  login(usernameOrEmail: string, password: string) {
    return request<TokenPair>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username_or_email: usernameOrEmail, password }),
    });
  },

  logout(refreshToken: string) {
    return request<void>('/auth/logout', {
      method: 'POST',
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
  },
};

// ---- Sessions ----

export const sessionsApi = {
  list(agentId?: string | null) {
    return request<Session[]>(agentId ? `/sessions?agent_id=${agentId}` : '/sessions');
  },

  get(id: string) {
    return request<SessionDetail>(`/sessions/${id}`);
  },

  getStatus(id: string) {
    return request<import('./types').SessionStatus>(`/sessions/${id}/status`);
  },

  /**
   * Replay a running channel task's agent events via SSE (GET /sessions/{id}/events).
   * Fires `onEvent` per event; emits `{ type: 'closed' }` on normal EOF so the
   * caller can finalize streaming state. Returns an AbortController for cancel.
   */
  watchEvents(id: string, onEvent: (event: Record<string, unknown>) => void): AbortController {
    const controller = new AbortController();

    (async () => {
      // Ensure access token is fresh before opening the stream
      if (isTokenExpiringSoon(tokenStorage.getAccess())) {
        const refreshed = await refreshAccessToken();
        if (!refreshed && tokenStorage.getRefresh()) {
          onEvent({ type: 'error', message: 'Session expired' });
          forceLogout();
          return;
        }
      }

      const token = tokenStorage.getAccess() || '';
      const resp = await fetch(`${API_BASE}/sessions/${id}/events`, {
        headers: { Authorization: `Bearer ${token}` },
        signal: controller.signal,
      });

      if (!resp.ok || !resp.body) {
        onEvent({ type: 'error', message: resp.statusText });
        return;
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      const flush = () => {
        if (buffer.trim().startsWith('data: ')) {
          try {
            onEvent(JSON.parse(buffer.trim().slice(6)));
          } catch { /* ignore malformed JSON */ }
        }
        buffer = '';
      };

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split('\n\n');
        buffer = parts.pop() || '';
        for (const part of parts) {
          const line = part.trim();
          if (!line.startsWith('data: ')) continue; // heartbeat comments
          try {
            onEvent(JSON.parse(line.slice(6)));
          } catch { /* ignore malformed JSON */ }
        }
      }
      flush();
      onEvent({ type: 'closed' });
    })().catch((err) => {
      if (err.name !== 'AbortError') {
        onEvent({ type: 'error', message: err.message || '网络请求失败' });
      }
    });

    return controller;
  },

  create(title?: string, agentId?: string | null, workspaceId?: string | null) {
    return request<Session>('/sessions', {
      method: 'POST',
      body: JSON.stringify({ title, agent_id: agentId, workspace_id: workspaceId }),
    });
  },

  rename(id: string, title: string) {
    return request<Session>(`/sessions/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ title }),
    });
  },

  pin(id: string, isPinned: boolean) {
    return request<Session>(`/sessions/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ is_pinned: isPinned }),
    });
  },

  archive(id: string, isArchived: boolean) {
    return request<Session>(`/sessions/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ is_archived: isArchived }),
    });
  },

  delete(id: string) {
    return request<void>(`/sessions/${id}`, { method: 'DELETE' });
  },
};

// ---- Chat ----

export const chatApi = {
  send(req: ChatRequest) {
    return request<ChatResponse>('/chat', {
      method: 'POST',
      body: JSON.stringify(req),
    });
  },

  /**
   * Streaming chat via SSE (POST /api/chat/stream).
   * Calls `onEvent` for each SSE event, returns an AbortController
   * so the caller can cancel the request.
   */
  stream(
    req: ChatRequest,
    onEvent: (event: Record<string, unknown>) => void,
  ): AbortController {
    const controller = new AbortController();

    (async () => {
      // Ensure access token is fresh before opening the stream
      if (isTokenExpiringSoon(tokenStorage.getAccess())) {
        const refreshed = await refreshAccessToken();
        if (!refreshed && tokenStorage.getRefresh()) {
          onEvent({ type: 'error', message: 'Session expired' });
          forceLogout();
        }
      }

      const token = tokenStorage.getAccess() || '';
      const resp = await fetch(`${API_BASE}/chat/stream`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify(req),
        signal: controller.signal,
      });

      if (!resp.ok || !resp.body) {
        let errMsg = resp.statusText;
        try {
          const body = await resp.json();
          // FastAPI validation errors return detail as an array of objects
          const detail = body.detail;
          if (Array.isArray(detail)) {
            errMsg = detail.map((d: Record<string, unknown>) => d.msg || JSON.stringify(d)).join('; ') || errMsg;
          } else if (typeof detail === 'string') {
            errMsg = detail;
          }
        } catch { /* ignore */ }
        onEvent({ type: 'error', message: errMsg });
        return;
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        // SSE events are separated by double newlines
        const parts = buffer.split('\n\n');
        buffer = parts.pop() || ''; // keep incomplete chunk

        for (const part of parts) {
          const line = part.trim();
          if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6));
              onEvent(data);
            } catch {
              /* ignore malformed JSON */
            }
          }
        }
      }

      // Process remaining buffer
      if (buffer.trim().startsWith('data: ')) {
        try {
          const data = JSON.parse(buffer.trim().slice(6));
          onEvent(data);
        } catch { /* ignore */ }
      }
    })().catch((err) => {
      if (err.name !== 'AbortError') {
        onEvent({ type: 'error', message: err.message || '网络请求失败' });
      }
    });

    return controller;
  },

  /**
   * Upload an image attachment for a future chat message.
   * Returns metadata including a presigned download URL.
   */
  uploadAttachment(
    file: File,
    sessionId?: string | null,
  ): Promise<ChatAttachment> {
    return (async () => {
      // Ensure access token is fresh before upload
      if (isTokenExpiringSoon(tokenStorage.getAccess())) {
        const refreshed = await refreshAccessToken();
        if (!refreshed && tokenStorage.getRefresh()) {
          throw new ApiError(401, 'Session expired');
        }
      }

      const form = new FormData();
      form.append('file', file);
      if (sessionId) form.append('session_id', sessionId);

      const token = tokenStorage.getAccess() || '';
      const resp = await fetch(`${API_BASE}/chat/attachments`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
        // Do NOT set Content-Type — browser auto-sets multipart boundary
        body: form,
      });
      if (!resp.ok) {
        let errMsg = resp.statusText;
        try {
          const body = await resp.json();
          errMsg = body.detail || errMsg;
        } catch { /* ignore */ }
        throw new ApiError(resp.status, errMsg);
      }
      return resp.json();
    })();
  },

  /**
   * Upload a file to the workspace for agent processing.
   * Returns file metadata including workspace_path.
   */
  uploadFile(
    file: File,
    sessionId: string,
  ): Promise<import('./types').FileAttachmentRef> {
    return (async () => {
      if (isTokenExpiringSoon(tokenStorage.getAccess())) {
        const refreshed = await refreshAccessToken();
        if (!refreshed && tokenStorage.getRefresh()) {
          throw new ApiError(401, 'Session expired');
        }
      }

      const form = new FormData();
      form.append('file', file);
      form.append('session_id', sessionId);

      const token = tokenStorage.getAccess() || '';
      const resp = await fetch(`${API_BASE}/chat/files`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
        body: form,
      });
      if (!resp.ok) {
        let errMsg = resp.statusText;
        try {
          const body = await resp.json();
          errMsg = body.detail || errMsg;
        } catch { /* ignore */ }
        throw new ApiError(resp.status, errMsg);
      }
      return resp.json();
    })();
  },

  /** Build the WebSocket URL for a session (legacy) */
  wsUrl(sessionId: string): string {
    const token = tokenStorage.getAccess() || '';
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    return `${protocol}//${host}${API_BASE}/chat/ws/${sessionId}?token=${encodeURIComponent(token)}`;
  },
};

// ---- Super admin: Tenants ----

export interface Tenant {
  id: string;
  name: string;
  slug: string;
  is_active: boolean;
  users_count: number;
  agents_count: number;
  knowledge_bases_count: number;
  created_at: string;
}

export interface TenantUser {
  id: string;
  tenant_id: string;
  username: string;
  email: string;
  display_name: string | null;
  role: 'user' | 'admin' | 'superadmin';
  is_active: boolean;
  created_at: string;
}

export interface AdminUser {
  id: string;
  username: string;
  email: string;
  display_name: string | null;
  role: 'user' | 'admin' | 'superadmin';
  is_active: boolean;
  active_tenant_id: string;
  tenant_ids: string[];
  created_at: string;
}

export const tenantsApi = {
  list() {
    return request<Tenant[]>('/admin/tenants');
  },

  create(data: { name: string; slug: string }) {
    return request<Tenant>('/admin/tenants', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  update(id: string, data: { name?: string; slug?: string; is_active?: boolean }) {
    return request<Tenant>(`/admin/tenants/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  },

  delete(id: string) {
    return request<void>(`/admin/tenants/${id}`, { method: 'DELETE' });
  },

  listUsers(tenantId: string) {
    return request<TenantUser[]>(`/admin/tenants/${tenantId}/users`);
  },

  assignUsers(tenantId: string, userIds: string[]) {
    return request<{ message: string; added_count: number }>(`/admin/tenants/${tenantId}/users`, {
      method: 'PUT',
      body: JSON.stringify({ user_ids: userIds }),
    });
  },

  removeUser(tenantId: string, userId: string) {
    return request<void>(`/admin/tenants/${tenantId}/users/${userId}`, {
      method: 'DELETE',
    });
  },
};

export const usersApi = {
  list() {
    return request<AdminUser[]>('/admin/users');
  },

  create(data: {
    username: string;
    email: string;
    display_name?: string;
    password: string;
    role?: 'user' | 'admin';
    tenant_ids: string[];
    active_tenant_id?: string;
  }) {
    return request<AdminUser>('/admin/users', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  update(userId: string, data: {
    username?: string;
    email?: string;
    display_name?: string | null;
    password?: string;
    role?: 'user' | 'admin';
    is_active?: boolean;
  }) {
    return request<AdminUser>(`/admin/users/${userId}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  },
};

// ---- Settings ----

export const settingsApi = {
  // Profile
  getProfile() {
    return request<{
      username: string;
      email: string;
      display_name: string | null;
      tenant_id: string;
      tenant_name: string;
    }>('/settings/profile');
  },

  listTenants() {
    return request<Array<{
      id: string;
      name: string;
      slug: string;
      is_active: boolean;
      is_current: boolean;
    }>>('/settings/tenants');
  },

  switchTenant(tenantId: string) {
    return request<{
      username: string;
      email: string;
      display_name: string | null;
      tenant_id: string;
      tenant_name: string;
    }>('/settings/active-tenant', {
      method: 'PUT',
      body: JSON.stringify({ tenant_id: tenantId }),
    });
  },

  updateProfile(data: { display_name?: string; username?: string; email?: string }) {
    return request<{
      username: string;
      email: string;
      display_name: string | null;
      tenant_id: string;
      tenant_name: string;
    }>('/settings/profile', {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  },

  updatePassword(data: { current_password: string; new_password: string }) {
    return request<{ message: string }>('/settings/password', {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  },

  // Security config
  getSecurityConfig() {
    return request<{ trust_level: string }>('/settings/security');
  },

  updateSecurityConfig(trust_level: string) {
    return request<{ trust_level: string }>('/settings/security', {
      method: 'PUT',
      body: JSON.stringify({ trust_level }),
    });
  },

  // Personal portrait
  getPersonalPortrait() {
    return request<{ personal_portrait: string | null }>('/settings/personal-portrait');
  },

  updatePersonalPortrait(personal_portrait: string | null) {
    return request<{ personal_portrait: string | null }>('/settings/personal-portrait', {
      method: 'PUT',
      body: JSON.stringify({ personal_portrait }),
    });
  },

  // Portrait version history
  listPortraitVersions() {
    return request<{ versions: Array<{ id: string; content: string | null; source: string; created_at: string }> }>(
      '/settings/personal-portrait/versions',
    );
  },

  getPortraitVersion(versionId: string) {
    return request<{ id: string; content: string | null; source: string; created_at: string }>(
      `/settings/personal-portrait/versions/${versionId}`,
    );
  },

  restorePortraitVersion(versionId: string) {
    return request<{ personal_portrait: string | null }>(
      `/settings/personal-portrait/versions/${versionId}/restore`,
      { method: 'POST' },
    );
  },

  // Memory config
  getMemoryConfig() {
    return request<{
      top_k: number;
      compress_threshold: number;
    }>('/settings/memory');
  },

  updateMemoryConfig(data: {
    top_k?: number;
    compress_threshold?: number;
  }) {
    return request<{
      top_k: number;
      compress_threshold: number;
    }>('/settings/memory', {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  },
};

// ---- Admin: LLM Providers & Models ----

export interface LLMProvider {
  id: string;
  name: string;
  provider_type: string;
  base_url: string | null;
  has_api_key: boolean;
  is_active: boolean;
}

export interface LLMModel {
  id: string;
  provider_id: string;
  provider_name: string;
  name: string;
  model_name: string;
  is_multimodal: boolean;
  is_default: boolean;
  is_active: boolean;
}

export const adminApi = {
  // Providers
  listProviders() {
    return request<LLMProvider[]>('/admin/models/providers');
  },

  createProvider(data: {
    name: string;
    provider_type: string;
    base_url?: string;
    api_key?: string;
  }) {
    return request<LLMProvider>('/admin/models/providers', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  updateProvider(id: string, data: {
    name?: string;
    provider_type?: string;
    base_url?: string;
    api_key?: string;
    is_active?: boolean;
  }) {
    return request<LLMProvider>(`/admin/models/providers/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  },

  deleteProvider(id: string) {
    return request<{ message: string }>(`/admin/models/providers/${id}`, {
      method: 'DELETE',
    });
  },

  // Models
  listModels() {
    return request<LLMModel[]>('/admin/models');
  },

  createModel(data: { provider_id: string; name: string; model_name: string; is_multimodal?: boolean }) {
    return request<LLMModel>('/admin/models', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  updateModel(id: string, data: {
    provider_id?: string;
    name?: string;
    model_name?: string;
    is_multimodal?: boolean;
    is_active?: boolean;
  }) {
    return request<LLMModel>(`/admin/models/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  },

  deleteModel(id: string) {
    return request<{ message: string }>(`/admin/models/${id}`, {
      method: 'DELETE',
    });
  },

  setDefaultModel(id: string) {
    return request<LLMModel>(`/admin/models/${id}/default`, {
      method: 'PUT',
    });
  },

  fetchRemoteModels(providerId: string) {
    return request<{ models: string[] }>(
      `/admin/models/providers/${providerId}/fetch-models`,
      { method: 'POST' },
    );
  },

  batchCreateModels(providerId: string, models: string[]) {
    return request<LLMModel[]>('/admin/models/batch-create', {
      method: 'POST',
      body: JSON.stringify({ provider_id: providerId, models }),
    });
  },
};

// ---- Admin: MCP Servers ----

export interface McpTool {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
}

export interface McpServer {
  id: string;
  name: string;
  transport_type: 'sse' | 'streamable-http';
  url: string;
  headers: Record<string, string>;
  is_active: boolean;
  tool_prefix: string | null;
  timeout: number;
  status: string;
  last_error: string | null;
  tools_count: number;
  tools: McpTool[];
}

export const mcpApi = {
  list() {
    return request<McpServer[]>('/admin/mcp-servers');
  },

  get(id: string) {
    return request<McpServer>(`/admin/mcp-servers/${id}`);
  },

  create(data: {
    name: string;
    transport_type: 'sse' | 'streamable-http';
    url: string;
    headers?: Record<string, string>;
    tool_prefix?: string;
    timeout?: number;
    is_active?: boolean;
  }) {
    return request<McpServer>('/admin/mcp-servers', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  update(id: string, data: {
    name?: string;
    transport_type?: 'sse' | 'streamable-http';
    url?: string;
    headers?: Record<string, string>;
    is_active?: boolean;
    tool_prefix?: string;
    timeout?: number;
  }) {
    return request<McpServer>(`/admin/mcp-servers/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  },

  delete(id: string) {
    return request<{ message: string }>(`/admin/mcp-servers/${id}`, {
      method: 'DELETE',
    });
  },

  refresh(id: string) {
    return request<McpServer>(`/admin/mcp-servers/${id}/refresh`, {
      method: 'POST',
    });
  },

  listTools(id: string) {
    return request<McpTool[]>(`/admin/mcp-servers/${id}/tools`);
  },
};

// ---- Admin: Knowledge Bases ----

export interface KnowledgeBase {
  id: string;
  name: string;
  dataset_id: string;
  description: string | null;
  is_active: boolean;
  tenant_id: string;
  created_by: string;
  visibility: 'tenant' | 'private';
  can_edit: boolean;
}

export interface RagflowSettings {
  base_url: string;
  has_api_key: boolean;
}

export interface WebToolConfig {
  enabled: boolean;
  search_provider: 'auto' | 'duckduckgo' | 'brave' | 'tavily' | 'searxng';
  searxng_url: string;
  summary_enabled: boolean;
  cache_ttl_seconds: number;
  fetch_max_chars: number;
  has_brave_api_key: boolean;
  has_tavily_api_key: boolean;
  has_firecrawl_api_key: boolean;
}

export interface WebToolConfigUpdate {
  enabled?: boolean;
  search_provider?: string;
  brave_api_key?: string;
  tavily_api_key?: string;
  searxng_url?: string;
  firecrawl_api_key?: string;
  summary_enabled?: boolean;
  cache_ttl_seconds?: number;
  fetch_max_chars?: number;
}

export interface TestResult {
  success: boolean;
  message: string;
  records_count: number;
}

export interface RetrievalRecord {
  content: string;
  score: number;
  title: string | null;
  metadata: Record<string, unknown> | null;
}

export interface RetrievalResult {
  success: boolean;
  records: RetrievalRecord[];
  message?: string;
  query_time_ms?: number;
}

export const knowledgeApi = {
  list() {
    return request<KnowledgeBase[]>('/admin/knowledge-bases');
  },

  create(data: {
    name: string;
    dataset_id: string;
    description?: string;
    is_active?: boolean;
    visibility?: 'tenant' | 'private';
  }) {
    return request<KnowledgeBase>('/admin/knowledge-bases', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  update(id: string, data: {
    name?: string;
    dataset_id?: string;
    description?: string;
    is_active?: boolean;
    visibility?: 'tenant' | 'private';
  }) {
    return request<KnowledgeBase>(`/admin/knowledge-bases/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  },

  delete(id: string) {
    return request<{ message: string }>(`/admin/knowledge-bases/${id}`, {
      method: 'DELETE',
    });
  },

  test(id: string) {
    return request<TestResult>(`/admin/knowledge-bases/${id}/test`, {
      method: 'POST',
    });
  },

  retrieval(id: string, data: {
    query: string;
    top_k?: number;
    score_threshold?: number;
  }) {
    return request<RetrievalResult>(`/admin/knowledge-bases/${id}/retrieval`, {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },
};

export const ragflowSettingsApi = {
  get() {
    return request<RagflowSettings>('/admin/settings/ragflow');
  },

  update(data: { base_url?: string; api_key?: string }) {
    return request<RagflowSettings>('/admin/settings/ragflow', {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  },
};

export const webToolSettingsApi = {
  get() {
    return request<WebToolConfig>('/admin/settings/web');
  },

  update(data: WebToolConfigUpdate) {
    return request<WebToolConfig>('/admin/settings/web', {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  },
};

// ---- Admin: System Config ----

export interface AutoTitleConfig {
  model_id: string | null;
  prompt: string;
  default_prompt: string;
}

export const systemConfigApi = {
  getAutoTitle() {
    return request<AutoTitleConfig>('/admin/system-config/auto-title');
  },

  updateAutoTitle(data: { model_id?: string | null; prompt?: string }) {
    return request<AutoTitleConfig>('/admin/system-config/auto-title', {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  },
};

// ---- Admin: Remote Tools (HTTP Tools) ----

export const remoteToolsApi = {
  list() {
    return request<RemoteTool[]>('/admin/remote-tools');
  },

  get(id: string) {
    return request<RemoteTool>(`/admin/remote-tools/${id}`);
  },

  create(data: RemoteToolCreate) {
    return request<RemoteTool>('/admin/remote-tools', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  update(id: string, data: RemoteToolUpdate) {
    return request<RemoteTool>(`/admin/remote-tools/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  },

  delete(id: string) {
    return request<{ message: string }>(`/admin/remote-tools/${id}`, {
      method: 'DELETE',
    });
  },

  toggle(id: string) {
    return request<RemoteTool>(`/admin/remote-tools/${id}/toggle`, {
      method: 'PATCH',
    });
  },

  test(id: string, args: Record<string, unknown>) {
    return request<RemoteToolTestResult>(`/admin/remote-tools/${id}/test`, {
      method: 'POST',
      body: JSON.stringify({ arguments: args }),
    });
  },
};

// ---- Admin: Channels (IM 渠道接入) ----

export const channelsApi = {
  list() {
    return request<Channel[]>('/channels');
  },

  create(data: ChannelCreate) {
    return request<Channel>('/channels', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  update(id: string, data: ChannelUpdate) {
    return request<Channel>(`/channels/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  },

  enable(id: string) {
    return request<Channel>(`/channels/${id}/enable`, { method: 'POST' });
  },

  disable(id: string) {
    return request<Channel>(`/channels/${id}/disable`, { method: 'POST' });
  },

  delete(id: string) {
    return request<void>(`/channels/${id}`, { method: 'DELETE' });
  },

  bindings(id: string) {
    return request<ChannelBinding[]>(`/channels/${id}/bindings`);
  },
};

// ---- User: Channel Bindings ----

export const channelBindingsApi = {
  list() {
    return request<ChannelBinding[]>('/channel-bindings');
  },

  bind(code: string) {
    return request<{ message: string }>('/channel-bindings/bind', {
      method: 'POST',
      body: JSON.stringify({ code }),
    });
  },

  unbind(id: string) {
    return request<void>(`/channel-bindings/${id}`, { method: 'DELETE' });
  },
};

// ---- Memories ----

export const memoriesApi = {
  list(params?: { layer?: string; limit?: number; offset?: number }) {
    const searchParams = new URLSearchParams();
    if (params?.layer) searchParams.set('layer', params.layer);
    if (params?.limit) searchParams.set('limit', String(params.limit));
    if (params?.offset) searchParams.set('offset', String(params.offset));
    const qs = searchParams.toString();
    return request<MemoryListResponse>(`/memories${qs ? `?${qs}` : ''}`);
  },

  get(id: string) {
    return request<Memory>(`/memories/${id}`);
  },

  create(data: { layer: MemoryLayer; content: string; metadata?: Record<string, unknown> }) {
    return request<Memory>('/memories', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  update(
    id: string,
    data: { content?: string; layer?: MemoryLayer; metadata?: Record<string, unknown> },
  ) {
    return request<Memory>(`/memories/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  },

  delete(id: string) {
    return request<void>(`/memories/${id}`, { method: 'DELETE' });
  },

  deleteMany(ids: string[]) {
    return request<{ deleted: number }>('/memories/batch-delete', {
      method: 'POST',
      body: JSON.stringify({ ids }),
    });
  },

  stats() {
    return request<Record<'L1' | 'L2' | 'L3', number>>('/memories/stats');
  },

  search(query: string, params?: { layer?: string; top_k?: number }) {
    const searchParams = new URLSearchParams({ q: query });
    if (params?.layer) searchParams.set('layer', params.layer);
    if (params?.top_k) searchParams.set('top_k', String(params.top_k));
    return request<MemorySearchResult[]>(`/memories/search?${searchParams.toString()}`);
  },
};

// ---- Skills ----

export const skillsApi = {
  list(params?: { category?: string; is_active?: boolean; limit?: number; offset?: number }) {
    const searchParams = new URLSearchParams();
    if (params?.category) searchParams.set('category', params.category);
    if (params?.is_active !== undefined) searchParams.set('is_active', String(params.is_active));
    if (params?.limit) searchParams.set('limit', String(params.limit));
    if (params?.offset) searchParams.set('offset', String(params.offset));
    const qs = searchParams.toString();
    return request<SkillListResponse>(`/skills${qs ? `?${qs}` : ''}`);
  },

  get(id: string) {
    return request<Skill>(`/skills/${id}`);
  },

  create(data: {
    name: string;
    description?: string;
    content: string;
    tags?: string[];
    category?: string;
    trigger_condition?: string;
  }) {
    return request<Skill>('/skills', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  update(
    id: string,
    data: {
      name?: string;
      description?: string;
      content?: string;
      tags?: string[];
      category?: string;
      trigger_condition?: string;
      is_active?: boolean;
      is_public?: boolean;
    },
  ) {
    return request<Skill>(`/skills/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  },

  delete(id: string) {
    return request<void>(`/skills/${id}`, { method: 'DELETE' });
  },

  search(query: string, params?: { category?: string; top_k?: number }) {
    const searchParams = new URLSearchParams({ q: query });
    if (params?.category) searchParams.set('category', params.category);
    if (params?.top_k) searchParams.set('top_k', String(params.top_k));
    return request<SkillSearchResult[]>(`/skills/search?${searchParams.toString()}`);
  },

  listVersions(id: string) {
    return request<SkillVersion[]>(`/skills/${id}/versions`);
  },

  getVersion(id: string, version: number) {
    return request<{ skill_id: string; version: number; content: string; created_at: string }>(
      `/skills/${id}/versions/${version}`,
    );
  },

  download(id: string) {
    return (async () => {
      // Ensure token is fresh
      if (isTokenExpiringSoon(tokenStorage.getAccess())) {
        const refreshed = await refreshAccessToken();
        if (!refreshed && tokenStorage.getRefresh()) forceLogout();
      }
      const token = tokenStorage.getAccess();
      const resp = await fetch(`${API_BASE}/skills/${id}/download`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!resp.ok) throw new ApiError(resp.status, 'Download failed');
      return resp.blob();
    })();
  },

  // ---- skills.sh sync ----

  shSearch(query: string, limit = 20) {
    const sp = new URLSearchParams({ q: query, limit: String(limit) });
    return request<SkillsShSearchItem[]>(`/skills/sh/search?${sp.toString()}`);
  },

  shResolve(input: string) {
    const sp = new URLSearchParams({ url: input });
    return request<SkillsShResolveResult>(`/skills/sh/resolve?${sp.toString()}`);
  },

  shImport(entries: Array<{ source: string; skill_id: string }>) {
    return request<SkillsShImportResult>('/skills/sh/import', {
      method: 'POST',
      body: JSON.stringify({ entries }),
    });
  },

  // Import skill from zip
  importFromZip(file: File) {
    return (async () => {
      if (isTokenExpiringSoon(tokenStorage.getAccess())) {
        const refreshed = await refreshAccessToken();
        if (!refreshed && tokenStorage.getRefresh()) forceLogout();
      }
      const token = tokenStorage.getAccess();
      const form = new FormData();
      form.append('file', file);
      const resp = await fetch(`${API_BASE}/skills/import`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
        body: form,
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: 'Import failed' }));
        throw new ApiError(resp.status, err.detail || 'Import failed');
      }
      return resp.json() as Promise<Skill>;
    })();
  },

  // File management
  listFiles(skillId: string) {
    return request<SkillFile[]>(`/skills/${skillId}/files`);
  },

  uploadFiles(
    skillId: string,
    files: File[],
    fileType: 'script' | 'reference' | 'asset' = 'script',
    descriptions?: Array<{ description: string; language?: string }>,
  ) {
    return (async () => {
      if (isTokenExpiringSoon(tokenStorage.getAccess())) {
        const refreshed = await refreshAccessToken();
        if (!refreshed && tokenStorage.getRefresh()) forceLogout();
      }
      const token = tokenStorage.getAccess();
      const form = new FormData();
      files.forEach((f) => form.append('files', f));
      form.append('file_type', fileType);
      form.append('descriptions', JSON.stringify(descriptions || []));
      const resp = await fetch(`${API_BASE}/skills/${skillId}/files`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
        body: form,
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: 'Upload failed' }));
        throw new ApiError(resp.status, err.detail || 'Upload failed');
      }
      return resp.json() as Promise<{ skill_id: string; files: SkillFile[]; message: string }>;
    })();
  },

  deleteFile(skillId: string, filePath: string) {
    return request<void>(`/skills/${skillId}/files/${filePath}`, { method: 'DELETE' });
  },

  downloadFile(skillId: string, filePath: string) {
    return (async () => {
      if (isTokenExpiringSoon(tokenStorage.getAccess())) {
        const refreshed = await refreshAccessToken();
        if (!refreshed && tokenStorage.getRefresh()) forceLogout();
      }
      const token = tokenStorage.getAccess();
      const resp = await fetch(`${API_BASE}/skills/${skillId}/files/${filePath}/download`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!resp.ok) throw new ApiError(resp.status, 'Download failed');
      return resp.blob();
    })();
  },
};

// ---- Agents ----

export const agentsApi = {
  // User-facing
  list() {
    return request<Agent[]>('/agents');
  },

  get(id: string) {
    return request<Agent>(`/agents/${id}`);
  },

  stats(id: string) {
    return request<AgentStats>(`/agents/${id}/stats`);
  },

  // Admin CRUD
  adminList() {
    return request<Agent[]>('/admin/agents');
  },

  adminCreate(data: {
    name: string;
    description?: string;
    icon?: string;
    system_prompt?: string;
    model_id?: string | null;
    enabled_tools?: string[];
    mcp_server_ids?: string[];
    skill_ids?: string[];
    visibility?: 'tenant' | 'private';
  }) {
    return request<Agent>('/admin/agents', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  adminUpdate(id: string, data: {
    name?: string;
    description?: string;
    icon?: string;
    system_prompt?: string;
    model_id?: string | null;
    enabled_tools?: string[];
    mcp_server_ids?: string[];
    skill_ids?: string[];
    enable_memory_extraction?: boolean;
    enable_retry?: boolean;
    enable_auto_title?: boolean;
    is_active?: boolean;
    child_ids?: string[];
    max_iterations?: number | null;
    temperature?: number | null;
    welcome_message?: string | null;
    visibility?: 'tenant' | 'private';
  }) {
    return request<Agent>(`/admin/agents/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  },

  adminDelete(id: string) {
    return request<{ message: string }>(`/admin/agents/${id}`, {
      method: 'DELETE',
    });
  },
};

export const toolsApi = {
  list() {
    return request<import('@/lib/types').ToolInfo[]>('/tools');
  },
};

// ---- Confirmations (AskUserQuestion) ----

export const confirmationsApi = {
  /** Submit a response to a pending confirmation */
  respond(confirmationId: string, response: ConfirmationResponse) {
    return request<{ success: boolean; message: string }>(
      `/confirmations/${confirmationId}/respond`,
      {
        method: 'POST',
        body: JSON.stringify(response),
      },
    );
  },

  /** Get all pending confirmations for a session (page refresh recovery) */
  getPending(sessionId: string) {
    return request<ConfirmationRequest[]>(
      `/confirmations/sessions/${sessionId}/pending`,
    );
  },
};

// ---- Agent External API (version management + API doc) ----

export const agentApiApi = {
  /** Get API documentation metadata for an agent */
  getDoc(agentId: string) {
    return request<import('@/lib/types').AgentApiDoc>(`/agents/${agentId}/api-doc`);
  },

  /** List all published versions of an agent */
  listVersions(agentId: string) {
    return request<import('@/lib/types').AgentVersion[]>(`/agents/${agentId}/versions`);
  },

  /** Publish a new version (admin/owner only) */
  publishVersion(agentId: string, data?: { version?: string; changelog?: string }) {
    return request<import('@/lib/types').AgentVersion>(`/agents/${agentId}/versions`, {
      method: 'POST',
      body: JSON.stringify(data || {}),
    });
  },
};

// ---- Cron Jobs ----

export const cronJobsApi = {
  list(params?: { limit?: number; offset?: number }) {
    const searchParams = new URLSearchParams();
    if (params?.limit) searchParams.set('limit', String(params.limit));
    if (params?.offset) searchParams.set('offset', String(params.offset));
    const qs = searchParams.toString();
    return request<CronJobListResponse>(`/cron-jobs${qs ? `?${qs}` : ''}`);
  },

  get(id: string) {
    return request<CronJob>(`/cron-jobs/${id}`);
  },

  create(data: {
    name: string;
    agent_id?: string | null;
    cron_expr?: string | null;
    run_at?: string | null;
    message?: string | null;
    task_config?: Record<string, unknown>;
    channel_id?: string | null;
    is_active?: boolean;
  }) {
    return request<CronJob>('/cron-jobs', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  update(id: string, data: {
    name?: string;
    agent_id?: string | null;
    cron_expr?: string | null;
    run_at?: string | null;
    message?: string | null;
    task_config?: Record<string, unknown>;
    channel_id?: string | null;
    is_active?: boolean;
  }) {
    return request<CronJob>(`/cron-jobs/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  },

  delete(id: string) {
    return request<void>(`/cron-jobs/${id}`, { method: 'DELETE' });
  },

  runs(jobId: string, params?: { limit?: number; offset?: number }) {
    const searchParams = new URLSearchParams();
    if (params?.limit) searchParams.set('limit', String(params.limit));
    if (params?.offset) searchParams.set('offset', String(params.offset));
    const qs = searchParams.toString();
    return request<CronJobRunListResponse>(`/cron-jobs/${jobId}/runs${qs ? `?${qs}` : ''}`);
  },
};

// ---- Workspaces ----

export interface Workspace {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  file_count: number;
  total_size_bytes: number;
  is_default: boolean;
  last_synced_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface WorkspaceFileEntry {
  path: string;
  size: number;
  is_dir: boolean;
}

export interface WorkspaceFileList {
  entries: WorkspaceFileEntry[];
  source: 'sandbox' | 'storage';
}

export const workspacesApi = {
  list() {
    return request<Workspace[]>('/workspaces');
  },

  create(name: string, description?: string) {
    return request<Workspace>('/workspaces', {
      method: 'POST',
      body: JSON.stringify({ name, description: description || null }),
    });
  },

  delete(id: string) {
    return request<void>(`/workspaces/${id}`, { method: 'DELETE' });
  },

  listFiles(id: string, path: string) {
    return request<WorkspaceFileList>(
      `/workspaces/${id}/files?path=${encodeURIComponent(path)}`,
    );
  },

  deleteFile(id: string, path: string) {
    return request<void>(
      `/workspaces/${id}/files?path=${encodeURIComponent(path)}`,
      { method: 'DELETE' },
    );
  },

  async uploadFile(id: string, path: string, file: File): Promise<{ path: string; size: number }> {
    if (isTokenExpiringSoon(tokenStorage.getAccess())) {
      const refreshed = await refreshAccessToken();
      if (!refreshed && tokenStorage.getRefresh()) {
        throw new ApiError(401, 'Session expired');
      }
    }

    const form = new FormData();
    form.append('file', file);

    const token = tokenStorage.getAccess() || '';
    const resp = await fetch(
      `${API_BASE}/workspaces/${id}/files/content?path=${encodeURIComponent(path)}`,
      {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
        // Do NOT set Content-Type — browser auto-sets multipart boundary
        body: form,
      },
    );
    if (!resp.ok) {
      let errMsg = resp.statusText;
      try {
        const body = await resp.json();
        errMsg = body.detail || errMsg;
      } catch { /* ignore */ }
      throw new ApiError(resp.status, errMsg);
    }
    return resp.json();
  },

  async downloadFile(id: string, path: string): Promise<Blob> {
    if (isTokenExpiringSoon(tokenStorage.getAccess())) {
      const refreshed = await refreshAccessToken();
      if (!refreshed && tokenStorage.getRefresh()) {
        throw new ApiError(401, 'Session expired');
      }
    }

    const token = tokenStorage.getAccess() || '';
    const resp = await fetch(
      `${API_BASE}/workspaces/${id}/files/content?path=${encodeURIComponent(path)}`,
      { headers: { Authorization: `Bearer ${token}` } },
    );
    if (!resp.ok) {
      let errMsg = resp.statusText;
      try {
        const body = await resp.json();
        errMsg = body.detail || errMsg;
      } catch { /* ignore */ }
      throw new ApiError(resp.status, errMsg);
    }
    return resp.blob();
  },
};

// ---- Slash commands ----

export const commandsApi = {
  list() {
    return request<CommandMeta[]>('/commands');
  },
};

// ---- Models ----

export const modelsApi = {
  list() {
    return request<ModelMeta[]>('/models');
  },
};

// ---- Analytics ----

export type AnalyticsScope = 'mine' | 'global';

export interface AnalyticsSummary {
  sessions: number;
  messages: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  request_count: number;
  active_users: number | null;
  prev_sessions: number;
  prev_messages: number;
  prev_total_tokens: number;
  prev_request_count: number;
}

export interface AnalyticsTrendPoint {
  date: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  sessions: number;
}

export interface AnalyticsDistributionItem {
  key: string;
  label: string;
  sessions: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  request_count: number;
}

export interface AnalyticsDetailItem {
  date: string;
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  request_count: number;
}

export interface AnalyticsQuery {
  start?: string;
  end?: string;
  scope?: AnalyticsScope;
}

function qs(q: object): string {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(q)) {
    if (v !== undefined && v !== '') params.set(k, String(v));
  }
  const s = params.toString();
  return s ? `?${s}` : '';
}

export const analyticsApi = {
  summary(q: AnalyticsQuery) {
    return request<AnalyticsSummary>(`/analytics/summary${qs(q)}`);
  },
  trend(q: AnalyticsQuery) {
    return request<AnalyticsTrendPoint[]>(`/analytics/trend${qs(q)}`);
  },
  distribution(q: AnalyticsQuery & { by: 'model' | 'agent' | 'user' }) {
    return request<AnalyticsDistributionItem[]>(`/analytics/distribution${qs(q)}`);
  },
  detail(q: AnalyticsQuery & { page?: number; page_size?: number }) {
    return request<{ items: AnalyticsDetailItem[]; total: number }>(
      `/analytics/detail${qs(q)}`,
    );
  },
};

// ---- Pets ----

export const petsApi = {
  market() {
    return request<PetPackage[]>('/pets/market');
  },
  myPackages() {
    return request<PetPackage[]>('/pets/packages/mine');
  },
  myPets() {
    return request<UserPet[]>('/pets/mine');
  },
  active() {
    return request<UserPet | null>('/pets/active');
  },
  async upload(
    file: File,
    rowMapping: Record<string, number>,
    visibility: PetVisibility = 'private',
  ): Promise<PetPackage> {
    if (isTokenExpiringSoon(tokenStorage.getAccess())) {
      const refreshed = await refreshAccessToken();
      if (!refreshed && tokenStorage.getRefresh()) {
        throw new ApiError(401, 'Session expired');
      }
    }
    const form = new FormData();
    form.append('file', file);
    form.append('row_mapping', JSON.stringify(rowMapping));
    form.append('visibility', visibility);
    const token = tokenStorage.getAccess() || '';
    const resp = await fetch(`${API_BASE}/pets/packages`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
      body: form,
    });
    if (!resp.ok) {
      let errMsg = resp.statusText;
      try {
        const body = await resp.json();
        errMsg = body.detail || errMsg;
      } catch {
        /* ignore */
      }
      throw new ApiError(resp.status, errMsg);
    }
    return resp.json();
  },
  /** 带 token 拉取精灵图（<img src> 无法带 Authorization，需 blob URL） */
  async spritesheetBlob(packageId: string): Promise<Blob> {
    if (isTokenExpiringSoon(tokenStorage.getAccess())) {
      const refreshed = await refreshAccessToken();
      if (!refreshed && tokenStorage.getRefresh()) {
        throw new ApiError(401, 'Session expired');
      }
    }
    const token = tokenStorage.getAccess() || '';
    const resp = await fetch(`${API_BASE}/pets/packages/${packageId}/spritesheet`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!resp.ok) {
      let errMsg = resp.statusText;
      try {
        const body = await resp.json();
        errMsg = body.detail || errMsg;
      } catch { /* ignore */ }
      throw new ApiError(resp.status, errMsg);
    }
    return resp.blob();
  },

  /** 带 token 拉取原始 zip 并触发下载 */
  async download(packageId: string, filename: string): Promise<void> {
    if (isTokenExpiringSoon(tokenStorage.getAccess())) {
      const refreshed = await refreshAccessToken();
      if (!refreshed && tokenStorage.getRefresh()) {
        throw new ApiError(401, 'Session expired');
      }
    }
    const token = tokenStorage.getAccess() || '';
    const resp = await fetch(`${API_BASE}/pets/packages/${packageId}/download`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!resp.ok) {
      let errMsg = resp.statusText;
      try {
        const body = await resp.json();
        errMsg = body.detail || errMsg;
      } catch { /* ignore */ }
      throw new ApiError(resp.status, errMsg);
    }
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  },

  setVisibility(packageId: string, visibility: PetVisibility) {
    return request<PetPackage>(`/pets/packages/${packageId}/visibility`, {
      method: 'PUT',
      body: JSON.stringify({ visibility }),
    });
  },
  setRowMapping(packageId: string, rowMapping: Record<string, number>) {
    return request<PetPackage>(`/pets/packages/${packageId}/row-mapping`, {
      method: 'PUT',
      body: JSON.stringify({ row_mapping: rowMapping }),
    });
  },
  deletePackage(packageId: string) {
    return request<void>(`/pets/packages/${packageId}`, { method: 'DELETE' });
  },
  adopt(packageId: string) {
    return request<UserPet>(`/pets/${packageId}/adopt`, { method: 'POST' });
  },
  activate(userPetId: string) {
    return request<UserPet>(`/pets/${userPetId}/activate`, { method: 'POST' });
  },
  deactivate() {
    return request<void>('/pets/deactivate', { method: 'POST' });
  },
  remove(userPetId: string) {
    return request<void>(`/pets/${userPetId}`, { method: 'DELETE' });
  },
  interact(userPetId: string) {
    return request<UserPet>(`/pets/${userPetId}/interact`, { method: 'POST' });
  },
  /** 实例级绑定/解绑智能体（agentId 为 null 解绑，回退包级默认） */
  bindAgent(userPetId: string, agentId: string | null) {
    return request<UserPet>(`/pets/${userPetId}/agent`, {
      method: 'PUT',
      body: JSON.stringify({ agent_id: agentId }),
    });
  },
  /** 包级默认人设 Agent（仅创建人） */
  setPackageDefaultAgent(packageId: string, agentId: string | null) {
    return request<PetPackage>(`/pets/packages/${packageId}/default-agent`, {
      method: 'PUT',
      body: JSON.stringify({ agent_id: agentId }),
    });
  },
  /** 上传者改包级动作名 */
  setPackageActions(packageId: string, actions: Record<string, string>) {
    return request<PetPackage>(`/pets/packages/${packageId}/actions`, {
      method: 'PUT',
      body: JSON.stringify({ actions }),
    });
  },
  /** 领养者改实例级动作名 + 状态映射（stateMapping 不传则不改映射） */
  setPetActions(
    userPetId: string,
    aliases: Record<string, string>,
    stateMapping?: Record<string, number>,
  ) {
    return request<UserPet>(`/pets/${userPetId}/actions`, {
      method: 'PUT',
      body: JSON.stringify({ aliases, state_mapping: stateMapping ?? null }),
    });
  },
  /** 开启/复用宠物闲聊会话，返回 conversation_id */
  petChat(userPetId: string) {
    return request<{ conversation_id: string; agent_id: string | null }>(
      `/pets/${userPetId}/chat`,
      { method: 'POST' },
    );
  },
  /**
   * 智能气泡：正常时 SSE 流式返回 pet_action / text_delta / bubble_done 事件；
   * 未绑定/失败/超限时返回 fallback JSON（type: 'fallback'）。返回 close 函数。
   */
  bubble(
    userPetId: string,
    onEvent: (ev: {
      type: string;
      text?: string;
      name?: string;
      row?: number;
      fallback?: boolean;
      quota_exceeded?: boolean;
    }) => void,
  ): () => void {
    const controller = new AbortController();
    (async () => {
      if (isTokenExpiringSoon(tokenStorage.getAccess())) {
        const refreshed = await refreshAccessToken();
        if (!refreshed && tokenStorage.getRefresh()) {
          onEvent({ type: 'error' });
          forceLogout();
          return;
        }
      }
      const token = tokenStorage.getAccess() || '';
      const resp = await fetch(`${API_BASE}/pets/${userPetId}/bubble`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
        signal: controller.signal,
      });
      if (!resp.ok || !resp.body) {
        onEvent({ type: 'error' });
        return;
      }
      const ctype = resp.headers.get('content-type') || '';
      if (ctype.includes('text/event-stream')) {
        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const parts = buffer.split('\n\n');
          buffer = parts.pop() || '';
          for (const part of parts) {
            const line = part.trim();
            if (line.startsWith('data: ')) {
              try {
                onEvent(JSON.parse(line.slice(6)));
              } catch {
                /* ignore malformed JSON */
              }
            }
          }
        }
        onEvent({ type: 'closed' });
      } else {
        try {
          const data = await resp.json();
          if (data?.fallback) {
            onEvent({
              type: 'fallback',
              fallback: true,
              text: data.text ?? null,
              quota_exceeded: !!data.quota_exceeded,
            });
          } else {
            onEvent({ type: 'error' });
          }
        } catch {
          onEvent({ type: 'error' });
        }
      }
    })().catch((err) => {
      if (err.name !== 'AbortError') onEvent({ type: 'error' });
    });
    return () => controller.abort();
  },
  /**
   * Watch channel task lifecycle via SSE (GET /api/pets/tasks/events).
   * Fires `onEvent` per event; emits `{ type: 'error' }` on failure and
   * `{ type: 'closed' }` when the server ends the stream. Returns a close fn.
   */
  watchActiveTasks(onEvent: (event: Record<string, unknown>) => void): () => void {
    const controller = new AbortController();

    (async () => {
      // Ensure access token is fresh before opening the stream
      if (isTokenExpiringSoon(tokenStorage.getAccess())) {
        const refreshed = await refreshAccessToken();
        if (!refreshed && tokenStorage.getRefresh()) {
          onEvent({ type: 'error', message: 'Session expired' });
          forceLogout();
          return;
        }
      }

      const token = tokenStorage.getAccess() || '';
      const resp = await fetch(`${API_BASE}/pets/tasks/events`, {
        headers: { Authorization: `Bearer ${token}` },
        signal: controller.signal,
      });

      if (!resp.ok || !resp.body) {
        onEvent({ type: 'error', message: resp.statusText });
        return;
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        // SSE events are separated by double newlines
        const parts = buffer.split('\n\n');
        buffer = parts.pop() || '';
        for (const part of parts) {
          const line = part.trim();
          if (line.startsWith('data: ')) {
            try {
              onEvent(JSON.parse(line.slice(6)));
            } catch {
              /* ignore malformed JSON */
            }
          }
        }
      }

      onEvent({ type: 'closed' });
    })().catch((err) => {
      if (err.name !== 'AbortError') {
        onEvent({ type: 'error', message: err.message || '连接断开' });
      }
    });

    return () => controller.abort();
  },
};

// ---- Observability (大模型可观测性) ----

export interface ObsOverviewCards {
  llm_requests: number;
  tool_requests: number;
  llm_error_rate: number;
  tool_error_rate: number;
  avg_ttft_ms: number | null;
  p95_latency_ms: number | null;
  total_tokens: number;
  context_util_p95: number | null;
  prompt_tokens: number;
  completion_tokens: number;
  cache_read_tokens: number;
  cache_hit_rate: number;
}

export interface ObsOverview {
  cards: ObsOverviewCards;
  series: Record<string, { ts: string; value: number }[]>;
}

export interface ObsTraceItem {
  trace_id: string;
  session_id: string | null;
  agent_id: string | null;
  status: string;
  iteration_count: number;
  tool_call_count: number;
  total_tokens: number;
  duration_ms: number | null;
  created_at: string;
  session_title: string | null;
}

export interface ObsTracePage {
  items: ObsTraceItem[];
  total: number;
}

export interface ObsTraceDetail {
  trace: Record<string, unknown>;
  llm_calls: Record<string, unknown>[];
  tool_calls: Record<string, unknown>[];
}

export interface ObsDistributionItem {
  key: string;
  label: string;
  request_count: number;
  total_tokens: number;
  error_count: number;
  avg_duration_ms: number | null;
}

export interface ObsToolRankItem {
  tool_name: string;
  request_count: number;
  error_count: number;
  error_rate: number;
  avg_duration_ms: number | null;
  p95_duration_ms: number | null;
  total_injected_tokens: number;
}

export interface ObsToolTrendPoint {
  ts: string;
  request_count: number;
  error_count: number;
}

export interface ObsQuality {
  trace_count: number;
  success_count: number;
  error_count: number;
  interrupted_count: number;
  avg_duration_ms: number | null;
  avg_tokens_per_trace: number | null;
  avg_llm_calls: number | null;
  avg_tool_calls: number | null;
  compress_count: number;
  saved_tokens: number;
  daily: {
    ts: string;
    trace_count: number;
    success_count: number;
    total_tokens: number;
    avg_duration_ms: number | null;
    compress_count: number;
    saved_tokens: number;
  }[];
}

export type ObsWindow = '1h' | '24h' | '7d';

export const observabilityApi = {
  overview(window: ObsWindow) {
    return request<ObsOverview>(`/observability/overview?window=${window}`);
  },
  traces(q: {
    window?: ObsWindow;
    page?: number;
    page_size?: number;
    status?: string;
    agent_id?: string;
    session_id?: string;
  }) {
    return request<ObsTracePage>(`/observability/traces${qs(q)}`);
  },
  trace(id: string) {
    return request<ObsTraceDetail>(`/observability/traces/${id}`);
  },
  stats(q: { window?: ObsWindow; by?: 'model' | 'agent' | 'user' | 'tenant'; metric?: string }) {
    return request<ObsDistributionItem[]>(`/observability/stats${qs(q)}`);
  },
  toolRanking(q: { window?: ObsWindow; metric?: string; top?: number }) {
    return request<ObsToolRankItem[]>(`/observability/tool-ranking${qs(q)}`);
  },
  toolTrend(tool: string, granularity: 'minute' | 'hour' | 'day') {
    return request<ObsToolTrendPoint[]>(
      `/observability/tools/${encodeURIComponent(tool)}/trend?granularity=${granularity}`,
    );
  },
  quality(window: ObsWindow) {
    return request<ObsQuality>(`/observability/quality?window=${window}`);
  },
};
