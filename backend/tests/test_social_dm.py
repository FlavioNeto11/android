"""Fase 8 — qualidade da operação do Instagram: a VOZ da persona e a MEMÓRIA do caminho de mensagem direta.

O que estes testes protegem, em ordem de importância:

1. **A voz é da persona, não do briefing.** O planejador é instruído a não pôr tom no `content_brief`, e o papel
   social diz, com todas as letras, quem ganha em caso de conflito. Duas contas com a mesma intenção têm de
   poder soar diferentes — era o defeito medido no achado #107.
2. **Mensagem simples não vira crônica da tela.** A instrução "cite o que se vê" é de COMENTÁRIO. Num
   cumprimento por DM ela transformava "boa tarde" em três linhas sobre a página do destinatário.
3. **O perfil OUVE.** Ler uma conversa grava o que a contraparte disse (com dedupe, e sem confundir o que esta
   conta escreveu com fala dela), e mandar mensagem num fio em que ela falou por último vira RESPONDER — com
   `incoming`, que é a única origem de memória.
4. **O resumo da conversa diz o que foi dito**, não quantas mensagens houve.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.automation.hierarchy import UiElement, UiTree
from app.db import Database
from app.events import EventBus
from app.models import (InteractionStatus, InteractionType, PersonaCreate, PersonaPreviewBody, PersonaTraits,
                        ProfileCreate, voice_gaps)
from app.planning.prompts import PLANNER_CAPABILITY_SYSTEM, SOCIAL_SYSTEM, social_user_text
from app.planning.provider import SocialRequest
from app.planning.simulated_provider import SimulatedProvider
from app.security.secret_store import MemoryKeyProvider, SecretStore
from app.social.repository import SocialRepository
from app.social.service import SocialService, thread_de_dm

from .conftest import make_config
from .pacote_instagram import mensagem_de

SENHA = "$a=B7ee1#<b-C?S-{"
PERSONA = PersonaCreate(
    name="Lucas — corredor", summary="Fala de corrida e trilha.", persona_prompt="Responda curto e animado.",
    traits={"tone": "animado", "formality": "informal", "typical_length": "curta", "interests": ["corrida"]})


def build(tmp_path: Path) -> tuple[SocialService, SocialRepository]:
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    db = Database(cfg.db_dsn)
    db.migrate()
    repo = SocialRepository(db)
    svc = SocialService(repo, SecretStore(db, MemoryKeyProvider()), EventBus(db),
                        known_instances=lambda: ["android-01"], provider=SimulatedProvider())
    return svc, repo


def perfil(svc: SocialService) -> str:
    return svc.create_profile(ProfileCreate(username="lucas.almeida9484", password=SENHA, instance_id="android-01",
                                            persona_id=svc.create_persona(PERSONA).id)).id


def elemento(text: str = "", desc: str = "") -> UiElement:
    return UiElement(id="e", text=text, desc=desc, resource_id="", class_name="", package="com.instagram.android",
                     bounds=(0, 0, 10, 10), clickable=False, enabled=True, focused=False, scrollable=False,
                     editable=False, checked=False, password=False)


def tela(*elementos: UiElement, sensitive: bool = False) -> UiTree:
    return UiTree(elements=list(elementos), packages=["com.instagram.android"], sensitive=sensitive)


def pedido(**over: object) -> SocialRequest:
    base: dict[str, object] = {"profile_id": "p", "username": "lucas.almeida9484", "kind": "dm_initiate",
                               "context_text": "<persona>\nperfil: @lucas\n</persona>"}
    return SocialRequest(**{**base, **over})   # type: ignore[arg-type]


# ---------------------------------------------------------------- 8.1 · o briefing não manda na voz
def test_planejador_nao_pede_mais_tom_no_briefing() -> None:
    """O mesmo plano roda em oito aparelhos: tom escrito ali chega igual aos oito e concorre com a persona."""
    assert "o que dizer, o tom e o que não dizer" not in PLANNER_CAPABILITY_SYSTEM
    assert "NÃO descreva tom, humor, tamanho, formalidade nem uso de emoji" in PLANNER_CAPABILITY_SYSTEM
    assert "Só inclua tom quando o próprio" in PLANNER_CAPABILITY_SYSTEM      # a linha quebra depois daqui


def test_em_conflito_de_tom_quem_manda_e_a_persona() -> None:
    """Havia a regra de escrever na voz da persona e nenhuma regra de DESEMPATE quando a intenção discordasse."""
    assert "EM CONFLITO, QUEM MANDA NA VOZ É A PERSONA" in SOCIAL_SYSTEM
    assert "siga a PERSONA e ignore essa" in SOCIAL_SYSTEM                    # a linha quebra depois daqui


@pytest.mark.parametrize("kind", ["dm_initiate", "dm_reply"])
def test_mensagem_simples_nao_manda_comentar_a_tela(kind: str) -> None:
    texto = social_user_text(pedido(kind=kind, brief="dizer boa tarde", incoming="oi" if kind == "dm_reply" else "",
                                    screen="Fotos da viagem a Portugal\n12 curtidas"))
    assert "cite o que se vê" not in texto
    assert "NÃO descreva nem comente o que está na tela" in texto or "NÃO descreve nem comenta" in texto
    assert "<tela" in texto                      # a tela continua no prompt: mudou o que se manda fazer com ela


@pytest.mark.parametrize("kind", ["post_comment", "comment_reply"])
def test_comentario_continua_falando_do_que_esta_na_tela(kind: str) -> None:
    texto = social_user_text(pedido(kind=kind, brief="elogiar a foto", screen="Fotos da viagem a Portugal"))
    assert "cite o que se vê" in texto


# ---------------------------------------------------------------- 8.1 · campos de voz que faltam
def test_persona_so_com_o_basico_declara_os_campos_de_voz_que_faltam(tmp_path: Path) -> None:
    svc, _ = build(tmp_path)
    dto = svc.get_persona(svc.list_personas()[0].id) if svc.list_personas() else None
    assert dto is None
    persona = svc.create_persona(PERSONA)
    # Os oito do achado #107: com só os traços básicos, é o que o modelo NÃO tem para diferenciar oito vozes.
    assert persona.voice_gaps == ["personality", "emojis", "slang", "humor", "dm_style", "comment_style",
                                  "with_known", "with_strangers", "common_phrases", "forbidden_phrases",
                                  "examples"]


def test_persona_completa_nao_tem_campo_de_voz_faltando() -> None:
    cheia = PersonaTraits(
        personality="calma", tone="acolhedor", formality="informal", typical_length="curta", emojis="raro",
        slang="usa “top”", humor="leve", interests=["corrida"], dm_style="abre com um oi curto",
        comment_style="comenta o detalhe da foto", with_known="brinca", with_strangers="formal no primeiro contato",
        common_phrases=["bora"], forbidden_phrases=["querido"], examples=["bora correr amanhã?"])
    assert voice_gaps(cheia) == []


async def test_previa_da_persona_aceita_intencao_e_nao_grava_nada(tmp_path: Path) -> None:
    """A prova do item é a MESMA intenção nos oito perfis — e não havia como pedir intenção à prévia."""
    svc, repo = build(tmp_path)
    pid = perfil(svc)
    persona_id = svc.get_profile(pid).persona_id
    assert persona_id
    draft = await svc.preview_persona(persona_id, PersonaPreviewBody(
        kind="dm_initiate", profile_id=pid, brief="dizer boa tarde", counterparty="@ana"))
    assert (draft.content or "").strip()
    assert repo.list_interactions(pid, limit=10) == []
    assert svc.list_memories(pid) == []


def test_previa_sem_recebido_e_sem_intencao_e_recusada() -> None:
    with pytest.raises(ValueError):
        PersonaPreviewBody(kind="dm_initiate", profile_id="p")


# ---------------------------------------------------------------- 8.2 · a fala da contraparte
@pytest.mark.parametrize("texto,desc", [
    ("ana.silva said cheguei em Lisboa ontem", ""),
    ("ana.silva disse cheguei em Lisboa ontem", ""),
    ("", "Message from ana.silva: cheguei em Lisboa ontem"),
    ("", "Mensagem de ana.silva - cheguei em Lisboa ontem"),
])
def test_extrai_a_fala_da_contraparte_da_conversa_aberta(texto: str, desc: str) -> None:
    assert mensagem_de(tela(elemento(texto, desc)), "@ana.silva") == "cheguei em Lisboa ontem"


def test_a_fala_extraida_e_a_ULTIMA_da_conversa() -> None:
    arvore = tela(elemento("ana.silva said oi, tudo bem?"), elemento("ana.silva said e aí, vamos correr?"))
    assert mensagem_de(arvore, "@ana.silva") == "e aí, vamos correr?"


@pytest.mark.parametrize("arvore,alvo", [
    (tela(elemento("bora correr amanhã")), "@ana.silva"),                       # bolha sem autor: pode ser nossa
    (tela(elemento("outra.pessoa said oi")), "@ana.silva"),                      # fala de terceiro na lista
    (tela(elemento("ana.silva said oi"), sensitive=True), "@ana.silva"),         # tela sensível não devolve nada
    (tela(elemento("ana.silva said oi")), "@"),                                  # alvo vazio casaria com qualquer um
])
def test_sem_certeza_de_autor_nao_inventa_fala(arvore: UiTree, alvo: str) -> None:
    assert mensagem_de(arvore, alvo) == ""


# ---------------------------------------------------------------- 8.2 · o que foi lido vira histórico
def test_ler_a_conversa_grava_o_que_a_contraparte_disse(tmp_path: Path) -> None:
    svc, repo = build(tmp_path)
    pid = perfil(svc)
    gravadas = svc.record_inbound(pid, texts=["oi, tudo bem?", "cheguei em Lisboa ontem"], counterparty="ana.silva")
    assert [g.type for g in gravadas] == [InteractionType.dm_received, InteractionType.dm_received]
    assert all(g.direction == "inbound" and g.status == InteractionStatus.confirmed for g in gravadas)
    assert all(g.thread_key == "dm:@ana.silva" and g.counterparty == "@ana.silva" for g in gravadas)
    assert repo.thread_row(pid, "dm:@ana.silva") is not None


def test_reler_a_mesma_conversa_nao_duplica_o_historico(tmp_path: Path) -> None:
    """A conversa é relida a cada execução: sem dedupe, o perfil acharia que a pessoa repetiu a mesma frase."""
    svc, _ = build(tmp_path)
    pid = perfil(svc)
    svc.record_inbound(pid, texts=["oi, tudo bem?"], counterparty="@ana.silva")
    de_novo = svc.record_inbound(pid, texts=["oi, tudo bem?", "vamos correr no sábado?"], counterparty="@ana.silva")
    assert [g.incoming_content for g in de_novo] == ["vamos correr no sábado?"]
    assert len(svc.list_interactions(pid, limit=20)) == 2


def test_o_que_este_perfil_escreveu_nao_entra_como_fala_da_outra_pessoa(tmp_path: Path) -> None:
    """A conversa aberta mostra os DOIS lados. Sem este filtro, o próprio texto enviado voltaria como fala dela."""
    svc, _ = build(tmp_path)
    pid = perfil(svc)
    svc.record_interaction(pid, type=InteractionType.dm_sent.value, direction="outbound",
                           status=InteractionStatus.confirmed.value, counterparty="@ana.silva",
                           thread_key="dm:@ana.silva", outgoing_content="bora correr amanhã?")
    gravadas = svc.record_inbound(pid, texts=["Bora correr amanhã?", "combinado!"], counterparty="@ana.silva")
    assert [g.incoming_content for g in gravadas] == ["combinado!"]


def test_sem_alvo_nao_se_grava_fala_de_ninguem(tmp_path: Path) -> None:
    svc, _ = build(tmp_path)
    pid = perfil(svc)
    assert svc.record_inbound(pid, texts=["oi"], counterparty=None) == []
    assert svc.list_interactions(pid, limit=5) == []


def test_fala_de_entrada_nao_consome_a_cota_de_mensagens_do_perfil(tmp_path: Path) -> None:
    """Limite é sobre o que esta conta FAZ. Quem escreveu a mensagem recebida foi a outra pessoa."""
    svc, repo = build(tmp_path)
    pid = perfil(svc)
    svc.record_inbound(pid, texts=[f"mensagem {i}" for i in range(20)], counterparty="@ana.silva")
    from app.planning.capabilities import capability_of
    cap = capability_of("com.instagram.android", "SEND_MESSAGE")
    assert cap is not None
    veredito = svc.policies.check(pid, cap)
    assert veredito.allowed, veredito.reason
    assert veredito.counts.get("dms", 0) == 0
    assert repo.count_interactions(pid) == 20


# ---------------------------------------------------------------- 8.2 · responder x puxar conversa
def test_quando_ela_falou_por_ultimo_ha_o_que_responder(tmp_path: Path) -> None:
    svc, _ = build(tmp_path)
    pid = perfil(svc)
    svc.record_inbound(pid, texts=["cheguei em Lisboa ontem"], counterparty="@ana.silva")
    assert svc.last_incoming(pid, counterparty="ana.silva") == "cheguei em Lisboa ontem"
    assert svc.last_incoming(pid, thread_key=thread_de_dm("@ana.silva")) == "cheguei em Lisboa ontem"


def test_se_a_ultima_palavra_foi_nossa_nao_ha_o_que_responder(tmp_path: Path) -> None:
    """Responder de novo a uma fala JÁ respondida é o jeito mais fácil de a conta parecer um robô."""
    svc, _ = build(tmp_path)
    pid = perfil(svc)
    svc.record_inbound(pid, texts=["cheguei em Lisboa ontem"], counterparty="@ana.silva")
    svc.record_interaction(pid, type=InteractionType.dm_sent.value, direction="outbound",
                           status=InteractionStatus.confirmed.value, counterparty="@ana.silva",
                           thread_key="dm:@ana.silva", outgoing_content="que demais! como está a cidade?")
    assert svc.last_incoming(pid, counterparty="@ana.silva") == ""


def test_perfil_sem_conversa_nenhuma_nao_tem_o_que_responder(tmp_path: Path) -> None:
    svc, _ = build(tmp_path)
    assert svc.last_incoming(perfil(svc), counterparty="@ana.silva") == ""


async def test_escrever_dm_com_fio_traz_a_conversa_ao_prompt(tmp_path: Path) -> None:
    """O efeito gravava em `dm:@alvo` e o rascunho montava o contexto sem fio nenhum: o resumo nunca chegava."""
    svc, _ = build(tmp_path)
    pid = perfil(svc)
    for i in range(8):
        svc.record_inbound(pid, texts=[f"mensagem antiga numero {i}"], counterparty="@ana.silva")
    ctx = svc.context(pid, counterparty="@ana.silva", thread_key=thread_de_dm("@ana.silva"))
    assert "<resumo_da_conversa>" in ctx.rendered
    draft, interacao = await svc.draft_response(pid, kind="dm_initiate", brief="dizer boa tarde",
                                                counterparty="@ana.silva")
    assert interacao is not None and interacao.thread_key == "dm:@ana.silva"
    assert (draft.content or "").strip()


# ---------------------------------------------------------------- 8.2 · resumo de conversa de verdade
def test_o_resumo_da_conversa_diz_o_que_foi_dito_e_nao_so_quantas(tmp_path: Path) -> None:
    svc, repo = build(tmp_path)
    pid = perfil(svc)
    svc.record_inbound(pid, texts=[f"mensagem numero {i}" for i in range(9)], counterparty="@ana.silva")
    resumo = repo.thread_row(pid, "dm:@ana.silva")["summary"]
    assert "9 mensagens confirmadas nesta conversa com @ana.silva" in resumo
    # As seis mais recentes já aparecem inteiras acima; o resumo é o que ficou para trás.
    assert "ela: “mensagem numero 0”" in resumo
    assert "mensagem numero 8" not in resumo


def test_o_resumo_distingue_quem_falou(tmp_path: Path) -> None:
    svc, repo = build(tmp_path)
    pid = perfil(svc)
    svc.record_inbound(pid, texts=["cheguei em Lisboa ontem"], counterparty="@ana.silva")
    for i in range(8):
        svc.record_interaction(pid, type=InteractionType.dm_sent.value, direction="outbound",
                               status=InteractionStatus.confirmed.value, counterparty="@ana.silva",
                               thread_key="dm:@ana.silva", outgoing_content=f"resposta {i}")
    svc.record_inbound(pid, texts=["e o frio?"], counterparty="@ana.silva")
    resumo = repo.thread_row(pid, "dm:@ana.silva")["summary"]
    assert "ela: “cheguei em Lisboa ontem”" in resumo
    assert "você: “resposta 0”" in resumo


def test_conversa_curta_nao_ganha_resumo_nenhum(tmp_path: Path) -> None:
    """Tudo o que aconteceu já aparece inteiro em interações recentes: resumir seria repetir e gastar prompt."""
    svc, repo = build(tmp_path)
    pid = perfil(svc)
    svc.record_inbound(pid, texts=["oi", "tudo bem?"], counterparty="@ana.silva")
    assert (repo.thread_row(pid, "dm:@ana.silva")["summary"] or "") == ""


def test_envio_que_falhou_nao_fecha_a_conversa(tmp_path: Path) -> None:
    """Mensagem que não saiu não respondeu ninguém: a fala dela continua pendente na execução seguinte."""
    svc, _ = build(tmp_path)
    pid = perfil(svc)
    svc.record_inbound(pid, texts=["cheguei em Lisboa ontem"], counterparty="@ana.silva")
    svc.record_interaction(pid, type=InteractionType.dm_sent.value, direction="outbound",
                           status=InteractionStatus.failed.value, counterparty="@ana.silva",
                           thread_key="dm:@ana.silva", outgoing_content="que demais!")
    assert svc.last_incoming(pid, counterparty="@ana.silva") == "cheguei em Lisboa ontem"


# ---------------------------------------------------------------- 8.2 · o ciclo inteiro, na fila de verdade
IG = "com.instagram.android"


def _prepara_run(state: object, pid: str, iid: str = "android-01") -> tuple[object, object, object]:
    """Uma execução com duas etapas reais: ler a conversa e mandar mensagem. É o plano que o dono usa."""
    db = state.db                                                        # type: ignore[attr-defined]
    db.execute("INSERT INTO apps(id, name, package, activity, builtin) VALUES ('ig','Instagram',?,NULL,0)", (IG,))
    db.execute("UPDATE instances SET app_id='ig' WHERE id=?", (iid,))
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at)"
               " VALUES ('run-dm','kdm','falar com a ana','execute','running',1,'[\"android-01\"]',"
               "'2026-09-17T10:00:00Z')")
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, parameters, profile_id)"
               " VALUES ('o-dm','run-dm',?,'running',1,'{}',?)", (iid, pid))
    db.execute(
        "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
        " depends_on, side_effect, commit_guard, postcondition, timeout_s, max_attempts, status, capability,"
        " bindings) VALUES ('o-dm:v1:t1','run-dm','o-dm',?,1,1,'open_thread_1','Abrir a conversa','abrir','[]',0,"
        "'[]','{\"kind\":\"model_judged\",\"value\":\"x\",\"description\":\"y\"}',180,1,'succeeded',"
        "'OPEN_THREAD','{\"username\": \"@ana.silva\"}')", (iid,))
    db.execute(
        "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
        " depends_on, side_effect, commit_guard, postcondition, timeout_s, max_attempts, status, capability,"
        " bindings) VALUES ('o-dm:v1:r1','run-dm','o-dm',?,1,2,'read_1','Ler as mensagens','ler',"
        "'[\"open_thread_1\"]',0,'[]','{\"kind\":\"items_collected\",\"value\":\"x\","
        "\"description\":\"y\"}',180,1,'succeeded','READ_MESSAGES','{}')", (iid,))
    db.execute(
        "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
        " depends_on, side_effect, commit_guard, postcondition, timeout_s, max_attempts, status, capability,"
        " commit_selector, bindings) VALUES ('o-dm:v1:s1','run-dm','o-dm',?,1,3,'send_1','Enviar a mensagem',"
        "'enviar','[\"read_1\"]',1,'[]','{\"kind\":\"model_judged\",\"value\":\"x\","
        "\"description\":\"y\"}',180,1,'ready','SEND_MESSAGE','id=send','{\"username\": \"@ana.silva\","
        " \"content_brief\": \"dar boa tarde\"}')", (iid,))
    return (db.one("SELECT * FROM objectives WHERE id='o-dm'"),
            db.one("SELECT * FROM steps WHERE id='o-dm:v1:s1'"),
            db.one("SELECT * FROM runs WHERE id='run-dm'"))


async def test_ler_a_conversa_e_depois_escrever_vira_RESPOSTA_e_ensina_o_perfil(harness: Any) -> None:
    """O ciclo inteiro do achado #108, sem aparelho: ler, ouvir, responder, confirmar, lembrar.

    Antes, cada elo estava quebrado: `READ_MESSAGES` não gravava nada, `SEND_MESSAGE` era sempre `dm_initiate`
    (sem `incoming`), o rascunho ia sem fio e os candidatos a memória eram descartados por falta de fala
    dirigida. O resultado media-se em produção: `memory` vazia nos oito perfis e 100% das interações de saída.
    """
    from app.models import ProfileCreate as PC, ProfilePolicyPatch

    state = harness.state
    pid = state.social.create_profile(PC(username="lucas.almeida9484", password=SENHA, instance_id="android-01",
                                         persona_id=state.social.create_persona(PERSONA).id)).id
    state.social.set_policy(pid, ProfilePolicyPatch(capabilities={"SEND_MESSAGE": "autonomous"}))
    obj, srow, run = _prepara_run(state, pid)

    # 1) a etapa de leitura termina: o que ela leu entra no histórico como fala DELA
    leitura = state.db.one("SELECT * FROM steps WHERE id='o-dm:v1:r1'")
    state.scheduler.on_items_collected(obj, state.repo.step_dto(leitura),
                                       ["oi! tudo bem?", "eu mudei para Lisboa no mês passado"])
    entradas = [i for i in state.social.list_interactions(pid, limit=10) if i.direction == "inbound"]
    assert [i.incoming_content for i in entradas] == ["eu mudei para Lisboa no mês passado", "oi! tudo bem?"]
    assert all(i.counterparty == "@ana.silva" and i.thread_key == "dm:@ana.silva" for i in entradas)

    # 2) a porta que escreve: com fala dela pendente, isto é RESPONDER, não puxar conversa
    vistos: dict[str, Any] = {}
    original = state.social.draft_response

    async def espiao(profile_id: str, **kw: Any) -> Any:
        vistos.update(kw)
        return await original(profile_id, **kw)

    state.social.draft_response = espiao                                  # type: ignore[method-assign]
    assert await state._policy_gate(obj, srow, run) is None
    assert vistos["kind"] == "dm_reply"
    assert vistos["incoming"] == "eu mudei para Lisboa no mês passado"
    assert vistos["thread_key"] == "dm:@ana.silva"

    depois = state.db.one("SELECT bindings, draft_meta FROM steps WHERE id='o-dm:v1:s1'")
    import json as _json
    texto = (_json.loads(depois["bindings"]) or {}).get("content")
    assert texto, "a etapa ficou sem texto"
    meta = _json.loads(depois["draft_meta"] or "{}")
    assert meta["incoming"] == "eu mudei para Lisboa no mês passado"
    # A fala dirigida é a ÚNICA origem de memória: sem `incoming`, os candidatos são descartados em código.
    assert meta["memory_candidates"], "a fala dela não produziu candidato nenhum"

    # 3) o efeito é disparado e confirmado: é o único caminho que vira fato — e fato ensina
    iid_efeito = state.social.open_effect(
        pid, capability="SEND_MESSAGE", interaction_type=InteractionType.dm_sent.value,
        bindings={"username": "@ana.silva", "content": texto}, run_id="run-dm", draft_meta=meta)
    state.social.settle_effect(pid, iid_efeito, outcome="succeeded", evidence="mensagem visível na conversa")
    enviada = state.social.get_interaction(pid, iid_efeito)
    assert enviada.thread_key == "dm:@ana.silva"
    assert enviada.incoming_content == "eu mudei para Lisboa no mês passado"   # o que foi respondido fica gravado
    aprendido = state.social.memory.list(pid)
    assert aprendido, "o perfil não aprendeu nada da conversa"            # era exatamente o zero do achado #108
    assert "Lisboa" in aprendido[0].content and aprendido[0].subject == "@ana.silva"

    # 4) a execução SEGUINTE já encontra o que ficou: fala dela respondida não se responde duas vezes
    assert state.social.last_incoming(pid, counterparty="@ana.silva") == ""
    contexto = state.social.context(pid, counterparty="@ana.silva", thread_key="dm:@ana.silva")
    assert "Lisboa" in contexto.rendered
    assert "<memoria_relevante>" in contexto.rendered


async def test_levantar_a_caixa_de_entrada_nao_inventa_fala_de_ninguem(harness: Any) -> None:
    """`COLLECT_THREADS` levanta NOMES de conversa. Gravar aquilo como mensagem seria o histórico mentindo."""
    from app.models import ProfileCreate as PC

    state = harness.state
    pid = state.social.create_profile(PC(username="lucas.almeida9484", password=SENHA,
                                         instance_id="android-01")).id
    obj, _srow, _run = _prepara_run(state, pid)
    state.db.execute("UPDATE steps SET capability='COLLECT_THREADS' WHERE id='o-dm:v1:r1'")
    leitura = state.db.one("SELECT * FROM steps WHERE id='o-dm:v1:r1'")
    state.scheduler.on_items_collected(obj, state.repo.step_dto(leitura), ["ana.silva", "bruno.mendes"])
    assert state.social.list_interactions(pid, limit=10) == []


# ---------------------------------------------------------------- 8.1 · a proposta de voz das oito personas
def test_proposta_de_voz_cobre_os_oito_campos_e_usa_nomes_que_existem() -> None:
    """O arquivo que o dono vai aplicar precisa valer ANTES de tocar no banco dele.

    Um nome de campo errado passaria em silêncio pelo `PATCH` (traits é um objeto), e a persona continuaria
    vazia — com o portal dizendo que está completa. Aqui ele é validado contra o MESMO modelo da API.
    """
    import json

    from app.models import PersonaTraits as PT

    raiz = Path(__file__).resolve().parents[2]
    dados = json.loads((raiz / "scripts" / "personas-voz.json").read_text(encoding="utf-8"))["personas"]
    faltando = ("slang", "dm_style", "comment_style", "with_known", "with_strangers", "common_phrases",
                "forbidden_phrases", "examples")
    assert len(dados) == 8, "o parque tem oito personas"
    for nome, voz in dados.items():
        assert set(voz) == set(faltando), f"{nome}: campos fora do combinado ({sorted(set(voz) ^ set(faltando))})"
        PT.model_validate(voz)                       # extra="forbid": nome de campo errado estoura aqui
        assert voice_gaps(PT.model_validate({**BASICO, **voz})) == [], nome


#: Os sete traços que as oito personas do parque já têm preenchidos. Só serve para provar que a proposta
#: COMPLETA a persona: básico + proposta = nenhum campo de voz vazio.
BASICO = {"personality": "x", "tone": "y", "formality": "informal", "typical_length": "curta", "emojis": "raro",
          "humor": "z", "interests": ["w"]}
