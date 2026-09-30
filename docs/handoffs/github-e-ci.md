# Handoff — GitHub: acessos, Actions, runners, cota e custo (30/09/2026)

Frente paralela de configuração do GitHub (conta `FlavioNeto11`, repositório `FlavioNeto11/android`). Feita pela
interface do Chrome do dono e por leitura da API (`gh api`). **Nenhuma configuração do GitHub foi alterada e nenhum
arquivo de workflow foi tocado.** Único efeito: uma execução manual do workflow `Contêiner` (run `36715781075`).

Coordenação: `docs/handoffs/terceira-evolucao.md` continua da outra frente (CI, P16, Outlook, rede). Este arquivo é
só desta frente; ninguém mais o escreve. Qualquer mudança de `.github/workflows/*.yml`, do runner ou de deploy fica
com a frente de implementação.

## 1. O bloqueio do Actions era o limite de gasto, e já caiu

| Pergunta | Resposta | Prova |
|---|---|---|
| Era falha de pagamento? | **Não.** Cartão válido, sem cobrança pendente | tela Payment information, 30/09 |
| Era cota incluída acabada? | Só em parte: as 3.000 min do Pro acabaram e o gasto adicional bateu no teto de **US$ 10** | cobrado exatamente US$ 10,00 em 26–27/09, zero depois |
| Mensagem do GitHub | "The job was not started because recent account payments have failed or your spending limit needs to be increased" | anotação do job PostgreSQL de 30/09 05:29Z e dos runs de Contêiner desde 28/09 |
| Quem mudou o teto | o dono, 30/09 12:10Z: US$ 10 → **US$ 100, sem "Stop usage"** | security log, evento `billing.budget_update` |
| Bloqueio resolvido? | **Sim, provado.** Run `36715781075` (`workflow_dispatch`, Contêiner, 30/09 12:35Z, commit `main`) iniciou em runner hospedado e passou em todos os passos | `gh run view 36715781075`: `conclusion: success` |

Os três níveis, sem misturar:

1. **Bloqueio administrativo resolvido**: `real` (run acima).
2. **Job iniciou em runner hospedado**: `real` (mesmo run).
3. **Testes do job PostgreSQL passaram**: **`not_run`**. O job `backend-postgres` só roda por `schedule`
   (05:17Z) e `workflow_dispatch` do `CI`; o `dispatch` do `CI` inteiro roda o SQLite no runner do central (~35 min),
   então não foi disparado. A próxima prova é o cron de **01/10 05:17Z**, já com a cota do ciclo novo.

O banner "You've used 100% of your Actions budget. Additional usage will be stopped" ainda aparece na Overview.
Está defasado em relação ao orçamento (a tela de Budgets mostra `Stop usage: No`, US$ 10,00 gasto de US$ 100,00) e a
execução hospedada já provou que nada está parando. Se persistir em 01/10, é só o banner.

## 2. Cota, consumo e custo (ciclo 01–30/09/2026)

Plano: **GitHub Pro, US$ 4,00/mês** (3.000 min de Actions e 2 GB de armazenamento incluídos, repositórios privados).
Copilot Free, US$ 0. Metered bruto US$ 71,85; desconto de uso incluído US$ 61,85; **cobrado US$ 10,00**.

| Componente do desconto | US$ |
|---|---|
| 3.000 min de Actions incluídos | 18,00 |
| Armazenamento de Actions (2 GB) | < 0,01 |
| **"Free usage" (100% de desconto)** | **43,85** |

O "Free usage" é o uso do `devops`, que é **repositório público** (US$ 39,00 brutos, todos descontados). Ele **não**
consome a cota dos privados. Correção da leitura provisória de antes: a cota de 3.000 min foi consumida por
`android` (bruto US$ 26,93) e `device-farmer` (US$ 4,85, parado desde 07/09), e a diferença até US$ 28,00 brutos
privados foi cobrada: **US$ 10,00**.

Minutos hospedados do `android` (soma dos jobs, arredondados para cima; script de leitura, sem gravar nada):
5.108 min no mês, **3.446 min em 26–27/09**, 233 min em 28/09, 26 em 29/09, 4 em 30/09. Depois de 28/09 o CI roda no
runner próprio e o gasto praticamente parou.

**Regime estável esperado (outubro):** só o job `backend-postgres` diário (~28 min por corrida, ~840 min/mês) e o
`Contêiner` (semanal, ~2 min, e por mudança de `deploy/**`). Cerca de 850 min/mês, dentro dos 3.000 incluídos:
**custo adicional esperado US$ 0** além dos US$ 4 do Pro.

### Preços oficiais usados (conferidos em 30/09/2026)

| Item | Valor |
|---|---|
| Pro, privados | 3.000 min/mês incluídos |
| Linux 2 núcleos hospedado | US$ 0,006/min (redução de até 39% em 01/01/2026) |
| Runner próprio | grátis; a taxa de plataforma de US$ 0,002/min anunciada para 01/03/2026 foi **adiada, não cancelada** |
| Label `ubuntu-latest` | passa ao Ubuntu 26 a partir de 19/10/2026 (afeta `backend-postgres` e `Contêiner`) |

## 3. Proposta de custo (só proposta; nada foi aplicado)

**Recomendação: manter o Pro e não mudar de plano.** Team exige organização, Enterprise é desproporcional, e o
consumo medido cabe na cota incluída.

Achado que pede decisão do dono: o orçamento de Actions está de US$ 100 **sem** "Stop usage" (só alerta em 75/90/100%).
Isso **não limita gasto**: se `CI_RUNS_ON` for apagada, ou o cron do PostgreSQL dobrar, o gasto passa de US$ 100 sem
parar. O teto anterior (US$ 10) bloqueava de verdade, e foi ele que travou o CI.

| Campo | Proposta |
|---|---|
| O quê | Trocar o orçamento de Actions (conta `FlavioNeto11`) para **US$ 15/mês com "Stop usage" ligado** |
| Moeda e recorrência | USD, mensal, reinicia no dia 1 |
| Benefício | teto real de gasto, folga de 3× o esperado (esperado: US$ 0 de adicional; folga cobre um mês de CI hospedado parcial) |
| Alertas | manter 75/90/100% |
| Risco | se atingir US$ 15, o job hospedado (PostgreSQL, Contêiner) para de novo. O runner próprio não é afetado |
| Situação | **aguarda aprovação específica do dono**; não alterei o valor atual de US$ 100 |

Os demais orçamentos (Codespaces, Packages, LFS, Models, AI Credits, Copilot) já têm "Stop usage" e uso zero; ficam
como estão. O saldo da API Anthropic é separado e não passa por aqui.

## 4. Runners

| Item | Estado em 30/09 12:37Z |
|---|---|
| Runner `central` (id 21) | online, `busy`, labels `self-hosted, Windows, X64, central`, versão **2.337.0** (é a última release) |
| Variável `CI_RUNS_ON` | `["self-hosted","central"]` |
| Jobs no runner próprio | backend-sqlite, frontend, dependências, backend-tipos, worker-agent-smoke, docs |
| Jobs sempre hospedados | `backend-postgres` (contêiner de serviço), `Contêiner` (Docker) |
| Fila | um único runner: o run `36713946044` (commit `9428a6a`, outra sessão) está em `backend · pytest (SQLite)`; os demais jobs dele esperam na fila. **Não foi cancelado nem disputado.** |

Condições para o PostgreSQL passar a rodar sem Docker no runner próprio (proposta, a decidir pela frente de
implementação): PostgreSQL local no central (serviço ou binários), porta e banco isolados do banco do ambiente
central, e o job com prioridade ociosa (K-058). Enquanto isso o hospedado custa ~US$ 0 dentro da cota.

## 5. Acessos e integrações (leitura)

| Item | Achado |
|---|---|
| Conta | `FlavioNeto11`, plano Pro, dono e responsável pela cobrança do repositório; passkey/2FA ativo |
| Repositório | privado, admin único (`FlavioNeto11`); sem colaboradores, sem webhooks, sem deploy keys, sem ambientes |
| Variáveis e segredos do repositório | variável `CI_RUNS_ON`; **nenhum secret** |
| Permissão padrão do `GITHUB_TOKEN` | somente leitura; Actions não aprova PR |
| Proteção da `main` | nenhuma (a convenção do dono é commit direto na `main`; não alterei) |
| Caches / artefatos | 32 caches, 347 MB (cache não é cobrado); 0 artefatos |
| GitHub Apps instaladas | ChatGPT Codex Connector, **Claude**, Claude Design Import, Netlify |
| OAuth apps autorizados | 12 (Cloudflare, fal.ai, Galileo, GitHub Android/CLI/iOS, Gitkraken, LangChain, Netlify Auth, Postman, Visual Studio, VS Code); vários "nunca usados" |

**Pendente de sudo (passkey do dono):** o app **Claude** tem um "Permission updates requested" (instalação
`136995446`). O GitHub pediu reautenticação (passkey) para abrir a tela de revisão e eu parei ali. Não aprovei nem
recusei nada, e não sei ainda o que ele pede nem em quais repositórios está instalado. A configuração dos outros
apps (escopo por repositório) também fica atrás do sudo.

Não recomendo, sem necessidade comprovada, revogar OAuth apps ou apps instalados; os "nunca usados" são higiene
opcional do dono, fora do escopo do projeto.

## 6. Registro de alterações (antes → depois)

| # | Alteração | Antes | Depois | Resultado |
|---|---|---|---|---|
| 1 | Execução manual do `Contêiner` (`workflow_dispatch`) | último run falhou (bloqueio de gasto), 29/09 | run `36715781075` verde | prova do bloqueio resolvido; custo ~2 min hospedados (< US$ 0,02) |

| 2 | Recarga da Anthropic registrada no livro-caixa da plataforma (`POST /api/ai/balances/anthropic/recharge {"amount": 20}`, 30/09 13:07Z, a pedido do dono, que recarregou US$ 20,00 no Console) | âncora de fechamento US$ 3,12, saldo estimado US$ 2,46 (`low`) | âncora `recarga`, saldo estimado US$ 22,46 (`ok`) | conferido em `GET /api/ai/balances`; OpenAI e Gemini inalterados; valor informado pelo dono, sem leitura do Console |

Nada mais foi alterado. O teto de US$ 15 com "Stop usage" segue **não aplicado**: a edição do valor foi barrada pelo
classificador de permissões desta sessão e ficou para o dono aplicar (ou liberar).

## 7. Ações que dependem do dono

1. **Decidir a proposta da seção 3** (teto de US$ 15 com "Stop usage") ou manter US$ 100 só com alerta.
2. **Passkey** para entrar em sudo mode no GitHub, e então revisar o "Permission updates requested" do app Claude
   (a tela já está a um passo: `https://github.com/settings/installations/136995446/permissions/update`).
3. Nada mais. `gh auth refresh -s user` (leitura da API de cobrança por script) é opcional.

## 8. Próximas verificações (quando a corrida sair)

- 01/10 05:17Z: cron do `CI` com `backend-postgres`. Se iniciar em `ubuntu-latest` e passar, o item 3 do §1 vira `real`.
- Fim de outubro: conferir se o gasto adicional ficou em US$ 0 (Billing → Usage, agrupado por repositório).

## 9. Proposta registrada: custo de tempo da suíte local (a decidir depois da janela do P16)

Medido em 30/09 por leitura, sem carga: a suíte inteira do backend em prioridade ociosa (K-058) levou ~33 min de
relógio para ~13 min de CPU (documentado: ~11 min sem restrição), é serial (241 arquivos, ~2.900 funções, sem
`xdist`) e cada commit de código pagava duas vezes (local + CI do runner, ~35–40 min). A coordenação adotou, para a
Fase 29: suíte inteira local **uma vez** (P16: 3860 passed, 1 failed, o `test_backup` de ambiente, sem `config.yaml`
no worktree, 34 min), depois só os arquivos afetados + `tests/test_arquitetura.py` + `tests/test_pacote_do_agente.py`,
com o CI do runner como a suíte inteira oficial antes do deploy.

**Pendente, só se o dono quiser (depois da janela de observação de 6 h do P16, com o central ocioso):** medir
`pytest --durations=50` para achar os testes lentos e avaliar `pytest-xdist` (porta base 5640, banco e arquivos
compartilhados podem não ser seguros em paralelo). Nada disso foi executado. Quem cita no fechamento da Fase 29 é a
coordenação.
