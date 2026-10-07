"""29.176: o runner `central` (máquina do dono) nunca é acionado por push de branch nem por PR. Prova `simulated` (lê os YAML).

Regras que este teste trava:
- `ci.yml` (o que usa `CI_RUNS_ON`, ou seja, pode cair no runner `central`) só tem `schedule` e `workflow_dispatch`;
- nenhum outro workflow usa `CI_RUNS_ON`;
- push só na `main` (e sem tag); `pull_request` só em workflow hospedado em `ubuntu-latest`;
- `pull_request_target` só no workflow de rótulo, também hospedado.
"""
from __future__ import annotations

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
RODA_NO_CENTRAL = {"ci.yml"}


def carregar(arq: Path) -> dict:
    dado = yaml.safe_load(arq.read_text(encoding="utf-8"))
    dado["on"] = dado.pop(True, dado.get("on"))  # PyYAML lê a chave `on` como True
    return dado


def todos() -> dict[str, dict]:
    return {a.name: carregar(a) for a in sorted(WORKFLOWS.glob("*.yml"))}


class Gatilhos(unittest.TestCase):
    def test_ha_workflows_para_conferir(self) -> None:
        self.assertGreaterEqual(len(todos()), 5)

    def test_ci_que_pode_cair_no_central_so_tem_cron_e_disparo_manual(self) -> None:
        gatilhos = set(todos()["ci.yml"]["on"])
        self.assertEqual(gatilhos - {"schedule", "workflow_dispatch"}, set(), "push ou PR no ci.yml aciona o runner central (29.102, 29.176)")

    def test_so_o_ci_usa_o_runner_central(self) -> None:
        for nome, wf in todos().items():
            texto = (WORKFLOWS / nome).read_text(encoding="utf-8")
            codigo = "\n".join(l for l in texto.splitlines() if not l.lstrip().startswith("#"))
            self.assertEqual("CI_RUNS_ON" in codigo, nome in RODA_NO_CENTRAL, nome)

    def test_push_so_na_main_e_sem_tag(self) -> None:
        for nome, wf in todos().items():
            push = wf["on"].get("push") if isinstance(wf["on"], dict) else None
            if push is not None:
                self.assertEqual(push.get("branches"), ["main"], nome)
                self.assertEqual(push.get("tags-ignore"), ["**"], nome)

    def test_pull_request_so_em_workflow_hospedado(self) -> None:
        for nome, wf in todos().items():
            gatilhos = set(wf["on"]) if isinstance(wf["on"], dict) else {wf["on"]}
            if gatilhos & {"pull_request", "pull_request_target"}:
                self.assertNotIn(nome, RODA_NO_CENTRAL, nome)
                for job, corpo in wf["jobs"].items():
                    self.assertEqual(corpo["runs-on"], "ubuntu-latest", f"{nome}:{job}")


if __name__ == "__main__":
    unittest.main()
