import { useCallback, useEffect, useState } from 'react';
import {
  FolderOutlined,
  FolderOpenOutlined,
  FileOutlined,
  PlusOutlined,
  DeleteOutlined,
  DownloadOutlined,
  UploadOutlined,
  ReloadOutlined,
  HomeOutlined,
} from '@ant-design/icons';
import {
  Button,
  Empty,
  Input,
  Modal,
  Popconfirm,
  Spin,
  Table,
  Tag,
  Upload,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { workspacesApi } from '@/lib/api';
import type { Workspace, WorkspaceFileEntry } from '@/lib/api';
import { cn } from '@/lib/utils';

function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** i).toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

export default function WorkspacesPage() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [currentPath, setCurrentPath] = useState('');
  const [entries, setEntries] = useState<WorkspaceFileEntry[]>([]);
  const [source, setSource] = useState<'sandbox' | 'storage'>('storage');
  const [loadingWs, setLoadingWs] = useState(false);
  const [loadingFiles, setLoadingFiles] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [newName, setNewName] = useState('');
  const [newDesc, setNewDesc] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const fetchWorkspaces = useCallback(async () => {
    setLoadingWs(true);
    try {
      const list = await workspacesApi.list();
      setWorkspaces(list);
      setSelectedId((prev) => {
        if (prev && list.some((w) => w.id === prev)) return prev;
        return list[0]?.id ?? null;
      });
    } catch {
      message.error('加载工作区失败');
    } finally {
      setLoadingWs(false);
    }
  }, []);

  const fetchFiles = useCallback(async (workspaceId: string, path: string) => {
    setLoadingFiles(true);
    try {
      const resp = await workspacesApi.listFiles(workspaceId, path);
      const sorted = [...resp.entries].sort((a, b) => {
        if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
        return a.path.localeCompare(b.path);
      });
      setEntries(sorted);
      setSource(resp.source);
    } catch {
      message.error('加载文件列表失败');
      setEntries([]);
    } finally {
      setLoadingFiles(false);
    }
  }, []);

  useEffect(() => {
    fetchWorkspaces();
  }, [fetchWorkspaces]);

  useEffect(() => {
    if (selectedId) {
      fetchFiles(selectedId, currentPath);
    } else {
      setEntries([]);
    }
  }, [selectedId, currentPath, fetchFiles]);

  const selectedWorkspace = workspaces.find((w) => w.id === selectedId) ?? null;

  const handleSelectWorkspace = (id: string) => {
    if (id === selectedId) return;
    setSelectedId(id);
    setCurrentPath('');
  };

  const handleCreate = async () => {
    if (!newName.trim()) return;
    setSubmitting(true);
    try {
      const ws = await workspacesApi.create(newName.trim(), newDesc.trim() || undefined);
      message.success('工作区已创建');
      setCreateOpen(false);
      setNewName('');
      setNewDesc('');
      await fetchWorkspaces();
      setSelectedId(ws.id);
      setCurrentPath('');
    } catch {
      message.error('创建失败');
    } finally {
      setSubmitting(false);
    }
  };

  const handleDeleteWorkspace = async (ws: Workspace) => {
    try {
      await workspacesApi.delete(ws.id);
      message.success('工作区已删除');
      fetchWorkspaces();
    } catch (e) {
      message.error(e instanceof Error ? e.message : '删除失败');
    }
  };

  const joinPath = (base: string, name: string) => (base ? `${base}/${name}` : name);

  const handleDownload = async (entry: WorkspaceFileEntry) => {
    if (!selectedId) return;
    const fullPath = joinPath(currentPath, entry.path);
    try {
      const blob = await workspacesApi.downloadFile(selectedId, fullPath);
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

  const handleDeleteFile = async (entry: WorkspaceFileEntry) => {
    if (!selectedId) return;
    const fullPath = joinPath(currentPath, entry.path);
    try {
      await workspacesApi.deleteFile(selectedId, fullPath);
      message.success('已删除');
      fetchFiles(selectedId, currentPath);
    } catch {
      message.error('删除失败');
    }
  };

  const breadcrumbs = currentPath ? currentPath.split('/') : [];

  const columns: ColumnsType<WorkspaceFileEntry> = [
    {
      title: '名称',
      dataIndex: 'path',
      key: 'path',
      render: (name: string, record) =>
        record.is_dir ? (
          <a
            className="flex items-center gap-2"
            onClick={() => setCurrentPath(joinPath(currentPath, name))}
          >
            <FolderOutlined className="text-amber-500" />
            {name}
          </a>
        ) : (
          <span className="flex items-center gap-2">
            <FileOutlined className="text-muted-foreground" />
            {name}
          </span>
        ),
    },
    {
      title: '大小',
      dataIndex: 'size',
      key: 'size',
      width: 120,
      render: (size: number, record) => (record.is_dir ? '—' : formatBytes(size)),
    },
    {
      title: '操作',
      key: 'actions',
      width: 140,
      render: (_, record) => (
        <span className="flex items-center gap-1">
          {!record.is_dir && (
            <Button
              type="text"
              size="small"
              icon={<DownloadOutlined />}
              onClick={() => handleDownload(record)}
            />
          )}
          <Popconfirm
            title={`删除${record.is_dir ? '文件夹' : '文件'} "${record.path}"？`}
            okText="删除"
            cancelText="取消"
            okButtonProps={{ danger: true }}
            onConfirm={() => handleDeleteFile(record)}
          >
            <Button type="text" size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </span>
      ),
    },
  ];

  return (
    <div className="flex flex-1 flex-col overflow-hidden bg-muted/15">
      <div className="flex w-full flex-1 flex-col overflow-hidden px-6 py-5">
        {/* Header */}
        <div className="mb-4 flex flex-shrink-0 items-center justify-between">
          <h1 className="flex items-center gap-2 text-xl font-semibold">
            <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary/10 text-primary">
              <FolderOpenOutlined />
            </span>
            工作区文件
          </h1>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
            新建工作区
          </Button>
        </div>

        <div className="flex flex-1 gap-3 overflow-hidden">
          {/* Workspace list */}
          <div className="w-64 flex-shrink-0 overflow-y-auto rounded-xl border border-border/70 bg-card p-2 shadow-sm">
            {loadingWs ? (
              <div className="flex justify-center py-10">
                <Spin />
              </div>
            ) : workspaces.length === 0 ? (
              <Empty className="py-10" description="暂无工作区" />
            ) : (
              workspaces.map((ws) => (
                <div
                  key={ws.id}
                  onClick={() => handleSelectWorkspace(ws.id)}
                  className={cn(
                    'group mb-1 flex cursor-pointer items-center justify-between rounded-lg px-3 py-2.5 transition last:mb-0',
                    ws.id === selectedId
                      ? 'bg-primary/[0.08] ring-1 ring-primary/15'
                      : 'hover:bg-muted/60',
                  )}
                >
                  <div className="flex min-w-0 items-center gap-2.5">
                    <span className={cn(
                      'flex h-8 w-8 shrink-0 items-center justify-center rounded-lg',
                      ws.id === selectedId ? 'bg-primary/10 text-primary' : 'bg-muted text-muted-foreground',
                    )}>
                      <FolderOutlined />
                    </span>
                    <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-sm font-medium text-foreground">{ws.name}</span>
                      {ws.is_default && <Tag variant="filled" color="blue" className="!m-0 text-[10px]">默认</Tag>}
                    </div>
                    <div className="text-xs text-muted-foreground">
                      {ws.file_count} 个文件 · {formatBytes(ws.total_size_bytes)}
                    </div>
                    </div>
                  </div>
                  {!ws.is_default && (
                    <Popconfirm
                      title={`删除工作区 "${ws.name}" 及其全部文件？`}
                      okText="删除"
                      cancelText="取消"
                      okButtonProps={{ danger: true }}
                      onConfirm={() => handleDeleteWorkspace(ws)}
                    >
                      <Button
                        type="text"
                        size="small"
                        danger
                        icon={<DeleteOutlined />}
                        className="opacity-0 group-hover:opacity-100"
                        onClick={(e) => e.stopPropagation()}
                      />
                    </Popconfirm>
                  )}
                </div>
              ))
            )}
          </div>

          {/* File browser */}
          <div className="flex flex-1 flex-col overflow-hidden rounded-xl border border-border/70 bg-card shadow-sm">
            {selectedWorkspace ? (
              <>
                {/* Toolbar */}
                <div className="flex flex-shrink-0 items-center justify-between gap-3 border-b border-border/70 px-4 py-3">
                  <div className="flex items-center gap-1 text-sm min-w-0">
                    <a
                      className={cn('flex items-center gap-1', !currentPath && 'text-muted-foreground')}
                      onClick={() => setCurrentPath('')}
                    >
                      <HomeOutlined />
                      根目录
                    </a>
                    {breadcrumbs.map((seg, idx) => (
                      <span key={idx} className="flex items-center gap-1">
                        <span className="text-muted-foreground">/</span>
                        <a onClick={() => setCurrentPath(breadcrumbs.slice(0, idx + 1).join('/'))}>
                          {seg}
                        </a>
                      </span>
                    ))}
                  </div>
                  <div className="flex items-center gap-2 flex-shrink-0">
                    {source === 'sandbox' ? (
                      <Tag color="green">沙箱实时</Tag>
                    ) : (
                      <Tag>存储快照</Tag>
                    )}
                    <Upload
                      showUploadList={false}
                      customRequest={async ({ file, onSuccess, onError }) => {
                        try {
                          const target = joinPath(currentPath, (file as File).name);
                          await workspacesApi.uploadFile(selectedWorkspace.id, target, file as File);
                          message.success(`已上传 ${(file as File).name}`);
                          onSuccess?.({});
                          fetchFiles(selectedWorkspace.id, currentPath);
                        } catch (e) {
                          message.error('上传失败');
                          onError?.(e as Error);
                        }
                      }}
                    >
                      <Button size="small" icon={<UploadOutlined />}>
                        上传
                      </Button>
                    </Upload>
                    <Button
                      size="small"
                      icon={<ReloadOutlined />}
                      onClick={() => fetchFiles(selectedWorkspace.id, currentPath)}
                    />
                  </div>
                </div>

                {/* File table */}
                <div className="flex-1 overflow-y-auto">
                  <Table<WorkspaceFileEntry>
                    rowKey="path"
                    columns={columns}
                    dataSource={entries}
                    loading={loadingFiles}
                    pagination={false}
                    size="middle"
                    locale={{ emptyText: <Empty description="空目录" /> }}
                  />
                </div>
              </>
            ) : (
              <div className="flex flex-1 items-center justify-center">
                <Empty description="请选择左侧工作区" />
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Create workspace modal */}
      <Modal
        title="新建工作区"
        open={createOpen}
        onOk={handleCreate}
        onCancel={() => setCreateOpen(false)}
        confirmLoading={submitting}
        okText="创建"
        cancelText="取消"
        okButtonProps={{ disabled: !newName.trim() }}
      >
        <div className="flex flex-col gap-3 py-2">
          <Input
            placeholder="名称"
            value={newName}
            maxLength={128}
            onChange={(e) => setNewName(e.target.value)}
            onPressEnter={handleCreate}
          />
          <Input.TextArea
            placeholder="描述（可选）"
            value={newDesc}
            rows={2}
            onChange={(e) => setNewDesc(e.target.value)}
          />
        </div>
      </Modal>
    </div>
  );
}
