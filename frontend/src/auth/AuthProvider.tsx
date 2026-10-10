import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { api, ApiError } from '../lib/api';
import type { Preferences, User } from '../lib/types';
import { AuthContext, defaultPreferences } from './context';

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [preferences, setPreferences] = useState(defaultPreferences);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error>();
  const [revision, setRevision] = useState(0);
  const csrf = useRef<string>(); // Never persisted to browser storage.
  const generation = useRef(0);
  const reload = useCallback(() => setRevision((value) => value + 1), []);
  useEffect(() => {
    const controller = new AbortController();
    const current = ++generation.current;
    setLoading(true);
    setError(undefined);
    api
      .me(controller.signal)
      .then(async (value) => {
        const saved = await api.preferences(controller.signal);
        if (current === generation.current && !controller.signal.aborted) {
          setUser(value);
          setPreferences(saved);
        }
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return;
        if (cause instanceof ApiError && cause.status === 401) {
          setUser(null);
          csrf.current = undefined;
        } else setError(cause instanceof Error ? cause : new Error('Account service unavailable'));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => {
      controller.abort();
    };
  }, [revision]);
  async function authenticate(mode: 'login' | 'register', email: string, password: string) {
    const session = await api[mode](email, password);
    ++generation.current;
    csrf.current = session.csrf_token;
    // Publish user and defaults together so the preference form cannot capture
    // guest defaults while the account's persisted preferences are still loading.
    let saved = defaultPreferences;
    let preferenceError: Error | undefined;
    try {
      saved = await api.preferences();
    } catch {
      preferenceError = new Error(
        'Signed in, but saved preferences could not be loaded. Retry before editing them.',
      );
    }
    setPreferences(saved);
    setUser(session.user);
    setError(preferenceError);
    setLoading(false);
  }
  async function protectedWrite<T>(action: (token: string) => Promise<T>): Promise<T> {
    try {
      if (!csrf.current) csrf.current = (await api.csrf()).csrf_token;
      try {
        return await action(csrf.current);
      } catch (cause) {
        if (!(cause instanceof ApiError && cause.code === 'csrf_rejected')) throw cause;
        // Another tab may have rotated the token; retry only a rejected CSRF write.
        csrf.current = (await api.csrf()).csrf_token;
        return await action(csrf.current);
      }
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 401) {
        setUser(null);
        csrf.current = undefined;
      }
      throw cause;
    }
  }
  async function logout() {
    await protectedWrite(api.logout);
    setUser(null);
    csrf.current = undefined;
    setPreferences(defaultPreferences);
  }
  async function savePreferences(value: Preferences) {
    setPreferences(await protectedWrite((token) => api.savePreferences(value, token)));
  }
  return (
    <AuthContext.Provider
      value={{
        user,
        preferences,
        loading,
        error,
        reload,
        authenticate,
        logout,
        savePreferences,
        write: protectedWrite,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}
