from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Callable

import pytest
import pytest_asyncio

from app.config import AppConfigFile, Config, EnvSettings
from app.models import RunCreate
from app.planning.simulated_provider import SimulatedProvider
from app.state import AppState

from .fake_device import FakeQaDevice

COMMAND = ('Abra o QA Messenger, entre na conversa com o contato de teste identificado como QA-001 e envie '
           '"Teste POC {instance_id} {run_id}". Confirme que a mensagem apareceu como enviada.')


def make_config(tmp: Path, count: int = 3) -> Config:
    file = AppConfigFile.model_validate({
        "paths": {"data_dir": str(tmp), "avd_home": str(tmp / "avd"), "evidence_dir": str(tmp / "evidence"),
                  "logs_dir": str(tmp / "logs"), "apk_dirs": [str(tmp / "apks")]},
        "instances": {"count": count, "default_app": "qa-messenger",
                      "accounts": {f"android-{i:02d}": f"qa-user-{i:02d}" for i in range(1, count + 1)}},
        "appium": {"autostart": False},
        "limits": {"retry_backoff_s": 0, "max_ai_concurrency": 4},
        "apps": [{"id": "qa-messenger", "name": "QA Messenger", "package": "com.pocqa.messenger",
                  "activity": ".MainActivity", "builtin": True}],
    })
    env = EnvSettings(_env_file=None, AI_PROVIDER="simulated", POC_DB_PATH=str(tmp / "test.sqlite3"))  # type: ignore[call-arg]
    return Config(file, env, root=tmp)


class Harness:
    def __init__(self, tmp: Path, count: int):
        self.tmp = tmp
        self.cfg = make_config(tmp, count)
        self.fakes: dict[str, FakeQaDevice] = {}
        self.state: AppState | None = None

    def _factory(self, rt: Any) -> FakeQaDevice:
        if rt.id not in self.fakes:                       # o "aparelho" sobrevive a reinícios do backend
            self.fakes[rt.id] = FakeQaDevice(account=f"qa-user-{rt.index:02d}")
        return self.fakes[rt.id]

    async def boot(self) -> AppState:
        self.state = AppState(self.cfg, provider=SimulatedProvider(), io_factory=self._factory, manage_appium=False)
        await self.state.start()
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
