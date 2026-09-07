import { useCallback, useEffect, useRef, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useChatStore } from '@/stores/chatStore';
import { chatApi, portalApi } from '@/lib/api';
import { useMessageQueue } from '@/hooks/useMessageQueue';
import { useChatStream } from '@/hooks/useChatStream';
import MessageList from '@/components/chat/MessageList';
import ChatInput from '@/components/chat/ChatInput';
import SessionSidebar from '@/components/chat/SessionSidebar';
import { Alert, App, Typography, Spin, Button, Skeleton, Tooltip } from 'antd';
import { PlusOutlined, ArrowLeftOutlined } from '@ant-design/icons';
import type { PortalAgent, ChatAttachment } from '@/lib/types';
import { getAgentIcon } from '@/lib/agent-icons';

const { Text } = Typography;

/**
 * 用户端门户对话页：纯净聊天（无斜杠命令 / workspace / 沙箱面板 / 渠道重连 /
 * agent 配置），复用 useChatStream 事件协议。
 */
export default function PortalChatPage() {
  const { agentId, sessionId: urlSessionId } = useParams<{ agentId: string; sessionId?: string }>();
  const navigate = useNavigate();
  const { activeSessionId, sessions, messages, messagesLoading, addMessage, createSession, renameSession, loadSessions, refreshSessions, setActiveSession } = useChatStore();
  const { message } = App.useApp();
  const [creatingSession, setCreatingSession] = useState(false);
  const [agent, setAgent] = useState<PortalAgent | null>(null);
  const [agentLoading, setAgentLoading] = useState(true);
  // onDone 需要 flushNext，但 useChatStream 初始化早于 useMessageQueue —— 用 ref 打破循环依赖
  const flushNextRef = useRef<() => void>(() => {});

  const { streaming, error, setError, abortRef, beginTurn, interrupt, handleEvent } =
    useChatStream({
      onDone: (_sid, { flush }) => {
        refreshSessions(agentId);
        if (flush) flushNextRef.current();
      },
      onError: (err) => message.error(err),
    });

  // Codex-style message queue: while streaming, sent messages are queued and
  // flushed one by one as each turn completes.
  const handleSendRef = useRef<(content: string, attachments?: ChatAttachment[]) => Promise<void> | void>();
  const { queue, enqueue, remove: removeQueued, clear: clearQueue, flushNext, sendNow: sendQueuedNow } =
    useMessageQueue(
      (content, attachments) => { void handleSendRef.current?.(content, attachments); },
      interrupt,
    );
  flushNextRef.current = flushNext;

  // Load agent info（门户脱敏接口）
  useEffect(() => {
    if (!agentId) return;
    setAgentLoading(true);
    portalApi
      .getAgent(agentId)
      .then(setAgent)
      .catch((err) => message.error(`加载智能体失败：${err.message}`))
      .finally(() => setAgentLoading(false));
  }, [agentId, message]);

  // Sync URL → store（同 AgentChatPage：无 sessionId 时清空残留会话）
  useEffect(() => {
    if (urlSessionId && urlSessionId !== activeSessionId) {
      setActiveSession(urlSessionId);
    } else if (!urlSessionId && activeSessionId) {
      setActiveSession(null);
    }
  }, [urlSessionId, agentId]); // eslint-disable-line react-hooks/exhaustive-deps

  // 只在 agent 变化时加载会话列表（点击历史项仅改 URL，不重新拉取）
  useEffect(() => {
    if (!agentId) return;
    if (urlSessionId) {
      refreshSessions(agentId);
    } else {
      loadSessions(agentId);
    }
  }, [agentId]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleSend = useCallback(
    async (content: string, attachments: ChatAttachment[] = []) => {
      let sessionId = activeSessionId;

      if (!sessionId) {
        const title = content.slice(0, 100) || (attachments.length > 0 ? '文件消息' : '新对话');
        sessionId = await createSession(title, agentId);
        if (sessionId) {
          navigate(`/portal/agents/${agentId}/chat/${sessionId}`, { replace: true });
        }
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

      addMessage(sessionId, {
        id: `msg-user-${Date.now()}`,
        role: 'user',
        content,
        attachments: attachments.length > 0 ? attachments : null,
        created_at: new Date().toISOString(),
      });

      beginTurn(sessionId);

      const controller = chatApi.stream(
        {
          session_id: sessionId,
          agent_id: agentId,
          message: content,
          attachments: attachments.length > 0 ? attachments : null,
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
        navigate(`/portal/agents/${agentId}/chat/${newId}`, { replace: true });
      }
    } finally {
      setCreatingSession(false);
    }
  };

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

  const currentMessages = activeSessionId ? messages[activeSessionId] || [] : [];

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      {/* Agent header */}
      {agentLoading && (
        <div className="flex items-center gap-2 border-b border-border/50 bg-card/50 px-5 py-3 sm:px-7">
          <Skeleton.Input active size="small" style={{ width: 180 }} />
        </div>
      )}
      {!agentLoading && agent && (
        <div className="flex items-center gap-2 border-b border-border/50 bg-card/50 px-5 py-3 sm:px-7">
          <Button
            type="text"
            size="small"
            icon={<ArrowLeftOutlined />}
            onClick={() => navigate('/portal')}
            className="!px-1.5 text-muted-foreground"
            aria-label="返回智能体列表"
          />
          <span className="text-xl">{getAgentIcon(agent.icon)}</span>
          <Text strong className="truncate">{agent.name}</Text>
          {agent.description && (
            <Text type="secondary" className="hidden md:inline text-sm ml-2 truncate">
              {agent.description}
            </Text>
          )}
          <div className="ml-auto flex items-center gap-1">
            <Tooltip title="新对话">
              <Button
                type="primary"
                shape="circle"
                icon={<PlusOutlined />}
                onClick={handleNewChat}
                loading={creatingSession}
                aria-label="新对话"
              />
            </Tooltip>
          </div>
        </div>
      )}

      {/* Main content: session sidebar + chat area */}
      <div className="flex flex-1 overflow-hidden relative">
        {agentId && <SessionSidebar agentId={agentId} portal />}

        <div className="flex flex-1 flex-col overflow-hidden min-w-0">
          {(messagesLoading || agentLoading) && currentMessages.length === 0 ? (
            <div className="flex flex-1 items-center justify-center">
              <Spin size="large" />
            </div>
          ) : (
            <MessageList
              messages={currentMessages}
              streaming={streaming}
              agent={agent}
              onEditResend={handleEditResend}
            />
          )}

          {error && (
            <div className="mx-auto max-w-3xl w-full px-4 pb-2">
              <Alert message={error} type="error" showIcon closable onClose={() => setError(null)} />
            </div>
          )}

          <ChatInput
            portal
            onSend={handleSend}
            onStop={interrupt}
            isStreaming={streaming.isStreaming}
            sessionId={activeSessionId}
            starterPrompts={agent?.starter_prompts ?? undefined}
            onStarterPromptClick={handleStarterPrompt}
            queue={queue}
            onQueue={enqueue}
            onQueueSendNow={sendQueuedNow}
            onQueueRemove={removeQueued}
          />
        </div>
      </div>
    </div>
  );
}
