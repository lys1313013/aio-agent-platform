import { Popover, Segmented, Tooltip } from 'antd';
import {
  BgColorsOutlined,
  CheckOutlined,
  DesktopOutlined,
  MoonOutlined,
  SunOutlined,
} from '@ant-design/icons';
import { useThemeStore } from '@/stores/themeStore';
import { SKINS } from '@/styles/skins';
import { cn } from '@/lib/utils';

export function SkinPickerContent() {
  const { theme, skin, setTheme, setSkin } = useThemeStore();

  return (
    <div>
      <div className="mb-1.5 text-xs font-medium text-muted-foreground">外观模式</div>
      <Segmented
        block
        value={theme}
        onChange={(value) => setTheme(value as 'light' | 'dark' | 'system')}
        options={[
          { value: 'light', icon: <SunOutlined />, label: '亮色' },
          { value: 'dark', icon: <MoonOutlined />, label: '暗色' },
          { value: 'system', icon: <DesktopOutlined />, label: '系统' },
        ]}
      />
      <div className="mb-2 mt-4 text-xs font-medium text-muted-foreground">主题色</div>
      <div className="grid grid-cols-3 gap-2">
        {SKINS.map((s) => {
          const active = s.id === skin;
          return (
            <Tooltip key={s.id} title={s.name}>
              <button
                aria-label={`切换到${s.name}主题`}
                onClick={() => setSkin(s.id)}
                className={cn(
                  'group flex flex-col items-center gap-1.5 rounded-lg px-2 py-2 transition-all',
                  active ? 'bg-muted' : 'hover:bg-muted/60',
                )}
              >
                <span
                  className={cn(
                    'flex h-8 w-8 items-center justify-center rounded-full transition-transform group-hover:scale-110',
                    active && 'ring-2 ring-primary ring-offset-2 ring-offset-card',
                  )}
                  style={{
                    backgroundImage: `linear-gradient(135deg, ${s.swatch[0]}, ${s.swatch[1]})`,
                  }}
                >
                  {active && <CheckOutlined className="text-xs text-white" />}
                </span>
                <span
                  className={cn(
                    'text-xs',
                    active ? 'font-medium text-foreground' : 'text-muted-foreground',
                  )}
                >
                  {s.name}
                </span>
              </button>
            </Tooltip>
          );
        })}
      </div>
    </div>
  );
}

export default function SkinPicker() {
  const content = (
    <div className="w-64">
      <SkinPickerContent />
    </div>
  );

  return (
    <Popover content={content} trigger="click" placement="bottomRight" arrow={false}>
      <button
        aria-label="外观与主题色"
        title="外观与主题色"
        className="flex h-8 w-8 items-center justify-center rounded-lg text-muted-foreground transition hover:bg-muted hover:text-foreground"
      >
        <BgColorsOutlined />
      </button>
    </Popover>
  );
}
