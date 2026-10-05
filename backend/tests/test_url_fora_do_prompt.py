"""Item 31.54: URL fora do prompt — a imagem (barra tapada, atrás de chave), o `observed_result` e o `read_value`.

O 31.52 limpou a barra de endereço na árvore e no histórico do ator. A medida da orquestradora (04/10, 7 dias, só
leitura) achou o resto do caminho: a imagem do Chrome mostra a barra, o `observed_result` grava o que a IA escreveu
da tela e o valor lido que é URL volta ao ator no histórico.

Nível de prova: `simulated` (imagem gerada aqui, árvore escrita à mão, harness com provedor simulado; nenhuma chamada
de IA, nenhum aparelho).
"""
from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
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


def test_a_chave_vem_ligada_e_desligada_a_imagem_vai_como_esta() -> None:
    """31.56: o A/B ao vivo deu LIGA, e o padrão é tapar. Com `false` explícito a imagem vai como antes."""
    assert AiCfg().tapar_barra_de_endereco is True
    tela, _ = _executor()._screen(_obs(COM_BARRA), ai=AiCfg(tapar_barra_de_endereco=False))  # noqa: SLF001
    assert tela.jpeg is not None and _pixel(tela.jpeg, tela.width // 2, 5) > 215


def test_o_padrao_do_codigo_e_o_do_exemplo_nao_divergem() -> None:
    """31.56: quem sobe sem `config.yaml` lê o exemplo; o padrão do código e o do exemplo têm de dizer o mesmo."""
    exemplo = Path(__file__).resolve().parents[2] / "config" / "config.example.yaml"
    bruto = yaml.safe_load(exemplo.read_text(encoding="utf-8"))
    assert bruto["ai"]["tapar_barra_de_endereco"] is AiCfg().tapar_barra_de_endereco


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
    assert linha == 'read_value(link) → lido e entregue: "https://contas.exemplo/convite/…?…"; faltam: assunto'
    assert linha_do_valor_lido("protocolo", "2026-0042", []) == (
        'read_value(protocolo) → lido e entregue: "2026-0042"; todos os valores da etapa lidos')


def test_a_recusa_do_juiz_com_url_vai_limpa_ao_historico_do_ator() -> None:
    """U1: com a barra sem tapar, o juiz pode transcrever a URL da imagem na recusa."""
    from app.taskqueue.executor import linha_da_recusa_do_juiz

    linha = linha_da_recusa_do_juiz("no meio da tentativa",
                                     "a barra mostra https://contas.exemplo/reset/tok?token=abc123 e não a lista")
    assert "abc123" not in linha and "tok" not in linha
    assert "https://contas.exemplo/reset/…?…" in linha and "NÃO está comprovada" in linha


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


async def test_o_erro_da_recusa_grava_a_url_limpa(harness: Harness) -> None:
    """U1: o `error` da tentativa (o texto do juiz pelo `fail_or_retry`, ou o anotado) volta no histórico da seguinte,
    no painel e no aviso: grava limpo, pelos dois caminhos."""
    st = harness.state
    assert st is not None
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=TERMINAIS)
    tentativa = st.db.one("SELECT a.id FROM attempts a JOIN steps s ON s.id=a.step_id WHERE s.run_id=?"
                          " ORDER BY a.started_at, a.id LIMIT 1", (run.id,))
    assert tentativa is not None
    st.db.execute("UPDATE attempts SET status='running', error=NULL WHERE id=?", (tentativa["id"],))
    st.repo.note_attempt(tentativa["id"], error="juiz: vi https://contas.exemplo/reset/tok?token=abc123")
    anotado = st.db.one("SELECT error FROM attempts WHERE id=?", (tentativa["id"],))
    assert anotado is not None and "abc123" not in anotado["error"] and "contas.exemplo/reset/…" in anotado["error"]
    st.repo.finish_attempt(tentativa["id"], AttemptStatus.failed,
                           error="recusada: a tela é https://contas.exemplo/reset/tok?token=abc123#x")
    gravado = st.db.one("SELECT error FROM attempts WHERE id=?", (tentativa["id"],))
    assert gravado is not None and "abc123" not in gravado["error"] and "contas.exemplo/reset/…" in gravado["error"]


async def test_a_recusa_final_do_juiz_com_url_vai_limpa_a_etapa_ao_objetivo_e_a_nota(
        harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """U1b: a recusa FINAL do juiz vira a nota da evidência e o `detail` do desfecho, que segue para
    `steps.status_detail`, `objectives.status_detail` e `blocked_reason` e para a mensagem do evento. Limpa na fonte
    (o veredito), nenhum desses destinos guarda a query nem o caminho além do 1º pedaço."""
    from app.planning.provider import Usage, Verdict

    st = harness.state
    assert st is not None
    executor = st.scheduler.executor
    juizes: list[int] = []

    async def verify(req: Any) -> tuple[Verdict, Usage]:
        juizes.append(1)
        return Verdict(satisfied="no", evidence="a barra mostra https://contas.exemplo/reset/tok?token=abc123#x "
                                                "e a mensagem não aparece"), Usage()

    monkeypatch.setattr(executor.provider, "verify", verify)
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=TERMINAIS + ("completed_with_issues", "cancelled"), timeout=60)
    assert juizes, "o juiz tem de ter sido chamado para a prova valer"
    textos = [r["t"] or "" for r in st.db.query(
        "SELECT status_detail t FROM steps WHERE run_id=? UNION ALL SELECT status_detail FROM objectives WHERE run_id=?"
        " UNION ALL SELECT blocked_reason FROM objectives WHERE run_id=? UNION ALL SELECT note FROM evidence"
        " WHERE run_id=? UNION ALL SELECT message FROM events WHERE run_id=?", (run.id,) * 5)]
    assert not [t for t in textos if "abc123" in t or "/reset/tok" in t]
    assert any("contas.exemplo/reset/…?…" in t for t in textos)     # o texto do juiz chegou, limpo
    notas = [r["note"] or "" for r in st.db.query("SELECT note FROM evidence WHERE run_id=?", (run.id,))]
    assert any(n.startswith("Pós-condição NÃO comprovada: ") and "contas.exemplo/reset/…?…" in n for n in notas)
    bloqueio = [r["b"] for r in st.db.query("SELECT blocked_reason b FROM objectives WHERE run_id=?", (run.id,))]
    assert any(b and "contas.exemplo/reset/…?…" in b for b in bloqueio)
