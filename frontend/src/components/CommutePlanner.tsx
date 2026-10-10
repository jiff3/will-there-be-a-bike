import { useCallback, useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../auth/context';
import { api } from '../lib/api';
import { freshness, time } from '../lib/presentation';
import type { Place, Recommendation, Station, TripRecommendations } from '../lib/types';
import { ErrorNotice } from './Feedback';
import { Icon } from './Icon';
import { useResource } from '../hooks/useResource';

export function PlacePicker({
  label,
  value,
  stations,
  onChange,
  onMap,
  stationOnly = false,
}: {
  label: string;
  value?: Place;
  stations: Station[];
  onChange: (value?: Place) => void;
  onMap?: () => void;
  stationOnly?: boolean;
}) {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [activeOption, setActiveOption] = useState(-1);
  const searchInput = useRef<HTMLInputElement>(null);
  const [latitude, setLatitude] = useState('');
  const [longitude, setLongitude] = useState('');
  const [coordinateError, setCoordinateError] = useState('');
  const [debounced, setDebounced] = useState('');
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(query.trim()), 250);
    return () => clearTimeout(timer);
  }, [query]);
  const load = useCallback(
    (signal: AbortSignal) =>
      open && debounced.length >= 2
        ? api.searchStations(debounced, signal)
        : Promise.resolve(undefined),
    [open, debounced],
  );
  const matches = useResource(load);
  const local = stations
    .filter((station) => station.name.toLowerCase().includes(query.toLowerCase()))
    .slice(0, 6);
  const candidates = debounced === query.trim() && matches.data ? matches.data : local;
  const options = candidates.filter((station) => !stationOnly || !station.is_demo);
  const pickerId = useId();
  const id = `${label.toLowerCase().replaceAll(' ', '-')}-${pickerId}`;
  function choose(station: Station) {
    onChange({
      label: station.name,
      latitude: station.latitude,
      longitude: station.longitude,
      station_id: station.external_id,
    });
    setQuery('');
    setActiveOption(-1);
    searchInput.current?.focus();
    setOpen(false);
  }
  return (
    <div
      className="place-picker"
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node)) setOpen(false);
      }}
    >
      <label htmlFor={`${id}-search`}>{label}</label>
      <div className="place-input">
        <span className={`point-letter point-${label === 'Origin' ? 'origin' : 'destination'}`}>
          {label === 'Origin' ? 'A' : 'B'}
        </span>
        <input
          id={`${id}-search`}
          ref={searchInput}
          role="combobox"
          autoComplete="off"
          aria-expanded={open}
          aria-controls={`${id}-options`}
          aria-autocomplete="list"
          aria-activedescendant={
            open && options[activeOption]
              ? `${id}-option-${options[activeOption].external_id}`
              : undefined
          }
          placeholder="Find a station or choose on map"
          value={value?.label ?? query}
          onFocus={() => setOpen(true)}
          onChange={(event) => {
            onChange(undefined);
            setQuery(event.target.value);
            setActiveOption(-1);
            setOpen(true);
          }}
          onKeyDown={(event) => {
            if (event.key === 'Escape') setOpen(false);
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
              event.preventDefault();
              setOpen(true);
              if (options.length)
                setActiveOption((index) =>
                  event.key === 'ArrowDown'
                    ? (index + 1) % options.length
                    : (index <= 0 ? options.length : index) - 1,
                );
            }
            if (event.key === 'Enter' && open) {
              event.preventDefault();
              const option = options[activeOption] ?? options[0];
              if (option) choose(option);
            }
          }}
        />
        {onMap && (
          <button
            type="button"
            className="icon-button"
            title={`Choose ${label.toLowerCase()} on map`}
            aria-label={`Choose ${label.toLowerCase()} on map`}
            onClick={onMap}
          >
            <Icon name="pin" size={18} />
          </button>
        )}
      </div>
      {open && (
        <div
          className="place-options"
          role="listbox"
          id={`${id}-options`}
          aria-label={`${label} station matches`}
        >
          {options.length ? (
            options.map((station, index) => (
              <button
                type="button"
                role="option"
                aria-selected={activeOption === index || value?.label === station.name}
                id={`${id}-option-${station.external_id}`}
                key={station.external_id}
                onClick={() => choose(station)}
              >
                <Icon name="pin" size={15} />
                {station.name}
              </button>
            ))
          ) : (
            <p>
              {matches.loading || debounced !== query.trim()
                ? 'Searching station landmarks…'
                : 'No matching station. Choose on the map or enter coordinates.'}
            </p>
          )}
          {matches.error && (
            <p>Station search unavailable. Map points and coordinates still work.</p>
          )}
        </div>
      )}
      {!stationOnly && (
        <details className="coordinate-entry">
          <summary>Enter approximate coordinates</summary>
          <div className="coordinate-fields">
            <label>
              Latitude
              <input
                type="number"
                step="any"
                min="-90"
                max="90"
                value={latitude}
                onChange={(event) => setLatitude(event.target.value)}
              />
            </label>
            <label>
              Longitude
              <input
                type="number"
                step="any"
                min="-180"
                max="180"
                value={longitude}
                onChange={(event) => setLongitude(event.target.value)}
              />
            </label>
          </div>
          <button
            type="button"
            className="text-button"
            onClick={() => {
              const lat = Number(latitude),
                lon = Number(longitude);
              if (
                !latitude ||
                !longitude ||
                !Number.isFinite(lat) ||
                !Number.isFinite(lon) ||
                Math.abs(lat) > 90 ||
                Math.abs(lon) > 180
              ) {
                setCoordinateError('Enter valid WGS84 latitude and longitude.');
                return;
              }
              setCoordinateError('');
              setOpen(false);
              onChange({
                latitude: lat,
                longitude: lon,
                label: `${lat.toFixed(4)}, ${lon.toFixed(4)}`,
              });
            }}
          >
            Use this point
          </button>
          {coordinateError && <p role="alert">{coordinateError}</p>}
        </details>
      )}
    </div>
  );
}

export function RecommendationCard({
  item,
  index,
  purpose,
  onSelect,
  now,
}: {
  item: Recommendation;
  index: number;
  purpose: 'pickup' | 'destination';
  onSelect: (id: string) => void;
  now: number;
}) {
  const known = freshness(item, now) === 'fresh' && !item.is_demo;
  const count = purpose === 'pickup' ? item.bikes : item.docks;
  return (
    <article className={`recommendation-card ${item.eligibility_tier ? 'caution-card' : ''}`}>
      <div className="recommendation-title">
        <span className="rank-number">{index + 1}</span>
        <button className="station-name-button" onClick={() => onSelect(item.external_id)}>
          {item.name}
          <Icon name="arrow" size={16} />
        </button>
      </div>
      <div className="recommendation-facts">
        <span>
          <Icon name="walk" size={15} />~{Math.round(item.walking_meters)} m
        </span>
        <span>
          <Icon name={purpose === 'pickup' ? 'bike' : 'dock'} size={15} />
          {known ? count : 'Unknown'} {purpose === 'pickup' ? 'bikes' : 'docks'}
        </span>
      </div>
      <span className={`tag ${item.mode === 'distance_only' ? 'tag-stale' : ''}`}>
        {item.mode === 'distance_only'
          ? 'Distance only'
          : item.mode === 'live_inventory_only'
            ? 'Measured inventory ranking'
            : 'Estimated inventory scenario'}
      </span>
      {item.estimated_usable_inventory !== null && (
        <p className="small-copy">
          ~{item.estimated_usable_inventory.toFixed(1)} usable{' '}
          {purpose === 'pickup' ? 'bikes' : 'docks'} estimated{' '}
          {item.estimate_minutes_ahead.toFixed(0)} min ahead. Not a guarantee.
        </p>
      )}
      {item.eligibility_tier !== 0 && (
        <p className="warning-text">
          {item.eligibility_tier === 2
            ? 'Currently empty or closed for this operation.'
            : 'Inventory is unknown. Check before relying on this option.'}
        </p>
      )}
      <details>
        <summary>Why this station?</summary>
        <ul>
          {item.reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      </details>
    </article>
  );
}

export function CommutePlanner({
  stations,
  origin,
  destination,
  onOrigin,
  onDestination,
  onPick,
  result,
  onResult,
  onSelect,
  now,
  presetPreferences,
}: {
  stations: Station[];
  origin?: Place;
  destination?: Place;
  onOrigin: (place?: Place) => void;
  onDestination: (place?: Place) => void;
  onPick: (mode: 'origin' | 'destination') => void;
  result?: TripRecommendations;
  onResult: (result?: TripRecommendations) => void;
  onSelect: (id: string) => void;
  now: number;
  presetPreferences?: import('../lib/types').Preferences;
}) {
  const auth = useAuth();
  const [preferences, setPreferences] = useState(auth.preferences);
  const [departure, setDeparture] = useState('0');
  const [customDate, setCustomDate] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const controller = useRef<AbortController>();
  useEffect(() => {
    setPreferences(presetPreferences ?? auth.preferences);
  }, [auth.preferences, presetPreferences]);
  useEffect(() => () => controller.current?.abort(), []);
  useEffect(() => {
    controller.current?.abort();
    setBusy(false);
    onResult(undefined);
  }, [origin, destination, onResult]);
  function invalidate() {
    controller.current?.abort();
    setBusy(false);
    onResult(undefined);
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(undefined);
    if (!origin || !destination) {
      setError(new Error('Choose both an origin and a destination.'));
      return;
    }
    const at =
      departure === 'custom'
        ? new Date(customDate)
        : new Date(Date.now() + Number(departure) * 60000);
    if (
      !Number.isFinite(at.getTime()) ||
      at.getTime() < Date.now() - 30000 ||
      at.getTime() > Date.now() + 3600000
    ) {
      setError(new Error('Choose a bike departure time between now and one hour ahead.'));
      return;
    }
    controller.current?.abort();
    const pending = new AbortController();
    controller.current = pending;
    setBusy(true);
    try {
      const response = await api.recommend(
        {
          origin: { latitude: origin.latitude, longitude: origin.longitude },
          destination: { latitude: destination.latitude, longitude: destination.longitude },
          departure_time: at.toISOString(),
          preferences,
          limit: 3,
        },
        pending.signal,
      );
      if (!pending.signal.aborted) onResult(response);
    } catch (cause) {
      if (!pending.signal.aborted) setError(cause);
    } finally {
      if (!pending.signal.aborted) setBusy(false);
    }
  }
  return (
    <div className="commute-planner">
      <p className="small-copy muted">
        Two points. Better options. Choose station landmarks, tap the map, or enter approximate
        coordinates.
      </p>
      <form onSubmit={submit} className="form-stack">
        <PlacePicker
          label="Origin"
          value={origin}
          stations={stations}
          onChange={onOrigin}
          onMap={() => onPick('origin')}
        />
        <button
          type="button"
          className="swap-button"
          aria-label="Swap origin and destination"
          onClick={() => {
            onOrigin(destination);
            onDestination(origin);
          }}
        >
          <Icon name="swap" size={16} />
        </button>
        <PlacePicker
          label="Destination"
          value={destination}
          stations={stations}
          onChange={onDestination}
          onMap={() => onPick('destination')}
        />
        <label>
          Bike departure time
          <select
            value={departure}
            onChange={(event) => {
              setDeparture(event.target.value);
              invalidate();
            }}
          >
            <option value="0">Now</option>
            <option value="15">In 15 minutes</option>
            <option value="30">In 30 minutes</option>
            <option value="45">In 45 minutes</option>
            <option value="custom">Choose a time</option>
          </select>
        </label>
        {departure === 'custom' && (
          <label>
            Departure in your device’s time zone
            <input
              type="datetime-local"
              required
              value={customDate}
              onChange={(event) => {
                setCustomDate(event.target.value);
                invalidate();
              }}
            />
          </label>
        )}
        <p className="fine-print">
          Allow time to walk before bike departure. Results use approximate distances, not verified
          pedestrian or cycling routes.
        </p>
        <details className="planner-preferences">
          <summary>Walking & ranking preferences</summary>
          <label>
            Maximum approximate walk <strong>{preferences.max_walk_meters} m</strong>
            <input
              type="range"
              min="100"
              max="3000"
              step="100"
              value={preferences.max_walk_meters}
              onChange={(event) => {
                setPreferences({ ...preferences, max_walk_meters: Number(event.target.value) });
                invalidate();
              }}
            />
          </label>
          <label>
            Prioritize
            <select
              value={preferences.reliability_weight}
              onChange={(event) => {
                setPreferences({ ...preferences, reliability_weight: Number(event.target.value) });
                invalidate();
              }}
            >
              <option value="0.3">Shorter walks</option>
              <option value="0.7">Balanced, favor availability</option>
              <option value="1">Availability first</option>
              {![0.3, 0.7, 1].includes(preferences.reliability_weight) && (
                <option value={preferences.reliability_weight}>
                  Custom availability weight ({Math.round(preferences.reliability_weight * 100)}%)
                </option>
              )}
            </select>
          </label>
          <p className="fine-print">
            {auth.user ? (
              'Based on your account defaults. Changes here apply to this plan only.'
            ) : (
              <>
                <Link to="/account">Sign in</Link> to save your defaults.
              </>
            )}
          </p>
        </details>
        {error !== undefined && <ErrorNotice error={error} />}
        <button className="primary-button" disabled={busy || !origin || !destination}>
          {busy ? 'Finding your options…' : result ? 'Refresh recommendations' : 'Find my ride'}
          <Icon name="arrow" size={18} />
        </button>
      </form>
      {origin && destination && auth.user && (
        <Link
          className="save-commute-link secondary-button"
          to="/commutes"
          state={{ draft: { origin, destination, preferences }, owner: auth.user.id }}
        >
          Save this commute
        </Link>
      )}
      {result && (
        <section className="trip-results" aria-label="Trip recommendations" aria-live="polite">
          <div className="section-heading">
            <h2>Your ride, considered.</h2>
            <span className="tag">Not a guarantee</span>
          </div>
          <p className="fine-print">
            Bike departure {time(result.departure_time)} · checked {time(result.generated_at)}.
          </p>
          {now - Date.parse(result.generated_at) > 60000 && (
            <p className="notice">These options are over a minute old. Refresh before you leave.</p>
          )}
          <div className="journey-estimate">
            <Icon name="bike" />
            <div>
              <strong>~{result.approximate_cycling_minutes.toFixed(0)} min cycling</strong>
              <span>Distance-based heuristic, not routing. Walking is additional.</span>
            </div>
          </div>
          <p className="notice">
            {result.pickup.some((item) => item.mode === 'uncalibrated_inventory_scenario') ||
            result.destination.some((item) => item.mode === 'uncalibrated_inventory_scenario')
              ? 'Forecast scenarios inform this ranking. Probabilities are not calibrated to actual stockouts.'
              : 'Forecast unavailable or low confidence: ranked using fresh measured inventory where available, otherwise approximate distance.'}
          </p>
          {(['pickup', 'destination'] as const).map((purpose) => (
            <div key={purpose}>
              <h3 className="result-group-title">
                <span className={`point-letter point-${purpose}`}>
                  {purpose === 'pickup' ? 'A' : 'B'}
                </span>
                {purpose === 'pickup' ? 'Pick up a bike' : 'Find an open dock'}
              </h3>
              {result[purpose].length ? (
                result[purpose].map((item, index) => (
                  <RecommendationCard
                    key={item.external_id}
                    item={item}
                    index={index}
                    purpose={purpose}
                    onSelect={onSelect}
                    now={now}
                  />
                ))
              ) : (
                <p className="empty-state">
                  No stations within your approximate walking limit. Choose another point or
                  increase the limit.
                </p>
              )}
            </div>
          ))}
        </section>
      )}
    </div>
  );
}
