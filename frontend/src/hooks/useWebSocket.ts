import { useCallback, useEffect, useRef, useState } from 'react';
import { chatApi } from '@/lib/api';
import type { StreamingState, WsServerMessage } from '@/lib/types';

const INITIAL_STATE: StreamingState = {
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

export function useWebSocket(sessionId: string | null) {
  const wsRef = useRef<WebSocket | null>(null);
  const [streaming, setStreaming] = useState<StreamingState>(INITIAL_STATE);
  const [error, setError] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);
  const doneRef = useRef<(() => void) | null>(null);
  // Queue for messages waiting to be sent after connection opens
  const pendingMessageRef = useRef<string | null>(null);
  const pendingDoneRef = useRef<(() => void) | null>(null);

  // Connect when sessionId changes
  useEffect(() => {
    if (!sessionId) {
      setConnected(false);
      return;
    }

    const url = chatApi.wsUrl(sessionId);
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      // Send any queued message
      if (pendingMessageRef.current !== null) {
        const msg = pendingMessageRef.current;
        const done = pendingDoneRef.current;
        pendingMessageRef.current = null;
        pendingDoneRef.current = null;

        setError(null);
        setStreaming(INITIAL_STATE);
        doneRef.current = done;

        ws.send(JSON.stringify({ type: 'message', content: msg }));
      }
    };

    ws.onmessage = (event) => {
      try {
        const msg: WsServerMessage = JSON.parse(event.data);

        switch (msg.type) {
          case 'thinking':
            setStreaming((prev) => ({
              ...prev,
              isStreaming: true,
              thinking: prev.thinking + (msg.content || ''),
            }));
            break;

          case 'tool_call':
            setStreaming((prev) => ({
              ...prev,
              toolCalls: [
                ...prev.toolCalls,
                {
                  id: msg.id || '',
                  name: msg.name || '',
                  arguments: msg.arguments || {},
                },
              ],
            }));
            break;

          case 'tool_result':
            setStreaming((prev) => ({
              ...prev,
              toolCalls: prev.toolCalls.map((tc) =>
                tc.id === msg.tool_call_id
                  ? { ...tc, result: { status: msg.status || '', preview: msg.preview || '' } }
                  : tc,
              ),
            }));
            break;

          case 'text':
            setStreaming((prev) => ({
              ...prev,
              finalText: msg.content || '',
            }));
            break;

          case 'done':
            setStreaming((prev) => ({ ...prev, isStreaming: false }));
            doneRef.current?.();
            doneRef.current = null;
            break;

          case 'error':
            setError(msg.message || 'Unknown error');
            setStreaming((prev) => ({ ...prev, isStreaming: false }));
            doneRef.current?.();
            doneRef.current = null;
            break;
        }
      } catch {
        /* ignore malformed messages */
      }
    };

    ws.onerror = () => {
      setError('WebSocket 连接失败');
      setConnected(false);
    };

    ws.onclose = () => {
      setStreaming((prev) => ({ ...prev, isStreaming: false }));
      setConnected(false);
    };

    return () => {
      ws.close();
      wsRef.current = null;
      setConnected(false);
    };
  }, [sessionId]);

  /**
   * Send a message through the WebSocket.
   * Accepts an optional `targetSessionId` so messages can be queued
   * even before the hook's `sessionId` has updated (e.g., right after
   * creating a new session).
   */
  const sendMessage = useCallback(
    (content: string, targetSessionId?: string): Promise<void> => {
      return new Promise((resolve) => {
        const ws = wsRef.current;

        if (ws && ws.readyState === WebSocket.OPEN) {
          // Connected — send immediately
          setError(null);
          setStreaming(INITIAL_STATE);
          doneRef.current = resolve;
          ws.send(JSON.stringify({ type: 'message', content }));
        } else if (targetSessionId || sessionId) {
          // Not connected yet but we have a session — queue the message.
          // It will be sent automatically in ws.onopen.
          // If the WS isn't even created yet (new session), create it now.
          const sid = targetSessionId || sessionId;

          if (!ws || ws.readyState === WebSocket.CLOSED || ws.readyState === WebSocket.CLOSING) {
            // Need to create a new WebSocket for this session
            const url = chatApi.wsUrl(sid!);
            const newWs = new WebSocket(url);
            wsRef.current = newWs;

            newWs.onopen = () => {
              setConnected(true);
              setError(null);
              setStreaming(INITIAL_STATE);
              doneRef.current = resolve;
              newWs.send(JSON.stringify({ type: 'message', content }));
            };

            newWs.onmessage = (event) => {
              try {
                const msg: WsServerMessage = JSON.parse(event.data);
                handleServerMessage(msg);
              } catch {
                /* ignore */
              }
            };

            newWs.onerror = () => {
              setError('WebSocket 连接失败');
              setConnected(false);
              resolve();
            };

            newWs.onclose = () => {
              setStreaming((prev) => ({ ...prev, isStreaming: false }));
              setConnected(false);
            };
          } else {
            // WS exists but still connecting — queue
            pendingMessageRef.current = content;
            pendingDoneRef.current = resolve;
          }
        } else {
          setError('没有活跃的会话');
          resolve();
        }
      });
    },
    [sessionId],
  );

  const handleServerMessage = (msg: WsServerMessage) => {
    switch (msg.type) {
      case 'thinking':
        setStreaming((prev) => ({
          ...prev,
          isStreaming: true,
          thinking: prev.thinking + (msg.content || ''),
        }));
        break;
      case 'tool_call':
        setStreaming((prev) => ({
          ...prev,
          toolCalls: [
            ...prev.toolCalls,
            {
              id: msg.id || '',
              name: msg.name || '',
              arguments: msg.arguments || {},
            },
          ],
        }));
        break;
      case 'tool_result':
        setStreaming((prev) => ({
          ...prev,
          toolCalls: prev.toolCalls.map((tc) =>
            tc.id === msg.tool_call_id
              ? { ...tc, result: { status: msg.status || '', preview: msg.preview || '' } }
              : tc,
          ),
        }));
        break;
      case 'text':
        setStreaming((prev) => ({ ...prev, finalText: msg.content || '' }));
        break;
      case 'done':
        setStreaming((prev) => ({ ...prev, isStreaming: false }));
        doneRef.current?.();
        doneRef.current = null;
        break;
      case 'error':
        setError(msg.message || 'Unknown error');
        setStreaming((prev) => ({ ...prev, isStreaming: false }));
        doneRef.current?.();
        doneRef.current = null;
        break;
    }
  };

  const resetStreaming = useCallback(() => {
    setStreaming(INITIAL_STATE);
    setError(null);
  }, []);

  return { streaming, error, connected, sendMessage, resetStreaming };
}
