"""Fase I, de ponta a ponta no harness: os TRÊS chamadores da RESOLVE dão a mesma resposta para a mesma frase.

`RunService._plan` (a execução), `POST /api/flows/match` (a estimativa do painel) e `apps_exigidos` (o pré-voo) —
mais a rota nova, `POST /api/skills/resolve`, que só prevê. Para cada frase da tabela:

- resolvida → a execução usa a habilidade sem chamar o planejador (`count("plan") == 0`), a estimativa é dela e o
  pré-voo exige o app dela;
- pergunta (tipo inválido, buraco vazio, empate) → `needs_input` com a pergunta, sem plano, sem objetivo e sem
  planejador; a estimativa é `null` e o pré-voo não exige nada;
- nada casa → o planejador de sempre; estimativa `null`, nada exigido.

Nível de prova: `simulated` — Harness (porta base 5640) + `FakeInstagram` + `AtorDoInstagram` no `CountingProvider`,
execuções em `mode=plan` (param em `planned`, nada roda no aparelho). Nenhuma IA real: as etapas semântica e LLM
ficam no provedor nulo e a rota diz `not_run`.
"""
from __future__ import annotations

import copy
from typing import Any

import pytest

from app.models import Plan
from app.modules.skills.domain.lifecycle import ContentTampered, SkillState
from app.modules.skills.domain.versions import Provenance, SourceKind
from app.state import AppState

from .conftest import Harness
from .fake_skills import perfil
from .test_fatia_abrir_conversa import (ABRIR, DONO, IID, carregar, cliente, estado, parque,  # noqa: F401
                                        publicar_abrir)


def publicar_variacao(s: AppState, skill_id: str, modelo: str, parametro: str) -> None:
    """Uma cópia de `ig.abrir_conversa` com outro comando, validada à mão pelo dono (P4) e publicada."""
    d = copy.deepcopy(carregar(ABRIR))
    d["metadata"]["id"], d["metadata"]["name"] = skill_id, f"Variação {skill_id}"
    d["spec"]["invocation"] = {"command_template": modelo, "examples": []}
    d["spec"]["parameters"] = [{"name": parametro, "type": "handle", "required": True, "example": "@ana"}]
    d["spec"]["nodes"][1]["with"] = {"username": "${parameters." + parametro + "}"}
    d["spec"]["success_criteria"] = []
    d["spec"]["validation"] = {"cases": []}
    repo = s.skill_repo
    v = repo.create_draft(skill_id, d, source=Provenance(SourceKind.MANUAL), by=DONO)
    repo.transition(v.ref, SkillState.CANDIDATE, by=DONO, reason="submetida")
    repo.transition(v.ref, SkillState.VALIDATED, by=DONO, manual=True, reason="variação de teste da fase I")
    repo.transition(v.ref, SkillState.PUBLISHED, by=DONO, reason="fase I")


#: frase → o que os três chamadores devem dizer: "skill" (e qual, com que parâmetros), "pergunta" ou "planejador".
FRASES: list[tuple[str, str, str | None, dict[str, str] | None]] = [
    ("abra a conversa com @ana no instagram", "skill", "ig.abrir_conversa@1", {"username": "@ana"}),
    ("abra a conversa com https://www.instagram.com/Bia/ no instagram", "skill", "ig.abrir_conversa@1",
     {"username": "@bia"}),
    ("abra a conversa com a Ana no instagram", "pergunta", "ig.abrir_conversa@1", None),       # tipo inválido
    ("abra a conversa com no instagram", "pergunta", "ig.abrir_conversa@1", None),             # buraco vazio
    ("abra o direct com @ana no instagram", "pergunta", None, None),                           # empate
    ("poste uma foto do gato", "planejador", None, None),                                      # nada casa
]


async def test_os_tres_chamadores_coerentes_para_a_mesma_frase(parque: Harness) -> None:  # noqa: F811
    s, ai = estado(parque), parque.ai
    await publicar_abrir(s)
    publicar_variacao(s, "ig.abrir_dm", "abra o direct com {usuario} no instagram", "usuario")
    publicar_variacao(s, "ig.abrir_dm_b", "abra o direct com {pessoa} no instagram", "pessoa")
    async with cliente(parque) as c:
        for frase, via, ref, parametros in FRASES:
            planos_antes = ai.count("plan")
            run = parque.run([IID], command=frase, mode="plan")
            detalhe = await parque.wait_run(run.id, statuses=("planned", "needs_input", "failed"), timeout=30)
            linha = s.repo.run_row(run.id)
            estimativa = (await c.post("/api/flows/match", json={"command": frase})).json()
            exigidos = [a["id"] for a in s.runs.apps_exigidos(frase)]
            previa = (await c.post("/api/skills/resolve", json={"command": frase})).json()
            etapas = {e["stage"]: e["outcome"] for e in previa["stages"]}
            assert previa["gated_by_config"] == {"skills.enabled": True, "ai.flows": True}
            if via == "skill":
                assert detalhe.status == "planned", (frase, detalhe.status_detail)
                assert ai.count("plan") == planos_antes, frase
                plano = Plan.model_validate_json(linha["plan"])
                assert (plano.planner.model, plano.parameters) == (f"skill:{ref}", parametros), frase
                assert (estimativa["skill_ref"], exigidos) == (ref, ["instagram"]), frase
                assert previa["status"] == "resolved" and previa["intent"]["skill_ref"] == ref
                assert {p["name"]: p["value"] for p in previa["intent"]["parameters"]} == parametros
            elif via == "pergunta":
                assert detalhe.status == "needs_input", (frase, detalhe.status)
                assert ai.count("plan") == planos_antes, frase                  # nem o planejador por fora
                assert linha["plan"] is None, frase                               # sem plano, nem parcial
                assert s.db.scalar("SELECT COUNT(*) FROM objectives WHERE run_id=?", (run.id,)) == 0
                assert (estimativa, exigidos) == (None, []), frase
                assert previa["status"] == "needs_input" and previa["questions"], frase
                assert detalhe.status_detail == " | ".join(q["question"] for q in previa["questions"])
                assert previa["subject"] == ref and linha["skill_id"] == (ref.split("@")[0] if ref else None)
                assert etapas["semantic"] in ("not_run", "skipped") and etapas["llm"] in ("not_run", "skipped")
            else:
                assert ai.count("plan") == planos_antes + 1, frase
                assert Plan.model_validate_json(linha["plan"]).planner.provider == "ator-do-instagram"
                assert (estimativa, exigidos) == (None, []), frase
                assert previa["status"] == "no_match" and etapas["semantic"] == "not_run"
                assert linha["skill_id"] is None and linha["flow_id"] is None


async def test_empate_na_execucao_pergunta_com_as_opcoes_e_nao_grava_skill(parque: Harness) -> None:  # noqa: F811
    s = estado(parque)
    publicar_variacao(s, "ig.abrir_dm", "abra o direct com {usuario} no instagram", "usuario")
    publicar_variacao(s, "ig.abrir_dm_b", "abra o direct com {pessoa} no instagram", "pessoa")
    run = parque.run([IID], command="abra o direct com @ana no instagram")
    detalhe = await parque.wait_run(run.id, statuses=("needs_input", "failed", "completed"), timeout=30)
    assert detalhe.status == "needs_input"
    assert "ig.abrir_dm@1" in (detalhe.status_detail or "") and "ig.abrir_dm_b@1" in (detalhe.status_detail or "")
    linha = s.repo.run_row(run.id)
    assert (linha["skill_id"], linha["skill_version"], linha["skill_hash"], linha["plan"]) == (None, None, None, None)
    [evento] = [e for e in s.db.query("SELECT data FROM events WHERE run_id=? AND message LIKE ?",
                                      (run.id, "%precisa de resposta%"))]
    assert '"questions"' in evento["data"] and "ig.abrir_dm_b@1" in evento["data"]


# ==================================================================== a rota
async def test_rota_resolve_atras_de_skills_enabled_e_sempre_com_os_interruptores(parque: Harness) -> None:  # noqa: F811
    s = estado(parque)
    await publicar_abrir(s)
    async with cliente(parque) as c:
        parque.cfg.file.skills.enabled = False
        r = await c.post("/api/skills/resolve", json={"command": "abra a conversa com @ana no instagram"})
        assert r.status_code == 404                      # como as rotas do ensino v2 com o flag desligado
        corpo = r.json()["detail"]
        assert corpo["code"] == "skills_disabled"
        assert corpo["gated_by_config"] == {"skills.enabled": False, "ai.flows": True}
        parque.cfg.file.skills.enabled = True
        parque.cfg.file.ai.flows = False
        r = await c.post("/api/skills/resolve", json={"command": "abra a conversa com @ana no instagram"})
        assert r.status_code == 200 and r.json()["gated_by_config"] == {"skills.enabled": True, "ai.flows": False}
        # corpo estrito, e aparelho que não existe é 404
        assert (await c.post("/api/skills/resolve", json={"command": "x", "extra": 1})).status_code == 422
        assert (await c.post("/api/skills/resolve", json={"command": ""})).status_code == 422
        r = await c.post("/api/skills/resolve", json={"command": "x", "instance_ids": ["android-99"]})
        assert r.status_code == 404
    # a prévia não cria execução nem chama IA
    assert s.db.scalar("SELECT COUNT(*) FROM runs") == 0 and parque.ai.calls == []


@pytest.mark.parametrize("corpo, status", [
    ({}, "resolved"),                                            # prévia sem aparelhos: qualquer escopo casa
    ({"profile_ids": ["p1"]}, "resolved"),
    ({"profile_ids": ["g1-p"]}, "resolved"),                     # pelo grupo
    ({"profile_ids": ["p2"]}, "no_match"),
    ({"instance_ids": [IID]}, "no_match"),                       # aparelho sem perfil vinculado: fora do escopo
])
async def test_rota_resolve_confere_o_escopo_como_o_planejamento(parque: Harness, corpo: dict[str, Any],  # noqa: F811
                                                                 status: str) -> None:
    s = estado(parque)
    await publicar_abrir(s)
    perfil(s.db, "p1")
    perfil(s.db, "p2")
    perfil(s.db, "g1-p", grupo="g1")
    s.skill_repo.set_scope(ABRIR, profile_ids=["p1"], group_ids=["g1"])
    async with cliente(parque) as c:
        r = await c.post("/api/skills/resolve", json={"command": "abra a conversa com @ana no instagram", **corpo})
    assert r.status_code == 200 and r.json()["status"] == status


async def test_rota_resolve_recusa_versao_adulterada_com_409(parque: Harness, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    """`ContentTampered` entre as candidatas vira 409 `content_tampered` com os interruptores — não 500."""
    s = estado(parque)
    await publicar_abrir(s)

    def adulterada(*_a: object, **_k: object) -> None:
        raise ContentTampered("ig.abrir_conversa@1: o conteúdo não bate com o hash gravado — versão alterada por fora.")

    monkeypatch.setattr(s.skill_planner, "resolve_intent", adulterada)
    async with cliente(parque) as c:
        r = await c.post("/api/skills/resolve", json={"command": "abra a conversa com @ana no instagram"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "content_tampered"
    assert "gated_by_config" in r.json()["detail"]



async def test_flows_match_so_aceita_post_com_corpo_json(parque: Harness) -> None:  # noqa: F811
    """29.25: o rascunho do comando (às vezes com e-mail) não vai mais na query string, que vira linha de log de
    acesso. O GET antigo deixou de existir (405) e o corpo é validado (vazio, campo a mais e texto longo: 422)."""
    async with cliente(parque) as c:
        assert (await c.get("/api/flows/match", params={"command": "abra a conversa com @ana"})).status_code == 405
        assert (await c.post("/api/flows/match")).status_code == 422                       # sem corpo
        assert (await c.post("/api/flows/match", params={"command": "x"})).status_code == 422   # a query não vale
        assert (await c.post("/api/flows/match", json={"command": ""})).status_code == 422
        assert (await c.post("/api/flows/match", json={"command": "x" * 4001})).status_code == 422
        assert (await c.post("/api/flows/match", json={"command": "x", "extra": 1})).status_code == 422
        ok = await c.post("/api/flows/match", json={"command": "poste uma foto do gato"})
        assert ok.status_code == 200 and ok.json() is None                                  # nada casa: `null`
