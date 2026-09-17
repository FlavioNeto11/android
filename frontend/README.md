# Central de Aparelhos — frontend

Painel de controle (POC local) para até 10 emuladores Android operados por um agente de IA.
React + TypeScript + Vite, estado com zustand, ícones lucide-react, CSS escrito à mão (tokens + CSS Modules).
Sem UI kit, sem Tailwind, sem fontes ou scripts externos.

O contrato com o backend está em [`../docs/api-contract.md`](../docs/api-contract.md).
`src/api/types.ts` é uma cópia literal dos tipos desse arquivo — se o contrato mudar, recopie o bloco.

## Scripts

| Comando | O que faz |
|---|---|
| `npm install` | Instala as dependências (versões exatas; `package-lock.json` versionado). Node ≥ 22.12. |
| `npm run dev` | Servidor de desenvolvimento em `http://127.0.0.1:5173` (`strictPort`). |
| `npm run build` | `tsc --noEmit` (modo estrito) + `vite build` → `frontend/dist`. |
| `npm run typecheck` | Só a checagem de tipos. |
| `npm test` | `vitest run`: testes de unidade (node) + integração da UI inteira (jsdom, backend simulado). |
| `npm run test:watch` | Vitest em modo observação. |

## Como o proxy funciona

- **Desenvolvimento:** o Vite escuta em `127.0.0.1:5173` e repassa tudo que começa com `/api` para
  `http://127.0.0.1:8000`, inclusive o WebSocket (`ws: true`). O navegador só fala com `:5173`.
- **Produção:** `npm run build` gera `dist/`, que o backend serve em `/`. Como o código usa apenas URLs
  relativas (`/api/...`) e monta o WebSocket a partir de `window.location` (`ws(s)://<host>/api/ws`),
  nada muda entre os dois modos e nenhuma porta fica fixa no código.
- A navegação entre telas usa o hash da URL (`#/painel`, `#/execucoes`, `#/configuracao`, `#/diagnostico`),
  então o backend não precisa de regra de fallback para rotas.

## Estrutura

```
src/
  api/          types.ts (contrato, literal) · client.ts (fetch tipado, ApiError, dicas pt-BR) · ws.ts (socket, ping, foco)
  store/        reducer.ts (núcleo puro: snapshot + eventos idempotentes) · app.ts · live.ts (snapshot → WS, backoff,
                resync, detalhe da execução) · ui.ts (tela, seleção, hash, localStorage) · control.ts (leases) · toasts.ts
  lib/          coords.ts (mapPointToDevice) · gesture.ts · time.ts (relógio único + offset do servidor) · status.ts
                (enum → rótulo/tom/ícone) · idempotency.ts · backoff.ts · format.ts · ids.ts · storage.ts
  components/   primitivos: Button, Badge, StatusBadge, Card, Tabs, Dialog, Popover, Tooltip, Toasts, Skeleton,
                EmptyState, Banner, Disclosure, ProgressBar, Field, JsonTree, RecordTable, Confirm
  features/     topbar · command · devices · focus · runs · settings · diagnostics · painel
  styles/       tokens.css (design tokens) · base.css (reset, foco, movimento reduzido)
  test/         fixtures e backend/WebSocket falsos usados por app.integration.test.tsx
```

## Regras da camada de dados (resumo)

1. No carregamento e em **toda** reconexão: `GET /api/snapshot` → hidrata o store → abre
   `/api/ws?last_event_id=<snapshot.last_event_id>`.
2. Eventos persistidos são aplicados uma única vez (`id` ≤ último aplicado é ignorado); eventos efêmeros
   (`id: null`: `frame`, `metrics`, `health.updated`…) nunca são descartados por essa regra.
3. `{type:'resync'}` refaz o passo 1. Queda de conexão: backoff exponencial 1 s → 15 s com jitter; a tela mantém
   os últimos dados marcados como possivelmente desatualizados. Reconectar nunca cria nem reinicia execuções.
4. O `RunDetail` da execução selecionada vem por REST e é mantido por eventos (`attempt.updated` chega sem
   `actions`, que são preservadas); é recarregado em `plan.revised`, ao sair de `planning` e, com debounce,
   quando a execução termina.
5. `idempotency_key`: uma chave por intenção (texto + seleção + modo), reutilizada em cliques repetidos e novas
   tentativas, trocada só depois de uma resposta 2xx.
6. Entradas manuais enviam o `frame_id` do frame **exibido** (cabeçalho `X-Frame-Id` da imagem carregada) e
   coordenadas em pixels do aparelho (`FrameInfo.width/height`), nunca o tamanho natural do JPEG.

## Acessibilidade e visual

Tema escuro único, um acento (teal), contraste AA. Todo status combina ícone + texto + cor (`lib/status.ts`).
Foco visível em todos os controles, `prefers-reduced-motion` respeitado, modais sobre `<dialog>` nativo,
detalhes técnicos (ids, portas, JSON, argumentos) sempre recolhidos em “Detalhes técnicos”.
