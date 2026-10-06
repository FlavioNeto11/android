"""A persona É a pessoa (evolução 2, onda A): a linha de `instagram_profiles` desde a migração 047.

O que se prova aqui, tudo `simulated`:
- `POST /personas` cria uma PESSOA sem conta (`username` guardado como `''`, exposto como `null`), com nome partido,
  proveniência `manual` e o visual separado da voz — inclusive quando um cliente antigo manda as chaves visuais
  dentro de `traits`;
- `PATCH /personas/{id}` mescla por seção: mudar `home.city` não apaga `work.profession`, `null` explícito apaga,
  lista substitui, e nulo em `name` é "não mexer";
- `/api/personas` lista todas as pessoas; `/api/instagram/profiles` só quem tem conta de cadastro; o mesmo id
  responde nas duas rotas com o MESMO objeto, e o id legado `persona-…` continua resolvendo;
- cadastrar uma conta com `persona_id` de uma pessoa sem conta ADOTA essa pessoa (mesmo id); `PATCH persona_id`
  numa pessoa existente ABSORVE a persona sem conta; o id de outra pessoa com conta é recusado nos dois casos;
- apagar a persona é apagar a pessoa: recusado com vínculo ativo ou execução em curso, e leva a credencial do cofre;
- o bloco `<persona>` traz nome, idade calculada, as linhas de `PERSONA_BIO_FIELDS` e as crenças com a linha de
  conduta (ADR-048), tudo por `sem_marcacao`, e o "perfil: @…" é o handle da conta do app da etapa, senão o usuário
  de cadastro, senão o nome;
- `SocialContextDTO.persona` não carrega credencial nem sessão;
- a porta de sessão responde "sem conta" para a pessoa vinculada a um aparelho que não tem conta naquele app.
"""
from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.main import create_app
from app.models import (PersonaBiography, PersonaCreate, PersonaPatch, PersonaTraits, PersonaVisual, ProfileCreate,
                        ProfilePatch)
from app.modules.identity.domain.persona import (BIOGRAPHY_SCHEMA_VERSION, idade_em, lacunas_da_biografia,
                                                 mesclar_secao, nome_ficticio_plausivel, separar_nome)
from app.social.service import SocialError

from .conftest import Harness, make_config
from .test_social_profiles import build, novo

SENHA = "s3nh4-de-teste-#9!"
IG = "com.instagram.android"
VOZ = dict(personality="calma", tone="acolhedor", formality="informal", typical_length="curta", emojis="raro",
           slang="pouca", humor="leve", interests=["fotografia"], dm_style="curto", comment_style="concreto",
           with_known="solta", with_strangers="educada", examples=["que luz boa!"], common_phrases=["boa!"],
           forbidden_phrases=["arrasou"])


def _com_instagram(db: Any) -> None:
    """`create_profile` só cria a conta em `profile_accounts` quando o app está registrado."""
    db.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('instagram','Instagram',?,0)", (IG,))


# ---------------------------------------------------------------- domínio puro
def test_regras_puras_da_pessoa() -> None:
    assert idade_em("1995-03-10", date(2026, 3, 9)) == 30 and idade_em("1995-03-10", date(2026, 3, 10)) == 31
    assert idade_em("10/03/1995", date(2026, 1, 1)) is None and idade_em("2099-01-01", date(2026, 1, 1)) is None
    assert separar_nome("Renata Vieira Lima") == ("Renata", "Vieira Lima") and separar_nome("Otávio") == ("Otávio", None)
    assert nome_ficticio_plausivel("Ana Souza") and nome_ficticio_plausivel("Jean-Luc D'Ávila")
    assert not nome_ficticio_plausivel("@lucas_99") and not nome_ficticio_plausivel("Ana") and not nome_ficticio_plausivel("Ana 2")
    base = {"home": {"city": "SP", "state": "SP"}, "tastes": {"hobbies": ["trilha"]}, "schema_version": 1}
    mesclado = mesclar_secao(base, {"home": {"city": "Rio", "state": None}, "tastes": {"hobbies": ["surfe", "yoga"]}})
    assert mesclado == {"home": {"city": "Rio"}, "tastes": {"hobbies": ["surfe", "yoga"]}, "schema_version": 1}
    assert lacunas_da_biografia({"home": {"city": "SP"}}) == ["origin.birthplace", "work.profession",
                                                              "work.education", "tastes.hobbies"]


# ---------------------------------------------------------------- criação e leitura
def test_cria_uma_pessoa_sem_conta_com_visual_separado_da_voz(tmp_path: Path) -> None:
    svc, repo, _secrets, db = build(tmp_path)
    try:
        dto = svc.create_persona(PersonaCreate(
            name="Marina Lopes", summary="Fotógrafa de 31 anos", birth_date="1995-03-10", gender="feminino",
            traits={**VOZ, "appearance": "cabelo cacheado", "photo_scenario": "estúdio"},
            biography=PersonaBiography.model_validate({"home": {"city": "Curitiba"}, "work": {"profession": "fotógrafa"}})))
        assert dto.username is None and dto.persona_id == dto.id and dto.profile_id == dto.id
        assert (dto.first_name, dto.last_name, dto.display_name, dto.name) == ("Marina", "Lopes", "Marina Lopes", "Marina Lopes")
        assert dto.age == idade_em("1995-03-10", date.today()) and dto.age is not None and dto.age >= 30
        assert dto.voice_gaps == [] and dto.traits.model_dump(exclude_none=True)["interests"] == ["fotografia"]
        assert dto.visual == PersonaVisual(appearance="cabelo cacheado", photo_scenario="estúdio")
        assert dto.biography.home.city == "Curitiba" and dto.biography.schema_version == BIOGRAPHY_SCHEMA_VERSION == 2
        assert dto.generation.source == "manual" and dto.generation.at
        assert dto.accounts_count == 0 and dto.images == [] and dto.credential.configured is False
        # No banco: `''` é "sem conta"; a voz gravada não tem chave visual; o visual tem as duas que vieram.
        linha = repo.profile_row(dto.id)
        assert linha is not None and linha["username"] == "" and "appearance" not in json.loads(linha["traits"])
        assert json.loads(linha["visual"]) == {"appearance": "cabelo cacheado", "photo_scenario": "estúdio"}
        # Aparece como pessoa, não como perfil com conta.
        assert [p.id for p in svc.list_personas()] == [dto.id] and svc.list_profiles() == []
        assert svc.get_persona(dto.id).model_dump() == svc.get_profile(dto.id).model_dump()
    finally:
        db.close()


def test_id_legado_da_tabela_personas_continua_resolvendo(tmp_path: Path) -> None:
    svc, repo, _secrets, db = build(tmp_path)
    try:
        dto = svc.create_persona(PersonaCreate(name="Quillon Teixeira"))
        db.execute("INSERT INTO personas(id, name, created_at, updated_at) VALUES ('persona-b1','Quillon',?,?)",
                   (dto.created_at, dto.updated_at))
        repo.update_profile(dto.id, {"persona_id": "persona-b1"})
        assert svc.get_persona("persona-b1").id == dto.id
        assert svc.get_persona(dto.id).persona_id == dto.id                 # o DTO expõe a própria pessoa
        with pytest.raises(SocialError) as exc:
            svc.get_persona("persona-que-nao-existe")
        assert exc.value.status == 404
    finally:
        db.close()


def test_linha_com_traits_fora_do_contrato_nao_derruba_a_listagem(tmp_path: Path) -> None:
    svc, repo, _secrets, db = build(tmp_path)
    try:
        dto = svc.create_persona(PersonaCreate(name="Caio Prado", traits=PersonaTraits(tone="seco")))
        repo.update_profile(dto.id, {"traits": json.dumps({"tone": "seco", "formality": "medieval", "appearance": "x"})})
        lida = svc.get_persona(dto.id)
        assert lida.traits.tone is None and lida.visual.appearance == "x"     # voz inválida sai vazia; visual legado é lido
        assert len(svc.list_personas()) == 1
    finally:
        db.close()


# ---------------------------------------------------------------- PATCH por seção
def test_patch_mescla_por_secao_e_nulo_apaga(tmp_path: Path) -> None:
    svc, _repo, _secrets, db = build(tmp_path)
    try:
        pid = svc.create_persona(PersonaCreate(name="Lia Nunes", traits=PersonaTraits(tone="direto", interests=["a", "b"]),
                                               biography=PersonaBiography.model_validate(
                                                   {"home": {"city": "SP", "state": "SP"}, "work": {"profession": "dev"}}))).id
        dto = svc.update_persona(pid, PersonaPatch(biography=PersonaBiography.model_validate({"home": {"city": "Rio"}})))
        assert (dto.biography.home.city, dto.biography.home.state, dto.biography.work.profession) == ("Rio", "SP", "dev")
        dto = svc.update_persona(pid, PersonaPatch.model_validate({"biography": {"home": {"state": None}},
                                                                    "traits": {"interests": ["c"], "humor": "seco"}}))
        assert dto.biography.home.state is None and dto.biography.home.city == "Rio"
        assert dto.traits.tone == "direto" and dto.traits.interests == ["c"] and dto.traits.humor == "seco"
        # Cliente antigo: as chaves visuais dentro de `traits` vão para `visual`, e o resto da voz fica.
        dto = svc.update_persona(pid, PersonaPatch.model_validate({"traits": {"appearance": "alta", "tone": "calmo"}}))
        assert dto.visual.appearance == "alta" and dto.traits.tone == "calmo" and dto.traits.humor == "seco"
        dto = svc.update_persona(pid, PersonaPatch.model_validate({"name": None, "summary": "novo", "visual": {"palette": "terrosa"}}))
        assert dto.name == "Lia Nunes" and dto.summary == "novo" and dto.visual == PersonaVisual(appearance="alta", palette="terrosa")
        dto = svc.update_persona(pid, PersonaPatch(name="Lia Nunes Prado", birth_date="1990-01-01", locale="pt-BR"))
        assert dto.display_name == "Lia Nunes Prado" and dto.first_name == "Lia" and dto.locale == "pt-BR"
        assert dto.age == idade_em("1990-01-01", date.today())
        with pytest.raises(Exception):
            PersonaPatch.model_validate({"biography": {"beliefs": {"religiao": "x"}}})    # chave desconhecida: 422
    finally:
        db.close()


# ---------------------------------------------------------------- conta ↔ pessoa
def test_cadastro_com_persona_id_adota_a_pessoa_sem_conta(tmp_path: Path) -> None:
    svc, repo, _secrets, db = build(tmp_path)
    try:
        _com_instagram(db)
        pessoa = svc.create_persona(PersonaCreate(name="Tadeu Quintela", summary="corredor", traits=PersonaTraits(tone="animado")))
        conta = svc.create_profile(novo("tadeu.quintela4821", SENHA, "android-01", persona_id=pessoa.id,
                                        email="tadeu@exemplo.com"))
        assert conta.id == pessoa.id and conta.username == "tadeu.quintela4821" and conta.summary == "corredor"
        assert conta.first_name == "Tadeu" and conta.email == "tadeu@exemplo.com"   # nome já existia; e-mail entrou
        assert conta.credential.configured and conta.instance_id == "android-01" and conta.accounts_count == 1
        assert [p.id for p in svc.list_profiles()] == [pessoa.id] and len(svc.list_personas()) == 1
        # A mesma pessoa não ganha segunda conta de cadastro; outra pessoa com conta também não é adotável.
        with pytest.raises(SocialError) as exc:
            svc.create_profile(novo("outra.conta", None, persona_id=pessoa.id))
        assert exc.value.code == "persona_in_use"
        with pytest.raises(SocialError) as exc:
            svc.create_profile(novo("outra.conta", None, persona_id="ig-nao-existe"))
        assert exc.value.code == "unknown_persona"
        assert repo.account_for_package(pessoa.id, IG) is not None
    finally:
        db.close()


def test_patch_persona_id_absorve_a_persona_sem_conta_e_recusa_outra_pessoa(tmp_path: Path) -> None:
    svc, repo, _secrets, db = build(tmp_path)
    try:
        tadeu = svc.create_profile(novo("tadeu.quintela4821", None, first_name="Tadeu", last_name="Quintela"))
        luciana = svc.create_profile(novo("luciana.bastos73519", None, first_name="Luciana", last_name="Bastos"))
        voz = svc.create_persona(PersonaCreate(name="Voz do Tadeu", summary="corredor", persona_prompt="Curto.",
                                               traits={**VOZ, "appearance": "alto"}, gender="masculino"))
        dto = svc.update_profile(tadeu.id, ProfilePatch(persona_id=voz.id))
        assert dto.id == tadeu.id and dto.summary == "corredor" and dto.persona_prompt == "Curto."
        assert dto.voice_gaps == [] and dto.visual.appearance == "alto" and dto.gender == "masculino"
        assert dto.name == "Tadeu Quintela"                                  # o nome do perfil manda
        assert repo.profile_row(voz.id) is None and len(svc.list_personas()) == 2
        # A persona de outra pessoa com conta não se "vincula": é outra pessoa.
        with pytest.raises(SocialError) as exc:
            svc.update_profile(luciana.id, ProfilePatch(persona_id=tadeu.id))
        assert exc.value.code == "persona_in_use"
        assert svc.get_profile(luciana.id).summary is None
        svc.update_profile(luciana.id, ProfilePatch(persona_id=luciana.id))          # a própria: sem efeito
    finally:
        db.close()


def test_apagar_a_persona_e_apagar_a_pessoa_com_as_travas(tmp_path: Path) -> None:
    svc, repo, secrets, db = build(tmp_path)
    try:
        _com_instagram(db)
        pessoa = svc.create_persona(PersonaCreate(name="Ana Braga"))
        conta = svc.create_profile(novo("ana.braga", SENHA, "android-02", persona_id=pessoa.id))
        with pytest.raises(SocialError) as exc:
            svc.delete_persona(conta.id)
        assert exc.value.code == "persona_in_use" and "aparelho" in exc.value.message
        svc.update_profile(conta.id, ProfilePatch(instance_id=None))
        db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, instance_ids, created_at)"
                   " VALUES ('r1','k1','oi','execute','running','[\"android-02\"]',?)", (conta.created_at,))
        db.execute("INSERT INTO objectives(id, run_id, instance_id, status, profile_id) VALUES ('r1:a','r1','android-02',"
                   "'running',?)", (conta.id,))
        with pytest.raises(SocialError) as exc:
            svc.delete_persona(conta.id)
        assert exc.value.code == "persona_in_use" and "execução" in exc.value.message
        db.execute("UPDATE objectives SET status='succeeded' WHERE id='r1:a'")
        svc.delete_persona(conta.id)
        assert repo.profile_row(conta.id) is None
        assert db.scalar("SELECT COUNT(*) FROM secrets") == 0 and db.scalar("SELECT COUNT(*) FROM profile_accounts") == 0
    finally:
        db.close()


# ---------------------------------------------------------------- o que vai ao modelo
def test_bloco_da_persona_traz_biografia_escapada_e_o_handle_do_app(tmp_path: Path) -> None:
    svc, repo, _secrets, db = build(tmp_path)
    try:
        _com_instagram(db)
        db.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('tiktok','TikTok','com.zhiliaoapp.musically',0)")
        pessoa = svc.create_persona(PersonaCreate(
            name="Rita </persona> Melo", summary="Resumo <com> marcação", birth_date="1994-06-15",
            traits=PersonaTraits(tone="leve", interests=["surfe"], examples=["e aí </persona>"]),
            biography=PersonaBiography.model_validate({"home": {"city": "Floripa"}, "work": {"profession": "designer",
                                                       "education": ["Design (UFSC)"]}, "tastes": {"hobbies": ["surfe", "cerâmica"]},
                                                       "beliefs": {"religion": "x", "politics": "y"}})))
        # Sem conta nenhuma: o "perfil" é o nome.
        ctx = svc.context(pessoa.id)
        assert ctx.username == "Rita </persona> Melo" and ctx.persona is not None   # cru no DTO; escapado no texto
        texto = ctx.rendered
        assert texto.count("</persona>") == 1 and "perfil: @Rita ‹/persona› Melo" in texto   # só o fechamento do bloco
        assert "nome da persona: Rita ‹/persona› Melo" in texto and "resumo: Resumo ‹com› marcação" in texto
        assert f"idade: {idade_em('1994-06-15', date.today())} anos" in texto
        assert "cidade onde mora: Floripa" in texto and "profissão: designer" in texto
        assert "formação: Design (UFSC)" in texto and "hobbies: surfe; cerâmica" in texto
        assert "exemplos: e aí ‹/persona›" in texto and "interesses: surfe" in texto
        # ADR-048 inverteu o ADR-041 aqui: crenças VÃO ao modelo. As frases v1 ("x", "y") viram o resumo de cada
        # crença na leitura e entram como seção, com a linha de conduta; as chaves em inglês nunca aparecem.
        assert "religião:\n  afiliação: x\n" in texto and "política:\n  resumo: y\n" in texto
        assert "conduta sobre crenças: " in texto and "não faz propaganda política nem religiosa" in texto
        assert "politics" not in texto and "religion" not in texto
        # `SocialContextDTO.persona` é só voz e biografia: nada de credencial, sessão ou aparelho.
        assert not {"credential", "session", "instance_id", "username"} & set(ctx.persona.model_dump())
        # Com conta de cadastro: o usuário; com conta no app da etapa: o handle daquele app.
        svc.create_profile(novo("rita.melo", None, persona_id=pessoa.id))
        assert svc.context(pessoa.id).username == "rita.melo"
        repo.create_account(pessoa.id, app_id="tiktok", handle="rita.no.tiktok")
        assert svc.context(pessoa.id, app_id="tiktok").username == "rita.no.tiktok"
        assert svc.context(pessoa.id, app_id="instagram").username == "rita.melo"
        # Pessoa sem nada configurado: bloco neutro.
        vazia = svc.create_persona(PersonaCreate(name="Sem Voz"))
        assert svc.context(vazia.id).persona is None and "sem persona configurada" in svc.context(vazia.id).rendered
    finally:
        db.close()


async def test_porta_de_sessao_responde_sem_conta_para_a_pessoa_sem_conta_no_app(harness: Harness) -> None:
    state = harness.state
    assert state is not None
    pessoa = state.social.create_persona(PersonaCreate(name="Sem Conta Ainda"))
    state.social.update_profile(pessoa.id, ProfilePatch(instance_id="android-01"))
    rt = state.devices.get("android-01")
    recusa = state._session_gate(rt, IG)
    assert recusa is not None and recusa[1] is None and "não tem conta em Instagram" in recusa[0]
    # Perfil antigo, sem linha em `profile_accounts` mas com usuário de cadastro: continua contando como conta.
    state.social.update_profile(pessoa.id, ProfilePatch(instance_id=None))
    conta = state.social.create_profile(ProfileCreate(username="tadeu.quintela4821", instance_id="android-01"))
    state.db.execute("DELETE FROM profile_accounts WHERE profile_id=?", (conta.id,))
    recusa = state._session_gate(rt, IG)
    assert recusa is None or "não tem conta" not in recusa[0]


# ---------------------------------------------------------------- HTTP
async def test_rotas_canonicas_e_apelidos_da_persona(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    app = create_app(cfg)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        async with app.router.lifespan_context(app):
            criada = await c.post("/api/personas", json={"name": "Nina Reis", "traits": {"tone": "leve", "appearance": "baixa"},
                                                         "biography": {"home": {"city": "Recife"}}})
            assert criada.status_code == 201
            pessoa = criada.json()
            assert pessoa["username"] is None and pessoa["visual"]["appearance"] == "baixa" and "appearance" not in pessoa["traits"]
            assert pessoa["persona_id"] == pessoa["id"] and pessoa["profile_id"] == pessoa["id"]
            # Lista de pessoas × lista de perfis (contas).
            assert [p["id"] for p in (await c.get("/api/personas")).json()] == [pessoa["id"]]
            assert (await c.get("/api/instagram/profiles")).json() == []
            # O mesmo objeto pelas duas rotas. A criação dispara a imagem gerada em segundo plano: compara depois que
            # ela assenta, senão as duas leituras pegam o antes e o depois (pending → ready; suíte 7, 03/10).
            for _ in range(100):
                imagens = (await c.get(f"/api/personas/{pessoa['id']}")).json().get("images") or []
                if all(i.get("status") != "pending" for i in imagens):
                    break
                await asyncio.sleep(0.05)
            pela_persona = (await c.get(f"/api/personas/{pessoa['id']}")).json()
            pelo_perfil = (await c.get(f"/api/instagram/profiles/{pessoa['id']}")).json()
            assert pela_persona == pelo_perfil
            # PATCH por seção (a mescla é do servidor).
            patch = await c.patch(f"/api/personas/{pessoa['id']}", json={"biography": {"work": {"profession": "atriz"}}})
            assert patch.status_code == 200
            assert patch.json()["biography"]["home"]["city"] == "Recife" and patch.json()["biography"]["work"]["profession"] == "atriz"
            recusa = await c.patch(f"/api/personas/{pessoa['id']}", json={"traits": {"cor_dos_olhos": "verde"}})
            assert recusa.status_code == 422
            # A conta de cadastro adota a pessoa; `persona_name` continua saindo (contexto.py e o painel o leem).
            conta = await c.post("/api/instagram/profiles", json={"username": "nina.reis", "persona_id": pessoa["id"]})
            assert conta.status_code == 201 and conta.json()["id"] == pessoa["id"]
            assert conta.json()["persona_name"] == "Nina Reis" and conta.json()["accounts_count"] == 1
            assert [p["id"] for p in (await c.get("/api/instagram/profiles")).json()] == [pessoa["id"]]
            # Apagar pela rota antiga é apagar a pessoa também.
            assert (await c.delete(f"/api/personas/{pessoa['id']}")).status_code == 204
            assert (await c.get(f"/api/instagram/profiles/{pessoa['id']}")).status_code == 404


def test_bloco_persona_leva_a_biografia_inteira_com_orcamento_e_a_regra_de_que_o_pedido_manda() -> None:
    """Decisão do dono de 28/09: tudo o que a pessoa é influencia a fala — origem, moradia, trabalho, vida, gostos e
    o que não gosta — mas o pedido manda no que fazer. A biografia entra na ordem de prioridade até o orçamento, cada
    lista com no máximo `_ITENS_POR_LISTA` itens, e `filhos: 0` é "não tem", não vazio."""
    from app.social.context import _ITENS_POR_LISTA, linhas_da_biografia

    bio = PersonaBiography.model_validate({
        "origin": {"birthplace": "Caruaru (PE)", "hometown": "Recife", "nationality": "brasileira"},
        "home": {"city": "Recife", "state": "PE", "country": "Brasil", "residence": "apartamento com a irmã"},
        "work": {"profession": "barista", "employer": "café do bairro", "education": ["Gastronomia"]},
        "life": {"marital_status": "solteira", "children": 0, "history": ["mudou para Recife aos 18"]},
        "tastes": {"hobbies": [f"hobby {n}" for n in range(12)], "preferences": ["café coado"],
                   "dislikes": ["fila", "calor sem ventilador"]},
    }).model_dump(exclude_none=True)
    texto = "\n".join(linhas_da_biografia(bio))
    for esperado in ("cidade onde mora: Recife", "profissão: barista", "formação: Gastronomia", "nasceu em: Caruaru (PE)",
                     "cresceu em: Recife", "mora: apartamento com a irmã", "estado civil: solteira",
                     "filhos: não tem", "onde trabalha: café do bairro", "gosta de: café coado",
                     "não gosta de: fila; calor sem ventilador", "fatos marcantes: mudou para Recife aos 18",
                     "estado onde mora: PE", "país onde mora: Brasil", "nacionalidade: brasileira"):
        assert esperado in texto, esperado
    hobbies = next(l for l in texto.split("\n") if l.startswith("hobbies: "))
    assert hobbies.count(";") == _ITENS_POR_LISTA - 1                   # só os primeiros itens entram
    # Ordem de prioridade: o essencial primeiro, o acessório no fim.
    assert texto.index("cidade onde mora") < texto.index("nasceu em") < texto.index("nacionalidade")

    # Orçamento apertado: entra o que cabe, na ordem, sem cortar linha no meio.
    curto = linhas_da_biografia(bio, orcamento=12)
    assert curto and curto[0].startswith("cidade onde mora: ") and len(curto) < 5
    assert all(": " in l and not l.endswith(":") for l in curto)


async def test_bloco_persona_traz_como_usar_so_quando_ha_persona(tmp_path: Path) -> None:
    svc, _repo, _secrets, db = build(tmp_path)
    try:
        pessoa = svc.create_persona(PersonaCreate(
            name="Nina Brito", traits=PersonaTraits(tone="leve"),
            biography=PersonaBiography.model_validate({"tastes": {"dislikes": ["fila"]}})))
        texto = svc.context(pessoa.id).rendered
        assert texto.count("como usar esta persona: ") == 1 and "manda no QUE fazer" in texto
        assert "não gosta de: fila" in texto
        assert texto.index("como usar esta persona") < texto.index("não gosta de") < texto.index("tom: leve")
        vazia = svc.create_persona(PersonaCreate(name="Sem Voz"))
        assert "como usar esta persona" not in svc.context(vazia.id).rendered
    finally:
        db.close()
