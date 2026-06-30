import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  MessageOutlined,
  BulbOutlined,
  ThunderboltOutlined,
  RobotOutlined,
  ClockCircleOutlined,
  ArrowRightOutlined,
  PlusOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  SyncOutlined,
} from '@ant-design/icons';
import { Card, Row, Col, List, Tag, Typography, Skeleton } from 'antd';
import { useChatStore } from '@/stores/chatStore';
import { memoriesApi, skillsApi, agentsApi, sessionsApi } from '@/lib/api';
import type { Agent } from '@/lib/types';
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

function formatRelativeTime(dateStr: string): string {
  const now = Date.now();
  const then = new Date(dateStr).getTime();
  const diff = now - then;
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return '刚刚';
  if (mins < 60) return `${mins} 分钟前`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days} 天前`;
  return new Date(dateStr).toLocaleDateString();
}

// ---- Types ----

interface DashboardStats {
  sessions: number;
  pinned: number;
  memories: number | null;
  skills: number | null;
  agents: number | null;
}

interface SystemStatus {
  api: 'online' | 'offline' | 'checking';
  database: 'online' | 'offline' | 'checking';
}

// ---- Stat Card Component ----

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
      styles={{ body: { padding: '20px 24px' } }}
    >
      <div className="flex items-center gap-4">
        <div
          className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl text-xl transition-transform group-hover:scale-110"
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
              <span className="text-2xl font-bold tracking-tight">{value}</span>
            ) : (
              <Skeleton.Input active size="small" className="!w-12" />
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

// ---- Quick Action ----

interface QuickActionProps {
  icon: React.ReactNode;
  label: string;
  description: string;
  onClick: () => void;
}

function QuickAction({ icon, label, description, onClick }: QuickActionProps) {
  return (
    <button
      onClick={onClick}
      className="flex w-full items-center gap-3 rounded-xl border border-border bg-card p-4 text-left transition-all hover:shadow-md hover:-translate-y-0.5 hover:border-primary/30 group"
    >
      <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary transition-colors group-hover:bg-primary group-hover:text-primary-foreground">
        {icon}
      </div>
      <div className="min-w-0 flex-1">
        <div className="font-medium text-sm text-foreground">{label}</div>
        <div className="text-xs text-muted-foreground truncate">{description}</div>
      </div>
      <ArrowRightOutlined className="text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
    </button>
  );
}

// ---- Main Component ----

export default function DashboardPage() {
  const navigate = useNavigate();
  const { sessions } = useChatStore();

  const [stats, setStats] = useState<DashboardStats>({
    sessions: sessions.length,
    pinned: sessions.filter((s) => s.is_pinned).length,
    memories: null,
    skills: null,
    agents: null,
  });
  const [agents, setAgents] = useState<Agent[]>([]);
  const [systemStatus, setSystemStatus] = useState<SystemStatus>({
    api: 'checking',
    database: 'checking',
  });

  // Fetch dashboard data
  useEffect(() => {
    let cancelled = false;

    async function fetchDashboardData() {
      try {
        const [memoriesRes, skillsRes, agentsRes] = await Promise.allSettled([
          memoriesApi.list({ limit: 1 }),
          skillsApi.list({ limit: 1 }),
          agentsApi.list(),
        ]);

        if (cancelled) return;

        setStats((prev) => ({
          ...prev,
          memories:
            memoriesRes.status === 'fulfilled' ? memoriesRes.value.total : 0,
          skills:
            skillsRes.status === 'fulfilled' ? skillsRes.value.total : 0,
          agents:
            agentsRes.status === 'fulfilled' ? agentsRes.value.length : 0,
        }));

        if (agentsRes.status === 'fulfilled') {
          setAgents(agentsRes.value);
        }
      } catch {
        /* individual errors handled per-stat */
      }
    }

    fetchDashboardData();
    return () => {
      cancelled = true;
    };
  }, []);

  // Update session stats from store
  useEffect(() => {
    setStats((prev) => ({
      ...prev,
      sessions: sessions.length,
      pinned: sessions.filter((s) => s.is_pinned).length,
    }));
  }, [sessions]);

  // System health check
  const checkHealth = useCallback(async () => {
    setSystemStatus({ api: 'checking', database: 'checking' });
    try {
      await sessionsApi.list();
      setSystemStatus({ api: 'online', database: 'online' });
    } catch {
      setSystemStatus({ api: 'offline', database: 'offline' });
    }
  }, []);

  useEffect(() => {
    checkHealth();
  }, [checkHealth]);

  // Recent sessions sorted by updated_at
  const recentSessions = [...sessions]
    .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime())
    .slice(0, 5);

  // Active agents (for the "active agent" stat)
  const activeAgents = agents.filter((a) => a.is_active);

  // Today's sessions
  const todayStart = new Date();
  todayStart.setHours(0, 0, 0, 0);
  const todaySessions = sessions.filter(
    (s) => new Date(s.updated_at).getTime() >= todayStart.getTime(),
  ).length;

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="w-full px-6 py-8">
        {/* Header */}
        <div className="mb-8">
          <h1 className="text-2xl font-bold mb-1">
            {getGreeting()}，欢迎回来
          </h1>
          <Text type="secondary">
            查看您的 Agent 活动和使用统计概览。
          </Text>
        </div>

        {/* Stats grid */}
        <Row gutter={[16, 16]}>
          <Col xs={24} sm={12} lg={6}>
            <StatCard
              title="对话总数"
              value={stats.sessions}
              icon={<MessageOutlined />}
              color="#3b82f6"
              bgColor="#3b82f620"
              suffix={stats.pinned > 0 ? `${stats.pinned} 已置顶` : undefined}
              onClick={() => navigate('/chat')}
            />
          </Col>
          <Col xs={24} sm={12} lg={6}>
            <StatCard
              title="记忆条数"
              value={stats.memories}
              icon={<BulbOutlined />}
              color="#a855f7"
              bgColor="#a855f720"
              onClick={() => navigate('/memory')}
            />
          </Col>
          <Col xs={24} sm={12} lg={6}>
            <StatCard
              title="技能数"
              value={stats.skills}
              icon={<ThunderboltOutlined />}
              color="#eab308"
              bgColor="#eab30820"
              onClick={() => navigate('/skills')}
            />
          </Col>
          <Col xs={24} sm={12} lg={6}>
            <StatCard
              title="智能体"
              value={stats.agents}
              icon={<RobotOutlined />}
              color="#22c55e"
              bgColor="#22c55e20"
              suffix={`${activeAgents.length} 个活跃`}
              onClick={() => navigate('/agents')}
            />
          </Col>
        </Row>

        {/* Quick Actions */}
        <div className="mt-6">
          <div className="flex items-center gap-2 mb-3">
            <ThunderboltOutlined className="text-primary" />
            <Text strong className="text-sm">
              快捷操作
            </Text>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            <QuickAction
              icon={<PlusOutlined />}
              label="新建对话"
              description="开始与智能体的新对话"
              onClick={() => navigate('/agents')}
            />
            <QuickAction
              icon={<RobotOutlined />}
              label="浏览智能体"
              description={`共 ${agents.length} 个可用智能体`}
              onClick={() => navigate('/agents')}
            />
            <QuickAction
              icon={<BulbOutlined />}
              label="管理记忆"
              description="查看和管理 Agent 记忆"
              onClick={() => navigate('/memory')}
            />
          </div>
        </div>

        {/* Bottom section: Recent + Status */}
        <Row gutter={[16, 16]} className="mt-6">
          {/* Recent sessions */}
          <Col xs={24} lg={14}>
            <Card
              title={
                <div className="flex items-center gap-2">
                  <ClockCircleOutlined />
                  <span>最近对话</span>
                  {todaySessions > 0 && (
                    <Tag color="blue" className="ml-2">
                      今日 +{todaySessions}
                    </Tag>
                  )}
                </div>
              }
              extra={
                sessions.length > 5 && (
                  <button
                    onClick={() => navigate('/chat')}
                    className="text-xs text-primary hover:text-primary/80 transition-colors"
                  >
                    查看全部
                  </button>
                )
              }
              styles={{ body: { padding: sessions.length === 0 ? '24px' : '8px 24px' } }}
            >
              {recentSessions.length === 0 ? (
                <div className="text-center py-8">
                  <MessageOutlined className="text-3xl text-muted-foreground mb-3" />
                  <div>
                    <Text type="secondary">
                      暂无对话。开始聊天后即可在此查看活动记录。
                    </Text>
                  </div>
                  <button
                    onClick={() => navigate('/agents')}
                    className="mt-4 text-primary text-sm hover:text-primary/80 transition-colors"
                  >
                    开始第一次对话 →
                  </button>
                </div>
              ) : (
                <List
                  dataSource={recentSessions}
                  renderItem={(session) => (
                    <List.Item
                      className="!px-0 cursor-pointer hover:bg-muted/50 rounded-lg transition-colors -mx-2 px-2"
                      onClick={() => navigate(`/chat?session=${session.id}`)}
                    >
                      <List.Item.Meta
                        avatar={
                          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary/10 text-primary">
                            <MessageOutlined className="text-sm" />
                          </div>
                        }
                        title={
                          <span className="text-sm font-medium">
                            {session.title || '无标题对话'}
                          </span>
                        }
                        description={
                          <Text type="secondary" className="text-xs">
                            {formatRelativeTime(session.updated_at)}
                          </Text>
                        }
                      />
                      <ArrowRightOutlined className="text-muted-foreground text-xs" />
                    </List.Item>
                  )}
                />
              )}
            </Card>
          </Col>

          {/* System status */}
          <Col xs={24} lg={10}>
            <Card
              title={
                <div className="flex items-center gap-2">
                  <CheckCircleOutlined />
                  <span>系统状态</span>
                </div>
              }
            >
              <div className="space-y-4">
                <div className="flex items-center justify-between">
                  <Text type="secondary">API 服务</Text>
                  <StatusBadge status={systemStatus.api} />
                </div>
                <div className="flex items-center justify-between">
                  <Text type="secondary">数据库</Text>
                  <StatusBadge status={systemStatus.database} />
                </div>
                <div className="flex items-center justify-between">
                  <Text type="secondary">LLM 提供商</Text>
                  <Tag color="success" icon={<CheckCircleOutlined />}>
                    已配置
                  </Tag>
                </div>
                <div className="flex items-center justify-between">
                  <Text type="secondary">智能体可用</Text>
                  <Tag color={agents.length > 0 ? 'success' : 'warning'} icon={agents.length > 0 ? <CheckCircleOutlined /> : <CloseCircleOutlined />}>
                    {agents.length > 0 ? `${agents.length} 个` : '无'}
                  </Tag>
                </div>

                {/* Today's activity */}
                <div className="pt-2 border-t border-border">
                  <div className="flex items-center justify-between">
                    <Text type="secondary" className="text-xs">
                      今日活跃度
                    </Text>
                    <Text className="text-xs">
                      {todaySessions} 次对话
                    </Text>
                  </div>
                </div>
              </div>
            </Card>
          </Col>
        </Row>
      </div>
    </div>
  );
}
