import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  base: './',
  plugins: [vue()],
  server: { port: 5180, proxy: { '/api': 'http://localhost:7746' } },
  build: { outDir: 'dist', emptyOutDir: true },
})
