"""Item 31.54: URL fora do prompt — a imagem (barra tapada, atrás de chave), o `observed_result` e o `read_value`.

O 31.52 limpou a barra de endereço na árvore e no histórico do ator. A medida da orquestradora (04/10, 7 dias, só
leitura) achou o resto do caminho: a imagem do Chrome mostra a barra, o `observed_result` grava o que a IA escreveu
da tela e o valor lido que é URL volta ao ator no histórico.

Nível de prova: `simulated` (imagem gerada aqui, árvore escrita à mão, harness com provedor simulado; nenhuma chamada
de IA, nenhum aparelho).
"""
from __future__ import annotations

import io
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from app.automation.hierarchy import parse_hierarchy
from app.config import AiCfg
from app.devices.manager import Observation
from app.models import AttemptStatus
from app.taskqueue import executor as executor_mod
from app.taskqueue.executor import StepExecutor, imagem_com_barra_tapada, linha_do_valor_lido

from .conftest import Harness

CHROME = "com.android.chrome"
LARGURA, ALTURA = 720, 1280
COM_BARRA = (
    '<hierarchy rotation="0">'
    f'<node index="0" text="contas.exemplo/reset/tok?token=abc123" resource-id="{CHROME}:id/url_bar"'
    f' class="android.widget.EditText" package="{CHROME}" content-desc="" clickable="true" enabled="true"'
    ' bounds="[0,0][720,100]" />'
    f'<node index="1" text="Conteúdo" resource-id="" class="android.view.View" package="{CHROME}" content-desc=""'
    ' clickable="false" enabled="true" bounds="[0,200][720,300]" />'
    '</hierarchy>')
SEM_BARRA = COM_BARRA.replace(f'resource-id="{CHROME}:id/url_bar"', 'resource-id=""')
TERMINAIS = ("completed", "completed_with_issues", "failed", "waiting_user")


def _branca(largura: int = LARGURA, altura: int = ALTURA) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (largura, altura), (255, 255, 255)).save(buf, "JPEG", quality=90)
    return buf.getvalue()


def _pixel(jpeg: bytes, x: int, y: int) -> int:
    with Image.open(io.BytesIO(jpeg)) as img:
        return int(sum(img.convert("RGB").getpixel((x, y))) / 3)


def _obs(xml: str) -> Observation:
    return Observation(frame_id="1", ts="2026-10-04T21:40:00Z", width=LARGURA, height=ALTURA, jpeg=_branca(),
                       tree=parse_hierarchy(xml), package=CHROME, sensitive=False)


def _executor() -> StepExecutor:
    ex = object.__new__(StepExecutor)
    ex.cfg = SimpleNamespace(file=SimpleNamespace(ai=AiCfg()))  # type: ignore[assignment]
    return ex


# ================================================================== (1) a imagem
def test_a_barra_e_tapada_pelos_bounds_da_arvore_na_escala_da_imagem() -> None:
    # imagem já reduzida à metade (o tamanho do modelo); os bounds da árvore estão no tamanho do aparelho
    tapada = imagem_com_barra_tapada(_branca(360, 640), parse_hierarchy(COM_BARRA), CHROME, LARGURA, ALTURA)
    assert tapada is not None
    assert _pixel(tapada, 180, 25) < 40          # dentro da barra (0..50 na imagem reduzida): preto
    assert _pixel(tapada, 180, 120) > 215        # o conteúdo da página fica


def test_sem_a_barra_na_arvore_nao_tapa_nada() -> None:
    assert imagem_com_barra_tapada(_branca(), parse_hierarchy(SEM_BARRA), CHROME, LARGURA, ALTURA) is None
    assert imagem_com_barra_tapada(_branca(), parse_hierarchy(COM_BARRA), "com.outro.app", LARGURA, ALTURA) is None


def test_a_chave_vem_desligada_e_a_imagem_vai_como_hoje() -> None:
    assert AiCfg().tapar_barra_de_endereco is False
    tela, _ = _executor()._screen(_obs(COM_BARRA), ai=AiCfg())  # noqa: SLF001
    assert tela.jpeg is not None and _pixel(tela.jpeg, tela.width // 2, 5) > 215


def test_ligada_a_imagem_do_ator_e_do_juiz_sai_com_a_barra_tapada(monkeypatch: pytest.MonkeyPatch) -> None:
    contadas: list[dict[str, Any]] = []
    monkeypatch.setattr(executor_mod.metricas, "contar", lambda nome, n=1, **r: contadas.append({"nome": nome, **r}))
    ligada = AiCfg(tapar_barra_de_endereco=True)
    tela, _ = _executor()._screen(_obs(COM_BARRA), ai=ligada)  # noqa: SLF001
    assert tela.jpeg is not None and _pixel(tela.jpeg, tela.width // 2, 5) < 40
    # a árvore que vai junto continua com o endereço limpo do 31.52, não com o cru
    assert not any("abc123" in linha for linha in tela.elements)
    # sem bounds da barra: a imagem vai como está e a métrica diz por quê
    tela, _ = _executor()._screen(_obs(SEM_BARRA), ai=ligada)  # noqa: SLF001
    assert tela.jpeg is not None and _pixel(tela.jpeg, tela.width // 2, 5) > 215
    assert [c["resultado"] for c in contadas if c["nome"] == "executor.barra_tapada"] == ["tapada", "sem_bounds"]


# ================================================================== (3) o valor lido
def test_o_valor_lido_que_e_url_vai_limpo_ao_historico_do_ator() -> None:
    linha = linha_do_valor_lido("link", "https://contas.exemplo/convite/Ab12Cd34Ef56Gh78?ref=x", ["assunto"])
    assert linha == "read_value(link) → lido: https://contas.exemplo/convite/…?…; faltam: assunto"
    assert linha_do_valor_lido("protocolo", "2026-0042", []) == (
        "read_value(protocolo) → lido: 2026-0042; todos os valores da etapa lidos")


# ================================================================== (2) o observed_result
async def test_o_observed_result_grava_a_url_limpa(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=TERMINAIS)
    tentativa = st.db.one("SELECT a.id FROM attempts a JOIN steps s ON s.id=a.step_id WHERE s.run_id=?"
                          " ORDER BY a.started_at, a.id LIMIT 1", (run.id,))
    assert tentativa is not None
    st.db.execute("UPDATE attempts SET status='running' WHERE id=?", (tentativa["id"],))
    st.repo.finish_attempt(tentativa["id"], AttemptStatus.succeeded,
                           observed="A página https://contas.exemplo/reset/tok?token=abc123 abriu")
    gravado = st.db.one("SELECT observed_result FROM attempts WHERE id=?", (tentativa["id"],))
    assert gravado is not None and gravado["observed_result"] == "A página https://contas.exemplo/reset/…?… abriu"
