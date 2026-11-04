import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The build goes into the Python package, so `inboxsitrep serve` works
// without node.  `npm run dev` proxies the API to a running server.
export default defineConfig({
  plugins: [react()],
  build: { outDir: '../inboxsitrep/static', emptyOutDir: true },
  server: { proxy: { '/api': 'http://127.0.0.1:8008' } },
})
