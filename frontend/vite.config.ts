import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: ['./tests/setup.ts'],
    env: {
      VITE_API_BASE_URL: 'http://127.0.0.1:8000',
      VITE_AMAP_JS_KEY: '',
      VITE_AMAP_SECURITY_JS_CODE: '',
      VITE_AMAP_SERVICE_HOST: '',
    },
  },
})
