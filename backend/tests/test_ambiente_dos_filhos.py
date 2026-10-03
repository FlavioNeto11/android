"""29.47: nenhum segredo do backend chega aos processos filhos (adb, emulador, Appium, sing-box, Diagnóstico).

O qemu herdava o ambiente inteiro do backend, `TYPESAFE_API_KEY` inclusive (K-078, rodada por adição). Os testes põem
variáveis SENTINELA com nomes de segredo no ambiente deste processo (valores falsos) e olham só os NOMES que chegam:
nenhum valor é lido nem impresso, nem o das sentinelas.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.devices import diagnostics
from app.devices.rede_servidor import ProcessosReais
from app.devices.sdk import ambiente_dos_filhos, SdkTools

#: Os nomes dos segredos do `.env` e das contas, mais dois genéricos que só a 2ª trava (nome com cara de segredo) pega.
SENTINELAS = ("TYPESAFE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "FARM_WORKER_TOKEN",
              "FARM_QUALQUER_COISA", "SERVICO_X_TOKEN", "ANDROID_X_SECRET", "ADB_PRIVATE_KEY")
#: O filho imprime os NOMES do próprio ambiente, nunca os valores.
NOMES_DO_FILHO = "import json, os; print(json.dumps(sorted(os.environ)))"


@pytest.fixture
def com_sentinelas(monkeypatch: pytest.MonkeyPatch) -> None:
    for nome in SENTINELAS:
        monkeypatch.setenv(nome, "sentinela-falsa")


def _sdk(tmp_path: Path) -> SdkTools:
    # SdkTools só lê `sdk_root` e `avd_home` da configuração
    return SdkTools(SimpleNamespace(sdk_root=tmp_path / "sdk", avd_home=tmp_path / "avd"))  # type: ignore[arg-type]


def _nomes(saida: str) -> set[str]:
    return {n.upper() for n in json.loads(saida.strip().splitlines()[-1])}


def _pelo_sdk(tmp_path: Path) -> set[str]:
    return _nomes(_sdk(tmp_path).run([sys.executable, "-c", NOMES_DO_FILHO], timeout=60, check=True).stdout)


def _pelo_sing_box(tmp_path: Path) -> set[str]:
    processos, saida = ProcessosReais(), tmp_path / "sing-box.log"
    pid = processos.lancar([sys.executable, "-c", NOMES_DO_FILHO], cwd=tmp_path, saida=saida)
    processos._filhos[pid].wait(timeout=60)
    return _nomes(saida.read_text(encoding="utf-8"))


def _pelo_diagnostico(tmp_path: Path) -> set[str]:
    return _nomes(diagnostics._run([sys.executable, "-c", NOMES_DO_FILHO], timeout=60))


@pytest.mark.parametrize("lancar", [_pelo_sdk, _pelo_sing_box, _pelo_diagnostico],
                         ids=["sdk (adb, emulador, Appium)", "sing-box", "diagnostico"])
def test_nenhum_segredo_chega_ao_filho(com_sentinelas: None, tmp_path: Path, lancar) -> None:
    assert all(n in os.environ for n in SENTINELAS)          # elas estão no pai...
    nomes = lancar(tmp_path)
    assert not nomes & set(SENTINELAS)                         # ...e não chegam ao filho
    assert not {n for n in nomes if n.startswith(("TYPESAFE_", "OPENAI_", "ANTHROPIC_", "GEMINI_", "FARM_"))}
    assert "PATH" in nomes                                     # o filho ainda acha os executáveis
    if os.name == "nt":
        assert "SYSTEMROOT" in nomes


def test_o_sdk_ainda_recebe_as_pastas_do_android(com_sentinelas: None, tmp_path: Path) -> None:
    nomes = _pelo_sdk(tmp_path)
    assert {"ANDROID_HOME", "ANDROID_SDK_ROOT", "ANDROID_AVD_HOME"} <= nomes


def test_a_decisao_e_pelo_nome_e_sem_caixa() -> None:
    origem = {"Path": "p", "SystemRoot": "s", "TEMP": "t", "JAVA_HOME": "j", "ANDROID_USER_HOME": "a",
              "ADB_VENDOR_KEYS": "k", "TypeSafe_Api_Key": "x", "openai_api_key": "x", "FARM_DB": "x",
              "MEU_APP_URL": "x", "ANDROID_EMU_TOKEN": "x", "GITHUB_TOKEN": "x", "NODE_OPTIONS": "x"}
    assert set(ambiente_dos_filhos(origem)) == {"Path", "SystemRoot", "TEMP", "JAVA_HOME", "ANDROID_USER_HOME",
                                                "ADB_VENDOR_KEYS"}
