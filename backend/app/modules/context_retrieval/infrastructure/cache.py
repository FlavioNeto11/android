"""Cache da resposta semântica, em JSON no disco, sem migração de banco.

Chave = revisão do repositório + pergunta normalizada + escopo + versão do retrieval + provedor + modelo + etapa. Mudou
qualquer um, é outra chave: resultado de uma revisão nunca atende a outra (um arquivo editado muda a revisão, e é
isso que invalida). Guarda SÓ caminho, linha e nota — nunca trecho de código, nunca a pergunta crua (a chave é um
hash) e nunca segredo. Arquivo corrompido ou ilegível é miss silencioso: cache nunca derruba o retrieval.

O mapa do repositório tem cache próprio (`repomap.py`, por revisão); este é só o da resposta do provedor.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from ..domain.model import RETRIEVAL_VERSION

#: Teto de arquivos guardados; passado dele, os mais antigos saem. Cada entrada tem poucos centenas de bytes.
MAX_ENTRADAS = 2000


def normalizar_pergunta(query: str) -> str:
    return re.sub(r"\s+", " ", query.strip().lower())


class SemanticCache:
    def __init__(self, directory: Path | None, *, enabled: bool = True) -> None:
        self._dir = directory
        self.enabled = enabled and directory is not None
        self.hits = 0
        self.misses = 0

    def key(self, *, revision: str, query: str, scope: tuple[str, ...], provider: str, model: str, stage: str,
            extra: str = "") -> str:
        bruto = "\x1f".join([RETRIEVAL_VERSION, revision, normalizar_pergunta(query), "|".join(scope), provider, model,
                             stage, extra])
        return hashlib.sha256(bruto.encode("utf-8")).hexdigest()

    def _arquivo(self, key: str) -> Path | None:
        if not self.enabled or self._dir is None or not re.fullmatch(r"[0-9a-f]{64}", key):
            return None
        return self._dir / f"{key}.json"

    def get(self, key: str) -> dict[str, object] | None:
        arq = self._arquivo(key)
        if arq is None:
            return None
        try:
            valor = json.loads(arq.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.misses += 1
            return None
        if not isinstance(valor, dict):
            self.misses += 1
            return None
        self.hits += 1
        return valor

    def put(self, key: str, value: dict[str, object]) -> None:
        arq = self._arquivo(key)
        if arq is None:
            return
        try:
            self._dir.mkdir(parents=True, exist_ok=True)  # type: ignore[union-attr]
            tmp = arq.with_suffix(".tmp")
            tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, arq)
            self._podar()
        except OSError:
            return

    def _podar(self) -> None:
        assert self._dir is not None
        arquivos = sorted(self._dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
        for velho in arquivos[:-MAX_ENTRADAS]:
            try:
                velho.unlink()
            except OSError:
                pass
