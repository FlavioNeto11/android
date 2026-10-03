"""`scripts/jev-braco-offline-intencao.py`: o braço offline do Jev (31.11), parte R2 e R3 (intenção, C3), num banco
sintético.

Prova `simulated`: banco SQLite temporário migrado, fluxos ativos como catálogo, a sombra da intenção do runtime gravando
com um `DecisorFalso` e um transporte FALSO no lugar do Jev (nada de rede). Confere: só o caso com hash vai (decisão da
orquestradora, 03/10 19:33Z); a linha sem hash fica contada; inglês e português saem com o mesmo estado; o `--enviar`
recusa abaixo de 10 comandos e antes do item 21; o hash registrado no envio é o do caso; a saída não leva o comando.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import DecisaoFechadaCfg  # noqa: E402
from app.db import Database  # noqa: E402
from app.modules.context_retrieval.domain.model import ProviderUsage  # noqa: E402
from app.planning.decisao_fechada.contrato import ID_NENHUMA, RespostaDeDecisao  # noqa: E402
from app.planning.decisao_fechada.decisores import DecisorFalso, DecisorJev  # noqa: E402
from app.planning.decisao_fechada.intencao import PERGUNTA_CATALOGO, ConsumidorDeIntencao, id_opaco  # noqa: E402
from app.planning.decisao_fechada.porta import Porta  # noqa: E402
from app.planning.decisao_fechada.sombra import RepositorioDeSombra, observador_de_sombra  # noqa: E402
from app.taskqueue.lote_intencao import INSTRUCOES_PT, LoteOffline  # noqa: E402
from app.taskqueue.sombra_intencao import SombraDaIntencao  # noqa: E402

_spec = importlib.util.spec_from_file_location("jev_braco_offline_intencao",
                                               ROOT / "scripts" / "jev-braco-offline-intencao.py")
lote = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = lote
_spec.loader.exec_module(lote)  # type: ignore[union-attr]

SEGREDO = "girassol"                    # palavra do comando que NUNCA pode aparecer na saída do braço
TS = "2026-01-01T00:00:00.000Z"
PLANO = {"summary": "Abrir o feed", "app_id": "instagram", "parameters": {},
         "steps": [{"key": "abrir", "title": "Abrir o feed", "goal": "abrir o feed",
                    "postcondition": {"kind": "app_foreground", "value": "instagram", "description": "app aberto"}}],
         "planner": {"provider": "fluxo", "model": "m", "simulated": True}}
FLUXOS = {"f-feed": "abra o feed do instagram", "f-reels": "abra os reels do instagram"}


class Mundo:
    """Dois fluxos ativos (o catálogo), um aparelho e a sombra da intenção do runtime gravando no banco."""

    def __init__(self, tmp: Path) -> None:
        self.caminho = tmp / "lote.sqlite3"
        self.db = Database(self.caminho)
        self.db.migrate()
        self.db.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('instagram','Instagram',"
                        "'com.instagram.android',0)")
        for fid, modelo in FLUXOS.items():
            self.db.execute("INSERT INTO flows(id, name, match_key, command_template, plan, app_id, status, uses,"
                            " created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                            (fid, f"Fluxo {fid}", modelo, modelo, json.dumps(PLANO), "instagram", "active", 0, TS))
        self.db.execute("INSERT INTO instances(id, idx, avd_name, console_port, system_port, mjpeg_port,"
                        " chromedriver_port) VALUES ('android-01', 1, 'a1', 19001, 19101, 19201, 19301)")
        self.db.execute("INSERT INTO events(ts, kind, level, message) VALUES (?, 'log', 'info', 'início')", (TS,))
        self.config = tmp / "config.yaml"
        self.config.write_text("skills:\n  enabled: false\nai:\n  flows: true\n", encoding="utf-8")
        self.lote = LoteOffline(self.db, skills_ligadas=False, fluxos_ligados=True)
        decisor = DecisorFalso({PERGUNTA_CATALOGO: RespostaDeDecisao(
            escolha=id_opaco("flow:f-feed"), probabilidades={id_opaco("flow:f-feed"): 0.9, ID_NENHUMA: 0.1},
            confianca=0.9)}, postado=True)
        self.sombra = RepositorioDeSombra(self.db)
        self.porta = Porta(decisor, cfg=DecisaoFechadaCfg(enabled=True, consumidores={"intencao": "shadow"}),
                           observador=observador_de_sombra(self.sombra))

    def viva(self, run_id: str, comando: str) -> None:
        foto = {"alvos": [{"instance_id": "android-01", "profile_id": None}],
                "command_sem_destinos": self.lote.runs.sem_destinos(comando)}
        self.db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, instance_ids, created_at, targets,"
                        " app_ids) VALUES (?,?,?,?,?,?,?,?,?)",
                        (run_id, f"k-{run_id}", comando, "execute", "planned", json.dumps(["android-01"]), TS,
                         json.dumps(foto), json.dumps(["instagram"])))
        SombraDaIntencao(ConsumidorDeIntencao(self.porta, self.sombra), resolver=self.lote.resolver,
                         catalogo=self.lote.catalogo)._observar(run_id, lambda: self.lote.runs.dados_da_sombra(run_id))
        self.porta.aguardar_sombras()

    def argv(self, tmp: Path, *extra: str) -> list[str]:
        return ["--db", str(self.caminho), "--config", str(self.config), "--json", str(tmp / "saida.json"),
                "--md", str(tmp / "saida.md"), *extra]

    def fechar(self) -> None:
        self.porta.encerrar()
        self.db.close()


@pytest.fixture
def mundo(tmp_path: Path) -> Any:
    m = Mundo(tmp_path)
    m.viva("run-1", f"abra o feed do instagram e curta o post da {SEGREDO}")
    m.viva("run-2", "abra os reels do instagram agora")
    m.db.execute("UPDATE decisao_fechada_sombra SET estado_hash=NULL WHERE run_id='run-2'")    # anterior à 086
    yield m
    m.fechar()


class TransporteFalso:
    """No lugar do Jev: escolhe o feed e mede um custo fixo; guarda o estado e as instruções de cada envio."""

    model = "jev-falso"

    def __init__(self) -> None:
        self.envios: list[tuple[dict[str, object], dict[str, str]]] = []

    def available(self) -> tuple[bool, str]:
        return True, "ok"

    def consultar(self, estado: Mapping[str, object], perguntas: Mapping[str, Mapping[str, object]], *,
                  timeout_s: float) -> tuple[Mapping[str, object], ProviderUsage]:
        self.envios.append((dict(estado), {pid: str(p["instructions"]) for pid, p in perguntas.items()}))
        escolha = id_opaco("flow:f-feed")
        resposta = {pid: {"type": "choice", "choice": escolha, "probabilities": {escolha: 0.9, ID_NENHUMA: 0.1},
                          "confidence": 0.9} for pid in perguntas}
        return resposta, ProviderUsage(input_tokens=100, output_tokens=5, cost_usd=0.00005, latency_ms=400.0)


def _saida(tmp: Path) -> dict[str, Any]:
    return json.loads((tmp / "saida.json").read_text(encoding="utf-8"))


def test_seco_so_o_hash_vai_e_a_linha_sem_hash_fica_contada(mundo: Mundo, tmp_path: Path) -> None:
    assert lote.main(mundo.argv(tmp_path, "--desde", "2026-10-03T15:29:51Z")) == 0
    r = _saida(tmp_path)
    assert r["enviado"] is False and r["custo"]["nivel"].startswith("not_run")
    assert r["casos"]["por_salvaguarda"] == {"c": 1} and r["casos"]["enviaveis"] == 1
    assert r["casos"]["fora"] == {"anterior_a_086": 1}
    assert r["salvaguarda_b"]["libera_envio"] is False and r["salvaguarda_b"]["codigo_igual"] is False
    assert r["pedidos_secos"] == 2                                      # inglês e português do MESMO caso
    assert set(r["idiomas"]) == {"en", "pt"} and set(r["idiomas"]["en"]) == {"instagram"}
    assert {(l["run_id"], l["idioma"]) for l in r["linhas"]} == {("run-1", "en"), ("run-1", "pt")}
    assert all(l["sombra"] == id_opaco("flow:f-feed") for l in r["linhas"])
    assert "acompanhamento" in r["aviso"]
    for arquivo in ("saida.json", "saida.md"):
        texto = (tmp_path / arquivo).read_text(encoding="utf-8")
        assert SEGREDO not in texto and "curta o post" not in texto     # nem o comando nem o estado saem


def test_desde_e_comparado_por_instante(mundo: Mundo, tmp_path: Path) -> None:
    assert lote.main(mundo.argv(tmp_path, "--desde", "2026-10-03T15:29:51.000Z")) == 0        # o mesmo instante


def test_enviar_abaixo_de_dez_comandos_nao_chama_ninguem(mundo: Mundo, tmp_path: Path,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lote.braco, "decisor_real", lambda teto: pytest.fail("não podia montar o decisor"))
    with pytest.raises(SystemExit, match="menos que 10"):
        lote.main(mundo.argv(tmp_path, "--enviar", "--teto", "0.05"))


def test_enviar_manda_o_mesmo_estado_em_ingles_e_portugues(mundo: Mundo, tmp_path: Path,
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    transporte = TransporteFalso()

    def real(teto: Any) -> DecisorJev:
        return DecisorJev(transporte, conferir_gasto=teto.conferir, registrar=teto.registrar)

    monkeypatch.setattr(lote, "MIN_COMANDOS_REAIS", 1)
    monkeypatch.setattr(lote.braco, "decisor_real", real)
    assert lote.main(mundo.argv(tmp_path, "--enviar", "--teto", "0.05")) == 0
    assert len(transporte.envios) == 2
    (estado_en, instr_en), (estado_pt, instr_pt) = transporte.envios
    assert estado_en == estado_pt                                       # só a instrução muda entre os idiomas
    assert instr_pt == {PERGUNTA_CATALOGO: INSTRUCOES_PT[PERGUNTA_CATALOGO]} and instr_en != instr_pt
    r = _saida(tmp_path)
    assert r["enviado"] is True and r["interrompido"] is None
    assert r["custo"]["chamadas"] == 2 and r["custo"]["em_ai_calls"] is False
    assert r["en_x_pt"]["escolha"] == {"comparaveis": 1, "iguais": 1, "taxa": 1.0}
    assert r["lote_x_sombra"]["escolha"]["iguais"] == 1


def test_hash_registrado_diferente_do_caso_interrompe(mundo: Mundo) -> None:
    from app.taskqueue.lote_intencao import consumidor_do_lote, ler_lote  # noqa: PLC0415

    leitura = ler_lote(mundo.lote, consumidor_do_lote(mundo.porta, mundo.db))
    [caso] = leitura.casos
    seco = lote.braco.DecisorSeco()
    registros, interrompido = lote.rodar([replace(caso, estado_hash="0" * 64)], seco)
    assert interrompido == "hash_no_envio" and len(seco.pedidos) == 1     # parou depois do 1º envio, antes do português
    assert set(registros) == {("run-1", "en")}


def test_codigo_igual_falha_fechada() -> None:
    assert lote.codigo_igual(ROOT, []) == (False, {})
    igual, detalhe = lote.codigo_igual(ROOT, ["commit-que-nao-existe"])
    assert igual is False and detalhe == {"commit-que-nao-existe": "erro"}


@pytest.mark.parametrize("argv, msg", [
    (["--desde", "2026-10-03T15:00:00Z"], "antes do item 21"),
    (["--desde", "2026-10-03T15:29:50.999Z"], "antes do item 21"),
    (["--desde", "2026-10-03T15:29:51"], "antes do item 21"),          # sem fuso: não se sabe o instante
    (["--desde", "ontem"], "fora do ISO-8601"),
    (["--enviar"], "exige --teto"),
    (["--enviar", "--teto", "0.5"], "exige --teto"),
])
def test_recusas_do_main(argv: list[str], msg: str) -> None:
    with pytest.raises(SystemExit, match=msg):
        lote.main(argv)
