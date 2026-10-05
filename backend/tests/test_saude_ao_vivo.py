"""Item 3.4 — achado #65: `health.updated` passa a ser emitido quando `health()` muda, em vez de nunca."""
from __future__ import annotations

from typing import Any

from .conftest import Harness


async def test_check_health_emite_apenas_quando_o_resultado_muda(harness: Harness) -> None:
    state = harness.state
    assert state is not None
    vistos: list[dict[str, Any]] = []
    original_emit = state.bus.emit

    def espiao(kind: str, message: str, **kw: Any) -> Any:
        if kind == "health.updated":
            vistos.append(kw.get("data") or {})
        return original_emit(kind, message, **kw)

    state.bus.emit = espiao  # type: ignore[method-assign]
    state.appium.is_up = lambda timeout=1.0: True  # type: ignore[assignment]  # ponto de partida estável
    # Também estável: `capacity_local` traz a RAM livre do host (de 100 em 100 MB) na mensagem, e com a suíte em
    # paralelo ela anda entre as duas checagens — o teste via uma "mudança" que não era do backend (falhou assim em
    # 03/10 e na rodada do 29.78). O próprio `capacity_local` tem os testes dele mais abaixo neste arquivo.
    state._problema_de_capacidade_local = lambda: None  # type: ignore[method-assign]

    state._check_health()
    assert len(vistos) == 1
    assert vistos[0]["health"]["status"] in ("ok", "degraded", "error")

    # Nada mudou: a segunda checagem não deve emitir de novo.
    state._check_health()
    assert len(vistos) == 1

    # Mudança real (Appium fica indisponível): agora sim, novo evento.
    state.appium.is_up = lambda timeout=1.0: False  # type: ignore[assignment]
    state.appium.detail = "simulado: fora do ar para o teste"
    state._check_health()
    assert len(vistos) == 2
    assert vistos[1]["health"]["status"] != "ok"


# ------------------------------------------------------------------ o banco entra na saúde (achado #33)
def _host_pronto(state: Any) -> None:
    """Tira da conta o que é da MÁQUINA que roda a suíte: sem SDK do Android (`sdk_missing`) ou sem aceleração
    (`no_acceleration`, o runner Linux do CI não tem KVM), a saúde vira `error` por causa do host — e os testes
    abaixo, que provam que um problema é AVISO e não parada, falhavam por isso e não pelo que provam (backlog B13).
    """
    state.tools.found = lambda: True  # type: ignore[method-assign]
    state._diag_cache = None


def _problema(state: Any, code: str) -> Any:
    return next((p for p in state.health().problems if p.code == code), None)


async def test_a_saude_diz_qual_banco_e_se_ele_respondeu(harness: Harness) -> None:
    """Antes, `/health` não fazia UMA consulta: a resposta não dizia sequer em qual banco o processo estava — a
    pergunta só se respondia lendo o `.env` da máquina."""
    state = harness.state
    assert state is not None
    banco = state.health().database
    assert banco is not None
    assert banco.dialect == state.db.dialect
    assert banco.reachable is True
    # O endereço nunca leva usuário nem senha: a saúde é lida pelo painel e vai para relatório.
    assert banco.target and "@" not in banco.target


async def test_banco_fora_do_ar_derruba_a_saude_em_vez_de_passar_batido(harness: Harness) -> None:
    """O defeito do achado #33: com o PostgreSQL fora do ar o processo respondia `degraded/ok` alegremente, e
    `migration` aparecia como `null` porque a única consulta da saúde engolia exceção."""
    state = harness.state
    assert state is not None
    state.db.alcancavel = lambda: False  # type: ignore[method-assign]
    saude = state.health()
    assert saude.status == "error"                    # sem banco não há fila, posse de etapa nem histórico
    assert saude.database is not None and saude.database.reachable is False
    problema = next(p for p in saude.problems if p.code == "database_down")
    assert "reabert" in problema.hint                  # e o operador é informado de que não precisa reiniciar


async def test_migracao_editada_depois_de_aplicada_aparece_na_saude(harness: Harness) -> None:
    """Achado #169: o banco de produção foi migrado com a 008 antiga e tem um esquema que o mesmo arquivo não gera
    mais. Nada detectava a divergência — agora ela tem nome e aparece no `/health`."""
    state = harness.state
    assert state is not None
    _host_pronto(state)
    state.db.divergencias = lambda: ["008_instagram_domain"]  # type: ignore[method-assign]
    saude = state.health()
    assert saude.status == "degraded"                  # aviso, não parada: o banco funciona, só divergiu
    problema = next(p for p in saude.problems if p.code == "migration_changed")
    assert "008_instagram_domain" in problema.message
    assert "não se edita" in problema.hint


async def test_credencial_de_outro_backend_aparece_na_saude(harness: Harness) -> None:
    """Achado #126: dois backends no mesmo banco, cada um com a sua chave mestra, os DOIS dizendo `ready`. O
    sintoma era login automático falhando de forma intermitente, sem nada na saúde apontando a causa."""
    state = harness.state
    assert state is not None
    _host_pronto(state)
    assert not any(p.code == "secret_store_foreign_key" for p in state.health().problems)
    agora = "2026-09-23T00:00:00Z"
    state.db.execute(
        "INSERT INTO secrets(ref, key_id, nonce, ciphertext, created_at, updated_at) VALUES (?,?,?,?,?,?)",
        ("sec-do-outro-backend", "dpapi-v1:0badc0de", b"x" * 12, b"y" * 32, agora, agora))
    problema = next(p for p in state.health().problems if p.code == "secret_store_foreign_key")
    assert "dpapi-v1:0badc0de" in problema.message
    assert "rekey" in problema.hint
    assert state.health().status == "degraded"          # aviso: o resto do sistema segue funcionando


# ------------------------------------------------------------------ capacidade local (item 10.3, achado #146)
class _MemoriaFalsa:
    """Substitui `psutil.virtual_memory()` pelo valor que o teste quer, sem depender da RAM real da máquina que
    roda a suíte."""

    def __init__(self, available_mb: float):
        self.available = available_mb * 2**20


def test_ram_livre_de_sobra_nao_gera_aviso_de_capacidade(harness: Harness, monkeypatch: Any) -> None:
    state = harness.state
    assert state is not None
    state.settings.update({"max_online_devices": 4})
    # Config padrão: est=2700 MB/instância (perfil medido de google_apis), folga 1500 MB. Com 20 GB livres cabem ~6 — acima do
    # alvo de 4 — e o aviso não deve aparecer.
    monkeypatch.setattr("app.state.psutil.virtual_memory", lambda: _MemoriaFalsa(20_000))
    assert not any(p.code == "capacity_local" for p in state.health().problems)


def test_ram_livre_insuficiente_para_o_alvo_gera_aviso_de_capacidade(harness: Harness, monkeypatch: Any) -> None:
    """Reproduz o achado #146: com o host quase sem RAM livre (o WSL comeu o resto), o alvo configurado
    (`max_online_devices`) deixa de caber — e isto precisa aparecer em `/api/health`, não só ser descoberto
    boot a boot pelo rodízio."""
    state = harness.state
    assert state is not None
    state.settings.update({"max_online_devices": 4})
    # 4 GB livres: (4000 - 1500) // 2700 = 0 cabe a mais, 0 online agora -> estimado 0 < alvo 4.
    monkeypatch.setattr("app.state.psutil.virtual_memory", lambda: _MemoriaFalsa(4_000))
    _host_pronto(state)
    saude = state.health()
    problema = next(p for p in saude.problems if p.code == "capacity_local")
    assert "alvo configurado (4" in problema.message
    assert "wslconfig" in problema.hint.lower() or "WSL" in problema.hint
    assert saude.status == "degraded"


# ---------------------------------------------------------------- canal do worker na porta principal
def _worker_remoto(state: Any) -> None:
    import json
    state.db.execute(
        "INSERT INTO workers(id, name, os, os_version, agent_version, protocol, appium_mode, max_slots, verbs,"
        " state, resources, devices, enrolled_at, last_seen_at, token_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("worker-lan-01", "Notebook da LAN", "windows", None, "1.0", 1, "central", 6, "[]", "online", None,
         json.dumps([]), "2026-09-22T00:00:00Z", "2026-09-22T00:00:00Z", "x"))


def test_worker_remoto_com_listener_dedicado_desligado_e_problema_de_saude(harness: Harness) -> None:
    """Medido: config recriado do exemplo trouxe `worker_port: 0`; o canal passou a atender na porta principal,
    onde o `-R` do túnel expõe a API inteira à máquina do worker — e nada reclamou. Com worker remoto inscrito,
    o padrão que é certo para uma máquina só vira problema."""
    state = harness.state
    assert state is not None
    _worker_remoto(state)
    harness.cfg.file.server.worker_port = 0
    problema = next((p for p in state.health().problems if p.code == "worker_channel_shared"), None)
    assert problema is not None and "worker_port" in problema.message

    harness.cfg.file.server.worker_port = 8010
    assert not any(p.code == "worker_channel_shared" for p in state.health().problems)


def test_sem_worker_remoto_o_listener_desligado_nao_e_problema(harness: Harness) -> None:
    state = harness.state
    assert state is not None
    harness.cfg.file.server.worker_port = 0
    assert not any(p.code == "worker_channel_shared" for p in state.health().problems)


async def test_chamadas_de_ia_em_fallback_aparecem_na_saude(harness: Harness) -> None:
    """Backlog B15 (bateria de 25/09): com o Ollama fora do ar as decisões iam todas para o fallback declarado e a
    saúde dizia `ok`. O fallback é o comportamento certo; o problema era não aparecer em lugar nenhum."""
    from datetime import timedelta

    from app.util import now, to_iso

    state = harness.state
    assert state is not None
    assert not any(p.code == "ai_fallback_em_uso" for p in state.health().problems)
    sql = ("INSERT INTO ai_calls(ts, role, model, requested_model, fallback, provider) VALUES (?,?,?,?,?,?)")
    antiga = to_iso(now() - timedelta(hours=2))            # fora da janela de 30 min: não conta
    state.db.execute(sql, (antiga, "decide", "claude-sonnet-5", "qwen3-vl:4b-instruct-16k", "anthropic", "anthropic"))
    assert not any(p.code == "ai_fallback_em_uso" for p in state.health().problems)
    for _ in range(3):
        state.db.execute(sql, (to_iso(now()), "decide", "claude-sonnet-5", "qwen3-vl:4b-instruct-16k", "anthropic",
                               "anthropic"))
    problema = next(p for p in state.health().problems if p.code == "ai_fallback_em_uso")
    assert "3 chamada(s) de 'decide'" in problema.message and "qwen3-vl:4b-instruct-16k" in problema.message
    assert "Ollama" in problema.hint
