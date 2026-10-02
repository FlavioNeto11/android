"""Obsolescência (30.14, §9.2): a leitura dos sinais para o rótulo `obsoleto_provavel` e o passo da curadoria que rebaixa
o que o catálogo não respalda (`catalogo_sem_efeito`).

A regra mora em `domain/obsolescencia.py`; aqui só se junta o que cada fonte já tem. A lista e o detalhe do Livro
chamam a MESMA `ContextoDeObsolescencia.sinais`, com os mesmos insumos, então os dois nunca discordam do rótulo.

Custo: a lista roda isto a cada leitura. As receitas são lidas UMA vez por contexto (vizinha seguinte e quadro de
versão, em lote) e o `conteudo` do 30.3 — que pode varrer `steps` para derivar a capability — só é lido para a receita
ou o fluxo VIVO com `commit` num app cujo catálogo tem alguma ação com efeito. Sem nenhuma (o Outlook hoje), o veredito
não depende da capability e nada é lido.

O rebaixamento passa pelo MESMO caminho de uma pessoa no Livro (`LearningService.mudar_estado` → `_mover_nativo` →
`transicionar_nativo`, com CAS, trilha e o aviso de espera do 30.21), com `by='sistema'` e o motivo estruturado. Não há
caminho novo para mudar status de receita ou fluxo, e nenhuma chamada de IA.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from app.modules.learning.application.ports import FontesDoLivro, RepositorioDeAprendizado
from app.modules.learning.domain.ciclo import SYSTEM_ACTOR, ErroDeAprendizado, SkillState
from app.modules.learning.domain.livro import EntradaDoLivro, entrada_do_item
from app.modules.learning.domain.obsolescencia import (CatalogoDoApp, Respaldo, SinaisDeObsolescencia, Substituta,
                                                       VereditoDoCatalogo, destino_do_rebaixamento,
                                                       do_quadro_de_versao, respaldo_da_receita, respaldo_do_fluxo)
from app.modules.learning.domain.relacoes import VIVOS
from app.modules.learning.domain.vocabulario import KINDS_DE_ITEM, LivroKind, absorvida_em
from app.modules.skills.domain.document import JsonObject

if TYPE_CHECKING:
    from app.modules.learning.application.servico import LearningService

log = logging.getLogger("poc.aprendizado")


class CatalogosDeEfeito(Protocol):
    def catalogo(self, app: str) -> CatalogoDoApp | None:
        """O catálogo ATUAL do app (o que a porta de política aplica na execução), ou `None` sem catálogo."""
        ...


@dataclass(frozen=True, slots=True)
class FatosDaReceita:
    """O que o lote de receitas diz de uma: a versão SEGUINTE da mesma chave (a substituta) e o quadro de versão."""

    seguinte: Substituta | None
    versao: JsonObject | None


class FatosDasReceitas(Protocol):
    def fatos(self) -> Mapping[str, FatosDaReceita]:
        """Por `recipes.id` (texto). Lido em lote: uma consulta das receitas e uma de versões vivas por app."""
        ...


#: O status nativo da receita que ainda pode ser usado ou decidido (o `VIVOS` do livro na língua da fonte).
_STATUS_VIVOS_DA_RECEITA = frozenset({"active", "candidate", "validated"})


class ContextoDeObsolescencia:
    """Uma leitura: os lotes são lidos no primeiro uso e reaproveitados para todas as entradas da mesma chamada."""

    def __init__(self, leitor: LeitorDeObsolescencia) -> None:
        self._leitor = leitor
        self._receitas: Mapping[str, FatosDaReceita] | None = None
        self._filhos: dict[str, list[EntradaDoLivro]] | None = None
        self._catalogos: dict[str, CatalogoDoApp | None] = {}

    def _fatos_da_receita(self, ref: str) -> FatosDaReceita | None:
        if self._receitas is None:
            self._receitas = self._leitor.receitas.fatos()
        return self._receitas.get(ref)

    def _filhos_vivos(self, ref: str) -> list[EntradaDoLivro]:
        if self._filhos is None:
            self._filhos = {}
            for item in self._leitor.repo.itens():
                if item.parent_id:
                    self._filhos.setdefault(item.parent_id, []).append(entrada_do_item(item))
        return [f for f in self._filhos.get(ref, []) if f.state in VIVOS]

    def catalogo(self, app: str | None) -> CatalogoDoApp | None:
        if not app:
            return None
        if app not in self._catalogos:
            self._catalogos[app] = self._leitor.catalogos.catalogo(app)
        return self._catalogos[app]

    def veredito(self, e: EntradaDoLivro) -> VereditoDoCatalogo | None:
        """O catálogo respalda o efeito da receita ou do fluxo VIVO? `None`: não se aplica."""
        if e.kind not in (LivroKind.RECEITA, LivroKind.FLUXO) or e.state not in VIVOS or not e.side_effect:
            return None
        catalogo = self.catalogo(e.app)
        if catalogo is None:
            return None
        # Sem nenhuma ação com efeito no catálogo, a capability não muda o veredito: não se lê o conteúdo.
        conteudo = self._leitor.fontes.conteudo(e.kind, e.ref) if catalogo.tem_efeito else None
        avaliar = respaldo_da_receita if e.kind is LivroKind.RECEITA else respaldo_do_fluxo
        return avaliar(conteudo, catalogo, tem_efeito=e.side_effect)

    def sinais(self, e: EntradaDoLivro, *, versao: JsonObject | None = None) -> SinaisDeObsolescencia | None:
        """Os sinais do §9.2 de um item PUBLICADO (o rótulo só vale para ele, §5.3). `versao`: o quadro do 30.6 que o
        serviço já montou (a tela); a receita usa o do lote."""
        if e.state is not SkillState.PUBLISHED:
            return None
        substituta: Substituta | None = None
        quadro: JsonObject | None = versao
        if e.kind is LivroKind.RECEITA:
            fatos = self._fatos_da_receita(e.ref)
            if fatos is not None:
                quadro = fatos.versao
                if fatos.seguinte is not None and fatos.seguinte.estado in _STATUS_VIVOS_DA_RECEITA:
                    substituta = fatos.seguinte
        elif e.kind in KINDS_DE_ITEM:
            filhos = self._filhos_vivos(e.ref)
            if filhos:
                f = filhos[0]
                substituta = Substituta(f.ref, f.state.value if f.state else "?", "learning_items.parent_id")
        fora, vivas, sem = do_quadro_de_versao(quadro) if e.kind in (LivroKind.RECEITA, LivroKind.TELA) \
            else (None, (), ())
        efeito = self.veredito(e)
        if efeito is not None and efeito.respaldo is Respaldo.RESPALDADO:
            efeito = None
        absorvida = absorvida_em(e.detail) if e.kind in KINDS_DE_ITEM else None
        return SinaisDeObsolescencia(substituta_viva=substituta, versao_fora_do_parque=fora, versoes_vivas=vivas,
                                     versoes_sem_reproducao=sem, efeito=efeito, absorvida_em=absorvida)


class LeitorDeObsolescencia:
    """Pendurado no serviço pela composição (`LearningService.anexar`); sem ele, a saúde não tem sinal de obsolescência."""

    def __init__(self, fontes: FontesDoLivro, repo: RepositorioDeAprendizado, catalogos: CatalogosDeEfeito,
                 receitas: FatosDasReceitas) -> None:
        self.fontes = fontes
        self.repo = repo
        self.catalogos = catalogos
        self.receitas = receitas

    def contexto(self) -> ContextoDeObsolescencia:
        return ContextoDeObsolescencia(self)


class RebaixamentoPorCatalogo:
    """`PassoDeCuradoria` (a cada `aprendizado.curadoria_s`): rebaixa a receita ou o fluxo vivo com `commit` que o
    catálogo atual não respalda. Determinístico e idempotente: só olha o que está vivo, e o rebaixado já não está —
    a segunda rodada não acha nada, nem transição nem evento. Uma linha recusada (CAS, veto, tabela) vira log e as
    outras seguem; reativar é de pessoa."""

    nome = "catalogo_sem_efeito"

    def __init__(self, servico: LearningService, leitor: LeitorDeObsolescencia) -> None:
        self._servico = servico
        self._leitor = leitor

    def executar(self, agora: datetime) -> int:
        contexto = self._leitor.contexto()
        rebaixados = 0
        for e in self._candidatos():
            veredito = contexto.veredito(e)
            if veredito is None or not veredito.rebaixa:
                continue
            destino = destino_do_rebaixamento(e.kind, e.state)
            if destino is None:
                continue
            try:
                self._servico.mudar_estado(e.kind, e.ref, destino, by=SYSTEM_ACTOR, reason=veredito.motivo)
            except ErroDeAprendizado as erro:
                log.warning("aprendizado: %s %s não rebaixado por %s: %s", e.kind.value, e.ref, veredito.motivo, erro)
                continue
            rebaixados += 1
        return rebaixados

    def _candidatos(self) -> Sequence[EntradaDoLivro]:
        fontes = self._leitor.fontes
        return [e for e in (*fontes.receitas(), *fontes.fluxos())
                if e.state in VIVOS and e.side_effect and e.app]


__all__ = ["CatalogosDeEfeito", "ContextoDeObsolescencia", "FatosDaReceita", "FatosDasReceitas",
           "LeitorDeObsolescencia", "RebaixamentoPorCatalogo"]
