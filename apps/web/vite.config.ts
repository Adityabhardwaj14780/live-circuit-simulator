/// <reference types="vitest" />
import react from '@vitejs/plugin-react';
import { defineConfig, loadEnv } from 'vite';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const apiTarget = env.VITE_API_PROXY_TARGET ?? 'http://localhost:4000';

  return {
    plugins: [react()],
    server: {
      host: '0.0.0.0',
      port: Number(env.VITE_PORT ?? 5173),
      strictPort: false,
      // The dev server is reachable through the sandbox preview host as well as localhost.
      allowedHosts: true,
      // The browser only ever calls relative /api paths; the dev server proxies them to Express.
      proxy: {
        '/api': { target: apiTarget, changeOrigin: true },
        '/health': { target: apiTarget, changeOrigin: true },
      },
    },
    // `vite preview` serves the production bundle with the same relative-path proxying as dev, so
    // the built app can be smoke-tested without nginx (the Docker image uses nginx instead).
    preview: {
      host: '0.0.0.0',
      port: Number(env.VITE_PREVIEW_PORT ?? 5174),
      allowedHosts: true,
      proxy: {
        '/api': { target: apiTarget, changeOrigin: true },
        '/health': { target: apiTarget, changeOrigin: true },
      },
    },
    build: { outDir: 'dist', sourcemap: true },
    test: {
      environment: 'node',
      include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
      testTimeout: 15000,
    },
  };
});
