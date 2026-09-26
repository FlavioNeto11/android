"""Configuração do agente: um YAML pequeno na máquina do worker, e a credencial num arquivo à parte.

Separado da configuração do central de propósito — o worker não precisa saber de IA, banco, limites nem perfis.
O que ele precisa é onde está o SDK, onde ficam os AVDs, quais aparelhos ele hospeda, e para quem ligar.
"""
from __future__ import annotations

import getpass
import json
import logging
import os
import platform
import ssl
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..config import AppConfigFile, Config, EnvSettings
from ..workers.protocol import AppiumMode

log = logging.getLogger("poc.worker")

#: A credencial permanente NÃO fica no YAML: ela é gravada pelo próprio agente, com permissão restrita, depois de
#: trocar o token de inscrição. Assim o arquivo que alguém edita à mão nunca contém segredo.
CREDENTIAL_FILE = "worker-credential.json"


def _padrao_sdk_root() -> str:
    """Onde o Android SDK costuma estar NESTE sistema.

    Windows: `C:\\Android\\Sdk`, que é o que `scripts/install-prereqs.ps1` instala. Linux/macOS: o caminho do
    `sdkmanager` de linha de comando (`~/Android/Sdk`), que é onde o instalador Linux deste projeto põe o SDK.
    """
    if os.name == "nt":
        return r"C:\Android\Sdk"
    # `os.path` e não `pathlib`: `Path` escolhe a classe pelo `os.name` do processo, e o padrão POSIX precisa
    # ser calculável (e testável) sem depender de qual sistema está rodando o código agora.
    return os.path.join(os.path.expanduser("~"), "Android", "Sdk")


def _padrao_work_dir() -> str:
    """A pasta de trabalho do agente (AVDs, logs, evidências, credencial) neste sistema.

    Fora do Windows fica sob o HOME da conta que roda o agente — a unidade systemd de exemplo
    (`config/farm-worker.service`) roda como uma conta dedicada, então isto vira a pasta dela e não um caminho
    de sistema que exigiria root para criar.
    """
    if os.name == "nt":
        return r"C:\farm"
    return os.path.join(os.path.expanduser("~"), "farm")


class DeviceSpec(BaseModel):
    """Um aparelho que este worker hospeda, e a instância do parque que ele serve."""

    model_config = ConfigDict(extra="forbid")
    instance_id: str = Field(min_length=3, max_length=40)
    #: Vazio só para aparelho NÃO gerido: sem AVD não há nome de AVD. Era `min_length=1`, e o próprio
    #: `config/worker.example.yaml` sugeria `avd_name: ""` para esse caso — o exemplo versionado não passava
    #: pela validação do código que ele exemplifica (achado #14).
    avd_name: str = Field(default="", max_length=60)
    console_port: int = Field(ge=5554, le=5680)
    #: Aparelho físico ou contêiner: o agente não cria nem liga, só opera por ADB.
    managed: bool = True
    #: Configuração de emulador DESTE aparelho, por cima da global do worker (`android:` no YAML). `None` em
    #: qualquer campo = segue a global, que é o comportamento de antes.
    #:
    #: Existe por causa da VM da LOJA num worker remoto: ela precisa de imagem com Play Store (a global do parque
    #: é `google_apis`, sem loja), de janela (é na janela do emulador que a conta Google é digitada — nenhuma
    #: tecla passa pelo painel) e, em geral, de mais RAM que um aparelho de tarefa. Enquanto o agente aplicava a
    #: mesma configuração a todos os seus aparelhos, a loja não podia sair do central.
    system_image: str | None = Field(default=None, min_length=3, max_length=120)
    ram_mb: int | None = Field(default=None, ge=512, le=32_768)
    window: bool | None = None

    @model_validator(mode="after")
    def _avd_so_e_dispensavel_em_aparelho_nao_gerido(self) -> "DeviceSpec":
        """Aparelho GERIDO sem `avd_name` não teria o que criar nem o que ligar — e o erro apareceria lá na
        frente, no `emulator -avd ''`. Recusar na leitura do YAML põe a queixa na frente de quem o editou."""
        if self.managed and not self.avd_name.strip():
            raise ValueError(f"{self.instance_id}: `avd_name` é obrigatório em aparelho gerido pelo agente "
                             "(use `managed: false` para um aparelho físico ou contêiner).")
        return self

    @property
    def serial(self) -> str:
        return f"emulator-{self.console_port}"

    @property
    def adb_port(self) -> int:
        return self.console_port + 1


class WorkerSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    #: Endereço do servidor CENTRAL. O worker liga para lá — nunca o contrário.
    #:
    #: `http://` só é aceito para LOOPBACK (o caso do túnel SSH, em que o tráfego já vai cifrado dentro do
    #: túnel). Para qualquer outro endereço o esquema tem de ser `https://`: a primeira mensagem desta conexão
    #: carrega a credencial permanente do worker — ou o token de inscrição — e tudo o que vem depois inclui
    #: screenshot e evidência. Ver `_exigir_tls_fora_do_loopback`.
    server: str = Field(default="http://127.0.0.1:8000", max_length=200)
    #: Certificado da autoridade que assinou o certificado do central, quando ele é uma CA própria (o caso de
    #: um parque doméstico). Sem isto, `websockets.connect` recusa um certificado privado — corretamente — e a
    #: mensagem de erro não diz o que fazer. `None` = usar as autoridades públicas do sistema.
    ca_file: str | None = Field(default=None, max_length=400)
    worker_id: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    name: str = Field(min_length=1, max_length=120)
    #: Padrões POR SISTEMA. Eram `C:\Android\Sdk` e `C:\farm` fixos, e num worker Linux — a decisão de entrada do
    #: parque é máquinas mistas — o agente subia procurando o SDK num caminho que não existe naquele SO e só
    #: falhava adiante, em `SdkTools.found()`, com uma mensagem sobre o emulador em vez de sobre a configuração.
    sdk_root: str = Field(default_factory=lambda: _padrao_sdk_root())
    work_dir: str = Field(default_factory=lambda: _padrao_work_dir())
    appium: AppiumMode = "central"
    appium_url: str | None = None
    max_slots: int = Field(default=1, ge=1, le=64)
    devices: list[DeviceSpec] = []
    #: Perfil de hardware dos AVDs que ESTE worker cria. Espelha `android` do central; sem `ram_mb`, vale o perfil
    #: medido da IMAGEM (`devices/perfis.py`): `google_apis` → 2048 MB de convidado com `-lowram`, ≈2,7 GB no host.
    #: 1536 MB nessa imagem entra em thrash pós-boot (android-12, load 22; B21 em 26/09).
    android: dict[str, Any] = {}
    #: Quantos emuladores podem estar BOOTANDO ao mesmo tempo. Subir quatro de uma vez travou os quatro em ANR
    #: (medido em 19/09); um a um subiram limpos em 104–192 s.
    boot_parallelism: int = Field(default=1, ge=1, le=8)
    #: PISO da conta de RAM do HOST por aparelho, em MB. A conta em si vem do perfil da imagem de cada aparelho
    #: (`WorkerExecutor._custo_de_ram`); este número só a SOBE. Era a conta inteira, com 1800 no exemplo — abaixo
    #: de todo custo medido (2400 a 5200 MB) —, e a guarda aprovava boot que a máquina não aguentava. Continua
    #: aceito porque `worker.yaml` já instalados o declaram (`extra="forbid"` recusaria a chave sumida).
    ram_per_device_mb: int | None = Field(default=None, ge=256, le=16384)
    #: Guarda da máquina: quanto deve sobrar de RAM depois de subir mais um aparelho.
    min_free_ram_mb: int = Field(default=4096, ge=512, le=131072)

    @field_validator("server")
    @classmethod
    def _exigir_tls_fora_do_loopback(cls, valor: str) -> str:
        """Recusa `http://` para um central que não seja esta máquina.

        O que estava em jogo, medido no achado #120: nesta conexão vão a credencial permanente do worker (a cada
        conexão), os screenshots e as evidências. Numa rede Wi-Fi com chave compartilhada — o caso do notebook do
        parque — isso é legível por quem tem a chave. Recusar na leitura do YAML, e não na hora de conectar, põe
        o erro na frente de quem editou o arquivo.

        Loopback continua em `http://` de propósito: é o alvo do túnel SSH (`-R`), e ali o tráfego já vai
        cifrado pelo próprio túnel.
        """
        p = urlparse(valor if "://" in valor else f"http://{valor}")
        if p.scheme in ("https", "wss"):
            return valor
        host = (p.hostname or "").strip().lower()
        if host in {"127.0.0.1", "localhost", "::1"}:
            return valor
        raise ValueError(f"server: {valor!r} manda a credencial deste worker em claro pela rede. Use https:// "
                         "(com server.tls_cert no central, ou um proxy TLS na frente dele). http:// só vale "
                         "para 127.0.0.1, que é o alvo do túnel SSH.")

    def ssl_context(self) -> ssl.SSLContext | None:
        """O contexto TLS da conexão com o central, ou `None` quando a conexão é `ws://` (loopback/túnel).

        `create_default_context` verifica cadeia E nome do host — é o padrão, e continua sendo. `ca_file` só
        ACRESCENTA a autoridade própria do parque; ele não desliga verificação nenhuma, e de propósito não
        existe opção para desligá-la: a única razão para querê-la seria aceitar um certificado que não confere,
        que é exatamente o ataque de que este item trata.
        """
        p = urlparse(self.server if "://" in self.server else f"http://{self.server}")
        if p.scheme not in ("https", "wss"):
            return None
        if self.ca_file:
            caminho = Path(self.ca_file)
            if not caminho.is_file():
                raise ValueError(f"ca_file: {self.ca_file!r} não é um arquivo legível.")
            return ssl.create_default_context(cafile=str(caminho))
        return ssl.create_default_context()

    @property
    def paths(self) -> dict[str, str]:
        base = Path(self.work_dir)
        return {"data_dir": str(base), "avd_home": str(base / "avd"), "logs_dir": str(base / "logs"),
                "evidence_dir": str(base / "evidence"), "apk_dirs": [str(base / "apks")],  # type: ignore[dict-item]
                "apk_inbox": str(base / "apks" / "inbox"), "apk_catalog": str(base / "apks")}

    def to_config(self) -> Config:
        """Monta o `Config` que `SdkTools`, `AvdManager` e `emulator` esperam.

        Reaproveitar o mesmo tipo é o que permite usar aqueles módulos sem alteração: eles pedem `cfg.sdk_root`,
        `cfg.avd_home`, `cfg.logs_dir` e `cfg.file.android`, e nada além disso.
        """
        arquivo = AppConfigFile.model_validate({
            "paths": self.paths,
            "android": {**{"sdk_root": self.sdk_root}, **self.android},
            # Uma instância por aparelho declarado, só para o `count` ser coerente; o agente não usa esse bloco.
            "instances": {"count": max(1, len(self.devices))},
            "appium": {"autostart": False},
        })
        env = EnvSettings(_env_file=None, AI_PROVIDER="simulated")  # type: ignore[call-arg]
        cfg = Config(arquivo, env, root=Path(self.work_dir))
        cfg.ensure_dirs()
        return cfg

    def device(self, instance_id: str) -> DeviceSpec | None:
        return next((d for d in self.devices if d.instance_id == instance_id), None)

    # ------------------------------------------------------------------ credencial
    def credential_path(self) -> Path:
        return Path(self.work_dir) / CREDENTIAL_FILE

    def read_credential(self) -> str | None:
        caminho = self.credential_path()
        if not caminho.exists():
            return None
        try:
            return json.loads(caminho.read_text(encoding="utf-8")).get("credential") or None
        except (OSError, ValueError):
            return None

    def write_credential(self, credential: str) -> None:
        caminho = self.credential_path()
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text(json.dumps({"worker_id": self.worker_id, "credential": credential}), encoding="utf-8")
        restringir_acesso(caminho)


def restringir_acesso(caminho: Path) -> bool:
    """Deixa o arquivo legível só para quem administra a máquina. Devolve `True` quando conseguiu.

    Existe porque a versão anterior confiava na herança: `if os.name != "nt": chmod(0o600)`, com o comentário de que
    "no Windows a herança de ACL da pasta de trabalho é quem protege". Medido na máquina do worker, a herança de
    `C:\\farm` dava `BUILTIN\\Users:(I)(RX)` — ou seja, **qualquer usuário local lia a credencial permanente do
    worker**, incluindo contas de serviço de CI que rodam na mesma máquina. E `docs/worker.md` prometia "arquivo de
    permissão restrita", que era verdade só fora do Windows.

    `/inheritance:r` corta a herança (sem isso, `/grant` só ACRESCENTA e o `Users` herdado continua lá) e
    `/grant:r` reescreve as permissões de cada conta. Concedidas por **SID** e não por nome: `Administrators` se
    chama outra coisa em Windows não-inglês, e o script quebraria calado justamente onde ninguém testa.

    Falha não derruba o agente: a credencial já foi gravada e o canal precisa subir. Mas fica registrada como erro,
    porque um arquivo de segredo com permissão frouxa é uma coisa que o operador tem de saber.
    """
    if os.name != "nt":
        caminho.chmod(0o600)
        return True
    contas = ["*S-1-5-18:F",       # NT AUTHORITY\SYSTEM — o serviço, quando o agente roda como serviço
              "*S-1-5-32-544:F"]   # BUILTIN\Administrators
    try:
        contas.append(f"{getpass.getuser()}:F")       # o usuário que roda o agente hoje, que pode não ser admin
    except Exception:  # noqa: BLE001 - sem nome de usuário ainda dá para trancar para SYSTEM/Administrators
        pass
    argumentos = [str(caminho), "/inheritance:r"]
    for conta in contas:
        argumentos += ["/grant:r", conta]
    try:
        r = subprocess.run(["icacls", *argumentos], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        log.error("não foi possível restringir a ACL de %s: %s", caminho.name, exc)
        return False
    if r.returncode != 0:
        # Só o nome do arquivo e o código: a saída do icacls não tem segredo, mas o conteúdo do arquivo tem e o
        # hábito de despejar saída de comando em log é como ele acaba lá.
        log.error("icacls devolveu %s ao restringir %s; a credencial pode estar legível para usuários locais",
                  r.returncode, caminho.name)
        return False
    return True


def load_settings(path: str | Path) -> WorkerSettings:
    dados = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return WorkerSettings.model_validate(dados)


def host_os() -> tuple[str, str]:
    return platform.system().lower(), platform.version()


#: O que o agente declara em `Hello.accel`. Fora do Linux é `None` ("não se sabe"): o WHPX do Windows só se
#: confere rodando `emulator -accel-check`, que custa um processo, e afirmar "whpx" sem medir seria dado falso.
KVM = "kvm"
KVM_INACESSIVEL = "kvm-inacessivel"
KVM_AUSENTE = "kvm-ausente"


def aceleracao_do_host() -> str | None:
    """Aceleração de virtualização deste host, do jeito mais barato que existe: um `stat` em `/dev/kvm`.

    Sem KVM o emulador do Android ou não sobe, ou sobe em emulação de software e um boot que leva 2 min passa a
    levar dezenas de minutos — e hoje o operador só descobre isso pelo comando que estourou o prazo, na outra
    máquina. Declarado no `hello`, vira uma linha no cartão da Infraestrutura antes de qualquer aparelho subir.

    `kvm-inacessivel` é o caso comum e o mais confuso de diagnosticar: o dispositivo existe, mas a conta que roda
    o agente não está no grupo `kvm` — é exatamente o que a unidade systemd de exemplo resolve com
    `SupplementaryGroups=kvm`.
    """
    if os.name == "nt":
        return None
    caminho = Path("/dev/kvm")
    try:
        if not caminho.exists():
            return KVM_AUSENTE
        return KVM if os.access(caminho, os.R_OK | os.W_OK) else KVM_INACESSIVEL
    except OSError:
        return KVM_AUSENTE
