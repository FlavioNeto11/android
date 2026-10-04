"""Pacote de app como DADO, de ponta a ponta (ADR-052, integração das fatias 2–4).

Um app que o núcleo nunca viu (um cliente de e-mail) entra criando UMA pasta de arquivos: `app.yaml`, `telas.yaml`,
`sessao.yaml` e `catalogo.yaml`. A descoberta monta o manifesto com os motores genéricos, e o registro de apps passa a
responder por ele com catálogo, leitura de tela e login, sem uma linha de Python e sem tocar no registro. É a prova
de "zero Python por app"; as provas de cada motor ficam nos testes de cada fatia.

Nível de prova: `simulated` (arquivos em diretório temporário; nenhum aparelho).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from app.automation.leitura_de_tela import LeituraDeclarada
from app.integrations.app_declarado.pacote import PASTA_DOS_APPS, PacoteInvalido, descobrir, manifesto_da_pasta
from app.integrations.app_declarado.sessao import SessaoDeclarada
from app.modules.applications.infrastructure import registry
from app.modules.identity.infrastructure.sessions import SessionDeps
from app.planning.capabilities import catalogo_do_pacote

from .fake_instagram import PKG
from .test_catalogo_como_dado import _acao, _doc
from .test_sessao_declarada import CORREIO, _gravar_correio

APP_DO_CORREIO: dict[str, Any] = {
    "app": CORREIO, "nome": "Correio de Exemplo", "rotulo": "Correio", "provedor_de_sessao": "correio",
    "precisa_de_perfil": True, "precisa_de_internet": True,
    "tipos_de_texto": {"RESPONDER": "dm_initiate"}, "leituras_de_conversa": {"LER": "dm_received"},
    "leitura": {"conteudo": {"minimo": 5},
                "mensagem": {"padroes": ["^{autor} escreveu:\\s+(.+)$"], "campos": ["text"], "escolher": "ultima"}},
}


def _gravar(pasta: Path, *, app: dict[str, Any] | None = None, catalogo: bool = True, sessao: bool = True) -> Path:
    if sessao:
        _gravar_correio(pasta)
    else:
        pasta.mkdir(parents=True)
    (pasta / "app.yaml").write_text(yaml.safe_dump(app or APP_DO_CORREIO, allow_unicode=True), encoding="utf-8")
    if catalogo:
        (pasta / "catalogo.yaml").write_text(yaml.safe_dump(
            _doc(_acao(), _acao(key="RESPONDER", side_effect=True, needs_draft=True, risk="high",
                                # 30.64: ação com efeito declara sobre quem age (o destinatário da resposta)
                                optional_bindings=["destinatario"], objeto_alvo=["destinatario"])),
            allow_unicode=True, sort_keys=False), encoding="utf-8")
    return pasta


def test_um_app_novo_entra_no_registro_so_com_uma_pasta_de_dados(tmp_path: Path) -> None:
    _gravar(tmp_path / CORREIO)
    (tmp_path / "rascunho-sem-app-yaml").mkdir()               # pasta sem app.yaml não é app
    manifestos = descobrir(tmp_path)
    assert [m.definition.package for m in manifestos] == [CORREIO]
    m = manifestos[0]
    d = m.definition
    assert (d.name, d.label, d.session_provider, d.needs_profile, d.requires_internet) == (
        "Correio de Exemplo", "Correio", "correio", True, True)
    assert d.text_kind("RESPONDER") == "dm_initiate" and d.conversation_read("LER") == "dm_received"
    assert m.catalog is not None and m.catalog.package == CORREIO and m.catalog.get("RESPONDER").side_effect
    assert isinstance(m.screen, LeituraDeclarada)
    assert m.session is not None
    provedor = m.session(SessionDeps(None, None, None, None, None, None))  # type: ignore[arg-type]
    assert isinstance(provedor, SessaoDeclarada) and provedor.package == CORREIO

    registry.register_manifest(m)
    try:
        assert registry.get(CORREIO) is m.catalog
        assert registry.screen_reader_of(CORREIO) is m.screen
        assert registry.session_factory_of(CORREIO) is m.session
        assert registry.session_provider_of(CORREIO) == "correio"
        assert CORREIO in {c.package for c in registry.registered()}
    finally:
        registry.unregister(CORREIO)


def test_app_sem_conta_nem_catalogo_e_so_definicao(tmp_path: Path) -> None:
    """O mínimo é o `app.yaml`: um app local sem login gerenciado nem ações declaradas segue no caminho livre."""
    app = {"app": "com.exemplo.notas", "nome": "Notas"}
    m = manifesto_da_pasta(_gravar(tmp_path / "com.exemplo.notas", app=app, catalogo=False, sessao=False))
    assert m.definition.session_provider is None and m.catalog is None and m.session is None and m.screen is None


@pytest.mark.parametrize(("mexe", "trecho"), [
    (lambda d: d.update(provedr_de_sessao="x"), "campo desconhecido provedr_de_sessao"),
    (lambda d: d.update(app="com.outro.app"), "a pasta traz o app.yaml de 'com.outro.app'"),
    (lambda d: d.update(app="nao-e-pacote"), "não é um pacote Android"),
    (lambda d: d.update(precisa_de_perfil="sim"), "precisa_de_perfil: true ou false"),
    (lambda d: d.update(tipos_de_texto=["RESPONDER"]), "tipos_de_texto: esperava um mapa"),
    (lambda d: d.pop("provedor_de_sessao"), "vêm juntos"),
    (lambda d: d.update(leitura={"mensagem": {"padroes": ["sem autor (.+)"]}}), "padroes"),
])
def test_app_yaml_invalido_e_recusado_na_carga(tmp_path: Path, mexe: Any, trecho: str) -> None:
    app = yaml.safe_load(yaml.safe_dump(APP_DO_CORREIO))
    mexe(app)
    _gravar(tmp_path / CORREIO, app=app)
    with pytest.raises(PacoteInvalido, match=trecho):
        descobrir(tmp_path)


def test_conta_gerenciada_sem_login_declarado_e_recusada(tmp_path: Path) -> None:
    """`provedor_de_sessao` sem `sessao.yaml` deixaria a porta de sessão dizendo "tem login" sem motor para fazê-lo."""
    _gravar(tmp_path / CORREIO, sessao=False)
    with pytest.raises(PacoteInvalido, match="vêm juntos"):
        descobrir(tmp_path)


def test_o_instagram_e_descoberto_da_pasta_real_e_e_o_que_o_registro_entrega() -> None:
    """A pasta de verdade: o Instagram é um pacote como qualquer outro, e o registro entrega o catálogo que o
    carregador lê do arquivo. (Mesmo conteúdo, não a mesma instância: há testes que limpam o cache do carregador.)"""
    pacotes = {m.definition.package: m for m in descobrir()}
    assert PKG in pacotes
    m = pacotes[PKG]
    lido, registrado = catalogo_do_pacote(PKG), registry.get(PKG)
    assert m.catalog is not None and lido is not None and registrado is not None
    assert [c.key for c in m.catalog.offered] == [c.key for c in lido.offered] == [c.key for c in registrado.offered]
    assert registry.session_provider_of(PKG) == m.definition.session_provider is not None
    assert PASTA_DOS_APPS.name == "apps" and (PASTA_DOS_APPS / PKG / "app.yaml").is_file()



# ==================================================================== fatia 4: o app âncora e os links de perfil
def test_o_app_ancora_sai_do_registro_e_nao_de_um_nome() -> None:
    """Era `package_of_provider("instagram")`: o núcleo achava "o app da conta da persona" pelo tipo de conta de um app
    com nome. Agora é o app cujo `app.yaml` diz `ancora_do_perfil: true`."""
    assert registry.pacote_ancora() == PKG
    assert registry.capabilities_of(PKG).profile_anchor


def test_dois_apps_ancora_sao_recusados_na_descoberta(tmp_path: Path) -> None:
    """A persona tem UM app âncora; dois é o item 12.3, decisão do dono, e não escolha pela ordem das pastas."""
    _gravar(tmp_path / CORREIO, app={**APP_DO_CORREIO, "ancora_do_perfil": True})
    outro = {"app": "com.exemplo.agenda", "nome": "Agenda", "ancora_do_perfil": True}
    _gravar(tmp_path / "com.exemplo.agenda", app=outro, catalogo=False, sessao=False)
    with pytest.raises(PacoteInvalido, match="mais de um app âncora"):
        descobrir(tmp_path)


def test_links_de_perfil_sao_dado_do_app(tmp_path: Path) -> None:
    from app.modules.skills.infrastructure.profile_links import profile_links_for

    app = {**APP_DO_CORREIO, "links_de_perfil": {"hosts": ["Correio.Exemplo"], "reservados": ["caixa"]}}
    m = manifesto_da_pasta(_gravar(tmp_path / CORREIO, app=app))
    assert m.definition.profile_link_hosts == ("correio.exemplo",)
    registry.register_manifest(m)
    try:
        (regra,) = profile_links_for(CORREIO)
        assert regra.hosts == frozenset({"correio.exemplo"}) and regra.reserved == frozenset({"caixa"})
    finally:
        registry.unregister(CORREIO)
    # O Instagram também: os domínios dele vêm do `app.yaml`, não de Python.
    (ig,) = profile_links_for(PKG)
    assert "instagram.com" in ig.hosts and "p" in ig.reserved
    assert profile_links_for("com.sem.links") == ()


@pytest.mark.parametrize(("links", "trecho"), [
    ({"hosts": []}, "ao menos um domínio"),
    ({"hosts": ["x.com"], "outros": []}, "mapa com `hosts` e `reservados`"),
    ({"hosts": "x.com"}, "lista de textos"),
])
def test_links_de_perfil_invalidos_sao_recusados(tmp_path: Path, links: object, trecho: str) -> None:
    _gravar(tmp_path / CORREIO, app={**APP_DO_CORREIO, "links_de_perfil": links})
    with pytest.raises(PacoteInvalido, match=trecho):
        descobrir(tmp_path)


def test_bloco_antigo_do_config_yaml_nao_e_ignorado_em_silencio() -> None:
    """O bloco `instagram:` saiu (fatia 4). Esquecido no arquivo, ele seria ignorado (o modelo aceita chave
    desconhecida) e a instalação perderia o ajuste sem saber: a carga recusa e diz para onde foi."""
    from pydantic import ValidationError

    from app.config import AppConfigFile

    with pytest.raises(ValidationError, match="contas.session_max_age_s"):
        AppConfigFile.model_validate({"instagram": {"session_max_age_s": 60}})
    cfg = AppConfigFile.model_validate({"contas": {"session_max_age_s": 60,
                                                   "sessao": {PKG: {"settle_s": 5, "max_auth_attempts": 2}}}})
    assert cfg.contas.session_max_age_s == 60 and cfg.contas.sessao[PKG].settle_s == 5


def test_outlook_embutido_entra_com_login_gerenciado_sem_ser_ancora() -> None:
    """Item 23.3 (ADR-057): o Outlook é a primeira pasta real depois do Instagram. Desde o 23.8 ela traz também
    `telas.yaml` e `sessao.yaml`: entra no registro com provedor de sessão próprio, sem leitura de tela para rascunho,
    exige perfil e internet, e NÃO é âncora: a conta âncora da persona segue sendo a do Instagram — dois âncoras
    derrubariam a descoberta inteira. O detalhe do login está em `test_outlook_declarado.py`."""
    outlook = "com.microsoft.office.outlook"
    manifestos = {m.definition.package: m for m in descobrir(PASTA_DOS_APPS)}
    assert {PKG, outlook} <= set(manifestos)
    m = manifestos[outlook]
    assert m.definition.needs_profile and m.definition.requires_internet
    assert m.definition.session_provider == "microsoft" and m.session is not None and m.screen is None
    # Catálogo só de leitura (23.8, T17 do ADR-057).
    assert m.catalog is not None and not any(c.side_effect for c in m.catalog.capabilities)
    assert [p for p, mm in manifestos.items() if mm.definition.profile_anchor] == [PKG]
