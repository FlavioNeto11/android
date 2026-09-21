from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Callable

import pytest
import pytest_asyncio

from app.config import AppConfigFile, Config, EnvSettings
from app.models import RunCreate
from app.planning.provider import Usage
from app.planning.simulated_provider import SimulatedProvider
from app.state import AppState

from .fake_device import FakeQaDevice

COMMAND = ('Abra o QA Messenger, entre na conversa com o contato de teste identificado como QA-001 e envie '
           '"Teste POC {instance_id} {run_id}". Confirme que a mensagem apareceu como enviada.')


def make_config(tmp: Path, count: int = 3, *, store: str | None = None,
                overrides: dict[str, dict[str, Any]] | None = None,
                external: dict[str, str] | None = None) -> Config:
    file = AppConfigFile.model_validate({
        "paths": {"data_dir": str(tmp), "avd_home": str(tmp / "avd"), "evidence_dir": str(tmp / "evidence"),
                  "logs_dir": str(tmp / "logs"), "apk_dirs": [str(tmp / "apks")],
                  # nunca apontar para a pasta real do projeto: teste não mexe no catálogo de verdade
                  "apk_inbox": str(tmp / "apks" / "inbox"), "apk_catalog": str(tmp / "apks")},
        # Portas longe do parque real (5554…5574): o `rt.adb` do harness é o adb DE VERDADE, e com as portas padrão o
        # "android-01" da suíte era o emulator-5554 ligado nesta máquina. Um HOME de teste já derrubou um canário em
        # andamento. Aqui, qualquer comando que escape do aparelho falso cai num serial que não existe.
        "instances": {"count": count, "default_app": "qa-messenger", "store": store, "overrides": overrides or {},
                      # Aparelho de outra máquina: em teste `_adopt` desvia para o dublê antes do ramo externo, então
                      # nenhum `adb connect` de verdade acontece — o que se exercita é a REGRA, não o transporte.
                      "external": external or {},
                      "base_console_port": 5640,
                      "accounts": {f"android-{i:02d}": f"qa-user-{i:02d}" for i in range(1, count + 1)}},
        "appium": {"autostart": False},
        "limits": {"retry_backoff_s": 0, "max_ai_concurrency": 4},
        "apps": [{"id": "qa-messenger", "name": "QA Messenger", "package": "com.pocqa.messenger",
                  "activity": ".MainActivity", "builtin": True}],
    })
    env = EnvSettings(_env_file=None, AI_PROVIDER="simulated", POC_DB_PATH=str(tmp / "test.sqlite3"),  # type: ignore[call-arg]
                      DATABASE_URL=_dsn_de_teste())
    return Config(file, env, root=tmp)


def _dsn_de_teste() -> str | None:
    r"""Sem `TEST_DATABASE_URL`, a suíte roda em SQLite, como sempre.

    Com ela, cada teste ganha um SCHEMA próprio no PostgreSQL — isolamento equivalente ao arquivo temporário do
    SQLite, e barato. Existe para provar o aplicativo INTEIRO no outro banco, não só as peças conferidas à mão:

        docker run -d --name farm-pg -e POSTGRES_PASSWORD=teste -e POSTGRES_DB=farm -p 55433:5432 postgres:17-alpine
        $env:TEST_DATABASE_URL = "postgresql://postgres:teste@127.0.0.1:55433/farm"
        .venv\Scripts\python.exe -m pytest -q
    """
    base = os.environ.get("TEST_DATABASE_URL")
    if not base:
        return None
    import psycopg

    schema = f"t{uuid.uuid4().hex[:12]}"
    with psycopg.connect(base, autocommit=True) as c:
        c.execute(f'CREATE SCHEMA "{schema}"')
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}options=-csearch_path%3D{schema}"


class CountingProvider:
    """Conta as chamadas NO PROVEDOR. O simulado reporta uso zero, então `objectives.ai_calls` não prova nada;
    este invólucro registra cada chamada (função, nível, imagem, etapa, aparelho) e devolve uso com a função."""

    def __init__(self, inner: Any):
        self.inner = inner
        self.calls: list[dict[str, Any]] = []
        self.name, self.model, self.simulated = inner.name, inner.model, inner.simulated

    def status(self) -> Any:
        return self.inner.status()

    def count(self, role: str, **match: Any) -> int:
        return sum(1 for c in self.calls if c["role"] == role and all(c.get(k) == v for k, v in match.items()))

    async def plan(self, req: Any) -> Any:
        self.calls.append({"role": "plan"})
        plan, _ = await self.inner.plan(req)
        return plan, Usage(calls=1, role="plan", model="simulado")

    async def decide(self, req: Any) -> Any:
        self.calls.append({"role": "decide", "tier": req.tier, "image": bool(req.screen.jpeg), "step": req.ctx.step_key,
                           "instance": req.ctx.instance_id})
        decision, _ = await self.inner.decide(req)
        return decision, Usage(calls=1, role="decide", model="simulado", tier=req.tier, with_image=bool(req.screen.jpeg))

    async def verify(self, req: Any) -> Any:
        self.calls.append({"role": "verify", "image": bool(req.screen.jpeg), "step": req.ctx.step_key,
                           "instance": req.ctx.instance_id})
        verdict, _ = await self.inner.verify(req)
        return verdict, Usage(calls=1, role="verify", model="simulado", with_image=bool(req.screen.jpeg))

    async def generate_social_response(self, req: Any) -> Any:
        self.calls.append({"role": "social", "profile": req.profile_id, "kind": req.kind, "preview": req.preview})
        draft, _ = await self.inner.generate_social_response(req)
        return draft, Usage(calls=1, role="social", model="simulado")


class Harness:
    def __init__(self, tmp: Path, count: int, **config_kw: Any):
        self.tmp = tmp
        self.cfg = make_config(tmp, count, **config_kw)
        self.fakes: dict[str, FakeQaDevice] = {}
        self.state: AppState | None = None
        self.ai = CountingProvider(SimulatedProvider())

    def _factory(self, rt: Any) -> FakeQaDevice:
        if rt.id not in self.fakes:                       # o "aparelho" sobrevive a reinícios do backend
            self.fakes[rt.id] = FakeQaDevice(account=f"qa-user-{rt.index:02d}")
        return self.fakes[rt.id]

    async def boot(self) -> AppState:
        self.state = AppState(self.cfg, provider=self.ai, io_factory=self._factory, manage_appium=False)
        await self.state.start()
        # A variante de interface (idioma/densidade) é a única parte da identidade da receita que `variant_of` lê do
        # aparelho REAL, por adb — todo o resto passa pelo IO falso. Sem declará-la aqui, a suíte fica presa a quais
        # emuladores estão ligados na máquina: com o aparelho desligado o adb falha, o executor desliga a receita
        # ("receita é otimização: nunca derruba a etapa") e toda etapa vai para a IA. O teste então falha falando de
        # contagem de chamadas de IA, escondendo a causa. O harness declara a variante, como já declara o aparelho.
        for rt in self.state.devices.devices.values():
            rt.ui_variant = "en-US/xhdpi"
        return self.state

    async def crash(self) -> None:
        """Simula a queda do processo: tarefas canceladas sem nenhum encerramento gracioso de etapas."""
        assert self.state is not None
        await self.state.stop()
        self.state = None

    def run(self, ids: list[str], *, command: str = COMMAND, key: str | None = None, mode: str = "execute") -> Any:
        assert self.state is not None
        return self.state.runs.create(RunCreate(command=command, instance_ids=ids, mode=mode,  # type: ignore[arg-type]
                                                idempotency_key=key or f"test-{uuid.uuid4()}"))

    async def wait(self, predicate: Callable[[], bool], timeout: float = 20.0, what: str = "condição") -> None:
        t = 0.0
        while not predicate():
            await asyncio.sleep(0.05)
            t += 0.05
            if t > timeout:
                raise AssertionError(f"tempo esgotado aguardando: {what}")

    async def wait_run(self, run_id: str, statuses: tuple[str, ...] = ("completed", "completed_with_issues", "cancelled", "failed"),
                       timeout: float = 30.0) -> Any:
        assert self.state is not None
        repo = self.state.repo
        await self.wait(lambda: repo.run_row(run_id)["status"] in statuses, timeout, f"execução {run_id} em {statuses}")
        return repo.run_detail(run_id)


@pytest_asyncio.fixture
async def harness(tmp_path: Path) -> AsyncIterator[Harness]:
    h = Harness(tmp_path, 3)
    await h.boot()
    try:
        yield h
    finally:
        if h.state is not None:
            await h.state.stop()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
