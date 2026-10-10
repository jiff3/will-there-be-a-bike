import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '../lib/api';
import type { ReplayComparison, ReplayDay } from '../lib/types';
import { useResource } from '../hooks/useResource';
import { ErrorNotice, Loading } from '../components/Feedback';
import { advanceFrame } from '../lib/replay';

const StationMap = lazy(() =>
  import('../components/StationMap').then((module) => ({ default: module.StationMap })),
);
const clock = (value: string) =>
  new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York',
    hour: 'numeric',
    minute: '2-digit',
    timeZoneName: 'short',
  }).format(new Date(value));
const noop = () => {};
const noHighlights: string[] = [];

function Comparison({ item }: { item?: ReplayComparison }) {
  if (!item)
    return (
      <p className="notice">
        No stored historical forecast at this station/time. Recorded rides still replay normally;
        forecasts are never invented on playback.
      </p>
    );
  return (
    <section className="replay-comparison">
      <h3>Looking forward, then looking back.</h3>
      <p className="notice">
        Retrospective, held-out forecasts using the sealed training reference and calendar available
        at this event time. Computed later ({new Date(item.computed_at).toLocaleDateString()}), not
        recorded live. No target rides were supplied as features.
      </p>
      <div className="comparison-grid">
        {(['departures_30', 'arrivals_30', 'departures_60', 'arrivals_60'] as const).map((key) => (
          <div className="comparison-cell" key={key}>
            <strong>
              {key.startsWith('departures') ? 'Departures' : 'Arrivals'} ·{' '}
              {key.endsWith('30') ? '30' : '60'} min
            </strong>
            <span className="comparison-number">
              {item.forecasts[key].mean.toFixed(1)} <small>predicted rides</small>
            </span>
            <span>
              {item.forecasts[key].interval.join('–')} rides · nominal{' '}
              {item.forecasts[key].interval_nominal_coverage * 100}% interval
            </span>
            <b>
              {item.observed[key] ?? 'Unknown'} <small>subsequently observed rides</small>
            </b>
          </div>
        ))}
      </div>
      <p className="fine-print">
        Model {item.model_version}. Cumulative windows begin at {clock(item.as_of)}. Unknown
        outcomes lack complete coverage. Intervals are estimates, not guarantees; these are not
        historical stockout probabilities.
      </p>
    </section>
  );
}

function ReplayWorkspace({
  day,
  onDay,
  days,
}: {
  day: string;
  onDay: (value: string) => void;
  days: string[];
}) {
  const [frame, setFrame] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1000);
  const [metric, setMetric] = useState<'departures' | 'arrivals'>('departures');
  const [stationId, setStationId] = useState('');
  const [search, setSearch] = useState('');
  const load = useCallback((signal: AbortSignal) => api.replayDay(day, signal), [day]);
  const replay = useResource(load);
  const buckets = replay.data?.buckets ?? [];
  const safeFrame = Math.min(frame, Math.max(0, buckets.length - 1));
  useEffect(() => {
    setFrame(0);
    setPlaying(false);
  }, [day]);
  useEffect(() => {
    if (!playing || replay.loading || buckets.length === 0) return;
    if (safeFrame >= buckets.length - 1) {
      setPlaying(false);
      return;
    }
    const timer = setTimeout(() => setFrame((index) => advanceFrame(index, buckets.length)), speed);
    return () => clearTimeout(timer);
  }, [playing, replay.loading, buckets.length, safeFrame, speed]);
  useEffect(() => {
    const pause = () => {
      if (document.hidden) setPlaying(false);
    };
    document.addEventListener('visibilitychange', pause);
    return () => document.removeEventListener('visibilitychange', pause);
  }, []);
  const data = replay.data;
  const selected =
    data?.stations.find((item) => item.external_id === stationId) ?? data?.stations[0];
  const counts = useMemo(
    () =>
      Object.fromEntries(
        (data?.series ?? []).map((item) => [item.station_id, item[metric][safeFrame] ?? null]),
      ),
    [data, metric, safeFrame],
  );
  const selectedSeries = data?.series.find((item) => item.station_id === selected?.external_id);
  const loadComparisons = useCallback(
    (signal: AbortSignal) =>
      selected
        ? api.replayComparisons(selected.external_id, day, signal)
        : Promise.resolve(undefined),
    [selected, day],
  );
  const comparisons = useResource(loadComparisons);
  const selectedComparison = comparisons.data?.items.find(
    (item) => Date.parse(item.as_of) === Date.parse(buckets[safeFrame]),
  );
  const known = Object.values(counts).filter((value): value is number => value !== null);
  const filtered =
    data?.stations.filter((item) => item.name.toLowerCase().includes(search.toLowerCase())) ?? [];
  const selectFrame = (index: number) => {
    setPlaying(false);
    setFrame(Math.min(Math.max(index, 0), Math.max(buckets.length - 1, 0)));
  };
  return (
    <>
      <section className="replay-controls rider-card" aria-label="Historical playback controls">
        <div className="form-grid">
          <label>
            Recorded day (New York)
            <select value={day} onChange={(event) => onDay(event.target.value)}>
              {days.map((item) => (
                <option key={item}>{item}</option>
              ))}
            </select>
          </label>
          <label>
            Map shows
            <select
              value={metric}
              onChange={(event) => setMetric(event.target.value as typeof metric)}
            >
              <option value="departures">Observed departures / 15 min</option>
              <option value="arrivals">Observed arrivals / 15 min</option>
            </select>
          </label>
          <label>
            Playback speed
            <select value={speed} onChange={(event) => setSpeed(Number(event.target.value))}>
              <option value="2000">Slow · 15 min / 2 sec</option>
              <option value="1000">Normal · 15 min / sec</option>
              <option value="250">Fast · 1 hour / sec</option>
            </select>
          </label>
        </div>
        {replay.loading && <Loading label="Loading recorded demand…" />}
        {replay.error && <ErrorNotice error={replay.error} retry={replay.retry} />}
        {!!buckets.length && (
          <>
            <div className="timeline-heading">
              <button
                className="primary-button"
                onClick={() => {
                  if (safeFrame === buckets.length - 1) setFrame(0);
                  setPlaying((value) => !value);
                }}
              >
                {playing
                  ? 'Pause replay'
                  : safeFrame === buckets.length - 1
                    ? 'Replay day'
                    : 'Play replay'}
              </button>
              <output aria-live={playing ? 'off' : 'polite'}>
                {clock(buckets[safeFrame])}{' '}
                <small>
                  {known.length
                    ? `${known.reduce((sum, value) => sum + value, 0)} observed ${metric}`
                    : 'Unknown observed total'}{' '}
                  · {known.length}/{data?.stations.length} stations with verified coverage
                </small>
              </output>
              <button
                className="secondary-button"
                disabled={safeFrame === 0}
                onClick={() => selectFrame(safeFrame - 1)}
              >
                Previous 15 min
              </button>
              <button
                className="secondary-button"
                disabled={safeFrame === buckets.length - 1}
                onClick={() => selectFrame(safeFrame + 1)}
              >
                Next 15 min
              </button>
            </div>
            <label className="timeline-slider">
              Time of day
              <input
                aria-label="Historical timeline"
                type="range"
                min="0"
                max={buckets.length - 1}
                step="1"
                value={safeFrame}
                onChange={(event) => selectFrame(Number(event.target.value))}
                aria-valuetext={clock(buckets[safeFrame])}
              />
            </label>
            <div className="timeline-labels">
              <span>{clock(buckets[0])}</span>
              <span>{buckets.length} quarter-hour frames · DST-aware</span>
              <span>{clock(buckets[buckets.length - 1])}</span>
            </div>
          </>
        )}
        {data && buckets.length === 0 && (
          <p className="notice">
            No recorded demand for this day. Choose a supported date; missing observations are not
            zero rides.
          </p>
        )}
      </section>
      {data && buckets.length > 0 && (
        <div className="replay-layout">
          <section className="replay-map" aria-label="Historical demand city map">
            <Suspense fallback={<Loading label="Loading city map…" />}>
              <StationMap
                stations={data.stations}
                selected={selected}
                replayCounts={counts}
                inventory="bikes"
                highlighted={noHighlights}
                pickMode={null}
                now={0}
                onSelect={setStationId}
                onBounds={noop}
                onPick={noop}
              />
            </Suspense>
            <div className="replay-map-label">
              <span className="tag tag-forecast">Historical rides, not inventory</span>
              <strong>
                {day} · {clock(buckets[safeFrame])}
              </strong>
              <small>
                Numbers: {metric} per 15 min. Darker green = more rides; — = unknown. Cluster
                numbers count stations.
              </small>
              <small>Map uses current catalog geography, not historical station locations.</small>
            </div>
          </section>
          <aside className="replay-stations rider-card">
            <h2>Across the city.</h2>
            <label>
              Find a replay station
              <input
                type="search"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
            </label>
            <div className="replay-station-list">
              {filtered.map((item) => (
                <button
                  key={item.external_id}
                  aria-pressed={selected?.external_id === item.external_id}
                  onClick={() => setStationId(item.external_id)}
                >
                  <span>{item.name}</span>
                  <strong>
                    {counts[item.external_id] ?? '—'} <small>rides</small>
                  </strong>
                </button>
              ))}
              {!filtered.length && <p>No matching station in this replay.</p>}
            </div>
          </aside>
        </div>
      )}
      {selected && selectedSeries && (
        <section className="rider-card replay-detail">
          <p className="eyebrow">A day in recorded rides</p>
          <h2>{selected.name}</h2>
          <p className="muted">
            {day} · green departures, violet arrivals · observed rides per 15 minutes. Gaps mean
            unverified coverage, not zero.
          </p>
          <DemandChart
            series={selectedSeries}
            buckets={buckets}
            frame={safeFrame}
            onFrame={selectFrame}
          />
          <p className="replay-current" aria-live={playing ? 'off' : 'polite'}>
            {clock(buckets[safeFrame])}: {selectedSeries.departures[safeFrame] ?? 'Unknown'}{' '}
            departures · {selectedSeries.arrivals[safeFrame] ?? 'Unknown'} arrivals.
          </p>
          {comparisons.loading ? (
            <Loading label="Loading stored comparisons…" />
          ) : comparisons.error ? (
            <ErrorNotice error={comparisons.error} retry={comparisons.retry} />
          ) : (
            <Comparison item={selectedComparison} />
          )}
        </section>
      )}
    </>
  );
}

function DemandChart({
  series,
  buckets,
  frame,
  onFrame,
}: {
  series: ReplayDay['series'][number];
  buckets: string[];
  frame: number;
  onFrame: (index: number) => void;
}) {
  const max = Math.max(
    1,
    ...series.departures.map((value) => value ?? 0),
    ...series.arrivals.map((value) => value ?? 0),
  );
  const width = 900,
    height = 170,
    step = width / buckets.length;
  return (
    <div className="replay-chart">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label="Recorded station departures and arrivals through this day. Use the timeline slider to select a quarter hour."
        onClick={(event) => {
          const rect = event.currentTarget.getBoundingClientRect();
          onFrame(Math.floor(((event.clientX - rect.left) / rect.width) * buckets.length));
        }}
      >
        <line x1="0" x2={width} y1="85" y2="85" stroke="#c8d0c1" />
        {buckets.map((bucket, index) => (
          <g key={bucket}>
            <rect
              x={index * step + 0.5}
              y={85 - ((series.departures[index] ?? 0) / max) * 70}
              width={Math.max(1, step - 1)}
              height={((series.departures[index] ?? 0) / max) * 70}
              fill="#256250"
            />
            <rect
              x={index * step + 0.5}
              y="86"
              width={Math.max(1, step - 1)}
              height={((series.arrivals[index] ?? 0) / max) * 70}
              fill="#8c71af"
            />
            {(series.departures[index] === null || series.arrivals[index] === null) && (
              <rect
                x={index * step}
                y="80"
                width={Math.max(1, step - 1)}
                height="10"
                fill="#9ba49e"
                opacity="0.65"
              />
            )}
            <title>
              {clock(bucket)}: {series.departures[index] ?? 'unknown'} departures,{' '}
              {series.arrivals[index] ?? 'unknown'} arrivals
            </title>
          </g>
        ))}
        <line
          x1={(frame + 0.5) * step}
          x2={(frame + 0.5) * step}
          y1="0"
          y2={height}
          stroke="#b3503d"
          strokeWidth="2"
        />
      </svg>
      <div className="timeline-labels">
        <span>{clock(buckets[0])}</span>
        <span>Scale: {max} rides above / below baseline</span>
        <span>{clock(buckets[buckets.length - 1])}</span>
      </div>
    </div>
  );
}

export function ReplayPage() {
  const [day, setDay] = useState('');
  const load = useCallback((signal: AbortSignal) => api.replayCatalog(signal), []);
  const catalog = useResource(load);
  const selectedDay = day || catalog.data?.days.at(-1) || '';
  return (
    <section className="rider-page replay-page">
      <div className="rider-page-heading">
        <div>
          <p className="eyebrow">The rhythm of a city</p>
          <h1>Watch the rides ripple.</h1>
          <p className="lede">
            {catalog.data?.dataset?.is_demo
              ? 'Offline demo: generated synthetic ride counts, one quarter hour at a time. These are not Citi Bike observations.'
              : 'Move through a real day of Citi Bike departures and arrivals. Recorded demand, one quarter hour at a time.'}
          </p>
        </div>
        <span className="tag">New York time</span>
      </div>
      <p className="notice">
        Trip records describe rides, not how many bikes or docks were available. This replay never
        reconstructs historical inventory or stockout events.
      </p>
      {catalog.loading && <Loading label="Finding supported replay dates…" />}
      {catalog.error && <ErrorNotice error={catalog.error} retry={catalog.retry} />}
      {catalog.data?.days.length === 0 && (
        <div className="rider-empty">
          <h2>No historical replay published yet.</h2>
          <p>
            A local operator must publish a verified coverage-aware demand export. No synthetic city
            animation is substituted.
          </p>
        </div>
      )}
      {selectedDay && (
        <ReplayWorkspace day={selectedDay} onDay={setDay} days={catalog.data?.days ?? []} />
      )}{' '}
      {catalog.data?.dataset && (
        <details className="replay-provenance">
          <summary>Replay provenance</summary>
          <p>
            {catalog.data.dataset.row_count.toLocaleString()} bins ·{' '}
            {catalog.data.dataset.source_label} · published{' '}
            {new Date(catalog.data.dataset.created_at).toLocaleString()}
          </p>
          <code>SHA-256 {catalog.data.dataset.source_sha256}</code>
        </details>
      )}
    </section>
  );
}
