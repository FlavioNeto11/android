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
| 29.4 | P16 (L11) | 6 h reais sem `restart` pedido por `rede`, com remedição | 29.2, 29.3, CI verde | C | **janela aberta às 15:24Z de 30/09**, fim previsto 21:30Z; laço de observação rodando |
| 29.5 | UDP #6/#21 | medição diz perna, tentativas, bytes e tempo | 29.2 integrado (mesmo arquivo) | A | **implantado**, `real` (medições #58 a #61 com o detalhe por perna) |
| 29.6 | P1 | saída medida ≠ esperada vira `parcial` com o motivo | 29.2 integrado (`rede.py`) | A | **implantado**, `simulated`; o real é o 29.7 |
| 29.7 | P1 | duas saídas distintas medidas | **dono**: 2 servidores e chaves | C | bloqueado (externo) |
| 29.8 | 25.7 | comando e leitura do firewall por porta, interface, origem, perfil | — | A | **implantado**, `simulated` + leitura `real` no central |
| 29.9 | 25.7, 25.9 | aparelho do notebook em `trafego_verificado`, com recuperação | **dono**: regra e DHCP; 29.8 | C | bloqueado (externo) |
| 29.10 | P15 | renderizador escolhido por medição, no AVD e pelo serviço | CI fora do ar (carga) | C | **feito**, `real`: E3 (`skiavk` refutado), E4 no central e no notebook (GPU do host pelo serviço: o Outlook abre e fica estável), E7 |
| 29.11 | P15 | renderizador por aparelho e por worker, persistente | 29.10 | C/A | configuração por aparelho e por worker provada (`real`: boot, reinício, hibernar e acordar no android-07); leitura do renderizador selecionado e recusa por app em implementação (agente) |
| 29.12 | 23.2, 23.7, 23.8, 23.12 | Outlook promovido, distribuído e reconhecido | 29.11 | C/A | canário do Outlook **passou** no android-07 (`real`); promoção e distribuição esperam o fim da janela e a decisão D9 |
| 29.13 | 23.11, 23.13, 24.9, 27.2 | login e C1 provados | 29.12; **dono**: consentimento, saldo, e-mail de teste | C | bloqueado (externo) |
| 29.14 | P17 | suíte e migrações em PostgreSQL real | cron do CI de 01/10 05:17Z (o limite de gasto caiu em 30/09 12:10Z) | C | aguarda o cron; a sessão "Github" monitora e avisa |
| 29.15 | 25.8, 23.10 | painel sem as três ambiguidades; estados ausentes inspecionados | 29.2 integrado (`RedePage`) | A | **implantado**; inspeção `real` a 800 e 375 px com o dado do central; estados que o dado real não mostra: só jsdom |
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
| D9 | **Decidir o renderizador dos aparelhos com conta real** (android-01, 03 e 06) para receberem o Outlook: `gpu_mode: host` muda o que os apps enxergam do aparelho (o `GL_RENDERER` deixa de ser o SwiftShader e passa a dizer o nome da placa do host) e exige um reinício de cada aparelho. Alternativa: manter esses aparelhos como estão e pôr a conta Outlook da persona num aparelho de QA com GPU do host | central | 29.12 (23.12), 29.13 | 0 |

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

**Ação do dono:** dois servidores pequenos com IPv4 público próprio, cada um com um WireGuard (ou sing-box) aceitando
UM par; as chaves dos clientes geradas por ele; os dados entram pelo painel (Aplicativos › Rede › Perfis), um perfil
por servidor. A referência de preço da pesquisa (dois servidores em São Paulo a US$ 7/mês cada) é de 30/09 e precisa
ser conferida na contratação; nada é contratado pela IDE.

Por perfil: tipo `vpn`, protocolo `wireguard`, `endpoint_host` e `endpoint_port` do servidor, o segredo (a chave
privada do cliente, que vai ao cofre) e `params` com `peer_public_key`, `address` (o endereço do cliente no túnel),
`egress_esperado` (o IPv4 público do servidor) e, se houver, `dns` e `mtu`.

**Roteiro da IDE (V1):** atribuir um perfil a cada um de dois aparelhos de QA sem conta real (android-07 e android-08),
política `exigida_com_bloqueio`; conferir em cada um `trafego_verificado`, `egress_matches: true`,
`egress_shared_with: []`, `leak_result: 1`, UDP por perna; reiniciar, hibernar e acordar; parar o serviço num dos
servidores e ver o aparelho ficar sem saída (falha fechada), sem cair para a saída do central. A reversão que **não**
se faz num aparelho com conta real está em `docs/dominios/parque.md` ("Reversão do piloto de saída distinta").

### D4, D5, D6 e D9 → Outlook logado e o fluxo entre apps (29.12, 29.13; fecham 23.8, 23.12, 23.13, 24.9, 27.2)

1. **D9** (renderizador nos aparelhos com conta real, ou a conta Outlook num aparelho de QA com GPU do host): sem ela
   o Outlook não pode ser aberto no aparelho da persona (com SwiftShader ele derruba o emulador).
2. **D4** (consentimento por conta, em Persona › Contas e acesso › cartão do Outlook): sem ele a senha não entra no
   canal sensível.
3. Com as duas: promover a release (já em `canary`, aprovada no android-07), distribuir só para os aparelhos com GPU
   do host, `inspect_app`, observar as telas de senha e de desafio (23.7), escrever `sessao.yaml`/`telas.yaml`/
   `catalogo.yaml` (23.8), login e persistência por perfil (23.13). Desafio, código e CAPTCHA ficam com a pessoa
   (ADR-009).
4. **D6** (um e-mail de teste do dono para a caixa da persona, assunto inofensivo com um perfil público, sem dígitos
   que pareçam código) e chamada paga dentro da autorização de 29/09 (até US$ 1,50; usados ~US$ 0,56; saldo estimado
   US$ 22,46 depois da recarga de 30/09): cenário C1 — ler o assunto no Outlook, buscar o perfil no Instagram só de
   leitura, com retomada, cancelamento e a negativa sem consentimento. Se o custo estimado passar do que resta da
   autorização, a IDE pede antes.

### D7 → PostgreSQL (29.14; fecha o P17)

O limite de gasto do GitHub Actions foi liberado pelo dono em 30/09 12:10Z. O job `backend-postgres` roda no cron
diário (05:17Z) no commit da `main`, que já tem a 063: a prova vem do run de 01/10, sem carga no central e sem
Docker/WSL. A sessão "Github" monitora e avisa; o resultado entra em `docs/relatorio-validacao.md`.

## Checkpoints

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

1. Janela do P16 em curso até 21:30Z: não reiniciar o backend, não implantar, não pedir `verify` nem reatribuir rede em
   android-02, 03, 05 e 06. Ler `data/rede/observacao-p16/janela-20260930.md` no fim e registrar o 29.4.
2. Dentro da janela: E4 no android-07 (GPU do host pelo serviço) e canário do Outlook nele; inspeção visual do painel
   (29.15).
3. Depois da janela: `gpu_mode: host` nos aparelhos que vão receber o Outlook (exige reinício do backend), promoção e
   distribuição (29.12), observação das telas e YAML do Outlook.
