import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useChatStore } from '@/stores/chatStore';
import { usePetStore } from '@/stores/petStore';
import { chatApi, sessionsApi } from '@/lib/api';
import { useMessageQueue } from '@/hooks/useMessageQueue';
import { useChatStream } from '@/hooks/useChatStream';
import { handleUiActionEvent } from '@/hooks/useUiActionEvents';
import { buildPageContext } from '@/lib/uiActions/registry';
import MessageList from '@/components/chat/MessageList';
import ChatInput from '@/components/chat/ChatInput';
import AgentConfigSidebar from '@/components/AgentConfigSidebar';
import SandboxFilePanel from '@/components/chat/SandboxFilePanel';
import WebpagePreviewPanel from '@/components/chat/WebpagePreviewPanel';
import { Alert, App, Typography, Spin, Tag, Button, Skeleton, Tooltip } from 'antd';
import { PlusOutlined, LinkOutlined, DeleteOutlined } from '@ant-design/icons';
import { agentsApi } from '@/lib/api';
import type { Agent, ChatAttachment, FileAttachmentRef, SessionStatus } from '@/lib/types';
import { getAgentIcon } from '@/lib/agent-icons';

const { Text } = Typography;

export default function AgentChatPage() {
  const { agentId, sessionId: urlSessionId } = useParams<{ agentId: string; sessionId?: string }>();
  const navigate = useNavigate();
  const { activeSessionId, sessions, messages, messagesLoading, addMessage, createSession, renameSession, deleteSession, loadSessions, refreshSessions, setActiveSession } = useChatStore();
  const { message, modal } = App.useApp();
  const [creatingSession, setCreatingSession] = useState(false);
  const [agent, setAgent] = useState<Agent | null>(null);
  const [agentLoading, setAgentLoading] = useState(true);
  const [sessionStatus, setSessionStatus] = useState<SessionStatus | null>(null);
  const [checkingStatus, setCheckingStatus] = useState(false);
  // onDone 需要 flushNext，但 useChatStream 初始化早于 useMessageQueue —— 用 ref 打破循环依赖
  const flushNextRef = useRef<() => void>(() => {});

  const { streaming, error, setError, abortRef, turnSessionIdRef, beginTurn, interrupt, handleEvent } =
    useChatStream({
      onEvent: (event) => {
        // ui_* 页内操作事件统一进全局 store（runner 在 AppLayout 执行）
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
        refreshSessions(agentId);
        if (flush) flushNextRef.current();
      },
      onError: (err) => message.error(err),
    });

  // Codex-style message queue: while streaming, sent messages are queued and
  // flushed one by one as each turn completes.
  const handleSendRef = useRef<(content: string, attachments?: ChatAttachment[], fileAttachments?: FileAttachmentRef[]) => Promise<void> | void>();
  const interruptStream = useCallback(() => {
    interrupt();
    usePetStore.getState().reportEvent('interrupt', {
      sessionId: useChatStore.getState().activeSessionId ?? undefined,
    });
  }, [interrupt]);
  const { queue, enqueue, remove: removeQueued, clear: clearQueue, flushNext, sendNow: sendQueuedNow } =
    useMessageQueue(
      (content, attachments, fileAttachments) => { void handleSendRef.current?.(content, attachments, fileAttachments); },
      interruptStream,
    );
  flushNextRef.current = flushNext;

  // Load agent info
  const loadAgent = useCallback((silent = false) => {
    if (!agentId) return;
    if (!silent) setAgentLoading(true);
    agentsApi
      .get(agentId)
      .then(setAgent)
      .catch((err) => message.error(`加载智能体失败：${err.message}`))
      .finally(() => { if (!silent) setAgentLoading(false); });
  }, [agentId, message]);

  useEffect(() => {
    loadAgent();
  }, [loadAgent]);

  // Sync URL → store: when URL has a sessionId, activate it; when URL has no
  // sessionId (blank chat), clear any stale session from a previous agent visit.
  useEffect(() => {
    if (urlSessionId && urlSessionId !== activeSessionId) {
      setActiveSession(urlSessionId);
    } else if (!urlSessionId && activeSessionId) {
      setActiveSession(null);
    }
  }, [urlSessionId, agentId]); // eslint-disable-line react-hooks/exhaustive-deps

  // 检查当前会话是否有后端渠道任务在跑（飞书等无浏览器 SSE 的场景）。
  // Web 端直接发送的消息不会注册在跑任务，因此只会在渠道触发时显示重新连接入口。
  const checkSessionStatus = useCallback(async (sessionId: string) => {
    setCheckingStatus(true);
    try {
      const status = await sessionsApi.getStatus(sessionId);
      setSessionStatus(status);
    } catch {
      // 静默失败：状态查询失败不影响现有消息展示
      setSessionStatus(null);
    } finally {
      setCheckingStatus(false);
    }
  }, []);

  useEffect(() => {
    if (urlSessionId && !streaming.isStreaming) {
      void checkSessionStatus(urlSessionId);
    } else {
      setSessionStatus(null);
    }
  }, [urlSessionId, streaming.isStreaming, checkSessionStatus]);

  // Load sessions for this agent.
  // loadSessions does a full reset (clears activeSessionId); refreshSessions
  // keeps activeSessionId intact so chat messages stay visible.
  // 只在 agent 变化时加载：点击历史会话仅改 URL sessionId，不应重新拉取整个列表
  // （否则每次点历史项列表都刷新/闪烁）。
  useEffect(() => {
    if (!agentId) return;
    if (urlSessionId) {
      refreshSessions(agentId);
    } else {
      loadSessions(agentId);
    }
  }, [agentId]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleSend = useCallback(
    async (content: string, attachments: ChatAttachment[] = [], fileAttachments?: FileAttachmentRef[]) => {
      let sessionId = activeSessionId;

      // Create a new session if none is active
      if (!sessionId) {
        const hasFiles = attachments.length > 0 || (fileAttachments && fileAttachments.length > 0);
        const title = content.slice(0, 100) || (hasFiles ? '文件消息' : '新对话');
        sessionId = await createSession(title, agentId);
        if (sessionId) {
          navigate(`/agents/${agentId}/chat/${sessionId}`, { replace: true });
        }
      } else if (content.trim()) {
        // Update session title if it still has a generic default name
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
        attachments: attachments.length > 0 ? attachments : null,
        file_attachments: fileAttachments && fileAttachments.length > 0 ? fileAttachments : null,
        created_at: new Date().toISOString(),
      });

      beginTurn(sessionId);
      usePetStore.getState().startTask(sessionId, content || '文件任务', agentId);

      // Start SSE stream
      const controller = chatApi.stream(
        {
          session_id: sessionId,
          agent_id: agentId,
          message: content,
          attachments: attachments.length > 0 ? attachments : null,
          file_attachments: fileAttachments && fileAttachments.length > 0 ? fileAttachments : null,
          page_context: buildPageContext(),
        },
        handleEvent,
      );

      abortRef.current = controller;
    },
    [activeSessionId, sessions, agentId, navigate, createSession, renameSession, addMessage, message, beginTurn, handleEvent, abortRef],
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
      const newId = await createSession('新对话', agentId);
      if (newId) {
        navigate(`/agents/${agentId}/chat/${newId}`, { replace: true });
      }
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
        // 回到无会话的空对话状态，URL 去掉 sessionId
        navigate(`/agents/${agentId}`, { replace: true });
      },
    });
  };

  const handleEnsureSession = useCallback(async (): Promise<string | null> => {
    if (activeSessionId) return activeSessionId;
    const newId = await createSession('新对话', agentId);
    return newId || null;
  }, [activeSessionId, createSession, agentId]);

  const handleEditResend = (content: string) => {
    if (streaming.isStreaming) {
      enqueue(content, []);
      return;
    }
    handleSend(content);
  };

  const handleStarterPrompt = (prompt: string) => {
    if (streaming.isStreaming) {
      enqueue(prompt, []);
      return;
    }
    handleSend(prompt);
  };

  const handleStop = () => {
    // Stop only cancels the current turn; queued messages stay in the queue.
    interruptStream();
  };

  const handleReconnect = useCallback(() => {
    if (!urlSessionId || !agentId) return;
    setSessionStatus(null);
    // 回放流：done 后不触发队列 flush
    beginTurn(urlSessionId, { flushOnDone: false });
    usePetStore.getState().startTask(urlSessionId, sessionStatus?.label || '重新连接', agentId);

    const controller = sessionsApi.watchEvents(urlSessionId, handleEvent);

    abortRef.current = controller;
  }, [urlSessionId, agentId, sessionStatus?.label, beginTurn, handleEvent, abortRef]);

  const currentMessages = activeSessionId ? messages[activeSessionId] || [] : [];

  const activeSession = useMemo(
    () => sessions.find((s) => s.id === activeSessionId) ?? null,
    [sessions, activeSessionId],
  );

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      {/* Agent header */}
      {agentLoading && (
        <div className="flex items-center gap-2 px-4 py-2 border-b border-border bg-card/50">
          <Skeleton.Input active size="small" style={{ width: 180 }} />
        </div>
      )}
      {!agentLoading && agent && (
        <div className="flex items-center gap-2 px-4 py-2 border-b border-border bg-card/50">
          <span className="text-xl">{getAgentIcon(agent.icon)}</span>
          <Text strong>{agent.name}</Text>
          {agent.description && (
            <Text type="secondary" className="text-sm ml-2">
              {agent.description}
            </Text>
          )}
          {agent.model_name ? (
            <Tag color="blue" className="text-xs">{agent.model_name}</Tag>
          ) : (
            <Tag className="text-xs">默认模型</Tag>
          )}
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
      )}

      {/* Main content: sidebar + chat area */}
      <div className="flex flex-1 overflow-hidden relative">
        {/* Config sidebar (history for all, admin config for admins) */}
        {agentId && (
          <AgentConfigSidebar agentId={agentId} onAgentUpdated={() => loadAgent(true)} />
        )}

        {/* Chat area */}
        <div className="flex flex-1 flex-col overflow-hidden">
          {(messagesLoading || agentLoading) && currentMessages.length === 0 ? (
            <div className="flex flex-1 items-center justify-center">
              <Spin size="large" />
            </div>
          ) : (
            <MessageList
              messages={currentMessages}
              streaming={streaming}
              agent={agent}
              onNewChat={handleNewChat}
              onEditResend={handleEditResend}
            />
          )}

          {error && (
            <div className="mx-auto max-w-3xl w-full px-4 pb-2">
              <Alert message={error} type="error" showIcon closable onClose={() => setError(null)} />
            </div>
          )}

          {sessionStatus?.is_running && !streaming.isStreaming && (
            <div className="mx-auto max-w-3xl w-full px-4 pb-2">
              <Alert
                message={
                  <span className="flex items-center gap-2">
                    智能体正在处理
                    {sessionStatus.tool && <Tag className="text-xs">{sessionStatus.tool}</Tag>}
                  </span>
                }
                description={sessionStatus.label ? `任务：${sessionStatus.label}` : '来自渠道会话，点击重新连接查看实时进度'}
                type="info"
                showIcon
                action={
                  <Button
                    size="small"
                    icon={<LinkOutlined />}
                    loading={checkingStatus}
                    onClick={handleReconnect}
                  >
                    重新连接
                  </Button>
                }
              />
            </div>
          )}

          <ChatInput
            onSend={handleSend}
            onStop={handleStop}
            isStreaming={streaming.isStreaming}
            sessionId={activeSessionId}
            onEnsureSession={handleEnsureSession}
            starterPrompts={agent?.starter_prompts ?? undefined}
            onStarterPromptClick={handleStarterPrompt}
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
