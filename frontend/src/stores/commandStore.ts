import { create } from 'zustand';
import { commandsApi } from '@/lib/api';
import type { CommandMeta } from '@/lib/types';

interface CommandState {
  commands: CommandMeta[];
  loaded: boolean;
  loading: boolean;
  /** Load command metadata once. Resolves to false if the endpoint failed. */
  load: (force?: boolean) => Promise<boolean>;
  /** Search commands by name/alias/usage prefix for the slash menu. */
  search: (query: string) => CommandMeta[];
}

const GROUPS = [
  '帮助', '会话', '技能', '记忆', '知识', '定时任务',
  '智能体', '确认', '工作区', '模型', '运行', '通用',
];

function groupRank(group: string): number {
  const idx = GROUPS.indexOf(group);
  return idx === -1 ? GROUPS.length : idx;
}

export const useCommandStore = create<CommandState>((set, get) => ({
  commands: [],
  loaded: false,
  loading: false,

  load: async (force = false) => {
    const { loaded, loading } = get();
    if (loaded && !force) return true;
    if (loading) return true;
    set({ loading: true });
    try {
      const commands = await commandsApi.list();
      set({ commands, loaded: true, loading: false });
      return true;
    } catch {
      set({ loading: false });
      return false;
    }
  },

  search: (query) => {
    const text = query.trim().toLowerCase();
    const { commands } = get();
    if (!text) return commands;
    return commands
      .filter((c) => {
        const names = [c.name, ...c.aliases];
        return (
          names.some((n) => n.toLowerCase().startsWith(text)) ||
          c.name.toLowerCase().includes(text) ||
          c.desc.toLowerCase().includes(text)
        );
      })
      .sort((a, b) => {
        // Exact prefix matches first, then by group order.
        const aPrefix = a.name.toLowerCase().startsWith(text) ? 0 : 1;
        const bPrefix = b.name.toLowerCase().startsWith(text) ? 0 : 1;
        if (aPrefix !== bPrefix) return aPrefix - bPrefix;
        return groupRank(a.group) - groupRank(b.group) || a.name.localeCompare(b.name);
      });
  },
}));
