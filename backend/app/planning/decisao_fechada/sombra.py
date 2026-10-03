"""Registro durável da sombra da porta `DecisaoFechada` (item 31.5, ADR-069): migração 074.

O que entra: UMA linha por pergunta respondida ou por fallback (a recusa de privacidade também é medida: ela mostra o que o
envio fechado deixou de decidir). O que NUNCA entra: o estado enviado, o texto das opções e as instruções da pergunta. A linha
só tem ids opacos, categorias dos vocabulários fechados de `contrato.py` e números; todo id é conferido por formato ANTES de
gravar, e o que não confere vira fallback (`unknown_choice`) ou NULL, nunca texto livre.

Dois preenchimentos vêm DEPOIS da gravação, por quem sabe o desfecho (31.8 e 31.9): a decisão real do caminho atual
(`casar_decisao_real`) e o desfecho (`casar_desfecho`), ambos por `ref` ou `step_id`. Só preenchem o que está vazio: a primeira
decisão real e o primeiro desfecho valem, e uma segunda chamada não reescreve a prova.

Retenção própria (`ai.decisao_fechada.retencao_dias`, padrão 180) no padrão da 055: a purga leva DIAS INTEIROS e o agregado
diário (`decisao_fechada_diario`) do que vai sumir é recalculado ANTES, então um dia nunca é agregado pela metade e o agregado
sobrevive às linhas. Um fallback nunca conta como acerto: ele só entra em `n` e `fallbacks`.
"""
from __future__ import annotations

import json
import logging
import math
import re
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Final

from ...util import now, to_iso
from .contrato import FALLBACKS, ID_NENHUMA, MOTIVOS_DE_PRIVACIDADE, ORIGENS
from .porta import Observador, RegistroDeDecisao

if TYPE_CHECKING:
    from ...db import Database

log = logging.getLogger("poc.ai")

#: Desfecho do caminho atual, vocabulário FECHADO (roteiro §3 item 8): o rótulo com que se mede o aceite errado. Valor novo
#: nasce num diff que o revisor veja (a coluna não tem CHECK).
DESFECHOS: Final[tuple[str, ...]] = ("reativado", "rebaixado", "usado_com_sucesso", "resolvido_sem_pergunta",
                                     "escolha_da_pessoa", "descartado")
#: Dias recentes recalculados a cada passo da retenção: a decisão real e o desfecho chegam depois da gravação.
JANELA_RECALCULO_DIAS: Final = 7

_ID: Final = re.compile(r"^[A-Za-z0-9_.:\-]{1,80}$")
_REF: Final = re.compile(r"^[A-Za-z0-9_.:/#@\-]{1,200}$")


def _id(valor: str | None) -> str | None:
    return valor if valor is not None and _ID.fullmatch(valor) else None


def _ref(valor: str | None) -> str | None:
    return valor if valor is not None and _REF.fullmatch(valor) else None


def _probabilidades(bruto: Mapping[str, float]) -> str | None:
    """Só chaves que são ids opacos e valores numéricos em [0, 1]; o resto é descartado, não gravado."""
    limpo = {k: round(float(v), 6) for k, v in bruto.items()
             if _ID.fullmatch(k) and isinstance(v, (int, float)) and 0.0 <= float(v) <= 1.0}
    return json.dumps(limpo, sort_keys=True) if limpo else None


def _p95(valores: list[float]) -> float | None:
    """Posto mais próximo (p95): sem interpolação, o valor é uma latência que aconteceu de fato."""
    if not valores:
        return None
    ordem = sorted(valores)
    return round(ordem[max(0, math.ceil(0.95 * len(ordem)) - 1)], 3)


class RepositorioDeSombra:
    def __init__(self, db: Database, *, relogio: Callable[[], datetime] = now) -> None:
        self._db = db
        self._relogio = relogio

    # ------------------------------------------------------------------ gravação
    def registrar(self, registro: RegistroDeDecisao) -> int:
        """Grava as linhas de UMA chamada (uma por pergunta). Devolve quantas. O custo e os tokens da chamada ficam só na
        primeira linha dela (as demais levam 0): a soma do dia não conta a mesma chamada N vezes."""
        res = registro.resultado
        if not res.respostas or registro.origem not in ORIGENS:
            return 0
        ts = to_iso(self._relogio())
        chamada = uuid.uuid4().hex[:16]
        run_id, step_id, ref = _ref(registro.run_id), _ref(registro.step_id), _ref(registro.ref)
        # Reverificação B do 31.9 (migração 079): só código de vocabulário fechado; o que vier fora dele vira `outro`.
        privacidade = registro.motivo_privacidade
        if privacidade is not None and privacidade not in MOTIVOS_DE_PRIVACIDADE:
            privacidade = "outro"
        with self._db.tx():
            for i, (pergunta_id, r) in enumerate(res.respostas.items()):
                escolha = None if r.escolha is None else _id(r.escolha)
                motivo = r.fallback_reason or res.fallback_reason
                if r.escolha is not None and escolha is None and motivo is None:
                    motivo = "unknown_choice"      # escolha que não é id opaco: nunca grava, e nunca conta como acerto
                if motivo is not None and motivo not in FALLBACKS:
                    motivo = "parse"
                if motivo is not None:
                    escolha = None
                self._db.execute(
                    "INSERT INTO decisao_fechada_sombra(ts, chamada, origem, classe, modo, pergunta_id, escolha,"
                    " probabilidades, confianca, usd, tokens, ms, fallback_reason, run_id, step_id, ref,"
                    " motivo_privacidade) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (ts, chamada, registro.origem, registro.classe, registro.modo,
                     _id(pergunta_id) or "invalido", escolha, _probabilidades(r.probabilidades),
                     None if r.confianca is None else round(float(r.confianca), 6),
                     float(res.usd) if i == 0 else 0.0, int(res.tokens) if i == 0 else 0, float(res.ms),
                     motivo, run_id, step_id, ref, privacidade if motivo == "privacidade" else None))
        return len(res.respostas)

    # ------------------------------------------------------------------ preenchimentos posteriores (31.8 e 31.9)
    def casar_decisao_real(self, decisoes: Mapping[str, str], *, ref: str | None = None,
                           step_id: str | None = None) -> int:
        """Preenche `decisao_real` (id opaco do caminho ATUAL) por `pergunta_id`, nas linhas do `ref` ou do `step_id` que
        ainda não têm. Devolve quantas linhas casaram. Sem `ref` nem `step_id` é erro de quem chama, não casamento amplo."""
        chave, valor = self._chave(ref, step_id)
        casadas = 0
        with self._db.tx():
            for pergunta_id, decisao in decisoes.items():
                if _id(decisao) is None or _id(pergunta_id) is None:
                    raise ValueError("decisao real e pergunta devem ser ids opacos")
                casadas += int(self._db.execute(
                    f"UPDATE decisao_fechada_sombra SET decisao_real=? WHERE {chave}=? AND pergunta_id=?"
                    " AND decisao_real IS NULL", (decisao, valor, pergunta_id)).rowcount or 0)
        return casadas

    def anotar_ambiguos(self, ambiguos: int, *, ref: str) -> int:
        """Preenche `ambiguos` (etapas da RESOLVE que terminaram em AMBIGUOUS, RA-2) nas linhas da INTENÇÃO do `ref` que
        ainda não têm. Só número: nada do comando. Devolve quantas linhas anotou."""
        if isinstance(ambiguos, bool) or not isinstance(ambiguos, int) or ambiguos < 0:
            raise ValueError("ambiguos deve ser um inteiro >= 0")
        with self._db.tx():
            return int(self._db.execute(
                "UPDATE decisao_fechada_sombra SET ambiguos=? WHERE ref=? AND origem='intencao' AND ambiguos IS NULL",
                (ambiguos, ref)).rowcount or 0)

    def casar_desfecho(self, desfecho: str, *, ref: str | None = None, step_id: str | None = None,
                       pergunta_id: str | None = None) -> int:
        """Preenche `desfecho` (vocabulário `DESFECHOS`) nas linhas do `ref` ou `step_id` que ainda não têm; `pergunta_id`
        restringe a uma pergunta. Devolve quantas linhas casaram."""
        if desfecho not in DESFECHOS:
            raise ValueError("desfecho fora do vocabulario")
        chave, valor = self._chave(ref, step_id)
        sql = f"UPDATE decisao_fechada_sombra SET desfecho=? WHERE {chave}=? AND desfecho IS NULL"
        params: tuple[object, ...] = (desfecho, valor)
        if pergunta_id is not None:
            sql += " AND pergunta_id=?"
            params += (pergunta_id,)
        with self._db.tx():
            return int(self._db.execute(sql, params).rowcount or 0)

    @staticmethod
    def _chave(ref: str | None, step_id: str | None) -> tuple[str, str]:
        if ref is not None:
            return "ref", ref
        if step_id is not None:
            return "step_id", step_id
        raise ValueError("informe ref ou step_id")

    # ------------------------------------------------------------------ agregado diário e retenção
    def agregar(self, desde: str, ate: str) -> int:
        """Recalcula `decisao_fechada_diario` POR INTEIRO para os dias de [desde, ate) (ISO-8601) que têm linhas. Só chamar
        com dias cujas linhas estão todas aqui (a purga leva dias inteiros). Devolve as linhas de agregado gravadas."""
        filtro, params = "ts >= ? AND ts < ?", (desde, ate)
        grupos = self._db.query(
            "SELECT SUBSTR(ts,1,10) AS dia, origem, pergunta_id, COUNT(*) AS n,"
            " SUM(CASE WHEN decisao_real IS NOT NULL THEN 1 ELSE 0 END) AS com_real,"
            " SUM(CASE WHEN escolha IS NOT NULL AND escolha = decisao_real THEN 1 ELSE 0 END) AS concordancia,"
            " SUM(CASE WHEN fallback_reason IS NULL AND escolha IS NOT NULL AND escolha <> ? THEN 1 ELSE 0 END) AS acima,"
            " SUM(CASE WHEN fallback_reason IS NULL AND escolha IS NOT NULL AND escolha <> ? AND decisao_real IS NOT NULL"
            " AND escolha <> decisao_real THEN 1 ELSE 0 END) AS errado,"
            " SUM(CASE WHEN fallback_reason IS NOT NULL THEN 1 ELSE 0 END) AS fallbacks,"
            " SUM(usd) AS usd, SUM(tokens) AS tokens FROM decisao_fechada_sombra"
            f" WHERE {filtro} GROUP BY SUBSTR(ts,1,10), origem, pergunta_id",
            (ID_NENHUMA, ID_NENHUMA, *params))
        latencias: dict[tuple[str, str, str], list[float]] = {}
        for r in self._db.query(
                "SELECT SUBSTR(ts,1,10) AS dia, origem, pergunta_id, ms FROM decisao_fechada_sombra"
                f" WHERE {filtro} AND ms > 0", params):
            latencias.setdefault((str(r["dia"]), str(r["origem"]), str(r["pergunta_id"])), []).append(float(r["ms"]))
        agora = to_iso(self._relogio())
        with self._db.tx():
            for dia in sorted({str(g["dia"]) for g in grupos}):
                self._db.execute("DELETE FROM decisao_fechada_diario WHERE day=?", (dia,))
            for g in grupos:
                chave = (str(g["dia"]), str(g["origem"]), str(g["pergunta_id"]))
                self._db.execute(
                    "INSERT INTO decisao_fechada_diario(day, origem, pergunta_id, n, com_decisao_real, concordancia,"
                    " acima_do_limiar, aceite_errado, fallbacks, usd, tokens, ms_p95, computed_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (*chave, int(g["n"]), int(g["com_real"] or 0), int(g["concordancia"] or 0), int(g["acima"] or 0),
                     int(g["errado"] or 0), int(g["fallbacks"] or 0), round(float(g["usd"] or 0), 6),
                     int(g["tokens"] or 0), _p95(latencias.get(chave, [])), agora))
        return len(grupos)

    def aplicar_retencao(self, retencao_dias: float) -> int:
        """Passo da retenção geral: (1) recalcula os dias recentes (a decisão real chega depois); (2) agrega a ÚLTIMA vez os
        dias que vão sumir e só então purga as linhas de dias inteiros anteriores ao corte. O agregado fica. Devolve as linhas
        purgadas."""
        agora = self._relogio()
        amanha = to_iso(agora + timedelta(days=1)).split("T")[0]
        recente = to_iso(agora - timedelta(days=JANELA_RECALCULO_DIAS)).split("T")[0]
        self.agregar(recente, amanha)
        corte = to_iso(agora - timedelta(days=retencao_dias)).split("T")[0]     # dia inteiro: `ts < 'AAAA-MM-DD'`
        antigo = self._db.scalar("SELECT MIN(ts) FROM decisao_fechada_sombra WHERE ts < ?", (corte,))
        if antigo is None:
            return 0
        self.agregar(str(antigo)[:10], corte)
        with self._db.tx():
            return int(self._db.execute("DELETE FROM decisao_fechada_sombra WHERE ts < ?", (corte,)).rowcount or 0)

    # ------------------------------------------------------------------ leitura
    def diario(self, desde_dia: str, ate_dia: str | None = None) -> list[Mapping[str, object]]:
        """Agregado diário de [desde_dia, ate_dia] (AAAA-MM-DD), para o 31.7 (limiares) e o painel."""
        sql, params = "SELECT * FROM decisao_fechada_diario WHERE day >= ?", (desde_dia,)
        if ate_dia is not None:
            sql, params = sql + " AND day <= ?", (desde_dia, ate_dia)
        return [dict(r) for r in self._db.query(sql + " ORDER BY day, origem, pergunta_id", params)]


def observador_de_sombra(repositorio: RepositorioDeSombra) -> Observador:
    """O `observador` da porta que grava a sombra. A porta já engole a falha do observador (medir nunca derruba o trabalho);
    aqui ela só ganha um log sem estado."""
    def _gravar(registro: RegistroDeDecisao) -> None:
        try:
            repositorio.registrar(registro)
        except Exception:
            log.warning("decisao_fechada: não foi possível gravar a sombra")
            raise
    return _gravar


__all__ = ["DESFECHOS", "JANELA_RECALCULO_DIAS", "RepositorioDeSombra", "observador_de_sombra"]
