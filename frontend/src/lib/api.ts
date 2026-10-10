import type {
  DemandHistory,
  Forecast,
  ModelCard,
  Preferences,
  Session,
  Station,
  StationPage,
  TripRecommendations,
  TripRequest,
  User,
  AlertInput,
  AvailabilityAlert,
  CommuteInput,
  SavedCommute,
  Inbox,
  ReplayCatalog,
  ReplayDay,
  ReplayComparison,
} from './types';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api/v1';

export interface HealthResponse {
  status: 'ok' | 'degraded';
  service: string;
  version: string;
  timestamp: string;
  checks?: Record<string, 'ok' | 'unavailable'>;
}

export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    request_id: string;
    details?: unknown;
  };
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
    readonly requestId?: string,
    readonly details?: { loc: (string | number)[]; msg: string }[],
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const cancel = () => controller.abort(init?.signal?.reason);
  if (init?.signal?.aborted) cancel();
  init?.signal?.addEventListener('abort', cancel, { once: true });
  const timeout = setTimeout(
    () => controller.abort(new DOMException('Request timed out', 'TimeoutError')),
    15000,
  );
  try {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      signal: controller.signal,
      credentials: 'include',
      headers: { Accept: 'application/json', ...init?.headers },
    });
    if (!response.ok) {
      const body = (await response.json().catch(() => null)) as ApiErrorBody | null;
      throw new ApiError(
        body?.error?.message ?? `Request failed with status ${response.status}`,
        response.status,
        body?.error?.code ?? 'request_failed',
        body?.error?.request_id,
        Array.isArray(body?.error?.details)
          ? body.error.details.filter((value): value is { loc: (string | number)[]; msg: string } =>
              Boolean(value && typeof value.msg === 'string' && Array.isArray(value.loc)),
            )
          : undefined,
      );
    }
    return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
  } finally {
    clearTimeout(timeout);
    init?.signal?.removeEventListener('abort', cancel);
  }
}

const json = (method: string, body?: unknown, csrf?: string): RequestInit => ({
  method,
  body: body === undefined ? undefined : JSON.stringify(body),
  headers: { 'Content-Type': 'application/json', ...(csrf ? { 'X-CSRF-Token': csrf } : {}) },
});
const stationPath = (id: string) => `/stations/${encodeURIComponent(id)}`;
export const api = {
  health: (signal?: AbortSignal) => request<HealthResponse>('/health/ready', { signal }),
  stations: async (bbox: string | undefined, includeDemo: boolean, signal?: AbortSignal) => {
    const items: Station[] = [];
    let offset: number | null = 0;
    let pages = 0;
    // Bounded viewport workload, with a visible truncation disclosure on expansion.
    while (offset !== null && items.length < 1000 && pages < 10) {
      const params = new URLSearchParams({
        limit: '100',
        offset: String(offset),
        include_demo: String(includeDemo),
      });
      if (bbox) params.set('bbox', bbox);
      const page: StationPage = await request(`/stations?${params}`, { signal });
      if (
        page.next_offset !== null &&
        (!Number.isInteger(page.next_offset) || page.next_offset <= offset)
      )
        throw new ApiError('Station pagination did not advance', 502, 'invalid_pagination');
      items.push(...page.items);
      offset = page.next_offset;
      pages += 1;
    }
    return { items, truncated: offset !== null };
  },
  station: (id: string, signal?: AbortSignal) => request<Station>(stationPath(id), { signal }),
  searchStations: async (search: string, signal?: AbortSignal) =>
    (
      await request<StationPage>(`/stations?${new URLSearchParams({ search, limit: '6' })}`, {
        signal,
      })
    ).items,
  forecast: (id: string, signal?: AbortSignal) =>
    request<Forecast>(`${stationPath(id)}/predictions`, { signal }),
  model: (signal?: AbortSignal) => request<ModelCard>('/forecasts/model', { signal }),
  demand: (id: string, start: string, end: string, signal?: AbortSignal) =>
    request<DemandHistory>(`${stationPath(id)}/demand?${new URLSearchParams({ start, end })}`, {
      signal,
    }),
  recommend: (body: TripRequest, signal?: AbortSignal) =>
    request<TripRecommendations>('/recommendations/trip', { ...json('POST', body), signal }),
  me: (signal?: AbortSignal) => request<User>('/auth/me', { signal }),
  login: (email: string, password: string) =>
    request<Session>('/auth/login', json('POST', { email, password })),
  register: (email: string, password: string) =>
    request<Session>('/auth/register', json('POST', { email, password })),
  csrf: () => request<{ csrf_token: string }>('/auth/csrf', json('POST')),
  logout: (csrf: string) => request<void>('/auth/logout', json('POST', undefined, csrf)),
  preferences: (signal?: AbortSignal) => request<Preferences>('/auth/preferences', { signal }),
  savePreferences: (value: Preferences, csrf: string) =>
    request<Preferences>('/auth/preferences', json('PUT', value, csrf)),
  commutes: (signal?: AbortSignal) => request<{ items: SavedCommute[] }>('/commutes', { signal }),
  commute: (id: string, signal?: AbortSignal) =>
    request<SavedCommute>(`/commutes/${encodeURIComponent(id)}`, { signal }),
  saveCommute: (value: CommuteInput, csrf: string, id?: string) =>
    request<SavedCommute>(
      id ? `/commutes/${encodeURIComponent(id)}` : '/commutes',
      json(id ? 'PUT' : 'POST', value, csrf),
    ),
  deleteCommute: (id: string, csrf: string) =>
    request<void>(`/commutes/${encodeURIComponent(id)}`, json('DELETE', undefined, csrf)),
  commuteRecommendations: (id: string, signal?: AbortSignal) =>
    request<TripRecommendations>(`/commutes/${encodeURIComponent(id)}/recommendations`, { signal }),
  alerts: (commute: string, signal?: AbortSignal) =>
    request<{ items: AvailabilityAlert[] }>(`/commutes/${encodeURIComponent(commute)}/alerts`, {
      signal,
    }),
  saveAlert: (commute: string, value: AlertInput, csrf: string, id?: string) =>
    request<AvailabilityAlert>(
      `/commutes/${encodeURIComponent(commute)}/alerts${id ? `/${encodeURIComponent(id)}` : ''}`,
      json(id ? 'PUT' : 'POST', value, csrf),
    ),
  deleteAlert: (commute: string, id: string, csrf: string) =>
    request<void>(
      `/commutes/${encodeURIComponent(commute)}/alerts/${encodeURIComponent(id)}`,
      json('DELETE', undefined, csrf),
    ),
  notifications: (offset = 0, signal?: AbortSignal) =>
    request<Inbox>(`/notifications?limit=20&offset=${offset}`, { signal }),
  readNotification: (id: string, read: boolean, csrf: string) =>
    request<void>(`/notifications/${encodeURIComponent(id)}`, json('PATCH', { read }, csrf)),
  replayCatalog: (signal?: AbortSignal) => request<ReplayCatalog>('/replay/catalog', { signal }),
  replayDay: (day: string, signal?: AbortSignal) =>
    request<ReplayDay>(`/replay/day?${new URLSearchParams({ day })}`, { signal }),
  replayComparisons: (station: string, day: string, signal?: AbortSignal) =>
    request<{ items: ReplayComparison[]; semantics: string }>(
      `/replay/stations/${encodeURIComponent(station)}/comparisons?${new URLSearchParams({ day })}`,
      { signal },
    ),
};
