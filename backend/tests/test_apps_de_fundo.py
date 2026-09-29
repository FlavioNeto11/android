"""Apps de fundo desativados no preparo dos aparelhos da automação (`android.desativar_apps`).

Medido em 29/09/2026 no central: no android-06, o app Google ocupava 101 MB, o GMS persistente 74 MB, o Android System
Intelligence 37 MB e o Mensagens 22 MB; no android-04, 2,7 h depois de reiniciar, load 14,7, 82 MB livres, 500 MB de
zram e o `kcompactd0` com 43% de CPU, com YouTube, YouTube Music, Gmail e Bem-estar digital subindo sozinhos — num
convidado de 2 GB. O preparo passa a desativar (`pm disable-user --user 0`) uma lista configurável, nunca o que a
automação usa.

Aqui, sem aparelho: o convidado é um shell falso que guarda o estado dos pacotes e o marcador, e responde como o `pm`.
Prova `simulated`.
"""
from __future__ import annotations

import re
import subprocess
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.config import AndroidCfg, AppConfigFile, load_config
from app.devices import apps_de_fundo as apps
from app.devices.adb import Adb, AdbTimeout
from app.devices.manager import DeviceRuntime
from app.models import InstanceState

from .conftest import Harness
from .test_configuracao_de_exemplo import EXEMPLO
from .test_prontidao_subsistemas import _externo, _readocao_local

YOUTUBE = "com.google.android.youtube"
GOOGLE = "com.google.android.googlequicksearchbox"
GMAIL = "com.google.android.gm"
MAPS = "com.google.android.apps.maps"

#: Uma amostra de pacotes de sistema que a imagem `google_apis` tem de verdade (e que ninguém lista).
SISTEMA = ("android.ext.shared", "com.android.systemui", "com.android.vending", "com.google.android.gms",
           "com.google.android.gsf", "com.google.android.webview", "com.google.android.inputmethod.latin",
           "com.google.android.apps.nexuslauncher", "com.android.chrome", "com.instagram.android",
           "io.appium.uiautomator2.server", "io.appium.settings")


class ConvidadoFalso:
    """O shell do aparelho, do ponto de vista do host: cada `adb shell` é uma linha de comandos separados por `;`,
    executados em ordem, com a saída que o convidado imprimiria. Guarda o estado dos pacotes e o marcador."""

    def __init__(self, habilitados: set[str], desativados: set[str] | None = None,
                 marcador: set[str] | None = None) -> None:
        self.habilitados = set(habilitados)
        self.desativados = set(desativados or ())
        self.marcador: set[str] | None = set(marcador) if marcador is not None else None
        self.comandos: list[str] = []
        #: Pacotes cujo `pm disable-user` o convidado recusa (pacote protegido pelo sistema, por exemplo).
        self.recusa: set[str] = set()
        #: Estoura o prazo na chamada que contiver este trecho.
        self.estoura_em: str | None = None
        self.pm_fora_do_ar = False

    def __call__(self, args: list[str], *, timeout: float = 30, binary: bool = False,
                 entrada: str | None = None) -> subprocess.CompletedProcess[str]:
        assert args[:1] == ["shell"] and len(args) == 2, args
        linha = args[1]
        self.comandos.append(linha)
        if self.estoura_em is not None and self.estoura_em in linha:
            raise AdbTimeout(f"adb shell excedeu {timeout}s em emulator-5640")
        saida: list[str] = []
        for parte in (p.strip() for p in linha.split(";")):
            saida.extend(self._um(parte))
        return subprocess.CompletedProcess(args, 0, "".join(f"{s}\n" for s in saida), "")

    def _um(self, cmd: str) -> list[str]:
        cmd = cmd.removesuffix("2>&1").strip()
        if cmd.startswith("echo "):
            return [cmd[5:].strip().strip("'")]
        if cmd.startswith("pm list packages"):
            if self.pm_fora_do_ar:
                return ["cmd: Can't find service: package"]
            fonte = self.desativados if " -d" in cmd else self.habilitados
            return [f"package:{p}" for p in sorted(fonte)]
        if cmd.startswith(f"cat {apps.MARCADOR}"):
            return sorted(self.marcador or ())
        if m := re.fullmatch(r"pm disable-user --user 0 (\S+)", cmd):
            p = m.group(1)
            if p in self.recusa:
                return [f"Error: java.lang.SecurityException: Cannot disable a protected package: {p}"]
            if p not in self.habilitados | self.desativados:
                return ["Exception occurred while executing 'disable-user':",
                        f"java.lang.IllegalArgumentException: Unknown package: {p}"]
            self.habilitados.discard(p)
            self.desativados.add(p)
            return [f"Package {p} new state: disabled-user"]
        if m := re.fullmatch(r"pm enable --user 0 (\S+)", cmd):
            p = m.group(1)
            self.desativados.discard(p)
            self.habilitados.add(p)
            return [f"Package {p} new state: enabled"]
        if cmd.startswith("printf '%s\\n' ") and cmd.endswith(f"> {apps.MARCADOR}"):
            self.marcador = set(cmd[len("printf '%s\\n' "):-len(f"> {apps.MARCADOR}")].split())
            return []
        if cmd == f"rm -f {apps.MARCADOR}":
            self.marcador = None
            return []
        # Os ajustes do preparo (`settings put`, `svc`, `wm`, `input keyevent`…): o convidado aceita calado.
        assert cmd.startswith(("settings ", "svc ", "locksettings ", "input keyevent ", "wm ")), cmd
        return []

    def pm(self, verbo: str) -> list[str]:
        """Os pacotes em que o host mandou `pm <verbo>`, em ordem."""
        return [p for c in self.comandos for p in re.findall(rf"pm {verbo} --user 0 (\S+)", c)]


def _adb(convidado: ConvidadoFalso, lista: tuple[str, ...] | None) -> Adb:
    adb = Adb(SimpleNamespace(), "emulator-5640", apps_de_fundo=lista)  # type: ignore[arg-type]  - `_run` é o dublê
    adb._run = convidado                                                   # type: ignore[method-assign]
    return adb


# ================================================================== a configuração
def test_o_padrao_e_o_conservador_e_nada_nele_e_protegido() -> None:
    assert AndroidCfg().desativar_apps == list(apps.PADRAO)
    assert {GOOGLE, "com.google.android.as", "com.google.android.apps.messaging", YOUTUBE,
            "com.google.android.apps.youtube.music", GMAIL, "com.google.android.apps.wellbeing"} <= set(apps.PADRAO)
    assert [p for p in apps.PADRAO if apps.protecao(p)] == []
    assert all(apps.PACOTE_RE.match(p) for p in apps.PADRAO)


def test_o_exemplo_traz_o_mesmo_padrao_do_modelo() -> None:
    """O `config.example.yaml` mostra a lista por extenso, e ela não pode divergir do padrão do código."""
    assert load_config(EXEMPLO).file.android.desativar_apps == list(apps.PADRAO)


@pytest.mark.parametrize("pacote", [
    "com.android.vending", "com.google.android.gms", "com.google.android.gms.policy_sidecar_aps",
    "com.google.android.gsf", "com.google.android.gsf.login", "com.google.android.webview", "com.android.webview",
    "com.google.android.inputmethod.latin", "com.google.android.apps.nexuslauncher", "com.android.launcher3",
    "com.android.systemui", "com.android.chrome", "com.instagram.android", "com.instagram.lite",
    "io.appium.uiautomator2.server", "io.appium.uiautomator2.server.test", "io.appium.settings",
])
def test_pacote_protegido_na_lista_e_recusado_na_carga(pacote: str) -> None:
    assert apps.protecao(pacote)
    with pytest.raises(ValidationError) as erro:
        AndroidCfg(desativar_apps=[YOUTUBE, pacote])
    assert pacote in str(erro.value) and "protegido" in str(erro.value)


@pytest.mark.parametrize("bruto", ["youtube", "com.google.android.youtube; reboot", "com..gm", "1com.x.y", ""])
def test_nome_que_nao_e_pacote_e_recusado(bruto: str) -> None:
    with pytest.raises(ValidationError):
        AndroidCfg(desativar_apps=[bruto])


def test_lista_repetida_vira_uma_so_e_vazia_vale() -> None:
    assert AndroidCfg(desativar_apps=[YOUTUBE, f" {YOUTUBE} ", GMAIL]).desativar_apps == [YOUTUBE, GMAIL]
    assert AndroidCfg(desativar_apps=[]).desativar_apps == []


def test_protegido_no_override_de_uma_instancia_e_recusado() -> None:
    with pytest.raises(ValidationError) as erro:
        AppConfigFile.model_validate({"instances": {"count": 2, "overrides": {
            "android-02": {"desativar_apps": [YOUTUBE, "com.android.systemui"]}}}})
    assert "com.android.systemui" in str(erro.value)


def test_app_alvo_declarado_na_configuracao_nao_pode_ser_desativado() -> None:
    """O app alvo (o que a automação abre) nunca sai: nem o do catálogo semeado, nem o de uma conta gerenciada."""
    with pytest.raises(ValidationError) as erro:
        AppConfigFile.model_validate({"apps": [{"id": "yt", "name": "YouTube", "package": YOUTUBE}]})
    assert YOUTUBE in str(erro.value) and "alvo" in str(erro.value)
    with pytest.raises(ValidationError) as erro:
        AppConfigFile.model_validate({"contas": {"sessao": {GMAIL: {"settle_s": 5}}}})
    assert GMAIL in str(erro.value)
    with pytest.raises(ValidationError):
        AppConfigFile.model_validate({"apps": [{"id": "maps", "name": "Maps", "package": MAPS}],
                                      "instances": {"count": 2, "overrides": {
                                          "android-02": {"desativar_apps": [MAPS]}}}})
    # Tirado da lista, o mesmo app volta a ser aceito como alvo.
    AppConfigFile.model_validate({"apps": [{"id": "yt", "name": "YouTube", "package": YOUTUBE}],
                                  "android": {"desativar_apps": [GMAIL]}})


# ================================================================== o preparo
def test_sem_lista_o_preparo_nao_toca_em_app_nenhum() -> None:
    """`None` é "não é aparelho da automação" (celular físico, o agente do worker): nem leitura de pacote."""
    convidado = ConvidadoFalso({*SISTEMA, YOUTUBE})
    assert _adb(convidado, None).prepare_for_automation() is None
    assert len(convidado.comandos) == 1 and "settings put global hide_error_dialogs 1" in convidado.comandos[0]
    assert not any("pm " in c for c in convidado.comandos)


def test_preparo_desativa_so_a_lista_e_registra() -> None:
    convidado = ConvidadoFalso({*SISTEMA, YOUTUBE, GOOGLE, GMAIL})
    ajuste = _adb(convidado, (GOOGLE, YOUTUBE, GMAIL, MAPS)).prepare_for_automation()
    # Os ajustes de sempre vêm PRIMEIRO, na chamada de sempre.
    assert "settings put global hide_error_dialogs 1" in convidado.comandos[0]
    assert convidado.pm("disable-user") == [GOOGLE, YOUTUBE, GMAIL]
    assert convidado.pm("enable") == []
    assert convidado.desativados == {GOOGLE, YOUTUBE, GMAIL}
    assert isinstance(ajuste, apps.AjusteDosApps)
    assert ajuste.desativados == (GOOGLE, YOUTUBE, GMAIL)
    assert ajuste.ausentes == (MAPS,)            # a imagem não tem: nada a fazer, e não é falha
    assert ajuste.falhas == () and ajuste.incerto == "" and ajuste.mudou
    assert YOUTUBE in ajuste.resumo() and "desativad" in ajuste.resumo()
    # O registro no aparelho: é por ele que o preparo sabe o que ELE desativou (e só isso reativa).
    assert convidado.marcador == {GOOGLE, YOUTUBE, GMAIL}


def test_preparo_e_idempotente_a_segunda_vez_so_le() -> None:
    convidado = ConvidadoFalso({*SISTEMA, YOUTUBE, GOOGLE})
    adb = _adb(convidado, (GOOGLE, YOUTUBE))
    adb.prepare_for_automation()
    antes = len(convidado.comandos)
    ajuste = adb.prepare_for_automation()
    novos = convidado.comandos[antes:]
    assert len(novos) == 2, novos                # os ajustes de sempre + UMA leitura; nenhuma escrita
    assert not any("disable-user" in c or "pm enable" in c or "printf" in c for c in novos)
    assert ajuste is not None and not ajuste.mudou
    assert ajuste.ja_desativados == (GOOGLE, YOUTUBE)


def test_protegido_que_chega_a_lista_por_codigo_nunca_e_desativado() -> None:
    """A recusa da carga é a primeira barreira; o `Adb` confere de novo antes de mandar o `pm`."""
    convidado = ConvidadoFalso({*SISTEMA, YOUTUBE})
    protegidos = ("com.android.vending", "com.google.android.gms", "com.android.systemui", "com.android.chrome",
                  "com.instagram.android", "io.appium.uiautomator2.server", "com.google.android.webview",
                  "com.google.android.inputmethod.latin", "com.google.android.apps.nexuslauncher")
    ajuste = _adb(convidado, (*protegidos, YOUTUBE)).prepare_for_automation()
    assert convidado.pm("disable-user") == [YOUTUBE]
    assert ajuste is not None and set(ajuste.recusados) == set(protegidos)
    assert convidado.habilitados >= set(protegidos)


def test_tirar_da_lista_reativa_so_o_que_o_preparo_desativou() -> None:
    """Reversível: o que sai da lista volta com `pm enable`. O que a pessoa desativou à mão (fora do marcador) fica."""
    convidado = ConvidadoFalso({*SISTEMA}, desativados={YOUTUBE, GMAIL, MAPS}, marcador={YOUTUBE, GMAIL})
    ajuste = _adb(convidado, (GMAIL,)).prepare_for_automation()
    assert convidado.pm("enable") == [YOUTUBE]
    assert convidado.pm("disable-user") == []
    assert YOUTUBE in convidado.habilitados and MAPS in convidado.desativados
    assert ajuste is not None and ajuste.reativados == (YOUTUBE,) and ajuste.mudou
    assert convidado.marcador == {GMAIL}


def test_lista_vazia_devolve_tudo_o_que_foi_desativado() -> None:
    convidado = ConvidadoFalso({*SISTEMA}, desativados={YOUTUBE, GMAIL}, marcador={YOUTUBE, GMAIL})
    ajuste = _adb(convidado, ()).prepare_for_automation()
    assert sorted(convidado.pm("enable")) == [GMAIL, YOUTUBE]
    assert convidado.marcador is None             # nada mais é nosso: o marcador sai
    assert ajuste is not None and set(ajuste.reativados) == {GMAIL, YOUTUBE}


def test_lista_vazia_sem_marcador_so_le() -> None:
    convidado = ConvidadoFalso({*SISTEMA}, desativados={YOUTUBE})
    ajuste = _adb(convidado, ()).prepare_for_automation()
    assert convidado.pm("enable") == [] and len(convidado.comandos) == 2
    assert ajuste is not None and not ajuste.mudou


def test_recusa_do_convidado_e_falha_registrada_e_nao_entra_no_marcador() -> None:
    convidado = ConvidadoFalso({*SISTEMA, YOUTUBE, GMAIL})
    convidado.recusa = {GMAIL}
    ajuste = _adb(convidado, (YOUTUBE, GMAIL)).prepare_for_automation()
    assert ajuste is not None and ajuste.desativados == (YOUTUBE,)
    assert len(ajuste.falhas) == 1 and ajuste.falhas[0].startswith(f"{GMAIL}: ")
    assert convidado.marcador == {YOUTUBE}


def test_prazo_estourado_nos_apps_nao_derruba_o_preparo_e_fica_incerto() -> None:
    """`pm disable-user` atrasado só mata um app de fundo da lista — nunca o alvo, o launcher ou o SystemUI, que são
    protegidos. Por isso o estouro AQUI vira registro `incerto` e não o estouro do preparo (que é da chamada dos
    ajustes, e continua levantando)."""
    convidado = ConvidadoFalso({*SISTEMA, YOUTUBE})
    convidado.estoura_em = "pm disable-user"
    ajuste = _adb(convidado, (YOUTUBE,)).prepare_for_automation()
    assert ajuste is not None and ajuste.incerto and not ajuste.desativados
    assert convidado.marcador is None             # nada confirmado, nada registrado como nosso


def test_prazo_estourado_nos_ajustes_de_sempre_continua_levantando() -> None:
    convidado = ConvidadoFalso({*SISTEMA, YOUTUBE})
    convidado.estoura_em = "settings put"
    with pytest.raises(AdbTimeout):
        _adb(convidado, (YOUTUBE,)).prepare_for_automation()
    assert not any("pm " in c for c in convidado.comandos)


def test_pm_fora_do_ar_nao_e_lista_vazia() -> None:
    """Sem o `pm`, não se sabe o que está instalado: nada é desativado nem reativado, e fica registrado."""
    convidado = ConvidadoFalso({*SISTEMA, YOUTUBE}, desativados={GMAIL}, marcador={GMAIL})
    convidado.pm_fora_do_ar = True
    ajuste = _adb(convidado, (YOUTUBE,)).prepare_for_automation()
    assert convidado.pm("disable-user") == [] and convidado.pm("enable") == []
    assert ajuste is not None and ajuste.falhas and not ajuste.mudou


def test_sem_tempo_no_preparo_os_apps_ficam_para_o_proximo() -> None:
    convidado = ConvidadoFalso({*SISTEMA, YOUTUBE})
    ajuste = _adb(convidado, (YOUTUBE,)).ajustar_apps_de_fundo((YOUTUBE,), prazo_s=0)
    assert convidado.comandos == [] and ajuste.incerto


# ================================================================== o central escolhe a lista de cada aparelho
async def test_central_aplica_a_lista_menos_o_catalogo_no_emulador_local(harness: Harness,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    s = harness.state
    assert s is not None
    rt = _readocao_local(harness, monkeypatch)
    s.devices.cfg.file.android.desativar_apps = [GOOGLE, YOUTUBE, GMAIL]
    s.db.execute("INSERT INTO apps(id, name, package) VALUES (?,?,?)", ("yt", "YouTube", YOUTUBE))
    vistos: list[tuple[str, ...] | None] = []
    publicados: list[str] = []

    def preparo() -> apps.AjusteDosApps:
        vistos.append(rt.adb.apps_de_fundo)
        return apps.AjusteDosApps(desativados=(GOOGLE, GMAIL))
    monkeypatch.setattr(rt.adb, "prepare_for_automation", preparo)
    original = s.devices.publish

    def publica(rt_: DeviceRuntime, message: str | None = None, level: str = "info") -> None:
        if message:
            publicados.append(message)
        original(rt_, message, level)
    monkeypatch.setattr(s.devices, "publish", publica)
    await s.devices._adopt(rt)
    assert rt.state == InstanceState.online
    # O app do catálogo é alvo: sai da lista deste aparelho, mesmo estando no `config.yaml`.
    assert vistos and vistos[-1] == (GOOGLE, GMAIL)
    assert any(GOOGLE in m and GMAIL in m for m in publicados), publicados


async def test_central_nao_mexe_nos_apps_do_celular_fisico(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    s = harness.state
    assert s is not None
    rt = _externo(harness, monkeypatch)
    rt.worker_id = None
    assert s.devices.apps_de_fundo_de(rt) is None
    rt.worker_id = "notebook"                     # aparelho de worker, pelo túnel: é o central quem prepara
    assert s.devices.apps_de_fundo_de(rt) == tuple(apps.PADRAO)


async def test_a_loja_nao_tem_app_desativado(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """A loja não é aparelho da automação: lista vazia — o que o preparo tinha desativado ali volta."""
    s = harness.state
    assert s is not None
    rt = s.devices.get("android-01")
    monkeypatch.setattr(rt, "store", True)
    assert s.devices.apps_de_fundo_de(rt) == ()
