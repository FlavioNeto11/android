"""O parecer do curador diante da pessoa (item 30.17, `aprendizado-vivo.md` §8.8, §8.9 e §11.2): quando ele aparece no
painel, o que aceitá-lo FAZ e como a decisão da pessoa vira rótulo. Puro: recebe a revisão já lida e as ações que a
pessoa pode dar (`livro.acoes_da_pessoa`) e devolve rótulos; quem lê e grava `learning_reviews` e transiciona é a
aplicação (`application/pareceres.py`).

Três regras mandam aqui:
- **A decisão da pessoa é o rótulo.** Toda transição de pessoa num item com parecer válido ainda sem decisão, tomada no
  MESMO estado que o parecer viu, grava `decisao_final`. Com o parecer À VISTA (o `review_id` veio, ou o curador está em
  `on`, quando o painel o mostra): `aceitou` quando a pessoa foi para o lado que ele sugeria, `recusou` quando não. Com
  ele oculto (`shadow`, `off`, ou a página legada que não o mostra): o rótulo da própria ação (`aprovar`,
  `rejeitar`...), às cegas. É a concordância do §8.9, que decide a saída do `shadow` (D-3), e por isso o painel nunca
  pode gravar como cega uma decisão que viu o parecer.
- **Em `shadow` e em `off` o parecer pendente não aparece**; só depois da decisão. Mostrá-lo antes ancoraria a pessoa,
  e a concordância deixaria de medir a IA. Em `on`, ele aparece na fila e no detalhe.
- **Aceitar é gesto da pessoa e respeita a classe** (`politica_de_risco.conferir_aceite`): na A o parecer é só
  registro, na C só item a item. Isso vale para o GESTO sobre o parecer (o botão e o lote de pareceres), nunca para a
  pessoa decidir sozinha pelo `/status`. Parecer simulado (provedor falso) nunca é aceito nem recusado: mover um item
  real por opinião de mentira seria pior que não ter opinião.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from app.modules.learning.domain.ciclo import ErroDeAprendizado
from app.modules.learning.domain.curador import (Causa, Confianca, Decisao, Falta, Inconsistencia, Parecer,
                                                 RiscoApontado)
from app.modules.learning.domain.livro import AcaoPermitida
from app.modules.learning.domain.politica_de_risco import ClasseDeRisco, conferir_aceite
from app.modules.learning.domain.vocabulario import Modo
from app.modules.skills.domain.document import JsonValue


class Direcao(StrEnum):
    """Para que lado a decisão leva o item. É a régua da concordância: a pessoa e a IA concordam quando vão para o
    mesmo lado (o passo exato depende do estado, a direção não)."""

    SOBE = "sobe"           # validar, aprovar, reativar
    DESCE = "desce"         # rejeitar, desligar, aposentar
    ESPERA = "espera"       # nenhuma transição agora


_DIRECAO_DA_DECISAO: Mapping[Decisao, Direcao] = {
    Decisao.APROVAR: Direcao.SOBE,
    Decisao.REBAIXAR: Direcao.DESCE, Decisao.DESATIVAR: Direcao.DESCE,
    Decisao.POSSIVELMENTE_OBSOLETO: Direcao.DESCE,
    # Substituir e fundir apontam OUTRO item, que fica no lugar deste: para este, o lado é descer.
    Decisao.SUBSTITUIR: Direcao.DESCE, Decisao.FUNDIR: Direcao.DESCE,
    Decisao.OBSERVAR: Direcao.ESPERA, Decisao.PEDIR_EVIDENCIA: Direcao.ESPERA, Decisao.MANTER: Direcao.ESPERA,
}
#: As chaves de `livro._ROTULO_DO_PASSO` (o que a pessoa pode fazer), pelo lado.
_DIRECAO_DA_ACAO: Mapping[str, Direcao] = {
    "validar": Direcao.SOBE, "aprovar": Direcao.SOBE, "reativar": Direcao.SOBE,
    "rejeitar": Direcao.DESCE, "desligar": Direcao.DESCE, "aposentar": Direcao.DESCE,
    # "Confirmar que fica" (30.24) não move o item: concorda com manter, observar e pedir evidência.
    "confirmar": Direcao.ESPERA,
}
#: O passo que ACEITAR o parecer dá, pela ordem de preferência entre as ações que a pessoa pode dar agora. Descer é
#: desligar ou rejeitar (a pessoa reativa depois), nunca aposentar: a receita aposentada não volta. Substituir e fundir
#: não estão aqui: aceitar é concordar, e quem age no alvo é a pessoa, pelo link da relação.
_PASSO_DO_ACEITE: Mapping[Decisao, tuple[str, ...]] = {
    Decisao.APROVAR: ("aprovar", "validar", "reativar"),
    Decisao.REBAIXAR: ("desligar", "rejeitar"), Decisao.DESATIVAR: ("desligar", "rejeitar"),
    Decisao.POSSIVELMENTE_OBSOLETO: ("desligar", "rejeitar"),
}


def direcao(decisao: Decisao) -> Direcao:
    return _DIRECAO_DA_DECISAO[decisao]


def direcao_da_acao(rotulo: str) -> Direcao | None:
    """`None`: rótulo fora do vocabulário das ações da pessoa (nunca concorda com nada)."""
    return _DIRECAO_DA_ACAO.get(rotulo)


def acao_do_aceite(decisao: Decisao, acoes: Iterable[AcaoPermitida]) -> AcaoPermitida | None:
    """A transição que aceitar o parecer faz no estado atual, ou `None` quando aceitar é só concordar (esperar, manter,
    substituir, fundir, ou nenhuma das ações do lado sugerido está aberta agora: aprovar o que já está publicado)."""
    por_rotulo = {a.rotulo: a for a in acoes}
    return next((por_rotulo[r] for r in _PASSO_DO_ACEITE.get(decisao, ()) if r in por_rotulo), None)


def parecer_visivel(modo: Modo, *, decidido: bool) -> bool:
    """Em `on`, sempre. Fora dele, só o já decidido: o pendente ancoraria a pessoa (e, em `off`, não há curador para
    dar o parecer de agora)."""
    return modo is Modo.ON or decidido


# ------------------------------------------------------------------ o rótulo da decisão
class DecisaoFinal(StrEnum):
    """`learning_reviews.decisao_final` quando a pessoa VIU o parecer. Às cegas, a coluna leva o rótulo da ação."""

    ACEITOU = "aceitou"
    RECUSOU = "recusou"


@dataclass(frozen=True, slots=True)
class DecisaoDaPessoa:
    decisao_final: str
    override: bool


def decisao_pela_transicao(sugerida: Decisao, rotulo: str, *, viu: bool) -> DecisaoDaPessoa:
    """O rótulo de uma transição que a pessoa deu num item com parecer pendente. `viu`: o parecer estava à vista."""
    concorda = direcao_da_acao(rotulo) is direcao(sugerida)
    if viu:
        return DecisaoDaPessoa(DecisaoFinal.ACEITOU.value if concorda else DecisaoFinal.RECUSOU.value, not concorda)
    return DecisaoDaPessoa(rotulo, not concorda)


# ------------------------------------------------------------------ a revisão gravada, como o painel a lê
@dataclass(frozen=True, slots=True)
class RevisaoGravada:
    """Uma linha de `learning_reviews` já lida. `parecer`: o da `saida` quando `validade = ok`; `estado_no_parecer`: o
    estado do item que o dossiê viu (`dossie.item.estado`)."""

    id: str
    criado_em: str
    item_ref: str
    item_kind: str
    gatilho: str
    validade: str
    classe: ClasseDeRisco | None
    politica: str | None
    simulated: bool
    provedor: str
    modelo: str
    estado_no_parecer: str | None
    parecer: Parecer | None
    decisao_final: str | None = None
    decidido_por: str | None = None
    transicao_id: int | None = None
    override: bool = False
    override_motivo: str | None = None

    @property
    def decidida(self) -> bool:
        return self.decisao_final is not None

    @property
    def classe_efetiva(self) -> ClasseDeRisco | None:
        """A da política no registro, endurecida pela faixa que a IA apontou (nunca afrouxada; na A, só registro)."""
        if self.classe is None:
            return None
        return self.parecer.faixa_efetiva(self.classe) if self.parecer is not None else self.classe

    def pendente_em(self, estado: str | None) -> bool:
        """Válida, sem decisão e sobre o estado em que o item está: é o parecer que uma decisão de agora responde."""
        return (self.parecer is not None and self.validade == "ok" and not self.decidida
                and estado is not None and self.estado_no_parecer == estado)


class RecusaDoGesto(StrEnum):
    """Por que o gesto sobre o parecer (aceitar ou recusar) não vale. As da classe vêm de `RecusaDoAceite`."""

    INVALIDO = "parecer_invalido"           # a resposta foi inválida ou recusada: não há parecer
    JA_DECIDIDO = "parecer_ja_decidido"
    DESATUALIZADO = "parecer_desatualizado"  # o item mudou de estado desde o parecer
    SIMULADO = "parecer_simulado"
    OCULTO = "parecer_oculto"               # curador fora do `on`: o parecer pendente não aparece para ninguém


#: O que o 409 diz à pessoa, por razão (as da classe vêm de `politica_de_risco.RecusaDoAceite`).
MENSAGEM_DO_GESTO: Mapping[str, str] = {
    RecusaDoGesto.INVALIDO: "Não há parecer para decidir: a resposta da IA foi inválida ou a revisão foi recusada.",
    RecusaDoGesto.JA_DECIDIDO: "Este parecer já foi decidido.",
    RecusaDoGesto.DESATUALIZADO: "O item mudou de estado depois do parecer: decida pelo estado de agora.",
    RecusaDoGesto.SIMULADO: "Parecer simulado (provedor falso) não move item real.",
    RecusaDoGesto.OCULTO: "Com o curador fora do 'on', o parecer pendente não aparece: decida pelo próprio item.",
    "so_registro_na_classe_a": "Na classe A o parecer é só registro: quem decide é a regra determinística.",
    "lote_na_classe_c": "Na classe C o parecer se decide item a item, nunca em lote.",
    "decisao_automatica": "Só uma pessoa aceita parecer.",
    "decisao_automatica_em_c": "Só uma pessoa aceita parecer.",
    "curador_fora_do_on": "Pedir revisão só com o curador ligado ('on'): em 'shadow' ele já revisa sozinho, e o "
                          "parecer só aparece depois da sua decisão.",
}


class GestoRecusado(ErroDeAprendizado):
    """O gesto sobre o parecer não vale agora (409). `code` é a razão (`RecusaDoGesto`, `RecusaDoAceite` ou
    `curador_fora_do_on`); a mensagem é para a pessoa."""

    def __init__(self, codigo: str) -> None:
        super().__init__(MENSAGEM_DO_GESTO.get(codigo, "O parecer não pode ser decidido agora."))
        self.code = codigo


def conferir_gesto(r: RevisaoGravada, *, estado_atual: str | None, modo: Modo, classe: ClasseDeRisco,
                   em_lote: bool) -> str | None:
    """Se a pessoa pode aceitar ou recusar este parecer agora. `classe`: a mais restritiva entre a do registro e a de
    agora (quem chama a calcula: um catálogo que mudou endurece, nunca afrouxa). `None` = pode."""
    if r.parecer is None or r.validade != "ok":
        return RecusaDoGesto.INVALIDO.value
    if r.decidida:
        return RecusaDoGesto.JA_DECIDIDO.value
    if not parecer_visivel(modo, decidido=False):
        return RecusaDoGesto.OCULTO.value
    if r.simulated:
        return RecusaDoGesto.SIMULADO.value
    if not r.pendente_em(estado_atual):
        return RecusaDoGesto.DESATUALIZADO.value
    recusa = conferir_aceite(classe, por_pessoa=True, em_lote=em_lote)
    return None if recusa is None else recusa.value


_ORDEM = {ClasseDeRisco.A: 0, ClasseDeRisco.B: 1, ClasseDeRisco.C: 2}


def mais_restritiva(*classes: ClasseDeRisco | None) -> ClasseDeRisco:
    """A mais restritiva das conhecidas; nenhuma conhecida é C (sem saber, item a item)."""
    conhecidas = [c for c in classes if c is not None]
    return max(conhecidas, key=lambda c: _ORDEM[c]) if conhecidas else ClasseDeRisco.C


# ------------------------------------------------------------------ a `saida` gravada de volta em `Parecer`
def _enum[E: StrEnum](tipo: type[E], valor: JsonValue) -> E | None:
    if valor is None:
        return None
    if not isinstance(valor, str):
        raise ValueError(valor)
    return tipo(valor)


def _enums[E: StrEnum](tipo: type[E], valor: JsonValue) -> tuple[E, ...]:
    if not isinstance(valor, list):
        return ()
    return tuple(tipo(v) for v in valor if isinstance(v, str))


def parecer_gravado(saida: JsonValue) -> Parecer | None:
    """A `saida` de `learning_reviews` (já validada contra o dossiê quando foi gravada, `Parecer.como_dados`) de volta
    em `Parecer`. Linha que não casa com o vocabulário de hoje vira `None` (o painel a mostra como inválida)."""
    if not isinstance(saida, dict):
        return None
    try:
        decisao = _enum(Decisao, saida.get("decisao"))
        if decisao is None:
            return None
        citadas = saida.get("evidencias_citadas")
        prob = saida.get("probabilidade")
        alvo = saida.get("alvo")
        conclusao = saida.get("conclusao")
        return Parecer(decisao=decisao,
                       evidencias_citadas=tuple(c for c in citadas if isinstance(c, str))
                       if isinstance(citadas, list) else (),
                       confianca=_enum(Confianca, saida.get("confianca")),
                       probabilidade=float(prob) if isinstance(prob, (int, float)) else None,
                       alvo=alvo if isinstance(alvo, str) else None,
                       faixa=_enum(ClasseDeRisco, saida.get("faixa")),
                       causa=_enum(Causa, saida.get("causa")),
                       riscos=_enums(RiscoApontado, saida.get("riscos")),
                       inconsistencias=_enums(Inconsistencia, saida.get("inconsistencias")),
                       falta=_enums(Falta, saida.get("falta")),
                       conclusao=conclusao if isinstance(conclusao, str) else None)
    except ValueError:
        return None


__all__ = ["MENSAGEM_DO_GESTO", "DecisaoDaPessoa", "DecisaoFinal", "Direcao", "GestoRecusado", "RecusaDoGesto",
           "RevisaoGravada", "acao_do_aceite", "conferir_gesto", "decisao_pela_transicao", "direcao",
           "direcao_da_acao", "mais_restritiva", "parecer_gravado", "parecer_visivel"]
