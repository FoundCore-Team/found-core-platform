import react from '@vitejs/plugin-react';
import { msw } from 'msw/vite';
import { loadEnv } from 'vite';
import { defineConfig } from 'vitest/config';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');

  return {
    // msw() serves the worker script in development; it is emitted only next to a bundle that
    // imports `virtual:msw`, and main.tsx imports it under `import.meta.env.DEV`.
    plugins: [react(), msw()],
    server: {
      // The browser calls a relative /api, so the backend needs no CORS in development.
      proxy: {
        '/api': {
          target: env.VITE_DEV_API_TARGET ?? 'http://localhost:8000',
          changeOrigin: true,
        },
      },
    },
    preview: {
      // Real Telegram opens the app through an https tunnel, which serves it under a changing
      // trycloudflare.com host; the preview server rejects hosts it does not know.
      allowedHosts: ['.trycloudflare.com'],
    },
    test: {
      environment: 'jsdom',
      setupFiles: ['./tests/setup.ts'],
      include: ['src/**/*.test.{ts,tsx}', 'tests/**/*.test.{ts,tsx}'],
    },
  };
});
