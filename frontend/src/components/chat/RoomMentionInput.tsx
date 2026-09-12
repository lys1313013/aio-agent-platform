import { useId, useLayoutEffect, useRef, useState } from 'react';
import { TeamOutlined } from '@ant-design/icons';
import { getAgentIcon } from '@/lib/agent-icons';
import { ALL_MEMBERS, editMentionDraft, insertMention, mentionQuery } from '@/lib/roomMentions';
import type { MentionCandidate, MentionDraft } from '@/lib/roomMentions';

interface Props {
  value: MentionDraft;
  onChange: (value: MentionDraft) => void;
  candidates: MentionCandidate[];
  onSend: () => void;
  disabled?: boolean;
  busy?: boolean;
}

export default function RoomMentionInput({ value, onChange, candidates, onSend, disabled, busy }: Props) {
  const textarea = useRef<HTMLTextAreaElement>(null);
  const mirror = useRef<HTMLDivElement>(null);
  const [caret, setCaret] = useState(0);
  const [focused, setFocused] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const listId = useId();
  const query = focused && !dismissed && !disabled ? mentionQuery(value, caret) : null;
  const matches = query ? candidates.filter(c => c.label.toLocaleLowerCase().includes(query.query.toLocaleLowerCase())) : [];
  const open = matches.length > 0;
  const highlighted = Math.min(activeIndex, Math.max(0, matches.length - 1));

  useLayoutEffect(() => {
    const el = textarea.current;
    if (!el) return;
    const resize = () => {
      el.style.height = 'auto';
      el.style.height = `${Math.min(192, Math.max(80, el.scrollHeight))}px`;
      if (mirror.current) {
        mirror.current.style.width = `${el.clientWidth}px`;
        mirror.current.style.transform = `translateY(-${el.scrollTop}px)`;
      }
    };
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(el);
    return () => observer.disconnect();
  }, [value.text]);

  const pick = (candidate: MentionCandidate) => {
    if (!query) return;
    const result = insertMention(value, query, candidate);
    onChange(result.draft);
    setCaret(result.caret);
    setDismissed(true);
    requestAnimationFrame(() => {
      textarea.current?.focus();
      textarea.current?.setSelectionRange(result.caret, result.caret);
    });
  };

  const highlightedText = [];
  let position = 0;
  for (const mention of [...value.mentions].sort((a, b) => a.start - b.start)) {
    highlightedText.push(value.text.slice(position, mention.start));
    highlightedText.push(<mark key={`${mention.start}:${mention.id}`} className="rounded bg-primary/10 text-primary">{value.text.slice(mention.start, mention.end)}</mark>);
    position = mention.end;
  }
  highlightedText.push(value.text.slice(position));

  return <div className="relative">
    {open && <div id={listId} role="listbox" aria-label="选择要点名的成员" className="absolute bottom-full left-0 z-30 mb-2 max-h-56 w-56 max-w-full overflow-auto rounded-lg border border-border bg-card p-1 shadow-lg">
      {matches.map((candidate, i) => <button
        key={candidate.id} id={`${listId}-${i}`} type="button" role="option" aria-selected={highlighted === i}
        className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left ${highlighted === i ? 'bg-primary/10' : 'hover:bg-muted'}`}
        onMouseDown={event => event.preventDefault()} onClick={() => pick(candidate)} onMouseEnter={() => setActiveIndex(i)}
      >
        <span className="flex h-5 w-5 shrink-0 items-center justify-center text-muted-foreground">
          {candidate.id === ALL_MEMBERS ? <TeamOutlined /> : getAgentIcon(candidate.icon || 'robot', undefined, 17)}
        </span>
        <span className="min-w-0 truncate text-sm">{candidate.label}</span>
      </button>)}
    </div>}
    <div className={`relative overflow-hidden rounded-lg border border-border bg-background focus-within:border-primary focus-within:ring-1 focus-within:ring-primary/20 ${disabled ? 'opacity-60' : ''}`}>
      <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden">
        <div ref={mirror} className="whitespace-pre-wrap break-words px-3 py-2 text-sm leading-6 text-foreground">{highlightedText}{'\u200b'}</div>
      </div>
      <textarea
        ref={textarea} aria-label="聊天室消息" aria-autocomplete="list" aria-controls={open ? listId : undefined}
        aria-activedescendant={open ? `${listId}-${highlighted}` : undefined}
        value={value.text} disabled={disabled} maxLength={50000} rows={2}
        className="relative block w-full resize-none bg-transparent px-3 py-2 text-sm leading-6 text-transparent caret-foreground outline-none placeholder:text-muted-foreground/60"
        placeholder={busy ? '可继续编辑草稿，停止或等待本轮结束后发送' : '输入问题，@ 点名成员或全体成员；Ctrl / ⌘ + Enter 发送'}
        onChange={event => {
          onChange(editMentionDraft(value, event.target.value));
          setCaret(event.target.selectionStart);
          setActiveIndex(0);
          setDismissed(false);
        }}
        onSelect={event => setCaret(event.currentTarget.selectionStart)}
        onFocus={() => setFocused(true)} onBlur={() => setFocused(false)}
        onScroll={event => { if (mirror.current) mirror.current.style.transform = `translateY(-${event.currentTarget.scrollTop}px)`; }}
        onKeyDown={event => {
          if (event.nativeEvent.isComposing) return;
          if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); onSend(); return; }
          if (!open) return;
          if (event.key === 'Escape') { event.preventDefault(); setDismissed(true); }
          else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault(); setActiveIndex((highlighted + (event.key === 'ArrowDown' ? 1 : -1) + matches.length) % matches.length);
          } else if (event.key === 'Enter' || event.key === 'Tab') { event.preventDefault(); pick(matches[highlighted]); }
        }}
      />
    </div>
  </div>;
}
