#!/usr/bin/env python3
"""Guarda do projeto (hook PreToolUse): barra leitura ou edição de segredo e edição de migração já commitada.

Recebe o JSON do hook no stdin. Sai com 2 (bloqueia; o motivo vai no stderr para o Claude) ou 0 (libera).
Falha aberta: erro interno libera a chamada e avisa no stderr, para um defeito aqui não travar as sessões.
config/config.yaml NÃO é segredo (segredo mora só no .env) e fica fora da lista: a orquestradora o lê e edita.
"""
import json
import os
import re
import subprocess
import sys

TOOLS_COM_CAMINHO = {"Read", "Edit", "Write", "MultiEdit", "NotebookEdit", "Grep"}
TOOLS_DE_ESCRITA = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
TOOLS_DE_COMANDO = {"Bash", "PowerShell"}
MIGRACAO = re.compile(r"(^|/)backend/migrations/\d{3}_[^/]+\.sql$")
SO_PELO_MECANISMO = (".claude/plano-100/estado.json", "docs/execucao-plano-100-runner.md")
# Arquivos que só se leem em trecho (CLAUDE.md, "Não abra por inteiro"): Read sem `limit` é barrado com a dica.
LEITURA_GRANDE = ("docs/decisoes.md", "docs/plano-100.md", "docs/estado-atual.md")
DICA_LEITURA = {
    "changelog.md": "grep -n \"<termo>\" CHANGELOG.md, ou Read com offset/limit (o topo: limit 80)",
    "docs/decisoes.md": "grep -n \"ADR-\" docs/decisoes.md e depois Read com offset/limit no ADR",
    "docs/plano-100.md": "grep -n \"^| <id> |\" docs/plano-100.md",
    "docs/estado-atual.md": "Read com limit 45 (o topo), ou sed -n 1,45p docs/estado-atual.md",
}
# Texto livre de commit ou tag (-m "…", --message=…) não abre arquivo: sai antes de procurar nomes de segredo.
MENSAGEM = re.compile(r"""(?:^|\s)(?:-m|--message)(?:=|\s+)(?:"(?:[^"\\]|\\.)*"|'[^']*'|\S+)""")


def normalizar(p):
    return p.replace("\\", "/").strip().strip("\"'").lower()


def sensivel(p):
    p = normalizar(p)
    base = p.rsplit("/", 1)[-1]
    if base == ".env" or (base.startswith(".env.") and base != ".env.example"):
        return True
    if base in ("cert.pem", "gradle.properties", "local.properties"):
        return True
    if base.endswith((".pem", ".key", ".jks", ".keystore")):
        return True
    return "/.cloudflared/" in "/" + p


def so_por_extensao(p):
    # True quando o token só é sensível pela extensão (não é o arquivo de ambiente, nome fixo nem a pasta do túnel).
    p = normalizar(p)
    base = p.rsplit("/", 1)[-1]
    if base == ".env" or base.startswith(".env.") or base in ("cert.pem", "gradle.properties", "local.properties"):
        return False
    return "/.cloudflared/" not in "/" + p


def parece_arquivo(token, cwd, raiz):
    # Num comando, `s.key` (coluna de SQL, atributo de objeto) termina como arquivo de chave e não é arquivo. O token
    # solto só é barrado se existir arquivo com esse nome na pasta atual, na raiz do projeto ou numa pasta do primeiro
    # nível dela (pega `cd data && cat credentials.key`). Com barra ou curinga, é caminho: barra sempre.
    t = token.strip().strip("\"'")
    if any(c in t for c in "/\\*?["):
        return True
    pastas = [cwd, raiz]
    try:
        pastas += [os.path.join(raiz, d) for d in os.listdir(raiz) if os.path.isdir(os.path.join(raiz, d))]
    except OSError:
        pass
    return any(p and os.path.isfile(os.path.join(p, t)) for p in pastas)


def leitura_grande(caminho):
    # Devolve a chave de DICA_LEITURA quando o caminho é um dos arquivos grandes, senão "". O CHANGELOG.md só conta
    # na raiz de um checkout (principal ou worktree: a pasta tem `.git`), para não pegar um changelog de dependência.
    alvo = normalizar(caminho)
    for rel in LEITURA_GRANDE:
        if alvo == rel or alvo.endswith("/" + rel):
            return rel
    if alvo.rsplit("/", 1)[-1] == "changelog.md":
        pasta = os.path.dirname(os.path.abspath(caminho))
        if os.path.exists(os.path.join(pasta, ".git")):
            return "changelog.md"
    return ""


def raiz_git(caminho):
    # Raiz do checkout que contém o arquivo (checkout principal ou worktree). Só lê.
    pasta = os.path.dirname(os.path.abspath(caminho))
    while pasta and not os.path.isdir(pasta):
        pasta = os.path.dirname(pasta)
    r = subprocess.run(["git", "-C", pasta, "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def commitado(caminho):
    raiz = raiz_git(caminho)
    if not raiz:
        return False
    c, r = normalizar(os.path.abspath(caminho)), normalizar(raiz).rstrip("/")
    rel = c[len(r) + 1:] if c.startswith(r + "/") else c
    # cat-file só lê objetos: não toca o índice nem cria index.lock.
    return subprocess.run(["git", "-C", raiz, "cat-file", "-e", "HEAD:" + rel], capture_output=True).returncode == 0


def bloquear(motivo):
    # O Claude Code lê o stderr como UTF-8; no Windows o padrão do Python é a página de código, e o acento chega quebrado.
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(motivo, file=sys.stderr)
    sys.exit(2)


def main():
    ev = json.load(sys.stdin)
    tool = ev.get("tool_name", "")
    inp = ev.get("tool_input") or {}

    if tool in TOOLS_DE_COMANDO:
        comando = MENSAGEM.sub(" ", inp.get("command", ""))
        tokens = re.split(r"[\s\"'=<>|;&()`,]+", comando)
        cwd = ev.get("cwd") or "."
        raiz = os.environ.get("CLAUDE_PROJECT_DIR") or cwd
        if any(t and sensivel(t) and (not so_por_extensao(t) or parece_arquivo(t, cwd, raiz)) for t in tokens):
            bloquear("Bloqueado pela guarda do projeto: o comando cita um arquivo de segredo (.env, chave ou "
                     "certificado). Para saber se a chave está configurada, use GET /api/ai. Se o nome aparece só "
                     "como texto, ponha-o numa mensagem -m \"…\" (que a guarda ignora), use git commit -F <arquivo>, "
                     "ou escreva 'arquivo de ambiente'.")
        return
    if tool not in TOOLS_COM_CAMINHO:
        return
    caminho = inp.get("file_path") or inp.get("notebook_path") or inp.get("path") or ""
    if not caminho:
        return
    if sensivel(caminho):
        bloquear("Bloqueado pela guarda do projeto: arquivo de segredo (" + caminho + "). Não leia nem edite.")
    if tool == "Read" and not inp.get("limit"):
        grande = leitura_grande(caminho)
        if grande:
            bloquear("Bloqueado pela guarda do projeto: " + caminho + " é grande e só se lê em trecho (CLAUDE.md, "
                     "'Não abra por inteiro'). Use: " + DICA_LEITURA[grande] + ".")
    if tool in TOOLS_DE_ESCRITA:
        alvo = normalizar(caminho)
        if alvo.endswith(SO_PELO_MECANISMO):
            bloquear("Bloqueado: " + caminho + " só muda por scripts/claude-plan-100.py aplicar|relatorio.")
        if MIGRACAO.search(alvo) and commitado(caminho):
            bloquear("Bloqueado: migração já commitada não se edita (" + caminho + "). Crie a próxima numerada.")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # falha aberta, mas visível
        print("guarda.py: erro interno, chamada liberada: " + repr(e), file=sys.stderr)
        sys.exit(0)
