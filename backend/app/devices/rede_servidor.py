"""O servidor de rede do central (ADR-056, item 25.4): o sing-box oficial em modo usuário, gerenciado pela plataforma.

Decisão do dono P2 (29/09): servidor WireGuard no central, sem driver, sem NAT, sem serviço do Windows e sem mudar a
rede ou o firewall do sistema. O piloto da medição 25.1 (`data/rede/piloto/`, fora do Git) provou o caminho: o
endpoint WireGuard do sing-box aceita o par e manda o tráfego ao `direct`, e o inbound `mixed` autenticado é
alcançado PELO túnel. Aqui ele deixa de ser montado à mão:

- **um par por aparelho atribuído** a um perfil de VPN com `params.servidor: "central"`. A chave do aparelho é
  gerada pela plataforma (`segredo_de_rede.gerar_chave_wireguard`, migração 058), estável, com um endereço próprio
  na sub-rede; o servidor tem a dele;
- **regras de rota que recusam o central e a rede local** (limitação medida no 25.1): o endpoint reescreve o
  próprio endereço (`10.66.0.1:<p>` chega como `127.0.0.1:<p>`), então qualquer par alcançava a API (8000), o
  servidor adb (5037) e os consoles dos emuladores; e pelo `direct` alcançava a LAN. Agora só a porta do proxy do
  central passa, e só vinda do túnel; loopback, a sub-rede do túnel, as faixas privadas, link-local, CGNAT e
  multicast são recusados para o túnel E para o proxy (senão o proxy autenticado vira a porta dos fundos);
- **segredo nunca em argumento**: a configuração (chave do servidor e senhas do proxy) vai para um arquivo em
  `paths.data_dir/rede/servidor/`, numa pasta com ACL só do usuário, pelo consumidor restrito do cofre; o processo
  recebe o CAMINHO;
- **reiniciado quando o conjunto muda** (par, usuário do proxy, porta): a assinatura do que foi gravado (sem
  segredo, só ids, chaves públicas e endereços) fica no arquivo de PID; igual e vivo = nada a fazer;
- **log de conexões** como evidência: `inbound connection from 10.66.0.N` diz que aquele par falou com o servidor e
  quando (`ultima_conexao`);
- **aparelhos de outra máquina** (25.7): chegam pela LAN (`endpoint_lan`) e dependem do Firewall do Windows daqui,
  que só é LIDO (`rede_firewall`); o `remote_access` do status diz quem são, o estado e o comando do dono.

O processo vive enquanto o backend vive (`parar` no encerramento; um órfão de queda é reconhecido pelo PID, pelo
executável e pelo caminho da configuração, e só então encerrado — processo alheio nunca é tocado). Sem nenhum
aparelho pedindo o servidor, ele não roda.
"""
from __future__ import annotations

import asyncio
import contextlib
import getpass
import hashlib
import ipaddress
import json
import logging
import os
import re
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import psutil

from ..db import Database, loads
from ..security.redaction import redact
from ..security.secret_store import SecretStore
from ..security.segredo_de_rede import Fonte, gerar_chave_wireguard, gravar_configuracao_do_servidor
from .rede_firewall import FirewallDoCentral, LeituraDoFirewall
from .sdk import ambiente_dos_filhos
from ..util import now_iso

if TYPE_CHECKING:
    from ..config import RedeServidorCfg

log = logging.getLogger(__name__)

#: `params.servidor` de um perfil que a plataforma serve pelo sing-box do central.
SERVIDOR_CENTRAL = "central"
DONO_DO_SERVIDOR = "servidor"
TAG_ENDPOINT = "wg-srv"
TAG_PROXY = "proxy-in"
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
NOVO_GRUPO = 0x00000200 if os.name == "nt" else 0

#: O que um par (ou o proxy) nunca alcança pelo `direct`: esta máquina e qualquer rede que não seja a internet
#: pública. A sub-rede do próprio túnel entra na hora de montar (vem da configuração).
FAIXAS_RECUSADAS: tuple[str, ...] = (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24",
    "192.0.2.0/24", "192.168.0.0/16", "198.18.0.0/15", "198.51.100.0/24", "203.0.113.0/24", "224.0.0.0/4",
    "240.0.0.0/4",
    "::/128", "::1/128", "::ffff:0:0/96", "64:ff9b::/96", "100::/64", "2001:db8::/32", "fc00::/7", "fe80::/10",
    "ff00::/8",
)


class ServidorDeRedeError(RuntimeError):
    """O servidor não subiu (binário ausente, configuração recusada, processo que morreu). Mensagem sem segredo."""


def gerenciado_pelo_central(params: Mapping[str, object] | None) -> bool:
    return bool(params) and params.get("servidor") == SERVIDOR_CENTRAL           # type: ignore[union-attr]


def endereco_do_servidor(sub_rede: str) -> str:
    """O primeiro endereço utilizável da sub-rede (10.66.0.1 em 10.66.0.0/24)."""
    return str(next(ipaddress.ip_network(sub_rede, strict=False).hosts()))


@dataclass(frozen=True)
class Par:
    """Um aparelho no servidor: a chave PÚBLICA dele e o endereço no túnel. Nada aqui é segredo."""

    instance_id: str
    public_key: str
    address: str


@dataclass(frozen=True)
class UsuarioDoProxy:
    """Um usuário do proxy do central: o perfil de proxy (a senha está no cofre, pela linha dele) e o nome."""

    profile_id: str
    username: str


@dataclass(frozen=True)
class Desejo:
    pares: tuple[Par, ...]
    usuarios: tuple[UsuarioDoProxy, ...]

    @property
    def vazio(self) -> bool:
        return not self.pares

    def assinatura(self, cfg: RedeServidorCfg, chave_publica: str) -> str:
        """Hash do que muda o arquivo, sem segredo: pares, usuários (e o perfil de onde vem a senha), portas, sub-rede
        e a chave pública do servidor. Senha trocada num perfil novo muda o `profile_id` e, portanto, a assinatura."""
        bruto = json.dumps({"pares": [(p.instance_id, p.public_key, p.address) for p in self.pares],
                            "usuarios": [(u.profile_id, u.username) for u in self.usuarios],
                            # O endereço da LAN não entra no arquivo do servidor (só no perfil do aparelho remoto):
                            # trocá-lo não pode derrubar as conexões de todos.
                            "cfg": cfg.model_dump(exclude={"binario", "log_max_mb", "endpoint_lan"}),
                            "servidor": chave_publica},
                           sort_keys=True)
        return hashlib.sha256(bruto.encode("utf-8")).hexdigest()


def usuario_do_proxy(profile_id: str, params: Mapping[str, object]) -> str:
    """O nome de usuário do proxy (`params.username`) ou, sem ele, o id do perfil: nome não é segredo."""
    nome = str(params.get("username") or "").strip()
    return nome or profile_id


# ============================================================================ a configuração (pura)
def regras_de_rota(cfg: RedeServidorCfg, *, com_proxy: bool) -> list[dict[str, object]]:
    """As regras que fecham o central e a rede local. A ordem é o contrato: a primeira que casa decide.

    1. `localhost` por NOME é recusado (o `direct` o resolveria nesta máquina);
    2. o destino pedido ao proxy por nome é resolvido antes das regras de faixa (`ip_cidr` não casa domínio);
    3. do túnel, só a porta do proxy do central passa — no endereço do servidor ou no 127.0.0.1 em que o endpoint a
       reescreve (medido no 25.1);
    4. o resto do central, a sub-rede do túnel e as faixas não públicas: recusado, para o túnel E para o proxy.
    O que sobra (internet pública) segue o `final: direct`."""
    servidor = endereco_do_servidor(cfg.sub_rede)
    rede = str(ipaddress.ip_network(cfg.sub_rede, strict=False))
    entradas = [TAG_ENDPOINT, TAG_PROXY] if com_proxy else [TAG_ENDPOINT]
    regras: list[dict[str, object]] = [{"inbound": entradas, "domain_suffix": ["localhost"], "action": "reject"}]
    if com_proxy:
        regras.append({"inbound": [TAG_PROXY], "action": "resolve"})
        regras.append({"inbound": [TAG_ENDPOINT], "ip_cidr": ["127.0.0.1/32", f"{servidor}/32"],
                       "port": [cfg.porta_proxy], "action": "route", "outbound": "direct"})
    regras.append({"inbound": entradas, "ip_cidr": [*FAIXAS_RECUSADAS, rede], "action": "reject"})
    return regras


def config_do_servidor(*, chave_privada: str, pares: Sequence[Par], usuarios: Mapping[str, str],
                       cfg: RedeServidorCfg, caminho_do_log: Path) -> dict[str, object]:
    """A configuração do sing-box do central. Recebe os segredos já abertos — só é chamada DENTRO do `montar` do
    consumidor restrito (`segredo_de_rede.gravar_configuracao_do_servidor`), nunca fora dele."""
    rede = ipaddress.ip_network(cfg.sub_rede, strict=False)
    servidor = endereco_do_servidor(cfg.sub_rede)
    inbounds: list[dict[str, object]] = []
    if usuarios:
        inbounds.append({"type": "mixed", "tag": TAG_PROXY, "listen": "127.0.0.1", "listen_port": cfg.porta_proxy,
                         "users": [{"username": u, "password": s} for u, s in sorted(usuarios.items())]})
    return {
        # `info` é o nível que registra cada conexão aceita (`inbound connection from 10.66.0.N`): a evidência.
        "log": {"level": "info", "timestamp": True, "output": caminho_do_log.as_posix()},
        "endpoints": [{
            "type": "wireguard", "tag": TAG_ENDPOINT, "system": False, "mtu": cfg.mtu,
            "address": [f"{servidor}/{rede.prefixlen}"], "private_key": chave_privada,
            "listen_port": cfg.porta_wireguard,
            "peers": [{"public_key": p.public_key, "allowed_ips": [f"{p.address}/32"]} for p in pares],
        }],
        "inbounds": inbounds,
        "outbounds": [{"type": "direct", "tag": "direct"}],
        "route": {"rules": regras_de_rota(cfg, com_proxy=bool(usuarios)), "final": "direct"},
    }


# ============================================================================ processo e ACL (bordas trocáveis)
class Processos(Protocol):
    """A máquina, para o gerenciador: subir, conferir e encerrar o processo. O teste troca por um dublê."""

    def lancar(self, argv: list[str], *, cwd: Path, saida: Path) -> int: ...
    def vivo(self, pid: int, binario: Path, config: Path) -> bool: ...
    def encerrar(self, pid: int) -> None: ...


class ProcessosReais:
    """`subprocess`/`psutil` de verdade. `vivo` só reconhece o NOSSO processo: PID reciclado pelo Windows, ou um
    sing-box de outra pessoa, nunca é adotado nem encerrado."""

    def __init__(self) -> None:
        self._filhos: dict[int, subprocess.Popen[bytes]] = {}

    def lancar(self, argv: list[str], *, cwd: Path, saida: Path) -> int:
        with open(saida, "ab") as out:
            # 29.47: processo longo de terceiro; recebe o caminho da configuração, nunca os segredos do backend.
            proc = subprocess.Popen(argv, cwd=str(cwd), stdout=out, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, env=ambiente_dos_filhos(),
                                    creationflags=NO_WINDOW | NOVO_GRUPO)
        self._filhos[proc.pid] = proc
        return proc.pid

    def vivo(self, pid: int, binario: Path, config: Path) -> bool:
        try:
            proc = psutil.Process(pid)
            if not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE:
                return False
            exe = Path(proc.exe())
            if exe.name.lower() != binario.name.lower():
                return False
            return any(Path(a) == config for a in proc.cmdline()[1:] if a and not a.startswith("-"))
        except (psutil.Error, OSError, ValueError):
            return False

    def encerrar(self, pid: int) -> None:
        try:
            proc = psutil.Process(pid)
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except psutil.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        except psutil.NoSuchProcess:
            pass
        filho = self._filhos.pop(pid, None)
        if filho is not None:
            with contextlib.suppress(Exception):
                filho.wait(timeout=1)


def restringir_ao_usuario(caminho: Path) -> None:
    """ACL só do usuário do backend. No Windows, `icacls` sem herança e com controle total só para ele (a pasta com
    herança para o que nascer dentro dela); fora dele, `chmod` 700/600. Falha levanta: sem ACL, nada é gravado."""
    if os.name == "nt":
        usuario = os.environ.get("USERNAME") or getpass.getuser()
        dominio = os.environ.get("USERDOMAIN")
        conta = f"{dominio}\\{usuario}" if dominio else usuario
        direito = "(OI)(CI)(F)" if caminho.is_dir() else "(F)"
        res = subprocess.run(["icacls", str(caminho), "/inheritance:r", "/grant:r", f"{conta}:{direito}"],
                             capture_output=True, text=True, timeout=30, env=ambiente_dos_filhos(),
                             creationflags=NO_WINDOW)
        if res.returncode != 0:
            raise OSError(f"icacls recusou restringir {caminho.name} ({res.returncode})")
        return
    os.chmod(caminho, 0o700 if caminho.is_dir() else 0o600)


# ============================================================================ o gerenciador
_CONEXAO = re.compile(r"^(?P<quando>[+-]\d{4} \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) INFO endpoint/wireguard\["
                      + re.escape(TAG_ENDPOINT) + r"\]: inbound connection from (?P<ip>[0-9a-fA-F.:]+?):\d+\s*$")


class ServidorDeRede:
    """O processo do sing-box do central: desejado (do banco) × rodando (PID e assinatura)."""

    def __init__(self, db: Database, secrets: SecretStore, cfg: Callable[[], RedeServidorCfg], *, pasta: Path,
                 binario: Callable[[], Path], processos: Processos | None = None,
                 restringir: Callable[[Path], None] = restringir_ao_usuario) -> None:
        self.db, self.secrets, self._cfg, self._binario = db, secrets, cfg, binario
        self.pasta = pasta
        self.processos: Processos = processos or ProcessosReais()
        self.restringir = restringir
        self._lock = asyncio.Lock()
        #: Quanto esperar depois de lançar para conferir que o processo não caiu na leitura da configuração.
        self.espera_de_subida_s = 1.0
        self._pid: int | None = None
        self._assinatura: str | None = None
        self._iniciado_em: str | None = None
        self.detail: str | None = None
        self._ultimo_erro: str | None = None
        #: O firewall do central diante dos aparelhos de outra máquina (25.7): só leitura, com cache.
        self.firewall = FirewallDoCentral(cfg, lambda: self._binario())
        #: "Este aparelho está em outra máquina?" (o `external` do gerenciador: worker da LAN ou celular). Quem liga é
        #: o `AppState`; sozinho, o servidor não conhece o parque e trata todos como locais.
        self.eh_remoto: Callable[[str], bool] = lambda _iid: False

    # -- caminhos
    @property
    def config(self) -> Path:
        return self.pasta / "servidor.json"

    @property
    def log(self) -> Path:
        return self.pasta / "servidor.log"

    @property
    def _saida(self) -> Path:
        return self.pasta / "servidor.stdout"

    @property
    def _arquivo_de_pid(self) -> Path:
        return self.pasta / "servidor.pid"

    # -- o desejado (banco)
    def desejado(self) -> Desejo:
        """Os pares são os aparelhos cujo perfil de VPN PEDIDO é servido pelo central e que já têm chave; os usuários
        do proxy, os perfis de proxy do central pedidos por algum aparelho. Pedido, e não aplicado: o servidor tem de
        conhecer a chave nova ANTES de o aparelho reiniciar com ela."""
        pares: list[Par] = []
        for r in self.db.query("SELECT dn.instance_id, p.params, k.public_key, k.address FROM device_network dn"
                               " JOIN network_profiles p ON p.id = dn.vpn_profile_id"
                               " JOIN network_keys k ON k.owner = dn.instance_id"
                               " WHERE p.kind='vpn' ORDER BY dn.instance_id"):
            if gerenciado_pelo_central(loads(r["params"], {}) or {}) and r["address"]:
                pares.append(Par(str(r["instance_id"]), str(r["public_key"]), str(r["address"])))
        usuarios: dict[str, UsuarioDoProxy] = {}
        for r in self.db.query("SELECT DISTINCT p.id, p.params, p.secret_ref FROM device_network dn"
                               " JOIN network_profiles p ON p.id = dn.proxy_profile_id WHERE p.kind='proxy'"
                               " ORDER BY p.id"):
            params = loads(r["params"], {}) or {}
            if gerenciado_pelo_central(params) and r["secret_ref"]:
                u = UsuarioDoProxy(str(r["id"]), usuario_do_proxy(str(r["id"]), params))
                usuarios.setdefault(u.username, u)          # dois perfis com o mesmo nome: vale o primeiro
        return Desejo(tuple(pares), tuple(usuarios.values()))

    def chave_publica(self) -> str:
        """A chave pública do servidor, gerada na primeira vez (a privada vai direto ao cofre)."""
        return gerar_chave_wireguard(self.db, self.secrets, DONO_DO_SERVIDOR, kind="servidor", address=None)

    def par_do_aparelho(self, instance_id: str) -> Par:
        """A chave do aparelho (gerada na primeira aplicação e estável depois) e o endereço dele no túnel."""
        row = self.db.one("SELECT public_key, address FROM network_keys WHERE owner=?", (instance_id,))
        if row is None:
            endereco = self._proximo_endereco()
            publica = gerar_chave_wireguard(self.db, self.secrets, instance_id, kind="aparelho", address=endereco)
            return Par(instance_id, publica, endereco)
        return Par(instance_id, str(row["public_key"]), str(row["address"]))

    def _proximo_endereco(self) -> str:
        """O menor endereço livre da sub-rede (sem o do servidor). Sem `await` entre ler e gravar: no laço de eventos
        ninguém pega o mesmo endereço no meio; o UNIQUE da 058 segura o resto."""
        cfg = self._cfg()
        usados = {str(r["address"]) for r in self.db.query("SELECT address FROM network_keys WHERE address IS NOT NULL")}
        usados.add(endereco_do_servidor(cfg.sub_rede))
        for ip in ipaddress.ip_network(cfg.sub_rede, strict=False).hosts():
            if str(ip) not in usados:
                return str(ip)
        raise ServidorDeRedeError(f"A sub-rede do túnel ({cfg.sub_rede}) não tem mais endereço livre.")

    # -- o rodando
    def _ler_pid(self) -> tuple[int | None, str | None, str | None]:
        try:
            dados = json.loads(self._arquivo_de_pid.read_text(encoding="utf-8"))
            return int(dados["pid"]), str(dados.get("assinatura") or ""), dados.get("iniciado_em")
        except (OSError, ValueError, KeyError, TypeError):
            return None, None, None

    def _vivo(self) -> bool:
        if self._pid is None:
            pid, assinatura, iniciado = self._ler_pid()
            if pid is not None and self.processos.vivo(pid, self._binario(), self.config):
                # Órfão de uma queda do backend: é nosso (executável e configuração conferem). Adotado com a
                # assinatura que ele gravou; se o desejado mudou, `garantir` o reinicia.
                self._pid, self._assinatura, self._iniciado_em = pid, assinatura, iniciado
                log.info("servidor de rede: processo %s adotado", pid)
            return self._pid is not None
        if self.processos.vivo(self._pid, self._binario(), self.config):
            return True
        cauda = self._cauda_da_saida()
        self.detail = f"o processo {self._pid} saiu" + (f": {cauda}" if cauda else "")
        log.warning("servidor de rede: %s", self.detail)
        self._pid = self._assinatura = self._iniciado_em = None
        return False

    def _cauda_da_saida(self) -> str:
        """O fim da saída do processo, redigido: é o que explica uma configuração recusada."""
        try:
            with open(self._saida, "rb") as arq:
                arq.seek(0, os.SEEK_END)
                arq.seek(max(0, arq.tell() - 600))
                return redact(arq.read().decode("utf-8", "replace").strip().splitlines()[-1])[:300]
        except (OSError, IndexError):
            return ""

    async def garantir(self) -> dict[str, object]:
        """Deixa o processo como o banco pede: parado sem pares; rodando com a assinatura do desejado com pares.
        Idempotente e barato quando nada mudou (uma consulta e uma conferência de PID)."""
        async with self._lock:
            desejo = self.desejado()
            if desejo.vazio:
                if self._vivo():
                    await asyncio.to_thread(self._parar_processo, "nenhum aparelho pede o servidor do central")
                elif self.config.exists():
                    # Sobra de uma queda: a configuração tem a chave do servidor e ninguém a usa.
                    await asyncio.to_thread(self._parar_processo, "configuração sem processo")
                return self.status(conexoes=False)
            publica = self.chave_publica()
            assinatura = desejo.assinatura(self._cfg(), publica)
            if self._vivo() and self._assinatura == assinatura:
                return self.status(conexoes=False)
            try:
                await asyncio.to_thread(self._reiniciar, desejo, assinatura)
            except Exception as exc:
                self.detail = str(exc)[:300]
                if self.detail != self._ultimo_erro:
                    log.warning("servidor de rede não subiu: %s", self.detail)
                    self._ultimo_erro = self.detail
                raise
            self._ultimo_erro = None
            return self.status(conexoes=False)

    def _reiniciar(self, desejo: Desejo, assinatura: str) -> None:
        binario = self._binario()
        if not binario.is_file():
            raise ServidorDeRedeError(f"O executável do sing-box não está em {binario} (rede.servidor.binario).")
        if self._pid is not None:
            self._parar_processo("o conjunto de aparelhos mudou")
        self._preparar_pasta()
        self._girar_log()
        fontes = {"servidor": Fonte("chave", DONO_DO_SERVIDOR)}
        fontes.update({f"proxy:{u.username}": Fonte("perfil", u.profile_id) for u in desejo.usuarios})
        cfg = self._cfg()

        def montar(valores: Mapping[str, str]) -> str:
            usuarios = {u.username: valores[f"proxy:{u.username}"] for u in desejo.usuarios}
            return json.dumps(config_do_servidor(chave_privada=valores["servidor"], pares=desejo.pares,
                                                 usuarios=usuarios, cfg=cfg, caminho_do_log=self.log), indent=1)

        gravar_configuracao_do_servidor(self.db, self.secrets, fontes, self.config, montar=montar,
                                        restringir=self.restringir)
        # Só o caminho no argumento: `run -c <arquivo>`. Nada de conteúdo, nada de senha.
        pid = self.processos.lancar([str(binario), "run", "-c", str(self.config)], cwd=self.pasta, saida=self._saida)
        time.sleep(self.espera_de_subida_s)     # uma configuração recusada derruba o processo na hora
        if not self.processos.vivo(pid, binario, self.config):
            cauda = self._cauda_da_saida()
            raise ServidorDeRedeError("O sing-box do central saiu logo depois de subir" + (f": {cauda}" if cauda else "."))
        self._pid, self._assinatura, self._iniciado_em = pid, assinatura, now_iso()
        self._arquivo_de_pid.write_text(json.dumps({"pid": pid, "assinatura": assinatura,
                                                    "iniciado_em": self._iniciado_em}), encoding="utf-8")
        self.detail = f"no ar com {len(desejo.pares)} aparelho(s) e {len(desejo.usuarios)} usuário(s) de proxy"
        log.info("servidor de rede: pid %s, %s", pid, self.detail)

    def _preparar_pasta(self) -> None:
        self.pasta.mkdir(parents=True, exist_ok=True)
        # A cada início, e não só quando a pasta nasce: uma pasta criada à mão (ou por um piloto) pode ter herdado a
        # ACL larga do disco. Tudo o que nascer dentro (configuração, log, PID) herda a ACL só do usuário.
        self.restringir(self.pasta)

    def _girar_log(self) -> None:
        teto = self._cfg().log_max_mb * 2**20
        for arq in (self.log, self._saida):
            with contextlib.suppress(OSError):
                if arq.stat().st_size > teto:
                    os.replace(arq, arq.with_name(arq.name + ".1"))

    def _parar_processo(self, motivo: str) -> None:
        if self._pid is not None:
            self.processos.encerrar(self._pid)
            log.info("servidor de rede: processo %s encerrado (%s)", self._pid, motivo)
        self._pid = self._assinatura = self._iniciado_em = None
        # A configuração tem a chave do servidor: sem processo, ela não precisa ficar no disco.
        for arq in (self.config, self._arquivo_de_pid):
            with contextlib.suppress(OSError):
                arq.unlink()
        self.detail = f"parado ({motivo})"

    async def parar(self, motivo: str = "encerramento do backend") -> None:
        async with self._lock:
            if self._vivo():
                await asyncio.to_thread(self._parar_processo, motivo)

    # -- os aparelhos de outra máquina (25.7)
    def pares_remotos(self, desejo: Desejo | None = None) -> list[str]:
        return [p.instance_id for p in (desejo or self.desejado()).pares if self.eh_remoto(p.instance_id)]

    async def conferir_acesso_remoto(self, *, forcar: bool = False) -> LeituraDoFirewall | None:
        """Relê o firewall quando há par remoto (ou quando pedido): sem aparelho de outra máquina, o firewall não
        importa e a leitura (segundos de PowerShell) não roda à toa."""
        if not forcar and not self.pares_remotos():
            return self.firewall.ultima()
        return await self.firewall.conferir(forcar=forcar)

    def acesso_remoto(self, desejo: Desejo | None = None) -> dict[str, object]:
        """O caminho dos aparelhos de outra máquina até o servidor: o endpoint da LAN configurado, quem são eles e a
        última leitura do firewall (cache: o GET não roda PowerShell). O comando é o que o DONO roda; a plataforma
        nunca mexe no firewall."""
        cfg = self._cfg()
        leitura = self.firewall.ultima()
        return {"lan_endpoint": cfg.endpoint_lan or None, "wireguard_udp_port": cfg.porta_wireguard,
                "remote_peers": self.pares_remotos(desejo),
                "firewall": leitura.como_dict() if leitura is not None else None}

    # -- evidência e estado
    def ultimas_conexoes(self, *, janela_bytes: int = 512 * 1024) -> dict[str, str]:
        """Endereço do par → quando (pelo relógio do log) ele abriu a última conexão pelo túnel. Uma leitura só da
        cauda do log: é evidência recente que interessa, não a história inteira."""
        try:
            with open(self.log, "rb") as arq:
                arq.seek(0, os.SEEK_END)
                arq.seek(max(0, arq.tell() - janela_bytes))
                linhas = arq.read().decode("utf-8", "replace").splitlines()
        except OSError:
            return {}
        saida: dict[str, str] = {}
        for linha in linhas:                          # em ordem: a última de cada par fica
            m = _CONEXAO.match(linha.strip())
            if m:
                saida[m.group("ip")] = m.group("quando")
        return saida

    def ultima_conexao(self, endereco: str) -> str | None:
        return self.ultimas_conexoes().get(endereco)

    def status(self, *, conexoes: bool = True) -> dict[str, object]:
        """O estado para o painel e a evidência. `conexoes=False` (o laço de 60 s) não lê o log."""
        cfg = self._cfg()
        desejo = self.desejado()
        vivo = self._pid is not None
        vistas = self.ultimas_conexoes() if vivo and conexoes else {}
        chave = self.db.one("SELECT public_key FROM network_keys WHERE owner=?", (DONO_DO_SERVIDOR,))
        return {
            "binary_present": self._binario().is_file(), "running": vivo, "pid": self._pid,
            "started_at": self._iniciado_em, "signature": (self._assinatura or "")[:12] or None,
            "in_sync": vivo and self._assinatura is not None and chave is not None
                       and self._assinatura == desejo.assinatura(cfg, str(chave["public_key"])),
            "server_address": endereco_do_servidor(cfg.sub_rede), "subnet": cfg.sub_rede,
            "wireguard_udp_port": cfg.porta_wireguard,
            "proxy": f"127.0.0.1:{cfg.porta_proxy}" if desejo.usuarios else None,
            "server_public_key": str(chave["public_key"]) if chave is not None else None,
            "peers": [{"instance_id": p.instance_id, "address": p.address, "public_key": p.public_key,
                       "last_connection": vistas.get(p.address), "remote": self.eh_remoto(p.instance_id)}
                      for p in desejo.pares],
            "proxy_users": [u.username for u in desejo.usuarios],
            "remote_access": self.acesso_remoto(desejo),
            "detail": self.detail,
        }
