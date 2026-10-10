import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, vi } from 'vitest';
import { useResource } from './useResource';

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

it('cancels superseded requests and ignores responses from previous selections', async () => {
  let resolve!: (value: string) => void;
  let oldSignal!: AbortSignal;
  const old = (signal: AbortSignal) => {
    oldSignal = signal;
    return new Promise<string>((done) => {
      resolve = done;
    });
  };
  const next = vi.fn(async () => 'new selection');
  const { result, rerender, unmount } = renderHook(({ load }) => useResource(load), {
    initialProps: { load: old },
  });
  rerender({ load: next });
  await waitFor(() => expect(result.current.data).toBe('new selection'));
  expect(oldSignal.aborted).toBe(true);
  await act(async () => resolve('old selection'));
  expect(result.current.data).toBe('new selection');
  unmount();
});

it('preserves the last successful observation on a transient poll error', async () => {
  vi.useFakeTimers();
  const load = vi
    .fn<(signal: AbortSignal) => Promise<string>>()
    .mockRejectedValue(new Error('temporary'))
    .mockResolvedValueOnce('measured');
  const { result, unmount } = renderHook(() => useResource(load, 1000));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(result.current.data).toBe('measured');
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1000);
  });
  expect(result.current.error?.message).toBe('temporary');
  expect(result.current.data).toBe('measured');
  unmount();
});

it('does not fetch while offline and resumes when connectivity returns', async () => {
  const online = vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false);
  const load = vi.fn(async () => 'fresh observation');
  const { result, unmount } = renderHook(() => useResource(load));
  expect(load).not.toHaveBeenCalled();
  expect(result.current.loading).toBe(false);
  online.mockReturnValue(true);
  await act(async () => {
    window.dispatchEvent(new Event('online'));
  });
  expect(result.current.data).toBe('fresh observation');
  unmount();
});

it('never overlaps polls and cancels an in-flight request on unmount', async () => {
  vi.useFakeTimers();
  let signal!: AbortSignal;
  const load = vi.fn((value: AbortSignal) => {
    signal = value;
    return new Promise<string>(() => {});
  });
  const { unmount } = renderHook(() => useResource(load, 1000));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10000);
  });
  expect(load).toHaveBeenCalledTimes(1);
  unmount();
  expect(signal.aborted).toBe(true);
  expect(vi.getTimerCount()).toBe(0);
});

it('pauses hidden-tab traffic and resumes on visibility change', async () => {
  const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(true);
  const load = vi.fn(async () => 'visible observation');
  const { result, unmount } = renderHook(() => useResource(load));
  expect(load).not.toHaveBeenCalled();
  hidden.mockReturnValue(false);
  await act(async () => {
    document.dispatchEvent(new Event('visibilitychange'));
  });
  expect(result.current.data).toBe('visible observation');
  unmount();
});
