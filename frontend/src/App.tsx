import { useEffect } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { useAuthStore } from '@/stores/authStore';
import LoginPage from '@/pages/LoginPage';
import RegisterPage from '@/pages/RegisterPage';
import ChatPage from '@/pages/ChatPage';
import RoomsPage from '@/pages/RoomsPage';
import AgentsPage from '@/pages/AgentsPage';
import AgentChatPage from '@/pages/AgentChatPage';
import AgentApiPage from '@/pages/AgentApiPage';
import DashboardPage from '@/pages/DashboardPage';
import UsagePage from '@/pages/UsagePage';
import ObservabilityPage from '@/pages/ObservabilityPage';
import MemoryPage from '@/pages/MemoryPage';
import SkillsPage from '@/pages/SkillsPage';
import PetsPage from '@/pages/PetsPage';
import WorkspacesPage from '@/pages/WorkspacesPage';
import SettingsPage from '@/pages/SettingsPage';
import PortraitPage from '@/pages/PortraitPage';
import ModelManagementPage from '@/pages/ModelManagementPage';
import McpManagementPage from '@/pages/McpManagementPage';
import KnowledgeManagementPage from '@/pages/KnowledgeManagementPage';
import KnowledgeGraphPage from '@/pages/KnowledgeGraphPage';
import KnowledgeGraphDetailPage from '@/pages/KnowledgeGraphDetailPage';
import RemoteToolManagementPage from '@/pages/RemoteToolManagementPage';
import WebToolSettingsPage from '@/pages/WebToolSettingsPage';
import SystemConfigPage from '@/pages/SystemConfigPage';
import ChannelsPage from '@/pages/ChannelsPage';
import CronJobsPage from '@/pages/CronJobsPage';
import CronJobRunsPage from '@/pages/CronJobRunsPage';
import TenantManagementPage from '@/pages/TenantManagementPage';
import UserManagementPage from '@/pages/UserManagementPage';
import PortalAgentListPage from '@/pages/portal/PortalAgentListPage';
import PortalChatPage from '@/pages/portal/PortalChatPage';
import AppLayout from '@/components/layout/AppLayout';
import PortalLayout from '@/components/layout/PortalLayout';

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const checkAuth = useAuthStore((s) => s.checkAuth);

  useEffect(() => {
    checkAuth();
  }, []);

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
        <Route path="/portal/rooms" element={<RoomsPage />} />
        <Route path="/portal/rooms/:roomId" element={<RoomsPage />} />
        <Route path="/portal/agents/:agentId/chat" element={<PortalChatPage />} />
        <Route path="/portal/agents/:agentId/chat/:sessionId" element={<PortalChatPage />} />
      </Route>

      {/* Catch all */}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
