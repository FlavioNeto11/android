# Tarefa UX2-12: detalhe da persona, mais enxuto e legível

Branch `ux2/12-detalhe-persona`, que sai de `claude/ux-portal-2` (`085e34e`). Data: 01/10/2026. Máquina: central (Windows
Server 2025), com o backend SIMULADO deste worktree (porta 8712) e o vite na 5112, aberto por `localhost`. Esforço: high.
Nenhuma ação com efeito real foi disparada (nem Executar, nem salvar, nem Marcar bloqueada/Excluir/Reativar). O ambiente
central (8000) não foi tocado; tudo o que subi foi parado ao fim.

## O que mudou

1. **Cabeçalho único** (`PersonaHeader.tsx`, novo): foto, nome (o único `h1`), @, estado, aparelho vinculado e a ação
   principal "Abrir no aparelho" (abre o Foco do aparelho principal; sem aparelho fica indisponível e diz o porquê).
2. **Estado acionável** (`estadoSessao.ts`): "Não verificada" é um botão ("Verificar conta →") que **só leva** à guia
   Contas e acesso, onde moram os botões de verificar de verdade; "Resolver" e "Ver conta" para os demais estados que pedem
   alguém; "Conectado" não é botão e diz "confirmada há 3 h" quando `session.verified_at` existe.
3. **Bloco Identidade só com atributos** (`GuiaVisaoGeral.tsx`): idade, gênero, cidade, profissão e e-mail. Religião e
   política foram para o bloco recolhível **"Atributos de personalidade"**, fechado por padrão (botão com `aria-expanded`
   e `aria-controls`; o conteúdo nem é montado enquanto fechado). O @ também não se repete na linha da conta do mesmo @.
4. **5 seções** (`abas.ts` + `ProfileDetail.tsx`): Visão geral, Perfil (Persona, Imagens, Memória), Contas e aparelhos
   (Contas e acesso, Aparelhos), Atividade (Interações, Execuções, Aprovações), Avançado (Habilidades, Configurações).
   Primeiro nível = 5 botões numa `nav` com `aria-current="page"`; segundo nível = as abas da seção aberta (somem quando a
   seção tem uma guia só). Em tela estreita (`@container page`, não `@media`: o Foco aberto também estreita) as 5 seções
   viram uma lista suspensa. As Aprovações pendentes aparecem no botão da seção Atividade.
5. **Escopo do filtro de app** (`ProfileAccounts.tsx`): "Mostrando: Instagram" (ou "todos os apps") e "Filtra só Memória
   e Interações; as outras guias não mudam". A fileira só aparece nessas duas guias (as únicas que ela filtra).
6. **Slug legível na URL** (`slugPersona.ts`): `#/personas/lucas-almeida`, aceito e gerado, com o id antigo compatível.

## As rotas: a URL guarda a GUIA, a seção é derivada

`#/personas/<persona>/<guia>`. `<persona>` é o slug ou o id antigo; `<guia>` continua sendo uma das 11 de sempre. A seção
nunca vai para a URL: `secaoDaAba(guia)`. Por isso nenhum link antigo quebrou (`…/memoria` abre a Memória, agora dentro
de Perfil), e `openPersona(id, tab)` e `?foco=` seguem iguais (`store/ui.ts` e `pendencias/modelo.ts` não mudaram).

- **Slug**: nome sem acento, minúsculas, `-` entre palavras (corte em 48 caracteres numa fronteira de palavra); nome sem
  letra alguma cai no nome de exibição, no @ ou em `persona`. **Homônimos** ganham todos um sufixo curto tirado do PRÓPRIO
  id (os 4 últimos caracteres `a-z0-9`: `lucas-almeida-fqg8`), então a ordem da lista e um terceiro homônimo não mudam o
  link dos outros; se dois ids terminarem igual, o sufixo cresce até distinguir. Slug que coincidir com o id de outra
  pessoa cede e usa o próprio id.
- **Resolução** sem endpoint novo, pela lista já carregada: 1º o id exato; 2º o slug de hoje; 3º a forma com sufixo
  mesmo que o homônimo já tenha saído (link compartilhado antes). O nome puro de dois homônimos NÃO adivinha: a tela diz
  "Mais de uma persona com esse nome" e oferece "Ver todas".
- **Sem piscar**: a tela já mostrava o esqueleto até a lista chegar; só depois, se ninguém bate, aparece "Persona não
  encontrada". Renomear a persona com a tela aberta não vira "não encontrada" (vale a última resolvida enquanto o
  segmento não muda) e o link acompanha o nome novo.
- **Canonização**: quem chega por id antigo (ou por `#/perfis/<id>/…`, guia inexistente, caixa diferente, slug velho) tem o
  hash trocado por `navegar(…, 'replace')`: nada empilha, `?foco=` e o resto da query atravessam. A Visão geral é a guia
  sem segmento (`…/visao` vira só o nome).

## Linha de base (reconfirmada neste branch, antes de mexer) e depois

Medido pelo navegador da IDE contra o backend simulado, persona "Lucas Almeida" com @ e aparelho, 1440 px.

| | Antes (`085e34e`) | Depois |
|---|---|---|
| Itens de navegação no 1º nível | 11 abas numa faixa | 5 seções (+ abas só da seção aberta, no máximo 3) |
| Nome na Visão geral (`main.innerText`) | 2 ocorrências (cabeçalho + bloco Identidade) | 1 |
| @ na Visão geral | 3 | 1 |
| `h1` | 1 | 1 |
| Campos sensíveis na Visão geral | à vista | recolhidos, fechados |
| URL | `#/personas/ig-R7UM9mwweF0rFqG8` | `#/personas/lucas-almeida-fqg8` (homônimo) ou `#/personas/ana-beatriz-nandu-avila` |

Divergência do briefing: ele conta **3** repetições de foto/nome/handle (cabeçalho, linha de chips, Identidade). No
código, a "linha de chips" é o seletor de apps, que não repete o nome; medi 2 do nome e 3 do @ (o 3º é a conta do
Instagram na lista de contas da Visão geral). Tudo isso agora aparece uma vez.

## Decisões e divergências do briefing

- **"Contas e dispositivos" virou "Contas e aparelhos"**: o glossário do portal é Aparelho, e é o título do cartão que já
  existe na Visão geral.
- **"Mais ações" não foi criado.** Bloquear, reativar e remover moram no menu "⋯" da lista e na guia Configurações; um
  segundo menu no cabeçalho duplicaria ação com efeito. O cabeçalho tem a ação principal e o estado acionável.
- **Navegação por dois níveis (botões + abas), não lateral**: a página já divide a largura com o menu do portal e com o
  Foco; um menu lateral a mais a estreitaria. As abas da seção seguem sendo `role="tab"` (teclado: setas, Home, End).
- **"Conectado mostra desde quando"** mostra "confirmada há X": `session.verified_at` é a última confirmação na tela, não
  o início da sessão, e dizer "desde" seria afirmar o que o dado não prova. Sem o dado, só "Conectado".
- **"Atributos de personalidade" com botão próprio** e não o `Disclosure` (`<details>`): o briefing pede `aria-expanded`
  explícito, que `<summary>` não expõe no DOM.
- **Filtro de app só em Memória e Interações**: é onde ele age. Antes ficava acima de todas as guias sem explicar. O
  estado do filtro continua guardado ao trocar de guia.
- **Fora do mapa de posse, o mínimo**: `ProfilesPage.tsx` (resolver/gerar slug, banner de ambiguidade) e
  `ProfileAccounts.tsx` (AppSwitcher). `lib/rotas.ts` só ganhou comentário do contrato (`<persona>` e guia); `store/ui.ts`
  não foi tocado.

## Arquivos

Novos: `features/profiles/PersonaHeader.tsx`, `slugPersona.ts` (+ `.test.ts`), `estadoSessao.ts` (+ `.test.ts`),
`abas.test.ts`. Alterados: `abas.ts` (seções), `ProfileDetail.tsx`, `GuiaVisaoGeral.tsx`, `ProfileAccounts.tsx`,
`ProfilesPage.tsx`, `Profiles.module.css` (bloco novo no fim; não toquei CSS global nem tokens), `lib/rotas.ts` (comentário),
`docs/produto.md`. Testes ajustados: `ProfileDetail.test.tsx` (helper `irParaGuia`; o teste das 11 abas virou o das 5
seções), `ProfilesPage.test.tsx` (hashes com slug), `NovaPersonaLote.test.tsx` (seção atual em vez de aba). Capturas em
`rodada-2/capturas/` (`ux2-12-visao-geral-768.jpg`, `ux2-12-memoria-escopo-390.jpg`).

## Provas

| Prova | Nível | Onde |
|---|---|---|
| Slug: acentos, espaços repetidos, homônimos (determinístico, sem depender da ordem, 3º homônimo, sufixo que cresce), nome vazio, corte, slug = id de outro, link com sufixo depois que o homônimo sai, ambiguidade | simulated | `slugPersona.test.ts` (18 testes) |
| As 11 guias moram em exatamente 1 seção; a seção sai da guia; 5 seções | simulated | `abas.test.ts` |
| Estado da sessão: "Não verificada" leva a Contas e acesso, "Conectado" sem ação com `verified_at` | simulated | `estadoSessao.test.ts` |
| Rota antiga `…/ig-1/memoria` abre Perfil > Memória, troca o id pelo slug, `history.length` não muda | simulated | `ProfilesPage.test.tsx::rotas da persona` |
| Cada uma das 10 guias não iniciais, por id antigo, abre a guia na seção certa e vira slug | simulated | idem (`cada guia antiga continua…`) |
| `#/perfis/<id>/aparelhos`, `?foco=` atravessando a troca, guia inexistente cai na Visão geral | simulated | idem |
| Lista carregando não pisca "não encontrada"; slug desconhecido avisa só depois; homônimos; nome puro ambíguo; renomear com a tela aberta | simulated | idem |
| 5 seções, `aria-current`, lista suspensa com as mesmas 5, só as guias da seção, identidade uma vez, "Não verificada" não dispara nada, "Abrir no aparelho", bloco recolhido (`aria-expanded`, não monta fechado), escopo do app | simulated | `ProfileDetail.test.tsx` |
| Abrir pelos links reais no navegador contra o backend simulado: `ig-…/aparelhos` → `ana-beatriz-nandu-avila/aparelhos`, `#/perfis/ig-…/contas` → `helena-prado/contas`, `?foco=` preservado, 0 entradas empilhadas, `nao-existe` → banner | real (01/10, central, `085e34e`+working tree, simulado 8712) | navegador da IDE, aba própria |
| Sem rolagem horizontal, sem rótulo cortado, sem alvo < 32 px no cabeçalho/navegação/abas, nas 11 URLs de guia, em 1440, 1024, 768 e 390 px; em 390 a lista suspensa aparece e os botões somem (e o inverso a partir de 768) | real (idem) | medição por DOM (`scrollWidth`/`clientWidth`, alturas) |
| axe 4.13 (wcag2a/aa, 2.1, 2.2, best-practice) nas 11 guias a 390 px e em 4 guias a 1440 px (com o bloco de atributos aberto): **0 falhas de contraste, 0 na Visão geral além de `region` do contêiner de avisos** | real (idem) | `javascript_tool` + axe servido de `C:\temp\ui-verificar` |
| `npm run typecheck` e `npm test` inteiros | real (01/10) | **88 arquivos, 1077 testes passando** |

## O que ficou de fora / pendências

- **axe, achados que não são desta tarefa** (arquivos que não toquei; não comparei com o build anterior): `region` no
  contêiner de avisos (global, em todas as telas); `heading-order` (h4) em Contas e acesso; `heading-order` e
  `aria-allowed-role` em Persona; `aria-allowed-role` em Aparelhos. Cabe à tarefa 13.
- **Capturas**: o painel da IDE recorta a janela de 1440 px; 1440 e 1024 foram verificados por medição de DOM e por uma
  captura parcial, 768 e 390 por captura inteira (as duas versionadas). Lighthouse não foi rodado.
- **Teclado**: o bloco "Atributos de personalidade" e os botões das seções são `<button>` nativos (Enter e Espaço); não
  foi feita uma passada manual de Tab/Shift+Tab no navegador, só os testes e o axe.
- **Dois achados do ambiente de verificação** (nada de produto): (1) o cookie de sessão não distingue porta, então outro
  agente que entre num backend simulado em `localhost` derrubou a minha sessão duas vezes (o RF-47 de novo); refiz a entrada
  pelo próprio `/api/login` do simulado. (2) Editar `lib/rotas.ts` com o vite aberto deixa a aba com o store antigo
  (a navegação por hash para de reagir) até recarregar: é só desenvolvimento.
- **Deslize de regra**: o primeiro `navigate` saiu sem `tabId` e redirecionou uma aba que já existia no painel (`seed`, a 390x844)
  para `localhost:5112/#/personas`. Corrigi na hora, abri aba própria (`tab-1`, depois `tab-4`, ambas fechadas ao fim com a
  emulação de tamanho desfeita) e não usei mais a `seed`.
- **Textos**: conferi o glossário (08 e 10): Persona, Aparelho, sem "perfil" nem "dispositivo" nas cadeias novas; nada em
  conflito com o que as rodadas anteriores decidiram.
- Não alterei `CHANGELOG.md`, `docs/estado-atual.md` nem ADR (o orquestrador consolida).
