"""28.31 F3: o resumo abre com "Precisa de você", diz só o que mudou e não sai quando nada mudou.

Prova `simulated`: só a parte pura (`montar`), sem central, plano, Trello nem Telegram.
Rodar da raiz: `backend/.venv/Scripts/python.exe -m pytest -q .claude/canais/test_resumo_laco.py`.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import resumo_laco as r  # noqa: E402

AGORA = datetime(2026, 10, 4, 22, 0, tzinfo=timezone.utc)
OK = "🟢 <b>Central saudável</b> · deploy 32 no ar"


def _estado(**kw: object) -> dict:
    base: dict = {"saude": OK, "plano": "<b>Plano geral: 500 de 700 itens concluídos (71 %)</b>", "plano_detalhe": "",
                  "frentes": {"Android": "boots no notebook"}, "parados": [], "n_parados": 0, "pendencias": [],
                  "mudou": [], "nao_curados": 0, "nao_curados_desde": "", "eventos_linha": 10}
    base.update(kw)
    return base


def test_primeiro_envio_abre_com_precisa_de_voce_e_mostra_como_esta() -> None:
    texto, retrato = r.montar(_estado(pendencias=["responder sim ao 29.41"]), None, AGORA, "19:09Z")
    assert texto is not None
    linhas = texto.split("\n")
    assert linhas[2] == "<b>🙋 Precisa de você: 1</b>" and linhas[3] == "▪️ responder sim ao 29.41"
    assert "🆕" not in texto                                    # no primeiro envio tudo é novo: não marca
    assert "<b>Como está</b>" in texto and OK in texto
    assert retrato["pendencias"] == ["responder sim ao 29.41"]


def test_nada_mudou_nao_envia() -> None:
    _, retrato = r.montar(_estado(pendencias=["p1"]), None, AGORA)
    texto, _ = r.montar(_estado(pendencias=["p1"]), retrato, AGORA, "21:40Z")
    assert texto is None                                         # a pendência segue, mas não é nova


def test_so_o_que_mudou_e_a_pendencia_nova_marcada() -> None:
    _, retrato = r.montar(_estado(pendencias=["p1"], frentes={"Android": "a", "Jev": "b"}), None, AGORA)
    texto, _ = r.montar(_estado(pendencias=["p1", "p2"], frentes={"Android": "a", "Jev": "c"}), retrato, AGORA, "21:40Z")
    assert texto is not None
    assert "<b>🙋 Precisa de você: 2</b>" in texto and "▪️ 🆕 p2" in texto and "▪️ p1" in texto
    assert "<b>Mudou desde 21:40Z</b>" in texto
    assert "<b>Jev</b> · c" in texto and "Android" not in texto  # a frente que não mudou não se repete
    assert OK not in texto and "Plano geral" not in texto        # saúde e plano iguais não aparecem


def test_pendencia_resolvida_e_contada() -> None:
    _, retrato = r.montar(_estado(pendencias=["p1"]), None, AGORA)
    texto, _ = r.montar(_estado(pendencias=[]), retrato, AGORA, "21:40Z")
    assert texto is not None and "<b>🙋 Nada espera você agora.</b>" in texto
    assert "✔️ 1 pendência sua saiu da lista" in texto


def test_saude_com_problema_que_nao_muda_so_volta_a_cada_3_horas() -> None:
    ruim = "🟡 <b>Central com 1 problema(s)</b>"
    _, retrato = r.montar(_estado(), None, AGORA)
    texto, retrato = r.montar(_estado(saude=ruim), retrato, AGORA, "21:40Z")
    assert texto is not None and ruim in texto                   # mudou: entra na hora
    uma_hora = AGORA + timedelta(hours=1)
    assert r.montar(_estado(saude=ruim), retrato, uma_hora, "22:00Z")[0] is None
    # outra mudança sai, mas a saúde igual não pega carona e o lembrete não é adiado
    texto, retrato = r.montar(_estado(saude=ruim, mudou=["x"]), retrato, uma_hora, "22:00Z")
    assert texto is not None and ruim not in texto
    texto, retrato = r.montar(_estado(saude=ruim), retrato, AGORA + timedelta(hours=3), "23:00Z")
    assert texto is not None and f"{ruim} · segue desde 22:00Z" in texto
    assert r.montar(_estado(saude=ruim), retrato, AGORA + timedelta(hours=5), "01:00Z")[0] is None
    pior = "🔴 <b>A Central não respondeu</b> na hora deste resumo"
    texto, _ = r.montar(_estado(saude=pior), retrato, AGORA + timedelta(hours=5), "01:00Z")
    assert texto is not None and pior in texto and "segue" not in texto  # mudou de novo: entra na hora


def test_mudanca_so_no_detalhe_do_plano_e_detectada() -> None:
    _, retrato = r.montar(_estado(plano_detalhe="22 parciais · 2 bloqueados · 22 a fazer"), None, AGORA)
    texto, _ = r.montar(_estado(plano_detalhe="21 parciais · 3 bloqueados · 22 a fazer"), retrato, AGORA, "21:40Z")
    assert texto is not None and "3 bloqueados" in texto


def test_plano_que_falha_na_leitura_mantem_o_retrato() -> None:
    _, retrato = r.montar(_estado(), None, AGORA)
    texto, retrato2 = r.montar(_estado(plano="", plano_detalhe="", mudou=["x"]), retrato, AGORA, "21:40Z")
    assert texto is not None and "Plano geral" not in texto
    assert retrato2["plano"] == retrato["plano"]
    assert r.montar(_estado(), retrato2, AGORA, "22:00Z")[0] is None  # a volta da leitura não repete o plano


def test_parados_so_os_novos_e_leitura_falha_nao_conta_como_mudanca() -> None:
    _, retrato = r.montar(_estado(parados=["cartão A"], n_parados=1), None, AGORA)
    texto, retrato2 = r.montar(_estado(parados=["cartão A", "cartão B"], n_parados=2), retrato, AGORA, "21:40Z")
    assert texto is not None and "cartão B" in texto and "cartão A" not in texto
    texto, _ = r.montar(_estado(parados=None, n_parados=None), retrato2, AGORA, "22:00Z")
    assert texto is None                                         # a leitura do Trello falhou: não inventa mudança


def test_parados_a_volta_da_leitura_depois_de_um_envio_com_falha_nao_e_novidade() -> None:
    _, retrato = r.montar(_estado(parados=["cartão A"], n_parados=1), None, AGORA)
    # a rodada com o Trello fora ENVIA por outro motivo: o retrato guarda os parados que já se conheciam
    texto, retrato2 = r.montar(_estado(parados=None, n_parados=None, mudou=["x"]), retrato, AGORA, "21:40Z")
    assert texto is not None and retrato2["parados"] == ["cartão A"]
    assert r.montar(_estado(parados=["cartão A"], n_parados=1), retrato2, AGORA, "22:00Z")[0] is None


def test_mudou_extra_e_eventos_nao_curados_contam_como_mudanca() -> None:
    _, retrato = r.montar(_estado(), None, AGORA)
    texto, _ = r.montar(_estado(mudou=["deploy 32 no ar"]), retrato, AGORA, "21:40Z")
    assert texto is not None and "▪️ deploy 32 no ar" in texto
    texto, _ = r.montar(_estado(nao_curados=3, nao_curados_desde="21:50Z"), retrato, AGORA, "21:40Z")
    assert texto is not None and "3 novidades desde 21:50Z" in texto


def test_eventos_nao_curados_ja_contados_nao_se_repetem(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(r, "_ler_json", lambda caminho, padrao: {"eventos_curados_ate": 10})
    monkeypatch.setattr(r, "_linhas_de_fato", lambda: [f"21:{i:02d}Z | fato {i}" for i in range(15)])
    monkeypatch.setattr(r, "_plano", lambda: ("", ""))
    monkeypatch.setattr(r, "_parados", lambda agora: None)
    monkeypatch.setattr(r, "_saude", lambda deploy: OK)
    e = r.ler_estado(AGORA)
    assert (e["nao_curados"], e["nao_curados_desde"]) == (5, "21:10Z")
    e = r.ler_estado(AGORA, ja_contado=13)                       # o último envio contou até a linha 13
    assert (e["nao_curados"], e["nao_curados_desde"]) == (2, "21:13Z")
    assert r.ler_estado(AGORA, ja_contado=15)["nao_curados"] == 0


def test_eventos_nas_duas_formas_misturadas_contam(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    eventos = tmp_path / "eventos.md"
    eventos.write_text("\n".join([
        "# Eventos",
        "",
        "04/10 12:13Z | Android | Funil | fato de tabela | Feito",
        "- 16:36Z (04/10) orquestradora: fato em lista",
        "  continuação solta de um fato, sem hora: não conta",
        "04/10 17:00Z | Jev | 31.39 | outra tabela | Feito",
        "- 9:05Z (05/10) orquestradora: hora de um dígito",
        "- item de lista sem hora: não conta",
    ]), encoding="utf-8")
    monkeypatch.setattr(r, "EVENTOS", eventos)
    assert len(r._linhas_de_fato()) == 4
    assert r._nao_curados(0) == (4, "04/10 12:13Z")
    assert r._nao_curados(1) == (3, "16:36Z")                   # o primeiro não curado é da forma em lista
    assert r._nao_curados(3) == (1, "9:05Z")
    assert r._nao_curados(4) == (0, "")


def test_cabecalho_separador_e_prosa_com_barra_nao_contam(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Revisão do #327: só a linha de tabela que começa pela hora é fato; o texto livre nunca vira a hora do resumo."""
    eventos = tmp_path / "eventos.md"
    eventos.write_text("\n".join([
        "| Hora | Frente | Item | O que mudou | Lista |",
        "|---|---|---|---|---|",
        "prosa solta com a | no meio, segredo de ninguém",
        "18:36Z | Orquestradora | 28.31 | fato sem data | Feito",
        "- 10:00Z (04/10) x | y",
        "04/10 08:14Z | Jev | 31.39 | fato com data | Feito",
    ]), encoding="utf-8")
    monkeypatch.setattr(r, "EVENTOS", eventos)
    assert len(r._linhas_de_fato()) == 3
    assert r._nao_curados(0) == (3, "18:36Z")
    assert r._nao_curados(1) == (2, "10:00Z")                   # lista com "|": um fato só, a hora da lista
    assert r._nao_curados(2) == (1, "04/10 08:14Z")
    assert r._hora_do_fato("prosa | com barra") == "" and r._hora_do_fato("12:00Z texto | x") == ""


@pytest.mark.parametrize(("entrada", "marcador"), [
    ("escreva para fulano.tal@exemplo.com.br hoje", "[e-mail]"),
    ("ligue +55 (11) 98765-4321", "[telefone]"),
    ("ligue 11 98765 4321", "[telefone]"),
    ("ligue 11987654321", "[telefone]"),
    ("veja https://exemplo.com/caminho?x=1", "[link]"),
    ("veja www.exemplo.com", "[link]"),
])
def test_contato_nao_vai_ao_dono(entrada: str, marcador: str) -> None:
    saida = r._e(entrada)
    assert marcador in saida and "exemplo" not in saida and "4321" not in saida


def test_numeros_do_resumo_nao_viram_telefone() -> None:
    texto = "436 de 482 itens · corte às 21:47Z de 2026-10-04 · deploy 31 · migração 106 · limite de 8 para 9"
    assert r._sem_contato(texto) == texto


@pytest.fixture
def padroes_da_redacao(monkeypatch: pytest.MonkeyPatch) -> None:
    """Os padrões do módulo voltam ao fim do teste (num worktree sem banco, `recarregar()` não os restauraria)."""
    for nome in ("_INTEIROS", "_PARTES", "_PERSONA", "_PERSONA_PARTE"):
        monkeypatch.setattr(r.redacao, nome, getattr(r.redacao, nome))


def test_redacao_rele_os_nomes_do_banco(tmp_path: Path, padroes_da_redacao: None) -> None:
    (tmp_path / "data").mkdir()
    con = sqlite3.connect(tmp_path / "data" / "poc.sqlite3")
    con.execute("create table personas (name text)")
    con.execute("create table profile_accounts (handle text)")
    con.execute("insert into personas values ('Teodora Quintanilha')")
    con.execute("insert into profile_accounts values ('@teo.quinta')")
    con.commit()
    con.close()
    assert r.redacao.recarregar(tmp_path) is True              # persona criada depois da importação
    saida = r._e("Teodora Quintanilha comentou como teo.quinta")
    assert "Teodora" not in saida and "teo.quinta" not in saida
    # falha transitória depois de uma leitura inteira: a lista completa continua valendo, não só a reserva
    assert r.redacao.recarregar(tmp_path / "sem-banco") is False
    saida = r._e("Teodora Quintanilha comentou como teo.quinta")
    assert "Teodora" not in saida and "teo.quinta" not in saida


def test_falha_parcial_do_banco_nao_troca_a_lista(tmp_path: Path, padroes_da_redacao: None) -> None:
    (tmp_path / "data").mkdir()
    con = sqlite3.connect(tmp_path / "data" / "poc.sqlite3")
    con.execute("create table personas (name text)")           # sem profile_accounts: a 2ª leitura falha
    con.execute("insert into personas values ('Outra Pessoa')")
    con.commit()
    con.close()
    antes = list(r.redacao._INTEIROS)
    assert r.redacao.recarregar(tmp_path) is False
    assert r.redacao._INTEIROS == antes


def test_sem_banco_na_subida_nao_envia_e_depois_da_primeira_leitura_segue(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(r, "_banco_lido", False)
    monkeypatch.setattr(r.redacao, "recarregar", lambda raiz=None: False)
    monkeypatch.setattr(r, "ler_estado", lambda agora, ja=0: pytest.fail("não lê nada sem os nomes"))
    assert r.compor({"eventos_linha": 7}, AGORA) == (None, None, 7)
    gravados: list[object] = []
    monkeypatch.setattr(r, "_gravar_json", lambda caminho, dado: gravados.append(dado))
    monkeypatch.setattr(r, "enviar", lambda texto: pytest.fail("não envia"))
    assert r.rodada({"eventos_linha": 7}) is None and gravados == []   # nem `conferido_em`
    # já leu uma vez nesta subida: a falha seguinte compõe com a lista da última leitura
    monkeypatch.setattr(r, "_banco_lido", True)
    monkeypatch.setattr(r, "ler_estado", lambda agora, ja=0: _estado())
    texto, retrato, _ = r.compor({"eventos_linha": 7}, AGORA)
    assert texto is not None and retrato is not None


def test_texto_dinamico_passa_pela_redacao() -> None:
    _, retrato = r.montar(_estado(), None, AGORA)
    texto, _ = r.montar(_estado(frentes={"Android": "<script>"}), retrato, AGORA, "21:40Z")
    assert texto is not None and "<script>" not in texto and "&lt;script&gt;" in texto


# --- cadência: a rotina no máximo a cada piso; "Precisa de você" que muda sai já (orquestradora, 04/10 23:14Z) ---

def test_rotina_antes_do_piso_nao_sai_e_depois_sai() -> None:
    ant = {"pendencias": ["p1"]}

    assert not r.pode_enviar({"pendencias": ["p1"]}, ant, "2026-10-04T21:30:00Z", AGORA, 3600)
    assert r.pode_enviar({"pendencias": ["p1"]}, ant, "2026-10-04T21:00:00Z", AGORA, 3600)


def test_precisa_de_voce_que_muda_sai_antes_do_piso() -> None:
    recente = "2026-10-04T21:59:00Z"
    assert r.pode_enviar({"pendencias": ["p1", "p2"]}, {"pendencias": ["p1"]}, recente, AGORA, 3600)   # nova
    assert r.pode_enviar({"pendencias": []}, {"pendencias": ["p1"]}, recente, AGORA, 3600)             # resolvida
    assert r.pode_enviar({"pendencias": ["p1"]}, None, recente, AGORA, 3600)                           # 1º retrato


def test_sem_envio_anterior_sai() -> None:
    assert r.pode_enviar({"pendencias": []}, None, None, AGORA, 3600)


def test_rodada_segura_a_rotina_sem_andar_o_retrato(monkeypatch: pytest.MonkeyPatch) -> None:
    gravados: list[tuple[Path, dict]] = []
    enviados: list[str] = []
    monkeypatch.setattr(r, "_agora", lambda: AGORA)
    monkeypatch.setattr(r, "compor", lambda c, a: ("texto de rotina", {"pendencias": []}, 20))
    monkeypatch.setattr(r, "enviar", lambda t: enviados.append(t) or 999)
    monkeypatch.setattr(r, "_gravar_json", lambda p, d: gravados.append((p, d)))
    monkeypatch.setattr(r, "_ler_json", lambda p, d: {})
    cursor = {"enviado_em": "2026-10-04T21:30:00Z", "eventos_linha": 10, "retrato": {"pendencias": []}}
    novo = r.rodada(cursor, 3600)
    assert enviados == [] and novo is not None
    assert novo["retrato"] == cursor["retrato"] and novo["eventos_linha"] == 10   # nada se perde: conta do último envio
    assert novo["conferido_em"] == "2026-10-04T22:00:00Z" and novo["enviado_em"] == cursor["enviado_em"]
    novo = r.rodada({**cursor, "enviado_em": "2026-10-04T20:59:00Z"}, 3600)
    assert enviados == ["texto de rotina"] and novo is not None and novo["message_id"] == 999
