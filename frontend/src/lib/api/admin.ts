import { request } from './transport';

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
    role?: 'user' | 'admin' | 'superadmin';
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
    role?: 'user' | 'admin' | 'superadmin';
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

  deletePortraitVersion(versionId: string) {
    return request<void>(
      `/settings/personal-portrait/versions/${versionId}`,
      { method: 'DELETE' },
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
