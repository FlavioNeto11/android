"""`GET /api/instagram/personas-pendentes` (ponte android <-> igfarm, migracao 132). Nivel de prova: `simulated`
(`SimulatedProvider`, gerador de imagem simulado, leitor de e-mail falso). Nada de IA paga nem de IMAP real."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.main import create_app
from app.models import PersonaCreate, ProfileCreate
from app.modules.email_do_parque.application.servico import ConfigEmail, EmailDoParque
from app.social.contas_nossas import registrar_lapide

from .conftest import Harness

pytestmark = pytest.mark.asyncio

URL = "/api/instagram/personas-pendentes"
DOM = "nvit.com.br"


def _cliente(harness: Harness) -> httpx.AsyncClient:
    harness.state.email_parque = EmailDoParque(ConfigEmail(DOM, (DOM,), True), None)
    harness.cfg.file.contas.criacao_pela_api_do_igfarm = True    # 31.335: o padrão é desligado
    app = create_app(harness.cfg, state=harness.state)
    app.state.poc = harness.state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _persona(h: Harness, nome: str, sobrenome: str, nasc: str | None = "1990-05-17", locale: str = "pt-BR") -> str:
    return h.state.social.create_persona(PersonaCreate(
        name=f"{nome} {sobrenome}", first_name=nome, last_name=sobrenome, birth_date=nasc, locale=locale,
        gender="feminino", summary=f"{nome} gosta de ceramica e trilhas")).id


async def test_lista_com_email_no_dominio_username_valido_e_sugestao_estavel(harness: Harness) -> None:
    ids = {_persona(harness, "Ana", "Lima"), _persona(harness, "Bruno", "Souza"), _persona(harness, "Clara", "Dias")}
    async with _cliente(harness) as c:
        r1 = await c.get(URL, params={"dominio": DOM, "limite": 10, "reservar": "false"})
        assert r1.status_code == 200, r1.text
        lista = r1.json()
        assert {p["persona_id"] for p in lista} == ids
        for p in lista:
            assert p["email_sugerido"].endswith("@" + DOM) and p["email_sugerido"].count("@") == 1
            assert 3 <= len(p["username_sugerido"]) <= 30 and not p["username_sugerido"].endswith(".")
            assert p["birth_date"] == "1990-05-17" and p["biografia"] is not None
            assert p["imagem_perfil"] is None and p["imagem_pendente"] is True   # listar nao gera foto
        assert len({p["email_sugerido"] for p in lista}) == 3 and len({p["username_sugerido"] for p in lista}) == 3
        chamadas = harness.ai.calls if hasattr(harness.ai, "calls") else None
        # Sugestao persistente: o segundo GET devolve o mesmo e-mail e o mesmo @, sem nova chamada de IA.
        r2 = await c.get(URL, params={"dominio": DOM, "reservar": "false"})

        def par(rs: httpx.Response) -> set[tuple[str, str, str]]:
            return {(p["persona_id"], p["email_sugerido"], p["username_sugerido"]) for p in rs.json()}
        assert par(r1) == par(r2)
        if chamadas is not None:
            assert harness.ai.calls == chamadas


async def test_reservar_gera_imagem_nao_repete_persona_e_vence_sozinha(harness: Harness) -> None:
    for n in ("Ana", "Bruno", "Clara"):
        _persona(harness, n, "Lima")
    async with _cliente(harness) as c:
        r1 = await c.get(URL, params={"dominio": DOM, "limite": 2, "reservar": "true"})
        assert r1.status_code == 200, r1.text
        a = r1.json()
        assert len(a) == 2 and all(p["imagem_perfil"] and not p["imagem_pendente"] for p in a)
        assert all(p["imagem_perfil"].startswith(f"/api/personas/{p['persona_id']}/images/") for p in a)
        img = await c.get(a[0]["imagem_perfil"])
        assert img.status_code == 200 and img.content[:2] in (b"\xff\xd8", b"\x89P")
        b = (await c.get(URL, params={"dominio": DOM, "limite": 5, "reservar": "true"})).json()
        assert len(b) == 1 and b[0]["persona_id"] not in {p["persona_id"] for p in a}
        assert (await c.get(URL, params={"dominio": DOM, "reservar": "true"})).json() == []
        # A reserva vence sozinha: com a validade no passado a pessoa volta, com a MESMA sugestao e a mesma foto.
        passado = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        harness.state.db.execute("UPDATE persona_reservas SET expira_em=?", (passado,))
        volta = {p["persona_id"]: p for p in (await c.get(URL, params={"dominio": DOM, "limite": 10})).json()}
        assert {p["persona_id"] for p in a} <= set(volta)
        assert volta[a[0]["persona_id"]]["username_sugerido"] == a[0]["username_sugerido"]
        assert volta[a[0]["persona_id"]]["imagem_perfil"] == a[0]["imagem_perfil"]


async def test_com_imagem_false_nunca_gera(harness: Harness) -> None:
    pid = _persona(harness, "Ana", "Lima")
    async with _cliente(harness) as c:
        r = await c.get(URL, params={"dominio": DOM, "reservar": "true", "com_imagem": "false"})
        assert r.status_code == 200 and r.json()[0]["imagem_perfil"] is None and r.json()[0]["imagem_pendente"] is False
        assert harness.state.persona_images.listar(pid) == []


async def test_exclui_quem_tem_conta_email_lapide_menor_ou_sem_data(harness: Harness) -> None:
    livre = _persona(harness, "Livre", "Silva")
    ano = datetime.now().year - 15
    _persona(harness, "Menor", "Silva", nasc=f"{ano}-01-01")
    _persona(harness, "Semdata", "Silva", nasc=None)
    com_email = _persona(harness, "Email", "Silva")
    harness.state.db.execute("UPDATE instagram_profiles SET email='x@y.com' WHERE id=?", (com_email,))
    bloqueada = _persona(harness, "Lapide", "Silva")
    registrar_lapide(harness.state.db, app_id="com.instagram.android", handle="velha", profile_id=bloqueada)
    harness.state.social.create_profile(ProfileCreate(username="ja.tem.conta", first_name="Tem", last_name="Conta"))
    async with _cliente(harness) as c:
        r = await c.get(URL, params={"dominio": DOM})
        assert [p["persona_id"] for p in r.json()] == [livre]


async def test_filtra_por_locale(harness: Harness) -> None:
    pt = _persona(harness, "Ana", "Lima")
    outra = _persona(harness, "John", "Doe", locale="en-US")
    async with _cliente(harness) as c:
        r = await c.get(URL, params={"dominio": DOM, "locale": "en-US"})
        assert [p["persona_id"] for p in r.json()] == [outra] and pt not in {p["persona_id"] for p in r.json()}


async def test_dominio_fora_da_allowlist_e_limite_invalido(harness: Harness) -> None:
    _persona(harness, "Ana", "Lima")
    async with _cliente(harness) as c:
        r = await c.get(URL, params={"dominio": "gmail.com"})
        assert r.status_code == 422 and r.json()["detail"]["code"] == "dominio_nao_permitido"
        assert (await c.get(URL, params={"dominio": DOM, "limite": 0})).status_code == 422
        assert (await c.get(URL, params={"dominio": DOM, "limite": 51})).status_code == 422


async def test_sem_provedor_de_ia_e_ai_unavailable(harness: Harness) -> None:
    _persona(harness, "Ana", "Lima")
    harness.state.social.provider = None
    async with _cliente(harness) as c:
        r = await c.get(URL, params={"dominio": DOM})
        assert r.status_code == 503 and r.json()["detail"]["code"] == "ai_unavailable"


async def test_gerador_de_imagem_sem_chave_e_409_e_nada_fica_reservado(harness: Harness) -> None:
    _persona(harness, "Ana", "Lima")

    class SemChave:
        name, model, simulated, configured, sends_data_externally = "openai", "m", False, False, True

    original = harness.state.persona_images.generator
    harness.state.persona_images.generator = SemChave()  # type: ignore[assignment]
    try:
        async with _cliente(harness) as c:
            r = await c.get(URL, params={"dominio": DOM, "reservar": "true"})
            assert r.status_code == 409 and r.json()["detail"]["code"] == "image_not_configured"
            assert harness.state.db.scalar("SELECT COUNT(*) FROM persona_reservas WHERE reservada_em IS NOT NULL") == 0
    finally:
        harness.state.persona_images.generator = original
