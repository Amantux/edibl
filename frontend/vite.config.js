import { defineConfig } from 'vite'
import { readFileSync } from 'node:fs'
import { fileURLToPath, URL } from 'node:url'
import vue from '@vitejs/plugin-vue'

// Surface the add-on version (from addon/config.yaml) to the SPA at build time so
// a bug report can include it. Falls back to 'dev' when the manifest isn't present.
function appVersion() {
  try {
    const yaml = readFileSync(fileURLToPath(new URL('../addon/config.yaml', import.meta.url)), 'utf8')
    const m = yaml.match(/^version:\s*["']?([^"'\n]+)["']?/m)
    return m ? m[1].trim() : 'dev'
  } catch {
    return 'dev'
  }
}

export default defineConfig({
  base: './',
  plugins: [vue()],
  define: { __APP_VERSION__: JSON.stringify(appVersion()) },
  server: { port: 5180, proxy: { '/api': 'http://localhost:7746' } },
  build: { outDir: 'dist', emptyOutDir: true },
})
