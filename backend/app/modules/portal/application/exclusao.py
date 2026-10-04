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
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Protocol

from app.modules.portal.domain.exclusao import (DIGITOS_MAX, DIGITOS_MIN, IDS_MAX, PEDIDO_POR, chave_do_telefone, final,
                                                mesmo_telefone)

log = logging.getLogger("poc.portal")


class PedidoInvalido(ValueError):
    """O corpo não serve: `code` vai na resposta 422."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class MuitasBuscas(Exception):
    """O operador passou do teto de buscas da hora; `espera_s` vai no `Retry-After`."""

    def __init__(self, espera_s: int) -> None:
        super().__init__(f"espere {espera_s} s")
        self.espera_s = espera_s


#: Uma hora: a janela do teto de buscas por operador.
JANELA_DAS_BUSCAS_S = 3600.0


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
                executado_por: str, mensagens_apagadas: int, mensagens_a_mao: int, agora: datetime) -> list[int]: ...


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
                 canal_presente: Callable[[], bool], buscas_por_hora: Callable[[], int]) -> None:
        self.repo = repo
        self._apagar_no_canal = apagar_no_canal
        self._canal_presente = canal_presente
        self._buscas_por_hora = buscas_por_hora
        # operador → instantes (relógio monotônico) das buscas da última hora. Em memória no processo: reiniciar zera,
        # e isso basta, porque a busca exige o número inteiro e é de quem já está logado (decisão da orquestradora).
        self._buscas: dict[str, list[float]] = {}

    def _contar_busca(self, operador: str, agora_s: float) -> None:
        recentes = [t for t in self._buscas.get(operador, []) if agora_s - t < JANELA_DAS_BUSCAS_S]
        if len(recentes) >= self._buscas_por_hora():
            self._buscas[operador] = recentes
            raise MuitasBuscas(max(1, int(JANELA_DAS_BUSCAS_S - (agora_s - recentes[0])) + 1))
        recentes.append(agora_s)
        self._buscas[operador] = recentes
        for chave in [c for c, ts in self._buscas.items() if not ts or agora_s - ts[-1] >= JANELA_DAS_BUSCAS_S]:
            del self._buscas[chave]                        # quem não busca há uma hora sai: o dicionário não cresce

    def buscar(self, telefone: object, *, operador: str, agora_s: float) -> list[dict[str, object]]:
        """Os contatos com aquele telefone, só com id, data, estado e os 4 dígitos finais. Nome, empresa e mensagem
        nunca saem daqui: a lista serve para escolher o que apagar, não para ler. Conta no teto do operador só a busca
        válida (a inválida não acha nada); o log leva o operador e a contagem, nunca o telefone."""
        if not isinstance(telefone, str) or chave_do_telefone(telefone) is None:
            raise PedidoInvalido("telefone_invalido", "Informe o telefone como a pessoa escreveu, com DDD se ela usou "
                                                      f"({DIGITOS_MIN} a {DIGITOS_MAX} dígitos).")
        self._contar_busca(operador, agora_s)
        achados = [{"id": c.id, "criado_em": c.criado_em, "estado": c.estado, "final": final(c.telefone)}
                   for c in self.repo.com_telefone() if mesmo_telefone(telefone, c.telefone)]
        log.info("portal: busca de exclusão por %s: %s achado(s)", operador, len(achados))
        return achados

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
                 agora: datetime) -> ResultadoDaExclusao:
        """O DELETE e o registro, juntos. A resposta diz só o que ESTE DELETE apagou: o id que outra exclusão (ou a
        faxina) levou no meio vai para `inexistentes`. Um pedido de ids que já não existem não deixa rastro."""
        de_fato: set[int] = set()
        if resultado.apagados or resultado.mantidos:
            de_fato = set(self.repo.excluir(
                ids=resultado.apagados, mantidos=resultado.mantidos, pedido_por=pedido.pedido_por,
                executado_por=executado_por, mensagens_apagadas=resultado.mensagens_apagadas,
                mensagens_a_mao=len(resultado.mensagens_a_mao), agora=agora))
        sumidos = [i for i in resultado.apagados if i not in de_fato]
        resultado = replace(resultado, apagados=[i for i in resultado.apagados if i in de_fato],
                            inexistentes=[*resultado.inexistentes, *sumidos])
        log.info("portal: exclusão a pedido (%s) por %s: %s apagado(s), %s mantido(s), %s inexistente(s), "
                 "%s mensagem(ns) apagada(s), %s à mão", pedido.pedido_por, executado_por, len(resultado.apagados),
                 len(resultado.mantidos), len(resultado.inexistentes), resultado.mensagens_apagadas,
                 len(resultado.mensagens_a_mao))
        return resultado
