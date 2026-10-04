"""Do grupo de falha ao diagnóstico determinístico (item 30.13, `docs/design/aprendizado-vivo.md` §9.1). Puro.

Três saídas, todas DADO para a pessoa e para o curador (nada aqui executa, rebaixa ou chama IA):

- **conhecimento envolvido**: o que estava em jogo nas tentativas do grupo (receita que conduziu, lições expostas, tela
  da falha, fluxo e habilidade da execução), com a referência citável (`receita:12`, `li-…`, `tela:<app>/<nome>`,
  `fluxo:<id>`, `habilidade:<id>@<v>`) e `aproximado` quando a junção não é exata (a receita sem `attempts.recipe_id`
  é achada por (app, `template_hash`));
- **`CausaProvavel`**, de um vocabulário fechado, com os FATOS que a sustentam. A ordem das regras importa e é esta:
  o tipo da falha decide as causas que não dependem de contexto (teto, provedor, sessão, aparelho, plano, pessoa,
  catálogo, verificador); só os tipos de navegação e de conhecimento olham as tentativas — receita (e dela a versão
  nova ou o aparelho), lição, tela, falta de conhecimento. O que nenhuma regra sustenta é `indeterminada`, escrito,
  e é o que a revisão por IA (item 30.11) lê; aqui não há chamada, prompt nem porta de IA;
- **proposta**: o tipo, o alvo (ref) e a causa. Vira linha do backlog (`parent_id` = o grupo) por `proposta_do_diagnostico`.

Falha ou incerteza nunca viram uma causa por palpite: sem contexto (fonte ausente, tentativa fora do banco) o
resultado é `indeterminada` com o fato `sem_contexto`.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.modules.learning.domain.backlog import (QUALQUER, ChaveDoGrupo, Proposta, TipoDeVerificacao, titulo)
from app.modules.learning.domain.falhas import Camada, FailureKind, camada_de
from app.modules.learning.domain.vocabulario import CausaProvavel, TipoDeProposta

#: Quantas tentativas de cada grupo o diagnóstico lê (as mais recentes): o custo de ler fica no tamanho da amostra.
AMOSTRA = 30
#: Uma lição só é acusada exposta em pelo menos tantas etapas FALHAS do grupo, e com pelo menos tantas unidades em cada
#: braço — bem abaixo do veredito oficial (8 por braço, `efeito.py`): é indício para a pessoa olhar, não decisão.
LICAO_ETAPAS_MIN = 3
LICAO_UNIDADES_MIN = 3
#: Quanto a taxa de falha com a lição precisa passar da do controle para ela ser acusada (pontos percentuais / 100).
LICAO_DIFERENCA_MIN = 0.10

#: Os tipos de falha em que olhar o contexto (receita, lição, tela) adianta: navegação, conduta do ator e coleta.
_DE_CONHECIMENTO = frozenset({
    FailureKind.IA_CHAMADA_INVALIDA, FailureKind.IA_DECLAROU_BLOQUEIO, FailureKind.CICLO_SEM_PROGRESSO,
    FailureKind.ALVO_AUSENTE, FailureKind.EFEITO_ALVO_ERRADO, FailureKind.COLETA_VAZIA, FailureKind.COLETA_INCOMPLETA})
#: Dos de conhecimento, os que dependem de a tela ter sido reconhecida (a `tela` vazia é a falha numa tela sem regra).
_DEPENDE_DA_TELA = frozenset({FailureKind.ALVO_AUSENTE, FailureKind.COLETA_VAZIA, FailureKind.COLETA_INCOMPLETA,
                              FailureKind.CICLO_SEM_PROGRESSO})
_DA_RECEITA = frozenset({"recipe", "recipe+ai"})
_DO_PROVEDOR = frozenset({FailureKind.IA_INDISPONIVEL, FailureKind.IA_RECUSA, FailureKind.IA_SALDO})
_DE_SESSAO = frozenset({FailureKind.AUTENTICACAO, FailureKind.CONTA_ERRADA})
_DE_VERIFICACAO = frozenset({FailureKind.EFEITO_NAO_COMPROVADO, FailureKind.POS_CONDICAO_NAO_COMPROVADA})
_DO_AMBIENTE = frozenset({Camada.APARELHO, Camada.AUTOMACAO, Camada.EXECUCAO})


class PapelDoConhecimento(StrEnum):
    CONDUZIU = "conduziu"                               # a receita que a etapa reproduziu
    EXPOSTA = "exposta"                                 # a lição que foi ao prompt (braço `with`)
    TELA_DA_FALHA = "tela_da_falha"                     # a tela do pacote onde a tentativa parou
    FLUXO_DA_EXECUCAO = "fluxo_da_execucao"
    HABILIDADE_DA_EXECUCAO = "habilidade_da_execucao"


# ------------------------------------------------------------------ o que a infraestrutura lê (fatos, sem texto de tela)
@dataclass(frozen=True, slots=True)
class ReceitaDaTentativa:
    """A receita da etapa. `aproximada`: achada por (app, `template_hash`) porque a tentativa não gravou o
    `recipe_id` (só a 045 em diante grava, e só quando a receita conduziu)."""

    ref: str                    # 'receita:<id>'
    status: str
    app_version: str
    estado_de_versao: str       # `EstadoDeVersao.value` (domain/versao.py)
    aproximada: bool


@dataclass(frozen=True, slots=True)
class TelaDaTentativa:
    """A tela do pacote onde a tentativa parou. `item` e `estado` só quando ela é uma tela aprendida do livro."""

    ref: str                    # 'tela:<pacote>/<nome>'
    item: str | None = None     # 'li-…'
    estado: str | None = None


@dataclass(frozen=True, slots=True)
class ContextoDaTentativa:
    attempt_id: str
    step_id: str
    run_id: str
    instance_id: str | None
    driven_by: str | None
    estrategia: str | None
    capability: str | None
    receita: ReceitaDaTentativa | None
    licoes: tuple[str, ...]                     # refs `li-…` expostas (braço `with`) NESTA etapa
    tela: TelaDaTentativa | None
    fluxo: str | None
    habilidade: str | None
    #: Aparelhos DA MESMA execução onde a mesma etapa (`template_hash`) terminou bem / falhou: a regra de
    #: `taskqueue/aproveitamento.py` (ok noutro aparelho = aparelho; falha em todos = receita).
    outros_ok: int = 0
    outros_falha: int = 0
    chamou_pessoa: bool = False                 # `tela_desconhecida_chamou_pessoa` ligado a esta tentativa ou etapa

    @property
    def conduzida_por_receita(self) -> bool:
        return (self.driven_by in _DA_RECEITA) or ("recipe" in (self.estrategia or "").split(">"))


@dataclass(frozen=True, slots=True)
class EstatisticaDaLicao:
    """A lição no braço de controle (todas as exposições já preenchidas da lição, não só as do grupo)."""

    ref: str
    estado: str
    detalhe: str | None
    com: int
    com_falhas: int
    controle: int
    controle_falhas: int

    @property
    def taxa_com(self) -> float | None:
        return self.com_falhas / self.com if self.com else None

    @property
    def taxa_controle(self) -> float | None:
        return self.controle_falhas / self.controle if self.controle else None


# ------------------------------------------------------------------ o que sai
@dataclass(frozen=True, slots=True)
class Fato:
    codigo: str
    valor: str


@dataclass(frozen=True, slots=True)
class ConhecimentoEnvolvido:
    ref: str
    kind: str                       # receita | licao | tela | fluxo | habilidade
    papel: PapelDoConhecimento
    etapas: int                     # etapas distintas do grupo em que ele esteve em jogo
    aproximado: bool
    estado: str | None = None


@dataclass(frozen=True, slots=True)
class PropostaDoDiagnostico:
    tipo: TipoDeProposta
    alvo: str
    causa: CausaProvavel


@dataclass(frozen=True, slots=True)
class Diagnostico:
    causa: CausaProvavel
    fatos: tuple[Fato, ...]
    conhecimento: tuple[ConhecimentoEnvolvido, ...]
    proposta: PropostaDoDiagnostico | None
    #: Tentativas do grupo de que o contexto foi lido (0 = só o tipo decidiu, ou não havia fonte).
    amostra: int

    @property
    def indeterminada(self) -> bool:
        return self.causa is CausaProvavel.INDETERMINADA


# ------------------------------------------------------------------ conhecimento envolvido
def _etapas(contextos: Sequence[ContextoDaTentativa], pega: Callable[[ContextoDaTentativa], object]) -> int:
    return len({c.step_id for c in contextos if pega(c)})


def conhecimento_envolvido(chave: ChaveDoGrupo, contextos: Sequence[ContextoDaTentativa]) -> tuple[ConhecimentoEnvolvido, ...]:
    """O que esteve em jogo nas tentativas lidas, do mais presente ao menos. A chave vem da referência citável; a
    contagem é de ETAPAS distintas (a mesma etapa que falha três vezes é uma)."""
    saida: list[ConhecimentoEnvolvido] = []
    receitas: dict[str, ReceitaDaTentativa] = {c.receita.ref: c.receita for c in contextos if c.receita}
    for ref, r in receitas.items():
        saida.append(ConhecimentoEnvolvido(ref, "receita", PapelDoConhecimento.CONDUZIU,
                                           _etapas(contextos, lambda c, ref=ref: c.receita is not None and c.receita.ref == ref),
                                           r.aproximada, r.estado_de_versao))
    for ref in sorted({ref for c in contextos for ref in c.licoes}):
        saida.append(ConhecimentoEnvolvido(ref, "licao", PapelDoConhecimento.EXPOSTA,
                                           _etapas(contextos, lambda c, ref=ref: ref in c.licoes), False))
    telas = {c.tela.ref: c.tela for c in contextos if c.tela}
    for ref, t in telas.items():
        saida.append(ConhecimentoEnvolvido(ref, "tela", PapelDoConhecimento.TELA_DA_FALHA,
                                           _etapas(contextos, lambda c, ref=ref: c.tela is not None and c.tela.ref == ref), False,
                                           t.estado))
        if t.item:
            saida.append(ConhecimentoEnvolvido(t.item, "tela", PapelDoConhecimento.TELA_DA_FALHA,
                                               _etapas(contextos, lambda c, ref=ref: c.tela is not None and c.tela.ref == ref),
                                               False, t.estado))
    for ref in sorted({c.fluxo for c in contextos if c.fluxo}):
        saida.append(ConhecimentoEnvolvido(ref, "fluxo", PapelDoConhecimento.FLUXO_DA_EXECUCAO,
                                           _etapas(contextos, lambda c, ref=ref: c.fluxo == ref), False))
    for ref in sorted({c.habilidade for c in contextos if c.habilidade}):
        saida.append(ConhecimentoEnvolvido(ref, "habilidade", PapelDoConhecimento.HABILIDADE_DA_EXECUCAO,
                                           _etapas(contextos, lambda c, ref=ref: c.habilidade == ref), False))
    return tuple(sorted(saida, key=lambda k: (-k.etapas, k.kind, k.ref)))


# ------------------------------------------------------------------ as regras
def _catalogo(chave: ChaveDoGrupo) -> str:
    return f"catalogo:{chave.app}/{chave.capability}"


def _investigar(chave: ChaveDoGrupo, causa: CausaProvavel) -> PropostaDoDiagnostico:
    return PropostaDoDiagnostico(TipoDeProposta.INVESTIGAR, f"grupo:{chave.id}", causa)


def _pelo_tipo(chave: ChaveDoGrupo, erros_de_ia: Mapping[str, int]) -> tuple[CausaProvavel, tuple[Fato, ...],
                                                                            PropostaDoDiagnostico | None] | None:
    """As causas que o TIPO da falha já decide. `None`: o tipo pede o contexto das tentativas."""
    tipo = chave.tipo
    if tipo in {t.value for t in TipoDeVerificacao}:
        return (CausaProvavel.VERIFICADOR, (Fato("tipo", tipo),), _investigar(chave, CausaProvavel.VERIFICADOR))
    try:
        falha = FailureKind(tipo)
    except ValueError:
        return CausaProvavel.INDETERMINADA, (Fato("tipo_fora_do_vocabulario", tipo),), None
    if falha is FailureKind.IA_ORCAMENTO:
        # O tipo (`AIError.kind == 'budget'`, de `ai_calls.error_kind`) é o fato; o texto do executor é o legado.
        via = f"tipo (ai_calls.error_kind=budget em {erros_de_ia['budget']} chamada(s))" if erros_de_ia.get("budget") \
            else "texto do erro (legado; sem ai_calls da tentativa)"
        return CausaProvavel.TETO_DE_IA, (Fato("tipo", tipo), Fato("via", via)), None
    if falha in _DO_PROVEDOR:
        return CausaProvavel.PROVEDOR_DE_IA, (Fato("tipo", tipo),), None
    if falha in _DE_SESSAO:
        return CausaProvavel.SESSAO_OU_AUTENTICACAO, (Fato("tipo", tipo),), None
    if camada_de(falha) in _DO_AMBIENTE:
        return CausaProvavel.APARELHO, (Fato("tipo", tipo), Fato("camada", camada_de(falha).value)), None
    if falha in (FailureKind.DEFEITO_DO_PLANO, FailureKind.SELETOR_EM_ELEMENTOS_DIFERENTES):
        return CausaProvavel.PLANO, (Fato("tipo", tipo),), None
    if falha is FailureKind.FALTA_INFORMACAO:
        return CausaProvavel.INFORMACAO_DA_PESSOA, (Fato("tipo", tipo),), None
    if falha is FailureKind.EFEITO_GUARDA_NAO_ATENDIDA:
        return (CausaProvavel.CATALOGO_RECUSOU, (Fato("tipo", tipo), Fato("acao", chave.capability)),
                PropostaDoDiagnostico(TipoDeProposta.AJUSTAR_CATALOGO, _catalogo(chave), CausaProvavel.CATALOGO_RECUSOU))
    if falha in _DE_VERIFICACAO:
        return CausaProvavel.VERIFICADOR, (Fato("tipo", tipo),), _investigar(chave, CausaProvavel.VERIFICADOR)
    if falha is FailureKind.OUTRO:
        return (CausaProvavel.INDETERMINADA, (Fato("sem_regra_para_o_texto", "o classificador devolveu `outro`"),),
                _investigar(chave, CausaProvavel.INDETERMINADA))
    if falha in _DE_CONHECIMENTO:
        return None
    return CausaProvavel.INDETERMINADA, (Fato("tipo_sem_regra", tipo),), _investigar(chave, CausaProvavel.INDETERMINADA)


def _da_receita(chave: ChaveDoGrupo, contextos: Sequence[ContextoDaTentativa],
                conduzidas: Sequence[ContextoDaTentativa]) -> tuple[CausaProvavel, tuple[Fato, ...],
                                                                    PropostaDoDiagnostico | None]:
    """A maioria das tentativas foi conduzida por receita. Ordem: a versão nova (receita quarentenada onde a anterior
    estava comprovada), o aparelho (a MESMA etapa terminou bem noutro aparelho da execução), a receita (falhou em todos
    os que tentaram, ou a própria receita já está em falha) e, sem comparação, `indeterminada`."""
    fatos = [Fato("conduzidas_por_receita", f"{len(conduzidas)} de {len(contextos)} tentativas lidas")]
    receitas = Counter(c.receita.ref for c in conduzidas if c.receita)
    mais = receitas.most_common(1)[0][0] if receitas else None
    estados = {c.receita.ref: c.receita for c in conduzidas if c.receita}
    incompativeis = sorted(ref for ref, r in estados.items() if r.estado_de_versao == "incompativel")
    if incompativeis:
        r = estados[incompativeis[0]]
        return (CausaProvavel.VERSAO_NOVA,
                (*fatos, Fato("receita_incompativel", f"{incompativeis[0]} na versão {r.app_version} do app"),
                 Fato("anterior_comprovada", "havia receita comprovada da mesma chave em versão anterior")),
                PropostaDoDiagnostico(TipoDeProposta.REAPRENDER_TELA, f"app:{chave.app}", CausaProvavel.VERSAO_NOVA))
    com_irma_ok = [c for c in conduzidas if c.outros_ok > 0]
    com_irma_falha = [c for c in conduzidas if c.outros_falha > 0]
    if com_irma_ok and not com_irma_falha:
        return (CausaProvavel.APARELHO,
                (*fatos, Fato("mesma_etapa_ok_noutro_aparelho",
                              f"em {len(com_irma_ok)} tentativa(s), a etapa terminou bem noutro aparelho da execução")),
                None)
    em_falha = [ref for ref, r in estados.items() if r.status == "quarantined" or r.estado_de_versao == "falhando"]
    if mais is not None and not com_irma_ok and (com_irma_falha or em_falha):
        if com_irma_falha:
            fatos.append(Fato("falhou_em_todos_os_aparelhos",
                              f"em {len(com_irma_falha)} tentativa(s), a etapa também falhou nos outros aparelhos"))
        if em_falha:
            fatos.append(Fato("receita_em_falha", ", ".join(sorted(em_falha))))
        return (CausaProvavel.RECEITA_DIVERGIU, (*fatos, Fato("receita", mais)),
                PropostaDoDiagnostico(TipoDeProposta.REBAIXAR_RECEITA, mais, CausaProvavel.RECEITA_DIVERGIU))
    if com_irma_ok and com_irma_falha:
        fatos.append(Fato("comparacao_mista", "a mesma etapa terminou bem em alguns aparelhos e falhou em outros"))
    else:
        fatos.append(Fato("sem_comparacao", "aparelho único na execução e a receita não está em quarentena nem falhando"))
    return CausaProvavel.INDETERMINADA, tuple(fatos), _investigar(chave, CausaProvavel.INDETERMINADA)


def _da_licao(contextos: Sequence[ContextoDaTentativa],
              licoes: Mapping[str, EstatisticaDaLicao]) -> tuple[tuple[Fato, ...], str] | None:
    """A lição mais acusada, ou nada. Devolve `(fatos, ref)`."""
    acusadas: list[tuple[float, str, tuple[Fato, ...]]] = []
    for ref in sorted({r for c in contextos for r in c.licoes}):
        e = licoes.get(ref)
        etapas = _etapas(contextos, lambda c, ref=ref: ref in c.licoes)
        if (e is None or etapas < LICAO_ETAPAS_MIN or e.com < LICAO_UNIDADES_MIN or e.controle < LICAO_UNIDADES_MIN
                or e.taxa_com is None or e.taxa_controle is None):
            continue
        diferenca = e.taxa_com - e.taxa_controle
        if diferenca >= LICAO_DIFERENCA_MIN:
            acusadas.append((diferenca, ref, (
                Fato("licao_exposta_em_etapas_falhas", f"{ref} em {etapas} etapa(s) do grupo"),
                Fato("taxa_de_falha_com_a_licao", f"{e.com_falhas}/{e.com} = {e.taxa_com:.0%}"),
                Fato("taxa_de_falha_do_controle", f"{e.controle_falhas}/{e.controle} = {e.taxa_controle:.0%}"),
                Fato("estado_da_licao", f"{e.estado}{(' ' + e.detalhe) if e.detalhe else ''}"))))
    if not acusadas:
        return None
    _, ref, fatos = max(acusadas, key=lambda x: (x[0], x[1]))
    return fatos, ref


def diagnosticar(chave: ChaveDoGrupo, contextos: Sequence[ContextoDaTentativa], *,
                 licoes: Mapping[str, EstatisticaDaLicao] | None = None,
                 erros_de_ia: Mapping[str, int] | None = None) -> Diagnostico:
    """O diagnóstico de um grupo. `contextos` vazio: só o tipo decide (e o conhecimento envolvido sai vazio)."""
    contextos = tuple(contextos)
    licoes = licoes or {}
    conhecimento = conhecimento_envolvido(chave, contextos)
    decidido = _pelo_tipo(chave, erros_de_ia or {})
    if decidido is not None:
        causa, fatos, proposta = decidido
        return Diagnostico(causa, fatos, conhecimento, proposta, len(contextos))
    # Daqui em diante o tipo é de navegação ou de conhecimento: o contexto das tentativas decide.
    if not contextos:
        return Diagnostico(CausaProvavel.INDETERMINADA, (Fato("sem_contexto", "nenhuma tentativa lida do banco"),),
                           conhecimento, _investigar(chave, CausaProvavel.INDETERMINADA), 0)
    conduzidas = [c for c in contextos if c.conduzida_por_receita]
    if len(conduzidas) * 2 >= len(contextos):
        causa, fatos, proposta = _da_receita(chave, contextos, conduzidas)
        return Diagnostico(causa, fatos, conhecimento, proposta, len(contextos))
    acusada = _da_licao(contextos, licoes)
    if acusada is not None:
        fatos, ref = acusada
        return Diagnostico(CausaProvavel.LICAO_ATRAPALHA, fatos, conhecimento,
                           PropostaDoDiagnostico(TipoDeProposta.REVISAR_LICAO, ref, CausaProvavel.LICAO_ATRAPALHA),
                           len(contextos))
    falha = FailureKind(chave.tipo)
    chamou = [c for c in contextos if c.chamou_pessoa]
    sem_tela = not chave.tela and falha in _DEPENDE_DA_TELA
    if chamou or sem_tela:
        fatos = [Fato("tela", "a falha caiu fora de toda tela declarada do app" if not chave.tela else chave.tela)]
        if chamou:
            fatos.append(Fato("tela_desconhecida_chamou_pessoa", f"em {len(chamou)} tentativa(s) lida(s)"))
        return (Diagnostico(CausaProvavel.TELA_DESCONHECIDA, tuple(fatos), conhecimento,
                            PropostaDoDiagnostico(TipoDeProposta.REAPRENDER_TELA,
                                                  f"tela:{chave.app}/{chave.tela or 'desconhecida'}",
                                                  CausaProvavel.TELA_DESCONHECIDA), len(contextos)))
    so_ia = [c for c in contextos if c.driven_by in (None, "ai") and c.receita is None and not c.licoes]
    if len(so_ia) * 2 >= len(contextos):
        capacidade = "etapa livre (sem ação do catálogo)" if chave.capability == QUALQUER else chave.capability
        return Diagnostico(
            CausaProvavel.FALTA_CONHECIMENTO,
            (Fato("so_a_ia_conduziu", f"{len(so_ia)} de {len(contextos)} tentativas, sem receita nem lição exposta"),
             Fato("acao", capacidade)),
            conhecimento, _investigar(chave, CausaProvavel.FALTA_CONHECIMENTO), len(contextos))
    return Diagnostico(
        CausaProvavel.INDETERMINADA,
        (Fato("sinais_insuficientes", f"{len(conduzidas)} por receita, {len(so_ia)} só da IA, "
                                      f"{sum(1 for c in contextos if c.licoes)} com lição, de {len(contextos)} tentativas"),),
        conhecimento, _investigar(chave, CausaProvavel.INDETERMINADA), len(contextos))


# ------------------------------------------------------------------ a proposta como linha do backlog
_VERBO: Mapping[TipoDeProposta, str] = {
    TipoDeProposta.REBAIXAR_RECEITA: "rebaixar", TipoDeProposta.REVISAR_LICAO: "revisar",
    TipoDeProposta.REAPRENDER_TELA: "reaprender", TipoDeProposta.AJUSTAR_CATALOGO: "ajustar",
    TipoDeProposta.INVESTIGAR: "investigar"}


def proposta_do_diagnostico(chave: ChaveDoGrupo, d: Diagnostico) -> Proposta | None:
    """A proposta do diagnóstico como `Proposta` do backlog: `ref` = grupo + alvo (um grupo diferente que acuse o mesmo
    alvo é outra linha), `parent_id` = o grupo (§9.1.4). Sem `fragmento`: não há o que commitar, só o que olhar."""
    p = d.proposta
    if p is None:
        return None
    fatos = "; ".join(f"{f.codigo}: {f.valor}" for f in d.fatos)
    return Proposta(tipo=p.tipo, ref=f"{chave.id}|{p.alvo}", app=chave.app,
                    titulo=f"{titulo(chave)} → {_VERBO[p.tipo]} {p.alvo}",
                    detalhe=f"causa provável {p.causa.value} — {fatos}", fragmento="", alvo=p.alvo,
                    causa=p.causa.value, parent_id=chave.id)


__all__ = ["AMOSTRA", "LICAO_DIFERENCA_MIN", "LICAO_ETAPAS_MIN", "LICAO_UNIDADES_MIN", "ConhecimentoEnvolvido",
           "ContextoDaTentativa", "Diagnostico", "EstatisticaDaLicao", "Fato", "PapelDoConhecimento",
           "PropostaDoDiagnostico", "ReceitaDaTentativa", "TelaDaTentativa", "conhecimento_envolvido", "diagnosticar",
           "proposta_do_diagnostico"]
