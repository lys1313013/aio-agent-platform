import { useCallback, useEffect, useRef, useState } from 'react';
import { useChatStore } from '@/stores/chatStore';
import { chatApi, type ChatRunInfo } from '@/lib/api';
import type { StreamingState } from '@/lib/types';

function mergeFileChanges(
  current: import('@/lib/types').FileChangeInfo[],
  incoming: import('@/lib/types').FileChangeInfo[],
): import('@/lib/types').FileChangeInfo[] {
  const merged = new Map(current.map((item) => [item.path, item]));
  for (const item of incoming) {
    const previous = merged.get(item.path);
    if (!previous) merged.set(item.path, item);
    else if (previous.action === 'created' && item.action === 'deleted') merged.delete(item.path);
    else if (previous.action === 'created') merged.set(item.path, { ...item, action: 'created' });
    else if (previous.action === 'deleted' && item.action === 'created') merged.set(item.path, { ...item, action: 'modified' });
    else merged.set(item.path, item);
  }
  return [...merged.values()];
}

export const IDLE_STREAMING: StreamingState = {
  thinking: '',
  thinkingChunks: [],
  toolCalls: [],
  finalText: '',
  isStreaming: false,
  delegations: [],
  actionOrder: [],
  confirmations: [],
  confirmationsResolved: {},
  fileChanges: [],
};

/** SSE 事件（后端推送的 JSON 对象，type 字段区分事件类型） */
export type ChatStreamEvent = Record<string, unknown> & { type?: string };

export interface UseChatStreamOptions {
  sessionId?: string | null;
  /**
   * 事件前置钩子，在所有内置处理之前调用。
   * 返回 true 表示事件已被消费（如 ui_* 页内操作事件），跳过后续内置处理。
   * 用于注入管理端扩展（petStore 任务条上报、uiActionStore 页内操作等）。
   */
  onEvent?: (event: ChatStreamEvent) => boolean | void;
  /**
   * 一轮对话正常结束（done 事件处理完）后回调。
   * flush=false 表示回放流（渠道任务重连），不应触发队列下一条。
   */
  onDone?: (sessionId: string, ctx: { flush: boolean }) => void;
  /** 错误通知（默认仅写入 error state） */
  onError?: (err: string) => void;
}

/**
 * 聊天 SSE 事件处理公共 hook。
 *
 * 管理端（AgentChatPage / ChatPage）与用户端门户共用同一套事件协议：
 * thinking / tool_call / tool_result / text_delta / text / done / error /
 * delegation_* / confirmation_* / session_title / command_result / closed。
 *
 * 用法：beginTurn(sessionId) 开始一轮 → chatApi.stream/sessionsApi.watchEvents
 * 的回调里调用 handleEvent(event) → interrupt() 中断。
 */
export function useChatStream(options: UseChatStreamOptions = {}) {
  const [streaming, setStreaming] = useState<StreamingState>(IDLE_STREAMING);
  const [error, setError] = useState<string | null>(null);
  const selectedSessionId = useChatStore((state) => state.activeSessionId);
  const activeSessionId = options.sessionId === undefined ? selectedSessionId : options.sessionId;
  const [run, setRun] = useState<ChatRunInfo | null>(null);
  const [stopping, setStopping] = useState(false);
  const runRef = useRef<ChatRunInfo | null>(null);
  const generationRef = useRef(0);
  const sequenceRef = useRef(0);
  const consumeRef = useRef<(event: ChatStreamEvent) => void>(() => {});
  const abortRef = useRef<AbortController | null>(null);
  /** 当前这一轮的会话 id（done/session_title 等事件回写消息的归属） */
  const turnSessionIdRef = useRef<string | null>(null);
  /** 本轮 done 后是否应 flush 消息队列（回放流为 false） */
  const flushOnDoneRef = useRef(true);
  /** 当前轮是否为斜杠命令（command_result 已落 system 消息，done 不再补 assistant 消息） */
  const gotCommandResultRef = useRef(false);

  // 页面卸载时主动断开 fetch/SSE，避免后台的阻塞式 Redis XREAD 继续占用连接。
  useEffect(() => () => {
    generationRef.current += 1;
    abortRef.current?.abort();
    abortRef.current = null;
  }, []);

  // 回调走 ref，保持 handleEvent 引用稳定（SSE 回调在流存活期内持续触发）
  const onEventRef = useRef(options.onEvent);
  onEventRef.current = options.onEvent;
  const onDoneRef = useRef(options.onDone);
  onDoneRef.current = options.onDone;
  const onErrorRef = useRef(options.onError);
  onErrorRef.current = options.onError;

  const beginTurn = useCallback((sessionId: string, opts?: { flushOnDone?: boolean }) => {
    // 同一页面开始新一轮前关闭旧传输，防止快速重连或切换会话留下并行 SSE。
    abortRef.current?.abort();
    abortRef.current = null;
    const generation = ++generationRef.current;
    sequenceRef.current = 0;
    runRef.current = null;
    setRun(null);
    turnSessionIdRef.current = sessionId;
    flushOnDoneRef.current = opts?.flushOnDone ?? true;
    gotCommandResultRef.current = false;
    setError(null);
    setStreaming({ ...IDLE_STREAMING, isStreaming: true });
    return (event: ChatStreamEvent) => {
      if (generation === generationRef.current) consumeRef.current(event);
    };
  }, []);

  const disconnect = useCallback(() => {
    generationRef.current += 1;
    abortRef.current?.abort();
    abortRef.current = null;
    runRef.current = null;
    setRun(null);
    setStreaming(IDLE_STREAMING);
  }, []);

  const interrupt = useCallback(async (): Promise<boolean> => {
    const sid = turnSessionIdRef.current;
    const generation = generationRef.current;
    if (!sid) return true;
    setStopping(true);
    try {
      let task = runRef.current;
      // The POST may still be reserving the run when Stop is clicked.
      for (let attempt = 0; !task && attempt < 20; attempt += 1) {
        const observed = runRef.current ?? await chatApi.latestRun(sid);
        // A previous completed turn can still be "latest" while the new POST
        // is reserving its task. Never mistake it for the turn being stopped.
        task = observed && (observed.status === 'running' || observed.id === runRef.current?.id)
          ? observed : null;
        if (!task) await new Promise((resolve) => setTimeout(resolve, 250));
      }
      if (!task) throw new Error('尚未确认任务状态，请稍后重试停止');
      const stopped = task.status === 'running' ? await chatApi.stopRun(task.id) : task;
      if (generation !== generationRef.current) return false;
      disconnect();
      runRef.current = stopped;
      setRun(stopped);
      await useChatStore.getState().loadSessionMessages(sid);
      return true;
    } catch (cause) {
      const msg = cause instanceof Error ? cause.message : '停止任务失败';
      setError(msg);
      return false;
    } finally {
      setStopping(false);
    }
  }, [disconnect]);

  const handleEvent = useCallback((event: ChatStreamEvent) => {
    if (typeof event.sequence === 'number') {
      if (event.sequence <= sequenceRef.current) return;
      sequenceRef.current = event.sequence;
    }
    // Replaying browser commands must never repeat a click or form submission.
    if (event.replay && typeof event.type === 'string'
      && (event.type.startsWith('ui_action') || event.type === 'pet_action')) return;
    if (onEventRef.current?.(event)) return;

    const sessionId = turnSessionIdRef.current;
    const type = event.type as string;

    switch (type) {
      case 'run': {
        const info = event as unknown as ChatRunInfo;
        runRef.current = info;
        setRun(info);
        break;
      }
      case 'run_status': {
        const info = event as unknown as ChatRunInfo;
        runRef.current = info;
        setRun(info);
        setStreaming(IDLE_STREAMING);
        if (sessionId) void useChatStore.getState().loadSessionMessages(sessionId);
        break;
      }
      case 'transport_error':
        abortRef.current = null;
        setError('连接已断开，后台任务继续执行，正在重新连接…');
        break;

      case 'session':
        // 信息性事件：会话由 createSession 建立，无需处理
        break;

      case 'session_title': {
        const sid = (event.session_id as string) || sessionId;
        const newTitle = event.title as string;
        if (sid && newTitle) {
          useChatStore.getState().setSessionTitleLocal(sid, newTitle);
        }
        break;
      }

      case 'command_result': {
        gotCommandResultRef.current = true;
        const content = (event.content as string) || '';
        const cmdSessionId = (event.session_id as string) || sessionId;
        if (cmdSessionId) {
          useChatStore.getState().addMessage(cmdSessionId, {
            id: (event.message_id as string) || `msg-system-${Date.now()}`,
            role: 'system',
            content,
            created_at: new Date().toISOString(),
          });
        }

        // 应用命令结果携带的副作用
        const data = (event.data as Record<string, unknown>) || {};
        if (data.new_session_id) {
          useChatStore.getState().setActiveSession(data.new_session_id as string);
        }
        if (data.deleted_session_id) {
          useChatStore.getState().deleteSession(data.deleted_session_id as string);
        }
        if (data.session_id && data.title) {
          useChatStore.getState().setSessionTitleLocal(
            data.session_id as string,
            data.title as string,
          );
        }
        if (data.agent_id || data.workspace_id) {
          useChatStore.getState().refreshSessions();
        }
        if (data.workspace_id) {
          useChatStore.getState().setSelectedWorkspace(data.workspace_id as string);
        }
        if (data.switch_session_id) {
          useChatStore.getState().setActiveSession(data.switch_session_id as string);
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

      case 'file_changes': {
        const incoming = (event.file_changes || []) as import('@/lib/types').FileChangeInfo[];
        setStreaming((prev) => ({
          ...prev,
          fileChanges: mergeFileChanges(prev.fileChanges, incoming),
        }));
        break;
      }

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
          isStreaming: true,
          finalText: (event.content as string) || '',
        }));
        break;

      case 'done': {
        if (runRef.current) {
          runRef.current = { ...runRef.current, status: 'completed' };
          setRun(runRef.current);
        }
        setStreaming(IDLE_STREAMING);
        if (gotCommandResultRef.current) {
          // 斜杠命令轮：system 消息已由 command_result 落库，无 assistant 消息
          gotCommandResultRef.current = false;
        } else if (sessionId) {
          const finalText = (event.content as string) || '';
          const msgId = (event.message_id as string) || `msg-assistant-${Date.now()}`;
          const toolCalls = event.tool_calls as Record<string, unknown>[] | undefined;
          const fileChanges = event.file_changes as import('@/lib/types').FileChangeInfo[] | undefined;
          const reasoning = event.reasoning as Array<{ id: string; content: string }> | undefined;
          useChatStore.getState().addMessage(sessionId, {
            id: msgId,
            role: 'assistant',
            content: finalText,
            tool_calls: toolCalls || null,
            file_changes: fileChanges || null,
            reasoning: reasoning || null,
            created_at: new Date().toISOString(),
          });
        }
        if (sessionId) {
          onDoneRef.current?.(sessionId, { flush: flushOnDoneRef.current });
        }
        break;
      }

      case 'error': {
        setStreaming(IDLE_STREAMING);
        // 防御：error.message 可能是字符串数组或非字符串
        const raw = event.message;
        const errStr = typeof raw === 'string' ? raw
          : Array.isArray(raw) ? raw.map((d: unknown) => typeof d === 'object' && d !== null ? (d as Record<string, unknown>).msg || JSON.stringify(d) : String(d)).join('; ')
          : String(raw ?? '未知错误');
        setError(errStr);
        onErrorRef.current?.(errStr);
        break;
      }

      // ---- 委派事件 ----
      case 'delegation_start': {
        const delegToolCallId = (event.tool_call_id as string) || '';
        const newDelegation = {
          delegation_id: (event.delegation_id as string) || '',
          child_agent_id: (event.child_agent_id as string) || '',
          child_agent_name: (event.child_agent_name as string) || '',
          child_agent_icon: (event.child_agent_icon as string) || undefined,
          is_dynamic: Boolean(event.is_dynamic),
          task: (event.task as string) || '',
          status: 'running' as const,
          thinking: '',
          toolCalls: [],
          tool_call_id: delegToolCallId,
        };

        setStreaming((prev) => {
          // 在 actionOrder 中找到对应 delegate_task 工具项并替换为委派项
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

      // ---- 确认事件 (AskUserQuestion) ----
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

      case 'closed':
        // 回放流正常结束但未收到 done（如空流/任务已结束）：复位 streaming
        abortRef.current = null;
        if (runRef.current?.status !== 'running') {
          setStreaming((prev) => (prev.isStreaming ? IDLE_STREAMING : prev));
        }
        break;
    }
  }, []);

  consumeRef.current = handleEvent;

  // Refresh/mount/session switch and network failures all use the same recovery.
  // Each reconnect rebuilds the current turn from sequence zero instead of
  // appending a replay to the previous transient state.
  useEffect(() => {
    if (turnSessionIdRef.current !== activeSessionId) {
      disconnect();
      turnSessionIdRef.current = activeSessionId;
    }
    if (!activeSessionId) return;
    let disposed = false;
    let busy = false;
    const recover = async () => {
      if (disposed || busy || abortRef.current) return;
      busy = true;
      const generation = generationRef.current;
      try {
        const info = await chatApi.latestRun(activeSessionId);
        if (disposed || generation !== generationRef.current || abortRef.current) return;
        const changed = info?.id !== runRef.current?.id || info?.status !== runRef.current?.status;
        runRef.current = info;
        setRun(info);
        if (info?.status === 'running') {
          await useChatStore.getState().loadSessionMessages(activeSessionId);
          if (disposed || generation !== generationRef.current || abortRef.current) return;
          const consume = beginTurn(activeSessionId, { flushOnDone: false });
          abortRef.current = chatApi.watchRun(info.id, consume);
        } else if (changed) {
          setStreaming(IDLE_STREAMING);
          await useChatStore.getState().loadSessionMessages(activeSessionId);
        }
      } catch {
        if (!disposed) setError('暂时无法读取后台任务状态，正在重试…');
      } finally {
        busy = false;
      }
    };
    void recover();
    const timer = window.setInterval(() => void recover(), 3000);
    return () => { disposed = true; window.clearInterval(timer); };
  }, [activeSessionId, beginTurn, disconnect]);

  const resume = useCallback(() => {
    const task = runRef.current;
    if (!task?.can_resume) return;
    const consume = beginTurn(task.session_id, { flushOnDone: false });
    abortRef.current = chatApi.resumeRun(task.id, consume);
  }, [beginTurn]);

  return {
    run,
    stopping,
    resume,
    disconnect,
    streaming,
    setStreaming,
    error,
    setError,
    abortRef,
    /** 当前轮会话 id（只读场景用，如 onEvent 前置钩子需要会话上下文） */
    turnSessionIdRef,
    beginTurn,
    interrupt,
    handleEvent,
  };
}
