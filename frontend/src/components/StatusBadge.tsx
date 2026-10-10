interface StatusBadgeProps {
  state: 'checking' | 'healthy' | 'unavailable';
  children: React.ReactNode;
}

export function StatusBadge({ state, children }: StatusBadgeProps) {
  return (
    <span className={`status-badge status-${state}`}>
      <i aria-hidden="true" />
      {children}
    </span>
  );
}
