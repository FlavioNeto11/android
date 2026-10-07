"""28.72: auditoria de coerência dos 3 quadros. Cartões, plano e Trello FICTÍCIOS; nenhum teste fala com o Trello real.

Prova `simulated`: o cliente é um fake em memória que FALHA o teste se receber qualquer coisa que não seja GET.
Rodar: backend/.venv/Scripts/python.exe -m pytest -q .claude/trello/test_auditoria_dos_quadros.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import auditoria_dos_quadros as A  # noqa: E402
from cartoes_de_aparelho import LISTA_CONCLUIDO, LISTA_EM_EXECUCAO, LISTA_EM_VALIDACAO  # noqa: E402
from espelho_do_deploy import ItemDoPlano  # noqa: E402

AGORA = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
L_VALID, L_PROX, L_CONC, L_PROVA = "🧪 Em validação (suíte → deploy)", "Próximas", "✅ Concluído nesta semana", "🧪 Prova 07/10"
L_PERG, L_RESP, L_ESPERA = "❓ Perguntas para você", "✔️ Perguntas respondidas", "🙋 Espera você"
_ID_DA_LISTA = {L_VALID: LISTA_EM_VALIDACAO, L_CONC: LISTA_CONCLUIDO}


def cartao(nome: str, lista: str, desc: str = "", quadro: str = "execucao", id_: str | None = None, id_lista: str | None = None) -> dict:
    return {"id": id_ or f"c{abs(hash((nome, lista, desc, quadro))) % 10**12:012d}", "nome": nome, "lista": lista,
            "id_lista": id_lista or _ID_DA_LISTA.get(lista, "l-" + lista[:6]), "desc": desc, "quadro": quadro}


def plano(*ids: str) -> list[ItemDoPlano]:
    return [ItemDoPlano(i, f"**Título {i}**", "origem", "M") for i in ids]


def sem_decisao(_c: list[dict], _e: dict) -> list:
    raise AssertionError("o Git não devia ser consultado")


def audita(cartoes: list[dict], itens: list | None = None, estado: dict | None = None, decidir=sem_decisao) -> dict:  # noqa: ANN001
    return A.auditar(cartoes, itens_do_plano_=itens if itens is not None else [], estado=estado if estado is not None else {"itens": {}},
                     decidir_fn=decidir)


# ------------------------------------------------------------------------------------------------ 1. duplicados
def test_duplicado_mesmo_id_em_dois_cartoes_de_quadros_diferentes() -> None:
    r = audita([cartao("28.30 · um", L_PROX), cartao("28.30 · um de novo", "Concluído", quadro="historico"),
                cartao("28.31 · outro", L_PROX), cartao("[A] 🙋 28.32 · dono", L_PROX), cartao("#4 · 28.32 · de novo", L_VALID)])
    v = r["verificacoes"]["duplicados"]
    assert v["n"] == 2 and v["exemplos"] == ["28.30 (2 cartões)", "28.32 (2 cartões)"]


def test_duplicado_ignora_a_lista_da_prova_e_cartao_sem_id() -> None:
    r = audita([cartao("28.30 · um", L_PROX), cartao("28.30 · prova", L_PROVA), cartao("Como ler o quadro", "Como ler"),
                cartao("Métrica", "Métricas", quadro="programa"), cartao("Métrica", "Métricas", quadro="programa")])
    assert r["verificacoes"]["duplicados"]["n"] == 0


def test_pergunta_duplicada_no_execucao_conta_mas_decisao_do_programa_nao() -> None:
    r = audita([cartao("P-026 · posso?", L_PERG), cartao("P-026 · posso? de novo", L_RESP, "**RESPOSTA DO DONO (x):** sim"),
                cartao("⚖️ P-027 · posso?", "Decisões do dono", quadro="programa"), cartao("P-027 · posso?", L_RESP, "**Leitura (06/10 14:19Z):** x")])
    assert r["verificacoes"]["duplicados"]["exemplos"] == ["P-026 (2 cartões)"]


def test_no_maximo_5_exemplos_mas_conta_todos() -> None:
    cs = [c for n in range(1, 9) for c in (cartao(f"30.{n} · a", L_PROX), cartao(f"30.{n} · b", L_VALID))]
    v = audita(cs)["verificacoes"]["duplicados"]
    assert v["n"] == 8 and len(v["exemplos"]) == 5


# ------------------------------------------------------------------------------------------------ 2. sem cartão
def test_item_do_plano_sem_cartao_em_nenhum_quadro() -> None:
    cs = [cartao("28.30 · um", L_PROX), cartao("28.31 · dois", "Concluído", quadro="historico"), cartao("🙋 28.32 · tres", L_PROX),
          cartao("28.33 · da prova", L_PROVA)]
    r = audita(cs, plano("28.30", "28.31", "28.32", "28.33", "28.34", "T.2", "28.34"))
    assert r["verificacoes"]["sem_cartao"] == {"n": 2, "exemplos": ["28.34", "T.2"]}  # cada ID uma vez; cartão da prova conta


def test_plano_ilegivel_deixa_2_e_5_como_nao_lidas_e_o_resto_segue() -> None:
    r = A.auditar([cartao("28.30 · a", L_PROX), cartao("28.30 · b", L_VALID)], itens_do_plano_=None, estado=None, decidir_fn=sem_decisao)
    assert r["ok"] and r["nao_lidas"] == ["sem_cartao", "concluido_fora"]
    assert r["verificacoes"]["sem_cartao"] is None and r["verificacoes"]["duplicados"]["n"] == 1 and r["total"] == 1


# ------------------------------------------------------------------------------------------------ 3. perguntas
def test_pergunta_respondida_ainda_em_perguntas_para_voce_e_aberta_em_respondidas() -> None:
    cs = [cartao("P-001 · a", L_PERG, "**RESPOSTA DO DONO (06/10 19:38Z, entrada 1):** \"sim\"."),
          cartao("P-002 · b", L_PERG, "**Resposta do dono (05/10 16:06Z, comentário):** sim"),
          cartao("P-003 · c", L_PERG, "Pergunta ainda sem resposta."),
          cartao("P-004 · d", L_RESP, "Sem nada no topo."),
          cartao("P-005 · e", L_RESP, "**Leitura da orquestradora (06/10 14:19Z):** fechada."),
          cartao("P-006 · f", L_RESP, "**RESPOSTA DO DONO (06/10 19:38Z):** sim"),
          cartao("Como responder aqui", L_PERG, "instruções"), cartao("P-007 · g", L_PROVA, "")]
    v = audita(cs)["verificacoes"]["pergunta_incoerente"]
    assert v["n"] == 3
    assert v["exemplos"] == ["P-001 (respondida, ainda em Perguntas para você)", "P-002 (respondida, ainda em Perguntas para você)",
                             "P-004 (em Perguntas respondidas sem a resposta registrada)"]


# ------------------------------------------------------------------------------------------------ 4. aparelhos
def desc_aparelho(rotulo: str, estado: str) -> str:
    return f"Aparelho: {rotulo}\nEstado da sessão: {estado}\nMotivo: x\n"


def test_aparelho_duplicado_fora_das_listas_e_em_lista_do_estado_errado() -> None:
    cs = [cartao("android-01 · sessão pronta", L_VALID, desc_aparelho("android-01", "pronta"), id_lista=LISTA_EM_EXECUCAO),
          cartao("android-02 · sessão pronta", L_VALID, desc_aparelho("android-02", "pronta"), id_lista=LISTA_EM_EXECUCAO),
          cartao("android-02 · de novo", L_VALID, desc_aparelho("android-02", "pronta"), id_lista=LISTA_EM_EXECUCAO),
          cartao("android-03 · sessão pronta", L_PROX, desc_aparelho("android-03", "pronta"), id_lista="l-proximas"),
          cartao("android-04 · sessão vencida", L_CONC, desc_aparelho("android-04", "vencida"), id_lista=LISTA_CONCLUIDO),
          cartao("android-05 · sem conta", L_CONC, desc_aparelho("android-05", "sem conta real"), id_lista=LISTA_CONCLUIDO),
          cartao("android-06 · prova", L_PROVA, desc_aparelho("android-06", "pronta"), id_lista="l-prova")]
    v = audita(cs)["verificacoes"]["aparelho_incoerente"]
    assert v["exemplos"] == ["android-02 (duplicado)", "android-03 (fora das listas de aparelho)",
                             "android-04 (lista diferente da do estado escrito)"]


def test_cartao_de_item_do_plano_com_linha_aparelho_nunca_e_de_aparelho_e_rotulo_estranho_nao_vaza() -> None:
    cs = [cartao("28.69 · cartão de aparelho", L_PROX, "Aparelho: android-09\n"),
          cartao("x", L_PROX, desc_aparelho("zoraide.benevides@exemplo.com.br", "pronta"), id_lista="l-proximas")]
    v = audita(cs)["verificacoes"]["aparelho_incoerente"]
    assert v["exemplos"] == ["aparelho de rótulo fora do formato (fora das listas de aparelho)"]
    assert "zoraide" not in json.dumps(v)


# ------------------------------------------------------------------------------------------------ 5. concluídos fora
def _estado() -> dict:
    return {"itens": {"30.1": {"status": "implemented", "proof": "simulated", "quando": "2026-10-06T10:00:00Z", "evidence": "t.py::t"},
                      "30.2": {"status": "partial", "proof": "simulated", "quando": "2026-10-06T10:00:00Z"},
                      "30.3": {"status": "implemented", "proof": "simulated", "quando": "2026-10-06T22:00:00Z"}}}  # 30.3: depois do deploy 57


def _decidir_com_deploy_57(candidatos: list[dict], estado: dict) -> list:
    """O `reconciliar.decidir` de verdade, com as fontes de Git trocadas por valores fixos."""
    return A.R.decidir(candidatos, estado, agora=AGORA, horas={57: "2026-10-06T20:00:00"}, suite_de=lambda pid: 57 if pid == "30.1" else None,
                       listas_do_historico={}, redigir=lambda t: t).acoes


def test_implementado_e_implantado_em_lista_de_trabalho_e_reportado_e_nada_e_movido() -> None:
    cs = [cartao("30.1 · pronto", L_VALID), cartao("30.2 · parcial", L_VALID), cartao("30.3 · sem deploy", L_VALID),
          cartao("30.1 · outro", L_CONC)]
    r = audita(cs, estado=_estado(), decidir=_decidir_com_deploy_57)
    assert r["verificacoes"]["concluido_fora"] == {"n": 1, "exemplos": ["30.1"]}


def test_sem_candidato_o_git_nem_e_consultado() -> None:
    cs = [cartao("30.1 · ja em concluido", L_CONC), cartao("30.2 · parcial", L_VALID), cartao("30.1 · da prova", L_PROVA),
          cartao("30.1 · no historico", "Fase 30", quadro="historico")]
    assert audita(cs, estado=_estado())["verificacoes"]["concluido_fora"]["n"] == 0  # `sem_decisao` levantaria


# ------------------------------------------------------------------------------------------------ 6. sem lista
def test_cartao_sem_lista_conhecida() -> None:
    cs = [cartao("30.1 · a", ""), cartao("Cartão solto", ""), cartao("P-009 · pergunta", ""),
          cartao("30.2 · b", "Lista que ninguém conhece"), cartao("30.9 · da prova sem lista", "", ""),
          cartao("Métrica", "", quadro="programa"), cartao("30.4 · ok", L_PROX), cartao("Métrica", "Métricas", quadro="programa")]
    v = audita(cs)["verificacoes"]["sem_lista"]
    assert v["n"] == 6 and v["exemplos"][:3] == ["30.1", v["exemplos"][1], "P-009"]
    assert v["exemplos"][1].startswith("sem ID (cartão …")  # só os 6 últimos caracteres do id, nunca o nome


# ------------------------------------------------------------------------------------------------ rede (fake só-GET)
class FakeTrello:
    def __init__(self, falha: Exception | None = None) -> None:
        self.chamadas: list[tuple[str, str]] = []
        self.falha = falha

    async def _pedir(self, metodo: str, caminho: str, *, params: dict | None = None, corpo: dict | None = None):  # noqa: ANN202
        self.chamadas.append((metodo, caminho))
        assert metodo == "GET" and corpo is None, "a auditoria é só leitura"
        if self.falha:
            raise self.falha
        if caminho.endswith("/lists"):
            return [{"id": "lp", "name": L_PROX}, {"id": "lh", "name": "Fase 30"}]
        return [{"id": "c1", "name": "30.1 · segredo de nome", "idList": "lp", "desc": "texto da descrição"},
                {"id": "c2", "name": "30.1 · repetido", "idList": "lh", "desc": ""}]


def _raiz_com_plano(tmp_path: Path) -> Path:
    (tmp_path / "docs").mkdir()
    (tmp_path / ".claude" / "plano-100").mkdir(parents=True)
    (tmp_path / "docs" / "plano-100.md").write_text("| 30.1 | **A** — a | origem | M |\n| 30.5 | **B** — b | origem | M |\n", encoding="utf-8")
    (tmp_path / ".claude" / "plano-100" / "estado.json").write_text(json.dumps({"itens": {}}), encoding="utf-8")
    return tmp_path


def test_auditar_tudo_le_so_por_get_nos_tres_quadros_e_devolve_o_resultado(tmp_path: Path) -> None:
    fake = FakeTrello()
    r = asyncio.run(A.auditar_tudo(fake, _raiz_com_plano(tmp_path), AGORA))
    assert r["ok"] and r["quadros"] == {"execucao": 2, "programa": 2, "historico": 2}
    assert len(fake.chamadas) == 6 and {m for m, _ in fake.chamadas} == {"GET"}
    assert r["verificacoes"]["sem_cartao"] == {"n": 1, "exemplos": ["30.5"]}
    assert r["verificacoes"]["duplicados"]["exemplos"] == ["30.1 (6 cartões)"]
    saida = json.dumps(r, ensure_ascii=False)
    assert "segredo de nome" not in saida and "texto da descrição" not in saida


def test_o_guarda_recusa_escrita_mesmo_que_o_codigo_tente() -> None:
    async def tenta() -> None:
        await A.SoLeitura(FakeTrello())._pedir("PUT", "/1/cards/x", corpo={"desc": "y"})

    with pytest.raises(PermissionError):
        asyncio.run(tenta())


def test_leitura_que_falha_vira_ok_false_com_so_o_tipo_do_erro(tmp_path: Path) -> None:
    r = asyncio.run(A.auditar_tudo(FakeTrello(RuntimeError("401 key=ABCDEF token=XYZ")), _raiz_com_plano(tmp_path), AGORA))
    assert r == {"ok": False, "falha": "RuntimeError"}


def test_plano_ausente_nao_derruba_a_leitura(tmp_path: Path) -> None:
    r = asyncio.run(A.auditar_tudo(FakeTrello(), tmp_path, AGORA))
    assert r["ok"] and r["nao_lidas"] == ["sem_cartao", "concluido_fora"]


# ------------------------------------------------------------------------------------------------ saída
def _res(**n: int) -> dict:
    v = {k: {"n": n.get(k, 0), "exemplos": ["28.30"] * min(n.get(k, 0), 5)} for k in A.VERIFICACOES}
    return {"ok": True, "quadros": {"execucao": 3, "programa": 2, "historico": 1}, "verificacoes": v, "nao_lidas": [],
            "total": sum(n.values())}


def test_secao_do_resumo_cobre_os_quatro_casos() -> None:
    pref = "• <b>Coerência dos quadros:</b> "
    assert A.secao_do_resumo(_res()) == pref + "nada fora do lugar."
    assert A.secao_do_resumo(_res(duplicados=2, sem_cartao=1)) == pref + "3 achados (duplicados 2, sem cartão 1)."
    assert A.secao_do_resumo(_res(sem_lista=1)) == pref + "1 achado (sem lista conhecida 1)."
    for ruim in (None, {}, {"ok": False, "falha": "ReadError"}, "texto", {"ok": True}, {"ok": True, "verificacoes": []}):
        linha = A.secao_do_resumo(ruim)
        assert linha == pref + "não consegui ler o Trello." and "nada fora do lugar" not in linha


def test_secao_com_verificacao_nao_lida_nunca_diz_nada_fora_do_lugar() -> None:
    sem_plano = _res()
    sem_plano["verificacoes"]["sem_cartao"] = None
    assert "nada fora do lugar" not in A.secao_do_resumo(sem_plano) and "não consegui ler o plano (sem cartão)" in A.secao_do_resumo(sem_plano)
    com_achado = _res(duplicados=1)
    com_achado["verificacoes"]["concluido_fora"] = None
    assert A.secao_do_resumo(com_achado).endswith("1 achado (duplicados 1); sem leitura de concluídos fora de Concluído.")


def test_secao_e_uma_linha_so_e_sem_contato() -> None:
    linha = A.secao_do_resumo(_res(duplicados=1))
    assert "\n" not in linha and linha.count("<b>") == linha.count("</b>") == 1
    suja = _res()
    suja["verificacoes"]["duplicados"] = {"n": "3 a@exemplo.com <i>", "exemplos": []}
    assert "@" not in A.secao_do_resumo(suja) and "<i>" not in A.secao_do_resumo(suja)


def test_exemplos_passam_pelo_filtro_de_contato() -> None:
    r = A._resultado(["28.30 a@exemplo.com 11 98765-4321"])
    assert "@" not in json.dumps(r) and "98765" not in json.dumps(r)


def test_console_mostra_contagem_e_exemplos_e_json_sai_integro(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                              capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(A, "novo_cliente", FakeTrello)
    raiz = _raiz_com_plano(tmp_path)
    assert A.main(["--raiz", str(raiz)]) == 0
    texto = capsys.readouterr().out
    assert "só leitura" in texto and "duplicados: 1" in texto and "30.1 (6 cartões)" in texto and "segredo de nome" not in texto
    assert A.main(["--json", "--raiz", str(raiz)]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    monkeypatch.setattr(A, "novo_cliente", lambda: FakeTrello(RuntimeError("rede")))
    assert A.main(["--raiz", str(raiz)]) == 1
    assert "não consegui ler os quadros" in capsys.readouterr().out
