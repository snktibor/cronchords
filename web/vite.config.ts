import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  // Relative asset paths, so the build also works under GitHub Pages' /<repo>/ path.
  base: './',
  server: {
    // Spotify only accepts loopback redirect URIs written as 127.0.0.1, not localhost.
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
  },
})
