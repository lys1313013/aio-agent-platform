import { useState, useEffect, useCallback } from 'react';
import {
  SaveOutlined,
  EditOutlined,
  EyeOutlined,
  IdcardOutlined,
  CloseOutlined,
  HistoryOutlined,
  RobotOutlined,
  UserOutlined,
  RollbackOutlined,
  DeleteOutlined,
} from '@ant-design/icons';
import { Button, Spin, App, Typography, Popconfirm, Tag } from 'antd';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { settingsApi } from '@/lib/api';

const { Text } = Typography;

interface PortraitVersion {
  id: string;
  content: string | null;
  source: string;
  created_at: string;
}

export default function PortraitPage() {
  const { message } = App.useApp();
  const [content, setContent] = useState('');
  const [savedContent, setSavedContent] = useState('');
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [initLoading, setInitLoading] = useState(true);

  // Version history
  const [versions, setVersions] = useState<PortraitVersion[]>([]);
  const [versionsLoading, setVersionsLoading] = useState(false);
  const [selectedVersion, setSelectedVersion] = useState<PortraitVersion | null>(null);
  const [restoring, setRestoring] = useState(false);

  useEffect(() => {
    settingsApi
      .getPersonalPortrait()
      .then((data) => {
        const val = data.personal_portrait || '';
        setContent(val);
        setSavedContent(val);
      })
      .catch((err) => message.error(`加载个人画像失败：${err.message}`))
      .finally(() => setInitLoading(false));

    loadVersions();
  }, []);

  const loadVersions = () => {
    setVersionsLoading(true);
    settingsApi
      .listPortraitVersions()
      .then((data) => setVersions(data.versions))
      .catch(() => { /* silently fail — versions are non-critical */ })
      .finally(() => setVersionsLoading(false));
  };

  const dirty = content !== savedContent;

  const handleSave = useCallback(async () => {
    setSaving(true);
    try {
      const portrait = content.trim() || null;
      await settingsApi.updatePersonalPortrait(portrait);
      setSavedContent(content);
      setEditing(false);
      setSelectedVersion(null);
      loadVersions(); // refresh version list
      message.success('个人画像已保存');
    } catch (err: any) {
      message.error(err.message || '保存失败');
    } finally {
      setSaving(false);
    }
  }, [content]);

  const handleCancel = useCallback(() => {
    setContent(savedContent);
    setEditing(false);
  }, [savedContent]);

  const handleChange = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setContent(e.target.value);
  }, []);

  const handleRestore = useCallback(async (version: PortraitVersion) => {
    setRestoring(true);
    try {
      const result = await settingsApi.restorePortraitVersion(version.id);
      const val = result.personal_portrait || '';
      setContent(val);
      setSavedContent(val);
      setSelectedVersion(null);
      loadVersions();
      message.success('已恢复至所选版本');
    } catch (err: any) {
      message.error(err.message || '恢复失败');
    } finally {
      setRestoring(false);
    }
  }, []);

  const handleDelete = useCallback(async (version: PortraitVersion) => {
    try {
      await settingsApi.deletePortraitVersion(version.id);
      if (selectedVersion?.id === version.id) setSelectedVersion(null);
      loadVersions();
      message.success('版本已删除');
    } catch (err: any) {
      message.error(err.message || '删除失败');
    }
  }, [selectedVersion]);

  const displayedContent = selectedVersion
    ? (selectedVersion.content || '')
    : savedContent;

  // Ctrl/Cmd+S to save
  useEffect(() => {
    if (!editing) return;
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 's') {
        e.preventDefault();
        if (dirty) handleSave();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [editing, dirty, handleSave]);

  const formatTime = (iso: string) => {
    const d = new Date(iso);
    const pad = (n: number) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  };

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-border bg-card">
        <div>
          <h1 className="text-xl font-bold flex items-center gap-2">
            <IdcardOutlined className="text-primary" />
            个人画像
          </h1>
          <Text type="secondary" className="text-sm">
            用 Markdown 描述自己，Agent 会据此个性化回复。
          </Text>
        </div>
        {editing ? (
          <div className="flex items-center gap-2">
            <Button icon={<CloseOutlined />} onClick={handleCancel}>
              取消
            </Button>
            <Button
              type="primary"
              icon={<SaveOutlined />}
              loading={saving}
              onClick={handleSave}
              disabled={!dirty}
            >
              保存
            </Button>
          </div>
        ) : (
          <Button type="primary" icon={<EditOutlined />} disabled={initLoading} onClick={() => setEditing(true)}>
            编辑
          </Button>
        )}
      </div>

      {initLoading ? (
        <div className="flex-1 flex items-center justify-center">
          <Spin size="large" />
        </div>
      ) : editing ? (
        /* Edit mode: split pane */
        <div className="flex-1 flex overflow-hidden">
          <div className="flex-1 flex flex-col border-r border-border">
            <div className="flex items-center gap-1.5 px-4 py-2 bg-muted/50 border-b border-border">
              <EditOutlined className="text-xs text-muted-foreground" />
              <span className="text-xs text-muted-foreground font-medium">编辑</span>
              {dirty && <span className="text-xs text-amber-500">● 未保存</span>}
            </div>
            <textarea
              className="flex-1 w-full resize-none p-4 bg-background text-foreground font-mono text-sm leading-relaxed outline-none placeholder:text-muted-foreground"
              value={content}
              onChange={handleChange}
              placeholder="介绍一下你自己，让 Agent 更懂你..."
              spellCheck={false}
            />
          </div>

          <div className="flex-1 flex flex-col">
            <div className="flex items-center gap-1.5 px-4 py-2 bg-muted/50 border-b border-border">
              <EyeOutlined className="text-xs text-muted-foreground" />
              <span className="text-xs text-muted-foreground font-medium">预览</span>
            </div>
            <div className="flex-1 overflow-y-auto p-4">
              {content.trim() ? (
                <div className="prose prose-sm dark:prose-invert max-w-none">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {content}
                  </ReactMarkdown>
                </div>
              ) : (
                <div className="text-muted-foreground text-sm italic">
                  在左侧输入 Markdown 内容，这里将实时渲染预览。
                </div>
              )}
            </div>
          </div>
        </div>
      ) : (
        /* View mode: version sidebar + rendered markdown */
        <div className="flex-1 flex overflow-hidden">
          {/* Version history sidebar */}
          <div className="w-64 flex-shrink-0 flex flex-col border-r border-border bg-card/50">
            <div className="flex items-center gap-1.5 px-4 py-3 border-b border-border">
              <HistoryOutlined className="text-sm text-muted-foreground" />
              <span className="text-sm font-medium text-foreground">历史版本</span>
            </div>
            <div className="flex-1 overflow-y-auto">
              {versionsLoading ? (
                <div className="flex justify-center py-8">
                  <Spin size="small" />
                </div>
              ) : (
                <div className="py-1">
                  {/* Current version entry */}
                  <button
                    onClick={() => setSelectedVersion(null)}
                    className={`w-full text-left px-4 py-2.5 transition border-l-2 ${
                      !selectedVersion
                        ? 'border-primary bg-primary/5'
                        : 'border-transparent hover:bg-muted/50'
                    }`}
                  >
                    <div className="flex items-center gap-1.5 text-xs text-muted-foreground mb-0.5">
                      <span className="text-[10px]">●</span>
                      <Tag
                        color="default"
                        className="text-[10px] leading-none px-1 py-0 m-0"
                      >
                        当前
                      </Tag>
                      &nbsp;
                    </div>
                  </button>
                  {versions.length === 0 ? (
                    <div className="px-4 py-6 text-center text-xs text-muted-foreground">
                      暂无历史版本
                    </div>
                  ) : (
                    versions.map((v) => {
                      const isSelected = selectedVersion?.id === v.id;
                      return (
                        <div
                          key={v.id}
                          className={`group relative transition border-l-2 ${
                            isSelected
                              ? 'border-primary bg-primary/5'
                              : 'border-transparent hover:bg-muted/50'
                          }`}
                        >
                          <button
                            onClick={() => setSelectedVersion(isSelected ? null : v)}
                            className="w-full text-left px-4 py-2.5 pr-8"
                          >
                            <div className="flex items-center gap-1.5 text-xs text-muted-foreground mb-0.5">
                              {v.source === 'ai' ? (
                                <RobotOutlined className="text-[10px] text-blue-500" />
                              ) : (
                                <UserOutlined className="text-[10px] text-green-500" />
                              )}
                              <Tag
                                color={v.source === 'ai' ? 'blue' : 'green'}
                                className="text-[10px] leading-none px-1 py-0 m-0"
                              >
                                {v.source === 'ai' ? 'AI' : '手动'}
                              </Tag>
                              <span>{formatTime(v.created_at)}</span>
                            </div>
                          </button>
                          <Popconfirm
                            title="删除此版本"
                            description="删除后不可恢复，确定删除？"
                            onConfirm={() => handleDelete(v)}
                            okText="删除"
                            cancelText="取消"
                            okButtonProps={{ danger: true }}
                          >
                            <Button
                              type="text"
                              size="small"
                              icon={<DeleteOutlined className="text-xs" />}
                              className="absolute right-1 top-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-100 transition-opacity"
                            />
                          </Popconfirm>
                        </div>
                      );
                    })
                  )}
                </div>
              )}
            </div>
          </div>

          {/* Main content area */}
          <div className="flex-1 flex flex-col overflow-hidden">
            {selectedVersion && (
              <div className="flex items-center justify-between px-4 py-2 bg-blue-50 dark:bg-blue-950 border-b border-blue-200 dark:border-blue-800">
                <Text className="text-xs text-blue-700 dark:text-blue-300">
                  正在查看历史版本（{formatTime(selectedVersion.created_at)}）
                  {selectedVersion.source === 'ai' ? ' · AI 编辑' : ' · 手动编辑'}
                </Text>
                <Popconfirm
                  title="恢复此版本"
                  description="当前画像将保存为一个新版本，然后恢复到该历史版本。"
                  onConfirm={() => handleRestore(selectedVersion)}
                  okText="确认恢复"
                  cancelText="取消"
                >
                  <Button
                    size="small"
                    type="primary"
                    ghost
                    icon={<RollbackOutlined />}
                    loading={restoring}
                  >
                    恢复此版本
                  </Button>
                </Popconfirm>
              </div>
            )}
            <div className="flex-1 overflow-y-auto p-8">
              {displayedContent.trim() ? (
                <div className="prose prose-sm dark:prose-invert max-w-3xl mx-auto">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {displayedContent}
                  </ReactMarkdown>
                </div>
              ) : (
                <div className="flex flex-col items-center justify-center h-full text-muted-foreground">
                  <IdcardOutlined className="text-4xl mb-4 opacity-30" />
                  <p className="text-sm">尚未设置个人画像</p>
                  <p className="text-xs mt-1 opacity-70">
                    点击右上角"编辑"按钮，用 Markdown 描述自己，让 Agent 更懂你。
                  </p>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
