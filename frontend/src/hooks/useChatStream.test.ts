import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { chatApi, type ChatRunInfo } from '@/lib/api';
import { useChatStore } from '@/stores/chatStore';
import { useChatStream, type ChatStreamEvent } from './useChatStream';

vi.mock('@/lib/api', () => ({
  chatApi: { latestRun: vi.fn(), watchRun: vi.fn(), stopRun: vi.fn(), resumeRun: vi.fn() },
  sessionsApi: {}, workspacesApi: {},
}));

const info: ChatRunInfo = {
  id: 'run-1', session_id: 's1', assistant_message_id: 'assistant-1',
  status: 'running', last_sequence: 2, stop_requested: false,
  can_resume: false, uncertain_tools: [],
};
let result: ReturnType<typeof useChatStream>;
let root: Root;
let consume: (event: ChatStreamEvent) => void;
let controller: AbortController;
const onEvent = vi.fn();

function Harness() {
  result = useChatStream({ onEvent });
  return null;
}

async function settle() {
  await act(async () => { await Promise.resolve(); });
}

beforeEach(async () => {
  vi.clearAllMocks();
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
  useChatStore.setState({ activeSessionId: 's1', messages: {}, loadSessionMessages: vi.fn().mockResolvedValue(undefined) });
  vi.mocked(chatApi.latestRun).mockResolvedValue({ ...info });
  vi.mocked(chatApi.watchRun).mockImplementation((_id, callback) => {
    consume = callback;
    controller = new AbortController();
    return controller;
  });
  root = createRoot(document.createElement('div'));
  await act(async () => { root.render(createElement(Harness)); });
  await settle();
});

afterEach(async () => {
  await act(async () => root.unmount());
});

describe('background chat subscriptions', () => {
  it('reconnects on mount and deduplicates events without replaying browser actions', async () => {
    expect(chatApi.watchRun).toHaveBeenCalledWith('run-1', expect.any(Function));
    await act(async () => {
      consume({ type: 'run', ...info });
      consume({ type: 'text_delta', sequence: 1, content: 'hello', replay: true });
      consume({ type: 'text_delta', sequence: 1, content: 'hello', replay: true });
      consume({ type: 'ui_action_required', sequence: 2, action_id: 'a', replay: true });
    });
    expect(result.streaming.finalText).toBe('hello');
    expect(onEvent.mock.calls.some(([event]) => event.type === 'ui_action_required')).toBe(false);
  });

  it('detaches on session switch without stopping the task and ignores stale callbacks', async () => {
    const old = consume;
    const oldController = controller;
    await act(async () => useChatStore.setState({ activeSessionId: null }));
    expect(oldController.signal.aborted).toBe(true);
    expect(chatApi.stopRun).not.toHaveBeenCalled();
    await act(async () => old({ type: 'text_delta', content: 'late' }));
    expect(result.streaming.finalText).toBe('');
  });

  it('explicit stop waits for the server and loads persisted history', async () => {
    vi.mocked(chatApi.stopRun).mockResolvedValue({ ...info, status: 'stopped', can_resume: true });
    await act(async () => consume({ type: 'run', ...info }));
    await act(async () => { expect(await result.interrupt()).toBe(true); });
    expect(chatApi.stopRun).toHaveBeenCalledWith('run-1');
    expect(controller.signal.aborted).toBe(true);
    expect(result.run?.status).toBe('stopped');
    expect(useChatStore.getState().loadSessionMessages).toHaveBeenCalledWith('s1');
  });

  it('rebuilds replay state and upserts an already persisted assistant message', async () => {
    useChatStore.setState({ messages: { s1: [{ id: 'assistant-1', role: 'assistant', content: 'partial', created_at: '' }] } });
    await act(async () => {
      consume({ type: 'run', ...info });
      consume({ type: 'text_delta', sequence: 1, content: 'answer' });
      consume({ type: 'done', sequence: 2, message_id: 'assistant-1', content: 'answer' });
    });
    expect(useChatStore.getState().messages.s1).toHaveLength(1);
    expect(useChatStore.getState().messages.s1[0].content).toBe('answer');
    expect(result.streaming.isStreaming).toBe(false);
  });

  it('keeps continuation disabled while external tool outcome is unknown', async () => {
    await act(async () => consume({ type: 'run_status', ...info, status: 'interrupted', uncertain_tools: ['submit'], can_resume: false }));
    await act(async () => result.resume());
    expect(chatApi.resumeRun).not.toHaveBeenCalled();
    expect(result.run?.uncertain_tools).toEqual(['submit']);
  });
});
