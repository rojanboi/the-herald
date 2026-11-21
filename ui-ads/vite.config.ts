import compiled from '@compiled/vite-plugin';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// The built app goes to ../ui/ads, which the API already serves: http://localhost:8080/ads/
// (same origin as the API, so sign-in and the cross-site check work unchanged).
// `npm run dev` is for working on the UI with hot reload; it forwards API calls to the running service.
const api = `http://localhost:${process.env.HERALD_PORT ?? 8080}`;

export default defineConfig({
  // Atlassian's components ship their styles as pre-built atomic CSS (*.compiled.css). Those rules only
  // work in a fixed order (media queries last), which this plugin restores after bundling.
  plugins: [compiled({ sortOnlyCompiledCss: true, importReact: false }), react()],
  base: './',
  build: { outDir: '../ui/ads', emptyOutDir: true, chunkSizeWarningLimit: 4000 },
  server: {
    proxy: Object.fromEntries(['/api', '/ingest', '/auth', '/login', '/lab.html'].map((p) => [p, api])),
  },
});
