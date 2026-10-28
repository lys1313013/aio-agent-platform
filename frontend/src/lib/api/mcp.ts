import { request } from './transport';

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
