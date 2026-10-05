"""28.37: quais canais cada backend liga, e o problema de saúde quando o líder da trava `avisos` não liga um que outro liga.

Com dois ou mais backends, a trava `avisos` é uma só para o aviso do Telegram e para o Trello (28.35): quem a toma
primeiro segura os dois laços. Se um backend liga só o aviso e outro só o Trello, o canal que só o outro liga para
calado. A trava guarda só o dono; aqui cada backend publica o que liga, e a saúde compara com o líder.

A publicação vive na tabela `settings` (sem migração), na chave `canais.config:<OWNER_ID>`. A tela de Configuração lê só
a chave `limits` e o `PUT /api/settings` recusa chave fora dos limites, então ela não aparece para gente nem se escreve
de fora. O valor leva só booleanos e a hora: nenhum nome de máquina, token, chat ou id de quadro. O texto do problema
também não leva o `OWNER_ID`, porque a saúde entra no resumo do Telegram.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from app.config import Config
from app.db import Database, dumps, loads
from app.models import Problem
from app.taskqueue.travas import AVISOS, TRAVA_TTL_S
from app.util import parse_iso, to_iso

log = logging.getLogger(__name__)

PREFIXO = "canais.config:"
#: A publicação vale enquanto o dono dela renova; depois disso, não conta (o backend pode ter caído).
FRESCA_S = 2 * TRAVA_TTL_S
#: Sem mudança, regrava só quando a publicação tem mais que isto: o laço das travas roda a cada 20 s.
REGRAVAR_S = 120.0
#: Publicação de backend que sumiu há mais que isto sai da tabela na próxima escrita de qualquer backend.
VELHA_S = 3600.0
#: Nome do canal para gente, no texto do problema.
NOME_DO_CANAL = {"avisos": "aviso no Telegram", "trello": "Trello"}


#: Uma publicação lida: os canais, a hora (`None` se ilegível) e o valor cru (a varredura apaga só se ele não mudou).
Publicacao = tuple[dict[str, bool], datetime | None, str]


class CanaisDaFrota:
    """`roda`: este processo roda os laços dos canais (`roda_scheduler`). Uma réplica só de API não publica, não retira e
    não se conta na comparação: os canais dela nunca rodam ali, e o `/health` dela acusaria um canal que nunca parou."""

    def __init__(self, db: Database, cfg: Config, *, dono: str, relogio: Callable[[], datetime] | None = None,
                 roda: bool = True):
        self.db = db
        self.cfg = cfg
        self.dono = dono
        self.roda = roda
        self.relogio: Callable[[], datetime] = relogio if relogio is not None else db.agora
        self._escrito: tuple[dict[str, bool], datetime] | None = None

    @property
    def chave(self) -> str:
        return PREFIXO + self.dono

    def meus(self) -> dict[str, bool]:
        return {"avisos": bool(self.cfg.file.avisos.enabled), "trello": bool(self.cfg.file.trello.enabled)}

    # ------------------------------------------------------------------ publicação
    def publicar(self) -> bool:
        """Grava o que este backend liga, só quando mudou ou quando a última escrita tem mais que `REGRAVAR_S`. Devolve
        se escreveu. Na escrita, varre as publicações de backend que sumiu há mais que `VELHA_S`."""
        if not self.roda:
            return False
        agora, meus = self.relogio(), self.meus()
        if self._escrito is not None:
            antes, em = self._escrito
            if antes == meus and agora - em < timedelta(seconds=REGRAVAR_S):
                return False
        self.db.execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (self.chave, dumps({**meus, "em": to_iso(agora)})))
        self._escrito = (meus, agora)
        self._varrer(agora)
        return True

    def retirar(self) -> None:
        """Encerramento limpo: a publicação sai na hora, e a saúde dos outros não espera ela envelhecer. A réplica só de
        API não publicou e não apaga: com o mesmo `OWNER_ID` do processo do scheduler, apagaria a publicação dele."""
        if not self.roda:
            return
        self.db.execute("DELETE FROM settings WHERE key=?", (self.chave,))
        self._escrito = None

    def _varrer(self, agora: datetime) -> None:
        limite = agora - timedelta(seconds=VELHA_S)
        velhas = [(k, p[2]) for k, p in self._todas().items() if k != self.chave and (p[1] is None or p[1] < limite)]
        for k, bruto in velhas:
            # Pelo valor lido, não só pela chave: se o dono voltou e regravou entre a leitura e o DELETE, a publicação
            # nova fica (sem isto, ela sumiria da saúde até a regravação seguinte dele, 120 s depois).
            self.db.execute("DELETE FROM settings WHERE key=? AND value=?", (k, bruto))

    def _todas(self) -> dict[str, Publicacao]:
        saida: dict[str, Publicacao] = {}
        for linha in self.db.query("SELECT key, value FROM settings WHERE key LIKE ?", (PREFIXO + "%",)):
            bruto = str(linha["value"])
            valor = loads(bruto, {}) or {}
            canais = {c: bool(valor.get(c)) for c in NOME_DO_CANAL}
            saida[str(linha["key"])] = (canais, parse_iso(str(valor.get("em") or "")), bruto)
        return saida

    # ------------------------------------------------------------------ saúde
    def problemas(self) -> list[Problem]:
        """`canais_divergentes` quando um canal ligado em algum backend com publicação fresca não está ligado no líder
        da trava `avisos`. Sem líder, ou com o líder sem publicação, não acusa: não há como saber o que ele liga."""
        agora = self.relogio()
        dono = self.db.scalar("SELECT dono FROM travas WHERE nome=? AND expira_em > ?", (AVISOS, to_iso(agora)))
        if not dono:
            return []
        fresca = agora - timedelta(seconds=FRESCA_S)
        frescas = {k[len(PREFIXO):]: canais for k, (canais, em, _) in self._todas().items()
                   if em is not None and em >= fresca}
        if self.roda:
            frescas[self.dono] = self.meus()  # o local vale pelo que está ligado agora, mesmo antes da primeira escrita
        lider = frescas.get(str(dono))
        if lider is None:
            return []
        parados = [c for c in NOME_DO_CANAL if not lider[c] and any(p[c] for d, p in frescas.items() if d != dono)]
        if not parados:
            return []
        nomes = " e o ".join(NOME_DO_CANAL[c] for c in parados)
        return [Problem(
            code="canais_divergentes",
            message=f"Os servidores da Central ligam canais diferentes: o servidor da vez não liga o {nomes}, que outro "
                    "servidor liga; esse canal está parado.",
            hint="Iguale avisos.enabled e trello.enabled em todos os backends e reinicie a tarefa farm-central "
                 "(docs/dominios/canais.md, seção 3).")]
