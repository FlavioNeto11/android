"""Pós-processamento HONESTO da foto (Pillow): recorte na proporção da receita, redução e reampliação, ruído e
desfoque leves, JPEG com qualidade variável — a variação que fotos de gente comum têm entre si.

O que NÃO se faz aqui, de propósito: EXIF inventado, remoção deliberada de proveniência ou qualquer coisa que
sirva para enganar detecção. O original do provedor é preservado pelo serviço (`original_key`); esta função
produz só o arquivo servido, e a receita inteira (`spec`) diz o que foi aplicado.
"""
from __future__ import annotations

import io
import random

from PIL import Image, ImageFilter

from app.modules.identity.domain.persona_image import PersonaImageSpec


def pos_processar(dados: bytes, spec: PersonaImageSpec) -> tuple[bytes, int, int]:
    """`(jpeg, largura, altura)` no tamanho final da proporção da receita. Determinístico pela semente da receita."""
    with Image.open(io.BytesIO(dados)) as origem:
        imagem = origem.convert("RGB")
    largura, altura = spec.final_size
    imagem = _recortar_na_proporcao(imagem, largura / altura, spec.crop_shift)
    if spec.downscale < 1.0:
        # Reduz e reamplia: perde detalhe fino como uma foto reencaminhada perde. Reamplia para o tamanho final.
        menor = (max(64, int(imagem.width * spec.downscale)), max(64, int(imagem.height * spec.downscale)))
        imagem = imagem.resize(menor, Image.Resampling.BILINEAR)
    imagem = imagem.resize((largura, altura), Image.Resampling.LANCZOS)
    if spec.blur_radius > 0:
        imagem = imagem.filter(ImageFilter.GaussianBlur(radius=spec.blur_radius))
    if spec.noise_sigma > 0:
        imagem = _com_ruido(imagem, spec.noise_sigma, spec.seed)
    saida = io.BytesIO()
    imagem.save(saida, format="JPEG", quality=int(spec.jpeg_quality), optimize=True)
    return saida.getvalue(), largura, altura


def dimensoes(dados: bytes) -> tuple[int, int] | None:
    """`(largura, altura)` de qualquer imagem que o Pillow abra; `None` para o que não é imagem."""
    try:
        with Image.open(io.BytesIO(dados)) as imagem:
            return int(imagem.width), int(imagem.height)
    except (OSError, ValueError):
        return None


def _recortar_na_proporcao(imagem: Image.Image, proporcao: float, deslocamento: float) -> Image.Image:
    """Maior recorte possível na proporção pedida, deslocado do centro em `deslocamento` (fração do sobra)."""
    largura, altura = imagem.size
    if largura / altura > proporcao:
        nova_largura, nova_altura = int(altura * proporcao), altura
    else:
        nova_largura, nova_altura = largura, int(largura / proporcao)
    sobra_x, sobra_y = largura - nova_largura, altura - nova_altura
    x0 = int(sobra_x / 2 + sobra_x * deslocamento)
    y0 = int(sobra_y / 2 + sobra_y * deslocamento)
    x0, y0 = max(0, min(x0, sobra_x)), max(0, min(y0, sobra_y))
    return imagem.crop((x0, y0, x0 + nova_largura, y0 + nova_altura))


def _com_ruido(imagem: Image.Image, sigma: float, semente: int) -> Image.Image:
    """Ruído gaussiano leve, determinístico pela semente. Numa imagem reduzida (para não custar segundos em 1080p),
    depois reampliado junto — o grão fica suave, como o de sensor pequeno."""
    rnd = random.Random(semente)
    reduzida = imagem.resize((max(1, imagem.width // 4), max(1, imagem.height // 4)), Image.Resampling.BILINEAR)
    # Pelos bytes crus (RGB, 3 por pixel), e não por `load()`/`getdata()`: os dois são tipados de forma que não
    # passa no mypy estrito, e a conta é a mesma — um desvio por pixel, aplicado aos três canais.
    bruto = reduzida.tobytes()
    saida = bytearray(len(bruto))
    for i in range(0, len(bruto) - 2, 3):
        desvio = int(rnd.gauss(0.0, sigma))
        saida[i], saida[i + 1], saida[i + 2] = (_canal(bruto[i] + desvio), _canal(bruto[i + 1] + desvio),
                                                _canal(bruto[i + 2] + desvio))
    grao = Image.frombytes("RGB", reduzida.size, bytes(saida)).resize(imagem.size, Image.Resampling.BILINEAR)
    return Image.blend(imagem, grao, 0.35)


def _canal(valor: int) -> int:
    return max(0, min(255, valor))
