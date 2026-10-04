#!/usr/bin/env python3
"""PostToolUse (Edit/Write): um .js de .claude/workflows/ tem que ficar em LF e sem quebra de linha real em string.

Regra: .claude/rules/workflows-js.md (K-007): o diálogo de aprovação do Workflow recusa o script com "control
characters" quando há CR ou quebra de linha literal dentro de uma string.

Recebe o JSON do hook no stdin. Arquivo fora de .claude/workflows/*.js: sai 0 sem abrir o arquivo.
Acusou: sai 2 e o motivo vai no stderr. No PostToolUse isso NÃO desfaz a edição (a ferramenta já rodou): o Claude
recebe o aviso e corrige. Falha aberta: erro interno sai 0, com aviso no stderr.

Analisador léxico mínimo de JS: aspas simples e duplas, template (crase) com ${...} aninhado, comentários e regex
literal (decidido pelo token anterior, como faz um lexer de JS).
"""
import json
import re
import sys

# Depois destes caracteres, uma "/" abre regex literal; depois de identificador, número, ")" ou "]", é divisão.
ABRE_REGEX_DEPOIS = set("(,=:[!&|?{};+-*%<>~^")
PALAVRAS_ANTES_DE_REGEX = {"return", "typeof", "instanceof", "in", "of", "new", "delete", "void", "throw", "case",
                           "do", "else", "yield", "await"}


def linha_de(t, i):
    return t.count("\n", 0, i) + 1


def achar_quebra(t):
    """Devolve (linha, tipo) da primeira quebra de linha real dentro de string/template/regex, ou None."""
    i, n = 0, len(t)
    pilha = []  # chaves abertas: "tpl" = ${ de template, "{" = bloco comum
    ultimo = ""  # último token significativo (para decidir regex x divisão)

    def ler_string(i, q):
        j = i + 1
        while j < n:
            c = t[j]
            if c == "\\":
                j += 2
                continue
            if c == "\n":
                return None, j
            if c == q:
                return j + 1, None
            j += 1
        return n, None

    def ler_template(i):
        # i aponta para o caractere depois da crase de abertura (ou depois do "}" que fecha um ${}).
        j = i
        while j < n:
            c = t[j]
            if c == "\\":
                j += 2
                continue
            if c == "\n":
                return None, j, False
            if c == "`":
                return j + 1, None, False
            if c == "$" and j + 1 < n and t[j + 1] == "{":
                return j + 2, None, True
            j += 1
        return n, None, False

    while i < n:
        c = t[i]
        if c in " \t\r\n":
            i += 1
            continue
        if t.startswith("//", i):
            j = t.find("\n", i)
            i = n if j < 0 else j
            continue
        if t.startswith("/*", i):
            j = t.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c in "'\"":
            fim, quebra = ler_string(i, c)
            if quebra is not None:
                return linha_de(t, quebra), "string"
            i, ultimo = fim, "lit"
            continue
        if c == "`":
            fim, quebra, abriu = ler_template(i + 1)
            if quebra is not None:
                return linha_de(t, quebra), "template"
            if abriu:
                pilha.append("tpl")
            i, ultimo = fim, ("(" if abriu else "lit")
            continue
        if c == "{":
            pilha.append("{")
            i, ultimo = i + 1, "{"
            continue
        if c == "}":
            topo = pilha.pop() if pilha else "{"
            if topo == "tpl":
                fim, quebra, abriu = ler_template(i + 1)
                if quebra is not None:
                    return linha_de(t, quebra), "template"
                if abriu:
                    pilha.append("tpl")
                i, ultimo = fim, ("(" if abriu else "lit")
            else:
                i, ultimo = i + 1, "}"
            continue
        if c == "/":
            eh_regex = ultimo == "" or ultimo in ABRE_REGEX_DEPOIS or ultimo in PALAVRAS_ANTES_DE_REGEX
            if eh_regex:
                j, classe = i + 1, False
                while j < n:
                    d = t[j]
                    if d == "\\":
                        j += 2
                        continue
                    if d == "\n":
                        return linha_de(t, j), "regex"
                    if d == "[":
                        classe = True
                    elif d == "]":
                        classe = False
                    elif d == "/" and not classe:
                        break
                    j += 1
                j += 1
                while j < n and (t[j].isalnum() or t[j] == "_"):
                    j += 1  # flags
                i, ultimo = j, "lit"
                continue
            i, ultimo = i + 1, "/"
            continue
        m = re.match(r"[A-Za-z_$][\w$]*", t[i:i + 64])
        if m:
            palavra = m.group(0)
            i, ultimo = i + len(palavra), (palavra if palavra in PALAVRAS_ANTES_DE_REGEX else "id")
            continue
        if c.isdigit():
            m = re.match(r"[\w.]+", t[i:i + 64])
            i, ultimo = i + len(m.group(0)), "lit"
            continue
        i, ultimo = i + 1, (c if c in ABRE_REGEX_DEPOIS else ("id" if c in ")]" else c))
    return None


def alvo(caminho):
    p = (caminho or "").replace("\\", "/").lower()
    return "/.claude/workflows/" in "/" + p and p.endswith(".js")


def avisar(msg):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(msg, file=sys.stderr)


def main():
    ev = json.load(sys.stdin)
    caminho = (ev.get("tool_input") or {}).get("file_path", "")
    if not alvo(caminho):
        return 0
    with open(caminho, "rb") as f:
        bruto = f.read()
    erros = []
    if b"\r" in bruto:
        erros.append("o arquivo ficou com CRLF: regrave-o inteiro em LF")
    achado = achar_quebra(bruto.decode("utf-8", "replace").replace("\r", ""))
    if achado:
        erros.append("ficou uma quebra de linha real dentro de %s na linha %d: troque por uma linha só, montando o "
                     "texto com [...].join(NL)" % (achado[1], achado[0]))
    if erros:
        avisar("workflow-js: a edição em " + caminho + " JÁ FOI GRAVADA, mas " + "; e ".join(erros) + ". Sem isso o "
               "diálogo do Workflow recusa o script com 'control characters' (.claude/rules/workflows-js.md, K-007). "
               "Corrija agora, antes de rodar o Workflow.")
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:  # falha aberta, mas visível
        avisar("workflow-js: erro interno, liberado: " + repr(e))
        sys.exit(0)
