from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Iterator

import pytest
import pytest_asyncio

from app.config import AppConfigFile, Config, EnvSettings
from app.db import MIGRATIONS_DIR as _MIGRACOES_ORIGINAIS
from app.devices.emulator_backend import FakeEmulatorBackend
from app.models import RunCreate
from app.planning.provider import Usage
from app.planning.simulated_provider import SimulatedProvider
from app.state import AppState

from .fake_device import FakeQaDevice
from .relogio_virtual import RelogioVirtual

COMMAND = ('Abra o QA Messenger, entre na conversa com o contato de teste identificado como QA-001 e envie '
           '"Teste POC {instance_id} {run_id}". Confirme que a mensagem apareceu como enviada.')


def make_config(tmp: Path, count: int = 3, *, store: str | None = None,
                overrides: dict[str, dict[str, Any]] | None = None,
                external: dict[str, str] | None = None,
                owner_id: str | None = None, role: str | None = None,
                db_dsn: str | None = None) -> Config:
    """`owner_id`/`role`/`db_dsn` existem para o teste de DOIS backends (fase 5).

    `db_dsn` é o que permite o segundo `AppState` abrir o MESMO banco do primeiro: sem ele, `_dsn_de_teste()`
    criaria outro schema no PostgreSQL (ou outro arquivo no SQLite) e o teste provaria isolamento por acidente.
    `owner_id` é obrigatório no segundo backend porque, na mesma máquina, o padrão dos dois é o hostname.
    """
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
                      # O reparo que espera a máquina aliviar lê a CPU REAL: a suíte roda com a máquina carregada.
                      "remediation_host_cpu_max": 101,
                      "accounts": {f"android-{i:02d}": f"qa-user-{i:02d}" for i in range(1, count + 1)}},
        "appium": {"autostart": False},
        # 29.11: o aparelho do harness declara `gpu_mode: host`. O padrão do produto é `swiftshader_indirect`, e o
        # Outlook — o segundo app de dezenas de testes (loja de apps, "sempre na promovida", entre apps) — recusa o
        # SwiftShader no `app.yaml`: com o padrão, todo teste que o distribui passaria a provar a recusa em vez do que
        # ele existe para provar. A recusa tem os testes dela (`test_renderizador.py`), que pedem o SwiftShader por
        # `overrides` ou escolhem o que o emulador seleciona (`harness.emulator.gles`).
        "android": {"gpu_mode": "host"},
        # Achado #164: a suíte pagava em tempo REAL assentamentos pensados para um emulador de verdade — o recuo
        # entre tentativas, o tick do despacho, a espera da sessão, o assentamento entre ações e os 4 s de
        # `effect_settle_s`, que era configurável e nenhum teste reduzia. Aqui todos caem juntos. O tick NÃO vai a
        # zero (`wait_for(timeout=0)` estoura na hora e vira espera ocupada); `judge_wait_s` também não, porque é
        # espera de COMPORTAMENTO: a zero, o verificador sonda antes de o app sair de "enviando" e paga dois
        # julgamentos, mudando a contagem de chamadas que vários testes conferem.
        "limits": {"retry_backoff_s": 0, "max_ai_concurrency": 4, "scheduler_tick_s": 0.02,
                   "ai_retry_wait_s": 0, "session_retry_wait_s": 0,
                   # T.2 (achado #164, fatia que faltava): o poll de `_wait_boot` (boot_completed/ui_ready) era
                   # o último `sleep` fixo do laço de boot; vira configurável do mesmo jeito, e cai aqui junto.
                   "boot_poll_s": 0.01},
        # `recipes_promote_after: 0`: a suíte que prova a REPRODUÇÃO (aprende no 1º aparelho, repete no 2º) segue com a
        # receita nascendo ativa; a prova por repetição da candidata tem os testes próprios (test_receita_candidata),
        # que ligam o valor de produção (2).
        "ai": {"effect_settle_s": 0, "action_settle_s": 0, "recipe_settle_s": 0, "judge_wait_s": 0.05,
               "recipes_promote_after": 0},
        # O mesmo para o fluxo (ADR-054, D1): a suíte que prova o REAPROVEITAMENTO (aprende na 1ª execução, reaproveita
        # na 2ª) segue com o fluxo nascendo ativo. O D1 de produção (nasce candidato, sombra no digest) tem os testes
        # próprios (test_d1_fluxos), que ligam `com_prova`.
        # E o que a execução simulada ensina (RA-19 B): o Harness é todo simulado, então a suíte que prova a reprodução e
        # o reaproveitamento segue no modo anterior; a regra de produção tem os testes próprios (test_origem_simulada).
        "aprendizado": {"fluxo": {"com_prova": False}, "simulada_publica": True},
        # Rede por aparelho (25.4): o executável do sing-box aponta para um caminho que NÃO existe. No checkout do
        # ambiente central o binário de verdade está em `data/rede/`, e um teste que escapasse do dublê de processo
        # subiria um servidor WireGuard na 51820 da máquina — em cima do que o parque usa. Assim, escapar é erro.
        "rede": {"servidor": {"binario": str(tmp / "sing-box-de-teste-inexistente.exe")},
                 # Nenhum teste abre socket para medir a saída do central (29.20): quem prova isso injeta o medidor.
                 "sonda": {"medir_central": False, "medir_sem_rede": False}},
        # O QA Messenger é o primeiro, e continua sendo o app padrão de todo aparelho do harness. O Instagram
        # entrou porque a porta de sessão passou a ser POR APP (item 6.1): sem um aparelho amarrado a ele, não há
        # como provar de ponta a ponta que um desafio de segurança bloqueia a tarefa — e essa é a garantia que
        # importa. Quem quiser esse caminho amarra explicitamente: UPDATE instances SET app_id='instagram'.
        "apps": [{"id": "qa-messenger", "name": "QA Messenger", "package": "com.pocqa.messenger",
                  "activity": ".MainActivity", "builtin": True},
                 {"id": "instagram", "name": "Instagram", "package": "com.instagram.android",
                  "activity": "com.instagram.mainactivity.MainActivity"}],
    })
    dsn = db_dsn if db_dsn is not None else _dsn_de_teste(reusar=True)
    e_pg = bool(dsn) and dsn.startswith(("postgres://", "postgresql://"))
    campos: dict[str, Any] = {
        "AI_PROVIDER": "simulated",
        "POC_DB_PATH": dsn if (dsn and not e_pg) else str(tmp / "test.sqlite3"),
        "DATABASE_URL": dsn if e_pg else None,
    }
    if os.name != "nt":
        # Fora do Windows não há DPAPI, e o cofre de credenciais recusava tudo ("Não há chave mestra"): ~25 testes
        # do CI (Linux) falhavam por isso, não pelo que provam (backlog B13). A chave de ambiente é o caminho real de
        # uma instalação Linux; aqui é uma chave FIXA e falsa, só de teste. No Windows o harness segue no DPAPI.
        campos["CREDENTIALS_MASTER_KEY"] = "dGVzdGUtZG8taGFybmVzcy1uYW8tZS1zZWdyZWRvLTA="
    if owner_id is not None:
        campos["OWNER_ID"] = owner_id
    if role is not None:
        campos["ROLE"] = role
    env = EnvSettings(_env_file=None, **campos)  # type: ignore[call-arg]
    return Config(file, env, root=tmp)


_SCHEMAS_DE_TESTE: list[str] = []              # achado #163: toda corrida em PostgreSQL some daqui no fim da sessão
_REUSO_NESTE_TESTE: list[bool] = [False]        # 29.63: o esquema do worker já foi entregue neste teste?


def _dsn_de_teste(*, reusar: bool = False) -> str | None:
    r"""Sem `TEST_DATABASE_URL`, a suíte roda em SQLite, como sempre.

    Com ela, cada teste ganha um SCHEMA próprio no PostgreSQL — isolamento equivalente ao arquivo temporário do
    SQLite, e barato. Existe para provar o aplicativo INTEIRO no outro banco, não só as peças conferidas à mão:

        docker run -d --name farm-pg -e POSTGRES_PASSWORD=teste -e POSTGRES_DB=farm -p 55433:5432 postgres:17-alpine
        $env:TEST_DATABASE_URL = "postgresql://postgres:teste@127.0.0.1:55433/farm"
        .venv\Scripts\python.exe -m pytest -q

    Cada chamada empilha o schema criado em `_SCHEMAS_DE_TESTE`; `pytest_sessionfinish` apaga todos no fim (achado
    #163 — sem isso o catálogo do banco de teste só cresce: 1608 schemas / 1,9 GB medidos numa única corrida).

    `reusar` (29.63, só os ajudantes compartilhados: `make_config` e `fake_skills.banco`): a primeira abertura de
    banco do teste recebe o esquema do worker, já migrado e esvaziado (`tests/esquema_do_worker.py`), em vez de um
    esquema novo a migrar (~3,2 s por teste). Com `MIGRATIONS_DIR` trocado (testes da própria migração), não reusa.
    `ESQUEMA_MODELO=off` desliga o reuso (a medida antes/depois no mesmo commit e nos mesmos testes).
    """
    base = os.environ.get("TEST_DATABASE_URL")
    if not base:
        return None
    if reusar and not _REUSO_NESTE_TESTE[0] and os.environ.get("ESQUEMA_MODELO") != "off":
        from app import db as db_mod

        from .esquema_do_worker import ESQUEMA_DO_WORKER

        if db_mod.MIGRATIONS_DIR == _MIGRACOES_ORIGINAIS:
            dsn = ESQUEMA_DO_WORKER.dsn(base, _SCHEMAS_DE_TESTE)
            if dsn is not None:
                _REUSO_NESTE_TESTE[0] = True
                return dsn
    import psycopg

    schema = f"t{uuid.uuid4().hex[:12]}"
    with psycopg.connect(base, autocommit=True) as c:
        c.execute(f'CREATE SCHEMA "{schema}"')
    _SCHEMAS_DE_TESTE.append(schema)
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}options=-csearch_path%3D{schema}"


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:  # noqa: ARG001 — assinatura exigida pelo pytest
    """Apaga, um por um, os schemas que ESTA sessão criou (achado #163). Nunca derruba a suíte: um schema que não
    apague vira lixo a limpar depois, não um teste vermelho por causa de faxina. `lock_timeout` curto porque o
    farm de teste é compartilhado por corridas em paralelo (ver `ritmo-de-trabalho`) — não vale a pena travar
    esperando um lock que outra sessão seja dona.

    De propósito, NÃO varre `^t[0-9a-f]{12}$` inteiro no início da sessão: duas corridas completas do dono no
    mesmo PostgreSQL de teste ao mesmo tempo (uma em primeiro plano, outra em segundo, como o ritmo de trabalho
    registra) apagariam os schemas uma da outra. Cada sessão só é dona do que ELA criou.
    """
    from .esquema_do_worker import ESQUEMA_DO_WORKER

    ESQUEMA_DO_WORKER.relatar()
    base = os.environ.get("TEST_DATABASE_URL")
    if not base or not (_SCHEMAS_DE_TESTE or ESQUEMA_DO_WORKER.criados):
        return
    try:
        import psycopg

        with psycopg.connect(base, autocommit=True, connect_timeout=5) as c:
            c.execute("SET lock_timeout = '3s'")
            for schema in [*_SCHEMAS_DE_TESTE, *ESQUEMA_DO_WORKER.criados]:
                try:
                    c.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
                except Exception:
                    continue            # um schema preso (lock de outra sessão) não impede os demais nem a suíte
    except Exception:
        pass                            # faxina é conveniência de desenvolvimento; nunca pode reprovar a corrida


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
                           "instance": req.ctx.instance_id, "escalate": bool(getattr(req, "escalate", False))})
        verdict, _ = await self.inner.verify(req)
        return verdict, Usage(calls=1, role="verify", model="simulado", with_image=bool(req.screen.jpeg))

    async def generalize(self, req: Any) -> Any:
        self.calls.append({"role": "generalize"})
        proposta, _ = await self.inner.generalize(req)
        return proposta, Usage(calls=1, role="plan", model="simulado")

    async def generate_social_response(self, req: Any) -> Any:
        self.calls.append({"role": "social", "profile": req.profile_id, "kind": req.kind, "preview": req.preview})
        draft, _ = await self.inner.generate_social_response(req)
        return draft, Usage(calls=1, role="social", model="simulado")

    async def orchestrate_targets(self, req: Any) -> Any:
        self.calls.append({"role": "plan", "kind": "orquestracao", "candidatas": len(req.cartoes)})
        out, _ = await self.inner.orchestrate_targets(req)
        return out, Usage(calls=1, role="plan", model="simulado")

    async def refine_command(self, req: Any) -> Any:
        self.calls.append({"role": "plan", "kind": "refine", "answers": len(req.answers), "pending": len(req.pending)})
        refinado, _ = await self.inner.refine_command(req)
        return refinado, Usage(calls=1, role="plan", model="simulado")

    async def transcribe(self, req: Any) -> Any:
        """O leitor da leitura visual (item 12.5): registra o pedido (só as saídas pedidas, nunca o conteúdo) e devolve o que o
        teste programou em `inner.leitura` (sem programação, "ilegível")."""
        self.calls.append({"role": "leitura", "saidas": list(req.saidas), "image": bool(req.recorte)})
        t, _ = await self.inner.transcribe(req)
        return t, Usage(calls=1, role="leitura", model="simulado", with_image=bool(req.recorte))

    async def generate_persona(self, req: Any) -> Any:
        self.calls.append({"role": "persona", "kind": "persona", "enrich": req.existing is not None})
        draft, _ = await self.inner.generate_persona(req)
        return draft, Usage(calls=1, role="persona", model="simulado")

    async def review_knowledge(self, req: Any) -> Any:
        self.calls.append({"role": "plan", "kind": "curador"})
        parecer, _ = await self.inner.review_knowledge(req)
        return parecer, Usage(calls=1, role="plan", model="simulado")


class Harness:
    def __init__(self, tmp: Path, count: int, *, factory: Callable[[Any], Any] | None = None, **config_kw: Any):
        """`factory(rt)` cria o aparelho falso de cada instância (padrão: `FakeQaDevice`). É o que põe outro app no
        harness — o `FakeInstagram` da fatia G — sem mudar o resto: o aparelho criado continua em `fakes` e
        sobrevive a reinícios do backend, como o de QA."""
        self.tmp = tmp
        self.cfg = make_config(tmp, count, **config_kw)
        self.fakes: dict[str, Any] = {}
        self._fabrica = factory
        self.state: AppState | None = None
        self.ai = CountingProvider(SimulatedProvider())
        # Achado #165: a MÁQUINA do ciclo de vida do emulador é um valor escolhido aqui, não a desta estação.
        # Com a guarda de capacidade valendo também para o aparelho falso, ler a RAM real faria a suíte recusar
        # boots de forma intermitente justamente nesta máquina, que roda emuladores. Quem quer provar a recusa
        # baixa `harness.emulator.free_mb`.
        self.emulator = FakeEmulatorBackend()
        # T.2: ligado por `pular_o_tempo()`; sem isso, tudo corre em tempo real como sempre.
        self.relogio: RelogioVirtual | None = None

    def pular_o_tempo(self) -> RelogioVirtual:
        """As ferramentas deixam de DORMIR e passam a avançar o relógio do aparelho falso (`relogio_virtual.py`).

        Opt-in por teste: o padrão da suíte segue em tempo real, porque ligar isto em todo teste muda quantas voltas o
        verificador dá em cada um e só se confirma com a suíte inteira. Vale para os aparelhos já criados, para os que
        nascerem depois e para o executor de cada `boot()` (o teste de reinício sobe outro backend)."""
        if self.relogio is None:
            self.relogio = RelogioVirtual()
        for fake in self.fakes.values():
            if hasattr(fake, "relogio"):
                fake.relogio = self.relogio.agora
        if self.state is not None:
            self.state.scheduler.executor.dormir = self.relogio.dormir
        return self.relogio

    def encurtar_verificacao(self, segundos: float = 1.5) -> None:
        """Encurta o ORÇAMENTO de `_verify` (normal, paciente e piso) para o teste que prova "sem a mensagem na tela até
        o prazo → incerto". Sem isto ele paga os 60 s do paciente em tempo real. Só o prazo muda: o que vale como prova
        (a tela não mostrou a mensagem dentro do orçamento) é a mesma; o piso (0,5 s) é o da validação do campo."""
        ai = self.cfg.file.ai
        ai.verify_budget_min_s = ai.verify_budget_s = ai.verify_budget_patient_s = float(segundos)

    def _factory(self, rt: Any) -> Any:
        if rt.id not in self.fakes:                       # o "aparelho" sobrevive a reinícios do backend
            self.fakes[rt.id] = (self._fabrica(rt) if self._fabrica is not None
                                 else FakeQaDevice(account=f"qa-user-{rt.index:02d}"))
            if self.relogio is not None and hasattr(self.fakes[rt.id], "relogio"):
                self.fakes[rt.id].relogio = self.relogio.agora
        return self.fakes[rt.id]

    async def boot(self) -> AppState:
        self.state = AppState(self.cfg, provider=self.ai, io_factory=self._factory, manage_appium=False,
                              emulator=self.emulator)
        if self.relogio is not None:                      # o executor do backend NOVO (reinício) pula o tempo também
            self.state.scheduler.executor.dormir = self.relogio.dormir
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

    async def ticks(self, n: int = 2, timeout: float = 10.0) -> None:
        """Espera o laço do despacho dar `n` voltas COMPLETAS (achado #164).

        É o substituto honesto de `sleep(1.2)` no teste que prova AUSÊNCIA de efeito. Dormir só prova que o
        relógio andou — numa máquina carregada (esta roda emuladores) o tick pode não ter acontecido dentro do
        sono, e o teste passa sem ter olhado para nada. Esperar o contador prova que o scheduler OLHOU para o
        estado, `n` vezes, e mesmo assim não fez o que o teste diz que ele não devia fazer.
        """
        assert self.state is not None
        sched = self.state.scheduler
        alvo = sched.ticks + n
        await self.wait(lambda: sched.ticks >= alvo, timeout, f"{n} volta(s) do laço de despacho")

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


@pytest.fixture(scope="session", autouse=True)
def _hosts_sinteticos_de_teste() -> Iterator[None]:
    """Os nomes que os clientes ASGI usam como `Host` (`test`, `testserver`, `testclient`) **saíram** do conjunto de
    produção de `security.access`: eles valiam como loopback no binário que roda no parque, e qualquer cliente da
    rede atravessava o portão mandando `Host: testserver`.

    Quem precisa deles é a suíte, então é a suíte que os declara — aqui, uma vez, e só enquanto ela roda. Um teste
    que queira provar o comportamento de PRODUÇÃO esvazia este conjunto localmente (ver
    `test_autenticacao.test_nomes_de_teste_nao_valem_em_producao`).
    """
    from app.security import access

    anterior = access.LOOPBACK_DE_TESTE
    access.LOOPBACK_DE_TESTE = frozenset({"test", "testserver", "testclient"})
    try:
        yield
    finally:
        access.LOOPBACK_DE_TESTE = anterior


@pytest.fixture(autouse=True)
def _schemas_do_teste_somem_ao_fim_dele() -> Iterator[None]:
    """No PostgreSQL, o schema de cada teste é apagado quando ELE termina, não só no fim da sessão (achado #163).

    Em 27/09 o PostgreSQL do CI caiu por falha de segmentação duas vezes seguidas, sempre por volta dos 20 min, num
    `CREATE TABLE` das migrações 042–046 (runs 36356203609 e a repetição): cada teste cria o catálogo inteiro de
    novo, e com ~2.400 testes o catálogo acumulado (tabelas, índices, gatilhos e funções) passava do que o servidor
    do contêiner aguentava. Apagando ao fim de cada teste, o catálogo fica do tamanho de UM teste.

    A desmontagem de fixture automático roda depois da do `harness`, que fecha as conexões. Schema que não apague
    em 2 s (conexão ainda presa) fica na lista e sai no `pytest_sessionfinish`, como antes. Nenhum fixture de escopo
    de módulo ou de sessão cria banco — se algum passar a criar, este apagaria o banco dele no meio do módulo.
    """
    inicio = len(_SCHEMAS_DE_TESTE)
    _REUSO_NESTE_TESTE[0] = False                  # 29.63: a 1ª abertura de banco deste teste pode reusar o do worker
    yield
    novos = _SCHEMAS_DE_TESTE[inicio:]
    base = os.environ.get("TEST_DATABASE_URL")
    if not novos or not base:
        return
    try:
        import psycopg

        with psycopg.connect(base, autocommit=True, connect_timeout=5) as c:
            c.execute("SET lock_timeout = '2s'")
            for schema in novos:
                try:
                    c.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
                    _SCHEMAS_DE_TESTE.remove(schema)
                except Exception:
                    continue            # fica para o `pytest_sessionfinish`
    except Exception:
        pass                            # faxina nunca reprova um teste


@pytest.fixture(autouse=True)
def _transicoes_dentro_da_tabela() -> Iterator[None]:
    """Máquinas de estado da execução, fase "só conferir" (design §16; `modules/execution/domain/states.py`).

    Em produção, uma transição de execução, objetivo ou tentativa fora da tabela só AVISA (evento `log` `warn`) e é
    contada em `repository.TRANSICOES_FORA_DA_TABELA`. Aqui ela reprova o teste que a produziu: é a suíte inteira
    provando que a tabela descreve o que o código faz — a condição para o próximo passo, impor a tabela. Um teste que
    force uma transição fora de propósito devolve a contagem ao que era antes (ver `test_maquinas_de_estado.py`).
    """
    from app.taskqueue.repository import TRANSICOES_FORA_DA_TABELA

    antes = TRANSICOES_FORA_DA_TABELA.copy()
    yield
    novas = TRANSICOES_FORA_DA_TABELA - antes
    if novas:
        pytest.fail("transição de estado fora da tabela do domínio (máquina, de, para): "
                    f"{dict(novas)} — confira app/modules/execution/domain/states.py", pytrace=False)


@pytest.fixture(autouse=True)
def _telas_aprendidas_sao_do_teste() -> Iterator[None]:
    """O fornecedor das telas aprendidas e o observador da sessão (ADR-054, fatia 5) são estado do PROCESSO: o
    `AppState` de um teste os liga, e o motor de sessão montado à mão no teste seguinte os herdaria — com o banco do
    teste anterior. Cada teste começa e termina sem eles."""
    from app.integrations.app_declarado.conhecimento import definir_regras_aprendidas
    from app.integrations.app_declarado.sessao import definir_observador_da_sessao

    definir_regras_aprendidas(None)
    definir_observador_da_sessao(None)
    yield
    definir_regras_aprendidas(None)
    definir_observador_da_sessao(None)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
