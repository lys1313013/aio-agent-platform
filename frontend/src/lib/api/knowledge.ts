import { request, uploadForm } from './transport';

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

// ---- Graph Knowledge Base (知识图谱) ----

export interface GraphKnowledgeBase {
  id: string;
  name: string;
  description: string | null;
  is_active: boolean;
  visibility: 'tenant' | 'private';
  tenant_id: string;
  created_by: string;
  can_edit: boolean;
  entity_count: number;
  relationship_count: number;
  document_count: number;
  created_at: string;
}

export interface GraphDocument {
  id: string;
  title: string;
  source_type: string;
  status: string;
  chunk_count: number;
  content: string;
  created_by: string;
  created_at: string;
}

export interface GraphChunk {
  id: string;
  seq: number;
  content: string;
}

export interface GraphEntity {
  id: string;
  name: string;
  type: string;
  description: string | null;
  status: string;
  source_chunk_id: string | null;
  created_at: string;
}

export interface GraphRelationship {
  id: string;
  source_entity_id: string;
  target_entity_id: string;
  source_name: string | null;
  target_name: string | null;
  relation_type: string;
  description: string | null;
  confidence: number;
  status: string;
  source_chunk_id: string | null;
  created_at: string;
}

export interface GraphExtractionJob {
  id: string;
  status: string;
  total_chunks: number;
  processed_chunks: number;
  entities_found: number;
  relationships_found: number;
  error: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface GraphRetrieveEntity {
  id: string;
  name: string;
  type: string;
  description: string | null;
  is_seed: boolean;
}

export interface GraphRetrieveRelationship {
  id: string;
  source: string;
  relation_type: string;
  target: string;
  confidence: number;
  description: string | null;
  depth: number;
}

export interface GraphRetrieveResult {
  entities: GraphRetrieveEntity[];
  relationships: GraphRetrieveRelationship[];
  seed_entities: Array<{ id: string; name: string; type: string }>;
  query_time_ms: number;
}

export interface Paginated<T> {
  items: T[];
  total: number;
}

export const graphKnowledgeApi = {
  list() {
    return request<GraphKnowledgeBase[]>('/admin/graph-knowledge-bases');
  },

  create(data: {
    name: string;
    description?: string;
    is_active?: boolean;
    visibility?: 'tenant' | 'private';
  }) {
    return request<GraphKnowledgeBase>('/admin/graph-knowledge-bases', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  update(id: string, data: {
    name?: string;
    description?: string;
    is_active?: boolean;
    visibility?: 'tenant' | 'private';
  }) {
    return request<GraphKnowledgeBase>(`/admin/graph-knowledge-bases/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  },

  delete(id: string) {
    return request<{ message: string }>(`/admin/graph-knowledge-bases/${id}`, {
      method: 'DELETE',
    });
  },

  // ---- Documents ----

  documents(id: string) {
    return request<GraphDocument[]>(`/admin/graph-knowledge-bases/${id}/documents`);
  },

  addDocument(id: string, data: {
    title?: string;
    content: string;
    source_type?: string;
  }) {
    return request<{ id: string; title: string; status: string; chunk_count: number }>(
      `/admin/graph-knowledge-bases/${id}/documents`,
      { method: 'POST', body: JSON.stringify(data) },
    );
  },

  /** Upload a document file (.md/.txt/.html/.pdf/.docx) for parsing and chunking. */
  uploadDocument(id: string, file: File, title?: string): Promise<{
    id: string; title: string; status: string; chunk_count: number;
  }> {
    const form = new FormData();
    form.append('file', file);
    if (title) form.append('title', title);
    return uploadForm(`/admin/graph-knowledge-bases/${id}/documents/upload`, form);
  },

  chunks(kbId: string, docId: string) {
    return request<GraphChunk[]>(`/admin/graph-knowledge-bases/${kbId}/documents/${docId}/chunks`);
  },

  deleteDocument(kbId: string, docId: string) {
    return request<{ message: string }>(`/admin/graph-knowledge-bases/${kbId}/documents/${docId}`, {
      method: 'DELETE',
    });
  },

  // ---- Extraction jobs ----

  extract(id: string) {
    return request<{ job_id: string; status: string }>(`/admin/graph-knowledge-bases/${id}/extract`, {
      method: 'POST',
    });
  },

  jobs(id: string) {
    return request<GraphExtractionJob[]>(`/admin/graph-knowledge-bases/${id}/jobs`);
  },

  retryJob(kbId: string, jobId: string) {
    return request<{ job_id: string; status: string }>(
      `/admin/graph-knowledge-bases/${kbId}/jobs/${jobId}/retry`,
      { method: 'POST' },
    );
  },

  // ---- Entities ----

  entities(id: string, params?: { status?: string; limit?: number; offset?: number }) {
    const qs = new URLSearchParams();
    if (params?.status) qs.set('status', params.status);
    if (params?.limit) qs.set('limit', String(params.limit));
    if (params?.offset) qs.set('offset', String(params.offset));
    const q = qs.toString();
    return request<Paginated<GraphEntity>>(
      `/admin/graph-knowledge-bases/${id}/entities${q ? `?${q}` : ''}`,
    );
  },

  updateEntity(kbId: string, entityId: string, data: {
    name?: string;
    type?: string;
    description?: string;
  }) {
    return request<GraphEntity>(`/admin/graph-knowledge-bases/${kbId}/entities/${entityId}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  },

  deleteEntity(kbId: string, entityId: string) {
    return request<{ message: string }>(`/admin/graph-knowledge-bases/${kbId}/entities/${entityId}`, {
      method: 'DELETE',
    });
  },

  approveEntities(kbId: string, ids: string[]) {
    return request<{ approved: number }>(`/admin/graph-knowledge-bases/${kbId}/entities/approve`, {
      method: 'POST',
      body: JSON.stringify({ ids }),
    });
  },

  // ---- Relationships ----

  relationships(id: string, params?: { status?: string; limit?: number; offset?: number }) {
    const qs = new URLSearchParams();
    if (params?.status) qs.set('status', params.status);
    if (params?.limit) qs.set('limit', String(params.limit));
    if (params?.offset) qs.set('offset', String(params.offset));
    const q = qs.toString();
    return request<Paginated<GraphRelationship>>(
      `/admin/graph-knowledge-bases/${id}/relationships${q ? `?${q}` : ''}`,
    );
  },

  updateRelationship(kbId: string, relId: string, data: {
    relation_type?: string;
    description?: string;
    confidence?: number;
  }) {
    return request<{ id: string; relation_type: string; description: string | null; confidence: number; status: string }>(
      `/admin/graph-knowledge-bases/${kbId}/relationships/${relId}`,
      { method: 'PUT', body: JSON.stringify(data) },
    );
  },

  deleteRelationship(kbId: string, relId: string) {
    return request<{ message: string }>(
      `/admin/graph-knowledge-bases/${kbId}/relationships/${relId}`,
      { method: 'DELETE' },
    );
  },

  approveRelationships(kbId: string, ids: string[]) {
    return request<{ approved: number }>(`/admin/graph-knowledge-bases/${kbId}/relationships/approve`, {
      method: 'POST',
      body: JSON.stringify({ ids }),
    });
  },

  // ---- Subgraph retrieval ----

  retrieve(kbId: string, data: { query: string; top_k_entities?: number; max_depth?: number }) {
    return request<GraphRetrieveResult>(`/admin/graph-knowledge-bases/${kbId}/retrieve`, {
      method: 'POST',
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
