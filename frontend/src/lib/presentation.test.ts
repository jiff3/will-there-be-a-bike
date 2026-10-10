import { describe, expect, it } from 'vitest';
import { freshness, historyRange } from './presentation';
import { station } from '../test/fixtures';

describe('observation presentation', () => {
  it('never replaces missing or stale inventory with live values', () => {
    expect(freshness({ ...station(), bikes: null })).toBe('missing');
    expect(
      freshness({ ...station(), reported_at: new Date(Date.now() - 301000).toISOString() }),
    ).toBe('stale');
    expect(
      freshness({ ...station(), fetched_at: new Date(Date.now() + 60000).toISOString() }),
    ).toBe('stale');
    expect(freshness(station())).toBe('fresh');
  });
  it.each([
    ['2025-03-09', 23],
    ['2025-11-02', 25],
    ['2025-02-28', 24],
  ])('uses actual New York day boundaries on %s', (date, hours) => {
    const [start, end] = historyRange(date);
    expect((Date.parse(end) - Date.parse(start)) / 3600000).toBe(hours);
    expect(Date.parse(start) % 900000).toBe(0);
    expect(Date.parse(end) % 900000).toBe(0);
  });
});
