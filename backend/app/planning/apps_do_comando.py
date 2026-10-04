"""Que aplicativos o TEXTO do comando cita, e se ele pede um site. Puro: só texto e a lista de apps configurados.

Mora no planejamento, e não em `taskqueue/service.py`, porque duas camadas perguntam a mesma coisa: o serviço, para
escolher os catálogos que vão ao planejador (item 24.1, ADR-058), e o provedor simulado, para montar o plano de
regras dos apps citados. Importar o serviço a partir do provedor inverteria as camadas.

O que se lê aqui é uma CANDIDATURA, não a lista final: o planejador recebe os catálogos dos apps candidatos, e os
apps exigidos (`Plan.required_apps`) são os que as etapas de fato usam.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from typing import TYPE_CHECKING, Final

from .catalog import capabilities_of

if TYPE_CHECKING:
    from .provider import AppContext

#: Pedido de navegador no comando: o alvo não é o app da conta do aparelho.
PEDE_NAVEGADOR = re.compile(r"\b(chrome|navegador|browser)\b", re.IGNORECASE)
#: Pedido de ABRIR um endereço: verbo de navegação e, logo adiante, URL, site ou portal. Uma URL solta não basta:
#: "envie o link https://… para @fulano" continua sendo tarefa do Instagram. "Página" fica de fora: no Instagram ela
#: é perfil ("curta o post da página X").
ABRIR_ENDERECO = re.compile(r"\b(abr\w*|acess\w*|entr\w*|naveg\w*|visit\w*|v[aá])\b[^.\n]{0,40}?"
                            r"(https?://|\bsite\b|\bportal\b)", re.IGNORECASE)
#: 31.29: os nomes COMUNS dos apps de sistema, por pacote. O cadastro tem um nome só ("Configurações do Android"), e
#: quem escreve "abra as Configurações" não o citava: o comando caía no app padrão (o Instagram) e o planejador, com o
#: catálogo errado, pedia a pessoa (11 de 12 na linha de base de 04/10). Só app de SISTEMA entra aqui; app com manifesto
#: tem o rótulo dele.
APELIDOS_POR_PACOTE: Final[dict[str, tuple[str, ...]]] = {
    "com.android.settings": ("Configurações", "Ajustes", "Settings"),
}
#: Depois de um apelido, "do/da/de/no/na <outro app>" é uma tela DENTRO do outro app ("as configurações do Instagram"),
#: não o app de sistema.
_DE_OUTRO_APP = r"\s+(?:do|da|de|dos|das|no|na|nos|nas)\s+(?:o\s+|a\s+)?"
#: Texto citado é CONTEÚDO (a mensagem a enviar, o comentário a escrever), não o pedido.
CITACAO = re.compile(r"\"[^\"]*\"|“[^”]*”|'[^']*'")


def sem_citacoes(command: str) -> str:
    """O comando sem o que está entre aspas: "diga 'abra o Outlook'" não pede o Outlook."""
    return CITACAO.sub(" ", command or "")


def pede_site(command: str) -> bool:
    """O comando pede navegador ou abrir um endereço (fora das aspas)?"""
    pedido = sem_citacoes(command)
    return bool(PEDE_NAVEGADOR.search(pedido) or ABRIR_ENDERECO.search(pedido))


def nomes_do_app(app: AppContext) -> list[str]:
    """Como uma pessoa escreve o app: o nome do cadastro, o rótulo do manifesto ("Microsoft Outlook" no cadastro,
    "no Outlook" no comando) e, para app de sistema, os apelidos comuns (`APELIDOS_POR_PACOTE`, 31.29). O rótulo de um
    app sem manifesto é o próprio pacote, e pacote não é nome falado.

    Público desde o 31.13: a sombra da R5 pergunta ao Jev pelos MESMOS nomes que esta leitura casa."""
    nomes = [app.name] if app.name else []
    rotulo = capabilities_of(app.package).label if app.package else ""
    if rotulo and rotulo != app.package:
        nomes.append(rotulo)
    nomes.extend(APELIDOS_POR_PACOTE.get(app.package or "", ()))
    return [n for n in dict.fromkeys(n.strip() for n in nomes) if len(n) >= 2]


def sem_acento(texto: str) -> str:
    """Para casar nome de app sem depender de acento nem de caixa ("configuracoes" = "Configurações")."""
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c)).casefold()


def apps_citados(command: str, apps: Sequence[AppContext]) -> list[AppContext]:
    """Os apps configurados que o comando nomeia fora das aspas, na ordem em que aparecem pela primeira vez."""
    texto = sem_acento(sem_citacoes(command))
    posicoes: list[tuple[int, int, AppContext]] = []
    for ordem, app in enumerate(apps):
        apelidos = {sem_acento(a) for a in APELIDOS_POR_PACOTE.get(app.package or "", ())}
        outros = [sem_acento(n) for o in apps if o is not app for n in nomes_do_app(o)]
        achados = []
        for nome in nomes_do_app(app):
            for m in re.finditer(rf"(?<!\w){re.escape(sem_acento(nome))}(?!\w)", texto):
                if sem_acento(nome) in apelidos and any(
                        re.match(rf"{_DE_OUTRO_APP}{re.escape(o)}(?!\w)", texto[m.end():]) for o in outros):
                    continue                    # "as configurações do Instagram" é tela do Instagram (31.29)
                achados.append(m.start())
                break
        if achados:
            posicoes.append((min(achados), ordem, app))
    return [app for _, _, app in sorted(posicoes, key=lambda p: (p[0], p[1]))]
