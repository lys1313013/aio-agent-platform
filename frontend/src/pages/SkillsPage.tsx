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
} from 'antd';
import { skillsApi } from '@/lib/api';
import type { Skill, SkillVersion, SkillFile } from '@/lib/types';
import SkillEditorDrawer from '@/components/skills/SkillEditorDrawer';

const { Text } = Typography;

const CATEGORIES = ['全部', '通用', '编码', '运维', '研究', '写作'];
const CATEGORY_MAP: Record<string, string> = { '全部': '', '通用': 'general', '编码': 'coding', '运维': 'ops', '研究': 'research', '写作': 'writing' };
const CATEGORY_REVERSE: Record<string, string> = Object.fromEntries(Object.entries(CATEGORY_MAP).map(([k, v]) => [v, k]));

type EditorMode = 'view' | 'edit' | 'create';

export default function SkillsPage() {
  const [activeCategory, setActiveCategory] = useState('全部');
  const [searchQuery, setSearchQuery] = useState('');
  const [skills, setSkills] = useState<Skill[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);

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
          width={480}
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
