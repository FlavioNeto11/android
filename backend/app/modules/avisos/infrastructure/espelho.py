"""O espelho do Trello (item 32.2, passo 3; `docs/design/trello-integracao.md`, §2 e §7.4): um RECONCILIADOR, sem nada
por evento. A cada volta compara o conjunto desejado de fatos (lido das portas da Central) com `trello_cartoes`:
fato novo cria o cartão, hash diferente o atualiza, fato que sumiu comenta o desfecho e o arquiva. Uma segunda volta sem
mudança não faz nenhuma chamada ao Trello. Marcos (um cartão por deploy) e custos (um por dia, de hora em hora) vêm
no mesmo laço.

O que vai ao Trello é só o que `application/espelho.py` monta (tipo, id curto, estado, link do painel; nunca texto de
origem). Ordem da CRIAÇÃO: banco primeiro. A linha `criando` (com o `card_id` sentinela) é gravada antes de chamar o
Trello; só depois do cartão criado vem o `card_id` verdadeiro e o `ativo`. Uma queda no meio deixa a linha em `criando`, e
a volta seguinte procura o cartão pela marca `🤖 chave: <família>:<fato>` (última linha da descrição) nas listas: achou,
adota (grava o `card_id` e o hash do que está lá); não achou, cria. Cartão sem marca (feito por pessoa) nunca é tocado.
Atualizar e arquivar são idempotentes: Trello primeiro, banco depois. A falha não definitiva só interrompe a volta; a
definitiva (401/403 e os outros 4xx) interrompe também e aparece na saúde (`trello_recusado`, `trello_pedido_invalido`),
como a recusa do Telegram. Roda só no líder da trava `avisos` e só com `trello.enabled` e os dois segredos.

Famílias espelhadas: `approval` e `run` (as pendências de `PortasReais.pendencias()`), `pedido` (ativo, pausado ou
aguardando_pessoa) e `livro` (validações do 082 em `pendente` ou `rodando`). Ficam de fora, por falta de porta limpa:
`session` (só há o evento, sem lista do que está aberto) e `learning` (a espera da pessoa é só evento; o Livro mostra no
painel).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

from app.config import Config
from app.models import Problem
from app.modules.avisos.adapters.trello import ClienteTrello, FalhaDoTrello
from app.modules.avisos.application.espelho import (
    FAMILIAS_ESPELHADAS,
    Fato,
    FontesDoEspelho,
    LinhaDeCusto,
    chave_da_marca,
    comentario_de_desfecho,
    fato_de_custo,
    fato_de_deploy,
    fato_de_pedido,
    fato_de_pendencia,
    fato_de_validacao,
    hash_do_conteudo,
)
from app.modules.avisos.domain.mensagem import chave_do_fato
from app.modules.avisos.infrastructure.entrada import Pendencia
from app.modules.avisos.infrastructure.espelho_sql import ARQUIVADO, ATIVO, CRIANDO, CartoesDoTrello
from app.taskqueue.travas import AVISOS
from app.util import parse_iso

log = logging.getLogger("poc.avisos.trello")

#: O custo do dia é atualizado no Trello no máximo uma vez por hora.
CUSTO_A_CADA = timedelta(hours=1)
_CAUSA = {401: "chave ou token inválido, ou o token foi revogado", 403: "o token não tem permissão neste quadro"}
#: Um cartão achado pela marca: (card_id, nome, descrição, lista).
Marcado = tuple[str, str, str, str]


def _utc() -> datetime:
    return datetime.now(timezone.utc)


class FontesDaCentral:
    """O conjunto desejado. Cada leitura que falha SOBE: um erro de leitura nunca vira "o fato sumiu" (que arquivaria
    o cartão); quem chama interrompe a volta."""

    def __init__(self, pendencias: Callable[[], list[Pendencia]], fontes: FontesDoEspelho,
                 url_painel: Callable[[], str | None]):
        self._pendencias = pendencias
        self._fontes = fontes
        self._url = url_painel

    def desejado(self) -> dict[str, Fato]:
        url = self._url()
        fatos = [fato_de_pendencia(p.tipo, p.ident, url) for p in self._pendencias()]
        fatos += [fato_de_pedido(ident, estado, url) for ident, estado in self._fontes.pedidos_abertos()]
        fatos += [fato_de_validacao(ident, item_ref, estado, url)
                  for ident, item_ref, estado in self._fontes.livro_em_validacao()]
        return {f.chave: f for f in fatos if f is not None}


class EspelhoDoTrello:
    def __init__(self, cfg: Config, repo: CartoesDoTrello, fontes: FontesDaCentral, *,
                 lider: Callable[[str], int | None], versao: Callable[[], tuple[str | None, str | None]],
                 custos: Callable[[], list[LinhaDeCusto]], relogio: Callable[[], datetime] = _utc,
                 cliente: ClienteTrello | None = None,
                 dormir: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self.cfg = cfg
        self.repo = repo
        self.fontes = fontes
        self._lider = lider
        self._versao = versao
        self._custos = custos
        self._relogio = relogio
        self._cliente = cliente
        self._dormir = dormir
        self._recusada: FalhaDoTrello | None = None
        self._custo_lido_em: datetime | None = None
        #: Desfecho já comentado nesta vida do processo (atalho): se o arquivamento falhar, a volta seguinte não comenta de
        #: novo. Depois de uma reinicialização quem decide é o último comentário do cartão (`_desfecho_ja_comentado`).
        self._comentados: set[str] = set()
        #: Os cartões abertos com a marca do espelho, por chave: lidos UMA vez por volta, só quando há o que criar.
        self._marcados: dict[str, list[Marcado]] | None = None

    # ------------------------------------------------------------------ configuração e saúde
    @property
    def ligado(self) -> bool:
        return bool(self.cfg.file.trello.enabled)

    def cliente(self) -> ClienteTrello | None:
        """O cliente pronto, ou `None` (desligado ou segredo faltando; a saúde já avisa `trello_sem_segredo`)."""
        if not self.ligado:
            return None
        if self._cliente is not None:
            return self._cliente
        env = self.cfg.env
        chave = env.trello_api_key.get_secret_value().strip() if env.trello_api_key else ""
        token = env.trello_token.get_secret_value().strip() if env.trello_token else ""
        return ClienteTrello(chave, token) if chave and token else None

    def problemas(self) -> list[Problem]:
        f = self._recusada
        if not self.ligado or f is None:
            return []
        if f.status in _CAUSA:
            return [Problem(
                code="trello_recusado",
                message=f"O espelho do Trello está parado: o Trello recusou a Central ({f.status}).",
                hint=f"Causa provável: {_CAUSA[f.status]}. Confira TRELLO_API_KEY e TRELLO_TOKEN no .env (docs/operacao.md) "
                     "ou desligue trello.enabled; a Central tenta de novo sozinha a cada volta.")]
        return [Problem(
            code="trello_pedido_invalido",
            message=f"O espelho do Trello está parado: o Trello não aceitou o pedido ({f.status}).",
            hint="Confira os ids de quadro e de lista em trello.quadros e trello.listas; um cartão apagado à mão "
                 "ou uma lista arquivada produz isto. A Central tenta de novo sozinha a cada volta.")]

    # ------------------------------------------------------------------ uma volta
    async def uma_volta(self) -> bool:
        """Reconcilia uma vez. `True` quando chegou ao fim; `False` quando pulou ou parou numa falha."""
        cliente = self.cliente()
        if cliente is None or self._lider(AVISOS) is None:
            return False
        self._marcados = None
        try:
            await self._fatos(cliente)
            await self._marco(cliente)
            await self._custo(cliente)
        except FalhaDoTrello as falha:
            if falha.definitiva:
                if self._recusada is None:
                    log.error("trello: o espelho foi recusado (%s)", falha.motivo)
                self._recusada = falha
            else:
                log.warning("trello: espelho (%s)", falha.motivo)
            return False
        except Exception:  # noqa: BLE001 - uma leitura que falha não pode virar "o fato sumiu"; nada é arquivado
            log.exception("trello: leitura dos fatos da Central")
            return False
        self._recusada = None
        return True

    def _quadro_padrao(self) -> str:
        quadros = self.cfg.file.trello.quadros
        return quadros[0] if quadros else ""

    # ------------------------------------------------------------------ criar, adotar, atualizar, arquivar
    async def _cartoes_marcados(self, cliente: ClienteTrello) -> dict[str, list[Marcado]]:
        """Os cartões ABERTOS das listas do espelho que levam a marca `🤖 chave: …`, por chave. Sem marca (feito por
        pessoa) não entra: nunca é tocado. Lê as listas uma vez por volta, na primeira vez que precisa."""
        if self._marcados is None:
            listas = self.cfg.file.trello.listas
            achados: dict[str, list[Marcado]] = {}
            for lista in dict.fromkeys(listas[p] for p in ("central_automatico", "marcos", "custos") if listas.get(p)):
                for c in await cliente.cartoes_da_lista(lista):
                    chave = chave_da_marca(str(c.get("desc") or ""))
                    if chave and isinstance(c.get("id"), str) and c["id"]:
                        achados.setdefault(chave, []).append(
                            (str(c["id"]), str(c.get("name") or ""), str(c.get("desc") or ""), lista))
            # O id do Trello começa pelo instante de criação: o menor é o mais antigo.
            self._marcados = {k: sorted(v) for k, v in achados.items()}
        return self._marcados

    async def _garantir(self, cliente: ClienteTrello, fato: Fato, lista: str) -> None:
        """Cria o cartão do fato, ou adota o que já existe. Banco primeiro (linha `criando`), depois o Trello, e só então
        o `card_id` verdadeiro. Marca repetida (raro: dois processos criando o mesmo fato): adota a MAIS ANTIGA e deixa a
        outra como está; arquivar um cartão que a Central não tem certeza de ser o duplicado seria mexer no que não é dela."""
        quadro = self._quadro_padrao()
        self.repo.intencao(fato.chave, quadro, lista)
        achados = (await self._cartoes_marcados(cliente)).get(fato.chave)
        if achados:
            card_id, nome, desc, onde = achados[0]
            # O hash é o do que está LÁ: se o conteúdo difere do desejado, a próxima comparação atualiza.
            self.repo.gravar(fato.chave, card_id, quadro, onde, hash_do_conteudo(nome, desc))
            return
        card = await cliente.criar_cartao(lista, fato.nome, fato.descricao)
        if not isinstance(card, dict) or not isinstance(card.get("id"), str) or not card["id"]:
            raise FalhaDoTrello("o Trello não devolveu o id do cartão criado")
        id_quadro = card.get("idBoard")
        self.repo.gravar(fato.chave, card["id"], id_quadro if isinstance(id_quadro, str) and id_quadro else quadro,
                         lista, fato.hash)

    async def _atualizar(self, cliente: ClienteTrello, fato: Fato, card_id: str) -> None:
        try:
            await cliente.atualizar_cartao(card_id, nome=fato.nome, desc=fato.descricao)
        except FalhaDoTrello as falha:
            if falha.status != 404:
                raise
            self.repo.arquivar(fato.chave)      # o cartão foi apagado à mão: a volta seguinte cria outro
            return
        self.repo.novo_hash(fato.chave, fato.hash)

    async def _desfecho_ja_comentado(self, cliente: ClienteTrello, chave: str, card_id: str) -> bool:
        """O último comentário já é o desfecho da Central ("🤖 … resolvido")? Vale depois de uma reinicialização, em que
        `_comentados` se perdeu: só arquiva, sem comentar de novo."""
        if chave in self._comentados:
            return True
        recentes = await cliente.comentarios(card_id, 5)
        return bool(recentes) and recentes[0].startswith("🤖") and "resolvido" in recentes[0]

    async def _arquivar(self, cliente: ClienteTrello, chave: str, card_id: str, agora: datetime) -> None:
        familia = chave.split(":", 1)[0]
        try:
            if not await self._desfecho_ja_comentado(cliente, chave, card_id):
                await cliente.comentar(card_id, comentario_de_desfecho(familia, agora))
            self._comentados.add(chave)
            await cliente.arquivar_cartao(card_id)
        except FalhaDoTrello as falha:
            if falha.status != 404:
                raise
        self.repo.arquivar(chave)
        self._comentados.discard(chave)

    async def _fatos(self, cliente: ClienteTrello) -> None:
        lista = self.cfg.file.trello.listas.get("central_automatico")
        if not lista:
            return
        desejado = self.fontes.desejado()
        linhas = self.repo.todos()
        agora = self._relogio()
        for chave, fato in desejado.items():
            linha = linhas.get(chave)
            if linha is None or linha["estado"] in (ARQUIVADO, CRIANDO):
                await self._garantir(cliente, fato, lista)
            elif linha["hash"] != fato.hash:
                await self._atualizar(cliente, fato, str(linha["card_id"]))
        for chave, linha in linhas.items():
            if chave in desejado or chave.split(":", 1)[0] not in FAMILIAS_ESPELHADAS:
                continue
            if linha["estado"] == ATIVO:
                await self._arquivar(cliente, chave, str(linha["card_id"]), agora)
            elif linha["estado"] == CRIANDO:
                # O fato sumiu antes de a criação fechar: se o cartão chegou a nascer, sai com o desfecho; senão, só a linha.
                nascido = (await self._cartoes_marcados(cliente)).get(chave)
                if nascido:
                    await self._arquivar(cliente, chave, nascido[0][0], agora)
                else:
                    self.repo.arquivar(chave)

    # ------------------------------------------------------------------ marcos e custos
    async def _marco(self, cliente: ClienteTrello) -> None:
        """Um cartão por deploy: quando (commit, migração) da saúde não tem cartão ainda. Nunca é arquivado."""
        lista = self.cfg.file.trello.listas.get("marcos")
        commit, migracao = self._versao()
        if not lista or not commit:
            return
        fato = fato_de_deploy(commit, migracao)
        linha = self.repo.um(fato.chave)
        if linha is None or linha["estado"] == CRIANDO:
            await self._garantir(cliente, fato, lista)

    async def _custo(self, cliente: ClienteTrello) -> None:
        """Um cartão por dia, atualizado no máximo de hora em hora (a marca é a `atualizado_em` da linha, que sobrevive
        a uma reinicialização)."""
        lista = self.cfg.file.trello.listas.get("custos")
        if not lista:
            return
        agora = self._relogio()
        if self._custo_lido_em is not None and agora - self._custo_lido_em < CUSTO_A_CADA:
            return
        chave = chave_do_fato("custo", f"{agora.astimezone(timezone.utc):%Y-%m-%d}")
        linha = self.repo.um(chave)
        if linha is not None and linha["estado"] == ATIVO:
            feito = parse_iso(str(linha["atualizado_em"]))
            if feito is not None and agora - feito < CUSTO_A_CADA:
                return
        fato = fato_de_custo(chave.split(":", 1)[1], self._custos())
        if linha is None or linha["estado"] != ATIVO:
            await self._garantir(cliente, fato, lista)
        elif linha["hash"] != fato.hash:
            await self._atualizar(cliente, fato, str(linha["card_id"]))
        self._custo_lido_em = agora      # só depois de dar certo: uma falha tenta de novo na volta seguinte

    # ------------------------------------------------------------------ laço
    async def laco(self) -> None:
        while True:
            try:
                if self.ligado:
                    await self.uma_volta()
                await self._dormir(float(self.cfg.file.trello.espelho_s))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - o espelho nunca derruba o processo
                log.exception("trello: laço do espelho")
                await self._dormir(5)
