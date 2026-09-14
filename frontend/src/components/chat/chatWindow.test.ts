import { act, createElement, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ChatInput, { type ChatInputProps } from './ChatInput';
import ChatWindow from './ChatWindow';
import StreamingMessage from './StreamingMessage';
import MessageList from './MessageList';
import type { Message } from '@/lib/types';
import { chatApi } from '@/lib/api';
import { emptyMentionDraft, mentionRecipients } from '@/lib/roomMentions';
import { roomMessageStreaming, type RoomMessage } from '@/lib/rooms';

let root: Root;
let container: HTMLDivElement;
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

const editor = () => container.querySelector('textarea')!;
async function type(text: string) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(editor(), text);
    editor().dispatchEvent(new Event('input', { bubbles: true }));
  });
}
async function key(key: string, options: KeyboardEventInit = {}) {
  await act(async () => editor().dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, ...options })));
}
const submit = async () => { await act(async () => container.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))); };

function RoomInput(props: Partial<ChatInputProps> & { onRecipients?: (ids: string[]) => void }) {
  const [draft, setDraft] = useState(emptyMentionDraft);
  return createElement(ChatInput, {
    sessionId: 'room-id', fixedWorkspace: true,
    onSend: () => true, ...props,
    mentions: { value: draft, onChange: next => { setDraft(next); props.onRecipients?.(mentionRecipients(next).ids); }, candidates: [{ id: 'member-a', label: '全能 VV' }] },
  });
}

describe('shared conversation composer', () => {
  it('retains text after a rejected send and clears only after acceptance', async () => {
    const onSend = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
    await act(async () => root.render(createElement(ChatInput, { simple: true, onSend })));
    await type('保留这条消息');
    await submit();
    expect(editor().value).toBe('保留这条消息');
    await submit();
    expect(onSend).toHaveBeenCalledTimes(2);
    expect(editor().value).toBe('');
  });

  it('queues normal messages, but never resubmits a busy room draft', async () => {
    const onSend = vi.fn();
    const onQueue = vi.fn().mockReturnValue(true);
    await act(async () => root.render(createElement(ChatInput, { simple: true, onSend, onQueue, isStreaming: true })));
    await type('下一条');
    await key('Enter');
    expect(onQueue).toHaveBeenCalledWith('下一条', [], undefined);
    expect(onSend).not.toHaveBeenCalled();
    await act(async () => root.render(createElement(RoomInput, { onSend, isStreaming: true, onStop: vi.fn() })));
    await type('聊天室草稿');
    await key('Enter');
    await submit();
    expect(editor().value).toBe('聊天室草稿');
    expect(onSend).not.toHaveBeenCalled();
    expect(container.querySelector('[aria-label="加入队列"]')).toBeNull();
    expect(container.querySelector('[aria-label="停止生成"]')).not.toBeNull();
  });

  it('binds names containing spaces before Enter sends, and ignores IME/Shift+Enter', async () => {
    const onSend = vi.fn().mockReturnValue(false);
    const onRecipients = vi.fn();
    await act(async () => root.render(createElement(RoomInput, { onSend, onRecipients })));
    await act(async () => editor().focus());
    await type('@');
    await act(async () => {
      editor().setSelectionRange(1, 1);
      editor().dispatchEvent(new Event('select', { bubbles: true }));
    });
    await key('Enter');
    expect(editor().value).toBe('@全能 VV ');
    expect(onRecipients).toHaveBeenLastCalledWith(['member-a']);
    expect(onSend).not.toHaveBeenCalled();
    await key('Enter', { isComposing: true });
    await key('Enter', { shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();
    await key('Enter');
    expect(onSend).toHaveBeenCalledWith('@全能 VV', [], undefined);
  });

  it('uploads to the room workspace, blocks partial sends, and retains files after rejection', async () => {
    let finishUpload!: (value: unknown) => void;
    const upload = vi.spyOn(chatApi, 'uploadFile').mockImplementation(() => new Promise(resolve => { finishUpload = resolve as typeof finishUpload; }));
    const onSend = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
    await act(async () => root.render(createElement(RoomInput, { onSend })));
    const file = new File(['room document'], 'notes.txt', { type: 'text/plain' });
    await act(async () => {
      const input = container.querySelector('input[type="file"]')!;
      Object.defineProperty(input, 'files', { value: [file] });
      input.dispatchEvent(new Event('change', { bubbles: true }));
    });
    expect(upload).toHaveBeenCalledWith(file, 'room-id');
    await submit();
    expect(onSend).not.toHaveBeenCalled();
    const uploaded = { file_id: 'f', filename: 'notes.txt', mime: 'text/plain', size: 13, workspace_path: 'uploads/f_notes.txt' };
    await act(async () => finishUpload(uploaded));
    await submit();
    expect(container.textContent).toContain('notes.txt');
    await submit();
    expect(onSend).toHaveBeenLastCalledWith('', [], [uploaded]);
    expect(container.textContent).not.toContain('notes.txt');
  });

  it('locks an in-flight submit against double Enter', async () => {
    let accept!: (value: boolean) => void;
    const onSend = vi.fn(() => new Promise<boolean>(resolve => { accept = resolve; }));
    await act(async () => root.render(createElement(ChatInput, { simple: true, onSend })));
    await type('一次');
    await submit();
    await submit();
    expect(onSend).toHaveBeenCalledTimes(1);
    await act(async () => accept(true));
    expect(editor().value).toBe('');
  });
});

it('renders room snapshots through the same live renderer and preserves room controls', async () => {
  const roomMessage: RoomMessage = { id: 'r1', role: 'assistant', name: '成员', icon: null, member_id: 'a', run_id: 'run', sequence: 1, status: 'running', reply_to_id: null, content: '', created_at: '2026-09-13T00:00:00Z' };
  const render = (msg: RoomMessage) => root.render(createElement(ChatWindow<RoomMessage>, {
    messages: { messages: [msg], workspaceId: 'room-workspace', conversationId: 'room',
      renderMessage: message => createElement(StreamingMessage, { streaming: roomMessageStreaming(message), workspaceId: 'room-workspace' }),
      afterMessages: createElement('button', null, '成员确认'),
    }, input: { simple: true, onSend: vi.fn() },
  }));
  await act(async () => render(roomMessage));
  expect(container.textContent).toContain('等待模型响应');
  await act(async () => render({ ...roomMessage, reasoning: [{ id: '0', content: '正在分析条件' }] }));
  expect(container.textContent).toContain('正在分析条件');
  expect(container.querySelector('[aria-expanded]')?.getAttribute('aria-expanded')).toBe('true');
  await act(async () => render({ ...roomMessage, content: '回答已到达', reasoning: [{ id: '0', content: '正在分析条件' }] }));
  expect(container.textContent).toContain('回答已到达');
  expect(container.querySelector('[aria-expanded]')?.getAttribute('aria-expanded')).toBe('false');
  expect(container.textContent).toContain('成员确认');
});

it('preserves the reading position when older room messages are prepended', async () => {
  const recent: Message = { id: 'recent', role: 'assistant', content: '当前消息', created_at: '' };
  const older: Message = { ...recent, id: 'older', content: '历史消息' };
  const render = (messages: Message[]) => root.render(createElement(MessageList, {
    messages, conversationId: 'room', renderMessage: msg => createElement('p', null, msg.content),
  }));
  await act(async () => render([recent]));
  const scroll = container.querySelector('[data-ui-exclude]') as HTMLDivElement;
  Object.defineProperty(scroll, 'clientHeight', { value: 100 });
  Object.defineProperty(scroll, 'scrollHeight', { get: () => container.querySelectorAll('p').length === 1 ? 500 : 800 });
  scroll.scrollTop = 100;
  await act(async () => scroll.dispatchEvent(new Event('scroll')));
  await act(async () => render([{ ...recent }]));
  await act(async () => render([older, recent]));
  expect(scroll.scrollTop).toBe(400);
});
