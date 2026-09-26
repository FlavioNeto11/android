"""Codificação da imagem da tela: UMA decodificação do PNG e só as codificações que alguém vai consumir.

Módulo leve de propósito (Pillow + stdlib): é importado pelos DOIS lados. O central codifica o screencap que leu
pelo ADB; o agente do worker, com a feature `observe_local` (adendo v0.20), codifica NA ORIGEM — perto do
aparelho — e só o JPEG já reduzido atravessa o túnel, em vez do PNG cheio. As duas pontas precisam produzir os
MESMOS bytes para a mesma tela (qualidade, tamanho do modelo, miniatura): se divergissem, a coordenada que o modelo
devolve não bateria com a imagem que ele viu. Por isso a regra mora aqui, uma vez.

O agente não pode importar `devices/manager.py` (arrasta banco, eventos e o resto do backend; o worker instala seis
dependências, não sessenta). Enquanto `manager.py` mantiver a própria cópia (`_codificar`, `dimensoes_do_modelo`),
as duas têm de continuar idênticas — o teste de paridade em `tests/test_observacao_na_origem.py` confere.
"""
from __future__ import annotations

import io
import time
from dataclasses import dataclass, field

from PIL import Image

#: Largura da miniatura da prévia. A mesma de `devices/manager.THUMB_WIDTH`.
THUMB_WIDTH = 360
#: Qualidade do JPEG cheio e do modelo, e da miniatura. As mesmas de `devices/manager._codificar`.
QUALIDADE = 72
QUALIDADE_MINIATURA = 62


def dimensoes_do_modelo(w: int, h: int, lado_max: int) -> tuple[int, int, float]:
    """O tamanho em que o modelo vê a tela: lado maior limitado a `lado_max`. UMA conta, usada por quem codifica
    e por quem monta a tela do modelo (`taskqueue.executor._screen`)."""
    if max(w, h) <= lado_max:
        return w, h, 1.0
    escala = max(w, h) / lado_max
    return round(w / escala), round(h / escala), escala


@dataclass(slots=True)
class Codificado:
    largura: int
    altura: int
    cheia: bytes | None = None
    miniatura: bytes | None = None
    modelo: bytes | None = None
    ms: dict[str, float] = field(default_factory=dict)


def codificar(png: bytes, *, previa: bool, cheia: bool, lado_max: int | None) -> Codificado:
    """UMA decodificação do PNG e só as codificações pedidas: a da prévia (cheia + miniatura), a cheia sozinha, a
    do modelo. A do modelo reaproveita os bytes da cheia quando o tamanho coincide, em vez de recodificar."""
    img = Image.open(io.BytesIO(png)).convert("RGB")
    w, h = img.size
    out = Codificado(largura=w, altura=h)
    if previa or cheia:
        t0 = time.perf_counter()
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=QUALIDADE, optimize=False)
        out.cheia = buf.getvalue()
        if previa:
            th = img.resize((THUMB_WIDTH, max(1, round(h * THUMB_WIDTH / w)))) if w > THUMB_WIDTH else img
            buf = io.BytesIO()
            th.save(buf, "JPEG", quality=QUALIDADE_MINIATURA)
            out.miniatura = buf.getvalue()
        out.ms["previa" if previa else "cheia"] = (time.perf_counter() - t0) * 1000
    if lado_max is not None:
        mw, mh, _ = dimensoes_do_modelo(w, h, lado_max)
        if (mw, mh) == (w, h) and out.cheia is not None:
            out.modelo = out.cheia
        else:
            t0 = time.perf_counter()
            buf = io.BytesIO()
            (img if (mw, mh) == (w, h) else img.resize((mw, mh))).save(buf, "JPEG", quality=QUALIDADE)
            out.modelo = buf.getvalue()
            out.ms["modelo"] = (time.perf_counter() - t0) * 1000
    return out


def tamanho_png(png: bytes) -> tuple[int, int]:
    """Largura e altura lendo só o cabeçalho: `Image.open` é preguiçoso e não decodifica os pixels."""
    with Image.open(io.BytesIO(png)) as img:
        return img.size
