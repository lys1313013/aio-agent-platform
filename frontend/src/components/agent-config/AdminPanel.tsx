import { ToolOutlined, ThunderboltOutlined, FileTextOutlined, CloseOutlined, ApiOutlined, DatabaseOutlined, ApartmentOutlined } from '@ant-design/icons';
import { Button, Typography, Spin } from 'antd';
import { useAgentConfig } from './context';
import type { AdminSectionKey } from './types';
import { PromptPanel } from './PromptPanel';
import { ToolsPanel } from './ToolsPanel';
import { McpPanel } from './McpPanel';
import { KnowledgePanel } from './KnowledgePanel';
import { GraphKnowledgePanel } from './GraphKnowledgePanel';
import { SkillsPanel } from './SkillsPanel';
import { ChildrenPanel } from './ChildrenPanel';

const { Text } = Typography;

export function AdminPanel({ activeSection, closePanel }: { activeSection: AdminSectionKey; closePanel: () => void; }) {
  const { loading, agent, error, retryLoad } = useAgentConfig();
  if (loading) return <div className="flex items-center justify-center py-16"><Spin /></div>;
  if (!agent) return <div className="space-y-3 p-4 text-center"><Text type="secondary">{error || '智能体未找到'}</Text><Button onClick={retryLoad}>重新加载</Button></div>;
  const sectionLabels: Record<AdminSectionKey, { icon: React.ReactNode; label: string; description: string; }> = {
    prompt: { icon: <FileTextOutlined />, label: '系统提示词', description: '模型与对话行为' },
    tools: { icon: <ToolOutlined />, label: '工具配置', description: '可调用的本地工具' },
    mcp: { icon: <ApiOutlined />, label: 'MCP 服务', description: '外部服务工具' },
    knowledge: { icon: <DatabaseOutlined />, label: '知识库', description: '检索增强内容' },
    'graph-knowledge': { icon: <ApartmentOutlined />, label: '图谱知识库', description: '关系与实体检索' },
    skills: { icon: <ThunderboltOutlined />, label: '技能配置', description: '可复用的能力流程' },
    children: { icon: <ApartmentOutlined />, label: '子智能体', description: '任务委派与协作' },
  };

  const sectionInfo = sectionLabels[activeSection];
  if (!sectionInfo) return null;

  return (
    <>
      <div className="flex items-center justify-between border-b border-border/50 bg-card px-4 py-3.5">
        <div className="flex items-center gap-2">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10 text-primary">
            {sectionInfo.icon}
          </span>
          <div>
            <Text strong className="block text-sm">{sectionInfo.label}</Text>
            <Text type="secondary" className="block text-[10px]">{sectionInfo.description}</Text>
          </div>
        </div>
        <button
          onClick={closePanel}
          className="flex h-6 w-6 items-center justify-center rounded text-muted-foreground transition hover:bg-muted hover:text-foreground"
        >
          <CloseOutlined className="text-xs" />
        </button>
      </div>

      <div className="agent-config-editor flex-1 overflow-y-auto bg-muted/20 p-3">
        {activeSection === 'prompt' && <PromptPanel />}
        {activeSection === 'tools' && <ToolsPanel />}
        {activeSection === 'mcp' && <McpPanel />}
        {activeSection === 'knowledge' && <KnowledgePanel />}
        {activeSection === 'graph-knowledge' && <GraphKnowledgePanel />}
        {activeSection === 'skills' && <SkillsPanel />}
        {activeSection === 'children' && <ChildrenPanel />}
      </div>
    </>
  );
}
