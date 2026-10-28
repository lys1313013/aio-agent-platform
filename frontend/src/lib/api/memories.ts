import type { Memory, MemoryLayer, MemoryListResponse, MemorySearchResult, DailyMemory } from '../types';
import { request } from './transport';

// ---- Memories ----

export const memoriesApi = {
  previewOrganization(data: { layer: 'L1' | 'L2'; agent_id?: string | null; ids?: string[]; offset?: number }) {
    return request<import('../types').MemoryOrganizePreview>('/memories/organize/preview', {
      method: 'POST', body: JSON.stringify(data),
    });
  },
  applyOrganization(id: string, selections: Array<{ id: string; content: string }>) {
    return request<{ change_id: string; kind: string }>(`/memories/organize/${id}/apply`, {
      method: 'POST', body: JSON.stringify({ selections }),
    });
  },
  changes(agentId?: string, layer?: string, offset = 0) {
    const params = new URLSearchParams({ offset: String(offset) });
    if (agentId) params.set('agent_id', agentId);
    if (layer) params.set('layer', layer);
    return request<import('../types').MemoryChange[]>(`/memories/changes?${params}`);
  },
  undoChange(id: string) {
    return request<{ change_id: string; kind: string }>(`/memories/changes/${id}/undo`, { method: 'POST' });
  },
  versions(id: string, offset = 0) {
    return request<import('../types').MemoryHistory>(`/memories/${id}/versions?offset=${offset}`);
  },
  restoreVersion(id: string, version: number, expectedVersion: number) {
    return request<{ change_id: string; kind: string }>(`/memories/${id}/versions/${version}/restore`, {
      method: 'POST', body: JSON.stringify({ expected_version: expectedVersion }),
    });
  },

  list(params?: { agent_id?: string; layer?: string; limit?: number; offset?: number }) {
    const searchParams = new URLSearchParams();
    if (params?.agent_id) searchParams.set('agent_id', params.agent_id);
    if (params?.layer) searchParams.set('layer', params.layer);
    if (params?.limit) searchParams.set('limit', String(params.limit));
    if (params?.offset) searchParams.set('offset', String(params.offset));
    const qs = searchParams.toString();
    return request<MemoryListResponse>(`/memories${qs ? `?${qs}` : ''}`);
  },

  get(id: string) {
    return request<Memory>(`/memories/${id}`);
  },

  create(data: { agent_id?: string | null; layer: MemoryLayer; content: string; metadata?: Record<string, unknown> }) {
    return request<Memory>('/memories', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  update(
    id: string,
    data: { expected_version?: number; agent_id?: string | null; content?: string; layer?: MemoryLayer; metadata?: Record<string, unknown> },
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

  stats(agentId?: string) {
    return request<Record<'L1' | 'L2' | 'L3', number>>(`/memories/stats${agentId ? `?agent_id=${agentId}` : ''}`);
  },

  search(query: string, params?: { agent_id?: string; layer?: string; top_k?: number }) {
    const searchParams = new URLSearchParams({ q: query });
    if (params?.agent_id) searchParams.set('agent_id', params.agent_id);
    if (params?.layer) searchParams.set('layer', params.layer);
    if (params?.top_k) searchParams.set('top_k', String(params.top_k));
    return request<MemorySearchResult[]>(`/memories/search?${searchParams.toString()}`);
  },
};

export const dailyMemoriesApi = {
  list(params?: { agent_id?: string; date?: string; start?: string; end?: string; limit?: number; offset?: number }) {
    const searchParams = new URLSearchParams();
    if (params?.agent_id) searchParams.set('agent_id', params.agent_id);
    if (params?.date) searchParams.set('date', params.date);
    if (params?.start) searchParams.set('start', params.start);
    if (params?.end) searchParams.set('end', params.end);
    if (params?.limit) searchParams.set('limit', String(params.limit));
    if (params?.offset) searchParams.set('offset', String(params.offset));
    const qs = searchParams.toString();
    return request<DailyMemory[]>(`/memories/daily${qs ? `?${qs}` : ''}`);
  },

  regenerate(day: string, agentId?: string) {
    return request<DailyMemory>(`/memories/daily/${day}/regenerate${agentId ? `?agent_id=${agentId}` : ''}`, { method: 'POST' });
  },

  delete(day: string, agentId?: string) {
    return request<void>(`/memories/daily/${day}${agentId ? `?agent_id=${agentId}` : ''}`, { method: 'DELETE' });
  },
};
