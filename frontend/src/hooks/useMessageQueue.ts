import { useCallback, useRef, useState } from 'react';
import type { ChatAttachment, FileAttachmentRef } from '@/lib/types';

export interface QueuedMessage {
  id: string;
  content: string;
  attachments: ChatAttachment[];
  fileAttachments?: FileAttachmentRef[];
}

export const MAX_QUEUED_MESSAGES = 10;

type SendFn = (
  content: string,
  attachments: ChatAttachment[],
  fileAttachments?: FileAttachmentRef[],
) => void;

/**
 * Codex-style message queue: while the agent is streaming, user messages are
 * queued locally. `flushNext` (called when a turn completes) auto-sends the
 * head message; `sendNow` interrupts the current turn and sends immediately.
 */
export function useMessageQueue(send: SendFn, interrupt: () => void) {
  const [queue, setQueue] = useState<QueuedMessage[]>([]);
  const queueRef = useRef<QueuedMessage[]>([]);
  const sendRef = useRef(send);
  const interruptRef = useRef(interrupt);
  sendRef.current = send;
  interruptRef.current = interrupt;

  const sync = (next: QueuedMessage[]) => {
    queueRef.current = next;
    setQueue(next);
  };

  const enqueue = useCallback(
    (content: string, attachments: ChatAttachment[], fileAttachments?: FileAttachmentRef[]): boolean => {
      if (queueRef.current.length >= MAX_QUEUED_MESSAGES) return false;
      sync([
        ...queueRef.current,
        {
          id: `q-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
          content,
          attachments,
          fileAttachments,
        },
      ]);
      return true;
    },
    [],
  );

  const remove = useCallback((id: string) => {
    sync(queueRef.current.filter((m) => m.id !== id));
  }, []);

  const clear = useCallback(() => sync([]), []);

  const flushNext = useCallback(() => {
    const [head, ...rest] = queueRef.current;
    if (!head) return;
    sync(rest);
    sendRef.current(head.content, head.attachments, head.fileAttachments);
  }, []);

  const sendNow = useCallback(async (id: string) => {
    const item = queueRef.current.find((m) => m.id === id);
    if (!item) return;
    sync(queueRef.current.filter((m) => m.id !== id));
    interruptRef.current();
    // Give the backend a moment to rescue/persist the interrupted turn
    // before the next request reloads conversation history.
    await new Promise((r) => setTimeout(r, 400));
    sendRef.current(item.content, item.attachments, item.fileAttachments);
  }, []);

  return { queue, enqueue, remove, clear, flushNext, sendNow };
}
