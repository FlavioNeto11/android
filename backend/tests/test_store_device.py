"""Aparelho-loja: o emulador com Play Store que serve de repositório oficial do app.

O que estes testes protegem, em uma frase cada:

* a loja é declarada na configuração e um erro de digitação ali quebra NA CARGA, não em silêncio no primeiro boot;
* o projeto gere o ciclo de vida dela (liga, desliga), mas NUNCA lhe despacha tarefa — o inverso do aparelho externo;
* a conta Google é digitada na janela do emulador: o painel recusa texto na loja, porque sem sessão de automação ele
  cairia em `adb input text`, com a senha na linha de comando do host.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import AndroidCfg
from app.devices import emulator
from app.devices.avd import AvdManager
from app.devices.sdk import SdkTools

from .conftest import make_config

LOJA = "android-03"
IMAGEM_LOJA = "system-images;android-34;google_apis_playstore;x86_64"


# ==================================================================== E1 — configuração
def test_loja_precisa_existir_nas_instancias(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="instances.store: 'android-09' não existe"):
        make_config(tmp_path, 3, store="android-09")


def test_loja_nao_pode_ser_tambem_aparelho_externo(tmp_path: Path) -> None:
    """São papéis opostos: o externo não é nosso para ligar; a loja é nossa e nunca recebe tarefa."""
    from app.config import AppConfigFile

    with pytest.raises(ValidationError, match="não pode ser também aparelho externo"):
        AppConfigFile.model_validate({"instances": {"count": 3, "store": LOJA, "external": {LOJA: "1a2b3c4d"}}})


def test_override_com_chave_desconhecida_e_recusado_na_carga(tmp_path: Path) -> None:
    """Antes era ignorado em silêncio: `hibernacao: false` não fazia nada e o aparelho subia com o padrão."""
    with pytest.raises(ValidationError, match="chave\\(s\\) desconhecida\\(s\\): hibernacao"):
        make_config(tmp_path, 3, overrides={LOJA: {"hibernacao": False}})


def test_override_com_tipo_errado_e_recusado_na_carga(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        make_config(tmp_path, 3, overrides={LOJA: {"ram_mb": "muita"}})


def test_override_de_instancia_que_nao_existe_e_recusado(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="essa instância não existe"):
        make_config(tmp_path, 3, overrides={"android-07": {"ram_mb": 2048}})


def test_override_valido_chega_a_configuracao_efetiva_so_daquela_instancia(tmp_path: Path) -> None:
    cfg = make_config(tmp_path, 3, store=LOJA,
                      overrides={LOJA: {"system_image": IMAGEM_LOJA, "window": True, "hibernation": False}})
    assert cfg.store_id == LOJA
    loja, parque = cfg.instance_android(LOJA), cfg.instance_android("android-01")
    assert loja.window is True and loja.system_image == IMAGEM_LOJA
    assert parque.window is False and "playstore" not in parque.system_image      # o parque não muda


def test_sem_loja_declarada_nada_muda(tmp_path: Path) -> None:
    assert make_config(tmp_path, 3).store_id is None


# ==================================================================== E1 — emulador e AVD
def _args(a: AndroidCfg) -> list[str]:
    tools = SdkTools.__new__(SdkTools)
    tools.emulator = Path("emulator.exe")  # type: ignore[attr-defined]
    return emulator.build_args(tools, "android-03", 5558, a, wipe_data=False)


def test_parque_sobe_sem_janela_e_a_loja_sobe_com_janela() -> None:
    """`-no-window` não tem flag que o desfaça: ou ele é omitido aqui, ou a janela nunca aparece."""
    assert "-no-window" in _args(AndroidCfg())
    com_janela = _args(AndroidCfg(window=True))
    assert "-no-window" not in com_janela
    assert {"-no-audio", "-no-boot-anim"} <= set(com_janela)                      # o resto continua igual


def test_avd_com_imagem_playstore_liga_a_loja_no_config_ini(tmp_path: Path) -> None:
    """`avdmanager create` sem perfil de aparelho pode gravar `PlayStore.enabled=false` mesmo com a imagem certa."""
    cfg = make_config(tmp_path, 3)
    avd_dir = cfg.avd_home / "android-03.avd"
    avd_dir.mkdir(parents=True)
    (avd_dir / "config.ini").write_text("PlayStore.enabled=false\nhw.ramSize=512\n", encoding="utf-8")
    mgr = AvdManager.__new__(AvdManager)
    mgr.cfg = cfg  # type: ignore[attr-defined]

    mgr.apply_hardware("android-03", AndroidCfg(system_image=IMAGEM_LOJA))
    ini = (avd_dir / "config.ini").read_text(encoding="utf-8")
    assert "PlayStore.enabled=yes" in ini and "PlayStore.enabled=false" not in ini

    # imagem sem loja: a chave não é tocada (nem criada)
    (avd_dir / "config.ini").write_text("hw.ramSize=512\n", encoding="utf-8")
    mgr.apply_hardware("android-03", AndroidCfg())
    assert "PlayStore" not in (avd_dir / "config.ini").read_text(encoding="utf-8")


# ==================================================================== E3 — a imagem de override aparece na saúde
IMAGEM_AUSENTE = "system-images;android-34;imagem_que_nao_existe;x86_64"


def test_so_quem_usa_imagem_diferente_da_padrao_entra_na_lista(tmp_path: Path) -> None:
    cfg = make_config(tmp_path, 3, store=LOJA, overrides={LOJA: {"system_image": IMAGEM_LOJA},
                                                          "android-01": {"ram_mb": 2048}})
    assert cfg.override_images() == {LOJA: IMAGEM_LOJA}              # android-01 mudou RAM, não imagem


async def test_saude_nomeia_a_imagem_de_override_ausente_e_da_o_comando(tmp_path: Path) -> None:
    """Antes só a imagem PADRÃO era conferida: a da loja, ausente, só aparecia como erro no primeiro boot."""
    from .conftest import Harness

    h = Harness(tmp_path, 3, store=LOJA, overrides={LOJA: {"system_image": IMAGEM_AUSENTE}})
    await h.boot()
    try:
        st = h.state
        assert st is not None
        st.tools.found = lambda: True                                # type: ignore[method-assign]
        problema = next(p for p in st.health().problems if p.code == "system_image_missing")
        assert LOJA in problema.message and IMAGEM_AUSENTE in problema.message
        assert f'sdkmanager "{IMAGEM_AUSENTE}"' in (problema.hint or "")

        st.tools.system_image_dir = lambda _pkg: tmp_path            # type: ignore[method-assign]  # "instalada"
        assert "system_image_missing" not in {p.code for p in st.health().problems}
    finally:
        await h.state.stop()                                         # type: ignore[union-attr]
