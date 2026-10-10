import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  timeout: 45000,
  retries: 0, // A flaky pass must not hide a failed first attempt.
  forbidOnly: !!process.env.CI,
  reporter: [
    ['list'],
    ['html', { open: 'never' }],
    ['junit', { outputFile: 'test-results/junit.xml' }],
  ],
  use: {
    baseURL: process.env.BIKE_E2E_BASE_URL ?? 'http://localhost:5173',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    ignoreHTTPSErrors: process.env.BIKE_E2E_SELF_SIGNED === '1',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: process.env.BIKE_E2E_EXTERNAL
    ? undefined
    : {
        command: process.env.BIKE_E2E_WEB_COMMAND ?? 'npm run dev',
        url: process.env.BIKE_E2E_BASE_URL ?? 'http://localhost:5173',
        reuseExistingServer: !process.env.CI || !!process.env.BIKE_E2E_EXTERNAL,
      },
});
