"""Dados da persona disponíveis ao plano (ADR-040): nomes lógicos, nunca valores de segredo.

O planejador e o ator recebem uma LISTA (nome, rótulo, tipo, sigiloso, app). Dado não sigiloso vira variável
`{perfil_email}` resolvida por aparelho na materialização; dado sigiloso (`conta_<app>_senha`) só existe para o
modelo como NOME, e o valor sai do cofre direto para o campo de senha por `type_secret`. Nada aqui lê valor de
segredo: a conta chega como `AccountRecord`, com a referência do cofre e os metadados, e é isso que se olha.

Os nomes seguem o alfabeto de `TEMPLATE_RE` (`taskqueue/recipes.py`) e de `PARAMETER_NAME` da DSL: minúsculas,
dígitos e sublinhado. `SENSITIVE_PARAM` já casa `senha`, então uma receita nunca guarda o que tiver esse sufixo.

Regra D2: função pura sobre dataclasses; quem lê o banco é a porta de aplicação e o adaptador de infraestrutura.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

#: O mesmo alfabeto de `TEMPLATE_RE` e de `PARAMETER_NAME`.
NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_NAO_SLUG = re.compile(r"[^a-z0-9_]+")
SLUG_MAX = 60
SUFIXO_USUARIO = "usuario"
SUFIXO_SENHA = "senha"


class DatumKind(StrEnum):
    text = "text"
    date = "date"
    secret = "secret"


@dataclass(frozen=True, slots=True)
class AvailableDatum:
    """Um dado que o plano pode usar. `sensitive` = só por `type_secret`, nunca como variável."""

    name: str
    label: str
    kind: DatumKind
    sensitive: bool
    #: `None` = dado do perfil; senão o app da conta de onde vem.
    app_id: str | None = None
    account_id: str | None = None
    host: str | None = None

    def prompt_line(self) -> str:
        """Uma linha para o modelo: o nome, o que é e como usar — sem valor nenhum."""
        if self.sensitive:
            return f"- {self.name}: {self.label} (SIGILOSO: só com type_secret(name=\"{self.name}\"); o valor nunca vem)"
        return f"- {{{self.name}}}: {self.label} ({self.kind.value})"


@dataclass(frozen=True, slots=True)
class AccountRecord:
    """Uma conta do perfil como a porta de dados a entrega: metadados, nunca o valor do segredo."""

    account_id: str
    app_id: str
    package: str | None
    app_label: str
    handle: str
    host: str | None
    login_identifier: str | None
    has_credential: bool
    credential_status: str | None
    consent_at: str | None
    secret_ref: str | None
    #: App com `SessionProvider` (o Instagram): o login é determinístico, fora do `type_secret`.
    managed: bool


@dataclass(frozen=True, slots=True)
class ResolvedSecret:
    """O que o canal sensível precisa para digitar: a referência do cofre e as travas (pacote e host da conta)."""

    name: str
    account_id: str
    app_id: str
    package: str | None
    host: str | None
    secret_ref: str
    consent_at: str
    label: str


@dataclass(frozen=True, slots=True)
class SecretResolution:
    """`secret` quando o nome resolve numa senha utilizável; senão `refusal` diz, para o modelo, por quê."""

    secret: ResolvedSecret | None = None
    refusal: str | None = None
    #: Existe a senha, mas a pessoa ainda não consentiu (`consent_at` nulo): a pendência é dela, não do modelo.
    pending_consent: bool = False


#: Colunas do perfil que viram variável, na ordem em que aparecem ao modelo: (nome, coluna, rótulo, tipo).
#: Lista FECHADA: campo novo entra aqui de propósito, nunca "todas as colunas". Gênero e idioma entraram no 31.87 F2
#: (decisão do dono de 05/10 15:13Z: os dados da persona entram no ensinado), sempre como texto simples. A biografia
#: da persona NÃO entra: é um JSON de seis seções, e que seção vira marcador é pergunta ao dono (P-012). Senha, código
#: e 2FA nunca são coluna do perfil: só pelo cofre (`type_secret`).
PROFILE_FIELDS: tuple[tuple[str, str, str, DatumKind], ...] = (
    ("perfil_nome", "first_name", "nome", DatumKind.text),
    ("perfil_sobrenome", "last_name", "sobrenome", DatumKind.text),
    ("perfil_nome_exibicao", "display_name", "nome de exibição", DatumKind.text),
    ("perfil_nascimento", "birth_date", "data de nascimento", DatumKind.date),
    ("perfil_email", "email", "e-mail", DatumKind.text),
    ("perfil_genero", "gender", "gênero", DatumKind.text),
    ("perfil_idioma", "locale", "idioma e região", DatumKind.text),
)


def slug(texto: str, limite: int = SLUG_MAX) -> str:
    """`configura-es-do-android` → `configura_es_do_android`; `portal.exemplo.gov.br` → `portal_exemplo_gov_br`."""
    s = _NAO_SLUG.sub("_", texto.strip().casefold()).strip("_")
    return s[:limite].strip("_") or "x"


def account_name(app_id: str, host: str | None, field: str) -> str:
    """`conta_<app>[_<host>]_<campo>`."""
    base = f"conta_{slug(app_id)}" + (f"_{slug(host)}" if host else "")
    return f"{base}_{field}"


def _sem_colisao(nome: str, usados: set[str]) -> str:
    """Dois hosts que viram o mesmo slug: o segundo recebe `_2`, o terceiro `_3`… e nenhum some em silêncio."""
    candidato, n = nome, 1
    while candidato in usados:
        n += 1
        candidato = f"{nome}_{n}"
    usados.add(candidato)
    return candidato


def _texto(valor: object) -> str | None:
    if valor is None:
        return None
    s = str(valor).strip()
    return s or None


def profile_data(fields: Mapping[str, object] | None) -> tuple[AvailableDatum, ...]:
    """Os dados não sigilosos do perfil QUE TÊM VALOR: o modelo não recebe nome de variável vazia."""
    if fields is None:
        return ()
    return tuple(AvailableDatum(name=nome, label=rotulo, kind=tipo, sensitive=False)
                 for nome, coluna, rotulo, tipo in PROFILE_FIELDS if _texto(fields.get(coluna)))


def _usuario_da_conta(c: AccountRecord) -> str | None:
    """O valor de `conta_<app>_usuario`: o NOME da conta no app (o @ do Instagram), que é o que o plano usa para
    abrir perfil, buscar e conferir. Item 29.71: com `login_identifier or handle`, o e-mail de login do Instagram
    vencia o @ e entrava no binding, no título e no objetivo da etapa (f85a37); pela busca, iria ao campo de busca.
    No app de login GERENCIADO o identificador de login nunca vira variável: quem entra é o provedor de sessão, que o
    lê da credencial. No app de login por formulário (sem provedor de sessão) a variável continua sendo o que o
    formulário pede, o identificador, e o handle só quando ele falta."""
    if c.managed:
        return _texto(c.handle)
    return _texto(c.login_identifier) or _texto(c.handle)


def _rotulo_da_conta(c: AccountRecord) -> str:
    return c.app_label + (f" ({c.host})" if c.host else "")


def account_data(accounts: Sequence[AccountRecord]) -> tuple[AvailableDatum, ...]:
    """Por conta: o usuário (não sigiloso, quando há um) e a senha (sigilosa) — esta só quando existe, tem
    consentimento e o app NÃO tem login gerenciado (ADR-040: o provedor de sessão entra sozinho; o `type_secret`
    nunca recebe a senha do Instagram)."""
    usados: set[str] = set()
    saida: list[AvailableDatum] = []
    for c in accounts:
        onde = _rotulo_da_conta(c)
        if _usuario_da_conta(c):
            nome = _sem_colisao(account_name(c.app_id, c.host, SUFIXO_USUARIO), usados)
            saida.append(AvailableDatum(name=nome, label=f"usuário da conta em {onde}", kind=DatumKind.text,
                                        sensitive=False, app_id=c.app_id, account_id=c.account_id, host=c.host))
        if c.has_credential and c.consent_at and not c.managed:
            nome = _sem_colisao(account_name(c.app_id, c.host, SUFIXO_SENHA), usados)
            saida.append(AvailableDatum(name=nome, label=f"senha da conta em {onde}", kind=DatumKind.secret,
                                        sensitive=True, app_id=c.app_id, account_id=c.account_id, host=c.host))
    return tuple(saida)


def available_data(fields: Mapping[str, object] | None, accounts: Sequence[AccountRecord]) -> tuple[AvailableDatum, ...]:
    return profile_data(fields) + account_data(accounts)


def profile_variables(fields: Mapping[str, object] | None, accounts: Sequence[AccountRecord]) -> dict[str, str]:
    """Os VALORES dos dados não sigilosos, para `Repository.materialize` resolver `{perfil_email}` por aparelho.
    Nenhum sigiloso entra aqui, por construção: só o que `available_data` marcou como não sigiloso."""
    valores: dict[str, str] = {}
    if fields is not None:
        for nome, coluna, _rotulo, _tipo in PROFILE_FIELDS:
            v = _texto(fields.get(coluna))
            if v:
                valores[nome] = v
    por_conta = {d.account_id: d for d in account_data(accounts) if not d.sensitive and d.account_id}
    for c in accounts:
        d = por_conta.get(c.account_id)
        v = _usuario_da_conta(c)
        if d is not None and v:
            valores[d.name] = v
    return valores


def common_names(listas: Sequence[Sequence[AvailableDatum]]) -> tuple[AvailableDatum, ...]:
    """Os dados presentes em TODOS os aparelhos da execução, na ordem do primeiro. Um aparelho sem perfil zera a
    lista: o plano é um só, e não pode contar com o que falta em algum lugar."""
    if not listas:
        return ()
    comuns = set.intersection(*({d.name for d in lista} for lista in listas))
    return tuple(d for d in listas[0] if d.name in comuns)


def resolve_secret(accounts: Sequence[AccountRecord], name: str) -> SecretResolution:
    """`type_secret(name)` → a senha de QUAL conta, ou por que não. As recusas são frases para o modelo (e para o
    histórico), sem valor nenhum."""
    usados: set[str] = set()
    for c in accounts:
        if _usuario_da_conta(c):
            _sem_colisao(account_name(c.app_id, c.host, SUFIXO_USUARIO), usados)
        if not c.has_credential:
            continue
        nome = account_name(c.app_id, c.host, SUFIXO_SENHA)
        if c.consent_at and not c.managed:
            nome = _sem_colisao(nome, usados)
        if nome != name:
            continue
        onde = _rotulo_da_conta(c)
        if c.managed:
            return SecretResolution(refusal=f"A conta em {onde} tem login gerenciado pelo sistema: o app entra sozinho "
                                            "antes da tarefa; não há o que digitar.")
        if not c.consent_at:
            return SecretResolution(refusal=f"A senha da conta em {onde} está guardada, mas a pessoa ainda não "
                                            "consentiu que a automação a digite; peça a ela para marcar o "
                                            "consentimento na conta.", pending_consent=True)
        if c.credential_status == "invalid":
            return SecretResolution(refusal=f"A senha da conta em {onde} foi recusada pela plataforma; a pessoa "
                                            "precisa cadastrá-la de novo.")
        assert c.secret_ref is not None
        return SecretResolution(secret=ResolvedSecret(name=name, account_id=c.account_id, app_id=c.app_id,
                                                      package=c.package, host=c.host, secret_ref=c.secret_ref,
                                                      consent_at=c.consent_at, label=onde))
    oferecidos = sorted(d.name for d in account_data(accounts) if d.sensitive)
    return SecretResolution(refusal=f"Credencial {name!r} não existe nas contas deste perfil; há: "
                                    f"{', '.join(oferecidos) or 'nenhuma'}.")


def typable_secret_for(accounts: Sequence[AccountRecord], package: str | None) -> SecretResolution:
    """Há senha digitável para o app deste pacote? É o `tem_credencial` de `pede_intervencao_humana`, por app e
    etapa: com ela a tela de senha é só mais uma tela; sem ela, é de pessoa. `pending_consent` diz ao aviso o que
    falta quando a senha existe mas não foi consentida."""
    if not package:
        return SecretResolution(refusal="sem app")
    pendente = None
    for c in accounts:
        if c.package != package or c.managed or not c.has_credential:
            continue
        if c.consent_at and c.credential_status != "invalid":
            return resolve_secret(accounts, account_name(c.app_id, c.host, SUFIXO_SENHA))
        if not c.consent_at:
            pendente = c
    if pendente is not None:
        return SecretResolution(refusal=f"a senha da conta em {_rotulo_da_conta(pendente)} está guardada sem "
                                        "consentimento", pending_consent=True)
    return SecretResolution(refusal="nenhuma senha de conta deste app")


def account_hosts(accounts: Sequence[AccountRecord]) -> frozenset[str]:
    """Os sites das contas de portal do perfil: onde `open_url` pode entrar além do que a pessoa escreveu."""
    return frozenset(c.host for c in accounts if c.host)


def missing_secrets(accounts: Sequence[AccountRecord], names: Sequence[str]) -> list[str]:
    """Os `requires.secrets` de uma skill que este perfil não tem como senha UTILIZÁVEL (guardada, consentida, não
    recusada, de app sem login gerenciado). Pré-voo: o que falta em algum aparelho recusa antes de planejar."""
    return [n for n in names if resolve_secret(accounts, n).secret is None]
