import { useState } from 'react';
import { App, Button, Collapse, Tag } from 'antd';
import {
  DeleteOutlined,
  DownloadOutlined,
  EditOutlined,
  FileAddOutlined,
} from '@ant-design/icons';
import type { FileChangeInfo, ToolCallInfo } from '@/lib/types';
import WorkspaceFilePreview, {
  downloadWorkspaceFile,
  inferFileMimeType,
} from '@/components/files/WorkspaceFilePreview';

function formatSize(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

const ACTION_META = {
  created: { label: '已创建', color: 'success', icon: <FileAddOutlined /> },
  modified: { label: '已修改', color: 'processing', icon: <EditOutlined /> },
  deleted: { label: '已删除', color: 'default', icon: <DeleteOutlined /> },
} as const;

export function findFileChangeForPath(
  path: unknown,
  files?: FileChangeInfo[] | null,
): FileChangeInfo | undefined {
  if (typeof path !== 'string') return undefined;
  const normalized = path.replace(/\\/g, '/').replace(/\/$/, '');
  return files?.find((file) => normalized === file.path || normalized.endsWith(`/${file.path}`));
}

/**
 * 新消息优先使用后端记录的文件变更；旧消息则从成功的写入/编辑结果中恢复路径。
 * 旧消息没有创建/修改快照，这里的 action 只用于展示，不作为审计依据。
 */
export function resolveToolFileChange(
  toolCall: ToolCallInfo,
  files: FileChangeInfo[] | null | undefined,
  workspaceId: string | null | undefined,
): FileChangeInfo | undefined {
  const recorded = findFileChangeForPath(toolCall.arguments.path, files);
  if (recorded) return recorded;
  if (
    !workspaceId
    || toolCall.result?.status !== 'ok'
    || !['write_file', 'edit_file'].includes(toolCall.name)
  ) return undefined;

  const preview = toolCall.result.preview || '';
  const resultPath = preview.match(/^Written to (.+)$/m)?.[1]
    || preview.match(/^Edited (.+) successfully$/m)?.[1];
  const argumentPath = typeof toolCall.arguments.path === 'string'
    ? toolCall.arguments.path
    : '';
  const path = (resultPath || argumentPath)
    .trim()
    .replace(/\\/g, '/')
    .replace(/^\/workspace\//, '')
    .replace(/^\.\//, '')
    .replace(/^\/+/, '');
  if (!path || path.split('/').includes('..')) return undefined;

  const filename = path.split('/').pop() || path;
  return {
    action: toolCall.name === 'write_file' ? 'created' : 'modified',
    workspace_id: workspaceId,
    path,
    filename,
    mime_type: inferFileMimeType(filename),
    size: 0,
  };
}

function FileChangeAccess({
  file,
  resultText,
}: {
  file: FileChangeInfo;
  resultText?: string;
}) {
  const { message } = App.useApp();
  const [open, setOpen] = useState(false);
  const deleted = file.action === 'deleted';
  const action = ACTION_META[file.action];

  const handleDownload = async () => {
    try {
      await downloadWorkspaceFile(file.workspace_id, file.path, file.filename);
    } catch (error) {
      message.error(error instanceof Error ? error.message : '文件下载失败');
    }
  };

  const filenameIndex = resultText?.lastIndexOf(file.filename) ?? -1;
  const resultPrefix = filenameIndex >= 0 ? resultText?.slice(0, filenameIndex) : resultText;
  const resultSuffix = filenameIndex >= 0 ? resultText?.slice(filenameIndex + file.filename.length) : '';

  const content = resultText ? (
    <div className="flex items-center gap-2 rounded bg-green-50 px-2 py-2 text-xs dark:bg-green-950/20">
      <span className="min-w-0 flex-1 truncate text-left" title={resultText}>
        {resultPrefix}
        <button
          type="button"
          className="text-primary underline-offset-2 hover:underline"
          onClick={() => setOpen(true)}
        >
          {filenameIndex >= 0 ? file.filename : '打开文件'}
        </button>
        {resultSuffix}
      </span>
      <Button type="link" size="small" icon={<DownloadOutlined />} onClick={handleDownload}>下载</Button>
    </div>
  ) : (
    <div className="flex items-center gap-3 rounded-lg border border-border bg-card px-3 py-2">
      <span className="text-primary">{action.icon}</span>
      <div className="min-w-0 flex-1">
        {deleted ? (
          <div className="truncate text-sm font-medium" title={file.path}>{file.filename}</div>
        ) : (
          <button
            type="button"
            className="block max-w-full truncate text-left text-sm font-medium text-primary underline-offset-2 hover:underline"
            title={`${file.path}（点击预览）`}
            onClick={() => setOpen(true)}
          >
            {file.filename}
          </button>
        )}
        <div className="truncate text-xs text-muted-foreground" title={file.path}>
          {file.path} · {formatSize(file.size)}
        </div>
      </div>
      <Tag color={action.color}>{action.label}</Tag>
      {!deleted && (
        <Button size="small" icon={<DownloadOutlined />} onClick={handleDownload}>下载</Button>
      )}
    </div>
  );

  return (
    <>
      {content}
      {!deleted && <WorkspaceFilePreview
        workspaceId={file.workspace_id}
        path={file.path}
        filename={file.filename}
        mimeType={file.mime_type}
        open={open}
        onClose={() => setOpen(false)}
      />}
    </>
  );
}

export function FileChangeResult({
  file,
  resultText,
}: {
  file: FileChangeInfo;
  resultText: string;
}) {
  if (file.action === 'deleted') return <span>{resultText}</span>;
  return <FileChangeAccess file={file} resultText={resultText} />;
}

export default function FileChangeList({ files }: { files?: FileChangeInfo[] | null }) {
  if (!files?.length) return null;
  return (
    <Collapse
      ghost
      defaultActiveKey={['files']}
      items={[{
        key: 'files',
        label: <span className="text-sm text-muted-foreground">本轮文件变更 · {files.length}</span>,
        children: <div className="space-y-2">{files.map((file) => <FileChangeAccess key={file.path} file={file} />)}</div>,
      }]}
    />
  );
}
