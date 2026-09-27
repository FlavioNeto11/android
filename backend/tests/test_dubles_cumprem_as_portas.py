"""Cada dublê dos testes tem a FORMA do `Protocol` que ele finge cumprir (fase G, G1).

O mypy estrito só olha `app.contracts` e `app.modules`; os dublês moram em `tests/` e entram no `AppState` por
injeção (`io_factory`, `provider`, `emulator`), fora de qualquer conferência de tipo. Um método que falta só aparece
quando o código de produção o chama — no meio de um teste que fala de outra coisa (foi assim que o `FakeInstagram`
ficou sem os cinco métodos de saúde do convidado que o `DeviceIO` ganhou). Aqui se confere, por `inspect.signature`,
nome, tipo e padrão de cada parâmetro de cada método da porta, se é `async` quando a porta é, e os atributos
declarados.

Nível de prova: `simulated` (só forma; o comportamento é dos testes de cada dublê).
"""
from __future__ import annotations

import inspect
from typing import Any

import pytest

from app.automation.driver import DeviceIO
from app.devices.emulator_backend import EmulatorBackend, FakeEmulatorBackend
from app.integrations.instagram.authentication import InstagramAuthenticator
from app.modules.identity.application.ports import SessionProvider
from app.modules.skills.application.ports import DocumentValidator
from app.planning.provider import AIProvider
from app.planning.simulated_provider import SimulatedProvider

from .conftest import CountingProvider
from .fake_device import FakeQaDevice
from .fake_dois_apps import AparelhoComDoisApps, AtorDosDoisApps, SessaoDoQa
from .fake_instagram import AtorDoInstagram, FakeInstagram
from .fake_skills import ValidadorFalso

PARES: list[tuple[Any, Any]] = [
    (DeviceIO, FakeQaDevice(account="qa")),
    (DeviceIO, FakeInstagram()),
    (EmulatorBackend, FakeEmulatorBackend()),
    (AIProvider, CountingProvider(SimulatedProvider())),
    (AIProvider, CountingProvider(AtorDoInstagram())),
    (AIProvider, AtorDoInstagram()),
    (DocumentValidator, ValidadorFalso()),
    # fase K1: o aparelho com dois apps, o ator que conduz cada app, e os dois provedores de sessão (o do Instagram
    # é o de produção: a conferência prova que ele cumpre a porta que o núcleo agora chama)
    (DeviceIO, AparelhoComDoisApps(FakeInstagram(), FakeQaDevice(account="qa"))),
    (AIProvider, AtorDosDoisApps()),
    (SessionProvider, SessaoDoQa()),
    (SessionProvider, InstagramAuthenticator(None, None, None, None, None, None)),
]


def _metodos(porta: Any) -> list[str]:
    return [m for m, v in vars(porta).items() if not m.startswith("_") and inspect.isfunction(v)]


def _forma(fn: Any) -> list[tuple[str, Any, Any]]:
    return [(p.name, p.kind, p.default) for p in inspect.signature(fn).parameters.values() if p.name != "self"]


@pytest.mark.parametrize(("porta", "duble"), PARES, ids=lambda x: getattr(x, "__name__", type(x).__name__))
def test_duble_tem_a_forma_da_porta(porta: Any, duble: Any) -> None:
    metodos = _metodos(porta)
    assert metodos, f"{porta.__name__} sem método público: a comparação não conferiria nada"
    for nome in metodos:
        real = getattr(duble, nome, None)
        assert callable(real), f"{type(duble).__name__} não tem {porta.__name__}.{nome}"
        esperado = getattr(porta, nome)
        assert _forma(real) == _forma(esperado), f"{type(duble).__name__}.{nome} não tem a forma de {porta.__name__}"
        assert inspect.iscoroutinefunction(real) == inspect.iscoroutinefunction(esperado), \
            f"{type(duble).__name__}.{nome}: async na porta e no dublê têm de bater"
    for atributo in getattr(porta, "__annotations__", {}):
        assert hasattr(duble, atributo), f"{type(duble).__name__} não declara {porta.__name__}.{atributo}"


def test_a_conferencia_reprova_um_duble_torto() -> None:
    """Autoteste: um dublê sem um método, ou com o parâmetro trocado, reprova."""
    class SemMetodo:
        def inspect(self) -> None: ...

    with pytest.raises(AssertionError):
        test_duble_tem_a_forma_da_porta(DocumentValidator, SemMetodo())
    with pytest.raises(AssertionError):
        test_duble_tem_a_forma_da_porta(DeviceIO, object())
