"""Observação com a árvore primeiro e a imagem só quando precisa (contrato C1 do adendo v0.20).

Antes, toda observação da IA era screenshot → hierarquia → JPEG cheio + miniatura da prévia → e, se o modelo fosse
receber a imagem, uma TERCEIRA codificação no tamanho dele. A decisão de mandar a imagem (`_want_image`) vinha
depois de tudo isso. Agora a árvore vem primeiro, a decisão é tomada com ela, e o screencap só acontece quando a
imagem vai ser consumida — decodificada uma vez, codificada só no que alguém usa.

Prova `simulated`: aparelho falso do harness; nenhuma medida de CPU aqui, só contagem de chamadas ao aparelho.
"""
from __future__ import annotations

import asyncio
import io
from typing import Any

from PIL import Image

from app.metricas import metricas

from .conftest import Harness


async def _sem_laco(rt: Any) -> None:
    t = rt.tasks.get("capture")
    if t is not None and not t.done():
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass


async def _pronto(h: Harness, iid: str = "android-01") -> tuple[Any, Any, Any]:
    s = h.state
    assert s is not None
    devs = s.devices
    rt, fake = devs.get(iid), h.fakes[iid]
    await _sem_laco(rt)
    metricas.limpar()
    return devs, rt, fake


async def test_arvore_suficiente_nenhum_screencap_e_tamanho_do_ultimo_frame(harness: Harness) -> None:
    devs, rt, fake = await _pronto(harness)
    fake.screen = "home"
    await devs.observe(rt, timeout=5)                         # um frame desta geração: 720x1280
    antes = len(fake.calls)
    obs = await devs.observe(rt, timeout=5, imagem=lambda t: False, lado_max=768)
    assert fake.calls[antes:] == ["page_source"], "a política dispensou a imagem: nada de screencap"
    assert obs.jpeg is None and obs.image_omitted == "policy" and not obs.sensitive
    assert (obs.width, obs.height) == (720, 1280) and obs.image_at is None and obs.tree_at
    assert obs.source == "central_adb" and obs.runtime_gen == rt.geracao
    assert metricas.valor("captura.evitada", motivo="politica") == 1


async def test_arvore_insuficiente_imagem_logo_depois_da_arvore_ja_no_tamanho_do_modelo(harness: Harness) -> None:
    harness.cfg.file.ai.screenshot_max_side = 768
    devs, rt, fake = await _pronto(harness)
    fake.screen = "launcher"                                  # 1 elemento: árvore pobre
    antes = len(fake.calls)
    obs = await devs.observe(rt, timeout=5, imagem=lambda t: len(t.elements) < 8, lado_max=768)
    assert fake.calls[antes:] == ["page_source", "screenshot"], "árvore PRIMEIRO, imagem logo em seguida"
    assert obs.jpeg and obs.image_omitted is None and obs.tree_at and obs.image_at and obs.tree_at <= obs.image_at
    with Image.open(io.BytesIO(obs.jpeg)) as img:
        assert img.size == (432, 768)                         # já no espaço do modelo
    assert (obs.width, obs.height) == (720, 1280)             # coordenadas continuam em pixels do aparelho
    executor = harness.state.scheduler.executor               # type: ignore[union-attr]
    screen, escala = executor._screen(obs, with_image=True)
    assert screen.jpeg is obs.jpeg, "a tela do modelo reaproveita os bytes: nenhuma terceira codificação"
    assert (screen.width, screen.height) == (432, 768) and escala > 1
    # Sem ninguém olhando, a prévia não foi codificada: só a imagem do modelo.
    assert rt.frame is None or rt.frame.info.id != obs.frame_id
    assert metricas.total("captura.total") == 1


async def test_com_interesse_a_mesma_decodificacao_alimenta_a_previa(harness: Harness) -> None:
    devs, rt, fake = await _pronto(harness)
    devs.registrar_interesse("aba", ["android-01"], None, 20)
    fake.screen = "launcher"
    obs = await devs.observe(rt, timeout=5, imagem=lambda t: True, lado_max=768)
    assert rt.frame is not None and rt.frame.info.id == obs.frame_id and rt.frame.jpeg_full and rt.frame.jpeg_thumb
    assert sum(1 for c in fake.calls if c == "screenshot") == 1
    devs.soltar_interesse("aba")


async def test_sensivel_sem_imagem_e_sem_previa(harness: Harness) -> None:
    devs, rt, fake = await _pronto(harness)
    fake.screen = "home"
    await devs.observe(rt, timeout=5)
    fake.screen = "login"
    antes = len(fake.calls)
    obs = await devs.observe(rt, timeout=5)                   # mesmo pedindo imagem (o padrão)
    assert fake.calls[antes:] == ["page_source"], "tela sensível: nem screencap"
    assert obs.jpeg is None and obs.sensitive and obs.image_omitted == "sensitive"
    assert rt.frame is not None and rt.frame.sensitive and rt.frame.info.id == obs.frame_id
    assert metricas.valor("captura.evitada", motivo="sensivel") == 1


async def test_sem_imagem_o_tamanho_segue_a_orientacao_da_hierarquia(harness: Harness) -> None:
    devs, rt, fake = await _pronto(harness)
    fake.screen = "home"
    await devs.observe(rt, timeout=5)                         # retrato 720x1280 visto nesta geração
    original = fake.page_source
    fake.page_source = lambda: original().replace('rotation="0"', 'rotation="1"')  # type: ignore[method-assign]
    try:
        obs = await devs.observe(rt, timeout=5, imagem=False)
    finally:
        fake.page_source = original                           # type: ignore[method-assign]
    assert (obs.width, obs.height) == (1280, 720) and obs.jpeg is None


async def test_sem_tamanho_conhecido_a_imagem_e_adquirida(harness: Harness) -> None:
    """Sem frame desta geração e sem `wm size` (o dublê não tem), o tamanho não se inventa: a imagem vem."""
    devs, rt, fake = await _pronto(harness)
    rt.geracao += 1                                           # nova geração: nada conhecido
    fake.screen = "home"
    antes = len(fake.calls)
    obs = await devs.observe(rt, timeout=5, imagem=False)
    assert fake.calls[antes:] == ["page_source", "screenshot"] and obs.jpeg and obs.image_omitted is None


async def test_imagem_tardia_nunca_de_tela_sensivel(harness: Harness) -> None:
    devs, rt, fake = await _pronto(harness)
    fake.screen = "login"
    antes = len(fake.calls)
    assert await devs.imagem_tardia(rt, timeout=5) is None
    assert "screenshot" not in fake.calls[antes:], "a classificação vem antes do screencap da evidência"
    fake.screen = "home"
    await devs.observe(rt, timeout=5, imagem=False)
    tardia = await devs.imagem_tardia(rt, timeout=5)
    assert tardia is not None and tardia[0][:2] == b"\xff\xd8" and tardia[1]


async def test_politica_auto_so_faz_screencap_quando_a_imagem_vai_ao_modelo(harness: Harness) -> None:
    harness.cfg.file.ai.image_policy = "auto"
    harness.cfg.file.ai.rich_tree_min_elements = 3
    devs, rt, fake = await _pronto(harness, "android-02")
    janelas: list[tuple[str | None, list[str]]] = []
    original = devs.observe

    async def observar(rt_: Any, **kw: Any) -> Any:
        antes = len(fake.calls)
        obs = await original(rt_, **kw)
        janelas.append((obs.image_omitted, fake.calls[antes:]))
        return obs

    devs.observe = observar                                   # type: ignore[method-assign]
    try:
        run = harness.run(["android-02"])
        assert (await harness.wait_run(run.id)).status == "completed"
    finally:
        devs.observe = original                               # type: ignore[method-assign]
    so_arvore = [c for omitida, c in janelas if omitida == "policy"]
    assert so_arvore, "com a árvore rica, alguma observação tinha de dispensar a imagem"
    assert all("screenshot" not in c for c in so_arvore)
    com_imagem = [c for omitida, c in janelas if omitida is None]
    assert all(c.index("page_source") < c.index("screenshot") for c in com_imagem)
    decides = [c for c in harness.ai.calls if c["role"] == "decide"]
    assert any(not c["image"] for c in decides)


async def test_politica_always_manda_imagem_em_toda_decisao(harness: Harness) -> None:
    harness.cfg.file.ai.image_policy = "always"
    run = harness.run(["android-01"])
    assert (await harness.wait_run(run.id)).status == "completed"
    decides = [c for c in harness.ai.calls if c["role"] == "decide"]
    assert decides and all(c["image"] for c in decides)


async def test_falha_grava_evidencia_com_imagem_tardia(harness: Harness) -> None:
    """Etapa que não se comprova (o envio se perdeu): a evidência final tem imagem mesmo com a observação só de árvore
    — adquirida depois, com o próprio horário na nota, e nunca de tela sensível."""
    harness.cfg.file.ai.image_policy = "auto"
    harness.cfg.file.ai.rich_tree_min_elements = 3
    fake = harness.fakes["android-01"]
    fake.send_fault = "error_lost"
    run = harness.run(["android-01"])
    detail = await harness.wait_run(run.id, timeout=90)
    assert detail.objectives[0].status == "uncertain"
    s = harness.state
    assert s is not None
    linhas = s.db.query("SELECT note, path, redacted FROM evidence WHERE run_id=? ORDER BY id", (run.id,))
    tardias = [r for r in linhas if "imagem adquirida depois da observação" in (r["note"] or "")]
    assert tardias and all(r["path"] and not r["redacted"] for r in tardias)
    falha = [r for r in linhas if (r["note"] or "").startswith("Pós-condição NÃO comprovada")]
    assert falha and all(r["path"] for r in falha), "a evidência da falha tem imagem"
