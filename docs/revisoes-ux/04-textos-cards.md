# Tarefa 04: textos técnicos e ruído dos cards de aparelho (relatório)

Branch `ux/04-textos-cards` (sai de `claude/ux-portal` 65a1a0e, que já tem as tarefas 01, 02 e 03). Data: 30/09/2026.
Máquina: central (Windows Server 2025). Modelo: Sonnet 5.5, esforço medium.

## O que mudou

1. **Mapa de tradução** em `frontend/src/lib/rotulos.ts` (módulo puro, sem React):
   - `rotuloDoComando(verbo)`: `app.distribute` → "Distribuição de app", `device.network` → "Rede do aparelho",
     `app.canary` → "Teste de versão do app", `session.verify` → "Verificação da sessão", `store.sync`,
     `session.connect`, `device.proxy` e os demais verbos de `APP_COMMAND_VERBS` do backend, mais os de ciclo de
     vida. Verbo que o mapa não conhece **nunca** sai cru: `app.limpar_cache` vira "Limpar cache (app)".
   - `tempoRelativo` / `duracaoHumana`: uma unidade só, arredondada para baixo. "há 161 h" → "há 6 dias", "há 1 min
     14 s" → "há 1 min", e segue em meses e anos.
   - `evidenciaLegivel`: `seletor id=…|text=qa-user-10: 1 elemento(s)` → `Confirmado na tela: “qa-user-10”`;
     `[nível observado: delivered]` → `[nível observado: entregue]`.
2. **Trilha de comandos** (`features/devices/CommandTrail.tsx`): `rotuloDoVerbo` cai no mapa em vez de devolver o
   identificador; a idade usa `duracaoHumana`; o servidor aparece pelo nome (não `WIN-7S2UASNLFOP`). O identificador
   original fica no `title` ("Identificador: app.distribute"). O estado `uncertain` passou de "Desconhecido" para
   **"Sem resposta"** ("Desconhecido há 161 h" → "Sem resposta há 6 dias"). "Desconhecido" fica reservado ao selo
   do aparelho cujo servidor não responde.
3. **Cartão sem ruído** (`DeviceCard.tsx`): somem "Sem tarefa em andamento", "Controle: —", "Sem rótulo de conta" e
   "Sem app associado" quando não há dado. A etiqueta "observado" virou "Evidência". A idade do frame usa o tempo
   relativo novo.
4. **Cartão compacto** para aparelho parado (`stopped`, `absent`, `hibernated`): cabeçalho, selo, conta/persona,
   comando ou aviso se houver, e o botão (Iniciar, Acordar, Criar AVD) mais "Abrir". Sem miniatura e sem o bloco
   "Emulador desligado". Altura medida no ambiente real: **79 a 125 px contra 426 a 478 px** do cartão completo
   (20 a 30%, bem abaixo da metade exigida). Quando o motivo de não haver tela é um problema (servidor fora do ar,
   túnel caído, ADB sem resposta), ele sobe como uma linha; "desligado de propósito" e "hibernado" não, porque o
   selo já diz.
5. **Agrupar por servidor e recolher "Paradas"** (`DeviceGrid.tsx`): com mais de um servidor, cada um ganha um título
   (central primeiro); dentro dele, os ativos em cards grandes e um botão "Paradas (N)" (`aria-expanded`) que
   recolhe os compactos. O recolhimento fica no `localStorage` (`cda.painel.paradasRecolhidas`).
6. **Cards / Lista** (`DeviceList.tsx` novo): a alternância fica no cabeçalho da seção Aparelhos e é lembrada em
   `cda.painel.visao` (leitura e gravação pelo `lib/storage.ts`, com try/catch; valor estranho volta para Cards). A
   lista é uma tabela: seleção, aparelho, estado, servidor, conta (ou persona), aplicativo, atividade (etapa ou
   comando) e ações (botão principal + Abrir). Ctrl e Shift+clique funcionam como nos cards. Abaixo de 520 px de
   contêiner a linha vira um bloco de duas a três linhas, sem rolagem horizontal.
7. **Selos de estado** (`features/devices/selos.ts`): um só lugar (`seloDoAparelho`), sempre ícone + texto + cor.
   Online, Parada, Hibernado etc. vêm de `INSTANCE_STATE`; **"Desconhecido"** (ícone de interrogação) aparece quando
   o servidor do aparelho está fora do ar, pela mesma regra do contador da tarefa 02 (`estadoContado`). "Desatualizado"
   continua na miniatura (ícone de alerta + texto).
8. **Filtro `?estado=`** no Painel. Lê `rota.query.estado` (tarefa 01) e mostra só os aparelhos naquele estado,
   com o chip "Filtro: desconhecidos (N)" e o botão "Limpar filtro" (`trocarQuery({ estado: undefined })`, que
   substitui a entrada do histórico). Valores aceitos (os de `ORDEM_DOS_ESTADOS` em `store/metricas.ts`): `online`,
   `booting`, `stopping`, `hibernated`, `stopped`, `absent`, `error`, `desconhecido`. Valor fora dessa lista é
   ignorado (não filtra). A contagem de desconhecidos continua vindo de `store/metricas.ts`; a grade usa
   `estadoContado` dali, então o número do link e o da grade não divergem. A loja nunca entra no filtro, igual à contagem.
   Filtro sem resultado mostra "Nenhum aparelho neste estado" e o botão de limpar.

## Decisões e divergências

- **Lista mostra todos os aparelhos, sem agrupar.** O briefing pede agrupar por servidor e recolher "Paradas"
  em geral; na Lista, a coluna "Servidor" e a altura de linha (36 px) já cumprem o objetivo e uma tabela única
  ordena melhor. O agrupamento ficou na visão em Cards.
- **"Sem resposta" para o comando `uncertain`.** O briefing só exemplifica; escolhi o nome do estado do comando no
  mapa `COMMAND_STATE`. Quem precisa do detalhe vê a descrição do selo ("Acabou sem que se saiba o efeito...").
- **Grupos por servidor só com mais de um servidor**, para não pôr um título inútil no caso comum (tudo local).
- **Aparelho `stopped` de servidor fora do ar** cai em "Paradas" (compacto) mas com selo "Desconhecido" e a linha do
  motivo; não inventei um terceiro grupo.
- **Preferência Cards/Lista por chave `cda.painel.visao`** (não entrou na URL); o filtro `estado` é o único parâmetro
  novo de URL.

## Toques em arquivo alheio

- `frontend/src/app.integration.test.tsx` (teste da tarefa 01): duas asserções atualizadas ("observado" →
  "Evidência"; o bloco "Hibernado — acorda em segundos..." não existe mais no cartão compacto).
- `features/focus/FocusInfoSections.tsx` (tarefa 03): **nenhuma mudança**; ele importa `rotuloDoVerbo` e
  `COMMAND_STATE` de `CommandTrail`, então herdou a tradução e "Sem resposta" sem edição.

## Fora do escopo, registrado para as tarefas 05 e 08

- `features/runs/PlanTab.tsx` (tarefa 05) ainda mostra `app com.instagram.android` e as chaves dos parâmetros do plano
  (`destinatario`, `aplicativo`) cruas. É da posse da 05 (corre em paralelo): não toquei. Sugestão: `appLabel(apps, pacote)`
  com o pacote no `title`.
- Nas execuções e logs vivos, procurei por `app.*`, `device.*`, `session.*`, `store.*` e `seletor id=…` nas telas
  Painel, Execuções, Infraestrutura e Diagnóstico (texto da página, real, 30/09): só restou `com.instagram.android`.

## Arquivos

Novos: `frontend/src/lib/rotulos.ts`, `rotulos.test.ts`, `features/devices/selos.ts`, `DeviceList.tsx`,
`DeviceGrid.test.tsx`.
Alterados: `features/devices/DeviceCard.tsx`, `DeviceGrid.tsx`, `CommandTrail.tsx`, `Devices.module.css`,
`DeviceCard.test.tsx`, `CommandTrail.test.tsx`, `frontend/src/app.integration.test.tsx`.

## Provas

**simulated** (vitest, backend falso):
- `lib/rotulos.test.ts` (16): os verbos do backend, o fallback legível, "há 161 h" → "há 6 dias", "há 1 min 14 s" → "há 1 min",
  escalas até anos, relógio adiantado, evidência por seletor e nível observado.
- `features/devices/DeviceGrid.test.tsx` (10): `estadoDoFiltro`, `agruparPorServidor`, grupos com "Paradas (N)" e
  recolhimento (com `aria-expanded` e `localStorage`), Lista com 5 linhas, preferência gravada e valor estranho,
  `?estado=desconhecido` com o chip e o botão "Limpar filtro", `estado` inválido e filtro vazio.
- `features/devices/DeviceCard.test.tsx` (+6): sem "Controle: —", sem "Sem tarefa em andamento", evidência por
  seletor em português com o original no `title`, compacto sem miniatura nem "Emulador desligado", selo
  "Desconhecido" com o servidor fora do ar.
- `features/devices/CommandTrail.test.tsx` (+1): `app.distribute` vira "Distribuição de app", servidor por nome,
  identificador no `title`, idade em meses/anos.
- `npm run typecheck` verde e `npm test` inteiro: **81 arquivos, 953 testes, todos verdes** (30/09, ~20:36Z).

**real** (30/09/2026, máquina central, vite na porta 5194 com `VITE_API_TARGET=http://127.0.0.1:8000`, painel do
navegador da IDE, só navegação e leitura; nenhum botão de ação foi clicado):
- 1440×900, Cards: 15 aparelhos em dois grupos ("Servidor central", "Notebook da LAN"); cards ativos de 426 a 499 px e
  compactos de 79 a 193 px (os de 193 px eram do notebook, com uma linha "Emulador desligado em…" que depois passei a esconder: em 1024 e 390 px, já sem ela, 79 a 125 px). "Sem resposta há 6 dias" e
  "Distribuição de app" aparecem no lugar de "Desconhecido há 161 h" e `app.distribute`. `sw` = largura (sem rolagem
  horizontal).
- 1440×900, Lista: 15 linhas, tabela de **550 px** de altura (cabe numa tela de 900 px, com a seção de execução já
  aparecendo abaixo). Preferência gravada em `cda.painel.visao`.
- `#/painel?estado=stopped`: chip "Filtro: paradas (7)", 7 linhas; "Limpar filtro" voltou para `#/painel` com 15 linhas.
  Não havia aparelho desconhecido no ambiente no momento, então o filtro `desconhecido` ficou só no teste `simulated`.
- 1024×768 e 390×844 (Cards e Lista): sem rolagem horizontal da página (`scrollWidth` = largura); no celular a Lista
  vira blocos e o botão "Iniciar" e o "Abrir" ficam à vista.
- Observado e **não corrigido** (alheio): em 390 px a faixa do topo sobrepõe os saldos de IA ("US$ 22,46" e "US$ 8,09")
  ao botão ao lado; é do topo (tarefas 02/07).

**not_run**: o filtro `estado=desconhecido` contra um servidor realmente fora do ar (nenhum estava); teclado/leitor
de tela nas linhas da Lista além dos rótulos por `aria-label`; efeitos de Iniciar/Acordar (proibido neste ambiente).
