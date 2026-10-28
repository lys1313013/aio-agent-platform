import type { TokenPair } from '../types';
import { request } from './transport';

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
