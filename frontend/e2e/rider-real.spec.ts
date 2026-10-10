/** Explicit opt-in: real API/DB, disposable test-owned account, no mocked endpoints. */
import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';

test.skip(!process.env.BIKE_E2E_REAL, 'Requires populated migrated local API');
test.use({ viewport: { width: 1440, height: 960 }, trace: 'off' });

test('real historical playback, server-stored comparisons and mobile controls', async ({
  page,
}) => {
  await page.goto('/replay');
  await expect(page.getByRole('slider', { name: 'Historical timeline' })).toBeVisible();
  await page.getByLabel('Recorded day (New York)').selectOption('2025-02-28');
  await page.getByLabel('Find a replay station').fill('W 35 St & 9 Ave');
  await page.getByRole('button', { name: /W 35 St & 9 Ave .*rides/ }).click();
  const timeline = page.getByRole('slider', { name: 'Historical timeline' });
  await timeline.fill('32');
  await expect(
    page.getByRole('heading', { name: 'Looking forward, then looking back.' }),
  ).toBeVisible();
  await expect(page.locator('.replay-current')).toContainText('2 departures');
  await expect(page.locator('.replay-comparison')).toContainText('not recorded live');
  await page.getByLabel('Playback speed').selectOption('250');
  await page.getByRole('button', { name: 'Play replay', exact: true }).click();
  await expect.poll(() => timeline.inputValue()).not.toBe('32');
  await page.getByRole('button', { name: 'Pause replay', exact: true }).click();
  await timeline.fill('32');
  await page.getByLabel('Map shows').selectOption('arrivals');
  await expect(page.locator('.station-map')).toHaveAttribute('aria-busy', 'false', {
    timeout: 15000,
  });
  await page.getByRole('heading', { name: 'Watch the rides ripple.' }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: resolve('../data/validation/rider-replay-desktop.png') });
  await page.getByRole('region', { name: 'Historical demand city map' }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: resolve('../data/validation/rider-replay-map-desktop.png') });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await timeline.scrollIntoViewIfNeeded();
  await page.screenshot({ path: resolve('../data/validation/rider-replay-mobile.png') });
  await timeline.fill('95');
  await expect(page.getByRole('button', { name: 'Next 15 min' })).toBeDisabled();
  await page.getByLabel('Recorded day (New York)').selectOption('2025-02-27');
  await expect(timeline).toHaveValue('0');
  await expect(page.getByText(/No stored historical forecast at this station/)).toBeVisible();
});

test('real saved commute persistence, alert evaluation, notification history and ownership', async ({
  page,
  browser,
}) => {
  test.setTimeout(120000);
  const email = `rider-e2e-${Date.now()}@example.com`;
  await page.goto('/account?mode=register');
  await page.getByLabel('Email address').fill(email);
  await page.getByLabel('Password', { exact: true }).fill('Disposable rider E2E password 2026!');
  await page.getByRole('button', { name: 'Create account', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Good to see you.' })).toBeVisible();
  await page.getByRole('navigation').getByRole('link', { name: 'Saved commutes' }).click();
  await page.getByRole('button', { name: 'New commute', exact: true }).click();
  await page.getByLabel('Commute name').fill('E2E morning ride');
  await page.getByRole('combobox', { name: 'Origin', exact: true }).fill('W 35 St & 9 Ave');
  await page.getByRole('option', { name: 'W 35 St & 9 Ave', exact: true }).click();
  await page
    .getByRole('combobox', { name: 'Destination', exact: true })
    .fill('Madison Ave & E 26 St');
  await page.getByRole('option', { name: 'Madison Ave & E 26 St', exact: true }).click();
  const schedule = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'America/New_York',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).format(new Date(Date.now() + 20 * 60000));
  await page.getByLabel('Preferred bike departure').fill(schedule);
  await page.getByLabel('Sat', { exact: true }).check();
  await page.getByLabel('Sun', { exact: true }).check();
  await page.getByRole('button', { name: 'Save commute', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'E2E morning ride' })).toBeVisible();
  await page.reload();
  await page.getByRole('button', { name: 'Edit commute', exact: true }).click();
  await page.getByLabel('Commute name').fill('E2E everyday ride');
  await page.getByRole('slider', { name: /Maximum approximate walk/ }).fill('700');
  await page.getByRole('button', { name: 'Save commute', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'E2E everyday ride' })).toBeVisible();
  const saved = (await (await page.request.get('/api/v1/commutes')).json()).items[0];
  expect(saved.preferences.max_walk_meters).toBe(700);
  const outsider = await browser.newContext({ baseURL: 'http://localhost:5173' });
  expect((await outsider.request.get(`/api/v1/commutes/${saved.id}`)).status()).toBe(401);
  await outsider.close();
  await page.getByRole('button', { name: 'Check recommendations now' }).click();
  await expect(
    page.getByRole('region', { name: 'Current recommendations for E2E everyday ride' }),
  ).toContainText('Pick up a bike');
  await page.getByRole('button', { name: 'Availability alerts', exact: true }).click();
  await page.getByRole('button', { name: 'Add availability alert' }).click();
  await page.getByRole('combobox', { name: 'Alert station' }).fill('W 35 St & 9 Ave');
  await page.getByRole('option', { name: 'W 35 St & 9 Ave', exact: true }).click();
  // Deliberately permissive TEST condition on actual measured inventory, never fake data.
  await page.getByLabel('Bikes / open docks threshold').fill('100');
  await page.getByRole('button', { name: 'Save alert', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Pause alert' })).toBeVisible();
  // Optional trusted local operator command shortens the scheduled-job wait.
  if (process.env.BIKE_E2E_PYTHON && process.env.DATABASE_URL) {
    execFileSync(process.env.BIKE_E2E_PYTHON, ['-m', 'app.modules.monitoring.cli', 'alerts'], {
      cwd: resolve('../backend'),
      env: { ...process.env, APP_ENV: 'development' },
      timeout: 60000,
      stdio: 'pipe',
    });
  }
  await expect
    .poll(
      async () => (await (await page.request.get('/api/v1/notifications')).json()).unread_count,
      { timeout: 90000, intervals: [1000, 5000] },
    )
    .toBe(1);
  await page.screenshot({ path: resolve('../data/validation/rider-commutes-desktop.png') });
  await page.getByRole('button', { name: 'Pause alert' }).click();
  await expect(page.getByRole('button', { name: 'Enable alert' })).toBeVisible();
  await page.getByRole('button', { name: 'Edit alert' }).click();
  await page.getByLabel('Bikes / open docks threshold').fill('99');
  await page.getByRole('button', { name: 'Save alert', exact: true }).click();
  await expect(page.getByText('Pickup bikes · ≤ 99 measured')).toBeVisible();
  await page.getByRole('button', { name: 'Remove alert', exact: true }).click();
  await expect(page.getByText(/No alerts yet/)).toBeVisible();
  await page.getByRole('link', { name: /^Notifications/ }).click();
  await expect(page.getByRole('button', { name: 'Mark read', exact: true })).toBeVisible();
  await page.screenshot({ path: resolve('../data/validation/rider-notifications-desktop.png') });
  await page.getByRole('button', { name: 'Mark read', exact: true }).click();
  await expect(page.getByText('0 unread', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Mark unread', exact: true }).click();
  await expect(page.getByText('1 unread', { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByText('1 unread', { exact: true })).toBeVisible();
  await page.getByRole('navigation').getByRole('link', { name: 'Saved commutes' }).click();
  await page.getByRole('link', { name: 'Open in map planner' }).click();
  await expect(page.getByRole('combobox', { name: 'Origin', exact: true })).toHaveValue(
    'W 35 St & 9 Ave',
  );
  await expect(page.getByRole('combobox', { name: 'Destination', exact: true })).toHaveValue(
    'Madison Ave & E 26 St',
  );
  await page.getByRole('link', { name: 'Save this commute', exact: true }).click();
  await expect(page.getByRole('region', { name: 'New saved commute' })).toBeVisible();
  await expect(page.getByRole('combobox', { name: 'Origin', exact: true })).toHaveValue(
    'W 35 St & 9 Ave',
  );
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.getByRole('navigation').getByRole('link', { name: 'Saved commutes' }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: resolve('../data/validation/rider-commutes-mobile.png') });
  await page.getByRole('button', { name: 'Delete commute', exact: true }).click();
  await page.getByRole('button', { name: 'Yes, delete commute', exact: true }).click();
  await expect(page.getByText('Your regular ride belongs here.')).toBeVisible();
  expect((await (await page.request.get('/api/v1/commutes')).json()).items).toHaveLength(0);
  expect((await (await page.request.get('/api/v1/notifications')).json()).items).toHaveLength(1);
});
