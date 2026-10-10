import type { Station } from './types';

export const NYC = 'America/New_York';
export const time = (value: string) =>
  new Intl.DateTimeFormat('en-US', {
    timeZone: NYC,
    hour: 'numeric',
    minute: '2-digit',
    timeZoneName: 'short',
  }).format(new Date(value));
export const day = (value: string | number) =>
  new Intl.DateTimeFormat('en-CA', {
    timeZone: NYC,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date(value));
export function freshness(
  station: Pick<
    Station,
    'bikes' | 'docks' | 'reported_at' | 'feed_updated_at' | 'fetched_at' | 'stale'
  >,
  now = Date.now(),
): 'missing' | 'stale' | 'fresh' {
  if (
    station.bikes === null ||
    station.docks === null ||
    !station.reported_at ||
    !station.feed_updated_at ||
    !station.fetched_at
  )
    return 'missing';
  if (
    station.stale ||
    [station.reported_at, station.feed_updated_at, station.fetched_at].some(
      (value) => now - Date.parse(value) > 300000 || Date.parse(value) > now,
    )
  )
    return 'stale';
  return 'fresh';
}
export function relativeTime(value: string | null, now = Date.now()) {
  if (!value) return 'No observation';
  const seconds = Math.max(0, Math.floor((now - Date.parse(value)) / 1000));
  return seconds < 60
    ? `${seconds}s ago`
    : seconds < 3600
      ? `${Math.floor(seconds / 60)}m ago`
      : `${Math.floor(seconds / 3600)}h ago`;
}
export function message(error: unknown) {
  if (!navigator.onLine) return 'You’re offline. Reconnect to refresh live data.';
  return error instanceof Error ? error.message : 'Something went wrong. Please try again.';
}
export function humanize(value: string) {
  return value.replaceAll('_', ' ');
}

/** Calendar conversion only. Both boundaries are NY midnights, including DST days. */
export function historyRange(date: string): [string, string] {
  function midnight(value: string) {
    const utc = Date.parse(`${value}T00:00:00Z`);
    let guess = utc;
    for (let i = 0; i < 3; i++) {
      const parts = new Intl.DateTimeFormat('en-CA', {
        timeZone: NYC,
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        hourCycle: 'h23',
      }).formatToParts(new Date(guess));
      const get = (type: string) => parts.find((part) => part.type === type)!.value;
      const represented = Date.parse(
        `${get('year')}-${get('month')}-${get('day')}T${get('hour')}:${get('minute')}:00Z`,
      );
      guess += utc - represented;
    }
    return new Date(guess).toISOString();
  }
  const next = new Date(Date.parse(`${date}T12:00:00Z`) + 86400000).toISOString().slice(0, 10);
  return [midnight(date), midnight(next)];
}
