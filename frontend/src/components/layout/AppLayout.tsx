import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useAuthStore } from '@/stores/authStore';
import {
  LogoutOutlined,
  RobotOutlined,
  BarChartOutlined,
  LineChartOutlined,
  DashboardOutlined,
  BulbOutlined,
  ThunderboltOutlined,
  SettingOutlined,
  UserOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  ApiOutlined,
  ApartmentOutlined,
  CloudServerOutlined,
  DatabaseOutlined,
  GlobalOutlined,
  IdcardOutlined,
  ClockCircleOutlined,
  UsergroupAddOutlined,
  TeamOutlined,
  FolderOpenOutlined,
  MessageOutlined,
  SearchOutlined,
  SmileOutlined,
  RightOutlined,
  BgColorsOutlined,
  HistoryOutlined,
} from '@ant-design/icons';
import { Dropdown, Avatar, Select, Modal } from 'antd';
import type { MenuProps } from 'antd';
import { cn } from '@/lib/utils';
import { settingsApi } from '@/lib/api';
import { SkinPickerContent } from '@/components/SkinPicker';
import BrandLogo from '@/components/BrandLogo';
import PetWidget from '@/components/pet/PetWidget';

export default function AppLayout() {
  const { logout, role, username, tenantName } = useAuthStore();
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [skinOpen, setSkinOpen] = useState(false);
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

  interface NavItem {
    label: string;
    path?: string;
    icon?: ReactNode;
    /** 仅精确匹配该路径（用于有同级子路由的列表项，避免子路由高亮父项） */
    exact?: boolean;
    children?: NavItem[];
  }
  interface NavGroup {
    key: string;
    label: string;
    items: NavItem[];
  }

  const navGroups: NavGroup[] = [
    {
      key: 'overview',
      label: '概览',
      items: [
        { path: '/dashboard', icon: <DashboardOutlined />, label: '仪表盘' },
        { path: '/usage', icon: <BarChartOutlined />, label: '用量统计' },
        ...(isAdmin
          ? [{ path: '/observability', icon: <LineChartOutlined />, label: '可观测性' }]
          : []),
      ],
    },
    {
      key: 'agents',
      label: '智能体',
      items: [
        { path: '/agents', icon: <RobotOutlined />, label: '智能体' },
        { path: '/skills', icon: <ThunderboltOutlined />, label: '技能' },
        { path: '/memory', icon: <BulbOutlined />, label: '记忆' },
        { path: '/pets', icon: <SmileOutlined />, label: '宠物' },
        ...(isAdmin
          ? [
              {
                label: '定时任务',
                icon: <ClockCircleOutlined />,
                children: [
                  { path: '/cron-jobs', icon: <ClockCircleOutlined />, label: '任务列表', exact: true },
                  { path: '/cron-jobs/runs', icon: <HistoryOutlined />, label: '执行记录' },
                ],
              },
            ]
          : []),
      ],
    },
    {
      key: 'resources',
      label: '资源与集成',
      items: [
        { path: '/workspaces', icon: <FolderOpenOutlined />, label: '工作区文件' },
        ...(isAdmin
          ? [
              { path: '/channels', icon: <MessageOutlined />, label: '渠道管理' },
              {
                label: '知识库',
                icon: <DatabaseOutlined />,
                children: [
                  { path: '/knowledge-graph', icon: <ApartmentOutlined />, label: '知识图谱' },
                  { path: '/knowledge', icon: <DatabaseOutlined />, label: 'RAGFlow 知识库' },
                ],
              },
              { path: '/mcp-servers', icon: <CloudServerOutlined />, label: 'MCP 服务' },
              { path: '/remote-tools', icon: <GlobalOutlined />, label: '远程工具' },
              { path: '/web-tools', icon: <SearchOutlined />, label: 'Web 工具' },
            ]
          : []),
      ],
    },
    ...(isAdmin || isSuperAdmin
      ? [
          {
            key: 'system',
            label: '系统管理',
            items: [
              ...(isAdmin
                ? [
                    { path: '/models', icon: <ApiOutlined />, label: '模型管理' },
                    { path: '/system-config', icon: <SettingOutlined />, label: '系统配置' },
                  ]
                : []),
              ...(isSuperAdmin
                ? [
                    { path: '/users', icon: <TeamOutlined />, label: '用户管理' },
                    { path: '/tenants', icon: <UsergroupAddOutlined />, label: '租户管理' },
                  ]
                : []),
            ],
          },
        ]
      : []),
    {
      key: 'personal',
      label: '个人',
      items: [
        { path: '/portrait', icon: <IdcardOutlined />, label: '个人画像' },
        { path: '/settings', icon: <SettingOutlined />, label: '设置' },
      ],
    },
  ];

  // 精确匹配或按路径段前缀匹配，避免 /knowledge 误匹配 /knowledge-graph
  const isPathActive = (path: string, exact?: boolean) => {
    if (!path) return false;
    if (path === '/') return location.pathname === '/';
    if (exact) return location.pathname === path;
    return location.pathname === path || location.pathname.startsWith(`${path}/`);
  };

  const isItemActive = (item: NavItem) => {
    if (item.path) {
      return isPathActive(item.path, item.exact);
    }
    return item.children?.some((child) => isPathActive(child.path ?? '', child.exact)) ?? false;
  };

  const activeGroupKey =
    navGroups.find((group) => group.items.some(isItemActive))?.key ?? navGroups[0].key;

  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>(() => {
    try {
      const saved = localStorage.getItem('nav-collapsed-groups');
      if (saved) return JSON.parse(saved) as Record<string, boolean>;
    } catch {}
    return Object.fromEntries(
      navGroups.filter((group) => group.key !== activeGroupKey).map((group) => [group.key, true]),
    );
  });

  useEffect(() => {
    setCollapsedGroups((prev) => {
      if (!prev[activeGroupKey]) return prev;
      const next = { ...prev, [activeGroupKey]: false };
      localStorage.setItem('nav-collapsed-groups', JSON.stringify(next));
      return next;
    });
  }, [activeGroupKey]);

  const toggleGroup = useCallback((key: string) => {
    setCollapsedGroups((prev) => {
      const next = { ...prev, [key]: !prev[key] };
      localStorage.setItem('nav-collapsed-groups', JSON.stringify(next));
      return next;
    });
  }, []);

  const [collapsedSubmenus, setCollapsedSubmenus] = useState<Record<string, boolean>>(() => {
    try {
      const saved = localStorage.getItem('nav-collapsed-submenus');
      if (saved) return JSON.parse(saved) as Record<string, boolean>;
    } catch {}
    return {};
  });

  const toggleSubmenu = useCallback((label: string) => {
    setCollapsedSubmenus((prev) => {
      const next = { ...prev, [label]: !prev[label] };
      localStorage.setItem('nav-collapsed-submenus', JSON.stringify(next));
      return next;
    });
  }, []);

  const isSubmenuExpanded = (item: NavItem) => {
    const saved = collapsedSubmenus[item.label];
    return saved === undefined ? isItemActive(item) : !saved;
  };

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
      {/* Top header — full width */}
      <header className="flex h-14 flex-shrink-0 items-center justify-between border-b border-border bg-card px-5">
        <div className="flex items-center gap-2.5">
          <BrandLogo className="h-6 w-6" />
          <h1 className="text-sm font-bold tracking-tight text-foreground">智能体平台</h1>
        </div>

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
              className="flex max-w-52 items-center gap-1.5 rounded-lg px-2 py-1 text-sm text-muted-foreground"
            >
              <GlobalOutlined className="shrink-0 text-primary" />
              <span className="truncate">{tenantName}</span>
            </span>
          ) : null}
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
              {navGroups.map((group) => {
                const isCollapsed = !!collapsedGroups[group.key];
                return (
                  <div key={group.key} className="mb-1">
                    <button
                      onClick={() => toggleGroup(group.key)}
                      className="flex w-full items-center justify-between rounded-lg px-3 pt-2.5 pb-1.5 text-xs font-semibold text-muted-foreground transition hover:text-foreground"
                    >
                      <span>{group.label}</span>
                      <RightOutlined
                        className={cn(
                          'text-[10px] transition-transform duration-200',
                          !isCollapsed && 'rotate-90',
                        )}
                      />
                    </button>
                    <div
                      className={cn(
                        'grid transition-[grid-template-rows] duration-200 ease-in-out',
                        isCollapsed ? 'grid-rows-[0fr]' : 'grid-rows-[1fr]',
                      )}
                    >
                      <div className="overflow-hidden">
                        {group.items.map((item) => {
                          if (item.children && item.children.length) {
                            const expanded = isSubmenuExpanded(item);
                            const active = isItemActive(item);
                            return (
                              <div key={item.label} className="mb-0.5">
                                <button
                                  onClick={() => toggleSubmenu(item.label)}
                                  className="group relative flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition-all hover:bg-muted/50 hover:text-foreground"
                                  title={item.label}
                                >
                                  {active && (
                                    <div className="absolute left-0 top-1/2 -translate-y-1/2 h-5 w-[3px] rounded-r-full bg-brand-gradient-b" />
                                  )}
                                  <span
                                    className={cn(
                                      'text-base transition',
                                      active && 'text-primary',
                                    )}
                                  >
                                    {item.icon}
                                  </span>
                                  <span className="flex-1 text-left">{item.label}</span>
                                  <RightOutlined
                                    className={cn(
                                      'text-[10px] transition-transform duration-200',
                                      expanded && 'rotate-90',
                                    )}
                                  />
                                </button>
                                <div
                                  className={cn(
                                    'grid transition-[grid-template-rows] duration-200 ease-in-out',
                                    expanded ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]',
                                  )}
                                >
                                  <div className="overflow-hidden">
                                    {item.children.map((child) => {
                                      const childActive = isPathActive(child.path ?? '', child.exact);

                                      return (
                                        <NavLink
                                          key={child.path}
                                          to={child.path ?? '#'}
                                          className={cn(
                                            'group relative flex items-center gap-3 rounded-lg py-2.5 pl-9 pr-3 text-sm transition-all',
                                            childActive
                                              ? 'bg-muted text-foreground font-medium'
                                              : 'text-muted-foreground hover:bg-muted/50 hover:text-foreground',
                                          )}
                                        >
                                          {childActive && (
                                            <div className="absolute left-0 top-1/2 -translate-y-1/2 h-5 w-[3px] rounded-r-full bg-brand-gradient-b" />
                                          )}
                                          <span
                                            className={cn(
                                              'text-sm transition',
                                              childActive && 'text-primary',
                                            )}
                                          >
                                            {child.icon}
                                          </span>
                                          <span>{child.label}</span>
                                        </NavLink>
                                      );
                                    })}
                                  </div>
                                </div>
                              </div>
                            );
                          }

                          const isActive = isPathActive(item.path ?? '', item.exact);

                          return (
                            <NavLink
                              key={item.path}
                              to={item.path ?? '#'}
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
                              <span
                                className={cn(
                                  'text-base transition',
                                  isActive && 'text-primary',
                                )}
                              >
                                {item.icon}
                              </span>
                              <span>{item.label}</span>
                            </NavLink>
                          );
                        })}
                      </div>
                    </div>
                  </div>
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
          <aside className="flex w-12 flex-shrink-0 flex-col items-center border-r border-border bg-card py-2 gap-1 overflow-y-auto">
            {navGroups.map((group, groupIndex) => (
              <div key={group.key} className="flex flex-col items-center gap-1 w-full">
                {groupIndex > 0 && <div className="my-1 h-px w-6 bg-border" />}
                {group.items
                  .flatMap((item) =>
                    item.children && item.children.length ? item.children : [item],
                  )
                  .map((item) => {
                    const isActive = isPathActive(item.path ?? '', item.exact);

                    return (
                      <NavLink
                        key={item.path}
                        to={item.path ?? '#'}
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
              </div>
            ))}
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
      <PetWidget />
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
