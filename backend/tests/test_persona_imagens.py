"""Imagens da persona (migração 048, evolução 2, onda A): receita, geradores, serviço, custo, rotas e avatar.

Tudo `simulated`: o gerador simulado pinta com Pillow; o OpenAI é exercitado contra `httpx.MockTransport` (o
CONTRATO da chamada, não uma imagem de modelo). Chamada real de imagem é `not_run` (chave e autorização do dono).
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from PIL import Image

from app.db import INTEGRITY_ERRORS, Database
from app.main import create_app
from app.models import PersonaBiography, PersonaCreate, PersonaTraits, PersonaVisual
from app.modules.identity.adapters.openai_images import OpenAIImageGenerator
from app.modules.identity.adapters.pos_processamento import dimensoes, pos_processar
from app.modules.identity.adapters.simulated_images import SimulatedImageGenerator, pintar
from app.modules.identity.application.persona_images import EVENTO_IMAGEM, PersonaImageService
from app.modules.identity.application.ports import GeneratedImage
from app.modules.identity.domain.persona_image import (SPEC_VERSION, GeracaoFalhou, GeracaoRecusada, MenorDeIdade,
                                                       OrcamentoEsgotado, PersonaIdentity, PersonaImageSpec,
                                                       montar_spec, semente)
from app.modules.identity.infrastructure.persona_images import (AiCallsAccounting, SqlPersonaImages, StorageBlobs,
                                                                identidade_para_foto, imagens_dto)
from app.planning import costs
from app.storage import DiskStorage

from .conftest import make_config
from .test_db import _Falso, _banco

ORIGEM = Path(__file__).resolve().parents[1] / "migrations"
TS = "2026-09-27T12:00:00Z"
IDENTIDADE = PersonaIdentity(appearance="cabelo cacheado castanho, estatura média", visual_style="casual de linho",
                             photo_scenario="varanda com plantas", interests=("cerâmica", "trilha"), age=31,
                             gender="feminino", profession="designer", city="Curitiba", palette="terrosa",
                             initials="ML")


def _png(largura: int = 64, altura: int = 64, cor: tuple[int, int, int] = (200, 30, 30)) -> bytes:
    saida = io.BytesIO()
    Image.new("RGB", (largura, altura), cor).save(saida, format="PNG")
    return saida.getvalue()


def _pixels(png: bytes) -> str:
    with Image.open(io.BytesIO(png)) as imagem:
        return hashlib.sha256(imagem.convert("RGB").tobytes()).hexdigest()


# ---------------------------------------------------------------- receita (domínio puro)
def test_semente_e_receita_sao_deterministicas_e_a_principal_e_um_busto_quadrado() -> None:
    esperado = int.from_bytes(hashlib.sha256(f"ig-abc:2:{SPEC_VERSION}".encode()).digest()[:4], "big") & 0x7FFFFFFF
    assert semente("ig-abc", 2) == esperado and 0 <= esperado < 2 ** 31
    a, b = montar_spec(IDENTIDADE, "ig-abc", 0), montar_spec(IDENTIDADE, "ig-abc", 0)
    assert a == b and a.index == 0 and a.aspect == "1:1" and a.framing == "head and shoulders"
    assert "face clearly visible" in a.pose and a.final_size == (1080, 1080) and a.provider_size == (1024, 1024)
    outra = montar_spec(IDENTIDADE, "ig-abc", 1)
    assert outra.seed != a.seed and outra.prompt != a.prompt
    for spec in (a, outra):
        assert spec.prompt.startswith("Candid amateur photo of a fictional 31-year-old adult woman who works as a designer")
        assert "no text, no logo, no watermark" in spec.prompt and "not a real person" in spec.prompt
        assert "ML" not in spec.prompt and "<" not in spec.prompt          # iniciais e marcação nunca vão ao prompt
        assert "Curitiba" in spec.prompt and "varanda com plantas" in spec.prompt
        assert 55 <= spec.jpeg_quality <= 92 and 0 <= spec.noise_sigma <= 4 and 0.6 <= spec.downscale <= 1.0
    assert PersonaImageSpec.from_json(a.to_json()) == a and a.prompt_sha256 == hashlib.sha256(a.prompt.encode()).hexdigest()
    with pytest.raises(MenorDeIdade):
        montar_spec(PersonaIdentity(age=17), "ig-abc", 0)
    sem_idade = montar_spec(PersonaIdentity(), "ig-x", 3)
    assert sem_idade.prompt.startswith("Candid amateur photo of a fictional adult person.")


# ---------------------------------------------------------------- adaptadores
async def test_simulado_pinta_os_mesmos_pixels_para_a_mesma_semente() -> None:
    spec = montar_spec(IDENTIDADE, "ig-abc", 0)
    assert _pixels(pintar(spec)) == _pixels(pintar(spec))
    assert _pixels(pintar(spec)) != _pixels(pintar(montar_spec(IDENTIDADE, "ig-abc", 1)))
    gerador = SimulatedImageGenerator()
    assert gerador.simulated and not gerador.sends_data_externally and gerador.configured
    imagem = await gerador.generate(spec)
    assert imagem.mime == "image/jpeg" and (imagem.width, imagem.height) == (1080, 1080) and imagem.usd == 0.0
    assert imagem.data[:2] == b"\xff\xd8" and dimensoes(imagem.data) == (1080, 1080)
    assert imagem.original is not None and imagem.original[:4] == b"\x89PNG" and imagem.provider_seed == str(spec.seed)
    retrato = montar_spec(IDENTIDADE, "ig-abc", 1)
    jpeg, w, h = pos_processar(_png(600, 400), retrato)
    assert (w, h) == retrato.final_size and dimensoes(jpeg) == (w, h)
    assert dimensoes(b"nao e imagem") is None


async def test_openai_generations_edits_e_erros_por_transporte_falso() -> None:
    pedidos: list[httpx.Request] = []
    respostas: list[httpx.Response] = []

    def handler(request: httpx.Request) -> httpx.Response:
        pedidos.append(request)
        return respostas.pop(0)

    gerador = OpenAIImageGenerator(api_key="chave-de-teste-nao-e-segredo", model="gpt-image-1-mini", quality="medium",
                                   price_per_image={"low": 0.005, "medium": 0.011, "high": 0.036})
    gerador.set_client(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert gerador.configured and gerador.price == 0.011 and gerador.sends_data_externally
    corpo_ok = {"data": [{"b64_json": base64.b64encode(_png(1024, 1024)).decode()}]}
    spec = montar_spec(IDENTIDADE, "ig-abc", 0)
    respostas.append(httpx.Response(200, json=corpo_ok, headers={"x-request-id": "req-1"}))
    imagem = await gerador.generate(spec)
    corpo = json.loads(pedidos[0].content)
    assert str(pedidos[0].url) == "https://api.openai.com/v1/images/generations"
    assert corpo == {"model": "gpt-image-1-mini", "prompt": spec.prompt, "size": "1024x1024", "quality": "medium",
                     "n": 1, "output_format": "png"}
    assert pedidos[0].headers["authorization"].startswith("Bearer ") and imagem.provider_request_id == "req-1"
    assert imagem.usd == 0.011 and (imagem.width, imagem.height) == (1080, 1080) and imagem.original == base64.b64decode(corpo_ok["data"][0]["b64_json"])
    # A partir da segunda, a principal vai como referência: `/images/edits`, multipart.
    respostas.append(httpx.Response(200, json=corpo_ok))
    await gerador.generate(montar_spec(IDENTIDADE, "ig-abc", 1), reference=imagem.data)
    assert str(pedidos[1].url).endswith("/images/edits") and pedidos[1].headers["content-type"].startswith("multipart/form-data")
    assert b'name="image"' in pedidos[1].content and spec.identity.appearance.encode() in pedidos[1].content
    # Recusa do filtro é recusa (nunca simulado no lugar); credencial e cota viram falha tipada.
    respostas.append(httpx.Response(400, json={"error": {"code": "content_policy_violation", "message": "safety"}}))
    with pytest.raises(GeracaoRecusada):
        await gerador.generate(spec)
    respostas.append(httpx.Response(401, text="unauthorized"))
    with pytest.raises(GeracaoFalhou) as exc:
        await gerador.generate(spec)
    assert exc.value.kind == "not_configured" and exc.value.status == 401
    respostas.append(httpx.Response(429, text="slow down"))
    with pytest.raises(GeracaoFalhou) as exc:
        await gerador.generate(spec)
    assert exc.value.retryable
    respostas.append(httpx.Response(200, json={"data": []}))
    with pytest.raises(GeracaoFalhou):
        await gerador.generate(spec)
    sem_chave = OpenAIImageGenerator(api_key=None)
    assert not sem_chave.configured
    with pytest.raises(GeracaoFalhou) as exc:
        await sem_chave.generate(spec)
    assert exc.value.kind == "not_configured" and len(pedidos) == 6
    await gerador.aclose()


async def test_gpt_image_2_custo_pelo_usage_da_resposta() -> None:
    """Fase 17: o `gpt-image-2` publica preço por TOKEN, não por imagem. Com `usage` na resposta, o custo é tokens ×
    `price_per_mtok` (texto, imagem de entrada e saída separados); sem `usage`, vale o preço declarado por imagem —
    nunca zero."""
    respostas: list[httpx.Response] = []
    gerador = OpenAIImageGenerator(api_key="chave-de-teste-nao-e-segredo", quality="medium",
                                   price_per_image={"medium": 0.06},
                                   price_per_mtok={"text_in": 5.0, "image_in": 8.0, "output": 30.0})
    gerador.set_client(httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: respostas.pop(0))))
    assert gerador.model == "gpt-image-2"
    imagem_b64 = base64.b64encode(_png(1024, 1024)).decode()
    spec = montar_spec(IDENTIDADE, "ig-abc", 0)
    uso = {"input_tokens": 150, "output_tokens": 1056,
           "input_tokens_details": {"text_tokens": 50, "image_tokens": 100}}
    respostas.append(httpx.Response(200, json={"data": [{"b64_json": imagem_b64}], "usage": uso}))
    assert (await gerador.generate(spec)).usd == pytest.approx((50 * 5 + 100 * 8 + 1056 * 30) / 1e6)
    # Sem o detalhe, a entrada inteira conta como imagem (a tarifa mais cara das duas).
    respostas.append(httpx.Response(200, json={"data": [{"b64_json": imagem_b64}],
                                               "usage": {"input_tokens": 150, "output_tokens": 1056}}))
    assert (await gerador.generate(spec)).usd == pytest.approx((150 * 8 + 1056 * 30) / 1e6)
    respostas.append(httpx.Response(200, json={"data": [{"b64_json": imagem_b64}]}))
    assert (await gerador.generate(spec)).usd == 0.06
    await gerador.aclose()


# ---------------------------------------------------------------- serviço
class _Contas:
    def __init__(self, limite: float = 0.0, gasto: float = 0.0):
        self.limite, self.gasto, self.linhas = limite, gasto, []

    def spent_today_usd(self) -> float:
        return self.gasto

    def daily_limit_usd(self) -> float:
        return self.limite

    def record(self, **campos: Any) -> None:
        self.linhas.append(campos)


class _Eventos:
    def __init__(self) -> None:
        self.itens: list[tuple[str, dict[str, object] | None]] = []

    def emit(self, kind: str, message: str, *, level: str = "info", instance_id: str | None = None,
             data: dict[str, object] | None = None) -> None:
        self.itens.append((kind, data))


class _Gerador:
    """Gerador de teste: registra se veio referência e falha/recusa a pedido."""

    name, model, simulated, sends_data_externally, configured = "duble", "m", False, True, True

    def __init__(self, erro: Exception | None = None):
        self.erro = erro
        self.referencias: list[bool] = []

    async def generate(self, spec: PersonaImageSpec, *, reference: bytes | None = None) -> GeneratedImage:
        self.referencias.append(reference is not None)
        if self.erro is not None:
            raise self.erro
        return GeneratedImage(data=_png(8, 8), mime="image/jpeg", width=8, height=8, usd=0.011, ms=5,
                              original=_png(8, 8), original_mime="image/png")


def _servico(tmp_path: Path, gerador: Any, contas: _Contas | None = None) -> tuple[PersonaImageService, Database, DiskStorage, _Eventos, _Contas]:
    db = _banco(tmp_path)
    db.migrate()
    db.execute("INSERT INTO instagram_profiles(id, username, display_name, created_at, updated_at) VALUES ('ig-1','','Marina Lopes',?,?)",
               (TS, TS))
    storage = DiskStorage(tmp_path / "data")
    eventos, contas = _Eventos(), contas or _Contas()
    contador = iter(range(1, 100))
    svc = PersonaImageService(generator=gerador, records=SqlPersonaImages(db), blobs=StorageBlobs(storage),
                              accounting=contas, events=eventos, new_id=lambda: f"img-{next(contador)}",
                              now_iso=lambda: TS, measure=dimensoes, per_persona=1, on_create=True)
    return svc, db, storage, eventos, contas


async def test_gera_guarda_registra_custo_e_define_a_principal(tmp_path: Path) -> None:
    svc, db, storage, eventos, contas = _servico(tmp_path, SimulatedImageGenerator())
    try:
        registros = await svc.gerar("ig-1", IDENTIDADE, count=2)
        assert [r.status for r in registros] == ["ready", "ready"] and [r.is_primary for r in registros] == [True, False]
        assert registros[0].storage_key == "personas/ig-1/img-1.jpg" and registros[0].original_key == "personas/ig-1/img-1.orig.png"
        assert storage.exists("personas/ig-1/img-1.jpg") and storage.exists("personas/ig-1/img-2.orig.png")
        assert PersonaImageSpec.from_json(registros[1].spec_json).index == 1 and registros[1].aspect in ("1:1", "4:5", "3:4", "9:16")
        assert registros[0].bytes_sha256 == hashlib.sha256(storage.get("personas/ig-1/img-1.jpg") or b"").hexdigest()
        assert [linha["ok"] for linha in contas.linhas] == [True, True] and all(l["usd"] == 0.0 for l in contas.linhas)
        assert [e[0] for e in eventos.itens] == [EVENTO_IMAGEM, EVENTO_IMAGEM] and eventos.itens[0][1] == {
            "profile_id": "ig-1", "image_id": "img-1", "status": "ready"}
        assert svc.principal("ig-1") is not None and svc.principal("ig-1").id == "img-1"
        # A receita de índice k é sempre a mesma: repetir não repete foto, continua a numeração.
        mais = await svc.gerar("ig-1", IDENTIDADE, count=1)
        assert PersonaImageSpec.from_json(mais[0].spec_json).index == 2 and len(svc.listar("ig-1")) == 3
        dtos = imagens_dto(svc.listar("ig-1"))
        assert dtos[0].url == "/api/personas/ig-1/images/img-1" and dtos[0].is_primary and dtos[0].provider == "simulated"
        with pytest.raises(MenorDeIdade):
            await svc.gerar("ig-1", PersonaIdentity(age=16), count=1)
    finally:
        db.close()


async def test_referencia_teto_recusa_e_falha(tmp_path: Path) -> None:
    gerador = _Gerador()
    svc, db, storage, eventos, contas = _servico(tmp_path, gerador, _Contas(limite=1.0, gasto=0.5))
    try:
        await svc.gerar("ig-1", IDENTIDADE, count=2)
        assert gerador.referencias == [False, True]                        # a segunda leva a principal como referência
        assert [l["usd"] for l in contas.linhas] == [0.011, 0.011]
        contas.gasto = 1.0
        with pytest.raises(OrcamentoEsgotado):
            await svc.gerar("ig-1", IDENTIDADE, count=1)
        contas.gasto, gerador.erro = 0.0, GeracaoRecusada("filtro")
        recusada = await svc.gerar("ig-1", IDENTIDADE, count=3)
        assert len(recusada) == 1 and recusada[0].status == "refused" and recusada[0].error == "filtro"
        assert recusada[0].storage_key is None and not storage.exists("personas/ig-1/img-3.jpg")
        assert contas.linhas[-1]["ok"] is False and contas.linhas[-1]["usd"] == 0.0
        assert eventos.itens[-1][1] == {"profile_id": "ig-1", "image_id": "img-3", "status": "refused"}
        gerador.erro = GeracaoFalhou("rede", retryable=True)
        falhou = await svc.gerar("ig-1", IDENTIDADE, count=1)
        assert falhou[0].status == "failed" and "rede" in (falhou[0].error or "")
        assert svc.principal("ig-1").id == "img-1"                         # a principal não mudou com as falhas
    finally:
        db.close()


async def test_upload_principal_apagar_e_avatar_legado(tmp_path: Path) -> None:
    svc, db, storage, _eventos, _contas = _servico(tmp_path, SimulatedImageGenerator())
    try:
        primeira = await svc.registrar_upload("ig-1", _png(40, 30), "image/png")
        segunda = await svc.registrar_upload("ig-1", _png(20, 20, (1, 2, 3)), "image/png")
        assert primeira.is_primary and (primeira.width, primeira.height) == (40, 30) and primeira.source == "upload"
        assert not segunda.is_primary and segunda.storage_key == "personas/ig-1/img-2.png"
        assert svc.definir_principal("ig-1", segunda.id).is_primary and not svc.obter("ig-1", primeira.id).is_primary
        with pytest.raises(KeyError):
            svc.definir_principal("ig-1", "img-nao-existe")
        svc.apagar("ig-1", segunda.id)
        assert not storage.exists("personas/ig-1/img-2.png") and svc.principal("ig-1") is None
        # Avatar legado: só entra em quem não tem imagem nenhuma, e nunca é apagado do disco.
        assert svc.importar_legado("ig-1") is None                          # já tem a primeira (upload)
        svc.apagar("ig-1", primeira.id)
        storage.put("avatars/ig-1.jpg", _png(360, 360), content_type="image/jpeg")
        legado = svc.importar_legado("ig-1")
        assert legado is not None and legado.source == "imported_legacy" and legado.is_primary and legado.storage_key == "avatars/ig-1.jpg"
        assert svc.importar_legado("ig-1") is None                          # idempotente
        svc.apagar("ig-1", legado.id)
        assert storage.exists("avatars/ig-1.jpg") and svc.listar("ig-1") == []
    finally:
        db.close()


# ---------------------------------------------------------------- custo e migração
def test_custo_declarado_da_imagem_entra_no_gasto_do_dia(tmp_path: Path) -> None:
    db = _banco(tmp_path)
    try:
        db.migrate()
        precos = {"claude-haiku-4-5": [1.0, 0.1, 1.25, 5.0]}
        contas = AiCallsAccounting(db, precos, lambda: type("S", (), {"ai_max_usd_per_day": 2.5})())
        assert contas.daily_limit_usd() == 2.5 and contas.spent_today_usd() == 0.0
        contas.record(provider="openai", model="gpt-image-1-mini", usd=0.011, ms=900, ok=True, error=None)
        contas.record(provider="openai", model="gpt-image-1-mini", usd=0.011, ms=900, ok=True, error=None)
        contas.record(provider="simulated", model="degrade", usd=0.0, ms=1, ok=True, error=None)
        db.execute("INSERT INTO ai_calls(ts, role, model, input_tokens, output_tokens, ok, provider) VALUES (?,?,?,?,?,?,?)",
                   (costs.day_start_iso(), "decide", "claude-haiku-4-5", 1_000_000, 0, 1, "anthropic"))
        assert costs.spent_today_usd(db, precos) == pytest.approx(1.022)
        linha = db.one("SELECT role, usd, ok, requested_model FROM ai_calls WHERE role='image' LIMIT 1")
        assert linha == {"role": "image", "usd": 0.011, "ok": 1, "requested_model": "gpt-image-1-mini"}
    finally:
        db.close()


def test_migracao_048_cria_a_galeria_com_cascata_e_uma_principal_so(tmp_path: Path) -> None:
    db = _banco(tmp_path)
    try:
        assert "048_imagens_da_persona" in db.migrate()
        assert {"id", "persona_id", "spec", "seed", "status", "source", "is_primary", "cost_usd"} <= db.columns("persona_images")
        assert "usd" in db.columns("ai_calls")
        db.execute("INSERT INTO instagram_profiles(id, username, created_at, updated_at) VALUES ('ig-1','',?,?)", (TS, TS))
        for i in (1, 2):
            db.execute("INSERT INTO persona_images(id, persona_id, source, status, is_primary, created_at) VALUES (?,?,?,?,?,?)",
                       (f"img-{i}", "ig-1", "upload", "ready", 1 if i == 1 else 0, TS))
        with pytest.raises(INTEGRITY_ERRORS):
            db.execute("UPDATE persona_images SET is_primary=1 WHERE id='img-2'")
        db.execute("DELETE FROM instagram_profiles WHERE id='ig-1'")
        assert db.scalar("SELECT COUNT(*) FROM persona_images") == 0
    finally:
        db.close()
    for dialeto in ("sqlite", "postgres"):
        texto = _Falso(dialeto).render((ORIGEM / "048_imagens_da_persona.sql").read_text("utf-8"))
        assert "{{" not in texto and len(Database._instrucoes(texto)) == 4


# ---------------------------------------------------------------- HTTP
async def _esperar(c: httpx.AsyncClient, pid: str, prontas: int) -> list[dict[str, Any]]:
    for _ in range(200):
        lista = (await c.get(f"/api/personas/{pid}/images")).json()
        if sum(1 for i in lista if i["status"] == "ready") >= prontas:
            return lista
        await asyncio.sleep(0.05)
    raise AssertionError(f"as {prontas} imagens não ficaram prontas: {lista}")


async def test_rotas_de_imagem_e_o_avatar_da_pessoa(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    app = create_app(cfg)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        async with app.router.lifespan_context(app):
            ia = (await c.get("/api/ai")).json()
            assert ia["image"] == {"provider": "simulated", "model": "degrade-por-semente", "quality": "medium",
                                   "configured": True, "simulated": True, "sends_data_externally": False,
                                   "per_persona": 1, "on_create": True, "price_per_image_usd": 0.0}
            criada = await c.post("/api/personas", json=PersonaCreate(
                name="Marina Lopes", birth_date="1995-03-10", gender="feminino",
                traits=PersonaTraits(interests=["cerâmica"]), visual=PersonaVisual(appearance="cacheada"),
                biography=PersonaBiography.model_validate({"home": {"city": "Curitiba"}})).model_dump(mode="json"))
            assert criada.status_code == 201
            pid = criada.json()["id"]
            # `on_create`: a primeira imagem sai em segundo plano, simulada, e vira a principal.
            lista = await _esperar(c, pid, 1)
            assert len(lista) == 1 and lista[0]["is_primary"] and lista[0]["provider"] == "simulated" and lista[0]["aspect"] == "1:1"
            img = lista[0]
            bytes_img = await c.get(img["url"])
            assert bytes_img.status_code == 200 and bytes_img.headers["content-type"].startswith("image/jpeg")
            assert bytes_img.content[:2] == b"\xff\xd8" and dimensoes(bytes_img.content) == (1080, 1080)
            avatar = await c.get(f"/api/instagram/profiles/{pid}/avatar")
            assert avatar.status_code == 200 and avatar.content == bytes_img.content
            pessoa = (await c.get(f"/api/personas/{pid}")).json()
            assert pessoa["primary_image_id"] == img["id"] and [i["id"] for i in pessoa["images"]] == [img["id"]]
            # Mais duas a pedido: 202, e a numeração continua.
            pedido = await c.post(f"/api/personas/{pid}/images", json={"count": 2})
            assert pedido.status_code == 202 and pedido.json() == {"accepted": True, "persona_id": pid, "count": 2,
                                                                   "provider": "simulated", "simulated": True}
            lista = await _esperar(c, pid, 3)
            assert (await c.post(f"/api/personas/{pid}/images", json={"count": 9})).status_code == 422
            # Principal trocada; upload cru; apagar.
            segunda = lista[1]["id"]
            trocada = await c.put(f"/api/personas/{pid}/images/{segunda}/primary")
            assert trocada.status_code == 200 and trocada.json()["primary_image_id"] == segunda
            assert (await c.get(f"/api/instagram/profiles/{pid}/avatar")).content == (await c.get(lista[1]["url"])).content
            enviada = await c.post(f"/api/personas/{pid}/images", content=_png(50, 40), headers={"content-type": "image/png"})
            assert enviada.status_code == 201 and enviada.json()["source"] == "upload" and enviada.json()["width"] == 50
            assert (await c.post(f"/api/personas/{pid}/images", content=b"lixo", headers={"content-type": "image/png"})).status_code == 400
            assert (await c.delete(f"/api/personas/{pid}/images/{enviada.json()['id']}")).status_code == 204
            assert (await c.delete(f"/api/personas/{pid}/images/{enviada.json()['id']}")).status_code == 404
            assert (await c.get(f"/api/personas/{pid}/images/img-nao-existe")).status_code == 404
            assert len((await c.get(f"/api/personas/{pid}/images")).json()) == 3
            # Menor de idade não é fotografada nem a pedido.
            menor = (await c.post("/api/personas", json={"name": "Nova Demais", "birth_date": "2015-01-01"})).json()
            assert (await c.post(f"/api/personas/{menor['id']}/images", json={"count": 1})).status_code == 409
            await asyncio.sleep(0.1)                                        # a tarefa de `on_create` termina em falha silenciosa
            assert (await c.get(f"/api/personas/{menor['id']}/images")).json() == []


async def test_avatar_legado_e_importado_na_partida_e_servido_sem_imagem_gerada(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    cfg.file.ai.image.on_create = False
    app = create_app(cfg)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        async with app.router.lifespan_context(app):
            estado = app.state.poc
            pid = (await c.post("/api/personas", json={"name": "Bruno Ferreira"})).json()["id"]
            assert (await c.get(f"/api/instagram/profiles/{pid}/avatar")).status_code == 404
            (Path(cfg.data_dir) / "avatars").mkdir(parents=True, exist_ok=True)
            (Path(cfg.data_dir) / "avatars" / f"{pid}.jpg").write_bytes(_png(360, 360))
            assert (await c.get(f"/api/instagram/profiles/{pid}/avatar")).status_code == 200   # o jpg legado responde
            estado._importar_avatares_legados()  # noqa: SLF001                # o que `start()` faz a cada partida
            estado._importar_avatares_legados()  # noqa: SLF001
            lista = (await c.get(f"/api/personas/{pid}/images")).json()
            assert len(lista) == 1 and lista[0]["source"] == "imported_legacy" and lista[0]["is_primary"]
            assert (await c.get(lista[0]["url"])).content == _png(360, 360)
            assert identidade_para_foto((await c.get(f"/api/personas/{pid}")).json() and estado.social.get_persona(pid)).initials == "BF"
