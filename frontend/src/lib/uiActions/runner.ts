/** UI action runner — 前端动作执行器（docs/22-浏览器页面自动化 §3.2）。
 *
 * 订阅 uiActionStore 队列串行执行；会话互斥锁（同时刻只服务一个 session）；
 * 跨标签页互斥用 navigator.locks；Esc capture 阶段拦截中断（不误关 AntD 弹层）。
 *
 * M3 已落地：@eN 快照引用定位（snapshot.ts）、真实 DOM 事件序列点击、
 * ui_input 原生 setter 写入 + 打字机视觉、点击后效果观察（no_visible_effect）、
 * 回包自动携带 delta_snapshot；ui_screenshot 依赖 M4（当前返回明确错误）。
 */

import { uiActionsApi, type UiActionRespondPayload } from '@/lib/api';
import { cursorController } from '@/components/ui-agent/cursorController';
import { uiActionStore, type UiActionPayload } from '@/stores/uiActionStore';
import { usePetStore } from '@/stores/petStore';
import { buildPageContext, frontendActionRegistry } from './registry';
import { captureScreenshot, dispatchRealClick, snapshotEngine } from './snapshot';

/** react-router 的 navigate 由 AppLayout 注册（runner 不在 React 树内） */
let navigateFn: ((path: string) => void) | null = null;
export function registerNavigate(fn: (path: string) => void): void {
  navigateFn = fn;
}

const LOCK_NAME = 'aio-ui-runner';
let holdsTabLock = false;
let activeSessionId: string | null = null;
let abortRequested = false;
let started = false;

// 跨标签页锁：模块级单次请求（React StrictMode 双挂载/清理不会重复请求）。
// 不用 ifAvailable——排队请求，持锁标签页关闭后本页自动获锁，无需刷新。
let lockRequestStarted = false;
let resolveLockReady: (() => void) | null = null;
const lockReady = new Promise<void>((r) => {
  resolveLockReady = r;
});

function acquireTabLock(): void {
  if (lockRequestStarted) return;
  lockRequestStarted = true;
  if (!navigator.locks) {
    holdsTabLock = true;
    resolveLockReady?.();
    return;
  }
  void navigator.locks
    .request(LOCK_NAME, () => {
      holdsTabLock = true;
      resolveLockReady?.();
      // 持锁期间不释放（直到标签页关闭）
      return new Promise<void>(() => {});
    })
    .catch(() => {
      resolveLockReady?.();
    });
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

// ---- 中断：Esc capture 拦截（优先于 AntD Modal 的 keyboard 关闭） ----

function onKeyDownCapture(e: KeyboardEvent): void {
  if (e.key === 'Escape' && activeSessionId) {
    e.stopPropagation();
    e.preventDefault();
    abortRequested = true;
  }
}

// ---- 元素定位与标签 ----

function elementLabel(el: Element): string {
  const text =
    el.getAttribute('aria-label') ||
    el.getAttribute('title') ||
    (el.textContent || '').trim().slice(0, 50);
  return text || el.tagName.toLowerCase();
}

async function moveCursorTo(el: Element, labelPrefix: string): Promise<void> {
  el.scrollIntoView({ block: 'center', behavior: 'instant' as ScrollBehavior });
  await sleep(120); // 等滚动稳定
  const rect = el.getBoundingClientRect();
  const x = rect.left + rect.width / 2;
  const y = rect.top + rect.height / 2;
  await cursorController.moveTo(x, y);
  cursorController.spotlight({
    rect: { x: rect.left, y: rect.top, width: rect.width, height: rect.height },
    label: `${labelPrefix}：${elementLabel(el)}`,
  });
  await sleep(300); // 让用户看清目标
  if (abortRequested) throw new Error('__aborted__');
}

// ---- 各工具执行体 ----

/** ui_navigate 白名单：平台已知路由的一级前缀（防跳转钓鱼/未知路径） */
const ROUTE_PREFIXES = [
  '/agents', '/chat', '/dashboard', '/usage', '/observability', '/memory',
  '/skills', '/pets', '/workspaces', '/portrait', '/settings', '/models',
  '/system-config', '/mcp-servers', '/knowledge', '/knowledge-graph',
  '/remote-tools', '/web-tools', '/channels', '/cron-jobs', '/tenants',
  '/users',
];

async function execNavigate(args: Record<string, unknown>): Promise<Record<string, unknown>> {
  const path = String(args.path || '');
  // 白名单：仅站内已知路由，拒绝外链与 protocol-relative
  if (
    !path.startsWith('/') ||
    path.startsWith('//') ||
    !ROUTE_PREFIXES.some((p) => path === p || path.startsWith(`${p}/`))
  ) {
    throw new Error('invalid_path: only known in-app routes are allowed');
  }
  if (!navigateFn) throw new Error('router_not_ready');
  navigateFn(path);
  await sleep(400); // 等路由渲染
  return { navigated_to: window.location.pathname };
}

async function execClick(
  action: UiActionPayload,
): Promise<Record<string, unknown>> {
  const { args } = action;
  const actionRef = args.action_ref as string | undefined;
  const ref = args.ref as string | undefined;

  if (actionRef) {
    const def = frontendActionRegistry.get(actionRef);
    if (!def) throw new Error('action_not_available');
    // 执行时 dangerous 复检（最终闸门）：注册表声明 dangerous 但后端未确认
    if (def.risk === 'dangerous' && !action.confirmed) {
      throw new Error('dangerous_not_confirmed');
    }
    if (def.anchorSelector) {
      const el = document.querySelector(def.anchorSelector);
      if (el) await moveCursorTo(el, '点击');
    }
    const tick0 = snapshotEngine.mutationTick;
    const result = await def.handler(args);
    cursorController.ripple();
    const warning = await observeEffect(tick0);
    return {
      ...((result as Record<string, unknown>) ?? { clicked: actionRef }),
      ...(warning ? { warning } : {}),
    };
  }

  if (ref) {
    const el = snapshotEngine.resolveRef(ref);
    if (!el) throw new Error('stale_ref');
    // 执行时 dangerous 复检（最终闸门）：快照启发式判定，未确认则拒绝
    if (snapshotEngine.isDangerous(el) && !action.confirmed) {
      throw new Error('dangerous_not_confirmed');
    }
    await moveCursorTo(el, '点击');
    const rect = el.getBoundingClientRect();
    const x = rect.left + rect.width / 2;
    const y = rect.top + rect.height / 2;
    const tick0 = snapshotEngine.mutationTick;
    dispatchRealClick(el, x, y);
    cursorController.ripple();
    const warning = await observeEffect(tick0);
    return { clicked: ref, label: elementLabel(el), ...(warning ? { warning } : {}) };
  }
  throw new Error('element_not_found');
}

/** 点击后验证（§2.2c）：800ms 观察窗口内无任何页面响应 → warning */
async function observeEffect(tick0: number): Promise<string | null> {
  const url = window.location.pathname;
  const active = document.activeElement;
  await sleep(800);
  const changed =
    snapshotEngine.mutationTick !== tick0 ||
    window.location.pathname !== url ||
    document.activeElement !== active ||
    document.querySelector('.ant-spin-spinning, .ant-btn-loading') !== null;
  return changed ? null : 'no_visible_effect';
}

async function execInput(
  action: UiActionPayload,
): Promise<Record<string, unknown>> {
  const { args } = action;
  const ref = args.ref as string | undefined;
  const value = String(args.value ?? '');
  if (!ref) throw new Error('element_not_found');
  const el = snapshotEngine.resolveRef(ref);
  if (!el) throw new Error('stale_ref');
  if (snapshotEngine.isDangerous(el) && !action.confirmed) {
    throw new Error('dangerous_not_confirmed');
  }
  const isNative =
    el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement;
  const isEditable = el instanceof HTMLElement && el.isContentEditable;
  if (!isNative && !isEditable && el.getAttribute('role') !== 'textbox') {
    throw new Error('not_input: ref 指向的不是输入框');
  }

  await moveCursorTo(el, '输入');
  const tick0 = snapshotEngine.mutationTick;
  if (isNative) {
    // React 受控组件：必须走原生 setter 再派事件，否则值被覆盖回弹
    const proto =
      el instanceof HTMLTextAreaElement
        ? HTMLTextAreaElement.prototype
        : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
    el.focus();
    if (setter) setter.call(el, value);
    else el.value = value;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  } else {
    (el as HTMLElement).focus();
    el.textContent = value;
    el.dispatchEvent(new Event('input', { bubbles: true }));
  }
  // 视觉层打字机：真实值已一次性写入，这里只播动画（不 await，避免长文本拖慢回包）
  cursorController.typeText(value.slice(0, 50));
  // 输入的效果即值写入本身（不产生 childList mutation）；值已写入则不报 warning
  const written = isNative
    ? (el as HTMLInputElement | HTMLTextAreaElement).value === value
    : el.textContent === value;
  const warning = written ? null : await observeEffect(tick0);
  return { filled: ref, length: value.length, ...(warning ? { warning } : {}) };
}

async function execScrollTo(
  action: UiActionPayload,
): Promise<Record<string, unknown>> {
  const ref = action.args.ref as string | undefined;
  if (!ref) throw new Error('element_not_found');
  const el = snapshotEngine.resolveRef(ref);
  if (!el) throw new Error('stale_ref');
  el.scrollIntoView({ block: 'center', behavior: 'instant' as ScrollBehavior });
  await sleep(300);
  return { scrolled_to: ref, label: elementLabel(el) };
}

async function execReadScreen(
  action: UiActionPayload,
): Promise<Record<string, unknown>> {
  const mode = action.args.mode === 'full' ? 'full' : 'compact';
  const snap = snapshotEngine.capture(mode);
  if (snap.unchanged) {
    return { unchanged: true, snapshot_version: snap.snapshot_version };
  }
  return {
    page_path: snap.page_path,
    page_title: snap.page_title,
    snapshot_version: snap.snapshot_version,
    ref_count: snap.ref_count,
    truncated: snap.truncated || undefined,
    tree: snap.tree,
  };
}

/** M4：SoM 截图。截图走 payload.image 独立字段，由后端作为 user 角色图片消息注入 */
async function execScreenshot(
  action: UiActionPayload,
): Promise<{ result: Record<string, unknown>; image: string }> {
  const annotate = action.args.annotate !== false;
  const shot = await captureScreenshot(annotate);
  return {
    result: {
      screenshot: `[screenshot v${shot.snapshot_version} ${shot.width}x${shot.height}, ${shot.marks} marks]`,
      snapshot_version: shot.snapshot_version,
      note: '截图已作为图片消息注入。图中编号与 @eN 引用一一对应，仍须用 ui_click/ui_input 的 ref 操作元素，禁止输出坐标。',
    },
    image: shot.dataUri,
  };
}

// ---- 主循环 ----

/** 执行看门狗：必须小于后端 60s 超时，保证回包一定先于后端超时 */
const EXEC_WATCHDOG_MS = 45_000;

function log(...args: unknown[]): void {
  // eslint-disable-next-line no-console
  console.info('[ui-runner]', ...args);
}

async function execute(action: UiActionPayload): Promise<void> {
  const store = uiActionStore.getState();
  // 宠物联动（§3.4）：执行开始切 work 动作
  usePetStore.getState().reportEvent('tool_call', {
    sessionId: action.session_id,
    tool: action.action,
  });
  try {
    let result: Record<string, unknown>;
    let image: string | undefined;
    switch (action.action) {
      case 'ui_navigate':
        result = await execNavigate(action.args);
        break;
      case 'ui_click':
        result = await execClick(action);
        break;
      case 'ui_input':
        result = await execInput(action);
        break;
      case 'ui_scroll_to':
        result = await execScrollTo(action);
        break;
      case 'ui_read_screen':
        result = await execReadScreen(action);
        break;
      case 'ui_screenshot': {
        const shot = await execScreenshot(action);
        result = shot.result;
        image = shot.image;
        break;
      }
      default:
        throw new Error(`unknown_action: ${action.action}`);
    }
    // 点击/输入/滚动后自动附带增量快照：多数情况下 Agent 无需再调 ui_read_screen
    // （delta 不变量：未展开子树的旧 ref 继续有效；新 ref 单调递增不复用）
    const mutating = ['ui_click', 'ui_input', 'ui_scroll_to', 'ui_navigate'].includes(
      action.action,
    );
    const delta = mutating ? snapshotEngine.captureDelta() : '';
    const payload: UiActionRespondPayload = {
      status: 'ok',
      result,
      snapshot_version: snapshotEngine.snapshotVersion,
      dangerous_refs: snapshotEngine.dangerousRefs,
      page_context: buildPageContext(),
      ...(delta ? { delta_snapshot: delta } : {}),
      ...(image ? { image } : {}),
    };
    await uiActionsApi.respond(action.action_id, payload);
    store.markResolved({
      action_id: action.action_id,
      tool_call_id: action.tool_call_id,
      status: 'ok',
      result,
    });
    // 成功 → think（终态 celebrate 由 SSE done 事件驱动，避免提前把任务条标记完成）
    usePetStore.getState().reportEvent('tool_result', { sessionId: action.session_id });
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    const aborted = message === '__aborted__';
    await uiActionsApi
      .respond(action.action_id, {
        status: aborted ? 'cancelled' : 'error',
        error: aborted ? 'cancelled' : message,
        snapshot_version: snapshotEngine.snapshotVersion,
        page_context: buildPageContext(),
      })
      .catch(() => {});
    store.markResolved({
      action_id: action.action_id,
      tool_call_id: action.tool_call_id,
      status: aborted ? 'cancelled' : 'error',
      result: { error: aborted ? 'cancelled' : message },
    });
    // 失败/中断 → sad（§3.4）
    usePetStore.getState().reportEvent(aborted ? 'interrupt' : 'error', {
      sessionId: action.session_id,
    });
  } finally {
    cursorController.clear();
    cursorController.setVisible(false);
  }
}

/** busy 快速回包：activateNext 取出后立即终态化，避免 active 悬挂卡死泵 */
async function rejectBusy(
  action: UiActionPayload,
  result?: Record<string, unknown>,
): Promise<void> {
  const store = uiActionStore.getState();
  await uiActionsApi
    .respond(action.action_id, { status: 'error', error: 'ui_busy', result })
    .catch(() => {});
  store.markResolved({
    action_id: action.action_id,
    tool_call_id: action.tool_call_id,
    status: 'error',
    result: { error: 'ui_busy', ...result },
  });
  store.finishActive();
}

async function pump(): Promise<void> {
  const store = uiActionStore.getState();
  // 会话互斥：同时刻只服务一个 session
  const next = store.queue[0];
  if (!next) return;
  log('pump', next.action, next.action_id, 'holdsTabLock =', holdsTabLock);
  if (activeSessionId && next.session_id !== activeSessionId) {
    // 非当前持有 session：立即回包 ui_busy（不干等 60s 超时）
    store.activateNext();
    await rejectBusy(next, { holder_session_id: activeSessionId });
    return pump();
  }
  // 跨标签页互斥：等待锁就绪（启动窗口或对方持锁时最多等 1.5s，避免干等 60s 超时）
  await Promise.race([lockReady, sleep(1500)]);
  if (uiActionStore.getState().queue[0] !== next) return pump(); // await 期间队列已变化
  if (!holdsTabLock) {
    store.activateNext();
    await rejectBusy(next, { reason: 'another_tab_holds_lock' });
    return pump();
  }

  const action = store.activateNext();
  if (!action) return;
  activeSessionId = action.session_id;
  abortRequested = false;
  log('execute start', action.action, action.action_id);
  // 看门狗：execute 挂死（如后台标签页 rAF 暂停）时兜底回包 + 复位，防泵永久卡死。
  // execute 正常完成后必须取消定时器（Promise.race 不会取消输家）
  let watchdogFired = false;
  let cancelWatchdog: () => void = () => {};
  const watchdog = new Promise<void>((resolve) => {
    const timer = setTimeout(() => {
      void (async () => {
        watchdogFired = true;
        log('watchdog fired', action.action_id);
        await uiActionsApi
          .respond(action.action_id, { status: 'error', error: 'execution_timeout' })
          .catch(() => {});
        uiActionStore.getState().markResolved({
          action_id: action.action_id,
          tool_call_id: action.tool_call_id,
          status: 'error',
          result: { error: 'execution_timeout' },
        });
        resolve();
      })();
    }, EXEC_WATCHDOG_MS);
    cancelWatchdog = () => {
      clearTimeout(timer);
      resolve();
    };
  });
  await Promise.race([execute(action), watchdog]);
  cancelWatchdog();
  if (watchdogFired) cursorController.clear();
  activeSessionId = null;
  store.finishActive();
  // 串行消费后续队列
  if (uiActionStore.getState().queue.length > 0) void pump();
}

function onBeforeUnload(): void {
  if (activeSessionId) {
    uiActionsApi.cancelAllBeacon(activeSessionId);
  }
}

/** 启动 runner（AppLayout 挂载时调用一次） */
export function startUiActionRunner(): () => void {
  acquireTabLock();
  if (started) return () => {};
  started = true;

  window.addEventListener('keydown', onKeyDownCapture, true);
  window.addEventListener('beforeunload', onBeforeUnload);
  const unsubscribe = uiActionStore.subscribe(() => {
    const { queue, active } = uiActionStore.getState();
    if (queue.length > 0 && !active) void pump();
  });

  return () => {
    started = false;
    unsubscribe();
    window.removeEventListener('keydown', onKeyDownCapture, true);
    window.removeEventListener('beforeunload', onBeforeUnload);
  };
}
