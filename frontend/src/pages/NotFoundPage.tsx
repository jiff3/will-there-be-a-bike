import { Link } from 'react-router-dom';

export function NotFoundPage() {
  return (
    <section className="content-page not-found">
      <p className="eyebrow">404</p>
      <h1>That route went off course.</h1>
      <p>Return to the trip planner and try another direction.</p>
      <Link className="button-link" to="/">
        Back to the map
      </Link>
    </section>
  );
}
