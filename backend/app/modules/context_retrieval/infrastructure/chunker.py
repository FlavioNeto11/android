"""Chunker (etapa B): corta em janelas de linhas SÓ os arquivos que a etapa A escolheu.

O teto de quantidade e de bytes é duro: o chunker para ANTES de um chunk que estouraria `max_bytes`, porque o que
sai daqui é o que um provedor semântico pode receber, e "um pouco acima do limite" não existe. Arquivo fora do
universo do `Workspace` (sensível, binário, grande, inexistente, fora da raiz) não gera chunk, mesmo que seu caminho
seja pedido: o portão é do `Workspace`, não da boa vontade de quem chama.

Duas seleções (J13, ADR-063), pela presença da `query`:

- **Sem `query`** (comportamento original): janelas do INÍCIO de cada arquivo, arquivo por arquivo, na ordem pedida. O 1º
  candidato esgota o teto e quem veio depois fica sem chunk (o H25 do J12).
- **Com `query`** (a que o serviço semântico usa): as janelas de cada arquivo são ordenadas pelos termos da pergunta que elas
  contêm (e, no empate, pela posição) e os candidatos são servidos em RODÍZIO, o 1º com o dobro das janelas por volta (a A põe
  o melhor primeiro). Medido de graça nas 30 perguntas do golden do poetry (`scripts/context-retrieval-chunk-eval.py`,
  `docs/dominios/context-retrieval.md`): a região esperada fica ao alcance em 27/30 com o esperado em 1º (antes 26), 24/30 em 2º
  (antes 11) e 24/30 em 3º (antes 1). Rodízio simples de janelas do início PIORA (7/30), por isso a ordem lexical vem junto.
"""
from __future__ import annotations

import re
from collections.abc import Sequence

from app.modules.context_retrieval.domain.model import CHUNK_SELECTION_VERSION, Chunk  # noqa: F401 - reexporta a versão
from app.modules.context_retrieval.infrastructure.workspace import Workspace

_VAZIAS = frozenset({"the", "and", "for", "that", "this", "with", "from", "what", "where", "which", "when", "how", "does",
                     "are", "was", "into", "not", "its", "has", "have", "can", "any", "all", "one", "out", "use", "used",
                     "uses", "file", "function"})
_PESO_DO_1O = 2


def termos_da_pergunta(texto: str) -> list[str]:
    """Identificadores e palavras da pergunta em minúsculas, quebrando snake_case e CamelCase, sem palavras vazias."""
    saida: list[str] = []
    for tok in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", texto):
        partes = [p for p in re.split(r"_+|(?<=[a-z0-9])(?=[A-Z])", tok) if len(p) >= 3]
        saida += [p.lower() for p in partes] + ([tok.lower()] if len(partes) > 1 else [])
    return [t for t in dict.fromkeys(saida) if t not in _VAZIAS]


def _pontua(texto: str, termos: Sequence[str]) -> int:
    minusculo = texto.lower()
    return sum(min(minusculo.count(t), 3) for t in termos)


class Chunker:
    """`ChunkSource`: janelas de `chunk_lines` linhas com `overlap` de sobreposição, ordem determinística."""

    def __init__(self, workspace: Workspace, *, chunk_lines: int = 40, overlap: int = 5) -> None:
        self._ws = workspace
        self._tam = max(1, chunk_lines)
        # sobreposição >= tamanho faria a janela não avançar; mantém o passo em pelo menos 1 linha
        self._passo = max(1, self._tam - max(0, overlap))

    def _janelas(self, caminho: str) -> list[Chunk]:
        texto = self._ws.read_text(caminho)
        if texto is None:
            return []
        linhas = texto.split("\n")
        if linhas and linhas[-1] == "":
            linhas.pop()  # o \n final não é uma linha a mais
        total = len(linhas)
        saida: list[Chunk] = []
        ini = 1
        while ini <= total:
            fim = min(total, ini + self._tam - 1)
            corpo = "\n".join(linhas[ini - 1:fim])
            if corpo.strip():
                saida.append(Chunk(caminho, ini, fim, corpo))
            if fim >= total:
                break
            ini += self._passo
        return saida

    def chunks_for(self, paths: Sequence[str], *, max_chunks: int, max_bytes: int, query: str | None = None) -> list[Chunk]:
        pedidos = list(dict.fromkeys(paths))  # ordem do pedido, sem repetir
        if query is None:
            fila = [w for p in pedidos for w in self._janelas(p)]
        else:
            fila = self._rodizio_lexical(pedidos, termos_da_pergunta(query))
        saida: list[Chunk] = []
        usados = 0
        for w in fila:
            tamanho = len(w.text.encode("utf-8"))
            if len(saida) >= max_chunks or usados + tamanho > max_bytes:
                break  # para ANTES de um chunk que estouraria o teto
            saida.append(w)
            usados += tamanho
        return saida

    def _rodizio_lexical(self, pedidos: list[str], termos: list[str]) -> list[Chunk]:
        """Uma volta por vez: o 1º candidato leva `_PESO_DO_1O` janelas, os demais uma, sempre a de maior pontuação que sobrou."""
        por_arquivo = {p: sorted(self._janelas(p), key=lambda w: (-_pontua(w.text, termos), w.start_line)) for p in pedidos}
        proximo = dict.fromkeys(pedidos, 0)
        pesos = {p: (_PESO_DO_1O if i == 0 else 1) for i, p in enumerate(pedidos)}
        fila: list[Chunk] = []
        while any(proximo[p] < len(por_arquivo[p]) for p in pedidos):
            for p in pedidos:
                for _ in range(pesos[p]):
                    if proximo[p] < len(por_arquivo[p]):
                        fila.append(por_arquivo[p][proximo[p]])
                        proximo[p] += 1
        return fila
