"""Retriever léxico: busca literal, sem distinção de maiúscula, dos identificadores (ou palavras) da pergunta.

É o sinal mais forte quando a pessoa nomeou um símbolo (`explicit_identifiers`): semântica nenhuma bate o `grep`
de `pick_device_for_task`. Dois motores, UM resultado: o `rg` só diz EM QUE LINHAS há casamento (e só nos arquivos
do universo do `Workspace`, que é o que garante que nada sensível seja varrido); a contagem, o ranking e as janelas
são sempre calculados em Python sobre essas linhas. Assim o fallback puro e o `rg` não divergem por detalhe de
alternância ou de casamento sobreposto do `rg`.

A metadata devolvida diz QUANTOS termos e arquivos, nunca QUAIS termos nem conteúdo.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from collections.abc import Iterable, Sequence

from app.devices.sdk import sem_segredos
from app.modules.context_retrieval.domain.identifiers import explicit_identifiers
from app.modules.context_retrieval.domain.model import ContextSelection, FileHit, Region, RetrievalRequest
from app.modules.context_retrieval.infrastructure.workspace import Workspace, normalize_scope, path_in_scope

STOPWORDS = frozenset("""
a o as os um uma uns umas de do da dos das em no na nos nas por para com sem sob sobre entre ate que qual quais quem
onde quando como ser foi sao esta estao tem ter faz fazer ha se ou e mas nao sim ao aos pelo pela pelos pelas
isso isto esse essa este desta deste dessa desse aqui ali la mais menos muito cada todo toda todos todas outro outra
the an of to in on at by for with from is are was be been do does did how where who what which when and or not it its
this that these those as into than then there their they them can will would should could has have had
""".split())

#: Teto de palavras quando a pergunta não traz identificador: mais que isto só alonga a linha de comando do `rg`.
MAX_PALAVRAS = 20
_PALAVRA = re.compile(r"[A-Za-zÀ-ÿ]{5,}")
#: Folga sob o limite de ~32 mil caracteres da linha de comando do Windows.
_LIMITE_LINHA_CMD = 20_000
#: `.cmd`/`.bat` passam pelo cmd.exe, que corta a linha em 8191 caracteres (e corta em silêncio).
_LIMITE_LINHA_CMD_SCRIPT = 7_000
_TIMEOUT_RG_S = 30.0


def deaccent(text: str) -> str:
    """Remove acentos preservando a caixa (o BM25 precisa dela para separar camelCase)."""
    nfd = unicodedata.normalize("NFD", text)
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn")


def fold(text: str) -> str:
    """Minúscula sem acento: o código do projeto mistura "manutenção" e "manutencao"."""
    return deaccent(text).lower()


#: Um termo é um grupo de variantes equivalentes (com e sem acento); casa se qualquer variante casar.
TermGroup = tuple[str, ...]


def build_terms(query: str) -> list[TermGroup]:
    """Termos da busca: identificadores explícitos; sem eles, palavras significativas (>= 5 letras, fora de stopwords)."""
    ids = explicit_identifiers(query)
    brutos = ids or [w for w in _PALAVRA.findall(query) if fold(w) not in STOPWORDS]
    grupos: list[TermGroup] = []
    vistos: set[str] = set()
    for b in brutos:
        chave = fold(b)
        if chave in vistos:
            continue
        vistos.add(chave)
        grupos.append(tuple(dict.fromkeys(v for v in (b.lower(), chave) if v)))
        if not ids and len(grupos) >= MAX_PALAVRAS:
            break
    return grupos


def merge_spans(spans: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    """Une intervalos de linha que se sobrepõem ou se tocam; saída ordenada."""
    saida: list[tuple[int, int]] = []
    for ini, fim in sorted(spans):
        if saida and ini <= saida[-1][1] + 1:
            saida[-1] = (saida[-1][0], max(saida[-1][1], fim))
        else:
            saida.append((ini, fim))
    return saida


def _contar(linha_baixa: str, grupo: TermGroup) -> int:
    return sum(linha_baixa.count(v) for v in grupo)


def localizar_ripgrep(configurado: str | None = None) -> str | None:
    """Caminho de um `rg` utilizável, ou `None` (e então o motor é o Python, com o mesmo resultado).

    Ordem: o caminho configurado (`context_retrieval.lexical.ripgrep_path`), a variável `RIPGREP_PATH` e, por último, o
    PATH (`shutil.which` já aplica o PATHEXT do Windows: acha `rg.exe`, `rg.cmd`). Nenhum caminho de instalação é
    presumido: o ripgrep do VS Code ou o embutido em outra ferramenta não são procurados, porque mudam de versão e de
    pasta. Candidato que não executa `--version` é descartado.
    """
    candidatos = [configurado, os.environ.get("RIPGREP_PATH"), shutil.which("rg")]
    for c in candidatos:
        if not c:
            continue
        achado = c if os.path.isfile(c) else shutil.which(c)
        if not achado:
            continue
        try:
            r = subprocess.run([achado, "--version"], capture_output=True, timeout=5, check=False, env=sem_segredos())
        except (OSError, subprocess.SubprocessError):
            continue
        if r.returncode == 0 and r.stdout.startswith(b"ripgrep"):
            return achado
    return None


class LexicalRetriever:
    """`ContextRetriever` por casamento literal. Não guarda estado entre pedidos (o universo é do `Workspace`)."""

    name = "lexical"

    def __init__(self, workspace: Workspace, *, use_ripgrep: bool = True, window_lines: int = 7,
                 max_windows_per_file: int = 3, ripgrep_path: str | None = None) -> None:
        self._ws = workspace
        self._rg = localizar_ripgrep(ripgrep_path) if use_ripgrep else None
        self._half = max(0, window_lines // 2)
        self._max_windows = max(1, max_windows_per_file)

    # ------------------------------------------------------------------ motores
    def _rg_linhas(self, arquivos: Sequence[str], grupos: Sequence[TermGroup]) -> dict[str, list[int]] | None:
        """Linhas casadas por arquivo segundo o `rg`, ou `None` se ele falhar (o chamador cai no Python)."""
        if not self._rg:
            return None
        padroes = [x for g in grupos for v in g for x in ("-e", v)]
        base = [self._rg, "--json", "--no-config", "--fixed-strings", "--ignore-case", "--no-ignore", "--hidden",
                *padroes, "--"]
        limite = _LIMITE_LINHA_CMD_SCRIPT if self._rg.lower().endswith((".cmd", ".bat")) else _LIMITE_LINHA_CMD
        gasto = sum(len(a) + 1 for a in base)
        lotes: list[list[str]] = [[]]
        for arq in arquivos:
            if lotes[-1] and gasto + sum(len(a) + 1 for a in lotes[-1]) + len(arq) + 1 > limite:
                lotes.append([])
            lotes[-1].append(arq)
        achados: dict[str, list[int]] = {}
        flags = 0x08000000 if sys.platform == "win32" else 0
        for lote in lotes:
            if not lote:
                continue
            try:
                r = subprocess.run([*base, *lote], cwd=self._ws.root, capture_output=True, timeout=_TIMEOUT_RG_S,
                                   env=sem_segredos(), creationflags=flags, check=False)
            except (OSError, subprocess.SubprocessError):
                return None
            if r.returncode not in (0, 1):  # 1 = nenhum casamento; 2 = erro (arquivo ilegível etc.): descarta o motor
                return None
            for bruto in r.stdout.decode("utf-8", errors="replace").splitlines():
                try:
                    ev = json.loads(bruto)
                except ValueError:
                    return None
                if ev.get("type") != "match":
                    continue
                dados = ev["data"]
                caminho = dados.get("path", {}).get("text")
                numero = dados.get("line_number")
                if isinstance(caminho, str) and isinstance(numero, int):
                    achados.setdefault(caminho.replace(chr(92), "/"), []).append(numero)
        return achados

    @staticmethod
    def _varrer(texto: str, grupos: Sequence[TermGroup], so_linhas: Iterable[int] | None
                ) -> tuple[list[int], dict[int, set[int]]]:
        """Contagem por termo e, por linha casada, o conjunto de termos que ela contém. Mesmo código para os dois motores."""
        if so_linhas is None:  # motor Python: larga o arquivo inteiro de uma vez quando nenhum termo aparece
            baixo = texto.lower()
            if not any(v in baixo for g in grupos for v in g):
                return [0] * len(grupos), {}
        linhas = texto.split("\n")
        numeros = range(1, len(linhas) + 1) if so_linhas is None else sorted({n for n in so_linhas if 1 <= n <= len(linhas)})
        contagem = [0] * len(grupos)
        por_linha: dict[int, set[int]] = {}
        for n in numeros:
            baixa = linhas[n - 1].lower()
            for i, g in enumerate(grupos):
                c = _contar(baixa, g)
                if c:
                    contagem[i] += c
                    por_linha.setdefault(n, set()).add(i)
        return contagem, por_linha

    def _janelas(self, caminho: str, texto: str, por_linha: dict[int, set[int]]) -> tuple[Region, ...]:
        """Até `max_windows` janelas ao redor das linhas com mais termos distintos; as que se tocam viram uma só."""
        linhas = texto.split("\n")
        total = len(linhas)
        spans: list[tuple[int, int]] = []
        for n in sorted(por_linha, key=lambda k: (-len(por_linha[k]), k)):
            if any(a <= n <= b for a, b in spans):
                continue  # já coberta por uma janela: não gasta o limite com ela
            spans = merge_spans([*spans, (max(1, n - self._half), min(total, n + self._half))])
            if len(spans) >= self._max_windows:
                break
        return tuple(Region(caminho, a, b, "\n".join(linhas[a - 1:b])) for a, b in spans)

    # ------------------------------------------------------------------ API
    def retrieve(self, request: RetrievalRequest) -> ContextSelection:
        t0 = time.perf_counter()
        grupos = build_terms(request.query)
        escopo = normalize_scope(request.scope)
        arquivos = [f for f in self._ws.files() if path_in_scope(f, escopo)]
        motor = "rg" if self._rg else "python"
        meta = {"terms": len(grupos), "files_considered": len(arquivos), "engine": motor}
        if not grupos:
            return ContextSelection.empty(self.name, warnings=("no_terms",), metadata=meta,
                                          latency_ms=(time.perf_counter() - t0) * 1000)

        candidatas = self._rg_linhas(arquivos, grupos) if self._rg else None
        if candidatas is None:
            motor = meta["engine"] = "python"
            alvo: list[tuple[str, list[int] | None]] = [(a, None) for a in arquivos]
        else:
            alvo = [(a, candidatas[a]) for a in arquivos if a in candidatas]

        # Motor Python: o ranking só precisa das CONTAGENS, que são aditivas por linha (o termo não atravessa quebra de
        # linha), então contar no texto inteiro dá o mesmo número que a varredura linha a linha e é bem mais rápido. As
        # linhas casadas (para as janelas) só são levantadas para os arquivos ESCOLHIDOS.
        rapido = candidatas is None and not any(chr(10) in v for g in grupos for v in g)
        resultados: list[tuple[int, int, str, dict[int, set[int]] | None]] = []
        for caminho, linhas_rg in alvo:
            texto = self._ws.read_text(caminho)
            if texto is None:
                continue
            if rapido:
                baixo = texto.lower()
                contagem = [_contar(baixo, g) for g in grupos]
                por_linha: dict[int, set[int]] | None = None
            else:
                contagem, por_linha = self._varrer(texto, grupos, linhas_rg)
            distintos = sum(1 for c in contagem if c)
            if distintos:
                resultados.append((distintos, sum(contagem), caminho, por_linha))
        resultados.sort(key=lambda r: (-r[0], -r[1], r[2]))

        escolhidos = resultados[: max(0, request.top_k)]
        hits: list[FileHit] = []
        regioes: list[Region] = []
        for distintos, ocorrencias, caminho, por_linha in escolhidos:
            texto = self._ws.read_text(caminho) or ""
            if por_linha is None:
                _, por_linha = self._varrer(texto, grupos, None)
            janelas = self._janelas(caminho, texto, por_linha)
            hits.append(FileHit(path=caminho, score=float(distintos * 1000 + min(ocorrencias, 999)), source=self.name,
                                matched_terms=distintos, regions=janelas))
            regioes.extend(janelas)
        return ContextSelection(
            selected_files=tuple(hits), selected_regions=tuple(regioes), source=self.name,
            latency_ms=(time.perf_counter() - t0) * 1000, metadata=meta,
            warnings=() if hits else ("no_matches",))
