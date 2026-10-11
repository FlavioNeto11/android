"""A execução de prova que NÃO vale como evidência do fluxo (item 30.42, desenho aprovado pela orquestradora em 03/10).

A prova de fluxo (30.37) grava UMA linha de evidência do fluxo provado. Três desfechos não dizem nada sobre o fluxo e
viram a posição `invalida` (`Posicao.INVALIDA`): nem a favor nem contra, à vista na trilha, fora das contagens:

- `efeito_repetido`: o efeito saiu mais de uma vez dentro da prova (o caso da `5f2de5`, a mensagem enviada duas
  vezes, que tinha virado evidência a favor);
- `ponto_de_partida`: a etapa de abertura não chegou ao ponto de partida do fluxo (o caso da `e1b7d0`, o app dentro
  de uma conversa deixada pela execução anterior, que tinha virado evidência contra);
- `ator_sem_acao`: a etapa reprovou sem ação do ator além de `step_done` (o ator declarou pronto sem agir).

O motivo vai no começo do `detail` da linha, num formato fechado: o desfecho da validação o lê para fechar o pedido
com o motivo de mesmo nome (`domain/validacao.Motivo`), e o painel o mostra. Puro: sem banco, sem relógio.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.modules.learning.domain.vocabulario import Posicao


class MotivoDaInvalida(StrEnum):
    """Por que a prova não vale. Vocabulário fechado: o mesmo valor é o `Motivo` do pedido de validação."""

    EFEITO_REPETIDO = "efeito_repetido"
    PONTO_DE_PARTIDA = "ponto_de_partida"
    ATOR_SEM_ACAO = "ator_sem_acao"


#: O começo do `detail` de uma linha `invalida`, depois da marca do conteúdo (`[xxxxxxxxxxxx] `) quando há.
PREFIXO = "invalida:"
_MOTIVO = re.compile(r"^(?:\[[0-9a-f]{6,}\]\s*)?invalida:([a-z_]+)")


def detalhe_da_invalida(motivo: MotivoDaInvalida, texto: str, *, marca: str | None = None) -> str:
    """O `detail` da linha: `[marca] invalida:<motivo> — <texto>`. O texto é técnico e curto (etapa, contagem), nunca
    texto de tela nem de pessoa; a coluna corta em 200 caracteres."""
    base = f"{PREFIXO}{motivo.value} — {texto}" if texto else f"{PREFIXO}{motivo.value}"
    return f"[{marca}] {base}" if marca else base


#: O motivo como o painel e o resumo da execução o dizem.
ROTULO_DO_MOTIVO: Mapping[MotivoDaInvalida, str] = {
    MotivoDaInvalida.EFEITO_REPETIDO: "efeito repetido", MotivoDaInvalida.PONTO_DE_PARTIDA: "ponto de partida",
    MotivoDaInvalida.ATOR_SEM_ACAO: "o ator não agiu"}


def motivo_da_invalida(detalhe: str | None) -> MotivoDaInvalida | None:
    """O motivo de um `detail` de linha `invalida`, ou `None` (outro texto, ou motivo fora do vocabulário)."""
    m = _MOTIVO.match(detalhe or "")
    if m is None:
        return None
    try:
        return MotivoDaInvalida(m.group(1))
    except ValueError:
        return None


_ETAPA_CITADA = re.compile(r"\betapa (\d+) \(([A-Za-z0-9_.-]+)\)")


def etapa_citada(detalhe: str | None) -> tuple[int, str] | None:
    """A etapa que o `detail` de uma evidência cita ("etapa 5 (send_message): reproduzida", "prova: etapa 5
    (send_message) reprovada: …"): (posição, chave). Só a PRIMEIRA citação; sem ela, `None`. O painel usa a chave para
    ler o título da etapa na execução, na hora da leitura: o título é texto do planejador e nunca vai para o `detail`."""
    m = _ETAPA_CITADA.search(detalhe or "")
    return None if m is None else (int(m.group(1)), m.group(2))


# ------------------------------------------------------------------ o veredito da prova (a regra, só sobre dados)
#: Ferramentas que não mexem no aparelho: quem só as usou não agiu (o ator observou e declarou pronto).
SEM_ACAO = frozenset({"step_done", "step_blocked", "observe_screen", "find_element", "find_row", "wait_for", "verify_state"})
#: Ferramentas que podem disparar um efeito externo (`automation.tools.EFFECT_CAPABLE`).
COM_EFEITO = frozenset({"tap", "long_press", "drag", "type_text"})


@dataclass(frozen=True, slots=True)
class AcaoDaProva:
    """Uma ação do diário (`actions`) de uma etapa da prova. `parece_commit`: o alvo resolvido tem cara de envio
    (`automation.tools.looks_like_commit`, calculado por quem lê o banco, que tem o alvo)."""

    tool: str
    status: str
    is_commit_action: bool = False
    parece_commit: bool = False

    @property
    def agiu(self) -> bool:
        """Mexeu no aparelho: leitura e controle (`step_done`) não são ação, e a recusada pelo executor nem saiu."""
        return self.tool not in SEM_ACAO and self.status != "rejected"

    @property
    def efeito(self) -> bool:
        """Disparou (ou pode ter disparado) um efeito externo: ferramenta de efeito, concluída, declarada commit ou com
        alvo de cara de envio."""
        return self.tool in COM_EFEITO and self.status == "done" and (self.is_commit_action or self.parece_commit)


@dataclass(frozen=True, slots=True)
class TentativaDaProva:
    """A ÚLTIMA tentativa de uma etapa: o erro de infra (`error_kind`, RA-22), o tipo da falha e o que o ator fez."""

    error_kind: str | None
    failure_kind: str | None
    acoes: tuple[AcaoDaProva, ...] = ()

    @property
    def agiu(self) -> bool:
        return any(a.agiu for a in self.acoes)


@dataclass(frozen=True, slots=True)
class EtapaDaProva:
    """Uma etapa executada da prova (a etapa-modelo do `for_each` nunca chega aqui). `acoes`: as de TODAS as tentativas;
    `efeito_do_resultado`: o que `steps.result["efeito_repetido"]` traz (contrato com o 29.58), `None` se ausente."""

    seq: int
    key: str
    status: str
    plan_version: int
    side_effect: bool
    driven_by: str | None = None
    detalhe: str | None = None
    ultima: TentativaDaProva | None = None
    acoes: tuple[AcaoDaProva, ...] = ()
    efeito_do_resultado: object = None


@dataclass(frozen=True, slots=True)
class VereditoDaProva:
    """O desfecho da prova: `posicao` `FOR`, `AGAINST`, `INVALIDA` (com `motivo`) ou `None` (sem evidência)."""

    posicao: Posicao | None
    motivo: MotivoDaInvalida | None
    texto: str


def _copias_do_resultado(valor: object) -> int | None:
    """`{"copias": int >= 2, "fonte": "verificador" | "acoes"}`; qualquer outra forma é ignorada (a regra própria vale)."""
    if not isinstance(valor, Mapping):
        return None
    copias = valor.get("copias")
    if isinstance(copias, bool) or not isinstance(copias, int) or copias < 2:
        return None
    return copias


def efeito_repetido(etapas: Sequence[EtapaDaProva], *, regra_propria: bool = True) -> int | None:
    """A ÚNICA fonte do "o efeito saiu mais de uma vez" (AJUSTE 6: trocar a fonte é trocar esta função). Devolve as
    cópias, ou `None` quando não houve repetição.

    1. `steps.result["efeito_repetido"]` (29.58, o verificador ou o diário): vence, e a chave ausente é "sem repetição".
    2. A regra própria, sobre o diário: mais de uma ação de efeito concluída numa etapa com efeito externo, ou uma ação
       de efeito concluída numa etapa SEM efeito (o toque no botão de enviar durante a abertura do app). Conta as
       tentativas todas da etapa: o efeito da primeira que "falhou" já saiu.

    `regra_propria=False` (30.43): só o resultado do 29.58. É o que vale na execução orgânica, onde a IA livre deixa
    mais ruído no diário do que a execução de validação, que parte de estado conhecido."""
    do_resultado = [c for e in etapas if (c := _copias_do_resultado(e.efeito_do_resultado)) is not None]
    if do_resultado:
        return max(do_resultado)
    if not regra_propria:
        return None
    total, repetido = 0, False
    for e in etapas:
        n = sum(1 for a in e.acoes if a.efeito)
        total += n
        repetido = repetido or n > (1 if e.side_effect else 0)
    return max(total, 2) if repetido else None


#: O começo do motivo da versão do plano que a EXPANSÃO do `for_each` cria (`Scheduler._expand_for_each`, gravado em
#: `plan_versions.reason`). A expansão não é replanejamento: as cópias por item SÃO o plano do fluxo.
PREFIXO_DA_EXPANSAO = "Expandido para "

#: 30.48: o rastro da amostra no motivo da expansão da prova (`plan_versions.reason`), lido de volta pelo veredito.
_AMOSTRA = re.compile(r"amostra de (\d+) de (\d+) itens")


def rastro_da_amostra(usados: int, total: int) -> str:
    """30.48: o que a expansão da PROVA diz da lista: a amostra (os N primeiros) ou a prova inteira (lista ≤ N)."""
    if usados < total:
        return f"prova de fluxo: amostra de {usados} de {total} itens, os primeiros na ordem da tela"
    return f"prova de fluxo: prova inteira, {total} de {total} itens"


_AMOSTRA_NA_EVIDENCIA = re.compile(r"em amostra de (\d+) \(de (\d+) itens\)")


def amostra_da_evidencia(detalhe: str | None) -> str | None:
    """30.48: `"N de M"` quando a evidência é de prova em amostra (o rótulo que o veredito põe no `detail`), ou `None`."""
    achado = _AMOSTRA_NA_EVIDENCIA.search(detalhe or "")
    return f"{achado.group(1)} de {achado.group(2)}" if achado is not None else None


def amostra_do_rastro(motivos: Sequence[str]) -> tuple[int, int] | None:
    """30.48: `(usados, total)` da amostra registrada na expansão, ou `None` (sem amostra: lista inteira ou sem laço)."""
    for m in motivos:
        achado = _AMOSTRA.search(m)
        if achado is not None:
            return int(achado.group(1)), int(achado.group(2))
    return None


def _da_expansao(e: EtapaDaProva, expansoes: frozenset[int]) -> bool:
    """A etapa aberta que a expansão do `for_each` pulou (`plano revisado (vN)`, com N uma versão de expansão): o
    lugar dela é a cópia da versão nova, e ela não conta no veredito."""
    return e.status == "skipped" and any(e.detalhe == f"plano revisado (v{v})" for v in expansoes)


def veredito_da_prova(etapas: Sequence[EtapaDaProva], *, status: str,
                      expansoes: frozenset[int] = frozenset()) -> VereditoDaProva:
    """A regra única do veredito da execução de prova (30.42), só sobre dados.

    Ordem: o efeito repetido (vale mesmo com a execução completa); plano acima de v1 (um plano novo não é mais o fluxo:
    sem evidência, e as versões nunca se misturam); infra (`error_kind`: orçamento, IA, aparelho, cancelamento pelo
    sistema) é sem evidência; a falha da ABERTURA é do ponto de partida; etapa reprovada sem ação do ator é do ator;
    CONTRA só quando a etapa agiu e a pós-condição do fluxo não veio; A FAVOR com todas as etapas comprovadas."""
    nada = VereditoDaProva(None, None, f"prova sem desfecho de tarefa ({status})")
    if not etapas:
        return nada
    copias = efeito_repetido(etapas)
    if copias is not None:
        onde = (next((e for e in etapas if _copias_do_resultado(e.efeito_do_resultado)), None)
                or next((e for e in etapas if sum(1 for a in e.acoes if a.efeito) > (1 if e.side_effect else 0)), None))
        quando = f" (etapa {onde.seq}, {onde.key})" if onde is not None else ""
        return VereditoDaProva(Posicao.INVALIDA, MotivoDaInvalida.EFEITO_REPETIDO,
                               f"o efeito saiu {copias} vezes{quando}")
    # `expansoes`: as versões do plano que só expandiram o `for_each` (não são replanejamento; `PREFIXO_DA_EXPANSAO`).
    # Sem isto, toda prova de fluxo com `for_each` caía em "plano revisado" (a expansão sobe a versão).
    etapas = [e for e in etapas if not _da_expansao(e, expansoes)]
    if any(e.plan_version > 1 and e.plan_version not in expansoes for e in etapas):
        return VereditoDaProva(None, None, "prova com plano revisado: um plano novo não é mais o fluxo")
    ordem = sorted(etapas, key=lambda e: (e.plan_version, e.seq))   # a `seq` recomeça em cada versão
    reprovadas = [e for e in ordem if e.status == "failed"]
    if reprovadas:
        if any(e.ultima is None or e.ultima.error_kind for e in reprovadas):
            return nada                                    # infra (ou sem tentativa): nem a favor nem contra
        r = reprovadas[0]
        assert r.ultima is not None
        if r is ordem[0]:
            return VereditoDaProva(Posicao.INVALIDA, MotivoDaInvalida.PONTO_DE_PARTIDA,
                                   f"a etapa de abertura ({r.key}) não chegou ao ponto de partida do fluxo")
        if not r.ultima.agiu:
            return VereditoDaProva(Posicao.INVALIDA, MotivoDaInvalida.ATOR_SEM_ACAO,
                                   f"o ator não agiu na etapa {r.seq} ({r.key}): só observou e declarou pronto")
        motivo = (r.detalhe or "sem detalhe")[:160]
        return VereditoDaProva(Posicao.AGAINST, None, f"prova: etapa {r.seq} ({r.key}) reprovada: {motivo}")
    if status == "completed" and all(e.status == "succeeded" for e in ordem):
        return VereditoDaProva(Posicao.FOR, None, f"prova: {len(ordem)}/{len(ordem)} etapas comprovadas")
    return nada



def evidencia_de_uso(posicao: Posicao | None, texto: str, *, conta_contra: bool) -> tuple[Posicao, str] | None:
    """30.51: o que a execução comum que USOU o fluxo ativo deixa, dado o veredito da MESMA regra da prova
    (`veredito_da_prova`: a `posicao` e o `texto`).

    Só `FOR` e `AGAINST`: a `INVALIDA` e o "sem desfecho" não dizem nada do fluxo e não viram linha. `conta_contra`
    falso (ensaio, lote de teste, execução cancelada) tira o `AGAINST`: o que a pessoa ou o teste interrompeu não é o
    fluxo falhando. O texto troca o "prova:" por "uso:", para quem lê a evidência saber de onde ela veio."""
    if posicao is Posicao.FOR or (posicao is Posicao.AGAINST and conta_contra):
        return posicao, ("uso:" + texto[len("prova:"):] if texto.startswith("prova:") else texto)
    return None

def conferencia_revalida(fatos: Sequence[Mapping[str, object]], esperadas: int) -> bool:
    """30.53: a `invalida:efeito_repetido` da execução se desfaz pela regra de hoje? Só quando TODO fato de repetição
    gravado nas etapas é da conferência do QA de antes do 30.53 (`fonte: provedor`, sem `esperadas`), e as cópias que ela
    contou (as mensagens da execução no app) não passam das etapas de efeito comprovadas (`esperadas`, uma por item do
    `for_each`). O fato com `esperadas` já é da regra nova (só se grava acima delas), e o do verificador ou do diário
    nunca se revalida aqui."""
    if not fatos or esperadas < 1:
        return False
    for f in fatos:
        if f.get("fonte") != "provedor" or f.get("esperadas") is not None:
            return False
        copias = f.get("copias")
        if not isinstance(copias, int) or copias > esperadas:
            return False
    return True

__all__ = ["AcaoDaProva", "COM_EFEITO", "EtapaDaProva", "MotivoDaInvalida", "PREFIXO", "PREFIXO_DA_EXPANSAO",
           "ROTULO_DO_MOTIVO", "SEM_ACAO", "TentativaDaProva", "VereditoDaProva", "amostra_da_evidencia",
           "amostra_do_rastro", "conferencia_revalida", "detalhe_da_invalida", "efeito_repetido", "evidencia_de_uso",
           "motivo_da_invalida", "rastro_da_amostra", "veredito_da_prova"]
