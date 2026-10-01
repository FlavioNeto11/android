# Regras para os agentes da revisão de UX/UI, rodada 2 (leia antes de agir)

Você não lê o `CLAUDE.md` do projeto automaticamente. Estas são as regras que valem para você. A rodada 1 está em
`docs/revisoes-ux/` (relatórios 01 a 14 e `revisao-final.md`); o ADR-062 registra as decisões de produto que **não se
reabrem**: "Pendência" tem dona (a caixa `#/pendencias`), a URL é a fonte da verdade da tela, `planned` não é em andamento.

## Onde trabalhar
- Só no worktree que o orquestrador indicou no seu prompt (caminho absoluto), no branch `ux2/NN-<tema>`, que sai de
  `claude/ux-portal-2` (que sai da `main` com o portal já implantado em `83af733`). Nunca trabalhe em `C:\git\android`
  (checkout do ambiente central) e nunca dê `cd` para lá.
- `frontend/node_modules` é uma **junção** para o do checkout central. **Não** rode `npm install`/`npm ci`, **não**
  apague `node_modules` (apagar recursivo destrói o do ambiente central).
- Commits pequenos, convencionais, em português (`feat(painel): …`, `fix(…)`, `docs(ux): …`), terminando EXATAMENTE com
  a linha `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` (não a troque pelo nome do seu modelo).
  Faça push **só do seu branch** (`git push origin ux2/NN-<tema>`). **Nunca** push na `main`, nunca `git merge` na `main`,
  nunca `deploy.ps1`, nunca `npm run build` no checkout central, nunca reiniciar backend/tarefas (`farm-central`),
  nunca mexer em emulador, túnel, relógio, WSL, firewall ou configuração do Windows.
- Sem `git stash` sem tag, sem `rm -rf` com variável.

## Stack (já descoberta)
Vite 8 + React 19 + TypeScript 7 + zustand + CSS Modules à mão (tokens em `frontend/src/styles/tokens.css`; sem UI kit,
sem Tailwind, sem fonte/script externo) + lucide-react + vitest (unidade em node; integração jsdom em
`src/app.integration.test.tsx`). Contratos que já existem e **não se duplicam**: rotas em `src/lib/rotas.ts`
(`hashDe`/`parseHash`) e `src/store/ui.ts` (`rota`, `navegar`, `trocarQuery`, `voltarPara`); números em
`src/store/metricas.ts`; visão cartões/lista/tabela em `src/lib/visao.ts`; foco ao conteúdo em `src/lib/scroll.ts`
(`focarConteudo`); tempo relativo em `src/lib/time.ts`; rótulos em `src/lib/rotulos.ts`; selos em
`src/features/devices/selos.ts`; barra de listagem em `src/components/BarraListagem.tsx`; Pendências em
`src/features/pendencias/`. Documentação do produto: `docs/produto.md`.

## Segurança do ambiente (inegociável)
- **Nenhuma ação com efeito real**: nunca dispare Executar, Planejar com IA paga, Resetar dados, Instalar app,
  Iniciar/Parar/Hibernar/Reiniciar aparelho, Assumir controle, Excluir/Marcar bloqueada persona, Promover versão, salvar
  configuração, cancelar execução, aprovar/recusar. Valide a interface e o fluxo de confirmação até o botão de confirmar,
  **sem confirmar**, e o resto pelos testes vitest.
- Segredo nunca em código, teste, log, captura. Não leia nem imprima `.env`/`config.yaml`. Chamada paga de IA: proibida.
- **Login é proibido** no central (`http://127.0.0.1:8000`). O navegador do orquestrador e o Chrome do dono não são seus:
  use só o painel do navegador da IDE (`mcp__Claude_Browser__*`), **sempre passando o `tabId` da sua aba**.
- **Telas autenticadas: use o backend SIMULADO do seu worktree** (precedente em
  `docs/auditoria-ux-2026-09-27/evo2-aceite.md` e `docs/revisoes-ux/13-prova-simulada.md`): `AI_PROVIDER=simulated`,
  `POC_CONFIG` e `POC_DB_PATH` numa pasta de rascunho FORA do repo (seu scratchpad), config novo a partir de
  `config/config.example.yaml`, `base_console_port: 5640`, `appium.autostart: false`, `android.sdk_root` apontando
  para uma pasta que não existe, **porta do backend 87NN** (a do seu prompt) e o venv do checkout central
  (`C:\git\android\backend\.venv\Scripts\python.exe`, sem criar venv). O frontend: `npx vite --port 51NN` com
  `VITE_API_TARGET=http://127.0.0.1:87NN` (portas do seu prompt). **Abra sempre por `http://localhost:51NN`, nunca por
  `127.0.0.1`**: o cookie de sessão não distingue porta e `127.0.0.1` é o host do central (RF-47). Se uma porta estiver
  ocupada por outra sessão, não mate o processo: registre `not_run`. Pare tudo o que você iniciou ao terminar.
- Leitura do central real só para consultar (GET); não há necessidade de abri-lo no navegador.
- **Carga do host**: outros trabalhos rodam (emuladores com contas reais, testes do notebook). UM trabalho pesado por
  vez no seu lado: só os testes afetados durante o trabalho; `npm run typecheck` e `npm test` inteiros **uma vez**, no
  fim; nunca dois vite/vitest seus ao mesmo tempo; se o host apertar (CPU alta), pare e espere.
- Ferramentas de acessibilidade já instaladas FORA do repo em `C:\temp\ui-verificar` (axe-core 4.13, playwright-core,
  lighthouse 13.5; Chrome em `C:\Program Files\Google\Chrome\Application\chrome.exe`). Para o axe numa tela autenticada,
  injete `axe.min.js` pelo `javascript_tool` do painel da IDE; `node scripts/ui-verificar.mjs` **não faz login** (só
  alcança a tela de entrada) e `scripts/ui-auditoria.js` mede fonte/alvos/contraste pelo DOM. Não instale nada no repo.

## Texto e provas
- Todo texto visível em português do Brasil, sem jargão técnico cru. Glossário: Persona, Aparelho, Execução,
  Aplicativo, Servidor, Pendência.
- Prova tem três níveis e não se misturam: `real` (data, máquina, commit, o que foi feito no navegador contra o backend
  simulado ou em leitura), `simulated` (`arquivo::teste`), `not_run`. Falha ou incerteza nunca contam como sucesso.
- Mudanças pequenas; não refatore o que não é da tarefa. Se o briefing contradisser o código, siga o código e registre
  a divergência. As medições dos briefings vêm do build implantado em `83af733`: **reconfirme a linha de base no seu
  branch antes de mexer** e registre a diferença.
- Verificação visual em 1440, 1024, 768 e 390 px.

## Entrega (obrigatória)
1. `npm run typecheck` e os testes afetados verdes; `npm test` inteiro no fim (relate o número).
2. Relatório em `docs/revisoes-ux/rodada-2/NN-<tema>.md`: o que mudou, arquivos, decisões, divergências do briefing,
   antes/depois das medições, provas (real/simulated/not_run), o que ficou de fora.
3. Commit + push do seu branch. Responda ao orquestrador com: branch, hash, resumo de 10 linhas, arquivos tocados,
   pendências. Não altere `CHANGELOG.md`, `docs/estado-atual.md` nem ADR (o orquestrador consolida no fim).
4. Só toque nos arquivos da **sua posse** (mapa abaixo). Se precisar de outro, faça a menor mudança possível e liste-a
   no relatório como "toque em arquivo alheio".

## Mapa de posse de arquivos
| Tarefa | É dono de | Pode tocar minimamente |
|---|---|---|
| 10 cabeçalho mobile | `features/topbar/TopBar*`, `SaudeAmbiente*`, `App.module.css` (cabeçalho) | `MenuLateral*` só para o hambúrguer |
| 11 truncamento | `components/TruncatedText*` (novo), CSS de elipse em `features/infra/*`, `apps/*`, `runs/*` (lista), `loja/*` | — (o **objetivo do painel de execução** é da 14) |
| 12 detalhe da persona | `features/profiles/ProfileDetail*`, `Guia*`, `PersonaHeader` (novo), `lib/rotas.ts` e `store/ui.ts` só para o slug | `features/profiles/ListaDePersonas*` só para o link com slug |
| 13 acessibilidade residual | `features/topbar/MenuLateral*`, `features/focus/Drawer*`, `styles/*`, alvos em `features/infra/*` e `diagnostics/*` | depois da 10 |
| 14 execução e pendências | `features/runs/RunView*`, `features/runs/RunsPage*` (painel de detalhe), `features/pendencias/*`, `features/aprendizado/*`, `store/metricas.ts` (contador) | — |
| 15 revalidação | só `docs/revisoes-ux/rodada-2/revalidacao-final.md` | não corrige nada |
