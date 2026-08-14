import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const backend = {
  target: 'http://127.0.0.1:8000',
  // summarize / ask 可能很久
  timeout: 0,
  proxyTimeout: 0,
}

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/ask': backend,
      '/summarize': backend,
      '/ingest': backend,
      '/health': backend,
    },
  },
})
