"""Composição do serviço de imagens da persona: banco (`persona_images`, `ai_calls`), storage, provedor e eventos.

Mora na infraestrutura porque os tipos são do legado (`Database`, `Storage`, `Config`, `EventBus`) e o serviço de
aplicação só conhece as portas. `compor_servico_de_imagens` é o que o `AppState` chama; o resto são as peças.
"""
from __future__ import annotations

import json
from collections.abc import Callable

from app.config import Config
from app.db import Database, Row
from app.models import AiImageStatus, PersonaDTO, PersonaImageDTO
from app.modules.identity.adapters.openai_images import OpenAIImageGenerator
from app.modules.identity.adapters.pos_processamento import dimensoes
from app.modules.identity.adapters.simulated_images import SimulatedImageGenerator
from app.modules.identity.application.persona_images import PersonaImageService
from app.modules.identity.application.ports import EventSink, ImageGenerator
from app.modules.identity.domain.persona_image import PersonaIdentity, PersonaImageRecord
from app.planning import costs, saldos
from app.storage import Storage, put_async
from app.util import new_token, now_iso

#: Colunas de `persona_images` na ordem do INSERT.
_COLUNAS = ("id", "persona_id", "storage", "storage_key", "original_key", "spec", "prompt_sha256", "provider", "model",
            "seed", "provider_seed", "provider_request_id", "width", "height", "bytes_sha256", "cost_usd", "status",
            "error", "source", "is_primary", "created_at")


class SqlPersonaImages:
    """`PersonaImageRepository` sobre `Database`. Toda consulta filtra por `persona_id` (regra de isolamento)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def insert(self, record: PersonaImageRecord) -> None:
        valores = (record.id, record.persona_id, record.storage, record.storage_key, record.original_key,
                   record.spec_json, record.prompt_sha256, record.provider, record.model, int(record.seed),
                   record.provider_seed, record.provider_request_id, record.width, record.height,
                   record.bytes_sha256, float(record.cost_usd), record.status, record.error, record.source,
                   int(record.is_primary), record.created_at)
        marcadores = ",".join("?" for _ in _COLUNAS)
        self._db.execute(f"INSERT INTO persona_images({', '.join(_COLUNAS)}) VALUES ({marcadores})", valores)  # noqa: S608

    def finish(self, persona_id: str, image_id: str, *, status: str, storage_key: str | None, original_key: str | None,
               width: int | None, height: int | None, bytes_sha256: str | None, cost_usd: float,
               provider_request_id: str | None, provider_seed: str | None, error: str | None) -> None:
        self._db.execute(
            "UPDATE persona_images SET status=?, storage_key=?, original_key=?, width=?, height=?, bytes_sha256=?,"
            " cost_usd=?, provider_request_id=?, provider_seed=?, error=? WHERE id=? AND persona_id=?",
            (status, storage_key, original_key, width, height, bytes_sha256, float(cost_usd), provider_request_id,
             provider_seed, error, image_id, persona_id))

    def list(self, persona_id: str) -> list[PersonaImageRecord]:
        linhas = self._db.query("SELECT * FROM persona_images WHERE persona_id=? ORDER BY created_at, id", (persona_id,))
        return [_registro(linha) for linha in linhas]

    def get(self, persona_id: str, image_id: str) -> PersonaImageRecord | None:
        linha = self._db.one("SELECT * FROM persona_images WHERE id=? AND persona_id=?", (image_id, persona_id))
        return _registro(linha) if linha is not None else None

    def set_primary(self, persona_id: str, image_id: str) -> None:
        # Duas instruções na mesma transação: o índice único parcial recusaria a segunda principal no meio.
        with self._db.tx():
            self._db.execute("UPDATE persona_images SET is_primary=0 WHERE persona_id=? AND is_primary=1", (persona_id,))
            self._db.execute("UPDATE persona_images SET is_primary=1 WHERE id=? AND persona_id=?", (image_id, persona_id))

    def delete(self, persona_id: str, image_id: str) -> None:
        self._db.execute("DELETE FROM persona_images WHERE id=? AND persona_id=?", (image_id, persona_id))


def _texto(valor: object) -> str | None:
    return valor if valor is None or isinstance(valor, str) else str(valor)


def _inteiro(valor: object) -> int | None:
    return int(valor) if isinstance(valor, int) and not isinstance(valor, bool) else None


def _registro(linha: Row) -> PersonaImageRecord:
    return PersonaImageRecord(
        id=str(linha["id"]), persona_id=str(linha["persona_id"]), status=str(linha["status"]),
        source=str(linha["source"]), is_primary=bool(linha["is_primary"]), storage=str(linha["storage"] or "disk"),
        storage_key=_texto(linha["storage_key"]), original_key=_texto(linha["original_key"]),
        spec_json=str(linha["spec"] or "{}"), prompt_sha256=_texto(linha["prompt_sha256"]),
        provider=_texto(linha["provider"]), model=_texto(linha["model"]), seed=_inteiro(linha["seed"]) or 0,
        provider_seed=_texto(linha["provider_seed"]), provider_request_id=_texto(linha["provider_request_id"]),
        width=_inteiro(linha["width"]), height=_inteiro(linha["height"]), bytes_sha256=_texto(linha["bytes_sha256"]),
        cost_usd=float(linha["cost_usd"] or 0.0), error=_texto(linha["error"]), created_at=str(linha["created_at"]))


class StorageBlobs:
    """`ImageBlobStore` sobre o `Storage` dos avatares (disco por omissão, S3 por bandeira)."""

    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    async def put(self, key: str, data: bytes, *, content_type: str) -> None:
        await put_async(self._storage, key, data, content_type=content_type)

    def get(self, key: str) -> bytes | None:
        return self._storage.get(key)

    def exists(self, key: str) -> bool:
        return self._storage.exists(key)

    def delete(self, key: str) -> None:
        self._storage.delete_prefix(key)


class AiCallsAccounting:
    """`ImageAccounting` sobre `ai_calls` (linha `role='image'` com `usd` declarado) e os limites de `settings`."""

    def __init__(self, db: Database, prices: dict[str, list[float]], settings_getter: Callable[[], object],
                 cfg: Config | None = None) -> None:
        self._db = db
        self._prices = prices
        self._settings = settings_getter
        self._cfg = cfg

    def balance_block_reason(self) -> str | None:
        if self._cfg is None or self._cfg.file.ai.image.provider == "simulated":
            return None
        return saldos.motivo_de_bloqueio(self._db, self._cfg, self._cfg.file.ai.image.provider)

    def record_exhausted(self, detail: str) -> None:
        """Sem crédito na API de imagens: leitura de saldo 0 da conta do gerador (ADR-051)."""
        if self._cfg is not None:
            saldos.registrar_esgotado(self._db, self._cfg, self._cfg.file.ai.image.provider, detail)

    def spent_today_usd(self) -> float:
        return costs.spent_today_usd(self._db, self._prices)

    def daily_limit_usd(self) -> float:
        return float(getattr(self._settings(), "ai_max_usd_per_day", 0.0) or 0.0)

    def record(self, *, provider: str, model: str, usd: float, ms: int, ok: bool, error: str | None) -> None:
        # `add_usage` (taskqueue) conta tokens; imagem não tem token — a linha nasce aqui, com `usd` declarado.
        # `provider='simulated'` continua fora do gasto (`costs.spent_usd` já o exclui).
        self._db.execute(
            "INSERT INTO ai_calls(ts, role, model, tier, input_tokens, cache_read, cache_write, output_tokens,"
            " with_image, ms, ok, requested_model, provider, error_kind, error_message, usd)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (now_iso(), "image", model, 0, 0, 0, 0, 0, 0, int(ms), int(ok), model, provider,
             None if ok else "image", None if ok else (error or "")[:500], float(usd)))


def construir_gerador(cfg: Config) -> ImageGenerator:
    imagem = cfg.file.ai.image
    if imagem.provider == "openai":
        chave = cfg.env.openai_api_key.get_secret_value() if cfg.env.openai_api_key else None
        return OpenAIImageGenerator(api_key=chave, model=imagem.model, quality=imagem.quality,
                                    price_per_image=imagem.price_per_image, price_per_mtok=imagem.price_per_mtok,
                                    timeout_s=imagem.timeout_s)
    return SimulatedImageGenerator()


def compor_servico_de_imagens(cfg: Config, *, db: Database, storage: Storage, bus: EventSink,
                              settings_getter: Callable[[], object]) -> PersonaImageService:
    imagem = cfg.file.ai.image
    return PersonaImageService(
        generator=construir_gerador(cfg), records=SqlPersonaImages(db), blobs=StorageBlobs(storage),
        accounting=AiCallsAccounting(db, cfg.file.ai.prices, settings_getter, cfg), events=bus, new_id=lambda: f"img-{new_token()}",
        now_iso=now_iso, measure=dimensoes, per_persona=imagem.per_persona, on_create=imagem.on_create)


def imagens_dto(registros: list[PersonaImageRecord]) -> list[PersonaImageDTO]:
    return [PersonaImageDTO(
        id=r.id, persona_id=r.persona_id, status=r.status, source=r.source, is_primary=r.is_primary, width=r.width,
        height=r.height, provider=r.provider, model=r.model, seed=r.seed or None, aspect=r.aspect, cost_usd=r.cost_usd,
        error=r.error, created_at=r.created_at, url=f"/api/personas/{r.persona_id}/images/{r.id}") for r in registros]


def identidade_para_foto(dto: PersonaDTO) -> PersonaIdentity:
    """O que da pessoa entra na receita da foto — sem o nome. As iniciais só pintam o carimbo do simulado."""
    iniciais = "".join(p[0] for p in dto.name.split()[:2] if p)
    return PersonaIdentity(
        appearance=dto.visual.appearance, visual_style=dto.visual.visual_style,
        photo_scenario=dto.visual.photo_scenario, interests=tuple(dto.traits.interests[:5]), age=dto.age,
        gender=dto.gender or dto.visual.gender_presentation, profession=dto.biography.work.profession,
        city=dto.biography.home.city, palette=dto.visual.palette, initials=iniciais)


def status_de_imagem(servico: PersonaImageService, cfg: Config) -> AiImageStatus:
    gerador = servico.generator
    imagem = cfg.file.ai.image
    preco = imagem.price_per_image.get(imagem.quality) if not gerador.simulated else 0.0
    return AiImageStatus(provider=gerador.name, model=gerador.model, quality=imagem.quality,
                         configured=gerador.configured, simulated=gerador.simulated,
                         sends_data_externally=gerador.sends_data_externally, per_persona=servico.per_persona,
                         on_create=servico.on_create, price_per_image_usd=preco)


def spec_json_valido(texto: str) -> bool:
    """Para os testes e o painel: a receita gravada é JSON legível."""
    try:
        return isinstance(json.loads(texto), dict)
    except ValueError:
        return False
