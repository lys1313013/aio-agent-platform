/** Explicit mention spans bind visible text to stable member ids, including names with spaces. */
export const ALL_MEMBERS = '__all__';

export interface MentionSpan {
  start: number;
  end: number;
  id: string;
  label: string;
}

export interface MentionDraft {
  text: string;
  mentions: MentionSpan[];
}

export interface MentionCandidate { id: string; label: string; description?: string | null; icon?: string | null }

export const emptyMentionDraft = (): MentionDraft => ({ text: '', mentions: [] });

export function readMentionDraft(saved: string | null): MentionDraft {
  if (!saved) return emptyMentionDraft();
  try {
    const data = JSON.parse(saved);
    if (typeof data.text === 'string' && Array.isArray(data.mentions)) {
      return { text: data.text, mentions: data.mentions.filter((m: MentionSpan) =>
        typeof m.id === 'string' && typeof m.label === 'string' && Number.isInteger(m.start)
        && Number.isInteger(m.end) && m.start >= 0 && m.end > m.start
        && data.text.slice(m.start, m.end) === `@${m.label}`) };
    }
  } catch { /* Existing drafts were stored as plain text. */ }
  return { text: saved, mentions: [] };
}

/** Preserve untouched tokens and invalidate tokens edited or pasted over. */
export function editMentionDraft(draft: MentionDraft, text: string): MentionDraft {
  if (text === draft.text) return draft;
  let start = 0;
  while (start < draft.text.length && start < text.length && draft.text[start] === text[start]) start++;
  let oldEnd = draft.text.length;
  let newEnd = text.length;
  while (oldEnd > start && newEnd > start && draft.text[oldEnd - 1] === text[newEnd - 1]) { oldEnd--; newEnd--; }
  const delta = newEnd - oldEnd;
  const mentions = draft.mentions.flatMap(mention => {
    if (mention.end <= start) return [mention];
    if (mention.start >= oldEnd) return [{ ...mention, start: mention.start + delta, end: mention.end + delta }];
    return [];
  }).filter(mention => text.slice(mention.start, mention.end) === `@${mention.label}`);
  return { text, mentions };
}

export function mentionQuery(draft: MentionDraft, caret: number) {
  const start = draft.text.lastIndexOf('@', caret - 1);
  if (start < 0 || /[\w.+-]/.test(draft.text[start - 1] || '')) return null;
  if (draft.mentions.some(m => caret > m.start && caret <= m.end)) return null;
  const query = draft.text.slice(start + 1, caret);
  if (query.includes('\n') || query.length > 80) return null;
  return { start, end: caret, query };
}

export function insertMention(draft: MentionDraft, range: { start: number; end: number }, candidate: MentionCandidate) {
  const token = `@${candidate.label}`;
  const text = draft.text.slice(0, range.start) + token + ' ' + draft.text.slice(range.end);
  let next = editMentionDraft(draft, text);
  next.mentions.push({ start: range.start, end: range.start + token.length, id: candidate.id, label: candidate.label });
  next.mentions.sort((a, b) => a.start - b.start);
  let caret = range.start + token.length + 1;
  // Choosing @全体成员 replaces individual mentions; choosing an individual
  // replaces @全体成员. Remove the visible conflicting tokens as well as their ids.
  const conflicts = next.mentions.filter(m => candidate.id === ALL_MEMBERS
    ? m.start !== range.start : m.id === ALL_MEMBERS).sort((a, b) => b.start - a.start);
  for (const conflict of conflicts) {
    const end = conflict.end + (next.text[conflict.end] === ' ' ? 1 : 0);
    const removed = end - conflict.start;
    next = {
      text: next.text.slice(0, conflict.start) + next.text.slice(end),
      mentions: next.mentions.filter(m => m.start !== conflict.start).map(m => m.start >= end
        ? { ...m, start: m.start - removed, end: m.end - removed } : m),
    };
    if (conflict.start < caret) caret -= end - conflict.start;
  }
  return { draft: next, caret };
}

export function mentionRecipients(draft: MentionDraft) {
  const ids = [...new Set([...draft.mentions].sort((a, b) => a.start - b.start).map(m => m.id))];
  return { all: ids.includes(ALL_MEMBERS), ids: ids.filter(id => id !== ALL_MEMBERS) };
}
