import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useChatStore } from '@/stores/chatStore';
import { usePetStore } from '@/stores/petStore';
import { chatApi } from '@/lib/api';
import { useMessageQueue } from '@/hooks/useMessageQueue';
import { useChatStream } from '@/hooks/useChatStream';
import { handleUiActionEvent } from '@/hooks/useUiActionEvents';
import { buildPageContext } from '@/lib/uiActions/registry';
import MessageList from '@/components/chat/MessageList';
import ChatInput from '@/components/chat/ChatInput';
import ChatHistorySidebar from '@/components/chat/ChatHistorySidebar';
import SandboxFilePanel from '@/components/chat/SandboxFilePanel';
import WebpagePreviewPanel from '@/components/chat/WebpagePreviewPanel';
import { Alert, App, Button, Spin, Tooltip } from 'antd';
import { PlusOutlined, DeleteOutlined } from '@ant-design/icons';

export default function ChatPage() {
  const { activeSessionId, sessions, messages, messagesLoading, addMessage, createSession, renameSession, deleteSession } = useChatStore();
  const { message, modal } = App.useApp();
  const scrollRef = useRef<HTMLDivElement>(null);
  const [creatingSession, setCreatingSession] = useState(false);
  // onDone 需要 flushNext，但 useChatStream 初始化早于 useMessageQueue —— 用 ref 打破循环依赖
  const flushNextRef = useRef<() => void>(() => {});

  const { streaming, error, setError, abortRef, turnSessionIdRef, beginTurn, interrupt, handleEvent } =
    useChatStream({
      onEvent: (event) => {
        // ui_* 页内操作事件统一进全局 store
        if (handleUiActionEvent(event)) return true;

        const type = event.type as string;
        usePetStore.getState().reportEvent(type, {
          sessionId: turnSessionIdRef.current ?? undefined,
          tool: type === 'tool_call' ? ((event.name as string) || undefined) : undefined,
          petAction:
            type === 'pet_action'
              ? {
                  name: (event.name as string) || undefined,
                  row: typeof event.row === 'number' ? (event.row as number) : undefined,
                }
              : undefined,
        });
        return false;
      },
      onDone: (_sid, { flush }) => {
        useChatStore.getState().refreshSessions();
        if (flush) flushNextRef.current();
      },
      onError: (err) => message.error(err),
    });

  // Codex-style message queue: while streaming, sent messages are queued and
  // flushed one by one as each turn completes.
  const handleSendRef = useRef<(content: string) => Promise<void> | void>();
  const interruptStream = useCallback(() => {
    interrupt();
    usePetStore.getState().reportEvent('interrupt', {
      sessionId: useChatStore.getState().activeSessionId ?? undefined,
    });
  }, [interrupt]);
  const { queue, enqueue, remove: removeQueued, clear: clearQueue, flushNext, sendNow: sendQueuedNow } =
    useMessageQueue(
      (content) => { void handleSendRef.current?.(content); },
      interruptStream,
    );
  flushNextRef.current = flushNext;

  // Auto-scroll on new messages / streaming updates
  useEffect(() => {
    const el = scrollRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [messages, streaming]);

  const handleSend = useCallback(
    async (content: string) => {
      let sessionId = activeSessionId;

      // Create a new session if none is active
      if (!sessionId) {
        sessionId = await createSession(content.slice(0, 100));
      } else if (content.trim()) {
        const session = sessions.find((s) => s.id === sessionId);
        if (session && (!session.title || session.title === '新对话' || session.title === 'New Chat')) {
          renameSession(sessionId, content.slice(0, 100));
        }
      }

      if (!sessionId) {
        message.error('无法创建会话');
        return;
      }

      // Add user message to local store immediately
      addMessage(sessionId, {
        id: `msg-user-${Date.now()}`,
        role: 'user',
        content,
        created_at: new Date().toISOString(),
      });

      beginTurn(sessionId);
      usePetStore.getState().startTask(sessionId, content);

      // Start SSE stream
      const controller = chatApi.stream(
        { session_id: sessionId, message: content, page_context: buildPageContext() },
        handleEvent,
      );

      abortRef.current = controller;
    },
    [activeSessionId, sessions, createSession, renameSession, addMessage, message, beginTurn, handleEvent, abortRef],
  );

  handleSendRef.current = handleSend;

  // Queue is tied to the session — switching sessions drops pending items.
  useEffect(() => {
    clearQueue();
  }, [activeSessionId, clearQueue]);

  const handleNewChat = async () => {
    if (creatingSession) return;
    setCreatingSession(true);
    try {
      await createSession('新对话');
    } finally {
      setCreatingSession(false);
    }
  };

  const handleDeleteChat = () => {
    const sid = activeSessionId;
    if (!sid) return;
    interruptStream();
    modal.confirm({
      title: '删除对话？',
      content: '此操作无法撤销。',
      okText: '删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        await deleteSession(sid);
        usePetStore.getState().removeTask(sid);
      },
    });
  };

  const handleEditResend = (content: string) => {
    if (streaming.isStreaming) {
      enqueue(content, []);
      return;
    }
    handleSend(content);
  };

  const handleStop = () => {
    // Stop only cancels the current turn; queued messages stay in the queue.
    interruptStream();
  };

  const currentMessages = activeSessionId ? messages[activeSessionId] || [] : [];

  const activeSession = useMemo(
    () => sessions.find((s) => s.id === activeSessionId) ?? null,
    [sessions, activeSessionId],
  );

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      {/* Header with new chat button */}
      <div className="flex items-center justify-end gap-2 px-4 py-2 border-b border-border bg-card/50">
        <Tooltip title="删除对话">
          <Button
            danger
            icon={<DeleteOutlined />}
            onClick={handleDeleteChat}
            disabled={!activeSessionId}
            className="ml-auto"
            aria-label="删除对话"
          />
        </Tooltip>
        <Tooltip title="新对话">
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={handleNewChat}
            loading={creatingSession}
            aria-label="新对话"
          />
        </Tooltip>
      </div>

      {/* Main content: sidebar + chat */}
      <div className="flex flex-1 overflow-hidden relative">
        {/* History sidebar */}
        <ChatHistorySidebar />

        {/* Chat area */}
        <div ref={scrollRef} className="flex flex-1 flex-col overflow-hidden">
          {messagesLoading && currentMessages.length === 0 ? (
            <div className="flex flex-1 items-center justify-center">
              <Spin size="large" />
            </div>
          ) : (
            <MessageList
              messages={currentMessages}
              streaming={streaming}
              onNewChat={handleNewChat}
              onEditResend={handleEditResend}
            />
          )}

          {error && (
            <div className="mx-auto max-w-3xl w-full px-4 pb-2">
              <Alert message={error} type="error" showIcon closable onClose={() => setError(null)} />
            </div>
          )}

          <ChatInput
            onSend={handleSend}
            onStop={handleStop}
            isStreaming={streaming.isStreaming}
            queue={queue}
            onQueue={enqueue}
            onQueueSendNow={sendQueuedNow}
            onQueueRemove={removeQueued}
          />
        </div>

        {/* Sandbox file panel */}
        <SandboxFilePanel workspaceId={activeSession?.workspace_id ?? null} />

        {/* Webpage artifact preview panel */}
        <WebpagePreviewPanel />
      </div>
    </div>
  );
}
