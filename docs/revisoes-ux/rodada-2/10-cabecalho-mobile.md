# Rodada 2, tarefa 10: cabeçalho compacto no celular e no tablet

Branch `ux2/10-cabecalho-mobile` (base `claude/ux-portal-2`, commit `085e34e`). Data: 01/10/2026. Máquina: a central de
desenvolvimento (Windows), navegador do painel da IDE contra um **backend simulado** do worktree. Nada foi aberto no
central real (`8000`).

## O que mudou

| Faixa | Antes | Depois |
|---|---|---|
| Celular, < 768 px | Duas faixas: marca, modelo de IA, conexão e operador em cima; semáforo, cinco contadores e custos empilhados embaixo. | **Uma linha de 56 px**: botão Menu (com o selo de pendências), marca, semáforo de saúde (cor, texto e contador intactos) e botão **Resumo**. O Resumo abre um painel com Capacidade (online, execuções, aguardando você, CPU, RAM), Custos, IA e conexão, e Sessão (com "Sair"). |
| Tablet, 768 a 1023 px | Duas faixas, com os rótulos "Saúde/Capacidade/Custos" quebrando a régua em três linhas. | Duas faixas, a de baixo numa linha só: semáforo, online, execuções, aguardando você e o chip **Recursos** (CPU, RAM e custos num popover). Rótulos de grupo escondidos (os grupos têm `aria-label`). |
| ≥ 1024 px | Régua inteira. | **Igual** (medido, ver abaixo). |

Alvos de toque abaixo de 1024 px: 40 px de altura (`--hit-touch`) em marca, semáforo, chips, contadores, ícones de
aviso e no "Reconectar". Acima de 1024 px nada mudou (32 px, como antes).

### Arquivos

- `frontend/src/features/topbar/TopBar.tsx`: `TopBar` escolhe a composição pela faixa; novos `BotaoMenu` (selo de
  pendências), `ResumoCelular`, `Recursos`, `AiResumo`, `SessaoDetalhes`; `Counters` ganhou `partes` e `painel`;
  `Custos` ganhou `painel`; o corpo do popover da IA virou `AiDetalhes` (usado nos dois lugares); `ConnectionIndicator`
  ganhou a variante `linha`.
- `frontend/src/features/topbar/TopBar.module.css`: linha compacta, painel, selo, alvos de toque.
- `frontend/src/features/topbar/SaudeAmbiente.tsx` e `.module.css`: o texto do rótulo ganhou um `span` (reticências antes
  de estourar a linha) e o gatilho vai a 40 px abaixo de 1024 px. O texto e o nome acessível são os mesmos (o RF-44 e
  os testes seguem valendo).
- `frontend/src/features/topbar/useFaixa.ts` (novo): `useFaixaDoCabecalho()` (`celular` / `tablet` / `largo`).
- `frontend/src/features/topbar/TopBarCompacto.test.tsx` (novo): 7 testes.
- **Toques em arquivo alheio: nenhum** (`MenuLateral*` e `App.module.css` ficaram como estavam).

## Decisões e divergências do briefing

1. **Uma só árvore por faixa, em JS (`matchMedia`), não CSS que esconde.** Esconder por CSS deixaria duas cópias do
   semáforo e dos contadores no DOM (nome acessível duplicado, hooks em dobro, testes ambíguos). O hook decide o que
   existe; o CSS decide a aparência. Sem `matchMedia` (jsdom) vale `largo`, então os testes antigos não mudaram.
2. **Selo de pendências no botão Menu.** O briefing pede que "o contador de pendências do menu continue visível em todas
   as larguras". Com a gaveta fechada ele sumia, e a régua com "aguardando você" também some no celular. O botão
   "Menu" agora leva o selo com o MESMO total da caixa de Pendências (`usePendencias().total`, D1) e o diz no nome:
   "Menu, 2 aguardando você". Zero não vira selo. O Resumo também mostra o contador, e seu rótulo começa por "Resumo"
   (WCAG 2.5.3) e cita as pendências.
3. **IA, conexão e operador saíram da linha** e foram para o Resumo (não cabiam em 390 px). Não há popover dentro do
   painel (o clique num popover filho contaria como "fora" do pai): o modelo de IA vira um detalhe expansível
   (`Disclosure`) com o mesmo corpo do popover do desktop, e a sessão vira texto com o botão "Sair". O painel mostra por
   escrito o que o tooltip dizia (custos: mensagem de cada conta e a nota "valores estimados"), porque no toque não há
   passar o mouse.
4. **Nenhum cálculo novo.** O painel monta `Counters`, `Custos` e `AiDetalhes` com os mesmos hooks (`store/metricas`,
   `usePendencias`, `lib/aiBalance`). O teste compara o texto dos indicadores do Resumo com o da régua larga.
5. **Limiares.** Celular < 768 px; tablet 768 a 1023 px (mesmo corte do menu lateral, 1024 px). O nome "Central de
   Aparelhos" some abaixo de 640 px e a marca abaixo de 350 px; o ícone do "Resumo" some abaixo de 381 px. Cada corte foi
   medido para que "Ambiente em atenção N" (o rótulo mais longo) caiba **sem reticências** em 360 e 390 px.
6. **Alvos de 40 px** valem abaixo de 1024 px (largura), não por `pointer: coarse`: é determinístico e o emulador do
   painel não garante o ponteiro.
7. **A linha de base do briefing se reproduziu**: ~245 px a 390 px. Mas **só com custos**: o backend simulado não tem
   saldos, então semeei dois (`POST /api/ai/balances/{conta}`, no banco de rascunho) para o grupo "Custos" aparecer.
   Sem saldos, a linha de base é 214 px (25%).
8. **Falta de aviso de conexão na linha.** Antes, o estado "Conectado/Reconectando" ficava sempre visível. Agora mora no
   Resumo. Quando a conexão cai, o `ConnectionBanner` do conteúdo continua avisando (não toquei nele), mas a linha não
   muda de cor. Fica como possível melhoria (um ponto de alerta no "Resumo").
9. **Um painel, não uma folha de tela cheia.** O painel do Resumo usa o `Popover` existente (largura até 380 px). Com os
   custos e a IA expandida ele chega a 560 px e rola por dentro; o "Sair" fica no fim.

## Antes e depois (medidos)

Backend simulado (`AI_PROVIDER=simulated`, porta 8710) com 2 saldos baixos semeados; Painel; altura do `<header>`.

| Largura | Antes (`085e34e`) | Depois | Observação |
|---|---|---|---|
| 390 px | **254 px (30,1%)**: 56 + 197 | **56 px (6,6%)** | o conteúdo começa em y=56 (antes, 254); sem rolagem horizontal |
| 360 px | não medido | 56 px | cabe sem reticências ("em atenção") |
| 640 e 767 px | não medido | 56 px | |
| 768 px | 166 px (16,2%): 56 + 109 | **106 px (10,4%)**: 56 + 49 | régua numa linha |
| 1023 px | não medido | 106 px | |
| 1024 px | 132 px: 56 + 75 | 132 px: 56 + 75 | **idêntico** |
| 1440 px | 98 px: 56 + 41 | 98 px: 56 + 41 | **idêntico** |

Alvos do cabeçalho com menos de 40 px: 9 em 390 e 768 px (antes) para **0** (depois). Em 1024 e 1440 px, os
mesmos 9 de 32 px de antes (inalterados de propósito).

Acima da dobra em 390 px (rota, primeiro título, posição em y): Painel "Painel" em 75 e "Comando" em 93; Personas
"Personas" em 76; Execuções "Execuções" em 76. Sem rolagem horizontal da página em nenhuma das três.

Painel do Resumo em 390 px: caixa de 351 x 560 px (x de 29 a 380), todos os alvos internos com 40 px ("execuções",
"aguardando você", "Modelo de IA", "Sair").

## Provas

**real** (01/10/2026, central de desenvolvimento, commit-base `085e34e` mais a árvore de trabalho, navegador do painel
da IDE contra o backend simulado do worktree, `http://[::1]:5110` → `127.0.0.1:8710`):
- medidas de altura e de alvos nas larguras da tabela acima, por script no DOM;
- Resumo e Recursos abertos e lidos (texto, posição, alvos); Esc fecha e devolve o foco ao botão;
- **axe-core 4.13** (`wcag2a/aa`, `wcag21a/aa`, `wcag22aa`, `best-practice`) em 390 px no cabeçalho, no painel do Resumo
  (fechado e com o detalhe da IA aberto) e em 768 px no cabeçalho e no painel do Recursos: **0 violações em todos**,
  incluindo `color-contrast`.

**simulated**:
- `frontend/src/features/topbar/TopBarCompacto.test.tsx::Cabeçalho — celular (< 768 px)` (3 testes: linha única; mesmos
  indicadores que a régua larga; custos, IA, conexão e sessão no painel, sem popover aninhado; rótulo do botão),
  `::Botão "Menu" — selo de pendências (D1)`, `::Cabeçalho — tablet (768 a 1023 px)`, `::Cabeçalho — largo (≥ 1024 px)`.
- `npm run typecheck`: limpo. `npm test` inteiro: **86 arquivos, 1034 testes, todos verdes**.

**not_run**:
- toque real em aparelho (só emulação de viewport; o ponteiro não é `coarse`);
- Lighthouse nesta tarefa (só o axe, que o briefing aceita);
- verificação visual por captura em 1440 e 1024 px: as capturas do painel saíram instáveis (cortadas ou desatualizadas
  com o painel oculto), então 1440 e 1024 foram provados por **medida de DOM idêntica** à de antes, não por imagem. Em
  390 e 768 px, vi o painel em captura além das medidas;
- o central real (8000), o tema claro (o produto é só escuro) e o foco por teclado dentro do painel do Resumo além do
  que o `Popover` já fazia (Esc e Tab para dentro).

## Armadilhas do procedimento (para quem repetir)

- O **escratch da sessão é compartilhado** entre os agentes em paralelo: a tarefa 12 sobrescreveu meu `mkcfg.py`.
  Usei uma pasta própria (`sim10/`) e um script com nome próprio.
- O **cookie de sessão não distingue porta** (RF-47): a tarefa 12 (backend 8712, `localhost:5112`) e esta (`localhost:5110`)
  se desconectariam mutuamente. Abri o portal por **`http://[::1]:5110`** (vite escuta em `::1`), que é outro host de
  cookie e é aceito pelo backend como loopback (precisa estar em `allowed_origins`). Não use `127.0.0.1` (é o central).
- No painel oculto da IDE, o `matchMedia` só dispara depois de um quadro renderizado: após redimensionar, tire uma
  captura (mesmo que dê "timeout") antes de medir, senão o cabeçalho parece "preso" na faixa anterior.
- Os processos que iniciei (vite 5110, backend 8710, servidor estático do axe 8741) foram parados; a porta 8000 seguiu
  com o dono de antes (PID 49680).

## O que ficou de fora

- Sidebar/hambúrguer da gaveta: é a tarefa 13. Aqui só o botão "Menu" na linha única, mais o selo.
- Ponto de alerta de conexão na linha (item 8 acima).
- Folha de tela cheia para o Resumo (item 9).
- `docs/produto.md`, `CHANGELOG.md`, `estado-atual.md` e ADR: o orquestrador consolida.
