import type { ToolCallInfo } from '@/lib/types';

interface MemoryReference {
  content: string;
  layer?: string;
  scope?: string;
}

export function memoryMutationView(toolCall: ToolCallInfo) {
  let result: { action?: string; memory?: MemoryReference; before?: MemoryReference } | null = null;
  try {
    const parsed = JSON.parse(toolCall.result?.preview || 'null');
    if (parsed && typeof parsed === 'object') result = parsed;
  } catch { /* Historical tool results are plain text. */ }
  const status = toolCall.result?.status;
  const action = result?.action;
  const labels: Record<string, string> = {
    created: '已新增记忆', updated: '已修改记忆', merged: '已合并记忆',
    deleted: '已删除记忆', skipped: '记忆未变更',
  };
  const successFallback = toolCall.name === 'memory_update' ? '已修改记忆'
    : toolCall.name === 'memory_delete' ? '已删除记忆' : '记忆已保存';
  return {
    title: !toolCall.result ? '正在处理记忆' : status === 'err' ? '记忆操作失败'
      : (action && labels[action]) || successFallback,
    content: status === 'ok' && typeof result?.memory?.content === 'string'
      ? result.memory.content : toolCall.arguments.content as string | undefined,
    layer: result?.memory?.layer || toolCall.arguments.layer as string | undefined,
    scope: result?.memory?.scope,
    before: status === 'ok' && action === 'updated' ? result?.before?.content : undefined,
  };
}
