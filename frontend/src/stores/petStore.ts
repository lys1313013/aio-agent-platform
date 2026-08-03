import { create } from 'zustand';
import { petsApi } from '@/lib/api';
import type { PetMood, PetPackage, UserPet } from '@/lib/types';

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

interface PetState {
  activePet: UserPet | null;
  enabled: boolean;
  mood: PetMood;
  /** 用户指定播放的精灵图行（互动菜单/左键点击），播放完自动回 null */
  actionRow: number | null;
  /** 正在处理的任务摘要（发消息时设置，done/error/interrupt 时清除） */
  taskLabel: string | null;
  /** 当前正在执行的工具名（tool_call 事件更新） */
  taskTool: string | null;
  size: number;
  loaded: boolean;

  loadActive: () => Promise<void>;
  setEnabled: (enabled: boolean) => void;
  setSize: (size: number) => void;
  /** 开始一个任务（发送消息时调用），宠物下方展示任务条 */
  startTask: (label: string) => void;
  /** SSE 事件驱动的状态机入口（ChatPage / AgentChatPage 每个事件调用一次），detail 目前用于 tool_call 传工具名 */
  reportEvent: (eventType: string, detail?: string) => void;
  /** 播放指定精灵图行动画 + 后端 interact(+1 exp，每日上限) */
  playRow: (row: number) => Promise<void>;
}

let resetTimer: ReturnType<typeof setTimeout> | null = null;

function scheduleReset(set: (s: Partial<PetState>) => void, ms: number) {
  if (resetTimer) clearTimeout(resetTimer);
  resetTimer = setTimeout(() => {
    resetTimer = null;
    set({ actionRow: null, mood: 'idle' });
  }, ms);
}

export const usePetStore = create<PetState>((set, get) => ({
  activePet: null,
  enabled: localStorage.getItem(ENABLED_KEY) !== '0',
  mood: 'idle',
  actionRow: null,
  taskLabel: null,
  taskTool: null,
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

  startTask: (label) => {
    const trimmed = label.trim().replace(/\s+/g, ' ');
    if (resetTimer) { clearTimeout(resetTimer); resetTimer = null; }
    set({ taskLabel: trimmed ? trimmed.slice(0, 30) : null, taskTool: null });
  },

  reportEvent: (eventType, detail) => {
    if (!get().activePet) return;
    switch (eventType) {
      case 'tool_call':
      case 'delegation_tool_call':
        if (resetTimer) { clearTimeout(resetTimer); resetTimer = null; }
        set({ mood: 'work', taskTool: detail ?? null });
        break;
      case 'thinking':
      case 'text_delta':
      case 'tool_result':
      case 'delegation_thinking':
      case 'delegation_text_delta':
      case 'delegation_tool_result':
        if (resetTimer) { clearTimeout(resetTimer); resetTimer = null; }
        set({ mood: 'think' });
        break;
      case 'confirmation_required':
        if (resetTimer) { clearTimeout(resetTimer); resetTimer = null; }
        set({ mood: 'wait' });
        break;
      case 'confirmation_resolved':
        set({ mood: 'think' });
        break;
      case 'done':
        set({ mood: 'celebrate', taskLabel: null, taskTool: null });
        scheduleReset(set, CELEBRATE_MS);
        break;
      case 'error':
      case 'interrupt':
        set({ mood: 'sad', taskLabel: null, taskTool: null });
        scheduleReset(set, SAD_MS);
        break;
      default:
        break;
    }
  },

  playRow: async (row) => {
    const pet = get().activePet;
    if (!pet) return;
    // 本地立即反馈（不依赖接口）：直接播放该行动画
    set({ actionRow: row, mood: 'happy' });
    scheduleReset(set, HAPPY_MS);
    try {
      const updated = await petsApi.interact(pet.id);
      set({ activePet: updated });
    } catch {
      // 互动上报失败不影响体验
    }
  },
}));
