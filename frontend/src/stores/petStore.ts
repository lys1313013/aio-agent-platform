import { create } from 'zustand';
import { petsApi } from '@/lib/api';
import type { PetActiveTask, PetMood, PetPackage, UserPet } from '@/lib/types';

const ENABLED_KEY = 'pet-widget-enabled';
const SIZE_KEY = 'pet-widget-size';
const CELEBRATE_MS = 2400;
const SAD_MS = 5000;
const HAPPY_MS = 1500;

// 平台状态中文名（动画映射 + 互动菜单共用）
export const PET_STATE_LABELS: Record<PetMood, string> = {
  idle: '待机',
  think: '思考',
  work: '工作',
  wait: '等待',
  celebrate: '庆祝',
  sad: '沮丧',
  sleep: '睡觉',
  happy: '开心',
  run_right: '向右跑',
  run_left: '向左跑',
};

// Codex 标准 9 行动画的默认名（行语义，2026-08-02 校准）
export const CODEX_ROW_NAMES = ['待机', '向右跑', '向左跑', '挥手', '跳跃', '失败', '等待', '工作', '思考'];

/** 精灵图行显示名：映射的状态名 > Codex 标准行名 > 「动画 N」 */
export function rowName(pkg: PetPackage, row: number): string {
  for (const [state, r] of Object.entries(pkg.row_mapping)) {
    if (state !== '_row_frames' && r === row) {
      return PET_STATE_LABELS[state as PetMood] ?? `动画 ${row}`;
    }
  }
  if (row < CODEX_ROW_NAMES.length) return CODEX_ROW_NAMES[row];
  return `动画 ${row}`;
}

// 悬浮宠物尺寸（px，连续可调）
export const PET_SIZE_MIN = 48;
export const PET_SIZE_MAX = 160;
export const PET_SIZE_STEP = 4;
const DEFAULT_SIZE = 96;

function loadSize(): number {
  const raw = Number(localStorage.getItem(SIZE_KEY));
  if (!Number.isFinite(raw)) return DEFAULT_SIZE;
  return Math.min(PET_SIZE_MAX, Math.max(PET_SIZE_MIN, raw));
}

/** 单个会话的在跑任务（按 sessionId 存于 tasks map） */
export interface PetTask {
  label: string | null;
  tool: string | null;
  updatedAt: number;
  /** 'done' = 已完成（打勾停留片刻再移除）；undefined = 进行中 */
  status?: 'done';
  doneAt?: number;
  /** 渠道来源（feishu/dingtalk/wecom/wecom_bot），仅远端任务有 */
  source?: string;
  /** 渠道会话标识，同一渠道聊天的多个 session 共享，用于同会话只保留最新一条 */
  chatKey?: string;
  /** 会话所属 Agent（点击任务条跳转会话页用）；ChatPage 通用会话无 */
  agentId?: string;
  /** 远端任务（渠道触发，轮询同步）；本地任务来自本页 SSE */
  remote?: boolean;
}

interface PetState {
  activePet: UserPet | null;
  enabled: boolean;
  mood: PetMood;
  /** 用户指定播放的精灵图行（互动菜单/左键点击），播放完自动回 null */
  actionRow: number | null;
  /** actionRow 播放时的帧率（右键菜单播得慢），null = 默认 */
  actionFps: number | null;
  /** 各会话正在处理的任务（key = sessionId），done/error/interrupt 或超时后移除 */
  tasks: Record<string, PetTask>;
  size: number;
  loaded: boolean;

  loadActive: () => Promise<void>;
  setEnabled: (enabled: boolean) => void;
  setSize: (size: number) => void;
  /** 开始一个任务（发送消息时调用），宠物旁展示任务条 */
  startTask: (sessionId: string, label: string, agentId?: string) => void;
  /** 手动关闭任务条（用户点 × 移除，不走超时） */
  removeTask: (sessionId: string) => void;
  /** SSE 事件驱动的状态机入口（ChatPage / AgentChatPage 每个事件调用一次） */
  reportEvent: (eventType: string, opts?: {
    sessionId?: string;
    tool?: string;
    petAction?: { name?: string; row?: number };
  }) => void;
  /** 轮询同步渠道触发的在跑任务（全量替换 remote 条目） */
  syncRemoteTasks: (list: PetActiveTask[]) => void;
  /** SSE 事件处理：snapshot / started / tool / finished */
  applyRemoteTaskEvent: (ev: Record<string, unknown>) => void;
  /** 播放指定精灵图行动画 + 后端 interact 互动上报（刷新实例数据） */
  playRow: (row: number, opts?: { durationMs?: number; fps?: number }) => Promise<void>;
  /** 按动作名播放动画（不调 interact；智能体触发的情绪动作走这里） */
  playAction: (name: string) => void;
}

let resetTimer: ReturnType<typeof setTimeout> | null = null;

/** 任务无事件超过该时长视为流已中断（页面关闭/断网未收到 error），自动清除任务条 */
const TASK_STALE_MS = 10 * 60 * 1000;
const TASK_SWEEP_MS = 60 * 1000;
/** 已完成任务打勾停留时长 */
const DONE_LINGER_MS = 10 * 60 * 1000;

function pruneTasks(tasks: Record<string, PetTask>): Record<string, PetTask> {
  const now = Date.now();
  let changed = false;
  const next: Record<string, PetTask> = {};
  for (const [id, t] of Object.entries(tasks)) {
    const alive = t.status === 'done'
      ? now - (t.doneAt ?? 0) < DONE_LINGER_MS
      : now - t.updatedAt < TASK_STALE_MS;
    if (alive) next[id] = t;
    else changed = true;
  }
  return changed ? next : tasks;
}

function markDone(tasks: Record<string, PetTask>, sessionId: string | undefined, doneAt: number): Record<string, PetTask> {
  if (!sessionId || !(sessionId in tasks)) return tasks;
  const t = tasks[sessionId];
  if (t.status === 'done') return tasks;
  return { ...tasks, [sessionId]: { ...t, status: 'done', tool: null, doneAt } };
}

/** doneAt 校验防止误删：linger 期间同会话又起了新任务（队列下一条/渠道新消息）则不移除 */
function scheduleRemoval(sessionId: string, doneAt: number) {
  setTimeout(() => {
    usePetStore.setState((s) => {
      const t = s.tasks[sessionId];
      if (!t || t.status !== 'done' || t.doneAt !== doneAt) return s;
      const next = { ...s.tasks };
      delete next[sessionId];
      return { tasks: next };
    });
  }, DONE_LINGER_MS);
}

function dropTask(tasks: Record<string, PetTask>, sessionId?: string): Record<string, PetTask> {
  if (!sessionId || !(sessionId in tasks)) return tasks;
  const next = { ...tasks };
  delete next[sessionId];
  return next;
}

/** 单条远端任务 upsert：新消息到达替换为最新 label 并回到进行中；
 *  同 chat_key 的旧条目（/new 换 session 后的残留）直接丢弃，由新任务替换 */
function mergeIncoming(tasks: Record<string, PetTask>, t: PetActiveTask): Record<string, PetTask> {
  const key = t.chat_key || t.session_id;
  const next: Record<string, PetTask> = {};
  for (const [sid, task] of Object.entries(tasks)) {
    if (sid !== t.session_id && (task.chatKey || sid) === key) continue; // 同渠道旧会话由新任务替换
    next[sid] = task;
  }
  const existing = next[t.session_id];
  next[t.session_id] = {
    ...(existing ?? {}),
    label: t.label || existing?.label || null,
    tool: t.tool,
    source: t.source ?? existing?.source,
    chatKey: t.chat_key || existing?.chatKey || undefined,
    agentId: t.agent_id || existing?.agentId || undefined,
    remote: true,
    status: undefined,
    doneAt: undefined,
    updatedAt: Date.now(),
  };
  return next;
}

function touchTask(tasks: Record<string, PetTask>, sessionId?: string): Record<string, PetTask> {
  if (!sessionId || !(sessionId in tasks)) return tasks;
  return { ...tasks, [sessionId]: { ...tasks[sessionId], updatedAt: Date.now() } };
}

function scheduleReset(set: (s: Partial<PetState>) => void, ms: number) {
  if (resetTimer) clearTimeout(resetTimer);
  resetTimer = setTimeout(() => {
    resetTimer = null;
    set({ actionRow: null, actionFps: null, mood: 'idle' });
  }, ms);
}

export const usePetStore = create<PetState>((set, get) => ({
  activePet: null,
  enabled: localStorage.getItem(ENABLED_KEY) !== '0',
  mood: 'idle',
  actionRow: null,
  actionFps: null,
  tasks: {},
  size: loadSize(),
  loaded: false,

  loadActive: async () => {
    try {
      const pet = await petsApi.active();
      set({ activePet: pet, loaded: true });
    } catch {
      // 宠物接口失败不影响主流程 — 静默降级为不显示
      set({ activePet: null, loaded: true });
    }
  },

  setEnabled: (enabled) => {
    localStorage.setItem(ENABLED_KEY, enabled ? '1' : '0');
    set({ enabled });
  },

  setSize: (size) => {
    localStorage.setItem(SIZE_KEY, String(size));
    set({ size });
  },

  startTask: (sessionId, label, agentId) => {
    const trimmed = label.trim().replace(/\s+/g, ' ');
    if (resetTimer) { clearTimeout(resetTimer); resetTimer = null; }
    set((s) => ({
      tasks: {
        ...s.tasks,
        [sessionId]: {
          label: trimmed ? trimmed.slice(0, 30) : null,
          tool: null,
          agentId: agentId ?? undefined,
          updatedAt: Date.now(),
        },
      },
    }));
  },

  removeTask: (sessionId) => {
    set((s) => {
      if (!(sessionId in s.tasks)) return s;
      const next = { ...s.tasks };
      delete next[sessionId];
      return { tasks: next };
    });
  },

  reportEvent: (eventType, opts) => {
    if (!get().activePet) return;
    const sessionId = opts?.sessionId;
    switch (eventType) {
      case 'tool_call':
      case 'delegation_tool_call':
        if (resetTimer) { clearTimeout(resetTimer); resetTimer = null; }
        set((s) => ({
          mood: 'work',
          tasks: sessionId && s.tasks[sessionId]
            ? { ...s.tasks, [sessionId]: { ...s.tasks[sessionId], tool: opts?.tool ?? null, updatedAt: Date.now() } }
            : s.tasks,
        }));
        break;
      case 'thinking':
      case 'text_delta':
      case 'tool_result':
      case 'delegation_thinking':
      case 'delegation_text_delta':
      case 'delegation_tool_result':
        if (resetTimer) { clearTimeout(resetTimer); resetTimer = null; }
        set((s) => ({ mood: 'think', tasks: touchTask(s.tasks, sessionId) }));
        break;
      case 'confirmation_required':
        if (resetTimer) { clearTimeout(resetTimer); resetTimer = null; }
        set((s) => ({ mood: 'wait', tasks: touchTask(s.tasks, sessionId) }));
        break;
      case 'confirmation_resolved':
        set((s) => ({ mood: 'think', tasks: touchTask(s.tasks, sessionId) }));
        break;
      case 'pet_action': {
        // 智能体主动触发的情绪动作（闲聊/气泡），按 name 或 row 播放，不触发互动上报
        const paName = opts?.petAction?.name;
        const paRow = opts?.petAction?.row;
        if (typeof paRow === 'number') {
          set({ actionRow: paRow, actionFps: null, mood: 'happy' });
          scheduleReset(set, HAPPY_MS);
        } else if (paName) {
          get().playAction(paName);
        }
        break;
      }
      case 'done': {
        const doneAt = Date.now();
        set((s) => ({ mood: 'celebrate', tasks: markDone(s.tasks, sessionId, doneAt) }));
        if (sessionId) scheduleRemoval(sessionId, doneAt);
        scheduleReset(set, CELEBRATE_MS);
        break;
      }
      case 'error':
      case 'interrupt':
        set((s) => ({ mood: 'sad', tasks: dropTask(s.tasks, sessionId) }));
        scheduleReset(set, SAD_MS);
        break;
      default:
        break;
    }
  },

  syncRemoteTasks: (list) => {
    const now = Date.now();
    // 同一渠道会话（chat_key）只保留最新一条（/new 会换 session_id，但用户视角是同一会话）
    const latestByChat = new Map<string, PetActiveTask>();
    for (const t of list) {
      const key = t.chat_key || t.session_id;
      const prev = latestByChat.get(key);
      if (!prev || t.started_at > prev.started_at) latestByChat.set(key, t);
    }
    const incoming = [...latestByChat.values()];
    const incomingIds = new Set(incoming.map((t) => t.session_id));
    const incomingChatKeys = new Set(incoming.map((t) => t.chat_key || t.session_id));

    const current = get().tasks;
    const newlyDone: string[] = [];
    let next: Record<string, PetTask> = {};
    for (const [sid, t] of Object.entries(current)) {
      if (!t.remote) {
        next[sid] = t;
        continue;
      }
      if (incomingIds.has(sid)) continue; // 由 incoming 写入最新状态
      // 同会话已有更新的任务：旧条目（含 done 停留残留）直接丢弃，只保留最后一条
      if (incomingChatKeys.has(t.chatKey || sid)) continue;
      if (t.status === 'done') {
        next[sid] = t; // 等 linger 定时器清除
        continue;
      }
      // 远端条目从轮询结果消失 = 后端已完成：打勾停留，由 linger 定时器移除
      next[sid] = { ...t, status: 'done', tool: null, doneAt: now };
      newlyDone.push(sid);
    }
    for (const t of incoming) {
      next = mergeIncoming(next, t);
    }
    set({ tasks: next });
    for (const sid of newlyDone) scheduleRemoval(sid, now);
  },

  applyRemoteTaskEvent: (ev) => {
    if (!get().activePet) return;
    const type = ev.type as string;
    if (type === 'pet_task_snapshot') {
      get().syncRemoteTasks((ev.tasks as PetActiveTask[]) || []);
      return;
    }
    if (type === 'pet_task_started') {
      const t = ev.task as PetActiveTask;
      if (!t) return;
      set((s) => ({ tasks: mergeIncoming(s.tasks, t) }));
      return;
    }
    if (type === 'pet_task_tool') {
      const sessionId = ev.session_id as string | undefined;
      if (!sessionId) return;
      const tool = (ev.tool as string | null) ?? null;
      set((s) => {
        const t = s.tasks[sessionId];
        if (!t) return s;
        return { tasks: { ...s.tasks, [sessionId]: { ...t, tool, updatedAt: Date.now() } } };
      });
      return;
    }
    if (type === 'pet_task_finished') {
      const sessionId = ev.session_id as string | undefined;
      if (!sessionId) return;
      const doneAt = Date.now();
      set((s) => ({ tasks: markDone(s.tasks, sessionId, doneAt) }));
      scheduleRemoval(sessionId, doneAt);
    }
  },

  playRow: async (row, opts) => {
    const pet = get().activePet;
    if (!pet) return;
    // 本地立即反馈（不依赖接口）：直接播放该行动画
    set({ actionRow: row, actionFps: opts?.fps ?? null, mood: 'happy' });
    scheduleReset(set, opts?.durationMs ?? HAPPY_MS);
    try {
      const updated = await petsApi.interact(pet.id);
      set({ activePet: updated });
    } catch {
      // 互动上报失败不影响体验
    }
  },

  playAction: (name) => {
    const pet = get().activePet;
    if (!pet || !name) return;
    const match = (pet.actions ?? []).find((a) => a.name === name);
    if (match && typeof match.row === 'number') {
      set({ actionRow: match.row, actionFps: null, mood: 'happy' });
      scheduleReset(set, HAPPY_MS);
    }
  },
}));

// 周期性清扫过期任务（流中断未收到结束事件时任务条不残留）
setInterval(() => {
  const s = usePetStore.getState();
  const pruned = pruneTasks(s.tasks);
  if (pruned !== s.tasks) usePetStore.setState({ tasks: pruned });
}, TASK_SWEEP_MS);
