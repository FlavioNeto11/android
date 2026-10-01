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
| 29.2 | P16 | reinício do backend com prova válida não reinicia aparelho | 063 livre | C | **implantado** `0d70882`; `real` na subida (adoção em 02, 03 e 06; teste e gravação no 05); as 6 h são o 29.4 |
| 29.3 | P16 | reinício que sobe sem túnel não vira cadeia de reinícios | medição em QA | C | **implantado**; `real` pela convergência no android-05 (túnel religado pelo tile depois do teste, sem reinício); boot falho pela convergência: a observar |
| 29.4 | P16 (L11) | 6 h reais sem `restart` pedido por `rede`, com remedição | 29.2, 29.3, CI verde | C | **feito**, `real`: 15:24:44–21:30Z sem reinício do backend; remedição 20:51–20:52Z (#62–#65) com `leak_blocked=1` da linha; 0 teste e 0 reinício pela rede em 02, 03 e 06 |
| 29.5 | UDP #6/#21 | medição diz perna, tentativas, bytes e tempo | 29.2 integrado (mesmo arquivo) | A | **implantado**, `real` (medições #58 a #61 com o detalhe por perna) |
| 29.6 | P1 | saída medida ≠ esperada vira `parcial` com o motivo | 29.2 integrado (`rede.py`) | A | **implantado**, `simulated`; o real é o 29.7 |
| 29.7 | P1 | duas saídas distintas medidas | **dono**: 2 servidores e chaves | C | **adiado por decisão do dono** (30/09 ~18:15Z: "pode ser feita depois"); roteiro (bloco D3) e script prontos |
| 29.8 | 25.7 | comando e leitura do firewall por porta, interface, origem, perfil | — | A | **implantado**, `simulated` + leitura `real` no central |
| 29.9 | 25.7, 25.9 | aparelho do notebook em `trafego_verificado`, com recuperação | **dono**: regra e DHCP; 29.8 | C | W0–W3 `real`; D1, D2 e D8 feitos; **W4: a causa era código, corrigida em `658e5bb` (checkpoint 12)**; W2–W7 `real` no android-09; **W8: o túnel não voltou depois do teste de vazamento** (a hipótese de memória caiu, K-067) |
| 29.10 | P15 | renderizador escolhido por medição, no AVD e pelo serviço | CI fora do ar (carga) | C | **feito**, `real`: E3 (`skiavk` refutado), E4 no central e no notebook (GPU do host pelo serviço: o Outlook abre e fica estável), E7 |
| 29.11 | P15 | renderizador por aparelho e por worker, persistente | 29.10 | C/A | **implantado**, `real`: renderizador lido pela API; recusa real em 01/03/06 antes do reinício; parque ligado todo em `host` |
| 29.12 | 23.2, 23.7, 23.8, 23.12 | Outlook promovido, distribuído e reconhecido | 29.11 | C/A | **feito**, `real`: promovido 21:42Z; `ready` em 01, 02, 03, 06, 07, 08, 10, 12; 05, 09, 13–15 instalam ao ligar |
| 29.13 | 23.11, 23.13, 24.9, 27.2 | login e C1 provados | 29.12; **dono**: consentimento, saldo, e-mail de teste | C | **Outlook das 3 contas logado** no aparelho do Instagram de cada uma (01, 03, 06); **C1 real** no 01 (`r-20260930230500-f52eec`); falta o C1 num aparelho com rede verificada (e-mail de teste para Bruno ou André) |
| 29.14 | P17 | suíte e migrações em PostgreSQL real | cron do CI de 01/10 05:17Z (o limite de gasto caiu em 30/09 12:10Z) | C | **feito**, `real`: run 36830963968 (01/10, 7c3b787) 3962 passed, 0 failed em PostgreSQL |
| 29.15 | 25.8, 23.10 | painel sem as três ambiguidades; estados ausentes inspecionados | 29.2 integrado (`RedePage`) | A | **implantado**; inspeção `real` a 800 e 375 px com o dado do central; estados que o dado real não mostra: só jsdom |
| 29.16 | 12.3, contagem | `check` sem interrupção; 12.3 no vocabulário; P15 reescrito | 29.10 para o P15 | C | índice regenerado neste commit |
| 29.17 | artefatos | espaço devolvido sem tocar o que está em uso | 29.10 (usa `diag-outlook`) | C | arquivado (movido, 11,3 GB, nada apagado); restam os worktrees das frentes, depois do segundo deploy |
| 29.18 | fechamento | relatório §27, CHANGELOG, estado pelo mecanismo | tudo acima | C | não iniciado |
| 29.19 | P1 (objetivo de 30/09) | cada aparelho ativo com um IPv4 de saída só dele, medido e estável | 29.7 (V1); **dono**: escala e IPv4 | C | **adiado por decisão do dono** (30/09 ~18:15Z), junto com o 29.7; script com N pares pronto |
| 29.20 | P1 | aparelho saindo pela casa acusado pela plataforma | 29.6 | C | não iniciado; segue o 29.19 (adiado) |

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
| android-07 | passa a ter (Outlook do Lucas) | Outlook com GPU do host (D9); canário já aprovado aqui | `stopped`, `gpu_mode: host` | desvincular a conta e desinstalar |
| android-10, android-12 (notebook) | passam a ter (Outlook do Bruno e do André) | Outlook com GPU do host (D9; o `worker.yaml` já é `host`) | `stopped` | desvincular e desinstalar |
| android-02, android-08 | não (QA) | piloto V1 (29.7), depois da janela e do D3 | 02 com a rede do central; 08 sem linha | voltar ao perfil do central (02) ou desatribuir (08) |
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

## Checklist do dono (30/09, em ordem)

A janela do P16 vai até 21:30Z (18:30 em Brasília). Nada abaixo reinicia o backend nem toca android-02, 03, 05 e 06.

| # | Quando | O quê | Onde | Custo | Libera |
|---|---|---|---|---|---|
| 1 | ~~já~~ **feito 17:20Z** | Regra de firewall (bloco D1 abaixo), num PowerShell **de administrador** | central | 0 | W0 `real` (`liberado`); W1–W8 depois das 21:30Z |
| 2 | ~~já~~ **feito ~18:05Z** | Reserva DHCP do central no **mesmo** `192.168.1.81` (MAC da Wi-Fi); sem reinício do roteador | roteador | 0 | 29.9 estável |
| 3 | ~~já~~ **feito 17:55–17:57Z** | Consentimento nos 3 cartões do Outlook (Lucas, Bruno, André) | painel › Persona › Contas | 0 | login (29.13) depois do segundo deploy |
| 4 | ~~quando quiser~~ **adiado pelo dono** (~18:15Z) | Servidores de saída própria (bloco D3 e 29.19): roteiro e script prontos para quando ele quiser | provedor + painel | 0 a US$ 65/mês, conforme a escala | 29.7, 29.19 |
| 5 | ~~depois do 3~~ **enviado pelo dono** (~18:15Z) | E-mail de teste para o Outlook do Lucas (texto no bloco D9) | conta do dono | 0 | C1 (29.13); a IDE confere a chegada |
| 6 | ~22:00Z em diante | Ficar à mão para um desafio da Microsoft no login | celular do dono | 0 | 23.13 |

Depois das 21:30Z a IDE registra o 29.4, faz o segundo deploy (29.11 e agente do notebook), promove e distribui o
Outlook (07, 10, 12), vincula as contas e, com o que estiver pronto, roda W0–W8 e V1 — um trabalho pesado por vez.

**Processo (decisão do dono de 30/09 ~18:35Z, transmitida pela sessão "Github"):** o CI deixa de bloquear
enquanto o ambiente não for produção. Código vai à `main` com `[skip ci]`, validado pela suíte local dos arquivos
afetados; o deploy não espera o CI e mantém ensaio e backup; o cron diário (05:17Z) segue como rede de segurança
(`docs/operacao.md` §5).

## Pedidos ao dono

| # | Ação | Onde | Desbloqueia | Custo |
|---|---|---|---|---|
| D1 | Regra do firewall (o comando sai do 29.8, conferido) num PowerShell de administrador | central | 29.9 (25.7, 25.9) | 0 |
| D2 | Reserva DHCP do central (MAC da Wi-Fi → `192.168.1.81`) | roteador | 29.9 estável | 0 |
| D3 | Dois servidores pequenos com IPv4 próprio e as chaves dos clientes | provedor à escolha | 29.7 (P1) | ~US$ 14/mês (referência da pesquisa; conferir antes) |
| D4 | Consentimento das 3 contas Outlook (Persona › Contas), **depois** do 29.12 | painel | 29.13 (23.13) | 0 |
| D5 | ~~Recarga da conta Anthropic~~ **feita** em 30/09 (saldo estimado US$ 22,46); vale a autorização de 29/09 (até US$ 1,50, usados ~US$ 0,56) | — | — | — |
| D6 | E-mail de teste com assunto inofensivo para a caixa da persona escolhida | conta do dono | 29.13 (C1) | 0 |
| D7 | ~~Docker/WSL~~ **resolvido pelo CI**: o cron de 01/10 05:17Z roda a suíte em PostgreSQL | — | 29.14 | 0 |
| D8 | ~~Perfil Public do firewall do notebook desligado~~ **feito pelo dono em 01/10** (`Set-NetFirewallProfile -Profile Public -Enabled True` no notebook e no central); conferido por leitura: Public `Enabled=True` nos dois, regra UDP 51820 do central `liberado`, worker conectado | — | — | 0 |
| D9 | **Decidido pelo dono em 30/09 (~17:00Z, transmitido pela sessão "Github")**: a alternativa conservadora. O `gpu_mode` de android-01, 03 e 06 (contas reais) **não muda**; a conta Outlook de cada persona (as mesmas contas do Instagram: Lucas, Bruno, André) roda num aparelho de QA com GPU do host. Consequências abaixo, em "D4, D6 e D9" | — | 29.12, 29.13 | 0 |

## Entregas prontas, à espera da ação do dono

Cada bloco diz a ação exata, o que ela libera e o roteiro que a IDE executa em seguida. Tudo aqui é `not_run` até a
ação acontecer.

### D1 e D2 → aparelho do notebook com rede (29.9; fecha 25.7 e o resto do 25.9)

**Ação do dono** (PowerShell de administrador no central; a plataforma só lê o firewall). O comando abaixo é o que a
plataforma gera hoje com os valores lidos do sistema (`POST /api/network/server/firewall-check`, 30/09 16:10Z: estado
`sem_regra`, perfil Public, interface Wi-Fi, sub-rede 192.168.1.0/24):

```powershell
$n='Central de Aparelhos - rede por aparelho (WireGuard UDP 51820)'; Get-NetFirewallRule -DisplayName $n -EA 0 | Remove-NetFirewallRule; New-NetFirewallRule -DisplayName $n -Direction Inbound -Action Allow -Protocol UDP -LocalPort 51820 -RemoteAddress 192.168.1.0/24 -InterfaceAlias 'Wi-Fi' -Profile Any
```

Conferir: o comando de inspeção que a mesma rota devolve (`inspect_command`). Desfazer:
`Remove-NetFirewallRule -DisplayName 'Central de Aparelhos - rede por aparelho (WireGuard UDP 51820)'`. E, no roteador,
a reserva DHCP do central (o endereço da Wi-Fi é o `rede.servidor.endpoint_lan`; se ele mudar, os aparelhos remotos com
bloqueio ficam sem rede até um Reaplicar).

**Roteiro da IDE (W0–W8), numa janela sem tarefa** (cadastrar o par reinicia o servidor VPN do central e derruba por
segundos o túnel de android-02, 03, 05 e 06; com a prova de vazamento na linha, isso não refaz teste nenhum):

| Passo | O quê | Evidência esperada |
|---|---|---|
| W0 | `POST /api/network/server/firewall-check` | `liberado`, sem aviso de origem `Any` |
| W1 | ligar o android-09 (QA, notebook) | `online` |
| W2 | `POST /api/network/assign` com `instance_ids: ["android-09"]`, o perfil do central e `policy: exigida` | linha `pendente`, rev 1 |
| W3 | provisão (a convergência usa `adb reverse` pelo túnel) | `configurado`; perfil importado |
| W4 | reinício pedido pela rede; túnel | `conectado`; no log do servidor, `inbound connection from 10.66.0.N` do par remoto |
| W5 | medição | `trafego_verificado`; linha em `network_measurements` com IP, DNS e UDP por perna |
| W6 | queda e volta: modo avião no convidado por 1 min | deriva → `configurado` → tile ou reinício → `conectado` de novo, sem reprovisão |
| W7 | reinício do agente do notebook | o aparelho segue com a rede; o central relê |
| W8 | só então `exigida_com_bloqueio` (teste de vazamento real no remoto) e, no fim, desatribuir (rollback) | prova na linha; depois a linha sai |

### D3 → piloto de saída distinta (29.7; fecha o P1 no que é piloto)

**Provedor sugerido** (conferido em 30/09/2026 na documentação oficial): Amazon Lightsail, região São Paulo
(`sa-east-1`), plano "Nano-0.5GB Linux with public IPv4" a **US$ 5,00/mês** cada, cobrado por hora até o teto mensal
([bundles](https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-bundles.html),
[regiões](https://docs.aws.amazon.com/lightsail/latest/userguide/understanding-regions-and-availability-zones-in-amazon-lightsail.html)).
Dois servidores: **US$ 10/mês**; apagar o servidor encerra a cobrança. Outro provedor serve igual (o script é Linux
comum); a Vultr não abriu a página de preços para leitura (403), e as fontes de terceiros citam US$ 5/mês com IPv4.
Nada é contratado pela IDE.

**Caminho gratuito** (achado da sessão "Github" em 30/09, documentação oficial da Oracle): o Always Free da Oracle
Cloud dá até duas VMs AMD Micro (`VM.Standard.E2.1.Micro`, 1/8 de OCPU, 1 GB, um IP público cada, 50 Mbps) na região
de origem da conta. Riscos: a Oracle recupera instância ociosa (CPU e rede abaixo de 20% por 7 dias) e há relatos de
falta de capacidade em São Paulo e Vinhedo. Para o piloto V1 (aparelhos de QA, sem conta) serve, e o país da saída
não importa; para conta real, a saída deveria ser do Brasil e estável — aí o Lightsail (IP estático) é o caminho.
Na Oracle: imagem **Canonical Ubuntu 24.04** (o script também aceita Oracle Linux 8/9), o script em "cloud-init
script", e na Security List da sub-rede uma regra de entrada stateful **UDP 51820** de `0.0.0.0/0`. O script desliga
o `iptables` que a imagem Ubuntu da Oracle carrega no boot (o REJECT final dele derrubaria o túnel) e guarda as regras
ao lado. O IP público efêmero serve para o piloto; se a instância for recriada, o perfil muda.

**Ação do dono, por servidor (A e B):**

1. No central, num terminal: `C:\git\android\data\rede\sing-box-1.14.2-windows-amd64\sing-box.exe generate wg-keypair`.
   Sai `PrivateKey` e `PublicKey`. A privada só vai ao formulário do painel (passo 5); não a cole em chat, arquivo
   ou chamado.
2. No Lightsail: criar a instância (Linux, Ubuntu 24.04, São Paulo, Nano com IPv4). Em "launch script", colar
   [`scripts/rede-saida-externa.sh`](../../scripts/rede-saida-externa.sh) com a `PublicKey` do passo 1 em
   `CHAVE_PUBLICA_DO_CLIENTE`.
3. Na aba **Networking** da instância (Lightsail; na Oracle, a Security List acima): anexar um **IP estático** (gratuito enquanto anexado; sem ele o IP muda ao
   parar e ligar); na lista de portas IPv4, **acrescentar UDP 51820** e tirar a HTTP 80. O firewall do Lightsail fica
   na frente do servidor: sem a regra UDP, o túnel não chega.
4. Ler a chave pública do servidor: `sudo wg show wg0 public-key` pelo SSH do console (Lightsail: "Connect using
   SSH"; Oracle: "Cloud Shell" ou o console serial), ou no `/etc/issue`, que o script preenche.
5. No painel, Aplicativos › Rede › Perfis › novo: nome `saida-a` (ou `-b`), tipo `vpn`, protocolo `wireguard`,
   endpoint = o IP estático, porta 51820, segredo = a `PrivateKey` do passo 1, e `params`:
   `{"peer_public_key": "<passo 4>", "address": "10.77.0.2/32", "egress_esperado": "<o IP estático>", "dns": "1.1.1.1", "mtu": 1420}`.
   Criar o perfil não toca aparelho nenhum.

**Roteiro da IDE (V1), depois das 21:30Z:** atribuir `saida-a` ao android-08 e `saida-b` ao android-02 (QA, sem conta
real), política `exigida_com_bloqueio`; conferir em cada um `trafego_verificado`, `egress_matches: true`,
`egress_shared_with: []`, `leak_result: 1`, UDP por perna; reiniciar, hibernar e acordar; parar o `wg-quick@wg0` num
servidor e ver o aparelho ficar sem saída (falha fechada), sem cair para a saída do central. A reversão que **não**
se faz num aparelho com conta real está em `docs/dominios/parque.md` ("Reversão do piloto de saída distinta"). Prova
do script: sintaxe conferida (`bash -n`); a execução num servidor real é `not_run` até o passo 2.

### Objetivo do dono de 30/09: saída própria por aparelho (29.19, 29.20)

**Adiado por decisão do dono** (30/09 ~18:15Z, transmitido pela sessão "Github"): "pode ser feita depois". Não é pendência dele nem bloqueio: o roteiro abaixo, o bloco D3 e o script ficam prontos para quando ele quiser, e a escala continua sendo decisão dele.

Transmitido pela sessão "Github" (~18:10Z): "cada android tenha sua própria saída, para fins de observabilidade e
telemetria de algum aplicativo, e nenhum venha do IP da minha rede". Amplia o 29.7 (piloto de dois) para o parque. Fica
dentro do ADR-056: saída declarada, estável e medida; sem rotação, sem mascarar emulador, imagem ou identidade.

Hoje (medido): android-02, 03, 05 e 06 saem pelo servidor VPN do central — ou seja, pelo IP da casa; android-01 e os
demais saem direto pela casa. Nenhum tem saída própria.

**Como (desenho):** um servidor com N IPv4 e um par WireGuard por aparelho, cada par saindo por um IP diferente (SNAT
pela origem no túnel), no lugar de N servidores. `scripts/rede-saida-externa.sh` já aceita `PARES` (uma linha por
aparelho: chave pública, endereço no túnel, IP local de saída) e recusa dois pares no mesmo IP de saída. No painel, um
perfil por aparelho: o mesmo endpoint e a mesma chave pública do servidor, `address` e `egress_esperado` próprios — o
modelo aceita (não há unicidade de endpoint; o aviso `saida_dedicada_compartilhada` é por perfil, e
`egress_shared_with` compara as saídas MEDIDAS entre aparelhos, que é a prova de que cada um tem a sua). O 29.20 fecha o
outro lado: a plataforma mede a saída do próprio central e acusa o aparelho que medir igual (IPv4 ou IPv6), e o IPv6
medido quando o perfil não leva IPv6.

**Ondas:** V1 com android-08 e android-02 (QA; o 07 ficou com o Outlook do Lucas pelo D9) → os demais aparelhos de QA
(central e notebook) → aparelho com conta real (01, 03, 06) **só com autorização do dono por aparelho** (ADR-056 §7,
`confirm_real_account`) e com IP do Brasil: trocar a saída de uma conta logada costuma disparar desafio (K-057).

**Custo por escala** (13 aparelhos do parque, sem a loja e a quarentena; preços a conferir na contratação):

| Caminho | Como | Custo por mês | Fonte |
|---|---|---|---|
| Oracle Always Free | 2 VMs, 1 IP público cada: 2 aparelhos | 0 | documentação da Oracle (sessão "Github", 30/09); risco de recuperação por ociosidade e de falta de capacidade em SP |
| Lightsail São Paulo | 1 instância Nano por aparelho (uma instância tem um IPv4 público) | US$ 5 × 13 = **US$ 65** | documentação da AWS, conferida em 30/09 |
| Vultr São Paulo | 1 servidor de US$ 5 + ~US$ 3 por IPv4 adicional | ~US$ 5 + 12 × 3 = **~US$ 41** | fontes de terceiros (a página da Vultr não abriu para leitura); limite de IPs por servidor a conferir |
| Hetzner | ~€ 0,50 por IPv4 | — | sem região no Brasil: não serve para conta real |

A decisão é do dono: quantos aparelhos, que provedor e se as contas reais entram (e quando).

### D9 revisto (30/09 ~18:17Z): tudo em todos os aparelhos

Instrução do dono, transmitida pela sessão "Github": "eu quero que tudo funcione em todos os aparelhos". Substitui a
leitura conservadora de ~17:00Z (abaixo, mantida como registro). Vale para os aparelhos com conta real: `gpu_mode:
host` e um reinício em android-01 (Lucas), 03 (Bruno) e 06 (André), com o Outlook no mesmo aparelho do Instagram de
cada persona (a plataforma aceita um app de cada tipo por aparelho). **Não** autoriza reset de dados nem efeito externo
em conta real (mensagem, comentário, publicar, seguir). **Confirmado pelo dono diretamente no chat da IDE** (30/09
~18:20Z), ampliando: "não só com esses mas em todos" — `gpu_mode: host` e reinício, sem apagar dados, em todos os
aparelhos do parque, os com conta real um por vez e com a conta conferida antes e depois.

Com isso voltam ao escopo: o C1 **Outlook → Instagram** só de leitura (24.9) no aparelho de cada persona, e o aceite
no mesmo aparelho (27.2).

**Ordem segura** (depois das 21:30Z e do segundo deploy; um trabalho pesado por vez, K-058):

1. QA: android-07, 10, 12 e 09; depois 08, 02 e 05 no central (trocar o renderizador deles também) e 13–15 no
   notebook. Em cada um: Outlook instalado, aberto e estável; renderizador selecionado lido do log; rede como está.
2. Um aparelho com conta por vez (06, depois 03, depois 01), cada um assim:
   - antes: Instagram logado, sessão viva (verificação da plataforma), a tela "Confirm you're human" ausente, cópia do
     `config.yaml`;
   - `instances.overrides.<id>.gpu_mode: host`, reinício pela plataforma (a escada de reparo sem reset);
   - depois: o mesmo exame do "antes", o renderizador `host` no log, o Outlook instalado e aberto;
   - **parar e voltar atrás** (tirar o override e reiniciar) se o Instagram perder a sessão, pedir desafio ou
     mostrar "Confirm you're human" — e nada toca nessa tela.
3. Vínculo (persona, aparelho, `outlook`) no aparelho da persona; login do Outlook (23.13) com o dono à mão para o
   desafio da Microsoft; C1 Outlook → Instagram em leitura; 27.2.

**Matriz por aparelho** (o que funciona e o que não, com o motivo): entra no fechamento (29.18) e em
`docs/relatorio-validacao.md` §27.

### D4, D6 e D9 → Outlook logado e o fluxo entre apps (29.12, 29.13; fecham 23.8, 23.12, 23.13, 24.9)

*Registro da leitura conservadora de ~17:00Z, substituída às ~18:17Z (bloco acima).*

**D9, decidido** (dono, 30/09): contas reais não mudam de renderizador. O que decorre, escolhido pelo caminho mais
conservador, sem nova pergunta:

- **Um aparelho de QA por conta Outlook.** A plataforma aceita uma conta de cada app por aparelho (409
  `conta_do_app_ja_no_aparelho`, D2-a), então as três contas não cabem no android-07. Mapa: **Lucas → android-07**
  (central; canário aprovado ali), **Bruno → android-10** e **André → android-12** (notebook; o `worker.yaml` já pede
  a GPU do host, e os aparelhos de lá são todos de QA). Nenhuma troca de `gpu_mode` é necessária. O android-09 fica
  para o W0–W8, e o android-08 e o 02 para o V1, para que nenhum experimento de rede aconteça num aparelho com conta.
- O vínculo (persona, aparelho, `outlook`) entra pela IDE **depois** da instalação (29.12), pela rota do vínculo N:N;
  a conta do Instagram de cada persona continua onde está.
- **Comando entre apps:** a execução roda todas as etapas no mesmo aparelho, e cada aparelho precisa ter cada app do
  comando. Com o Outlook fora dos aparelhos do Instagram, o recorte **Outlook → Instagram na mesma conta** (24.9) e o
  aceite integrado **no mesmo aparelho** (27.2) ficam fora por decisão — pôr o Instagram real num aparelho de QA não se
  propõe (ADR-055; risco de desafio). O C1 vira **Outlook → Chrome** no android-07: ler no Outlook o nome de um perfil
  público e abri-lo no navegador, só leitura. Voltar atrás é reversível: trocar o `gpu_mode` de um aparelho com conta
  real é uma linha de configuração e um reinício.

**Onde o Outlook fica** ("todos os aparelhos", com o D9): com conta, android-07 (Lucas), 10 (Bruno) e 12 (André); o 09 já o tem desde o E4. Fora, e por quê: 01, 03 e 06 (conta real no SwiftShader, D9); 02, 05 e 08 (QA do central no SwiftShader: o app derrubaria o emulador, e trocar o renderizador deles só vale se alguém for usá-los com o Outlook); 04 (quarentena); 11 (loja); 13, 14 e 15 (QA do notebook, sem conta: recebem quando forem usados, pela mesma distribuição).

**D4** (consentimento por conta, Persona › Contas e acesso › cartão do Outlook, nas três personas): sem ele a senha não
entra no canal sensível. Pode ser dado a qualquer hora; só é usado no login, depois do segundo deploy. No login, um
desafio da Microsoft (código, aprovação no celular) vai para o dono (ADR-009).

**D6** (e-mail de teste, depois do D4): de uma conta do dono para a caixa Outlook do **Lucas**, assunto
`Perfil para conferir: natgeo` e corpo `Oi! Quando puder, dá uma olhada no perfil público natgeo.` — sem dígitos,
sem link, sem nada que pareça código. A prova C1 usa chamada paga dentro da autorização de 29/09 (resta ~US$ 0,94); se
a estimativa passar disso, a IDE pede antes.

### D7 → PostgreSQL (29.14; fecha o P17)

O limite de gasto do GitHub Actions foi liberado pelo dono em 30/09 12:10Z. O job `backend-postgres` roda no cron
diário (05:17Z) no commit da `main`, que já tem a 063: a prova vem do run de 01/10, sem carga no central e sem
Docker/WSL. A sessão "Github" monitora e avisa; o resultado entra em `docs/relatorio-validacao.md`.

## Checkpoints

### Checkpoint 12 — 01/10 ~13:45Z — W4–W8 no notebook, a causa do W4 era código

- **Causa do W4 achada e corrigida** (`658e5bb`, implantado 12:59Z): a observação contava o cabeçalho "Lockdown filtering
  rules:" do dumpsys (sempre presente) como regra de bloqueio; com a política `exigida`, o túnel no ar nunca valia e a
  rede reiniciava em cadeia. A hipótese de memória do notebook caiu de vez (K-067).
- **android-09 (notebook), `real`:** W2–W5 `trafego_verificado` às 13:08Z (#76), um reinício, o servidor viu o par
  10.66.0.6; W6 modo avião 1 min, túnel de volta sozinho; W7 agente religado, aparelho seguiu sem reinício; W8 teste
  de vazamento real no remoto (bloqueio provado às 13:26Z). Depois do teste, com o bloqueio, o túnel não voltou (tile
  falhou, 2 reinícios, a rede desistiu): rollback às 13:37Z, rede tirada às 13:42Z. 4 reinícios pela rede no W8, todos
  no QA.
- **android-15:** Outlook `ready` (a leitura lenta se confirmou ao ligar).
- **C1 no 03 (US$ 0,60 autorizados):** `r-20261001124036-996716`, ~US$ 0,26; a IA viu o assunto na captura, mas a
  lista do Outlook não expõe texto na árvore e `read_value` recusou. Total pago desde 29/09: ~US$ 2,37.

### Checkpoint 11 — 01/10 ~12:40Z — C1 autorizado no 03, limite do notebook

- **C1 no android-03** (autorização do dono no chat: "pode rodar o C1, até US$ 1,00", uma tentativa):
  `r-20261001123433-09ff22` falhou na primeira etapa. O comando citou a aba Focused, o plano a pôs na pós-condição, e o
  verificador não achou na árvore a indicação da aba selecionada (a lista do Outlook não expõe texto). Gasto medido
  pelo saldo: ~US$ 0,25 (21,15 → 20,90). Sem repetição. Gasto pago total desde 29/09: ~US$ 2,11.
- **C1 de novo, sem citar a aba** (autorização do dono no chat, até US$ 0,60): `r-20261001124036-996716` — a IA viu o
  assunto "Perfil para conferir: nasa" na captura, mas `read_value` foi recusado: a linha da lista do Outlook no
  android-03 não expõe texto na árvore. Execução cancelada ao ficar esperando o usuário; ~US$ 0,26. Total pago desde
  29/09: ~US$ 2,37. Fechar o 27.2 pede leitura do valor pela captura (executor) ou abrir o e-mail.
  - **Conferência no banco (01/10, tarde; leitura, sem repetir):** execução real (`simulated=0`) criada 12:40:36Z, fora das 30
    históricas; 168.977 tokens de entrada, 5.351 de saída e 17 chamadas de IA no objetivo. Etapa 1 (abrir a caixa) terminou
    depois de retentativa; etapa 2 (ler o assunto) começou e foi cancelada; etapa 3 não iniciou. Todas `side_effect=0`, `effects=[]`:
    sem efeito externo. O cancelamento (12:43Z) também ficou com `created_by=panel`, sem identidade recuperável.
- **Notebook:** `max_slots` 3 (decisão do dono no chat, 01/10 ~12:38Z), aplicado por `PUT /api/servers/worker-lan-01/limits`;
  efetivo 3. Desfazer: `{"max_slots": null}` volta ao declarado (6).

### Checkpoint 10 — 01/10 ~10:50Z — pendências da manhã

- **Rodízio** (`0f76562`, implantado 10:00Z): entrega de app sozinha não tira a vaga de aparelho com conta vinculada
  (teste que falhava antes). Com tarefa na fila, o rodízio segue girando as contas.
- **Outlook** `ready` também em 05, 09 e 13; 14 (instalação interrompida) e 15 (leitura lenta) ficam para quando
  ligarem.
- **Triagem das execuções paradas:** 17 de teste sem efeito externo canceladas (QA Messenger e leituras de 24/09 a
  28/09); 10 ficam para o dono decidir (mensagens a terceiros e o site da CETESB).
- **W4 (29.9) bloqueado:** a primeira leitura apontou memória paginada, mas a comparação com o central derrubou a
  hipótese (K-067 reescrito); a causa segue sem medição e o plano está em [memoria-do-notebook.md](memoria-do-notebook.md).
- **C1 no android-03 falhou:** o e-mail de teste enviado pela sessão "Github" (Outlook do André → Bruno, autorizado
  pelo dono) não estava na caixa do Bruno; a etapa de leitura esgotou o teto de 60 chamadas de IA duas vezes
  (~US$ 0,92). **A autorização paga de 29/09 (US$ 1,50) foi ultrapassada: ~US$ 1,86 no total.** Nada pago roda até o
  dono decidir. Achado: o teto por objetivo deixa uma etapa que não acha o que procura gastar 60 chamadas, e a
  repetição gasta outras 60.
- **Limpeza (29.17):** 27 worktrees terminados removidos (os branches ficam; junções desfeitas só como atalho;
  `backend/.venv` e `frontend/node_modules` do central conferidos intactos). Ficam os das sessões em curso (`evo3`,
  `github`, `orq`, `ux-promocao`).
- **D8 feito pelo dono** (Public religado no notebook e no central; conferido por leitura).
- **Execuções antigas:** as 13 restantes (mensagens a terceiros, CETESB, planned) canceladas com a autorização do dono,
  registradas em [execucoes-canceladas-20261001.md](execucoes-canceladas-20261001.md) para recriação.
- **Correções:** aviso do renderizador com as duas causas (`4fb45d7`); recusa do "salvar login" pelo texto quando o
  elemento não é clicável (`b665bb5`).

### Checkpoint 9 — 30/09 ~23:10Z — depois da janela: deploy, renderizador em todos, Outlook logado e C1

- **29.4 fechado** (`real`): 6 h 05 min sem reinício de aparelho pela rede, remedição com a prova da linha.
- **Deploys:** `f6c7df2` às 21:33Z (29.11, correção da axios, login gerenciado do Outlook) e o agente do notebook em
  `0.1.0+f6c7df2`; depois, correções medidas no aparelho, cada uma com teste que falhava antes: GMS pelo pacote
  (`da62dd7`), "Continue" desabilitado até o e-mail (`43db7a6`), rótulo repetido na descrição (`9f7b18b`), releitura
  do campo sensível no WebView (`48efc82`), telas depois da senha (`9ff427c`). Cinco reinícios do backend, nenhum
  reinício de aparelho pedido por prova de vazamento perdida. `2e29350` (boas-vindas pelo carrossel) implantado às 23:11Z (`9f6acdb`), com o agente
  do notebook no mesmo commit.
- **Renderizador `host` em todos** (confirmação do dono no chat, ~18:20Z): `android.gpu_mode: host` no `config.yaml`
  (cópia `data/backups/config.yaml.antes-gpu-host-todos-20260930-213144`). QA primeiro (02, 05, 07, 08), depois as
  contas reais uma por vez, cada uma com o Instagram conferido depois: 06 (21:58Z), 03 (22:25Z), 01 (reinstalado e
  logado de novo, 22:32Z). Nenhuma tela "Confirm you're human". Efeito colateral: com `limits.max_online_devices: 4`,
  a distribuição que ligou o 08 fez o rodízio hibernar 01, 03 e 06 juntos (snapshot, sem apagar nada); acordados um
  por vez. **Com as três contas reais ligadas, a quarta vaga do central fica livre** (android-07 hibernado às 23:13Z):
  qualquer boot novo no central faria o rodízio hibernar uma delas. Para mais aparelhos ligados, o dono sobe o
  limite.
- **Outlook:** promovido e `ready` em 01, 02, 03, 06, 07, 08, 10, 12. Vínculos Lucas→01, Bruno→03, André→06 (os de 10
  e 12 desfeitos). Logins: André (android-06) com as telas depois da senha passadas à mão — "OK" no aviso da conta,
  passkey recusada com Voltar, "Maybe later", "Decline" no diagnóstico opcional — e declaradas; Bruno (03) e Lucas (01)
  **sozinhos** pelas telas declaradas. As três contas `session_ready`. Nenhum desafio da Microsoft.
- **C1 real** (android-01, Lucas): `r-20260930230500-f52eec` leu "Perfil para conferir: natgeo" no Outlook e abriu
  @natgeo no Instagram, só leitura; ~US$ 0,38 (autorização de 29/09: ~US$ 0,94 usados de 1,50).
- **W4 falhou** no android-09 (notebook): depois de aplicar a VPN, o túnel não subiu e o adbd do convidado ficou
  offline; a rede pediu 2 reinícios; rollback às 22:48Z e o aparelho voltou. Fica para investigar.
- Achados registrados: dica "Now your folders…" depois da gaveta (fora da árvore; deixa o login incerto até um
  Voltar); "Save your login info?" do Instagram em Views não clicáveis (o login do Lucas parou nela; "Not now" à mão);
  o aviso de fallback do renderizador diz "sem avisar" também quando a configuração mudou e falta o reinício.

### Checkpoint 8 — 30/09 ~19:25Z — Outlook no notebook antes do deploy, telas do login

- Canário oficial do Outlook, **por aparelho** (sem promover), no android-10 e no android-12 (notebook, `gpu_mode:
  host`; `emuglConfig_init: gles_mode_selected:host` nos dois): `ready`, "app chegou ao primeiro plano em 3 s / 11 s e
  permaneceu". Não promovi: o android-02 (SwiftShader, na janela do P16) tem o Outlook como desejado desde 29/09.
- Vínculos (persona, aparelho, `outlook`): Bruno → android-10, André → android-12; o Instagram segue principal.
- **Telas do login observadas** (android-10, conta do Bruno, `real`): "Add account" → identificador → "Continue" → WebView
  da Microsoft `common_auth_webview` com o conteúdo acessível na árvore → "Verify your email" (código por padrão, com
  "Use your password") → "Enter your password" (`passwordEntry`, "Next"). Parei na senha, sem digitar, e voltei ao
  início. Nenhum código foi pedido.
- `sessao.yaml`/`telas.yaml`/`catalogo.yaml` do Outlook (23.8) em escrita por um agente em worktree, a partir dessas
  telas; entram no segundo deploy.
- "Antes" dos aparelhos com conta (só leitura): android-03 Instagram 447 `ready`, sessão `session_ready` (verificada às
  02:33Z, velha); android-06 Instagram 447 `ready`; android-01 **sem o Instagram** (o reset de 29/09 apagou o app): o
  Lucas precisa do Instagram reinstalado e do login antes do C1 no aparelho dele.

### Checkpoint 7 — 30/09 ~18:35Z — firewall, DHCP e consentimento prontos

- **D1 feito pelo dono** (~17:20Z) e **W0 `real`** (17:5xZ, `POST /api/network/server/firewall-check`, só leitura):
  `liberado`, perfil Public, interface Wi-Fi, sub-rede 192.168.1.0/24, sem aviso, sem regra obsoleta.
- **D2 feito** (~18:05Z) pela sessão "Github" no Chrome do dono, com a sessão dele: reserva DHCP do MAC da Wi-Fi do
  central no mesmo `192.168.1.81`, sem reinício do roteador.
- **D4 feito** (17:55–17:57Z) pela sessão "Github" no painel, por delegação do dono e com a sessão dele, sem digitar
  senha: as três contas Outlook com `consent_at` preenchido.
- **D6**: rascunho no Gmail do dono; o envio é dele. A IDE confere a chegada na caixa antes do C1.
- **CI**: o run 36747845045 (29.11) passou em pytest SQLite, mypy e painel e reprovou só no `npm audit`: sete avisos
  novos da `axios` 1.19.0, publicados à tarde. Corrigido em `d197fec` (overrides na raiz, troca das duas cópias
  empacotadas, disco conferido; K-064).
- **D3**: o script passa a servir também na Oracle Cloud (Ubuntu com o `iptables` da imagem, e Oracle Linux com
  firewalld); caminho gratuito no bloco D3.

### Checkpoint 6 — 30/09 ~17:15Z — D9 decidido, checklist do dono, entrega do D3

- **29.11 integrado** na `main` (`a89cf0d`, `c45da93`, `c38c7fb`, `9367aef` da frente do renderizador, e `bb9b8e5`
  com contrato, CHANGELOG e livro). Testes direcionados (renderizador, contratos do worker, pacote do agente,
  arquitetura, rede, loja) e o painel (73 arquivos, 844 testes) verdes na árvore integrada; CI em curso
  (run 36747845045). Não implantado: a janela segue.
- **D9 decidido** pelo dono (transmitido pela sessão "Github"): contas reais não mudam de renderizador. Mapa de
  aparelhos e consequências no bloco "D4, D6 e D9" (C1 vira Outlook → Chrome; Outlook → Instagram no mesmo aparelho e
  o 27.2 ficam fora por decisão).
- **D3 preparado:** `scripts/rede-saida-externa.sh` (um par, só UDP e SSH, chave privada do servidor não sai dele,
  IPv4 só) e o passo a passo com o Lightsail São Paulo (US$ 5/mês por servidor, fonte oficial conferida em 30/09).
  A chave do cliente é gerada pelo dono com o `sing-box` do central e vai só ao cofre.
- Contas conferidas pela API (sem imprimir endereço): as três personas vivas têm a conta Outlook ativa, com a senha
  clonada e **sem consentimento** (`consent_at` vazio).

### Checkpoint 5 — 30/09 ~16:10Z — E4 (GPU do host pelo serviço), canário do Outlook e painel

Tudo dentro da janela do P16, sem reiniciar o backend; a leitura de 15:46Z e as seguintes seguem sem teste de
vazamento e sem reinício pedido pela rede em android-02, 03 e 06.

- **E4 no central** (`real`, 15:30–15:50Z, android-07, QA sem conta, ligado pela plataforma: tarefa `farm-central`,
  sessão 0): com `instances.overrides.android-07.gpu_mode: host` o log do emulador diz
  `emuglConfig_init: vulkan_mode_selected:host gles_mode_selected:host` e seleciona a NVIDIA RTX 2000. **O canário
  oficial do Outlook passou** (`c-20260930153719-d11a00`: 4 splits instalados, "app chegou ao primeiro plano em 7 s e
  permaneceu"; a release saiu da quarentena para `canary` em android-07, de propósito). Cinco minutos no onboarding — a
  tela que derrubava o emulador — sem queda, sem sinal fatal, carga do convidado 0,12, ~200 MB de VRAM, captura de
  tela funcionando. CPU do host 29–33% durante o experimento.
- **E4 no notebook** (`real`, 15:51–15:58Z, android-09, QA): `gpu_mode: host` no `worker.yaml` (cópia em
  `C:\farm\worker.yaml.antes-gpu-host-20260930`), agente religado; emulador na sessão 0 com
  `gles_mode_selected:host` na Quadro T1000; o Outlook, que em 29/09 derrubou este mesmo emulador, ficou 5 min no
  onboarding depois de `pm clear`, sem queda (177 MB de VRAM). O `worker.yaml` fica com `host`: os aparelhos do
  notebook são todos de QA.
- **Persistência da configuração** (`real`, android-07, 16:03–16:06Z): boot a frio, reinício pela plataforma, hibernar
  e acordar (17 s, do snapshot) mantêm `gles_mode_selected:host`. É argumento de subida do emulador, então vale em
  toda subida; mudar o renderizador muda a assinatura de hardware e descarta o snapshot antigo.
- **O que o convidado enxerga** (`real`): com a GPU do host, o `GLES` do SurfaceFlinger é "Android Emulator OpenGL ES
  Translator (NVIDIA RTX 2000 Ada …)". É o que um app lê como `GL_RENDERER`. Por isso a troca nos aparelhos com conta
  real é decisão do dono (D9): é configuração declarada, mas muda o que o Instagram vê do aparelho.
- **Telas do Outlook observadas sem digitar** (`real`, android-07, pela árvore de tela da plataforma): onboarding
  (`SplashActivity`: `btn_primary_button` "Add account", `btn_secondary_button` "Create new account") e
  `AddAccountActivity` (`auto_complete_input_email` "Enter your email", `btn_primary_button` "Continue",
  `btn_add_google_account`, `menu_qr_code`). A tela da senha só aparece depois do e-mail: depende do consentimento (D4).
- **Painel com o dado do central** (`real`, ~16:00Z, navegador embutido, só leitura além do nome do operador):
  android-01 mostra "sem proxy (legado conferido)"; cada aparelho com bloqueio mostra "bloqueio fora da VPN: provado
  em … (rev 1, cliente 1.14.2 (739))" e "UDP: DNS ok · NTP ok"; a 800 px e a 375 px a página não rola para o lado
  (`scrollWidth` igual à janela) e a tabela rola dentro do cartão, com IP e pacote inteiros; no cartão de contas o
  Outlook mostra o endereço com o ícone de carta e o Instagram o usuário com o arroba. Console sem erro.
- **Não promovi o Outlook**: promover faz o parque perseguir a versão, e abrir o app num aparelho ainda em
  SwiftShader derruba o emulador. A recusa por app (renderizador) está em implementação (29.11) e entra antes da
  promoção.

### Checkpoint 4 — 30/09 ~15:35Z — primeiro deploy e início da janela do P16

**Implantado:** central em `0d70882`, migração `063_prova_de_vazamento`, `/api/health` `ok` sem problemas (15:24:44Z,
`scripts/deploy.ps1`, com backup e a etapa nova do Appium: `npm ci` e o disco conferido em 5.0.12). CI do commit: run
36730509649 verde (pytest SQLite 36m39s; o job de PostgreSQL só roda no cron). Agente do notebook atualizado para
`0.1.0+0d70882` pelo instalador oficial (15:29Z); o worker segue `degraded` só pelo relógio (+5,9 s em relação ao
central), que não é desta fase.

**Ensaio antes do deploy** (15:22Z, `real`): a 063 numa cópia fresca do banco, sem divergência; decisão de adoção
igual à do checkpoint 2; nenhum comando aberto.

**O que a subida mostrou** (`real`, banco do central, comandos e eventos):

| Aparelho | Conta real | O que aconteceu | Cliente VPN parado? | Reinício pedido pela rede? |
|---|---|---|---|---|
| android-02 | não | 15:25:44Z prova anterior **adotada** (teste a partir de 30/09 01:22:29Z, comando `c-20260930012229-187ca4`); medição #59 às 15:26:07Z, `leak_blocked=1`, `trafego_verificado` | não | não |
| android-03 | sim (Bruno) | 15:25:44Z **adotada** (teste a partir de 02:29:45Z, `c-20260930022945-fd0d76`); medição #58, `leak_blocked=1` | não | não |
| android-06 | sim (André) | 15:25:44Z **adotada** (teste a partir de 01:38:43Z, `c-20260930013843-6b8340`); medição #60, `leak_blocked=1` | não | não |
| android-05 | não | sem adoção, por desenho. Com o objetivo parado: "medição dispensada" (nenhuma sonda à toa). Objetivo abandonado às 15:26:30Z; `POST …/apply` às 15:26:37Z → comando `c-20260930152637-06956a`: **teste às 15:26:50Z, bloqueio provado e gravado na linha**; **túnel religado pelo tile às 15:26:59Z, sem reinício**; medição #61 às 15:27:22Z, `trafego_verificado` | sim, uma vez (o teste) | não |

É a primeira vez que o teste de vazamento termina sem reiniciar o aparelho: 45 s do pedido ao `trafego_verificado`.
As medições novas trazem o UDP por perna (`UDP DNS 83 B (1ª de 3, 2,0 s), NTP 48 B (1ª de 3, 2,0 s)`).

**Janela de observação (29.4).** Início: **30/09 15:24Z** (a subida do backend). Fim previsto: **21:30Z**. Commit
`0d70882`; revisão 1 nos quatro aparelhos. A remedição a 90% da validade, que antes disparava o defeito, cai por volta
de 20:50Z (5,4 h depois das medições de 15:26Z). O que invalida a janela: reinício do backend, deploy, `POST …/verify`
ou reatribuição de rede em qualquer dos quatro aparelhos.

- Laço de observação: `scripts/rede-observacao.py --desde 2026-09-30T15:23:00Z --a-cada 600 --ate 2026-09-30T21:40:00Z`,
  processo próprio no central (não depende da sessão da IDE), acrescentando uma leitura a cada 10 min em
  `data/rede/observacao-p16/janela-20260930.md` (fora do Git). Ele separa teste de vazamento, adoção e reinício pedido
  pela rede (classificado pelo passo anterior: teste de vazamento, túnel ou aplicação).
- Critério: em android-02, 03 e 06, **nenhum** teste de vazamento e **nenhum** reinício classificado como "teste de
  vazamento" até o fim, com pelo menos uma medição nova por aparelho depois de 20:50Z trazendo `leak_blocked=1`. Um
  reinício classificado como "túnel" é do 29.3 e entra no registro como tal, não como falha do P16.
- Evento que pode contaminar, registrado de antemão: o E4 (android-07 com a GPU do host) e o canário do Outlook nele
  rodam dentro da janela, só com a CPU do host abaixo de 60%; início e fim ficam anotados aqui.

### Checkpoint 3 — 30/09 ~14:40Z
### Checkpoint 3 — 30/09 ~14:40Z

- Commits locais em `claude/evolucao3`, sobre `15d9ff9`, prontos para publicar: P16 com firewall (29.2, 29.8),
  painel (29.15), túnel no boot e achados da revisão (29.3), UDP (29.5), saída esperada (29.6).
- **Suíte inteira do backend** (SQLite), uma vez, no commit do P16 com o 29.3: 3860 passed, 1 failed
  (`tests/test_backup.py::test_backup_e_restore_ensaio_de_ponta_a_ponta`, falha de ambiente conhecida: o worktree não
  tem `config/config.yaml`), 34 min em prioridade ociosa. Depois dela entraram 29.5 e 29.6, com os arquivos afetados
  (176 testes de rede, banco, arquitetura e pacote do agente) e o painel (`tsc`, vitest 840/840); a suíte inteira
  desses dois é a do CI.
- **Revisão independente do P16** (agente em modo leitura): o mecanismo central segurou (nenhum caminho para o cliente
  em dois testes sem `verify`); quatro achados, todos corrigidos com o teste que falhava antes — A1 (falha antes da
  adoção virava teste destrutivo), A2 (`verify` e wipe sem marca na linha antiga), A3 (linha recriada adotava a prova
  da anterior), A4 (medição a cada passada sem IP).
- **29.3 medido** (`real`, android-05, 7 boots): o always-on tenta uma vez por boot e falhou em 5 de 7 (ANR de início
  do serviço com o convidado sem CPU, ou serviço que para sozinho); sobe entre 92 e 176 s; `force-stop` não religa; o
  tile do cliente religa. O código do gesto rodou duas vezes no android-05 às 13:30Z (túnel de volta em ~9 s, bloqueio
  intacto). Incidente da medição: um filtro largo matou o processo do host que segura o UiAutomator2 do android-05; o
  servidor no aparelho seguiu vivo e a API diz `automation: ready`.
- **E3 executado** (`real`, 14:08–14:36Z, AVD `diag-outlook`, emulador 37.1.11, um fator por vez):

  | Fase | O que mudou | Selecionado de fato | Resultado |
  |---|---|---|---|
  | controle | `-gpu swiftshader_indirect` | `gles_mode_selected:swiftshader`; `debug.hwui.renderer=skiagl` | o emulador caiu ~31 s depois de abrir o Outlook (reproduz o P15) |
  | `-prop` | `-prop debug.hwui.renderer=skiavk` | a propriedade **não** foi aplicada (continuou `skiagl`) | o emulador caiu em ~57 s |
  | `setprop` | `setprop debug.hwui.renderer skiavk` como o shell | `skiavk`, Vulkan do SwiftShader | o emulador não cai, mas **o convidado quebra**: `VulkanManager: Assertion failed: !grExtensions.hasExtension(VK_KHR_EXTERNAL_SEMAPHORE_FD…)` em `system_server`, SystemUI e Settings, em laço |
  | `setprop` + `-gpu lavapipe` | Vulkan do lavapipe | `vulkan_mode_selected:lavapipe`, GLES ainda `swiftshader` | o convidado travou (o shell parou de responder por mais de 9 min) |

  Conclusão: **`skiavk` não serve** nesta imagem e neste emulador; o relato externo de 240 aberturas não vale aqui. As
  cinco "aberturas sem queda" da fase `setprop` eram o convidado em laço de queda — por isso a conferência do estado
  do app, e não só "o emulador está vivo". O caminho provado continua sendo `-gpu host` (E2); `swangle` e
  `angle_indirect` selecionam o SwiftShader-GL no 37.1.11.
- **E4 preparado**: `instances.overrides.android-07.gpu_mode: host` no `config.yaml` do central (cópia em
  `data/backups/config.yaml.antes-fase29-20260930-143629`); o override por instância é a estrutura que já existe e
  vale em todo boot. Entra em vigor no deploy; o experimento (ligar o android-07 pelo serviço, abrir o Outlook, medir
  GPU, RAM e captura) roda depois dele, sem reiniciar o backend.
- **E7 executado** (checkpoint 2).

### Checkpoint 2 — 30/09 ~13:15Z
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

1. Pedido ao dono (uma linha): um e-mail de teste igual ao do Lucas para a caixa do Bruno ou do André — fecha o 27.2
   (C1 num aparelho com a rede verificada).
2. Achados sem correção ainda: dica "Now your folders…" depois da gaveta; "Save your login info?" do Instagram em
   Views não clicáveis; texto do aviso de fallback do renderizador quando falta só o reinício.
3. W4 (29.9): **resolvido** pelo `658e5bb` (checkpoint 12). Resta o W8: depois do teste de vazamento real no android-09 o túnel
   não voltou (tile falhou, 2 reinícios, a rede desistiu); medir antes de repetir.
4. PostgreSQL (29.14): **verde** em 01/10 (run 36830963968); rotina: cron 05:17Z e dispatch `somente_postgres`.
5. Persistência do Outlook no reinício do aparelho (a do app fechado e reaberto já é `real`, 23:10Z).
6. Fechamento (29.18): fecha com o 27.2 e o 29.14; o §27 e a matriz por aparelho já estão no relatório.
