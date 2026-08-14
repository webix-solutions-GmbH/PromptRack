import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Ports are deliberately off the defaults (vite 5173, uvicorn 8000): those
// collide with every other project on the machine, and vite's default
// behaviour on a taken port is to silently move to the next one — which
// serves *someone else's* app at the URL you expect. `strictPort` makes that
// a startup error instead.
const API_PORT = 8077
const WEB_PORT = 5177

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  server: {
    port: WEB_PORT,
    strictPort: true,
    proxy: {
      '/api': {
        target: `http://localhost:${API_PORT}`,
        changeOrigin: true,
      },
    },
  },
})
