import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev: Vite serves the UI on :5173 and proxies /api to the FastAPI backend.
// Build: `npm run build` emits ./dist, which the FastAPI app serves at / .
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { '/api': 'http://127.0.0.1:8011' } },
  build: { outDir: 'dist', emptyOutDir: true },
})
