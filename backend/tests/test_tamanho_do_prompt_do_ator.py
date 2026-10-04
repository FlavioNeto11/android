"""Item 31.35: o tamanho das partes do prompt do ator é medido, e a barra do navegador sai da árvore do prompt.

Achado real do 28.12 (r-20261004090000-bbfe54): cada decisão do ator trazia ~2,8 mil tokens de entrada nova, e o
banco não dizia quanto era árvore e quanto era histórico. A migração 097 grava os dois (em caracteres) e quantos
elementos da barra do navegador a poda tirou; a poda (`ai.podar_ui_do_navegador`) só mexe no que VAI AO MODELO.

Nível de prova: `simulated` (árvore escrita à mão e harness com provedor simulado; nenhuma chamada de IA).
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.automation.hierarchy import parse_hierarchy
from app.taskqueue.executor import UI_DO_NAVEGADOR

from .conftest import Harness

CHROME = "com.android.chrome"
XML = (
    '<hierarchy rotation="0">'
    f'<node index="0" text="" resource-id="{CHROME}:id/url_bar" class="android.widget.EditText" package="{CHROME}"'
    ' content-desc="" clickable="true" enabled="true" focusable="true" bounds="[0,0][720,100]" />'
    f'<node index="1" text="" resource-id="{CHROME}:id/tab_switcher_button" class="android.widget.ImageButton"'
    f' package="{CHROME}" content-desc="Abas" clickable="true" enabled="true" bounds="[600,0][660,100]" />'
    f'<node index="2" text="Lista de compras" resource-id="" class="android.view.View" package="{CHROME}"'
    ' content-desc="" clickable="true" enabled="true" bounds="[0,200][720,300]" />'
    f'<node index="3" text="Aceitar" resource-id="{CHROME}:id/terms_accept" class="android.widget.Button"'
    f' package="{CHROME}" content-desc="" clickable="true" enabled="true" bounds="[0,400][720,500]" />'
    '</hierarchy>')
TERMINAIS = ("completed", "completed_with_issues", "failed", "waiting_user")


def test_a_poda_tira_so_a_barra_do_navegador_do_prompt() -> None:
    arvore = parse_hierarchy(XML)
    inteira = "\n".join(arvore.prompt_lines(50))
    podada = "\n".join(arvore.prompt_lines(50, ocultar=UI_DO_NAVEGADOR[CHROME]))
    assert "url_bar" in inteira and "tab_switcher_button" in inteira
    # 31.52: a barra de endereço FICA (o ator lê a página e digita o endereço); o resto da barra sai
    assert "url_bar" in podada and "tab_switcher_button" not in podada
    # o conteúdo da página e o diálogo próprio do Chrome (fora da lista fechada) continuam no prompt
    assert "Lista de compras" in podada and "terms_accept" in podada
    assert len(arvore.elements) == 4            # a árvore local segue inteira (seletores, guardas, pós-condições)


async def test_cada_decisao_do_ator_grava_o_tamanho_da_arvore_e_do_historico(harness: Harness) -> None:
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=TERMINAIS)
    db = harness.state.db                       # type: ignore[union-attr]
    assert db is not None
    chamadas = db.query("SELECT role, prompt_arvore_chars, prompt_historico_chars, prompt_podados FROM ai_calls"
                        " WHERE run_id=? AND ok=1", (run.id,))
    decides = [c for c in chamadas if c["role"] == "decide"]
    assert decides, "o plano simulado não chamou o ator: o teste não mediria nada"
    for c in decides:
        assert c["prompt_arvore_chars"] > 0 and c["prompt_historico_chars"] >= 0 and c["prompt_podados"] == 0
    for c in chamadas:
        if c["role"] != "decide":
            assert (c["prompt_arvore_chars"], c["prompt_historico_chars"], c["prompt_podados"]) == (None, None, None)


async def test_31_52_o_diagnostico_grava_a_arvore_antes_da_poda_com_texto_redigido(tmp_path: Any) -> None:
    """31.52: a árvore inteira (com o que a poda tira) vira evidência `hierarchy` JSON; o texto passa pela redação; e o
    A/B offline (`scripts/poda-ab-offline.py`) remonta a mesma árvore e mede sem e com poda."""
    import importlib.util
    import json
    from pathlib import Path
    from types import SimpleNamespace

    from app.devices.manager import Observation
    from app.taskqueue.executor import StepExecutor

    xml = XML.replace('text="Lista de compras"', 'text="Lista senha=xyz12345"')    # formato que a redação conhece
    obs = Observation(frame_id="1", ts="2026-10-04T12:00:00Z", width=720, height=1280, jpeg=None,
                      tree=parse_hierarchy(xml), package=CHROME, sensitive=False)
    gravadas: list[dict[str, Any]] = []

    async def add_evidence_async(**k: Any) -> int:
        gravadas.append(k)
        return 1

    ex = object.__new__(StepExecutor)
    sem_conta = SimpleNamespace(one=lambda sql, args: None)          # android-09: aparelho de QA, sem vínculo
    ex.repo = SimpleNamespace(add_evidence_async=add_evidence_async, db=sem_conta)  # type: ignore[assignment]
    await ex._arvore_antes_da_poda(obs, 1, run_id="r", iid="android-09", step_id="s", attempt_id="a")  # noqa: SLF001
    [g] = gravadas
    assert (g["kind"], g["ext"], g["instance_id"]) == ("hierarchy", "json", "android-09")
    assert g["note"].startswith("31.52:")
    corpo = json.loads(g["data"].decode("utf-8"))
    assert len(corpo["elements"]) == 4 and corpo["podados"] == 1           # a árvore INTEIRA, antes da poda
    assert "xyz12345" not in g["data"].decode("utf-8")                       # segredo não vai para o disco
    arquivo = Path(tmp_path) / "arvore.json"
    arquivo.write_bytes(g["data"])
    raiz = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("poda_ab", raiz / "scripts" / "poda-ab-offline.py")
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    m = modulo.medir(json.loads(arquivo.read_text(encoding="utf-8")), 140)
    assert m["elementos"] == 4 and 0 < m["chars_com_poda"] < m["chars_sem_poda"]


def test_31_52_o_diagnostico_vem_desligado() -> None:
    from app.config import AiCfg
    assert AiCfg().diagnostico_arvore_aparelhos == []


async def test_31_52_aparelho_com_conta_real_nunca_grava_a_arvore(harness: Harness) -> None:
    """Revisão da orquestradora: listar um aparelho de conta real no diagnóstico não grava nada. A regra é a do
    ADR-055 (vínculo ativo de persona = conta real logada), lida do banco na hora de gravar."""
    from app.devices.manager import Observation
    from app.taskqueue.executor import StepExecutor

    st = harness.state
    assert st is not None
    obs = Observation(frame_id="1", ts="2026-10-04T12:00:00Z", width=720, height=1280, jpeg=None,
                      tree=parse_hierarchy(XML), package=CHROME, sensitive=False)
    gravadas: list[dict[str, Any]] = []

    async def add_evidence_async(**k: Any) -> int:
        gravadas.append(k)
        return 1

    ex = object.__new__(StepExecutor)
    ex.repo = SimpleNamespace(add_evidence_async=add_evidence_async, db=st.db)  # type: ignore[assignment]
    perfil = st.social_repo.create_profile(username="conta_teste", first_name=None, last_name=None,
                                           display_name=None, birth_date=None, email=None, persona_id=None)
    st.social_repo.bind(perfil, "android-01", reason="teste")
    await ex._arvore_antes_da_poda(obs, 1, run_id="r", iid="android-01", step_id="s", attempt_id="a")  # noqa: SLF001
    assert gravadas == []
    await ex._arvore_antes_da_poda(obs, 1, run_id="r", iid="android-02", step_id="s", attempt_id="a")  # noqa: SLF001
    assert [g["instance_id"] for g in gravadas] == ["android-02"]     # sem vínculo, grava
