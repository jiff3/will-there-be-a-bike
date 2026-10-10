import { StatusBadge } from '../components/StatusBadge';
import { useHealth } from '../hooks/useHealth';

export function StatusPage() {
  const health = useHealth();
  const label =
    health.state === 'checking'
      ? 'Checking'
      : health.state === 'healthy'
        ? 'Operational'
        : 'Unavailable';

  return (
    <section className="content-page status-page">
      <p className="eyebrow">Service health</p>
      <h1>System status</h1>
      <p className="lede">Live readiness from the API and its PostgreSQL dependency.</p>
      <p className="notice">
        Readiness does not certify feed freshness or model validity. Check station timestamps and
        forecast notices separately.
      </p>
      <div className="status-card">
        <div>
          <strong>Application API</strong>
          <p>
            {health.data
              ? `${health.data.service} · v${health.data.version}`
              : 'API and database readiness'}
          </p>
        </div>
        <StatusBadge state={health.state}>{label}</StatusBadge>
      </div>
    </section>
  );
}
