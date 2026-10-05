#!/bin/sh
# Roda um hook em Python escolhendo o interpretador pela plataforma (29.147).
# Uso: sh python.sh <script.py>   (o script é achado ao lado deste arquivo; o JSON do hook segue pelo stdin).
#
# Por quê: `.claude/settings.json` chamava `C:/Program Files/Python313/python.exe`, que só existe no Windows do dono.
# O agente de nuvem do Copilot roda em Linux e honra esses hooks: sem o caminho, parava com "hook errored" antes de
# alterar qualquer coisa. Aqui o mesmo comando vale nas duas máquinas, e o que o hook FAZ não muda: mesmo `-S -E`,
# mesmo carregamento do script, o código de saída do Python passa direto (2 = bloqueia).
#
# Ordem: o Python do dono, se existir (no Windows o `python3` pode ser o atalho da Loja, que não roda); depois
# `python3` e `python` do PATH, o primeiro que de fato executa. Script ausente = sai 0, como antes (falha aberta).
# Sem nenhum interpretador: avisa no stderr; no Windows sai 1 (erro visível, como antes), fora dele sai 0 para um
# ambiente sem Python não parar o trabalho.

script="$1"
[ -n "$script" ] || { echo "python.sh: faltou o nome do script do hook" >&2; exit 0; }

dir=$(cd "$(dirname "$0")" 2>/dev/null && { pwd -W 2>/dev/null || pwd; }) || exit 0
alvo="$dir/$script"
[ -f "$alvo" ] || exit 0

interprete=""
fixo="${HOOK_PYTHON_WINDOWS:-C:/Program Files/Python313/python.exe}"
if [ -f "$fixo" ]; then
  interprete="$fixo"
else
  for c in python3 python; do
    caminho=$(command -v "$c" 2>/dev/null) || continue
    if "$caminho" -c "" >/dev/null 2>&1; then
      interprete="$caminho"
      break
    fi
  done
fi

if [ -z "$interprete" ]; then
  echo "python.sh: nenhum Python encontrado para o hook $script (a guarda NÃO rodou)" >&2
  case "$(uname -s 2>/dev/null)" in
    MINGW*|MSYS*|CYGWIN*) exit 1 ;;
    *) exit 0 ;;
  esac
fi

exec "$interprete" -S -E -c "import os,runpy,sys; p=sys.argv[1]; os.path.isfile(p) and runpy.run_path(p, run_name='__main__')" "$alvo"
