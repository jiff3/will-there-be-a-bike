import { useCallback, useState, type FormEvent } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '../auth/context';
import { api } from '../lib/api';
import type {
  AlertInput,
  AvailabilityAlert,
  CommuteInput,
  Place,
  SavedCommute,
} from '../lib/types';
import { useClock, useResource } from '../hooks/useResource';
import { AccountRequired } from '../components/AccountRequired';
import { PlacePicker, RecommendationCard } from '../components/CommutePlanner';
import { ErrorNotice, Loading } from '../components/Feedback';
import { time } from '../lib/presentation';

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const emptyStations: [] = [];

function CommuteForm({
  value,
  onSaved,
  onCancel,
}: {
  value?: Partial<CommuteInput> & { id?: string };
  onSaved: () => void;
  onCancel: () => void;
}) {
  const auth = useAuth();
  const [origin, setOrigin] = useState<Place | undefined>(value?.origin);
  const [destination, setDestination] = useState<Place | undefined>(value?.destination);
  const [days, setDays] = useState(value?.weekdays ?? [1, 2, 3, 4, 5]);
  const [walk, setWalk] = useState(
    value?.preferences?.max_walk_meters ?? auth.preferences.max_walk_meters,
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!origin || !destination || !days.length) {
      setError(new Error('Choose both places and at least one day.'));
      return;
    }
    const fields = new FormData(event.currentTarget);
    const body: CommuteInput = {
      name: String(fields.get('name')),
      origin,
      destination,
      departure_local: String(fields.get('departure')),
      timezone: String(fields.get('timezone')),
      weekdays: days,
      preferences: {
        max_walk_meters: walk,
        reliability_weight:
          value?.preferences?.reliability_weight ?? auth.preferences.reliability_weight,
      },
    };
    setBusy(true);
    setError(undefined);
    try {
      await auth.write((token) => api.saveCommute(body, token, value?.id));
      onSaved();
    } catch (cause) {
      setError(cause);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section
      className="rider-card commute-editor"
      aria-label={value?.id ? 'Edit saved commute' : 'New saved commute'}
    >
      <h2>{value?.id ? 'Make it yours.' : 'A familiar way there.'}</h2>
      <form className="form-stack" onSubmit={submit}>
        <label>
          Commute name
          <input
            name="name"
            required
            maxLength={80}
            defaultValue={value?.name}
            placeholder="The morning ride"
            autoFocus
          />
        </label>
        <div className="place-grid">
          <PlacePicker
            label="Origin"
            value={origin}
            onChange={setOrigin}
            stations={emptyStations}
          />
          <PlacePicker
            label="Destination"
            value={destination}
            onChange={setDestination}
            stations={emptyStations}
          />
        </div>
        <p className="fine-print">
          Search actual station landmarks or enter approximate coordinates. You can also select both
          points in the map planner, then choose “Save this commute”.
        </p>
        <div className="form-grid">
          <label>
            Preferred bike departure
            <input
              type="time"
              name="departure"
              required
              defaultValue={value?.departure_local ?? '08:30'}
            />
          </label>
          <label>
            Time zone
            <input name="timezone" required defaultValue={value?.timezone ?? 'America/New_York'} />
          </label>
        </div>
        <fieldset className="weekday-picker">
          <legend>Commute days</legend>
          {DAYS.map((day, index) => (
            <label key={day}>
              <input
                type="checkbox"
                checked={days.includes(index + 1)}
                onChange={(event) =>
                  setDays(
                    event.target.checked
                      ? [...days, index + 1]
                      : days.filter((item) => item !== index + 1),
                  )
                }
              />
              <span>{day}</span>
            </label>
          ))}
        </fieldset>
        <label>
          Maximum approximate walk <strong>{walk} m</strong>
          <input
            type="range"
            min="100"
            max="3000"
            step="100"
            value={walk}
            onChange={(event) => setWalk(Number(event.target.value))}
          />
        </label>
        {error !== undefined && <ErrorNotice error={error} />}
        <div className="button-row">
          <button className="primary-button" disabled={busy || !origin || !destination}>
            {busy ? 'Saving…' : 'Save commute'}
          </button>
          <button type="button" className="secondary-button" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
        </div>
      </form>
    </section>
  );
}

function AlertForm({
  commute,
  value,
  onSaved,
  onCancel,
}: {
  commute: SavedCommute;
  value?: AvailabilityAlert;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const auth = useAuth();
  const [place, setPlace] = useState<Place | undefined>(
    value
      ? {
          label: value.station_name,
          latitude: value.latitude,
          longitude: value.longitude,
          station_id: value.station_id,
        }
      : undefined,
  );
  const [mode, setMode] = useState<AlertInput['mode']>(value?.mode ?? 'inventory');
  const [threshold, setThreshold] = useState(value?.threshold ?? 3);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(undefined);
    try {
      const fields = new FormData(event.currentTarget);
      if (!place?.station_id) throw new Error('Select a real station from the search results.');
      const body: AlertInput = {
        station_id: place.station_id,
        mode,
        threshold,
        kind: String(fields.get('kind')) as AlertInput['kind'],
        lead_minutes: Number(fields.get('lead')) as 30 | 60,
        cooldown_minutes: Number(fields.get('cooldown')),
        enabled: value?.enabled ?? true,
      };
      await auth.write((token) => api.saveAlert(commute.id, body, token, value?.id));
      onSaved();
    } catch (cause) {
      setError(cause);
    } finally {
      setBusy(false);
    }
  }
  return (
    <form
      className="form-stack alert-editor"
      onSubmit={submit}
      aria-label="Availability alert settings"
    >
      <h3>{value ? 'Edit availability alert' : 'Keep an eye on a station'}</h3>
      <p className="notice">
        Alerts require real station measurements. Synthetic offline stations cannot trigger alerts.
      </p>
      <PlacePicker
        label="Alert station"
        value={place}
        onChange={setPlace}
        stations={emptyStations}
        stationOnly
      />
      <label>
        Watch for
        <select name="kind" defaultValue={value?.kind ?? 'pickup_empty'}>
          <option value="pickup_empty">Low bikes at pickup</option>
          <option value="destination_full">Low docks at destination</option>
        </select>
      </label>
      <label>
        Condition
        <select
          value={mode}
          onChange={(event) => {
            const next = event.target.value as AlertInput['mode'];
            setMode(next);
            setThreshold(next === 'inventory' ? 3 : 50);
          }}
        >
          <option value="inventory">Measured inventory at or below threshold</option>
          <option value="estimated_risk">Estimated boundary risk at or above threshold</option>
        </select>
      </label>
      <label>
        {mode === 'inventory'
          ? 'Bikes / open docks threshold'
          : 'Uncalibrated scenario threshold (%)'}
        <input
          type="number"
          required
          min="0"
          max="100"
          step={mode === 'inventory' ? '1' : '0.1'}
          value={threshold}
          onChange={(event) => setThreshold(Number(event.target.value))}
        />
      </label>
      <div className="form-grid">
        <label>
          Check before departure
          <select name="lead" defaultValue={value?.lead_minutes ?? 30}>
            <option value="30">Within 30 minutes</option>
            <option value="60">Within 60 minutes</option>
          </select>
        </label>
        <label>
          Cooldown (minutes)
          <input
            name="cooldown"
            type="number"
            min="15"
            max="1440"
            required
            defaultValue={value?.cooldown_minutes ?? 60}
          />
        </label>
      </div>
      <p className="notice">
        {mode === 'estimated_risk'
          ? 'Risk is an uncalibrated model scenario, not a validated stockout probability. Low-confidence or missing models do not trigger this condition. The forecast window begins at issuance, not exactly at your commute.'
          : 'This watches fresh measured bikes/docks during your commute window. It does not predict future inventory.'}{' '}
        Destination checks use the same pre-departure window, not a routed arrival time.
      </p>
      {error !== undefined && <ErrorNotice error={error} />}
      <div className="button-row">
        <button className="primary-button" disabled={busy || !place}>
          {busy ? 'Saving…' : 'Save alert'}
        </button>
        <button type="button" className="secondary-button" onClick={onCancel} disabled={busy}>
          Cancel alert
        </button>
      </div>
    </form>
  );
}

function CommuteCard({
  commute,
  onEdit,
  onDeleted,
}: {
  commute: SavedCommute;
  onEdit: () => void;
  onDeleted: () => void;
}) {
  const auth = useAuth();
  const navigate = useNavigate();
  const now = useClock();
  const [check, setCheck] = useState(false);
  const [showAlerts, setShowAlerts] = useState(false);
  const [editing, setEditing] = useState<AvailabilityAlert | null | undefined>();
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const load = useCallback(
    (signal: AbortSignal) =>
      check ? api.commuteRecommendations(commute.id, signal) : Promise.resolve(undefined),
    [check, commute.id],
  );
  const recommendations = useResource(load);
  const loadAlerts = useCallback(
    (signal: AbortSignal) =>
      showAlerts ? api.alerts(commute.id, signal) : Promise.resolve(undefined),
    [showAlerts, commute.id],
  );
  const alerts = useResource(loadAlerts, 60000);
  async function remove() {
    setBusy(true);
    try {
      await auth.write((token) => api.deleteCommute(commute.id, token));
      onDeleted();
    } catch (cause) {
      setError(cause);
    } finally {
      setBusy(false);
    }
  }
  async function changeAlert(item: AvailabilityAlert, remove = false) {
    setBusy(true);
    setError(undefined);
    try {
      if (remove) await auth.write((token) => api.deleteAlert(commute.id, item.id, token));
      else {
        const { station_id, kind, mode, threshold, lead_minutes, cooldown_minutes, enabled } = item;
        await auth.write((token) =>
          api.saveAlert(
            commute.id,
            {
              station_id,
              kind,
              mode,
              threshold,
              lead_minutes,
              cooldown_minutes,
              enabled: !enabled,
            },
            token,
            item.id,
          ),
        );
      }
      alerts.retry();
    } catch (cause) {
      setError(cause);
    } finally {
      setBusy(false);
    }
  }
  return (
    <article className="rider-card commute-card">
      <p className="eyebrow">
        {commute.departure_local} · {commute.timezone}
      </p>
      <h2>{commute.name}</h2>
      <div className="commute-endpoints">
        <p>
          <span className="point-letter">A</span>
          {commute.origin.label}
        </p>
        <p>
          <span className="point-letter point-destination">B</span>
          {commute.destination.label}
        </p>
      </div>
      <p className="small-copy muted">
        {commute.weekdays.map((day) => DAYS[day - 1]).join(' · ')} · up to ~
        {commute.preferences.max_walk_meters} m walking
      </p>
      <p className="fine-print">
        Next departure:{' '}
        {new Intl.DateTimeFormat(undefined, {
          dateStyle: 'medium',
          timeStyle: 'short',
          timeZone: commute.timezone,
        }).format(new Date(commute.next_departure))}{' '}
        ({commute.timezone})
      </p>
      <div className="button-row">
        <button
          className="primary-button"
          onClick={() => {
            setCheck(true);
            if (check) recommendations.retry();
          }}
        >
          Check recommendations now
        </button>
        <button
          className="secondary-button"
          aria-expanded={showAlerts}
          onClick={() => setShowAlerts((value) => !value)}
        >
          Availability alerts
        </button>
      </div>
      <div className="button-row">
        <Link className="text-button" to={`/?commute=${commute.id}`}>
          Open in map planner
        </Link>
        <button className="text-button" onClick={onEdit}>
          Edit commute
        </button>
        <button className="text-button danger-text" onClick={() => setConfirm(true)}>
          Delete commute
        </button>
      </div>
      {confirm && (
        <div className="notice" role="group" aria-label="Confirm commute deletion">
          <p>
            Delete this commute and its alert rules? Notification history remains for up to 90 days.
          </p>
          <button className="secondary-button" disabled={busy} onClick={() => void remove()}>
            Yes, delete commute
          </button>
          <button className="text-button" onClick={() => setConfirm(false)}>
            Keep commute
          </button>
        </div>
      )}
      {error !== undefined && <ErrorNotice error={error} />}
      {check && recommendations.loading && <Loading label="Checking current recommendations…" />}
      {recommendations.error && (
        <ErrorNotice error={recommendations.error} retry={recommendations.retry} />
      )}
      {recommendations.data && (
        <section
          className="saved-recommendations"
          aria-label={`Current recommendations for ${commute.name}`}
        >
          <p className="notice">
            For departure now, checked {time(recommendations.data.generated_at)}. Not a forecast for
            your next scheduled day. Approximate distances, not verified routes.
          </p>
          {(['pickup', 'destination'] as const).map((purpose) => (
            <div key={purpose}>
              <h3>{purpose === 'pickup' ? 'Pick up a bike' : 'Find an open dock'}</h3>
              {recommendations.data![purpose].length ? (
                recommendations.data![purpose].map((item, index) => (
                  <RecommendationCard
                    key={item.external_id}
                    item={item}
                    index={index}
                    purpose={purpose}
                    now={now}
                    onSelect={(id) => navigate(`/?station=${encodeURIComponent(id)}`)}
                  />
                ))
              ) : (
                <p>No station within the saved walking limit.</p>
              )}
            </div>
          ))}
        </section>
      )}
      {showAlerts && (
        <section className="saved-alerts" aria-label={`Alerts for ${commute.name}`}>
          <h3>A heads-up before you leave.</h3>
          <p className="fine-print">
            In-app only. Checked by the scheduled job during selected commute days, with cooldown
            and at most one notification per rule per departure. No email, SMS or browser push.
          </p>
          {alerts.loading && <Loading label="Loading alert rules…" />}
          {alerts.error && <ErrorNotice error={alerts.error} retry={alerts.retry} />}
          {alerts.data?.items.map((item) => (
            <div className="alert-rule" key={item.id}>
              <strong>{item.station_name}</strong>
              <p>
                {item.kind === 'pickup_empty' ? 'Pickup bikes' : 'Destination docks'} ·{' '}
                {item.mode === 'inventory'
                  ? `≤ ${item.threshold} measured`
                  : `≥ ${item.threshold}% estimated scenario`}
              </p>
              <p className="fine-print">
                {item.enabled ? 'Enabled' : 'Paused'} · {item.last_status.replaceAll('_', ' ')}
                {item.last_checked_at ? ` · checked ${time(item.last_checked_at)}` : ''}
              </p>
              <div className="button-row">
                <button className="text-button" onClick={() => setEditing(item)}>
                  Edit alert
                </button>
                <button
                  className="text-button"
                  disabled={busy}
                  onClick={() => void changeAlert(item)}
                >
                  {item.enabled ? 'Pause alert' : 'Enable alert'}
                </button>
                <button
                  className="text-button danger-text"
                  disabled={busy}
                  onClick={() => void changeAlert(item, true)}
                >
                  Remove alert
                </button>
              </div>
            </div>
          ))}
          {alerts.data?.items.length === 0 && (
            <p className="muted">
              No alerts yet. Add a measured-inventory condition or an estimated risk condition.
            </p>
          )}
          {editing !== undefined ? (
            <AlertForm
              key={editing?.id ?? 'new'}
              commute={commute}
              value={editing ?? undefined}
              onSaved={() => {
                setEditing(undefined);
                alerts.retry();
              }}
              onCancel={() => setEditing(undefined)}
            />
          ) : (
            <button
              className="secondary-button"
              disabled={(alerts.data?.items.length ?? 0) >= 4}
              onClick={() => setEditing(null)}
            >
              Add availability alert
            </button>
          )}
        </section>
      )}
    </article>
  );
}

function CommutesWorkspace() {
  const auth = useAuth();
  const location = useLocation();
  const routeDraft = location.state as { draft?: Partial<CommuteInput>; owner?: string } | null;
  const [draft, setDraft] = useState(
    routeDraft?.owner === auth.user?.id ? routeDraft?.draft : undefined,
  );
  const [editing, setEditing] = useState<SavedCommute | null | undefined>(draft ? null : undefined);
  const load = useCallback((signal: AbortSignal) => api.commutes(signal), []);
  const commutes = useResource(load);
  return (
    <section className="rider-page">
      <div className="rider-page-heading">
        <div>
          <p className="eyebrow">Your everyday rides</p>
          <h1>Make the familiar easier.</h1>
          <p className="lede">
            Your places, your pace. Check today’s options or set a quiet heads-up before you leave.
          </p>
        </div>
        <button
          className="primary-button"
          disabled={editing !== undefined}
          onClick={() => setEditing(null)}
        >
          New commute
        </button>
      </div>
      {editing !== undefined && (
        <CommuteForm
          key={editing?.id ?? 'new'}
          value={editing ?? draft}
          onSaved={() => {
            setDraft(undefined);
            setEditing(undefined);
            commutes.retry();
          }}
          onCancel={() => {
            setDraft(undefined);
            setEditing(undefined);
          }}
        />
      )}
      {commutes.loading && <Loading label="Loading your commutes…" />}
      {commutes.error && <ErrorNotice error={commutes.error} retry={commutes.retry} />}
      {commutes.data?.items.length === 0 && editing === undefined && (
        <div className="rider-empty">
          <h2>Your regular ride belongs here.</h2>
          <p>Save an origin and destination to get back to useful options in one click.</p>
          <button className="secondary-button" onClick={() => setEditing(null)}>
            Save your first commute
          </button>
        </div>
      )}
      <div className="commute-grid">
        {commutes.data?.items.map((item) => (
          <CommuteCard
            key={item.id + item.updated_at}
            commute={item}
            onEdit={() => {
              setEditing(item);
              window.scrollTo({ top: 0, behavior: 'smooth' });
            }}
            onDeleted={commutes.retry}
          />
        ))}
      </div>
    </section>
  );
}
export function CommutesPage() {
  return (
    <AccountRequired>
      <CommutesWorkspace />
    </AccountRequired>
  );
}
