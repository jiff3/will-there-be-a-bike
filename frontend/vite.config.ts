import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  return {
    plugins: [react()],
    build: {
      // MapLibre is deliberately lazy-loaded; its renderer is the only large chunk.
      chunkSizeWarningLimit: 1100,
    },
    server: {
      port: 5173,
      watch: {
        // Generated reports must not reload a browser while tests are exercising it.
        ignored: ['**/coverage/**', '**/test-results/**', '**/playwright-report/**'],
      },
      proxy: {
        '/api': {
          target: env.VITE_DEV_PROXY_TARGET ?? 'http://localhost:8000',
          changeOrigin: true,
        },
      },
    },
    test: {
      environment: 'jsdom',
      globals: true,
      setupFiles: './src/test/setup.ts',
      exclude: ['e2e/**', 'node_modules/**'],
      coverage: {
        provider: 'v8',
        reporter: ['text', 'html', 'lcov', 'json-summary'],
        thresholds: { lines: 75, branches: 65, functions: 60, statements: 75 },
      },
    },
  };
});
