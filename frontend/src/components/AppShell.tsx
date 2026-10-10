import { NavLink, Outlet } from 'react-router-dom';
import { useAuth } from '../auth/context';
import { Icon } from './Icon';
import { useCallback, useEffect } from 'react';
import { api } from '../lib/api';
import { useResource } from '../hooks/useResource';

export function AppShell() {
  const auth = useAuth();
  const userId = auth.user?.id;
  const load = useCallback(
    async (signal: AbortSignal) =>
      userId ? { owner: userId, inbox: await api.notifications(0, signal) } : undefined,
    [userId],
  );
  const inbox = useResource(load, 60000);
  useEffect(() => {
    window.addEventListener('notifications-changed', inbox.retry);
    return () => window.removeEventListener('notifications-changed', inbox.retry);
  }, [inbox.retry]);
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Skip to main content
      </a>
      <header className="site-header">
        <NavLink to="/" className="brand" aria-label="Will There Be a Bike home">
          <span className="brand-mark" aria-hidden="true">
            <Icon name="bike" size={29} />
          </span>
          <span>
            <strong>Will There Be a Bike?</strong>
            <small>
              WHEN I GET THERE? <span>NEW YORK CITY</span>
            </small>
          </span>
        </NavLink>
        <nav aria-label="Primary navigation">
          <NavLink to="/" end>
            Explore & plan
          </NavLink>
          <NavLink to="/status">System status</NavLink>
          <NavLink to="/commutes">Saved commutes</NavLink>
          <NavLink to="/notifications">
            Notifications
            {userId && inbox.data?.owner === userId && inbox.data.inbox.unread_count > 0 && (
              <span className="unread-count" aria-label={`${inbox.data.inbox.unread_count} unread`}>
                {inbox.data.inbox.unread_count}
              </span>
            )}
          </NavLink>
          <NavLink to="/replay">Replay</NavLink>
          <NavLink to="/account" className="account-link">
            <Icon name="user" size={16} />
            {auth.user ? 'My account' : 'Sign in'}
          </NavLink>
        </nav>
      </header>
      <main id="main-content" tabIndex={-1}>
        <Outlet />
      </main>
      <footer>
        <span>Less guessing. More getting there.</span>
        <span>
          Independent project · <NavLink to="/status">Data & system status</NavLink>
        </span>
      </footer>
    </div>
  );
}
