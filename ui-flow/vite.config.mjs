import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

const here = dirname(fileURLToPath(import.meta.url));

// Builds one classic script (window.HeraldFlow) and one stylesheet into ../ui/flow, which the API
// already serves at http://localhost:8080/flow/. React, react-dom and React Flow are bundled in.
// Everything in ./public (demo.html) is copied next to them, so emptying the folder is safe.
export default defineConfig({
  plugins: [react()],
  define: { 'process.env.NODE_ENV': JSON.stringify('production') },
  publicDir: resolve(here, 'public'),
  build: {
    outDir: resolve(here, '../ui/flow'),
    emptyOutDir: true,
    copyPublicDir: true,
    cssCodeSplit: false,
    sourcemap: false,
    lib: {
      entry: resolve(here, 'src/index.jsx'),
      name: 'HeraldFlow',
      formats: ['iife'],
      fileName: () => 'herald-flow.js',
      cssFileName: 'herald-flow',
    },
  },
});
