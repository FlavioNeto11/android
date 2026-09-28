"""Classificação das telas do Instagram por sinais ESTRUTURAIS.

Os ids do Instagram são ofuscados e mudam a cada versão, então nada aqui depende de um id fixo. A âncora é o
atributo `password` da hierarquia, que vem direto do Android (`AccessibilityNodeInfo.isPassword()`), e as posições
relativas entre os campos. Texto localizado é sinal COMPLEMENTAR, nunca a decisão: ele mora numa tabela por idioma
e só desempata.

Idioma: `en-US` é a variante suportada nesta rodada (é o que o emulador do projeto usa). Acrescentar outro idioma é
acrescentar uma chave no conhecimento, não espalhar texto pelo código.

Desde o ADR-052 (fatia 1), os sinais, as regras de tela em ordem, a leitura da conta e o estado conhecido são DADO,
em `app/conhecimento/apps/com.instagram.android/telas.yaml`, lido pelo motor genérico
`automation/conhecimento_de_telas.py`. Desde a fatia 3, o login e a conferência da conta também são dado
(`sessao.yaml`, na mesma pasta) e motor genérico (`integrations/app_declarado/`): a geometria do formulário e a
dispensa de telas benignas saíram daqui. O que sobra é a interface de telas de sempre (`classify`, `Screen`…) e a
leitura de conteúdo — as próximas fatias.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from ...automation import conhecimento_de_telas as telas
from ...automation.hierarchy import UiTree
from ..app_declarado import conhecimento as declarado
from ..app_declarado.formulario import LoginForm

PACKAGE = "com.instagram.android"


class Screen(StrEnum):
    FEED = "feed"
    LOGIN = "login"
    TWO_FACTOR = "two_factor"
    CHALLENGE = "challenge"
    SAVE_LOGIN_PROMPT = "save_login_prompt"
    ACCOUNT_SWITCHER = "account_switcher"
    PROFILE = "profile"
    INBOX = "inbox"
    LOADING = "loading"
    # Fatia 1 do ADR-052 (execução e31953): telas de dentro do app, logadas, sem a barra de navegação.
    THREAD = "thread"
    COMMENTS = "comments"
    POST = "post"
    SEARCH = "search"
    UNKNOWN = "unknown"


#: O conhecimento de sessão e de telas do Instagram, carregado uma vez (o MESMO objeto que o provedor de sessão usa).
#: Arquivo errado derruba a importação — na partida, não no meio de uma sessão.
SESSAO = declarado.do_app(PACKAGE)
CONHECIMENTO = SESSAO.telas
# Sinais por idioma, compilados do conhecimento. Mesmo formato de antes para quem os consulta (reconciliação,
# conferência com `hierarchy._DESAFIO`).
SIGNALS: dict[str, dict[str, re.Pattern[str]]] = CONHECIMENTO.sinais
DEFAULT_LOCALE = CONHECIMENTO.idioma_padrao
# Um `@usuario` mostrado na tela, pela extração declarada.
USERNAME_TEXT = CONHECIMENTO.extracoes["conta_no_cabecalho"].padrao


@dataclass(slots=True)
class Classification:
    screen: Screen
    reason: str
    form: LoginForm | None = None
    evidence: list[str] = field(default_factory=list)


def signals(locale: str | None) -> dict[str, re.Pattern[str]]:
    """`pt-BR` -> tabela `pt`; idioma não suportado cai no padrão declarado (inglês)."""
    return CONHECIMENTO.sinais_de(locale)


def header_username(tree: UiTree) -> str | None:
    """Conta aberta, lida SÓ de um id de cabeçalho de perfil conhecido — a fonte confiável de QUEM está logado.

    O feed mostra o @ de outras contas (autores de reels, stories que a conta segue); pegar o "primeiro @ da tela"
    confundia um desses com a conta própria e disparava "conta errada" falso. Para decidir identidade, só o
    cabeçalho vale; quem precisa disso navega até a aba de perfil primeiro. Os ids vêm do conhecimento.
    """
    return CONHECIMENTO.extrair("conta_no_cabecalho", tree, palpite=False)


def observed_username(tree: UiTree) -> str | None:
    """Conta aberta, lida da tela. Prefere um id conhecido; se não houver, usa o `@usuario` mais evidente.

    O palpite pelo `@` só é seguro em telas onde o único @ é o da conta (perfil, troca de contas). Para IDENTIFICAR
    quem está logado a partir do feed, use `header_username`, que ignora esse palpite.
    """
    return CONHECIMENTO.extrair("conta_no_cabecalho", tree, palpite=True)


# Rótulos de interface que aparecem em quase toda tela do app e não dizem nada sobre o CONTEÚDO. Ficam fora da
# leitura para que o gerador receba a legenda e os comentários, não a barra de navegação.
_CHROME = re.compile(
    r"^\s*(curtir|curtidas?|comentar|coment[áa]rios?|compartilhar|enviar|salvar|seguir|seguindo|seguidores?"
    r"|publica[çc][õo]es|in[íi]cio|pesquisa|explorar|reels|perfil|mais|op[çc][õo]es|voltar|fechar|adicionar"
    r"|ver tradu[çc][ãa]o|ver mais|ver todos.*|responder|h[áa] \d+.*|\d+ ?[a-z]?|like[sd]?|comments?|share|send"
    r"|save|follow(ing)?|followers?|posts?|home|search|explore|profile|more|options|back|close|add|reply"
    r"|see translation|see more|view all.*|\d+ ?[wdhm]|now|agora)\s*$", re.IGNORECASE)
# Texto curto demais é rótulo, não conteúdo. Uma legenda ou comentário de verdade não cabe em 15 caracteres.
_MIN_CONTEUDO = 15


def conteudo_visivel(tree: UiTree, *, limite: int = 600, max_linhas: int = 8) -> str:
    """O que está ESCRITO na tela agora: legenda da publicação, comentários, mensagem da conversa.

    Serve para o texto que o perfil vai escrever falar do que está ali, em vez de elogiar no vácuo. É leitura
    heurística de propósito: os ids do Instagram são ofuscados e mudam a cada versão, então filtrar por rótulo de
    interface e por tamanho envelhece melhor do que depender de um id que some na próxima atualização.

    Tela sensível (campo de senha) devolve vazio: nada dela sai daqui, nem para o modelo.

    O que sai daqui é DADO de terceiro — quem monta o prompt precisa marcar como tal, nunca como instrução.
    """
    if tree.sensitive:
        return ""
    linhas: list[str] = []
    vistos: set[str] = set()
    for e in tree.elements:
        texto = (e.text or "").strip()
        if len(texto) < _MIN_CONTEUDO or _CHROME.match(texto):
            continue
        chave = texto.lower()
        if chave in vistos:
            continue
        vistos.add(chave)
        linhas.append(texto)
        if len(linhas) >= max_linhas:
            break
    saida = "\n".join(linhas)
    return saida[:limite].rstrip() if len(saida) > limite else saida


# A linha de um comentário chega como "autor said texto…" (a mesma forma que `COLLECT_COMMENTS` usa para recortar
# o autor). Aqui o interesse é o oposto: o TEXTO, já que o autor é quem se sabe de antemão.
def _padrao_do_autor(username: str) -> re.Pattern[str]:
    arroba = re.escape(username.strip().lstrip("@"))
    return re.compile(rf"^@?{arroba}\s+(?:said|disse|comentou)\s+(.+)$", re.IGNORECASE | re.DOTALL)


def comentario_de(tree: UiTree, username: str, *, limite: int = 400) -> str:
    """O que ESTA pessoa escreveu no comentário visível — e nada do que as outras escreveram.

    Responder sem ler o comentário é responder no escuro. Mas pegar "um texto qualquer da tela" seria pior: numa
    lista de comentários, atribuir a fala do vizinho a quem se está respondendo produz resposta sem sentido e,
    pior, memória falsa no nome da pessoa errada. Por isso o casamento exige o autor na MESMA linha do texto.

    Devolve vazio quando não há certeza — e vazio significa "escreva sem isto", nunca "invente".
    """
    # Testa o valor JÁ sem a arroba: "@" sozinho passaria no teste e produziria um padrão que casa com qualquer
    # linha "@ disse …" — fala de ninguém entrando como fala da contraparte, no único bloco de onde nasce memória.
    if tree.sensitive or not (username or "").strip().lstrip("@"):
        return ""
    padrao = _padrao_do_autor(username)
    for e in tree.elements:
        achado = padrao.match((e.text or "").strip())
        if achado:
            texto = achado.group(1).strip()
            return texto[:limite].rstrip() if len(texto) > limite else texto
    return ""


# Numa conversa aberta, a bolha da outra pessoa é anunciada pela acessibilidade com o autor junto do texto. As
# formas vistas em campo e nas traduções do app: "fulano said oi", "fulano disse oi", "Message from fulano: oi",
# "Mensagem de fulano: oi". Casar o AUTOR é a única maneira honesta de saber de quem é a fala — bolha sem autor
# pode ser desta própria conta, e atribuir à outra pessoa o que nós mesmos escrevemos é memória falsa no nome
# errado.
def _padroes_de_mensagem(username: str) -> tuple[re.Pattern[str], ...]:
    arroba = re.escape(username.strip().lstrip("@"))
    return (
        re.compile(rf"^@?{arroba}\s+(?:said|disse|sent|enviou|escreveu)\s*:?\s+(.+)$", re.IGNORECASE | re.DOTALL),
        re.compile(rf"^(?:message|mensagem)\s+(?:from|de)\s+@?{arroba}\s*[:\-]\s*(.+)$", re.IGNORECASE | re.DOTALL),
    )


def mensagem_de(tree: UiTree, username: str, *, limite: int = 400) -> str:
    """A ÚLTIMA fala desta pessoa na conversa aberta — e nada que esta conta tenha escrito.

    É o equivalente de `comentario_de` para o fio de mensagem direta. Sem ele, o caminho de DM nunca tinha lado
    de "recebido": o que a outra pessoa respondeu entrava só como texto de tela, e de tela não sai memória
    (achado #108). Com ele, mandar mensagem numa conversa em que ela acabou de falar vira RESPONDER.

    Duas decisões que valem mais que o formato:

    * **Exige o autor.** Sem atribuição explícita devolve vazio, e vazio quer dizer "escreva sem isto" — nunca
      "invente". Quem chama volta a puxar conversa, que é o comportamento de sempre.
    * **Pega a última, não a primeira.** A hierarquia vem de cima para baixo e a conversa também: a fala que
      pede resposta é a de baixo. `comentario_de` pega a primeira porque ali há UM comentário sendo respondido.

    Tela sensível devolve vazio, como todo o resto deste módulo: dali não sai nada, nem para o modelo.
    """
    if tree.sensitive or not (username or "").strip().lstrip("@"):
        return ""
    padroes = _padroes_de_mensagem(username)
    achado = ""
    for e in tree.elements:
        # `desc` entra junto com `text`: em bolha de conversa o autor costuma estar na descrição de
        # acessibilidade, e o texto visível é só a frase.
        for bruto in ((e.text or "").strip(), (e.desc or "").strip()):
            if not bruto:
                continue
            for padrao in padroes:
                if (m := padrao.match(bruto)) and (texto := m.group(1).strip()):
                    achado = texto
                    break
    return achado[:limite].rstrip() if len(achado) > limite else achado


def reconhecer(tree: UiTree, *, package: str | None, locale: str | None = None) -> telas.TelaReconhecida:
    """O resultado cru do motor genérico, com o tipo declarado (autenticada, desafio…) e o formulário de login achado
    pela geometria do motor de sessão com os sinais declarados."""
    return SESSAO.reconhecer(tree, package=package, locale=locale)


def classify(tree: UiTree, *, package: str | None, locale: str | None = None) -> Classification:
    """Decide o que está na tela pelo conhecimento declarado. Estrutura primeiro; texto localizado só desempata."""
    r = reconhecer(tree, package=package, locale=locale)
    tela = Screen(r.tela) if r.tela in Screen._value2member_map_ else Screen.UNKNOWN
    form = r.formulario if isinstance(r.formulario, LoginForm) else None
    return Classification(tela, r.razao, form=form, evidence=r.evidencia)


def em_casa(screen: Screen) -> bool:
    """A tela é uma das do estado conhecido (de onde a conta pode ser lida)?"""
    return CONHECIMENTO.em_casa(screen.value)


def autenticada(screen: Screen) -> bool:
    """A tela, pelo conhecimento, só aparece com uma conta aberta."""
    return CONHECIMENTO.autenticada(screen.value)
