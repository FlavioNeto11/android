"""Caso de uso do e-mail do parque: gerar/validar o endereço da persona e ler o código de confirmação.

Nunca registra corpo de e-mail nem credencial: o código é o único dado que sai daqui, e quem o pede é quem o usa."""
from __future__ import annotations

import re
from collections.abc import Callable, Collection
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

from ..domain import endereco as dominio_endereco
from .ports import CabecalhoDeMensagem, LeitorCaixa, LeitorDeCabecalhos, Mensagem

_CODIGO = re.compile(r"\b(\d{6})\b")
#: Um código de confirmação pode vir no assunto ("123456 é seu código"): a lista de cabeçalhos nunca o mostra.
_SEIS_DIGITOS = re.compile(r"\d{6}")


class ErroEmailDoParque(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


@dataclass(frozen=True)
class ConfigEmail:
    dominio_padrao: str
    allowlist: tuple[str, ...]
    imap_configurado: bool
    # Remetente esperado do código de confirmação (trecho do endereço de quem envia). Vem de fora porque o núcleo não
    # sabe o nome do app: quem monta a config o deriva do pacote âncora (ADR-052).
    remetente_codigo: str = ""


@dataclass(frozen=True)
class CodigoRecente:
    codigo: str
    recebido_em: datetime
    remetente: str


def _utc(quando: datetime) -> datetime:
    return quando.replace(tzinfo=timezone.utc) if quando.tzinfo is None else quando.astimezone(timezone.utc)


class EmailDoParque:
    def __init__(self, config: ConfigEmail, leitor: LeitorCaixa | None,
                 *, agora: Callable[[], datetime] | None = None) -> None:
        self.config = config
        self.leitor = leitor
        self._agora = agora or (lambda: datetime.now(timezone.utc))

    @property
    def dominios_permitidos(self) -> tuple[str, ...]:
        """A allowlist; vazia vale só o domínio padrão, e sem ele nenhum domínio é permitido."""
        lista = tuple(d for d in (x.strip().lower() for x in self.config.allowlist) if d)
        if lista:
            return lista
        padrao = self.config.dominio_padrao.strip().lower()
        return (padrao,) if padrao else ()

    def validar_dominio(self, dominio: str) -> str:
        normal = (dominio or "").strip().lower()
        if not normal or normal not in self.dominios_permitidos:
            raise ErroEmailDoParque("dominio_nao_permitido", "Domínio de e-mail fora da lista permitida.", 422)
        return normal

    def gerar_endereco(self, *, persona_id: str, primeiro_nome: str, sobrenome: str, dominio: str,
                       existentes: Collection[str]) -> str:
        return dominio_endereco.gerar_endereco(
            persona_id=persona_id, primeiro_nome=primeiro_nome, sobrenome=sobrenome,
            dominio=self.validar_dominio(dominio), existentes=existentes)

    def confere_dominio(self, email: str, dominio: str) -> None:
        """O domínio declarado tem de ser o do e-mail: o igfarm não registra um e-mail de um domínio e diz outro."""
        try:
            do_email = dominio_endereco.dominio_do_endereco(email)
        except ValueError:
            raise ErroEmailDoParque("email_invalido", "Endereço de e-mail inválido.", 422) from None
        if do_email != (dominio or "").strip().lower():
            raise ErroEmailDoParque("dominio_divergente", "O domínio informado não é o do e-mail.", 422)

    async def codigo_recente(self, endereco: str, remetente: str | None = None, *,
                             janela_min: int = 30) -> CodigoRecente | None:
        """Código de 6 dígitos da mensagem mais recente (com código) para `endereco`; `None` se não há.

        A mais recente vence: um código antigo da mesma caixa não pode ser devolvido no lugar do novo."""
        if self.leitor is None or not self.config.imap_configurado:
            raise ErroEmailDoParque("email_indisponivel", "Leitura de e-mail não configurada.", 503)
        quem = (remetente or self.config.remetente_codigo).strip().lower()
        if not quem:                    # sem remetente a caixa compartilhada devolveria código de qualquer um
            raise ErroEmailDoParque("remetente_nao_configurado", "Remetente do código não configurado.", 503)
        alvo = endereco.strip().lower()
        desde = _utc(self._agora()) - timedelta(minutes=janela_min)
        achadas = await self.leitor.buscar(destinatario=alvo, remetente=quem, desde=desde)
        # O leitor já filtra, mas a porta é só um contrato: reconferir destino, remetente e janela aqui é barato
        # e impede que um leitor frouxo devolva o código de outra persona (caixa compartilhada).
        candidatas = [m for m in achadas
                      if alvo in {d.strip().lower() for d in m.destinatarios}
                      and quem in m.remetente.lower() and _utc(m.recebida_em) >= desde]
        for msg in sorted(candidatas, key=lambda m: _utc(m.recebida_em), reverse=True):
            codigo = _extrair_codigo(msg)
            if codigo:
                return CodigoRecente(codigo=codigo, recebido_em=_utc(msg.recebida_em), remetente=msg.remetente)
        return None


    async def cabecalhos(self, endereco: str, *, horas: int = 48, limite: int = 20) -> list[CabecalhoDeMensagem]:
        """31.336: os cabeçalhos (sem corpo) das mensagens para `endereco` nas últimas `horas`, da mais recente para a mais
        antiga. Só leitura. Serve para ver se o app mandou e-mail à caixa da conta (confirmação), se alguém devolveu e como o
        servidor de entrada autenticou. Seis dígitos seguidos no assunto viram `######` (podem ser o código)."""
        leitor = self.leitor
        if leitor is None or not self.config.imap_configurado or not isinstance(leitor, LeitorDeCabecalhos):
            raise ErroEmailDoParque("email_indisponivel", "Leitura de e-mail não configurada.", 503)
        alvo = endereco.strip().lower()
        desde = _utc(self._agora()) - timedelta(hours=max(1, min(int(horas), 24 * 14)))
        achadas = await leitor.listar_cabecalhos(destinatario=alvo, desde=desde, limite=max(1, min(int(limite), 50)))
        limpas = [replace(c, recebida_em=_utc(c.recebida_em), assunto=_SEIS_DIGITOS.sub("######", c.assunto or ""))
                  for c in achadas if _utc(c.recebida_em) >= desde]
        return sorted(limpas, key=lambda c: c.recebida_em, reverse=True)


def _extrair_codigo(msg: Mensagem) -> str | None:
    for texto in (msg.assunto, msg.corpo):
        achado = _CODIGO.search(texto or "")
        if achado:
            return achado.group(1)
    return None
