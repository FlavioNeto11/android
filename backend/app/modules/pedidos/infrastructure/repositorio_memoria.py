"""SQL da memória, das observações e dos relatórios do pedido (070; docs/design/pedidos-laco.md, "28.7").

Só consultas e escritas pontuais, no estilo de `repositorio.py`: este módulo não abre transação (quem precisa de
atomicidade, como o fechamento da ocorrência, envolve em `Lideranca.cercada`) e as REGRAS ficam no domínio
(`domain/memoria.py`, `observacao.py`, `relatorio.py`). Compatível com SQLite e PostgreSQL (`ON CONFLICT DO NOTHING` sem
alvo cobre qualquer `UNIQUE`).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.db import Database, Row
from app.modules.pedidos.domain.memoria import Entrada
from app.modules.pedidos.infrastructure.repositorio import novo_id


@dataclass(frozen=True)
class NovaObservacao:
    pedido_id: str
    pedido_versao: int
    ocorrencia_id: str
    run_id: str | None
    step_id: str | None
    alvo: str
    nome: str
    tipo: str
    situacao: str
    valor: str | None
    fonte: str
    trecho: str | None
    sha256: str | None
    capturado_em: str


def _entrada(r: Row) -> Entrada:
    return Entrada(chave=r["chave"], tipo=r["tipo"], valor=r["valor"], versao=int(r["versao"]),
                   atualizada_em=r["atualizada_em"], ocorrencia_id=r["ocorrencia_id"], resolvida=bool(r["resolvida"]))


class RepositorioDeMemoria:
    def __init__(self, db: Database):
        self.db = db

    # ------------------------------------------------------------------ memória
    def entrada(self, pedido_id: str, chave: str) -> Entrada | None:
        r = self.db.one("SELECT * FROM pedido_memoria WHERE pedido_id=? AND chave=?", (pedido_id, chave))
        return _entrada(r) if r is not None else None

    def entradas(self, pedido_id: str) -> list[Entrada]:
        return [_entrada(r) for r in self.db.query(
            "SELECT * FROM pedido_memoria WHERE pedido_id=? ORDER BY tipo, chave", (pedido_id,))]

    def pendencias_abertas(self, pedido_id: str) -> list[str]:
        return [r["valor"] for r in self.db.query(
            "SELECT valor FROM pedido_memoria WHERE pedido_id=? AND tipo='pendencia' AND resolvida=0 ORDER BY chave",
            (pedido_id,))]

    def gravar_entrada(self, pedido_id: str, antes: Entrada | None, nova: Entrada) -> bool:
        """Grava a entrada que `domain.memoria.escrever` decidiu. CAS pela versão lida: se outro escritor mudou a chave
        entre a leitura e aqui, devolve `False` e quem chama relê (o valor dele vale; o nosso se reescreve por cima)."""
        if antes is None:
            cur = self.db.execute(
                "INSERT INTO pedido_memoria(id, pedido_id, chave, tipo, valor, versao, ocorrencia_id, resolvida,"
                " atualizada_em) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
                (novo_id("mem"), pedido_id, nova.chave, nova.tipo, nova.valor, nova.versao, nova.ocorrencia_id,
                 1 if nova.resolvida else 0, nova.atualizada_em))
        else:
            cur = self.db.execute(
                "UPDATE pedido_memoria SET valor=?, versao=?, ocorrencia_id=?, resolvida=?, atualizada_em=?"
                " WHERE pedido_id=? AND chave=? AND versao=?",
                (nova.valor, nova.versao, nova.ocorrencia_id, 1 if nova.resolvida else 0, nova.atualizada_em,
                 pedido_id, nova.chave, antes.versao))
        return (cur.rowcount or 0) == 1

    # ------------------------------------------------------------------ observações
    def saidas_da_execucao(self, run_id: str) -> list[Row]:
        """O que a execução LEU entre etapas (`step_outputs`, 056), com a origem; vazio se a purga já apagou a execução."""
        return self.db.query(
            "SELECT o.instance_id AS alvo, so.step_id, so.name, so.value, so.value_kind, so.created_at,"
            " s.title AS etapa, COALESCE(a.name, so.app_id) AS app"
            " FROM step_outputs so JOIN objectives o ON o.id = so.objective_id JOIN steps s ON s.id = so.step_id"
            " LEFT JOIN apps a ON a.id = so.app_id WHERE so.run_id=? ORDER BY o.instance_id, s.seq, so.name",
            (run_id,))

    def inserir_observacoes(self, observacoes: Sequence[NovaObservacao]) -> int:
        """Reentrante: `UNIQUE (ocorrencia_id, alvo, nome)` + `ON CONFLICT DO NOTHING`. Devolve quantas entraram."""
        gravadas = 0
        for x in observacoes:
            cur = self.db.execute(
                "INSERT INTO pedido_observacoes(id, pedido_id, pedido_versao, ocorrencia_id, run_id, step_id, alvo,"
                " nome, tipo, situacao, valor, fonte, trecho, sha256, capturado_em)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
                (novo_id("obs"), x.pedido_id, x.pedido_versao, x.ocorrencia_id, x.run_id, x.step_id, x.alvo, x.nome,
                 x.tipo, x.situacao, x.valor, x.fonte, x.trecho, x.sha256, x.capturado_em))
            gravadas += cur.rowcount or 0
        return gravadas

    def observacoes(self, pedido_id: str, *, limite: int = 200, antes_de: str | None = None) -> list[Row]:
        """Da mais nova para a mais velha; `antes_de` é o `capturado_em` do último item da página anterior."""
        if antes_de is None:
            return self.db.query("SELECT * FROM pedido_observacoes WHERE pedido_id=? ORDER BY capturado_em DESC, id DESC"
                                 " LIMIT ?", (pedido_id, limite))
        return self.db.query("SELECT * FROM pedido_observacoes WHERE pedido_id=? AND capturado_em < ?"
                             " ORDER BY capturado_em DESC, id DESC LIMIT ?", (pedido_id, antes_de, limite))

    def todas_as_observacoes(self, pedido_id: str) -> list[Row]:
        return self.db.query("SELECT * FROM pedido_observacoes WHERE pedido_id=? ORDER BY capturado_em, id", (pedido_id,))

    def ocorrencias(self, pedido_id: str) -> list[Row]:
        return self.db.query("SELECT id, previsto_para, estado, motivo, custo_usd, origem FROM pedido_ocorrencias"
                             " WHERE pedido_id=? ORDER BY previsto_para, id", (pedido_id,))

    # ------------------------------------------------------------------ relatórios
    def relatorio_de_encerramento(self, pedido_id: str) -> Row | None:
        return self.db.one("SELECT * FROM pedido_relatorios WHERE pedido_id=? AND gatilho='encerramento'", (pedido_id,))

    def relatorios(self, pedido_id: str, *, limite: int = 50, antes_de: int | None = None) -> list[Row]:
        """Do mais novo para o mais velho; `antes_de` é a `sequencia` do último item da página anterior."""
        if antes_de is None:
            return self.db.query("SELECT * FROM pedido_relatorios WHERE pedido_id=? ORDER BY sequencia DESC LIMIT ?",
                                 (pedido_id, limite))
        return self.db.query("SELECT * FROM pedido_relatorios WHERE pedido_id=? AND sequencia < ?"
                             " ORDER BY sequencia DESC LIMIT ?", (pedido_id, antes_de, limite))

    def relatorio(self, relatorio_id: str) -> Row | None:
        return self.db.one("SELECT * FROM pedido_relatorios WHERE id=?", (relatorio_id,))

    def inserir_relatorio(self, *, pedido_id: str, gatilho: str, pedido_versao: int, periodo_de: str | None,
                          periodo_ate: str, conteudo: str, sha256: str, gerado_em: str, gerado_por: str = "deterministico",
                          resumo_texto: str | None = None, resumo_por: str | None = None, custo_usd: float = 0.0) -> Row | None:
        """A próxima `sequencia` do pedido. `None` quando perdeu a corrida de uma chave única (o de encerramento já
        existe, ou outro escritor tomou a mesma sequência): quem chama relê o do encerramento ou tenta de novo."""
        sequencia = int(self.db.scalar("SELECT COALESCE(MAX(sequencia), 0) FROM pedido_relatorios WHERE pedido_id=?",
                                       (pedido_id,)) or 0) + 1
        rid = novo_id("rel")
        cur = self.db.execute(
            "INSERT INTO pedido_relatorios(id, pedido_id, sequencia, gatilho, pedido_versao, periodo_de, periodo_ate,"
            " gerado_por, conteudo, sha256, resumo_texto, resumo_por, custo_usd, gerado_em)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
            (rid, pedido_id, sequencia, gatilho, pedido_versao, periodo_de, periodo_ate, gerado_por, conteudo, sha256,
             resumo_texto, resumo_por, custo_usd, gerado_em))
        return self.relatorio(rid) if (cur.rowcount or 0) == 1 else None
