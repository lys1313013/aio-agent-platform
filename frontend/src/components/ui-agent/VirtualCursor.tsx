/** VirtualCursor — 虚拟光标 + 聚光灯遮罩（docs/22-浏览器页面自动化 §3.2）。
 *
 * z-index 规范：遮罩 1001（压过 AntD Modal 1000 但保留挖孔）、光标与气泡 1100
 * （高于 Select/Dropdown 弹层 ~1050）。根节点 id="virtual-cursor-root"，
 * 快照引擎的 MutationObserver 失效检测会排除它（M3）。
 *
 * 光标起点 = 宠物悬浮组件当前屏幕位置（"宠物伸手"的观感）。
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { attachCursor, type CursorApi, type SpotlightTarget } from './cursorController';

const CURSOR_Z = 1100;
const OVERLAY_Z = 1001;
const OVERLAY_COLOR = 'rgba(0, 0, 0, 0.35)';

interface Ripple {
  key: number;
  x: number;
  y: number;
}

/** easeOutCubic */
function ease(t: number): number {
  return 1 - Math.pow(1 - t, 3);
}

/** 移动时长：按距离 clamp(200ms, dist/1.5px·ms, 800ms) */
function moveDuration(dist: number): number {
  return Math.min(800, Math.max(200, dist / 1.5));
}

/** 宠物悬浮组件的屏幕坐标（光标起点）；取不到时用右下角 */
function petPosition(): { x: number; y: number } {
  const pet = document.querySelector('[data-pet-widget]');
  if (pet) {
    const r = pet.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  }
  return { x: window.innerWidth - 120, y: window.innerHeight - 120 };
}

export default function VirtualCursor() {
  const [visible, setVisible] = useState(false);
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null);
  const [spot, setSpot] = useState<SpotlightTarget | null>(null);
  const [ripples, setRipples] = useState<Ripple[]>([]);
  const [typing, setTyping] = useState<string>('');
  const posRef = useRef<{ x: number; y: number } | null>(null);
  const rafRef = useRef<number>(0);
  const typingTimerRef = useRef<ReturnType<typeof setInterval>>(undefined);

  const moveTo = useCallback((x: number, y: number): Promise<void> => {
    const from = posRef.current ?? petPosition();
    const dist = Math.hypot(x - from.x, y - from.y);
    const duration = moveDuration(dist);
    setVisible(true);

    // 后台标签页 rAF 会暂停，挂 setTimeout 兜底，保证 promise 一定 settle
    return new Promise((resolve) => {
      cancelAnimationFrame(rafRef.current);
      const done = () => {
        clearTimeout(fallback);
        posRef.current = { x, y };
        setPos({ x, y });
        resolve();
      };
      const fallback = setTimeout(done, duration + 120);
      const start = performance.now();
      const tick = (now: number) => {
        const t = Math.min(1, (now - start) / duration);
        const e = ease(t);
        const cx = from.x + (x - from.x) * e;
        const cy = from.y + (y - from.y) * e;
        posRef.current = { x: cx, y: cy };
        setPos({ x: cx, y: cy });
        if (t < 1) {
          rafRef.current = requestAnimationFrame(tick);
        } else {
          done();
        }
      };
      rafRef.current = requestAnimationFrame(tick);
    });
  }, []);

  const ripple = useCallback(() => {
    const p = posRef.current;
    if (!p) return;
    const key = Date.now();
    setRipples((prev) => [...prev, { key, x: p.x, y: p.y }]);
    setTimeout(() => {
      setRipples((prev) => prev.filter((r) => r.key !== key));
    }, 600);
  }, []);

  const typeText = useCallback((text: string) => {
    // 视觉层打字机：真实值已由 handler 一次性写入，这里只播动画
    clearInterval(typingTimerRef.current);
    let i = 0;
    setTyping('');
    typingTimerRef.current = setInterval(() => {
      i += 1;
      setTyping(text.slice(0, i));
      if (i >= text.length) clearInterval(typingTimerRef.current);
    }, 40);
  }, []);

  const clear = useCallback(() => {
    setSpot(null);
    setTyping('');
    clearInterval(typingTimerRef.current);
  }, []);

  useEffect(() => {
    const api: CursorApi = {
      moveTo,
      spotlight: setSpot,
      ripple,
      typeText,
      clear,
      setVisible,
      getPos: () => posRef.current,
    };
    const detach = attachCursor(api);
    return () => {
      detach();
      cancelAnimationFrame(rafRef.current);
      clearInterval(typingTimerRef.current);
    };
  }, [moveTo, ripple, typeText, clear]);

  if (!visible && !spot) return null;

  return (
    <div id="virtual-cursor-root" className="pointer-events-none fixed inset-0" style={{ zIndex: CURSOR_Z }}>
      {/* 聚光灯遮罩：四块暗化区域挖孔（z 1001，压在 Modal 之上） */}
      {spot && (
        <>
          <div className="fixed left-0 top-0 w-full" style={{ height: Math.max(0, spot.rect.y), background: OVERLAY_COLOR, zIndex: OVERLAY_Z }} />
          <div className="fixed left-0 w-full" style={{ top: spot.rect.y + spot.rect.height, height: Math.max(0, window.innerHeight - spot.rect.y - spot.rect.height), background: OVERLAY_COLOR, zIndex: OVERLAY_Z }} />
          <div className="fixed" style={{ left: 0, top: spot.rect.y, width: Math.max(0, spot.rect.x), height: spot.rect.height, background: OVERLAY_COLOR, zIndex: OVERLAY_Z }} />
          <div className="fixed" style={{ left: spot.rect.x + spot.rect.width, top: spot.rect.y, width: Math.max(0, window.innerWidth - spot.rect.x - spot.rect.width), height: spot.rect.height, background: OVERLAY_COLOR, zIndex: OVERLAY_Z }} />
          {/* 目标描边 */}
          <div
            className="fixed rounded-md border-2 border-violet-400 shadow-[0_0_12px_rgba(167,139,250,0.7)]"
            style={{
              left: spot.rect.x - 4,
              top: spot.rect.y - 4,
              width: spot.rect.width + 8,
              height: spot.rect.height + 8,
              zIndex: OVERLAY_Z,
            }}
          />
          {/* 气泡说明：元素真实文本 */}
          <div
            className="fixed max-w-xs rounded-lg bg-violet-600 px-2.5 py-1.5 text-xs text-white shadow-lg"
            style={{
              left: Math.min(spot.rect.x, window.innerWidth - 220),
              top: spot.rect.y + spot.rect.height + 10 > window.innerHeight - 60
                ? spot.rect.y - 44
                : spot.rect.y + spot.rect.height + 10,
              zIndex: CURSOR_Z,
            }}
          >
            {spot.label}
          </div>
        </>
      )}

      {/* 打字机气泡 */}
      {typing && (
        <div
          className="fixed rounded-lg bg-card px-2.5 py-1.5 font-mono text-xs text-foreground shadow-lg ring-1 ring-violet-400"
          style={{ left: (posRef.current?.x ?? 0) + 18, top: (posRef.current?.y ?? 0) - 36, zIndex: CURSOR_Z }}
        >
          {typing}
          <span className="animate-pulse">▌</span>
        </div>
      )}

      {/* 点击涟漪 */}
      {ripples.map((r) => (
        <span
          key={r.key}
          className="fixed h-3 w-3 animate-ping rounded-full bg-violet-400"
          style={{ left: r.x - 6, top: r.y - 6, zIndex: CURSOR_Z }}
        />
      ))}

      {/* 光标本体（小爪子，配合宠物主题） */}
      {visible && pos && (
        <div
          className="fixed text-violet-500 drop-shadow-[0_0_6px_rgba(167,139,250,0.9)]"
          style={{ left: pos.x - 10, top: pos.y - 10, zIndex: CURSOR_Z }}
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
            <path d="M12 2c-1.1 0-2 .9-2 2 0 .74.4 1.38 1 1.72V7h2V5.72c.6-.34 1-.98 1-1.72 0-1.1-.9-2-2-2zM7 7C5.9 7 5 7.9 5 9c0 .74.4 1.38 1 1.72V11h2v-.28C8.6 10.38 9 9.74 9 9c0-1.1-.9-2-2-2zm10 0c-1.1 0-2 .9-2 2 0 .74.4 1.38 1 1.72V11h2v-.28c.6-.34 1-.98 1-1.72 0-1.1-.9-2-2-2zm-12 5.5c0 4.14 3.13 7.5 7 7.5s7-3.36 7-7.5c0-1.66-.67-3.16-1.76-4.24l.01-1.26h-10.5l.01 1.26C6.67 9.34 6 10.84 6 12.5h-1z" />
          </svg>
        </div>
      )}
    </div>
  );
}
