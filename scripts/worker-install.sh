#!/usr/bin/env bash
# Instala (ou atualiza) o AGENTE do parque numa máquina LINUX: pacote, venv, configuração e unidade systemd.
#
# Achados #14 e #180. A decisão de entrada do parque é workers MISTOS (Windows e Linux), mas o suporte a Linux
# era só intenção: nenhum instalador, nenhuma unidade systemd versionada (`git ls-files | grep systemd` = vazio)
# e o único worker real é Windows. O código do agente é portátil — as marcas de Windows estão isoladas em
# `devices/sdk.py` e viram 0 fora dele —, o que faltava era o caminho até a máquina.
#
# O equivalente Windows é `scripts/worker-install.ps1`, e os dois fazem a mesma coisa na mesma ordem:
# copiar o pacote, gravar a versão derivada do commit, criar o venv, semear a configuração, registrar o serviço.
#
#   sudo bash scripts/worker-install.sh --dry-run          # o plano, sem tocar em nada
#   sudo bash scripts/worker-install.sh --enroll <token>   # primeira instalação
#   sudo bash scripts/worker-install.sh                    # atualização do agente
#
# O que ele NÃO faz: instalar o Android SDK. Baixe as command-line tools e rode
# `sdkmanager 'platform-tools' 'emulator' 'system-images;android-34;google_apis;x86_64'`; o caminho padrão do
# agente no Linux é ~/Android/Sdk, e `sdk_root` no YAML manda sobre ele.
set -euo pipefail

ORIGEM="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DESTINO=/opt/farm/agent
WORK_DIR=/var/lib/farm
CONFIG=/etc/farm/worker.yaml
LOG=/var/lib/farm/logs/agente.log
USUARIO=farm
UNIDADE=farm-worker
ENROLL=""
DRY_RUN=0

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --enroll) ENROLL="${2:?--enroll precisa do token}"; shift ;;
    --destino) DESTINO="${2:?}"; shift ;;
    --work-dir) WORK_DIR="${2:?}"; shift ;;
    --config) CONFIG="${2:?}"; shift ;;
    --usuario) USUARIO="${2:?}"; shift ;;
    --origem) ORIGEM="${2:?}"; shift ;;
    -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
    *) echo "opção desconhecida: $1" >&2; exit 2 ;;
  esac
  shift
done

APP_ORIGEM="$ORIGEM/backend/app"
REQUISITOS="$ORIGEM/backend/worker-requirements.txt"
EXEMPLO="$ORIGEM/config/worker.example.yaml"
UNIDADE_MODELO="$ORIGEM/config/farm-worker.service"
# Só os módulos que o agente importa de verdade, conferido por `import app.worker.agent` + sys.modules.
PASTAS="worker workers devices security"
ARQUIVOS="__init__.py config.py util.py version.py metricas.py"

for caminho in "$APP_ORIGEM" "$REQUISITOS" "$EXEMPLO" "$UNIDADE_MODELO"; do
  [ -e "$caminho" ] || { echo "não encontrei $caminho — confira --origem." >&2; exit 1; }
done

# A versão que o agente vai declarar: `0.1.0+<sha7>`, lida do .git sem chamar `git` (ele pode não estar aqui).
versao_do_commit() {
  local cabeca ref sha
  cabeca="$(cat "$ORIGEM/.git/HEAD" 2>/dev/null || true)"
  case "$cabeca" in
    ref:*) ref="${cabeca#ref: }"; sha="$(cat "$ORIGEM/.git/$ref" 2>/dev/null || true)" ;;
    *)     sha="$cabeca" ;;
  esac
  if [ -n "$sha" ]; then echo "0.1.0+${sha:0:7}"; else echo "0.1.0+desconhecido"; fi
}
VERSAO="$(versao_do_commit)"

# ---------------------------------------------------------------- pré-voo: KVM
# Sem /dev/kvm o emulador não sobe em tempo útil. Avisa alto e segue: quem instala pode estar preparando a
# máquina antes de habilitar a virtualização na BIOS, e travar aqui não ajudaria.
if [ ! -e /dev/kvm ]; then
  echo "AVISO: /dev/kvm não existe. Habilite a virtualização (VT-x/AMD-V) e instale o KVM; sem ele o emulador" >&2
  echo "       roda em emulação de software e um boot de 2 min vira dezenas." >&2
elif ! getent group kvm >/dev/null; then
  echo "AVISO: não há grupo 'kvm' nesta máquina; a unidade systemd pede SupplementaryGroups=kvm." >&2
fi

if [ "$DRY_RUN" = "1" ]; then
  echo "origem: $ORIGEM"
  echo "destino: $DESTINO"
  echo "versao: $VERSAO"
  echo "pastas: $PASTAS"
  echo "arquivos: $ARQUIVOS"
  echo "config: $CONFIG"
  echo "usuario: $USUARIO"
  echo "unidade: /etc/systemd/system/$UNIDADE.service"
  echo "log: $LOG"
  echo "kvm: $([ -e /dev/kvm ] && echo presente || echo ausente)"
  [ -n "$ENROLL" ] && echo "inscricao: rodaria o agente uma vez em primeiro plano (token nao e impresso)"
  echo "simulacao: nada foi copiado, instalado nem registrado"
  exit 0
fi

[ "$(id -u)" = "0" ] || { echo "rode com sudo: a unidade systemd e /opt precisam de root." >&2; exit 1; }

# ---------------------------------------------------------------- 1. conta de serviço
if ! id -u "$USUARIO" >/dev/null 2>&1; then
  echo "criando a conta de serviço '$USUARIO' (sem shell de login)"
  useradd --system --create-home --shell /usr/sbin/nologin "$USUARIO"
fi
getent group kvm >/dev/null && usermod -aG kvm "$USUARIO" || true

# ---------------------------------------------------------------- 2. parar antes de trocar os arquivos
if systemctl list-unit-files | grep -q "^$UNIDADE.service"; then
  echo "parando $UNIDADE antes de trocar os arquivos (os emuladores ficam de pé: KillMode=process)"
  systemctl stop "$UNIDADE" || true
fi

# ---------------------------------------------------------------- 3. pacote
install -d -o "$USUARIO" -g "$USUARIO" -m 0755 "$DESTINO" "$DESTINO/app" "$WORK_DIR" "$(dirname "$LOG")"
install -d -m 0755 "$(dirname "$CONFIG")"
for pasta in $PASTAS; do
  rm -rf "${DESTINO:?}/app/$pasta"
  cp -r "$APP_ORIGEM/$pasta" "$DESTINO/app/$pasta"
  find "$DESTINO/app/$pasta" -name __pycache__ -type d -prune -exec rm -rf {} +
done
for arquivo in $ARQUIVOS; do
  cp "$APP_ORIGEM/$arquivo" "$DESTINO/app/$arquivo"
done
echo "$VERSAO" > "$DESTINO/app/BUILD_VERSION"
cp "$REQUISITOS" "$DESTINO/worker-requirements.txt"
chown -R "$USUARIO:$USUARIO" "$DESTINO"
echo "pacote do agente em $DESTINO (versao $VERSAO)"

# ---------------------------------------------------------------- 4. ambiente Python
if [ ! -x "$DESTINO/.venv/bin/python" ]; then
  python3 -m venv "$DESTINO/.venv"
fi
"$DESTINO/.venv/bin/python" -m pip install --disable-pip-version-check -q -r "$DESTINO/worker-requirements.txt"
chown -R "$USUARIO:$USUARIO" "$DESTINO/.venv"
echo "dependências do agente instaladas (sete, não as do backend)."

# ---------------------------------------------------------------- 5. configuração
if [ ! -f "$CONFIG" ]; then
  # O exemplo é o do Windows (`sdk_root: C:\Android\Sdk`, `work_dir: C:\farm`). Copiá-lo cru aqui deixaria o
  # agente gravando a credencial num caminho que não é o que este instalador confere no passo da inscrição —
  # e o aviso "ajuste antes de inscrever" não salva quem instala com `--enroll` na mesma linha.
  sed -e "s|^work_dir:.*|work_dir: $WORK_DIR|" \
      -e "s|^sdk_root:.*|sdk_root: $(getent passwd "$USUARIO" | cut -d: -f6)/Android/Sdk|" \
      "$EXEMPLO" > "$CONFIG"
  chown "$USUARIO:$USUARIO" "$CONFIG"
  chmod 0640 "$CONFIG"
  echo "AVISO: configuração criada em $CONFIG a partir do exemplo. Ajuste server, worker_id, name, sdk_root," >&2
  echo "       work_dir e devices ANTES de inscrever — o exemplo não descreve esta máquina." >&2
else
  echo "configuração já existente em $CONFIG (não foi tocada)."
fi

# ---------------------------------------------------------------- 6. inscrição (uma vez, em primeiro plano)
if [ -n "$ENROLL" ] && [ ! -f "$WORK_DIR/worker-credential.json" ]; then
  echo "trocando o token de inscrição pela credencial permanente…"
  # O token vai só como argumento deste processo e nunca é impresso. A tarefa/unidade NUNCA o carrega: ele é de
  # uso único e já terá sido gasto aqui.
  timeout 120 sudo -u "$USUARIO" "$DESTINO/.venv/bin/python" -m app.worker \
      --config "$CONFIG" --log-file "$LOG" --enroll "$ENROLL" || true
  [ -f "$WORK_DIR/worker-credential.json" ] || {
    echo "a credencial permanente não apareceu em $WORK_DIR. Veja o fim de $LOG: token vencido, já usado, ou" >&2
    echo "o central inalcançável são as três causas comuns." >&2
    exit 1
  }
fi

# ---------------------------------------------------------------- 7. unidade systemd
sed -e "s|__AGENT_DIR__|$DESTINO|g" -e "s|__CONFIG__|$CONFIG|g" -e "s|__LOG__|$LOG|g" \
    -e "s|__USUARIO__|$USUARIO|g" "$UNIDADE_MODELO" > "/etc/systemd/system/$UNIDADE.service"
systemctl daemon-reload
systemctl enable "$UNIDADE"
systemctl restart "$UNIDADE"
echo "unidade '$UNIDADE' habilitada e no ar. Acompanhe: journalctl -u $UNIDADE -f  (e $LOG)"
