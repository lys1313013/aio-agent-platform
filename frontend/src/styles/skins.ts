import type { ThemeConfig } from 'antd';

export type SkinId = 'blue' | 'emerald' | 'violet' | 'amber' | 'rose' | 'cyan';

export interface Skin {
  id: SkinId;
  name: string;
  /** 选择器里的色板预览（纯色，hex） */
  swatch: string;
  /** antd token 覆盖 */
  antd: NonNullable<ThemeConfig['token']>;
}

export const SKINS: Skin[] = [
  {
    id: 'blue',
    name: '湛蓝',
    swatch: '#2f81f7',
    antd: { colorPrimary: '#2f81f7', colorInfo: '#2f81f7', colorLink: '#2f81f7' },
  },
  {
    id: 'emerald',
    name: '翠绿',
    swatch: '#3fb950',
    antd: { colorPrimary: '#3fb950', colorInfo: '#3fb950', colorLink: '#3fb950' },
  },
  {
    id: 'violet',
    name: '绛紫',
    swatch: '#a371f7',
    antd: { colorPrimary: '#a371f7', colorInfo: '#a371f7', colorLink: '#a371f7' },
  },
  {
    id: 'amber',
    name: '暖阳',
    swatch: '#d29922',
    antd: { colorPrimary: '#d29922', colorInfo: '#d29922', colorLink: '#d29922' },
  },
  {
    id: 'rose',
    name: '绯红',
    swatch: '#f85149',
    antd: { colorPrimary: '#f85149', colorInfo: '#f85149', colorLink: '#f85149' },
  },
  {
    id: 'cyan',
    name: '青霭',
    swatch: '#39c5cf',
    antd: { colorPrimary: '#39c5cf', colorInfo: '#39c5cf', colorLink: '#39c5cf' },
  },
];

export const DEFAULT_SKIN: SkinId = 'blue';

export function getSkin(id: SkinId): Skin {
  return SKINS.find((s) => s.id === id) ?? SKINS.find((s) => s.id === DEFAULT_SKIN)!;
}

export function isSkinId(value: unknown): value is SkinId {
  return typeof value === 'string' && SKINS.some((s) => s.id === value);
}
