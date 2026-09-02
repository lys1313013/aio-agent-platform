/** useUiActionEvents — 共享 SSE 事件处理 hook（docs/22-浏览器页面自动化 §3.3）。
 *
 * 4 处 SSE 消费点（AgentChatPage handleSend/handleReconnect、ChatPage、
 * PetChatPanel）在各自 onEvent 开头调用 handleUiActionEvent，
 * ui_* 事件统一写入 uiActionStore，由 runner（AppLayout）订阅执行。
 */

import type { UiActionPayload } from '@/stores/uiActionStore';
import { uiActionStore } from '@/stores/uiActionStore';

/** 处理 ui_* SSE 事件；返回 true 表示事件已被消费，调用方无需再处理 */
export function handleUiActionEvent(event: Record<string, unknown>): boolean {
  const type = event.type as string;

  if (type === 'ui_action_required') {
    const payload: UiActionPayload = {
      action_id: (event.action_id as string) || '',
      tool_call_id: (event.tool_call_id as string) || '',
      session_id: (event.session_id as string) || '',
      action: (event.action as string) || '',
      args: (event.args as Record<string, unknown>) || {},
      risk: (event.risk as UiActionPayload['risk']) || 'write',
      confirmed: Boolean(event.confirmed),
      created_at: (event.created_at as string) || new Date().toISOString(),
    };
    uiActionStore.getState().enqueue(payload);
    // eslint-disable-next-line no-console
    console.info('[ui-runner] enqueued', payload.action, payload.action_id);
    return true;
  }

  if (type === 'ui_action_resolved') {
    uiActionStore.getState().markResolved({
      action_id: (event.action_id as string) || '',
      tool_call_id: (event.tool_call_id as string) || '',
      status: (event.status as string) || '',
      result: (event.result as Record<string, unknown>) || undefined,
      resolved_at: (event.resolved_at as string) || undefined,
    });
    return true;
  }

  if (type === 'ui_action_heartbeat') {
    return true; // keepalive，无需处理
  }

  return false;
}
