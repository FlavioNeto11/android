"""A tabela `portal_contatos` (migração 107) e o sal do portal em `settings` (29.77, ADR-075).

Só SQL; a decisão do que fazer (taxa, teto, reenvio) mora na aplicação. Horários em ISO UTC (`util.to_iso`), que
ordenam como texto nos dois bancos.
"""
from __future__ import annotations

import secrets
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

    def retidos(self) -> int:
        return int(self.db.scalar("SELECT COUNT(*) AS n FROM portal_contatos WHERE estado='retido'") or 0)

    def descartados_desde(self, desde: datetime) -> int:
        return int(self.db.scalar(
            "SELECT COUNT(*) AS n FROM portal_contatos WHERE estado='descartado' AND atualizado_em>=?",
            (to_iso(desde),)) or 0)

    def marcar(self, contato_id: int, estado: str, motivo: str | None, agora: datetime, *,
               tentou: bool = False) -> None:
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
