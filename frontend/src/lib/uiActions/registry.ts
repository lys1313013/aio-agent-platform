/** Frontend action registry — 页内动作注册表（docs/22-浏览器页面自动化 §3.1）。
 *
 * 工具（后端 6 个 ui_* 固定工具）≠ 动作（本注册表中各页面动态注册的具名操作）。
 * 页面组件 useEffect 注册、卸载注销（register 返回注销函数）。
 */

import { snapshotEngine } from './snapshot';

export type UiActionRisk = 'read' | 'write' | 'dangerous';

export interface UiActionDef {
  /** 唯一名称，如 "agents.open_create_modal" */
  name: string;
  /** 给 LLM 看的语义描述（一句话） */
  description: string;
  /** 参数 JSON Schema（走 tools 通道，不进 system prompt） */
  parameters?: Record<string, unknown>;
  risk: UiActionRisk;
  /** 执行体：target 解析与操作都在 handler 内完成 */
  handler: (args: Record<string, unknown>) => Promise<unknown>;
  /** 可选：动作锚点元素选择器，用于光标定位与 spotlight */
  anchorSelector?: string;
}

class FrontendActionRegistry {
  private actions = new Map<string, UiActionDef>();
  private listeners = new Set<() => void>();

  register(def: UiActionDef): () => void {
    this.actions.set(def.name, def);
    this.emit();
    return () => {
      this.actions.delete(def.name);
      this.emit();
    };
  }

  get(name: string): UiActionDef | undefined {
    return this.actions.get(name);
  }

  list(): UiActionDef[] {
    return [...this.actions.values()];
  }

  /** 注册表变更订阅（page_context 上报用） */
  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private emit(): void {
    this.listeners.forEach((l) => l());
  }
}

export const frontendActionRegistry = new FrontendActionRegistry();

/** 构建上报给后端的 page_context（ChatRequest.page_context / respond 回包）。
 *  保持短小：每轮 ReAct 迭代都会随 system prompt 重发。 */
export function buildPageContext(): import('@/lib/types').PageContext {
  const actions = frontendActionRegistry.list();
  return {
    page_path: window.location.pathname,
    page_title: document.title,
    actions: actions.map((a) => ({
      name: a.name,
      description: a.description,
      risk: a.risk,
    })),
    snapshot_version: snapshotEngine.snapshotVersion,
    // dangerous_refs 双源：注册表声明的 dangerous 动作名 + 快照启发式标出的 @eN
    dangerous_refs: [
      ...actions.filter((a) => a.risk === 'dangerous').map((a) => a.name),
      ...snapshotEngine.dangerousRefs,
    ],
  };
}
