import { request } from './transport';

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
