import type { ThemeConfig } from 'antd';

export type SkinId = 'graphite' | 'blue' | 'violet' | 'emerald' | 'amber' | 'rose' | 'cyan';

export interface Skin {
  id: SkinId;
  name: string;
  /** 选择器里的色板预览（纯色，hex） */
  swatch: string;
  /** antd token 覆盖（亮色） */
  antd: NonNullable<ThemeConfig['token']>;
  /** antd token 覆盖（暗色，可选；用于近黑主色在暗色下反白） */
  antdDark?: NonNullable<ThemeConfig['token']>;
  /** CSS 变量：HSL 三元组（不含 hsl()）；brandFrom/brandTo 已收敛为同色（纯色，无渐变） */
  vars: {
    primary: string;
    ring: string;
    brandFrom: string;
    brandTo: string;
    /** RGB 三元组，预留给带透明度的品牌色阴影 */
    brandGlowRgb: string;
  };
}

export const SKINS: Skin[] = [
  {
    id: 'graphite',
    name: '石墨',
    swatch: '#18181b',
    antd: { colorPrimary: '#18181b', colorInfo: '#18181b', colorLink: '#18181b' },
    antdDark: {
      colorPrimary: '#fafafa',
      colorInfo: '#fafafa',
      colorLink: '#e4e4e7',
      colorTextLightSolid: '#18181b',
    },
    vars: {
      primary: '240 5.9% 10%',
      ring: '240 5% 64.9%',
      brandFrom: '240 5.9% 10%',
      brandTo: '240 5.9% 10%',
      brandGlowRgb: '24 24 27',
    },
  },
  {
    id: 'blue',
    name: '湛蓝',
    swatch: '#3b82f6',
    antd: { colorPrimary: '#3b82f6', colorInfo: '#3b82f6', colorLink: '#3b82f6' },
    vars: {
      primary: '217 91% 60%',
      ring: '217 91% 60%',
      brandFrom: '217 91% 60%',
      brandTo: '217 91% 60%',
      brandGlowRgb: '59 130 246',
    },
  },
  {
    id: 'violet',
    name: '绛紫',
    swatch: '#6366f1',
    antd: { colorPrimary: '#6366f1', colorInfo: '#6366f1', colorLink: '#6366f1' },
    vars: {
      primary: '239 84% 67%',
      ring: '239 84% 67%',
      brandFrom: '239 84% 67%',
      brandTo: '239 84% 67%',
      brandGlowRgb: '99 102 241',
    },
  },
  {
    id: 'emerald',
    name: '翡冷',
    swatch: '#10b981',
    antd: { colorPrimary: '#10b981', colorInfo: '#10b981', colorLink: '#0d9488' },
    vars: {
      primary: '160 84% 39%',
      ring: '160 84% 39%',
      brandFrom: '160 84% 39%',
      brandTo: '160 84% 39%',
      brandGlowRgb: '16 185 129',
    },
  },
  {
    id: 'amber',
    name: '暖阳',
    swatch: '#f59e0b',
    antd: { colorPrimary: '#f59e0b', colorInfo: '#f59e0b', colorLink: '#d97706' },
    vars: {
      primary: '38 92% 50%',
      ring: '38 92% 50%',
      brandFrom: '38 92% 50%',
      brandTo: '38 92% 50%',
      brandGlowRgb: '245 158 11',
    },
  },
  {
    id: 'rose',
    name: '绯樱',
    swatch: '#f43f5e',
    antd: { colorPrimary: '#f43f5e', colorInfo: '#f43f5e', colorLink: '#e11d48' },
    vars: {
      primary: '347 77% 50%',
      ring: '347 77% 50%',
      brandFrom: '347 77% 50%',
      brandTo: '347 77% 50%',
      brandGlowRgb: '244 63 94',
    },
  },
  {
    id: 'cyan',
    name: '青霭',
    swatch: '#06b6d4',
    antd: { colorPrimary: '#06b6d4', colorInfo: '#06b6d4', colorLink: '#0284c7' },
    vars: {
      primary: '188 94% 43%',
      ring: '188 94% 43%',
      brandFrom: '188 94% 43%',
      brandTo: '188 94% 43%',
      brandGlowRgb: '6 182 212',
    },
  },
];

export const DEFAULT_SKIN: SkinId = 'graphite';

export function getSkin(id: SkinId): Skin {
  return SKINS.find((s) => s.id === id) ?? SKINS.find((s) => s.id === DEFAULT_SKIN)!;
}

export function isSkinId(value: unknown): value is SkinId {
  return typeof value === 'string' && SKINS.some((s) => s.id === value);
}
