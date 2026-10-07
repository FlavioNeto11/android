"""31.219: o ensino do fluxo para alvo de TERCEIRO, preparado para a conta de teste (sem executar nada real).

O alvo da onda 2 passou a ser o primeiro post de uma página pública de terceiro (P-029), e a única receita ensinada do
Instagram (a 221) abre o PRÓPRIO perfil. A proposta de referência (`fixtures/ensino/proposta_alvo_de_terceiro.json`)
é o que a sessão de ensino deve salvar quando o dono der a conta: busca → perfil de `{username}` → primeira
publicação → comentários, só leitura, com as ações do catálogo do Instagram. O roteiro está em
`docs/dominios/aprendizado.md` ("O ensino do fluxo para alvo de terceiro").

O que estes testes protegem:
* a proposta só usa ações do catálogo do Instagram, nenhuma com efeito, e o único parâmetro é `{username}`;
* cada etapa monta pelo catálogo, com a pós-condição dele (sem `model_judged` no post e nos comentários);
* na prévia de uma sessão de ensino do Instagram, ela não é recusada, não avisa "conta da própria persona" (31.182) e
  sai com as três etapas; nada é gravado (é a prévia).

Nível de prova: `simulated` (harness com aparelho falso; nenhuma IA; nenhuma conta real).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.planning.capabilities import CapabilityNode, load_catalog

from .conftest import Harness
from .test_modo_treinamento import _no_controle

PROPOSTA = Path(__file__).parent / "fixtures" / "ensino" / "proposta_alvo_de_terceiro.json"
PKG = "com.instagram.android"


def _proposta() -> dict[str, Any]:
    return json.loads(PROPOSTA.read_text(encoding="utf-8"))


def test_a_proposta_so_le_e_so_tem_o_username() -> None:
    p = _proposta()
    cat = load_catalog(PKG)
    assert cat is not None
    assert [s["capability"] for s in p["steps"]] == ["OPEN_PROFILE", "OPEN_POST", "OPEN_COMMENTS"]
    assert [x["name"] for x in p["parameters"]] == ["username"]
    for st in p["steps"]:
        cap = cat.get(st["capability"])
        assert not cap.side_effect and not st["side_effect"], st["key"]
        valores = [b["value"] for b in st["bindings"]]
        assert all("{" not in v or v == "{username}" for v in valores), st["key"]     # só o parâmetro do comando
        passo = cat.build_step(CapabilityNode(key=st["key"], capability=st["capability"],
                                              bindings={b["name"]: b["value"] for b in st["bindings"]}))
        assert not passo.side_effect
        if st["capability"] != "OPEN_PROFILE":
            assert passo.postcondition.kind == "element_present", st["key"]     # prova local, sem juiz
    assert "conta_" not in json.dumps(p)                     # nunca o marcador da conta da persona (31.182)


async def test_a_previa_aceita_sem_aviso_de_conta_propria(harness: Harness) -> None:
    st, rt, lease = await _no_controle(harness)
    s = st.training.start("android-01", intent="Abrir o primeiro post de uma página", lease_id=lease,
                          app_id="instagram")
    st.training.record(rt, {"type": "open_app", "app_id": "instagram"}, None)
    st.training.stop(s["id"], lease_id=lease)
    flows_antes = st.db.scalar("SELECT COUNT(*) FROM flows")
    receitas_antes = st.db.scalar("SELECT COUNT(*) FROM recipes")
    proposta = _proposta()
    # a entrada gravada aqui (abrir o app) não é de etapa nenhuma; no real, a IA atribui cada entrada a uma etapa
    proposta["discarded"] = [{"seq": 1, "reason": "abrir o app"}]
    previa = await st.skills.preview(s["id"], proposal=proposta, profile_ids=[], group_ids=[])
    assert [e["key"] for e in previa["steps"]] == ["abrir_perfil", "abrir_primeira_publicacao", "abrir_comentarios"]
    assert not any("mira a conta da própria persona" in a for a in previa["warnings"]), previa["warnings"]
    assert st.db.scalar("SELECT COUNT(*) FROM flows") == flows_antes                     # a prévia não grava
    assert st.db.scalar("SELECT COUNT(*) FROM recipes") == receitas_antes
