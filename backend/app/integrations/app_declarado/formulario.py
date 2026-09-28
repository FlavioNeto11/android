"""Geometria do formulário de login, da aba de perfil e da dispensa de telas intermediárias: heurística do MOTOR.

Ids de app costumam ser ofuscados e mudar a cada versão, então nada aqui depende de um id fixo. A âncora é o atributo
`password` da hierarquia, que vem direto do Android (`AccessibilityNodeInfo.isPassword()`), e as posições relativas
entre os campos: usuário logo acima da senha, botão de entrar logo abaixo. O que é do app (o texto do botão de
entrar, o que desqualifica um candidato, os rótulos e ids de recusa) chega pronto do conhecimento declarado
(`sessao.yaml` + sinais do `telas.yaml`); este módulo só aplica.
"""
from __future__ import annotations

import re
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


def _rotulo(e: UiElement) -> str:
    return f"{e.text} {e.desc}".strip()


def password_field(tree: UiTree) -> UiElement | None:
    """Âncora do formulário. `password` vem do Android; `editable` não serve, porque há app que usa widgets próprios
    que a heurística de classe não reconhece (foi assim no primeiro app operado aqui)."""
    campos = [e for e in tree.elements if e.password and e.enabled]
    return campos[0] if len(campos) == 1 else None


def username_field(tree: UiTree, password: UiElement) -> UiElement | None:
    """O campo de usuário é o campo de texto imediatamente ACIMA do de senha, na mesma faixa horizontal."""
    px1, py1, px2, _ = password.bounds
    candidatos = [
        e for e in tree.elements
        if not e.password and e.enabled and e.bounds[3] <= py1                     # inteiramente acima
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
        if entrar.match(rotulo.strip()):
            candidatos.append(e)
    return candidatos[0] if len(candidatos) == 1 else None


def login_form(tree: UiTree, *, entrar: re.Pattern[str], exclusao: re.Pattern[str]) -> LoginForm | None:
    password = password_field(tree)
    if password is None:
        return None
    return LoginForm(password=password, username=username_field(tree, password),
                     submit=submit_button(tree, password, entrar=entrar, exclusao=exclusao))


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
