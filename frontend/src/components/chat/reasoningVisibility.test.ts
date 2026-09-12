import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import StreamingMessage from './StreamingMessage';
import ChatMessage from './ChatMessage';
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
  it('collapses streamed reasoning when answer text arrives', async () => {
    await act(async () => root.render(createElement(StreamingMessage, { streaming: state })));
    expect(header().getAttribute('aria-expanded')).toBe('true');
    expect(container.textContent).toContain(reasoning);
    await act(async () => root.render(createElement(StreamingMessage, {
      streaming: { ...state, finalText: '答案' },
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
