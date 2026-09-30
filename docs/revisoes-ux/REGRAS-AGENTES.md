# Regras para os agentes da revisão de UX/UI (leia antes de agir)

Você não lê o `CLAUDE.md` do projeto automaticamente. Estas são as regras que valem para você.

## Onde trabalhar
- Só no worktree que o orquestrador indicou no seu prompt (caminho absoluto), no branch `ux/NN-<tema>`, que sai de
  `claude/ux-portal`. Nunca trabalhe em `C:\git\android` (checkout do ambiente central) e nunca dê `cd` para lá.
- `frontend/node_modules` já é uma junção. **Não** rode `npm install`/`npm ci`, **não** apague `node_modules` (apagar
  recursivo destrói o do ambiente central).
- Commits pequenos, convencionais, em português (`feat(painel): …`, `fix(…)`, `docs(ux): …`), terminando com
  `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Faça **push só do seu branch** (`git push origin ux/NN-<tema>`).
  **Nunca** push na `main`, nunca `git merge` na `main`, nunca `deploy.ps1`, nunca `npm run build` no checkout central
  (o `dist` do central não muda), nunca reiniciar backend/tarefas (`farm-central`), nunca mexer em emulador, túnel,
  relógio, WSL.
- Não use `git stash` sem tag, não use `rm -rf` com variável.

## Stack (já descoberta)
Vite 8 + React 19 + TypeScript 7 + zustand + CSS Modules à mão (tokens; sem UI kit, sem Tailwind, sem fonte/script
externo) + lucide-react + vitest (unidade em node; integração jsdom em `src/app.integration.test.tsx`).
Rotas por hash em `frontend/src/store/ui.ts` (`view`, `bindHashRouting`), telas em `frontend/src/features/*`,
estado do backend em `src/store/app.ts`/`reducer.ts`/`live.ts`. Contrato de rotas: `frontend/src/lib/rotas.ts`
(fonte única; use `hashDe`/`parseHash`, nunca `#/…` à mão). Documentação do produto: `docs/produto.md`.

## Segurança do ambiente (inegociável)
- **Nenhuma ação com efeito real**: nunca clique/dispare Executar, Planejar com IA paga, Resetar dados, Instalar app,
  Iniciar/Parar/Hibernar/Reiniciar aparelho, Assumir controle, Excluir/Marcar bloqueada persona, Promover versão, salvar
  configuração, login em conta. Em testes visuais contra o backend vivo use **somente leitura** (navegar, abrir
  drawers, filtrar, redimensionar). O que exigir escrita você valida pelos testes (vitest com backend simulado) e
  pelo fluxo de confirmação até o botão de confirmar, **sem confirmar**.
- Segredo nunca em código, teste, log, captura. Não leia nem imprima `.env`/`config.yaml`.
- Chamada paga de IA: proibida.
- Dev server: `cd <worktree>/frontend && npx vite --port <PORTA>` com `VITE_API_TARGET=http://127.0.0.1:8000`
  (leitura). A porta é a do seu prompt (uma por tarefa); `strictPort` — não use 5173/8000. Pare o servidor ao terminar.
- Carga do host: outros trabalhos rodam (emuladores). Rode só os testes afetados durante o trabalho
  (`npx vitest run <arquivo>`), a suíte `npm test` uma vez no fim, e prefira prioridade baixa
  (`powershell -Command "Start-Process -Wait -NoNewWindow -FilePath npx.cmd -ArgumentList 'vitest','run' "` não é
  obrigatório; apenas não abra vários vite/vitest ao mesmo tempo).

## Texto e provas
- Todo texto visível em português do Brasil, sem jargão técnico cru. Glossário: Persona, Aparelho, Execução,
  Aplicativo, Servidor, Pendência.
- Prova tem três níveis e não se misturam: `real` (data, máquina, commit, o que foi feito no navegador contra o
  backend vivo em leitura), `simulated` (`arquivo::teste`), `not_run`. Falha ou incerteza nunca contam como sucesso.
- Faça mudanças pequenas e revisáveis; não refatore o que não é da tarefa. Se o briefing contradisser o código,
  siga o código e registre a divergência.
- Verificação visual em 1440, 1024 e 390 px (ferramentas `mcp__Claude_Browser__*`: `preview_start {url}`/`navigate`,
  `resize_window`, `read_page`, `computer screenshot`). Se a ferramenta não estiver disponível para você, diga
  `not_run` no relatório em vez de inventar.

## Entrega (obrigatória)
1. `npm run typecheck` e os testes afetados verdes; `npm test` inteiro no fim (relate o número).
2. Relatório em `docs/revisoes-ux/NN-<tema>.md` (no seu worktree): o que mudou, arquivos alterados, decisões,
   divergências do briefing, provas (real/simulated/not_run), o que ficou de fora.
3. Commit + push do seu branch. Responda ao orquestrador com: branch, hash do commit, resumo de 10 linhas, arquivos
   tocados, pendências. Não altere `CHANGELOG.md` nem `docs/estado-atual.md` (o orquestrador consolida no fim).
4. Só toque nos arquivos da **sua posse** (mapa abaixo). Se precisar de um arquivo de outra posse, faça a menor
   mudança possível e liste-a no relatório como "toque em arquivo alheio".

## Mapa de posse de arquivos
| Tarefa | É dono de | Pode tocar minimamente |
|---|---|---|
| 01 rotas e menu | `store/ui.ts`, `App.tsx`, `App.module.css`, `features/topbar/TopBar*`, `lib/sections.ts`, `lib/rotas.ts` | ligação de rota em `features/profiles`, `runs`, `focus`, `apps` (só ler/gravar a URL) |
| 02 números e saúde | `store/app.ts` e seletores novos (`store/metricas.ts`), `features/infra/*`, `features/diagnostics/*`, componente novo `features/topbar/SaudeAmbiente.tsx` | `TopBar.tsx` (só trocar o popover de saúde por `SaudeAmbiente`) |
| 03 barra e drawer | `features/focus/*`, `features/command/*`, barra de seleção em `features/painel/*` | — |
| 04 textos e cards | `lib/rotulos.ts` (novo), cards de aparelho em `features/devices/*`, `features/painel/*` (só a grade) | depois de 03 |
| 05 busca e tabela | `features/profiles/*`, `features/runs/*`, componente novo `components/BarraListagem*` | — |
| 06 apps e pendências | `features/apps/*`, `features/settings/*`, `features/aprendizado/*`, `features/pendencias/*` (novo) | depois de 05 |
| 07 tipografia e a11y | `styles/*` (tokens), CSS global | toques de CSS em componentes só para alvo/contraste |
| 08 varredura de textos | só strings | depois de 04 e 06 |
| 09 revisão | só `docs/revisoes-ux/revisao-final.md` | não corrige nada |
