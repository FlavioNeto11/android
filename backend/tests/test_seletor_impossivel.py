"""31.32: o seletor composto da pós-condição que pede no MESMO elemento (`|`) o que a tela mostra em elementos diferentes.

Achado real: r-20261004082521-2f21e2 (04/10, android-12). O plano pediu `id=…message_input|text=Suporte QA` com a
conversa aberta: a caixa de texto e o título com "Suporte QA" estavam na tela, mas em elementos diferentes. A etapa
falhou 3 vezes, a recuperação copiou a etapa igual e falhou mais 3.

Nível de prova: `simulated` (árvores e aparelho falsos, harness com o provedor simulado). Prova-se que:
- o diagnóstico só vale quando CADA parte casa sozinha algum elemento e nenhum elemento casa todas;
- a verificação devolve `unprovable` só na tela final (nunca na conferência de uma rodada antes do ator);
- a etapa falha na 1ª tentativa, sem plano revisado, com o tipo `seletor_em_elementos_diferentes` (camada do plano);
- o seletor composto válido (id e texto no mesmo elemento) continua comprovando.
"""
from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.automation.hierarchy import UiTree, parse_hierarchy
from app.models import Postcondition, StepDTO, StepStatus
from app.modules.learning.domain.falhas import Camada, FailureKind, camada_de, classificar_texto
from app.planning.simulated_provider import _post
from app.taskqueue.executor import PARTES_EM_ELEMENTOS_DIFERENTES

from .conftest import Harness
from .test_caminho_rapido_2 import IID, PKG, _executor


def _conversa() -> UiTree:
    """A conversa aberta: o título com o contato e a caixa de texto vazia, em elementos separados."""
    return parse_hierarchy(
        "<hierarchy>"
        f'<node class="android.widget.TextView" text="Suporte QA" resource-id="{PKG}:id/chat_title" bounds="[0,0][700,80]"/>'
        f'<node class="android.widget.EditText" text="" resource-id="{PKG}:id/message_input" bounds="[0,1100][600,1180]"/>'
        "</hierarchy>")


def test_o_diagnostico_exige_cada_parte_na_tela_e_nenhum_elemento_com_todas() -> None:
    tela = _conversa()
    assert tela.partes_em_elementos_diferentes("id=message_input|text=Suporte QA")      # o caso da 2f21e2
    assert not tela.partes_em_elementos_diferentes("id=chat_title|text=Suporte QA")     # válido: mesmo elemento
    assert not tela.partes_em_elementos_diferentes("id=message_input|text=Bruno")       # uma parte não está na tela
    assert not tela.partes_em_elementos_diferentes("id=message_input")                  # uma parte só
    assert not tela.partes_em_elementos_diferentes("text=Suporte QA")


def _etapa(valor: str) -> StepDTO:
    return StepDTO(id=f"r-sel:{IID}:v1:abrir", run_id="r-sel", objective_id=f"r-sel:{IID}", instance_id=IID,
                   plan_version=1, seq=1, key="abrir", title="Abrir a conversa", goal="abrir a conversa",
                   depends_on=[], side_effect=False,
                   postcondition=Postcondition(kind="element_present", value=valor, description="A conversa está aberta."),
                   timeout_s=60, max_attempts=3, attempts=1, status=StepStatus.verifying)


async def _verificar(tmp_path: Path, valor: str, *, uma_rodada: bool = False) -> tuple[bool, str, bool]:
    ex, _aparelho, juiz = _executor(tmp_path, [_conversa()])
    ok, texto, _nivel, _obs, impossivel = await ex._verify(  # noqa: SLF001
        SimpleNamespace(id=IID), _etapa(valor), lambda: SimpleNamespace(step_key="abrir", instance_id=IID),  # type: ignore[arg-type]
        "r-sel", f"r-sel:{IID}", time.monotonic() + 60.0, 5.0, patient=False, facts=[], pacote=None,
        uma_rodada=uma_rodada)
    assert juiz.chamadas == 0                                   # element_present é determinístico: nada de juiz
    return ok, texto, impossivel


async def test_a_verificacao_marca_o_seletor_impossivel_na_tela_final(tmp_path: Path) -> None:
    ok, texto, impossivel = await _verificar(tmp_path, "id=message_input|text=Suporte QA")
    assert (ok, impossivel) == (False, True)
    assert PARTES_EM_ELEMENTOS_DIFERENTES in texto and "0 elemento(s)" in texto


async def test_conferencia_de_uma_rodada_e_seletor_ausente_nao_viram_defeito(tmp_path: Path) -> None:
    # Antes do ator (LT-1/LT-2) a tela ainda não é a final: o "não" volta ao ator como sempre.
    ok, _texto, impossivel = await _verificar(tmp_path, "id=message_input|text=Suporte QA", uma_rodada=True)
    assert (ok, impossivel) == (False, False)
    # Uma parte que não está na tela é a falha de sempre (a tela certa pode não ter chegado).
    ok, texto, impossivel = await _verificar(tmp_path, "id=message_input|text=Bruno")
    assert (ok, impossivel) == (False, False) and PARTES_EM_ELEMENTOS_DIFERENTES not in texto
    # O composto válido segue comprovando.
    ok, _texto, impossivel = await _verificar(tmp_path, "id=chat_title|text=Suporte QA")
    assert (ok, impossivel) == (True, False)


def test_o_texto_da_falha_tem_tipo_proprio_na_camada_do_plano() -> None:
    texto = ("Defeito do plano — o seletor da pós-condição exige no MESMO elemento (`|`) o que a tela mostra em "
             "elementos separados; repetir ou recuperar não resolve: seletor id=message_input|text=Suporte QA: 0 "
             f"elemento(s); {PARTES_EM_ELEMENTOS_DIFERENTES}")
    assert classificar_texto(texto) is FailureKind.SELETOR_EM_ELEMENTOS_DIFERENTES
    assert camada_de(FailureKind.SELETOR_EM_ELEMENTOS_DIFERENTES) is Camada.PLANO
    # o defeito do plano de sempre continua com o tipo dele
    assert classificar_texto("Defeito do plano — a pós-condição não é comprovável pela tela") is FailureKind.DEFEITO_DO_PLANO


async def test_a_etapa_falha_na_primeira_tentativa_sem_plano_revisado(harness: Harness) -> None:
    """Ponta a ponta no harness: o plano simulado com o seletor da 2f21e2 na abertura da conversa. Antes eram 3
    tentativas e um plano revisado com a mesma etapa; agora é 1 tentativa, `plan_version` 1, e nada é enviado."""
    harness.encurtar_verificacao(1.0)
    inner = harness.ai.inner
    plan0 = inner.plan

    async def plan(req: Any) -> Any:
        p, u = await plan0(req)
        passos = [s.model_copy(update={"postcondition": _post("element_present", "id=message_input|text={recipient}",
                                                             "O cabeçalho da conversa mostra {recipient}.")})
                  if s.key == "open_conversation" else s for s in p.steps]
        return p.model_copy(update={"steps": passos}), u

    inner.plan = plan
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, statuses=("completed", "completed_with_issues", "failed", "waiting_user"))
    db = harness.state.db                                          # type: ignore[union-attr]
    obj = db.one("SELECT * FROM objectives WHERE run_id=?", (run.id,))
    assert obj["status"] == "failed" and "Defeito do plano" in obj["status_detail"]
    assert obj["plan_version"] == 1                                # a recuperação não copiou a etapa igual
    etapa = db.one("SELECT attempts, failure_kind FROM steps WHERE objective_id=? AND key='open_conversation'", (obj["id"],))
    assert etapa["attempts"] == 1 and etapa["failure_kind"] == FailureKind.SELETOR_EM_ELEMENTOS_DIFERENTES.value
    assert not harness.fakes["android-01"].messages                # nenhum envio
