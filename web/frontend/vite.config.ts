import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: { host: '127.0.0.1', port: 57450, strictPort: true },
  preview: { host: '127.0.0.1', port: 57450, strictPort: true },
  build: { target: 'es2022' },
})
