import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// Dev: http://127.0.0.1:5173 com proxy de /api (REST + WebSocket) para o backend FastAPI.
// Produção: `vite build` gera frontend/dist, servido estaticamente pelo backend em "/central/" (29.54: o painel
// ganhou prefixo para poder ser exposto em https://<host>/central; `GET /` redireciona para lá).
// `--mode simulado` aponta o proxy para um backend SIMULADO em 127.0.0.1:8765 (aceite visual sem tocar a produção,
// que na máquina central é o 8000); `VITE_API_TARGET` no ambiente manda sobre os dois.
export default defineConfig(({ mode }) => ({
  // Vale para o build (URLs dos bundles e do favicon) e para o dev (o painel abre em http://127.0.0.1:5173/central/).
  base: '/central/',
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
    // O central de desenvolvimento divide a máquina com a suíte do backend, o PostgreSQL e os emuladores: com o padrão de 5 s, testes que
    // passam em 0,1 s sozinhos estouram o tempo quando a CPU está disputada (funil 60: 16 falhas, todas passam isoladas). O tempo maior só
    // custa quando o teste de fato falha; a asserção não muda.
    testTimeout: 30000,
    hookTimeout: 30000,
    // 29.150: `DESLOCAMENTO_DIAS=40 npm test` roda a suíte com o relógio 40 dias à frente (acha o teste com data fixa que
    // vence); sem a variável, nada muda. Ver src/test/relogioDeslocado.ts.
    setupFiles: process.env.DESLOCAMENTO_DIAS ? ['./src/test/relogioDeslocado.ts'] : [],
  },
}));
