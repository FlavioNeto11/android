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

**Passkey feito em 30/09; o pedido de permissão do app Claude já estava atendido e o acesso foi restrito ao `android` (registro 3 abaixo).** Os outros três apps e os 12 OAuth apps ficam como estão.

Não recomendo, sem necessidade comprovada, revogar OAuth apps ou apps instalados; os "nunca usados" são higiene
opcional do dono, fora do escopo do projeto.

## 6. Registro de alterações (antes → depois)

| # | Alteração | Antes | Depois | Resultado |
|---|---|---|---|---|
| 1 | Execução manual do `Contêiner` (`workflow_dispatch`) | último run falhou (bloqueio de gasto), 29/09 | run `36715781075` verde | prova do bloqueio resolvido; custo ~2 min hospedados (< US$ 0,02) |

| 2 | Recarga da Anthropic registrada no livro-caixa da plataforma (`POST /api/ai/balances/anthropic/recharge {"amount": 20}`, 30/09 13:07Z, a pedido do dono, que recarregou US$ 20,00 no Console) | âncora de fechamento US$ 3,12, saldo estimado US$ 2,46 (`low`) | âncora `recarga`, saldo estimado US$ 22,46 (`ok`) | conferido em `GET /api/ai/balances`; OpenAI e Gemini inalterados; valor informado pelo dono, sem leitura do Console |
| 3 | Acesso do GitHub App **Claude** (instalação `136995446`) restrito ao repositório `android` (a pedido do dono, 30/09, após passkey) | "All repositories" (atuais e futuros da conta); permissões de leitura/escrita em actions, checks, code, discussions, issues, pull requests, hooks e workflows | "Only select repositories": só `FlavioNeto11/android`; permissões inalteradas | conferido relendo a página: `selected:true`, "Selected 1 repository". O pedido de permissão pendente já estava atendido ("already up to date"). Reversível na mesma tela |
| 4 | Orçamento de Actions (conta `FlavioNeto11`), aplicado **pelo dono** | US$ 100,00, "Stop usage: No" (só alerta) | **US$ 15,00, "Stop usage: Yes"** (US$ 10,01 gastos no ciclo) | conferido na tela em 30/09 ~17:50Z (commit 3b4ba0b às 17:51Z); alertas 75/90/100% mantidos. O ciclo reinicia em 01/10 |
| 5 | Regra de firewall do central (UDP 51820, LAN, Wi-Fi), aplicada **pelo dono** num PowerShell de administrador (D1 da Fase 29) | sem regra | regra ativa, conferida por leitura (`Get-NetFirewallRule`) e pelo `firewall-check` da plataforma (`liberado`) | a coordenação registra o W0 |
| 6 | Reserva DHCP no roteador ZTE F6600P (D2 da Fase 29), feita por mim no Chrome do dono logado por ele | sem reserva | `centralfrm` → MAC `ac:45:ef:2c:22:fc` (Wi-Fi do central) → `192.168.1.81` | persiste após recarregar; o central segue em 192.168.1.81; sem reinício do roteador; nenhuma outra entrada alterada |
| 7 | Consentimento das 3 contas Outlook (André, Bruno, Lucas) dado **pelo painel** no Chrome do dono, por delegação dele em chat (Persona › Contas e acesso › Outlook › "Autorizo a automação a digitar esta senha…" + "Autorizar sem redigitar"), sem digitar senha | `consent_at` vazio nas 3 | `consent_at` 17:55:50Z, 17:56:53Z e 17:57:29Z, `consent_by: Flavio` | conferido por `GET /api/instagram/profiles/{id}/accounts`; vínculo com aparelho é da coordenação |
| 8 | Rascunho do e-mail de teste do Lucas no Gmail do dono (assunto, corpo sem dígitos nem link; o dono digitou o assunto e enviou) | — | enviado pelo dono | o classificador barrou o preenchimento do assunto; cliques em janela minimizada abriram uma conversa e podem ter acionado atalhos do Gmail: sem dano visível (Caixa de entrada, estrela e contagens iguais); Gmail avisa que a conta para de enviar/receber em 19/10/2026 por armazenamento cheio |

Nada mais foi alterado. O teto de US$ 15 (registro 4) foi aplicado pelo dono depois que a edição do valor foi barrada duas vezes
pelo classificador de permissões da sessão desta frente.

## 7. Ações que dependem do dono

Nenhuma pendente desta frente: teto, firewall e DHCP foram aplicados e conferidos (registros 4 a 6). `gh auth refresh -s user` (leitura da API de cobrança por script) é opcional. As pendências da Fase 29 (servidores de saída, consentimento das contas Outlook, e-mail de teste) estão em `pendencias-evolucao3.md`.

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
