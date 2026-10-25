import { lazy, Suspense, useEffect } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { useAuthStore } from '@/stores/authStore';
const AppLayout = lazy(() => import('@/components/layout/AppLayout'));
const PortalLayout = lazy(() => import('@/components/layout/PortalLayout'));

const LoginPage = lazy(() => import('@/pages/LoginPage'));
const RegisterPage = lazy(() => import('@/pages/RegisterPage'));
const ChatPage = lazy(() => import('@/pages/ChatPage'));
const RoomsPage = lazy(() => import('@/pages/RoomsPage'));
const AgentsPage = lazy(() => import('@/pages/AgentsPage'));
const AgentChatPage = lazy(() => import('@/pages/AgentChatPage'));
const AgentApiPage = lazy(() => import('@/pages/AgentApiPage'));
const DashboardPage = lazy(() => import('@/pages/DashboardPage'));
const UsagePage = lazy(() => import('@/pages/UsagePage'));
const ObservabilityPage = lazy(() => import('@/pages/ObservabilityPage'));
const MemoryPage = lazy(() => import('@/pages/MemoryPage'));
const SkillsPage = lazy(() => import('@/pages/SkillsPage'));
const SkillDetailPage = lazy(() => import('@/pages/SkillDetailPage'));
const PetsPage = lazy(() => import('@/pages/PetsPage'));
const WorkspacesPage = lazy(() => import('@/pages/WorkspacesPage'));
const SettingsPage = lazy(() => import('@/pages/SettingsPage'));
const PortraitPage = lazy(() => import('@/pages/PortraitPage'));
const ModelManagementPage = lazy(() => import('@/pages/ModelManagementPage'));
const McpManagementPage = lazy(() => import('@/pages/McpManagementPage'));
const KnowledgeManagementPage = lazy(() => import('@/pages/KnowledgeManagementPage'));
const KnowledgeGraphPage = lazy(() => import('@/pages/KnowledgeGraphPage'));
const KnowledgeGraphDetailPage = lazy(() => import('@/pages/KnowledgeGraphDetailPage'));
const RemoteToolManagementPage = lazy(() => import('@/pages/RemoteToolManagementPage'));
const WebToolSettingsPage = lazy(() => import('@/pages/WebToolSettingsPage'));
const SystemConfigPage = lazy(() => import('@/pages/SystemConfigPage'));
const ChannelsPage = lazy(() => import('@/pages/ChannelsPage'));
const CronJobsPage = lazy(() => import('@/pages/CronJobsPage'));
const CronJobRunsPage = lazy(() => import('@/pages/CronJobRunsPage'));
const TenantManagementPage = lazy(() => import('@/pages/TenantManagementPage'));
const UserManagementPage = lazy(() => import('@/pages/UserManagementPage'));
const PortalAgentListPage = lazy(() => import('@/pages/portal/PortalAgentListPage'));
const PortalChatPage = lazy(() => import('@/pages/portal/PortalChatPage'));
const PortalSkillsPage = lazy(() => import('@/pages/portal/PortalSkillsPage'));

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const checkAuth = useAuthStore((s) => s.checkAuth);

  useEffect(() => {
    checkAuth();
  }, [checkAuth]);

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />;
  }

  return <>{children}</>;
}

function PublicRoute({ children }: { children: React.ReactNode }) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);

  if (isAuthenticated) {
    return <Navigate to="/" replace />;
  }

  return <>{children}</>;
}

/** 按角色分流首页：普通用户进用户端门户，管理员进管理端 */
function HomeRedirect() {
  const role = useAuthStore((s) => s.role);
  return <Navigate to={role === 'user' ? '/portal' : '/agents'} replace />;
}

export default function App() {
  return (
    <Suspense fallback={<div role="status" className="p-6 text-muted-foreground">加载中…</div>}>
    <Routes>
      {/* Public routes */}
      <Route
        path="/login"
        element={
          <PublicRoute>
            <LoginPage />
          </PublicRoute>
        }
      />
      <Route
        path="/register"
        element={
          <PublicRoute>
            <RegisterPage />
          </PublicRoute>
        }
      />

      <Route path="/skills/:skillId" element={<ProtectedRoute><SkillDetailPage /></ProtectedRoute>} />

      {/* Protected routes */}
      <Route
        element={
          <ProtectedRoute>
            <AppLayout />
          </ProtectedRoute>
        }
      >
        <Route path="/" element={<HomeRedirect />} />
        <Route path="/agents" element={<AgentsPage />} />
        <Route path="/agents/:agentId/chat" element={<AgentChatPage />} />
        <Route path="/agents/:agentId/chat/:sessionId" element={<AgentChatPage />} />
        <Route path="/agents/:agentId/api" element={<AgentApiPage />} />
        <Route path="/chat" element={<ChatPage />} />
        <Route path="/chat/:sessionId" element={<ChatPage />} />
        <Route path="/rooms" element={<RoomsPage />} />
        <Route path="/rooms/:roomId" element={<RoomsPage />} />
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/usage" element={<UsagePage />} />
        <Route path="/observability" element={<ObservabilityPage />} />
        <Route path="/memory" element={<MemoryPage />} />
        <Route path="/skills" element={<SkillsPage />} />
        <Route path="/pets" element={<PetsPage />} />
        <Route path="/workspaces" element={<WorkspacesPage />} />
        <Route path="/portrait" element={<PortraitPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/models" element={<ModelManagementPage />} />
        <Route path="/system-config" element={<SystemConfigPage />} />
        <Route path="/mcp-servers" element={<McpManagementPage />} />
        <Route path="/knowledge" element={<KnowledgeManagementPage />} />
        <Route path="/knowledge-graph" element={<KnowledgeGraphPage />} />
        <Route path="/knowledge-graph/:kbId" element={<KnowledgeGraphDetailPage />} />
        <Route path="/remote-tools" element={<RemoteToolManagementPage />} />
        <Route path="/web-tools" element={<WebToolSettingsPage />} />
        <Route path="/channels" element={<ChannelsPage />} />
        <Route path="/cron-jobs" element={<CronJobsPage />} />
        <Route path="/cron-jobs/runs" element={<CronJobRunsPage />} />
        <Route path="/tenants" element={<TenantManagementPage />} />
        <Route path="/users" element={<UserManagementPage />} />
      </Route>

      {/* 用户端对话门户（无管理端菜单的纯净布局） */}
      <Route
        element={
          <ProtectedRoute>
            <PortalLayout />
          </ProtectedRoute>
        }
      >
        <Route path="/portal" element={<PortalAgentListPage />} />
        <Route path="/portal/skills" element={<PortalSkillsPage />} />
        <Route path="/portal/rooms" element={<RoomsPage />} />
        <Route path="/portal/rooms/:roomId" element={<RoomsPage />} />
        <Route path="/portal/agents/:agentId/chat" element={<PortalChatPage />} />
        <Route path="/portal/agents/:agentId/chat/:sessionId" element={<PortalChatPage />} />
      </Route>

      {/* Catch all */}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
    </Suspense>
  );
}
