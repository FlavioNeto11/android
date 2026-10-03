"""Grava, recua ou ensaia `avisos.url_painel` no config.yaml do central (ida ao ar do portal, 29.54).

Pedido da orquestradora (03/10 20:56Z): a linha entra no MESMO reinício em que ela aplica o hostname, depois do
deploy 15 e só com o sinal "grave o url_painel". Se a prova de fora falhar, recua no reinício de volta.

Uso (da raiz do repositório):
  backend/.venv/Scripts/python.exe .claude/canais/url_painel.py --ensaio   # só diz o que mudaria
  backend/.venv/Scripts/python.exe .claude/canais/url_painel.py --gravar   # backup + grava a linha
  backend/.venv/Scripts/python.exe .claude/canais/url_painel.py --recuar   # backup + tira a linha (se for a nossa)

Edita por linha, sem reescrever o YAML: comentários e ordem ficam. Nunca imprime o arquivo, só a linha tocada e o
número dela. Não reinicia nada: o reinício é da orquestradora.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parents[2]
CONFIG = RAIZ / "config" / "config.yaml"
BACKUPS = RAIZ / ".claude" / "handoffs" / "canais" / "backups"          # fora do Git
VALOR = "https://dev.nvit.com.br/central"
CHAVE = "url_painel"


def _bloco_avisos(linhas: list[str]) -> tuple[int, int, str] | None:
    """(início, fim exclusivo, recuo dos filhos) do bloco `avisos:` de 1º nível, ou None sem âncora."""
    inicio = next((i for i, l in enumerate(linhas) if re.match(r"^avisos:\s*(#.*)?$", l)), None)
    if inicio is None:
        return None
    fim, recuo = len(linhas), ""
    for j in range(inicio + 1, len(linhas)):
        l = linhas[j]
        if not l.strip() or l.lstrip().startswith("#"):
            continue
        if not l[0].isspace():                 # próxima chave de 1º nível
            fim = j
            break
        if not recuo:
            recuo = l[: len(l) - len(l.lstrip())]
    return inicio, fim, recuo or "  "


def _linha_atual(linhas: list[str], bloco: tuple[int, int, str]) -> int | None:
    inicio, fim, recuo = bloco
    padrao = re.compile(rf"^{re.escape(recuo)}{CHAVE}:")
    return next((i for i in range(inicio + 1, fim) if padrao.match(linhas[i])), None)


def _valor_carregado(texto: str) -> object:
    dados = yaml.safe_load(texto) or {}
    return (dados.get("avisos") or {}).get(CHAVE)


def _backup() -> Path:
    BACKUPS.mkdir(exist_ok=True)
    destino = BACKUPS / f"config.yaml.{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    shutil.copy2(CONFIG, destino)
    return destino


def main() -> int:
    ap = argparse.ArgumentParser()
    modo = ap.add_mutually_exclusive_group(required=True)
    modo.add_argument("--ensaio", action="store_true")
    modo.add_argument("--gravar", action="store_true")
    modo.add_argument("--recuar", action="store_true")
    a = ap.parse_args()

    if not CONFIG.exists():
        print("config/config.yaml não existe; nada feito")
        return 2
    texto = CONFIG.read_text(encoding="utf-8")
    linhas = texto.splitlines(keepends=True)
    bloco = _bloco_avisos([l.rstrip("\r\n") for l in linhas])
    if bloco is None:
        print("âncora: NÃO existe bloco `avisos:` de 1º nível; nada feito")
        return 3
    inicio, fim, recuo = bloco
    atual = _linha_atual([l.rstrip("\r\n") for l in linhas], bloco)
    fim_de_linha = "\r\n" if linhas[inicio].endswith("\r\n") else "\n"
    nova = f"{recuo}{CHAVE}: {VALOR}{fim_de_linha}"
    print(f"âncora: bloco `avisos:` na linha {inicio + 1} (filhos com {len(recuo)} espaços, até a linha {fim})")
    print(f"hoje: {CHAVE} {'na linha ' + str(atual + 1) if atual is not None else 'ausente'}; "
          f"valor carregado = {_valor_carregado(texto)!r}")

    if a.recuar:
        if atual is None:
            print("recuar: nada a tirar")
            return 0
        if linhas[atual].strip() != nova.strip():
            print(f"recuar: a linha {atual + 1} não é a nossa ({CHAVE} com outro valor); nada feito")
            return 4
        destino = _backup()
        del linhas[atual]
    else:
        if atual is not None and linhas[atual].strip() == nova.strip():
            print("gravar: já está com o valor certo; nada a fazer")
            return 0
        if atual is not None:
            print(f"mudaria a linha {atual + 1} para: {nova.strip()}")
        else:
            print(f"acrescentaria na linha {inicio + 2}: {nova.strip()}")
        if a.ensaio:
            print("ensaio: nada gravado")
            return 0
        destino = _backup()
        if atual is not None:
            linhas[atual] = nova
        else:
            linhas.insert(inicio + 1, nova)

    novo_texto = "".join(linhas)
    esperado = None if a.recuar else VALOR
    if _valor_carregado(novo_texto) != esperado:
        print("conferência falhou: o YAML novo não carrega o valor esperado; nada gravado")
        return 5
    CONFIG.write_text(novo_texto, encoding="utf-8", newline="")
    print(f"gravado ({'recuo' if a.recuar else 'gravação'}); backup em {destino.relative_to(RAIZ)}; "
          f"valor carregado agora = {_valor_carregado(novo_texto)!r}. Reinício: com a orquestradora.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
