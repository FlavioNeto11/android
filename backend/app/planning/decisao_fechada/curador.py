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
- **Decisão real = o parecer do curador principal**, mapeado (`TRIAGEM_DO_PARECER`). Sem voto da pessoa, isto mede
  CONCORDÂNCIA com o curador, não acerto (roteiro R1); o rótulo de acerto é o desfecho posterior do item.
- O casamento espera a linha da sombra existir numa thread própria: a volta do curador não espera nada.

O módulo não importa o aprendizado: o pedido e a resposta do curador são lidos por forma (`PedidoDoCurador`,
`RespostaDoCurador`), e o decorador cumpre a porta `CuradorDeIA` por estrutura.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import Final, Protocol

from .contrato import PedidoDeDecisao, pergunta_choice
from .porta import TIMEOUT_SHADOW_S, Porta, modo_efetivo
from .sombra import RepositorioDeSombra

log = logging.getLogger("poc.ai")

PERGUNTA_TRIAGEM: Final = "curador_triagem"
KINDS_F1: Final[frozenset[str]] = frozenset({"licao", "receita"})
OPCOES: Final[dict[str, str]] = {
    "opt:manter": "keep: the item works as it is",
    "opt:revisar": "review: a person should look at it",
    "opt:rebaixar": "demote: lower its stage, it is not reliable now",
    "opt:descartar": "discard: it should leave the book",
}
#: O parecer do curador principal (`learning/domain/curador.Decisao`) na régua da triagem. `substituir`/`fundir` tiram o
#: item do Livro em favor de outro: `descartar`.
TRIAGEM_DO_PARECER: Final[Mapping[str, str]] = {
    "manter": "opt:manter", "aprovar": "opt:manter",
    "observar": "opt:revisar", "pedir_evidencia": "opt:revisar", "possivelmente_obsoleto": "opt:revisar",
    "rebaixar": "opt:rebaixar",
    "desativar": "opt:descartar", "substituir": "opt:descartar", "fundir": "opt:descartar",
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


class RespostaDoCurador(Protocol):
    @property
    def bruto(self) -> Mapping[str, object]: ...


class CuradorInterno(Protocol):
    provedor: str
    simulado: bool

    def revisar(self, pedido: PedidoDoCurador) -> RespostaDoCurador: ...


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
    """O consumidor: monta o pedido C0 e casa a decisão real. Inerte com a config padrão (`ativo()` falso)."""

    def __init__(self, porta: Porta, repositorio: RepositorioDeSombra, *,
                 espera_s: float = TIMEOUT_SHADOW_S + 2.0) -> None:
        self._porta = porta
        self._repositorio = repositorio
        self._espera_s = espera_s
        self._pool: ThreadPoolExecutor | None = None

    def ativo(self) -> bool:
        return modo_efetivo("shadow", self._porta.cfg, "curador") == "shadow"

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

    def observar(self, dossie: Mapping[str, object], dossie_hash: str, decisao_do_parecer: object) -> None:
        """Agenda a sombra e o casamento. Nunca levanta: medir nunca derruba o curador."""
        try:
            if not self.ativo():
                return
            pedido = self.pedido(dossie, dossie_hash)
            if pedido is None:
                return
            self._porta.consultar(pedido)            # shadow: a porta agenda e volta na hora
            real = TRIAGEM_DO_PARECER.get(decisao_do_parecer) if isinstance(decisao_do_parecer, str) else None
            if real is not None:
                self._executor().submit(self._casar, dossie_hash, real)
        except Exception:  # noqa: BLE001
            log.warning("decisao_fechada: falha na sombra da triagem do curador")

    def aguardar(self) -> None:
        """Testes e desligamento: espera os casamentos pendentes."""
        if self._pool is not None:
            self._pool.shutdown(wait=True)
            self._pool = None

    def _executor(self) -> ThreadPoolExecutor:
        if self._pool is None:
            self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sombra-curador")
        return self._pool

    def _casar(self, ref: str, real: str) -> None:
        limite = time.monotonic() + self._espera_s
        while True:
            try:
                if self._repositorio.casar_decisao_real({PERGUNTA_TRIAGEM: real}, ref=ref):
                    return
            except Exception:  # noqa: BLE001
                log.warning("decisao_fechada: não foi possível casar a triagem do curador")
                return
            if time.monotonic() >= limite:
                return                                  # recusa por privacidade ou sombra perdida: nada a casar
            time.sleep(0.05)


class CuradorComTriagemEmSombra:
    """Decora o curador principal (porta `CuradorDeIA` do aprendizado): devolve o parecer DELE, intacto, e só depois
    entrega o item à triagem em sombra. Falha do curador principal sobe como antes, sem sombra."""

    def __init__(self, interno: CuradorInterno, triagem: TriagemDoCurador) -> None:
        self._interno = interno
        self._triagem = triagem

    @property
    def provedor(self) -> str:
        return self._interno.provedor

    @property
    def simulado(self) -> bool:
        return self._interno.simulado

    def revisar(self, pedido: PedidoDoCurador) -> RespostaDoCurador:
        resposta = self._interno.revisar(pedido)
        bruto = resposta.bruto
        self._triagem.observar(pedido.dossie, pedido.dossie_hash,
                               bruto.get("decisao") if isinstance(bruto, Mapping) else None)
        return resposta


__all__ = ["CAMPOS", "KINDS_F1", "OPCOES", "PERGUNTA_TRIAGEM", "TRIAGEM_DO_PARECER", "CuradorComTriagemEmSombra",
           "TriagemDoCurador", "estado_do_dossie"]
