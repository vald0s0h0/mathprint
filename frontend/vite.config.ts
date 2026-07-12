import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  server: {
    // cible surchargable : MATHPRINT_API=http://localhost:8899 npm run dev
    proxy: { '/api': process.env.MATHPRINT_API ?? 'http://localhost:8787' },
  },
})
