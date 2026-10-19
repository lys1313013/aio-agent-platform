import { act, createElement, useRef } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useMobileChatViewport } from './useMobileChatViewport';

let root: Root;
let container: HTMLDivElement;
let media: EventTarget & { matches: boolean };
let viewport: EventTarget & { height: number; offsetTop: number; scale: number };

function ChatViewport() {
  const ref = useRef<HTMLDivElement>(null);
  const mobile = useMobileChatViewport(ref);
  return createElement('div', { ref, 'data-mobile': mobile });
}

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  media = Object.assign(new EventTarget(), { matches: true });
  viewport = Object.assign(new EventTarget(), { height: 844, offsetTop: 0, scale: 1 });
  vi.spyOn(window, 'matchMedia').mockReturnValue(media as MediaQueryList);
  vi.stubGlobal('visualViewport', viewport);
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it('tracks the visible height and offset when a keyboard opens and closes', async () => {
  await act(async () => root.render(createElement(ChatViewport)));
  const element = container.firstElementChild as HTMLElement;
  expect(element.style.getPropertyValue('--chat-viewport-height')).toBe('844px');
  await act(async () => {
    viewport.height = 420;
    viewport.offsetTop = 24;
    viewport.dispatchEvent(new Event('resize'));
  });
  expect(element.style.getPropertyValue('--chat-viewport-height')).toBe('420px');
  expect(element.style.getPropertyValue('--chat-viewport-top')).toBe('24px');
  await act(async () => {
    viewport.height = 844;
    viewport.offsetTop = 0;
    viewport.dispatchEvent(new Event('resize'));
  });
  expect(element.style.getPropertyValue('--chat-viewport-height')).toBe('844px');
  expect(element.style.getPropertyValue('--chat-viewport-top')).toBe('0px');
});

it('releases mobile sizing on desktop and removes viewport listeners on unmount', async () => {
  const removeListener = vi.spyOn(viewport, 'removeEventListener');
  await act(async () => root.render(createElement(ChatViewport)));
  const element = container.firstElementChild as HTMLElement;
  await act(async () => {
    media.matches = false;
    media.dispatchEvent(new Event('change'));
  });
  expect(element.dataset.mobile).toBe('false');
  expect(element.style.getPropertyValue('--chat-viewport-height')).toBe('');
  await act(async () => root.render(null));
  expect(removeListener).toHaveBeenCalledWith('resize', expect.any(Function));
  expect(removeListener).toHaveBeenCalledWith('scroll', expect.any(Function));
});
