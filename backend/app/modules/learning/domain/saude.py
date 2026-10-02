"""Saúde do item do Livro (30.4, `docs/design/aprendizado-vivo.md` §5): dimensões medidas e UM rótulo derivado por
regra, com os motivos que o produziram. Nenhuma nota de 0 a 100.

Puro: recebe os sinais já lidos (`SinaisDeSaude`) e devolve `Saude`. Quem lê o banco é o serviço; quem monta o JSON é a
apresentação. A lista, o detalhe e as métricas usam esta mesma função, então o painel nunca recalcula taxa nem ordem.

Duas regras mandam aqui. (1) Dimensão sem dado é `desconhecida`, nunca zero: falta de dado não vira boa nem ruim. (2) Falha
ou incerteza nunca contam como saudável: o rótulo `saudavel` exige as TRÊS medidas (uso recente, eficácia ≥ mínimo,
contestação conhecida e zerada); se alguma é desconhecida, o rótulo é `indeterminado` e os motivos dizem o que falta.

O rótulo NÃO é estado e não move nada: quem move é o `ciclo.py` (ou a pessoa).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from app.modules.learning.domain.ciclo import SkillState
from app.modules.learning.domain.vocabulario import LivroKind


class Rotulo(StrEnum):
    INATIVO = "inativo"
    EM_PROVA = "em_prova"
    SEM_EVIDENCIA = "sem_evidencia"
    DEGRADANDO = "degradando"
    OBSOLETO_PROVAVEL = "obsoleto_provavel"
    SAUDAVEL = "saudavel"
    #: Publicado, mas sem medida suficiente para dizer "saudável" nem "degradando". Não está no §5.3: é o que impede que
    #: incerteza vire saudável (e que eficácia fraca estável vire boa).
    INDETERMINADO = "indeterminado"


class Dimensao(StrEnum):
    USO = "uso"
    EFICACIA = "eficacia"
    BASE_DE_EVIDENCIA = "base_de_evidencia"
    FRESCOR = "frescor"
    VERSAO = "versao"
    CONTESTACAO = "contestacao"
    INTERVENCAO_HUMANA = "intervencao_humana"


class CodigoDoMotivo(StrEnum):
    """O vocabulário FECHADO dos motivos (o texto em português é do painel, como em `AcaoPermitida.rotulo`)."""

    # inativo
    DESLIGADO = "desligado"
    APOSENTADO = "aposentado"
    # em_prova
    AGUARDA_REPETICAO = "aguarda_repeticao"
    AGUARDA_O_DONO = "aguarda_o_dono"
    VALIDADO_AGUARDA_PUBLICACAO = "validado_aguarda_publicacao"
    # sem_evidencia
    SEM_USO_E_SEM_EVIDENCIA = "sem_uso_e_sem_evidencia"
    # degradando
    FALHAS_SEGUIDAS = "falhas_seguidas"
    QUEDA_NA_JANELA = "queda_na_janela"
    CONTESTADO_RECENTEMENTE = "contestado_recentemente"
    INTERVENCAO_HUMANA_REPETIDA = "intervencao_humana_repetida"
    # obsoleto_provavel
    SINAL_DE_OBSOLESCENCIA = "sinal_de_obsolescencia"
    # saudavel
    USADO_RECENTEMENTE = "usado_recentemente"
    EFICACIA_ACIMA_DO_MINIMO = "eficacia_acima_do_minimo"
    SEM_CONTESTACAO_RECENTE = "sem_contestacao_recente"
    # indeterminado (o que falta para decidir)
    USO_DESCONHECIDO = "uso_desconhecido"
    SEM_USO_RECENTE = "sem_uso_recente"
    EFICACIA_DESCONHECIDA = "eficacia_desconhecida"
    EFICACIA_ABAIXO_DO_MINIMO = "eficacia_abaixo_do_minimo"
    CONTESTACAO_DESCONHECIDA = "contestacao_desconhecida"
    # item sem saúde calculável (estado fora do ciclo)
    ESTADO_DESCONHECIDO = "estado_desconhecido"


@dataclass(frozen=True, slots=True)
class LimiaresDeSaude:
    """Os limiares do §5.3. Os defaults são os valores iniciais do desenho; o config (`aprendizado.saude`) os troca."""

    sem_uso_dias: int = 14
    janela_usos: int = 10                # usos recentes que a taxa da janela exige para valer
    taxa_minima: float = 0.8
    queda_pp: float = 0.20               # taxa da janela < taxa histórica − isto (0,20 = 20 p.p.)
    contestacao_dias: int = 7
    intervencoes_minimas: int = 2        # intervenção humana na etapa, na janela de contestação


@dataclass(frozen=True, slots=True)
class SinaisDeSaude:
    """Tudo que a regra lê, já medido. `None` = o sinal não existe para este item (vira dimensão `desconhecida`)."""

    kind: LivroKind
    estado: SkillState | None
    agora: datetime
    criado_em: str | None = None
    estado_desde: str | None = None
    ultimo_uso: str | None = None
    usos: int | None = None
    a_favor: int | None = None
    contra: int | None = None
    exige_o_dono: bool = False
    #: Texto da fonte que explica o estado: o que falta (em prova) ou o motivo da trilha (inativo).
    detalhe: str | None = None
    falhas_seguidas: int | None = None
    taxa_na_janela: float | None = None
    usos_na_janela: int | None = None
    contestacoes_recentes: int | None = None
    intervencoes_recentes: int | None = None
    #: Sinais de obsolescência do §9.2 (30.6). `None` = a dimensão Versão ainda não é medida; `()` = medida e sem sinal.
    obsolescencia: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class DimensaoMedida:
    nome: Dimensao
    #: `None` = desconhecida. Número (taxa, dias, contagem) ou texto.
    valor: float | int | str | None
    amostra: int | None
    fonte: str

    @property
    def desconhecida(self) -> bool:
        return self.valor is None


@dataclass(frozen=True, slots=True)
class Motivo:
    codigo: CodigoDoMotivo
    dimensao: Dimensao | None = None
    valor: float | int | str | None = None
    limite: float | int | None = None
    detalhe: str | None = None


@dataclass(frozen=True, slots=True)
class Saude:
    rotulo: Rotulo
    motivos: tuple[Motivo, ...]
    dimensoes: tuple[DimensaoMedida, ...]


_C = CodigoDoMotivo
_D = Dimensao

#: De onde vem cada contador (a fonte nativa dona dele). A evidência vem sempre de `learning_evidence`.
_FONTE_DO_USO: dict[LivroKind, str] = {
    LivroKind.RECEITA: "recipes.replay_ok+replay_fail", LivroKind.FLUXO: "flows.uses",
    LivroKind.HABILIDADE: "sem contador de uso",
}
_FONTE_DA_EVIDENCIA = "learning_evidence"


def _dias_desde(iso: str | None, agora: datetime) -> int | None:
    """Dias inteiros entre `iso` e `agora`; `None` se faltar ou não for uma data (nunca vira zero)."""
    if not iso:
        return None
    try:
        quando = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if quando.tzinfo is None:
        quando = quando.replace(tzinfo=agora.tzinfo)
    return max(0, (agora - quando) // timedelta(days=1))


def _taxa(a_favor: int | None, contra: int | None) -> tuple[float | None, int | None]:
    """(taxa de acerto, amostra). Sem contador ou sem nenhuma tentativa, a taxa é desconhecida, não 0 nem 1."""
    if a_favor is None or contra is None:
        return None, None
    n = a_favor + contra
    return (a_favor / n if n else None), n


def _dimensoes(s: SinaisDeSaude, taxa: float | None, amostra: int | None) -> tuple[DimensaoMedida, ...]:
    contador = _FONTE_DO_USO.get(s.kind, "learning_items")
    ultimo = _dias_desde(s.ultimo_uso, s.agora)
    # Frescor: desde o último uso; nunca usado, desde a criação (o item novo não nasce "velho" por falta de uso).
    desde = ultimo if s.ultimo_uso else _dias_desde(s.criado_em, s.agora)
    base = None if s.a_favor is None or s.contra is None else s.a_favor + s.contra
    return (
        DimensaoMedida(_D.USO, s.usos, s.usos, contador),
        DimensaoMedida(_D.EFICACIA, None if taxa is None else round(taxa, 4), amostra, contador),
        DimensaoMedida(_D.BASE_DE_EVIDENCIA, base, base, _FONTE_DA_EVIDENCIA),
        DimensaoMedida(_D.FRESCOR, desde, None, "last_used_at" if s.ultimo_uso else "created_at"),
        DimensaoMedida(_D.VERSAO, None if s.obsolescencia is None else len(s.obsolescencia), None,
                       "versão do app (§7)"),
        DimensaoMedida(_D.CONTESTACAO, s.contestacoes_recentes, s.contestacoes_recentes, _FONTE_DA_EVIDENCIA),
        DimensaoMedida(_D.INTERVENCAO_HUMANA, s.intervencoes_recentes, s.intervencoes_recentes, "learning_signals"),
    )


def _degradando(s: SinaisDeSaude, taxa: float | None, lim: LimiaresDeSaude) -> list[Motivo]:
    motivos: list[Motivo] = []
    if s.falhas_seguidas is not None and s.falhas_seguidas >= 1:
        motivos.append(Motivo(_C.FALHAS_SEGUIDAS, _D.EFICACIA, s.falhas_seguidas, 1))
    # A janela só vale com amostra cheia e com a taxa histórica conhecida: poucos usos não "derrubam" nada.
    if (s.taxa_na_janela is not None and s.usos_na_janela is not None and s.usos_na_janela >= lim.janela_usos
            and taxa is not None and s.taxa_na_janela < taxa - lim.queda_pp):
        motivos.append(Motivo(_C.QUEDA_NA_JANELA, _D.EFICACIA, round(s.taxa_na_janela, 4),
                              round(taxa - lim.queda_pp, 4)))
    if s.contestacoes_recentes is not None and s.contestacoes_recentes >= 1:
        motivos.append(Motivo(_C.CONTESTADO_RECENTEMENTE, _D.CONTESTACAO, s.contestacoes_recentes, 1,
                              f"últimos {lim.contestacao_dias} dias"))
    if s.intervencoes_recentes is not None and s.intervencoes_recentes >= lim.intervencoes_minimas:
        motivos.append(Motivo(_C.INTERVENCAO_HUMANA_REPETIDA, _D.INTERVENCAO_HUMANA, s.intervencoes_recentes,
                              lim.intervencoes_minimas, f"últimos {lim.contestacao_dias} dias"))
    return motivos


def calcular(s: SinaisDeSaude, lim: LimiaresDeSaude = LimiaresDeSaude()) -> Saude | None:
    """O rótulo e os motivos de um item, pela ordem do §5.3 (o primeiro que casa vence). `None`: a memória, que não
    é item de ciclo (só a contagem sai)."""
    if s.kind is LivroKind.MEMORIA:
        return None
    taxa, amostra = _taxa(s.a_favor, s.contra)
    dims = _dimensoes(s, taxa, amostra)
    estado = s.estado

    if estado is SkillState.DEPRECATED or estado is SkillState.DISABLED:
        codigo = _C.APOSENTADO if estado is SkillState.DEPRECATED else _C.DESLIGADO
        return Saude(Rotulo.INATIVO, (Motivo(codigo, detalhe=s.detalhe),), dims)

    if estado is SkillState.CANDIDATE or estado is SkillState.VALIDATED or estado is SkillState.DRAFT:
        if estado is SkillState.VALIDATED:
            codigo = _C.AGUARDA_O_DONO if s.exige_o_dono else _C.VALIDADO_AGUARDA_PUBLICACAO
        else:
            codigo = _C.AGUARDA_REPETICAO
        return Saude(Rotulo.EM_PROVA, (Motivo(codigo, detalhe=s.detalhe),), dims)

    if estado is not SkillState.PUBLISHED:
        return Saude(Rotulo.INDETERMINADO, (Motivo(_C.ESTADO_DESCONHECIDO),), dims)

    # ---- publicado
    parado = _dias_desde(s.estado_desde or s.criado_em, s.agora)
    if (s.usos == 0 and s.a_favor == 0 and parado is not None and parado >= lim.sem_uso_dias):
        return Saude(Rotulo.SEM_EVIDENCIA, (Motivo(_C.SEM_USO_E_SEM_EVIDENCIA, _D.USO, parado, lim.sem_uso_dias,
                                                   "dias publicado"),), dims)

    degradando = _degradando(s, taxa, lim)
    if degradando:
        return Saude(Rotulo.DEGRADANDO, tuple(degradando), dims)

    if s.obsolescencia:
        return Saude(Rotulo.OBSOLETO_PROVAVEL,
                     tuple(Motivo(_C.SINAL_DE_OBSOLESCENCIA, _D.VERSAO, detalhe=sinal) for sinal in s.obsolescencia),
                     dims)

    # ---- saudável exige as três medidas; o que falta vira motivo de `indeterminado`
    faltas: list[Motivo] = []
    ultimo = _dias_desde(s.ultimo_uso, s.agora)
    if ultimo is None:
        faltas.append(Motivo(_C.USO_DESCONHECIDO, _D.USO))
    elif ultimo >= lim.sem_uso_dias:
        faltas.append(Motivo(_C.SEM_USO_RECENTE, _D.USO, ultimo, lim.sem_uso_dias))
    if taxa is None:
        faltas.append(Motivo(_C.EFICACIA_DESCONHECIDA, _D.EFICACIA))
    elif taxa < lim.taxa_minima:
        faltas.append(Motivo(_C.EFICACIA_ABAIXO_DO_MINIMO, _D.EFICACIA, round(taxa, 4), lim.taxa_minima))
    if s.contestacoes_recentes is None:
        faltas.append(Motivo(_C.CONTESTACAO_DESCONHECIDA, _D.CONTESTACAO))
    if faltas:
        return Saude(Rotulo.INDETERMINADO, tuple(faltas), dims)
    assert ultimo is not None and taxa is not None
    return Saude(Rotulo.SAUDAVEL, (
        Motivo(_C.USADO_RECENTEMENTE, _D.USO, ultimo, lim.sem_uso_dias),
        Motivo(_C.EFICACIA_ACIMA_DO_MINIMO, _D.EFICACIA, round(taxa, 4), lim.taxa_minima),
        Motivo(_C.SEM_CONTESTACAO_RECENTE, _D.CONTESTACAO, 0, 1, f"últimos {lim.contestacao_dias} dias")), dims)
