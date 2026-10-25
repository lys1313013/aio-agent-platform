import { create } from 'zustand';
import { getAuthGeneration, isCurrentAuthGeneration, onAuthSessionChange } from '@/lib/authSession';
import { tokenStorage, getUserRole } from '@/lib/auth';
import { authApi, settingsApi } from '@/lib/api';

interface AuthState {
  isAuthenticated: boolean;
  isLoading: boolean;
  error: string | null;
  role: string | null;
  username: string | null;
  tenantId: string | null;
  tenantName: string | null;

  login: (usernameOrEmail: string, password: string) => Promise<boolean>;
  register: (username: string, email: string, password: string, tenantName?: string) => Promise<boolean>;
  logout: () => Promise<void>;
  checkAuth: () => boolean;
  clearError: () => void;
  loadProfile: () => Promise<void>;
}

function readInitialAuth() {
  const has = tokenStorage.hasTokens();
  return {
    isAuthenticated: has,
    role: has ? getUserRole() : null,
  };
}

const initial = readInitialAuth();

export const useAuthStore = create<AuthState>((set, get) => ({
  isAuthenticated: initial.isAuthenticated,
  isLoading: false,
  error: null,
  role: initial.role,
  username: null,
  tenantId: null,
  tenantName: null,

  login: async (usernameOrEmail, password) => {
    tokenStorage.clear();
    const generation = getAuthGeneration();
    set({ isLoading: true, error: null });
    try {
      const tokens = await authApi.login(usernameOrEmail, password);
      if (!isCurrentAuthGeneration(generation)) return false;
      tokenStorage.set(tokens.access_token, tokens.refresh_token);
      set({ isAuthenticated: true, isLoading: false, role: getUserRole() });
      await get().loadProfile();
      return isCurrentAuthGeneration(generation);
    } catch (err) {
      if (!isCurrentAuthGeneration(generation)) return false;
      set({
        error: err instanceof Error ? err.message : 'Login failed',
        isLoading: false,
      });
      return false;
    }
  },

  register: async (username, email, password, tenantName) => {
    tokenStorage.clear();
    const generation = getAuthGeneration();
    set({ isLoading: true, error: null });
    try {
      const tokens = await authApi.register(username, email, password, tenantName);
      if (!isCurrentAuthGeneration(generation)) return false;
      tokenStorage.set(tokens.access_token, tokens.refresh_token);
      set({ isAuthenticated: true, isLoading: false, role: getUserRole() });
      await get().loadProfile();
      return isCurrentAuthGeneration(generation);
    } catch (err) {
      if (!isCurrentAuthGeneration(generation)) return false;
      set({
        error: err instanceof Error ? err.message : 'Registration failed',
        isLoading: false,
      });
      return false;
    }
  },

  logout: async () => {
    const refresh = tokenStorage.getRefresh();
    // Invalidate immediately; a slow logout response must not clear a later login.
    tokenStorage.clear();
    if (refresh) {
      try {
        await authApi.logout(refresh);
      } catch {
        /* ignore */
      }
    }
  },

  checkAuth: () => {
    const has = tokenStorage.hasTokens();
    if (has !== get().isAuthenticated) {
      set({ isAuthenticated: has, role: has ? getUserRole() : null });
    }
    if (has && !get().username) {
      get().loadProfile();
    }
    return has;
  },

  clearError: () => set({ error: null }),

  loadProfile: async () => {
    const generation = getAuthGeneration();
    try {
      const profile = await settingsApi.getProfile();
      if (!isCurrentAuthGeneration(generation)) return;
      set({
        username: profile.display_name || profile.username,
        tenantId: profile.tenant_id,
        tenantName: profile.tenant_name,
      });
    } catch {
      /* ignore */
    }
  },
}));

onAuthSessionChange(() => {
  useAuthStore.setState({ isAuthenticated: false, isLoading: false, error: null, role: null, username: null, tenantId: null, tenantName: null });
});
