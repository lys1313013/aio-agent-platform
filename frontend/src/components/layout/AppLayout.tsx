import { useEffect, useState } from 'react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useAuthStore } from '@/stores/authStore';
import {
  LogoutOutlined,
  RobotOutlined,
  DashboardOutlined,
  BulbOutlined,
  ThunderboltOutlined,
  SettingOutlined,
  UserOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  ApiOutlined,
  CloudServerOutlined,
  DatabaseOutlined,
  GlobalOutlined,
  IdcardOutlined,
  ClockCircleOutlined,
  UsergroupAddOutlined,
  TeamOutlined,
  FolderOpenOutlined,
  MessageOutlined,
} from '@ant-design/icons';
import { Dropdown, Avatar, Select } from 'antd';
import type { MenuProps } from 'antd';
import { cn } from '@/lib/utils';
import { settingsApi } from '@/lib/api';
import SkinPicker from '@/components/SkinPicker';

export default function AppLayout() {
  const { logout, role, username } = useAuthStore();
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [tenantOptions, setTenantOptions] = useState<Array<{
    id: string;
    name: string;
    is_active: boolean;
    is_current: boolean;
  }>>([]);
  const location = useLocation();
  const navigate = useNavigate();

  const isAdmin = role === 'admin' || role === 'superadmin';
  const isSuperAdmin = role === 'superadmin';

  useEffect(() => {
    settingsApi.listTenants().then(setTenantOptions).catch(() => {});
  }, []);

  const navItems = [
    { path: '/dashboard', icon: <DashboardOutlined />, label: '仪表盘' },
    { path: '/agents', icon: <RobotOutlined />, label: '智能体' },
    { path: '/memory', icon: <BulbOutlined />, label: '记忆' },
    { path: '/skills', icon: <ThunderboltOutlined />, label: '技能' },
    { path: '/workspaces', icon: <FolderOpenOutlined />, label: '工作区文件' },
    ...(isAdmin
      ? [
          { path: '/models', icon: <ApiOutlined />, label: '模型管理' },
          { path: '/mcp-servers', icon: <CloudServerOutlined />, label: 'MCP 服务' },
          { path: '/knowledge', icon: <DatabaseOutlined />, label: '知识库' },
          { path: '/remote-tools', icon: <GlobalOutlined />, label: '远程工具' },
          { path: '/channels', icon: <MessageOutlined />, label: '渠道管理' },
          { path: '/cron-jobs', icon: <ClockCircleOutlined />, label: '定时任务' },
        ]
      : []),
    ...(isSuperAdmin
      ? [
          { path: '/users', icon: <TeamOutlined />, label: '用户管理' },
          { path: '/tenants', icon: <UsergroupAddOutlined />, label: '租户管理' },
        ]
      : []),
    { path: '/portrait', icon: <IdcardOutlined />, label: '个人画像' },
    { path: '/settings', icon: <SettingOutlined />, label: '设置' },
  ];

  const userMenuItems: MenuProps['items'] = [
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
      {/* Top header — full width */}
      <header className="flex h-14 flex-shrink-0 items-center justify-between border-b border-border bg-card px-5">
        <div className="flex items-center gap-2.5">
          <RobotOutlined className="text-xl text-primary" />
          <h1 className="text-sm font-bold tracking-tight text-foreground">智能体平台</h1>
        </div>

        <div className="flex items-center gap-2">
          {tenantOptions.length > 0 && (
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
          )}
          <SkinPicker />
          <Dropdown menu={{ items: userMenuItems }} trigger={['click']} placement="bottomRight">
            <button className="flex items-center gap-2 rounded-lg px-2 py-1.5 transition hover:bg-muted">
              <Avatar size={28} icon={<UserOutlined />} className="bg-primary/20 text-primary" />
              <span className="text-sm text-foreground">{username || '用户'}</span>
            </button>
          </Dropdown>
        </div>
      </header>

      {/* Below header: sidebar + content */}
      <div className="flex flex-1 overflow-hidden">
        {/* Sidebar */}
        {sidebarOpen ? (
          <aside className="flex w-56 flex-shrink-0 flex-col border-r border-border bg-card transition-all duration-200">
            <nav className="flex-1 overflow-y-auto py-2 px-2">
              {navItems.map((item) => {
                const isActive =
                  item.path === '/'
                    ? location.pathname === '/'
                    : location.pathname.startsWith(item.path);

                return (
                  <NavLink
                    key={item.path}
                    to={item.path}
                    className={cn(
                      'group relative flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition-all',
                      isActive
                        ? 'bg-muted text-foreground font-medium'
                        : 'text-muted-foreground hover:bg-muted/50 hover:text-foreground',
                    )}
                  >
                    {isActive && (
                      <div className="absolute left-0 top-1/2 -translate-y-1/2 h-5 w-[3px] rounded-r-full bg-brand-gradient-b" />
                    )}
                    <span className={cn('text-base transition', isActive && 'text-primary')}>
                      {item.icon}
                    </span>
                    <span>{item.label}</span>
                  </NavLink>
                );
              })}
            </nav>
            <div className="border-t border-border p-2 flex justify-center">
              <button
                onClick={() => setSidebarOpen(false)}
                className="flex h-8 w-8 items-center justify-center rounded-lg text-muted-foreground transition hover:bg-muted hover:text-foreground"
                title="折叠侧边栏"
              >
                <MenuFoldOutlined />
              </button>
            </div>
          </aside>
        ) : (
          <aside className="flex w-12 flex-shrink-0 flex-col items-center border-r border-border bg-card py-2 gap-1">
            {navItems.map((item) => {
              const isActive =
                item.path === '/'
                  ? location.pathname === '/'
                  : location.pathname.startsWith(item.path);

              return (
                <NavLink
                  key={item.path}
                  to={item.path}
                  className={cn(
                    'flex h-9 w-9 items-center justify-center rounded-lg text-base transition',
                    isActive
                      ? 'bg-muted text-primary'
                      : 'text-muted-foreground hover:bg-muted/50 hover:text-foreground',
                  )}
                  title={item.label}
                >
                  {item.icon}
                </NavLink>
              );
            })}
            <div className="flex-1" />
            <button
              onClick={() => setSidebarOpen(true)}
              className="flex h-9 w-9 items-center justify-center rounded-lg text-muted-foreground transition hover:bg-muted hover:text-foreground"
              title="展开侧边栏"
            >
              <MenuUnfoldOutlined />
            </button>
          </aside>
        )}

        {/* Main content */}
        <main className="flex flex-1 flex-col overflow-hidden bg-background">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
