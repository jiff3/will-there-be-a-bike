import { ApiError } from '../lib/api';
import { message } from '../lib/presentation';
export function ErrorNotice({ error, retry }: { error: unknown; retry?: () => void }) {
  return (
    <div className="notice error-notice" role="alert">
      <span>
        {message(error)}
        {error instanceof ApiError &&
          error.details?.map((detail, index) => (
            <small key={index}>
              {detail.loc.filter((part) => part !== 'body').join(' › ')}: {detail.msg}
            </small>
          ))}
        {error instanceof ApiError && error.requestId && (
          <small>Reference: {error.requestId}</small>
        )}
      </span>
      {retry && (
        <button className="text-button" onClick={retry}>
          Try again
        </button>
      )}
    </div>
  );
}
export function Loading({ label = 'Loading station data…' }: { label?: string }) {
  return (
    <div className="loading-state" role="status">
      <span className="spinner" />
      {label}
    </div>
  );
}
