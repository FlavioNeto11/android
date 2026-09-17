"""Hierarquia do UiAutomator → lista compacta de elementos para seletores e para o modelo."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass

from ..util import norm_text

BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")
MASK = "••••"


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

    def line(self, scale: float = 1.0) -> str:
        """Uma linha compacta para o prompt do modelo. `scale` = pixels do aparelho por pixel do espaço de coordenadas
        que o modelo enxerga: os limites vão no MESMO espaço da imagem, senão um x,y tirado deles cairia fora do alvo."""
        parts = [self.id, self.class_name.rsplit(".", 1)[-1]]
        if self.text:
            parts.append(f'text="{self.text[:80]}"')
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
    sensitive: bool          # há campo de senha na tela → não enviar/gravar imagem

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

    def prompt_lines(self, max_lines: int, scale: float = 1.0) -> list[str]:
        """Linhas para o prompt. A árvore local fica COMPLETA (seletores, guardas e pós-condições usam tudo); só o
        que vai ao modelo é limitado — e por relevância, não pelo fim do documento: primeiro o que dá para operar
        (clicável/editável/rolável), depois o que tem texto ou descrição; ordem de tela preservada."""
        if len(self.elements) <= max_lines:
            return [e.line(scale) for e in self.elements]

        def score(e: UiElement) -> int:
            return (4 * (e.clickable or e.editable or e.scrollable) + 2 * bool(e.text) + bool(e.desc)
                    + (e.focused or e.checked) + e.enabled)

        ranked = sorted(range(len(self.elements)), key=lambda i: (-score(self.elements[i]), i))[:max_lines]
        lines = [self.elements[i].line(scale) for i in sorted(ranked)]
        lines.append(f"(+{len(self.elements) - max_lines} elementos menos relevantes omitidos; use find_element para procurá-los)")
        return lines

    def signature(self) -> str:
        """Assinatura estável da tela para detectar ciclos sem progresso."""
        import hashlib

        h = hashlib.sha1()
        for e in self.elements:
            h.update(f"{e.class_name}|{e.resource_id}|{e.text}|{e.desc}|{e.checked}|{e.focused}\n".encode())
        return h.hexdigest()[:16]


def parse_hierarchy(xml_text: str, *, max_elements: int = 1500) -> UiTree:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return UiTree(elements=[], packages=[], sensitive=False)
    elements: list[UiElement] = []
    packages: list[str] = []
    sensitive = False
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
        sensitive = sensitive or is_password          # varre o documento inteiro: campo de senha nunca passa despercebido
        if len(elements) >= max_elements:
            continue
        text = a.get("text", "") or ""
        desc = a.get("content-desc", "") or ""
        rid = a.get("resource-id", "") or ""
        cls = a.get("class", node.tag) or ""
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
    return UiTree(elements=elements, packages=packages, sensitive=sensitive)
