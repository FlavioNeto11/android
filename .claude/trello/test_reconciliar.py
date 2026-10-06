"""Reconciliação dos quadros do Trello com o estado do plano (regra de 06/10/2026). Tudo com dados fictícios: nenhum
cartão real, nenhuma chamada de rede.

Rodar: backend/.venv/Scripts/python.exe -m pytest -q .claude/trello/test_reconciliar.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from reconciliar import (  # noqa: E402
    MARCA,
    Acao,
    auditar_historico_e_programa,
    com_linha,
    decidir,
    deploy_do_item,
    deploys_do_changelog,
    ids_citados_por_deploy,
    ids_citados_por_deploy,
    id_do_item,
    inicio_da_semana,
    leitura_datada,
    papel_da_lista,
    topo_da_linha,
)

AGORA = datetime(2026, 10, 6, 2, 0, tzinfo=timezone.utc)          # terça 05/10 23:00 em Brasília; a semana abriu 05/10 03:00Z
HORAS = {38: "2026-10-05T16:28:44", 39: "2026-10-05T18:11:11", 43: "2026-10-06T00:50:39"}
HIST = {"Fases 28": "L28", "Fase 29": "L29", "Fases 0–5": "L05"}
SUITES = {"29.1": 38, "29.2": 44}


def estado(**itens):
    return {"itens": itens}


def cartao(i, nome, lista, desc=""):
    return {"id": i, "nome": nome, "lista": lista, "desc": desc}


def roda(cartoes, est):
    return decidir(cartoes, est, agora=AGORA, horas=HORAS, suite_de=lambda p: SUITES.get(p), listas_do_historico=HIST)


def test_nome_e_lista():
    assert id_do_item("29.1 · algo") == "29.1"
    assert id_do_item("[A] 🙋 31.90 · algo") == "31.90"
    assert id_do_item("#162 · 31.21 · algo") == "31.21"
    assert id_do_item("P-001. Pergunta") is None
    assert id_do_item("Suíte 16 → deploy 16") is None
    assert papel_da_lista("✅ Concluído nesta semana") == "concluido"
    assert papel_da_lista("🧪 Em validação") == "em_validacao"
    assert papel_da_lista("🙋 Espera você") == "espera_voce"


def test_semana_vira_na_segunda_em_brasilia():
    assert inicio_da_semana(AGORA) == "2026-10-05T03:00:00"
    assert inicio_da_semana(datetime(2026, 10, 5, 2, 59, tzinfo=timezone.utc)) == "2026-09-28T03:00:00"


def test_deploys_do_changelog():
    t = "## 2026-10-06 — Deploy 43 (suíte 43)\n\n## 2026-10-05 — Deploy 42 (x)\n## 2026-10-05 — 29.1 outra coisa\n"
    assert deploys_do_changelog(t) == [42, 43]


def test_deploy_do_item():
    assert deploy_do_item("2026-10-05T10:00:00", 38, HORAS) == 38          # a suíte do commit manda
    assert deploy_do_item("2026-10-05T10:00:00", 44, HORAS) is None        # suíte que ainda não foi ao ar
    assert deploy_do_item("2026-10-01T10:00:00+00:00", None, HORAS) == 0   # antes do primeiro deploy conhecido
    assert deploy_do_item("2026-10-05T17:00:00+00:00", None, HORAS) == 39  # entre o 38 e o 39: entrou no 39
    assert deploy_do_item("2026-10-06T01:30:00+00:00", None, HORAS) is None
    assert deploy_do_item(None, None, HORAS) is None


def test_implementado_e_implantado_vai_a_concluido_com_a_linha_de_prova():
    est = estado(**{"29.1": {"status": "implemented", "proof": "simulated", "quando": "2026-10-05T17:00:00+00:00",
                             "evidence": "backend/tests/test_x.py::test_a passou"}})
    rel = roda([cartao("c1", "29.1 · algo", "🧭 Próximas")], est)
    a = rel.acoes[0]
    assert (a.tipo, a.para) == ("mover", "concluido")
    assert a.linha.startswith("prova simulada (backend/tests/test_x.py::test_a)")
    assert "no ar desde o deploy 38" in a.linha


def test_prova_real_leva_data_e_ids():
    est = estado(**{"29.1": {"status": "implemented", "proof": "real", "quando": "2026-10-05T17:00:00+00:00",
                             "evidence": "validado em 05/10 nas execuções r-20261005123456-abc e r-20261005123500-def"}})
    a = roda([cartao("c1", "29.1 · algo", "🧭 Próximas")], est).acoes[0]
    assert a.linha.startswith("prova real (05/10, r-20261005123456-abc, r-20261005123500-def;")


def test_implementado_sem_deploy_fica_em_validacao():
    est = estado(**{"29.2": {"status": "implemented", "proof": "simulated", "quando": "2026-10-06T01:00:00+00:00"}})
    a = roda([cartao("c2", "29.2 · algo", "✅ Concluído nesta semana")], est).acoes[0]
    assert (a.tipo, a.para) == ("mover", "em_validacao")
    assert "ainda não implantado" in a.linha


def test_parcial_sai_de_concluido_e_diz_o_que_falta():
    est = estado(**{"29.1": {"status": "partial", "status_detail": "falta a prova real no aparelho"}})
    a = roda([cartao("c1", "29.1 · algo", "✅ Concluído nesta semana")], est).acoes[0]
    assert (a.tipo, a.para) == ("mover", "em_validacao")
    assert a.linha == "item parcial no plano; falta: falta a prova real no aparelho"


def test_parcial_em_execucao_so_ganha_a_linha_uma_vez():
    est = estado(**{"29.1": {"status": "partial"}})
    a = roda([cartao("c1", "29.1 · algo", "🛠 Em execução")], est).acoes[0]
    assert a.tipo == "marcar"
    pronto = cartao("c1", "29.1 · algo", "🛠 Em execução", desc=MARCA + "estado do plano (x):** fica.")
    assert roda([pronto], est).acoes == []


def test_bloqueado_vai_para_bloqueado():
    est = estado(**{"29.1": {"status": "blocked", "blocker": "espera o sim do dono"}})
    a = roda([cartao("c1", "29.1 · algo", "🧪 Em validação")], est).acoes[0]
    assert (a.tipo, a.para) == ("mover", "bloqueado")
    assert a.linha == "item bloqueado no plano: espera o sim do dono"


def test_semana_anterior_vai_para_a_lista_da_fase_no_historico():
    est = estado(**{"28.7": {"status": "implemented", "proof": "simulated", "quando": "2026-10-02T10:00:00+00:00"}})
    a = roda([cartao("c1", "28.7 · algo", "✅ Concluído nesta semana")], est).acoes[0]
    assert (a.tipo, a.para, a.lista_do_historico) == ("mover", "historico", "L28")
    assert "um deploy anterior ao 38" in a.linha


def test_espera_voce_e_sem_estado_so_sao_listados():
    est = estado(**{"29.1": {"status": "implemented", "proof": "simulated", "quando": "2026-10-05T17:00:00+00:00"}})
    rel = roda([cartao("c1", "29.1 · algo", "🙋 Espera você"),
                cartao("c2", "99.9 · sem estado", "✅ Concluído nesta semana"),
                cartao("c3", "99.8 · sem estado", "🧭 Próximas")], est)
    assert [(a.cartao, a.tipo) for a in rel.acoes] == [("c1", "listar"), ("c2", "listar")]


def test_rodada_seguinte_nao_acha_nada():
    est = estado(**{"29.1": {"status": "implemented", "proof": "simulated", "quando": "2026-10-05T17:00:00+00:00"}})
    c = cartao("c1", "29.1 · algo", "🧭 Próximas")
    a = roda([c], est).acoes[0]
    c = cartao("c1", "29.1 · algo", "✅ Concluído nesta semana", desc=com_linha("texto", topo_da_linha(a, "06/10/2026 02:00Z")))
    assert roda([c], est).acoes == []


def test_linha_nova_troca_a_antiga_em_vez_de_empilhar():
    a = Acao("c", "n", "mover", "proximas", "concluido", "prova simulada (x), no ar desde o deploy 38", "m")
    um = com_linha("corpo", topo_da_linha(a, "06/10/2026 02:00Z"))
    dois = com_linha(um, topo_da_linha(a, "06/10/2026 03:00Z"))
    assert dois.count(MARCA) == 1 and "03:00Z" in dois and dois.endswith("corpo")
    assert "Próximas (06/10/2026 02:00Z, regra de 06/10):** vai para Concluído. Prova simulada" in um


def test_historico_e_programa_so_relatam():
    est = estado(**{"29.1": {"status": "partial"}, "29.3": {"status": "implemented"}})
    hist = [cartao("h1", "29.1 · parcial no Histórico", "Fase 29"), cartao("h2", "29.3 · ok", "Fase 29"),
            cartao("h3", "99.1 · sem estado", "Fase 29")]
    prog = [cartao("p1", "📏 M8 · aparelhos", "📏 Métricas", desc="**Leitura de 06/10 01:50Z (real):** x"),
            cartao("p2", "📏 M1 · sucesso", "📏 Métricas", desc="**Última leitura registrada: 03/10;** x"),
            cartao("p3", "💰 Gasto", "💰 Custos", desc="**Leitura de 01/10 10:00Z:** velho"),
            cartao("p4", "🎯 O1", "🎯 Objetivos")]
    rel = auditar_historico_e_programa(hist, prog, est, agora=AGORA)
    assert [(a.cartao, a.motivo) for a in rel.acoes] == [
        ("h1", "no Histórico, mas o plano diz partial"), ("h3", "no Histórico, mas sem estado no plano"),
        ("p2", "sem leitura datada"), ("p3", "leitura de 01/10")]
    assert all(a.tipo == "listar" for a in rel.acoes)


def test_item_citado_na_secao_do_deploy_vale_como_implantado():
    texto = ("## 2026-10-06 — Deploy 43 (suíte 43)\n\n- 29.104 e 31.88 entraram.\n\n"
             "## 2026-10-05 — Deploy 39 e rodada\n\n- 29.141 também.\n\n## 2026-10-05 — 29.104 (2º PR)\n\nfora de deploy\n")
    assert ids_citados_por_deploy(texto) == {"29.104": 43, "31.88": 43, "29.141": 39}
    est = estado(**{"29.104": {"status": "implemented", "proof": "simulated", "quando": "2026-10-06T01:30:00+00:00"}})
    c = [cartao("c1", "29.104 · testes do painel", "🧭 Próximas")]
    assert roda(c, est).acoes[0].para == "em_validacao"             # sem a citação, ainda não implantado
    ok = decidir(c, est, agora=AGORA, horas=HORAS, suite_de=lambda p: None, listas_do_historico=HIST,
                 citados={"29.104": 43})
    assert (ok.acoes[0].para, "deploy 43" in ok.acoes[0].linha) == ("concluido", True)


def test_leitura_datada():
    assert leitura_datada("**Leitura de 06/10 01:50Z (real):** x", AGORA) == datetime(2026, 10, 6, 1, 50, tzinfo=timezone.utc)
    assert leitura_datada("**Última leitura registrada: 03/10", AGORA) is None
    assert leitura_datada("**Leitura de 31/02 01:50Z", AGORA) is None

