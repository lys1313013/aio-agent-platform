import type { ThemeConfig } from 'antd';

export type SkinId = 'terracotta' | 'olive' | 'amber' | 'crimson' | 'coffee' | 'graphite';

export interface Skin {
  id: SkinId;
  name: string;
  /** 选择器里的色板预览（纯色，hex） */
  swatch: string;
  /** antd token 覆盖 */
  antd: NonNullable<ThemeConfig['token']>;
  /** CSS 变量：HSL 三元组（不含 hsl()） */
  vars: {
    primary: string;
    ring: string;
    brand: string;
  };
}

export const SKINS: Skin[] = [
  {
    id: 'terracotta',
    name: '赭陶',
    swatch: '#c2410c',
    antd: { colorPrimary: '#c2410c', colorInfo: '#c2410c', colorLink: '#c2410c' },
    vars: {
      primary: '21 90% 40%',
      ring: '21 90% 40%',
      brand: '21 90% 40%',
    },
  },
  {
    id: 'olive',
    name: '苔绿',
    swatch: '#3f6212',
    antd: { colorPrimary: '#3f6212', colorInfo: '#3f6212', colorLink: '#3f6212' },
    vars: {
      primary: '86 69% 23%',
      ring: '86 69% 23%',
      brand: '86 69% 23%',
    },
  },
  {
    id: 'amber',
    name: '暖棕',
    swatch: '#b45309',
    antd: { colorPrimary: '#b45309', colorInfo: '#b45309', colorLink: '#b45309' },
    vars: {
      primary: '26 91% 37%',
      ring: '26 91% 37%',
      brand: '26 91% 37%',
    },
  },
  {
    id: 'crimson',
    name: '绯红',
    swatch: '#be123c',
    antd: { colorPrimary: '#be123c', colorInfo: '#be123c', colorLink: '#be123c' },
    vars: {
      primary: '345 83% 41%',
      ring: '345 83% 41%',
      brand: '345 83% 41%',
    },
  },
  {
    id: 'coffee',
    name: '咖啡',
    swatch: '#78350f',
    antd: { colorPrimary: '#78350f', colorInfo: '#78350f', colorLink: '#78350f' },
    vars: {
      primary: '22 78% 26%',
      ring: '22 78% 26%',
      brand: '22 78% 26%',
    },
  },
  {
    id: 'graphite',
    name: '石墨',
    swatch: '#44403c',
    antd: { colorPrimary: '#44403c', colorInfo: '#44403c', colorLink: '#44403c' },
    vars: {
      primary: '31 6% 25%',
      ring: '31 6% 25%',
      brand: '31 6% 25%',
    },
  },
];

export const DEFAULT_SKIN: SkinId = 'terracotta';

export function getSkin(id: SkinId): Skin {
  return SKINS.find((s) => s.id === id) ?? SKINS.find((s) => s.id === DEFAULT_SKIN)!;
}

export function isSkinId(value: unknown): value is SkinId {
  return typeof value === 'string' && SKINS.some((s) => s.id === value);
}
