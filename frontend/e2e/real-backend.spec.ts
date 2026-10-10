import { test, expect } from '@playwright/test';
import type { StationPage } from '../src/lib/types';

test.skip(
  !process.env.BIKE_E2E_REAL,
  'Opt in with BIKE_E2E_REAL=1 and a populated local PostgreSQL-backed API',
);
test.use({ viewport: { width: 1440, height: 960 }, trace: 'off' });

test('real map click → predictions → historical rides → ranked recommendations', async ({
  page,
}) => {
  const response = await page.request.get('/api/v1/stations?limit=100');
  expect(response.status()).toBe(200);
  const catalog = (await response.json()) as StationPage;
  const selected = catalog.items.find((station) => station.name === 'W 35 St & 9 Ave')!;
  expect(selected).toBeTruthy();
  await page.goto('/');
  await expect(page.getByRole('link', { name: 'OpenStreetMap', exact: true })).toBeVisible();
  await expect(page.getByText('Loading the city map…')).toBeHidden();
  const moved = page.waitForResponse(
    (result) =>
      result.url().includes('/stations?') &&
      result.url().includes('bbox=') &&
      result.status() === 200,
  );
  await page.getByRole('button', { name: 'Zoom in', exact: true }).click();
  await moved;
  await expect(page.locator('.station-map')).toHaveAttribute('aria-busy', 'false', {
    timeout: 15000,
  });
  const canvas = page.locator('.station-map canvas');
  const box = await canvas.boundingBox();
  expect(box).not.toBeNull();
  // Project one real station at the known initial camera after one zoom-in.
  // This is test-only map geometry, not demand/inventory logic.
  const world = 512 * 2 ** 14.4;
  const mercatorY = (lat: number) =>
    (1 - Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360)) / Math.PI) / 2;
  const x = box!.width / 2 + ((selected.longitude - -73.986) / 360) * world;
  const y = box!.height / 2 + (mercatorY(selected.latitude) - mercatorY(40.751)) * world;
  await canvas.click({ position: { x, y } });
  await expect(page.getByRole('heading', { name: selected.name, exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'What’s coming next?' })).toBeVisible();
  await page.getByRole('button', { name: '60-min horizon', exact: true }).click();
  await expect(page.getByText(/Cumulative observed-ride demand/)).toBeVisible();
  await page.getByRole('button', { name: 'Ride history', exact: true }).click();
  await expect(
    page.getByRole('group', { name: 'Hourly observed rides, New York time' }),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Start here', exact: true }).click();
  await page.getByRole('combobox', { name: 'Destination', exact: true }).fill('Madison Ave & E 26');
  await page.getByRole('option', { name: 'Madison Ave & E 26 St', exact: true }).click();
  await page.getByRole('button', { name: 'Find my ride', exact: true }).click();
  const results = page.getByRole('region', { name: 'Trip recommendations' });
  await expect(results).toContainText('Pick up a bike');
  await expect(results).toContainText('Find an open dock');
  await expect(results).toContainText('not routing');
  await expect(results.locator('.recommendation-card')).toHaveCount(6);
  await page.getByRole('button', { name: 'Demand outlook', exact: true }).click();
  await results.getByRole('heading', { name: 'Your ride, considered.' }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: 'test-results/real-desktop.png' });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole('button', { name: 'Close station details' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole('heading', { name: selected.name, exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: 'test-results/real-mobile.png' });
});

test('real registration, reload/CSRF recovery, private preferences, logout and login', async ({
  page,
  context,
}) => {
  const email = `frontend-e2e-${Date.now()}@example.com`;
  const password = `Frontend-test-${Date.now()}-only`;
  await page.goto('/account?mode=register');
  await page.getByLabel('Email address').fill(email);
  await page.getByLabel('Password', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Create account', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Good to see you.' })).toBeVisible();
  const cookies = await context.cookies();
  expect(cookies.find((cookie) => cookie.name === 'bike_session')?.httpOnly).toBe(true);
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Good to see you.' })).toBeVisible();
  await page.getByRole('slider', { name: /Maximum approximate walk/ }).fill('700');
  await page.getByRole('button', { name: 'Save preferences', exact: true }).click();
  await expect(page.getByText('Preferences saved', { exact: true })).toBeVisible();
  expect((await (await page.request.get('/api/v1/auth/preferences')).json()).max_walk_meters).toBe(
    700,
  );
  expect(await page.evaluate(() => Object.keys(localStorage).length)).toBe(0);
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await expect(page.getByLabel('Email address')).toBeVisible();
  expect((await page.request.get('/api/v1/auth/preferences')).status()).toBe(401);
  await page.getByLabel('Email address').fill(email);
  await page.getByLabel('Password', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Good to see you.' })).toBeVisible();
  await expect(page.getByRole('slider', { name: /Maximum approximate walk/ })).toHaveValue('700');
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await expect(page.getByLabel('Email address')).toBeVisible();
});
