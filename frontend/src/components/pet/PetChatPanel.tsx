import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Alert, Spin, App } from 'antd';
import { CloseOutlined, DeleteOutlined, PlusOutlined } from '@ant-design/icons';
import { useChatStore } from '@/stores/chatStore';
import { usePetStore } from '@/stores/petStore';
import { chatApi, petsApi } from '@/lib/api';
import { useChatStream } from '@/hooks/useChatStream';
import ChatRunNotice from '@/components/chat/ChatRunNotice';
import { useMessageQueue } from '@/hooks/useMessageQueue';
import { handleUiActionEvent } from '@/hooks/useUiActionEvents';
import { buildPageContext } from '@/lib/uiActions/registry';
import MessageList from '@/components/chat/MessageList';
import ChatInput from '@/components/chat/ChatInput';
import PetCanvas from './PetCanvas';
import type { ChatAttachment, FileAttachmentRef, UserPet } from '@/lib/types';

const PANEL_W = 340;
const PANEL_H = 620;
const MIN_W = 280;
const MIN_H = 360;

type ResizeDir = 'e' | 's' | 'se';

interface Props {
  open: boolean;
  pet: UserPet | null;
  sessionId: string | null;
  agentId: string | null;
  onClose: () => void;
  /** 新建会话后回调，由父组件切换 chatDlg 的 sessionId */
  onSessionChange?: (sessionId: string, agentId: string | null) => void;
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
export default function PetChatPanel({ open, pet, sessionId, agentId, onClose, onSessionChange }: Props) {
  const { message, modal } = App.useApp();
  const addMessage = useChatStore((s) => s.addMessage);
  const deleteSession = useChatStore((s) => s.deleteSession);
  const messages = useChatStore((s) => (sessionId ? s.messages[sessionId] : undefined)) ?? [];
  const flushNextRef = useRef<() => void>(() => {});
  const { run, stopping, resume, streaming, error, setError, abortRef, beginTurn, interrupt } = useChatStream({
    sessionId: open ? sessionId : null,
    onEvent: (event) => {
      if (handleUiActionEvent(event)) return true;
      const type = event.type as string;
      usePetStore.getState().reportEvent(type, {
        sessionId: sessionId ?? undefined,
        tool: type === 'tool_call' ? event.name as string : undefined,
        petAction: type === 'pet_action' ? { name: event.name as string, row: event.row as number } : undefined,
      });
      return false;
    },
    onDone: (_sid, { flush }) => { if (flush) flushNextRef.current(); },
    onError: (err) => message.error(err),
  });
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [newSessionLoading, setNewSessionLoading] = useState(false);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<{ startX: number; startY: number; baseX: number; baseY: number; moved: boolean } | null>(null);
  const resizeRef = useRef<{
    startX: number;
    startY: number;
    baseW: number;
    baseH: number;
    baseX: number;
    baseY: number;
    dir: ResizeDir;
  } | null>(null);
  const [pos, setPos] = useState(defaultPos);
  const [size, setSize] = useState({ w: PANEL_W, h: PANEL_H });

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

  const interruptStream = useCallback(() => interrupt(), [interrupt]);

  const handleDeleteConversation = () => {
    if (!sessionId) return;
    modal.confirm({
      title: '删除对话？',
      content: '此操作无法撤销。',
      okText: '删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        if (streaming.isStreaming && !await interruptStream()) return;
        await deleteSession(sessionId);
        usePetStore.getState().removeTask(sessionId);
        onClose();
      },
    });
  };

  // 新建会话仅切换订阅，旧任务继续在后台执行。
  const handleNewConversation = () => {
    if (!pet || newSessionLoading) return;
    setNewSessionLoading(true);
    void (async () => {
      try {
        const r = await petsApi.petChat(pet.id, { forceNew: true });
        setError(null);
        onSessionChange?.(r.conversation_id, r.agent_id ?? null);
      } catch (err) {
        message.warning(err instanceof Error ? err.message : '新建会话失败');
      } finally {
        setNewSessionLoading(false);
      }
    })();
  };

  const handleClose = onClose;

  const handleSendRef = useRef<(content: string, attachments?: ChatAttachment[], fileAttachments?: FileAttachmentRef[]) => Promise<void> | void>();
  const { queue, enqueue, remove: removeQueued, clear: clearQueue, flushNext, sendNow: sendQueuedNow } =
    useMessageQueue(
      (content, attachments, fileAttachments) => { void handleSendRef.current?.(content, attachments, fileAttachments); },
      interruptStream,
    );
  flushNextRef.current = flushNext;

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

      const consume = beginTurn(sessionId);
      usePetStore.getState().startTask(sessionId, content || '文件任务', agentId ?? undefined);

      const controller = chatApi.stream(
        {
          session_id: sessionId,
          agent_id: agentId ?? undefined,
          message: content,
          attachments: attachments.length > 0 ? attachments : null,
          file_attachments: fileAttachments && fileAttachments.length > 0 ? fileAttachments : null,
          page_context: buildPageContext(),
        },
        consume,
      );

      abortRef.current = controller;
    },
    [sessionId, agentId, addMessage, beginTurn, abortRef],
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
    // 必须在标题栏自身捕获：捕获到 panel（父元素）时，pointermove/up 会被浏览器
    // 重定向到 panel，标题栏的 onPointerMove 收不到事件，拖拽会「断开」
    e.currentTarget.setPointerCapture(e.pointerId);
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
    el.style.left = `${Math.min(Math.max(d.baseX + dx, 0), window.innerWidth - el.offsetWidth)}px`;
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

  const onTitlePointerCancel = useCallback(() => {
    dragRef.current = null;
  }, []);

  // ---- 边缘/角落拖拽调整大小 ----
  const onResizePointerDown = useCallback((e: React.PointerEvent, dir: ResizeDir) => {
    if (e.button !== 0) return;
    const el = panelRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    resizeRef.current = {
      startX: e.clientX,
      startY: e.clientY,
      baseW: rect.width,
      baseH: rect.height,
      baseX: rect.left,
      baseY: rect.top,
      dir,
    };
    e.stopPropagation();
    e.currentTarget.setPointerCapture(e.pointerId);
  }, []);

  const onResizePointerMove = useCallback((e: React.PointerEvent) => {
    const r = resizeRef.current;
    if (!r) return;
    const el = panelRef.current;
    if (!el) return;
    // 拖拽中直接改 DOM，避免高频 setState 挤掉流式渲染帧；上限保证面板不出视口
    if (r.dir.includes('e')) {
      const w = Math.min(Math.max(r.baseW + e.clientX - r.startX, MIN_W), window.innerWidth - r.baseX - 8);
      el.style.width = `${w}px`;
    }
    if (r.dir.includes('s')) {
      const h = Math.min(Math.max(r.baseH + e.clientY - r.startY, MIN_H), window.innerHeight - r.baseY - 8);
      el.style.height = `${h}px`;
    }
  }, []);

  const onResizePointerUp = useCallback(() => {
    const r = resizeRef.current;
    resizeRef.current = null;
    if (!r) return;
    const el = panelRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    setSize({ w: rect.width, h: rect.height });
  }, []);

  const onResizePointerCancel = useCallback(() => {
    resizeRef.current = null;
  }, []);

  if (!open) return null;

  // 用 portal 渲染到 body：PetWidget 容器带 transform，fixed 子元素会以它为
  // 包含块导致定位错乱，必须脱离容器按 viewport 定位
  return createPortal(
    <div
      ref={panelRef}
      className="fixed z-[1010] flex flex-col overflow-hidden rounded-2xl border border-border/60 bg-card/90 shadow-[0_16px_48px_-8px_rgba(0,0,0,0.25)] backdrop-blur-xl"
      style={{ left: pos.x, top: pos.y, width: size.w, height: `min(${size.h}px, 85vh)` }}
    >
      {/* 标题栏：可拖动 */}
      <div
        className="flex cursor-move select-none items-center gap-2.5 border-b border-border/60 bg-muted/20 py-2 pl-3 pr-2"
        onPointerDown={onTitlePointerDown}
        onPointerMove={onTitlePointerMove}
        onPointerUp={onTitlePointerUp}
        onPointerCancel={onTitlePointerCancel}
      >
        {/* 宠物头像：流式响应时播放思考动作 */}
        {pet && (
          <div className="flex h-8 w-8 shrink-0 items-center justify-center overflow-hidden rounded-full border border-border/60 bg-muted/50 shadow-sm">
            <PetCanvas pkg={pet.package} mood={streaming.isStreaming ? 'think' : 'idle'} size={28} />
          </div>
        )}
        <div className="flex min-w-0 flex-1 flex-col justify-center">
          <span className="truncate text-[13px] font-semibold leading-tight">
            {pet ? pet.package.display_name : '宠物'}
          </span>
          <span className="flex items-center gap-1 text-[11px] leading-tight text-muted-foreground">
            <span className="inline-block h-1.5 w-1.5 rounded-full bg-green-500" />
            {pet?.agent ? pet.agent.name : '在线'}
          </span>
        </div>
        <button
          type="button"
          title="新对话"
          disabled={newSessionLoading}
          onPointerDown={(e) => e.stopPropagation()}
          onClick={handleNewConversation}
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-50"
        >
          {newSessionLoading ? <Spin size="small" /> : <PlusOutlined className="text-xs" />}
        </button>
        <button
          type="button"
          title="删除对话"
          onPointerDown={(e) => e.stopPropagation()}
          onClick={handleDeleteConversation}
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          <DeleteOutlined className="text-xs" />
        </button>
        <button
          type="button"
          title="关闭"
          onPointerDown={(e) => e.stopPropagation()}
          onClick={handleClose}
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
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
            messages={messages.filter((item) => !(streaming.isStreaming && run && item.id === run.assistant_message_id))}
            streaming={streaming}
            compact
            emptyTitle={pet ? `和 ${pet.package.display_name} 打个招呼吧` : '和宠物打个招呼吧'}
            emptyIcon={
              pet ? <PetCanvas pkg={pet.package} mood="happy" size={72} /> : undefined
            }
            onEditResend={handleEditResend}
            scrollToBottomOnMount
          />
        )}

        <ChatRunNotice run={run} stopping={stopping} onResume={resume} />
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

      {/* 调整大小热区：右边、下边、右下角（角带视觉手柄） */}
      <div
        className="absolute bottom-2 right-0 top-2 w-1.5 cursor-ew-resize touch-none"
        onPointerDown={(e) => onResizePointerDown(e, 'e')}
        onPointerMove={onResizePointerMove}
        onPointerUp={onResizePointerUp}
        onPointerCancel={onResizePointerCancel}
      />
      <div
        className="absolute bottom-0 left-2 right-2 h-1.5 cursor-ns-resize touch-none"
        onPointerDown={(e) => onResizePointerDown(e, 's')}
        onPointerMove={onResizePointerMove}
        onPointerUp={onResizePointerUp}
        onPointerCancel={onResizePointerCancel}
      />
      <div
        className="absolute bottom-0 right-0 flex h-4 w-4 cursor-nwse-resize touch-none items-end justify-end text-muted-foreground/50"
        onPointerDown={(e) => onResizePointerDown(e, 'se')}
        onPointerMove={onResizePointerMove}
        onPointerUp={onResizePointerUp}
        onPointerCancel={onResizePointerCancel}
      >
        <svg viewBox="0 0 16 16" className="h-3 w-3" fill="none" stroke="currentColor" strokeWidth="1.5">
          <path d="M5 15 L15 5 M10 15 L15 10" />
        </svg>
      </div>
    </div>,
    document.body,
  );
}
