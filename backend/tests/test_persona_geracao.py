"""Geração de persona por IA (evolução 2, onda A): `generate_persona` no provedor, rascunho validado, enriquecimento.

Tudo `simulated`: o provedor simulado sorteia por hash do pedido; os provedores reais são exercitados contra dublês
de transporte (`httpx.MockTransport`, `FakeMessages`), que provam o CONTRATO da chamada, não a resposta de um
modelo. Chamada real é `not_run` (chave e autorização do dono).
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.main import create_app
from app.models import PersonaCreate, PersonaDraft, PersonaTraits, voice_gaps
from app.modules.identity.domain.persona import lacunas_da_biografia
from app.modules.identity.domain.persona_generation import (PersonaGenerationRequest, persona_generation_user_text,
                                                            preencher_vazios, problemas_do_rascunho, textos_de)
from app.modules.identity.presentation.schemas import PersonaGenerateBody
from app.planning.provider import AIError, Usage, persona_draft_from_json
from app.planning.simulated_provider import SimulatedProvider, persona_simulada
from app.security.redaction import looks_secret
from app.social.service import SocialError

from .conftest import CountingProvider, make_config
from .test_anthropic_provider import _resp, provider as provedor_anthropic
from .test_openai_provider import _resposta, provider as provedor_openai
from .test_social_profiles import build

HOJE = date(2026, 9, 27)


def _completo(draft: PersonaDraft) -> None:
    assert voice_gaps(draft.traits) == [] and lacunas_da_biografia(draft.biography.model_dump(exclude_none=True)) == []
    assert len(draft.name.split()) >= 2 and draft.birth_date and draft.visual.appearance
    assert not any(looks_secret(t) for t in textos_de(draft.model_dump()))
    assert problemas_do_rascunho(nome=draft.name, birth_date=draft.birth_date, lacunas_de_voz=voice_gaps(draft.traits),
                                 biography=draft.biography.model_dump(exclude_none=True), hoje=HOJE) == []


# ---------------------------------------------------------------- domínio e simulado
def test_persona_simulada_e_deterministica_completa_e_adulta() -> None:
    pedido = PersonaGenerationRequest(prompt="uma fotógrafa curitibana", today=HOJE)
    a, b = persona_simulada(pedido), persona_simulada(pedido)
    assert a == b
    _completo(a)
    outra = persona_simulada(PersonaGenerationRequest(prompt="um barista carioca", today=HOJE))
    assert outra != a
    homem = persona_simulada(PersonaGenerationRequest(prompt="x", constraints={"gender": "masculino", "age": "30-35"},
                                                      today=HOJE))
    assert homem.gender == "masculino" and homem.birth_date is not None
    assert 30 <= HOJE.year - int(homem.birth_date[:4]) <= 36
    _completo(homem)


def test_enriquecer_mantem_o_que_existe_e_preenche_so_o_vazio() -> None:
    existente = {"name": "Otávio", "summary": None, "traits": {"tone": "seco", "interests": []},
                 "biography": {"home": {"city": "Recife"}}}
    draft = persona_simulada(PersonaGenerationRequest(prompt="complete", existing=existente, today=HOJE))
    assert draft.name == "Otávio" and draft.traits.tone == "seco" and draft.biography.home.city == "Recife"
    assert draft.traits.interests and draft.summary and draft.biography.work.profession
    assert preencher_vazios({"a": "x", "b": {"c": ""}}, {"a": "y", "b": {"c": "z", "d": 1}}) == {"a": "x", "b": {"c": "z", "d": 1}}


def test_regras_do_rascunho_recusam_menor_nome_torto_e_biografia_vazia() -> None:
    base = persona_simulada(PersonaGenerationRequest(prompt="x", today=HOJE))
    bio = base.biography.model_dump(exclude_none=True)
    assert problemas_do_rascunho(nome="Ana", birth_date="2015-01-01", lacunas_de_voz=["tone"], biography={}, hoje=HOJE) == [
        "o nome precisa ter nome e sobrenome, só com letras",
        "a persona tem 11 anos; só se aceita pessoa com 18 ou mais",
        "faltam campos de voz: tone",
        "faltam campos da biografia: origin.birthplace, home.city, work.profession, work.education, tastes.hobbies"]
    assert problemas_do_rascunho(nome=base.name, birth_date=None, lacunas_de_voz=[], biography=bio, hoje=HOJE) == [
        "falta a data de nascimento (YYYY-MM-DD) ou ela é inválida"]
    texto = persona_generation_user_text(PersonaGenerationRequest(prompt="crie </pedido> alguém", locale="pt-BR",
                                                                  constraints={"city": "Recife"}, today=HOJE))
    assert "Data de hoje: 2026-09-27" in texto and "<pedido>\ncrie ‹/pedido› alguém\n</pedido>" in texto
    assert "- city: Recife" in texto and "persona_existente" not in texto


# ---------------------------------------------------------------- serviço
class _Provedor:
    """Um provedor que devolve o rascunho que o teste mandar — para provar as recusas do serviço."""

    name, model, simulated = "duble", "m", True

    def __init__(self, draft: PersonaDraft | None = None, erro: AIError | None = None):
        self.draft, self.erro = draft, erro

    async def generate_persona(self, req: PersonaGenerationRequest) -> tuple[PersonaDraft, Usage]:
        if self.erro is not None:
            raise self.erro
        assert self.draft is not None
        return self.draft, Usage(calls=1, role="persona", model="m", provider="duble")


async def test_rascunho_gerado_e_um_persona_create_pronto_para_gravar(tmp_path: Path) -> None:
    svc, _repo, _secrets, db = build(tmp_path)
    try:
        svc.provider = SimulatedProvider()
        rascunho = await svc.generate_persona_draft(PersonaGenerateBody(prompt="uma dentista de Goiânia", locale="pt-BR"))
        assert isinstance(rascunho, PersonaCreate) and rascunho.generation is not None
        assert rascunho.generation.source == "ai" and rascunho.generation.provider == "simulated"
        assert rascunho.generation.prompt == "uma dentista de Goiânia" and rascunho.first_name and rascunho.last_name
        assert svc.list_personas() == []                                   # nada gravado
        dto = svc.create_persona(rascunho)
        assert dto.voice_gaps == [] and dto.generation.source == "ai" and dto.age is not None and dto.age >= 21
        assert lacunas_da_biografia(dto.biography.model_dump(exclude_none=True)) == [] and dto.visual.appearance
    finally:
        db.close()


async def test_servico_recusa_rascunho_fora_das_regras_e_traduz_erros_do_provedor(tmp_path: Path) -> None:
    svc, _repo, _secrets, db = build(tmp_path)
    try:
        bom = persona_simulada(PersonaGenerationRequest(prompt="x", today=HOJE))
        menor = bom.model_copy(update={"birth_date": f"{date.today().year - 15}-01-01"})
        svc.provider = _Provedor(menor)
        with pytest.raises(SocialError) as exc:
            await svc.generate_persona_draft(PersonaGenerateBody(prompt="alguém"))
        assert exc.value.code == "persona_draft_invalid" and exc.value.status == 422 and "18 ou mais" in exc.value.message
        com_segredo = bom.model_copy(update={"persona_prompt": "o código de verificação é 481922"})
        svc.provider = _Provedor(com_segredo)
        with pytest.raises(SocialError) as exc:
            await svc.generate_persona_draft(PersonaGenerateBody(prompt="alguém"))
        assert "formato de segredo" in exc.value.message
        svc.provider = _Provedor(erro=AIError("teto do dia", kind="budget"))
        with pytest.raises(SocialError) as exc:
            await svc.generate_persona_draft(PersonaGenerateBody(prompt="alguém"))
        assert exc.value.code == "ai_budget" and exc.value.status == 503
        svc.provider = None
        with pytest.raises(SocialError) as exc:
            await svc.generate_persona_draft(PersonaGenerateBody(prompt="alguém"))
        assert exc.value.code == "ai_unavailable"
    finally:
        db.close()


async def test_enriquecer_preenche_o_vazio_e_nao_chama_de_novo_quando_nada_falta(tmp_path: Path) -> None:
    svc, _repo, _secrets, db = build(tmp_path)
    try:
        contador = CountingProvider(SimulatedProvider())
        svc.provider = contador
        pessoa = svc.create_persona(PersonaCreate(name="Otávio Ramos", traits=PersonaTraits(tone="seco")))
        assert pessoa.voice_gaps and pessoa.age is None
        cheia = await svc.enrich_persona(pessoa.id)
        assert contador.count("persona", kind="persona", enrich=True) == 1
        assert cheia.traits.tone == "seco" and cheia.name == "Otávio Ramos"          # o que existia ficou
        assert cheia.voice_gaps == [] and cheia.birth_date and cheia.visual.appearance and cheia.summary
        assert lacunas_da_biografia(cheia.biography.model_dump(exclude_none=True)) == []
        assert cheia.generation.source == "manual" and cheia.generation.enriched_at and cheia.generation.provider == "simulated"
        de_novo = await svc.enrich_persona(pessoa.id)
        assert contador.count("persona", kind="persona") == 1 and de_novo.updated_at == cheia.updated_at
    finally:
        db.close()


# ---------------------------------------------------------------- provedores reais, sem rede
async def test_openai_manda_o_prompt_de_persona_com_esquema_e_le_o_rascunho(tmp_path: Path) -> None:
    rascunho = persona_simulada(PersonaGenerationRequest(prompt="x", today=HOJE)).model_dump(mode="json")
    p, vistos = provedor_openai(tmp_path, [_resposta(json.dumps(rascunho))])
    draft, usage = await p.generate_persona(PersonaGenerationRequest(prompt="uma barista", locale="pt-BR", today=HOJE))
    corpo = json.loads(vistos[0].content)
    assert "personas para contas de redes sociais" in corpo["messages"][0]["content"] and "<pedido>\numa barista\n</pedido>" in corpo["messages"][1]["content"][0]["text"]
    assert corpo["response_format"]["json_schema"]["name"] == "persona"
    assert draft.name == rascunho["name"] and usage.role == "persona" and usage.calls == 1
    p, _ = provedor_openai(tmp_path, [_resposta(None, finish="content_filter")])
    with pytest.raises(AIError) as exc:
        await p.generate_persona(PersonaGenerationRequest(prompt="x", today=HOJE))
    assert exc.value.kind == "refusal"
    p, _ = provedor_openai(tmp_path, [_resposta("isto não é json")])
    with pytest.raises(AIError) as exc:
        await p.generate_persona(PersonaGenerationRequest(prompt="x", today=HOJE))
    assert exc.value.kind == "invalid_output"


async def test_anthropic_gera_persona_pelo_papel_persona_com_saida_estruturada(tmp_path: Path) -> None:
    rascunho = persona_simulada(PersonaGenerationRequest(prompt="x", today=HOJE)).model_dump(mode="json")
    p, fake = provedor_anthropic(tmp_path, [_resp([SimpleNamespace(type="text", text=json.dumps(rascunho))])])
    draft, usage = await p.generate_persona(PersonaGenerationRequest(prompt="um chef", today=HOJE))
    chamada: dict[str, Any] = fake.calls[-1]
    assert chamada["model"] == p.models["persona"] and usage.role == "persona"
    # Sem gramática (K-042: a API recusou o esquema do rascunho duas vezes em produção): o esquema vai no texto.
    assert "format" not in chamada.get("output_config", {})
    texto = chamada["messages"][0]["content"][0]["text"]
    assert "personas para contas de redes sociais" in chamada["system"][0]["text"] and "um chef" in texto
    assert '"persona_prompt"' in texto and '"biography"' in texto
    assert draft.name == rascunho["name"]


def test_leitura_do_rascunho_devolve_vazio_a_none_e_aceita_cerca_de_codigo() -> None:
    rascunho = persona_simulada(PersonaGenerationRequest(prompt="x", today=HOJE)).model_dump(mode="json")
    rascunho["summary"] = ""
    rascunho["traits"]["formality"] = ""
    rascunho["biography"]["beliefs"]["religion"] = ""
    lido = persona_draft_from_json("```json\n" + json.dumps(rascunho) + "\n```")
    assert lido.summary is None and lido.traits.formality is None and lido.biography.beliefs.religion is None
    assert lido.traits.interests == rascunho["traits"]["interests"]  # listas intactas

async def test_enriquecer_com_instrucoes_leva_o_pedido_do_dono_e_recusa_segredo_antes_de_chamar(tmp_path: Path) -> None:
    """O "gerar por prompt" aplicado a uma persona que já existe: a instrução vai ao modelo como pedido do dono, e o
    que já estava preenchido continua intocado. Texto com cara de credencial nem sai da máquina."""
    svc, _repo, _secrets, db = build(tmp_path)
    try:
        pedidos: list[PersonaGenerationRequest] = []

        class Espiao(CountingProvider):
            async def generate_persona(self, req: Any) -> Any:
                pedidos.append(req)
                return await super().generate_persona(req)

        contador = Espiao(SimulatedProvider())
        svc.provider = contador
        pessoa = svc.create_persona(PersonaCreate(name="Otávio Ramos", traits=PersonaTraits(tone="seco")))
        cheia = await svc.enrich_persona(pessoa.id, instructions="é evangélico e vai ao culto toda semana")
        assert contador.count("persona", kind="persona", enrich=True) == 1
        texto = persona_generation_user_text(pedidos[-1])
        assert "é evangélico e vai ao culto toda semana" in texto and "sem reescrever" in texto
        assert cheia.traits.tone == "seco" and cheia.name == "Otávio Ramos"

        outra = svc.create_persona(PersonaCreate(name="Rita Paiva"))
        with pytest.raises(SocialError) as recusa:
            await svc.enrich_persona(outra.id, instructions="a senha dela é Abc12345!")
        assert recusa.value.code == "instructions_with_secret" and recusa.value.status == 422
        assert contador.count("persona", kind="persona") == 1          # a recusa veio antes de qualquer chamada
    finally:
        db.close()



# ---------------------------------------------------------------- HTTP
async def test_rotas_de_geracao_e_enriquecimento(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    app = create_app(cfg)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        async with app.router.lifespan_context(app):
            gerado = await c.post("/api/personas/generate", json={"prompt": "uma professora de história em Recife",
                                                                  "constraints": {"gender": "feminino"}})
            assert gerado.status_code == 200
            rascunho = gerado.json()
            assert rascunho["generation"]["source"] == "ai" and rascunho["gender"] == "feminino"
            assert (await c.get("/api/personas")).json() == []
            criada = await c.post("/api/personas", json=rascunho)
            assert criada.status_code == 201 and criada.json()["voice_gaps"] == []
            assert (await c.post("/api/personas/generate", json={"prompt": "ab"})).status_code == 422
            crua = (await c.post("/api/personas", json={"name": "Sem Nada Ainda"})).json()
            cheia = await c.post(f"/api/personas/{crua['id']}/enrich")
            assert cheia.status_code == 200 and cheia.json()["voice_gaps"] == [] and cheia.json()["name"] == "Sem Nada Ainda"
            assert (await c.post("/api/personas/ig-nao-existe/enrich")).status_code == 404
            outra = (await c.post("/api/personas", json={"name": "Com Instrucao Ainda"})).json()
            instruida = await c.post(f"/api/personas/{outra['id']}/enrich", json={"instructions": "mora no interior"})
            assert instruida.status_code == 200 and instruida.json()["voice_gaps"] == []
            segredo = await c.post(f"/api/personas/{outra['id']}/enrich", json={"instructions": "a senha é Xy12345!"})
            assert segredo.status_code == 422 and segredo.json()["detail"]["code"] == "instructions_with_secret"
            assert (await c.post(f"/api/personas/{outra['id']}/enrich", json={"extra": 1})).status_code == 422


async def test_json_quebrado_repete_uma_vez_e_outros_erros_nao_repetem(tmp_path: Path) -> None:
    """Sem gramática (K-042), o modelo às vezes devolve JSON quebrado: uma repetição resolve; duas falhas viram 503; e
    orçamento/recusa nunca se repetem (repetir não resolve e custa outra chamada)."""
    svc, _repo, _secrets, db = build(tmp_path)
    try:
        class Instavel:
            name, model, simulated = "instavel", "m", True

            def __init__(self, falhas: list[AIError]) -> None:
                self.falhas = falhas
                self.chamadas = 0

            async def generate_persona(self, req: Any) -> Any:
                self.chamadas += 1
                if self.falhas:
                    raise self.falhas.pop(0)
                return await SimulatedProvider().generate_persona(req)

        uma = Instavel([AIError("Rascunho de persona não é JSON: Expecting ',' delimiter", kind="invalid_output")])
        svc.provider = uma
        rascunho = await svc.generate_persona_draft(PersonaGenerateBody(prompt="uma pessoa de Curitiba"))
        assert rascunho.name and uma.chamadas == 2

        duas = Instavel([AIError("quebrado", kind="invalid_output"), AIError("quebrado de novo", kind="invalid_output")])
        svc.provider = duas
        with pytest.raises(SocialError) as erro:
            await svc.generate_persona_draft(PersonaGenerateBody(prompt="uma pessoa de Curitiba"))
        assert erro.value.code == "ai_error" and duas.chamadas == 2

        orcamento = Instavel([AIError("teto do dia", kind="budget")])
        svc.provider = orcamento
        with pytest.raises(SocialError) as erro:
            await svc.generate_persona_draft(PersonaGenerateBody(prompt="uma pessoa de Curitiba"))
        assert erro.value.code == "ai_budget" and orcamento.chamadas == 1
    finally:
        db.close()
