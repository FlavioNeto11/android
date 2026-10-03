"""`scripts/jev-leitura-intencao.py`: a leitura da sombra de intenção (R2/R3) depois do deploy 11, num banco sintético.

Prova `simulated`: banco SQLite temporário migrado (como `test_jev_prova_31_17.py`), com linhas de sombra e chamadas do Jev
montadas aqui. Confere as contagens por pergunta, as recusas por motivo, a distribuição de confiança e de probabilidade
contra o limiar, o custo e a latência pelo posto mais próximo, e o "zero texto" que falha fechado sem nunca imprimir o
valor que achou. O banco abre só para leitura.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.db import Database  # noqa: E402
from app.planning.decisao_fechada.intencao import id_opaco  # noqa: E402

_spec = importlib.util.spec_from_file_location("jev_leitura_intencao", ROOT / "scripts" / "jev-leitura-intencao.py")
leitura = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(leitura)  # type: ignore[union-attr]

DEPOIS = "2026-10-03T16:10:00.000Z"
ANTES = "2026-10-03T15:00:00.000Z"
A, B, NENHUMA = id_opaco("ig.abrir_conversa"), id_opaco("ig.abrir_numero"), "opt:nenhuma"
#: Um texto de pessoa: se ele aparecer em qualquer saída, o script vazou o que achou.
TEXTO_DA_PESSOA = "entre com a senha girassol do lucas"


class Banco:
    def __init__(self, tmp: Path) -> None:
        self.caminho = tmp / "leitura.sqlite3"
        self.db = Database(self.caminho)
        self.db.migrate()
        self.db.execute("PRAGMA foreign_keys=OFF")

    def sombra(self, run: str, chamada: str, *, pergunta: str = "intencao_catalogo", escolha: str | None = A,
               probs: Any = None, conf: float | None = 0.9, fallback: str | None = None, motivo: str | None = None,
               usd: float = 0.0, ms: float = 400.0, ts: str = DEPOIS, origem: str = "intencao", classe: str = "C3",
               ref: str | None = None) -> None:
        if probs is None and fallback != "privacidade":
            probs = {escolha or A: conf or 0.0, NENHUMA: round(1 - (conf or 0.0), 2)}
        self.db.execute(
            "INSERT INTO decisao_fechada_sombra(ts, chamada, origem, classe, modo, pergunta_id, escolha, probabilidades,"
            " confianca, usd, tokens, ms, fallback_reason, run_id, ref, motivo_privacidade)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ts, chamada, origem, classe, "shadow", pergunta, escolha if fallback is None else None,
             probs if isinstance(probs, str) else (json.dumps(probs, sort_keys=True) if probs else None),
             conf, usd, 10 if usd else 0, ms if fallback != "privacidade" else 0.0, fallback, run,
             ref if ref is not None else run, motivo))

    def chamada(self, run: str, *, ok: bool = True, usd: float = 0.00003, ms: int = 400, ts: str = DEPOIS,
                origem: str = "intencao", erro: str | None = None) -> None:
        self.db.execute(
            "INSERT INTO ai_calls(ts, role, model, tier, input_tokens, output_tokens, ms, ok, provider, error_kind, usd,"
            " origem, ref) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ts, "decisao_fechada", "jev-1.13.0", 0, 600, 80, ms, int(ok), "jev", erro, usd, "decisao_fechada",
             f"{origem}:{run}"))

    def rodar(self, tmp: Path, *args: str) -> tuple[int, dict[str, Any]]:
        self.db.close()
        saida = tmp / "leitura.json"
        codigo = leitura.main(["--db", str(self.caminho), "--json", str(saida), "--desde", "2026-10-03T15:30:00Z",
                               *args])
        return codigo, json.loads(saida.read_text(encoding="utf-8"))


@pytest.fixture
def banco(tmp_path: Path) -> Banco:
    return Banco(tmp_path)


def _sombra_limpa(b: Banco) -> None:
    """Três comandos: r-1 com R2 respondida e R3 abaixo do limiar; r-2 com R2 abstendo-se; r-3 recusado pela privacidade
    antes do POST (sem linha em `ai_calls`)."""
    b.sombra("r-1", "ch1", escolha=A, conf=0.7, probs={A: 0.9, B: 0.05, NENHUMA: 0.05}, usd=0.00003, ms=420.0)
    b.sombra("r-1", "ch1", pergunta="intencao_desempate", escolha=None, conf=0.5, probs={A: 0.6, B: 0.4},
             fallback="abaixo_do_limiar", ms=420.0)
    b.chamada("r-1", ms=420)
    b.sombra("r-2", "ch2", escolha=NENHUMA, conf=0.95, probs={NENHUMA: 0.95, A: 0.05}, usd=0.00002, ms=380.0)
    b.chamada("r-2", usd=0.00002, ms=380)
    b.sombra("r-3", "ch3", escolha=None, conf=None, fallback="privacidade", motivo="c7_gatilho")
    b.sombra("r-0", "ch0", ts=ANTES)                                    # antes do T_on: fora
    b.sombra("abc123", "ch4", origem="curador", classe="C0", pergunta="curador_triagem", escolha="opt:manter",
             probs={"opt:manter": 0.9}, ref="a" * 64)                    # outra origem: só nas contagens


def test_sombra_limpa_da_intencao(banco: Banco, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _sombra_limpa(banco)
    codigo, rel = banco.rodar(tmp_path)
    assert codigo == 0 and rel["veredito"] == "OK", rel["problemas"]
    s, i = rel["sombra"], rel["sombra"]["intencao"]
    assert s["por_origem_classe_modo"] == {"intencao/C3/shadow": 4, "curador/C0/shadow": 1}
    assert (i["linhas"], i["chamadas"], i["comandos"]) == (4, 3, 3)
    assert i["por_pergunta"]["R2"] == {"pedidos": 3, "respondidas": 2, "abstencoes": 1, "fallbacks": 1}
    assert i["por_pergunta"]["R3"] == {"pedidos": 1, "respondidas": 0, "abstencoes": 0, "fallbacks": 1}
    assert i["fallbacks"] == {"abaixo_do_limiar": 1, "privacidade": 1}
    assert i["motivos_de_privacidade"] == {"c7_gatilho": 1}
    # a porta mede a probabilidade da escolha (31.19): confiança 0,7 e P(escolha) 0,9 é resposta válida
    assert i["probabilidade_da_escolha"]["n"] == 2 and i["probabilidade_da_escolha"]["acima_do_limiar"] == 2
    assert i["confianca"] == {"n": 3, "min": 0.5, "p50": 0.7, "p95": 0.95, "max": 0.95, "acima_do_limiar": 1,
                              "faixas": {">= 0.50": 1, ">= 0.70": 1, ">= 0.85": 1}}
    assert i["maior_probabilidade"]["n"] == 3 and i["maior_probabilidade"]["acima_do_limiar"] == 2
    g = rel["gasto"]
    assert g["chamadas"] == 2 and g["usd"] == pytest.approx(0.00005) and g["usd_por_chamada"] == pytest.approx(0.000025)
    assert (g["ms_p50"], g["ms_p95"]) == (380.0, 420.0)
    assert i["usd_na_sombra"] == pytest.approx(0.00005)
    assert rel["cruzamento"] == {"chamadas_postadas_na_sombra": 2, "linhas_em_ai_calls": 2, "bate": True}
    linhas = capsys.readouterr().out.strip().splitlines()
    assert len(linhas) == 10 and linhas[0].startswith("1. ") and "nenhuma violação" in linhas[-1]


def test_texto_em_qualquer_coluna_falha_fechado_sem_imprimir_o_valor(banco: Banco, tmp_path: Path,
                                                                      capsys: pytest.CaptureFixture[str]) -> None:
    _sombra_limpa(banco)
    banco.sombra("r-9", "ch9", escolha=TEXTO_DA_PESSOA, probs={A: 0.9})              # escolha que não é id opaco
    banco.sombra("r-9", "ch9", pergunta="intencao_desempate", probs={TEXTO_DA_PESSOA: 0.9})  # texto na chave
    banco.sombra("r-9", "ch9", ref=TEXTO_DA_PESSOA)                                   # texto no ref
    banco.sombra("r-9", "ch9", fallback="privacidade", escolha=None, conf=None, motivo=TEXTO_DA_PESSOA)
    banco.sombra("r-9", "ch9", probs="não é json")
    banco.sombra("r-9", "ch9", fallback=TEXTO_DA_PESSOA, escolha=None, conf=None)     # texto no motivo do fallback
    banco.chamada("r-9", ok=False, erro=TEXTO_DA_PESSOA)                              # e no erro da chamada
    codigo, rel = banco.rodar(tmp_path)
    assert codigo == 1 and rel["veredito"] == "FALHOU"
    v = rel["sombra"]["violacoes"]
    assert v["formato:escolha"] == 1 and v["formato:probabilidades"] == 2 and v["formato:ref"] == 1
    assert v["formato:motivo_privacidade"] == 1 and v["formato:fallback_reason"] == 1
    assert rel["sombra"]["intencao"]["fallbacks"]["(fora do vocabulário)"] == 1
    assert rel["gasto"]["falhas"] == {"(fora do vocabulário)": 1}
    assert v["texto_em:escolha"] == 1 and v["texto_em:ref"] == 1 and v["texto_em:motivo_privacidade"] == 1
    despejo = json.dumps(rel, ensure_ascii=False) + capsys.readouterr().out
    assert "girassol" not in despejo and "não é json" not in despejo        # o valor achado nunca sai
    assert "girassol" not in (tmp_path / "leitura.json").read_text(encoding="utf-8")


def test_coluna_nova_sem_conferencia_falha_fechado(banco: Banco, tmp_path: Path) -> None:
    banco.db.execute("ALTER TABLE decisao_fechada_sombra ADD COLUMN comando TEXT")
    banco.sombra("r-1", "ch1")
    codigo, rel = banco.rodar(tmp_path)
    assert codigo == 1 and rel["sombra"]["violacoes"] == {"coluna_desconhecida:comando": 1}


def test_resposta_que_a_porta_nao_deixaria_passar_e_problema(banco: Banco, tmp_path: Path) -> None:
    banco.sombra("r-1", "ch1", escolha=A, conf=0.95, probs={A: 0.6, NENHUMA: 0.4})   # respondida com P(escolha) 0,6
    banco.sombra("r-2", "ch2", escolha=A, conf=0.95, probs={B: 0.9})                 # sem a probabilidade da escolha
    codigo, rel = banco.rodar(tmp_path)
    assert codigo == 1
    assert rel["sombra"]["violacoes"] == {"porta:respondida_abaixo_do_limiar": 1,
                                          "porta:respondida_sem_probabilidade_da_escolha": 1}


def test_sem_linha_da_intencao_e_sem_amostra(banco: Banco, tmp_path: Path) -> None:
    banco.sombra("r-0", "ch0", ts=ANTES)
    banco.sombra("abc", "ch4", origem="curador", classe="C0", pergunta="curador_triagem", escolha="opt:manter",
                 probs={"opt:manter": 0.9}, ref="b" * 64)
    codigo, rel = banco.rodar(tmp_path)
    assert codigo == 2 and rel["veredito"] == "SEM AMOSTRA"
    assert rel["sombra"]["intencao"]["linhas"] == 0 and rel["gasto"]["chamadas"] == 0


def test_chamada_do_curador_nao_entra_no_gasto_da_intencao(banco: Banco, tmp_path: Path) -> None:
    banco.sombra("r-1", "ch1", usd=0.00003)
    banco.chamada("r-1")
    banco.chamada("abc", origem="curador", usd=0.5)
    codigo, rel = banco.rodar(tmp_path)
    assert codigo == 0 and rel["gasto"]["chamadas"] == 1 and rel["gasto"]["usd"] == pytest.approx(0.00003)


def test_o_limiar_vem_do_codigo_e_o_percentil_e_o_dos_outros_scripts() -> None:
    from app.planning.decisao_fechada.sombra import _p95

    assert leitura.LIMIAR == 0.85
    for n in range(1, 61):
        valores = [float((i * 37) % 101) for i in range(n)]
        assert leitura.percentil(valores, 95) == _p95(valores)
    assert leitura.percentil([421.96, 437.34, 510.23, 552.80], 50) == 437.34


def test_abre_so_leitura_e_nao_cria_banco(tmp_path: Path, banco: Banco) -> None:
    with pytest.raises(SystemExit, match="banco não encontrado"):
        leitura.main(["--db", str(tmp_path / "nao-existe.sqlite3")])
    assert not (tmp_path / "nao-existe.sqlite3").exists()
    banco.db.close()
    conn = leitura.abrir(banco.caminho)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM decisao_fechada_sombra")
    finally:
        conn.close()
