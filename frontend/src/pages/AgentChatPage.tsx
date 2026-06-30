import { useCallback, useEffect, useRef, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useChatStore } from '@/stores/chatStore';
import { chatApi } from '@/lib/api';
import MessageList from '@/components/chat/MessageList';
import ChatInput from '@/components/chat/ChatInput';
import AgentConfigSidebar from '@/components/AgentConfigSidebar';
import { Alert, App, Typography, Spin, Tag, Button } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { agentsApi } from '@/lib/api';
import type { Agent, ChatAttachment, FileAttachmentRef, StreamingState } from '@/lib/types';
import { getAgentIcon } from '@/lib/agent-icons';

const { Text } = Typography;

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

export default function AgentChatPage() {
  const { agentId, sessionId: urlSessionId } = useParams<{ agentId: string; sessionId?: string }>();
  const navigate = useNavigate();
  const { activeSessionId, sessions, messages, addMessage, createSession, renameSession, loadSessions, refreshSessions, setActiveSession } = useChatStore();
  const { message } = App.useApp();
  const [streaming, setStreaming] = useState<StreamingState>(IDLE_STREAMING);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const [agent, setAgent] = useState<Agent | null>(null);
  const [agentLoading, setAgentLoading] = useState(true);

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

  // Load sessions for this agent.
  // loadSessions does a full reset (clears activeSessionId); refreshSessions
  // keeps activeSessionId intact so chat messages stay visible.
  useEffect(() => {
    if (!agentId) return;
    if (urlSessionId) {
      refreshSessions(agentId);
    } else {
      loadSessions(agentId);
    }
  }, [agentId, urlSessionId]); // eslint-disable-line react-hooks/exhaustive-deps

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

      // Start streaming state
      setError(null);
      setStreaming({ ...IDLE_STREAMING, isStreaming: true });

      // Start SSE stream
      const controller = chatApi.stream(
        {
          session_id: sessionId,
          agent_id: agentId,
          message: content,
          attachments: attachments.length > 0 ? attachments : null,
          file_attachments: fileAttachments && fileAttachments.length > 0 ? fileAttachments : null,
        },
        (event) => {
          const type = event.type as string;

          switch (type) {
            case 'session':
              break;

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
                      i === prev.thinkingChunks.length - 1
                        ? { ...c, content: c.content + content }
                        : c,
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
                actionOrder: [
                  ...prev.actionOrder,
                  { type: 'tool', id: (event.id as string) || '' },
                ],
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

              addMessage(sessionId!, {
                id: msgId,
                role: 'assistant',
                content: finalText,
                tool_calls: toolCalls || null,
                created_at: new Date().toISOString(),
              });

              setStreaming(IDLE_STREAMING);
              refreshSessions(agentId);
              break;
            }

            case 'error': {
              setStreaming(IDLE_STREAMING);
              // Defensive: ensure error message is always a string
              const raw = event.message;
              const errStr = typeof raw === 'string' ? raw
                : Array.isArray(raw) ? raw.map((d: unknown) => typeof d === 'object' && d !== null ? (d as Record<string, unknown>).msg || JSON.stringify(d) : String(d)).join('; ')
                : String(raw ?? '未知错误');
              setError(errStr);
              message.error(errStr);
              break;
            }

            // ---- Delegation events ----
            case 'delegation_start': {
              const delegToolCallId = (event.tool_call_id as string) || '';
              const newDelegation = {
                delegation_id: (event.delegation_id as string) || '',
                child_agent_id: (event.child_agent_id as string) || '',
                child_agent_name: (event.child_agent_name as string) || '',
                child_agent_icon: (event.child_agent_icon as string) || undefined,
                task: (event.task as string) || '',
                status: 'running' as const,
                thinking: '',
                toolCalls: [],
                tool_call_id: delegToolCallId,
              };

              setStreaming((prev) => {
                const newActionOrder = [...prev.actionOrder];
                let inserted = false;

                if (delegToolCallId) {
                  const idx = newActionOrder.findIndex(
                    (a) => a.type === 'tool' && a.id === delegToolCallId
                  );
                  if (idx >= 0) {
                    newActionOrder[idx] = {
                      type: 'delegation',
                      id: newDelegation.delegation_id,
                    };
                    inserted = true;
                  }
                }

                if (!inserted) {
                  newActionOrder.push({
                    type: 'delegation',
                    id: newDelegation.delegation_id,
                  });
                }

                return {
                  ...prev,
                  delegations: [...prev.delegations, newDelegation],
                  actionOrder: newActionOrder,
                };
              });
              break;
            }

            case 'delegation_thinking':
              setStreaming((prev) => ({
                ...prev,
                delegations: prev.delegations.map((d) =>
                  d.delegation_id === event.delegation_id
                    ? { ...d, thinking: (d.thinking || '') + ((event.content as string) || '') }
                    : d,
                ),
              }));
              break;

            case 'delegation_tool_call':
              setStreaming((prev) => ({
                ...prev,
                delegations: prev.delegations.map((d) =>
                  d.delegation_id === event.delegation_id
                    ? {
                        ...d,
                        toolCalls: [
                          ...(d.toolCalls || []),
                          {
                            id: (event.id as string) || '',
                            name: (event.name as string) || '',
                            arguments: (event.arguments as Record<string, unknown>) || {},
                          },
                        ],
                      }
                    : d,
                ),
              }));
              break;

            case 'delegation_tool_result':
              setStreaming((prev) => ({
                ...prev,
                delegations: prev.delegations.map((d) =>
                  d.delegation_id === event.delegation_id
                    ? {
                        ...d,
                        toolCalls: (d.toolCalls || []).map((tc) =>
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
                      }
                    : d,
                ),
              }));
              break;

            case 'delegation_text_delta':
              setStreaming((prev) => ({
                ...prev,
                delegations: prev.delegations.map((d) =>
                  d.delegation_id === event.delegation_id
                    ? { ...d, result: (d.result || '') + ((event.content as string) || '') }
                    : d,
                ),
              }));
              break;

            case 'delegation_end':
              setStreaming((prev) => ({
                ...prev,
                delegations: prev.delegations.map((d) =>
                  d.delegation_id === event.delegation_id
                    ? {
                        ...d,
                        status: (event.status as 'completed' | 'failed' | 'timeout') || 'completed',
                        error: (event.error as string) || undefined,
                        duration_ms: (event.duration_ms as number) || undefined,
                        result: d.result || (event.result_preview as string) || '',
                      }
                    : d,
                ),
              }));
              break;

            // ---- Confirmation events (AskUserQuestion) ----
            case 'confirmation_required':
              setStreaming((prev) => ({
                ...prev,
                confirmations: [
                  ...prev.confirmations,
                  {
                    confirmation_id: (event.confirmation_id as string) || '',
                    question: (event.question as string) || '',
                    mode: (event.mode as import('@/lib/types').ConfirmationMode) || 'single_select',
                    options: (event.options as import('@/lib/types').ConfirmationOption[]) || [],
                    table_schema: (event.table_schema as import('@/lib/types').TableSchema) || undefined,
                    context: (event.context as import('@/lib/types').ConfirmationContext) || { timeout_seconds: 300 },
                    created_at: (event.created_at as string) || new Date().toISOString(),
                  },
                ],
                actionOrder: [
                  ...prev.actionOrder,
                  { type: 'confirmation', id: (event.confirmation_id as string) || '' },
                ],
              }));
              break;

            case 'confirmation_resolved':
              setStreaming((prev) => ({
                ...prev,
                confirmationsResolved: {
                  ...prev.confirmationsResolved,
                  [(event.confirmation_id as string) || '']: {
                    confirmation_id: (event.confirmation_id as string) || '',
                    status: (event.status as import('@/lib/types').ConfirmationStatus) || 'timeout',
                    selected_options: (event.selected_options as string[]) || undefined,
                    user_input: (event.user_input as string) || undefined,
                    table_data: (event.table_data as Record<string, unknown>[]) || undefined,
                    resolved_at: (event.resolved_at as string) || new Date().toISOString(),
                  },
                },
              }));
              break;
          }
        },
      );

      abortRef.current = controller;
    },
    [activeSessionId, sessions, agentId, navigate, createSession, renameSession, addMessage, message, refreshSessions],
  );

  const handleNewChat = async () => {
    const newId = await createSession('新对话', agentId);
    if (newId) {
      navigate(`/agents/${agentId}/chat/${newId}`, { replace: true });
    }
  };

  const handleEnsureSession = useCallback(async (): Promise<string | null> => {
    if (activeSessionId) return activeSessionId;
    const newId = await createSession('新对话', agentId);
    return newId || null;
  }, [activeSessionId, createSession, agentId]);

  const handleEditResend = (content: string) => {
    handleSend(content);
  };

  const handleStop = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    setStreaming(IDLE_STREAMING);
  };

  const currentMessages = activeSessionId ? messages[activeSessionId] || [] : [];

  if (agentLoading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <Spin size="large" />
      </div>
    );
  }

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      {/* Agent header */}
      {agent && (
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
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={handleNewChat}
            className="ml-auto !bg-gradient-to-r !from-[#6366f1] !to-[#8b5cf6] !border-none !shadow-[0_2px_8px_rgba(99,102,241,0.25)] hover:!shadow-[0_4px_12px_rgba(99,102,241,0.35)]"
          >
            新对话
          </Button>
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
          <MessageList
            messages={currentMessages}
            streaming={streaming}
            agent={agent}
            onNewChat={handleNewChat}
            onEditResend={handleEditResend}
          />

          {error && (
            <div className="mx-auto max-w-3xl w-full px-4 pb-2">
              <Alert message={error} type="error" showIcon closable onClose={() => setError(null)} />
            </div>
          )}

          <ChatInput
            onSend={handleSend}
            onStop={handleStop}
            disabled={streaming.isStreaming}
            isStreaming={streaming.isStreaming}
            sessionId={activeSessionId}
            onEnsureSession={handleEnsureSession}
            starterPrompts={agent?.starter_prompts ?? undefined}
            onStarterPromptClick={handleSend}
          />
        </div>
      </div>
    </div>
  );
}
