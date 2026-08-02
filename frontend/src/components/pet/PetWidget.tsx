import { Component, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { Dropdown } from 'antd';
import { petsApi } from '@/lib/api';
import type { UserPet } from '@/lib/types';
import { rowName, usePetStore } from '@/stores/petStore';
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
  '摸摸头，+1 经验',
  '看我看我，我在工作哦',
  '汪！嗷呜——',
];

function pickBubble(): string {
  return BUBBLES[Math.floor(Math.random() * BUBBLES.length)];
}

interface Pos {
  x: number;
  y: number;
}

function loadPos(): Pos | null {
  try {
    const raw = localStorage.getItem(POS_KEY);
    return raw ? (JSON.parse(raw) as Pos) : null;
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
  const { activePet, enabled, mood, actionRow, size, loaded, loadActive, setEnabled, playRow } = usePetStore();
  const [pos, setPos] = useState<Pos | null>(loadPos);
  const [collapsed, setCollapsed] = useState(false);
  const [bubble, setBubble] = useState<string | null>(null);
  const [myPets, setMyPets] = useState<UserPet[]>([]);
  const bubbleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const dragRef = useRef<{ startX: number; startY: number; baseX: number; baseY: number; moved: boolean } | null>(null);
  const sleepTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 右键「切换宠物」子菜单数据
  useEffect(() => {
    petsApi.myPets().then(setMyPets).catch(() => {});
  }, []);

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
    const dim = collapsed ? COLLAPSED_SIZE : size;
    const x = Math.min(Math.max(drag.baseX + dx, 0), window.innerWidth - dim);
    const y = Math.min(Math.max(drag.baseY + dy, 0), window.innerHeight - dim);
    setPos({ x, y });
  }, [collapsed, size]);

  const onPointerUp = useCallback(() => {
    const drag = dragRef.current;
    dragRef.current = null;
    if (!drag) return;
    if (drag.moved) {
      setPos((p) => {
        if (p) localStorage.setItem(POS_KEY, JSON.stringify(p));
        return p;
      });
    } else {
      // 左键单击默认播放「开心」行（无映射则行 0）
      if (activePet) playRowX(activePet.package.row_mapping.happy ?? 0);
    }
  }, [playRowX, activePet]);

  // 菜单尽量朝屏幕边缘弹出，避免盖住聊天内容
  // 注意：必须在任何条件 return 之前调用（React Hooks 规则）
  const placement = useMemo(() => {
    const px = pos ? pos.x : window.innerWidth - size - 24;
    const py = pos ? pos.y : window.innerHeight - size - 24;
    const onRight = px + size / 2 > window.innerWidth / 2;
    const onBottom = py + size / 2 > window.innerHeight / 2;
    if (onRight && onBottom) return 'topLeft';
    if (onRight && !onBottom) return 'bottomLeft';
    if (!onRight && onBottom) return 'topRight';
    return 'bottomRight';
  }, [pos, size]);

  if (!enabled || !activePet) return null;

  const style: React.CSSProperties = pos
    ? { left: pos.x, top: pos.y }
    : { right: 24, bottom: 24 };

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
    { type: 'divider' as const },
    { key: 'collapse', label: collapsed ? '展开宠物' : '收起宠物' },
    { key: 'toggle', label: enabled ? '隐藏宠物' : '显示宠物' },
  ];

  const onMenuClick = ({ key }: { key: string }) => {
    if (key.startsWith('play-')) {
      const row = Number(key.slice('play-'.length));
      if (!Number.isNaN(row)) playRowX(row);
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
      <div key={mood === 'happy' ? 'bounce' : 'still'} className={mood === 'happy' ? 'pet-bounce' : ''}>
        <PetCanvas pkg={activePet.package} mood={mood} size={size} fixedRow={actionRow ?? undefined} />
      </div>
    </div>
  );

  return (
    <div
      className="fixed z-50 select-none"
      style={style}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
    >
      <Dropdown
        trigger={['contextMenu']}
        menu={{ items: menuItems, onClick: onMenuClick }}
        placement={placement}
      >
        <div className="inline-block">{petBody}</div>
      </Dropdown>
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
