import { useEffect, useState } from 'react';
import { api, type HealthResponse } from '../lib/api';

interface HealthState {
  data?: HealthResponse;
  state: 'checking' | 'healthy' | 'unavailable';
}

export function useHealth(): HealthState {
  const [health, setHealth] = useState<HealthState>({ state: 'checking' });

  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    api
      .health(controller.signal)
      .then((data) => {
        if (active) setHealth({ state: 'healthy', data });
      })
      .catch(() => {
        if (active) setHealth({ state: 'unavailable' });
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, []);

  return health;
}
