/** Snapshot-Ref 快照引擎 — 紧凑可访问性树 + @eN 引用（docs/22-浏览器页面自动化 §2.2a/§3.1a）。
 *
 * 参考 agent-browser / browser-use：只抽取可交互元素并编号，比全量 a11y 树省 ~90% token。
 * 关键不变量：
 *  - @eN 单调递增、跨快照不复用；元素未变化时 ref 跨快照保持稳定（delta 不变量 a）
 *  - 路由变化 / MutationObserver 检测到交互区域大变 → invalidate() 作废全部 ref
 *  - MutationObserver 排除自身动态子树（光标、宠物、聊天流式区），防误失效
 */

export interface SnapshotResult {
  snapshot_version: number;
  page_path: string;
  page_title: string;
  /** 紧凑 a11y 树文本（带 @eN）；unchanged 时为空串 */
  tree: string;
  /** 随每次 respond 上报后端的危险引用清单 */
  dangerous_refs: string[];
  /** 与上次哈希相同（页面无变化） */
  unchanged?: boolean;
  /** full 模式超上限被截断 */
  truncated?: boolean;
  ref_count: number;
}

interface SnapshotLine {
  ref: string;
  el: Element;
  text: string; // 完整行文本（含缩进与 @eN）
  dangerous: boolean;
}

const INTERACTIVE_SELECTOR = [
  'button',
  'a[href]',
  'input:not([type="hidden"])',
  'textarea',
  'select',
  '[role="button"]',
  '[role="link"]',
  '[role="tab"]',
  '[role="menuitem"]',
  '[role="checkbox"]',
  '[role="switch"]',
  '[role="combobox"]',
  '[role="listbox"]',
  '[role="option"]',
  '[role="textbox"]',
  '[onclick]',
  '[contenteditable="true"]',
  '[data-ui-action]',
].join(',');

/** 结构性容器：有文本标签且含可交互后代时输出为上下文行（表格行消歧） */
const STRUCTURAL_SELECTOR =
  'table, tr, form, ul, ol, li, [role="dialog"], [role="row"], [role="list"], .ant-card, .ant-list-item';

/** AntD 弹层容器（渲染在 body 末尾，快照尾部单独收集并标注 [in-modal]） */
const OVERLAY_SELECTOR = [
  '.ant-modal-wrap',
  '.ant-drawer-open',
  '.ant-popover',
  '.ant-dropdown',
  '.ant-select-dropdown',
  '.ant-picker-dropdown',
].join(',');

/** 快照与 MutationObserver 都排除的动态/自身子树 */
const EXCLUDE_SELECTOR = [
  '#virtual-cursor-root',
  '[data-pet-widget]',
  '[data-ui-exclude]',
  '.ant-message',
  '.ant-notification',
  'script',
  'style',
  'noscript',
].join(',');

/** 危险文案关键词（短文本才匹配，避免误伤长段落） */
const DANGER_RE = /删除|停用|清空|提交|支付/;
/** prompt injection 打标模式 */
const INJECT_RE = /忽略.*指令|ignore.*instructions|disregard.*instructions/i;

const TEXT_MAX = 50;
const COMPACT_MAX_LINES = 120;
const FULL_MAX_LINES = 400;
/** full 模式硬上限 ~4000 token（粗略 4 字符/token） */
const FULL_MAX_CHARS = 16000;
/** MutationObserver：防抖窗口内交互元素增删超过该阈值才 invalidate */
const INVALIDATE_THRESHOLD = 5;
const DEBOUNCE_MS = 500;

function truncate(s: string, max = TEXT_MAX): string {
  const t = s.trim().replace(/\s+/g, ' ');
  return t.length > max ? `${t.slice(0, max)}…` : t;
}

/** 元素文本标签：textContent → aria-label → title → placeholder → 内部图标 aria-label（AntD icon）→ 相邻文本 */
function labelOf(el: Element): string {
  const text = (el.textContent || '').trim();
  let label = text;
  if (!label) label = el.getAttribute('aria-label') || '';
  if (!label) label = el.getAttribute('title') || '';
  if (!label && (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement)) {
    label = el.placeholder || '';
  }
  if (!label && el instanceof HTMLImageElement) label = el.alt || '';
  if (!label) {
    // 纯图标按钮：AntD 图标是 <span role="img" aria-label="ellipsis">，取其语义
    const icon = el.querySelector('[role="img"][aria-label]');
    const iconLabel = icon?.getAttribute('aria-label');
    if (iconLabel) label = `${iconLabel} 图标`;
  }
  if (!label) {
    // 相邻文本兜底
    const sib = el.previousElementSibling || el.nextElementSibling;
    if (sib) label = (sib.textContent || '').trim();
  }
  label = truncate(label);
  if (INJECT_RE.test(label)) return '[filtered]';
  return label || el.tagName.toLowerCase();
}

function isVisible(el: Element): boolean {
  const rect = el.getBoundingClientRect();
  if (rect.width === 0 && rect.height === 0) return false;
  const style = window.getComputedStyle(el);
  return style.display !== 'none' && style.visibility !== 'hidden';
}

function inViewportArea(el: Element): boolean {
  const rect = el.getBoundingClientRect();
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  // 视口 ±1 屏
  return (
    rect.bottom >= -vh &&
    rect.top <= vh * 2 &&
    rect.right >= -vw * 0.5 &&
    rect.left <= vw * 1.5
  );
}

class SnapshotEngine {
  private version = 0;
  private counter = 0;
  /** ref → 元素（弱引用） */
  private refMap = new Map<string, WeakRef<Element>>();
  /** 元素 → 上次的 ref 与行文本（ref 稳定性/delta diff 用） */
  private elMap = new WeakMap<Element, { ref: string; text: string }>();
  private lastLines: SnapshotLine[] = [];
  private lastHash = '';
  private lastDangerousRefs: string[] = [];
  /** 变更计数：post-click 效果观察用 */
  mutationTick = 0;
  private pendingMutations = 0;
  private debounceTimer: ReturnType<typeof setTimeout> | undefined;
  private observer: MutationObserver | null = null;

  get snapshotVersion(): number {
    return this.version;
  }

  get dangerousRefs(): string[] {
    return this.lastDangerousRefs;
  }

  /** 作废全部 ref（路由变化 / 交互区域大变） */
  invalidate(): void {
    this.version += 1;
    this.refMap.clear();
    this.elMap = new WeakMap();
    this.lastLines = [];
    this.lastHash = '';
    this.lastDangerousRefs = [];
  }

  /** 启动 MutationObserver（AppLayout 挂载时调用一次）。
   *  只有"移除"计入失效阈值——新增元素不影响已有 ref 的有效性
   * （resolveRef 的 isConnected 检查已能逐个发现失效元素）；
   *  路由变化由 AppLayout 显式 invalidate()。 */
  startObserver(): () => void {
    if (this.observer) return () => {};
    this.observer = new MutationObserver((records) => {
      let removed = 0;
      let touched = false;
      for (const rec of records) {
        if (rec.type !== 'childList') continue; // 忽略 characterData/attributes
        const target = rec.target as Element;
        if (target.closest && target.closest(EXCLUDE_SELECTOR)) continue;
        const count = (nodes: NodeList) => {
          let n = 0;
          nodes.forEach((node) => {
            if (!(node instanceof Element)) return;
            if (node.closest(EXCLUDE_SELECTOR)) return;
            if (node.matches(INTERACTIVE_SELECTOR)) n += 1;
            n += node.querySelectorAll(INTERACTIVE_SELECTOR).length;
          });
          return n;
        };
        const added = count(rec.addedNodes);
        removed += count(rec.removedNodes);
        if (added > 0 || removed > 0) touched = true;
      }
      if (!touched) return;
      this.mutationTick += 1;
      this.pendingMutations += removed;
      clearTimeout(this.debounceTimer);
      this.debounceTimer = setTimeout(() => {
        if (this.pendingMutations > INVALIDATE_THRESHOLD) this.invalidate();
        this.pendingMutations = 0;
      }, DEBOUNCE_MS);
    });
    this.observer.observe(document.body, { childList: true, subtree: true });
    return () => {
      this.observer?.disconnect();
      this.observer = null;
      clearTimeout(this.debounceTimer);
    };
  }

  /** "@e4" → Element；ref 不存在或元素已脱离文档返回 null（stale_ref） */
  resolveRef(ref: string): Element | null {
    const weak = this.refMap.get(ref);
    if (!weak) return null;
    const el = weak.deref();
    if (!el || !el.isConnected) return null;
    return el;
  }

  /** 执行时 dangerous 复检（最终闸门）：类名 + 关键词 + 危险容器继承 */
  isDangerous(el: Element): boolean {
    // AntD 危险按钮（按钮本体或内部 icon/span）
    if (el.closest('.ant-btn-dangerous')) return true;
    // Popconfirm 内按钮（确认删除的 OK）、显式危险区域
    if (el.closest('.ant-popconfirm, [data-danger-zone]')) return true;
    const text = (el.textContent || '').trim();
    if (text.length <= 20 && DANGER_RE.test(text)) return true;
    return false;
  }

  /** 采集快照。compact=视口±1屏（默认），full=全页（硬上限截断） */
  capture(mode: 'compact' | 'full' = 'compact'): SnapshotResult {
    const lines: SnapshotLine[] = [];
    const mainRoot = document.querySelector('main') || document.body;

    this.walk(mainRoot, 0, false, mode, lines);
    // 弹层内容置快照尾部
    for (const overlay of document.querySelectorAll(OVERLAY_SELECTOR)) {
      if (overlay.closest(EXCLUDE_SELECTOR)) continue;
      if (!isVisible(overlay)) continue;
      const before = lines.length;
      this.walk(overlay, 1, true, mode, lines);
      if (lines.length > before) {
        lines.splice(before, 0, {
          ref: '',
          el: overlay,
          text: '  [in-modal]',
          dangerous: false,
        });
      }
    }

    const maxLines = mode === 'full' ? FULL_MAX_LINES : COMPACT_MAX_LINES;
    let truncated = lines.length > maxLines;
    const kept = truncated ? lines.slice(0, maxLines) : lines;
    let tree = kept.map((l) => l.text).join('\n');
    if (mode === 'full' && tree.length > FULL_MAX_CHARS) {
      tree = tree.slice(0, FULL_MAX_CHARS);
      truncated = true;
    }
    if (truncated) {
      tree += `\n... (已截断，共 ${lines.length} 个可交互元素，请用 ui_scroll_to 分段探索)`;
    }

    const dangerousRefs = kept.filter((l) => l.dangerous && l.ref).map((l) => l.ref);
    const hash = String(hashCode(tree));
    const unchanged = hash === this.lastHash && kept.length > 0;
    this.lastHash = hash;
    this.lastLines = kept.filter((l) => l.ref);
    this.lastDangerousRefs = dangerousRefs;

    return {
      snapshot_version: this.version,
      page_path: window.location.pathname,
      page_title: document.title,
      tree: unchanged ? '' : tree,
      dangerous_refs: dangerousRefs,
      unchanged,
      truncated,
      ref_count: kept.filter((l) => l.ref).length,
    };
  }

  /** 增量快照：与上次 capture 逐行 diff，未变化连续段折叠为 `...`（delta 不变量见 §2.2a） */
  captureDelta(): string {
    const prev = this.lastLines;
    const prevByEl = new Map<Element, SnapshotLine>();
    for (const l of prev) prevByEl.set(l.el, l);

    // 重新采集（capture 会复用未变化元素的 ref）
    const result = this.capture('compact');
    if (result.unchanged) return '';
    const curr = this.lastLines;

    const out: string[] = [];
    let runUnchanged = 0;
    const flush = () => {
      if (runUnchanged > 0) {
        out.push('...');
        runUnchanged = 0;
      }
    };
    for (const line of curr) {
      const old = prevByEl.get(line.el);
      if (old && old.text === line.text) {
        runUnchanged += 1;
      } else {
        flush();
        out.push(line.text);
      }
    }
    flush();
    // 消失的 ref（旧有新无）
    const currEls = new Set(curr.map((l) => l.el));
    for (const line of prev) {
      if (!currEls.has(line.el) && !line.el.isConnected) {
        out.push(`~ ${line.ref} (gone)`);
      }
    }
    return out.join('\n');
  }

  // ---- 内部 ----

  private emitInteractive(
    el: Element,
    indent: number,
    inModal: boolean,
    lines: SnapshotLine[],
  ): void {
    const label = labelOf(el);
    const tag = tagName(el);
    const dangerous = this.isDangerous(el);
    let text = `${'  '.repeat(indent)}- ${tag} "${label}"`;
    const prev = this.elMap.get(el);
    let ref: string;
    if (prev) {
      // 元素未变：ref 跨快照保持稳定（delta 不变量 a）
      ref = prev.ref;
    } else {
      this.counter += 1;
      ref = `@e${this.counter}`;
      this.refMap.set(ref, new WeakRef(el));
    }
    text += ` ${ref}`;
    // 属性只保留 name/label/placeholder/value/dangerous
    const props: string[] = [];
    if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) {
      if (el instanceof HTMLInputElement && el.type === 'password') {
        props.push('value: "***"');
      } else if (el.value) {
        props.push(`value: "${truncate(el.value, 30)}"`);
      }
    }
    if (el instanceof HTMLButtonElement || el.getAttribute('aria-disabled') === 'true') {
      if ((el as HTMLButtonElement).disabled || el.getAttribute('aria-disabled') === 'true') {
        props.push('disabled');
      }
    }
    if (props.length) text += ` {${props.join(', ')}}`;
    if (dangerous) text += ' [dangerous]';
    if (inModal) text += ' [in-modal]';
    const line: SnapshotLine = { ref, el, text, dangerous };
    this.elMap.set(el, { ref, text });
    lines.push(line);
  }

  private walk(
    root: Element,
    indent: number,
    inModal: boolean,
    mode: 'compact' | 'full',
    lines: SnapshotLine[],
  ): void {
    for (const child of Array.from(root.children)) {
      if (child.matches(EXCLUDE_SELECTOR)) continue;
      // 主流程排除弹层子树（尾部统一收集）
      if (!inModal && child.matches(OVERLAY_SELECTOR)) continue;

      const isInteractive = child.matches(INTERACTIVE_SELECTOR);
      if (isInteractive) {
        // 嵌套可交互元素只取最外层（button > span[onclick] 这类）
        if (child.parentElement?.closest(INTERACTIVE_SELECTOR)) {
          continue;
        }
        if (!isVisible(child)) continue;
        if (mode === 'compact' && !inModal && !inViewportArea(child)) continue;
        this.emitInteractive(child, indent, inModal, lines);
        // 可交互元素的子树不再深入（避免 button 内 svg/span 噪声）
        continue;
      }

      // 结构性容器：有标签且含可交互后代 → 输出上下文行（表格行消歧）
      if (child.matches(STRUCTURAL_SELECTOR)) {
        const hasInteractive = child.querySelector(INTERACTIVE_SELECTOR);
        if (hasInteractive && isVisible(child)) {
          const label = structuralLabel(child);
          if (label) {
            lines.push({ ref: '', el: child, text: `${'  '.repeat(indent)}- ${label}`, dangerous: false });
            this.walk(child, indent + 1, inModal, mode, lines);
            continue;
          }
        }
      }
      // 无交互包装 div：压缩层级，缩进不增加
      this.walk(child, indent, inModal, mode, lines);
    }
  }
}

function tagName(el: Element): string {
  if (el instanceof HTMLInputElement) return `input[${el.type}]`;
  const role = el.getAttribute('role');
  const tag = el.tagName.toLowerCase();
  return role ? `${tag}:${role}` : tag;
}

/** 结构行标签：首个不属于可交互后代/徽标的文本节点（行上下文消歧用，不含按钮文案） */
function structuralLabel(el: Element): string {
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  let node = walker.nextNode();
  while (node) {
    const t = (node.textContent || '').trim();
    const parent = node.parentElement;
    // 跳过可交互元素与徽标（.ant-tag 的"启用/租户内可见"不是上下文标题）
    if (t && parent && !parent.closest(INTERACTIVE_SELECTOR) && !parent.closest('.ant-tag')) {
      return `${el.tagName.toLowerCase()} "${truncate(t)}"`;
    }
    node = walker.nextNode();
  }
  return '';
}

function hashCode(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) {
    h = (Math.imul(h, 31) + s.charCodeAt(i)) | 0;
  }
  return h;
}

export const snapshotEngine = new SnapshotEngine();

/** 真实 DOM 点击事件序列（React/AntD 按真实点击响应；§2.2c） */
export function dispatchRealClick(el: Element, x: number, y: number): void {
  const opts: PointerEventInit = {
    bubbles: true,
    cancelable: true,
    view: window,
    clientX: x,
    clientY: y,
    button: 0,
    isPrimary: true,
  };
  // 测试环境（happy-dom）可能无 PointerEvent，降级 MouseEvent
  const Ptr = (
    typeof PointerEvent !== 'undefined' ? PointerEvent : MouseEvent
  ) as typeof MouseEvent;
  el.dispatchEvent(new Ptr('pointerdown', opts));
  el.dispatchEvent(new MouseEvent('mousedown', opts));
  if (el instanceof HTMLElement) el.focus();
  el.dispatchEvent(new Ptr('pointerup', opts));
  el.dispatchEvent(new MouseEvent('mouseup', opts));
  el.dispatchEvent(new MouseEvent('click', opts));
}
