import { describe, expect, it } from 'vitest';
import type { ToolCallInfo } from '@/lib/types';
import { memoryMutationView } from './memoryResult';

function call(name = 'memory_write', action?: string): ToolCallInfo {
  return {
    id: 'call', name, arguments: { content: 'requested', layer: 'L1' },
    ...(action ? { result: { status: 'ok' as const, preview: JSON.stringify({
      action, memory: { content: 'stored', layer: 'L1', scope: 'user' }, before: { content: 'old' },
    }) } } : {}),
  };
}

describe('memory operation results', () => {
  it('never claims success while pending or after failure', () => {
    expect(memoryMutationView(call()).title).toBe('正在处理记忆');
    const failed = call();
    failed.result = { status: 'err', preview: 'version conflict' };
    expect(memoryMutationView(failed).title).toBe('记忆操作失败');
  });
  it.each([
    ['created', '已新增记忆'], ['updated', '已修改记忆'], ['merged', '已合并记忆'],
    ['deleted', '已删除记忆'], ['skipped', '记忆未变更'],
  ])('displays actual action %s and stored content', (action, title) => {
    expect(memoryMutationView(call('memory_write', action))).toMatchObject({ title, content: 'stored', scope: 'user' });
  });
  it('shows before and after for explicit edits', () => {
    expect(memoryMutationView(call('memory_update', 'updated'))).toMatchObject({ before: 'old', content: 'stored' });
  });
  it('keeps historical plain text results readable', () => {
    const old = call();
    old.result = { status: 'ok', preview: 'Memory saved to L1' };
    expect(memoryMutationView(old)).toMatchObject({ title: '记忆已保存', content: 'requested' });
  });
});
