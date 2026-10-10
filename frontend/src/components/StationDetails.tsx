import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../lib/api';
import { day, freshness, historyRange, humanize, relativeTime, time } from '../lib/presentation';
import type { DemandHistory, Forecast, ForecastItem, Place, Station } from '../lib/types';
import { useResource } from '../hooks/useResource';
import { ErrorNotice, Loading } from './Feedback';
import { Icon } from './Icon';

function ForecastBar({
  item,
  label,
  className,
}: {
  item: ForecastItem;
  label: string;
  className: string;
}) {
  const maximum = Math.max(item.interval[1], item.mean, 1);
  return (
    <div className={`forecast-row ${className}`}>
      <div className="forecast-row-heading">
        <span>{label}</span>
        <strong>
          {item.mean.toFixed(1)} <small>rides</small>
        </strong>
      </div>
      <div
        className="interval-track"
        role="img"
        aria-label={`${label}: mean ${item.mean.toFixed(1)} rides; interval ${item.interval[0]} to ${item.interval[1]} rides`}
      >
        <span
          className="interval-band"
          style={{
            left: `${(item.interval[0] / maximum) * 90}%`,
            width: `${((item.interval[1] - item.interval[0]) / maximum) * 90}%`,
          }}
        />
        <i style={{ left: `${(item.mean / maximum) * 90}%` }} />
      </div>
      <p className="interval-label">
        {item.interval[0]}–{item.interval[1]} rides · nominal{' '}
        {Math.round(item.interval_nominal_coverage * 100)}% interval
      </p>
    </div>
  );
}

export function ForecastView({ forecast }: { forecast: Forecast }) {
  const [horizon, setHorizon] = useState<30 | 60>(30);
  const departures = forecast.forecasts[`departures_${horizon}`];
  const arrivals = forecast.forecasts[`arrivals_${horizon}`];
  const low = departures.confidence === 'low' || arrivals.confidence === 'low';
  const risk = forecast.availability_risk;
  const scenario = risk.horizons?.[String(horizon)];
  return (
    <>
      <div className="section-heading">
        <h3>What’s coming next?</h3>
        <span className="tag forecast-tag">Forecast</span>
      </div>
      <div className="segmented horizon-switch" role="group" aria-label="Forecast horizon">
        <button aria-pressed={horizon === 30} onClick={() => setHorizon(30)}>
          30-min horizon
        </button>
        <button aria-pressed={horizon === 60} onClick={() => setHorizon(60)}>
          60-min horizon
        </button>
      </div>
      {forecast.is_demo && (
        <p className="notice">Synthetic demonstration model. Not a real demand measurement.</p>
      )}
      {low && (
        <div className="notice">
          <strong>Use with caution</strong>
          <p>
            This model is outside its evaluated context. Use fresh measured inventory for today’s
            decisions.
          </p>
        </div>
      )}
      <ForecastBar item={departures} label="Bikes departing" className="departure-color" />
      <ForecastBar item={arrivals} label="Bikes arriving" className="arrival-color" />
      <p className="fine-print">
        Cumulative observed-ride demand from {time(forecast.as_of)} to{' '}
        {time(new Date(Date.parse(forecast.as_of) + horizon * 60000).toISOString())}. Fixed issuance
        window; part may already have elapsed. Dots show means; bands show uncertainty, not
        minimum/maximum guarantees.
        {!departures.interval_validated_for_this_context ||
        !arrivals.interval_validated_for_this_context
          ? ' Interval coverage is not validated for this prediction context.'
          : ''}
      </p>
      <div className="risk-card">
        <div className="section-heading">
          <h3>Will it empty or fill?</h3>
          <Icon name="bike" size={20} />
        </div>
        {risk.status === 'estimated_scenario' && scenario ? (
          <>
            <p className="fine-print">Uncalibrated simulation · {horizon}-minute horizon</p>
            <div className="risk-values">
              <div>
                <strong>{Math.round(scenario.scenario_empty_at_end * 100)}%</strong>
                <span>empty at horizon</span>
              </div>
              <div>
                <strong>{Math.round(scenario.scenario_full_at_end * 100)}%</strong>
                <span>full at horizon</span>
              </div>
            </div>
            <p>
              {scenario.expected_bikes_at_end.toFixed(1)} expected bikes · scenario p10–p90{' '}
              {scenario.bikes_at_end_p10}–{scenario.bikes_at_end_p90} bikes.
            </p>
            <p className="fine-print">
              These are scenario probabilities, not validated real-world stockout rates. Inventory
              reported{' '}
              {risk.inventory_reported_at ? time(risk.inventory_reported_at) : 'at an unknown time'}
              .
            </p>
          </>
        ) : (
          <>
            <strong className="risk-unavailable">No reliable risk estimate</strong>
            <p className="muted">
              {risk.reason
                ? humanize(risk.reason)
                : 'Required inventory or forecast context is unavailable'}
              . We won’t turn uncertain inputs into a confident availability claim.
            </p>
          </>
        )}
        <details>
          <summary>Simulation assumptions</summary>
          <p className="fine-print">{risk.assumptions}</p>
        </details>
      </div>
      <details className="model-details">
        <summary>Why this forecast?</summary>
        <p className="fine-print">
          Model: {humanize(departures.model)} · {Math.round(forecast.training_age_days)} days since
          training history.
        </p>
        <ul>
          {departures.drivers.map((driver) => (
            <li key={driver.feature}>
              {humanize(driver.feature)}
              {driver.value === null ? ': unavailable' : `: ${driver.value.toFixed(2)}`}
            </li>
          ))}
        </ul>
        {forecast.warnings.length > 0 && (
          <ul>
            {forecast.warnings.map((warning) => (
              <li key={warning}>{humanize(warning)}</li>
            ))}
          </ul>
        )}
        <p className="fine-print model-version">Version {forecast.model_version}</p>
        <p className="fine-print">{forecast.target_semantics}</p>
      </details>
    </>
  );
}

export function HistoryChart({ history }: { history: DemandHistory }) {
  const [selected, setSelected] = useState<number | null>(null);
  // Display aggregation only: never fill absent observations or infer inventory.
  const hours = Array.from({ length: 24 }, (_, hour) => {
    const rows = history.observations.filter(
      (row) =>
        Number(
          new Intl.DateTimeFormat('en-US', {
            timeZone: 'America/New_York',
            hour: '2-digit',
            hourCycle: 'h23',
          }).format(new Date(row.bucket_start)),
        ) === hour,
    );
    return rows.length
      ? {
          departures: rows.reduce((sum, row) => sum + row.departures, 0),
          arrivals: rows.reduce((sum, row) => sum + row.arrivals, 0),
          bins: rows.length,
        }
      : null;
  });
  const maximum = Math.max(
    1,
    ...hours.flatMap((row) => (row ? [row.departures, row.arrivals] : [])),
  );
  const row = selected === null ? null : hours[selected];
  return (
    <>
      <div className="history-chart" role="group" aria-label="Hourly observed rides, New York time">
        {hours.map((hour, i) => (
          <button
            key={i}
            className={`history-column ${hour ? '' : 'unobserved'}`}
            aria-label={`${i}:00 New York: ${hour ? `${hour.departures} departures, ${hour.arrivals} arrivals in ${hour.bins} returned 15-minute bins` : 'no returned observations, not assumed zero'}`}
            onMouseEnter={() => setSelected(i)}
            onFocus={() => setSelected(i)}
            onClick={() => setSelected(i)}
          >
            {hour && (
              <>
                <span
                  className="departure-bar"
                  style={{ height: `${(hour.departures / maximum) * 100}%` }}
                />
                <span
                  className="arrival-bar"
                  style={{ height: `${(hour.arrivals / maximum) * 100}%` }}
                />
              </>
            )}
          </button>
        ))}
      </div>
      <div className="chart-axis">
        <span>12 am</span>
        <span>6 am</span>
        <span>12 pm</span>
        <span>6 pm</span>
        <span>NY</span>
      </div>
      <div className="chart-legend">
        <span>
          <i className="departure-dot" />
          Departures
        </span>
        <span>
          <i className="arrival-dot" />
          Arrivals
        </span>
      </div>
      <p className="chart-readout" aria-live="polite">
        {selected === null
          ? 'Hover, tap or focus a bar to inspect observed rides.'
          : row
            ? `${selected}:00 · ${row.departures} departures / ${row.arrivals} arrivals · ${row.bins} returned quarter-hour bins`
            : `${selected}:00 · No returned observations. Not assumed zero.`}
      </p>
      <p className="fine-print">
        Observed rides per hour, summed only from returned 15-minute bins. Gaps and partial hours
        have unknown completeness. Repeated DST hours are combined. Trips do not reveal past
        bike/dock inventory.
      </p>
      <details>
        <summary>View exact observations ({history.observations.length})</summary>
        <div className="table-scroll">
          <table>
            <caption>15-minute observed rides · New York time</caption>
            <thead>
              <tr>
                <th>Time</th>
                <th>Departures</th>
                <th>Arrivals</th>
              </tr>
            </thead>
            <tbody>
              {history.observations.map((point) => (
                <tr key={point.bucket_start}>
                  <td>{time(point.bucket_start)}</td>
                  <td>{point.departures}</td>
                  <td>{point.arrivals}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </>
  );
}

function HistoricalDay({ stationId, initialDate }: { stationId: string; initialDate: string }) {
  const [date, setDate] = useState(initialDate);
  const load = useCallback(
    (signal: AbortSignal) => {
      if (!date) return Promise.resolve(undefined);
      const [start, end] = historyRange(date);
      return api.demand(stationId, start, end, signal);
    },
    [date, stationId],
  );
  const resource = useResource(load);
  return (
    <>
      <label className="history-date">
        Historical date · New York
        <input
          type="date"
          value={date}
          onChange={(event) => setDate(event.target.value)}
          required
        />
      </label>
      {resource.loading && <Loading label="Loading observed rides…" />}
      {resource.error && <ErrorNotice error={resource.error} retry={resource.retry} />}
      {resource.data &&
        (resource.data.observations.length ? (
          <HistoryChart history={resource.data} />
        ) : (
          <div className="empty-state">
            <Icon name="clock" />
            <h3>No observations for this date</h3>
            <p>
              Try a date in an imported archive. No returned trips does not establish zero demand.
            </p>
          </div>
        ))}
      {resource.data && (
        <p className="fine-print">
          {resource.data.sources.length} completed source archive(s) recorded for this station.{' '}
          {resource.data.is_demo
            ? 'Synthetic observations.'
            : 'Persisted historical trip observations.'}
        </p>
      )}
    </>
  );
}

function HistoryView({ stationId }: { stationId: string }) {
  const load = useCallback((signal: AbortSignal) => api.model(signal), []);
  const model = useResource(load);
  if (model.loading) return <Loading label="Finding the model’s reference archive…" />;
  return (
    <>
      <div className="section-heading">
        <h3>A look back</h3>
        <span className="tag">Observed rides</span>
      </div>
      <p className="muted small-copy">
        Explore demand by time of day. These are recorded rides, not past station inventory.
      </p>
      {model.data && (
        <p className="fine-print">
          Model reference archive: {day(model.data.source.first_bucket * 1000)} –{' '}
          {day(model.data.source.last_bucket * 1000)}. Other dates depend on imported data.
        </p>
      )}
      {model.error && (
        <p className="notice">Model reference dates unavailable. Choose a date you’ve imported.</p>
      )}
      <HistoricalDay
        stationId={stationId}
        initialDate={day(model.data ? model.data.source.last_bucket * 1000 : Date.now())}
      />
    </>
  );
}

export function StationDetails({
  station,
  now,
  onClose,
  onUse,
}: {
  station: Station;
  now: number;
  onClose: () => void;
  onUse: (purpose: 'origin' | 'destination', place: Place) => void;
}) {
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    heading.current?.focus({ preventScroll: true });
    if (window.innerWidth <= 760)
      heading.current?.scrollIntoView({
        behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
        block: 'start',
      });
  }, []);
  const [tab, setTab] = useState<'outlook' | 'history'>('outlook');
  const load = useCallback(
    (signal: AbortSignal) => api.forecast(station.external_id, signal),
    [station.external_id],
  );
  const forecast = useResource(load, 300000);
  const status = freshness(station, now);
  const slots = (station.bikes ?? 0) + (station.docks ?? 0);
  const place: Place = {
    label: station.name,
    latitude: station.latitude,
    longitude: station.longitude,
  };
  return (
    <aside className="station-details" aria-label={`Details for ${station.name}`}>
      <div className="details-header">
        <div>
          <p className="eyebrow">Station details</p>
          <h2 ref={heading} tabIndex={-1}>
            {station.name}
          </h2>
        </div>
        <button className="icon-button" onClick={onClose} aria-label="Close station details">
          <Icon name="close" />
        </button>
      </div>
      <div className="details-scroll">
        <div className="observation-heading">
          <span className={`tag tag-${status}`}>
            {station.is_demo
              ? 'Synthetic demo'
              : status === 'fresh'
                ? 'Live observation'
                : status === 'stale'
                  ? 'Stale observation'
                  : 'No inventory data'}
          </span>
          <span className="fine-print">{relativeTime(station.reported_at, now)}</span>
        </div>
        <div className="inventory-values">
          <div>
            <Icon name="bike" />
            <strong>{station.bikes ?? '–'}</strong>
            <span>bikes available</span>
          </div>
          <div>
            <Icon name="dock" />
            <strong>{station.docks ?? '–'}</strong>
            <span>open docks</span>
          </div>
        </div>
        {station.bikes !== null && station.docks !== null && (
          <div
            className="inventory-track"
            aria-label={`${station.bikes} bikes and ${station.docks} open docks, ${slots} currently usable slots`}
            role="img"
          >
            <span style={{ width: `${slots ? (station.bikes / slots) * 100 : 0}%` }} />
          </div>
        )}
        <p className="fine-print">
          {slots} usable slots
          {station.capacity !== null ? ` · ${station.capacity} catalog capacity` : ''}. Disabled
          slots may account for a difference.
        </p>
        {status !== 'fresh' && (
          <p className="notice">
            {status === 'stale'
              ? 'Last known counts only. Do not rely on these numbers until refreshed.'
              : 'Inventory is unknown, not zero.'}
          </p>
        )}
        <div className="operation-flags">
          <span>
            Pickup:{' '}
            {station.is_installed === false || station.is_renting === false
              ? 'closed'
              : station.is_installed === null || station.is_renting === null
                ? 'unknown'
                : 'enabled'}
          </span>
          <span>
            Returns:{' '}
            {station.is_installed === false || station.is_returning === false
              ? 'closed'
              : station.is_installed === null || station.is_returning === null
                ? 'unknown'
                : 'enabled'}
          </span>
        </div>
        <div className="station-actions">
          <button className="secondary-button" onClick={() => onUse('origin', place)}>
            Start here <Icon name="arrow" size={15} />
          </button>
          <button className="secondary-button" onClick={() => onUse('destination', place)}>
            Go here <Icon name="pin" size={15} />
          </button>
        </div>
        <div className="detail-tabs" role="group" aria-label="Station information">
          <button aria-pressed={tab === 'outlook'} onClick={() => setTab('outlook')}>
            Demand outlook
          </button>
          <button aria-pressed={tab === 'history'} onClick={() => setTab('history')}>
            Ride history
          </button>
        </div>
        {tab === 'outlook' ? (
          <>
            {station.is_demo && (
              <p className="notice">
                Synthetic station. Demand charts use an unvalidated pooled historical reference, not
                a forecast validated for this demo station. No reliable availability risk is
                supplied.
              </p>
            )}
            {forecast.loading && <Loading label="Loading demand forecasts…" />}
            {forecast.error && <ErrorNotice error={forecast.error} retry={forecast.retry} />}
            {forecast.data && <ForecastView forecast={forecast.data} />}
          </>
        ) : (
          <HistoryView stationId={station.external_id} />
        )}
        <details className="data-timestamps">
          <summary>Observation timestamps</summary>
          <dl>
            {[
              ['Station reported', station.reported_at],
              ['Feed updated', station.feed_updated_at],
              ['Collected', station.fetched_at],
            ].map(([label, value]) => (
              <div key={label}>
                <dt>{label}</dt>
                <dd>
                  {value
                    ? new Date(value).toLocaleString('en-US', {
                        timeZone: 'America/New_York',
                        timeZoneName: 'short',
                      })
                    : 'Unknown'}
                </dd>
              </div>
            ))}
          </dl>
        </details>
      </div>
    </aside>
  );
}
