"""A tabela `portal_contatos` (migração 107) e o sal do portal em `settings` (29.77, ADR-075).

Só SQL; a decisão do que fazer (taxa, teto, reenvio) mora na aplicação. Horários em ISO UTC (`util.to_iso`), que
ordenam como texto nos dois bancos.
"""
from __future__ import annotations

import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.db import Database, dumps, loads
from app.util import to_iso

#: A chave do sal em `settings`. Nasce na primeira leitura e não gira: girar invalidaria os tokens das páginas abertas
#: e zeraria a taxa por cliente. Não é segredo de credencial (não abre nada), mas também não sai do banco.
CHAVE_DO_SAL = "portal.sal"

#: O prazo escrito no aviso de privacidade da página. Mudar aqui exige mudar o texto da página e o ADR-075.
RETENCAO_DIAS = 180


@dataclass(frozen=True, slots=True)
class ContatoGuardado:
    id: int
    nome: str
    empresa: str
    telefone: str
    mensagem: str
    estado: str
    tentativas: int


@dataclass(frozen=True, slots=True)
class ContatoAchado:
    """O que a busca da exclusão (29.83) lê: o telefone só para comparar, nunca para mostrar."""
    id: int
    criado_em: str
    estado: str
    telefone: str


class ContatosSql:
    def __init__(self, db: Database) -> None:
        self.db = db

    def sal(self) -> bytes:
        """O sal da instalação. Duas réplicas que sobem juntas convergem no mesmo valor: quem perde o `INSERT` lê o do
        outro."""
        valor = loads(self.db.scalar("SELECT value FROM settings WHERE key=?", (CHAVE_DO_SAL,)), None)
        if not valor:
            self.db.execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING",
                            (CHAVE_DO_SAL, dumps(secrets.token_hex(32))))
            valor = loads(self.db.scalar("SELECT value FROM settings WHERE key=?", (CHAVE_DO_SAL,)), None)
        return bytes.fromhex(str(valor))

    def gravar(self, *, nome: str, empresa: str, telefone: str, mensagem: str, cliente_hash: str, agora: datetime,
               estado: str = "pendente", motivo: str | None = None) -> int:
        quando = to_iso(agora)
        return int(self.db.inserted_id(
            "INSERT INTO portal_contatos(criado_em, nome, empresa, telefone, mensagem, cliente_hash, estado, "
            "atualizado_em, motivo) VALUES (?,?,?,?,?,?,?,?,?)",
            (quando, nome, empresa, telefone, mensagem, cliente_hash, estado, quando, motivo)))

    def do_cliente_desde(self, cliente_hash: str, desde: datetime) -> int:
        return int(self.db.scalar(
            "SELECT COUNT(*) AS n FROM portal_contatos WHERE cliente_hash=? AND criado_em>=?",
            (cliente_hash, to_iso(desde))) or 0)

    def guardados_desde(self, desde: datetime) -> int:
        """Quantos entraram na tabela desde `desde`, descartados fora: é a conta do teto diário."""
        return int(self.db.scalar(
            "SELECT COUNT(*) AS n FROM portal_contatos WHERE criado_em>=? AND estado<>'descartado'",
            (to_iso(desde),)) or 0)

    def entregues_desde(self, desde: datetime) -> int:
        """Quantos foram entregues à Canais desde `desde`: é a conta do teto de avisos por hora."""
        return int(self.db.scalar(
            "SELECT COUNT(*) AS n FROM portal_contatos WHERE estado='entregue' AND atualizado_em>=?",
            (to_iso(desde),)) or 0)

    def retidos_que_chegaram_entre(self, inicio: datetime, fim: datetime) -> int:
        """Os contatos que CHEGARAM em `[inicio, fim)` e bateram no teto de avisos por hora (motivo `teto_por_hora`,
        que a linha guarda mesmo depois de entregue). É o "guardados sem aviso na última hora" do resumo, não o
        estoque de agora (revisão do #333, R2)."""
        return int(self.db.scalar(
            "SELECT COUNT(*) AS n FROM portal_contatos WHERE motivo='teto_por_hora' AND estado IN ('retido', 'entregue') "
            "AND criado_em>=? AND criado_em<?", (to_iso(inicio), to_iso(fim))) or 0)

    def descartados_pelo_teto_diario_entre(self, inicio: datetime, fim: datetime) -> int:
        """Só o teto diário é sinal de abuso; `campo_invalido` e `falhas_demais` não entram no resumo (R3)."""
        return int(self.db.scalar(
            "SELECT COUNT(*) AS n FROM portal_contatos WHERE estado='descartado' AND motivo='teto_diario' "
            "AND criado_em>=? AND criado_em<?", (to_iso(inicio), to_iso(fim))) or 0)

    def parados(self, agora: datetime) -> tuple[int, int]:
        """Para a saúde: os `pendente` por `canal_desligado` que chegaram há mais de 1 h, e os descartados por
        `falhas_demais` nas últimas 24 h. Só contagens."""
        esperando = self.db.scalar(
            "SELECT COUNT(*) AS n FROM portal_contatos WHERE estado='pendente' AND motivo='canal_desligado' "
            "AND criado_em<?", (to_iso(agora - timedelta(hours=1)),))
        falhos = self.db.scalar(
            "SELECT COUNT(*) AS n FROM portal_contatos WHERE estado='descartado' AND motivo='falhas_demais' "
            "AND atualizado_em>=?", (to_iso(agora - timedelta(days=1)),))
        return int(esperando or 0), int(falhos or 0)

    def marcar(self, contato_id: int, estado: str, motivo: str | None, agora: datetime, *,
               tentou: bool = False) -> None:
        """Descartar apaga o conteúdo, por qualquer motivo: não se guarda dado de quem não vai ser atendido (revisão
        do #333; o teto diário já grava vazio). Ficam o estado, o motivo, as horas e o hash do cliente (a taxa)."""
        if estado == "descartado":
            self.db.execute(
                "UPDATE portal_contatos SET estado=?, motivo=?, atualizado_em=?, tentativas=tentativas+?, nome='', "
                "empresa='', telefone='', mensagem='' WHERE id=?",
                (estado, motivo, to_iso(agora), 1 if tentou else 0, contato_id))
            return
        self.db.execute(
            "UPDATE portal_contatos SET estado=?, motivo=?, atualizado_em=?, tentativas=tentativas+? WHERE id=?",
            (estado, motivo, to_iso(agora), 1 if tentou else 0, contato_id))

    def a_reenviar(self, limite: int) -> list[ContatoGuardado]:
        """Os não entregues, os que menos falharam primeiro e, entre eles, do mais antigo para o mais novo: um contato
        que faz a Canais falhar desce na fila e não prende os outros (revisão do #333, A2)."""
        linhas = self.db.query(
            "SELECT id, nome, empresa, telefone, mensagem, estado, tentativas FROM portal_contatos "
            "WHERE estado IN ('pendente', 'retido') ORDER BY tentativas, id LIMIT ?", (limite,))
        return [ContatoGuardado(int(r["id"]), r["nome"], r["empresa"], r["telefone"], r["mensagem"], r["estado"],
                                int(r["tentativas"])) for r in linhas]

    def apagar_vencidos(self, agora: datetime) -> int:
        """Apaga a linha inteira (o dado pessoal some com ela) dos contatos mais velhos que a retenção."""
        cursor = self.db.execute("DELETE FROM portal_contatos WHERE criado_em<?",
                                 (to_iso(agora - timedelta(days=RETENCAO_DIAS)),))
        return int(getattr(cursor, "rowcount", 0) or 0)

    # ------------------------------------------------------------------ exclusão a pedido do titular (29.83)
    def com_telefone(self) -> list[ContatoAchado]:
        """Os contatos que ainda têm telefone (o descarte apaga). A comparação dígito a dígito é da aplicação: a tabela
        é pequena (180 dias, teto de 500 por dia) e o telefone é texto livre do visitante, sem forma canônica."""
        linhas = self.db.query(
            "SELECT id, criado_em, estado, telefone FROM portal_contatos WHERE telefone<>'' ORDER BY id")
        return [ContatoAchado(int(r["id"]), str(r["criado_em"]), str(r["estado"]), str(r["telefone"])) for r in linhas]

    def estados(self, ids: Sequence[int]) -> dict[int, str]:
        if not ids:
            return {}
        marcas = ",".join("?" * len(ids))
        linhas = self.db.query(f"SELECT id, estado FROM portal_contatos WHERE id IN ({marcas})",  # noqa: S608
                               tuple(ids))
        return {int(r["id"]): str(r["estado"]) for r in linhas}

    def excluir(self, *, ids: Sequence[int], mantidos: Sequence[tuple[int, str]], pedido_por: str,
                executado_por: str, mensagens_apagadas: int, mensagens_a_mao: int, agora: datetime) -> int:
        """O `DELETE` das linhas e o registro da exclusão, juntos: ou os dois ficam, ou nenhum. O registro só leva ids,
        motivos e contagens; nada do titular. Devolve o id do registro."""
        with self.db.tx():
            if ids:
                marcas = ",".join("?" * len(ids))
                self.db.execute(f"DELETE FROM portal_contatos WHERE id IN ({marcas})", tuple(ids))  # noqa: S608
            return int(self.db.inserted_id(
                "INSERT INTO portal_exclusoes(executado_em, executado_por, pedido_por, ids, mantidos, "
                "mensagens_apagadas, mensagens_a_mao) VALUES (?,?,?,?,?,?,?)",
                (to_iso(agora), executado_por, pedido_por, dumps(list(ids)),
                 dumps([{"id": i, "motivo": m} for i, m in mantidos]), mensagens_apagadas, mensagens_a_mao)))
