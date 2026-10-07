"""Frente GitHub (29.177): gera uma issue de tarefa a partir do pacote de um item do plano-100.

`python scripts/issue_do_pacote.py 29.177 --repo dono/nome` (ensaio, o padrão) imprime a issue que criaria; `--aplicar` cria.
O pacote (`.claude/plano-100/pacotes/<id>.md`, fora do Git, só no checkout central) dá o título, o tamanho e o trabalho; o índice
(`indice.json`, na mesma pasta) dá os arquivos de posse. A issue leva o ID no título, a marca `<!-- pacote:<id> -->` no corpo (é
por ela que o script é IDEMPOTENTE: se já existe issue aberta ou fechada com a marca, não cria outra), a prova exigida (real,
simulated, not_run) e as regras fixas do repositório.

NUNCA atribui a issue ao agente de nuvem: não usa `--assignee`, e o rótulo `agente` só entra com `--agente`. A atribuição continua
manual e com o sim do dono (orçamento do Copilot). Nunca cria rótulo (`scripts/github_rotulos.py --aplicar` cria); rótulo que não
existe é pulado com aviso.

O texto do pacote pode citar coisa que não pode ir para uma issue. Por isso o script RECUSA (sem imprimir o trecho) se achar e-mail,
IPv4, serial `emulator-NNNN`/`worker-NN`, arroba de conta, sequência longa de letras e dígitos ou caminho de usuário do Windows.
Nome de persona não tem formato e não é detectável: quem decide o que entra é quem lê a prévia, e revisar antes de `--aplicar` é
obrigatório. `android-NN` (id do parque ou nome de imagem) não é serial e fica como está no pacote.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
PACOTES = RAIZ / ".claude" / "plano-100" / "pacotes"
TRABALHO_MAX = 2500
FRENTES = ("android", "jev", "aprendizado", "portal", "canais", "github", "desenho")
Gh = Callable[..., str]

_ID = re.compile(r"\d{1,3}\.\d{1,4}|T\.\d{1,3}")
_PROIBIDO = {
    "e-mail": re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),
    "IPv4": re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"),
    "serial de aparelho": re.compile(r"\b(?:emulator|worker)-\d+\b", re.IGNORECASE),
    "arroba de conta": re.compile(r"(?<![\w.])@[A-Za-z][\w.]{1,}"),
    "sequência longa": re.compile(r"\b[A-Za-z0-9_\-]{32,}\b"),
    "caminho de máquina": re.compile(r"\b[A-Za-z]:[\\/]|(?<![\w.])/(?:home|Users)/"),
    "credencial": re.compile(r"(?i)\bbearer\s+\S{6,}|\b(?:senha|password|passwd|secret|token|api[_-]?key)\s*[:=]\s*\S{3,}"),
    "chave conhecida": re.compile(r"\b(?:sk-|ghp_|gho_|AKIA)[A-Za-z0-9_\-]{8,}"),
}

REGRAS = (
    "Nunca coloque segredo, nome de conta ou de pessoa, arroba, e-mail, IP, serial de aparelho nem caminho de máquina em código, teste, log, commit ou PR.",
    "Sem conta real, sem serviço de fora e sem ensino; só aparelho e provedor falsos.",
    "Um PR por tarefa, em rascunho, só com os arquivos de posse; não edite migração já commitada, `.claude/`, `config/` nem `.env`.",
    "Falha ou incerteza nunca contam como sucesso: diga no PR o que foi provado e o que não foi.",
)
PROVA = (
    "`real`: data, máquina, commit e ids de execução ou comando (o agente de nuvem não alcança a máquina: normalmente não se aplica).",
    "`simulated`: `arquivo::teste`, com provedor ou aparelho falso; o teste tem de falhar com o código errado.",
    "`not_run`: o que falta e por quê. Um teste com mock não prova o ambiente real.",
)


def gh_real(*args: str, entrada: str | None = None) -> str:
    r = subprocess.run(["gh", *args], input=entrada, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])} saiu com {r.returncode}: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout


def marca(item: str) -> str:
    return f"<!-- pacote:{item} -->"


def proibidos(texto: str) -> list[str]:
    return [nome for nome, rx in _PROIBIDO.items() if rx.search(texto)]


def secao(texto: str, titulo: str) -> str:
    achou = re.search(rf"(?ms)^## {re.escape(titulo)}\s*\n(.*?)(?=^## |\Z)", texto)
    return achou.group(1).strip() if achou else ""


def ler_pacote(item: str, pacotes: Path) -> dict[str, object]:
    arq = pacotes / f"{item}.md"
    if not arq.is_file():
        raise ValueError(f"não há pacote para o item {item} em {pacotes}")
    texto = arq.read_text(encoding="utf-8")
    cab = re.match(r"# Item (\S+) — (.+)", texto)
    if not cab or cab.group(1) != item:
        raise ValueError("o cabeçalho do pacote não traz '# Item <id> — <título>'")
    tam = re.search(r"tamanho no plano: ([PMG])\b", texto)
    trabalho = secao(texto, "O trabalho")
    if not trabalho:
        raise ValueError("o pacote não tem a seção 'O trabalho'")
    arquivos: list[str] = []
    indice = pacotes / "indice.json"
    if indice.is_file():
        entrada = json.loads(indice.read_text(encoding="utf-8")).get(item, {})
        arquivos = [a for a in entrada.get("arquivos", []) if isinstance(a, str) and re.fullmatch(r"[\w./-]{1,200}", a)][:30]
    return {"titulo": cab.group(2).strip(), "tamanho": tam.group(1) if tam else "", "trabalho": trabalho, "arquivos": arquivos}


def montar(item: str, pacote: dict[str, object]) -> tuple[str, str]:
    trabalho = str(pacote["trabalho"])
    if len(trabalho) > TRABALHO_MAX:
        trabalho = trabalho[: TRABALHO_MAX - 1].rstrip() + "…\n\n(texto cortado; o pacote inteiro está no checkout central)"
    titulo = f"{item}: {pacote['titulo']}"
    corpo = "\n".join([
        marca(item), "",
        f"Gerada a partir do pacote do item {item} por `scripts/issue_do_pacote.py` (29.177). **Não está atribuída ao agente de nuvem**: "
        "a atribuição só sai de `scripts/agente_nuvem.py`, pedida pela orquestradora (orçamento do Copilot).", "",
        "## Item do plano", item + (f" · tamanho {pacote['tamanho']}" if pacote["tamanho"] else ""), "",
        "## O que fazer", trabalho, "",
        "## Arquivos de posse (do índice do plano)",
        *( [f"- `{a}`" for a in pacote["arquivos"]] or ["(o índice não lista arquivos: confira no código antes de mexer)"]), "",  # type: ignore[union-attr]
        "## Prova exigida", *[f"- {p}" for p in PROVA], "",
        "## Regras", *[f"- {r}" for r in REGRAS], "",
    ])
    achados = proibidos(titulo + "\n" + corpo)
    if achados:
        raise ValueError(f"o texto do pacote contém {', '.join(achados)}: nada foi criado; reescreva o pacote ou a issue à mão")
    return titulo, corpo


def ja_existe(repo: str, item: str, gh: Gh) -> tuple[int, str] | None:
    for it in json.loads(gh("issue", "list", "--repo", repo, "--state", "all", "--limit", "1000", "--json", "number,state,body")):
        if marca(item) in str(it.get("body") or ""):
            return int(it["number"]), str(it["state"])
    return None


def rotulos_da_issue(pacote: dict[str, object], frente: str | None, agente: bool, agente_nuvem: bool = False) -> list[str]:
    r = [f"tamanho:{pacote['tamanho']}"] if pacote["tamanho"] else []
    if frente:
        r.append(f"frente:{frente}")
    if agente:
        r.append("agente")
    if agente_nuvem:
        r.append("agente-nuvem")
    return r


def main(argv: list[str] | None = None, gh: Gh | None = None, pacotes: Path | None = None) -> int:
    ap = argparse.ArgumentParser(description="Gera a issue de tarefa a partir do pacote de um item do plano (não atribui ao agente).")
    ap.add_argument("item")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--frente", choices=FRENTES)
    ap.add_argument("--agente", action="store_true", help="põe o rótulo `agente` (NÃO atribui a issue; a atribuição é manual)")
    ap.add_argument("--agente-nuvem", action="store_true", dest="agente_nuvem",
                    help="põe a etiqueta `agente-nuvem` (item liberado pela orquestradora; também NÃO atribui: quem atribui é scripts/agente_nuvem.py)")
    ap.add_argument("--aplicar", action="store_true", help="cria a issue (padrão: ensaio)")
    ap.add_argument("--pacotes", type=Path, help="pasta dos pacotes (padrão: .claude/plano-100/pacotes deste checkout; fica fora do Git)")
    a = ap.parse_args(argv)
    for f in (sys.stdout, sys.stderr):
        if hasattr(f, "reconfigure"):
            f.reconfigure(encoding="utf-8", errors="replace")
    if not _ID.fullmatch(a.item) or not re.fullmatch(r"[\w.-]+/[\w.-]+", a.repo):
        print("erro: item (ex. 29.177) ou --repo (dono/nome) fora do formato", file=sys.stderr)
        return 1
    gh = gh or gh_real
    try:
        pacote = ler_pacote(a.item, pacotes or a.pacotes or PACOTES)
        titulo, corpo = montar(a.item, pacote)
        existente = ja_existe(a.repo, a.item, gh)
        if existente:
            print(f"já existe a issue #{existente[0]} ({existente[1].lower()}) com a marca do item {a.item}: nada criado")
            return 0
        pedidos = rotulos_da_issue(pacote, a.frente, a.agente, a.agente_nuvem)
        existentes = {str(x.get("name")) for x in json.loads(gh("label", "list", "--repo", a.repo, "--limit", "200", "--json", "name"))}
        rotulos = [r for r in pedidos if r in existentes]
        for r in pedidos:
            if r not in existentes:
                print(f"aviso: o rótulo '{r}' não existe no repositório e foi pulado (scripts/github_rotulos.py --aplicar)", file=sys.stderr)
        if not a.aplicar:
            print(f"ENSAIO. Título: {titulo}\nRótulos: {', '.join(rotulos) or '(nenhum)'}\n\n{corpo}")
            return 0
        args = ["issue", "create", "--repo", a.repo, "--title", titulo, "--body-file", "-"]
        for r in rotulos:
            args += ["--label", r]
        print(gh(*args, entrada=corpo).strip())
    except (RuntimeError, ValueError, KeyError, TypeError, AttributeError, OSError) as e:
        print(f"erro: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
