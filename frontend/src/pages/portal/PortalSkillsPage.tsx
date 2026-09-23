import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { App, Empty, Spin, Tag, Typography } from 'antd';
import { skillsApi } from '@/lib/api';
import type { Skill } from '@/lib/types';

const { Title, Text, Paragraph } = Typography;

const verificationLabels: Record<string, string> = {
  unverified: '未验证',
  passed: '验证通过',
  failed: '验证失败',
};

/** 用户端门户技能库：只读查看本人技能，创建与修改通过对话由智能体完成 */
export default function PortalSkillsPage() {
  const navigate = useNavigate();
  const { message } = App.useApp();
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    skillsApi
      .list()
      .then((resp) => setSkills(resp.items))
      .catch((err) => message.error(`加载技能失败：${err.message}`))
      .finally(() => setLoading(false));
  }, [message]);

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto max-w-5xl px-4 sm:px-6 py-8">
        <div className="mb-6">
          <Title level={3} className="!mb-1">我的技能</Title>
          <Text type="secondary">在对话中让智能体创建或修改技能，保存后即可在此查看</Text>
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-24">
            <Spin size="large" />
          </div>
        ) : skills.length === 0 ? (
          <div className="flex items-center justify-center py-24">
            <Empty description="还没有技能，可在对话中让智能体把常用方法保存成技能" />
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {skills.map((skill) => (
              <button
                key={skill.id}
                onClick={() => navigate(`/skills/${skill.id}`)}
                className="group flex flex-col rounded-2xl border border-border bg-card p-5 text-left transition-all hover:border-primary/30 hover:shadow-lg hover:-translate-y-0.5 active:translate-y-0 cursor-pointer"
              >
                <div className="mb-2 flex items-center gap-2">
                  <span className="min-w-0 flex-1 truncate text-base font-semibold text-foreground group-hover:text-primary transition-colors">
                    {skill.name}
                  </span>
                  <Tag className="!m-0">v{skill.version}</Tag>
                </div>
                <Paragraph
                  type="secondary"
                  className="!mb-3 !text-sm"
                  ellipsis={{ rows: 2, tooltip: skill.description || undefined }}
                >
                  {skill.description || '暂无描述'}
                </Paragraph>
                <div className="flex flex-wrap gap-1">
                  <Tag className="!text-[10px]">{verificationLabels[skill.verification?.status ?? 'unverified'] ?? '未验证'}</Tag>
                  {!skill.is_active && <Tag className="!text-[10px]">已停用</Tag>}
                  {skill.files.length > 0 && <Tag className="!text-[10px]">{skill.files.length} 个附件</Tag>}
                </div>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}