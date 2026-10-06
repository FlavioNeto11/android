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
    SEPARADOR,
    Acao,
    auditar_historico_e_programa,
    com_linha,
    decidir,
    deploy_do_item,
    deploy_pela_evidencia,
    deploys_do_changelog,
    deploys_por_commit,
    frase_da_evidencia,
    frase_do_cartao,
    linha_do_parcial,
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
    assert id_do_item("T.2 · Testes: relógio injetável") == "T.2"
    assert id_do_item("[A] T.14 · algo") == "T.14"
    assert id_do_item("T.2.1 · algo") is None
    assert id_do_item("Tabela 1.2") is None
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
    assert a.linha == "item parcial no plano, com prova simulada (ver o item 29.1 no estado do plano); falta: falta a prova real no aparelho"


def test_parcial_em_execucao_so_ganha_a_linha_uma_vez():
    est = estado(**{"29.1": {"status": "partial"}})
    a = roda([cartao("c1", "29.1 · algo", "🛠 Em execução")], est).acoes[0]
    assert a.tipo == "marcar"
    pronto = cartao("c1", "29.1 · algo", "🛠 Em execução",
                    desc=com_linha("corpo", topo_da_linha(a, "06/10/2026 02:00Z")))
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


def test_item_transversal_de_semana_anterior_fica_em_concluido_sem_lista_de_fase():
    est = estado(**{"T.3": {"status": "implemented", "proof": "simulated", "quando": "2026-10-02T10:00:00+00:00"}})
    a = roda([cartao("c1", "T.3 · algo", "🧭 Próximas")], est).acoes[0]
    assert (a.tipo, a.para, a.lista_do_historico) == ("mover", "concluido", None)


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



def test_raiz_trocada_depois_do_import_vale_para_as_consultas_ao_git(monkeypatch, tmp_path):
    import reconciliar

    chamadas = []

    class R:
        stdout = ""

    monkeypatch.setattr(reconciliar, "RAIZ", tmp_path)
    monkeypatch.setattr(reconciliar.subprocess, "run", lambda cmd, **kw: chamadas.append(cmd) or R())
    (tmp_path / "CHANGELOG.md").write_text("## 2026-10-06 — Deploy 44 (x)\n", encoding="utf-8")
    assert reconciliar.suite_do_commit("29.1") is None
    assert reconciliar.horas_dos_deploys() == {}
    assert all(str(tmp_path) in c for c in chamadas) and len(chamadas) == 2


def test_prova_real_nova_troca_a_linha_simulada_do_cartao_concluido():
    est = estado(**{"29.1": {"status": "implemented", "proof": "real", "quando": "2026-10-05T17:00:00+00:00",
                             "evidence": "validado em 06/10 na execução r-20261006012340-abc"}})
    velha = MARCA + "estado do plano (06/10/2026 02:00Z):** fica em Concluído. Prova simulada (a.py::t), no ar desde o deploy 38." + SEPARADOR + "corpo"
    a = roda([cartao("c1", "29.1 · algo", "✅ Concluído nesta semana", desc=velha)], est).acoes
    assert [(x.tipo, x.motivo) for x in a] == [("marcar", "o plano ganhou prova real")]
    assert a[0].linha.startswith("prova real (06/10, r-20261006012340-abc")
    nova = com_linha("corpo", topo_da_linha(a[0], "06/10/2026 03:30Z"))
    assert roda([cartao("c1", "29.1 · algo", "✅ Concluído nesta semana", desc=nova)], est).acoes == []


PARCIAL_REAL = {"status": "partial", "proof": "real", "quando": "2026-10-06T03:33:00+00:00",
                "evidence": "06/10/2026 provado na execução r-20261006012340-abc. Faltam F4 e F5 (tela). A em ponta própria.",
                "blocker": "F4 e F5 ainda não entregues"}


def test_frase_do_que_falta_vem_da_evidencia():
    assert frase_da_evidencia(PARCIAL_REAL, lambda t: t) == "faltam F4 e F5 (tela)"
    assert frase_da_evidencia({"evidence": "tudo provado"}, lambda t: t) is None
    assert frase_da_evidencia({"evidence": "só uma parte; falta provar o resto. Outra frase."}, lambda t: t) == "falta provar o resto"


def test_linha_do_parcial_tem_o_nivel_da_prova_e_a_frase_da_evidencia():
    linha = linha_do_parcial("31.111", PARCIAL_REAL, cartao("c", "31.111 · x", "🧪 Em validação"), lambda t: t)
    assert linha == ("item parcial no plano, com prova real (06/10/2026, r-20261006012340-abc; evidência no estado do plano, item 31.111); "
                     "faltam F4 e F5 (tela)")


def test_sem_frase_na_evidencia_mantem_a_do_cartao_e_so_troca_a_prova():
    item = {"status": "partial", "proof": "real", "quando": "2026-10-06T03:33:00+00:00", "evidence": "06/10/2026 provado em r-20261006012340-abc"}
    velha = MARCA + "estado do plano (06/10/2026 01:00Z):** fica em Em validação. Item parcial no plano; falta: a decisão do dono sobre os ids antigos." + SEPARADOR + "corpo"
    c = cartao("c1", "29.1 · algo", "🧪 Em validação", desc=velha)
    assert frase_do_cartao(c) == "falta: a decisão do dono sobre os ids antigos"
    a = roda([c], estado(**{"29.1": item})).acoes
    assert [(x.tipo, x.motivo) for x in a] == [("marcar", "a linha do parcial mudou (prova ou o que falta)")]
    assert a[0].linha.endswith("); falta: a decisão do dono sobre os ids antigos")
    assert "prova real (06/10/2026, r-20261006012340-abc" in a[0].linha
    nova = cartao("c1", "29.1 · algo", "🧪 Em validação", desc=com_linha("corpo", topo_da_linha(a[0], "06/10/2026 03:40Z")))
    assert roda([nova], estado(**{"29.1": item})).acoes == []          # idempotente
    assert frase_do_cartao(nova) == "falta: a decisão do dono sobre os ids antigos"


def test_sem_frase_em_lugar_nenhum_cai_no_bloqueio_do_estado():
    item = {"status": "partial", "proof": "simulated", "blocker": "espera o 31.113"}
    linha = linha_do_parcial("29.1", item, cartao("c", "29.1 · x", "🧪 Em validação"), lambda t: t)
    assert linha.endswith("); falta: espera o 31.113") and "com prova simulada" in linha


def test_cartao_parcial_com_prova_simulada_antiga_ganha_a_real():
    simulada = {**PARCIAL_REAL, "proof": "simulated", "evidence": "backend/tests/test_x.py::test_a. Faltam F4 e F5 (tela)."}
    c0 = cartao("c1", "31.111 · x", "🧪 Em validação")
    a0 = roda([c0], estado(**{"31.111": simulada})).acoes[0]
    c1 = cartao("c1", "31.111 · x", "🧪 Em validação", desc=com_linha("corpo", topo_da_linha(a0, "06/10/2026 03:00Z")))
    assert roda([c1], estado(**{"31.111": simulada})).acoes == []
    a1 = roda([c1], estado(**{"31.111": PARCIAL_REAL})).acoes
    assert [x.tipo for x in a1] == ["marcar"] and "com prova real" in a1[0].linha and a1[0].linha.endswith("faltam F4 e F5 (tela)")


def test_frase_que_falta_aguenta_ponto_dentro_de_nome_de_arquivo():
    ev = "Real (06/10). Falta: .claude/trello/mapa.json fora do Git como estado por instalação (corte 46). Outra frase."
    assert frase_da_evidencia({"evidence": ev}, lambda t: t) == "falta: .claude/trello/mapa.json fora do Git como estado por instalação (corte 46)"


CHANGELOG_45 = ("## 2026-10-06 — Deploy 45 (suíte 45)\n\n- **Implantado** às 03:13Z: central em `7154d7cf`, migração 119.\n\n"
                "## 2026-10-06 — Deploy 44 (suíte 44)\n\n- **Implantado** às 02:15Z: central em `33c7d5ab`, migrações 117 e 118.\n")


def test_deploy_pelo_commit_do_central_citado_na_evidencia():
    pc = deploys_por_commit(CHANGELOG_45)
    assert pc == {"7154d7cf": 45, "33c7d5ab": 44}
    item = {"status": "implemented", "proof": "real", "quando": "2026-10-06T03:36:17+00:00",
            "evidence": "Real, 06/10/2026, central 7154d7cf (contém f7153ddf), android-04 sem conta real."}
    assert deploy_pela_evidencia(item, pc) == 45
    assert deploy_pela_evidencia({"evidence": "sem commit"}, pc) is None
    c = [cartao("c1", "29.5 · algo", "🧭 Próximas")]
    sem = roda(c, estado(**{"29.5": item})).acoes[0]
    assert sem.para == "em_validacao"                                   # classificado depois do último deploy registrado
    com = decidir(c, estado(**{"29.5": item}), agora=AGORA, horas=HORAS, suite_de=lambda p: None,
                  listas_do_historico=HIST, por_commit=pc).acoes[0]
    assert (com.para, "deploy 45" in com.linha) == ("concluido", True)


def test_deploy_pelo_numero_citado_na_evidencia_so_vale_ate_o_ultimo_deploy():
    item = {"evidence": "04/10/2026, deploy 32 (2c47b9fa), r-20261004232524-2e0775"}
    assert deploy_pela_evidencia(item, {}, 45) == 32
    assert deploy_pela_evidencia({"evidence": "vai no deploy 46"}, {}, 45) is None
    assert deploy_pela_evidencia({"evidence": "deploy 44 e depois o deploy 45"}, {}, 45) == 44
    real = {"status": "implemented", "proof": "real", "quando": "2026-10-06T03:41:14+00:00", "evidence": "provado no deploy 32 (2c47b9fa)"}
    c = [cartao("c1", "29.6 · algo", "🧪 Em validação")]
    a = decidir(c, estado(**{"29.6": real}), agora=AGORA, horas=HORAS, suite_de=lambda p: None, listas_do_historico=HIST).acoes[0]
    assert (a.para, "deploy 32" in a.linha) == ("concluido", True)


def test_item_classificado_junto_do_registro_do_deploy_vale_o_deploy_do_git():
    item = {"status": "implemented", "proof": "real", "quando": "2026-10-05T17:00:00+00:00",
            "evidence": "provado em 06/10 no deploy 43"}
    c = [cartao("c1", "29.7 · algo", "🧭 Próximas")]
    # a hora diz 39, mas o git diz 38 e o git manda; com o commit de suíte, é a suíte que manda
    a = decidir(c, estado(**{"29.7": item}), agora=AGORA, horas=HORAS, suite_de=lambda p: None,
                listas_do_historico=HIST, deploy_git=lambda p: 38).acoes[0]
    assert "no ar desde o deploy 38" in a.linha
    a = decidir(c, estado(**{"29.7": item}), agora=AGORA, horas=HORAS, suite_de=lambda p: 39,
                listas_do_historico=HIST, deploy_git=lambda p: 38).acoes[0]
    assert "no ar desde o deploy 39" in a.linha
    mesmo_da_ultima = {**item, "quando": "2026-10-06T00:55:00+00:00"}  # depois do 43: ambíguo
    a = decidir(c, estado(**{"29.7": mesmo_da_ultima}), agora=AGORA, horas=HORAS, suite_de=lambda p: None,
                listas_do_historico=HIST, deploy_git=lambda p: 39).acoes[0]
    assert "no ar desde o deploy 39" in a.linha


def test_linha_do_concluido_com_deploy_errado_e_refeita():
    item = {"status": "implemented", "proof": "simulated", "quando": "2026-10-06T00:55:00+00:00", "evidence": "a.py::t"}
    errada = Acao("c1", "n", "mover", "proximas", "concluido", "prova simulada (a.py::t), no ar desde o deploy 43", "m")
    c = cartao("c1", "29.7 · algo", "✅ Concluído nesta semana", desc=com_linha("corpo", topo_da_linha(errada, "06/10/2026 02:00Z")))
    a = decidir([c], estado(**{"29.7": item}), agora=AGORA, horas=HORAS, suite_de=lambda p: None,
                listas_do_historico=HIST, deploy_git=lambda p: 39).acoes
    assert [(x.tipo, x.motivo) for x in a] == [("marcar", "o deploy do item mudou")]
    assert "no ar desde o deploy 39" in a[0].linha


def test_deploy_do_git_no_primeiro_deploy_conhecido_diz_neste_ou_num_anterior():
    item = {"status": "implemented", "proof": "simulated", "quando": "2026-10-06T00:55:00+00:00", "evidence": "a.py::t"}
    c = [cartao("c1", "29.7 · algo", "🧭 Próximas")]
    a = decidir(c, estado(**{"29.7": item}), agora=AGORA, horas=HORAS, suite_de=lambda p: None,
                listas_do_historico=HIST, deploy_git=lambda p: -38).acoes[0]
    assert "no ar desde o deploy 38 ou um anterior" in a.linha


def test_item_que_fica_para_outro_corte_nao_conta_como_citado_no_deploy():
    texto = ("## 2026-10-06 — Deploy 47 (suíte 47)\n\n- Itens: 15.15 F7, 31.113 F3, 31.91 T1 na tela, 31.116 e 31.117 ficam para o corte 48.\n"
             "- Também 29.200, 29.201, segue para o corte 49.\n\n"
             "## 2026-10-06 — Deploy 46 (suíte 46)\n\n- 31.114 e 31.91.\n")
    achados = ids_citados_por_deploy(texto)
    assert "31.116" not in achados and "31.117" not in achados
    assert achados["15.15"] == 47 and achados["31.113"] == 47 and achados["31.91"] == 46 and achados["31.114"] == 46
