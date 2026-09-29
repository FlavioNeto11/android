"""Consumidor restrito do cofre para a provisão de rede (ADR-056 §5, item 25.3): o segundo lugar que abre um segredo.

O primeiro é o canal de entrada sensível (`type_secret`, em `sensitive_input.py`), que digita a senha de uma conta
no instante do login pela função que o executor e a sessão declarada lhe entregam. Este é o do mesmo molde, para a rede: a chave privada do WireGuard, a senha do proxy ou a configuração do
sing-box saem do cofre aqui, e só aqui, no instante de chegar ao aparelho. A lista de quem chama `get_secret` é
conferida por teste (`tests/test_segredo_de_rede.py`): um terceiro consumidor é decisão, não descuido.

O que o torna RESTRITO:
- recebe o `profile_id` de um perfil de rede, nunca um `secret_ref`: a referência é lida da própria linha de
  `network_profiles`, então a senha de uma conta do Instagram não sai por aqui nem que alguém passe a referência dela;
- o valor não volta a quem chama. Ele é montado (`montar`) e entregue DENTRO do bloco; quem chama recebe o caminho
  do arquivo no convidado e mais nada — nem o tamanho, nem um hash;
- a entrega é por stdin (`adb exec-in`, o padrão) ou por arquivo temporário empurrado (`adb push`), nunca por
  argumento de processo; o arquivo temporário do host é apagado assim que o `push` volta, e o do convidado ao sair
  do bloco, com erro ou sem;
- a mensagem de erro é fixa. Exceção de `montar` ou do transporte pode trazer o texto que carregava o segredo; ela é
  descartada dentro de uma função, e quem levanta o erro fica fora de qualquer `except`, para o original não ir
  encadeado em `__context__` nem aparecer num traceback de log (o mesmo cuidado de `sensitive_input._ran`).

O log diz QUE houve entrega (perfil, aparelho, modo), como o canal sensível diz que houve digitação. Evento e
evidência não recebem nada daqui: o 25.4 registra a observação pelo `rede.registrar_observacao`, que não conhece o
valor.
"""
from __future__ import annotations

import contextlib
import logging
import os
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Literal, Protocol

from ..db import Database
from .secret_store import SecretStore

log = logging.getLogger("poc.security")

ModoDeEntrega = Literal["stdin", "push"]


class SegredoDeRedeError(RuntimeError):
    """Falha ao entregar o segredo de rede. A mensagem é fixa: nunca carrega texto do transporte nem do segredo."""


class TransporteDeArquivoPrivado(Protocol):
    """O que o consumidor precisa do aparelho — `devices.adb.Adb` cumpre. Protocolo, e não import, para o cofre
    não depender da camada de aparelhos (e o teste trocar o adb por um dublê sem subprocesso)."""

    serial: str

    def gravar_arquivo_privado(self, nome: str, conteudo: bytes) -> str: ...
    def enviar_arquivo_privado(self, local: str, nome: str, *, tamanho: int) -> str: ...
    def apagar_arquivo_privado(self, nome: str) -> None: ...


@dataclass(frozen=True)
class EntregaDeSegredo:
    """O recibo da entrega. Só identifica ONDE o segredo está; o que ele é fica no cofre e no arquivo do convidado."""

    profile_id: str
    serial: str
    caminho: str
    modo: ModoDeEntrega


def _referencia(db: Database, profile_id: str) -> str:
    """A `secret_ref` do perfil de rede — o único jeito de este consumidor chegar a uma referência."""
    row = db.one("SELECT secret_ref FROM network_profiles WHERE id=?", (profile_id,))
    if row is None:
        raise SegredoDeRedeError(f"Perfil de rede {profile_id} não encontrado.")
    if not row["secret_ref"]:
        raise SegredoDeRedeError(f"O perfil de rede {profile_id} não tem segredo no cofre.")
    return str(row["secret_ref"])


def _montado(montar: Callable[[str], str | bytes] | None, segredo: str) -> bytes | None:
    """Monta o conteúdo do arquivo; `None` quando `montar` falhou. A exceção morre AQUI dentro: a mensagem de um
    `KeyError` ou de um `format` pode citar o próprio segredo."""
    try:
        conteudo = montar(segredo) if montar is not None else segredo
        return conteudo.encode("utf-8") if isinstance(conteudo, str) else bytes(conteudo)
    except Exception:  # noqa: BLE001 - proposital: a mensagem original nunca pode escapar
        return None


def _gravado(transporte: TransporteDeArquivoPrivado, nome: str, conteudo: bytes, modo: ModoDeEntrega) -> str | None:
    """Entrega e devolve o caminho no convidado; `None` quando o transporte falhou (a exceção dele morre aqui)."""
    try:
        if modo == "stdin":
            return transporte.gravar_arquivo_privado(nome, conteudo)
        # `push`: arquivo temporário do host, criado 600 pelo `mkstemp`, apagado assim que o `push` volta — antes
        # de o bloco de quem chama começar, não depois.
        fd, local = tempfile.mkstemp(prefix="rede-")
        try:
            with os.fdopen(fd, "wb") as arq:
                arq.write(conteudo)
            return transporte.enviar_arquivo_privado(local, nome, tamanho=len(conteudo))
        finally:
            with contextlib.suppress(OSError):
                os.remove(local)
    except Exception:  # noqa: BLE001 - proposital: a mensagem original nunca pode escapar
        return None


def _apagado(transporte: TransporteDeArquivoPrivado, nome: str) -> bool:
    try:
        transporte.apagar_arquivo_privado(nome)
    except Exception:  # noqa: BLE001 - proposital: a mensagem original nunca pode escapar
        return False
    return True


@contextlib.contextmanager
def segredo_no_convidado(db: Database, secrets: SecretStore, profile_id: str,
                         transporte: TransporteDeArquivoPrivado, *, nome_do_arquivo: str,
                         montar: Callable[[str], str | bytes] | None = None,
                         modo: ModoDeEntrega = "stdin") -> Iterator[EntregaDeSegredo]:
    """Põe o segredo do perfil num arquivo 600 do convidado durante o bloco e o apaga ao sair.

    `montar(segredo)` devolve o conteúdo do arquivo — a configuração do cliente com a chave dentro, por exemplo — e
    roda aqui dentro, para a configuração completa também não sair do bloco. Sem ele, o arquivo é o segredo puro.
    Dentro do bloco, quem chama manda o cliente importar o arquivo (25.4); o recibo diz só onde ele está.

    Levanta `SegredoDeRedeError` (perfil sem segredo, montagem ou entrega que falhou, arquivo que não saiu do
    convidado), `SecretStoreLocked`/`SecretStoreUnavailable` do cofre (a mensagem deles fala da chave mestra, nunca
    do valor). Se o bloco de quem chama levantar, a exceção dele segue; a limpeza acontece antes.
    """
    ref = _referencia(db, profile_id)
    try:
        segredo = secrets.get_secret(ref)
    except KeyError:
        # A linha aponta para uma referência que o cofre não tem (apagada à mão, banco restaurado sem o cofre).
        segredo = None
    if segredo is None:
        raise SegredoDeRedeError(f"O segredo do perfil de rede {profile_id} não está no cofre; recadastre-o.")
    conteudo = _montado(montar, segredo)
    del segredo                      # o valor em texto não precisa viver além da montagem
    if conteudo is None:
        raise SegredoDeRedeError(f"Não foi possível montar o arquivo de rede do perfil {profile_id}.")
    caminho = _gravado(transporte, nome_do_arquivo, conteudo, modo)
    del conteudo
    if caminho is None:
        # O que pode ter ficado pela metade no convidado sai agora; se nem isso der, a mensagem diz.
        limpo = _apagado(transporte, nome_do_arquivo)
        raise SegredoDeRedeError(
            f"A entrega do segredo de rede ao aparelho {transporte.serial} falhou ({modo})"
            + ("." if limpo else "; o arquivo parcial pode ter ficado no convidado e precisa ser apagado."))
    log.info("segredo de rede do perfil %s entregue a %s por %s", profile_id, transporte.serial, modo)
    falhou_no_bloco = False
    try:
        yield EntregaDeSegredo(profile_id=profile_id, serial=transporte.serial, caminho=caminho, modo=modo)
    except BaseException:
        falhou_no_bloco = True
        raise
    finally:
        limpo = _apagado(transporte, nome_do_arquivo)
        if limpo:
            log.info("arquivo de rede do perfil %s apagado de %s", profile_id, transporte.serial)
        elif falhou_no_bloco:
            # A exceção do bloco é a que importa a quem chama; a sobra no convidado fica no log, sem o conteúdo.
            log.warning("o arquivo de rede do perfil %s ficou em %s (%s): apague-o", profile_id,
                        transporte.serial, caminho)
    if not limpo:
        raise SegredoDeRedeError(
            f"O arquivo de rede do perfil {profile_id} não foi apagado de {transporte.serial} ({caminho}).")
