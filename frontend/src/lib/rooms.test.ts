import { describe, expect, it } from 'vitest';
import { mergeRoom, mergeRoomHistory, type Room, type RoomMessage } from './rooms';

function room(revision: number, messages: Array<[string, number, string]>, hasMore = false): Room {
  return {
    id: 'room', title: '评审', goal: '需求评审', workspace_id: 'ws', default_member_id: 'a',
    is_pinned: false, is_archived: false, revision, updated_at: '', members: [], tasks: [], runs: [],
    has_more: hasMore, messages: messages.map(([id, sequence, content]) => ({
      id, sequence, content, name: '架构', role: 'assistant', member_id: 'a', run_id: 'run',
      status: 'completed', icon: 'robot', reply_to_id: null, created_at: '',
    } as RoomMessage)),
  };
}

describe('room snapshot recovery', () => {
  it('replaces partial content without duplicating the message', () => {
    const initial = room(1, [['m1', 1, '部分']]);
    const update = room(2, [['m1', 1, '完整回答'], ['m2', 2, '下一位']]);
    const merged = mergeRoom(mergeRoom(initial, update), update);
    expect(merged.messages.map(m => m.content)).toEqual(['完整回答', '下一位']);
  });

  it('ignores late snapshots from an older execution state', () => {
    const current = room(8, [['m1', 1, '已停止']]);
    expect(mergeRoom(current, room(7, [['m1', 1, '执行中']]))).toBe(current);
  });

  it('keeps old pages while applying new live messages', () => {
    const current = room(8, [['old', 1, '旧记录'], ['m2', 102, '完成']]);
    const next = room(9, [['m2', 102, '完成'], ['m3', 103, '新增']], true);
    const merged = mergeRoom(current, next);
    expect(merged.messages.map(m => m.sequence)).toEqual([1, 102, 103]);
    expect(merged.has_more).toBe(false);
  });

  it('loads an older page without rolling back the latest run revision', () => {
    const current = room(10, [['m3', 3, '最新内容']], true);
    const older = room(8, [['m1', 1, '早期'], ['m3', 3, '旧内容']]);
    const merged = mergeRoomHistory(current, older);
    expect(merged.revision).toBe(10);
    expect(merged.messages.map(m => m.content)).toEqual(['早期', '最新内容']);
    expect(merged.has_more).toBe(false);
  });

  it('does not mix different rooms', () => {
    const next = { ...room(0, []), id: 'other' };
    expect(mergeRoom(room(10, [['secret', 1, '另一房间']]), next)).toBe(next);
  });
});
