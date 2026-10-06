"""15.15 F2: teste de CARACTERIZAÇÃO de `AppState.health()` (e do que ela lê), escrito ANTES de mover a saúde para
`app/saude.py`.

Ele não diz que a saúde está certa; diz o que ela FAZ hoje, em cada cenário: o `status`, a lista `problems` na ordem
(código, mensagem e dica), o banco, o AppState-independente `features`, o Appium e a IA. O arquivo dourado
(`golden/saude_caracterizacao.json`) foi gravado na `main` de 06/10 e o mesmo teste tem de dar idêntico depois do move: uma
linha, um espaço ou uma ordem diferente reprova. Regravar o dourado é decisão de quem muda a saúde de propósito, nunca de
quem só a move:

    GERAR_GOLDEN_SAUDE=1 python -m pytest tests/test_saude_caracterizacao.py

O que o teste mexe são as ENTRADAS (SDK, banco, aparelhos, workers, Appium, IA, cofre, serviços de canal, RAM do host,
relógio, diagnóstico), nunca os métodos de saúde: por isso o mesmo arquivo vale antes e depois. Os dois pontos de entrada que
vão mudar de dono no move (`_diag_cache` e `_clock_skew_s`) passam por `_definir_diag` e `_definir_skew`: é o único lugar a
editar. Nível de prova: `simulated` (harness, aparelhos falsos, RAM do host trocada por um número fixo).
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.models import InstanceState, Problem

from .conftest import Harness

GOLDEN = Path(__file__).parent / "golden" / "saude_caracterizacao.json"
GIB = 2**30


# ------------------------------------------------------------------ entradas que mudam de dono no move
def _definir_diag(state: Any, valor: dict[str, Any] | None) -> None:
    state._diag_cache = valor


def _definir_skew(state: Any, segundos: float) -> None:
    state._clock_skew_s = segundos


# ------------------------------------------------------------------ o ambiente fixo de todo cenário
def _p(code: str) -> Problem:
    return Problem(code=code, message=f"mensagem de {code}", hint=f"dica de {code}")


def _base(state: Any, mp: pytest.MonkeyPatch) -> None:
    """Um host sem defeito, igual em qualquer máquina: SDK presente, sem diagnóstico, RAM de sobra, Appium de pé e
    mascarado, relógio certo, IA configurada e sem gasto, nenhum serviço de canal com achado."""
    mp.setattr(state.tools, "found", lambda: True)
    mp.setattr("psutil.virtual_memory", lambda: SimpleNamespace(available=64 * GIB))
    mp.setattr(state.workers, "capacidade", lambda _owner: SimpleNamespace(max_slots=2))
    mp.setattr(state.workers, "dtos", lambda: [])
    mp.setattr(state.appium, "is_up", lambda timeout=1.0: True)
    mp.setattr(state.appium, "log_masking_active", True, raising=False)
    mp.setattr(state.appium, "detail", "", raising=False)
    mp.setattr(state.social_repo, "contas_travadas_abertas", lambda: [])
    mp.setattr(state, "saldos_de_ia", lambda: [])
    mp.setattr(state.secrets, "status", lambda: "ok")
    mp.setattr(state.secrets, "chaves_estranhas", lambda: [])
    mp.setattr(state.scheduler.executor, "ai_breaker", None, raising=False)
    for servico in ("avisos", "canais_da_frota", "telegram_entrada", "trello_espelho", "trello_leitor",
                    "trello_webhook", "trello_cadastro"):
        mp.setattr(getattr(state, servico), "problemas", lambda: [])
    mp.setattr(state.portal, "problemas", lambda: [])
    for rt in state.devices.devices.values():
        mp.setattr(rt, "attention", None, raising=False)
    status = state.provider.status().model_copy(update={"configured": True, "simulated": False, "spend_today_usd": 0.0})
    mp.setattr(state.provider, "status", lambda: status)
    _definir_diag(state, None)
    _definir_skew(state, 0.0)


# ------------------------------------------------------------------ cada cenário muda UMA coisa (ou, no "tudo", várias)
Cenario = Callable[[Any, pytest.MonkeyPatch], None]


def _sdk_ausente(state: Any, mp: pytest.MonkeyPatch) -> None:
    mp.setattr(state.tools, "found", lambda: False)


def _imagem_ausente(state: Any, mp: pytest.MonkeyPatch) -> None:
    mp.setattr(type(state.cfg), "override_images", lambda self: {"android-01": "system-images;android-0;x;x86_64"})
    mp.setattr(state.tools, "system_image_dir", lambda _imagem: Path("/nao/existe/imagem"))


def _aparelho_degradado(state: Any, mp: pytest.MonkeyPatch) -> None:
    rt = state.devices.get("android-01")
    mp.setattr(rt, "state", InstanceState.error)
    mp.setattr(rt, "attention", "sessão não abre", raising=False)


def _pouca_ram(state: Any, mp: pytest.MonkeyPatch) -> None:
    mp.setattr("psutil.virtual_memory", lambda: SimpleNamespace(available=1 * GIB))
    mp.setattr(state.workers, "capacidade", lambda _owner: SimpleNamespace(max_slots=40))


def _conta_travada(state: Any, mp: pytest.MonkeyPatch) -> None:
    rt = state.devices.get("android-01")
    mp.setattr(rt, "state", InstanceState.online)
    mp.setattr(state.social_repo, "contas_travadas_abertas", lambda: [{"instance_id": "android-01"}])
    mp.setattr(state.social_repo, "rotulo_da_conta", lambda _m: "conta.de.teste")


def _tunel_fora(state: Any, mp: pytest.MonkeyPatch) -> None:
    mp.setattr(state.workers, "dtos", lambda: [SimpleNamespace(transport_state="down", name="worker-a", local=False),
                                               SimpleNamespace(transport_state="up", name="worker-b", local=False)])
    mp.setattr(state.cfg.file.server, "worker_port", 8010)


def _canal_do_worker_compartilhado(state: Any, mp: pytest.MonkeyPatch) -> None:
    mp.setattr(state.workers, "dtos", lambda: [SimpleNamespace(transport_state="up", name="worker-a", local=False)])
    mp.setattr(state.cfg.file.server, "worker_port", 0)


def _exposicao_publica(state: Any, mp: pytest.MonkeyPatch) -> None:
    mp.setattr(state.cfg.file.server, "public_hosts", ["central.exemplo.test"])
    mp.setattr(state.cfg.file.server, "allowed_origins", [])


def _portal(state: Any, mp: pytest.MonkeyPatch) -> None:
    mp.setattr(state.portal, "problemas", lambda: [("portal_x", "mensagem do portal", "dica do portal")])


def _appium_fora(state: Any, mp: pytest.MonkeyPatch) -> None:
    mp.setattr(state.appium, "is_up", lambda timeout=1.0: False)
    mp.setattr(state.appium, "detail", "simulado: fora do ar")


def _appium_sem_mascara(state: Any, mp: pytest.MonkeyPatch) -> None:
    mp.setattr(state.appium, "log_masking_active", False, raising=False)


def _relogio(state: Any, mp: pytest.MonkeyPatch) -> None:
    _definir_skew(state, 99.0)


def _ia_sem_chave(state: Any, mp: pytest.MonkeyPatch) -> None:
    status = state.provider.status().model_copy(update={"configured": False})
    mp.setattr(state.provider, "status", lambda: status)


def _ia_simulada(state: Any, mp: pytest.MonkeyPatch) -> None:
    status = state.provider.status().model_copy(update={"simulated": True})
    mp.setattr(state.provider, "status", lambda: status)


def _teto_do_dia(gasto: float) -> Cenario:
    def aplicar(state: Any, mp: pytest.MonkeyPatch) -> None:
        status = state.provider.status().model_copy(update={"spend_today_usd": gasto})
        mp.setattr(state.provider, "status", lambda: status)
        ajustes = state.settings.get().model_copy(update={"ai_max_usd_per_day": 10.0})
        mp.setattr(state.settings, "get", lambda: ajustes)
    return aplicar


def _disjuntor(tipo: str) -> Cenario:
    def aplicar(state: Any, mp: pytest.MonkeyPatch) -> None:
        trip = SimpleNamespace(kind=tipo, message="conta recusada", run_id="r-teste", at="2026-10-06T00:00:00Z")
        mp.setattr(state.scheduler.executor, "ai_breaker", trip, raising=False)
    return aplicar


def _saldos(state: Any, mp: pytest.MonkeyPatch) -> None:
    def conta(estado: str, rotulo: str, stale: bool = False) -> Any:
        return SimpleNamespace(em_uso=True, roles=["decide"], image=False, state=estado, label=rotulo, stale=stale,
                               message=f"saldo {estado}", console="console.exemplo.test", as_dict=lambda: {})

    mp.setattr(state, "saldos_de_ia", lambda: [
        conta("blocked", "Conta A"), conta("low", "Conta B"), conta("unknown", "Conta C"), conta("ok", "Conta D", True),
        SimpleNamespace(em_uso=False, roles=[], image=False, state="blocked", label="Fora de uso", stale=False,
                        message="x", console="x", as_dict=lambda: {})])


def _servicos_de_canal(state: Any, mp: pytest.MonkeyPatch) -> None:
    """Cada serviço devolve um achado; o espelho e o leitor do Trello repetem um código, que não pode sair duas vezes."""
    mp.setattr(state.avisos, "problemas", lambda: [_p("avisos_a")])
    mp.setattr(state.canais_da_frota, "problemas", lambda: [_p("frota_a")])
    mp.setattr(state.telegram_entrada, "problemas", lambda: [_p("telegram_a")])
    mp.setattr(state.trello_espelho, "problemas", lambda: [_p("trello_recusa")])
    mp.setattr(state.trello_leitor, "problemas", lambda: [_p("trello_recusa"), _p("trello_leitor_b")])
    mp.setattr(state.trello_webhook, "problemas", lambda: [_p("trello_webhook_a")])
    mp.setattr(state.trello_cadastro, "problemas", lambda: [_p("trello_cadastro_a")])


def _ia_em_fallback(state: Any, mp: pytest.MonkeyPatch) -> None:
    from app.util import now_iso

    for _ in range(3):
        state.db.execute("INSERT INTO ai_calls(ts, role, model, requested_model, fallback) VALUES (?,?,?,?,?)",
                         (now_iso(), "decide", "modelo-b", "modelo-a", "modelo-b"))


def _cofre(estado: str) -> Cenario:
    def aplicar(state: Any, mp: pytest.MonkeyPatch) -> None:
        mp.setattr(state.secrets, "status", lambda: estado)
    return aplicar


def _cofre_chave_estranha(state: Any, mp: pytest.MonkeyPatch) -> None:
    mp.setattr(state.secrets, "chaves_estranhas", lambda: ["chave_estranha"])
    # a identificação da chave deste backend nasce a cada teste: fixa, para a mensagem ser a mesma sempre
    mp.setattr(state.secrets, "provider", SimpleNamespace(key_id="chave-do-teste"))


def _sem_aceleracao(state: Any, mp: pytest.MonkeyPatch) -> None:
    _definir_diag(state, {"acceleration": {"usable": False, "detail": "sem aceleração (simulado)"},
                          "tools": [{"name": "Android Emulator", "version": "35.0.0"}]})


def _com_aceleracao(state: Any, mp: pytest.MonkeyPatch) -> None:
    _definir_diag(state, {"acceleration": {"usable": True, "detail": "WHPX (simulado)"},
                          "tools": [{"name": "Android Emulator", "version": "35.0.0"}]})


def _banco_fora(state: Any, mp: pytest.MonkeyPatch) -> None:
    mp.setattr(state.db, "alcancavel", lambda: False)


def _migracao_editada(state: Any, mp: pytest.MonkeyPatch) -> None:
    mp.setattr(state.db, "divergencias", lambda: {"008_exemplo", "001_inicial"})


def _tudo(state: Any, mp: pytest.MonkeyPatch) -> None:
    """Quase todos de uma vez, para fixar a ORDEM em que os grupos entram em `problems`. Fora os pares que se excluem
    (Appium fora × sem máscara, as três falas do cofre, o teto do dia × o aviso de 80 %), que têm cenário próprio."""
    for aplicar in (_sdk_ausente, _imagem_ausente, _aparelho_degradado, _pouca_ram, _tunel_fora, _exposicao_publica,
                    _portal, _appium_fora, _relogio, _ia_sem_chave, _teto_do_dia(11.0), _disjuntor("billing"), _saldos,
                    _servicos_de_canal, _ia_em_fallback, _cofre("locked"), _ia_simulada, _sem_aceleracao):
        aplicar(state, mp)
    # a conta travada precisa do aparelho ligado: vem depois do degradado, que o deixou em `error` (também é "no ar")
    mp.setattr(state.social_repo, "contas_travadas_abertas", lambda: [{"instance_id": "android-01"}])
    mp.setattr(state.social_repo, "rotulo_da_conta", lambda _m: "conta.de.teste")


CENARIOS: dict[str, Cenario] = {
    "base": lambda s, m: None,
    "sdk_ausente": _sdk_ausente,
    "imagem_ausente": _imagem_ausente,
    "aparelho_degradado": _aparelho_degradado,
    "pouca_ram": _pouca_ram,
    "conta_travada_no_ar": _conta_travada,
    "tunel_fora": _tunel_fora,
    "canal_do_worker_compartilhado": _canal_do_worker_compartilhado,
    "exposicao_publica_incompleta": _exposicao_publica,
    "portal": _portal,
    "appium_fora": _appium_fora,
    "appium_sem_mascara": _appium_sem_mascara,
    "relogio_longe": _relogio,
    "ia_sem_chave": _ia_sem_chave,
    "ia_simulada": _ia_simulada,
    "teto_do_dia_atingido": _teto_do_dia(11.0),
    "teto_do_dia_80_por_cento": _teto_do_dia(8.5),
    "disjuntor_cobranca": _disjuntor("billing"),
    "disjuntor_saldo": _disjuntor("balance"),
    "disjuntor_credencial": _disjuntor("auth"),
    "saldos_de_ia": _saldos,
    "servicos_de_canal": _servicos_de_canal,
    "ia_em_fallback": _ia_em_fallback,
    "cofre_travado": _cofre("locked"),
    "cofre_indisponivel": _cofre("unavailable"),
    "cofre_chave_estranha": _cofre_chave_estranha,
    "sem_aceleracao": _sem_aceleracao,
    "com_aceleracao": _com_aceleracao,
    "banco_fora": _banco_fora,
    "migracao_editada": _migracao_editada,
    "tudo_de_uma_vez": _tudo,
}


# ------------------------------------------------------------------ a fotografia de um cenário
def _fotografia(state: Any, raiz: Path) -> dict[str, Any]:
    saude = state.health()
    dono = {str(raiz): "<raiz>", str(state.cfg.sdk_root): "<sdk>"}

    # O que depende do AMBIENTE não entra no dourado: o dialeto do banco (e o endereço dele) vira o marcador `<dialeto>`, também
    # no "(dialeto)" da mensagem `database_down`. A suíte em PostgreSQL reprovou o dourado gerado em SQLite (K-104); `problems`
    # segue inteiro e o resto da fotografia, comparado campo a campo.
    dialeto = saude.database.dialect

    def limpo(texto: str) -> str:
        for de, para in dono.items():
            texto = texto.replace(de, para)
        return texto.replace(f"({dialeto})", "(<dialeto>)")

    features = {k: v for k, v in saude.features.items() if k not in ("system_image", "ensino_v2_chamadas")}
    return {
        "status": saude.status,
        "problems": [[p.code, limpo(p.message), limpo(p.hint or "")] for p in saude.problems],
        "database": {"dialect": "<dialeto>", "reachable": saude.database.reachable, "target": "<dialeto>"},
        "sdk": {"found": saude.sdk.found, "emulator_version": saude.sdk.emulator_version, "accel": limpo(str(saude.sdk.accel))},
        "appium": {"running": saude.appium.running},
        "ai": {"configured": saude.ai.configured, "simulated": saude.ai.simulated,
               "account_blocked": saude.ai.account_blocked},
        "features": json.loads(json.dumps(features, default=str, sort_keys=True)),
    }


@pytest.mark.parametrize("nome", sorted(CENARIOS))
async def test_a_saude_faz_hoje_o_que_o_arquivo_dourado_diz(harness: Harness, nome: str,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    state = harness.state
    assert state is not None
    _base(state, monkeypatch)
    CENARIOS[nome](state, monkeypatch)
    foto = _fotografia(state, harness.cfg.root)
    if os.environ.get("GERAR_GOLDEN_SAUDE"):
        previo = json.loads(GOLDEN.read_text(encoding="utf-8")) if GOLDEN.exists() else {}
        previo[nome] = foto
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(previo, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8",
                          newline="\n")
        return
    esperado = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert nome in esperado, f"cenário {nome} sem dourado: rode com GERAR_GOLDEN_SAUDE=1"
    assert foto == esperado[nome], f"a saúde mudou no cenário {nome!r}"


async def test_o_dourado_cobre_todos_os_cenarios_e_nenhum_a_mais() -> None:
    esperado = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert sorted(esperado) == sorted(CENARIOS)


async def test_o_dourado_cobre_cada_codigo_de_problema_do_health() -> None:
    """Se alguém acrescentar um código a `health()`, ele tem de ganhar um cenário aqui: a lista abaixo é a união dos
    códigos que o dourado já tem, e o que não está nela e aparece em `state.py` reprova."""
    esperado = json.loads(GOLDEN.read_text(encoding="utf-8"))
    vistos = {linha[0] for foto in esperado.values() for linha in foto["problems"]}
    fonte = (Path(__file__).parent.parent / "app" / "state.py").read_text(encoding="utf-8")
    saude = Path(__file__).parent.parent / "app" / "saude.py"
    if saude.exists():
        fonte += saude.read_text(encoding="utf-8")
    import re

    codigos_no_codigo = set(re.findall(r'code="([a-z_0-9]+)"', fonte))
    # os dois códigos montados por variável (`ai_billing`/`ai_auth_failed` e os do portal) estão nos cenários do disjuntor e do portal
    faltam = sorted(c for c in codigos_no_codigo if c not in vistos and not c.startswith("ai_balance_"))
    assert not faltam, f"código de saúde sem cenário no teste de caracterização: {faltam}"
