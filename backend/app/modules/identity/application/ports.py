"""Portas que o contexto de identidade CONSOME (design §3: a porta pertence a quem consome; §7).

Quem implementa não importa estes `Protocol`s: `SocialRepository` e `EventBus` cumprem as duas primeiras por
estrutura, o `InstagramAuthenticator` cumpre `SessionProvider`, e o `bootstrap` (hoje `AppState`) liga as partes.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol


class DeviceRef(Protocol):
    """O aparelho em que a sessão é aberta. O núcleo passa o `DeviceRuntime`; a porta só promete a identidade —
    o que o provedor faz com o resto (fila exclusiva, driver, adb) é dele."""

    @property
    def id(self) -> str: ...


class SessionOutcome(Protocol):
    """O desfecho de uma garantia de sessão. `ready` só quando a conta ESPERADA foi lida na tela (ADR-009: incerteza
    nunca conta como sucesso)."""

    @property
    def ready(self) -> bool: ...

    @property
    def detail(self) -> str: ...


class SessionProvider(Protocol):
    """Quem abre e confere a conta de um perfil num app, no aparelho — fora do laço da IA (design §7, §2.5).

    É o que tira do núcleo as comparações com `"instagram"`: a porta de sessão, a reobservação depois do controle
    manual e "Conectar"/"Verificar conta" pedem ao provedor do PACOTE, achado no registro de apps, em vez de chamar o
    autenticador do Instagram pelo nome. A implementação de hoje é `integrations/instagram/authentication.py`.

    - `automatic=True` é a chamada do agendador: estado que só uma pessoa resolve (desafio, conta errada) nem toca o
      aparelho;
    - `observe_only=True` é "Verificar conta": lê a tela e nunca autentica;
    - `force_login=True` refaz o login mesmo com a sessão aberta (pedido explícito do painel).

    O provedor nunca repete envio por timeout, nunca segue com conta errada e nunca tenta resolver desafio de
    segurança (ADR-009); a senha só passa pelo canal sensível (ADR-025). O que ele observa ele grava na sessão do
    perfil, aplicando as regras de `session_rules` (ADR-029).

    O classificador de tela da §7 (`classify`) ficou de fora: nenhum código do núcleo o consumiria sem mudar
    comportamento (a detecção genérica de desafio em `automation/hierarchy.py` vale para qualquer app, de propósito).
    """

    @property
    def package(self) -> str: ...

    async def ensure_session(self, rt: DeviceRef, profile_id: str, *, force_login: bool = False,
                             automatic: bool = False, observe_only: bool = False) -> SessionOutcome: ...


class ProfileStore(Protocol):
    """O pedaço do repositório de perfis que as regras de sessão usam (`social.repository.SocialRepository`)."""

    def profile_row(self, profile_id: str) -> Mapping[str, object] | None: ...

    def update_profile(self, profile_id: str, fields: dict[str, object]) -> None: ...


class EventSink(Protocol):
    """`events.EventBus.emit`, só com os argumentos que a identidade usa."""

    def emit(self, kind: str, message: str, *, level: str = "info", instance_id: str | None = None,
             data: dict[str, object] | None = None) -> object: ...
