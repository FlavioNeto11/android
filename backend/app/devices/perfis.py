"""Perfil de hardware por IMAGEM do sistema: quanto de RAM o convidado precisa, medido — não chutado.

Existe porque o android-12 "não subia": convidado `google_apis` com 1536 MB e `-lowram` entrou em thrash pós-boot
(load 22, 87 MB livres, swap em uso) e cada `adb shell` levava 20–40 s — a sessão do Appium estourava o prazo e
o central marcava "falha ao abrir sessão de automação". O número certo por imagem estava só em `docs/` e em
comentário de yaml; nenhum código o conhecia, e o worker só aplicava RAM ao CRIAR o AVD.

Sem importar nada do app de propósito: `config.py` e `devices/avd.py` importam daqui, e um ciclo aqui derrubaria
o carregamento do worker (que leva `devices/` inteiro, mas não `state`).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PerfilDeImagem:
    """Duas grandezas que NÃO se misturam:

    - `ram_mb` é a RAM do CONVIDADO (`hw.ramSize` do AVD): o que o Android enxerga e o que o AVD recebe;
    - `est_real_mb` é o custo no HOST: convidado + processo do emulador + app e automação rodando, medido.

    Quem guarda capacidade (o `DeviceManager` no central, `worker/executor._custo_de_ram` no agente) cobra
    `est_real_mb`, pela mesma `AndroidCfg.est_ram_host_mb()`. Cobrar `ram_mb` subestimaria cada aparelho em
    ≈0,7–1,1 GB — foi o que o `ram_per_device_mb: 1800` do exemplo do worker fazia.
    """

    ram_mb: int                 # RAM do CONVIDADO (`hw.ramSize` do AVD)
    extra_args: tuple[str, ...]  # flags do emulador que o perfil exige (`-lowram` faz o emulador RESPEITAR ram_mb)
    est_real_mb: int            # custo REAL medido no HOST, com app e automação
    origem: str                 # de onde veio o número, para o painel e para o log


#: Tabela medida (docs/relatorio-validacao.md): a imagem Android 14 impõe piso de 2560 MB sem `-lowram`; com a
#: flag ela respeita `hw.ramSize`. 1536 MB dá ≈2,4 GB reais e thrash com os apps Google; 2048 MB dá ≈2,7 GB e
#: passa. A loja (`google_apis_playstore`) precisa de folga para a Play Store e roda sem `-lowram` (com janela).
#: Evidência de novo em 26/09 (B21): com convidados de 1,5 GB, o android-04 falhou a prova de abertura (adb
#: `shell` > 30 s) ao receber um app e o android-01 chegou a load 22 ao abrir o Instagram. Não baixe `ram_mb`
#: para caber mais aparelhos: o custo não some, vai para swap e thrash — o que se ganha é o rodízio.
_PERFIS: dict[str, PerfilDeImagem] = {
    "google_apis_playstore": PerfilDeImagem(4096, (), 5200, "medido: imagem com Play Store"),
    "google_apis": PerfilDeImagem(2048, ("-lowram",), 2700, "medido: google_apis em thrash com 1536 MB"),
    "default": PerfilDeImagem(1536, ("-lowram",), 2400, "medido: AOSP sem serviços Google"),
}
_PADRAO = _PERFIS["google_apis"]


def tag_da_imagem(system_image: str) -> str:
    partes = [p for p in (system_image or "").split(";") if p]
    return partes[2] if len(partes) >= 3 else ""


def perfil_por_imagem(system_image: str) -> PerfilDeImagem:
    """O perfil da imagem pelo NOME (`system-images;android-34;google_apis;x86_64`). Imagem desconhecida cai no
    perfil de `google_apis`, que é o do parque — errar para mais RAM é o erro barato."""
    tag = tag_da_imagem(system_image)
    if tag in _PERFIS:
        return _PERFIS[tag]
    if tag.startswith("google"):
        return _PERFIS["google_apis"]
    return _PADRAO if not tag else _PERFIS["default"]
