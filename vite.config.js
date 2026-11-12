// vite.config.js
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath, URL } from 'node:url'

// Backend address; override for local testing:  LOTUS_API=http://localhost:5000 npm run dev
const API = process.env.LOTUS_API || 'http://hpc.psy.ntu.edu.tw:5000'
const proxy = {
  '/terms': API,
  '/query': API,
  '/help': API,
}

export default defineConfig({
  base: '/lotus-bf/',
  plugins: [react()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    allowedHosts: ['hpc.psy.ntu.edu.tw'],
    host: '0.0.0.0',
    port: 3000,
    proxy,
  },
  // `npm run preview` (serves the production build) uses the same proxy
  preview: {
    allowedHosts: ['hpc.psy.ntu.edu.tw'],
    host: '0.0.0.0',
    port: 3000,
    proxy,
  },
})
