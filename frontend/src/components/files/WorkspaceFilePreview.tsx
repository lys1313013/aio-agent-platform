import { useEffect, useState } from 'react';
import { App, Button, Drawer, Empty } from 'antd';
import { DownloadOutlined } from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { workspacesApi } from '@/lib/api';

const TEXT_EXTENSIONS = new Set([
  'txt', 'csv', 'json', 'xml', 'yaml', 'yml', 'toml', 'ini', 'cfg', 'conf',
  'py', 'js', 'ts', 'jsx', 'tsx', 'html', 'css', 'scss', 'less',
  'sh', 'bash', 'zsh', 'fish', 'bat', 'ps1',
  'c', 'cpp', 'h', 'hpp', 'java', 'go', 'rs', 'rb', 'php', 'swift', 'kt',
  'sql', 'r', 'lua', 'pl', 'pm', 'tcl', 'log', 'env', 'gitignore',
  'dockerignore', 'editorconfig', 'makefile', 'cmake', 'dockerfile',
]);

const MIME_TYPES: Record<string, string> = {
  csv: 'text/csv',
  gif: 'image/gif',
  html: 'text/html',
  jpeg: 'image/jpeg',
  jpg: 'image/jpeg',
  json: 'application/json',
  md: 'text/markdown',
  markdown: 'text/markdown',
  pdf: 'application/pdf',
  png: 'image/png',
  svg: 'image/svg+xml',
  txt: 'text/plain',
  webp: 'image/webp',
  xml: 'application/xml',
  yaml: 'application/yaml',
  yml: 'application/yaml',
};

export type FilePreviewKind = 'markdown' | 'text' | 'image' | 'pdf' | 'none';

function extensionOf(filename: string): string {
  const lowerName = filename.toLowerCase();
  const ext = lowerName.split('.').pop() || '';
  return TEXT_EXTENSIONS.has(lowerName) ? lowerName : ext;
}

export function inferFileMimeType(filename: string): string {
  const ext = extensionOf(filename);
  if (MIME_TYPES[ext]) return MIME_TYPES[ext];
  if (TEXT_EXTENSIONS.has(ext)) return 'text/plain';
  return 'application/octet-stream';
}

export function getFilePreviewKind(filename: string, mimeType?: string): FilePreviewKind {
  const ext = extensionOf(filename);
  const mime = mimeType || inferFileMimeType(filename);
  if (mime === 'text/markdown' || ext === 'md' || ext === 'markdown') return 'markdown';
  if (mime === 'application/pdf' || ext === 'pdf') return 'pdf';
  if (mime.startsWith('image/')) return 'image';
  if (mime.startsWith('text/') || TEXT_EXTENSIONS.has(ext)) return 'text';
  return 'none';
}

export function isEditableTextFile(filename: string): boolean {
  const kind = getFilePreviewKind(filename);
  return kind === 'markdown' || kind === 'text';
}

export async function downloadWorkspaceFile(
  workspaceId: string,
  path: string,
  filename: string,
): Promise<void> {
  const blob = await workspacesApi.downloadFile(workspaceId, path);
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function FilePreviewContent({
  filename,
  mimeType,
  text,
  previewUrl,
}: {
  filename: string;
  mimeType?: string;
  text: string;
  previewUrl: string;
}) {
  const kind = getFilePreviewKind(filename, mimeType);

  if (kind === 'markdown') {
    return (
      <div className="prose prose-sm max-w-none break-words dark:prose-invert">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{text || '（空文件）'}</ReactMarkdown>
      </div>
    );
  }
  if (kind === 'text') {
    return (
      <pre className="whitespace-pre-wrap break-words rounded bg-muted p-4 text-sm leading-relaxed">
        {text || '（空文件）'}
      </pre>
    );
  }
  if (kind === 'image' && previewUrl) {
    return <img className="mx-auto max-w-full" src={previewUrl} alt={filename} />;
  }
  if (kind === 'pdf' && previewUrl) {
    return <iframe title={filename} sandbox="" className="h-[75vh] w-full border-0" src={previewUrl} />;
  }
  return <Empty description="暂不支持在线预览，请下载后查看" />;
}

interface WorkspaceFilePreviewProps {
  workspaceId: string;
  path: string;
  filename: string;
  mimeType?: string;
  open: boolean;
  onClose: () => void;
}

export default function WorkspaceFilePreview({
  workspaceId,
  path,
  filename,
  mimeType,
  open,
  onClose,
}: WorkspaceFilePreviewProps) {
  const { message } = App.useApp();
  const [loading, setLoading] = useState(false);
  const [text, setText] = useState('');
  const [previewUrl, setPreviewUrl] = useState('');
  const kind = getFilePreviewKind(filename, mimeType);
  const resolvedMimeType = mimeType || inferFileMimeType(filename);

  useEffect(() => {
    if (!open) return undefined;
    let cancelled = false;
    let objectUrl = '';
    setLoading(true);
    setText('');
    setPreviewUrl('');

    if (kind === 'none') {
      setLoading(false);
      return undefined;
    }

    void workspacesApi.downloadFile(workspaceId, path)
      .then(async (blob) => {
        if (cancelled) return;
        if (kind === 'markdown' || kind === 'text') {
          setText(await blob.text());
        } else {
          objectUrl = URL.createObjectURL(new Blob([blob], { type: resolvedMimeType }));
          setPreviewUrl(objectUrl);
        }
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        message.error(error instanceof Error ? error.message : '文件读取失败');
        onClose();
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [kind, message, onClose, open, path, resolvedMimeType, workspaceId]);

  const handleDownload = async () => {
    try {
      await downloadWorkspaceFile(workspaceId, path, filename);
    } catch (error) {
      message.error(error instanceof Error ? error.message : '文件下载失败');
    }
  };

  return (
    <Drawer
      title={filename}
      size="large"
      open={open}
      loading={loading}
      onClose={onClose}
      extra={<Button icon={<DownloadOutlined />} onClick={handleDownload}>下载</Button>}
    >
      <FilePreviewContent
        filename={filename}
        mimeType={resolvedMimeType}
        text={text}
        previewUrl={previewUrl}
      />
    </Drawer>
  );
}
