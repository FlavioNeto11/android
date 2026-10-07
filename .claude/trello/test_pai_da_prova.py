"""28.70: o bloco dos critérios da prova na descrição do cartão-pai, lido do relatório do servidor.

Prova `simulated`: relatório, cliente do Trello e `urllib` FALSOS, dados inventados. Nada aqui fala com a central nem com o
Trello. O contrato é o final do adendo v1.111 (19 critérios).
Rodar da raiz: `backend/.venv/Scripts/python.exe -m pytest -q .claude/trello/test_pai_da_prova.py`.
"""
from __future__ import annotations

import copy
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pai_da_prova as p  # noqa: E402
import redacao  # noqa: E402

#: nomes FICTÍCIOS, fora da lista de reserva da redação: só valem depois de `recarregar` com um banco de mentira
NOME = "Zoraide Benevides"
HANDLE = "zoraide.benevides.ig"

IDS = ["1", "2", "2b", "3", "3b", "4", "5", "6", "7", "8", "9", "10", "11", "11b", "12", "13", "14", "15", "16"]
PROVADOS = {"3", "9", "11", "14"}


def _relatorio(**kw: object) -> dict:
    criterios = [{"id": i, "nome": f"Critério {i}", "estado": "provado_real" if i in PROVADOS else "implementado",
                  "nesta_operacao": "sim" if i in PROVADOS else "nao_medido",
                  "evidencia": "operação op-20261006130000-abc123" if i in PROVADOS else None} for i in IDS]
    base: dict = {
        "gerado_em": "2026-10-07T15:04:05.123Z", "ambiente": "real",
        "operacao": {"id": "op-20261006130000-abc123", "comando": "faça algo", "status": "concluida_com_bloqueios"},
        "capacidade": {"solicitados": 30, "contas_existentes": 24, "sessoes_validas": 18, "contas_disponiveis": 15,
                       "concluidas": 12, "bloqueadas": 14, "em_curso": 0, "motivos": [{"motivo": "sem sessão", "n": 9}]},
        "identidades": {"solicitadas": 30, "executam_hoje": 15, "deficit": 15,
                        "nao_executam": [{"motivo": "sem conta no app", "n": 6}, {"motivo": "sem sessão válida", "n": 6}]},
        "custo": {"pesquisa_usd": 0.1, "alvos_usd": 0.19, "total_usd": 0.289, "teto_usd": 5.0, "por_peca_usd": 0.02},
        "criterios": criterios,
    }
    base.update(kw)
    return base


def _bloco(dados: dict) -> str:
    return p.montar_bloco(p.ler_relatorio(dados))


def _linhas(bloco: str) -> list[str]:
    return [x for x in bloco.splitlines() if x.startswith("- ")]


@pytest.fixture
def nomes_de_mentira(tmp_path: Path):
    """Um banco de personas inventado; os padrões da redação voltam ao que eram depois do teste."""
    (tmp_path / "data").mkdir()
    con = sqlite3.connect(tmp_path / "data" / "poc.sqlite3")
    con.execute("create table personas (name text)")
    con.execute("create table profile_accounts (handle text)")
    con.execute("insert into personas values (?)", (NOME,))
    con.execute("insert into profile_accounts values (?)", (f"@{HANDLE}",))
    con.commit()
    con.close()
    antes = (redacao._INTEIROS, redacao._PARTES, redacao._PERSONA, redacao._PERSONA_PARTE)  # noqa: SLF001
    assert redacao.recarregar(tmp_path)
    yield
    redacao._INTEIROS, redacao._PARTES, redacao._PERSONA, redacao._PERSONA_PARTE = antes  # noqa: SLF001


# ----------------------------------------------------------------------------------------------------- o bloco
def test_o_bloco_traz_os_19_criterios_e_o_cabecalho() -> None:
    bloco = _bloco(_relatorio())
    assert bloco.startswith(p.INICIO + "\n") and bloco.endswith("\n" + p.FIM)
    linhas = _linhas(bloco)
    assert len(linhas) == 19
    assert [x.split()[2] for x in linhas] == IDS
    assert "Relatório gerado em 2026-10-07 15:04Z · operação abc123 · status concluída com bloqueios · ambiente real" in bloco
    assert "solicitados 30 · contas existentes 24 · sessões válidas 18 · contas disponíveis 15" in bloco
    assert "Identidades: 30 pedidas · 15 executam hoje · déficit 15 (sem conta no app 6, sem sessão válida 6)" in bloco
    assert "Custo: US$ 0,289 (teto US$ 5)" in bloco
    assert "Atenção" not in bloco


def test_icones_e_estados_por_criterio() -> None:
    d = _relatorio()
    d["criterios"][0].update(estado="provado_real", nesta_operacao="sim", evidencia="operação op-1-abcd")
    d["criterios"][1].update(estado="testado_em_simulacao", nesta_operacao="nao")
    d["criterios"][2].update(estado="implementado")
    d["criterios"][3].update(estado="bloqueado")
    d["criterios"][4].update(estado="nao_implementado")
    linhas = _linhas(_bloco(d))
    assert linhas[0].startswith("- ✅ 1 Critério 1 — provado real (nesta operação: sim; com evidência)")
    assert linhas[1].startswith("- ⚠️ 2 ") and "testado em simulação (nesta operação: não;" in linhas[1]
    assert linhas[2].startswith("- ⚠️ 2b ") and "— implementado" in linhas[2]
    assert linhas[3].startswith("- ⬜ 3 ") and "— bloqueado" in linhas[3]
    assert linhas[4].startswith("- ⬜ 3b ") and "— não implementado" in linhas[4]


def test_nao_medido_e_ausente_nunca_viram_sim_nem_zero() -> None:
    d = _relatorio(capacidade={"solicitados": 30, "concluidas": None, "bloqueadas": "nao_medido", "em_curso": 0},
                   identidades={"solicitadas": 30}, custo={"total_usd": None}, ambiente="qualquer coisa")
    d["gerado_em"] = None
    d["operacao"]["status"] = None
    d["criterios"][0].pop("nesta_operacao")
    d["criterios"][1]["nesta_operacao"] = "talvez"
    d["criterios"][2]["nesta_operacao"] = "nao_medido"
    d["criterios"][3]["nesta_operacao"] = True
    d["criterios"][4].pop("estado")
    d["criterios"][5]["estado"] = "inventado"
    bloco = _bloco(d)
    assert "hora não informada" in bloco and "status não medido" in bloco and "ambiente não medido" in bloco
    assert "contas existentes não medido" in bloco and "concluídas não medido" in bloco and "bloqueadas não medido" in bloco
    assert "em curso 0" in bloco and "solicitados 30" in bloco
    assert "30 pedidas · não medido executam hoje · déficit não medido" in bloco
    assert "Custo: não medido (teto não medido)" in bloco
    for linha in _linhas(bloco)[:4]:
        assert "(nesta operação: não medido;" in linha
    assert "nesta operação: sim" not in "".join(_linhas(bloco)[:4])
    assert "estado não informado" in _linhas(bloco)[4] and "estado não informado" in _linhas(bloco)[5]
    assert _linhas(bloco)[4].startswith("- ⬜")


def test_criterio_sem_id_ou_nome_e_ignorado_e_avisado_e_contagem_diferente_avisa() -> None:
    d = _relatorio()
    d["criterios"][3] = {"id": "3", "nome": ""}
    d["criterios"][4] = "lixo"
    bloco = _bloco(d)
    assert len(_linhas(bloco)) == 17
    assert "Atenção: esperados 19 critérios, vieram 17; 2 ilegível(is) ignorado(s)." in bloco
    assert "Atenção: esperados 19 critérios, vieram 3." in _bloco(_relatorio(criterios=_relatorio()["criterios"][:3]))


def test_o_bloco_e_funcao_so_do_relatorio_e_a_hora_e_a_do_relatorio_em_utc() -> None:
    d = _relatorio(gerado_em="2026-10-07T12:04:05-03:00")
    assert _bloco(d) == _bloco(copy.deepcopy(d))
    assert "gerado em 2026-10-07 15:04Z" in _bloco(d)
    assert "gerado em 2026-10-07 15:04Z" in _bloco(_relatorio(gerado_em="2026-10-07T15:04:00"))


def test_evidencia_em_texto_livre_nao_vai_so_com_ou_sem(nomes_de_mentira: None) -> None:
    d = _relatorio()
    d["criterios"][0].update(evidencia=f"o comentário de @{HANDLE}: ligue 11 98765-4321, x@exemplo.com https://exemplo.com/a",
                             nesta_operacao="sim")
    d["criterios"][1].update(evidencia="   ", nesta_operacao="sim")
    bloco = _bloco(d)
    assert "com evidência" in _linhas(bloco)[0] and "sem evidência" in _linhas(bloco)[1]
    for proibido in (HANDLE, "98765", "exemplo.com", "comentário de"):
        assert proibido not in bloco


def test_nome_e_handle_de_persona_ficticios_saem_do_bloco(nomes_de_mentira: None) -> None:
    d = _relatorio(operacao={"id": "op-20261006130000-abc123", "status": f"parou com {NOME}"})
    d["criterios"][0]["nome"] = f"Falar com {NOME.upper()} e @{HANDLE} por a@b.com em 10.0.0.7 ou +55 (11) 98765-4321"
    bloco = _bloco(d)
    for proibido in (NOME, NOME.upper(), "Zoraide", "Benevides", HANDLE, "a@b.com", "10.0.0.7", "98765"):
        assert proibido not in bloco
    assert "[persona]" in bloco and "[e-mail]" in bloco and "[ip]" in bloco


def test_o_id_da_operacao_sai_curto() -> None:
    assert "operação abc123 " in _bloco(_relatorio())
    d = _relatorio(operacao={"id": "id-sem-formato-conhecido-aaaa-bbbbbbbbbbbbbbbbbbbbbbbb", "status": "em_curso"})
    assert "operação id-sem-f " in _bloco(d) and "status em curso" in _bloco(d)


# ------------------------------------------------------------------------------------------------ recusa e leitura
@pytest.mark.parametrize("dados", [
    None, [], "x", {}, {"criterios": []}, {"operacao": {"id": "op-1-abcd"}},                    # sem operação ou sem lista
    {"operacao": {"id": "op-1-abcd"}, "criterios": None}, {"operacao": {"id": "op-1-abcd"}, "criterios": "x"},
    {"operacao": {"id": "op-1-abcd"}, "criterios": []}, {"operacao": {"id": "op-1-abcd"}, "criterios": [{"id": "1"}]},
])
def test_relatorio_sem_criterios_ou_sem_operacao_e_recusado(dados: object) -> None:
    with pytest.raises(p.Recusa) as exc:
        p.ler_relatorio(dados)
    assert "não foi tocada" in str(exc.value)


def test_lista_de_criterios_ausente_nao_apaga_o_bloco_existente() -> None:
    antes = f"texto do dono\n\n{p.INICIO}\nvelho\n{p.FIM}\n"
    with pytest.raises(p.Recusa):
        p.ler_relatorio({"operacao": {"id": "op-1-abcd"}})  # a recusa vem antes de qualquer descrição ser montada
    assert antes.count(p.INICIO) == 1


def test_id_numerico_e_aceito_como_texto() -> None:
    d = _relatorio(criterios=[{"id": 11, "nome": "Ação", "estado": "provado_real", "nesta_operacao": "sim"}])
    assert p.ler_relatorio(d).criterios[0].id == "11"


# ------------------------------------------------------------------------------------------------- a descrição
def test_sem_marcadores_o_bloco_e_acrescentado_ao_fim_e_o_resto_fica() -> None:
    bloco = _bloco(_relatorio())
    nova, acao = p.nova_descricao("Texto do dono.\nSegunda linha.  \n", bloco)
    assert acao == "criado" and nova == f"Texto do dono.\nSegunda linha.\n\n{bloco}"
    assert p.nova_descricao("", bloco) == (bloco, "criado")


def test_com_marcadores_so_o_bloco_e_substituido() -> None:
    velho = f"{p.INICIO}\nlinha velha\n{p.FIM}"
    desc = f"antes\n\n{velho}\n\ndepois com **negrito**\n"
    bloco = _bloco(_relatorio())
    nova, acao = p.nova_descricao(desc, bloco)
    assert acao == "substituido"
    assert nova == f"antes\n\n{bloco}\n\ndepois com **negrito**\n"
    assert "linha velha" not in nova


def test_idempotente_mesmo_bloco_zero_acoes_inclusive_com_crlf() -> None:
    bloco = _bloco(_relatorio())
    desc = f"antes\n\n{bloco}\n\ndepois"
    assert p.nova_descricao(desc, bloco) == (desc, "igual")
    crlf = desc.replace("\n", "\r\n")
    assert p.nova_descricao(crlf, bloco) == (crlf, "igual")
    nova, _ = p.nova_descricao(desc, _bloco(_relatorio(gerado_em="2026-10-08T00:00:00Z")))
    assert p.nova_descricao(nova, _bloco(_relatorio(gerado_em="2026-10-08T00:00:00Z")))[1] == "igual"


@pytest.mark.parametrize("desc", [
    f"{p.INICIO} sem fim", f"só o fim {p.FIM}", f"{p.FIM} fora de ordem {p.INICIO}",
    f"{p.INICIO} a {p.FIM} e {p.INICIO} b {p.FIM}",
])
def test_marcadores_quebrados_recusam_sem_adivinhar(desc: str) -> None:
    with pytest.raises(p.Recusa):
        p.nova_descricao(desc, "bloco")


def test_descricao_grande_demais_recusa() -> None:
    with pytest.raises(p.Recusa):
        p.nova_descricao("x" * p.LIMITE_DESCRICAO, "bloco")


# ------------------------------------------------------------------------------------------------ cartão (falso)
class _Trello:
    def __init__(self, desc: str, quadro: str = p.QUADRO_EXECUCAO) -> None:
        self.desc, self.quadro, self.chamadas = desc, quadro, []

    async def cartao(self, card: str) -> tuple[str, str]:
        self.chamadas.append(("cartao", card))
        return "id-inteiro-do-cartao", self.quadro

    async def _pedir(self, metodo: str, caminho: str, *, params: dict | None = None, corpo: dict | None = None) -> dict:
        self.chamadas.append((metodo, caminho))
        assert metodo == "GET"
        return {"desc": self.desc}

    async def atualizar_cartao(self, card: str, *, nome: str | None = None, desc: str | None = None,
                               lista: str | None = None) -> dict:
        assert nome is None and lista is None
        self.chamadas.append(("PUT", card, desc))
        self.desc = desc or ""
        return {}


def _rodar(cl: _Trello, bloco: str, aplicar: bool) -> str:
    import asyncio
    return asyncio.run(p.sincronizar(cl, p.CARTAO_PAI, bloco, aplicar))


def test_sincronizar_cria_substitui_e_na_segunda_rodada_nao_faz_nada() -> None:
    bloco = _bloco(_relatorio())
    cl = _Trello("Descrição do pai.")
    assert _rodar(cl, bloco, aplicar=True) == "criado"
    assert cl.desc == f"Descrição do pai.\n\n{bloco}"
    puts = [c for c in cl.chamadas if c[0] == "PUT"]
    assert len(puts) == 1 and puts[0][1] == "id-inteiro-do-cartao"
    assert _rodar(cl, bloco, aplicar=True) == "igual"
    assert len([c for c in cl.chamadas if c[0] == "PUT"]) == 1
    novo = _bloco(_relatorio(gerado_em="2026-10-09T00:00:00Z"))
    assert _rodar(cl, novo, aplicar=True) == "substituido"
    assert cl.desc.startswith("Descrição do pai.\n\n") and cl.desc.count(p.INICIO) == 1 and "2026-10-09" in cl.desc


def test_ensaio_le_o_cartao_mas_nunca_grava() -> None:
    cl = _Trello("pai")
    assert _rodar(cl, _bloco(_relatorio()), aplicar=False) == "criado"
    assert [c[0] for c in cl.chamadas] == ["cartao", "GET"] and cl.desc == "pai"


def test_cartao_de_outro_quadro_recusa_sem_gravar() -> None:
    cl = _Trello("pai", quadro="outro-quadro")
    with pytest.raises(p.Recusa):
        _rodar(cl, _bloco(_relatorio()), aplicar=True)
    assert not [c for c in cl.chamadas if c[0] == "PUT"]


def test_marcador_quebrado_no_cartao_recusa_sem_gravar() -> None:
    cl = _Trello(f"pai {p.INICIO} sem fim")
    with pytest.raises(p.Recusa):
        _rodar(cl, _bloco(_relatorio()), aplicar=True)
    assert not [c for c in cl.chamadas if c[0] == "PUT"]


# ------------------------------------------------------------------------------------------------ entrada e linha de comando
class _Resposta:
    def __init__(self, corpo: bytes) -> None:
        self.corpo = corpo

    def __enter__(self) -> _Resposta:
        return self

    def __exit__(self, *a: object) -> None:
        return None

    def read(self) -> bytes:
        return self.corpo


def test_leitura_da_central_usa_a_rota_do_relatorio_e_recusa_id_estranho(monkeypatch: pytest.MonkeyPatch) -> None:
    vistos: list[str] = []

    def falso(url: str, timeout: float = 0) -> _Resposta:
        vistos.append(url)
        return _Resposta(json.dumps(_relatorio()).encode())

    monkeypatch.setattr(p.urllib.request, "urlopen", falso)
    assert p.ler_da_central("op-20261006130000-abc123")["ambiente"] == "real"
    assert vistos == [f"{p.CENTRAL}/api/operacoes/op-20261006130000-abc123/relatorio"]
    for ruim in ("../x", "op-1/../../y", "abc"):
        with pytest.raises(p.Recusa):
            p.ler_da_central(ruim)


def test_central_fora_do_ar_e_recusa_limpa(monkeypatch: pytest.MonkeyPatch) -> None:
    def cai(*a: object, **k: object) -> None:
        raise OSError("sem rede")

    monkeypatch.setattr(p.urllib.request, "urlopen", cai)
    with pytest.raises(p.Recusa) as exc:
        p.ler_da_central("op-20261006130000-abc123")
    assert "não respondeu" in str(exc.value)


def test_modo_padrao_so_imprime_e_nao_cria_cliente(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                   capsys: pytest.CaptureFixture[str]) -> None:
    arq = tmp_path / "relatorio.json"
    arq.write_text(json.dumps(_relatorio()), encoding="utf-8")
    monkeypatch.setattr(p.redacao, "recarregar", lambda *_: False)
    monkeypatch.setattr(p, "_novo_cliente", lambda: pytest.fail("o ensaio não pode abrir o Trello"))
    assert p.main(["--arquivo", str(arq)]) == 0
    saida = capsys.readouterr().out
    assert p.INICIO in saida and p.FIM in saida and "ensaio: nada foi lido do Trello nem gravado" in saida


def test_aplicar_grava_pelo_cliente_e_repetir_da_zero_acoes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                            capsys: pytest.CaptureFixture[str]) -> None:
    arq = tmp_path / "relatorio.json"
    arq.write_text(json.dumps(_relatorio()), encoding="utf-8")
    cl = _Trello("pai")
    monkeypatch.setattr(p.redacao, "recarregar", lambda *_: True)
    monkeypatch.setattr(p, "_novo_cliente", lambda: cl)
    assert p.main(["--arquivo", str(arq), "--aplicar"]) == 0
    assert "acrescentado ao fim" in capsys.readouterr().out and p.INICIO in cl.desc
    assert p.main(["--arquivo", str(arq), "--aplicar"]) == 0
    assert "0 ações" in capsys.readouterr().out
    assert len([c for c in cl.chamadas if c[0] == "PUT"]) == 1


def test_aplicar_sem_ler_os_nomes_do_banco_recusa(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                  capsys: pytest.CaptureFixture[str]) -> None:
    arq = tmp_path / "relatorio.json"
    arq.write_text(json.dumps(_relatorio()), encoding="utf-8")
    monkeypatch.setattr(p.redacao, "recarregar", lambda *_: False)
    monkeypatch.setattr(p, "_novo_cliente", lambda: pytest.fail("não pode abrir o Trello"))
    assert p.main(["--arquivo", str(arq), "--aplicar"]) == 2
    assert "nada foi gravado" in capsys.readouterr().out


def test_relatorio_sem_criterios_na_linha_de_comando_recusa_e_nao_toca_o_cartao(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    arq = tmp_path / "relatorio.json"
    arq.write_text(json.dumps({"operacao": {"id": "op-1-abcd"}}), encoding="utf-8")
    monkeypatch.setattr(p.redacao, "recarregar", lambda *_: True)
    monkeypatch.setattr(p, "_novo_cliente", lambda: pytest.fail("não pode abrir o Trello"))
    assert p.main(["--arquivo", str(arq), "--aplicar"]) == 2
    assert "lista `criterios`" in capsys.readouterr().out
