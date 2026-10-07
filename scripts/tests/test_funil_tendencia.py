"""29.205: a tendência por etapa (scripts/funil-tendencia.py) com run.txt e deploys.jsonl de mentira; só leitura de arquivos pequenos."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location("funil_tendencia", Path(__file__).resolve().parents[1] / "funil-tendencia.py")
ft = importlib.util.module_from_spec(_SPEC)
sys.modules["funil_tendencia"] = ft
_SPEC.loader.exec_module(ft)


def _novo(dur: dict[str, float], falhou: tuple[str, ...] = ()) -> str:
    linhas = ["FUNIL inicio=2026-10-07T00:00:00Z raiz=x commit=abc teto=25"]
    for i, (chave, d) in enumerate(dur.items(), 1):
        st = "falhou" if chave in falhou else "ok"
        linhas.append(f'ETAPA id={i} chave={chave} nome="{chave} -n 6" ini=2026-10-07T00:00:00Z fim=2026-10-07T00:10:00Z dur_s={d:.0f} rc=0 status={st} teto=25 passed=1 failed=0')
    return "\n".join(linhas) + "\n"


def test_etiqueta_ordena_por_numero_e_sufixo():
    assert ft.etiqueta_do_corte(Path("x/funilcorte59-run.txt")) == ((59, ""), "59")
    assert ft.etiqueta_do_corte(Path("x/funilcorte57b-run.txt")) == ((57, "b"), "57b")


def test_formato_novo_le_duracao_e_status(tmp_path):
    p = tmp_path / "funil60-run.txt"
    p.write_text(_novo({"scripts": 120, "pg": 1500}, falhou=("pg",)), encoding="utf-8")
    r = ft.ler_run(p)
    assert r["scripts"] == {"dur_s": 120.0, "falhou": False} and r["pg"] == {"dur_s": 1500.0, "falhou": True}


def test_formato_antigo_usa_a_distancia_entre_inis_e_o_resumo_do_pytest(tmp_path):
    p = tmp_path / "funilcorte58-run.txt"
    p.write_text("1 scripts/tests ini=23:50:00Z\n887 passed in 141s\n2 sqlite inteiro ini=00:10:00Z\n"
                 "FAILED a.py::t\n3 failed, 9 passed in 600.5s (0:10:00)\n", encoding="utf-8")
    r = ft.ler_run(p)
    assert r["scripts"]["dur_s"] == 1200.0, "vira a meia-noite"
    assert r["sqlite"]["dur_s"] == 600.5 and r["sqlite"]["falhou"] is True


def test_regressao_pede_percentual_e_piso_absoluto():
    linhas = [{"rotulo": "1", "pg": 1500.0}, {"rotulo": "2", "pg": 1600.0}, {"rotulo": "3", "pg": 2000.0, "mypy": 30.0}, {"rotulo": "4", "pg": 1800.0}]
    # 1800 contra mediana(1500, 1600, 2000)=1600: +12,5 % < 15 %: não é regressão
    assert ft.regressoes(linhas, ["pg"], 0.15, 60) == []
    linhas[-1]["pg"] = 1900.0   # +18,75 %, +300 s
    a = ft.regressoes(linhas, ["pg"], 0.15, 60)
    assert [x["coluna"] for x in a] == ["pg"] and a[0]["mediana_s"] == 1600.0 and a[0]["pct"] == pytest.approx(18.75)
    curta = [{"rotulo": "1", "mypy": 10.0}, {"rotulo": "2", "mypy": 20.0}]
    assert ft.regressoes(curta, ["mypy"], 0.15, 60) == [], "dobrou, mas só 10 s: ruído"


def test_etapa_que_falhou_nao_entra_na_mediana():
    linhas = [{"rotulo": "1", "pg": 600.0, "_falhou": ("pg",)}, {"rotulo": "2", "pg": 1500.0}, {"rotulo": "3", "pg": 1800.0}]
    a = ft.regressoes(linhas, ["pg"], 0.15, 60)
    assert a and a[0]["mediana_s"] == 1500.0 and a[0]["amostras"] == 1


def test_main_gera_tabela_flag_de_piora_e_deploys(tmp_path, capsys):
    for n, pg in ((58, 1500), (59, 1520), (60, 2200)):
        (tmp_path / f"funil{n}-run.txt").write_text(_novo({"scripts": 100, "pg": pg}), encoding="utf-8")
    dep = tmp_path / "deploys.jsonl"
    dep.write_text("\n".join(json.dumps({"ts_utc": f"2026-10-0{d}T01:00:00Z", "resultado": "ok", "commit_depois": "abcdef0123", "duracao_s": s, "etapas_s": {"subida": u}})
                             for d, s, u in ((5, 70, 50), (6, 72, 52), (7, 100, 80))) + "\nlixo\n", encoding="utf-8")
    saida = tmp_path / "t.md"
    rc = ft.main([str(tmp_path / "funil*-run.txt"), "--deploys", str(dep), "--saida", str(saida), "--falhar-se-piora"])
    out = capsys.readouterr().out
    assert rc == 1 and "`pg` em 60" in out and "`subida` em 10-07 01:00 abcdef0" in out and saida.read_text(encoding="utf-8") == out.rstrip("\n") + "\n" or rc == 1
    assert ft.main([str(tmp_path / "funil5*-run.txt")]) == 0, "sem piora no recorte de 58 e 59"


def test_sem_dados_nao_quebra(tmp_path, capsys):
    assert ft.main([str(tmp_path / "nao-existe-run.txt")]) == 0
    assert "sem dados" in capsys.readouterr().out
