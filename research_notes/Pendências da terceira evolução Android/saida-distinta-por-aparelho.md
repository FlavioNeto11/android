# Saída pública distinta e estável por aparelho (P1 da terceira evolução)

Notas de pesquisa, 30/09/2026. Escopo: arquiteturas e provedores para dar a cada emulador Android do parque um IP
público de saída **distinto e estável**, sobre o desenho já implantado (ADR-056: sing-box SFA 1.14.2 no convidado,
always-on com bloqueio, WireGuard até o sing-box 1.14.2 em modo usuário no central, proxy autenticado encadeado por
`detour`). Leitura do repositório só de leitura; nenhuma contratação, nenhuma mudança de rede, nenhuma credencial.

Convenção de confiança: **[CONFIRMADO]** = lido em fonte primária (código do repositório, documentação oficial ou
código-fonte do sing-box, página oficial do provedor com data); **[INFERIDO]** = conclusão minha a partir do
confirmado, com o grau de confiança indicado.

## Pergunta 1 — O que um perfil de rede expressa hoje e onde a saída distinta por aparelho se encaixa

### Takeaway

Um perfil (`network_profiles`) é a unidade de endpoint + segredo + `params`, compartilhável N:1 com aparelhos; não há
campo por aparelho além da chave WireGuard gerada pela plataforma quando `params.servidor: "central"`. Saída distinta
por aparelho hoje é, portanto, **um perfil por aparelho**, e dois caminhos já existem sem código novo: perfil de VPN
`protocol: wireguard` apontando para um servidor externo (chave privada do cliente no cofre, `params.peer_public_key`
e `params.address`) e perfil de proxy externo (`endpoint_host`, `params.username`, senha no cofre) encadeado pelo
túnel do central. O servidor do central (`servidor: central`) não pode dar saída distinta: um único `direct` e um único
IP público atrás do NAT.

### Cited Findings

- O perfil tem `kind` (vpn|proxy), `protocol` (wireguard|singbox|http|socks5), `endpoint_host`, `endpoint_port`,
  `secret_ref` (referência no cofre) e `params` (JSON sem segredo); `device_network` liga um aparelho a no máximo um
  perfil de VPN e um de proxy, com `policy`, `desired_rev`/`applied_rev`, `state` e a última saída medida
  (`egress_ipv4`, `egress_ipv6`, `verified_at`); `network_measurements` guarda cada medição com `udp_ok`, `per_app` e
  `leak_blocked` — [CONFIRMADO] `C:\git\android\backend\migrations\057_rede_por_aparelho.sql`.
- `network_keys` guarda, por `owner` (`servidor` ou `instance_id`), a chave pública, a referência da privada no cofre
  e o `address` no túnel (UNIQUE) — só para os aparelhos servidos pelo central — [CONFIRMADO]
  `C:\git\android\backend\migrations\058_chaves_de_rede.sql`.
- O par tipo × protocolo é regra do código: `vpn` aceita `wireguard`|`singbox`, `proxy` aceita `http`|`socks5`
  (`PROTOCOLOS_POR_TIPO`) — [CONFIRMADO] `C:\git\android\backend\app\devices\rede.py` linhas 46-49.
- `params` é JSON livre até 4000 caracteres; o validador só recusa chaves que pareçam segredo e texto que a redação
  mascararia — [CONFIRMADO] `rede.py` linhas 128-143. Logo um campo novo como `params.egress_esperado` cabe sem
  migração [INFERIDO, confiança alta].
- `montar_plano` traduz o pedido em três formas de VPN: (1) `params.servidor: "central"` → chave gerada pela
  plataforma (`servidor.par_do_aparelho`), par = chave pública do servidor do central, endpoint = `10.0.2.2:51820`
  para o emulador local ou `rede.servidor.endpoint_lan` para o aparelho de outra máquina; (2) `protocol: wireguard`
  sem `servidor: central` → **servidor WireGuard externo**: o segredo do perfil é a chave privada do cliente e `params`
  traz `peer_public_key`, `address` (e opcionais `dns`, `mtu`, `allowed_ips`), com `endpoint_host`/`endpoint_port`
  do perfil; (3) `singbox` sem `servidor: central` é recusado — [CONFIRMADO]
  `C:\git\android\backend\app\devices\rede_aplicacao.py` linhas 174-224.
- Proxy: `servidor: central` → o `mixed` do central alcançado pelo túnel (exige a VPN do central); senão, **proxy
  externo** com `endpoint_host`/`endpoint_port` do perfil, `params.username` e a senha no cofre; com VPN presente o
  proxy recebe `detour: wg-out` (é alcançado PELO túnel) e vira o `final` da rota — [CONFIRMADO] `rede_aplicacao.py`
  linhas 226-247 e 271-288.
- O cliente gerado: `tun` com `auto_route`, `strict_route`, DNS UDP com `detour: wg-out`, regra `hijack-dns`,
  `final: wg-out` (ou `proxy-out`); com proxy HTTP, `{"network": "udp", "action": "reject"}`; keepalive 25 s,
  `allowed_ips` `0.0.0.0/0, ::/0` por padrão — [CONFIRMADO] `rede_aplicacao.py` linhas 76-90 e 249-298.
- O servidor do central: um endpoint `wireguard` com `system: false`, um par por aparelho (`allowed_ips` =
  `<address>/32`), inbound `mixed` só em `127.0.0.1:18080` com usuários, **um único outbound `direct`** e
  `route.final: direct`; as regras recusam loopback, a sub-rede do túnel e as faixas privadas para o túnel e para o
  proxy; a única exceção é a porta do proxy vinda do túnel — [CONFIRMADO]
  `C:\git\android\backend\app\devices\rede_servidor.py` linhas 140-183.
- O servidor só sobe com aparelho atribuído a perfil `servidor: central`, é reiniciado quando a assinatura (pares,
  usuários, portas, sub-rede, chave pública) muda, e a evidência de conexão é a linha `inbound connection from
  10.66.0.N` do log — [CONFIRMADO] `rede_servidor.py` linhas 255-257, 301-320, 381-406, 490-505.
- O `endpoint_lan` (aparelhos do notebook) exige regra de firewall do Windows no central, que a plataforma só lê e
  mostra — [CONFIRMADO] `config/config.example.yaml` linhas 349-351 e `rede_servidor.py` linhas 468-487.
- A plataforma detecta saída **compartilhada** (`saidas_compartilhadas`: outros aparelhos com o mesmo `egress_ipv4`
  ou `egress_ipv6` em `device_network`) e emite aviso, não bloqueio; não existe noção de saída **esperada** —
  [CONFIRMADO] `rede.py` linhas 832-870 e `docs/dominios/parque.md` ("egress_shared_with … aviso, não bloqueio").
- `trafego_verificado` exige IP público medido, cada app exigido `ok` e, com bloqueio, `leak_blocked`; `udp_ok` é
  gravado mas **não** condiciona o estado — [CONFIRMADO] `rede.py` linhas 813-830.
- Estado real em 30/09: android-05, 02, 06 e 03 em `trafego_verificado`, todos saindo pelo IP do central; "IP
  distinto por aparelho depende de provedor (P1)" — [CONFIRMADO] `docs/estado-atual.md` linhas 11-18 e
  `docs/relatorio-validacao.md` linhas 1959-1961.

### Inferences

- **Onde a saída distinta se encaixa, sem migração:** (i) um perfil por aparelho, atribuído por
  `POST /api/network/assign` com `instance_ids` de um só aparelho; (ii) `params.egress_esperado` (IPv4 do endpoint
  ou do proxy contratado) no perfil; (iii) em `registrar_medicao`, comparar o medido com o esperado e rebaixar a
  `parcial` no desencontro (hoje só se compara entre aparelhos). Confiança alta: são três pontos já delimitados no
  código.
- **Chaves para servidor externo:** hoje o caminho `protocol: wireguard` externo espera que o dono gere o par de
  chaves fora e mande a privada no `secret`; a geração pela plataforma (`gerar_chave_wireguard`, migração 058) só
  acontece no modo `servidor: central`. Um modo "servidor remoto gerenciado" (a plataforma gera a chave e publica o
  par no VPS) é item de plataforma posterior, não pré-requisito do piloto. Confiança alta.
- **Aparelhos do notebook ganham com endpoint externo:** o cliente inicia UDP de saída para o VPS, então a regra de
  firewall do Windows e o `endpoint_lan` deixam de ser necessários para eles. Confiança alta (o firewall só importa
  para conexões de entrada no central).

### Gaps

- Não há teste ou prova real de um perfil `wireguard` externo no repositório: o caminho existe no código
  (`montar_plano`) mas o relatório de validação registra só o servidor do central. Fica `not_run` até o piloto.

## Pergunta 2 — Opções de arquitetura e o que cada uma entrega de fato

### Takeaway

Verifiquei no código-fonte do sing-box que o servidor consegue rotear por par: o endpoint WireGuard marca cada
conexão com o tag do endpoint e o endereço de origem do par, e o outbound `direct` com `inet4_bind_address` fixa o IP
de origem para TCP **e** UDP. Isso valida a opção (b) (um VPS Linux com vários IPv4 e um `direct` por aparelho, ou
SNAT por par no nftables). A opção (a) (um VPS por aparelho) é a mais simples e a mais cara em escala; a (c) (proxy
estático por aparelho encadeado pelo túnel atual) é a única que não muda o servidor do central, mas perde UDP na
maioria dos provedores e carrega tensão com o ADR-056 §6 quando o proxy é "residencial/ISP"; a (d) (vários gateways
no host Windows) não é viável sem NAT nem sem mexer na rede do central, que o dono reservou.

### Cited Findings

**sing-box (documentação 1.14/1.15 e código-fonte, ramo `testing`, lido em 30/09/2026):**

- Dial Fields: `detour` ("The tag of the upstream outbound. If enabled, all other fields will be ignored"),
  `bind_interface`, `inet4_bind_address` ("Sets the IPv4 address for outbound connections"), `inet6_bind_address`,
  `routing_mark` (Linux); a página não diz se o bind vale para UDP — [CONFIRMADO]
  [sing-box: Dial Fields](https://sing-box.sagernet.org/configuration/shared/dial/).
- No código, `Inet4BindAddress` vira `dialer4.LocalAddr` (TCP) **e** `udpDialer4.LocalAddr`/`udpAddr4`, usado em
  `ListenPacket` para UDP; `BindInterface` usa `control.BindToInterface` — [CONFIRMADO]
  [sing-box `common/dialer/default.go` na tag v1.14.2](https://github.com/SagerNet/sing-box/blob/v1.14.2/common/dialer/default.go)
  linhas 203-206 e 358 (no ramo `testing`, linhas 206-213 e 349-366). Ou seja, o bind cobre TCP e UDP na versão
  que o central roda.
- Regra de rota: campos `inbound` ("Tags of Inbound"), `source_ip_cidr` ("Match source IP CIDR"), `ip_cidr`,
  `network` (`tcp`|`udp`|`icmp`), `action` obrigatório (`route`, `route-options`, `reject`, `hijack-dns`, `sniff`,
  `resolve`); `outbound` na regra foi depreciado na 1.11.0 e movido para a ação `route` — [CONFIRMADO]
  [sing-box: Route Rule](https://sing-box.sagernet.org/configuration/route/rule/).
- Endpoint: "An endpoint is a protocol with inbound and outbound behavior" (desde 1.11.0) — [CONFIRMADO]
  [sing-box: Endpoint](https://sing-box.sagernet.org/configuration/endpoint/).
- O endpoint WireGuard preenche `metadata.Inbound = w.Tag()`, `metadata.Source = source` (o endereço do par no
  túnel) e loga `inbound connection from <source>`; destino igual ao próprio endereço do endpoint é reescrito para
  `127.0.0.1` — [CONFIRMADO]
  [sing-box `protocol/wireguard/endpoint.go` na tag v1.14.2](https://github.com/SagerNet/sing-box/blob/v1.14.2/protocol/wireguard/endpoint.go)
  linhas 209-235 (ramo `testing`: 216-275); a regra `inbound` casa por `metadata.Inbound` —
  [`route/rule/rule_item_inbound.go`](https://github.com/SagerNet/sing-box/blob/testing/route/rule/rule_item_inbound.go)
  linha 25-26. O repositório já usa `inbound: [wg-srv]` nas regras do servidor e a reescrita para `127.0.0.1` foi
  medida no 25.1 (`rede_servidor.py` linhas 10-13 e 155-156).
- Endpoint WireGuard: `system` ("Use system interface. Requires privilege and cannot conflict with exists system
  interfaces"), `name`, `mtu` (1408 padrão), `address`, `listen_port`, `peers[].allowed_ips` (obrigatório),
  `persistent_keepalive_interval`; o endpoint aceita Dial Fields — [CONFIRMADO]
  [sing-box: WireGuard Endpoint](https://sing-box.sagernet.org/configuration/endpoint/wireguard/).
- Outbound `socks`: `version` 4|4a|5, `username`, `password`, `network` ("One of tcp udp. Both is enabled by
  default"), `udp_over_tcp`, e Dial Fields (logo `detour`) — [CONFIRMADO]
  [sing-box: SOCKS Outbound](https://sing-box.sagernet.org/configuration/outbound/socks/).

**WireGuard, nftables, QUIC:**

- Cryptokey routing: "each peer (a client) will be able to send packets to the network interface with a source IP
  matching his corresponding list of allowed IPs"; na saída, o destino escolhe o par — [CONFIRMADO]
  [wireguard.com](https://www.wireguard.com/). Com `allowed_ips: <address>/32` por par (o que o servidor do central
  já faz), o endereço de origem 10.66.0.N identifica o aparelho de forma criptográfica.
- SNAT no nftables: `nft add rule nat postrouting ip saddr 192.168.1.0/24 oif eth0 snat to 1.2.3.4`, em cadeia
  `type nat hook postrouting priority 100`; NAT é stateful via conntrack — [CONFIRMADO]
  [wiki nftables: NAT](https://wiki.nftables.org/wiki-nftables/index.php/Performing_Network_Address_Translation_(NAT)).
- RFC 9308: "between 3% and 5% of networks block all UDP traffic" e "all applications running on top of QUIC must
  either be prepared to accept connectivity failure on such networks or be engineered to fall back to some other
  transport protocol" — [CONFIRMADO] [RFC 9308](https://www.rfc-editor.org/rfc/rfc9308.html). O Chromium (e o
  Cronet, o mesmo código de rede embutido em apps) volta a TCP quando o QUIC falha —
  [Zscaler, "Managing the QUIC Protocol"](https://help.zscaler.com/zia/managing-quic-protocol) (fonte secundária;
  o próprio Instagram não documenta o transporte).

**Android e o desenho já adotado (do repositório):**

- ADR-056 descartou rota ou VPN no host ("separa pouco … e mexe no caminho do túnel e do ADB, que exige
  autorização") e o `-http-proxy` do emulador (sem UDP, senha no argumento, invalida o snapshot) — [CONFIRMADO]
  `docs/decisoes.md`, ADR-056 "Alternativas".
- O servidor do central é "sem driver, sem NAT, sem serviço do Windows e sem mudar a rede ou o firewall do sistema"
  (decisão P2 do dono, 29/09) — [CONFIRMADO] `rede_servidor.py` linhas 3-6.

### Inferences

**(a) Um servidor WireGuard/VPS por aparelho.** Cada VPS tem o próprio IPv4; o sing-box (ou o WireGuard do kernel)
no VPS aceita um par e manda tudo ao `direct`. Entrega IPv4 distinto e, se o provedor der /64, IPv6 distinto também
(irrelevante hoje: `strict_route` deixa o IPv6 preso no túnel; ver Pergunta 3). TCP, UDP e DNS seguem o túnel. Na
plataforma: um perfil `wireguard` por aparelho, `endpoint_host` = IP do VPS; o dono gera as chaves fora (ou a
plataforma, num modo futuro). Custo linear: N VPS + N IPv4. Operação: N máquinas para atualizar. Confiança alta.

**(b) Um VPS Linux com vários IPv4 e um outbound por par.** Duas formas, ambas validadas pela documentação:

- *Só sing-box (`system: false`, modo usuário como no central):* um outbound `direct` por IP público com
  `inet4_bind_address`, e uma regra `{"inbound": ["wg-srv"], "source_ip_cidr": ["10.66.0.2/32"], "action": "route",
  "outbound": "direct-ip1"}` por par. O bind vale para TCP e UDP (código lido acima). Todos os IPs adicionais
  precisam estar configurados na interface do VPS (a maneira varia por provedor: alias na mesma interface ou rota
  para o IP). Confiança alta na semântica; a prova real fica para o piloto.
- *WireGuard do kernel (ou sing-box com `system: true`) + nftables:* `ip saddr 10.66.0.2 oif eth0 snat to <IP1>`
  por par, na cadeia `nat postrouting`; `ip_forward=1`. É o caminho mais padrão, auditável com `nft list ruleset`
  e independente do sing-box no servidor. Confiança alta.

  Limites: a quantidade de IPv4 por instância é regra do provedor (Pergunta 4), e o IP costuma ser do provedor —
  estável enquanto a assinatura durar. Um VPS só é ponto único de falha para todos os aparelhos que o usam.

**(c) VPN do central → proxy estático por aparelho num provedor.** Sem mudar o servidor do central: perfil de proxy
externo por aparelho, `detour: wg-out`. O provedor vê a conexão vindo do IP do central (38.211.146.161), o que é
compatível com autenticação por usuário/senha ou por lista de IP. Entrega IPv4 distinto **só para TCP** com proxy
HTTP (o cliente já recusa UDP não-DNS) e para UDP só se o provedor documentar UDP ASSOCIATE no SOCKS5 (a medição do
25.5 já viu associações SOCKS5 expirarem). DNS: com VPN, o DNS vai pelo túnel do central (`dns-remoto` com `detour:
wg-out`), ou seja, a resolução sai pelo IP do central e o tráfego pelo proxy — o resolvedor visto é o do central,
não o do proxy; é aceitável, mas é preciso registrar que "DNS pelo proxy" exige `detour: proxy-out` no servidor de
DNS, e proxy HTTP só carrega DNS em TCP. QUIC do Instagram: com UDP recusado, o app volta a TCP (RFC 9308) — perde-se
HTTP/3, não a função. Tensão com o ADR-056 §6: proxies "residenciais/ISP" são vendidos justamente para o destino
não distinguir o tráfego de um usuário doméstico; datacenter dedicado é um IP de servidor declarado. A classificação
fica com o dono; aqui registro só a tensão.

**(d) Vários gateways no host Windows.** Para dar IPs distintos a partir do central seria preciso ou vários IPs
públicos na WAN do dono (a conexão doméstica/escritório está atrás de NAT: não há) ou NAT/rotas por aparelho no
Windows (RRAS, `New-NetNat`, regras de firewall), o que o dono excluiu na decisão P2 e o ADR-056 descartou (mexe no
caminho do túnel e do ADB). Não viável. Confiança alta.

**Comparação resumida (o que cada uma entrega de fato):**

| Opção | IPv4 distinto | IPv6 | TCP | UDP | DNS pela mesma saída | Muda o servidor do central | Custo em escala |
|---|---|---|---|---|---|---|---|
| (a) VPS por aparelho | sim | sim (se houver, hoje bloqueado no cliente) | sim | sim | sim | não (perfil externo) | linear, o maior |
| (b) 1 VPS, N IPv4, bind/SNAT por par | sim | possível (blocos /64) | sim | sim | sim | não (perfil externo) | base + N × IP |
| (c) VPN central → proxy estático por aparelho | sim (TCP) | raro | sim | só SOCKS5 com UDP documentado | não (resolve pelo túnel do central) | não | N × IP, sem VPS |
| (d) gateways no host Windows | não há IPs | — | — | — | — | sim (NAT/rotas) | inviável |

### Gaps

- Nenhuma prova real de `inet4_bind_address` por par num servidor sing-box 1.14.2 em Linux: a leitura é da
  documentação e do código da tag v1.14.2 (conferido); o piloto precisa medir.
- Instagram não documenta seu transporte (QUIC/HTTP3 e o fallback); a inferência é a do Chromium/Cronet e da RFC.

## Pergunta 3 — Endereço do túnel, endpoint, configuração por aparelho e IP observado; TCP/UDP/DNS; detecção de duplicidade

### Takeaway

São quatro coisas diferentes: o endereço privado do túnel (10.66.0.N, identidade criptográfica do par), o endpoint
(para onde o cliente conecta), a configuração do aparelho (perfil + política) e o IP público observado de dentro
(a medição). Só a medição prova saída distinta; a plataforma já detecta duplicidade comparando o medido entre
aparelhos, e falta o par "esperado × medido" por aparelho. Na prática todas as opções entregam IPv4 distinto; IPv6
distinto é possível em (a) e (b), mas o cliente hoje deixa o IPv6 inalcançável de propósito.

### Cited Findings

- `10.66.0.0/24`, servidor em `.1`, um endereço por aparelho a partir do `.2`; `allowed_ips` `<address>/32` por par
  — [CONFIRMADO] `config/config.example.yaml` linha 345 e `rede_servidor.py` linha 178.
- `strict_route` no `tun`: "IPv6 inalcançável em vez de vazar" — [CONFIRMADO] `rede_aplicacao.py` linhas 253-254 e
  293-294; a sonda mede `hosts_ipv6` e a configuração diz "com strict_route o IPv6 fica preso no túnel" —
  [CONFIRMADO] `config/config.example.yaml` linhas 353-354.
- A sonda mede IPv4 por HTTP/1.0 na porta 80 (só registro A), UDP de DNS (8.8.4.4) e UDP não-DNS (NTP em
  time.google.com); `udp_ok` só com os dois — [CONFIRMADO] `config/config.example.yaml` linhas 352-356.
- `NetworkMeasurementInput` recusa IP não global (`is_global`): 10.0.2.15, a interface do túnel, loopback, rede
  local, CGNAT, link-local, ULA e faixas de documentação não valem como saída — [CONFIRMADO] `rede.py` linhas
  191-230 e `docs/dominios/parque.md` ("IP de saída é o público").
- Duplicidade: `saidas_compartilhadas` compara `egress_ipv4`/`egress_ipv6` da última medição de cada aparelho;
  igual → `detail` da medição recebe "aviso: a mesma saída medida em …" e o evento `network.updated` sai com
  `level="warn"` e `acao: saida_compartilhada`; `GET /api/network/devices` traz `egress_shared_with` —
  [CONFIRMADO] `rede.py` linhas 832-870.
- Persistência: always-on e lockdown valem no boot, por isso a aplicação termina com um reinício; a conferência roda
  "ao ligar, ao acordar, depois do reinício do backend e a cada `rede.deriva_s`" e só regride; túnel caído com
  configuração no lugar → `configurado` (reinicia) — [CONFIRMADO] `rede_convergencia.py` linha 14 e
  `docs/dominios/parque.md` (25.4). O cliente usa `persistent_keepalive_interval: 25` — [CONFIRMADO]
  `rede_aplicacao.py` linha 87 e 268.
- Com proxy HTTP o cliente recusa UDP não-DNS; com SOCKS5 o UDP vai pelo proxy e a medição viu associações expirarem
  — [CONFIRMADO] `rede_aplicacao.py` linhas 254-256.
- Faixas recusadas no servidor do central (loopback, 10/8, CGNAT, 172.16/12, 192.168/16, link-local, multicast,
  ULA…): um provedor de proxy com IP público passa pelo `final: direct` — [CONFIRMADO] `rede_servidor.py` linhas
  72-78 e 157.

### Inferences

- **Vínculo aparelho → configuração → saída esperada.** Proposta mínima: `params.egress_esperado` no perfil (IPv4
  do VPS na opção (a)/(b); IPv4 do proxy na (c)); o perfil é por aparelho, então o vínculo é 1:1. Em
  `registrar_medicao`: medido ≠ esperado → `parcial` com o motivo ("saída medida X, esperada Y"), além do aviso de
  duplicidade já existente; medido = esperado e distinto dos outros → `trafego_verificado` como hoje. O painel já
  mostra `egress_shared_with`; ganharia `egress_expected`. Confiança alta.
- **Comportamento em falha, por opção.** (a)/(b): VPS fora → handshake WireGuard não completa; com
  `exigida_com_bloqueio` o lockdown do Android segura tudo fora da VPN (`leak_blocked`), o aparelho não vaza pelo IP
  do central nem do notebook; a conferência vê `tun0` sem tráfego/handshake e a tarefa fica segurada pela porta de
  rede (política exigida). (c): proxy fora → o `final: proxy-out` falha; o tráfego não cai para o túnel do central
  porque a rota final é o proxy e não há regra de fallback; a sonda mede "falhou" e o estado não vira
  `trafego_verificado`. Em nenhuma opção a falha vira saída compartilhada silenciosa, desde que a política seja
  `exigida_com_bloqueio`. Confiança média-alta (o comportamento do lockdown foi provado; o do `final` sem fallback
  é leitura da configuração).
- **Persistência após reinício/hibernação.** O estado já implantado cobre: always-on + lockdown no boot, keepalive
  de 25 s reabre o handshake depois do snapshot, conferência "ao acordar". Com endpoint externo, o que muda é que o
  handshake vai para a internet (o VPS) em vez de `10.0.2.2`; a hibernação do emulador (snapshot) preserva a
  configuração do SFA, e o WireGuard renegocia sozinho pelo keepalive. Ponto de atenção: o servidor externo precisa
  aceitar o par com endpoint de origem novo (NAT do dono muda porta): o WireGuard faz roaming por padrão. Confiança
  alta.
- **IPv4 só vs IPv6 também.** (a)/(b) podem entregar IPv6 distinto se o provedor der /64 por VPS e o servidor
  rotear `::/0` do par; hoje seria desperdício: o cliente deixa IPv6 preso (`strict_route`) e a sonda registra
  `egress_ipv6` vazio. (c) raramente oferece IPv6 dedicado. Manter "IPv4 distinto" como objetivo e IPv6 bloqueado
  como está. Confiança alta.
- **Evidência por app.** Não muda com a arquitetura: `per_app` continua vindo da sonda de dentro do aparelho (uid
  2000) por app exigido; o que muda é o valor esperado do IP. Confiança alta.

### Gaps

- Não há, no repositório, medição de quanto tempo o SFA leva para renegociar o WireGuard com endpoint na internet
  depois de um snapshot (só o `espera_tun_s: 60` medido com o servidor local).

## Pergunta 4 — Comparação de provedores e cenários de custo (1, 10 e 50 aparelhos)

### Takeaway

Para "um VPS com muitos IPv4" só Hetzner (até 20 Floating IPs por servidor, €3,00 cada) e OVHcloud (até 16
Additional IPs por VPS, US$ 2,39 cada) publicam limite e preço; nenhum dos dois tem região no Brasil. Linode/Akamai é
o único com São Paulo confirmado (Nanode US$ 7,00/mês), mas o IPv4 adicional depende de ticket e não tem preço
publicado — o que torna "um VPS pequeno por aparelho em São Paulo" (US$ 7/aparelho) a forma mais previsível de ter IP
brasileiro. DigitalOcean e Contabo limitam a 1 IP extra por instância. Nos proxies estáticos, os preços vão de US$
1,57 a US$ 3,50 por IP/mês, com Brasil confirmado em IPRoyal, Oxylabs ISP e Decodo ISP; **nenhum documenta UDP
ASSOCIATE para produto estático** (a Rayobyte diz explicitamente que não há UDP), então a opção (c) é TCP-only na
prática. Valores observados em 30/09/2026, sem imposto, na moeda da página.

### Cited Findings

**IPv4 adicional em VPS (levantamento do subagente, páginas oficiais lidas em 30/09/2026):**

- Hetzner Cloud: Primary IPv4 **máximo 1 por servidor**, €0,50/mês; Floating IPv4 **€3,00/mês**, até **20 por
  servidor**, 10 por projeto por padrão ("can be increased upon request"); mais de um IPv4 público no mesmo servidor
  só por Floating IP — [CONFIRMADO]
  [Primary IPs](https://docs.hetzner.com/cloud/servers/primary-ips/overview),
  [Floating IPs](https://docs.hetzner.com/cloud/floating-ips/overview),
  [Billing FAQ](https://docs.hetzner.com/cloud/billing/faq/). Menor servidor CX23 **€5,49/mês** (Falkenstein,
  Nuremberg, Helsinki; preços desde 15/06/2026) — [CONFIRMADO]
  [price adjustment](https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/). Sem região
  na América Latina — [CONFIRMADO] [hetzner.com/cloud](https://www.hetzner.com/cloud/). Termos sem cláusula sobre
  VPN/proxy; proíbem DDoS, open relay, spam e mineração — [CONFIRMADO]
  [Terms](https://www.hetzner.com/legal/terms-and-conditions/). Cota de tráfego (20 TB na UE) e excedente: só
  terceiros [INFERIDO].
- OVHcloud: Additional IP **US$ 2,39/IP/mês**, **até 16 por VPS**, geolocalizados em 14 países; VPS-1 "from US$
  4,54/mês" (2 vCPU, 4 GB, tráfego ilimitado, IPv6 incluído); Brasil ausente da tabela regional — [CONFIRMADO]
  [Additional IP](https://www.ovhcloud.com/en/network/additional-ip/), [VPS options](https://www.ovhcloud.com/en/vps/options/),
  [VPS](https://www.ovhcloud.com/en/vps/). Taxa de setup: não encontrada.
- DigitalOcean: Reserved IPv4 grátis atribuída, US$ 5,00/mês solta; **"Droplets cannot have more than one reserved
  IP address"**; Droplet mínimo US$ 4,00/mês (500 GiB, excedente US$ 0,01/GiB); sem região no Brasil — [CONFIRMADO]
  [Reserved IPs pricing](https://docs.digitalocean.com/products/networking/reserved-ips/details/pricing/),
  [limits](https://docs.digitalocean.com/products/networking/reserved-ips/details/limits/),
  [Droplet pricing](https://www.digitalocean.com/pricing/droplets).
- Linode/Akamai: "Additional IPv4 addresses require technical justification. Please open a Support Ticket"; preço
  "may vary by region" e **não publicado** — [CONFIRMADO]
  [Managing IP addresses](https://techdocs.akamai.com/cloud-computing/docs/managing-ip-addresses-on-a-compute-instance).
  **São Paulo: Nanode 1 GB US$ 7,00/mês, 1 TB incluído, excedente US$ 0,007/GB** — [CONFIRMADO]
  [Pricing São Paulo](https://www.akamai.com/cloud/pricing/sao-paulo). AUP só em PDF, não lida.
- Contabo: IPv4 adicional €3,50/mês, **VPS: 1 adicional**; Cloud VPS 4 €5,50/mês (24 meses); sem Brasil —
  [CONFIRMADO] [Contabo help 103000269701](https://help.contabo.com/en/support/solutions/articles/103000269701-can-i-order-additional-ip-addresses-for-my-server-),
  [contabo.com/en/vps](https://contabo.com/en/vps/).
- Vultr: Reserved IP US$ 0,004/h (≈ US$ 3/mês), "not free" — [CONFIRMADO]
  [Are reserved IPs free](https://docs.vultr.com/support/products/network/are-reserved-ips-free); "up to two
  additional addresses" por instância e US$ 2/mês: só via snippet/comunidade [INFERIDO]
  ([discuss.vultr.com](https://discuss.vultr.com/discussion/195/additional-ips)); página de preços e FAQ devolveram
  403; São Paulo não confirmado nesta leitura.

**Proxies estáticos dedicados (levantamento do subagente, páginas oficiais lidas em 30/09/2026, USD):**

- Bright Data ISP dedicado: 10 IPs US$ 3,50/IP (US$ 35/mês) … 1.000 IPs US$ 2,50/IP; "Each IP includes a 100 GB
  fair usage allowance per month"; datacenter dedicado 10 IPs US$ 2,20/IP; pool ISP lista "Brazil 33,173 IPs";
  autenticação por usuário/senha na `brd.superproxy.io:22225` — [CONFIRMADO]
  [ISP pricing](https://brightdata.com/pricing/proxy-network/isp-proxies),
  [Datacenter pricing](https://brightdata.com/pricing/proxy-network/datacenter-proxies),
  [ISP proxies](https://brightdata.com/proxy-types/isp-proxies). UDP e IPv6: silente.
- Oxylabs ISP: Starter 10 IPs US$ 1,60/IP (US$ 16/mês); **até 50 GB/mês e 100 sessões simultâneas por proxy**;
  "Brazil… 5.4k IPs" — [CONFIRMADO] [ISP proxies](https://oxylabs.io/products/isp-proxies). Datacenter dedicado US$
  2,25/IP, mínimo 3 — [CONFIRMADO] [Datacenter pricing](https://oxylabs.io/pricing/datacenter-proxies). UDP
  documentado **só** para Residential ("support SOCKS5 connections over TCP and UDP") — [CONFIRMADO]
  [Residential protocols](https://developers.oxylabs.io/products/proxies/residential-proxies/protocols).
- Decodo (ex-Smartproxy) ISP dedicado: 3 IPs US$ 9,99 (US$ 3,33/IP), 10 IPs US$ 29, 50 IPs US$ 135; SOCKS5 e
  HTTP(S), user:pass em `isp.decodo.com:10000`; Brasil listado — [CONFIRMADO]
  [ISP pricing](https://decodo.com/proxies/isp-proxies/pricing), [ISP proxies](https://decodo.com/proxies/isp-proxies).
  Datacenter dedicado "from US$ 1,15/IP", países **US, UK, DE, FR, JP (sem Brasil)** — [CONFIRMADO]
  [Dedicated datacenter](https://decodo.com/proxies/dedicated-datacenter-proxies).
- IPRoyal static residential: "Starting from" US$ 2,70/proxy (30 dias), dedicado, tráfego ilimitado, SOCKS5 e HTTP(S),
  Brasil "10,482 IPs"; datacenter dedicado "from US$ 1,57/proxy", 100 GB/IP/mês, Brasil "5,007 IPs" — [CONFIRMADO]
  [Static residential](https://iproyal.com/static-residential-proxies/),
  [Datacenter](https://iproyal.com/datacenter-proxies/).
- Webshare datacenter dedicado: 20 proxies a US$ 1,33/proxy (US$ 26,60/mês); static residential dedicado 20 a US$
  2,10 (US$ 42/mês); HTTP/SOCKS5, IP authorization; Brasil não confirmado — [CONFIRMADO]
  [Dedicated datacenter](https://www.webshare.io/dedicated-datacenter-proxy),
  [Dedicated static residential](https://www.webshare.io/dedicated-static-residential-proxy).
- Proxy-Seller IPv4 dedicado "starts from US$ 0,49", mínimo 1 IP, user:pass ou allowlist, Brasil listado; preço do
  Brasil não obtido — [CONFIRMADO] [proxy-seller.com/ipv4](https://proxy-seller.com/ipv4/).
- **UDP ASSOCIATE em proxy estático:** nenhum dos seis documenta; a Rayobyte declara "SOCKS5 connections on our
  Static Proxy services are limited to TCP traffic and do not support UDP" — [CONFIRMADO]
  [Rayobyte support](https://portal.rayobyte.com/en/support/solutions/articles/64000287112-does-your-socks-protocol-support-udp-).
  Um teste de terceiro relata erro SOCKS5 0x07 em Bright Data e IPRoyal e conexão fechada em Decodo ao pedir UDP —
  [INFERIDO, fonte secundária] [aimultiple](https://aimultiple.com/socks5-proxies).
- KYC: Bright Data exige verificação para residencial ("available to verified companies only"), ISP e datacenter
  "not requiring KYC"; Decodo aciona verificação por risco ou alvo restrito — [INFERIDO, snippets de
  docs.brightdata.com e help.decodo.com, páginas não abertas].

**Tabela — IPv4 adicional em VPS**

| Provedor | VPS base/mês | IPv4 extra/mês | Máx. IPv4 por instância | Como pedir | Brasil |
|---|---|---|---|---|---|
| Hetzner | CX23 €5,49 | Floating €3,00 (Primary €0,50, só 1) | 1 + 20 Floating (10/projeto padrão) | painel/API | não |
| OVHcloud | VPS-1 US$ 4,54 | US$ 2,39 | 1 + 16 | painel | não |
| Linode/Akamai | Nanode US$ 7,00 (SP) | não publicado | não publicado | ticket com justificativa | **sim (São Paulo)** |
| DigitalOcean | US$ 4,00 | Reserved grátis/US$ 5 solta | 1 reserved | painel | não |
| Contabo | €5,50 | €3,50 | 1 | painel (add-on) | não |
| Vultr | não confirmado (403) | Reserved ≈ US$ 3 | 1 + 2 [INFERIDO] | painel/ticket | não confirmado |

**Tabela — proxies estáticos dedicados (US$/mês)**

| Provedor / produto | Preço por IP | Mínimo | Cobrança | SOCKS5 | UDP documentado | IPv6 | Brasil |
|---|---|---|---|---|---|---|---|
| Bright Data ISP dedicado | 3,50 (10) → 2,50 | 10 IPs = 35 | por IP + 100 GB/IP | sim | silente | silente | pool sim; tier dedicado [INFERIDO] |
| Bright Data DC dedicado | 2,20 (10) → 1,30 | 10 IPs = 22 | idem | sim | silente | silente | não confirmado |
| Oxylabs ISP | 1,60 (10) → 1,20 | 10 IPs = 16 | por IP, fair use 50 GB | sim | só Residential | silente | sim |
| Oxylabs DC dedicado | 2,25 | 3 IPs = 6,75 | por IP | sim | silente | silente | não explícito |
| Decodo ISP dedicado | 3,33 (3) → 2,50 | 3 IPs = 9,99 | por IP | sim | silente | silente | sim |
| Decodo DC dedicado | from 1,15 | ? | por IP | sim | silente | silente | **não** |
| IPRoyal static residential | from 2,70 | 1 IP | por IP, ilimitado | sim | silente | não menciona | sim |
| IPRoyal DC dedicado | from 1,57 | 1 IP | por IP + 100 GB | sim | silente | não menciona | sim |
| Webshare DC dedicado | 1,33 (20) → 0,77 | 20 = 26,60 | por IP | sim | silente | silente | não confirmado |
| Proxy-Seller IPv4 | from 0,49 | 1 IP | por IP, ilimitado | sim | silente | não obtido | listado |

### Inferences

**Premissas dos cenários** (explícitas; ajustar se o dono discordar):

1. Consumo por aparelho: 10–30 GB/mês (Instagram com vídeo; não há medição no repositório). 50 aparelhos = 0,5–1,5
   TB/mês: cabe na cota de 20 TB (Hetzner, terceiros), no ilimitado da OVH, e passa de 1 TB num único Nanode
   (excedente US$ 0,007/GB em SP: ≤ US$ 3,50/mês de excedente por 500 GB).
2. O IPv4 principal do VPS serve **um** aparelho (é também o endpoint WireGuard); para D aparelhos são D−1 IPs
   adicionais. Sem imposto, sem setup, sem câmbio (euro e dólar mantidos).
3. A VPN do central (opção c) não tem custo marginal; o proxy é pago por IP.
4. Onde o preço é "a partir de", é piso: Brasil pode custar mais.

**Cenários (mensal):**

| Arquitetura / provedor | 1 aparelho | 10 aparelhos | 50 aparelhos | Observação |
|---|---|---|---|---|
| (a) 1 VPS por aparelho — Linode SP (Nanode) | US$ 7,00 | US$ 70,00 | US$ 350,00 | IP brasileiro; 50 máquinas para operar |
| (a) 1 VPS por aparelho — Hetzner CX23 + Primary | €5,99 | €59,90 | €299,50 | Europa |
| (a) 1 VPS por aparelho — OVH VPS-1 | US$ 4,54 | US$ 45,40 | US$ 227,00 | sem Brasil |
| (a) 1 VPS por aparelho — DigitalOcean | US$ 4,00 | US$ 40,00 | US$ 200,00 | sem Brasil |
| (b) 1 VPS + N IPs — Hetzner (CX23 + 0,50 + 3,00 × (D−1)) | €5,99 | €32,99 | €158,97 (3 servidores × 21 saídas; 47 Floating, limite do projeto elevado) | Europa |
| (b) 1 VPS + N IPs — OVH (VPS-1 + 2,39 × (D−1)) | US$ 4,54 | US$ 26,05 | US$ 125,95 (3 VPS × 17 saídas) | sem Brasil; setup não encontrado |
| (b) 1 VPS + N IPs — Linode SP | US$ 7,00 | US$ 7,00 + 9 IPs a preço não publicado | ticket | depende da resposta da Akamai |
| (c) VPN central + IPRoyal DC dedicado BR | US$ 1,57 | US$ 15,70 | US$ 78,50 | TCP-only; piso |
| (c) VPN central + IPRoyal static residential BR | US$ 2,70 | US$ 27,00 | US$ 135,00 | TCP-only; tensão ADR-056 §6 |
| (c) VPN central + Oxylabs ISP | US$ 16,00 (mín. 10) | US$ 16,00 | US$ 80,00 | TCP-only; 50 GB/IP |
| (c) VPN central + Decodo ISP | US$ 9,99 (mín. 3) | US$ 29,00 | US$ 135,00 | TCP-only |
| (c) VPN central + Bright Data ISP dedicado | US$ 35,00 (mín. 10) | US$ 35,00 | US$ 175,00 | TCP-only |

- **Geo e identidade estável.** A saída atual, 38.211.146.161, está registrada no RDAP da ARIN dentro do bloco
  38.0.0.0/8 de "Cogent Communications, LLC" (registrante nos EUA), sem reatribuição mais específica visível —
  [CONFIRMADO] [RDAP ARIN 38.211.146.161](https://rdap.arin.net/registry/ip/38.211.146.161), lido em 30/09/2026. Ou
  seja, a identidade de hoje já é um IP de operadora de trânsito, não um IP residencial de ISP brasileiro; se as bases
  de geolocalização o colocam no Brasil é [INFERIDO, não conferido]. Isso enfraquece a premissa "sair do Brasil muda
  tudo", mas não a desfaz: um datacenter em São Paulo continua sendo a troca mais conservadora de identidade para
  persona brasileira, e a diferença de €/US$ 1–2 por IP é pequena diante disso. Por isso a ordem: Linode SP em (a),
  IPRoyal/Oxylabs/Decodo BR em (c), OVH/Hetzner se o dono aceitar IP europeu ou norte-americano.
- **Escala de 50.** Em (b), nenhum provedor lido põe 50 IPv4 numa instância pequena: Hetzner dá 21 saídas por
  servidor, OVH 17; a conta de 50 é 3 servidores. Em (a), 50 máquinas é custo de operação (atualizar, monitorar,
  chaves) que a plataforma não gerencia hoje.
- **Escolha do provedor em (c):** os "residential/ISP" são vendidos como IP de usuário doméstico; os "datacenter
  dedicated" são IP de servidor declarado (mesma natureza do VPS). Se o dono quiser evitar a tensão com o §6, a
  classe é datacenter dedicado, e no Brasil só IPRoyal DC confirma disponibilidade.

### Gaps

- Preço e limite do IPv4 adicional na Akamai/Linode (São Paulo): só por ticket; sem número oficial.
- Vultr: página de preços e FAQ inacessíveis (403) nesta leitura; região São Paulo e IPs adicionais não confirmados
  em fonte oficial.
- Taxa de setup da OVH para Additional IP; cota e excedente de tráfego da Hetzner em página oficial.
- IPv6 em todos os proxies; UDP ASSOCIATE em todos os proxies estáticos (silêncio ≠ suporte).
- ToS de Webshare e Proxy-Seller; AUP da Akamai (PDF); ToS de OVH, DO, Contabo e Vultr sobre VPN/proxy não lidas.
- Preço real do Brasil em IPRoyal, Proxy-Seller e Oxylabs DC ("a partir de").
- Consumo real de tráfego por aparelho: nenhuma medição no repositório.

## Pergunta 5 — Arquitetura recomendada, alternativa e o piloto mínimo

### Takeaway

Recomendo a família "endpoint WireGuard externo por aparelho" com dois degraus: **piloto = dois VPS mínimos em São
Paulo (Linode Nanode, US$ 14/mês), um por aparelho, sem código novo** (dois perfis `wireguard` externos, sonda e
detecção de duplicidade já existentes); **consolidação = um VPS Linux com N IPv4 e um outbound `direct` por par**
(`inet4_bind_address`) ou WireGuard do kernel + SNAT por par no nftables, quando o provedor de IPs adicionais estiver
decidido. A alternativa é (c): manter a VPN do central e encadear um proxy datacenter dedicado brasileiro por
aparelho (IPRoyal DC, a partir de US$ 1,57/IP), sem tocar no servidor — mais barata e sem VPS, mas TCP-only e com o
DNS resolvido pelo túnel do central. O que a plataforma precisa acrescentar em qualquer caso é o vínculo "saída
esperada" por perfil e a comparação na medição.

### Cited Findings

- Caminho de perfil externo `wireguard`, com o segredo = chave privada do cliente e `params.peer_public_key`,
  `params.address` — [CONFIRMADO] `rede_aplicacao.py` linhas 206-220.
- Atribuição por aparelho com `instance_ids` explícitos, `dry_run`, tudo ou nada; aparelho com conta real exige
  `confirm_real_account` por aparelho; loja e quarentena recusadas — [CONFIRMADO] `docs/dominios/parque.md`
  ("Atribuição") e ADR-056 §7.
- `POST …/reapply` e `…/verify` (202, `executed: false`), `…/apply` executa já; a convergência aplica no ponto
  seguro, reinicia o aparelho e mede; `GET /api/network/devices` traz `egress_shared_with`, `last_measurement`,
  `required_apps` — [CONFIRMADO] `docs/dominios/parque.md` ("Verificar e reaplicar", "Visão por aparelho").
- Só 3 contas Instagram vivas (lucas, bruno, andre); android-02 e 06 (André) e android-03 (Bruno) têm conta real;
  android-05 foi usado só em leitura (QA Messenger → Chrome) — [CONFIRMADO] `docs/estado-atual.md` linhas 11-14 e
  `docs/relatorio-validacao.md` linha 1971; memória do projeto (`checkout-de-producao-e-worktrees.md`).
- Outbound `direct` aceita Dial Fields (`inet4_bind_address`); rota `source_ip_cidr` + `inbound`; endpoint marca
  `Inbound` e `Source` — [CONFIRMADO] Pergunta 2.
- Custo do piloto: Nanode São Paulo US$ 7,00/mês cada — [CONFIRMADO]
  [Pricing São Paulo](https://www.akamai.com/cloud/pricing/sao-paulo).
- O dono decidiu que o ambiente central é de validação, e que chamada paga, ação em conta real de terceiros e
  mexer no túnel/relógio/WSL exigem autorização; contratar um provedor não está na lista de permitidos —
  [CONFIRMADO] `CLAUDE.md` ("Invariantes"). Nada aqui foi contratado.

### Inferences

**Arquitetura recomendada — (b) com degrau (a) como piloto.**

- Degrau 1 (piloto, 2 aparelhos, sem código): dois Nanodes em São Paulo, cada um com sing-box 1.14.x (ou WireGuard do
  kernel) aceitando UM par e saindo pelo `direct` — **com as mesmas regras de recusa do central** (`FAIXAS_RECUSADAS` +
  a sub-rede do túnel, `action: reject`): o endpoint reescreve o destino igual ao próprio endereço para `127.0.0.1`
  (código lido na Pergunta 2), então sem essa regra o par alcançaria o `sshd` e o que mais estiver em loopback no VPS,
  como o 25.1 mediu no central. Na variante kernel, o equivalente é um filtro `forward` que derruba par → faixas
  privadas/loopback e uma regra `input` que só aceita do `wg0` o que o serviço precisar (nada, no piloto). Na
  plataforma: dois perfis `vpn/wireguard` (um por aparelho) com `endpoint_host` = IP do VPS, `endpoint_port` 51820,
  `secret` = chave privada do cliente gerada pelo dono (`wg genkey`), `params: {peer_public_key, address:
  "10.66.0.2/32", dns: "1.1.1.1", mtu: 1280}`; atribuição com `policy: exigida_com_bloqueio`. Custo US$ 14/mês.
- Degrau 2 (consolidação): um VPS com N IPv4 (Linode SP via ticket, ou OVH/Hetzner se o dono aceitar IP europeu ou
  norte-americano), sing-box com um `direct` por IP e uma regra por par — ou kernel + nftables. Esboço da configuração
  do servidor no VPS (sem segredo; nomes ilustrativos):

  ```json
  {
    "log": {"level": "info", "timestamp": true, "output": "/var/log/sing-box/servidor.log"},
    "endpoints": [{
      "type": "wireguard", "tag": "wg-srv", "system": false, "mtu": 1408,
      "address": ["10.66.0.1/24"], "private_key": "<do cofre do VPS>", "listen_port": 51820,
      "peers": [
        {"public_key": "<pública do aparelho A>", "allowed_ips": ["10.66.0.2/32"]},
        {"public_key": "<pública do aparelho B>", "allowed_ips": ["10.66.0.3/32"]}
      ]
    }],
    "outbounds": [
      {"type": "direct", "tag": "saida-ip1", "inet4_bind_address": "<IPv4 público 1>"},
      {"type": "direct", "tag": "saida-ip2", "inet4_bind_address": "<IPv4 público 2>"}
    ],
    "route": {
      "rules": [
        {"inbound": ["wg-srv"], "ip_cidr": ["<FAIXAS_RECUSADAS do central>", "10.66.0.0/24"], "action": "reject"},
        {"inbound": ["wg-srv"], "source_ip_cidr": ["10.66.0.2/32"], "action": "route", "outbound": "saida-ip1"},
        {"inbound": ["wg-srv"], "source_ip_cidr": ["10.66.0.3/32"], "action": "route", "outbound": "saida-ip2"},
        {"inbound": ["wg-srv"], "action": "reject"}
      ],
      "final": "saida-ip1"
    }
  }
  ```

  A última regra (`inbound: wg-srv` sem outro critério → `reject`) é a segurança: par sem regra própria não sai por
  lugar nenhum — nunca por um IP compartilhado por acidente. O `final: saida-ip1` é só o campo obrigatório: nenhum
  tráfego do túnel chega a ele, porque a recusa genérica vem antes e o endpoint é a única entrada da configuração.
  Não usar outbound `block` para isso: os outbounds
  especiais `block`/`dns` foram depreciados na 1.11.0 e removidos na 1.13.0, substituídos pelas ações de regra —
  [CONFIRMADO] [sing-box: Deprecated](https://sing-box.sagernet.org/deprecated/). Variante kernel: `wg-quick` com os mesmos pares, `net.ipv4.ip_forward=1` e, no
  nftables, `table ip nat { chain postrouting { type nat hook postrouting priority 100; ip saddr 10.66.0.2 oif eth0
  snat to <IP1>; ip saddr 10.66.0.3 oif eth0 snat to <IP2>; } }` mais uma regra de `filter forward` que derruba o
  que não casou (o equivalente do `final: bloqueio`). Os IPs adicionais precisam estar configurados na interface do
  VPS conforme o provedor (Hetzner: Floating IP exige configuração no SO; OVH: Additional IP idem).
- **O que a plataforma acrescenta (ordem de valor):**
  1. `params.egress_esperado` no perfil e a comparação em `registrar_medicao` (medido ≠ esperado → `parcial`;
     `egress_expected` na visão por aparelho). Sem migração.
  2. Uma verificação de cadastro: dois perfis com o mesmo `egress_esperado` atribuídos a aparelhos diferentes →
     aviso na prévia do `assign` (hoje o aviso só nasce na medição).
  3. Depois, um modo "servidor remoto gerenciado": a plataforma gera a chave do aparelho (como faz com `servidor:
     central`), mantém a lista de pares e a publica no VPS (SSH ou API do sing-box); até lá, o VPS é mantido à mão e o
     dono gera as chaves.
- **Critérios de aceite do piloto** (tudo `real`, com data, commit e id de medição):
  1. Para cada um dos 2 aparelhos, `GET /api/network/devices` mostra `state: trafego_verificado`, `egress_ipv4`
     igual ao IP do VPS daquele aparelho, `egress_shared_with: []`, `leak_blocked: 1`, `udp_ok: 1` e `per_app` com
     todos os apps exigidos `ok` (sem conta vinculada: ao menos um app `ok`).
  2. O log do sing-box no VPS mostra `inbound connection from 10.66.0.2` só no VPS A e `…0.3` só no VPS B.
  3. Persistência: depois de um `restart` pedido pela plataforma e depois de hibernar/acordar o emulador, a
     conferência mantém `trafego_verificado` e uma nova medição (`POST …/verify`) devolve o mesmo IP.
  4. Falha fechada: parando o sing-box do VPS A, o aparelho A não produz medição alguma com o IP do central
     (lockdown), o estado regride e a porta de rede segura a tarefa; o aparelho B segue verificado.
  5. Nenhum segredo em log, evento, evidência ou Git (`redact` já cobre `PrivateKey`).
- **Rollback:** `POST /api/network/assign` com o perfil do central de volta (ou `vpn_profile_id: null`), que gera
  nova revisão e reaplica no ponto seguro; `DELETE` do perfil apaga a linha e o segredo juntos; destruir os VPS. O
  aparelho não guarda estado além do perfil importado no SFA, que a reaplicação substitui.
- **Alternativa — (c) proxy datacenter dedicado brasileiro por aparelho, pela VPN do central.** Um perfil `proxy/socks5`
  (ou `http`) por aparelho com `endpoint_host` do provedor, `params.username`, senha no `secret`, ao lado do perfil de
  VPN do central já em uso: `detour: wg-out` faz o proxy ser alcançado pelo túnel; o provedor vê a conexão vinda do IP
  do central (estável, serve para allowlist). Vantagens: zero mudança no servidor, sem VPS, o menor preço por IP, Brasil
  disponível (IPRoyal DC). Limites: TCP-only (QUIC do Instagram volta a TCP; `udp_ok` ficará 0, o que hoje não bloqueia
  `trafego_verificado`, mas o NTP e qualquer UDP do app não saem).
- **Por que não (d):** exige NAT ou rotas no host Windows e vários IPs públicos que a conexão do dono não tem; o
  dono excluiu mexer na rede do central (P2) e o ADR-056 descartou rota no host.

### Gaps

- Nenhum item do piloto foi executado: o estado é `not_run` com dependência "dono contrata 2 VPS (ou 1 VPS + IP
  adicional) e autoriza a atribuição em dois aparelhos sem conta real".
- Não medi o tempo de reconexão WireGuard com endpoint na internet depois da hibernação do emulador nem a latência
  São Paulo ↔ conexão do dono; ambos entram nos critérios de aceite como medição, não como premissa.
- A forma de configurar Floating/Additional IP no SO de cada provedor (alias na interface versus rota) não foi lida
  nas páginas de ajuda; é procedimento do degrau 2.
