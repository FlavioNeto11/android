"""A caixa de avisos do pedido (item 28.9; migração 072 `pedido_avisos`; adendo v0.45 de `docs/api-contract.md`).

O PONTO ÚNICO por onde todo `pedido.aviso` passa, venha do laço (28.4 a 28.7: pausa, incerta, orçamento, relatório) ou da
API (pausa, encerramento e perdida vistos na mudança de estado):

    registrar  grava a linha com `INSERT ... ON CONFLICT DO NOTHING` na `chave_dedupe` e SÓ ENTÃO emite o evento, e só se
               a linha é nova. Dois caminhos que dizem o mesmo fato com a mesma chave dão uma linha e um evento.
    listar     a caixa (`GET /api/pedidos/avisos`), com `lido`, `requer_pessoa` e `pedido_id`.
    ler        marca `lido_em` (idempotente: o que já estava lido não muda de data) e devolve as contagens.
    nao_lidos  o contador "avisos não lidos". Conta só os informativos (`requer_pessoa=0`): o que pede uma pessoa mora nas
               Pendências, e o pedido que espera já é UM item lá (ADR-062: não contar duas vezes).

Falha ao emitir nunca desfaz a linha: o banco é a fonte da verdade, e a caixa se refaz pelo `resync`.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence

from app.db import Database, Row, dumps, loads
from app.modules.pedidos.domain.autor import DONO
from app.modules.pedidos.domain.avisos import NIVEIS, TIPOS, id_do_aviso
from app.util import to_iso

log = logging.getLogger("poc.pedidos")

JsonObject = dict[str, object]

#: Teto de ids por chamada de `ler` (o contrato diz ≤ 200).
MAXIMO_DE_IDS = 200


def _autoria(p: Row) -> JsonObject:
    """28.31 F2a: o aviso diz se o pedido é do dono (aí o canal pode mostrar o título) e se é do lote de uma frente (aí
    o aviso vai à janela de rotina). Pedido anterior à migração 106 não é de nenhum dos dois."""
    return {"criado_pelo_dono": p["criado_por_tipo"] == DONO, "de_lote": bool(p["lote"])}


class CaixaDeAvisos:
    def __init__(self, db: Database, emitir: Callable[..., object], agora: Callable[[], object]):
        self.db = db
        self.emitir = emitir
        self.agora = agora

    def _agora(self) -> str:
        return to_iso(self.agora())                                          # type: ignore[arg-type]

    # ------------------------------------------------------------------ gravar e emitir
    def registrar(self, *, pedido_id: str, tipo: str, nivel: str | None, mensagem: str, chave: str,
                  ocorrencia_id: str | None = None, dados: Mapping[str, object] | None = None,
                  requer_pessoa: bool | None = None) -> JsonObject | None:
        """Devolve o `AvisoDTO` emitido, ou `None` (o fato já estava gravado, ou o pedido não existe mais). `nivel` e
        `requer_pessoa` omitidos vêm do vocabulário do tipo."""
        if tipo not in TIPOS:
            raise ValueError(f"tipo de aviso desconhecido: {tipo!r}")
        nivel_padrao, pessoa_padrao = TIPOS[tipo]
        nivel = nivel or nivel_padrao
        if nivel not in NIVEIS:
            raise ValueError(f"nível de aviso inválido: {nivel!r}")
        pessoa = pessoa_padrao if requer_pessoa is None else requer_pessoa
        pedido = self.db.one("SELECT titulo, criado_por_tipo, lote FROM pedidos WHERE id=?", (pedido_id,))
        if pedido is None:
            return None
        if self.db.one("SELECT 1 AS x FROM pedido_avisos WHERE chave_dedupe=?", (chave,)) is not None:
            return None                         # a volta do laço repete a pergunta: sem escrita enquanto nada mudou
        aviso_id, criado_em = id_do_aviso(chave), self._agora()
        cur = self.db.execute(
            "INSERT INTO pedido_avisos(id, pedido_id, ocorrencia_id, tipo, nivel, mensagem, dados, requer_pessoa,"
            " chave_dedupe, criado_em) VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT (chave_dedupe) DO NOTHING",
            (aviso_id, pedido_id, ocorrencia_id, tipo, nivel, mensagem, dumps(dict(dados or {})), 1 if pessoa else 0,
             chave, criado_em))
        if (cur.rowcount or 0) != 1:
            return None
        dto: JsonObject = {"id": aviso_id, "pedido_id": pedido_id, "pedido_titulo": pedido["titulo"], **_autoria(pedido),
                           "ocorrencia_id": ocorrencia_id, "tipo": tipo, "nivel": nivel, "mensagem": mensagem,
                           "dados": dict(dados or {}), "requer_pessoa": pessoa, "criado_em": criado_em, "lido_em": None}
        try:
            self.emitir("pedido.aviso", mensagem, level=nivel, data={"aviso": dto})
        except Exception:  # noqa: BLE001
            log.exception("pedidos: evento do aviso %s", tipo)
        return dto

    def registrar_dto(self, aviso: Mapping[str, object]) -> JsonObject | None:
        """O `AvisoDTO` montado pelo laço (`domain/tentativas.py::aviso`): o `id` dele é a chave de deduplicação."""
        return self.registrar(
            pedido_id=str(aviso["pedido_id"]), tipo=str(aviso["tipo"]), nivel=str(aviso["nivel"]),
            mensagem=str(aviso["mensagem"]), chave=str(aviso["id"]),
            ocorrencia_id=str(aviso["ocorrencia_id"]) if aviso.get("ocorrencia_id") else None,
            dados=aviso.get("dados") if isinstance(aviso.get("dados"), Mapping) else None,   # type: ignore[arg-type]
            requer_pessoa=bool(aviso.get("requer_pessoa")))

    # ------------------------------------------------------------------ ler
    @staticmethod
    def dto(r: Row) -> JsonObject:
        return {"id": r["id"], "pedido_id": r["pedido_id"], "pedido_titulo": r["pedido_titulo"], **_autoria(r),
                "ocorrencia_id": r["ocorrencia_id"], "tipo": r["tipo"], "nivel": r["nivel"], "mensagem": r["mensagem"],
                "dados": loads(r["dados"], {}) or {}, "requer_pessoa": bool(r["requer_pessoa"]),
                "criado_em": r["criado_em"], "lido_em": r["lido_em"]}

    def listar(self, *, pedido_id: str | None, requer_pessoa: bool | None, lido: bool | None, limite: int, inicio: int
               ) -> tuple[list[JsonObject], bool]:
        """Da mais nova para a mais velha. O segundo valor diz se há mais uma página depois desta."""
        onde, params = ["1=1"], []
        if pedido_id:
            onde.append("a.pedido_id=?")
            params.append(pedido_id)
        if requer_pessoa is not None:
            onde.append("a.requer_pessoa=?")
            params.append(1 if requer_pessoa else 0)
        if lido is not None:
            onde.append("a.lido_em IS NOT NULL" if lido else "a.lido_em IS NULL")
        linhas = self.db.query(
            "SELECT a.*, p.titulo AS pedido_titulo, p.criado_por_tipo, p.lote FROM pedido_avisos a"
            " JOIN pedidos p ON p.id = a.pedido_id"
            f" WHERE {' AND '.join(onde)} ORDER BY a.criado_em DESC, a.id DESC LIMIT ? OFFSET ?",
            (*params, limite + 1, inicio))
        return [self.dto(r) for r in linhas[:limite]], len(linhas) > limite

    def nao_lidos(self, pedido_id: str | None = None) -> int:
        """Avisos informativos ainda não lidos (de um pedido, ou de todos)."""
        if pedido_id:
            return int(self.db.scalar("SELECT COUNT(*) FROM pedido_avisos WHERE lido_em IS NULL AND requer_pessoa=0"
                                      " AND pedido_id=?", (pedido_id,)) or 0)
        return int(self.db.scalar("SELECT COUNT(*) FROM pedido_avisos WHERE lido_em IS NULL AND requer_pessoa=0") or 0)

    def nao_lidos_por_pedido(self) -> dict[str, int]:
        return {r["pedido_id"]: int(r["n"]) for r in self.db.query(
            "SELECT pedido_id, COUNT(*) AS n FROM pedido_avisos WHERE lido_em IS NULL AND requer_pessoa=0"
            " GROUP BY pedido_id")}

    # ------------------------------------------------------------------ marcar como lido
    def inexistentes(self, ids: Sequence[str]) -> list[str]:
        if not ids:
            return []
        achados = {r["id"] for r in self.db.query(
            f"SELECT id FROM pedido_avisos WHERE id IN ({','.join('?' for _ in ids)})", tuple(ids))}  # noqa: S608
        return [i for i in ids if i not in achados]

    def ler(self, *, ids: Sequence[str] | None, todos: bool, pedido_id: str | None = None) -> int:
        """Marca `lido_em` no que ainda não estava lido e devolve quantos mudaram. `todos` marca os informativos (os
        mesmos da caixa) e pode se restringir a um pedido; `ids` marca exatamente os pedidos. Repetir devolve 0."""
        agora = self._agora()
        mudou = 0
        if ids:
            lista = list(dict.fromkeys(ids))
            cur = self.db.execute(
                f"UPDATE pedido_avisos SET lido_em=? WHERE lido_em IS NULL AND id IN ({','.join('?' for _ in lista)})",  # noqa: S608
                (agora, *lista))
            mudou += cur.rowcount or 0
        if todos:
            sql, params = "UPDATE pedido_avisos SET lido_em=? WHERE lido_em IS NULL AND requer_pessoa=0", [agora]
            if pedido_id:
                sql += " AND pedido_id=?"
                params.append(pedido_id)
            mudou += self.db.execute(sql, tuple(params)).rowcount or 0
        return mudou


__all__ = ["CaixaDeAvisos", "MAXIMO_DE_IDS"]
