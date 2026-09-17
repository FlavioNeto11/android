"""Fase 0A — a credencial não pode ser registrada em lugar nenhum.

O vazamento que estes testes protegem é real e foi reproduzido neste repositório: o PIN do app de QA aparecia em
texto claro em `data/logs/appium.log`, porque o Appium registra o corpo de cada requisição e o projeto o iniciava
com `--log-level info` sem mascaramento.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any
from xml.sax.saxutils import quoteattr

import pytest

from app.automation.appium_server import LOG_FILTER_RULES
from app.automation.hierarchy import parse_hierarchy
from app.security.redaction import RedactingFilter, redact, redact_obj
from app.security.sensitive_input import SensitiveInputChannel, SensitiveInputError, SensitiveInputUnavailable

from .conftest import Harness

SECRET = "$a=B7ee1#<b-C?S-{"          # formato realista: símbolos, sem espaço
LEAK_TEMPLATE = '[HTTP] --> POST /session/abc/execute/sync {"script":"mobile: type","args":[{"text":"VALOR"}]}'


def leak_line(secret: str = SECRET) -> str:
    return LEAK_TEMPLATE.replace("VALOR", secret)


def apply_appium_rules(line: str) -> str:
    """Aplica as regras exatamente como o Appium aplicaria (JS `String.replace`, com grupos `$1`)."""
    for rule in LOG_FILTER_RULES:
        replacement = re.sub(r"\$(\d)", r"\\\1", rule["replacer"])
        line = re.compile(rule["pattern"]).sub(replacement, line)
    return line


class FakeSecretField:
    """Campo de senha de mentira: guarda o texto e registra cada chamada do driver."""

    def __init__(self, text: str = "", *, clearable: bool = True, raise_with: str | None = None):
        self.text = text
        self.clearable = clearable
        self.raise_with = raise_with
        self.calls: list[tuple[Any, ...]] = []

    def tap(self, x: int, y: int) -> None:
        self.calls.append(("tap", x, y))

    def type_text(self, text: str, *, clear_first: bool) -> None:
        self.calls.append(("type", text, clear_first))
        if self.raise_with:
            raise RuntimeError(f"falha do driver ao digitar {self.raise_with}")
        if clear_first and self.clearable:
            self.text = ""
        self.text += text

    # -- o que o canal enxerga ------------------------------------------------
    def tree(self) -> Any:
        node = ('<node class="android.widget.EditText" resource-id="com.instagram.android:id/password" '
                f"text={quoteattr(self.text)} password=\"true\" clickable=\"true\" enabled=\"true\" "
                'bounds="[40,300][680,380]"/>')
        return parse_hierarchy("<hierarchy>" + node + "</hierarchy>")

    async def observe(self) -> Any:
        return self.tree()

    @staticmethod
    def locate(tree: Any) -> Any:
        found = tree.find_selector("id=password")
        return found[0] if found else None

    @property
    def typed(self) -> list[str]:
        return [c[1] for c in self.calls if c[0] == "type" and c[1]]


async def run_call(fn: Any, *args: Any, timeout: float = 0, label: str = "") -> Any:
    return fn(*args)


def channel(active: bool = True) -> SensitiveInputChannel:
    return SensitiveInputChannel(lambda: active)


# ---------------------------------------------------------------- canal de entrada sensível
async def test_preenche_uma_unica_vez_e_nao_registra_o_valor_nem_o_tamanho() -> None:
    field = FakeSecretField()
    receipt = await channel().fill(call=run_call, io=field, observe=field.observe, locate=field.locate,
                                   secret=lambda: SECRET)
    assert field.typed == [SECRET]                       # digitado exatamente uma vez
    assert ("type", "", True) in field.calls             # limpou antes
    assert field.calls[0][0] == "tap"                    # focou antes de limpar
    registro = json.dumps(receipt.to_dict())
    assert receipt.to_dict() == {"sensitive_input_completed": True, "field": "com.instagram.android:id/password"}
    assert SECRET not in registro and str(len(SECRET)) not in registro   # nem o valor, nem o comprimento


async def test_campo_com_resto_de_tentativa_anterior_e_limpo_antes() -> None:
    field = FakeSecretField(text=SECRET)                 # tentativa anterior preencheu e caiu antes de enviar
    await channel().fill(call=run_call, io=field, observe=field.observe, locate=field.locate, secret=lambda: SECRET)
    assert field.text == SECRET                          # não ficou concatenado
    assert field.typed == [SECRET]


async def test_campo_que_nao_esvazia_aborta_sem_digitar_a_senha() -> None:
    field = FakeSecretField(text="resto", clearable=False)
    with pytest.raises(SensitiveInputError, match="não ficou vazio"):
        await channel().fill(call=run_call, io=field, observe=field.observe, locate=field.locate,
                             secret=lambda: SECRET)
    assert field.typed == []                             # a senha nunca foi digitada


async def test_erro_do_driver_nunca_propaga_o_texto_digitado() -> None:
    field = FakeSecretField(raise_with=SECRET)
    with pytest.raises(SensitiveInputError) as exc:
        await channel().fill(call=run_call, io=field, observe=field.observe, locate=field.locate,
                             secret=lambda: SECRET)
    assert SECRET not in str(exc.value)
    assert exc.value.__cause__ is None and exc.value.__context__ is None   # nada encadeado que vaze no traceback


async def test_sem_mascaramento_comprovado_o_canal_se_recusa_a_operar() -> None:
    field = FakeSecretField()
    with pytest.raises(SensitiveInputUnavailable):
        await channel(active=False).fill(call=run_call, io=field, observe=field.observe, locate=field.locate,
                                         secret=lambda: SECRET)
    assert field.calls == []                             # não tocou no aparelho


async def test_segredo_so_e_resolvido_no_ultimo_instante() -> None:
    field = FakeSecretField()
    resolved: list[int] = []

    def secret() -> str:
        resolved.append(len(field.calls))
        return SECRET

    await channel().fill(call=run_call, io=field, observe=field.observe, locate=field.locate, secret=secret)
    assert resolved == [2]        # só depois de focar e limpar; uma única resolução


# ---------------------------------------------------------------- mascaramento na origem (Appium)
def test_regras_do_appium_sao_validas_e_mascaram_o_vazamento_conhecido() -> None:
    assert json.loads(json.dumps(LOG_FILTER_RULES))      # o Appium recusa subir com regra inválida
    masked = apply_appium_rules(leak_line())
    assert SECRET not in masked and "**SECURE**" in masked


def test_envio_de_teclas_do_webdriver_tambem_e_mascarado() -> None:
    line = '{"text":"' + SECRET + '","value":["a","b"]}'
    assert SECRET not in apply_appium_rules(line)


def test_pin_do_app_de_qa_tambem_seria_mascarado() -> None:
    # o valor que hoje está em claro em data/logs/appium.log
    assert '"text":"1234"' not in apply_appium_rules(leak_line("1234"))


# ---------------------------------------------------------------- redator (camada extra)
def test_redator_cobre_os_formatos_que_carregam_segredo() -> None:
    assert SECRET not in (redact(leak_line()) or "")
    assert SECRET not in (redact('{"password": "' + SECRET + '"}') or "")
    assert SECRET not in (redact("senha=" + SECRET) or "")
    assert SECRET not in json.dumps(redact_obj({"credential": {"password": SECRET}}))
    assert redact("mensagem comum sem segredo") == "mensagem comum sem segredo"


def test_filtro_de_log_redige_a_mensagem_formatada() -> None:
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "senha=%s", (SECRET,), None)
    assert RedactingFilter().filter(record) is True
    assert SECRET not in record.getMessage()


# ---------------------------------------------------------------- integração: nada chega ao banco nem ao painel
async def test_evento_com_segredo_nao_e_persistido_em_claro(harness: Harness) -> None:
    state = harness.state
    assert state is not None
    state.bus.emit("log", "requisição: " + leak_line(), data={"payload": {"password": SECRET}})
    dump = "".join(str(dict(r)) for r in state.db.query("SELECT * FROM events"))
    assert SECRET not in dump


async def test_painel_informa_quando_o_mascaramento_nao_esta_comprovado(harness: Harness) -> None:
    state = harness.state
    assert state is not None
    state.appium.is_up = lambda timeout=1.0: True            # type: ignore[assignment]
    state.appium.log_masking_active = False
    codes = [p.code for p in state.health().problems]
    assert "appium_log_masking_off" in codes
    state.appium.log_masking_active = True
    assert "appium_log_masking_off" not in [p.code for p in state.health().problems]
