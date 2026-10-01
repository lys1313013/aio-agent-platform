import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import StreamingMessage from './StreamingMessage';
import ChatMessage from './ChatMessage';
import MessageList from './MessageList';
import type { StreamingState } from '@/lib/types';

const reasoning = '先核对条件，再计算结果。';
const state: StreamingState = {
  isStreaming: true, thinking: reasoning,
  thinkingChunks: [{ id: 'thinking-0', content: reasoning }],
  actionOrder: [{ type: 'thinking', id: 'thinking-0' }],
  finalText: '', toolCalls: [], delegations: [], confirmations: [],
  confirmationsResolved: {}, fileChanges: [],
};
let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

const header = () => container.querySelector('[aria-expanded]') as HTMLElement;

describe('reasoning visibility', () => {
  it('keeps reasoning open when answer text arrives and collapses at turn completion', async () => {
    await act(async () => root.render(createElement(StreamingMessage, { streaming: state })));
    expect(header().getAttribute('aria-expanded')).toBe('true');
    expect(container.textContent).toContain(reasoning);
    await act(async () => root.render(createElement(StreamingMessage, {
      streaming: { ...state, finalText: '答案' },
    })));
    expect(header().getAttribute('aria-expanded')).toBe('true');
    await act(async () => root.render(createElement(StreamingMessage, {
      streaming: { ...state, finalText: '答案', isStreaming: false },
    })));
    expect(header().getAttribute('aria-expanded')).toBe('false');
    await act(async () => header().click());
    expect(header().getAttribute('aria-expanded')).toBe('true');
  });

  it('allows manual collapse while thinking and preserves it on updates', async () => {
    await act(async () => root.render(createElement(StreamingMessage, { streaming: state })));
    await act(async () => header().click());
    expect(header().getAttribute('aria-expanded')).toBe('false');
    await act(async () => root.render(createElement(StreamingMessage, {
      streaming: { ...state, finalText: '答案' },
    })));
    expect(header().getAttribute('aria-expanded')).toBe('false');
  });

  it('keeps earlier reasoning open through tools and subsequent reasoning', async () => {
    await act(async () => root.render(createElement(StreamingMessage, { streaming: {
      ...state,
      thinkingChunks: [...state.thinkingChunks, { id: 'thinking-1', content: '核对工具结果' }],
      toolCalls: [{ id: 'tool-0', name: 'web_search', arguments: {} }],
      actionOrder: [...state.actionOrder, { type: 'tool', id: 'tool-0' }, { type: 'thinking', id: 'thinking-1' }],
    } })));
    const headers = container.querySelectorAll('.ant-collapse-header');
    expect(headers).toHaveLength(2);
    expect([...headers].map(item => item.getAttribute('aria-expanded'))).toEqual(['true', 'true']);
  });

  it('preserves explicit reopening when the turn completes', async () => {
    await act(async () => root.render(createElement(StreamingMessage, { streaming: state })));
    await act(async () => header().click());
    await act(async () => header().click());
    await act(async () => root.render(createElement(StreamingMessage, {
      streaming: { ...state, isStreaming: false },
    })));
    expect(header().getAttribute('aria-expanded')).toBe('true');
  });

  it('keeps inline reasoning open while answer text streams', async () => {
    const streaming = { ...state, thinkingChunks: [], actionOrder: [], finalText: `<think>${reasoning}</think>答案` };
    await act(async () => root.render(createElement(StreamingMessage, { streaming })));
    expect(header().getAttribute('aria-expanded')).toBe('true');
    await act(async () => root.render(createElement(StreamingMessage, {
      streaming: { ...streaming, isStreaming: false },
    })));
    expect(header().getAttribute('aria-expanded')).toBe('false');
  });

  it.each([true, false, undefined])('retains manual choice %s across the saved-message handoff without affecting the next turn', async (choice) => {
    const user = { id: 'user-1', role: 'user' as const, content: '问题', created_at: '' };
    const answer = { id: 'answer-1', role: 'assistant' as const, content: '答案', reasoning: state.thinkingChunks, created_at: '' };
    await act(async () => root.render(createElement(MessageList, {
      conversationId: 'session-1', messages: [user], streaming: state,
    })));
    if (choice !== undefined) await act(async () => header().click());
    if (choice === true) await act(async () => header().click());
    await act(async () => root.render(createElement(MessageList, {
      conversationId: 'session-1', messages: [user, answer],
    })));
    expect(header().getAttribute('aria-expanded')).toBe(String(choice ?? false));
    await act(async () => root.render(createElement(MessageList, {
      conversationId: 'session-1', messages: [user, answer, { ...user, id: 'user-2' }], streaming: state,
    })));
    const headers = container.querySelectorAll('.ant-collapse-header');
    expect(headers[1].getAttribute('aria-expanded')).toBe('true');
    await act(async () => root.render(createElement(MessageList, {
      conversationId: 'session-2', messages: [{ ...user, id: 'user-1' }], streaming: state,
    })));
    expect(header().getAttribute('aria-expanded')).toBe('true');
  });

  it('defaults persisted reasoning to collapsed and allows opening it', async () => {
    await act(async () => root.render(createElement(ChatMessage, {
      message: {
        id: 'answer', role: 'assistant', content: '答案',
        reasoning: state.thinkingChunks, created_at: '2026-09-12T00:00:00Z',
      },
    })));
    expect(header().getAttribute('aria-expanded')).toBe('false');
    await act(async () => header().click());
    expect(header().getAttribute('aria-expanded')).toBe('true');
    expect(container.textContent).toContain(reasoning);
  });

  it('collapses when streaming ends without answer text', async () => {
    await act(async () => root.render(createElement(StreamingMessage, { streaming: state })));
    expect(header().getAttribute('aria-expanded')).toBe('true');
    await act(async () => root.render(createElement(StreamingMessage, {
      streaming: { ...state, isStreaming: false },
    })));
    expect(header().getAttribute('aria-expanded')).toBe('false');
  });

  it('defaults saved inline think blocks to collapsed', async () => {
    await act(async () => root.render(createElement(ChatMessage, {
      message: {
        id: 'inline-answer', role: 'assistant', content: `<think>${reasoning}</think>答案`,
        created_at: '2026-09-12T00:00:00Z',
      },
    })));
    expect(header().getAttribute('aria-expanded')).toBe('false');
  });
});

describe('saved reasoning and tool order', () => {
  const toolCalls = [
    { id: 'a', name: 'web_search', arguments: { query: '第一批 A' }, result: { status: 'ok', preview: 'A' } },
    { id: 'b', name: 'web_search', arguments: { query: '第一批 B' }, result: { status: 'ok', preview: 'B' } },
    { id: 'c', name: 'web_fetch', arguments: { url: 'https://example.com' }, result: { status: 'ok', preview: 'C' } },
  ];
  const chunks = [
    { id: 'thinking-0', content: '先搜索', tool_call_index: 0 },
    { id: 'thinking-1', content: '再读取', tool_call_index: 2 },
    { id: 'thinking-2', content: '最后总结', tool_call_index: 3 },
  ];
  const order = () => [...container.querySelectorAll('.chat-reasoning, .tool-call-card')]
    .map(el => el.classList.contains('chat-reasoning') ? 'thinking' : 'tool');

  it('keeps interleaved order across live, completed and reloaded views', async () => {
    const streaming: StreamingState = {
      ...state, thinkingChunks: chunks, toolCalls,
      actionOrder: [
        { type: 'thinking', id: 'thinking-0' }, { type: 'tool', id: 'a' }, { type: 'tool', id: 'b' },
        { type: 'thinking', id: 'thinking-1' }, { type: 'tool', id: 'c' }, { type: 'thinking', id: 'thinking-2' },
      ],
    };
    await act(async () => root.render(createElement(StreamingMessage, { streaming })));
    const liveOrder = order();
    expect(liveOrder).toEqual(['thinking', 'tool', 'tool', 'thinking', 'tool', 'thinking']);
    const saved = {
      id: 'saved', role: 'assistant' as const, content: '完成', created_at: '2026-10-01T00:00:00Z',
      reasoning: chunks, tool_calls: toolCalls,
    };
    await act(async () => root.render(createElement(ChatMessage, { message: saved })));
    expect(order()).toEqual(liveOrder);
    await act(async () => root.render(null));
    await act(async () => root.render(createElement(ChatMessage, { message: JSON.parse(JSON.stringify(saved)) })));
    expect(order()).toEqual(liveOrder);
    const headers = container.querySelectorAll('.chat-reasoning [aria-expanded]');
    await act(async () => (headers[1] as HTMLElement).click());
    expect(container.textContent).toContain('再读取');
  });

  it('keeps legacy reasoning and tool details when no ordering evidence exists', async () => {
    await act(async () => root.render(createElement(ChatMessage, { message: {
      id: 'legacy', role: 'assistant', content: '', created_at: '2026-10-01T00:00:00Z',
      reasoning: chunks.map(({ id, content }) => ({ id, content })), tool_calls: toolCalls,
    } })));
    expect(order()).toEqual(['thinking', 'thinking', 'thinking', 'tool', 'tool', 'tool']);
  });
});
