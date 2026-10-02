"""Achado #165: a guarda de capacidade do host passa a ser exercitada pelo caminho que roda em produção.

Antes, `_boot` começava com `if self.io_factory is not None: … return` — o desvio de teste ficava DENTRO do
código de produção, acima da guarda de RAM. Consequência: nenhum teste passava pela guarda. O que existia era
`devs.fake_boot_refusal = "Capacidade do host atingida: 900 MB disponíveis; …"`, quer dizer, o teste escrevia a
frase da recusa e depois conferia que ela aparecia — provava a si mesmo.

Agora a máquina entra por `EmulatorBackend` (memória livre, subida e encerramento do processo, descarte de
snapshot), a guarda é uma só (`_recusa_por_capacidade`) e vale para os dois caminhos. O que estes testes leem —
a frase, a espera crescente, o estado para onde o aparelho VOLTA, a linha de medição — é produzido pelo código
de produção, não pelo dublê.

T.2 (fatia que faltava) estendeu a mesma costura a `stop_instance`: o `if self.io_factory is not None` que
"hibernava" ou "desligava" o aparelho falso sem passar pela decisão real saiu de lá. Quem decide agora se o
snapshot foi salvo é `EmulatorBackend.save_snapshot` (real chama o console pelo `Adb`; falso obedece
`emulator.snapshot_ok`), e a elegibilidade de hibernação (`a.hibernation`, `rt.pid is not None`,
`snapshot_unsupported`, `fresh_data`) é a MESMA para os dois caminhos — os testes abaixo de
`test_hibernar_com_sucesso_passa_pelo_caminho_real_e_mede` em diante prendem isso.

`_wait_boot` só roda no caminho REAL (o `_boot` do aparelho falso continua retornando antes de chegar nele); o
ramo de prazo estourado agora tem teste, chamando o método direto — o mesmo padrão de
`test_a_decisao_depende_so_da_memoria_declarada` sobre `_recusa_por_capacidade` — com `boot_timeout_s=0`, que
estoura ANTES de qualquer sonda por `adb`. As sondas em si (`boot_completed`, `ui_ready`, `prepare_for_automation`) têm teste em `test_wait_boot_sondas.py` (J10), com dublês só nas três chamadas ao `Adb`; o veredito do snapshot durante o boot e a extração no agente remoto (`worker/executor.py`) seguem de fora (T.2).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.devices.emulator import SNAPSHOT_NAME
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
    harness.cfg.file.android.hibernation = True    # T.2: elegibilidade real passa a valer também pro aparelho falso
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
    harness.cfg.file.android.hibernation = True    # T.2: elegibilidade real passa a valer também pro aparelho falso
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


# ---------------------------------------------------------------------- T.2 (fatia que faltava): stop_instance
# real (hibernar, falhar ao salvar, hibernação desligada, parada simples) sem o desvio de teste.


async def test_hibernar_com_sucesso_passa_pelo_caminho_real_e_mede(harness: Harness) -> None:
    """Sem o `if self.io_factory` antigo, hibernar o aparelho falso agora entra pela MESMA elegibilidade e pelo
    MESMO backend do caminho real: precisa de `hibernation=True` e de PID (adoção passou a dar um)."""
    st = harness.state
    assert st is not None
    harness.cfg.file.android.hibernation = True
    rt = st.devices.get("android-01")
    assert rt.pid is not None, "T.2: a adoção do aparelho falso agora dá PID — é o que a elegibilidade exige"

    await st.devices.stop_instance(rt, hibernate=True)

    assert rt.state == InstanceState.hibernated and rt.snapshot_valid
    assert rt.pid is None, "o processo (falso) foi encerrado depois de salvar o snapshot"
    assert rt.avd_name in harness.emulator.stopped
    assert SNAPSHOT_NAME in harness.emulator.saved
    linha = st.db.query("SELECT * FROM measurements WHERE kind='hibernate'")[-1]
    dados = json.loads(linha["data"])
    assert dados["instance_id"] == "android-01" and dados["saved"] is True


async def test_hibernar_com_falha_no_snapshot_desliga_sem_hibernar_e_descarta(harness: Harness) -> None:
    """`emulator.snapshot_ok = False` é o valor que o TESTE escolhe (achado #165); quem decide o desfecho —
    desligar sem hibernar, com o motivo certo — é o código de produção, não o dublê."""
    st = harness.state
    assert st is not None
    harness.cfg.file.android.hibernation = True
    rt = st.devices.get("android-01")
    harness.emulator.snapshot_ok = False

    await st.devices.stop_instance(rt, hibernate=True)

    assert rt.state == InstanceState.stopped
    detalhe = rt.state_detail or ""
    assert "o snapshot não foi salvo" in detalhe and "o próximo boot será a frio" in detalhe
    assert SNAPSHOT_NAME not in harness.emulator.saved
    assert harness.emulator.discarded, "sem snapshot confiável, o antigo é descartado"
    linha = st.db.query("SELECT * FROM measurements WHERE kind='hibernate'")[-1]
    assert json.loads(linha["data"])["saved"] is False


async def test_hibernacao_desligada_na_configuracao_desliga_sem_tentar_salvar(harness: Harness) -> None:
    """`android.hibernation=False` (o padrão) é motivo por si só — nem chega a chamar `save_snapshot`."""
    st = harness.state
    assert st is not None
    rt = st.devices.get("android-01")
    assert harness.cfg.file.android.hibernation is False

    await st.devices.stop_instance(rt, hibernate=True)

    assert rt.state == InstanceState.stopped
    assert "hibernação desligada na configuração" in (rt.state_detail or "")
    assert not harness.emulator.saved


async def test_parada_simples_zera_o_pid_e_passa_pelo_backend_de_processo(harness: Harness) -> None:
    """Parada sem pedido de hibernar: o processo (falso) é encerrado pelo MESMO `stop_process` do caminho real,
    e o PID grava `None` — antes disso o aparelho falso nunca tinha PID nenhum para zerar."""
    st = harness.state
    assert st is not None
    rt = st.devices.get("android-01")
    assert rt.pid is not None

    await st.devices.stop_instance(rt)

    assert rt.state == InstanceState.stopped
    assert rt.pid is None
    assert rt.avd_name in harness.emulator.stopped


# ---------------------------------------------------------------------- T.2: o que ainda falta do achado #165


async def test_wait_boot_estoura_prazo_e_marca_erro(harness: Harness) -> None:
    """`_wait_boot` só roda no caminho REAL (o `_boot` do aparelho falso retorna antes de chegar nele) — segue
    listado no achado como o que falta extrair. Chamado direto, como já se faz com `_recusa_por_capacidade`,
    prova o ramo de prazo estourado sem emulador nenhum: `boot_timeout_s=0` estoura na PRIMEIRA volta do laço,
    antes de qualquer sonda por `adb` — é por isso que este teste não precisa de um backend mais fake do que já
    existe. As sondas em si: `test_wait_boot_sondas.py`."""
    import time

    st = harness.state
    assert st is not None
    harness.cfg.file.instances.overrides["android-01"] = {"boot_timeout_s": 0}
    rt = st.devices.get("android-01")

    ok = await st.devices._wait_boot(rt, time.monotonic(), warm=False)    # noqa: SLF001

    assert ok is False
    assert rt.state == InstanceState.error
    assert "Boot excedeu 0s" in (rt.state_detail or "")
