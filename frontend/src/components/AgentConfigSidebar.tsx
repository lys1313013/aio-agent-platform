import { ToolOutlined, ThunderboltOutlined, FileTextOutlined, HistoryOutlined, DashboardOutlined, ApiOutlined, DatabaseOutlined, ApartmentOutlined, MenuFoldOutlined, MenuUnfoldOutlined } from '@ant-design/icons';
import { cn } from '@/lib/utils';
import { useState } from 'react';
import { App, Tooltip } from 'antd';
import { AgentConfigContext } from './agent-config/context';
import { useAgentConfigData, useAgentConfigScope } from './agent-config/useAgentConfigData';
import { OverviewPanel } from './agent-config/OverviewPanel';
import { HistoryPanel } from './agent-config/HistoryPanel';
import { AdminPanel } from './agent-config/AdminPanel';
import type { SectionKey } from './agent-config/types';

interface AgentConfigSidebarProps {
  agentId: string;
  onAgentUpdated?: () => void;
}

export default function AgentConfigSidebar(props: AgentConfigSidebarProps) {
  const scope = useAgentConfigScope();
  return <AgentConfigSidebarSession key={`${scope}:${props.agentId}`} {...props} />;
}

function AgentConfigSidebarSession({ agentId, onAgentUpdated }: AgentConfigSidebarProps) {
  const { message } = App.useApp();
  const config = useAgentConfigData(agentId, { success: message.success, error: message.error, onUpdated: onAgentUpdated });
  const [activeSection, setActiveSection] = useState<SectionKey | null>('overview');
  const [sidebarExpanded, setSidebarExpanded] = useState(true);
  const toggleSection = (key: SectionKey) => {
    setActiveSection((prev) => {
      if (prev === key) {
        // Closing the section — also collapse the sidebar
        setSidebarExpanded(false);
        return null;
      }
      // Opening/switching section — ensure sidebar is expanded
      setSidebarExpanded(true);
      return key;
    });
  };

  const closePanel = () => {
    setActiveSection(null);
    setSidebarExpanded(false);
  };
  return (
    <AgentConfigContext.Provider value={config}>
      <div
        className={cn(
          'flex flex-shrink-0 border-r border-border bg-card overflow-hidden transition-all duration-200 ease-in-out',
          sidebarExpanded && activeSection ? 'w-[380px]' : 'w-11',
        )}
      >
        {/* Icon rail */}
        <div className="flex flex-col items-center py-2 w-11 flex-shrink-0 gap-1">
          {/* Overview */}
          <Tooltip title="概览" placement="right" mouseEnterDelay={0.5}>
            <button
              onClick={() => toggleSection('overview')}
              className={cn(
                'flex items-center justify-center w-9 h-9 rounded-lg transition-colors',
                activeSection === 'overview'
                  ? 'bg-primary/15 text-primary'
                  : 'text-muted-foreground hover:bg-muted hover:text-foreground',
              )}
            >
              <DashboardOutlined />
            </button>
          </Tooltip>

          {/* Config icons */}
          <Tooltip title="系统提示词" placement="right" mouseEnterDelay={0.5}>
            <button
              onClick={() => toggleSection('prompt')}
              className={cn(
                'flex items-center justify-center w-9 h-9 rounded-lg transition-colors',
                activeSection === 'prompt'
                  ? 'bg-primary/15 text-primary'
                  : 'text-muted-foreground hover:bg-muted hover:text-foreground',
              )}
            >
              <FileTextOutlined />
            </button>
          </Tooltip>

          <Tooltip title="工具配置" placement="right" mouseEnterDelay={0.5}>
            <button
              onClick={() => toggleSection('tools')}
              className={cn(
                'flex items-center justify-center w-9 h-9 rounded-lg transition-colors',
                activeSection === 'tools'
                  ? 'bg-primary/15 text-primary'
                  : 'text-muted-foreground hover:bg-muted hover:text-foreground',
              )}
            >
              <ToolOutlined />
            </button>
          </Tooltip>

          <Tooltip title="MCP 服务" placement="right" mouseEnterDelay={0.5}>
            <button
              onClick={() => toggleSection('mcp')}
              className={cn(
                'flex items-center justify-center w-9 h-9 rounded-lg transition-colors',
                activeSection === 'mcp'
                  ? 'bg-primary/15 text-primary'
                  : 'text-muted-foreground hover:bg-muted hover:text-foreground',
              )}
            >
              <ApiOutlined />
            </button>
          </Tooltip>

          <Tooltip title="知识库" placement="right" mouseEnterDelay={0.5}>
            <button
              onClick={() => toggleSection('knowledge')}
              className={cn(
                'flex items-center justify-center w-9 h-9 rounded-lg transition-colors',
                activeSection === 'knowledge'
                  ? 'bg-primary/15 text-primary'
                  : 'text-muted-foreground hover:bg-muted hover:text-foreground',
              )}
            >
              <DatabaseOutlined />
            </button>
          </Tooltip>

          <Tooltip title="图谱知识库" placement="right" mouseEnterDelay={0.5}>
            <button
              onClick={() => toggleSection('graph-knowledge')}
              className={cn(
                'flex items-center justify-center w-9 h-9 rounded-lg transition-colors',
                activeSection === 'graph-knowledge'
                  ? 'bg-primary/15 text-primary'
                  : 'text-muted-foreground hover:bg-muted hover:text-foreground',
              )}
            >
              <ApartmentOutlined />
            </button>
          </Tooltip>

          <Tooltip title="技能配置" placement="right" mouseEnterDelay={0.5}>
            <button
              onClick={() => toggleSection('skills')}
              className={cn(
                'flex items-center justify-center w-9 h-9 rounded-lg transition-colors',
                activeSection === 'skills'
                  ? 'bg-primary/15 text-primary'
                  : 'text-muted-foreground hover:bg-muted hover:text-foreground',
              )}
            >
              <ThunderboltOutlined />
            </button>
          </Tooltip>

          <Tooltip title="子智能体" placement="right" mouseEnterDelay={0.5}>
            <button
              onClick={() => toggleSection('children')}
              className={cn(
                'flex items-center justify-center w-9 h-9 rounded-lg transition-colors',
                activeSection === 'children'
                  ? 'bg-primary/15 text-primary'
                  : 'text-muted-foreground hover:bg-muted hover:text-foreground',
              )}
            >
              <ApartmentOutlined />
            </button>
          </Tooltip>

          {/* History icon - last */}
          <Tooltip title="对话历史" placement="right" mouseEnterDelay={0.5}>
            <button
              onClick={() => toggleSection('history')}
              className={cn(
                'flex items-center justify-center w-9 h-9 rounded-lg transition-colors',
                activeSection === 'history'
                  ? 'bg-primary/15 text-primary'
                  : 'text-muted-foreground hover:bg-muted hover:text-foreground',
              )}
            >
              <HistoryOutlined />
            </button>
          </Tooltip>

          {/* Spacer */}
          <div className="flex-1" />

          {/* Sidebar collapse/expand toggle */}
          <Tooltip title={sidebarExpanded ? '收起侧边栏' : '展开侧边栏'} placement="right" mouseEnterDelay={0.5}>
            <button
              onClick={() => {
                if (sidebarExpanded) {
                  // Collapse: close panel and shrink
                  setSidebarExpanded(false);
                  setActiveSection(null);
                } else {
                  // Expand: restore last section or default to history
                  setSidebarExpanded(true);
                  if (!activeSection) {
                    setActiveSection('history');
                  }
                }
              }}
              className="flex items-center justify-center w-9 h-9 rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              {sidebarExpanded ? <MenuFoldOutlined /> : <MenuUnfoldOutlined />}
            </button>
          </Tooltip>
        </div>

        {/* Inline panel — expands naturally with the sidebar */}
        {sidebarExpanded && activeSection && (
          <div className="flex-1 min-w-0 flex flex-col overflow-hidden">
            {activeSection === 'history'
              ? <HistoryPanel agentId={agentId} closePanel={closePanel} />
              : activeSection === 'overview'
                ? <OverviewPanel closePanel={closePanel} setActiveSection={setActiveSection} />
                : <AdminPanel activeSection={activeSection} closePanel={closePanel} />}
          </div>
        )}
      </div>
    </AgentConfigContext.Provider>
  );
}
