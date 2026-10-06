"""As personas (`/api/personas*` e `/api/instances/{id}/personas`): cadastro, geração e enriquecimento por IA, o vínculo com os
aparelhos (primário e desvincular), a prévia, o lote de geração e as imagens da persona (listar, enviar, servir, marcar a principal e a
"feita por IA", apagar). Saíram de `api.py` no 15.15 F4 (corte 8, F4h) sem mudar caminho, método, corpo nem resposta.

Montado em `main.py` no MESMO lugar em que `api.router` entra, e a ordem das rotas é a que era (`/personas/generate/batch` e
`/personas/generate` antes de `/personas/{persona_id}/...`). Este módulo não importa `app.api` (ciclo): o estado é
`request.app.state.poc` (`AppState` só em `TYPE_CHECKING`); o que as rotas de perfis do Instagram, que ficaram em `api.py`,
dividem com estas (o erro de social, o mime da chave e servir do storage) mora em `comum.py` ao lado.
"""
from __future__ import annotations


from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.models import (
    PersonaCreate,
    PersonaDTO,
    PersonaDeviceBody,
    PersonaImageDTO,
    PersonaOnDeviceDTO,
    PersonaPatch,
    PersonaPreviewBody,
)
from app.modules.identity.adapters.pos_processamento import dimensoes
from app.modules.identity.domain.persona import MAIORIDADE
from app.modules.identity.domain.persona_image import OrcamentoEsgotado
from app.modules.identity.infrastructure.persona_images import imagens_dto
from app.modules.identity.presentation.comum import device, mime_da_chave, servir_do_storage, social_error
from app.modules.identity.presentation.schemas import (
    FeitaPorIaBody,
    PersonaBatchBody,
    PersonaEnrichBody,
    PersonaGenerateBody,
    PersonaImagesBody,
)
from app.social.persona_batch import PersonaBatchAccepted, PersonaBatchDTO
from app.social.service import SocialError

if TYPE_CHECKING:
    from app.state import AppState

router = APIRouter(prefix="/api")


def _st(request: Request) -> AppState:
    state: AppState = request.app.state.poc
    return state


def _err(status: int, code: str, message: str, **extra: object) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


@router.get("/personas", response_model=None)
async def list_personas(request: Request) -> object:
    return _st(request).social.list_personas()


@router.post("/personas", status_code=201, response_model=None)
async def create_persona(request: Request, body: PersonaCreate) -> object:
    """Cria a PESSOA (sem conta em app nenhum). Com `ai.image.on_create`, as primeiras imagens saem em segundo plano
    pelo gerador configurado (simulado por omissão) e chegam pelo evento `persona.image.updated`."""
    try:
        return _st(request).criar_persona(body)
    except SocialError as exc:
        raise social_error(exc) from exc


# Declaradas antes de `/personas/{persona_id}`: `generate` é caminho literal, nunca um id de persona.
@router.post("/personas/generate/batch", status_code=202)
async def generate_persona_batch(request: Request, body: PersonaBatchBody) -> PersonaBatchAccepted:
    """Personas em LOTE (v0.34): o mesmo pedido, `count` vezes (1 a 10), em segundo plano com concorrência 2. Cada
    item é uma chamada PAGA pelo papel social, pelo mesmo caminho de `POST /personas/generate`; `create: true` grava
    cada rascunho válido (com a foto automática). O progresso chega por `persona.batch.updated`; o estado, por
    `GET /personas/generate/batch/{id}`. Sem provedor de IA, 503 antes de aceitar."""
    try:
        lote = _st(request).lotes_de_persona.iniciar(body)
    except SocialError as exc:
        raise social_error(exc) from exc
    return PersonaBatchAccepted(batch_id=lote.batch_id, count=lote.count)


@router.get("/personas/generate/batch/{batch_id}")
async def get_persona_batch(request: Request, batch_id: str) -> PersonaBatchDTO:
    """O estado do lote. Vive na MEMÓRIA do servidor: um reinício o perde (são rascunhos; o que foi criado está no
    banco), e aí a resposta é 404."""
    lote = _st(request).lotes_de_persona.obter(batch_id)
    if lote is None:
        raise _err(404, "not_found", "Lote não encontrado: os lotes vivem na memória do servidor e somem num "
                                    "reinício. As personas já criadas estão na lista.")
    return lote


@router.get("/personas/{persona_id}", response_model=None)
async def get_persona(request: Request, persona_id: str) -> object:
    try:
        return _st(request).social.get_persona(persona_id)
    except SocialError as exc:
        raise social_error(exc) from exc


@router.patch("/personas/{persona_id}", response_model=None)
async def update_persona(request: Request, persona_id: str, body: PersonaPatch) -> object:
    try:
        return _st(request).social.update_persona(persona_id, body)
    except SocialError as exc:
        raise social_error(exc) from exc


@router.delete("/personas/{persona_id}", status_code=204)
async def delete_persona(request: Request, persona_id: str) -> None:
    try:
        _st(request).social.delete_persona(persona_id)
    except SocialError as exc:
        raise social_error(exc) from exc


@router.post("/personas/{persona_id}/devices", status_code=201)
async def bind_persona_device(request: Request, persona_id: str, body: PersonaDeviceBody) -> PersonaDTO:
    """Vínculo N:N (migração 051): soma um aparelho à persona para um app, sem mover ninguém. Duas contas do mesmo
    app no mesmo aparelho → 409 `conta_do_app_ja_no_aparelho` (D2-a); a loja e aparelho desconhecido → 400."""
    try:
        return _st(request).social.bind_device(persona_id, body)
    except SocialError as exc:
        raise social_error(exc) from exc


@router.delete("/personas/{persona_id}/devices/{instance_id}")
async def unbind_persona_device(request: Request, persona_id: str, instance_id: str,
                                app_id: str | None = None) -> PersonaDTO:
    """Desvincula a persona DAQUELE aparelho (com `?app_id=`, só daquele app); o principal que sai é substituído
    pelo mais antigo que sobrou. Devolve a persona atualizada."""
    try:
        return _st(request).social.unbind_device(persona_id, instance_id, app_id)
    except SocialError as exc:
        raise social_error(exc) from exc


@router.put("/personas/{persona_id}/devices/{instance_id}/primary")
async def set_persona_primary_device(request: Request, persona_id: str, instance_id: str) -> PersonaDTO:
    """O aparelho principal da persona passa a ser este: alvo padrão de conectar/verificar/sair e do contexto."""
    try:
        return _st(request).social.set_primary_device(persona_id, instance_id)
    except SocialError as exc:
        raise social_error(exc) from exc


@router.get("/instances/{instance_id}/personas")
async def instance_personas(request: Request, instance_id: str) -> list[PersonaOnDeviceDTO]:
    """Quem está neste aparelho (a outra direção do vínculo N:N), com a sessão de cada uma AQUI."""
    s = _st(request)
    device(s, instance_id)
    return s.social.personas_of_instance(instance_id)


@router.post("/personas/{persona_id}/preview", response_model=None)
async def preview_persona(request: Request, persona_id: str, body: PersonaPreviewBody) -> object:
    """Testar Persona: mostra como ela responderia. Não toca em aparelho, não grava interação, não publica nada."""
    try:
        return await _st(request).social.preview_persona(persona_id, body)
    except SocialError as exc:
        raise social_error(exc) from exc


@router.post("/personas/generate")
async def generate_persona(request: Request, body: PersonaGenerateBody) -> PersonaCreate:
    """Rascunho de persona por IA (chamada PAGA, papel social, teto do dia). NADA é gravado: a resposta tem o formato
    de `POST /personas`, para a pessoa revisar e então criar. Rascunho fora das regras (menor, nome que não é nome,
    voz ou biografia incompletas, texto com cara de segredo) volta como 422 `persona_draft_invalid`."""
    try:
        return await _st(request).social.generate_persona_draft(body)
    except SocialError as exc:
        raise social_error(exc) from exc


@router.post("/personas/{persona_id}/enrich")
async def enrich_persona(request: Request, persona_id: str, body: PersonaEnrichBody | None = None) -> PersonaDTO:
    """Completa SÓ o que está vazio numa persona existente (chamada PAGA). Sem lacuna, devolve a persona sem chamar
    o modelo; com lacuna, o que já existia nunca é reescrito. `instructions` (opcional) dizem ao modelo o que o dono
    quer para o que falta — é o "gerar por prompt" aplicado a uma persona que já existe."""
    try:
        return await _st(request).social.enrich_persona(persona_id, instructions=body.instructions if body else None)
    except SocialError as exc:
        raise social_error(exc) from exc


_UPLOAD_DE_IMAGEM = ("image/jpeg", "image/png")


_UPLOAD_MAX_BYTES = 10 * 1024 * 1024


def _pessoa(s: AppState, persona_id: str) -> PersonaDTO:
    try:
        return s.social.get_persona(persona_id)
    except SocialError as exc:
        raise social_error(exc) from exc


@router.get("/personas/{persona_id}/images")
async def list_persona_images(request: Request, persona_id: str) -> list[PersonaImageDTO]:
    s = _st(request)
    return imagens_dto(s.persona_images.listar(_pessoa(s, persona_id).id))


@router.post("/personas/{persona_id}/images", status_code=202)
async def add_persona_images(request: Request, persona_id: str) -> Response:
    """Duas entradas na mesma rota, distinguidas pelo `Content-Type`:

    - JSON `{count}` (1 a 3): GERA em segundo plano pelo provedor configurado e responde 202; cada imagem chega pelo
      evento `persona.image.updated`. Teto do dia e chave são conferidos ANTES de aceitar; menor de idade é recusado;
    - corpo cru `image/jpeg` ou `image/png` (o padrão do envio de APK): UPLOAD de uma foto, 201 com a imagem.
    """
    s = _st(request)
    pessoa = _pessoa(s, persona_id)
    tipo = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    corpo = await request.body()
    if tipo in _UPLOAD_DE_IMAGEM:
        if len(corpo) > _UPLOAD_MAX_BYTES:
            raise _err(413, "image_too_large", "A imagem passa de 10 MB.")
        if dimensoes(corpo) is None:
            raise _err(400, "invalid_image", "O corpo não é uma imagem JPEG/PNG legível.")
        marca = (request.query_params.get("feita_por_ia") or "").strip().lower()
        if marca not in ("", "true", "false"):
            raise _err(422, "invalid_body", "feita_por_ia é true, false ou ausente (não informado).")
        registro = await s.persona_images.registrar_upload(pessoa.id, corpo, tipo,
                                                           feita_por_ia=None if not marca else marca == "true")
        return JSONResponse(status_code=201, content=imagens_dto([registro])[0].model_dump())
    try:
        pedido = PersonaImagesBody.model_validate_json(corpo or b"{}")
    except ValidationError as exc:
        raise _err(422, "invalid_body", f"Corpo inválido: {exc.errors()[0].get('msg', 'erro de validação')}") from exc
    gerador = s.persona_images.generator
    if not gerador.configured:
        raise _err(409, "image_not_configured", f"O provedor de imagem '{gerador.name}' não tem chave configurada "
                                                "(OPENAI_API_KEY no .env) — ou use ai.image.provider: simulated.")
    if pessoa.age is not None and pessoa.age < MAIORIDADE:
        raise _err(409, "persona_minor", f"A persona tem {pessoa.age} anos; só se fotografa pessoa adulta.")
    try:
        s.persona_images.conferir_orcamento()
    except OrcamentoEsgotado as exc:
        raise _err(409, "ai_budget", str(exc)) from exc
    s.agendar_imagens(pessoa.id, pedido.count)
    return JSONResponse(status_code=202, content={"accepted": True, "persona_id": pessoa.id, "count": pedido.count,
                                                  "provider": gerador.name, "simulated": gerador.simulated})


@router.get("/personas/{persona_id}/images/{image_id}")
async def get_persona_image(request: Request, persona_id: str, image_id: str) -> Response:
    """Os bytes da imagem, pelo storage (disco local ou bucket), nunca por caminho vindo da URL."""
    s = _st(request)
    registro = s.persona_images.obter(_pessoa(s, persona_id).id, image_id)
    if registro is None:
        raise _err(404, "not_found", "Imagem não encontrada nesta persona.")
    if registro.status != "ready" or not registro.storage_key:
        raise _err(409, "image_not_ready", f"A imagem está '{registro.status}'." + (f" {registro.error}" if registro.error else ""))
    return servir_do_storage(s.avatares, registro.storage_key, mime_da_chave(registro.storage_key),
                              ausente=("sem_foto", "O arquivo desta imagem não está no storage."))


@router.put("/personas/{persona_id}/images/{image_id}/primary")
async def set_primary_persona_image(request: Request, persona_id: str, image_id: str) -> PersonaDTO:
    s = _st(request)
    pessoa = _pessoa(s, persona_id)
    try:
        s.persona_images.definir_principal(pessoa.id, image_id)
    except KeyError:
        raise _err(404, "not_found", "Imagem não encontrada nesta persona.") from None
    except ValueError as exc:
        raise _err(409, "image_not_ready", str(exc)) from None
    return s.social.get_persona(pessoa.id)


@router.put("/personas/{persona_id}/images/{image_id}/feita-por-ia")
async def set_persona_image_feita_por_ia(request: Request, persona_id: str, image_id: str,
                                         body: FeitaPorIaBody) -> PersonaImageDTO:
    """29.81: o dono diz (ou corrige) se a foto que enviou foi feita por IA. As etapas abertas que publicam a imagem
    regravam o `rotulo_ia`, e o sim dado antes deixa de cobrir a publicação (a chave muda)."""
    s = _st(request)
    pid = _pessoa(s, persona_id).id
    # N2 da revisão: a marca e as etapas abertas mudam juntas; se a regravação falhar no meio, nenhuma das duas fica.
    with s.db.tx():
        try:
            registro, mudou = s.persona_images.marcar_feita_por_ia(pid, image_id, body.feita_por_ia)
        except KeyError:
            raise _err(404, "not_found", "Imagem não encontrada nesta persona.") from None
        except ValueError as exc:
            raise _err(409, "nao_e_upload", str(exc)) from None
        # N3/R1: só a resposta que MUDOU (decidido na escrita, dentro da transação) regrava as etapas abertas.
        if mudou:
            s.repo.ressincronizar_rotulo_ia(image_id)
    return imagens_dto([registro])[0]


@router.delete("/personas/{persona_id}/images/{image_id}", status_code=204)
async def delete_persona_image(request: Request, persona_id: str, image_id: str) -> Response:
    s = _st(request)
    try:
        s.persona_images.apagar(_pessoa(s, persona_id).id, image_id)
    except KeyError:
        raise _err(404, "not_found", "Imagem não encontrada nesta persona.") from None
    return Response(status_code=204)
