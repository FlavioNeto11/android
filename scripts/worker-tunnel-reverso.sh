#!/usr/bin/env bash
# Túnel iniciado PELO WORKER, versão Linux/macOS. Irmão de `worker-tunnel-reverso.ps1` — mesma ideia, mesmo
# formato de mapa, mesmo arquivo de mapa gerado pelo central (`data/tunnel/<worker>.map`).
#
# Por que ele existe: `scripts/worker-tunnel.ps1` roda no SERVIDOR CENTRAL e abre ssh PARA o worker, o que exige
# sshd no worker e rota do central até ele. Atrás de um NAT que não se controla isso não acontece. Aqui quem
# inicia a conexão é o worker, então nenhuma porta precisa ser alcançável de fora desta máquina.
#
# No central, a conta do túnel é restrita a encaminhamento (sem shell), em authorized_keys:
#   restrict,port-forwarding,permitopen="127.0.0.1:8010" ssh-ed25519 AAAA... worker-01
#
# Uso:
#   CENTRAL=central.exemplo MAPA='15555:5555,15557:5557' ./scripts/worker-tunnel-reverso.sh
#   CENTRAL=central.exemplo MAPA_ARQUIVO=/etc/farm/worker-01.map ./scripts/worker-tunnel-reverso.sh
#   MOSTRAR_MAPA=1 MAPA_ARQUIVO=/etc/farm/worker-01.map ./scripts/worker-tunnel-reverso.sh
#
# Como serviço (systemd), sem inventar caminho: aponte ExecStart para este script com as variáveis no
# Environment=, Restart=always e RestartSec=10.
set -uo pipefail

CENTRAL="${CENTRAL:-central.local}"
USUARIO="${USUARIO:-farm-tunnel}"
CHAVE="${CHAVE:-$HOME/.ssh/central_ed25519}"
MAPA="${MAPA:-15555:5555}"                 # portaNoCentral:portaDeAdbAqui,...
MAPA_ARQUIVO="${MAPA_ARQUIVO:-}"
MAPA_API="${MAPA_API:-18000:8010}"         # portaAqui:portaNoCentral (NUNCA a 8000 do central: ver o .ps1)
MOSTRAR_MAPA="${MOSTRAR_MAPA:-}"

resolve_mapa() {
  # Arquivo quando existe e tem conteúdo; senão o MAPA. Arquivo ilegível não derruba o túnel.
  if [ -n "$MAPA_ARQUIVO" ] && [ -r "$MAPA_ARQUIVO" ]; then
    local bruto
    bruto="$(tr -d '[:space:]' < "$MAPA_ARQUIVO" 2>/dev/null || true)"
    if [ -n "$bruto" ]; then printf '%s' "$bruto"; return; fi
  fi
  printf '%s' "$MAPA"
}

if [ -n "$MOSTRAR_MAPA" ]; then resolve_mapa; echo; exit 0; fi

registra() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

seguidas=0
while true; do
  mapa_atual="$(resolve_mapa)"
  args=()
  IFS=',' read -r -a entradas <<< "$mapa_atual"
  for e in "${entradas[@]}"; do
    [ -n "$e" ] || continue
    case "$e" in
      *:*) args+=(-R "${e%%:*}:127.0.0.1:${e##*:}") ;;
      *) registra "entrada inválida no mapa: '$e' (esperado portaNoCentral:portaDeAdbAqui)"; ;;
    esac
  done
  if [ ${#args[@]} -eq 0 ]; then registra 'mapa vazio; nova tentativa em 30s'; sleep 30; continue; fi
  args+=(-L "${MAPA_API%%:*}:127.0.0.1:${MAPA_API##*:}")

  registra "iniciando; mapa=$mapa_atual api=$MAPA_API"
  inicio=$SECONDS
  ssh -i "$CHAVE" -N \
      -o BatchMode=yes -o ExitOnForwardFailure=yes \
      -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
      -o StrictHostKeyChecking=accept-new \
      "${args[@]}" "$USUARIO@$CENTRAL" &
  ssh_pid=$!
  # Vigia o arquivo de mapa enquanto o túnel está de pé: aparelho adotado no painel entra sem reinstalar nada.
  while kill -0 "$ssh_pid" 2>/dev/null; do
    sleep 5
    if [ "$(resolve_mapa)" != "$mapa_atual" ]; then
      registra 'o mapa mudou; derrubando o ssh para subir com as portas novas'
      kill "$ssh_pid" 2>/dev/null || true
      break
    fi
  done
  wait "$ssh_pid" 2>/dev/null || true
  viveu=$(( SECONDS - inicio ))
  if [ "$viveu" -lt 10 ]; then seguidas=$(( seguidas + 1 )); else seguidas=0; fi
  espera=$(( 5 * (seguidas > 0 ? seguidas : 1) )); [ "$espera" -gt 60 ] && espera=60
  registra "ssh saiu depois de ${viveu}s (falhas rapidas seguidas=$seguidas); nova tentativa em ${espera}s"
  sleep "$espera"
done
