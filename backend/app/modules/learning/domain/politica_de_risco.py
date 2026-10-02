"""A política de risco do aprendizado (item 30.10, `aprendizado-vivo.md` §8.4, APROVADA pelo dono em 02/10): de que
CLASSE é um item do livro (A, B ou C), por quê, e o que isso permite. Fonte ÚNICA: a espera do dono (30.21,
`domain/espera.py`) e o curador (30.10/30.11, `domain/curador.py`) leem a classe daqui. Puro: recebe os FATOS já lidos
(o catálogo do app vem do YAML pela infraestrutura); sem I/O, sem relógio.

As três classes:
- **A** (navegação e leitura, sem `commit`, sem origem humana): o sistema decide pela regra determinística de hoje. A
  faixa A NUNCA gasta IA (decisão do orçamento, §8.7): não há parecer a dar.
- **B** (efeito médio; `commit` em app sem catálogo; origem humana sem efeito, D-2): a IA recomenda, o dono aprova EM
  LOTE.
- **C** (alto risco: `risk=high`, `manual_only`, sessão e autenticação, família de envio, publicação ou exclusão,
  texto escrito para outra pessoa (`needs_draft`), e o `commit` que o catálogo não declara): sempre o dono, ITEM A
  ITEM; a IA só monta parecer, nunca decide.

**Vale a mais restritiva** entre o catálogo (a capability da etapa) e o conteúdo (o `commit` da receita, a etapa de
efeito do fluxo). É o que pega a anomalia da receita 100 do Outlook: `commit` numa capability que o catálogo (só de
leitura) declara sem efeito. Sem os fatos da etapa (capability não derivável), não há divergência a afirmar: o `commit`
sozinho é B, como na 30.21.

A IA não transiciona em classe nenhuma (§8.1): `conferir_aceite` é a regra que torna a decisão automática a partir de
um parecer impossível, e o lote impossível na classe C.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.modules.skills.domain.document import JsonObject


class ClasseDeRisco(StrEnum):
    A = "A"                     # o sistema decide pela regra determinística; nunca vai à IA
    B = "B"                     # o dono decide em lote, com a recomendação da IA
    C = "C"                     # o dono decide item a item; a IA só dá parecer


class MotivoDeEntrada(StrEnum):
    """O motivo CURTO que o evento `learning.needs_person` carrega (vocabulário fechado do §8.11). Mora aqui porque é
    a saída da classificação; `domain/espera.py` o reexporta."""

    EFEITO_EXTERNO = "efeito_externo"
    TEXTO_DE_PESSOA = "texto_de_pessoa"
    COMMIT_SEM_CATALOGO = "commit_sem_catalogo"
    ALTO_RISCO = "alto_risco"
    SESSAO_OU_AUTENTICACAO = "sessao_ou_autenticacao"
    PARECER_DA_IA = "parecer_da_ia"         # publicado pelo curador (30.11); a porta está pronta, ninguém a chama ainda


class Razao(StrEnum):
    """O detalhe INTERNO da classificação (vai ao dossiê e a `learning_reviews.politica`, nunca ao evento). Mais
    fino que o motivo: um item pode ter várias razões; a classe é a da mais restritiva."""

    SESSAO_OU_AUTENTICACAO = "sessao_ou_autenticacao"
    RISCO_ALTO = "risco_alto"                           # catálogo: `risk: high`
    POLITICA_MANUAL = "politica_manual"                 # catálogo: `default_policy: manual_only | disabled`
    TEXTO_PARA_OUTRA_PESSOA = "texto_para_outra_pessoa"  # catálogo: `needs_draft` (mensagem, comentário)
    FAMILIA_DE_ALTO_RISCO = "familia_de_alto_risco"     # catálogo: efeito da família envio, publicação ou exclusão
    COMMIT_FORA_DO_CATALOGO = "commit_fora_do_catalogo"  # há `commit`, e o catálogo diz que a etapa não tem efeito
    RISCO_MEDIO = "risco_medio"                         # catálogo: `risk: medium`
    EFEITO_DECLARADO = "efeito_declarado"               # catálogo: `side_effect: true`
    COMMIT_SEM_FATOS_DA_ETAPA = "commit_sem_fatos_da_etapa"  # app com catálogo, capability da etapa não derivável
    COMMIT_SEM_CATALOGO = "commit_sem_catalogo"         # app sem `catalogo.yaml`
    TEXTO_DE_PESSOA = "texto_de_pessoa"                 # `human_origin` (nota, edição, resposta)


class PoliticaDaClasse(StrEnum):
    """Quem decide, gravado em `learning_reviews.politica`."""

    REGRA_DETERMINISTICA = "regra_deterministica"
    DONO_EM_LOTE = "dono_em_lote"
    DONO_ITEM_A_ITEM = "dono_item_a_item"


#: As famílias de efeito que fazem a capability ser classe C. O MAPA de `interaction_type` para família é DADO do
#: catálogo de cada app (§8.4), não código: o domínio só recebe a família já lida. Os catálogos de hoje ainda não a
#: declaram (o Instagram cai em C pelo `risk: high`/`needs_draft` das mesmas ações).
FAMILIAS_DE_ALTO_RISCO = frozenset({"envio", "publicacao", "exclusao"})

_POLITICAS_MANUAIS = frozenset({"manual_only", "disabled"})


@dataclass(frozen=True, slots=True)
class FatosDoCatalogo:
    """O que o catálogo do app diz da capability da etapa (`governance` e `side_effect`); texto de ação nunca entra."""

    risco: str = "low"                      # low | medium | high
    politica: str = "autonomous"            # autonomous | approval_required | manual_only | disabled
    precisa_rascunho: bool = False          # `needs_draft`: a ação envia conteúdo escrito (mensagem, comentário)
    efeito_externo: bool = False
    familia_do_efeito: str | None = None    # envio | publicacao | exclusao, quando o catálogo a declarar
    interacao: str | None = None            # `interaction_type` (dm_sent, followed...): identificador, vai ao dossiê


@dataclass(frozen=True, slots=True)
class FatosDeRisco:
    """A entrada da política: o que o item é (conteúdo) e o que o catálogo diz da etapa dele."""

    side_effect: bool                       # `commit` da receita / etapa de efeito do fluxo (`receita_tem_efeito`)
    human_origin: bool
    tem_catalogo: bool                      # o app tem `catalogo.yaml`
    catalogo: FatosDoCatalogo | None = None  # None: capability não derivável ou desconhecida do catálogo
    sessao_ou_autenticacao: bool = False

    def como_dados(self) -> JsonObject:
        c = self.catalogo
        return {"side_effect": self.side_effect, "human_origin": self.human_origin, "tem_catalogo": self.tem_catalogo,
                "sessao_ou_autenticacao": self.sessao_ou_autenticacao,
                "catalogo": None if c is None else {
                    "risco": c.risco, "politica": c.politica, "precisa_rascunho": c.precisa_rascunho,
                    "efeito_externo": c.efeito_externo, "familia_do_efeito": c.familia_do_efeito,
                    "interacao": c.interacao}}


#: Ordem de prioridade das razões: a primeira presente dá o MOTIVO do evento. Sessão vence o alto risco, que vence a
#: divergência, que vence o efeito, que vence o texto de pessoa (a mesma ordem que a 30.21 já publicava).
_REGRAS: tuple[tuple[Razao, ClasseDeRisco, MotivoDeEntrada | None], ...] = (
    (Razao.SESSAO_OU_AUTENTICACAO, ClasseDeRisco.C, MotivoDeEntrada.SESSAO_OU_AUTENTICACAO),
    (Razao.RISCO_ALTO, ClasseDeRisco.C, MotivoDeEntrada.ALTO_RISCO),
    (Razao.POLITICA_MANUAL, ClasseDeRisco.C, MotivoDeEntrada.ALTO_RISCO),
    (Razao.TEXTO_PARA_OUTRA_PESSOA, ClasseDeRisco.C, MotivoDeEntrada.ALTO_RISCO),
    (Razao.FAMILIA_DE_ALTO_RISCO, ClasseDeRisco.C, MotivoDeEntrada.ALTO_RISCO),
    (Razao.COMMIT_FORA_DO_CATALOGO, ClasseDeRisco.C, MotivoDeEntrada.EFEITO_EXTERNO),
    (Razao.RISCO_MEDIO, ClasseDeRisco.B, MotivoDeEntrada.EFEITO_EXTERNO),
    (Razao.EFEITO_DECLARADO, ClasseDeRisco.B, MotivoDeEntrada.EFEITO_EXTERNO),
    (Razao.COMMIT_SEM_FATOS_DA_ETAPA, ClasseDeRisco.B, MotivoDeEntrada.EFEITO_EXTERNO),
    (Razao.COMMIT_SEM_CATALOGO, ClasseDeRisco.B, MotivoDeEntrada.COMMIT_SEM_CATALOGO),
    (Razao.TEXTO_DE_PESSOA, ClasseDeRisco.B, MotivoDeEntrada.TEXTO_DE_PESSOA),
)
_CLASSE_DA_RAZAO = {r: c for r, c, _ in _REGRAS}
_MOTIVO_DA_RAZAO = {r: m for r, _, m in _REGRAS}
_POLITICA = {ClasseDeRisco.A: PoliticaDaClasse.REGRA_DETERMINISTICA, ClasseDeRisco.B: PoliticaDaClasse.DONO_EM_LOTE,
             ClasseDeRisco.C: PoliticaDaClasse.DONO_ITEM_A_ITEM}


@dataclass(frozen=True, slots=True)
class Classificacao:
    classe: ClasseDeRisco
    razoes: tuple[Razao, ...]               # em ordem de prioridade; vazia só na classe A
    motivo: MotivoDeEntrada | None          # o do evento; None na classe A (ninguém espera)

    @property
    def politica(self) -> PoliticaDaClasse:
        return _POLITICA[self.classe]

    @property
    def gasta_ia(self) -> bool:
        """A faixa A nunca vai à IA (§8.4, §8.7); B recebe recomendação; C, parecer de apoio."""
        return self.classe is not ClasseDeRisco.A

    @property
    def decide_sozinho(self) -> bool:
        """Só a classe A transiciona sem a pessoa, e só pela regra determinística (nunca por parecer)."""
        return self.classe is ClasseDeRisco.A

    @property
    def aceita_lote(self) -> bool:
        return self.classe is ClasseDeRisco.B

    def como_dados(self) -> JsonObject:
        return {"classe": self.classe.value, "politica": self.politica.value,
                "motivo": None if self.motivo is None else self.motivo.value,
                "razoes": [r.value for r in self.razoes]}


def _razoes(f: FatosDeRisco) -> list[Razao]:
    c = f.catalogo
    achadas: set[Razao] = set()
    if f.sessao_ou_autenticacao:
        achadas.add(Razao.SESSAO_OU_AUTENTICACAO)
    if c is not None:
        if c.risco == "high":
            achadas.add(Razao.RISCO_ALTO)
        if c.politica in _POLITICAS_MANUAIS:
            achadas.add(Razao.POLITICA_MANUAL)
        if c.precisa_rascunho:
            achadas.add(Razao.TEXTO_PARA_OUTRA_PESSOA)
        if c.familia_do_efeito in FAMILIAS_DE_ALTO_RISCO:
            achadas.add(Razao.FAMILIA_DE_ALTO_RISCO)
        if c.risco == "medium":
            achadas.add(Razao.RISCO_MEDIO)
        if c.efeito_externo:
            achadas.add(Razao.EFEITO_DECLARADO)
    if f.side_effect:
        if not f.tem_catalogo:
            achadas.add(Razao.COMMIT_SEM_CATALOGO)
        elif c is None:
            achadas.add(Razao.COMMIT_SEM_FATOS_DA_ETAPA)
        elif not c.efeito_externo:
            # O conteúdo diz que há efeito e o catálogo diz que a etapa não tem: a divergência é a anomalia (§8.4,
            # "vale a mais restritiva"). Com catálogo, um efeito que ele não declara é da pessoa (porta de política).
            achadas.add(Razao.COMMIT_FORA_DO_CATALOGO)
    if f.human_origin:
        achadas.add(Razao.TEXTO_DE_PESSOA)
    return [r for r, _, _ in _REGRAS if r in achadas]


def classificar(fatos: FatosDeRisco) -> Classificacao:
    """A classe do item: a mais restritiva entre as razões achadas; sem razão nenhuma, A."""
    razoes = tuple(_razoes(fatos))
    if not razoes:
        return Classificacao(ClasseDeRisco.A, (), None)
    classe = ClasseDeRisco.C if any(_CLASSE_DA_RAZAO[r] is ClasseDeRisco.C for r in razoes) else ClasseDeRisco.B
    motivo = next(_MOTIVO_DA_RAZAO[r] for r in razoes if _CLASSE_DA_RAZAO[r] is classe)
    return Classificacao(classe, razoes, motivo)


#: Os `source_kind` e os `FailureKind` que tocam sessão, conta ou autenticação (§8.4, linha C). Strings, para o
#: domínio não depender do vocabulário das falhas só por isto; `tests/test_learning_politica_de_risco.py` confere.
ORIGENS_DE_SESSAO = frozenset({"session_unknown"})
FALHAS_DE_SESSAO = frozenset({"autenticacao", "conta_errada"})


def toca_sessao_ou_autenticacao(*, source_kind: str | None = None, falhas: frozenset[str] = frozenset(),
                                tela_autenticada: bool = False) -> bool:
    """Se o item é de sessão e autenticação: nasceu de `session_unknown`, está ligado a falha de autenticação ou de
    conta errada, ou é a regra de uma tela que exige autenticação."""
    return (source_kind in ORIGENS_DE_SESSAO) or bool(falhas & FALHAS_DE_SESSAO) or tela_autenticada


class RecusaDoAceite(StrEnum):
    DECISAO_AUTOMATICA = "decisao_automatica"           # o sistema aplicaria um parecer sozinho
    DECISAO_AUTOMATICA_EM_C = "decisao_automatica_em_c"
    LOTE_NA_CLASSE_C = "lote_na_classe_c"
    PARECER_NA_CLASSE_A = "parecer_na_classe_a"         # a classe A não recebe parecer: não há o que aceitar


def conferir_aceite(classe: ClasseDeRisco, *, por_pessoa: bool, em_lote: bool) -> RecusaDoAceite | None:
    """Se um parecer pode virar decisão assim. `None` = pode (e quem transiciona continua sendo o `ciclo.py`, com o D1
    e o veto). A IA nunca decide: aceitar um parecer é sempre gesto da pessoa; na classe C, um item de cada vez."""
    if classe is ClasseDeRisco.A:
        return RecusaDoAceite.PARECER_NA_CLASSE_A
    if not por_pessoa:
        if classe is ClasseDeRisco.C:
            return RecusaDoAceite.DECISAO_AUTOMATICA_EM_C
        return RecusaDoAceite.DECISAO_AUTOMATICA
    if em_lote and classe is ClasseDeRisco.C:
        return RecusaDoAceite.LOTE_NA_CLASSE_C
    return None


__all__ = ["FALHAS_DE_SESSAO", "FAMILIAS_DE_ALTO_RISCO", "ORIGENS_DE_SESSAO", "ClasseDeRisco", "Classificacao",
           "FatosDeRisco", "FatosDoCatalogo", "MotivoDeEntrada", "PoliticaDaClasse", "Razao", "RecusaDoAceite",
           "classificar", "conferir_aceite", "toca_sessao_ou_autenticacao"]
