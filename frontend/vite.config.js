import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Adresse de l'API FastAPI (APP_PORT du fichier backend/.env).
const API = process.env.VITE_BACKEND_URL || 'http://localhost:8000';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    proxy: {
      '/api': { target: API, changeOrigin: true },
    },
  },
});