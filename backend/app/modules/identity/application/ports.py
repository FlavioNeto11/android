"""Portas que o contexto de identidade CONSOME (design §3: a porta pertence a quem consome; §7).

Quem implementa não importa estes `Protocol`s: `SocialRepository` e `EventBus` cumprem as duas primeiras por
estrutura, a `SessaoDeclarada` cumpre `SessionProvider`, e o `bootstrap` (hoje `AppState`) liga as partes.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from app.modules.identity.domain.persona_image import PersonaImageRecord, PersonaImageSpec


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


class CodigoDeEmail(Protocol):
    """O código de confirmação que um app manda por e-mail à conta (ADR-090). O valor só existe entre a caixa e o
    canal de entrada sensível: nunca vai a log, evento, evidência ou resposta."""

    async def codigo_depois_de(self, account_id: str, desde: datetime, *, espera_s: float) -> str | None:
        """O código MAIS NOVO que `desde` (UTC) na caixa da conta, esperando até `espera_s`; `None` se a conta não tem
        caixa, o leitor não está disponível ou nada chegou no prazo. Código mais antigo que `desde` nunca serve."""
        ...


class SessionProvider(Protocol):
    """Quem abre e confere a conta de um perfil num app, no aparelho — fora do laço da IA (design §7, §2.5).

    É o que tira do núcleo as comparações com `"instagram"`: a porta de sessão, a reobservação depois do controle
    manual e "Conectar"/"Verificar conta" pedem ao provedor do PACOTE, achado no registro de apps, em vez de chamar o
    autenticador do Instagram pelo nome. A implementação de hoje é o motor genérico
    `integrations/app_declarado/sessao.py`, dirigido pelo `sessao.yaml` de cada app (ADR-052, fatia 3).

    - `automatic=True` é a chamada do agendador: estado que só uma pessoa resolve (desafio, conta errada) nem toca o
      aparelho;
    - `observe_only=True` é "Verificar conta": lê a tela e nunca autentica;
    - `force_login=True` refaz o login mesmo com a sessão aberta (pedido explícito do painel);
    - `account_id` (contrato C1, item 23.4, ADR-057) é a conta que a chamada abre: do perfil e do pacote DESTE
      provedor, senão a chamada recusa sem tocar no aparelho. `None` = a conta do perfil neste app: no provedor do app
      âncora é a conta âncora, como sempre foi; em qualquer outro, a conta daquele app — nunca a de outro app.

    O provedor nunca repete envio por timeout, nunca segue com conta errada e nunca tenta resolver desafio de
    segurança (ADR-009); a senha só passa pelo canal sensível (ADR-025). Tudo o que ele lê e grava — credencial,
    tentativa, teto diário, marcação da credencial, sessão no aparelho — é DESSA conta (`account_credentials`,
    `authentication_attempts.account_id`, `account_sessions`), e a conta lida na tela é comparada ao @ e ao login
    dela, não ao @ de cadastro do perfil. O desafio (`session_rules.aplicar_desafio`, item 23.5): no app âncora
    bloqueia a persona (ADR-029/055); em qualquer outro app para só a conta daquele app, e a conta travada põe o
    aparelho em quarentena sem bloquear a persona.

    O classificador de tela da §7 (`classify`) ficou de fora: nenhum código do núcleo o consumiria sem mudar
    comportamento (a detecção genérica de desafio em `automation/hierarchy.py` vale para qualquer app, de propósito).
    """

    @property
    def package(self) -> str: ...

    async def ensure_session(self, rt: DeviceRef, profile_id: str, *, account_id: str | None = None,
                             force_login: bool = False, automatic: bool = False,
                             observe_only: bool = False) -> SessionOutcome: ...


class ProfileStore(Protocol):
    """O pedaço do repositório de perfis que as regras de sessão usam (`social.repository.SocialRepository`)."""

    def profile_row(self, profile_id: str) -> Mapping[str, object] | None: ...

    def update_profile(self, profile_id: str, fields: dict[str, object]) -> None: ...


class CredenciaisDaConta(Protocol):
    """O pedaço do repositório que para o login automático de UMA conta (item 23.5): a credencial dela e a marcação
    (`account_credentials.status`). `social.repository.SocialRepository` cumpre por estrutura."""

    def account_credential_row(self, profile_id: str, account_id: str) -> Mapping[str, object] | None: ...

    def mark_account_credential(self, profile_id: str, account_id: str, *, status: str,
                                failed_attempts: int | None = None, blocked_until: str | None = None) -> None: ...


@runtime_checkable
class QuarentenaDeContas(Protocol):
    """A quarentena de aparelho com conta travada (ADR-055, pacote "quarentena", migração 054):
    `social.repository.SocialRepository.marcar_conta_travada`, com a assinatura de lá.

    `origem` é COMO se sabe — `observado` (a tela foi lida), `declarado` (uma pessoa disse), `regra` (uma regra
    decidiu) — e a implementação levanta `ValueError` para qualquer outra; QUEM viu vai em `visto_por`. Na revisão do
    pacote "detector", `origem="sessao"`/`"execucao"` fez a quarentena real recusar toda marcação. Devolve se o
    marcador nasceu agora (`False`: já havia um aberto para a conta naquele aparelho).

    `runtime_checkable` porque a quarentena chega em paralelo: o repositório que ainda não a tem simplesmente não
    cumpre esta porta, e quem chama segue sem ela."""

    def marcar_conta_travada(self, instance_id: str, handle: str, evidencia: str | None, origem: str, *,
                             visto_por: str = ..., profile_id: str | None = ...,
                             app_id: str | None = ...) -> bool: ...


class EventSink(Protocol):
    """`events.EventBus.emit`, só com os argumentos que a identidade usa."""

    def emit(self, kind: str, message: str, *, level: str = "info", instance_id: str | None = None,
             data: dict[str, object] | None = None) -> object: ...


# ------------------------------------------------------------------ imagens da persona (evolução 2, onda A)
@dataclass(frozen=True, slots=True)
class GeneratedImage:
    """O que um gerador devolve: os bytes JÁ pós-processados (`data`) e, quando houver, o original intacto do
    provedor (`original`) — é ele que preserva a proveniência (C2PA) que a recompressão apaga."""

    data: bytes
    mime: str
    width: int
    height: int
    provider_request_id: str | None = None
    usd: float = 0.0
    ms: int = 0
    provider_seed: str | None = None
    original: bytes | None = None
    original_mime: str | None = None


class ImageGenerator(Protocol):
    """Quem transforma uma receita (`PersonaImageSpec`) em imagem. Implementações em `modules/identity/adapters/`:
    o simulado (Pillow determinístico, sem chave, nada sai da máquina) e o OpenAI Images. `reference` é a imagem
    PRINCIPAL da pessoa, para as seguintes manterem o mesmo rosto — e é o único dado além de atributos que sai.

    Recusa do filtro do provedor sobe como `GeracaoRecusada`; falha, como `GeracaoFalhou`. Nunca se cai do pago
    para o simulado por conta própria (a mesma regra do hub de IA)."""

    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...

    @property
    def simulated(self) -> bool: ...

    @property
    def sends_data_externally(self) -> bool: ...

    @property
    def configured(self) -> bool: ...

    async def generate(self, spec: PersonaImageSpec, *, reference: bytes | None = None) -> GeneratedImage: ...


class ImageBlobStore(Protocol):
    """Os bytes de uma imagem, por chave de storage (`app.storage.Storage` por baixo, fora do laço de eventos)."""

    async def put(self, key: str, data: bytes, *, content_type: str) -> None: ...

    def get(self, key: str) -> bytes | None: ...

    def exists(self, key: str) -> bool: ...

    def delete(self, key: str) -> None: ...


class PersonaImageRepository(Protocol):
    """As linhas de `persona_images` (048). Toda leitura exige `persona_id`: regra de isolamento do repositório social."""

    def insert(self, record: PersonaImageRecord) -> None: ...

    def finish(self, persona_id: str, image_id: str, *, status: str, storage_key: str | None, original_key: str | None,
               width: int | None, height: int | None, bytes_sha256: str | None, cost_usd: float,
               provider_request_id: str | None, provider_seed: str | None, error: str | None) -> None: ...

    def list(self, persona_id: str) -> list[PersonaImageRecord]: ...

    def get(self, persona_id: str, image_id: str) -> PersonaImageRecord | None: ...

    def set_primary(self, persona_id: str, image_id: str) -> None: ...

    def set_feita_por_ia(self, persona_id: str, image_id: str, valor: bool | None) -> bool:
        """Grava a resposta; `True` só quando ela MUDOU (decidido na própria escrita, não numa leitura antes)."""
        ...

    def delete(self, persona_id: str, image_id: str) -> None: ...


class ImageAccounting(Protocol):
    """O custo das imagens no MESMO relatório das chamadas de texto (`ai_calls`, `role='image'`, coluna `usd`)."""

    def spent_today_usd(self) -> float: ...

    def daily_limit_usd(self) -> float: ...

    def balance_block_reason(self) -> str | None:
        """Conta do gerador barrada pelo saldo (ADR-051)? `None` libera."""
        ...

    def record(self, *, provider: str, model: str, usd: float, ms: int, ok: bool, error: str | None) -> None: ...
