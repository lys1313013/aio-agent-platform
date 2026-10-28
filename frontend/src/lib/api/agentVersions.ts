import { request } from './transport';

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
