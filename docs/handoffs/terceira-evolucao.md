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

## Registro da integração

Vazio. Cada lote integrado entra aqui com o SHA, a frente, o que entrou e a prova (`simulated`, `real` ou `not_run`).

## Próxima ação

1. Com o dono: P3 (Outlook e cliente VPN no android-11), P2 ou P1 (endpoint para o piloto de rede), P4 e P5.
2. Onda 0 pelo coordenador: contratos C1–C5 e o item 23.1.
3. Onda 1 pela esteira (`plano-100`), bloco a bloco, com os arquivos reservados acima.
