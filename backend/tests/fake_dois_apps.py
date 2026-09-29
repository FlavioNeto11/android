"""Dublês da fase K1: o QA Messenger como SEGUNDO app registrado e um aparelho com os dois apps no mesmo convidado.

- `manifesto_do_qa()`: o que um app novo precisa trazer — definição, catálogo (3 capabilities que o `FakeQaDevice`
  sabe encenar) e provedor de sessão (`SessaoDoQa`). Registrado SÓ por `register_manifest()` dentro de teste, e
  tirado no fim: embutido, ele mudaria `_policy_gate` e `_mistura_de_apps` da suíte inteira (o QA passaria a "ter
  catálogo", e toda etapa com efeito sem capability do plano simulado seria recusada).
- `AparelhoComDoisApps`: um `DeviceIO` que delega ao app em primeiro plano; `open_app(pacote)` troca a frente. É o
  mínimo para um processo que atravessa apps rodar num aparelho só, com o `FakeInstagram` e o `FakeQaDevice` de
  sempre, sem mudar nenhum dos dois nem o `Harness` (que já aceita `factory=`).
- `AtorDosDoisApps`: o provedor de IA por regras que conduz cada etapa com o ator do app DELA (`ctx.app.package`):
  `AtorDoInstagram` nas do Instagram, `SimulatedProvider` (que conhece o QA pelas chaves `open_conversation`,
  `compose_message`, `send_message`) nas do QA. Não planeja: pelo caminho de skills o planejador não é chamado.

Nível de prova de quem usa: `simulated`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.devices.adb import MorteDoApp
from app.models import AiStatus, PersonaDraft, Plan, SessionStatus, SocialDraftDTO
from app.modules.applications.domain.definition import AppDefinition
from app.modules.applications.infrastructure.registry import AppManifest
from app.modules.identity.infrastructure.sessions import SessionDeps
from app.planning.capabilities import Capability, CapabilityCatalog
from app.planning.provider import (Decision, DecisionRequest, PersonaGenerationRequest, PlanRequest, SocialRequest, Usage, Verdict,
                                   VerifyRequest)
from app.planning.simulated_provider import SimulatedProvider
from app.util import now_iso

from .fake_device import PKG as QA
from .fake_device import FakeQaDevice
from .fake_instagram import PKG as IG
from .fake_instagram import AtorDoInstagram, FakeInstagram


#: Tipo do provedor de sessão do QA no registro (o do Instagram é "instagram").
TIPO_DE_SESSAO_DO_QA = "qa-messenger"


def catalogo_do_qa() -> CapabilityCatalog:
    """Três capabilities do QA Messenger com os textos que o `SimulatedProvider` já conduz (as mesmas etapas do plano
    simulado de "enviar mensagem"). As chaves de `bindings` são os nomes que o ator lê em `ctx.parameters`."""
    return CapabilityCatalog(QA, [
        Capability(
            key="QA_OPEN_CHAT", title="Abrir a conversa com {recipient}",
            goal="Localizar o contato {recipient} na lista e abrir a conversa.",
            post_kind="element_present", post_value="id=chat_title|text={recipient}",
            post_description="O cabeçalho da conversa mostra {recipient}.", bindings=("recipient",)),
        Capability(
            key="QA_COMPOSE", title="Preencher a mensagem", goal="Digitar o conteúdo no campo Mensagem.",
            post_kind="text_visible", post_value="{message}", post_description="O campo de mensagem contém o texto.",
            bindings=("message",)),
        Capability(
            key="QA_SEND_MESSAGE", title="Enviar a mensagem para {recipient}", goal="Tocar em Enviar uma única vez.",
            post_kind="model_judged", post_value="mensagem na conversa",
            post_description="A mensagem aparece na conversa.", bindings=("recipient", "message"),
            side_effect=True, risk="low", commit_selector="id=send_button", commit_guard=("{recipient}", "{message}"),
            reconciliation="Observar a conversa: a mensagem aparece com status; enviar de novo duplicaria."),
    ])


@dataclass(frozen=True)
class ResultadoDoQa:
    """O `SessionOutcome` do provedor do QA."""

    ready: bool
    detail: str


class SessaoDoQa:
    """Provedor de sessão do QA Messenger (dublê que cumpre `SessionProvider`).

    Lê a tela do app no aparelho — o formulário `login_account` é "deslogado", o resto é "conta aberta" — e grava o
    que viu na sessão do perfil pelo repositório que a COMPOSIÇÃO entregou (`SessionDeps.repo`), como o autenticador
    do Instagram faz. Nunca digita nada. Registra cada pedido, para o teste saber quem a porta chamou.
    """

    def __init__(self, deps: SessionDeps | None = None) -> None:
        self.deps = deps
        self.chamadas: list[tuple[str, str, bool, bool, bool]] = []
        self.contas: list[str | None] = []           # o `account_id` de cada chamada, na ordem (item 23.4)

    @property
    def package(self) -> str:
        return QA

    async def ensure_session(self, rt: Any, profile_id: str, *, account_id: str | None = None,
                             force_login: bool = False, automatic: bool = False, observe_only: bool = False) -> ResultadoDoQa:
        self.chamadas.append((rt.id, profile_id, force_login, automatic, observe_only))
        self.contas.append(account_id)
        pronto = "login_account" not in rt.io.page_source()
        detalhe = "conta do QA Messenger aberta na tela" if pronto else "o QA Messenger pede login"
        if self.deps is not None:
            # Grava na conta DO QA (a dita, ou a da persona no pacote), como o motor genérico desde o 23.4 — nunca
            # na conta âncora do perfil.
            conta = account_id
            if conta is None:
                linha = self.deps.repo.conta_do_pacote(profile_id, QA)
                conta = str(linha["id"]) if linha is not None else None
            if conta is not None:
                self.deps.repo.set_account_session(profile_id, conta, rt.id, status=SessionStatus.session_ready
                                                   if pronto else SessionStatus.auth_required,
                                                   verified_at=now_iso() if pronto else None, detail=detalhe)
        return ResultadoDoQa(pronto, detalhe)


def manifesto_do_qa() -> AppManifest:
    """O app novo inteiro: manifesto + provedor + catálogo. Nada disto existe no núcleo."""
    return AppManifest(definition=AppDefinition(package=QA, name="QA Messenger", label="QA Messenger",
                                                session_provider=TIPO_DE_SESSAO_DO_QA),
                       catalog=catalogo_do_qa(), session=SessaoDoQa)


class AparelhoComDoisApps:
    """Um convidado com o Instagram e o QA Messenger. Tudo que é de tela vai ao app da frente; `open_app` escolhe.

    O que o `FakeInstagram` expõe além do `DeviceIO` (foco, diálogo do sistema, `getprop`...) chega pelo app da frente
    (`__getattr__`), do jeito que o executor e o gerenciador já o procuram por `getattr`.
    """

    def __init__(self, instagram: FakeInstagram, qa: FakeQaDevice) -> None:
        self.instagram = instagram
        self.qa = qa
        self.frente: FakeInstagram | FakeQaDevice = instagram
        self.aberturas: list[str] = []

    def _app(self, package: str) -> FakeInstagram | FakeQaDevice | None:
        return self.instagram if package == IG else self.qa if package == QA else None

    def __getattr__(self, nome: str) -> Any:
        if nome == "frente":                       # antes do __init__ terminar: sem isto, recursão infinita
            raise AttributeError(nome)
        return getattr(self.frente, nome)

    # ------------------------------------------------------------------ DeviceIO: saúde do convidado
    def framework_alive(self, *, timeout: float = 25) -> bool:
        return True

    def system_server_alive(self, *, timeout: float = 8) -> bool:
        return True

    def display_alive(self, *, timeout: float = 12) -> bool:
        return True

    def guest_pressure(self) -> dict[str, float]:
        return {"load1": 0.5, "mem_total_mb": 2048.0, "mem_available_mb": 900.0, "ncpu": 2.0}

    def connectivity_probe(self) -> dict[str, bool]:
        return {"route": True, "dns": True, "tcp_443": True, "validated": True}

    # ------------------------------------------------------------------ DeviceIO: tela e interação
    def screenshot_png(self) -> bytes:
        return self.frente.screenshot_png()

    def page_source(self) -> str:
        return self.frente.page_source()

    def current_package(self) -> str | None:
        return self.frente.current_package()

    def current_focus(self) -> tuple[str | None, str | None]:
        return self.frente.current_focus()

    def app_deaths(self, package: str, *, within_s: float | None = None) -> list[MorteDoApp]:
        app = self._app(package)
        return app.app_deaths(package, within_s=within_s) if app is not None else []

    def app_version(self, package: str) -> str:
        app = self._app(package)
        return app.app_version(package) if app is not None else ""

    def tap(self, x: int, y: int) -> None:
        self.frente.tap(x, y)

    def long_press(self, x: int, y: int, duration_ms: int) -> None:
        self.frente.long_press(x, y, duration_ms)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> None:
        self.frente.swipe(x1, y1, x2, y2, duration_ms)

    def type_text(self, text: str, *, clear_first: bool) -> None:
        self.frente.type_text(text, clear_first=clear_first)

    def set_text(self, text: str, *, clear_first: bool) -> None:
        self.frente.set_text(text, clear_first=clear_first)

    def press_key(self, key: str) -> None:
        self.frente.press_key(key)

    def open_app(self, package: str, activity: str | None) -> None:
        app = self._app(package)
        if app is None:
            return
        self.aberturas.append(package)
        self.frente = app
        app.open_app(package, activity)

    def open_url(self, url: str) -> None:
        self.frente.open_url(url)

    def force_stop(self, package: str) -> None:
        app = self._app(package)
        if app is not None:
            app.force_stop(package)


class AtorDosDoisApps:
    """Cada etapa com o ator do app dela. Não é IA e não finge ser."""

    name = "ator-dos-dois-apps"
    model = "roteiro-por-app"
    simulated = True

    def __init__(self) -> None:
        self.instagram = AtorDoInstagram()
        self.qa = SimulatedProvider()

    def _de(self, req: DecisionRequest | VerifyRequest) -> Any:
        return self.instagram if req.ctx.app.package == IG else self.qa

    def status(self) -> AiStatus:
        return AiStatus(provider=self.name, model=self.model, configured=True, simulated=True,
                        sends_data_externally=False, effort=None,
                        notice="Teste: um ator por regras para cada app do processo.")

    async def plan(self, req: PlanRequest) -> tuple[Plan, Usage]:
        raise AssertionError("pelo caminho de skills o planejador não é chamado")

    async def decide(self, req: DecisionRequest) -> tuple[Decision, Usage]:
        decisao, uso = await self._de(req).decide(req)
        return decisao, uso

    async def verify(self, req: VerifyRequest) -> tuple[Verdict, Usage]:
        veredito, uso = await self._de(req).verify(req)
        return veredito, uso

    async def generalize(self, req: Any) -> tuple[dict[str, Any], Usage]:
        raise AssertionError("o ator dos dois apps não generaliza demonstrações")

    async def generate_social_response(self, req: SocialRequest) -> tuple[SocialDraftDTO, Usage]:
        rascunho, uso = await self.qa.generate_social_response(req)
        return rascunho, uso

    async def generate_persona(self, req: PersonaGenerationRequest) -> tuple[PersonaDraft, Usage]:
        return await self.qa.generate_persona(req)
