"""Codificação da imagem da tela: UMA decodificação do PNG e só as codificações que alguém vai consumir.

Módulo leve de propósito (Pillow + stdlib): é importado pelos DOIS lados. O central codifica o screencap que leu
pelo ADB; o agente do worker, com a feature `observe_local` (adendo v0.20), codifica NA ORIGEM — perto do
aparelho — e só o JPEG já reduzido atravessa o túnel, em vez do PNG cheio. As duas pontas precisam produzir os
MESMOS bytes para a mesma tela (qualidade, tamanho do modelo, miniatura): se divergissem, a coordenada que o modelo
devolve não bateria com a imagem que ele viu. Por isso a regra mora aqui, uma vez.

O agente não pode importar `devices/manager.py` (arrasta banco, eventos e o resto do backend; o worker instala sete
dependências, não sessenta). Por isso a regra mora AQUI e `manager.py` a importa (`_codificar`, `_tamanho_png`,
`dimensoes_do_modelo` são apelidos deste módulo); o teste de paridade em `tests/test_observacao_na_origem.py` segue
conferindo que as duas pontas dão os mesmos bytes.
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


#: Teto do recorte (item 12.5): é uma LINHA de tela (no máximo duas), nunca o print inteiro. Lado maior em pixels da imagem
#: enviada, fração da altura da imagem, altura absoluta em pixels e bytes do JPEG. Passar de qualquer um é `ValueError`
#: (a âncora não é uma linha). O que sai é dado de TERCEIROS (a caixa de entrada de uma conta): metade da tela seriam várias
#: mensagens, e não a que a etapa precisa.
#:
#: Medida: a linha da caixa de entrada do teste tem 162 px no aparelho de 720x1280, que é 130 px na imagem de 576x1024 e 162
#: px na de 720x1280 (`ai.screenshot_max_side` 1280, o padrão): uma linha cabe com folga em 0,2 da altura (205 a 256 px), duas
#: não (324 px). O teto absoluto de 320 px (duas linhas dessa medida) vale para a imagem maior que o padrão, onde a fração
#: sozinha deixaria passar mais.
RECORTE_LADO_MAX = 1600
RECORTE_ALTURA_MAX_FRACAO = 0.2
RECORTE_ALTURA_MAX_PX = 320
RECORTE_BYTES_MAX = 400_000


def recortar_jpeg(jpeg: bytes, largura: int, altura: int, limites: tuple[int, int, int, int]) -> bytes:
    """Um recorte do JPEG (item 12.5): `limites` em pixels do APARELHO (`largura` x `altura`) × a razão do tamanho real da
    imagem — a observação do modelo vem reduzida —, cortados na tela e SEM margem. `ValueError` se a área é vazia ou o
    JPEG não abre: recorte vazio não vira leitura."""
    if largura <= 0 or altura <= 0:
        raise ValueError("tamanho do aparelho inválido")
    x1, y1 = max(0, limites[0]), max(0, limites[1])
    x2, y2 = min(largura, limites[2]), min(altura, limites[3])
    if x2 <= x1 or y2 <= y1:
        raise ValueError("a âncora não tem área dentro da tela")
    try:
        with Image.open(io.BytesIO(jpeg)) as img:
            sx, sy = img.width / largura, img.height / altura
            caixa = (round(x1 * sx), round(y1 * sy), round(x2 * sx), round(y2 * sy))
            if caixa[2] <= caixa[0] or caixa[3] <= caixa[1]:
                raise ValueError("o recorte ficou sem área")
            if (max(caixa[2] - caixa[0], caixa[3] - caixa[1]) > RECORTE_LADO_MAX
                    or caixa[3] - caixa[1] > min(img.height * RECORTE_ALTURA_MAX_FRACAO, RECORTE_ALTURA_MAX_PX)):
                raise ValueError("o recorte é grande demais para ser uma linha (nunca a tela inteira)")
            corte = img.convert("RGB").crop(caixa)
    except OSError as exc:
        raise ValueError("JPEG ilegível") from exc
    for qualidade in (90, 60):
        saida = io.BytesIO()
        corte.save(saida, "JPEG", quality=qualidade)
        if saida.tell() <= RECORTE_BYTES_MAX:
            return saida.getvalue()
    raise ValueError("o recorte passa do teto de bytes")
