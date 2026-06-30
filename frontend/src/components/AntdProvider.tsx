import { ConfigProvider, App, theme } from 'antd';
import { useThemeStore } from '@/stores/themeStore';
import { useMemo } from 'react';

interface Props {
  children: React.ReactNode;
}

export default function AntdProvider({ children }: Props) {
  const { theme: themeMode } = useThemeStore();

  const resolved = themeMode === 'system'
    ? (typeof window !== 'undefined' && window.matchMedia('(prefers-color-scheme: dark)').matches
        ? 'dark'
        : 'light')
    : themeMode;

  const algorithm = useMemo(
    () => (resolved === 'dark' ? theme.darkAlgorithm : theme.defaultAlgorithm),
    [resolved],
  );

  return (
    <ConfigProvider
      theme={{
        algorithm,
        token: {
          colorPrimary: '#3b82f6',
          borderRadius: 8,
          fontFamily:
            '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
        },
      }}
    >
      <App>{children}</App>
    </ConfigProvider>
  );
}
