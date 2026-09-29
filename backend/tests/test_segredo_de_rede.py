"""Segredos de rede, item 25.3 (ADR-056 §5): o consumidor restrito do cofre e a redação por formato da rede.

O que se prova aqui (tudo `simulated`: o `subprocess.run` do adb é um dublê com um sistema de arquivos de mentira do
convidado; nenhum adb, nenhum aparelho, nenhuma VPN):
- o segredo sai do cofre só pelo perfil de rede (nunca por uma `secret_ref` qualquer) e chega ao convidado pelo stdin
  do `adb exec-in` ou por arquivo temporário empurrado — nunca em argumento de processo, e byte a byte (sem `\\r\\n`);
- o arquivo temporário do host some antes do bloco de quem chama; o do convidado some ao sair, com erro ou sem;
- falha de montagem ou de transporte vira mensagem fixa, sem o segredo e sem a exceção original encadeada;
- `get_secret` só é chamado pelos consumidores documentados;
- a redação mascara `socks5://usuario:senha@`, `PrivateKey`, `PresharedKey` e a chave de 44 caracteres solta em
  contexto de WireGuard, e deixa a chave pública em paz (senão o cadastro recusaria um perfil legítimo).

Nenhum valor de segredo mora no teste: são gerados na hora (CLAUDE.md, invariante de segredo).
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import secrets as pysecrets
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator

import pytest

from app.db import Database, dumps
from app.devices import adb as adb_mod
from app.devices.adb import DIR_PRIVADO_NO_CONVIDADO, Adb
from app.devices.rede import ler_cadastro
from app.security.redaction import MASK, redact
from app.security.secret_store import MemoryKeyProvider, SecretStore
from app.security.segredo_de_rede import SegredoDeRedeError, segredo_no_convidado
from app.util import now_iso

from .conftest import _dsn_de_teste

SERIAL = "emulator-5640"
BACKEND = Path(__file__).resolve().parents[1]


def _chave_wg() -> str:
    """32 bytes aleatórios em base64: exatamente o formato de uma chave do WireGuard, sem ser a de ninguém."""
    return base64.b64encode(os.urandom(32)).decode("ascii")


# ============================================================================ dublê do adb
@dataclass
class ConvidadoFalso:
    """O `subprocess.run` do adb, com os arquivos do convidado num dicionário. Guarda cada argv e cada stdin."""

    arquivos: dict[str, bytes] = field(default_factory=dict)
    argvs: list[list[str]] = field(default_factory=list)
    stdins: list[bytes] = field(default_factory=list)
    modos: dict[str, str] = field(default_factory=dict)
    locais_empurrados: list[str] = field(default_factory=list)
    falhar_exec_in: bool = False
    truncar: bool = False
    falhar_rm: bool = False

    def __call__(self, cmd: list[str], **kw: object) -> subprocess.CompletedProcess:
        assert cmd[:3] == ["adb", "-s", SERIAL]
        args = cmd[3:]
        self.argvs.append(list(cmd))
        texto = bool(kw.get("text"))
        entrada = kw.get("input")

        def res(rc: int = 0, out: str = "", err: str = "") -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess(cmd, rc, out if texto else out.encode(), err if texto else err.encode())

        if args[0] == "exec-in":
            # O stdin tem de chegar em BYTES: em modo texto o Windows trocaria `\n` por `\r\n`.
            assert not texto and isinstance(entrada, bytes)
            self.stdins.append(entrada)
            if self.falhar_exec_in:
                return res(1, err="cat: " + entrada.decode("utf-8", "replace"))   # ecoa o conteúdo, de maldade
            destino = re.search(r"cat > (\S+)$", args[1])
            assert destino is not None and "umask 077" in args[1]
            self.arquivos[destino.group(1)] = entrada[:-1] if self.truncar else entrada
            self.modos[destino.group(1)] = "600"
            return res()
        if args[0] == "push":
            local, remoto = args[1], args[2]
            self.locais_empurrados.append(local)
            self.arquivos[remoto] = Path(local).read_bytes()
            self.modos[remoto] = "644"                     # o push leva o modo da origem; o chmod corrige
            return res()
        assert args[0] == "shell"
        linha = args[1]
        if m := re.fullmatch(r"stat -c %s (\S+)", linha):
            return res(0, f"{len(self.arquivos[m.group(1)])}\n") if m.group(1) in self.arquivos else res(1)
        if m := re.fullmatch(r"chmod 600 (\S+)", linha):
            self.modos[m.group(1)] = "600"
            return res()
        if m := re.fullmatch(r"rm -f (\S+)", linha):
            if self.falhar_rm:
                return res(1)
            self.arquivos.pop(m.group(1), None)
            return res()
        if linha.startswith("umask 077 && mkdir -p "):
            return res()
        raise AssertionError(f"comando inesperado no convidado: {linha}")


@pytest.fixture
def convidado(monkeypatch: pytest.MonkeyPatch) -> ConvidadoFalso:
    falso = ConvidadoFalso()
    monkeypatch.setattr(adb_mod.subprocess, "run", falso)
    return falso


def _adb() -> Adb:
    return Adb(SimpleNamespace(adb="adb", env=lambda: {}), SERIAL)      # type: ignore[arg-type]  - o dublê é o run


@pytest.fixture
def cofre(tmp_path: Path) -> Iterator[tuple[Database, SecretStore]]:
    db = Database(_dsn_de_teste() or tmp_path / "rede.sqlite3")
    db.migrate()
    try:
        yield db, SecretStore(db, MemoryKeyProvider())
    finally:
        db.close()


def _perfil(db: Database, secrets: SecretStore, segredo: str | None, *, pid: str = "vpn-teste") -> str:
    ref = secrets.store_secret(segredo) if segredo is not None else None
    db.execute("INSERT INTO network_profiles(id, name, kind, protocol, endpoint_host, endpoint_port, secret_ref,"
               " params, created_at, created_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
               (pid, pid, "vpn", "wireguard", "vpn.test", 51820, ref, dumps({}), now_iso(), "teste"))
    return pid


def _em_nenhum_argv(falso: ConvidadoFalso, valor: str) -> None:
    assert all(valor not in " ".join(a) for a in falso.argvs)


# ============================================================================ o consumidor restrito
def test_stdin_entrega_byte_a_byte_sem_argumento_e_apaga_ao_sair(convidado: ConvidadoFalso, cofre,
                                                                 caplog: pytest.LogCaptureFixture) -> None:
    db, secrets = cofre
    chave = _chave_wg()
    pid = _perfil(db, secrets, chave)
    conf = "[Interface]\nPrivateKey = {}\nAddress = 10.0.0.2/32\n"
    caplog.set_level(logging.DEBUG)
    with segredo_no_convidado(db, secrets, pid, _adb(), nome_do_arquivo="wg0.conf",
                              montar=lambda s: conf.format(s)) as entrega:
        caminho = f"{DIR_PRIVADO_NO_CONVIDADO}/wg0.conf"
        assert entrega.caminho == caminho and entrega.modo == "stdin" and entrega.serial == SERIAL
        # A configuração inteira chegou igual, com `\n` e não `\r\n`, e nasceu 600.
        assert convidado.arquivos[caminho] == conf.format(chave).encode("utf-8")
        assert b"\r\n" not in convidado.arquivos[caminho] and convidado.modos[caminho] == "600"
        assert chave not in repr(entrega)                         # o recibo não carrega o valor
    assert convidado.arquivos == {}                               # apagado ao sair do bloco
    assert convidado.stdins == [conf.format(chave).encode("utf-8")]
    _em_nenhum_argv(convidado, chave)
    assert chave not in caplog.text and "entregue" in caplog.text


def test_push_apaga_o_temporario_do_host_antes_do_bloco(convidado: ConvidadoFalso, cofre) -> None:
    db, secrets = cofre
    senha = pysecrets.token_urlsafe(18)
    pid = _perfil(db, secrets, senha)
    with segredo_no_convidado(db, secrets, pid, _adb(), nome_do_arquivo="proxy.txt", modo="push") as entrega:
        assert entrega.modo == "push"
        assert convidado.arquivos[entrega.caminho] == senha.encode() and convidado.modos[entrega.caminho] == "600"
        [local] = convidado.locais_empurrados
        assert not Path(local).exists()                           # o host não guarda cópia durante o uso
    assert convidado.arquivos == {} and convidado.stdins == []
    _em_nenhum_argv(convidado, senha)


def test_erro_no_bloco_segue_e_o_arquivo_sai_do_convidado(convidado: ConvidadoFalso, cofre) -> None:
    db, secrets = cofre
    pid = _perfil(db, secrets, _chave_wg())
    with pytest.raises(RuntimeError, match="cliente recusou"):
        with segredo_no_convidado(db, secrets, pid, _adb(), nome_do_arquivo="wg0.conf"):
            raise RuntimeError("cliente recusou a importação")
    assert convidado.arquivos == {}


def test_so_abre_segredo_de_perfil_de_rede(convidado: ConvidadoFalso, cofre) -> None:
    """Restrito de verdade: a referência sai da linha do perfil. A senha de uma conta, guardada no mesmo cofre, não
    tem caminho por aqui — nem passando a referência dela no lugar do perfil."""
    db, secrets = cofre
    ref_da_conta = secrets.store_secret(pysecrets.token_urlsafe(12))
    sem_segredo = _perfil(db, secrets, None, pid="proxy-aberto")
    for alvo in (ref_da_conta, "perfil-que-nao-existe", sem_segredo):
        with pytest.raises(SegredoDeRedeError):
            with segredo_no_convidado(db, secrets, alvo, _adb(), nome_do_arquivo="x.conf"):
                pytest.fail("não podia entrar no bloco")
    assert convidado.argvs == []                                  # nada chegou ao adb


def test_referencia_que_o_cofre_perdeu_e_recusada_sem_adb(convidado: ConvidadoFalso, cofre) -> None:
    db, secrets = cofre
    pid = _perfil(db, secrets, _chave_wg())
    ref = db.one("SELECT secret_ref FROM network_profiles WHERE id=?", (pid,))["secret_ref"]
    secrets.delete_secret(ref)
    with pytest.raises(SegredoDeRedeError, match="não está no cofre"):
        with segredo_no_convidado(db, secrets, pid, _adb(), nome_do_arquivo="wg0.conf"):
            pytest.fail("não podia entrar no bloco")
    assert convidado.argvs == []


def test_montagem_que_falha_nao_leva_o_segredo_na_mensagem(convidado: ConvidadoFalso, cofre) -> None:
    db, secrets = cofre
    chave = _chave_wg()
    pid = _perfil(db, secrets, chave)

    def montar(s: str) -> str:
        raise KeyError(f"campo ausente perto de {s}")

    with pytest.raises(SegredoDeRedeError) as exc:
        with segredo_no_convidado(db, secrets, pid, _adb(), nome_do_arquivo="wg0.conf", montar=montar):
            pytest.fail("não podia entrar no bloco")
    assert chave not in str(exc.value) and exc.value.__context__ is None and exc.value.__cause__ is None
    assert convidado.argvs == []


def test_transporte_que_falha_e_limpo_e_nao_ecoa(convidado: ConvidadoFalso, cofre) -> None:
    db, secrets = cofre
    chave = _chave_wg()
    pid = _perfil(db, secrets, chave)
    convidado.falhar_exec_in = True
    with pytest.raises(SegredoDeRedeError) as exc:
        with segredo_no_convidado(db, secrets, pid, _adb(), nome_do_arquivo="wg0.conf"):
            pytest.fail("não podia entrar no bloco")
    assert chave not in str(exc.value) and exc.value.__context__ is None
    assert any(a[3:5] == ["shell", f"rm -f {DIR_PRIVADO_NO_CONVIDADO}/wg0.conf"] for a in convidado.argvs)


def test_arquivo_que_nao_chegou_inteiro_e_falha(convidado: ConvidadoFalso, cofre) -> None:
    """`exec-in` não devolve o código do `cat`: sem reler o tamanho, um arquivo truncado passaria por entregue."""
    db, secrets = cofre
    pid = _perfil(db, secrets, _chave_wg())
    convidado.truncar = True
    with pytest.raises(SegredoDeRedeError, match="falhou"):
        with segredo_no_convidado(db, secrets, pid, _adb(), nome_do_arquivo="wg0.conf"):
            pytest.fail("não podia entrar no bloco")
    assert convidado.arquivos == {}                               # o parcial também sai


def test_sobra_no_convidado_e_avisada(convidado: ConvidadoFalso, cofre) -> None:
    db, secrets = cofre
    pid = _perfil(db, secrets, _chave_wg())
    convidado.falhar_rm = True
    with pytest.raises(SegredoDeRedeError, match="não foi apagado"):
        with segredo_no_convidado(db, secrets, pid, _adb(), nome_do_arquivo="wg0.conf"):
            pass


def test_nome_de_arquivo_fora_da_regra_nao_vira_comando(convidado: ConvidadoFalso, cofre) -> None:
    db, secrets = cofre
    pid = _perfil(db, secrets, _chave_wg())
    for nome in ("../x", "a;rm -rf /", "A.conf", "", "x" * 61):
        with pytest.raises(SegredoDeRedeError):
            with segredo_no_convidado(db, secrets, pid, _adb(), nome_do_arquivo=nome):
                pytest.fail("não podia entrar no bloco")
    assert convidado.argvs == []


def test_get_secret_so_nos_consumidores_documentados() -> None:
    """ADR-056 §5: um SEGUNDO consumidor, restrito. Um terceiro é decisão (docstring de `secret_store.py`), não
    descuido: quem precisar muda esta lista e o docstring juntos."""
    permitidos = {
        "app/security/segredo_de_rede.py",                    # provisão de rede (25.3)
        "app/security/rekey.py",                              # recifrar o cofre inteiro
        # O canal sensível recebe a função que resolve o valor no instante da digitação; estes dois a montam.
        "app/taskqueue/executor.py",
        "app/integrations/app_declarado/sessao.py",
    }
    achados = {p.relative_to(BACKEND).as_posix() for p in (BACKEND / "app").rglob("*.py")
               if re.search(r"\.get_secret\(", p.read_text(encoding="utf-8"))}
    assert achados == permitidos


# ============================================================================ redação por formato (25.3)
@pytest.mark.parametrize("esquema", ["socks5", "socks5h", "socks4", "SOCKS5"])
def test_senha_de_proxy_socks_na_url_e_mascarada(esquema: str) -> None:
    senha = pysecrets.token_urlsafe(12)
    redigido = redact(f"falha no upstream {esquema}://joao:{senha}@proxy.test:1080 (timeout)") or ""
    assert senha not in redigido and f"{esquema}://joao:{MASK}@proxy.test:1080" in redigido
    assert redact("socks5://proxy.test:1080") == "socks5://proxy.test:1080"      # sem senha, intacto


def test_configuracao_do_wireguard_mascara_as_chaves_secretas_e_mostra_a_publica() -> None:
    privada, publica, pre = _chave_wg(), _chave_wg(), _chave_wg()
    conf = (f"[Interface]\nPrivateKey = {privada}\nAddress = 10.0.0.2/32\n\n"
            f"[Peer]\nPublicKey = {publica}\nPresharedKey = {pre}\nEndpoint = vpn.test:51820\n")
    redigido = redact(conf) or ""
    assert privada not in redigido and pre not in redigido
    assert f"PublicKey = {publica}" in redigido and "Endpoint = vpn.test:51820" in redigido


def test_chaves_do_singbox_em_json() -> None:
    privada, publica, pre = _chave_wg(), _chave_wg(), _chave_wg()
    texto = json.dumps({"type": "wireguard", "private_key": privada, "peer_public_key": publica,
                        "pre_shared_key": pre, "psk": pre})
    redigido = redact(texto) or ""
    assert privada not in redigido and pre not in redigido and publica in redigido


def test_chave_solta_so_e_mascarada_em_contexto_de_wireguard() -> None:
    chave = _chave_wg()
    for linha in (f"wg set wg0 peer {chave} allowed-ips 0.0.0.0/0", f"WireGuard/GoBackend: handshake {chave}",
                  f"com.wireguard.android: chave {chave} recusada"):
        assert chave not in (redact(linha) or ""), linha
    # Fora de contexto, 44 caracteres de base64 são um hash qualquer: a redação não os come.
    assert redact(f"sha do pacote {chave}") == f"sha do pacote {chave}"
    # `PublicKey` numa linha não protege a chave solta da linha seguinte.
    publica = _chave_wg()
    redigido = redact(f"[Peer]\nPublicKey = {publica}\n{chave}") or ""
    assert publica in redigido and chave not in redigido


def test_cadastro_de_perfil_aceita_chave_publica_e_recusa_a_secreta_em_params() -> None:
    """O `params` do perfil é recusado quando a redação o mudaria (`rede.NetworkProfileInput._params`): a chave
    pública do peer tem de passar, a privada solta não — ela vai em `secret`, para o cofre."""
    base = {"name": "wg", "kind": "vpn", "protocol": "wireguard", "endpoint_host": "vpn.test", "endpoint_port": 51820}
    publica = _chave_wg()
    ok = ler_cadastro({**base, "params": {"client": "wireguard", "peer_public_key": publica}})
    assert ok.params["peer_public_key"] == publica
    for params in ({"client": "wireguard", "peer": _chave_wg()},
                   {"upstream": f"socks5://joao:{pysecrets.token_urlsafe(8)}@proxy.test:1080"},
                   {"presharedkey": _chave_wg()}):
        with pytest.raises(ValueError):
            ler_cadastro({**base, "params": params})
