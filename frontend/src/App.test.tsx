import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { vi } from 'vitest';
import { App } from './App';
import { api, ApiError } from './lib/api';
import { forecast, history, recommendations, station } from './test/fixtures';
import { ForecastView } from './components/StationDetails';

vi.mock('./components/StationMap', () => ({
  StationMap: ({ onSelect }: { onSelect: (id: string) => void }) => (
    <div aria-label="Bike-share station map">
      <button onClick={() => onSelect('alpha')}>Map station Alpha</button>
    </div>
  ),
}));

beforeEach(() => {
  vi.spyOn(api, 'me').mockRejectedValue(
    new ApiError('Sign in required', 401, 'authentication_required'),
  );
  vi.spyOn(api, 'stations').mockResolvedValue({
    items: [station(), station('beta', 'Beta & Second')],
    truncated: false,
  });
  vi.spyOn(api, 'station').mockImplementation(async (id) =>
    station(id, id === 'alpha' ? 'Alpha & First' : 'Beta & Second'),
  );
  vi.spyOn(api, 'searchStations').mockImplementation(async (query) =>
    [station(), station('beta', 'Beta & Second')].filter((row) =>
      row.name.toLowerCase().includes(query.toLowerCase()),
    ),
  );
  vi.spyOn(api, 'forecast').mockResolvedValue(forecast);
  vi.spyOn(api, 'model').mockResolvedValue({
    version: 'synthetic',
    created_at: '2026-10-09T00:00:00Z',
    is_demo: true,
    config: { test_end: '2025-03-01' },
    source: { first_bucket: 1735707600, last_bucket: 1740804300, station_count: 2, type: 'test' },
  });
  vi.spyOn(api, 'demand').mockResolvedValue(history);
  vi.spyOn(api, 'recommend').mockResolvedValue(recommendations());
});
afterEach(() => vi.restoreAllMocks());
const open = (route = '/') =>
  render(
    <MemoryRouter initialEntries={[route]}>
      <App />
    </MemoryRouter>,
  );

it('labels simulated risk as uncalibrated and renders only server-provided scenario values', () => {
  const scenario = {
    scenario_empty_at_end: 0.2,
    scenario_full_at_end: 0.1,
    scenario_empty_at_any_time: 0.3,
    scenario_full_at_any_time: 0.15,
    expected_bikes_at_end: 5,
    bikes_at_end_p10: 1,
    bikes_at_end_p90: 9,
  };
  render(
    <ForecastView
      forecast={{
        ...forecast,
        availability_risk: {
          status: 'estimated_scenario',
          calibrated_stockout_probability: false,
          assumptions: 'Synthetic test scenario only',
          horizons: { '30': scenario, '60': { ...scenario, scenario_empty_at_end: 0.4 } },
        },
      }}
    />,
  );
  expect(screen.getByText('20%')).toBeInTheDocument();
  expect(screen.getByText(/not validated real-world stockout rates/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '60-min horizon' }));
  expect(screen.getByText('40%')).toBeInTheDocument();
});

it('searches station landmarks outside the current map viewport', async () => {
  vi.mocked(api.stations).mockResolvedValue({ items: [station()], truncated: false });
  open('/?station=alpha');
  fireEvent.click(await screen.findByRole('button', { name: 'Start here' }));
  fireEvent.change(screen.getByRole('combobox', { name: 'Destination' }), {
    target: { value: 'Beta' },
  });
  expect(await screen.findByRole('option', { name: 'Beta & Second' })).toBeInTheDocument();
  expect(api.searchStations).toHaveBeenCalledWith('Beta', expect.any(AbortSignal));
});

it('supports arrow-key landmark selection and dismisses the picker on Escape', async () => {
  open('/?station=alpha');
  fireEvent.click(await screen.findByRole('button', { name: 'Start here' }));
  const destination = screen.getByRole('combobox', { name: 'Destination' });
  fireEvent.focus(destination);
  fireEvent.change(destination, { target: { value: 'Beta' } });
  const option = screen.getByRole('option', { name: 'Beta & Second' });
  fireEvent.keyDown(destination, { key: 'ArrowDown' });
  expect(destination).toHaveAttribute('aria-activedescendant', option.id);
  fireEvent.keyDown(destination, { key: 'Escape' });
  expect(destination).toHaveAttribute('aria-expanded', 'false');
  fireEvent.keyDown(destination, { key: 'ArrowDown' });
  fireEvent.keyDown(destination, { key: 'Enter' });
  expect(destination).toHaveValue('Beta & Second');
  expect(screen.getByRole('button', { name: 'Find my ride' })).toBeEnabled();
});

it('connects map selection, observed inventory, uncertainty and both forecast horizons', async () => {
  open();
  expect(screen.getByRole('heading', { name: /a bike there/i })).toBeInTheDocument();
  fireEvent.click(await screen.findByRole('button', { name: 'Map station Alpha' }));
  const details = await screen.findByRole('complementary', { name: 'Details for Alpha & First' });
  expect(await within(details).findByText('Use with caution')).toBeInTheDocument();
  expect(
    within(details).getByRole('img', { name: /5 bikes and 18 open docks/ }),
  ).toBeInTheDocument();
  expect(within(details).getByText('No reliable risk estimate')).toBeInTheDocument();
  fireEvent.click(within(details).getByRole('button', { name: '60-min horizon' }));
  expect(
    within(details).getByRole('img', { name: /Bikes departing: mean 4.0/ }),
  ).toBeInTheDocument();
  expect(within(details).getByText(/coverage is not validated/)).toBeInTheDocument();
});

it('loads persisted sparse history and does not turn gaps into zero counts', async () => {
  open('/?station=alpha');
  fireEvent.click(await screen.findByRole('button', { name: 'Ride history' }));
  const column = await screen.findByRole('button', {
    name: /8:00 New York: 5 departures, 1 arrivals in 2/,
  });
  fireEvent.focus(column);
  expect(screen.getByText(/8:00 · 5 departures/)).toBeInTheDocument();
  expect(
    screen.getByRole('button', {
      name: /^0:00 New York: no returned observations, not assumed zero$/,
    }),
  ).toBeInTheDocument();
  expect(api.demand).toHaveBeenCalledWith(
    'alpha',
    '2025-02-28T05:00:00.000Z',
    '2025-03-01T05:00:00.000Z',
    expect.any(AbortSignal),
  );
});

it('submits exact coordinate/preferences contracts and displays server-ranked alternatives', async () => {
  open('/?station=alpha');
  fireEvent.click(await screen.findByRole('button', { name: 'Start here' }));
  fireEvent.change(screen.getByRole('combobox', { name: 'Destination' }), {
    target: { value: 'Beta' },
  });
  fireEvent.click(screen.getByRole('option', { name: 'Beta & Second' }));
  fireEvent.click(screen.getByRole('button', { name: 'Find my ride' }));
  const results = await screen.findByRole('region', { name: 'Trip recommendations' });
  expect(within(results).getByText(/not routing. Walking is additional/)).toBeInTheDocument();
  expect(within(results).getAllByText('Measured inventory ranking')).toHaveLength(2);
  expect(within(results).getByText('5 bikes')).toBeInTheDocument();
  expect(api.recommend).toHaveBeenCalledWith(
    expect.objectContaining({
      origin: { latitude: 40.748, longitude: -73.985 },
      destination: { latitude: 40.748, longitude: -73.985 },
      preferences: { max_walk_meters: 1000, reliability_weight: 0.7 },
    }),
    expect.any(AbortSignal),
  );
  fireEvent.change(screen.getByRole('combobox', { name: 'Destination' }), {
    target: { value: 'Different point' },
  });
  await waitFor(() =>
    expect(screen.queryByRole('region', { name: 'Trip recommendations' })).not.toBeInTheDocument(),
  );
});

it('keeps observations usable when the model service fails', async () => {
  vi.mocked(api.forecast).mockRejectedValue(
    new ApiError('Approved model unavailable', 503, 'model_unavailable', 'safe-id'),
  );
  open('/?station=alpha');
  expect(await screen.findByText('Approved model unavailable')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Start here' })).toBeEnabled();
  expect(screen.getByText('Reference: safe-id')).toBeInTheDocument();
});

it('clearly labels stale and unknown observations instead of claiming live zero inventory', async () => {
  vi.mocked(api.stations).mockResolvedValue({
    items: [
      { ...station(), stale: true },
      { ...station('missing', 'Unknown Station'), bikes: null, docks: null, reported_at: null },
    ],
    truncated: false,
  });
  open();
  expect(await screen.findByText('Stale · last known counts')).toBeInTheDocument();
  expect(screen.getByText('No inventory observation')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Available only' }));
  expect(screen.getByText('No matches just yet')).toBeInTheDocument();
});

it('provides recoverable catalog errors without invented stations', async () => {
  vi.mocked(api.stations).mockRejectedValue(new Error('Service unavailable'));
  open();
  expect(await screen.findByRole('alert')).toHaveTextContent('Service unavailable');
  expect(screen.queryByText('Alpha & First')).not.toBeInTheDocument();
  vi.mocked(api.stations).mockResolvedValue({ items: [station()], truncated: false });
  fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
  expect(await screen.findByText('Alpha & First')).toBeInTheDocument();
});

it('renders a useful not-found route', () => {
  open('/missing');
  expect(screen.getByRole('heading', { name: /off course/i })).toBeInTheDocument();
});
