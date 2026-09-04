import { ConfigProvider, App, theme } from 'antd';
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

  // 与 globals.css 的 CSS 变量保持一致的中性表面色（GitHub Dark / Light）
  const neutralToken = useMemo(
    () =>
      resolved === 'dark'
        ? {
            colorBgLayout: '#0d1117',
            colorBgContainer: '#161b22',
            colorBgElevated: '#21262d',
            colorBorder: '#30363d',
            colorBorderSecondary: '#21262d',
            colorText: '#e6edf3',
            colorTextSecondary: '#7d8590',
          }
        : {
            colorBgLayout: '#f6f8fa',
            colorBgContainer: '#ffffff',
            colorBgElevated: '#ffffff',
            colorBorder: '#d1d9e0',
            colorBorderSecondary: '#eaeef2',
            colorText: '#1f2328',
            colorTextSecondary: '#59636e',
          },
    [resolved],
  );

  return (
    <ConfigProvider
      theme={{
        algorithm,
        token: {
          borderRadius: 6,
          fontFamily:
            '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
          ...neutralToken,
          ...skinToken,
        },
      }}
    >
      <App>{children}</App>
    </ConfigProvider>
  );
}
