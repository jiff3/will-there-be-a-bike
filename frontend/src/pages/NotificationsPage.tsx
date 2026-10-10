import { useCallback, useState } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../auth/context';
import { api } from '../lib/api';
import { useResource } from '../hooks/useResource';
import { AccountRequired } from '../components/AccountRequired';
import { ErrorNotice, Loading } from '../components/Feedback';

function InboxWorkspace() {
  const auth = useAuth();
  const [offset, setOffset] = useState(0);
  const [busy, setBusy] = useState<string>();
  const [error, setError] = useState<unknown>();
  const load = useCallback((signal: AbortSignal) => api.notifications(offset, signal), [offset]);
  const inbox = useResource(load, 60000);
  async function mark(id: string, read: boolean) {
    setBusy(id);
    setError(undefined);
    try {
      await auth.write((token) => api.readNotification(id, read, token));
      inbox.retry();
      window.dispatchEvent(new Event('notifications-changed'));
    } catch (cause) {
      setError(cause);
    } finally {
      setBusy(undefined);
    }
  }
  return (
    <section className="rider-page inbox-page">
      <div className="rider-page-heading">
        <div>
          <p className="eyebrow">A quiet heads-up</p>
          <h1>Your ride, on your radar.</h1>
          <p className="lede">
            Availability checks for the commutes you care about. In-app only; no email or push
            messages.
          </p>
        </div>
        <Link className="secondary-button" to="/commutes">
          Manage alerts
        </Link>
      </div>
      <div className="inbox-toolbar">
        <strong>{inbox.data?.unread_count ?? '—'} unread</strong>
        <button className="text-button" onClick={inbox.retry}>
          Refresh notifications
        </button>
      </div>
      {inbox.loading && <Loading label="Loading notifications…" />}
      {inbox.error && <ErrorNotice error={inbox.error} retry={inbox.retry} />}{' '}
      {error !== undefined && <ErrorNotice error={error} />}
      {inbox.data?.items.length === 0 && (
        <div className="rider-empty">
          <h2>Nothing needs your attention.</h2>
          <p>
            {offset
              ? 'No more notifications on this page.'
              : 'Create an alert from a saved commute. Notifications appear only when a valid condition is met during its scheduled window.'}
          </p>
          <p className="fine-print">
            Silence is not a guarantee: stale feeds, closed stations and low-confidence models do
            not trigger availability conditions. Rule status is shown with each saved commute.
          </p>
          <Link to="/commutes" className="secondary-button">
            Go to saved commutes
          </Link>
        </div>
      )}
      <div className="notification-list">
        {inbox.data?.items.map((item) => (
          <article
            key={item.id}
            className={`rider-card notification ${item.read_at ? 'is-read' : 'is-unread'}`}
          >
            <div className="section-heading">
              <span
                className={`tag ${item.evidence.mode === 'estimated_risk' ? 'tag-forecast' : ''}`}
              >
                {item.evidence.mode === 'estimated_risk'
                  ? 'Estimated scenario'
                  : 'Measured inventory'}
              </span>
              {!item.read_at && <span className="unread-dot" aria-label="Unread" />}
            </div>
            <h2>{item.title}</h2>
            <p>{item.message}</p>
            <p className="fine-print">
              Notified {new Date(item.created_at).toLocaleString()} · scheduled commute{' '}
              {new Date(item.scheduled_for).toLocaleString()}. Observed{' '}
              {new Date(item.evidence.reported_at).toLocaleString()}.
            </p>
            {item.evidence.model_version && (
              <p className="fine-print">
                Model {item.evidence.model_version} · issuance{' '}
                {item.evidence.issuance
                  ? new Date(item.evidence.issuance).toLocaleString()
                  : 'unavailable'}
                .
              </p>
            )}
            <div className="button-row">
              <Link
                className="text-button"
                to={`/?station=${encodeURIComponent(item.evidence.station_id)}`}
              >
                Check station now
              </Link>
              <button
                className="secondary-button"
                disabled={busy === item.id}
                onClick={() => void mark(item.id, !item.read_at)}
              >
                {busy === item.id ? 'Updating…' : item.read_at ? 'Mark unread' : 'Mark read'}
              </button>
            </div>
          </article>
        ))}
      </div>
      <div className="button-row pagination">
        <button
          className="secondary-button"
          disabled={offset === 0 || inbox.loading}
          onClick={() => setOffset((value) => Math.max(0, value - 20))}
        >
          Previous page
        </button>
        <span>Page {offset / 20 + 1}</span>
        <button
          className="secondary-button"
          disabled={inbox.loading || inbox.data?.next_offset == null}
          onClick={() => setOffset(inbox.data!.next_offset!)}
        >
          Next page
        </button>
      </div>
      <p className="fine-print">
        History is retained for 90 days. Removing a commute stops its alerts but does not erase
        earlier notifications immediately.
      </p>
    </section>
  );
}
export function NotificationsPage() {
  return (
    <AccountRequired>
      <InboxWorkspace />
    </AccountRequired>
  );
}
