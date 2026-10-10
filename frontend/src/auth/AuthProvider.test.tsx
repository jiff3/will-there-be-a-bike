import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { vi } from 'vitest';
import { App } from '../App';
import { api, ApiError } from '../lib/api';

const preferences = { max_walk_meters: 1000, reliability_weight: 0.7 };
const user = { id: 'test-user-only', email: 'rider@example.test' };
beforeEach(() => {
  vi.spyOn(api, 'me').mockRejectedValue(
    new ApiError('Sign in required', 401, 'authentication_required'),
  );
  vi.spyOn(api, 'preferences').mockResolvedValue(preferences);
  vi.spyOn(api, 'notifications').mockResolvedValue({
    items: [],
    unread_count: 0,
    next_offset: null,
  });
});
afterEach(() => vi.restoreAllMocks());
const open = (route = '/account') =>
  render(
    <MemoryRouter initialEntries={[route]}>
      <App />
    </MemoryRouter>,
  );

it('loads persisted defaults before publishing a newly authenticated account', async () => {
  let resolve!: (value: typeof preferences) => void;
  vi.mocked(api.preferences).mockImplementation(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  vi.spyOn(api, 'login').mockResolvedValue({ user, csrf_token: 'memory-token' });
  open();
  fireEvent.change(await screen.findByLabelText('Email address'), {
    target: { value: user.email },
  });
  fireEvent.change(screen.getByLabelText('Password'), {
    target: { value: 'test-only-password-123' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
  await waitFor(() => expect(api.preferences).toHaveBeenCalled());
  expect(screen.queryByRole('heading', { name: 'Good to see you.' })).not.toBeInTheDocument();
  await act(async () => resolve({ ...preferences, max_walk_meters: 700 }));
  expect(await screen.findByRole('slider', { name: /Maximum approximate walk/ })).toHaveValue(
    '700',
  );
});

it.each(['login', 'register'] as const)(
  'completes %s without storing credentials in localStorage',
  async (mode) => {
    const authenticate = vi
      .spyOn(api, mode)
      .mockResolvedValue({ user, csrf_token: 'in-memory-test-token' });
    const storage = vi.spyOn(Storage.prototype, 'setItem');
    open(`/account?mode=${mode}`);
    const email = await screen.findByLabelText('Email address');
    fireEvent.change(email, { target: { value: user.email } });
    fireEvent.change(screen.getByLabelText('Password'), {
      target: { value: 'test-only-password-123' },
    });
    fireEvent.click(
      screen.getByRole('button', { name: mode === 'register' ? 'Create account' : 'Sign in' }),
    );
    expect(await screen.findByRole('heading', { name: 'Good to see you.' })).toBeInTheDocument();
    expect(authenticate).toHaveBeenCalledWith(user.email, 'test-only-password-123');
    expect(storage).not.toHaveBeenCalled();
    expect(screen.getByRole('link', { name: 'Notification history' })).toBeInTheDocument();
  },
);

it('recovers CSRF after reload/rotation and saves only session-owned preferences', async () => {
  vi.mocked(api.me).mockResolvedValue(user);
  const rotate = vi
    .spyOn(api, 'csrf')
    .mockResolvedValueOnce({ csrf_token: 'token-1' })
    .mockResolvedValueOnce({ csrf_token: 'token-2' });
  const save = vi
    .spyOn(api, 'savePreferences')
    .mockRejectedValueOnce(new ApiError('Rotated token', 403, 'csrf_rejected'))
    .mockResolvedValue({ ...preferences, max_walk_meters: 700 });
  const logout = vi.spyOn(api, 'logout').mockResolvedValue();
  open();
  fireEvent.change(await screen.findByRole('slider', { name: /Maximum approximate walk/ }), {
    target: { value: '700' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Save preferences' }));
  expect(await screen.findByText('Preferences saved')).toBeInTheDocument();
  expect(rotate).toHaveBeenCalledTimes(2);
  expect(save).toHaveBeenNthCalledWith(
    1,
    { max_walk_meters: 700, reliability_weight: 0.7 },
    'token-1',
  );
  expect(save).toHaveBeenNthCalledWith(
    2,
    { max_walk_meters: 700, reliability_weight: 0.7 },
    'token-2',
  );
  fireEvent.click(screen.getByRole('button', { name: 'Sign out' }));
  await waitFor(() => expect(logout).toHaveBeenCalledWith('token-2'));
  expect(await screen.findByLabelText('Email address')).toBeInTheDocument();
});

it('presents login failures without exposing passwords', async () => {
  vi.spyOn(api, 'login').mockRejectedValue(
    new ApiError('Invalid email or password', 401, 'invalid_credentials'),
  );
  open();
  fireEvent.change(await screen.findByLabelText('Email address'), {
    target: { value: user.email },
  });
  fireEvent.change(screen.getByLabelText('Password'), {
    target: { value: 'test-only-password-123' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Invalid email or password');
  expect(screen.getByRole('alert')).not.toHaveTextContent('test-only-password-123');
});
