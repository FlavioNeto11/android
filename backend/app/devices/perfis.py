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
    ram_mb: int                 # `hw.ramSize` do AVD
    extra_args: tuple[str, ...]  # flags do emulador que o perfil exige (`-lowram` faz o emulador RESPEITAR ram_mb)
    est_real_mb: int            # custo REAL medido no host, com app e automação
    origem: str                 # de onde veio o número, para o painel e para o log


#: Tabela medida (docs/relatorio-validacao.md): a imagem Android 14 impõe piso de 2560 MB sem `-lowram`; com a
#: flag ela respeita `hw.ramSize`. 1536 MB dá ≈2,4 GB reais e thrash com os apps Google; 2048 MB dá ≈2,7 GB e
#: passa. A loja (`google_apis_playstore`) precisa de folga para a Play Store e roda sem `-lowram` (com janela).
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
