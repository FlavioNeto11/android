#!/bin/bash
# Servidor WireGuard de saída DEDICADA por aparelho (item 29.7; ADR-056). Roda num servidor Linux novo (Ubuntu
# 22.04+/Debian 12, ou Oracle Linux 8/9), como root: colado como "user data"/cloud-init na criação (Lightsail:
# "launch script"; Oracle Cloud: "Show advanced options › Management › cloud-init script"), ou por SSH (`sudo bash`
# com este arquivo). Cada par é UM aparelho (um perfil no painel); com mais de um IP no servidor, cada par sai por
# um IP diferente (SNAT pela origem no túnel). Nada além do UDP do túnel é aceito (e o SSH, se ABRIR_SSH=1).
#
# O provedor tem um firewall próprio NA FRENTE do servidor, e ele também precisa deixar passar o UDP da porta:
# Lightsail, aba Networking (regra UDP 51820); Oracle Cloud, a Security List (ou NSG) da sub-rede, regra de entrada
# stateful UDP 51820 de 0.0.0.0/0. Sem ela, o script roda certo e o túnel não chega.
#
# Por que assim:
# - a chave privada do CLIENTE nunca passa por aqui: quem a gera é o dono, no central (`sing-box generate
#   wg-keypair`), e ela entra só no cofre, pelo formulário do perfil no painel. Aqui chega a PÚBLICA;
# - a chave privada do SERVIDOR nasce aqui e não sai daqui. O script imprime só a chave PÚBLICA do servidor e os
#   IPv4 das interfaces, e os grava em /etc/issue para aparecerem no console web do provedor;
# - dois pares nunca dividem um IP de saída sem que isso esteja escrito em PARES: o par sem IP próprio sai pelo IP
#   principal do servidor, e o script avisa quando mais de um par cai nele;
# - sem IPv6 no túnel: o perfil manda o IPv6 do aparelho para o túnel, e aqui ele não tem saída — falha fechada,
#   em vez de sair pela rede do provedor com outro endereço;
# - rodar de novo é seguro: a chave do servidor é reaproveitada e os pares passam a ser os informados.
#
# Desfazer: apagar o servidor no provedor (é o rollback do piloto; o aparelho fica sem saída, com o bloqueio,
# até o perfil sair dele no painel — veja "Reversão do piloto de saída distinta" em docs/dominios/parque.md).
set -euo pipefail

# ---- preencha antes de rodar -------------------------------------------------------------------------------------
# Um par por linha: "<chave PÚBLICA do cliente> <endereço no túnel>/32 [IPv4 local de saída]". O IPv4 local de saída é
# um endereço DESTE servidor (na Oracle Cloud, o IP privado secundário da VNIC ao qual o IP público foi anexado);
# sem ele, o par sai pelo IP principal. O endereço no túnel é o `address` do perfil no painel.
PARES="${PARES:-COLE-AQUI-A-CHAVE-PUBLICA-DO-CLIENTE 10.77.0.2/32}"
ENDERECO_DO_SERVIDOR="${ENDERECO_DO_SERVIDOR:-10.77.0.1/24}"
PORTA="${PORTA:-51820}"
ABRIR_SSH="${ABRIR_SSH:-1}"   # 0 = só o UDP do túnel; a administração passa a ser pelo console web do provedor
# Compatível com a primeira versão (um par): CHAVE_PUBLICA_DO_CLIENTE e ENDERECO_DO_CLIENTE, se vierem, viram PARES.
if [ -n "${CHAVE_PUBLICA_DO_CLIENTE:-}" ]; then
  PARES="${CHAVE_PUBLICA_DO_CLIENTE} ${ENDERECO_DO_CLIENTE:-10.77.0.2/32}"
fi
# ------------------------------------------------------------------------------------------------------------------

erro() { echo "ERRO: $*" >&2; exit 2; }

# Lê e confere os pares ANTES de instalar qualquer coisa.
CHAVES=(); ENDERECOS=(); SAIDAS=()
while read -r chave endereco saida resto; do
  [ -z "${chave:-}" ] && continue
  case "$chave" in \#*) continue ;; COLE-AQUI*) erro "preencha PARES com a chave PÚBLICA de cada cliente." ;; esac
  [ -z "${resto:-}" ] || erro "par com campos a mais: '$chave $endereco $saida $resto'."
  # 44 caracteres de base64 terminando em '=': a forma de uma chave WireGuard. A privada colada por engano tem a
  # mesma forma; por isso o procedimento diz: aqui vai a linha "PublicKey".
  [[ "$chave" =~ ^[A-Za-z0-9+/]{42}[AEIMQUYcgkosw048]=$ ]] || erro "'$chave' não tem a forma de uma chave WireGuard."
  [[ "${endereco:-}" =~ ^10\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}/32$ ]] || erro "endereço no túnel '${endereco:-}' não é um 10.x.y.z/32."
  if [ -n "${saida:-}" ]; then
    [[ "$saida" =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]] || erro "IPv4 de saída '$saida' ilegível."
  fi
  for i in "${!CHAVES[@]}"; do
    [ "${CHAVES[$i]}" != "$chave" ] || erro "a mesma chave em dois pares."
    [ "${ENDERECOS[$i]}" != "$endereco" ] || erro "o endereço $endereco em dois pares."
    [ -z "${saida:-}" ] || [ "${SAIDAS[$i]}" != "$saida" ] || erro "o IP de saída $saida em dois pares: a saída deixaria de ser dedicada."
  done
  CHAVES+=("$chave"); ENDERECOS+=("$endereco"); SAIDAS+=("${saida:-}")
done <<< "$(printf '%s\n' "$PARES" | tr ';' '\n')"
[ "${#CHAVES[@]}" -gt 0 ] || erro "PARES vazio."

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

# Os IPs de saída declarados precisam existir nesta máquina: a Oracle Cloud atribui o IP privado secundário à VNIC,
# mas o Ubuntu não o põe na interface sozinho. O que faltar entra no PostUp do wg0 (e sai no PostDown), então vale a
# cada boot sem arquivo de rede novo.
POSTUP=""; POSTDOWN=""
for s in "${SAIDAS[@]}"; do
  [ -n "$s" ] || continue
  if ! ip -4 -o addr show | awk '{print $4}' | cut -d/ -f1 | grep -qx "$s"; then
    POSTUP+="PostUp = ip -4 addr add ${s}/32 dev ${SAIDA} || true"$'\n'
    POSTDOWN+="PostDown = ip -4 addr del ${s}/32 dev ${SAIDA} || true"$'\n'
  fi
done

{
  echo "[Interface]"
  echo "Address = ${ENDERECO_DO_SERVIDOR}"
  echo "ListenPort = ${PORTA}"
  echo "PostUp = wg set %i private-key /etc/wireguard/servidor.key"
  printf '%s' "$POSTUP" "$POSTDOWN"
  for i in "${!CHAVES[@]}"; do
    echo ""
    echo "[Peer]"
    echo "PublicKey = ${CHAVES[$i]}"
    echo "AllowedIPs = ${ENDERECOS[$i]}"
  done
} > /etc/wireguard/wg0.conf

# Encaminhamento só de IPv4: o aparelho sai com um IPv4 deste servidor e nenhum outro.
cat > /etc/sysctl.d/90-saida-dedicada.conf <<SYS
net.ipv4.ip_forward = 1
SYS
sysctl -q -p /etc/sysctl.d/90-saida-dedicada.conf

ENCAMINHAR=""; NAT=""; PRINCIPAL=0
for i in "${!CHAVES[@]}"; do
  origem="${ENDERECOS[$i]}"
  ENCAMINHAR+="    iifname \"wg0\" oifname \"${SAIDA}\" ip saddr ${origem} accept"$'\n'
  if [ -n "${SAIDAS[$i]}" ]; then
    NAT+="    oifname \"${SAIDA}\" ip saddr ${origem} snat to ${SAIDAS[$i]}"$'\n'
  else
    NAT+="    oifname \"${SAIDA}\" ip saddr ${origem} masquerade"$'\n'
    PRINCIPAL=$((PRINCIPAL + 1))
  fi
done
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
${ENCAMINHAR}    iifname "${SAIDA}" oifname "wg0" ct state established,related accept
  }
}
table ip saida {
  chain pos {
    type nat hook postrouting priority 100;
${NAT}  }
}
NFT
nft -c -f "$REGRAS"   # confere a sintaxe antes de trocar o filtro no ar
systemctl enable -q nftables
systemctl restart nftables
systemctl enable -q wg-quick@wg0
systemctl restart wg-quick@wg0

# Os IPv4 das interfaces. Em provedor com NAT 1:1 (Lightsail, Oracle Cloud) o IP público não aparece aqui: vale o
# que o painel do provedor mostra para cada IP local, e é esse que entra como `egress_esperado` do perfil.
LOCAIS="$(ip -4 -o addr show dev "$SAIDA" scope global | awk '{print $4}' | cut -d/ -f1 | tr '\n' ' ')"
BLOCO="
Saída dedicada (Central de Aparelhos)
  IPv4 locais em ${SAIDA}: ${LOCAIS}
  porta UDP: ${PORTA}
  chave PÚBLICA do servidor: ${PUBLICA_DO_SERVIDOR}
"
echo "$BLOCO"
for i in "${!CHAVES[@]}"; do
  echo "  par ${ENDERECOS[$i]} sai por ${SAIDAS[$i]:-o IP principal}"
done
if [ "$PRINCIPAL" -gt 1 ]; then
  echo "AVISO: ${PRINCIPAL} pares saem pelo IP principal — para eles a saída NÃO é dedicada." >&2
fi
# No /etc/issue, uma vez só: é o que o console web do provedor mostra antes do login.
grep -qF "$PUBLICA_DO_SERVIDOR" /etc/issue 2>/dev/null || printf '%s\n' "$BLOCO" >> /etc/issue

echo "ok: wg0 no ar com ${#CHAVES[@]} par(es); nada além do UDP ${PORTA}$( [ "$ABRIR_SSH" = "1" ] && echo ' e do SSH') aceito."
