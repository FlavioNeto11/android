"""30.14: liga a obsolescência ao Livro. O leitor fica pendurado no serviço (a saúde o acha pelo tipo e calcula
`obsoleto_provavel`); o rebaixamento `catalogo_sem_efeito` entra como passo da curadoria periódica. Nada aqui chama IA."""
from __future__ import annotations

from app.db import Database
from app.modules.learning.application.obsolescencia import LeitorDeObsolescencia, RebaixamentoPorCatalogo
from app.modules.learning.application.ports import FontesDoLivro, RepositorioDeAprendizado
from app.modules.learning.application.servico import LearningService
from app.modules.learning.infrastructure.fontes import FontesSql
from app.modules.learning.infrastructure.obsolescencia_sql import CatalogosDoRegistro, FatosDasReceitasSql


def ligar(servico: LearningService, repo: RepositorioDeAprendizado, db: Database, *,
          fontes: FontesDoLivro | None = None) -> LeitorDeObsolescencia:
    """`fontes`: as do Livro (a composição passa as mesmas do serviço; sem elas, as do banco sem registro de apps)."""
    leitura = FontesSql(db)
    leitor = LeitorDeObsolescencia(fontes or leitura, repo, CatalogosDoRegistro(),
                                   FatosDasReceitasSql(db, vivas=leitura.vivas))
    servico.anexar(leitor)
    servico.registrar_passo(RebaixamentoPorCatalogo(servico, leitor))
    return leitor


__all__ = ["ligar"]
