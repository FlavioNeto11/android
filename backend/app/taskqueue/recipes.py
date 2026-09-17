"""Receitas: a IA descobre como cumprir uma etapa UMA vez; depois a etapa é repetida por seletores, sem modelo.

Princípios:
- a receita é só uma FONTE DE DECISÃO. Tudo o que protege a execução (validação da ferramenta, guardas de commit,
  registro da intenção antes de agir, detecção de ciclo, verificação da pós-condição) continua no executor;
- um seletor só vale se casar EXATAMENTE UM elemento habilitado na tela atual; qualquer dúvida é divergência, e a
  IA assume aquela etapa a partir da tela em que o aparelho está;
- nada sensível é aprendido: campo de senha nunca vira alvo e texto digitado precisa ser 100 % coberto por
  parâmetros da execução (o que sobra seria dado pessoal ou invenção do modelo);
- a chave da receita é a etapa em forma de TEMPLATE (antes de resolver {instance_id}, {recipient}…) + app + versão
  do app: atualização do app é só uma busca sem resultado, e a etapa é reaprendida.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from ..automation.hierarchy import UiElement, UiTree
from ..db import Database, dumps, loads
from ..models import PlanStep
from ..planning.provider import Decision
from ..util import norm_text, now_iso

SENSITIVE_PARAM = re.compile(r"pass|senha|pin\b|otp|token|secret|segredo|c[oó]digo|code", re.IGNORECASE)
READ_ONLY = {"observe_screen", "find_element", "wait_for", "verify_state"}
TARGETED = {"tap", "long_press", "type_text", "collect_list"}                  # precisam de um elemento-alvo para serem repetíveis
UNSAFE_TO_REPLAY = {"press_back", "press_home", "drag"}        # dependem do estado/coords de quem aprendeu
SELECTOR_RANK = ("rid+text", "rid+desc", "rid", "desc", "text")
QUARANTINE_AFTER = 3
MAX_ACTIONS = 8
TEMPLATE_RE = re.compile(r"\{([a-z_][a-z0-9_]*)\}")


class RecipeDiverged(Exception):
    pass


# ------------------------------------------------------------------ chave da etapa
def step_template_hash(step: PlanStep) -> str:
    """Identidade da etapa em forma de template. Título e objetivo ficam de fora (o planejador os reescreve)."""
    post = step.postcondition
    # cópias de um bloco for_each (open_conversation_i1, _i2…) compartilham a identidade da etapa-modelo
    raw = json.dumps([getattr(step, "template_key", None) or step.key, step.side_effect, post.kind, post.value,
                      post.required_delivery_level.value if post.required_delivery_level else None,
                      sorted(step.commit_guard)], ensure_ascii=False)
    return hashlib.sha1(raw.encode()).hexdigest()[:20]


# ------------------------------------------------------------------ des-templatização
def detemplate(text: str, variables: dict[str, str]) -> tuple[str, bool, bool]:
    """Troca valores conhecidos por {nome} (o mais longo primeiro).
    Devolve (texto, usou_alguma_variável, ficou_100%_coberto_por_variáveis)."""
    out, used = text, False
    for name, value in sorted(variables.items(), key=lambda kv: -len(kv[1] or "")):
        if value and len(value) >= 3 and value in out:
            out, used = out.replace(value, "{" + name + "}"), True
    covered = used and not TEMPLATE_RE.sub("", out).strip(" \t\r\n.,;:!?-—()[]\"'“”")
    return out, used, covered


def retemplate(text: str, variables: dict[str, str]) -> str:
    missing = [m for m in TEMPLATE_RE.findall(text) if m not in variables]
    if missing:
        raise RecipeDiverged(f"parâmetro ausente nesta execução: {', '.join(missing)}")
    return TEMPLATE_RE.sub(lambda m: variables[m.group(1)], text)


# ------------------------------------------------------------------ seletores
def _match(tree: UiTree, rid: str | None, text: str | None, desc: str | None) -> list[UiElement]:
    out = []
    for e in tree.elements:
        if rid is not None and e.resource_id != rid:
            continue
        if text is not None and norm_text(e.text) != norm_text(text):
            continue
        if desc is not None and norm_text(e.desc) != norm_text(desc):
            continue
        out.append(e)
    return out


def _combo(kind: str, el: dict[str, Any] | UiElement) -> tuple[str | None, str | None, str | None] | None:
    g = (lambda k: el.get(k)) if isinstance(el, dict) else (lambda k: getattr(el, k))
    rid, text, desc = g("resource_id") or None, g("text") or None, g("desc") or None
    parts = {"rid+text": (rid, text, None), "rid+desc": (rid, None, desc), "rid": (rid, None, None),
             "desc": (None, None, desc), "text": (None, text, None)}[kind]
    needed = {"rid+text": (rid, text), "rid+desc": (rid, desc), "rid": (rid,), "desc": (desc,), "text": (text,)}[kind]
    return parts if all(needed) else None


def unique_selectors(tree: UiTree, el: UiElement) -> list[str]:
    """No momento da ação: quais combinações identificam SOZINHAS o alvo nesta tela (gravado junto do alvo)."""
    kinds = []
    for kind in SELECTOR_RANK:
        combo = _combo(kind, el)
        if combo and len(_match(tree, *combo)) == 1:
            kinds.append(kind)
    return kinds


def _usable_text(text: str, variables: dict[str, str]) -> str | None:
    """Texto de elemento que pode virar seletor: coberto por parâmetros, ou rótulo fixo curto e sem números."""
    templ, used, covered = detemplate(text, variables)
    if used:
        return templ if covered else None
    return text if (len(text) <= 40 and not re.search(r"\d", text)) else None


def build_selectors(target: dict[str, Any], variables: dict[str, str]) -> list[dict[str, str]]:
    sels: list[dict[str, str]] = []
    for kind in target.get("unique") or []:
        combo = _combo(kind, target)
        if combo is None:
            continue
        rid, text, desc = combo
        sel: dict[str, str] = {"kind": kind}
        if rid:
            sel["rid"] = rid
        if text is not None:
            usable = _usable_text(text, variables)
            if usable is None:
                continue
            sel["text"] = usable
        if desc is not None:
            usable = _usable_text(desc, variables)
            if usable is None:
                continue
            sel["desc"] = usable
        sels.append(sel)
    return sels


def resolve_selectors(tree: UiTree, selectors: list[dict[str, str]], variables: dict[str, str]) -> UiElement | None:
    """Primeiro seletor (na ordem de confiança) que casa exatamente um elemento HABILITADO."""
    for sel in selectors:
        text = retemplate(sel["text"], variables) if "text" in sel else None
        desc = retemplate(sel["desc"], variables) if "desc" in sel else None
        found = [e for e in _match(tree, sel.get("rid"), text, desc) if e.enabled]
        if len(found) == 1:
            return found[0]
    return None


# ------------------------------------------------------------------ destilação
def distill(action_rows: list[sqlite3.Row], variables: dict[str, str]) -> tuple[list[dict[str, Any]] | None, str]:
    """Ações executadas pela IA numa tentativa limpa → receita. Devolve (ações | None, motivo)."""
    secret_values = {v for k, v in variables.items() if v and SENSITIVE_PARAM.search(k)}
    out: list[dict[str, Any]] = []
    pending_scrolls: list[str] = []
    for r in action_rows:
        tool, status = r["tool"], r["status"]
        if tool in READ_ONLY or tool in ("step_done", "step_blocked"):
            continue
        if status != "done" or r["source"] != "ai":
            return None, f"tentativa não foi limpa ({tool}: {status}/{r['source']})"
        if tool in UNSAFE_TO_REPLAY:
            return None, f"{tool} depende do estado de quem aprendeu"
        args = loads(r["args"], {}) or {}
        target = loads(r["target"]) if r["target"] else None
        if tool == "scroll":
            pending_scrolls.append(args.get("direction", "down"))
            continue
        item: dict[str, Any] = {"tool": tool, "commit": bool(r["side_effect"]), "why": (r["rationale"] or "")[:160]}
        if tool == "type_text":
            text = str(args.get("text", ""))
            if any(s and s in text for s in secret_values):
                return None, "texto digitado contém parâmetro sensível"
            templ, _, covered = detemplate(text, variables)
            if not covered:
                return None, "texto digitado não é 100 % coberto por parâmetros"
            item["args"] = {"text": templ, "clear_first": bool(args.get("clear_first", True)),
                            "press_enter": bool(args.get("press_enter", False))}
            if args.get("element_id") is None:
                item["selectors"] = []
        elif tool == "long_press":
            item["args"] = {"duration_ms": int(args.get("duration_ms", 800))}
        elif tool == "open_app":
            item["args"] = {"package": args.get("package")}
        elif tool == "collect_list":
            item["args"] = {"item_selector": str(args.get("item_selector") or ""),
                            "exclude": [str(x) for x in (args.get("exclude") or [])]}
        else:
            item["args"] = {}
        needs_target = tool in TARGETED and not (tool == "type_text" and args.get("element_id") is None)
        if needs_target:
            if not target:
                return None, f"{tool} sem alvo resolvido (coordenadas soltas ou campo de senha)"
            sels = build_selectors(target, variables)
            if not sels:
                return None, f"{tool}: o alvo não tinha seletor estável e único"
            if item["commit"] and all(s["kind"] == "text" for s in sels):
                return None, "ação de efeito externo só com seletor por texto — fraco demais"
            item["selectors"] = sels
        if pending_scrolls:
            item["scroll"] = {"direction": pending_scrolls[-1], "max": len(pending_scrolls) + 3}
            pending_scrolls = []
        out.append(item)
        if item["commit"]:
            break                                   # depois do efeito não há o que repetir: só verificar
    if pending_scrolls:
        return None, "rolagem sem ação-alvo depois dela"
    if len(out) > MAX_ACTIONS:
        return None, "ações demais para uma receita confiável"
    return out, "ok"


# ------------------------------------------------------------------ replay
@dataclass
class Replayer:
    """Devolve, a cada observação, a próxima decisão da receita — já apontando para o element_id DA TELA ATUAL."""
    recipe_id: int
    version: int
    actions: list[dict[str, Any]]
    variables: dict[str, str]
    idx: int = 0
    scrolls: int = 0
    done_actions: int = 0
    diverged: str | None = None
    log: list[str] = field(default_factory=list)

    @property
    def exhausted(self) -> bool:
        return self.idx >= len(self.actions)

    def next(self, tree: UiTree) -> Decision | None:
        if self.exhausted:
            return None
        act = self.actions[self.idx]
        tag = f"[receita v{self.version}] {act.get('why') or act['tool']}"
        args: dict[str, Any] = {"rationale": tag, **act.get("args", {})}
        if "text" in args:
            args["text"] = retemplate(args["text"], self.variables)
        if act.get("selectors"):
            el = resolve_selectors(tree, act["selectors"], self.variables)
            if el is None:
                hint = act.get("scroll")
                if hint and self.scrolls < int(hint["max"]):
                    self.scrolls += 1
                    return Decision(tool="scroll", args={"rationale": f"{tag} (rolando até o alvo aparecer)",
                                                         "direction": hint["direction"], "element_id": None,
                                                         "expect_done": False})
                raise RecipeDiverged(f"ação {self.idx + 1} ({act['tool']}): alvo ausente ou ambíguo nesta tela")
            args["element_id"] = el.id
        if act["tool"] in ("tap", "long_press"):
            args.setdefault("element_id", None)
            args.update({"x": None, "y": None, "is_commit_action": bool(act.get("commit"))})
        elif act["tool"] == "type_text":
            args.setdefault("element_id", None)
            args["is_commit_action"] = bool(act.get("commit"))
        args["expect_done"] = False
        self.idx += 1
        self.scrolls = 0
        self.done_actions += 1
        return Decision(tool=act["tool"], args=args)


# ------------------------------------------------------------------ persistência
class RecipeStore:
    def __init__(self, db: Database):
        self.db = db

    def find(self, package: str | None, app_version: str | None, step_hash: str | None) -> sqlite3.Row | None:
        if not (package and app_version and step_hash):
            return None
        return self.db.one("SELECT * FROM recipes WHERE app_package=? AND app_version=? AND step_hash=? AND status='active'"
                           " ORDER BY version DESC LIMIT 1", (package, app_version, step_hash))

    def save(self, *, package: str, app_version: str, step_hash: str, step_key: str, actions: list[dict[str, Any]],
             learned_from: str) -> int | None:
        """Grava uma versão nova SÓ se não houver receita ativa (a ativa só sai por quarentena)."""
        with self.db.tx():
            if self.find(package, app_version, step_hash) is not None:
                return None
            ver = int(self.db.scalar("SELECT COALESCE(MAX(version),0)+1 FROM recipes WHERE app_package=? AND app_version=?"
                                     " AND step_hash=?", (package, app_version, step_hash)))
            cur = self.db.execute(
                "INSERT INTO recipes(app_package, app_version, step_hash, step_key, version, actions, learned_from_step,"
                " created_at) VALUES (?,?,?,?,?,?,?,?)",
                (package, app_version, step_hash, step_key, ver, dumps(actions), learned_from, now_iso()))
            return int(cur.lastrowid or 0)

    def result(self, recipe_id: int, ok: bool) -> bool:
        """Conta o uso. Devolve True se a receita entrou em quarentena agora."""
        if ok:
            self.db.execute("UPDATE recipes SET replay_ok=replay_ok+1, consecutive_fail=0, last_used_at=? WHERE id=?",
                            (now_iso(), recipe_id))
            return False
        self.db.execute("UPDATE recipes SET replay_fail=replay_fail+1, consecutive_fail=consecutive_fail+1, last_used_at=?"
                        " WHERE id=?", (now_iso(), recipe_id))
        row = self.db.one("SELECT consecutive_fail FROM recipes WHERE id=?", (recipe_id,))
        if row and row["consecutive_fail"] >= QUARANTINE_AFTER:
            self.db.execute("UPDATE recipes SET status='quarantined' WHERE id=?", (recipe_id,))
            return True
        return False

    def shadow(self, recipe_id: int, agreed: bool) -> None:
        self.db.execute("UPDATE recipes SET shadow_total=shadow_total+1, shadow_agree=shadow_agree+? WHERE id=?",
                        (int(agreed), recipe_id))

    def replayer(self, row: sqlite3.Row, variables: dict[str, str]) -> Replayer:
        return Replayer(recipe_id=row["id"], version=row["version"], actions=loads(row["actions"], []), variables=variables)
