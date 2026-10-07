# Rede por aparelho nos emuladores do worker remoto (25.7): auditoria da regra de firewall proposta, workers fora da LAN e plano de validação

Pesquisa só-leitura de 30/09/2026 (~10:40–10:50Z). Central `WIN-7S2UASNLFOP` (192.168.1.81, Windows Server 2025
10.0.26100) no commit `69970916` (`/api/health`: `status: degraded` só por `ai_balance_low`, migração
`058_chaves_de_rede`); notebook `worker-lan-01` (192.168.1.19, Windows 10.0.26100) com o agente em `0.1.0+e26a469`.
Nada foi criado, alterado, iniciado ou parado: nem regra de firewall, nem config, nem aparelho, nem deploy. Nenhum
segredo foi lido (o `worker.yaml` foi filtrado por padrão; o `.env` e o `config.yaml` não foram abertos).

Convenção de prova: **real** = medido nesta data nas máquinas acima; **código** = lido no repositório em `6997091`;
**doc externa** = fonte pública com URL; **inferência** = dedução, com confiança declarada; **not_run** = proposto,
não executado. Pesquisa irmã: [saida-distinta-por-aparelho.md](saida-distinta-por-aparelho.md) (P1: servidor num VPS).

## Pergunta 1 — Auditoria da regra proposta: quem recebe o tráfego, perfil efetivo, escopo de origem, rotas e portas de fato, e o que acontece quando o IP do DHCP ou o executável mudam

### Takeaway
A regra que a plataforma propõe é tecnicamente correta para o cenário de hoje (o UDP 51820 chega ao central pela Wi-Fi
192.168.1.81, classificada **Public**, vindo do notebook 192.168.1.19 na mesma /24, e o sing-box escuta em `0.0.0.0` e
`[::]`), mas é frágil em três pontos que a própria plataforma não vê: `-Profile Public` morre se a rede for
reclassificada; `-Program` com a versão no caminho morre na próxima atualização do sing-box (a plataforma volta a
`sem_regra` e RECUSA a aplicação, falha visível, não silenciosa); e `LocalSubnet` abrange também a sub-rede do
vEthernet do WSL (172.26.32.0/20), além de ser dinâmico (segue o IP atual). A mudança do IP do DHCP não muda a regra
(o keyword se expande em tempo de execução), mas invalida `endpoint_lan` e o perfil já importado em cada aparelho
remoto, e a plataforma não reprovisiona sozinha.

### Cited Findings

**O que a plataforma lê e o que propõe (código + real):**

- O leitor do firewall é um PowerShell sem verbo de escrita, no `ActiveStore`: `Get-NetFirewallProfile`,
  `Get-NetConnectionProfile` (+ `Get-NetIPAddress` por interface), `Get-NetFirewallApplicationFilter`,
  `Get-NetFirewallPortFilter`, `Get-NetFirewallRule` — [rede_firewall.py:64-104](C:\git\android\backend\app\devices\rede_firewall.py).
- O perfil é decidido assim: se `endpoint_lan` é um IP de alguma interface listada por `Get-NetConnectionProfile`, o
  `NetworkCategory` dela vira o perfil (`public → Public`, `private → Private`, `domainauthenticated → Domain`);
  `endpoint_is_local` fica `True`. Se não é de nenhuma interface, `endpoint_is_local = False` e o aviso "não é endereço
  de nenhuma interface do central" entra no detalhe; o perfil-alvo passa a ser todos os ligados —
  [rede_firewall.py:186-214](C:\git\android\backend\app\devices\rede_firewall.py).
- A avaliação filtra regras por: habilitada, `Inbound`, protocolo UDP/17/Any, porta que cobre 51820 (número, faixa ou
  Any), programa igual ao binário **ou Any**, e perfil que cobre o alvo (ou Any). Bloqueio vence permissão; sem regra,
  vale `DefaultInboundAction` (`NotConfigured` = Block) — [rede_firewall.py:147-264](C:\git\android\backend\app\devices\rede_firewall.py).
  **Não lê `RemoteAddress`, `InterfaceAlias` nem `InterfaceType`**: uma regra `-RemoteAddress Any` e uma
  `-RemoteAddress LocalSubnet` são indistinguíveis para a plataforma (mesmo arquivo; nenhum
  `Get-NetFirewallAddressFilter`/`Get-NetFirewallInterfaceFilter` no script).
- O comando proposto: `New-NetFirewallRule -DisplayName 'Central de Aparelhos - rede por aparelho (WireGuard UDP {porta})' -Direction Inbound -Action Allow -Protocol UDP -LocalPort {porta} -Program '{binário}' -RemoteAddress LocalSubnet -Profile {perfil ou Any}` —
  [rede_firewall.py:107-112](C:\git\android\backend\app\devices\rede_firewall.py).
- Leitura **real** pela própria plataforma, `POST /api/network/server/firewall-check`, 30/09 10:44:23Z, central
  `69970916`: `state: sem_regra`, `profile: Public`, `interface: Wi-Fi`, `endpoint_is_local: true`,
  `allowing_rules: []`, `blocking_rules: []`, `lan_endpoint: 192.168.1.81`, `remote_peers: []`, e o comando
  exatamente como no enunciado (com `-Program 'C:\git\android\data\rede\sing-box-1.14.2-windows-amd64\sing-box.exe'
  -RemoteAddress LocalSubnet -Profile Public`).
- O cache da leitura vale 600 s; o `GET /api/network/server` só devolve a última leitura (`firewall: null` antes da
  primeira) e a aplicação num remoto relê forçado — [rede_firewall.py:283-324](C:\git\android\backend\app\devices\rede_firewall.py),
  [rede_servidor.py:472-486](C:\git\android\backend\app\devices\rede_servidor.py). Sem par remoto, o laço não lê
  (`conferir_acesso_remoto`). Real: o `GET` de 10:40Z trazia `firewall: null`.

**Quem recebe o tráfego, e onde o servidor escuta (código + doc + real):**

- A configuração do servidor tem `"listen_port": cfg.porta_wireguard` e nenhum endereço de escuta —
  [rede_servidor.py:174-179](C:\git\android\backend\app\devices\rede_servidor.py). A documentação do endpoint
  `wireguard` do sing-box lista `system, name, mtu, address, private_key, listen_port, peers, workers, on_demand` +
  campos de UDP NAT e Dial; **só existe `listen_port`, não há campo de endereço de escuta** —
  [sing-box: WireGuard Endpoint](https://sing-box.sagernet.org/configuration/endpoint/wireguard/) (lido em 30/09/2026).
- **Real** (`Get-NetUDPEndpoint -LocalPort 51820`, central, 30/09 ~10:42Z): `0.0.0.0:51820` **e** `[::]:51820`,
  PID 21776 `sing-box`, caminho `C:\git\android\data\rede\sing-box-1.14.2-windows-amd64\sing-box.exe`, iniciado
  29/09 23:51:36 local. `GET /api/network/server`: `running: true`, `in_sync: true`, `detail: "no ar com 4 aparelho(s)
  e 0 usuário(s) de proxy"`, pares android-02 (10.66.0.3), 03 (.5), 05 (.2), 06 (.4), todos `remote: false`, com
  `last_connection` entre 07:37 e 07:40 (-0300). Ou seja: o servidor **está** rodando e escuta em todas as interfaces
  IPv4 **e IPv6**.
- A porta do proxy (`mixed`, 18080) fica só em `127.0.0.1` — [rede_servidor.py:169-170](C:\git\android\backend\app\devices\rede_servidor.py);
  hoje `proxy: null` (0 usuários). Nada além da UDP 51820 precisa entrar pela LAN para a rede por aparelho.
- Interfaces do central (**real**, `Get-NetIPAddress -AddressFamily IPv4`): `vEthernet (WSL (Hyper-V firewall))`
  172.26.32.1/20 (Manual); `Ethernet` 169.254.56.131/16 (APIPA, `Disconnected`); `Wi-Fi` 192.168.1.81/24 (**Dhcp**,
  `ValidLifetime 12:35:37`); loopback. Só a Wi-Fi tem `Get-NetConnectionProfile`: `Padilha_5G`, `InterfaceIndex 19`,
  `NetworkCategory Public`, `IPv4Connectivity Internet`, `IPv6Connectivity Internet`. Rota padrão IPv4: única, pela
  Wi-Fi para 192.168.1.1 (`RouteMetric 0`, `InterfaceMetric 30`).
- `ipconfig /all` (**real**, bloco Wi-Fi, sem segredo): `Intel(R) Wi-Fi 6E AX211`, MAC `AC-45-EF-2C-22-FC`, `DHCP
  Enabled: Yes`, `Lease Obtained 23/09/2026 20:15:15`, `Lease Expires 30/09/2026 20:15:23`, `DHCP Server 192.168.1.1`,
  `Default Gateway fe80::1%19`, `DNS Servers 2001:4860:4860::8888`. (O gateway IPv4 192.168.1.1 aparece em
  `Get-NetRoute`; o `ipconfig` mostrou primeiro o v6.)
- Perfis do firewall do central (**real**): Domain/Private/Public todos `Enabled True`, `DefaultInboundAction
  NotConfigured`, `DefaultOutboundAction NotConfigured` (Windows trata `NotConfigured` de entrada como Block: é o que
  a plataforma assume — [rede_firewall.py:183-185,255-263](C:\git\android\backend\app\devices\rede_firewall.py) — e o
  que a prova do notebook confirma abaixo).
- Regras de entrada habilitadas que cobrem UDP 51820 ou `LocalPort Any` no `ActiveStore` (**real**): nenhuma para o
  sing-box e nenhuma para a porta 51820 por número. As que casam por `Any` são todas amarradas a outros programas
  (`svchost` AllJoyn, `mdeserver` Cast, `WFD Driver-only` = System, Firefox, TeamViewer, Lync, Teams, M365 Copilot);
  as de perfil Public são TeamViewer, Lync/UcMapi e as `Any` (Teams, Copilot, WFD), todas com `RemoteAddress Any`.
  `Get-NetFirewallApplicationFilter | Where Program -like '*sing-box*'`: **vazio**.
- **Prova real de que o caminho está fechado hoje**, do notebook (`Test-NetConnection 192.168.1.81 -Port 8010`, via
  SSH, 30/09 ~10:45Z): `TcpTestSucceeded False`, **`PingSucceeded False`**, `SourceAddress 192.168.1.19`. O 8010 falha
  por desenho (só escuta em 127.0.0.1 e é alcançado pelo `-R` — [worker-tunnel.ps1:50-57](C:\git\android\scripts\worker-tunnel.ps1));
  o ICMP falhar é o perfil Public com entrada Block em ação. UDP não é testável pelo `Test-NetConnection`.

**Semântica dos parâmetros (doc externa, Microsoft Learn, módulo NetSecurity, página do Windows Server 2025,
atualizada 2025-05-14):**

- `-RemoteAddress`: aceita IPv4/IPv6 único, sub-rede por bits ou máscara, faixa, e os keywords `Any, LocalSubnet,
  DNS, DHCP, WINS, DefaultGateway, Internet, Intranet, IntranetRemoteAccess, PlayToDevice, CaptivePortal`; "Keywords
  can be restricted to IPv4 or IPv6 by appending a 4 or 6 (for example, keyword "LocalSubnet4" means that all local
  IPv4 addresses are matching this rule)" — [New-NetFirewallRule](https://learn.microsoft.com/en-us/powershell/module/netsecurity/new-netfirewallrule).
- `-Profile`: "The rule is active on the local computer only when the specified profile is currently active. ...
  Only one profile is applied at a time. The acceptable values ... Any, Domain, Private, Public, or NotApplicable. The
  default value is Any" — mesma fonte.
- `-Program`: "Specifies the path and file name of the program for which the rule allows traffic. This is specified as
  the full path to an application file" — mesma fonte. `-InterfaceAlias` (WildcardPattern[]) e `-InterfaceType`
  (`Any, Wired, Wireless, RemoteAccess`) existem e restringem a regra à interface — mesma fonte.
- Keywords de escopo: "Keywords represent server types, such as local subnet, DNS server, DHCP server, WINS servers,
  and default gateway. **IP addresses are derived by expanding keywords at run time**" —
  [Firewall Rule Properties – Scope (arquivo, WS2008, atualizado 2021-11-12)](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2008-R2-and-2008/cc771905(v=ws.10)).
  A enumeração COM define `NET_FW_SCOPE_LOCAL_SUBNET` como "the scope is local subnet only" —
  [NET_FW_SCOPE](https://learn.microsoft.com/en-us/windows/win32/api/icftypes/ne-icftypes-net_fw_scope).

**Executável e atualização (código):**

- O caminho vem de `rede.servidor.binario: data/rede/sing-box-1.14.2-windows-amd64/sing-box.exe` —
  [config.example.yaml:343](C:\git\android\config\config.example.yaml); a versão faz parte do caminho.
- A comparação de programa expande `%VAR%` e compara caminho normalizado sem caixa; `Any` sempre casa —
  [rede_firewall.py:81-84,147-151](C:\git\android\backend\app\devices\rede_firewall.py).

### Inferences

- **Quem deve receber o tráfego:** o central, na Wi-Fi 192.168.1.81, perfil efetivo **Public**. A plataforma acertou
  o perfil (mapeou `NetworkCategory Public` da interface que tem o IP). Confiança alta (real).
- **`LocalSubnet` no central expande para 192.168.1.0/24 E 172.26.32.0/20 (vEthernet do WSL) E 169.254.0.0/16 se a
  Ethernet subir** — pelo texto "local subnet only" + "expanded at run time" e pela lista de interfaces real. Não é
  furo grave (o WSL é da própria máquina; a APIPA só existe sem cabo), mas é mais largo do que "o notebook".
  Confiança alta na semântica, média na lista exata (não há doc que enumere "todas as interfaces" literalmente).
- **IPv6:** o servidor escuta em `[::]:51820` e a Wi-Fi tem IPv6 Internet. A regra proposta é `-RemoteAddress
  LocalSubnet` (v4 e v6 on-link), então continua fechada para a internet v6 pelo Block padrão. Se algum dia alguém
  trocar para `-RemoteAddress Any`, a UDP 51820 fica exposta em v6 público. Confiança alta.
- **Mudança do IP do DHCP:** a regra **não** quebra (`LocalSubnet` é dinâmico; `-Program` e porta não mudam). O que
  quebra é (1) `rede.servidor.endpoint_lan` no `config.yaml`, lido a cada plano (`endpoint_do_central`,
  [rede_aplicacao.py:160-171](C:\git\android\backend\app\devices\rede_aplicacao.py)) e sem recarga a quente (nenhum
  `reload` em `config.py`/`api.py`; o doc manda reiniciar o backend — [parque.md:629-630](C:\git\android\docs\dominios\parque.md));
  (2) o perfil **já importado** em cada aparelho remoto, que carrega `peers[].address = endpoint_lan` fixo
  ([rede_aplicacao.py:263-268](C:\git\android\backend\app\devices\rede_aplicacao.py)). A observação de deriva compara
  `always_on`, `lockdown`, `tun`, `vpn_conectada` — não o endpoint ([rede_aplicacao.py:560-583](C:\git\android\backend\app\devices\rede_aplicacao.py)) —,
  então o aparelho ficaria com o `tun` no ar e sem handshake: a convergência veria "túnel caído com a configuração no
  lugar → `configurado` → reinicia" até `rede.reinicios_max` (2) e cairia em `pendente` com erro
  ([rede_convergencia.py:12-14,507-529](C:\git\android\backend\app\devices\rede_convergencia.py)). A saída é o
  **Reaplicar** (`desired_rev+1`, [rede.py:718-731](C:\git\android\backend\app\devices\rede.py)), que reprovisiona com
  o `endpoint_lan` novo. Com `lockdown=1` (política `exigida_com_bloqueio`), o aparelho fica **sem rede nenhuma**
  nesse intervalo. Confiança alta (código).
- **Executável que muda de caminho:** com `-Program` fixo em `...-1.14.2-...`, a atualização para 1.15 faz a leitura
  voltar a `sem_regra` (nenhuma regra casa o binário novo) e a convergência **recusa** aplicar em remotos
  (`_firewall_para_remoto`, [rede_convergencia.py:416-431](C:\git\android\backend\app\devices\rede_convergencia.py)),
  com o comando novo no `error`. Os remotos já provisionados continuam discando e o Windows passa a descartar o UDP
  (a regra velha não casa o processo novo). Falha visível, mas derruba a rede de todos os remotos até o dono rodar o
  comando novo. Confiança alta.
- O `-RemoteAddress` e a interface **não** entram na avaliação da plataforma; logo a regra pode ser mais estrita do
  que a proposta (interface, sub-rede explícita) sem que a plataforma deixe de ler `liberado`. Confiança alta (código).

### Gaps

- Não há documento da Microsoft na versão atual (Windows Server 2025) que enumere textualmente que `LocalSubnet`
  cobre "todas as sub-redes de todas as interfaces"; a evidência é a definição da enumeração e a expansão em tempo de
  execução (página arquivada de 2008 R2). Uma prova real exigiria criar a regra e testar — fora do escopo (só leitura).
- `Get-NetFirewallProfile` devolveu `DefaultInboundAction NotConfigured` nos três perfis; a equivalência
  `NotConfigured = Block` é assumida pelo código e confirmada indiretamente pelo ping recusado, não por documento lido
  nesta pesquisa.

## Pergunta 2 — Workers na mesma LAN versus workers fora dela (o que a regra `LocalSubnet` prova e o que não prova)

### Takeaway
A regra `LocalSubnet` só serve ao notebook que está na mesma /24 da Wi-Fi do central e nada prova para um worker
fora da LAN: para esse, o desenho atual (servidor no central, atrás de NAT doméstico com IP DHCP e firewall Public)
exigiria endpoint público + encaminhamento no roteador + regra com escopo Internet, que o dono vetou; a alternativa
coerente com a pesquisa irmã é o servidor num VPS, que serve LAN e remotos, tira a dependência do firewall do central
e do `endpoint_lan`. Esta pesquisa **não prova** worker fora da LAN.

### Cited Findings

- O agente do notebook chega ao central só pelo túnel SSH: `-L porta:127.0.0.1:remota` por aparelho e `-R
  18000:127.0.0.1:8010` para o WebSocket do agente; a chave no worker é restrita a encaminhamento
  (`restrict,port-forwarding,permitopen=...,permitlisten=...,command="exit"`) — [worker-tunnel.ps1:6-57,282-283](C:\git\android\scripts\worker-tunnel.ps1),
  [docs/worker.md:279-324](C:\git\android\docs\worker.md). O túnel **não** carrega o UDP do WireGuard: "Nada muda na
  rota do host nem no túnel SSH: o UDP sai pela rede do notebook até a LAN do central" —
  [rede_aplicacao.py:160-163](C:\git\android\backend\app\devices\rede_aplicacao.py).
- **Real** (notebook, via SSH, 30/09 ~10:47Z): `Wi-Fi 192.168.1.19/24 (Dhcp)`, `vEthernet (WSL) 172.28.208.1/20`, três
  APIPA; `Get-NetConnectionProfile`: `Padilha_5G 2`, `Public`, `IPv4Connectivity Internet`; rota padrão pela Wi-Fi
  para 192.168.1.1; `Test-NetConnection 127.0.0.1 -Port 18000`: `TcpTestSucceeded True` (o `-L` do agente está de pé);
  `Get-NetUDPEndpoint -LocalPort 51820`: vazio (nada escuta 51820 lá, como esperado); processos: só `adb` (PID
  18744), nenhum `emulator`/`qemu` — os seis aparelhos android-09/10/12/13/14/15 estão `stopped`
  (`GET /api/instances`, serials `127.0.0.1:15555…15565`, `remote_adb_port 5555…5565`).
- **Real** (notebook): perfis do firewall `Domain True`, `Private True`, **`Public False`** (o perfil ativo da Wi-Fi
  do notebook está com o firewall DESLIGADO). Não afeta a entrada no central, mas é um achado de segurança do
  notebook (a saída UDP do notebook seria permitida de qualquer jeito: `DefaultOutboundAction NotConfigured` = Allow).
- **Real** (central, `adb devices`, platform-tools 37.0.1): `127.0.0.1:15555…15565` aparecem como `offline`
  (a porta local do `-L` aceita a conexão; o emulador do outro lado está parado), `emulator-5554/5556/5558/5562/5564`
  como `device`. Coerente com `transport_state: up` do worker (`GET /api/workers`: `connected: true`, `state:
  degraded` só por "relógio desalinhado: +5.4 s", `agent_outdated: true` — agente `e26a469`, esperado `6997091`, que
  é um commit só de docs).
- `docs/worker.md` registra que o emulador escuta ADB **só em 127.0.0.1** nas duas máquinas (medido 19/09/2026), por
  isso o túnel é requisito — [worker-tunnel.ps1:6-9](C:\git\android\scripts\worker-tunnel.ps1).
- O doc do domínio já reconhece o limite: "uma regra com `-RemoteAddress LocalSubnet` só vale para o notebook na
  mesma sub-rede do central" — [parque.md:642-643](C:\git\android\docs\dominios\parque.md).
- Pesquisa irmã: "Aparelhos do notebook ganham com endpoint externo: o cliente inicia UDP de saída para o VPS, então
  a regra de firewall do central deixa de ser necessária" — [saida-distinta-por-aparelho.md](saida-distinta-por-aparelho.md)
  (Pergunta 1, Inferences). Opções (a) VPS por aparelho, (b) 1 VPS com N IPv4, (c) proxy estático — idem, Pergunta 2.
- O cliente é um endpoint `wireguard` do sing-box no SFA com `peers[].address = endpoint_lan` e
  `persistent_keepalive_interval: 25` (`keepalive: int = 25`) — [rede_aplicacao.py:82-87,263-268](C:\git\android\backend\app\devices\rede_aplicacao.py).
  Doc: "WireGuard persistent keepalive interval, in seconds. Disabled by default" —
  [sing-box WireGuard Endpoint](https://sing-box.sagernet.org/configuration/endpoint/wireguard/).

### Inferences

- **Na mesma LAN (o caso de hoje):** o pacote sai do emulador (NAT do QEMU/slirp) → pilha do notebook → Wi-Fi
  192.168.1.19 → roteador/AP → Wi-Fi do central 192.168.1.81 → firewall Public → sing-box. O NAT do emulador é
  transparente para UDP de saída com resposta (é o mesmo NAT que serve o `10.0.2.2`). Confiança alta na cadeia;
  **não provada** (nenhum remoto rodou com a rede).
- **Isolamento de clientes no AP:** se o roteador `Padilha_5G` tiver "AP/client isolation" ligado, dois clientes Wi-Fi
  não se falam e a regra de firewall não resolve. O ping falhar hoje é explicável pelo firewall, mas não separa as
  duas causas. O SSH do central para o notebook (192.168.1.81 → 192.168.1.19:22) funciona, e ambos estão na Wi-Fi,
  então isolamento total está descartado; isolamento só do sentido inverso não existe em APs comuns. Confiança
  média-alta.
- **Fora da LAN:** precisaria de (i) endpoint público do central (IP do provedor, hoje `38.211.146.161` segundo o
  handoff, e provavelmente dinâmico), (ii) encaminhamento UDP 51820 no roteador, (iii) regra `-RemoteAddress` com
  o IP do worker remoto ou `Any`, (iv) DDNS ou reprovisão a cada mudança. Tudo isso contraria a decisão P2 ("sem
  mudar a rede ou o firewall do sistema" além da regra pontual) e expõe o servidor à internet. Como alternativa
  sem furo de entrada: um túnel SSH extra com `-R` UDP não existe (SSH só encaminha TCP), então "carregar o
  WireGuard pelo túnel" não é opção. O caminho limpo é o VPS da pesquisa irmã: o cliente de qualquer worker inicia UDP
  de SAÍDA para o VPS; o central some da equação de firewall e de DHCP. Confiança alta.
- Uma regra `LocalSubnet` no central **não é evidência** para nenhum worker fora da /24: nem mesmo para um segundo
  worker em outra VLAN/sub-rede da mesma casa. Confiança alta.

### Gaps

- Não medi o roteador (isolamento de clientes, tipo de NAT, IP público estático ou não): exigiria acesso à interface
  do roteador, fora do escopo.
- Nenhum worker fora da LAN existe para medir; a seção é desenho, não prova.

## Pergunta 3 — Comandos de diagnóstico só-leitura (saídas brutas) e o que cada uma diz

### Takeaway
As leituras reais fecham o quadro: o servidor escuta na UDP 51820 em todas as interfaces; o central está na Wi-Fi
Public com entrada Block e sem regra; o notebook está na mesma /24, com o túnel de pé e os emuladores parados; o
caminho UDP notebook→central está fechado hoje (ping recusado). Os comandos abaixo podem ser repetidos a qualquer
momento sem efeito.

### Cited Findings

Central (PowerShell 7, sem privilégio de escrita, 30/09 ~10:41–10:45Z), saídas resumidas fielmente:

```text
Get-NetConnectionProfile        → Padilha_5G | Wi-Fi | ifIndex 19 | Public | IPv4 Internet | IPv6 Internet
Get-NetFirewallProfile          → Domain/Private/Public: Enabled True; DefaultInbound NotConfigured; DefaultOutbound NotConfigured
Get-NetIPAddress -AddressFamily IPv4
                                → vEthernet (WSL) 172.26.32.1/20 Manual | Ethernet 169.254.56.131/16 Tentative (Disconnected)
                                  Wi-Fi 192.168.1.81/24 Dhcp (ValidLifetime 12:35:37) | Loopback 127.0.0.1/8
Get-NetRoute 0.0.0.0/0          → ifIndex 19 Wi-Fi → 192.168.1.1, RouteMetric 0, InterfaceMetric 30 (única)
Get-NetIPInterface (IPv4)       → WSL 34 Dhcp Disabled Connected m5000 | Ethernet 15 Dhcp Enabled Disconnected m5
                                  Wi-Fi 19 Dhcp Enabled Connected m30 | Loopback 1
Regras Inbound+Enabled UDP {51820|Any} (ActiveStore)
                                → 61 filtros de porta candidatos; nenhuma regra para 51820 por número; nenhuma com programa sing-box;
                                  as de LocalPort Any são de svchost/mdeserver/System/Firefox/TeamViewer/Lync/Teams/Copilot
Get-NetUDPEndpoint -LocalPort 51820
                                → :: 51820 PID 21776 sing-box | 0.0.0.0 51820 PID 21776 sing-box
Get-Process sing-box            → 21776, C:\git\android\data\rede\sing-box-1.14.2-windows-amd64\sing-box.exe, StartTime 29/09/2026 23:51:36
ipconfig /all (Wi-Fi)           → MAC AC-45-EF-2C-22-FC; DHCP Yes; lease 23/09 20:15:15 → 30/09 20:15:23; DHCP server 192.168.1.1
GET /api/health                 → degraded (só ai_balance_low); commit 69970916; migration 058_chaves_de_rede
GET /api/network/server         → running true, pid 21776, in_sync true, 10.66.0.1/24, UDP 51820, proxy null,
                                  peers 02/.3, 03/.5, 05/.2, 06/.4 (remote false, last_connection 07:37–07:40 -0300);
                                  remote_access: lan_endpoint 192.168.1.81, remote_peers [], firewall null
POST /api/network/server/firewall-check
                                → sem_regra | Public | Wi-Fi | endpoint_is_local true | comando com -Program '...1.14.2...' -RemoteAddress LocalSubnet -Profile Public
GET /api/workers (worker-lan-01)→ agent 0.1.0+e26a469 (esperado +6997091, agent_outdated true), verbs create/start/stop/restart/reset/emulator_log
                                  (SEM hibernate/wake), connected true, transport_state up desde 24/09, state degraded (relógio +5.4 s)
adb devices                     → 127.0.0.1:15555/15557/15559/15561/15563/15565 offline; emulator-5554/5556/5558/5562/5564 device
```

Saídas brutas dos dois itens centrais da auditoria (central, 30/09):

```text
PS> Get-NetUDPEndpoint -LocalPort 51820 | Select LocalAddress,LocalPort,OwningProcess,Proc

LocalAddress LocalPort OwningProcess Proc
------------ --------- ------------- ----
::               51820         21776 sing-box
0.0.0.0          51820         21776 sing-box
```

```json
POST http://127.0.0.1:8000/api/network/server/firewall-check  (10:44:23Z)
{
  "lan_endpoint": "192.168.1.81",
  "wireguard_udp_port": 51820,
  "remote_peers": [],
  "firewall": {
    "state": "sem_regra",
    "detail": "firewall ligado no perfil Public com entrada padrão Block e nenhuma regra que permita UDP 51820 ao sing-box",
    "endpoint": "192.168.1.81",
    "profile": "Public",
    "interface": "Wi-Fi",
    "endpoint_is_local": true,
    "allowing_rules": [],
    "blocking_rules": [],
    "commands": [
      "New-NetFirewallRule -DisplayName 'Central de Aparelhos - rede por aparelho (WireGuard UDP 51820)' -Direction Inbound -Action Allow -Protocol UDP -LocalPort 51820 -Program 'C:\\git\\android\\data\\rede\\sing-box-1.14.2-windows-amd64\\sing-box.exe' -RemoteAddress LocalSubnet -Profile Public"
    ],
    "checked_at": "2026-09-30T10:44:23.697Z"
  }
}
```

Notebook (SSH `Administrator@192.168.1.19`, chave `worker_ed25519`, `-o BatchMode=yes`, Windows PowerShell 5.1 por
`-EncodedCommand`; sem `&&`), 30/09 ~10:45–10:48Z:

```text
Get-NetIPAddress -AddressFamily IPv4
                                → vEthernet (WSL) 172.28.208.1/20 | Local Area Connection* 10/11 e Ethernet: APIPA Tentative
                                  Wi-Fi 192.168.1.19/24 Dhcp Preferred | Loopback
Get-NetConnectionProfile        → Padilha_5G 2 | Wi-Fi | Public | IPv4 Internet
Get-NetRoute 0.0.0.0/0          → ifIndex 8 Wi-Fi → 192.168.1.1
Test-NetConnection 192.168.1.81 -Port 8010
                                → TcpTestSucceeded False; PingSucceeded False; SourceAddress 192.168.1.19
Test-NetConnection 127.0.0.1 -Port 18000
                                → TcpTestSucceeded True  (o -L do agente até o 8010 do central está de pé)
Get-NetUDPEndpoint -LocalPort 51820
                                → (vazio)
Get-NetFirewallProfile          → Domain True | Private True | Public FALSE (desligado); DefaultInbound/Outbound NotConfigured
Get-Process emulator,qemu-system-x86_64,adb
                                → só adb (18744)
worker.yaml (filtrado)          → appium: central; ram_per_device_mb 4300; min_free_ram_mb 4096;
                                  system_image android-34 google_apis x86_64; gpu_mode swiftshader_indirect; ram_mb 3072; -lowram;
                                  dns_servers ["192.168.1.1", "8.8.8.8"] (K-025: o DHCP entregava um DNS morto); sem chave hibernation/rede/proxy
```

Fontes: saídas dos comandos acima (real); `GET /api/workers` e `GET /api/instances` (real). O comando SSH usado
para o notebook: `ssh -i ~/.ssh/worker_ed25519 -o BatchMode=yes Administrator@192.168.1.19 "powershell -NoProfile
-NonInteractive -EncodedCommand <base64 UTF-16LE do script>"` (evita `$_` ser expandido pelo Git Bash — a primeira
tentativa com aspas falhou exatamente por isso).

### Inferences

- `Test-NetConnection` só prova TCP e ICMP; a única prova do UDP 51820 é o `inbound connection from 10.66.0.N` no log
  do servidor (`_CONEXAO`, [rede_servidor.py:255-256](C:\git\android\backend\app\devices\rede_servidor.py)) ou uma
  captura. Um diagnóstico só-leitura do UDP a partir do notebook seria `Test-NetConnection` com `-Port` e
  `-InformationLevel Detailed`... que não faz UDP; alternativa sem efeito: `Get-NetUDPEndpoint` no central antes e
  depois (não muda) — isto é, **não há teste só-leitura do UDP**; o teste é o handshake do WireGuard. Confiança alta.
- O `agent_outdated: true` é só por `6997091` ser commit de docs sobre `e26a469`; não há código novo pendente no agente.
  Confiança alta (`git log`).

### Gaps

- Comentário desatualizado no `worker.yaml` do notebook: "O agente alcança o central pelo túnel REVERSO (18000 aqui
  -> 8000 no central)"; o túnel real mapeia `18000 → 8010` (`-MapaReverso '18000:8010'`,
  [worker-tunnel.ps1:50-57](C:\git\android\scripts\worker-tunnel.ps1)). Só o comentário está errado (o `-L` no
  18000 responde, real); vale corrigir na próxima atualização do agente.
- Não rodei `Get-NetFirewallRule` com `-PolicyStore RSOP`/política de grupo separada: `PolicyStoreSourceType` das
  regras listadas era `Local`, e não há indício de GPO nesta máquina, mas não foi confirmado com `gpresult`.

## Pergunta 4 — DHCP: como estabilizar o endpoint (reserva no roteador, mDNS, DNS interno, releitura pela plataforma)

### Takeaway
A reserva de DHCP no roteador para o MAC `AC-45-EF-2C-22-FC` (Wi-Fi do central) é a única opção que estabiliza o
endpoint sem mexer em código nem depender de resolução de nomes no aparelho; nome mDNS não é caminho (o sing-box do
Android resolve por DNS configurado, não por multicast), DNS interno cria dependência circular com o DNS sequestrado
pelo túnel, e "reprovisionar quando o IP mudar" é possível mas é um item de plataforma (hoje exige Reaplicar manual).

### Cited Findings

- Lease atual do central: obtido 23/09 20:15:15, expira 30/09 20:15:23, servidor DHCP 192.168.1.1 (real, `ipconfig /all`).
  O `Get-NetIPAddress` mostra `ValidLifetime 12:35:37` às ~10:41Z, coerente com a expiração de hoje à noite. O DHCP
  do roteador já mudou comportamento antes (K-025: "o DHCP do roteador entrega 1.178.36.77 (morto) antes de 8.8.8.8",
  no `worker.yaml` do notebook — real, filtrado).
- `endpoint_host` do perfil aceita "um nome de host, um IPv4 ou um IPv6" — [rede.py:80,104,117](C:\git\android\backend\app\devices\rede.py);
  o `endpoint_lan` é validado como IP (nem 10.0.2.2, loopback, link-local, multicast ou dentro da sub-rede do túnel) —
  [parque.md:595-599](C:\git\android\docs\dominios\parque.md); `avaliar()` trata um nome como "não dá para achar a
  interface por ele" — [rede_firewall.py:191-196](C:\git\android\backend\app\devices\rede_firewall.py).
- No sing-box 1.14, endpoint/outbound com domínio no endereço do servidor precisa de `domain_resolver` (Dial Fields,
  "Since sing-box 1.14.0 ... required for outbound/endpoints using domain names in server addresses"); `bind_interface`,
  `inet4_bind_address` e `detour` existem nos Dial Fields — [sing-box Dial Fields](https://sing-box.sagernet.org/configuration/shared/dial/).
  O perfil do cliente gerado pela plataforma define um único DNS `udp` para `plano.dns` (1.1.1.1 por padrão) com
  `detour: wg-out` quando há VPN, e `route.default_domain_resolver: "dns-remoto"` —
  [rede_aplicacao.py:261-266,291-297](C:\git\android\backend\app\devices\rede_aplicacao.py), [config.example.yaml:341](C:\git\android\config\config.example.yaml).
- O `endpoint_lan` não entra na assinatura do servidor (trocá-lo não reinicia o sing-box) —
  [rede_servidor.py:127](C:\git\android\backend\app\devices\rede_servidor.py) (`exclude={"binario", "log_max_mb", "endpoint_lan"}`),
  teste `test_endpoint_lan_nao_muda_a_assinatura_do_servidor` — [test_rede_worker.py:168](C:\git\android\backend\tests\test_rede_worker.py).
- O emulador do notebook usa `-dns-server 192.168.1.1,8.8.8.8` (`dns_servers` do `worker.yaml`, real); o emulador só
  usa o primeiro DNS IPv4 do host por padrão (comentário no `worker.yaml`, K-025).

### Inferences

- **mDNS (`central.local`)**: o cliente sing-box no Android resolveria `central.local` pelo `domain_resolver`, que é o
  DNS 1.1.1.1 **pelo túnel** (`detour: wg-out`) — um túnel que ainda não subiu porque precisa do endereço. Dependência
  circular; e o 1.1.1.1 não conhece `.local`. Mesmo com um resolver "direct", o sing-box não faz mDNS (não há tal
  transporte na doc de DNS lida). **Descartar.** Confiança alta.
- **Nome em DNS interno (roteador)**: o mesmo problema circular, a menos que o perfil ganhe um segundo servidor DNS
  `direct` só para o nome do endpoint (mudança de código no `config_do_cliente` + validação do `endpoint_lan` aceitar
  nome). Além disso, o emulador do notebook resolve pelo `-dns-server` do host, e o DNS do roteador já se mostrou
  instável (K-025). **Não recomendo** para o piloto. Confiança média-alta.
- **Reserva DHCP no roteador (ação do dono)**: amarra 192.168.1.81 ao MAC `AC-45-EF-2C-22-FC`; nada muda na plataforma,
  a regra continua válida e os perfis importados continuam válidos. É a recomendação. Evidência da mudança feita:
  `ipconfig /all` com o mesmo IP depois de `ipconfig /renew` (ou depois de 20:15 de hoje) e, idealmente, a tela do
  roteador. Risco: o roteador ser trocado/resetado; mitigação: registrar em `docs/operacao.md` e no handoff. Confiança alta.
- **IP estático na interface do central**: também funciona, mas é "mexer na rede do sistema" (P2) e conflita com o
  DHCP se a faixa não for reservada. Só se o dono preferir. Confiança alta.
- **Plataforma reler o IP e reprovisionar** (item futuro): (1) o leitor já sabe quando `endpoint_is_local` vira
  `False` (o IP saiu das interfaces) — daria para levar isso a `/api/health` como problema `lan_endpoint_stale`;
  (2) reprovisionar exige `Reaplicar` em cada remoto (`desired_rev+1`) e, pela receita, importar perfil novo e
  reiniciar o aparelho — o aparelho com `lockdown=1` fica sem rede até lá, e um aparelho com conta real precisa da
  autorização por aparelho (ADR-056 §7). O cliente **não** descobre o endpoint novo sozinho: WireGuard só faz
  roaming do lado que RECEBE (o servidor aprende o IP de origem do par), nunca do endpoint que o cliente disca.
  Confiança alta na mecânica; o desenho é proposta.

### Gaps

- Não confirmei na doc do sing-box se `domain_resolver` pode apontar um servidor DNS `direct` só para o endpoint sem
  quebrar o `hijack-dns` do túnel (seria a única forma de nome funcionar); não é necessário se a reserva DHCP for feita.
- Não há como saber, sem a interface do roteador, se ele suporta reserva por MAC (roteadores domésticos comuns
  suportam).

## Pergunta 5 — O caminho do executável na regra (`-Program`) versus regra sem programa; e o perfil Public/Private

### Takeaway
Recomendo trocar a fragilidade do `-Program` versionado por escopo de rede explícito: regra **sem `-Program`**,
com `-RemoteAddress 192.168.1.0/24` (ou `LocalSubnet4`), `-InterfaceAlias Wi-Fi`, `-Profile Any` — o que se perde
(qualquer processo do central pode escutar a UDP 51820 vinda da LAN) é pequeno perto do que se ganha (a regra
sobrevive à atualização do sing-box e à reclassificação da rede), e a plataforma continua lendo `liberado` porque
`Program Any` casa. Se o dono preferir manter `-Program`, a alternativa é uma junção estável
(`data\rede\sing-box-atual`) apontando para a pasta da versão, com o `config.yaml` usando o caminho estável.

### Cited Findings

- `_mesmo_programa` devolve `True` para programa vazio ou `Any` — [rede_firewall.py:147-151](C:\git\android\backend\app\devices\rede_firewall.py);
  `_cobre_o_perfil` devolve `True` para `Any` — [rede_firewall.py:165-167](C:\git\android\backend\app\devices\rede_firewall.py).
  Teste `test_permissao_so_conta_no_perfil_na_porta_e_no_programa_certos` — [test_rede_worker.py:104](C:\git\android\backend\tests\test_rede_worker.py).
- `-Profile`: "The rule is active on the local computer only when the specified profile is currently active ... default
  value is Any"; `-InterfaceAlias` e `-InterfaceType Wireless` existem —
  [New-NetFirewallRule](https://learn.microsoft.com/en-us/powershell/module/netsecurity/new-netfirewallrule).
- `LocalSubnet4` restringe o keyword ao IPv4 — mesma fonte.
- A rede do central é `Public` hoje (real); a leitura de 29/09 22:12Z também era Public — [parque.md:625-627](C:\git\android\docs\dominios\parque.md).
- O sing-box só escuta a UDP 51820 enquanto há aparelho atribuído a perfil `servidor: central`; "Sem nenhum aparelho
  pedindo o servidor, ele não roda" — [rede_servidor.py:26-28](C:\git\android\backend\app\devices\rede_servidor.py).
- O caminho do binário é por instalação (`config.yaml`, fora do Git) e a pasta `data/rede/` também —
  [config.example.yaml:343](C:\git\android\config\config.example.yaml). O projeto já usa junções para SDK/venv
  (memória: "junções venv/node_modules", "SdkBeta ... com junções para as imagens").

### Inferences

- **Trade-off do `-Program`:** ele limita a permissão a UM executável; sem ele, qualquer processo que abra a UDP 51820
  no central recebe pacotes da LAN. Como só o sing-box gerenciado usa a porta, e a origem fica restrita à /24 da Wi-Fi
  (e à interface), o ganho de segurança do `-Program` é marginal; o custo (quebrar em cada atualização, com todos os
  remotos caindo até o dono agir) é concreto. Confiança alta.
- **Junção estável**: `New-Item -ItemType Junction data\rede\sing-box-atual -Target data\rede\sing-box-1.14.2-windows-amd64`
  e `rede.servidor.binario: data/rede/sing-box-atual/sing-box.exe`. Atenção: o Windows Firewall resolve o caminho
  do processo em execução como o **caminho real** (o kernel abre o arquivo pelo alvo da junção); logo `-Program` com
  o caminho da junção **pode não casar** o processo. Não confirmei em doc; é motivo a mais para preferir a regra sem
  `-Program`. Confiança média (marcar como a verificar). **Consequência, se for assim:** o leitor da plataforma
  compara o `binario` configurado por `os.path.abspath` (que não resolve pontos de reanálise) com o `Program` da
  regra como texto ([rede_firewall.py:147-151,300](C:\git\android\backend\app\devices\rede_firewall.py)); com a
  regra apontando para a junção, a plataforma diria **`liberado` enquanto o Windows descarta os pacotes** — a única
  variante em que a leitura só-leitura e a realidade divergem em silêncio, exatamente a classe de falha que os
  invariantes do projeto proíbem. A regra sem `-Program` elimina essa divergência.
- **Perfil:** `-Profile Public` está certo hoje. Se o dono marcar a rede como Private, a regra fica inativa, o
  leitor mapeia a interface para `Private`, volta `sem_regra` e propõe um comando novo com `-Profile Private` — a
  falha é visível, mas derruba os remotos. `-Profile Any` com origem/interface restrita cobre os dois casos sem
  ampliar a exposição (a restrição real está no `RemoteAddress`/interface, não no perfil). Confiança alta.
- **IPv6:** com `-RemoteAddress 192.168.1.0/24` (ou `LocalSubnet4`) a regra não abre nada em v6, e o `[::]:51820`
  segue fechado pelo Block padrão. Recomendado. Confiança alta.

### Gaps

- Não testei (só leitura) se `-Program` com caminho de junção casa o processo; só há a inferência acima.
- Não há decisão registrada do dono sobre "sem -Program"; a plataforma hoje SUGERE com `-Program` e isso exigiria
  ajuste em `comando_de_liberacao` se a recomendação for adotada como padrão (mudança de código pequena, com teste
  em `test_rede_worker.py`).

## Pergunta 6 — Caminho do aparelho remoto no código: `adb reverse` pelo túnel, serial `127.0.0.1:155xx`, endpoint do cliente, recusa por firewall, `restart` e `sync` no agente

### Takeaway
O caminho está implementado e coerente: o remoto recebe o perfil por `adb reverse tcp:P tcp:P` no serial
`127.0.0.1:155xx` (o HTTP de uso único fica em 127.0.0.1 do central), disca `endpoint_lan:51820`, é recusado se o
firewall está `bloqueado`/`sem_regra`, reinicia pelo verbo `restart` do agente, e o `sync` antes do `emu kill` **já
está** no agente instalado (`e26a469` descende de `81e26f0` e `emulator.py` vai no manifesto). Toda a prova disso é
**simulada** (15 casos em `test_rede_worker.py`); nenhum remoto rodou com a rede.

### Cited Findings

- Serial do remoto: `127.0.0.1:15555…15565` (real, `GET /api/instances`); é o `external_serial` que o `adb connect
  127.0.0.1:<porta>` cria — [docs/worker.md:210-232](C:\git\android\docs\worker.md), [worker-tunnel.ps1:6-9](C:\git\android\scripts\worker-tunnel.ps1).
- `Adb.reverse(port)` roda `adb -s <serial> reverse tcp:P tcp:P` e `remove_reverse` desfaz —
  [adb.py:731-742](C:\git\android\backend\app\devices\adb.py). O `adb` local (platform-tools 37.0.1, `adb help`, real)
  documenta `reverse [--no-rebind] REMOTE LOCAL` com `tcp:<port>` e `reverse --remove REMOTE`. A página oficial do
  adb lida ([developer.android.com/tools/adb](https://developer.android.com/tools/adb)) mostra `adb devices` listando
  `ip:port device` e `adb -s 0.0.0.0:6520 install ...` (serial TCP com `-s`), mas **não** documenta `adb reverse`.
- Provisão do remoto: `host = "127.0.0.1" if ap.external else "10.0.2.2"`; `if ap.external: await ap.reverso(servidor.porta)`;
  o `ServidorDeUmaVez` escuta em `("127.0.0.1", 0)`; ao fim, `desfazer_reverso` (falha só loga) —
  [rede_aplicacao.py:303-356,477-527](C:\git\android\backend\app\devices\rede_aplicacao.py).
- `origem_do_aparelho`: `external` do gerenciador → `worker_id` (ou "externo") — [rede_aplicacao.py:151-157](C:\git\android\backend\app\devices\rede_aplicacao.py);
  `endpoint_do_central`: remoto → `(cfg.servidor.endpoint_lan, porta_wireguard)`; vazio → `RedeAplicacaoError` antes
  de gerar par — [rede_aplicacao.py:160-171](C:\git\android\backend\app\devices\rede_aplicacao.py).
- `_firewall_para_remoto`: relê forçado; `leitura.fechado` (`bloqueado`/`sem_regra`) → `RedeAplicacaoError` com o
  comando; `desconhecido` segue com nota — [rede_convergencia.py:416-431](C:\git\android\backend\app\devices\rede_convergencia.py);
  chamado quando `plano.gerenciado and plano.remoto`, ANTES de o servidor conhecer a chave — [rede_convergencia.py:387-390](C:\git\android\backend\app\devices\rede_convergencia.py).
- Reinício: `_pedir_reinicio` → `despacho.pedir_ciclo_de_vida(st, iid, "restart", ...)`; recusa conta para
  `reinicios_max` — [rede_convergencia.py:744-774](C:\git\android\backend\app\devices\rede_convergencia.py). O
  agente do notebook anuncia `restart` (real, `GET /api/workers` → `verbs`), e `_v_restart` = `_v_stop` + `_v_start(from_snapshot=False)`
  — [executor.py:690-692](C:\git\android\backend\app\worker\executor.py); `_v_stop` chama `emu.stop_process` —
  [executor.py:651-663](C:\git\android\backend\app\worker\executor.py).
- `stop_process` faz `adb.shell("sync", timeout=20)` antes do `emu kill` (motivo: perfil importado 26 s antes do
  restart sumiu, medido 29/09 no android-05) — [emulator.py:129-145](C:\git\android\backend\app\devices\emulator.py).
  `devices/emulator.py` está no manifesto do agente — [worker-manifest.txt](C:\git\android\backend\worker-manifest.txt).
  `git merge-base --is-ancestor 81e26f0 e26a469` = verdadeiro (real, 30/09), e `git show e26a469:backend/app/devices/emulator.py`
  contém `adb.shell("sync", ...)` na linha 142 (real). Logo o agente instalado (`0.1.0+e26a469`) **tem** o sync.
- Comentário desatualizado: "o `stop_process` agora sincroniza também, mas o agente do worker só ganha isso quando for
  atualizado" — [rede_aplicacao.py:14](C:\git\android\backend\app\devices\rede_aplicacao.py). Já foi atualizado.
- Teste da fatia: `test_remoto_disca_o_endereco_da_lan_e_recebe_pelo_adb_reverse`,
  `test_firewall_fechado_recusa_o_remoto_com_o_comando_do_dono`, `test_sem_endpoint_lan_o_remoto_e_recusado_antes_do_par`,
  `test_rotas_mostram_e_releem_o_acesso_remoto_sem_segredo` etc. — [test_rede_worker.py:219-321](C:\git\android\backend\tests\test_rede_worker.py)
  (aparelho remoto e firewall falsos). Registro oficial: "aplicação num aparelho do notebook `not_run`" —
  [parque.md:657](C:\git\android\docs\dominios\parque.md); porém a mesma página diz "o perfil chega por `adb reverse` ... (a prova
  é do 25.7)" — [parque.md:592-594](C:\git\android\docs\dominios\parque.md) e [rede_aplicacao.py:10](C:\git\android\backend\app\devices\rede_aplicacao.py).
- Hibernação: o agente do notebook **não** anuncia `hibernate`/`wake` (`sem_hibernacao(VERBS, bool(cfg.file.android.hibernation))`
  — [agent.py:173](C:\git\android\backend\app\worker\agent.py); `worker.yaml` filtrado não tem `hibernation`, real).

### Inferences

- `adb reverse` é função do transporte adb (o servidor adb do central manda `reverse:forward:...` ao adbd do
  convidado, que abre um listener em `127.0.0.1:P` dentro do Android e devolve cada conexão pelo mesmo transporte).
  Funciona igual sobre um transporte TCP (`127.0.0.1:155xx` pelo `-L`) e sobre `emulator-5554`; o socket de retorno
  atravessa o túnel SSH como qualquer outro comando adb (o `adb forward` do Appium e o `install` de 20 MB já fazem
  isso pelo túnel — [docs/worker.md:47-49](C:\git\android\docs\worker.md)). Confiança alta na mecânica; **not_run**
  neste parque.
- Ordem na provisão: `am force-stop` do SFA ANTES do `adb reverse` — não importa para o reverse; o download do perfil
  é feito pelo SFA pelo `127.0.0.1:P` do convidado, com a VPN parada. Se o aparelho já estivesse com always-on +
  lockdown de uma revisão anterior, o `127.0.0.1` do convidado não passa pelo lockdown (loopback não é roteado pela
  VPN). Confiança média-alta.
- O sing-box do central precisa conhecer a chave pública do remoto antes do primeiro handshake; a convergência grava
  o par e reinicia o servidor "quando o conjunto muda" — os quatro pares locais têm o servidor reiniciado nesse momento
  (assinatura), o que derruba por segundos o túnel dos android-02/03/05/06. Confiança alta (docstring de
  `rede_servidor.py:19-21`); vale avisar antes de atribuir o remoto.
- A frase "(a prova é do 25.7)" nos docs sugere prova real que não existe; o registro correto é o da tabela
  (`not_run`). Confiança alta.

### Gaps

- `adb reverse` sobre transporte TCP com adbd do android-34 não foi exercitado aqui (emuladores do notebook parados;
  ligar é ação no parque, fora do escopo). Fica para o passo 3 do plano.
- Não confirmei se o `permitopen`/`permitlisten` da chave restrita no notebook precisa incluir algo para o
  `reverse` (não precisa: o reverse corre DENTRO da conexão adb já encaminhada; o SSH não vê portas novas) — inferência,
  confiança alta.

## Pergunta 7 — Procedimento futuro de configuração (regra restrita, validação, rollback, evidência esperada), sem desligar firewall nem abrir portas amplamente

### Takeaway
Procedimento em quatro blocos, todo ele **not_run** e para o dono executar num PowerShell de administrador: (0)
estabilizar o IP (reserva DHCP), (1) criar UMA regra de entrada restrita a UDP 51820, à sub-rede da Wi-Fi e à
interface Wi-Fi, (2) confirmar pela plataforma (`firewall-check` → `liberado`), (3) rollback por
`Remove-NetFirewallRule -DisplayName`. Nada aqui desliga perfil, muda `DefaultInboundAction` ou usa
`-RemoteAddress Any`.

### Cited Findings

- Nome da regra que a plataforma reconhece por exibição: `Central de Aparelhos - rede por aparelho (WireGuard UDP
  51820)`; a leitura seguinte a acha "pelo executável e pela porta, não pelo nome" — [rede_firewall.py:49-51](C:\git\android\backend\app\devices\rede_firewall.py).
- `POST /api/network/server/firewall-check` relê já e devolve `remote_access` — [parque.md:622](C:\git\android\docs\dominios\parque.md).
- Estado `liberado` = "uma regra habilitada de entrada permite UDP na porta ao executável (ou Any)" — [rede_firewall.py:14](C:\git\android\backend\app\devices\rede_firewall.py).
- Sintaxe dos parâmetros (`-RemoteAddress` sub-rede por bits, `-InterfaceAlias`, `-Profile Any`) —
  [New-NetFirewallRule](https://learn.microsoft.com/en-us/powershell/module/netsecurity/new-netfirewallrule).
- Backups do `config.yaml` ficam em `data/backups/config.yaml.antes-*` — [terceira-evolucao.md:239-240](C:\git\android\docs\handoffs\terceira-evolucao.md).

### Inferences

**O procedimento proposto (not_run).**

Valores confirmados: sub-rede `192.168.1.0/24`, interface `Wi-Fi`, IP atual `192.168.1.81`, MAC `AC-45-EF-2C-22-FC`,
porta `51820`, binário atual `C:\git\android\data\rede\sing-box-1.14.2-windows-amd64\sing-box.exe`. Placeholders:
`<IP-do-notebook>` = 192.168.1.19 hoje (também DHCP).

**Bloco 0 — estabilizar o endpoint (roteador, ação do dono).** Reserva DHCP: MAC `AC-45-EF-2C-22-FC` → `192.168.1.81`
(e, opcional, o notebook → `192.168.1.19`). Evidência: `ipconfig /all` do central depois de `ipconfig /renew` (ou
após 20:15 de hoje) ainda com 192.168.1.81 e o lease novo. Sem isso, o procedimento vale até o próximo IP.

**Bloco 1 — a regra (PowerShell de administrador no central).** Opção recomendada (sem `-Program`, restrita à
sub-rede e à interface, imune à reclassificação do perfil):

```powershell
# Ensaio: nada é criado; só confere que não existe regra com esse nome
Get-NetFirewallRule -DisplayName 'Central de Aparelhos - rede por aparelho (WireGuard UDP 51820)' -ErrorAction SilentlyContinue

New-NetFirewallRule -DisplayName 'Central de Aparelhos - rede por aparelho (WireGuard UDP 51820)' `
  -Description 'UDP do WireGuard dos aparelhos do worker da LAN (ADR-056, 25.7). Só a sub-rede da Wi-Fi, só a interface Wi-Fi.' `
  -Direction Inbound -Action Allow -Protocol UDP -LocalPort 51820 `
  -RemoteAddress 192.168.1.0/24 -InterfaceAlias 'Wi-Fi' -Profile Any -Enabled True
```

Variante mais estrita (só o notebook): `-RemoteAddress 192.168.1.19` — exige reserva DHCP do notebook também.
Variante fiel à plataforma (a proposta original) — funciona hoje, com as fragilidades da Pergunta 5:
`... -Program 'C:\git\android\data\rede\sing-box-1.14.2-windows-amd64\sing-box.exe' -RemoteAddress LocalSubnet -Profile Public`.

**Bloco 2 — validação da regra (só leitura).**

```powershell
Get-NetFirewallRule -DisplayName 'Central de Aparelhos - rede por aparelho (WireGuard UDP 51820)' |
  Format-List DisplayName,Enabled,Profile,Direction,Action,PolicyStoreSourceType
Get-NetFirewallRule -DisplayName 'Central de Aparelhos - rede por aparelho (WireGuard UDP 51820)' | Get-NetFirewallPortFilter
Get-NetFirewallRule -DisplayName 'Central de Aparelhos - rede por aparelho (WireGuard UDP 51820)' | Get-NetFirewallAddressFilter
Get-NetFirewallRule -DisplayName 'Central de Aparelhos - rede por aparelho (WireGuard UDP 51820)' | Get-NetFirewallInterfaceFilter
```

Esperado: `Enabled True`, `Profile Any`, `Protocol UDP`, `LocalPort 51820`, `RemoteAddress 192.168.1.0/24`,
`InterfaceAlias Wi-Fi`. Depois, `curl -s -X POST http://127.0.0.1:8000/api/network/server/firewall-check` →
`firewall.state: "liberado"`, `allowing_rules: ["Central de Aparelhos - rede por aparelho (WireGuard UDP 51820)"]`,
`profile: "Public"`, `endpoint_is_local: true`. (A plataforma não confere `RemoteAddress`/interface — Pergunta 1 —,
por isso o `Get-NetFirewallAddressFilter` acima é parte da evidência.) Do notebook, `Test-NetConnection 192.168.1.81`
continua com `PingSucceeded False`: a regra é só UDP 51820, e isso é o esperado — não é sinal de falha.

**Bloco 3 — rollback.**

```powershell
Remove-NetFirewallRule -DisplayName 'Central de Aparelhos - rede por aparelho (WireGuard UDP 51820)'
```

E, antes, desatribuir a rede dos remotos (`POST /api/network/assign` com `vpn_profile_id: null` explícito — a convergência
desfaz always-on e lockdown e pede o reinício, [rede_convergencia.py:433-448](C:\git\android\backend\app\devices\rede_convergencia.py));
`firewall-check` volta a `sem_regra`. Nenhum passo desliga perfil nem altera `DefaultInboundAction`.

**Registro:** `docs/operacao.md` (§ do firewall) com o comando final, a data e o `firewall-check` de antes e depois;
`docs/estado-atual.md`. Se a regra sem `-Program` for adotada, ajustar `comando_de_liberacao` para propor a mesma
forma (senão a plataforma seguirá sugerindo a versão com `-Program` a quem ler o painel).

### Gaps

- Não executado (só leitura): a regra não foi criada e o `liberado` não foi observado. Prova `not_run`.
- Se a rede for reclassificada para Private no futuro, a regra `-Profile Any` continua ativa; o leitor passará a
  avaliar o perfil Private — deve continuar `liberado` (regra `Any` cobre) — a confirmar então.

## Pergunta 8 — Plano de validação completo (proposto, não executado): do aparelho do notebook até a saída pública, com TCP/UDP/DNS, queda do túnel e recuperação, e rollback

### Takeaway
O plano tem oito passos, cada um com o comando/rota, a evidência esperada e o critério de falha; começa pelo
`firewall-check` e por um aparelho do notebook **sem conta real** (android-09…15 são de QA, `stopped` hoje), passa
pela provisão (`adb reverse` real), pelo handshake (`inbound connection from 10.66.0.N`), pela medição da sonda (IP de
saída = IP público do central, DNS e NTP por UDP), pela queda menos destrutiva (modo avião/`svc wifi` no convidado — o
notebook não tem `hibernate`/`wake`) e pela recuperação (`restart` do agente), e termina no rollback. Saúde do backend
ou um handshake isolado não bastam: só `trafego_verificado` com `network_measurements` gravada fecha.

### Cited Findings

- Estados e transições: `pendente → configurado → conectado → trafego_verificado | parcial`; a medição da sonda é a
  única que leva a `trafego_verificado`; `conectado` de um remoto sem conexão no log diz o endpoint e o firewall —
  [rede_convergencia.py:1-18](C:\git\android\backend\app\devices\rede_convergencia.py), [parque.md:614-618](C:\git\android\docs\dominios\parque.md).
- Sonda (25.5): eco de IP por HTTP na 80 (`api.ipify.org`, `ipv4.icanhazip.com`; v6 idem), DNS UDP de ida e volta
  (`8.8.4.4`), NTP (`time.google.com`) — `udp_ok` só com os dois; cobertura por UID com `dumpsys netstats` —
  [config.example.yaml:352-360](C:\git\android\config\config.example.yaml), [terceira-evolucao.md:242-246](C:\git\android\docs\handoffs\terceira-evolucao.md).
- Evidência do servidor: `INFO endpoint/wireguard[wg-srv]: inbound connection from <ip>:<porta>` → `peers[].last_connection` —
  [rede_servidor.py:255-256,489-503](C:\git\android\backend\app\devices\rede_servidor.py).
- Observação no aparelho como uid 2000: `always_on`, `lockdown`, `tun`, `vpn_conectada` (VPN CONNECTED no `dumpsys`),
  `regras_de_bloqueio`, `uptime` — [rede_aplicacao.py:560-624](C:\git\android\backend\app\devices\rede_aplicacao.py).
- Os aparelhos do notebook: android-09/10/12/13/14/15, `api_level 34`, `stopped` (real, `GET /api/instances`); só 3
  contas IG vivas (lucas, bruno, andre) nos aparelhos do central (memória; android-03 e 06 com conta real).
- Exemplo real de resultado no central: android-05 e android-02 em `trafego_verificado`, saída `38.211.146.161` "o IP
  do central" — [terceira-evolucao.md:236-237](C:\git\android\docs\handoffs\terceira-evolucao.md).
- O teste de vazamento para o cliente e reinicia o aparelho; não é feito em "aparelho que a plataforma não reinicia
  de verdade" — [rede_convergencia.py:610-623](C:\git\android\backend\app\devices\rede_convergencia.py).
- Verbos do notebook: sem `hibernate`/`wake` (real). Ligar aparelho, atribuir rede e reiniciar são ações no parque
  (permitidas no ambiente central pelo CLAUDE.md, mas fora do escopo desta pesquisa só-leitura).
- A saída pública dos remotos pelo túnel é a do central (o `direct` do sing-box do central), não a do notebook —
  [rede_servidor.py:140-149](C:\git\android\backend\app\devices\rede_servidor.py) (`final: direct`).

### Inferences

**O plano (not_run).**

| # | Passo | Como | Evidência esperada | Falha = |
|---|---|---|---|---|
| 0 | Pré-condições | `firewall-check` → `liberado`; `GET /api/network/server` `running true`; `GET /api/workers` `transport_state up`; `adb devices` mostra `127.0.0.1:155xx` | tudo verde; anotar `checked_at`, commit, hora | qualquer `sem_regra`/`bloqueado`/túnel `down` |
| 1 | Ligar um remoto de QA | `POST /api/instances/android-09/actions/start` ([api.py:2540](C:\git\android\backend\app\api.py); verbo `start` do agente) | `state online`, `adb -s 127.0.0.1:15555 shell getprop sys.boot_completed` = 1 | boot > prazo; `offline` |
| 2 | Atribuir a rede | `POST /api/network/assign` (`instance_ids: ["android-09"]`, `vpn_profile_id` de um perfil com `params.servidor: "central"`, `policy: "exigida"`; [api.py:2071](C:\git\android\backend\app\api.py)) (não `exigida_com_bloqueio` na primeira vez: se algo falhar, o aparelho não fica sem rede) | `device_network` → `pendente` (rev N); evento "Rede de android-09: ..." | 409/422 (sem `endpoint_lan`, sem consentimento) |
| 3 | Provisão pelo `adb reverse` | a convergência aplica (ou `POST /api/network/devices/android-09/apply`) | `detail` com "1 GET no HTTP de uso único, adb reverse", "firewall do central para a LAN: liberado", `always-on=io.nekohasekai.sfa, lockdown=0`; `adb reverse --list` vazio depois; log do servidor com o par novo e `signature` mudada (os 4 locais reconectam) | `pendente` com `error` citando o firewall (o comando) ou "não baixou o perfil" |
| 4 | Reinício e handshake | `restart` pedido pela convergência (verbo do agente; `sync` antes do kill já no `e26a469`) | comando `restart` no histórico do android-09 com `restarted true`; depois do boot, `configurado → conectado`; **`inbound connection from 10.66.0.6` (ou o endereço dado) no log do servidor** e `peers[].last_connection` com `remote: true`; `dumpsys connectivity` via `adb -s 127.0.0.1:15555 shell` com `VPN CONNECTED` e `tun0` | `conectado` sem `last_connection` (endpoint/firewall/isolamento do AP); 2 reinícios e `pendente` |
| 5 | Saída pública e protocolos | a convergência mede (ou `POST /api/network/devices/android-09/verify`) | linha em `network_measurements`: `egress_ip` = IP público do central (o mesmo `38.211.146.161` dos locais, se ainda for), `dns_ok true`, `udp_ok true` (DNS + NTP), IPv6 preso (strict_route); estado `trafego_verificado`; `dumpsys netstats` cobre o UID do app exigido | `parcial` (`udp_ok false` → NAT do emulador ou keepalive), `egress_ip` = IP do notebook (vazamento) |
| 6 | Queda do caminho (menos destrutivo) | no convidado: `adb -s 127.0.0.1:15555 shell cmd connectivity airplane-mode enable` (ou `svc wifi disable`), esperar ~60 s, `... airplane-mode disable` | durante: `tun0` fica, `vpn_conectada` cai ou `last_connection` para de avançar; depois: nova `inbound connection` no log sem reprovisão (o cliente rediscá o mesmo endpoint) e o estado volta a `conectado`/mantém `trafego_verificado` na releitura (`deriva_s`) | não volta em `espera_tun_s` → convergência reinicia; se nem assim, `pendente` |
| 6b | Queda do túnel SSH (só se autorizado) | matar a tarefa `farm-tunel-worker-lan-01` **não** é necessário: o túnel SSH não carrega o WireGuard; a queda dele afeta ADB/agente, não a VPN. Se o dono quiser provar isso, é a única evidência que exige autorização (mexe no túnel) | com o túnel fora, `last_connection` do remoto continua avançando (keepalive 25 s) enquanto o `health` acusa `tunnel_down` | `last_connection` para = a VPN dependia do túnel (não deveria) |
| 7 | Recuperação por `restart` | `POST /api/instances/android-09/actions/restart` | mesmo do passo 4, em menos de `espera_tun_s` (60 s) + boot; `uptime` reiniciado; perfil ainda presente (o `sync` do agente) | perfil sumiu (`profiles.db` vazio) = o sync não valeu no agente |
| 8 | Rollback | desatribuir a rede (`POST /api/network/assign` com `vpn_profile_id: null` explícito — `atribuir` só muda o que vem no corpo, [rede.py:618-640](C:\git\android\backend\app\devices\rede.py)); `Remove-NetFirewallRule -DisplayName ...` | `device_network` removido, always-on `null`, reinício; `firewall-check` → `sem_regra`; servidor sem o par (assinatura muda de novo) | linha presa em `configurado` (reinício recusado) |

Notas do plano:
- **Bloqueio (`exigida_com_bloqueio`)** só depois do passo 5 verde: com `lockdown=1` e sem handshake, o aparelho fica
  sem rede, e o teste de vazamento reinicia o aparelho. Confiança alta.
- **`udp_ok` no remoto**: o NAT do emulador (slirp/QEMU) mantém mapeamentos UDP por tempo limitado; o
  `persistent_keepalive_interval: 25` do cliente deve mantê-lo. Se a sonda medir `udp_ok false` só no remoto, esse é
  o primeiro suspeito. Inferência, confiança média.
- **Efeito colateral nos locais**: adicionar o par reinicia o sing-box do central (assinatura); os android-02/03/05/06
  perdem o túnel por segundos e o cliente rediscá. Evitar fazê-lo com objetivo em curso nesses aparelhos.
- **O que NÃO conta como prova**: `/api/health ok`, `firewall-check liberado` sozinho, ou uma `inbound connection`
  sem a medição da sonda; a prova é `trafego_verificado` + a linha de `network_measurements` + o `dumpsys` do remoto
  + o `inbound connection` datado, todos com commit e hora.

### Gaps

- Nada do plano foi executado (aparelhos parados, regra inexistente). Prova `not_run`.
- `cmd connectivity airplane-mode` e `svc wifi` no emulador android-34: comportamento com always-on VPN e o tempo de
  reconexão do sing-box não foram medidos; se o modo avião derrubar o `tun0`, a convergência trata como "túnel
  caído" e reinicia (o que também serve de prova de recuperação, só que mais lenta).
- Não há como testar UDP do notebook ao central em modo só-leitura; a prova do UDP é o handshake do passo 4.
