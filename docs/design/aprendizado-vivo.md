# Aprendizado vivo — desenho da Fase B

Frente Aprendizado, 02/10/2026. Desenho, não código. Parte do diagnóstico da Fase A ([`aprendizado-vivo-diagnostico.md`](aprendizado-vivo-diagnostico.md));
a especificação é o pedido do dono (briefing de 24 seções, guardado no handoff local da frente, `.claude/handoffs/aprendizado-briefing.md`).
Base de código: `origin/main` fdf25a4c, mais o 12.3 mergeado em c4e6f893 (o Outlook tem `catalogo.yaml` com 3 ações só de leitura:
`OPEN_MAIL_INBOX`, `COLLECT_MAIL_HEADERS`, `SEARCH_MAIL`, com `saidas` e o ADR-065). Decisão proposta: **ADR-067** (§14, número reservado;
entra em [`decisoes.md`](../decisoes.md) depois do PR do índice). Plano: **Fase 30** do plano-100 (§13).

**Prova**: nada aqui foi executado (`not_run`). Os números do central marcados `real` são leituras de 02/10 (central em c82a5210, migração 067).
Onde o fato não foi conferido no código, está escrito **a conferir**.

Números do central usados no desenho (`real`, leitura, 02/10):
- 0 lições e 0 exposições, apesar de 29 contrastes falha→sucesso em 14 dias: em `shadow` a lição não grava exposição
  (`modules/learning/application/licoes.py:171-179`). Causa medida: o digest que minera lições só existe desde 29/09 (migração 055) e não há backfill; os 12 contrastes aprováveis são anteriores.
  Decisão do orquestrador (02/10, ambiente central de validação, sem IA, sem aparelho, sem conta; dono informado): backfill único e idempotente
  só das lições, só nas 12 execuções com contraste aprovável (30.22).
- ~10 candidatos novos por dia (pico 45); 29 de 31 transições foram do sistema; fila do dono: 1 pendente + 39 "a revisar".
- 23 receitas com ação `commit` (4 Instagram, 18 QAMessenger, 1 Outlook). A do Outlook (receita 100) é anomalia: veio do envio do cenário C1
  de 01/10, no planejamento livre, antes do catálogo. Desde o 12.3 a porta de política recusa efeito no Outlook sem ação do catálogo, e o
  catálogo dele não tem ação com efeito. Vira sinal de obsolescência com rebaixamento (§9.2).
- Custo estimado de uma revisão por IA: ~US$ 0,005 (Haiku) a ~US$ 0,02 (Opus 5.5).

---

## 1. Resumo e o que se preserva

O aprendizado deixa de ser uma lista plana e passa a ter **eixo de aplicativo**, **conteúdo legível**, **saúde explicável**,
**compatibilidade por versão**, **lineage mínimo** e um **curador por IA auditável**. O lifecycle não muda de dono.
A forma é uma **composição de leitura**: o Livro continua agregador, cada conteúdo continua na casa nativa, e a única tabela nova é a
trilha das revisões da IA (`learning_reviews`, §8.5). Ela é registro de auditoria, não segunda verdade de conhecimento.

**Preservado do ADR-054, sem exceção:**

| Princípio | Onde vive | Como este desenho o respeita |
|---|---|---|
| D1: o sistema publica sozinho só sem efeito externo, sem texto de pessoa e com o modo do tipo em `on` | `domain/ciclo.py:42-51`, `:124-155`; repositório no `UPDATE` com CAS | A IA só emite parecer e nunca chama o repositório. Toda transição continua passando por `conferir_transicao` e pela política de risco (§8.4) |
| Tabela fechada de transições; reativar é de pessoa; veto por `content_hash` | `ciclo.py` | Não muda. Nenhum estado novo; "saúde" e "versão" são leituras derivadas, não estados |
| Só evidência real promove, contada por execução e aparelho distintos | `domain/promocao.py:36-50` | A IA não cria evidência: ela cita evidências do dossiê, e uma citação fora do dossiê invalida a resposta (§8.3) |
| Livro sem cópia; dono do conteúdo = tabela nativa | `application/servico.py:124-135`, `infrastructure/fontes.py` | A visão por app compõe registro de apps + Livro na leitura. Nada de YAML, receita ou fluxo copiado para `learning_items` |
| Falha em vocabulário fechado; prova da correção medida (`fixed`/`reopened` nunca vêm de pessoa) | `domain/falhas.py`, `domain/backlog.py` | A proposta da IA é um registro novo ligado ao grupo. Ela não marca `fixed` |
| Lições com teto de tokens e braço de controle | `domain/tokens.py`, `domain/licoes.py` | Não muda. O curador não escreve texto de lição |
| App como dado (ADR-052): zero lógica por app | `conhecimento/apps/*`, `modules/applications/infrastructure/registry.py` | Lista de apps sai do registro e dos dados; nenhum pacote literal no código ou no front |
| Segredos fora (`SENSITIVE_PARAM`, `type_secret` fora da receita, nota com cara de credencial recusada) | `taskqueue/recipes.py:31-36`, `TriagemDeTexto` | O dossiê da IA é montado de campos estruturados e passa pela mesma triagem. Texto livre de tela não entra |

**Revisto em parte:** a decisão 9 do ADR-054 ("nenhuma IA no pipeline"). A IA entra como **intérprete** sobre um dossiê determinístico, com modo,
orçamento proporcional e trilha próprios, e fora do digest por execução e da curadoria determinística (§8). `aprendizado.ia_resumos_por_dia` continua
aceitando só 0: lição escrita por IA segue descartada.

---

## 2. Modelo de domínio: tipos, escopos e donos

### 2.1 Quem é dono de cada conteúdo (três casas, não duas)

| Casa | O que guarda | Muda por | Lifecycle |
|---|---|---|---|
| **Repositório** (`backend/app/conhecimento/apps/<pacote>/`) | `app.yaml` (identidade, sessão, âncora, renderizador), `catalogo.yaml` (ações, risco, efeito, pré/pós-condição, `saidas`), `telas.yaml`, `sessao.yaml` | commit (PR) | nenhum no Livro: "vigente no commit X" |
| **Loja** (tabela `apps`) | `name`, `package`, `activity`, `nav_hints`, `known_selectors`. Vão ao prompt do ator por `AppContext` (`taskqueue/scheduler.py:1409`, `service.py:290`) | rota da loja | nenhum |
| **Tabelas nativas do aprendizado** | `recipes`, `flows`, `skill_versions` (+`skill_definitions`), `memory_items`, `learning_items` (tela, lição, voz, preferência) | execução, ensino, gesto | `ciclo.py` (receita e fluxo pelo mapeamento de `domain/livro.py:27-58`; habilidade pela rota própria) |

Os registros auxiliares (`learning_evidence`, `learning_transitions`, `learning_signals`, `learning_exposures`, `learning_daily`,
`learning_backlog` e, novo, `learning_reviews`) **não** são conhecimento: são evidência, trilha, medida e auditoria sobre ele.

### 2.2 Escopo de cada tipo: hoje × devido

Escopos possíveis: global · app (pacote) · capability · versão do app (+ assinatura e variante) · persona · conta · aparelho.

| Tipo | Escopo hoje (fonte) | Escopo devido | Mudança |
|---|---|---|---|
| Declarado (`app.yaml`, `telas.yaml`, `sessao.yaml`) | app; sem versão (`versao: 1` é esquema) | app; independente de versão até o YAML declarar o contrário | nenhuma |
| Catálogo (capability) | app × capability; `contract_version` fixo em 1 | idem | nenhuma (versionar capability fica fora desta fase) |
| `apps.nav_hints`/`known_selectors` | app (loja) | idem | só aparecer na visão por app como "declarado (loja)" |
| Receita | app × versão × assinatura × variante × `step_hash` (`recipes`, chave única da 010) | idem; **capability derivada na leitura** (§4) | nenhuma no modelo |
| Fluxo | comando (`match_key`) + `app_id`; `flow_required_apps` | app principal + apps exigidos; independente de versão | chave de app canônica (30.2) |
| Habilidade | `match_key` + `app_ids` | idem; independente de versão | chave canônica (30.2) |
| Tela aprendida | app; `app_version` como sinal | app (versão como sinal de obsolescência, já é) | nenhuma |
| Lição | app × capability (`*` = livre) × `step_hash` × papel; `app_version` só desempata | idem | nenhuma |
| Voz, preferência | persona (`scope_profile_id`) | persona | nenhuma |
| Memória | persona (`memory_items.profile_id`) | persona; **fora do eixo de app** | sai da contagem por app na UX |
| Sessão por conta, login | conta (motor `SessaoDeclarada`) | conta | não é conhecimento do Livro; só o reflexo "login gerenciado" na visão do app |
| Aparelho | só como evidência (`learning_evidence.instance_id`, sinais) | só evidência | nenhuma: nenhum conhecimento é "do aparelho" |

**Regra de obsolescência por escopo** (briefing §12): um item só é julgado sem uso no **próprio** escopo. Uma receita
(escopo app × versão × etapa) não fica obsoleta porque uma persona deixou de usar o app. Ela fica obsoleta quando a etapa continua sendo
executada naquele app e versão e outra coisa a conduz (§9.2). Voz e preferência de persona só são julgadas pelas execuções daquela persona.
Uso por persona de receita e fluxo não é medido hoje: `runs` não tem `profile_id` (só `objectives.profile_id`, 010). O caminho de junção
fica **a conferir**; até lá, "usado por persona" aparece como "não medido".

---

## 3. Declarado × aprendido: a visão por app como composição de leitura

### 3.1 Lista de apps (sem tabela nova)

`apps_do_aprendizado = registry.registered() ∪ {apps.package} ∪ {pacotes distintos das fontes do Livro}`.
Cada app traz a sua **origem de existência**: `declarado` (pasta no repo), `loja` (só na tabela `apps`), `só aprendido`
(o pacote aparece em linhas do Livro e em nenhuma das outras duas). Um app sem item aparece com zeros, não some. Assim o Outlook aparece
com o declarado e "0 publicados" (causa 1 do diagnóstico), e o QAMessenger aparece como `loja` + muito aprendido, sem nenhum declarado.

Chave canônica = pacote Android. Fluxo e habilidade que caem no `app_id` cru (`fontes.py:27-30`) são resolvidos pela tabela `apps`
e, sem ela, ficam no balde "app não resolvido", que é visível e contado (30.2). Hoje eles somem do filtro `app=`.

### 3.2 Origem de cada linha da visão

| Origem | Significado | Fonte | Ações no Livro |
|---|---|---|---|
| `declarado` | vem do repositório ou da loja; vale enquanto o commit ou a linha valer | registro de apps, `apps` | nenhuma (mudar = PR ou loja); link para o arquivo e o commit implantado |
| `aprendido` | nasceu de execução, ensino, treino ou gesto; tem lifecycle | tabelas nativas | as do `ciclo.py`, calculadas no backend (§5.4) |
| `absorvido` | foi aprendido e hoje é declarado (`state_detail = absorvida:<commit>`) | `learning_items` + YAML | nenhuma; mostra os dois lados (o item aposentado e a regra declarada que o absorveu) |

O item absorvido **não some**: na visão do app ele aparece com `absorvido em <commit>` e com o histórico. O filtro padrão por estado
(published) deixa de escondê-lo porque a origem `absorvido` tem chip próprio.

### 3.3 Camada de uso em runtime (o que o dono não vê hoje, causa C12)

Derivada do tipo, do estado e dos modos do `LearningCfg` e de `ai.recipes`/`ai.flows`. É uma função só no backend; o front exibe o rótulo e o "por quê".

| Tipo e estado | Camada | Por quê (texto devolvido pela API) |
|---|---|---|
| Receita `active` (`ai.recipes: replay`) | **decide sem IA** | reproduz por seletor; diverge → IA assume (`recipes.py:5-8`) |
| Receita `candidate` | **medido, não usado** | roda em sombra; promove com `recipes_promote_after` concordâncias |
| Receita `validated` | **inerte, espera o dono** | tem `commit` (D1) |
| Fluxo `active` (`ai.flows`) | **decide sem IA** | substitui o planejador para o comando |
| Fluxo `candidate` | **medido, não usado** | sombra dos fluxos (`application/nativos.py:215`) |
| Habilidade `published` (`skills.enabled`) | **decide sem IA** | resolve o comando antes do fluxo |
| Lição `published` com modo efetivo `on` (global ou do app, §8.10) | **vai ao prompt** (ator e planejador, com braço de controle) | teto de tokens |
| Lição com modo efetivo `shadow` | **nem medido** (0 exposições no central) | em `shadow` não grava exposição (`licoes.py:171-179`) |
| Tela aprendida `published` com modo efetivo `on` | **classifica tela (sem prompt)** | `com_aprendidas` |
| Tela aprendida com modo efetivo `observe` | **medido, não usado** | grava, minera e valida; a sessão não consome |
| `catalogo.yaml` | **vai ao prompt do planejador**, à porta de política e à prova local | |
| `telas.yaml` | **classifica tela e detecta bloqueio (sem prompt)** | `ScreenInput` sem telas conhecidas |
| `sessao.yaml` | **login fora da IA** | motor `SessaoDeclarada` |
| `apps.nav_hints`/`known_selectors` | **vai ao prompt do ator** | `AppContext` |
| Preferência | **pré-preenche** | só decide sozinha sem etapa com efeito |
| Voz, memória | contexto da persona | onde entram no prompt social: **a conferir** |

---

## 4. Receita legível

O detalhe da receita (`GET /api/aprendizado/receita/{id}`) passa a devolver o conteúdo inteligível, só a partir do que existe, sem coluna nova.

| Campo exibido | Fonte | Observação |
|---|---|---|
| App, versão, assinatura, variante, etapa (`step_key`), versão da receita, estado | `recipes` | já existe (hoje só em Configuração) |
| Sequência de ações: ferramenta, alvo por seletor (`rid+text`, `rid+desc`, `rid`, `desc`, `text`), `commit`, parâmetros | `recipes.actions` | texto digitado já é 100% parâmetro (`{nome}`): mostra o NOME do parâmetro, nunca valor; `SENSITIVE_PARAM` e `type_secret` já não entram |
| **Efeito externo** | `receita_tem_efeito(actions)` (`domain/livro.py:70`) | selo + qual ação faz o `commit` |
| **Capability** | derivada: `recipes.learned_from_step → steps.capability`; sem origem, `steps.template_hash = recipes.step_hash` no mesmo app | `steps` e `runs` não são purgados (só `ai_calls`, `state.py:2568`). Com mais de uma capability para o mesmo `step_hash`, mostra todas e marca "ambíguo" |
| **Pré e pós-condição, guardas** | da etapa de origem (`learned_from_step → steps` → plano da execução) | são do plano, não da receita: rotular "da etapa que a originou". Onde o plano guarda guardas e pós-condição por etapa: **a conferir** |
| **De onde nasceu** | `learned_from_step` → `steps.run_id` → execução (link); prefixo `training:` = treino | origem `execucao` / `treino` |
| Uso | `replay_ok`, `replay_fail`, `consecutive_fail`, `last_used_at` | |
| Sombra | `shadow_agree`/`shadow_total` | candidata |
| Aparelhos em que reproduziu | `steps.driven_by in ('recipe','recipe+ai')` + `template_hash` + `runs.instance` | junção aproximada (o `steps` não guarda id nem versão da receita): **a conferir**; rotular "aproximado" |
| Trilha e motivo de promoção ou rebaixamento | `learning_transitions` (`item_ref='receita:<id>'`) | receitas antigas não têm trilha: "anterior à trilha" |
| Substituída por, substitui | mesma chave, `version` vizinha (`recipes.py:562-575`) | §6 |
| Validada em quais versões | a própria `app_version` (receita não cruza versão) + quadro de versão do §7 | |

**Não existe e não se inventa:** capability gravada, contrato de capability referenciado, pré/pós-condição própria, "campos usados" além
dos parâmetros, histórico de edição (receita é imutável: mudar é nova versão). **Proposta de modelo (não nesta fase):** gravar `capability`
em `recipes` quando a etapa tem uma (migração futura) e, com ela, `steps.recipe_id` para medir uso por receita. Não é pré-requisito
de nada da Fase 30.

O mesmo molde vale para **fluxo** (plano por etapa: chave, capability, alvo, pós-condição; `source_run_id`; efeito por `fluxo_tem_efeito`)
e **habilidade** (documento DSL e versão; a rota é a das habilidades).

---

## 5. Saúde: dimensões separadas, um rótulo explicável

### 5.1 Princípio

Nenhuma nota de 0 a 100. A saúde é um conjunto de **dimensões medidas** e um **rótulo derivado por regra**. Cada rótulo devolve a
lista de fatos que o produziram (o "por quê" clicável). Uma função só no backend (`domain/saude.py`, pura) calcula. A lista, o detalhe,
a visão por app e as métricas usam a mesma função. O front não recalcula taxa, tendência nem ordem (causa C10).

### 5.2 Dimensões (só sinais existentes)

| Dimensão | Receita | Fluxo | Lição, tela, voz, preferência | Habilidade |
|---|---|---|---|---|
| Uso | `replay_ok+replay_fail`, `last_used_at` | `flows.uses`, último uso | exposições `with` (lição), `last_used_at` | `uses_lock`/uso: **a conferir** |
| Eficácia | `replay_ok/(ok+fail)`, `consecutive_fail`; sombra `shadow_agree/shadow_total` | concordâncias e discordâncias na sombra | lição: `medida:ajuda/neutra/atrapalha`; tela: prova local | `skill_validation_results` |
| Base de evidência | contadores próprios | `learning_evidence` | `evidence_for/against`, `distinct_runs`, `distinct_devices` | casos de validação |
| Frescor | dias desde `last_used_at` / `created_at` | idem | idem; tela: `sem_casar` em versão nova | idem |
| Versão | §7 | independente | tela: `app_version`; lição: só desempata | independente |
| Contestação | quarentena, votos D2 "deu errado" por navegação | 2 discordâncias | `against`/`conflict`, 2 refutações humanas | `uncertain` |
| Intervenção humana | sinais `tomou_controle`/`confirmou_a_mao` com mesmo `app_package`+`step_hash` | idem por etapa do plano | idem | idem |

Dimensão sem dado é `desconhecida` (nunca zero), no mesmo espírito de `metricas.py`.

### 5.3 Rótulos e regras (limiares no config; APROVADOS pelo dono em 02/10, D-5)

**D-5 (aprovada como proposta, 02/10):** a tabela abaixo foi o rascunho. Vale a versão aprovada, medida sobre as 79 receitas ativas do
central: `degradando` = ≥ 2 falhas seguidas, ou eficácia < 0,8 com ≥ 5 usos, ou contra/conflito em 7 dias; `sem_evidencia` = publicado
há ≥ 14 dias e nunca usado; `parado` (novo) = já usado, sem uso há > 14 dias; `pouca_amostra` (novo) = usado nos últimos 14 dias com < 5
usos; `saudavel` = o resto com uso recente, ≥ 5 usos, eficácia ≥ 0,8 e sem contestação; `indeterminado` = sem dado para decidir (nunca
vira `saudavel`). Eficácia acumulada (`replay_ok/(ok+fail)`), sem janela de 10 usos. Rótulo só de leitura: nada desligado sozinho,
exibido no painel, sem aviso no Telegram. Config `aprendizado.saude.{sem_uso_dias: 14, amostra_minima: 5, taxa_minima: 0.8,
falhas_seguidas: 2, contestacao_dias: 7}`. Detalhe e números: `api-contract.md` adendo v0.52.

| Rótulo | Regra (todas determinísticas) |
|---|---|
| `inativo` | estado `deprecated` ou `disabled` (com o motivo da trilha) |
| `em_prova` | `candidate` ou `validated`; mostra o que falta (`veredito_de_repeticao`, sombra x/`promote_after`, "espera o dono") |
| `sem_evidencia` | publicado, uso = 0 e evidência a favor = 0 há ≥ `saude.sem_uso_dias` (14) |
| `degradando` | publicado e (`consecutive_fail ≥ 1`, ou taxa dos últimos `saude.janela_usos` (10) usos < taxa histórica − 20 p.p., ou ≥1 contra/conflito nos últimos 7 dias, ou intervenção humana na etapa ≥ 2 em 7 dias) |
| `obsoleto_provavel` | publicado e algum sinal do §9.2 |
| `saudavel` | publicado, usado nos últimos `saude.sem_uso_dias`, eficácia ≥ `saude.taxa_minima` (0,8), sem contestação recente |

A ordem de avaliação é a da tabela (o primeiro que casa vence). O rótulo **não é estado** e não move nada. Quem move continua sendo o
`ciclo.py`, pelos gatilhos atuais ou pelo curador (§8).

### 5.4 Ações permitidas vêm do backend

A entrada do Livro passa a trazer `acoes: [{to, rotulo, exige_motivo, motivo_de_bloqueio}]`, calculada de `ciclo.TRANSICOES` +
`conferir_transicao(by=pessoa)` + regras nativas (receita `superseded` não volta; habilidade → rota própria). O front apaga
`acoesDoItem`/`porQueOSistemaNaoPublica` (`frontend/src/features/aprendizado/model.ts:195-250`), e a paridade passa a ser testada no backend.

---

## 6. Lineage e relações (mínimo, sem grafo)

| Relação | De onde sai, hoje | Novo? |
|---|---|---|
| **nasceu de** (execução, etapa, treino, ensino) | receita `learned_from_step`; fluxo `source_run_id`; item `provenance` (até 20 ids); habilidade `source` | não; só exibir |
| **substitui / substituída por** | receita: mesma chave, `version` anterior/seguinte com `superseded`; item: `parent_id` | não |
| **derivado de** | `learning_items.parent_id`; fluxo legado → habilidade `flow:<id>@1` (`run_planning.py:14-20`) | não |
| **absorvido em** | `state_detail = absorvida:<commit>` | não; parsear e ligar à regra do YAML pelo nome |
| **validado por / refutado por** | `learning_evidence` (`for`/`against`/`conflict`, `origin_ref`, `run_id`) e transições com `run_id` | não; expor (hoje chega e não é exibido) |
| **contradiz** | não existe | derivado na leitura: mesmo `scope_key`, `content_hash` diferente, ambos vivos; mais a evidência `conflict` |
| **complementa / depende de** | fluxo → receitas das etapas (`step_hash` do plano); habilidade → capabilities | derivado na leitura, só para navegação |
| **revisado por** (IA) | — | `learning_reviews.item_ref` (§8.5) |

Tudo vira um campo `relacoes: [{tipo, kind, ref, rotulo, desde}]` no detalhe, montado na leitura. Nenhuma tabela de arestas.
Se um dia houver relação que não se derive (ex.: "funde com" decidido por pessoa), ela entra como transição com motivo estruturado,
não como tabela nova.

---

## 7. Versão do aplicativo

**Fontes**: `device_app_state.observed_version_name/code` por aparelho (007), `recipes.app_version` (lida do aparelho na etapa,
`executor.py:743`), `learning_items.app_version`, `learning_evidence.app_version`, `learning_transitions.app_version` (veto).
Equivalência entre `devices.app_version()` e `observed_version_name`: **a conferir** (usar o mesmo formato ao comparar).

**Versões vivas do app** = versões observadas hoje em aparelhos ativos do parque. É o eixo de comparação.

| Estado de versão | Regra | Vale para |
|---|---|---|
| `independente` | o tipo não depende de versão | declarado, fluxo, habilidade, lição, voz, preferência, memória |
| `comprovado` | receita `active` na versão V, V viva, `replay_ok > 0` | receita |
| `nao_testado` | existe receita da etapa (mesma assinatura, variante, `step_hash`) numa versão anterior, e a versão viva V' não tem receita nenhuma | receita (aparece na versão antiga como "não testada em V'") |
| `em_prova` | candidata em V' | receita |
| `falhando` | `consecutive_fail > 0` ou quarentena em V' | receita |
| `incompativel` | quarentenada em V' enquanto a da versão anterior estava `comprovada` | receita; tela aprendida com `sem_casar` em versão nova |
| `superseded` | `status = superseded` (versão nova da mesma chave) | receita |
| `versao_aposentada` | a versão da receita não está viva em nenhum aparelho | receita (não é falha; é só fora de uso) |

Nada é apagado: a receita da versão antiga fica com o estado de versão e volta a valer se um aparelho voltar para aquela versão
(o `find` usa a chave exata). A tela aprendida já guarda a versão e conta as versões novas em que não casa (`domain/telas.py:316`); ela
entra no mesmo quadro.

---

## 8. Curador por IA

### 8.1 Três camadas que não se misturam

```
fatos (dossiê determinístico)  →  interpretação (IA, saída estruturada)  →  decisão (política de risco + ciclo.py)
       domain/dossie.py                porta CuradorDeIA                        domain/politica_de_risco.py + ciclo.py
```

A IA nunca transiciona. O que ela devolve é um **parecer** gravado em `learning_reviews`. A publicação e o rebaixamento automáticos continuam
sendo só os das regras determinísticas de hoje. Na faixa A, o parecer (só com sobra de orçamento) é só registro. Na faixa B, o parecer é recomendação para o dono aprovar em lote;
na C, apoio à decisão item a item. Quem transiciona é sempre o `ciclo.py`: pelo sistema, nas regras atuais, ou pela pessoa, com `review_id` registrando o aceite ou
o override. A tabela do `ciclo.py` não ganha linha.

### 8.2 Dossiê (entrada da IA; só fatos, montado sem IA)

Por item (`kind:ref`), com ids estáveis em cada fato:
- identidade: tipo, app, capability (derivada), versão, estado, origem, `side_effect`, `human_origin`, idade;
- conteúdo legível (§4), sem valores de parâmetro, sem texto de tela;
- evidências: até N (padrão 30) de `learning_evidence`, mais as reproduções e a sombra; cada uma com `origin_ref`, `run_id`, aparelho, versão,
  `simulated`, data;
- saúde: dimensões e rótulo (§5) com os motivos;
- versão: quadro do §7;
- lineage: relações do §6 (predecessor e substituta, com os estados);
- falhas relacionadas: grupos do backlog com o mesmo app × capability (× tela);
- intervenções humanas e votos D2 da etapa;
- risco: classe do §8.4 e os campos de catálogo que a determinam;
- política vigente: modo do tipo, limiares e o que o D1 permite.

O dossiê tem `dossie_hash` (sha256 do JSON canônico). Se o hash não mudou desde a última revisão, o item não é revisado de novo.
O dossiê passa pela `TriagemDeTexto` antes de sair; recusa = não revisa e registra o motivo.

### 8.3 Saída da IA (contrato fixo, validado como JSON)

```
{ "decisao": "aprovar|observar|pedir_evidencia|rebaixar|desativar|substituir|fundir|possivelmente_obsoleto|manter",
  "alvo": "kind:ref" | null,                 # para substituir/fundir: o outro item, que precisa estar no dossiê
  "confianca": "baixa|media|alta",
  "conclusao": "≤ 300 caracteres",
  "evidencias_citadas": ["<ids do dossiê>"], # obrigatório e não vazio para qualquer decisão diferente de "manter"
  "riscos": ["..."], "inconsistencias": ["..."], "falta": ["..."] }
```

Validação determinística: um id citado que não está no dossiê, uma decisão fora do vocabulário ou um `alvo` desconhecido tornam a
revisão `invalida`. Ela é gravada assim e não gera ação. Confiança é categórica, não percentual (sem número inventado).

### 8.4 Política de risco (APROVADA pelo dono em 02/10)

A classe sai de campos que já existem. **Vale a mais restritiva** entre a do catálogo (capability da etapa) e a do conteúdo (o `commit` da receita,
a etapa de efeito do fluxo). A política coincide com o D1 e o estreita; não o afrouxa.

| Classe | Como se reconhece | Quem decide | Papel da IA |
|---|---|---|---|
| **A: navegação e leitura** | sem `commit` (`receita_tem_efeito`), sem etapa de efeito (`fluxo_tem_efeito`), capability sem `side_effect.external` e `risk=low`, `human_origin=0` | o sistema, **pela regra determinística atual** (repetição, sombra, modo do tipo em `on`; rebaixamento pelos gatilhos de hoje) | **só opinião de registro, e só com sobra** de orçamento depois das prioridades 1 a 4 (decisão do dono, 02/10; ver a nota abaixo); nunca muda o resultado |
| **B: efeito médio, ou `commit` em app sem catálogo** | capability com `risk=medium`; ou receita/fluxo com `commit` em app sem `catalogo.yaml` (com catálogo e sem ação de efeito para a etapa, a regra é a de obsolescência do §9.2, que rebaixa). O dono confirmou a B em 03/10, e a emenda para C da mesma madrugada foi revertida | o dono, **em lote** | recomenda (aprovar, observar, pedir evidência, desativar); o dono aceita um lote de pareceres com um gesto (`by = pessoa`, um `review_id` por item) ou recusa com motivo (override) |
| **C: alto risco** | `risk=high`, `default_policy=manual_only`, sessão, conta, autenticação (telas e etapas de login, conta errada; `FailureKind.AUTENTICACAO`/`CONTA_ERRADA`), envio, publicação, exclusão (`side_effect.external` com `interaction_type` dessas famílias, `needs_draft`) | **sempre o dono, item a item** | dossiê determinístico e parecer (prioridade 2 do orçamento), nunca em lote. Conteúdo sensível de sessão e autenticação não entra no dossiê. |

Fica fora das três: item com `human_origin=1` e sem efeito (lição de nota, preferência). O D1 já o entrega ao dono. Tratamento proposto:
como a classe B, recomendação da IA e aprovação em lote (D-2, decidido pelo dono em 02/10).

A IA não publica nada em nenhuma classe. Toda transição confere `conferir_transicao(by, side_effect, human_origin, modo)` e o veto, como hoje.
O mapeamento de `interaction_type` para "envio/publicação/exclusão" sai do catálogo de cada app (dado, não código). Quais valores existem
hoje: **a conferir** em `catalogo.yaml`.

**Nota de implementação (30.10, 02/10).** Decisões que o texto acima não fixava:
- *Divergência*: com os fatos da etapa presentes e dizendo "sem efeito", um `commit` no conteúdo é **C** (`commit_fora_do_catalogo`,
  motivo do evento `efeito_externo`), pela regra "vale a mais restritiva" aprovada pelo dono; isso substitui o parêntese da linha B
  (o rebaixamento por obsolescência do §9.2 continua possível, não exclusivo). Sem fatos da etapa (capability não derivável ou
  desconhecida do catálogo), o `commit` fica **B**, como na 30.21.
- `default_policy: disabled` conta como `manual_only` (C).
- Os catálogos de hoje (Instagram, Outlook) **não declaram família**; os `interaction_type` existentes são `dm_sent`,
  `comment_replied`, `comment_liked`, `post_liked`, `post_unliked`, `followed`, `unfollowed`, `follow_request_*`. O domínio recebe
  a família como fato (`familia_do_efeito`); as ações de envio e comentário do Instagram já caem em C por `risk: high`/`needs_draft`.
- "A IA não decide" vira regra de domínio (`conferir_aceite`): aceite de parecer só por pessoa; lote só na B.
- *Faixa A com sobra* (decisão do dono, 02/10, prioridade 5; revê a célula "nunca gasta IA" da tabela, a frase do §8.1 e o
  §8.6-8.7 onde dizem o mesmo): quem decide continua sendo a regra determinística e a IA nunca muda o resultado de um item A, mas
  ela PODE opinar se sobrar orçamento na janela depois das prioridades 1 a 4. No domínio: `ia_permitida(A) = "so_com_sobra"`
  (B e C: `"sim"`; o corte por orçamento é do 30.11); o parecer da A é válido e só de registro (`efeito_do_parecer = "so_registro"`),
  `conferir_aceite` recusa qualquer efeito dele (`so_registro_na_classe_a`) e a `faixa` apontada pela IA não a muda.
- *Saída em rótulos fechados* (orientação da coordenação, 02/10; revê o §8.3): `decisao`, `faixa`, `causa`, `riscos`,
  `inconsistencias` e `falta` são escolhas de conjuntos fechados (`curador.OPCOES_FECHADAS`), para um adaptador de `choice` com
  probabilidade; `confianca` sai da probabilidade da escolha (baixa < 0,60 ≤ média < 0,85 ≤ alta, a recalibrar no `shadow`) e só sem
  ela de um rótulo; `conclusao` vira opcional e é o único texto livre. A `faixa` apontada pela IA só pode endurecer a da política.

**Nota (30.23, 03/10).** O item reaprendido depois de uma evidência inválida (§9.3) é **B**: razão
`reaprendido_de_evidencia_invalida`, motivo do evento `reaprendido`. Ele vem antes das outras razões B (é o motivo mais
específico para o dono olhar o item de novo) e depois das C, que continuam vencendo.

**Decisão do dono (03/10, relatada pela orquestradora).** Quem valida fluxo é a IA, não o dono à mão: "esses fluxos você
precisa aprender a validar com a IA, foi o que eu pedi". O curador revisa os fluxos candidatos com o modo `on` para fluxo e
com usos reais. A classe A publica pela regra determinística (o parecer é registro); a B vai para o lote do dono com o
parecer; a C segue item a item. O dono não abre um fluxo candidato para julgá-lo sozinho. Primeira rodada: os 5 fluxos
candidatos, depois do reinício do deploy da suíte 7 e do LT-3 da Android (usos reais), com teto de US$ 2 e o custo
registrado; o dossiê leva os apps de cada etapa (#133), para que um fluxo entre apps não pareça incoerente.

### 8.5 Registro auditável: `learning_reviews` (migração 069, provisória: confirmar com o orquestrador no commit)

Precisa ser tabela própria porque `ai_calls` é purgada pela retenção e a auditoria não pode sumir.

| Coluna | Conteúdo |
|---|---|
| `id`, `created_at` | `lr-<token>` |
| `item_ref`, `item_kind`, `scope_app` | o item analisado (ou `fk-…` para grupo do backlog, §9) |
| `gatilho` | vocabulário fechado (§8.6) |
| `dossie_hash`, `dossie` | JSON dos fatos usados (sem segredo, sem texto de tela; limite de tamanho) |
| `template_id`, `template_versao` | o prompt é arquivo versionado no repo; grava-se a versão, não o texto |
| `provedor`, `modelo`, `simulated` | o que respondeu de fato |
| `input_tokens`, `output_tokens`, `usd`, `ms` | custo próprio (mesma conta de `ai.prices`) |
| `saida` | JSON do §8.3 como veio, validado |
| `validade` | `ok` / `invalida:<motivo>` |
| `classe_de_risco`, `politica` | a classe e a regra aplicada |
| `decisao_final`, `decidido_por`, `transicao_id` | o que o sistema ou a pessoa fez de fato e a linha de `learning_transitions` |
| `override`, `override_motivo` | a pessoa decidiu diferente da sugestão |
| `resultado_posterior`, `resultado_em` | preenchido pela curadoria após 14 e 30 dias: o item continuou saudável, foi refutado, foi reativado por pessoa |

Sem FK e sem CHECK, no padrão da 055. Nunca purgada (como trilha e backlog).

### 8.6 Gatilhos e filtros (determinísticos antes da IA)

Gatilhos (vocabulário fechado): `nova_pendencia_do_dono`, `a_revisar`, `degradando`, `obsoleto_provavel`, `versao_nova`, `conflito`,
`grupo_de_falha_acima_do_minimo`, `pedido_da_pessoa`.

Filtros, em ordem: modo do curador ≠ `off` → `dossie_hash` mudou desde a última revisão → item fora do cooldown (`cooldown_h`) →
cabe no orçamento proporcional (§8.7) → ordem de prioridade do §8.7. A faixa A não passa daqui: nunca vai à IA. Volume medido: ~10 candidatos por dia (pico 45); a ~US$ 0,005 (Haiku) a ~US$ 0,02 (Opus 5.5) por revisão.

Execução: um laço periódico próprio (`aprendizado.curador.intervalo_s`) sob a **trava de líder** do ADR-064, separado do
`PassoDeCuradoria` (cuja porta promete "nunca chama IA" e continua assim).

### 8.7 Orçamento proporcional (DECISÃO DO DONO, 02/10)

O dono rejeitou teto fixo em US$ e aprovou orçamento **proporcional ao uso**, no espírito do teto proporcional do 17.12.
Parâmetros em `aprendizado.curador.orcamento`.

**Fórmula**: `B_W = min( α · G_W , k · N_W · c̄ )`, janela móvel `W = 7 dias`, com `α = 10%` e `k = 1,5`.
- `G_W` = gasto de IA da operação na janela, **sem a curadoria**: soma de `learning_daily.usd`. A régua diária é durável. `ai_calls` é purgada.
- `N_W` = itens que chegam à curadoria depois dos filtros determinísticos (§8.6).
- `c̄` = custo médio **medido** por revisão: média de `learning_reviews.usd` na janela. Sem histórico, vale a estimativa por tokens do
  dossiê × `ai.prices`.
- `C_W` = custo já gasto pela curadoria na janela. Pode revisar se `C_W + custo_estimado ≤ B_W`.
- **Custo máximo por revisão**: `c_max = 4 × mediana(c_rev)` da janela. Acima disso, o dossiê é cortado (menos evidências) ou cai no modelo
  mais barato do perfil. Se ainda passar, a revisão é recusada (`validade = recusada:custo`).
- Com `G_W = 0` (operação parada), `B_W = 0`: sem operação, não há curadoria paga.

Números de hoje (`real`, central, 02/10): `G_7d = US$ 9,56`, `N_7d = 71`, `c̄ ≈ US$ 0,008` → `α·G = 0,96`, `k·N·c̄ = 0,85` →
**`B = US$ 0,85 por 7 dias`**.

**Prioridade quando o orçamento acaba** (decisão do dono):
1. conflito ou evidência contra em item publicado;
2. alto risco (faixa C);
3. falha recorrente, ordenada por US$ perdido + intervenções;
4. risco médio (faixa B);
5. baixo risco (faixa A): a regra determinística decide; a IA **nunca decide** e só opina (registro) se sobrar orçamento depois de 1–4.

**Salvaguardas relativas** (decisão do dono):
- 1 revisão por (item, `dossie_hash`);
- gasto da última hora ≤ `B_W / 7 / 2`;
- entrada do dia > 3 × a média diária da janela → só as prioridades 1–2, mais alerta (evento + Problem em `/api/health`);
- sem nova tentativa em laço: uma revisão recusada ou inválida não se repete até o dossiê mudar;
- a sobra (o que não coube) vai para a fila humana sem parecer, ou para a próxima janela.

Aviso a 80% de `B_W`, como os tetos de IA atuais.

**Nota de implementação (30.11, 02/10).** Decisões que o texto acima não fixava (detalhe em `dominios/aprendizado.md`):
- A classe A vai SEMPRE por último (prioridade 5), mesmo publicada e contestada: a prioridade 1 é de item B ou C (`so_com_sobra`).
- O corte por orçamento não vira linha de `learning_reviews` (gastaria a chave (item, dossiê) e a sobra não voltaria na próxima janela);
  só `recusada:custo`, `recusada:triagem` e `invalida:*` viram linha e não se repetem até o dossiê mudar.
- O teto da hora (`B_W/W/2`) é conferido sobre o JÁ gasto antes de cada revisão: com `N_W` pequeno ele fica abaixo de uma revisão e
  a leitura "mais esta cabe" nunca deixaria a primeira passar.
- Pico: só com histórico (média > 0) e com pelo menos 5 elegíveis no dia (piso de amostra, a confirmar com o dono); o alerta é log.
- Sem `usd` medido (a 069 é `NOT NULL DEFAULT 0`), `C_W` usa a estimativa recalculada do dossiê gravado; só `usd > 0` é medida.

### 8.8 Modos e abstração de provedor

`aprendizado.curador.modo`: `off` (padrão de fábrica) | `shadow` (revisa e grava; o parecer não aparece na fila; mede a concordância
com as decisões humanas) | `on` (o parecer aparece na fila e no detalhe, e a classe B ganha a aprovação em lote).

**No painel (30.17, adendo v0.72).** Em `shadow` e `off`, o parecer pendente não aparece nem na fila nem no detalhe: o detalhe só avisa
que há um, e ele aparece depois da decisão da pessoa, que o rotula às cegas (o rótulo da ação e o `override` pelo lado). Em `on`, aparece
na fila e no detalhe, e toda decisão conta como vista (`aceitou`/`recusou`). O pedido de revisão (§11.3) só vale em `on`: em `shadow`, o
curador já revisa sozinho.

Porta `CuradorDeIA.revisar(dossie, template) -> (saida_bruta, uso)` em `modules/learning/application/ports.py`. Adaptadores: simulado
(testes, determinístico) e hub de IA. *Correção de 03/10 (30.19): o hub tem 6 papéis de texto em `AI_ROLES` (`config.py:30`:
`plan|decide|verify|escalation|social|persona`), mais o `leitura` fora da lista (12.5), e os métodos fora do ciclo de execução
`generalize`, `orchestrate_targets`, `refine_command`, `generate_social_response`, `generate_persona` e `review_knowledge`. O curador
entrou pelo 30.12 como `review_knowledge`, no papel `plan` emprestado, com origem `curador` e fatia própria do dia, sem papel novo.*
Texto de 02/10: o hub hoje só tem os papéis `plan|decide|verify|escalation|social` (`config.py:28`) e métodos fixos no
`AIProvider`. Ligar o curador a ele (papel novo ou chamada genérica) toca `config.py`/`planning/routing.py`, área da Jev (17.x): é item próprio,
combinado (30.12). **Nada de `modules/context_retrieval/`** no caminho; se um dia o dossiê precisar de contexto de código, entra por outra porta.

### 8.9 Como avaliar se a IA decide bem

Com `shadow`: concordância parecer × decisão humana por classe e tipo, e taxa de override. Com `resultado_posterior`: dos pareceres
aceitos, quantos foram refutados ou revertidos em 30 dias. Critério proposto para passar de `shadow` a `on`: ≥ 30 revisões válidas e
concordância ≥ 90% na classe B. Decidido pelo dono em 02/10 (D-3); a faixa C continua sempre com o dono.

### 8.10 Modo por app para lições e telas (decisão do dono, 02/10)

Hoje `aprendizado.licoes.modo` e `aprendizado.telas.modo` são globais (`config.py:841`, `:851`). O dono decidiu ligar por app:
- **lições**: não ligam agora. Depois do backfill (30.22) e do modo por app (30.20), ligam primeiro no **QAMessenger**, com braço de controle
  (a premissa antiga, "depois da correção do app por etapa", caiu: o app por etapa já existe, `app_da_etapa`, PR #65);
- **telas** `on`: primeiro no **Outlook**, depois da leitura real no android-01.

Desenho: override por pacote, `aprendizado.licoes.por_app: {<pacote>: off|shadow|on}` e `aprendizado.telas.por_app: {<pacote>: off|observe|on}`,
com padrão = o modo global. Uma função só, `modo_efetivo(tipo, pacote)`, usada pela coleta, pelo D1 (`conferir_transicao` recebe o modo
efetivo do pacote do item), pelo fornecedor de lições, pelo fornecedor de telas e pela camada de uso do §3.3. O pacote é dado de instalação
(config), não código: nenhuma regra por app no Python. Item 30.20.

**Painel (30.20, só leitura; adendo v0.64).** O detalhe do app mostra o modo que vale e se é do app ou do global, com o
passo a passo para mudar (arquivo, trecho, reiniciar o central).

**Ideia de backlog (fora do escopo; decisão da orquestradora, 02/10): editar o modo pelo painel.** Hoje o painel só
mostra, porque o config é lido uma vez, ao iniciar (`state.py`), e não há escrita de config no backend. Duas formas, as
duas mexem em núcleo e vão à suíte combinada:
- **(a) gravar o `config.yaml` e recarregar** o bloco `aprendizado` sem reiniciar: toca `config.py` e `state.py`;
  preserva o "dado de instalação" deste §8.10, mas abre escrita em arquivo de instalação pelo painel;
- **(b) levar o override para o banco** (tabela própria ou o store de `settings`), editável com trilha: **conflita com
  este §8.10**, que diz que o pacote é dado de instalação (config), não estado do banco.

Fica para quando o dono pedir edição pelo painel; a orquestradora leva a ele.

### 8.11 Evento de domínio: conhecimento aguardando a pessoa

Quando um item entra nas faixas **B** ou **C** da política (§8.4) e passa a esperar decisão humana, e quando sai dessa espera, o Livro
publica no barramento o evento **`learning.needs_person`**, no padrão de `approval.pending` e `session.needs_person`
([`api-contract.md`](../api-contract.md), tabela de eventos). Persistido (como os dois).

| Campo | Conteúdo |
|---|---|
| `kind`, `ref` | o item (`receita`, `100`) |
| `app` | pacote |
| `faixa` | `B` ou `C` |
| `aguardando` | `true` ao entrar na espera; `false` ao sair (decidido, rebaixado pelo sistema, substituído) |
| `motivo` | vocabulário fechado e curto: `efeito_externo`, `texto_de_pessoa`, `commit_sem_catalogo`, `alto_risco`, `sessao_ou_autenticacao`, `parecer_da_ia`, `reaprendido` (30.23, §9.3); na saída, `decidido_por_pessoa`, `rebaixado_pelo_sistema`, `substituido` |
| `href` | link interno do painel para o detalhe (`#/aprendizado?aba=aprendido&item=<kind>:<ref>`) |
| `desde` | quando entrou na espera |

**Nunca** vai no payload: conteúdo da receita ou do fluxo, seletor, texto digitado ou parâmetro, texto de persona, nota, conclusão da IA.
Quem quer o detalhe abre o `href`, com a autenticação do painel.

**Quem publica**: o serviço do Livro (`modules/learning/application/servico.py`), por uma porta de eventos do próprio módulo (o mesmo
`EventSink` que `session_rules.py` recebe), em três pontos: (1) a transição ou o nascimento que deixa o item à espera do dono
(`validated` com `requires_owner`; candidata com texto de pessoa), inclusive quando vem do ouvinte das receitas e fluxos
(`application/nativos.py`, que já recebe `MudancaDaReceita`); (2) a gravação de um parecer de faixa B (`motivo = parecer_da_ia`); (3) a saída
da espera, por qualquer transição. Idempotente por (`kind:ref`, `aguardando`): sem mudança, não publica de novo.

**Consumidores, sem acoplamento direto** (assinam o evento, o Livro não os conhece): o aviso fora do painel do 28.11 (Telegram, frente Jev, que
escuta `approval.pending`, `run.updated` em `needs_input`, `session.needs_person`, `pedido.aviso` e, desde o 28.14, este evento: só a entrada na
faixa C por padrão, chave `learning:{kind}:{ref}:{desde}`, nenhum identificador do item na mensagem) e a caixa de Pendências (ADR-062).
Item 30.21. A linha entra na tabela de eventos do `api-contract.md` no adendo da implementação.

Em `shadow`, o aviso com `motivo = parecer_da_ia` leva a pessoa ao item, mas o detalhe não mostra o parecer antes da decisão dela
(30.17): diz só que há um. O aviso não carrega a conclusão (regra acima), então não contamina o rótulo às cegas.

---

## 9. Falhas → diagnóstico → proposta → prova; e obsolescência

### 9.1 Do grupo de falha à proposta

Hoje: o grupo (app × capability × tipo × tela) tem prova da correção medida (`fixed`/`reopened`), e as propostas são de três tipos
(`acao_de_catalogo`, `promover_licao`, `promover_tela`). Evolução:

1. **Ligar o grupo ao conhecimento usado** (determinístico): nas etapas do grupo, o que conduziu (`steps.driven_by`), a receita da chave
   (`template_hash` + app + versão), o fluxo ou a habilidade da execução (`runs.skill_id`, `flow`), as lições expostas. Sai uma lista
   `conhecimento_envolvido`.
2. **Hipóteses determinísticas antes da IA**, com o vocabulário do briefing §9 como enum fechado `CausaProvavel`: `ambiente` (camada
   `aparelho`/`infra` do `FailureKind`), `sessao_ou_conta` (autenticação, conta errada), `ia` (orçamento, saldo, indisponível),
   `verificador` (`TipoDeVerificacao`), `receita` (falha com `driven_by` em receita, mesma etapa ok em outro aparelho = aparelho; em todos =
   receita, regra que já existe em `taskqueue/aproveitamento.py`), `versao` (falhas começaram com versão nova observada), `tela_alterada`
   (`failure_screen` desconhecida ou tela que deixou de casar), `falta_conhecimento` (etapa livre sem receita nem catálogo), `codigo`, `temporario`, `indeterminado`.
3. **IA só quando a hipótese é `indeterminado` ou há mais de uma**: dossiê do grupo (§8.2 adaptado) → saída com `causa_provavel`,
   `proposta` (enum: `corrigir_receita`, `nova_versao_da_receita`, `rebaixar`, `nova_candidata`, `corrigir_fluxo`, `coletar_tela`,
   `atualizar_catalogo`, `item_tecnico`, `nova_execucao`, `aguardar`), `evidencia_necessaria`, `como_validar`.
4. A proposta vira linha `learning_backlog` de categoria `proposta` (tipos novos em `TipoDeProposta`, fechados), com `parent_id` = o grupo
   de falha e a revisão em `learning_reviews`. A IA **não** altera conhecimento nem código: `rebaixar` da proposta é recomendação; só rebaixam sozinhos o gatilho determinístico novo (`catalogo_sem_efeito`, §9.2) e os de hoje; o resto é do dono.
   é pessoa ou sessão de desenvolvimento.
5. **Prova** = a régua que já existe (`prova_minimo`, `prova_fator`, reincidência). `fixed` continua medido, nunca declarado.

O app do grupo já sai por etapa (`app_da_etapa`, `taskqueue/projecao.py:72`). As lacunas que restam estão no §12.

### 9.2 Obsolescência (sinais existentes → rótulo `obsoleto_provavel`, nunca transição direta)

| Sinal | Fonte | Tipos |
|---|---|---|
| sem uso no próprio escopo há X dias **enquanto a etapa continua sendo executada por outro caminho** | `last_used_at` × `steps` com o mesmo `template_hash`/app conduzidas por `ai` | receita, lição |
| versão viva nova sem reprodução; versão da receita fora do parque | §7 | receita, tela |
| substituta ativa | §6 | receita, item com `parent_id` |
| habilidade publicada com a mesma `match_key` (o fluxo nunca casado é `sem_evidencia`, como a receita nunca usada: mesmo fato, mesmo rótulo; adendo v0.63) | `run_planning.py` | fluxo |
| quedas de eficácia, contra recente, intervenção recorrente | §5.2 | todos |
| duplicado | mesmo `scope_key` e `content_hash` diferente (contradiz) ou mesmo conteúdo em chaves vizinhas | lição, tela |
| absorvido pelo repositório | `absorvida:` | tela |
| neutra na medida / 60 dias sem exposição (já existe) | `licoes.py` | lição |
| **efeito sem respaldo no catálogo**: receita ou fluxo com `commit` num app cujo catálogo ATUAL não tem ação com efeito para a etapa (caso real: receita 100 do Outlook) | `receita_tem_efeito`/`fluxo_tem_efeito` × `catalogo.yaml` do pacote × capability derivada (§4) | receita, fluxo |

O rótulo é de leitura. A transição continua com os gatilhos de hoje (quarentena, neutra, 30 dias sem casar). Há um gatilho novo,
**determinístico**: o "efeito sem respaldo no catálogo" rebaixa (`candidate`/`validated` → `disabled`, `published` → `deprecated`) pelo
sistema, com motivo estruturado `catalogo_sem_efeito:<capability|*>` na trilha. Rebaixar já é automático no `ciclo.py`, e a porta de política
já recusaria o efeito na execução. Se o catálogo ganhar a ação, reativar é de pessoa. Fora disso, desligar o que é de classe B ou C é do dono;
a IA só opina.

### 9.3 Evidência inválida: o sucesso falso que ensinou (30.23, decisão da coordenação, 03/10)

Uma execução que termina como sucesso sem comprovar o que fez ensina errado: o que se aprende dela nasce de evidência
inválida. Caso real: a receita 109 e o fluxo do Outlook "abrir a caixa de entrada e ler", da `r-20261002204347-8c3f6e`. Desligar por pessoa
vetaria para sempre um conteúdo cuja navegação provavelmente é boa (o falso foi a leitura). A regra:

- **Tipo próprio de desligamento.** A pessoa marca, pela ação própria do item, a execução de origem dele. A trilha recebe o
  motivo estruturado `evidencia_invalida:<run>`, e o motivo livre nesse formato é recusado. O vivo é desligado; o já desligado
  ganha a linha que reclassifica o motivo. O domínio lê o tipo, e o painel nunca interpreta texto.
- **O veto barra só a mesma execução**, sem prazo. Outra execução real que ensine o mesmo no mesmo escopo faz o item
  (re)nascer como candidato. Valem as portas de aprendizado de hoje: a receita vem de etapa comprovada, e o fluxo, de execução
  `completed` com prova.
- **Reaprendido.** É derivado da trilha do escopo e nunca gravado: o último nascimento do item vem depois da última marca do
  escopo, sem publicação de pessoa no meio. Força a classe B (§8.4) e "Para aprovar". O sistema para em `validated` na
  sombra da receita e na do fluxo, e o repositório recusa a publicação pelo sistema. Depois que uma pessoa publica no
  escopo, o que nascer ali já segue o D1 de sempre.
- **A evidência da execução marcada fica à vista e não mede**: sai da sombra do fluxo, da saúde (§5), da versão (§7) e
  do dossiê do curador (§8.2), cujos fatos de risco carregam o reaprendido.
- **Relações** (§6): `reaprende` e `reaprendida_por` entre a receita nova e a desligada. O fluxo renasce na mesma linha
  (`match_key` único) e não aponta para si.
- **Limites.** A receita renasce como versão nova, e a desligada vira `superseded` (o veto sobrevive à arrumação). O escopo
  da receita inclui a versão do app, como o veto de sempre.

Contrato: adendo v0.70 do [`api-contract.md`](../api-contract.md).

---

## 10. Observabilidade

Endpoint `GET /api/aprendizado/metricas?app=&dias=`, calculado no backend a partir das tabelas, sem contador novo em memória
(exceto o que já existe em `metricas.py`).

| Métrica | Cálculo | Fonte |
|---|---|---|
| itens por app × tipo × estado × origem; candidatos, publicados, validados, deprecated, disabled, pendentes | contagem da composição (§3) | Livro |
| aprovações automáticas × humanas | `learning_transitions.decided_by` (`sistema` × sessão) por `to_state` | trilha |
| decisões da IA, overrides, aplicadas, inválidas, custo | `learning_reviews` | novo |
| refutado depois de promovido | item `published` que depois foi a `disabled` por contradição/voto, ou evidência `against` após `state_at` | trilha + evidência |
| taxa de sucesso após promoção | eficácia (§5.2) na janela pós-`state_at` | receita, fluxo, lição |
| churn | transições por item na janela; itens criados e desligados na janela | trilha |
| tempo candidate→validated e validated→published (mediana, p90) | pares de transições do mesmo `item_ref` | trilha (receitas antigas sem trilha ficam de fora, e o `n` sai junto) |
| nunca usados; obsoletos prováveis | rótulos do §5.3 | saúde |
| economia por reutilização | chamadas de IA evitadas, já medidas | `taskqueue/aproveitamento.py` (reaproveitar, não recalcular) |
| falhas evitadas | **não é mensurável hoje** (não há contrafactual por etapa); proxy: taxa de falha com receita × com IA na mesma etapa, rotulado como proxy | `steps` |
| custo da curadoria | soma `learning_reviews.usd` na janela × `B_W` (§8.7); aviso a 80% | novo |

---

## 11. UX: arquitetura da informação e contratos

### 11.1 Navegação (nos dois sentidos)

```
Global ── App ── Capability/processo ── Item de conhecimento ── Evidência / execução
  ▲          ▲            ▲                       │                     │
  └──────────┴────────────┴───────── links de volta (breadcrumb) ◄──────┘
```

- **Global** (substitui a aba padrão): cartões por app (`/apps`): declarado sim/não, contagens por origem e estado, rótulos de saúde,
  "precisa de atenção" com o motivo, modos do app (o que é medido e não usado). Abaixo, a fila **Atenção**, transversal.
- **App**: três blocos: *O que o sistema sabe por declaração* (telas, sessão, capabilities com risco, efeito e `saidas`; loja), *O que aprendeu*
  agrupado por capability (`*` = etapa livre), e *O que falha* (grupos do backlog do app, com proposta e prova). Cada bloco mostra a camada de uso.
- **Capability**: receitas, fluxos e habilidades que a usam, lições do escopo, falhas e propostas.
- **Item** (drawer ou página, §11.2).
- **Evidência**: link para a execução, a etapa e o aparelho; do lado da execução, o bloco "Aprendizado desta execução" (já existe) linka de volta.

"Para aprovar" vira o filtro **Atenção**, com motivos estruturados: `espera_o_dono` (D1), `parecer_da_ia` (sugestão pendente),
`degradando`, `obsoleto_provavel`, `conflito`, `falha_recorrente`. "Sinais" passa a ser evidência do item e uma aba técnica secundária.
"O que mais falha" vira o bloco do app mais a lista global de grupos. Memória fica na Persona, não no eixo de app.

### 11.2 Detalhe do item (seções; aparecem só as aplicáveis)

| Seção | Conteúdo | Contrato |
|---|---|---|
| Identidade | tipo, app, capability, versão, estado, origem, camada de uso, classe de risco | `item` + `camada_de_uso` + `classe_de_risco` |
| Conteúdo | receita/fluxo/habilidade legível (§4), tela (ids e nome), lição (o texto exato do prompt) | `conteudo` |
| Evidência | a favor/contra/conflito com execução, aparelho, versão, data, simulado; reproduções e sombra | `evidencias[]` (já vem; passa a ser exibida) |
| Saúde | dimensões + rótulo + motivos | `saude` |
| Versão | quadro do §7 | `versao` |
| Histórico | trilha com quem e por quê | `trilha[]` (já vem) |
| Lineage | relações do §6 | `relacoes[]` |
| IA | última revisão: decisão, confiança, conclusão, evidências citadas (links), riscos, faltas, modelo, data; histórico de revisões (30.17; o custo espera o `usd` medido na rubrica) | `pareceres[]` + `curador` |
| Ações | as permitidas pelo backend (§5.4), aceitar/recusar parecer com motivo, pedir revisão | `acoes[]`; o parecer traz `acao` e `recusa` |

Progressive disclosure: a lista mostra 1 linha por item (título legível, app › capability, rótulo de saúde, camada de uso, selo de efeito e
"por que precisa de você"). O resto só abre no detalhe. Paginação no backend (a lista hoje não tem limite).

### 11.3 Contratos HTTP (adendo ao [`api-contract.md`](../api-contract.md) quando implementados)

- `GET /api/aprendizado/apps` → `[{pacote, nome, existencia: declarado|loja|so_aprendido, declarado: {catalogo: {acoes, com_efeito}, telas, sessao, loja}, contagem: {kind: {estado: n}}, origem: {declarado, aprendido, absorvido}, saude: {rotulo: n}, atencao: n, modos}]`
- `GET /api/aprendizado/apps/{pacote}` → declarado (nomes de telas, capabilities com `risk`, `side_effect.external`, `saidas`), grupos por capability, falhas do app.
- `GET /api/aprendizado?app=&capability=&kind=&state=&origem=&saude=&atencao=&limite=&cursor=` → como hoje, mais `saude`, `camada_de_uso`, `acoes`; `app_nao_resolvido` explícito.
- `GET /api/aprendizado/{kind}/{ref}` → hoje + `conteudo`, `saude`, `versao`, `relacoes`, `revisoes`, `acoes`.
- `POST /api/aprendizado/{kind}/{ref}/status` → igual, com `review_id` opcional (registra aceite ou override). Feito no 30.17.
- `POST /api/aprendizado/{kind}/{ref}/parecer/{review_id}` → aceitar (o passo do lado sugerido) ou recusar com motivo. Feito no 30.17.
- `POST /api/aprendizado/{kind}/{ref}/revisao` → pede revisão (gatilho `pedido_da_pessoa`; respeita orçamento e modo). Feito no 30.17.
- `GET /api/aprendizado/revisoes?app=&decisao=&desde=` e `GET /api/aprendizado/metricas?app=&dias=`. Feito no 30.8 (adendo
  v0.89).

---

## 12. Instrumentação: o que já existe e as três lacunas

A atribuição de app por etapa **já existe** no aprendizado. `steps.app_id` NULL é desenho: a etapa herda o app do plano
(`planning/parsing.py:204-207`). `app_da_etapa` (`taskqueue/projecao.py:72`) resolve o app em `learning_daily`, falhas, backlog, lições e
feedback, e `learning_daily` não funde run misto (correção medida pelo orquestrador; o diagnóstico já foi ajustado). **Não é pré-requisito.**
Restam três lacunas, que não bloqueiam a Fase 30:
1. o fluxo multi-app é registrado no app do plano (o único fluxo com Outlook é um candidato "do Instagram"). A visão por app mostra o fluxo
   em todos os apps de `flow_required_apps`, com o app principal marcado (30.1);
2. `failure_screen` vazio no backlog antigo (anterior a 29/09, ou app sem `telas.yaml`). Fica assim; o dossiê diz "tela não registrada";
3. a tela Apps (`apps_overview.py`) parece usar o 1º app do run. **A conferir**; se confirmado, vira correção pequena ligada à 30.1.

O que tocar `taskqueue/repository.py`, `service.py`, `travas.py` ou `state.py` é da frente Jev/Pedidos e entra como dependência combinada, não como
item da Fase 30.

---

## 13. Plano de implementação: Fase 30

Arquivos relativos a `backend/app/` (back) e `frontend/src/` (front). "Dono" = frente que executa. Nenhum item edita `taskqueue/repository.py`,
`service.py`, `travas.py`, `state.py` nem os arquivos do 12.3. `taskqueue/recipes.py`/`flows.py`/`aproveitamento.py` só são **lidos** (consulta SQL
a partir de `modules/learning/infrastructure/`). Testes de backend no harness (`base_console_port: 5640`).

| ID | Título | Tam. | Arquivos | Dono | Depende de | Validação |
|---|---|---|---|---|---|---|
| 30.1 | Visão por app no backend: lista e detalhe de app como composição (registro ∪ loja ∪ Livro), origem e camada de uso | M | `modules/learning/application/apps.py` (novo), `domain/camada.py` (novo), `infrastructure/fontes.py`, `presentation/apps.py` (novo), `presentation/router.py`, testes | Aprendizado | — | `simulated` (`tests/test_learning_apps.py`, registro falso com app declarado, app só de loja e app só aprendido); `real` = leitura no central após deploy, Outlook com zeros e declarado |
| 30.2 | Chave de app canônica: fluxo e habilidade sem `apps.package` resolvidos; balde "app não resolvido"; memória fora do eixo de app | P | `infrastructure/fontes.py`, `application/servico.py`, testes | Aprendizado | — | `simulated` |
| 30.3 | Conteúdo legível no detalhe: receita (ações, efeito, capability derivada, etapa e execução de origem, substituta), fluxo e habilidade | M | `domain/conteudo.py` (novo), `infrastructure/fontes.py`, `application/servico.py` (`DetalheDoLivro`), `presentation/livro.py`, testes | Aprendizado | 30.2 | `simulated` (receita com `commit`, com parâmetro, com `training:`; nenhum valor de parâmetro na saída) |
| 30.4 | Saúde: `domain/saude.py` puro (dimensões, rótulo, motivos), limiares no config, exposta na lista e no detalhe | M | `domain/saude.py` (novo), `application/servico.py`, `presentation/livro.py`, `config.py` (só `LearningCfg`), `config/config.example.yaml`, testes | Aprendizado | 30.3 | `simulated` (tabela de casos por rótulo; dimensão sem dado = desconhecida) |
| 30.5 | Ações permitidas calculadas no backend; o front apaga o espelho de `ciclo.py` | P | `domain/livro.py`, `presentation/livro.py`, `features/aprendizado/model.ts`, `ItemDoLivro.tsx`, testes back e front | Aprendizado | — | `simulated` (teste percorre `TRANSICOES` × D1 e compara com `acoes`) |
| 30.6 | Estado de versão por item (§7) a partir de `device_app_state` e das chaves de receita | M | `domain/versao.py` (novo), `infrastructure/fontes.py`, testes | Aprendizado | 30.3 | `simulated` (duas versões vivas, receita só na antiga = `nao_testado`) |
| 30.7 | Relações derivadas (§6) no detalhe | P | `domain/relacoes.py` (novo), `application/servico.py`, testes | Aprendizado | 30.3 | `simulated` |
| 30.8 | Métricas do aprendizado (`/metricas`), reaproveitando `aproveitamento.py` e a trilha | M | `application/metricas.py` (novo), `infrastructure/relatorio_sql.py`, `presentation/` (rota), testes | Aprendizado | 30.1, 30.4 | `simulated`; `real` = leitura no central |
| 30.9 | Migração `learning_reviews` (**069, provisória**; confirmar no commit e renumerar se outra entrar antes) e `docs/banco.md` | P | `migrations/0NN_revisoes_do_aprendizado.sql`, `docs/banco.md` | Aprendizado | número do orquestrador | `simulated` (SQLite na suíte); PostgreSQL: P17 (29.14) |
| 30.10 | Curador, domínio: dossiê, contrato de saída e validação de citações, política de risco por classe (§8.2-8.4) | M | `domain/curador.py`, `domain/politica_de_risco.py` (novos), testes | Aprendizado | 30.4, 30.6, 30.7 | `simulated` (citação inventada = inválida; classe C sem chamada à IA; vale a classe mais restritiva entre catálogo e `commit`) |
| 30.11 | Curador, aplicação: porta `CuradorDeIA`, adaptador simulado, gravação, laço sob trava de líder, gatilhos e filtros, orçamento proporcional (§8.7), modos `off/shadow/on`, `aprendizado.curador` no config | G | `application/ports.py`, `application/curador.py`, `infrastructure/curador_sql.py`, `infrastructure/montagem.py`, `config.py` (só `LearningCfg`), testes | Aprendizado | 30.9, 30.10; ADR-064 (na main) | `simulated` (provedor falso; `G_W = 0` não revisa; prioridade quando o orçamento acaba; salvaguarda de disparo; `dossie_hash` repetido não revisa; o parecer nunca transiciona) |
| 30.12 | Adaptador do curador para o hub de IA (papel ou chamada genérica), custo em `learning_reviews` | M | `planning/` e `config.py` (`ai.roles`): **combinar com a Jev** (17.x) | Aprendizado + Jev | 30.11; parâmetros do orçamento (D-1) | `simulated`; `real` = uma revisão pontual no central com custo registrado (autorização (b) de 02/10) |
| 30.13 | Falhas → diagnóstico: conhecimento envolvido por grupo, `CausaProvavel` determinística, proposta estruturada (tipos novos de `TipoDeProposta`) e revisão da IA só quando indeterminado | M | `domain/backlog.py`, `domain/vocabulario.py`, `application/falhas.py`, `infrastructure/relatorio_sql.py`, testes | Aprendizado | 30.11 | `simulated` |
| 30.14 | Obsolescência: rótulo `obsoleto_provavel` com os sinais do §9.2; gatilho do curador; rebaixamento determinístico `catalogo_sem_efeito` (receita 100 do Outlook) | M | `domain/saude.py`, `application/curador.py`, testes | Aprendizado | 30.4, 30.6, 30.11 | `simulated` |
| 30.15 | Painel: Global → App → Capability → Item, filtro de app, fila Atenção, memória fora do eixo de app | G | `features/aprendizado/*` (página e abas novas), `api.ts`, testes vitest | Aprendizado | 30.1, 30.2, 30.4 | `simulated` (vitest); `real` = inspeção no central após deploy |
| 30.16 | Painel: detalhe rico (seções do §11.2) | M | `features/aprendizado/ItemDoLivro.tsx` (ou drawer novo), `model.ts`, testes | Aprendizado | 30.3-30.7 | `simulated` (vitest) |
| 30.17 | Painel: parecer da IA na fila e no detalhe; aceitar ou recusar com motivo (override); pedir revisão | M | `domain/parecer.py`, `application/pareceres.py` (novos), `infrastructure/revisoes_sql.py`, `presentation/livro.py`, `features/aprendizado/*` (`ParecerDaIA.tsx`, `parecer.ts`), testes | Aprendizado | 30.11, 30.16 | `simulated` |
| 30.18 | Prova da Fase H: Instagram, QAMessenger e Outlook (só leitura, android-01 primeiro) na visão nova, curador em `shadow` com orçamento proporcional | M | `docs/relatorio-validacao.md` | Aprendizado (+ Android para a execução do Outlook) | deploy de 30.1-30.17; D-1, D-3 | `real` (data, máquina, commit, ids); sem efeito em conta de terceiros |
| 30.19 | Docs: ADR-067 em `decisoes.md`, `docs/dominios/aprendizado.md`, adendo do contrato, CHANGELOG, estado pelo mecanismo | P | `docs/*` | Aprendizado | depois do PR do índice de ADRs | `python scripts/docs-check.py` |
| 30.20 | Modo por app para lições e telas (§8.10): `por_app` com padrão = global, `modo_efetivo(tipo, pacote)` na coleta, no D1, nos fornecedores e na camada de uso | M | `config.py` (só `LearningCfg`), `config/config.example.yaml`, `application/licoes.py`, `application/telas.py`, `infrastructure/ligar_telas.py`, `ligar_licoes.py`, `domain/camada.py`, testes | Aprendizado | — (ligar de fato: lições no QAMessenger e telas no Outlook, depois da leitura real no android-01) | `simulated` (dois pacotes, modos diferentes; sem override = global); ligar no central = `real` |
| 30.21 | Evento `learning.needs_person` (§8.11): porta de eventos do Livro, publicação na entrada e na saída da espera (faixas B e C), payload sem conteúdo, idempotente; linha na tabela de eventos | P | `application/ports.py`, `application/servico.py`, `application/nativos.py`, `infrastructure/montagem.py`, `docs/api-contract.md`, testes | Aprendizado | — (consumidores: 28.11 da Jev e Pendências, ADR-062, assinam depois) | `simulated` (barramento falso: entra, sai, não repete; o payload não tem campo de conteúdo) |
| 30.22 | Backfill único e idempotente SÓ dos mineradores de lição, nas 12 execuções reais com contraste aprovável anteriores à 055 (lista explícita tirada do dry-run), sem IA; confere a migração do banco antes de gravar; backup antes; reexecutar não duplica | P | `scripts/aprendizado-backfill-licoes.py` (novo), `modules/learning/**`, testes | Aprendizado | — | `simulated` (rodar 2× = mesmo resultado); `real` = contagem de candidatas antes e depois no central |

| 30.23 | Evidência inválida (§9.3): tipo próprio de desligamento com motivo estruturado, veto só da mesma execução, renascimento reaprendido em classe B, transição corretiva da 109 e do fluxo; painel (trilha, evidência, ação, Para aprovar) | M | `domain/evidencia_invalida.py` (novo), `domain/ciclo.py`, `domain/livro.py`, `domain/politica_de_risco.py`, `domain/relacoes.py`, `application/servico.py`, `application/nativos.py`, `infrastructure/`, `presentation/livro.py`, **`taskqueue/recipes.py`** (gancho da sombra: exceção ao "só leitura", aceita pela coordenação), `features/aprendizado/*`, testes | Aprendizado | — | `simulated`; a marca da 109 e do fluxo no central depois do deploy (suíte 6) |

Ordem sugerida: 30.20 e 30.21 são independentes e podem ir cedo (a decisão do dono já existe). 30.1, 30.2 e 30.5 em paralelo (não compartilham arquivo, exceto `fontes.py` entre 30.1 e 30.2: em sequência). Depois 30.3 → (30.4, 30.6,
30.7) → 30.10. Migração (30.9) cedo, porque depende do número. Front (30.15, 30.16) atrás dos contratos. 30.11 → 30.12/30.13/30.14 → 30.17 → 30.18.
Os IDs entram num bloco de `.claude/plano-100.json` e no `docs/plano-100.md` pelo mecanismo, fora deste PR.

---

## 14. ADR-067 (proposto): aprendizado vivo com eixo de app, saúde derivada e curador por IA auditável

> **Aceito em 03/10/2026** (30.19): o texto vigente, com o que foi implementado e o que ficou pendente, está em
> [`decisoes.md`](../decisoes.md#adr-067--aprendizado-vivo-eixo-de-app-saúde-derivada-e-curador-por-ia-auditável). Esta seção fica como a proposta de 02/10.

**Estado:** proposto (Fase B da frente Aprendizado, 02/10). Revê **em parte** a decisão 9 do ADR-054; mantém D1–D8 e a tabela de `ciclo.py`.
Completa o ADR-052 (app como dado) no lado da leitura.

**Contexto.** O diagnóstico mostrou que o aprendizado é um catálogo sem eixo de app, que o declarado não aparece, que o absorvido some, que a
receita não é legível onde se decide sobre ela, que versão, lineage e saúde não existem como leitura, e que toda decisão sem regra cai no dono
(1 pendente + 39 a revisar). O Outlook "some" por seis causas somadas, nenhuma delas um filtro.

**Decisão.**
1. **Visão por app como composição de leitura.** Lista de apps = registro ∪ loja ∪ pacotes do Livro. Origem explícita (`declarado`, `aprendido`,
   `absorvido`) e camada de uso em runtime. Sem tabela de conhecimento nova, sem cópia de YAML, receita ou fluxo.
2. **Saúde derivada, não estado.** Dimensões medidas e um rótulo por regra com motivos, calculados por uma função só no backend. Nenhuma nota numérica.
3. **Versão e lineage derivados.** Estado de versão a partir das versões vivas no parque e das chaves de receita; relações a partir de `parent_id`,
   chave + `version`, `absorvida:`, evidência e `content_hash`. Sem tabela de arestas.
4. **Ações permitidas calculadas no backend.** O front não espelha `ciclo.py`.
5. **Curador por IA como intérprete.** Dossiê determinístico → parecer estruturado com citações validadas → decisão pela política de risco,
   com a decisão final no `ciclo.py`. A IA nunca transiciona. Modos `off` (fábrica) / `shadow` / `on`; laço sob a trava de líder; porta própria
   (independente de `context_retrieval`).
   - **Política de risco (decisão do dono, 02/10)**, valendo a mais restritiva entre catálogo e `commit`: (a) navegação e leitura publica pela
     regra determinística atual, e a IA só opina (registro) se sobrar orçamento; (b) efeito médio ou `commit` em app sem catálogo: a IA recomenda e o dono aprova em lote;
     (c) alto risco, `manual_only`, sessão, autenticação, envio, publicação ou exclusão: sempre o dono, item a item.
     *Nota de 03/10:* a orquestradora emendou a política (PR #127), passando o `commit` em app sem catálogo de (b) para (c). O dono
     confirmou (b) na mesma madrugada, e a emenda foi revertida.
   - **Orçamento proporcional (decisão do dono, 02/10)**: `B_W = min(α·G_W, k·N_W·c̄)`, α = 10%, k = 1,5, W = 7 dias, `c_max = 4 × mediana(c_rev)`;
     prioridades conflito > (c) > falha recorrente > (b), e (a) nunca; salvaguardas relativas (§8.7). Em 02/10: B = US$ 0,85 por 7 dias.
   - **Curador (decisões do dono, 02/10)**: modelo barato por padrão (Haiku na triagem), escalada a Opus só em faixa C ou conflito (D-1);
     origem humana sem efeito = faixa B (D-2); `shadow` → `on` só com ≥ 30 revisões válidas e ≥ 90 % de acordo (D-3); faixa C sempre item a item.
   - **Saúde (D-5, decisão do dono, 02/10)**: rótulo só de leitura com 2 falhas seguidas, eficácia 0,8 com 5 usos, 14 dias sem uso e contra
     em 7 dias (§5.3); nada é desligado pela saúde e não há aviso fora do painel.
6. **Trilha própria `learning_reviews`**, nunca purgada, com modelo, template, custo, decisão, override e resultado posterior.
7. **Falha → proposta** pelo backlog existente: conhecimento envolvido e causa provável determinísticos; IA só no indeterminado; prova da correção medida como hoje.
8. **Modo por app** para lições e telas (override por pacote, padrão = global).
9. **Rebaixamento determinístico** de receita ou fluxo com efeito num app cujo catálogo atual não tem ação de efeito para a etapa.
10. **Evento `learning.needs_person`** na entrada e na saída da espera humana (faixas B e C), sem conteúdo, para os consumidores de aviso e Pendências.
11. **Backfill único** só das lições, nas 12 execuções reais com contraste aprovável anteriores à 055 (decisão do orquestrador, sem IA).

**Alternativas recusadas.** Copiar o declarado para `learning_items` (segunda verdade). Nota de saúde 0–100 (sem origem explicável).
Grafo de conhecimento (custo sem uso medido). IA aprovando direto (perde o D1 e a auditoria). Reaproveitar `ia_resumos_por_dia` ou `PassoDeCuradoria`
(ambos prometem "sem IA"). Acoplar ao `context_retrieval`/Jev.

**Consequências.** Uma migração (`learning_reviews`). Blocos de config `aprendizado.curador` (com `orcamento`), `aprendizado.saude` e
`por_app` em lições e telas. O gasto pago é proporcional ao uso e para quando a operação para.
O painel de Aprendizado é refeito sobre contratos novos. A atribuição de app por etapa já existe (`app_da_etapa`) e não é pré-requisito.

---

## 15. Decisões do dono

Já decididas (02/10), incorporadas acima: política de risco A/B/C, com a mais restritiva entre catálogo e `commit` (§8.4); orçamento
proporcional com α = 10%, k = 1,5, W = 7 dias, `c_max = 4 × mediana`, prioridades e salvaguardas (§8.7); backfill das lições nas 12 execuções aprováveis (30.22, decisão do orquestrador com o dono informado); lições e telas
por app (§8.10): lições ainda não ligam e, depois, primeiro no QAMessenger; telas `on` primeiro no Outlook, depois da leitura real no android-01.

Decididas pelo dono em 02/10 (via orquestrador), depois do desenho:

| # | Decisão do dono |
|---|---|
| D-1 | Curador com modelo **barato por padrão** (Haiku na triagem); escalada para Opus **só em alto risco (faixa C) ou conflito**, sempre dentro de `c_max` |
| D-2 | Conhecimento de origem humana sem efeito (lição de nota, preferência) entra na **faixa B**: a IA recomenda e o dono aprova em lote; nunca publica sozinho |
| D-3 | Curador de `shadow` para `on` só com **≥ 30 revisões válidas em sombra e ≥ 90 % de acordo** com as decisões do dono; a faixa C continua sempre com o dono |
| Faixa C | Confirmada: a IA dá parecer (prioridade 2 do orçamento), mas a decisão é **sempre do dono, item a item, nunca em lote** |
| D-5 | Limiares da saúde **aprovados como propostos** (§5.3): 2 falhas seguidas, eficácia 0,8 com 5 usos, 14 dias, contra em 7 dias; rótulo só de leitura, no painel, sem Telegram |
| 30.9 | Migração `learning_reviews` = **069, provisória**: confirmar com o orquestrador no commit; quem mergear depois renumera para ficar acima de todas da `main` |

Decididas pelo dono em 03/10 (via orquestradora):

| # | Decisão do dono |
|---|---|
| B confirmada | `commit` em app sem catálogo fica na **classe B** (a emenda para C da madrugada foi revertida; §8.4 e §14) |
| Fluxos pela IA | O dono **não valida fluxo à mão**: o curador valida (A publica pela regra, B no lote do dono, C item a item); nota no §8.4 |

Em aberto:

| # | Decisão | Recomendação |
|---|---|---|
