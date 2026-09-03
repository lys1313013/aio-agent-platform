import { useEffect, useState } from 'react';
import { Outlet, useNavigate } from 'react-router-dom';
import { useAuthStore } from '@/stores/authStore';
import {
  LogoutOutlined,
  SettingOutlined,
  UserOutlined,
  GlobalOutlined,
  BgColorsOutlined,
} from '@ant-design/icons';
import { Dropdown, Avatar, Modal, Select } from 'antd';
import type { MenuProps } from 'antd';
import { settingsApi } from '@/lib/api';
import { SkinPickerContent } from '@/components/SkinPicker';
import BrandLogo from '@/components/BrandLogo';

/**
 * 用户端对话门户布局：仅顶栏（品牌 / 租户 / 用户菜单），无管理端侧边菜单，
 * 不挂载宠物挂件与页内自动化（VirtualCursor / uiActions runner）。
 */
export default function PortalLayout() {
  const { logout, username, tenantName } = useAuthStore();
  const navigate = useNavigate();
  const [skinOpen, setSkinOpen] = useState(false);
  const [tenantOptions, setTenantOptions] = useState<Array<{
    id: string;
    name: string;
    is_active: boolean;
    is_current: boolean;
  }>>([]);

  useEffect(() => {
    settingsApi.listTenants().then(setTenantOptions).catch(() => {});
  }, []);

  const userMenuItems: MenuProps['items'] = [
    {
      key: 'appearance',
      icon: <BgColorsOutlined />,
      label: '外观与主题',
      onClick: () => setSkinOpen(true),
    },
    {
      key: 'settings',
      icon: <SettingOutlined />,
      label: '设置',
      onClick: () => navigate('/settings'),
    },
    { type: 'divider' },
    {
      key: 'logout',
      icon: <LogoutOutlined />,
      label: '退出登录',
      danger: true,
      onClick: logout,
    },
  ];

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-background">
      <header className="flex h-14 flex-shrink-0 items-center justify-between border-b border-border bg-card px-4 sm:px-5">
        <button
          onClick={() => navigate('/portal')}
          className="flex items-center gap-2.5 cursor-pointer"
        >
          <BrandLogo className="h-6 w-6" />
          <h1 className="text-sm font-bold tracking-tight text-foreground">智能体平台</h1>
        </button>

        <div className="flex items-center gap-2">
          {tenantOptions.length > 1 ? (
            <Select
              aria-label="当前租户"
              size="small"
              className="min-w-32 max-w-52"
              value={tenantOptions.find((tenant) => tenant.is_current)?.id}
              options={tenantOptions
                .filter((tenant) => tenant.is_active)
                .map((tenant) => ({ value: tenant.id, label: tenant.name }))}
              onChange={async (tenantId) => {
                await settingsApi.switchTenant(tenantId);
                window.location.reload();
              }}
            />
          ) : tenantName ? (
            <span
              title={tenantName}
              className="hidden sm:flex max-w-52 items-center gap-1.5 rounded-lg px-2 py-1 text-sm text-muted-foreground"
            >
              <GlobalOutlined className="shrink-0 text-primary" />
              <span className="truncate">{tenantName}</span>
            </span>
          ) : null}
          <Dropdown menu={{ items: userMenuItems }} trigger={['click']} placement="bottomRight">
            <button className="flex items-center gap-2 rounded-lg px-2 py-1.5 transition hover:bg-muted">
              <Avatar size={28} icon={<UserOutlined />} className="bg-primary/20 text-primary" />
              <span className="hidden sm:inline text-sm text-foreground">{username || '用户'}</span>
            </button>
          </Dropdown>
        </div>
      </header>

      <main className="flex flex-1 flex-col overflow-hidden bg-background">
        <Outlet />
      </main>

      <Modal
        open={skinOpen}
        onCancel={() => setSkinOpen(false)}
        footer={null}
        title="外观与主题"
        width={340}
      >
        <SkinPickerContent />
      </Modal>
    </div>
  );
}
