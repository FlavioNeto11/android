# Aprendizado vivo — diagnóstico (Fase A)

Frente Aprendizado, 02/10/2026. Fontes: cinco investigações só de leitura (domínio/API, runtime, conhecimento de app e Outlook,
painel, dados do central), cujos relatórios ficam no handoff local da frente, e duas conferências (SQL só leitura e config, abaixo).
Desenho que parte deste diagnóstico: [`aprendizado-vivo.md`](aprendizado-vivo.md). Só agregados; nenhum conteúdo de persona.
Código: origin/main c82a5210 (`apr-diag`); 12.3 em 58713c6a. Dados: central em c82a5210, migração 067.
**Níveis de prova**: o que vem do banco/API do central é `real` (leitura); o que vem do código é leitura estática (`not_run` como comportamento);
o que está marcado HIPÓTESE ainda não foi medido.

Conferências desta sessão (real, leitura, 02/10 ~19:35Z):
- `config/config.yaml` do central **não tem bloco `aprendizado:`** → valem os padrões de `config.py`: `aprendizado.enabled=true`,
  `licoes.modo=shadow` (`config.py:841`), `telas.modo=observe` (`:851`), `voz.modo=off` e `preferencias.modo=off` (`ModoDoAprendizadoCfg`, `:864`, `:895-896`), `fluxo.concordancias=1`, `com_prova=true`;
  `ai.recipes: replay`, `ai.flows: true`; `recipes_promote_after` padrão 2 (`config.py:562`).
- Receitas do Outlook: 10 linhas, **7 `step_hash` distintos**, 1 `app_version`; `step_key`: open_outlook, open_app, open_inbox, open_compose (2),
  fill_recipient (2), fill_subject (2), send_email. Runs com Outlook: 5, com **4 comandos distintos**. Messenger: 61 `step_hash` distintos.

---

## 1. Resumo executivo

1. **O aprendizado hoje é um catálogo de linhas de tabela, sem eixo de aplicativo.** O "Livro" agrega bem (receitas, fluxos, habilidades, memórias
   e `learning_items`, sem copiar conteúdo), mas não sabe quais apps existem, não lê nada do conhecimento declarado e a tela não filtra, não agrupa
   e não enumera apps.
2. **O conhecimento do Outlook EXISTE nas duas formas e chega à API**: declarado (`app.yaml`, `telas.yaml` 158 linhas, `sessao.yaml` 116) e aprendido
   (10 receitas, 1 tela candidata, 2 falhas no backlog, 23 sinais `tela_vista`). Não há filtro no backend nem no frontend que o esconda.
   Ele "desaparece" por seis causas somadas (§4).
3. **O que a IA usa de fato no runtime é pouco do que o sistema "sabe"**: receitas e fluxos decidem fora do prompt; o catálogo entra no
   planejador; lições estão em `shadow` (medidas, nunca no prompt); telas aprendidas em `observe` (nunca publicam); `telas.yaml` não vai ao prompt.
4. **Não há IA no pipeline de aprendizado** (decisão do ADR-054). Todo o ciclo é regra determinística + gesto humano (D1/D2). Bom ponto de partida
   para a curadoria por IA, desde que ela entre como intérprete auditável, não como dona do lifecycle.
5. **Versão do app, persona, capability, lineage e substituição são quase ausentes como dado estruturado** — existem pedaços (receita tem
   `app_version` na chave; `learning_items` tem `scope_*`, `parent_id`; releases têm `observed_version_code` por aparelho) que nunca se cruzam.
6. **A instrumentação por app tem duas arestas** (CORRIGIDO em 02/10 após medição): `steps.app_id` NULL em 99% é DESENHO — a etapa
   herda o app do plano (`planning/parsing.py:204-207`) e o aprendizado já resolve o app por etapa (`app_da_etapa`, `taskqueue/projecao.py:72`),
   inclusive em `learning_daily`, falhas e backlog; o que resta é o fluxo multi-app registrado no app do plano e o `failure_screen` vazio no
   backlog antigo (falhas anteriores ao escritor de 29/09 ou de apps sem `telas.yaml`).

---

## 2. Três camadas: declarativo × aprendido × usado em runtime

| Conhecimento | Declarativo (repo, por pacote) | Aprendido (banco, por execução) | Usado em runtime | Aparece no Aprendizado? |
|---|---|---|---|---|
| Identidade do app, provedor de sessão, âncora, renderizador recusado | `app.yaml` | — | sim (despacho, compatibilidade, apps de fundo) | não |
| Ações do app (capabilities), risco, pré/pós-condição | `catalogo.yaml` (só Instagram na main; Outlook no 12.3) | — | sim: planejador (`PlanRequest.catalog`), porta de política, prova local de VERIFY | não (só `/api/capabilities`) |
| Login e conferência de conta | `sessao.yaml` (motor `SessaoDeclarada`) | — | sim (fora do laço da IA) | não |
| Receita (ações por seletor de uma etapa) | — | `recipes` (`candidate→active/validated→quarantined/superseded`) | sim, decide a etapa SEM IA (ativa) ou em sombra (candidata) | sim, como linha genérica; conteúdo só em Configuração |
| Fluxo (plano de um comando) | — | `flows` (`match_key`) | sim, substitui o planejador | sim, linha genérica |
| Habilidade (skill versionada) | DSL/ensino/import | `skill_versions` | sim, resolve o comando antes do fluxo | sim, sem ação no livro |
| Lição | — | `learning_items kind=licao` | **não no central** (`licoes.modo=shadow`) | sim |
| Voz, preferência | — | `learning_items` (por persona) | voz só publicada pelo dono; preferência só pré-preenche | sim |
| Memória da persona | — | `memory_items` | contexto de persona | sim, 112 linhas sem app e sem estado |
| Falhas | — | `learning_backlog`, `attempts.failure_kind`, `learning_daily` | não | aba "O que mais falha" |

**Conclusão conceitual**: a tela atual não "mistura errado" declarativo e aprendido — ela simplesmente **não mostra o declarativo**. E onde os
dois se tocam (tela aprendida → absorvida no `telas.yaml`), o conhecimento sai da visão justamente quando vira confiável. A arquitetura
pedida no briefing §2/§3 precisa de uma **visão por app que una as duas origens com a fonte explícita**, sem copiar o declarativo para o banco
(o registro de apps já carrega os YAML em memória; ele é a fonte da lista de apps).

---

## 3. Respostas às 15 perguntas do briefing §1 (síntese; detalhe nos relatórios)

| # | Pergunta | Resposta curta | Onde |
|---|---|---|---|
| 1 | De onde nasce | receita: etapa da IA limpa (`distill`); fluxo: run `completed` (`learn_from_run`); habilidade: ensino/import/manual; tela: sinal `tela_vista` em etapa comprovada; lição: contraste falha→sucesso; voz: aprovação editada; preferência: 3 respostas iguais | A, B |
| 2 | Onde persiste | `recipes`, `flows`, `skill_versions`, `memory_items`, `learning_items` (+ `learning_transitions/evidence/signals/exposures/daily/backlog`) | A |
| 3 | Validação | regra determinística por repetição (`promocao.py`: n=3, execuções=2, aparelhos=1, contra=0; por tipo do config); receita: sombra com `promote_after=2` | A, B |
| 4 | Evidência | `learning_evidence` (só real promove); receita usa `replay_ok/fail` e `shadow_*` nas próprias colunas | A |
| 5 | Promoção | sistema publica só sem efeito, sem origem humana e com modo `on`; senão D1 (pessoa) | A |
| 6 | Rebaixamento | receita: quarentena após 3 falhas seguidas; fluxo: 2 discordâncias; lição: neutra aos 20 / 60 dias sem exposição | A, B |
| 7 | Desativação | sistema por contradição/medida; pessoa por gesto; reativar só pessoa; veto 90 dias ou até mudar versão | A |
| 8 | Uso na execução | comando: habilidade → fluxo → planejador (catálogo ou livre); etapa: receita ativa → sombra → IA; cascata de tier | B |
| 9 | Relação com app | chave = pacote Android; fluxo/habilidade via `apps.package` ou `app_id` cru (chaves podem divergir) | A, C |
| 10 | Persona | só voz, preferência e memória têm persona; receita, fluxo, tela, lição não | A |
| 11 | Versão do app | declarativo não tem; receita tem na chave; tela aprendida guarda e usa como sinal de obsolescência; releases têm por aparelho; nada cruza | C |
| 12 | Receita/fluxo/skill/tela/capability | receita ↔ etapa por `step_hash` (sem capability); fluxo ↔ skill disputam `match_key`; tela sem chave comum; capability só no plano | B |
| 13 | Declarativo | os 4 YAML por pacote (§2) | C |
| 14 | Aprendido por execução | receitas, fluxos, telas, lições, voz, preferências, falhas | A, C, E |
| 15 | Usado pela IA | ator: hierarquia+imagem, `AppContext`, lições (em shadow = nada); planejador: catálogo e lições; receitas/fluxos decidem fora do prompt | B |

---

## 4. Resposta: por que o Outlook não aparece no Aprendizado

**Não é**: filtro do backend (SQL × API batem por app e estado — E), filtro do frontend (nenhum literal de app, nenhum filtro de app cabeado — D),
hardcode de Instagram no runtime (B: zero ocorrências que excluam outro app), nem "não existe conhecimento" (C: 3 arquivos declarados).

**É a soma de seis causas** (ordem de peso estimado):

1. **A tela não tem eixo de aplicativo** (D). Os 11 itens do Outlook ficam diluídos numa lista plana de 144 (112 são memórias sem app).
   A aba padrão "Para aprovar" mostra 1 pendente + 39 a revisar, **nenhum do Outlook** (E). A tela "Apps" mostra só "receitas ativas" — o Outlook tem 0.
   Um app sem item nunca aparece, nem como vazio.
2. **O conhecimento declarado não entra no Livro por construção** (A). `telas.yaml`, `sessao.yaml` e `app.yaml` do Outlook (o que o sistema
   mais sabe dele: login gerenciado, 158 linhas de telas) são invisíveis no Aprendizado. O mesmo vale para o Instagram, mas lá o volume aprendido disfarça.
3. **Volume real pequeno e sem repetição** (E + conferência). 5 runs, 4 comandos distintos, 7 `step_hash` distintos, 1 versão do app: nenhuma etapa
   se repetiu, então nenhuma receita candidata teve a 2ª concordância em sombra (`promote_after=2`) → `replay_ok=replay_fail=0`, `last_used_at` NULL.
   A tela candidata tem `distinct_runs=1` (precisa de 3 observações em 2 execuções) e, mesmo validada, não publica com `telas.modo=observe`.
   Parte das receitas (`fill_recipient`, `fill_subject`, `send_email`) é de envio — com `commit` elas parariam em `validated` esperando o dono.
4. **Sem `catalogo.yaml` na main** (C, B). Sem capabilities: `/api/capabilities` devolve `[]`, o planejamento é livre, não há eixo de ação para
   agrupar receitas/falhas/lições do app (capability `*`), e a porta de política recusa efeito como `risco desconhecido`. O 12.3 (Android, não mergeado)
   traz 3 ações de leitura e `saidas`. Note: a falta de catálogo NÃO explica a falta de reprodução — o QAMessenger também planeja no caminho
   livre e tem 61 `step_hash` distintos, 59 receitas ativas e 399 reproduções. O que reproduz é o comando repetido (causa 3).
5. **Fluxo multi-app fica no app do plano** (E). O único fluxo envolvendo Outlook é um candidato **do Instagram** (Outlook só em
   `flow_required_apps`); fluxo/habilidade sem `apps.package` ficam com `app=None` e nunca casam com `app=`. (CORRIGIDO 02/10: a suspeita de que
   `learning_daily` fundia o run misto e de que `steps.app_id` nulo perdia o app foi refutada — o aprendizado já atribui por etapa; teste
   `test_aprendizado_app_por_etapa.py` trava o comportamento. A tela Apps, `apps_overview.py`, usa o 1º app do run: a conferir.)
6. **Modos conservadores no central** (conferência): lições `shadow`, telas `observe`. O que o Outlook "aprenderia" de telas e lições é medido mas
   nunca publicado nem usado — e a tela não diz isso.

**Resposta à pergunta conceitual**: a tela confunde "o que o sistema possui" com "o que ele aprendeu" por omissão — mostra só o segundo, sem dizer.
A correção não é copiar o declarativo para `learning_items` (segunda verdade, briefing §18), e sim uma **visão por app que compõe, na leitura**,
o registro de apps (declarado, dono = repositório) com o Livro (aprendido, dono = cada tabela nativa), com a origem sempre explícita.

---

## 5. Dados reais por app (central, 02/10, `real`)

| | Instagram (`com.instagram.android`) | QAMessenger (`com.pocqa.messenger`) | Outlook (`com.microsoft.office.outlook`) |
|---|---|---|---|
| Declarativo | app, catálogo (23 ações), telas, sessão | **nenhum** (sem pasta; caminho livre) | app, telas, sessão; **sem catálogo** |
| Runs (por `app_ids`) | 83 (+4 mistos com Outlook) | 160 (+3 com Chrome) | 1 só + 4 mistos |
| Receitas | 19 active (39 ok/10 falha), 4 quarantined | 59 active (399/9), 8 quarantined, 4 candidate, 1 validated | 7 candidate, 3 superseded, 0 usos |
| Fluxos | 11 active (14 usos), 1 candidate | 13 active (37 usos) | 0 |
| Habilidades | 1 (`ig.abrir_conversa`) | 0 | 0 |
| Telas aprendidas | 0 | 0 | 1 candidata |
| Backlog | 13 falhas (≤ 09-28) | 6 falhas + 25 propostas | 2 falhas (10-01) |

Leitura: o QAMessenger é o app mais "aprendido" **sem nenhum conhecimento declarado** — prova de que o ciclo é genérico; o Instagram tem as duas
camadas; o Outlook tem o declarado e quase nada aprendido. É exatamente o contraste que a prova do briefing §22 pede, e a tela atual não mostra.

---

## 6. Causas estruturais (para as Fases B–G)

| # | Causa | Evidência | Briefing |
|---|---|---|---|
| C1 | Não existe a entidade "aplicativo" no domínio de aprendizado (nem lista, nem agregação, nem saúde por app) | A: API sem lista de apps; D: sem eixo | §3 |
| C2 | Declarado e aprendido vivem em mundos sem ponte de leitura; a única ponte (tela absorvida) apaga o item da visão | A, C | §2, §14 |
| C3 | Receita não tem capability nem origem estruturada (só `step_hash`, `learned_from_step`); pré/pós-condição ficam no plano; conteúdo legível só em Configuração | B, D | §4, §17 |
| C4 | Versão do app não é dimensão: só chave da receita e sinal da tela; releases por aparelho não cruzam com conhecimento | C | §13 |
| C5 | Lineage parcial: `parent_id`, `superseded` sem destino, `absorvida:` em texto; sem `substitui/contradiz/derivado de` | A, D | §14 |
| C6 | Evidência é contagem; a origem (`run_id`, `instance_id`, `app_version`) existe em `learning_evidence` e não é exposta; receita guarda evidência em colunas próprias | A, D | §5, §11 |
| C7 | Atribuição por app com arestas: fluxo multi-app no app do plano, `failure_screen` vazio no backlog antigo, `apps_overview` pelo 1º app do run (a conferir); `steps.app_id` nulo é desenho, resolvido por `app_da_etapa` | E + medição | §9, §16 |
| C8 | Ciclo 100% determinístico + humano, sem interpretação; "O que mais falha" é relatório + backlog sem diagnóstico nem proposta ligada a conhecimento | A, E | §6, §9, §15 |
| C9 | Obsolescência existe só em pedaços (receita: quarentena; lição: neutra/60 dias; tela: 30 dias sem casar em versão nova; veto até mudar versão); sem visão nem sinal unificado | A | §10 |
| C10 | Segunda verdade no front: `acoesDoItem` espelha `ciclo.py` à mão; taxas, tendência e ordenações recalculadas; evidência recontada | D | §18 |
| C11 | Persona: só voz/preferência/memória têm escopo de persona; o resto é global por app; uso por persona não é medido | A | §12 |
| C12 | Modos `shadow`/`observe` invisíveis na UX: o dono não sabe o que é medido mas não usado | conferência | §5, §24 |

## 7. Princípios a preservar
- Livro como **agregador sem cópia** (dono do conteúdo = tabela nativa; `content_hash` só para identidade).
- Tabela fechada de transições (`ciclo.py`) e D1: nada com efeito externo ou origem humana publica sozinho; reativar é humano; veto.
- Evidência só real promove; contagem por execução e aparelho distintos.
- Lições com teto de tokens e braço de controle (medir antes de usar).
- App como dado (ADR-052): nenhuma lógica por app no código; chave = pacote.
- Segredos fora das receitas (`SENSITIVE_PARAM`, `type_secret` fora).

## 8. Direção para a Fase B (a desenhar com o orquestrador; nada implementado)
- **Visão por app como composição de leitura**: registro de apps (`conhecimento/apps/*` + apps com linhas aprendidas) × Livro, com origem
  (`declarado` / `aprendido` / `absorvido`) e camada de uso em runtime (`decide sem IA` / `vai ao prompt` / `medido, não usado`). Sem tabela nova de conhecimento.
- **Detalhe de receita legível** a partir de `recipes.actions` + etapa de origem (`learned_from_step` → step → run), sem segredos.
- **Dimensões de saúde** só com sinais que já existem (uso, `replay_ok/fail`, `shadow_*`, `distinct_runs/devices`, freshness, versão), cada uma explicável.
- **Curador por IA** como camada de interpretação sobre um dossiê determinístico, gravando decisão sugerida auditável; o lifecycle continua em `ciclo.py`.
- **Atribuição por app** (passo/etapa, não run) como pré-requisito de métricas e da curadoria — toca `taskqueue/` (Jev/Pedidos): combinar.

## 9. Conflitos e dependências (para o orquestrador)
- 12.3 (Android): o catálogo do Outlook é pré-requisito da prova do Outlook (§22); a Fase B pode começar sem ele, a H não.
- Atribuição de app por etapa e `failure_screen` tocam `taskqueue/executor.py`/`costuras.py` (Jev, 28.4 em curso).
- Lições × perfil de IA (17.7) / cascata (17.10): `PedidoDeLicoes` não carrega `ai_profile`/tier — só registrar; mudança passa pelo orquestrador.
- Índice de ADRs defasado (ADR-052 "fatias 2–5 propostas", ADR-054 "A2–A9 pendentes", ADR-058 "proposto"): correção documental pequena, fora do código.
- Decisões do dono que a Fase B vai precisar: publicar telas/lições (`observe`/`shadow` → `on`) em algum app; política de risco para aprovação
  automática; teto de gasto da curadoria por IA.

## 10. Lacunas desta Fase A (não medidas)
- Corpo do ADR-054 e `docs/dominios/aprendizado.md:262-396` não lidos por inteiro; testes do aprendizado não lidos.
- Onde `AppContext` (`nav_hints`, `known_selectors`) é montado.
- Motivos das 19 receitas desativadas e dos `needs_input`.
