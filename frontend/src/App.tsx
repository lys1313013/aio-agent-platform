import { useEffect } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { useAuthStore } from '@/stores/authStore';
import LoginPage from '@/pages/LoginPage';
import RegisterPage from '@/pages/RegisterPage';
import ChatPage from '@/pages/ChatPage';
import AgentsPage from '@/pages/AgentsPage';
import AgentChatPage from '@/pages/AgentChatPage';
import AgentApiPage from '@/pages/AgentApiPage';
import DashboardPage from '@/pages/DashboardPage';
import UsagePage from '@/pages/UsagePage';
import MemoryPage from '@/pages/MemoryPage';
import SkillsPage from '@/pages/SkillsPage';
import PetsPage from '@/pages/PetsPage';
import WorkspacesPage from '@/pages/WorkspacesPage';
import SettingsPage from '@/pages/SettingsPage';
import PortraitPage from '@/pages/PortraitPage';
import ModelManagementPage from '@/pages/ModelManagementPage';
import McpManagementPage from '@/pages/McpManagementPage';
import KnowledgeManagementPage from '@/pages/KnowledgeManagementPage';
import RemoteToolManagementPage from '@/pages/RemoteToolManagementPage';
import WebToolSettingsPage from '@/pages/WebToolSettingsPage';
import SystemConfigPage from '@/pages/SystemConfigPage';
import ChannelsPage from '@/pages/ChannelsPage';
import CronJobsPage from '@/pages/CronJobsPage';
import TenantManagementPage from '@/pages/TenantManagementPage';
import UserManagementPage from '@/pages/UserManagementPage';
import AppLayout from '@/components/layout/AppLayout';

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
        <Route path="/" element={<Navigate to="/agents" replace />} />
        <Route path="/agents" element={<AgentsPage />} />
        <Route path="/agents/:agentId/chat" element={<AgentChatPage />} />
        <Route path="/agents/:agentId/chat/:sessionId" element={<AgentChatPage />} />
        <Route path="/agents/:agentId/api" element={<AgentApiPage />} />
        <Route path="/chat" element={<ChatPage />} />
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/usage" element={<UsagePage />} />
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
        <Route path="/remote-tools" element={<RemoteToolManagementPage />} />
        <Route path="/web-tools" element={<WebToolSettingsPage />} />
        <Route path="/channels" element={<ChannelsPage />} />
        <Route path="/cron-jobs" element={<CronJobsPage />} />
        <Route path="/tenants" element={<TenantManagementPage />} />
        <Route path="/users" element={<UserManagementPage />} />
      </Route>

      {/* Catch all */}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
