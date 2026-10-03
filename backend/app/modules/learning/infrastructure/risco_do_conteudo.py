"""O que o conteúdo de um item diz do risco dele, lido num lugar só (30.33): a capability (derivada, na receita e no
fluxo) e, no FLUXO, as etapas com os fatos do catálogo do app de cada uma (30.32).

Dois classificam o item por aqui: o dossiê do curador (`dossies.py`) e o aviso `learning.needs_person`
(`application/espera.py`). Os dois leem do mesmo jeito para a faixa do aviso ser a classe do dossiê. Antes, o aviso da
transição nativa não via a capability da receita nem as etapas do fluxo, e dizia B onde o parecer dizia C.
"""
from __future__ import annotations

from app.db import Database
from app.modules.learning.application.ports import CatalogoDeRisco
from app.modules.learning.domain.livro import EntradaDoLivro
from app.modules.learning.domain.politica_de_risco import EtapaDeRisco, FatosDoCatalogo
from app.modules.learning.domain.vocabulario import KINDS_DE_ITEM, LivroKind
from app.modules.learning.infrastructure import linhas
from app.modules.skills.domain.document import JsonObject


def capability_do_item(e: EntradaDoLivro, conteudo: JsonObject | None, item_capability: str) -> str:
    """A capability do item: a do escopo, no item do livro; a derivada do conteúdo (única e não ambígua), na fonte
    nativa. Sem uma só, vazio: o catálogo não é consultado."""
    if e.kind in KINDS_DE_ITEM:
        return item_capability
    cap = (conteudo or {}).get("capability")
    if isinstance(cap, dict) and cap.get("ambigua") is not True:
        nomes = cap.get("nomes")
        if isinstance(nomes, list) and len(nomes) == 1 and isinstance(nomes[0], str):
            return nomes[0]
    return ""


class RiscoDoConteudo:
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

    def etapas(self, e: EntradaDoLivro, conteudo: JsonObject | None) -> tuple[EtapaDeRisco, ...]:
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

    def do_nativo(self, e: EntradaDoLivro, conteudo: JsonObject | None) -> tuple[str, tuple[EtapaDeRisco, ...]]:
        """A capability e as etapas de uma fonte nativa (receita ou fluxo), como o dossiê as lê."""
        return capability_do_item(e, conteudo, ""), self.etapas(e, conteudo)


__all__ = ["RiscoDoConteudo", "capability_do_item"]
