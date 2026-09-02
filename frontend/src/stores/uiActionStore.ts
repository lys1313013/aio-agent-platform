/** UI action store — 页内浏览器操作的全局事件分发层（docs/22-浏览器页面自动化 §3.3）。
 *
 * 4 处 SSE 消费点（AgentChatPage handleSend / handleReconnect、ChatPage、
 * PetChatPanel）只负责把事件写入本 store；runner（挂 AppLayout）订阅 queue
 * 串行执行。页面卸载/切换不影响执行与展示；action_id 幂等去重防双流。
 */

import { create } from 'zustand';

export interface UiActionPayload {
  action_id: string;
  tool_call_id: string;
  session_id: string;
  action: string;
  args: Record<string, unknown>;
  risk: 'read' | 'write' | 'dangerous';
  confirmed: boolean;
  created_at: string;
}

export interface UiActionResolved {
  action_id: string;
  tool_call_id: string;
  status: string;
  result?: Record<string, unknown>;
  resolved_at?: string;
}

interface UiActionState {
  /** 待执行队列（runner 串行消费） */
  queue: UiActionPayload[];
  /** action_id → 终态（UiActionCard 渲染用） */
  resolved: Record<string, UiActionResolved>;
  /** action_id 幂等去重（SSE 重连/双流重复投递） */
  seen: Record<string, true>;
  /** 当前正在执行的动作（互斥锁持有者），null 表示空闲 */
  active: UiActionPayload | null;
  enqueue: (p: UiActionPayload) => void;
  markResolved: (r: UiActionResolved) => void;
  /** runner 取下一个动作开始执行 */
  activateNext: () => UiActionPayload | null;
  /** 当前动作执行完毕 */
  finishActive: () => void;
}

export const useUiActionStore = create<UiActionState>((set, get) => ({
  queue: [],
  resolved: {},
  seen: {},
  active: null,

  enqueue: (p) => {
    if (!p.action_id) return;
    const { seen, queue, active } = get();
    if (seen[p.action_id]) return; // 幂等去重
    set({
      seen: { ...seen, [p.action_id]: true },
      queue: active?.action_id === p.action_id ? queue : [...queue, p],
    });
  },

  markResolved: (r) => {
    if (!r.action_id) return;
    set({ resolved: { ...get().resolved, [r.action_id]: r } });
  },

  activateNext: () => {
    const { queue } = get();
    const next = queue[0] ?? null;
    if (next) {
      set({ queue: queue.slice(1), active: next });
    }
    return next;
  },

  finishActive: () => set({ active: null }),
}));

/** 非 React 上下文读取 store（runner 用） */
export const uiActionStore = useUiActionStore;
