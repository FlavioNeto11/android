"""Recursos EFETIVOS do worker e a admissão que o central faz com eles (adendo v0.20, C6 e C7).

Três defeitos travados aqui:

1. **O host no lugar do cgroup.** `psutil.virtual_memory()` lê a RAM do host; num contêiner ou numa unidade
   systemd com `MemoryMax=`, o que mata o emulador é o limite do cgroup. O parsing é testado com arquivos
   falsos — a suíte roda no Windows e nenhum Linux é necessário.
2. **Batida velha liberava a admissão.** `WorkerCapacity.sem_recurso()` devolvia `None` com a batida vencida, e
   o rodízio tratava "não sei" como "sobra tudo": mandava tantos `start` quantas vagas houvesse no tick.
3. **Contrato nos dois sentidos.** Campo novo tem padrão e os modelos ignoram o que não conhecem: agente antigo
   com central novo, e agente novo com central antigo, continuam se entendendo.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, create_model

from app.devices import recursos
from app.devices.recursos import (contar_cpus, cpu_do_cgroup, grupos_do_processo, medir, memoria_do_cgroup,
                                  niveis, pressao_de_memoria)
from app.workers.protocol import (FEATURE_RESERVA_DE_BOOT, ContadorAgregado, Heartbeat, Hello, Welcome,
                                  WorkerResources)
from app.workers.registry import (BATIDA_VELHA_S, PISO_RAM_MB, WorkerCapacity, ram_efetiva_mb,
                                  recurso_no_limite)

MB = 1024 * 1024
UNIDADE = "/system.slice/farm-worker.service"
V2 = "/sys/fs/cgroup"


def _leitor(arquivos: dict[str, str]) -> Any:
    """Leitor de arquivo falso: o que não está no dicionário não existe."""
    return arquivos.get


def _host(total_mb: int = 16384, livre_mb: int = 12000) -> Any:
    return lambda: SimpleNamespace(total=total_mb * MB, available=livre_mb * MB)


def _medir(arquivos: dict[str, str], *, linux: bool = True, total_mb: int = 16384,
           livre_mb: int = 12000) -> recursos.RecursosEfetivos:
    return medir(ler=_leitor(arquivos), linux=linux, memoria=_host(total_mb, livre_mb),
                 swap=lambda: SimpleNamespace(total=8 * 1024 * MB, percent=12.5), cpus=lambda: 8,
                 agora=lambda: "2026-09-26T12:00:00.000Z")


# ---------------------------------------------------------------- cgroup v2
def test_limite_da_unidade_systemd_vale_e_nao_o_da_raiz() -> None:
    """O erro clássico é ler `/sys/fs/cgroup/memory.max` na raiz, que no v2 nem existe: o serviço com
    `MemoryMax=4G` pareceria sem limite, e o central decidiria pelos 12 GB livres do host."""
    rec = _medir({
        "/proc/self/cgroup": f"0::{UNIDADE}\n",
        f"{V2}{UNIDADE}/memory.max": str(4096 * MB),
        f"{V2}{UNIDADE}/memory.current": str(1024 * MB),
        # Cache inativo é recuperável sem matar ninguém: a folga é limite − (uso − inativo).
        f"{V2}{UNIDADE}/memory.stat": f"anon 700000000\ninactive_file {256 * MB}\nactive_file 1000\n",
    })
    assert rec.mem_limit_mb == 4096
    assert rec.mem_available_mb == 4096 - (1024 - 256)
    assert rec.ram_free_mb == 12000, "a RAM do host continua declarada, ao lado da efetiva"
    assert rec.measured_at == "2026-09-26T12:00:00.000Z"


def test_limite_num_ancestral_e_encontrado_subindo_a_arvore() -> None:
    """A unidade em `max` e a FATIA com limite: o menor limite entre o processo e a raiz é o que vale, e a folga
    é medida no nível DELE."""
    rec = _medir({
        "/proc/self/cgroup": f"0::{UNIDADE}\n",
        f"{V2}{UNIDADE}/memory.max": "max\n",
        f"{V2}{UNIDADE}/memory.current": str(300 * MB),
        f"{V2}/system.slice/memory.max": str(2048 * MB),
        f"{V2}/system.slice/memory.current": str(1536 * MB),
    })
    assert (rec.mem_limit_mb, rec.mem_available_mb) == (2048, 512)


def test_max_em_toda_parte_e_sem_limite_e_a_disponivel_e_a_do_host() -> None:
    rec = _medir({"/proc/self/cgroup": f"0::{UNIDADE}\n", f"{V2}{UNIDADE}/memory.max": "max",
                  f"{V2}{UNIDADE}/memory.current": str(900 * MB)})
    assert rec.mem_limit_mb is None
    assert rec.mem_available_mb == 12000


def test_limite_que_nao_e_menor_que_o_host_nao_conta() -> None:
    rec = _medir({"/proc/self/cgroup": "0::/\n", f"{V2}/memory.max": str(32768 * MB),
                  f"{V2}/memory.current": str(100 * MB)}, total_mb=16384)
    assert rec.mem_limit_mb is None and rec.mem_available_mb == 12000


def test_limite_sem_uso_legivel_deixa_o_disponivel_desconhecido() -> None:
    """Revisão F8: era o limite inteiro (8192) — uso ilegível tratado como zero. O uso dentro do limite pode ser
    qualquer coisa: o disponível é DESCONHECIDO, e a guarda do boot recusa por isso, com o motivo."""
    rec = _medir({"/proc/self/cgroup": "0::/\n", f"{V2}/memory.max": str(8192 * MB)}, livre_mb=12000)
    assert rec.mem_limit_mb == 8192
    assert rec.mem_available_mb is None, "sem `memory.current`, o limite virou folga inteira"


def test_texto_ilegivel_vira_desconhecido() -> None:
    rec = _medir({"/proc/self/cgroup": "0::/\n", f"{V2}/memory.max": "lixo", f"{V2}/cpu.max": "x y z"})
    assert rec.mem_limit_mb is None and rec.cpu_effective is None
    assert rec.mem_available_mb == 12000


def test_host_menor_que_a_folga_do_cgroup_ganha() -> None:
    rec = _medir({"/proc/self/cgroup": "0::/\n", f"{V2}/memory.max": str(8192 * MB),
                  f"{V2}/memory.current": str(1024 * MB)}, livre_mb=3000)
    assert rec.mem_available_mb == 3000


# ---------------------------------------------------------------- cgroup v1 e máquina híbrida
def test_cgroup_v1_com_sentinela_de_sem_limite() -> None:
    sem_limite = {"/proc/self/cgroup": "4:memory:/user.slice\n",
                  "/sys/fs/cgroup/memory/user.slice/memory.limit_in_bytes": "9223372036854771712\n",
                  "/sys/fs/cgroup/memory/user.slice/memory.usage_in_bytes": str(500 * MB)}
    rec = _medir(sem_limite)
    assert rec.mem_limit_mb is None and rec.mem_available_mb == 12000


def test_cgroup_v1_de_conteiner_sem_namespace_acha_o_limite_na_raiz_da_montagem() -> None:
    """Sem namespace de cgroup, o caminho listado é o do HOST (`/docker/<id>`) e o do contêiner está montado na
    raiz: subir a árvore até a montagem é o que o encontra."""
    rec = _medir({"/proc/self/cgroup": "11:memory:/docker/abc123\n4:cpu,cpuacct:/docker/abc123\n",
                  "/sys/fs/cgroup/memory/memory.limit_in_bytes": str(3072 * MB),
                  "/sys/fs/cgroup/memory/memory.usage_in_bytes": str(1024 * MB),
                  "/sys/fs/cgroup/memory/memory.stat": "total_inactive_file 0\n",
                  "/sys/fs/cgroup/cpu,cpuacct/cpu.cfs_quota_us": "200000",
                  "/sys/fs/cgroup/cpu,cpuacct/cpu.cfs_period_us": "100000"})
    assert (rec.mem_limit_mb, rec.mem_available_mb) == (3072, 2048)
    assert rec.cpu_effective == 2.0


def test_maquina_hibrida_le_a_memoria_no_v1() -> None:
    grupos = grupos_do_processo(_leitor({"/proc/self/cgroup": "0::/a\n5:memory:/b\n"}))
    assert grupos == {"": "/a", "memory": "/b"}
    limite, _ = memoria_do_cgroup(_leitor({"/sys/fs/cgroup/memory/b/memory.limit_in_bytes": str(1024 * MB),
                                           f"{V2}/a/memory.max": str(4096 * MB)}), grupos, 16384)
    assert limite == 1024


def test_niveis_vao_do_processo_ate_a_raiz() -> None:
    assert niveis(V2, "/a/b") == [f"{V2}/a/b", f"{V2}/a", V2]
    assert niveis(V2, "/") == [V2]


# ---------------------------------------------------------------- CPU e pressão
@pytest.mark.parametrize("arquivos, esperado", [
    ({f"{V2}/cpu.max": "max 100000"}, None),
    ({f"{V2}/cpu.max": "150000 100000"}, 1.5),
    ({f"{V2}/cpu.max": "150000 100000", f"{V2}/cpuset.cpus.effective": "0-3,6\n"}, 1.5),
    ({f"{V2}/cpu.max": "max 100000", f"{V2}/cpuset.cpus.effective": "0-3,6\n"}, 5.0),
])
def test_cpu_efetiva_e_o_menor_entre_quota_e_conjunto(arquivos: dict[str, str], esperado: float | None) -> None:
    assert cpu_do_cgroup(_leitor(arquivos), {"": "/"}) == esperado


def test_cpu_v1_quota_menos_um_e_sem_quota() -> None:
    grupos = {"cpu": "/", "cpuacct": "/", "cpuset": "/"}
    assert cpu_do_cgroup(_leitor({"/sys/fs/cgroup/cpu/cpu.cfs_quota_us": "-1",
                                  "/sys/fs/cgroup/cpu/cpu.cfs_period_us": "100000"}), grupos) is None
    assert cpu_do_cgroup(_leitor({"/sys/fs/cgroup/cpuset/cpuset.effective_cpus": "0-1"}), grupos) == 2.0


@pytest.mark.parametrize("texto, esperado", [("0-3,6", 5), ("2", 1), ("", None), (None, None), ("a-b", None)])
def test_contar_cpus(texto: str | None, esperado: int | None) -> None:
    assert contar_cpus(texto) == esperado


def test_pressao_de_memoria_e_o_some_avg10() -> None:
    psi = "some avg10=12.34 avg60=3.00 avg300=1.00 total=123\nfull avg10=4.00 avg60=1.00 avg300=0.50 total=9\n"
    assert pressao_de_memoria(_leitor({"/proc/pressure/memory": psi})) == 12.34
    assert pressao_de_memoria(_leitor({})) is None, "kernel sem PSI é desconhecido, não zero"
    assert pressao_de_memoria(_leitor({"/proc/pressure/memory": "some avg10=nan? total"})) is None
    assert _medir({"/proc/pressure/memory": psi}).mem_pressure == 12.34


def test_fora_do_linux_nada_de_cgroup_e_inventado() -> None:
    """No Windows, limite de job, quota e PSI não são medidos: ficam `None`, mesmo que o leitor tivesse algo."""
    arquivos = {"/proc/self/cgroup": "0::/\n", f"{V2}/memory.max": str(1024 * MB),
                f"{V2}/memory.current": "0", "/proc/pressure/memory": "some avg10=50.00 total=1\n"}
    rec = _medir(arquivos, linux=False)
    assert (rec.mem_limit_mb, rec.cpu_effective, rec.mem_pressure) == (None, None, None)
    assert rec.mem_available_mb == rec.ram_free_mb == 12000
    assert rec.swap_used_pct == 12.5 and rec.cpu_count == 8


# ---------------------------------------------------------------- admissão no central
def _cap(**kw: Any) -> WorkerCapacity:
    base: dict[str, Any] = dict(connected=True, maintenance=False, max_slots=6, ram_free_mb=46000,
                                disk_free_gb=400.0, last_seen_at="2026-09-26T12:00:00Z", stale=False,
                                degraded_detail=None, idade_s=3.0)
    return WorkerCapacity("worker-lan-01", "Notebook da LAN", **{**base, **kw})


def test_batida_velha_segura_o_boot_novo_e_diz_por_que() -> None:
    """Era `None` ("não sei" não é "não pode"), e o rodízio mandava um `start` por vaga no mesmo tick para uma
    máquina de cujo estado ninguém sabia. Agora a porta fecha, com a frase."""
    velha = _cap(stale=True, idade_s=BATIDA_VELHA_S + 15)
    motivo = velha.sem_recurso()
    assert motivo is not None
    assert "sem medição recente" in motivo and f"{BATIDA_VELHA_S + 15:.0f} s" in motivo
    assert "espera a próxima batida" in motivo
    nunca = _cap(stale=True, idade_s=None, last_seen_at=None).sem_recurso()
    assert nunca is not None and "ainda não mandou batida" in nunca


def test_sem_ram_medida_nao_e_ram_de_sobra() -> None:
    motivo = _cap(ram_free_mb=None, mem_available_mb=None).sem_recurso()
    assert motivo is not None and "não informou RAM" in motivo


def _hello_de_teste() -> Hello:
    from .test_workers import _hello

    return _hello()


def test_limite_conhecido_com_disponivel_nulo_segura_o_boot_com_o_motivo() -> None:
    """Revisão F8: com `mem_available_mb` nulo a admissão caía na RAM do HOST, mesmo com um limite de cgroup
    conhecido. Nem o host nem o limite inteiro valem: o disponível dentro do limite é desconhecido."""
    motivo = _cap(ram_free_mb=46000, mem_available_mb=None, mem_limit_mb=1500).sem_recurso()
    assert motivo is not None and "limite de cgroup de 1500 MB" in motivo
    assert ram_efetiva_mb(WorkerResources(ram_free_mb=46000, mem_limit_mb=8192)) is None
    assert ram_efetiva_mb(WorkerResources(ram_free_mb=46000, mem_available_mb=3000, mem_limit_mb=8192)) == 3000


def test_admissao_usa_a_ram_efetiva_do_cgroup_e_nao_a_do_host() -> None:
    """O host tem 46 GB livres; o cgroup do serviço, 1,5 GB. Decidir pelo host mandaria o boot para o OOM."""
    motivo = _cap(ram_free_mb=46000, mem_available_mb=1500, mem_limit_mb=4096).sem_recurso()
    assert motivo is not None and "1500 MB" in motivo and "limite do cgroup 4096 MB" in motivo
    assert _cap(ram_free_mb=46000, mem_available_mb=8000).sem_recurso() is None


def test_admissao_desconta_a_ram_reservada_por_boots_em_andamento() -> None:
    """4 GB disponíveis e 2,7 GB já prometidos a um boot que ainda não alocou: sobra 1,3 GB, abaixo do piso."""
    cap = _cap(ram_free_mb=4000, mem_available_mb=4000, reserved_mb=2700)
    assert cap.ram_para_boot_mb() == 1300
    motivo = cap.sem_recurso()
    assert motivo is not None and "reservados para boots em andamento" in motivo
    assert _cap(ram_free_mb=4000, mem_available_mb=4000, reserved_mb=0).sem_recurso() is None


def test_agente_antigo_so_com_ram_do_host_segue_admitido_como_antes() -> None:
    cap = _cap(ram_free_mb=PISO_RAM_MB + 1, mem_available_mb=None, reserved_mb=None)
    assert cap.sem_recurso() is None
    assert _cap(ram_free_mb=PISO_RAM_MB - 1).sem_recurso() is not None


def test_degraded_usa_a_ram_efetiva_mas_nao_pisca_a_cada_boot() -> None:
    """Descontar a reserva na marca de `degraded` faria todo boot virar o worker para degradado e de volta."""
    assert recurso_no_limite(WorkerResources(ram_free_mb=46000, mem_available_mb=1500)) is not None
    assert recurso_no_limite(WorkerResources(ram_free_mb=4000, mem_available_mb=4000, reserved_mb=2700)) is None
    assert ram_efetiva_mb(WorkerResources()) is None


# ---------------------------------------------------------------- compatibilidade do protocolo (C6/C7)
NOVOS_EM_RECURSOS = {"mem_limit_mb", "mem_available_mb", "cpu_effective", "swap_used_pct", "mem_pressure",
                     "reserved_mb", "measured_at"}


def _modelo_antigo(modelo: type[BaseModel], sem: set[str], **troca: Any) -> type[BaseModel]:
    """O modelo como ele era ANTES desta onda: os mesmos campos, menos os novos, com `extra="ignore"` — que é o
    que o central e o agente em produção (`37bb6e6`) carregam."""
    campos = {nome: (troca.get(nome, info.annotation), info) for nome, info in modelo.model_fields.items()
              if nome not in sem}
    return create_model(f"{modelo.__name__}Antigo", __config__=ConfigDict(extra="ignore"), **campos)  # type: ignore[call-overload]


RecursosAntigo = _modelo_antigo(WorkerResources, NOVOS_EM_RECURSOS)
HelloAntigo = _modelo_antigo(Hello, {"features"}, resources=RecursosAntigo | None)
HeartbeatAntigo = _modelo_antigo(Heartbeat, {"metricas"}, resources=RecursosAntigo | None)
WelcomeAntigo = _modelo_antigo(Welcome, {"accepted_features"})


def _hello_novo() -> Hello:
    return Hello(worker_id="worker-lan-01", name="Notebook", agent_version="0.1.0+novo", os="linux",
                 features=[FEATURE_RESERVA_DE_BOOT],
                 resources=WorkerResources(ram_total_mb=16384, ram_free_mb=12000, mem_limit_mb=4096,
                                           mem_available_mb=3000, cpu_effective=1.5, swap_used_pct=3.0,
                                           mem_pressure=0.5, reserved_mb=2700, measured_at="2026-09-26T12:00:00Z"))


def test_central_novo_aceita_o_hello_do_agente_antigo() -> None:
    antigo = HelloAntigo(worker_id="worker-lan-01", name="Notebook", agent_version="0.1.0+37bb6e6", os="windows",
                         resources=RecursosAntigo(ram_total_mb=16384, ram_free_mb=12000)).model_dump()
    hello = Hello.model_validate(antigo)
    assert hello.features == []
    assert hello.resources is not None and hello.resources.mem_available_mb is None
    assert hello.resources.reserved_mb is None and hello.resources.measured_at is None
    assert ram_efetiva_mb(hello.resources) == 12000, "sem os campos novos, vale a RAM do host de antes"


def test_central_antigo_aceita_o_hello_e_a_batida_do_agente_novo() -> None:
    hello = HelloAntigo.model_validate(_hello_novo().model_dump())
    assert not hasattr(hello, "features")
    assert hello.resources is not None and hello.resources.ram_free_mb == 12000       # type: ignore[attr-defined]
    assert "mem_available_mb" not in hello.resources.model_dump()                     # type: ignore[attr-defined]
    nova = Heartbeat(resources=_hello_novo().resources,
                     metricas=[ContadorAgregado(nome="capacidade.reserva", rotulos={"resultado": "concedida"},
                                                valor=2)])
    batida = HeartbeatAntigo.model_validate(nova.model_dump())
    assert batida.resources.ram_free_mb == 12000                                      # type: ignore[attr-defined]
    assert not hasattr(batida, "metricas"), "central antigo ignora as contagens (e segue como antes)"


def test_central_novo_aceita_a_batida_do_agente_antigo_sem_metricas() -> None:
    antiga = HeartbeatAntigo(resources=RecursosAntigo(ram_free_mb=12000)).model_dump()
    assert Heartbeat.model_validate(antiga).metricas == []


# ---------------------------------------------------------------- métrica do agente no central (revisão F8)
def test_a_batida_soma_a_metrica_do_agente_no_central_com_o_rotulo_do_worker(tmp_path: Any) -> None:
    """`capacidade.reserva` ficava no processo do AGENTE; só o central grava janela e serve /api/desempenho. A
    batida leva o delta e o registro soma com `worker`. Nome ou rótulo desconhecido não vira série: a batida vem
    de outra máquina, e rótulo livre estouraria o teto de séries."""
    from app.metricas import metricas

    from .test_workers import _registro

    metricas.limpar()
    reg = _registro(tmp_path)
    reg.autenticar(_hello_de_teste(), token=None, enrollment=reg.criar_inscricao())
    contadores = [
        ContadorAgregado(nome="capacidade.reserva", rotulos={"resultado": "recusada", "motivo": "ram"}, valor=3),
        ContadorAgregado(nome="capacidade.reserva", rotulos={"resultado": "concedida"}, valor=1),
        ContadorAgregado(nome="capacidade.reserva", rotulos={"resultado": "recusada", "motivo": "android-04"},
                         valor=1),
        ContadorAgregado(nome="captura.total", rotulos={"origem": "x"}, valor=5),
        ContadorAgregado(nome="capacidade.reserva", rotulos={"resultado": "concedida"}, valor=10_000_000),
    ]
    reg.on_heartbeat("worker-lan-01", Heartbeat(resources=WorkerResources(ram_free_mb=46000), metricas=contadores))
    assert metricas.valor("capacidade.reserva", resultado="recusada", motivo="ram", worker="worker-lan-01") == 3
    assert metricas.valor("capacidade.reserva", resultado="concedida", worker="worker-lan-01") == 1
    assert metricas.total("capacidade.reserva") == 4, "rótulo fora do conjunto ou valor absurdo virou série"
    assert metricas.total("captura.total") == 0, "nome que o central não aceita do agente virou série"


def test_welcome_novo_e_lido_pelo_agente_antigo_e_o_antigo_pelo_novo() -> None:
    novo = Welcome(server_time="2026-09-26T12:00:00Z", accepted_features=[FEATURE_RESERVA_DE_BOOT]).model_dump()
    assert not hasattr(WelcomeAntigo.model_validate(novo), "accepted_features")
    antigo = WelcomeAntigo(server_time="2026-09-26T12:00:00Z").model_dump()
    assert Welcome.model_validate(antigo).accepted_features == []


def test_o_central_desta_onda_nao_aceita_feature_nenhuma(tmp_path: Any) -> None:
    """C7: só os campos nesta onda. O `welcome` sai com a lista vazia e o agente segue o caminho de antes."""
    from .test_workers import _registro

    reg = _registro(tmp_path)
    assert reg.welcome({}).accepted_features == []
    assert reg.welcome({}, [FEATURE_RESERVA_DE_BOOT]).accepted_features == [FEATURE_RESERVA_DE_BOOT]
