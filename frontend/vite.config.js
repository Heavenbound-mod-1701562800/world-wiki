import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const backend = {
  target: 'http://127.0.0.1:8000',
  // summarize / ask 可能很久
  timeout: 0,
  proxyTimeout: 0,
}

const proxy = {
  '/ask': backend,
  '/summarize': backend,
  '/ingest': backend,
  '/pages': backend,
  '/health': backend,
}

export default defineConfig({
  plugins: [react()],
  server: { proxy },
  // vite preview 也走同一套代理，否则生产构建页请求不到 Flask
  preview: { proxy },
})
