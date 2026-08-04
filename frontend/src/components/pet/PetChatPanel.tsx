import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Alert, Spin, message } from 'antd';
import { CloseOutlined } from '@ant-design/icons';
import { useChatStore } from '@/stores/chatStore';
import { usePetStore } from '@/stores/petStore';
import { chatApi } from '@/lib/api';
import { useMessageQueue } from '@/hooks/useMessageQueue';
import MessageList from '@/components/chat/MessageList';
import ChatInput from '@/components/chat/ChatInput';
import type { ChatAttachment, FileAttachmentRef, StreamingState, UserPet } from '@/lib/types';

const IDLE_STREAMING: StreamingState = {
  thinking: '',
  thinkingChunks: [],
  toolCalls: [],
  finalText: '',
  isStreaming: false,
  delegations: [],
  actionOrder: [],
  confirmations: [],
  confirmationsResolved: {},
};

const PANEL_W = 400;
const PANEL_H = 560;

interface Props {
  open: boolean;
  pet: UserPet | null;
  sessionId: string | null;
  agentId: string | null;
  onClose: () => void;
}

function defaultPos() {
  return {
    x: Math.max(window.innerWidth - PANEL_W - 20, 8),
    y: Math.max(Math.min((window.innerHeight - PANEL_H) / 2, 160), 16),
  };
}

/**
 * 宠物对话浮动面板。无遮罩、标题栏可拖动、贴侧边栏。
 * 复用 chatStore 的 messages 缓存按 sessionId 读写，不切换全局
 * activeSessionId，因此不影响主界面正在查看的会话。
 */
export default function PetChatPanel({ open, pet, sessionId, agentId, onClose }: Props) {
  const addMessage = useChatStore((s) => s.addMessage);
  const messages = useChatStore((s) => (sessionId ? s.messages[sessionId] : undefined)) ?? [];
  const [streaming, setStreaming] = useState<StreamingState>(IDLE_STREAMING);
  const [error, setError] = useState<string | null>(null);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<{ startX: number; startY: number; baseX: number; baseY: number; moved: boolean } | null>(null);
  const [pos, setPos] = useState(defaultPos);

  // 打开时加载会话历史（有缓存直接复用）
  useEffect(() => {
    if (!open || !sessionId) return;
    let cancelled = false;
    setMessagesLoading(true);
    (async () => {
      try {
        if (!useChatStore.getState().messages[sessionId]) {
          await useChatStore.getState().loadSessionMessages(sessionId);
        }
      } finally {
        if (!cancelled) setMessagesLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, sessionId]);

  // 关闭时中断流并复位；卸载兜底
  useEffect(() => {
    if (open) return;
    abortRef.current?.abort();
    abortRef.current = null;
    setStreaming(IDLE_STREAMING);
    setError(null);
  }, [open]);
  useEffect(() => () => abortRef.current?.abort(), []);

  const interruptStream = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setStreaming(IDLE_STREAMING);
    usePetStore.getState().reportEvent('interrupt', {
      sessionId: sessionId ?? undefined,
    });
  }, [sessionId]);

  const handleSendRef = useRef<(content: string, attachments?: ChatAttachment[], fileAttachments?: FileAttachmentRef[]) => Promise<void> | void>();
  const { queue, enqueue, remove: removeQueued, clear: clearQueue, flushNext, sendNow: sendQueuedNow } =
    useMessageQueue(
      (content, attachments, fileAttachments) => { void handleSendRef.current?.(content, attachments, fileAttachments); },
      interruptStream,
    );

  useEffect(() => {
    clearQueue();
  }, [sessionId, clearQueue]);

  const handleSend = useCallback(
    async (content: string, attachments: ChatAttachment[] = [], fileAttachments?: FileAttachmentRef[]) => {
      if (!sessionId) return;

      addMessage(sessionId, {
        id: `msg-user-${Date.now()}`,
        role: 'user',
        content,
        attachments: attachments.length > 0 ? attachments : null,
        file_attachments: fileAttachments && fileAttachments.length > 0 ? fileAttachments : null,
        created_at: new Date().toISOString(),
      });

      setError(null);
      setStreaming({ ...IDLE_STREAMING, isStreaming: true });
      usePetStore.getState().startTask(sessionId, content || '文件任务', agentId ?? undefined);

      const controller = chatApi.stream(
        {
          session_id: sessionId,
          agent_id: agentId ?? undefined,
          message: content,
          attachments: attachments.length > 0 ? attachments : null,
          file_attachments: fileAttachments && fileAttachments.length > 0 ? fileAttachments : null,
        },
        (event) => {
          const type = event.type as string;
          usePetStore.getState().reportEvent(type, {
            sessionId,
            tool: type === 'tool_call' ? ((event.name as string) || undefined) : undefined,
            petAction:
              type === 'pet_action'
                ? {
                    name: (event.name as string) || undefined,
                    row: typeof event.row === 'number' ? (event.row as number) : undefined,
                  }
                : undefined,
          });

          switch (type) {
            case 'session':
              break;

            case 'session_title': {
              const sid = (event.session_id as string) || sessionId;
              const newTitle = event.title as string;
              if (sid && newTitle) {
                useChatStore.getState().setSessionTitleLocal(sid, newTitle);
              }
              break;
            }

            case 'thinking':
              setStreaming((prev) => {
                const content = (event.content as string) || '';
                const lastAction = prev.actionOrder[prev.actionOrder.length - 1];
                if (lastAction?.type === 'thinking') {
                  return {
                    ...prev,
                    isStreaming: true,
                    thinking: prev.thinking + content,
                    thinkingChunks: prev.thinkingChunks.map((c, i) =>
                      i === prev.thinkingChunks.length - 1 ? { ...c, content: c.content + content } : c,
                    ),
                  };
                }
                const newId = `thinking-${prev.thinkingChunks.length}`;
                return {
                  ...prev,
                  isStreaming: true,
                  thinking: prev.thinking + content,
                  thinkingChunks: [...prev.thinkingChunks, { id: newId, content }],
                  actionOrder: [...prev.actionOrder, { type: 'thinking' as const, id: newId }],
                };
              });
              break;

            case 'tool_call':
              setStreaming((prev) => ({
                ...prev,
                toolCalls: [
                  ...prev.toolCalls,
                  {
                    id: (event.id as string) || '',
                    name: (event.name as string) || '',
                    arguments: (event.arguments as Record<string, unknown>) || {},
                  },
                ],
                actionOrder: [...prev.actionOrder, { type: 'tool', id: (event.id as string) || '' }],
              }));
              break;

            case 'tool_result':
              setStreaming((prev) => ({
                ...prev,
                toolCalls: prev.toolCalls.map((tc) =>
                  tc.id === event.tool_call_id
                    ? {
                        ...tc,
                        result: {
                          status: (event.status as string) || '',
                          preview: (event.preview as string) || '',
                        },
                      }
                    : tc,
                ),
              }));
              break;

            case 'text_delta':
              setStreaming((prev) => ({
                ...prev,
                isStreaming: true,
                finalText: prev.finalText + ((event.content as string) || ''),
              }));
              break;

            case 'text':
              setStreaming((prev) => ({
                ...prev,
                isStreaming: false,
                finalText: (event.content as string) || '',
              }));
              break;

            case 'done': {
              const finalText = (event.content as string) || '';
              const msgId = (event.message_id as string) || `msg-assistant-${Date.now()}`;
              const toolCalls = event.tool_calls as Record<string, unknown>[] | undefined;

              addMessage(sessionId, {
                id: msgId,
                role: 'assistant',
                content: finalText,
                tool_calls: toolCalls || null,
                created_at: new Date().toISOString(),
              });

              setStreaming(IDLE_STREAMING);
              flushNext();
              break;
            }

            case 'error': {
              setStreaming(IDLE_STREAMING);
              const raw = event.message;
              const errStr = typeof raw === 'string' ? raw
                : Array.isArray(raw) ? raw.map((d: unknown) => typeof d === 'object' && d !== null ? (d as Record<string, unknown>).msg || JSON.stringify(d) : String(d)).join('; ')
                : String(raw ?? '未知错误');
              setError(errStr);
              message.error(errStr);
              break;
            }
          }
        },
      );

      abortRef.current = controller;
    },
    [sessionId, agentId, addMessage, flushNext],
  );

  handleSendRef.current = handleSend;

  const handleEditResend = (content: string) => {
    if (streaming.isStreaming) {
      enqueue(content, []);
      return;
    }
    handleSend(content);
  };

  const handleStop = () => {
    interruptStream();
  };

  const handleEnsureSession = useCallback(async (): Promise<string | null> => sessionId, [sessionId]);

  // ---- 标题栏拖拽 ----
  const onTitlePointerDown = useCallback((e: React.PointerEvent) => {
    if (e.button !== 0) return;
    const el = panelRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    dragRef.current = { startX: e.clientX, startY: e.clientY, baseX: rect.left, baseY: rect.top, moved: false };
    el.setPointerCapture(e.pointerId);
  }, []);

  const onTitlePointerMove = useCallback((e: React.PointerEvent) => {
    const d = dragRef.current;
    if (!d) return;
    const dx = e.clientX - d.startX;
    const dy = e.clientY - d.startY;
    if (!d.moved && Math.hypot(dx, dy) < 4) return;
    d.moved = true;
    // 拖拽中直接改 DOM，避免高频 setState 挤掉流式渲染帧
    const el = panelRef.current;
    if (!el) return;
    el.style.left = `${Math.min(Math.max(d.baseX + dx, 0), window.innerWidth - PANEL_W)}px`;
    el.style.top = `${Math.min(Math.max(d.baseY + dy, 0), window.innerHeight - 44)}px`;
  }, []);

  const onTitlePointerUp = useCallback(() => {
    const d = dragRef.current;
    dragRef.current = null;
    if (!d?.moved) return;
    const el = panelRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    setPos({ x: r.left, y: r.top });
  }, []);

  if (!open) return null;

  // 用 portal 渲染到 body：PetWidget 容器带 transform，fixed 子元素会以它为
  // 包含块导致定位错乱，必须脱离容器按 viewport 定位
  return createPortal(
    <div
      ref={panelRef}
      className="fixed z-[60] flex flex-col overflow-hidden rounded-2xl border border-border bg-card shadow-2xl"
      style={{ left: pos.x, top: pos.y, width: PANEL_W, height: `min(${PANEL_H}px, 80vh)` }}
    >
      {/* 标题栏：可拖动 */}
      <div
        className="flex cursor-move select-none items-center gap-2 border-b border-border bg-muted/40 px-3 py-2"
        onPointerDown={onTitlePointerDown}
        onPointerMove={onTitlePointerMove}
        onPointerUp={onTitlePointerUp}
      >
        <span className="truncate text-sm font-semibold">
          {pet ? `和 ${pet.package.display_name} 对话` : '和宠物对话'}
        </span>
        <button
          type="button"
          title="关闭"
          onPointerDown={(e) => e.stopPropagation()}
          onClick={onClose}
          className="ml-auto flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          <CloseOutlined className="text-xs" />
        </button>
      </div>

      {/* 消息区 + 输入框 */}
      <div className="flex min-h-0 flex-1 flex-col">
        {messagesLoading && messages.length === 0 ? (
          <div className="flex flex-1 items-center justify-center">
            <Spin size="large" />
          </div>
        ) : (
          <MessageList
            messages={messages}
            streaming={streaming}
            emptyTitle={pet ? `和 ${pet.package.display_name} 打个招呼吧` : '和宠物打个招呼吧'}
            onEditResend={handleEditResend}
          />
        )}

        {error && (
          <div className="w-full px-3 pb-2">
            <Alert message={error} type="error" showIcon closable onClose={() => setError(null)} />
          </div>
        )}

        <ChatInput
          simple
          onSend={handleSend}
          onStop={handleStop}
          isStreaming={streaming.isStreaming}
          sessionId={sessionId}
          onEnsureSession={handleEnsureSession}
          queue={queue}
          onQueue={enqueue}
          onQueueSendNow={sendQueuedNow}
          onQueueRemove={removeQueued}
        />
      </div>
    </div>,
    document.body,
  );
}
