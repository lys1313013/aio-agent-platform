import { useEffect, useRef } from 'react';
import { petsApi } from '@/lib/api';
import type { PetMood, PetPackage } from '@/lib/types';

const FPS = 8;

interface PetCanvasProps {
  pkg: PetPackage;
  mood: PetMood;
  size?: number;
  className?: string;
  /** 强制渲染指定行（用于上传/映射编辑时的行预览），优先级高于 mood */
  fixedRow?: number;
}

/** 按 row_mapping 把心情映射到精灵图行，缺行降级 idle */
function resolveRow(pkg: PetPackage, mood: PetMood): number {
  const mapping = pkg.row_mapping;
  return mapping[mood] ?? mapping.idle ?? 0;
}

/**
 * 精灵图行动画渲染器：Codex 格式（每行一个动画、每列一帧、RGBA 透明背景）。
 * 用 <img> 离屏加载 + canvas 逐帧裁剪绘制；页面不可见时自动暂停。
 */
export default function PetCanvas({ pkg, mood, size = 96, className, fixedRow }: PetCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const stateRef = useRef({ pkg, mood, fixedRow });

  stateRef.current = { pkg, mood, fixedRow };

  // 精灵图接口需要鉴权，<img src> 无法带 Bearer token，改用 blob URL
  useEffect(() => {
    let url: string | null = null;
    let cancelled = false;
    const img = new Image();
    petsApi
      .spritesheetBlob(pkg.id)
      .then((blob) => {
        if (cancelled) return;
        url = URL.createObjectURL(blob);
        img.src = url;
        img.onload = () => {
          imgRef.current = img;
        };
      })
      .catch(() => {
        // 精灵图加载失败 → 保持空白，不影响页面
      });
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
      imgRef.current = null;
    };
  }, [pkg.id]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    canvas.width = size * dpr;
    canvas.height = size * dpr;

    let raf = 0;
    let last = 0;
    let frame = 0;

    const draw = (ts: number) => {
      raf = requestAnimationFrame(draw);
      if (document.hidden) return;
      if (ts - last < 1000 / FPS) return;
      last = ts;

      const { pkg: p, mood: m, fixedRow: fr } = stateRef.current;
      const img = imgRef.current;
      if (!img) return;

      const row = fr ?? resolveRow(p, m);
      const rowFrames = p.row_mapping._row_frames?.[row] ?? p.col_count;
      const fw = p.frame_width;
      const fh = p.frame_height;
      frame = (frame + 1) % Math.max(rowFrames, 1);

      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.imageSmoothingEnabled = false;
      // 保持帧宽高比，居中绘制
      const scale = Math.min(canvas.width / fw, canvas.height / fh);
      const dw = fw * scale;
      const dh = fh * scale;
      ctx.drawImage(
        img,
        frame * fw,
        row * fh,
        fw,
        fh,
        (canvas.width - dw) / 2,
        (canvas.height - dh) / 2,
        dw,
        dh,
      );
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [size]);

  return (
    <canvas
      ref={canvasRef}
      className={className}
      style={{ width: size, height: size }}
      aria-label={`pet-${mood}`}
    />
  );
}
