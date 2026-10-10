import { test, expect } from '@playwright/test';
import { forecast, history, recommendations, station } from '../src/test/fixtures';

test.beforeEach(async ({ page }) => {
  // Explicit synthetic HTTP fixtures for deterministic offline CI, not app demo data.
  await page.route('**/api/v1/**', (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    let body: unknown;
    let status = 200;
    if (path === '/api/v1/auth/me') {
      status = 401;
      body = { error: { code: 'authentication_required', message: 'Sign in required' } };
    } else if (path === '/api/v1/stations')
      body = {
        items: [station(), station('beta', 'Beta & Second')],
        next_offset: null,
        limit: 100,
        offset: 0,
      };
    else if (path.endsWith('/predictions')) body = forecast;
    else if (path.endsWith('/demand')) body = history;
    else if (path === '/api/v1/forecasts/model')
      body = {
        version: 'synthetic',
        is_demo: true,
        source: { first_bucket: 1735707600, last_bucket: 1740804300 },
      };
    else if (path === '/api/v1/recommendations/trip') body = recommendations();
    else if (path.startsWith('/api/v1/stations/')) body = station();
    else {
      status = 503;
      body = { error: { code: 'dependency_unavailable', message: 'Test dependency unavailable' } };
    }
    return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.route('https://tiles.openfreemap.org/**', (route) => route.abort());
  await page.route('https://fonts.googleapis.com/**', (route) => route.abort());
});

test('offline contracts support station → forecast → history → planner', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: /^Alpha & First/ }).click();
  await expect(page.getByText('No reliable risk estimate')).toBeVisible();
  await page.getByRole('button', { name: '60-min horizon', exact: true }).click();
  await expect(page.getByRole('img', { name: /Bikes departing: mean 4.0/ })).toBeVisible();
  await page.getByRole('button', { name: 'Ride history', exact: true }).click();
  await expect(page.getByRole('button', { name: /^8:00 New York: 5 departures/ })).toBeVisible();
  await page.getByRole('button', { name: 'Start here', exact: true }).click();
  await page.getByRole('combobox', { name: 'Destination', exact: true }).fill('Beta');
  await page.getByRole('option', { name: 'Beta & Second', exact: true }).click();
  await page.getByRole('button', { name: 'Find my ride', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Trip recommendations' })).toContainText(
    'Measured inventory ranking',
  );
});

test('mobile controls work without basemap/network dependencies', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/?demo=1');
  await expect(page.getByText(/Demo mode: explicitly includes synthetic/)).toBeVisible();
  await page.getByRole('searchbox', { name: 'Search loaded stations' }).fill('No matching station');
  await expect(page.getByRole('heading', { name: 'No matches just yet' })).toBeVisible();
  // A cold dev server first compiles the lazy renderer; the map also has a bounded
  // 20-second failure deadline. Wait for that state, not an arbitrary sleep.
  await expect(page.getByText('Basemap unavailable', { exact: true })).toBeVisible({
    timeout: 30000,
  });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
