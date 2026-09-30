"""O Google Play Services do aparelho, lido pelo adb (30/09/2026).

A imagem `google_apis` do parque traz o `com.google.android.gms` instalado e NÃO tem `ro.com.google.gmsversion`
(medido em android-02 e android-07). Só com a propriedade, todo emulador do central era "AOSP" e a distribuição do
Outlook, que exige GMS, era recusada como incompatível.
"""
from app.devices.manager import gms_do_aparelho


def test_google_apis_sem_a_propriedade_mas_com_o_pacote_tem_gms():
    assert gms_do_aparelho("", True, externo=False) is True


def test_a_propriedade_basta():
    assert gms_do_aparelho("24.40.00", False, externo=False) is True


def test_nenhum_dos_dois_num_emulador_nosso_e_aosp():
    assert gms_do_aparelho(None, False, externo=False) is False


def test_nenhum_dos_dois_em_aparelho_de_fora_nao_se_sabe():
    # Físico ou de outra distribuição: a falta dos dois não prova ausência.
    assert gms_do_aparelho("", False, externo=True) is None
