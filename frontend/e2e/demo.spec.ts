/** Reproducible actual-app walkthrough. No mocked APIs, counts or model responses. */
import { test, expect } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';

test.skip(
  !process.env.BIKE_E2E_DEMO,
  'Opt in against the offline demo or an explicitly selected real-data app',
);
test.use({ viewport: { width: 1440, height: 1000 }, video: 'on' });

test('station → qualified forecasts → commute alternatives → historical playback', async ({
  page,
}, info) => {
  const stationId = process.env.BIKE_DEMO_STATION ?? 'DEMO-A';
  const result = await page.request.get(`/api/v1/stations/${encodeURIComponent(stationId)}`);
  expect(result.status()).toBe(200);
  const station = await result.json();
  const catalog = await (await page.request.get('/api/v1/stations?limit=100')).json();
  const destination = catalog.items.find(
    (item: { external_id: string }) => item.external_id !== stationId,
  );
  expect(destination).toBeTruthy();
  const folder = process.env.BIKE_DEMO_CAPTURE
    ? resolve('../docs/screenshots')
    : info.outputPath('walkthrough');
  mkdirSync(folder, { recursive: true });
  await page.goto(`/?station=${encodeURIComponent(stationId)}`);
  await expect(page.getByRole('heading', { name: station.name, exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'What’s coming next?' })).toBeVisible();
  await expect(page.locator('.station-map')).toHaveAttribute('aria-busy', 'false', {
    timeout: 20000,
  });
  await page.getByRole('button', { name: '60-min horizon', exact: true }).click();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: `${folder}/station-outlook.png`, fullPage: true });
  if (station.is_demo)
    await expect(page.getByText('Synthetic station.', { exact: false })).toBeVisible();
  await page.getByRole('button', { name: 'Start here', exact: true }).click();
  await page.getByRole('combobox', { name: 'Destination', exact: true }).fill(destination.name);
  await page.getByRole('option', { name: destination.name, exact: true }).click();
  await page.getByRole('button', { name: 'Find my ride', exact: true }).click();
  const recommendations = page.getByRole('region', { name: 'Trip recommendations' });
  await expect(recommendations).toContainText('Pick up a bike');
  await expect(recommendations.locator('.recommendation-card').first()).toBeVisible();
  await page.locator('.explorer-body').evaluate((element) => {
    element.scrollTop = 0;
  });
  await page.screenshot({ path: `${folder}/commute-planner.png`, fullPage: true });
  await page.getByRole('navigation').getByRole('link', { name: 'Replay', exact: true }).click();
  const timeline = page.getByRole('slider', { name: 'Historical timeline' });
  await expect(timeline).toBeVisible();
  await page.getByLabel('Find a replay station').fill(station.name);
  await page
    .getByRole('button', {
      name: new RegExp(station.name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ' .*rides'),
    })
    .click();
  await timeline.fill('34');
  await page.getByLabel('Playback speed').selectOption('250');
  await page.getByRole('button', { name: 'Play replay', exact: true }).click();
  await expect.poll(() => timeline.inputValue()).not.toBe('34');
  await page.getByRole('button', { name: 'Pause replay', exact: true }).click();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: `${folder}/historical-replay.png`, fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `${folder}/replay-mobile.png`, fullPage: true });
});

test('offline accounts persist commutes without enabling synthetic inventory alerts', async ({
  page,
}) => {
  test.skip(
    !!process.env.BIKE_DEMO_STATION,
    'Do not create accounts in a real-data screenshot app',
  );
  const metadata = await (await page.request.get('/api/v1/metadata')).json();
  expect(metadata.is_demo).toBe(true); // Fail closed before creating test-owned data.
  const email = `offline-walkthrough-${crypto.randomUUID()}@example.com`;
  await page.goto('/account?mode=register');
  await page.getByLabel('Email address').fill(email);
  await page
    .getByLabel('Password', { exact: true })
    .fill('Disposable offline walkthrough password!');
  await page.getByRole('button', { name: 'Create account', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Good to see you.' })).toBeVisible();
  await page.getByRole('navigation').getByRole('link', { name: 'Saved commutes' }).click();
  await page.getByRole('button', { name: 'New commute', exact: true }).click();
  await page.getByLabel('Commute name').fill('Offline morning ride');
  const catalog = await (await page.request.get('/api/v1/stations?limit=10')).json();
  for (const [label, identifier] of [
    ['Origin', 'DEMO-A'],
    ['Destination', 'DEMO-B'],
  ]) {
    const name = catalog.items.find(
      (station: { external_id: string }) => station.external_id === identifier,
    ).name;
    await page.getByRole('combobox', { name: label, exact: true }).fill(name);
    await page.getByRole('option', { name, exact: true }).click();
  }
  await page.getByRole('button', { name: 'Save commute', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Offline morning ride' })).toBeVisible();
  await page.reload();
  await page.getByRole('button', { name: 'Edit commute', exact: true }).click();
  await page.getByLabel('Commute name').fill('Offline edited ride');
  await page.getByRole('button', { name: 'Save commute', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Offline edited ride' })).toBeVisible();
  await page.getByRole('button', { name: 'Check recommendations now' }).click();
  await expect(
    page.getByRole('region', { name: 'Current recommendations for Offline edited ride' }),
  ).toContainText('Pick up a bike');
  await page.getByRole('button', { name: 'Availability alerts', exact: true }).click();
  await page.getByRole('button', { name: 'Add availability alert' }).click();
  await expect(
    page.getByText('Alerts require real station measurements.', { exact: false }),
  ).toBeVisible();
  await page.getByRole('combobox', { name: 'Alert station' }).fill('Demo');
  await expect(
    page.getByRole('option', { name: 'Demo · Midtown pickup', exact: true }),
  ).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Save alert', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: 'Cancel alert', exact: true }).click();
  await page.getByRole('button', { name: 'Delete commute', exact: true }).click();
  await page.getByRole('button', { name: 'Yes, delete commute', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Offline edited ride' })).toHaveCount(0);
  await page.goto('/account');
  const signedOut = page.waitForResponse(
    (response) => response.url().endsWith('/auth/logout') && response.status() === 204,
  );
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await signedOut;
  expect((await page.request.get('/api/v1/commutes')).status()).toBe(401);
});
