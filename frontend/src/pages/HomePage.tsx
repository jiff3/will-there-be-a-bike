import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api } from '../lib/api';
import { freshness, relativeTime } from '../lib/presentation';
import type { Place, Point, TripRecommendations } from '../lib/types';
import { useClock, useResource } from '../hooks/useResource';
import { CommutePlanner } from '../components/CommutePlanner';
import { StationDetails } from '../components/StationDetails';
import { ErrorNotice, Loading } from '../components/Feedback';
import { Icon } from '../components/Icon';

const StationMap = lazy(() =>
  import('../components/StationMap').then((module) => ({ default: module.StationMap })),
);

export function HomePage() {
  const [params, setParams] = useSearchParams();
  const selectedId = params.get('station');
  const commuteId = params.get('commute');
  const demo = params.get('demo') === '1';
  const [tab, setTab] = useState<'stations' | 'plan'>('stations');
  const [inventory, setInventory] = useState<'bikes' | 'docks'>('bikes');
  const [bbox, setBbox] = useState<string>();
  const [search, setSearch] = useState('');
  const [onlyAvailable, setOnlyAvailable] = useState(false);
  const [origin, setOrigin] = useState<Place>();
  const [destination, setDestination] = useState<Place>();
  const [pickMode, setPickMode] = useState<'origin' | 'destination' | null>(null);
  const [result, setResult] = useState<TripRecommendations>();
  const [online, setOnline] = useState(navigator.onLine);
  const clock = useClock();
  // Poll responses can be newer than the last 30-second aging tick.
  const now = Math.max(clock, Date.now());
  const load = useCallback((signal: AbortSignal) => api.stations(bbox, demo, signal), [bbox, demo]);
  const catalog = useResource(load, 60000);
  const loadCommute = useCallback(
    (signal: AbortSignal) =>
      commuteId ? api.commute(commuteId, signal) : Promise.resolve(undefined),
    [commuteId],
  );
  const saved = useResource(loadCommute);
  useEffect(() => {
    if (saved.data) {
      setOrigin(saved.data.origin);
      setDestination(saved.data.destination);
      setTab('plan');
    }
  }, [saved.data]);
  const loadSelected = useCallback(
    (signal: AbortSignal) =>
      selectedId ? api.station(selectedId, signal) : Promise.resolve(undefined),
    [selectedId],
  );
  const selection = useResource(loadSelected, 60000);
  const stations = useMemo(() => catalog.data?.items ?? [], [catalog.data]);
  const selected = selection.data ?? stations.find((station) => station.external_id === selectedId);
  const highlighted = useMemo(
    () =>
      result ? [...result.pickup, ...result.destination].map((station) => station.external_id) : [],
    [result],
  );
  const mapStations = useMemo(
    () => [
      ...new Map(
        [...(result?.pickup ?? []), ...(result?.destination ?? []), ...stations].map((station) => [
          station.external_id,
          station,
        ]),
      ).values(),
    ],
    [stations, result],
  );
  const onBounds = useCallback((value: string) => setBbox(value), []);
  const onResult = useCallback((value?: TripRecommendations) => setResult(value), []);
  const onSelect = useCallback(
    (id: string) =>
      setParams(
        (previous) => {
          previous.set('station', id);
          return previous;
        },
        { replace: true },
      ),
    [setParams],
  );
  const close = () =>
    setParams(
      (previous) => {
        previous.delete('station');
        return previous;
      },
      { replace: true },
    );
  useEffect(() => {
    const update = () => setOnline(navigator.onLine);
    window.addEventListener('online', update);
    window.addEventListener('offline', update);
    return () => {
      window.removeEventListener('online', update);
      window.removeEventListener('offline', update);
    };
  }, []);
  useEffect(() => {
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setPickMode(null);
    };
    window.addEventListener('keydown', escape);
    return () => window.removeEventListener('keydown', escape);
  }, []);
  function pick(point: Point) {
    const place = {
      ...point,
      label: `Map point · ${point.latitude.toFixed(4)}, ${point.longitude.toFixed(4)}`,
    };
    if (pickMode === 'origin') setOrigin(place);
    else if (pickMode === 'destination') setDestination(place);
    setPickMode(null);
  }
  function useStation(purpose: 'origin' | 'destination', place: Place) {
    if (purpose === 'origin') setOrigin(place);
    else setDestination(place);
    setTab('plan');
  }
  const filtered = stations.filter(
    (station) =>
      station.name.toLowerCase().includes(search.toLowerCase()) &&
      (!onlyAvailable ||
        (freshness(station, now) === 'fresh' &&
          !station.is_demo &&
          (station[inventory] ?? 0) > 0 &&
          station.is_installed !== false &&
          (inventory === 'bikes' ? station.is_renting : station.is_returning) !== false)),
  );
  const freshCount = stations.filter(
    (station) => freshness(station, now) === 'fresh' && !station.is_demo,
  ).length;
  return (
    <div className={`ride-workspace ${selectedId ? 'has-selection' : ''}`}>
      <aside className="explorer-panel" aria-label="Station explorer and trip planner">
        <div className="explorer-heading">
          <p className="eyebrow">
            <span className="city-dot" />
            New York, on two wheels
          </p>
          <h1>
            A bike there.
            <br />
            <em>A dock here.</em>
          </h1>
          <p>
            Know your options.
            <br />
            Enjoy the way there.
          </p>
        </div>
        <div className="explorer-tabs" role="group" aria-label="Explore or plan">
          <button aria-pressed={tab === 'stations'} onClick={() => setTab('stations')}>
            <Icon name="pin" size={17} />
            Find a station
          </button>
          <button aria-pressed={tab === 'plan'} onClick={() => setTab('plan')}>
            <Icon name="arrow" size={17} />
            Plan a ride
          </button>
        </div>
        <div className="explorer-body">
          {saved.error && <ErrorNotice error={saved.error} retry={saved.retry} />}
          {saved.data && (
            <p className="notice">
              {saved.data.name} · saved {saved.data.departure_local} ({saved.data.timezone}), ~
              {saved.data.preferences.max_walk_meters} m walking. The planner checks departure now
              or within one hour, not a distant scheduled day.
            </p>
          )}
          {!online && (
            <p className="notice" role="status">
              Offline · showing last loaded data, if any. No simulated availability.
            </p>
          )}
          {(demo || catalog.data?.items.some((station) => station.is_demo)) && (
            <p className="notice">
              Demo mode: explicitly includes synthetic stations. Demo values are not real
              measurements. Demo recommendations are distance-only; no reliable availability risk is
              supplied.
            </p>
          )}
          {catalog.error && <ErrorNotice error={catalog.error} retry={catalog.retry} />}
          <div hidden={tab !== 'stations'}>
            <label className="station-search">
              <Icon name="search" size={18} />
              <input
                aria-label="Search loaded stations"
                type="search"
                placeholder="Search stations in this view"
                value={search}
                maxLength={100}
                onChange={(event) => setSearch(event.target.value)}
              />
            </label>
            <div className="station-filters">
              <div className="segmented" role="group" aria-label="Map inventory">
                <button aria-pressed={inventory === 'bikes'} onClick={() => setInventory('bikes')}>
                  <Icon name="bike" size={16} />
                  Bikes
                </button>
                <button aria-pressed={inventory === 'docks'} onClick={() => setInventory('docks')}>
                  <Icon name="dock" size={16} />
                  Docks
                </button>
              </div>
              <button
                className={`availability-filter ${onlyAvailable ? 'active' : ''}`}
                aria-pressed={onlyAvailable}
                onClick={() => setOnlyAvailable(!onlyAvailable)}
              >
                Available only
              </button>
            </div>
            <div className="list-heading">
              <span>
                {catalog.loading
                  ? 'Finding stations…'
                  : `${filtered.length} stations ${bbox ? 'in this view' : 'in loaded scope'}`}
              </span>
              <button
                className="text-button mobile-map-link"
                onClick={() =>
                  document
                    .querySelector('.map-area')
                    ?.scrollIntoView({ behavior: 'smooth', block: 'start' })
                }
              >
                View map
              </button>
              <button className="icon-button" aria-label="Refresh stations" onClick={catalog.retry}>
                <Icon name="refresh" size={16} />
              </button>
            </div>
            {catalog.loading && <Loading />}
            {catalog.data?.truncated && (
              <p className="notice">Showing up to 1,000 stations. Zoom in to narrow the area.</p>
            )}
            {!catalog.loading && !filtered.length && (
              <div className="empty-state">
                <Icon name="search" />
                <h3>{stations.length ? 'No matches just yet' : 'No stations in this area'}</h3>
                <p>
                  {stations.length
                    ? 'Try another name or turn off the availability filter.'
                    : 'Pan back to Midtown or check the configured data scope.'}
                </p>
                <button
                  className="text-button"
                  onClick={() => {
                    setSearch('');
                    setOnlyAvailable(false);
                    setBbox(undefined);
                  }}
                >
                  Show loaded scope
                </button>
              </div>
            )}
            <div className="station-list" aria-label="Stations">
              {filtered.map((station) => {
                const status = freshness(station, now);
                const closed =
                  station.is_installed === false ||
                  (inventory === 'bikes' ? station.is_renting : station.is_returning) === false;
                return (
                  <button
                    className={`station-card ${station.external_id === selectedId ? 'selected' : ''}`}
                    key={station.external_id}
                    onClick={() => onSelect(station.external_id)}
                    aria-pressed={station.external_id === selectedId}
                  >
                    <span className="station-card-top">
                      <strong>{station.name}</strong>
                      <Icon name="arrow" size={17} />
                    </span>
                    <span className="station-card-bottom">
                      <span className="station-inventory">
                        <Icon name="bike" size={16} />
                        <b>{station.bikes ?? '–'}</b> bikes <span className="divider" />{' '}
                        <Icon name="dock" size={15} />
                        <b>{station.docks ?? '–'}</b> docks
                      </span>
                      <span className={`freshness-dot ${status}`} />
                    </span>
                    <span className="station-status">
                      {station.is_demo
                        ? 'Synthetic demo'
                        : closed
                          ? 'Operation closed'
                          : status === 'fresh'
                            ? `Reported ${relativeTime(station.reported_at, now)}`
                            : status === 'stale'
                              ? 'Stale · last known counts'
                              : 'No inventory observation'}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
          <div hidden={tab !== 'plan'}>
            <CommutePlanner
              presetPreferences={saved.data?.preferences}
              stations={stations}
              origin={origin}
              destination={destination}
              onOrigin={setOrigin}
              onDestination={setDestination}
              onPick={(mode) => {
                setPickMode(mode);
                document
                  .querySelector('.map-area')
                  ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
              }}
              result={result}
              onResult={onResult}
              onSelect={onSelect}
              now={now}
            />
          </div>
        </div>
        <div className="explorer-foot">
          <span className={`live-dot ${freshCount ? '' : 'muted-dot'}`} />
          <span>
            {freshCount
              ? `${freshCount} fresh stations in loaded view`
              : 'Waiting for fresh station observations'}
            <small>
              {catalog.data?.items.some((station) => station.is_demo)
                ? 'Synthetic offline demo · no live provider measurements'
                : 'Official Citi Bike GBFS · checks every minute'}
            </small>
          </span>
        </div>
      </aside>
      <section className="map-area" aria-label="City map">
        <Suspense fallback={<div className="map-loading">Loading city map…</div>}>
          <StationMap
            stations={mapStations}
            selected={selected}
            inventory={inventory}
            highlighted={highlighted}
            origin={origin}
            destination={destination}
            pickMode={pickMode}
            now={now}
            onSelect={onSelect}
            onBounds={onBounds}
            onPick={pick}
          />
        </Suspense>
        <div className="map-top-label">
          <span className="city-dot" />
          <strong>MANHATTAN</strong>
          <span>
            {catalog.data?.items.some((station) => station.is_demo)
              ? 'Synthetic demo'
              : 'Citi Bike'}
          </span>
        </div>
        {pickMode && (
          <div className="map-pick-prompt" role="status">
            Tap the map for your {pickMode}.
            <button className="text-button" onClick={() => setPickMode(null)}>
              Cancel
            </button>
          </div>
        )}
        <div className="map-legend">
          <strong>{inventory === 'bikes' ? 'Bikes available' : 'Open docks'}</strong>
          <span>
            <i className="legend-good" />
            4+
          </span>
          <span>
            <i className="legend-low" />
            1–3
          </span>
          <span>
            <i className="legend-empty" />0
          </span>
          <span>
            <i className="legend-unknown" />
            Stale / unknown / closed
          </span>
          <small>Cluster numbers count stations, not bikes.</small>
        </div>
        {!selectedId && (
          <div className="map-discovery">
            <Icon name="bike" size={28} />
            <div>
              <strong>A small check. A smoother ride.</strong>
              <p>Select a station for live counts, demand forecasts, and a little more context.</p>
            </div>
          </div>
        )}
      </section>
      {selectedId && selection.error && (
        <div className="selection-error">
          <ErrorNotice error={selection.error} retry={selection.retry} />
          <button className="secondary-button" onClick={close}>
            Close details
          </button>
        </div>
      )}
      {selectedId && !selected && selection.loading && (
        <div className="selection-error">
          <Loading label="Opening station…" />
        </div>
      )}
      {selected && (
        <StationDetails
          key={selected.external_id}
          station={selected}
          now={now}
          onClose={close}
          onUse={useStation}
        />
      )}
    </div>
  );
}
