import { useState, type FormEvent } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useAuth } from '../auth/context';
import { ErrorNotice, Loading } from '../components/Feedback';
import { Icon } from '../components/Icon';
import type { Preferences } from '../lib/types';

function PreferenceForm() {
  const auth = useAuth();
  const [value, setValue] = useState<Preferences>(auth.preferences);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<unknown>();
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(undefined);
    setSaved(false);
    try {
      await auth.savePreferences(value);
      setSaved(true);
    } catch (cause) {
      setError(cause);
    } finally {
      setBusy(false);
    }
  }
  return (
    <form onSubmit={submit} className="form-stack">
      <h2>Your walking & ranking preferences</h2>
      <p className="muted">Applied to new trip plans. Saved privately to your account.</p>
      <label>
        Maximum approximate walk <strong>{value.max_walk_meters} m</strong>
        <input
          type="range"
          min="100"
          max="3000"
          step="100"
          value={value.max_walk_meters}
          onChange={(e) => {
            setSaved(false);
            setValue({ ...value, max_walk_meters: Number(e.target.value) });
          }}
        />
      </label>
      <label>
        Prioritize{' '}
        <select
          value={value.reliability_weight}
          onChange={(e) => {
            setSaved(false);
            setValue({ ...value, reliability_weight: Number(e.target.value) });
          }}
        >
          <option value="0.3">Shorter walks</option>
          <option value="0.7">Balanced, favor availability</option>
          <option value="1">Availability first</option>
          {![0.3, 0.7, 1].includes(value.reliability_weight) && (
            <option value={value.reliability_weight}>
              Custom availability weight ({Math.round(value.reliability_weight * 100)}%)
            </option>
          )}
        </select>
      </label>
      {error !== undefined && <ErrorNotice error={error} />}
      <button className="primary-button" disabled={busy}>
        {busy ? 'Saving…' : 'Save preferences'}
      </button>
      {saved && (
        <p className="success-text" role="status">
          <Icon name="check" size={16} /> Preferences saved
        </p>
      )}
    </form>
  );
}

export function AccountPage() {
  const auth = useAuth();
  const [params, setParams] = useSearchParams();
  const mode = params.get('mode') === 'register' ? 'register' : 'login';
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const navigate = useNavigate();
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(undefined);
    const form = event.currentTarget;
    const data = new FormData(form);
    try {
      await auth.authenticate(mode, String(data.get('email')), String(data.get('password')));
      form.reset();
      navigate('/account');
    } catch (cause) {
      setError(cause);
    } finally {
      setBusy(false);
    }
  }
  async function logout() {
    setBusy(true);
    setError(undefined);
    try {
      await auth.logout();
    } catch (cause) {
      setError(cause);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="account-page">
      <div className="account-intro">
        <p className="eyebrow">A little less uncertainty</p>
        <h1>
          Your city. <br />
          Your pace.
          <br />
          <em>Your next ride.</em>
        </h1>
        <p>
          Make bike share part of your everyday. Keep your walking and availability preferences in
          one place.
        </p>
        <div className="account-art" aria-hidden="true">
          <Icon name="bike" size={132} />
          <span>A better way to get there.</span>
        </div>
        <Link to="/" className="text-button">
          Explore stations <Icon name="arrow" size={16} />
        </Link>
      </div>
      <div className="account-card">
        {auth.loading ? (
          <Loading label="Checking your session…" />
        ) : auth.user ? (
          <>
            <p className="eyebrow">Your account</p>
            <h1 className="small-title">Good to see you.</h1>
            <p className="account-email">{auth.user.email}</p>
            {auth.error ? (
              <ErrorNotice error={auth.error} retry={auth.reload} />
            ) : (
              <PreferenceForm key={auth.user.id} />
            )}
            <div className="future-feature">
              <h3>Your regular rides</h3>
              <p>
                Save your regular places and set availability conditions for a quiet in-app
                heads-up.
              </p>
              <div className="button-row">
                <Link to="/commutes">Saved commutes</Link>
                <Link to="/notifications">Notification history</Link>
              </div>
            </div>
            {error !== undefined && <ErrorNotice error={error} />}
            <button className="secondary-button" disabled={busy} onClick={() => void logout()}>
              {busy ? 'Signing out…' : 'Sign out'}
            </button>
          </>
        ) : (
          <>
            <p className="eyebrow">Ride with a little more confidence</p>
            <h1 className="small-title">
              {mode === 'register' ? 'Make yourself at home.' : 'Welcome back.'}
            </h1>
            <p className="muted">
              {mode === 'register'
                ? 'Create an account to keep your planning preferences.'
                : 'Sign in to use your saved preferences.'}
            </p>
            {auth.error && <ErrorNotice error={auth.error} retry={auth.reload} />}
            <form onSubmit={submit} className="form-stack">
              <label>
                Email address
                <input
                  autoComplete="email"
                  type="email"
                  name="email"
                  required
                  maxLength={254}
                  placeholder="you@example.com"
                />
              </label>
              <label>
                Password
                <input
                  autoComplete={mode === 'register' ? 'new-password' : 'current-password'}
                  type="password"
                  name="password"
                  required
                  minLength={12}
                  maxLength={128}
                  aria-describedby="password-help"
                />
              </label>
              <small id="password-help">12–128 characters. Use a unique password.</small>
              {error !== undefined && <ErrorNotice error={error} />}
              <button className="primary-button" disabled={busy}>
                {busy ? 'One moment…' : mode === 'register' ? 'Create account' : 'Sign in'}
                <Icon name="arrow" size={18} />
              </button>
            </form>
            <p className="auth-switch">
              {mode === 'register' ? 'Already have an account?' : 'New around here?'}{' '}
              <button
                className="text-button"
                onClick={() => {
                  setError(undefined);
                  setParams({ mode: mode === 'register' ? 'login' : 'register' });
                }}
              >
                {mode === 'register' ? 'Sign in' : 'Create an account'}
              </button>
            </p>
            <p className="fine-print">
              Secure, HttpOnly session cookies. No passwords or session tokens in browser storage.
              Password recovery is not available yet.
            </p>
          </>
        )}
      </div>
    </section>
  );
}
