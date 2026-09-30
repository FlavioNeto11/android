#!/bin/bash
# Servidor WireGuard de UMA saída dedicada (piloto V1, item 29.7; ADR-056). Roda num servidor Linux novo (Ubuntu
# 22.04+/Debian 12, ou Oracle Linux 8/9), como root: colado como "user data"/cloud-init na criação (Lightsail:
# "launch script"; Oracle Cloud: "Show advanced options › Management › cloud-init script"), ou por SSH (`sudo bash`
# com este arquivo). Aceita UM par — o aparelho do perfil — e nada além do UDP do túnel (e do SSH, se ABRIR_SSH=1).
#
# O provedor tem um firewall próprio NA FRENTE do servidor, e ele também precisa deixar passar o UDP da porta:
# Lightsail, aba Networking (regra UDP 51820); Oracle Cloud, a Security List (ou NSG) da sub-rede, regra de entrada
# stateful UDP 51820 de 0.0.0.0/0. Sem ela, o script roda certo e o túnel não chega.
#
# Por que assim:
# - a chave privada do CLIENTE nunca passa por aqui: quem a gera é o dono, no central (`sing-box generate
#   wg-keypair`), e ela entra só no cofre, pelo formulário do perfil no painel. Aqui chega a PÚBLICA;
# - a chave privada do SERVIDOR nasce aqui e não sai daqui. O script imprime só o IPv4 público e a chave PÚBLICA
#   do servidor, e as grava em /etc/issue para aparecerem no console web do provedor, sem precisar de login;
# - sem IPv6 no túnel: o perfil manda o IPv6 do aparelho para o túnel, e aqui ele não tem saída — falha fechada,
#   em vez de sair pela rede do provedor com outro endereço;
# - rodar de novo é seguro: a chave do servidor é reaproveitada e o par é trocado pelo informado.
#
# Desfazer: apagar o servidor no provedor (é o rollback do piloto; o aparelho fica sem saída, com o bloqueio,
# até o perfil sair dele no painel — veja "Reversão do piloto de saída distinta" em docs/dominios/parque.md).
set -euo pipefail

# ---- preencha antes de rodar -------------------------------------------------------------------------------------
CHAVE_PUBLICA_DO_CLIENTE="${CHAVE_PUBLICA_DO_CLIENTE:-COLE-AQUI-A-CHAVE-PUBLICA-DO-CLIENTE}"
ENDERECO_DO_CLIENTE="${ENDERECO_DO_CLIENTE:-10.77.0.2/32}"   # o `address` do perfil no painel
ENDERECO_DO_SERVIDOR="${ENDERECO_DO_SERVIDOR:-10.77.0.1/24}"
PORTA="${PORTA:-51820}"
ABRIR_SSH="${ABRIR_SSH:-1}"   # 0 = só o UDP do túnel; a administração passa a ser pelo console web do provedor
# ------------------------------------------------------------------------------------------------------------------

case "$CHAVE_PUBLICA_DO_CLIENTE" in
  COLE-AQUI*) echo "ERRO: preencha CHAVE_PUBLICA_DO_CLIENTE (a chave PÚBLICA gerada no central)." >&2; exit 2 ;;
esac
# 44 caracteres de base64 terminando em '=': a forma de uma chave WireGuard. Recusa a privada colada por engano?
# Não dá para distinguir pela forma; por isso o aviso no painel e no procedimento: aqui vai a linha "PublicKey".
if ! [[ "$CHAVE_PUBLICA_DO_CLIENTE" =~ ^[A-Za-z0-9+/]{42}[AEIMQUYcgkosw048]=$ ]]; then
  echo "ERRO: CHAVE_PUBLICA_DO_CLIENTE não tem a forma de uma chave WireGuard (44 caracteres base64)." >&2; exit 2
fi

# Pacotes e o dono do filtro de pacotes, por família. Um filtro só manda aqui: num gancho do netfilter, um `drop`
# de QUALQUER tabela vence, então o REJECT final que a imagem Ubuntu da Oracle Cloud carrega do `iptables`
# (netfilter-persistent, /etc/iptables/rules.v4) ou o firewalld do Oracle Linux derrubariam o UDP do túnel mesmo com
# a regra abaixo. Os dois são desligados, e as regras antigas ficam guardadas ao lado (desfazer = religar).
if command -v apt-get >/dev/null; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq wireguard-tools nftables >/dev/null
  if systemctl list-unit-files netfilter-persistent.service >/dev/null 2>&1; then
    systemctl disable --now -q netfilter-persistent || true
    for f in /etc/iptables/rules.v4 /etc/iptables/rules.v6; do
      [ -f "$f" ] && mv "$f" "$f.antes-saida-dedicada"
    done
  fi
  REGRAS=/etc/nftables.conf
elif command -v dnf >/dev/null; then
  # Oracle Linux 8/9 (e família RHEL): wireguard-tools vem do repositório da distribuição; o módulo, do kernel UEK.
  dnf install -y -q wireguard-tools nftables
  if systemctl is-enabled -q firewalld 2>/dev/null; then
    systemctl disable --now -q firewalld
  fi
  mkdir -p /etc/nftables
  REGRAS=/etc/nftables/saida-dedicada.nft
  grep -qF "$REGRAS" /etc/sysconfig/nftables.conf 2>/dev/null || echo "include \"$REGRAS\"" >> /etc/sysconfig/nftables.conf
else
  echo "ERRO: sem apt-get nem dnf. Use Ubuntu 22.04+/Debian 12 ou Oracle Linux 8/9." >&2; exit 1
fi

umask 077
mkdir -p /etc/wireguard
[ -s /etc/wireguard/servidor.key ] || wg genkey > /etc/wireguard/servidor.key
PUBLICA_DO_SERVIDOR="$(wg pubkey < /etc/wireguard/servidor.key)"

# A interface de saída é a da rota padrão (eth0, ens3, enp1s0… varia por provedor).
SAIDA="$(ip -4 route show default | awk '{for (i=1;i<NF;i++) if ($i=="dev") {print $(i+1); exit}}')"
[ -n "$SAIDA" ] || { echo "ERRO: sem rota padrão IPv4." >&2; exit 1; }

cat > /etc/wireguard/wg0.conf <<CONF
[Interface]
Address = ${ENDERECO_DO_SERVIDOR}
ListenPort = ${PORTA}
PostUp = wg set %i private-key /etc/wireguard/servidor.key

[Peer]
PublicKey = ${CHAVE_PUBLICA_DO_CLIENTE}
AllowedIPs = ${ENDERECO_DO_CLIENTE}
CONF

# Encaminhamento só de IPv4: o aparelho sai com o IPv4 deste servidor e nenhum outro.
cat > /etc/sysctl.d/90-saida-dedicada.conf <<SYS
net.ipv4.ip_forward = 1
SYS
sysctl -q -p /etc/sysctl.d/90-saida-dedicada.conf

SSH_REGRA=""
[ "$ABRIR_SSH" = "1" ] && SSH_REGRA="tcp dport 22 accept"
cat > "$REGRAS" <<NFT
#!/usr/sbin/nft -f
flush ruleset
table inet filtro {
  chain entrada {
    type filter hook input priority 0; policy drop;
    iif lo accept
    ct state established,related accept
    icmp type echo-request limit rate 5/second accept
    udp dport ${PORTA} accept
    ${SSH_REGRA}
  }
  chain encaminhar {
    type filter hook forward priority 0; policy drop;
    iifname "wg0" oifname "${SAIDA}" ip saddr ${ENDERECO_DO_CLIENTE} accept
    iifname "${SAIDA}" oifname "wg0" ct state established,related accept
  }
}
table ip saida {
  chain pos {
    type nat hook postrouting priority 100;
    oifname "${SAIDA}" ip saddr ${ENDERECO_DO_CLIENTE} masquerade
  }
}
NFT
systemctl enable -q nftables
systemctl restart nftables
systemctl enable -q wg-quick@wg0
systemctl restart wg-quick@wg0

# O IPv4 da interface. Em provedor com NAT 1:1 (o IP público não aparece na interface) ele difere do público: aí
# vale o IP que o painel do provedor mostra, e é esse que entra como `egress_esperado`.
LOCAL="$(ip -4 -o addr show dev "$SAIDA" scope global | awk '{print $4}' | cut -d/ -f1 | head -1)"

BLOCO="
Saída dedicada (Central de Aparelhos, piloto V1)
  IPv4 da interface ${SAIDA}: ${LOCAL}
  porta UDP: ${PORTA}
  chave PÚBLICA do servidor: ${PUBLICA_DO_SERVIDOR}
"
echo "$BLOCO"
# No /etc/issue, uma vez só: é o que o console web do provedor mostra antes do login.
grep -qF "$PUBLICA_DO_SERVIDOR" /etc/issue 2>/dev/null || printf '%s\n' "$BLOCO" >> /etc/issue

echo "ok: wg0 no ar, um par (${ENDERECO_DO_CLIENTE}); nada além do UDP ${PORTA}$( [ "$ABRIR_SSH" = "1" ] && echo ' e do SSH') aceito."
