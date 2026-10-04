"""Exclusão de contatos do site a pedido do titular (29.83, ADR-075).

Quem aperta "Apagar definitivamente" é uma pessoa, na sessão dela (a rota confere); este serviço só decide a ordem:

1. Para cada contato escolhido, a Canais primeiro (`apagar_avisos_do_portal`, 28.34): ela tira o aviso da fila PARA
   SEMPRE (lápide na chave `portal:<id>`, que faz qualquer reenfileirar virar no-op), apaga o texto das respostas do
   dono e tenta apagar a mensagem do bot no chat. Só com `ok` dela a linha do contato sai: com `em_envio` ou `falhou`
   a linha fica, para não sobrar mensagem saindo depois da exclusão.
2. O `DELETE` das linhas e o registro em `portal_exclusoes`, numa transação.

Sem a função da Canais na base: se o aviso do 28.32 existe, nada é apagado (`canal_sem_exclusao`), porque um aviso na
fila sairia depois; sem aviso nenhum não há fila, e a exclusão segue (`sem_canal`).

O registro não guarda dado do titular (só ids, contagens, o operador e o canal do pedido), e o log também não.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from app.modules.portal.domain.exclusao import IDS_MAX, PEDIDO_POR, final, mesmo_telefone, nacional

log = logging.getLogger("poc.portal")


class PedidoInvalido(ValueError):
    """O corpo não serve: `code` vai na resposta 422."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class Achado(Protocol):
    id: int
    criado_em: str
    estado: str
    telefone: str


class Apagado(Protocol):
    """A resposta da Canais (`ApagadoNoCanal`, contrato do 28.34); lida por atributo, sem importar a classe."""
    estado: str
    apagadas: int
    a_mao: tuple[str, ...]


ApagarNoCanal = Callable[[int, datetime], Awaitable[Apagado]]


class RepositorioDeExclusao(Protocol):
    def com_telefone(self) -> Sequence[Achado]: ...
    def estados(self, ids: Sequence[int]) -> Mapping[int, str]: ...
    def excluir(self, *, ids: Sequence[int], mantidos: Sequence[tuple[int, str]], pedido_por: str,
                executado_por: str, mensagens_apagadas: int, mensagens_a_mao: int, agora: datetime) -> int: ...


@dataclass(frozen=True, slots=True)
class ResultadoDaExclusao:
    apagados: list[int] = field(default_factory=list)
    mantidos: list[tuple[int, str]] = field(default_factory=list)
    inexistentes: list[int] = field(default_factory=list)
    mensagens_apagadas: int = 0
    mensagens_a_mao: list[tuple[int, str]] = field(default_factory=list)
    sem_canal: bool = False

    def corpo(self) -> dict[str, object]:
        return {
            "apagados": self.apagados,
            "mantidos": [{"id": i, "motivo": m} for i, m in self.mantidos],
            "inexistentes": self.inexistentes,
            "mensagens_apagadas": self.mensagens_apagadas,
            "mensagens_a_mao": [{"contato_id": i, "enviada_em": e} for i, e in self.mensagens_a_mao],
            "sem_canal": self.sem_canal,
        }


@dataclass(frozen=True, slots=True)
class PedidoDeExclusao:
    ids: list[int]
    pedido_por: str
    existentes: set[int]


def _ids(valor: object) -> list[int]:
    if not isinstance(valor, list) or not valor or len(valor) > IDS_MAX:
        raise PedidoInvalido("ids_invalidos", f"Escolha de 1 a {IDS_MAX} contatos.")
    ids: list[int] = []
    for item in valor:
        if isinstance(item, bool) or not isinstance(item, int) or item <= 0:
            raise PedidoInvalido("ids_invalidos", "Os contatos vêm pelo número de cada um.")
        if item not in ids:
            ids.append(item)
    return ids


class ServicoDeExclusao:
    def __init__(self, repo: RepositorioDeExclusao, *, apagar_no_canal: Callable[[], ApagarNoCanal | None],
                 canal_presente: Callable[[], bool]) -> None:
        self.repo = repo
        self._apagar_no_canal = apagar_no_canal
        self._canal_presente = canal_presente

    def buscar(self, telefone: object) -> list[dict[str, object]]:
        """Os contatos com aquele telefone, só com id, data, estado e os 4 dígitos finais. Nome, empresa e mensagem
        nunca saem daqui: a lista serve para escolher o que apagar, não para ler."""
        if not isinstance(telefone, str) or nacional(telefone) is None:
            raise PedidoInvalido("telefone_invalido", "Informe o telefone com DDD (10 a 13 dígitos).")
        return [{"id": c.id, "criado_em": c.criado_em, "estado": c.estado, "final": final(c.telefone)}
                for c in self.repo.com_telefone() if mesmo_telefone(telefone, c.telefone)]

    # A exclusão em três passos, para esta camada não carregar o laço de eventos: a rota roda `preparar` e `concluir`
    # (banco) numa thread e `decidir` (só a Canais, que é assíncrona) no laço.
    def preparar(self, dados: Mapping[str, object]) -> PedidoDeExclusao:
        ids = _ids(dados.get("ids"))
        pedido_por = dados.get("pedido_por")
        if not isinstance(pedido_por, str) or pedido_por not in PEDIDO_POR:
            raise PedidoInvalido("pedido_por_invalido", "Diga por onde o pedido chegou: formulário, telefone ou outro.")
        return PedidoDeExclusao(ids=ids, pedido_por=pedido_por, existentes=set(self.repo.estados(ids)))

    async def decidir(self, pedido: PedidoDeExclusao, agora: datetime) -> ResultadoDaExclusao:
        """A Canais primeiro, contato a contato; só o `ok` dela libera o DELETE daquele contato."""
        apagar = self._apagar_no_canal()
        sem_canal = apagar is None and not self._canal_presente()
        a_apagar: list[int] = []
        mantidos: list[tuple[int, str]] = []
        apagadas = 0
        a_mao: list[tuple[int, str]] = []
        for contato_id in (i for i in pedido.ids if i in pedido.existentes):
            if apagar is None:
                if sem_canal:
                    a_apagar.append(contato_id)
                else:
                    mantidos.append((contato_id, "canal_sem_exclusao"))
                continue
            try:
                resposta = await apagar(contato_id, agora)
            except Exception as erro:  # noqa: BLE001 - a falha da Canais mantém o contato; nunca derruba a rota
                log.warning("portal: exclusão do contato %s: a Canais falhou (%s)", contato_id, type(erro).__name__)
                mantidos.append((contato_id, "falhou"))
                continue
            estado = getattr(resposta, "estado", None)
            if estado == "ok":
                a_apagar.append(contato_id)
                apagadas += int(getattr(resposta, "apagadas", 0) or 0)
                a_mao.extend((contato_id, str(e)) for e in (getattr(resposta, "a_mao", ()) or ()))
            else:
                mantidos.append((contato_id, "em_envio" if estado == "em_envio" else "falhou"))
        return ResultadoDaExclusao(apagados=a_apagar, mantidos=mantidos,
                                   inexistentes=[i for i in pedido.ids if i not in pedido.existentes],
                                   mensagens_apagadas=apagadas, mensagens_a_mao=a_mao, sem_canal=sem_canal)

    def concluir(self, pedido: PedidoDeExclusao, resultado: ResultadoDaExclusao, *, executado_por: str,
                 agora: datetime) -> None:
        """O DELETE e o registro, juntos. Só com algum contato existente: um pedido de ids que já não existem não deixa
        rastro."""
        if resultado.apagados or resultado.mantidos:
            self.repo.excluir(ids=resultado.apagados, mantidos=resultado.mantidos, pedido_por=pedido.pedido_por,
                              executado_por=executado_por, mensagens_apagadas=resultado.mensagens_apagadas,
                              mensagens_a_mao=len(resultado.mensagens_a_mao), agora=agora)
        log.info("portal: exclusão a pedido (%s) por %s: %s apagado(s), %s mantido(s), %s inexistente(s), "
                 "%s mensagem(ns) apagada(s), %s à mão", pedido.pedido_por, executado_por, len(resultado.apagados),
                 len(resultado.mantidos), len(resultado.inexistentes), resultado.mensagens_apagadas,
                 len(resultado.mensagens_a_mao))
