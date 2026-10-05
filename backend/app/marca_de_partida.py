"""A marca da partida do backend (29.124): o supervisor distingue "subindo devagar" de "travado".

No incidente de 05/10, com o disco saturado, a partida do backend passou da carência do supervisor (90 s e mais três
conferências) e virou laço: a subida das 13:07:31Z não escreveu linha em 2,5 min e foi morta, e a das 13:10:18Z
também.

Como funciona. A cada subida, o supervisor sorteia um id e o passa ao backend na variável `POC_PARTIDA_ID`. O backend
reescreve a marca (`id`, `fase`, `ts`) nas fases naturais da partida: antes do `AppState` (que migra o banco), depois
dele, antes do `poc.start()` e, ao fim, `no_ar`. O supervisor só tolera o silêncio de `/api/health` quando a marca é
DESTA subida (o id bate), a fase ainda não é `no_ar`, a partida está abaixo do teto e a última reescrita é recente.

Por que um id e não o PID: no Windows, o `python.exe` do venv é um lançador; o PID que o supervisor guarda é o do
lançador, e o `os.getpid()` do backend é o do filho dele. O id passa pelo ambiente e não depende da árvore.

Ler ou gravar a marca nunca derruba nada: o backend engole qualquer erro de escrita, e o supervisor trata arquivo
ausente, parcial ou ilegível como "sem marca", que é a regra de sempre. No arquivo, só id, fase e ts.

Só stdlib: o supervisor sobe sem as dependências do backend.
"""
from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ARQUIVO = "backend-partida.json"
VARIAVEL = "POC_PARTIDA_ID"
#: A pasta de dados do supervisor (N5 da leitura do #421): o backend grava a marca nela e o vigia despeja em
#: `<pasta>/logs`, onde o supervisor procura. Sem ela (backend subido à mão), valem as pastas do config.
PASTA = "POC_PASTA_DO_SUPERVISOR"

ANTES_DO_ESTADO = "antes_do_estado"
ESTADO_PRONTO = "estado_pronto"
#: 29.131: reescritas DENTRO do `AppState`, para o prazo de 240 s valer por passo e não pelo estado inteiro (com uma
#: só marca antes dele, uma migração lenta passava dos 240 s e era morta no meio).
MIGRANDO = "migrando"
APARELHOS = "aparelhos"
INICIANDO = "iniciando"
NO_AR = "no_ar"


@dataclass(frozen=True)
class Marca:
    id: str
    fase: str
    ts: float


def nova_partida() -> str:
    return secrets.token_hex(8)


def pasta_do_supervisor(padrao: Path, sub: str = "") -> Path:
    """A pasta que o supervisor passou (mais `sub`), ou `padrao`, a do config, sem supervisor."""
    pasta = os.environ.get(PASTA)
    if not pasta:
        return padrao
    return Path(pasta) / sub if sub else Path(pasta)


def gravar(pasta: Path, fase: str, *, partida_id: str | None = None,
           agora: Callable[[], float] = time.time) -> None:
    """Grava por troca atômica (temporário + `os.replace`). Sem id (backend subido à mão, teste), não grava nada."""
    ident = partida_id if partida_id is not None else os.environ.get(VARIAVEL)
    if not ident:
        return
    try:
        pasta.mkdir(parents=True, exist_ok=True)
        tmp = pasta / f"{ARQUIVO}.{os.getpid()}.tmp"
        tmp.write_text(json.dumps({"id": ident, "fase": fase, "ts": agora()}), encoding="utf-8")
        os.replace(tmp, pasta / ARQUIVO)
    except Exception:  # noqa: BLE001 - a marca é ajuda ao supervisor; a partida não pode cair por causa dela
        pass


def ler(pasta: Path) -> Marca | None:
    """A marca gravada, ou `None` se falta, está pela metade ou não tem a forma esperada."""
    try:
        dados = json.loads((pasta / ARQUIVO).read_text(encoding="utf-8"))
        if not isinstance(dados, dict):
            return None
        ident, fase, ts = dados.get("id"), dados.get("fase"), dados.get("ts")
        if not (isinstance(ident, str) and isinstance(fase, str) and isinstance(ts, (int, float))
                and not isinstance(ts, bool)):
            return None
        return Marca(ident, fase, float(ts))
    except Exception:  # noqa: BLE001 - ilegível ou corrida com a troca: vale a regra de sempre
        return None
