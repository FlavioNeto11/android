"""Mapa do repositório (etapa A): caminho, linguagem, tamanho e símbolos por arquivo, SEM corpo de código.

É o que um provedor semântico enxerga para escolher arquivos, então o que entra aqui é decisão de privacidade: só o
nome dos símbolos de nível alto (e os métodos de classe) e a primeira linha do docstring do módulo. Nenhuma linha de
corpo, nenhum valor, nenhum comentário. Os símbolos saem por regex (rápido e tolerante a arquivo quebrado, ao
contrário de `ast`).

O mapa é caro de refazer no repositório inteiro e só muda com a revisão, então é guardado em JSON em disco,
chaveado por `revision` e `RETRIEVAL_VERSION`.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from app.modules.context_retrieval.domain.model import RETRIEVAL_VERSION, MapEntry, RepoMap
from app.modules.context_retrieval.domain.sensitive import hard_secret_kind, has_soft_secret
from app.modules.context_retrieval.infrastructure.workspace import Workspace

MAX_RESUMO = 80
MAX_TITULO = 60
#: Quantos arquivos de cache de revisões antigas ficam (o mais recente é sempre um deles).
CACHES_MANTIDOS = 3

_PY_TOPO = re.compile(r"^(?:async\s+def|def|class)\s+(\w+)")
_PY_CLASSE = re.compile(r"^class\s+(\w+)")
_PY_METODO = re.compile(r"^(?: {4}|\t)(?:async\s+def|def)\s+(\w+)")
_PY_DOCSTRING = re.compile(r'\A(?:\s*#[^\n]*\n|\s*\n)*\s*[rRuUbB]{0,2}("""|\'\'\')(.*?)\1', re.DOTALL)

_JS_DECL = re.compile(
    r"^(?:export\s+)?(?:default\s+)?(?:declare\s+)?(?:abstract\s+)?"
    r"(?:async\s+function\*?|function\*?|class|interface|enum|type)\s+([A-Za-z_$][\w$]*)")
_JS_CONST = re.compile(r"^export\s+(?:declare\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)")
_JS_TYPE_SEM_IGUAL = re.compile(r"^(?:export\s+)?(?:declare\s+)?type\s+[\w$]+\s*(?:<[^=]*>)?\s*(?:=|$)")

_SQL = re.compile(r"^\s*CREATE\s+(?:UNIQUE\s+)?(?:OR\s+REPLACE\s+)?(TABLE|INDEX|VIEW)\s+(?:IF\s+NOT\s+EXISTS\s+)?([^\s(]+)",
                  re.IGNORECASE)
_MD_TITULO = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$")
_PS_FUNCAO = re.compile(r"^\s*function\s+([\w\-:.]+)", re.IGNORECASE)

_FAMILIA_JS = frozenset({"typescript", "tsx", "javascript", "jsx"})


def _unicos(itens: list[str], limite: int) -> tuple[str, ...]:
    return tuple(list(dict.fromkeys(itens))[:limite])


def _simbolos_python(linhas: list[str]) -> list[str]:
    saida: list[str] = []
    classe: str | None = None
    for ln in linhas:
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        if ln[0] not in " \t":
            m = _PY_TOPO.match(ln)
            c = _PY_CLASSE.match(ln)
            if m or ln.startswith("@"):
                # só def/class/decorador de nível alto encerram a classe corrente: texto de docstring na coluna 0
                # (comum aqui) não pode fazer os métodos seguintes perderem a classe
                classe = c.group(1) if c else None
            if m:
                saida.append(m.group(1))
        elif classe:
            m = _PY_METODO.match(ln)
            if m:
                saida.append(f"{classe}.{m.group(1)}")
    return saida


def _simbolos_js(linhas: list[str]) -> list[str]:
    saida: list[str] = []
    for ln in linhas:
        m = _JS_DECL.match(ln) or _JS_CONST.match(ln)
        if m and (not ln.lstrip().startswith(("type ", "export type ", "declare type ", "export declare type "))
                  or _JS_TYPE_SEM_IGUAL.match(ln)):
            saida.append(m.group(1))
    return saida


def _simbolos_sql(linhas: list[str]) -> list[str]:
    return [f"{m.group(1).upper()} {m.group(2).strip(chr(34) + '`;')}" for ln in linhas if (m := _SQL.match(ln))]


def _simbolos_md(linhas: list[str]) -> list[str]:
    saida: list[str] = []
    cerca = False
    for ln in linhas:
        if ln.lstrip().startswith(("```", "~~~")):
            cerca = not cerca  # `# comentario` dentro de bloco de código não é título
            continue
        m = None if cerca else _MD_TITULO.match(ln)
        if m:
            saida.append(m.group(1)[:MAX_TITULO])
    return saida


def _simbolos_ps1(linhas: list[str]) -> list[str]:
    return [m.group(1) for ln in linhas if (m := _PS_FUNCAO.match(ln))]


def _resumo_python(texto: str) -> str:
    """Primeira linha do docstring do módulo, cortada em `MAX_RESUMO`; vazio se não houver."""
    m = _PY_DOCSTRING.match(texto[:6000])
    if not m:
        return ""
    primeira = next((x.strip() for x in m.group(2).split("\n") if x.strip()), "")
    return primeira if len(primeira) <= MAX_RESUMO else primeira[: MAX_RESUMO - 1].rstrip() + "…"


def _entrada(ws: Workspace, caminho: str, max_symbols: int) -> MapEntry:
    lang = ws.language_of(caminho)
    simbolos: tuple[str, ...] = ()
    resumo = ""
    extrator = {"python": _simbolos_python, "sql": _simbolos_sql, "markdown": _simbolos_md,
                "powershell": _simbolos_ps1}.get(lang) or (_simbolos_js if lang in _FAMILIA_JS else None)
    if extrator is not None:
        texto = ws.read_text(caminho)
        if texto is not None:
            simbolos = _unicos(extrator(texto.split("\n")), max_symbols)
            if lang == "python":
                resumo = _resumo_python(texto)
    return MapEntry(path=caminho, language=lang, size=ws.size_of(caminho) or 0, symbols=simbolos, summary=resumo)


def _com_segredo(e: MapEntry) -> bool:
    """Resumo de docstring, título de markdown ou símbolo com cara de credencial. Só o mapa da etapa A (saída para provedor
    externo) usa estas entradas; o retrieval local não passa por aqui."""
    texto = RepoMap("", (e,)).render()
    return bool(hard_secret_kind(texto)) or has_soft_secret(texto)


def _construir(ws: Workspace, revisao: str, max_symbols: int) -> RepoMap:
    # A entrada com segredo (duro ou mole) é OMITIDA do mapa: nem fica em cache no disco, nem entra no payload, e não é
    # trocada por marcador (que viraria termo de ranking). `SemanticRetriever` ainda confere cada entrada na saída.
    entradas = tuple(e for e in (_entrada(ws, p, max_symbols) for p in sorted(ws.files())) if not _com_segredo(e))
    return RepoMap(revision=revisao, entries=entradas)


def build_repo_map(workspace: Workspace, *, max_symbols: int = 12) -> RepoMap:
    """Mapa do repositório na revisão atual, sem cache."""
    return _construir(workspace, workspace.revision(), max_symbols)


# ---------------------------------------------------------------- cache em disco
def _para_dict(mapa: RepoMap, max_symbols: int) -> dict[str, object]:
    return {
        "revision": mapa.revision, "version": RETRIEVAL_VERSION, "max_symbols": max_symbols,
        "entries": [{"path": e.path, "language": e.language, "size": e.size, "symbols": list(e.symbols),
                     "summary": e.summary} for e in mapa.entries],
    }


def _de_dict(d: object, revisao: str, max_symbols: int) -> RepoMap | None:
    """`RepoMap` do JSON, ou `None` se a versão, a revisão ou a forma não baterem (cache corrompido = descartado)."""
    try:
        if d["revision"] != revisao or d["version"] != RETRIEVAL_VERSION or d["max_symbols"] != max_symbols:
            return None
        entradas = tuple(MapEntry(path=str(e["path"]), language=str(e["language"]), size=int(e["size"]),
                                  symbols=tuple(str(s) for s in e["symbols"]), summary=str(e["summary"]))
                         for e in d["entries"])
    except (KeyError, TypeError, ValueError, IndexError, AttributeError, OverflowError):
        return None
    return RepoMap(revision=revisao, entries=entradas)


def _nome_cache(revisao: str) -> str:
    seguro = re.sub(r"[^A-Za-z0-9]+", "-", revisao).strip("-") or "sem-revisao"
    return f"repomap-{seguro}-v{RETRIEVAL_VERSION}.json"


class RepoMapProvider:
    """`MapSource`: devolve o mapa da revisão atual, do cache (memória, depois disco) ou construído."""

    def __init__(self, workspace: Workspace, cache_dir: Path | None = None, *, max_symbols: int = 12) -> None:
        self._ws = workspace
        self._dir = Path(cache_dir) if cache_dir is not None else None
        self._max_symbols = max_symbols
        self._memoria: RepoMap | None = None
        #: De onde veio o último mapa: "memory", "disk" ou "built". Para teste e observabilidade, nunca para decisão.
        self.last_source = ""

    def repo_map(self) -> RepoMap:
        rev = self._ws.revision()
        if self._memoria is not None and self._memoria.revision == rev:
            self.last_source = "memory"
            return self._memoria
        mapa = self._ler_disco(rev)
        if mapa is not None:
            self.last_source = "disk"
        else:
            mapa = _construir(self._ws, rev, self._max_symbols)
            self.last_source = "built"
            self._gravar_disco(mapa)
        self._memoria = mapa
        return mapa

    def _ler_disco(self, revisao: str) -> RepoMap | None:
        if self._dir is None:
            return None
        try:
            with open(self._dir / _nome_cache(revisao), encoding="utf-8") as fh:
                return _de_dict(json.load(fh), revisao, self._max_symbols)
        except (OSError, ValueError, RecursionError):  # ausente, ilegível, JSON quebrado ou aninhado demais: reconstrói
            return None

    def _gravar_disco(self, mapa: RepoMap) -> None:
        if self._dir is None:
            return
        destino = self._dir / _nome_cache(mapa.revision)
        tmp = destino.with_name(f"{destino.name}.{os.getpid()}.tmp")
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(_para_dict(mapa, self._max_symbols), ensure_ascii=False, separators=(",", ":")),
                           encoding="utf-8")
            os.replace(tmp, destino)  # atômico: leitor nunca vê arquivo pela metade
            self._limpar_antigos(destino)
        except OSError:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def _limpar_antigos(self, atual: Path) -> None:
        """Mantém `CACHES_MANTIDOS` arquivos: o atual (mesmo que o mtime empate) e os mais recentes entre os outros."""
        if self._dir is None:
            return
        try:
            outros = sorted((p for p in self._dir.glob("repomap-*-v*.json") if p.name != atual.name),
                            key=lambda p: (p.stat().st_mtime_ns, p.name), reverse=True)
        except OSError:
            return
        for p in outros[CACHES_MANTIDOS - 1:]:
            try:
                p.unlink()
            except OSError:
                pass
