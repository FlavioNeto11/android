"""31.269: a troca do grupo de política da persona deixa trilha (evento `profile.policy_group`, adendo v1.131).

Trocar de grupo tira a persona da aprovação ou a põe sob ela; o 31.265 só anunciava a mudança de status. O evento leva o
id da persona, os ids dos dois grupos e o autor — nunca o nome do grupo, o @ nem a pessoa — e não sai sem mudança de fato.

Nível de prova: `simulated` (harness e SQLite; nenhuma conta real).
"""
from __future__ import annotations

import json
from typing import Any

from app.models import ProfilePatch
from app.util import now_iso

from .conftest import Harness
from .test_operacoes import _persona


def _grupo(st: Any, gid: str, nome: str) -> None:
    st.db.execute("INSERT INTO policy_groups(id, name, created_at, updated_at) VALUES (?,?,?,?)",
                  (gid, nome, now_iso(), now_iso()))


def _eventos(st: Any, pid: str) -> list[dict[str, Any]]:
    return [json.loads(r["data"]) for r in st.db.query(
        "SELECT data FROM events WHERE kind='profile.policy_group' ORDER BY id") if json.loads(r["data"])["profile_id"] == pid]


async def test_a_troca_de_grupo_deixa_o_evento_com_anterior_novo_e_autor(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    pid = _persona(harness, "Ana")
    _grupo(st, "grp-a", "Grupo Secreto A")
    _grupo(st, "grp-b", "Grupo Secreto B")
    st.social.update_profile(pid, ProfilePatch(policy_group_id="grp-a"))
    st.social.update_profile(pid, ProfilePatch(policy_group_id="grp-b"))
    st.social.update_profile(pid, ProfilePatch(policy_group_id=None))
    assert _eventos(st, pid) == [
        {"profile_id": pid, "anterior": None, "novo": "grp-a", "autor": "painel"},
        {"profile_id": pid, "anterior": "grp-a", "novo": "grp-b", "autor": "painel"},
        {"profile_id": pid, "anterior": "grp-b", "novo": None, "autor": "painel"}]
    texto = " ".join(str(r["message"]) for r in st.db.query("SELECT message FROM events WHERE kind='profile.policy_group'"))
    assert "Secreto" not in texto and "ana" not in texto.lower()                 # nem nome do grupo nem da pessoa


async def test_sem_mudanca_de_fato_ou_sem_o_campo_nao_ha_evento(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    pid = _persona(harness, "Bia")
    _grupo(st, "grp-c", "C")
    st.social.update_profile(pid, ProfilePatch(policy_group_id="grp-c"))
    st.social.update_profile(pid, ProfilePatch(policy_group_id="grp-c"))          # o mesmo grupo
    st.social.update_profile(pid, ProfilePatch(first_name="Beatriz"))              # outro campo
    assert len(_eventos(st, pid)) == 1
