"""`scripts/portal-prova-de-fora.sh` com o site e o contato ligados (29.77, ADR-075), contra um `curl` FALSO.

O `curl` falso imita o central como ele tem de responder de fora, e registra cada pedido. Assim a prova é testada sem
rede: o script inteiro roda, a saída é conferida, e o registro mostra que ela nunca manda um contato de verdade (todo
POST na rota do contato leva a isca, ou é o de tipo errado ou o de corpo grande) nem chama a rota de entrada com
credencial. Prova `simulated`; a prova de fora de verdade é a do procedimento em `docs/operacao.md`.
"""
from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
SCRIPT = RAIZ / "scripts" / "portal-prova-de-fora.sh"

#: O central visto de fora, com `portal.site_ligado` e `portal.contato_ligado`. `QUEBRA` liga um defeito por teste.
CURL_FALSO = r'''#!/usr/bin/env bash
metodo=GET; url=""; formato=""; dados=""; tipo=""; host=""; cabecalhos=0; corpo_fora=0; auth=0; navegador=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    -X) metodo="$2"; shift 2 ;;
    -w) formato="$2"; shift 2 ;;
    -d) dados="$2"; [[ "$metodo" == GET ]] && metodo=POST; shift 2 ;;
    -H) case "$2" in
          [Cc]ontent-[Tt]ype:*) tipo="${2#*: }" ;;
          [Hh]ost:*) host="${2#*: }" ;;
          [Aa]uthorization:*) auth=1 ;;
          [Uu]ser-[Aa]gent:*Mozilla*) navegador=1 ;;
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
echo "$metodo $caminho tipo=$tipo bytes=${#dados} auth=$auth isca=$([[ "$dados" == *'"site":"isca"'* ]] && echo 1 || echo 0) nav=$navegador" >> "$CURL_LOG"
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
    /assets/site.css) codigo=200 ;;
    /central/) codigo=200
       extra="cache-control: no-cache, must-revalidate, no-transform"$'\r\n'"content-security-policy: default-src 'self'; script-src 'self'; frame-ancestors 'none'"
       [[ "$QUEBRA" == painel_so_relata ]] && extra="cache-control: no-cache, must-revalidate, no-transform"$'\r\n'"content-security-policy-report-only: default-src 'self'; script-src 'self'; frame-ancestors 'none'" ;;
    /api/portal/contatos/busca|/api/portal/contatos/excluir) codigo=401 ;;
    /central) codigo=307; destino="${url%/central}/central/" ;;
    /) codigo=200; extra="content-security-policy: default-src 'self'; script-src 'self'; frame-ancestors 'none'"
       case "$QUEBRA" in
         transformado) extra="$extra"$'\r\n''cache-control: no-store'$'\r\n''content-encoding: gzip' ;;
         recomprimido) extra="$extra"$'\r\n''cache-control: no-store, no-transform'$'\r\n''Content-Encoding: br' ;;
         *) extra="$extra"$'\r\n''cache-control: no-store, no-transform'$'\r\n''Content-Encoding: gzip' ;;
       esac
       [[ "$QUEBRA" == cookie ]] && extra="$extra"$'\r\n''set-cookie: __cf_bm=x; Path=/; Secure; HttpOnly' ;;
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
# A raiz pedida COMO navegador (29.85): a borda da Cloudflare injeta o beacon só nesse caso; o curl puro não vê.
if [[ "$caminho" == / && "$codigo" == 200 && "$navegador" == 1 ]]; then
  # O script do próprio site, relativo e absoluto (com o nome em maiúscula: é o mesmo endereço).
  corpo='<!doctype html><html><head><script src="/assets/site.js" defer></script>'
  corpo="$corpo<script src=\"HTTPS://PROVA.INVALID/assets/site.js\" defer></script>"
  # Y1: dois-pontos DEPOIS do primeiro / (versão com hora) não é esquema: segue relativo.
  corpo="$corpo<script src=\"/assets/site.js?v=2026-10-05T01:00\" defer></script></head><body><main></main>"
  case "$QUEBRA" in
    beacon) corpo="$corpo<script defer src='https://static.cloudflareinsights.com/beacon.min.js' data-cf-beacon='{\"token\": \"x\"}'></script>" ;;
    script_de_fora) corpo="$corpo<script src=\"https://cdn.exemplo.invalid/x.js\"></script>" ;;
    src_espacado) corpo="$corpo<script"$'\n'"  defer"$'\n'"  src = \"https://cdn.exemplo.invalid/y.js\"></script>" ;;
    maiuscula) corpo="$corpo<SCRIPT SRC='HTTPS://cdn.exemplo.invalid/z.js'></SCRIPT>" ;;
    src_na_query) corpo="$corpo<script defer src=\"https://cdn.exemplo.invalid/a.js?src=b\"></script>" ;;
    cdn_cgi) corpo="$corpo<script src=\"/cdn-cgi/scripts/7d0fa10a/cloudflare-static/rocket-loader.min.js\" defer></script>" ;;
    desafio_embutido) corpo="$corpo<script>(function(){var a=document.createElement('script');a.src='/cdn-cgi/challenge-platform/scripts/jsd/main.js';})();</script>" ;;
    desafio) codigo=403; corpo='<!doctype html><html><head><title>Just a moment...</title></head><body></body>' ;;
  esac
  corpo="$corpo</body></html>"
fi
# O painel pedido como navegador (29.91): o bundle com hash, relativo; a borda injetou o beacon nele em 05/10.
if [[ "$caminho" == /central/ && "$codigo" == 200 && "$navegador" == 1 ]]; then
  corpo='<!doctype html><html><head><script type="module" crossorigin src="/central/assets/index-abc123.js"></script></head><body><div id="root"></div>'
  [[ "$QUEBRA" == beacon_no_painel ]] && corpo="$corpo<script defer src='https://static.cloudflareinsights.com/beacon.min.js'></script>"
  corpo="$corpo</body></html>"
fi
if [[ "$cabecalhos" == 1 ]]; then printf 'HTTP/2 %s\r\n%s\r\n\r\n' "$codigo" "$extra"; fi
case "$formato" in
  '%{http_code}') printf '%s' "$codigo" ;;
  '%{redirect_url}') printf '%s' "$destino" ;;
  '\n%{http_code}') printf '%s\n%s' "$corpo" "$codigo" ;;
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
    # Blindagem contra a rede de verdade: o nome público é um `.invalid` (RFC 2606, nunca resolve) e, se o PATH não
    # pegar a pasta do `curl` falso, o comando para antes de rodar a prova (saída 97) em vez de usar o `curl` real.
    comando = (f'export PATH="{caminho}:$PATH" CURL_LOG="{reg}" QUEBRA="{quebra}" SITE=ligado CONTATO=ligado '
               f'HOSTNAME_PUBLICO=prova.invalid; '
               f'[ "$(command -v curl)" = "{caminho}/curl" ] || {{ echo "curl real no PATH"; exit 97; }}; '
               f'bash "{script}" depois')
    r = subprocess.run([bash, "-c", comando], capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert r.returncode != 97, r.stdout + r.stderr
    pedidos = registro.read_text(encoding="utf-8").splitlines()
    assert pedidos, "nenhum pedido passou pelo curl falso"
    return r, pedidos


def test_com_o_site_e_o_contato_ligados_tudo_passa_e_nada_de_verdade_e_enviado(tmp_path: Path) -> None:
    r, pedidos = _rodar(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULTADO: tudo como esperado" in r.stdout
    for esperado in ("/rascunho.pdf", "/site/index.html", "/robots.txt (corpo)", "/ (cabecalhos)", "/ (sem cookie)",
                     "tipo errado recusado", "corpo acima do teto", "POST com a isca", "POST isca (sem cookie)",
                     "/api/portal/contatos/busca", "/api/portal/contatos/excluir", "/api/instances"):
        assert esperado in r.stdout, esperado
    contato = [p for p in pedidos if p.split()[:2] == ["POST", "/api/portal/contato"]]
    assert len(contato) == 4                                   # a isca duas vezes (código e cabeçalhos), 415 e 413
    assert all("isca=1" in p or "tipo=text/plain" in p or int(p.split("bytes=")[1].split()[0]) > 8192
               for p in contato), contato
    assert not [p for p in pedidos if "auth=1" in p], "a prova nunca manda credencial"
    exclusao = [p for p in pedidos if p.split()[1].startswith("/api/portal/contatos/")]
    assert len(exclusao) == 2, exclusao
    # Os corpos da exclusão são inválidos de propósito: ids vazio e telefone curto, nunca um pedido que apagaria algo.
    assert all(int(p.split("bytes=")[1].split()[0]) < 40 for p in exclusao), exclusao
    login = [p for p in pedidos if p.split()[1] == "/api/login"]
    assert login and all(p.startswith("GET ") for p in login), "a rota de entrada só leva GET (405 na origem)"


def test_api_aberta_de_fora_para_tudo(tmp_path: Path) -> None:
    r, _ = _rodar(tmp_path, quebra="instancias")
    assert r.returncode == 2 and "PARE" in r.stdout


def test_cookie_na_raiz_reprova(tmp_path: Path) -> None:
    """A página promete "não usa cookies"; um cookie da borda na raiz (o `__cf_bm`, por exemplo) reprova a prova."""
    r, _ = _rodar(tmp_path, quebra="cookie")
    assert r.returncode == 1
    assert "FALHOU / (sem cookie)" in r.stdout


def test_robots_sem_barrar_a_api_reprova(tmp_path: Path) -> None:
    r, _ = _rodar(tmp_path, quebra="robots")
    assert r.returncode == 1
    assert "FALHOU /robots.txt (corpo)" in r.stdout


# ------------------------------------------------------------------ 29.85: script de outra origem no HTML
def test_a_raiz_e_pedida_como_navegador_e_o_script_proprio_passa(tmp_path: Path) -> None:
    """A borda da Cloudflare só injeta o beacon quando o pedido parece de navegador: a prova baixa a raiz assim, e o
    `<script src>` do próprio site, relativo ou absoluto com o nome em maiúscula, não reprova."""
    r, pedidos = _rodar(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ok     / (como navegador)" in r.stdout
    assert [p for p in pedidos if p.startswith("GET / ") and p.endswith("nav=1")], pedidos


@pytest.mark.parametrize(("quebra", "esperado"), [
    ("beacon", "https://static.cloudflareinsights.com/beacon.min.js"),   # o achado de 05/10, aspas simples
    ("script_de_fora", "https://cdn.exemplo.invalid/x.js"),
    ("src_espacado", "https://cdn.exemplo.invalid/y.js"),                # X1: tag em linhas, `src = "…"`
    ("maiuscula", "HTTPS://cdn.exemplo.invalid/z.js"),                    # X2: SCRIPT SRC e HTTPS:// em maiúscula
    ("src_na_query", "https://cdn.exemplo.invalid/a.js?src=b"),          # Y2: `?src=` dentro da URL não é o valor
    ("cdn_cgi", "/cdn-cgi/scripts/7d0fa10a/cloudflare-static/rocket-loader.min.js"),   # X3: a própria origem
    ("desafio_embutido", "(embutido)"),                                   # X3: /cdn-cgi/ em script sem src
])
def test_script_que_a_pagina_nao_tem_reprova_e_diz_onde_desligar(tmp_path: Path, quebra: str, esperado: str) -> None:
    r, _ = _rodar(tmp_path, quebra=quebra)
    assert r.returncode == 1, r.stdout
    assert "FALHOU / (como navegador)" in r.stdout and esperado in r.stdout
    assert "Web Analytics" in r.stdout and "Rocket Loader" in r.stdout and "Nao afrouxe a CSP" in r.stdout


def test_raiz_que_nao_vem_200_como_navegador_reprova(tmp_path: Path) -> None:
    """X4: um desafio da Cloudflare (403 ou 503) não tem a página para conferir; não pode dar `ok`."""
    r, _ = _rodar(tmp_path, quebra="desafio")
    assert r.returncode == 1
    assert re.search(r"FALHOU / \(como navegador\) +403  esperado 200", r.stdout), r.stdout


def test_todo_curl_do_script_ignora_o_curlrc() -> None:
    """X5: `-q` é o primeiro argumento de todo `curl`, para o `~/.curlrc` de quem roda não entrar no pedido (um proxy,
    um cabeçalho, um `--insecure`). Vale também para os `curl` que outro PR acrescentar."""
    chamadas = re.findall(r"\bcurl[ \t]+(-\S*)", SCRIPT.read_text(encoding="utf-8"))
    assert chamadas and all(c == "-q" for c in chamadas), chamadas


# ------------------------------------------------------------------ 29.91: a borda não reescreve o HTML
def test_o_painel_e_pedido_como_navegador_e_o_html_sai_intocado(tmp_path: Path) -> None:
    """O painel também é baixado como navegador, e a raiz e o painel provam `no-transform`: a raiz no gzip que a
    origem fez, o painel com a CSP dele."""
    r, pedidos = _rodar(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    for linha in ("ok     /central/ (como navegador)", "ok     / (sem transformar)", "ok     /central/ (sem transformar)"):
        assert linha in r.stdout, r.stdout
    assert [p for p in pedidos if p.startswith("GET /central/ ") and p.endswith("nav=1")], pedidos


@pytest.mark.parametrize(("quebra", "linha", "motivo"), [
    ("beacon_no_painel", "FALHOU /central/ (como navegador)", "https://static.cloudflareinsights.com/beacon.min.js"),
    ("transformado", "FALHOU / (sem transformar)", "sem no-transform"),
    ("recomprimido", "FALHOU / (sem transformar)", "esperado o gzip da origem; veio content-encoding: br"),
    ("painel_so_relata", "FALHOU /central/ (sem transformar)", "server.csp_do_painel em aplicar?"),
])
def test_borda_que_reescreve_ou_painel_sem_csp_reprova(tmp_path: Path, quebra: str, linha: str, motivo: str) -> None:
    r, _ = _rodar(tmp_path, quebra=quebra)
    assert r.returncode == 1, r.stdout
    assert linha in r.stdout and motivo in r.stdout, r.stdout
