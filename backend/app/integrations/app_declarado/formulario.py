"""Geometria do formulário de login, da aba de perfil e da dispensa de telas intermediárias: heurística do MOTOR.

Ids de app costumam ser ofuscados e mudar a cada versão, então nada aqui depende de um id fixo. A âncora é o atributo
`password` da hierarquia, que vem direto do Android (`AccessibilityNodeInfo.isPassword()`), e as posições relativas
entre os campos: usuário logo acima da senha, botão de entrar logo abaixo. O que é do app (o texto do botão de
entrar, o que desqualifica um candidato, os rótulos e ids de recusa) chega pronto do conhecimento declarado
(`sessao.yaml` + sinais do `telas.yaml`); este módulo só aplica.

Item 23.6 (ADR-057): login em ETAPAS (o identificador numa tela, com um "avançar"; a senha na seguinte, que mostra a
conta), o acesso à conta fora da barra inferior (um elemento declarado por id ou rótulo, com um candidato só) e a
leitura do site na barra de endereço de uma Custom Tab do navegador declarado.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from ...automation.hierarchy import UiElement, UiTree


@dataclass(slots=True)
class LoginForm:
    """Os três elementos do formulário, quando a tela é de login."""

    password: UiElement
    username: UiElement | None = None
    submit: UiElement | None = None

    @property
    def complete(self) -> bool:
        return self.username is not None and self.submit is not None

    @property
    def usuario_editavel(self) -> bool:
        """O "usuário" achado é um campo de TEXTO. No login em etapas, a tela da senha mostra a conta num cabeçalho
        clicável (o "voltar para trocar de conta") logo acima do campo: a geometria o toma por usuário, e digitar nele
        seria tocar no botão de trocar de conta."""
        return self.username is not None and self.username.editable


@dataclass(slots=True)
class FormularioDoUsuario:
    """A etapa do identificador de um login em etapas: o campo e o botão que leva à tela da senha."""

    campo: UiElement
    botao: UiElement


def _rotulo(e: UiElement) -> str:
    return f"{e.text} {e.desc}".strip()


def _casa_inteiro(padrao: re.Pattern[str], e: UiElement) -> bool:
    """O rótulo casa `padrao` por inteiro pelo texto, pela descrição ou pelos dois juntos.

    O mesmo botão às vezes traz o texto repetido na descrição: medido em 30/09/2026 no "Continue" do Outlook
    (android-06, "Continue Continue") e nos botões do WebView da Microsoft. Só o rótulo junto não casaria."""
    junto = _rotulo(e)
    return any(padrao.match(t.strip()) for t in {e.text or "", e.desc or "", junto} if t.strip())


def _sufixo(resource_id: str) -> str:
    return resource_id.rsplit("/", 1)[-1].lower()


def password_field(tree: UiTree) -> UiElement | None:
    """Âncora do formulário. `password` vem do Android; `editable` não serve, porque há app que usa widgets próprios
    que a heurística de classe não reconhece (foi assim no primeiro app operado aqui)."""
    campos = [e for e in tree.elements if e.password and e.enabled]
    return campos[0] if len(campos) == 1 else None


#: (pacote, sufixo do id) de campos que nunca são do formulário: a barra de endereço de um navegador declarado.
Ignorados = tuple[tuple[str, str], ...]


def _ignorado(e: UiElement, ignorar: Ignorados) -> bool:
    return any(e.package == pacote and _sufixo(e.resource_id) == sufixo for pacote, sufixo in ignorar)


def username_field(tree: UiTree, password: UiElement, *, ignorar: Ignorados = ()) -> UiElement | None:
    """O campo de usuário é o campo de texto imediatamente ACIMA do de senha, na mesma faixa horizontal.

    `ignorar`: a barra de endereço da Custom Tab é um campo de texto largo acima de tudo — numa página de senha sem
    usuário, a geometria a tomaria por usuário, e o identificador seria digitado na barra."""
    px1, py1, px2, _ = password.bounds
    candidatos = [
        e for e in tree.elements
        if not e.password and e.enabled and e.bounds[3] <= py1                     # inteiramente acima
        and not _ignorado(e, ignorar)
        and e.bounds[2] > px1 and e.bounds[0] < px2                                # sobreposto horizontalmente
        and (e.editable or e.class_name.endswith("EditText") or e.clickable)
        and (e.bounds[2] - e.bounds[0]) >= (px2 - px1) * 0.6                       # largura parecida com a do campo
    ]
    if not candidatos:
        return None
    return max(candidatos, key=lambda e: e.bounds[1])                              # o mais próximo, logo acima


def submit_button(tree: UiTree, password: UiElement, *, entrar: re.Pattern[str],
                  exclusao: re.Pattern[str]) -> UiElement | None:
    """Clicável, habilitado, ABAIXO do campo de senha, sem casar a `exclusao`, e exatamente um candidato.

    `entrar` é o sinal declarado do rótulo do botão (casa o rótulo inteiro); `exclusao` desqualifica um candidato —
    entrar por OUTRA conta ou rede nunca é o botão deste formulário. Mais de um candidato é incerteza, não escolha: o
    mesmo rótulo de entrar também aparece em "já tem conta? Entrar", em "entrar com <outra rede>" e no seletor de
    contas.
    """
    _, _, _, py2 = password.bounds
    candidatos = []
    for e in tree.elements:
        if not (e.clickable and e.enabled) or e.bounds[1] < py2:
            continue
        rotulo = _rotulo(e)
        if not rotulo or exclusao.search(rotulo):
            continue
        if _casa_inteiro(entrar, e):
            candidatos.append(e)
    return candidatos[0] if len(candidatos) == 1 else None


def login_form(tree: UiTree, *, entrar: re.Pattern[str], exclusao: re.Pattern[str],
               ignorar: Ignorados = ()) -> LoginForm | None:
    password = password_field(tree)
    if password is None:
        return None
    return LoginForm(password=password, username=username_field(tree, password, ignorar=ignorar),
                     submit=submit_button(tree, password, entrar=entrar, exclusao=exclusao))


def identifier_form(tree: UiTree, *, avancar: re.Pattern[str], exclusao: re.Pattern[str],
                    ignorar: Ignorados = ()) -> FormularioDoUsuario | None:
    """A etapa do identificador: UM campo de texto, nenhum campo de senha, e UM botão abaixo do campo cujo rótulo casa
    `avancar` por inteiro, sem casar a `exclusao`.

    Campo de senha na tela é o formulário de uma tela só (ou a etapa da senha), nunca esta. Dois campos de texto, ou
    dois botões de avançar, é incerteza: o identificador digitado no campo errado iria parar numa busca ou num "criar
    conta" — então nada é escolhido. A barra de endereço da Custom Tab (`ignorar`) não é campo do formulário.

    O botão pode estar DESABILITADO: o "Continue" do Outlook só habilita com um e-mail no campo (medido em 30/09/2026
    no android-06 — exigir `enabled` fazia o login parar em "formulário não identificado" antes de digitar). Quem toca
    relê a tela depois de preencher, e aí o botão já está habilitado.
    """
    if any(e.password for e in tree.elements):
        return None
    campos = [e for e in tree.elements if e.editable and e.enabled and not _ignorado(e, ignorar)]
    if len(campos) != 1:
        return None
    campo = campos[0]
    botoes = []
    for e in tree.elements:
        if not e.clickable or e is campo or e.bounds[1] < campo.bounds[3]:
            continue
        rotulo = _rotulo(e)
        if rotulo and not exclusao.search(rotulo) and _casa_inteiro(avancar, e):
            botoes.append(e)
    return FormularioDoUsuario(campo=campo, botao=botoes[0]) if len(botoes) == 1 else None


def botao_unico(tree: UiTree, *, pacote: str, rotulo: re.Pattern[str], exclusao: re.Pattern[str]) -> UiElement | None:
    """UM clicável habilitado do app cujo rótulo casa `rotulo` por inteiro, sem casar a `exclusao` (item 23.8).

    É o toque de navegação do login em etapas (abrir a etapa do identificador, escolher entrar com a senha). Nenhuma
    posição conta — a página muda de lugar com o teclado —, mas o candidato tem de ser um só: dois botões com o mesmo
    rótulo, ou o de "entrar com outra conta", é incerteza, e o toque não acontece.

    O rótulo vale pelo texto, pela descrição ou pelos dois juntos: num WebView o mesmo botão às vezes traz o texto
    repetido na descrição, e "Use your password Use your password" não casaria por inteiro."""

    def casa(e: UiElement) -> bool:
        junto = _rotulo(e)
        return bool(junto) and not exclusao.search(junto) and _casa_inteiro(rotulo, e)

    candidatos = [e for e in tree.elements
                  if e.clickable and e.enabled and not e.editable and (not e.package or e.package == pacote) and casa(e)]
    return candidatos[0] if len(candidatos) == 1 else None


def mostra_o_identificador(tree: UiTree, identificador: str) -> bool:
    """A tela MOSTRA este identificador (o que a etapa anterior digitou), como palavra inteira, fora de campo de texto?

    É a porta da senha no login em etapas: a tela da senha diz para QUAL conta é. Um app que lembrou outro e-mail e
    pulou direto para a senha (ou a etapa que voltou com outra conta) não recebe a senha desta. Palavra inteira de
    propósito: `ana@exemplo.com` aparece dentro de `joana@exemplo.com`. Campo de texto não conta: o que está num campo
    não é o que a página afirma — a barra de endereço do navegador é um campo, e a URL pode trazer o identificador como
    simples dica.
    """
    alvo = identificador.strip().lstrip("@").strip()
    if not alvo:
        return False
    padrao = re.compile(r"(?<![\w.@+-])@?" + re.escape(alvo) + r"(?![\w.@+-])", re.IGNORECASE)
    return any(not e.password and not e.editable and (padrao.search(e.text or "") or padrao.search(e.desc or ""))
               for e in tree.elements)


def dismiss_button(tree: UiTree, *, rotulos: tuple[re.Pattern[str], ...],
                   ids: tuple[re.Pattern[str], ...]) -> UiElement | None:
    """Botão que fecha uma tela intermediária benigna sem conceder permissão nem seguir ninguém.

    Duas passadas, nesta ordem: primeiro o RÓTULO de recusa declarado em qualquer elemento clicável; só depois o id
    de recusa (o lado de um diálogo aceitar/recusar que não concede nada), que vale quando o rótulo não bate. O
    conhecimento do app só declara RECUSA — jamais "permitir", "próximo" ou "sim", que liberariam contatos,
    notificações, sincronização ou sairiam seguindo gente.
    """
    for e in tree.elements:
        if e.clickable and any(p.search(_rotulo(e)) for p in rotulos):
            return e
    for e in tree.elements:
        if e.clickable and any(p.search(e.resource_id) for p in ids):
            return e
    return None


def save_login_dismiss(tree: UiTree, *, agora_nao: re.Pattern[str]) -> UiElement | None:
    """Botão que dispensa o "Salvar dados de login?" sem salvá-los na nuvem — o "Agora não" declarado.

    É a escolha que preserva privacidade (o login não vai para o backup da conta Google do aparelho). Se o texto do
    botão mudar, devolve None e o fluxo segue sem tocar em nada: falha para o lado seguro, nunca no botão errado.
    """
    for e in tree.elements:
        if e.clickable and agora_nao.search(_rotulo(e)):
            return e
    return None


def profile_tab(tree: UiTree, *, prefixo_de_id: str, rotulos: tuple[str, ...],
                faixa_inferior: float) -> tuple[int, int] | None:
    """Aba de perfil da barra inferior. O id do tab (pelo prefixo declarado) é o sinal mais forte; senão, um item DA
    BARRA com rótulo EXATO entre os declarados.

    O casamento é exato e restrito à barra de baixo (a faixa a partir de `faixa_inferior` da altura ocupada) de
    propósito: "perfil" como substring casava com o botão "Editar perfil" no meio da tela de perfil, e o toque abria a
    edição — onde a conta não tem como ser lida.
    """
    for e in tree.elements:
        if e.clickable and e.resource_id.rsplit("/", 1)[-1].lower().startswith(prefixo_de_id):
            return e.center
    if not tree.elements:
        return None
    base = max(e.bounds[3] for e in tree.elements)
    for e in tree.elements:
        if e.clickable and e.bounds[3] >= base * faixa_inferior and _rotulo(e).lower() in rotulos:
            return e.center
    # Sem o tab identificado, NÃO se chuta. O palpite antigo ("o clicável mais à direita lá embaixo") pegava um
    # carrossel de mídia ou um perfil sugerido na tela de onboarding: o toque abria o perfil de OUTRA pessoa e a
    # conta lida virava a dela (o caso real está no `sessao.yaml` do primeiro app).
    return None


def account_opener(tree: UiTree, *, pacote: str, prefixos_de_id: tuple[str, ...],
                   rotulos: tuple[re.Pattern[str], ...]) -> tuple[int, int] | None:
    """O elemento que abre a conta quando ela NÃO está numa barra inferior (o avatar no canto, o menu da conta): um
    clicável do próprio app cujo id começa por um dos prefixos declarados ou cujo texto/descrição casa um dos rótulos.

    Exatamente um candidato, senão nada: fora da barra de baixo não há faixa que restrinja, e um menu de contas é onde
    "o elemento parecido" abre a conta de outra pessoa ou o "adicionar conta".
    """
    candidatos = [
        e for e in tree.elements
        if e.clickable and e.enabled and (not e.package or e.package == pacote)
        and ((prefixos_de_id and _sufixo(e.resource_id).startswith(prefixos_de_id))
             or any(p.search(_rotulo(e)) for p in rotulos))
    ]
    return candidatos[0].center if len(candidatos) == 1 else None


def valores_extraidos(tree: UiTree, *, pacote: str, ids: tuple[str, ...], padrao: re.Pattern[str]) -> set[str]:
    """Todos os valores que a extração declarada acha na tela: pelo texto e, sem texto que case, pela descrição, dos
    elementos do app com um dos `ids` (sufixo exato); sem `ids`, de qualquer elemento do app. Quem lê exige UM valor:
    uma lista de contas mostra várias, e a primeira não é a aberta."""
    valores: set[str] = set()
    for e in tree.elements:
        if e.password or (e.package and e.package != pacote) or (ids and _sufixo(e.resource_id) not in ids):
            continue
        for bruto in (e.text, e.desc):
            if m := padrao.match((bruto or "").strip()):
                valores.add(m.group(1).lower())
                break
    return valores


def host_da_url(url_ou_texto: str) -> str:
    """O host de uma URL ou do texto da barra de endereço (o navegador mostra sem `https://`)."""
    t = (url_ou_texto or "").strip().casefold()
    t = t.split("://", 1)[1] if "://" in t else t
    return t.split("/", 1)[0].split("#", 1)[0].split("?", 1)[0].rsplit("@", 1)[-1].split(":", 1)[0]


def host_da_barra(tree: UiTree, *, pacote: str, sufixo: str) -> str:
    """O site na barra de endereço do navegador (`pacote`), pelo id declarado. Vazio quando a barra não está visível:
    quem chama trata como site NÃO confirmado."""
    for e in tree.elements:
        if e.package == pacote and _sufixo(e.resource_id) == sufixo and e.text:
            return host_da_url(e.text)
    return ""


def host_permitido(host: str, hosts: Iterable[str]) -> bool:
    """O site é um dos declarados, ou subdomínio de um deles. Vazio nunca é: site não lido não recebe nada."""
    host = host.strip().lower()
    return bool(host) and any(h and (host == h or host.endswith("." + h)) for h in (x.strip().lower() for x in hosts))
