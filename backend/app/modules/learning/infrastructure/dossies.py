"""`FonteDeDossies`: o dossiê do curador (§8.2, `domain/curador.montar_dossie`) montado do que o Livro já lê, sem IA.

Do detalhe do Livro (`LearningService.detalhe`) vêm o conteúdo legível, a saúde, a versão, as relações e a trilha; a
evidência é lida aqui porque o dossiê cita cada uma pelo id da linha (`ev:<id>`), que a leitura do Livro não carrega.
Os fatos de risco seguem a mesma regra do aviso de espera (`application/espera.py`): o catálogo do app pela
capability do item, nunca a etapa livre (`*`), e o reaprendido depois de uma evidência inválida (30.23), que é B. A
evidência da execução marcada como inválida no item não entra: ela não prova nada (a mesma regra da saúde e da sombra).

Fica de fora nesta fatia (dossiê com menos fatos, nunca com fato inventado): os grupos do backlog e os votos e
intervenções da etapa (fontes de outros itens). O que o dossiê não traz, a resposta não pode citar.
"""
from __future__ import annotations

from app.db import Database
from app.modules.learning.application.ports import CatalogoDeRisco, RepositorioDeAprendizado
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.ciclo import SYSTEM_ACTOR, NaoEncontrado
from app.modules.learning.domain.curador import (MAX_EVIDENCIAS, AppDoItem, Dossie, Evidencia, IdentidadeDoItem,
                                                 PassoDaTrilha, Relacao, montar_dossie)
from app.modules.learning.domain.evidencia_invalida import run_invalidada
from app.modules.learning.domain.politica_de_risco import FatosDeRisco, toca_sessao_ou_autenticacao
from app.modules.learning.domain.livro import EntradaDoLivro
from app.modules.learning.domain.saude import Saude
from app.modules.learning.domain.validacao import Motivo
from app.modules.learning.domain.vocabulario import KINDS_DE_ITEM, LivroKind
from app.modules.learning.infrastructure import linhas
from app.modules.learning.infrastructure.risco_do_conteudo import RiscoDoConteudo, capability_do_item
from app.modules.skills.domain.document import JsonObject


def _saude(s: Saude | None) -> JsonObject | None:
    """Só o rótulo, os códigos e os números: o `detalhe` de um motivo pode ser o texto de uma transição."""
    if s is None:
        return None
    return {"rotulo": s.rotulo.value,
            "motivos": [{"codigo": m.codigo.value, "dimensao": None if m.dimensao is None else m.dimensao.value,
                         "valor": m.valor if isinstance(m.valor, (int, float)) else None, "limite": m.limite}
                        for m in s.motivos]}


class DossiesSql:
    def __init__(self, db: Database, servico: LearningService, repo: RepositorioDeAprendizado,
                 catalogo: CatalogoDeRisco | None) -> None:
        self._db = db
        self._servico = servico
        self._repo = repo
        # 30.33: a capability, as etapas do fluxo e os fatos do catálogo vêm do mesmo leitor do aviso
        # `learning.needs_person`.
        self._risco = RiscoDoConteudo(db, catalogo)

    def _evidencias(self, item_ref: str) -> list[Evidencia]:
        return [Evidencia(id=linhas.inteiro(r, "id"), posicao=linhas.texto(r, "stance"),
                          origin_ref=linhas.texto(r, "origin_ref"), em=linhas.texto(r, "observed_at"),
                          run_id=linhas.texto_ou_nulo(r, "run_id"), aparelho=linhas.texto_ou_nulo(r, "instance_id"),
                          app_version=linhas.texto_ou_nulo(r, "app_version"), simulated=bool(r["simulated"]))
                for r in self._db.query(
                    "SELECT id, stance, origin_ref, run_id, instance_id, app_version, simulated, observed_at"
                    " FROM learning_evidence WHERE item_ref=? ORDER BY id DESC LIMIT 200", (item_ref,))]

    def _apps(self, e: EntradaDoLivro, conteudo: JsonObject | None) -> tuple[AppDoItem, ...]:
        """30.33-C: os apps do fluxo multi-app pelo id E pelo pacote, na ordem do plano, com a marca do principal
        (o curador leu `item.app` = pacote contra `conteudo.apps` = ids como divergência). Só com mais de um app."""
        ids = (conteudo or {}).get("apps") if e.kind is LivroKind.FLUXO and len(e.apps) > 1 else None
        if not isinstance(ids, list):
            return ()
        principal = (conteudo or {}).get("app")
        todos = [*ids, principal]                       # o principal fora da tabela de exigidos entra no fim
        apps = tuple(AppDoItem(id=a, pacote=self._risco.pacote(a), principal=a == principal)
                     for a in dict.fromkeys(x for x in todos if isinstance(x, str) and x))
        return apps if len(apps) > 1 else ()

    def _sem_caminho(self, e: EntradaDoLivro) -> bool:
        """30.36: a validação da receita fechou `sem_caminho` (o fluxo ativo do comando não chega à etapa dela)."""
        return e.kind is LivroKind.RECEITA and self._db.one(
            "SELECT 1 AS x FROM learning_validations WHERE item_ref=? AND motivo=?",
            (e.trail_ref, Motivo.SEM_CAMINHO.value)) is not None

    def dossie(self, entrada: EntradaDoLivro, *, max_evidencias: int | None = None) -> Dossie | None:
        if entrada.kind in (LivroKind.MEMORIA, LivroKind.HABILIDADE):
            return None
        try:
            d = self._servico.detalhe(entrada.kind, entrada.ref)
        except NaoEncontrado:
            return None
        e = d.entrada
        item = self._repo.item(e.ref) if e.kind in KINDS_DE_ITEM else None
        capability = capability_do_item(e, d.conteudo, item.escopo.capability if item is not None else "")
        tela_autenticada = e.kind is LivroKind.TELA and (d.conteudo or {}).get("autenticada") is True
        source_kind = item.source_kind.value if item is not None else None
        tem, fatos = self._risco.fatos_do_catalogo(e.app or "", capability)
        risco = FatosDeRisco(side_effect=e.side_effect, human_origin=e.human_origin, tem_catalogo=tem, catalogo=fatos,
                             sessao_ou_autenticacao=toca_sessao_ou_autenticacao(source_kind=source_kind,
                                                                                tela_autenticada=tela_autenticada),
                             reaprendido=e.reaprendido is not None, etapas=self._risco.etapas(e, d.conteudo))
        identidade = IdentidadeDoItem(kind=e.kind.value, ref=e.ref, app=e.app or "", capability=capability,
                                      app_version=e.app_version, estado=None if e.state is None else e.state.value,
                                      origem=e.origin.value, side_effect=e.side_effect, human_origin=e.human_origin,
                                      criado_em=e.created_at, apps=self._apps(e, d.conteudo),
                                      sem_caminho=self._sem_caminho(e))
        trilha = [PassoDaTrilha(id=t.id, para=t.to_state.value, em=t.decided_at,
                                de=None if t.from_state is None else t.from_state.value,
                                por_pessoa=t.decided_by != SYSTEM_ACTOR, run_id=t.run_id) for t in d.trilha]
        relacoes = [Relacao(tipo=str(r.get("tipo")), kind=str(r.get("kind")), ref=str(r.get("ref")))
                    for r in d.relacoes if isinstance(r, dict) and r.get("kind") and r.get("ref")]
        invalidas = frozenset(r for t in d.trilha if (r := run_invalidada(t.reason)) is not None)
        todas = self._evidencias(e.trail_ref)
        # 30.36: o `against` que tem a `forma` da mesma origem saiu do contra (`promocao.efetivas`): o curador não o vê
        formas = {x.origin_ref for x in todas if x.posicao == "forma"}
        evidencias = [x for x in todas if (x.run_id is None or x.run_id not in invalidas)
                      and not (x.posicao == "against" and x.origin_ref in formas)]
        return montar_dossie(identidade, risco, d.conteudo, evidencias=evidencias, trilha=trilha,
                             relacoes=relacoes, saude=_saude(d.saude), versao=d.versao,
                             max_evidencias=MAX_EVIDENCIAS if max_evidencias is None else max_evidencias)


__all__ = ["DossiesSql"]
