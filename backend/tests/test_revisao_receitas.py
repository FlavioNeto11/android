"""Revisão independente (F8) da frente F3 — receitas, funil medido, desbravador e aproveitamento.

Tudo no harness (`base_console_port: 5640`, provedor simulado, aparelho falso): nada de adb real, IA paga ou rede.

Convenção dos testes que documentam defeito: `xfail(strict=True, raises=DefeitoF8)`. As conferências do cenário são
`assert` comuns — se o cenário deixar de acontecer, o teste FALHA de verdade (AssertionError não é `DefeitoF8`); só a
propriedade desejada, que hoje não vale, levanta `DefeitoF8` e vira xfail. Quando o defeito for corrigido, o xfail
estrito passa a acusar XPASS, e o marcador sai.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.db import dumps, loads
from app.metricas import metricas
from app.models import ControlOwner, InstanceState
from app.modules.learning.domain.causa_do_ausente import CausaDoAusente
from app.planning.provider import Decision, Usage
from app.taskqueue.aproveitamento import aproveitamento
from app.taskqueue.recipes import _MOTIVOS_DO_RETORNO

from .conftest import Harness
from .test_desbravador import _esperando, _espera_s, _liga, _obj, _segura, _status


class DefeitoF8(AssertionError):
    """A propriedade que a F3 promete (docstring/contrato) e o código não entrega."""


def _defeito(condicao: bool, mensagem: str) -> None:
    if not condicao:
        raise DefeitoF8(mensagem)


# ------------------------------------------------------------------ privacidade dos rótulos (C5)
_VOCABULARIO = {
    # `candidata`: a receita em prova (test_receita_candidata) — achada, mas a IA decide e ela só é comparada
    # `herdada`: a chave sem receita herdou a provada de outra chave, como candidata (RA-20, test_receita_heranca)
    "receita.consulta": {"resultado": {"encontrada", "candidata", "ausente", "quarentena", "herdada"}},
    "receita.ausente": {"causa": {c.value for c in CausaDoAusente}},
    "receita.reproducao": {"resultado": {"ok", "divergiu"}},
    "receita.retorno_ia": {"motivo": {m for _, m in _MOTIVOS_DO_RETORNO} | {"outro"}},
    "pathfinder.desfecho": {"resultado": {"aprendeu", "falhou", "expirou", "liberado"}},
    "pathfinder.espera_s": {},
}


def _rotulos_fechados(proibidos: tuple[str, ...] = ()) -> None:
    """Toda série `receita.*`/`pathfinder.*` só tem rótulo do vocabulário fechado — nunca aparelho, execução, @ ou
    texto de tela (C5). `proibidos`: ids concretos do teste que não podem aparecer em lugar nenhum do snapshot."""
    snap = metricas.snapshot()
    series = [s for s in [*snap["contadores"], *snap["distribuicoes"]]
              if s["nome"].startswith(("receita.", "pathfinder."))]
    for serie in series:
        nome, rotulos = serie["nome"], serie["rotulos"]
        assert nome in _VOCABULARIO, nome
        permitido = _VOCABULARIO[nome]
        assert set(rotulos) <= set(permitido), (nome, rotulos)
        for chave, valor in rotulos.items():
            assert valor in permitido[chave], (nome, rotulos)
    texto = json.dumps(series, ensure_ascii=False)
    for p in ("android-", "@", *proibidos):
        assert p not in texto, p


# ------------------------------------------------------------------ cenário comum: aprende em 01, quebra uma receita
async def _aprende_em_01(h: Harness) -> tuple[Any, int]:
    h.cfg.file.ai.recipes = "replay"
    r1 = await h.wait_run(h.run(["android-01"]).id)
    assert r1.status == "completed"
    db = h.state.db                                                          # type: ignore[union-attr]
    n = db.scalar("SELECT COUNT(*) FROM recipes WHERE status='active'")
    assert n >= 3
    return r1, n


def _quebra_open_conversation(h: Harness) -> int:
    """A 1ª ação de `open_conversation` passa a apontar para um alvo que não existe (tela que mudou): reproduzir
    diverge na 1ª ação, com motivo `alvo_ausente`."""
    db = h.state.db                                                          # type: ignore[union-attr]
    receita = db.one("SELECT id, actions FROM recipes WHERE step_key='open_conversation' AND status='active'")
    acoes = loads(receita["actions"])
    acoes[0]["selectors"] = [{"kind": "rid", "rid": "app:id/nao_existe_mais"}]
    acoes[0].pop("scroll", None)
    db.execute("UPDATE recipes SET actions=? WHERE id=?", (dumps(acoes), receita["id"]))
    return int(receita["id"])


def _driven(h: Harness, run_id: str) -> dict[str, Any]:
    return {r["key"]: r["driven_by"] for r in h.state.db.query(             # type: ignore[union-attr]
        "SELECT key, driven_by FROM steps WHERE run_id=?", (run_id,))}


# ================================================================== cenário 2: divergência → IA, sem escalar
async def test_divergencia_volta_a_ia_no_modelo_de_acao_e_o_retorno_e_contado_uma_vez(harness: Harness) -> None:
    """Era defeito F8 (`receita.retorno_ia` nunca contado): o executor passou a contar o retorno UMA vez por
    tentativa, na primeira consulta à IA depois da divergência — sem subir de tier (a IA decide no modelo de ação)."""
    _, n = await _aprende_em_01(harness)
    _quebra_open_conversation(harness)
    harness.ai.calls.clear()
    metricas.limpar()
    run = await harness.wait_run(harness.run(["android-02"]).id)
    assert run.status == "completed" and len(harness.fakes["android-02"].messages) == 1

    # o cenário aconteceu: só AQUELA etapa voltou para a IA; as outras reproduziram sem decisão de IA
    driven = _driven(harness, run.id)
    assert driven["open_conversation"] == "recipe+ai" and driven["send_message"] == "recipe"
    decisoes = [c for c in harness.ai.calls if c["role"] == "decide" and c["instance"] == "android-02"]
    assert {c["step"] for c in decisoes if c["step"] in ("compose_message", "send_message")} == set()
    divergidas = [c for c in decisoes if c["step"] == "open_conversation"]
    assert divergidas
    # a divergência NÃO escala o modelo (config.py desde 7879d86; decisão da evolução de desempenho, 26/09)
    assert all(c["tier"] == 0 for c in divergidas), divergidas
    assert metricas.valor("receita.consulta", resultado="encontrada") == n
    assert metricas.valor("receita.reproducao", resultado="divergiu") == 1
    assert metricas.valor("receita.reproducao", resultado="ok") == n - 1
    _rotulos_fechados((run.id,))

    # a IA foi consultada por causa da divergência: o nome reservado do contrato registra UMA volta, com o motivo,
    # por mais decisões de IA que a etapa tenha pedido depois dela
    assert metricas.total("receita.retorno_ia") == 1, (metricas.total("receita.retorno_ia"), len(divergidas))
    assert metricas.valor("receita.retorno_ia", motivo="alvo_ausente") == 1


# ================================================================== cenário 5: nova tentativa da mesma etapa
async def test_nova_tentativa_conta_consulta_reproducao_e_retorno_por_tentativa(harness: Harness) -> None:
    """A unidade do funil é a TENTATIVA, nas três pontas (revisão F8). Era defeito: a consulta contava por tentativa
    e a reprodução só por etapa (`_after_step` saía cedo em `retry`), então uma nova tentativa virava duas
    consultas para uma reprodução e a divergência da 1ª sumia. Por tentativa a razão fecha: cada receita encontrada
    desemboca em exatamente um veredito de reprodução, e cada divergência que levou a etapa à IA é um retorno."""
    _, n = await _aprende_em_01(harness)
    _quebra_open_conversation(harness)
    harness.cfg.file.ai.cascade_blocked_to_tier1 = False    # o bloqueio forçado aqui é o que o teste exercita: a cascata do 17.10 o absorveria no tier 1
    inner = harness.ai.inner
    decide0 = inner.decide
    falhou = {"feito": False}

    async def decide(req: Any) -> Any:
        # a 1ª decisão de IA depois da divergência desiste da etapa (sem pedir pessoa): `fail_or_retry` → retry
        if req.ctx.instance_id == "android-02" and req.ctx.step_key == "open_conversation" and not falhou["feito"]:
            falhou["feito"] = True
            return Decision(tool="step_blocked", args={"rationale": "teste", "kind": "other",
                                                       "reason": "falha forçada pelo teste", "needs_user": False}), Usage()
        return await decide0(req)

    inner.decide = decide
    metricas.limpar()
    run = await harness.wait_run(harness.run(["android-02"]).id, timeout=60)
    assert run.status == "completed" and falhou["feito"]
    db = harness.state.db                                                   # type: ignore[union-attr]
    etapa = db.one("SELECT id, driven_by FROM steps WHERE run_id=? AND key='open_conversation'", (run.id,))
    tentativas = db.scalar("SELECT COUNT(*) FROM attempts WHERE step_id=?", (etapa["id"],))
    assert tentativas == 2 and etapa["driven_by"] == "recipe+ai"

    encontradas = metricas.valor("receita.consulta", resultado="encontrada")
    reproducoes = metricas.total("receita.reproducao")
    # n etapas elegíveis, uma delas com 2 tentativas: n+1 consultas "encontrada" e n+1 vereditos de reprodução — as
    # duas tentativas de `open_conversation` divergiram (a receita segue quebrada na 2ª) e as outras n-1 reproduziram
    assert encontradas == n + 1 == reproducoes, (encontradas, reproducoes, n, tentativas)
    assert metricas.valor("receita.reproducao", resultado="divergiu") == tentativas
    assert metricas.valor("receita.reproducao", resultado="ok") == n - 1
    # cada divergência levou a sua tentativa de volta à IA: um retorno por tentativa, nem mais nem menos
    assert metricas.total("receita.retorno_ia") == tentativas
    _rotulos_fechados((run.id,))
    # a quarentena continua só com veredito da ETAPA: a tentativa que terminou em `retry` não entrou nela
    falhas = db.scalar("SELECT replay_fail FROM recipes WHERE step_key='open_conversation' AND status='active'")
    assert falhas == 1


# ================================================================== cenário 3: quarentena
async def test_receita_em_quarentena_nao_reproduz_conta_quarentena_e_reaprende(harness: Harness) -> None:
    r1, n = await _aprende_em_01(harness)
    db = harness.state.db                                                   # type: ignore[union-attr]
    db.execute("UPDATE recipes SET status='quarantined' WHERE step_key='open_conversation' AND status='active'")
    harness.ai.calls.clear()
    metricas.limpar()
    run = await harness.wait_run(harness.run(["android-02"]).id)
    assert run.status == "completed" and len(harness.fakes["android-02"].messages) == 1

    # não reproduziu: a IA conduziu a etapa inteira, e o funil diz POR QUE (quarentena, não "nunca aprendida")
    assert harness.ai.count("decide", instance="android-02", step="open_conversation") >= 1
    assert harness.ai.count("decide", instance="android-02", step="send_message") == 0     # as outras seguem por receita
    assert metricas.valor("receita.consulta", resultado="quarentena") == 1
    assert metricas.valor("receita.consulta", resultado="encontrada") == n - 1
    assert metricas.valor("receita.reproducao", resultado="ok") == n - 1
    assert metricas.valor("receita.reproducao", resultado="divergiu") == 0                 # quarentena não é veredito
    assert _driven(harness, run.id)["open_conversation"] == "ai"
    # reaprendida: `save` usa `_ativa` (sem medir), vê que não há ativa e grava a versão seguinte
    versoes = [r["version"] for r in db.query(
        "SELECT version FROM recipes WHERE step_key='open_conversation' AND status='active'")]
    assert versoes == [2]
    # uma consulta por etapa elegível, sem nova tentativa: o próprio save (reaprendizado) não inflou o funil
    etapas = db.scalar("SELECT COUNT(*) FROM steps WHERE run_id=?", (run.id,))
    assert metricas.total("receita.consulta") == etapas
    assert metricas.valor("receita.consulta", resultado="ausente") == etapas - n    # só leitura: nunca viram receita

    # aproveitamento: a receita existia antes (em quarentena) → "outra chave ou quarentena", não "sem cobertura"
    res = aproveitamento(db)
    assert res["totais"]["outra_chave_ou_quarentena"] == 1
    # invariante: etapa marcada "por receita" é sempre etapa comprovada
    assert db.scalar("SELECT COUNT(*) FROM steps WHERE driven_by='recipe' AND status!='succeeded'") == 0
    # privacidade: a leitura exposta em /api/flows/cobertura não carrega id de execução nem de aparelho
    exposto = json.dumps(res, ensure_ascii=False)
    for proibido in (r1.id, run.id, "android-"):
        assert proibido not in exposto, proibido
    _rotulos_fechados((r1.id, run.id))


# ================================================================== cenário 4: desbravador
async def test_lider_so_e_eleito_quando_despachado(harness: Harness) -> None:
    """android-01 é o 1º da fila mas está sob controle manual (a porta `ai_begin` o segura DEPOIS do desbravador):
    não pode virar líder, senão 02 e 03 esperariam um aparelho parado até o teto."""
    _liga(harness)
    devs = harness.state.devices.devices                                    # type: ignore[union-attr]
    devs["android-01"].control = ControlOwner.user
    portao = asyncio.Event()
    _segura(harness, "android-02", portao)
    run = harness.run(["android-01", "android-02", "android-03"])
    try:
        o3 = await _esperando(harness, run.id, "android-03")
        assert "desbravador android-02" in o3["status_detail"]
        grupos = harness.state.scheduler._pathfinders[run.id]              # type: ignore[union-attr]
        assert [g.instance_id for g in grupos] == ["android-02"]
        assert _status(harness, run.id, "android-01") == "pending"
        # 01 sob controle manual: o motivo que o painel mostra é a PESSOA no controle, não o desbravador (revisão
        # F8 — antes ele aparecia "aguardando o desbravador" sem poder ser despachado de qualquer jeito)
        await harness.wait(lambda: "controle manual" in (_status(harness, run.id, "android-01", "status_detail") or ""),
                           what="android-01 esperando o controle manual")
        o1 = _obj(harness, run.id, "android-01")
        assert o1["wait_reason"] == "device_slot" and "desbravador" not in o1["status_detail"]
        await harness.ticks(3)
        assert "controle manual" in _obj(harness, run.id, "android-01")["status_detail"]   # estável, não oscila
        # nem seguidor (espera aberta) nem candidato a líder enquanto a pessoa está no controle
        assert o1["id"] not in harness.state.scheduler._esperas               # type: ignore[union-attr]

        devs["android-01"].control = ControlOwner.none                     # a pessoa devolve: vira SEGUIDOR de 02
        o1 = await _esperando(harness, run.id, "android-01")
        assert "desbravador android-02" in o1["status_detail"]
        await harness.ticks(3)
        assert _obj(harness, run.id, "android-01")["wait_reason"] == "pathfinder"
        assert [g.instance_id for g in grupos] == ["android-02"]
    finally:
        devs["android-01"].control = ControlOwner.none
        portao.set()
    assert (await harness.wait_run(run.id, timeout=60)).status == "completed"
    assert metricas.valor("pathfinder.desfecho", resultado="aprendeu") == 2
    dist = _espera_s()
    assert dist is not None and dist["n"] == 2                              # uma observação por espera
    _rotulos_fechados((run.id,))


async def test_aparelho_do_lider_que_cai_libera_quem_espera(harness: Harness) -> None:
    _liga(harness)
    portao = asyncio.Event()
    _segura(harness, "android-01", portao)
    run = harness.run(["android-01", "android-02"])
    rt1 = harness.state.devices.devices["android-01"]                      # type: ignore[union-attr]
    try:
        await _esperando(harness, run.id, "android-02")
        rt1.state = InstanceState.error                                    # o aparelho do líder sai do ar
        await harness.wait(lambda: metricas.valor("pathfinder.desfecho", resultado="liberado") == 1,
                           what="espera liberada")
        await harness.wait(lambda: _status(harness, run.id, "android-02", "wait_reason") != "pathfinder",
                           what="motivo de espera limpo")
        assert metricas.total("pathfinder.desfecho") == 1
        dist = _espera_s()
        assert dist is not None and dist["n"] == 1
    finally:
        rt1.state = InstanceState.online
        portao.set()
    await harness.wait_run(run.id, timeout=60)
    assert metricas.total("pathfinder.desfecho") == 1                       # a espera fechada não volta a contar
    _rotulos_fechados((run.id,))


async def test_execucao_cancelada_fecha_cada_espera_uma_unica_vez(harness: Harness) -> None:
    _liga(harness)
    portao = asyncio.Event()
    _segura(harness, "android-01", portao)
    run = harness.run(["android-01", "android-02", "android-03"])
    try:
        await _esperando(harness, run.id, "android-02")
        await _esperando(harness, run.id, "android-03")
        harness.state.runs.cancel(run.id)                                  # type: ignore[union-attr]
        # os seguidores saem de `pending` sem passar pelo despacho: `_varrer_esperas` fecha as duas esperas
        await harness.wait(lambda: metricas.total("pathfinder.desfecho") == 2, what="duas esperas fechadas")
        await harness.ticks(3)
        assert metricas.total("pathfinder.desfecho") == 2
        dist = _espera_s()
        assert dist is not None and dist["n"] == 2
        assert harness.state.scheduler._esperas == {}                      # type: ignore[union-attr]
        # nenhuma espera fechada como "aprendeu": cancelar não abre caminho nenhum
        assert metricas.valor("pathfinder.desfecho", resultado="aprendeu") == 0
    finally:
        portao.set()
    assert (await harness.wait_run(run.id, timeout=60)).status == "cancelled"
    assert metricas.total("pathfinder.desfecho") == 2
    _rotulos_fechados((run.id,))
