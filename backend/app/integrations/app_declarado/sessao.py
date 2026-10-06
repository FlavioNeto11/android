"""Sessão de um app declarado: login e conferência da conta, código determinístico, fora do laço da IA (ADR-052).

É o motor de sessão de TODO app com conta gerenciada. O que é de cada app — pacote, rótulo, sinais do botão de entrar
e do "agora não", o que dispensa uma tela benigna, a aba de perfil, a tabela de desfechos depois do envio, os textos
de ajuda e os ajustes padrão — vem do conhecimento declarado (`conhecimento.ConhecimentoDeSessao`, lido de
`app/conhecimento/apps/<pacote>/`). Este módulo não conhece app nenhum.

Por que fora do laço da IA: o executor para em qualquer tela com campo de senha, e o ator é instruído a nunca digitar
credencial. Este motor fala direto com o driver, pela fila exclusiva do aparelho, e a senha passa só pelo canal de
entrada sensível — que não gera argumento de ação nem histórico para o modelo (ADR-040: a credencial que a pessoa
guardou, com o consentimento dela, só no app daquela conta).

O que ele nunca faz: repetir o envio por causa de timeout, tentar de novo depois de senha comprovadamente errada,
seguir com a conta errada, ou tentar resolver um desafio de segurança (ADR-009/ADR-029: desafio, 2FA e CAPTCHA são
da pessoa). Conduta do login (ADR-055): conta bloqueada nunca recebe a senha; depois de UM envio sem sucesso o login
automático para até uma pessoa olhar; e há um teto de envios por conta em 24 h.

Ler a conta é o que separa "o app abriu" de "a conta certa está aberta": nenhuma ação social acontece sem isso
confirmado, e conta errada nunca continua em silêncio. Nunca se presume sucesso pelo retorno do Appium: observa-se de
novo e classifica-se pelo que está na tela.

Sessão por CONTA (item 23.4, ADR-057): cada chamada resolve, antes de tocar no aparelho, a conta que ela abre — a dita
(`account_id`, que tem de ser do perfil e deste app) ou a do perfil NESTE app — e tudo o que o motor lê e grava é
dessa conta: credencial, consentimento, tentativa, teto diário, marcação da credencial, sessão no aparelho. Antes tudo
caía na conta do app âncora do perfil, qualquer que fosse o app do provedor: o login do segundo app gravava a sessão e
a senha recusada na conta do primeiro. A conta lida na tela é comparada ao @ (`handle`) e ao identificador de login
DESSA conta, não ao @ de cadastro do perfil.

Login em ETAPAS e conta fora da barra inferior (item 23.6, ADR-057), declarados no `sessao.yaml`: o identificador numa
tela com "avançar" e a senha na seguinte — que só recebe a senha mostrando ESTE identificador (o app que lembrou outra
conta não recebe a senha desta); a recusa do identificador para o login sem julgar a senha. A conta pode ser aberta
por um elemento declarado (avatar, menu), lida com um valor só e, no app cuja tela inicial não a mostra, logo depois do
envio. A Custom Tab do navegador declarado é tela do app só num site de login que o app declara (`navegador.hosts`):
fora dele a pessoa assume, o desafio continua visto, e a senha — conferida no instante de digitar, na mesma árvore do
campo — nunca cai lá. Conta com `host` (de portal, no navegador) não tem login gerenciado.

Item 23.8, também declarado: a tela do app deslogado com o botão que abre a do identificador (`entrada`) e, entre o
"avançar" e a senha, a tela que propõe um código e oferece a senha (`alternativas`) — escolher a senha é o método de
entrada do titular, não resolver desafio; sem a oferta na tela, a mesma tela vai para a pessoa.

Telas aprendidas (ADR-054, fatia 5): cada chamada usa o conhecimento UNIDO (`conhecimento.com_as_aprendidas`) — a tela
de casa que mudou numa atualização do app, aprendida com a aba de perfil declarada, entra no estado conhecido, e a
conferência para nela em vez de voltar para fora do app. A conta continua lida só pela tela de perfil DECLARADA. E cada
conferência avisa um observador tipado (`definir_observador_da_sessao`; o padrão é nenhum) do que viu: a tela
aprendida em que parou, o desfecho e, quando o voltar saiu do app sem resolver, a tela desconhecida de antes.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from ...automation import conhecimento_de_telas as telas
from ...automation.conhecimento_de_telas import TelaReconhecida
from ...automation.driver import DriverError
from ...automation.hierarchy import SUBTIPO_CONTA_TRAVADA, ContaTravada, UiElement, UiTree
from ...devices.adb import AVISO_DE_ANR, motivo_de_anr
from ...devices.installer import LAUNCH_POLL_S, wait_for_focus
from ...models import SessionStatus
from ...modules.identity.application.session_rules import (CREDENCIAL_EM_REVISAO, aplicar_desafio, conta_para_conferir,
                                                           emit_needs_person_change, motivo_do_login_parado)
from ...security.sensitive_input import SensitiveInputError, SensitiveInputUnavailable
from ...shared.vinculos import tem_vinculo_ativo
from ...util import now, now_iso, parse_iso
from . import formulario as geometria
from .conhecimento import CONFERIR_CONTA, Ajustes, ConhecimentoDeSessao, com_as_aprendidas
from .formulario import FormularioDoUsuario, LoginForm

if TYPE_CHECKING:  # pragma: no cover - só para o verificador de tipos
    from ...config import Config
    from ...db import Row
    from ...devices.manager import DeviceManager, DeviceRuntime
    from ...events import EventBus
    from ...security.secret_store import SecretStore
    from ...security.sensitive_input import SensitiveInputChannel
    from ...social.repository import SocialRepository

# `poc.conta`, e não `poc.sessao`: esse é o logger da sessão do PAINEL (`security/sessions.py`, login do dono).
log = logging.getLogger("poc.conta")

AUTOMATION_TRIES = 3
AUTOMATION_WAIT_S = 8.0
FILL_TRIES = 3                # tentativas de pôr o usuário no campo; só se envia com o valor conferido
#: Intervalo entre as leituras depois do envio. Só observa; nunca reenvia.
OBSERVAR_DEPOIS_DO_ENVIO_S = 2.0
#: Tipos de tela (vocabulário de `automation/conhecimento_de_telas.py`) que só uma pessoa resolve.
TIPOS_DE_DESAFIO = frozenset({"desafio", "dois_fatores"})
#: Etapas do login em ETAPAS (item 23.6) em que a tentativa termina antes da senha: o desafio visto depois do
#: "avançar", o navegador fora do site da conta, o identificador recusado, a tela da senha que não mostra esta conta
#: e a tela da senha que não chegou. Todas param o login automático até uma pessoa olhar (o "avançar" já é efeito no
#: servidor) e nenhuma gasta o teto diário (a senha não saiu).
ETAPA_DESAFIO_ANTES_DA_SENHA = "challenge_before_password"
ETAPA_NAVEGADOR_FORA_DA_CONTA = "browser_outside_account"
ETAPA_IDENTIFICADOR_RECUSADO = "identifier_rejected"
ETAPA_CONTA_NAO_MOSTRADA = "identity_not_shown"
ETAPA_SEM_TELA_DA_SENHA = "password_step_missing"
#: Etapas de uma tentativa em que a senha AINDA NÃO saiu da máquina (o envio é gravado como `submitting` antes do
#: toque em Entrar). Todo o resto — inclusive uma tentativa que parou em `submitting` sem desfecho — conta como envio
#: no teto diário: na dúvida, conta.
ETAPAS_ANTES_DO_ENVIO = frozenset({"started", "form_found", "username_mismatch", "sensitive_channel_blocked",
                                   "fill_failed", "submit_not_delivered", "account_blocked", "login_parado",
                                   ETAPA_DESAFIO_ANTES_DA_SENHA, ETAPA_NAVEGADOR_FORA_DA_CONTA,
                                   ETAPA_IDENTIFICADOR_RECUSADO, ETAPA_CONTA_NAO_MOSTRADA, ETAPA_SEM_TELA_DA_SENHA})
#: Janela do teto diário de logins por conta (`max_logins_per_day`).
JANELA_DO_TETO_DIARIO = timedelta(hours=24)
#: Começo do aviso que ESTE motor põe no cartão do aparelho quando o app não fica na frente. É por ele que o motor
#: reconhece o aviso como seu: só ocupa o cartão vazio ou o que já é dele, e só tira o que é dele.
AVISO_FORA_DA_FRENTE = "App fora do primeiro plano"


class Outcome(StrEnum):
    SESSION_READY = "session_ready"            # conta certa aberta
    INVALID_CREDENTIAL = "invalid_credential"  # a tela disse que a senha está errada
    AUTH_CHALLENGE = "auth_challenge"          # 2FA, captcha, confirmação: só uma pessoa resolve
    WRONG_ACCOUNT = "wrong_account"            # abriu, mas é outra conta
    RETRYABLE = "retryable"                    # falhou antes de qualquer efeito; pode tentar de novo
    UNCERTAIN = "uncertain"                    # não deu para saber; NÃO repete sozinho

    @property
    def terminal(self) -> bool:
        """Desfecho que nunca gera nova tentativa automática — é o que evita bloquear a conta."""
        return self in (Outcome.INVALID_CREDENTIAL, Outcome.AUTH_CHALLENGE, Outcome.WRONG_ACCOUNT)


@dataclass(slots=True)
class Verdict:
    """O que a tela disse depois do envio. `etapa` é o nome gravado na tentativa (a fase local filtra por ele)."""

    outcome: Outcome
    detail: str
    observed_username: str | None = None
    tela: str = telas.DESCONHECIDA
    etapa: str = "classified"
    trava: ContaTravada | None = None    #: desafio ou código depois do envio: subtipo e trecho (ADR-055)
    #: Uma tela de casa em que a conta não está à vista: quem confirma é a leitura pelo acesso declarado (23.6).
    conferir: bool = False


@dataclass(slots=True)
class AuthResult:
    outcome: Outcome
    detail: str
    observed_username: str | None = None
    session_status: SessionStatus = SessionStatus.unknown
    attempted_login: bool = False

    @property
    def ready(self) -> bool:
        return self.outcome is Outcome.SESSION_READY


@dataclass(slots=True)
class AccountCheck:
    observed: str | None
    matches: bool
    detail: str
    outro_app: bool = False          # a leitura terminou com OUTRO app na frente (o nosso caiu ou não voltou)
    trava: ContaTravada | None = None  # a leitura parou numa tela de verificação, sem tocar nela (ADR-055)


def normalizar_conta(valor: str | None) -> str:
    """A conta como se compara: sem espaço, sem o '@' do começo e em minúsculas. O '@' do meio fica (e-mail)."""
    return (valor or "").strip().lstrip("@").strip().lower()


def _como_conta(valor: str) -> str:
    """A conta na mensagem: `@usuario`; um e-mail vai como está (`@ana@exemplo.com` não diz nada a ninguém)."""
    limpo = valor.strip().lstrip("@").strip()
    return limpo if "@" in limpo else f"@{limpo}"


@dataclass(frozen=True, slots=True)
class ContaDaSessao:
    """A conta que UMA chamada de `ensure_session` abre e confere (item 23.4). Resolvida antes de tocar no aparelho,
    e é a casa de tudo o que o motor lê e grava na chamada.

    `handle` é a conta como a tela deve mostrá-la (o @ da conta do app; na falta dele, o identificador de login);
    `aceitos` são os identificadores DESTA conta, normalizados, que confirmam a leitura — o @ e o login (um app que
    mostra o e-mail no cabeçalho confere pelo login). `ancora` = a conta do app que provê a conta do perfil, a única
    cujo "verificado" o cartão do perfil mostra. É sempre a conta do app inteiro (`profile_accounts.host` nulo): a
    conta de portal, com `host`, é acessada pelo navegador e não tem login gerenciado (`_resolver_conta`)."""

    profile_id: str
    id: str
    app_id: str
    handle: str
    aceitos: frozenset[str]
    ancora: bool


@dataclass(frozen=True, slots=True)
class Destino:
    """Onde a tela está, para o login (item 23.6): o app da conta, outro app, ou o navegador declarado para a Custom
    Tab do login, num site. `permitido` = o app da conta, ou o navegador num site de login declarado pelo app — o único
    lugar em que o motor segue o login e em que a senha pode ser digitada."""

    pacote: str | None
    host: str | None = None          # só no navegador declarado: o site da barra de endereço ('' = não lido)
    permitido: bool = True

    @property
    def navegador(self) -> bool:
        return self.host is not None


@dataclass(slots=True)
class _Aberturas:
    """Mortes por ANR já vistas numa chamada de `ensure_session`. Por chamada, e não no provedor: `SessaoDeclarada` é
    uma só por app, para todos os aparelhos."""
    inicio: float = field(default_factory=time.monotonic)
    mortes: set[tuple[str, int]] = field(default_factory=set)


class AppParouDeResponder(RuntimeError):
    """A 2ª morte por ANR na mesma chamada: a reabertura já foi gasta (pacote "anr")."""


# ---------------------------------------------------------------------------------------------------- aprendizado
@dataclass(frozen=True, slots=True)
class ConferenciaDaSessao:
    """O que UMA conferência da conta viu, para o aprendizado (ADR-054, fatia 5). Nenhum texto: a árvore da tela
    desconhecida vai ao observador, que tira dela só os ids e pula a tela protegida."""

    pacote: str
    instance_id: str
    profile_id: str
    desfecho: str                    # `Outcome.value`
    tipo_da_tela: str                # o tipo da tela em que a conferência decidiu (depois de voltar ao conhecido)
    tela_aprendida: str | None       # a tela aprendida em que ela parou, quando parou numa
    desconhecida: UiTree | None      # a tela do app não reconhecida de onde o voltar saiu, quando nada a resolveu
    tentou_login: bool


class ObservadorDaSessao(Protocol):
    def ao_conferir(self, conferencia: ConferenciaDaSessao) -> None: ...


_OBSERVADOR: list[ObservadorDaSessao] = []


def definir_observador_da_sessao(observador: ObservadorDaSessao | None) -> None:
    """Liga (ou, com `None`, desliga) quem ouve as conferências. Um só por processo: a composição do aprendizado."""
    _OBSERVADOR.clear()
    if observador is not None:
        _OBSERVADOR.append(observador)


@dataclass(slots=True)
class _Visto:
    """O que a chamada viu, anotado no caminho para um aviso só no fim."""

    observou: bool = False           # a tela do aparelho foi lida nesta chamada (sem isso, nada a avisar)
    tipo: str = telas.DESCONHECIDA
    aprendida: str | None = None
    desconhecida: UiTree | None = None
    sem_resolver: bool = False

    def anotar(self, tree: UiTree, r: TelaReconhecida) -> TelaReconhecida:
        self.observou = True
        if r.tela == telas.DESCONHECIDA and not r.outro_app:
            self.desconhecida = tree
        return r


def _aprendida(k: ConhecimentoDeSessao, tela: str) -> bool:
    regra = k.telas.regra(tela)
    return regra is not None and regra.aprendida


Observar = Callable[[], Awaitable[tuple[UiTree, str | None]]]
Tocar = Callable[[int, int], Awaitable[None]]
Reconhecer = Callable[[UiTree, str | None], TelaReconhecida]


def _nome_da_tela(r: TelaReconhecida) -> str:
    """O nome da tela no diagnóstico. A não reconhecida sai como `unknown`, a grafia que o detalhe das tentativas e o
    log sempre gravaram (antes do motor genérico o nome vinha de um enum do app, e a fase local lê esse histórico)."""
    return "unknown" if r.tela == telas.DESCONHECIDA else r.tela


def _texto_da_tela(tree: UiTree) -> str:
    return "\n".join(f"{e.text} {e.desc}".strip() for e in tree.elements if e.text or e.desc)


def _fora_do_site(destino: Destino) -> str:
    return (f"o login abriu no navegador ({destino.host or 'site não identificado'}), fora dos sites declarados para "
            "o login do app")


def _dados_da_trava(profile_id: str, trava: ContaTravada | None) -> dict[str, str | None]:
    """O tipo e o trecho da tela de verificação no evento (ADR-055): antes eram calculados e descartados."""
    return {"profile_id": profile_id, "subtipo": trava.subtipo if trava is not None else None,
            "trecho": trava.trecho if trava is not None else None, "tela": trava.origem if trava is not None else None}


# ---------------------------------------------------------------------------------------------------- desfechos
def classificar_depois_do_envio(k: ConhecimentoDeSessao, tree: UiTree, *, package: str | None,
                                expected_username: str, locale: str | None = None,
                                aceitos: Iterable[str] = ()) -> Verdict:
    """Lê a tela depois do envio e decide pela tabela declarada (`depois_do_envio`): a primeira regra que casar vence.

    Nunca se repete o envio por causa de timeout — só uma falha COMPROVADA antes de qualquer efeito autoriza nova
    tentativa, e essa não passa por aqui. `expected_username` é comparado sem diferenciar maiúsculas nem o '@' do
    começo; `aceitos` são os outros identificadores da MESMA conta (o login) que também a confirmam.
    """
    sig = k.telas.sinais_de(locale)
    texto = _texto_da_tela(tree)
    r = k.reconhecer(tree, package=package, locale=locale)
    for regra in k.depois_do_envio:
        if regra.sinal is not None:
            casou = bool(sig[regra.sinal].search(texto))
        elif regra.tipos:
            casou = r.tipo in regra.tipos
        else:
            casou = r.tela in regra.telas
        if not casou:
            continue
        if regra.desfecho == CONFERIR_CONTA:
            return _conferir_conta_depois_do_envio(k, tree, r, expected_username, aceitos)
        detalhe = f"{regra.detalhe} (tela: {_nome_da_tela(r)})" if regra.anexar_tela else regra.detalhe
        return Verdict(Outcome(regra.desfecho), detalhe, tela=r.tela, etapa=regra.etapa, trava=r.trava)
    return Verdict(Outcome.UNCERTAIN, f"não foi possível classificar a tela ({r.razao})", tela=r.tela)


def _confere(observado: str, esperado: str, aceitos: Iterable[str]) -> bool:
    o = normalizar_conta(observado)
    return bool(o) and (o == normalizar_conta(esperado) or o in {normalizar_conta(a) for a in aceitos})


def _conferir_conta_depois_do_envio(k: ConhecimentoDeSessao, tree: UiTree, r: TelaReconhecida,
                                    esperado: str, aceitos: Iterable[str] = ()) -> Verdict:
    observado = k.conta_observada(tree)
    if observado and not _confere(observado, esperado, aceitos):
        return Verdict(Outcome.WRONG_ACCOUNT,
                       f"a conta aberta é {_como_conta(observado)}, e a esperada é {_como_conta(esperado)}",
                       observed_username=observado, tela=r.tela)
    if observado:
        return Verdict(Outcome.SESSION_READY, f"{_como_conta(observado)} confirmado na tela",
                       observed_username=observado, tela=r.tela)
    # Entrou no app mas a conta ainda não apareceu: quem confirma é a leitura pela aba de perfil, não este palpite.
    return Verdict(Outcome.UNCERTAIN, "o app abriu, mas a conta ainda não foi confirmada na tela", tela=r.tela,
                   conferir=True)


# ---------------------------------------------------------------------------------------------------- conta aberta
async def ler_conta(k: ConhecimentoDeSessao, observe: Observar, tap: Tocar, *, expected: str,
                    locale: str | None, aceitos: Iterable[str] = (),
                    reconhecer: Reconhecer | None = None,
                    voltar: Callable[[], Awaitable[None]] | None = None) -> AccountCheck:
    """Tenta ler a conta; abre a aba de perfil declarada (ou o acesso à conta fora da barra, item 23.6) e lê de lá.

    `observe` devolve `(UiTree, package)`; `tap` recebe (x, y). Nada aqui digita nem toca em nada além de dispensas
    de recusa e da aba de perfil (ou do acesso), que é navegação sem efeito externo. `expected` e `aceitos` são da
    conta do app que esta chamada abre (o @ e o login dela), nunca o @ de cadastro do perfil. `reconhecer` é o do
    provedor (`SessaoDeclarada._reconhecer`): a Custom Tab no site da conta é tela do app, e o desafio dentro dela é
    visto; o padrão é o conhecimento puro. `voltar` aperta a tecla Voltar: é como sai o diálogo de outro pacote que o
    app declara em `dispensa.voltar` (item 23.8); sem ele, esse diálogo não é dispensado aqui.
    """
    def ver(t: UiTree, p: str | None) -> TelaReconhecida:
        return reconhecer(t, p) if reconhecer is not None else k.reconhecer(t, package=p, locale=locale)

    # Depois de entrar, o app pode empilhar telas na frente: "Salvar dados de login?", dicas e passos de onboarding.
    # Nenhuma delas tem barra de perfil, então não adianta procurar a conta ali. O laço abre caminho: dispensa o que
    # estiver na frente, vai até a aba de perfil e só então lê.
    #
    # A identidade sai SÓ da tela de perfil declarada: numa tela de conteúdo, o mesmo campo de cabeçalho pode mostrar
    # o autor do que está em foco, e ler dali acusava "conta errada" na conta certa (caso real no `sessao.yaml` do
    # primeiro app).
    #
    # Antes de qualquer toque, a tela passa pelo detector de conta travada (ADR-055): a verificação que aparece no
    # meio da leitura — ao abrir a aba de perfil, por exemplo — tem botões de recusa ("Not now"), e o laço os
    # dispensava e seguia tocando. Quem ouve "trava" sai sem tocar em nada.
    tree: UiTree | None = None
    package: str | None = None
    espera = k.conta.espera_s
    for _ in range(k.conta.passos_max):
        tree, package = await observe()
        agora = ver(tree, package)
        if agora.trava is not None:
            return _travada(agora.trava)
        # As dispensas declaradas pelo app (item 23.8) vêm antes: o botão de UMA tela intermediária ("OK" de um
        # aviso) e o Voltar num diálogo de outro pacote (o sistema oferecendo uma chave de acesso).
        declarada = k.dispensa_declarada(tree, agora, package, locale)
        if declarada is not None and (declarada.botao is not None or voltar is not None):
            await (tap(*declarada.botao.center) if declarada.botao is not None else voltar())  # type: ignore[misc]
            await asyncio.sleep(espera)
            continue
        if agora.outro_app and k.barra_de_endereco(package) is not None:
            break               # o navegador declarado fora do site da conta: nem a recusa se toca numa página alheia
        dispensar = k.botao_de_nao_salvar_login(tree, locale) or k.botao_de_dispensa(tree)
        if dispensar is not None:
            await tap(*dispensar.center)
            await asyncio.sleep(espera)
            continue

        # A conta só é lida DEPOIS de tocar na NOSSA aba de perfil. Ler o perfil em que se caiu não serve: o perfil
        # de outra pessoa tem o mesmo cabeçalho, e foi assim que @vinijr virou "conta errada" no aparelho de outra conta nossa.
        alvo = k.aba_de_perfil(tree)
        if alvo is None:
            break
        await tap(*alvo)
        await asyncio.sleep(espera)
        tree, package = await observe()
        reconhecida = ver(tree, package)
        if reconhecida.trava is not None:
            return _travada(reconhecida.trava)
        if reconhecida.tela == k.conta.tela_de_perfil:
            achado = k.conta_no_cabecalho(tree)
            if achado:
                return _check(achado, expected, aceitos)

    motivo, outro_app = "tela desconhecida", False
    if tree is not None:
        reconhecida = ver(tree, package)
        motivo, outro_app = reconhecida.razao, reconhecida.outro_app
    return AccountCheck(None, False, f"a conta não pôde ser lida na tela ({motivo})", outro_app)


def _travada(trava: ContaTravada) -> AccountCheck:
    return AccountCheck(None, False, f"a leitura da conta parou numa tela de verificação ({trava.descrever()})",
                        trava=trava)


def _check(observed: str, expected: str, aceitos: Iterable[str] = ()) -> AccountCheck:
    ok = _confere(observed, expected, aceitos)
    return AccountCheck(observed, ok,
                        f"{_como_conta(observed)} confirmado na tela" if ok
                        else f"a conta aberta é {_como_conta(observed)}, e a esperada é {_como_conta(expected)}")


# ---------------------------------------------------------------------------------------------------- o provedor
class SessaoDeclarada:
    """`SessionProvider` de um app declarado: garante a conta do perfil aberta no aparelho, pelo conhecimento dele."""

    def __init__(self, conhecimento: ConhecimentoDeSessao, cfg: Config | None, devices: DeviceManager,
                 repo: SocialRepository, secrets: SecretStore, sensitive: SensitiveInputChannel, bus: EventBus):
        self.conhecimento = conhecimento
        self.cfg = cfg
        self.devices = devices
        self.repo = repo
        self.secrets = secrets
        self.sensitive = sensitive
        self.bus = bus
        self.focus_poll_s = LAUNCH_POLL_S          # intervalo entre leituras de foco enquanto o app abre

    @property
    def package(self) -> str:
        """O pacote que este provedor abre e confere (`SessionProvider.package`)."""
        return self.conhecimento.app

    @property
    def ajustes(self) -> Ajustes:
        """Os padrões do app com o que a instalação sobrescreveu (`Config.ajustes_de_sessao`). Lidos a CADA uso, não
        guardados na construção: a configuração é a mesma instância que o resto do backend lê e ajusta."""
        if self.cfg is None:
            return self.conhecimento.ajustes
        return self.conhecimento.ajustes.com(self.cfg.ajustes_de_sessao(self.package))

    # ------------------------------------------------------------------ onde a tela está (item 23.6)
    @staticmethod
    def _destino(k: ConhecimentoDeSessao, tree: UiTree, package: str | None) -> Destino:
        """O app da conta, outro app, ou o navegador declarado num site. O site vale só se estiver entre os de login
        que o app declara (`navegador.hosts`, ou um subdomínio deles). Barra de endereço fora da tela = site não
        confirmado = não permitido. O `host` de uma conta NÃO soma aqui: conta com `host` é de portal no navegador, e
        o login gerenciado a recusa (`_resolver_conta`)."""
        if not package or package == k.app:
            return Destino(package)
        sufixo = k.barra_de_endereco(package)
        if sufixo is None or k.navegador is None:
            return Destino(package, permitido=False)
        host = geometria.host_da_barra(tree, pacote=package, sufixo=sufixo)
        return Destino(package, host, permitido=geometria.host_permitido(host, k.navegador.hosts))

    def _reconhecer(self, k: ConhecimentoDeSessao, tree: UiTree, package: str | None,
                    locale: str | None) -> TelaReconhecida:
        """A tela pelo conhecimento; na Custom Tab do navegador declarado, num site de login do app, como tela DO APP
        (é o login dele). Fora do site, a tela é "outro app" — mas o desafio aparece mesmo assim: `classificar` devolve
        outro app antes do detector, e um desafio dentro da Custom Tab passaria sem ser visto."""
        destino = self._destino(k, tree, package)
        if not destino.navegador:
            return k.reconhecer(tree, package=package, locale=locale)
        como_app = k.reconhecer(tree, package=k.app, locale=locale)
        if destino.permitido or como_app.trava is not None:
            return como_app
        return TelaReconhecida(telas.DESCONHECIDA, telas.DESCONHECIDA, _fora_do_site(destino), outro_app=True)

    def _no_navegador_fora_do_site(self, k: ConhecimentoDeSessao, tree: UiTree, package: str | None,
                                   estado: TelaReconhecida) -> Destino | None:
        """O navegador declarado na frente, num site que não é de login do app, sem desafio na tela: o destino, para a
        entrega à pessoa. `None` em qualquer outro caso (o desafio segue o caminho do desafio)."""
        if estado.trava is not None:
            return None
        destino = self._destino(k, tree, package)
        return destino if destino.navegador and not destino.permitido else None

    # ------------------------------------------------------------------ entrada principal
    async def ensure_session(self, rt: DeviceRuntime, profile_id: str, *, account_id: str | None = None,
                             force_login: bool = False, automatic: bool = False,
                             observe_only: bool = False) -> AuthResult:
        """Garante que a conta do perfil está aberta neste aparelho. Reaproveita sessão sempre que possível.

        `automatic=True` é a chamada do agendador: nela, estado que depende de pessoa (desafio de segurança, conta
        errada) nem chega a tocar no aparelho. A chamada explícita do portal sempre reobserva, que é como o usuário
        retoma depois de resolver o desafio à mão.

        `observe_only=True` é "Verificar conta": lê a tela e nada mais. Num aparelho deslogado ele PARA na tela de
        login em vez de autenticar — antes, quem apertava "Verificar" gastava uma tentativa de login real sem saber.

        `account_id` (contrato C1, item 23.4, ADR-057) é a conta que esta chamada abre: tem de ser do perfil e deste
        app, senão a chamada recusa sem tocar no aparelho. `None` é a conta do perfil NESTE app — no provedor do app
        âncora, a conta âncora, como sempre foi; em qualquer outro, a conta daquele app, e sem ela a chamada recusa.
        Nunca cai na conta de outro app: era assim que o login do segundo app gravava na conta do primeiro.
        """
        visto = _Visto()
        resultado = await self._garantir(rt, profile_id, visto, account_id=account_id, force_login=force_login,
                                         automatic=automatic, observe_only=observe_only)
        self._avisar_o_aprendizado(rt, profile_id, visto, resultado)
        return resultado

    def _resolver_conta(self, profile_id: str, account_id: str | None) -> ContaDaSessao | AuthResult:
        """A conta desta chamada, ou a recusa (sem gravar nada e sem tocar no aparelho) quando não há uma que sirva.

        A conta de outro app nunca é aceita: o provedor abre e confere o SEU pacote, e gravar a sessão dele noutra
        conta afirmaria um login que ninguém viu."""
        perfil = self.repo.profile_row(profile_id)
        if perfil is None:
            return AuthResult(Outcome.UNCERTAIN, "perfil não encontrado")
        rotulo = self.conhecimento.rotulo
        if account_id is not None:
            linha = self.repo.account_row(profile_id, account_id)
            if linha is None:
                return AuthResult(Outcome.UNCERTAIN, f"a conta {account_id} não é desta persona")
            pacote = self.repo.pacote_da_conta(profile_id, account_id)
            if pacote != self.package:
                return AuthResult(Outcome.UNCERTAIN, f"a conta {account_id} é de outro app ({pacote}); este login é "
                                                     f"o do {rotulo}")
            if str(linha["host"] or "").strip():
                # Conta de PORTAL (um site, pelo navegador): não é a que o login gerenciado abre. A porta de sessão e o
                # despacho nunca a acham (`conta_do_pacote` é a do app inteiro); aceitá-la só aqui abriria pelo
                # "Conectar" um login que nenhum outro caminho reconhece, e somaria o site dela aos da Custom Tab.
                return AuthResult(Outcome.UNCERTAIN, f"a conta {account_id} é de site ({linha['host']}), usada pelo "
                                                     f"navegador; o login gerenciado do {rotulo} é o da conta do app, "
                                                     "sem site")
        else:
            linha = self.repo.conta_do_pacote(profile_id, self.package, criar=True)
            if linha is None:
                # Terminal, como "não há credencial": só uma pessoa cadastrando a conta resolve; insistir não muda
                # nada.
                return AuthResult(Outcome.INVALID_CREDENTIAL, f"esta persona não tem conta no {rotulo}; cadastre a "
                                                              "conta na tela da persona antes de conectar",
                                  session_status=SessionStatus.auth_required)
        ancora = self.repo.eh_pacote_ancora(profile_id, self.package)
        cred = self.repo.account_credential_row(profile_id, linha["id"])
        login = str(cred["login_identifier"] or "").strip() if cred is not None else ""
        # O @ da conta (o de cadastro na âncora antiga, criada sem @), senão o login: a mesma regra da quarentena.
        principal = conta_para_conferir(handle=linha["handle"], login_identifier=login, username=perfil["username"],
                                        ancora=ancora)
        if not normalizar_conta(principal):
            # Sem um identificador, a leitura da tela não teria com o que comparar — e "qualquer conta serve" é
            # exatamente o que nunca pode acontecer.
            return AuthResult(Outcome.UNCERTAIN, f"a conta do {rotulo} desta persona não tem @ nem identificador de "
                                                 "login para conferir na tela; preencha-os na conta")
        return ContaDaSessao(profile_id=profile_id, id=str(linha["id"]), app_id=str(linha["app_id"]), handle=principal,
                             aceitos=frozenset(n for n in (normalizar_conta(principal), normalizar_conta(login)) if n),
                             ancora=ancora)

    def _avisar_o_aprendizado(self, rt: DeviceRuntime, profile_id: str, visto: _Visto, resultado: AuthResult) -> None:
        """Um aviso por chamada, e só quando a tela foi lida (a recusa sem tocar no aparelho não é observação)."""
        if not _OBSERVADOR or not visto.observou:
            return
        conferencia = ConferenciaDaSessao(
            pacote=self.package, instance_id=rt.id, profile_id=profile_id, desfecho=resultado.outcome.value,
            tipo_da_tela=visto.tipo, tela_aprendida=visto.aprendida,
            desconhecida=visto.desconhecida if visto.sem_resolver else None, tentou_login=resultado.attempted_login)
        for observador in tuple(_OBSERVADOR):
            try:
                observador.ao_conferir(conferencia)
            except Exception:  # noqa: BLE001 - aprendizado é registro: nunca muda o desfecho da sessão
                log.exception("%s: o aprendizado não ouviu a conferência da conta (a sessão seguiu)", rt.id)

    async def _garantir(self, rt: DeviceRuntime, profile_id: str, visto: _Visto, *, account_id: str | None,
                        force_login: bool, automatic: bool, observe_only: bool) -> AuthResult:
        # O conhecimento UNIDO desta chamada: as telas aprendidas publicadas entram depois das declaradas (ADR-054).
        k = com_as_aprendidas(self.conhecimento)
        conta = self._resolver_conta(profile_id, account_id)
        if isinstance(conta, AuthResult):
            return conta

        # ADR-055: conta bloqueada (ou pausada pelo dono) nunca recebe a senha — nem pelo agendador, nem pelo
        # "Conectar". Recusa SEM gravar a sessão: gravar `auth_required` por cima de um desafio tirava a conta da fila
        # "Aguardando intervenção" e apagava o que a pessoa precisa ver. Só a leitura (`observe_only`) segue: não
        # digita nada, e é como a pessoa confirma que resolveu a tela (a reativação do perfil continua dela).
        if not observe_only and (parada := self._conta_parada(conta)) is not None:
            return self._recusa_sem_tocar(conta, rt.id, parada)
        if automatic and (parado := self._needs_person(conta, rt.id)):
            return AuthResult(parado[0], parado[1], session_status=self._status_for(parado[0]))
        bloqueio = self._blocked_reason(conta)
        if bloqueio:
            self._save(conta, rt.id, SessionStatus.auth_required, detail=bloqueio)
            return AuthResult(Outcome.INVALID_CREDENTIAL, bloqueio, session_status=SessionStatus.auth_required)
        if automatic and not observe_only and self._login_em_revisao(conta):
            # Um envio de senha já saiu sem sucesso: o agendador não tenta de novo, nem depois de intervalo nenhum, e
            # nem toca no aparelho. Quem pode tentar é uma pessoa, pelo "Conectar" (chamada não automática).
            return self._recusa_sem_tocar(conta, rt.id, motivo_do_login_parado(self.conhecimento.rotulo))

        if not await self._ensure_automation(rt):
            return AuthResult(Outcome.RETRYABLE, "a sessão de automação do aparelho não ficou pronta")

        locale = await self._locale(rt)
        aberturas = _Aberturas()
        try:
            await self._open_app(rt, aberturas)
        except AppParouDeResponder as exc:
            return self._parou_de_responder(rt, str(exc))
        tree, package = await self._observe(rt)
        # Pelo `_reconhecer` (item 23.6): a Custom Tab do login num site declarado para a conta é tela do app.
        estado = visto.anotar(tree, self._reconhecer(k, tree, package, locale))

        # Depois de entrar, o app pode intercalar dicas e passos de onboarding que o tapam. São benignas e o botão
        # usado (só de RECUSA, pelo dado) não concede nada — mas enquanto estiverem na frente, a tela não é
        # classificável e a conta não tem como ser lida. Dispensa no máximo algumas, para não virar laço.
        # Só tela DESCONHECIDA é dispensada: a de verificação da conta (ADR-055) é reconhecida pelo detector antes de
        # qualquer regra — em qualquer idioma, com o apóstrofo tipográfico — e sai deste laço sem toque nenhum.
        # O navegador declarado para o login na frente, fora dos sites da conta (item 23.6), também sai sem toque: o
        # app deixou uma Custom Tab aberta num site que ninguém declarou, e nem a recusa se toca numa página alheia.
        fora = self._no_navegador_fora_do_site(k, tree, package, estado)
        for _ in range(k.dispensa.intersticiais_max):
            if fora is not None:
                break
            # A dispensa DECLARADA (item 23.8) vale também aqui: o app reaberto no meio do primeiro uso, ou com o
            # diálogo do sistema por cima (outro pacote, que o reconhecimento só chama de "outro app").
            declarada = k.dispensa_declarada(tree, estado, package, locale)
            if declarada is not None and declarada.voltar:
                await self._voltar(rt)
            else:
                if declarada is None and estado.tela != telas.DESCONHECIDA:
                    break
                botao = declarada.botao if declarada is not None else k.botao_de_dispensa(tree)
                if botao is None:
                    break
                await self._tap(rt, *botao.center)
            await asyncio.sleep(float(self.ajustes.settle_s))
            tree, package = await self._observe(rt)
            estado = visto.anotar(tree, self._reconhecer(k, tree, package, locale))
            fora = self._no_navegador_fora_do_site(k, tree, package, estado)

        # Voltar da Custom Tab ou reabrir o app não é o caminho — o login está lá —, e digitar ali é o que nunca
        # acontece. A pessoa assume (o desafio já saiu como tela do app, pelo `_reconhecer`).
        if fora is not None:
            return self._navegador_fora_da_conta(rt, conta, fora, parar=not observe_only)

        # Fora do estado conhecido (conversa aberta, post, comentários, busca) ou numa tela desconhecida: volta ao
        # estado que o conhecimento declara ANTES de concluir qualquer coisa. Execução e31953: o app retomou uma
        # conversa do perfil, a tela não casava com nenhum sinal e a checagem chamou uma pessoa em 1 minuto — a
        # conta estava logada o tempo todo. Voltar e reabrir o app não têm efeito externo. Intersticial não entra:
        # tem tratamento próprio (a leitura da conta o dispensa).
        passos: list[str] = []
        if estado.tela == telas.DESCONHECIDA or (k.telas.autenticada(estado.tela)
                                                 and not k.telas.em_casa(estado.tela)
                                                 and estado.tipo != "intersticial"):
            async def voltar() -> None:
                await rt.executor.run(rt.io.press_key, "back", timeout=30, label="voltar")
                await asyncio.sleep(float(self.ajustes.settle_s))

            # 29.92 (ressalva b): no aparelho com conta real, uma tela de OUTRO pacote que casa com o detector de
            # verificação humana não leva o app reaberto por cima. Não marca conta travada (uma página qualquer com
            # verificação daria falso positivo e retiraria a conta): é `unknown` com motivo próprio, que no teto 1
            # deste aparelho já para e chama a pessoa.
            com_conta_real = tem_vinculo_ativo(self.repo.db, rt.id)
            verificacao_alheia: list[bool] = []

            def nao_reabrir_sobre(t: UiTree) -> bool:
                if com_conta_real and telas.detectar_conta_travada(t, k.telas) is not None:
                    verificacao_alheia.append(True)
                    return True
                return False

            try:
                tree, package, estado, passos = await telas.voltar_ao_estado_conhecido(
                    k.telas, observar=lambda: self._observe(rt), voltar=voltar,
                    reabrir=lambda: self._open_app(rt, aberturas),
                    reconhecer=lambda t, p: visto.anotar(t, self._reconhecer(k, t, p, locale)),
                    nao_reabrir_sobre=nao_reabrir_sobre)
            except AppParouDeResponder as exc:
                return self._parou_de_responder(rt, str(exc))
            if verificacao_alheia and estado.outro_app:
                detail = (f"outro app na frente ({package or 'sem pacote'}) mostra uma verificação humana; o "
                          f"{self.conhecimento.rotulo} não foi reaberto por cima, e uma pessoa olha a tela")
                self._save(conta, rt.id, SessionStatus.unknown, detail=detail, reobserved=True)
                return AuthResult(Outcome.UNCERTAIN, detail, session_status=SessionStatus.unknown)
            if passos:
                log.info("%s: estado conhecido do app — %s → %s", rt.id, " → ".join(passos), _nome_da_tela(estado))
            # A reabertura pode trazer de volta a Custom Tab que estava por cima do app.
            if (fora := self._no_navegador_fora_do_site(k, tree, package, estado)) is not None:
                return self._navegador_fora_da_conta(rt, conta, fora, parar=not observe_only)
        visto.tipo = estado.tipo
        visto.aprendida = estado.tela if _aprendida(k, estado.tela) else None
        # O app esteve na frente nesta chamada? "voltar" só é dado sobre uma tela DELE (fora de casa), então quem
        # termina no launcher depois de um "voltar" viu o app — a tela dele é que não foi reconhecida, e o voltar saiu
        # dela (a raiz). Só a outra forma de acabar no launcher é "o app não chegou ao primeiro plano".
        viu_o_app = not estado.outro_app or "voltar" in passos

        # 1) Já autenticado? Reaproveitar é o caminho normal: ninguém digita senha à toa.
        if k.telas.autenticada(estado.tela) and not force_login:
            check = await ler_conta(k, lambda: self._observe(rt), lambda x, y: self._tap(rt, x, y),
                                    expected=conta.handle, locale=locale, aceitos=conta.aceitos,
                                    reconhecer=lambda t, p: self._reconhecer(k, t, p, locale),
                                    voltar=lambda: self._voltar(rt))
            if check.trava is not None:
                self._app_voltou_a_frente(rt)
                return self._challenge(conta, rt.id, check.detail, check.trava)
            # Antes de conta certa/errada: `outro_app` só vem quando nada foi lido. E o aviso do cartão só sai DEPOIS
            # da leitura — tirá-lo ao ver o app e repô-lo quando ele cai publicaria "voltou / caiu" a cada tentativa.
            if check.outro_app:
                return self._fora_do_primeiro_plano(rt, conta,
                                                    f"{check.detail}; o app saiu da frente durante a leitura")
            self._app_voltou_a_frente(rt)
            if check.matches:
                self._save(conta, rt.id, SessionStatus.session_ready, observed=check.observed,
                           verified_at=now_iso(), detail=check.detail)
                self._reconciliar_revisao(conta, rt.id)
                return AuthResult(Outcome.SESSION_READY, check.detail, check.observed, SessionStatus.session_ready)
            if check.observed:
                if k.troca is not None and not observe_only:
                    return await self._trocar_de_conta(rt, k, conta, check.observed, locale, automatic=automatic)
                return await self._wrong_account(rt, conta, check.observed, locale)
            # entrou, mas a conta não pôde ser lida: não é sucesso nem motivo para digitar senha
            self._save(conta, rt.id, SessionStatus.unknown, detail=check.detail, reobserved=True)
            return AuthResult(Outcome.UNCERTAIN, check.detail, session_status=SessionStatus.unknown)
        if viu_o_app:
            self._app_voltou_a_frente(rt)

        if estado.tipo in TIPOS_DE_DESAFIO:
            return self._challenge(conta, rt.id, estado.razao, estado.trava)

        # 2) Deslogado: fazer login.
        if estado.tipo != "login":
            # Revisão do pacote: o launcher só é "fora do primeiro plano" quando o app NÃO esteve na frente. Uma tela
            # do próprio app não reconhecida, da qual o voltar sai do app (a raiz — um feed cujos sinais mudaram numa
            # atualização), termina no launcher e é exatamente o achado #104: tem de somar até o teto, senão a porta
            # reabre o app e relê a tela a cada tick, para sempre.
            if estado.outro_app and not viu_o_app:
                return self._fora_do_primeiro_plano(
                    rt, conta, f"{self.conhecimento.rotulo} não chegou ao primeiro plano ({estado.razao})")
            detail = (f"{self.conhecimento.rotulo} não voltou ao estado conhecido: o voltar saiu do app "
                      f"({estado.razao})" if estado.outro_app
                      else f"o app não está na tela de login nem autenticado ({estado.razao})")
            visto.sem_resolver = True                      # a tela desconhecida vista vai ao aprendizado (sinal)
            self._save(conta, rt.id, SessionStatus.unknown, detail=detail, reobserved=True)
            return AuthResult(Outcome.UNCERTAIN, detail)

        if observe_only:
            # "Verificar conta" só observa. Autenticar aqui gastaria uma tentativa de login REAL num clique que o
            # painel anuncia como leitura — e é o botão sugerido logo depois de um desafio resolvido à mão.
            detail = "o aparelho está deslogado; use Conectar para autenticar"
            self._save(conta, rt.id, SessionStatus.auth_required, detail=detail)
            return AuthResult(Outcome.UNCERTAIN, detail, session_status=SessionStatus.auth_required)

        return await self._login(rt, k, conta, estado, tree, locale, automatic=automatic)

    # ------------------------------------------------------------------ login
    async def _login(self, rt: DeviceRuntime, k: ConhecimentoDeSessao, conta: ContaDaSessao,
                     estado: TelaReconhecida, tree: UiTree, locale: str | None, *,
                     automatic: bool = False) -> AuthResult:
        """Três formas de entrada, todas pelo dado do app:

        - o formulário de uma tela (usuário, senha e Entrar juntos), como sempre;
        - a etapa do identificador de um login em etapas (item 23.6): preenche, confere, avança e espera a tela da
          senha (`_etapa_do_usuario`);
        - a tela da senha de um login em etapas, direto (o app lembrou o identificador).

        Nas duas últimas a senha só é digitada numa tela que mostra ESTE identificador: é ela que diz de quem é a senha
        pedida, como o campo de usuário conferido diz no formulário de uma tela.
        """
        profile_id = conta.profile_id
        cred = self.repo.account_credential_row(profile_id, conta.id)
        if cred is None:
            detail = "não há credencial cadastrada para este perfil"
            self._save(conta, rt.id, SessionStatus.auth_required, detail=detail)
            return AuthResult(Outcome.INVALID_CREDENTIAL, detail, session_status=SessionStatus.auth_required)
        if (teto := self._teto_diario(conta)) is not None:
            # Vale para o "Conectar" também: o teto é da CONTA. No automático, além de recusar, para o login até uma
            # pessoa olhar — senão a porta de sessão pediria este mesmo login a cada volta do agendador até a janela
            # de 24 h andar.
            if automatic:
                self._parar_login(conta, rt.id, teto, falhou=False)
            self._save(conta, rt.id, SessionStatus.auth_required, detail=teto)
            return AuthResult(Outcome.INVALID_CREDENTIAL, teto, session_status=SessionStatus.auth_required)

        etapa = k.formulario.etapa_do_usuario
        if etapa is not None and etapa.entrada is not None and estado.tela == etapa.entrada.tela:
            # A tela do app deslogado ("adicionar conta", item 23.8): um toque sem segredo abre a do identificador.
            aberta = await self._abrir_a_etapa_do_usuario(rt, k, conta, tree, locale)
            if isinstance(aberta, AuthResult):
                return aberta
            estado, tree = aberta
        form = estado.formulario if isinstance(estado.formulario, LoginForm) else None
        do_usuario = k.formulario_de_usuario(tree, locale) if etapa is not None and estado.tela == etapa.tela \
            else None
        # No login em etapas, o "usuário" acima da senha só vale se for um campo de texto: a tela da senha mostra a
        # conta num cabeçalho clicável (o de trocar de conta), que a geometria tomaria por usuário.
        uma_tela = form is not None and form.complete and (etapa is None or form.usuario_editavel)
        so_senha = etapa is not None and form is not None and form.submit is not None and not uma_tela
        if do_usuario is None and not uma_tela and not so_senha:
            detail = "o formulário de login não pôde ser identificado com segurança nesta tela"
            self._save(conta, rt.id, SessionStatus.auth_required, detail=detail)
            return AuthResult(Outcome.UNCERTAIN, detail, session_status=SessionStatus.auth_required)
        identificador = str(cred["login_identifier"] or conta.handle)

        # A tentativa é da CONTA deste app: o teto diário e o histórico de uma conta não somam os envios da outra.
        attempt = self.repo.start_auth_attempt(profile_id, rt.id, stage="form_found", account_id=conta.id)
        try:
            if do_usuario is not None:
                passo = await self._etapa_do_usuario(rt, k, conta, attempt, do_usuario, identificador, locale)
                if isinstance(passo, AuthResult):
                    return passo
                form = passo
            elif uma_tela:
                assert form is not None and form.username is not None      # `uma_tela` garante os dois

                def usuario_atual(t: UiTree) -> UiElement | None:
                    atual = k.formulario_de_login(t, locale)
                    return atual.username if atual is not None else None

                if not await self._fill_username(rt, form.username, identificador, usuario_atual):
                    detail = "o campo de usuário não ficou com o valor esperado; envio abortado"
                    self.repo.finish_auth_attempt(profile_id, attempt, outcome=Outcome.RETRYABLE.value, detail=detail,
                                                  stage="username_mismatch")
                    self._count_failure(conta, rt.id)
                    self._save(conta, rt.id, SessionStatus.auth_required, detail=detail)
                    return AuthResult(Outcome.RETRYABLE, detail, session_status=SessionStatus.auth_required)

                # O teclado sobe ao focar o usuário e empurra a tela para cima; as posições lidas com o formulário
                # vazio deixam de valer. Relê e passa a usar as coordenadas ATUAIS de senha e de Entrar. Sem isto, o
                # toque em Entrar cai no vão abaixo do botão e o login nunca é enviado — visto no aparelho real:
                # campos preenchidos, nenhuma mensagem de erro, parado na tela de login.
                try:
                    tree, package = await self._observe(rt)
                    atual = self._reconhecer(k, tree, package, locale).formulario
                    if isinstance(atual, LoginForm) and atual.complete:
                        form = atual
                except DriverError:
                    pass                                       # sem a releitura, segue com as coordenadas iniciais
            elif not geometria.mostra_o_identificador(tree, identificador):
                # A tela da senha, direto, sem dizer para quem: o app pode ter lembrado OUTRA conta.
                return self._conta_nao_mostrada(rt, conta, attempt, identificador)
            assert form is not None                     # as três formas chegam aqui com o formulário da senha

            # Relido AGORA, e não só no começo: a mesma pessoa pode estar em dois aparelhos, e o desafio visto no outro
            # bloqueia o perfil enquanto este digita o usuário. Conta bloqueada nunca recebe a senha (ADR-055).
            if (interrompido := self._parada_no_meio(conta, automatic=automatic)) is not None:
                return self._abortar_pela_conta(conta, attempt, *interrompido, rt.id)
            await self._fill_password(rt, k, conta, cred["secret_ref"])
            if etapa is not None:
                form = await self._botao_de_entrar_atual(rt, k, conta, form, locale)
        except SensitiveInputUnavailable as exc:
            # Não é falha de credencial: nem a senha foi enviada, nem o app foi consultado. Contar aqui gastava o
            # teto de tentativas (e podia acionar o cooldown) por um motivo de infraestrutura do PRÓPRIO backend —
            # achado #105. O perfil grava o motivo (o painel não explicava por que não conectou) e a sessão fica
            # `auth_required`, não silenciosa.
            self.repo.finish_auth_attempt(profile_id, attempt, outcome=Outcome.RETRYABLE.value, detail=str(exc),
                                          stage="sensitive_channel_blocked")
            self._save(conta, rt.id, SessionStatus.auth_required, detail=str(exc))
            return AuthResult(Outcome.RETRYABLE, str(exc), session_status=SessionStatus.auth_required)
        except SensitiveInputError as exc:
            # Mensagem fixa por construção: nunca carrega o que foi digitado.
            self.repo.finish_auth_attempt(profile_id, attempt, outcome=Outcome.RETRYABLE.value, detail=str(exc),
                                          stage="fill_failed")
            self._count_failure(conta, rt.id)
            return AuthResult(Outcome.RETRYABLE, str(exc))
        except (DriverError, KeyError) as exc:
            detail = f"não foi possível preencher o formulário: {type(exc).__name__}"
            self.repo.finish_auth_attempt(profile_id, attempt, outcome=Outcome.RETRYABLE.value, detail=detail,
                                          stage="fill_failed")
            self._count_failure(conta, rt.id)
            return AuthResult(Outcome.RETRYABLE, detail)

        # A última porta antes de a senha sair da máquina: o campo preenchido não é envio; o toque em Entrar é.
        if (interrompido := self._parada_no_meio(conta, automatic=automatic)) is not None:
            return self._abortar_pela_conta(conta, attempt, *interrompido, rt.id)
        # Envio: registrado ANTES de acontecer. Depois disso, timeout nunca autoriza repetir.
        self.repo.finish_auth_attempt(profile_id, attempt, outcome="", detail=None, stage="submitting")
        fired = True
        botao: UiElement = form.submit  # type: ignore[assignment]  # as três formas garantem o botão
        try:
            await self._tap(rt, *botao.center)
        except DriverError as exc:
            if not exc.effect_possible:
                fired = False
            log.warning("%s: erro ao tocar em Entrar (efeito possível=%s)", rt.id, exc.effect_possible)
        if not fired:
            detail = "o toque em Entrar não chegou ao aparelho; nada foi enviado"
            self.repo.finish_auth_attempt(profile_id, attempt, outcome=Outcome.RETRYABLE.value, detail=detail,
                                          stage="submit_not_delivered")
            self._count_failure(conta, rt.id)
            return AuthResult(Outcome.RETRYABLE, detail, attempted_login=True)

        verdict = await self._watch_after_submit(rt, k, conta, locale)
        if verdict.outcome is Outcome.UNCERTAIN:
            verdict = await self._retocar_se_intacto(rt, k, conta, attempt, identificador, locale, verdict,
                                                     automatic=automatic)
        self._apply_verdict(conta, rt.id, attempt, verdict)
        return AuthResult(verdict.outcome, verdict.detail, verdict.observed_username,
                          self._status_for(verdict.outcome), attempted_login=True)

    async def _retocar_se_intacto(self, rt: DeviceRuntime, k: ConhecimentoDeSessao, conta: ContaDaSessao,
                                  attempt: int, identificador: str, locale: str | None, incerto: Verdict, *,
                                  automatic: bool) -> Verdict:
        """29.64: UM re-toque em Entrar quando o envio ficou incerto e o formulário seguiu intacto.

        Visto no android-13 (04/10, c-20261004001548-03701b): o toque em Entrar se perdeu, o formulário ficou preenchido,
        o botão habilitado, sem erro nem carregando, e o login fechou `uncertain`. Um toque manual no mesmo botão, sem
        redigitar nada, entrou. A senha NÃO é digitada de novo: o re-toque só vale com ela ainda no campo (o app que
        recusou ou consumiu o envio limpa o campo). Uma vez só, e só com tudo isto conferido na tela de AGORA; qualquer
        dúvida (carregando, erro, outra tela, campo vazio, conta parada no meio) devolve o incerto de antes."""
        botao = await self._formulario_intacto(rt, k, conta, identificador, locale)
        if botao is None or self._parada_no_meio(conta, automatic=automatic) is not None:
            return incerto
        self.repo.finish_auth_attempt(conta.profile_id, attempt, outcome="", detail=None, stage="resubmitting")
        log.info("%s: %s — formulário intacto depois do envio; um re-toque em Entrar, sem redigitar", rt.id,
                 self.conhecimento.rotulo)
        try:
            await self._tap(rt, *botao.center)
        except DriverError as exc:
            log.warning("%s: erro no re-toque em Entrar (efeito possível=%s)", rt.id, exc.effect_possible)
            if not exc.effect_possible:
                return incerto
        return await self._watch_after_submit(rt, k, conta, locale)

    async def _formulario_intacto(self, rt: DeviceRuntime, k: ConhecimentoDeSessao, conta: ContaDaSessao,
                                  identificador: str, locale: str | None) -> UiElement | None:
        """O botão Entrar ATUAL, quando a tela segue sendo o formulário deste login, pronto para outro toque: o
        identificador à vista (no campo de usuário ou, no login em etapas, na tela), a senha ainda no campo, o botão
        habilitado e nada carregando. `None` em qualquer outro caso."""
        try:
            tree, package = await self._observe(rt)
        except DriverError:
            return None
        if package != k.app:
            return None
        estado = self._reconhecer(k, tree, package, locale)
        form = estado.formulario if isinstance(estado.formulario, LoginForm) else None
        if estado.tipo != "login" or form is None or form.submit is None or not form.submit.enabled:
            return None
        # Desafio e erro de credencial conferidos na tela de AGORA, de forma explícita: o incerto da observação diz só
        # o que ela viu até o prazo, e a tela pode ter mudado depois dele.
        if estado.trava is not None:
            return None
        if classificar_depois_do_envio(k, tree, package=package, expected_username=conta.handle, locale=locale,
                                       aceitos=conta.aceitos).outcome is not Outcome.UNCERTAIN:
            return None
        if not (form.password.text or "").strip():
            return None
        if any("progress" in (e.class_name or "").lower() for e in tree.elements):
            return None
        esperado = identificador.strip().lstrip("@").lower()
        if form.username is not None and form.usuario_editavel:
            if (form.username.text or "").strip().lstrip("@").lower() != esperado:
                return None
        elif not geometria.mostra_o_identificador(tree, identificador):
            return None
        return form.submit

    # ------------------------------------------------------------------ login em etapas (item 23.6)
    async def _abrir_a_etapa_do_usuario(self, rt: DeviceRuntime, k: ConhecimentoDeSessao, conta: ContaDaSessao,
                                        tree: UiTree, locale: str | None) -> tuple[TelaReconhecida, UiTree] | AuthResult:
        """Da tela de entrada declarada até a do identificador (item 23.8): toca o botão declarado (um candidato só) e
        espera a tela do identificador. É navegação: nada foi digitado e nenhuma tentativa começa, então nada aqui
        conta falha nem gasta o teto diário. Um desafio no caminho vai para a pessoa como em qualquer outra tela."""
        etapa = k.formulario.etapa_do_usuario
        assert etapa is not None and etapa.entrada is not None
        botao = k.botao_da_entrada(tree, locale)
        if botao is None:
            detail = (f"a tela de entrada do {self.conhecimento.rotulo} não mostra um botão único para começar o "
                      "login; nada foi tocado")
            self._save(conta, rt.id, SessionStatus.auth_required, detail=detail)
            return AuthResult(Outcome.UNCERTAIN, detail, session_status=SessionStatus.auth_required)
        await self._tap(rt, *botao.center)
        prazo = now().timestamp() + float(self.ajustes.submit_wait_s)
        ultima = "nenhuma leitura"
        while now().timestamp() < prazo:
            await asyncio.sleep(OBSERVAR_DEPOIS_DO_ENVIO_S)
            try:
                tree, package = await self._observe(rt)
            except DriverError:
                continue
            r = self._reconhecer(k, tree, package, locale)
            if r.trava is not None or r.tipo in TIPOS_DE_DESAFIO:
                return self._challenge(conta, rt.id, r.razao, r.trava)
            if r.tela == etapa.tela:
                return r, tree
            ultima = r.razao
        detail = (f"a tela do identificador do {self.conhecimento.rotulo} não apareceu depois da tela de entrada "
                  f"({ultima}); nada foi digitado")
        self._save(conta, rt.id, SessionStatus.auth_required, detail=detail)
        return AuthResult(Outcome.UNCERTAIN, detail, session_status=SessionStatus.auth_required)

    async def _etapa_do_usuario(self, rt: DeviceRuntime, k: ConhecimentoDeSessao, conta: ContaDaSessao,
                                attempt: int, form_u: FormularioDoUsuario, identificador: str,
                                locale: str | None) -> LoginForm | AuthResult:
        """A etapa do identificador: preenche, CONFERE, toca no "avançar" e espera a tela da senha — que só serve
        mostrando este identificador. Nada secreto sai daqui: o toque é no "avançar", e a senha só é digitada depois,
        pelo canal sensível. Devolve o formulário da tela da senha, ou o desfecho que encerra a tentativa."""
        profile_id = conta.profile_id

        def campo_atual(t: UiTree) -> UiElement | None:
            atual = k.formulario_de_usuario(t, locale)
            return atual.campo if atual is not None else None

        if not await self._fill_username(rt, form_u.campo, identificador, campo_atual):
            detail = "o campo do identificador não ficou com o valor esperado; nada foi enviado"
            self.repo.finish_auth_attempt(profile_id, attempt, outcome=Outcome.RETRYABLE.value, detail=detail,
                                          stage="username_mismatch")
            self._count_failure(conta, rt.id)
            self._save(conta, rt.id, SessionStatus.auth_required, detail=detail)
            return AuthResult(Outcome.RETRYABLE, detail, session_status=SessionStatus.auth_required)
        # O teclado subiu com o campo: o "avançar" é relido na tela de agora (a mesma razão da releitura no formulário
        # de uma tela).
        tree, _ = await self._observe(rt)
        await self._tap(rt, *(k.formulario_de_usuario(tree, locale) or form_u).botao.center)

        prazo = now().timestamp() + float(self.ajustes.submit_wait_s)
        sem_a_conta = False
        escolhidas: set[str] = set()
        while now().timestamp() < prazo:
            await asyncio.sleep(OBSERVAR_DEPOIS_DO_ENVIO_S)
            try:
                tree, package = await self._observe(rt)
            except DriverError:
                continue
            r = self._reconhecer(k, tree, package, locale)
            # Item 23.8: ANTES do desafio. A tela declarada como alternativa (o provedor propõe um código e oferece a
            # senha) é, pelo tipo, de código — e iria para a pessoa sem que o titular tivesse escolhido o código. Com o
            # botão declarado na tela (um só, fora de conta travada, no destino permitido), escolhe-se a senha, uma vez
            # por tela; enquanto a mesma tela seguir com o botão, espera a troca (o WebView demora um instante) em vez
            # de julgá-la desafio. Sem o botão, cai no desafio logo abaixo.
            alternativa = k.botao_da_alternativa(tree, r, locale)
            if alternativa is not None and self._destino(k, tree, package).permitido:
                if r.tela not in escolhidas:
                    escolhidas.add(r.tela)
                    log.info("%s: %s ofereceu entrar com a senha (%s); escolhida", rt.id, self.conhecimento.rotulo,
                             r.tela)
                    await self._tap(rt, *alternativa.center)
                    prazo = now().timestamp() + float(self.ajustes.submit_wait_s)
                continue
            if r.trava is not None or r.tipo in TIPOS_DE_DESAFIO:
                trava = f" [{r.trava.descrever()}]" if r.trava is not None else ""
                self.repo.finish_auth_attempt(profile_id, attempt, outcome=Outcome.AUTH_CHALLENGE.value,
                                              detail=r.razao + trava, stage=ETAPA_DESAFIO_ANTES_DA_SENHA)
                return self._challenge(conta, rt.id, r.razao, r.trava)
            if (fora := self._no_navegador_fora_do_site(k, tree, package, r)) is not None:
                self.repo.finish_auth_attempt(profile_id, attempt, outcome=Outcome.UNCERTAIN.value,
                                              detail=_fora_do_site(fora), stage=ETAPA_NAVEGADOR_FORA_DA_CONTA)
                return self._navegador_fora_da_conta(rt, conta, fora, parar=True)
            if (recusa := k.recusa_do_identificador(tree, locale)) is not None:
                return self._identificador_recusado(rt, conta, attempt, recusa)
            form = r.formulario if isinstance(r.formulario, LoginForm) else None
            if form is not None and form.submit is not None:
                if geometria.mostra_o_identificador(tree, identificador):
                    return form
                sem_a_conta = True          # a tela da senha chegou sem dizer para quem: pode estar carregando
        if sem_a_conta:
            return self._conta_nao_mostrada(rt, conta, attempt, identificador)
        # O "avançar" já foi efeito no servidor: o identificador saiu, e o provedor de contas pode ter mandado um código
        # por e-mail ou um pedido de aprovação no telefone. Contar falha (3 e o intervalo) repetia esse toque a cada
        # intervalo vencido, sem fim e com a credencial `active`: o login para até uma pessoa olhar, como nas outras
        # saídas da etapa. A senha não saiu, então a etapa segue fora do teto diário.
        detail = ("a tela da senha não apareceu depois do identificador; a senha não foi digitada. Confira no aparelho "
                  f"o que o {self.conhecimento.rotulo} mostrou depois do identificador")
        self.repo.finish_auth_attempt(profile_id, attempt, outcome=Outcome.UNCERTAIN.value, detail=detail,
                                      stage=ETAPA_SEM_TELA_DA_SENHA)
        self._parar_login(conta, rt.id, detail, falhou=False)
        self._save(conta, rt.id, SessionStatus.auth_required, detail=detail)
        return AuthResult(Outcome.UNCERTAIN, detail, session_status=SessionStatus.auth_required)

    async def _botao_de_entrar_atual(self, rt: DeviceRuntime, k: ConhecimentoDeSessao, conta: ContaDaSessao,
                                     form: LoginForm, locale: str | None) -> LoginForm:
        """Na tela da senha do login em etapas, o teclado sobe ao focar o campo e empurra o botão de entrar: o toque
        usa a posição de AGORA, se a tela de agora ainda é o formulário no mesmo destino permitido; senão, a de antes."""
        try:
            tree, package = await self._observe(rt)
        except DriverError:
            return form
        if not self._destino(k, tree, package).permitido:
            return form
        atual = self._reconhecer(k, tree, package, locale).formulario
        return atual if isinstance(atual, LoginForm) and atual.submit is not None else form

    def _conta_nao_mostrada(self, rt: DeviceRuntime, conta: ContaDaSessao, attempt: int,
                            identificador: str) -> AuthResult:
        """A tela da senha não diz que é desta conta: nada é digitado, e o login para até uma pessoa olhar — a tela
        vai continuar a mesma a cada tentativa (o app lembrou outra conta, ou mudou o jeito de mostrá-la)."""
        detail = (f"a tela da senha do {self.conhecimento.rotulo} não mostra a conta {_como_conta(identificador)}; a "
                  "senha não foi digitada. Confira no aparelho qual conta o app está pedindo")
        self.repo.finish_auth_attempt(conta.profile_id, attempt, outcome=Outcome.UNCERTAIN.value, detail=detail,
                                      stage=ETAPA_CONTA_NAO_MOSTRADA)
        self._parar_login(conta, rt.id, detail, falhou=False)
        self._save(conta, rt.id, SessionStatus.auth_required, detail=detail)
        return AuthResult(Outcome.UNCERTAIN, detail, session_status=SessionStatus.auth_required)

    def _identificador_recusado(self, rt: DeviceRuntime, conta: ContaDaSessao, attempt: int,
                                recusa: str) -> AuthResult:
        """O app recusou o IDENTIFICADOR (a conta não existe, o endereço é inválido). A senha não saiu, então a
        credencial não vira `invalid` (ninguém julgou a senha): o login para em `review` até uma pessoa conferir o
        identificador de login da conta — insistir mostraria a mesma recusa."""
        detail = f"{recusa}; a senha não foi digitada. Confira o identificador de login da conta"
        self.repo.finish_auth_attempt(conta.profile_id, attempt, outcome=Outcome.INVALID_CREDENTIAL.value,
                                      detail=detail, stage=ETAPA_IDENTIFICADOR_RECUSADO)
        self._parar_login(conta, rt.id, detail, falhou=False)
        self._save(conta, rt.id, SessionStatus.auth_required, detail=detail)
        return AuthResult(Outcome.INVALID_CREDENTIAL, detail, session_status=SessionStatus.auth_required)

    def _navegador_fora_da_conta(self, rt: DeviceRuntime, conta: ContaDaSessao, destino: Destino, *,
                                 parar: bool) -> AuthResult:
        """O login está no navegador declarado, num site que não é da conta (ou sem a barra de endereço à vista): a
        pessoa assume. Nada foi digitado ali. `parar` = o login automático para até uma pessoa olhar (a mesma Custom
        Tab voltaria a cada tentativa); a leitura ("Verificar conta") só registra."""
        detail = (f"{_fora_do_site(destino)}; a senha não foi digitada. Assuma o controle do aparelho e entre à mão "
                  f"no {self.conhecimento.rotulo} — ou, se o site é o de login do app, declare-o")
        if parar:
            self._parar_login(conta, rt.id, detail, falhou=False)
        self._save(conta, rt.id, SessionStatus.auth_required, detail=detail)
        return AuthResult(Outcome.UNCERTAIN, detail, session_status=SessionStatus.auth_required)

    async def _watch_after_submit(self, rt: DeviceRuntime, k: ConhecimentoDeSessao, conta: ContaDaSessao,
                                  locale: str | None) -> Verdict:
        """Observa até a tela decidir. Nunca reenvia: só olha.

        Na Custom Tab declarada, num site da conta, a tela é do app. Fora do site, só o desafio decide: regra do app
        não vale numa página alheia (uma "senha incorreta" ali não é deste login). Com `conta.ler_ao_entrar`, a tela
        de casa sem a conta à vista chama a leitura pelo acesso declarado — uma vez: é o que confirma o login num app
        cuja tela inicial nunca mostra a conta.
        """
        prazo = now().timestamp() + float(self.ajustes.submit_wait_s)
        ultimo = Verdict(Outcome.UNCERTAIN, "a tela não mudou depois do envio")
        leu = False
        dispensas = 0
        while now().timestamp() < prazo:
            await asyncio.sleep(OBSERVAR_DEPOIS_DO_ENVIO_S)
            try:
                tree, package = await self._observe(rt)
            except DriverError:
                continue
            # Item 23.8: o que o app DECLARA dispensar depois de entrar (o aviso da conta, o diálogo de chave de acesso
            # do sistema, os informativos do primeiro uso) sai daqui mesmo, sem esperar a tela de casa — a Microsoft
            # empilha meia dúzia deles antes da caixa. Só o declarado (quem não declara segue igual), até o teto de
            # dispensas, e cada uma renova o prazo: é a tela mudando, não o envio sem resposta.
            if dispensas < k.dispensa.intersticiais_max:
                declarada = k.dispensa_declarada(tree, self._reconhecer(k, tree, package, locale), package, locale)
                if declarada is not None:
                    dispensas += 1
                    log.info("%s: %s — dispensa declarada (%s)", rt.id, self.conhecimento.rotulo, declarada.onde)
                    if declarada.botao is not None:
                        await self._tap(rt, *declarada.botao.center)
                    else:
                        await self._voltar(rt)
                    prazo = max(prazo, now().timestamp() + float(self.ajustes.submit_wait_s))
                    continue
            destino = self._destino(k, tree, package)
            ultimo = classificar_depois_do_envio(k, tree, package=k.app if destino.navegador else package,
                                                 expected_username=conta.handle, locale=locale,
                                                 aceitos=conta.aceitos)
            if destino.navegador and not destino.permitido and ultimo.outcome is not Outcome.AUTH_CHALLENGE:
                ultimo = Verdict(Outcome.UNCERTAIN, _fora_do_site(destino))
                continue
            if ultimo.outcome is not Outcome.UNCERTAIN:
                return ultimo
            if ultimo.conferir and k.conta.ler_ao_entrar and not leu:
                lido, fora_da_frente = await self._ler_ao_entrar(rt, k, conta, locale)
                if lido is not None:
                    return lido
                # A leitura só conta como feita com o app na frente: a Custom Tab ainda fechando (ou a tela de
                # transição) não gasta a única leitura, que fica para quando a tela de casa chegar.
                leu = not fora_da_frente
        return ultimo

    async def _ler_ao_entrar(self, rt: DeviceRuntime, k: ConhecimentoDeSessao, conta: ContaDaSessao,
                             locale: str | None) -> tuple[Verdict | None, bool]:
        """A conta lida pelo acesso declarado logo depois do envio. Só navegação sem efeito externo (as dispensas são
        de recusa e o acesso abre a conta). Devolve o desfecho (ou `None`: nada foi lido e a observação segue até o
        prazo) e se a leitura terminou com OUTRO pacote na frente — o app ainda não tinha voltado da Custom Tab (a
        página de transição do navegador, no site da conta, é "tela do app" para o reconhecimento, não para isto)."""
        pacote_da_frente: list[str | None] = [None]

        async def observar() -> tuple[UiTree, str | None]:
            tree, package = await self._observe(rt)
            pacote_da_frente[0] = package
            return tree, package

        check = await ler_conta(k, observar, lambda x, y: self._tap(rt, x, y),
                                expected=conta.handle, locale=locale, aceitos=conta.aceitos,
                                reconhecer=lambda t, p: self._reconhecer(k, t, p, locale),
                                voltar=lambda: self._voltar(rt))
        if check.trava is not None:
            return Verdict(Outcome.AUTH_CHALLENGE, check.detail, tela=check.trava.origem or telas.DESCONHECIDA,
                           trava=check.trava), False
        if check.observed:
            return Verdict(Outcome.SESSION_READY if check.matches else Outcome.WRONG_ACCOUNT, check.detail,
                           observed_username=check.observed, tela=k.conta.tela_de_perfil), False
        return None, pacote_da_frente[0] is not None and pacote_da_frente[0] != k.app

    # ------------------------------------------------------------------ conta errada
    async def _trocar_de_conta(self, rt: DeviceRuntime, k: ConhecimentoDeSessao, conta: ContaDaSessao,
                               observado: str, locale: str | None, *, automatic: bool) -> AuthResult:
        """31.155 (ADR-080): a conta lida na tela não é a esperada, e o app DECLARA como sair dela.

        Ordem: (1) a conta esperada pode entrar — os pré-cheques do `_login` rodam ANTES de tirar a outra, senão uma
        recusa depois do "Sair" deixaria o aparelho sem conta nenhuma e a que estava aberta sem sessão à toa; (2) os
        toques de saída, cada um na tela declarada e com um candidato só (tela de verificação: nada é tocado); (3) a
        tela tem de ser a de login, e só então o `_login` de sempre digita a senha da conta esperada, do cofre, pelo
        canal sensível. Qualquer desvio cai em `_wrong_account` com o motivo, e a pessoa assume."""
        assert k.troca is not None                  # quem chama confere
        if (recusa := self._antes_de_sair(rt, conta, automatic=automatic)) is not None:
            return recusa
        if (sem_volta := self._sem_volta(rt, conta, observado)) is not None:
            return await self._wrong_account(rt, conta, observado, locale, troca=sem_volta)
        tocou, falha = False, None
        for i, passo in enumerate(k.troca.sair, start=1):
            tree, package = await self._observe(rt)
            estado = self._reconhecer(k, tree, package, locale)
            if (i == 1 and estado.tela != passo.tela and passo.tela == k.conta.tela_de_perfil
                    and k.telas.autenticada(estado.tela) and (aba := k.aba_de_perfil(tree)) is not None):
                # A conta pode ter sido lida numa tela de casa; o "Sair" mora na tela da conta, aberta pela MESMA aba
                # (ou acesso) que a leitura da conta usa. Toque sem segredo e sem efeito fora do app.
                await self._tap(rt, *aba)
                await asyncio.sleep(float(self.ajustes.settle_s))
                tree, package = await self._observe(rt)
                estado = self._reconhecer(k, tree, package, locale)
            if estado.trava is not None or estado.tipo in TIPOS_DE_DESAFIO:
                # Como no `_garantir`: nada é tocado, e o desafio segue a regra de sempre (ADR-055: quarentena do
                # aparelho, conta travada sai). Atribuído à conta esperada, como no caminho do `ler_conta`.
                if tocou:
                    self._invalidar_o_app(rt, conta)
                return self._challenge(conta, rt.id, estado.razao, estado.trava)
            if estado.tela != passo.tela:
                falha = f"o passo {i} da saída esperava a tela '{passo.tela}', e a tela é {_nome_da_tela(estado)}"
                break
            botao = k.botao_da_troca(tree, passo, locale)
            if botao is None:
                falha = f"o botão do passo {i} da saída não foi achado com um candidato só"
                break
            # `tocou` vale ANTES do toque: um erro do driver depois de o toque chegar (prazo estourado com o "Sair" já
            # aceito) deixaria a conta deslogada com a sessão ainda pronta e tarefas despachadas para ela. O desfecho
            # é incerto: as sessões do app neste aparelho caem para `unknown` e o erro sobe como sempre.
            tocou = True
            try:
                await self._tap(rt, *botao.center)
            except BaseException:
                self._invalidar_o_app(rt, conta)
                raise
            await asyncio.sleep(float(self.ajustes.settle_s))
        if tocou:
            self._invalidar_o_app(rt, conta)
        if falha is None:
            # A saída leva o tempo do app (rede, animação): espera a tela de login até o prazo da verificação, em vez de
            # concluir pela primeira leitura e mandar a pessoa a um aparelho que só estava saindo.
            prazo = time.monotonic() + float(self.ajustes.verify_timeout_s)
            while True:
                tree, package = await self._observe(rt)
                estado = self._reconhecer(k, tree, package, locale)
                if (estado.tipo == "login" or estado.trava is not None or estado.tipo in TIPOS_DE_DESAFIO
                        or time.monotonic() >= prazo):
                    break
                await asyncio.sleep(float(self.ajustes.settle_s))
            if estado.trava is not None or estado.tipo in TIPOS_DE_DESAFIO:
                return self._challenge(conta, rt.id, estado.razao, estado.trava)
            if estado.tipo == "login":
                self.bus.emit("log", f"{rt.id}: troca de conta no {self.conhecimento.rotulo}: "
                                     f"{_como_conta(observado)} saiu para entrar {_como_conta(conta.handle)}",
                              instance_id=rt.id)
                return await self._login(rt, k, conta, estado, tree, locale, automatic=automatic)
            falha = f"depois da saída a tela não é a de login ({_nome_da_tela(estado)})"
        return await self._wrong_account(rt, conta, observado, locale, troca=falha)

    def _invalidar_o_app(self, rt: DeviceRuntime, conta: ContaDaSessao) -> None:
        """Depois de um toque de saída, nenhuma sessão deste app neste aparelho vale mais o que dizia — não só a da conta
        que estava aberta: o app pode ter levado junto as outras contas lembradas. Desafio e conta errada ficam como
        estão: são o que a pessoa precisa ver (o mesmo cuidado do começo do `_garantir`)."""
        motivo = f"a conta saiu do {self.conhecimento.rotulo} neste aparelho pela troca de conta (ADR-080)"
        linhas = self.repo.db.query(
            "SELECT s.account_id, a.profile_id FROM account_sessions s JOIN profile_accounts a ON a.id = s.account_id"
            " WHERE s.instance_id=? AND a.app_id IN (?, ?) AND s.status IN (?, ?)",
            (rt.id, conta.app_id, self.package, SessionStatus.session_ready.value, SessionStatus.auth_required.value))
        for linha in linhas:
            self.repo.set_account_session(str(linha["profile_id"]), str(linha["account_id"]), rt.id,
                                          status=SessionStatus.unknown, detail=motivo)

    def _sem_volta(self, rt: DeviceRuntime, conta: ContaDaSessao, observado: str) -> str | None:
        """Por que a conta aberta NÃO pode sair (31.155); `None` = pode. Sem tocar no aparelho.

        - Aparelho em quarentena (ADR-055): com conta travada ali, nada entra nem sai pela automação.
        - A conta aberta tem de ser uma conta nossa deste app, ativa, com senha guardada, ativa e com consentimento: só ela a
          automação consegue trazer de volta. Uma conta que alguém abriu à mão (fora do cofre, ou que passou por
          verificação) não é deslogada — derrubá-la seria sem volta."""
        if self.repo.conta_travada_no_aparelho(rt.id) is not None:
            return "o aparelho está em quarentena por conta travada (ADR-055); a troca não começa"
        alvo = observado.strip().lstrip("@").lower()
        linhas = self.repo.db.query(
            "SELECT a.handle, c.login_identifier, c.consent_at, a.status, c.status AS credencial FROM profile_accounts a"
            " JOIN account_credentials c ON c.account_id = a.id WHERE a.app_id IN (?, ?)", (conta.app_id, self.package))
        for linha in linhas:
            nomes = {str(linha["handle"] or "").strip().lstrip("@").lower(),
                     str(linha["login_identifier"] or "").strip().lstrip("@").lower()}
            # A credencial também tem de estar ativa: a senha recusada (`invalid`) ou em revisão depois de um desafio
            # não traz a conta de volta, e deslogá-la tiraria uma sessão que hoje funciona.
            if (alvo in nomes and linha["consent_at"] is not None and (linha["status"] or "active") == "active"
                    and (linha["credencial"] or "active") == "active"):
                return None
        return ("a conta aberta não é uma conta nossa deste app com senha guardada e consentimento; ela não é "
                "deslogada, porque a automação não a traria de volta")

    def _antes_de_sair(self, rt: DeviceRuntime, conta: ContaDaSessao, *, automatic: bool) -> AuthResult | None:
        """O que barraria o login da conta esperada, conferido sem tocar no aparelho (31.155). `None` = pode trocar.

        São as recusas do `_login` (credencial, teto diário, parada no meio) e mais duas que a porta de sessão confere
        para o automático e o "Conectar" não: o consentimento da conta (ADR-040) e o canal sensível."""
        cred = self.repo.account_credential_row(conta.profile_id, conta.id)
        if cred is None or cred["consent_at"] is None:
            detail = ("a conta esperada não tem senha guardada com o consentimento para a automação digitá-la; a troca "
                      "de conta não tira a conta aberta sem poder entrar na esperada")
            self._save(conta, rt.id, SessionStatus.auth_required, detail=detail)
            return AuthResult(Outcome.INVALID_CREDENTIAL, detail, session_status=SessionStatus.auth_required)
        if (teto := self._teto_diario(conta)) is not None:
            if automatic:
                self._parar_login(conta, rt.id, teto, falhou=False)
            self._save(conta, rt.id, SessionStatus.auth_required, detail=teto)
            return AuthResult(Outcome.INVALID_CREDENTIAL, teto, session_status=SessionStatus.auth_required)
        if (parada := self._parada_no_meio(conta, automatic=automatic)) is not None:
            return self._recusa_sem_tocar(conta, rt.id, parada[0])
        if not self.sensitive.available():
            return AuthResult(Outcome.RETRYABLE, "o canal de preenchimento de credencial está indisponível; a troca de "
                                                 "conta não tira a conta aberta sem poder digitar a senha da esperada")
        return None

    async def _wrong_account(self, rt: DeviceRuntime, conta: ContaDaSessao, observado: str,
                             locale: str | None, *, troca: str | None = None) -> AuthResult:
        # Achado #115: no app que NÃO declara a troca (`troca` no `sessao.yaml`), conta errada é SEMPRE intervenção
        # humana; nenhum caminho digita senha nem troca de conta sozinho aqui. No app que declara (31.155, ADR-080),
        # chega-se aqui só quando a troca não terminou (`troca` = o motivo): a sessão fica `wrong_account`, que a
        # porta de sessão e `_needs_person` tratam como caso de pessoa — a troca que falhou não se repete a cada tick.
        if troca is None and self.conhecimento.troca is not None:
            # Só o "Verificar conta" (que nunca troca) chega aqui num app que declara a troca.
            detail = (f"a conta aberta é {_como_conta(observado)}, e a esperada é {_como_conta(conta.handle)}. Este "
                      "app declara a troca de conta: use Conectar para trocar (a verificação só lê a tela).")
        elif troca is None:
            detail = (f"a conta aberta é {_como_conta(observado)}, e a esperada é {_como_conta(conta.handle)}. A "
                      "troca de conta é sempre manual — assuma o controle do aparelho e faça login na conta certa (ou "
                      "'Sair da conta', que apaga os dados do app).")
        else:
            detail = (f"a conta aberta era {_como_conta(observado)}, e a esperada é {_como_conta(conta.handle)}. A "
                      f"troca de conta declarada pelo app não terminou ({troca}); assuma o controle do aparelho e faça "
                      "login na conta certa.")
        self._save(conta, rt.id, SessionStatus.wrong_account, observed=observado, detail=detail)
        self.bus.emit("log", f"{rt.id}: {detail}", level="warn", instance_id=rt.id)
        return AuthResult(Outcome.WRONG_ACCOUNT, detail, observado, SessionStatus.wrong_account)

    # ------------------------------------------------------------------ persistência e limites
    # Tudo daqui para baixo é da CONTA desta chamada (`ContaDaSessao`): a credencial e a marcação dela, as tentativas
    # e o teto diário, a sessão no aparelho. O status do PERFIL (`blocked`/`disabled`) continua da persona.
    def _apply_verdict(self, conta: ContaDaSessao, instance_id: str, attempt: int, verdict: Verdict) -> None:
        profile_id = conta.profile_id
        textos = self.conhecimento.textos
        # A etapa nomeia o diálogo genérico de erro (declarada na regra): é o que a fase local filtra no histórico
        # (sem senha, sem token). O subtipo e o trecho do desafio vão no detalhe (ADR-055): a etapa fica a de sempre.
        trava = f" [{verdict.trava.descrever()}]" if verdict.trava is not None else ""
        self.repo.finish_auth_attempt(profile_id, attempt, outcome=verdict.outcome.value,
                                      detail=verdict.detail + trava, stage=verdict.etapa)
        status = self._status_for(verdict.outcome)
        detalhe = f"{textos.desafio} ({verdict.detail}){trava}" if verdict.outcome is Outcome.AUTH_CHALLENGE \
            else verdict.detail
        if verdict.outcome is Outcome.UNCERTAIN:
            # O incerto é o caso que se repetia (uma conta real, 18/09): a sessão tem de dizer que o login parou e por quê.
            detalhe = f"{verdict.detail}; {motivo_do_login_parado(self.conhecimento.rotulo)}"
        self._save(conta, instance_id, status, observed=verdict.observed_username,
                   verified_at=now_iso() if verdict.outcome is Outcome.SESSION_READY else None,
                   detail=detalhe, trava=verdict.trava)
        if verdict.outcome is Outcome.AUTH_CHALLENGE:
            self.bus.emit("log", f"{instance_id}: {textos.desafio}{trava}", level="warn", instance_id=instance_id,
                          data=_dados_da_trava(profile_id, verdict.trava))
        if verdict.outcome is Outcome.SESSION_READY:
            self.repo.mark_account_credential(profile_id, conta.id, status="active", failed_attempts=0,
                                              blocked_until=None)
            self.repo.touch_account_credential(profile_id, conta.id)
            return
        if verdict.outcome is Outcome.INVALID_CREDENTIAL:
            # Senha comprovadamente errada: bloqueia nova tentativa automática até a credencial mudar.
            self.repo.mark_account_credential(profile_id, conta.id, status="invalid", blocked_until=None)
            usuario = conta.handle.strip().lstrip("@")
            self.bus.emit("log", f"{instance_id}: {textos.credencial_recusada.format(usuario=usuario)}",
                          level="error", instance_id=instance_id)
            return
        # Daqui para baixo, a SENHA JÁ FOI ENVIADA e o desfecho não foi sucesso (incerto, desafio, conta errada).
        # ADR-055: UM envio assim já para o login automático até uma pessoa olhar. O freio de antes (3 falhas e 300 s
        # de espera, `_count_failure`) se repetia a cada intervalo vencido: uma conta real recebeu seis envios de senha
        # em 4h25 em 18/09. O intervalo continua só para falha ANTES do envio, em que a senha não saiu da máquina.
        self._parar_login(conta, instance_id, verdict.detail, falhou=True)

    def _count_failure(self, conta: ContaDaSessao, instance_id: str) -> None:
        """Toda falha repetível conta para o teto. Sem isso, um erro que se repete viraria laço infinito de login.

        Contar NÃO muda o estado da credencial: o status gravado é o que já estava. Revisão do pacote (ADR-055): isto
        gravava `active` sem condição, e a falha antes do envio de um "Conectar" (a pessoa tenta com a credencial em
        `review`, e o toque em Entrar não chega) soltava o login parado — a volta seguinte do agendador enviava a
        senha sem ninguém ter visto um login dar certo. O mesmo na corrida entre aparelhos: o B falhando antes do
        envio depois que o A pôs a credencial em `review` (ou `invalid`). De `review` só se sai guardando a senha de
        novo (`set_account_credential`), com um login que confirma a conta (`_apply_verdict`) ou com a conta conferida
        aberta no aparelho (`_reconciliar_revisao`, 29.64)."""
        cred = self.repo.account_credential_row(conta.profile_id, conta.id)
        if cred is None:
            return
        ajustes = self.ajustes
        status = cred["status"] or "active"
        falhas = (cred["failed_attempts"] or 0) + 1
        if falhas >= int(ajustes.max_auth_attempts):
            espera = now().timestamp() + float(ajustes.auth_cooldown_s)
            self.repo.mark_account_credential(conta.profile_id, conta.id, status=status, failed_attempts=falhas,
                                              blocked_until=_iso(espera))
            self.bus.emit("log", f"{instance_id}: {falhas} tentativas de login sem sucesso; aguardando "
                                 f"{ajustes.auth_cooldown_s}s antes de tentar de novo", level="warn",
                          instance_id=instance_id)
        else:
            self.repo.mark_account_credential(conta.profile_id, conta.id, status=status, failed_attempts=falhas,
                                              blocked_until=None)

    def _parar_login(self, conta: ContaDaSessao, instance_id: str, motivo: str, *, falhou: bool) -> None:
        """O login automático desta conta para até uma pessoa olhar (ADR-055): a credencial vai a `review`, que o
        agendador e a porta de sessão respeitam. `falhou` = houve um envio sem sucesso (soma nas falhas); o teto
        diário para sem ser falha."""
        cred = self.repo.account_credential_row(conta.profile_id, conta.id)
        if cred is None:
            return
        falhas = (cred["failed_attempts"] or 0) + (1 if falhou else 0)
        self.repo.mark_account_credential(conta.profile_id, conta.id, status=CREDENCIAL_EM_REVISAO,
                                          failed_attempts=falhas, blocked_until=None)
        self.bus.emit("log", f"{instance_id}: {motivo_do_login_parado(self.conhecimento.rotulo)} ({motivo})",
                      level="error", instance_id=instance_id,
                      data={"profile_id": conta.profile_id, "account_id": conta.id, "reason": "login_parado",
                            "detail": motivo[:300]})

    def _reconciliar_revisao(self, conta: ContaDaSessao, instance_id: str) -> None:
        """29.64: a credencial em `review` volta a `active` quando a conta CONFERIDA está aberta no aparelho.

        O `review` diz "o login parou sem ninguém ver dar certo" (ADR-055). A conta lida e conferida na tela é ver dar
        certo: foi o que ficou faltando no android-13 (04/10), em que um toque manual concluiu o envio incerto, a
        sessão virou `session_ready` e a credencial ficou em `review`, travando o login automático dos outros
        aparelhos da persona sem motivo. `invalid` NÃO sai daqui: a senha guardada foi recusada, e a sessão aberta à
        mão não a conserta. Vale também na leitura sem login (`observe_only`): é o registro de um fato visto na tela,
        não uma ação no aparelho."""
        cred = self.repo.account_credential_row(conta.profile_id, conta.id)
        if cred is None or cred["status"] != CREDENCIAL_EM_REVISAO:
            return
        # Só o estado e as falhas mudam: uma pausa em vigor (`blocked_until`) é proteção e fica até vencer.
        self.repo.mark_account_credential(conta.profile_id, conta.id, status="active", failed_attempts=0,
                                          blocked_until=cred["blocked_until"])
        self.bus.emit("log", f"{instance_id}: {_como_conta(conta.handle)} está aberta e conferida no aparelho; o login "
                             "automático desta conta volta a valer", level="info", instance_id=instance_id,
                      data={"profile_id": conta.profile_id, "account_id": conta.id, "reason": "login_reconciliado"})

    def _login_em_revisao(self, conta: ContaDaSessao) -> bool:
        cred = self.repo.account_credential_row(conta.profile_id, conta.id)
        return cred is not None and cred["status"] == CREDENCIAL_EM_REVISAO

    def _teto_diario(self, conta: ContaDaSessao) -> str | None:
        """Motivo da recusa quando esta conta já teve `max_logins_per_day` envios de senha nas últimas 24 h."""
        limite = int(self.ajustes.max_logins_per_day)
        desde = now() - JANELA_DO_TETO_DIARIO
        envios = 0
        # Mais recentes primeiro: para no primeiro fora da janela (sem data legível, conta — na dúvida, foi recente).
        # O limite da leitura cobre as falhas ANTES do envio (no máximo `max_auth_attempts` por intervalo), que não
        # contam mas ocupam linhas. Só as tentativas DESTA conta: os envios do outro app não gastam o teto deste.
        for tentativa in self.repo.auth_attempts(conta.profile_id, limit=500, account_id=conta.id):
            quando = parse_iso(tentativa["started_at"])
            if quando is not None and quando < desde:
                break
            if (tentativa["stage"] or "") not in ETAPAS_ANTES_DO_ENVIO:
                envios += 1
        if envios < limite:
            return None
        return (f"teto diário de logins atingido: {envios} envio(s) de senha nas últimas 24 h para esta conta (limite "
                f"{limite}); uma conta que precisa entrar tantas vezes está perdendo a sessão — uma pessoa precisa "
                "olhar antes de qualquer novo login")

    def _conta_parada(self, conta: ContaDaSessao) -> str | None:
        """O perfil não está `active`: `blocked` (a plataforma travou a conta, ADR-029/055) ou `disabled` (o dono
        pausou). Nos dois, a senha não é digitada: insistir numa conta bloqueada é o que a faz perder de vez. O status
        é da PERSONA e só o desafio na conta ÂNCORA o põe em `blocked`; o desafio noutro app para só aquela conta, pela
        credencial em `review` (item 23.5, `session_rules.aplicar_desafio`), que `_parada_no_meio` e a porta de sessão
        respeitam. A mensagem nomeia a conta desta chamada."""
        perfil = self.repo.profile_row(conta.profile_id)
        if perfil is None:
            return None
        status = perfil["status"] or "active"
        if status == "active":
            return None
        arroba = _como_conta(conta.handle)
        if status == "blocked":
            return (f"a conta {arroba} está bloqueada: a senha nunca é digitada numa conta bloqueada (ADR-055); uma "
                    "pessoa confere a conta no aparelho e reativa o perfil na tela dele")
        return f"o perfil {arroba} está '{status}': nenhum login é feito até uma pessoa reativá-lo na tela do perfil"

    def _parada_no_meio(self, conta: ContaDaSessao, *, automatic: bool) -> tuple[str, str] | None:
        """Relida no meio do login (antes da senha e antes do toque em Entrar): (motivo, etapa) para interromper.

        - O perfil parou (`_conta_parada`): vale para qualquer chamada — conta bloqueada nunca recebe a senha.
        - O login automático parou (credencial em `review`): vale só para o automático, que já passou pela porta do
          começo. Revisão do pacote: com a mesma conta em dois aparelhos, o envio sem sucesso do A põe a credencial
          em `review` enquanto o B digita o usuário; seguir enviaria a senha uma segunda vez sem ninguém ter olhado
          (regra (e) do ADR-055). O "Conectar" é a pessoa olhando, e segue."""
        if (parada := self._conta_parada(conta)) is not None:
            return parada, "account_blocked"
        if automatic and self._login_em_revisao(conta):
            return motivo_do_login_parado(self.conhecimento.rotulo), "login_parado"
        return None

    def _abortar_pela_conta(self, conta: ContaDaSessao, attempt: int, parada: str, etapa: str,
                            instance_id: str) -> AuthResult:
        """A conta (ou o login automático dela) parou no meio do login: nada é enviado. Não conta falha nem grava a
        sessão — a senha não saiu, e a sessão (um desafio, talvez) é o que a pessoa precisa ver. A `etapa` está em
        `ETAPAS_ANTES_DO_ENVIO`: a interrupção não gasta o teto diário."""
        self.repo.finish_auth_attempt(conta.profile_id, attempt, outcome=Outcome.RETRYABLE.value, detail=parada,
                                      stage=etapa)
        log.warning("%s: login interrompido sem enviar a senha — %s", instance_id, parada)
        return self._recusa_sem_tocar(conta, instance_id, parada)

    def _sessao(self, conta: ContaDaSessao, instance_id: str) -> Row | None:
        """A sessão DESTA conta neste aparelho — nunca a da conta âncora do perfil (a porta de outro app)."""
        return self.repo.account_session_row(conta.profile_id, conta.id, instance_id)

    def _recusa_sem_tocar(self, conta: ContaDaSessao, instance_id: str, motivo: str) -> AuthResult:
        """Recusa sem gravar a sessão. O desfecho acompanha o que está gravado — um desafio continua desafio para
        quem chama —, e senão é o terminal de sempre das recusas (nenhum deles gera nova tentativa sozinho)."""
        sess = self._sessao(conta, instance_id)
        status = SessionStatus(sess["status"]) if sess is not None else SessionStatus.unknown
        desfecho = {SessionStatus.auth_challenge: Outcome.AUTH_CHALLENGE,
                    SessionStatus.wrong_account: Outcome.WRONG_ACCOUNT}.get(status, Outcome.INVALID_CREDENTIAL)
        return AuthResult(desfecho, motivo, session_status=status)

    def _needs_person(self, conta: ContaDaSessao, instance_id: str) -> tuple[Outcome, str] | None:
        """Estado que só uma pessoa resolve: o agendador não insiste, para não virar laço nem bloquear a conta.
        A sessão é do par (conta, aparelho): a lida é a DESTE aparelho."""
        sess = self._sessao(conta, instance_id)
        if sess is None:
            return None
        if sess["status"] == SessionStatus.auth_challenge.value:
            return Outcome.AUTH_CHALLENGE, (sess["detail"] or self.conhecimento.textos.desafio_pendente)
        if sess["status"] == SessionStatus.wrong_account.value:
            return Outcome.WRONG_ACCOUNT, (sess["detail"] or "o aparelho está logado em outra conta")
        return None

    def _challenge(self, conta: ContaDaSessao, instance_id: str, motivo: str,
                   trava: ContaTravada | None = None) -> AuthResult:
        """Desafio na tela: nada é tocado, a sessão vai a `auth_challenge` e a pessoa é chamada. O subtipo decide o
        destino do perfil (ADR-055, em `_save`) e vai, com o trecho que casou, na sessão e no evento."""
        detail = self.conhecimento.textos.desafio + (f" [{trava.descrever()}]" if trava is not None else "")
        self._save(conta, instance_id, SessionStatus.auth_challenge, detail=detail, trava=trava)
        self.bus.emit("log", f"{instance_id}: {detail} ({motivo})", level="warn", instance_id=instance_id,
                      data=_dados_da_trava(conta.profile_id, trava))
        return AuthResult(Outcome.AUTH_CHALLENGE, detail, session_status=SessionStatus.auth_challenge)

    def _fora_do_primeiro_plano(self, rt: DeviceRuntime, conta: ContaDaSessao, detail: str) -> AuthResult:
        """OUTRO app na frente (o launcher, quase sempre): o app não chegou ao primeiro plano, ou caiu no meio da
        leitura da conta.

        Não é "tela não reconhecida": a tela que está na frente não é do app — ou nenhuma tela dele foi lida, ou a
        que foi lida era de casa e o app caiu depois. Contar isso no `unknown_streak` (achado #104) transformava
        lentidão do aparelho em caso de pessoa — r-20260928195344-02ee9e: o convidado do android-06 (2 vCPU) saturado
        demorava a trazer o app à frente, e três leituras do launcher bloqueavam o perfil pedindo que alguém
        "identificasse a tela". A sessão fica `unknown` SEM somar (a sequência de telas do app é interrompida), a
        porta tenta de novo sozinha, e o caso vai para a saúde do APARELHO: o cartão (`attention`) e o histórico —
        este uma vez por entrada no estado, não a cada tentativa.

        O cartão segue a regra dos outros avisos dele (pressão do convidado, relógio, internet): só ocupa o cartão
        vazio ou o que já é deste aviso. No android-06 a pressão do convidado costuma estar lá, e ela é a causa — o
        aviso daqui não a atropela; o histórico registra do mesmo jeito.
        """
        anterior = self._sessao(conta, rt.id)
        self._save(conta, rt.id, SessionStatus.unknown, detail=detail)
        if anterior is None or anterior["detail"] != detail:
            self.bus.emit("log", f"{rt.id}: {detail} — é o aparelho (lento ou o app caindo na abertura), não uma tela "
                                 "desconhecida; a conferência da conta tenta de novo sozinha", level="warn",
                          instance_id=rt.id)
        if rt.attention is None or rt.attention.startswith(AVISO_FORA_DA_FRENTE):
            self.devices.marcar_atencao(rt, f"{AVISO_FORA_DA_FRENTE}: {detail}. A conferência da conta tenta de novo "
                                            "sozinha; se continuar, o aparelho está lento demais para o app ou o app "
                                            "cai ao abrir — reinicie o aparelho.")
        return AuthResult(Outcome.UNCERTAIN, detail, session_status=SessionStatus.unknown)

    def _app_voltou_a_frente(self, rt: DeviceRuntime) -> None:
        """O app esteve na frente nesta chamada: o aviso "fora do primeiro plano" deste motor sai do cartão. Sem isto,
        UMA abertura lenta deixava o alarme no cartão para sempre (só um reinício o limpava). Aviso de outro assunto
        fica: não é deste motor."""
        if rt.attention is not None and rt.attention.startswith(AVISO_FORA_DA_FRENTE):
            rt.attention = None
            self.devices.publish(rt, f"{rt.id}: {self.conhecimento.rotulo} voltou a abrir na frente")

    def _blocked_reason(self, conta: ContaDaSessao) -> str | None:
        """O que na CREDENCIAL impede o login (quem chama grava a recusa na sessão). O status do PERFIL (`blocked`,
        ADR-055) é conferido antes, por `_conta_parada`, e de novo antes da senha e do envio: aquela recusa não pode
        gravar a sessão por cima de um desafio, e a leitura (`observe_only`) de uma conta bloqueada continua valendo."""
        cred = self.repo.account_credential_row(conta.profile_id, conta.id)
        if cred is None:
            return "não há credencial cadastrada para este perfil"
        if cred["consent_at"] is None:
            # ADR-040: o consentimento é por conta e vale para o provedor como para o `type_secret`. Sem a marca, a
            # senha fica guardada e não é digitada por ninguém.
            return ("a senha guardada ainda não tem o consentimento para a automação digitá-la; marque-o na conta "
                    "do perfil")
        if cred["status"] == "invalid":
            return self.conhecimento.textos.senha_recusada
        ate = parse_iso(cred["blocked_until"])
        if ate and ate > now():
            return f"aguardando o intervalo entre tentativas (até {cred['blocked_until']})"
        return None

    def _save(self, conta: ContaDaSessao, instance_id: str, status: SessionStatus, *, observed: str | None = None,
              verified_at: str | None = None, detail: str | None = None, reobserved: bool = False,
              trava: ContaTravada | None = None) -> None:
        profile_id = conta.profile_id
        anterior = self._sessao(conta, instance_id)
        self.repo.set_account_session(profile_id, conta.id, instance_id, status=status, observed_handle=observed,
                                      verified_at=verified_at, detail=detail, reobserved=reobserved)
        if status is SessionStatus.session_ready and conta.ancora:
            # O "verificado em" do cartão do perfil é o da conta âncora (é a sessão que o cartão mostra); a conta de
            # outro app tem o dela, na sessão da conta.
            self.repo.update_profile(profile_id, {"last_verified_at": now_iso()})
        if status is SessionStatus.auth_challenge:
            # A regra do desafio (item 23.5), com o rótulo do app declarado nas mensagens. Na conta âncora, só a
            # TRAVADA (ADR-055) bloqueia o perfil: o código de login/2FA pede uma pessoa sem bloquear — duas contas reais
            # passaram por ele em 18/09 e seguem vivos. Sem subtipo é regra declarada por sinal, fora do detector:
            # vale como trava. Na conta de outro app, qualquer desafio para só ela (P9). A quarentena é protegida por
            # dentro: falhar não pode impedir o evento da fila logo abaixo. A conta travada é a DESTE app (o @ e o
            # app dela), não o @ de cadastro do perfil.
            aplicar_desafio(self.repo, self.bus, profile_id=profile_id, account_id=conta.id, app_id=conta.app_id,
                            handle=conta.handle, ancora=conta.ancora,
                            travada=trava is None or trava.subtipo == SUBTIPO_CONTA_TRAVADA, instance_id=instance_id,
                            anterior_status=anterior["status"] if anterior is not None else None, detail=detail,
                            evidencia=trava.trecho if trava is not None else (detail or "")[:300],
                            app_label=self.conhecimento.rotulo, visto_por="motor de sessão")
        if self.repo.account_row(profile_id, conta.id) is None:
            return          # a trava confirmada retirou a conta (29.23): não há item de fila para uma conta que saiu
        emit_needs_person_change(self.bus, profile_id=profile_id, instance_id=instance_id, status=status,
                                 anterior_status=anterior["status"] if anterior is not None else None,
                                 detail=detail, account_id=conta.id,
                                 no_teto=self.repo.unknown_no_teto(self._sessao(conta, instance_id), instance_id),
                                 anterior_no_teto=self.repo.unknown_no_teto(anterior, instance_id))

    @staticmethod
    def _status_for(outcome: Outcome) -> SessionStatus:
        return {
            Outcome.SESSION_READY: SessionStatus.session_ready,
            Outcome.INVALID_CREDENTIAL: SessionStatus.auth_required,
            Outcome.AUTH_CHALLENGE: SessionStatus.auth_challenge,
            Outcome.WRONG_ACCOUNT: SessionStatus.wrong_account,
        }.get(outcome, SessionStatus.unknown)

    # ------------------------------------------------------------------ operações no aparelho
    async def _ensure_automation(self, rt: DeviceRuntime) -> bool:
        for tentativa in range(AUTOMATION_TRIES):
            if await self.devices.ensure_automation(rt):
                return True
            if tentativa < AUTOMATION_TRIES - 1:
                await asyncio.sleep(AUTOMATION_WAIT_S)
        return False

    async def _open_app(self, rt: DeviceRuntime, aberturas: _Aberturas | None = None) -> None:
        """Abre o app e espera o foco. Com `aberturas`, a morte por ANR na partida tem sinal próprio: a 1ª da chamada
        reabre UMA vez aqui; a 2ª levanta `AppParouDeResponder` (quem chama devolve o motivo, não "tela não
        reconhecida")."""
        pacote, ajustes = self.package, self.ajustes
        while True:
            try:
                await rt.executor.run(rt.adb.start_app, pacote, None, timeout=60,
                                      label=f"abrir {self.conhecimento.rotulo}")
            except Exception:  # noqa: BLE001 - abrir pode falhar; a classificação da tela decide o que fazer
                log.warning("%s: não foi possível abrir %s", rt.id, pacote)
            # Sem esperar o app aparecer, uma abertura a frio seria classificada como "outro app em primeiro plano" e
            # a conexão sairia incerta sem motivo real. Se não aparecer no prazo, a classificação diz o que está na
            # tela.
            if await wait_for_focus(rt, pacote, deadline_s=float(ajustes.open_timeout_s), poll_s=self.focus_poll_s):
                break
            log.warning("%s: %s não chegou ao primeiro plano em %.0f s", rt.id, pacote, float(ajustes.open_timeout_s))
            # Launcher com morte recente é o app MORRENDO, não "tela não reconhecida": com `hide_error_dialogs=1`
            # todo ANR fecha o app sem diálogo (r-20260928165254-e31953 e r-20260928195344-02ee9e, android-06).
            if aberturas is None or not await self._morreu_por_anr(rt, aberturas):
                break
            if len(aberturas.mortes) >= 2:
                raise AppParouDeResponder(motivo_de_anr(self.conhecimento.rotulo, rt.id))
            log.warning("%s: %s parou de responder (ANR) e foi fechado; reabrindo uma vez", rt.id, pacote)
        await asyncio.sleep(float(ajustes.settle_s))

    async def _morreu_por_anr(self, rt: DeviceRuntime, aberturas: _Aberturas) -> bool:
        """Houve morte NOVA por ANR desde o início desta chamada? Não deu para ler = não (o caminho de antes: a
        classificação diz o que está na tela). A idade é medida no relógio do convidado, com a mesma janela do
        executor (`StepExecutor._mortes_por_anr`): o decorrido mais o prazo da leitura na ida, o decorrido na volta."""
        prazo = 30.0
        janela = time.monotonic() - aberturas.inicio + prazo
        try:
            mortes = await rt.executor.run(lambda: rt.io.app_deaths(self.package, within_s=janela), timeout=prazo,
                                           label="mortes do app")
        except (DriverError, AttributeError, TypeError) as exc:
            log.info("%s: sem ler as mortes de %s agora (%s)", rt.id, self.package, exc)
            return False
        decorrido = time.monotonic() - aberturas.inicio
        novas = {m.chave for m in mortes if m.anr and m.idade_s <= decorrido} - aberturas.mortes
        aberturas.mortes |= novas
        return bool(novas)

    def _parou_de_responder(self, rt: DeviceRuntime, motivo: str) -> AuthResult:
        """A 2ª morte por ANR: nada foi digitado e a credencial nem foi usada, então não conta no teto de tentativas
        (`_count_failure`) — é o aparelho, como "a sessão de automação não ficou pronta". Vai para a saúde do aparelho
        com a regra dos outros avisos do cartão: só ocupa o cartão vazio ou o que já é dele."""
        self.bus.emit("log", f"{rt.id}: {motivo}", level="warn", instance_id=rt.id)
        if rt.attention is None or AVISO_DE_ANR in rt.attention:
            self.devices.marcar_atencao(rt, motivo[:1].upper() + motivo[1:])
        return AuthResult(Outcome.RETRYABLE, motivo)

    async def _observe(self, rt: DeviceRuntime) -> tuple[UiTree, str | None]:
        # Só árvore e pacote: o login determinístico nunca manda imagem ao modelo, então não há screencap nem
        # codificação a pagar aqui (contrato C1 do adendo v0.20: árvore primeiro, imagem só quando pedida).
        obs = await self.devices.observe(rt, timeout=float(self.ajustes.verify_timeout_s), imagem=False)
        return obs.tree, obs.package

    async def _locale(self, rt: DeviceRuntime) -> str | None:
        try:
            idioma: str | None = await rt.executor.run(rt.adb.getprop, "ro.product.locale", timeout=15, label="idioma")
            return idioma
        except Exception:  # noqa: BLE001
            return None

    async def _tap(self, rt: DeviceRuntime, x: int, y: int) -> None:
        await rt.executor.run(rt.io.tap, x, y, timeout=30, label="toque")

    async def _voltar(self, rt: DeviceRuntime) -> None:
        """A tecla Voltar: a recusa de um diálogo de outro pacote declarado em `dispensa.voltar` (item 23.8)."""
        await rt.executor.run(rt.io.press_key, "back", timeout=30, label="voltar")

    async def _fill_username(self, rt: DeviceRuntime, campo: UiElement, valor: str,
                             reler: Callable[[UiTree], UiElement | None]) -> bool:
        """Preenche o campo de usuário (ou o do identificador, no login em etapas) e CONFERE o que ficou lá. Devolve
        False se não bater — e aí nada é enviado. `reler` acha o MESMO campo numa tela relida.

        Tenta mais de uma vez de propósito. `mobile: type` digita no elemento em FOCO, e há campo de usuário com
        variantes: quando ele está vazio com placeholder (a que apareceu depois de reinstalar o primeiro app operado),
        o primeiro toque às vezes não foca e a digitação cai no vazio — o login abortava em "username_mismatch" com o
        campo em branco. A cada tentativa a tela é relida, então a posição usada é a ATUAL (o teclado sobe e empurra
        tudo). A conferência continua valendo para todas: só se envia com o campo exatamente igual ao esperado.
        """
        esperado = valor.strip().lstrip("@").lower()
        alvo: UiElement | None = campo
        for tentativa in range(FILL_TRIES):
            if alvo is None:
                return False
            await self._tap(rt, *alvo.center)
            await rt.executor.run(lambda: rt.io.type_text(valor, clear_first=True), timeout=30, label="usuário")
            await asyncio.sleep(0.6)

            tree, _ = await self._observe(rt)
            atual = reler(tree)
            if atual is None:
                return False
            if (atual.text or "").strip().lstrip("@").lower() == esperado:
                return True
            if tentativa + 1 < FILL_TRIES:
                log.warning("%s: o usuário não ficou no campo (tentativa %d); relendo a tela e repetindo",
                            rt.id, tentativa + 1)
            alvo = atual                      # posição nova: a tela pode ter subido com o teclado
        return False

    async def _fill_password(self, rt: DeviceRuntime, k: ConhecimentoDeSessao, conta: ContaDaSessao,
                             secret_ref: str) -> None:
        """A senha só existe entre o cofre e o driver, por um caminho que não gera ação nem histórico.

        E só cai no campo de senha do app DA CONTA, ou do navegador declarado num site da conta (item 23.6): o destino
        é conferido na MESMA árvore em que o campo é achado, no instante de focar e de novo depois de digitar. Fora
        dele o campo "não existe" para o canal, que recusa com a mensagem fixa dele (nunca com o valor)."""
        pacote_da_frente: list[str | None] = [None]

        async def observe_tree() -> UiTree:
            tree, package = await self._observe(rt)
            pacote_da_frente[0] = package
            return tree

        def localizar(tree: UiTree) -> UiElement | None:
            campo = k.campo_de_senha(tree)
            if campo is None or not self._destino(k, tree, campo.package or pacote_da_frente[0]).permitido:
                return None
            return campo

        await self.sensitive.fill(call=rt.executor.run, io=rt.io, observe=observe_tree, locate=localizar,
                                  secret=lambda: self.secrets.get_secret(secret_ref))

def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.") + \
        f"{int(timestamp % 1 * 1000):03d}Z"
