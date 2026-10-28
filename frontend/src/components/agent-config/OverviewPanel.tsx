import { useState, useEffect } from 'react';
import { ToolOutlined, ThunderboltOutlined, CloseOutlined, DashboardOutlined, ApiOutlined, DatabaseOutlined, ApartmentOutlined } from '@ant-design/icons';
import { Typography, Spin } from 'antd';
import { agentsApi } from '@/lib/api';
import { formatRelativeTime, cn } from '@/lib/utils';
import type { AgentStats } from '@/lib/types';
import { getAgentIcon } from '@/lib/agent-icons';
import { useAgentConfig } from './context';
import type { SectionKey } from './types';

const { Text } = Typography;

export function OverviewPanel({ closePanel, setActiveSection }: { closePanel: () => void; setActiveSection: (key: SectionKey) => void; }) {
  const { agentId, agent, loading, allTools } = useAgentConfig();
  const [agentStats, setAgentStats] = useState<AgentStats | null>(null);
  useEffect(() => {
    let cancelled = false;
    agentsApi.stats(agentId).then((stats) => { if (!cancelled) setAgentStats(stats); }).catch(() => { });
    return () => { cancelled = true; };
  }, [agentId]);
  return (
    <>
      <div className="flex items-center justify-between border-b border-border/50 px-5 py-4">
        <div className="flex items-center gap-2">
          <DashboardOutlined className="text-primary" />
          <Text strong>智能体概览</Text>
        </div>
        <button
          onClick={closePanel}
          className="flex h-6 w-6 items-center justify-center rounded text-muted-foreground transition hover:bg-muted hover:text-foreground"
        >
          <CloseOutlined className="text-xs" />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {loading && !agent ? (
          <div className="flex items-center justify-center py-16">
            <Spin />
          </div>
        ) : agent ? (
          <div className="space-y-4">
            <div className="rounded-2xl border border-border/60 bg-primary/[0.06] p-4">
              <div className="flex items-start gap-3">
                <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-2xl">
                  {getAgentIcon(agent.icon)}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <Text strong className="truncate text-base">{agent.name}</Text>
                    <span
                      className={cn(
                        'h-2 w-2 shrink-0 rounded-full',
                        agent.is_active ? 'bg-emerald-500' : 'bg-muted-foreground/40',
                      )}
                    />
                  </div>
                  <Text type="secondary" className="mt-0.5 block truncate text-xs">
                    {agent.model_name || '全局默认模型'}
                  </Text>
                  {agent.description && (
                    <Text type="secondary" className="mt-2 block line-clamp-2 text-xs leading-5">
                      {agent.description}
                    </Text>
                  )}
                </div>
              </div>
            </div>

            <div className="grid grid-cols-3 divide-x divide-border/60 rounded-xl border border-border/60 bg-background py-3">
              <div className="text-center">
                <div className="text-base font-semibold tabular-nums text-foreground">{agentStats?.total_sessions ?? 0}</div>
                <div className="mt-0.5 text-[11px] text-muted-foreground">会话</div>
              </div>
              <div className="text-center">
                <div className="text-base font-semibold tabular-nums text-foreground">{agentStats?.total_messages ?? 0}</div>
                <div className="mt-0.5 text-[11px] text-muted-foreground">消息</div>
              </div>
              <div className="text-center">
                <div className="truncate px-1 text-sm font-semibold text-foreground">
                  {agentStats?.last_active_at ? formatRelativeTime(agentStats.last_active_at) : '-'}
                </div>
                <div className="mt-0.5 text-[11px] text-muted-foreground">活跃</div>
              </div>
            </div>

            <div>
              <div className="mb-2 px-1 text-xs font-medium text-muted-foreground">配置</div>
              <div className="grid grid-cols-2 gap-2">
                {[
                  { key: 'children' as SectionKey, label: '子智能体', value: agent.children_count ?? 0, icon: <ApartmentOutlined /> },
                  { key: 'skills' as SectionKey, label: '技能', value: agent.skill_ids?.length ?? 0, icon: <ThunderboltOutlined /> },
                  { key: 'tools' as SectionKey, label: '工具', value: (agent.enabled_tools || []).filter((tool) => allTools.some((item) => item.category !== 'mcp' && item.name === tool)).length || '全部', icon: <ToolOutlined /> },
                  { key: 'mcp' as SectionKey, label: 'MCP', value: agent.mcp_server_ids?.length ?? 0, icon: <ApiOutlined /> },
                  { key: 'knowledge' as SectionKey, label: '知识库', value: agent.knowledge_base_ids?.length ?? 0, icon: <DatabaseOutlined /> },
                  { key: 'graph-knowledge' as SectionKey, label: '知识图谱', value: agent.graph_knowledge_base_ids?.length ?? 0, icon: <ApartmentOutlined /> },
                ].map((item) => (
                  <button
                    key={item.key}
                    onClick={() => setActiveSection(item.key)}
                    className="group flex items-center gap-2.5 rounded-xl border border-border/60 bg-background px-3 py-2.5 text-left transition hover:border-primary/30 hover:bg-primary/[0.04]"
                  >
                    <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-muted text-xs text-muted-foreground transition group-hover:bg-primary/10 group-hover:text-primary">
                      {item.icon}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-xs text-foreground">{item.label}</span>
                    <span className="text-xs font-semibold tabular-nums text-muted-foreground">{item.value}</span>
                  </button>
                ))}
              </div>
            </div>

            <button
              onClick={() => setActiveSection('prompt')}
              className="flex w-full items-center justify-between rounded-xl bg-muted/45 px-3 py-2.5 text-xs transition hover:bg-muted"
            >
              <span className="text-muted-foreground">运行设置</span>
              <span className="flex items-center gap-2 text-foreground">
                {agent.enable_memory_extraction !== false && <span>记忆</span>}
                {agent.enable_retry !== false && <span>重试</span>}
                {agent.enable_auto_title !== false && <span>标题</span>}
                <span className="text-muted-foreground">›</span>
              </span>
            </button>
          </div>
        ) : (
          <div className="py-16 text-center text-xs text-muted-foreground">
            暂无智能体信息
          </div>
        )}
      </div>
    </>
  );

}
