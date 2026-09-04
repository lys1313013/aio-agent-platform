import { ConfigProvider, App, theme } from 'antd';
import type { ThemeConfig } from 'antd';
import { useThemeStore } from '@/stores/themeStore';
import { getSkin } from '@/styles/skins';
import { useMemo } from 'react';

interface Props {
  children: React.ReactNode;
}

export default function AntdProvider({ children }: Props) {
  const { theme: themeMode, skin } = useThemeStore();

  const resolved = themeMode === 'system'
    ? (typeof window !== 'undefined' && window.matchMedia('(prefers-color-scheme: dark)').matches
        ? 'dark'
        : 'light')
    : themeMode;

  const algorithm = useMemo(
    () => (resolved === 'dark' ? theme.darkAlgorithm : theme.defaultAlgorithm),
    [resolved],
  );

  const skinToken = useMemo(() => getSkin(skin).antd, [skin]);

  // Warm paper palette: align antd surfaces with the warm neutral CSS vars
  const modeToken = useMemo<NonNullable<ThemeConfig['token']>>(
    () =>
      resolved === 'dark'
        ? {
            colorBgLayout: '#1a1614',
            colorBgContainer: '#231e1a',
            colorBgElevated: '#2c2520',
            colorBorder: '#3d352d',
            colorBorderSecondary: '#2e2823',
            colorText: '#efe9e0',
            colorTextSecondary: '#a89e92',
          }
        : {
            colorBgLayout: '#faf9f7',
            colorText: '#292524',
            colorTextSecondary: '#78716c',
            colorBorder: '#e3ddd2',
            colorBorderSecondary: '#ece6db',
          },
    [resolved],
  );

  return (
    <ConfigProvider
      theme={{
        algorithm,
        token: {
          borderRadius: 10,
          fontFamily:
            '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
          ...modeToken,
          ...skinToken,
        },
      }}
    >
      <App>{children}</App>
    </ConfigProvider>
  );
}
