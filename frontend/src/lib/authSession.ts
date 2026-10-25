/** Identity lifetime shared by requests and user-owned stores (no store imports). */
let generation = 0;
let controller = new AbortController();
const listeners = new Set<() => void>();

export const getAuthGeneration = () => generation;
export const isCurrentAuthGeneration = (value: number) => value === generation;
export const getAuthSignal = () => controller.signal;

export function assertAuthGeneration(value: number): void {
  if (!isCurrentAuthGeneration(value)) {
    throw new DOMException('登录身份已变更', 'AbortError');
  }
}

export function onAuthSessionChange(listener: () => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

export function invalidateAuthSession(): void {
  generation += 1;
  const previous = controller;
  controller = new AbortController();
  previous.abort();
  for (const listener of listeners) listener();
}

/** Ignore buffered SSE/poll callbacks from an earlier identity. */
export function scopeAuthCallback<T extends unknown[]>(callback: (...args: T) => void) {
  const current = generation;
  return (...args: T) => {
    if (isCurrentAuthGeneration(current)) callback(...args);
  };
}
