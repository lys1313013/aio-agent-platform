import { useCallback, useEffect, useState } from 'react';
import {
  ThunderboltOutlined,
  PlusOutlined,
  EditOutlined,
  DeleteOutlined,
  SearchOutlined,
  BarChartOutlined,
  DownloadOutlined,
  HistoryOutlined,
  CodeOutlined,
  FileTextOutlined,
  PaperClipOutlined,
  InboxOutlined,
  ImportOutlined,
  CloudSyncOutlined,
  StarOutlined,
  ForkOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  ExclamationCircleOutlined,
} from '@ant-design/icons';
import {
  Button,
  Input,
  Tag,
  Empty,
  Card,
  Typography,
  Row,
  Col,
  Spin,
  Modal,
  Popconfirm,
  message,
  Drawer,
  Timeline,
  Upload,
  Tooltip,
  Checkbox,
} from 'antd';
import { skillsApi } from '@/lib/api';
import type { Skill, SkillVersion, SkillFile, SkillsShRepoMeta, SkillsShResolveResult, SkillsShSearchItem } from '@/lib/types';
import SkillEditorDrawer from '@/components/skills/SkillEditorDrawer';

const { Text } = Typography;

const CATEGORIES = ['全部', '通用', '编码', '运维', '研究', '写作'];
const CATEGORY_MAP: Record<string, string> = { '全部': '', '通用': 'general', '编码': 'coding', '运维': 'ops', '研究': 'research', '写作': 'writing' };
const CATEGORY_REVERSE: Record<string, string> = Object.fromEntries(Object.entries(CATEGORY_MAP).map(([k, v]) => [v, k]));

type EditorMode = 'view' | 'edit' | 'create';

/** skills.sh 市场搜索结果条目（含 GitHub 仓库元信息） */
interface ShResult extends SkillsShRepoMeta {
  key: string;
  skill_id: string;
  name: string;
  source: string;
  installs: number;
  description?: string;
  tags?: string[];
}

export default function SkillsPage() {
  const [activeCategory, setActiveCategory] = useState('全部');
  const [searchQuery, setSearchQuery] = useState('');
  const [skills, setSkills] = useState<Skill[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);

  const [editorOpen, setEditorOpen] = useState(false);
  const [editorMode, setEditorMode] = useState<EditorMode>('view');
  const [editorSkill, setEditorSkill] = useState<Skill | null>(null);

  const [versionsOpen, setVersionsOpen] = useState(false);
  const [versionsSkill, setVersionsSkill] = useState<Skill | null>(null);
  const [versions, setVersions] = useState<SkillVersion[]>([]);
  const [versionsLoading, setVersionsLoading] = useState(false);

  const [importOpen, setImportOpen] = useState(false);
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importing, setImporting] = useState(false);

  const [shOpen, setShOpen] = useState(false);
  const [shInput, setShInput] = useState('');
  const [shLoading, setShLoading] = useState(false);
  const [shResults, setShResults] = useState<ShResult[]>([]);
  const [shSelected, setShSelected] = useState<Set<string>>(new Set());
  const [shImporting, setShImporting] = useState(false);

  const fetchSkills = useCallback(async () => {
    setLoading(true);
    try {
      const cat = CATEGORY_MAP[activeCategory];
      if (searchQuery.trim()) {
        const results = await skillsApi.search(searchQuery, { category: cat || undefined });
        setSkills(results.map((r) => ({
          id: r.id, name: r.name, description: r.description, content: null,
          tags: r.tags, category: r.category, trigger_condition: null,
          use_count: r.use_count, success_count: r.success_count,
          is_public: false, is_active: true, version: r.version,
          files: r.files || [], last_used_at: null, created_at: '', updated_at: '',
        })));
        setTotal(results.length);
      } else {
        const resp = await skillsApi.list({ category: cat || undefined });
        setSkills(resp.items);
        setTotal(resp.total);
      }
    } catch {
      message.error('加载技能失败');
    } finally {
      setLoading(false);
    }
  }, [activeCategory, searchQuery]);

  useEffect(() => { fetchSkills(); }, [fetchSkills]);

  const openCreate = () => { setEditorSkill(null); setEditorMode('create'); setEditorOpen(true); };

  const openView = async (skill: Skill) => {
    try {
      const full = await skillsApi.get(skill.id);
      setEditorSkill(full); setEditorMode('view'); setEditorOpen(true);
    } catch { message.error('加载技能详情失败'); }
  };

  const openEdit = async (skill: Skill) => {
    try {
      const full = await skillsApi.get(skill.id);
      setEditorSkill(full); setEditorMode('edit'); setEditorOpen(true);
    } catch { message.error('加载技能详情失败'); }
  };

  const handleDelete = async (id: string) => {
    try { await skillsApi.delete(id); message.success('已删除'); fetchSkills(); }
    catch { message.error('删除失败'); }
  };

  const handleDownload = async (skill: Skill) => {
    try {
      const blob = await skillsApi.download(skill.id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${skill.name.replace(/\s+/g, '_')}_v${skill.version}.zip`;
      a.click();
      URL.revokeObjectURL(url);
    } catch { message.error('下载失败'); }
  };

  const openVersions = async (skill: Skill) => {
    setVersionsSkill(skill); setVersionsOpen(true); setVersionsLoading(true);
    try { setVersions(await skillsApi.listVersions(skill.id)); }
    catch { message.error('加载版本历史失败'); }
    finally { setVersionsLoading(false); }
  };

  const handleImport = async () => {
    if (!importFile) { message.warning('请选择 zip 文件'); return; }
    setImporting(true);
    try {
      await skillsApi.importFromZip(importFile);
      message.success('导入成功');
      setImportOpen(false); setImportFile(null); fetchSkills();
    } catch (e: any) {
      message.error(e?.message || '导入失败');
    } finally { setImporting(false); }
  };

  const openShSync = () => {
    setShOpen(true);
    setShInput('');
    setShResults([]);
    setShSelected(new Set());
  };

  const handleShSearch = async () => {
    const input = shInput.trim();
    if (!input) { message.warning('请输入 skills.sh 链接或关键词'); return; }
    setShLoading(true);
    try {
      let results: ShResult[];
      const looksLikeRef = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(input);
      if (input.includes('skills.sh/') || looksLikeRef) {
        const r: SkillsShResolveResult = await skillsApi.shResolve(input);
        results = [{
          key: `${r.source}/${r.skill_id}`,
          skill_id: r.skill_id,
          name: r.name,
          source: r.source,
          installs: r.installs ?? 0,
          description: r.description || undefined,
          tags: r.tags,
          stars: r.stars,
          forks: r.forks,
          repo_description: r.repo_description,
          language: r.language,
          license: r.license,
        }];
      } else {
        results = (await skillsApi.shSearch(input)).map((s: SkillsShSearchItem) => ({
          key: `${s.source}/${s.skill_id}`,
          skill_id: s.skill_id,
          name: s.name,
          source: s.source,
          installs: s.installs,
          stars: s.stars,
          forks: s.forks,
          repo_description: s.repo_description,
          language: s.language,
          license: s.license,
        }));
      }
      if (results.length === 0) message.info('未找到匹配的技能');
      setShResults(results);
      setShSelected(new Set());
    } catch (e: any) {
      message.error(e?.message || '搜索失败');
    } finally { setShLoading(false); }
  };

  const toggleShSelect = (key: string) => {
    setShSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const handleShImport = async () => {
    const entries = shResults
      .filter((r) => shSelected.has(r.key))
      .map((r) => ({ source: r.source, skill_id: r.skill_id }));
    if (entries.length === 0) { message.warning('请先选择要同步的技能'); return; }
    setShImporting(true);
    try {
      const res = await skillsApi.shImport(entries);
      if (res.imported.length > 0) message.success(`已同步 ${res.imported.length} 个技能`);
      if (res.errors.length > 0) {
        message.error(`${res.errors.length} 个技能同步失败: ${res.errors[0].error}`);
      }
      if (res.imported.length > 0) {
        setShOpen(false);
        fetchSkills();
      }
    } catch (e: any) {
      message.error(e?.message || '同步失败');
    } finally { setShImporting(false); }
  };

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="w-full max-w-[1400px] mx-auto px-6 py-8">
        {/* Header */}
        <div className="mb-8 flex items-start justify-between">
          <div>
            <h1 className="text-2xl font-bold flex items-center gap-2.5">
              <span className="inline-flex items-center justify-center w-9 h-9 rounded-xl bg-primary/10 text-primary">
                <ThunderboltOutlined className="text-lg" />
              </span>
              技能
            </h1>
            <Text type="secondary" className="mt-1 block">查看和管理 Agent 从完成任务中学到的技能</Text>
          </div>
          <div className="flex items-center gap-2">
            <Tooltip title="从 skills.sh 技能市场同步">
              <Button icon={<CloudSyncOutlined />} onClick={openShSync}>同步</Button>
            </Tooltip>
            <Tooltip title="从 zip 包导入技能">
              <Button icon={<ImportOutlined />} onClick={() => setImportOpen(true)}>导入</Button>
            </Tooltip>
            <Button type="primary" icon={<PlusOutlined />} onClick={openCreate} size="large" className="shadow-sm shadow-primary/20">
              创建技能
            </Button>
          </div>
        </div>

        {/* Category filter pills */}
        <div className="flex flex-wrap items-center gap-2 mb-5">
          {CATEGORIES.map((cat) => {
            const active = activeCategory === cat;
            return (
              <button
                key={cat}
                onClick={() => setActiveCategory(cat)}
                className={`px-3.5 py-1.5 rounded-full text-sm font-medium transition-all border ${
                  active
                    ? 'bg-primary text-white border-primary shadow-sm shadow-primary/20'
                    : 'bg-white dark:bg-gray-900 text-muted-foreground border-border hover:border-primary/40 hover:text-foreground'
                }`}
              >
                {cat}
              </button>
            );
          })}
        </div>

        {/* Search */}
        <Input
          prefix={<SearchOutlined className="text-muted-foreground" />}
          placeholder="按名称或描述搜索技能..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          allowClear
          size="large"
          className="mb-6 rounded-xl [&_.ant-input]:!text-sm"
        />

        {/* Grid */}
        <Spin spinning={loading}>
          {skills.length === 0 ? (
            <Card className="rounded-xl border-dashed">
              <Empty
                image={<ThunderboltOutlined className="text-6xl text-muted-foreground/20" />}
                styles={{ image: { height: 60 } }}
                description={
                  <div>
                    <Text type="secondary">{searchQuery || activeCategory !== '全部' ? '没有匹配的技能。' : '还没有学到任何技能。'}</Text><br />
                    <Text type="secondary" className="text-xs">当 Agent 完成多步骤任务时，技能会自动提取。</Text>
                  </div>
                }
              />
            </Card>
          ) : (
            <>
              <div className="mb-3 flex items-center gap-2">
                <Text type="secondary" className="text-xs">共 <Text strong className="text-xs">{total}</Text> 个技能</Text>
                {searchQuery && <Tag color="blue" closable onClose={() => setSearchQuery('')}>搜索: {searchQuery}</Tag>}
              </div>
              <Row gutter={[16, 16]}>
                {skills.map((skill) => {
                  const rate = skill.use_count > 0 ? Math.round((skill.success_count / skill.use_count) * 100) : 0;
                  return (
                    <Col xs={24} sm={12} lg={8} key={skill.id}>
                      <Card
                        className="group rounded-xl border-border/60 hover:border-primary/40 hover:shadow-md transition-all duration-200 h-full flex flex-col overflow-hidden [&_.ant-card-body]:flex [&_.ant-card-body]:flex-col [&_.ant-card-body]:flex-1 [&_.ant-card-body]:!p-4"
                      >
                        {/* Header */}
                        <div className="flex items-start justify-between mb-2 cursor-pointer" onClick={() => openView(skill)}>
                          <h3 className="font-semibold text-sm hover:text-primary transition-colors leading-snug pr-2">
                            {skill.name}
                          </h3>
                          <Tag className="!ml-0 shrink-0 !text-[10px] !px-1.5 !py-0 rounded-full" color="blue">v{skill.version}</Tag>
                        </div>

                        <Text
                          type="secondary"
                          className="text-xs leading-relaxed line-clamp-2 mb-3 cursor-pointer"
                          onClick={() => openView(skill)}
                        >
                          {skill.description || '暂无描述'}
                        </Text>

                        {/* Tags */}
                        {skill.tags.length > 0 && (
                          <div className="flex flex-wrap gap-1 mb-3">
                            {skill.tags.slice(0, 4).map((t) => (
                              <Tag key={t} className="!text-[10px] !px-1.5 !py-0 rounded-full bg-gray-50 dark:bg-gray-800 border-0">{t}</Tag>
                            ))}
                            {skill.tags.length > 4 && (
                              <Text type="secondary" className="text-[10px] self-center">+{skill.tags.length - 4}</Text>
                            )}
                          </div>
                        )}

                        {/* File counts */}
                        {skill.files && skill.files.length > 0 && (
                          <div className="flex flex-wrap gap-1.5 mb-3">
                            {skill.files.filter((f: SkillFile) => f.type === 'script').length > 0 && (
                              <span className="inline-flex items-center gap-1 text-[10px] text-geekblue-600 bg-geekblue-50 dark:bg-geekblue-950/30 px-2 py-0.5 rounded-full">
                                <CodeOutlined /> {skill.files.filter((f: SkillFile) => f.type === 'script').length} 脚本
                              </span>
                            )}
                            {skill.files.filter((f: SkillFile) => f.type === 'reference').length > 0 && (
                              <span className="inline-flex items-center gap-1 text-[10px] text-cyan-600 bg-cyan-50 dark:bg-cyan-950/30 px-2 py-0.5 rounded-full">
                                <FileTextOutlined /> {skill.files.filter((f: SkillFile) => f.type === 'reference').length} 参考
                              </span>
                            )}
                            {skill.files.filter((f: SkillFile) => f.type === 'asset').length > 0 && (
                              <span className="inline-flex items-center gap-1 text-[10px] text-purple-600 bg-purple-50 dark:bg-purple-950/30 px-2 py-0.5 rounded-full">
                                <PaperClipOutlined /> {skill.files.filter((f: SkillFile) => f.type === 'asset').length} 资源
                              </span>
                            )}
                          </div>
                        )}

                        {/* Stats row */}
                        <div className="flex items-center gap-3 text-[11px] mb-3">
                          <span className="inline-flex items-center gap-1 text-muted-foreground">
                            <BarChartOutlined className="text-blue-500" />
                            使用 {skill.use_count} 次
                          </span>
                          {skill.use_count > 0 && (
                            <span className={`inline-flex items-center gap-1 ${
                              rate >= 80 ? 'text-green-600' : rate >= 50 ? 'text-amber-600' : 'text-red-500'
                            }`}>
                              {rate >= 80 ? <CheckCircleOutlined /> : rate >= 50 ? <ExclamationCircleOutlined /> : <CloseCircleOutlined />}
                              成功率 {rate}%
                            </span>
                          )}
                          <span className="ml-auto text-[10px] text-muted-foreground bg-gray-50 dark:bg-gray-800 px-1.5 py-0.5 rounded">
                            {CATEGORY_REVERSE[skill.category] || skill.category}
                          </span>
                        </div>

                        {/* Action bar */}
                        <div className="mt-auto pt-3 border-t border-border/40 flex items-center gap-0.5">
                          <Tooltip title="编辑">
                            <Button type="text" size="small" icon={<EditOutlined />} onClick={() => openEdit(skill)} className="!text-muted-foreground hover:!text-primary" />
                          </Tooltip>
                          <Tooltip title="下载">
                            <Button type="text" size="small" icon={<DownloadOutlined />} onClick={() => handleDownload(skill)} className="!text-muted-foreground hover:!text-primary" />
                          </Tooltip>
                          <Tooltip title="版本历史">
                            <Button type="text" size="small" icon={<HistoryOutlined />} onClick={() => openVersions(skill)} className="!text-muted-foreground hover:!text-primary" />
                          </Tooltip>
                          <div className="ml-auto">
                            <Popconfirm title="确定删除此技能？" onConfirm={() => handleDelete(skill.id)} okText="删除" cancelText="取消" okButtonProps={{ danger: true }}>
                              <Tooltip title="删除">
                                <Button type="text" size="small" danger icon={<DeleteOutlined />} />
                              </Tooltip>
                            </Popconfirm>
                          </div>
                        </div>
                      </Card>
                    </Col>
                  );
                })}
              </Row>
            </>
          )}
        </Spin>

        {/* Skill editor modal */}
        <SkillEditorDrawer
          open={editorOpen}
          mode={editorMode}
          skill={editorSkill}
          onClose={() => { setEditorOpen(false); setEditorSkill(null); }}
          onSaved={fetchSkills}
        />

        {/* Version History drawer */}
        <Drawer
          title={versionsSkill ? (
            <span className="flex items-center gap-2">
              <HistoryOutlined className="text-muted-foreground" />
              <span className="font-semibold">{versionsSkill.name}</span>
              <Text type="secondary" className="text-sm font-normal">版本历史</Text>
            </span>
          ) : '版本历史'}
          open={versionsOpen}
          onClose={() => setVersionsOpen(false)}
          size={480}
          styles={{ body: { padding: '16px' } }}
        >
          <Spin spinning={versionsLoading}>
            {versions.length === 0 ? <Empty description="暂无历史版本" /> : (
              <Timeline
                items={versions.map((v, i) => ({
                  color: i === 0 ? 'blue' : 'gray',
                  children: (
                    <div>
                      <div className="flex items-center gap-2 mb-1">
                        <Text strong className="text-sm">v{v.version}</Text>
                        {i === 0 && <Tag color="blue" className="!text-[10px]">最新</Tag>}
                      </div>
                      <Text type="secondary" className="text-xs block">{new Date(v.created_at).toLocaleString('zh-CN')}</Text>
                      <pre className="mt-2 text-xs bg-muted p-3 rounded-lg max-h-40 overflow-auto whitespace-pre-wrap leading-relaxed">
                        {v.content.slice(0, 500)}{v.content.length > 500 ? '\n...' : ''}
                      </pre>
                    </div>
                  ),
                }))}
              />
            )}
          </Spin>
        </Drawer>

        {/* skills.sh sync modal */}
        <Modal
          title={
            <span className="flex items-center gap-2">
              <CloudSyncOutlined className="text-primary" />
              <span className="font-semibold">从 skills.sh 同步</span>
            </span>
          }
          open={shOpen}
          onCancel={() => setShOpen(false)}
          width={720}
          footer={[
            <Button key="cancel" onClick={() => setShOpen(false)}>取消</Button>,
            <Button
              key="sync"
              type="primary"
              icon={<CloudSyncOutlined />}
              loading={shImporting}
              disabled={shSelected.size === 0}
              onClick={handleShImport}
            >
              同步所选 ({shSelected.size})
            </Button>,
          ]}
        >
          <div className="space-y-4 pt-2">
            <div className="flex gap-2">
              <Input
                prefix={<SearchOutlined className="text-muted-foreground" />}
                placeholder="粘贴 skills.sh 链接（如 https://www.skills.sh/vercel-labs/skills/find-skills），或输入关键词搜索"
                value={shInput}
                onChange={(e) => setShInput(e.target.value)}
                onPressEnter={handleShSearch}
                allowClear
                size="large"
              />
              <Button type="primary" size="large" loading={shLoading} onClick={handleShSearch}>搜索</Button>
            </div>
            <Text type="secondary" className="text-xs block">粘贴单个技能链接可直接解析该技能；输入关键词则搜索 skills.sh 技能市场，勾选后点击「同步所选」导入。</Text>

            <Spin spinning={shLoading}>
              {shResults.length === 0 ? (
                <Empty description={shLoading ? ' ' : '暂无结果'} image={Empty.PRESENTED_IMAGE_SIMPLE} />
              ) : (
                <div className="max-h-[360px] overflow-y-auto space-y-2 pr-1">
                  {shResults.map((r) => {
                    const checked = shSelected.has(r.key);
                    return (
                      <div
                        key={r.key}
                        className={`flex items-start gap-3 p-3 rounded-xl border cursor-pointer transition-colors ${
                          checked ? 'border-primary bg-primary/5' : 'border-border hover:border-primary/40'
                        }`}
                        onClick={() => toggleShSelect(r.key)}
                      >
                        <Checkbox
                          checked={checked}
                          className="mt-0.5"
                          onClick={(e) => e.stopPropagation()}
                          onChange={() => toggleShSelect(r.key)}
                        />
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <Text strong className="text-sm">{r.name}</Text>
                            <Tag color="blue" className="!ml-0 !text-[10px]">{r.source}</Tag>
                          </div>
                          {(r.description || r.repo_description) && (
                            <Text type="secondary" className="text-xs block mt-1 line-clamp-2">
                              {r.description || r.repo_description}
                            </Text>
                          )}
                          {r.tags && r.tags.length > 0 && (
                            <div className="flex flex-wrap gap-1 mt-1">
                              {r.tags.slice(0, 4).map((t) => (
                                <Tag key={t} className="!text-[10px] !px-1.5 !py-0 rounded-full bg-gray-50 dark:bg-gray-800 border-0">{t}</Tag>
                              ))}
                            </div>
                          )}
                          <div className="flex flex-wrap items-center gap-3 mt-1.5 text-[11px] text-muted-foreground">
                            {r.installs > 0 && (
                              <span className="inline-flex items-center gap-1"><DownloadOutlined className="text-geekblue-500" />{r.installs.toLocaleString()}</span>
                            )}
                            {r.stars != null && (
                              <span className="inline-flex items-center gap-1"><StarOutlined className="text-amber-500" />{r.stars.toLocaleString()}</span>
                            )}
                            {r.forks != null && (
                              <span className="inline-flex items-center gap-1"><ForkOutlined className="text-muted-foreground" />{r.forks.toLocaleString()}</span>
                            )}
                            {r.language && (
                              <span className="inline-flex items-center px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-800">{r.language}</span>
                            )}
                            {r.license && (
                              <span className="inline-flex items-center px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-800">{r.license}</span>
                            )}
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </Spin>
          </div>
        </Modal>

        {/* Import modal */}
        <Modal
          title={
            <span className="flex items-center gap-2">
              <ImportOutlined className="text-primary" />
              <span className="font-semibold">导入技能</span>
            </span>
          }
          open={importOpen}
          onOk={handleImport}
          onCancel={() => { setImportOpen(false); setImportFile(null); }}
          confirmLoading={importing}
          okText="导入"
          cancelText="取消"
          okButtonProps={{ disabled: !importFile }}
          width={520}
        >
          <div className="space-y-4 pt-2">
            <Text type="secondary" className="text-sm">上传技能包 zip 文件，包含以下结构：</Text>
            <pre className="text-xs bg-muted p-3 rounded-lg leading-relaxed">{`skill-name/\n├── SKILL.md              ← 必填\n├── scripts/              ← 可选：可执行脚本\n├── references/           ← 可选：参考文档\n└── assets/               ← 可选：资源文件`}</pre>
            <Upload.Dragger
              accept=".zip" maxCount={1}
              fileList={importFile ? [{ uid: '-1', name: importFile.name, status: 'done' } as any] : []}
              beforeUpload={(file) => {
                if (!file.name.toLowerCase().endsWith('.zip')) { message.error('只支持 .zip'); return false; }
                if (file.size > 10 * 1024 * 1024) { message.error('超过 10MB'); return false; }
                setImportFile(file); return false;
              }}
              onRemove={() => setImportFile(null)}
            >
              <p className="ant-upload-drag-icon"><InboxOutlined className="text-primary/60" /></p>
              <p className="ant-upload-text">点击或拖放 zip 文件到此处</p>
              <p className="ant-upload-hint">支持 .zip 格式，最大 10MB</p>
            </Upload.Dragger>
          </div>
        </Modal>
      </div>
    </div>
  );
}
