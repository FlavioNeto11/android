"""Item 24.6 (R9, ADR-058): o Comando deixa de escolher "um app" — a distribuição entre servidores sai do comando.

Antes, "Distribuir entre servidores" exigia `distribute.app_id`, e o painel preenchia com o app mais comum do parque
quando a pessoa não escolhia: o comando entre apps era repartido pelos aparelhos de UM app que ninguém pediu. Agora:

- sem `app_id`, os apps são os que o comando usa, pela leitura do roteamento (`_app_do_comando`) e, para um app sem
  conta citado sozinho, pela citação;
- com vários apps, o conjunto de candidatos é a UNIÃO dos aparelhos deles (não os do primeiro citado, que dependeria
  da ordem do texto), sem o aparelho em que algum deles se sabe fora de pronto, dito app por app;
- comando que não diz app: prévia vazia com o motivo, e a criação recusa com `distribution_sem_app`;
- `GET /runs/distribution` aceita `command` no lugar de `app_id` (um dos dois), com a mesma recusa de credencial.

Nível de prova: `simulated` (harness na porta 5640, aparelhos falsos).
"""
from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.models import DistributeSpec, InstanceState, RunCreate
from app.taskqueue.service import RunError, RunService

from .conftest import Harness

NOTAS = "com.pocqa.notes"


def _notas(h: Harness, *, principal_de: tuple[str, ...] = ()) -> None:
    """Um segundo app SEM conta cadastrado ("Notas") e, opcionalmente, aparelhos que o têm como app principal."""
    assert h.state is not None
    db = h.state.db
    db.execute("INSERT INTO apps(id, name, package, activity, builtin) VALUES ('notes','Notas',?,'.Main',0)", (NOTAS,))
    for iid in principal_de:
        db.execute("UPDATE instances SET app_id='notes' WHERE id=?", (iid,))


def _ligados(h: Harness) -> None:
    assert h.state is not None
    for rt in h.state.devices.devices.values():
        rt.state = InstanceState.online


def _ids(previa: Any) -> list[str]:
    return sorted(p.instance_id for p in previa.picks)


def test_sem_app_escolhido_o_app_vem_do_comando(harness: Harness) -> None:
    """Um app sem conta citado sozinho não entra no roteamento, mas diz de quais aparelhos se trata."""
    assert harness.state is not None
    _ligados(harness)
    previa = harness.state.runs.previa_de_distribuicao(DistributeSpec(count=2), "abra o QA Messenger e mande oi")
    assert previa.missing == 0 and len(previa.picks) == 2
    assert set(_ids(previa)) <= {"android-01", "android-02", "android-03"}


def test_varios_apps_repartem_pela_uniao_e_nao_pela_ordem_do_texto(harness: Harness) -> None:
    assert harness.state is not None
    _notas(harness, principal_de=("android-03",))
    _ligados(harness)
    runs = harness.state.runs
    ida = runs.previa_de_distribuicao(DistributeSpec(count=3), "leia a nota no Notas e mande no QA Messenger")
    volta = runs.previa_de_distribuicao(DistributeSpec(count=3), "mande no QA Messenger o que diz a nota do Notas")
    # O aparelho do Notas e os do QA Messenger, nas duas ordens: antes, só os do primeiro app (ou do mais comum).
    assert _ids(ida) == _ids(volta) == ["android-01", "android-02", "android-03"]
    # Com o app escolhido pela pessoa, restringe a ele, como sempre.
    so_notas = runs.previa_de_distribuicao(DistributeSpec(count=3, app_id="notes"),
                                           "leia a nota no Notas e mande no QA Messenger")
    assert _ids(so_notas) == ["android-03"]


def test_app_do_conjunto_fora_de_pronto_tira_o_aparelho_e_diz_qual(harness: Harness) -> None:
    assert harness.state is not None
    _notas(harness, principal_de=("android-03",))
    _ligados(harness)
    harness.state.db.execute("INSERT INTO device_app_state(instance_id, package_name, state) VALUES (?,?,?)",
                             ("android-02", NOTAS, "missing"))
    previa = harness.state.runs.previa_de_distribuicao(DistributeSpec(count=3),
                                                       "leia a nota no Notas e mande no QA Messenger")
    assert _ids(previa) == ["android-01", "android-03"] and previa.missing == 1
    assert "1 aparelho(s) com o app Notas fora de pronto" in previa.reasons
    # A frase do balanceamento diz os apps do comando, não "deste app".
    assert not any("deste app" in m or "este app" in m for m in previa.reasons), previa.reasons


def test_comando_sem_app_nao_distribui_e_diz_o_que_fazer(harness: Harness) -> None:
    assert harness.state is not None
    _ligados(harness)
    previa = harness.state.runs.previa_de_distribuicao(DistributeSpec(count=2), "faça algo útil hoje")
    assert previa.picks == [] and previa.missing == 2
    assert previa.reasons == [RunService.SEM_APP_NA_DISTRIBUICAO]
    with pytest.raises(RunError) as exc:
        harness.state.runs.create(RunCreate(command="faça algo útil hoje", idempotency_key="dist-sem-app-1",
                                            distribute=DistributeSpec(count=2)))
    assert exc.value.code == "distribution_sem_app" and exc.value.status == 409


async def test_criar_distribuida_pelo_comando_sem_app_id(harness: Harness) -> None:
    assert harness.state is not None
    _ligados(harness)
    run = harness.state.runs.create(RunCreate(command="abra o QA Messenger e mande oi",
                                              idempotency_key="dist-pelo-comando-1",
                                              distribute=DistributeSpec(count=2)))
    assert run.instances_requested == 2


async def test_rota_da_previa_aceita_o_comando_no_lugar_do_app(harness: Harness) -> None:
    from app.main import create_app

    assert harness.state is not None
    _ligados(harness)
    app = create_app(harness.cfg, state=harness.state)
    app.state.poc = harness.state
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/runs/distribution", params={"count": 2, "command": "abra o QA Messenger e mande oi"})
        assert r.status_code == 200, r.text
        assert len(r.json()["picks"]) == 2
        # Nem app nem comando: não há de quem distribuir.
        r = await c.get("/api/runs/distribution", params={"count": 2})
        assert r.status_code == 422 and r.json()["detail"]["code"] == "distribution_sem_alvo"
        # Senha no texto não passa nem pela prévia (ADR-025/040).
        r = await c.get("/api/runs/distribution", params={"count": 2, "command": "abra o QA Messenger senha: x1y2z3"})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "credencial_no_comando"
        # O app escolhido segue valendo sozinho, como antes.
        r = await c.get("/api/runs/distribution", params={"count": 1, "app_id": "qa-messenger"})
        assert r.status_code == 200 and len(r.json()["picks"]) == 1
