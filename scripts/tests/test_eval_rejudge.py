"""scripts/eval_rejudge.py (achados #98/#99): só a lógica pura — nada de rede, nada de banco de verdade.

Cobre a extração do veredito do Haiku a partir da nota de evidência (a mesma regra que a grava, em
executor.py) e a montagem do StepContext a partir de uma linha da consulta, incluindo o caso em que o app não
está mais cadastrado (LEFT JOIN vazio) e o caso de `postcondition` corrompido/ausente.
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

_spec = importlib.util.spec_from_file_location("eval_rejudge", ROOT / "scripts" / "eval_rejudge.py")
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)  # type: ignore[union-attr]


def _row(**over: object) -> dict[str, object]:
    base = {"evidence_id": 1, "run_id": "r1", "instance_id": "android-01", "step_id": "r1:android-01:v1:send",
            "ts": "2026-09-20T00:05:00Z", "note": "Pós-condição comprovada: mensagem enviada",
            "path": "r1/android-01/x.jpg", "title": "Enviar", "goal": "tocar em enviar",
            "postcondition": '{"kind": "model_judged", "value": "msg enviada", "description": "mensagem aparece como enviada"}',
            "account_label": "qa-user-01", "app_id": "qa-messenger", "app_name": "QA Messenger",
            "app_package": "com.pocqa.messenger", "app_activity": ".MainActivity", "app_nav_hints": "dicas",
            "app_selectors": '{"send_button": "id=send"}'}
    return {**base, **over}


class TestHaikuOk(unittest.TestCase):
    def test_comprovada_e_ok(self) -> None:
        self.assertTrue(mod._haiku_ok("Pós-condição comprovada: mensagem enviada"))

    def test_nao_comprovada_nao_e_ok(self) -> None:
        self.assertFalse(mod._haiku_ok("Pós-condição NÃO comprovada: sem sinal de envio"))


class TestCtxDe(unittest.TestCase):
    def test_monta_contexto_com_postcondicao_e_app(self) -> None:
        ctx = mod._ctx_de(_row())
        self.assertEqual(ctx.step_key, "send")
        self.assertEqual(ctx.app.package, "com.pocqa.messenger")
        self.assertEqual(ctx.postcondition_description, "mensagem aparece como enviada")
        self.assertEqual(ctx.account_label, "qa-user-01")

    def test_sobrevive_a_app_nao_cadastrado(self) -> None:
        """LEFT JOIN apps vazio (app removido do catálogo desde a captura): tudo None, sem exceção."""
        ctx = mod._ctx_de(_row(app_id=None, app_name=None, app_package=None, app_activity=None,
                               app_nav_hints=None, app_selectors=None))
        self.assertIsNone(ctx.app.package)

    def test_sobrevive_a_postcondicao_corrompida(self) -> None:
        """JSON quebrado ou ausente: cai para a nota de evidência em vez de lançar exceção no meio do lote."""
        ctx = mod._ctx_de(_row(postcondition="não é json"))
        self.assertEqual(ctx.postcondition_description, _row()["note"])


if __name__ == "__main__":
    unittest.main()
