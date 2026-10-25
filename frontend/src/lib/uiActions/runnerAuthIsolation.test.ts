import { afterEach, expect, it, vi } from 'vitest';
import { invalidateAuthSession } from '@/lib/authSession';
import { uiActionStore, type UiActionPayload } from '@/stores/uiActionStore';
import { cursorController } from '@/components/ui-agent/cursorController';
import { frontendActionRegistry } from './registry';
import { startUiActionRunner } from './runner';
import { uiActionsApi } from '@/lib/api';

vi.mock('@/lib/api', () => ({ uiActionsApi: { respond: vi.fn().mockResolvedValue({}), cancelAllBeacon: vi.fn() } }));
vi.mock('@/stores/petStore', () => ({ usePetStore: { getState: () => ({ reportEvent: vi.fn() }) } }));
vi.mock('@/components/ui-agent/cursorController', () => ({ cursorController: { moveTo: vi.fn(), spotlight: vi.fn(), clear: vi.fn(), setVisible: vi.fn(), ripple: vi.fn() } }));
vi.mock('./registry', () => ({ frontendActionRegistry: { get: vi.fn() }, buildPageContext: vi.fn() }));
vi.mock('./snapshot', () => ({ snapshotEngine: { mutationTick: 0, captureDelta: vi.fn(), dangerousRefs: [] }, dispatchRealClick: vi.fn(), captureScreenshot: vi.fn() }));

let stop: (() => void) | undefined;
afterEach(() => { stop?.(); invalidateAuthSession(); vi.useRealTimers(); document.body.innerHTML = ''; });

it('does not finish a queued click or overwrite the new active action after logout', async () => {
  vi.useFakeTimers();
  Object.defineProperty(navigator, 'locks', { configurable: true, value: undefined });
  let release!: () => void;
  vi.mocked(cursorController.moveTo).mockReturnValue(new Promise<void>((resolve) => { release = resolve; }));
  const handler = vi.fn().mockResolvedValue({});
  vi.mocked(frontendActionRegistry.get).mockReturnValue({ name: 'test', description: '', risk: 'write', anchorSelector: '#target', handler });
  document.body.innerHTML = '<button id="target">Save</button>';
  document.querySelector('#target')!.scrollIntoView = vi.fn();
  const action: UiActionPayload = { action_id: 'A', tool_call_id: 'tc', session_id: 'A', action: 'ui_click', args: { action_ref: 'test' }, risk: 'write', confirmed: false, created_at: '' };
  stop = startUiActionRunner();
  uiActionStore.getState().enqueue(action);
  await vi.advanceTimersByTimeAsync(120);
  expect(cursorController.moveTo).toHaveBeenCalled();
  invalidateAuthSession();
  const newAction = { ...action, action_id: 'B', session_id: 'B' };
  uiActionStore.setState({ active: newAction });
  release();
  await vi.advanceTimersByTimeAsync(1000);
  expect(handler).not.toHaveBeenCalled();
  expect(uiActionsApi.respond).not.toHaveBeenCalled();
  expect(uiActionStore.getState()).toMatchObject({ active: newAction, resolved: {} });
});
