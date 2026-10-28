import type { Skill, SkillListResponse, SkillSearchResult, SkillsShImportResult, SkillsShResolveResult, SkillsShSearchItem, SkillVersion, SkillFile } from '../types';
import { request, uploadForm, downloadBlob } from './transport';

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
    files?: Array<{ path: string; content_base64: string; description?: string }>;
  }) {
    return request<Skill>('/skills', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  update(
    id: string,
    data: {
      expected_version: number;
      files?: Array<{ path: string; content_base64: string; description?: string }>;
      remove_files?: string[];
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
    return request<{ skill_id: string; version: number; content: string; created_at: string; snapshot?: Partial<Skill> }>(
      `/skills/${id}/versions/${version}`,
    );
  },

  download(id: string, version?: number) {
    return downloadBlob(`/skills/${id}${version === undefined ? '' : `/versions/${version}`}/download`);
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
    const form = new FormData();
    form.append('file', file);
    return uploadForm<Skill>('/skills/import', form);
  },

  // File management
  listFiles(skillId: string) {
    return request<SkillFile[]>(`/skills/${skillId}/files`);
  },

  uploadFiles(
    skillId: string,
    files: File[],
    fileType: 'script' | 'reference' | 'asset',
    expectedVersion: number,
    descriptions?: Array<{ description: string; language?: string }>,
  ) {
    const form = new FormData();
    files.forEach((file) => form.append('files', file));
    form.append('file_type', fileType);
    form.append('expected_version', String(expectedVersion));
    form.append('descriptions', JSON.stringify(descriptions || []));
    return uploadForm<{ skill_id: string; files: SkillFile[]; message: string }>(`/skills/${skillId}/files`, form);
  },

  deleteFile(skillId: string, filePath: string, expectedVersion: number) {
    return request<void>(`/skills/${skillId}/files/${filePath}?expected_version=${expectedVersion}`, { method: 'DELETE' });
  },

  downloadFile(skillId: string, filePath: string) {
    return downloadBlob(`/skills/${skillId}/files/${filePath}/download`);
  },
};
