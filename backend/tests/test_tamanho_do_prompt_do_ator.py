"""Item 31.35: o tamanho das partes do prompt do ator é medido, e a barra do navegador sai da árvore do prompt.

Achado real do 28.12 (r-20261004090000-bbfe54): cada decisão do ator trazia ~2,8 mil tokens de entrada nova, e o
banco não dizia quanto era árvore e quanto era histórico. A migração 097 grava os dois (em caracteres) e quantos
elementos da barra do navegador a poda tirou; a poda (`ai.podar_ui_do_navegador`) só mexe no que VAI AO MODELO.

Nível de prova: `simulated` (árvore escrita à mão e harness com provedor simulado; nenhuma chamada de IA).
"""
from __future__ import annotations

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
    assert "url_bar" not in podada and "tab_switcher_button" not in podada
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
