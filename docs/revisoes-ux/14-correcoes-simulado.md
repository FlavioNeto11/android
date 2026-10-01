# Tarefa 14: correções da prova simulada (RF-40, RF-43, RF-44, RF-45)

Branch `ux/14-correcoes-simulado`, que sai de `ux/13-prova-simulada` `d9ef5e1` (que é `claude/ux-portal` `6f184f8`
mais o relatório da 13). Data: 30/09/2026. Máquina: central `WIN-7S2UASNLFOP` (Windows Server 2025). Esforço pedido:
high.

Um commit por defeito. Não subi backend simulado nem dev server, e não disparei nenhuma ação. As provas são testes
vitest (jsdom, com o backend falso dos próprios testes) e leitura de código. Os RF-41, 42, 46, 47, 48 e 49 ficam de
fora por decisão do orquestrador.

| Defeito | Commit | O que mudou | Prova |
|---|---|---|---|
| RF-40 (Média) | `91fd01c` | Todas as telas dizem "Desconhecido" para o aparelho de servidor sem resposta e nenhuma oferece verbo a partir do estado guardado | simulated |
| RF-43 | `96a7624` | "Limpar filtros" com 32 px de alvo, sem mudar a altura do rodapé | simulated (regra do CSS); medida na tela `not_run` |
| RF-44 | `86b4d9d` | O nome acessível do semáforo começa literalmente pelo texto visível | simulated |
| RF-45 | `b56fddb` | O link do popover do semáforo leva o foco ao `#conteudo`, pela mesma função da gaveta do menu | simulated |

## RF-40: uma regra para "desconhecido"

A regra continua sendo uma só: `store/metricas.ts::estadoContado` (servidor não inscrito ou sem canal). Ela chega às
telas por `features/devices/selos.ts`:

- `seloDoAparelho`, que já existia;
- `aparelhoDesconhecido`, novo, que é só `estadoContado(...) === 'desconhecido'`.

Nenhuma tela reescreve o `!enrolled || !connected`.

Há uma armadilha de importação: `selos → metricas → deviceState`. Por isso `deviceState.ts` não pode perguntar a regra.
`focusActionGroups` ganhou um 4º parâmetro opcional (`servidorSemResposta`), que o Foco calcula com a regra. O motivo
mora em `deviceState.ts`:

> `MOTIVO_SERVIDOR_SEM_RESPOSTA = 'Servidor sem resposta: não dá para agir neste aparelho até o servidor dele voltar.'`

Todas as telas importam o motivo dali.

**Foco** (`FocusPanel`, `FocusActions`, `FocusInfoSections`, `Screen`):

- **Cabeçalho**: o selo vem de `seloDoAparelho`. Para o aparelho de servidor fora do ar, ele diz "Desconhecido".
- **Tela**: mostra "Desconhecido" e "O servidor deste aparelho não está respondendo; não dá para saber se o emulador
  está ligado." Também diz que a tela e as ações voltam quando o servidor responder.
  - Não usa o `state_detail` do backend ("Aparelho em 'stopped'…").
  - Com o estado guardado `online`, não liga a tela ao vivo.
- **"Estado e saúde"**: a linha "Aparelho" e o semáforo da seção usam o mesmo selo (`healthLight` aceita o selo como
  parâmetro opcional), também sem o `state_detail`.
- **Ações indisponíveis**, com "Servidor sem resposta…" no próprio botão (o padrão `disabledReason` da tarefa 03):
  - ciclo de vida: Iniciar, Parar, Reiniciar, Hibernar e Resetar dados;
  - apps: Instalar app, Abrir app e Verificar app;
  - controle: Assumir controle, as teclas e o motivo do controle manual;
  - Atualizar imagem.
- **O que segue igual**:
  - "Cancelar comando": é a saída do comando em voo.
  - "Reler contexto" e "Ver hierarquia": só leem do central.
  - "Indisponíveis": o eixo é a capacidade, não o estado.
- A constante `online` do Foco passou a ser `state === 'online' && !desconhecido`. Isso também desliga a barra de
  treino, a hierarquia e a interação com a tela.

**Cartão e linha** (`DeviceCard`, `DeviceList`):

- O verbo do estado guardado ("Iniciar" para um `stopped`) continua no lugar, mas fica indisponível, com o mesmo motivo
  no botão.
- O ícone "Hibernar" do cartão faz o mesmo.

**Barra em lote** (`DeviceGrid`, `BarraDeSelecao`):

- Os marcados à vista com servidor sem resposta saem dos `ids` da ação, como os escondidos pelo filtro (RF-01). Eles
  continuam marcados e são contados à parte: "2 selecionados (1 ignorado: servidor sem resposta)".
- Quando todos os marcados à vista são desconhecidos, não há barra de ações. A nota diz por quê: "…o servidor deles não
  está respondendo. Nenhuma ação os alcança até ele voltar." Antes ela dizia que o filtro escondia, o que seria falso
  aqui.

**Infraestrutura** (`InfraPage`, `infraState`): `instanceStateMeta` passou a receber o estado contado
(`estadoContado(i, workers)`), com `desconhecido: { label: 'desconhecido', tone: 'warning' }`. Antes era "parado".

**Outras telas com selo de estado**, para cumprir "em TODAS as telas":

- a guia Visão geral da persona;
- a guia Aparelhos da persona, no selo e no `state_detail`;
- o cartão "Contexto operacional";
- Configuração → Aparelhos.

As quatro trocaram `metaOf(INSTANCE_STATE, state)` por `seloDoAparelho`. São só selos: essas telas não oferecem verbo
de ciclo de vida.

### Decisões e leituras do briefing

- **"o mesmo na linha/cartão (nada de 'Iniciar' para aparelho desconhecido)"**: li como **o mesmo tratamento do Foco**.
  O botão fica no lugar, indisponível e com o motivo no próprio botão. Não é escondido. "Nada de Iniciar" quer dizer
  nada de Iniciar *clicável*, que era o defeito. Esconder deixaria o cartão sem pista de por que a ação sumiu e
  divergiria do Foco. Se o dono preferir esconder, basta trocar o `disabledReason` por um `primary && !semServidor` nos
  dois arquivos.
- **Motivo com explicação**: o motivo começa por "Servidor sem resposta" e explica o que falta ("…até o servidor dele
  voltar"). O tooltip e o nome acessível levam a frase inteira.
- **Fica registrado, não corrigido**: no Painel em Cartões, um aparelho desconhecido com estado guardado
  `stopped`/`absent`/`hibernated` continua no grupo "Parados (N)" do servidor dele. É o compromisso da tarefa 04
  (`04-textos-cards.md`, linha 59): o agrupamento usa o `state`. O selo do cartão diz "Desconhecido", e o resumo, a
  contagem e o filtro já o tratam como desconhecido.
- **Também fica igual**: a miniatura do cartão para estados que não são `stopped` nem `error` (por exemplo, um
  `hibernated` guardado) mostra o título do estado guardado. O `noFrameTitle` só reescreve `stopped` e `error` para
  servidor fora do ar. Com o estado guardado `online`, a miniatura do cartão mostra a última imagem com o aviso
  "Desatualizado", e não um título. O selo do cartão já diz "Desconhecido" nos dois casos. Não mexi: é a miniatura,
  da tarefa 04.

### Provas do RF-40 (simulated)

Os testes novos falharam **antes** da correção: 8 de 10. Os outros dois são controles e passam nos dois casos. Saída
da execução antes do código:

```
× na Lista: selo "Desconhecido" e o verbo do estado guardado indisponível com o motivo no botão
× nos Cartões: o mesmo, inclusive o "Hibernar" do aparelho que constava online
× barra em lote: age só nos de estado conhecido e conta os ignorados
× barra em lote: só desconhecidos marcados, nenhuma ação, e a nota diz por quê
× o aparelho do servidor sem canal é "desconhecido", não o estado guardado      (Infra: 'android-14paradoexternal')
× o estado diz "Desconhecido" e explica, sem afirmar "Parada" nem "desligado"
× as ações que dependem do servidor ficam indisponíveis com o motivo no próprio botão   (/^Iniciar/: aria-disabled null)
× estado guardado "online" com o servidor fora: nada de tela ao vivo nem verbos de aparelho ligado
Tests  8 failed | 72 passed (80)
```

Depois da correção, todos passam:

- `frontend/src/features/focus/FocusPanel.test.tsx`, bloco "servidor sem resposta = estado desconhecido (RF-40)", com
  4 testes:
  - o selo e o texto explicativo aparecem, sem "Parada", sem "O emulador está desligado" e sem o `state_detail`;
  - Iniciar, Assumir controle, Resetar dados…, Instalar app e Abrir app ficam com `aria-disabled` e "Servidor sem
    resposta";
  - com o estado guardado `online`, não há tela ao vivo, e Parar, Reiniciar e Assumir controle ficam bloqueados;
  - controle: com o servidor conectado, "Iniciar" fica livre.
- `frontend/src/features/devices/DeviceGrid.test.tsx`, bloco "servidor sem resposta (RF-40)", com 4 testes:
  - Lista e Cartões, inclusive o "Hibernar";
  - a barra com 1 ignorado;
  - a barra só com desconhecidos.
- `frontend/src/features/infra/InfraPage.test.tsx`, bloco "servidor fora do ar (RF-40)", com 2 testes:
  "desconhecido" fora do ar e "parado" no ar.

As telas da persona, o contexto operacional e a Configuração ficaram provados por `npm run typecheck` e pela suíte
inteira verde (os testes existentes delas). Não ganharam teste próprio de "Desconhecido".

## RF-43: alvo de "Limpar filtros"

Em `components/BarraListagem.module.css`, `.limpar` ganhou `min-height: var(--hit-min)`, `padding: 0 6px` (era
`2px 6px`) e `margin: -4px 0`. São 32 px de alvo. A caixa na linha continua com os 24 px de antes (32 − 2 × 4), então a
altura do rodapé não muda. É a receita da tarefa 07 para links em linhas densas.

A única diferença visual é o fundo do `:hover`, que passa a cobrir os 32 px, igual aos outros links com essa receita.

**Prova (simulated)**: `frontend/src/components/BarraListagem.test.ts`, com 2 testes. Eles leem a regra do CSS Module
do disco, porque no vitest o `?raw` de CSS não chega como texto. Antes da correção, os dois falharam: a regra não tinha
`min-height: var(--hit-min)` nem `margin: -4px 0`. Depois, passam.

**`not_run`**: a medida de 32 px na tela, por exemplo com o auditor de alvos em `#/execucoes?status=pendencia` a 1024 e
390 px. O jsdom não mede pixels, e não subi dev server.

## RF-44: nome acessível do semáforo

**Causa**: o texto do botão era `{rotulo}` seguido do `<span>` do selo, sem nada entre eles. O texto lido ficava
"Ambiente crítico8", e a comparação do axe (minúsculas, sem pontuação) não o achava em "ambiente crítico 8 motivos…".

**Correção**:

- um `{' '}` entre o rótulo e o selo; o `.gatilho` é `inline-flex`, e espaço entre itens flex não ocupa lugar;
- o nome começa literalmente pelo texto visível: "Ambiente crítico 8 motivos. Abrir detalhes do ambiente" (era
  "Ambiente crítico, 8 motivos. …").

O `aria-label` ficou: o `Popover` exige `label`, e o nome continua dizendo o que o botão faz.

**Prova (simulated)**: `frontend/src/features/topbar/SaudeAmbiente.test.tsx`.

- O novo "RF-44: o nome acessível começa pelo texto visível, em todos os níveis" cobre OK, Atenção e Crítico. Antes ele
  falhava com `'ambiente em atenção2'`.
- Três asserções antigas, de texto exato, foram ajustadas ao novo texto visível, sem afrouxar nada:
  - `'Ambiente em atenção2'` virou `'Ambiente em atenção 2'`;
  - `'Ambiente crítico1'` virou `'Ambiente crítico 1'`;
  - a regex `/^Ambiente em atenção, 2 motivos\./` virou `/^Ambiente em atenção 2 motivos\./`.

**`not_run`**: o Lighthouse e o axe no navegador (`label-content-name-mismatch`).

## RF-45: foco depois de seguir um link do popover

A regra da gaveta do menu foi para `lib/scroll.ts`:

- `focarConteudo()` foca o `#conteudo` e devolve se havia onde focar;
- `ID_CONTEUDO` também passou a ser usado por `conteudoAoTopo`.

Quem usa a função:

- **`MenuLateral`**: a constante local e o `getElementById(...).focus()` dele saíram. O efeito chama
  `focarConteudo()`, ou volta ao botão "Menu" quando a gaveta fechou sem trocar de tela.
- **`SaudeAmbiente`**: todo link do popover (os motivos, "Também" e o rodapé) chama `fechar()` e depois
  `focarConteudo()`. Como o foco se move antes de o portal desmontar o link, ele não cai no `<body>`.

**Prova (simulated)**:

- `frontend/src/features/topbar/SaudeAmbiente.test.tsx`, no teste "RF-45: seguir um link do popover leva o foco ao
  conteúdo principal…": com "Abrir Infraestrutura" e com "Diagnóstico", o `document.activeElement` é o `<main
  id="conteudo">`, e o popover fecha. Antes falhava, com o foco em `<body>`.
- O teste da gaveta (`frontend/src/features/topbar/TopBar.test.tsx`, "escolher uma seção na gaveta leva o foco ao
  conteúdo…") segue verde com a função compartilhada.

**`not_run`**: o teclado real no navegador.

## Verificação

- `npm run typecheck`: sem erros.
- `npm test` (uma vez, no fim): **85 arquivos, 1027 testes, todos verdes**, em 16,5 s. Na tarefa 13 eram 84 arquivos e
  1013 testes. A diferença é 1 arquivo novo (`BarraListagem.test.ts`) e 14 testes novos: 4 no Foco, 4 na Grade, 2 na
  Infraestrutura, 2 na BarraListagem e 2 no semáforo.
- Durante o trabalho rodei só os arquivos afetados.

## Arquivos alterados

Pela regra das revisões, cada tarefa tem um dono de arquivos. Esta tarefa não tem mapa de posse próprio: o orquestrador
liberou tocar onde o defeito exigir. Por isso todos os arquivos abaixo são **toque em arquivo alheio**, com o dono
original indicado.

| Arquivo | Dono original | Defeito |
|---|---|---|
| `frontend/src/features/devices/selos.ts` | 04 | RF-40 (descrição do selo e `aparelhoDesconhecido`) |
| `frontend/src/features/devices/deviceState.ts` | 03/04 | RF-40 (motivo e parâmetro de `focusActionGroups`) |
| `frontend/src/features/devices/DeviceCard.tsx`, `DeviceList.tsx`, `DeviceGrid.tsx` | 04 | RF-40 |
| `frontend/src/features/devices/OperationalContextCard.tsx` | 04 | RF-40 (selo) |
| `frontend/src/features/focus/FocusPanel.tsx`, `FocusActions.tsx`, `FocusInfoSections.tsx`, `Screen.tsx` | 03 | RF-40 |
| `frontend/src/features/painel/BarraDeSelecao.tsx` | 03 | RF-40 |
| `frontend/src/features/infra/InfraPage.tsx`, `infraState.ts` | 02 | RF-40 |
| `frontend/src/features/profiles/GuiaAparelhos.tsx`, `GuiaVisaoGeral.tsx` | 05 | RF-40 (selo) |
| `frontend/src/features/settings/InstancesSection.tsx` | 06 | RF-40 (selo) |
| `frontend/src/components/BarraListagem.module.css` | 05 | RF-43 |
| `frontend/src/features/topbar/SaudeAmbiente.tsx` | 02 | RF-44, RF-45 |
| `frontend/src/features/topbar/MenuLateral.tsx` | 01 | RF-45 |
| `frontend/src/lib/scroll.ts` | (comum) | RF-45 |

Testes:

- `frontend/src/features/focus/FocusPanel.test.tsx`;
- `frontend/src/features/devices/DeviceGrid.test.tsx`;
- `frontend/src/features/infra/InfraPage.test.tsx`;
- `frontend/src/components/BarraListagem.test.ts` (novo);
- `frontend/src/features/topbar/SaudeAmbiente.test.tsx`.

Não foram tocados `CHANGELOG.md`, `docs/estado-atual.md` nem nenhum ADR.

## O que ficou de fora

- **RF-41, 42, 46, 47, 48 e 49**: decisão do orquestrador. Ficam registrados na `13-prova-simulada.md`.
- **O grupo "Parados (N)" com aparelho desconhecido** e **o título da miniatura** para estados guardados que não são
  `stopped` nem `error` (ver "Decisões").
- **Verificações visuais a 1440, 1024 e 390 px**: `not_run`. O briefing proibiu subir dev server, e as provas acima
  bastam para o comportamento. Ficam para a próxima prova no navegador:
  - o Foco "Desconhecido" com os botões apagados;
  - a medida de 32 px do "Limpar filtros";
  - o Lighthouse sem `label-content-name-mismatch`;
  - o foco depois do link do popover com teclado real.
