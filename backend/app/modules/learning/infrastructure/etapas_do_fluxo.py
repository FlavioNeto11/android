"""As etapas do FLUXO com os fatos do catálogo do app de cada uma (30.32), lidas num lugar só.

Quem classifica o fluxo pela etapa mais restritiva são dois: o dossiê do curador (`dossies.py`) e o aviso
`learning.needs_person` (`application/espera.py`, 30.33). Os dois leem as etapas por aqui, para a faixa do aviso ser a
classe do dossiê (antes, o aviso não via as etapas e dizia B para o fluxo que o catálogo põe em C).
"""
from __future__ import annotations

from app.db import Database
from app.modules.learning.application.ports import CatalogoDeRisco
from app.modules.learning.domain.livro import EntradaDoLivro
from app.modules.learning.domain.politica_de_risco import EtapaDeRisco, FatosDoCatalogo
from app.modules.learning.domain.vocabulario import LivroKind
from app.modules.learning.infrastructure import linhas
from app.modules.skills.domain.document import JsonObject


class EtapasDoFluxo:
    def __init__(self, db: Database, catalogo: CatalogoDeRisco | None) -> None:
        self._db = db
        self._catalogo = catalogo

    def fatos_do_catalogo(self, app: str, capability: str) -> tuple[bool, FatosDoCatalogo | None]:
        tem = bool(app) and self._catalogo is not None and self._catalogo.tem_catalogo(app)
        if not tem or self._catalogo is None or not capability or capability == "*":
            return tem, None
        return tem, self._catalogo.da_capability(app, capability)

    def _pacote(self, app_id: str) -> str:
        """O `app_id` da etapa (12.1: `instagram`, `outlook`) é o id de `apps`; o catálogo é pelo pacote. Sem linha,
        nada: a etapa fica sem fatos (e, se for de efeito, o fluxo fica na lacuna de sempre)."""
        r = self._db.one("SELECT package FROM apps WHERE id=?", (app_id,))
        return (linhas.texto_ou_nulo(r, "package") or "") if r is not None else ""

    def de(self, e: EntradaDoLivro, conteudo: JsonObject | None) -> tuple[EtapaDeRisco, ...]:
        """30.32: cada etapa do fluxo com os fatos do catálogo do app DELA (`app` nulo = o app do fluxo). A classe do
        fluxo é a da etapa mais restritiva (`politica_de_risco._razoes`); sem isto, todo fluxo com efeito caía em
        `commit_sem_fatos_da_etapa` (B), inclusive comentar, mandar mensagem e seguir, que o catálogo diz C."""
        if e.kind is not LivroKind.FLUXO:
            return ()
        etapas = (conteudo or {}).get("etapas")
        saida: list[EtapaDeRisco] = []
        for etapa in etapas if isinstance(etapas, list) else []:
            if not isinstance(etapa, dict):
                continue
            capability = etapa.get("capability")
            capability = capability if isinstance(capability, str) else ""
            app = etapa.get("app")
            pacote = self._pacote(app) if isinstance(app, str) and app else e.app or ""
            _, fatos = self.fatos_do_catalogo(pacote, capability)
            saida.append(EtapaDeRisco(capability=capability, efeito=etapa.get("efeito") is True, catalogo=fatos))
        return tuple(saida)


__all__ = ["EtapasDoFluxo"]
