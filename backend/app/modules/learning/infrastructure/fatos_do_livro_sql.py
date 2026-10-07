"""31.231: os fatos do Livro de um assunto, para a pesquisa da operação reaproveitar. Só leitura; uma consulta.

São os itens que a curadoria do 31.190 fez dos fatos confirmados da pesquisa (`SourceKind.FATO_DA_OPERACAO`), com o
assunto canônico no escopo (`scope_subject`, 31.200) e o pacote do app (`scope_app`, que a curadoria grava): o mesmo
assunto em outro app não cobre a pesquisa deste (revisão da Jev). O texto vem do conteúdo do item; o frescor e os domínios das
fontes, da proveniência (v1.117). Regra de "cobrir o pedido": `domain/reaproveitamento_da_pesquisa.py`.
"""
from __future__ import annotations

from app.db import Database, loads
from app.modules.learning.domain.livro import assunto_canonico
from app.modules.learning.domain.reaproveitamento_da_pesquisa import ESTADOS_VIVOS, FatoDoLivro
from app.modules.learning.domain.vocabulario import SourceKind

#: Teto de fatos lidos por assunto (o bloco da operação tem teto próprio; isto só limita a leitura).
MAXIMO = 20


class LeitorDeFatosDoLivro:
    def __init__(self, db: Database):
        self.db = db

    def do_assunto(self, assunto: str, pacote: str) -> tuple[FatoDoLivro, ...]:
        canonico = assunto_canonico(assunto)
        if not canonico or not pacote or "scope_subject" not in self.db.columns("learning_items"):
            return ()
        marcas = ",".join("?" * len(ESTADOS_VIVOS))
        saida: list[FatoDoLivro] = []
        for r in self.db.query(f"SELECT id, state, content, provenance FROM learning_items WHERE source_kind=?"
                               f" AND scope_subject=? AND scope_app=? AND state IN ({marcas})"
                               f" ORDER BY updated_at DESC, id DESC LIMIT ?",
                               (SourceKind.FATO_DA_OPERACAO.value, canonico, pacote, *ESTADOS_VIVOS, MAXIMO)):
            conteudo = loads(r["content"], {}) or {}
            prov = loads(r["provenance"], {}) or {}
            texto = " ".join(str(conteudo.get("fato") or "").split())
            if not texto:
                continue
            frescor = prov.get("frescor_ate")
            dominios = prov.get("fontes")
            saida.append(FatoDoLivro(ref=str(r["id"]), texto=texto, estado=str(r["state"]),
                                     frescor_ate=str(frescor) if isinstance(frescor, str) and frescor else None,
                                     dominios=tuple(str(d) for d in dominios) if isinstance(dominios, list) else (),
                                     operacao_de_origem=str(prov.get("operacao") or "")))
        return tuple(saida)


__all__ = ["LeitorDeFatosDoLivro"]
