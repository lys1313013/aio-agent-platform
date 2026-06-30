import { create } from 'zustand';
import { sessionsApi } from '@/lib/api';
import type { Session, Message } from '@/lib/types';

interface ChatState {
  sessions: Session[];
  activeSessionId: string | null;
  messages: Record<string, Message[]>;
  isSessionsLoading: boolean;

  // Actions
  loadSessions: (agentId?: string | null) => Promise<void>;
  refreshSessions: (agentId?: string | null) => Promise<void>;
  createSession: (title?: string, agentId?: string | null) => Promise<string>;
  deleteSession: (id: string) => Promise<void>;
  setActiveSession: (id: string | null) => void;
  loadSessionMessages: (id: string) => Promise<void>;
  addMessage: (sessionId: string, msg: Message) => void;
  renameSession: (id: string, title: string) => Promise<void>;
  pinSession: (id: string, isPinned: boolean) => Promise<void>;
  archiveSession: (id: string, isArchived: boolean) => Promise<void>;
}

export const useChatStore = create<ChatState>((set, get) => ({
  sessions: [],
  activeSessionId: null,
  messages: {},
  isSessionsLoading: false,

  loadSessions: async (agentId) => {
    // Full reset — only use on initial page load / navigation
    set({ sessions: [], activeSessionId: null, messages: {}, isSessionsLoading: true });
    try {
      const sessions = await sessionsApi.list(agentId);
      set({ sessions, isSessionsLoading: false });
    } catch {
      set({ isSessionsLoading: false });
    }
  },

  refreshSessions: async (agentId) => {
    // Lightweight refresh — preserves activeSessionId so chat messages stay visible
    set({ isSessionsLoading: true });
    try {
      const sessions = await sessionsApi.list(agentId);
      set({ sessions, isSessionsLoading: false });
    } catch {
      set({ isSessionsLoading: false });
    }
  },

  createSession: async (title, agentId) => {
    const session = await sessionsApi.create(title, agentId);
    set((state) => ({
      sessions: [session, ...state.sessions],
      activeSessionId: session.id,
    }));
    return session.id;
  },

  deleteSession: async (id) => {
    await sessionsApi.delete(id);
    set((state) => ({
      sessions: state.sessions.filter((s) => s.id !== id),
      activeSessionId: state.activeSessionId === id ? null : state.activeSessionId,
      messages: Object.fromEntries(
        Object.entries(state.messages).filter(([key]) => key !== id),
      ),
    }));
  },

  setActiveSession: (id) => {
    set({ activeSessionId: id });
    if (id && !get().messages[id]) {
      get().loadSessionMessages(id);
    }
  },

  loadSessionMessages: async (id) => {
    try {
      const detail = await sessionsApi.get(id);
      set((state) => ({
        messages: { ...state.messages, [id]: detail.messages },
      }));
    } catch {
      /* ignore */
    }
  },

  addMessage: (sessionId, msg) => {
    set((state) => {
      const existing = state.messages[sessionId] || [];
      return {
        messages: { ...state.messages, [sessionId]: [...existing, msg] },
      };
    });
  },

  renameSession: async (id, title) => {
    await sessionsApi.rename(id, title);
    set((state) => ({
      sessions: state.sessions.map((s) => (s.id === id ? { ...s, title } : s)),
    }));
  },

  pinSession: async (id, isPinned) => {
    await sessionsApi.pin(id, isPinned);
    set((state) => ({
      sessions: state.sessions.map((s) => (s.id === id ? { ...s, is_pinned: isPinned } : s)),
    }));
  },

  archiveSession: async (id, isArchived) => {
    await sessionsApi.archive(id, isArchived);
    set((state) => ({
      sessions: state.sessions.map((s) =>
        s.id === id ? { ...s, is_archived: isArchived } : s,
      ),
    }));
  },
}));
