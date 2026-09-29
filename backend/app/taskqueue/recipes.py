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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..automation.hierarchy import UiElement, UiTree
from ..db import Database, Row, dumps, loads
from ..metricas import metricas
from ..models import PlanStep
from ..planning.provider import Decision
from ..util import norm_text, now_iso

SENSITIVE_PARAM = re.compile(r"pass|senha|pin\b|otp|token|secret|segredo|c[oó]digo|code", re.IGNORECASE)
READ_ONLY = {"observe_screen", "find_element", "wait_for", "verify_state"}
TARGETED = {"tap", "long_press", "type_text", "collect_list"}                  # precisam de um elemento-alvo para serem repetíveis
UNSAFE_TO_REPLAY = {"press_back", "press_home", "drag"}        # dependem do estado/coords de quem aprendeu
# ADR-025: a credencial é DA execução (apagada quando ela termina) e o endereço é do comando — uma receita que os
# repetisse noutra execução digitaria/abriria o que ninguém forneceu para ela.
UNSAFE_TO_REPLAY |= {"type_secret", "open_url"}
SELECTOR_RANK = ("rid+text", "rid+desc", "rid", "desc", "text")
QUARANTINE_AFTER = 3
MAX_ACTIONS = 8
TEMPLATE_RE = re.compile(r"\{([a-z_][a-z0-9_]*)\}")


class RecipeDiverged(Exception):
    pass


# ------------------------------------------------------------------ funil medido (adendo v0.20, C5)
#: Texto da divergência → motivo curto do retorno à IA. O texto é livre (vem de `RecipeDiverged` e das rejeições do
#: executor) e não pode virar rótulo de métrica: rótulo é conjunto pequeno e fixo. A ordem importa: os prefixos que o
#: executor escreve (ação inválida, efeito externo) vêm antes, porque o complemento deles é livre e poderia conter
#: outro trecho da lista.
_MOTIVOS_DO_RETORNO: tuple[tuple[str, str], ...] = (
    ("ação da receita inválida", "acao_invalida"),
    ("alvo do efeito externo", "alvo_do_efeito"),
    ("guarda do efeito externo", "guarda_do_efeito"),
    ("parâmetro ausente", "parametro_ausente"),
    ("pós-condição não apareceu", "pos_condicao"),
    ("alvo ausente ou ambíguo", "alvo_ausente"),
)


def motivo_do_retorno(texto: str | None) -> str:
    """Classe da divergência que devolveu a etapa à IA, em vocabulário fechado (rótulo de `receita.retorno_ia`)."""
    t = (texto or "").casefold()
    return next((motivo for trecho, motivo in _MOTIVOS_DO_RETORNO if trecho in t), "outro")


def contar_retorno_ia(texto: str | None) -> None:
    """A receita divergiu e a IA vai decidir esta etapa daqui em diante. Chamado pelo executor UMA vez por tentativa
    de etapa (o estado da receita é refeito a cada tentativa; uma nova tentativa que diverge de novo conta de novo),
    no instante em que a IA é de fato consultada — é esse o custo que a divergência cobra, termine a tentativa em
    sucesso, nova tentativa ou falha."""
    metricas.contar("receita.retorno_ia", motivo=motivo_do_retorno(texto))


# ------------------------------------------------------------------ chave da etapa
#: Parâmetros que nunca identificam uma etapa: são de execução, não de intenção.
_NAO_TEMPLATIZA = {"instance_id", "run_id", "account_label"}


def para_hash(step: PlanStep, variables: dict[str, str] | None) -> PlanStep:
    """A etapa com os VALORES dos parâmetros trocados pelos nomes (`@nasa` → `{perfil}`), só para a identidade.

    Medido em 23/09/2026: o planejador às vezes escreve o valor literal na pós-condição ("perfil de @nasa aberto")
    em vez de `{perfil}`. A receita era gravada com o hash desse literal e nunca casava com o mesmo caminho para
    outro alvo — 15 receitas ativas do Instagram e cobertura zero em todos os fluxos. O fluxo-modelo já faz esta
    troca ao aprender (`flows._sub_values`); aqui ela passa a valer também na identidade da etapa, dos dois lados.
    """
    valores = {k: v for k, v in (variables or {}).items()
               if k not in _NAO_TEMPLATIZA and isinstance(v, str) and len(v) >= 3 and "{" not in v}
    if not valores:
        return step

    def troca(texto: str | None) -> str | None:
        if not texto:
            return texto
        for nome, valor in sorted(valores.items(), key=lambda kv: -len(kv[1])):
            texto = texto.replace(valor, "{" + nome + "}")
        return texto

    return step.model_copy(update={
        "postcondition": step.postcondition.model_copy(update={"value": troca(step.postcondition.value) or ""}),
        "commit_guard": [troca(g) or "" for g in step.commit_guard]})


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
def _sem_arroba(valor: str) -> str | None:
    return valor[1:] if valor.startswith("@") and len(valor) > 1 else None


def _formas(valor: str) -> set[str]:
    """Grafias aceitas de um texto de seletor: `@bia` também casa "bia" — a mesma regra das provas locais
    (`proofs.variantes_de_arroba`). O Instagram quase nunca mostra a arroba que o parâmetro traz."""
    puro = _sem_arroba(valor)
    return {norm_text(valor)} | ({norm_text(puro)} if puro else set())


def _match(tree: UiTree, rid: str | None, text: str | None, desc: str | None) -> list[UiElement]:
    out = []
    textos, descs = (_formas(text) if text is not None else None), (_formas(desc) if desc is not None else None)
    for e in tree.elements:
        if rid is not None and e.resource_id != rid:
            continue
        if textos is not None and norm_text(e.text) not in textos:
            continue
        if descs is not None and norm_text(e.desc) not in descs:
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
    # A tela mostra "ana" para o parâmetro "@ana" (linha da caixa de mensagens, cabeçalho da conversa). Sem isto o
    # seletor gravava "ana" LITERAL e, reproduzido para "@bia", tocava a conversa de outra pessoa — a verificação
    # recusava, mas a tentativa se perdia e a receita ia para a quarentena (achado da fase G, 27/09). Só o texto
    # INTEIRO igual ao valor sem arroba vira parâmetro: um pedaço ("Mariana" para "@ana") nunca.
    for name, value in sorted(variables.items(), key=lambda kv: -len(kv[1] or "")):
        puro = _sem_arroba(value or "")
        if puro and len(puro) >= 3 and norm_text(text) == norm_text(puro):
            return "{" + name + "}"
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
def distill(action_rows: list[Row], variables: dict[str, str]) -> tuple[list[dict[str, Any]] | None, str]:
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
    if not out:
        # A IA declarou a etapa pronta sem agir porque o aparelho JÁ estava no estado final (sobra da execução
        # anterior). Isso não é um caminho para repetir: reproduzir "nada" e conferir a pós-condição derruba a
        # etapa em qualquer tela que não seja aquela (execução eda77f, receita 46).
        return None, "nenhuma ação executada: o aparelho já estava no estado final — não há caminho a repetir"
    if len(out) > MAX_ACTIONS:
        return None, "ações demais para uma receita confiável"
    return out, "ok"


def distill_training(inputs: list[dict[str, Any]], variables: dict[str, str], *, side_effect: bool,
                     app_packages: dict[str, str] | None = None) -> tuple[list[dict[str, Any]] | None, str]:
    """Entradas gravadas pela PESSOA (modo treinamento, item 13.2) numa etapa → receita, com as mesmas regras de
    `distill`: alvo com seletor estável e único, texto 100 % coberto por parâmetros, nada de voltar/início (depende
    do estado de quem ensinou), efeito externo nunca só por texto. O que não passa não vira receita — a etapa
    continua no fluxo e a IA a conduz na hora, que é a degradação que o sistema já tem.

    `side_effect`: o último toque da etapa é o commit (o que dispara o efeito)."""
    secret_values = {v for k, v in variables.items() if v and SENSITIVE_PARAM.search(k)}
    toques = [i for i, e in enumerate(inputs) if e["type"] in ("tap", "long_press")]
    ultimo_toque = toques[-1] if toques else None
    out: list[dict[str, Any]] = []
    pending_scrolls: list[str] = []
    for i, e in enumerate(inputs):
        tipo = e["type"]
        if tipo == "key":
            return None, f"tecla {e.get('key_name')} depende do estado de quem ensinou"
        if tipo == "swipe":
            dy = (e.get("y2") or 0) - (e.get("y") or 0)
            pending_scrolls.append("down" if dy < 0 else "up")          # dedo sobe = conteúdo rola para baixo
            continue
        commit = bool(side_effect and i == ultimo_toque)
        item: dict[str, Any] = {"tool": tipo, "commit": commit, "why": "ensinado no modo treinamento"}
        if tipo == "open_app":
            pacote = (app_packages or {}).get(e.get("app_id") or "")
            if not pacote:
                return None, "abrir app sem pacote conhecido"
            item["args"] = {"package": pacote}
        elif tipo == "text":
            texto = e.get("text")
            if texto is None:
                return None, "texto sigiloso não vira receita"
            if any(v and v in texto for v in secret_values):
                return None, "texto digitado contém parâmetro sensível"
            templ, _, covered = detemplate(texto, variables)
            if not covered:
                return None, "texto digitado não é 100 % coberto por parâmetros"
            item["tool"] = "type_text"
            item["args"] = {"text": templ, "clear_first": True, "press_enter": False}
            item["selectors"] = []                                     # digita no campo que estiver em foco
        else:
            alvo = e.get("target")
            if not alvo:
                return None, f"{tipo} sem elemento identificado (coordenada solta)"
            sels = build_selectors(alvo, variables)
            if not sels:
                return None, f"{tipo}: o alvo não tinha seletor estável e único"
            if commit and all(x["kind"] == "text" for x in sels):
                return None, "ação de efeito externo só com seletor por texto — fraco demais"
            item["args"] = {"duration_ms": 800} if tipo == "long_press" else {}
            item["selectors"] = sels
        if pending_scrolls:
            item["scroll"] = {"direction": pending_scrolls[-1], "max": len(pending_scrolls) + 3}
            pending_scrolls = []
        out.append(item)
        if commit:
            break
    if pending_scrolls:
        return None, "rolagem sem ação-alvo depois dela"
    if not out:
        return None, "nenhuma ação a repetir nesta etapa"
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
def _caminho(acoes: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    """O que a reprodução faz, sem o `why` — a justificativa da IA muda a cada execução e não muda o caminho."""
    return [{k: v for k, v in a.items() if k != "why"} for a in acoes]


class RecipeStore:
    def __init__(self, db: Database):
        self.db = db

    def find(self, package: str | None, app_version: str | None, step_hash: str | None, *,
             signature: str = "", variant: str = "") -> Row | None:
        """Identidade da receita: pacote + versão + ASSINATURA + VARIANTE de interface + etapa.

        Assinatura entra porque dois APKs podem dizer a mesma versão e não serem o mesmo app; variante entra porque
        idioma e densidade mudam a tela. Receita aprendida numa combinação não vale para outra.

        Devolve a ativa; sem ela, a candidata em prova (o executor a põe em sombra, nunca a reproduz).

        É a CONSULTA do funil (`receita.consulta`): o executor só chega aqui com uma etapa elegível (modo ligado, app
        conhecido, efeito ainda não disparado), então a soma dos resultados é o total de TENTATIVAS elegíveis (o
        funil conta por tentativa — revisão F8: é a única unidade em que consulta, reprodução e retorno fecham). Chave
        incompleta não é "ausente": a etapa nem era elegível, e não entra na conta.
        """
        if not (package and app_version and step_hash):
            return None
        row = self._ativa(package, app_version, step_hash, signature=signature, variant=variant)
        if row is not None:
            resultado = "encontrada"
        elif (row := self._candidata(package, app_version, step_hash, signature=signature,
                                     variant=variant)) is not None:
            # Candidata não reproduz (a IA decide e ela só é comparada): contá-la como "encontrada" quebraria a
            # promessa do funil de que toda encontrada termina num veredito de `receita.reproducao`.
            resultado = "candidata"
        else:
            # Uma consulta a mais, só no erro: distingue "nunca aprendida" de "aprendida e posta de lado". As duas
            # mandam a etapa para a IA, mas pedem coisas diferentes de quem lê (aprender × investigar a tela).
            quarentena = self.db.one("SELECT 1 FROM recipes WHERE app_package=? AND app_version=? AND app_signature=?"
                                     " AND variant=? AND step_hash=? AND status='quarantined' LIMIT 1",
                                     (package, app_version, signature, variant, step_hash))
            resultado = "quarentena" if quarentena is not None else "ausente"
        metricas.contar("receita.consulta", resultado=resultado)
        return row

    def _ativa(self, package: str, app_version: str, step_hash: str, *, signature: str, variant: str) -> Row | None:
        """A receita ativa da chave, sem medir nada — `save` também pergunta isto, e não é consulta de etapa."""
        return self._com_status("active", package, app_version, step_hash, signature=signature, variant=variant)

    def _candidata(self, package: str, app_version: str, step_hash: str, *, signature: str,
                   variant: str) -> Row | None:
        return self._com_status("candidate", package, app_version, step_hash, signature=signature, variant=variant)

    def _com_status(self, status: str, package: str, app_version: str, step_hash: str, *, signature: str,
                    variant: str) -> Row | None:
        return self.db.one("SELECT * FROM recipes WHERE app_package=? AND app_version=? AND app_signature=?"
                           " AND variant=? AND step_hash=? AND status=? ORDER BY version DESC LIMIT 1",
                           (package, app_version, signature, variant, step_hash, status))

    def save(self, *, package: str, app_version: str, step_hash: str, step_key: str, actions: list[dict[str, Any]],
             learned_from: str, signature: str = "", variant: str = "", candidate: bool = False,
             replaces: int | None = None) -> int | None:
        """Grava uma versão nova SÓ se não houver receita ativa (a ativa só sai por quarentena).

        `candidate`: a receita nasce em prova (`recipes_promote_after`, o caminho que a IA aprendeu); sem ele nasce
        ativa — o modo treinamento (a pessoa demonstrou) e o `recipes_promote_after: 0`.
        Candidata em prova só é trocada por quem a viu divergir (`replaces` = o id dela), e só por um caminho que
        reproduzido faria outra coisa: aparelhos aprendendo em paralelo, sem nunca terem comparado com ela, não
        reescrevem a prova a cada execução, e uma cópia dela não vira versão nova. A versão gravada marca
        as anteriores ainda vivas da chave (candidata trocada, ativa que caiu em quarentena) como `superseded` — uma
        só receita viva por chave, e a substituída não volta pelo painel.
        """
        chave = (package, app_version, signature, variant, step_hash)
        with self.db.tx():
            if self._ativa(package, app_version, step_hash, signature=signature, variant=variant) is not None:
                return None
            em_prova = self._candidata(package, app_version, step_hash, signature=signature, variant=variant)
            if candidate and em_prova is not None and em_prova["id"] != replaces:
                return None
            if (candidate and em_prova is not None
                    and _caminho(loads(em_prova["actions"], [])) == _caminho(actions)):
                # Divergência sem diferença no que a receita grava (ex.: a IA rolou para baixo e voltou; a receita só
                # guarda a última direção): a cópia não mudaria a reprodução, só deixaria uma `superseded` por
                # execução. Fica a candidata, com a prova já recomeçada pela divergência.
                return None
            ver = int(self.db.scalar(
                "SELECT COALESCE(MAX(version),0)+1 FROM recipes WHERE app_package=? AND app_version=? AND"
                " app_signature=? AND variant=? AND step_hash=?", chave))
            self.db.execute("UPDATE recipes SET status='superseded' WHERE app_package=? AND app_version=? AND"
                            " app_signature=? AND variant=? AND step_hash=?"
                            " AND status IN ('candidate','quarantined')", chave)
            return int(self.db.inserted_id(
                "INSERT INTO recipes(app_package, app_version, app_signature, variant, step_hash, step_key, version,"
                " status, actions, learned_from_step, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (package, app_version, signature, variant, step_hash, step_key, ver,
                 "candidate" if candidate else "active", dumps(actions), learned_from, now_iso())) or 0)

    def result(self, recipe_id: int, ok: bool) -> bool:
        """Conta o uso. Devolve True se a receita entrou em quarentena agora.

        `receita.reproducao`: `ok` = a etapa terminou comprovada só com a receita; `divergiu` = a receita não levou a
        etapa até o fim (divergiu e a IA assumiu, ou a etapa falhou depois dela). A QUARENTENA só olha o veredito da
        etapa: quando a tentativa termina em nova tentativa (`retry`) o executor não chama isto — de propósito, para
        um aparelho com problema próprio não pôr a receita em quarentena sozinho — e conta a reprodução daquela
        tentativa ele mesmo (`divergiu`), para o funil fechar por tentativa (revisão F8).
        """
        metricas.contar("receita.reproducao", resultado="ok" if ok else "divergiu")
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

    def shadow(self, recipe_id: int, agreed: bool, *, promote_after: int) -> bool:
        """Veredito da sombra de UMA execução da etapa. Devolve True se a candidata foi promovida a ativa agora.

        A unidade é a execução, não a decisão: duas decisões concordantes numa mesma etapa não são repetição. Na
        candidata, a divergência zera os DOIS contadores (a prova recomeça), de modo que `shadow_agree` é a
        sequência de concordâncias e `shadow_agree/shadow_total` continua uma taxa; na ativa (modo `shadow` global)
        a taxa só acumula. A promoção confere, na mesma transação, que ela ainda é a candidata e que a chave não
        ganhou uma ativa enquanto isso (outro aparelho, ou a pessoa pelo painel) — nunca duas ativas por chave.
        """
        with self.db.tx():
            row = self.db.one("SELECT * FROM recipes WHERE id=?", (recipe_id,))
            if row is None:
                return False
            if agreed:
                self.db.execute("UPDATE recipes SET shadow_total=shadow_total+1, shadow_agree=shadow_agree+1"
                                " WHERE id=?", (recipe_id,))
            elif row["status"] == "candidate":
                self.db.execute("UPDATE recipes SET shadow_total=0, shadow_agree=0 WHERE id=?", (recipe_id,))
            else:
                self.db.execute("UPDATE recipes SET shadow_total=shadow_total+1 WHERE id=?", (recipe_id,))
            seguidas = int(self.db.scalar("SELECT shadow_agree FROM recipes WHERE id=?", (recipe_id,)) or 0)
            if not (agreed and row["status"] == "candidate" and seguidas >= max(1, promote_after)):
                return False
            if self._ativa(row["app_package"], row["app_version"], row["step_hash"], signature=row["app_signature"],
                           variant=row["variant"]) is not None:
                return False
            self.db.execute("UPDATE recipes SET status='active' WHERE id=?", (recipe_id,))
            return True

    def replayer(self, row: Row, variables: dict[str, str]) -> Replayer:
        return Replayer(recipe_id=row["id"], version=row["version"], actions=loads(row["actions"], []), variables=variables)
