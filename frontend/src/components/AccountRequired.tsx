import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../auth/context';
import { ErrorNotice, Loading } from './Feedback';

export function AccountRequired({ children }: { children: ReactNode }) {
  const auth = useAuth();
  if (auth.loading) return <Loading label="Checking your account…" />;
  if (auth.error)
    return (
      <section className="content-page">
        <ErrorNotice error={auth.error} retry={auth.reload} />
      </section>
    );
  if (!auth.user)
    return (
      <section className="content-page">
        <p className="eyebrow">Your everyday rides</p>
        <h1>A little more personal.</h1>
        <p className="lede">
          Sign in to keep your commutes and availability alerts together. Your saved places are
          private to your account.
        </p>
        <Link className="primary-button" to="/account">
          Sign in or create an account
        </Link>
      </section>
    );
  return <div key={auth.user.id}>{children}</div>;
}
