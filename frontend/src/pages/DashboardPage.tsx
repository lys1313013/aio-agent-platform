import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  MessageOutlined,
  BulbOutlined,
  RobotOutlined,
  ArrowRightOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  SyncOutlined,
  FieldTimeOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { Card, Row, Col, Tag, Typography, Skeleton, Empty } from 'antd';
import { useChatStore } from '@/stores/chatStore';
import { useAuthStore } from '@/stores/authStore';
import {
  sessionsApi,
  agentsApi,
  cronJobsApi,
  analyticsApi,
  memoriesApi,
  skillsApi,
  type AnalyticsSummary,
} from '@/lib/api';
import type { Agent, CronJob } from '@/lib/types';
import { getAgentIcon, DEFAULT_ICON } from '@/lib/agent-icons';
import { cn } from '@/lib/utils';

const { Text } = Typography;

// ---- Helpers ----

function getGreeting(): string {
  const h = new Date().getHours();
  if (h < 6) return '夜深了';
  if (h < 12) return '早上好';
  if (h < 14) return '中午好';
  if (h < 18) return '下午好';
  return '晚上好';
}

function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

// ---- Types ----

interface SystemStatus {
  api: 'online' | 'offline' | 'checking';
}

// ---- Status Badge ----

function StatusBadge({ status }: { status: 'online' | 'offline' | 'checking' }) {
  if (status === 'checking') {
    return (
      <Tag icon={<SyncOutlined spin />} color="processing">
        检测中
      </Tag>
    );
  }
  if (status === 'online') {
    return (
      <Tag icon={<CheckCircleOutlined />} color="success">
        正常
      </Tag>
    );
  }
  return (
    <Tag icon={<CloseCircleOutlined />} color="error">
      异常
    </Tag>
  );
}

// ---- Stat Card ----

interface StatCardProps {
  title: string;
  value: number | null;
  icon: React.ReactNode;
  color: string;
  bgColor: string;
  suffix?: string;
  onClick?: () => void;
}

function StatCard({ title, value, icon, color, bgColor, suffix, onClick }: StatCardProps) {
  return (
    <Card
      hoverable={!!onClick}
      onClick={onClick}
      className={cn(
        'group transition-all duration-200 border-border',
        onClick && 'cursor-pointer hover:shadow-md hover:-translate-y-0.5',
      )}
      styles={{ body: { padding: '16px 20px' } }}
    >
      <div className="flex items-center gap-3">
        <div
          className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-lg transition-transform group-hover:scale-110"
          style={{ backgroundColor: bgColor, color }}
        >
          {icon}
        </div>
        <div className="min-w-0 flex-1">
          <Text type="secondary" className="text-xs">
            {title}
          </Text>
          <div className="flex items-baseline gap-1.5">
            {value !== null ? (
              <span className="text-xl font-bold tracking-tight">{value}</span>
            ) : (
              <Skeleton.Input active size="small" className="!w-10" />
            )}
            {suffix && (
              <Text type="secondary" className="text-xs">
                {suffix}
              </Text>
            )}
          </div>
        </div>
        {onClick && (
          <ArrowRightOutlined className="text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
        )}
      </div>
    </Card>
  );
}

// ---- Section Header ----

function SectionHeader({
  icon,
  title,
  extra,
}: {
  icon: React.ReactNode;
  title: string;
  extra?: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between mb-3">
      <div className="flex items-center gap-2">
        <span className="text-primary">{icon}</span>
        <Text strong className="text-sm">
          {title}
        </Text>
      </div>
      {extra}
    </div>
  );
}

// ---- Agent Card ----

function AgentCard({ agent, onClick }: { agent: Agent; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'flex w-full items-center gap-3 rounded-xl border border-border bg-card p-3.5 text-left',
        'transition-all hover:shadow-md hover:-translate-y-0.5 hover:border-primary/30 group',
        !agent.is_active && 'opacity-50',
      )}
    >
      <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
        {getAgentIcon(agent.icon || DEFAULT_ICON, undefined, 20)}
      </div>
      <div className="min-w-0 flex-1">
        <div className="font-medium text-sm text-foreground truncate">{agent.name}</div>
        {agent.description && (
          <div className="text-xs text-muted-foreground truncate">{agent.description}</div>
        )}
      </div>
      <ArrowRightOutlined className="text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 shrink-0" />
    </button>
  );
}

// ---- Cron Job Row ----

function CronJobRow({ job, agentName }: { job: CronJob; agentName?: string }) {
  return (
    <div className="flex items-center justify-between py-2.5 border-b border-border last:border-0">
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium truncate">{job.name}</div>
        <Text type="secondary" className="text-xs">
          {agentName || '未绑定智能体'}
          {job.cron_expr ? ` · ${job.cron_expr}` : job.run_at ? ' · 单次任务' : ''}
        </Text>
      </div>
      <Tag color={job.is_active ? 'success' : 'default'} className="shrink-0 ml-3">
        {job.is_active ? '启用' : '停用'}
      </Tag>
    </div>
  );
}

// ---- Main Component ----

export default function DashboardPage() {
  const navigate = useNavigate();
  const { sessions } = useChatStore();
  const username = useAuthStore((s) => s.username);

  const [agents, setAgents] = useState<Agent[]>([]);
  const [cronJobs, setCronJobs] = useState<CronJob[]>([]);
  const [summary, setSummary] = useState<AnalyticsSummary | null>(null);
  const [memoryCount, setMemoryCount] = useState<number | null>(null);
  const [skillCount, setSkillCount] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [systemStatus, setSystemStatus] = useState<SystemStatus>({ api: 'checking' });

  useEffect(() => {
    let cancelled = false;

    async function fetchData() {
      const today = new Date();
      const start = today.toISOString().slice(0, 10);

      const [agentsRes, cronRes, summaryRes, memoriesRes, skillsRes] = await Promise.allSettled([
        agentsApi.list(),
        cronJobsApi.list({ limit: 5 }),
        analyticsApi.summary({ start, scope: 'mine' }),
        memoriesApi.list({ limit: 1 }),
        skillsApi.list({ limit: 1 }),
      ]);

      if (cancelled) return;

      if (agentsRes.status === 'fulfilled') setAgents(agentsRes.value);
      if (cronRes.status === 'fulfilled') setCronJobs(cronRes.value.items);
      if (summaryRes.status === 'fulfilled') setSummary(summaryRes.value);
      if (memoriesRes.status === 'fulfilled') setMemoryCount(memoriesRes.value.total);
      if (skillsRes.status === 'fulfilled') setSkillCount(skillsRes.value.total);
      setLoading(false);
    }

    fetchData();
    return () => {
      cancelled = true;
    };
  }, []);

  // System health check
  const checkHealth = useCallback(async () => {
    setSystemStatus({ api: 'checking' });
    try {
      await sessionsApi.list();
      setSystemStatus({ api: 'online' });
    } catch {
      setSystemStatus({ api: 'offline' });
    }
  }, []);

  useEffect(() => {
    checkHealth();
  }, [checkHealth]);

  // Today's sessions from local store
  const todayStart = new Date();
  todayStart.setHours(0, 0, 0, 0);
  const todaySessions = sessions.filter(
    (s) => new Date(s.updated_at).getTime() >= todayStart.getTime(),
  ).length;

  // Agents the user can chat with: active first
  const chatAgents = agents.filter((a) => a.is_active).slice(0, 6);
  const activeCronCount = cronJobs.filter((j) => j.is_active).length;
  const activeAgentCount = agents.filter((a) => a.is_active).length;

  const agentNameById = new Map(agents.map((a) => [a.id, a.name]));

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="w-full px-6 py-8 max-w-5xl">
        {/* Header */}
        <div className="mb-6">
          <h1 className="text-2xl font-bold mb-1">
            {getGreeting()}{username ? `，${username}` : ''}
          </h1>
          <Text type="secondary">
            {todaySessions > 0 || (summary?.sessions ?? 0) > 0
              ? `今日 ${todaySessions} 次对话 · ${formatNumber(summary?.total_tokens ?? 0)} tokens`
              : '开始今天的第一次对话吧'}
          </Text>
        </div>

        {/* Stats grid */}
        <Row gutter={[16, 16]} className="mb-6">
          <Col xs={12} sm={6}>
            <StatCard
              title="对话总数"
              value={sessions.length}
              icon={<MessageOutlined />}
              color="#3b82f6"
              bgColor="#3b82f620"
              onClick={() => navigate('/chat')}
            />
          </Col>
          <Col xs={12} sm={6}>
            <StatCard
              title="记忆条数"
              value={memoryCount}
              icon={<BulbOutlined />}
              color="#a855f7"
              bgColor="#a855f720"
              onClick={() => navigate('/memory')}
            />
          </Col>
          <Col xs={12} sm={6}>
            <StatCard
              title="技能数"
              value={skillCount}
              icon={<ThunderboltOutlined />}
              color="#eab308"
              bgColor="#eab30820"
              onClick={() => navigate('/skills')}
            />
          </Col>
          <Col xs={12} sm={6}>
            <StatCard
              title="智能体"
              value={agents.length}
              icon={<RobotOutlined />}
              color="#22c55e"
              bgColor="#22c55e20"
              suffix={`${activeAgentCount} 个活跃`}
              onClick={() => navigate('/agents')}
            />
          </Col>
        </Row>

        <Row gutter={[16, 16]}>
          {/* Main column */}
          <Col xs={24} lg={15}>
            {/* Agents */}
            <div className="mb-6">
              <SectionHeader
                icon={<RobotOutlined />}
                title="开始对话"
                extra={
                  <button
                    onClick={() => navigate('/agents')}
                    className="text-xs text-primary hover:text-primary/80 transition-colors"
                  >
                    全部智能体 →
                  </button>
                }
              />
              {loading ? (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  {[0, 1, 2, 3].map((i) => (
                    <Skeleton key={i} active avatar paragraph={{ rows: 1 }} />
                  ))}
                </div>
              ) : chatAgents.length === 0 ? (
                <Card styles={{ body: { padding: '24px' } }}>
                  <Empty
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                    description="暂无可用智能体"
                  >
                    <button
                      onClick={() => navigate('/agents')}
                      className="text-primary text-sm hover:text-primary/80 transition-colors"
                    >
                      去创建智能体 →
                    </button>
                  </Empty>
                </Card>
              ) : (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  {chatAgents.map((agent) => (
                    <AgentCard
                      key={agent.id}
                      agent={agent}
                      onClick={() => navigate(`/agents/${agent.id}/chat`)}
                    />
                  ))}
                </div>
              )}
            </div>

            {/* Cron jobs */}
            <div>
              <SectionHeader
                icon={<FieldTimeOutlined />}
                title="定时任务"
                extra={
                  <button
                    onClick={() => navigate('/cron-jobs')}
                    className="text-xs text-primary hover:text-primary/80 transition-colors"
                  >
                    管理 →
                  </button>
                }
              />
              <Card styles={{ body: { padding: '8px 20px' } }}>
                {loading ? (
                  <Skeleton active paragraph={{ rows: 2 }} title={false} />
                ) : cronJobs.length === 0 ? (
                  <div className="py-4 text-center">
                    <Text type="secondary" className="text-sm">
                      暂无定时任务
                    </Text>
                  </div>
                ) : (
                  cronJobs.map((job) => (
                    <CronJobRow
                      key={job.id}
                      job={job}
                      agentName={job.agent_id ? agentNameById.get(job.agent_id) : undefined}
                    />
                  ))
                )}
              </Card>
            </div>
          </Col>

          {/* Side column */}
          <Col xs={24} lg={9}>
            {/* Today's stats */}
            <Card className="mb-4" styles={{ body: { padding: '16px 20px' } }}>
              <div className="flex items-center gap-2 mb-3">
                <ThunderboltOutlined className="text-primary" />
                <Text strong className="text-sm">
                  今日概览
                </Text>
              </div>
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <Text type="secondary" className="text-xs">对话</Text>
                  <span className="text-lg font-semibold">{todaySessions}</span>
                </div>
                <div className="flex items-center justify-between">
                  <Text type="secondary" className="text-xs">Tokens</Text>
                  <span className="text-lg font-semibold">
                    {summary ? formatNumber(summary.total_tokens) : '—'}
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <Text type="secondary" className="text-xs">启用中的定时任务</Text>
                  <span className="text-lg font-semibold">{activeCronCount}</span>
                </div>
              </div>
            </Card>

            {/* System status */}
            <Card styles={{ body: { padding: '16px 20px' } }}>
              <div className="flex items-center gap-2 mb-3">
                <CheckCircleOutlined className="text-primary" />
                <Text strong className="text-sm">
                  系统状态
                </Text>
              </div>
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <Text type="secondary" className="text-xs">API 服务</Text>
                  <StatusBadge status={systemStatus.api} />
                </div>
                <div className="flex items-center justify-between">
                  <Text type="secondary" className="text-xs">智能体</Text>
                  <Tag color={agents.length > 0 ? 'success' : 'warning'}>
                    {agents.length > 0 ? `${agents.length} 个可用` : '未配置'}
                  </Tag>
                </div>
              </div>
            </Card>
          </Col>
        </Row>
      </div>
    </div>
  );
}
