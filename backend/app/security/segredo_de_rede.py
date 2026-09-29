"""Consumidor restrito do cofre para a provisão de rede (ADR-056 §5, item 25.3): o segundo lugar que abre um segredo.

O primeiro é o canal de entrada sensível (`sensitive_input.py`), que digita a senha de uma conta no instante do
login. Este é o do mesmo molde, para a rede: a chave privada do WireGuard, a senha do proxy ou a configuração do
sing-box saem do cofre aqui, e só aqui, no instante de chegar ao aparelho. A lista de quem chama `get_secret` é
conferida por teste (`tests/test_segredo_de_rede.py`): um terceiro consumidor é decisão, não descuido.

O que o torna RESTRITO:
- recebe o id de uma LINHA de rede, nunca um `secret_ref`: o `profile_id` de um perfil (`network_profiles`) ou o dono
  de uma chave gerada pela plataforma (`network_keys`, item 25.4). A referência é lida da própria linha, então a
  senha de uma conta do Instagram não sai por aqui nem que alguém passe a referência dela;
- o valor não volta a quem chama. Ele é montado (`montar`) e entregue DENTRO do bloco; quem chama recebe o caminho
  do arquivo no convidado (ou a URL de uso único que o serve, no 25.4) e mais nada — nem o tamanho, nem um hash;
- a entrega é por stdin (`adb exec-in`, o padrão) ou por arquivo temporário empurrado (`adb push`), nunca por
  argumento de processo; o arquivo temporário do host é apagado assim que o `push` volta, e o do convidado ao sair
  do bloco, com erro ou sem;
- a mensagem de erro é fixa. Exceção de `montar` ou do transporte pode trazer o texto que carregava o segredo; ela é
  descartada dentro de uma função, e quem levanta o erro fica fora de qualquer `except`, para o original não ir
  encadeado em `__context__` nem aparecer num traceback de log (o mesmo cuidado de `sensitive_input._ran`).

O 25.4 acrescentou dois usos do mesmo molde: a configuração do servidor sing-box do central, gravada num arquivo com
ACL só do usuário (`gravar_configuracao_do_servidor`: o processo recebe o CAMINHO, nunca o conteúdo), e a geração das
chaves WireGuard (`gerar_chave_wireguard`: a privada nasce aqui e vai direto ao cofre; quem chama recebe a pública).

O log diz QUE houve entrega (perfil, aparelho, modo), como o canal sensível diz que houve digitação. Evento e
evidência não recebem nada daqui: o 25.4 registra a observação pelo `rede.registrar_observacao`, que não conhece o
valor.
"""
from __future__ import annotations

import base64
import contextlib
import logging
import os
import tempfile
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

from ..db import Database
from ..util import now_iso
from .secret_store import SecretStore

log = logging.getLogger("poc.security")

ModoDeEntrega = Literal["stdin", "push"]
TipoDeFonte = Literal["perfil", "chave"]


class SegredoDeRedeError(RuntimeError):
    """Falha ao entregar o segredo de rede. A mensagem é fixa: nunca carrega texto do transporte nem do segredo."""


class TransporteDeArquivoPrivado(Protocol):
    """O que o consumidor precisa do aparelho — `devices.adb.Adb` cumpre, e o servidor HTTP de uso único do 25.4
    (`devices.rede_aplicacao.ServidorDeUmaVez`) também. Protocolo, e não import, para o cofre não depender da camada
    de aparelhos (e o teste trocar o adb por um dublê sem subprocesso)."""

    serial: str

    def gravar_arquivo_privado(self, nome: str, conteudo: bytes) -> str: ...
    def enviar_arquivo_privado(self, local: str, nome: str, *, tamanho: int) -> str: ...
    def apagar_arquivo_privado(self, nome: str) -> None: ...


@dataclass(frozen=True)
class Fonte:
    """De onde sai UM segredo: a linha de um perfil de rede ou a de uma chave gerada pela plataforma. Sempre por id de
    linha — é o que impede este consumidor de abrir uma referência qualquer do cofre."""

    tipo: TipoDeFonte
    id: str

    def descrever(self) -> str:
        return f"do perfil de rede {self.id}" if self.tipo == "perfil" else f"da chave de rede de {self.id}"


@dataclass(frozen=True)
class EntregaDeSegredo:
    """O recibo da entrega. Só identifica ONDE o segredo está; o que ele é fica no cofre e no destino.

    `profile_id` é o id da primeira fonte (o perfil, no uso de uma fonte só do 25.3); `fontes` lista todas."""

    profile_id: str
    serial: str
    caminho: str
    modo: ModoDeEntrega
    fontes: tuple[str, ...] = ()


def _referencia(db: Database, profile_id: str) -> str:
    """A `secret_ref` do perfil de rede — um dos dois jeitos de este consumidor chegar a uma referência."""
    row = db.one("SELECT secret_ref FROM network_profiles WHERE id=?", (profile_id,))
    if row is None:
        raise SegredoDeRedeError(f"Perfil de rede {profile_id} não encontrado.")
    if not row["secret_ref"]:
        raise SegredoDeRedeError(f"O perfil de rede {profile_id} não tem segredo no cofre.")
    return str(row["secret_ref"])


def _referencia_da_chave(db: Database, dono: str) -> str:
    """A `secret_ref` da chave WireGuard que a plataforma gerou para `dono` (o servidor ou um aparelho)."""
    row = db.one("SELECT secret_ref FROM network_keys WHERE owner=?", (dono,))
    if row is None:
        raise SegredoDeRedeError(f"Não há chave de rede gerada para {dono}.")
    return str(row["secret_ref"])


def _abrir(db: Database, secrets: SecretStore, fontes: Mapping[str, Fonte]) -> dict[str, str]:
    """Os valores de cada fonte, pelo nome que `montar` usa. Todas as referências são resolvidas ANTES de abrir a
    primeira: fonte inexistente é recusada sem nada ter saído do cofre."""
    if not fontes:
        raise SegredoDeRedeError("Nenhuma fonte de segredo de rede foi pedida.")
    refs = {nome: (_referencia(db, f.id) if f.tipo == "perfil" else _referencia_da_chave(db, f.id))
            for nome, f in fontes.items()}
    valores: dict[str, str] = {}
    for nome, ref in refs.items():
        try:
            valores[nome] = secrets.get_secret(ref)
        except KeyError:
            # A linha aponta para uma referência que o cofre não tem (apagada à mão, banco restaurado sem o cofre).
            falta = fontes[nome]
            valores.clear()
            break
    else:
        return valores
    raise SegredoDeRedeError(f"O segredo {falta.descrever()} não está no cofre; recadastre-o.")


def _montado(montar: Callable[[Mapping[str, str]], str | bytes], valores: Mapping[str, str]) -> bytes | None:
    """Monta o conteúdo; `None` quando `montar` falhou. A exceção morre AQUI dentro: a mensagem de um `KeyError` ou
    de um `format` pode citar o próprio segredo."""
    try:
        conteudo = montar(valores)
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
def segredos_entregues(db: Database, secrets: SecretStore, fontes: Mapping[str, Fonte],
                       transporte: TransporteDeArquivoPrivado, *, nome_do_arquivo: str,
                       montar: Callable[[Mapping[str, str]], str | bytes],
                       modo: ModoDeEntrega = "stdin") -> Iterator[EntregaDeSegredo]:
    """Monta um arquivo com os segredos de VÁRIAS fontes e o entrega pelo `transporte` durante o bloco.

    É o caso do perfil do cliente sing-box (25.4): a chave do aparelho (fonte `chave`), a senha do proxy (fonte
    `perfil`) e, num provedor externo, a chave que veio com o perfil — tudo no mesmo JSON. `montar(valores)` recebe
    `{nome: valor}` e roda aqui dentro. Mesmas garantias de `segredo_no_convidado`: nada volta a quem chama, erro com
    mensagem fixa, destino apagado ao sair do bloco (com erro ou sem)."""
    alvo = " e ".join(f.descrever() for f in fontes.values())
    valores = _abrir(db, secrets, fontes)
    conteudo = _montado(montar, valores)
    valores.clear()                  # os valores em texto não precisam viver além da montagem
    del valores
    if conteudo is None:
        raise SegredoDeRedeError(f"Não foi possível montar o arquivo de rede {alvo}.")
    caminho = _gravado(transporte, nome_do_arquivo, conteudo, modo)
    del conteudo
    if caminho is None:
        # O que pode ter ficado pela metade no destino sai agora; se nem isso der, a mensagem diz.
        limpo = _apagado(transporte, nome_do_arquivo)
        raise SegredoDeRedeError(
            f"A entrega do segredo de rede ao aparelho {transporte.serial} falhou ({modo})"
            + ("." if limpo else "; o arquivo parcial pode ter ficado no convidado e precisa ser apagado."))
    ids = tuple(f.id for f in fontes.values())
    log.info("segredo de rede %s entregue a %s por %s", alvo, transporte.serial, modo)
    falhou_no_bloco = False
    try:
        yield EntregaDeSegredo(profile_id=ids[0], serial=transporte.serial, caminho=caminho, modo=modo, fontes=ids)
    except BaseException:
        falhou_no_bloco = True
        raise
    finally:
        limpo = _apagado(transporte, nome_do_arquivo)
        if limpo:
            log.info("arquivo de rede %s apagado de %s", alvo, transporte.serial)
        elif falhou_no_bloco:
            # A exceção do bloco é a que importa a quem chama; a sobra no destino fica no log, sem o conteúdo.
            log.warning("o arquivo de rede %s ficou em %s (%s): apague-o", alvo, transporte.serial, caminho)
    if not limpo:
        raise SegredoDeRedeError(f"O arquivo de rede {alvo} não foi apagado de {transporte.serial} ({caminho}).")


@contextlib.contextmanager
def segredo_no_convidado(db: Database, secrets: SecretStore, profile_id: str,
                         transporte: TransporteDeArquivoPrivado, *, nome_do_arquivo: str,
                         montar: Callable[[str], str | bytes] | None = None,
                         modo: ModoDeEntrega = "stdin") -> Iterator[EntregaDeSegredo]:
    """Põe o segredo do perfil num arquivo 600 do convidado durante o bloco e o apaga ao sair.

    `montar(segredo)` devolve o conteúdo do arquivo — a configuração do cliente com a chave dentro, por exemplo — e
    roda aqui dentro, para a configuração completa também não sair do bloco. Sem ele, o arquivo é o segredo puro.
    Dentro do bloco, quem chama manda o cliente importar o arquivo; o recibo diz só onde ele está. É o caso de uma
    fonte só de `segredos_entregues`.

    Levanta `SegredoDeRedeError` (perfil sem segredo, montagem ou entrega que falhou, arquivo que não saiu do
    convidado), `SecretStoreLocked`/`SecretStoreUnavailable` do cofre (a mensagem deles fala da chave mestra, nunca
    do valor). Se o bloco de quem chama levantar, a exceção dele segue; a limpeza acontece antes.
    """
    def montar_um(valores: Mapping[str, str]) -> str | bytes:
        return montar(valores["segredo"]) if montar is not None else valores["segredo"]

    with segredos_entregues(db, secrets, {"segredo": Fonte("perfil", profile_id)}, transporte,
                            nome_do_arquivo=nome_do_arquivo, montar=montar_um, modo=modo) as entrega:
        yield entrega


# ============================================================================ servidor do central (25.4)
def gravar_configuracao_do_servidor(db: Database, secrets: SecretStore, fontes: Mapping[str, Fonte], destino: Path,
                                    *, montar: Callable[[Mapping[str, str]], str | bytes],
                                    restringir: Callable[[Path], None]) -> Path:
    """Grava a configuração do servidor sing-box (chave do servidor, senhas do proxy) em `destino` e devolve o caminho.

    O processo recebe o CAMINHO no argumento, nunca o conteúdo. O arquivo nasce vazio, recebe a ACL só do usuário
    (`restringir`) e só então o conteúdo; a troca pelo arquivo anterior é atômica (`os.replace`), para o processo
    nunca ler uma configuração pela metade. Sem ACL não há gravação: `restringir` que falha recusa antes de o
    segredo sair do cofre."""
    alvo = " e ".join(f.descrever() for f in fontes.values())
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporario = destino.with_name(destino.name + ".novo")
    with contextlib.suppress(OSError):
        temporario.unlink()
    try:
        temporario.touch(mode=0o600, exist_ok=False)
        restringir(temporario)
    except Exception:  # noqa: BLE001 - a mensagem do icacls/chmod não carrega segredo, mas a regra é uma só
        with contextlib.suppress(OSError):
            temporario.unlink()
        falhou = True
    else:
        falhou = False
    if falhou:
        raise SegredoDeRedeError(f"A configuração do servidor de rede não foi gravada: o arquivo não ficou restrito ao "
                                 f"usuário ({destino.parent}).")
    valores = _abrir(db, secrets, fontes)
    conteudo = _montado(montar, valores)
    valores.clear()
    del valores
    if conteudo is None:
        with contextlib.suppress(OSError):
            temporario.unlink()
        raise SegredoDeRedeError(f"Não foi possível montar a configuração do servidor de rede ({alvo}).")
    try:
        with open(temporario, "wb") as arq:
            arq.write(conteudo)
        os.replace(temporario, destino)
        ok = True
    except OSError:
        ok = False
    del conteudo
    if not ok:
        with contextlib.suppress(OSError):
            temporario.unlink()
        raise SegredoDeRedeError(f"A configuração do servidor de rede não foi gravada em {destino.parent}.")
    log.info("configuração do servidor de rede gravada (%s) em %s", alvo, destino)
    return destino


# ============================================================================ chaves WireGuard da plataforma (25.4)
def gerar_chave_wireguard(db: Database, secrets: SecretStore, dono: str, *, kind: Literal["servidor", "aparelho"],
                          address: str | None) -> str:
    """Gera o par X25519 do WireGuard para `dono`, guarda a PRIVADA no cofre e devolve só a PÚBLICA.

    Idempotente: dono que já tem chave recebe a pública de sempre (a do aparelho é estável: trocar a chave a cada
    aplicação derrubaria o túnel velho no servidor antes de o novo existir). A chave e a linha entram na mesma
    transação: um INSERT recusado (endereço repetido, corrida) não deixa segredo órfão no cofre."""
    row = db.one("SELECT public_key FROM network_keys WHERE owner=?", (dono,))
    if row is not None:
        return str(row["public_key"])
    privada = X25519PrivateKey.generate()
    publica = base64.b64encode(privada.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode("ascii")
    with db.tx():
        ref = secrets.store_secret(base64.b64encode(
            privada.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())).decode("ascii"))
        db.execute("INSERT INTO network_keys(owner, kind, secret_ref, public_key, address, created_at)"
                   " VALUES (?,?,?,?,?,?)", (dono, kind, ref, publica, address, now_iso()))
    del privada
    log.info("chave de rede gerada para %s (%s)", dono, kind)
    return publica


def apagar_chave_wireguard(db: Database, secrets: SecretStore, dono: str) -> bool:
    """Tira a chave de `dono` do cofre e da tabela, juntas. `False` quando não havia chave."""
    row = db.one("SELECT secret_ref FROM network_keys WHERE owner=?", (dono,))
    if row is None:
        return False
    with db.tx():
        db.execute("DELETE FROM network_keys WHERE owner=?", (dono,))
        secrets.delete_secret(str(row["secret_ref"]))
    log.info("chave de rede de %s apagada", dono)
    return True
