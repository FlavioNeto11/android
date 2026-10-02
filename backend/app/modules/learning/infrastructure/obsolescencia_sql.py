"""As fontes da obsolescência (30.14): o catálogo ATUAL do app pelo registro e as receitas em lote. Só leitura.

O catálogo é o que `registry.get` devolve — o mesmo que a porta de política aplica na execução. Ter só o arquivo
`catalogo.yaml` e o registro não carregá-lo seria rebaixar por um catálogo que a execução não usa.

As receitas: UMA consulta de todas e o mesmo cálculo de `FontesSql` (a vizinha seguinte da mesma chave, como no
`conteudo` do 30.3; o quadro de versão por `quadro_da_receita`, como no `versao` do 30.6), para a lista do Livro não
fazer três consultas por receita. As versões vivas são lidas uma vez por app.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping

from app.db import Database
from app.modules.applications.infrastructure import registry
from app.modules.learning.application.obsolescencia import FatosDaReceita
from app.modules.learning.domain.obsolescencia import CatalogoDoApp, Substituta
from app.modules.learning.domain.versao import ReceitaDaChave, VersaoViva, quadro_da_receita
from app.modules.learning.infrastructure import linhas


class CatalogosDoRegistro:
    def catalogo(self, app: str) -> CatalogoDoApp | None:
        catalogo = registry.get(app)
        if catalogo is None:
            return None
        return CatalogoDoApp({c.key: bool(c.side_effect) for c in catalogo.capabilities})


class FatosDasReceitasSql:
    def __init__(self, db: Database, vivas: Callable[[str], tuple[VersaoViva, ...]]) -> None:
        """`vivas`: `FontesSql.vivas` (a mesma consulta do quadro de versão do detalhe)."""
        self._db = db
        self._vivas = vivas

    def fatos(self) -> Mapping[str, FatosDaReceita]:
        linhas_ = self._db.query(
            "SELECT id, app_package, app_version, app_signature, variant, step_hash, version, status, replay_ok,"
            " consecutive_fail, created_at FROM recipes ORDER BY app_version, version, id")
        por_chave: dict[tuple[str, str, str, str], list[tuple[str, ReceitaDaChave]]] = {}
        for r in linhas_:
            chave = (linhas.texto(r, "app_package"), linhas.texto(r, "app_signature"), linhas.texto(r, "variant"),
                     linhas.texto(r, "step_hash"))
            receita = ReceitaDaChave(
                ref=str(linhas.inteiro(r, "id")), app_version=linhas.texto(r, "app_version"),
                versao=linhas.inteiro(r, "version"), status=linhas.texto(r, "status"),
                replay_ok=linhas.inteiro(r, "replay_ok"), consecutive_fail=linhas.inteiro(r, "consecutive_fail"),
                criada_em=linhas.texto(r, "created_at"))
            por_chave.setdefault(chave, []).append((chave[0], receita))
        vivas: dict[str, tuple[VersaoViva, ...]] = {}
        saida: dict[str, FatosDaReceita] = {}
        for grupo in por_chave.values():
            da_chave = [rc for _, rc in grupo]
            for pacote, rc in grupo:
                if pacote not in vivas:
                    vivas[pacote] = self._vivas(pacote)
                # A seguinte é a de MENOR versão acima desta na mesma versão do app (a regra de `_conteudo_da_receita`).
                acima = [o for o in da_chave if o.app_version == rc.app_version and o.versao > rc.versao]
                seguinte = min(acima, key=lambda o: o.versao) if acima else None
                saida[rc.ref] = FatosDaReceita(
                    seguinte=(Substituta(seguinte.ref, seguinte.status, "recipes: mesma chave, versão seguinte")
                              if seguinte is not None else None),
                    versao=quadro_da_receita(rc, app=pacote, da_chave=da_chave, vivas=vivas[pacote]))
        return saida


__all__ = ["CatalogosDoRegistro", "FatosDasReceitasSql"]
