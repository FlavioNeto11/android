"""Os estágios de um alvo da operação (prova de 07/10, J2, adendo v1.94), no vocabulário do dono e nesta ordem fixa.

Um alvo é persona + conta + aparelho numa execução própria. O estágio é DERIVADO do que a execução já grava (etapas,
rascunho, pedido de aprovação, efeito e verificação), sem gancho no meio do despacho: quem lê é a operação, na hora da
leitura. Só duas coisas são gravadas de fora: a parada na criação (`conta`, `sessao`, `aparelho`, teto de custo) e as
marcas de quem conhece um estágio que a execução não grava (o conhecimento recuperado, pela frente de aprendizado).

Nunca se infere um estágio por um posterior: `estagios` pode ter lacuna (o rascunho lê a tela DEPOIS de a interface de
comentário abrir, e o conhecimento só existe se alguém o marcou). Puro: sem banco, sem relógio, sem nome de app.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

#: A ordem do dono (06/10, `pedido-do-dono-06-10-d.md`). `acao_executada` e `acao_bloqueada` são o mesmo degrau.
ESTAGIOS: tuple[str, ...] = (
    "persona", "conta", "sessao", "aparelho", "app_aberto", "target_localizado", "post_localizado", "conteudo_lido",
    "conhecimento_recuperado", "resposta_gerada", "interface_de_comentario_alcancada", "acao_preparada",
    "acao_executada", "acao_bloqueada", "resultado_verificado")
#: Os estágios que a ação do catálogo de um app pode declarar (`operacao.estagios` no `app.yaml`).
ESTAGIOS_DE_APP: frozenset[str] = frozenset({"target_localizado", "post_localizado", "conteudo_lido",
                                             "interface_de_comentario_alcancada"})
#: Os que vêm de fora da execução (`registrar_estagio`).
ESTAGIOS_MARCAVEIS: frozenset[str] = frozenset({"conhecimento_recuperado", "conteudo_lido"})
ESTADOS: tuple[str, ...] = ("pendente", "em_curso", "concluido", "bloqueado", "cancelado")


def ordem(estagio: str, abertura: str = "app_aberto") -> int:
    """A posição do estágio na ordem do dono; o rótulo de abertura do app (`instagram_aberto`) ocupa a de `app_aberto`."""
    chave = "app_aberto" if estagio == abertura else estagio
    if chave == "acao_bloqueada":
        chave = "acao_executada"
    return ESTAGIOS.index(chave) if chave in ESTAGIOS else -1


@dataclass(frozen=True, slots=True)
class EtapaLida:
    """O que a leitura precisa de UMA etapa da versão vigente do plano do alvo."""

    capability: str | None
    status: str
    side_effect: bool
    terminou_em: str | None
    comecou_em: str | None
    tem_texto: bool                 # o rascunho fechou (`bindings.content`)
    verificada: bool                # `result.verified`
    pedido_de_aprovacao: str | None  # status do pedido (`pending`, `approved`…), quando há
    #: Quando o pedido de aprovação nasceu: é a hora do rascunho da etapa que ainda não começou (espera o liberar).
    pedido_em: str | None = None


@dataclass(frozen=True, slots=True)
class FatosDoAlvo:
    """Tudo que a derivação lê, já tirado do banco por quem chama."""

    parada_na_criacao: str | None             # o estágio em que o alvo parou ANTES de ganhar execução
    motivo_na_criacao: str | None
    objetivo_status: str | None               # None = sem execução
    objetivo_comecou_em: str | None
    objetivo_motivo: str | None
    run_status: str | None
    etapas: Sequence[EtapaLida] = ()
    marcas: Mapping[str, str] = field(default_factory=dict)   # {estagio: hora} de `registrar_estagio`
    abertura: str = "app_aberto"
    estagio_por_capability: Mapping[str, str] = field(default_factory=dict)
    acao_final: str = "preparar"
    criado_em: str = ""
    #: `objectives.blocked_kind`: `policy` = uma porta (frota, conduta, teto) RECUSOU a etapa, antes de ela rodar.
    objetivo_bloqueio: str | None = None
    #: A execução terminou SEM objetivo (recusada no planejamento: parâmetro em conflito, teto observar, fora do
    #: catálogo): o motivo e a hora do fim. Sem isto o alvo ficava `pendente` para sempre e a operação não fechava.
    recusa_no_plano: str | None = None
    recusa_em: str | None = None


@dataclass(frozen=True, slots=True)
class Leitura:
    estagio: str
    estado: str
    motivo: str | None
    estagios: tuple[tuple[str, str], ...]     # (estagio, hora), na ordem do dono
    #: Onde o alvo PAROU, quando parou (`bloqueado`/`cancelado`): o estágio que não foi alcançado. Na criação é o da
    #: conferência (`conta`, `sessao`…); na execução, o seguinte ao último alcançado, pulando os marcáveis de fora.
    parou_em: str | None = None
    #: Quando a ação final voltou a rodar depois do pedido de aprovação (o início da etapa com efeito liberada). Não é
    #: estágio: separa a espera pela pessoa da latência da ação executada (`domain/latencia.py`).
    liberado_em: str | None = None


_OBJETIVO_FECHADO = {"succeeded", "failed", "cancelled", "uncertain"}


def derivar(f: FatosDoAlvo) -> Leitura:
    """O estágio, o estado e o motivo de um alvo. `estagios` = os alcançados com prova, na ordem do dono."""
    alcancados: dict[str, str] = {"persona": f.criado_em}
    if f.parada_na_criacao is not None:
        # Parou antes de existir execução: os estágios anteriores ao da parada foram conferidos na criação.
        for anterior in ESTAGIOS[1:ESTAGIOS.index(f.parada_na_criacao)] if f.parada_na_criacao in ESTAGIOS else ():
            alcancados[anterior] = f.criado_em
        return Leitura(estagio=_ultimo(alcancados, f.abertura), estado="bloqueado",
                       motivo=f.motivo_na_criacao or f.parada_na_criacao, estagios=_em_ordem(alcancados, f.abertura),
                       parou_em=f.parada_na_criacao)
    for conferido in ("conta", "sessao"):
        alcancados[conferido] = f.criado_em
    if f.objetivo_status is None and f.recusa_no_plano:
        # Nada rodou no aparelho: a ação final foi barrada antes de existir etapa.
        alcancados["acao_bloqueada"] = f.recusa_em or f.criado_em
        return Leitura(estagio="acao_bloqueada", estado="bloqueado", motivo=f.recusa_no_plano,
                       estagios=_em_ordem(alcancados, f.abertura), parou_em="acao_bloqueada")
    if f.objetivo_status is None:
        return Leitura(estagio=_ultimo(alcancados, f.abertura), estado="pendente", motivo=None,
                       estagios=_em_ordem(alcancados, f.abertura))
    if f.objetivo_comecou_em:
        alcancados["aparelho"] = f.objetivo_comecou_em
    # A abertura do app: a primeira etapa que começou a rodar no aparelho (a porta do app passou antes dela).
    inicio = min((e.comecou_em for e in f.etapas if e.comecou_em), default=None)
    if inicio:
        alcancados[f.abertura] = inicio
    efeito_bloqueado: str | None = None
    liberado_em: str | None = None
    for e in f.etapas:
        estagio = f.estagio_por_capability.get(e.capability or "")
        if estagio and e.status == "succeeded" and e.terminou_em:
            alcancados[estagio] = e.terminou_em
        if not e.side_effect:
            continue
        # Cada estágio com a SUA hora (achado do percurso do 57: os quatro saíam com a hora do fim da etapa, que depois
        # do liberar é a da liberação). O rascunho fecha antes do pedido de aprovação, ou dentro da etapa que roda sem
        # pedido; o efeito e a verificação, no fim da etapa.
        rascunho = e.pedido_em or e.comecou_em or e.terminou_em or f.objetivo_comecou_em or f.criado_em
        if e.pedido_de_aprovacao is not None and e.comecou_em:
            liberado_em = e.comecou_em
        if e.tem_texto:
            alcancados["resposta_gerada"] = rascunho
        if e.tem_texto and (e.pedido_de_aprovacao is not None or e.status in ("running", "succeeded", "failed")):
            alcancados["acao_preparada"] = rascunho
        if e.status == "succeeded":
            alcancados["acao_executada"] = e.terminou_em or rascunho
            if e.verificada:
                alcancados["resultado_verificado"] = e.terminou_em or rascunho
        elif e.status in ("failed", "skipped", "cancelled") or e.pedido_de_aprovacao == "rejected":
            efeito_bloqueado = e.status if e.pedido_de_aprovacao != "rejected" else "aprovação recusada"
    for estagio, hora in f.marcas.items():
        if estagio in ESTAGIOS_MARCAVEIS:
            alcancados.setdefault(estagio, hora)
    if (efeito_bloqueado is None and f.objetivo_bloqueio == "policy" and "acao_executada" not in alcancados
            and any(e.side_effect and e.status not in ("succeeded", "running") for e in f.etapas)):
        # A porta recusou a ação final antes de ela rodar (a regra da frota, por exemplo): recusada não é preparada.
        # Sem isto o alvo ficava no último estágio de navegação, com "parou em acao_preparada".
        efeito_bloqueado = "recusada pela política"
    if efeito_bloqueado is not None:
        alcancados.pop("acao_executada", None)
        alcancados["acao_bloqueada"] = max(alcancados.values())
    estado, motivo = _estado(f, alcancados, efeito_bloqueado)
    ultimo = _ultimo(alcancados, f.abertura)
    parou = None
    if estado in ("bloqueado", "cancelado"):
        parou = "acao_bloqueada" if ultimo == "acao_bloqueada" else _seguinte(ultimo, f.abertura)
    return Leitura(estagio=ultimo, estado=estado, motivo=motivo, estagios=_em_ordem(alcancados, f.abertura),
                   parou_em=parou, liberado_em=liberado_em)


def _seguinte(estagio: str, abertura: str) -> str | None:
    """O estágio seguinte na ordem do dono, pulando os que só existem se alguém os marca de fora."""
    for e in ESTAGIOS[ordem(estagio, abertura) + 1:]:
        if e not in ESTAGIOS_MARCAVEIS and e != "acao_bloqueada":
            return abertura if e == "app_aberto" else e
    return None


def _estado(f: FatosDoAlvo, alcancados: Mapping[str, str], efeito_bloqueado: str | None) -> tuple[str, str | None]:
    alvo_final = "acao_preparada" if f.acao_final == "preparar" else "resultado_verificado"
    if alvo_final in alcancados:
        return "concluido", None
    if f.objetivo_status == "cancelled" or f.run_status == "cancelled":
        return "cancelado", f.objetivo_motivo or "cancelado"
    if efeito_bloqueado is not None:
        return "bloqueado", f.objetivo_motivo or f"a ação final terminou {efeito_bloqueado}"
    if f.objetivo_status in _OBJETIVO_FECHADO or f.objetivo_status == "waiting_user":
        return "bloqueado", f.objetivo_motivo or f.objetivo_status
    if f.objetivo_status == "pending" and "aparelho" not in alcancados:
        # 31.173: o motivo da ESPERA (a porta de sessão relendo o aparelho, a vaga), e não só "pendente": era o que a
        # onda 2 precisava ver num alvo parado na sessão.
        return "pendente", f.objetivo_motivo
    return "em_curso", None


def _em_ordem(alcancados: Mapping[str, str], abertura: str) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(alcancados.items(), key=lambda kv: ordem(kv[0], abertura)))


def _ultimo(alcancados: Mapping[str, str], abertura: str) -> str:
    return max(alcancados, key=lambda e: ordem(e, abertura))


def motivo_curto(texto: str | None, limite: int = 120) -> str | None:
    """O motivo como entra na contagem por motivo: uma linha, sem quebra, cortado. A REDAÇÃO é de quem grava
    (`infrastructure/servico.py::_motivo`): o domínio não vê a segurança."""
    if not texto:
        return None
    linha = " ".join(str(texto).split())
    return linha if len(linha) <= limite else linha[: limite - 1] + "…"
