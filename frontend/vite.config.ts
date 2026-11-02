import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  server: {
    // accès à distance par Tailscale Serve (https://<mac>.<tailnet>.ts.net), voir DEV_LOCAL.md
    allowedHosts: ['.ts.net'],
    // cible surchargable : MATHPRINT_API=http://localhost:8899 npm run dev
    proxy: { '/api': process.env.MATHPRINT_API ?? 'http://localhost:8787' },
  },
})
