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
    assert not any(p.code == "secret_store_foreign_key" for p in state.health().problems)
    agora = "2026-09-23T00:00:00Z"
    state.db.execute(
        "INSERT INTO secrets(ref, key_id, nonce, ciphertext, created_at, updated_at) VALUES (?,?,?,?,?,?)",
        ("sec-do-outro-backend", "dpapi-v1:0badc0de", b"x" * 12, b"y" * 32, agora, agora))
    problema = next(p for p in state.health().problems if p.code == "secret_store_foreign_key")
    assert "dpapi-v1:0badc0de" in problema.message
    assert "rekey" in problema.hint
    assert state.health().status == "degraded"          # aviso: o resto do sistema segue funcionando
