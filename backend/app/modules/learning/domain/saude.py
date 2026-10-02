"""Saúde do item do Livro (30.4, `docs/design/aprendizado-vivo.md` §5): dimensões medidas e UM rótulo derivado por
regra, com os motivos que o produziram. Nenhuma nota de 0 a 100.

Puro: recebe os sinais já lidos (`SinaisDeSaude`) e devolve `Saude`. Quem lê o banco é o serviço; quem monta o JSON é a
apresentação. A lista, o detalhe e as métricas usam esta mesma função, então o painel nunca recalcula taxa nem ordem.

Duas regras mandam aqui. (1) Dimensão sem dado é `desconhecida`, nunca zero: falta de dado não vira boa nem ruim. (2) Falha
ou incerteza nunca contam como saudável: o rótulo `saudavel` exige uso recente com amostra, eficácia ≥ mínimo e contestação
conhecida e zerada; se alguma medida falta, o rótulo é `indeterminado` e os motivos dizem o que falta.

As regras e os limiares são os medidos no banco real (proposta D-5 ao dono), que valem sobre o §5.3 do desenho: a eficácia é
a ACUMULADA (`replay_ok/(ok+fail)`; não há fonte por item para a "janela dos últimos N usos"), `parado` e `pouca_amostra`
separam o que o §5.3 chamava de saudável sem distinguir. `obsoleto_provavel` (30.14) vem depois de `degradando` e antes de
`sem_evidencia`, com os sinais do §9.2 que têm fonte hoje (`domain/obsolescencia.py`); cada motivo leva o fato em `valor` e a
fonte em `detalhe`.

O rótulo NÃO é estado e não move nada: quem move é o `ciclo.py` (ou a pessoa).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from app.modules.learning.domain.ciclo import SkillState
from app.modules.learning.domain.obsolescencia import SinaisDeObsolescencia
from app.modules.learning.domain.vocabulario import LivroKind


class Rotulo(StrEnum):
    INATIVO = "inativo"
    EM_PROVA = "em_prova"
    DEGRADANDO = "degradando"
    OBSOLETO_PROVAVEL = "obsoleto_provavel"  # publicado e algum sinal do §9.2 (30.14); só leitura
    SEM_EVIDENCIA = "sem_evidencia"        # publicado há ≥ `sem_uso_dias` e nunca usado
    PARADO = "parado"                      # já usado, mas sem uso há mais de `sem_uso_dias`
    POUCA_AMOSTRA = "pouca_amostra"        # usado dentro da janela, com menos de `amostra_minima` usos
    SAUDAVEL = "saudavel"
    #: Publicado, mas sem medida para decidir (contador ou data ausente). Fora da lista de rótulos do desenho: é o que
    #: impede que incerteza vire saudável.
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
    # degradando
    FALHAS_SEGUIDAS = "falhas_seguidas"
    EFICACIA_ABAIXO_DO_MINIMO = "eficacia_abaixo_do_minimo"
    CONTESTADO_RECENTEMENTE = "contestado_recentemente"
    # obsoleto_provavel (30.14, §9.2): só os sinais com fonte hoje
    SUBSTITUTA_VIVA = "substituta_viva"
    VERSAO_FORA_DO_PARQUE = "versao_fora_do_parque"
    VERSAO_VIVA_SEM_REPRODUCAO = "versao_viva_sem_reproducao"
    EFEITO_SEM_RESPALDO_NO_CATALOGO = "efeito_sem_respaldo_no_catalogo"
    FLUXO_NUNCA_CASADO = "fluxo_nunca_casado"
    ABSORVIDA = "absorvida"
    # sem_evidencia, parado, pouca_amostra
    NUNCA_USADO = "nunca_usado"
    SEM_USO_RECENTE = "sem_uso_recente"
    AMOSTRA_PEQUENA = "amostra_pequena"
    # saudavel
    USADO_RECENTEMENTE = "usado_recentemente"
    AMOSTRA_SUFICIENTE = "amostra_suficiente"
    EFICACIA_ACIMA_DO_MINIMO = "eficacia_acima_do_minimo"
    SEM_CONTESTACAO_RECENTE = "sem_contestacao_recente"
    # indeterminado (o que falta para decidir)
    USO_DESCONHECIDO = "uso_desconhecido"
    IDADE_DESCONHECIDA = "idade_desconhecida"
    EFICACIA_DESCONHECIDA = "eficacia_desconhecida"
    CONTESTACAO_DESCONHECIDA = "contestacao_desconhecida"
    ESTADO_DESCONHECIDO = "estado_desconhecido"


@dataclass(frozen=True, slots=True)
class LimiaresDeSaude:
    """Os limiares da proposta D-5. Os defaults são os medidos no banco real; o config (`aprendizado.saude`) os troca."""

    sem_uso_dias: int = 14               # nunca usado há isto = sem_evidencia; sem uso há mais que isto = parado
    amostra_minima: int = 5              # usos para a eficácia valer (pouca_amostra abaixo disto)
    taxa_minima: float = 0.8
    falhas_seguidas: int = 2             # `consecutive_fail` a partir do qual o item degrada
    contestacao_dias: int = 7            # janela da evidência contra/conflito


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
    contestacoes_recentes: int | None = None
    #: Os sinais do §9.2 já lidos (30.14). `None`: o serviço não os leu (nenhum sinal; o rótulo segue a regra de antes).
    obsolescencia: SinaisDeObsolescencia | None = None


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
    """Um fato que produziu o rótulo: o código (vocabulário fechado), o valor medido e o limiar que ele cruzou."""

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
    # Frescor: desde o último uso; nunca usado, desde a criação (o item novo não nasce "velho" por falta de uso).
    desde = _dias_desde(s.ultimo_uso, s.agora) if s.ultimo_uso else _dias_desde(s.criado_em, s.agora)
    base = None if s.a_favor is None or s.contra is None else s.a_favor + s.contra
    return (
        DimensaoMedida(_D.USO, s.usos, s.usos, contador),
        DimensaoMedida(_D.EFICACIA, None if taxa is None else round(taxa, 4), amostra, contador),
        DimensaoMedida(_D.BASE_DE_EVIDENCIA, base, base, _FONTE_DA_EVIDENCIA),
        DimensaoMedida(_D.FRESCOR, desde, None, "last_used_at" if s.ultimo_uso else "created_at"),
        # Ainda sem medida (versão: 30.6/30.13; intervenção humana: sem sinal por item): desconhecidas, nunca zero.
        DimensaoMedida(_D.VERSAO, None, None, "versão do app (§7)"),
        DimensaoMedida(_D.CONTESTACAO, s.contestacoes_recentes, s.contestacoes_recentes, _FONTE_DA_EVIDENCIA),
        DimensaoMedida(_D.INTERVENCAO_HUMANA, None, None, "learning_signals"),
    )


def _degradando(s: SinaisDeSaude, taxa: float | None, lim: LimiaresDeSaude) -> list[Motivo]:
    motivos: list[Motivo] = []
    if s.falhas_seguidas is not None and s.falhas_seguidas >= lim.falhas_seguidas:
        motivos.append(Motivo(_C.FALHAS_SEGUIDAS, _D.EFICACIA, s.falhas_seguidas, lim.falhas_seguidas))
    # A eficácia só condena com amostra: poucos usos não "derrubam" nada (pouca_amostra cuida deles).
    if taxa is not None and s.usos is not None and s.usos >= lim.amostra_minima and taxa < lim.taxa_minima:
        motivos.append(Motivo(_C.EFICACIA_ABAIXO_DO_MINIMO, _D.EFICACIA, round(taxa, 4), lim.taxa_minima,
                              f"{s.usos} usos"))
    if s.contestacoes_recentes is not None and s.contestacoes_recentes >= 1:
        motivos.append(Motivo(_C.CONTESTADO_RECENTEMENTE, _D.CONTESTACAO, s.contestacoes_recentes, 1,
                              f"últimos {lim.contestacao_dias} dias"))
    return motivos


def _obsoleto(s: SinaisDeSaude, lim: LimiaresDeSaude) -> list[Motivo]:
    """Os motivos de `obsoleto_provavel`, na ordem do §9.2. O fato vai em `valor`; a fonte, em `detalhe`."""
    motivos: list[Motivo] = []
    o = s.obsolescencia
    if o is not None and o.substituta_viva is not None:
        sub = o.substituta_viva
        motivos.append(Motivo(_C.SUBSTITUTA_VIVA, None, sub.ref, None, f"{sub.fonte} (estado {sub.estado})"))
    if o is not None and o.versao_fora_do_parque is not None:
        vivas = ", ".join(o.versoes_vivas) or "nenhuma"
        motivos.append(Motivo(_C.VERSAO_FORA_DO_PARQUE, _D.VERSAO, o.versao_fora_do_parque, None,
                              f"device_app_state: versões vivas {vivas}"))
    if o is not None and o.versoes_sem_reproducao:
        motivos.append(Motivo(_C.VERSAO_VIVA_SEM_REPRODUCAO, _D.VERSAO, ", ".join(o.versoes_sem_reproducao), None,
                              "device_app_state × itens da mesma chave"))
    if o is not None and o.efeito is not None:
        motivos.append(Motivo(_C.EFEITO_SEM_RESPALDO_NO_CATALOGO, None, o.efeito.capability, None,
                              f"catalogo.yaml ({o.efeito.respaldo.value}): {o.efeito.fato}"))
    if s.kind is LivroKind.FLUXO and s.usos == 0 and not s.ultimo_uso:
        idade = _dias_desde(s.estado_desde or s.criado_em, s.agora)
        if idade is not None and idade >= lim.sem_uso_dias:
            motivos.append(Motivo(_C.FLUXO_NUNCA_CASADO, _D.USO, idade, lim.sem_uso_dias, "flows.uses: dias publicado"))
    if o is not None and o.absorvida_em is not None:
        motivos.append(Motivo(_C.ABSORVIDA, None, o.absorvida_em, None, "learning_items.state_detail: absorvida:<commit>"))
    return motivos


def calcular(s: SinaisDeSaude, lim: LimiaresDeSaude = LimiaresDeSaude()) -> Saude | None:
    """O rótulo e os motivos de um item, pela ordem da proposta D-5 (o primeiro que casa vence). `None`: a memória,
    que não é item de ciclo (só a contagem sai)."""
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
    degradando = _degradando(s, taxa, lim)
    if degradando:
        return Saude(Rotulo.DEGRADANDO, tuple(degradando), dims)
    obsoleto = _obsoleto(s, lim)
    if obsoleto:
        return Saude(Rotulo.OBSOLETO_PROVAVEL, tuple(obsoleto), dims)

    if s.usos is None:                                  # sem contador de uso o item não se classifica
        return Saude(Rotulo.INDETERMINADO, (Motivo(_C.USO_DESCONHECIDO, _D.USO),), dims)
    if s.usos == 0 and not s.ultimo_uso:
        idade = _dias_desde(s.estado_desde or s.criado_em, s.agora)
        if idade is None:
            return Saude(Rotulo.INDETERMINADO, (Motivo(_C.IDADE_DESCONHECIDA, _D.FRESCOR),), dims)
        if idade >= lim.sem_uso_dias:
            return Saude(Rotulo.SEM_EVIDENCIA, (Motivo(_C.NUNCA_USADO, _D.USO, idade, lim.sem_uso_dias,
                                                       "dias publicado"),), dims)
        return Saude(Rotulo.POUCA_AMOSTRA, (Motivo(_C.AMOSTRA_PEQUENA, _D.USO, 0, lim.amostra_minima),), dims)
    ultimo = _dias_desde(s.ultimo_uso, s.agora)
    if ultimo is None:                                  # usado, mas sem data do último uso
        return Saude(Rotulo.INDETERMINADO, (Motivo(_C.USO_DESCONHECIDO, _D.USO),), dims)
    if ultimo > lim.sem_uso_dias:
        return Saude(Rotulo.PARADO, (Motivo(_C.SEM_USO_RECENTE, _D.USO, ultimo, lim.sem_uso_dias,
                                            "dias desde o último uso"),), dims)
    if s.usos < lim.amostra_minima:
        return Saude(Rotulo.POUCA_AMOSTRA, (Motivo(_C.AMOSTRA_PEQUENA, _D.USO, s.usos, lim.amostra_minima),), dims)

    # ---- saudável: o que falta medir vira motivo de `indeterminado` (eficácia abaixo do mínimo já saiu em degradando)
    faltas: list[Motivo] = []
    if taxa is None:
        faltas.append(Motivo(_C.EFICACIA_DESCONHECIDA, _D.EFICACIA))
    if s.contestacoes_recentes is None:
        faltas.append(Motivo(_C.CONTESTACAO_DESCONHECIDA, _D.CONTESTACAO))
    if taxa is None or faltas:
        return Saude(Rotulo.INDETERMINADO, tuple(faltas), dims)
    return Saude(Rotulo.SAUDAVEL, (
        Motivo(_C.USADO_RECENTEMENTE, _D.USO, ultimo, lim.sem_uso_dias),
        Motivo(_C.AMOSTRA_SUFICIENTE, _D.USO, s.usos, lim.amostra_minima),
        Motivo(_C.EFICACIA_ACIMA_DO_MINIMO, _D.EFICACIA, round(taxa, 4), lim.taxa_minima),
        Motivo(_C.SEM_CONTESTACAO_RECENTE, _D.CONTESTACAO, 0, 1, f"últimos {lim.contestacao_dias} dias")), dims)
