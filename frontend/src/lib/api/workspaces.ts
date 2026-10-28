import { request, uploadForm, downloadBlob } from './transport';

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

  uploadFile(id: string, path: string, file: File): Promise<{ path: string; size: number }> {
    const form = new FormData();
    form.append('file', file);
    return uploadForm(`/workspaces/${id}/files/content?path=${encodeURIComponent(path)}`, form);
  },

  downloadFile(id: string, path: string): Promise<Blob> {
    return downloadBlob(`/workspaces/${id}/files/content?path=${encodeURIComponent(path)}`);
  },
};
