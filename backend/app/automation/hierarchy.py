"""Hierarquia do UiAutomator → lista compacta de elementos para seletores e para o modelo."""
from __future__ import annotations

import re
import unicodedata
import xml.etree.ElementTree as ET
from typing import Callable
from dataclasses import asdict, dataclass

from ..util import norm_text

#: Apóstrofos e aspas simples tipográficos → o apóstrofo simples. O Instagram desenha "Confirm you’re human" com o
#: U+2019, e os padrões (`'?`) só conheciam o simples: a tela relatada pelo dono não casava em idioma nenhum e virava
#: "desconhecida" — e tela desconhecida o motor de sessão dispensa e volta (ADR-055).
_APOSTROFOS = str.maketrans({"\u2019": "'", "\u2018": "'", "\u02bc": "'", "\u00b4": "'", "\u0060": "'",
                             "\u2032": "'", "\uff07": "'"})


def normalizar_texto_de_tela(value: str | None) -> str:
    """O texto de tela na forma em que os sinais são escritos: apóstrofo simples, minúsculas, espaços colapsados e
    SEM acento. `norm_text` só tira espaço e caixa — e a tela de desafio chega escrita no idioma que o app escolheu,
    com acento e com apóstrofo tipográfico. Comparar sem isto é acertar por sorte."""
    base = norm_text((value or "").translate(_APOSTROFOS))
    return "".join(c for c in unicodedata.normalize("NFD", base) if unicodedata.category(c) != "Mn")


_sem_acento = normalizar_texto_de_tela


BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")
MASK = "••••"
TEXT_CAP = 80             # corte padrão de um elemento no prompt: a UI raramente mostra mais que isso por vez
PROTECTED_TEXT_CAP = 400  # elemento que casa com um texto protegido (ex.: {content} de uma DM): acima do max_length
                          # do rascunho (300 — social/service.py) para nunca truncar o próprio conteúdo comprovado

#: Texto que denuncia uma tela de DESAFIO — 2FA, código por e-mail/SMS, "confirme que é você". Achado #127: até
#: aqui o único critério de "tela sensível" era `password=true`, então uma tela de verificação (que não tem campo
#: de senha nenhum) virava JPEG em disco e imagem no corpo da requisição ao provedor de IA.
#:
#: Casa contra o texto NORMALIZADO (`normalizar_texto_de_tela`: apóstrofo simples, minúsculas, sem acento), por isso
#: está escrito assim aqui.
#:
#: As frases vêm dos sinais `two_factor` e `challenge` (en e pt) do `telas.yaml` do Instagram
#: (`app/conhecimento/apps/com.instagram.android/`), o classificador que já sabia reconhecer essas telas — e que só
#: era consultado DEPOIS de a imagem ter sido capturada e enviada. `test_sensitive_input` confere, string por string,
#: que os dois concordam: dois classificadores discordando sobre a MESMA tela seria pior do que ter um só.
#:
#: Duas famílias, com consequências diferentes (ADR-055, decisão do dono de 29/09/2026):
#:
#: - `_CONTA_TRAVADA`: "confirme que você é humano", CAPTCHA, "detectamos uma atividade suspeita". É a tela que
#:   denuncia a conta travada — cinco das oito contas do parque pararam nela e não voltaram. Vale SEM campo de texto:
#:   a tela real ("Confirm you’re human to use your account, fulano") só tem botões ("Continue", "Get support"), e
#:   exigir o campo deixava a receita e o ator decidirem sobre ela. Por isso as frases são FRASES: palavra solta
#:   ("suspeito", "unusual", "detectamos") aparece em DM e legenda, e o detector roda no meio da execução — uma
#:   conversa com "achei suspeito" bloquearia uma das três contas vivas.
#: - `_CODIGO`: código de login por e-mail/SMS e 2FA. Precisa de pessoa, mas NÃO é conta travada (bruno e andre
#:   passaram por ele em 18/09 e seguem vivos). Continua exigindo onde digitar: a linha "Autenticação de dois fatores"
#:   do MENU de configurações não é pedido de código.
_CONTA_TRAVADA = re.compile(
    # português
    r"((?:confirme|confirmar|verifique|comprove) que (?:voce )?e (?:um[ae]? pessoa(?: real)?|humano)|"
    r"confirme que e voce|ajude a confirmar|verifique sua conta|"
    r"detectamos (?:uma? |algum[ae]? )?(?:tentativa|atividade|acesso|login|comportamento)|"
    r"(?:atividade|tentativa de login|login|acesso|comportamento) (?:suspeit|incomum)|comportamento automatizado|"
    r"nao sou um rob|ajude-nos a proteger (?:a )?sua conta|"
    # inglês. "Help us protect your account" é o título da página da Microsoft que segura a conta até a pessoa
    # comprovar um contato (item 23.8). As outras frases de desafio da Microsoft ("your account has been locked",
    # "enter code") ficam só no `telas.yaml` do Outlook, ancoradas na linha: soltas aqui, um DM de golpe com "your
    # account has been locked" travaria uma conta viva do Instagram.
    r"(?:confirm|verify|prove) (?:that )?you(?:'?re| are) (?:a )?(?:human|real person)|confirm it'?s you|help us confirm|verify your account|"
    r"help us protect your account|"
    r"(?:we|we'?ve|we have) detected (?:an? |some )?(?:unusual|suspicious)|"
    r"(?:suspicious|unusual) (?:login|activity|attempt|behavio)|automated behavio|"
    r"captcha|i'?m not a robot)")
_CODIGO = re.compile(
    # português
    r"(autenticacao de dois fatores|verificacao em duas etapas|"
    r"codigo de (?:seguranca|verificacao|confirma|acesso|autenticacao|backup|login)|codigo de \d+ digitos|"
    r"insira o codigo|digite o codigo|enviamos um codigo|"
    # inglês
    r"two.?factor|two.?step verification|security code|confirmation code|verification code|login code|"
    r"one.?time (?:code|password)|backup code|\d.?digit(?: (?:security |login |confirmation )?code)?|"
    r"enter the code|we sent (?:you )?a code)")

#: Subtipos do desfecho `auth_challenge` (ADR-055). `conta_travada` bloqueia o perfil (ADR-029) e avisa o dono;
#: `codigo` só pede uma pessoa. `verificacao`: o ATOR relatou uma tela de verificação que nenhum sinal conhece — é
#: julgamento do modelo, não casamento determinístico, então pede uma pessoa sem bloquear; quem olhar decide.
SUBTIPO_CONTA_TRAVADA = "conta_travada"
SUBTIPO_CODIGO = "codigo"
SUBTIPO_VERIFICACAO = "verificacao"
#: Tipo de tela (vocabulário de `conhecimento_de_telas.TIPOS`) de cada subtipo detectado.
TIPO_DO_SUBTIPO = {SUBTIPO_CONTA_TRAVADA: "desafio", SUBTIPO_CODIGO: "dois_fatores"}


@dataclass(slots=True, frozen=True)
class ContaTravada:
    """O que o detector de conta travada achou na tela. `trecho` é o pedaço do texto (normalizado) que casou — vai
    na tentativa, no evento e no aviso ao dono, para a pessoa saber POR QUE a automação parou. É a frase da tela de
    segurança, nunca o código digitado: nenhum padrão casa só dígitos."""

    subtipo: str             #: SUBTIPO_CONTA_TRAVADA | SUBTIPO_CODIGO (| SUBTIPO_VERIFICACAO, só no relato do ator)
    trecho: str
    origem: str = "generico"  #: "generico" (sinais deste módulo) ou o nome da tela declarada pelo app que casou

    @property
    def tipo(self) -> str:
        return TIPO_DO_SUBTIPO.get(self.subtipo, "desafio")

    def descrever(self) -> str:
        return f"{self.subtipo}: “{self.trecho}”"


def detectar_trava_generica(texto_normalizado: str, *, tem_onde_digitar: bool) -> ContaTravada | None:
    """Os sinais que valem para QUALQUER app. Conta travada primeiro (é a mais grave, e a tela dela pode também citar
    um código); código só com onde digitar."""
    if m := _CONTA_TRAVADA.search(texto_normalizado):
        return ContaTravada(SUBTIPO_CONTA_TRAVADA, m.group(0)[:80])
    if tem_onde_digitar and (m := _CODIGO.search(texto_normalizado)):
        return ContaTravada(SUBTIPO_CODIGO, m.group(0)[:80])
    return None


#: Numa tela de desafio, um texto que é SÓ dígitos é o código — inclusive o que o operador acabou de digitar no
#: campo. Fora de uma tela de desafio este mesmo formato é preço, contador ou ano, e por isso a máscara depende
#: da tela, não do elemento: é a diferença entre proteger o código e mascarar metade da interface.
_SO_DIGITOS = re.compile(r"^\s*[0-9][0-9 \-]{2,10}\s*$")


def eh_campo_de_texto(classe: str) -> bool:
    """A classe (de acessibilidade) é de um campo onde se digita? `EditText` e os de sugestão: o compositor de
    comentário do Instagram é `AutoCompleteTextView` (a classe que ele declara à acessibilidade não é `EditText`), e
    fora desta lista a conferência da digitação não achava o campo e saía `verified=None`
    (r-20260928165254-e31953). `AutoCompleteTextView` cobre também `MultiAutoCompleteTextView`."""
    return "EditText" in classe or "AutoCompleteTextView" in classe


MOTIVO_SENHA = "campo de senha"
MOTIVO_DESAFIO = "desafio de verificação (2FA/código de acesso)"
MOTIVO_LOJA = "aparelho-loja: a tela mostra a conta Google do parque"


@dataclass(slots=True, frozen=True)
class RegraDeTelaSensivel:
    """Uma tela que um APP declara como sensível, mesmo sem campo de senha.

    Existe porque o catálogo de apps (E10/E11) traz aplicativos que ninguém analisou: o critério genérico não sabe
    que a tela de "dados da conta" daquele app tem CPF, e quem sabe é quem cadastrou o app. Vem do `config.yaml`
    (`sensitive_screens`), não do banco: é declaração de configuração do parque, lida uma vez na subida.
    """

    package: str | None = None              #: `None` vale para qualquer app
    resource_ids: tuple[str, ...] = ()      #: casa por SUFIXO (`:id/cpf` casa com `com.x:id/cpf`)
    texts: tuple[str, ...] = ()             #: casa por texto normalizado CONTIDO em `text` ou `content-desc`
    why: str | None = None

    def motivo(self) -> str:
        alvo = self.package or "qualquer app"
        return self.why or f"tela declarada como sensível para {alvo}"


#: As chaves que `UiTree._partes_do_seletor` entende (31.44: `parte_sem_valor` só vale para elas).
_CHAVES_DO_SELETOR = frozenset({"id", "resource-id", "resource_id", "desc", "accessibility-id", "accessibility_id",
                                "content-desc", "text"})


@dataclass(slots=True)
class UiElement:
    id: str
    text: str
    desc: str
    resource_id: str
    class_name: str
    package: str
    bounds: tuple[int, int, int, int]
    clickable: bool
    enabled: bool
    focused: bool
    scrollable: bool
    editable: bool
    checked: bool
    password: bool

    @property
    def center(self) -> tuple[int, int]:
        x1, y1, x2, y2 = self.bounds
        return (x1 + x2) // 2, (y1 + y2) // 2

    def to_dict(self) -> dict:
        d = asdict(self)
        d["bounds"] = list(self.bounds)
        return d

    def line(self, scale: float = 1.0, *, protect: tuple[str, ...] = ()) -> str:
        """Uma linha compacta para o prompt do modelo. `scale` = pixels do aparelho por pixel do espaço de coordenadas
        que o modelo enxerga: os limites vão no MESMO espaço da imagem, senão um x,y tirado deles cairia fora do alvo.

        `protect`: textos (já normalizados pelo chamador) que, se casarem com o texto deste elemento, usam um corte
        bem mais alto — é o que impede o verificador de julgar 'truncado' um elemento que É o conteúdo comprovado
        (achado #102: DM com mais de 80 caracteres virava 'incerto' porque só os 80 primeiros chegavam ao modelo)."""
        parts = [self.id, self.class_name.rsplit(".", 1)[-1]]
        if self.text:
            cap = PROTECTED_TEXT_CAP if (protect and any(p and p in norm_text(self.text) for p in protect)) else TEXT_CAP
            parts.append(f'text="{self.text[:cap]}"')
        if self.desc:
            parts.append(f'desc="{self.desc[:60]}"')
        if self.resource_id:
            parts.append(f"id={self.resource_id.rsplit('/', 1)[-1]}")
        flags = [n for n, v in (("clickable", self.clickable), ("editable", self.editable), ("scrollable", self.scrollable),
                                ("focused", self.focused), ("checked", self.checked), ("disabled", not self.enabled),
                                ("password", self.password)) if v]
        if flags:
            parts.append(",".join(flags))
        parts.append("[%d,%d,%d,%d]" % tuple(round(v / scale) for v in self.bounds))
        return " | ".join(parts)


#: 31.59: as classes de lista do Android (o nome simples, de qualquer pacote): o contêiner das mensagens de uma
#: conversa quando a lista não se declara rolável (fio curto, que cabe na tela; medido no 31.26).
_CLASSES_DE_LISTA = frozenset({"RecyclerView", "ListView", "ScrollView", "NestedScrollView", "AbsListView", "GridView"})


def _dentro(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    """O retângulo `a` está dentro de `b` (bordas inclusive)."""
    return b[0] <= a[0] and b[1] <= a[1] and a[2] <= b[2] and a[3] <= b[3]


@dataclass(slots=True)
class UiTree:
    elements: list[UiElement]
    packages: list[str]
    sensitive: bool          # tela sensível → não enviar/gravar imagem (ver `sensitive_reason`)
    #: POR QUE a tela é sensível, em português, para virar mensagem de etapa e evidência. `None` quando não é.
    #: Existe porque o critério deixou de ser um só (achado #127) e "campo de senha" passou a ser mentira em
    #: metade dos casos — e a mensagem que o operador lê é a única coisa que explica por que a IA parou.
    sensitive_reason: str | None = None
    #: A conta travada que os sinais GENÉRICOS acharam nesta tela, varrendo o documento inteiro (não só os elementos
    #: que entram no corte). Quem decide com o conhecimento do app é `conhecimento_de_telas.detectar_conta_travada`,
    #: que parte daqui (ADR-055).
    conta_travada: ContaTravada | None = None
    #: A árvore bateu no teto de elementos (`parse_hierarchy(max_elements)`) e há nós que ficaram de fora. Conservador: um
    #: nó a mais depois do teto basta. A leitura visual (item 12.5) recusa com `arvore_truncada`, porque "esta linha não
    #: tem texto" só vale se a subárvore inteira foi vista.
    truncada: bool = False

    def at(self, x: int, y: int) -> UiElement | None:
        """O elemento sob o ponto (x, y) em pixels do aparelho: o MENOR que o contém — o mesmo critério do toque
        por coordenada da IA (`tools.resolve_point`). Usado pela gravação do modo treinamento (item 13.1)."""
        hit = [e for e in self.elements if e.bounds[0] <= x <= e.bounds[2] and e.bounds[1] <= y <= e.bounds[3]]
        return min(hit, key=lambda e: (e.bounds[2] - e.bounds[0]) * (e.bounds[3] - e.bounds[1])) if hit else None

    def by_id(self, element_id: str) -> UiElement | None:
        return next((e for e in self.elements if e.id == element_id), None)

    def texts(self) -> list[str]:
        out: list[str] = []
        for e in self.elements:
            if e.text:
                out.append(e.text)
            if e.desc:
                out.append(e.desc)
        return out

    def contains_text(self, needle: str) -> bool:
        n = norm_text(needle)
        return bool(n) and any(n in norm_text(t) for t in self.texts())

    def bolhas_iguais(self, content: str) -> list[UiElement]:
        """31.59: as bolhas DISTINTAS com o texto (ou a descrição) IGUAL ao conteúdo, normalizado, em elementos não
        editáveis. Igualdade e não "contém": a bolha antiga "oi, tudo bem?" não é a mensagem "oi". Distintas (R2 da
        revisão): o elemento contido noutro já contado (o texto dentro do balão que repete a mesma descrição) não soma."""
        n = norm_text(content)
        if not n:
            return []
        iguais = sorted((e for e in self.elements if not e.editable and (norm_text(e.text) == n or norm_text(e.desc) == n)),
                        key=lambda e: -((e.bounds[2] - e.bounds[0]) * (e.bounds[3] - e.bounds[1])))
        distintas: list[UiElement] = []
        for e in iguais:
            if not any(_dentro(e.bounds, d.bounds) for d in distintas):
                distintas.append(e)
        return distintas

    def mensagens_iguais(self, content: str) -> int:
        """31.59: quantas bolhas distintas com o texto IGUAL (`bolhas_iguais`)."""
        return len(self.bolhas_iguais(content))

    def ultima_bolha_igual(self, content: str) -> UiElement | None:
        """31.59 (R1 da revisão): a bolha igual mais baixa, se ela for a ÚLTIMA mensagem da conversa; `None` senão.

        "Mensagem" é o mesmo tipo de elemento da bolha (o `resource_id` dela ou o de um elemento igual dentro dela): se
        houver outro desse tipo abaixo, a bolha é antiga (revelada por rolagem, ou com resposta depois). O "Seen", a hora
        e a reação abaixo dela não são mensagem e não contam.

        Sem `resource_id` (a bolha do Direct do Instagram, medida no 31.26: um `TextView` sem id, filho direto da lista),
        `_ultima_sem_tipo` decide pelo contêiner de lista."""
        bolhas = self.bolhas_iguais(content)
        if not bolhas:
            return None
        bolha = max(bolhas, key=lambda e: e.bounds[3])
        n = norm_text(content)
        tipos = {e.resource_id for e in self.elements
                 if e.resource_id and not e.editable and _dentro(e.bounds, bolha.bounds)
                 and (norm_text(e.text) == n or norm_text(e.desc) == n)}
        if not tipos:
            return self._ultima_sem_tipo(bolhas)
        if any(e.resource_id in tipos and e.bounds[1] >= bolha.bounds[3] for e in self.elements):
            return None
        return bolha

    def _ultima_sem_tipo(self, bolhas: list[UiElement]) -> UiElement | None:
        """31.59 (bolha sem `resource_id`): sem o tipo da bolha, "mensagem abaixo" é QUALQUER elemento não editável com
        texto ou descrição dentro do menor contêiner de lista que contém a bolha (rolável, ou de classe de lista do
        Android: não depende de app) e com o topo depois do fim dela. Na dúvida, `None` e o modelo julga, nunca
        "enviado":

        - mais de uma bolha igual (sem tipo não se diz qual é a nova);
        - bolha fora de contêiner de lista;
        - qualquer texto abaixo dela na lista, inclusive um rótulo curto (hora, "Seen", reação): sem o tipo, a árvore não
          separa rótulo de uma resposta curta, então a prova cai no juiz enquanto ele estiver lá. O rótulo nunca vira a
          bolha: ela é só o elemento com o texto IGUAL ao conteúdo."""
        if len(bolhas) != 1:
            return None
        bolha = bolhas[0]
        lista = min((e for e in self.elements
                     if e is not bolha and _dentro(bolha.bounds, e.bounds) and e.bounds != bolha.bounds
                     and (e.scrollable or e.class_name.rsplit(".", 1)[-1] in _CLASSES_DE_LISTA)),
                    key=lambda e: (e.bounds[2] - e.bounds[0]) * (e.bounds[3] - e.bounds[1]), default=None)
        if lista is None:
            return None
        abaixo = any(not e.editable and (e.text.strip() or e.desc.strip()) and e is not bolha
                     and not _dentro(e.bounds, bolha.bounds) and _dentro(e.bounds, lista.bounds)
                     and e.bounds[1] >= bolha.bounds[3] for e in self.elements)
        return None if abaixo else bolha

    def sent_as_message(self, content: str, *, antes: int | None = None) -> bool | None:
        """Prova determinística de 'texto enviado numa conversa' (achado #102), sem chamar o modelo: o conteúdo
        aparece num elemento que NÃO é editável (uma mensagem já publicada no fio) e não sobra em nenhum campo
        editável (o campo de escrita, que some/limpa depois do envio — se ainda tiver o texto, ele não saiu de
        lá e não está comprovado). `None` quando não há conteúdo para provar (etapa sem `content` conhecido);
        chamador cai para o julgamento do modelo nesse caso e em qualquer resultado False.

        31.59: `antes` é quantas bolhas com o texto IGUAL havia na tela de ANTES do envio (a linha de base que o executor
        guarda no toque do efeito). A prova exige que a contagem tenha AUMENTADO: sem isso, uma mensagem antiga com o
        mesmo texto e o campo limpo eram indistinguíveis de um envio. Sem linha de base (`None`), a árvore não afirma
        nada e o modelo julga, como antes de existir a prova.

        A bolha que conta também tem de ser a ÚLTIMA mensagem da conversa (`ultima_bolha_igual`, R1): uma bolha antiga
        que entrou na tela por rolagem tem mensagens mais novas abaixo."""
        n = norm_text(content)
        if not n or antes is None:
            return None
        no_campo = any(e.editable and n in norm_text(e.text) for e in self.elements)
        if no_campo or self.mensagens_iguais(content) <= antes:
            return False
        return self.ultima_bolha_igual(content) is not None

    def count_text(self, needle: str) -> int:
        n = norm_text(needle)
        return sum(1 for e in self.elements if n and n in norm_text(e.text))

    def find(self, *, text: str | None = None, resource_id: str | None = None, desc: str | None = None,
             exact: bool = False) -> list[UiElement]:
        def match(hay: str, needle: str) -> bool:
            return norm_text(hay) == norm_text(needle) if exact else norm_text(needle) in norm_text(hay)

        found = []
        for e in self.elements:
            if text is not None and not match(e.text, text):
                continue
            if desc is not None and not match(e.desc, desc):
                continue
            if resource_id is not None:
                rid = e.resource_id
                if not (rid == resource_id or rid.endswith("/" + resource_id) or rid.endswith(":id/" + resource_id)):
                    continue
            found.append(e)
        return found

    @staticmethod
    def _partes_do_seletor(selector: str) -> list[tuple[str, str, bool]]:
        """(campo, valor, exato) por parte. `chave=valor` casa por substring (como sempre); `chave==valor` casa
        EXATO — `desc=Like` também casa "Liked", e num botão de curtir isso é a diferença entre curtir e descurtir."""
        partes: list[tuple[str, str, bool]] = []
        for part in selector.split("|"):
            exato = "==" in part
            kind, sep, value = part.partition("==" if exato else "=")
            kind, value = kind.strip().lower(), value.strip()
            if not sep or not value:
                partes.append(("text", part.strip(), False))
            elif kind in ("id", "resource-id", "resource_id"):
                partes.append(("resource_id", value, False))
            elif kind in ("desc", "accessibility-id", "accessibility_id", "content-desc"):
                partes.append(("desc", value, exato))
            elif kind == "text":
                partes.append(("text", value, exato))
            elif kind == "dentro":
                # 31.50: o elemento fica DENTRO dos limites de um elemento com este id (o título NA barra, não o item
                # de mesmo texto no menu lateral aberto por cima de outra pasta).
                partes.append(("dentro", value, False))
            else:
                partes.append(("text", part.strip(), False))
        return partes

    @staticmethod
    def parte_sem_valor(selector: str) -> str | None:
        """31.44: a primeira parte do seletor (`a|b`) que declara a chave e deixa o valor VAZIO (`text=`, `desc==`), ou
        `<vazia>` para uma parte em branco; `None` quando todas têm valor. É o que sobra de um molde `text={var}` cuja
        variável chegou sem valor. `_partes_do_seletor` não rejeita: `text=` vira a busca pelo texto literal "text="
        (e uma parte em branco casaria com qualquer elemento), e a etapa só falha depois de gastar as tentativas.
        `chave=valor` com chave desconhecida é texto literal, como em `_partes_do_seletor`, e não entra aqui."""
        for part in selector.split("|"):
            if not part.strip():
                return "<vazia>"
            kind, sep, value = part.partition("==" if "==" in part else "=")
            if sep and not value.strip() and kind.strip().lower() in _CHAVES_DO_SELETOR:
                return part.strip()
        return None

    def _find_partes(self, partes: list[tuple[str, str, bool]], *,
                     variants: Callable[[str], tuple[str, ...]] | None = None) -> list[UiElement]:
        def do_id(e: UiElement, valor: str) -> bool:
            rid = e.resource_id
            return rid == valor or rid.endswith("/" + valor) or rid.endswith(":id/" + valor)

        def casa(e: UiElement, campo: str, valor: str, exato: bool) -> bool:
            if campo == "resource_id":
                return do_id(e, valor)
            if campo == "dentro":
                x1, y1, x2, y2 = e.bounds
                return any(c is not e and do_id(c, valor) and c.bounds[0] <= x1 and c.bounds[1] <= y1
                           and x2 <= c.bounds[2] and y2 <= c.bounds[3] for c in self.elements)
            hay = norm_text(e.text if campo == "text" else e.desc)
            formas = variants(valor) if variants else (valor,)
            return any((hay == norm_text(v)) if exato else (norm_text(v) in hay) for v in formas)

        return [e for e in self.elements if all(casa(e, *p) for p in partes)]

    def find_selector(self, selector: str) -> list[UiElement]:
        """Seletor textual: `id=…`, `text=…`, `desc=…` (accessibility id) ou texto puro; `==` casa exato.
        Partes unidas por `|` precisam casar no MESMO elemento: `id=chat_title|text=QA-001`. `dentro=<id>` (31.50): o
        elemento está dentro dos limites de um elemento com esse id (`text==Inbox|dentro=toolbar`)."""
        return self._find_partes(self._partes_do_seletor(selector))

    def partes_em_elementos_diferentes(self, selector: str) -> bool:
        """31.32: o seletor composto (`a|b`) não casa nenhum elemento, mas CADA parte casa sozinha algum elemento desta
        tela. A tela tem tudo o que ele pede, só que em elementos diferentes: `id=message_input|text=Suporte QA` na
        conversa aberta (r-20261004082521-2f21e2). Seletor de uma parte, ou parte que não casa nada, é `False`."""
        partes = self._partes_do_seletor(selector)
        if len(partes) < 2 or self._find_partes(partes):
            return False
        return all(self._find_partes([p]) for p in partes)

    def find_proof(self, selector: str, *, variants: Callable[[str], tuple[str, ...]]) -> list[UiElement]:
        """`find_selector` para provas locais: cada valor de `text=`/`desc=` é aceito em qualquer das `variants`
        (um `@usuario` também vale sem a arroba — o Instagram quase nunca a mostra)."""
        return self._find_partes(self._partes_do_seletor(selector), variants=variants)

    def text_in_band(self, needle: str, bounds: tuple[int, int, int, int], *, tolerance: int | None = None) -> bool:
        """O texto aparece na MESMA faixa vertical de `bounds`?

        Numa lista, "@ana" visível em qualquer lugar da tela não prova que o botão que vai ser tocado é o dela —
        pode ser a linha de cima. Esta é a diferença entre confirmar o alvo e confirmar que ele existe na tela.
        """
        n = norm_text(needle)
        if not n:
            return False
        y1, y2 = bounds[1], bounds[3]
        tol = tolerance if tolerance is not None else max(40, (y2 - y1))
        for e in self.elements:
            centro = (e.bounds[1] + e.bounds[3]) // 2
            if y1 - tol <= centro <= y2 + tol and n in norm_text(f"{e.text} {e.desc}"):
                return True
        return False

    def text_in_card(self, needle: str, alvo: UiElement) -> bool:
        """O texto está no CARTÃO de `alvo` — uma publicação num feed, com o botão de curtir como alvo?

        Num cartão a legenda fica ABAIXO dos botões ("N likes", legenda, "View all comments"), e a faixa simétrica de
        `text_in_band`, pensada para uma linha de lista, não a alcança com segurança: no android-06 (720x1280) o
        coração tinha 92 px de altura e a legenda começa perto do limite da faixa. O cartão vai do topo do alvo até a
        METADE do caminho para o próximo elemento com o mesmo id abaixo (o mesmo botão no cartão seguinte, curtido ou
        não), ou para o fim da tela. A metade, e não o botão seguinte inteiro, deixa de fora a mídia do cartão de
        baixo — o Instagram descreve a imagem ("may be an image of text that says…") e ela pode repetir o texto da
        legenda. Sem o texto nessa região, a resposta é não: na dúvida, o toque é recusado (r-20260928165254-e31953).
        """
        n = norm_text(needle)
        if not n:
            return False
        topo, base = alvo.bounds[1], alvo.bounds[3]
        seguintes = [e.bounds[1] for e in self.elements
                     if alvo.resource_id and e.id != alvo.id and e.resource_id == alvo.resource_id
                     and e.bounds[1] >= base]
        fundo = min(seguintes) if seguintes else max((e.bounds[3] for e in self.elements), default=base)
        limite = base + max(0, fundo - base) // 2
        for e in self.elements:
            centro = (e.bounds[1] + e.bounds[3]) // 2
            if topo <= centro < limite and n in norm_text(f"{e.text} {e.desc}"):
                return True
        return False

    def prompt_lines(self, max_lines: int, scale: float = 1.0, *, protect: tuple[str, ...] = (),
                     boost: tuple[str, ...] = (), ocultar: frozenset[str] = frozenset()) -> list[str]:
        """Linhas para o prompt. A árvore local fica COMPLETA (seletores, guardas e pós-condições usam tudo); só o
        que vai ao modelo é limitado — e por relevância, não pelo fim do documento: primeiro o que dá para operar
        (clicável/editável/rolável), depois o que tem texto ou descrição; ordem de tela preservada.

        `protect`: textos que não podem ser cortados em 80 caracteres quando aparecem no elemento (ex.: `{content}`
        de uma DM) — ver `UiElement.line`.

        `boost` (item 7.6, dieta do contexto do ator): textos do ALVO desta etapa (bindings, commit_selector,
        known_selectors) — o elemento que casa ganha +6 no placar e não é cortado por uma tela grande cheia de
        decoração; os vizinhos imediatos (mesma faixa de leitura) ganham +3, porque um rótulo ao lado do alvo
        costuma ser o que confirma que é ELE."""
        protect = tuple(norm_text(p) for p in protect if p and norm_text(p))
        # `ocultar` (item 31.35): `resource_id` completos que não vão ao modelo (a barra do navegador).
        elementos = [e for e in self.elements if e.resource_id not in ocultar] if ocultar else self.elements
        boost_terms = tuple(norm_text(b) for b in boost if b and norm_text(b))
        if len(elementos) <= max_lines:
            return [e.line(scale, protect=protect) for e in elementos]

        def casa_boost(e: UiElement) -> bool:
            if not boost_terms:
                return False
            hay = norm_text(f"{e.text} {e.desc} {e.resource_id}")
            return any(b in hay for b in boost_terms)

        boosted = {i for i, e in enumerate(elementos) if casa_boost(e)}

        def score(i: int) -> int:
            e = elementos[i]
            base = (4 * (e.clickable or e.editable or e.scrollable) + 2 * bool(e.text) + bool(e.desc)
                    + (e.focused or e.checked) + e.enabled)
            if i in boosted:
                return base + 6
            if (i - 1) in boosted or (i + 1) in boosted:
                return base + 3
            return base

        ranked = sorted(range(len(elementos)), key=lambda i: (-score(i), i))[:max_lines]
        lines = [elementos[i].line(scale, protect=protect) for i in sorted(ranked)]
        lines.append(f"(+{len(elementos) - max_lines} elementos menos relevantes omitidos; use find_element para procurá-los)")
        return lines

    def signature(self, *, estrutural: bool = False) -> str:
        """Assinatura estável da tela para detectar ciclos sem progresso.

        `estrutural=True` ignora texto, descrição e estado: só classe e resource-id. Serve ao ciclo de DUAS ações
        (tocar → voltar → tocar…): a tela do post traz "há 32 minutos" e contagens que mudam a cada visita, e com
        texto na conta as voltas nunca eram iguais entre si (execução f41d10).
        """
        import hashlib

        h = hashlib.sha1()
        for e in self.elements:
            linha = (f"{e.class_name}|{e.resource_id}\n" if estrutural
                     else f"{e.class_name}|{e.resource_id}|{e.text}|{e.desc}|{e.checked}|{e.focused}\n")
            h.update(linha.encode())
        return h.hexdigest()[:16]


def parse_hierarchy(xml_text: str, *, max_elements: int = 1500,
                    regras: tuple[RegraDeTelaSensivel, ...] = (), sempre_sensivel: str | None = None) -> UiTree:
    """`regras` e `sempre_sensivel` são os dois critérios de "tela sensível" que faltavam (achado #127).

    Antes havia um só: `password=true` num campo. Qualquer outra tela — desafio de 2FA, dados da conta, conversa
    de terceiro, tela da VM-loja com a conta Google — virava JPEG em `data/evidence` e imagem no corpo da
    requisição ao provedor de IA. `sempre_sensivel` é o que a VM-loja usa: lá TODA tela é da conta Google do
    parque, e não há critério de conteúdo que valha a pena discutir.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return UiTree(elements=[], packages=[], sensitive=bool(sempre_sensivel),
                      sensitive_reason=sempre_sensivel)
    elements: list[UiElement] = []
    packages: list[str] = []
    truncada = False
    sensitive = bool(sempre_sensivel)
    motivo: str | None = sempre_sensivel
    # O pedido de CÓDIGO só conta quando há ONDE digitá-lo. Sem esta condição, a linha "Autenticação de dois fatores"
    # do MENU de configurações marcaria a tela inteira como sensível — e o executor pararia a etapa pedindo
    # intervenção humana no meio de uma navegação comum. Medido no desenho, não depois. A conta travada não tem essa
    # condição (ADR-055): a tela dela só tem botões. Ver `detectar_trava_generica`.
    textos_normalizados: list[str] = []
    tem_onde_digitar = False
    n = 0
    for node in root.iter():
        a = node.attrib
        if "bounds" not in a:
            continue
        m = BOUNDS_RE.match(a.get("bounds", ""))
        if not m:
            continue
        bounds = tuple(int(g) for g in m.groups())
        if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
            continue
        pkg = a.get("package", "")
        if pkg and pkg not in packages:
            packages.append(pkg)
        is_password = a.get("password") == "true"
        if is_password and not sensitive:             # varre o documento inteiro: campo de senha nunca passa despercebido
            sensitive, motivo = True, motivo or MOTIVO_SENHA
        texto_bruto = (a.get("text", "") or "") + " " + (a.get("content-desc", "") or "")
        # `_sem_acento` normaliza Unicode, e a hierarquia tem milhares de nós sem texto nenhum: não pagar por eles.
        normalizado = _sem_acento(texto_bruto) if texto_bruto.strip() else ""
        rid_bruto = a.get("resource-id", "") or ""
        cls_bruta = a.get("class", node.tag) or ""
        if eh_campo_de_texto(cls_bruta):
            tem_onde_digitar = True
        if normalizado:
            textos_normalizados.append(normalizado)
        for regra in regras:                          # varre TODO o documento, não só os elementos que entram no corte
            if regra.package and pkg != regra.package:
                continue
            casou = (any(rid_bruto.endswith(s) for s in regra.resource_ids if s)
                     or any(_sem_acento(t) in normalizado for t in regra.texts if _sem_acento(t)))
            if casou and not sensitive:
                sensitive, motivo = True, regra.motivo()
        if len(elements) >= max_elements:
            truncada = True
            continue
        text = a.get("text", "") or ""
        desc = a.get("content-desc", "") or ""
        rid = rid_bruto
        cls = cls_bruta
        clickable = a.get("clickable") == "true"
        scrollable = a.get("scrollable") == "true"
        editable = eh_campo_de_texto(cls)
        interesting = bool(text or desc or clickable or scrollable or editable or a.get("checkable") == "true")
        if not interesting and not rid:
            continue
        if not interesting and rid.startswith("android:id/"):
            continue
        n += 1
        elements.append(UiElement(
            id=f"e{n}", text=MASK if (is_password and text) else text, desc=desc, resource_id=rid, class_name=cls,
            package=pkg, bounds=bounds,  # type: ignore[arg-type]
            clickable=clickable, enabled=a.get("enabled", "true") == "true", focused=a.get("focused") == "true",
            scrollable=scrollable, editable=editable, checked=a.get("checked") == "true", password=is_password))
    trava = detectar_trava_generica("\n".join(textos_normalizados), tem_onde_digitar=tem_onde_digitar)
    if trava is not None:
        if not sensitive:
            sensitive, motivo = True, MOTIVO_DESAFIO
        # O código EM SI não pode ir ao modelo nem para o histórico. Só aqui, e só numa tela já classificada como
        # desafio: fora dela, "1234" é preço, contador ou ano, e mascarar isso seria apagar metade da interface.
        for e in elements:
            if _SO_DIGITOS.match(e.text):
                e.text = MASK
    return UiTree(elements=elements, packages=packages, sensitive=sensitive, sensitive_reason=motivo,
                  conta_travada=trava, truncada=truncada)
