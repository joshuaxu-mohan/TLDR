import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: 'dist',
  },
  server: {
    port: 5173,
    // Proxy API calls to the FastAPI backend during development.
    // No rewrite — /api/... is forwarded to FastAPI unchanged so both dev
    // and production hit the same /api/* route paths.
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
