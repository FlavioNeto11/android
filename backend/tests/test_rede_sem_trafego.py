"""29.44: o app sem tráfego na janela (`sem_trafego`) é distinto do não medido e não segura o `parcial` quando outro app
passou pelo túnel e nenhum saiu por fora. "Verificado" = tudo o que trafegou passou pelo túnel."""
from __future__ import annotations

import pytest

from app.devices import rede
from app.devices.sonda_rede import Cobertura

IG, OUT, SHELL = "com.instagram.android", "com.microsoft.office.outlook", "com.android.shell"
IP = "45.162.8.10"


def _falta(per_app: dict[str, str], exigidos: list[str] = [IG]) -> list[str]:  # noqa: B006
    return rede._falta_para_verificar(rede.NetworkMeasurementInput(method="x", egress_ipv4=IP, per_app=per_app),
                                      "exigida", exigidos, provado=True)


def test_cobertura_sem_byte_e_sem_trafego_e_nao_nao_medido() -> None:
    assert Cobertura(0, 0).resultado == "sem_trafego"
    assert Cobertura(500, 500).resultado == "ok" and Cobertura(0, 900).resultado == "fora_da_rede"


@pytest.mark.parametrize("per_app, falta", [
    ({IG: "sem_trafego", SHELL: "ok"}, []),                                     # o shell trafegou: não segura
    ({IG: "sem_trafego", OUT: "ok", SHELL: "ok"}, []),
    ({IG: "sem_trafego", SHELL: "sem_trafego"}, ["nenhum app trafegou"]),       # nada trafegou: segura
    ({IG: "nao_medido", SHELL: "ok"}, ["apps fora da rede pedida"]),             # não instalado segue segurando
    ({IG: "sem_trafego", OUT: "fora_da_rede", SHELL: "ok"}, ["apps fora da rede pedida"]),
    ({SHELL: "ok"}, ["apps do aparelho não medidos"]),                          # o exigido nem entrou na medição
])
def test_o_que_segura_o_parcial(per_app: dict[str, str], falta: list[str]) -> None:
    obtido = _falta(per_app, [IG, OUT] if OUT in per_app else [IG])
    assert len(obtido) == len(falta) and all(o.startswith(f) for o, f in zip(obtido, falta, strict=True))


def test_a_ressalva_diz_o_app_e_que_nao_segura() -> None:
    m = rede.NetworkMeasurementInput(method="x", egress_ipv4=IP, per_app={OUT: "sem_trafego", IG: "ok", SHELL: "ok"})
    assert rede.apps_sem_trafego(m) == [OUT]
    assert rede.ressalva_sem_trafego([OUT]) == f"{OUT} sem tráfego na janela: não provado, não segura o estado"


def test_o_valor_novo_e_aceito_no_contrato_da_medicao() -> None:
    m = rede.NetworkMeasurementInput(method="x", per_app={IG: "sem_trafego"})
    assert m.per_app[IG] == "sem_trafego"
    with pytest.raises(ValueError):
        rede.NetworkMeasurementInput(method="x", per_app={IG: "parado"})
