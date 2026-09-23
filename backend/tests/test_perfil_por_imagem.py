"""Perfil de RAM por imagem: o número certo em código, não em comentário de yaml.

Medido em 23/09/2026: android-12 (`google_apis`, 1536 MB + -lowram) em thrash pós-boot — load 22 em 2 vCPUs,
87 MB livres, swap em uso — e cada `adb shell` levando 20–40 s. Ninguém "não subia": o convidado só não tinha
memória para os apps Google. O perfil por imagem é o que faz o provisionamento acertar sozinho.
"""
from __future__ import annotations

from app.config import AndroidCfg
from app.devices import emulator
from app.devices.perfis import perfil_por_imagem


def test_tabela_por_imagem() -> None:
    g = perfil_por_imagem("system-images;android-34;google_apis;x86_64")
    assert (g.ram_mb, g.extra_args) == (2048, ("-lowram",))
    loja = perfil_por_imagem("system-images;android-34;google_apis_playstore;x86_64")
    assert loja.ram_mb == 4096 and "-lowram" not in loja.extra_args
    aosp = perfil_por_imagem("system-images;android-34;default;x86_64")
    assert (aosp.ram_mb, aosp.extra_args) == (1536, ("-lowram",))
    # Desconhecida cai no perfil do parque: errar para mais RAM é o erro barato.
    assert perfil_por_imagem("") .ram_mb == 2048


def test_config_sem_ram_mb_usa_o_perfil_e_com_ram_mb_respeita_o_dono() -> None:
    pelo_perfil = AndroidCfg()
    assert pelo_perfil.ram_efetiva() == 2048
    assert pelo_perfil.args_extras_efetivos() == ["-lowram"]
    assert pelo_perfil.est_ram_host_mb() == 2700

    do_dono = AndroidCfg(ram_mb=1536, extra_emulator_args=["-lowram"])
    assert do_dono.ram_efetiva() == 1536
    assert do_dono.args_extras_efetivos() == ["-lowram"]
    assert do_dono.est_ram_host_mb() == 1536 + 1100          # regra antiga, preservada para número explícito

    sem_flag = AndroidCfg(ram_mb=2560)
    assert sem_flag.args_extras_efetivos() == []              # o dono decidiu; o perfil não acrescenta nada


def test_o_perfil_chega_na_linha_de_comando_do_emulador() -> None:
    class Tools:
        emulator = "emulator"

    args = emulator.build_args(Tools(), "worker-03", 5558, AndroidCfg(), wipe_data=False)  # type: ignore[arg-type]
    assert "-lowram" in args
    # Declarado uma vez só, mesmo quando o dono repete a flag.
    args2 = emulator.build_args(Tools(), "worker-03", 5558, AndroidCfg(extra_emulator_args=["-lowram"]),  # type: ignore[arg-type]
                                wipe_data=False)
    assert args2.count("-lowram") == 1
