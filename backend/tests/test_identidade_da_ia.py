"""Item 29.57: a IA da Central se chama ANA onde fala com a pessoa, e o nome nunca entra no que a persona publica.

A regra vive em `app/contracts/identidade.py` e entra no prompt de sistema de quem fala com a pessoa (os três
planejadores, que perguntam o que falta e recusam, e o assistente do comando). O escritor social fala PELA persona:
a regra não entra lá, nem o nome, nem a apresentação. O painel espelha o nome em `frontend/src/lib/identidade.ts`.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.contracts.identidade import APRESENTACAO_DA_IA, NOME_DA_IA, REGRA_DE_IDENTIDADE
from app.modules.execution.domain.command_refinement import refine_system
from app.planning import prompts

RAIZ = Path(__file__).resolve().parents[2]


def test_quem_fala_com_a_pessoa_leva_a_regra_e_diz_que_e_ia() -> None:
    for nome in ("PLANNER_SYSTEM", "PLANNER_CAPABILITY_SYSTEM", "PLANNER_MULTIAPP_SYSTEM", "PLANNER_SYSTEM_CURTO",
                 "PLANNER_MULTIAPP_SYSTEM_CURTO"):
        assert REGRA_DE_IDENTIDADE in getattr(prompts, nome), nome
    assert REGRA_DE_IDENTIDADE in refine_system(prompts.UNTRUSTED_RULE, prompts.CONDUCT_RULE)
    assert f"você é {NOME_DA_IA}" in REGRA_DE_IDENTIDADE
    assert "diga que é uma IA" in REGRA_DE_IDENTIDADE


def test_o_nome_nunca_entra_no_prompt_de_quem_escreve_pela_persona() -> None:
    """O escritor social escreve post, comentário e mensagem da persona: nem a regra, nem o nome, nem a apresentação.
    O ator e o verificador não falam com ninguém, e também ficam de fora."""
    for nome in ("SOCIAL_SYSTEM", "ACTOR_SYSTEM", "VERIFIER_SYSTEM"):
        texto = getattr(prompts, nome)
        assert REGRA_DE_IDENTIDADE not in texto, nome
        assert APRESENTACAO_DA_IA not in texto, nome
        assert re.search(rf"\b{NOME_DA_IA}\b", texto) is None, nome


def test_o_escritor_social_nao_importa_a_identidade() -> None:
    """Por construção: o caminho do conteúdo publicado (`app/social/`) não alcança o nome da IA."""
    for arquivo in (RAIZ / "backend" / "app" / "social").rglob("*.py"):
        assert "contracts.identidade" not in arquivo.read_text(encoding="utf-8"), arquivo


def test_o_painel_usa_o_mesmo_nome() -> None:
    fonte = (RAIZ / "frontend" / "src" / "lib" / "identidade.ts").read_text(encoding="utf-8")
    assert re.search(r"export const NOME_DA_IA = '([^']+)';", fonte).group(1) == NOME_DA_IA  # type: ignore[union-attr]
