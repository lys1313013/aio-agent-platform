import { useState, useRef, useCallback, useEffect, useMemo, type ReactNode, type FormEvent, type KeyboardEvent, type DragEvent, type ClipboardEvent } from 'react';
import { ArrowUpOutlined, StopOutlined, PaperClipOutlined, CloseOutlined, LoadingOutlined, FileTextOutlined, FolderOutlined, ThunderboltOutlined, EditOutlined } from '@ant-design/icons';
import { Input, Button, App, Image, Tooltip, Select } from 'antd';
import { chatApi } from '@/lib/api';
import { useChatStore } from '@/stores/chatStore';
import { useCommandStore } from '@/stores/commandStore';
import { MAX_QUEUED_MESSAGES, type QueuedMessage } from '@/hooks/useMessageQueue';
import type { ChatAttachment, CommandMeta, FileAttachmentRef } from '@/lib/types';
import CommandMenu from './CommandMenu';
import RoomMentionInput from './RoomMentionInput';
import { editMentionDraft, type MentionCandidate, type MentionDraft } from '@/lib/roomMentions';

const { TextArea } = Input;

const MAX_ATTACHMENTS = 4;
const MAX_IMAGE_SIZE = 10 * 1024 * 1024; // 10MB
const MAX_FILE_SIZE = 500 * 1024 * 1024; // 500MB
const ALLOWED_IMAGE_TYPES = ['image/jpeg', 'image/png', 'image/gif', 'image/webp'];

interface PendingAttachment {
  localId: string;
  status: 'uploading' | 'done' | 'error';
  thumb?: string;
  attachment?: ChatAttachment;
  error?: string;
}

interface PendingFile {
  localId: string;
  status: 'uploading' | 'done' | 'error';
  fileName: string;
  fileSize: number;
  fileRef?: FileAttachmentRef;
  error?: string;
}

interface StarterPrompt {
  label: string;
  icon: string;
}

export interface ChatInputProps {
  onSend: (text: string, attachments: ChatAttachment[], fileAttachments?: FileAttachmentRef[]) => void | boolean | Promise<void | boolean>;
  onStop?: () => void;
  disabled?: boolean;
  isStreaming?: boolean;
  sessionId?: string | null;
  /** Ensures a session exists, creating one if needed. Returns the session id. */
  onEnsureSession?: () => Promise<string | null>;
  starterPrompts?: StarterPrompt[];
  onStarterPromptClick?: (prompt: string) => void;
  /** Queued messages waiting for the current turn to finish. */
  queue?: QueuedMessage[];
  /** Enqueue a message while streaming. Returns false when the queue is full. */
  onQueue?: (text: string, attachments: ChatAttachment[], fileAttachments?: FileAttachmentRef[]) => boolean;
  /** Interrupt the current turn and send this queued message immediately. */
  onQueueSendNow?: (id: string) => void;
  onQueueRemove?: (id: string) => void;
  /** 极简模式：隐藏工作区选择、图片/文件上传、starter 提示（宠物对话等轻量场景） */
  simple?: boolean;
  /** 门户模式：隐藏斜杠命令、工作区选择器与工作区文件上传，保留图片附件与 starter 提示 */
  portal?: boolean;
  /** A room owns its workspace; never switch the global chat workspace here. */
  fixedWorkspace?: boolean;
  mentions?: { value: MentionDraft; onChange: (draft: MentionDraft) => void; candidates: MentionCandidate[] };
  context?: ReactNode;
  sendDisabled?: boolean;
  stopping?: boolean;
  attachmentLimits?: { images: number; files: number; total: number };
}

function formatSize(bytes: number): string {
  if (bytes > 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  if (bytes > 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${bytes} B`;
}

export default function ChatInput({ onSend, onStop, disabled, isStreaming, sessionId, onEnsureSession, starterPrompts, onStarterPromptClick, queue, onQueue, onQueueSendNow, onQueueRemove, simple, portal, fixedWorkspace, mentions, context, sendDisabled, stopping, attachmentLimits }: ChatInputProps) {
  const [localInput, setLocalInput] = useState('');
  const input = mentions ? mentions.value.text : localInput;
  const setInput = (value: string) => mentions ? mentions.onChange(editMentionDraft(mentions.value, value)) : setLocalInput(value);
  const [sending, setSending] = useState(false);
  const sendingRef = useRef(false);
  const limits = attachmentLimits ?? { images: MAX_ATTACHMENTS, files: MAX_ATTACHMENTS, total: MAX_ATTACHMENTS };
  const commandsEnabled = !simple && !portal && !mentions;
  const [pending, setPending] = useState<PendingAttachment[]>([]);
  const [pendingFiles, setPendingFiles] = useState<PendingFile[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const [commandIndex, setCommandIndex] = useState(0);
  const [commandDismissed, setCommandDismissed] = useState(false);
  const textareaRef = useRef<any>(null);
  const attachmentInputRef = useRef<HTMLInputElement>(null);
  const { message } = App.useApp();
  const { workspaces, selectedWorkspaceId, isWorkspacesLoading, setSelectedWorkspace, loadWorkspaces } = useChatStore();

  // Load the slash-command catalog once so the palette can be populated.
  const loadCommands = useCommandStore((s) => s.load);
  const searchCommands = useCommandStore((s) => s.search);
  useEffect(() => {
    if (commandsEnabled) loadCommands();
  }, [loadCommands, commandsEnabled]);

  const isCommandInput = commandsEnabled && input.startsWith('/');
  const commandMenuOpen = isCommandInput && !commandDismissed;
  const commandItems = useMemo(
    () => (commandMenuOpen ? searchCommands(input.slice(1)) : []),
    [commandMenuOpen, input, searchCommands],
  );

  // Reset palette navigation whenever the input changes or the palette closes.
  useEffect(() => {
    setCommandIndex(0);
    setCommandDismissed(false);
  }, [input]);

  const selectCommand = (cmd: CommandMeta) => {
    setInput(`/${cmd.name} `);
    setCommandDismissed(true);
    requestAnimationFrame(() => textareaRef.current?.focus());
  };

  // Load workspaces on mount
  useEffect(() => {
    if (!simple && !portal && !fixedWorkspace) loadWorkspaces();
  }, [loadWorkspaces, simple, portal, fixedWorkspace]);

  const readAsDataURL = (file: File): Promise<string> => {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result as string);
      reader.onerror = reject;
      reader.readAsDataURL(file);
    });
  };

  const totalPending = () =>
    pending.filter((p) => p.status !== 'error').length +
    pendingFiles.filter((p) => p.status !== 'error').length;

  const counts = useRef({ images: 0, files: 0 });
  counts.current = { images: pending.filter(p => p.status !== 'error').length, files: pendingFiles.filter(p => p.status !== 'error').length };
  const reserveAttachment = (kind: 'images' | 'files') => {
    const count = counts.current;
    if (count.images + count.files >= limits.total || count[kind] >= limits[kind]) {
      message.warning(kind === 'images' ? `最多 ${limits.images} 张图片，共 ${limits.total} 个附件` : `最多 ${limits.files} 个文件，共 ${limits.total} 个附件`);
      return false;
    }
    count[kind] += 1;
    return true;
  };

  // ---- Image upload handling ----

  const handleImageFiles = useCallback(async (files: FileList | File[]) => {
    const imageFiles = Array.from(files).filter((f) => ALLOWED_IMAGE_TYPES.includes(f.type));

    if (imageFiles.length === 0) {
      message.warning('仅支持 JPG/PNG/GIF/WebP 格式的图片');
      return;
    }

    for (const file of imageFiles) {
      if (file.size > MAX_IMAGE_SIZE) {
        message.error(`${file.name} 超过 ${MAX_IMAGE_SIZE / (1024 * 1024)}MB`);
        continue;
      }

      if (!reserveAttachment('images')) break;

      const localId = `img-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      setPending((prev) => [...prev, { localId, status: 'uploading' }]);

      try {
        const thumb = await readAsDataURL(file);
        setPending(prev => prev.map(p => p.localId === localId ? { ...p, thumb } : p));
        const attachment = await chatApi.uploadAttachment(file, sessionId);
        setPending((prev) => prev.map((p) => (p.localId === localId ? { ...p, status: 'done', attachment } : p)));
      } catch (err: any) {
        const errMsg = err?.message || '上传失败';
        setPending((prev) => prev.map((p) => (p.localId === localId ? { ...p, status: 'error', error: errMsg } : p)));
        message.error(`${file.name}: ${errMsg}`);
      }
    }
  }, [sessionId, message, limits.images, limits.total]);

  // ---- File upload handling ----

  const handleFileUpload = useCallback(async (files: FileList | File[]) => {
    const fileArr = Array.from(files);

    let effectiveSessionId = sessionId;
    if (!effectiveSessionId) {
      effectiveSessionId = onEnsureSession ? await onEnsureSession() : null;
      if (!effectiveSessionId) {
        message.warning('请先创建会话');
        return;
      }
    }

    for (const file of fileArr) {
      if (file.size > MAX_FILE_SIZE) {
        message.error(`${file.name} 超过 ${MAX_FILE_SIZE / (1024 * 1024)}MB`);
        continue;
      }

      if (!reserveAttachment('files')) break;

      const localId = `file-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

      setPendingFiles((prev) => [...prev, {
        localId,
        status: 'uploading',
        fileName: file.name,
        fileSize: file.size,
      }]);

      try {
        const fileRef = await chatApi.uploadFile(file, effectiveSessionId);
        setPendingFiles((prev) => prev.map((p) => (p.localId === localId ? { ...p, status: 'done', fileRef } : p)));
      } catch (err: any) {
        const errMsg = err?.message || '上传失败';
        setPendingFiles((prev) => prev.map((p) => (p.localId === localId ? { ...p, status: 'error', error: errMsg } : p)));
        message.error(`${file.name}: ${errMsg}`);
      }
    }
  }, [sessionId, onEnsureSession, message, limits.files, limits.total]);

  const handleAttachmentFiles = (files: FileList | File[]) => {
    if (disabled || sendingRef.current || sendDisabled) return;
    const selectedFiles = Array.from(files);
    const images = selectedFiles.filter((file) => ALLOWED_IMAGE_TYPES.includes(file.type));
    const otherFiles = selectedFiles.filter((file) => !ALLOWED_IMAGE_TYPES.includes(file.type));

    if (images.length > 0) handleImageFiles(images);
    if (otherFiles.length > 0) {
      if (portal) {
        message.warning('当前仅支持图片附件');
      } else {
        handleFileUpload(otherFiles);
      }
    }
  };

  const removeFileAttachment = (localId: string) => {
    setPendingFiles((prev) => prev.filter((p) => p.localId !== localId));
  };

  // ---- Form submit ----

  const handleQueueEdit = (item: QueuedMessage) => {
    if (!onQueueRemove || disabled) return;
    if (input.trim() || pending.length > 0 || pendingFiles.length > 0) {
      message.warning('请先发送或清空输入框中的草稿，再编辑排队消息');
      textareaRef.current?.focus({ cursor: 'end' });
      return;
    }

    // Remove first so the original message cannot auto-send while being edited.
    onQueueRemove(item.id);
    setInput(item.content);
    setPending(item.attachments.map((attachment, index) => ({
      localId: `${item.id}-img-${index}`,
      status: 'done',
      thumb: attachment.url,
      attachment,
    })));
    setPendingFiles((item.fileAttachments ?? []).map((fileRef, index) => ({
      localId: `${item.id}-file-${index}`,
      status: 'done',
      fileName: fileRef.filename,
      fileSize: fileRef.size,
      fileRef,
    })));
    requestAnimationFrame(() => textareaRef.current?.focus({ cursor: 'end' }));
  };

  const handleSubmit = async (e?: FormEvent) => {
    e?.preventDefault();
    if (disabled || sendDisabled || sendingRef.current || pending.some(p => p.status === 'uploading') || pendingFiles.some(p => p.status === 'uploading')) return;
    const text = input.trim();
    const attachments = pending.filter((p) => p.status === 'done' && p.attachment).map((p) => p.attachment!);
    const fileAttachments = pendingFiles.filter((p) => p.status === 'done' && p.fileRef).map((p) => p.fileRef!);
    if (!text && attachments.length === 0 && fileAttachments.length === 0) return;

    const isCommand = commandsEnabled && text.startsWith('/');
    if (isStreaming && !onQueue && !isCommand) return;

    // Commands bypass the queue so control commands (e.g. /stop) apply
    // immediately. Interrupt any in-flight stream first.
    if (isCommand) {
      if (isStreaming && onStop) onStop();
    } else if (isStreaming && onQueue) {
      // While the agent is streaming, normal messages go into the queue
      // instead of being sent directly (Codex-style). The parent flushes the
      // queue head when the current turn completes — do NOT also call onSend
      // here, or the same message would be sent twice.
      const queued = onQueue(text, attachments, fileAttachments.length > 0 ? fileAttachments : undefined);
      if (!queued) {
        message.warning(`最多排队 ${MAX_QUEUED_MESSAGES} 条消息`);
        return;
      }
      setInput('');
      setPending([]);
      setPendingFiles([]);
      return;
    }

    sendingRef.current = true;
    setSending(true);
    try {
      const accepted = await onSend(text, attachments, fileAttachments.length > 0 ? fileAttachments : undefined);
      if (accepted === false) return;
      setInput('');
      setPending([]);
      setPendingFiles([]);
    } catch (error) {
      message.error(error instanceof Error ? error.message : '发送失败，请重试');
    } finally {
      sendingRef.current = false;
      setSending(false);
    }
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.nativeEvent.isComposing || e.keyCode === 229) return;
    if (commandMenuOpen && commandItems.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setCommandIndex((i) => (i + 1) % commandItems.length);
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setCommandIndex((i) => (i - 1 + commandItems.length) % commandItems.length);
        return;
      }
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        selectCommand(commandItems[commandIndex]);
        return;
      }
      if (e.key === 'Tab') {
        e.preventDefault();
        selectCommand(commandItems[commandIndex]);
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        setCommandDismissed(true);
        return;
      }
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  // ---- Drag & drop ----

  const handleDragOver = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragging(true);
  };

  const handleDragLeave = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragging(false);
  };

  const handleDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragging(false);
    if (e.dataTransfer.files) {
      handleAttachmentFiles(e.dataTransfer.files);
    }
  };

  // ---- Paste images ----

  const handlePaste = (e: ClipboardEvent<HTMLTextAreaElement>) => {
    const items = e.clipboardData?.items;
    if (!items) return;

    const imageFiles: File[] = [];
    const otherFiles: File[] = [];
    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      if (item.kind !== 'file') continue;
      const file = item.getAsFile();
      if (!file) continue;
      if (item.type.startsWith('image/')) {
        imageFiles.push(file);
      } else {
        otherFiles.push(file);
      }
    }

    if (imageFiles.length > 0 || otherFiles.length > 0) {
      e.preventDefault();
      handleAttachmentFiles([...imageFiles, ...otherFiles]);
    }
  };

  const hasUploading = pending.some(p => p.status === 'uploading') || pendingFiles.some(p => p.status === 'uploading');
  const canSend = !sendDisabled && !sending && !hasUploading && Boolean(input.trim() || pending.some(p => p.status === 'done') || pendingFiles.some(p => p.status === 'done'));

  const renderSendActions = (compact: boolean) => {
    const buttonSize = compact ? 32 : 36;
    const buttonClass = 'flex-shrink-0 !rounded-full !border-0 shadow-sm';

    if (isStreaming) {
      return (
        <>
          {onQueue && <Tooltip title="加入队列 (Enter)">
            <Button
              type="primary"
              htmlType="submit"
              aria-label="加入队列"
              icon={<ArrowUpOutlined />}
              disabled={disabled || !canSend}
              className={buttonClass}
              style={{ height: buttonSize, width: buttonSize }}
            />
          </Tooltip>}
          <Tooltip title={stopping ? '停止中' : '停止生成'}>
            <Button
              type="primary"
              danger
              icon={<StopOutlined />}
              onClick={onStop}
              disabled={stopping || !onStop}
              aria-label={stopping ? '停止中' : '停止生成'}
              className={buttonClass}
              style={{ height: buttonSize, width: buttonSize }}
            />
          </Tooltip>
        </>
      );
    }

    return (
      <Tooltip title="发送消息">
        <Button
          type="primary"
          aria-label="发送消息"
          loading={sending}
          htmlType="submit"
          icon={<ArrowUpOutlined />}
          disabled={disabled || !canSend}
          className={buttonClass}
          style={{ height: buttonSize, width: buttonSize }}
        />
      </Tooltip>
    );
  };

  return (
    <div
      className={`${simple ? 'border-t border-border/60 bg-muted/10 p-2.5' : portal ? 'bg-muted/10 px-4 pb-5 pt-3 sm:px-7 sm:pb-7' : 'bg-muted/20 p-3'} ${isDragging ? 'bg-primary/5' : ''}`}
      onDragOver={simple ? undefined : handleDragOver}
      onDragLeave={simple ? undefined : handleDragLeave}
      onDrop={simple ? undefined : handleDrop}
    >
      {context && <div className="mx-auto mb-2 max-w-3xl">{context}</div>}
      {/* Starter prompts */}
      {!simple && starterPrompts && starterPrompts.length > 0 && (
        <div className="mx-auto max-w-3xl mb-3 flex flex-wrap gap-2">
          {starterPrompts.map((hint) => (
            <button
              key={hint.label}
              onClick={() => onStarterPromptClick?.(hint.label)}
              disabled={disabled}
              className="flex items-center gap-1.5 rounded-full border border-primary/15 bg-primary/5 px-3.5 py-1.5 text-xs text-primary transition hover:border-primary/25 hover:bg-primary/10 cursor-pointer active:scale-[0.97] disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <span>{hint.icon}</span>
              <span>{hint.label}</span>
            </button>
          ))}
        </div>
      )}

      {/* Hidden inputs */}
      {!simple && (
        <input
          ref={attachmentInputRef}
          type="file"
          multiple
          hidden
          onChange={(e) => {
            if (e.target.files) handleAttachmentFiles(e.target.files);
            e.target.value = '';
          }}
        />
      )}

      {/* Image previews */}
      {!simple && pending.length > 0 && (
        <div className="mx-auto max-w-3xl mb-3">
          <Image.PreviewGroup>
            <div className="flex flex-wrap gap-3">
              {pending.map((p) => (
                <div key={p.localId} className="relative group">
                  {p.thumb && (
                    <Image
                      src={p.thumb}
                      alt="preview"
                      width={96}
                      height={96}
                      className={`object-cover rounded-lg border border-border shadow-sm transition-all duration-200 hover:shadow-md hover:scale-[1.02] ${p.status === 'error' ? 'opacity-50' : ''}`}
                      rootClassName="rounded-lg overflow-hidden"
                      preview={{ src: p.thumb }}
                      style={{ display: 'block' }}
                    />
                  )}
                  {p.status === 'uploading' && (
                    <div className="absolute inset-0 flex items-center justify-center bg-black/40 rounded-lg backdrop-blur-[1px] pointer-events-none">
                      <LoadingOutlined className="text-white text-xl" />
                    </div>
                  )}
                  {(p.status === 'done' || p.status === 'error') && (
                    <button
                      onClick={() => {
                        setPending((prev) => prev.filter((px) => px.localId !== p.localId));
                      }}
                      className="absolute top-1 right-1 w-6 h-6 rounded-full bg-black/70 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-all duration-200 hover:bg-red-500/90 hover:scale-110"
                      title="移除图片"
                    >
                      <CloseOutlined className="text-white text-xs" />
                    </button>
                  )}
                  {p.status === 'error' && (
                    <button
                      onClick={() => {
                        setPending((prev) => prev.filter((px) => px.localId !== p.localId));
                      }}
                      className="absolute inset-0 flex items-center justify-center bg-red-500/60 rounded-lg cursor-pointer hover:bg-red-500/70 transition-colors backdrop-blur-[1px]"
                      title="上传失败，点击移除"
                    >
                      <CloseOutlined className="text-white text-xl" />
                    </button>
                  )}
                </div>
              ))}
            </div>
          </Image.PreviewGroup>
        </div>
      )}

      {/* File attachment previews */}
      {!simple && pendingFiles.length > 0 && (
        <div className="mx-auto max-w-3xl mb-3">
          <div className="flex flex-wrap gap-2">
            {pendingFiles.map((p) => (
              <div
                key={p.localId}
                className={`relative group flex items-center gap-2 px-3 py-2 rounded-lg border border-border bg-background text-sm transition-all ${
                  p.status === 'error' ? 'border-red-300 bg-red-50' : ''
                }`}
              >
                <FileTextOutlined className="text-base text-muted-foreground" />
                <div className="flex flex-col min-w-0">
                  <span className="truncate max-w-[200px] font-medium text-foreground">{p.fileName}</span>
                  <span className="text-xs text-muted-foreground">{formatSize(p.fileSize)}</span>
                </div>
                {p.status === 'uploading' && (
                  <LoadingOutlined className="text-primary text-sm ml-1" />
                )}
                {p.status === 'done' && (
                  <span className="text-green-500 text-xs ml-1">✓</span>
                )}
                {(p.status === 'done' || p.status === 'error') && (
                  <button
                    onClick={() => removeFileAttachment(p.localId)}
                    className="absolute -top-1.5 -right-1.5 w-5 h-5 rounded-full bg-black/70 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-all duration-200 hover:bg-red-500/90 hover:scale-110"
                    title="移除文件"
                  >
                    <CloseOutlined className="text-white text-[10px]" />
                  </button>
                )}
                {p.status === 'error' && (
                  <Tooltip title={p.error}>
                    <span className="text-red-500 text-xs cursor-help ml-1">上传失败</span>
                  </Tooltip>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Queued messages */}
      {queue && queue.length > 0 && (
        <div className="mx-auto max-w-3xl mb-3 space-y-1.5">
          {queue.map((q, idx) => (
            <div
              key={q.id}
              className="group flex items-center gap-2 rounded-lg border border-dashed border-primary/25 bg-primary/[0.04] px-3 py-2 text-sm transition-colors hover:border-primary/40"
            >
              <span className="flex-shrink-0 text-xs text-muted-foreground tabular-nums">
                {idx + 1}
              </span>
              <span className="flex-1 truncate text-foreground">
                {q.content || '[附件]'}
              </span>
              {(q.attachments.length > 0 || (q.fileAttachments && q.fileAttachments.length > 0)) && (
                <span className="flex-shrink-0 text-xs text-muted-foreground">
                  +{q.attachments.length + (q.fileAttachments?.length ?? 0)} 附件
                </span>
              )}
              <span className="flex-shrink-0 rounded-full bg-primary/10 px-2 py-0.5 text-[11px] text-primary">
                排队中
              </span>
              {onQueueRemove && (
                <Tooltip title="编辑消息，放回输入框">
                  <button
                    type="button"
                    aria-label="编辑排队消息"
                    disabled={disabled}
                    onClick={() => handleQueueEdit(q)}
                    className="flex-shrink-0 flex h-6 w-6 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-primary/10 hover:text-primary disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <EditOutlined className="text-xs" />
                  </button>
                </Tooltip>
              )}
              <Tooltip title="中断当前回复，立即发送">
                <button
                  onClick={() => onQueueSendNow?.(q.id)}
                  className="flex-shrink-0 flex h-6 w-6 items-center justify-center rounded-full text-muted-foreground opacity-0 transition-all group-hover:opacity-100 hover:bg-primary/10 hover:text-primary"
                >
                  <ThunderboltOutlined className="text-xs" />
                </button>
              </Tooltip>
              <Tooltip title="移出队列">
                <button
                  onClick={() => onQueueRemove?.(q.id)}
                  className="flex-shrink-0 flex h-6 w-6 items-center justify-center rounded-full text-muted-foreground opacity-0 transition-all group-hover:opacity-100 hover:bg-red-500/10 hover:text-red-500"
                >
                  <CloseOutlined className="text-xs" />
                </button>
              </Tooltip>
            </div>
          ))}
        </div>
      )}

      {/* Input form */}
      <form
        onSubmit={handleSubmit}
        className={simple
          ? 'relative mx-auto flex max-w-none items-end gap-2'
          : `relative mx-auto flex ${portal ? 'max-w-4xl border-border/60 bg-card/80 shadow-[0_8px_30px_rgba(15,23,42,0.06)]' : 'max-w-3xl border-border/80 bg-background shadow-sm'} flex-col rounded-2xl border p-2 transition focus-within:border-primary/35 focus-within:shadow-[0_8px_30px_rgba(15,23,42,0.08)]`}
      >
        {commandMenuOpen && commandItems.length > 0 && (
          <CommandMenu
            query={input.slice(1)}
            activeIndex={commandIndex}
            onSelect={selectCommand}
            onClose={() => setCommandDismissed(true)}
          />
        )}
        {mentions ? <RoomMentionInput
          value={mentions.value} onChange={mentions.onChange} candidates={mentions.candidates}
          onSend={() => void handleSubmit()} onPaste={handlePaste}
          disabled={disabled || sending} busy={isStreaming} embedded
        /> : <TextArea
          ref={textareaRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          onPaste={simple ? undefined : handlePaste}
          placeholder={
            simple
              ? '输入消息...'
              : isStreaming
                ? '继续输入，Enter 加入队列'
                : '输入消息，Enter 发送'
          }
          autoSize={{ minRows: 1, maxRows: simple ? 4 : 6 }}
          disabled={disabled || sending}
          aria-label="对话消息"
          variant={simple ? 'outlined' : 'borderless'}
          className={simple ? '!rounded-xl !text-sm' : '!px-2 !py-1.5 !text-sm !shadow-none'}
        />}

        {simple ? (
          renderSendActions(true)
        ) : (
          <div className="flex w-full items-center justify-between gap-3 pt-1">
            <div className="flex min-w-0 items-center gap-1">
              <Tooltip title={portal ? '添加图片' : '添加图片或文件'}>
                <Button
                  type="text"
                  icon={<PaperClipOutlined />}
                  onClick={() => attachmentInputRef.current?.click()}
                  disabled={disabled || sending || sendDisabled || totalPending() >= limits.total}
                  className="flex-shrink-0 !rounded-xl text-muted-foreground"
                  style={{ height: 34, width: 34 }}
                  aria-label="添加附件"
                />
              </Tooltip>
              {!portal && !fixedWorkspace && (
              <Tooltip title="附件和生成的文件会保存在所选工作区">
                <div className="flex min-w-0 items-center gap-0.5 text-muted-foreground">
                  <FolderOutlined className="ml-1 flex-shrink-0 text-xs" />
                  <Select
                    size="small"
                    value={selectedWorkspaceId}
                    onChange={setSelectedWorkspace}
                    loading={isWorkspacesLoading}
                    variant="borderless"
                    popupMatchSelectWidth={false}
                    style={{ minWidth: 124 }}
                    options={workspaces.map((ws) => ({
                      value: ws.id,
                      label: ws.is_default ? `${ws.name} · 默认` : ws.name,
                    }))}
                    placeholder="选择工作区"
                  />
                </div>
              </Tooltip>
              )}
            </div>
            <div className="flex flex-shrink-0 items-center gap-1.5">
              {renderSendActions(false)}
            </div>
          </div>
        )}
      </form>

      {!simple && isDragging && (
        <div className="fixed inset-0 bg-primary/10 border-2 border-dashed border-primary pointer-events-none z-50 flex items-center justify-center">
          <div className="bg-background rounded-lg px-6 py-3 shadow-lg text-primary font-semibold">
            拖放文件到此处
          </div>
        </div>
      )}
    </div>
  );
}
