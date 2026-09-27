# Auditoria de usabilidade do painel — 27/09/2026

**Base:** `origin/main` em `9276d63` (fases A–I, H2 e K2 da evolução arquitetural integradas; J e K1 ainda fora).
**Pedido do dono (27/09):** antes de encerrar a evolução, conferir se o que foi construído no painel faz sentido em
termos de frontend, experiência do usuário, layout e otimização, e corrigir o que não fizer.
**Quem executa as correções:** a sessão "Evolução Android multiagentes", como uma fase própria (ver [§7](#7-plano-de-correção-fase-l--usabilidade)).

## 1. Método e níveis de prova

| O que | Como | Prova |
|---|---|---|
| Verificações automáticas | `npm ci`, `npm run typecheck`, `npm test`, `npm run build` em checkout limpo de `9276d63` | `simulated` (harness) |
| Capturas de tela | backend em modo simulado (Python 3.13, `AI_PROVIDER=simulated`, `skills.enabled: true`, 4 aparelhos falsos da suíte com prévia ligada), painel servido do `dist` recém-gerado, Playwright em 1440×900 e 390×844, 93 PNGs | `simulated` (nenhum aparelho nem IA real) |
| Leitura de código | todas as pastas de `frontend/src/features` e `frontend/src/components`, com atenção às partes novas (`training/TeachingPanel.tsx`, bloco "Habilidades" de `settings/FlowsRecipesSection.tsx`, integração em `training/TrainingReview.tsx`) | leitura; os achados citam `arquivo:linha` em `9276d63` |

Cinco capturas de apoio ficam em [`capturas/`](capturas/). As demais foram geradas na sessão de revisão e não entram
no Git (15 MB); qualquer uma se regenera com o mesmo arranjo (backend simulado + Playwright).

## 2. Resultado das verificações automáticas

| Verificação | Resultado |
|---|---|
| `npm run typecheck` | limpo |
| `npm test` (vitest 5) | 42 arquivos, **480 testes, todos verdes**, 22,6 s |
| `npm run build` (vite 8) | ok, com **1 aviso**: chunk acima de 500 kB |
| `dist/` | **912 kB** em 3 arquivos: `index-*.js` **789 kB** (gzip 233 kB), `index-*.css` 122 kB (gzip 22 kB) |
| Console no navegador | nenhum `pageerror`, nenhum `warning`, nenhum 5xx. Único 4xx: `GET /api/instagram/profiles/<id>/avatar` → 404 para perfil sem avatar, em Perfis e em Configuração › Instâncias e contas |

## 3. Achados que bloqueiam o uso (P1)

| # | Onde | O que acontece | Evidência | Correção |
|---|---|---|---|---|
| P1.1 | `frontend/src/styles/tokens.css:104` (`--focus-panel-w: clamp(600px, 44vw, 920px)`), `features/focus/Focus.module.css:2-5` (`flex: none`), `:102` (grade `minmax(0,1fr) 300px`); nenhum `@media` no arquivo | Em celular o Foco tem 600 px de largura mínima, é recortado à direita pelo `overflow: clip` do app e reduz o `main` a zero. Os botões do painel só respondem a clique via DOM | [`capturas/390-foco.png`](capturas/390-foco.png) | Abaixo de 720 px, o Foco vira tela cheia (`position: fixed; inset: 0`), uma coluna, com botão "Voltar" no topo; acima, mantém o `clamp`, mas com `44vw` mínimo de `min(600px, 100vw)` |
| P1.2 | `features/training/TeachingPanel.tsx:149` manda publicar em "Configurações → Fluxos e receitas → Habilidades"; `features/settings/FlowsRecipesSection.tsx:171-212` é só leitura; `api/client.ts` não tem chamada de status; a aba se chama "Configuração" (`features/topbar/TopBar.tsx:49`) | Quem ensina uma habilidade chega a um beco sem saída: o rascunho existe, mas não há como publicar, desabilitar, reverter ou descartar pelo painel. A API tem tudo isso (`backend/app/modules/skills/presentation/router.py:196,204,212,287`) | [`capturas/1440-config-fluxos.png`](capturas/1440-config-fluxos.png) | Na lista "Habilidades": ações por versão com `confirm()` — Publicar (draft/validated → published), Desabilitar (published → disabled), Reverter (rollback) — e Descartar no ensino; agrupar por `skill_id` mostrando a versão publicada e a última; corrigir o texto para "Configuração" |
| P1.3 | `features/apps/AppsPage.tsx:82-91`, `features/loja/LojaPage.tsx:36-42`, `features/profiles/ProfilesPage.tsx:72-78` | Falha de carga vira lista vazia: o painel diz "Nenhum aplicativo cadastrado" / "Nenhum perfil cadastrado" quando na verdade a API falhou. Só um toast passageiro conta a verdade | leitura | Guardar o erro no estado e mostrar `EmptyState` de erro com "Tentar de novo" (o padrão já existe em `FlowsRecipesSection.tsx::ListError`) |
| P1.4 | `features/training/TrainingReview.tsx:40-41` e `:195` | Se perfis ou grupos falham ao carregar, a lista fica vazia em silêncio ao lado de "Nada marcado = todos os perfis": a pessoa pode salvar a habilidade para todos os perfis sem saber | leitura | Mostrar o erro no bloco de escopo, com "Tentar de novo", e bloquear "Salvar" (`disabledReason`) até a carga terminar |
| P1.5 | `features/training/TeachingPanel.tsx:36-43` (`.catch(() => undefined)`, sem indicador de carga) | Enquanto carrega, ou se a carga falhar, aparece "Gerar candidata de habilidade"; o clique cria uma **segunda** sessão de ensino para a mesma gravação | [`capturas/1440-treinamento-revisao-dialogo.png`](capturas/1440-treinamento-revisao-dialogo.png) | `LoadingRegion` na carga; erro com "Tentar de novo"; só oferecer "Gerar" depois de saber que não há ensino |

## 4. Achados de atrito (P2)

| # | Onde | O que acontece | Correção |
|---|---|---|---|
| P2.1 | `TrainingReview.tsx:108,114` ("Salvar habilidade" salva um **fluxo**), `ProfileDetail.tsx:76,1091` (aba "Habilidades" = fluxos e receitas), `TeachingPanel.tsx:81` (habilidade versionada) | "Habilidade" tem três sentidos no mesmo painel; na revisão do treino há dois "salvar" lado a lado com significados diferentes, e dois botões primários no mesmo diálogo ("Pedir proposta à IA" e "Salvar habilidade") | Reservar "habilidade" para a versionada. Rodapé: "Salvar como fluxo"; aba do perfil: "Fluxos e receitas"; um único botão primário por diálogo (o ensino v2 como caminho principal quando o flag está ligado, o fluxo como secundário) |
| P2.2 | `TeachingPanel.tsx:33` (um único `ocupado`), `:70` (validação que não compila volta sem aviso), `:115,121` (erros e riscos com fundo accent), `:91` (status da candidata em inglês), `:125` (pergunta com `text` nulo vira só ícone), `:127` (aria-label com o id numérico), `:72,149` (`result_version_id` cru), `:90` ("virou rascunho" em success; em Configuração o mesmo rascunho é neutral) | Estados e tons destoam do resto do painel | Busy por ação; `Banner` danger quando não compila e warning para riscos; mapa de rótulos em português para o status da candidata; tons iguais nas duas telas; aria-label com o texto da pergunta |
| P2.3 | `features/settings/Settings.module.css:27` (`minmax(360px,1fr)`), `:444` (`minmax(420px,1fr)`), `:81,428` (2 colunas fixas), `features/settings/AiSection.tsx:132` (tabela sem `tableWrap`) | Em 390 px as grades passam dos ~324 px úteis e são cortadas pelo `overflow-x: hidden` do `main` | `minmax(min(100%, Npx), 1fr)` (o padrão já usado em `Settings.module.css:536`), `.tableWrap` na tabela de papéis de IA |
| P2.4 | `features/devices/Devices.module.css:42` (`repeat(2, …)` sem `@media`), `:376-383` (`.foot` sem `flex-wrap`); nenhum `@media` em `Devices`, `Diagnostics`, `Infra` e `Releases` | Em celular os cartões de aparelho têm ~170 px: textos truncam ("observado · texto …", "@r…") e os botões do rodapé transbordam | Uma coluna abaixo de 560 px; `flex-wrap` no rodapé; mesma passada em Diagnóstico (`.grid` 2 colunas, `.eventRow 70px 150px 1fr`), Infra e Releases. Evidência: [`capturas/390-aparelhos.png`](capturas/390-aparelhos.png) |
| P2.5 | `features/topbar/TopBar.tsx:214` (`total: Math.max(10, list.length)`), `TopBar.module.css:349-351` | O contador mostra "4 /10" com 4 aparelhos (o 10 é fixo); em 390 px os contadores se sobrepõem ("EXECUÇÕESBLOQUEADAS") e "Configuração" e "Diagnóstico" somem da navegação sem pista | Total = aparelhos cadastrados; contadores em duas linhas ou só os dois principais abaixo de 560 px; indicador de rolagem (gradiente) na navegação. Evidência: [`capturas/390-topbar.png`](capturas/390-topbar.png) |
| P2.6 | `features/training/TrainingBar.tsx:107` ("Descartar" sem confirmação), `TrainingReview.tsx:105,207` e `components/Dialog.tsx:55-57` (clique no fundo descarta edições), `features/devices/CommandTrail.tsx:219-230` ("Marcar como concluído/falhou" sem confirmação, enquanto `runs/runActions.ts:81-112` pede) | Ações destrutivas sem confirmação, e inconsistentes entre telas | `confirm()` quando há edição ou efeito irreversível |
| P2.7 | `FlowsRecipesSection.tsx:105` ("Atualizar" não recarrega habilidades), `:151` (`defaultOpen` fixo, sem `useSectionOpen`), `:193` ("conteúdo alterado" sem explicação), `:203` (`app: <id>` cru; na lista de fluxos o nome é resolvido), `:162-169` (Badge sem ícone; receitas usam `StatusBadge`) | O bloco novo não segue os padrões do bloco vizinho | Recarregar junto; lembrar a seção aberta; `title` explicando o hash; nome do app; `StatusBadge` |
| P2.8 | Receitas: `FlowsRecipesSection.tsx:416` (`tableWrap` com rolagem horizontal) | Mesmo em 1440 px a coluna "Ações" fica fora da vista ("Pôr em quar…") e nada indica que há rolagem | Coluna de ações fixa à direita (`position: sticky`) ou ações num menu; sombra indicando rolagem. Evidência: [`capturas/1440-config-fluxos.png`](capturas/1440-config-fluxos.png) |
| P2.9 | Valores crus em inglês: `features/devices/OperationalContextCard.tsx:78-119` (`device.state`, `readiness.phase`, `stream.status`, `connectivity.state`, `session.status`), `features/apps/AppsPage.tsx:250-293`, `ProfileDetail.tsx:1097`, `TeachingPanel.tsx:91` | Estados aparecem como enum | Um mapa `StatusMeta` por enum, com rótulo em português e tom, no mesmo lugar dos que já existem (`flowsRecipes.ts:35-37`) |
| P2.10 | "aparelho" × "instância" na mesma tela: `features/devices/DeviceGrid.tsx:71/111/149/209`, `features/command/CommandPanel.tsx:162/330/419`, `features/focus/FocusPanel.tsx:143/155`, `features/settings/InstancesSection.tsx:131/146/237`, `features/runs/RunView.tsx:199/234` | Dois nomes para a mesma coisa | Fixar "aparelho" na interface (é o nome do produto: "Central de Aparelhos"); "instância" só em identificadores técnicos |
| P2.11 | Carregamento eterno em erro: `TrainingReview.tsx:39,112`, `ProfileDetail.tsx:1298,1328`, `AppsPage.tsx:144-147`; "Carregando…" em texto puro: `ProfileAccounts.tsx:130`, `settings/ServersLimits.tsx:42` | Sem `Skeleton`/`LoadingRegion` nem saída do estado de carga | `LoadingRegion` + `EmptyState` de erro com "Tentar de novo" |

## 5. Desempenho do painel (P3, mas barato)

| # | Onde | O que acontece | Correção |
|---|---|---|---|
| P3.1 | `vite build`: um chunk de 789 kB; nenhum `React.lazy`/`import()` em `src` | Todo o painel carrega de uma vez, inclusive Diagnóstico, Infra, Releases e Treinamento | `React.lazy` por view em `App.tsx` (7 views) e nos diálogos pesados (`TrainingReview`, `ReleasesPage`); meta: chunk inicial < 350 kB |
| P3.2 | Assinaturas amplas do store: `TopBar.tsx:204` (`s.instances` inteiro; o evento `frame` cria mapa novo em `store/reducer.ts:381-385`), `CommandPanel.tsx:67`, `RunsPage.tsx:27`, `InfraPage.tsx:48-57`, `DeviceCard.tsx:191` (`s.workers` inteiro anula o `memo`) | Barra do topo, comando e infra re-renderizam a cada frame de qualquer aparelho | Seletores derivados (contagens, ids) com `useShallow`; `serverHintOf` com seletor por worker |
| P3.3 | `useNow()` no topo da página: `InfraPage.tsx:61`, `OperationalContextCard.tsx:41`; `Screen.tsx:421-423` (role=status mudando a cada segundo) | Páginas inteiras re-renderizam a cada 1 s; leitores de tela anunciam o "há N s" repetidamente | `useNow` só no componente que mostra tempo relativo; `aria-live="off"` no contador de idade do frame |
| P3.4 | Polling sem pausa com a aba oculta: `settings/ServersLimits.tsx:35` (10 s), `command/DistributeTarget.tsx:40` (15 s), `loja/ProxyPage.tsx:59` (3 s, e `toastError` sem chave repete o mesmo erro) | Requisições em segundo plano e toasts repetidos | Pausar com `document.visibilityState !== 'visible'`; `toastError` com chave |
| P3.5 | `TrainingBar.tsx:49-52` | Cada `training.input` dispara `listTraining` e `getTraining` (2 GETs por toque) | Aplicar o evento no estado local e recarregar só no fim |
| P3.6 | Duplicações: chips refeitos três vezes (`Settings.module.css:239-262` sem `aria-pressed`, `Loja.module.css:5-10`, `Profiles.module.css .appChip`) enquanto `ui.chip` existe; `<select>` nativo em `RunsPage.tsx:104,112`, `DistributeTarget.tsx:73`, `ReleasesPage.tsx:597,788`; `jumpTo`/`useSectionOpen` copiados em `DiagnosticsPage.tsx:55-72`; "StaleBanner" reescrito em 5 telas | Mais CSS e comportamento divergente (foco, teclado) | Um `Chip` e um `StaleBanner` em `components/`; `Select` nos filtros |

## 6. O que está bem e deve ser preservado

- Tema, tokens e contraste (`styles/tokens.css`): um acento só, status sempre com ícone + texto, `prefers-reduced-motion` e `forced-colors` tratados.
- `Button` com `loading`, `disabledReason` (motivo visível no tooltip) e `iconOnly` com aria-label; `confirm()` central; `Dialog` nativo com Esc.
- Comando: motivo do bloqueio visível e no botão, pré-voo com "Seguir só com aptos", cooldown com motivo.
- Execuções: skeleton, erro com "Tentar de novo", paginação de 50, linha do tempo paginada, `DetailUnavailable`.
- Banner de desconexão com motivo e contagem regressiva, dados marcados como desatualizados.
- O flag `features.skills` desligado deixa o painel exatamente como antes (provado em `TrainingReview.test.tsx` e `FlowsRecipesSection.test.tsx`).

## 7. Plano de correção (fase L — usabilidade)

Para a sessão "Evolução Android multiagentes" executar depois de J e K1, antes do deploy final. Ordem por impacto;
cada item fecha com teste direcionado (vitest) e, no fim da fase, com a prova visual.

1. **P1.1 Foco em celular** e **P2.4/P2.3 grades e tabelas em 390 px**: tela cheia abaixo de 720 px, uma coluna abaixo de 560 px, `minmax(min(100%, Npx), 1fr)`, `tableWrap`.
2. **P1.2 ações de habilidade no painel**: cliente (`api/client.ts`) com `setSkillStatus`, `rollbackSkill`, `discardTeaching`; lista agrupada por skill com Publicar / Desabilitar / Reverter e `confirm()`; texto do `TeachingPanel` apontando para o lugar certo. Sem mudar a API.
3. **P1.3/P1.4/P1.5/P2.11 erro nunca vira vazio nem carga eterna**: estado de erro + "Tentar de novo" em Apps, Loja, Perfis, revisão do treino (perfis/grupos e ensino), detalhe de app, abas do perfil.
4. **P2.1/P2.10 vocabulário**: "habilidade" só para a versionada; "aparelho" na interface; um botão primário por diálogo.
5. **P2.2/P2.7/P2.9 padrões nas telas novas e enums traduzidos**: busy por ação, `Banner` para erro e risco, `StatusBadge`, mapas de rótulo.
6. **P2.5/P2.6/P2.8 topo, confirmações e coluna de ações**.
7. **P3.1 a P3.6 desempenho**: `React.lazy` por view, seletores com `useShallow`, `useNow` local, polling pausado, componentes únicos de chip e banner.

**Pronto quando:** `npm run typecheck`, `npm test` e `npm run build` verdes; chunk inicial abaixo de 350 kB; as 7
views e o Foco navegáveis em 390×844 sem recorte nem sobreposição (capturas em `docs/auditoria-ux-2026-09-27/capturas/`,
substituindo as cinco de hoje pelas de depois); nenhum estado de erro apresentado como vazio (teste por tela);
[`produto.md`](../produto.md) §3 atualizado (ensino v2 com publicar pelo painel), `CHANGELOG.md` e
[`estado-atual.md`](../estado-atual.md). Prova `simulated`; a conferência em aparelho real fica `not_run` até o
deploy.

## 8. Fora do escopo desta auditoria

- Conteúdo e correção das telas em aparelho real (frames, prévia, controle manual): só com o parque, depois do deploy.
- A loja com aparelho-loja, releases e proxy: vazios no simulado; layout conferido, fluxo não.
- Geração real de candidata de habilidade e proposta da IA: `not_run` (custam chamada de modelo).
