"""`scripts/jev-relatorio-31-10.py`: o relatório do 31.10 contra os limiares pré-registrados, num banco sintético.

Prova `simulated`: banco SQLite temporário migrado (como `test_bench.py`), linhas de sombra, revisões e transições montadas
aqui. Confere o que o golden set manda: fallback só na cobertura, `nenhuma` como abstenção, "sem amostra" abaixo do
mínimo, o rótulo da pessoa antes do desfecho, a métrica principal só nas execuções sem fluxo, e o banco aberto só para
leitura.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.db import Database  # noqa: E402
from app.planning.decisao_fechada.intencao import id_opaco  # noqa: E402

_spec = importlib.util.spec_from_file_location("jev_relatorio_31_10", ROOT / "scripts" / "jev-relatorio-31-10.py")
rel = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rel)  # type: ignore[union-attr]

AGORA = datetime(2026, 10, 10, 12, tzinfo=UTC)
NENHUMA = "opt:nenhuma"
#: 31.19: o rótulo 1 só vale do autor dono declarado; nos testes antigos ele é `sessao-1`, o autor padrão da transição.
DONO = frozenset({"sessao-1"})


class Banco:
    def __init__(self, tmp: Path) -> None:
        self.db = Database(tmp / "relatorio.sqlite3")
        self.db.migrate()
        # Dados sintéticos: a etapa sem objetivo nem aparelho cadastrados basta para o desfecho comprovado.
        self.db.execute("PRAGMA foreign_keys=OFF")
        self._n = 0

    def sombra(self, *, origem: str, pergunta: str, escolha: str | None, ref: str, run_id: str | None = None,
               decisao_real: str | None = None, fallback: str | None = None, motivo: str | None = None,
               ambiguos: int | None = None, ts: str = "2026-10-05T10:00:00Z", usd: float = 0.0, ms: float = 100.0,
               probs: dict[str, float] | None = None, confianca: float | None = None) -> None:
        self._n += 1
        self.db.execute(
            "INSERT INTO decisao_fechada_sombra(ts, chamada, origem, classe, modo, pergunta_id, escolha, decisao_real, usd,"
            " tokens, ms, fallback_reason, run_id, ref, ambiguos, motivo_privacidade, probabilidades, confianca)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ts, f"ch-{self._n}", origem, "C0" if origem == "curador" else "C3", "shadow", pergunta, escolha, decisao_real,
             usd, 10 if usd else 0, ms, fallback, run_id, ref, ambiguos, motivo,
             json.dumps(probs) if probs is not None else None, confianca))

    def revisao(self, *, template: str, item_ref: str, dossie_hash: str, kind: str = "receita", saida: Any = None,
                validade: str = "ok", simulated: int = 0, decisao_final: str | None = None, decidido_por: str | None = None,
                dossie: Any = None) -> None:
        self._n += 1
        self.db.execute(
            "INSERT INTO learning_reviews(id, created_at, item_ref, item_kind, gatilho, dossie_hash, dossie, template_id,"
            " template_versao, simulated, saida, validade, decisao_final, decidido_por) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"lr-{self._n}", "2026-10-05T09:00:00Z", item_ref, kind, "teste", dossie_hash, json.dumps(dossie or {}),
             template, "v1", simulated, json.dumps(saida) if saida is not None else None, validade, decisao_final,
             decidido_por))

    def transicao(self, item_ref: str, to_state: str, *, por: str = "sessao-1", em: str = "2026-10-06T00:00:00Z",
                  de: str = "published") -> None:
        self.db.execute("INSERT INTO learning_transitions(item_ref, item_kind, from_state, to_state, reason, decided_by,"
                        " decided_at) VALUES (?,?,?,?,?,?,?)", (item_ref, "receita", de, to_state, "teste", por, em))

    def execucao(self, run_id: str, app: str, *, comprovada: bool = True) -> None:
        self.db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, instance_ids, created_at, app_ids)"
                        " VALUES (?,?,?,?,?,?,?,?)", (run_id, f"k-{run_id}", "c", "execute", "completed", "[]",
                                                       "2026-10-05T09:00:00Z", json.dumps([app])))
        self.db.execute("INSERT INTO steps(run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
                        " postcondition, timeout_s, max_attempts, status, result) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (run_id, "o1", "android-01", 1, 1, "k", "t", "g", "{}", 60, 1, "succeeded",
                         json.dumps({"verified": comprovada})))


@pytest.fixture
def banco(tmp_path: Path) -> Any:
    b = Banco(tmp_path)
    yield b
    b.db.close()


def _curador(b: Banco, n: int, *, escolha: str, rotulo_estado: str | None, kind: str = "receita",
             controle_contra: int = 0) -> None:
    for i in range(n):
        h = f"h-{kind}-{escolha}-{rotulo_estado}-{i}"
        b.revisao(template="curador", item_ref=f"receita:{h}", dossie_hash=h, kind=kind, saida={"decisao": "manter"},
                  dossie={"item": {"kind": kind}, "evidencias": {"contra": controle_contra}})
        b.sombra(origem="curador", pergunta="curador_triagem", escolha=escolha, ref=h)
        if rotulo_estado:
            b.transicao(f"receita:{h}", rotulo_estado)


# ------------------------------------------------------------------ curador
def test_curador_sem_amostra_nao_da_taxa_de_go(banco: Banco) -> None:
    _curador(banco, 5, escolha="opt:manter", rotulo_estado="published")
    r = rel.relatorio_do_curador(banco.db, None, AGORA, DONO)["estratos"]["receita"]
    assert r["veredito"] == "sem amostra" and r["modo"] == "off"
    assert r["medidas"]["rotulos"] == 5 and r["falhou"] == ["rótulos 5 < 30"]
    assert r["data_prevista"] is not None                                     # há ritmo: 5 rótulos desde a 1ª linha


def test_curador_go_com_todos_os_criterios(banco: Banco) -> None:
    _curador(banco, 30, escolha="opt:manter", rotulo_estado="published")      # controle também diz manter: vantagem 0
    r = rel.relatorio_do_curador(banco.db, None, AGORA, DONO)["estratos"]["receita"]
    assert r["veredito"] == "NO-GO" and any("vantagem" in f for f in r["falhou"])
    assert r["medidas"]["acordo"] == 1.0 and r["medidas"]["acordo_controle"] == 1.0


def test_curador_erro_grave_e_fallback_so_na_cobertura(banco: Banco) -> None:
    _curador(banco, 28, escolha="opt:rebaixar", rotulo_estado="deprecated")
    _curador(banco, 2, escolha="opt:descartar", rotulo_estado="published")   # rótulo manter, Jev descarta: grave
    for i in range(5):                                                         # fallback: cobertura, nunca acerto
        banco.sombra(origem="curador", pergunta="curador_triagem", escolha=None, ref=f"fb-{i}", fallback="timeout")
    r = rel.relatorio_do_curador(banco.db, None, AGORA, DONO)["estratos"]
    m = r["receita"]["medidas"]
    assert m["rotulos"] == 30 and m["acordo"] == round(28 / 30, 4) and m["erro_grave"] == round(2 / 30, 4)
    assert r["desconhecido"]["medidas"]["fallbacks"] == {"timeout": 5}
    assert r["receita"]["veredito"] == "NO-GO"


def test_curador_so_rotula_decisao_de_pessoa_depois_da_sombra(banco: Banco) -> None:
    banco.revisao(template="curador", item_ref="receita:a", dossie_hash="a", saida={"decisao": "rebaixar"})
    banco.sombra(origem="curador", pergunta="curador_triagem", escolha="opt:rebaixar", ref="a")
    banco.transicao("receita:a", "disabled", por="sistema")                    # do sistema: não é rótulo
    banco.transicao("receita:a", "disabled", em="2026-10-01T00:00:00Z")       # antes da sombra: não é rótulo
    m = rel.relatorio_do_curador(banco.db, None, AGORA, DONO)["estratos"]["receita"]["medidas"]
    assert m["rotulos"] == 0
    assert m["concordancia_com_o_curador"] == 1.0 and m["com_parecer_valido"] == 1   # acompanhamento, não GO


def test_curador_rotulo_1_so_do_autor_dono(banco: Banco) -> None:
    """31.19: sessão Claude e `panel` (o último recurso de `api.quem`) não rotulam; sem dono declarado, ninguém rotula."""
    for ref, por in (("a", "orquestradora"), ("b", "panel"), ("c", "Flavio")):
        banco.revisao(template="curador", item_ref=f"receita:{ref}", dossie_hash=ref, saida={"decisao": "manter"})
        banco.sombra(origem="curador", pergunta="curador_triagem", escolha="opt:descartar", ref=ref)
        banco.transicao(f"receita:{ref}", "disabled", por=por)
    banco.transicao("receita:a", "disabled", por="sistema")                    # o sistema nunca entra na conta
    sem_dono = rel.relatorio_do_curador(banco.db, None, AGORA)
    assert sem_dono["estratos"]["receita"]["medidas"]["rotulos"] == 0
    assert sem_dono["rotulo_1"]["transicoes_fora_do_dono"] == 3 and "desligado" in sem_dono["rotulo_1"]["nota"]
    com_dono = rel.relatorio_do_curador(banco.db, None, AGORA, frozenset({"Flavio"}))
    m = com_dono["estratos"]["receita"]["medidas"]
    assert m["rotulos"] == 1 and m["rotulos_por_fonte"] == {"dono": 1} and m["acordo"] == 1.0
    assert com_dono["rotulo_1"] == {"autores_dono": ["Flavio"], "nivel": "PROVED", "transicoes_fora_do_dono": 2}


def test_curador_estados_distintos_e_cobertura_por_limiar(banco: Banco) -> None:
    """31.19: as 4 respostas reais de 03/10, na forma: mesmo C0, confiança 0,50–0,52, maior probabilidade 0,60–0,62,
    todas `abaixo_do_limiar` no 0,85. O contrafactual só mede; a porta continua no limiar do contrato."""
    for i, (conf, rev) in enumerate(((0.50, 0.60), (0.51, 0.61), (0.52, 0.62), (0.50, 0.60))):
        banco.revisao(template="curador", item_ref=f"receita:s{i}", dossie_hash=f"s{i}", saida={"decisao": "revisar"},
                      dossie={"item": {"kind": "receita"}, "evidencias": {"total": 1}})
        banco.sombra(origem="curador", pergunta="curador_triagem", escolha=None, ref=f"s{i}",
                     fallback="abaixo_do_limiar", confianca=conf,
                     probs={"opt:revisar": rev, "opt:manter": round(1 - rev - 0.05, 2), "opt:nenhuma": 0.05})
    m = rel.relatorio_do_curador(banco.db, None, AGORA)["estratos"]["receita"]["medidas"]
    assert m["estados_distintos"] == 1 and m["respondidos"] == 0 and m["fallbacks"] == {"abaixo_do_limiar": 4}
    assert m["cobertura_por_limiar"] == {
        "0.50": {"confianca": 1.0, "maior_probabilidade": 1.0},
        "0.60": {"confianca": 0.0, "maior_probabilidade": 1.0},
        "0.70": {"confianca": 0.0, "maior_probabilidade": 0.0},
        "0.85": {"confianca": 0.0, "maior_probabilidade": 0.0}}
    banco.revisao(template="curador", item_ref="receita:s9", dossie_hash="s9", saida={"decisao": "manter"},
                  dossie={"item": {"kind": "receita"}, "evidencias": {"total": 3}})
    banco.sombra(origem="curador", pergunta="curador_triagem", escolha="opt:manter", ref="s9", confianca=0.9,
                 probs={"opt:manter": 0.9})
    m = rel.relatorio_do_curador(banco.db, None, AGORA)["estratos"]["receita"]["medidas"]
    assert m["estados_distintos"] == 2 and m["cobertura_por_limiar"]["0.85"] == {"confianca": 0.2,
                                                                                   "maior_probabilidade": 0.2}


def test_percentil_unificado_nos_dois_scripts() -> None:
    """31.19: relatório, prova do 31.17 e `sombra._p95` (que grava `ms_p95`) dão o mesmo posto."""
    from app.planning.decisao_fechada.sombra import _p95

    spec = importlib.util.spec_from_file_location("jev_prova_31_17", ROOT / "scripts" / "jev-prova-31-17.py")
    prova = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prova)  # type: ignore[union-attr]
    for n in range(1, 61):
        valores = [float((i * 37) % 101) for i in range(n)]                 # fora de ordem, sem repetição
        assert rel.percentil(valores, 95) == prova._percentil(valores, 95) == round(_p95(valores), 1)
        assert rel.percentil(valores, 50) == prova._percentil(valores, 50)
    # as 4 chamadas do Jev de 03/10 (ms): antes, 473,8 (mediana interpolada) e 510,2 (q·(n−1) arredondado)
    reais = [552.80, 421.96, 510.23, 437.34]
    assert rel._percentis(reais) == {"p50": 437.3, "p95": 552.8}
    assert prova._percentil(reais, 50) == 437.3 and prova._percentil(reais, 95) == 552.8
    assert rel.percentil([], 50) is None and prova._percentil([], 95) is None


def test_curador_rotulo_pela_direcao_da_transicao() -> None:
    assert rel._rotulo_da_transicao("draft", "candidate") == "manter"          # subiu
    assert rel._rotulo_da_transicao("published", "candidate") == "rebaixar"    # desceu
    assert rel._rotulo_da_transicao(None, "validated") == "manter"
    assert rel._rotulo_da_transicao("disabled", "candidate") == "manter"       # reativou
    assert rel._rotulo_da_transicao("validated", "deprecated") == "rebaixar"
    assert rel._rotulo_da_transicao("candidate", "disabled") == "descartar"


# ------------------------------------------------------------------ intenção
def test_intencao_rotulo_da_pessoa_e_do_desfecho(banco: Banco) -> None:
    skill = "flow:abc"
    banco.execucao("r-1", "qa-messenger")
    banco.revisao(template="intencao", item_ref="run:r-1", dossie_hash="d1", kind="execucao",
                  decisao_final=skill, decidido_por="sessao-1")
    banco.sombra(origem="intencao", pergunta="intencao_catalogo", escolha=id_opaco(skill), ref="r-1", run_id="r-1",
                 decisao_real=NENHUMA, ambiguos=1)
    banco.execucao("r-2", "qa-messenger")                                      # cadeia resolveu e a execução comprovou
    banco.sombra(origem="intencao", pergunta="intencao_catalogo", escolha=id_opaco("flow:x"), ref="r-2", run_id="r-2",
                 decisao_real=id_opaco("flow:y"), ambiguos=0)
    banco.execucao("r-3", "qa-messenger", comprovada=False)                    # sem prova: sem rótulo pelo desfecho
    banco.sombra(origem="intencao", pergunta="intencao_catalogo", escolha=id_opaco("flow:y"), ref="r-3", run_id="r-3",
                 decisao_real=id_opaco("flow:y"))
    r = rel.relatorio_da_intencao(banco.db, None, AGORA, flows=None)
    m = r["estratos"]["qa-messenger"]["medidas"]
    assert m["rotulos"] == 2 and m["rotulos_por_fonte"] == {"pessoa": 1, "desfecho": 1}
    assert m["precisao"] == 0.5 and m["aceite_errado"] == 0.5
    assert m["principal_sem_fluxo_rotulados"] == 1 and m["principal"] == 1      # r-1: sem fluxo e o Jev casou
    # o controle só se mede contra a pessoa: em r-1 a cadeia ficou sem fluxo e a pessoa escolheu um
    assert m["controle_sobre_rotulos_da_pessoa"] == 1 and m["acerto_controle_cadeia"] == 0.0
    assert m["ambiguos"] == {"0": 1, "1": 1} and m["execucoes_com_ambiguidade"] == 1
    assert r["estratos"]["qa-messenger"]["veredito"] == "sem amostra"


def test_intencao_privacidade_relatada_fora_do_acerto(banco: Banco) -> None:
    banco.execucao("r-p", "instagram")
    banco.sombra(origem="intencao", pergunta="intencao_catalogo", escolha=None, ref="r-p", run_id="r-p",
                 fallback="privacidade", motivo="c7_login_valor")
    e = rel.relatorio_da_intencao(banco.db, None, AGORA, flows=None)["estratos"]["instagram"]
    assert e["medidas"]["recusas_por_privacidade"] == {"c7_login_valor": 1}
    assert e["medidas"]["cobertura"] == 0.0 and e["medidas"]["rotulos"] == 0
    assert e["data_prevista"] == "sem data (golden set §3)"


def test_intencao_go_exige_a_metrica_principal(banco: Banco) -> None:
    for i in range(50):                                     # 50 rotulados pelo desfecho, todos certos, nenhum sem fluxo
        banco.execucao(f"g-{i}", "qa-messenger")
        banco.sombra(origem="intencao", pergunta="intencao_catalogo", escolha=id_opaco("flow:y"), ref=f"g-{i}",
                     run_id=f"g-{i}", decisao_real=id_opaco("flow:y"))
    e = rel.relatorio_da_intencao(banco.db, None, AGORA, flows=None)["estratos"]["qa-messenger"]
    assert e["medidas"]["precisao"] == 1.0 and e["veredito"] == "NO-GO"
    assert e["falhou"] == ["métrica principal 0 = 0"]


def test_teto_da_r2_pelos_fluxos_ativos() -> None:
    flows = [{"id": "1", "status": "active", "command_template": "abrir o feed"},
             {"id": "2", "status": "active", "command_template": "mandar {mensagem} para {contato}"},
             {"id": "3", "status": "disabled", "command_template": "curtir"}]
    assert rel._teto_da_r2(flows) == {"nivel": "PROVED", "ativos_sem_parametro": 1, "ativos": 2,
                                      "todos_sem_parametro": 2, "todos": 3}
    assert rel._teto_da_r2(None)["nivel"] == "not_run"


# ------------------------------------------------------------------ montagem, saída e só leitura
def test_montar_e_markdown(banco: Banco) -> None:
    _curador(banco, 2, escolha="opt:manter", rotulo_estado="published")
    banco.sombra(origem="curador", pergunta="curador_triagem", escolha="opt:manter", ref="x", usd=0.0002,
                 ts="2026-10-06T10:00:00Z")
    r = rel.montar(banco.db, desde="2026-10-06T00:00:00Z", agora=AGORA, flows=[])
    assert r["curador"]["linhas"] == 1 and r["curador"]["custo"]["usd"] == 0.0002
    md = rel.em_markdown(r)
    assert "Curador do Livro" in md and "sem amostra" in md and "nunca liga nada" in md
    assert "Rótulo 1 (decisão do dono): autores nenhum" in md and "desligado" in md


def test_cli_abre_so_leitura_e_nao_cria_banco(tmp_path: Path, banco: Banco) -> None:
    with pytest.raises(SystemExit, match="banco não encontrado"):
        rel.main(["--db", str(tmp_path / "nao-existe.sqlite3"), "--sem-flows"])
    assert not (tmp_path / "nao-existe.sqlite3").exists()
    saida = tmp_path / "r.json"
    assert rel.main(["--db", str(tmp_path / "relatorio.sqlite3"), "--sem-flows", "--json", str(saida),
                     "--md", str(tmp_path / "r.md"), "--autor-dono", "Flavio", "--autor-dono", " "]) == 0
    lido = json.loads(saida.read_text(encoding="utf-8"))
    assert lido["intencao"]["teto_da_r2"]["nivel"] == "not_run"
    assert lido["curador"]["rotulo_1"]["autores_dono"] == ["Flavio"]                # o nome vazio não vira autor
    db = rel._abrir(type("A", (), {"dsn": None, "db": str(tmp_path / "relatorio.sqlite3")})())
    try:
        with pytest.raises(Exception, match="readonly|read-only|query_only|attempt to write"):
            db.execute("DELETE FROM decisao_fechada_sombra")
        db._reabrir()   # a reconexão também nasce só leitura
        with pytest.raises(Exception, match="readonly|read-only|query_only|attempt to write"):
            db.execute("DELETE FROM decisao_fechada_sombra")
    finally:
        db.close()
