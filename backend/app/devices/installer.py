"""Instalação de release num aparelho, com compatibilidade conferida antes e estado observado depois.

Nenhuma outra parte do projeto chama `adb install` direto: a decisão entre arquivo único e conjunto de splits, a
conferência de compatibilidade e a verificação no aparelho ficam todas aqui. Os comandos são montados como lista de
argumentos e executados sem shell, pelo `SdkTools.run` que já existe.

A frase acima já foi falsa: o verbo `install_apk` do painel instalava direto de `apps.apk_path`, sem hash, sem
assinatura aprovada, sem canário e sem gravar `device_app_state` — o mesmo pacote entrava por dois caminhos e o
painel continuava descrevendo o antigo (#83). Ele agora resolve a versão PROMOVIDA e cai aqui, como todo o resto.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..models import InstalledAppState
from .adb import AdbError, AdbTimeout

log = logging.getLogger("poc.installer")

# A primeira abertura depois de instalar é o pior caso: o Android ainda prepara o código e o emulador desenha por
# software. Medido no Instagram 385311929 em android-01: 25,4 s até o primeiro quadro (~8 s numa segunda abertura a
# frio). O prazo é teto, não espera — a sonda devolve assim que o app aparece e se mantém.
LAUNCH_DEADLINE_S = 90.0
# Depois de aparecer, quanto tempo o app precisa continuar em primeiro plano. Instalar não prova que roda —
# especialmente com biblioteca nativa traduzida de arm64 para x86_64.
LAUNCH_SETTLE_S = 6.0
LAUNCH_POLL_S = 2.0
#: Prazos padrão de leitura e de instalação (segundos). A configuração (`releases.*`) manda sobre eles.
PROFILE_TIMEOUT_S = 30.0
INSPECT_TIMEOUT_S = 40.0
INSTALL_TIMEOUT_S = 900.0
# Quantas vezes a sondagem tenta dispensar um diálogo do sistema antes de desistir. Duas: uma para o diálogo
# que já estava na tela, outra para um que apareça logo depois. Mais do que isso seria insistir numa tela presa.
DISPENSAS_DE_DIALOGO = 2


class InstallError(RuntimeError):
    """Falha de instalação já traduzida para linguagem humana."""


class DowngradeRefused(InstallError):
    """O Android recusou instalar por cima uma versão mais antiga preservando os dados.

    Existe como tipo próprio porque a saída é uma decisão de pessoa, não uma repetição: reinstalar resolve, mas
    apaga os dados do app — e com eles a sessão.
    """


#: Faixas de densidade do Android, pelos PONTOS MÉDIOS entre as faixas oficiais (120/160/240/320/480/640).
#: Tabela única do projeto: havia duas, com limites diferentes — 190 dpi virava `mdpi` para escolher o split e
#: `hdpi` para escolher a variante de receita; 400 dpi, `xhdpi` numa e `xxhdpi` na outra. Duas respostas para a
#: mesma pergunta é uma a mais.
_FAIXAS_DE_DENSIDADE = ((140, "ldpi"), (200, "mdpi"), (280, "hdpi"), (400, "xhdpi"), (560, "xxhdpi"))


def density_bucket(density: int | None, *, desconhecida: str = "mdpi") -> str:
    """A faixa de densidade deste aparelho. `desconhecida` é o que responder quando o aparelho não disse."""
    if not density:
        return desconhecida
    return next((nome for limite, nome in _FAIXAS_DE_DENSIDADE if density <= limite), "xxxhdpi")


@dataclass(slots=True)
class DeviceProfile:
    """O que o aparelho responde sobre si mesmo. Lido, nunca suposto."""

    abi: str
    abis: list[str]
    sdk: int | None
    locale: str
    density: int | None

    @property
    def density_bucket(self) -> str:
        """Faixa de densidade, que é como os splits do Android são nomeados. Uma tabela só, `density_bucket`."""
        return density_bucket(self.density)


@dataclass(slots=True)
class Compatibility:
    verdict: str                      # compatible | incompatible | uncertain
    reason: str
    translated_abi: str | None = None

    @property
    def blocked(self) -> bool:
        return self.verdict == "incompatible"


@dataclass(slots=True)
class SplitChoice:
    """Quais splits deste conjunto fazem sentido NESTE aparelho."""

    chosen: list[str]                 # nomes de split escolhidos (o base é implícito e nunca entra aqui)
    skipped: list[str]
    reason: str

    @property
    def filtered(self) -> bool:
        return bool(self.skipped)


# Nomes que o Android usa nos splits de configuração. Reconhecer é o que permite descartar; o que não é reconhecido
# nunca é descartado.
_DENSIDADES = ("ldpi", "mdpi", "tvdpi", "hdpi", "xhdpi", "xxhdpi", "xxxhdpi")
_SEM_DENSIDADE = ("nodpi", "anydpi")
_ABIS = ("armeabi", "armeabi_v7a", "arm64_v8a", "x86", "x86_64", "mips", "mips64", "riscv64")
_IDIOMA = re.compile(r"^[a-z]{2,3}(?:[_-][A-Za-z]{2,4})?$")


def _config_token(split_name: str) -> str | None:
    """`config.arm64_v8a` e `feature.config.pt` viram `arm64_v8a` e `pt`. Sem `config.`, não é split de configuração."""
    marca = "config."
    i = split_name.rfind(marca)
    return split_name[i + len(marca):] if i >= 0 else None


def config_kind(split_name: str) -> tuple[str | None, str]:
    """Que tipo de configuração este split atende (`abi`/`density`/`lang`) e qual alvo. Público: a release
    precisa dizer na tela a quem o conjunto serve, e a resposta é a mesma que escolhe os splits."""
    return _config_kind(split_name)


def _config_kind(split_name: str) -> tuple[str | None, str]:
    token = _config_token(split_name)
    if not token:
        return None, ""
    if token in _DENSIDADES:
        return "density", token
    if token in _SEM_DENSIDADE:
        return None, token                      # serve para qualquer densidade: nunca se descarta
    if token.replace("-", "_") in _ABIS:
        return "abi", token.replace("-", "_")
    if _IDIOMA.match(token):
        return "lang", token.split("_")[0].split("-")[0].lower()
    return None, token                          # split de funcionalidade ou nome desconhecido


def select_splits(split_names: list[str], profile: DeviceProfile) -> SplitChoice:
    """Escolhe os splits de configuração que servem a este aparelho. Função pura, testada com nomes sintéticos.

    Três regras, todas conservadoras:

    * o que não é reconhecido como split de configuração **sempre entra** — descartar por engano quebra o app;
    * dentro de uma categoria (ABI, densidade, idioma), se **nenhum** candidato serve ao aparelho, entram todos:
      é melhor instalar demais do que instalar um app sem strings ou sem biblioteca nativa;
    * o base nunca aparece aqui — ele entra sempre, por definição.

    **O que já foi exercitado:** o conjunto real do Instagram vindo da Play Store passou por aqui em 18/09 e não
    teve nada a filtrar (a loja entrega à VM só os splits dela) — ou seja, o caminho do DESCARTE não havia sido
    exercitado com um conjunto real. Ele passou a ser, com um conjunto completo no formato que a Play Store
    publica (várias ABIs, cinco densidades e vários idiomas): ver
    `tests/test_release_lifecycle.py::test_conjunto_completo_de_loja_descarta_o_que_nao_serve`.
    """
    grupos: dict[str, list[str]] = {"abi": [], "density": [], "lang": []}
    livres: list[str] = []
    for nome in split_names:
        kind, _ = _config_kind(nome)
        (grupos[kind] if kind else livres).append(nome)

    abis = {a.replace("-", "_") for a in profile.abis}
    idioma = profile.locale.split("-")[0].split("_")[0].lower()
    serve = {
        "abi": lambda t: t in abis,
        "density": lambda t: t == profile.density_bucket,
        # Aparelho sem idioma lido não autoriza descartar nada: sem o sinal, a regra não se aplica.
        "lang": lambda t: not idioma or t == idioma,
    }
    escolhidos: list[str] = []
    descartados: list[str] = []
    motivos: list[str] = []
    for kind, nomes in grupos.items():
        if not nomes:
            continue
        casam = [n for n in nomes if serve[kind](_config_kind(n)[1])]
        if not casam:
            escolhidos.extend(nomes)
            motivos.append(f"nenhum split de {kind} servia ao aparelho; todos foram mantidos")
            continue
        escolhidos.extend(casam)
        descartados.extend(n for n in nomes if n not in casam)
    escolhidos.extend(livres)
    if descartados:
        motivos.insert(0, f"{len(descartados)} split(s) de outra configuração ficaram de fora")
    ordem = {n: i for i, n in enumerate(split_names)}
    return SplitChoice(chosen=sorted(escolhidos, key=lambda n: ordem[n]),
                       skipped=sorted(descartados, key=lambda n: ordem[n]),
                       reason="; ".join(motivos) or "o conjunto inteiro serve a este aparelho")


@dataclass(slots=True)
class InstalledApp:
    """Estado observado no aparelho."""

    present: bool
    version_name: str | None = None
    version_code: int | None = None
    splits: list[str] = field(default_factory=list)
    first_install_time: str | None = None
    last_update_time: str | None = None
    paths: list[str] = field(default_factory=list)


def compatibility(*, min_sdk: int | None, abis: list[str], profile: DeviceProfile) -> Compatibility:
    """Decide ANTES de instalar. Tentar e descobrir pelo erro do ADB seria mais lento e menos informativo."""
    if min_sdk is not None and profile.sdk is not None and min_sdk > profile.sdk:
        return Compatibility("incompatible",
                            f"o pacote exige Android com SDK {min_sdk} e o aparelho tem {profile.sdk}")
    if not abis:
        return Compatibility("compatible", "o pacote não traz biblioteca nativa; qualquer ABI serve")
    if profile.abi in abis:
        return Compatibility("compatible", f"ABI nativa do aparelho ({profile.abi}) está no pacote")
    translated = next((a for a in profile.abis if a in abis), None)
    if translated:
        # Aceitar a instalação não prova que roda: a prova é a sonda de abertura.
        return Compatibility("uncertain",
                             f"só há {translated}, que este aparelho executa por tradução — confirmar abrindo o app",
                             translated_abi=translated)
    return Compatibility("incompatible",
                         f"o pacote traz {', '.join(abis)} e o aparelho aceita {', '.join(profile.abis)}")


class AppInstaller:
    """Opera um aparelho já sob posse de quem chamou (a posse é do scheduler, não deste módulo)."""

    def __init__(self, devices: Any, *, launch_deadline_s: float = LAUNCH_DEADLINE_S,
                 launch_settle_s: float = LAUNCH_SETTLE_S, launch_poll_s: float = LAUNCH_POLL_S,
                 profile_timeout_s: float = PROFILE_TIMEOUT_S, inspect_timeout_s: float = INSPECT_TIMEOUT_S,
                 install_timeout_s: float = INSTALL_TIMEOUT_S):
        self.devices = devices
        self.launch_deadline_s = launch_deadline_s
        self.launch_settle_s = launch_settle_s
        self.launch_poll_s = launch_poll_s
        # Prazos de leitura e de instalação: vêm da configuração (`releases.*_timeout_s`) porque o custo real
        # deles muda com a máquina — no worker remoto, sob disputa de CPU, 30 s de `adb shell` já não bastaram.
        self.profile_timeout_s = profile_timeout_s
        self.inspect_timeout_s = inspect_timeout_s
        self.install_timeout_s = install_timeout_s

    @classmethod
    def from_config(cls, devices: Any, cfg: Any) -> "AppInstaller":
        r = cfg.file.releases
        return cls(devices, launch_deadline_s=r.launch_deadline_s, profile_timeout_s=r.profile_timeout_s,
                   inspect_timeout_s=r.inspect_timeout_s, install_timeout_s=r.install_timeout_s)

    # ------------------------------------------------------------------ leitura
    async def profile(self, rt: Any, *, timeout: float | None = None) -> DeviceProfile:
        timeout = self.profile_timeout_s if timeout is None else timeout
        run = rt.executor.run
        abilist = await run(rt.adb.getprop, "ro.product.cpu.abilist", timeout=timeout, label="abis do aparelho")
        abi = await run(rt.adb.getprop, "ro.product.cpu.abi", timeout=timeout, label="abi do aparelho")
        sdk = await run(rt.adb.getprop, "ro.build.version.sdk", timeout=timeout, label="sdk do aparelho")
        locale = await run(rt.adb.getprop, "ro.product.locale", timeout=timeout, label="idioma do aparelho")
        density = await run(rt.adb.wm_density, timeout=timeout, label="densidade do aparelho")
        return DeviceProfile(
            abi=abi.strip(),
            abis=[a.strip() for a in (abilist or abi).split(",") if a.strip()],
            sdk=int(sdk) if sdk.strip().isdigit() else None,
            locale=locale.strip() or "",
            density=density)

    async def inspect(self, rt: Any, package: str, *, timeout: float | None = None) -> InstalledApp:
        timeout = self.inspect_timeout_s if timeout is None else timeout
        info = await rt.executor.run(rt.adb.package_info, package, timeout=timeout, label="estado do app")
        if not info:
            return InstalledApp(present=False)
        return InstalledApp(present=True, version_name=info["version_name"], version_code=info["version_code"],
                            splits=list(info["splits"]), first_install_time=info["first_install_time"],
                            last_update_time=info["last_update_time"], paths=list(info["paths"]))

    # ------------------------------------------------------------------ escrita
    async def install(self, rt: Any, *, paths: list[Path], timeout: float | None = None,
                      allow_downgrade: bool = False) -> None:
        """Arquivo único usa `install`; conjunto usa `install-multiple`, que é atômico no `pm`.

        `allow_downgrade` acrescenta `-d`, que o Android pode aceitar ou recusar conforme o build. Recusa vira
        `DowngradeRefused`, não uma falha genérica: a saída é decisão de pessoa, nunca repetição.
        """
        if not paths:
            raise InstallError("Release sem arquivos para instalar.")
        timeout = self.install_timeout_s if timeout is None else timeout
        args = [str(p) for p in paths]
        try:
            if len(args) == 1:
                await rt.executor.run(lambda: rt.adb.install(args[0], timeout=timeout - 30,
                                                             allow_downgrade=allow_downgrade),
                                      timeout=timeout, label="instalar APK")
            else:
                await rt.executor.run(lambda: rt.adb.install_multiple(args, timeout=timeout - 30,
                                                                      allow_downgrade=allow_downgrade),
                                      timeout=timeout, label="instalar conjunto de APKs")
        except AdbTimeout:
            # Timeout NÃO é recusa do `pm`: o comando pode ter terminado depois que a leitura da resposta
            # desistiu. Sobe como está — engolido em `InstallError`, ele viraria "falhou" para quem chamou, e era
            # exatamente assim que uma instalação bem-sucedida acabava marcada como `install_failed`.
            raise
        except AdbError as exc:
            texto = str(exc)
            if _recusou_downgrade(texto):
                raise DowngradeRefused(texto) from None
            raise InstallError(texto) from None
        rt.app_versions.clear()          # a versão em cache é a chave das receitas; ela mudou

    async def uninstall(self, rt: Any, package: str, *, timeout: float = 120) -> None:
        try:
            await rt.executor.run(rt.adb.uninstall, package, timeout=timeout, label="desinstalar app")
        except AdbError as exc:
            raise InstallError(str(exc)) from None
        rt.app_versions.clear()

    # ------------------------------------------------------------------ prova de que roda
    async def launch_probe(self, rt: Any, package: str, *, settle_s: float | None = None) -> tuple[bool, str]:
        """Abre o app e confere que ele chega ao primeiro plano e fica lá. É isto que decide se uma ABI traduzida serve.

        Foco nulo é transição — o app desenhando a primeira tela, ou passando de uma tela sua para outra — e nunca
        conta como falha: só o prazo esgota a espera. Outro app em foco ANTES de o nosso aparecer também é espera (o
        launcher segue na frente até o primeiro quadro); DEPOIS de aparecer, é o app que caiu ou se fechou.
        """
        estavel = self.launch_settle_s if settle_s is None else settle_s
        try:
            await rt.executor.run(rt.adb.start_app, package, None, timeout=60, label="abrir app")
        except AdbError as exc:
            return False, f"o app não abriu: {exc}"
        relogio = asyncio.get_running_loop().time
        inicio = relogio()
        limite = inicio + self.launch_deadline_s
        visto_em: float | None = None
        dispensas = 0
        while True:
            try:
                foco = await rt.executor.run(rt.adb.current_focus, timeout=30, label="janela em foco")
            except AdbError:
                foco = (None, None)
            agora = relogio()
            dono = foco[0]
            if dono is None and visto_em is None and dispensas < DISPENSAS_DE_DIALOGO:
                # Foco sem dono é quase sempre transição — mas também é assim que aparece um diálogo do sistema
                # (ANR), que não tem forma `pacote/atividade` e seguraria a tela até o prazo estourar.
                dispensas += 1
                try:
                    if await rt.executor.run(rt.adb.dismiss_system_dialog, timeout=40, label="dispensar diálogo"):
                        await rt.executor.run(rt.adb.start_app, package, None, timeout=60, label="reabrir app")
                except AdbError:
                    pass
            if dono == package:
                if visto_em is None:
                    visto_em = agora
                    # Apareceu perto do prazo: a prova de estabilidade ainda precisa caber, com folga para uma troca
                    # de tela do próprio app no meio.
                    limite = max(limite, visto_em + estavel + 3 * self.launch_poll_s)
                if agora - visto_em >= estavel:
                    break
            elif dono is not None and visto_em is not None:
                return False, (f"o app abriu e saiu do primeiro plano {agora - visto_em:.0f} s depois "
                               f"(foco: {_descreve_foco(foco)})")
            if agora >= limite:
                if visto_em is None:
                    onde = _descreve_foco(foco)
                    if foco[0] is None:
                        # "nenhuma janela" mentia quando havia um diálogo do sistema segurando a tela.
                        try:
                            if dialogo := await rt.executor.run(rt.adb.system_dialog, timeout=20,
                                                                label="diálogo do sistema"):
                                onde = f"diálogo do sistema: {dialogo}"
                        except AdbError:
                            pass
                    return False, (f"o app não chegou ao primeiro plano em {agora - inicio:.0f} s "
                                   f"(foco: {onde})")
                return False, f"o app apareceu, mas não se firmou em primeiro plano (foco: {_descreve_foco(foco)})"
            espera = self.launch_poll_s
            if visto_em is not None and (falta := visto_em + estavel - agora) > 0:
                espera = min(espera, falta)
            await asyncio.sleep(min(espera, max(limite - agora, 0.0)))
        alive = await rt.executor.run(rt.adb.is_installed, package, timeout=30, label="pacote presente")
        if not alive:
            return False, "o pacote sumiu depois de abrir"
        return True, f"app chegou ao primeiro plano em {visto_em - inicio:.0f} s e permaneceu"


async def wait_for_focus(rt: Any, package: str, *, deadline_s: float, poll_s: float = LAUNCH_POLL_S) -> bool:
    """Espera o app ter janela em foco, até o prazo. Não julga nada: quem chama decide o que fazer com a tela."""
    relogio = asyncio.get_running_loop().time
    limite = relogio() + deadline_s
    while True:
        try:
            dono, _ = await rt.executor.run(rt.adb.current_focus, timeout=30, label="janela em foco")
        except AdbError:
            dono = None
        if dono == package:
            return True
        agora = relogio()
        if agora >= limite:
            return False
        await asyncio.sleep(min(poll_s, limite - agora))


def _descreve_foco(foco: tuple[str | None, str | None]) -> str:
    pacote, atividade = foco
    if not pacote:
        return "nenhuma janela"
    return f"{pacote}/{atividade}"[:100] if atividade else pacote


def _recusou_downgrade(texto: str) -> bool:
    """O Android nomeia essa recusa de mais de um jeito; a decisão a jusante é a mesma nos dois."""
    baixo = texto.lower()
    return "version_downgrade" in baixo or "downgrade" in baixo


def drift_of(observed: InstalledApp, *, expected_version_code: int | None,
             expected_splits: list[str] | None = None) -> tuple[InstalledAppState, str | None, str | None]:
    """Compara o que o aparelho respondeu com o que o banco esperava. O banco nunca ganha essa disputa."""
    if not observed.present:
        return InstalledAppState.missing, "app_missing", "O pacote não está instalado neste aparelho."
    if expected_version_code is None:
        return InstalledAppState.installed, None, None
    if observed.version_code != expected_version_code:
        direction = "maior" if (observed.version_code or 0) > expected_version_code else "menor"
        return (InstalledAppState.version_drift, "app_version_drift",
                f"O aparelho tem versionCode {observed.version_code} ({direction} que o esperado "
                f"{expected_version_code}); receitas da versão anterior ficam bloqueadas até reconciliar.")
    if expected_splits:
        missing = sorted(set(expected_splits) - set(observed.splits))
        if missing:
            return (InstalledAppState.version_drift, "split_mismatch",
                    f"Faltam splits no aparelho: {', '.join(missing)}.")
    return InstalledAppState.installed, None, None
