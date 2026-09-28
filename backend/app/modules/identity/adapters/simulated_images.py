"""Gerador de imagem SIMULADO: Pillow determinístico pela semente da receita. NÃO é um modelo de imagem.

Existe para a galeria, as rotas, o avatar e o custo funcionarem sem chave nem gasto, e para os testes compararem
bytes: degradê por semente, silhueta (cabeça e ombros), iniciais e o carimbo "SIMULADO" bem visível — ninguém
confunde com foto. Nada sai da máquina. O mesmo pós-processamento do gerador real é aplicado, para a variação
(proporção, JPEG, ruído) ser exercitada aqui também.
"""
from __future__ import annotations

import io
import random
import time

from PIL import Image, ImageDraw

from app.modules.identity.adapters.pos_processamento import pos_processar
from app.modules.identity.application.ports import GeneratedImage
from app.modules.identity.domain.persona_image import PersonaImageSpec

LADO = 512


class SimulatedImageGenerator:
    name = "simulated"
    model = "degrade-por-semente"
    simulated = True
    sends_data_externally = False
    configured = True

    async def generate(self, spec: PersonaImageSpec, *, reference: bytes | None = None) -> GeneratedImage:
        t0 = time.monotonic()
        original = pintar(spec, com_referencia=reference is not None)
        dados, largura, altura = pos_processar(original, spec)
        return GeneratedImage(data=dados, mime="image/jpeg", width=largura, height=altura, provider_request_id=None,
                              usd=0.0, ms=round((time.monotonic() - t0) * 1000), provider_seed=str(spec.seed),
                              original=original, original_mime="image/png")


def pintar(spec: PersonaImageSpec, *, com_referencia: bool = False) -> bytes:
    """PNG quadrado determinístico: degradê de duas cores sorteadas pela semente, silhueta, iniciais e carimbo."""
    rnd = random.Random(spec.seed)
    cor_a = (rnd.randint(40, 200), rnd.randint(40, 200), rnd.randint(40, 200))
    cor_b = (rnd.randint(40, 200), rnd.randint(40, 200), rnd.randint(40, 200))
    imagem = Image.new("RGB", (LADO, LADO))
    desenho = ImageDraw.Draw(imagem)
    for y in range(LADO):
        t = y / (LADO - 1)
        cor = tuple(int(cor_a[i] * (1 - t) + cor_b[i] * t) for i in range(3))
        desenho.line([(0, y), (LADO, y)], fill=(cor[0], cor[1], cor[2]))
    # Silhueta: cabeça e ombros, deslocados um pouco pela semente (a "pose" do simulado).
    dx = rnd.randint(-40, 40)
    pele = (235 - rnd.randint(0, 120), 200 - rnd.randint(0, 110), 170 - rnd.randint(0, 100))
    desenho.ellipse([196 + dx, 120, 316 + dx, 250], fill=pele)
    desenho.rounded_rectangle([116 + dx, 250, 396 + dx, 512], radius=90, fill=(60, 60, 70))
    iniciais = (spec.identity.initials or "?")[:2].upper()
    desenho.text((236 + dx, 300), iniciais, fill=(240, 240, 240))
    desenho.rectangle([0, LADO - 48, LADO, LADO], fill=(20, 20, 20))
    desenho.text((12, LADO - 36), f"SIMULADO · {spec.persona_id[:12]} · #{spec.index} · {spec.aspect}"
                 + (" · ref" if com_referencia else ""), fill=(255, 220, 0))
    saida = io.BytesIO()
    imagem.save(saida, format="PNG")
    return saida.getvalue()
