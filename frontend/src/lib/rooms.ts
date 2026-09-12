import { apiFetch, ApiError, request } from './api';
import type { ChatAttachment, ConfirmationRequest, ConfirmationResponse, FileAttachmentRef, Message } from './types';

export interface RoomMember {
  id: string;
  agent_id: string;
  name: string;
  icon: string | null;
  description: string | null;
  position: number;
  is_active: boolean;
  available: boolean;
}

export interface RoomSummary {
  id: string;
  title: string;
  goal: string;
  workspace_id: string;
  default_member_id: string;
  is_pinned: boolean;
  is_archived: boolean;
  revision: number;
  updated_at: string;
}

export interface RoomMessage extends Message {
  name: string;
  icon: string | null;
  member_id: string | null;
  run_id: string | null;
  task_id?: string;
  sequence: number;
  status: string;
  reply_to_id: string | null;
  member_ids?: string[];
}

export interface RoomTask {
  id: string;
  run_id: string;
  member_id: string;
  message_id: string | null;
  position: number;
  status: string;
  error: string | null;
  token_usage: { total_tokens: number } | null;
  duration_ms: number | null;
  confirmation: (ConfirmationRequest & { confirmation_id: string }) | null;
  response_submitted?: boolean;
}

export interface RoomRun {
  id: string;
  status: string;
  error: string | null;
  stop_requested: boolean;
  created_at: string;
}

export interface Room extends RoomSummary {
  members: RoomMember[];
  messages: RoomMessage[];
  runs: RoomRun[];
  tasks: RoomTask[];
  has_more: boolean;
}

export interface RoomSend {
  request_id: string;
  message: string;
  mode: 'default' | 'mentions' | 'all' | 'summary';
  member_ids: string[];
  reply_to_id?: string;
  attachments?: ChatAttachment[];
  file_attachments?: FileAttachmentRef[];
}

export const RUNNING = new Set(['queued', 'running', 'stopping']);
export const STATUS: Record<string, string> = {
  queued: '等待', running: '执行中', waiting_user: '等待用户', stopping: '停止中',
  stopped: '已停止', cancelled: '已取消', completed: '完成', failed: '失败',
  partial: '部分完成', interrupted: '执行中断',
};

/** Revision snapshots replace current state; historical pages survive refreshes. */
export function mergeRoom(previous: Room | null, next: Room): Room {
  if (!previous || previous.id !== next.id) return next;
  if (next.revision < previous.revision) return previous;
  const messages = new Map(previous.messages.map(m => [m.id, m]));
  next.messages.forEach(m => messages.set(m.id, m));
  const tasks = new Map(previous.tasks.map(t => [t.id, t]));
  next.tasks.forEach(t => tasks.set(t.id, t));
  return { ...next, messages: [...messages.values()].sort((a, b) => a.sequence - b.sequence),
    tasks: [...tasks.values()], has_more: previous.messages[0]?.sequence < (next.messages[0]?.sequence ?? 0)
      ? previous.has_more : next.has_more };
}

export function mergeRoomHistory(current: Room, page: Room): Room {
  const messages = new Map(page.messages.map(m => [m.id, m]));
  current.messages.forEach(m => messages.set(m.id, m));
  const tasks = new Map(page.tasks.map(t => [t.id, t]));
  current.tasks.forEach(t => tasks.set(t.id, t));
  return { ...current, messages: [...messages.values()].sort((a, b) => a.sequence - b.sequence),
    tasks: [...tasks.values()], has_more: page.has_more };
}

export const roomsApi = {
  list: (offset = 0) => request<RoomSummary[]>(`/rooms?offset=${offset}`),
  get: (id: string, before?: number) => request<Room>(`/rooms/${id}${before ? `?before=${before}` : ''}`),
  getMessage: (id: string, messageId: string) => request<RoomMessage>(`/rooms/${id}/messages/${messageId}`),
  create: (body: { title: string; goal: string; agent_ids: string[]; default_agent_id?: string }) => request<Room>('/rooms', { method: 'POST', body: JSON.stringify(body) }),
  update: (id: string, body: { title?: string; goal?: string; agent_ids?: string[]; default_agent_id?: string; default_member_id?: string; is_pinned?: boolean; is_archived?: boolean }) => request<Room>(`/rooms/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  delete: (id: string) => request<void>(`/rooms/${id}`, { method: 'DELETE' }),
  send: (id: string, body: RoomSend) => request<{ run_id: string }>(`/rooms/${id}/runs`, { method: 'POST', body: JSON.stringify(body) }),
  stop: (id: string, runId: string) => request(`/rooms/${id}/runs/${runId}/stop`, { method: 'POST' }),
  retry: (id: string, taskId: string, requestId: string, acknowledge: boolean) => request(`/rooms/${id}/tasks/${taskId}/retry`, { method: 'POST', body: JSON.stringify({ request_id: requestId, acknowledge_side_effects: acknowledge }) }),
  respond: (id: string, taskId: string, confirmationId: string, response: ConfirmationResponse) => request<void>(`/rooms/${id}/tasks/${taskId}/respond`, { method: 'POST', body: JSON.stringify({ ...response, confirmation_id: confirmationId }) }),

  watch(id: string, onRoom: (room: Room) => void, onConnection: (state: 'connected' | 'retrying' | 'denied') => void) {
    const controller = new AbortController();
    let revision = -1;
    let delay = 1000;
    const run = async () => {
      while (!controller.signal.aborted) {
        try {
          const response = await apiFetch(`/rooms/${id}/events?after_revision=${revision}`, { signal: controller.signal });
          const reader = response.body?.getReader();
          if (!reader) throw new Error('无法读取聊天室事件');
          const decoder = new TextDecoder();
          let buffer = '';
          onConnection('connected');
          delay = 1000;
          try {
            while (!controller.signal.aborted) {
              const { done, value } = await reader.read();
              if (done) break;
              buffer += decoder.decode(value, { stream: true });
              let boundary: number;
              while ((boundary = buffer.indexOf('\n\n')) !== -1) {
                const frame = buffer.slice(0, boundary);
                buffer = buffer.slice(boundary + 2);
                const line = frame.split('\n').find(l => l.startsWith('data: '));
                if (!line) continue;
                const event = JSON.parse(line.slice(6));
                if (event.type === 'access_revoked') {
                  onConnection('denied');
                  controller.abort();
                  return;
                }
                if (event.type === 'snapshot' && event.room.revision >= revision) {
                  revision = event.room.revision;
                  onRoom(event.room);
                }
              }
            }
          } finally {
            await reader.cancel().catch(() => {});
            reader.releaseLock();
          }
        } catch (error) {
          if (controller.signal.aborted) return;
          if (error instanceof ApiError && [403, 404].includes(error.status)) {
            onConnection('denied');
            return;
          }
          onConnection('retrying');
        }
        if (!controller.signal.aborted) await new Promise<void>(resolve => {
          const onAbort = () => { clearTimeout(timer); resolve(); };
          const timer = setTimeout(() => { controller.signal.removeEventListener('abort', onAbort); resolve(); }, delay);
          controller.signal.addEventListener('abort', onAbort, { once: true });
        });
        delay = Math.min(delay * 2, 10000);
      }
    };
    void run();
    return () => controller.abort();
  },
};
