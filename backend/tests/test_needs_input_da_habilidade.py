"""I4 da validação do deploy 7: a habilidade que casou e não compilou vira `needs_input` com texto para o dono.

Antes, o quadro "A IA precisa de mais informações" mostrava `E_PLAN_INVALID: flow:…@1: …`, com o id e o código crus e o id
duas vezes. Agora a execução diz o NOME da habilidade e o motivo em português; o id e o código seguem no evento.

Prova `simulated`: o método do serviço com um repositório espião.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.models import RunStatus
from app.modules.skills.domain.errors import Code, CompileIssue
from app.taskqueue.service import RunService, _motivo_para_o_dono

REF = "flow:abrir-o-qa-messenger-e-navegar-ate-a-tel@1"


class RepoEspiao:
    def __init__(self) -> None:
        self.status: list[tuple[Any, ...]] = []
        self.eventos: list[tuple[str, dict[str, Any]]] = []
        self.bus = SimpleNamespace(emit=lambda tipo, msg, **kw: self.eventos.append((msg, kw)))

    def note_run_skill(self, *a: Any, **k: Any) -> None:
        pass

    def set_run_status(self, run_id: str, status: RunStatus, detalhe: str, **kw: Any) -> None:
        self.status.append((run_id, status, detalhe, kw))


def _sem_plano(issues: tuple[CompileIssue, ...], nome: str | None) -> RepoEspiao:
    repo = RepoEspiao()
    resolvida = SimpleNamespace(resolved=None, questions=(), issues=issues, ref=REF, name=nome)
    RunService._skill_sem_plano(SimpleNamespace(repo=repo), "r-1", resolvida)  # type: ignore[arg-type]  # noqa: SLF001
    return repo


def test_o_texto_da_execucao_diz_o_nome_e_o_motivo_sem_id_nem_codigo() -> None:
    falta = CompileIssue(Code.E_PLAN_INVALID, f"{REF}: {REF}: o comando não dá valor a todos os parâmetros do plano.")
    repo = _sem_plano((falta,), "Abrir o QA Messenger e navegar até a tela de Perfil")
    [(_, status, detalhe, _)] = repo.status
    assert status is RunStatus.needs_input
    assert detalhe == ("A habilidade “Abrir o QA Messenger e navegar até a tela de Perfil” não serve para este comando: "
                       "faltam valores para os parâmetros do plano. Corrija o comando ou a habilidade.")
    assert "E_PLAN_INVALID" not in detalhe and REF not in detalhe
    # quem desenvolve acha o id e o código no evento
    [(_, kw)] = repo.eventos
    assert kw["data"]["skill"] == REF and kw["data"]["issues"][0]["code"] == "E_PLAN_INVALID"


def test_motivos_repetidos_saem_uma_vez_e_os_outros_passam_sem_o_id() -> None:
    a = CompileIssue(Code.E_PLAN_INVALID, f"{REF}: o comando não dá valor a todos os parâmetros do plano.")
    b = CompileIssue(Code.E_PLAN_INVALID, "nó n2: a capability saiu do catálogo.")
    [(_, _, detalhe, _)] = _sem_plano((a, a, b), None).status
    assert detalhe == ("A habilidade casada não serve para este comando: faltam valores para os parâmetros do plano; "
                       "nó n2: a capability saiu do catálogo. Corrija o comando ou a habilidade.")


def test_o_motivo_sem_o_id_na_frente() -> None:
    assert _motivo_para_o_dono(f"{REF}: algo deu errado.", REF) == "algo deu errado"
    assert _motivo_para_o_dono("sem id aqui", REF) == "sem id aqui"
