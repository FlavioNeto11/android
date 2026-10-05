"""A régua da borda para a prova de fora (29.97): a MESMA de `backend/app/modules/portal/domain/borda.py`, que o vigia
do central usa. O script de prova baixa com `curl` e passa o que veio pelo stdin; aqui só se decide e se imprime a
linha `ok` / `FALHOU` no formato da prova (ASCII, coluna de 28).

Carrega o módulo de domínio pelo caminho: roda com qualquer Python 3.11+, sem o venv e sem o app.

Uso (o stdin é o corpo, ou os cabeçalhos do `curl -D -`):
  portal-regua-da-borda.py pagina     --rotulo R --onde / --host H --status 200
  portal-regua-da-borda.py cabecalhos --rotulo R --onde / [--sem-transformar] [--gzip] [--csp site|aplicar|so_relatar]
                                      [--sem-cookie]
  portal-regua-da-borda.py versao     --rotulo R --onde /assets/site.css --versao V
  portal-regua-da-borda.py versoes    (a raiz no stdin; imprime "<caminho> <versao>" do CSS e do JS)
Sai com 0 (ok) ou 1 (FALHOU, inclusive "nao conferido").
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
import unicodedata
from pathlib import Path
from types import ModuleType

DOMINIO = Path(__file__).resolve().parents[1] / "backend" / "app" / "modules" / "portal" / "domain" / "borda.py"


def _regua() -> ModuleType:
    spec = importlib.util.spec_from_file_location("regua_da_borda", DOMINIO)
    assert spec is not None and spec.loader is not None, DOMINIO
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modulo                  # o `dataclass` procura o módulo aqui
    spec.loader.exec_module(modulo)
    return modulo


def _ascii(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")


def _cabecalhos(texto: str) -> tuple[int, dict[str, str]]:
    """O último bloco do `curl -D -` (o de depois de um 100 Continue, se houver): status e cabeçalhos."""
    blocos = [b for b in texto.replace("\r", "").split("\n\n") if b.strip()]
    linhas = blocos[-1].split("\n") if blocos else [""]
    partes = linhas[0].split()
    status = int(partes[1]) if len(partes) > 1 and partes[1].isdigit() else 0
    cab: dict[str, str] = {}
    for linha in linhas[1:]:
        nome, _, valor = linha.partition(":")
        if nome.strip():
            cab[nome.strip().lower()] = valor.strip()
    return status, cab


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("conferencia", choices=("pagina", "cabecalhos", "versao", "versoes"))
    ap.add_argument("--rotulo", default="")
    ap.add_argument("--onde", default="")
    ap.add_argument("--host", default="")
    ap.add_argument("--status", type=int, default=None)
    ap.add_argument("--versao", default="")
    ap.add_argument("--sem-transformar", action="store_true")
    ap.add_argument("--gzip", action="store_true")
    ap.add_argument("--csp", choices=("site", "aplicar", "so_relatar"))
    ap.add_argument("--sem-cookie", action="store_true")
    a = ap.parse_args(argv)
    r = _regua()
    entrada = sys.stdin.buffer.read()

    if a.conferencia == "versoes":                   # a raiz no stdin; uma linha "<caminho> <versao>" por arquivo
        for caminho, versao in r.versoes_pedidas(entrada.decode("utf-8", "replace")).items():
            print(caminho, versao)
        return 0
    if a.conferencia == "pagina":
        status = a.status if a.status is not None else 0
        desfecho = r.conferir_html(a.onde, status, entrada.decode("utf-8", "replace"), host=a.host)
        bom = "nenhum script de fora nem da Cloudflare no HTML"
    elif a.conferencia == "cabecalhos":
        status, cab = _cabecalhos(entrada.decode("latin-1"))
        desfecho = r.conferir_cabecalhos(a.onde, status, cab, sem_transformar=a.sem_transformar, gzip_da_origem=a.gzip,
                                         csp=a.csp, sem_cookie=a.sem_cookie)
        partes = (["no-transform"] if a.sem_transformar else []) + (["gzip da origem"] if a.gzip else [])
        if a.csp:
            partes.append({"site": "CSP do site", "aplicar": "CSP do painel",
                           "so_relatar": "CSP do painel em Report-Only"}[a.csp])
        if a.sem_cookie:
            partes.append("nenhum Set-Cookie, como a pagina promete")
        bom = ", ".join(partes)
    else:
        desfecho = r.conferir_versao(a.onde, a.versao or None, a.status, entrada)
        bom = f"?v={a.versao} e o conteudo que a borda entrega"

    rotulo = f"{a.rotulo:<28}"
    if desfecho.estado == r.OK:
        print(f"ok     {rotulo}      ({bom})")
        return 0
    if desfecho.estado == r.SEM_CONFERIR:
        print(_ascii(f"FALHOU {rotulo}      nao conferido: {desfecho.motivo} (a borda nao alcancou o central?)"))
        return 1
    codigos = list(dict.fromkeys(achado.codigo for achado in desfecho.achados))
    scripts = [achado.detalhe for achado in desfecho.achados if achado.codigo == r.SCRIPT_INJETADO]
    for achado in desfecho.achados:
        if achado.codigo == r.SCRIPT_INJETADO:
            continue
        if achado.codigo == r.PAGINA_FORA and achado.detalhe.startswith("status "):
            print(f"FALHOU {a.rotulo:<28} {achado.detalhe[7:]}  esperado 200: sem ver a pagina nao ha o que conferir "
                  "(desafio da Cloudflare?)")
        else:
            print(_ascii(f"FALHOU {rotulo}      {achado.detalhe}"))
    if scripts:
        print(_ascii(f"FALHOU {rotulo}      script que a pagina nao tem no HTML: {' '.join(scripts)}"))
    for codigo in codigos:
        if codigo in r.GESTOS and codigo != r.PAGINA_FORA:
            print(_ascii(f"       -> {r.GESTOS[codigo]}"))
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
