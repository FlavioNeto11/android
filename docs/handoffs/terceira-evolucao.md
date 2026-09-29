# Checkpoint — terceira evolução (Outlook, comando entre apps, rede por aparelho, pedidos persistentes)

Pedido do dono de 29/09/2026 (`prompt-outlook-comandos-multiapp-tarefas-continuas.md`). **Escritor único deste
arquivo: o coordenador.** Se a sessão cair, retome daqui: não refaça o diagnóstico
([../design/terceira-evolucao.md](../design/terceira-evolucao.md)) e não reabra decisões (ADR-056 a ADR-059) sem
evidência nova. Os itens estão nas Fases 23–27 de [../plano-100.md](../plano-100.md); o estado de cada um, só pelo
mecanismo (`scripts/claude-plan-100.py`).

## Base e integração

- **SHA base do planejamento:** `origin/main` em `de03a4c` (Fase 22 integrada; central em `b34e2f6`).
- **Planejamento registrado** no branch `claude/evolucao3-plano` (worktree `.claude/worktrees/evolucao3-plano`), só
  documentação, publicado em `origin/main`.
- **O checkout `C:\git\android` é o do ambiente central:** fica no commit implantado; nada é integrado nele sem
  deploy combinado com as outras sessões.
- **Números reservados:** Fases 23–27; ADR-056 a ADR-059; K-062 em diante; migrações 056 (saídas de etapa), 057
  (rede por aparelho) e 058 (reserva); adendo do contrato v0.41 em diante. Antes de usar, confira em todos os
  branches (`git for-each-ref`): outras sessões numeram em paralelo.
- **Estado desta etapa:** só planejamento. Nenhum código, nenhum aparelho tocado, nenhuma chamada paga. Todos os
  itens das Fases 23–27 estão pendentes e `not_run`.

## Decisões do dono tomadas nesta etapa (29/09)

- **Rede:** rever a cláusula de rede do ADR-055 por ADR novo (ADR-056); a recomendação contrária da IDE está anotada
  nele.
- **Senha do Outlook:** clonar dentro do cofre, com consentimento por conta (ADR-057).
- **12.3:** o Outlook é o primeiro app novo (o próprio pedido).

## Decisões e autorizações do dono na execução (29/09, ~17:40Z)

- **P3:** a IDE toca em "Instalar" na Play Store do android-11 (Outlook, WireGuard, sing-box; gratuitos), só com a
  conta Google já logada; login, senha ou verificação voltam ao dono.
- **P4:** o e-mail `outlook.com` cadastrado na persona é o endereço do Outlook, com a mesma senha do Instagram; só
  as 3 ativas (André, Bruno, Lucas). As 5 bloqueadas ficam pendentes; as 6 personas sem e-mail recebem o app sem conta.
- **P2:** servidor WireGuard no central autorizado. Escolha técnica: sing-box oficial em modo usuário (WireGuard
  sem driver, sem NAT e sem mudar a rede do sistema), com um proxy autenticado no mesmo processo para provar a
  composição. O IP de saída do piloto é o do central, e isso fica registrado.
- **P8:** IA paga pontual até US$ 1,50 nas provas 23.13, 24.9 e 27.2.
- **P7:** rede nova em android-03 e android-06 depois de validada nos de QA, um por vez, fora de uso, conta
  conferida antes e depois.
- **Login no Outlook** das 3 ativas autorizado (só leitura da caixa; verificação da Microsoft volta ao dono).
- **P9:** desafio ou código no Outlook para só a conta Outlook; `conta_travada` mantém a quarentena do aparelho.
- **Ainda pendentes:** P1 (provedor, para IP distinto), P5 (consentimento de cada conta Outlook, no painel), P10
  (bloqueadas e lucas no Instagram), P12, P13.

## Frentes, contratos e arquivos reservados

Coordenador único (esta sessão) escreve o handoff, os contratos, `docs/estado-atual.md`, `CHANGELOG.md`, o plano e
integra. Cada frente em worktree próprio a partir do SHA dos contratos.

| Frente | Itens | Arquivos reservados |
|---|---|---|
| A. Distribuição | 23.2, 23.3, 23.12, 25.10 | `conhecimento/apps/com.microsoft.office.outlook/app.yaml`; operação pela API |
| B. Contas e autenticação | 23.4–23.11, 23.13 | `integrations/app_declarado/*`, `modules/identity/*`, `social/*`, `security/secret_store.py`, `automation/hierarchy.py`, `frontend/src/features/profiles/*` |
| C. Comando entre apps | 24.1–24.9 | `taskqueue/{service,scheduler,executor,foreach,orquestrador}.py`, `planning/*`, `automation/tools.py`, `modules/execution/application/alvos.py`, `frontend/src/features/{runs,command}/*` |
| D1. Rede: núcleo | 25.1–25.5 | `devices/rede.py` (novo), `devices/{proxy,sonda_rede,conectividade,adb}.py`, `security/redaction.py`, `vitrine.py`, `commands/despacho.py`, `qa-app/` |
| D2. Rede: fila e worker | 25.6, 25.7 | trecho do portão em `scheduler.py` (depois da frente C), `worker/settings.py` |
| D3. Rede: painel | 25.8 | `frontend/src/features/loja/ProxyPage.tsx` → `features/rede/*` |
| E. Pedidos persistentes | 26.1–26.8 | `docs/design/pedidos-persistentes.md` |
| V. Validação e revisão | 27.1–27.3 | `docs/relatorio-validacao.md` (seção nova) |

**Onda 0, contratos (só o coordenador edita):** `state.py`, `api.py`, `models.py`, `frontend/src/api/{types,client}.ts`,
`docs/api-contract.md`, `backend/migrations/056_*.sql` e `057_*.sql`, `docs/banco.md`.

| Contrato | Conteúdo |
|---|---|
| C1 | `SessionProvider.ensure_session` com `account_id` |
| C2 | `StepOutcome.outputs`, referência de saída e tabela de saídas (056) |
| C3 | Tabelas e DTO de rede, comando `device.network`, os cinco estados (057) |
| C4 | Assinatura da porta de rede injetada no scheduler |
| C5 | Conjunto de apps em alvos, prévia e sugestão |

Cada delegação leva: objetivo, pacote do item, arquivos reservados, contratos, regras do `CLAUDE.md` que valem
para subagente (sem commit, sem mundo real, teste direcionado, sem segredo, migração nova), aceite e formato de
entrega (`resultado.json` do mecanismo).

**Dependências por item** (vão para o handoff):

| Item | Depende de |
|---|---|
| 23.4, 24.3, 25.2 | Onda 0 (C1, C2, C3) |
| 23.8 | 23.6, 23.7 |
| 23.11 | 23.9, 23.10, P4, P5 |
| 23.12 | 23.2, 23.3 |
| 23.13 | 23.4, 23.8, 23.11, 23.12, 25.9 no aparelho |
| 24.1–24.7 | Fase 22 (integrada em `de03a4c`) |
| 24.9 | 24.1–24.7, 23.13, P8 |
| 25.4 | 25.1, 25.2, 25.3, 25.10 |
| 25.6 | 25.2, 24.4 (portas repassadas na troca de app) |
| 25.9 | 25.4, 25.5, 25.8, P1 ou P2 |
| 26.2–26.8 | 26.1; 26.5 e 26.8 alinhados ao 24.3 e ao 24.5 |
| 27.2 | 23.13, 24.9, 25.9 no mesmo aparelho |

## Ordem de integração

1. **Fase 22**: integrada em `origin/main` (`de03a4c`) e implantada (`b34e2f6`) enquanto este plano era feito. A
   dependência está resolvida; todas as frentes partem do SHA dos contratos (Onda 0).
2. **Onda 0**: contratos C1–C5 e 23.1 num commit do coordenador.
3. **Onda 1, em paralelo, prova `simulated`**: B (23.4–23.6, 23.9, 23.10), C (24.1–24.8), D1–D3 (25.2–25.6, 25.8),
   E (26.x), V (27.1). Em paralelo, com o dono: 23.2, 23.7, 25.1, 25.10.
4. **Integração**: contratos → contas → comando entre apps → rede → painel. Suíte inteira uma vez, em segundo
   plano e prioridade ociosa (K-058). Deploy com ensaio e backup, combinado com as outras sessões.
5. **Ondas reais por aparelho**, nesta ordem dentro de cada aparelho: rede validada (25.9) → Outlook instalado
   (23.12) → conta e login (23.11, 23.13) → comando entre apps (24.9). Aparelhos: android-05 (piloto), android-02,
   um remoto (android-09), e só então aparelhos com conta real, um por vez, com autorização.
6. **Aceite integrado** (27.2) e fechamento (27.3).

## Pendências e bloqueios

| # | Pendência | Bloqueia |
|---|---|---|
| P1 | Provedor de VPN e de proxy: endpoints, protocolo, credenciais, quantidade de IPs. Nada é contratado automaticamente | 25.9 além do piloto; IPs distintos ficam `not_run` |
| P2 | Par para o piloto (servidor WireGuard próprio no notebook ou no central mexe na máquina) | 25.1 |
| P3 | Login Google e Instalar no android-11 (Outlook e cliente VPN) | 23.2, 25.10 |
| P4 | Endereço Outlook completo de cada persona | 23.11 |
| P5 | Consentimento por conta Outlook | 23.11, 23.13 |
| P6 | Confirmar o propósito que o ADR-056 cita do pedido: "isolamento, persistência e comprovação da rota e do IP de saída" e, "como objetivo de separação", "saída pública distinta e estável por dispositivo" | texto final do ADR-056 |
| P7 | Autorização por aparelho para trocar a rede de conta real logada | 25.9 nos aparelhos 03 e 06 |
| P8 | Saldo de IA (Anthropic em US$ 3,31) e autorização das validações pagas | 23.13, 24.9, 27.2 |
| P9 | Desafio no Outlook bloqueia a persona inteira ou só a conta? | 23.5 |
| P10 | Personas bloqueadas (5) e lucas (android-01 sem o Instagram) | 23.11, 23.13 |
| P11 | RAM de 2 GB com dois apps grandes: medir no canário; pode pedir 3 GB | 23.12 |
| P12 | Troca das senhas das 3 contas vivas (pendência anterior): depois do clone as senhas ficam independentes | 23.11 |
| P13 | Janela de deploy combinada com as outras sessões | passo 4 |
| P14 | Créditos da IDE: a única medição é ~US$ 8 por item médio em Opus (n=3, `docs/claude-plano-100.md`). Com ~30 itens de código, a faixa é de US$ 200 a 400, contra US$ 250 de crédito registrado em 28/09 | ritmo da Onda 1 |

## Contratos da Onda 0 (especificação; escritor: o coordenador)

Todos compatíveis para trás: o padrão reproduz o comportamento de hoje. As frentes implementam o comportamento;
a Onda 0 só cria a forma, com teste de contrato.

- **C1 — sessão por conta.** `SessionProvider.ensure_session(rt, profile_id, *, account_id: str | None = None,
  force_login=False, automatic=False, observe_only=False)` em `modules/identity/application/ports.py` e em quem a
  implementa (`integrations/app_declarado/sessao.py`) e nos dublês (`test_dubles_cumprem_as_portas.py`).
  `account_id=None` = a conta do app âncora, como hoje. A resolução por conta é o 23.4.
- **C2 — saídas de etapa.** Migração `056_saidas_de_etapa.sql`: tabela `step_outputs` (`id` TEXT PK, `run_id`,
  `objective_id`, `step_id` com FK e `ON DELETE CASCADE` como `steps`, `name` TEXT, `value` TEXT, `value_kind` TEXT
  `text|number|url|list`, `app_id` TEXT NULL, `created_at`; `UNIQUE(objective_id, name)`: o nome é único no
  objetivo, a última escrita vence). `PlanStep.saidas: list[str] = []` (nomes que a etapa produz) e
  `StepOutcome.outputs: dict[str, str] | None = None`. Referência no texto e nas variáveis da etapa:
  `{{saida:<nome>}}`, resolvida pelo executor antes da etapa (24.3). Repositório: `Repository.save_step_output`,
  `Repository.step_outputs(objective_id) -> dict[str, str]`. Nome: `^[a-z][a-z0-9_]{0,39}$`; valor até 2000
  caracteres.
- **C3 — rede por aparelho.** Migração `057_rede_por_aparelho.sql`:
  - `network_profiles` (`id`, `name` único, `kind` `vpn|proxy`, `protocol` `wireguard|singbox|http|socks5`,
    `endpoint_host`, `endpoint_port`, `secret_ref` NULL, `params` JSON sem segredo, `created_at`, `created_by`);
  - `device_network` (`instance_id` PK, `vpn_profile_id` NULL, `proxy_profile_id` NULL, `policy`
    `livre|exigida|exigida_com_bloqueio` padrão `livre`, `desired_rev` INT, `applied_rev` INT NULL, `state`
    `pendente|configurado|conectado|trafego_verificado|parcial`, `detail`, `error`, `egress_ipv4`, `egress_ipv6`,
    `verified_at`, `updated_at`, `updated_by`);
  - `network_measurements` (`id`, `instance_id`, `measured_at`, `method`, `egress_ipv4`, `egress_ipv6`,
    `dns_resolver`, `udp_ok` INT NULL, `per_app` JSON, `leak_blocked` INT NULL, `detail`).
  DTOs em `models.py` (`NetworkProfileDTO` sem segredo, só `has_secret: bool`; `DeviceNetworkDTO`;
  `NetworkMeasurementDTO`; `NetworkState` e `NetworkPolicy` como `Literal`) e em `frontend/src/api/types.ts`. Verbo
  de comando `device.network` em `commands/despacho.py::APP_COMMAND_VERBS`. Rotas (25.2/25.8): `GET/POST
  /api/network/profiles`, `DELETE /api/network/profiles/{id}`, `GET /api/network/devices`, `POST
  /api/network/assign` (lote com `dry_run`), `POST /api/network/devices/{iid}/verify`, `POST
  /api/network/devices/{iid}/reapply`. As tabelas da 041 ficam; o proxy legado é lido como `configurado` no máximo.
- **C4 — portão de rede.** `Scheduler.rede_gate: Callable[[str], str | None] | None = None` (recebe o
  `instance_id`, devolve o motivo da espera ou `None`), no molde de `worker_gate`; o `_tick` chama e faz
  `note_waiting` com `wait_reason="rede"`. Ligado em `state.py` pelo 25.6.
- **C5 — conjunto de apps.** `RunTargetsSuggestion.app_ids: list[str] = []` ao lado de `app_id` (que segue sendo o
  primeiro); `ResolvedTargetDTO.app_ids` idem; `Plan.required_apps` já existe e passa a ser a fonte. Frontend:
  os tipos espelham.
- **Números:** adendo do contrato **v0.41** (Onda 0) e seguintes; migrações 056 e 057 citadas em `docs/banco.md`.

## Registro da integração

Cada lote integrado entra aqui com o SHA, a frente, o que entrou e a prova (`simulated`, `real` ou `not_run`).

**Checkpoint 1 (29/09 ~17:55Z).** Integração no worktree `.claude/worktrees/evo3`, branch `claude/evolucao3`, a
partir de `2a822b6`, com junções `backend/.venv` e `frontend/node_modules` (desfazer com `rmdir` do link; nunca
apagar recursivo).
- **Real, sem código novo:** android-11 ligado (`c-20260929173711-9e0446`); a Play Store já tinha o Outlook
  5.2635.3; a IDE instalou WireGuard 1.0.20260315 e sing-box 1.14.2 (autorização P3); `store/sync` importou os dois
  clientes (`c-20260929174417-3303c9`, `c-20260929174423-bdc392`) e a assinatura deles foi aprovada (releases
  `com.wireguard.android-519-e2ae03cae9df`, `io.nekohasekai.sfa-739-5535a350073a`, `installable`).
- **Defeito achado no 23.2:** o `store/sync` do Outlook (`c-20260929174407-153853`) recusou a importação:
  `split_config.en.apk` sem esquema v1 ("Missing META-INF/MANIFEST.MF"). Corrigido em `releases/inspector.py`
  (`_signature` repete com `--min-sdk-version 24`), teste
  `tests/test_app_releases.py::test_split_sem_esquema_v1_le_a_assinatura_pelo_v2_e_v3`; prova com os 4 APKs reais
  do Outlook (mesmo assinante; base 159 MB, minSdk 30). A importação real espera o deploy.
- **Servidor do piloto:** `data/rede/sing-box-1.14.2-windows-amd64/sing-box.exe` (GitHub SagerNet, SHA-256
  conferido com o `digest` publicado), fora do Git.
- **Em curso (agentes):** Onda 0 (C1–C5, 23.1) no `evo3`; medição 25.1 no android-05 (relatório em
  `data/rede/piloto/medicao-25.1.md`); Fase 26 em `docs/design/pedidos-persistentes.md`.
- **Ajuste de plano (25.5):** a sonda de saída mede por `nc` HTTP a um eco de IP e cobre por UID com `dumpsys
  netstats`, em vez de estender o app de QA, se a medição do 25.1 confirmar que basta. O motivo: a medição fica
  sem app novo a distribuir e a mesma em todos os aparelhos. O app de QA tem build no central (`qa-app/`, Gradle 9.7
  e JDK 21), e estendê-lo segue como alternativa se o `nc` do convidado não bastar.

## Próxima ação

1. Com o dono: P3 (Outlook e cliente VPN no android-11), P2 ou P1 (endpoint para o piloto de rede), P4 e P5.
2. Onda 0 pelo coordenador: contratos C1–C5 e o item 23.1.
3. Onda 1 pela esteira (`plano-100`), bloco a bloco, com os arquivos reservados acima.
