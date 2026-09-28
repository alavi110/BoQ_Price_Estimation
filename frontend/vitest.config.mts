import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    // jsdom, not happy-dom: the dashboard leans on layout-independent APIs but
    // Recharts measures its container, and jsdom is the environment the library's
    // own tests target. A chart that renders in one and not the other is a chart
    // whose test is measuring the environment.
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./vitest.setup.ts'],
    // One runner for the whole suite, e2e included. The flow test is a jsdom
    // integration test rather than a Playwright run, so it needs no browser and
    // no running server - and excluding it would mean the seam between the
    // screens is the one part of the dashboard with no test at all.
    include: ['tests/**/test_*.{ts,tsx}'],
    exclude: ['node_modules/**'],
    css: false,
  },
});
