/** Real FastAPI + migrated PostGIS + sealed ML artifact. No API response mocks.
 * All database observations/counts are explicitly SYNTHETIC QA fixtures.
 * Only the basemap is replaced by a local, tile-free MapLibre test style.
 */
import { test, expect, type Page } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';

test.skip(!process.env.BIKE_E2E_QA, 'Requires isolated QA API and guarded fixture seed');
test.use({ viewport: { width: 1440, height: 960 } });

function driver(
  command: 'alerts' | 'fresh' | 'stale' | 'empty-replay' | 'seed' | 'clean-accounts',
) {
  if (!process.env.BIKE_E2E_PYTHON) throw new Error('Set BIKE_E2E_PYTHON to test Python');
  return JSON.parse(
    execFileSync(process.env.BIKE_E2E_PYTHON, ['-m', 'tests.support.qa', command], {
      cwd: resolve('../backend'),
      env: process.env,
      timeout: 30000,
      encoding: 'utf-8',
    }),
  );
}

test.beforeAll(() => {
  if (process.env.BIKE_E2E_QA) driver('clean-accounts');
});
test.afterAll(() => {
  if (process.env.BIKE_E2E_QA) driver('clean-accounts');
});

async function register(page: Page, email = `qa-browser-${crypto.randomUUID()}@example.com`) {
  await page.goto('/account?mode=register');
  await page.getByLabel('Email address').fill(email);
  await page.getByLabel('Password', { exact: true }).fill('Disposable QA password 2026!');
  await page.getByRole('button', { name: 'Create account', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Good to see you.' })).toBeVisible();
  return email;
}

test.beforeEach(async ({ context, page }) => {
  // Any unexpected external dependency fails closed in the browser too.
  await context.route('**/*', (route) => {
    const host = new URL(route.request().url()).hostname;
    return ['localhost', '127.0.0.1'].includes(host) ? route.continue() : route.abort();
  });
  await context.route('**/qa-map-style.json', (route) =>
    route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        version: 8,
        sources: {},
        layers: [
          { id: 'qa-background', type: 'background', paint: { 'background-color': '#e9eee3' } },
        ],
      }),
    }),
  );
  await context.route('https://tiles.openfreemap.org/styles/positron', (route) =>
    route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        version: 8,
        sources: {},
        layers: [
          { id: 'qa-background', type: 'background', paint: { 'background-color': '#e9eee3' } },
        ],
      }),
    }),
  );
  const ready = await page.request.get('/api/v1/health/ready');
  expect(ready.status()).toBe(200);
});

test('empty persisted replay is clearly explained without invented animation', async ({ page }) => {
  driver('empty-replay');
  try {
    await page.goto('/replay');
    await expect(
      page.getByRole('heading', { name: 'No historical replay published yet.' }),
    ).toBeVisible();
    await expect(page.getByRole('slider', { name: 'Historical timeline' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Play replay' })).toHaveCount(0);
  } finally {
    driver('seed');
  }
});

test('registration, secure session, CSRF recovery, logout and login', async ({ page, context }) => {
  const email = await register(page);
  const cookie = (await context.cookies()).find((value) =>
    ['bike_session', '__Host-bike_session'].includes(value.name),
  );
  expect(cookie?.httpOnly).toBe(true);
  if (new URL(page.url()).protocol === 'https:') expect(cookie?.secure).toBe(true);
  await page.reload();
  await page.getByRole('slider', { name: /Maximum approximate walk/ }).fill('700');
  await page.getByRole('button', { name: 'Save preferences', exact: true }).click();
  await expect(page.getByText('Preferences saved', { exact: true })).toBeVisible();
  expect(await page.evaluate(() => Object.keys(localStorage))).toEqual([]);
  const signedOut = page.waitForResponse(
    (response) => response.url().endsWith('/auth/logout') && response.status() === 204,
  );
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await signedOut;
  await expect(page.getByLabel('Email address')).toBeVisible();
  expect((await page.request.get('/api/v1/auth/preferences')).status()).toBe(401);
  await page.getByLabel('Email address').fill(email);
  await page.getByLabel('Password', { exact: true }).fill('Disposable QA password 2026!');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByRole('slider', { name: /Maximum approximate walk/ })).toHaveValue('700');
});

test('real map selection, model charts, history and ranked recommendations on mobile', async ({
  page,
}, info) => {
  driver('fresh');
  await page.goto('/');
  await expect(page.locator('.station-map')).toHaveAttribute('aria-busy', 'false');
  const moved = page.waitForResponse(
    (response) =>
      response.url().includes('/stations?') &&
      response.url().includes('bbox=') &&
      response.status() === 200,
  );
  await page.getByRole('button', { name: 'Zoom in', exact: true }).click();
  await moved;
  await expect(page.locator('.station-map')).toHaveAttribute('aria-busy', 'false');
  // QA Pickup is exactly at the initial center. Clicking the real rendered circle
  // uses MapLibre's hit testing, not a stubbed selection callback.
  const canvas = page.locator('.station-map canvas');
  const box = await canvas.boundingBox();
  expect(box).not.toBeNull();
  // Hit the southwest part of its circle, away from overlapping eastern/northern
  // markers. Circle strokes are hit-testable too, so center clicks are ambiguous.
  await canvas.click({ position: { x: box!.width / 2 - 8, y: box!.height / 2 + 8 } });
  await expect(page.getByRole('heading', { name: 'QA Pickup', exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'What’s coming next?' })).toBeVisible();
  await page.getByRole('button', { name: '60-min horizon', exact: true }).click();
  await expect(page.getByText(/Cumulative observed-ride demand/)).toBeVisible();
  await expect(page.getByText('No reliable risk estimate')).toBeVisible(); // Old winter artifact, honest fallback.
  await page.getByRole('button', { name: 'Ride history', exact: true }).click();
  await page.getByLabel('Historical date · New York').fill('2025-02-28');
  await expect(
    page.getByRole('group', { name: 'Hourly observed rides, New York time' }),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Start here', exact: true }).click();
  await page.getByRole('combobox', { name: 'Destination', exact: true }).fill('QA Destination');
  await page.getByRole('option', { name: 'QA Destination', exact: true }).click();
  await page.getByRole('button', { name: 'Find my ride', exact: true }).click();
  const result = page.getByRole('region', { name: 'Trip recommendations' });
  await expect(result).toContainText('Pick up a bike');
  await expect(result.locator('.recommendation-card')).toHaveCount(6);
  await expect(result).toContainText('not routing');
  await page.screenshot({ path: info.outputPath('map-desktop.png') });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole('button', { name: 'Close station details' }).click();
  await page.screenshot({ path: info.outputPath('map-mobile.png') });
});

test('persisted commute CRUD, scheduled notifications, deduplication and authenticated isolation', async ({
  page,
  browser,
}, info) => {
  test.setTimeout(90000);
  await register(page);
  await page.getByRole('navigation').getByRole('link', { name: 'Saved commutes' }).click();
  await page.getByRole('button', { name: 'New commute', exact: true }).click();
  await page.getByLabel('Commute name').fill('QA regular ride');
  for (const [label, station] of [
    ['Origin', 'QA Pickup'],
    ['Destination', 'QA Destination'],
  ]) {
    await page.getByRole('combobox', { name: label, exact: true }).fill(station);
    await page.getByRole('option', { name: station, exact: true }).click();
  }
  await page.getByLabel('Preferred bike departure').fill(
    new Intl.DateTimeFormat('en-GB', {
      timeZone: 'America/New_York',
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23',
    }).format(new Date(Date.now() + 20 * 60000)),
  );
  await page.getByLabel('Sat', { exact: true }).check();
  await page.getByLabel('Sun', { exact: true }).check();
  await page.getByRole('button', { name: 'Save commute', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'QA regular ride' })).toBeVisible();
  await page.reload();
  await page.getByRole('button', { name: 'Edit commute', exact: true }).click();
  await page.getByLabel('Commute name').fill('QA edited ride');
  await page.getByRole('slider', { name: /Maximum approximate walk/ }).fill('700');
  await page.getByRole('button', { name: 'Save commute', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'QA edited ride' })).toBeVisible();
  const saved = (await (await page.request.get('/api/v1/commutes')).json()).items[0];
  expect(saved.preferences.max_walk_meters).toBe(700);
  await page.getByRole('button', { name: 'Check recommendations now' }).click();
  await expect(
    page.getByRole('region', { name: 'Current recommendations for QA edited ride' }),
  ).toContainText('Pick up a bike');
  await page.getByRole('button', { name: 'Availability alerts', exact: true }).click();
  await page.getByRole('button', { name: 'Add availability alert' }).click();
  await page.getByRole('combobox', { name: 'Alert station' }).fill('QA Pickup');
  await page.getByRole('option', { name: 'QA Pickup', exact: true }).click();
  await page.getByLabel('Bikes / open docks threshold').fill('3');
  await page.getByRole('button', { name: 'Save alert', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Pause alert' })).toBeVisible();
  const rule = (await (await page.request.get(`/api/v1/commutes/${saved.id}/alerts`)).json())
    .items[0];
  expect(driver('alerts').status).toBe('succeeded');
  expect(driver('alerts').status).toBe('succeeded');
  const inbox = await (await page.request.get('/api/v1/notifications')).json();
  expect(inbox.items).toHaveLength(1);
  const foreign = await browser.newContext({
    baseURL: process.env.BIKE_E2E_BASE_URL ?? 'http://localhost:5173',
  });
  const origin = { Origin: process.env.BIKE_E2E_BASE_URL ?? 'http://localhost:5173' };
  const registration = await foreign.request.post('/api/v1/auth/register', {
    data: {
      email: `qa-outsider-${crypto.randomUUID()}@example.com`,
      password: 'Disposable QA outsider password!',
    },
    headers: origin,
  });
  expect(registration.status()).toBe(201);
  const csrf = (await registration.json()).csrf_token;
  const headers = { ...origin, 'X-CSRF-Token': csrf };
  for (const suffix of ['', '/recommendations', '/alerts'])
    expect((await foreign.request.get(`/api/v1/commutes/${saved.id}${suffix}`)).status()).toBe(404);
  expect((await foreign.request.delete(`/api/v1/commutes/${saved.id}`, { headers })).status()).toBe(
    404,
  );
  expect(
    (
      await foreign.request.delete(`/api/v1/commutes/${saved.id}/alerts/${rule.id}`, { headers })
    ).status(),
  ).toBe(404);
  expect(
    (
      await foreign.request.patch(`/api/v1/notifications/${inbox.items[0].id}`, {
        headers,
        data: { read: true },
      })
    ).status(),
  ).toBe(404);
  expect((await (await foreign.request.get('/api/v1/notifications')).json()).items).toEqual([]);
  await foreign.close();
  await page.getByRole('link', { name: /^Notifications/ }).click();
  await expect(page.getByText('1 unread', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Mark read', exact: true }).click();
  await expect(page.getByText('0 unread', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Mark unread', exact: true }).click();
  await page.reload();
  await expect(page.getByText('1 unread', { exact: true })).toBeVisible();
  await page.screenshot({ path: info.outputPath('notification.png') });
  await page.getByRole('navigation').getByRole('link', { name: 'Saved commutes' }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: 'Delete commute', exact: true }).click();
  await page.getByRole('button', { name: 'Yes, delete commute', exact: true }).click();
  await expect(page.getByText('Your regular ride belongs here.')).toBeVisible();
  expect((await (await page.request.get('/api/v1/notifications')).json()).items).toHaveLength(1);
});

test('historical playback controls, timeline boundaries and station-specific counts', async ({
  page,
}, info) => {
  await page.goto('/replay');
  const timeline = page.getByRole('slider', { name: 'Historical timeline' });
  await expect(timeline).toHaveValue('0');
  await expect(page.getByRole('button', { name: 'Previous 15 min' })).toBeDisabled();
  await page.getByLabel('Find a replay station').fill('QA Pickup');
  await page.getByRole('button', { name: /QA Pickup .*rides/ }).click();
  await timeline.fill('34');
  await expect(page.locator('.replay-current')).toContainText('2 departures');
  await expect(page.getByText(/No stored historical forecast at this station/)).toBeVisible();
  await page.getByLabel('Playback speed').selectOption('250');
  await page.getByRole('button', { name: 'Play replay', exact: true }).click();
  await expect.poll(() => timeline.inputValue()).not.toBe('34');
  await page.getByRole('button', { name: 'Pause replay', exact: true }).click();
  await timeline.fill('95');
  await expect(page.getByRole('button', { name: 'Next 15 min' })).toBeDisabled();
  await page.getByRole('button', { name: 'Replay day', exact: true }).click();
  await expect.poll(() => timeline.inputValue()).not.toBe('95');
  await page.getByRole('button', { name: 'Pause replay', exact: true }).click();
  await page.getByLabel('Map shows').selectOption('arrivals');
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath('replay-mobile.png') });
});

test('stale measured feeds and browser offline state recover without invented risk', async ({
  page,
  context,
}) => {
  driver('stale');
  try {
    await page.goto('/?station=05c5e17c-7aa9-49b7-9da3-9db4858ec1fc');
    await expect(page.getByRole('heading', { name: 'QA Pickup', exact: true })).toBeVisible();
    await expect(page.getByText(/stale/i).first()).toBeVisible();
    await expect(page.getByText('No reliable risk estimate')).toBeVisible();
    await context.setOffline(true);
    await expect(page.getByText(/offline/i).first()).toBeVisible();
    await context.setOffline(false);
    driver('fresh');
    await page.reload();
    expect(
      (
        await (
          await page.request.get('/api/v1/stations/05c5e17c-7aa9-49b7-9da3-9db4858ec1fc')
        ).json()
      ).freshness,
    ).toBe('fresh');
    await expect(page.getByRole('heading', { name: 'QA Pickup', exact: true })).toBeVisible();
  } finally {
    await context.setOffline(false);
    driver('fresh');
  }
});
