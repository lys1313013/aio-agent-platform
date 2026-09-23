import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { Alert, Button, Card, Select, Space, Spin, Tag, Typography, message } from 'antd';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { skillsApi } from '@/lib/api';
import { openSourceSession } from '@/lib/skillSource';
import type { Skill, SkillVersion } from '@/lib/types';
import { useAuthStore } from '@/stores/authStore';

const { Title, Text, Paragraph } = Typography;
const verificationLabels: Record<string, string> = { unverified: '未验证', passed: '验证通过', failed: '验证失败' };

export default function SkillDetailPage() {
  const { skillId = '' } = useParams();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const versionParam = params.get('version');
  const [skill, setSkill] = useState<Skill | null>(null);
  const [current, setCurrent] = useState<Skill | null>(null);
  const [versions, setVersions] = useState<SkillVersion[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const role = useAuthStore(s => s.role);

  useEffect(() => {
    let cancelled = false;
    setLoading(true); setError(''); setSkill(null);
    (async () => {
      try {
        const [latest, history] = await Promise.all([skillsApi.get(skillId), skillsApi.listVersions(skillId)]);
        let selected = latest;
        if (versionParam && Number(versionParam) !== latest.version) {
          const version = Number(versionParam);
          if (!Number.isSafeInteger(version) || version < 1) throw new Error('版本不存在');
          const old = await skillsApi.getVersion(skillId, version);
          selected = { ...latest, ...old.snapshot, version, content: old.content,
            files: old.snapshot?.files ?? [], provenance: old.snapshot?.provenance ?? {},
            verification: old.snapshot?.verification ?? {},
            name: old.snapshot?.name ?? `${latest.name}（历史元数据缺失）` };
        }
        if (!cancelled) { setSkill(selected); setCurrent(latest); setVersions(history); }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : '技能已删除或无权访问');
      } finally { if (!cancelled) setLoading(false); }
    })();
    return () => { cancelled = true; };
  }, [skillId, versionParam]);

  const download = async () => {
    if (!skill || !current) return;
    try {
      const blob = await skillsApi.download(skill.id, skill.version === current.version ? undefined : skill.version);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a'); a.href = url; a.download = `${skill.name}_v${skill.version}.zip`; a.click();
      URL.revokeObjectURL(url);
    } catch (e) { message.error(e instanceof Error ? e.message : '下载失败'); }
  };

  const openSource = async (sessionId: string) => {
    if (!await openSourceSession(sessionId, role, navigate)) {
      message.error('来源会话已删除或无权访问');
    }
  };

  const source = skill?.provenance?.created;
  const modified = skill?.provenance?.modified;
  return (
    <main className="mx-auto max-w-5xl p-6 space-y-4">
      <Link to={role === 'user' ? '/portal/skills' : '/skills'}>返回{role === 'user' ? '我的技能' : '技能库'}</Link>
      {loading ? <Spin aria-label="加载技能" /> : error ? <Alert type="error" showIcon title="技能不可访问" description={error} /> : skill && current && <>
        <Card>
          <Space wrap><Title level={3} style={{ margin: 0 }}>{skill.name}</Title><Tag>v{skill.version}</Tag>
            <Tag color={skill.is_active ? 'blue' : undefined}>{skill.is_active ? '已启用' : '已停用'}</Tag>
            <Tag>{verificationLabels[skill.verification?.status ?? 'unverified'] ?? '未验证'}</Tag>
            <Tag>{skill.is_public ? '已公开' : '仅本人可见'}</Tag>
          </Space>
          <Paragraph className="mt-3">{skill.description}</Paragraph>
          <Space wrap>
            <Select aria-label="技能版本" value={skill.version} onChange={v => setParams(v === current.version ? {} : { version: String(v) })}
              options={[{ value: current.version, label: `v${current.version}（当前）` }, ...versions.map(v => ({ value: v.version, label: `v${v.version}（历史）` }))]} />
            <Button onClick={download}>下载此版本</Button>
          </Space>
          <Paragraph className="mt-3" type="secondary">来源：{source?.type === 'agent' ? '智能体创建' : source?.type === 'manual' ? '手工创建' : '历史记录，来源未知'}{source?.agent_id ? ` · 智能体 ${source.agent_id}` : ''}</Paragraph>
          {source?.session_id && <Button type="link" onClick={() => openSource(source.session_id!)}>查看来源会话</Button>}
          {modified?.summary && <Paragraph>修改说明：{modified.summary}</Paragraph>}
          {modified?.at && <Text type="secondary">更新时间：{new Date(modified.at).toLocaleString('zh-CN')}</Text>}
          {skill.verification?.note && <Paragraph>{skill.verification.note}</Paragraph>}
          <Paragraph>触发条件：{skill.trigger_condition || '未填写'}</Paragraph>
        </Card>
        <Card title="技能说明"><article className="prose dark:prose-invert max-w-none"><ReactMarkdown remarkPlugins={[remarkGfm]}>{skill.content || ''}</ReactMarkdown></article></Card>
        <Card title={`附件（${skill.files.length}）`}>
          {skill.files.length ? skill.files.map(f => <Paragraph key={f.path}><code>{f.path}</code> · {f.size} 字节 {f.description}</Paragraph>) : <Text type="secondary">无附件</Text>}
          <Paragraph type="secondary">下载此版本可查看完整附件；历史版本不会随新版本修改。</Paragraph>
        </Card>
      </>}
    </main>
  );
}
