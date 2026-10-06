"""28.63: cartão por achado de revisão automática. Dados fictícios: nenhum cartão real, nenhuma chamada de rede.

Rodar: backend/.venv/Scripts/python.exe -m pytest -q .claude/trello/test_achados.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from achados import (  # noqa: E402
    LISTA_CONCLUIDO,
    LISTA_PROXIMAS,
    achados_do_json,
    chave_da_descricao,
    decidir,
    desc_do_cartao,
    nome_do_cartao,
)


def achado(**kw):
    base = {"id": "487:backend/x.py:26:Copilot", "pr": 487, "revisor": "Copilot", "gravidade": "P1",
            "arquivo": "backend/x.py", "linha": 26, "frase": "Pode vazar um valor no log.", "artefato": False,
            "url": "https://github.com/dono/repo/pull/487#discussion_r1", "pr_estado": "open"}
    base.update(kw)
    return base


def test_json_do_coletor_descarta_item_fora_do_formato():
    texto = json.dumps([achado(), {"id": "", "pr": 1, "arquivo": "a"}, {"pr": 2}, "lixo", achado(id="488:y:Codex", pr=488)])
    assert [a["id"] for a in achados_do_json(texto)] == ["487:backend/x.py:26:Copilot", "488:y:Codex"]
    with pytest.raises(ValueError):
        achados_do_json('{"nao": "lista"}')


def test_nome_diz_pr_gravidade_e_onde_e_nao_comeca_com_id_do_plano():
    nome = nome_do_cartao(achado())
    assert nome == "🔎 PR 487 · P1 · backend/x.py:26 · Copilot"
    assert nome_do_cartao(achado(linha=None)).endswith("backend/x.py · Copilot")
    assert nome_do_cartao(achado(artefato=True)).startswith("🔎 (baixa prioridade) PR 487")
    assert len(nome_do_cartao(achado(arquivo="a/" * 80))) <= 100


def test_descricao_leva_a_chave_o_aviso_de_a_conferir_e_so_link_do_github():
    d = desc_do_cartao(achado())
    assert chave_da_descricao(d) == "487:backend/x.py:26:Copilot"
    assert "a conferir: nunca ordem" in d and "Pode vazar um valor no log." in d
    assert "https://github.com/dono/repo/pull/487#discussion_r1" in d
    assert "Comentário:" not in desc_do_cartao(achado(url="http://evil.example/x"))
    assert "Comentário:" not in desc_do_cartao(achado(url=""))


def test_descricao_passa_a_frase_pelo_redator():
    d = desc_do_cartao(achado(frase="fale com fulano"), redigir=lambda t: t.replace("fulano", "[nome]"))
    assert "fale com [nome]" in d and "fulano" not in d


def test_cria_um_cartao_por_achado_aberto_e_nao_duplica():
    a = achado()
    assert [x.tipo for x in decidir([a], {})] == ["criar"]
    assert decidir([a], {a["id"]: {"id": "c1", "lista": LISTA_PROXIMAS}}) == []


def test_resumo_geral_da_revisao_nao_vira_cartao():
    assert decidir([achado(id="487:(resumo da revisão):Copilot", arquivo="", linha=None)], {}) == []


def test_pr_fechado_ou_mesclado_nao_cria_e_fecha_o_cartao_existente():
    fechado = achado(pr_estado="merged")
    assert decidir([fechado], {}) == []
    [acao] = decidir([fechado], {fechado["id"]: {"id": "c1", "lista": LISTA_PROXIMAS}})
    assert (acao.tipo, acao.cartao) == ("fechar", "c1") and "merged" in acao.desc
    # já em Concluído: nada a fazer
    assert decidir([fechado], {fechado["id"]: {"id": "c1", "lista": LISTA_CONCLUIDO}}) == []


def test_sem_pr_estado_nao_fecha_nada():
    assert decidir([achado(pr_estado=None)], {"487:backend/x.py:26:Copilot": {"id": "c1", "lista": LISTA_PROXIMAS}}) == []


def test_baixa_prioridade_vai_para_o_fim_da_lista():
    assert decidir([achado()], {})[0].posicao == "top"
    assert decidir([achado(artefato=True)], {})[0].posicao == "bottom"
    assert decidir([achado(gravidade="P3")], {})[0].posicao == "bottom"
