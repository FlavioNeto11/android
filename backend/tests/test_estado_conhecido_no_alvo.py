"""31.267: antes da 1ª etapa de um alvo de operação, o app volta ao estado conhecido pelo motor de sessão, sem IA.

Achado do Aprendizado (31.262, leitura real da rodada de 07/10 12:55Z, op-20261007125539-22ef67): os três aparelhos
começaram com a folha de comentários da operação anterior aberta, e a IA gastou 2 a 5 decisões (US$ 0,03 a 0,08 por
alvo) só para voltar. O motor de sessão sabe voltar (`voltar_ao_estado_conhecido`), mas a porta de sessão só o chama
com a sessão vencida, e às 12:55Z ela tinha sido lida às 09:30Z.

O preparo é `ensure_session(observe_only=True)`: voltar, reabrir o app e ler a conta, sem digitar nada e sem efeito
externo. Uma vez por objetivo e só antes da 1ª tentativa (o objetivo retomado depois de uma aprovação já está na tela
certa); a sessão lida depois de a execução nascer já deixou o app em casa.

Nível de prova: `simulated` (harness na porta 5640, provedor de sessão falso). `real`: 1ª operação após o deploy.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

from app.util import to_iso

from .conftest import Harness
from .test_operacoes import APP, _conta, _persona



class _Provedor:
    def __init__(self) -> None:
        self.chamadas: list[dict[str, Any]] = []

    async def ensure_session(self, rt: Any, profile_id: str, **kw: Any) -> Any:
        self.chamadas.append({"aparelho": rt.id, "perfil": profile_id, **kw})
        return SimpleNamespace(ready=True, detail="ok")


def _alvo(st: Any, pid: str, *, run_id: str, criada_em: str, operacao: str | None = "op-x",
          tentativa: bool = False) -> Any:
    st.db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at,"
                  " operacao_id) VALUES (?,?,'comentar','execute','running',1,'[\"android-02\"]',?,?)",
                  (run_id, f"k-{run_id}", criada_em, operacao))
    oid = f"{run_id}:android-02"
    st.db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, parameters, profile_id)"
                  " VALUES (?,?,'android-02','running',1,'{}',?)", (oid, run_id, pid))
    if tentativa:
        sid = f"{oid}:v1:c1"
        st.db.execute(
            "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal, depends_on,"
            " side_effect, commit_guard, postcondition, timeout_s, max_attempts, status)"
            " VALUES (?,?,?,'android-02',1,1,'c1','Abrir','abrir','[]',0,'[]',?,180,1,'running')",
            (sid, run_id, oid, '{"kind":"model_judged","value":"x","description":"y"}'))
        st.db.execute("INSERT INTO attempts(id, step_id, number, status, started_at) VALUES (?,?,1,'running',?)",
                      (f"{sid}:a1", sid, criada_em))
    return st.db.one("SELECT * FROM objectives WHERE id=?", (oid,))


def _preparar(harness: Harness) -> tuple[Any, _Provedor, str, str]:
    st = harness.state
    assert st is not None
    pacote = str(st.db.scalar("SELECT package FROM apps WHERE id=?", (APP,)))
    provedor = _Provedor()
    st.sessoes.for_package = lambda p: provedor if p == pacote else None  # type: ignore[method-assign]
    pid = _persona(harness, "Olivia", "android-02")
    _conta(harness, pid, "qa-user-71", sessao_em="android-02")          # sessão lida AGORA
    return st, provedor, pid, pacote


async def test_o_alvo_de_operacao_volta_ao_estado_conhecido_uma_vez_sem_ia(harness: Harness) -> None:
    st, provedor, pid, pacote = _preparar(harness)
    depois = to_iso(datetime.now(timezone.utc) + timedelta(hours=1))   # a execução nasceu depois da última leitura
    obj = _alvo(st, pid, run_id="run-op", criada_em=depois)
    rt = st.devices.get("android-02")
    preparo = st._preparo_do_alvo(rt, pacote, obj)  # noqa: SLF001
    assert preparo is not None and "estado conhecido" in preparo[0]
    await preparo[1]()
    [chamada] = provedor.chamadas
    assert (chamada["aparelho"], chamada["perfil"], chamada["observe_only"]) == ("android-02", pid, True)
    assert not chamada.get("automatic") and not chamada.get("force_login")       # nunca digita nada
    evento = st.db.one("SELECT data FROM events WHERE kind='preparo.estado_conhecido' AND objective_id=?", (obj["id"],))
    assert evento is not None and json.loads(evento["data"])["sessao_pronta"] is True
    assert st._preparo_do_alvo(rt, pacote, obj) is None  # noqa: SLF001     # um preparo por objetivo
    assert st.scheduler.preparo_do_alvo == st._preparo_do_alvo  # noqa: SLF001


async def test_sem_preparo_fora_de_operacao_no_meio_do_objetivo_ou_com_sessao_lida_depois(harness: Harness) -> None:
    st, provedor, pid, pacote = _preparar(harness)
    rt = st.devices.get("android-02")
    depois = to_iso(datetime.now(timezone.utc) + timedelta(hours=1))
    avulsa = _alvo(st, pid, run_id="run-avulsa", criada_em=depois, operacao=None)
    assert st._preparo_do_alvo(rt, pacote, avulsa) is None  # noqa: SLF001
    retomado = _alvo(st, pid, run_id="run-retomada", criada_em=depois, tentativa=True)
    assert st._preparo_do_alvo(rt, pacote, retomado) is None  # noqa: SLF001   # já começou: a tela é a da etapa
    lida_depois = _alvo(st, pid, run_id="run-lida", criada_em="2026-01-01T00:00:00.000Z")
    assert st._preparo_do_alvo(rt, pacote, lida_depois) is None  # noqa: SLF001
    outro = _alvo(st, pid, run_id="run-outro", criada_em=depois)
    assert st._preparo_do_alvo(rt, "com.outro.app", outro) is None  # noqa: SLF001     # app sem motor de sessão
    assert provedor.chamadas == []
