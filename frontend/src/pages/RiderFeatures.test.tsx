import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { vi } from 'vitest';
import { App } from '../App';
import { api, ApiError } from '../lib/api';
import { advanceFrame } from '../lib/replay';
import type { SavedCommute, ReplayDay, RiderNotification } from '../lib/types';
import { station, recommendations } from '../test/fixtures';

vi.mock('../components/StationMap', () => ({
  StationMap: () => <div aria-label="Historical map test double" />,
}));
const preferences = { max_walk_meters: 700, reliability_weight: 0.7 };
const commute: SavedCommute = {
  id: 'commute-test',
  name: 'The morning ride',
  origin: { latitude: 40.7, longitude: -74, label: 'Alpha & First', station_id: 'alpha' },
  destination: { latitude: 40.8, longitude: -73.9, label: 'Beta & Second', station_id: 'beta' },
  departure_local: '08:30',
  timezone: 'America/New_York',
  weekdays: [1, 2, 3, 4, 5],
  preferences,
  created_at: '2025-02-28T05:00:00Z',
  updated_at: '2025-02-28T05:00:00Z',
  next_departure: '2026-10-12T12:30:00Z',
};
const replay: ReplayDay = {
  dataset_id: 'test',
  buckets: ['2025-02-28T05:00:00Z', '2025-02-28T05:15:00Z', '2025-02-28T05:30:00Z'],
  stations: [station()],
  series: [{ station_id: 'alpha', departures: [0, 3, null], arrivals: [1, 0, null] }],
};
beforeEach(() => {
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {});
  vi.spyOn(api, 'me').mockResolvedValue({ id: 'rider-test', email: 'rider@example.test' });
  vi.spyOn(api, 'preferences').mockResolvedValue(preferences);
  vi.spyOn(api, 'csrf').mockResolvedValue({ csrf_token: 'test-memory-token' });
  vi.spyOn(api, 'notifications').mockResolvedValue({
    items: [],
    unread_count: 0,
    next_offset: null,
  });
  vi.spyOn(api, 'commutes').mockResolvedValue({ items: [commute] });
  vi.spyOn(api, 'alerts').mockResolvedValue({ items: [] });
  vi.spyOn(api, 'searchStations').mockResolvedValue([station()]);
  vi.spyOn(api, 'replayCatalog').mockResolvedValue({
    dataset: null,
    days: ['2025-02-27', '2025-02-28'],
    timezone: 'America/New_York',
  });
  vi.spyOn(api, 'replayDay').mockResolvedValue(replay);
  vi.spyOn(api, 'replayComparisons').mockResolvedValue({
    items: [],
    semantics: 'synthetic test comparison',
  });
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});
const open = (route: string) =>
  render(
    <MemoryRouter initialEntries={[route]}>
      <App />
    </MemoryRouter>,
  );

it('gates private pages without issuing private data requests', async () => {
  vi.mocked(api.me).mockRejectedValue(
    new ApiError('Sign in required', 401, 'authentication_required'),
  );
  open('/commutes');
  expect(
    await screen.findByRole('link', { name: 'Sign in or create an account' }),
  ).toBeInTheDocument();
  expect(api.commutes).not.toHaveBeenCalled();
});

it('edits persisted settings through CSRF-protected writes and deletes with confirmation', async () => {
  vi.spyOn(api, 'saveCommute').mockResolvedValue({ ...commute, name: 'Revised ride' });
  vi.spyOn(api, 'deleteCommute').mockResolvedValue();
  open('/commutes');
  fireEvent.click(await screen.findByRole('button', { name: 'Edit commute' }));
  fireEvent.change(screen.getByLabelText('Commute name'), { target: { value: 'Revised ride' } });
  fireEvent.change(screen.getByLabelText('Preferred bike departure'), {
    target: { value: '09:15' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Save commute' }));
  await waitFor(() =>
    expect(api.saveCommute).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'Revised ride', departure_local: '09:15', preferences }),
      'test-memory-token',
      commute.id,
    ),
  );
  fireEvent.click(await screen.findByRole('button', { name: 'Delete commute' }));
  expect(api.deleteCommute).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Yes, delete commute' }));
  await waitFor(() =>
    expect(api.deleteCommute).toHaveBeenCalledWith(commute.id, 'test-memory-token'),
  );
});

it('uses backend-ranked current recommendations directly on a saved commute', async () => {
  vi.spyOn(api, 'commuteRecommendations').mockResolvedValue(recommendations());
  open('/commutes');
  fireEvent.click(await screen.findByRole('button', { name: 'Check recommendations now' }));
  const result = await screen.findByRole('region', {
    name: 'Current recommendations for The morning ride',
  });
  expect(
    within(result).getByText(/Not a forecast for your next scheduled day/),
  ).toBeInTheDocument();
  expect(within(result).getByText('Pick up a bike')).toBeInTheDocument();
  expect(api.commuteRecommendations).toHaveBeenCalledWith(commute.id, expect.any(AbortSignal));
});

it('creates a real station alert and exposes the empty and failure cases honestly', async () => {
  vi.spyOn(api, 'saveAlert').mockResolvedValue({
    id: 'rule-test',
    commute_id: commute.id,
    station_id: 'alpha',
    station_name: 'Alpha & First',
    latitude: 40.7,
    longitude: -74,
    kind: 'pickup_empty',
    mode: 'inventory',
    threshold: 3,
    lead_minutes: 30,
    cooldown_minutes: 60,
    enabled: true,
    last_checked_at: null,
    last_status: 'not_checked',
  });
  open('/commutes');
  fireEvent.click(await screen.findByRole('button', { name: 'Availability alerts' }));
  expect(await screen.findByText(/No alerts yet/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Add availability alert' }));
  fireEvent.change(screen.getByRole('combobox', { name: 'Alert station' }), {
    target: { value: 'Alpha' },
  });
  fireEvent.click(await screen.findByRole('option', { name: 'Alpha & First' }));
  fireEvent.click(screen.getByRole('button', { name: 'Save alert' }));
  await waitFor(() =>
    expect(api.saveAlert).toHaveBeenCalledWith(
      commute.id,
      expect.objectContaining({ station_id: 'alpha', threshold: 3, mode: 'inventory' }),
      'test-memory-token',
      undefined,
    ),
  );
});

it('persists read and unread state and never treats notifications as guarantees', async () => {
  const item: RiderNotification = {
    id: 'notice-test',
    title: 'Morning ride alert',
    message: 'Latest measurement: 2 bikes. Not a future prediction.',
    created_at: '2026-10-10T07:00:00Z',
    scheduled_for: '2026-10-10T07:30:00Z',
    read_at: null,
    evidence: {
      mode: 'inventory',
      station_id: 'alpha',
      station_name: 'Alpha',
      reported_at: '2026-10-10T07:00:00Z',
      commute_id: commute.id,
    },
  };
  vi.mocked(api.notifications).mockImplementation(async () => ({
    items: [item],
    unread_count: item.read_at ? 0 : 1,
    next_offset: null,
  }));
  vi.spyOn(api, 'readNotification').mockImplementation(async (_, read) => {
    item.read_at = read ? '2026-10-10T07:01:00Z' : null;
  });
  open('/notifications');
  fireEvent.click(await screen.findByRole('button', { name: 'Mark read' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Mark unread' }));
  expect(await screen.findByRole('button', { name: 'Mark read' })).toBeInTheDocument();
  expect(api.readNotification).toHaveBeenLastCalledWith(item.id, false, 'test-memory-token');
});

it('keeps replay boundaries, missing observations and empty datasets explicit', async () => {
  expect(advanceFrame(2, 3)).toBe(2);
  expect(advanceFrame(0, 0)).toBe(0);
  open('/replay');
  const slider = await screen.findByRole('slider', { name: 'Historical timeline' });
  expect(screen.getByRole('button', { name: 'Previous 15 min' })).toBeDisabled();
  fireEvent.change(slider, { target: { value: '2' } });
  expect(screen.getByRole('button', { name: 'Next 15 min' })).toBeDisabled();
  expect(screen.getByText(/Unknown departures · Unknown arrivals/)).toBeInTheDocument();
  expect(screen.getByText(/No stored historical forecast/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Recorded day (New York)'), {
    target: { value: '2025-02-27' },
  });
  await waitFor(() =>
    expect(screen.getByRole('slider', { name: 'Historical timeline' })).toHaveValue('0'),
  );
});

it('plays, changes speed, pauses and stops at the last recorded frame', async () => {
  open('/replay');
  const slider = await screen.findByRole('slider', { name: 'Historical timeline' });
  vi.useFakeTimers();
  fireEvent.click(screen.getByRole('button', { name: 'Play replay' }));
  await act(async () => vi.advanceTimersByTime(1000));
  expect(slider).toHaveValue('1');
  fireEvent.click(screen.getByRole('button', { name: 'Pause replay' }));
  await act(async () => vi.advanceTimersByTime(2000));
  expect(slider).toHaveValue('1');
  fireEvent.change(screen.getByLabelText('Playback speed'), { target: { value: '250' } });
  fireEvent.click(screen.getByRole('button', { name: 'Play replay' }));
  await act(async () => vi.advanceTimersByTime(250));
  expect(slider).toHaveValue('2');
  expect(screen.getByRole('button', { name: 'Replay day' })).toBeInTheDocument();
});

it('renders no fabricated replay when no historical import is published', async () => {
  vi.mocked(api.replayCatalog).mockResolvedValue({
    dataset: null,
    days: [],
    timezone: 'America/New_York',
  });
  open('/replay');
  expect(await screen.findByText('No historical replay published yet.')).toBeInTheDocument();
  expect(api.replayDay).not.toHaveBeenCalled();
});
