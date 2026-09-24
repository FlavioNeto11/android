"""Hierarquia do UiAutomator → lista compacta de elementos para seletores e para o modelo."""
from __future__ import annotations

import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass

from ..util import norm_text


def _sem_acento(value: str | None) -> str:
    """Minúsculas E sem acento. `norm_text` só tira espaço e caixa — e a tela de desafio chega escrita em
    português, com acento, escolhido pelo idioma DO APARELHO. Comparar sem isto é acertar por sorte."""
    base = norm_text(value)
    return "".join(c for c in unicodedata.normalize("NFD", base) if unicodedata.category(c) != "Mn")


BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")
MASK = "••••"
TEXT_CAP = 80             # corte padrão de um elemento no prompt: a UI raramente mostra mais que isso por vez
PROTECTED_TEXT_CAP = 400  # elemento que casa com um texto protegido (ex.: {content} de uma DM): acima do max_length
                          # do rascunho (300 — social/service.py) para nunca truncar o próprio conteúdo comprovado

#: Texto que denuncia uma tela de DESAFIO — 2FA, código por e-mail/SMS, "confirme que é você". Achado #127: até
#: aqui o único critério de "tela sensível" era `password=true`, então uma tela de verificação (que não tem campo
#: de senha nenhum) virava JPEG em disco e imagem no corpo da requisição ao provedor de IA.
#:
#: Casa contra o texto NORMALIZADO (`_sem_acento`: minúsculas, sem acento), por isso está escrito sem acento aqui.
#:
#: As frases vêm de `integrations/instagram/navigation.SIGNALS` (`two_factor` e `challenge`, en e pt), que é o
#: classificador que já sabia reconhecer essas telas — e que só era consultado DEPOIS de a imagem ter sido
#: capturada e enviada. `test_sensitive_input` confere, string por string, que os dois concordam: dois
#: classificadores discordando sobre a MESMA tela seria pior do que ter um só.
_DESAFIO = re.compile(
    # português
    r"(autenticacao de dois fatores|verificacao em duas etapas|"
    r"codigo de (?:seguranca|verificacao|confirma|acesso|autenticacao|backup)|codigo de \d+ digitos|"
    r"insira o codigo|digite o codigo|enviamos um codigo|"
    r"confirme que (?:voce )?e (?:um[ae]? pessoa|humano|voce)|ajude a confirmar|verifique sua conta|"
    r"detectamos|suspeit|nao sou um rob|"
    # inglês
    r"two.?factor|two.?step verification|security code|confirmation code|verification code|"
    r"one.?time (?:code|password)|backup code|\d.?digit|"
    r"enter the code|we sent (?:you )?a code|"
    r"confirm it.?s you|confirm you.?re human|help us confirm|verify your account|"
    r"suspicious|unusual (?:login|activity|attempt)|we detected|"
    r"captcha|i.?m not a robot)")

#: Numa tela de desafio, um texto que é SÓ dígitos é o código — inclusive o que o operador acabou de digitar no
#: campo. Fora de uma tela de desafio este mesmo formato é preço, contador ou ano, e por isso a máscara depende
#: da tela, não do elemento: é a diferença entre proteger o código e mascarar metade da interface.
_SO_DIGITOS = re.compile(r"^\s*[0-9][0-9 \-]{2,10}\s*$")

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


@dataclass(slots=True)
class UiTree:
    elements: list[UiElement]
    packages: list[str]
    sensitive: bool          # tela sensível → não enviar/gravar imagem (ver `sensitive_reason`)
    #: POR QUE a tela é sensível, em português, para virar mensagem de etapa e evidência. `None` quando não é.
    #: Existe porque o critério deixou de ser um só (achado #127) e "campo de senha" passou a ser mentira em
    #: metade dos casos — e a mensagem que o operador lê é a única coisa que explica por que a IA parou.
    sensitive_reason: str | None = None

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

    def sent_as_message(self, content: str) -> bool | None:
        """Prova determinística de 'texto enviado numa conversa' (achado #102), sem chamar o modelo: o conteúdo
        aparece num elemento que NÃO é editável (uma mensagem já publicada no fio) e não sobra em nenhum campo
        editável (o campo de escrita, que some/limpa depois do envio — se ainda tiver o texto, ele não saiu de
        lá e não está comprovado). `None` quando não há conteúdo para provar (etapa sem `content` conhecido);
        chamador cai para o julgamento do modelo nesse caso e em qualquer resultado False."""
        n = norm_text(content)
        if not n:
            return None
        em_bolha = any(not e.editable and n in norm_text(f"{e.text} {e.desc}") for e in self.elements)
        no_campo = any(e.editable and n in norm_text(e.text) for e in self.elements)
        return em_bolha and not no_campo

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

    def find_selector(self, selector: str) -> list[UiElement]:
        """Seletor textual: `id=…`, `text=…`, `desc=…` (accessibility id) ou texto puro.
        Partes unidas por `|` precisam casar no MESMO elemento: `id=chat_title|text=QA-001`."""
        kwargs: dict[str, str] = {}
        for part in selector.split("|"):
            kind, sep, value = part.partition("=")
            kind, value = kind.strip().lower(), value.strip()
            if not sep or not value:
                kwargs["text"] = part.strip()
            elif kind in ("id", "resource-id", "resource_id"):
                kwargs["resource_id"] = value
            elif kind in ("desc", "accessibility-id", "accessibility_id", "content-desc"):
                kwargs["desc"] = value
            elif kind == "text":
                kwargs["text"] = value
            else:
                kwargs["text"] = part.strip()
        return self.find(**kwargs)

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

    def prompt_lines(self, max_lines: int, scale: float = 1.0, *, protect: tuple[str, ...] = ()) -> list[str]:
        """Linhas para o prompt. A árvore local fica COMPLETA (seletores, guardas e pós-condições usam tudo); só o
        que vai ao modelo é limitado — e por relevância, não pelo fim do documento: primeiro o que dá para operar
        (clicável/editável/rolável), depois o que tem texto ou descrição; ordem de tela preservada.

        `protect`: textos que não podem ser cortados em 80 caracteres quando aparecem no elemento (ex.: `{content}`
        de uma DM) — ver `UiElement.line`."""
        protect = tuple(norm_text(p) for p in protect if p and norm_text(p))
        if len(self.elements) <= max_lines:
            return [e.line(scale, protect=protect) for e in self.elements]

        def score(e: UiElement) -> int:
            return (4 * (e.clickable or e.editable or e.scrollable) + 2 * bool(e.text) + bool(e.desc)
                    + (e.focused or e.checked) + e.enabled)

        ranked = sorted(range(len(self.elements)), key=lambda i: (-score(self.elements[i]), i))[:max_lines]
        lines = [self.elements[i].line(scale, protect=protect) for i in sorted(ranked)]
        lines.append(f"(+{len(self.elements) - max_lines} elementos menos relevantes omitidos; use find_element para procurá-los)")
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
    sensitive = bool(sempre_sensivel)
    motivo: str | None = sempre_sensivel
    # A tela de desafio só conta quando há ONDE digitar o código. Sem esta condição, a linha "Autenticação de dois
    # fatores" do MENU de configurações marcaria a tela inteira como sensível — e o executor pararia a etapa
    # pedindo intervenção humana no meio de uma navegação comum. Medido no desenho, não depois.
    fala_de_desafio = False
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
        if "EditText" in cls_bruta:
            tem_onde_digitar = True
        if normalizado and _DESAFIO.search(normalizado):
            fala_de_desafio = True
        for regra in regras:                          # varre TODO o documento, não só os elementos que entram no corte
            if regra.package and pkg != regra.package:
                continue
            casou = (any(rid_bruto.endswith(s) for s in regra.resource_ids if s)
                     or any(_sem_acento(t) in normalizado for t in regra.texts if _sem_acento(t)))
            if casou and not sensitive:
                sensitive, motivo = True, regra.motivo()
        if len(elements) >= max_elements:
            continue
        text = a.get("text", "") or ""
        desc = a.get("content-desc", "") or ""
        rid = rid_bruto
        cls = cls_bruta
        clickable = a.get("clickable") == "true"
        scrollable = a.get("scrollable") == "true"
        editable = "EditText" in cls
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
    if fala_de_desafio and tem_onde_digitar:
        if not sensitive:
            sensitive, motivo = True, MOTIVO_DESAFIO
        # O código EM SI não pode ir ao modelo nem para o histórico. Só aqui, e só numa tela já classificada como
        # desafio: fora dela, "1234" é preço, contador ou ano, e mascarar isso seria apagar metade da interface.
        for e in elements:
            if _SO_DIGITOS.match(e.text):
                e.text = MASK
    return UiTree(elements=elements, packages=packages, sensitive=sensitive, sensitive_reason=motivo)
