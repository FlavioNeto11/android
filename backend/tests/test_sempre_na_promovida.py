"""ADR-026 — decisão do dono de 26/09/2026: "todos devem ficar atualizados sempre".

Tudo aqui é `simulated`: aparelhos do harness (porta base 5640) com o adb trocado pelo falso da loja de apps
(`AdbPorPacote`) — nenhum APK é instalado num emulador de verdade. O que estes testes protegem:

* promover uma versão atualiza sozinho TODO aparelho que tem o app, principal ou secundário: o ligado e livre já, o
  ocupado quando fica livre, o desligado quando liga — e ninguém é ligado por isso;
* o aparelho que liga adota a promovida de cada app que tem, não só a do app principal;
* quem não tem o app não o recebe (espalhar continua sendo "Distribuir");
* com duas promovidas de mesmo número, a escolha não depende da ordem em que o banco devolve as linhas;
* voltar a versão num aparelho leva o parque de volta à promovida anterior, com `-d` (preservando os dados), e o
  desejo que apontava para a versão voltada deixa de bloquear o aparelho;
* a quarentena para de espalhar a versão, mas não rebaixa quem já está nela (isso é a volta);
* o aparelho em prova de canário não é rebaixado, e a entrega que falhou não se repete a cada passada da varredura;
* o app secundário não fica na frente depois da prova de abertura (HOME + `force-stop`), e o "Abrir app" volta à tela
  inicial quando outro app está na frente (critério 8, medido no android-01 em 26/09).
"""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
import pytest_asyncio

from app.models import InstanceState, ReleaseChannel
from app.vitrine import convergir_ligados, vitrine

from .conftest import Harness
from .test_app_releases import LAUNCHER, QA_APK, StubInspector, part
from .test_distribute import PACOTE
from .test_loja_de_apps import OUTLOOK, estado, falsificar, ligar, pronto, versao

LOJA = "android-05"


@pytest_asyncio.fixture
async def parque(tmp_path: Path) -> AsyncIterator[Harness]:
    """android-01..04 são de tarefa (o app principal de todos é o de QA); android-05 é a loja."""
    h = Harness(tmp_path, 5, store=LOJA)
    await h.boot()
    try:
        yield h
    finally:
        if h.state is not None:
            await h.state.stop()


def em_prova(h: Harness, rid: str, onde: str = "android-01") -> None:
    """Leva a versão ao ponto de ser promovida pela ROTA: canário com prova de instalar e abrir registrada."""
    st = h.state
    assert st is not None
    st.release_repo.set_channel(rid, ReleaseChannel.canary, canary_instance_id=onde)
    st.release_repo.record_validation(rid, onde, stage="install", ok=True, detail=None)
    st.release_repo.record_validation(rid, onde, stage="launch", ok=True, detail="abriu")


def cliente(h: Harness) -> httpx.AsyncClient:
    from app.main import create_app

    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def promover(c: httpx.AsyncClient, rid: str) -> dict[str, Any]:
    r = await c.post(f"/api/releases/{rid}/lifecycle", json={"verb": "promote"})
    assert r.status_code == 200, r.text
    return r.json()


def so_app_de_outro(h: Harness, *ids: str) -> None:
    """Estes aparelhos passam a operar o Instagram (sem versão no catálogo): o app de QA deixa de ser o principal
    deles, e eles só o teriam se alguém o distribuísse."""
    for iid in ids:
        h.state.db.execute("UPDATE instances SET app_id='instagram' WHERE id=?", (iid,))  # type: ignore[union-attr]


def instalacoes(falso: Any) -> int:
    return sum(1 for c in falso.calls if c.startswith("install"))


async def livre(h: Harness, iid: str) -> None:
    await h.wait(lambda: iid not in h.state.scheduler.workers, what=f"{iid} livre")  # type: ignore[union-attr]


# ==================================================================== 1. promover atualiza todos que têm o app
async def test_promover_atualiza_sozinho_quem_tem_o_app_secundario_e_nao_espalha(parque: Harness) -> None:
    """Antes, promover só mudava o banco: nenhum aparelho recebia a versão nova sem um "Distribuir" de novo."""
    falsos = falsificar(parque)
    st = parque.state
    assert st is not None
    devs = st.devices
    v3 = versao(parque, OUTLOOK, 3)
    st.distribute(v3, instance_ids=["android-01", "android-02", "android-03"])
    for iid in ("android-01", "android-02", "android-03"):
        await pronto(parque, iid, v3, OUTLOOK)
    rt3 = devs.get("android-03")
    await devs.stop_instance(rt3)
    ocupado = asyncio.get_running_loop().create_future()             # android-02 está com outro trabalho
    st.scheduler.workers["android-02"] = ocupado                      # type: ignore[assignment]

    v4 = versao(parque, OUTLOOK, 4, promovida=False)
    em_prova(parque, v4)
    async with cliente(parque) as c:
        r = await promover(c, v4)
    assert r["target_release_id"] == v4 and r["release"]["channel"] == "promoted"
    saida = {d["id"]: d for d in r["devices"]}
    assert set(saida) == {"android-01", "android-02", "android-03"}  # android-04 não tem o Outlook: nem aparece
    assert saida["android-01"]["outcome"] == "started"
    assert saida["android-02"]["outcome"] == "pending" and "ocupado" in saida["android-02"]["reason"]
    assert saida["android-03"]["outcome"] == "pending" and "quando ligar" in saida["android-03"]["reason"]
    assert rt3.state != InstanceState.online                          # promover não liga ninguém

    await pronto(parque, "android-01", v4, OUTLOOK)
    assert falsos["android-01"].apps[OUTLOOK]["version_code"] == 4

    st.scheduler.workers.pop("android-02")                           # ficou livre: a varredura entrega
    ocupado.cancel()
    assert convergir_ligados(st) == ["android-02"]
    await pronto(parque, "android-02", v4, OUTLOOK)

    await ligar(parque, rt3)                                         # desligado: recebe quando liga
    await pronto(parque, "android-03", v4, OUTLOOK)

    assert estado(parque, "android-04", OUTLOOK) is None             # quem não tem o app não o recebe
    assert OUTLOOK not in falsos["android-04"].apps
    assert convergir_ligados(st) == []                               # nada mais pendente


async def test_promover_atualiza_o_app_principal_de_quem_esta_ligado_e_livre(parque: Harness) -> None:
    """O app principal só era entregue pela porta do app, antes da PRÓXIMA tarefa: um aparelho ligado e sem trabalho
    ficava na versão antiga até alguém mandar tarefa para ele."""
    falsos = falsificar(parque)
    st = parque.state
    assert st is not None
    so_app_de_outro(parque, "android-03")
    v7 = versao(parque, PACOTE, 7)
    st.distribute(v7, instance_ids=["android-01", "android-02"])
    await pronto(parque, "android-01", v7)
    await pronto(parque, "android-02", v7)

    v8 = versao(parque, PACOTE, 8, promovida=False)
    em_prova(parque, v8)
    async with cliente(parque) as c:
        r = await promover(c, v8)
    saida = {d["id"]: d["outcome"] for d in r["devices"]}
    # O app principal do aparelho conta como "tem" mesmo sem linha (a regra que já existia, android-12..15): o
    # android-04 opera o app de QA e o recebe. O android-03 opera outro app e nunca teve este: fica de fora.
    assert saida == {"android-01": "started", "android-02": "started", "android-04": "started"}
    for iid in ("android-01", "android-02", "android-04"):
        await pronto(parque, iid, v8)
    assert estado(parque, "android-03") is None and falsos["android-03"].calls == []


# ==================================================================== 2. quem liga adota a promovida de todos os apps
async def test_aparelho_que_liga_adota_a_promovida_de_cada_app_que_tem(parque: Harness) -> None:
    """Promovida pelo serviço, sem a rota: o único caminho até o aparelho desligado é o "entrou no ar". Antes, só o
    app principal era adotado ali; o Outlook ficava na versão que tinha, com desejada == instalada."""
    falsos = falsificar(parque)
    st = parque.state
    assert st is not None
    devs = st.devices
    v3 = versao(parque, OUTLOOK, 3)
    st.distribute(v3, instance_ids=["android-02"])
    await pronto(parque, "android-02", v3, OUTLOOK)
    rt2 = devs.get("android-02")
    await devs.stop_instance(rt2)

    v4 = versao(parque, OUTLOOK, 4)
    assert estado(parque, "android-02", OUTLOOK)["desired_release_id"] == v3
    await ligar(parque, rt2)
    await pronto(parque, "android-02", v4, OUTLOOK)
    assert falsos["android-02"].apps[OUTLOOK]["version_code"] == 4

    rt3 = devs.get("android-03")                                     # nunca teve o Outlook
    await devs.stop_instance(rt3)
    await ligar(parque, rt3)
    await livre(parque, "android-03")
    assert estado(parque, "android-03", OUTLOOK) is None and OUTLOOK not in falsos["android-03"].apps


# ==================================================================== 5. escolha estável entre promovidas empatadas
def versao_com_sal(h: Harness, pacote: str, codigo: int, sal: bytes) -> str:
    """Como `versao`, mas com bytes próprios: duas versões de MESMO número e conteúdo diferente (dois builds)."""
    st = h.state
    assert st is not None
    st.releases.inspector = StubInspector(                           # type: ignore[assignment]
        {"base.apk": part(package_name=pacote, version_code=codigo, version_name=f"{codigo}.0.0", abis=[])})
    inbox = st.cfg.apk_inbox
    inbox.mkdir(parents=True, exist_ok=True)
    shutil.copy2(QA_APK, inbox / "base.apk")
    with open(inbox / "base.apk", "ab") as fh:
        fh.write(pacote.encode() + sal)
    saida = st.releases.import_inbox()[0]
    assert saida.ok, saida.reason
    rid = str(saida.release_id)
    st.releases.approve_signature(rid)
    em_prova(h, rid)
    st.releases.promote(rid)
    return rid


async def test_duas_promovidas_de_mesmo_numero_tem_escolha_estavel(parque: Harness, monkeypatch) -> None:
    """A produção tem `com.pocqa.messenger-1-0a769110a5f2` e `-1-a796939db14c`, ambas 1.0.0/1 e promovidas. Com
    `ORDER BY version_code` só, a ordem do empate era a do banco — no SQLite, a de inserção; no PostgreSQL, qualquer
    uma (K-030). A imitação abaixo devolve as linhas de `app_releases` na ordem inversa, como outro banco poderia."""
    falsos = falsificar(parque)
    st = parque.state
    assert st is not None
    a = versao_com_sal(parque, PACOTE, 1, b"build-a")
    b = versao_com_sal(parque, PACOTE, 1, b"build-b")
    assert a != b
    st.db.execute("UPDATE app_releases SET channel_at=? WHERE id=?", ("2026-09-20T10:00:00.000Z", a))
    st.db.execute("UPDATE app_releases SET channel_at=? WHERE id=?", ("2026-09-21T10:00:00.000Z", b))

    consulta_real = st.db.query
    inverter = [False]

    def consulta(sql: str, params: Any = ()) -> Any:
        linhas = consulta_real(sql, params)
        return list(reversed(linhas)) if inverter[0] and "FROM app_releases" in sql else linhas

    monkeypatch.setattr(st.db, "query", consulta)

    def escolhas() -> set[str]:
        vistas = set()
        for inv in (False, True):
            inverter[0] = inv
            vistas.add(st.releases.promoted_release(PACOTE).id)      # type: ignore[union-attr]
        inverter[0] = False
        return vistas

    assert escolhas() == {b}                                         # a promovida por último ganha o empate
    st.db.execute("UPDATE app_releases SET channel_at=? WHERE id IN (?,?)", ("2026-09-21T10:00:00.000Z", a, b))
    assert escolhas() == {max(a, b)}                                 # mesmo instante: desempate pelo id
    alvo = max(a, b)
    outra = min(a, b)
    qa = next(x for x in vitrine(st) if x["package"] == PACOTE)      # o cartão mostra a mesma que o parque persegue
    assert qa["promoted"]["id"] == alvo

    # Quem está na OUTRA promovida de mesmo número não é reinstalado: "atualizado" é pelo número.
    so_app_de_outro(parque, "android-02", "android-03", "android-04")
    st.distribute(outra, instance_ids=["android-01"])
    await pronto(parque, "android-01", outra)
    antes = instalacoes(falsos["android-01"])
    for _ in range(3):
        convergir_ligados(st)
        await livre(parque, "android-01")
    assert instalacoes(falsos["android-01"]) == antes
    assert estado(parque, "android-01")["installed_release_id"] == outra


# ==================================================================== 6. voltar leva o parque de volta
async def test_voltar_um_aparelho_leva_o_parque_de_volta_a_promovida_anterior(parque: Harness) -> None:
    """Antes, a volta marcava a 4.0 como substituída e parava aí: os outros aparelhos ficavam nela (desejada ==
    instalada), e quem esperava a 4.0 tinha a tarefa bloqueada com "não pode mais ser entregue"."""
    falsos = falsificar(parque)
    st = parque.state
    assert st is not None
    devs = st.devices
    todos = ["android-01", "android-02", "android-03", "android-04"]
    o3 = versao(parque, OUTLOOK, 3)
    st.distribute(o3, instance_ids=todos)
    for iid in todos:
        await pronto(parque, iid, o3, OUTLOOK)
    rt4 = devs.get("android-04")
    await devs.stop_instance(rt4)                                    # desligado desde antes da 4.0: fica esperando-a

    o4 = versao(parque, OUTLOOK, 4, promovida=False)
    em_prova(parque, o4)
    async with cliente(parque) as c:
        r = await promover(c, o4)
        assert {d["id"]: d["outcome"] for d in r["devices"]}["android-04"] == "pending"
        for iid in ("android-01", "android-02", "android-03"):
            await pronto(parque, iid, o4, OUTLOOK)
        assert estado(parque, "android-04", OUTLOOK)["desired_release_id"] == o4
        rt3 = devs.get("android-03")
        await devs.stop_instance(rt3)                                # desligado já na 4.0

        r = await c.post(f"/api/releases/{o4}/lifecycle", json={"verb": "rollback", "instance_id": "android-01"})
        assert r.status_code in (200, 202), r.text
        await pronto(parque, "android-01", o3, OUTLOOK)
    # `ready` é gravado DENTRO de `install_on`; marcar a 4.0 como substituída e convergir vêm depois, no mesmo
    # trabalho. Esperar, não afirmar na hora — sob a latência do PostgreSQL do CI a ordem não é garantida.
    await parque.wait(lambda: st.release_repo.release_row(o4)["channel"] == ReleaseChannel.rolled_back.value,
                      what="4.0 substituída")
    await parque.wait(lambda: st.releases.promoted_release(OUTLOOK).id == o3,  # type: ignore[union-attr]
                      what="3.0 volta a ser a promovida")

    await pronto(parque, "android-02", o3, OUTLOOK)                  # ligado: volta já, sem esperar a varredura
    assert "install -d" in falsos["android-02"].calls                # rebaixar é com -d: preserva os dados
    assert falsos["android-02"].apps[OUTLOOK]["version_code"] == 3

    await ligar(parque, rt3)                                         # desligado na 4.0: volta quando liga
    await pronto(parque, "android-03", o3, OUTLOOK)

    antes = instalacoes(falsos["android-04"])
    await ligar(parque, rt4)                                         # esperava a 4.0 e já está na 3.0: nada a fazer
    await livre(parque, "android-04")
    linha = estado(parque, "android-04", OUTLOOK)
    assert linha["installed_release_id"] == o3 and linha["desired_release_id"] == o3
    assert instalacoes(falsos["android-04"]) == antes


async def test_voltar_o_app_principal_leva_os_outros_de_volta_e_recusa_de_rebaixar_nao_se_repete(
        parque: Harness) -> None:
    falsos = falsificar(parque)
    st = parque.state
    assert st is not None
    so_app_de_outro(parque, "android-04")
    q7 = versao(parque, PACOTE, 7)
    st.distribute(q7, instance_ids=["android-01", "android-02", "android-03"])
    for iid in ("android-01", "android-02", "android-03"):
        await pronto(parque, iid, q7)
    q8 = versao(parque, PACOTE, 8, promovida=False)
    em_prova(parque, q8)
    falsos["android-03"].refuse_downgrade = True                     # build que recusa voltar mesmo com -d
    async with cliente(parque) as c:
        await promover(c, q8)
        for iid in ("android-01", "android-02", "android-03"):
            await pronto(parque, iid, q8)
        r = await c.post(f"/api/releases/{q8}/lifecycle", json={"verb": "rollback", "instance_id": "android-01"})
        assert r.status_code in (200, 202), r.text
    await pronto(parque, "android-01", q7)
    await pronto(parque, "android-02", q7)
    assert "install -d" in falsos["android-02"].calls

    def recusado() -> bool:
        linha = estado(parque, "android-03")
        return linha["state"] == "install_failed" and linha["drift_kind"] == "downgrade_refused"

    await parque.wait(recusado, what="android-03 recusou voltar")
    await livre(parque, "android-03")
    tentativas = instalacoes(falsos["android-03"])
    # Nem a nova tentativa diária: a saída da recusa apaga os dados do app, e quem decide é uma pessoa.
    st.db.execute("UPDATE commands SET created_at='2026-09-01T00:00:00.000Z'")
    st.db.execute("UPDATE app_release_validations SET observed_at='2026-09-01T00:00:00.000Z'")
    for _ in range(2):
        convergir_ligados(st)
        await livre(parque, "android-03")
    assert instalacoes(falsos["android-03"]) == tentativas
    assert falsos["android-03"].apps[PACOTE]["version_code"] == 8     # nada foi apagado


async def test_quarentena_para_de_espalhar_mas_nao_rebaixa_quem_esta_na_versao(parque: Harness) -> None:
    """A quarentena não é a volta: o painel promete "nenhum aparelho muda sozinho: quem já está nela continua até
    você pedir a volta". Quem ainda ESPERAVA a versão deixa de esperá-la (senão a porta do app bloquearia a tarefa
    com "não pode mais ser entregue")."""
    falsos = falsificar(parque)
    st = parque.state
    assert st is not None
    devs = st.devices
    o3 = versao(parque, OUTLOOK, 3)
    st.distribute(o3, instance_ids=["android-01", "android-02"])
    await pronto(parque, "android-01", o3, OUTLOOK)
    await pronto(parque, "android-02", o3, OUTLOOK)
    rt2 = devs.get("android-02")
    await devs.stop_instance(rt2)
    o4 = versao(parque, OUTLOOK, 4, promovida=False)
    em_prova(parque, o4)
    async with cliente(parque) as c:
        await promover(c, o4)
        await pronto(parque, "android-01", o4, OUTLOOK)
        r = await c.post(f"/api/releases/{o4}/lifecycle", json={"verb": "quarantine", "note": "trava no feed"})
        assert r.status_code == 200, r.text
    antes = instalacoes(falsos["android-01"])
    convergir_ligados(st)
    await livre(parque, "android-01")
    assert instalacoes(falsos["android-01"]) == antes                 # continua na 4.0 até alguém pedir a volta
    assert estado(parque, "android-01", OUTLOOK)["installed_release_id"] == o4

    await ligar(parque, rt2)                                         # esperava a 4.0: fica na 3.0, sem bloqueio
    await livre(parque, "android-02")
    linha = estado(parque, "android-02", OUTLOOK)
    assert linha["installed_release_id"] == o3 and linha["desired_release_id"] == o3
    assert falsos["android-02"].apps[OUTLOOK]["version_code"] == 3


# ==================================================================== travas de sempre
async def test_aparelho_em_prova_de_canario_nao_e_rebaixado_pela_varredura(parque: Harness) -> None:
    falsos = falsificar(parque)
    st = parque.state
    assert st is not None
    so_app_de_outro(parque, "android-02", "android-03", "android-04")
    v7 = versao(parque, PACOTE, 7)
    st.distribute(v7, instance_ids=["android-01"])
    await pronto(parque, "android-01", v7)
    v8 = versao(parque, PACOTE, 8, promovida=False)
    await st.releases.start_canary(st.devices.get("android-01"), v8, st.installer)
    assert estado(parque, "android-01")["installed_release_id"] == v8
    antes = instalacoes(falsos["android-01"])
    for _ in range(2):
        convergir_ligados(st)
        await livre(parque, "android-01")
    assert instalacoes(falsos["android-01"]) == antes                 # a 7 promovida não desfaz a prova da 8
    assert estado(parque, "android-01")["installed_release_id"] == v8


async def test_entrega_que_falhou_na_varredura_nao_se_repete_a_cada_passada(parque: Harness) -> None:
    """A entrega da varredura não abre comando. Com o relógio da nova tentativa diária contado só em `commands`, um
    comando de app de dias atrás rearmaria a entrega a cada passada (60 s): o retry cego que o projeto proíbe."""
    falsos = falsificar(parque)
    st = parque.state
    assert st is not None
    o3 = versao(parque, OUTLOOK, 3)
    st.distribute(o3, instance_ids=["android-02"])
    await pronto(parque, "android-02", o3, OUTLOOK)
    st.db.execute("UPDATE commands SET created_at='2026-09-01T00:00:00.000Z'")   # o último comando é antigo
    falsos["android-02"].install_error = "INSTALL_FAILED_INSUFFICIENT_STORAGE"
    o4 = versao(parque, OUTLOOK, 4)
    assert "android-02" in convergir_ligados(st)

    def falhou() -> bool:
        return estado(parque, "android-02", OUTLOOK)["state"] == "install_failed"

    await parque.wait(falhou, what="entrega do Outlook 4 falhou")
    await livre(parque, "android-02")
    tentativas = instalacoes(falsos["android-02"])
    for _ in range(3):
        convergir_ligados(st)
        await livre(parque, "android-02")
    assert instalacoes(falsos["android-02"]) == tentativas

    # Um dia depois da última tentativa, UMA nova — e o motivo já passou.
    st.db.execute("UPDATE app_release_validations SET observed_at='2026-09-01T00:00:00.000Z'")
    falsos["android-02"].install_error = None
    assert "android-02" in convergir_ligados(st)
    await pronto(parque, "android-02", o4, OUTLOOK)


# ==================================================================== 8. o secundário não fica na frente
def gravar_tela(falso: Any) -> tuple[list[tuple[str, str]], set[str]]:
    """O dublê passa a registrar o que muda a tela: HOME, `force-stop` e abertura, e quais processos estão de pé."""
    registro: list[tuple[str, str]] = []
    rodando: set[str] = set()
    abrir = falso.start_app

    def start_app(package: str, activity: Any = None) -> None:
        registro.append(("start_app", package))
        rodando.add(package)
        abrir(package, activity)

    def keyevent(key: str) -> None:
        registro.append(("keyevent", key))
        if key == "home":
            falso.focus = LAUNCHER

    def force_stop(package: str) -> None:
        registro.append(("force_stop", package))
        rodando.discard(package)
        if falso.focus[0] == package:
            falso.focus = LAUNCHER

    falso.start_app, falso.keyevent, falso.force_stop = start_app, keyevent, force_stop
    return registro, rodando


async def test_prova_de_abertura_de_app_secundario_devolve_a_tela_inicial_e_para_o_processo(parque: Harness) -> None:
    """Medido na produção (26/09): o app de QA distribuído ao android-01, aparelho de conta Instagram, ficou na frente
    depois da prova de abertura, e dois "Abrir app" do Instagram terminaram `uncertain`. A prova continua valendo; o
    aparelho é que volta à tela inicial, com o app conferido parado."""
    falsos = falsificar(parque)
    st = parque.state
    assert st is not None
    registro, rodando = gravar_tela(falsos["android-01"])
    o3 = versao(parque, OUTLOOK, 3)
    st.distribute(o3, instance_ids=["android-01"])
    await pronto(parque, "android-01", o3, OUTLOOK)                   # a prova de abertura valeu: `ready`
    await livre(parque, "android-01")
    assert ("start_app", OUTLOOK) in registro                         # abriu para a prova
    depois = registro[registro.index(("start_app", OUTLOOK)):]
    assert [e for e in depois if e[0] != "start_app"] == [("keyevent", "home"), ("force_stop", OUTLOOK)]
    assert falsos["android-01"].focus[0] != OUTLOOK and OUTLOOK not in rodando


async def test_prova_de_abertura_do_app_principal_continua_deixando_o_app_na_frente(parque: Harness) -> None:
    falsos = falsificar(parque)
    st = parque.state
    assert st is not None
    registro, rodando = gravar_tela(falsos["android-01"])
    v7 = versao(parque, PACOTE, 7)
    st.distribute(v7, instance_ids=["android-01"])
    await pronto(parque, "android-01", v7)
    await livre(parque, "android-01")
    assert ("start_app", PACOTE) in registro
    assert not [e for e in registro if e[0] in ("keyevent", "force_stop")]
    assert falsos["android-01"].focus[0] == PACOTE and PACOTE in rodando


async def test_abrir_app_com_outro_app_na_frente_volta_a_tela_inicial_antes(parque: Harness) -> None:
    falsos = falsificar(parque)
    st = parque.state
    assert st is not None
    rt = st.devices.get("android-01")
    registro, _ = gravar_tela(falsos["android-01"])
    instagram = st.db.one("SELECT * FROM apps WHERE id='instagram'")
    falsos["android-01"].focus = (PACOTE, ".MainActivity")           # o secundário que ficou aberto
    ok, _detalhe = await st.devices.open_app(rt, instagram)
    assert ok
    assert registro == [("keyevent", "home"), ("start_app", "com.instagram.android")]

    registro.clear()                                                 # já na frente: nada de HOME
    ok, _detalhe = await st.devices.open_app(rt, instagram)
    assert ok and registro == [("start_app", "com.instagram.android")]


async def test_promocao_que_valeu_nao_vira_500_se_a_convergencia_imediata_falhar(parque: Harness, monkeypatch) -> None:
    """A promoção já foi gravada quando a convergência roda. Um 500 aqui faria quem chamou repetir e levar 409 ("só
    promove quem está em canário"); a varredura e a entrada no ar entregam do mesmo jeito."""
    import app.api as api_mod                   # a rota busca `convergir_o_parque` no módulo dela

    falsificar(parque)
    v4 = versao(parque, OUTLOOK, 4, promovida=False)
    em_prova(parque, v4)

    def quebrada(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("banco indisponível")

    monkeypatch.setattr(api_mod, "convergir_o_parque", quebrada)
    async with cliente(parque) as c:
        r = await promover(c, v4)
    assert r["release"]["channel"] == "promoted" and r["devices"] == []
    assert "banco indisponível" in r["convergence_error"]


# ==================================================================== revisão do PR #13
async def test_versao_voltada_com_prova_de_abertura_falha_tambem_volta(parque: Harness) -> None:
    """Revisão do PR #13: a instalação da 4.0 chegou ao aparelho, mas a prova de abertura falhou — `install_on`
    guarda o número observado (4) e a desejada, e limpa `installed_release_id`. Olhando só a instalada, o aparelho
    parecia ter "uma versão mais nova instalada por fora" e nunca saía da 4.0 voltada; e a trava da tentativa
    diária seguraria o alvo novo como se fosse repetir a mesma entrega."""
    falsificar(parque)
    st = parque.state
    assert st is not None
    o3 = versao(parque, OUTLOOK, 3)
    st.distribute(o3, instance_ids=["android-02"])
    await pronto(parque, "android-02", o3, OUTLOOK)
    o4 = versao(parque, OUTLOOK, 4)
    await livre(parque, "android-02")
    st.db.execute("UPDATE device_app_state SET installed_release_id=NULL, observed_version_code=4, desired_release_id=?,"
                  " state='verify_failed', pending_op=NULL, drift_kind=NULL WHERE instance_id='android-02'"
                  " AND package_name=?", (o4, OUTLOOK))
    st.release_repo.set_channel(o4, ReleaseChannel.rolled_back)       # a 4.0 foi voltada no parque
    assert st.releases.promoted_release(OUTLOOK).id == o3             # type: ignore[union-attr]
    rt = st.devices.get("android-02")

    assert st.aplicar_versao_promovida(rt, OUTLOOK) == o3
    linha = estado(parque, "android-02", OUTLOOK)
    assert linha["desired_release_id"] == o3 and linha["state"] == "installed"
    assert st._rebaixa_do_parque("android-02", OUTLOOK, o3), "voltar da 4.0 para a 3.0 vai com -d"


async def test_objetivo_incerto_segura_a_troca_do_app_principal(parque: Harness) -> None:
    """Revisão do PR #13: objetivo `uncertain` soltou o trabalhador e não é despachável, mas a tela dele é a evidência
    que o operador precisa ver para decidir se o efeito aconteceu. A troca automática do app principal espera."""
    from app.util import now_iso
    from app.vitrine import objetivo_em_andamento

    st = parque.state
    assert st is not None
    assert not objetivo_em_andamento(st, "android-01")
    st.db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, instance_ids, created_at)"
                  " VALUES ('r-teste-incerto','k-teste-incerto','abrir','execute','completed_with_issues','[\"android-01\"]',?)",
                  (now_iso(),))
    st.db.execute("INSERT INTO objectives(id, run_id, instance_id, status) VALUES ('o-teste-incerto','r-teste-incerto',"
                  "'android-01','uncertain')")
    assert objetivo_em_andamento(st, "android-01")


async def test_relogio_da_tentativa_diaria_conta_so_este_app_e_so_o_que_saiu(parque: Harness) -> None:
    """Revisão do PR #13: o máximo de TODO `app.*` do aparelho fazia a atividade do app principal (ou um comando
    recusado antes de tocar no aparelho) adiar para sempre a nova tentativa diária de um secundário."""
    from app.util import now_iso

    st = parque.state
    assert st is not None
    antigo = "2026-09-01T00:00:00.000Z"

    def comando(cid: str, pacote: str, estado_: str, quando: str) -> None:
        st.db.execute("INSERT INTO commands(id, instance_id, verb, params, idempotency_key, state, requested_by,"
                      " created_at) VALUES (?,?,?,?,?,?,?,?)",
                      (cid, "android-02", "app.distribute", f'{{"release_id":"{pacote}-9-x","package":"{pacote}"}}',
                       cid, estado_, "teste", quando))

    comando("c-outlook-antigo", OUTLOOK, "failed", antigo)
    comando("c-principal-agora", PACOTE, "succeeded", now_iso())     # o app principal trabalhou agora
    comando("c-outlook-recusado", OUTLOOK, "rejected", now_iso())    # recusado: não chegou ao aparelho
    quando = st._ultima_tentativa_de_entrega("android-02", OUTLOOK)
    assert quando is not None and quando.isoformat().startswith("2026-09-01"), quando
