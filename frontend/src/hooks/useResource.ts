import { useCallback, useEffect, useState } from 'react';

/** Serial polling, cancellation on dependency change, no background/offline traffic. */
export function useResource<T>(load: (signal: AbortSignal) => Promise<T>, interval = 0) {
  const [value, setValue] = useState<{
    data?: T;
    error?: Error;
    loading: boolean;
    updatedAt?: number;
  }>({ loading: true });
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    let controller: AbortController | undefined;
    let running = false;
    setValue({ loading: true });
    async function refresh() {
      if (running || !active) return;
      clearTimeout(timer);
      if (document.hidden || !navigator.onLine) {
        setValue((previous) => ({ ...previous, loading: false }));
        return;
      }
      running = true;
      controller = new AbortController();
      try {
        const data = await load(controller.signal);
        if (active) setValue({ data, loading: false, updatedAt: Date.now() });
      } catch (error) {
        if (active && !controller.signal.aborted)
          setValue((previous) => ({
            ...previous,
            loading: false,
            error: error instanceof Error ? error : new Error('Request unavailable'),
          }));
      } finally {
        running = false;
        if (active && interval) timer = setTimeout(() => void refresh(), interval);
      }
    }
    void refresh();
    const resume = () => {
      if (!document.hidden) void refresh();
    };
    document.addEventListener('visibilitychange', resume);
    window.addEventListener('online', resume);
    return () => {
      active = false;
      clearTimeout(timer);
      controller?.abort();
      document.removeEventListener('visibilitychange', resume);
      window.removeEventListener('online', resume);
    };
  }, [load, interval, revision]);
  const retry = useCallback(() => setRevision((previous) => previous + 1), []);
  return { ...value, retry };
}

export function useClock() {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 30000);
    return () => clearInterval(timer);
  }, []);
  return now;
}
