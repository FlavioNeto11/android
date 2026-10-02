"""Item 28.8 — gatilhos de evento, condição e persona (docs/design/pedidos-laco.md §14).

Prova `simulated` (`arquivo::teste`): SQLite, `RunService` de verdade com o planejamento desligado, relógio falso, eventos
gravados à mão em `events` (com `ts` escolhido). NADA aqui prova o ambiente real.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from app.modules.pedidos.domain import gatilhos_dinamicos as gd
from app.modules.pedidos.domain import previa
from app.util import to_iso

from .conftest import Harness
from .test_pedidos_laco import _gatilho, _laco, _ocs, _pedido
from .test_travas import Relogio

UTC = timezone.utc


@pytest_asyncio.fixture
async def h(harness: Harness, monkeypatch) -> Harness:
    monkeypatch.setattr(harness.state.runs, "_spawn_planning", lambda run_id: None)
    return harness


# =============================================================================================== domínio puro
def test_evento_valida_kinds() -> None:
    assert gd.normalizar("evento", {"kinds": ["run.finished", "run.failed", "run.finished"]}) == {
        "kinds": ["run.failed", "run.finished"]}
    assert gd.normalizar("evento", {"kinds": ["run.failed"], "niveis": ["error", "warn"]})["niveis"] == ["warn", "error"]
    for ruim, campo in (({}, "spec.kinds"), ({"kinds": []}, "spec.kinds"), ({"kinds": ["Run Finished"]}, "spec.kinds"),
                        ({"kinds": ["pedido.updated"]}, "spec.kinds"), ({"kinds": ["frame"]}, "spec.kinds"),
                        ({"kinds": [f"k{i}" for i in range(11)]}, "spec.kinds"),
                        ({"kinds": ["run.failed"], "niveis": ["fatal"]}, "spec.niveis")):
        with pytest.raises(gd.SpecInvalida) as e:
            gd.normalizar("evento", ruim, efemeros={"frame"})
        assert e.value.campo == campo


def test_buraco_so_quando_o_primeiro_depois_do_cursor_sumiu() -> None:
    assert gd.buraco(10, None) is None              # log vazio
    assert gd.buraco(10, 11) is None                # o seguinte existe
    assert gd.buraco(10, 5) is None                 # cursor à frente do menor
    assert gd.buraco(10, 15) == (11, 14)
    assert gd.cursor_de_evento("ev:42") == 42 and gd.cursor_de_evento("2026-10-02T00:00:00Z") is None
    assert gd.formatar_cursor_de_evento(7) == "ev:7"


def test_persona_prende_a_proposta_aos_limites() -> None:
    spec = gd.normalizar("persona", {"intervalo_min_s": 3600, "intervalo_max_s": 86400})
    t0 = datetime(2026, 10, 2, 12, tzinfo=UTC)
    assert gd.proxima_visita(anterior_terminada_em=None, criado_em=t0, proposta_s=None, spec=spec, fim_em=None) == t0
    def prox(p):
        return gd.proxima_visita(anterior_terminada_em=t0, criado_em=t0, proposta_s=p, spec=spec, fim_em=None)
    assert prox(None) == t0 + timedelta(days=1), "sem proposta: o máximo"
    assert prox(60) == t0 + timedelta(hours=1), "abaixo do mínimo: o mínimo"
    assert prox(7200) == t0 + timedelta(hours=2)
    assert prox(10 ** 7) == t0 + timedelta(days=1), "acima do máximo: o máximo"
    assert gd.proxima_visita(anterior_terminada_em=t0, criado_em=t0, proposta_s=None, spec=spec,
                             fim_em=t0 + timedelta(hours=5)) is None, "passou de fim_em"
    assert gd.proposta_valida("number", "observado", "7200") == 7200
    assert gd.proposta_valida("number", "incerto", "7200") is None
    assert gd.proposta_valida("text", "observado", "7200") is None
    assert gd.proposta_valida("number", "observado", "-1") is None
    for ruim in ({"intervalo_min_s": 10, "intervalo_max_s": 3600}, {"intervalo_min_s": 7200, "intervalo_max_s": 3600},
                 {"intervalo_min_s": True, "intervalo_max_s": 3600}, {"intervalo_max_s": 3600}):
        with pytest.raises(gd.SpecInvalida):
            gd.normalizar("persona", ruim)


def _obs(valor, *, tipo="number", situacao="observado", sha=None):
    return gd.Observada(tipo, situacao, valor, sha)


def test_condicao_avalia_e_dispara_por_borda() -> None:
    menor = gd.normalizar("condicao", {"observacao": "preco_total", "op": "<", "valor": 3500})
    assert gd.avaliar(menor, _obs("3499.90"), None) is True
    assert gd.avaliar(menor, _obs("3500"), None) is False
    assert gd.avaliar(menor, _obs("3000", situacao="incerto"), None) is None, "incerto não dá veredito"
    assert gd.avaliar(menor, _obs(None, situacao="ausente"), None) is None
    assert gd.avaliar(menor, _obs("barato", tipo="text"), None) is None
    igual = gd.normalizar("condicao", {"observacao": "status", "op": "==", "valor": "esgotado"})
    assert gd.avaliar(igual, _obs(" esgotado ", tipo="text"), None) is True
    mudou = gd.normalizar("condicao", {"observacao": "pagina", "op": "mudou"})
    assert gd.avaliar(mudou, _obs("a", sha="1"), None) is None, "sem anterior não há mudança a medir"
    assert gd.avaliar(mudou, _obs("b", sha="2"), _obs("a", sha="1")) is True
    assert gd.avaliar(mudou, _obs("a", sha="1"), _obs("a", sha="1")) is False
    assert gd.disparou(True, None) and gd.disparou(True, (False, "o1"))
    assert not gd.disparou(True, (True, "o1")), "verdadeiro seguido de verdadeiro não repete"
    assert not gd.disparou(False, None) and not gd.disparou(None, (False, "o1"))
    assert gd.cursor_de_condicao(gd.formatar_cursor_de_condicao(True, "o_ab12")) == (True, "o_ab12")
    for ruim in ({"observacao": "Preco", "op": "<", "valor": 1}, {"observacao": "p", "op": "~", "valor": 1},
                 {"observacao": "p", "op": "<", "valor": "barato"}, {"observacao": "p", "op": "==", "valor": ""}):
        with pytest.raises(gd.SpecInvalida):
            gd.normalizar("condicao", ruim)


def test_descricao_para_a_pessoa() -> None:
    assert previa.descrever_gatilho(("evento", {"kinds": ["run.failed"]}), "UTC") == "Quando acontecer: run.failed"
    assert previa.descrever_gatilho(("condicao", {"observacao": "preco_total", "op": "<", "valor": 3500.0}),
                                    "UTC") == "Avisa quando preco_total < 3500"
    assert previa.descrever_gatilho(("persona", {"intervalo_min_s": 3600, "intervalo_max_s": 86400}),
                                    "UTC") == "A persona volta entre 1 h e 1 dia"
    assert previa.descrever_gatilho(("evento", {}), "UTC") == "evento", "spec quebrada não derruba a leitura"


# =============================================================================================== laço: evento
def _evento(db, kind: str, ts: datetime, *, run_id: str | None = None, level: str = "info") -> int:
    return int(db.inserted_id("INSERT INTO events(ts, kind, level, run_id, message, data) VALUES (?,?,?,?,?,?)",
                              (to_iso(ts), kind, level, run_id, "texto de terceiro: NÃO pode vazar", None)))


def _maior_evento(db) -> int:
    return int(db.scalar("SELECT COALESCE(MAX(id), 0) FROM events") or 0)


def _cursor(db, gid: str) -> str | None:
    return db.scalar("SELECT cursor FROM pedido_gatilhos WHERE id=?", (gid,))


def _pedido_de_evento(db, r: Relogio, *, kinds=("run.failed",), cursor: str | None = "base", **kw) -> None:
    criado = r.t - timedelta(minutes=1)
    _pedido(db, criado=criado, **kw)
    base = f"ev:{_maior_evento(db)}" if cursor == "base" else cursor
    _gatilho(db, "gev", "ped1", "evento", {"kinds": list(kinds)}, criado, cursor=base)


async def test_evento_sem_linha_de_base_so_fixa_a_base(h: Harness) -> None:
    r = Relogio()
    db = h.state.db
    antigo = _evento(db, "run.failed", r.t - timedelta(minutes=10))
    _pedido_de_evento(db, r, cursor=None)
    _laco(h, r).uma_volta()
    assert _ocs(db) == [], "o histórico anterior à ativação nunca dispara"
    assert _cursor(db, "gev") == f"ev:{max(antigo, _maior_evento(db))}"


async def test_evento_casado_vira_uma_ocorrencia_devida_sem_texto_do_evento(h: Harness) -> None:
    r = Relogio()
    db = h.state.db
    _pedido_de_evento(db, r)
    laco = _laco(h, r)
    ids = [_evento(db, "run.failed", r.t - timedelta(seconds=30)), _evento(db, "run.started", r.t - timedelta(seconds=29)),
           _evento(db, "run.failed", r.t - timedelta(seconds=28))]
    fresco = _evento(db, "run.failed", r.t)        # dentro da margem: fica para a volta seguinte
    res = laco.uma_volta()
    [o] = _ocs(db)
    assert o["origem"] == "evento" and o["gatilho_id"] == "gev" and res.materializadas == 1
    assert o["chave"] == f"ped:ped1:gev:{o['previsto_para']}"
    assert "2 evento(s) run.failed" in o["motivo"] and f"#{ids[0]} a #{ids[2]}" in o["motivo"]
    assert "terceiro" not in o["motivo"], "o motivo nunca leva a message do evento"
    assert _cursor(db, "gev") == f"ev:{ids[2]}", "o cursor para antes do evento dentro da margem"
    assert fresco > ids[2]


async def test_evento_da_execucao_do_proprio_pedido_nao_dispara(h: Harness) -> None:
    r = Relogio()
    db = h.state.db
    _pedido_de_evento(db, r, kinds=("run.created", "run.failed"))
    laco = _laco(h, r)
    _evento(db, "run.failed", r.t - timedelta(seconds=30))
    laco.uma_volta()
    [o] = _ocs(db)
    run_id = db.scalar("SELECT run_id FROM pedido_ocorrencias WHERE id=?", (o["id"],))
    assert run_id, "a primeira ocorrência foi despachada"
    r.avancar(3600)                                  # passa do piso
    _evento(db, "run.failed", r.t - timedelta(seconds=30), run_id=run_id)
    laco.uma_volta()
    assert len(_ocs(db)) == 1, "laço fechado: o evento da própria execução não dispara de novo"
    _evento(db, "run.failed", r.t - timedelta(seconds=20))
    laco.uma_volta()
    assert len(_ocs(db)) == 2, "evento de fora dispara"


async def test_evento_respeita_o_piso_e_coalesce(h: Harness) -> None:
    r = Relogio()
    db = h.state.db
    _pedido_de_evento(db, r)
    laco = _laco(h, r)
    _evento(db, "run.failed", r.t - timedelta(seconds=30))
    laco.uma_volta()
    assert len(_ocs(db)) == 1
    db.execute("UPDATE runs SET status='completed' WHERE pedido_id='ped1'")    # a anterior acabou: sem sobreposição
    r.avancar(60)
    _evento(db, "run.failed", r.t - timedelta(seconds=30))
    _evento(db, "run.failed", r.t - timedelta(seconds=20))
    laco.uma_volta()
    assert len(_ocs(db)) == 1, "dentro do piso de `observar` (900 s): espera"
    r.avancar(900)
    laco.uma_volta()
    ocs = _ocs(db)
    assert len(ocs) == 2 and "2 evento(s)" in ocs[1]["motivo"], "os eventos que esperaram coalescem numa ocorrência"


async def test_evento_que_nao_casa_tambem_avanca_o_cursor(h: Harness) -> None:
    r = Relogio()
    db = h.state.db
    _pedido_de_evento(db, r)
    laco = _laco(h, r)
    outro = _evento(db, "run.started", r.t - timedelta(seconds=30))
    laco.uma_volta()
    assert _ocs(db) == [] and _cursor(db, "gev") == f"ev:{outro}", "sem isso a purga do que não casa pareceria buraco"


async def test_buraco_da_retencao_registra_e_nao_dispara(h: Harness) -> None:
    """O aceite do 28.8 (pedidos-persistentes.md §13): cursor abaixo do menor evento gera o registro do buraco, não
    disparo."""
    r = Relogio()
    db = h.state.db
    velhos = [_evento(db, "run.failed", r.t - timedelta(days=20)) for _ in range(3)]
    _pedido_de_evento(db, r, cursor=f"ev:{velhos[0]}")
    db.execute("DELETE FROM events WHERE id <= ?", (velhos[2],))                      # a retenção passou
    depois = _evento(db, "run.started", r.t - timedelta(seconds=30))                  # existe, mas não casa
    laco = _laco(h, r)
    res = laco.uma_volta()
    assert _ocs(db) == [], "nada dispara pelo buraco"
    assert res.buracos == 1
    mem = db.one("SELECT * FROM pedido_memoria WHERE pedido_id='ped1' AND chave='evento.buraco.gev'")
    assert mem["tipo"] == "pendencia" and f"#{velhos[1]} a #{depois - 1}" in mem["valor"]
    assert _cursor(db, "gev") == f"ev:{depois}"
    assert laco.uma_volta().buracos == 0, "o buraco é registrado uma vez"


async def test_retomar_daqui_refaz_a_linha_de_base(h: Harness) -> None:
    r = Relogio()
    db = h.state.db
    _pedido_de_evento(db, r, estado="pausado")
    laco = _laco(h, r)
    durante = _evento(db, "run.failed", r.t - timedelta(seconds=30))
    laco.acoes.retomar("ped1", "daqui")
    laco.uma_volta()
    assert _ocs(db) == [] and _cursor(db, "gev") == f"ev:{durante}"


async def test_pedido_so_de_evento_encerra_no_fim(h: Harness) -> None:
    r = Relogio()
    db = h.state.db
    _pedido_de_evento(db, r)
    db.execute("UPDATE pedidos SET fim_em=? WHERE id='ped1'", (to_iso(r.t + timedelta(minutes=5)),))
    laco = _laco(h, r)
    laco.uma_volta()
    assert db.scalar("SELECT estado FROM pedidos WHERE id='ped1'") == "ativo"
    r.avancar(600)
    _evento(db, "run.failed", r.t - timedelta(seconds=30))
    laco.uma_volta()
    assert _ocs(db) == [], "depois de fim_em o evento não materializa"
    assert db.scalar("SELECT estado FROM pedidos WHERE id='ped1'") == "encerrado"


# =============================================================================================== laço: persona
def _fechar(db, oid: str, quando: datetime) -> None:
    db.execute("UPDATE pedido_ocorrencias SET estado='concluida', terminada_em=? WHERE id=?", (to_iso(quando), oid))


def _observacao(db, oid: str, nome: str, valor: str | None, *, tipo: str = "number", situacao: str = "observado",
                alvo: str = "android-01", quando: datetime, sha: str | None = None) -> None:
    db.execute("INSERT INTO pedido_observacoes(id, pedido_id, ocorrencia_id, alvo, nome, tipo, situacao, valor, sha256,"
               " capturado_em) VALUES (?,?,?,?,?,?,?,?,?,?)",
               (f"obs-{oid}-{nome}-{alvo}", "ped1", oid, alvo, nome, tipo, situacao, valor, sha, to_iso(quando)))


async def test_persona_visita_na_ativacao_e_volta_pela_proposta(h: Harness) -> None:
    r = Relogio()
    db = h.state.db
    criado = r.t - timedelta(seconds=5)
    _pedido(db, criado=criado)
    _gatilho(db, "gpe", "ped1", "persona", {"intervalo_min_s": 3600, "intervalo_max_s": 86400}, criado)
    laco = _laco(h, r)
    laco.uma_volta()
    [o1] = _ocs(db)
    assert o1["origem"] == "persona" and o1["estado"] in ("devida", "despachada")
    laco.uma_volta()
    assert len(_ocs(db)) == 1, "a visita seguinte espera a anterior fechar"
    db.execute("UPDATE pedido_ocorrencias SET run_id=NULL WHERE id=?", (o1["id"],))
    _fechar(db, o1["id"], r.t)
    _observacao(db, o1["id"], "proxima_visita_s", "60", quando=r.t)          # pede 1 min: preso ao mínimo (1 h)
    laco.uma_volta()
    o2 = _ocs(db)[1]
    assert o2["estado"] == "prevista" and o2["origem"] == "persona"
    assert o2["previsto_para"] == (r.t + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    r.avancar(3600)
    laco.uma_volta()
    assert db.scalar("SELECT estado FROM pedido_ocorrencias WHERE id=?", (o2["id"],)) in ("devida", "despachada")


async def test_persona_sem_proposta_volta_no_maximo(h: Harness) -> None:
    r = Relogio()
    db = h.state.db
    criado = r.t - timedelta(seconds=5)
    _pedido(db, criado=criado)
    _gatilho(db, "gpe", "ped1", "persona", {"intervalo_min_s": 3600, "intervalo_max_s": 7200}, criado)
    laco = _laco(h, r)
    laco.uma_volta()
    [o1] = _ocs(db)
    db.execute("UPDATE pedido_ocorrencias SET run_id=NULL WHERE id=?", (o1["id"],))
    _fechar(db, o1["id"], r.t)
    _observacao(db, o1["id"], "proxima_visita_s", "60", situacao="incerto", quando=r.t)
    laco.uma_volta()
    assert _ocs(db)[1]["previsto_para"] == (r.t + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")


# =============================================================================================== laço: condição
def _pedido_com_condicao(db, r: Relogio) -> None:
    criado = r.t - timedelta(minutes=1)
    _pedido(db, criado=criado)
    # A recorrência (observação) fica para amanhã: aqui só a condição sobre observações já gravadas é medida.
    _gatilho(db, "grec", "ped1", "recorrencia", {"dtstart": "2030-01-01T08:00:00", "rrule": "FREQ=DAILY"}, criado)
    _gatilho(db, "gcond", "ped1", "condicao", {"observacao": "preco_total", "op": "<", "valor": 3500}, criado)


def _ocorrencia(db, oid: str, quando: datetime) -> None:
    db.execute("INSERT INTO pedido_ocorrencias(id, pedido_id, pedido_versao, gatilho_id, previsto_para, chave, origem,"
               " estado, criada_em, terminada_em) VALUES (?,?,1,'grec',?,?,'agenda','concluida',?,?)",
               (oid, "ped1", quando.strftime("%Y-%m-%dT%H:%M:%SZ"), f"ped:ped1:grec:{oid}", to_iso(quando),
                to_iso(quando)))


async def test_condicao_avisa_so_na_borda(h: Harness) -> None:
    r = Relogio()
    db = h.state.db
    _pedido_com_condicao(db, r)
    laco = _laco(h, r)
    _ocorrencia(db, "o1", r.t - timedelta(hours=3))
    _observacao(db, "o1", "preco_total", "3900", quando=r.t - timedelta(hours=3))
    assert laco.uma_volta().condicoes == 0
    assert _cursor(db, "gcond") == "cond:0:o1"
    _ocorrencia(db, "o2", r.t - timedelta(hours=2))
    _observacao(db, "o2", "preco_total", "3400", situacao="incerto", quando=r.t - timedelta(hours=2))
    assert laco.uma_volta().condicoes == 0, "incerto não dá veredito"
    assert _cursor(db, "gcond") == "cond:0:o1"
    _ocorrencia(db, "o3", r.t - timedelta(hours=1))
    _observacao(db, "o3", "preco_total", "3400", quando=r.t - timedelta(hours=1))
    assert laco.uma_volta().condicoes == 1
    mem = db.one("SELECT * FROM pedido_memoria WHERE pedido_id='ped1' AND chave='condicao.gcond'")
    assert mem["tipo"] == "descoberta" and "o3" in mem["valor"]
    assert laco.uma_volta().condicoes == 0, "a mesma ocorrência não avisa duas vezes"
    _ocorrencia(db, "o4", r.t - timedelta(minutes=30))
    _observacao(db, "o4", "preco_total", "3300", quando=r.t - timedelta(minutes=30))
    assert laco.uma_volta().condicoes == 0, "verdadeiro seguido de verdadeiro não repete"
    assert _ocs(db) and all(o["origem"] == "agenda" for o in _ocs(db)), "a condição não cria ocorrência"


# =============================================================================================== API
async def test_api_aceita_e_descreve_os_gatilhos_novos(h: Harness) -> None:
    from .test_pedidos_api import _cliente, _corpo
    h.state.pedidos.relogio = Relogio()
    c = _cliente(h)
    evento = {"tipo": "evento", "spec": {"kinds": ["run.failed"]}}
    persona = {"tipo": "persona", "spec": {"intervalo_min_s": 3600, "intervalo_max_s": 86400}}
    condicao = {"tipo": "condicao", "spec": {"observacao": "preco_total", "op": "<", "valor": 3500}}
    p = c.post("/api/pedidos/previa", json=_corpo(gatilhos=[evento, persona, condicao])).json()
    assert p["valido"], p["bloqueios"]
    for spec, codigo, campo in (({"kinds": ["pedido.updated"]}, "gatilho_invalido", "gatilhos[0].spec.kinds"),
                                ({"kinds": ["frame"]}, "gatilho_invalido", "gatilhos[0].spec.kinds")):
        bloqueios = c.post("/api/pedidos/previa", json=_corpo(gatilhos=[{"tipo": "evento", "spec": spec}])).json()[
            "bloqueios"]
        assert (codigo, campo) in {(b["codigo"], b["campo"]) for b in bloqueios}
    so_condicao = c.post("/api/pedidos/previa", json=_corpo(gatilhos=[condicao])).json()
    assert [b["codigo"] for b in so_condicao["bloqueios"]] == ["condicao_sem_observacao"]
    rapida = {"tipo": "persona", "spec": {"intervalo_min_s": 600, "intervalo_max_s": 86400}}
    [b] = c.post("/api/pedidos/previa", json=_corpo(gatilhos=[rapida])).json()["bloqueios"]
    assert b["codigo"] == "frequencia_abaixo_do_piso" and b["campo"] == "gatilhos[0].spec.intervalo_min_s"
    criado = c.post("/api/pedidos", json={**_corpo(gatilhos=[evento, persona, condicao]),
                                          "idempotency_key": "chave-28-8-gatilhos", "confirmacao": p["confirmacao"]})
    assert criado.status_code in (200, 201), criado.text
    pedido = criado.json()
    textos = sorted(g["descricao"] for g in pedido["gatilhos_resumo"])      # mesma criação: a ordem é a dos ids
    assert textos == ["A persona volta entre 1 h e 1 dia", "Avisa quando preco_total < 3500",
                      "Quando acontecer: run.failed"]
    cursores = {x["tipo"]: x["cursor"] for x in h.state.db.query("SELECT tipo, cursor FROM pedido_gatilhos WHERE"
                                                                     " pedido_id=?", (pedido["id"],))}
    assert str(cursores["evento"]).startswith("ev:"), "a ativação fixa a linha de base dos eventos"
    assert json.loads(h.state.db.scalar("SELECT spec FROM pedido_gatilhos WHERE pedido_id=? AND tipo='condicao'",
                                        (pedido["id"],))) == {"observacao": "preco_total", "op": "<", "valor": 3500.0}


async def test_log_que_volta_refaz_a_base_e_registra(h: Harness) -> None:
    """Banco restaurado de backup: a sequência de `events` regride para baixo do cursor. O gatilho não pode emudecer."""
    r = Relogio()
    db = h.state.db
    _pedido_de_evento(db, r, cursor="ev:99999999")
    laco = _laco(h, r)
    laco.uma_volta()
    assert _ocs(db) == [] and _cursor(db, "gev") == f"ev:{_maior_evento(db)}"
    assert db.one("SELECT tipo FROM pedido_memoria WHERE chave='evento.base.gev'")["tipo"] == "pendencia"
    _evento(db, "run.failed", r.t - timedelta(seconds=30))
    laco.uma_volta()
    assert len(_ocs(db)) == 1, "depois de refeita a base, o gatilho volta a disparar"


async def test_buraco_com_ids_no_formato_real(h: Harness) -> None:
    """Os outros testes usam ids curtos; a produção usa `g`/`o` + 16 hex, que passam pelo filtro de segredo da memória."""
    from app.modules.pedidos.infrastructure.repositorio import novo_id
    r = Relogio()
    db = h.state.db
    gid = novo_id("g")
    velhos = [_evento(db, "run.failed", r.t - timedelta(days=20)) for _ in range(3)]
    criado = r.t - timedelta(minutes=1)
    _pedido(db, criado=criado)
    _gatilho(db, gid, "ped1", "evento", {"kinds": ["run.failed"]}, criado, cursor=f"ev:{velhos[0]}")
    db.execute("DELETE FROM events WHERE id <= ?", (velhos[2],))
    _evento(db, "run.started", r.t - timedelta(seconds=30))
    assert _laco(h, r).uma_volta().buracos == 1
    assert db.one("SELECT tipo FROM pedido_memoria WHERE chave=?", (f"evento.buraco.{gid}",))["tipo"] == "pendencia"


async def test_patch_nao_troca_para_gatilho_novo(h: Harness) -> None:
    from .test_pedidos_api import _cliente, _criar
    h.state.pedidos.relogio = Relogio()
    c = _cliente(h)
    pid = _criar(c, "chave-28-8-patch-01").json()["id"]
    versao = c.get(f"/api/pedidos/{pid}").json()["versao"]
    r = c.patch(f"/api/pedidos/{pid}", json={"versao": versao, "gatilhos": [
        {"tipo": "evento", "spec": {"kinds": ["run.failed"]}}]})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "gatilho_nao_suportado", r.text
