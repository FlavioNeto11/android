"""PUT_MEDIA_IN_GALLERY (29.30): colocar a imagem de UMA persona na galeria do aparelho, por código determinístico.

É a capability `internal` do catálogo do Instagram: fora do laço da IA, sem tela e sem efeito na conta. O que ela faz é
só pôr o arquivo onde o editor de publicação o enxergue; quem publica é CREATE_POST, depois da aprovação de uma pessoa.

A regra que importa vive aqui, antes de qualquer byte sair da central: a imagem tem de ser DA persona da conta do
aparelho e estar PRONTA. `PersonaImageService.obter(persona_id, image_id)` já consulta pelas duas chaves, então a imagem
de outra pessoa volta como inexistente — e é recusada sem tocar no aparelho. Função síncrona de propósito: o executor
a roda fora do laço de eventos (`rt.executor.run`), com prazo.
"""
from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Callable
from typing import Any

#: Capabilities `internal` que o executor despacha por código, em vez de entregar ao ator.
INTERNAS_POR_CODIGO = frozenset({"PUT_MEDIA_IN_GALLERY"})


class MidiaRecusada(Exception):
    """A imagem não pôde ir para a galeria e nada foi enviado ao aparelho. A mensagem é o motivo, para a pessoa."""


def nome_na_galeria(image_id: str) -> str:
    """O nome do arquivo na galeria, fixo por imagem (repetir o envio sobrescreve, não duplica)."""
    limpo = re.sub(r"[^A-Za-z0-9_\-]", "", image_id or "")[:50]
    if not limpo:
        raise MidiaRecusada("o id da imagem não é utilizável como nome de arquivo")
    return f"img_{limpo}"


def colocar_midia_na_galeria(persona_images: Any, persona_id: str | None, image_id: str | None,
                             enviar: Callable[[str, str], str]) -> str:
    """Confere a imagem, grava os bytes num arquivo temporário e os entrega a `enviar(local, nome)` (o
    `Adb.enviar_midia_para_galeria` do aparelho, ou o dublê dele). Devolve o caminho no aparelho.

    `MidiaRecusada` = nada foi enviado (sem serviço, sem persona, imagem de outra pessoa ou não pronta, sem bytes).
    Qualquer erro de `enviar` (AdbError) sobe como está: o push pode ter acontecido pela metade."""
    if persona_images is None:
        raise MidiaRecusada("o serviço de imagens da persona não está disponível neste servidor")
    if not persona_id:
        raise MidiaRecusada("este aparelho não tem persona vinculada: não há de quem tirar a imagem")
    if not (image_id or "").strip():
        raise MidiaRecusada("a etapa não diz qual imagem colocar (image_id)")
    registro = persona_images.obter(persona_id, image_id.strip())
    if registro is None:
        raise MidiaRecusada(f"a imagem {image_id} não é desta persona (ou não existe): nada foi enviado ao aparelho")
    if registro.status != "ready":
        raise MidiaRecusada(f"a imagem {image_id} não está pronta (estado: {registro.status}): nada foi enviado")
    dados = persona_images.bytes_de(registro)
    if not dados:
        raise MidiaRecusada(f"a imagem {image_id} está pronta no registro, mas sem arquivo no armazenamento")
    nome = nome_na_galeria(image_id)
    # `delete=False` e fechar antes do push: no Windows o `adb.exe` não abre um arquivo que a gente ainda mantém aberto.
    with tempfile.NamedTemporaryFile(prefix="galeria_", suffix=".jpg", delete=False) as tmp:
        tmp.write(dados)
        local = tmp.name
    try:
        return enviar(local, nome)
    finally:
        try:
            os.unlink(local)
        except OSError:
            pass
