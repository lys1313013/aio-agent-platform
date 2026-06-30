import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  ToolOutlined,
  ThunderboltOutlined,
  FileTextOutlined,
  SaveOutlined,
  CheckCircleFilled,
  CloseOutlined,
  HistoryOutlined,
  MessageOutlined,
  SearchOutlined,
  PushpinOutlined,
  PushpinFilled,
  InboxOutlined,
  EditOutlined,
  CheckOutlined,
  MoreOutlined,
  UndoOutlined,
  DeleteOutlined,
  DashboardOutlined,
  DisconnectOutlined,
  DatabaseOutlined,
  PlusOutlined,
  ArrowUpOutlined,
  ArrowDownOutlined,
  MinusCircleOutlined,
  ApartmentOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
} from '@ant-design/icons';
import {
  Button,
  Select,
  Checkbox,
  Input,
  InputNumber,
  Tag,
  Typography,
  Spin,
  App,
  Tooltip,
  Dropdown,
  Switch,
  Divider,
  Modal,
} from 'antd';
import type { MenuProps } from 'antd';
import { agentsApi, adminApi, skillsApi, toolsApi, mcpApi, knowledgeApi } from '@/lib/api';
import type { McpServer, KnowledgeBase } from '@/lib/api';
import { useChatStore } from '@/stores/chatStore';
import { formatRelativeTime, cn } from '@/lib/utils';
import { PROMPT_TEMPLATES } from '@/lib/promptTemplates';
import type { LLMModel } from '@/lib/api';
import type { Agent, Skill, ToolInfo, Session, AgentStats } from '@/lib/types';
import { getAgentIcon } from '@/lib/agent-icons';

const { Text } = Typography;
const { TextArea } = Input;
const { Option } = Select;

const TOOL_CATEGORY_LABELS: Record<string, string> = {
  sandbox: '沙箱执行',
  memory: '记忆',
  skills: '技能',
  multi_agent: '多智能体',
  interaction: '交互',
  knowledge: '知识库',
  file: '文件',
  remote: '远程工具',
  other: '其他',
};

const SKILL_CATEGORY_REVERSE: Record<string, string> = {
  'general': '通用', 'coding': '编码', 'ops': '运维', 'research': '研究', 'writing': '写作',
};

const TOOL_CATEGORY_ORDER = [
  'sandbox',
  'file',
  'knowledge',
  'memory',
  'skills',
  'multi_agent',
  'interaction',
  'remote',
  'other',
];

type AdminSectionKey = 'prompt' | 'tools' | 'mcp' | 'knowledge' | 'skills' | 'children';
type SectionKey = AdminSectionKey | 'overview' | 'history';

interface AgentConfigSidebarProps {
  agentId: string;
  onAgentUpdated?: () => void;
}

export default function AgentConfigSidebar({ agentId, onAgentUpdated }: AgentConfigSidebarProps) {
  const navigate = useNavigate();
  const { modal } = App.useApp();
  const [activeSection, setActiveSection] = useState<SectionKey | null>('overview');
  const [sidebarExpanded, setSidebarExpanded] = useState(true);
  const [agent, setAgent] = useState<Agent | null>(null);
  const [models, setModels] = useState<LLMModel[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [allTools, setAllTools] = useState<ToolInfo[]>([]);
  const [mcpServers, setMcpServers] = useState<McpServer[]>([]);
  const [allAgents, setAllAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(false);
  const sidebarRef = useRef<HTMLDivElement>(null);

  // Admin form states
  const [selectedModelId, setSelectedModelId] = useState<string | null>(null);
  const [enabledTools, setEnabledTools] = useState<string[]>([]);
  const [enabledMcpTools, setEnabledMcpTools] = useState<string[]>([]);
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [selectedKnowledgeBaseIds, setSelectedKnowledgeBaseIds] = useState<string[]>([]);
  const [selectedSkillIds, setSelectedSkillIds] = useState<string[]>([]);
  const [systemPrompt, setSystemPrompt] = useState('');
  const [agentTemperature, setAgentTemperature] = useState<number | null>(null);
  const [agentMaxIterations, setAgentMaxIterations] = useState<number | null>(null);
  const [welcomeMessage, setWelcomeMessage] = useState('');
  const [starterPrompts, setStarterPrompts] = useState<Array<{ label: string; icon: string }>>([]);
  const [enableMemoryExtraction, setEnableMemoryExtraction] = useState(true);
  const [enableRetry, setEnableRetry] = useState(true);
  const [selectedChildIds, setSelectedChildIds] = useState<string[]>([]);
  const [childMaxIterations, setChildMaxIterations] = useState<Record<string, number | null>>({});
  const [savingSection, setSavingSection] = useState<string | null>(null);
  const [savedSection, setSavedSection] = useState<string | null>(null);

  // Tool selection modal state
  const [toolModalOpen, setToolModalOpen] = useState(false);
  const [toolSearchQuery, setToolSearchQuery] = useState('');
  const [toolDetailModalOpen, setToolDetailModalOpen] = useState(false);
  const [selectedToolDetail, setSelectedToolDetail] = useState<ToolInfo | null>(null);
  // Draft selection edited inside the tool modal; committed to enabledTools only on confirm.
  const [toolDraft, setToolDraft] = useState<string[]>([]);

  // Skills modal state
  const [skillsModalOpen, setSkillsModalOpen] = useState(false);
  const [skillsSearchQuery, setSkillsSearchQuery] = useState('');
  const [skillsDraft, setSkillsDraft] = useState<string[]>([]);

  // Children (sub-agents) modal state
  const [childrenModalOpen, setChildrenModalOpen] = useState(false);
  const [childrenSearchQuery, setChildrenSearchQuery] = useState('');
  const [childrenDetailModalOpen, setChildrenDetailModalOpen] = useState(false);
  const [selectedChildDetail, setSelectedChildDetail] = useState<Agent | null>(null);
  // Draft selection edited inside the modal; committed to selectedChildIds only on confirm.
  const [childrenDraft, setChildrenDraft] = useState<string[]>([]);

  // Overview states
  const [agentStats, setAgentStats] = useState<AgentStats | null>(null);

  // Session history states
  const [searchQuery, setSearchQuery] = useState('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editValue, setEditValue] = useState('');

  const {
    sessions,
    activeSessionId,
    setActiveSession,
    deleteSession,
    renameSession,
    pinSession,
    archiveSession,
    refreshSessions,
    isSessionsLoading,
  } = useChatStore();

  const { message } = App.useApp();

  // Fetch agent config data
  const fetchAgentData = useCallback(async () => {
    setLoading(true);
    try {
      const [agents, m, s, t, mcp, kbs] = await Promise.all([
        agentsApi.adminList(),
        adminApi.listModels(),
        skillsApi.list({ is_active: true, limit: 200 }),
        toolsApi.list(),
        mcpApi.list(),
        knowledgeApi.list(),
      ]);
      const found = agents.find((a) => a.id === agentId);
      if (found) {
        setAgent(found);
        setSelectedModelId(found.model_id);
        // Filter out knowledge_retrieval and delegate_task — they're auto-injected
        // by backend when knowledge bases / child agents are bound
        setEnabledTools((found.enabled_tools || []).filter((t) => t !== 'knowledge_retrieval' && t !== 'delegate_task'));
        setSelectedKnowledgeBaseIds(found.knowledge_base_ids || []);
        setSelectedSkillIds(found.skill_ids || []);
        setSelectedChildIds(found.child_ids || []);
        setSystemPrompt(found.system_prompt || '');
        setAgentTemperature(found.temperature ?? null);
        setAgentMaxIterations(found.max_iterations ?? null);
        setWelcomeMessage(found.welcome_message || '');
        setStarterPrompts(found.starter_prompts || []);
        setEnableMemoryExtraction(found.enable_memory_extraction ?? true);
        setEnableRetry(found.enable_retry ?? true);
      }
      setModels(m);
      setAllAgents(agents);
      setSkills(s.items || []);
      setAllTools(t);
      setMcpServers(mcp);
      setKnowledgeBases(kbs);
      // Initialize child max iterations from loaded agents
      const childIters: Record<string, number | null> = {};
      agents.forEach((a) => {
        if (found?.child_ids?.includes(a.id)) {
          childIters[a.id] = a.max_iterations ?? null;
        }
      });
      if (Object.keys(childIters).length > 0) {
        setChildMaxIterations((prev) => ({ ...childIters, ...prev }));
      }
    } catch (err: any) {
      message.error(`加载配置失败：${err.message}`);
    } finally {
      setLoading(false);
    }
  }, [agentId, message]);

  // Fetch data when a section is opened
  useEffect(() => {
    if (activeSection === 'overview') {
      fetchAgentData();
      agentsApi.stats(agentId).then(setAgentStats).catch(() => {});
    } else if (activeSection && activeSection !== 'history') {
      fetchAgentData();
    }
    if (activeSection === 'history') {
      refreshSessions(agentId);
    }
  }, [activeSection, fetchAgentData, refreshSessions, agentId]);

  // Split agent.enabled_tools into built-in and MCP tool lists
  useEffect(() => {
    if (!agent?.enabled_tools || allTools.length === 0) return;
    const builtinNames = new Set(allTools.filter(t => t.category !== 'mcp').map(t => t.name));
    const builtinEnabled: string[] = [];
    const mcpEnabled: string[] = [];
    for (const name of agent.enabled_tools) {
      if (builtinNames.has(name)) {
        builtinEnabled.push(name);
      } else {
        mcpEnabled.push(name);
      }
    }
    setEnabledTools(builtinEnabled);
    setEnabledMcpTools(mcpEnabled);
  }, [agent?.enabled_tools, allTools]);

  // (click-outside auto-collapse removed — sidebar is manually controlled)

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

  // Session history handlers
  const handleDeleteSession = (id: string) => {
    modal.confirm({
      title: '删除对话？',
      content: '此操作无法撤销。',
      okText: '删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: () => deleteSession(id),
    });
  };

  const handleStartRename = (id: string, currentTitle: string) => {
    setEditingId(id);
    setEditValue(currentTitle);
  };

  const handleFinishRename = async (id: string) => {
    const trimmed = editValue.trim();
    if (trimmed && trimmed !== sessions.find((s) => s.id === id)?.title) {
      await renameSession(id, trimmed);
    }
    setEditingId(null);
  };

  const handleCancelRename = () => {
    setEditingId(null);
    setEditValue('');
  };

  const handlePin = async (id: string, currentPinned: boolean) => {
    await pinSession(id, !currentPinned);
  };

  const handleArchive = async (id: string, currentArchived: boolean) => {
    await archiveSession(id, !currentArchived);
  };

  const handleSelectSession = (id: string) => {
    setActiveSession(id);
    navigate(`/agents/${agentId}/chat/${id}`, { replace: true });
  };

  const filteredSessions = sessions.filter(
    (s) =>
      searchQuery === '' ||
      (s.title || '').toLowerCase().includes(searchQuery.toLowerCase()),
  );

  const pinnedSessions = filteredSessions.filter((s) => s.is_pinned);
  const regularSessions = filteredSessions.filter((s) => !s.is_pinned && !s.is_archived);
  const archivedSessions = filteredSessions.filter((s) => s.is_archived);

  const groupedRegular = useMemo(() => {
    const now = new Date();
    const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    const yesterdayStart = todayStart - 86400000;

    const groups: { today: Session[]; yesterday: Session[]; earlier: Session[] } = {
      today: [],
      yesterday: [],
      earlier: [],
    };

    for (const s of regularSessions) {
      const ts = new Date(s.updated_at).getTime();
      if (ts >= todayStart) groups.today.push(s);
      else if (ts >= yesterdayStart) groups.yesterday.push(s);
      else groups.earlier.push(s);
    }
    return groups;
  }, [regularSessions]);

  // Admin save handlers
  const showSaved = (section: string) => {
    setSavedSection(section);
    setTimeout(() => setSavedSection(null), 1500);
  };

  const saveSection = async (section: string, payload: Record<string, unknown>) => {
    setSavingSection(section);
    try {
      await agentsApi.adminUpdate(agentId, payload);
      message.success('保存成功');
      showSaved(section);
      onAgentUpdated?.();
      const agents = await agentsApi.adminList();
      const found = agents.find((a) => a.id === agentId);
      if (found) setAgent(found);
    } catch (err: any) {
      message.error(err.message || '保存失败');
    } finally {
      setSavingSection(null);
    }
  };

  const saveChildrenSection = async () => {
    setSavingSection('children');
    try {
      // 1. Save parent-child relationships
      await agentsApi.adminUpdate(agentId, { child_ids: selectedChildIds });
      // 2. Update each child agent's max_iterations
      const childUpdates = selectedChildIds
        .filter((cid) => childMaxIterations[cid] !== undefined)
        .map((cid) =>
          agentsApi.adminUpdate(cid, { max_iterations: childMaxIterations[cid] })
        );
      await Promise.all(childUpdates);
      message.success('保存成功');
      showSaved('children');
      onAgentUpdated?.();
      const agents = await agentsApi.adminList();
      const found = agents.find((a) => a.id === agentId);
      if (found) setAgent(found);
    } catch (err: any) {
      message.error(err.message || '保存失败');
    } finally {
      setSavingSection(null);
    }
  };

  // Render overview panel
  const renderOverviewPanel = () => (
    <>
      <div className="flex items-center justify-between px-4 py-3 border-b border-border/50">
        <div className="flex items-center gap-2">
          <DashboardOutlined className="text-primary" />
          <Text strong className="text-sm">概览</Text>
        </div>
        <button
          onClick={closePanel}
          className="flex h-6 w-6 items-center justify-center rounded text-muted-foreground transition hover:bg-muted hover:text-foreground"
        >
          <CloseOutlined className="text-xs" />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-4 pb-4">
        {loading && !agent ? (
          <div className="flex items-center justify-center py-16">
            <Spin />
          </div>
        ) : (
          <>
            {/* Stats cards */}
            <div className="grid grid-cols-3 gap-2 mt-3">
              <div className="rounded-lg bg-muted/50 p-3 text-center">
                <div className="text-lg font-bold text-foreground">
                  {agentStats?.total_sessions ?? 0}
                </div>
                <div className="text-xs text-muted-foreground">会话数</div>
              </div>
              <div className="rounded-lg bg-muted/50 p-3 text-center">
                <div className="text-lg font-bold text-foreground">
                  {agentStats?.total_messages ?? 0}
                </div>
                <div className="text-xs text-muted-foreground">消息数</div>
              </div>
              <div className="rounded-lg bg-muted/50 p-3 text-center">
                <div className="text-lg font-bold text-foreground">
                  {agentStats?.last_active_at ? formatRelativeTime(agentStats.last_active_at) : '-'}
                </div>
                <div className="text-xs text-muted-foreground">最后活跃</div>
              </div>
            </div>

            {/* Basic info */}
            {agent && (
              <div className="mt-4 space-y-3">
                <div className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                  基本信息
                </div>

                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <Text type="secondary" className="text-xs">名称</Text>
                    <Text className="text-xs">{agent.name}</Text>
                  </div>
                  <div className="flex items-center justify-between">
                    <Text type="secondary" className="text-xs">模型</Text>
                    <Tag
                      className="text-xs cursor-pointer"
                      onClick={() => setActiveSection('prompt')}
                    >
                      {agent.model_name || '全局默认'}
                    </Tag>
                  </div>
                  {agent.description && (
                    <div className="flex items-start justify-between gap-2">
                      <Text type="secondary" className="text-xs flex-shrink-0">描述</Text>
                      <Text className="text-xs text-right">{agent.description}</Text>
                    </div>
                  )}
                  <div className="flex items-center justify-between">
                    <Text type="secondary" className="text-xs">状态</Text>
                    <Tag color={agent.is_active ? 'green' : 'red'} className="text-xs">
                      {agent.is_active ? '启用' : '禁用'}
                    </Tag>
                  </div>
                  <div className="flex items-center justify-between">
                    <Text type="secondary" className="text-xs">记忆提取</Text>
                    <Tag
                      color={agent.enable_memory_extraction !== false ? 'green' : 'default'}
                      className="text-xs cursor-pointer"
                      onClick={() => setActiveSection('prompt')}
                    >
                      {agent.enable_memory_extraction !== false ? '已开启' : '已关闭'}
                    </Tag>
                  </div>
                  <div className="flex items-center justify-between">
                    <Text type="secondary" className="text-xs">LLM 重试</Text>
                    <Tag
                      color={agent.enable_retry !== false ? 'green' : 'default'}
                      className="text-xs cursor-pointer"
                      onClick={() => setActiveSection('prompt')}
                    >
                      {agent.enable_retry !== false ? '已开启' : '已关闭'}
                    </Tag>
                  </div>
                </div>

                <div className="border-t border-border/50 pt-3 space-y-2">
                  <div className="flex items-center justify-between">
                    <Text type="secondary" className="text-xs">子智能体</Text>
                    <Tag
                      color="cyan"
                      className="text-xs cursor-pointer"
                      onClick={() => setActiveSection('children')}
                    >
                      {agent.children_count} 个
                    </Tag>
                  </div>
                  <div className="flex items-center justify-between">
                    <Text type="secondary" className="text-xs">绑定技能</Text>
                    <Tag
                      color="purple"
                      className="text-xs cursor-pointer"
                      onClick={() => setActiveSection('skills')}
                    >
                      {agent.skill_ids?.length ?? 0} 个
                    </Tag>
                  </div>
                  <div className="flex items-center justify-between">
                    <Text type="secondary" className="text-xs">启用工具</Text>
                    <Tag
                      color="blue"
                      className="text-xs cursor-pointer"
                      onClick={() => setActiveSection('tools')}
                    >
                      {(() => {
                        const builtinNames = new Set(
                          allTools.filter((t) => t.category !== 'mcp').map((t) => t.name),
                        );
                        return (agent.enabled_tools || []).filter((t) => builtinNames.has(t)).length;
                      })()} 个
                    </Tag>
                  </div>
                  <div className="flex items-center justify-between">
                    <Text type="secondary" className="text-xs">MCP 服务</Text>
                    <Tag
                      color="geekblue"
                      className="text-xs cursor-pointer"
                      onClick={() => setActiveSection('mcp')}
                    >
                      {agent.mcp_server_ids?.length ?? 0} 个
                    </Tag>
                  </div>
                  <div className="flex items-center justify-between">
                    <Text type="secondary" className="text-xs">知识库</Text>
                    <Tag
                      color="purple"
                      className="text-xs cursor-pointer"
                      onClick={() => setActiveSection('knowledge')}
                    >
                      {agent.knowledge_base_ids?.length ?? 0} 个
                    </Tag>
                  </div>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </>
  );

  // Render history panel
  const renderHistoryPanel = () => (
    <>
      <div className="flex items-center justify-between px-4 py-3 border-b border-border/50">
        <div className="flex items-center gap-2">
          <HistoryOutlined className="text-primary" />
          <Text strong className="text-sm">对话历史</Text>
        </div>
        <button
          onClick={closePanel}
          className="flex h-6 w-6 items-center justify-center rounded text-muted-foreground transition hover:bg-muted hover:text-foreground"
        >
          <CloseOutlined className="text-xs" />
        </button>
      </div>

      <div className="px-3 py-2">
        <div className="relative">
          <SearchOutlined className="absolute left-3 top-1/2 -translate-y-1/2 text-xs text-muted-foreground/60" />
          <input
            type="text"
            placeholder="搜索对话..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full rounded-lg border border-border bg-background py-1.5 pl-8 pr-8 text-xs text-foreground outline-none transition placeholder:text-muted-foreground/50 focus:border-primary/40 focus:shadow-[0_0_0_3px_rgba(99,102,241,0.08)]"
          />
          {searchQuery && (
            <button
              onClick={() => setSearchQuery('')}
              className="absolute right-2 top-1/2 -translate-y-1/2 flex h-4 w-4 items-center justify-center rounded-full bg-muted text-muted-foreground transition hover:bg-muted-foreground/30"
            >
              <CloseOutlined className="text-[8px]" />
            </button>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-2 pb-4">
        {isSessionsLoading ? (
          <div className="px-3 py-8 text-center">
            <Spin size="small" />
          </div>
        ) : filteredSessions.length === 0 ? (
          <div className="flex flex-col items-center gap-2 px-3 py-10 text-center">
            <MessageOutlined className="text-3xl text-muted-foreground/20" />
            <span className="text-xs text-muted-foreground">
              {searchQuery ? '未找到匹配结果' : '暂无对话'}
            </span>
          </div>
        ) : (
          <div className="space-y-1">
            {pinnedSessions.length > 0 && (
              <SessionSection
                title="已置顶"
                icon={<PushpinFilled className="text-[10px]" />}
                sessions={pinnedSessions}
                activeSessionId={activeSessionId}
                editingId={editingId}
                editValue={editValue}
                onEditValueChange={setEditValue}
                onSelect={handleSelectSession}
                onStartRename={handleStartRename}
                onFinishRename={handleFinishRename}
                onCancelRename={handleCancelRename}
                onDelete={handleDeleteSession}
                onPin={handlePin}
                onArchive={handleArchive}
              />
            )}

            {groupedRegular.today.length > 0 && (
              <SessionSection
                title="今天"
                sessions={groupedRegular.today}
                activeSessionId={activeSessionId}
                editingId={editingId}
                editValue={editValue}
                onEditValueChange={setEditValue}
                onSelect={handleSelectSession}
                onStartRename={handleStartRename}
                onFinishRename={handleFinishRename}
                onCancelRename={handleCancelRename}
                onDelete={handleDeleteSession}
                onPin={handlePin}
                onArchive={handleArchive}
              />
            )}

            {groupedRegular.yesterday.length > 0 && (
              <SessionSection
                title="昨天"
                sessions={groupedRegular.yesterday}
                activeSessionId={activeSessionId}
                editingId={editingId}
                editValue={editValue}
                onEditValueChange={setEditValue}
                onSelect={handleSelectSession}
                onStartRename={handleStartRename}
                onFinishRename={handleFinishRename}
                onCancelRename={handleCancelRename}
                onDelete={handleDeleteSession}
                onPin={handlePin}
                onArchive={handleArchive}
              />
            )}

            {groupedRegular.earlier.length > 0 && (
              <SessionSection
                title="更早"
                sessions={groupedRegular.earlier}
                activeSessionId={activeSessionId}
                editingId={editingId}
                editValue={editValue}
                onEditValueChange={setEditValue}
                onSelect={handleSelectSession}
                onStartRename={handleStartRename}
                onFinishRename={handleFinishRename}
                onCancelRename={handleCancelRename}
                onDelete={handleDeleteSession}
                onPin={handlePin}
                onArchive={handleArchive}
              />
            )}

            {archivedSessions.length > 0 && (
              <SessionSection
                title="已归档"
                icon={<InboxOutlined className="text-[10px]" />}
                sessions={archivedSessions}
                activeSessionId={activeSessionId}
                editingId={editingId}
                editValue={editValue}
                onEditValueChange={setEditValue}
                onSelect={handleSelectSession}
                onStartRename={handleStartRename}
                onFinishRename={handleFinishRename}
                onCancelRename={handleCancelRename}
                onDelete={handleDeleteSession}
                onPin={handlePin}
                onArchive={handleArchive}
              />
            )}
          </div>
        )}
      </div>
    </>
  );

  // Render admin config panel
  const renderAdminPanel = () => {
    if (loading) {
      return (
        <div className="flex items-center justify-center py-16">
          <Spin />
        </div>
      );
    }
    if (!agent) {
      return (
        <div className="flex items-center justify-center py-16">
          <Text type="secondary" className="text-sm">智能体未找到</Text>
        </div>
      );
    }

    const sectionLabels: Record<AdminSectionKey, { icon: React.ReactNode; label: string }> = {
      prompt: { icon: <FileTextOutlined />, label: '系统提示词' },
      tools: { icon: <ToolOutlined />, label: '工具配置' },
      mcp: { icon: <DisconnectOutlined />, label: 'MCP 服务' },
      knowledge: { icon: <DatabaseOutlined />, label: '知识库' },
      skills: { icon: <ThunderboltOutlined />, label: '技能配置' },
      children: { icon: <ApartmentOutlined />, label: '子智能体' },
    };

    const sectionInfo = sectionLabels[activeSection as AdminSectionKey];
    if (!sectionInfo) return null;

    return (
      <>
        <div className="flex items-center justify-between px-4 py-3">
          <div className="flex items-center gap-2">
            <span className="text-primary">{sectionInfo.icon}</span>
            <Text strong className="text-sm">{sectionInfo.label}</Text>
          </div>
          <button
            onClick={closePanel}
            className="flex h-6 w-6 items-center justify-center rounded text-muted-foreground transition hover:bg-muted hover:text-foreground"
          >
            <CloseOutlined className="text-xs" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-4 pb-4">
          {activeSection === 'prompt' && (
            <div>
              {/* Model selection */}
              <Text type="secondary" className="text-xs block mb-2">
                模型（留空使用全局默认）
              </Text>
              <Select
                value={selectedModelId || undefined}
                onChange={(v) => setSelectedModelId(v || null)}
                allowClear
                placeholder="选择模型"
                className="w-full"
                size="small"
              >
                {models
                  .filter((m) => m.is_active)
                  .map((m) => (
                    <Option key={m.id} value={m.id}>
                      {m.provider_name} / {m.name}
                      {m.is_default && (
                        <Tag color="blue" className="ml-1 text-xs">默认</Tag>
                      )}
                    </Option>
                  ))}
              </Select>

              {/* Temperature control */}
              <div className="mt-3">
                <Text type="secondary" className="text-xs block mb-2">
                  推理温度（留空使用全局默认 0.7）
                </Text>
                <InputNumber
                  min={0}
                  max={2}
                  step={0.1}
                  precision={1}
                  value={agentTemperature ?? undefined}
                  onChange={(v) => setAgentTemperature(v ?? null)}
                  placeholder="默认"
                  className="w-full"
                  size="small"
                />
                {agentTemperature !== null && (
                  <Button
                    type="link"
                    size="small"
                    className="!p-0 !text-xs mt-1"
                    onClick={() => setAgentTemperature(null)}
                  >
                    重置为全局默认
                  </Button>
                )}
              </div>

              {/* Max iterations control */}
              <div className="mt-3">
                <Text type="secondary" className="text-xs block mb-2">
                  最大迭代次数（单轮对话内工具调用循环上限，留空使用全局默认 20，上限 100）
                </Text>
                <InputNumber
                  min={1}
                  max={100}
                  step={1}
                  precision={0}
                  value={agentMaxIterations ?? undefined}
                  onChange={(v) => setAgentMaxIterations(v ?? null)}
                  placeholder="默认"
                  className="w-full"
                  size="small"
                />
                {agentMaxIterations !== null && (
                  <Button
                    type="link"
                    size="small"
                    className="!p-0 !text-xs mt-1"
                    onClick={() => setAgentMaxIterations(null)}
                  >
                    重置为全局默认
                  </Button>
                )}
              </div>

              <Divider className="my-4" />

              {/* System prompt */}
              <Text type="secondary" className="text-xs block mb-2">
                系统提示词（留空使用默认模板）
              </Text>
              <Select
                placeholder="从模板加载提示词..."
                allowClear
                size="small"
                className="w-full mb-2"
                onChange={(templateId: string) => {
                  const tpl = PROMPT_TEMPLATES.find(t => t.id === templateId);
                  if (tpl) setSystemPrompt(tpl.content);
                }}
              >
                {PROMPT_TEMPLATES.map(t => (
                  <Option key={t.id} value={t.id}>
                    {t.icon} {t.name}
                  </Option>
                ))}
              </Select>
              <TextArea
                value={systemPrompt}
                onChange={(e) => setSystemPrompt(e.target.value)}
                rows={10}
                placeholder="你是一个专业的编程助手..."
                className="font-mono text-xs"
              />

              {/* Welcome message */}
              <div className="mt-4 pt-4 border-t border-border/50">
                <Text type="secondary" className="text-xs block mb-2">
                  欢迎语（新会话开始时展示）
                </Text>
                <TextArea
                  value={welcomeMessage}
                  onChange={(e) => setWelcomeMessage(e.target.value)}
                  rows={3}
                  placeholder="你好！有什么可以帮你的吗？"
                  className="text-xs"
                />
              </div>

              {/* Starter prompts */}
              <div className="mt-4 pt-4 border-t border-border/50">
                <div className="flex items-center justify-between mb-2">
                  <Text type="secondary" className="text-xs">
                    快捷提问（欢迎页引导用户开始对话）
                  </Text>
                  <Button
                    type="link"
                    size="small"
                    icon={<PlusOutlined />}
                    className="!p-0 !text-xs !h-auto"
                    onClick={() => setStarterPrompts([...starterPrompts, { label: '', icon: '💡' }])}
                  >
                    添加
                  </Button>
                </div>

                {starterPrompts.length === 0 ? (
                  <div className="py-3 text-center rounded-lg bg-muted/30 border border-dashed border-border/50">
                    <Text type="secondary" className="text-xs">
                      暂未配置，将使用默认提示
                    </Text>
                  </div>
                ) : (
                  <div className="space-y-2">
                    {starterPrompts.map((sp, idx) => (
                      <div
                        key={idx}
                        className="flex items-center gap-1.5 p-2 rounded-lg bg-muted/30 border border-border/50 group"
                      >
                        <Input
                          size="small"
                          value={sp.icon}
                          onChange={(e) => {
                            const next = [...starterPrompts];
                            next[idx] = { ...sp, icon: e.target.value };
                            setStarterPrompts(next);
                          }}
                          className="!w-12 text-center !text-sm"
                          maxLength={4}
                        />
                        <Input
                          size="small"
                          value={sp.label}
                          onChange={(e) => {
                            const next = [...starterPrompts];
                            next[idx] = { ...sp, label: e.target.value };
                            setStarterPrompts(next);
                          }}
                          placeholder="提问内容"
                          className="flex-1 !text-xs"
                        />
                        <div className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
                          <button
                            onClick={() => {
                              if (idx === 0) return;
                              const next = [...starterPrompts];
                              [next[idx - 1], next[idx]] = [next[idx], next[idx - 1]];
                              setStarterPrompts(next);
                            }}
                            disabled={idx === 0}
                            className="flex h-5 w-5 items-center justify-center rounded text-muted-foreground transition hover:bg-muted hover:text-foreground disabled:opacity-30"
                          >
                            <ArrowUpOutlined className="text-[10px]" />
                          </button>
                          <button
                            onClick={() => {
                              if (idx === starterPrompts.length - 1) return;
                              const next = [...starterPrompts];
                              [next[idx], next[idx + 1]] = [next[idx + 1], next[idx]];
                              setStarterPrompts(next);
                            }}
                            disabled={idx === starterPrompts.length - 1}
                            className="flex h-5 w-5 items-center justify-center rounded text-muted-foreground transition hover:bg-muted hover:text-foreground disabled:opacity-30"
                          >
                            <ArrowDownOutlined className="text-[10px]" />
                          </button>
                          <button
                            onClick={() => setStarterPrompts(starterPrompts.filter((_, i) => i !== idx))}
                            className="flex h-5 w-5 items-center justify-center rounded text-muted-foreground transition hover:bg-red-500/10 hover:text-red-500"
                          >
                            <MinusCircleOutlined className="text-[10px]" />
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Memory extraction toggle */}
              <div className="mt-4 pt-4 border-t border-border/50">
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0">
                    <Text className="text-xs block">自动记忆提取</Text>
                    <Text type="secondary" className="text-[10px] block mt-0.5 leading-snug">
                      对话结束后自动提取关键信息写入记忆，用于后续对话上下文
                    </Text>
                  </div>
                  <Switch
                    size="small"
                    checked={enableMemoryExtraction}
                    onChange={setEnableMemoryExtraction}
                    className="flex-shrink-0"
                  />
                </div>
              </div>

              {/* LLM retry toggle */}
              <div className="mt-4 pt-4 border-t border-border/50">
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0">
                    <Text className="text-xs block">LLM 自动重试</Text>
                    <Text type="secondary" className="text-[10px] block mt-0.5 leading-snug">
                      API 调用失败时自动重试（最多 3 次，指数退避），关闭后立即报错
                    </Text>
                  </div>
                  <Switch
                    size="small"
                    checked={enableRetry}
                    onChange={setEnableRetry}
                    className="flex-shrink-0"
                  />
                </div>
              </div>

              <SaveButtonRow
                saving={savingSection === 'prompt'}
                saved={savedSection === 'prompt'}
                onSave={() => saveSection('prompt', {
                  model_id: selectedModelId,
                  temperature: agentTemperature,
                  max_iterations: agentMaxIterations,
                  system_prompt: systemPrompt || null,
                  welcome_message: welcomeMessage || null,
                  starter_prompts: starterPrompts.filter((sp) => sp.label.trim()).length > 0
                    ? starterPrompts.filter((sp) => sp.label.trim())
                    : null,
                  enable_memory_extraction: enableMemoryExtraction,
                  enable_retry: enableRetry,
                })}
              />
            </div>
          )}

          {activeSection === 'tools' && (() => {
            // knowledge_retrieval and delegate_task are auto-injected by backend
            // when knowledge bases / child agents are bound;
            // hide them from the manual tool selection UI so users don't need to check them.
            // mcp tools have their own dedicated section, so exclude them here too.
            const allLocalTools = allTools.filter(
              (t) => t.category !== 'mcp' && t.name !== 'knowledge_retrieval' && t.name !== 'delegate_task',
            );

            // Filter tools for the modal by search query
            const q = toolSearchQuery.toLowerCase();
            const filteredTools = allLocalTools.filter((tool) => {
              if (!toolSearchQuery) return true;
              return (
                tool.name.toLowerCase().includes(q) ||
                tool.label.toLowerCase().includes(q) ||
                tool.description.toLowerCase().includes(q)
              );
            });

            // Group filtered tools by category, ordered by TOOL_CATEGORY_ORDER
            const toolsByCategory = filteredTools.reduce<Record<string, ToolInfo[]>>((acc, tool) => {
              const cat = tool.category || 'other';
              (acc[cat] ||= []).push(tool);
              return acc;
            }, {});
            const orderedCategories = Object.keys(toolsByCategory).sort((a, b) => {
              const ia = TOOL_CATEGORY_ORDER.indexOf(a);
              const ib = TOOL_CATEGORY_ORDER.indexOf(b);
              return (ia === -1 ? 999 : ia) - (ib === -1 ? 999 : ib);
            });

            return (
            <>
            <div>
              <Text type="secondary" className="text-xs block mb-2">
                选择该智能体可使用的工具。知识库检索和委派任务工具会根据知识库绑定和子智能体配置自动注入，无需手动选择。
              </Text>

              {/* Selected tools display */}
              {enabledTools.length > 0 && (
                <>
                  <div className="flex items-center justify-between mb-2">
                    <Text type="secondary" className="text-xs font-semibold">
                      已选择 ({enabledTools.length})
                    </Text>
                    <div className="flex items-center gap-2">
                      <Button
                        size="small"
                        type="link"
                        className="!p-0 !text-xs"
                        onClick={() => setEnabledTools(allLocalTools.map((t) => t.name))}
                      >
                        全选
                      </Button>
                      <Button
                        size="small"
                        type="link"
                        danger
                        className="!p-0 !text-xs"
                        onClick={() => setEnabledTools([])}
                      >
                        清空
                      </Button>
                    </div>
                  </div>
                  <div className="space-y-1 mb-3 max-h-80 overflow-y-auto">
                    {enabledTools.map((toolName) => {
                      const tool = allLocalTools.find((t) => t.name === toolName);
                      if (!tool) return null;
                      return (
                        <div
                          key={tool.name}
                          className="flex items-center gap-2 p-2 rounded-lg border border-border/50 bg-muted/30 hover:bg-muted/50 transition-colors group"
                        >
                          <div
                            className="flex-1 min-w-0 cursor-pointer"
                            onClick={() => {
                              setSelectedToolDetail(tool);
                              setToolDetailModalOpen(true);
                            }}
                          >
                            <div className="flex items-center gap-1.5">
                              <Text className="text-xs font-medium">{tool.label}</Text>
                              {tool.permission_level === 'dangerous' && (
                                <Tag color="red" className="text-[10px] leading-none">危险</Tag>
                              )}
                              {tool.category === 'multi_agent' && (
                                <Tag color="cyan" className="text-[10px] leading-none">多智能体</Tag>
                              )}
                              {tool.category === 'remote' && (
                                <Tag color="purple" className="text-[10px] leading-none">远程</Tag>
                              )}
                            </div>
                          </div>
                          <button
                            onClick={() => {
                              setEnabledTools((prev) => prev.filter((t) => t !== tool.name));
                            }}
                            className="flex-shrink-0 w-5 h-5 flex items-center justify-center rounded text-muted-foreground hover:text-red-500 hover:bg-red-500/10 transition-colors opacity-0 group-hover:opacity-100"
                          >
                            <CloseOutlined className="text-xs" />
                          </button>
                        </div>
                      );
                    })}
                  </div>
                </>
              )}

              {/* Add button */}
              <div className="mb-3">
                <Button
                  block
                  icon={<PlusOutlined />}
                  onClick={() => {
                    setToolDraft(enabledTools);
                    setToolModalOpen(true);
                  }}
                  className="text-xs"
                >
                  添加工具
                </Button>
              </div>

              <SaveButtonRow
                saving={savingSection === 'tools'}
                saved={savedSection === 'tools'}
                onSave={() => saveSection('tools', {
                  enabled_tools: [...enabledTools, ...enabledMcpTools],
                })}
              />
            </div>

            {/* Tool selection modal */}
            <Modal
              title="选择工具"
              open={toolModalOpen}
              onCancel={() => {
                setToolModalOpen(false);
                setToolSearchQuery('');
              }}
              onOk={() => {
                setEnabledTools(toolDraft);
                setToolModalOpen(false);
                setToolSearchQuery('');
              }}
              okText="确定"
              cancelText="取消"
              width={1320}
              destroyOnClose
              styles={{ body: { maxHeight: '80vh', overflowY: 'auto' } }}
            >
              <div className="relative mb-3">
                <SearchOutlined className="absolute left-3 top-1/2 -translate-y-1/2 text-sm text-muted-foreground/60" />
                <input
                  type="text"
                  placeholder="搜索工具名称或描述..."
                  value={toolSearchQuery}
                  onChange={(e) => setToolSearchQuery(e.target.value)}
                  className="w-full rounded-lg border border-border bg-background py-2 pl-9 pr-3 text-sm text-foreground outline-none transition placeholder:text-muted-foreground/50 focus:border-primary/40 focus:shadow-[0_0_0_3px_rgba(99,102,241,0.08)]"
                />
              </div>

              <div className="space-y-3">
                {orderedCategories.map((cat) => {
                  const catTools = toolsByCategory[cat];
                  const catLabel = TOOL_CATEGORY_LABELS[cat] || cat;
                  const catSelectedCount = catTools.filter((t) => toolDraft.includes(t.name)).length;
                  const catAllSelected = catTools.length > 0 && catSelectedCount === catTools.length;
                  return (
                    <div key={cat}>
                      <div className="sticky top-0 bg-background z-10 py-1.5 flex items-center justify-between">
                        <Text type="secondary" className="text-sm font-semibold">
                          {catLabel} ({catTools.length})
                          {catSelectedCount > 0 && (
                            <Text type="secondary" className="text-xs font-normal ml-1.5">
                              已选 {catSelectedCount}
                            </Text>
                          )}
                        </Text>
                        <Button
                          size="small"
                          type="link"
                          className="!p-0 !text-xs"
                          onClick={() => {
                            const names = catTools.map((t) => t.name);
                            if (catAllSelected) {
                              setToolDraft((prev) => prev.filter((n) => !names.includes(n)));
                            } else {
                              setToolDraft((prev) => [
                                ...prev,
                                ...names.filter((n) => !prev.includes(n)),
                              ]);
                            }
                          }}
                        >
                          {catAllSelected ? '取消全选' : '全选'}
                        </Button>
                      </div>
                      <div className="grid grid-cols-2 gap-2.5">
                        {catTools.map((tool) => {
                          const isSelected = toolDraft.includes(tool.name);
                          return (
                            <div
                              key={tool.name}
                              className={`flex items-start gap-2 p-3 rounded-lg cursor-pointer border transition-colors ${
                                isSelected
                                  ? 'bg-primary/5 border-primary/30'
                                  : 'border-border/50 hover:bg-muted/50'
                              }`}
                            >
                              <Checkbox
                                checked={isSelected}
                                className="mt-0.5"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  if (isSelected) {
                                    setToolDraft((prev) => prev.filter((t) => t !== tool.name));
                                  } else {
                                    setToolDraft((prev) => [...prev, tool.name]);
                                  }
                                }}
                              />
                              <div
                                className="flex-1 min-w-0"
                                onClick={() => {
                                  setSelectedToolDetail(tool);
                                  setToolDetailModalOpen(true);
                                }}
                              >
                                <div className="flex items-center gap-1.5 flex-wrap">
                                  <Text className="text-sm font-medium">{tool.label}</Text>
                                  <Tag className="text-xs leading-none font-mono">{tool.name}</Tag>
                                  {tool.permission_level === 'dangerous' && (
                                    <Tag color="red" className="text-xs leading-none">危险</Tag>
                                  )}
                                  {tool.category === 'multi_agent' && (
                                    <Tag color="cyan" className="text-xs leading-none">多智能体</Tag>
                                  )}
                                  {tool.category === 'remote' && (
                                    <Tag color="purple" className="text-xs leading-none">远程</Tag>
                                  )}
                                  {tool.category === 'remote' && tool.method && (
                                    <Tag color="blue" className="text-xs leading-none">{tool.method}</Tag>
                                  )}
                                </div>
                                {tool.description && (
                                  <Text type="secondary" className="text-xs block mt-1 line-clamp-2">
                                    {tool.description}
                                  </Text>
                                )}
                                <div className="flex items-center gap-2 mt-1.5 flex-wrap">
                                  {tool.timeout && (
                                    <Text type="secondary" className="text-xs">
                                      超时: {tool.timeout}s
                                    </Text>
                                  )}
                                  {tool.requires_sandbox && (
                                    <Tag color="orange" className="text-xs leading-none">需要沙箱</Tag>
                                  )}
                                </div>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  );
                })}

                {/* No results */}
                {orderedCategories.length === 0 && (
                  <div className="py-8 text-center">
                    <ToolOutlined className="text-2xl text-muted-foreground/40 mb-2" />
                    <Text type="secondary" className="text-xs block">
                      {toolSearchQuery ? '未找到匹配的工具' : '暂无可用工具'}
                    </Text>
                  </div>
                )}
              </div>
            </Modal>

            {/* Tool detail modal */}
            <Modal
              title="工具详情"
              open={toolDetailModalOpen}
              onCancel={() => {
                setToolDetailModalOpen(false);
                setSelectedToolDetail(null);
              }}
              footer={null}
              width={800}
              destroyOnClose
            >
              {selectedToolDetail && (
                <div className="space-y-4">
                  {/* Basic info */}
                  <div>
                    <div className="flex items-center gap-2 mb-3">
                      <Text className="text-base font-semibold">{selectedToolDetail.label}</Text>
                      <Tag className="font-mono">{selectedToolDetail.name}</Tag>
                      {selectedToolDetail.permission_level === 'dangerous' && (
                        <Tag color="red">危险</Tag>
                      )}
                      {selectedToolDetail.category === 'multi_agent' && (
                        <Tag color="cyan">多智能体</Tag>
                      )}
                      {selectedToolDetail.category === 'remote' && (
                        <Tag color="purple">远程</Tag>
                      )}
                    </div>

                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <Text type="secondary" className="text-xs block mb-1">分类</Text>
                        <Tag className="text-xs">{selectedToolDetail.category}</Tag>
                      </div>
                      <div>
                        <Text type="secondary" className="text-xs block mb-1">权限级别</Text>
                        <Tag
                          color={selectedToolDetail.permission_level === 'dangerous' ? 'red' : 'default'}
                          className="text-xs"
                        >
                          {selectedToolDetail.permission_level}
                        </Tag>
                      </div>
                      {selectedToolDetail.timeout && (
                        <div>
                          <Text type="secondary" className="text-xs block mb-1">超时时间</Text>
                          <Text className="text-xs">{selectedToolDetail.timeout}s</Text>
                        </div>
                      )}
                      <div>
                        <Text type="secondary" className="text-xs block mb-1">需要沙箱</Text>
                        <Tag color={selectedToolDetail.requires_sandbox ? 'orange' : 'default'} className="text-xs">
                          {selectedToolDetail.requires_sandbox ? '是' : '否'}
                        </Tag>
                      </div>
                      {selectedToolDetail.method && (
                        <div>
                          <Text type="secondary" className="text-xs block mb-1">HTTP 方法</Text>
                          <Tag color="blue" className="text-xs">{selectedToolDetail.method}</Tag>
                        </div>
                      )}
                    </div>
                  </div>

                  <Divider className="!my-3" />

                  {/* Description */}
                  <div>
                    <Text type="secondary" className="text-xs block mb-2">工具说明</Text>
                    <div className="p-3 rounded-lg bg-muted/30 border border-border/50">
                      <Text className="text-xs whitespace-pre-wrap">
                        {selectedToolDetail.description || '暂无说明'}
                      </Text>
                    </div>
                  </div>

                  {/* Function signature with parameters */}
                  {selectedToolDetail.parameters && (
                    <div>
                      <Text type="secondary" className="text-xs block mb-2">函数签名</Text>
                      <div className="p-3 rounded-lg bg-muted/30 border border-border/50">
                        <pre className="text-xs font-mono whitespace-pre-wrap overflow-x-auto">
                          {(() => {
                            const params = selectedToolDetail.parameters as Record<string, unknown>;
                            const properties = (params.properties || {}) as Record<string, Record<string, unknown>>;
                            const required = (params.required || []) as string[];

                            const paramsList = Object.entries(properties).map(([key, value]) => {
                              const type = value.type as string || 'any';
                              const isRequired = required.includes(key);
                              const description = value.description as string || '';
                              return `  ${key}${isRequired ? '' : '?'}: ${type}  // ${description}`;
                            });

                            return `${selectedToolDetail.name}(\n${paramsList.join('\n')}\n)`;
                          })()}
                        </pre>
                      </div>
                    </div>
                  )}

                  {/* Parameters detail */}
                  {selectedToolDetail.parameters && (
                    <div>
                      <Text type="secondary" className="text-xs block mb-2">参数详情</Text>
                      <div className="space-y-2">
                        {(() => {
                          const params = selectedToolDetail.parameters as Record<string, unknown>;
                          const properties = (params.properties || {}) as Record<string, Record<string, unknown>>;
                          const required = (params.required || []) as string[];

                          return Object.entries(properties).map(([key, value]) => {
                            const type = value.type as string || 'any';
                            const isRequired = required.includes(key);
                            const description = value.description as string || '';
                            const enumValues = value.enum as string[] | undefined;
                            const defaultValue = value.default;

                            return (
                              <div key={key} className="p-2.5 rounded-lg bg-muted/20 border border-border/30">
                                <div className="flex items-center gap-2 mb-1">
                                  <Text className="text-xs font-mono font-semibold">{key}</Text>
                                  <Tag className="text-[10px] leading-none">{type}</Tag>
                                  {isRequired ? (
                                    <Tag color="red" className="text-[10px] leading-none">必填</Tag>
                                  ) : (
                                    <Tag className="text-[10px] leading-none">可选</Tag>
                                  )}
                                </div>
                                {description && (
                                  <Text type="secondary" className="text-xs block mb-1">
                                    {description}
                                  </Text>
                                )}
                                {enumValues && (
                                  <div className="mt-1">
                                    <Text type="secondary" className="text-[10px]">
                                      可选值: {enumValues.join(', ')}
                                    </Text>
                                  </div>
                                )}
                                {defaultValue !== undefined && (
                                  <div className="mt-1">
                                    <Text type="secondary" className="text-[10px]">
                                      默认值: {String(defaultValue)}
                                    </Text>
                                  </div>
                                )}
                              </div>
                            );
                          });
                        })()}
                      </div>
                    </div>
                  )}

                  {/* Usage note */}
                  {selectedToolDetail.permission_level === 'dangerous' && (
                    <div className="p-3 rounded-lg bg-red-500/5 border border-red-500/20">
                      <div className="flex items-start gap-2">
                        <span className="text-red-500">⚠️</span>
                        <div>
                          <Text className="text-xs font-medium text-red-600 block mb-1">危险工具提示</Text>
                          <Text type="secondary" className="text-xs">
                            此工具具有较高权限，可能对系统产生重大影响。使用时请谨慎评估风险。
                          </Text>
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </Modal>
            </>
            );
          })()}

          {activeSection === 'mcp' && (
            <div>
              <Text type="secondary" className="text-xs block mb-3">
                勾选该智能体可使用的 MCP 工具。支持逐个选择或按服务器批量选择。
              </Text>

              {mcpServers.length === 0 ? (
                <div className="py-6 text-center">
                  <DisconnectOutlined className="text-lg text-muted-foreground/40" />
                  <Text type="secondary" className="text-xs block mt-1">
                    暂无可用的 MCP 服务
                  </Text>
                  <Text type="secondary" className="text-xs block mt-0.5">
                    请先在 MCP 服务管理页面添加服务
                  </Text>
                </div>
              ) : (
                <div className="space-y-3">
                  {mcpServers.map((server) => {
                    const statusColor =
                      server.status === 'connected' ? 'green'
                      : server.status === 'error' ? 'red'
                      : 'default';
                    const statusLabel =
                      server.status === 'connected' ? '已连接'
                      : server.status === 'error' ? '错误'
                      : '未连接';

                    // Get tool full names for this server
                    const serverToolNames = (server.tools || []).map(
                      (t) => `${server.tool_prefix || ''}${t.name}`
                    );
                    const selectedCount = serverToolNames.filter((n) => enabledMcpTools.includes(n)).length;
                    const allSelected = serverToolNames.length > 0 && selectedCount === serverToolNames.length;
                    const someSelected = selectedCount > 0 && !allSelected;

                    const toggleServerTools = () => {
                      if (allSelected) {
                        // Deselect all tools from this server
                        setEnabledMcpTools((prev) => prev.filter((n) => !serverToolNames.includes(n)));
                      } else {
                        // Select all tools from this server
                        const newTools = serverToolNames.filter((n) => !enabledMcpTools.includes(n));
                        setEnabledMcpTools((prev) => [...prev, ...newTools]);
                      }
                    };

                    return (
                      <div
                        key={server.id}
                        className={cn(
                          'rounded-lg border transition',
                          someSelected || allSelected
                            ? 'border-primary/40 bg-primary/5'
                            : 'border-border/50',
                          !server.is_active && 'opacity-50',
                        )}
                      >
                        {/* Server header */}
                        <div
                          className="flex items-center gap-2 p-2.5 cursor-pointer"
                          onClick={toggleServerTools}
                        >
                          <Checkbox
                            checked={allSelected}
                            indeterminate={someSelected}
                            onClick={(e) => e.stopPropagation()}
                            onChange={() => toggleServerTools()}
                          />
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-1.5">
                              <Text className="text-xs font-medium truncate">{server.name}</Text>
                              <Tag color={statusColor} className="text-[10px] leading-none flex-shrink-0">
                                {statusLabel}
                              </Tag>
                            </div>
                            <Text type="secondary" className="text-[10px] block mt-0.5 truncate">
                              {server.transport_type} · {server.tools_count} 个工具
                              {selectedCount > 0 && ` · 已选 ${selectedCount}`}
                            </Text>
                          </div>
                        </div>

                        {/* Tool list */}
                        {server.tools && server.tools.length > 0 && (
                          <div className="border-t border-border/30 px-2 py-1.5 space-y-1">
                            {server.tools.map((tool) => {
                              const fullName = `${server.tool_prefix || ''}${tool.name}`;
                              const isChecked = enabledMcpTools.includes(fullName);
                              return (
                                <div key={fullName} className="flex items-start gap-2 px-1">
                                  <Checkbox
                                    checked={isChecked}
                                    onChange={(e) => {
                                      if (e.target.checked) {
                                        setEnabledMcpTools((prev) => [...prev, fullName]);
                                      } else {
                                        setEnabledMcpTools((prev) => prev.filter((n) => n !== fullName));
                                      }
                                    }}
                                  />
                                  <div className="flex-1 min-w-0">
                                    <Text className="text-xs">{tool.name}</Text>
                                    {tool.description && (
                                      <Text type="secondary" className="text-[10px] block truncate">
                                        {tool.description}
                                      </Text>
                                    )}
                                  </div>
                                </div>
                              );
                            })}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}

              <SaveButtonRow
                saving={savingSection === 'mcp'}
                saved={savedSection === 'mcp'}
                onSave={() => {
                  // Derive mcp_server_ids from selected MCP tools
                  const selectedServerIds = mcpServers
                    .filter((server) => {
                      const serverToolNames = (server.tools || []).map(
                        (t) => `${server.tool_prefix || ''}${t.name}`
                      );
                      return serverToolNames.some((n) => enabledMcpTools.includes(n));
                    })
                    .map((s) => s.id);
                  saveSection('mcp', {
                    enabled_tools: [...enabledTools, ...enabledMcpTools],
                    mcp_server_ids: selectedServerIds,
                  });
                }}
              />
            </div>
          )}

          {activeSection === 'knowledge' && (
            <div>
              <Text type="secondary" className="text-xs block mb-2">
                选择该智能体可检索的知识库。绑定后，Agent 可自动调用知识库检索工具获取相关信息。
              </Text>

              {knowledgeBases.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-8 text-muted-foreground/60">
                  <DatabaseOutlined className="text-lg mb-2" />
                  <Text type="secondary" className="text-xs">
                    暂无可用的知识库
                  </Text>
                  <Text type="secondary" className="text-xs">
                    请先在知识库管理页面添加知识库
                  </Text>
                </div>
              ) : (
                <div className="space-y-2 max-h-64 overflow-y-auto">
                  {knowledgeBases.map((kb) => {
                    const isSelected = selectedKnowledgeBaseIds.includes(kb.id);
                    return (
                      <div
                        key={kb.id}
                        className={cn(
                          'flex items-center gap-2 p-2 rounded-lg cursor-pointer border transition-colors',
                          isSelected
                            ? 'bg-primary/5 border-primary/30'
                            : 'border-border/50 hover:bg-muted/50',
                          !kb.is_active && 'opacity-50',
                        )}
                        onClick={() => {
                          setSelectedKnowledgeBaseIds((prev) =>
                            isSelected
                              ? prev.filter((id) => id !== kb.id)
                              : [...prev, kb.id]
                          );
                        }}
                      >
                        <input
                          type="checkbox"
                          checked={isSelected}
                          readOnly
                          className="accent-primary"
                        />
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-1">
                            <Text className="text-xs font-medium truncate">{kb.name}</Text>
                            {!kb.is_active && (
                              <Tag className="text-[10px] leading-none px-1 py-0 ml-auto">已禁用</Tag>
                            )}
                          </div>
                          <Text type="secondary" className="text-[10px] block font-mono truncate">
                            {kb.dataset_id}
                          </Text>
                          {kb.description && (
                            <Text type="secondary" className="text-[10px] block truncate">
                              {kb.description}
                            </Text>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}

              <SaveButtonRow
                saving={savingSection === 'knowledge'}
                saved={savedSection === 'knowledge'}
                onSave={() => saveSection('knowledge', { knowledge_base_ids: selectedKnowledgeBaseIds })}
              />
            </div>
          )}

          {activeSection === 'skills' && (
            <>
            <div>
              <Text type="secondary" className="text-xs block mb-2">
                选择该智能体可使用的技能。Agent 会在对话中自动匹配并调用相关技能。
              </Text>

              {/* Selected skills */}
              {selectedSkillIds.length > 0 && (
                <>
                  <div className="flex items-center justify-between mb-2">
                    <Text type="secondary" className="text-xs font-semibold">
                      已绑定 ({selectedSkillIds.length})
                    </Text>
                    <div className="flex items-center gap-2">
                      <Button
                        size="small"
                        type="link"
                        className="!p-0 !text-xs"
                        onClick={() => setSelectedSkillIds(skills.map((s) => s.id))}
                      >
                        全选
                      </Button>
                      <Button
                        size="small"
                        type="link"
                        danger
                        className="!p-0 !text-xs"
                        onClick={() => setSelectedSkillIds([])}
                      >
                        清空
                      </Button>
                    </div>
                  </div>
                  <div className="space-y-1.5 mb-3 max-h-80 overflow-y-auto">
                    {selectedSkillIds.map((sid) => {
                      const skill = skills.find((s) => s.id === sid);
                      if (!skill) return null;
                      const rate = skill.use_count > 0 ? Math.round((skill.success_count / skill.use_count) * 100) : 0;
                      return (
                        <div
                          key={sid}
                          className="flex items-start gap-2.5 p-2.5 rounded-lg border border-primary/20 bg-primary/[0.03] hover:bg-primary/[0.06] transition-colors group"
                        >
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-1.5">
                              <ThunderboltOutlined className="text-amber-500 text-xs flex-shrink-0" />
                              <Text className="text-xs font-medium truncate">{skill.name}</Text>
                              <Tag className="!text-[9px] !px-1 !py-0 flex-shrink-0" color="blue">v{skill.version}</Tag>
                            </div>
                            {skill.description && (
                              <Text type="secondary" className="text-[11px] block mt-0.5 line-clamp-2 leading-relaxed">
                                {skill.description}
                              </Text>
                            )}
                            <div className="flex items-center gap-2 mt-1.5 flex-wrap">
                              {skill.category && (
                                <span className="text-[10px] text-muted-foreground bg-gray-100 dark:bg-gray-800 px-1.5 py-0.5 rounded-full">
                                  {SKILL_CATEGORY_REVERSE[skill.category] || skill.category}
                                </span>
                              )}
                              <span className="text-[10px] text-muted-foreground">
                                使用 {skill.use_count} 次
                              </span>
                              {skill.use_count > 0 && (
                                <span className={`text-[10px] ${rate >= 80 ? 'text-green-600' : rate >= 50 ? 'text-amber-600' : 'text-red-500'}`}>
                                  成功率 {rate}%
                                </span>
                              )}
                              {skill.files && skill.files.length > 0 && (
                                <span className="text-[10px] text-muted-foreground">
                                  {skill.files.length} 个文件
                                </span>
                              )}
                            </div>
                          </div>
                          <button
                            onClick={() => setSelectedSkillIds((prev) => prev.filter((id) => id !== sid))}
                            className="flex-shrink-0 w-5 h-5 flex items-center justify-center rounded text-muted-foreground hover:text-red-500 hover:bg-red-500/10 transition-colors opacity-0 group-hover:opacity-100 mt-0.5"
                          >
                            <CloseOutlined className="text-[10px]" />
                          </button>
                        </div>
                      );
                    })}
                  </div>
                </>
              )}

              {/* Add button */}
              <div className="mb-3">
                <Button
                  block
                  icon={<PlusOutlined />}
                  onClick={() => {
                    setSkillsDraft([...selectedSkillIds]);
                    setSkillsSearchQuery('');
                    setSkillsModalOpen(true);
                  }}
                  className="text-xs"
                >
                  添加技能
                </Button>
              </div>

              {selectedSkillIds.length === 0 && (
                <div className="py-4 text-center">
                  <ThunderboltOutlined className="text-lg text-muted-foreground/30" />
                  <Text type="secondary" className="text-xs block mt-1">暂未绑定技能</Text>
                  <Text type="secondary" className="text-[10px] block mt-0.5">绑定后 Agent 可自动调用相关技能</Text>
                </div>
              )}

              <SaveButtonRow
                saving={savingSection === 'skills'}
                saved={savedSection === 'skills'}
                onSave={() => saveSection('skills', { skill_ids: selectedSkillIds })}
              />
            </div>

            {/* Skills selection modal */}
            <Modal
              title={
                <div className="flex items-center gap-2">
                  <ThunderboltOutlined className="text-amber-500" />
                  <span className="font-semibold">选择技能</span>
                </div>
              }
              open={skillsModalOpen}
              onCancel={() => {
                setSkillsModalOpen(false);
                setSkillsSearchQuery('');
              }}
              onOk={() => {
                setSelectedSkillIds(skillsDraft);
                setSkillsModalOpen(false);
                setSkillsSearchQuery('');
              }}
              okText="确定"
              cancelText="取消"
              width={Math.min(1100, window.innerWidth - 80)}
              destroyOnClose
              styles={{ body: { maxHeight: '75vh', overflowY: 'auto', padding: '20px' } }}
            >
              <div className="flex items-center gap-3 mb-4">
                <div className="relative flex-1">
                  <SearchOutlined className="absolute left-3 top-1/2 -translate-y-1/2 text-sm text-muted-foreground/60" />
                  <input
                    type="text"
                    placeholder="搜索技能名称或描述..."
                    value={skillsSearchQuery}
                    onChange={(e) => setSkillsSearchQuery(e.target.value)}
                    className="w-full rounded-lg border border-border bg-background py-2 pl-9 pr-3 text-sm text-foreground outline-none transition placeholder:text-muted-foreground/50 focus:border-primary/40 focus:shadow-[0_0_0_3px_rgba(99,102,241,0.08)]"
                  />
                </div>
                <Text type="secondary" className="text-xs whitespace-nowrap">
                  已选 {skillsDraft.length} / 共 {skills.length}
                </Text>
                <Button
                  size="small"
                  type="link"
                  className="!p-0 !text-xs"
                  onClick={() => setSkillsDraft(skills.map((s) => s.id))}
                >
                  全选
                </Button>
                <Button
                  size="small"
                  type="link"
                  danger
                  className="!p-0 !text-xs"
                  disabled={skillsDraft.length === 0}
                  onClick={() => setSkillsDraft([])}
                >
                  清空
                </Button>
              </div>

              {(() => {
                const q = skillsSearchQuery.toLowerCase();
                const filtered = skills.filter((s) => {
                  if (!q) return true;
                  return (
                    s.name.toLowerCase().includes(q) ||
                    (s.description || '').toLowerCase().includes(q) ||
                    (s.tags || []).some((t: string) => t.toLowerCase().includes(q))
                  );
                });

                if (filtered.length === 0) {
                  return (
                    <div className="py-12 text-center">
                      <ThunderboltOutlined className="text-3xl text-muted-foreground/20 mb-3 block" />
                      <Text type="secondary" className="text-sm block">
                        {skillsSearchQuery ? '未找到匹配的技能' : '暂无可用的技能'}
                      </Text>
                    </div>
                  );
                }

                const selectedCount = filtered.filter((s) => skillsDraft.includes(s.id)).length;
                const allSelected = selectedCount === filtered.length;

                return (
                  <div>
                    <div className="flex items-center justify-between mb-3">
                      <Text type="secondary" className="text-sm font-semibold">
                        可用技能 ({filtered.length})
                        {selectedCount > 0 && (
                          <Text type="secondary" className="text-xs font-normal ml-1.5">
                            已选 {selectedCount}
                          </Text>
                        )}
                      </Text>
                      <Button
                        size="small"
                        type="link"
                        className="!p-0 !text-xs"
                        onClick={() => {
                          if (allSelected) {
                            setSkillsDraft((prev) => prev.filter((id) => !filtered.find((s) => s.id === id)));
                          } else {
                            const ids = filtered.map((s) => s.id);
                            setSkillsDraft((prev) => [...prev, ...ids.filter((id) => !prev.includes(id))]);
                          }
                        }}
                      >
                        {allSelected ? '取消全选' : '全选'}
                      </Button>
                    </div>

                    <div className="grid grid-cols-2 gap-2.5">
                      {filtered.map((skill) => {
                        const isSelected = skillsDraft.includes(skill.id);
                        const rate = skill.use_count > 0 ? Math.round((skill.success_count / skill.use_count) * 100) : 0;
                        return (
                          <div
                            key={skill.id}
                            className={`flex items-start gap-2.5 p-3 rounded-lg cursor-pointer border transition-colors ${
                              isSelected
                                ? 'bg-primary/5 border-primary/30 ring-1 ring-primary/10'
                                : 'border-border/50 hover:bg-muted/50'
                            }`}
                            onClick={() => {
                              if (isSelected) {
                                setSkillsDraft((prev) => prev.filter((id) => id !== skill.id));
                              } else {
                                setSkillsDraft((prev) => [...prev, skill.id]);
                              }
                            }}
                          >
                            <Checkbox checked={isSelected} className="mt-0.5" />
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center gap-1.5 flex-wrap">
                                <ThunderboltOutlined className="text-amber-500 text-xs" />
                                <Text className="text-sm font-medium truncate">{skill.name}</Text>
                                <Tag className="!text-[9px] !px-1 !py-0" color="blue">v{skill.version}</Tag>
                                {skill.is_public && (
                                  <Tag className="!text-[9px] !px-1 !py-0" color="green">公共</Tag>
                                )}
                              </div>
                              {skill.description && (
                                <Text type="secondary" className="text-xs block mt-1 line-clamp-2 leading-relaxed">
                                  {skill.description}
                                </Text>
                              )}
                              <div className="flex items-center gap-2 mt-1.5 flex-wrap">
                                <span className="text-[10px] text-muted-foreground bg-gray-100 dark:bg-gray-800 px-1.5 py-0.5 rounded-full">
                                  {SKILL_CATEGORY_REVERSE[skill.category] || skill.category}
                                </span>
                                <span className="text-[10px] text-muted-foreground">
                                  使用 {skill.use_count} 次
                                </span>
                                {skill.use_count > 0 && (
                                  <span className={`text-[10px] ${rate >= 80 ? 'text-green-600' : rate >= 50 ? 'text-amber-600' : 'text-red-500'}`}>
                                    成功率 {rate}%
                                  </span>
                                )}
                              </div>
                              {skill.tags && skill.tags.length > 0 && (
                                <div className="flex flex-wrap gap-1 mt-1.5">
                                  {skill.tags.slice(0, 3).map((t: string) => (
                                    <Tag key={t} className="!text-[9px] !px-1 !py-0 rounded-full">{t}</Tag>
                                  ))}
                                  {skill.tags.length > 3 && (
                                    <Text type="secondary" className="text-[9px]">+{skill.tags.length - 3}</Text>
                                  )}
                                </div>
                              )}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                );
              })()}
            </Modal>
            </>
          )}

          {activeSection === 'children' && (
            <div>
              <Text type="secondary" className="text-xs block mb-3">
                管理当前智能体的子级智能体。子智能体可被委派任务，实现多智能体协作。
              </Text>

              {/* Button to open child agent selection modal */}
              <Button
                block
                icon={<PlusOutlined />}
                onClick={() => {
                  setChildrenDraft([...selectedChildIds]);
                  setChildrenSearchQuery('');
                  setChildrenModalOpen(true);
                }}
              >
                添加子智能体
              </Button>

              {/* Child agent cards */}
              {selectedChildIds.length > 0 ? (
                <div className="mt-3 space-y-2">
                  <div className="flex items-center justify-between">
                    <Text className="text-xs font-medium">
                      已配置 {selectedChildIds.length} 个子智能体
                    </Text>
                    <Button
                      size="small"
                      type="link"
                      danger
                      className="!p-0 !text-xs !h-auto"
                      onClick={() => setSelectedChildIds([])}
                    >
                      全部移除
                    </Button>
                  </div>
                  {selectedChildIds.map((cid) => {
                    const child = allAgents.find((a) => a.id === cid);
                    if (!child) return null;
                    return (
                      <div
                        key={cid}
                        className="flex items-start gap-2 p-2 rounded-lg bg-muted/50 border border-border/50 group"
                      >
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-1.5">
                            <span className="text-sm flex-shrink-0">
                              {getAgentIcon(child.icon)}
                            </span>
                            <Text className="text-xs font-medium truncate">{child.name}</Text>
                            <Tag
                              color={child.is_active ? 'green' : 'default'}
                              className="text-[10px] leading-none flex-shrink-0"
                            >
                              {child.is_active ? '启用' : '禁用'}
                            </Tag>
                          </div>
                          {child.description && (
                            <Text type="secondary" className="text-xs block mt-0.5 line-clamp-2">
                              {child.description}
                            </Text>
                          )}
                          <div className="flex items-center gap-1.5 mt-1">
                            <Tag className="text-[10px] leading-none">
                              {child.model_name || '全局默认'}
                            </Tag>
                            <Tag className="text-[10px] leading-none">
                              {(() => {
                                const builtinNames = new Set(
                                  allTools.filter((t) => t.category !== 'mcp').map((t) => t.name),
                                );
                                return (child.enabled_tools || []).filter((t) => builtinNames.has(t)).length;
                              })()} 个工具
                            </Tag>
                          </div>
                          <div className="flex items-center gap-2 mt-2">
                            <Text className="text-[11px] text-muted-foreground flex-shrink-0">最大轮次</Text>
                            <InputNumber
                              min={1}
                              max={100}
                              step={1}
                              precision={0}
                              size="small"
                              className="w-full"
                              placeholder="默认"
                              value={childMaxIterations[cid] ?? undefined}
                              onChange={(v) =>
                                setChildMaxIterations((prev) => ({ ...prev, [cid]: v ?? null }))
                              }
                            />
                          </div>
                        </div>
                        <button
                          onClick={() =>
                            setSelectedChildIds((prev) => prev.filter((id) => id !== cid))
                          }
                          className="flex-shrink-0 w-5 h-5 flex items-center justify-center rounded text-muted-foreground hover:text-red-500 hover:bg-red-500/10 transition-colors opacity-0 group-hover:opacity-100"
                        >
                          <CloseOutlined className="text-xs" />
                        </button>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div className="mt-3 py-4 text-center">
                  <ApartmentOutlined className="text-lg text-muted-foreground/40" />
                  <Text type="secondary" className="text-xs block mt-1">暂未配置子智能体</Text>
                  <Text type="secondary" className="text-xs block mt-0.5">
                    添加后，可向子智能体委派任务
                  </Text>
                </div>
              )}

              <SaveButtonRow
                saving={savingSection === 'children'}
                saved={savedSection === 'children'}
                onSave={saveChildrenSection}
              />
            </div>
          )}

          {/* Children (sub-agents) selection modal */}
          <Modal
            title="选择子智能体"
            open={childrenModalOpen}
            onCancel={() => {
              setChildrenModalOpen(false);
              setChildrenSearchQuery('');
            }}
            onOk={() => {
              setSelectedChildIds(childrenDraft);
              setChildMaxIterations((prev) => {
                const next = { ...prev };
                Object.keys(next).forEach((id) => {
                  if (!childrenDraft.includes(id)) delete next[id];
                });
                return next;
              });
              setChildrenModalOpen(false);
              setChildrenSearchQuery('');
            }}
            okText="确定"
            cancelText="取消"
            width={1000}
            destroyOnClose
            styles={{ body: { maxHeight: '75vh', overflowY: 'auto' } }}
          >
            <div className="relative mb-3">
              <SearchOutlined className="absolute left-3 top-1/2 -translate-y-1/2 text-sm text-muted-foreground/60" />
              <input
                type="text"
                placeholder="搜索智能体名称或描述..."
                value={childrenSearchQuery}
                onChange={(e) => setChildrenSearchQuery(e.target.value)}
                className="w-full rounded-lg border border-border bg-background py-2 pl-9 pr-3 text-sm text-foreground outline-none transition placeholder:text-muted-foreground/50 focus:border-primary/40 focus:shadow-[0_0_0_3px_rgba(99,102,241,0.08)]"
              />
            </div>

            {(() => {
              const candidates = allAgents.filter((a) => {
                if (a.id === agentId) return false;
                if (a.parent_ids && a.parent_ids.includes(agentId)) return false;
                const q = childrenSearchQuery.toLowerCase();
                if (q) {
                  const nameMatch = a.name.toLowerCase().includes(q);
                  const descMatch = a.description?.toLowerCase().includes(q) ?? false;
                  if (!nameMatch && !descMatch) return false;
                }
                return true;
              });

              if (candidates.length === 0) {
                return (
                  <div className="py-8 text-center">
                    <ApartmentOutlined className="text-2xl text-muted-foreground/40 mb-2" />
                    <Text type="secondary" className="text-xs block">
                      {childrenSearchQuery ? '未找到匹配的智能体' : '暂无可选的智能体'}
                    </Text>
                  </div>
                );
              }

              const selectedCount = candidates.filter((a) => childrenDraft.includes(a.id)).length;
              const allSelected = selectedCount === candidates.length;

              return (
                <div>
                  <div className="sticky top-0 bg-background z-10 py-1.5 flex items-center justify-between">
                    <Text type="secondary" className="text-sm font-semibold">
                      可选智能体 ({candidates.length})
                      {selectedCount > 0 && (
                        <Text type="secondary" className="text-xs font-normal ml-1.5">
                          已选 {selectedCount}
                        </Text>
                      )}
                    </Text>
                    <Button
                      size="small"
                      type="link"
                      className="!p-0 !text-xs"
                      onClick={() => {
                        if (allSelected) {
                          setChildrenDraft((prev) =>
                            prev.filter((id) => !candidates.find((a) => a.id === id)),
                          );
                        } else {
                          const ids = candidates.map((a) => a.id);
                          setChildrenDraft((prev) => [...prev, ...ids.filter((id) => !prev.includes(id))]);
                        }
                      }}
                    >
                      {allSelected ? '取消全选' : '全选'}
                    </Button>
                  </div>
                  <div className="grid grid-cols-2 gap-2.5">
                    {candidates.map((agent) => {
                      const isSelected = childrenDraft.includes(agent.id);
                      const builtinNames = new Set(
                        allTools.filter((t) => t.category !== 'mcp').map((t) => t.name),
                      );
                      const toolCount = (agent.enabled_tools || []).filter((t) => builtinNames.has(t)).length;
                      return (
                        <div
                          key={agent.id}
                          className={`flex items-start gap-2 p-3 rounded-lg cursor-pointer border transition-colors ${
                            isSelected
                              ? 'bg-primary/5 border-primary/30'
                              : 'border-border/50 hover:bg-muted/50'
                          }`}
                        >
                          <Checkbox
                            checked={isSelected}
                            className="mt-0.5"
                            onClick={(e) => {
                              e.stopPropagation();
                              if (isSelected) {
                                setChildrenDraft((prev) => prev.filter((id) => id !== agent.id));
                              } else {
                                setChildrenDraft((prev) => [...prev, agent.id]);
                              }
                            }}
                          />
                          <div
                            className="flex-1 min-w-0"
                            onClick={() => {
                              setSelectedChildDetail(agent);
                              setChildrenDetailModalOpen(true);
                            }}
                          >
                            <div className="flex items-center gap-1.5 flex-wrap">
                              <span className="text-sm">{getAgentIcon(agent.icon)}</span>
                              <Text className="text-sm font-medium truncate">{agent.name}</Text>
                              <Tag
                                color={agent.is_active ? 'green' : 'default'}
                                className="text-[10px] leading-none"
                              >
                                {agent.is_active ? '启用' : '禁用'}
                              </Tag>
                            </div>
                            {agent.description && (
                              <Text type="secondary" className="text-xs block mt-1 line-clamp-2">
                                {agent.description}
                              </Text>
                            )}
                            <div className="flex items-center gap-1.5 mt-1.5 flex-wrap">
                              <Tag className="text-[10px] leading-none">
                                {agent.model_name || '全局默认'}
                              </Tag>
                              <Tag className="text-[10px] leading-none">
                                {toolCount} 个工具
                              </Tag>
                              {agent.knowledge_base_ids && agent.knowledge_base_ids.length > 0 && (
                                <Tag color="blue" className="text-[10px] leading-none">
                                  {agent.knowledge_base_ids.length} 个知识库
                                </Tag>
                              )}
                              {agent.skill_ids && agent.skill_ids.length > 0 && (
                                <Tag color="purple" className="text-[10px] leading-none">
                                  {agent.skill_ids.length} 个技能
                                </Tag>
                              )}
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              );
            })()}
          </Modal>

          {/* Child agent detail modal */}
          <Modal
            title="智能体详情"
            open={childrenDetailModalOpen}
            onCancel={() => {
              setChildrenDetailModalOpen(false);
              setSelectedChildDetail(null);
            }}
            footer={null}
            width={800}
            destroyOnClose
            styles={{ body: { maxHeight: '70vh', overflowY: 'auto' } }}
          >
            {selectedChildDetail && (
              <div className="space-y-4">
                <div className="flex items-center gap-3">
                  <span className="text-2xl">{getAgentIcon(selectedChildDetail.icon)}</span>
                  <div>
                    <Text className="text-lg font-semibold block">{selectedChildDetail.name}</Text>
                    <div className="flex items-center gap-2 mt-0.5">
                      <Tag
                        color={selectedChildDetail.is_active ? 'green' : 'default'}
                        className="text-xs leading-none"
                      >
                        {selectedChildDetail.is_active ? '启用' : '禁用'}
                      </Tag>
                      <Text type="secondary" className="text-xs">
                        {selectedChildDetail.model_name || '全局默认模型'}
                      </Text>
                    </div>
                  </div>
                </div>

                {selectedChildDetail.description && (
                  <div>
                    <Text type="secondary" className="text-xs font-medium block mb-1">描述</Text>
                    <Text className="text-sm">{selectedChildDetail.description}</Text>
                  </div>
                )}

                <div>
                  <Text type="secondary" className="text-xs font-medium block mb-2">
                    可用工具 ({(() => {
                      const names = new Set(
                        allTools.filter((t) => t.category !== 'mcp').map((t) => t.name),
                      );
                      return (selectedChildDetail.enabled_tools || []).filter((t) => names.has(t)).length;
                    })()})
                  </Text>
                  {selectedChildDetail.enabled_tools?.length > 0 ? (
                    <div className="flex flex-wrap gap-1.5">
                      {selectedChildDetail.enabled_tools.map((t) => (
                        <Tag key={t} className="text-xs">{t}</Tag>
                      ))}
                    </div>
                  ) : (
                    <Text type="secondary" className="text-xs">未配置工具</Text>
                  )}
                </div>

                <div>
                  <Text type="secondary" className="text-xs font-medium block mb-2">
                    知识库 ({(selectedChildDetail.knowledge_base_ids || []).length})
                  </Text>
                  {selectedChildDetail.knowledge_base_ids?.length > 0 ? (
                    <Text className="text-xs">
                      已绑定 {selectedChildDetail.knowledge_base_ids.length} 个知识库
                    </Text>
                  ) : (
                    <Text type="secondary" className="text-xs">未绑定知识库</Text>
                  )}
                </div>

                <div>
                  <Text type="secondary" className="text-xs font-medium block mb-2">
                    技能 ({(selectedChildDetail.skill_ids || []).length})
                  </Text>
                  {selectedChildDetail.skill_ids?.length > 0 ? (
                    <Text className="text-xs">
                      已绑定 {selectedChildDetail.skill_ids.length} 个技能
                    </Text>
                  ) : (
                    <Text type="secondary" className="text-xs">未绑定技能</Text>
                  )}
                </div>

                {selectedChildDetail.system_prompt && (
                  <div>
                    <Text type="secondary" className="text-xs font-medium block mb-1">系统提示词</Text>
                    <div className="p-3 rounded-lg bg-muted/50 border border-border/50 max-h-48 overflow-y-auto">
                      <Text className="text-xs whitespace-pre-wrap">
                        {selectedChildDetail.system_prompt}
                      </Text>
                    </div>
                  </div>
                )}
              </div>
            )}
          </Modal>
        </div>
      </>
    );
  };

  return (
    <div
      ref={sidebarRef}
      className={cn(
        'flex flex-shrink-0 border-r border-border bg-card overflow-hidden transition-all duration-200 ease-in-out',
        sidebarExpanded && activeSection ? 'w-[340px]' : 'w-11',
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
            <DisconnectOutlined />
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
            ? renderHistoryPanel()
            : activeSection === 'overview'
              ? renderOverviewPanel()
              : renderAdminPanel()}
        </div>
      )}
    </div>
  );
}

// Session section component
interface SessionSectionProps {
  title: string;
  icon?: React.ReactNode;
  sessions: Session[];
  activeSessionId: string | null;
  editingId: string | null;
  editValue: string;
  onEditValueChange: (v: string) => void;
  onSelect: (id: string) => void;
  onStartRename: (id: string, title: string) => void;
  onFinishRename: (id: string) => void;
  onCancelRename: () => void;
  onDelete: (id: string) => void;
  onPin: (id: string, currentPinned: boolean) => void;
  onArchive: (id: string, currentArchived: boolean) => void;
}

function SessionSection({
  title,
  icon,
  sessions,
  activeSessionId,
  editingId,
  editValue,
  onEditValueChange,
  onSelect,
  onStartRename,
  onFinishRename,
  onCancelRename,
  onDelete,
  onPin,
  onArchive,
}: SessionSectionProps) {
  return (
    <div className="mb-1">
      {title && (
        <div className="flex items-center gap-1.5 px-3 py-1.5">
          {icon && <span className="text-muted-foreground">{icon}</span>}
          <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            {title}
          </span>
        </div>
      )}
      <div className="space-y-0.5">
        {sessions.map((session) => (
          <SessionItem
            key={session.id}
            session={session}
            isActive={activeSessionId === session.id}
            isEditing={editingId === session.id}
            editValue={editValue}
            onEditValueChange={onEditValueChange}
            onSelect={() => onSelect(session.id)}
            onStartRename={() => onStartRename(session.id, session.title || '')}
            onFinishRename={() => onFinishRename(session.id)}
            onCancelRename={onCancelRename}
            onDelete={() => onDelete(session.id)}
            onPin={() => onPin(session.id, session.is_pinned)}
            onArchive={() => onArchive(session.id, session.is_archived)}
          />
        ))}
      </div>
    </div>
  );
}

// Session item component
interface SessionItemProps {
  session: Session;
  isActive: boolean;
  isEditing: boolean;
  editValue: string;
  onEditValueChange: (v: string) => void;
  onSelect: () => void;
  onStartRename: () => void;
  onFinishRename: () => void;
  onCancelRename: () => void;
  onDelete: () => void;
  onPin: () => void;
  onArchive: () => void;
}

function SessionItem({
  session,
  isActive,
  isEditing,
  editValue,
  onEditValueChange,
  onSelect,
  onStartRename,
  onFinishRename,
  onCancelRename,
  onDelete,
  onPin,
  onArchive,
}: SessionItemProps) {
  const menuItems: MenuProps['items'] = [
    {
      key: 'rename',
      icon: <EditOutlined />,
      label: '重命名',
      onClick: onStartRename,
    },
    {
      key: 'pin',
      icon: session.is_pinned ? <PushpinOutlined /> : <PushpinFilled />,
      label: session.is_pinned ? '取消置顶' : '置顶',
      onClick: onPin,
    },
    {
      key: 'archive',
      icon: session.is_archived ? <UndoOutlined /> : <InboxOutlined />,
      label: session.is_archived ? '取消归档' : '归档',
      onClick: onArchive,
    },
    { type: 'divider' },
    {
      key: 'delete',
      icon: <DeleteOutlined />,
      label: '删除',
      danger: true,
      onClick: onDelete,
    },
  ];

  return (
    <div
      onClick={onSelect}
      className={cn(
        'group relative flex cursor-pointer items-start gap-2.5 rounded-lg px-3 py-2.5 text-sm transition-all',
        isActive
          ? 'bg-muted/60 text-foreground shadow-[0_1px_4px_rgba(0,0,0,0.06),0_0_0_1px_rgba(99,102,241,0.12)]'
          : 'text-muted-foreground hover:bg-muted/40 hover:text-foreground',
      )}
    >
      <MessageOutlined
        className={cn(
          'mt-0.5 flex-shrink-0 text-xs transition',
          isActive ? 'text-primary' : 'text-muted-foreground/40 group-hover:text-muted-foreground',
        )}
      />

      {isEditing ? (
        <div className="flex flex-1 items-center gap-1" onClick={(e) => e.stopPropagation()}>
          <Input
            size="small"
            value={editValue}
            onChange={(e) => onEditValueChange(e.target.value)}
            onPressEnter={onFinishRename}
            onKeyDown={(e) => {
              if (e.key === 'Escape') onCancelRename();
            }}
            autoFocus
          />
          <button
            onClick={onFinishRename}
            className="flex h-6 w-6 items-center justify-center rounded text-green-500 transition hover:bg-green-500/10"
          >
            <CheckOutlined className="text-xs" />
          </button>
          <button
            onClick={onCancelRename}
            className="flex h-6 w-6 items-center justify-center rounded text-muted-foreground transition hover:bg-muted"
          >
            <CloseOutlined className="text-xs" />
          </button>
        </div>
      ) : (
        <>
          <div className="flex-1 overflow-hidden">
            <div
              className={cn(
                'truncate text-sm leading-snug',
                isActive && 'font-medium text-primary',
              )}
            >
              {session.title || '无标题'}
            </div>
            <div className="mt-0.5 truncate text-[10px] text-muted-foreground/60">
              {formatRelativeTime(session.updated_at)}
            </div>
          </div>

          <Dropdown menu={{ items: menuItems }} trigger={['click']}>
            <button
              onClick={(e) => e.stopPropagation()}
              className="mt-0.5 flex h-6 w-6 flex-shrink-0 items-center justify-center rounded text-muted-foreground opacity-0 transition hover:bg-muted hover:text-foreground group-hover:opacity-100"
            >
              <MoreOutlined className="text-xs" />
            </button>
          </Dropdown>
        </>
      )}
    </div>
  );
}

// Save button row component
function SaveButtonRow({
  saving,
  saved,
  onSave,
}: {
  saving: boolean;
  saved: boolean;
  onSave: () => void;
}) {
  return (
    <div className="flex items-center gap-2 mt-4">
      <Button
        type="primary"
        size="small"
        icon={<SaveOutlined />}
        loading={saving}
        onClick={onSave}
      >
        保存
      </Button>
      {saved && (
        <span className="text-green-500 text-xs flex items-center gap-1">
          <CheckCircleFilled /> 已保存
        </span>
      )}
    </div>
  );
}
