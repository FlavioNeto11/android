"""Item 29.66: `abis`, `supported_verbs` e `renderer` alternavam entre dois valores a cada reinício do backend.

Medido no central em 04/10 (só leitura): 1108 de 1114 trocas nos aparelhos do worker caíam em horas de reinício do
backend ou de reconexão do worker. Duas fontes discordavam:
- `abis`: o ADB lê `ro.product.cpu.abilist` (x86_64 e a tradução arm64-v8a), o worker declara o `abi.type` do AVD
  (só x86_64). Agora o aparelho vence: a declaração só preenche o que ninguém leu.
- verbos e renderizador: no seed, antes do `hello`, o aparelho do worker aparece como "externo sem worker". Nessa
  janela os dois campos não contam como fato novo; o painel recebe o DTO real como `instance.progress`.

Nível de prova: `simulated` (harness na porta 5640, aparelho falso).
"""
from __future__ import annotations

from typing import Any

from .conftest import Harness


def _gravados_desde(state: Any, desde: int, instance_id: str) -> list[str]:
    return [r["kind"] for r in state.db.query("SELECT kind FROM events WHERE id > ? AND instance_id = ? ORDER BY id",
                                              (desde, instance_id))]


def _ultimo_id(state: Any) -> int:
    return int(state.db.query("SELECT COALESCE(MAX(id), 0) AS m FROM events")[0]["m"])


async def test_abis_do_aparelho_vencem_a_declaracao(harness: Harness) -> None:
    s = harness.state
    assert s is not None
    devs = s.devices
    rt = devs.get("android-01")
    rt.abis = []
    assert devs.registrar_capacidades(rt, {"abis": ["x86_64"]}, fonte="declaração do worker w", publicar=False)
    assert rt.abis == ["x86_64"]                                       # ninguém tinha lido: a declaração preenche
    assert devs.registrar_capacidades(rt, {"abis": ["x86_64", "arm64-v8a"]}, fonte="o próprio aparelho (adb)",
                                      publicar=False, observado=True)
    assert not devs.registrar_capacidades(rt, {"abis": ["x86_64"]}, fonte="declaração do worker w", publicar=False)
    assert rt.abis == ["x86_64", "arm64-v8a"]                          # a declaração não desfaz a leitura
    linha = s.db.query("SELECT abis FROM instances WHERE id=?", (rt.id,))[0]["abis"]
    assert "arm64-v8a" in linha


async def test_janela_antes_do_hello_compara_verbos_e_renderer_com_o_gravado(harness: Harness) -> None:
    """Na janela (worker do aparelho conhecido e desconectado), verbos e renderizador de "sem worker" valem o último
    DTO gravado na comparação. O `stream` que diz "worker desconectado" é fato e continua contando."""
    s = harness.state
    assert s is not None
    devs = s.devices
    rt = devs.get("android-01")
    antes = (rt.worker_id, rt.worker_verbs)
    try:
        rt.worker_id, rt.worker_verbs = "worker-x", ["start"]          # worker conectado, declarando o ciclo
        devs.publish(rt, level="warn")                                  # referência gravada
        gravado = dict(rt.dto_persistido or {})
        rt.worker_verbs = None                                          # o worker caiu: verbos de "sem worker"
        real = devs.dto(rt).model_dump(mode="json")
        assert real["supported_verbs"] != gravado["supported_verbs"]    # o painel vê o que vale agora
        comparado = devs._dto_para_comparar(rt, real)
        assert comparado["supported_verbs"] == gravado["supported_verbs"]
        assert comparado["renderer"] == gravado["renderer"]
        # O hello devolve os de sempre: nada de troca nova no log por causa dos verbos.
        rt.worker_verbs = ["start"]
        desde = _ultimo_id(s)
        devs.publish(rt)
        assert _gravados_desde(s, desde, rt.id) == []
        # Fora da janela, verbo que muda de verdade continua sendo fato.
        rt.worker_verbs = ["start", "stop"]
        devs.publish(rt)
        assert _gravados_desde(s, desde, rt.id) == ["instance.updated"]
    finally:
        rt.worker_id, rt.worker_verbs = antes


async def test_reinicio_do_backend_le_o_ultimo_dto_gravado(harness: Harness) -> None:
    """O caso que mais pesava: o processo novo não tem referência em memória; ela vem do último `instance.updated`."""
    s = harness.state
    assert s is not None
    devs = s.devices
    rt = devs.get("android-01")
    antes = (rt.worker_id, rt.worker_verbs)
    try:
        rt.worker_id, rt.worker_verbs = "worker-x", ["start"]
        devs.publish(rt, level="warn")
        gravado = dict(rt.dto_persistido or {})
        # Processo novo: nada em memória, o worker ainda não falou.
        rt.dto_publicado = rt.controle_anunciado = rt.dto_persistido = None
        rt.dto_persistido_lido = False
        rt.worker_verbs = None
        comparado = devs._dto_para_comparar(rt, devs.dto(rt).model_dump(mode="json"))
        assert comparado["supported_verbs"] == gravado["supported_verbs"]     # lido do log, não da memória
        assert rt.dto_persistido_lido
    finally:
        rt.worker_id, rt.worker_verbs = antes


async def test_renderizador_ainda_nao_sondado_nao_apaga_o_conhecido(harness: Harness) -> None:
    s = harness.state
    assert s is not None
    devs = s.devices
    rt = devs.get("android-01")
    rt.dto_persistido = {"renderer": {"configured": "host", "fallback": False, "gles": "host", "vulkan": "host"}}
    dto = {"renderer": {"configured": "host", "fallback": False, "gles": None, "vulkan": None}}
    assert devs._dto_para_comparar(rt, dto)["renderer"]["gles"] == "host"
    outro = {"renderer": {"configured": "host", "fallback": True, "gles": "swiftshader", "vulkan": None}}
    comparado = devs._dto_para_comparar(rt, outro)["renderer"]
    assert comparado["gles"] == "swiftshader" and comparado["fallback"] is True   # valor novo de verdade conta


async def test_seed_com_mensagem_marca_a_janela_e_o_reinicio_pula_o_dto_provisorio(harness: Harness) -> None:
    """Deploy 26 (04/10): as publicações do seed COM mensagem ("online — aparelho externo via ADB") gravam o DTO
    provisório (verbos de "sem worker", renderizador nulo). O evento passa a dizer `janela_do_seed`, e o processo novo
    procura a referência no último DTO com o renderizador conhecido, não na última linha."""
    import json

    s = harness.state
    assert s is not None
    devs = s.devices
    rt = devs.get("android-01")
    antes = (rt.worker_id, rt.worker_verbs, rt.renderer_configured)
    try:
        rt.worker_id, rt.worker_verbs, rt.renderer_configured = "worker-x", ["start", "stop"], "host"
        devs.publish(rt, level="warn")                                  # o worker falando: a referência boa
        boa = dict(rt.dto_persistido or {})
        rt.worker_verbs, rt.renderer_configured = None, None            # o backend cai e o seed grava o provisório
        devs.publish(rt, "online — aparelho externo via ADB")
        ultima = s.db.query("SELECT data FROM events WHERE kind='instance.updated' AND instance_id=? ORDER BY id DESC"
                            " LIMIT 1", (rt.id,))[0]
        dados = json.loads(ultima["data"])
        assert dados.get("janela_do_seed") is True
        assert (dados["instance"].get("renderer") or {}).get("configured") is None     # o provisório foi gravado

        # Processo novo: a última linha é a provisória; a referência é a boa.
        rt.dto_publicado = rt.controle_anunciado = rt.dto_persistido = None
        rt.dto_persistido_lido = False
        devs.publish(rt, "online — aparelho externo via ADB")           # seed de novo, com mensagem: grava
        assert (rt.dto_persistido or {}).get("supported_verbs") == boa["supported_verbs"]
        # O `hello` devolve os mesmos verbos e renderizador. O evento grava (o `stream` sai de `worker_offline`: o
        # worker conectou, isso é fato), sem a marca, e os verbos são os de antes do seed: a troca provisório → real tem
        # a anterior marcada `janela_do_seed` e não conta como oscilação.
        rt.worker_verbs, rt.renderer_configured = ["start", "stop"], "host"
        desde = _ultimo_id(s)
        devs.publish(rt)
        novos = s.db.query("SELECT data FROM events WHERE id > ? AND instance_id=? AND kind='instance.updated'",
                           (desde, rt.id))
        assert len(novos) == 1
        depois = json.loads(novos[0]["data"])
        assert "janela_do_seed" not in depois
        assert depois["instance"]["supported_verbs"] == boa["supported_verbs"]
        assert (depois["instance"].get("renderer") or {}).get("configured") == (boa.get("renderer") or {}).get("configured")
    finally:
        rt.worker_id, rt.worker_verbs, rt.renderer_configured = antes
