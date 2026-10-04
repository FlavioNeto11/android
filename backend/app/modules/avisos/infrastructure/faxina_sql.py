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
- no Trello, também os `trello_cartoes` (087) já arquivados;
- os `canal_contato_eventos` (090) do canal: o que cada convidado mandou entra ali redigido e cortado, mas é conteúdo, e
  vence como o resto. O limite por hora do convidado olha só a última hora, bem dentro do prazo mínimo.

O registro `canal_contatos` (090) NÃO vence, embora o comentário da 090 diga que a faxina cobre as duas tabelas: ali
moram a decisão do dono (autorizado ou recusado) e o histórico de quem falou com o bot, que o dono pediu para guardar.
Apagado por prazo, o convidado autorizado voltaria a ser pessoa nova e o dono teria de decidir de novo.

Os anexos (item 28.24, migração 101) seguem a mesma ordem: a fase 1 APAGA o arquivo de `data/anexos` (e marca a linha
`apagado`) e a fase 2 apaga a linha. O arquivo é nomeado só pelo sha256, e dois anexos iguais dividem um arquivo: ele só
sai quando nenhuma outra linha `guardado` (de qualquer canal, ainda dentro do prazo) o usa. O caminho vem de
`caminho_em`, que não sai de `data/anexos` nem segue link; o que não se valida fica como está e vai ao log. Se o arquivo
não pôde ser apagado (em uso, erro de disco), a linha continua `guardado` e a próxima volta tenta de novo.

O cursor do Trello é uma linha por quadro e não entra. Um reply a um aviso cuja `canal_enviadas` já foi apagada é tratado
como reply a uma mensagem que a Central não mandou: vira recado guardado para a orquestradora, nunca ação.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from app.db import Database
from app.modules.avisos.application.entrega import Cerca
from app.modules.avisos.infrastructure.anexos import CaminhoForaDoArmazem, caminho_em
from app.util import to_iso

log = logging.getLogger("poc.avisos.faxina")

#: Os estados em que a linha ainda espera alguém: perdem o conteúdo, mas não são apagados.
ESTADOS_QUE_ESPERAM = ("recebida", "pergunta", "executando")


@dataclass(frozen=True)
class Faxina:
    zeradas: int = 0
    apagadas: int = 0
    enviadas: int = 0
    cartoes: int = 0
    eventos: int = 0
    anexos: int = 0

    @property
    def algo(self) -> bool:
        return bool(self.zeradas or self.apagadas or self.enviadas or self.cartoes or self.eventos or self.anexos)


class FaxinaDosCanais:
    def __init__(self, db: Database, *, relogio: Callable[[], datetime] | None = None,
                 pasta_anexos: Path | None = None):
        self.db = db
        self.relogio: Callable[[], datetime] = relogio if relogio is not None else db.agora
        #: `data/anexos`. Sem ela (testes que não tratam de anexo), a faxina não mexe nos anexos: nem arquivo nem linha.
        self.pasta_anexos = pasta_anexos

    def _apagar_arquivos(self, canal: str, limite: str) -> None:
        """Fase 1 dos anexos: apaga o arquivo de cada conteúdo vencido que nenhuma outra linha `guardado` usa e marca as
        linhas `apagado`. O arquivo que não se apaga deixa a linha `guardado` (a próxima volta repete)."""
        assert self.pasta_anexos is not None
        vencidos = self.db.query(
            "SELECT id, sha256, mime FROM canal_anexos WHERE canal=? AND estado='guardado' AND criado_em < ?",
            (canal, limite))
        agora = to_iso(self.relogio())
        por_conteudo: dict[tuple[str, str], list[int]] = {}
        for v in vencidos:
            por_conteudo.setdefault((str(v["sha256"] or ""), str(v["mime"] or "")), []).append(int(v["id"]))
        for (sha, mime), ids in por_conteudo.items():
            em_uso = self.db.one(
                "SELECT 1 AS x FROM canal_anexos WHERE sha256=? AND estado='guardado'"
                " AND NOT (canal=? AND criado_em < ?) LIMIT 1", (sha, canal, limite))
            if em_uso is None and sha:
                try:
                    caminho = caminho_em(self.pasta_anexos, sha, mime)
                    caminho.unlink(missing_ok=True)
                except CaminhoForaDoArmazem:
                    log.warning("canais: anexo %s fora do armazém ou inválido; o arquivo não foi tocado", ids[0])
                except OSError as exc:
                    log.warning("canais: arquivo do anexo %s não apagado (%s); a próxima volta tenta de novo", ids[0],
                                type(exc).__name__)
                    continue
            marcas = ",".join("?" * len(ids))
            self.db.execute(f"UPDATE canal_anexos SET estado='apagado', apagado_em=? WHERE id IN ({marcas})",  # noqa: S608
                            (agora, *ids))

    def faxinar(self, *, cerca: Cerca, canal: str, retencao_dias: float) -> Faxina:
        limite = to_iso(self.relogio() - timedelta(days=retencao_dias))
        marcas = ",".join("?" * len(ESTADOS_QUE_ESPERAM))
        if self.pasta_anexos is not None:
            with cerca():
                self._apagar_arquivos(canal, limite)
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
            eventos = self.db.execute(
                "DELETE FROM canal_contato_eventos WHERE canal=? AND em < ?", (canal, limite)).rowcount or 0
            anexos = 0
            if self.pasta_anexos is not None:
                anexos = self.db.execute(
                    "DELETE FROM canal_anexos WHERE canal=? AND criado_em < ? AND estado IN ('apagado','recusado')",
                    (canal, limite)).rowcount or 0
        return Faxina(int(zeradas), int(apagadas), int(enviadas), int(cartoes), int(eventos), int(anexos))
