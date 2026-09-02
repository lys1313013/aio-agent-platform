/** Cursor controller — runner 与 VirtualCursor 组件之间的命令式桥（M2）。
 *
 * VirtualCursor 挂载时 attach 自己的实现；runner 不依赖 React 状态，
 * 直接调用本单例。所有方法在组件未挂载时安全降级为 no-op。
 */

export interface SpotlightTarget {
  rect: { x: number; y: number; width: number; height: number };
  /** 元素真实文本（非 LLM 自报描述） */
  label: string;
}

export interface CursorApi {
  /** 平滑移动光标到目标点，动画结束后 resolve */
  moveTo(x: number, y: number): Promise<void>;
  /** 聚光灯高亮目标区域（挖孔遮罩 + 描边 + 气泡） */
  spotlight(target: SpotlightTarget): void;
  /** 点击涟漪 */
  ripple(): void;
  /** 输入视觉反馈（打字机效果气泡） */
  typeText(text: string): void;
  /** 清除高亮与气泡 */
  clear(): void;
  /** 显示/隐藏光标 */
  setVisible(visible: boolean): void;
  /** 当前光标位置（未移动过则为 null） */
  getPos(): { x: number; y: number } | null;
}

let impl: CursorApi | null = null;

const noop: CursorApi = {
  moveTo: () => Promise.resolve(),
  spotlight: () => {},
  ripple: () => {},
  typeText: () => {},
  clear: () => {},
  setVisible: () => {},
  getPos: () => null,
};

export const cursorController: CursorApi = {
  moveTo: (x, y) => (impl ?? noop).moveTo(x, y),
  spotlight: (t) => (impl ?? noop).spotlight(t),
  ripple: () => (impl ?? noop).ripple(),
  typeText: (t) => (impl ?? noop).typeText(t),
  clear: () => (impl ?? noop).clear(),
  setVisible: (v) => (impl ?? noop).setVisible(v),
  getPos: () => (impl ?? noop).getPos(),
};

export function attachCursor(api: CursorApi): () => void {
  impl = api;
  return () => {
    if (impl === api) impl = null;
  };
}
