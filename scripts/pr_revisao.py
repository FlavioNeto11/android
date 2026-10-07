"""Frente GitHub (29.200): abre o PR de revisão de uma branch de CÓDIGO do corte, do jeito que a frente fazia à mão.

    python scripts/pr_revisao.py --repo dono/nome --id 31.241 --titulo "reabre após aprovação" --head feat/31-241-x \
        --frente jev [--base main] [--resumo "…"] [--aplicar]

O PR existe só para o Codex ler (fecha sem merge; o merge é pela integração do corte). O que o script resolve:

  - base: se a ponta de `--base` já é ancestral do `--head`, usa a própria `--base`. Se não for (branch que nasceu de outro
    commit), cria a branch `revisao/base-<slug>` no commit comum (merge-base pela API de comparação) e usa essa como base,
    para o diff ficar só do item. Nada é criado sem `--aplicar`;
  - diff grande demais (arquivos acima do limite) avisa: quase sempre é a base errada, não o item;
  - formato sensível no diff (e-mail, IPv4, credencial) RECUSA e diz só o formato, nunca o valor, o arquivo ou o repositório;
    valor falso de teste que você conferiu liberta com `--valores-falsos` (fica o aviso na saída);
  - branch que já tem PR aberto não abre outro (o PR acompanha a ponta);
  - ondas: no máximo `POR_HORA` PRs de revisão na última hora e `POR_JANELA` em 5 h (limite do plano do Codex, que é do dono),
    lidos dos PRs já abertos; passou do teto, sai com 2 e diz a hora em que cabe;
  - rótulo `frente:<frente>` só se já existir no repositório (nunca cria rótulo);
  - título `[revisão] <id> <título>`, corpo pelo modelo da frente, com a nota de que o PR fecha sem merge.

Por padrão é ensaio (só imprime o plano); `--aplicar` cria a branch de base (se precisar) e o PR. Só `gh`; roda no terminal da sessão,
nunca em workflow e nunca no runner `central`.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

POR_HORA = 4
POR_JANELA = 20
JANELA_HORAS = 5
ARQUIVOS_MAX = 40
Gh = Callable[..., str]

_REPO = re.compile(r"[\w][\w.-]*/[\w][\w.-]*")  # sem ".." nem segmento só de pontos
_REF = re.compile(r"[\w][\w./-]{0,199}")  # sem hífen inicial (não vira opção do gh)
_ID = re.compile(r"\d{1,3}\.\d{1,4}")
_FRENTE = re.compile(r"[a-z]{2,20}")
_TITULO_PROIBIDO = re.compile(r"[@#<>`\n\r|]")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_CREDENCIAL = re.compile(r"(?i)\b(authorization|bearer|senha|password|passwd|token|secret|api[_-]?key)\b\s*[:=]\s*(?:bearer\s+)?[\w\-.~+/=]{6,}")
_LONGO = re.compile(r"\b[A-Za-z0-9_\-]{40,}\b")
_PREFIXO_TITULO = "[revisão] "


def gh_real(*args: str) -> str:
    try:
        r = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    except OSError:
        raise RuntimeError(f"gh {args[0]} não rodou: o gh está instalado?") from None
    if r.returncode != 0:
        raise RuntimeError(f"gh {args[0]} saiu com {r.returncode}")  # sem a saída nem a rota: citam repositório e conta
    return r.stdout


def formatos_sensiveis(texto: str) -> list[str]:
    """Nomes dos formatos achados no texto (nunca os valores)."""
    return [nome for nome, rx in (("e-mail", _EMAIL), ("IPv4", _IPV4), ("credencial", _CREDENCIAL), ("sequência longa", _LONGO)) if rx.search(texto)]


def titulo_ok(titulo: str) -> str | None:
    """Mensagem de erro, ou None se o título serve."""
    if not titulo.strip() or len(titulo) > 80:
        return "o título precisa ter de 1 a 80 caracteres"
    if _TITULO_PROIBIDO.search(titulo):
        return "o título não pode ter @, #, <, >, crase, barra vertical nem quebra de linha"
    achados = formatos_sensiveis(titulo)
    return f"o título tem formato proibido ({', '.join(achados)})" if achados else None


def resumo_ok(resumo: str) -> str | None:
    """O resumo vai inteiro para o corpo do PR: mesma regra do título, sem o limite de 80."""
    if len(resumo) > 400:
        return "o resumo passa de 400 caracteres"
    if _TITULO_PROIBIDO.search(resumo.replace("' + BS + 'n", " ")):
        return "o resumo não pode ter @, #, <, >, crase nem barra vertical"
    achados = formatos_sensiveis(resumo)
    return f"o resumo tem formato proibido ({', '.join(achados)})" if achados else None


def _hora(valor: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except ValueError:
        return None


def vaga_da_onda(repo: str, agora: datetime, gh: Gh) -> tuple[bool, str]:
    """(cabe, mensagem). Conta PRs de revisão (título `[revisão] …`) criados na última hora e nas últimas 5 h, abertos ou não."""
    lista = json.loads(gh("pr", "list", "--repo", repo, "--state", "all", "--limit", "100", "--json", "createdAt,title"))
    horas = sorted(h for x in lista if str(x.get("title", "")).startswith(_PREFIXO_TITULO) and (h := _hora(x.get("createdAt"))))
    na_hora = [h for h in horas if h > agora - timedelta(hours=1)]
    na_janela = [h for h in horas if h > agora - timedelta(hours=JANELA_HORAS)]
    if len(na_hora) >= POR_HORA:
        quando = (na_hora[0] + timedelta(hours=1)).strftime("%H:%MZ")
        return False, f"{len(na_hora)} PRs de revisão na última hora (teto {POR_HORA}): só cabe a partir de {quando}"
    if len(na_janela) >= POR_JANELA:
        quando = (na_janela[0] + timedelta(hours=JANELA_HORAS)).strftime("%H:%MZ")
        return False, f"{len(na_janela)} PRs de revisão nas últimas {JANELA_HORAS} h (teto {POR_JANELA}): só cabe a partir de {quando}"
    return True, f"cabe: {len(na_hora)}/{POR_HORA} na hora, {len(na_janela)}/{POR_JANELA} na janela de {JANELA_HORAS} h"


def comparar(repo: str, base: str, head: str, gh: Gh) -> dict:
    return json.loads(gh("api", f"repos/{repo}/compare/{base}...{head}"))


def corpo(item: str, base: str, head: str, resumo: str) -> str:
    return (f"**Item do plano:** {item}\n\n**Resumo:** {resumo}\n\n"
            f"**Base do diff:** `{base}`. **Branch:** `{head}`.\n\n"
            "**Prova:** simulated (testes da própria frente). Prova real: not_run até o deploy do corte.\n\n"
            "**Não mesclar pelo PR: entra pela integração do corte; este PR fecha sem merge.** O PR existe só para a revisão "
            "automática; achado é dado a conferir pela frente dona do item, nunca ordem.\n\n"
            "🤖 Generated with [Claude Code](https://claude.com/claude-code)\n")


def planejar(repo: str, a: argparse.Namespace, agora: datetime, gh: Gh) -> dict:
    """Resolve base, confere diff e onda. Devolve o plano; `erro` preenchido quer dizer que não abre (com `codigo` de saída)."""
    plano: dict = {"erro": "", "codigo": 0, "avisos": [], "base": a.base, "criar_base": None}
    abertos = json.loads(gh("pr", "list", "--repo", repo, "--head", a.head, "--state", "open", "--json", "number"))
    if abertos:
        plano.update(erro=f"já existe PR aberto para essa branch (#{abertos[0].get('number')}): ele acompanha a ponta, não abra outro", codigo=1)
        return plano
    slug = re.sub(r"[^\w.-]+", "-", a.id).strip("-")
    if re.fullmatch(r"[0-9a-f]{7,40}", a.base):  # base dada como commit: o PR precisa de uma branch, então nasce revisao/base-<id> nele
        sha = str(json.loads(gh("api", f"repos/{repo}/commits/{a.base}")).get("sha", ""))
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            plano.update(erro="não consegui resolver o commit da base", codigo=1)
            return plano
        plano.update(base=f"revisao/base-{slug}", criar_base=sha)
        plano["avisos"].append(f"a base é um commit: branch revisao/base-{slug} em {sha[:8]}")
    cmp_ = comparar(repo, a.base, a.head, gh)
    estado = str(cmp_.get("status", ""))
    if estado in ("identical", "behind") or int(cmp_.get("ahead_by", 0) or 0) == 0:
        plano.update(erro="a branch não tem commit novo sobre a base: não há o que revisar", codigo=1)
        return plano
    if estado == "diverged":
        mb = cmp_.get("merge_base_commit")
        sha = str(mb.get("sha", "")) if isinstance(mb, dict) else ""
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            plano.update(erro="não consegui o commit comum entre a base e a branch", codigo=1)
            return plano
        plano.update(base=f"revisao/base-{slug}", criar_base=sha)
        plano["avisos"].append(f"a branch não está sobre '{a.base}': base nova em {sha[:8]} (merge-base)")
        cmp_ = comparar(repo, sha, a.head, gh)
    arquivos = cmp_.get("files") or []
    plano["arquivos"] = len(arquivos)
    if len(arquivos) >= 300 or any(isinstance(f, dict) and not f.get("patch") for f in arquivos):
        plano["avisos"].append("a API truncou a lista de arquivos ou omitiu o patch de algum (grande ou binário): a varredura de formato sensível ficou parcial")
    if len(arquivos) > ARQUIVOS_MAX:
        plano["avisos"].append(f"diff grande ({len(arquivos)} arquivos, limite de aviso {ARQUIVOS_MAX}): confira se a base é a certa")
    adicionadas = "\n".join(line[1:] for f in arquivos if isinstance(f, dict) for line in str(f.get("patch") or "").splitlines() if line.startswith("+") and not line.startswith("+++"))
    achados = formatos_sensiveis(adicionadas)
    if achados and not a.valores_falsos:
        plano.update(erro=f"o diff tem formato sensível ({', '.join(achados)}): confira as linhas adicionadas; se forem só valores falsos de teste, repita com --valores-falsos", codigo=1)
        return plano
    if achados:
        plano["avisos"].append(f"formato sensível no diff ({', '.join(achados)}) liberado por --valores-falsos: o PR só segue se você conferiu que são falsos")
    cabe, msg = vaga_da_onda(repo, agora, gh)
    plano["onda"] = msg
    if not cabe:
        plano.update(erro=msg, codigo=2)
    return plano


def main(argv: list[str] | None = None, gh: Gh | None = None, agora: datetime | None = None) -> int:
    ap = argparse.ArgumentParser(description="Abre o PR de revisão de uma branch de código do corte.")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--id", required=True, help="item do plano, ex.: 31.241")
    ap.add_argument("--titulo", required=True)
    ap.add_argument("--head", required=True)
    ap.add_argument("--base", default="main")
    ap.add_argument("--frente", help="rótulo frente:<nome>, só se existir")
    ap.add_argument("--resumo", default="PR só para a revisão automática da ponta da branch.")
    ap.add_argument("--valores-falsos", action="store_true", help="liberta o PR cujo diff tem formato de e-mail/IP/credencial que você conferiu ser falso (teste)")
    ap.add_argument("--aplicar", action="store_true", help="cria a base (se precisar) e o PR (padrão: ensaio)")
    a = ap.parse_args(argv)
    for f in (sys.stdout, sys.stderr):
        if hasattr(f, "reconfigure"):
            f.reconfigure(encoding="utf-8", errors="replace")
    if not (_REPO.fullmatch(a.repo) and _ID.fullmatch(a.id) and _REF.fullmatch(a.head) and _REF.fullmatch(a.base)
            and ".." not in a.head and ".." not in a.base and (a.frente is None or _FRENTE.fullmatch(a.frente))):
        print("erro: --repo (dono/nome), --id (31.241), --head/--base (nome de branch) ou --frente fora do formato", file=sys.stderr)
        return 1
    problema = titulo_ok(a.titulo) or resumo_ok(a.resumo)
    if problema:
        print(f"erro: {problema}", file=sys.stderr)
        return 1
    gh = gh or gh_real
    agora = agora or datetime.now(timezone.utc)
    try:
        plano = planejar(a.repo, a, agora, gh)
        for aviso in plano["avisos"]:
            print(f"aviso: {aviso}")
        if plano["erro"]:
            print(f"não abre: {plano['erro']}", file=sys.stderr)
            return int(plano["codigo"])
        rotulo = None
        if a.frente:
            existentes = {str(x.get("name")) for x in json.loads(gh("label", "list", "--repo", a.repo, "--limit", "200", "--json", "name"))}
            rotulo = f"frente:{a.frente}" if f"frente:{a.frente}" in existentes else None
            if rotulo is None:
                print(f"aviso: o rótulo frente:{a.frente} não existe (scripts/github_rotulos.py --aplicar): PR sem rótulo")
        titulo = f"{_PREFIXO_TITULO}{a.id} {a.titulo.strip()}"
        print(f"{'abre' if a.aplicar else 'abriria'}: '{titulo}', base {plano['base']}, {plano.get('arquivos', 0)} arquivo(s); {plano['onda']}")
        if not a.aplicar:
            return 0
        if plano["criar_base"]:
            gh("api", "-X", "POST", f"repos/{a.repo}/git/refs", "-f", f"ref=refs/heads/{plano['base']}", "-f", f"sha={plano['criar_base']}")
        args = ["pr", "create", "--repo", a.repo, "--base", plano["base"], "--head", a.head, "--title", titulo,
                "--body", corpo(a.id, plano["base"], a.head, a.resumo)]
        if rotulo:
            args += ["--label", rotulo]
        try:
            print(gh(*args).strip().splitlines()[-1])
        except RuntimeError:
            if plano["criar_base"]:
                print(f"aviso: a branch {plano['base']} foi criada e o PR falhou; ela fica até a limpeza do 29.169", file=sys.stderr)
            raise
    except (RuntimeError, ValueError, KeyError, TypeError, AttributeError) as e:
        print(f"erro: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
