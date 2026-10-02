"""Chunker (etapa B): corta em janelas de linhas SÓ os arquivos que a etapa A escolheu.

O teto de quantidade e de bytes é duro: o chunker para ANTES de um chunk que estouraria `max_bytes`, porque o que
sai daqui é o que um provedor semântico pode receber, e "um pouco acima do limite" não existe. Arquivo fora do
universo do `Workspace` (sensível, binário, grande, inexistente, fora da raiz) não gera chunk, mesmo que seu caminho
seja pedido: o portão é do `Workspace`, não da boa vontade de quem chama.
"""
from __future__ import annotations

from collections.abc import Sequence

from app.modules.context_retrieval.domain.model import Chunk
from app.modules.context_retrieval.infrastructure.workspace import Workspace


class Chunker:
    """`ChunkSource`: janelas de `chunk_lines` linhas com `overlap` de sobreposição, ordem determinística."""

    def __init__(self, workspace: Workspace, *, chunk_lines: int = 40, overlap: int = 5) -> None:
        self._ws = workspace
        self._tam = max(1, chunk_lines)
        # sobreposição >= tamanho faria a janela não avançar; mantém o passo em pelo menos 1 linha
        self._passo = max(1, self._tam - max(0, overlap))

    def chunks_for(self, paths: Sequence[str], *, max_chunks: int, max_bytes: int) -> list[Chunk]:
        saida: list[Chunk] = []
        usados = 0
        for caminho in dict.fromkeys(paths):  # ordem do pedido, sem repetir
            texto = self._ws.read_text(caminho)
            if texto is None:
                continue
            linhas = texto.split("\n")
            if linhas and linhas[-1] == "":
                linhas.pop()  # o \n final não é uma linha a mais
            total = len(linhas)
            ini = 1
            while ini <= total:
                fim = min(total, ini + self._tam - 1)
                corpo = "\n".join(linhas[ini - 1:fim])
                if corpo.strip():
                    tamanho = len(corpo.encode("utf-8"))
                    if len(saida) >= max_chunks or usados + tamanho > max_bytes:
                        return saida
                    saida.append(Chunk(caminho, ini, fim, corpo))
                    usados += tamanho
                if fim >= total:
                    break
                ini += self._passo
        return saida
