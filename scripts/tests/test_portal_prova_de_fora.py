"""`scripts/portal-prova-de-fora.sh` com o site e o contato ligados (29.77, ADR-075), contra um `curl` FALSO.

O `curl` falso imita o central como ele tem de responder de fora, e registra cada pedido. Assim a prova é testada sem
rede: o script inteiro roda, a saída é conferida, e o registro mostra que ela nunca manda um contato de verdade (todo
POST na rota do contato leva a isca, ou é o de tipo errado ou o de corpo grande) nem chama a rota de entrada com
credencial. Prova `simulated`; a prova de fora de verdade é a do procedimento em `docs/operacao.md`.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
SCRIPT = RAIZ / "scripts" / "portal-prova-de-fora.sh"

#: O central visto de fora, com `portal.site_ligado` e `portal.contato_ligado`. `QUEBRA` liga um defeito por teste.
CURL_FALSO = r'''#!/usr/bin/env bash
metodo=GET; url=""; formato=""; dados=""; tipo=""; host=""; cabecalhos=0; corpo_fora=0; auth=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    -X) metodo="$2"; shift 2 ;;
    -w) formato="$2"; shift 2 ;;
    -d) dados="$2"; [[ "$metodo" == GET ]] && metodo=POST; shift 2 ;;
    -H) case "$2" in
          [Cc]ontent-[Tt]ype:*) tipo="${2#*: }" ;;
          [Hh]ost:*) host="${2#*: }" ;;
          [Aa]uthorization:*) auth=1 ;;
        esac; shift 2 ;;
    -I) metodo=HEAD; shift ;;
    -D) cabecalhos=1; shift 2 ;;
    -o) corpo_fora=1; shift 2 ;;
    -m) shift 2 ;;
    -s|--http1.1) shift ;;
    http*) url="$1"; shift ;;
    *) shift ;;
  esac
done
caminho="/${url#*://*/}"; [[ "$url" == *://*/* ]] || caminho="/"
esquema="${url%%://*}"
echo "$metodo $caminho tipo=$tipo bytes=${#dados} auth=$auth isca=$([[ "$dados" == *'"site":"isca"'* ]] && echo 1 || echo 0)" >> "$CURL_LOG"
codigo=404; corpo=""; destino=""; extra=""
if [[ "$esquema" == http ]]; then codigo=301
elif [[ -n "$host" ]]; then codigo=403
else
  case "$caminho" in
    /api/instances) codigo=401; [[ "$QUEBRA" == instancias ]] && codigo=200 ;;
    /api/health|/api/workers|/api/docs|/api/openapi.json) codigo=401 ;;
    /api/session) codigo=200; corpo='{"token_required": true, "operator": null}' ;;
    /api/ws) codigo=403 ;;
    /api/login) codigo=429 ;;
    /central/|/assets/site.css) codigo=200 ;;
    /central) codigo=307; destino="${url%/central}/central/" ;;
    /) codigo=200; extra="content-security-policy: default-src 'self'; script-src 'self'; frame-ancestors 'none'" ;;
    /robots.txt) codigo=200; corpo=$'User-agent: *\nAllow: /\nDisallow: /central/\nDisallow: /api/'
                 [[ "$QUEBRA" == robots ]] && corpo=$'User-agent: *\nAllow: /' ;;
    /api/portal/contato)
      if [[ "$metodo" != POST ]]; then codigo=401
      elif [[ "$tipo" != application/json ]]; then codigo=415
      elif (( ${#dados} > 8192 )); then codigo=413
      elif [[ "$dados" == *'"site":"isca"'* ]]; then codigo=202
      else codigo=500; fi ;;
  esac
fi
if [[ "$cabecalhos" == 1 ]]; then printf 'HTTP/2 %s\r\n%s\r\n\r\n' "$codigo" "$extra"; fi
case "$formato" in
  '%{http_code}') printf '%s' "$codigo" ;;
  '%{redirect_url}') printf '%s' "$destino" ;;
  *) [[ "$corpo_fora" == 0 ]] && printf '%s' "$corpo" ;;
esac
exit 0
'''


def _bash() -> str:
    """O bash do Git no Windows, ou o do sistema fora dele. Nunca o `bash.exe` do System32, que é o do WSL (o WSL é
    do dono; os testes não o tocam)."""
    git = Path(r"C:\Program Files\Git\bin\bash.exe")
    if os.name == "nt":
        if git.is_file():
            return str(git)
        pytest.skip("sem o bash do Git nesta máquina")
    achado = shutil.which("bash")
    if achado is None:
        pytest.skip("sem bash")
    return achado


def _rodar(tmp_path: Path, quebra: str = "") -> tuple[subprocess.CompletedProcess[str], list[str]]:
    pasta = tmp_path / "bin"
    pasta.mkdir()
    curl = pasta / "curl"
    curl.write_bytes(CURL_FALSO.encode("utf-8"))
    curl.chmod(curl.stat().st_mode | stat.S_IEXEC)
    registro = tmp_path / "pedidos.log"
    registro.write_text("", encoding="utf-8")
    bash = _bash()
    caminho = (subprocess.run([bash, "-c", f"cygpath -u '{pasta}' 2>/dev/null || echo '{pasta}'"],
                              capture_output=True, text=True).stdout.strip())
    reg = (subprocess.run([bash, "-c", f"cygpath -u '{registro}' 2>/dev/null || echo '{registro}'"],
                          capture_output=True, text=True).stdout.strip())
    script = (subprocess.run([bash, "-c", f"cygpath -u '{SCRIPT}' 2>/dev/null || echo '{SCRIPT}'"],
                             capture_output=True, text=True).stdout.strip())
    comando = (f'export PATH="{caminho}:$PATH" CURL_LOG="{reg}" QUEBRA="{quebra}" SITE=ligado CONTATO=ligado; '
               f'bash "{script}" depois')
    r = subprocess.run([bash, "-c", comando], capture_output=True, text=True, encoding="utf-8", timeout=120)
    return r, registro.read_text(encoding="utf-8").splitlines()


def test_com_o_site_e_o_contato_ligados_tudo_passa_e_nada_de_verdade_e_enviado(tmp_path: Path) -> None:
    r, pedidos = _rodar(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULTADO: tudo como esperado" in r.stdout
    for esperado in ("/rascunho.pdf", "/site/index.html", "/robots.txt (corpo)", "/ (cabecalhos)",
                     "tipo errado recusado", "corpo acima do teto", "POST com a isca", "/api/instances"):
        assert esperado in r.stdout, esperado
    contato = [p for p in pedidos if p.startswith("POST /api/portal/contato")]
    assert len(contato) == 3
    assert all("isca=1" in p or "tipo=text/plain" in p or int(p.split("bytes=")[1].split()[0]) > 8192
               for p in contato), contato
    assert not [p for p in pedidos if "auth=1" in p], "a prova nunca manda credencial"
    login = [p for p in pedidos if p.split()[1] == "/api/login"]
    assert login and all(p.startswith("GET ") for p in login), "a rota de entrada só leva GET (405 na origem)"


def test_api_aberta_de_fora_para_tudo(tmp_path: Path) -> None:
    r, _ = _rodar(tmp_path, quebra="instancias")
    assert r.returncode == 2 and "PARE" in r.stdout


def test_robots_sem_barrar_a_api_reprova(tmp_path: Path) -> None:
    r, _ = _rodar(tmp_path, quebra="robots")
    assert r.returncode == 1
    assert "FALHOU /robots.txt (corpo)" in r.stdout
