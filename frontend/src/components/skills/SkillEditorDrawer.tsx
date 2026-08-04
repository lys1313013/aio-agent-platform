import { useCallback, useEffect, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  Modal,
  Spin,
  Typography,
  Tag,
  Button,
  Input,
  Select,
  Upload,
  Popconfirm,
  Tabs,
  message,
  Collapse,
  Divider,
} from 'antd';
import {
  CodeOutlined,
  FileTextOutlined,
  PaperClipOutlined,
  FolderOutlined,
  FolderOpenOutlined,
  EditOutlined,
  EyeOutlined,
  SaveOutlined,
  DeleteOutlined,
  PlusOutlined,
  ThunderboltOutlined,
  CheckCircleOutlined,
  BarChartOutlined,
  CloseOutlined,
} from '@ant-design/icons';
import { skillsApi } from '@/lib/api';
import type { Skill } from '@/lib/types';

const { Text } = Typography;
const { TextArea } = Input;

const CATEGORIES = ['通用', '编码', '运维', '研究', '写作'];
const CATEGORY_MAP: Record<string, string> = {
  '通用': 'general', '编码': 'coding', '运维': 'ops', '研究': 'research', '写作': 'writing',
};

type DrawerMode = 'view' | 'edit' | 'create';

interface Props {
  open: boolean;
  mode: DrawerMode;
  skill: Skill | null;
  onClose: () => void;
  onSaved: () => void;
}

// ── helpers ───────────────────────────────────────────────

function fileIcon(type: string) {
  if (type === 'script') return <CodeOutlined />;
  if (type === 'reference') return <FileTextOutlined />;
  return <PaperClipOutlined />;
}
function fileColor(type: string) {
  if (type === 'script') return 'geekblue';
  if (type === 'reference') return 'cyan';
  return 'purple';
}

interface TreeNode { name: string; path: string; type: string; children: TreeNode[]; isDir: boolean }

function buildTree(files: { path: string; type: string }[]): TreeNode[] {
  const root: TreeNode[] = [];
  for (const file of files) {
    const parts = file.path.split('/');
    let level = root;
    for (let i = 0; i < parts.length; i++) {
      const part = parts[i];
      const isLast = i === parts.length - 1;
      const currentPath = parts.slice(0, i + 1).join('/');
      let existing = level.find((n) => n.name === part);
      if (!existing) {
        existing = { name: part, path: currentPath, type: isLast ? file.type : 'dir', children: [], isDir: !isLast };
        level.push(existing);
      }
      level = existing.children;
    }
  }
  return root;
}

function FileTreeNode({ node, selected, onSelect }: { node: TreeNode; selected: string | null; onSelect: (p: string) => void }) {
  const [expanded, setExpanded] = useState(true);
  if (node.isDir) {
    return (
      <div>
        <div
          className="flex items-center gap-1.5 py-1 px-1.5 cursor-pointer rounded-md text-sm text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors"
          onClick={() => setExpanded(!expanded)}
        >
          {expanded ? <FolderOpenOutlined className="text-amber-500 text-xs" /> : <FolderOutlined className="text-amber-500 text-xs" />}
          <span className="truncate text-xs font-medium">{node.name}</span>
          <span className="ml-auto text-[10px] text-muted-foreground/60">{node.children.length}</span>
        </div>
        {expanded && node.children.length > 0 && (
          <div className="ml-3 border-l border-border/60 pl-2">{node.children.map((c) => <FileTreeNode key={c.path} node={c} selected={selected} onSelect={onSelect} />)}</div>
        )}
      </div>
    );
  }
  const isSel = selected === node.path;
  return (
    <div
      className={`flex items-center gap-1.5 py-1 px-1.5 cursor-pointer rounded-md text-sm transition-colors ${
        isSel ? 'bg-primary/10 text-primary font-medium ring-1 ring-primary/20' : 'hover:bg-muted/50 text-muted-foreground hover:text-foreground'
      }`}
      onClick={() => onSelect(node.path)}
    >
      <span className="text-xs">{fileIcon(node.type)}</span>
      <span className="truncate text-xs">{node.name}</span>
    </div>
  );
}

const isMarkdown = (p: string) => /\.(md|txt)$/i.test(p);
const isCode = (p: string) => /\.(sh|py|js|ts|bash|json|yaml|yml|xml|css|html|toml|ini|cfg)$/i.test(p);

// ── main component ────────────────────────────────────────

export default function SkillEditorDrawer({ open, mode: initialMode, skill, onClose, onSaved }: Props) {
  const [mode, setMode] = useState<DrawerMode>(initialMode);
  const [selectedPath, setSelectedPath] = useState('SKILL.md');
  const [fileContents, setFileContents] = useState<Record<string, string>>({});
  const [contentLoading, setContentLoading] = useState(false);

  // Form state
  const [formName, setFormName] = useState('');
  const [formDescription, setFormDescription] = useState('');
  const [formContent, setFormContent] = useState('');
  const [formTags, setFormTags] = useState<string[]>([]);
  const [formCategory, setFormCategory] = useState('general');
  const [formTrigger, setFormTrigger] = useState('');
  const [formFiles, setFormFiles] = useState<{ file: File; type: 'script' | 'reference' | 'asset' }[]>([]);
  const [submitting, setSubmitting] = useState(false);

  const existingFiles: { path: string; type: string }[] = skill?.files?.map((f) => ({ path: f.path, type: f.type })) || [];
  const pendingFiles: { path: string; type: string }[] = formFiles.map((f) => ({
    path: `${f.type === 'script' ? 'scripts' : f.type === 'reference' ? 'references' : 'assets'}/${f.file.name}`,
    type: f.type,
  }));
  const allTreeFiles = [...existingFiles, ...pendingFiles];
  const tree = buildTree(allTreeFiles);

  // ── sync on open ──
  useEffect(() => {
    setMode(initialMode);
    setSelectedPath('SKILL.md');
    setFileContents({});
    setFormFiles([]);
    if (skill) {
      setFormName(skill.name);
      setFormDescription(skill.description || '');
      setFormContent(skill.content || '');
      setFormTags([...(skill.tags || [])]);
      setFormCategory(skill.category);
      setFormTrigger(skill.trigger_condition || '');
      setFileContents((prev) => ({ ...prev, 'SKILL.md': skill.content || '' }));
    } else {
      setFormName('');
      setFormDescription('');
      setFormContent('');
      setFormTags([]);
      setFormCategory('general');
      setFormTrigger('');
    }
  }, [skill, open, initialMode]);

  useEffect(() => {
    if (mode === 'edit' || mode === 'create') {
      setFileContents((prev) => ({ ...prev, 'SKILL.md': formContent }));
    }
  }, [formContent, mode]);

  // ── file loading ──
  const loadFile = useCallback(async (path: string) => {
    if (fileContents[path] !== undefined) return;
    if (!skill) return;
    setContentLoading(true);
    try {
      const blob = await skillsApi.downloadFile(skill.id, path);
      const text = await blob.text();
      setFileContents((prev) => ({ ...prev, [path]: text }));
    } catch {
      setFileContents((prev) => ({ ...prev, [path]: '(无法加载文件)' }));
    } finally {
      setContentLoading(false);
    }
  }, [skill, fileContents]);

  const handleSelectFile = (path: string) => {
    setSelectedPath(path);
    loadFile(path);
  };

  const handleDeleteFile = async (filePath: string) => {
    if (!skill) return;
    try {
      await skillsApi.deleteFile(skill.id, filePath);
      message.success('文件已删除');
      setFileContents((prev) => { const c = { ...prev }; delete c[filePath]; return c; });
      const refreshed = await skillsApi.get(skill.id);
      setFileContents((prev) => ({ ...prev, 'SKILL.md': refreshed.content || '' }));
      if (selectedPath === filePath) setSelectedPath('SKILL.md');
      onSaved();
    } catch {
      message.error('删除文件失败');
    }
  };

  // ── save ──
  const handleSubmit = async () => {
    if (!formName.trim() || !formContent.trim()) { message.warning('名称和内容不能为空'); return; }
    setSubmitting(true);
    try {
      let skillId: string;
      if (mode === 'edit' && skill) {
        const updated = await skillsApi.update(skill.id, {
          name: formName.trim(),
          description: formDescription.trim() || undefined,
          content: formContent.trim(),
          tags: formTags,
          category: formCategory,
          trigger_condition: formTrigger.trim() || undefined,
        });
        skillId = updated.id;
        message.success('技能已更新');
      } else {
        const created = await skillsApi.create({
          name: formName.trim(),
          description: formDescription.trim() || undefined,
          content: formContent.trim(),
          tags: formTags,
          category: formCategory,
          trigger_condition: formTrigger.trim() || undefined,
        });
        skillId = created.id;
        message.success('技能已创建');
      }
      if (formFiles.length > 0) {
        const byType: Record<string, File[]> = { script: [], reference: [], asset: [] };
        formFiles.forEach(({ file, type }) => byType[type].push(file));
        for (const [ft, fl] of Object.entries(byType)) {
          if (fl.length > 0) {
            try { await skillsApi.uploadFiles(skillId, fl, ft as 'script' | 'reference' | 'asset'); }
            catch { message.warning(`${ft} 文件上传失败`); }
          }
        }
      }
      onSaved();
      onClose();
    } catch {
      message.error(mode === 'edit' ? '更新失败' : '创建失败');
    } finally {
      setSubmitting(false);
    }
  };

  // ── rendering ──
  const renderFileContent = () => {
    if (contentLoading) return <div className="flex items-center justify-center h-full"><Spin /></div>;
    const content = fileContents[selectedPath];
    if (content === undefined) return <div className="flex items-center justify-center h-full text-muted-foreground"><Spin /></div>;
    if (isMarkdown(selectedPath)) {
      return (
        <div className="prose prose-sm max-w-none dark:prose-invert p-6 overflow-auto h-full">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
        </div>
      );
    }
    if (isCode(selectedPath)) {
      return (
        <pre className="text-xs bg-gray-50 dark:bg-gray-900 p-6 rounded-none overflow-auto h-full whitespace-pre-wrap font-mono leading-relaxed m-0">{content}</pre>
      );
    }
    return (
      <pre className="text-xs bg-gray-50 dark:bg-gray-900 p-6 rounded-none overflow-auto h-full whitespace-pre-wrap m-0">{content}</pre>
    );
  };

  const isEditing = mode === 'edit' || mode === 'create';
  const selectedFile = skill?.files?.find((f) => f.path === selectedPath);

  const titleText = mode === 'create' ? '创建技能' : mode === 'edit' ? '编辑技能' : '技能详情';
  const titleIcon = mode === 'create' ? <PlusOutlined /> : mode === 'edit' ? <EditOutlined /> : <ThunderboltOutlined />;

  return (
    <Modal
      title={
        <div className="flex items-center gap-2">
          <span className="inline-flex items-center justify-center w-7 h-7 rounded-lg bg-primary/10 text-primary text-sm">
            {titleIcon}
          </span>
          <span className="font-semibold text-base">{titleText}</span>
          {skill && (
            <Tag className="ml-1" color="blue">v{skill.version}</Tag>
          )}
          {mode === 'view' && skill && (
            <Button size="small" type="primary" ghost icon={<EditOutlined />} onClick={() => setMode('edit')} className="ml-2">
              编辑
            </Button>
          )}
        </div>
      }
      open={open}
      onCancel={onClose}
      width={Math.min(1100, window.innerWidth - 80)}
      footer={null}
      closable
      closeIcon={<CloseOutlined className="text-muted-foreground hover:text-foreground transition-colors" />}
      styles={{
        body: { padding: 0, height: 'calc(85vh - 110px)', overflow: 'hidden' },
        header: { padding: '14px 20px', borderBottom: '1px solid hsl(var(--border))' },
      }}
      destroyOnHidden
      classNames={{ body: '!p-0' }}
    >
      {/* ── Top status bar (view mode) ── */}
      {mode === 'view' && skill && (
        <div className="flex items-center gap-4 px-5 py-2.5 bg-gray-50/80 dark:bg-gray-900/50 border-b border-border/60 text-xs">
          <div className="flex items-center gap-1.5 text-muted-foreground">
            <BarChartOutlined className="text-blue-500" />
            <span>使用 <Text strong className="text-xs">{skill.use_count}</Text> 次</span>
          </div>
          <div className="flex items-center gap-1.5 text-muted-foreground">
            <CheckCircleOutlined className={skill.use_count > 0 ? 'text-green-500' : 'text-muted-foreground'} />
            <span>成功率 {skill.use_count > 0 ? <Text strong className="text-xs" type={skill.success_count / skill.use_count >= 0.8 ? 'success' : 'warning'}>{Math.round((skill.success_count / skill.use_count) * 100)}%</Text> : '-'}</span>
          </div>
          <div className="flex items-center gap-1.5 text-muted-foreground">
            <FolderOutlined className="text-amber-500" />
            <span>{skill.files?.length || 0} 个文件</span>
          </div>
          <Tag>{CATEGORIES.find((c) => CATEGORY_MAP[c] === skill.category) || skill.category}</Tag>
          {skill.tags?.length > 0 && (
            <div className="flex items-center gap-1 ml-auto">
              {skill.tags.slice(0, 4).map((t) => <Tag key={t} className="!text-[10px] !px-1.5 !py-0">{t}</Tag>)}
              {skill.tags.length > 4 && <Text type="secondary" className="text-[10px]">+{skill.tags.length - 4}</Text>}
            </div>
          )}
        </div>
      )}

      {/* ── Main content ── */}
      <div className="flex h-full" style={{ height: mode === 'view' && skill ? 'calc(100% - 40px)' : '100%' }}>
        {/* ── Left: File sidebar ── */}
        <div className="w-64 border-r border-border flex flex-col bg-gray-50/40 dark:bg-gray-950/40 shrink-0">
          {/* Sidebar header */}
          <div className="px-3 py-2.5 border-b border-border/60 flex items-center gap-2">
            <FolderOpenOutlined className="text-xs text-muted-foreground" />
            <Text className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">文件浏览器</Text>
            <Text type="secondary" className="text-[10px] ml-auto">{allTreeFiles.length + 1}</Text>
          </div>

          {/* File list */}
          <div className="flex-1 overflow-auto p-2">
            {/* SKILL.md */}
            <div
              className={`flex items-center gap-2 py-1.5 px-2 cursor-pointer rounded-md text-sm mb-1 transition-colors ${
                selectedPath === 'SKILL.md'
                  ? 'bg-primary/10 text-primary font-medium ring-1 ring-primary/20'
                  : 'hover:bg-muted/50 text-muted-foreground hover:text-foreground'
              }`}
              onClick={() => setSelectedPath('SKILL.md')}
            >
              <FileTextOutlined className="text-orange-500 text-xs" />
              <span className="text-xs font-medium">SKILL.md</span>
              {isEditing && <Tag className="!ml-auto !text-[9px] !px-1 !py-0" color="orange">编辑</Tag>}
            </div>

            {/* Tree nodes */}
            {tree.map((node) => (
              <FileTreeNode key={node.path} node={node} selected={selectedPath} onSelect={handleSelectFile} />
            ))}

            {allTreeFiles.length === 0 && !isEditing && (
              <div className="text-center py-6">
                <PaperClipOutlined className="text-2xl text-muted-foreground/30 mb-1 block" />
                <Text type="secondary" className="text-[11px]">暂无附属文件</Text>
              </div>
            )}

            {/* Upload area (edit/create only) */}
            {isEditing && (
              <>
                <Divider className="!my-3" />
                <Collapse
                  size="small"
                  ghost
                  className="[&_.ant-collapse-header]:!px-1 [&_.ant-collapse-content-box]:!px-0"
                  items={[{
                    key: 'upload',
                    label: <span className="text-xs font-medium text-muted-foreground"><PlusOutlined className="mr-1" />添加文件</span>,
                    children: (
                      <Tabs
                        size="small"
                        className="[&_.ant-tabs-nav]:!mb-2"
                        items={[
                          {
                            key: 'script', label: <span className="text-[11px]"><CodeOutlined /> 脚本</span>,
                            children: (
                              <Upload.Dragger
                                multiple accept=".sh,.py,.js,.ts,.bash" fileList={[]}
                                className="[&_.ant-upload-drag]:!py-2 [&_.ant-upload-drag]:!px-2"
                                beforeUpload={(file) => {
                                  if (file.size > 1024 * 1024) { message.error('超过 1MB'); return false; }
                                  setFormFiles((p) => [...p, { file, type: 'script' }]); return false;
                                }}
                              >
                                <p className="text-[10px] text-gray-400 m-0">.sh .py .js .ts .bash</p>
                              </Upload.Dragger>
                            ),
                          },
                          {
                            key: 'reference', label: <span className="text-[11px]"><FileTextOutlined /> 参考</span>,
                            children: (
                              <Upload.Dragger
                                multiple accept=".md,.txt,.pdf,.json,.yaml,.yml,.xml,.csv" fileList={[]}
                                className="[&_.ant-upload-drag]:!py-2 [&_.ant-upload-drag]:!px-2"
                                beforeUpload={(file) => {
                                  if (file.size > 1024 * 1024) { message.error('超过 1MB'); return false; }
                                  setFormFiles((p) => [...p, { file, type: 'reference' }]); return false;
                                }}
                              >
                                <p className="text-[10px] text-gray-400 m-0">.md .txt .json .yaml 等</p>
                              </Upload.Dragger>
                            ),
                          },
                          {
                            key: 'asset', label: <span className="text-[11px]"><PaperClipOutlined /> 资源</span>,
                            children: (
                              <Upload.Dragger
                                multiple fileList={[]}
                                className="[&_.ant-upload-drag]:!py-2 [&_.ant-upload-drag]:!px-2"
                                beforeUpload={(file) => {
                                  if (file.size > 1024 * 1024) { message.error('超过 1MB'); return false; }
                                  setFormFiles((p) => [...p, { file, type: 'asset' }]); return false;
                                }}
                              >
                                <p className="text-[10px] text-gray-400 m-0">模板、图片、字体等</p>
                              </Upload.Dragger>
                            ),
                          },
                        ]}
                      />
                    ),
                  }]}
                />

                {/* Pending files */}
                {formFiles.length > 0 && (
                  <div className="mt-2 space-y-1">
                    <Text type="secondary" className="text-[10px]">待上传 ({formFiles.length}):</Text>
                    {formFiles.map((item, i) => (
                      <div key={i} className="flex items-center justify-between text-xs bg-blue-50/60 dark:bg-blue-950/30 border border-blue-100 dark:border-blue-900/30 px-2 py-1 rounded-md">
                        <span className="truncate text-[11px]">{fileIcon(item.type)} {item.file.name}</span>
                        <Button type="text" size="small" danger icon={<DeleteOutlined />} className="!text-[10px]" onClick={() => setFormFiles((p) => p.filter((_, idx) => idx !== i))} />
                      </div>
                    ))}
                  </div>
                )}
              </>
            )}
          </div>
        </div>

        {/* ── Right: Content area ── */}
        <div className="flex-1 flex flex-col min-w-0 bg-white dark:bg-gray-950">
          {/* File header bar */}
          <div className="flex items-center gap-2 px-4 py-2 border-b border-border/60 bg-gray-50/50 dark:bg-gray-900/50 shrink-0">
            {selectedFile ? (
              <>
                <Tag color={fileColor(selectedFile.type)} className="!mr-0 !text-[10px]">{fileIcon(selectedFile.type)} {selectedFile.type}</Tag>
                <Text className="text-xs font-mono text-muted-foreground">{selectedFile.path}</Text>
                {selectedFile.size != null && (
                  <Text type="secondary" className="text-[10px] ml-auto">
                    {selectedFile.size < 1024 ? `${selectedFile.size} B` : `${(selectedFile.size / 1024).toFixed(1)} KB`}
                  </Text>
                )}
                {mode === 'view' && selectedPath !== 'SKILL.md' && (
                  <Popconfirm title="删除此文件？" onConfirm={() => handleDeleteFile(selectedFile.path)} okText="删除" cancelText="取消" okButtonProps={{ danger: true }}>
                    <Button size="small" type="text" danger icon={<DeleteOutlined />} className="!text-xs" />
                  </Popconfirm>
                )}
              </>
            ) : (
              <div className="flex items-center gap-1.5">
                <FileTextOutlined className="text-orange-500 text-xs" />
                <Text className="text-xs font-mono font-medium">{selectedPath}</Text>
              </div>
            )}
          </div>

          {/* Content body */}
          <div className="flex-1 overflow-hidden">
            {selectedPath === 'SKILL.md' && isEditing ? (
              <div className="flex h-full">
                {/* Left: editor + metadata */}
                <div className="w-1/2 border-r border-border/60 flex flex-col overflow-hidden">
                  <div className="p-4 space-y-3 overflow-auto flex-1">
                    <div>
                      <Text className="mb-1 block text-xs font-semibold text-muted-foreground">名称 <Text type="danger" className="text-xs">*</Text></Text>
                      <Input size="small" value={formName} onChange={(e) => setFormName(e.target.value)} placeholder="例如：部署 Docker 应用到 ECS" maxLength={256} className="rounded-md" />
                    </div>
                    <div>
                      <Text className="mb-1 block text-xs font-semibold text-muted-foreground">描述</Text>
                      <Input size="small" value={formDescription} onChange={(e) => setFormDescription(e.target.value)} placeholder="一句话描述技能用途" maxLength={2000} className="rounded-md" />
                    </div>
                    <div className="flex gap-3">
                      <div className="flex-1">
                        <Text className="mb-1 block text-xs font-semibold text-muted-foreground">类别</Text>
                        <Select size="small" value={formCategory} onChange={setFormCategory} className="w-full"
                          options={CATEGORIES.map((c) => ({ value: CATEGORY_MAP[c], label: c }))} />
                      </div>
                      <div className="flex-1">
                        <Text className="mb-1 block text-xs font-semibold text-muted-foreground">标签</Text>
                        <Select size="small" mode="tags" value={formTags} onChange={setFormTags} placeholder="回车添加" className="w-full" />
                      </div>
                    </div>
                    <div>
                      <Text className="mb-1 block text-xs font-semibold text-muted-foreground">触发条件</Text>
                      <Input size="small" value={formTrigger} onChange={(e) => setFormTrigger(e.target.value)} placeholder="什么时候使用这个技能" className="rounded-md" />
                    </div>
                    <div className="flex-1 flex flex-col min-h-0">
                      <Text className="mb-1 block text-xs font-semibold text-muted-foreground">SKILL.md 内容 <Text type="danger" className="text-xs">*</Text></Text>
                      <TextArea
                        value={formContent}
                        onChange={(e) => setFormContent(e.target.value)}
                        placeholder="完整的方法论：步骤、注意事项、工具依赖等..."
                        className="flex-1 font-mono text-xs rounded-md"
                        style={{ minHeight: 240, resize: 'vertical' }}
                      />
                    </div>
                  </div>
                </div>
                {/* Right: live preview */}
                <div className="w-1/2 flex flex-col overflow-hidden bg-gray-50/30 dark:bg-gray-950/50">
                  <div className="px-4 py-1.5 border-b border-border/60 bg-gray-50 dark:bg-gray-900/50 shrink-0 flex items-center gap-1.5">
                    <EyeOutlined className="text-[10px] text-muted-foreground" />
                    <Text type="secondary" className="text-[11px] font-medium">实时预览</Text>
                  </div>
                  <div className="flex-1 overflow-auto prose prose-sm max-w-none dark:prose-invert p-5">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{formContent || '*暂无内容*'}</ReactMarkdown>
                  </div>
                </div>
              </div>
            ) : (
              renderFileContent()
            )}
          </div>
        </div>
      </div>

      {/* ── Bottom action bar (edit/create mode) ── */}
      {isEditing && (
        <div className="flex items-center justify-end gap-2 px-5 py-3 border-t border-border bg-gray-50/80 dark:bg-gray-950/80">
          <Button onClick={onClose} size="middle">取消</Button>
          <Button type="primary" size="middle" icon={<SaveOutlined />} loading={submitting} onClick={handleSubmit}>
            {mode === 'edit' ? '保存更改' : '创建技能'}
          </Button>
        </div>
      )}
    </Modal>
  );
}
