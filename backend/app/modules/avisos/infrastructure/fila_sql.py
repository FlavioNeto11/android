"""A fila durável dos avisos (migração 068), em SQLite e PostgreSQL.

Quem escreve aqui e com que garantia:

- `enfileirar` roda em QUALQUER backend que viu o evento. É idempotente pela chave única (`ON CONFLICT DO NOTHING`, e
  não `except IntegrityError`: no PostgreSQL o erro abortaria a transação do chamador): duas réplicas, ou o mesmo
  evento relido, produzem UMA linha.
- `reivindicar_um` e as varreduras só rodam no líder da trava `avisos` e dentro de `Lideranca.cercada`: a transição
  `pendente → enviando` confere o token NA MESMA transação. Um líder que acordou de uma pausa longa e já perdeu o
  mandato é recusado antes de marcar a linha e, portanto, antes de enviar.
- `enviando` abandonado (o processo caiu entre reivindicar e gravar o desfecho) NUNCA volta a `pendente`: a varredura o
  passa a `incerto`. O envio pode ter saído; reenviar duplicaria, e não reenviar apenas deixa de repetir o que a caixa
  do painel já mostra.

O tempo é o relógio injetado (o do banco, por padrão): comparar `proximo_envio_em` com o relógio de cada máquina
reabriria o defeito do item 5.3.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import cast

from app.db import Database
from app.modules.avisos.application.entrega import Cerca, Entrega
from app.modules.avisos.domain.mensagem import Aviso
from app.util import to_iso

#: Estados que já não mudam: candidatos à purga.
ESTADOS_FINAIS = ("enviado", "falhou", "incerto", "descartado")
MAX_ERRO = 300


def _curto(texto: str) -> str:
    """O erro gravado é curto e de uma linha: ele vai para o diagnóstico, não para a tela de ninguém."""
    return " ".join(texto.split())[:MAX_ERRO]


class FilaDeAvisos:
    def __init__(self, db: Database, *, canal: str = "telegram", relogio: Callable[[], datetime] | None = None):
        self.db = db
        self.canal = canal
        self.relogio: Callable[[], datetime] = relogio if relogio is not None else db.agora

    def _agora(self) -> str:
        return to_iso(self.relogio())

    # ------------------------------------------------------------------ entrada
    def enfileirar(self, aviso: Aviso) -> bool:
        """Grava o aviso como `pendente`. Devolve se a linha é nova (`False` = o fato já estava na fila)."""
        cur = self.db.execute(
            "INSERT INTO avisos_entregas(chave, tipo, titulo, corpo, link, canal, criado_em) VALUES (?,?,?,?,?,?,?)"
            " ON CONFLICT (chave) DO NOTHING",
            (aviso.chave, aviso.tipo, aviso.titulo, aviso.corpo, aviso.link, self.canal, self._agora()))
        return (cur.rowcount or 0) == 1

    # ------------------------------------------------------------------ saída (líder)
    def reivindicar_um(self, *, cerca: Cerca) -> Entrega | None:
        """A próxima linha devida, já marcada `enviando` e com a tentativa contada, ou `None`.

        A cerca é a do mandato do líder. O `UPDATE ... WHERE estado='pendente'` confere de novo o estado: duas
        reivindicações que sobrevivessem à cerca ainda não pegariam a mesma linha.
        """
        agora = self._agora()
        with cerca():
            linha = self.db.one(
                "SELECT id FROM avisos_entregas WHERE estado='pendente' AND canal=?"
                " AND (proximo_envio_em IS NULL OR proximo_envio_em <= ?) ORDER BY id LIMIT 1",
                (self.canal, agora))
            if linha is None:
                return None
            cur = self.db.execute(
                "UPDATE avisos_entregas SET estado='enviando', tentativas=tentativas+1, iniciado_em=?"
                " WHERE id=? AND estado='pendente'", (agora, int(linha["id"])))
            if (cur.rowcount or 0) != 1:
                return None
            r = self.db.one("SELECT id, chave, tipo, titulo, corpo, link, tentativas FROM avisos_entregas WHERE id=?",
                            (int(linha["id"]),))
        assert r is not None
        return Entrega(id=int(r["id"]), chave=str(r["chave"]), tipo=str(r["tipo"]), titulo=str(r["titulo"]),
                       corpo=str(r["corpo"] or ""), link=cast("str | None", r["link"]), tentativas=int(r["tentativas"]))

    def marcar_enviado(self, entrega_id: int, *, message_id: int | None = None) -> None:
        """Com o `message_id`, a mensagem entra no registro do que a Central enviou (085, item 28.15): é o que liga o
        reply da pessoa ao fato do aviso, e o que separa o reply à Central do reply à orquestradora."""
        agora = self._agora()
        self.db.execute("UPDATE avisos_entregas SET estado='enviado', enviado_em=?, proximo_envio_em=NULL,"
                        " ultimo_erro=NULL WHERE id=? AND estado='enviando'", (agora, entrega_id))
        if message_id is not None:
            self.db.execute(
                "INSERT INTO telegram_enviadas(message_id, origem, fato, aviso_id, enviada_em)"
                " SELECT ?, 'aviso', chave, id, ? FROM avisos_entregas WHERE id=? ON CONFLICT (message_id) DO NOTHING",
                (int(message_id), agora, entrega_id))

    def marcar_retentar(self, entrega_id: int, *, ate: datetime, erro: str) -> None:
        self.db.execute("UPDATE avisos_entregas SET estado='pendente', proximo_envio_em=?, ultimo_erro=?"
                        " WHERE id=? AND estado='enviando'", (to_iso(ate), _curto(erro), entrega_id))

    def marcar_falhou(self, entrega_id: int, *, erro: str) -> None:
        self.db.execute("UPDATE avisos_entregas SET estado='falhou', proximo_envio_em=NULL, ultimo_erro=?"
                        " WHERE id=? AND estado='enviando'", (_curto(erro), entrega_id))

    # ------------------------------------------------------------------ varreduras (líder, cercadas)
    def varrer_incertos(self, *, cerca: Cerca, parado_ha_s: float) -> int:
        """`enviando` há mais de `parado_ha_s` é resto de uma queda no meio do envio: vira `incerto`, sem reenvio."""
        limite = to_iso(self.relogio() - timedelta(seconds=parado_ha_s))
        with cerca():
            cur = self.db.execute(
                "UPDATE avisos_entregas SET estado='incerto', ultimo_erro=?"
                " WHERE estado='enviando' AND iniciado_em < ?",
                ("o processo parou durante o envio; pode ter saído, não foi reenviado", limite))
        return int(cur.rowcount or 0)

    def vencer(self, *, cerca: Cerca, validade_h: float) -> int:
        """`pendente` velho demais não é mais notícia (canal fora do ar por dias, segredo faltando): `descartado`."""
        limite = to_iso(self.relogio() - timedelta(hours=validade_h))
        with cerca():
            cur = self.db.execute(
                "UPDATE avisos_entregas SET estado='descartado', ultimo_erro=?"
                " WHERE estado='pendente' AND criado_em < ?",
                (f"venceu: ficou mais de {validade_h:g} h sem sair", limite))
        return int(cur.rowcount or 0)

    def purgar(self, *, cerca: Cerca, retencao_dias: float) -> int:
        limite = to_iso(self.relogio() - timedelta(days=retencao_dias))
        marcas = ",".join("?" * len(ESTADOS_FINAIS))
        with cerca():
            cur = self.db.execute(
                f"DELETE FROM avisos_entregas WHERE estado IN ({marcas}) AND criado_em < ?",  # noqa: S608
                (*ESTADOS_FINAIS, limite))
        return int(cur.rowcount or 0)

    # ------------------------------------------------------------------ leitura
    def contagens(self) -> dict[str, int]:
        linhas = self.db.query("SELECT estado, COUNT(*) AS n FROM avisos_entregas GROUP BY estado")
        return {str(r["estado"]): int(r["n"]) for r in linhas}
