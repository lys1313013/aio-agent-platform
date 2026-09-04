import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { App, Empty, Spin, Typography } from 'antd';
import { portalApi } from '@/lib/api';
import type { PortalAgent } from '@/lib/types';
import { getAgentIcon } from '@/lib/agent-icons';

const { Title, Text, Paragraph } = Typography;

/** 用户端门户首页：可用智能体卡片列表（脱敏数据，无管理端信息） */
export default function PortalAgentListPage() {
  const navigate = useNavigate();
  const { message } = App.useApp();
  const [agents, setAgents] = useState<PortalAgent[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    portalApi
      .listAgents()
      .then(setAgents)
      .catch((err) => message.error(`加载智能体失败：${err.message}`))
      .finally(() => setLoading(false));
  }, [message]);

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto max-w-5xl px-4 sm:px-6 py-8">
        <div className="mb-6">
          <Title level={3} className="!mb-1">智能体</Title>
          <Text type="secondary">选择一个智能体开始对话</Text>
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-24">
            <Spin size="large" />
          </div>
        ) : agents.length === 0 ? (
          <div className="flex items-center justify-center py-24">
            <Empty description="暂无可用的智能体，请联系管理员" />
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {agents.map((agent) => (
              <button
                key={agent.id}
                onClick={() => navigate(`/portal/agents/${agent.id}/chat`)}
                className="group flex flex-col rounded-2xl border border-border bg-card p-5 text-left transition-all hover:border-primary/30 hover:shadow-lg hover:-translate-y-0.5 active:translate-y-0 cursor-pointer"
              >
                <div className="mb-3 flex items-center gap-3">
                  <span className="flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-xl bg-primary/10 text-2xl">
                    {getAgentIcon(agent.icon)}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-base font-semibold text-foreground group-hover:text-primary transition-colors">
                    {agent.name}
                  </span>
                </div>
                <Paragraph
                  type="secondary"
                  className="!mb-0 !text-sm"
                  ellipsis={{ rows: 2, tooltip: agent.description }}
                >
                  {agent.description || agent.welcome_message || '暂无描述'}
                </Paragraph>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
