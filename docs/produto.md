# Produto

> Fonte principal para "o que este sistema é e o que ele promete". Para arquitetura interna, ver
> [docs/arquitetura.md](arquitetura.md); para domínios, [docs/dominios/](dominios/); para o estado de execução do
> plano de trabalho, [docs/estado-atual.md](estado-atual.md) e [docs/roadmap.md](roadmap.md).

## 1. Objetivo

Um painel controla um **parque de emuladores Android** (locais e em outras máquinas, via *worker*) e executa
tarefas em aplicativos a partir de um **objetivo em português**. A IA lê a tela, decide uma ação por vez, age pela
interface (Appium/UiAutomator2) e **comprova** o resultado — nunca aceita a resposta do driver como prova, só a
pós-condição observada na tela seguinte.

Quatro compromissos guiam o design, e aparecem espalhados pelo código com o mesmo nome:

1. **Ver os aparelhos e mandar objetivo em português.** Um comando descreve o que fazer, não como; a IA monta o
   plano, decide etapa por etapa e relata **por instância**.
2. **Falha ou incerteza nunca conta como sucesso.** Um comando que termina sem se saber o efeito vira `uncertain`
   — nunca é reenviado sozinho; alguém confirma, repete ou abandona (`backend/app/commands/`,
   `backend/app/taskqueue/executor.py`). Um resultado do driver (Appium) nunca fecha uma etapa: só a pós-condição
   observada na tela fecha.
3. **Custo sob controle.** Receitas (aprender uma vez, repetir por seletor), fluxos (plano congelado reaproveitado
   sem novo planejamento), modelo por função e rodízio de instâncias existem para que IA e RAM não cresçam
   linearmente com o número de contas. Ver [docs/ia.md](ia.md) §7.
4. **Operar contas com persona e aprovação.** Uma conta de Instagram tem persona (voz, limites, memória) e
   política por ação (sozinho / com aprovação / só manual) — nunca ação livre sem esses dois.

## 2. Conceitos

| Termo | O que é |
|---|---|
| **Aparelho / instância** | Um emulador Android com AVD próprio (`android-NN`), userdata, apps e sessões preservados entre reinícios. |
| **Servidor / worker** | Uma máquina que hospeda aparelhos. O central é um worker de si mesmo (`LocalWorker`); um worker remoto se inscreve pelo mesmo contrato e recebe trabalho por um canal WebSocket dedicado, atrás de túnel SSH. |
| **Comando** | Um verbo do ciclo de vida do aparelho (`start`, `stop`, `hibernate`, `wake`, `restart`, `reset`, `open_app`…) endereçado a uma instância. Responde `202` com `command_id` e caminha por estados até `succeeded`/`failed`/`rejected`/`cancelled`/`uncertain`. |
| **Execução / objetivo / etapa** | Uma *execução* (`run`) atende um comando em linguagem natural para um conjunto de instâncias; cada instância ganha um *objetivo* (`objective`); um objetivo se decompõe em *etapas* (`step`) com dependência e pós-condição observável. |
| **Receita** | Uma etapa aprendida uma vez pela IA vira uma sequência de seletores repetível sem nova chamada de modelo (`ai.recipes: replay`). |
| **Fluxo** | Uma execução 100% comprovada vira um plano congelado; o mesmo comando com outros parâmetros reaproveita o plano sem chamar o planejador (`ai.flows: true`). |
| **Perfil / persona** | Um perfil (`instagram_profiles`) é a identidade que opera um app: conta, voz (persona), memória, política por ação e limites por hora. |
| **Política / aprovação** | Cada ação do catálogo tem uma política: sozinho, com aprovação (fica em `pending_approvals` até alguém decidir) ou só manual. |
| **Release / canário** | Uma versão de APK importada nasce `validated`; só vira instalável em massa (`promoted`) depois de provar um canário (instalar, abrir, continuar de pé) num aparelho só. |
| **Loja** | Um emulador extra com imagem Play Store, logado na conta Google do dono. É a única fonte de APK do Instagram: o backend copia o conjunto (base + splits) desse aparelho por `adb` e distribui ao parque. Nunca recebe tarefa nem entra no rodízio. |

## 3. Fluxos do usuário

- **Primeiro comando.** Selecionar instâncias, escrever o objetivo, *Planejar* (inspeciona sem agir) ou
  *Executar*. Se faltar dado essencial, a execução fica `needs_input` com as perguntas — a IA nunca inventa.
- **Controle manual.** Pedir o controle faz a IA ceder no próximo ponto seguro; toques são mapeados para o frame
  exibido; devolver o controle faz a IA reobservar a tela antes de continuar.
- **Distribuir app.** Uma release promovida é entregue por rodízio: quem está ligado instala já, o resto recebe
  antes da próxima tarefa daquele app (ou `-Agora`, que liga o parque inteiro para instalar).
- **Cadastrar perfil + senha pelo portal.** Aba Perfis → Novo perfil: usuário, senha, aparelho. A senha vai direto
  ao cofre cifrado (DPAPI no Windows) e nunca volta — nem em resposta, nem em log, nem em evidência, nem em
  prompt.
- **Aprovar ação.** Uma ação de política "com aprovação" fica pendente até alguém decidir (aprovar/rejeitar) pela
  aba Aprovações do perfil.
- **Treinar habilidade.** Assumir o controle no Foco e realizar a tarefa; cada entrada é gravada com o elemento
  tocado; a IA generaliza a gravação em comando + etapas + receitas, com escopo por perfis/grupos (item 13.1–13.3
  do plano — ver §5).

## 4. Limitações e exclusões por decisão

De [docs/plano-100.md §6](plano-100.md) (o que não dá para provar com o hardware de hoje) e
[§7](plano-100.md) (o que fica fora, por decisão — não por lacuna técnica):

- **Desafio, CAPTCHA e 2FA são sempre manuais.** O sistema nunca tenta resolvê-los; vira `AUTH_CHALLENGE` e espera
  uma pessoa. O plano melhora a fila de intervenção em volta (item 6.4), não automatiza o desafio.
- **Sem evasão de detecção** de emulador ou de antibot. Multi-conta em emulador pode ser bloqueada pela
  plataforma; o projeto não contorna isso.
- **APK só da Play Store**, com a conta Google do dono (via emulador-loja), ou de um arquivo que o dono forneça —
  nunca de espelho de terceiros; `apks/` fica fora do Git.
- **Senha nunca em resposta, log, evento, evidência, captura, prompt, memória, fixture ou Git.**
- Dois cenários ficam sem prova possível no hardware atual: worker fora da LAN (falta máquina fora da rede local)
  e worker Linux (falta máquina com KVM); o mecanismo para o primeiro existe (item 4.5 do plano), a prova, não.

## 5. Critérios de sucesso — os 9 aceites

O pedido de execução distribuída definiu 9 aceites. Há dois registros datados, e eles **não foram fundidos**:
[docs/plano-100.md §2](plano-100.md) (21/09/2026, auditoria inicial) e
[docs/relatorio-validacao.md §13](relatorio-validacao.md) (23/09/2026, tabela viva — é o texto que manda em caso
de conflito, por ser mais recente e ter ids). Um resumo de §13, com a ressalva do próprio arquivo: **toda prova
real do lado distribuído é de 19–21/09, anterior às fases 0–10 do plano**; o trabalho de código das fases (itens
0.1–13.3, ver `.claude/plano-100/estado.json`) rodou em 23–24/09 mas **não tornou a exercitar o parque real** —
cada blocker relevante nesse arquivo diz textualmente que o ensaio ao vivo "não foi executado" por proibição da
chamada que fez a implementação. Ou seja: em 24/09 o código dos 9 aceites está majoritariamente implementado, mas
a validação em infraestrutura real segue no mesmo ponto de 23/09.

| # | Aceite | Estado em 23/09 (§13) | O que muda com o código de 23-24/09 |
|---|---|---|---|
| 1 | Mesma operação em local e remoto | Local: `hibernate`/`wake`/`start`/`stop`/`open_app` provados. Remoto: só `stop` chegou a `succeeded`; `start` remoto ficou `uncertain` (`c-20260921172322-6f7fdc`, aberto desde 21/09); `hibernate`/`wake`/`restart`/`reset`/`create` nunca foram despachados a um worker | Item 1.2/1.7 implementados (`proof: real`/`simulated`, ver estado.json); a reconciliação do `uncertain` acima e um novo ensaio remoto **não** foram refeitos |
| 2 | Workflow completo remoto acompanhado pelo painel | Só leitura, 19/09, sem efeito externo, sem imagem, sem verificação por modelo | Item 1.8 implementado (simulado); ensaio real com efeito externo continua pendente |
| 3 | Controle manual de tela remoto | Só por log de eventos, sem `command_id`; sem roteiro pelo painel registrado | Sem item dedicado; segue não feito |
| 4 | Instalar/abrir app ≠ Instagram em remoto | QA Messenger nos 6 remotos (19/09); pelo catálogo de releases, nunca; conjunto completo do Instagram, só em 6.6 (23/09, prova real citada abaixo) | Item 6.6: `implemented`/`real` — conjunto real do Instagram instalado em 6 remotos, 23/09 |
| 5 | Distribuição entre dois workers | Impossível: só um worker inscrito | Item 2.1 (`LocalWorker`) implementado, simulado; ainda falta uma **segunda máquina física** — decisão e hardware do dono |
| 6 | Queda e reconexão com reconciliação | Túnel reconecta em ~5s; matar o agente durante um comando, não testado ao vivo | Item 1.4 implementado, proof `not_run`: "falta o ensaio REAL... derrubar o túnel durante um start/reset" |
| 7 | Reinício de API/scheduler sem perder tarefa | Provado só localmente (17/09), anterior às tabelas `commands`/`workers` de hoje | T.1 e item 0.1: backup/deploy reais em produção (24/09), mas reinício com comando **remoto em voo** continua não exercitado |
| 8 | Cancelamento/duplicada/incerto sem repetir efeito | Idempotência real comprovada; cancelamento remoto, não | Item 1.6 implementado, simulado ("não exercitado no mundo real: cancelar um start de verdade... os testes usam agente falso") |
| 9 | Bloqueio de execução concorrente | Só etapa×etapa num processo | Item 1.3 implementado, mas o "ensaio AO VIVO... não foi feito: é fechamento de fase" |

Leitura recomendada para retomar o trabalho: comece por
[docs/relatorio-validacao.md §13.1](relatorio-validacao.md) — lista, aceite por aceite, o roteiro já escrito e
pronto para rodar (`scripts/aceites-remotos.ps1`, `scripts/test-restart-recovery.ps1`), pendente só de autorização
para tocar o parque real.

## 6. Matriz de funcionalidades

Fonte: `.claude/plano-100/estado.json` (88 itens; campos `status`/`proof`/`evidence`/`blocker`, gerado em 24/09)
e `docs/relatorio-validacao.md`. **Regra de leitura do `proof`:** `real` só conta como ambiente real quando a
evidência cita data+máquina+id **e** o campo `blocker` não diz que o ensaio ao vivo ainda falta — vários itens
marcados `real` têm blocker afirmando o oposto (ex.: 5.6/5.7 "não há broker/MinIO nesta máquina"; 1.3/3.4/3.5/4.5
"ensaio real pendente"); esses entram como **"real parcial"** abaixo. `simulated` = só teste automatizado.
`not_run`/`pending` = não executado. `tests`/`unit` (10.5, 11.10, 12.1, 12.2, 13.1–13.3) = escrito à mão pelo
próprio executor do item, sem conferência externa — tratado aqui como **automatizada**, não como prova
independente.

| Funcionalidade | Implementação | Validação | Origem |
|---|---|---|---|
| Ciclo de vida local honesto (start/restart/reset esperam o boot) | `backend/app/api.py` (`PRAZO_POR_VERBO`, `VERBOS_QUE_ESPERAM_O_BOOT`) | Real parcial — código medido, ensaio dedicado de aceite não refeito | plano-100 1.2 |
| Um aparelho, uma operação (409 `device_busy`, trava por aparelho) | `backend/app/api.py` (`VERBOS_EXCLUSIVOS`, `_precheck`) | Real parcial — camadas central+agente testadas; ensaio AO VIVO (aceite 9) não feito | plano-100 1.3 |
| Queda de conexão não cancela o trabalho | `backend/app/worker/agent.py` (`finally` não cancela; diário de resultados) | Não executado — falta derrubar o túnel real durante um `start`/`reset` | plano-100 1.4 |
| `uncertain` com saída (reconciliação + decisão humana) | `backend/app/commands/reconciler.py` | Simulada | plano-100 1.5 |
| Cancelamento de ponta a ponta | `POST /commands/{id}/cancel` (`backend/app/api.py`) | Simulada — não exercitado com worker real | plano-100 1.6 |
| Remoto produz os mesmos efeitos que o local (`desired_state`, `hibernated`+snapshot, `wake`) | `backend/app/devices/manager.py` (`aplicar_desfecho_remoto`) | Simulada | plano-100 1.7 |
| Painel acompanha por evento (sem teto de 210s) | `frontend/src/features/devices/actions.ts` | Simulada | plano-100 1.8 |
| `LocalWorker` — central vira worker de si mesmo | `backend/app/workers/local.py` | Simulada — `worker/executor.py` não virou núcleo comum, por decisão declarada | plano-100 2.1 |
| Capacidades declaradas por worker (imagem, API, ABI, GMS…) | migração 019, `backend/app/devices/compatibilidade.py` | Simulada — `appium: local` real não ensaiado | plano-100 2.2 |
| Saúde do convidado além de `boot_completed` | `backend/app/devices/adb.py` (`framework_alive`) | Real — mas limpeza de dado herdado em produção não executada | plano-100 3.1 |
| Estado de app/sessão com identidade física (serial) | `backend/app/devices/manager.py` (`_esquecer_o_que_o_disco_tinha`) | Real | plano-100 3.2 |
| Sessão com validade (desafio/login atualiza o perfil) | `backend/app/taskqueue/executor.py` (`_sessao_desmentida`) | Real | plano-100 3.3 |
| Saúde ao vivo (`health.updated`) | `backend/app/state.py` (`_health_loop`) | Real parcial — readoção do Appium órfão em produção não confirmada ao vivo | plano-100 3.4 |
| Túnel como componente monitorado | migração 021, `backend/app/state.py` (`_probe_*`) | Real parcial — queda real do `ssh.exe` de produção não exercitada nesta chamada | plano-100 3.5 |
| Execução registra onde rodou (`worker_id`, serial, backend) | migração 022 | Real | plano-100 4.1 |
| Scheduler ciente de worker (vagas/slots por máquina) | `backend/app/taskqueue/scheduler.py` (`_rotate`) | Simulada — ensaio de carga real (6 contas/3 vagas) não feito, por tocar produção | plano-100 4.2 |
| Pré-voo na criação da execução | `RunService.pre_voo` (`backend/app/taskqueue/service.py`) | Real | plano-100 4.3 |
| Localidade de perfil (worker+aparelho físico) | migração 023 | Real | plano-100 4.4 |
| Servidor/aparelho novo sem editar YAML (conferência de inventário) | `backend/app/devices/manager.py` (`conferir_inventario`) | Real parcial — ensaio com worker fora da LAN não feito (falta máquina) | plano-100 4.5 |
| Hospedeiro por backend (`hosted_by`), `ROLE=api\|scheduler\|all` | migração 027 | Real | plano-100 5.1 |
| Limite de IA global por lease no banco (`ai_slots`) | `backend/app/taskqueue/ai_slots.py` | Real | plano-100 5.2 |
| Guarda de relógio entre backends | `backend/app/db.py` (`desvio_do_relogio`) | Real | plano-100 5.3 |
| PostgreSQL de produção (reconexão, `/health`, migração 018/028) | `backend/app/db.py` | Real parcial — perna PostgreSQL da suíte não rodou nesta chamada (falta `TEST_DATABASE_URL`) | plano-100 5.4 |
| Cofre entre backends (`key_id`) | `backend/app/security/secret_store.py` | Real | plano-100 5.5 |
| Outbox + transporte NATS (atrás de bandeira) | migração 029, `backend/app/commands/outbox.py` | Real parcial — sem broker NATS nesta máquina, não exercitado ao vivo | plano-100 5.6 |
| Storage de evidências (disco/S3) | `backend/app/storage.py` | Real parcial — sem MinIO nesta máquina, não exercitado ao vivo | plano-100 5.7 |
| Catálogo de apps (fora do `if package == instagram`) | `backend/app/planning/catalog/__init__.py` | Simulada | plano-100 6.1 |
| Comandos de app (install/canary/rollback/distribute/verify) | `backend/app/api.py` (`APP_COMMAND_VERBS`) | Simulada | plano-100 6.2 |
| Catálogo visual (ícone, versão, upload) | `backend/app/releases/inspector.py`, `catalog.py` | Simulada — `install_apk` pelo painel restrito, pendências declaradas | plano-100 6.3 |
| Fila "aguardando intervenção" | `backend/app/devices/manager.py` (hook `on_control_released`) | Real | plano-100 6.4 |
| VM da loja pode ser remota | `backend/app/config.py` | Simulada | plano-100 6.5 |
| Conjunto real do Instagram instalado em remoto | `install-multiple` via túnel | **Real** — 23/09, 21:02–21:05, android-09/10/12/13/14/15 | plano-100 6.6 |
| Hub de IA — provedor por função, roteamento | `backend/app/planning/routing.py` | Real | plano-100 7.1 (`implemented`/`real`: ator no Ollama local medido em produção em 24/09, `ad48634`; vLLM nunca exercitado) |
| Fallback explícito por função + preço cadastrado | migração 032 | Simulada — decisão do dono sobre o padrão global não tomada | plano-100 7.2 |
| Interface distingue aguardando/vaga/resposta/recusa | `backend/app/taskqueue/executor.py` | Real | plano-100 7.3 |
| Bateria de avaliação (linha de base vs. atual) | `scripts/eval-run.ps1`, `scripts/eval-rejudge.ps1` | Real parcial — falta rejulgar as 56 capturas com o modelo caro | plano-100 7.4 (`partial`) |
| Cache de prompt sempre ligado + escalonamento por risco | `backend/app/planning/anthropic_provider.py` | Real — medido: Instagram 2/3→3/3, US$0,20→0,08/caso | plano-100 7.5 |
| Dieta de contexto do ator (−tokens por decisão) | `backend/app/planning/prompts.py`, `hierarchy.py` | Real | plano-100 7.6 |
| Estimativa de custo antes de rodar | `GET /api/flows/cobertura` | Real | plano-100 7.7 |
| Modelo local (Ollama) preparado, piso de conteúdo | `backend/app/planning/openai_provider.py` | Real parcial — Ollama real não exercitado no teste do item (não instalado no momento do item); **ligado em produção depois, 24/09** (ver `docs/ia.md` §10) | plano-100 7.8 |
| Personas completas + prova antes/depois | `scripts/personas_completar.py` | Não executado — preencher as 8 personas é decisão e gasto do dono | plano-100 8.1 |
| Memória de DM com resumo real | `backend/app/social/` | Não executado — aceite de nível 2 (conversa real entre duas contas) | plano-100 8.2 |
| Sinais e limites (classificador, teto diário) | `backend/app/social/` | Real parcial — `REPLY_COMMENT` em aparelho real não confirmado | plano-100 8.3 (`partial`) |
| Instagram operando em worker remoto | — | **Bloqueado** — autorização do dono pendente (instalar app real em aparelho remoto) | plano-100 8.4 (`blocked`) |
| Login/sessão do painel com identidade | `backend/app/security/sessions.py` | Real | plano-100 9.1 |
| TLS na porta de rede | `backend/app/config.py` (`tls_cert`/`tls_behind_proxy`) | Real | plano-100 9.2 |
| Credencial de worker revogável/rotacionável | `backend/app/workers/registry.py` (`rotate_credential`) | Real | plano-100 9.3 |
| Túnel com usuário restrito | `scripts/worker-ssh-restrito.ps1` | Não executado — feito parcialmente em 23/09 (digital de host conferida), resto pendente | plano-100 9.4 |
| Redação de dado sensível (URL, `Authorization`, chaves) | `backend/app/security/redaction.py` | Real parcial — job de CI de dependências nunca rodou de fato neste repositório | plano-100 9.5 |
| Tudo volta sozinho (tarefas supervisionadas) | `scripts/install-central-service.ps1`, `scripts/worker-agent.ps1` | Real | plano-100 10.1 |
| Crescimento sob controle (retenção, rotação de log) | — | Real parcial — envio de log do agente sob demanda não implementado | plano-100 10.2 |
| Capacidade (alvo, alerta, teste de escala) | `scripts/scale-test.ps1` | Real | plano-100 10.3 |
| Worker Linux | `backend/app/worker/` | Não executado — falta máquina Linux com KVM | plano-100 10.4 |
| Limites por servidor + distribuição entre servidores | migração 039, `frontend/src/features/settings/ServersLimits.tsx` | **Automatizada** (`proof: unit`) — 15 testes; sem ensaio de carga real registrado aqui | plano-100 10.5 |
| Usabilidade do painel (11.1–11.9: rolagem, tabelas, perfil, diagnóstico, instâncias) | `frontend/src/features/*` | Real | plano-100 11.1–11.9 |
| Grupos de acesso (política herdável por grupo) | migração 036, `backend/app/social/policy.py` | **Automatizada** (`proof: tests`) | plano-100 11.10 |
| Contas por app (`profile_accounts`, `app_id` por etapa) | migração 037 | **Automatizada** (`proof: tests`) | plano-100 12.1 |
| Telas de Perfil/Aplicativos por app | `frontend/src/features/profiles/` | **Automatizada** (`proof: tests`) | plano-100 12.2 |
| Apps novos operando de verdade (Outlook, TikTok…) | — | **Não iniciado** — decisão do dono sobre qual app vem primeiro | plano-100 12.3 (`pending`) |
| Modo treinamento — gravação | migração 038 | **Automatizada** (`proof: tests`) | plano-100 13.1 |
| Modo treinamento — generalização assistida (`generalize`) | `backend/app/planning/training.py` | **Automatizada** (`proof: tests`) | plano-100 13.2 |
| Modo treinamento — telas | `frontend/src/features/` | **Automatizada** (`proof: tests`) | plano-100 13.3 |

Itens de `.claude/plano-100/estado.json` não listados linha a linha acima (fase 0 completa, T.1–T.3) estão
resumidos no §5; para o detalhe por item, ler o próprio `estado.json` ou
[docs/execucao-plano-100-runner.md](execucao-plano-100-runner.md) (arquivo gerado, não editar — tem `arquivo:linha`
por item).

## 7. Lacunas para o backlog

Extraídas desta varredura, para o coordenador priorizar (não implementadas aqui):

- Nenhum dos 9 aceites de execução distribuída tem ensaio **ao vivo** completo desde 23/09 — a fase de código
  (23–24/09) não voltou a tocar o parque real. Roteiro pronto em `docs/relatorio-validacao.md §13.1`.
- Item 12.3 (apps novos operando de verdade) está `pending`: decisão do dono sobre qual app vem primeiro nunca
  foi tomada.
- Item 8.4 (Instagram num worker remoto) está `blocked`: falta autorização para instalar o app real (~238 MB) em
  aparelho remoto.
- Itens com proof `tests`/`unit` escrito pelo próprio executor (10.5, 11.10, 12.1, 12.2, 13.1–13.3) não têm
  conferência independente registrada — candidatos a auditoria cruzada antes de contar como fechados.
- Item 9.4 (túnel com usuário restrito): só a digital de host foi conferida em 23/09; o restante do endurecimento
  do túnel segue pendente.
- Item 8.1 (personas completas): 8 dos 15 campos de voz seguem vazios nas personas reais (achado #107 do
  plano-100), decisão e trabalho do dono.
