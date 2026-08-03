import { Component, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { Dropdown, Slider } from 'antd';
import { petsApi } from '@/lib/api';
import type { UserPet } from '@/lib/types';
import { PET_SIZE_MAX, PET_SIZE_MIN, PET_SIZE_STEP, rowName, usePetStore } from '@/stores/petStore';
import { useChatStore } from '@/stores/chatStore';
import PetCanvas from './PetCanvas';

const POS_KEY = 'pet-widget-pos';
const SLEEP_AFTER_MS = 30 * 60 * 1000;
const BUBBLE_MS = 1800;
const COLLAPSED_SIZE = 28;

/** 互动播放的随机气泡（本地库，不走 LLM，零 token） */
const BUBBLES = [
  '主人，戳我干嘛~',
  '嘿嘿，有点痒！',
  '(*^▽^*)',
  '今天也要加油鸭~',
  '(^・ω・^ )',
  '摸摸头，好舒服~',
  '看我看我，我在工作哦',
  '汪！嗷呜——',
];

function pickBubble(): string {
  return BUBBLES[Math.floor(Math.random() * BUBBLES.length)];
}

/** 渠道来源标识（任务条前缀） */
const SOURCE_LABELS: Record<string, string> = { feishu: '飞书', dingtalk: '钉钉', wecom: '企微' };

interface Pos {
  x: number;
  y: number;
}

/** 锚定位置：记距最近两条边的距离，窗口缩放后相对位置不变 */
interface AnchorPos {
  h: 'left' | 'right';
  v: 'top' | 'bottom';
  dx: number;
  dy: number;
}

function fromAbsolute(x: number, y: number, dim: number): AnchorPos {
  const rightDist = window.innerWidth - dim - x;
  const bottomDist = window.innerHeight - dim - y;
  return {
    h: x <= rightDist ? 'left' : 'right',
    v: y <= bottomDist ? 'top' : 'bottom',
    dx: Math.max(Math.min(x, rightDist), 0),
    dy: Math.max(Math.min(y, bottomDist), 0),
  };
}

function toAbsolute(p: AnchorPos, dim: number): Pos {
  return {
    x: p.h === 'left' ? p.dx : Math.max(window.innerWidth - dim - p.dx, 0),
    y: p.v === 'top' ? p.dy : Math.max(window.innerHeight - dim - p.dy, 0),
  };
}

/** 从未拖拽过的默认位置：右下 24px */
function defaultPos(dim: number): Pos {
  return {
    x: Math.max(window.innerWidth - dim - 24, 0),
    y: Math.max(window.innerHeight - dim - 24, 0),
  };
}

function loadPos(): AnchorPos | null {
  try {
    const raw = localStorage.getItem(POS_KEY);
    if (!raw) return null;
    const p = JSON.parse(raw) as Partial<AnchorPos> & Partial<Pos>;
    if ((p.h === 'left' || p.h === 'right') && typeof p.dx === 'number') return p as AnchorPos;
    // 旧格式 {x, y}：按当前窗口换算为锚定坐标
    if (typeof p.x === 'number' && typeof p.y === 'number') return fromAbsolute(p.x, p.y, 96);
    return null;
  } catch {
    return null;
  }
}

/** 局部降级：宠物组件崩溃只隐藏宠物，不影响页面 */
class PetErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    return this.state.failed ? null : this.props.children;
  }
}

function PetWidgetInner() {
  const { activePet, enabled, mood, actionRow, actionFps, size, loaded, tasks, loadActive, setEnabled, setSize, playRow } = usePetStore();
  const navigate = useNavigate();

  // 点击任务条进入对应会话：有 agentId 走 Agent 会话路由，否则通用聊天页 + 激活会话
  const openSession = useCallback(
    (sessionId: string, agentId?: string) => {
      if (agentId) {
        navigate(`/agents/${agentId}/chat/${sessionId}`);
      } else {
        useChatStore.getState().setActiveSession(sessionId);
        navigate('/chat');
      }
    },
    [navigate],
  );
  const [anchor, setAnchor] = useState<AnchorPos | null>(loadPos);
  const [collapsed, setCollapsed] = useState(false);
  const [bubble, setBubble] = useState<string | null>(null);
  const [tasksExpanded, setTasksExpanded] = useState(false);
  const [myPets, setMyPets] = useState<UserPet[]>([]);
  const bubbleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const bounceRef = useRef<HTMLDivElement | null>(null);

  // 进入 happy 状态时重触发 bounce 动画（不能用 key 重挂载，会销毁 canvas 并重新拉取精灵图）
  useEffect(() => {
    if (mood !== 'happy') return;
    const el = bounceRef.current;
    if (!el) return;
    el.classList.remove('pet-bounce');
    void el.offsetWidth;
    el.classList.add('pet-bounce');
  }, [mood]);
  const dragRef = useRef<{
    startX: number;
    startY: number;
    baseX: number;
    baseY: number;
    moved: boolean;
    lastX?: number;
    lastY?: number;
  } | null>(null);
  const sleepTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);

  const dim = collapsed ? COLLAPSED_SIZE : size;
  const pos: Pos = anchor ? toAbsolute(anchor, dim) : defaultPos(dim);

  // 锚定坐标不变、窗口尺寸在变：resize 时用 rAF 节流直接改 DOM transform，
  // 不走 React 重渲染（高频 resize 事件下重渲染会挤掉 canvas 的 rAF 帧，表现为闪烁）
  const anchorRef = useRef(anchor);
  anchorRef.current = anchor;
  const dimRef = useRef(dim);
  dimRef.current = dim;
  useEffect(() => {
    let raf = 0;
    const onResize = () => {
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        const el = containerRef.current;
        if (!el) return;
        const a = anchorRef.current;
        const p = a ? toAbsolute(a, dimRef.current) : defaultPos(dimRef.current);
        el.style.transform = `translate3d(${p.x}px, ${p.y}px, 0)`;
      });
    };
    window.addEventListener('resize', onResize);
    return () => {
      window.removeEventListener('resize', onResize);
      if (raf) cancelAnimationFrame(raf);
    };
  }, []);

  // 锚定位置持久化
  useEffect(() => {
    if (anchor) localStorage.setItem(POS_KEY, JSON.stringify(anchor));
  }, [anchor]);

  // 右键「切换宠物」子菜单数据
  useEffect(() => {
    petsApi.myPets().then(setMyPets).catch(() => {});
  }, []);

  // 渠道（飞书等）触发的在跑任务：SSE 实时推送（连接即快照 + 增量事件），断开 3s 后重连
  useEffect(() => {
    if (!enabled || !activePet) return;
    let stop = false;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let close: (() => void) | null = null;
    const connect = () => {
      if (stop) return;
      close = petsApi.watchActiveTasks((ev) => {
        if (stop) return;
        if (ev.type === 'error' || ev.type === 'closed') {
          retryTimer = setTimeout(connect, 3000);
          return;
        }
        usePetStore.getState().applyRemoteTaskEvent(ev);
      });
    };
    connect();
    return () => {
      stop = true;
      if (retryTimer) clearTimeout(retryTimer);
      close?.();
    };
  }, [enabled, activePet]);

  const showBubble = useCallback((text: string) => {
    if (bubbleTimer.current) clearTimeout(bubbleTimer.current);
    setBubble(text);
    bubbleTimer.current = setTimeout(() => setBubble(null), BUBBLE_MS);
  }, []);

  // 播放指定精灵图行（互动菜单 / 左键点击）
  const playRowX = useCallback(
    (row: number) => {
      void playRow(row);
      showBubble(pickBubble());
    },
    [playRow, showBubble],
  );

  useEffect(() => {
    if (!loaded) void loadActive();
  }, [loaded, loadActive]);

  // 30 分钟无交互 → 睡觉
  useEffect(() => {
    const setMood = usePetStore.setState;
    const reset = () => {
      if (sleepTimer.current) clearTimeout(sleepTimer.current);
      sleepTimer.current = setTimeout(() => {
        const s = usePetStore.getState();
        if (s.mood === 'idle') setMood({ mood: 'sleep' });
      }, SLEEP_AFTER_MS);
      const s = usePetStore.getState();
      if (s.mood === 'sleep') setMood({ mood: 'idle' });
    };
    reset();
    window.addEventListener('pointerdown', reset);
    window.addEventListener('keydown', reset);
    return () => {
      if (sleepTimer.current) clearTimeout(sleepTimer.current);
      window.removeEventListener('pointerdown', reset);
      window.removeEventListener('keydown', reset);
    };
  }, []);

  const onPointerDown = useCallback((e: React.PointerEvent) => {
    // 仅左键拖拽，右键交给上下文菜单
    if (e.button !== 0) return;
    const el = e.currentTarget as HTMLElement;
    // Dropdown 菜单渲染在 portal 里，但 React 事件会沿组件树冒泡回来——
    // 菜单项的 pointerdown 不能触发 setPointerCapture，否则真实 click 被重定向到宠物上
    if (!el.contains(e.target as Node)) return;
    const rect = el.getBoundingClientRect();
    dragRef.current = {
      startX: e.clientX,
      startY: e.clientY,
      baseX: rect.left,
      baseY: rect.top,
      moved: false,
    };
    el.setPointerCapture(e.pointerId);
  }, []);

  const onPointerMove = useCallback((e: React.PointerEvent) => {
    const drag = dragRef.current;
    if (!drag) return;
    const dx = e.clientX - drag.startX;
    const dy = e.clientY - drag.startY;
    if (!drag.moved && Math.hypot(dx, dy) < 4) return;
    drag.moved = true;
    const x = Math.min(Math.max(drag.baseX + dx, 0), window.innerWidth - dim);
    const y = Math.min(Math.max(drag.baseY + dy, 0), window.innerHeight - dim);
    drag.lastX = x;
    drag.lastY = y;
    // 拖拽中直接改 DOM，不走 React 状态：pointermove 频率高，
    // 每次 setState 重渲染整棵 Dropdown 树会挤掉 canvas 的 rAF 帧，表现为闪烁
    const el = containerRef.current;
    if (el) el.style.transform = `translate3d(${x}px, ${y}px, 0)`;
  }, [dim]);

  const onPointerUp = useCallback(() => {
    const drag = dragRef.current;
    dragRef.current = null;
    if (!drag) return;
    if (drag.moved) {
      // 松手才提交状态（触发持久化）；React 重渲染后的 style 与拖拽中的 DOM 值一致，无跳变
      setAnchor(fromAbsolute(drag.lastX ?? drag.baseX, drag.lastY ?? drag.baseY, dim));
    } else if (activePet) {
      // 左键单击默认播放「开心」行（无映射则行 0）
      playRowX(activePet.package.row_mapping.happy ?? 0);
    }
  }, [playRowX, activePet, dim]);

  // 记录右键时的光标位置 + 角色可见内容的包围盒：contextMenu 触发下 antd 强制
  // alignPoint（菜单锚定光标而非宠物），且画布四周有透明边——按内容边缘算 offset 菜单才贴得近
  const [ctxPos, setCtxPos] = useState<{
    x: number;
    y: number;
    box: { left: number; right: number; top: number; bottom: number } | null;
  } | null>(null);

  const onContextMenu = useCallback((e: React.MouseEvent) => {
    let box = null;
    const canvas = containerRef.current?.querySelector('canvas');
    if (canvas) {
      const r = canvas.getBoundingClientRect();
      const ctx = canvas.getContext('2d');
      if (ctx && r.width > 0) {
        const dpr = canvas.width / r.width;
        const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
        let minX = canvas.width, maxX = -1, minY = canvas.height, maxY = -1;
        for (let y = 0; y < canvas.height; y++) {
          for (let x = 0; x < canvas.width; x++) {
            if (data[(y * canvas.width + x) * 4 + 3] > 10) {
              if (x < minX) minX = x;
              if (x > maxX) maxX = x;
              if (y < minY) minY = y;
              if (y > maxY) maxY = y;
            }
          }
        }
        if (maxX >= 0) {
          box = {
            left: r.left + minX / dpr,
            right: r.left + (maxX + 1) / dpr,
            top: r.top + minY / dpr,
            bottom: r.top + (maxY + 1) / dpr,
          };
        }
      }
    }
    setCtxPos({ x: e.clientX, y: e.clientY, box });
  }, []);

  // 上下弹出兜底时的朝向：尽量朝屏幕边缘弹出，避免盖住聊天内容
  // 注意：必须在任何条件 return 之前调用（React Hooks 规则）
  const placement = useMemo(() => {
    const px = pos.x;
    const py = pos.y;
    const onRight = px + size / 2 > window.innerWidth / 2;
    const onBottom = py + size / 2 > window.innerHeight / 2;
    if (onRight && onBottom) return 'topLeft';
    if (onRight && !onBottom) return 'bottomLeft';
    if (!onRight && onBottom) return 'topRight';
    return 'bottomRight';
  }, [pos, size]);

  // 菜单位置策略：优先放宠物右边 → 右边没位置放左边 → 两边都没位置放上面/下面。
  const dropdownAlign = useMemo(() => {
    const GAP = 15;
    const SIDE_W = 260; // 主菜单 + 子菜单展开所需宽度（实测约 230，留余量）
    // 优先用角色可见内容的包围盒（剔除精灵图透明边），没有才退化到画布矩形
    const canvasLeft = pos.x;
    const canvasTop = pos.y;
    const petLeft = ctxPos?.box?.left ?? canvasLeft;
    const petTop = ctxPos?.box?.top ?? canvasTop;
    const petRight = ctxPos?.box?.right ?? canvasLeft + dim;
    const petBottom = ctxPos?.box?.bottom ?? canvasTop + dim;

    if (ctxPos) {
      const oy = petTop - ctxPos.y; // 菜单顶边对齐角色顶边（正 Y 向下，实测约定）
      if (window.innerWidth - petRight - GAP >= SIDE_W) {
        // 右边：菜单左边 = 角色右边 + GAP
        return {
          points: ['tl', 'tr'] as ('tl' | 'tr')[],
          offset: [petRight + GAP - ctxPos.x, oy] as [number, number],
        };
      }
      if (petLeft - GAP >= SIDE_W) {
        // 左边：菜单右边 = 角色左边 - GAP
        return {
          points: ['tr', 'tl'] as ('tr' | 'tl')[],
          offset: [petLeft - GAP - ctxPos.x, oy] as [number, number],
        };
      }
    }

    // 兜底：上/下弹出，垂直方向避开宠物
    const oy = ctxPos
      ? placement.startsWith('top')
        ? petTop - GAP - ctxPos.y
        : petBottom + GAP - ctxPos.y
      : 0;
    switch (placement) {
      case 'topLeft':
        return { points: ['bl', 'tl'] as ('bl' | 'tl')[], offset: [0, oy] as [number, number] };
      case 'topRight':
        return { points: ['br', 'tr'] as ('br' | 'tr')[], offset: [0, oy] as [number, number] };
      case 'bottomLeft':
        return { points: ['tl', 'bl'] as ('tl' | 'bl')[], offset: [0, oy] as [number, number] };
      default:
        return { points: ['tr', 'br'] as ('tr' | 'br')[], offset: [0, oy] as [number, number] };
    }
  }, [ctxPos, placement, pos, dim]);

  if (!enabled || !activePet) return null;

  // 任务条：最多平铺 2 条，超出折叠为「还有 N 个」可展开；展开后超高滚动
  const taskList = Object.entries(tasks).sort((a, b) => {
    const da = a[1].status === 'done' ? 1 : 0;
    const db = b[1].status === 'done' ? 1 : 0;
    return da - db || b[1].updatedAt - a[1].updatedAt;
  });
  // 渠道任务在跑时宠物进入工作态（渠道任务没有本地 SSE 事件驱动 mood）
  const effectiveMood =
    (mood === 'idle' || mood === 'sleep') && taskList.some(([, t]) => t.remote && t.status !== 'done')
      ? 'work'
      : mood;
  const collapsedCount = tasksExpanded ? taskList.length : Math.min(taskList.length, 2);
  const visibleRows = Math.min(collapsedCount, 5) + (taskList.length > 2 ? 1 : 0);
  // 位置：默认挂宠物下方；下方没空间时挂上方（气泡也在上方时错开一格）
  const taskPillBelow = pos.y + dim + visibleRows * 24 + 8 <= window.innerHeight;

  // 统一 transform 定位：移动走合成层，不触发布局/重绘（拖拽和 resize 都直接改这个值）
  const style: React.CSSProperties = {
    left: 0,
    top: 0,
    transform: `translate3d(${pos.x}px, ${pos.y}px, 0)`,
  };

  const taskPills = taskList.length > 0 ? (
    <div
      className="absolute left-1/2 z-10 flex -translate-x-1/2 flex-col items-center gap-1"
      style={taskPillBelow ? { top: dim + 6 } : { bottom: dim + (bubble ? 40 : 6) }}
    >
      <div
        className={`flex flex-col items-center gap-1 ${tasksExpanded ? 'overflow-y-auto' : ''}`}
        style={tasksExpanded ? { maxHeight: 5 * 24 } : undefined}
      >
        {(tasksExpanded ? taskList : taskList.slice(0, 2)).map(([sid, t]) => (
          <button
            key={sid}
            type="button"
            title="点击进入会话"
            className={`pet-task-pill flex max-w-[220px] cursor-pointer items-center gap-1.5 whitespace-nowrap rounded-full border border-border bg-card px-2.5 py-0.5 text-[11px] text-muted-foreground shadow-md hover:text-foreground ${t.status === 'done' ? 'opacity-75' : ''}`}
            onPointerDown={(e) => e.stopPropagation()}
            onClick={() => openSession(sid, t.agentId)}
          >
            {t.status === 'done' ? (
              <span className="shrink-0 text-[11px] font-bold leading-none" style={{ color: '#22c55e' }}>
                ✓
              </span>
            ) : (
              <span className="pet-task-dot inline-block h-1.5 w-1.5 shrink-0 rounded-full bg-primary" />
            )}
            <span className="overflow-hidden text-ellipsis">
              {t.source ? `${SOURCE_LABELS[t.source] ?? t.source} · ` : ''}
              {t.label ?? '处理中'}
              {t.tool ? ` · ${t.tool}` : ''}
            </span>
          </button>
        ))}
      </div>
      {taskList.length > 2 && (
        <button
          type="button"
          className="pet-task-pill rounded-full border border-border bg-card px-2.5 py-0.5 text-[11px] text-muted-foreground shadow-md hover:text-foreground"
          onPointerDown={(e) => e.stopPropagation()}
          onClick={() => setTasksExpanded((v) => !v)}
        >
          {tasksExpanded ? '收起' : `还有 ${taskList.length - 2} 个任务…`}
        </button>
      )}
    </div>
  ) : null;

  const menuItems = [
    {
      key: 'play',
      label: '播放动画',
      children: Array.from({ length: activePet.package.row_count }, (_, row) => ({
        key: `play-${row}`,
        label: `${rowName(activePet.package, row)}（行${row}）`,
      })),
    },
    {
      key: 'switch',
      label: '切换宠物',
      children:
        myPets.length > 0
          ? myPets.map((p) => ({
              key: `switch-${p.id}`,
              label: `${p.package.display_name}${p.is_active ? '（当前）' : ''}`,
            }))
          : [{ key: 'switch-empty', label: '暂无其他宠物', disabled: true }],
    },
    {
      key: 'size',
      label: (
        <div
          className="flex w-[190px] items-center gap-2"
          onClick={(e) => e.stopPropagation()}
          onMouseDown={(e) => e.stopPropagation()}
        >
          <span className="shrink-0 text-xs text-muted-foreground">大小</span>
          <Slider
            min={PET_SIZE_MIN}
            max={PET_SIZE_MAX}
            step={PET_SIZE_STEP}
            value={size}
            onChange={setSize}
            style={{ flex: 1 }}
            tooltip={{ formatter: (v) => `${v}px` }}
          />
          <span className="w-9 shrink-0 text-right text-xs tabular-nums text-muted-foreground">
            {size}px
          </span>
        </div>
      ),
    },
    { type: 'divider' as const },
    { key: 'collapse', label: collapsed ? '展开宠物' : '收起宠物' },
    { key: 'toggle', label: enabled ? '隐藏宠物' : '显示宠物' },
  ];

  const onMenuClick = ({ key }: { key: string }) => {
    if (key.startsWith('play-')) {
      const row = Number(key.slice('play-'.length));
      // 菜单手动点播：播久一点（5s）、慢一点（4 FPS），看得清动作
      if (!Number.isNaN(row)) {
        void playRow(row, { durationMs: 5000, fps: 4 });
        showBubble(pickBubble());
      }
      return;
    }
    if (key.startsWith('switch-')) {
      const id = key.slice('switch-'.length);
      void (async () => {
        try {
          await petsApi.activate(id);
          await loadActive();
          setMyPets((prev) =>
            prev.map((p) => ({ ...p, is_active: p.id === id })),
          );
        } catch {
          /* 切换失败忽略 */
        }
      })();
      return;
    }
    switch (key) {
      case 'collapse':
        setCollapsed((c) => !c);
        break;
      case 'toggle':
        setEnabled(!enabled);
        break;
    }
  };

  const petBody = collapsed ? (
    <div
      className="flex h-8 w-8 items-center justify-center overflow-hidden rounded-full border border-border bg-card shadow-md cursor-pointer"
      title="展开宠物（右键更多操作）"
    >
      <PetCanvas pkg={activePet.package} mood="idle" size={COLLAPSED_SIZE} />
    </div>
  ) : (
    <div className="relative cursor-grab active:cursor-grabbing">
      {bubble && (
        <div
          className="pet-bubble absolute left-1/2 z-10 -translate-x-1/2 whitespace-nowrap rounded-full bg-card px-3 py-1 text-xs shadow-lg border border-border text-foreground"
          style={{ top: -34 }}
        >
          {bubble}
        </div>
      )}
      <div ref={bounceRef}>
        <PetCanvas pkg={activePet.package} mood={effectiveMood} size={size} fixedRow={actionRow ?? undefined} fps={actionFps ?? undefined} />
      </div>
    </div>
  );

  return (
    <div
      ref={containerRef}
      className="fixed z-50 select-none"
      style={style}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onContextMenu={onContextMenu}
    >
      <Dropdown
        trigger={['contextMenu']}
        menu={{ items: menuItems, onClick: onMenuClick }}
        placement={placement}
        align={dropdownAlign}
      >
        <div className="inline-block">{petBody}</div>
      </Dropdown>
      {taskPills}
    </div>
  );
}

export default function PetWidget() {
  return (
    <PetErrorBoundary>
      <PetWidgetInner />
    </PetErrorBoundary>
  );
}
