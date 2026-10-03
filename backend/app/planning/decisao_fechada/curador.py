"""Triagem do curador do Livro em SOMBRA (item 31.8, R1 do roteiro, ADR-069): o Jev escolhe entre `manter`, `revisar`,
`rebaixar` e `descartar` (mais `nenhuma`) para o mesmo item que o curador principal (30.11/30.12) acabou de revisar.

- **Só sombra.** O parecer do curador principal é devolvido intacto; a triagem vai à porta em `shadow` (assíncrona, fora
  do caminho) e nada do que o Jev responde volta ao Livro. O GO para ligar `on` exige os limiares pré-registrados do golden
  set (31.7); sem eles, esta sombra só registra.
- **Dado F1, classe C0.** O estado são METADADOS e CONTAGENS do dossiê, por campos nomeados (`CAMPOS`): tipo, estado,
  origem, efeito, origem humana, classe e política de risco, contagens de evidência, falha, voto, intervenção e execução, e
  o rótulo de saúde quando é um rótulo. Nada de conteúdo (nem a lição "de texto fechado": fica para quando houver a lista
  dos campos fechados da lição), de app, de capability, de id ou de data. Só `licao` e `receita` (memória fora; fluxo é
  C2, F2).
- **Decisão real = o parecer do curador principal que VALEU**, mapeado (`TRIAGEM_DO_PARECER`), lido do registro
  (`learning_reviews` do mesmo `dossie_hash`, `validade = 'ok'`, `simulated = 0`) pelo relatório do 31.10, com
  `decisao_real_da_triagem`: a validade só existe depois do `revisar` (I2 da revisão do 31.9). Sem voto da pessoa, isto
  mede CONCORDÂNCIA com o curador, não acerto (roteiro R1); o rótulo de acerto é o desfecho posterior do item.

O módulo não importa o aprendizado: o pedido do curador é lido por forma (`PedidoDoCurador`), a resposta passa intacta,
e o decorador cumpre a porta `CuradorDeIA` por estrutura.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Final, Generic, Protocol, TypeVar

from . import privacidade
from .contrato import PedidoDeDecisao, pergunta_choice
from .porta import Porta, modo_efetivo

log = logging.getLogger("poc.ai")

PERGUNTA_TRIAGEM: Final = "curador_triagem"
KINDS_F1: Final[frozenset[str]] = frozenset({"licao", "receita"})
OPCOES: Final[dict[str, str]] = {
    "opt:manter": "keep: the item works as it is",
    "opt:revisar": "review: a person should look at it",
    "opt:rebaixar": "demote: lower its stage, it is not reliable now",
    "opt:descartar": "discard: it should leave the book",
}
#: O parecer do curador principal (`learning/domain/curador.Decisao`) na régua da triagem: o mapeamento fixo combinado com a
#: frente Aprendizado (resposta ao 31.7). `aprovar`, `possivelmente_obsoleto`, `substituir` e `fundir` não têm par na régua
#: grossa (alvo, ou mudança de estágio que a triagem não diz): ficam fora da comparação, sem decisão real casada.
TRIAGEM_DO_PARECER: Final[Mapping[str, str]] = {
    "manter": "opt:manter",
    "observar": "opt:revisar", "pedir_evidencia": "opt:revisar",
    "rebaixar": "opt:rebaixar",
    "desativar": "opt:descartar",
}
_INSTRUCOES: Final = (
    "The state describes one item that a device-automation platform learned by itself (a lesson or a recipe), only as "
    "counts and categories. Pick what should happen to it, or none if the facts do not tell.")
#: Campos que saem (CAMPOS_POR_ORIGEM["curador"]). Todo valor é um rótulo curto de vocabulário fechado ou um número.
CAMPOS: Final[frozenset[str]] = frozenset({
    "kind", "estado", "origem", "side_effect", "human_origin", "classe_de_risco", "politica",
    "evidencias_total", "evidencias_a_favor", "evidencias_contra", "evidencias_simuladas",
    "falhas", "falhas_ocorrencias", "votos", "intervencoes", "execucoes", "saude"})
_ROTULO_MAX: Final = 40


class PedidoDoCurador(Protocol):
    @property
    def dossie(self) -> Mapping[str, object]: ...
    @property
    def dossie_hash(self) -> str: ...


# O pedido e a resposta são genéricos para o embrulho devolver EXATAMENTE o tipo do curador que ele decora (a porta
# `CuradorDeIA` do aprendizado pede `RespostaDeRevisao`, não a forma mais larga lida aqui).
Pedido = TypeVar("Pedido", bound=PedidoDoCurador, contravariant=True)
Resposta = TypeVar("Resposta", covariant=True)


class CuradorInterno(Protocol[Pedido, Resposta]):
    @property
    def provedor(self) -> str: ...
    @property
    def simulado(self) -> bool: ...

    def revisar(self, pedido: Pedido) -> Resposta: ...


def _rotulo(valor: object) -> str | None:
    """Só rótulo de vocabulário (minúsculas, dígitos, `_`, `-`) e curto; qualquer outra coisa não sai."""
    if isinstance(valor, bool):
        return "sim" if valor else "nao"
    if isinstance(valor, str) and 0 < len(valor) <= _ROTULO_MAX and all(c.islower() or c.isdigit() or c in "_-"
                                                                        for c in valor):
        return valor
    return None


def _lista(dossie: Mapping[str, object], chave: str) -> list[Mapping[str, object]]:
    valor = dossie.get(chave)
    return [x for x in valor if isinstance(x, Mapping)] if isinstance(valor, list) else []


def estado_do_dossie(dossie: Mapping[str, object]) -> dict[str, str]:
    """O estado C0 do item: só os `CAMPOS`, cada um rótulo fechado ou contagem. Campo sem valor confiável fica de fora."""
    item = dossie.get("item")
    item = item if isinstance(item, Mapping) else {}
    risco = dossie.get("risco")
    risco = risco if isinstance(risco, Mapping) else {}
    evid = dossie.get("evidencias")
    evid = evid if isinstance(evid, Mapping) else {}
    lista = [e for e in evid.get("lista", []) if isinstance(e, Mapping)] if isinstance(evid.get("lista"), list) else []
    falhas = _lista(dossie, "falhas")
    saude = dossie.get("saude")
    candidatos: dict[str, object] = {
        "kind": item.get("kind"), "estado": item.get("estado"), "origem": item.get("origem"),
        "side_effect": item.get("side_effect"), "human_origin": item.get("human_origin"),
        "politica": risco.get("politica"),
        "saude": saude.get("rotulo") if isinstance(saude, Mapping) else None,
    }
    estado = {k: r for k, v in candidatos.items() if (r := _rotulo(v)) is not None}
    if risco.get("classe") in ("A", "B", "C"):                 # a classe é maiúscula no vocabulário do aprendizado
        estado["classe_de_risco"] = str(risco["classe"])
    total = evid.get("total")
    contagens = {
        "evidencias_total": total if isinstance(total, int) and not isinstance(total, bool) else len(lista),
        "evidencias_a_favor": sum(1 for e in lista if e.get("posicao") == "for"),
        "evidencias_contra": sum(1 for e in lista if e.get("posicao") not in (None, "for")),
        "evidencias_simuladas": sum(1 for e in lista if e.get("simulated") is True),
        "falhas": len(falhas),
        "falhas_ocorrencias": sum(o for f in falhas if isinstance(o := f.get("ocorrencias"), int)
                                  and not isinstance(o, bool)),
        "votos": len(_lista(dossie, "votos")),
        "intervencoes": len(_lista(dossie, "intervencoes")),
        "execucoes": len(dossie["execucoes"]) if isinstance(dossie.get("execucoes"), list) else 0,
    }
    estado.update({k: str(v) for k, v in contagens.items()})
    return estado


class TriagemDoCurador:
    """O consumidor: monta o pedido C0 e o entrega à porta em sombra. Inerte com a config padrão (`ativo()` falso).

    NÃO casa a decisão real (I2 da revisão do 31.9): o parecer do curador principal só vale depois de `validar_saida`, que
    o aprendizado roda DEPOIS do `revisar`, e pode ser simulado. A decisão real sai do registro no relatório do 31.10:
    `learning_reviews` do mesmo `dossie_hash` (o `ref` da linha da sombra), por `decisao_real_da_triagem`. Sem casamento,
    não há thread nem espera própria: no desligamento basta a porta (`Porta.encerrar` e `aguardar_sombras`)."""

    def __init__(self, porta: Porta) -> None:
        self._porta = porta

    def ativo(self) -> bool:
        """Como `ConsumidorDeIntencao.ativo`: com o envio fechado no código, NADA é feito (nem recusa gravada). A porta
        recusaria por privacidade e mediria a recusa, mas o que se mede aqui é o Jev respondendo, e ele não responde com o
        interruptor fechado. Aberto desde o 31.17: vale a config (`curador: shadow` com a porta ligada)."""
        return privacidade.JEV_RUNTIME_SEND_APPROVED and modo_efetivo("shadow", self._porta.cfg, "curador") == "shadow"

    @staticmethod
    def pedido(dossie: Mapping[str, object], dossie_hash: str) -> PedidoDeDecisao | None:
        """O pedido de sombra do item, ou `None` fora de F1 (memória, fluxo, tela, voz, preferência)."""
        item = dossie.get("item")
        kind = item.get("kind") if isinstance(item, Mapping) else None
        if kind not in KINDS_F1:
            return None
        return PedidoDeDecisao(origem="curador", classe="C0", estado=estado_do_dossie(dossie),
                               perguntas=(pergunta_choice(PERGUNTA_TRIAGEM, _INSTRUCOES, OPCOES),), modo="shadow",
                               ref=dossie_hash)

    def observar(self, dossie: Mapping[str, object], dossie_hash: str) -> None:
        """Entrega a sombra à porta (em `shadow` ela agenda e volta na hora). Nunca levanta: medir nunca derruba o curador."""
        try:
            if not self.ativo():
                return
            pedido = self.pedido(dossie, dossie_hash)
            if pedido is not None:
                self._porta.consultar(pedido)
        except Exception:  # noqa: BLE001
            log.warning("decisao_fechada: falha na sombra da triagem do curador")


def decisao_real_da_triagem(decisao: object, *, validade: object, simulado: object) -> str | None:
    """A decisão real da R1, para o relatório do 31.10 sobre a linha de `learning_reviews` do mesmo `dossie_hash`: o
    parecer do curador principal que VALEU (`validade == 'ok'`) e não veio de provedor simulado, na régua da triagem
    (`TRIAGEM_DO_PARECER`). Parecer inválido ou recusado, simulado, ou sem par na régua grossa: `None` (a sombra só
    registrou a escolha do Jev; não há com o que comparar)."""
    if validade != "ok" or simulado or not isinstance(decisao, str):
        return None
    real = TRIAGEM_DO_PARECER.get(decisao)
    return real if real in OPCOES else None


class CuradorComTriagemEmSombra(Generic[Pedido, Resposta]):
    """Decora o curador principal (porta `CuradorDeIA` do aprendizado): devolve o parecer DELE, intacto, e só depois
    entrega o item à triagem em sombra. Falha do curador principal sobe como antes, sem sombra."""

    def __init__(self, interno: CuradorInterno[Pedido, Resposta], triagem: TriagemDoCurador) -> None:
        self._interno = interno
        self._triagem = triagem

    @property
    def provedor(self) -> str:
        return self._interno.provedor

    @property
    def simulado(self) -> bool:
        return self._interno.simulado

    def revisar(self, pedido: Pedido) -> Resposta:
        resposta = self._interno.revisar(pedido)
        self._triagem.observar(pedido.dossie, pedido.dossie_hash)
        return resposta


__all__ = ["CAMPOS", "KINDS_F1", "OPCOES", "PERGUNTA_TRIAGEM", "TRIAGEM_DO_PARECER", "CuradorComTriagemEmSombra",
           "TriagemDoCurador", "decisao_real_da_triagem", "estado_do_dossie"]
