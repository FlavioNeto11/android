"""31.153: as etapas ensinadas como ações conhecidas do app para o planejador livre. Puro: sem banco.

Medido em 06/10: a receita ensinada só é achada pela chave da etapa (o `template_hash`), o plano livre gera etapas com
outro texto e outro hash, e as 27 receitas ensinadas eram usadas só pelos próprios fluxos, quase todos desligados.

Agora cada etapa ensinada com receita estável (ativa, 1 ou mais reproduções boas, sem efeito externo) é oferecida ao
planejador livre do mesmo app pelo NOME (a chave da etapa), com os nomes dos parâmetros. Quando o plano livre tem uma
etapa com esse nome, o código a troca pela etapa-molde do ensino: o `template_hash` passa a ser o da receita, e o
executor a roda sem IA (e cai na IA se divergir, como em qualquer receita). Não muda o esquema de saída do plano.

A etapa com efeito externo não é oferecida: ela segue pela aprovação do plano e pelo catálogo. O escopo da receita é o
do 30.81 (vale para quem ensinou até o "Confirmar que fica"); a decisão do dono (a ação ensinada por uma persona serve
a todas ou só ao escopo de quem ensinou) não muda isso aqui.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from ..models import PlanStep
from ..modules.learning.domain.licoes import ACAO_DE_SESSAO
from .habilidades import RESERVADOS

#: O nome de uma etapa ensinada oferecida: o formato da chave livre (sem dígito, sem valor).
_NOME = re.compile(r"^[a-z][a-z_]{2,40}$")
_MARCADOR = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
#: Quantas etapas ensinadas vão ao prompt.
MAXIMO = 12


@dataclass(frozen=True, slots=True)
class EtapaEnsinada:
    nome: str                       # a chave da etapa no ensino
    app_id: str
    passo: PlanStep                 # a etapa-molde (`{nome}` no lugar dos valores)
    receita: int
    reproducoes: int
    origem: str                     # `training:<sessão>`; na descoberta, o `steps.id` da etapa exploratória
    #: 31.273 (ADR-084): a etapa que a IA DESCOBRIU numa exploração e a receita comprovou; ninguém a demonstrou. O
    #: planejador e a trilha precisam saber a diferença, e a ensinada por pessoa ganha o nome repetido.
    descoberta: bool = False

    @property
    def parametros(self) -> tuple[str, ...]:
        texto = " ".join([self.passo.title, self.passo.goal, self.passo.postcondition.value or "",
                          self.passo.postcondition.description or ""])
        return tuple(n for n in dict.fromkeys(_MARCADOR.findall(texto)) if n not in RESERVADOS)

    def linha(self) -> str:
        """Só nome, app e parâmetros: o título do ensino pode trazer o valor demonstrado (um nome próprio passa por
        qualquer filtro de @, endereço e número), e o prompt vai a todas as personas do app."""
        params = ", ".join(self.parametros) or "nenhum"
        return f"- nome: {self.nome} | app: {self.app_id} | parâmetros: {params}"


def _normal(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto.casefold()) if not unicodedata.combining(c))


def oferecivel(passo: PlanStep, exemplos: Iterable[str] = ()) -> bool:
    """Fora: a etapa com efeito, com `commit_guard` ou do catálogo; a de sessão, login ou desafio (`ACAO_DE_SESSAO`:
    o planejador livre não passa a acionar um login ensinado); e a chave que carrega um valor demonstrado (`exemplos`,
    os valores dos parâmetros do fluxo: "seguir_joao_silva" iria ao prompt de todas as personas do app)."""
    if not _NOME.match(passo.key) or passo.side_effect or passo.commit_guard or passo.capability:
        return False
    if ACAO_DE_SESSAO.search(passo.key):
        return False
    chave = _normal(passo.key.replace("_", " "))
    palavras = {p for v in exemplos for p in re.findall(r"[^\W\d_]{3,}", _normal(str(v).lstrip("@")))}
    return not any(re.search(r"(?<![a-z])" + re.escape(p) + r"(?![a-z])", chave) for p in palavras)


def oferecivel_descoberta(passo: PlanStep, exemplos: Iterable[str] = ()) -> bool:
    """31.273: a etapa descoberta por exploração só é oferecida como molde se NADA nela carrega o valor de uma pessoa.
    O planejador livre escreve o título e o objetivo à vontade (o fluxo ensinado vem de um molde com `{nome}` no lugar
    do valor), então, além do que vale para a ensinada (`oferecivel`), a etapa não pode ter digitação (`bindings`) nem
    ter nenhum valor dos parâmetros do plano no título, no objetivo, na pré-condição ou na pós-condição. A etapa
    exploratória que nasceu com o valor escrito fica de fora; a receita dela segue valendo pelo caminho de sempre."""
    exemplos = list(exemplos)
    if not oferecivel(passo, exemplos) or passo.bindings or passo.for_each or passo.template_key:
        return False
    texto = _normal(" ".join(filter(None, [passo.title, passo.goal, passo.precondition, passo.postcondition.value,
                                           passo.postcondition.description])))
    valores = {_normal(str(v).lstrip("@").strip()) for v in exemplos}
    return not any(v and v in texto for v in valores)


def _com_efeito(passo: PlanStep) -> bool:
    """A etapa do plano que o planejador marcou com efeito, trava, ação do catálogo ou digitação: trocá-la pelo molde
    (que não tem essas marcas) a tiraria da aprovação e do catálogo."""
    return bool(passo.side_effect or passo.commit_guard or passo.capability or passo.bindings)


def escolher(candidatas: Sequence[EtapaEnsinada], apps: Sequence[str]) -> list[EtapaEnsinada]:
    """As do app do plano, uma por (app, nome): a de mais reproduções boas (depois a receita mais nova)."""
    melhores: dict[tuple[str, str], EtapaEnsinada] = {}
    for e in candidatas:
        if e.app_id not in apps:
            continue
        atual = melhores.get((e.app_id, e.nome))
        # a ensinada por pessoa vence a descoberta de mesmo nome (31.273); entre iguais, a de mais reproduções
        if atual is None or (not e.descoberta, e.reproducoes, e.receita) > (not atual.descoberta, atual.reproducoes,
                                                                            atual.receita):
            melhores[(e.app_id, e.nome)] = e
    return sorted(melhores.values(), key=lambda e: (e.descoberta, -e.reproducoes, e.app_id, e.nome))[:MAXIMO]


def bloco(etapas: Sequence[EtapaEnsinada]) -> str:
    """O bloco do texto de USUÁRIO, seguido de linha em branco; sem etapa, nada. As ensinadas por pessoa e as
    descobertas pela IA (31.273) vão em blocos separados: o planejador sabe quais foram demonstradas e quais não."""
    return _bloco([e for e in etapas if not e.descoberta], "etapas_ensinadas",
                  "demonstradas por uma pessoa e reproduzidas sem IA") + _bloco(
        [e for e in etapas if e.descoberta], "etapas_descobertas",
        "descobertas pela IA numa exploração e comprovadas sem IA; nenhuma pessoa as demonstrou")


def _bloco(etapas: Sequence[EtapaEnsinada], tag: str, origem: str) -> str:
    if not etapas:
        return ""
    linhas = "\n".join(e.linha() for e in etapas)
    return (f"<{tag} origem=\"{origem}\">\n"
            f"{linhas}\n"
            "Quando uma etapa do plano faz exatamente o que o nome de uma destas diz, no mesmo app, use como `key` "
            "dela o `nome` "
            "acima e declare em `parameters` o valor de cada parâmetro dela (tirado do comando). O sistema roda a "
            "etapa ensinada.\n"
            f"</{tag}>\n\n")


def trocar(passos: Sequence[PlanStep], app_do_plano: str | None, oferecidas: Sequence[EtapaEnsinada],
           parametros: Mapping[str, str]) -> tuple[list[PlanStep], list[tuple[str, EtapaEnsinada]], list[str]]:
    """(etapas, trocadas, recusas): a etapa do plano com o nome de uma oferecida, no mesmo app e com todos os
    parâmetros declarados no plano, vira a etapa-molde do ensino (mantendo as dependências e o app da etapa do plano).
    Faltando parâmetro, ou se a etapa do plano tem efeito (`_com_efeito`), fica a do plano, e a recusa diz por quê."""
    por = {(e.app_id, e.nome): e for e in oferecidas}
    saida: list[PlanStep] = []
    trocadas: list[tuple[str, EtapaEnsinada]] = []
    recusas: list[str] = []
    for s in passos:
        e = por.get((s.app_id or app_do_plano or "", s.key))
        if e is None:
            saida.append(s)
            continue
        if _com_efeito(s):
            recusas.append(f"{s.key}: a etapa do plano tem efeito, trava ou ação do catálogo")
            saida.append(s)
            continue
        faltam = [p for p in e.parametros if not str(parametros.get(p, "")).strip()]
        if faltam:
            recusas.append(f"{s.key}: o plano não declarou " + ", ".join("{" + p + "}" for p in faltam))
            saida.append(s)
            continue
        saida.append(e.passo.model_copy(update={"depends_on": list(s.depends_on), "app_id": s.app_id,
                                                "for_each": s.for_each}))
        trocadas.append((s.key, e))
    return saida, trocadas, recusas


__all__ = ["MAXIMO", "EtapaEnsinada", "bloco", "escolher", "oferecivel", "oferecivel_descoberta", "trocar"]
