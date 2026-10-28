import type { Agent, AgentStats, PortalAgent } from '../types';
import { request } from './transport';

// ---- Agents ----

// ---- Portal (用户端对话门户，脱敏只读) ----
export const portalApi = {
  listAgents() {
    return request<PortalAgent[]>('/portal/agents');
  },

  getAgent(id: string) {
    return request<PortalAgent>(`/portal/agents/${id}`);
  },
};

export const agentsApi = {
  // User-facing
  list() {
    return request<Agent[]>('/agents');
  },  get(id: string) {
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
    mcp_all_tools_server_ids?: string[];
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
    mcp_all_tools_server_ids?: string[];
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
