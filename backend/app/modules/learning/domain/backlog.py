"""O que mais falha e o backlog da plataforma (ADR-054, decisão 7): o aprendizado para as sessões de desenvolvimento.

Puro: recebe ocorrências já lidas (e o erro já redigido e cortado pela infraestrutura) e devolve grupos, ordem, prova
da correção e propostas. Nunca chama IA, nunca grava nada.

- **Grupo**: (app, ação, tipo, tela). A chave canônica sai de UMA função (`chave_do_grupo`) usada pelo relatório e
  pela gravação — o `fk-*` que a sessão lê no md é o mesmo da linha que ela altera. App é o PACOTE (`*` quando a
  etapa não diz), ação é a `capability` (`*` = etapa livre), tela é a do pacote declarado ('' quando não há). A
  MEDIDA de uma linha sem tela (prova e reincidência) abrange o mesmo (app, ação, tipo) em qualquer tela
  (`ChaveDoGrupo.abrange`): ela só vira corrigida quando a falha para em TODAS.
- **Tipo**: um `FailureKind` (a tentativa) ou um `TipoDeVerificacao` (o sinal de pessoa que desmente o verificador).
- **Ordem**: custo total = US$ perdido + minutos × `aparelho_usd_min` + intervenções × `pessoa_usd`. As três colunas
  saem separadas; o total só ordena. O falso positivo do verificador fica SEMPRE no topo: é o sucesso mascarado.
- **Mínimo**: um grupo só entra no topo com `minimo_ocorrencias`; a seção de verificação sai inteira.
- **Prova da correção**: depois do commit implantado, ≥ `prova_minimo` tentativas elegíveis com taxa ≤
  `prova_fator` × a linha de base marcam `fixed`; acima, `reopened`; abaixo do mínimo, "faltam N". É medida, não
  declaração: `fixed` e `reopened` nunca vêm de uma pessoa.
- **Reincidência**: depois de `fixed`, a mesma régua roda numa janela que ANDA — as últimas
  `JANELA_DA_REINCIDENCIA` × `prova_minimo` tentativas elegíveis, nunca antes da prova. Medida desde a prova, a janela
  só cresceria e as boas de semanas diluiriam a volta concentrada da falha (a linha nunca mais reabriria: falha
  contada como sucesso).
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from app.modules.learning.domain.ciclo import EntradaInvalida, TransicaoProibida
from app.modules.learning.domain.falhas import CAMADA, ONDE_ALTERAR, Camada, FailureKind, OndeAlterar
from app.modules.learning.domain.vocabulario import CategoriaDoBacklog, EstadoDoBacklog, TipoDeProposta
from app.modules.skills.domain.document import JsonObject, JsonValue
from app.util import parse_iso

#: App desconhecido (a etapa não diz) ou etapa livre (sem `capability`): o mesmo `*` de `learning_daily`.
QUALQUER = "*"
#: Acima disto de `outro` entre as tentativas, o classificador precisa de regra nova (a catraca é por AST no executor).
LIMITE_DE_OUTRO = 0.15
#: Até quantos exemplos por grupo, e quantos ids a prova da correção guarda.
EXEMPLOS = 3
IDS_DA_PROVA = 20
#: A linha de base da prova: as semanas antes da correção.
BASE_DIAS = 28
#: A reincidência depois de `fixed` olha as últimas `JANELA_DA_REINCIDENCIA × prova_minimo` tentativas elegíveis. Com
#: base de 50% (limite 25%) e uma correção boa (~10% de falha): com 1× (10), 3 em 10 saem em ~7% das janelas e a linha
#: reabriria à toa em alguma das avaliações a cada 15 min; com 2× (20), 6 em 20 saem em ~1%, e a volta da falha (8 em 10
#: no último dia) ainda passa do limite (8/20 = 40%).
JANELA_DA_REINCIDENCIA = 2
#: `steps.driven_by` nulo (etapa anterior à coluna, ou que nunca chegou ao executor).
SEM_CONDUCAO = "-"
#: Propostas (ADR-054, tipos "ação nova", "lição" e "tela").
ACAO_EXECUCOES_MIN = 3
LICAO_AJUDA_DIAS = 14
TELA_PUBLICADA_DIAS = 7


class TipoDeVerificacao(StrEnum):
    """Falhas da VERIFICAÇÃO, que nenhuma tentativa grava: é a pessoa quem as mostra (sinais do D2 e `confirm_done`)."""

    FALSO_POSITIVO = "verificacao_falso_positivo"    # 'deu errado' numa etapa que o verificador comprovou
    FALSO_NEGATIVO = "verificacao_falso_negativo"    # 'deu certo' numa etapa que o verificador não comprovou
    CONFIRMOU_A_MAO = "verificacao_lacuna"           # a pessoa confirmou à mão o que a tela não comprovou


#: A frase de cada tipo, para o título. Todo tipo tem uma (um teste confere).
ROTULO: Mapping[str, str] = {
    FailureKind.PRAZO_DA_ETAPA.value: "prazo da etapa esgotado",
    FailureKind.UI_OCUPADA.value: "interface do aparelho ocupada",
    FailureKind.APP_ANR.value: "o app parou de responder (ANR)",
    FailureKind.APARELHO_TRAVADO.value: "chamada ao aparelho travada",
    FailureKind.SESSAO_DE_AUTOMACAO.value: "sessão de automação indisponível",
    FailureKind.INTERROMPIDA.value: "tentativa interrompida",
    FailureKind.AUTENTICACAO.value: "o app pede autenticação",
    FailureKind.CONTA_ERRADA.value: "conta errada",
    FailureKind.IA_INDISPONIVEL.value: "IA indisponível",
    FailureKind.IA_RECUSA.value: "a IA recusou por política",
    FailureKind.IA_ORCAMENTO.value: "teto de IA atingido",
    FailureKind.IA_SALDO.value: "sem saldo de IA",
    FailureKind.IA_CHAMADA_INVALIDA.value: "chamada inválida da IA",
    FailureKind.IA_DECLAROU_BLOQUEIO.value: "a IA declarou bloqueio",
    FailureKind.CICLO_SEM_PROGRESSO.value: "ciclo sem progresso",
    FailureKind.ALVO_AUSENTE.value: "alvo ausente",
    FailureKind.EFEITO_ALVO_ERRADO.value: "efeito no alvo errado",
    FailureKind.EFEITO_GUARDA_NAO_ATENDIDA.value: "guarda do efeito não atendida",
    FailureKind.EFEITO_NAO_COMPROVADO.value: "efeito não comprovado",
    FailureKind.POS_CONDICAO_NAO_COMPROVADA.value: "pós-condição não comprovada",
    FailureKind.COLETA_VAZIA.value: "coleta vazia",
    FailureKind.COLETA_INCOMPLETA.value: "coleta incompleta",
    FailureKind.DIGITACAO_INCOMPLETA.value: "digitação incompleta",
    FailureKind.DEFEITO_DO_PLANO.value: "defeito do plano",
    FailureKind.FALTA_INFORMACAO.value: "falta informação de quem pediu",
    FailureKind.OUTRO.value: "outro (o classificador não tem regra)",
    TipoDeVerificacao.FALSO_POSITIVO.value: "falso positivo do verificador (sucesso mascarado)",
    TipoDeVerificacao.FALSO_NEGATIVO.value: "falso negativo do verificador",
    TipoDeVerificacao.CONFIRMOU_A_MAO.value: "confirmado à mão (lacuna da verificação)",
}

_TIPOS_DE_FALHA = frozenset(k.value for k in FailureKind)
_TIPOS_DE_VERIFICACAO = frozenset(k.value for k in TipoDeVerificacao)


def camada_do_tipo(tipo: str) -> Camada:
    if tipo in _TIPOS_DE_VERIFICACAO:
        return Camada.VERIFICACAO
    if tipo in _TIPOS_DE_FALHA:
        return CAMADA[FailureKind(tipo)]
    return Camada.INDEFINIDA


def onde_alterar_do_tipo(tipo: str) -> OndeAlterar:
    return ONDE_ALTERAR[camada_do_tipo(tipo)]


# ------------------------------------------------------------------ regras (o bloco `aprendizado.backlog`)
@dataclass(frozen=True, slots=True)
class RegrasDoBacklog:
    minimo_ocorrencias: int = 3
    pessoa_usd: float = 0.25           # custo de uma intervenção humana, só para ordenar
    aparelho_usd_min: float = 0.0      # custo de um minuto de aparelho, só para ordenar
    prova_minimo: int = 10             # tentativas elegíveis depois da correção para provar
    prova_fator: float = 0.5           # taxa depois ≤ fator × base → corrigido


# ------------------------------------------------------------------ chave
def id_do_backlog(cluster_key: str) -> str:
    return "fk-" + hashlib.sha1(cluster_key.encode("utf-8")).hexdigest()[:10]


@dataclass(frozen=True, slots=True, order=True)
class ChaveDoGrupo:
    app: str
    capability: str
    tipo: str
    tela: str = ""

    @property
    def cluster_key(self) -> str:
        return f"{self.app}|{self.capability}|{self.tipo}|{self.tela}"

    @property
    def id(self) -> str:
        return id_do_backlog(self.cluster_key)

    @property
    def app_e_acao(self) -> tuple[str, str]:
        return self.app, self.capability

    def abrange(self, outra: ChaveDoGrupo) -> bool:
        """A ocorrência de chave `outra` conta na MEDIDA da linha desta chave (prova da correção e reincidência)?

        Com tela, só a mesma chave. Sem tela, o mesmo (app, ação, tipo) em QUALQUER tela (item 22.3): antes do
        escritor de `attempts.failure_screen` toda falha nascia sem tela, e depois dele as mesmas falhas caem no grupo
        da tela nomeada. Medida só pela chave exata, a linha antiga "sem tela" veria zero ocorrência enquanto a falha
        continua — e a prova a daria como corrigida (falha contada como sucesso). O agrupamento do relatório segue
        exato: somar aqui lá contaria a mesma tentativa em dois grupos."""
        if self.tela:
            return outra == self
        return (outra.app, outra.capability, outra.tipo) == (self.app, self.capability, self.tipo)


def chave_do_grupo(app: str | None, capability: str | None, tipo: str, tela: str | None) -> ChaveDoGrupo:
    """A ÚNICA forma de montar a chave: vazio e nulo são o mesmo grupo (`*` no app e na ação, '' na tela)."""
    return ChaveDoGrupo(app=(app or "").strip() or QUALQUER, capability=(capability or "").strip() or QUALQUER,
                        tipo=tipo, tela=(tela or "").strip())


def chave_de_proposta(tipo: TipoDeProposta, ref: str) -> str:
    return f"{tipo.value}|{ref}"


def titulo(chave: ChaveDoGrupo) -> str:
    acao = "etapa livre" if chave.capability == QUALQUER else chave.capability
    texto = f"{chave.app} · {acao}: {ROTULO.get(chave.tipo, chave.tipo)}"
    return texto + (f" (tela {chave.tela})" if chave.tela else "")


# ------------------------------------------------------------------ ocorrências
class FonteDaOcorrencia(StrEnum):
    TENTATIVA = "tentativa"
    SINAL = "sinal"


@dataclass(frozen=True, slots=True)
class Ocorrencia:
    """Uma observação de falha. `usd` vem de UMA fonte: as chamadas de IA da própria tentativa quando ela começou
    depois do último corte da purga de `ai_calls`; antes disso (`do_diario`), o custo médio por tentativa do mesmo
    (dia, app, ação, tipo) na régua durável (`learning_daily`). Sem linha na régua, o custo é desconhecido
    (`custo_conhecido=False`) — e o relatório diz, em vez de mostrar zero."""

    chave: ChaveDoGrupo
    quando: str
    fonte: FonteDaOcorrencia
    run_id: str | None = None
    attempt_id: str | None = None
    step_id: str | None = None
    instance_id: str | None = None
    segundos: float | None = None
    usd: float = 0.0
    intervencoes: int = 0
    retroativa: bool = False
    do_diario: bool = False
    custo_conhecido: bool = True
    erro: str | None = None
    erros_de_ia: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Tendencia:
    ultimos_7d: int
    anteriores_7d: int

    @property
    def direcao(self) -> str:
        if self.ultimos_7d == self.anteriores_7d:
            return "estavel"
        if self.anteriores_7d == 0:
            return "nova"
        return "subindo" if self.ultimos_7d > self.anteriores_7d else "descendo"


@dataclass(frozen=True, slots=True)
class Exemplo:
    run_id: str | None
    attempt_id: str | None
    quando: str
    erro: str | None


@dataclass(frozen=True, slots=True)
class GrupoDeFalha:
    chave: ChaveDoGrupo
    ocorrencias: int
    retroativas: int
    #: Tentativas reais da mesma (app, ação) na janela: o denominador da taxa. `None` = nenhuma (ausente não é zero).
    elegiveis: int | None
    etapas: int
    execucoes: int
    aparelhos: int
    usd_perdido: float
    min_perdidos: float
    intervencoes: int
    tendencia: Tendencia
    exemplos: tuple[Exemplo, ...]
    erros_de_ia: Mapping[str, int]
    primeira: str
    ultima: str
    #: Alguma ocorrência da janela caiu num dia sem `ai_calls` inteiro nem linha em `learning_daily`.
    custo_parcial: bool = False

    @property
    def id(self) -> str:
        return self.chave.id

    @property
    def taxa(self) -> float | None:
        return self.ocorrencias / self.elegiveis if self.elegiveis else None

    @property
    def camada(self) -> Camada:
        return camada_do_tipo(self.chave.tipo)

    @property
    def onde_alterar(self) -> OndeAlterar:
        return onde_alterar_do_tipo(self.chave.tipo)

    @property
    def titulo(self) -> str:
        return titulo(self.chave)

    @property
    def falso_positivo(self) -> bool:
        return self.chave.tipo == TipoDeVerificacao.FALSO_POSITIVO.value

    def custo_total(self, regras: RegrasDoBacklog) -> float:
        return custo_total(self.usd_perdido, self.min_perdidos, self.intervencoes, regras)


def custo_total(usd: float, minutos: float, intervencoes: int, regras: RegrasDoBacklog) -> float:
    return usd + minutos * regras.aparelho_usd_min + intervencoes * regras.pessoa_usd


def _depois_de(quando: str, limite: datetime) -> bool:
    instante = _instante(quando)
    return instante is not None and instante >= limite


def _instante(quando: str | None) -> datetime | None:
    try:
        return parse_iso(quando)
    except ValueError:
        return None


def agrupar(ocorrencias: Iterable[Ocorrencia], *, desde: str, agora: datetime,
            elegiveis: Mapping[tuple[str, str], int]) -> list[GrupoDeFalha]:
    """Agrupa as ocorrências. As MÉTRICAS contam só `quando >= desde`; a TENDÊNCIA compara sempre os últimos 7 dias
    com os 7 anteriores, com o que vier (quem chama lê pelo menos 14 dias)."""
    por_chave: dict[ChaveDoGrupo, list[Ocorrencia]] = defaultdict(list)
    for o in ocorrencias:
        por_chave[o.chave].append(o)
    sete, catorze = agora - timedelta(days=7), agora - timedelta(days=14)
    grupos: list[GrupoDeFalha] = []
    for chave, todas in por_chave.items():
        na_janela = sorted((o for o in todas if o.quando >= desde), key=lambda o: (o.quando, o.attempt_id or ""))
        if not na_janela:
            continue
        ultimos = sum(1 for o in todas if _depois_de(o.quando, sete))
        anteriores = sum(1 for o in todas if _depois_de(o.quando, catorze) and not _depois_de(o.quando, sete))
        erros_de_ia: Counter[str] = Counter(k for o in na_janela for k in o.erros_de_ia)
        exemplos = tuple(Exemplo(o.run_id, o.attempt_id, o.quando, o.erro)
                         for o in reversed(na_janela[-EXEMPLOS:]))
        tentativas = [o for o in na_janela if o.fonte is FonteDaOcorrencia.TENTATIVA]
        grupos.append(GrupoDeFalha(
            chave=chave, ocorrencias=len(na_janela), retroativas=sum(1 for o in na_janela if o.retroativa),
            elegiveis=elegiveis.get(chave.app_e_acao) or None,
            etapas=len({o.step_id for o in na_janela if o.step_id}),
            execucoes=len({o.run_id for o in na_janela if o.run_id}),
            aparelhos=len({o.instance_id for o in na_janela if o.instance_id}),
            usd_perdido=round(sum(o.usd for o in na_janela), 6),
            min_perdidos=round(sum(o.segundos or 0.0 for o in tentativas) / 60.0, 3),
            intervencoes=sum(o.intervencoes for o in na_janela),
            tendencia=Tendencia(ultimos, anteriores), exemplos=exemplos, erros_de_ia=dict(erros_de_ia),
            primeira=na_janela[0].quando, ultima=na_janela[-1].quando,
            custo_parcial=any(not o.custo_conhecido for o in na_janela)))
    return grupos


def ordenar(grupos: Iterable[GrupoDeFalha], regras: RegrasDoBacklog) -> list[GrupoDeFalha]:
    """O falso positivo do verificador primeiro, sempre; depois o custo total, as ocorrências e a chave."""
    return sorted(grupos, key=lambda g: (not g.falso_positivo, -g.custo_total(regras), -g.ocorrencias,
                                         g.chave.cluster_key))


def entra_no_topo(g: GrupoDeFalha, *, camada: Camada | None) -> bool:
    """A camada `pessoa` (falta a informação que só quem pediu tem) fica fora por padrão."""
    if camada is not None:
        return g.camada is camada
    return g.camada is not Camada.PESSOA


@dataclass(frozen=True, slots=True)
class ParteDeOutro:
    ocorrencias: int
    total: int

    @property
    def pct(self) -> float | None:
        return self.ocorrencias / self.total if self.total else None

    @property
    def alerta(self) -> bool:
        return self.pct is not None and self.pct > LIMITE_DE_OUTRO


def parte_de_outro(ocorrencias: Iterable[Ocorrencia]) -> ParteDeOutro:
    tentativas = [o for o in ocorrencias if o.fonte is FonteDaOcorrencia.TENTATIVA]
    return ParteDeOutro(sum(1 for o in tentativas if o.chave.tipo == FailureKind.OUTRO.value), len(tentativas))


# ------------------------------------------------------------------ telas que chamaram uma pessoa
@dataclass(frozen=True, slots=True)
class ChamadaDeTela:
    app: str
    tela: str
    quando: str
    chamou_pessoa: bool


@dataclass(frozen=True, slots=True)
class TelaQueChamou:
    app: str
    tela: str
    chamadas_de_pessoa: int
    falhas: int
    ultima: str


def telas_que_chamaram(chamadas: Iterable[ChamadaDeTela], ocorrencias: Iterable[Ocorrencia]) -> list[TelaQueChamou]:
    """As telas desconhecidas que pararam a automação: o sinal da sessão (`tela_desconhecida_chamou_pessoa`) e as
    tentativas que falharam numa tela `desconhecida:<sig>`."""
    pessoa: Counter[tuple[str, str]] = Counter()
    falhas: Counter[tuple[str, str]] = Counter()
    ultima: dict[tuple[str, str], str] = {}
    for c in chamadas:
        k = (c.app, c.tela)
        pessoa[k] += int(c.chamou_pessoa)
        ultima[k] = max(ultima.get(k, ""), c.quando)
    for o in ocorrencias:
        if o.fonte is FonteDaOcorrencia.TENTATIVA and o.chave.tela.startswith("desconhecida:"):
            k = (o.chave.app, o.chave.tela)
            falhas[k] += 1
            ultima[k] = max(ultima.get(k, ""), o.quando)
    chaves = set(pessoa) | set(falhas)
    return sorted((TelaQueChamou(a, t, pessoa[(a, t)], falhas[(a, t)], ultima[(a, t)]) for a, t in chaves),
                  key=lambda x: (-x.chamadas_de_pessoa, -x.falhas, x.app, x.tela))


# ------------------------------------------------------------------ prova da correção
@dataclass(frozen=True, slots=True)
class Medida:
    """Tentativas elegíveis (reais, mesma app e ação) e ocorrências do grupo em [desde, ate)."""

    desde: str
    ate: str
    elegiveis: int
    ocorrencias: int
    ids: tuple[str, ...]
    usd: float | None = None

    @property
    def taxa(self) -> float | None:
        return self.ocorrencias / self.elegiveis if self.elegiveis else None

    def json(self) -> JsonObject:
        taxa = self.taxa
        saida: JsonObject = {"desde": self.desde, "ate": self.ate, "elegiveis": self.elegiveis,
                             "ocorrencias": self.ocorrencias, "taxa": None if taxa is None else round(taxa, 4),
                             "ids": list(self.ids)}
        if self.usd is not None:
            saida["usd"] = round(self.usd, 6)
        return saida


def medida_de_json(dado: JsonValue) -> Medida | None:
    """A linha de base gravada; ilegível é `None` (e a prova trata como "sem base")."""
    if not isinstance(dado, dict):
        return None
    desde, ate, n, oc = dado.get("desde"), dado.get("ate"), dado.get("elegiveis"), dado.get("ocorrencias")
    if not (isinstance(desde, str) and isinstance(ate, str) and isinstance(n, int) and isinstance(oc, int)):
        return None
    ids = dado.get("ids")
    usd = dado.get("usd")
    return Medida(desde, ate, n, oc, tuple(i for i in ids if isinstance(i, str)) if isinstance(ids, list) else (),
                  float(usd) if isinstance(usd, (int, float)) and not isinstance(usd, bool) else None)


class Veredito(StrEnum):
    FALTAM = "faltam"
    CORRIGIDO = "corrigido"
    REABRE = "reabre"


@dataclass(frozen=True, slots=True)
class ResultadoDaProva:
    veredito: Veredito
    novo_estado: EstadoDoBacklog | None
    faltam: int
    #: A taxa máxima aceita (fator × base; 0 sem base).
    limite: float
    motivo: str


def provar(estado: EstadoDoBacklog, base: Medida | None, depois: Medida, regras: RegrasDoBacklog) -> ResultadoDaProva:
    """O veredito da correção. Só vale para `fixed_pending_proof` (prova) e `fixed` (reincidência).

    Sem base (nenhuma tentativa elegível antes da correção), o limite é zero: só a ausência de ocorrências prova —
    incerteza nunca conta como sucesso. Abaixo de `prova_minimo` elegíveis, nada muda: faltam N.
    """
    if estado not in (EstadoDoBacklog.FIXED_PENDING_PROOF, EstadoDoBacklog.FIXED):
        raise ValueError(f"prova só vale para fixed_pending_proof e fixed, não para {estado.value}")
    taxa_base = base.taxa if base is not None else None
    limite = round(regras.prova_fator * taxa_base, 6) if taxa_base is not None else 0.0
    faltam = max(0, regras.prova_minimo - depois.elegiveis)
    if faltam:
        return ResultadoDaProva(Veredito.FALTAM, None, faltam, limite,
                                f"faltam {faltam} tentativa(s) elegível(is) para medir")
    taxa = depois.taxa or 0.0
    if taxa <= limite:
        novo = EstadoDoBacklog.FIXED if estado is EstadoDoBacklog.FIXED_PENDING_PROOF else None
        return ResultadoDaProva(Veredito.CORRIGIDO, novo, 0, limite,
                                f"taxa {taxa:.1%} ≤ {limite:.1%} em {depois.elegiveis} tentativas elegíveis")
    return ResultadoDaProva(Veredito.REABRE, EstadoDoBacklog.REOPENED, 0, limite,
                            f"taxa {taxa:.1%} > {limite:.1%} em {depois.elegiveis} tentativas elegíveis")


def tentativas_da_reincidencia(regras: RegrasDoBacklog) -> int:
    """Quantas tentativas elegíveis (as mais recentes, desde a prova) a reincidência mede."""
    return max(1, JANELA_DA_REINCIDENCIA * regras.prova_minimo)


# ------------------------------------------------------------------ a linha gravada e o que a pessoa pode mudar
@dataclass(frozen=True, slots=True)
class LinhaDoBacklog:
    id: str
    category: CategoriaDoBacklog
    cluster_key: str
    title: str
    state: EstadoDoBacklog
    first_seen: str
    last_seen: str
    app_package: str | None = None
    capability: str | None = None
    failure_kind: str | None = None
    failure_screen: str | None = None
    plan_item: str | None = None
    fixed_in_commit: str | None = None
    fixed_at: str | None = None
    baseline: JsonObject | None = None
    verification: JsonObject | None = None
    reopened_count: int = 0
    parent_id: str | None = None
    notes: str | None = None
    updated_by: str | None = None
    updated_at: str | None = None


def linha_de_grupo(g: GrupoDeFalha) -> LinhaDoBacklog:
    c = g.chave
    return LinhaDoBacklog(id=c.id, category=CategoriaDoBacklog.FALHA, cluster_key=c.cluster_key, title=g.titulo,
                          state=EstadoDoBacklog.OPEN, first_seen=g.primeira, last_seen=g.ultima, app_package=c.app,
                          capability=c.capability, failure_kind=c.tipo, failure_screen=c.tela)


#: O que uma pessoa (ou a sessão de desenvolvimento) pode marcar. `fixed` e `reopened` são medida.
ESTADOS_DA_PESSOA = frozenset({EstadoDoBacklog.OPEN, EstadoDoBacklog.TRIAGED, EstadoDoBacklog.PLANNED,
                               EstadoDoBacklog.FIXED_PENDING_PROOF, EstadoDoBacklog.WONTFIX})
_SHA = re.compile(r"^[0-9a-f]{7,40}$")


def commit_valido(commit: str | None) -> str:
    sha = (commit or "").strip().lower()
    if not _SHA.match(sha):
        raise EntradaInvalida("Diga o commit da correção (sha de 7 a 40 caracteres hexadecimais): a prova começa "
                              "no commit implantado.")
    return sha


def conferir_alteracao(para: EstadoDoBacklog, fixed_in_commit: str | None) -> str | None:
    """Recusa o que não é da pessoa e devolve o commit normalizado quando a correção vai para a prova."""
    if para not in ESTADOS_DA_PESSOA:
        raise TransicaoProibida(f"'{para.value}' é medida, não declaração: a curadoria marca depois de "
                                "provar (ou de ver a falha voltar). Use 'fixed_pending_proof' com o commit.")
    if para is EstadoDoBacklog.FIXED_PENDING_PROOF:
        return commit_valido(fixed_in_commit)
    return None


# ------------------------------------------------------------------ propostas
@dataclass(frozen=True, slots=True)
class Proposta:
    tipo: TipoDeProposta
    ref: str
    app: str
    titulo: str
    detalhe: str
    fragmento: str

    @property
    def cluster_key(self) -> str:
        return chave_de_proposta(self.tipo, self.ref)

    @property
    def id(self) -> str:
        return id_do_backlog(self.cluster_key)


def linha_de_proposta(p: Proposta, agora: str) -> LinhaDoBacklog:
    return LinhaDoBacklog(id=p.id, category=CategoriaDoBacklog.PROPOSTA, cluster_key=p.cluster_key, title=p.titulo,
                          state=EstadoDoBacklog.OPEN, first_seen=agora, last_seen=agora, app_package=p.app)


@dataclass(frozen=True, slots=True)
class AcaoLivre:
    """Etapa livre (sem ação do catálogo) comprovada, agrupada pelo modelo (`steps.template_hash`)."""

    app: str
    template_hash: str
    chave: str
    execucoes: int


def proposta_de_acao(a: AcaoLivre) -> Proposta | None:
    """Etapa livre com o mesmo modelo comprovada em ≥3 execuções: candidata a ação do catálogo. SEMPRE pessoa: a
    ação entra por habilidade publicada ou fragmento commitado, nunca por catálogo sobreposto no banco."""
    if a.execucoes < ACAO_EXECUCOES_MIN:
        return None
    fragmento = (f"# proposta: ação de catálogo a partir da etapa livre '{a.chave}' (template_hash {a.template_hash})\n"
                 f"# comprovada em {a.execucoes} execuções reais em {a.app}; revise antes de commitar\n"
                 "- id: A_DEFINIR\n"
                 f"  # chave da etapa: {a.chave}\n"
                 "  side_effect: A_DEFINIR\n"
                 "  postcondition: A_DEFINIR")
    return Proposta(TipoDeProposta.ACAO_DE_CATALOGO, f"{a.app}|{a.template_hash}", a.app,
                    f"{a.app} · etapa livre '{a.chave}': virar ação do catálogo",
                    f"comprovada em {a.execucoes} execuções", fragmento)


@dataclass(frozen=True, slots=True)
class ItemParaPromover:
    """Uma lição ou tela publicada, lida do livro (o que a proposta precisa saber dela)."""

    id: str
    kind: str
    app: str
    summary: str
    state: str
    state_detail: str | None
    state_at: str | None
    contra: int
    conflitos: int


def proposta_de_item(item: ItemParaPromover, agora: datetime) -> Proposta | None:
    """Lição com efeito 'ajuda' há ≥14 dias, ou tela publicada há ≥7 dias sem nenhum conflito: promover para o
    repositório (regra no código ou no YAML do app), o que o banco da instalação não faz sozinho."""
    desde = _instante(item.state_at)
    if item.state != "published" or desde is None:
        return None
    idade = agora - desde
    if item.kind == "licao" and item.state_detail == "medida:ajuda" and idade >= timedelta(days=LICAO_AJUDA_DIAS):
        return Proposta(TipoDeProposta.PROMOVER_LICAO, item.id, item.app, f"{item.app} · lição {item.id}: promover",
                        f"efeito 'ajuda' há {idade.days} dias",
                        f"# promover a lição {item.id} (ajuda medida)\n# {item.summary}")
    if (item.kind == "tela" and not item.contra and not item.conflitos
            and idade >= timedelta(days=TELA_PUBLICADA_DIAS)):
        return Proposta(TipoDeProposta.PROMOVER_TELA, item.id, item.app, f"{item.app} · tela {item.id}: promover",
                        f"publicada há {idade.days} dias sem conflito",
                        f"# absorver a tela {item.id} no telas.yaml do app (exportação do livro)\n# {item.summary}")
    return None


# ------------------------------------------------------------------ saúde do aprendizado
@dataclass(frozen=True, slots=True)
class SaudeDasExecucoes:
    execucoes: int
    execucoes_com_fluxo: int
    fluxos_distintos: int
    etapas_por_conducao: Mapping[str, int]
    intervencoes: int


@dataclass(frozen=True, slots=True)
class Saude:
    itens_por_tipo: Mapping[str, Mapping[str, int]]
    execucoes: int
    execucoes_com_fluxo: int
    fluxos_distintos: int
    etapas_por_conducao: Mapping[str, int] = field(default_factory=dict)
    intervencoes: int = 0

    @property
    def pct_por_receita(self) -> float | None:
        """Etapas conduzidas só por receita, sobre as que registraram quem conduziu (`-` = anterior à coluna)."""
        total = sum(n for k, n in self.etapas_por_conducao.items() if k != SEM_CONDUCAO)
        return self.etapas_por_conducao.get("recipe", 0) / total if total else None

    @property
    def intervencoes_por_10_execucoes(self) -> float | None:
        return round(self.intervencoes * 10 / self.execucoes, 2) if self.execucoes else None


__all__ = ["ACAO_EXECUCOES_MIN", "BASE_DIAS", "ESTADOS_DA_PESSOA", "EXEMPLOS", "IDS_DA_PROVA", "JANELA_DA_REINCIDENCIA",
           "LIMITE_DE_OUTRO", "QUALQUER", "ROTULO", "SEM_CONDUCAO", "AcaoLivre", "ChamadaDeTela", "ChaveDoGrupo",
           "Exemplo", "FonteDaOcorrencia", "GrupoDeFalha", "ItemParaPromover", "LinhaDoBacklog", "Medida", "Ocorrencia",
           "ParteDeOutro", "Proposta", "RegrasDoBacklog", "ResultadoDaProva", "Saude", "SaudeDasExecucoes",
           "TelaQueChamou", "Tendencia", "TipoDeVerificacao", "Veredito", "agrupar", "camada_do_tipo",
           "chave_de_proposta", "chave_do_grupo", "commit_valido", "conferir_alteracao", "custo_total", "entra_no_topo",
           "id_do_backlog", "linha_de_grupo", "linha_de_proposta", "medida_de_json", "onde_alterar_do_tipo", "ordenar",
           "parte_de_outro", "proposta_de_acao", "proposta_de_item", "provar", "telas_que_chamaram",
           "tentativas_da_reincidencia", "titulo"]
