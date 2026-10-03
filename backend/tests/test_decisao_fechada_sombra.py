"""Sombra da porta `DecisaoFechada` (item 31.5, ADR-069): migração 074, gravação, casamento, agregado, retenção, preço,
livro-caixa e transparência.

Prova `simulated`: `DecisorFalso`, banco de teste e relógio falso. Nada toca rede, chave ou a TypeSafe, e o envio continua
fechado no código (`JEV_RUNTIME_SEND_APPROVED = False`); os testes que precisam de uma porta aberta a abrem com `monkeypatch`.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from app.config import DecisaoFechadaCfg
from app.db import Database
from app.main import create_app
from app.planning import costs, saldos
from app.planning import decisao_fechada as df
from app.planning.decisao_fechada import privacidade, transparencia
from app.planning.decisao_fechada.contrato import (
    ID_NENHUMA, FalhaDeDecisao, PedidoDeDecisao, RespostaDeDecisao, pergunta_choice,
)
from app.planning.decisao_fechada.porta import Porta, RegistroDeDecisao
from app.planning.decisao_fechada.sombra import DESFECHOS, RepositorioDeSombra, observador_de_sombra
from app.util import to_iso

from .conftest import Harness, _dsn_de_teste
from .test_saldos_de_ia import _cfg

OPCOES = {"opt:a": "TEXTO-DA-OPCAO-A", "opt:b": "TEXTO-DA-OPCAO-B"}
ESTADO_SECRETO = "ESTADO-ENVIADO-NAO-PODE-FICAR-NO-BANCO"
T0 = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


class Relogio:
    def __init__(self, inicio: datetime = T0) -> None:
        self.agora = inicio

    def __call__(self) -> datetime:
        return self.agora


def _db(tmp: Path) -> Database:
    db = Database(_dsn_de_teste() or tmp / "sombra.sqlite3")
    db.migrate()
    return db


def _pedido(**kw: object) -> PedidoDeDecisao:
    base: dict[str, object] = dict(
        origem="curador", classe="C1", estado={"licao": ESTADO_SECRETO}, modo="shadow", ref="item-1", step_id="st-1",
        run_id="run-1", perguntas=(pergunta_choice("q1", "Pick one.", OPCOES, limiar=0.8),
                                   pergunta_choice("q2", "Pick another.", OPCOES, limiar=0.8)))
    base.update(kw)
    return PedidoDeDecisao(**base)  # type: ignore[arg-type]


def _resp(escolha: str | None = "opt:a", conf: float = 0.95) -> RespostaDeDecisao:
    return RespostaDeDecisao(escolha=escolha, probabilidades={"opt:a": conf, "opt:b": round(1 - conf, 2)}, confianca=conf)


@pytest.fixture
def aberta(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(privacidade, "JEV_RUNTIME_SEND_APPROVED", True)
    for o in privacidade.ORIGENS:
        monkeypatch.setitem(privacidade.CAMPOS_POR_ORIGEM, o, frozenset({"licao"}))


def _porta(db: Database, decisor: df.DecisorFalso, relogio: Relogio | None = None,
           **consumidores: str) -> tuple[Porta, RepositorioDeSombra]:
    repo = RepositorioDeSombra(db, relogio=relogio or Relogio())
    cfg = DecisaoFechadaCfg(enabled=True, consumidores=consumidores or {"curador": "shadow"})  # type: ignore[arg-type]
    return Porta(decisor, cfg=cfg, observador=observador_de_sombra(repo)), repo


def _linhas(db: Database) -> list[dict]:
    return [dict(r) for r in db.query("SELECT * FROM decisao_fechada_sombra ORDER BY id")]


# ---------------------------------------------------------------- migração 074
def test_migracao_074_cria_as_tabelas_sem_coluna_para_estado_nem_texto(tmp_path: Path) -> None:
    db = _db(tmp_path)
    assert db.one("SELECT 1 AS x FROM schema_migrations WHERE version LIKE ?", ("074%",)) is not None
    cols = {r["name"] for r in db.query("PRAGMA table_info(decisao_fechada_sombra)")} if db.dialect == "sqlite" else {
        r["column_name"] for r in db.query("SELECT column_name FROM information_schema.columns WHERE table_name=?",
                                           ("decisao_fechada_sombra",))}
    assert {"id", "ts", "origem", "classe", "modo", "pergunta_id", "escolha", "probabilidades", "confianca",
            "decisao_real", "desfecho", "usd", "tokens", "ms", "fallback_reason", "run_id", "step_id", "ref"} <= cols
    assert not {c for c in cols if c in ("estado", "texto", "opcoes", "instrucoes", "descricao", "corpo")}
    assert db.scalar("SELECT COUNT(*) FROM decisao_fechada_diario") == 0
    db.close()


# ---------------------------------------------------------------- gravação
def test_shadow_grava_uma_linha_por_pergunta_e_nunca_o_estado_nem_o_texto(aberta: None, tmp_path: Path) -> None:
    db = _db(tmp_path)
    falso = df.DecisorFalso({"q1": _resp("opt:a", 0.95), "q2": _resp("opt:b", 0.9)}, custo_tokens=1200, custo_usd=0.00005)
    porta, _ = _porta(db, falso)
    porta.consultar(_pedido())
    porta.aguardar_sombras()
    linhas = _linhas(db)
    assert [(x["pergunta_id"], x["escolha"], x["fallback_reason"]) for x in linhas] == [
        ("q1", "opt:a", None), ("q2", "opt:b", None)]
    assert all(x["modo"] == "shadow" and x["origem"] == "curador" and x["classe"] == "C1" for x in linhas)
    assert all(x["ref"] == "item-1" and x["step_id"] == "st-1" and x["run_id"] == "run-1" for x in linhas)
    assert json.loads(linhas[0]["probabilidades"]) == {"opt:a": 0.95, "opt:b": 0.05}
    # custo da chamada ÚNICA só na primeira linha: a soma do dia não conta duas vezes
    assert [x["usd"] for x in linhas] == [0.00005, 0.0] and [x["tokens"] for x in linhas] == [1200, 0]
    despejo = json.dumps(linhas, default=str)
    assert ESTADO_SECRETO not in despejo and "TEXTO-DA-OPCAO" not in despejo and "Pick" not in despejo
    assert len(falso.chamadas) == 1
    db.close()


def test_fallback_grava_linha_sem_escolha_e_com_o_motivo(aberta: None, tmp_path: Path) -> None:
    db = _db(tmp_path)
    porta, _ = _porta(db, df.DecisorFalso(falha=FalhaDeDecisao("429")))
    porta.consultar(_pedido())
    porta.aguardar_sombras()
    linhas = _linhas(db)
    assert len(linhas) == 2 and all(x["escolha"] is None and x["fallback_reason"] == "429" for x in linhas)
    db.close()


def test_abaixo_do_limiar_e_escolha_fora_das_opcoes_viram_fallback_registrado(aberta: None, tmp_path: Path) -> None:
    db = _db(tmp_path)
    porta, _ = _porta(db, df.DecisorFalso({"q1": _resp("opt:a", 0.5), "q2": _resp("opt:fora", 0.99)}))
    porta.consultar(_pedido())
    porta.aguardar_sombras()
    motivos = {x["pergunta_id"]: (x["escolha"], x["fallback_reason"]) for x in _linhas(db)}
    assert motivos == {"q1": (None, "abaixo_do_limiar"), "q2": (None, "unknown_choice")}
    db.close()


def test_recusa_de_privacidade_tambem_e_medida_sem_o_estado(tmp_path: Path) -> None:
    db = _db(tmp_path)
    porta, _ = _porta(db, df.DecisorFalso({"q1": _resp()}))
    porta.consultar(_pedido(modo="on"))           # envio fechado no código: recusa inteira
    linhas = _linhas(db)
    assert len(linhas) == 2 and all(x["fallback_reason"] == "privacidade" and x["escolha"] is None for x in linhas)
    assert ESTADO_SECRETO not in json.dumps(linhas, default=str)
    db.close()


def test_modo_off_nao_grava_nada(tmp_path: Path) -> None:
    db = _db(tmp_path)
    repo = RepositorioDeSombra(db)
    porta = Porta(df.DecisorFalso(), cfg=DecisaoFechadaCfg(enabled=False), observador=observador_de_sombra(repo))
    porta.consultar(_pedido())
    assert _linhas(db) == []
    db.close()


def test_repositorio_nao_grava_texto_livre_no_lugar_de_id(tmp_path: Path) -> None:
    db = _db(tmp_path)
    repo = RepositorioDeSombra(db)
    texto = "uma frase com espaços que não é id opaco"
    res = df.ResultadoDeDecisao({"q1": RespostaDeDecisao(escolha=texto, probabilidades={texto: 0.9, "opt:a": 0.1},
                                                         confianca=0.9)})
    repo.registrar(RegistroDeDecisao("curador", "C1", "shadow", "run-1", "st-1", "ref com espaço e texto", res))
    (linha,) = _linhas(db)
    assert linha["escolha"] is None and linha["fallback_reason"] == "unknown_choice"   # nunca conta como acerto
    assert json.loads(linha["probabilidades"]) == {"opt:a": 0.1} and linha["ref"] is None
    assert texto not in json.dumps(linha, default=str)
    db.close()


# ---------------------------------------------------------------- casamento posterior
def test_casa_decisao_real_e_desfecho_por_ref_ou_step_sem_reescrever(aberta: None, tmp_path: Path) -> None:
    db = _db(tmp_path)
    porta, repo = _porta(db, df.DecisorFalso({"q1": _resp("opt:a"), "q2": _resp("opt:b")}))
    porta.consultar(_pedido())
    porta.aguardar_sombras()
    assert repo.casar_decisao_real({"q1": "opt:a", "q2": "opt:a"}, ref="item-1") == 2
    assert repo.casar_decisao_real({"q1": "opt:b"}, ref="item-1") == 0       # a primeira decisão real vale
    assert repo.casar_desfecho("usado_com_sucesso", step_id="st-1", pergunta_id="q1") == 1
    assert repo.casar_desfecho("rebaixado", step_id="st-1") == 1             # só a q2 ainda estava sem desfecho
    por = {x["pergunta_id"]: (x["decisao_real"], x["desfecho"]) for x in _linhas(db)}
    assert por == {"q1": ("opt:a", "usado_com_sucesso"), "q2": ("opt:a", "rebaixado")}
    assert repo.casar_decisao_real({"q1": "opt:a"}, ref="outro") == 0
    with pytest.raises(ValueError):
        repo.casar_desfecho("inventado", ref="item-1")
    with pytest.raises(ValueError):
        repo.casar_decisao_real({"q1": "texto livre"}, ref="item-1")
    with pytest.raises(ValueError):
        repo.casar_desfecho("descartado")
    assert "descartado" in DESFECHOS
    db.close()


# ---------------------------------------------------------------- agregado diário
def _linha(repo: RepositorioDeSombra, relogio: Relogio, escolha: str | None, real: str | None, *,
           motivo: str | None = None, ms: float = 100.0, usd: float = 0.0, pergunta: str = "q1") -> None:
    res = df.ResultadoDeDecisao({pergunta: RespostaDeDecisao(escolha=escolha, confianca=0.9 if escolha else None,
                                                             fallback_reason=motivo)},  # type: ignore[arg-type]
                                usd=usd, ms=ms, fallback_reason=motivo)  # type: ignore[arg-type]
    ref = f"r{relogio.agora.timestamp()}-{escolha}-{real}-{motivo}-{ms}"
    repo.registrar(RegistroDeDecisao("curador", "C1", "shadow", None, None, ref, res))
    if real is not None:
        repo.casar_decisao_real({pergunta: real}, ref=ref)


def test_agregado_diario_conta_concordancia_aceite_errado_e_fallback_a_parte(tmp_path: Path) -> None:
    db = _db(tmp_path)
    rel = Relogio()
    repo = RepositorioDeSombra(db, relogio=rel)
    _linha(repo, rel, "opt:a", "opt:a", ms=100, usd=0.001)                   # concorda
    _linha(repo, rel, "opt:a", "opt:b", ms=200, usd=0.002)                   # aceite errado
    _linha(repo, rel, ID_NENHUMA, "opt:b", ms=300)                           # abstenção: não é aceite
    _linha(repo, rel, None, "opt:a", motivo="429", ms=400)                   # fallback: nunca acerto
    _linha(repo, rel, "opt:a", None, ms=500)                                 # ainda sem decisão real
    assert repo.agregar("2026-10-02", "2026-10-03") == 1
    (dia,) = repo.diario("2026-10-02")
    assert (dia["n"], dia["com_decisao_real"], dia["concordancia"], dia["acima_do_limiar"], dia["aceite_errado"],
            dia["fallbacks"]) == (5, 4, 1, 3, 1, 1)
    assert dia["usd"] == pytest.approx(0.003) and dia["ms_p95"] == 500
    # recalcular é idempotente: o dia é refeito por inteiro, não somado
    repo.agregar("2026-10-02", "2026-10-03")
    assert db.scalar("SELECT COUNT(*) FROM decisao_fechada_diario") == 1
    db.close()


# ---------------------------------------------------------------- retenção
def test_retencao_agrega_antes_de_purgar_e_so_leva_dias_inteiros(tmp_path: Path) -> None:
    db = _db(tmp_path)
    rel = Relogio(T0 - timedelta(days=200))
    repo = RepositorioDeSombra(db, relogio=rel)
    _linha(repo, rel, "opt:a", "opt:a")                       # há 200 dias: vence (corte em 180)
    rel.agora = (T0 - timedelta(days=180)).replace(hour=8)
    _linha(repo, rel, "opt:a", "opt:b", ms=250)               # no dia do corte, mas ANTES da hora dele: o dia é inteiro, fica
    rel.agora = T0 - timedelta(days=2)
    _linha(repo, rel, "opt:a", "opt:a")                       # recente: fica
    rel.agora = T0
    assert repo.aplicar_retencao(180) == 1
    corte = (T0 - timedelta(days=180)).date().isoformat()
    restam = db.query("SELECT ts FROM decisao_fechada_sombra ORDER BY ts")
    assert len(restam) == 2 and all(r["ts"][:10] >= corte for r in restam)
    antigo = repo.diario("2000-01-01", "2026-04-04")
    assert len(antigo) == 1 and antigo[0]["n"] == 1 and antigo[0]["concordancia"] == 1   # o agregado do dia purgado ficou
    recentes = db.scalar("SELECT COUNT(*) FROM decisao_fechada_diario WHERE day >= ?", (corte,))
    assert recentes == 1                                      # só o dia recente (janela de 7) foi agregado de novo
    assert repo.aplicar_retencao(180) == 0                    # idempotente
    assert len(repo.diario("2000-01-01", "2026-04-04")) == 1
    db.close()


async def test_retencao_do_estado_chama_a_sombra_com_o_prazo_da_config(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    assert st.cfg.file.ai.decisao_fechada.retencao_dias == 180
    antigo = RepositorioDeSombra(st.db, relogio=Relogio(T0 - timedelta(days=400)))
    antigo.registrar(RegistroDeDecisao("apps", "C0", "shadow", None, None, "r", df.ResultadoDeDecisao(
        {"q1": RespostaDeDecisao(escolha="opt:a", confianca=0.9)})))
    st._purgar_demais_tabelas("2026-01-01T00:00:00Z")
    assert st.db.scalar("SELECT COUNT(*) FROM decisao_fechada_sombra") == 0
    assert st.db.scalar("SELECT COUNT(*) FROM decisao_fechada_diario") == 1


# ---------------------------------------------------------------- preço e custo
def test_jev_tem_preco_cadastrado_e_o_usd_declarado_vence_os_tokens(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    assert cfg.file.ai.prices["jev-1.13.0"] == [0.042, 0.0, 0.0, 0.0]
    assert costs.usd(cfg.file.ai.prices, "jev-1.13.0", [1_000_000, 0, 0, 5_000_000]) == pytest.approx(0.042)  # saída grátis
    db = _db(tmp_path)
    ts = to_iso(datetime.now(timezone.utc))
    db.execute("INSERT INTO ai_calls(ts, role, model, provider, input_tokens, output_tokens, usd) VALUES (?,?,?,?,?,?,?)",
               (ts, "decide", "jev-1.13.0", "jev", 500_000, 0, 0.5))
    db.execute("INSERT INTO ai_calls(ts, role, model, provider, input_tokens, output_tokens) VALUES (?,?,?,?,?,?)",
               (ts, "decide", "jev-1.13.0", "jev", 1_000_000, 100))
    # 0,50 declarado + 1 M de entrada × 0,042 (a linha sem `usd` cai na conta de tokens)
    assert costs.spent_usd(db, cfg.file.ai.prices, since="2000-01-01T00:00:00.000Z") == pytest.approx(0.542)
    db.close()


# ---------------------------------------------------------------- livro-caixa (ADR-051)
def test_conta_typesafe_nasce_sem_ancora_e_o_gasto_do_jev_cai_nela(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    db = _db(tmp_path)
    assert saldos.conta_do_provedor(cfg, "jev", "jev-1.13.0") == "typesafe"
    assert saldos.conta_do_provedor(cfg, None, "jev-1.13.0") == "typesafe"
    assert saldos.conta_do_provedor(cfg, "anthropic", "claude-sonnet-5") == "anthropic"
    c = saldos.de_uma(db, cfg, "typesafe")
    assert c is not None and c.anchor_balance is None and c.estimated_balance is None and c.state == "unknown"
    assert c.message.startswith("Sem âncora") and not c.em_uso and not c.bloqueia and not c.admin_key_configured
    assert saldos.motivo_de_bloqueio(db, cfg, "typesafe") is None
    ts = to_iso(datetime.now(timezone.utc))
    db.execute("INSERT INTO ai_calls(ts, role, model, provider, input_tokens, output_tokens, usd) VALUES (?,?,?,?,?,?,?)",
               (ts, "decide", "jev-1.13.0", "jev", 1000, 0, 0.25))
    assert saldos.gasto_usd_por_conta(db, cfg, "2000-01-01T00:00:00.000Z").get("typesafe") == pytest.approx(0.25)
    # a primeira recarga registrada É a âncora (base 0), sem leitura de console
    saldos.registrar_recarga(db, cfg, "typesafe", 5.0)
    c = saldos.de_uma(db, cfg, "typesafe")
    assert c is not None and c.anchor_balance == 5.0 and c.anchor_source == "recarga"
    db.close()


async def test_api_de_saldos_lista_typesafe_sem_ancora_e_sem_rede(harness: Harness,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    st = harness.state
    assert st is not None

    def sem_rede(*a: object, **k: object) -> None:
        raise AssertionError("o livro-caixa da TypeSafe não faz chamada de rede")

    # os transportes de REDE de verdade; o `ASGITransport` do próprio teste não passa por eles
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", sem_rede, raising=True)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", sem_rede, raising=True)
    app = create_app(harness.cfg, state=st)
    app.state.poc = st
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/ai/balances")
        assert r.status_code == 200, r.text
        contas = {x["account"]: x for x in r.json()["accounts"]}
        t = contas["typesafe"]
        assert t["anchor_balance"] is None and t["anchor_at"] is None and t["state"] == "unknown"
        assert t["message"].startswith("Sem âncora") and t["in_use"] is False and t["currency"] == "USD"
        assert {"anthropic", "openai", "gemini"} <= set(contas)


# ---------------------------------------------------------------- transparência
def test_aviso_nomeia_a_typesafe_e_as_classes_so_com_consumidor_em_shadow_ou_on() -> None:
    assert transparencia.aviso(None, chave_configurada=True) is None
    assert transparencia.aviso(DecisaoFechadaCfg(enabled=False, consumidores={"curador": "shadow"}),
                               chave_configurada=True) is None                 # enabled falso vence tudo
    assert transparencia.aviso(DecisaoFechadaCfg(enabled=True, consumidores={"curador": "off"}),
                               chave_configurada=True) is None
    cfg = DecisaoFechadaCfg(enabled=True, consumidores={"curador": "shadow", "apps": "on"},
                            classes_permitidas=["C0", "C1"])
    aviso = transparencia.aviso(cfg, chave_configurada=True)
    assert aviso is not None and "TypeSafe" in aviso and "C0, C1" in aviso and "C3" not in aviso
    assert "curador (shadow)" in aviso and "apps (on)" in aviso and "configurada" in aviso
    assert "FECHADO" in aviso                                                 # envio ainda fechado no código
    assert "não configurada" in transparencia.aviso(cfg, chave_configurada=False)  # type: ignore[operator]
    bloco = transparencia.status(cfg, chave_configurada=False)
    assert bloco is not None and bloco["key"] == "não configurada" and bloco["send_approved"] is False


async def test_api_ai_lista_o_jev_e_mostra_a_chave_so_como_configurada(harness: Harness,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    st = harness.state
    assert st is not None
    app = create_app(harness.cfg, state=st)
    app.state.poc = st
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        antes = (await c.get("/api/ai")).json()
        assert antes["decisao_fechada"] is None and "TypeSafe" not in antes["notice"]
        valor_falso = "valor-fabricado-so-para-o-teste"
        monkeypatch.setattr(st.cfg.file.ai, "decisao_fechada",
                            DecisaoFechadaCfg(enabled=True, consumidores={"intencao": "shadow"}))
        monkeypatch.setattr(st.cfg.env, "typesafe_api_key", SecretStr(valor_falso))
        r = await c.get("/api/ai")
        assert r.status_code == 200, r.text
        corpo = r.json()
        assert "TypeSafe" in corpo["notice"] and "Chave da TypeSafe: configurada" in corpo["notice"]
        assert corpo["decisao_fechada"]["key"] == "configurada" and corpo["decisao_fechada"]["consumers"] == {
            "intencao": "shadow"}
        assert valor_falso not in r.text                                       # a chave nunca vai na resposta
        monkeypatch.setattr(st.cfg.env, "typesafe_api_key", None)
        assert (await c.get("/api/ai")).json()["decisao_fechada"]["key"] == "não configurada"


@pytest.mark.parametrize(("consumidores", "classes", "catalogo", "comando"), [
    ({"curador": "shadow"}, None, False, False),                       # o curador manda C0, mesmo com o teto em C3
    ({"curador": "shadow", "apps": "on"}, ["C0", "C1"], False, False),
    ({"intencao": "shadow"}, None, True, True),                        # intenção em shadow: C2 (catálogo) e C3 (comando)
    ({"intencao": "shadow"}, ["C0", "C1", "C2"], True, False),
    ({"intencao": "shadow"}, ["C0", "C1", "C3"], False, True),
    ({"intencao": "shadow"}, ["C0", "C1"], False, False),
    ({"intencao": "on"}, None, True, False),                           # C3 só em shadow (privacidade.C3_MODOS)
    ({"curador": "shadow", "intencao": "shadow"}, None, True, True),
])
def test_aviso_diz_o_que_de_fato_sai_por_combinacao_de_classes(consumidores: dict[str, str], classes: list[str] | None,
                                                               catalogo: bool, comando: bool) -> None:
    cfg = DecisaoFechadaCfg(enabled=True, consumidores=consumidores, classes_permitidas=classes)  # type: ignore[arg-type]
    aviso = transparencia.aviso(cfg, chave_configurada=True)
    assert aviso is not None and "ids e categorias" in aviso
    assert ("nomes e descrições do catálogo" in aviso) is catalogo
    assert ("o comando do dono filtrado (e-mail, telefone, @handle, link e número mascarados; nome fica)" in aviso) is comando
    assert transparencia.o_que_sai(cfg)[0] == "ids e categorias"
