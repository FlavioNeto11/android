"""31.190: o passo da curadoria que leva o fato confirmado da pesquisa de uma operação ENCERRADA ao Livro, como
candidata do escritor (`domain/fatos_da_operacao.py`). Sem IA; idempotente: o mesmo fato no mesmo app cai no mesmo item
(`LearningService.propor` devolve o vivo), e o fato que uma pessoa vetou não volta (o veto do sistema recusa).

Só lê a memória da operação (`pedido_memoria` com `operacao_id`, 125), as observações das fontes e as marcas dos alvos
(124); escreve só pelo serviço do Livro. Olha as operações encerradas na janela (`JANELA_DIAS`): a mais velha já foi
curada, e reler tudo a cada passo só gastaria banco.

Curadoria por operação: `da_operacao` roda o mesmo passo para UMA operação assim que ela encerra (o laço da curadoria
ouve `operacao.encerrada`) e devolve o relatório: as candidatas que nasceram, as que já estavam no Livro, as recusadas
por motivo fechado (`MotivoDaRecusa`), as vetadas e os fatos do Livro do mesmo app cujo frescor venceu. Só ids e
contagens: o texto do fato fica no Livro.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from app.db import Database, loads
from app.modules.learning.application.ports import RepositorioDeAprendizado
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.ciclo import ErroDeAprendizado
from app.modules.learning.domain.fatos_da_operacao import (PREFIXO_DO_FATO, FatoDaOperacao, candidata, recusa,
                                                           vencida)
from app.modules.learning.domain.reaproveitamento_da_pesquisa import CHAVE_DA_LEITURA, assunto_da_leitura
from app.modules.learning.domain.vocabulario import SourceKind
from app.modules.skills.domain.document import JsonObject, JsonValue

log = logging.getLogger(__name__)

#: As operações encerradas há até tantos dias entram no passo.
JANELA_DIAS = 7


def _iso(agora: datetime) -> str:
    return agora.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class FatosDaOperacaoParaOLivro:
    """`PassoDeCuradoria` (`application/ports.py`)."""

    nome = "fatos_da_operacao"

    def __init__(self, servico: LearningService, repo: RepositorioDeAprendizado, db: Database) -> None:
        self._servico = servico
        self._repo = repo
        self.db = db

    def _operacoes(self, desde: str, operacao: str | None = None) -> list[tuple[str, str, str]]:
        """`(operacao, pacote, assunto)` das encerradas na janela (ou só a `operacao`, se encerrada). Sem a 124, nada."""
        if "operacoes" not in self.db.tables() or "operacao_id" not in self.db.columns("pedido_memoria"):
            return []
        filtro, params = ((" AND o.id=?", (operacao,)) if operacao is not None
                          else (" AND o.finished_at >= ?", (desde,)))
        return [(str(r["id"]), str(r["pacote"] or ""), str(r["assunto"] or "").strip() or self._assunto_da_leitura(str(r["id"])))
                for r in self.db.query(
                    "SELECT o.id, a.package AS pacote, o.assunto FROM operacoes o LEFT JOIN apps a ON a.id = o.app_id"
                    f" WHERE o.finished_at IS NOT NULL{filtro} ORDER BY o.finished_at, o.id", params)]

    def _assunto_da_leitura(self, operacao: str) -> str:
        """31.248: a operação sem assunto guardado pesquisou com o da leitura do alvo; o fato que ela deixa leva o MESMO
        assunto ao Livro (a mesma regra, sobre a mesma leitura), para a próxima operação daquela publicação o reusar."""
        valor = self.db.scalar("SELECT valor FROM pedido_memoria WHERE operacao_id=? AND chave=?", (operacao, CHAVE_DA_LEITURA))
        return assunto_da_leitura(str(valor) if valor else None) or ""

    def _dominios(self, evidencia: object) -> tuple[str, ...]:
        ids = [str(i) for i in (evidencia if isinstance(evidencia, list) else [])][:20]
        if not ids:
            return ()
        urls = [str(r["valor"] or "") for r in self.db.query(
            f"SELECT valor FROM pedido_observacoes WHERE id IN ({','.join('?' * len(ids))}) AND tipo='url'", tuple(ids))]
        return tuple(sorted({urlparse(u).netloc.lower().removeprefix("www.") for u in urls if urlparse(u).netloc}))

    def _fatos(self, operacao: str, pacote: str, assunto: str) -> list[FatoDaOperacao]:
        execucoes = tuple(str(r["id"]) for r in self.db.query(
            "SELECT id FROM runs WHERE operacao_id=? ORDER BY id", (operacao,)))
        saida = []
        for m in self.db.query("SELECT chave, tipo, valor, confianca, evidencia, frescor_ate FROM pedido_memoria"
                               " WHERE operacao_id=? AND chave LIKE ? AND resolvida=0 ORDER BY chave",
                               (operacao, PREFIXO_DO_FATO + "%")):
            usado = int(self.db.scalar("SELECT COUNT(*) FROM operacao_alvos WHERE operacao_id=? AND marcas LIKE ?",
                                       (operacao, f'%"fato:{m["chave"]}"%')) or 0)
            saida.append(FatoDaOperacao(
                operacao_id=operacao, chave=str(m["chave"]), tipo=str(m["tipo"]), texto=str(m["valor"] or ""),
                confianca=str(m["confianca"]), frescor_ate=str(m["frescor_ate"]) if m["frescor_ate"] else None,
                pacote=pacote, assunto=" ".join(assunto.split()), dominios=self._dominios(loads(m["evidencia"], [])),
                execucoes=execucoes, usado_em=usado))
        return saida

    def executar(self, agora: datetime) -> int:
        """Quantas candidatas NASCERAM neste passo (o fato que já tem item não conta)."""
        hoje = _iso(agora)
        total = 0
        for op, pacote, assunto in self._operacoes(_iso(agora - timedelta(days=JANELA_DIAS))):
            nascidas = self._curar(op, pacote, assunto, hoje)["nascidas"]
            total += len(nascidas) if isinstance(nascidas, list) else 0
        return total

    def da_operacao(self, operacao: str, agora: datetime) -> JsonObject | None:
        """O passo para UMA operação encerrada, com o relatório; None quando ela não existe ou não encerrou."""
        hoje = _iso(agora)
        achada = self._operacoes(hoje, operacao)
        if not achada:
            return None
        _, pacote, assunto = achada[0]
        return {"operacao": operacao, "app": pacote, **self._curar(operacao, pacote, assunto, hoje),
                "vencidas_no_livro": self._vencidas(pacote, hoje)}

    def _curar(self, operacao: str, pacote: str, assunto: str, hoje: str) -> JsonObject:
        nascidas: list[str] = []
        ja_no_livro = vetadas = 0
        recusadas: dict[str, int] = {}
        for fato in self._fatos(operacao, pacote, assunto):
            motivo = recusa(fato, hoje)
            novo = None if motivo is not None else candidata(fato, hoje)
            if novo is None:
                chave = motivo.value if motivo is not None else "outro"
                recusadas[chave] = recusadas.get(chave, 0) + 1
                continue
            antes = self._repo.item_vivo(novo)
            try:
                item = self._servico.propor(novo)
            except ErroDeAprendizado as exc:            # vetado por uma pessoa, ou texto com cara de credencial
                log.info("aprendizado: o fato %s da operação %s não foi ao Livro (%s)", fato.chave, operacao,
                         getattr(exc, "code", type(exc).__name__))
                vetadas += 1
                continue
            if antes is None and item is not None:
                nascidas.append(item.id)
            else:
                ja_no_livro += 1
        return {"nascidas": list[JsonValue](nascidas), "ja_no_livro": ja_no_livro,
                "recusadas": dict[str, JsonValue](sorted(recusadas.items())), "vetadas": vetadas}

    def _vencidas(self, pacote: str, hoje: str) -> list[JsonValue]:
        """Os itens vivos do Livro que nasceram de um fato (do mesmo app) cujo frescor venceu."""
        if not pacote:
            return []
        return [str(r["id"]) for r in self.db.query(
            "SELECT id, provenance FROM learning_items WHERE source_kind=? AND scope_app=?"
            " AND state IN ('candidate', 'validated', 'published') ORDER BY id",
            (SourceKind.FATO_DA_OPERACAO.value, pacote)) if vencida(loads(r["provenance"], {}) or {}, hoje)]


__all__ = ["JANELA_DIAS", "FatosDaOperacaoParaOLivro"]
