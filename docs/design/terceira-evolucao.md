# Terceira evolução: Outlook e comando entre apps, rede por aparelho, pedidos persistentes

Pedido do dono de 29/09/2026 (`prompt-outlook-comandos-multiapp-tarefas-continuas.md`, fora do repositório): (1) o
Outlook na loja e em todos os perfis, com as contas vinculadas e o funcionamento comprovado, e comandos que atravessam
vários apps; (2) VPN e proxy por aparelho, com isolamento, persistência e prova da rota e do IP de saída; (3) pesquisa
e desenho de pedidos persistentes. Esta página é o **diagnóstico e o desenho**; a coordenação (frentes, arquivos
reservados, ordem, dependências e pendências) está em [../handoffs/terceira-evolucao.md](../handoffs/terceira-evolucao.md)
e os itens nas Fases 23–27 de [../plano-100.md](../plano-100.md). Decisões: ADR-056 a ADR-059 em
[../decisoes.md](../decisoes.md).

O diagnóstico foi feito só por leitura (`GET` no central e código em `a962edb`) em 29/09. Os números de linha são
daquele commit; a Fase 22 (`de03a4c`) já deslocou `taskqueue/{scheduler,executor,service,repository}.py`. Confira
no código pelo nome da função antes de repetir um achado.

## 1. Decisões já tomadas

| # | Decisão | Origem |
|---|---|---|
| D0 | **Rever a cláusula de rede do ADR-055 por ADR novo (ADR-056).** Saída distinta e estável por aparelho vira objetivo. A recomendação contrária da IDE (manter o ADR-055) fica anotada no ADR | dono, 29/09 (pergunta desta sessão) |
| D1 | **Credencial do Outlook clonada dentro do cofre**, em entrada própria da conta; consentimento continua por conta, dado pelo dono | dono, 29/09 (pergunta desta sessão) |
| D2 | **Outlook é o primeiro app do item 12.3** (decisão pendente em `docs/roadmap.md` §1) | o próprio pedido |
| D3 | O valor que atravessa apps é dado comum, triado por formato | invariante vigente |
| D4 | Colaboração entre personas é divisão interna de trabalho; para fora vale uma conta por alvo (ADR-055), com aprovação e sem simular apoio de pessoas independentes | pedido §6 + ADR-055 |

## 2. Diagnóstico (medido em 29/09, somente leitura)

### 2.1 Git, sessões e ambiente

| Fato | Evidência |
|---|---|
| Base do planejamento: `origin/main` em `de03a4c` (o checkout do central segue no commit implantado) | `git log -1 origin/main` |
| Central em `c071341` no início, `b34e2f6` no fim do planejamento; migração 055, health `ok` | `GET /api/health`, `/api/workers` |
| **Fase 22** integrada em `origin/main` (`de03a4c`) e implantada (`b34e2f6`) durante este planejamento. Mudou `_run_guarded`/`_apply`, `retry_failed`/`resolve`, `finish_attempt`, `frontend/src/features/runs/*`, adendo v0.40, K-061. **Não tocou** `_plan`, `_app_context`, `_portas_do_app`, `_mistura_de_apps`. Sem migração | `git log origin/main`, `GET /api/health` |
| Nenhuma execução em andamento; todas as sessões da IDE ociosas | `GET /api/runs`, `ListAgents` |
| Parque: online android-01 (lucas, **sem o Instagram desde o reset**) e android-06 (andre); hibernados 02 e 05 (QA); parados 03 (bruno), 04 (quarentena), 07, 08, 09–15 (remotos), 11 (loja) | `GET /api/instances` |
| Proxy: **nenhum perfil cadastrado** | `GET /api/proxies` |
| Saldo estimado da Anthropic US$ 3,31 | `docs/estado-atual.md:26-27` |
| `claude-plan-100.py check` interrompe: pacotes (fora do Git) velhos | saída do comando |

**Números reservados:** Fases **23–27**; ADR-**056…059**; K-**062+**; migrações **056–058**; adendo **v0.41+**.
(Maiores em todos os branches em 29/09: migração 055, ADR-055, K-061 e adendo v0.40.)

### 2.2 Rede por aparelho

| Tema | Fato | Onde |
|---|---|---|
| Proxy atual | `settings put global http_proxy`, relê e compara; "prova a configuração, não o tráfego"; sem autenticação | `backend/app/devices/proxy.py:8-12,208-215` |
| Dados | `proxy_profiles`, `device_proxy_state` (desejado × observado) | `backend/migrations/041_loja_de_apps.sql:24-41` |
| Pendência | `pending` entregue ao ligar e na varredura de 60 s | `backend/app/vitrine.py:180-233` |
| Desvio | `applied` nunca é relido; wipe e reset não invalidam | `devices/manager.py:520,525` |
| Excluídos | loja e quarentena; o celular físico não | `proxy.py:83-84`, `vitrine.py:187-189` |
| Emulador | sem `-http-proxy`; flag extra invalida o snapshot | `devices/emulator.py:26-50`, `manager.py:3769-3775` |
| Worker | agente só tem verbos de ciclo de vida; o proxy remoto sai do central pelo túnel | `worker/executor.py:46`, `proxy.py:208-211` |
| Imagem | `android-34;google_apis;x86_64`; `adb root` evitado (derruba adbd) | `config.py:165`, `devices/adb.py:701-702` |
| Conectividade | sonda por `ip route`/`dumpsys`/`ping`/`nc`; sem `curl`; **sem medição de IP de saída** | `devices/sonda_rede.py:18-29` |
| Portão | só "internet `healthy`" por app; nada de "rede exigida" | `taskqueue/scheduler.py:405-443` |
| Cofre | `get_secret` só no canal sensível; redação não cobre `socks5://` nem chave WireGuard | `security/secret_store.py:7-8`, `security/redaction.py:25-61` |

### 2.3 Outlook, loja e contas

| Tema | Fato | Onde |
|---|---|---|
| Loja e distribuição | genéricas por pacote; **sem código novo**. Dependem do dono no android-11 (login Google, Instalar) | `releases/service.py:684-737`, `vitrine.py` |
| Conhecimento declarado | só o Instagram. App novo = pasta com `app.yaml` (+ `telas.yaml` e `sessao.yaml` se houver login gerenciado). Dois apps âncora derrubam o registro | `conhecimento/apps/`, `registry.py:230-240`, `pacote.py:186-190` |
| Conta por app | `profile_accounts`, `account_credentials`, `account_sessions`; vínculo (persona, aparelho, app) | migrações 037, 049, 051 |
| **Login preso à âncora** | credencial, tentativa, sessão e invalidação resolvidas pela conta do Instagram; a interface de sessão não recebe a conta | `integrations/app_declarado/sessao.py:387-390,523,541,668-673,874`; `state.py:754-772,806-819,1019,1042`; `modules/identity/application/ports.py:34-58` |
| Formulário | o motor exige usuário, senha e botão na mesma tela; conta lida só por aba inferior; Custom Tab vira "outro app" | `app_declarado/formulario.py:34-83,116-137`; `executor.py:381-384` |
| Desafio | trava em qualquer app bloqueia a persona inteira e põe o aparelho em quarentena | `session_rules.py:46-113` |
| Reuso de senha | não há caminho; `_guardar_senha` grava sempre na referência da própria conta | `social/service.py:358-373` |
| Caminho sem login gerenciado | a IA digita a senha consentida da conta pelo canal sensível | `available_data.py:200-251` |
| Painel | `ehInstagram` fixo; política de ações sem app | `GuiaContas.tsx:29-34`, `PolicyGroups.tsx:23-31` |
| Contas | vivas: lucas (01), bruno (03), andre (06); 5 bloqueadas | `docs/relatorio-desempenho.md:852-867` |

### 2.4 Comando entre aplicativos

Já existe (item 12.1, prova só automatizada): app por etapa (`models.py:194-196`), contexto de app por etapa
(`scheduler.py:1223-1233`), portas por app no despacho (`scheduler.py:318-327,405-464`), skill com nós de apps
diferentes (`modules/skills/domain/compiler.py:503-539`), retomada sem repetir efeito (`scheduler.py:1438-1447`).

| Falta | Onde |
|---|---|
| R1. Citar outro app derruba o catálogo, e a porta de política recusa efeito sem capability | `service.py:60-67,677-685`; `state.py:1660-1670` |
| R2. Planejador com catálogo conhece um app só | `planning/prompts.py:344-354`; `planning/parsing.py:142-160` |
| R3. Nenhum dado passa de uma etapa a outra, fora `{item}` | `automation/tools.py:192-197`; `taskqueue/foreach.py:15-31` |
| R4. "Conta esperada" é a do aparelho, não a do app da etapa | `scheduler.py:1227-1233`; `prompts.py:360` |
| R5. Roteamento, sessão pronta e modo Automático por um app | `service.py:234-292`; `orquestrador.py:119`; `alvos.py:151-152` |
| R6. Plano livre não declara `required_apps` | `parsing.py:118-130` |
| R7. Checagens só do app principal ou do primeiro pacote | `service.py:399-424,594-601`; `state.py:1337-1347`; `scheduler.py:466-479,533` |
| R8. Portas não repassadas na troca de app dentro de `_work` | `scheduler.py:1070-1114` |
| R9. Painel com um app só | `CommandPanel.tsx:159-168`; `PlanTab.tsx:100` |

### 2.5 Pedidos persistentes

Não há agendamento, recorrência nem gatilho. Base reutilizável: espera com hora marcada (`scheduler.py:1204-1218`,
`repository.py:335-348`), `runs.idempotency_key`, foto `runs.targets` e sucessora (`assistente.py:143-195`), gancho
`on_run_settled` (`state.py:342`), eventos com id crescente, posse por prazo, orçamento e saldo, memória, política,
aprovações e aprendizado. Lacunas: entidade de pedido e gatilho; laços sem trava de líder (`state.py:2083`); teto de
4 h por objetivo (`config.py:290`); dependência e consolidação entre objetivos; valor lido no relatório; canal de
aviso; assinante interno do `EventBus`.

## 3. Decisões técnicas

### Rede (ADR-056)

| # | Decisão | Fundamento |
|---|---|---|
| T1 | **A VPN roda dentro do Android do aparelho**, num único cliente `VpnService`, com always-on e "bloquear conexões sem VPN" | [VPN no Android](https://developer.android.com/develop/connectivity/vpn): um serviço por usuário; o bloqueio é do sistema. Rota no host mexeria no caminho do túnel e do ADB (exige autorização) |
| T2 | **O `-http-proxy` do emulador não é usado** | [Proxy no emulador](https://developer.android.com/studio/run/emulator-networking-proxy): "doesn't support UDP redirection"; a senha iria em argumento de processo; flag invalida o snapshot |
| T3 | **VPN e proxy compõem dentro do mesmo cliente**: app → TUN → proxy autenticado → túnel VPN → saída. Saída final: o proxy, se houver; senão o servidor VPN. Com proxy HTTP, UDP é bloqueado (só DNS pelo túnel); com SOCKS5, segue se o provedor aceitar | dois `VpnService` disputam o serviço único; `setHttpProxy` é só recomendação |
| T4 | Cliente: **sing-box** (VPN + proxy encadeado, campo `detour`) ou **WireGuard oficial** (só VPN), ambos da Play Store. Escolha pela medição do item 25.1 | [sing-box dial](https://sing-box.sagernet.org/configuration/shared/dial/); intents do WireGuard |
| T5 | O proxy global legado é rebaixado: no máximo `configurado`, nunca `tráfego verificado` | `proxy.py:8-9` |
| T6 | **Cinco estados separados** por aparelho: `pendente`, `configurado`, `conectado`, `trafego_verificado`, `parcial`. Cada evidência diz o que prova | pedido §3 |
| T7 | Endpoint, configuração e **IP de saída medido** são campos distintos. Dois aparelhos com o mesmo IP medido geram aviso; configuração diferente não é prova de IP diferente | pedido §3 |
| T8 | IP de saída medido de dentro do aparelho pelo **app de QA estendido** e conferido por UID (`dumpsys connectivity`, `netstats`) para Outlook e Instagram | sonda atual ignora proxy; teste no navegador não prova outro app |
| T9 | Segredo de rede no cofre por `secret_ref`, com **segundo consumidor** de `get_secret` restrito à provisão de rede; entrega por stdin ou arquivo no convidado, nunca argumento nem evento; redação por formato ganha `socks5://`, `PrivateKey`, `PresharedKey` | `secret_store.py:7-8`; `redaction.py:25-61` |
| T10 | Aplicação pelo central via ADB (inclusive remotos, pelo túnel); **sem verbo novo no agente** | precedente `apps_de_fundo` (K-059) |
| T11 | Loja (android-11) e aparelho em quarentena ficam fora; aparelho com conta real só muda de rede com autorização do dono por aparelho | ADR-055; risco de desafio ao trocar a saída de conta logada |

### Outlook e contas (ADR-057)

| # | Decisão |
|---|---|
| T12 | Outlook **não é âncora**. Primeiro marco pelo caminho livre (só `app.yaml`); login gerenciado entra depois de desancorar o motor |
| T13 | `SessionProvider` passa a receber a **conta** (`account_id`); credencial, tentativa, sessão e invalidação por conta e pacote |
| T14 | Formulário em etapas e leitura da conta fora da aba inferior entram no **motor genérico**, declarados em YAML; zero Python por app (ADR-052) |
| T15 | `SecretStore.clonar(ref)` devolve referência nova sem expor o valor; só entre contas da mesma persona; consentimento nunca é clonado |
| T16 | Endereço Outlook vem do dado conferido pelo dono; nunca derivado de `<usuario>@outlook.com` |
| T17 | Catálogo inicial do Outlook só de leitura; enviar e-mail fica `manual_only` |

### Comando entre apps (ADR-058)

| # | Decisão |
|---|---|
| T18 | Catálogo por app da etapa; o planejador recebe os catálogos dos apps exigidos; `required_apps` sempre preenchido |
| T19 | A porta de política não muda de regra: efeito sem capability em app com catálogo continua recusado |
| T20 | Saída de etapa tipada e nomeada (`read_value`), persistida; código, senha e token nunca são saída: a etapa para (D3) |
| T21 | Portas de app, internet, sessão e rede repassadas a cada troca de app |

### Pedidos persistentes (ADR-059, proposto)

| # | Decisão |
|---|---|
| T22 | Estado e execução no backend e nos workers; nada depende da IDE nem das ferramentas de agendamento dela |
| T23 | Um pedido gera **execuções** (`runs`) por ocorrência; a identidade da ocorrência é a chave de idempotência |
| T24 | Vocabulário de partida: sobreposição, janela de recuperação, `coalesce` ([Temporal Schedules](https://docs.temporal.io/schedule), [APScheduler](https://apscheduler.readthedocs.io/en/master/userguide.html)). Adoção de biblioteca é resultado da pesquisa, não premissa |

## 4. Aceites por frente

Os critérios "Fecha quando" de cada fase estão em [../plano-100.md](../plano-100.md) (Fases 23 a 27). Em resumo:

- **Outlook (Fase 23):** instalação, vínculo e autenticação registrados separadamente, por perfil; abrir o app não
  prova login; fechar e reabrir mantém a sessão.
- **Comando entre apps (Fase 24):** Outlook → Instagram sem efeito externo, com valor lido numa etapa usado na outra, e
  conta indisponível, interrupção e retomada sem repetir etapa concluída.
- **Rede (Fase 25):** por aparelho, `configurado`, `conectado`, `trafego_verificado`, `parcial` ou `pendente`, com IP de
  saída medido de dentro do aparelho, cobertura por app (Outlook e Instagram), DNS, IPv4, IPv6 e UDP, persistência
  depois de reinício e hibernação, isolamento e controle remoto preservado.
- **Pedidos persistentes (Fase 26):** pesquisa com fontes datadas, recomendação e plano incremental.
- **Integrado (Fase 27):** rede validada, conta correta e comando entre apps no mesmo aparelho.
