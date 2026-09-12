import { describe, expect, it } from 'vitest';
import { ALL_MEMBERS, editMentionDraft, insertMention, mentionQuery, mentionRecipients, readMentionDraft } from './roomMentions';

const add = (text = '@', id = 'a', label = '全能 VV') => insertMention({ text, mentions: [] }, { start: text.indexOf('@'), end: text.indexOf('@') + 1 }, { id, label }).draft;

describe('inline room mentions', () => {
  it('binds names containing spaces and restores draft bindings', () => {
    const draft = add();
    expect(draft.text).toBe('@全能 VV ');
    expect(mentionRecipients(readMentionDraft(JSON.stringify(draft))).ids).toEqual(['a']);
  });
  it('preserves bindings when surrounding text changes and removes edited mentions', () => {
    const draft = add();
    const shifted = editMentionDraft(editMentionDraft(draft, '请问 ' + draft.text), '请问 ' + draft.text + '分析一下');
    expect(shifted.mentions[0].start).toBe(3);
    expect(mentionRecipients(editMentionDraft(shifted, shifted.text.replace('全能', '测试'))).ids).toEqual([]);
    expect(editMentionDraft(draft, '').mentions).toEqual([]);
  });
  it('does not route pasted names or email addresses', () => {
    expect(mentionRecipients(readMentionDraft('@全能 VV')).ids).toEqual([]);
    expect(mentionQuery({ text: 'hi@example.com', mentions: [] }, 5)).toBeNull();
  });
  it('inserts at the cursor and preserves trailing text', () => {
    expect(add('请 @ 分析').text).toBe('请 @全能 VV  分析');
  });
  it('routes multiple members in their visible order', () => {
    const first = add();
    const draft = editMentionDraft(first, first.text + '@');
    const second = insertMention(draft, { start: draft.text.length - 1, end: draft.text.length }, { id: 'b', label: '测试' }).draft;
    expect(mentionRecipients(second).ids).toEqual(['a', 'b']);
  });
  it('replaces individual mentions with all and back', () => {
    const first = add();
    const draft = editMentionDraft(first, first.text + '@');
    const all = insertMention(draft, { start: draft.text.length - 1, end: draft.text.length }, { id: ALL_MEMBERS, label: '全体成员' }).draft;
    expect(all.text).toBe('@全体成员 ');
    expect(mentionRecipients(all)).toEqual({ all: true, ids: [] });
    const query = editMentionDraft(all, all.text + '@');
    const individual = insertMention(query, { start: query.text.length - 1, end: query.text.length }, { id: 'b', label: '测试' }).draft;
    expect(individual.text).toBe('@测试 ');
    expect(mentionRecipients(individual)).toEqual({ all: false, ids: ['b'] });
  });
});
