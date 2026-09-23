"""Achado #165: a guarda de capacidade do host passa a ser exercitada pelo caminho que roda em produção.

Antes, `_boot` começava com `if self.io_factory is not None: … return` — o desvio de teste ficava DENTRO do
código de produção, acima da guarda de RAM. Consequência: nenhum teste passava pela guarda. O que existia era
`devs.fake_boot_refusal = "Capacidade do host atingida: 900 MB disponíveis; …"`, quer dizer, o teste escrevia a
frase da recusa e depois conferia que ela aparecia — provava a si mesmo.

Agora a máquina entra por `EmulatorBackend` (memória livre, subida e encerramento do processo, descarte de
snapshot), a guarda é uma só (`_recusa_por_capacidade`) e vale para os dois caminhos. O que estes testes leem —
a frase, a espera crescente, o estado para onde o aparelho VOLTA, a linha de medição — é produzido pelo código
de produção, não pelo dublê.

Fica de fora, e está registrado no achado: `_wait_boot` (boot que estoura prazo), o veredito do snapshot e a
hibernação real seguem com desvio por `io_factory`; para prová-los é preciso a mesma extração aplicada ao
`stop_instance` e ao agente remoto (`worker/executor.py`).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.devices.emulator_backend import FakeEmulatorBackend, RealEmulatorBackend
from app.models import InstanceState

from .conftest import Harness


async def _desligar(h: Harness, iid: str = "android-01") -> object:
    st = h.state
    assert st is not None
    rt = st.devices.get(iid)
    await st.devices.stop_instance(rt)
    assert rt.state == InstanceState.stopped
    return rt


async def test_guarda_de_ram_recusa_o_boot_e_o_aparelho_volta_ao_estado_anterior(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    rt = await _desligar(harness)

    harness.emulator.free_mb = 900.0              # a máquina não tem memória para mais um aparelho
    await st.devices.start_instance(rt)           # type: ignore[arg-type]
    await harness.wait(lambda: rt.state != InstanceState.booting,  # type: ignore[attr-defined]
                       what="a guarda decidir")

    assert rt.state == InstanceState.stopped      # type: ignore[attr-defined]
    detalhe = rt.state_detail or ""               # type: ignore[attr-defined]
    assert "Capacidade do host atingida" in detalhe and "900 MB disponíveis" in detalhe
    assert "Libere memória no host" in detalhe    # a recusa diz o que a pessoa pode fazer
    assert rt.attention                           # type: ignore[attr-defined]  # e aparece no cartão
    assert st.db.query("SELECT * FROM measurements WHERE kind='capacity'"), "a recusa tem de virar medição"
    linha = st.db.query("SELECT * FROM measurements WHERE kind='capacity'")[-1]
    import json

    dados = json.loads(linha["data"])
    assert dados["instance_id"] == "android-01" and dados["refused"] is True
    assert dados["mem_available_mb"] == 900


async def test_recusa_seguida_espaca_as_tentativas_em_vez_de_martelar(harness: Harness) -> None:
    """A espera cresce a cada recusa: sem isso o rodízio tentaria ligar o mesmo aparelho a cada tick."""
    import time

    st = harness.state
    assert st is not None
    rt = await _desligar(harness)
    harness.emulator.free_mb = 500.0

    esperas = []
    for _ in range(3):
        rt.start_backoff_until = 0.0              # type: ignore[attr-defined]
        await st.devices.start_instance(rt)       # type: ignore[arg-type]
        await harness.wait(lambda: rt.state != InstanceState.booting,  # type: ignore[attr-defined]
                           what="a guarda decidir")
        esperas.append(rt.start_backoff_until - time.monotonic())      # type: ignore[attr-defined]

    assert rt.start_refusals == 3                 # type: ignore[attr-defined]
    assert esperas[0] < esperas[1] < esperas[2]   # 15 s → 30 s → 60 s
    assert esperas[2] <= 120                      # com teto: a espera não vira abandono


async def test_aparelho_hibernado_recusado_continua_hibernado_e_nao_perde_o_snapshot(harness: Harness) -> None:
    """Recusar por RAM não pode destruir o snapshot: o aparelho volta para onde estava, e o próximo boot é a quente."""
    st = harness.state
    assert st is not None
    rt = st.devices.get("android-01")
    await st.devices.stop_instance(rt, hibernate=True)
    assert rt.state == InstanceState.hibernated and rt.snapshot_valid

    harness.emulator.free_mb = 800.0
    await st.devices.start_instance(rt)
    await harness.wait(lambda: rt.state != InstanceState.booting, what="a guarda decidir")

    assert rt.state == InstanceState.hibernated
    assert rt.snapshot_valid, "o snapshot continua válido: nada foi iniciado para invalidá-lo"
    assert "Capacidade do host atingida" in (rt.state_detail or "")


async def test_com_memoria_sobrando_a_guarda_deixa_passar(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    rt = await _desligar(harness)
    harness.emulator.free_mb = 64_000.0
    await st.devices.start_instance(rt)           # type: ignore[arg-type]
    await harness.wait(lambda: rt.state == InstanceState.online,  # type: ignore[attr-defined]
                       what="o aparelho subir")
    assert rt.start_refusals == 0                 # type: ignore[attr-defined]


async def test_boot_em_andamento_reserva_memoria_do_proximo(harness: Harness) -> None:
    """O segundo boot desconta o que o primeiro ainda vai alocar — senão dois boots simultâneos passariam os dois."""
    st = harness.state
    assert st is not None
    a = harness.cfg.instance_android("android-02")
    estimado = a.est_ram_host_mb()                 # a MESMA conta do portão de boot (perfil da imagem quando não há número)

    primeiro = st.devices.get("android-01")
    segundo = st.devices.get("android-02")
    await st.devices.stop_instance(segundo)

    # `android-01` no meio de um boot, sem RSS medido: reserva a estimativa inteira.
    primeiro.state, primeiro.pid = InstanceState.booting, 4242
    primeiro.resources = None

    # Memória que COMPORTA uma instância, mas não duas: sem a reserva do boot em andamento, esta passaria.
    harness.emulator.free_mb = float(estimado + a.min_free_ram_mb_after_boot + 50)
    assert st.devices._recusa_por_capacidade(segundo, a) is not None      # noqa: SLF001
    assert "reservados para boots em andamento" in (segundo.state_detail or "")

    # Com o primeiro fora do boot, a mesma memória basta.
    primeiro.state, primeiro.pid = InstanceState.stopped, None
    segundo.start_refusals = 0
    assert st.devices._recusa_por_capacidade(segundo, a) is None          # noqa: SLF001


async def test_reset_marca_o_apagamento_para_o_proximo_boot(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    rt = st.devices.get("android-01")
    await st.devices.stop_instance(rt, hibernate=True)
    assert rt.snapshot_valid

    await st.devices.reset_instance(rt)
    # Reset é uma promessa sobre o PRÓXIMO boot: sem snapshot (senão o disco antigo voltaria) e com wipe marcado.
    assert rt.snapshot_valid is False
    assert harness.emulator.discarded, "o snapshot tem de ser descartado pela máquina, não só no banco"
    assert isinstance(harness.emulator.discarded[-1], Path)
    await harness.wait(lambda: rt.state == InstanceState.online, what="o aparelho voltar")


def test_a_maquina_de_verdade_nao_e_um_esqueleto() -> None:
    """O dublê só vale se a implementação real fizer o que ele finge: memória livre de verdade, em MB."""
    import psutil

    real = RealEmulatorBackend()
    livre = real.free_ram_mb()
    assert livre > 0
    esperado = psutil.virtual_memory().available / 2**20
    assert abs(livre - esperado) < esperado * 0.5      # mesma ordem de grandeza, mesma unidade


def test_o_duble_declara_memoria_generosa_por_padrao() -> None:
    """Se o padrão fosse a RAM desta máquina, a suíte recusaria boots quando os emuladores estivessem ligados."""
    assert FakeEmulatorBackend().free_ram_mb() >= 32_000


async def test_aparelho_externo_nao_gasta_ram_desta_maquina(tmp_path: Path) -> None:
    """Aparelho de outra máquina não passa pela guarda daqui: a RAM que ele gasta é a de lá."""
    h = Harness(tmp_path, 3, external={"android-03": "127.0.0.1:15555"})
    await h.boot()
    try:
        st = h.state
        assert st is not None
        rt = st.devices.get("android-03")
        h.emulator.free_mb = 10.0                 # esta máquina sem memória nenhuma
        await st.devices.start_instance(rt)
        await h.wait(lambda: rt.state != InstanceState.booting, what="a decisão sobre o externo")
        assert "Capacidade do host atingida" not in (rt.state_detail or "")
    finally:
        if h.state is not None:
            await h.state.stop()


@pytest.mark.parametrize("livre,cabe", [(300.0, False), (64_000.0, True)])
async def test_a_decisao_depende_so_da_memoria_declarada(harness: Harness, livre: float, cabe: bool) -> None:
    """A mesma chamada, dois valores de memória, dois desfechos — e nada mais muda."""
    st = harness.state
    assert st is not None
    rt = st.devices.get("android-02")
    a = harness.cfg.instance_android(rt.id)
    harness.emulator.free_mb = livre
    assert (st.devices._recusa_por_capacidade(rt, a) is None) is cabe     # noqa: SLF001
