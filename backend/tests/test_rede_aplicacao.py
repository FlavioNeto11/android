"""Rede por aparelho, item 25.4 (ADR-056): aplicação no aparelho, convergência e o servidor sing-box do central.

O que se prova aqui (tudo `simulated`: aparelho falso no lugar do adb, processo falso no lugar do sing-box, nenhuma
VPN, nenhum emulador; o único socket de verdade é o HTTP de uso único, em 127.0.0.1 e porta efêmera):
- a configuração do servidor fecha o central e a rede local (limitação medida no 25.1): loopback (API 8000, adb
  5037, consoles), a sub-rede do túnel, faixas privadas e link-local são recusados para o túnel E para o proxy; só a
  porta do proxy do central passa, e só vinda do túnel — conferido por um avaliador das regras, na ordem;
- o processo do servidor recebe só o CAMINHO da configuração (nunca segredo em argumento), a pasta e o arquivo são
  restritos antes de o segredo sair do cofre, ele reinicia quando os pares mudam e para sem pares, e um órfão só é
  adotado se for nosso;
- a receita no aparelho segue a medição: appops, VPN desligada, perfil servido UMA vez (10.0.2.2 no local, `adb
  reverse` no do worker), toques achados pelo TEXTO só no pacote do cliente, `sync`, always-on e bloqueio relidos,
  relatórios de falha apagados, `sync`; a chave do aparelho só atravessa o HTTP de uso único;
- a convergência aplica, pede o reinício depois de soltar o aparelho, conecta (uid 2000) e registra `conectado`;
  túnel que não sobe reinicia até o teto e vira `pendente` com erro e espera; deriva regride; wipe invalida; desfazer
  tira a rede e apaga a linha; loja e quarentena ficam fora; a porta da rede segura a tarefa com política exigida;
- `stop_process` faz `sync` antes do `emu kill` (o perfil importado 26 s antes do `restart` se perdeu no 25.1).

Nenhum valor de segredo mora no teste: chaves e senhas são geradas na hora (CLAUDE.md, invariante de segredo).
"""
from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import secrets as pysecrets
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

import httpx
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from app.commands import despacho
from app.config import RedeCfg, RedeServidorCfg
from app.db import Database, dumps
from app.devices import emulator as emu
from app.devices import rede
from app.devices.rede_aplicacao import (Elemento, NoDaTela, Plano, ProxyDoCliente, RedeAplicacaoError, ServidorDeUmaVez,
                                        TunelWg, comando_de_observacao, config_do_cliente, desfazer, ler_observacao,
                                        observar, provisionar, tocar_importacao)
from app.devices.rede_servidor import (FAIXAS_RECUSADAS, Par, ServidorDeRede, ServidorDeRedeError,
                                       config_do_servidor, regras_de_rota)
from app.main import create_app
from app.security.secret_store import MemoryKeyProvider, SecretStore
from app.security.segredo_de_rede import (Fonte, SegredoDeRedeError, apagar_chave_wireguard, gerar_chave_wireguard,
                                          gravar_configuracao_do_servidor, segredos_entregues)
from app.util import now_iso

from .conftest import Harness, _dsn_de_teste

PKG = "io.nekohasekai.sfa"
CFG = RedeServidorCfg()


# ============================================================================ avaliador das regras do servidor
def _e_ip(x: str) -> bool:
    try:
        ipaddress.ip_address(x)
        return True
    except ValueError:
        return False


def _decidir(regras: list[dict[str, object]], *, entrada: str, destino: str, porta: int,
             dns: dict[str, str] | None = None) -> str:
    """A primeira regra que casa decide, como no sing-box; `resolve` troca o nome pelo IP e segue. Sem regra que
    case, vale o `final` (`direct`). É a SEMÂNTICA que o teste confere; a do binário 1.14.2 é `not_run`."""
    alvo = destino
    for r in regras:
        if entrada not in r.get("inbound", [entrada]):                     # type: ignore[operator]
            continue
        if r["action"] == "resolve":
            if not _e_ip(alvo):
                alvo = (dns or {})[alvo]
            continue
        if "domain_suffix" in r and (_e_ip(alvo) or not any(
                alvo == s or alvo.endswith("." + s) for s in r["domain_suffix"])):          # type: ignore[union-attr]
            continue
        if "ip_cidr" in r and (not _e_ip(alvo) or not any(
                ipaddress.ip_address(alvo) in ipaddress.ip_network(c) for c in r["ip_cidr"])):  # type: ignore[union-attr]
            continue
        if "port" in r and porta not in r["port"]:                          # type: ignore[operator]
            continue
        return "reject" if r["action"] == "reject" else str(r["outbound"])
    return "final:direct"


def _reescrita(destino: str) -> str:
    """O endpoint WireGuard do sing-box reescreve o próprio endereço para o loopback (medido no 25.1, 18:26:59)."""
    return "127.0.0.1" if destino == "10.66.0.1" else destino


def test_regras_do_servidor_fecham_o_central_e_a_rede_local() -> None:
    regras = regras_de_rota(CFG, com_proxy=True)
    tunel = [
        ("127.0.0.1", 8000, "reject"), ("127.0.0.1", 5037, "reject"), ("127.0.0.1", 5554, "reject"),
        ("10.66.0.1", 8000, "reject"), ("10.66.0.1", 5037, "reject"),
        ("127.0.0.1", 18080, "direct"), ("10.66.0.1", 18080, "direct"),         # só o proxy do central, pelo túnel
        ("10.66.0.7", 80, "reject"),                                             # outro aparelho do túnel
        ("192.168.1.19", 8000, "reject"), ("10.0.2.15", 5555, "reject"), ("172.20.0.1", 22, "reject"),
        ("169.254.169.254", 80, "reject"), ("100.64.0.1", 443, "reject"), ("224.0.0.251", 5353, "reject"),
        ("::1", 8000, "reject"), ("fe80::1", 80, "reject"), ("fd00::1", 80, "reject"), ("::ffff:127.0.0.1", 80, "reject"),
        ("1.1.1.1", 53, "final:direct"), ("104.26.12.205", 80, "final:direct"), ("2606:4700::1111", 443, "final:direct"),
    ]
    for destino, porta, esperado in tunel:
        for d in {destino, _reescrita(destino)}:
            assert _decidir(regras, entrada="wg-srv", destino=d, porta=porta) == esperado, (d, porta)
    # O proxy autenticado não é porta dos fundos: nada do central nem da rede local, nem por NOME.
    dns = {"intranet.lan": "192.168.0.10", "api.ipify.org": "104.26.12.205", "rebind.test": "127.0.0.1"}
    proxy = [("127.0.0.1", 8000, "reject"), ("127.0.0.1", 18080, "reject"), ("10.66.0.1", 18080, "reject"),
             ("localhost", 8000, "reject"), ("api.localhost", 80, "reject"), ("intranet.lan", 80, "reject"),
             ("rebind.test", 8000, "reject"), ("192.168.1.19", 8000, "reject"),
             ("api.ipify.org", 80, "final:direct"), ("8.8.8.8", 53, "final:direct")]
    for destino, porta, esperado in proxy:
        assert _decidir(regras, entrada="proxy-in", destino=destino, porta=porta, dns=dns) == esperado, destino
    # A sub-rede do túnel entra junto com as faixas fixas.
    recusa = next(r for r in regras if r["action"] == "reject" and "ip_cidr" in r)
    assert "10.66.0.0/24" in recusa["ip_cidr"] and set(FAIXAS_RECUSADAS) <= set(recusa["ip_cidr"])  # type: ignore[operator]
    assert recusa["inbound"] == ["wg-srv", "proxy-in"]


def test_sem_proxy_nem_a_porta_do_proxy_passa() -> None:
    regras = regras_de_rota(CFG, com_proxy=False)
    assert _decidir(regras, entrada="wg-srv", destino="127.0.0.1", porta=18080) == "reject"
    assert all("proxy-in" not in r["inbound"] for r in regras)                   # type: ignore[operator]
    conf = config_do_servidor(chave_privada="x", pares=[], usuarios={}, cfg=CFG, caminho_do_log=Path("s.log"))
    assert conf["inbounds"] == []


def test_um_par_por_aparelho_e_o_proxy_so_em_loopback() -> None:
    pares = [Par("android-05", "pub-5", "10.66.0.2"), Par("android-02", "pub-2", "10.66.0.3")]
    conf = config_do_servidor(chave_privada="k", pares=pares, usuarios={"piloto": "s"}, cfg=CFG,
                              caminho_do_log=Path("C:/x/servidor.log"))
    [ep] = conf["endpoints"]                                                     # type: ignore[misc]
    assert ep["address"] == ["10.66.0.1/24"] and ep["listen_port"] == 51820 and ep["system"] is False
    assert ep["peers"] == [{"public_key": "pub-5", "allowed_ips": ["10.66.0.2/32"]},
                           {"public_key": "pub-2", "allowed_ips": ["10.66.0.3/32"]}]
    [mixed] = conf["inbounds"]                                                   # type: ignore[misc]
    assert mixed["listen"] == "127.0.0.1" and mixed["listen_port"] == 18080
    assert conf["route"]["final"] == "direct" and conf["log"]["output"] == "C:/x/servidor.log"  # type: ignore[index]


# ============================================================================ cofre e consumidor (25.3 estendido)
@pytest.fixture
def cofre(tmp_path: Path) -> Iterator[tuple[Database, SecretStore]]:
    db = Database(_dsn_de_teste() or tmp_path / "rede.sqlite3")
    db.migrate()
    try:
        yield db, SecretStore(db, MemoryKeyProvider())
    finally:
        db.close()


def _perfil(db: Database, secrets: SecretStore, pid: str, *, kind: str = "vpn", protocol: str = "singbox",
            params: dict[str, object] | None = None, segredo: str | None = None, host: str = "10.0.2.2",
            porta: int = 51820) -> str:
    ref = secrets.store_secret(segredo) if segredo is not None else None
    db.execute("INSERT INTO network_profiles(id, name, kind, protocol, endpoint_host, endpoint_port, secret_ref,"
               " params, created_at, created_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
               (pid, pid, kind, protocol, host, porta, ref, dumps(params or {}), now_iso(), "teste"))
    return pid


def _pedir(db: Database, iid: str, *, vpn: str | None, proxy: str | None = None, policy: str = "exigida") -> None:
    db.execute("INSERT INTO device_network(instance_id, vpn_profile_id, proxy_profile_id, policy, desired_rev, state,"
               " updated_at) VALUES (?,?,?,?,1,'pendente',?)", (iid, vpn, proxy, policy, now_iso()))


def test_chave_do_wireguard_nasce_no_cofre_e_so_a_publica_sai(cofre) -> None:
    db, secrets = cofre
    publica = gerar_chave_wireguard(db, secrets, "android-05", kind="aparelho", address="10.66.0.2")
    assert gerar_chave_wireguard(db, secrets, "android-05", kind="aparelho", address="10.66.0.9") == publica
    row = db.one("SELECT * FROM network_keys WHERE owner='android-05'")
    assert row["public_key"] == publica and row["address"] == "10.66.0.2" and row["kind"] == "aparelho"
    privada = base64.b64decode(secrets.get_secret(row["secret_ref"]))           # só o teste abre o cofre
    assert len(privada) == 32
    calculada = X25519PrivateKey.from_private_bytes(privada).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    assert base64.b64encode(calculada).decode() == publica                         # a pública é a da privada
    assert secrets.get_secret(row["secret_ref"]) not in json.dumps(dict(row))
    assert apagar_chave_wireguard(db, secrets, "android-05") and not secrets.exists(row["secret_ref"])
    assert db.one("SELECT 1 FROM network_keys WHERE owner='android-05'") is None


@dataclass
class DestinoFalso:
    serial: str = "destino-falso"
    recebido: dict[str, bytes] = field(default_factory=dict)
    apagados: list[str] = field(default_factory=list)

    def gravar_arquivo_privado(self, nome: str, conteudo: bytes) -> str:
        self.recebido[nome] = conteudo
        return f"falso://{nome}"

    def enviar_arquivo_privado(self, local: str, nome: str, *, tamanho: int) -> str:
        raise NotImplementedError

    def apagar_arquivo_privado(self, nome: str) -> None:
        self.apagados.append(nome)


def test_varias_fontes_num_arquivo_so_pela_linha(cofre) -> None:
    db, secrets = cofre
    senha = pysecrets.token_urlsafe(12)
    proxy = _perfil(db, secrets, "proxy-a", kind="proxy", protocol="socks5", segredo=senha)
    gerar_chave_wireguard(db, secrets, "android-02", kind="aparelho", address="10.66.0.4")
    destino = DestinoFalso()
    vistos: dict[str, str] = {}

    def montar(v):
        vistos.update(v)
        return json.dumps({"k": v["chave"], "p": v["senha"]})

    with segredos_entregues(db, secrets, {"chave": Fonte("chave", "android-02"), "senha": Fonte("perfil", proxy)},
                            destino, nome_do_arquivo="perfil.json", montar=montar) as entrega:
        assert entrega.caminho == "falso://perfil.json" and entrega.fontes == ("android-02", "proxy-a")
        assert senha in destino.recebido["perfil.json"].decode() and senha not in repr(entrega)
    assert destino.apagados == ["perfil.json"] and vistos["senha"] == senha
    # Chave que não existe: recusada antes de abrir qualquer coisa do cofre.
    with pytest.raises(SegredoDeRedeError, match="Não há chave"):
        with segredos_entregues(db, secrets, {"chave": Fonte("chave", "android-99")}, DestinoFalso(),
                                nome_do_arquivo="x.json", montar=lambda v: "x"):
            pytest.fail("não podia entrar")


def test_configuracao_do_servidor_so_e_escrita_depois_da_acl(cofre, tmp_path: Path) -> None:
    db, secrets = cofre
    gerar_chave_wireguard(db, secrets, "servidor", kind="servidor", address=None)
    destino = tmp_path / "srv" / "servidor.json"
    vistos: list[tuple[str, int]] = []

    def restringir(p: Path) -> None:
        vistos.append((p.name, p.stat().st_size))                              # vazio quando a ACL é posta

    caminho = gravar_configuracao_do_servidor(db, secrets, {"servidor": Fonte("chave", "servidor")}, destino,
                                              montar=lambda v: json.dumps({"k": v["servidor"]}), restringir=restringir)
    assert caminho == destino and vistos == [("servidor.json.novo", 0)] and json.loads(destino.read_text())["k"]

    def recusa(_p: Path) -> None:
        raise OSError("icacls: acesso negado")

    outro = tmp_path / "srv2" / "servidor.json"
    with pytest.raises(SegredoDeRedeError, match="restrito"):
        gravar_configuracao_do_servidor(db, secrets, {"servidor": Fonte("chave", "servidor")}, outro,
                                        montar=lambda v: v["servidor"], restringir=recusa)
    assert not outro.exists() and not list(outro.parent.iterdir())


# ============================================================================ o servidor gerenciado
@dataclass
class ProcessosFalsos:
    vivos: set[int] = field(default_factory=set)
    lancados: list[list[str]] = field(default_factory=list)
    encerrados: list[int] = field(default_factory=list)
    proximo: int = 7000
    morrer_ao_lancar: bool = False

    def lancar(self, argv: list[str], *, cwd: Path, saida: Path) -> int:
        self.lancados.append(list(argv))
        self.proximo += 1
        if self.morrer_ao_lancar:
            saida.write_text("FATAL decode config: unknown field\n", encoding="utf-8")
        else:
            self.vivos.add(self.proximo)
        return self.proximo

    def vivo(self, pid: int, binario: Path, config: Path) -> bool:
        return pid in self.vivos

    def encerrar(self, pid: int) -> None:
        self.encerrados.append(pid)
        self.vivos.discard(pid)


def _servidor(db: Database, secrets: SecretStore, tmp_path: Path, processos: ProcessosFalsos,
              restritos: list[Path] | None = None) -> ServidorDeRede:
    binario = tmp_path / "bin" / "sing-box.exe"
    binario.parent.mkdir(exist_ok=True)
    binario.write_bytes(b"")
    s = ServidorDeRede(db, secrets, lambda: CFG, pasta=tmp_path / "rede" / "servidor", binario=lambda: binario,
                       processos=processos, restringir=(restritos.append if restritos is not None else lambda p: None))
    s.espera_de_subida_s = 0
    return s


async def test_servidor_sobe_com_o_primeiro_par_sem_segredo_no_argumento(cofre, tmp_path: Path) -> None:
    db, secrets = cofre
    procs, restritos = ProcessosFalsos(), []
    srv = _servidor(db, secrets, tmp_path, procs, restritos)
    senha = pysecrets.token_urlsafe(14)
    vpn = _perfil(db, secrets, "vpn-central", params={"servidor": "central"})
    proxy = _perfil(db, secrets, "proxy-central", kind="proxy", protocol="socks5", host="10.66.0.1", porta=18080,
                    params={"servidor": "central", "username": "android"}, segredo=senha)
    assert (await srv.garantir())["running"] is False and procs.lancados == []   # ninguém pede: nada roda
    _pedir(db, "android-05", vpn=vpn, proxy=proxy)
    par = srv.par_do_aparelho("android-05")
    assert par.address == "10.66.0.2" and srv.par_do_aparelho("android-05") == par   # estável
    estado = await srv.garantir()
    assert estado["running"] is True and [p["address"] for p in estado["peers"]] == ["10.66.0.2"]
    [argv] = procs.lancados
    assert argv[1:] == ["run", "-c", str(srv.config)]                              # só o caminho
    conf = json.loads(srv.config.read_text(encoding="utf-8"))
    privada = conf["endpoints"][0]["private_key"]
    assert privada and conf["inbounds"][0]["users"] == [{"username": "android", "password": senha}]
    assert conf["endpoints"][0]["peers"] == [{"public_key": par.public_key, "allowed_ips": ["10.66.0.2/32"]}]
    for segredo in (privada, senha):
        assert all(segredo not in a for a in argv)
        assert segredo not in json.dumps(estado) and segredo not in (srv.pasta / "servidor.pid").read_text()
    assert srv.pasta in restritos and any(p.name == "servidor.json.novo" for p in restritos)
    assert estado["in_sync"] is True and estado["proxy"] == "127.0.0.1:18080"


async def test_servidor_reinicia_quando_os_pares_mudam_e_para_sem_pares(cofre, tmp_path: Path) -> None:
    db, secrets = cofre
    procs = ProcessosFalsos()
    srv = _servidor(db, secrets, tmp_path, procs)
    vpn = _perfil(db, secrets, "vpn-central", params={"servidor": "central"})
    _perfil(db, secrets, "vpn-externa", protocol="wireguard", params={})
    _pedir(db, "android-05", vpn=vpn)
    srv.par_do_aparelho("android-05")
    await srv.garantir()
    await srv.garantir()                                                           # nada mudou: nada reinicia
    assert len(procs.lancados) == 1 and procs.encerrados == []
    _pedir(db, "android-02", vpn=vpn)
    await srv.garantir()                                                           # sem chave ainda: não é par
    assert len(procs.lancados) == 1
    assert srv.par_do_aparelho("android-02").address == "10.66.0.3"
    await srv.garantir()
    assert len(procs.lancados) == 2 and len(procs.encerrados) == 1
    # Aparelho que troca para um perfil de outro servidor sai do conjunto.
    db.execute("UPDATE device_network SET vpn_profile_id='vpn-externa' WHERE instance_id='android-02'")
    await srv.garantir()
    assert len(procs.lancados) == 3
    db.execute("DELETE FROM device_network")
    estado = await srv.garantir()
    assert estado["running"] is False and len(procs.encerrados) == 3
    assert not srv.config.exists()                                                 # a chave não fica no disco


async def test_orfao_so_e_adotado_se_for_nosso(cofre, tmp_path: Path) -> None:
    db, secrets = cofre
    procs = ProcessosFalsos()
    srv = _servidor(db, secrets, tmp_path, procs)
    vpn = _perfil(db, secrets, "vpn-central", params={"servidor": "central"})
    _pedir(db, "android-05", vpn=vpn)
    srv.par_do_aparelho("android-05")
    await srv.garantir()
    # O backend "caiu": um gerenciador novo encontra o PID e o processo vivo com a mesma assinatura — adota.
    novo = _servidor(db, secrets, tmp_path, procs)
    await novo.garantir()
    assert len(procs.lancados) == 1 and novo.status()["running"] is True
    # PID reciclado por outro processo (não é o nosso): não é adotado nem encerrado; sobe um novo.
    procs.vivos.clear()
    terceiro = _servidor(db, secrets, tmp_path, procs)
    await terceiro.garantir()
    assert len(procs.lancados) == 2 and procs.encerrados == []


async def test_servidor_sem_binario_ou_que_cai_explica_sem_segredo(cofre, tmp_path: Path) -> None:
    db, secrets = cofre
    procs = ProcessosFalsos(morrer_ao_lancar=True)
    srv = _servidor(db, secrets, tmp_path, procs)
    vpn = _perfil(db, secrets, "vpn-central", params={"servidor": "central"})
    _pedir(db, "android-05", vpn=vpn)
    srv.par_do_aparelho("android-05")
    with pytest.raises(ServidorDeRedeError, match="saiu logo depois de subir: FATAL decode config"):
        await srv.garantir()
    srv._binario = lambda: tmp_path / "nao-existe.exe"
    with pytest.raises(ServidorDeRedeError, match="executável do sing-box não está"):
        await srv.garantir()
    assert "executável" in (srv.status()["detail"] or "")


def test_ultima_conexao_vem_do_log_do_servidor(cofre, tmp_path: Path) -> None:
    db, secrets = cofre
    srv = _servidor(db, secrets, tmp_path, ProcessosFalsos())
    srv.pasta.mkdir(parents=True)
    srv.log.write_text(
        "-0300 2026-09-29 14:56:31 INFO endpoint/wireguard[wg-srv]: inbound connection from 10.66.0.2:37428\n"
        "-0300 2026-09-29 15:23:08 INFO endpoint/wireguard[wg-srv]: inbound connection from 10.66.0.3:40012\n"
        "-0300 2026-09-29 15:31:03 INFO [2122822056 0ms] inbound/mixed[proxy-in]: inbound connection from "
        "127.0.0.1:59440\n", encoding="utf-8")
    assert srv.ultima_conexao("10.66.0.3") == "-0300 2026-09-29 15:23:08"
    assert srv.ultima_conexao("10.66.0.2") == "-0300 2026-09-29 14:56:31"
    assert srv.ultima_conexao("10.66.0.9") is None and srv.ultima_conexao("127.0.0.1") is None


# ============================================================================ o perfil do cliente
def _plano(**kw: object) -> Plano:
    base: dict[str, object] = dict(instance_id="android-05", rev=1, policy="exigida_com_bloqueio",
                                   vpn=TunelWg(Fonte("chave", "android-05"), "10.66.0.2/32", "10.0.2.2", 51820, "pub-srv"),
                                   proxy=None, dns="1.1.1.1", gerenciado=True)
    base.update(kw)
    return Plano(**base)                                                          # type: ignore[arg-type]


def test_perfil_do_cliente_compoe_vpn_e_proxy_como_no_piloto() -> None:
    so_vpn = config_do_cliente(_plano(), {"chave": "PRIV"})
    [ep] = so_vpn["endpoints"]                                                    # type: ignore[misc]
    assert ep["private_key"] == "PRIV" and ep["peers"][0]["address"] == "10.0.2.2" and ep["address"] == ["10.66.0.2/32"]
    assert so_vpn["route"]["final"] == "wg-out" and so_vpn["inbounds"][0]["strict_route"] is True  # type: ignore[index]
    assert so_vpn["dns"]["servers"][0]["detour"] == "wg-out"                     # type: ignore[index]
    socks = config_do_cliente(_plano(proxy=ProxyDoCliente("socks", "10.66.0.1", 18080, "android",
                                                          Fonte("perfil", "p"))), {"chave": "PRIV", "senha_do_proxy": "S"})
    saida = socks["outbounds"][0]                                                 # type: ignore[index]
    assert saida == {"type": "socks", "tag": "proxy-out", "server": "10.66.0.1", "server_port": 18080, "version": "5",
                     "username": "android", "password": "S", "detour": "wg-out"}
    assert socks["route"]["final"] == "proxy-out"                                 # type: ignore[index]
    assert {"network": "udp", "action": "reject"} not in socks["route"]["rules"]  # type: ignore[index]
    http = config_do_cliente(_plano(proxy=ProxyDoCliente("http", "proxy.test", 3128, None, None)), {"chave": "PRIV"})
    assert {"network": "udp", "action": "reject"} in http["route"]["rules"]      # type: ignore[index]  # T3
    so_proxy = config_do_cliente(_plano(vpn=None, proxy=ProxyDoCliente("http", "proxy.test", 3128, None, None)), {})
    assert so_proxy["endpoints"] == [] and so_proxy["dns"]["servers"][0] == {    # type: ignore[index]
        "type": "tcp", "tag": "dns-remoto", "server": "1.1.1.1", "detour": "proxy-out"}


# ============================================================================ o HTTP de uso único
def _get(url: str, timeout: float = 5) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:                   # noqa: S310 - 127.0.0.1 do teste
            return r.status, r.read()
    except urllib.error.HTTPError as exc:
        return exc.code, b""


def _ate_o_fato(cond: Callable[[], bool], o_que: str, prazo_s: float = 5.0) -> None:
    """Espera o FATO (o contador do servidor que roda noutra thread), nunca um tempo fixo."""
    fim = time.monotonic() + prazo_s
    while not cond() and time.monotonic() < fim:
        time.sleep(0.005)
    assert cond(), f"não aconteceu em {prazo_s} s: {o_que}"


def _atrasar_depois_do_corpo(srv: ServidorDeUmaVez, atraso_s: float) -> None:
    """Faz o servidor demorar `atraso_s` depois de CADA gravação no socket — como uma thread de servidor que perdeu a CPU
    entre entregar o corpo e contar a entrega (o que o `-n 8` provoca) — sem tocar no código de produção."""
    classe = srv._srv.RequestHandlerClass
    setup = classe.setup

    class _Lento:
        def __init__(self, real: object) -> None:
            self._real = real

        def write(self, dados: bytes) -> int:
            n = self._real.write(dados)                                           # type: ignore[attr-defined]
            time.sleep(atraso_s)
            return n

        def __getattr__(self, nome: str) -> object:
            return getattr(self._real, nome)

    def setup_lento(self: object) -> None:
        setup(self)                                                               # type: ignore[arg-type]
        self.wfile = _Lento(self.wfile)                                           # type: ignore[attr-defined]

    classe.setup = setup_lento                                                    # type: ignore[method-assign]


@pytest.mark.parametrize("atraso_s", [0.0, 0.3], ids=["servidor_ligeiro", "servidor_sem_cpu_depois_do_corpo"])
def test_servidor_de_uma_vez_serve_um_get_so_no_caminho_do_token(atraso_s: float) -> None:
    """K-072: o servidor entrega o corpo e SÓ DEPOIS conta (`entregues`); o cliente volta com o corpo antes da contagem.
    Afirmar `entregues == 1` ao receber a resposta é corrida (falhava com `-n 8`): o teste espera o fato. Com o atraso
    de 0,3 s depois do corpo a corrida deixa de ser rara e passa a ser certa para quem não espera."""
    srv = ServidorDeUmaVez(host_do_aparelho="10.0.2.2", serial="emulator-5640", prazo_s=10)
    _atrasar_depois_do_corpo(srv, atraso_s)
    url = srv.gravar_arquivo_privado("perfil-abc.json", b'{"k": 1}')
    assert url == f"http://10.0.2.2:{srv.porta}/perfil-abc.json"
    local = url.replace("10.0.2.2", "127.0.0.1")
    assert _get(local.replace("perfil-abc", "outro"))[0] == 404                   # caminho errado não conta
    _ate_o_fato(lambda: srv.recusados == 1, "o 404 do caminho errado contado")
    assert srv.entregues == 0                                                     # o 404 não é entrega
    assert _get(local) == (200, b'{"k": 1}')
    _ate_o_fato(lambda: srv.entregues == 1, "a entrega contada depois do corpo")
    with pytest.raises((urllib.error.URLError, ConnectionError, OSError)):
        _get(local, timeout=1)                                                     # depois do GET, fechou
    assert srv.entregues == 1 and srv.recusados == 1
    srv.apagar_arquivo_privado("perfil-abc.json")
    srv.apagar_arquivo_privado("perfil-abc.json")                                  # idempotente


def test_dois_servidores_de_uma_vez_ao_mesmo_tempo_nao_colidem_na_porta() -> None:
    """Descarta a hipótese da porta fixa: cada servidor pede a porta 0 (efêmera) e lê de volta a que o SO deu."""
    a = ServidorDeUmaVez(host_do_aparelho="10.0.2.2", serial="emulator-5640", prazo_s=10)
    b = ServidorDeUmaVez(host_do_aparelho="10.0.2.2", serial="emulator-5642", prazo_s=10)
    try:
        assert a.porta != b.porta and a.porta > 0 and b.porta > 0
        ua = a.gravar_arquivo_privado("a.json", b"A").replace("10.0.2.2", "127.0.0.1")
        ub = b.gravar_arquivo_privado("b.json", b"B").replace("10.0.2.2", "127.0.0.1")
        assert _get(ub.replace("b.json", "a.json"))[0] == 404                     # o caminho de A não existe em B
        assert _get(ua) == (200, b"A") and _get(ub) == (200, b"B")
        _ate_o_fato(lambda: a.entregues == 1 and b.entregues == 1, "uma entrega em cada servidor")
        assert (a.recusados, b.recusados) == (0, 1)
    finally:
        a.apagar_arquivo_privado("a.json")
        b.apagar_arquivo_privado("b.json")


@pytest.fixture(autouse=True)
def _sem_espera_do_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    """A espera de ~10 s do desfazer (stopped persistir) é real em produção; nos testes é zero, salvo o que a mede."""
    monkeypatch.setattr("app.devices.rede_aplicacao.PARADA_PERSISTIR_S", 0.0)


# ============================================================================ o aparelho falso da receita
@dataclass
class AparelhoFalso:
    """O aparelho pela porta `AparelhoDaRede`: shell, árvore, toque e `adb reverse`, com o estado do Android."""

    id: str = "android-05"
    serial: str = "emulator-5640"
    external: bool = False
    comandos: list[str] = field(default_factory=list)
    uid: int = 2000
    instalado: bool = True
    # A instalação do cliente VPN: a pasta muda a cada instalação ou atualização (o que invalida a prova de vazamento).
    instalacao: str = "inst1"
    versao: int = 739
    instalado_em: int | None = 1_700_000_000        # o APK gravado em 2023: anterior a qualquer teste deste arquivo
    relogio_atrasado_s: int = 0                     # quanto o relógio do aparelho está atrás do servidor
    # O tile de configurações rápidas do cliente (item 29.3): se o clique religa o túnel, se o SystemUI reinicia no
    # meio da preparação (o laço de ANR do boot) e quantas vezes o tile foi de fato clicado.
    tile_religa: bool = False
    sistema_instavel: bool = False
    tile_na_barra: bool = False
    cliques_no_tile: int = 0
    # O `serviceMode` do SFA 1.14.2 (W8, commit upstream fc21909): o TILE chama BoxService.start() com a classe do modo
    # SEM recalcular; o Start da UI chama rebuildServiceMode() (o perfil tem `tun` → VPN) e depois a classe. Com o modo
    # não-VPN (cliente que só importou o perfil: o android-09) o tile inicia o ProxyService, que aborta sem tun0.
    modo_vpn: bool = True
    # A interface: o Start religa (`ui_religa`), inicia o ProxyService mesmo assim (`ui_inicia_proxy`: hasTunInbound
    # falso), sobe o tun0 sem VPN CONNECTED (`ui_tun_sem_vpn`); o app não abre (`abre_app_falha`); o que a tela mostra
    # (`tela_ui`: start | stop | nada | outro_pacote | start_ambiguo | start_sem_conteiner | start_desabilitado).
    ui_religa: bool = False
    ui_inicia_proxy: bool = False
    ui_tun_sem_vpn: bool = False
    abre_app_falha: bool = False
    tela_ui: str = "start"
    app_aberto: bool = False
    foco: str = "launcher"
    toques_ui: list[tuple[int, int]] = field(default_factory=list)
    starts_na_ui: int = 0
    homes: int = 0
    fp: int = 0                                    # am_foreground_service_start do ProxyService (total)
    fv: int = 0                                    # ... do VPNService (total)
    servicos: str = ""
    # O locale do aparelho e os rótulos que o SFA mostra nele; o buffer `events` com HORA (a janela do Start, W8):
    # `eventos_fgs` = (hora, classe); `relogio_ap` anda a cada ida ao aparelho; `buffer_girou` = a entrada mais antiga
    # é posterior ao baseline; `janela_sem_leitura` = o comando da janela não devolve contagens; `ui_corrida` = o Start
    # inicia as DUAS classes; `ui_proxy_e_tun_por_fora` = o ProxyService do Start e um tun0 que aparece por outro caminho.
    locale: str = "en-US"
    rotulo_start: str = "Start"
    rotulo_stop: str = "Stop"
    eventos_fgs: list[tuple[str, str]] = field(default_factory=list)
    relogio_ap: int = 0
    buffer_girou: bool = False
    janela_sem_leitura: bool = False
    ui_corrida: bool = False
    appops: bool = False
    always_on: str = "null"
    lockdown: str = "0"
    tun: bool = False
    vpn: bool = False
    regras: bool = False
    # `am force-stop` do cliente: marca o estado `stopped` e (com `force_stop_derruba_tun`) derruba o túnel, como o Android.
    forcado: bool = False
    force_stop_derruba_tun: bool = False
    relatorios: int = 1
    uptime: int = 500
    primeira_execucao: bool = True
    tela: list[str] = field(default_factory=list)
    baixado: bytes | None = None
    baixar: bool = True
    dialogo_do_sistema: bool = False
    reversos: list[tuple[str, int]] = field(default_factory=list)
    toques: list[str] = field(default_factory=list)
    _link: str = ""

    async def shell(self, comando: str, *, timeout: float = 40) -> str:
        self.comandos.append(comando)
        if comando.startswith("echo U=$(id -u)"):
            return (f"U={self.uid}\nA={self.always_on}\nL={self.lockdown}\nT={int(self.tun)}\nV={int(self.vpn)}\n"
                    f"R={int(self.regras)}\nC={self.relatorios}\nP={int(self.instalado)}\nK={self.linha_do_cliente()}\n"
                    f"S={self.uptime}\n")
        if comando.startswith("echo F=$(dumpsys window"):                  # o estado da interface do cliente (W8)
            self.relogio_ap += 1
            return (f"F=mCurrentFocus=Window{{1 u0 {self.foco}}}\nT={int(self.tun)}\nSR={self.servicos}\n"
                    f"L1=\nL2=null\nL3={self.locale}\nBASE={self._hora()}\n")
        if comando.startswith("echo T=$(ip -o addr show tun0") and "OLD=" in comando:      # a janela do Start (W8)
            self.relogio_ap += 1
            base = comando.split("-T '", 1)[1].split("'", 1)[0]
            assert base.startswith("10-01 15:"), base                                        # o baseline do aparelho, não o do host
            if self.janela_sem_leitura:
                return f"T={int(self.tun)}\nOLD=\nEVP=\nEVV=\nSR={self.servicos}\n"
            antiga = "12-31 23:59:59.000" if self.buffer_girou else "01-01 00:00:00.000"      # o buffer vai bem antes do baseline
            ep = sum(1 for h, c in self.eventos_fgs if c == "ProxyService" and h >= base)
            ev = sum(1 for h, c in self.eventos_fgs if c == "VPNService" and h >= base)
            return f"T={int(self.tun)}\nOLD={antiga}\nEVP={ep}\nEVV={ev}\nSR={self.servicos}\n"
        if comando.startswith("F=$(dumpsys window"):                       # devolver o foco: HOME só se o foco é do cliente
            if PKG in self.foco:
                self.foco, self.homes = "launcher", self.homes + 1
            return ""
        if comando.startswith("am start -n "):
            if self.abre_app_falha:
                return "Error type 3\nError: Activity class {io.nekohasekai.sfa/.compose.MainActivity} does not exist.\n"
            self.app_aberto, self.foco = True, f"{PKG}/{PKG}.compose.MainActivity"
            return "Starting: Intent { cmp=io.nekohasekai.sfa/.compose.MainActivity }\n"
        if comando.startswith("Q0=$(settings get secure sysui_qs_tiles"):
            havia, tun = int(self.tile_na_barra), int(self.tun)
            clicou = not self.sistema_instavel and not self.tun
            self.tile_na_barra = not self.sistema_instavel
            if clicou:
                self.cliques_no_tile += 1
                if not self.modo_vpn:                       # o tile não recalcula o modo: ProxyService, sem tun0
                    self._fgs("ProxyService")
                elif self.tile_religa:
                    self.tun = self.vpn = True
                    self._fgs("VPNService")
                    if hasattr(self, "parado"):
                        self.parado = False
            return (f"Q0={havia}\nP1=900\nP2={901 if self.sistema_instavel else 900}\n"
                    f"Q={int(self.tile_na_barra)}\nT={tun}\nCLICOU={int(clicou)}\n")
        if comando.startswith("cmd statusbar remove-tile"):
            self.tile_na_barra = False
            return ""
        if comando.startswith("echo M=$(stat -c %Y "):
            return (f"M={'' if self.instalado_em is None else self.instalado_em}\n"
                    f"N={int(time.time()) - self.relogio_atrasado_s}\nU={self.uid}\n")
        if comando.startswith("echo A=$(settings get"):
            return f"A={self.always_on}\nL={self.lockdown}\n"
        if comando.startswith("pm path"):
            if self.instalado:
                return "package:/data/app/sfa/base.apk\n"
            if not comando.rstrip().endswith("; true"):
                # Como o Android: pacote ausente é código 1, e o shell da plataforma levanta (android-02, 29/09).
                raise RuntimeError("adb shell falhou (1)")
            return ""
        if comando.startswith("cmd appops set"):
            self.appops = True
        elif comando.startswith("cmd appops get"):
            return "ACTIVATE_VPN: allow; time=+1s\n" if self.appops else "ACTIVATE_VPN: ignore\n"
        elif comando.startswith("am start"):
            self.app_aberto = False                                   # a importação usa a árvore de `elementos`, não a da UI
            self._link = comando.split("-d '", 1)[1].rstrip("'")
            self.tela = ["No, thanks"] if self.primeira_execucao else ["OK"]
            return "Starting: Intent { act=android.intent.action.VIEW }\n"
        elif comando.startswith("settings put secure always_on_vpn_app "):
            self.always_on = comando.split()[-1]
        elif comando.startswith("settings put secure always_on_vpn_lockdown "):
            self.lockdown = comando.split()[-1]
        elif comando == "settings delete secure always_on_vpn_app":
            self.always_on = "null"
        elif comando.startswith("rm -rf /sdcard/Android/data/"):
            self.relatorios = 0
        elif comando.startswith("am force-stop "):
            self.forcado = True
            if self.force_stop_derruba_tun:
                self.tun = self.vpn = False
        elif comando.startswith("dumpsys package ") and "stopped=" in comando:
            return "stopped=true\n" if self.forcado else "stopped=false\n"
        return ""

    def linha_do_cliente(self) -> str:
        """O que o `dumpsys package` mostra do cliente, como o `echo` sem aspas entrega (uma linha só)."""
        if not self.instalado:
            return ""
        return (f"codePath=/data/app/~~{self.instalacao}==/{PKG}-{self.instalacao}== versionCode={self.versao} "
                "minSdk=32 targetSdk=37 versionName=1.14.2")

    @property
    def cliente(self) -> str:
        return f"1.14.2 ({self.versao}) /data/app/~~{self.instalacao}==/{PKG}-{self.instalacao}=="

    async def elementos(self) -> list[Elemento]:
        els = [Elemento(t, PKG, (100 + 10 * i, 900)) for i, t in enumerate(self.tela)]
        if self.dialogo_do_sistema:
            els.insert(0, Elemento("OK", "com.android.systemui", (5, 5)))
        return els

    async def arvore(self) -> list[NoDaTela]:
        if not self.app_aberto:
            return [NoDaTela("", "com.google.android.apps.nexuslauncher", (0, 0, 720, 1280), False)]
        quadro = NoDaTela("Dashboard", PKG, (32, 84, 247, 140), False)
        botao = {"start": (self.rotulo_start, True), "stop": (self.rotulo_stop, True)}
        if self.tela_ui in botao:
            texto, _ = botao[self.tela_ui]
            return [quadro, NoDaTela("", PKG, (576, 928, 688, 1040), True), NoDaTela(texto, PKG, (608, 960, 656, 1008), False)]
        if self.tela_ui == "outro_pacote":
            return [quadro, NoDaTela("", "com.android.systemui", (576, 928, 688, 1040), True),
                    NoDaTela("Start", "com.android.systemui", (608, 960, 656, 1008), False)]
        if self.tela_ui == "start_ambiguo":
            return [quadro, NoDaTela("", PKG, (0, 900, 720, 1100), True), NoDaTela("Start", PKG, (608, 960, 656, 1008), False),
                    NoDaTela("Start", PKG, (100, 960, 150, 1008), False)]
        if self.tela_ui == "start_sem_conteiner":
            return [quadro, NoDaTela("Start", PKG, (608, 960, 656, 1008), False)]
        if self.tela_ui == "start_desabilitado":
            return [quadro, NoDaTela("", PKG, (576, 928, 688, 1040), True, False), NoDaTela("Start", PKG, (608, 960, 656, 1008), False)]
        return [quadro]

    def _hora(self) -> str:
        return f"10-01 15:{self.relogio_ap // 60:02d}:{self.relogio_ap % 60:02d}.000"

    def _fgs(self, classe: str) -> None:
        """O ActivityManager iniciou `classe` em primeiro plano AGORA (um evento com a hora do aparelho)."""
        self.relogio_ap += 1
        self.eventos_fgs.append((self._hora(), classe))
        if classe == "ProxyService":
            self.fp += 1
        else:
            self.fv += 1

    def semear(self, classe: str, n: int = 1) -> None:
        """Eventos HISTÓRICOS no buffer (antes de qualquer baseline): o contador cumulativo os veria; a janela, não."""
        for _ in range(n):
            self._fgs(classe)

    def _start_da_ui(self) -> None:
        self.starts_na_ui += 1
        if self.tun:                                                    # o Start/Stop alterna: tocar com a VPN no ar a derruba
            self.tun = self.vpn = False
        elif self.ui_corrida:
            self._fgs("ProxyService")
            self._fgs("VPNService")
            self.modo_vpn, self.tun, self.vpn = True, self.ui_religa, self.ui_religa
        elif self.ui_inicia_proxy:
            self._fgs("ProxyService")
        elif self.ui_religa or self.ui_tun_sem_vpn:                     # rebuildServiceMode → VPN → VPNService
            self.modo_vpn = True
            self._fgs("VPNService")
            self.tun = True
            self.vpn = not self.ui_tun_sem_vpn
            self.servicos = f"{PKG}/.bg.VPNService,"
            if hasattr(self, "parado"):
                self.parado = False

    async def tocar(self, x: int, y: int) -> None:
        if self.app_aberto and self.tela_ui in ("start", "stop", "start_ambiguo", "start_sem_conteiner", "start_desabilitado"):
            self.toques_ui.append((x, y))
            assert PKG in self.foco, "tocou com o foco fora do cliente"
            if (x, y) == (632, 984) and self.tela_ui == "start":
                self._start_da_ui()
                return
            assert False, f"toque arbitrário na interface: {(x, y)}"
        alvo = next((e.texto for e in await self.elementos() if e.centro == (x, y)), None)
        assert alvo is not None and (x, y) != (5, 5), "tocou fora do cliente"
        self.toques.append(alvo)
        if alvo == "No, thanks":
            self.primeira_execucao, self.tela = False, ["OK"]
        elif alvo == "OK":
            self.tela = ["Create"]
        elif alvo == "Create":
            self.tela = []
            if self.baixar:
                url = urllib.parse.parse_qs(urllib.parse.urlsplit(self._link.split("#")[0]).query)["url"][0]
                if not self.external:
                    assert url.startswith("http://10.0.2.2:")
                    url = url.replace("10.0.2.2", "127.0.0.1")                  # o NAT do emulador faz isto
                else:
                    assert url.startswith("http://127.0.0.1:")
                self.baixado = (await asyncio.to_thread(_get, url))[1]

    async def reverso(self, porta: int) -> None:
        self.reversos.append(("reverse", porta))

    async def desfazer_reverso(self, porta: int) -> None:
        self.reversos.append(("remove", porta))

    def depois_do_boot(self, *, uptime: int = 30, tun: bool = True) -> None:
        self.uptime, self.tun, self.vpn = uptime, tun, tun
        self.regras = self.lockdown == "1"


def _indice(comandos: list[str], prefixo: str) -> int:
    return next(i for i, c in enumerate(comandos) if c.startswith(prefixo))


async def test_provisao_segue_a_receita_medida_e_a_chave_so_passa_pelo_http(cofre) -> None:
    db, secrets = cofre
    gerar_chave_wireguard(db, secrets, "android-05", kind="aparelho", address="10.66.0.2")
    ref = db.one("SELECT secret_ref FROM network_keys WHERE owner='android-05'")["secret_ref"]
    chave = secrets.get_secret(ref)
    ap = AparelhoFalso()
    evidencia = await provisionar(ap, db, secrets, _plano(), RedeCfg(), pausa_s=0.01)
    c = ap.comandos
    assert ap.toques == ["No, thanks", "OK", "Create"]
    assert _indice(c, f"cmd appops set {PKG} ACTIVATE_VPN allow") < _indice(c, f"am force-stop {PKG}") \
        < _indice(c, "am start") < c.index("sync") < _indice(c, "settings put secure always_on_vpn_app") \
        < _indice(c, "rm -rf /sdcard/Android/data/io.nekohasekai.sfa/files/crash_reports") and c[-1] == "sync"
    assert f"settings put secure always_on_vpn_app {PKG}" in c and "settings put secure always_on_vpn_lockdown 1" in c
    assert "sing-box://import-remote-profile?url=http%3A%2F%2F10.0.2.2%3A" in next(x for x in c if x.startswith("am start"))
    perfil = json.loads(ap.baixado or b"{}")
    assert perfil["endpoints"][0]["private_key"] == chave                        # chegou, e só por aqui
    assert all(chave not in x for x in c) and chave not in evidencia
    assert "http://" not in evidencia and "10.0.2.2" in evidencia and "lockdown=1" in evidencia
    assert ap.always_on == PKG and ap.relatorios == 0


async def test_aparelho_do_worker_recebe_pelo_adb_reverse_e_o_mapa_sai(cofre) -> None:
    db, secrets = cofre
    gerar_chave_wireguard(db, secrets, "android-05", kind="aparelho", address="10.66.0.2")
    ap = AparelhoFalso(external=True, primeira_execucao=False)
    await provisionar(ap, db, secrets, _plano(policy="exigida"), RedeCfg(), pausa_s=0.01)
    [(r1, p1), (r2, p2)] = ap.reversos
    assert (r1, r2) == ("reverse", "remove") and p1 == p2 and ap.baixado
    assert ap.toques == ["OK", "Create"] and "settings put secure always_on_vpn_lockdown 0" in ap.comandos


async def test_toque_so_no_pacote_do_cliente_e_sem_download_falha(cofre) -> None:
    db, secrets = cofre
    gerar_chave_wireguard(db, secrets, "android-05", kind="aparelho", address="10.66.0.2")
    ap = AparelhoFalso(dialogo_do_sistema=True, baixar=False)
    with pytest.raises(RedeAplicacaoError, match="não baixou o perfil"):
        await provisionar(ap, db, secrets, _plano(), RedeCfg(), pausa_s=0.01, prazo_do_download_s=0.3)
    assert "Create" in ap.toques and "settings put secure always_on_vpn_app io.nekohasekai.sfa" not in ap.comandos
    # A tela que nunca chega ao "Create" também para, com o que se viu nela.
    parado = AparelhoFalso()
    parado.tela = ["Loading"]

    async def sem_tela() -> list[Elemento]:
        return [Elemento("Loading", PKG, (1, 1))]

    parado.elementos = sem_tela                                                    # type: ignore[method-assign]
    with pytest.raises(RedeAplicacaoError, match="na tela: Loading"):
        await tocar_importacao(parado, PKG, prazo_s=0.2, pausa_s=0.01)


async def test_desfazer_e_a_leitura_como_uid_2000() -> None:
    ap = AparelhoFalso(always_on=PKG, lockdown="1")
    evidencia = await desfazer(ap, RedeCfg())
    assert ap.always_on == "null" and ap.lockdown == "0" and f"am force-stop {PKG}" in ap.comandos
    assert ap.comandos[-2:] == ["sync", "echo A=$(settings get secure always_on_vpn_app); "
                                        "echo L=$(settings get secure always_on_vpn_lockdown)"]
    assert "tirados" in evidencia
    assert "id -u" in comando_de_observacao(PKG) and "ni[{]VPN CONNECTED" in comando_de_observacao(PKG)
    obs = ler_observacao("U=2000\nA=io.nekohasekai.sfa\nL=1\nT=1\nV=1\nR=1\nC=0\nP=1\n"
                         "K=codePath=/data/app/~~AbC==/io.nekohasekai.sfa-XyZ== versionCode=739 minSdk=32 targetSdk=37 "
                         "versionName=1.14.2\nS=42\n")
    # A instalação do cliente: versão e pasta (sorteada a cada instalação). É a isto que a prova de vazamento se prende.
    assert obs.cliente == "1.14.2 (739) /data/app/~~AbC==/io.nekohasekai.sfa-XyZ=="
    assert "dumpsys package io.nekohasekai.sfa" in comando_de_observacao(PKG)
    # Instalado e sem identidade legível é leitura incompleta (repete depois), nunca "cliente desconhecido".
    with pytest.raises(RedeAplicacaoError, match="incompleta"):
        ler_observacao("U=2000\nA=io.nekohasekai.sfa\nL=1\nT=1\nV=1\nR=1\nC=0\nP=1\nK=\nS=42\n")
    assert ler_observacao("U=2000\nA=null\nL=0\nT=0\nV=0\nR=0\nC=0\nP=0\nK=\nS=42\n").cliente == ""
    assert obs.configuracao_ok(PKG, True) and obs.conectada(True) and obs.uptime_s == 42
    root = AparelhoFalso(uid=0)
    with pytest.raises(RedeAplicacaoError, match="uid 0"):
        await observar(root, PKG)
    with pytest.raises(RedeAplicacaoError, match="incompleta"):
        ler_observacao("U=2000\nA=null\n")


# ============================================================================ a sincronização antes do desligamento
def test_stop_process_sincroniza_antes_do_emu_kill() -> None:
    ordem: list[str] = []

    class AdbFalso:
        def shell(self, comando: str, *, timeout: float = 30) -> str:
            ordem.append(f"shell {comando}")
            return ""

        def emu_kill(self) -> None:
            ordem.append("emu kill")

    assert "não iniciado por este projeto" in emu.stop_process(AdbFalso(), None, "avd-x")        # type: ignore[arg-type]
    assert ordem == ["shell sync", "emu kill"]

    class AdbTravado(AdbFalso):
        def shell(self, comando: str, *, timeout: float = 30) -> str:
            ordem.append("shell travou")
            raise TimeoutError("adb shell excedeu")

    ordem.clear()
    emu.stop_process(AdbTravado(), None, "avd-x")                                  # type: ignore[arg-type]
    assert ordem == ["shell travou", "emu kill"]                                  # o desligamento segue


# ============================================================================ convergência (harness)
@pytest_asyncio.fixture
async def parque(tmp_path: Path) -> Iterator[Harness]:
    """android-01..03 de tarefa; android-04 é a loja. Servidor e aparelho de rede trocados por dublês."""
    h = Harness(tmp_path, 4, store="android-04")
    await h.boot()
    st = h.state
    assert st is not None
    binario = tmp_path / "sing-box.exe"
    binario.write_bytes(b"")
    st.rede_servidor.processos = ProcessosFalsos()
    st.rede_servidor._binario = lambda: binario
    st.rede_servidor.restringir = lambda p: None
    st.rede_servidor.espera_de_subida_s = 0
    st.rede_convergencia.atraso_do_reinicio_s = 0.05
    st.rede_convergencia.intervalo_do_reinicio_s = 0.05
    st.rede_convergencia.pausa_da_tela_s = 0.01
    st.rede_convergencia.espera_da_interface_s = 0
    st.rede_convergencia.prazo_do_botao_s = 0.05
    st.rede_convergencia.pausa_da_interface_s = 0.01
    try:
        yield h
    finally:
        if h.state is not None:
            await h.state.stop()


@dataclass
class Reinicios:
    pedidos: list[tuple[str, str]] = field(default_factory=list)
    aceitar: bool = True

    def __call__(self, s, instance_id: str, verb: str, motivo: str, *, requested_by: str, **_kw) -> str | None:
        assert verb == "restart" and requested_by == "rede"
        self.pedidos.append((instance_id, motivo))
        return f"c-teste-{len(self.pedidos)}" if self.aceitar else None


def _preparar(parque: Harness, monkeypatch: pytest.MonkeyPatch, *, policy: str = "exigida_com_bloqueio",
              iid: str = "android-01") -> tuple[AparelhoFalso, Reinicios, str]:
    st = parque.state
    assert st is not None
    ap = AparelhoFalso(id=iid)
    st.rede_convergencia._aparelho = lambda _s, _rt: ap
    reinicios = Reinicios()
    monkeypatch.setattr(despacho, "pedir_ciclo_de_vida", reinicios)
    perfil = rede.criar_perfil(st, rede.ler_cadastro({"name": "Central", "kind": "vpn", "protocol": "singbox",
                                                      "endpoint_host": "10.0.2.2", "endpoint_port": 51820,
                                                      "params": {"servidor": "central"}}), "teste")
    rede.atribuir(st, rede.NetworkAssignBody(instance_ids=[iid], vpn_profile_id=perfil.id, policy=policy), "teste")
    return ap, reinicios, perfil.id


def _linha(parque: Harness, iid: str = "android-01") -> dict[str, object] | None:
    r = parque.state.db.one("SELECT * FROM device_network WHERE instance_id=?", (iid,))  # type: ignore[union-attr]
    return dict(r) if r is not None else None


async def _passo(parque: Harness, motivo: str = "ligou", iid: str = "android-01") -> bool:
    st = parque.state
    assert st is not None
    trabalho = st.rede_convergencia.trabalho(st.devices.devices[iid], motivo=motivo)            # type: ignore[arg-type]
    if trabalho is None:
        return False
    await trabalho()
    return True


def _envelhecer_configuracao(parque: Harness, iid: str = "android-01", s: float = 600) -> None:
    parque.state.rede_convergencia.memoria(iid).configurado_em = time.time() - s  # type: ignore[union-attr]


async def test_convergencia_aplica_pede_reinicio_e_conecta(parque: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    st = parque.state
    assert st is not None and st.devices.devices["android-01"].state.value == "online"
    ap, reinicios, _ = _preparar(parque, monkeypatch)
    # Política exigida: a porta segura a tarefa e dispara a aplicação antes dela.
    assert "aplicando a rede pedida (rev 1)" in (st.rede_convergencia.motivo_de_espera("android-01") or "")
    await parque.wait(lambda: (_linha(parque) or {}).get("state") == "configurado", what="rede aplicada pela porta")
    await asyncio.sleep(0.3)                                                       # o reinício sai depois do trabalho
    assert [i for i, _ in reinicios.pedidos] == ["android-01"]
    linha = _linha(parque)
    assert linha["applied_rev"] == 1 and "reinício c-teste-1" in str(linha["detail"])
    [cmd] = st.db.query("SELECT * FROM commands WHERE verb='device.network'")
    assert cmd["state"] == "succeeded" and json.loads(cmd["params"])["acao"] == "aplicar"
    assert cmd["requested_by"] == "rede"
    servidor = st.rede_servidor.status()
    assert servidor["running"] is True and [p["instance_id"] for p in servidor["peers"]] == ["android-01"]
    assert "reinício" in (st.rede_convergencia.motivo_de_espera("android-01") or "")
    # O boot com o always-on: túnel no ar, lido como uid 2000 → conectado. Ainda não libera: falta a medição.
    _envelhecer_configuracao(parque)
    ap.depois_do_boot(uptime=45)
    assert await _passo(parque, "ligou")
    linha = _linha(parque)
    assert linha["state"] == "conectado" and "uid 2000" in str(linha["detail"])
    assert "medição do tráfego" in (st.rede_convergencia.motivo_de_espera("android-01") or "")
    # A primeira medição da conexão sai na próxima varredura, não depois da deriva (android-05, 29/09: 16 min parado).
    assert st.rede_convergencia._acao(_linha(parque), "varredura") == "verificar"
    # Com bloqueio, a medição sozinha não libera: falta a prova de vazamento da linha (o teste com o cliente parado).
    medicao = rede.NetworkMeasurementInput(method="app_qa", egress_ipv4="45.162.8.9",
                                           per_app={"com.pocqa.messenger": "ok"}, leak_blocked=True)
    rede.registrar_medicao(st, "android-01", medicao, rev=1)
    assert _linha(parque)["state"] == "parcial" and st.rede_convergencia.motivo_de_espera("android-01") is not None
    assert rede.marcar_ensaio_de_vazamento(st, "android-01", rev=1, cliente=ap.cliente)
    assert rede.gravar_prova_de_vazamento(st, "android-01", rev=1, cliente=ap.cliente, resultado=True,
                                          quando=now_iso(), detalhe="Permission denied")
    rede.registrar_medicao(st, "android-01", medicao, rev=1)
    assert _linha(parque)["state"] == "trafego_verificado"
    assert st.rede_convergencia.motivo_de_espera("android-01") is None
    # Nenhum segredo nas tabelas de texto, nos comandos nem nos eventos.
    ref = st.db.one("SELECT secret_ref FROM network_keys WHERE owner='android-01'")["secret_ref"]
    chave = st.secrets.get_secret(ref)
    for tabela in ("device_network", "commands", "events", "network_keys"):
        for r in st.db.query(f"SELECT * FROM {tabela}"):                          # noqa: S608 - nome fixo
            assert chave not in json.dumps(dict(r), default=str), tabela


async def test_tunel_que_nao_sobe_reinicia_ate_o_teto_e_depois_espera(parque: Harness,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    st = parque.state
    assert st is not None
    ap, reinicios, _ = _preparar(parque, monkeypatch, policy="exigida")
    assert await _passo(parque, "varredura")                                       # aplicar
    await asyncio.sleep(0.3)
    for n in (2, 3):
        _envelhecer_configuracao(parque)
        ap.depois_do_boot(uptime=300, tun=False)                                   # reiniciou e o SFA caiu (FGS)
        ap.relatorios = 1
        assert await _passo(parque, "ligou")
        assert ap.relatorios == 0                                                  # a queda grava a chave ali
        if n == 2:
            await asyncio.sleep(0.3)
            assert len(reinicios.pedidos) == 2 and _linha(parque)["state"] == "configurado"
    linha = _linha(parque)
    assert linha["state"] == "pendente" and "o túnel não subiu depois de 2 reinício" in str(linha["error"])
    # Antes de cada reinício (e de desistir), o Start da interface do cliente foi tentado, UMA vez por passada — e aqui não
    # religa (W8). O tile nunca é chamado: o motivo conhecido da interface fica na evidência, sem loop novo.
    assert ap.starts_na_ui == 2 and ap.cliques_no_tile == 0 and ap.tile_na_barra is False
    assert "religar pela interface: tun_nao_subiu" in str(linha["error"]) and ap.foco == "launcher" and ap.homes == 2
    assert await _passo(parque, "varredura") is False                             # espera antes de repetir
    assert "falhou" in (st.rede_convergencia.motivo_de_espera("android-01") or "")
    [ultimo] = st.db.query("SELECT * FROM commands WHERE verb='device.network' ORDER BY created_at DESC LIMIT 1")
    assert ultimo["state"] == "failed"


async def test_tunel_que_nao_sobe_no_boot_e_religado_pelo_start_da_interface_sem_outro_reinicio(
        parque: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """Item 29.3 + W8. Em 30/09 a primeira tentativa do always-on falhou em 5 de 7 boots (ANR de início do serviço com o
    convidado sem CPU), e cada conferência sem `tun0` pedia OUTRO reinício — o mesmo dado, rolado de novo (android-06:
    6 reinícios e 2 reaplicações). O Start da interface do cliente religa sem reinício, com always-on e bloqueio
    intactos, MESMO com o `serviceMode` do cliente em NORMAL (o caso real do android-09: o tile iniciaria o ProxyService)."""
    st = parque.state
    assert st is not None
    ap, reinicios, _ = _preparar(parque, monkeypatch)
    ap.modo_vpn = False                                                            # o stale do 09: nunca um Start da UI
    assert await _passo(parque, "varredura")                                       # aplicar
    await asyncio.sleep(0.3)
    assert len(reinicios.pedidos) == 1
    # Sem boot desde a configuração, o bloqueio ainda não vale no sistema: a UI NÃO é tocada, o caminho é o reinício.
    ap.ui_religa = True
    assert await _passo(parque, "varredura")
    await asyncio.sleep(0.3)
    assert ap.starts_na_ui == 0 and not ap.app_aberto and _linha(parque)["state"] == "configurado"
    pedidos = len(reinicios.pedidos)
    # O boot veio e o cliente não subiu (a configuração e as regras de bloqueio estão no lugar; só o túnel falta).
    _envelhecer_configuracao(parque)
    ap.depois_do_boot(uptime=300, tun=False)
    comandos = len(ap.comandos)
    assert await _passo(parque, "ligou")
    await asyncio.sleep(0.3)
    linha = _linha(parque)
    assert linha["state"] == "conectado" and "religado pelo Start da interface do cliente, sem reinício" in str(linha["detail"])
    assert ap.starts_na_ui == 1 and ap.cliques_no_tile == 0 and len(reinicios.pedidos) == pedidos   # nenhum reinício a mais
    assert ap.modo_vpn and ap.fv == 1 and ap.fp == 0                               # o caminho da UI recalculou o modo
    # O gesto não mexe na configuração: always-on, bloqueio e regras como estavam; o foco volta ao launcher.
    assert ap.always_on == PKG and ap.lockdown == "1" and ap.regras is True and ap.foco == "launcher" and ap.homes == 1
    assert not any(c.startswith("settings put") or c.startswith("settings delete") for c in ap.comandos[comandos:])
    assert not any("statusbar" in c or "Q0=$(settings get secure sysui_qs_tiles" in c for c in ap.comandos[comandos:])
    # Com o túnel no ar a UI nunca é tocada (Start/Stop alterna: desligaria a VPN).
    assert await _passo(parque, "ligou") is True and ap.starts_na_ui == 1


async def test_start_que_inicia_o_proxyservice_nao_e_recuperacao_e_o_reinicio_segue(
        parque: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """Guard D (W8): num plano com VPN/TUN, o Start que iniciou o ProxyService é incompatibilidade de classe/modo, não
    "o cliente começou". A linha fica `configurado` (não saudável), a razão vai na evidência e o reinício de sempre
    continua valendo, sem fallback para o tile e sem laço novo."""
    st = parque.state
    assert st is not None
    ap, reinicios, _ = _preparar(parque, monkeypatch)
    assert await _passo(parque, "varredura")
    await asyncio.sleep(0.3)
    _envelhecer_configuracao(parque)
    ap.depois_do_boot(uptime=300, tun=False)
    ap.ui_inicia_proxy, ap.tile_religa = True, True
    assert await _passo(parque, "ligou")
    await asyncio.sleep(0.3)
    linha = _linha(parque)
    assert linha["state"] == "configurado" and "wrong_service_class_for_tun" in str(linha["detail"])
    assert ap.starts_na_ui == 1 and ap.fp == 1 and ap.cliques_no_tile == 0 and not ap.tun
    assert len(reinicios.pedidos) == 2 and "wrong_service_class_for_tun" in str(reinicios.pedidos[-1][1])


async def test_app_que_nao_abre_nao_cai_cegamente_no_tile_e_o_reinicio_segue(parque: Harness,
                                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    st = parque.state
    assert st is not None
    ap, reinicios, _ = _preparar(parque, monkeypatch)
    assert await _passo(parque, "varredura")
    await asyncio.sleep(0.3)
    _envelhecer_configuracao(parque)
    ap.depois_do_boot(uptime=300, tun=False)
    ap.abre_app_falha, ap.tile_religa = True, True
    assert await _passo(parque, "ligou")
    await asyncio.sleep(0.3)
    assert ap.starts_na_ui == 0 and ap.cliques_no_tile == 0 and _linha(parque)["state"] == "configurado"
    assert "abertura_falhou" in str(_linha(parque)["detail"]) and len(reinicios.pedidos) == 2


async def test_gesto_desligado_pela_configuracao_nao_toca_na_interface_e_o_reinicio_segue(
        parque: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    st = parque.state
    assert st is not None
    st.rede_convergencia.cfg.cliente_atividade = ""                                # `rede.cliente_atividade` vazio desliga
    ap, reinicios, _ = _preparar(parque, monkeypatch)
    ap.ui_religa = True
    assert await _passo(parque, "varredura")
    await asyncio.sleep(0.3)
    _envelhecer_configuracao(parque)
    ap.depois_do_boot(uptime=300, tun=False)
    assert await _passo(parque, "ligou")
    await asyncio.sleep(0.3)
    assert not ap.app_aberto and ap.starts_na_ui == 0 and ap.cliques_no_tile == 0
    assert _linha(parque)["state"] == "configurado" and len(reinicios.pedidos) == 2


def test_comando_do_tile_so_clica_sem_tun0_com_o_systemui_estavel_e_o_tile_na_barra() -> None:
    from app.devices.rede_aplicacao import comando_de_religar

    cmd = comando_de_religar("io.nekohasekai.sfa/.bg.TileService")
    assert cmd.index("remove-tile") < cmd.index("add-tile") < cmd.index("click-tile")   # tile velho não responde
    assert '[ "$P1" = "$P2" ]' in cmd and '[ "$T" -eq 0 ]' in cmd and '[ "$Q" -gt 0 ]' in cmd
    assert "always_on_vpn" not in cmd and "settings put" not in cmd                      # só LÊ a barra
    for ruim in ("", "io.nekohasekai.sfa", "pkg/.Tile; reboot", "pkg/$(id)"):
        with pytest.raises(RedeAplicacaoError):
            comando_de_religar(ruim)


async def test_falha_de_aplicacao_nao_se_repete_as_cegas(parque: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    st = parque.state
    assert st is not None
    ap, reinicios, _ = _preparar(parque, monkeypatch, policy="livre")
    ap.instalado = False                                                           # sem SFA e sem versão promovida
    assert await _passo(parque, "varredura")
    linha = _linha(parque)
    assert linha["state"] == "pendente" and "25.10" in str(linha["error"]) and reinicios.pedidos == []
    assert await _passo(parque, "varredura") is False and await _passo(parque, "ligou") is False
    assert await _passo(parque, "pedido")                                          # "Aplicar agora" passa por cima
    # Com política livre, a tarefa não depende da rede: a porta não segura nada.
    assert st.rede_convergencia.motivo_de_espera("android-01") is None


async def test_deriva_regride_e_wipe_invalida(parque: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    st = parque.state
    assert st is not None
    ap, _, _ = _preparar(parque, monkeypatch)
    assert await _passo(parque, "varredura")
    _envelhecer_configuracao(parque)
    ap.depois_do_boot(uptime=45)
    assert await _passo(parque, "ligou") and _linha(parque)["state"] == "conectado"
    # A primeira medição da conexão é pedida ao conectar (coberta em test_convergencia_aplica_pede_reinicio_e_conecta);
    # aqui o assunto é a deriva, então a marca é consumida como se a medição já tivesse saído.
    assert st.rede_convergencia._acao(_linha(parque), "varredura") == "verificar"
    st.rede_convergencia.memoria("android-01").verificacao_pedida = False
    # Varredura antes de vencer a deriva: nada a fazer; vencida, relê.
    assert await _passo(parque, "varredura") is False
    st.cfg.file.rede.deriva_s = 0.01
    await asyncio.sleep(0.02)
    ap.tun = ap.vpn = False                                                        # o túnel caiu, a config ficou
    ap.uptime = 5000                                                               # (longe do boot: sem esperar o tun0)
    assert await _passo(parque, "varredura")
    assert _linha(parque)["state"] == "configurado" and "túnel caído" in str(_linha(parque)["detail"])
    ap.depois_do_boot(uptime=45)
    _envelhecer_configuracao(parque)
    assert await _passo(parque, "ligou") and _linha(parque)["state"] == "conectado"
    ap.always_on = "null"                                                          # alguém tirou o always-on
    assert await _passo(parque, "ligou") and _linha(parque)["state"] == "pendente"
    # Wipe: a rede aplicada deixou de existir, e a linha regride (o gancho do gerenciador chega aqui).
    rede.registrar_observacao(st, "android-01", rev=1, estado="conectado", evidencia="teste")
    st.devices.on_device_wiped("android-01", "reset do aparelho")
    linha = _linha(parque)
    assert linha["state"] == "pendente" and "dados do aparelho apagados" in str(linha["detail"])


async def test_desfazer_espera_o_stopped_persistir_e_le_o_estado(monkeypatch: pytest.MonkeyPatch) -> None:
    """W8 r2/r3: o force-stop só vale no boot se o estado stopped chegou ao disco; o desfazer espera (rede.parada_persistir_s) e lê."""
    ap = AparelhoFalso(always_on=PKG, lockdown="1")
    esperas: list[float] = []
    original = asyncio.sleep

    async def _sleep(s: float, *a, **k):
        esperas.append(s)
        return await original(0, *a, **k)

    import app.devices.rede_aplicacao as mod
    mod.asyncio.sleep = _sleep                                                    # type: ignore[assignment]
    monkeypatch.setattr(mod, "PARADA_PERSISTIR_S", 10.0)
    try:
        evidencia = await desfazer(ap, RedeCfg())
    finally:
        mod.asyncio.sleep = original                                              # type: ignore[assignment]
    assert esperas == [10.0], "esperou ~10 s depois do force-stop e antes de devolver (o reinício vem depois)"
    assert any(c.startswith(f"dumpsys package {PKG}") and "stopped=" in c for c in ap.comandos)
    assert ap.comandos.index(f"am force-stop {PKG}") < len(ap.comandos) - 1
    assert "(stopped=true)" in evidencia
    # Desligada (0): nenhuma espera nem leitura (o comportamento antigo).
    ap2 = AparelhoFalso(always_on=PKG, lockdown="1")
    monkeypatch.setattr(mod, "PARADA_PERSISTIR_S", 0.0)
    assert "stopped=" not in await desfazer(ap2, RedeCfg())


def test_cliente_solto_so_com_a_rede_tirada_e_o_tun_no_ar() -> None:
    solto = ler_observacao("U=2000\nA=null\nL=0\nT=1\nV=1\nR=0\nC=0\nP=0\nK=\nS=42\n")
    assert solto.cliente_solto() and not solto.removida()
    assert not ler_observacao("U=2000\nA=null\nL=0\nT=0\nV=0\nR=0\nC=0\nP=0\nK=\nS=42\n").cliente_solto()       # removida
    assert not ler_observacao("U=2000\nA=io.nekohasekai.sfa\nL=0\nT=1\nV=1\nR=0\nC=0\nP=0\nK=\nS=42\n").cliente_solto()  # always-on ainda no lugar
    assert not ler_observacao("U=2000\nA=null\nL=1\nT=1\nV=1\nR=1\nC=0\nP=0\nK=\nS=42\n").cliente_solto()       # bloqueio ainda em vigor


async def test_desfazer_tira_a_rede_e_apaga_a_linha(parque: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    st = parque.state
    assert st is not None
    ap, reinicios, _ = _preparar(parque, monkeypatch)
    assert await _passo(parque, "varredura")
    _envelhecer_configuracao(parque)
    ap.depois_do_boot(uptime=45)
    assert await _passo(parque, "ligou") and _linha(parque)["state"] == "conectado"
    await asyncio.sleep(0.3)
    antes = len(reinicios.pedidos)
    rede.atribuir(st, rede.NetworkAssignBody(instance_ids=["android-01"], vpn_profile_id=None, policy="livre"), "teste")
    assert _linha(parque)["desired_rev"] == 2
    assert await _passo(parque, "varredura")                                       # desfazer
    assert ap.always_on == "null" and ap.lockdown == "0" and _linha(parque)["state"] == "configurado"
    assert st.rede_servidor.status()["running"] is False                          # o par saiu do servidor
    await asyncio.sleep(0.3)
    assert len(reinicios.pedidos) == antes + 1
    _envelhecer_configuracao(parque)
    ap.depois_do_boot(uptime=45, tun=False)
    ap.regras = False
    assert await _passo(parque, "ligou")
    assert _linha(parque) is None                                                  # nada pedido, nada aplicado
    acoes = [json.loads(c["params"])["acao"] for c in st.db.query(
        "SELECT params FROM commands WHERE verb='device.network' ORDER BY created_at")]
    assert acoes == ["aplicar", "conectar", "desfazer", "conectar"]


async def test_cliente_que_religou_sozinho_depois_do_desfazer_e_parado_sem_novo_reinicio(parque: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """W8 r2 (02/10): depois de tirar a rede o SFA religou sozinho no boot (tun0 para um par que saiu) e a convergência pedia MAIS um reinício
    (que o religaria de novo). Agora: force-stop do cliente, sem reinício, e a linha sai."""
    st = parque.state
    assert st is not None
    ap, reinicios, _ = _preparar(parque, monkeypatch)
    assert await _passo(parque, "varredura")
    _envelhecer_configuracao(parque)
    ap.depois_do_boot(uptime=45)
    assert await _passo(parque, "ligou") and _linha(parque)["state"] == "conectado"
    await asyncio.sleep(0.3)
    rede.atribuir(st, rede.NetworkAssignBody(instance_ids=["android-01"], vpn_profile_id=None, policy="livre"), "teste")
    assert await _passo(parque, "varredura")                                       # desfazer
    await asyncio.sleep(0.3)
    antes = len(reinicios.pedidos)
    _envelhecer_configuracao(parque)
    ap.depois_do_boot(uptime=45, tun=True)                                         # o cliente religou sozinho no boot
    ap.regras = False
    ap.force_stop_derruba_tun = True
    ap.forcado = False
    assert await _passo(parque, "ligou")
    await asyncio.sleep(0.3)
    assert ap.forcado and not ap.tun, "o cliente foi parado"
    assert len(reinicios.pedidos) == antes, "nenhum reinício extra por tun0 sem par"
    assert _linha(parque) is None


async def test_cliente_que_nao_para_com_o_force_stop_ainda_cai_no_reinicio_ate_o_teto(parque: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """Rede de segurança: se o force-stop não derrubou o túnel, o caminho de antes (reiniciar até o teto) continua valendo."""
    st = parque.state
    assert st is not None
    ap, reinicios, _ = _preparar(parque, monkeypatch)
    assert await _passo(parque, "varredura")
    _envelhecer_configuracao(parque)
    ap.depois_do_boot(uptime=45)
    assert await _passo(parque, "ligou")
    await asyncio.sleep(0.3)
    rede.atribuir(st, rede.NetworkAssignBody(instance_ids=["android-01"], vpn_profile_id=None, policy="livre"), "teste")
    assert await _passo(parque, "varredura")
    await asyncio.sleep(0.3)
    antes = len(reinicios.pedidos)
    _envelhecer_configuracao(parque)
    ap.depois_do_boot(uptime=45, tun=True)
    ap.regras = False
    ap.force_stop_derruba_tun = False                                              # o force-stop não derruba o túnel
    await _passo(parque, "ligou")
    await asyncio.sleep(0.3)
    assert len(reinicios.pedidos) == antes + 1
    assert _linha(parque) is not None


async def test_loja_quarentena_e_ocupado_ficam_fora(parque: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    st = parque.state
    assert st is not None
    _preparar(parque, monkeypatch, iid="android-02")
    st.social_repo.marcar_conta_travada("android-02", "felipe.teste", "tela de desafio", "declarado", visto_por="dono")
    assert st.rede_convergencia.trabalho(st.devices.devices["android-02"], motivo="pedido") is None
    assert st.rede_convergencia.motivo_de_espera("android-02") is None           # a quarentena tem a porta dela
    with pytest.raises(rede.RedeError) as exc:
        st.rede_convergencia.aplicar_agora("android-02", "teste")
    assert exc.value.code == "aparelho_em_quarentena"
    with pytest.raises(rede.RedeError) as exc:
        st.rede_convergencia.aplicar_agora("android-04", "teste")
    assert exc.value.code == "store_instance"
    assert st.db.one("SELECT id FROM commands WHERE verb='device.network'") is None


async def test_rotas_apply_e_server_sem_segredo(parque: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    st = parque.state
    assert st is not None
    ap, _, _ = _preparar(parque, monkeypatch, policy="livre")
    app = create_app(parque.cfg, state=st)
    app.state.poc = st
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/network/devices/android-01/apply")
        assert r.status_code == 202 and r.json()["executed"] is True, r.text
        await parque.wait(lambda: (_linha(parque) or {}).get("state") == "configurado", what="apply pela rota")
        assert (await c.post("/api/network/devices/android-03/apply")).json()["detail"]["code"] == "nothing_requested"
        r = await c.get("/api/network/server")
        assert r.status_code == 200 and r.json()["running"] is True
        assert [p["address"] for p in r.json()["peers"]] == ["10.66.0.2"]
    corpo = r.text
    for dono in ("android-01", "servidor"):
        ref = st.db.one("SELECT secret_ref FROM network_keys WHERE owner=?", (dono,))["secret_ref"]
        assert st.secrets.get_secret(ref) not in corpo


async def test_reinicio_so_no_ponto_seguro_e_recusa_nao_vira_comando_a_cada_passada(
        parque: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.models import ControlOwner

    st = parque.state
    assert st is not None
    ap, reinicios, _ = _preparar(parque, monkeypatch, policy="livre")
    rt = st.devices.devices["android-01"]
    rt.control = ControlOwner.user                                                 # uma pessoa pegou o aparelho
    try:
        assert await _passo(parque, "pedido")                                      # aplicar (a pedido)
        await asyncio.sleep(0.3)
        assert reinicios.pedidos == []                                             # ocupado: não reinicia por baixo
    finally:
        rt.control = ControlOwner.none
    await asyncio.sleep(0.3)
    assert [i for i, _ in reinicios.pedidos] == ["android-01"]                     # livre: agora sim
    # Reinício recusado (o worker não tem o verbo, manutenção): a varredura não abre um comando por passada.
    reinicios.aceitar = False
    assert await _passo(parque, "ligou")                                           # sem boot: pede de novo
    await asyncio.sleep(0.3)
    assert len(reinicios.pedidos) == 2
    comandos = st.db.scalar("SELECT COUNT(*) FROM commands WHERE verb='device.network'")
    assert await _passo(parque, "varredura") is False and await _passo(parque, "tarefa") is False
    assert st.db.scalar("SELECT COUNT(*) FROM commands WHERE verb='device.network'") == comandos


@pytest.mark.parametrize("uptime", [5000, None], ids=["boot_nunca_zera", "uptime_nao_lido"])
async def test_reinicio_sem_boot_detectado_tem_teto(parque: Harness, monkeypatch: pytest.MonkeyPatch,
                                                    uptime: int | None) -> None:
    """Revisão (25.4): o teto só contava reinício com boot DETECTADO. O celular sem worker (o `restart` só solta e
    readota a sessão: o uptime nunca zera) ou uma leitura sem `S=` pediam um reinício novo a cada passada, para
    sempre. Agora o teto conta os pedidos, e a linha vai a `pendente` com o porquê."""
    st = parque.state
    assert st is not None
    ap, reinicios, _ = _preparar(parque, monkeypatch, policy="livre")
    ap.external = True
    conv = st.rede_convergencia
    agora = [1_000_000.0]
    conv._agora = lambda: agora[0]
    assert await _passo(parque, "varredura")                                       # aplicar: 1º reinício pedido
    await asyncio.sleep(0.3)
    ap.uptime = uptime                                                             # type: ignore[assignment]
    for _ in range(8):                                                             # o "reinício" que nunca reinicia
        if _linha(parque)["state"] == "pendente":
            break
        agora[0] += 301                                                            # passou a espera da releitura
        await _passo(parque, "varredura")
        await asyncio.sleep(0.2)
    # Antes: um pedido novo a cada passada, sem fim. Agora: o teto, e a linha desiste com o porquê.
    maximo = int(st.cfg.file.rede.reinicios_max)
    assert len(reinicios.pedidos) == maximo
    linha = _linha(parque)
    assert linha["state"] == "pendente" and "nenhum boot foi detectado" in str(linha["error"])
    assert "reinicie-o por fora" in str(linha["error"])
    # Desistiu: a espera crescente das falhas vale (5, 15, 45, 60 min), e até ela a varredura não abre nada.
    comandos = st.db.scalar("SELECT COUNT(*) FROM commands WHERE verb='device.network'")
    for _ in range(4):
        agora[0] += 60
        assert await _passo(parque, "varredura") is False
    assert st.db.scalar("SELECT COUNT(*) FROM commands WHERE verb='device.network'") == comandos
    assert len(reinicios.pedidos) == maximo


async def test_reinicio_recusado_tambem_conta_para_o_teto(parque: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """Um worker sem o verbo recusa o `restart` para sempre: sem contar a recusa, a varredura pediria de 5 em 5 min."""
    st = parque.state
    assert st is not None
    _, reinicios, _ = _preparar(parque, monkeypatch, policy="livre")
    reinicios.aceitar = False
    conv = st.rede_convergencia
    agora = [1_000_000.0]
    conv._agora = lambda: agora[0]
    assert await _passo(parque, "varredura")
    await asyncio.sleep(0.3)
    for _ in range(8):
        if _linha(parque)["state"] == "pendente":
            break
        agora[0] += 301
        await _passo(parque, "varredura")
        await asyncio.sleep(0.2)
    assert len(reinicios.pedidos) == int(st.cfg.file.rede.reinicios_max)
    assert _linha(parque)["state"] == "pendente" and "nenhum boot foi detectado" in str(_linha(parque)["error"])


async def test_reiniciar_o_backend_nao_zera_o_teto_de_reinicios(parque: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """25.11 (RA-12): a conta de reinícios pedidos da revisão vivia só na memória, e cada reinício do backend a zerava
    (88 reinícios pedidos pela rede em 7 dias). Agora ela está na linha (`restart_rev`, `restarts_requested`, migração
    093): esvaziar a memória da convergência, como um processo novo, não dá reinícios novos ao aparelho."""
    st = parque.state
    assert st is not None
    _, reinicios, _ = _preparar(parque, monkeypatch, policy="livre")
    reinicios.aceitar = False
    conv = st.rede_convergencia
    agora = [1_000_000.0]
    conv._agora = lambda: agora[0]
    maximo = int(st.cfg.file.rede.reinicios_max)
    assert maximo >= 2
    assert await _passo(parque, "varredura")
    await asyncio.sleep(0.3)
    assert len(reinicios.pedidos) == 1
    linha = _linha(parque)
    assert linha is not None and linha["restarts_requested"] == 1 and linha["restart_rev"] == linha["desired_rev"]
    conv._mem.clear()                                                              # o backend reiniciou
    for _ in range(8):
        if _linha(parque)["state"] == "pendente":
            break
        agora[0] += 301
        await _passo(parque, "varredura")
        await asyncio.sleep(0.2)
    # O teto valeu somando o antes e o depois do reinício: nenhum pedido além de `reinicios_max`.
    assert len(reinicios.pedidos) == maximo
    linha = _linha(parque)
    assert linha["state"] == "pendente"
    assert linha["restarts_requested"] == 0 and linha["restart_rev"] is None    # a desistência fecha a conta


def test_adb_reverse_so_leva_a_porta_no_argumento(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess
    from types import SimpleNamespace

    from app.devices import adb as adb_mod

    argvs: list[list[str]] = []

    def run(cmd: list[str], **_kw: object) -> subprocess.CompletedProcess:
        argvs.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(adb_mod.subprocess, "run", run)
    a = adb_mod.Adb(SimpleNamespace(adb="adb", env=lambda: {}), "emulator-5640")  # type: ignore[arg-type]
    a.reverse(18123)
    a.remove_reverse(18123)
    assert argvs == [["adb", "-s", "emulator-5640", "reverse", "tcp:18123", "tcp:18123"],
                     ["adb", "-s", "emulator-5640", "reverse", "--remove", "tcp:18123"]]


async def test_sem_download_com_o_aparelho_sem_internet_diz_o_que_a_plataforma_mediu(
        parque: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """android-03 (30/09): o aparelho estava sem internet desde antes da troca, e o erro dizia só "não baixou o
    perfil". Com a conectividade medida `unavailable`, o erro diz isso e sugere o restart; sem ela, o erro fica como era
    (a aplicação não é recusada pela sonda: com o bloqueio ativo e a VPN caída ela também dá `unavailable`)."""
    from app.devices import rede_convergencia
    from app.models import ConnectivityInfo

    st = parque.state
    assert st is not None
    _preparar(parque, monkeypatch, policy="exigida")

    async def sem_download(*_a: object, **_k: object) -> str:
        raise RedeAplicacaoError("o io.nekohasekai.sfa não baixou o perfil (OK, Create tocados, nenhum GET)")

    monkeypatch.setattr(rede_convergencia, "provisionar", sem_download)
    rt = st.devices.devices["android-01"]
    rt.connectivity = ConnectivityInfo(state="unavailable", detail="sem internet: DNS não responde")
    assert await _passo(parque, "pedido")
    erro = str(_linha(parque)["error"])
    assert "não baixou o perfil" in erro and "SEM internet" in erro and "restart" in erro
    rt.connectivity = ConnectivityInfo(state="healthy")
    assert await _passo(parque, "pedido")
    erro = str(_linha(parque)["error"])
    assert "não baixou o perfil" in erro and "SEM internet" not in erro


def test_regras_de_bloqueio_contam_as_regras_e_nao_o_cabecalho() -> None:
    """O `dumpsys connectivity` do Android 14 imprime "Lockdown filtering rules:" mesmo sem bloqueio (lista vazia).
    Contando o cabeçalho, a política `exigida` (sem bloqueio) nunca via o túnel como conectado e a rede reiniciava o
    aparelho em cadeia (android-09, 01/10/2026). A contagem é das linhas `UIDs:` da lista; medido no aparelho real:
    R=3 no android-03 (com bloqueio) e R=0 no android-09 (sem)."""
    from app.devices.rede_aplicacao import comando_de_observacao

    cmd = comando_de_observacao("io.nekohasekai.sfa")
    trecho = next(p for p in cmd.split("; ") if p.startswith("echo R="))
    assert "grep -c 'UIDs:'" in trecho and "grep -A" in trecho
    assert "grep -c 'Lockdown filtering rules'" not in cmd


async def test_manutencao_do_worker_recusa_o_reinicio_da_rede_e_nao_o_device_network(
        parque: Harness) -> None:
    """Abort gate do W8 (diagnóstico do android-09), pelo `_precheck` REAL: com o worker em manutenção, o reinício que a
    convergência pede é recusado e a recusa fica no histórico do aparelho; o `device.network` (que não passa pelo
    `_precheck`) segue correndo; o pedido recusado conta para `reinicios_max` e a convergência espera
    `_ESPERA_DA_RELEITURA_S` antes de tentar de novo. Sem manutenção, o mesmo pedido é aceito."""
    from app.devices import rede_convergencia as conv_mod
    from app.workers.protocol import Hello, WorkerResources

    st = parque.state
    assert st is not None
    reg = st.workers
    reg.autenticar(Hello(worker_id="worker-lan-01", name="Notebook da LAN", agent_version="0.1.0", os="windows",
                         max_slots=6, verbs=["restart"], devices=[],
                         resources=WorkerResources(cpu_count=4, ram_total_mb=8192, ram_free_mb=4096)),
                   token=None, enrollment=reg.criar_inscricao())

    async def _noop(payload: dict) -> None:
        return None
    reg.attach("worker-lan-01", _noop)
    rt = st.devices.devices["android-01"]
    rt.worker_id, rt.worker_verbs = "worker-lan-01", ["restart"]
    conv = st.rede_convergencia
    conv.atraso_do_reinicio_s = 0.05
    ap = AparelhoFalso(id="android-01")
    conv._aparelho = lambda _s, _rt: ap
    perfil = rede.criar_perfil(st, rede.ler_cadastro({"name": "Central", "kind": "vpn", "protocol": "singbox",
                                                      "endpoint_host": "10.0.2.2", "endpoint_port": 51820,
                                                      "params": {"servidor": "central"}}), "teste")
    rede.atribuir(st, rede.NetworkAssignBody(instance_ids=["android-01"], vpn_profile_id=perfil.id, policy="livre"),
                  "teste")

    reg.set_maintenance("worker-lan-01", True)
    agora = [1_000_000.0]
    conv._agora = lambda: agora[0]
    assert await _passo(parque, "pedido")                                        # o apply corre em manutenção
    await asyncio.sleep(0.4)                                                     # o reinício é pedido 0,05 s depois
    [apply_cmd] = st.db.query("SELECT * FROM commands WHERE verb='device.network'")
    assert apply_cmd["state"] == "succeeded"                                     # manutenção não barra a rede
    [reinicio] = st.db.query("SELECT * FROM commands WHERE verb='restart'")
    assert reinicio["state"] == "rejected" and "manutenção" in str(reinicio["reason"])
    linha = _linha(parque)
    assert linha["state"] == "configurado" and linha["applied_rev"] == 1         # a evidência segue disponível
    mem = conv.memoria("android-01")
    assert mem.reinicios.get(1) == 1                                             # a recusa conta para o teto
    assert mem.espera_ate == agora[0] + conv_mod._ESPERA_DA_RELEITURA_S == agora[0] + 300.0
    assert "android-01" not in conv._reinicio_agendado
    assert await _passo(parque, "varredura") is False                            # na espera: nada de comando por passada
    assert st.db.scalar("SELECT COUNT(*) FROM commands WHERE verb='restart'") == 1

    reg.set_maintenance("worker-lan-01", False)                                  # sem manutenção, nada dispara sozinho
    await asyncio.sleep(0.2)
    assert st.db.scalar("SELECT COUNT(*) FROM commands WHERE verb='restart'") == 1
    conv._pedir_reinicio("android-01", 1, "de novo", 24)                         # o próximo pedido é aceito
    [_, aceito] = st.db.query("SELECT * FROM commands WHERE verb='restart' ORDER BY created_at, id")
    assert aceito["state"] != "rejected"
    assert conv.memoria("android-01").reinicios.get(1) == 2


# ============================================================================ 29.21: o reinício que a rede pede e nunca sai
def _objetivo_parado(parque: Harness, status: str, wait_reason: str | None, iid: str = "android-01",
                     oid: str = "run-2921:o1") -> str:
    """Um objetivo de execução `running` SEM etapa pronta (o escalonador não o despacha): só a linha que a convergência
    e a vitrine leem. `wait_reason='rede'` é a espera da porta (25.6 `running`, ou `pending` pelo despacho)."""
    st = parque.state
    assert st is not None
    # K-084: `INSERT OR IGNORE` é só do SQLite; `ON CONFLICT DO NOTHING` vale nos dois.
    st.db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids,"
                  " created_at, plan) VALUES ('run-2921','k-2921','teste','execute','running',1,?,?,'{}')"
                  " ON CONFLICT DO NOTHING", (json.dumps([iid]), now_iso()))
    st.db.execute("DELETE FROM objectives WHERE id=?", (oid,))
    st.db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, wait_reason)"
                  " VALUES (?,'run-2921',?,?,1,?)", (oid, iid, status, wait_reason))
    return oid


@pytest.mark.parametrize("status", ["running", "pending"])
@pytest.mark.parametrize("ocupado", [False, True], ids=["livre", "ocupado_24_tentativas"])
async def test_reinicio_com_objetivo_esperando_a_rede(parque: Harness, monkeypatch: pytest.MonkeyPatch, ocupado: bool,
                                                      status: str) -> None:
    """android-05 (02/10): `configurado` sem boot, um objetivo parado em `wait_reason='rede'` e nenhum `restart` aberto
    nunca. O objetivo que espera a rede NÃO segura o reinício em nenhum termo; com o aparelho ocupado por outro motivo,
    esgotadas as tentativas (24 de 5 s) NÃO vêm os 300 s mudos: a retentativa de ~30 s abre o `restart` assim que solta."""
    from app.models import ControlOwner

    st = parque.state
    assert st is not None
    _, reinicios, _ = _preparar(parque, monkeypatch)
    conv = st.rede_convergencia
    conv.intervalo_do_reinicio_s = 0.001                                           # as 24 tentativas, sem esperar de verdade
    conv.retentativa_do_reinicio_s = 0.05                                          # o "30 s" do produto
    rt = st.devices.devices["android-01"]
    oid = _objetivo_parado(parque, status, "rede")
    if ocupado:
        rt.control = ControlOwner.ai                                               # outro trabalho no aparelho
    try:
        assert await _passo(parque, "pedido")                                      # `configurado`, reinício agendado
        if ocupado:
            await parque.wait(lambda: "nova tentativa em" in str((_linha(parque) or {}).get("detail")), 10,
                              "esgotamento das tentativas dito na linha")
            assert reinicios.pedidos == []
            assert conv.memoria("android-01").espera_ate == 0.0                    # nada de 300 s de mudez
            assert "android-01" in conv._reinicio_agendado                         # a retentativa está agendada
            detalhe = str(_linha(parque)["detail"])
            assert "controle da IA" in detalhe and f"objetivo {oid} espera a rede" in detalhe, detalhe
    finally:
        rt.control = ControlOwner.none
    await parque.wait(lambda: len(reinicios.pedidos) == 1, 10, "o restart pedido")
    assert [i for i, _ in reinicios.pedidos] == ["android-01"]
    assert "android-01" not in conv._reinicio_agendado
    assert conv.memoria("android-01").reinicio_segurado == ""
    assert _linha(parque)["state"] == "configurado" and "reinício c-teste-1 pedido" in str(_linha(parque)["detail"])


async def test_reinicio_que_nao_sai_diz_qual_termo_segurou(parque: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """A observação (29.21): o termo de `ocupado` que segurou o reinício vai para a linha, UMA vez por termo (a tentativa
    é de 5 em 5 s), o esgotamento e o `restart` que o worker não sabe fazer também dizem a causa."""
    from app.models import ControlOwner

    st = parque.state
    assert st is not None
    _, reinicios, _ = _preparar(parque, monkeypatch)
    conv = st.rede_convergencia
    conv.atraso_do_reinicio_s = 3600.0                                             # as tentativas só correm daqui, à mão
    conv.intervalo_do_reinicio_s = 3600.0
    agora = [1_000_000.0]
    conv._agora = lambda: agora[0]
    gravadas: list[str] = []
    registrar0 = rede.registrar_observacao

    def registrar(st_, iid, *, rev, estado, evidencia, **kw):
        gravadas.append(evidencia)
        return registrar0(st_, iid, rev=rev, estado=estado, evidencia=evidencia, **kw)

    monkeypatch.setattr(rede, "registrar_observacao", registrar)
    assert await _passo(parque, "pedido")                                          # `configurado`, nada pedido ainda
    gravadas.clear()
    rt = st.devices.devices["android-01"]
    rt.control = ControlOwner.user
    conv._pedir_reinicio("android-01", 1, "m", 5)
    conv._pedir_reinicio("android-01", 1, "m", 4)                                  # mesmo termo: não regrava
    assert len(gravadas) == 1 and "aguarda o aparelho: controle de uma pessoa" in gravadas[0]
    rt.control = ControlOwner.none
    oid = _objetivo_parado(parque, "waiting_user", None)
    conv._pedir_reinicio("android-01", 1, "m", 3)                                  # outro termo: grava
    assert len(gravadas) == 2 and f"objetivo {oid} em andamento" in gravadas[1]
    assert reinicios.pedidos == []
    # Esgotou, sem objetivo esperando a rede: diz o termo e a espera de 5 min, e a varredura volta depois.
    conv._reinicio_agendado.add("android-01")
    conv._pedir_reinicio("android-01", 1, "m", 0)
    assert "não saiu em 25 tentativas" in gravadas[2] and f"objetivo {oid} em andamento" in gravadas[2]
    assert "5 min" in gravadas[2] and len(gravadas) == 3
    assert conv.memoria("android-01").espera_ate == agora[0] + 300.0
    assert "android-01" not in conv._reinicio_agendado
    # Livre, mas o worker do aparelho não tem o verbo: `pedir_ciclo_de_vida` devolve None sem abrir linha, e a causa fica dita.
    st.db.execute("UPDATE objectives SET status='succeeded' WHERE id=?", (oid,))
    reinicios.aceitar = False
    conv._pedir_reinicio("android-01", 1, "m", 0)
    assert len(reinicios.pedidos) == 1 and "recusado no pré-voo ou pela pausa de reparo" in gravadas[-1]
    rt.worker_verbs = []
    conv._pedir_reinicio("android-01", 1, "m", 0)
    assert len(reinicios.pedidos) == 2
    assert "reinício da rede não foi aceito: o worker do aparelho não tem o verbo restart" in gravadas[-1]
    assert "reinício da rede não foi aceito" in str(_linha(parque)["detail"])
