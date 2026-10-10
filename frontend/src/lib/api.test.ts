import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError, request } from './api';

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

it('bounds slow requests and releases the timeout after failure', async () => {
  vi.useFakeTimers();
  vi.stubGlobal(
    'fetch',
    vi.fn(
      (_url, init: RequestInit) =>
        new Promise((_resolve, reject) => {
          init.signal!.addEventListener('abort', () => reject(init.signal!.reason));
        }),
    ),
  );
  const failure = expect(api.health()).rejects.toMatchObject({ name: 'TimeoutError' });
  await vi.advanceTimersByTimeAsync(15000);
  await failure;
  expect(vi.getTimerCount()).toBe(0);
});

it('rejects nonadvancing pagination instead of issuing an infinite request loop', async () => {
  const fetch = vi
    .fn()
    .mockResolvedValue(new Response(JSON.stringify({ items: [], next_offset: 0 })));
  vi.stubGlobal('fetch', fetch);
  await expect(api.stations(undefined, false)).rejects.toMatchObject({
    code: 'invalid_pagination',
  });
  expect(fetch).toHaveBeenCalledTimes(1);
});

it('limits sparse pagination to ten requests and discloses truncation', async () => {
  let offset = 0;
  const fetch = vi.fn(
    async () => new Response(JSON.stringify({ items: [], next_offset: ++offset })),
  );
  vi.stubGlobal('fetch', fetch);
  await expect(api.stations(undefined, false)).resolves.toEqual({ items: [], truncated: true });
  expect(fetch).toHaveBeenCalledTimes(10);
});

it('preserves safe validation messages while discarding malformed error details', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          error: {
            code: 'validation_error',
            message: 'Invalid input',
            request_id: 'qa-error',
            details: [
              { loc: ['body', 'latitude'], msg: 'Invalid latitude' },
              { secret: 'not a field error' },
              null,
            ],
          },
        }),
        { status: 422 },
      ),
    ),
  );
  await expect(api.health()).rejects.toMatchObject({
    requestId: 'qa-error',
    details: [{ loc: ['body', 'latitude'], msg: 'Invalid latitude' }],
  });
});

describe('API client', () => {
  it('parses a healthy response', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            status: 'ok',
            service: 'API',
            version: '0.1.0',
            timestamp: '2026-10-09T00:00:00Z',
          }),
          { status: 200 },
        ),
      ),
    );

    await expect(api.health()).resolves.toMatchObject({ status: 'ok', version: '0.1.0' });
  });

  it('preserves structured error context', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            error: {
              code: 'unavailable',
              message: 'Database unavailable',
              request_id: 'request-1',
            },
          }),
          { status: 503 },
        ),
      ),
    );

    const error = await api.health().catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ code: 'unavailable', status: 503, requestId: 'request-1' });
  });
});

it('sends cookie credentials and CSRF headers and handles a 204 logout', async () => {
  const fetch = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
  vi.stubGlobal('fetch', fetch);
  await expect(api.logout('memory-only-token')).resolves.toBeUndefined();
  expect(fetch).toHaveBeenCalledWith(
    '/api/v1/auth/logout',
    expect.objectContaining({
      credentials: 'include',
      headers: expect.objectContaining({ 'X-CSRF-Token': 'memory-only-token' }),
    }),
  );
});

it('handles malformed proxy errors without losing the HTTP status', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{}', { status: 502 })));
  await expect(api.health()).rejects.toMatchObject({ status: 502, code: 'request_failed' });
});

it('follows station pagination within a bounded request budget', async () => {
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ items: [{ external_id: 'one' }], next_offset: 100 }), {
        status: 200,
      }),
    )
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ items: [{ external_id: 'two' }], next_offset: null }), {
        status: 200,
      }),
    );
  vi.stubGlobal('fetch', fetch);
  await expect(api.stations('0,0,1,1', false)).resolves.toEqual({
    items: [{ external_id: 'one' }, { external_id: 'two' }],
    truncated: false,
  });
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(fetch.mock.calls[1][0]).toContain('offset=100');
});

it('propagates cancellation to the fetch request', async () => {
  let signal: AbortSignal | undefined;
  vi.stubGlobal(
    'fetch',
    vi.fn((_url, init: RequestInit) => {
      signal = init.signal as AbortSignal;
      return new Promise((_resolve, reject) =>
        signal!.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError'))),
      );
    }),
  );
  const controller = new AbortController();
  const pending = request('/health/live', { signal: controller.signal });
  controller.abort();
  await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
  expect(signal?.aborted).toBe(true);
});
