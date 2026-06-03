import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Build into the location Flask serves from (backend/core/paths.py STATIC_DIR).
// Dev server proxies /api to the Flask backend on :5174.
export default defineConfig({
  plugins: [vue()],
  base: './',
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:5174',
    },
  },
})
