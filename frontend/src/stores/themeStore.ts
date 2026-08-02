import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { DEFAULT_SKIN, isSkinId, type SkinId } from '@/styles/skins';

type Theme = 'light' | 'dark' | 'system';

interface ThemeState {
  theme: Theme;
  skin: SkinId;
  setTheme: (theme: Theme) => void;
  setSkin: (skin: SkinId) => void;
  resolvedTheme: () => 'light' | 'dark';
}

function getSystemTheme(): 'light' | 'dark' {
  if (typeof window === 'undefined') return 'dark';
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

export const useThemeStore = create<ThemeState>()(
  persist(
    (set, get) => ({
      theme: 'dark',
      skin: DEFAULT_SKIN,

      setTheme: (theme) => {
        set({ theme });
        applyTheme(theme);
      },

      setSkin: (skin) => {
        set({ skin });
        applySkin(skin);
      },

      resolvedTheme: () => {
        const { theme } = get();
        return theme === 'system' ? getSystemTheme() : theme;
      },
    }),
    {
      name: 'aio-theme',
      merge: (persisted, current) => {
        const state = { ...current, ...(persisted as Partial<ThemeState>) };
        if (!isSkinId(state.skin)) state.skin = DEFAULT_SKIN;
        return state;
      },
    },
  ),
);

function applyTheme(theme: Theme) {
  const root = document.documentElement;
  const resolved = theme === 'system' ? getSystemTheme() : theme;
  root.classList.remove('light', 'dark');
  root.classList.add(resolved);
}

function applySkin(skin: SkinId) {
  document.documentElement.dataset.skin = skin;
}

/** Call once on app startup to apply the persisted theme */
export function initTheme() {
  const { theme, skin } = useThemeStore.getState();
  applyTheme(theme);
  applySkin(isSkinId(skin) ? skin : DEFAULT_SKIN);

  // Listen for system theme changes when in 'system' mode
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    if (useThemeStore.getState().theme === 'system') {
      applyTheme('system');
    }
  });
}
