"""Renderizador do emulador por aparelho e por worker (29.11).

O que foi medido com infraestrutura real em 30/09/2026 (e que estes testes NÃO repetem): o Outlook 5.2635.3 derruba
o processo do emulador 37.1.11 quando o GLES do host é o SwiftShader (`-gpu swiftshader_indirect`, o padrão do parque),
e abre estável com `-gpu host`. E que **argumento aceito não é renderizador usado**: o emulador troca de renderizador
sem avisar, e só a linha `emuglConfig_init: …` do log diz qual ele selecionou.

Tudo aqui é `simulated`: texto de log escrito pelo teste, aparelhos do harness (porta base 5640) e o adb falso da loja
de apps — nenhum emulador sobe e nenhum APK é instalado. O que estes testes protegem:

* a leitura do log: vale a ÚLTIMA linha (uma por subida), log sem a linha é "não se sabe", apelidos se equivalem;
* o renderizador selecionado aparece no DTO do aparelho depois do boot, some fora do ar e é relido no boot seguinte;
* pedido `host` com `swiftshader` selecionado vira atenção do aparelho, com o pedido, o selecionado e o log;
* `renderizador_recusado` é uma chave do `app.yaml`: valor desconhecido derruba a carga, e o Outlook a declara;
* canário, instalação, distribuição (e a prévia), os destinos e a porta do app recusam o app no aparelho cujo
  renderizador ele recusa, e seguem no aparelho com `host`; app sem a chave não muda em nada;
* o aparelho de outra máquina declara o renderizador na batida; sem declaração, o app que tem a chave é recusado
  com "renderizador desconhecido".
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from app.contracts.worker import protocol
from app.devices import compatibilidade
from app.devices import emulator as emu
from app.integrations.app_declarado import pacote as pacote_declarado
from app.models import InstanceState, ReleaseChannel
from app.modules.applications.infrastructure import registry
from app.releases.catalog import ReleaseValidationError
from app.worker.settings import DeviceSpec, WorkerSettings

from .conftest import Harness
from .test_distribute import PACOTE
from .test_loja_de_apps import OUTLOOK, estado, falsificar, pronto, versao
from .test_sempre_na_promovida import cliente

#: O que o emulador 37.1.11 escreve a cada subida (30/09/2026, android-07 com `-gpu host`).
LINHA_HOST = "INFO         | emuglConfig_init: vulkan_mode_selected:host gles_mode_selected:host"
LINHA_SWIFT = "INFO         | emuglConfig_init: vulkan_mode_selected:swiftshader gles_mode_selected:swiftshader"
SWIFT = {"android-02": {"gpu_mode": "swiftshader_indirect"}}


# ==================================================================== a leitura do log
def test_a_ultima_linha_do_log_e_a_que_vale() -> None:
    """O log é aberto em append: cada subida escreve a sua linha, e a de antes descreve um processo que já morreu."""
    texto = "\n".join(["emulator: boot 1", LINHA_SWIFT, "barulho qualquer", LINHA_HOST, "INFO | Boot completed"])
    lido = emu.renderizador_do_log(texto)
    assert lido is not None and (lido.gles, lido.vulkan) == ("host", "host")
    ao_contrario = emu.renderizador_do_log("\n".join([LINHA_HOST, LINHA_SWIFT]))
    assert ao_contrario is not None and ao_contrario.gles == "swiftshader"


def test_log_sem_a_linha_e_nao_se_sabe() -> None:
    assert emu.renderizador_do_log("") is None
    assert emu.renderizador_do_log("INFO | Boot completed in 41233 ms\nWARNING | -gpu angle_indirect not valid") is None


def test_gles_e_vulkan_sao_lidos_separados() -> None:
    lido = emu.renderizador_do_log("x | emuglConfig_init: vulkan_mode_selected:swangle gles_mode_selected:swiftshader\r\n")
    assert lido is not None and (lido.gles, lido.vulkan) == ("swiftshader", "swangle")


def test_apelidos_do_mesmo_renderizador_se_equivalem() -> None:
    assert emu.normalizar_renderizador("swiftshader_indirect") == "swiftshader"
    assert emu.normalizar_renderizador(" SwiftShader ") == "swiftshader"
    assert emu.normalizar_renderizador("host") == "host"
    assert emu.normalizar_renderizador(None) is None and emu.normalizar_renderizador("") is None
    # Pedido `swiftshader_indirect`, selecionado `swiftshader`: é o que foi pedido, não um fallback.
    assert emu.houve_fallback("swiftshader_indirect", "swiftshader") is False
    assert emu.houve_fallback("host", "host") is False
    assert emu.houve_fallback("host", "swiftshader") is True
    # `-gpu swangle` é aceito e acaba em `gles_mode_selected:swiftshader` (medido): argumento aceito, outro usado.
    assert emu.houve_fallback("swangle", "swiftshader") is True
    # Sem selecionado não há o que comparar; `auto` é pedir "o que houver".
    assert emu.houve_fallback("host", None) is False
    assert emu.houve_fallback("auto", "swiftshader") is False


def test_a_leitura_do_arquivo_acha_a_ultima_linha_e_tolera_a_ausencia(tmp_path: Path) -> None:
    assert emu.ler_renderizador(tmp_path, "android-01") is None                    # arquivo não existe
    log = tmp_path / "emulator-android-01.log"
    log.write_bytes(b"lixo \xff\xfe sem a linha\n")
    assert emu.ler_renderizador(tmp_path, "android-01") is None
    with log.open("ab") as fh:
        fh.write((LINHA_SWIFT + "\n").encode() + b"x" * 300_000 + ("\n" + LINHA_HOST + "\n").encode())
    lido = emu.ler_renderizador(tmp_path, "android-01")
    assert lido is not None and lido.gles == "host"


# ==================================================================== exposição no DTO e aviso de fallback
async def _ligado(h: Harness, rt: Any) -> None:
    await h.state.devices.start_instance(rt)                                      # type: ignore[union-attr]
    await h.wait(lambda: rt.state == InstanceState.online, what=f"{rt.id} ligado")


async def test_renderizador_selecionado_aparece_no_dto_depois_do_boot(tmp_path: Path) -> None:
    h = Harness(tmp_path, 2, overrides=SWIFT)
    h.emulator.gles, h.emulator.vulkan = "host", "host"
    await h.boot()
    assert h.state is not None
    try:
        devs = h.state.devices
        rt = devs.get("android-01")
        dto = devs.dto(rt)
        assert dto.renderer is not None
        assert dto.renderer.model_dump() == {"configured": "host", "gles": "host", "vulkan": "host", "fallback": False}
        assert dto.attention is None
        # É o que `GET /api/instances` devolve: o campo é do DTO, não de uma rota à parte.
        async with cliente(h) as c:
            pela_api = {i["id"]: i for i in (await c.get("/api/instances")).json()}
        assert pela_api["android-01"]["renderer"] == {"configured": "host", "gles": "host", "vulkan": "host",
                                                      "fallback": False}

        # Fora do ar não há processo: o selecionado some, o pedido fica.
        await devs.stop_instance(rt)
        parado = devs.dto(rt).renderer
        assert parado is not None and (parado.configured, parado.gles, parado.vulkan) == ("host", None, None)
        assert parado.fallback is False

        # Boot seguinte: a linha nova é a que vale (o log é o mesmo arquivo, em append).
        h.emulator.gles = h.emulator.vulkan = "swiftshader"
        await _ligado(h, rt)
        de_novo = devs.dto(rt).renderer
        assert de_novo is not None and de_novo.gles == "swiftshader" and de_novo.fallback is True
    finally:
        await h.state.stop()


async def test_emulador_que_nao_escreveu_a_linha_fica_so_com_o_pedido(tmp_path: Path) -> None:
    h = Harness(tmp_path, 2, overrides=SWIFT)                # o dublê da máquina não escreve nada por padrão
    await h.boot()
    assert h.state is not None
    try:
        dto = h.state.devices.dto(h.state.devices.get("android-02"))
        assert dto.renderer is not None
        assert dto.renderer.model_dump() == {"configured": "swiftshader_indirect", "gles": None, "vulkan": None,
                                             "fallback": False}
        assert dto.attention is None
    finally:
        await h.state.stop()


async def test_fallback_silencioso_vira_atencao_do_aparelho(tmp_path: Path) -> None:
    """Pedido `host`, selecionado `swiftshader`: o emulador não reclama, e o app que recusa o SwiftShader derrubaria
    o aparelho. O cartão diz o pedido, o selecionado e onde ler o log."""
    h = Harness(tmp_path, 2, overrides=SWIFT)
    h.emulator.gles, h.emulator.vulkan = "swiftshader", "swiftshader"
    await h.boot()
    assert h.state is not None
    try:
        devs = h.state.devices
        rt = devs.get("android-01")                                   # pediu `host` (o padrão do harness)
        dto = devs.dto(rt)
        assert dto.renderer is not None and dto.renderer.fallback is True
        assert dto.attention is not None
        assert "host" in dto.attention and "swiftshader" in dto.attention
        assert str(h.cfg.logs_dir / f"emulator-{rt.avd_name}.log") in dto.attention
        # Quem pediu `swiftshader_indirect` e recebeu `swiftshader` recebeu o que pediu: sem aviso.
        pedido = devs.dto(devs.get("android-02"))
        assert pedido.renderer is not None and pedido.renderer.fallback is False and pedido.attention is None

        # O aviso não atropela outro assunto do cartão, e volta quando o outro some.
        rt.attention = "Bloqueado: outra coisa"
        assert devs.dto(rt).attention == "Bloqueado: outra coisa"
        rt.attention = None
        assert devs.dto(rt).attention == dto.attention
        # Fora do ar o aviso some: não há emulador selecionando nada.
        await devs.stop_instance(rt)
        assert devs.dto(rt).attention is None
    finally:
        await h.state.stop()


# ==================================================================== o requisito do app, como dado
def _app(tmp_path: Path, **campos: Any) -> Path:
    pasta = tmp_path / "br.exemplo.correio"
    pasta.mkdir(parents=True)
    dados = {"app": "br.exemplo.correio", "nome": "Correio de Exemplo", **campos}
    (pasta / "app.yaml").write_text(yaml.safe_dump(dados, allow_unicode=True), encoding="utf-8")
    return pasta


def test_app_yaml_aceita_renderizador_recusado(tmp_path: Path) -> None:
    m = pacote_declarado.manifesto_da_pasta(_app(tmp_path, renderizador_recusado=["swiftshader"]))
    assert m.definition.refused_renderers == ("swiftshader",)


def test_app_yaml_aceita_o_apelido_e_guarda_o_nome_canonico(tmp_path: Path) -> None:
    m = pacote_declarado.manifesto_da_pasta(_app(tmp_path, renderizador_recusado=["swiftshader_indirect"]))
    assert m.definition.refused_renderers == ("swiftshader",)


def test_app_yaml_sem_a_chave_nao_recusa_nada(tmp_path: Path) -> None:
    assert pacote_declarado.manifesto_da_pasta(_app(tmp_path)).definition.refused_renderers == ()


@pytest.mark.parametrize("valor", [["skiavk"], ["swiftshadder"], "swiftshader", [3], ["host", "angle"]])
def test_app_yaml_recusa_renderizador_desconhecido(tmp_path: Path, valor: Any) -> None:
    with pytest.raises(pacote_declarado.PacoteInvalido, match="renderizador_recusado"):
        pacote_declarado.manifesto_da_pasta(_app(tmp_path, renderizador_recusado=valor))


def test_app_que_recusa_todos_os_renderizadores_nao_carrega(tmp_path: Path) -> None:
    """Recusar `host` e `swiftshader` é dizer que o app não roda em emulador nenhum: isso não é requisito, é erro."""
    with pytest.raises(pacote_declarado.PacoteInvalido, match="renderizador_recusado"):
        pacote_declarado.manifesto_da_pasta(_app(tmp_path, renderizador_recusado=["host", "swiftshader"]))


def test_o_outlook_declara_que_recusa_o_swiftshader_e_o_instagram_nao() -> None:
    assert registry.capabilities_of(OUTLOOK).refused_renderers == ("swiftshader",)
    assert registry.capabilities_of("com.instagram.android").refused_renderers == ()
    assert registry.capabilities_of("br.app.sem.registro").refused_renderers == ()


# ==================================================================== a regra, sem aparelho
def _req(*recusados: str) -> Any:
    return compatibilidade.Requisitos(rotulo="com.microsoft.office.outlook 7.0", app="Outlook",
                                      renderizador_recusado=tuple(recusados))


def test_motivo_diz_o_app_o_renderizador_e_o_que_configurar() -> None:
    cap = compatibilidade.Capacidades(renderizador="swiftshader", renderizador_pedido="swiftshader")
    porque = compatibilidade.motivo_incompativel(_req("swiftshader"), cap, aparelho="android-02")
    assert porque is not None
    assert "Outlook" in porque and "SwiftShader" in porque and "gpu_mode: host" in porque and "android-02" in porque
    assert compatibilidade.motivo_do_renderizador(_req("swiftshader"), cap, aparelho="android-02") == porque


def test_com_host_ou_sem_a_chave_nao_ha_recusa() -> None:
    swift = compatibilidade.Capacidades(renderizador="swiftshader", renderizador_pedido="swiftshader")
    host = compatibilidade.Capacidades(renderizador="host", renderizador_pedido="host")
    assert compatibilidade.motivo_incompativel(_req("swiftshader"), host) is None
    assert compatibilidade.motivo_incompativel(_req(), swift) is None
    assert compatibilidade.motivo_incompativel(_req(), compatibilidade.Capacidades()) is None


def test_fallback_e_dito_como_fallback() -> None:
    """Pediu `host` e o emulador selecionou SwiftShader: mandar "configure host" seria mandar fazer o que já está."""
    cap = compatibilidade.Capacidades(renderizador="swiftshader", renderizador_pedido="host")
    porque = compatibilidade.motivo_incompativel(_req("swiftshader"), cap, aparelho="android-01")
    assert porque is not None and "selecionou" in porque and "emuglConfig_init" in porque


def test_renderizador_desconhecido_recusa_so_o_app_que_tem_a_chave() -> None:
    """A exceção à regra "o que não se sabe nunca vira recusa": errar aqui derruba um emulador com conta logada."""
    sem_nada = compatibilidade.Capacidades()
    porque = compatibilidade.motivo_incompativel(_req("swiftshader"), sem_nada, aparelho="android-09")
    assert porque is not None and "renderizador desconhecido" in porque and "android-09" in porque
    assert compatibilidade.motivo_incompativel(_req(), sem_nada) is None
    # Aparelho físico não tem renderizador de emulador: o requisito não se aplica a ele.
    fisico = compatibilidade.Capacidades(device_kind="physical")
    assert compatibilidade.motivo_incompativel(_req("swiftshader"), fisico) is None


# ==================================================================== canário, instalação e distribuição
async def _parque(tmp_path: Path, **kw: Any) -> Harness:
    """android-01 pede `host` (o padrão do harness); android-02 pede `swiftshader_indirect`, o padrão do parque."""
    h = Harness(tmp_path, 3, **{"overrides": SWIFT, **kw})
    await h.boot()
    return h


async def test_canario_do_outlook_e_recusado_no_swiftshader_e_aceito_no_host(tmp_path: Path) -> None:
    h = await _parque(tmp_path)
    assert h.state is not None
    try:
        falsos = falsificar(h)
        st = h.state
        rid = versao(h, OUTLOOK, 7, promovida=False)
        with pytest.raises(ReleaseValidationError) as exc:
            await st.releases.start_canary(st.devices.get("android-02"), rid, st.installer)
        assert "Outlook" in str(exc.value) and "SwiftShader" in str(exc.value) and "gpu_mode: host" in str(exc.value)
        # Recusado ANTES de mexer em qualquer coisa: a versão não entrou em prova e nada tocou o aparelho.
        assert st.release_repo.release_row(rid)["channel"] != ReleaseChannel.canary.value
        assert falsos["android-02"].calls == [] and estado(h, "android-02", OUTLOOK) is None

        await st.releases.start_canary(st.devices.get("android-01"), rid, st.installer)
        assert st.release_repo.release_row(rid)["channel"] == ReleaseChannel.canary.value
        assert estado(h, "android-01", OUTLOOK)["state"] == "ready"
    finally:
        await h.state.stop()


async def test_canario_pela_rota_recusa_antes_de_aceitar(tmp_path: Path) -> None:
    """O trabalho do canário roda em segundo plano: recusado lá dentro, a rota já teria respondido "aceito"."""
    h = await _parque(tmp_path)
    assert h.state is not None
    try:
        falsificar(h)
        rid = versao(h, OUTLOOK, 7, promovida=False)
        async with cliente(h) as c:
            r = await c.post(f"/api/releases/{rid}/lifecycle", json={"verb": "canary", "instance_id": "android-02"})
            assert r.status_code == 409, r.text
            assert r.json()["detail"]["code"] == "app_incompativel"
            assert "SwiftShader" in r.json()["detail"]["message"]
            assert h.state.release_repo.release_row(rid)["channel"] != ReleaseChannel.canary.value
            aceito = await c.post(f"/api/releases/{rid}/lifecycle", json={"verb": "canary", "instance_id": "android-01"})
            assert aceito.status_code == 200 and aceito.json()["accepted"] is True, aceito.text
        await pronto(h, "android-01", rid, OUTLOOK)
    finally:
        await h.state.stop()


async def test_instalacao_do_outlook_e_recusada_no_swiftshader_e_aceita_no_host(tmp_path: Path) -> None:
    h = await _parque(tmp_path)
    assert h.state is not None
    try:
        falsos = falsificar(h)
        st = h.state
        rid = versao(h, OUTLOOK, 7)
        with pytest.raises(ReleaseValidationError, match="SwiftShader"):
            await st.releases.install_on(st.devices.get("android-02"), rid, st.installer)
        assert falsos["android-02"].calls == [] and estado(h, "android-02", OUTLOOK) is None
        async with cliente(h) as c:
            r = await c.post("/api/instances/android-02/app/install", json={"release_id": rid})
            assert r.status_code == 409 and r.json()["detail"]["code"] == "app_incompativel", r.text
            assert "gpu_mode: host" in r.json()["detail"]["message"]
            aceito = await c.post("/api/instances/android-01/app/install", json={"release_id": rid})
            assert aceito.status_code == 202, aceito.text
        await pronto(h, "android-01", rid, OUTLOOK)
    finally:
        await h.state.stop()


async def test_distribuicao_e_previa_pulam_o_aparelho_com_o_renderizador_recusado(tmp_path: Path) -> None:
    h = await _parque(tmp_path)
    assert h.state is not None
    try:
        falsos = falsificar(h)
        st = h.state
        rid = versao(h, OUTLOOK, 7)
        previa = {d["id"]: d for d in st.distribute(rid, dry_run=True)}
        assert previa["android-02"]["outcome"] == "incompatible" and "SwiftShader" in previa["android-02"]["reason"]
        assert previa["android-01"]["outcome"] == "would_start" and previa["android-03"]["outcome"] == "would_start"
        # "N aparelhos" escolhe entre os que PODEM receber.
        assert [d["id"] for d in st.distribute(rid, count=3, dry_run=True)] == ["android-01", "android-03"]

        saida = {d["id"]: d for d in st.distribute(rid)}
        assert saida["android-02"]["outcome"] == "incompatible"
        assert "gpu_mode: host" in saida["android-02"]["reason"]
        assert saida["android-01"]["outcome"] == "started"
        await pronto(h, "android-01", rid, OUTLOOK)
        await pronto(h, "android-03", rid, OUTLOOK)
        # No recusado: nem versão desejada gravada, nem um toque no aparelho.
        assert estado(h, "android-02", OUTLOOK) is None and falsos["android-02"].calls == []
        async with cliente(h) as c:
            alvos = {a["id"]: a for a in (await c.get(f"/api/releases/{rid}/targets")).json()["targets"]}
        assert alvos["android-02"]["compatible"] is False and "SwiftShader" in alvos["android-02"]["reason"]
        assert alvos["android-01"]["compatible"] is True
    finally:
        await h.state.stop()


async def test_o_selecionado_vale_mais_que_o_pedido(tmp_path: Path) -> None:
    """Pediu `host` e o emulador caiu para o SwiftShader: quem derruba o aparelho é o renderizador em uso."""
    h = Harness(tmp_path, 3, overrides=SWIFT)
    h.emulator.gles = h.emulator.vulkan = "swiftshader"
    await h.boot()
    assert h.state is not None
    try:
        falsos = falsificar(h)
        st = h.state
        rid = versao(h, OUTLOOK, 7)
        saida = {d["id"]: d for d in st.distribute(rid)}
        assert {d["outcome"] for d in saida.values()} == {"incompatible"}
        assert "selecionou" in saida["android-01"]["reason"]
        assert all(f.calls == [] for f in falsos.values())
        with pytest.raises(ReleaseValidationError, match="SwiftShader"):
            await st.releases.install_on(st.devices.get("android-01"), rid, st.installer)
    finally:
        await h.state.stop()


async def test_app_sem_a_chave_nao_muda_em_nada(tmp_path: Path) -> None:
    h = await _parque(tmp_path)
    assert h.state is not None
    try:
        falsificar(h)
        st = h.state
        rid = versao(h)                                              # o app de QA: nenhum requisito de renderizador
        saida = {d["id"]: d for d in st.distribute(rid)}
        assert {d["outcome"] for d in saida.values()} == {"started"}
        for iid in ("android-01", "android-02", "android-03"):
            await pronto(h, iid, rid)
        assert st._app_preflight(st.devices.get("android-02"), [PACOTE]) is None
    finally:
        await h.state.stop()


async def test_porta_do_app_recusa_a_tarefa_no_aparelho_com_o_renderizador_recusado(tmp_path: Path) -> None:
    """Instalar não é o único jeito de derrubar o aparelho: o app que já estava lá (instalado quando o aparelho era
    `host`, ou à mão) seria ABERTO pela tarefa. O pré-voo diz antes de agendar; a porta bloqueia para uma pessoa."""
    h = await _parque(tmp_path)
    assert h.state is not None
    try:
        falsos = falsificar(h)
        st = h.state
        rt1, rt2 = st.devices.get("android-01"), st.devices.get("android-02")
        recusa = st._app_preflight(rt2, [OUTLOOK])
        assert recusa is not None and recusa["code"] == "app_incompativel"
        assert "Outlook" in recusa["motivo"] and "SwiftShader" in recusa["motivo"] and "gpu_mode" in recusa["acao"]
        assert st._app_preflight(rt1, [OUTLOOK]) is None

        pendente = {"status": "pending"}
        porta = st._app_resolver(rt2, OUTLOOK, pendente)
        assert porta is not None and porta[1] is None and "SwiftShader" in porta[0]
        assert st._app_resolver(rt1, OUTLOOK, pendente) is None
        # A entrega que chegue por outro caminho também para antes do aparelho, e não vira `install_failed`.
        rid = versao(h, OUTLOOK, 7)
        with pytest.raises(ReleaseValidationError, match="SwiftShader"):
            await st._entregar(rt2, OUTLOOK, rid)
        assert estado(h, "android-02", OUTLOOK) is None and falsos["android-02"].calls == []
    finally:
        await h.state.stop()


async def test_abrir_e_instalar_pelo_painel_sao_recusados_no_aparelho_com_o_renderizador_recusado(
        tmp_path: Path) -> None:
    """O caminho de uma pessoa: o botão "Abrir app" (e o "Instalar") do aparelho. É aqui que se derruba, por engano,
    um emulador com conta logada — a recusa vem antes do 202 e fica no histórico do aparelho, com o motivo."""
    h = await _parque(tmp_path)
    assert h.state is not None
    try:
        falsificar(h)
        versao(h, OUTLOOK, 7)                                        # importar a versão cadastra o app sozinho
        app_id = h.state.db.scalar("SELECT id FROM apps WHERE package=?", (OUTLOOK,))
        async with cliente(h) as c:
            for verbo in ("open_app", "install_apk"):
                r = await c.post(f"/api/instances/android-02/actions/{verbo}", json={"app_id": app_id})
                assert r.status_code == 409, r.text
                detalhe = r.json()["detail"]
                assert detalhe["code"] == "app_incompativel", detalhe
                assert "Outlook" in detalhe["message"] and "SwiftShader" in detalhe["message"]
                comando = h.state.commands.get(detalhe["command_id"])
                assert comando["state"] == "rejected" and "gpu_mode: host" in comando["reason"]
            # No aparelho com `host` o mesmo app abre; e o app sem a chave segue abrindo no SwiftShader.
            aceito = await c.post("/api/instances/android-01/actions/open_app", json={"app_id": app_id})
            assert aceito.status_code == 202, aceito.text
            qa = await c.post("/api/instances/android-02/actions/open_app", json={})
            assert qa.status_code == 202, qa.text
    finally:
        await h.state.stop()


# ==================================================================== o aparelho de outra máquina
def _declarado(**campos: Any) -> Any:
    return protocol.WorkerDevice(serial="emulator-5554", avd_name="w-03", instance_id="android-03", kind="emulator",
                                 **campos)


async def _remoto(tmp_path: Path) -> Harness:
    h = Harness(tmp_path, 3, external={"android-03": "127.0.0.1:15555"})
    await h.boot()
    assert h.state is not None
    h.state.db.execute("UPDATE instances SET worker_id=? WHERE id=?", ("worker-lan-01", "android-03"))
    h.state.devices.get("android-03").worker_id = "worker-lan-01"
    return h


def test_o_contrato_do_worker_leva_o_renderizador_e_aceita_agente_antigo() -> None:
    novo = _declarado(gpu_mode="host", gpu_gles="host", gpu_vulkan="host")
    assert (novo.gpu_mode, novo.gpu_gles, novo.gpu_vulkan) == ("host", "host", "host")
    antigo = protocol.WorkerDevice.model_validate({"serial": "emulator-5554"})
    assert (antigo.gpu_mode, antigo.gpu_gles, antigo.gpu_vulkan) == (None, None, None)


async def test_o_worker_declara_o_renderizador_dos_aparelhos_dele(tmp_path: Path) -> None:
    h = await _remoto(tmp_path)
    assert h.state is not None
    try:
        devs = h.state.devices
        rt = devs.get("android-03")
        assert devs.dto(rt).renderer is None                     # agente antigo: nada declarado, nada afirmado
        devs.capacidades_do_worker("worker-lan-01", [_declarado(gpu_mode="host", gpu_gles="host", gpu_vulkan="host")])
        assert devs.dto(rt).renderer.model_dump() == {"configured": "host", "gles": "host", "vulkan": "host",
                                                      "fallback": False}
        # A batida seguinte, com o emulador fora do ar lá: o selecionado some, o pedido fica.
        devs.capacidades_do_worker("worker-lan-01", [_declarado(gpu_mode="host")])
        assert devs.dto(rt).renderer.model_dump() == {"configured": "host", "gles": None, "vulkan": None,
                                                      "fallback": False}
        # Fallback na outra máquina também vira atenção, e o texto manda ler o log LÁ.
        devs.capacidades_do_worker("worker-lan-01", [_declarado(gpu_mode="host", gpu_gles="swiftshader",
                                                                gpu_vulkan="swiftshader")])
        dto = devs.dto(rt)
        assert dto.renderer.fallback is True and dto.attention is not None
        assert "worker-lan-01" in dto.attention and "emulator-w-03.log" in dto.attention
        # Declaração de outro worker não fala por este aparelho.
        devs.capacidades_do_worker("outro-worker", [_declarado(gpu_mode="swiftshader_indirect")])
        assert devs.dto(rt).renderer.configured == "host"
    finally:
        await h.state.stop()


async def test_aparelho_remoto_sem_declaracao_recusa_o_outlook_por_renderizador_desconhecido(tmp_path: Path) -> None:
    h = await _remoto(tmp_path)
    assert h.state is not None
    try:
        falsificar(h)
        st = h.state
        rid = versao(h, OUTLOOK, 7)
        rt = st.devices.get("android-03")
        antes = {d["id"]: d for d in st.distribute(rid, instance_ids=["android-03"], dry_run=True)}
        assert antes["android-03"]["outcome"] == "incompatible"
        assert "renderizador desconhecido" in antes["android-03"]["reason"]
        # O app sem a chave segue indo para o mesmo aparelho, como sempre foi.
        qa = versao(h, PACOTE, 8)
        assert st.distribute(qa, instance_ids=["android-03"], dry_run=True)[0]["outcome"] != "incompatible"

        st.devices.capacidades_do_worker("worker-lan-01", [_declarado(gpu_mode="host")])
        depois = st.distribute(rid, instance_ids=["android-03"], dry_run=True)
        assert depois[0]["outcome"] != "incompatible"
        st.devices.capacidades_do_worker("worker-lan-01", [_declarado(gpu_mode="swiftshader_indirect")])
        recusado = st.distribute(rid, instance_ids=["android-03"], dry_run=True)
        assert recusado[0]["outcome"] == "incompatible" and "SwiftShader" in recusado[0]["reason"]
        assert compatibilidade.capacidades_de(rt).renderizador == "swiftshader"
    finally:
        await h.state.stop()


def _executor_do_agente(tmp_path: Path, **android: Any) -> Any:
    from app.worker.executor import WorkerExecutor

    settings = WorkerSettings(worker_id="worker-lan-01", name="Notebook", work_dir=str(tmp_path / "farm"),
                              sdk_root=str(tmp_path / "sdk-que-nao-existe"), android=android,
                              devices=[DeviceSpec(instance_id="android-09", avd_name="worker-09", console_port=5554)])
    return WorkerExecutor(settings, settings.to_config())


def test_o_agente_declara_o_pedido_e_le_o_selecionado_do_log_dele(tmp_path: Path) -> None:
    ex = _executor_do_agente(tmp_path, gpu_mode="host")
    spec = ex.settings.devices[0]
    # Fora do ar: só o pedido (o `gpu_mode` do worker.yaml), sem abrir arquivo nenhum.
    assert ex.renderizador(spec, None) == {"gpu_mode": "host", "gpu_gles": None, "gpu_vulkan": None}
    ex.cfg.logs_dir.mkdir(parents=True, exist_ok=True)
    log = ex.cfg.logs_dir / "emulator-worker-09.log"
    log.write_text(LINHA_HOST + "\n", encoding="utf-8")
    assert ex.renderizador(spec, 4321) == {"gpu_mode": "host", "gpu_gles": "host", "gpu_vulkan": "host"}
    # Mesmo processo: o log não é relido a cada batida (ele chega a megabytes, e são vários aparelhos).
    log.write_text(LINHA_SWIFT + "\n", encoding="utf-8")
    assert ex.renderizador(spec, 4321)["gpu_gles"] == "host"
    # Processo novo (o aparelho reiniciou): lê de novo.
    assert ex.renderizador(spec, 4322)["gpu_gles"] == "swiftshader"
    assert ex.renderizador(spec, None)["gpu_gles"] is None


def test_o_agente_sem_gpu_mode_no_yaml_declara_o_padrao(tmp_path: Path) -> None:
    ex = _executor_do_agente(tmp_path)
    assert ex.renderizador(ex.settings.devices[0], None)["gpu_mode"] == "swiftshader_indirect"
