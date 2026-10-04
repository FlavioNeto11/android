"""O que do DTO do aparelho é FATO (vai ao log como `instance.updated`) e o que é telemetria (`instance.progress`).

Item 14.13 (RA-11, parte Android). Medido no central em 04/10 (24 h, 7383 `instance.updated`): o DTO já só era
publicado quando algo mudava, mas ~2550 publicações por dia eram trocas de controle `ai↔none`, que o `control.changed`
já grava (cada troca ia ao log duas vezes, uma delas com o DTO inteiro), e 391 mudavam só leitura de instrumento
(CPU/RAM, idade do quadro, hora da sonda de rede, contagem regressiva da pausa). Tudo isso empurrava para fora da janela
de replay do painel o que importa.

A regra: `instance.updated` (persistido) quando muda um campo MATERIAL do DTO, quando a publicação traz mensagem
própria (é registro: "inventário conferido", "adotado do worker…"), quando o nível não é `info`, ou quando o controle
mudou sem `control.changed` que o anunciasse. Fora disso, `instance.progress` (efêmero, em
`events.EPHEMERAL_KINDS`): o painel recebe o mesmo DTO ao vivo e o log não cresce.
"""
from __future__ import annotations

import json

#: Leitura de instrumento: muda a cada amostra e não conta história. O valor atual chega pelo `instance.progress` e
#: pelo snapshot.
CAMPOS_VOLATEIS = frozenset({"resources", "frame"})
SUBCAMPOS_VOLATEIS: dict[str, tuple[str, ...]] = {
    "connectivity": ("checked_at",),
    "stream": ("frame_age_s", "last_frame_at"),
    "repair_pause": ("remaining_s",),
}
#: Anunciados pelo `control.changed` (persistido, e o reducer do painel o aplica): fora da assinatura, conferidos à
#: parte por `controle_anunciado`.
CAMPOS_DO_CONTROLE = frozenset({"control", "control_since", "control_pending"})


def assinatura_material(dto: dict[str, object]) -> str:
    """O DTO sem a telemetria e sem o controle, em JSON canônico: duas publicações com a mesma assinatura contam o
    mesmo fato."""
    material: dict[str, object] = {}
    for campo, valor in dto.items():
        if campo in CAMPOS_VOLATEIS or campo in CAMPOS_DO_CONTROLE:
            continue
        fora = SUBCAMPOS_VOLATEIS.get(campo)
        if fora and isinstance(valor, dict):
            valor = {k: v for k, v in valor.items() if k not in fora}
        material[campo] = valor
    return json.dumps(material, sort_keys=True, separators=(",", ":"), default=str)
