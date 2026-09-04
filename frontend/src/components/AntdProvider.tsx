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

  const skinToken = useMemo(() => {
    const s = getSkin(skin);
    return resolved === 'dark' ? { ...s.antd, ...s.antdDark } : s.antd;
  }, [skin, resolved]);

  return (
    <ConfigProvider
      theme={{
        algorithm,
        token: {
          borderRadius: 6,
          fontFamily:
            '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
          ...skinToken,
        },
      }}
    >
      <App>{children}</App>
    </ConfigProvider>
  );
}
