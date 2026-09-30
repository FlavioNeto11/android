# Checkpoint — pendências da terceira evolução (Fase 29)

Coordenação da correção das pendências que a terceira evolução deixou. Plano: Fase 29 de
[`../plano-100.md`](../plano-100.md), bloco `29-pendencias-evolucao3`. Insumo: a pesquisa de 30/09/2026 (relatório em
`reports/Pendências da terceira evolução Android.md` e seis notas em `research_notes/`, os dois **fora do Git**; o que
decide a execução está transcrito aqui). O handoff anterior, com o estado por frente das Fases 23–27 e as pendências
P1–P17, segue em [`terceira-evolucao.md`](terceira-evolucao.md).

**Coordenador:** uma sessão da IDE (a que escreve este arquivo). Ela é a única que integra, implanta e atualiza o
agente do notebook. Níveis de prova: `real`, `simulated`, `not_run` (`CLAUDE.md`).

## Base conferida (30/09/2026, 12:00Z)

| O quê | Valor | Como foi lido |
|---|---|---|
| `origin/main` no começo | `6997091` | `git rev-list --left-right --count main...origin/main` → `0 0` |
| Central | `6997091`, migração `058_chaves_de_rede`, `degraded` só por `ai_balance_low` | `GET /api/health` |
| Agente do notebook | conectado (`worker-lan-01`); versão `0.1.0+e26a469` | `GET /api/workers`, handoff anterior |
| Maior migração em qualquer branch | 058 (nenhum `059_`–`069_`) | `git for-each-ref` + `git ls-tree` em 123 refs |
| Sessões no repositório | nenhuma outra ativa (as demais estão offline ou são de outros projetos) | lista de sessões da IDE |
| Aparelhos ligados | android-01 (Lucas), 02 (QA), 03 (Bruno), 05 (QA), 06 (André) | `GET /api/instances` |
| Rede aplicada | android-02, 03, 06 em `trafego_verificado`; android-05 em `parcial` | `GET /api/network/devices` |

**Números desta frente.** Migração **063** (a 059–062 seguem nomeadas nas linhas 28.1, 28.2, 28.7 e 28.10 da Fase
28, que nenhuma sessão abriu; o migrador aplica todo arquivo que ainda não está em `schema_migrations`, em ordem de
nome, então uma 059 criada depois ainda é aplicada num banco que já tem a 063 — coberto por teste no 29.2). ADR
**061** em diante (o 060 é o que `design/pedidos-persistentes.md` §5 nomeia para a Fase 28). Aprendizado **K-064** em
diante.

## O que a conferência mudou em relação ao relatório

O relatório é insumo. Quatro pontos dele não se sustentaram ou estavam incompletos quando conferidos no código e no
ambiente:

| Ponto do relatório | O que a conferência mostrou | Consequência |
|---|---|---|
| "`npm audit fix` em `tools/appium` resolve o CI (15 min)" | O `brace-expansion` 5.0.9 vem **dentro do tarball** do driver (`inBundle`); `npm audit fix` dá `changed: 0`, `overrides` não alcança, e o lock editado deixa o audit verde com a 5.0.9 no disco | 29.1 troca o arquivo instalado (`postinstall`) e o CI confere o disco (K-064) |
| "*Backfill* por `UPDATE` na migração, com `leak_client` NULL valendo até a primeira leitura" | NULL como curinga atribuiria a prova a um cliente desconhecido | 29.4: adoção só com aparelho, revisão, configuração e cliente demonstrados (abaixo) |
| "Um reinício por teste de vazamento" | Em 30/09, 15 de 30 conferências depois do boot leram o aparelho **sem `tun0`** com 95–159 s de ligado, e cada uma pediu outro reinício: android-06 levou 6 reinícios e 2 reaplicações por um teste | 29.3 (item novo): sem ele, qualquer reinício legítimo continua virando cadeia |
| "android-05 em `parcial` é o P16" | O teste dele está **adiado** desde 06:28Z porque o objetivo `r-20260930023809-12d329:android-05` (a prova de "conta indisponível" de 30/09) ficou em `waiting_user`; a sonda roda a cada 11 min (34 medições) sem poder concluir | resolver o objetivo antes do deploy; 29.2 deixa de medir em laço quem só espera a prova |

## Estado por tarefa

Atualizado a cada checkpoint. "Responsável": C = coordenador; A = agente delegado (com o escopo da linha).

| Item | Liga a | Resultado esperado | Depende de | Resp. | Estado |
|---|---|---|---|---|---|
| 29.1 | CI | job `dependências` verde no commit publicado | — | C | **feito**, `real`: `9428a6a`, run 36713946044 verde (13:03Z) |
| 29.2 | P16 | reinício do backend com prova válida não reinicia aparelho | 063 livre | C | **integrado** (commit local do P16); `simulated` + leitura e ensaio `real`; revisão independente em curso |
| 29.3 | P16 | reinício que sobe sem túnel não vira cadeia de reinícios | medição em QA | C | a medir (android-05) |
| 29.4 | P16 (L11) | 6 h reais sem `restart` pedido por `rede`, com remedição | 29.2, 29.3, CI verde | C | não iniciado |
| 29.5 | UDP #6/#21 | medição diz perna, tentativas, bytes e tempo | 29.2 integrado (mesmo arquivo) | A | não iniciado |
| 29.6 | P1 | saída medida ≠ esperada vira `parcial` com o motivo | 29.2 integrado (`rede.py`) | A | não iniciado |
| 29.7 | P1 | duas saídas distintas medidas | **dono**: 2 servidores e chaves | C | bloqueado (externo) |
| 29.8 | 25.7 | comando e leitura do firewall por porta, interface, origem, perfil | — | A | **integrado** (no commit do P16, de `d59fd36`); `simulated` + leitura `real` no central |
| 29.9 | 25.7, 25.9 | aparelho do notebook em `trafego_verificado`, com recuperação | **dono**: regra e DHCP; 29.8 | C | bloqueado (externo) |
| 29.10 | P15 | renderizador escolhido por medição, no AVD e pelo serviço | CI fora do ar (carga) | C | não iniciado |
| 29.11 | P15 | renderizador por aparelho e por worker, persistente | 29.10 | C/A | não iniciado |
| 29.12 | 23.2, 23.7, 23.8, 23.12 | Outlook promovido, distribuído e reconhecido | 29.11 | C/A | não iniciado |
| 29.13 | 23.11, 23.13, 24.9, 27.2 | login e C1 provados | 29.12; **dono**: consentimento, saldo, e-mail de teste | C | bloqueado (externo) |
| 29.14 | P17 | suíte e migrações em PostgreSQL real | cron do CI de 01/10 05:17Z (o limite de gasto caiu em 30/09 12:10Z) | C | aguarda o cron; a sessão "Github" monitora e avisa |
| 29.15 | 25.8, 23.10 | painel sem as três ambiguidades; estados ausentes inspecionados | 29.2 integrado (`RedePage`) | A | não iniciado |
| 29.16 | 12.3, contagem | `check` sem interrupção; 12.3 no vocabulário; P15 reescrito | 29.10 para o P15 | C | índice regenerado neste commit |
| 29.17 | artefatos | espaço devolvido sem tocar o que está em uso | 29.10 (usa `diag-outlook`) | C | não iniciado |
| 29.18 | fechamento | relatório §27, CHANGELOG, estado pelo mecanismo | tudo acima | C | não iniciado |

## Frentes, arquivos e quem escreve

P16, saída esperada e UDP tocam arquivos vizinhos; por isso entram **em sequência**, não em paralelo.

| Frente | Itens | Arquivos (um dono por vez) | Ordem |
|---|---|---|---|
| CI | 29.1 | `tools/appium/*`, `.github/workflows/ci.yml` | feito |
| P16 | 29.2, 29.3 | `backend/migrations/063_*.sql`, `devices/rede.py`, `rede_convergencia.py`, `rede_aplicacao.py`, `rede_medicao.py`, `models.py` (DTO de rede), `frontend/src/api/types.ts`, `features/rede/RedePage.tsx`, `tests/test_rede_aplicacao.py`, `docs/banco.md`, `docs/dominios/parque.md` | 1º; só o coordenador |
| Firewall | 29.8 | `devices/rede_firewall.py`, `tests/test_rede_worker.py` | paralelo ao P16 (arquivos disjuntos) |
| Medição | 29.5 | `devices/sonda_rede.py`, o trecho de DNS/UDP de `rede_medicao.medir`, `tests/test_rede_sonda.py` | depois do P16 integrado |
| Saída esperada | 29.6 | `devices/rede.py` (`_params`, `_falta_para_verificar`, `registrar_medicao`, prévia do `atribuir`), DTO, `RedePage.tsx` | depois da Medição |
| Painel | 29.15 | `features/loja/AppsPage.tsx`, cartão de conta, `RedePage.tsx` (tabela) | depois da Saída esperada |
| Outlook | 29.10–29.12 | `devices/emulator.py`, `config.py`, `worker/settings.py`, `conhecimento/apps/com.microsoft.office.outlook/*`, `automation/hierarchy.py` | experimentos já; código depois do 1º deploy |

Contratos compartilhados (`models.py`, `api.py`, `frontend/src/api/types.ts`, `docs/api-contract.md`): só o
coordenador integra; o agente entrega o trecho no próprio worktree e diz o que tocou.

## Aparelhos reservados

Um trabalho pesado por vez no central (K-058): corrida do CI (o runner é o próprio central, ~40 min por push), suíte
inteira, Docker/WSL e emulador de diagnóstico **não** se sobrepõem.

| Aparelho | Conta real | Finalidade aqui | Estado antes | Como volta |
|---|---|---|---|---|
| `diag-outlook` (AVD fora do parque, porta 5700) | não | E3, E5, E6 (29.10) | parado | `adb -s emulator-5700 emu kill` |
| android-07 (ou 08) | não; parado; sem linha de rede | E4: GPU do host pelo serviço (29.10) | `stopped` | voltar o `gpu` anterior e `actions/stop` |
| android-05 (`emulator-5562`) | não (QA) | 29.3: boots medidos; primeiro teste de vazamento real com o código novo (29.4) | rede rev 1, `exigida_com_bloqueio`, `parcial` | fica com a rede aplicada |
| android-02 (`emulator-5556`) | não (QA) | controle da observação do P16 | `trafego_verificado` | — |
| android-03, android-06 | **sim** (Bruno, André) | só observação (29.4): nenhuma ação, nenhum experimento | `trafego_verificado` | — |
| android-01 | **sim** (Lucas) | nada | sem rede gerenciada | — |
| android-09 (notebook) | não (QA) | W0–W8 (29.9), depois da regra de firewall | `stopped` | desatribuir a rede, `stop` |
| android-11 (loja) | conta Google do dono | nada (a atualização do Outlook, E8, é opcional e do dono) | `stopped` | — |

## Contrato do P16 (29.2 a 29.4)

**Prova.** Seis colunas em `device_network`: `leak_rev`, `leak_client`, `leak_result` (1 bloqueou, 0 vazou, NULL
inconclusivo), `leak_at`, `leak_detail`, `leak_pending`. A prova **vale** quando `leak_rev = desired_rev`,
`leak_client` é o cliente VPN lido agora no aparelho e `leak_result = 1`. `leak_client` é a identidade da instalação
(versão e pasta de instalação do pacote, que muda a cada instalação ou atualização), lida na mesma ida em que se lê o
resto da rede.

**O que invalida.** Revisão nova (perfil, política com bloqueio, reaplicação pedida): a chave deixa de casar. Cliente
atualizado ou reinstalado: `leak_client` diferente. Wipe, reset ou outro aparelho atrás do id: o gancho `invalidar`
apaga as colunas. `POST …/verify`: apaga (é o pedido explícito de refazer). Reaplicar a **mesma** revisão depois de
uma falha não invalida: senão um teste que custa reinício viraria laço.

**O que não invalida.** O relógio. A validade de 6 h (`rede.validade_verificacao_s`) governa a medição barata (IP,
DNS, UDP, apps); a ausência do bloqueio no sistema (`always_on_vpn_lockdown`, "Lockdown filtering rules") já regride a
linha a cada conferência (`rede.deriva_s`).

**Ensaio interrompido.** `leak_pending = 1` é gravado **antes** do `force-stop`, com CAS por revisão; o desfecho o
zera. Quem encontra `leak_pending = 1` sem ensaio em curso (backend reiniciado no meio) registra "inconclusivo:
interrompido" e **não** para o cliente de novo; o túnel caído é tratado pelo caminho que já existe (deriva →
`configurado` → religar).

**Nunca aprova.** Resultado 0, NULL ou ausente leva a `parcial` com o motivo, e não é refeito sozinho: só por
`POST …/verify`, revisão nova ou cliente novo.

**Primeira implantação (29.4).** A prova de hoje só existe na memória do backend. A migração 063 só cria colunas:
ela não conhece o aparelho, e um `UPDATE` com o cliente vazio valendo "até a primeira leitura" seria um curinga. Não
adotar nada forçaria um teste destrutivo (e os reinícios do 29.3) em android-03 e android-06, que têm conta real — e
logo na primeira varredura, porque a porta passa a exigir a prova da linha. A adoção é feita pelo código, na primeira
verificação de cada aparelho, só com a correspondência demonstrada (`rede.prova_anterior` + a data do APK lida do
aparelho; descrição em `docs/dominios/parque.md`, "A prova de vazamento").

Ensaio `real` de 30/09 12:57Z: a 063 aplicada numa cópia do banco do central (sem divergência) e a decisão de adoção
calculada com os aparelhos lidos como uid 2000, só leitura:

| Aparelho | Teste mais antigo da sequência provada (comando `verificar`) | Última medição | APK do cliente gravado em (`stat -c %Y`) | Decisão |
|---|---|---|---|---|
| android-02 | 30/09 01:22:29Z (`c-20260930012229-187ca4`) | #43, bloqueio provado | 30/09 01:07:36Z | **adota** |
| android-06 | 30/09 01:38:43Z (`c-20260930013843-6b8340`) | #14, bloqueio provado | 30/09 01:32:45Z | **adota** |
| android-03 | 30/09 02:29:45Z (`c-20260930022945-fd0d76`) | #18, bloqueio provado | 30/09 01:48:46Z | **adota** |
| android-05 | — (a última medição não provou: o teste está adiado desde 06:28Z) | #40+, sem prova | 29/09 22:28:44Z | **não adota** |

O android-05 (QA, sem conta) faz o primeiro teste real com o código novo: é a prova real do caminho de gravação
(intenção, desfecho, reinício). **Antes do deploy:** repetir o ensaio (o estado pode ter mudado) e resolver o objetivo
preso do android-05.

**Reversão.** Voltar ao código de `6997091` reintroduz o defeito (a prova volta a viver só na memória e o próximo
reinício arma os testes). As colunas novas ficam no banco e o código antigo as ignora: isso torna a volta possível,
não segura. A reversão operacional é corrigir para a frente; se for preciso voltar, fazê-lo com os quatro aparelhos
ociosos e avisar que os testes serão refeitos.

## Ordem de integração e de implantação

1. **29.1** publicado sozinho (`9428a6a`). O CI roda no próprio central: nada pesado enquanto ele roda.
2. **29.2 + 29.3** no mesmo commit funcional, com os testes que reproduzem o defeito antes da correção.
3. Suíte inteira do backend uma vez (segundo plano, prioridade ociosa), `npm run typecheck && npm test`.
4. Push; esperar o CI; `deploy.ps1 -Ensaio` e a migração numa cópia do banco; **primeiro deploy** com os quatro
   aparelhos ociosos (29.4). Início da janela de 6 h registrado aqui.
5. Durante a janela: só trabalho que não reinicia o backend nem toca os aparelhos observados (experimentos do Outlook
   no `diag-outlook` e no android-07; código de 29.5, 29.6, 29.8, 29.15 em worktree).
6. Fim da janela: segundo deploy com 29.5, 29.6, 29.8, 29.15 e o renderizador (29.11); depois 29.12.
7. 29.7, 29.9, 29.13 e 29.14 quando a dependência externa de cada um chegar.

## Pedidos ao dono

| # | Ação | Onde | Desbloqueia | Custo |
|---|---|---|---|---|
| D1 | Regra do firewall (o comando sai do 29.8, conferido) num PowerShell de administrador | central | 29.9 (25.7, 25.9) | 0 |
| D2 | Reserva DHCP do central (MAC da Wi-Fi → `192.168.1.81`) | roteador | 29.9 estável | 0 |
| D3 | Dois servidores pequenos com IPv4 próprio e as chaves dos clientes | provedor à escolha | 29.7 (P1) | ~US$ 14/mês (referência da pesquisa; conferir antes) |
| D4 | Consentimento das 3 contas Outlook (Persona › Contas), **depois** do 29.12 | painel | 29.13 (23.13) | 0 |
| D5 | Recarga da conta Anthropic e autorização das provas pagas | console do provedor | 29.13 (23.13, 24.9, 27.2) | US$ 2,8–4,5 estimados |
| D6 | E-mail de teste com assunto inofensivo para a caixa da persona escolhida | conta do dono | 29.13 (C1) | 0 |
| D7 | Autorizar Docker/WSL no central, ou esperar o job do CI | central | 29.14 (P17) | 0 |
| D8 | Perfil Public do firewall do **notebook** está desligado (achado, fora do escopo) | notebook | segurança do notebook | 0 |

## Checkpoints

### Checkpoint 2 — 30/09 ~13:15Z

- 29.1 **feito** (`real`): run 36713946044 verde às 13:03Z no commit `9428a6a`.
- 29.2 e 29.8 num commit local (`claude/evolucao3`, sobre `d8bcbf4`), **ainda não publicado**: falta a revisão
  independente (em curso), o 29.3 e a suíte inteira. Testes de rede e de banco: verdes (SQLite). Painel: `tsc` e
  vitest 834/834.
- 29.3: medição em curso no android-05 (boots e gestos sem reinício); o objetivo `r-20260930023809-12d329:android-05`
  segue em `waiting_user` de propósito até a medição acabar (é o que impede a plataforma de reiniciar o aparelho).
- E7 (29.10) **executado**, `real`, só leitura: o único dump do emulador no notebook
  (`qemu-system-x86_64-headless.exe.16644.dmp`, 29/09 19:45Z, canário do Outlook no android-09) tem a mesma assinatura
  do central — `0xc0000005` de leitura em código sem módulo, com `gles_swiftshader\libGLESv2.dll` na pilha.
- Remedições de 12:33Z (android-02) e 12:57Z (android-06) saíram **sem** teste destrutivo: a memória do backend atual
  ainda tem a prova. O defeito volta no próximo reinício do backend com o código antigo; com o novo, a adoção o evita.
- Outra sessão ("Github") cuida de Actions e cota: só empurra docs com `[skip ci]`, não implanta, e monitora o cron
  de PostgreSQL de 01/10. O dono recarregou a Anthropic (US$ 22,46 estimados às 13:07Z): o saldo deixou de ser
  bloqueio; a autorização de chamada paga continua sendo a de 29/09 (até US$ 1,50, ~US$ 0,56 usados).
- `deploy.ps1` ganhou a etapa das dependências do Appium (o `node_modules` do central ainda tem a 5.0.9).

### Checkpoint 1 — 30/09 ~12:30Z

- `9428a6a` na `main`: 29.1. Run do CI 36713946044 em curso.
- Fase 29 registrada (este commit). Nenhum deploy. Nenhum aparelho tocado além de leituras como uid 2000.
- Em implementação no worktree `evo3` (branch `claude/evolucao3`): 29.2.

## Próxima ação

1. Aplicar os achados da revisão do P16 e o 29.3 (com o que a medição do android-05 mostrar).
2. Suíte inteira do backend uma vez, push, esperar o CI.
3. Resolver o objetivo `r-20260930023809-12d329:android-05`, repetir o ensaio da 063 e da adoção, e fazer o primeiro
   deploy (29.4) com os aparelhos ociosos; registrar aqui o início da janela de 6 h.
