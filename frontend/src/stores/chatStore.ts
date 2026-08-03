import { create } from 'zustand';
import { sessionsApi, workspacesApi } from '@/lib/api';
import type { Session, Message } from '@/lib/types';
import type { Workspace } from '@/lib/api';

interface ChatState {
  sessions: Session[];
  activeSessionId: string | null;
  messages: Record<string, Message[]>;
  isSessionsLoading: boolean;
  /** 当前会话消息异步加载中（点击历史项切换会话时，消息未就绪前显示 loading） */
  messagesLoading: boolean;

  // Workspace selection
  workspaces: Workspace[];
  selectedWorkspaceId: string | null;
  isWorkspacesLoading: boolean;

  // Actions
  loadSessions: (agentId?: string | null) => Promise<void>;
  refreshSessions: (agentId?: string | null) => Promise<void>;
  createSession: (title?: string, agentId?: string | null) => Promise<string>;
  deleteSession: (id: string) => Promise<void>;
  setActiveSession: (id: string | null) => void;
  loadSessionMessages: (id: string) => Promise<void>;
  addMessage: (sessionId: string, msg: Message) => void;
  renameSession: (id: string, title: string) => Promise<void>;
  setSessionTitleLocal: (id: string, title: string) => void;
  pinSession: (id: string, isPinned: boolean) => Promise<void>;
  archiveSession: (id: string, isArchived: boolean) => Promise<void>;

  // Workspace actions
  loadWorkspaces: () => Promise<void>;
  setSelectedWorkspace: (id: string | null) => void;
}

export const useChatStore = create<ChatState>((set, get) => ({
  sessions: [],
  activeSessionId: null,
  messages: {},
  isSessionsLoading: false,
  messagesLoading: false,
  workspaces: [],
  selectedWorkspaceId: null,
  isWorkspacesLoading: false,

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
    const workspaceId = get().selectedWorkspaceId;
    const session = await sessionsApi.create(title, agentId, workspaceId);
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
    // 未缓存的消息触发异步加载并置 loading；已缓存直接使用，清掉可能残留的 loading
    set({ activeSessionId: id, messagesLoading: !!id && !get().messages[id] });
    if (id && !get().messages[id]) {
      get().loadSessionMessages(id);
    }
  },

  loadSessionMessages: async (id) => {
    try {
      const detail = await sessionsApi.get(id);
      set((state) => ({
        messages: { ...state.messages, [id]: detail.messages },
        // 仅当仍是当前会话才清 loading，避免快速切换时旧加载误清新会话的指示
        messagesLoading: get().activeSessionId === id ? false : state.messagesLoading,
      }));
    } catch {
      set((state) => ({
        messagesLoading: get().activeSessionId === id ? false : state.messagesLoading,
      }));
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

  // Local-only update — used when the backend pushes an auto-generated title
  setSessionTitleLocal: (id, title) => {
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

  loadWorkspaces: async () => {
    set({ isWorkspacesLoading: true });
    try {
      const workspaces = await workspacesApi.list();
      // Default to the default workspace, or the first one
      const defaultWs = workspaces.find((w) => w.is_default) || workspaces[0];
      set({
        workspaces,
        selectedWorkspaceId: defaultWs?.id ?? null,
        isWorkspacesLoading: false,
      });
    } catch {
      set({ isWorkspacesLoading: false });
    }
  },

  setSelectedWorkspace: (id) => {
    set({ selectedWorkspaceId: id });
  },
}));
