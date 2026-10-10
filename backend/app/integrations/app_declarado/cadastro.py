"""O motor do CADASTRO GUIADO (31.310, ADR-087, adendo v1.137): preenche o formulário que o app declara, lê o código do e-mail
da própria conta e comprova a conta, sem IA e sem conhecer app nenhum.

O que decide cada passo é a TELA, não uma memória: a cada volta o motor observa, reconhece a tela pelo `cadastro.yaml` do app
(`cadastro_conhecimento.py`) e age. Por isso reiniciar no meio retoma sozinho, e por isso o formulário NUNCA é enviado duas
vezes: o envio só acontece com a conta em `aguardando_cadastro_externo` e uma única vez por execução.

Onde ele PARA e chama uma pessoa, sem tentar de novo, sem solver e sem tocar na tela (`Parada`): CAPTCHA, "confirme que você é
humano" (a detecção genérica da trava, ADR-055 — a conta não existe ainda, então nada aqui a bloqueia: só devolve), telefone, @
indisponível, app fora do ar, tela que o app não declarou, código que não chegou ou foi recusado, conta que não se leu igual ao
desejado. Cada parada é um código fechado.

O motor não toca no aparelho nem no banco: fala com a `Mesa` (observar, tocar, digitar, digitar pelo canal sensível) e com o
`Ciclo` (as transições da conta). As implementações de verdade estão em `identity/infrastructure/cadastro_guiado.py`; os testes
usam as falsas.
"""
from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from ...automation.hierarchy import SUBTIPO_CONTA_TRAVADA, UiElement, UiTree, normalizar_texto_de_tela
from ...modules.identity.domain.cadastro import Parada, Passo
from ...modules.identity.domain.provisionamento import Estado
from .cadastro_conhecimento import Alvo, Campo, ConhecimentoDeCadastro, TelaDeCadastro

log = logging.getLogger("farm.identidade.cadastro")

#: Quantas vezes o motor tenta fazer o texto ficar no campo antes de desistir (o teclado sobe e empurra a tela).
TENTATIVAS_DO_CAMPO = 3
#: Os estados em que o motor pode agir (de `falha` o operador retoma primeiro).
ESTADOS_QUE_COMECAM = (Estado.CREDENCIAL_PREPARADA, Estado.AGUARDANDO_CADASTRO_EXTERNO, Estado.AGUARDANDO_VERIFICACAO)
#: A mesma tela de toque quantas vezes seguidas antes de desconfiar de que o toque não faz nada.
REPETICAO_MAXIMA = 3


class FalhaNaMesa(Exception):
    """O aparelho não respondeu como esperado (driver, canal sensível). A mensagem nunca leva valor de segredo."""


@dataclass(frozen=True, slots=True)
class Desfecho:
    confirmada: bool
    parada: Parada | None = None
    passo: Passo = Passo.INICIO


class Mesa(Protocol):
    async def observar(self) -> tuple[UiTree, str | None]: ...
    async def tocar(self, x: int, y: int) -> None: ...
    async def digitar(self, texto: str) -> None:
        """Digita texto COMUM no campo em foco, apagando o que houver antes."""
        ...

    async def digitar_sensivel(self, localizar: Callable[[UiTree], UiElement | None], segredo: Callable[[], str]) -> None:
        """Pelo canal sensível: foca o campo que `localizar` acha, esvazia, digita o segredo e confere que algo ficou."""
        ...

    async def esperar(self, segundos: float) -> None: ...


class Ciclo(Protocol):
    """As transições da conta (comparar e trocar). Cada método leva o `passo` do cadastro, para o evento."""

    def estado(self) -> Estado: ...
    def desde_do_envio(self) -> datetime | None:
        """Quando a conta entrou no estado de agora (o piso do `desde` do código: nenhum e-mail mais velho serve)."""
        ...

    def iniciar(self) -> None: ...                          # credencial_preparada → aguardando_cadastro_externo
    def enviado(self, passo: Passo) -> None: ...            # aguardando_cadastro_externo → aguardando_verificacao
    def parar(self, parada: Parada, passo: Passo) -> None: ...   # falhar, com o motivo fechado
    def confirmar(self, handle_observado: str) -> bool: ...  # grava a sessão observada e confirma; False = não confere


@dataclass(slots=True)
class Dados:
    """O que o cadastro preenche. Só o NÃO secreto está aqui; a senha é um callable que lê o cofre no último instante."""

    usuario: str
    nome: str
    primeiro_nome: str
    sobrenome: str
    email: str | None
    senha: Callable[[], str]
    #: `(desde, espera_s) → código` da caixa da conta; `None` se a conta não tem como ler.
    codigo: Callable[[datetime, float], Awaitable[str | None]] | None = None

    def valor(self, dado: str) -> str | None:
        texto = {"usuario": self.usuario, "nome": self.nome, "primeiro_nome": self.primeiro_nome,
                 "sobrenome": self.sobrenome, "email": self.email}.get(dado)
        return texto if texto and texto.strip() else None


def _arrobas(texto: str) -> str:
    return texto.strip().lstrip("@").lower()


def _agora() -> datetime:
    return datetime.now(UTC)


class MotorDeCadastro:
    def __init__(self, k: ConhecimentoDeCadastro, mesa: Mesa, ciclo: Ciclo, dados: Dados, *,
                 relogio: Callable[[], datetime] = _agora) -> None:
        self.k, self.mesa, self.ciclo, self.dados, self._relogio = k, mesa, ciclo, dados, relogio
        self._envios = 0                    # toques em "enviar" nesta execução (conta mesmo o que pode não ter chegado)
        self._passo = Passo.INICIO

    # ------------------------------------------------------------------ a execução
    async def executar(self) -> Desfecho:
        """Roda o cadastro até a confirmação ou até uma parada. Nunca levanta: erro inesperado (cofre, caixa de e-mail, banco)
        vira a parada `falha_interna`, SEM a mensagem do erro (ela pode trazer referência de segredo ou endereço)."""
        try:
            return await self._executar()
        except Exception as exc:  # noqa: BLE001 - qualquer erro nosso é uma parada, nunca um traceback com valores
            log.warning("cadastro guiado de %s: erro inesperado (%s)", self.k.rotulo, type(exc).__name__)
            return self._parar(Parada.FALHA_INTERNA, self._passo)

    async def _executar(self) -> Desfecho:
        k = self.k
        inicial = self.ciclo.estado()
        if inicial not in ESTADOS_QUE_COMECAM:
            # Conta planejada, em `falha` ou já confirmada: não é hora de tocar em nada (a rota também recusa, mas a rede é aqui).
            return Desfecho(False, Parada.TELA_DESCONHECIDA, Passo.INICIO)
        if inicial is Estado.CREDENCIAL_PREPARADA:
            self.ciclo.iniciar()
        enviado_em: datetime | None = None
        codigo_digitado = False
        repeticoes: Counter[str] = Counter()
        ultima: str | None = None
        passo = self._passo = Passo.INICIO
        for _ in range(k.passos_max):
            self._passo = passo
            try:
                tree, pacote = await self.mesa.observar()
            except FalhaNaMesa:
                return self._parar(Parada.APP_FORA_DO_AR, passo)
            if pacote != k.app:
                return self._parar(Parada.APP_FORA_DO_AR, passo)
            trava = tree.conta_travada
            if trava is not None and trava.subtipo == SUBTIPO_CONTA_TRAVADA:
                # "Confirme que você é humano" e parentes (ADR-055). Nada toca nela; a conta nem existe ainda. O pedido de
                # CÓDIGO (subtipo `codigo`) não é trava: é a tela que o app declara em `acao: codigo`, ou, sem declarar, cai
                # em tela desconhecida logo abaixo.
                return self._parar(Parada.DESAFIO, passo)
            tela = k.reconhecer(tree)
            if tela is None:
                return self._parar(Parada.TELA_DESCONHECIDA, passo)
            repeticoes[tela.nome] = repeticoes[tela.nome] + 1 if tela.nome == ultima else 1
            ultima = tela.nome
            if repeticoes[tela.nome] > REPETICAO_MAXIMA:
                return self._parar(Parada.TELA_DESCONHECIDA, passo)
            if tela.acao == "parar":
                assert tela.motivo is not None
                return self._parar(tela.motivo, passo)
            if tela.acao != "preencher" and self.ciclo.estado() is Estado.AGUARDANDO_CADASTRO_EXTERNO and self._envios:
                # O formulário saiu da tela sem erro declarado: o envio aconteceu.
                self.ciclo.enviado(Passo.ENVIO)
            elif (tela.acao in ("codigo", "sucesso") and self.ciclo.estado() is Estado.AGUARDANDO_CADASTRO_EXTERNO):
                self.ciclo.enviado(Passo.ENVIO)         # retomada: o envio foi antes do reinício

            if tela.acao == "tocar":
                passo = Passo.INICIO
                if not await self._tocar(tela.botao, tree):
                    return self._parar(Parada.TELA_DESCONHECIDA, passo)
            elif tela.acao == "preencher":
                passo = Passo.FORMULARIO
                if self.ciclo.estado() is not Estado.AGUARDANDO_CADASTRO_EXTERNO or (tela.envia and self._envios):
                    # O formulário continua na tela depois do envio, sem tela de erro declarada: não reenvia.
                    return self._parar(Parada.TELA_DESCONHECIDA, passo)
                motivo = await self._preencher(tela)
                if motivo is not None:
                    return self._parar(motivo, passo)
                momento = self._relogio()

                def marcar_o_envio() -> None:
                    nonlocal enviado_em
                    enviado_em = momento
                    self._envios += 1

                if tela.envia:
                    passo = self._passo = Passo.ENVIO
                motivo = await self._tocar_relido(tela.botao, marcar_o_envio if tela.envia else None)
                if motivo is not None:
                    return self._parar(motivo, passo)
                if tela.envia:
                    await self._esperar_sair_da(tela)
            elif tela.acao == "codigo":
                passo = Passo.CODIGO
                if codigo_digitado:
                    return self._parar(Parada.CODIGO_NAO_CHEGOU, passo)      # a tela do código voltou: foi recusado
                motivo = await self._digitar_o_codigo(tela, enviado_em)
                if motivo is not None:
                    return self._parar(motivo, passo)
                codigo_digitado = True
            elif tela.acao == "sucesso":
                passo = Passo.CONFIRMACAO
                handle = self._conta_lida(tela, tree)
                if handle is None or _arrobas(handle) != _arrobas(self.dados.usuario):
                    return self._parar(Parada.CONTA_NAO_LIDA, passo)
                if not self.ciclo.confirmar(handle.strip().lstrip("@")):
                    return self._parar(Parada.CONTA_NAO_LIDA, passo)
                return Desfecho(True, None, passo)
            await self.mesa.esperar(k.espera_s)
        return self._parar(Parada.TELA_DESCONHECIDA, passo)

    # ------------------------------------------------------------------ passos
    def _parar(self, parada: Parada, passo: Passo) -> Desfecho:
        log.info("cadastro guiado de %s parou: %s (%s)", self.k.rotulo, parada.value, passo.value)
        if (self._envios and parada is not Parada.USUARIO_INDISPONIVEL
                and self.ciclo.estado() is Estado.AGUARDANDO_CADASTRO_EXTERNO):
            # O formulário JÁ foi enviado nesta execução e o provedor não o recusou (a recusa declarada é só o @ indisponível).
            # Registrar o envio ANTES de parar é o que faz o "nunca duas vezes" valer depois de CAPTCHA, desafio, telefone, tela
            # desconhecida ou queda: retomar não volta a `aguardando_cadastro_externo`, então o formulário não é reenviado.
            try:
                self.ciclo.enviado(Passo.ENVIO)
            except Exception as exc:  # noqa: BLE001 - a parada acontece de qualquer jeito
                log.warning("cadastro guiado: o envio não pôde ser registrado (%s)", type(exc).__name__)
        try:
            self.ciclo.parar(parada, passo)
        except Exception as exc:  # noqa: BLE001
            log.warning("cadastro guiado: a parada %s não pôde ser gravada (%s)", parada.value, type(exc).__name__)
        return Desfecho(False, parada, passo)

    async def _tocar(self, alvo: Alvo | None, tree: UiTree) -> bool:
        botao = alvo.unico(tree, clicavel=True) if alvo is not None else None
        if botao is None:
            return False
        try:
            await self.mesa.tocar(*botao.center)
        except FalhaNaMesa:
            return False
        return True

    async def _tocar_relido(self, alvo: Alvo | None, antes_do_toque: Callable[[], None] | None = None) -> Parada | None:
        """O botão é relido na tela de AGORA: o teclado subiu com o último campo e empurrou tudo. Um desafio que tenha surgido
        entre o preenchimento e o toque é visto aqui, e então nada é tocado. `antes_do_toque` roda logo antes do toque (é onde o
        envio do formulário é contado: mesmo um toque que falhe pode ter chegado)."""
        try:
            tree, _ = await self.mesa.observar()
        except FalhaNaMesa:
            return Parada.APP_FORA_DO_AR
        trava = tree.conta_travada
        if trava is not None and trava.subtipo == SUBTIPO_CONTA_TRAVADA:
            return Parada.DESAFIO
        if (alvo.unico(tree, clicavel=True) if alvo is not None else None) is None:
            return Parada.TELA_DESCONHECIDA
        if antes_do_toque is not None:
            antes_do_toque()
        return None if await self._tocar(alvo, tree) else Parada.TELA_DESCONHECIDA

    async def _preencher(self, tela: TelaDeCadastro) -> Parada | None:
        for campo in tela.campos:
            if campo.segredo:
                parada = await self._senha(campo)
            else:
                valor = self.dados.valor(campo.dado)
                if valor is None:
                    return Parada.TELA_DESCONHECIDA                    # o serviço confere antes; aqui é a rede de segurança
                parada = await self._campo(campo, valor)
            if parada is not None:
                return parada
        return None

    async def _campo(self, campo: Campo, valor: str) -> Parada | None:
        esperado = _arrobas(valor) if campo.dado == "usuario" else normalizar_texto_de_tela(valor)
        for _ in range(TENTATIVAS_DO_CAMPO):
            try:
                tree, pacote = await self.mesa.observar()
                alvo = campo.alvo.unico(tree, editavel=True) if pacote == self.k.app else None
                if alvo is None:
                    return Parada.TELA_DESCONHECIDA
                await self.mesa.tocar(*alvo.center)
                await self.mesa.digitar(valor)
                await self.mesa.esperar(0.3)
                tree, _ = await self.mesa.observar()
            except FalhaNaMesa:
                return Parada.TELA_DESCONHECIDA
            atual = campo.alvo.unico(tree, editavel=True)
            if atual is None:
                return Parada.TELA_DESCONHECIDA
            lido = _arrobas(atual.text or "") if campo.dado == "usuario" else normalizar_texto_de_tela(atual.text)
            if lido == esperado:
                return None
        return Parada.TELA_DESCONHECIDA                                # nada foi enviado: o campo não ficou com o valor

    async def _senha(self, campo: Campo) -> Parada | None:
        def localizar(tree: UiTree) -> UiElement | None:
            e = campo.alvo.unico(tree, editavel=True)
            return e if e is not None and e.password else None         # senha só vai a campo que o Android diz ser de senha

        try:
            await self.mesa.digitar_sensivel(localizar, self.dados.senha)
        except FalhaNaMesa:
            return Parada.TELA_DESCONHECIDA
        return None

    async def _esperar_sair_da(self, tela: TelaDeCadastro) -> None:
        """Depois do envio: observa até a tela deixar de ser o formulário (ou o prazo acabar). Só observa; nunca reenvia."""
        passo_s = max(self.k.espera_s, 0.5)
        gasto = 0.0
        while gasto < self.k.envio_espera_s:
            await self.mesa.esperar(passo_s)
            gasto += passo_s
            try:
                tree, _ = await self.mesa.observar()
            except FalhaNaMesa:
                return
            atual = self.k.reconhecer(tree)
            if atual is None or atual.nome != tela.nome:
                return

    async def _digitar_o_codigo(self, tela: TelaDeCadastro, enviado_em: datetime | None) -> Parada | None:
        if self.dados.codigo is None or tela.campo is None:
            return Parada.CODIGO_NAO_CHEGOU
        desde = enviado_em or self.ciclo.desde_do_envio()
        if desde is None:
            return Parada.CODIGO_NAO_CHEGOU
        codigo = await self.dados.codigo(desde, self.k.codigo_espera_s)
        if not codigo:
            return Parada.CODIGO_NAO_CHEGOU
        campo = tela.campo

        def localizar(tree: UiTree) -> UiElement | None:
            return campo.unico(tree, editavel=True)

        try:
            await self.mesa.digitar_sensivel(localizar, lambda: codigo)
        except FalhaNaMesa:
            return Parada.TELA_DESCONHECIDA
        finally:
            del codigo
        return await self._tocar_relido(tela.botao)

    def _conta_lida(self, tela: TelaDeCadastro, tree: UiTree) -> str | None:
        e = tela.conta.unico(tree) if tela.conta is not None else None
        texto = (e.text or e.desc) if e is not None else ""
        return texto.strip() or None
