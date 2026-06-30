import { useParams, useNavigate } from 'react-router-dom';
import { useState, useEffect, useCallback } from 'react';
import { ArrowLeftOutlined } from '@ant-design/icons';
import { Button, Spin, Typography, App } from 'antd';
import { agentsApi } from '@/lib/api';
import type { Agent } from '@/lib/types';
import ApiDocPanel from '@/components/api/ApiDocPanel';
import { getAgentIcon } from '@/lib/agent-icons';

const { Text } = Typography;

export default function AgentApiPage() {
  const { agentId } = useParams<{ agentId: string }>();
  const navigate = useNavigate();
  const { message } = App.useApp();
  const [agent, setAgent] = useState<Agent | null>(null);
  const [loading, setLoading] = useState(true);

  const loadAgent = useCallback(async () => {
    if (!agentId) return;
    setLoading(true);
    try {
      const a = await agentsApi.get(agentId);
      setAgent(a);
    } catch (err: any) {
      message.error(`加载智能体失败: ${err.message}`);
    } finally {
      setLoading(false);
    }
  }, [agentId, message]);

  useEffect(() => {
    loadAgent();
  }, [loadAgent]);

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <Spin size="large" />
      </div>
    );
  }

  if (!agent || !agentId) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <Text type="secondary">智能体未找到</Text>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden bg-card">
      {/* Header */}
      <div className="flex items-center gap-3 px-6 py-4 border-b border-border bg-card/80 backdrop-blur-sm">
        <Button
          type="text"
          icon={<ArrowLeftOutlined />}
          onClick={() => navigate(-1)}
          className="!text-muted-foreground hover:!text-foreground"
        />
        <span className="text-2xl">{getAgentIcon(agent.icon)}</span>
        <div className="min-w-0">
          <div className="font-semibold text-base">{agent.name}</div>
          <Text type="secondary" className="text-xs">API 文档</Text>
        </div>
      </div>

      {/* API Doc content — wider layout for standalone page */}
      <div className="flex-1 overflow-y-auto">
        <div className="max-w-3xl mx-auto py-6 px-6">
          <ApiDocPanel agentId={agentId} onClose={() => navigate(-1)} embedded />
        </div>
      </div>
    </div>
  );
}
