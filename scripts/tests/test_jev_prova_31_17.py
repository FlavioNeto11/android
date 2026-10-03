"""`scripts/jev-prova-31-17.py`: a prova real do 31.17 (sombra C0–C1 do curador), num banco sintético.

Prova `simulated`: banco SQLite temporário migrado (como `test_jev_relatorio_31_10.py`), com linhas de sombra, revisões do
curador e chamadas do Jev montadas aqui. Confere que o script aceita a sombra limpa, aponta origem, classe e dossiê que não
batem, acha texto do dossiê no corpo, ignora o que veio antes do T_on e abre o banco só para leitura.
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
from app.modules.skills.domain.document import content_hash  # noqa: E402

_spec = importlib.util.spec_from_file_location("jev_prova_31_17", ROOT / "scripts" / "jev-prova-31-17.py")
prova = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(prova)  # type: ignore[union-attr]

TEXTO_DA_PESSOA = "a senha do lucas fica no caderno azul"
DEPOIS = "2026-10-03T11:30:00.000Z"
ANTES = "2026-10-03T10:00:00.000Z"


def _dossie(**extra: object) -> dict[str, object]:
    d: dict[str, object] = {
        "versao_do_dossie": 1,
        "item": {"id": "item:licao:L1", "kind": "licao", "ref": "L1", "app": "com.instagram.android",
                 "capability": "OPEN_POST", "app_version": "447", "estado": "candidate", "origem": "manual",
                 "side_effect": False, "human_origin": True, "criado_em": "2026-10-02T12:00:00.000Z"},
        "risco": {"classe": "B", "politica": "dono_em_lote", "razoes": ["texto_de_pessoa"], "fatos": {}},
        "conteudo": {"modelo": "alvo_ausente", "alvo": TEXTO_DA_PESSOA},
        "evidencias": {"total": 2, "incluidas": 2, "lista": [
            {"id": "ev:1", "posicao": "for", "origin_ref": "run:execucao-0001", "run_id": "execucao-0001",
             "aparelho": "android-01", "app_version": "447", "simulated": False, "em": "2026-10-02T12:00:00.000Z"},
            {"id": "ev:2", "posicao": "against", "origin_ref": "run:execucao-0002", "run_id": "execucao-0002",
             "aparelho": "android-02", "app_version": "447", "simulated": False, "em": "2026-10-02T12:00:00.000Z"}]},
        "falhas": [{"id": "falha:1", "falha": "alvo_ausente", "ocorrencias": 4, "estado": "aberto"}],
        "votos": [{"id": "voto:1", "veredito": "contra", "motivo": "a pessoa achou que o alvo mudou de lugar"}],
        "intervencoes": [], "execucoes": ["run:execucao-0001", "run:execucao-0002"],
        "saude": {"rotulo": "degradando", "motivos": [], "dimensoes": []},
    }
    d.update(extra)
    return d


class Banco:
    def __init__(self, tmp: Path) -> None:
        self.caminho = tmp / "prova.sqlite3"
        self.db = Database(self.caminho)
        self.db.migrate()
        self.db.execute("PRAGMA foreign_keys=OFF")
        self._n = 0

    def sombra(self, ref: str, *, origem: str = "curador", classe: str = "C0", modo: str = "shadow",
               escolha: str | None = "opt:manter", fallback: str | None = None, ts: str = DEPOIS, usd: float = 0.0001,
               ms: float = 800.0, chamada: str | None = None) -> None:
        self._n += 1
        probs = json.dumps({"opt:manter": 0.8, "opt:revisar": 0.2}) if fallback is None else None
        self.db.execute(
            "INSERT INTO decisao_fechada_sombra(ts, chamada, origem, classe, modo, pergunta_id, escolha, probabilidades,"
            " confianca, usd, tokens, ms, fallback_reason, ref) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ts, chamada or f"ch-{self._n}", origem, classe, modo,
             "curador_triagem" if origem == "curador" else "intencao_catalogo", escolha if fallback is None else None,
             probs, 0.8 if fallback is None else None, usd, 120, ms, fallback, ref))

    def revisao(self, dossie: dict[str, object], *, dossie_hash: str | None = None) -> str:
        self._n += 1
        hash_ = dossie_hash or content_hash(dossie)
        self.db.execute(
            "INSERT INTO learning_reviews(id, created_at, item_ref, item_kind, gatilho, dossie_hash, dossie, template_id,"
            " template_versao, simulated, validade) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (f"lr-{self._n}", DEPOIS, "licao:L1", "licao", "teste", hash_, json.dumps(dossie), "curador", "v1", 0, "ok"))
        return hash_

    def chamada(self, ref: str, *, ok: bool = True, ts: str = DEPOIS, usd: float = 0.0001, ms: int = 800,
                erro: str | None = None) -> None:
        self.db.execute(
            "INSERT INTO ai_calls(ts, role, model, tier, input_tokens, output_tokens, ms, ok, provider, error_kind, usd,"
            " origem, ref) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ts, "decisao_fechada", "jev-1.13.0", 0, 120, 0, ms, int(ok), "jev", erro, usd, "decisao_fechada",
             f"curador:{ref}"))

    def rodar(self, tmp: Path, *args: str) -> tuple[int, dict[str, Any]]:
        self.db.close()
        saida = tmp / "prova.json"
        codigo = prova.main(["--db", str(self.caminho), "--json", str(saida), *args])
        return codigo, json.loads(saida.read_text(encoding="utf-8"))


@pytest.fixture
def banco(tmp_path: Path) -> Banco:
    return Banco(tmp_path)


def test_sombra_limpa_do_curador_confere(banco: Banco, tmp_path: Path) -> None:
    ref = banco.revisao(_dossie())
    banco.sombra(ref)
    banco.chamada(ref)
    codigo, rel = banco.rodar(tmp_path)
    assert codigo == 0 and rel["veredito"] == "OK", rel["problemas"]
    assert rel["sombra"]["por_origem_modo_classe"] == {"curador/shadow/C0": 1}
    assert rel["corpos"]["hash_confere"] == 1 and rel["corpos"]["corpos_remontados"] == 1
    assert rel["corpos"]["violacoes"] == {}
    assert rel["gasto"]["chamadas"] == 1 and rel["gasto"]["usd"] == pytest.approx(0.0001)
    assert rel["gasto"]["ms_p50"] == 800.0
    assert rel["cruzamento"]["bate"] is True


def test_origem_classe_e_modo_fora_do_31_17_falham(banco: Banco, tmp_path: Path) -> None:
    ref = banco.revisao(_dossie())
    banco.sombra(ref)
    banco.sombra("x" * 64, origem="intencao", classe="C3")
    banco.sombra(ref, modo="on")
    codigo, rel = banco.rodar(tmp_path)
    assert codigo == 1 and rel["veredito"] == "FALHOU"
    assert {"sombra:origem:intencao", "sombra:classe:C3", "sombra:modo:on"} <= set(rel["problemas"])


def test_dossie_que_nao_bate_com_o_ref_nao_vira_corpo(banco: Banco, tmp_path: Path) -> None:
    ref = banco.revisao(_dossie(), dossie_hash="a" * 64)
    banco.sombra(ref)
    banco.chamada(ref)
    codigo, rel = banco.rodar(tmp_path)
    assert codigo == 1 and "corpo:hash_nao_confere" in rel["problemas"]
    assert rel["corpos"].get("corpos_remontados", 0) == 0


def test_o_corpo_remontado_so_leva_o_C0_e_nenhum_texto_do_dossie() -> None:
    dossie = _dossie()
    ref = content_hash(dossie)
    corpo, motivo = prova.corpo_remontado(dossie, ref, "jev-1.13.0", frozenset({"C0", "C1"}))
    assert motivo is None and corpo is not None
    assert prova.conferir_corpo(corpo, dossie, dossie_hash=ref, item_ref="licao:L1") == []
    texto = corpo.decode("utf-8")
    for proibido in (TEXTO_DA_PESSOA, "com.instagram.android", "OPEN_POST", "execucao-0001", "android-01", ref):
        assert proibido not in texto


def test_o_conferente_acha_vazamento_num_corpo_forjado() -> None:
    """O conferente não confia no caminho da porta: um corpo com texto, id ou hash do dossiê é violação."""
    dossie = _dossie()
    ref = content_hash(dossie)
    forjado = json.dumps({"state": {"kind": "licao", "nota": TEXTO_DA_PESSOA}, "model": "jev-1.13.0",
                          "questions": {"q": {"ref": ref, "run": "execucao-0001"}}}).encode("utf-8")
    violacoes = set(prova.conferir_corpo(forjado, dossie, dossie_hash=ref, item_ref="licao:L1"))
    assert {"campo_fora_dos_CAMPOS", "dossie_hash_no_corpo", "texto_do_dossie_no_corpo", "hex_longo_no_corpo"} <= violacoes


def test_so_conta_o_que_veio_depois_do_t_on_e_sem_linha_e_sem_amostra(banco: Banco, tmp_path: Path) -> None:
    ref = banco.revisao(_dossie())
    banco.sombra(ref, ts=ANTES, origem="intencao", classe="C3")
    banco.chamada(ref, ts=ANTES)
    codigo, rel = banco.rodar(tmp_path)
    assert codigo == 2 and rel["veredito"] == "SEM AMOSTRA"
    assert rel["gasto"]["chamadas"] == 0


def test_recusa_antes_do_post_nao_entra_no_cruzamento(banco: Banco, tmp_path: Path) -> None:
    ref = banco.revisao(_dossie())
    banco.sombra(ref)
    banco.chamada(ref)
    banco.sombra(ref, fallback="orcamento", usd=0.0)
    banco.sombra(ref, fallback="rede", usd=0.0)
    banco.chamada(ref, ok=False, erro="rede", usd=0.0, ms=5000)
    codigo, rel = banco.rodar(tmp_path)
    assert codigo == 0, rel["problemas"]
    assert rel["cruzamento"] == {"chamadas_postadas_na_sombra": 2, "linhas_em_ai_calls": 2, "bate": True}
    assert rel["gasto"]["falhas"] == {"rede": 1}
    assert rel["sombra"]["fallbacks"] == {"orcamento": 1, "rede": 1}


def test_o_banco_abre_so_para_leitura(banco: Banco, tmp_path: Path) -> None:
    banco.db.close()
    conn = prova.abrir(banco.caminho)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO ai_calls(ts, role, model) VALUES ('x', 'y', 'z')")
    finally:
        conn.close()
    inexistente = tmp_path / "nao-existe.sqlite3"
    with pytest.raises(SystemExit):
        prova.abrir(inexistente)
    assert not inexistente.exists()
