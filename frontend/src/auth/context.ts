import { createContext, useContext } from 'react';
import type { Preferences, User } from '../lib/types';

export const defaultPreferences: Preferences = { max_walk_meters: 1000, reliability_weight: 0.7 };
export interface AuthState {
  user: User | null;
  loading: boolean;
  error?: Error;
  preferences: Preferences;
  reload: () => void;
  authenticate: (mode: 'login' | 'register', email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  savePreferences: (value: Preferences) => Promise<void>;
  write: <T>(action: (csrf: string) => Promise<T>) => Promise<T>;
}
export const AuthContext = createContext<AuthState | null>(null);
export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error('AuthProvider is required');
  return context;
}
