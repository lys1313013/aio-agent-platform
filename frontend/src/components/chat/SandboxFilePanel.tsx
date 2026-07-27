import { useCallback, useEffect, useState } from 'react';
import {
  FolderOutlined,
  FileOutlined,
  ReloadOutlined,
  HomeOutlined,
  RightOutlined,
  LeftOutlined,
  FolderOpenOutlined,
  DownloadOutlined,
  DeleteOutlined,
  EyeOutlined,
  EditOutlined,
} from '@ant-design/icons';
import { App, Button, Empty, Input, Modal, Popconfirm, Spin, Tag, Tooltip, Typography } from 'antd';
import { workspacesApi } from '@/lib/api';
import type { WorkspaceFileEntry } from '@/lib/api';
import { cn } from '@/lib/utils';

const { Text } = Typography;
const { TextArea } = Input;

const TEXT_EXTS = new Set([
  'txt', 'md', 'json', 'xml', 'yaml', 'yml', 'toml', 'ini', 'cfg', 'conf',
  'py', 'js', 'ts', 'jsx', 'tsx', 'html', 'css', 'scss', 'less',
  'sh', 'bash', 'zsh', 'fish', 'bat', 'ps1',
  'c', 'cpp', 'h', 'hpp', 'java', 'go', 'rs', 'rb', 'php', 'swift', 'kt',
  'sql', 'r', 'lua', 'pl', 'pm', 'tcl',
  'csv', 'log', 'env', 'gitignore', 'dockerignore', 'editorconfig',
  'makefile', 'cmake', 'Dockerfile', 'dockerfile',
]);

function isTextFile(name: string): boolean {
  const ext = name.split('.').pop()?.toLowerCase() ?? '';
  return TEXT_EXTS.has(ext) || TEXT_EXTS.has(name.toLowerCase());
}

interface SandboxFilePanelProps {
  workspaceId: string | null;
}

export default function SandboxFilePanel({ workspaceId }: SandboxFilePanelProps) {
  const { message } = App.useApp();
  const [collapsed, setCollapsed] = useState(true);
  const [currentPath, setCurrentPath] = useState('');
  const [entries, setEntries] = useState<WorkspaceFileEntry[]>([]);
  const [source, setSource] = useState<'sandbox' | 'storage'>('storage');
  const [loading, setLoading] = useState(false);

  // View/Edit modal state
  const [modalOpen, setModalOpen] = useState(false);
  const [modalEntry, setModalEntry] = useState<WorkspaceFileEntry | null>(null);
  const [modalContent, setModalContent] = useState('');
  const [modalLoading, setModalLoading] = useState(false);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);

  const fetchFiles = useCallback(
    async (path: string) => {
      if (!workspaceId) return;
      setLoading(true);
      try {
        const resp = await workspacesApi.listFiles(workspaceId, path);
        const sorted = [...resp.entries].sort((a, b) => {
          if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
          return a.path.localeCompare(b.path);
        });
        setEntries(sorted);
        setSource(resp.source);
      } catch {
        setEntries([]);
      } finally {
        setLoading(false);
      }
    },
    [workspaceId],
  );

  useEffect(() => {
    if (!collapsed && workspaceId) {
      fetchFiles(currentPath);
    }
  }, [collapsed, workspaceId, currentPath, fetchFiles]);

  const joinPath = (base: string, name: string) => (base ? `${base}/${name}` : name);

  const navigateTo = (entry: WorkspaceFileEntry) => {
    if (entry.is_dir) {
      setCurrentPath(joinPath(currentPath, entry.path));
    }
  };

  const loadFileContent = async (entry: WorkspaceFileEntry): Promise<string | null> => {
    if (!workspaceId || entry.is_dir) return null;
    const fullPath = joinPath(currentPath, entry.path);
    try {
      const blob = await workspacesApi.downloadFile(workspaceId, fullPath);
      return await blob.text();
    } catch {
      return null;
    }
  };

  const openModal = async (entry: WorkspaceFileEntry, startEditing: boolean) => {
    if (!workspaceId || entry.is_dir) return;
    setModalEntry(entry);
    setModalContent('');
    setEditing(startEditing);
    setModalOpen(true);

    if (!isTextFile(entry.path)) {
      return;
    }

    setModalLoading(true);
    const text = await loadFileContent(entry);
    setModalContent(text ?? '');
    setModalLoading(false);
  };

  const handlePreview = (entry: WorkspaceFileEntry) => openModal(entry, false);
  const handleEdit = (entry: WorkspaceFileEntry) => openModal(entry, true);

  const handleSave = async () => {
    if (!workspaceId || !modalEntry) return;
    const fullPath = joinPath(currentPath, modalEntry.path);
    setSaving(true);
    try {
      const blob = new Blob([modalContent], { type: 'text/plain' });
      const file = new File([blob], modalEntry.path, { type: 'text/plain' });
      await workspacesApi.uploadFile(workspaceId, fullPath, file);
      message.success(`已保存 ${modalEntry.path}`);
      setEditing(false);
      fetchFiles(currentPath);
    } catch {
      message.error('保存失败');
    } finally {
      setSaving(false);
    }
  };

  const handleDownload = async (entry: WorkspaceFileEntry) => {
    if (!workspaceId || entry.is_dir) return;
    const fullPath = joinPath(currentPath, entry.path);
    try {
      const blob = await workspacesApi.downloadFile(workspaceId, fullPath);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = entry.path;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      message.error('下载失败');
    }
  };

  const handleDelete = async (entry: WorkspaceFileEntry) => {
    if (!workspaceId) return;
    const fullPath = joinPath(currentPath, entry.path);
    try {
      await workspacesApi.deleteFile(workspaceId, fullPath);
      message.success(`已删除 ${entry.path}`);
      fetchFiles(currentPath);
    } catch {
      message.error('删除失败');
    }
  };

  const handleCloseModal = () => {
    setModalOpen(false);
    setEditing(false);
  };

  const breadcrumbs = currentPath ? currentPath.split('/') : [];

  if (!workspaceId) return null;

  return (
    <div className="relative flex items-stretch flex-shrink-0">
      {/* Toggle button */}
      <Tooltip title={collapsed ? '打开沙箱文件' : '收起沙箱文件'}>
        <button
          onClick={() => setCollapsed(!collapsed)}
          className={cn(
            'absolute top-1/2 -translate-y-1/2 z-20 flex items-center justify-center',
            'w-6 h-16 rounded-l-md border border-r-0 border-border bg-card hover:bg-muted transition',
            'text-muted-foreground hover:text-foreground',
            collapsed ? 'right-0' : '-left-6',
          )}
        >
          {collapsed ? <LeftOutlined className="text-[10px]" /> : <RightOutlined className="text-[10px]" />}
        </button>
      </Tooltip>

      {/* Panel */}
      <div
        className={cn(
          'flex flex-col border-l border-border bg-card transition-all duration-200 overflow-hidden',
          collapsed ? 'w-0 border-l-0' : 'w-72',
        )}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-3 py-2 border-b border-border flex-shrink-0">
          <Text strong className="text-sm flex items-center gap-1.5">
            <FolderOpenOutlined className="text-amber-500" />
            沙箱文件
          </Text>
          <div className="flex items-center gap-1">
            {source === 'sandbox' ? (
              <Tag color="green" className="text-[10px] leading-none px-1.5 py-0 m-0">实时</Tag>
            ) : (
              <Tag className="text-[10px] leading-none px-1.5 py-0 m-0">快照</Tag>
            )}
            <Button
              type="text"
              size="small"
              icon={<ReloadOutlined />}
              onClick={() => fetchFiles(currentPath)}
            />
          </div>
        </div>

        {/* Breadcrumbs */}
        <div className="flex items-center gap-0.5 px-3 py-1.5 border-b border-border flex-shrink-0 overflow-x-auto text-xs">
          <a
            className={cn('flex-shrink-0', !currentPath && 'text-muted-foreground pointer-events-none')}
            onClick={() => setCurrentPath('')}
          >
            <HomeOutlined className="text-xs" />
          </a>
          {breadcrumbs.map((seg, idx) => (
            <span key={idx} className="flex items-center gap-0.5 flex-shrink-0">
              <span className="text-muted-foreground">/</span>
              <a
                className="truncate max-w-16"
                onClick={() => setCurrentPath(breadcrumbs.slice(0, idx + 1).join('/'))}
              >
                {seg}
              </a>
            </span>
          ))}
        </div>

        {/* File list */}
        <div className="flex-1 overflow-y-auto">
          {loading ? (
            <div className="flex justify-center py-8">
              <Spin size="small" />
            </div>
          ) : entries.length === 0 ? (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description="空目录"
              className="py-6"
            />
          ) : (
            entries.map((entry) => (
              <div
                key={entry.path}
                className={cn(
                  'group flex items-center gap-2 px-2 py-1 text-sm border-b border-border/50 last:border-b-0',
                  entry.is_dir && 'cursor-pointer hover:bg-muted/50 transition',
                )}
              >
                <div
                  className="flex items-center gap-2 flex-1 min-w-0"
                  onClick={() => navigateTo(entry)}
                >
                  {entry.is_dir ? (
                    <FolderOutlined className="text-amber-500 text-sm flex-shrink-0" />
                  ) : (
                    <FileOutlined className="text-muted-foreground text-sm flex-shrink-0" />
                  )}
                  <span className="truncate">{entry.path}</span>
                </div>

                {/* Actions */}
                <span className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition flex-shrink-0">
                  {!entry.is_dir && (
                    <Tooltip title="预览">
                      <Button
                        type="text"
                        size="small"
                        icon={<EyeOutlined className="text-xs" />}
                        onClick={(e) => { e.stopPropagation(); handlePreview(entry); }}
                      />
                    </Tooltip>
                  )}
                  {!entry.is_dir && isTextFile(entry.path) && (
                    <Tooltip title="编辑">
                      <Button
                        type="text"
                        size="small"
                        icon={<EditOutlined className="text-xs" />}
                        onClick={(e) => { e.stopPropagation(); handleEdit(entry); }}
                      />
                    </Tooltip>
                  )}
                  {!entry.is_dir && (
                    <Tooltip title="下载">
                      <Button
                        type="text"
                        size="small"
                        icon={<DownloadOutlined className="text-xs" />}
                        onClick={(e) => { e.stopPropagation(); handleDownload(entry); }}
                      />
                    </Tooltip>
                  )}
                  <Popconfirm
                    title={`删除${entry.is_dir ? '文件夹' : '文件'} "${entry.path}"？`}
                    okText="删除"
                    cancelText="取消"
                    okButtonProps={{ danger: true }}
                    onConfirm={() => handleDelete(entry)}
                  >
                    <Tooltip title="删除">
                      <Button
                        type="text"
                        size="small"
                        danger
                        icon={<DeleteOutlined className="text-xs" />}
                        onClick={(e) => e.stopPropagation()}
                      />
                    </Tooltip>
                  </Popconfirm>
                </span>
              </div>
            ))
          )}
        </div>
      </div>

      {/* Preview / Edit modal */}
      <Modal
        title={
          <span className="flex items-center gap-2">
            {modalEntry?.path}
            {editing && <Tag color="orange" className="text-[10px] m-0">编辑中</Tag>}
          </span>
        }
        open={modalOpen}
        onCancel={handleCloseModal}
        width={720}
        footer={
          modalEntry && isTextFile(modalEntry.path) ? (
            <div className="flex justify-between">
              <Button
                onClick={() => setEditing(!editing)}
                icon={editing ? <EyeOutlined /> : <EditOutlined />}
              >
                {editing ? '切换预览' : '编辑'}
              </Button>
              {editing && (
                <div className="flex gap-2">
                  <Button onClick={() => { setEditing(false); setModalContent(modalContent); }}>
                    取消
                  </Button>
                  <Button type="primary" loading={saving} onClick={handleSave}>
                    保存
                  </Button>
                </div>
              )}
            </div>
          ) : null
        }
        destroyOnClose
      >
        {modalEntry && !isTextFile(modalEntry.path) ? (
          <div className="text-center py-8 text-muted-foreground">
            无法预览此文件类型（二进制或未知格式）
          </div>
        ) : modalLoading ? (
          <div className="flex justify-center py-8">
            <Spin />
          </div>
        ) : editing ? (
          <TextArea
            value={modalContent}
            onChange={(e) => setModalContent(e.target.value)}
            className="font-mono text-xs"
            autoSize={{ minRows: 6, maxRows: 20 }}
            spellCheck={false}
          />
        ) : (
          <pre className="max-h-96 overflow-auto rounded bg-muted p-4 text-xs leading-relaxed whitespace-pre-wrap break-all">
            {modalContent || '（空文件）'}
          </pre>
        )}
      </Modal>
    </div>
  );
}
