"""A faxina das tabelas de canal por prazo de retenção (item 28.16; achado I6 da revisão do #166).

O que a conversa pelos canais guarda (o texto do comando, a prévia, a resposta) não pode ficar além do prazo. A faxina
corre no líder da trava `avisos`, sob a cerca do mandato, em duas fases e nesta ordem:
1. ZERA o texto, a prévia, a resposta e o erro das linhas vencidas, em qualquer estado. Se a fase 2 falhar, nada de
   conteúdo ficou;
2. só depois APAGA as linhas vencidas que já terminaram.

O que fica de pé:
- A linha que espera alguém (`recebida`, `pergunta`, `executando`) perde o texto, mas não some.
- A linha mais nova de cada canal nunca é apagada. Ela segura o offset do Telegram (`MAX(ordem) + 1`) e tira o canal
  de "vazio": um canal sem linha seria tratado como 1ª subida, e uma mensagem pendente iria embora como histórico.

A chave do dedupe `(canal, id_externo)` some junto da linha. Por isso o prazo mínimo (`ge=2` dias na config) passa da
janela em que o canal repete uma entrega: o Telegram guarda a update por até 24 h, e o webhook do Trello reenvia por
horas. A reconciliação do Trello lê só depois do cursor.

O que se apaga, por canal:
- `canal_entradas` e `canal_enviadas` (085);
- no Trello, também os `trello_cartoes` (087) já arquivados.

O cursor do Trello é uma linha por quadro e não entra. Um reply a um aviso cuja `canal_enviadas` já foi apagada é tratado
como reply a uma mensagem que a Central não mandou: vira recado guardado para a orquestradora, nunca ação.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.db import Database
from app.modules.avisos.application.entrega import Cerca
from app.util import to_iso

#: Os estados em que a linha ainda espera alguém: perdem o conteúdo, mas não são apagados.
ESTADOS_QUE_ESPERAM = ("recebida", "pergunta", "executando")


@dataclass(frozen=True)
class Faxina:
    zeradas: int = 0
    apagadas: int = 0
    enviadas: int = 0
    cartoes: int = 0

    @property
    def algo(self) -> bool:
        return bool(self.zeradas or self.apagadas or self.enviadas or self.cartoes)


class FaxinaDosCanais:
    def __init__(self, db: Database, *, relogio: Callable[[], datetime] | None = None):
        self.db = db
        self.relogio: Callable[[], datetime] = relogio if relogio is not None else db.agora

    def faxinar(self, *, cerca: Cerca, canal: str, retencao_dias: float) -> Faxina:
        limite = to_iso(self.relogio() - timedelta(days=retencao_dias))
        marcas = ",".join("?" * len(ESTADOS_QUE_ESPERAM))
        with cerca():
            zeradas = self.db.execute(
                "UPDATE canal_entradas SET texto=NULL, previa=NULL, resposta=NULL, erro=NULL"
                " WHERE canal=? AND recebida_em < ?"
                " AND (texto IS NOT NULL OR previa IS NOT NULL OR resposta IS NOT NULL OR erro IS NOT NULL)",
                (canal, limite)).rowcount or 0
        with cerca():
            apagadas = self.db.execute(
                f"DELETE FROM canal_entradas WHERE canal=? AND recebida_em < ? AND estado NOT IN ({marcas})"  # noqa: S608
                " AND id < (SELECT MAX(id) FROM canal_entradas WHERE canal=?)",
                (canal, limite, *ESTADOS_QUE_ESPERAM, canal)).rowcount or 0
            enviadas = self.db.execute(
                "DELETE FROM canal_enviadas WHERE canal=? AND enviada_em < ?", (canal, limite)).rowcount or 0
            cartoes = 0
            if canal == "trello":
                cartoes = self.db.execute(
                    "DELETE FROM trello_cartoes WHERE estado='arquivado' AND atualizado_em < ?", (limite,)).rowcount or 0
        return Faxina(int(zeradas), int(apagadas), int(enviadas), int(cartoes))
