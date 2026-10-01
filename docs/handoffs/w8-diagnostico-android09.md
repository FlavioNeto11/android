# W8 do android-09 — diagnóstico instrumentado (estado e plano, 01/10/2026)

**W4 = CLOSED** (causa em código, corrigida em `658e5bb`; K-067 e checkpoint 12 de
[pendencias-evolucao3.md](pendencias-evolucao3.md)). **W8 = OPEN**: depois do teste de vazamento real no android-09 o
túnel não voltou. Este arquivo é o plano do diagnóstico; a evidência bruta do dia está nos checkpoints 10–12 daquele
arquivo e não é repetida aqui. Nada do que está abaixo foi executado no aparelho: o experimento depende de autorização.

## 1. O que aconteceu (UTC, `events`, `commands.result`, `backend.log`)

| Hora | Evento |
|---|---|
| 13:19:45 | rede rev 2 pedida, política `exigida_com_bloqueio` |
| 13:20:30 → :31 | aplicada (`always-on=sfa`, `lockdown=1` relidos); **reinício 1** ("o always-on só vale no boot") |
| 13:21:47 → 13:24:15 | boot 1: 148 s esperando `tun0`, tile clicado, "o túnel não subiu em 20 s"; **reinício 2** |
| 13:25:32 → :35 | boot 2: **`conectado`** com 67 s de uptime (único boot bom dos 4) |
| 13:26:12 → :27 | teste de vazamento automático: cliente parado, sonda recusada `Permission denied` = **bloqueio provado** |
| 13:26:55 | `_tunel_de_volta` falso; tile clicado, "não subiu em 20 s"; `_religar_pelo_boot`; **reinício 3** |
| 13:28:11 → 13:30:37 | boot 3: sem túnel, tile clicado, sem túnel; **reinício 4** |
| 13:31:54 → 13:34:17 | boot 4: sem túnel (ligado há 182 s), tile clicado, sem túnel; `reinicios_max=2` → falha, linha `pendente` |
| 13:37:18 → 13:41:48 | rollback (rev 3, livre): `always-on` e lockdown tirados, reinício 5, conferido (`always-on=null`, `lockdown=0`, sem `tun0`) |

Três falhas diferentes dentro do mesmo "W8" (não confundir):

- **A) Falha de subida do túnel depois do boot.** O `startAlwaysOnVpn` roda uma vez por boot. No android-09: 1 boot bom em 4.
  No android-05 (30/09) foram 2 de 7. É a falha **conhecida** (P16): ANR de `startForeground` com o convidado sem CPU, ou o
  serviço que sobe e para. Estatisticamente compatível com o que já se media.
- **B) Falha do tile.** O gesto (`religar_pelo_tile`) rodou 4 vezes no android-09 e foi `CLICOU=1` nas 4 sem `tun0` em 20 s.
  No android-05 religou 5 de 5 (túnel em ~9 s). Esta é a diferença real a explicar. O relatório §27.3 já dizia `not_run`
  para "o gesto nos aparelhos do notebook".
- **C) Específica do pós-teste de vazamento.** O `force-stop` do teste deixa o pacote `stopped` (flag que persiste). Uma
  única observação (13:26:55), com a mesma falha do tile de B. O boot 1 (sem `force-stop` antes) já tinha falhado, então
  C não explica sozinho nada.

## 2. android-05 (bom) × android-09 (ruim): só o comprovável

Iguais: imagem `android-34;google_apis;x86_64`, API 34, `gpu_mode host`, cliente sing-box 1.14.2 no 05 (a versão do 09
não está persistida: a linha `device_network` foi apagada no rollback), `-lowram`, 2 vCPU.
Diferentes: o 05 é local no central; o 09 é de **worker remoto** (adb por túnel `127.0.0.1:15555`). O 05 não tinha
eventos de pressão de CPU nas janelas de 30/09; o 09 teve load 13,9 e 11,6 durante o teste (nos boots o aparelho está
fora do ar e não há medição). **Não reconstruível:** estado `stopped`, pid do SystemUI e do cliente, e o conteúdo bruto
do comando do tile (não é persistido). Correlação não é causa.

## 3. O comando do tile (`comando_de_religar`)

`Q0` o tile já estava em `sysui_qs_tiles` (se sim, não é removido no fim) · `P1`/`P2` pid do SystemUI antes/depois ·
`Q` o tile está na barra após o `add-tile` · `T` há `tun0` com endereço (se sim, clicar **desligaria** a VPN) ·
`CLICOU` = 1 só com P1=P2, Q>0 e T=0. O log "foi clicado e o túnel não subiu" só sai com `CLICOU=1`; logo, nos 4 casos do
android-09, SystemUI estável e tile na barra. **O `click-tile` roda com saída e erro descartados**: não se sabe se o
clique foi *executado com sucesso*. Os valores brutos não são persistidos.

## 4. Hipóteses (numeração do pedido)

| | Para | Contra | Desconhecido | Como distinguir |
|---|---|---|---|---|
| H1 SystemUI reinicia | — | `CLICOU=1` ⇒ P1=P2, 4/4 | — | pid do SystemUI na janela |
| H2 tile não entra | — | `CLICOU=1` ⇒ Q>0, 4/4 | — | `sysui_qs_tiles` por amostra |
| H3 clique não executa | tile 0/4 contra 5/5; saída descartada | nada direto | retorno do `click-tile` | `TileService`/`onClick` no logcat; retorno do comando |
| H4 pacote preso em stopped | `force-stop` do teste | boot 1 falhou sem `force-stop`; no 05 religou após `force-stop` | `stopped=` | `dumpsys package … stopped=` antes/depois |
| H5 cliente inicia, serviço não | modo conhecido no 05 | — | logs do cliente | pid do cliente + `startForeground`/ANR |
| H6 `tun0` sobe, sem CONNECTED | — | `tun0` ausente nas 4 leituras finais | — | `V` (CONNECTED) com `tun0` |
| H7 lockdown incoerente | — | regras ativas como esperado nas 4 | com `tun0` no ar | nº de linhas `UIDs:` |
| H8 sem handshake | — | `tun0` nunca subiu depois do tile | handshake do boot 2 (o `last_connection` lido era anterior a ele) | `last_connection` do peer |
| H9 fallback de boot falha sob carga | 3 de 4 boots sem túnel; 5/7 no 05; load alto | — | log do `startAlwaysOnVpn` | ANR/`Start proc` no logcat do boot |
| H10 outra | worker remoto; CPU do convidado | — | tudo | comparar com o caso bom na mesma janela |

Memória do notebook é **controle**, não hipótese (`memoria-do-notebook.md`, K-067).

## 5. Classificador (primeiro ponto quebrado; `scripts/diag-w8.py classificar`)

Ordem do caminho do tile: **F1_SYSTEMUI_RESTART** (pid do SystemUI mudou) → **F2_TILE_NOT_ADDED** (tile ausente em ≥3
leituras) → **F3_TILE_NOT_CLICKED** (logcat capturado sem sinal do tile e o cliente não nasceu) →
**F4_PACKAGE_STAYS_STOPPED** (`stopped=true` e nenhum processo) → **F5_VPN_SERVICE_NOT_STARTED** (ANR/`startForeground`, ou
processo sem serviço no logcat) → **F6_TUN_NOT_CREATED** (serviço subiu, sem `tun0`) → **F7_VPN_NOT_CONNECTED** (`tun0` ≥5 s
sem `VPN CONNECTED`) → **F8_LOCKDOWN_MISMATCH** (regras ≠ política) → **F9_NO_WIREGUARD_HANDSHAKE** (peer sem conexão nova).
Modo `boot`: **F10_BOOT_RECOVERY_FAILED** (180 s de uptime sem `tun0`). **F_OTHER**: tudo passou mas o túnel caiu no fim.
Regras: estágio sem dado = `UNKNOWN`; falha com estágio anterior desconhecido = `UNKNOWN` com `candidato`; ausência de
log **nunca** vira sucesso. Testado com 7 fixtures sintéticas + o caminho saudável (`scripts/tests/test_diag_w8.py`).

## 6. Instrumentação (`scripts/diag-w8.py`, **somente leitura**, não existe modo que escreva)

- **Aparelho:** uma ida `adb shell` por amostra, ~2 s (uptime, `boot_completed`, `always_on`, `lockdown`, `tun0`, pid do
  cliente, pid do SystemUI, tile presente); a parte pesada (`dumpsys connectivity`: CONNECTED e regras `UIDs:`; `stopped=`)
  a cada 5 amostras, porque o convidado de 2 vCPU sem CPU é a hipótese de fundo e o coletor não pode derrubá-lo.
- **Logcat:** um laço contínuo que reconecta (o reinício derruba o adb e apaga o buffer), com horário e filtro (cliente,
  sing-box, VPN/ConnectivityService, TileService, ANR, force-stop, `startForeground`, `Start proc`, SystemUI que morre),
  teto de 20 MB.
- **Central (`mode=ro` + GET):** `device_network` (`desired_rev`, `applied_rev`, `state`, `policy`, `leak_*`, `detail`,
  `error`), comandos `device.network` e `restart` com a recusa, eventos `network.updated`, peer em `/api/network/server`.
- **Notebook:** SSH só se responder. **Hoje não responde**: `farm-tunel@192.168.1.19` dá `Permission denied (publickey…)` e,
  de qualquer modo, a chave do projeto é restrita a túnel (`command="exit"`). Fallback: o que o agente do worker já reporta
  (CPU, RAM livre, `swap_used_pct`). `Pages/sec`, `Committed Bytes`, `Commit Limit` e CPU do `qemu` exigem acesso de
  leitura que **só o dono pode dar** (ou rodar o PowerShell de `diag-w8.py comandos` localmente no notebook).
- Uso: `python scripts/diag-w8.py preflight`, `coletar --duracao 900`, `classificar --run <pasta> --desde … --ate …`.
  A saída vai para `data/diag-w8/` (fora do Git).

## 7. Abort gate: manutenção do worker (provado por teste automatizado, sem worker real)

Teste: `backend/tests/test_rede_aplicacao.py::test_manutencao_do_worker_recusa_o_reinicio_da_rede_e_nao_o_device_network`
(com a manutenção desligada ele falha). O que está provado:

- `device.network` **não** passa por `_precheck` (só `despacho.py:631` lê a manutenção): trabalho em voo e novo seguem.
- O `restart` da convergência passa por `pedir_ciclo_de_vida` → `_precheck` → **recusado** com "worker … em manutenção":
  fica um comando `restart` em `rejected` com o motivo no histórico; o aparelho não recebe `restart`; a linha de rede e a
  evidência permanecem. (Outro `None` é o verbo fora de `worker_verbs`: aí **nem** há comando; o worker-lan-01 declara `restart`.)
- `_pedir_reinicio` soma ao `mem.reinicios[rev]` **antes** de olhar o resultado: a recusa conta para `reinicios_max=2`;
  `mem.espera_ate = agora + _ESPERA_DA_RELEITURA_S` (**300,0 s**). Sem manutenção, o pedido seguinte é aceito.
- Os outros pedidos de `restart` (remediação, `state.py`) usam o mesmo caminho e são recusados igual. O que o agente do
  worker faria localmente não tem dado.
- Durante os 300 s, `_acao` devolve nada para `varredura`/`tarefa`, mas **não** para `ligou`/`pedido` (um POST de apply
  passa). Ao voltar a manutenção para `false` **nada dispara sozinho**: vale a próxima varredura depois de `espera_ate`.
- **Pela leitura do código (não provado por teste):** com a manutenção ligada, cada passada de `conectar` repete o tile
  (a releitura com regras ativas) e soma uma recusa: ~+300 s segunda tentativa, ~+600 s teto 2 → `pendente`, ~+900 s `aplicar`
  (reprovisiona e perturba a evidência). Logo, **não é congelamento indefinido**: ~10 min até `pendente`, ~15 até reaplicar,
  com 2 tentativas extras do tile no meio.

**Armar cedo (no t0) não serve:** bloquearia o reinício do apply ("o always-on só vale no boot") e os boots necessários
para chegar a `conectado`. O ponto de armar é o evento `network.updated … → conectado`: dali ao fim do teste de
vazamento passaram 80 s em 01/10 (13:25:35 → 13:26:55), uma janela de **dezenas de segundos para automação**, não de
1 s para uma pessoa. Isso exigiria um modo com flag no coletor (não implementado: não há mutação aqui). Antes do rollback
a manutenção tem de voltar a `false`, senão o reinício do rollback também é recusado. A manutenção vale para o **worker
inteiro** (os outros aparelhos do notebook estão parados).

## 8. FASE A futura — "TILE-ONLY" (recomendada antes do W8 completo)

Possível pelo estado atual (perfil dentro do cliente, `always-on=null`, `lockdown=0`, sem `tun0`, sem peer, cliente com
processo vivo e `stopped=false`, aparelho online sem linha de rede): `force-stop` → preparar o tile → `click-tile`
**com saída e retorno capturados** → observar pacote/processo/serviço/`tun0`, com o coletor rodando. Sem `assign`, sem peer
no servidor, sem tocar nos outros aparelhos, sem teste de vazamento, sem reinício automático (não há linha de rede).

- **Prova:** F3 (clique executado ou não, com o retorno do comando), F4 (`stopped` antes/depois), F5/F6 (processo, serviço,
  `tun0`) **no android-09, com impacto só nele**. Se o tile não religar nem aqui, o defeito é do gesto neste aparelho e
  não do W8 nem do boot.
- **Não prova (verbatim):** (1) o comportamento com `always-on` + `lockdown=1`, que é a condição real do W8: o sistema pode
  segurar ou reiniciar a VPN de outro jeito; (2) `CONNECTED` sob as regras de lockdown (F7/F8); (3) a recuperação de
  `force-stop` sob lockdown (H4 na condição real). Também não prova F9 (sem peer, a ausência de handshake é esperada) nem o
  boot com o convidado sem CPU (H9).
- **Impacto:** o aparelho fica sem internet enquanto o `tun0` estiver no ar apontando para um servidor sem peer; desfaz-se
  clicando o tile de novo ou parando o cliente. É conta zero (QA). Requer um acionador pontual **com flag** (`force-stop`,
  `add-tile`, `click-tile`), que **não** existe: o coletor é só leitura.

## 9. W8 completo: sequência mínima (se a Fase A não bastar)

1. Baseline (leitura) e coletor ligado. 2. `POST /api/network/assign` direto com `exigida_com_bloqueio` (rev nova), sem
passar por `exigida`. 3. O apply pede o reinício; cada boot tem ~30% de chance de subir o túnel. 4. A cada boot sem túnel
o código tenta o tile e reinicia (já é a Fase A de graça, com o teto 2). 5. Se chegar a `conectado`, o teste de
vazamento roda sozinho na varredura seguinte (≤60 s). 6. Observar; parar assim que o primeiro ponto for classificável
(manutenção armada em `conectado`, com flag). 7. Rollback. **Impacto:** cadastrar o par reinicia o servidor VPN do central e
derruba por segundos o túnel de 02, 03, 05 e 06 (03 e 06 têm conta real): só numa janela sem tarefa.

## 10. Rollback comprovável

`POST /api/network/assign` política `livre` (como em 13:37Z) com a manutenção em `false` → aguardar o reinício de remoção
→ provas: `always-on=null`, `lockdown=0`, `tun0` ausente, sem rede VPN conectada, sem regras de bloqueio, cliente sem
segurar a VPN, **conectividade `healthy`** (depois do rollback anterior houve minutos de "sem internet validada"),
android-09 `online`, `GET /api/network/server` sem peer do 09, e os aparelhos 02/03/05/06 sem mudança de estado.

## 11. Fatos provados × desconhecidos

**Provados:** W4 resolvido; bloqueio fora da VPN provado (13:26:27); 4 reinícios na revisão, o teto é 2 por fase e zera ao
conectar; o tile foi clicado nos 4 casos (SystemUI estável, tile na barra, sem `tun0`); o gate de manutenção recusa
`restart` e não `device.network`; o android-09 está hoje online, sem rede, sem peer, `always-on=null`, `lockdown=0`.
**Desconhecidos:** se o `click-tile` é executado com sucesso; `stopped` do pacote nos 4 casos; logs do cliente/boot; versão
do cliente do 09; handshake no boot 2; contadores de memória do notebook (SSH negado); se o gesto funciona no 09 isolado.

## 12. Se o defeito se confirmar (arquivos, não alterados)

`backend/app/devices/rede_aplicacao.py` (`comando_de_religar`, `religar_pelo_tile`: saída e retorno do clique),
`backend/app/devices/rede_convergencia.py` (`_conectar`, `_religar_sem_reinicio`, `_reiniciar_ou_desistir`),
`backend/app/config.py` (`reinicios_max`, espera do tile), `backend/tests/test_rede_aplicacao.py`, `docs/conhecimento/aprendizados.md`.
Só se o coletor externo não bastar para distinguir H3–H6 (decisão de 01/10: sem mudar produção para logar melhor antes).
