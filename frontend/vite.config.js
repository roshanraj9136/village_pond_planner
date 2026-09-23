import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// `npm run dev` proxies the API to the deployed gateway (override with VITE_API_TARGET).
const target = process.env.VITE_API_TARGET || 'http://10.1.75.53:3297'
const proxy = Object.fromEntries(
  ['/api', '/docs', '/openapi.json', '/gateway', '/analyzeContour', '/findCatchment'].map((p) => [p, { target, changeOrigin: true }]),
)

export default defineConfig({
  plugins: [react()],
  server: { proxy },
  build: { sourcemap: false, chunkSizeWarningLimit: 700 },
})
