# Ensino assistido: validação real e auditoria (05/10/2026)

Itens: 31.79 (validação pedida pelo dono), 31.80 (primeiro defeito), 31.81 (prioridade nº 1 do dono: ensinar, gerar o
conhecimento certo e as personas usarem certo). Ambiente: central `WIN-7S2UASNLFOP`, commit no ar `30667a2e` (deploy 37).
Arquivo fora do Git (pasta `reports/`).

## 1. O que foi provado de verdade (`real`)

| O quê | Resultado | Ids |
|---|---|---|
| Gravar pelo painel (Foco › Assumir controle › Modo treinamento) | funciona: toque com alvo por `resource_id`, tecla, texto | sessão `trn-wZQnQtmKXyrZbLQO`, android-09, 12:45Z a 12:54Z, 19 entradas |
| Gravar uma demonstração completa (rotas do painel, pela API de loopback) | 6 entradas: Perfil, campo Nome, texto, campo Cidade, texto, Salvar; o app mostrou "Perfil salvo" | sessão `trn-RJXCrrwQlMLumbAy`, android-10, 13:33Z a 13:34Z |
| Proposta da IA (`POST /api/training/{id}/propose`) | comando `atualize meu perfil no QA Messenger com o nome {nome} e a cidade {cidade}`, 2 parâmetros, 4 etapas com pós-condição; 10,6 s; US$ 0,0141 | chamada `plan` 13:35:27Z, claude-sonnet-5-5 |
| Salvar como fluxo (`POST /api/training/{id}/save`) | fluxo `active`, `source=training:trn-RJXCrrwQlMLumbAy`; 4 receitas ativas (194 a 197), todas "receita gravada" | fluxo `atualizar-o-cadastro-do-perfil-no-qa-mes` |
| Prévia do casamento (`POST /api/flows/match`) | frase do molde casa: 4 de 4 etapas com receita, IA "zero", estimativa US$ 0,0184. Frase um pouco diferente NÃO casa (`null`) | 13:36:37Z |
| Execução 1, mesmo aparelho, valores novos | `completed`, "1 de 1 com sucesso comprovado" em 26 s; plano `fluxo` sem planejador; 4 etapas `driven_by=recipe` pelas receitas do treino, 0 decisões de IA; o app ficou com "Rafael Prova" / "Recife" (campo limpo antes de digitar) | `r-20261005133644-181b7b`, `lote:jev:31.79-1`, US$ 0,0204 |
| Execução 2, OUTRO aparelho e outra conta do app, partindo de dentro de uma conversa | `completed` em 42 s; etapa 1: a receita não achou o botão, a IA assumiu (3 decisões) e voltou à tela certa (`recipe+ai`); etapas 2 a 4 pelas receitas do treino; app com "Helena Ensaio" / "Manaus" | `r-20261005133833-122345`, `lote:jev:31.79-2`, android-12 |
| Demonstração "natural" (apagar o campo com Apagar e digitar) | a IA pôs o toque, as 15 teclas e o texto na MESMA etapa e não descartou nada: essa etapa não vira receita | proposta da sessão `trn-wZQnQtmKXyrZbLQO`, 13:40Z |

Custo pago: proposta US$ 0,0141 + execução 1 US$ 0,0204 + execução 2 e a 2ª proposta (não fechei a conta; ordem de
US$ 0,03 a 0,05). Teto que me dei: US$ 0,50.

Não executado (`not_run`): os botões "Pedir proposta à IA" e "Salvar como fluxo" da tela de revisão (a janela do
Chrome ficou encoberta e o painel suspende a imagem; usei as mesmas rotas pela API); ensino com persona vinculada;
Ensino v2 (habilidade) até publicar; efeito externo em app real.

## 2. Resposta curta

- O caminho feliz FUNCIONA de ponta a ponta: ensinar uma vez, a IA generalizar com parâmetros, e execuções em dois
  aparelhos usarem o ensinado sem planejador e quase sem IA.
- Antes de hoje ele nunca tinha sido usado de verdade (0 sessões, 0 fluxos e 0 receitas de treino no banco).
- Ele é frágil fora do caminho feliz, e hoje uma persona NÃO consegue usar o ensinado "com os dados dela": o valor tem
  de vir escrito no comando.

## 3. Defeitos e lacunas (numerados; evidência no código em `scratchpad/auditoria_ensino_{1,2,3}_*.md` da sessão)

Gravar:
- **31.80** [real] reinício do backend no meio da gravação deixa a sessão órfã: o painel diz "Gravando", nada grava,
  e não dá para começar outra (`manager.py:351`, `recorder.py:126`, `TrainingBar.tsx`).
- **31.82** [código] segredo pode vazar para a gravação: sem leitura da tela (aparelho lento) a senha digitada só cai
  em heurística; conteúdo de campo tocado e linhas da tela (ex.: "seu código é 123456") são gravados
  (`recorder.py:130-150`, `redaction.py:191-203`, `executor.py:4618-4629`). A dica do painel promete mais do que isso.
- **31.84** [real] o texto do painel não limpa o campo; quem ensina aperta Apagar; qualquer tecla na etapa derruba a
  receita (`manager.py:4274`, `recipes.py:501`); a IA não descarta as teclas.
- **31.85** [real] com o aparelho lento, entradas são recusadas por "quadro velho" (409) e cliques somem sem aviso
  (`manager.py:4227-4230`, `FocusActions.tsx:185`, `FocusPanel.tsx:118`). Medido: toque de 24 s, 15 teclas recusadas
  em série; digitar 12 caracteres levou 17,5 s.
- **31.86** [real] a revisão de uma gravação só aparece com o aparelho ONLINE (`FocusPanel.tsx:278`): o android-09
  entrou em erro e as duas gravações dele ficaram inalcançáveis no painel; salvar com o aparelho fora do ar não gera
  receita e não há como refazer (`skills.py:162`, `:88`).
- Menores: "Abrir app" gravado antes de confirmar que abriu; persona não escolhível ao iniciar; falha ao gravar some
  no log; sem desfazer a última entrada.

Gerar o conhecimento:
- **31.83** [código] o salvar confia no JSON da tela: parâmetro fora do comando gera fluxo que NUNCA casa; `{x}` sem
  parâmetro fica literal; etapa sem chave dá erro 500; comando só com parâmetros pode sequestrar pedidos alheios
  (`skills.py:90-134`, `flows.py:317,341`).
- **31.88** [código] o ensinado nasce ATIVO para todos os perfis, sem ensaio (`flows.py:239,257`, `recipes.py:848`);
  a prova em aparelho de teste do aprendizado (30.47) não o alcança.
- **30.79 (Aprendizado)** [código] a demonstração nunca substitui uma receita ativa que a IA já tinha; a tela diz só
  "já havia receita ativa" (`recipes.py:820`, `skills.py:179`).
- **30.80 (Aprendizado)** [real] a divergência por tela de partida diferente contou como falha da receita ensinada
  (receita 194: 1 ok, 1 falha depois da execução 2); com 3 seguidas ela vai para quarentena sem avisar quem ensinou.
- **31.90** [código] as perguntas que a IA faz na proposta não têm como ser respondidas; a revisão não deixa editar
  parâmetros, pós-condição nem entradas, e só mostra por que a etapa ficou sem receita DEPOIS de salvar.

Usar (personas):
- **31.87** [código] dado da persona não chega ao fluxo ensinado. Existe `{perfil_*}` para 5 campos (nome, sobrenome,
  nome de exibição, nascimento, e-mail; `available_data.py:100`), mas o treino nunca o gera. Cidade, bio e telefone
  não existem como variável: incluir pede decisão do dono.
- **31.89** [real + código] casamento frágil: só a frase do molde casa; entre vários moldes ganha o mais usado, não o
  mais específico (`flows.py:286,344`); troca de valor por substring (`flows.py:100`).
- **31.91** [código] dois caminhos de ensino sem ponte (Modo treinamento v1 e Ensino v2); a única habilidade v2
  publicada nunca foi usada; a sessão v2 real está parada desde 27/09 esperando resposta.
- Ninguém dispara comando sozinho para a persona: ela usa o ensinado quando um comando casa o molde.

## 4. Plano (ordem de execução)

1. Onda 1, consertos pequenos e independentes, já em implementação: 31.80, 31.82, 31.83, 31.84, 31.85, 31.86.
2. Onda 2, com desenho: 31.87 (dados da persona), 31.88 (nascer candidato e provar antes de valer para todos),
   30.79 e 30.80 (Aprendizado), 31.89, 31.90, 31.91 (decisão v1 x v2, ADR).
3. Cada conserto: teste que falha sem ele, segunda leitura independente, e prova `real` num aparelho de teste.

## 5. Estado deixado no ambiente

- Fluxo de teste ATIVO: `atualizar-o-cadastro-do-perfil-no-qa-mes` (QA Messenger, app local). Receitas 194 a 197.
- Perfis do app de teste alterados: android-10 ("Rafael Prova" / "Recife"), android-12 ("Helena Ensaio" / "Manaus").
- Sessões de treino: `trn-wZQnQtmKXyrZbLQO` (com proposta, não salva), `trn-3VoEWEgPaLsyKWnR` (3 entradas),
  `trn-RJXCrrwQlMLumbAy` (salva). Nada foi apagado.
