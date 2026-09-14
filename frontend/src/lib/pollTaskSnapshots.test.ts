import { afterEach, expect, it, vi } from 'vitest';
import { pollTaskSnapshots } from './pollTaskSnapshots';
afterEach(() => vi.useRealTimers());

it('uses non-overlapping short requests and stops polling after cleanup', async () => {
  vi.useFakeTimers();
  const receive = vi.fn();
  const load = vi.fn().mockResolvedValue([{ session_id: 'one' }]);
  const stop = pollTaskSnapshots(load, receive);
  await vi.advanceTimersByTimeAsync(0);
  expect(receive).toHaveBeenCalledWith({ type: 'pet_task_snapshot', tasks: [{ session_id: 'one' }] });
  await vi.advanceTimersByTimeAsync(5000);
  expect(load).toHaveBeenCalledTimes(2);
  stop();
  await vi.advanceTimersByTimeAsync(15000);
  expect(load).toHaveBeenCalledTimes(2);
});

it('aborts a stalled request before retrying and suppresses results after cleanup', async () => {
  vi.useFakeTimers();
  const receive = vi.fn();
  let signal: AbortSignal;
  const load = vi.fn((s: AbortSignal) => {
    signal = s;
    return new Promise<unknown[]>((_, reject) => s.addEventListener('abort', () => reject(new Error('aborted'))));
  });
  const stop = pollTaskSnapshots(load, receive);
  await vi.advanceTimersByTimeAsync(9999);
  expect(load).toHaveBeenCalledTimes(1);
  expect(signal!.aborted).toBe(false);
  await vi.advanceTimersByTimeAsync(1);
  expect(signal!.aborted).toBe(true);
  await vi.advanceTimersByTimeAsync(5000);
  expect(load).toHaveBeenCalledTimes(2);
  stop();
  expect(signal!.aborted).toBe(true);
  await vi.advanceTimersByTimeAsync(15000);
  expect(load).toHaveBeenCalledTimes(2);
  expect(receive).not.toHaveBeenCalled();
});
