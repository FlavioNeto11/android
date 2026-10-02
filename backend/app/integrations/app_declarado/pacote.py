"""O pacote de um app, descoberto como DADO (ADR-052): o `AppManifest` de cada pasta de `app/conhecimento/apps/`.

Uma pasta por pacote Android, com os arquivos que o app tiver:

- `app.yaml` (obrigatório): quem é o app, se tem conta gerenciada, se exige perfil e internet, que texto cada ação
  escreve, que leitura vira fala de outra pessoa e como ler a tela para o rascunho;
- `catalogo.yaml`: as ações (fatia 2, `planning/capabilities.carregar_catalogo`);
- `telas.yaml` + `sessao.yaml`: telas, estado conhecido e login declarado (fatias 1 e 3, `conhecimento.py`).

O registro de apps (`modules/applications/infrastructure/registry.py`) chama `descobrir()` na primeira consulta: um
app novo entra criando a pasta, sem uma linha de Python e sem tocar no registro. Nada aqui conhece app nenhum.

Arquivo errado falha na CARGA, com o caminho do campo, e derruba a descoberta inteira: um pacote pela metade (catálogo
sem conta, conta sem login declarado) seria pior que nenhum, porque o núcleo passaria a confiar nele.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from ...automation import leitura_de_tela
from ...devices.emulator import RENDERIZADORES, normalizar_renderizador
from ...modules.applications.domain.definition import AppDefinition
from ...modules.applications.infrastructure.registry import AppManifest
from ...modules.identity.infrastructure.sessions import SessionDeps, SessionProviderFactory
from ...planning.capabilities import CONHECIMENTO_DE_APPS, CapabilityCatalog, carregar_catalogo, catalogo_do_pacote
from . import conhecimento as declarado
from .conhecimento import ConhecimentoDeSessao
from .sessao import SessaoDeclarada

#: A raiz dos pacotes. É a mesma do catálogo e do login (uma só, para não haver duas verdades sobre onde o app mora).
PASTA_DOS_APPS = CONHECIMENTO_DE_APPS

#: Os campos que o `app.yaml` aceita. Campo fora daqui é erro de digitação que seria ignorado em silêncio.
_CAMPOS = frozenset({"app", "nome", "rotulo", "provedor_de_sessao", "precisa_de_perfil", "precisa_de_internet",
                     "ancora_do_perfil", "links_de_perfil", "tipos_de_texto", "leituras_de_conversa", "leitura",
                     "renderizador_recusado", "atividades_de_conta_perdida", "limpar_ao_retirar"})
_PACOTE_ANDROID = re.compile(r"^[a-zA-Z][\w]*(\.[a-zA-Z][\w]*)+$")


class PacoteInvalido(ValueError):
    """O `app.yaml` (ou a combinação de arquivos da pasta) não se sustenta: recusa na carga."""


def _texto(bruto: object, onde: str, *, opcional: bool = False) -> str | None:
    if bruto is None and opcional:
        return None
    if not isinstance(bruto, str) or not bruto.strip():
        raise PacoteInvalido(f"{onde}: texto não vazio")
    return bruto.strip()


def _booleano(bruto: object, onde: str) -> bool:
    if bruto is None:
        return False
    if not isinstance(bruto, bool):
        raise PacoteInvalido(f"{onde}: true ou false")
    return bruto


def _pares(bruto: object, onde: str) -> tuple[tuple[str, str], ...]:
    """Um mapa ação → tipo, como pares (a forma que `AppDefinition` guarda)."""
    if bruto is None:
        return ()
    if not isinstance(bruto, dict):
        raise PacoteInvalido(f"{onde}: esperava um mapa AÇÃO: tipo")
    pares: list[tuple[str, str]] = []
    for chave, valor in bruto.items():
        if not isinstance(chave, str) or not isinstance(valor, str) or not chave or not valor:
            raise PacoteInvalido(f"{onde}.{chave}: ação e tipo são textos")
        pares.append((chave, valor))
    return tuple(pares)


def _textos(bruto: object, onde: str) -> tuple[str, ...]:
    if bruto is None:
        return ()
    if not isinstance(bruto, list) or not all(isinstance(t, str) and t.strip() for t in bruto):
        raise PacoteInvalido(f"{onde}: lista de textos")
    return tuple(str(t).strip().lower() for t in bruto)


def _links(bruto: object, onde: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """`links_de_perfil`: `hosts` (domínios do link de perfil) e `reservados` (primeiro segmento que não é perfil)."""
    if bruto is None:
        return (), ()
    if not isinstance(bruto, dict) or set(bruto) - {"hosts", "reservados"}:
        raise PacoteInvalido(f"{onde}: mapa com `hosts` e `reservados`")
    hosts = _textos(bruto.get("hosts"), f"{onde}.hosts")
    if not hosts:
        raise PacoteInvalido(f"{onde}.hosts: ao menos um domínio")
    return hosts, _textos(bruto.get("reservados"), f"{onde}.reservados")


def _renderizadores(bruto: object, onde: str) -> tuple[str, ...]:
    """`renderizador_recusado`: os renderizadores do emulador em que o app NÃO roda, pelo nome canônico (29.11).

    Só os que o emulador seleciona de fato (`devices.emulator.RENDERIZADORES`); o apelido `swiftshader_indirect` vale
    por `swiftshader`. Valor fora disso é recusado na carga: um nome escrito errado viraria um requisito que nunca
    casa com aparelho nenhum, e o app seria instalado justamente onde ele derruba o emulador. Recusar TODOS também é
    erro: isso é dizer que o app não roda em emulador, e não um requisito de renderizador.
    """
    if bruto is None:
        return ()
    conhecidos = ", ".join(RENDERIZADORES)
    if not isinstance(bruto, list) or not all(isinstance(r, str) for r in bruto):
        raise PacoteInvalido(f"{onde}: lista de renderizadores ({conhecidos})")
    nomes = tuple(dict.fromkeys(normalizar_renderizador(r) or "" for r in bruto))
    estranhos = [r for r in nomes if r not in RENDERIZADORES]
    if estranhos:
        raise PacoteInvalido(f"{onde}: renderizador desconhecido {', '.join(repr(r) for r in estranhos)}"
                             f" (conhecidos: {conhecidos})")
    if set(nomes) == set(RENDERIZADORES):
        raise PacoteInvalido(f"{onde}: recusa todos os renderizadores ({conhecidos}); sobra nenhum para o app rodar")
    return nomes


def definicao_de_dados(dados: object, onde: str = "app.yaml") -> AppDefinition:
    """A `AppDefinition` de um `app.yaml` já lido."""
    if not isinstance(dados, dict):
        raise PacoteInvalido(f"{onde}: esperava um mapa")
    desconhecidos = sorted(str(k) for k in dados if k not in _CAMPOS)
    if desconhecidos:
        raise PacoteInvalido(f"{onde}: campo desconhecido {', '.join(desconhecidos)}"
                             f" (aceitos: {', '.join(sorted(_CAMPOS))})")
    pacote = _texto(dados.get("app"), f"{onde}: app") or ""
    if not _PACOTE_ANDROID.match(pacote):
        raise PacoteInvalido(f"{onde}: app {pacote!r} não é um pacote Android")
    nome = _texto(dados.get("nome"), f"{onde}: nome") or pacote
    hosts, reservados = _links(dados.get("links_de_perfil"), f"{onde}: links_de_perfil")
    return AppDefinition(package=pacote, name=nome,
                         session_provider=_texto(dados.get("provedor_de_sessao"), f"{onde}: provedor_de_sessao",
                                                 opcional=True),
                         needs_profile=_booleano(dados.get("precisa_de_perfil"), f"{onde}: precisa_de_perfil"),
                         requires_internet=_booleano(dados.get("precisa_de_internet"), f"{onde}: precisa_de_internet"),
                         label=_texto(dados.get("rotulo"), f"{onde}: rotulo", opcional=True) or "",
                         text_kinds=_pares(dados.get("tipos_de_texto"), f"{onde}: tipos_de_texto"),
                         conversation_reads=_pares(dados.get("leituras_de_conversa"), f"{onde}: leituras_de_conversa"),
                         profile_anchor=_booleano(dados.get("ancora_do_perfil"), f"{onde}: ancora_do_perfil"),
                         profile_link_hosts=hosts, profile_link_reserved=reservados,
                         refused_renderers=_renderizadores(dados.get("renderizador_recusado"),
                                                           f"{onde}: renderizador_recusado"),
                         lost_account_activities=_textos(dados.get("atividades_de_conta_perdida"),
                                                         f"{onde}: atividades_de_conta_perdida"),
                         clear_on_account_retire=_booleano(dados.get("limpar_ao_retirar"),
                                                           f"{onde}: limpar_ao_retirar"))


def _ler(caminho: Path) -> object:
    try:
        return yaml.safe_load(caminho.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PacoteInvalido(f"{caminho}: YAML inválido ({exc})") from exc


def _catalogo(pasta: Path, pacote: str, padrao: bool) -> CapabilityCatalog | None:
    if padrao:                                  # a mesma instância que o resto do processo já leu (cache)
        return catalogo_do_pacote(pacote)
    caminho = pasta / "catalogo.yaml"
    if not caminho.is_file():
        return None
    catalogo = carregar_catalogo(caminho)
    if catalogo.package != pacote:
        raise PacoteInvalido(f"{caminho}: `app` é {catalogo.package!r}, mas o app.yaml diz {pacote!r}")
    return catalogo


def _sessao(pasta: Path, pacote: str, padrao: bool) -> ConhecimentoDeSessao:
    k = declarado.do_app(pacote) if padrao else declarado.carregar(pasta)
    if k.app != pacote:
        raise PacoteInvalido(f"{pasta}: o sessao.yaml é de {k.app!r}, mas o app.yaml diz {pacote!r}")
    return k


def fabrica_de_sessao(k: ConhecimentoDeSessao) -> SessionProviderFactory:
    """A fábrica do provedor de sessão: o motor genérico com o conhecimento deste app. A senha só passa pelo canal
    de entrada sensível (ADR-025/040), como em qualquer app declarado."""

    def sessao(deps: SessionDeps) -> SessaoDeclarada:
        return SessaoDeclarada(k, deps.cfg, deps.devices, deps.repo, deps.secrets, deps.sensitive_input, deps.bus)

    return sessao


def manifesto_da_pasta(pasta: Path, *, raiz: Path | None = None) -> AppManifest:
    """O manifesto de UMA pasta de app. A pasta tem o nome do pacote, e o `app.yaml` tem de dizer o mesmo."""
    brutos = _ler(pasta / "app.yaml")
    definicao = definicao_de_dados(brutos, str(pasta / "app.yaml"))
    pacote = definicao.package
    if pasta.name != pacote:
        raise PacoteInvalido(f"{pasta}: a pasta traz o app.yaml de {pacote!r}")
    padrao = (raiz or pasta.parent).resolve() == PASTA_DOS_APPS.resolve()
    tem_sessao = (pasta / "sessao.yaml").is_file()
    if tem_sessao != (definicao.session_provider is not None):
        # Um sem o outro deixaria a porta de sessão e a invalidação respondendo coisas diferentes para o mesmo app.
        raise PacoteInvalido(f"{pasta}: `provedor_de_sessao` e `sessao.yaml` vêm juntos (conta gerenciada) ou não"
                             " vêm")
    leitura = brutos.get("leitura") if isinstance(brutos, dict) else None
    try:
        tela = leitura_de_tela.de_dados(leitura) if leitura is not None else None
    except leitura_de_tela.LeituraInvalida as exc:
        raise PacoteInvalido(f"{pasta / 'app.yaml'}: {exc}") from exc
    return AppManifest(definition=definicao, catalog=_catalogo(pasta, pacote, padrao), screen=tela,
                       session=fabrica_de_sessao(_sessao(pasta, pacote, padrao)) if tem_sessao else None)


def descobrir(raiz: Path | None = None) -> tuple[AppManifest, ...]:
    """Os manifestos de todos os pacotes sob `raiz` (padrão: `app/conhecimento/apps/`), em ordem de pasta. Pasta sem
    `app.yaml` não é app (fica de fora); pasta com `app.yaml` inválido derruba a descoberta."""
    base = raiz or PASTA_DOS_APPS
    if not base.is_dir():
        return ()
    manifestos = tuple(manifesto_da_pasta(p, raiz=base) for p in sorted(base.iterdir())
                       if p.is_dir() and (p / "app.yaml").is_file())
    ancoras = [m.definition.package for m in manifestos if m.definition.profile_anchor]
    if len(ancoras) > 1:
        # A persona tem UM app âncora (onde vivem a credencial e a sessão dela); mais de um é o item 12.3, que é
        # decisão do dono, e não uma escolha que a descoberta faça pela ordem das pastas.
        raise PacoteInvalido(f"{base}: mais de um app âncora do perfil ({', '.join(ancoras)}); item 12.3")
    return manifestos
