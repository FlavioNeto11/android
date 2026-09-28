import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// Dev: http://127.0.0.1:5173 com proxy de /api (REST + WebSocket) para o backend FastAPI.
// Produção: `vite build` gera frontend/dist, servido estaticamente pelo backend em "/".
// `--mode simulado` aponta o proxy para um backend SIMULADO em 127.0.0.1:8765 (aceite visual sem tocar a produção,
// que na máquina central é o 8000); `VITE_API_TARGET` no ambiente manda sobre os dois.
export default defineConfig(({ mode }) => ({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': {
        target: process.env.VITE_API_TARGET ?? (mode === 'simulado' ? 'http://127.0.0.1:8765' : 'http://127.0.0.1:8000'),
        changeOrigin: false,
        ws: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: false,
  },
  css: {
    modules: {
      localsConvention: 'camelCaseOnly',
    },
  },
  test: {
    // Testes de lógica pura rodam em node; os de integração da UI pedem jsdom no próprio arquivo
    // (docblock `@vitest-environment jsdom`).
    environment: 'node',
    include: ['src/**/*.test.{ts,tsx}'],
    restoreMocks: true,
  },
}));
