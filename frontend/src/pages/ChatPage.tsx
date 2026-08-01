import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useChatStore } from '@/stores/chatStore';
import { chatApi } from '@/lib/api';
import { useMessageQueue } from '@/hooks/useMessageQueue';
import MessageList from '@/components/chat/MessageList';
import ChatInput from '@/components/chat/ChatInput';
import ChatHistorySidebar from '@/components/chat/ChatHistorySidebar';
import SandboxFilePanel from '@/components/chat/SandboxFilePanel';
import { Alert, App, Button } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import type { StreamingState } from '@/lib/types';

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

export default function ChatPage() {
  const { activeSessionId, sessions, messages, addMessage, createSession, renameSession } = useChatStore();
  const { message } = App.useApp();
  const scrollRef = useRef<HTMLDivElement>(null);
  const [streaming, setStreaming] = useState<StreamingState>(IDLE_STREAMING);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Codex-style message queue: while streaming, sent messages are queued and
  // flushed one by one as each turn completes.
  const handleSendRef = useRef<(content: string) => Promise<void> | void>();
  const interruptStream = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setStreaming(IDLE_STREAMING);
  }, []);
  const { queue, enqueue, remove: removeQueued, clear: clearQueue, flushNext, sendNow: sendQueuedNow } =
    useMessageQueue(
      (content) => { void handleSendRef.current?.(content); },
      interruptStream,
    );

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

      // Start streaming state
      setError(null);
      setStreaming({ ...IDLE_STREAMING, isStreaming: true });

      // Start SSE stream
      const controller = chatApi.stream(
        { session_id: sessionId, message: content },
        (event) => {
          const type = event.type as string;

          switch (type) {
            case 'session':
              // Backend returns the session_id — no need to call setActiveSession
              // (createSession already set it). This event is informational.
              break;

            case 'thinking':
              setStreaming((prev) => {
                const content = (event.content as string) || '';
                const lastAction = prev.actionOrder[prev.actionOrder.length - 1];
                if (lastAction?.type === 'thinking') {
                  // Append to current thinking chunk
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
                // Start a new thinking chunk
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
              // Incremental text streaming (final answer)
              setStreaming((prev) => ({
                ...prev,
                isStreaming: true,
                finalText: prev.finalText + ((event.content as string) || ''),
              }));
              break;

            case 'text':
              // Final text from the agent — set complete text
              setStreaming((prev) => ({
                ...prev,
                isStreaming: false,
                finalText: (event.content as string) || '',
              }));
              break;

            case 'done': {
              // Message is complete — add to store and clear streaming
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

              // Clear streaming immediately (message is now in the list)
              setStreaming(IDLE_STREAMING);

              // Reload sessions to update sidebar timestamps (without clearing activeSessionId)
              useChatStore.getState().refreshSessions();
              // Auto-send the next queued message, if any
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
                // Insert delegation entry at the correct position in actionOrder
                // by finding the matching delegate_task tool call entry.
                const newActionOrder = [...prev.actionOrder];
                let inserted = false;

                if (delegToolCallId) {
                  const idx = newActionOrder.findIndex(
                    (a) => a.type === 'tool' && a.id === delegToolCallId
                  );
                  if (idx >= 0) {
                    // Replace the delegate_task tool entry with the delegation entry
                    newActionOrder[idx] = {
                      type: 'delegation',
                      id: newDelegation.delegation_id,
                    };
                    inserted = true;
                  }
                }

                if (!inserted) {
                  // Fallback: append at end (backward compat)
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
    [activeSessionId, sessions, createSession, renameSession, addMessage, message, flushNext],
  );

  handleSendRef.current = handleSend;

  // Queue is tied to the session — switching sessions drops pending items.
  useEffect(() => {
    clearQueue();
  }, [activeSessionId, clearQueue]);

  const handleNewChat = async () => {
    await createSession('新对话');
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
      <div className="flex items-center justify-end px-4 py-2 border-b border-border bg-card/50">
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={handleNewChat}
          className="!bg-gradient-to-r !from-[#6366f1] !to-[#8b5cf6] !border-none !shadow-[0_2px_8px_rgba(99,102,241,0.25)] hover:!shadow-[0_4px_12px_rgba(99,102,241,0.35)]"
        >
          新对话
        </Button>
      </div>

      {/* Main content: sidebar + chat */}
      <div className="flex flex-1 overflow-hidden relative">
        {/* History sidebar */}
        <ChatHistorySidebar />

        {/* Chat area */}
        <div ref={scrollRef} className="flex flex-1 flex-col overflow-hidden">
          <MessageList
            messages={currentMessages}
            streaming={streaming}
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
            isStreaming={streaming.isStreaming}
            queue={queue}
            onQueue={enqueue}
            onQueueSendNow={sendQueuedNow}
            onQueueRemove={removeQueued}
          />
        </div>

        {/* Sandbox file panel */}
        <SandboxFilePanel workspaceId={activeSession?.workspace_id ?? null} />
      </div>
    </div>
  );
}
