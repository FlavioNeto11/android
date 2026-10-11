"""Receitas: a IA descobre como cumprir uma etapa UMA vez; depois a etapa é repetida por seletores, sem modelo.

Princípios:
- a receita é só uma FONTE DE DECISÃO. Tudo o que protege a execução (validação da ferramenta, guardas de commit,
  registro da intenção antes de agir, detecção de ciclo, verificação da pós-condição) continua no executor;
- um seletor só vale se casar EXATAMENTE UM elemento habilitado na tela atual; qualquer dúvida é divergência, e a
  IA assume aquela etapa a partir da tela em que o aparelho está;
- nada sensível é aprendido: campo de senha nunca vira alvo e texto digitado precisa ser 100 % coberto por
  parâmetros da execução (o que sobra seria dado pessoal ou invenção do modelo);
- a chave da receita é a etapa em forma de TEMPLATE (antes de resolver {instance_id}, {recipient}…) + app + versão
  do app: atualização do app é só uma busca sem resultado, e a etapa é reaprendida;
- D1 (ADR-054): a candidata que concordou com a IA só passa a AGIR sozinha se não tiver ação de efeito externo
  (`commit`). Com efeito, ela para em `validated` — inerte como a candidata — e quem a publica é o dono, pelo livro de
  aprendizado. As ativas com efeito de antes do D1 não mudam (aparecem em "Revisar").
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..automation.hierarchy import UiElement, UiTree
from ..db import Database, Row, dumps, loads
from ..metricas import metricas
from ..models import PlanStep
from ..modules.learning.domain.aprovacao_automatica import PLATAFORMA
from ..modules.learning.domain.causa_do_ausente import ChaveDaReceita, ReceitaVizinha, causa_do_ausente, doadora
from ..modules.learning.domain.livro import CONFIRMADO_QUE_FICA, receita_tem_efeito
from ..planning.provider import Decision
from .flows import trocar_valores_por_nomes
from ..util import norm_text, now_iso
from .flows import PREFIXO_DO_TREINO, SISTEMA

SENSITIVE_PARAM = re.compile(r"pass|senha|pin\b|otp|token|secret|segredo|c[oó]digo|code", re.IGNORECASE)
READ_ONLY = {"observe_screen", "find_element", "find_row", "wait_for", "verify_state"}
TARGETED = {"tap", "long_press", "type_text", "collect_list"}                  # precisam de um elemento-alvo para serem repetíveis
UNSAFE_TO_REPLAY = {"press_back", "press_home", "drag"}        # dependem do estado/coords de quem aprendeu
# ADR-025: a credencial é DA execução (apagada quando ela termina) e o endereço é do comando — uma receita que os
# repetisse noutra execução digitaria/abriria o que ninguém forneceu para ela.
UNSAFE_TO_REPLAY |= {"type_secret", "open_url"}
SELECTOR_RANK = ("rid+text", "rid+desc", "rid", "desc", "text")
QUARANTINE_AFTER = 3
#: 30.80: a receita que "não se aplicou" (tela de partida diferente na ação 1, etapa comprovada pela IA) não conta como
#: falha; a N-ésima SEGUIDA conta, para um 1º seletor quebrado (atualização do app) não ficar isento para sempre.
NAO_APLICAVEL_CONTA_APOS = 3
MAX_ACTIONS = 8
TEMPLATE_RE = re.compile(r"\{([a-z_][a-z0-9_]*)\}")

log = logging.getLogger("poc.receitas")


class RecipeDiverged(Exception):
    pass


class AlvoAusente(RecipeDiverged):
    """30.80: o alvo da ação não está nesta tela. Na AÇÃO 1, antes de a receita agir, é a tela de partida que é outra
    (a r-20261005133833-122345 partiu de dentro de uma conversa), não a receita que errou: o executor decide se conta."""


# ------------------------------------------------------------------ funil medido (adendo v0.20, C5)
#: Texto da divergência → motivo curto do retorno à IA. O texto é livre (vem de `RecipeDiverged` e das rejeições do
#: executor) e não pode virar rótulo de métrica: rótulo é conjunto pequeno e fixo. A ordem importa: os prefixos que o
#: executor escreve (ação inválida, efeito externo) vêm antes, porque o complemento deles é livre e poderia conter
#: outro trecho da lista.
_MOTIVOS_DO_RETORNO: tuple[tuple[str, str], ...] = (
    ("ação da receita inválida", "acao_invalida"),
    ("alvo do efeito externo", "alvo_do_efeito"),
    ("guarda do efeito externo", "guarda_do_efeito"),
    ("tela mudou antes do toque", "tela_mudou"),          # 29.90: a releitura antes do toque de efeito
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


def _mesmo_valor(v: str) -> str:
    """Como o 31.87 compara o exemplo com o dado da persona, mais o @ da frente: "@Fulano" no comando e "fulano" na
    conta são o mesmo perfil."""
    return v.strip().lstrip("@").casefold()


def _empates_com_a_persona(variables: Mapping[str, str], persona: Sequence[str]) -> dict[str, str]:
    """31.165: `{param}` → `{marcador}` quando o parâmetro do objetivo tem o MESMO valor de um dado da persona.

    Medido na onda 1 de 06/10: o ensino (31.87) grava "perfil de {conta_instagram_usuario} aberto" quando o alvo é a
    própria persona, e a execução planejada para o mesmo alvo dava "perfil de {perfil} aberto" (o parâmetro "@x" é mais
    longo que a conta "x" e ganhava a troca). Eram a mesma etapa com dois nomes, e a receita 221 nunca casava. No
    empate, vale o marcador da persona, como na proposta. Dois marcadores com o mesmo valor: o primeiro em ordem
    alfabética, para a identidade não depender da ordem do dicionário."""
    por_valor: dict[str, str] = {}
    for nome in sorted(persona):
        v = variables.get(nome)
        if isinstance(v, str) and len(_mesmo_valor(v)) >= 3 and "{" not in v:
            por_valor.setdefault(_mesmo_valor(v), nome)
    trocas: dict[str, str] = {}
    for nome, v in variables.items():
        if nome in persona or nome in _NAO_TEMPLATIZA or not isinstance(v, str):
            continue
        marcador = por_valor.get(_mesmo_valor(v))
        if marcador is not None:
            trocas["{" + nome + "}"] = "{" + marcador + "}"
    return trocas


def para_hash(step: PlanStep, variables: dict[str, str] | None, *, persona: Sequence[str] = ()) -> PlanStep:
    """A etapa com os VALORES dos parâmetros trocados pelos nomes (`@nasa` → `{perfil}`), só para a identidade.

    Medido em 23/09/2026: o planejador às vezes escreve o valor literal na pós-condição ("perfil de @nasa aberto")
    em vez de `{perfil}`. A receita era gravada com o hash desse literal e nunca casava com o mesmo caminho para
    outro alvo — 15 receitas ativas do Instagram e cobertura zero em todos os fluxos. O fluxo-modelo já faz esta
    troca ao aprender (`flows.trocar_valores_por_nomes`); aqui ela passa a valer também na identidade da etapa, dos dois lados.

    `persona` (31.165): os nomes, entre as `variables`, que são dados da persona. No empate de valor com um parâmetro
    do objetivo, o nome que fica é o marcador da persona (`_empates_com_a_persona`). Sem `persona`, nada muda.
    """
    valores = {k: v for k, v in (variables or {}).items()
               if k not in _NAO_TEMPLATIZA and isinstance(v, str) and len(v) >= 3 and "{" not in v}
    empates = _empates_com_a_persona(variables or {}, persona) if persona else {}
    if not valores:
        return step

    def troca(texto: str | None) -> str | None:
        if not texto:
            return texto
        # A mesma troca, com a mesma borda, de quando o fluxo aprende (31.96): `str.replace` partia "nasal" por "nasa"
        # e dava ao hash da receita uma identidade que o fluxo-modelo não tem.
        saida = trocar_valores_por_nomes(texto, valores) or texto
        for nome, marcador in empates.items():      # também o `{perfil}` que o planejador já escreveu como marcador
            saida = saida.replace(nome, marcador)
        return saida

    return step.model_copy(update={
        "postcondition": step.postcondition.model_copy(update={"value": troca(step.postcondition.value) or ""}),
        "commit_guard": [troca(g) or "" for g in step.commit_guard]})


def _identidade(chave: str, side_effect: bool, kind: str, value: str, nivel: str | None, guard: Sequence[str]) -> str:
    raw = json.dumps([chave, side_effect, kind, value, nivel, sorted(guard)], ensure_ascii=False)
    return hashlib.sha1(raw.encode()).hexdigest()[:20]


def step_template_hash(step: PlanStep) -> str:
    """Identidade da etapa em forma de template. Título e objetivo ficam de fora (o planejador os reescreve)."""
    post = step.postcondition
    # cópias de um bloco for_each (open_conversation_i1, _i2…) compartilham a identidade da etapa-modelo
    return _identidade(getattr(step, "template_key", None) or step.key, step.side_effect, post.kind, post.value,
                       post.required_delivery_level.value if post.required_delivery_level else None, step.commit_guard)


# ------------------------------------------------------------------ chave genérica (RA-20 fatia B)
def hash_generico(chave: str | None, side_effect: bool | None, kind: str | None, nivel: str | None,
                  commit_guard: Sequence[str] | None) -> str | None:
    """A identidade da etapa SEM a pós-condição escrita, ou None quando a etapa não tem chave genérica.

    Medido em 03/10/2026 (RA-20): o planejador reescreve a pós-condição julgada pelo modelo a cada plano ("conversa
    com @x aberta", "perfil de @x aberto") e cada redação vira uma chave; 15 receitas ativas moravam em 5 caminhos
    iguais. Sem o texto, a mesma etapa casa de novo. Só a etapa `model_judged`, sem efeito e sem `commit_guard`: o
    texto das outras é o que a pós-condição confere (texto visível, elemento, app em frente) e não é só redação.
    Conservadora: um campo ausente (`None`) devolve None, e a receita fica na chave específica. Uma função só para o
    save, a consulta do executor, os consumidores e o backfill: a mesma etapa dá o mesmo hash pelos dois caminhos
    (`hash_generico_da_etapa` e `hash_generico_da_linha`). Com a pós-condição vazia, é igual ao específico.
    """
    if not chave or side_effect is None or side_effect or kind != "model_judged" or commit_guard is None or commit_guard:
        return None
    return _identidade(chave, False, kind, "", nivel, ())


def hash_generico_da_etapa(step: PlanStep) -> str | None:
    """`hash_generico` da etapa do plano (a chave é a da etapa-modelo, como em `step_template_hash`)."""
    post = step.postcondition
    return hash_generico(getattr(step, "template_key", None) or step.key, step.side_effect, post.kind,
                         post.required_delivery_level.value if post.required_delivery_level else None,
                         step.commit_guard)


def hash_generico_da_linha(row: Row) -> str | None:
    """`hash_generico` da linha de `steps` (`template_key`, `key`, `side_effect`, `postcondition`, `commit_guard`).

    O executor chega aqui pela linha, nunca pelo `StepDTO`: o DTO não traz o `template_key` da cópia do for_each."""
    try:
        post = loads(row["postcondition"], None)
        guard = loads(row["commit_guard"], None) if row["commit_guard"] is not None else []
        side = row["side_effect"]
        chave = row["template_key"] or row["key"]
    except (KeyError, IndexError, TypeError):
        return None
    if not isinstance(post, dict) or not isinstance(guard, list) or side is None:
        return None
    return hash_generico(chave, bool(side), post.get("kind"), post.get("required_delivery_level"), guard)


def _normal(texto: str) -> str:
    """Caixa, acento, arroba e espaços fora: "@NASA" e "nasa" são o mesmo literal."""
    sem_acento = "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))
    return " ".join(sem_acento.casefold().replace("@", " ").split())


def _contem(texto: str, trecho: str) -> bool:
    return bool(trecho) and re.search(r"(?<!\w)" + re.escape(trecho) + r"(?!\w)", texto) is not None


def _literais(actions: Sequence[Mapping[str, object]]) -> list[str]:
    """Os textos que a reprodução procura ou digita tal como foram gravados: `text`/`desc` dos seletores e os argumentos
    de texto. O que tem `{nome}` é o valor da vez e não conta; `why` e `package` não identificam o alvo."""
    out: list[str] = []
    for a in actions:
        seletores, args = a.get("selectors"), a.get("args")
        for s in seletores if isinstance(seletores, list) else []:
            out += [str(s[k]) for k in ("text", "desc") if isinstance(s, Mapping) and s.get(k)]
        for k, v in args.items() if isinstance(args, Mapping) else ():
            if k != "package":
                out += [x for x in (v if isinstance(v, list) else [v]) if isinstance(x, str) and x]
    return [t for t in out if "{" not in t]


def eh_generica(actions: Sequence[Mapping[str, object]], post_value: str | None,
                variables: Mapping[str, str] | None, titulo: str | None = None) -> bool:
    """A receita serve a QUALQUER valor da etapa? Não, se o que ela procura ou digita traz o literal do valor dela.

    O caso medido: `open_profile` grava o toque em `text: 'nasa'` (o Instagram mostra o @ sem arroba). Na chave
    genérica, "perfil de @bia" tocaria em "nasa". Específica quando um literal da receita aparece, como palavra, na
    pós-condição escrita (normalizada) ou quando o valor de um parâmetro da execução aparece num literal. Na dúvida,
    específica: errar para cá só adia o ganho. "Message", "Options" e "Send message" (`open_thread`) são genéricos.

    `titulo`: o título RESOLVIDO da etapa entra junto da pós-condição (revisão da Android no #145). O planejador às vezes
    escreve o alvo só no título ("Abrir a conversa com Fulano"), sem parâmetro; sem isso, o toque em "Fulano" passaria
    como genérico e só a sombra com outro valor o pegaria.
    """
    post = _normal(f"{post_value or ''} {titulo or ''}")
    valores = [_normal(v) for k, v in (variables or {}).items()
               if k not in _NAO_TEMPLATIZA and isinstance(v, str) and len(_normal(v)) >= 3 and "{" not in v]
    for literal in map(_normal, _literais(actions)):
        if len(literal) >= 3 and _contem(post, literal):
            return False
        if any(_contem(literal, v) for v in valores):
            return False
    return True


# ------------------------------------------------------------------ des-templatização
def detemplate(text: str, variables: dict[str, str], *,
               marcadores: Collection[str] = ()) -> tuple[str, bool, bool]:
    """Troca valores conhecidos por {nome} (o mais longo primeiro), com a MESMA borda do fluxo-modelo e do hash da
    receita (31.109, `flows._sub_values`): o valor só vale inteiro, então "nasal" não vira `{perfil}l` e "v10" não vira
    `v{n}`. Antes era `str.replace` sem borda, e a ação aprendida divergia da identidade da etapa.
    Devolve (texto, usou_alguma_variável, ficou_100%_coberto_por_variáveis).

    31.244 (`marcadores`, os nomes das variáveis da persona que a REPRODUÇÃO resolve): o registro grava o dado da
    persona já como marcador (31.113 F1: `{perfil_nome}`; 31.243: `@{conta_<app>_usuario}`). Esse marcador conta como
    variável usada, e a arroba logo antes dele não sobra como literal. Um marcador gravado que nem a etapa nem a persona
    resolvem não cobre (a reprodução falharia). Sem `marcadores`, tudo como antes."""
    valores = {n: v for n, v in variables.items() if v and len(v) >= 3}
    out = trocar_valores_por_nomes(text, valores) or text
    used = out != text
    resto = out
    gravados = TEMPLATE_RE.findall(text)
    if marcadores and gravados:
        if not all(m in variables or m in marcadores for m in gravados):
            return out, used, False
        da_persona = [m for m in gravados if m in marcadores]
        used = used or bool(da_persona)
        if da_persona:
            resto = re.sub(r"@(?=\{(?:" + "|".join(map(re.escape, da_persona)) + r")\})", "", out)
    covered = used and not TEMPLATE_RE.sub("", resto).strip(" \t\r\n.,;:!?-—()[]\"'“”")
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


#: Quantos filhos rotulados a gravação guarda do alvo sem identidade (29.40 item 2): a destilação usa o primeiro cujo
#: texto vire seletor (`_usable_text` depende das variáveis, que só a destilação conhece).
FILHOS_NO_ALVO = 3


#: O rótulo que muda com o estado ou com o tempo não identifica o filho (revisão da Android, 03/10): tempo relativo ou
#: absoluto ("2h", "5m ago", "10:42", "Yesterday") e as palavras de estado do Instagram e do Outlook ("Following",
#: "Seen", "Active now", "Unread"). A candidata gravada com "Active now" só falharia no dia seguinte.
_ROTULO_DE_ESTADO = re.compile(
    r"\b(?:\d+\s?[smhdw]|ago|yesterday|ontem|hoje|today|am|pm|now|agora|seen|visto|sent|enviad[ao]|delivered|unread"
    r"|active|ativ[ao]|follow|following|seguir|seguindo|requested|solicitado|message|mensagem|like|unlike|curtir"
    r"|online|offline|typing|digitando|new|nov[ao])\b"                     # o selo de não lido (revisão da Android)
    r"|\d{1,2}:\d{2}", re.IGNORECASE)


def _dentro_de(fora: tuple[int, int, int, int], dentro: tuple[int, int, int, int]) -> bool:
    return fora[0] <= dentro[0] and fora[1] <= dentro[1] and dentro[2] <= fora[2] and dentro[3] <= fora[3]


def clicavel_no_ponto(tree: UiTree, x: int, y: int) -> UiElement | None:
    """O menor elemento clicável e habilitado que contém o ponto: quem recebe o toque pelo despacho do Android."""
    hit = [e for e in tree.elements if e.clickable and e.enabled and _dentro_de(e.bounds, (x, y, x, y))]
    return min(hit, key=lambda e: (e.bounds[2] - e.bounds[0]) * (e.bounds[3] - e.bounds[1])) if hit else None


def mesmo_alvo(tree: UiTree, a: str | None, b: str | None) -> bool:
    """31.327: dois `element_id` desta tela são o MESMO alvo do toque? Iguais, ou os dois toques caem no mesmo clicável.

    A receita grava o rótulo (o texto "Junk", um filho sem clique) e a reprodução toca o centro dele; a IA escolhe a linha
    clicável que o contém. São ids diferentes para a mesma ação (prova real do P-043, 10/10: "a IA escolheu outra ação"
    com a IA tocando o "Junk" certo). Quem recebe o toque é o menor clicável no ponto (`clicavel_no_ponto`), e é ele que
    se compara; sem clicável em algum dos dois pontos, só a igualdade vale."""
    if a == b:
        return True
    if a is None or b is None:
        return False
    ea, eb = tree.by_id(a), tree.by_id(b)
    if ea is None or eb is None:
        return False
    ra, rb = clicavel_no_ponto(tree, *ea.center), clicavel_no_ponto(tree, *eb.center)
    return ra is not None and ra is rb


def toque_no_filho_cai_no_conteiner(tree: UiTree, filho: UiElement, conteiner: str | None) -> bool:
    """A trava de hit-test (revisão da Android): o menor clicável no centro do filho contém o filho, não é ele e tem a
    classe do contêiner gravado. Um botão "Enviar" na linha, um ícone ou uma camada por cima fazem divergir ANTES do
    toque."""
    alvo = clicavel_no_ponto(tree, *filho.center)
    return (alvo is not None and alvo is not filho and _dentro_de(alvo.bounds, filho.bounds)
            and (not conteiner or alvo.class_name == conteiner))


def filhos_rotulados(tree: UiTree, el: UiElement) -> list[dict[str, object]]:
    """O alvo sem combinação única (o contêiner sem id, como o `LinearLayout` da linha da lista): os elementos DENTRO
    dele que se identificam sozinhos nesta tela, do maior para o menor. Só os NÃO clicáveis cujo toque no centro cai
    no próprio alvo (hit-test): o despacho do Android sobe o toque ao contêiner; um filho clicável, ou outro clicável
    por cima, faria outra coisa. Os candidatos vêm da janela da ordem do dump (o uiautomator despeja em pré-ordem: os
    descendentes são os nós logo depois do alvo, até o primeiro fora dos bounds), o que deixa de fora a sobreposição
    de outra camada (FAB, diálogo, snackbar, a linha de baixo durante a rolagem)."""
    try:
        i = next(k for k, e in enumerate(tree.elements) if e is el)
    except StopIteration:
        return []
    janela: list[UiElement] = []
    for e in tree.elements[i + 1:]:
        if not _dentro_de(el.bounds, e.bounds):
            break
        janela.append(e)
    dentro = [e for e in janela if e.enabled and not e.clickable and not e.password
              and clicavel_no_ponto(tree, *e.center) is el]
    dentro.sort(key=lambda e: (-(e.bounds[2] - e.bounds[0]) * (e.bounds[3] - e.bounds[1]), e.bounds[1], e.bounds[0]))
    saida: list[dict[str, object]] = []
    for e in dentro:
        unicos = unique_selectors(tree, e)
        if unicos:
            saida.append({"resource_id": e.resource_id, "text": e.text, "desc": e.desc, "class_name": e.class_name,
                          "unique": unicos, "conteiner": el.class_name})
            if len(saida) == FILHOS_NO_ALVO:
                break
    return saida


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
    # 31.109: o valor DENTRO de palavra maior ("@ana_silva" com "@ana", "Mariana Silva" com "Maria") não é trocado
    # (a borda), mas o texto é de outra pessoa: sem este corte ele cairia no rótulo fixo abaixo e viraria seletor
    # literal, que o replay para outro alvo poderia tocar. Como era antes da borda: descartado.
    if any(v and len(v) >= 3 and v in text for v in variables.values()):
        return None
    # A tela mostra "ana" para o parâmetro "@ana" (linha da caixa de mensagens, cabeçalho da conversa). Sem isto o
    # seletor gravava "ana" LITERAL e, reproduzido para "@bia", tocava a conversa de outra pessoa — a verificação
    # recusava, mas a tentativa se perdia e a receita ia para a quarentena (achado da fase G, 27/09). Só o texto
    # INTEIRO igual ao valor sem arroba vira parâmetro: um pedaço ("Fabiana" para "@ana") nunca.
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
    if not sels:
        # 29.40 item 2: o contêiner sem identidade é alcançado pelo filho rotulado gravado com ele; a reprodução toca o
        # centro do filho, que fica dentro do contêiner, e a pós-condição confere como sempre.
        for filho in target.get("filhos") or []:
            sels = [{**s, "via": "filho", "conteiner": str(filho.get("conteiner") or "")}
                    for s in build_selectors(filho, variables) if _rotulo_estavel(s)]
            if sels:
                break
    return sels


def _rotulo_estavel(sel: dict[str, str]) -> bool:
    """O literal do seletor do filho (sem os `{parâmetros}`) não muda com o estado nem traz um @ de pessoa: o nome de
    pessoa só vale templatizado. Qualquer @ literal recusa ("por @fulano", "Foto de @fulano"; revisão da Android)."""
    for chave in ("text", "desc"):
        literal = re.sub(r"\{[^}]*\}", "", sel.get(chave) or "").strip()
        if literal and ("@" in literal or _ROTULO_DE_ESTADO.search(literal)):
            return False
    return True


def resolve_selectors(tree: UiTree, selectors: list[dict[str, str]], variables: dict[str, str]) -> UiElement | None:
    """Primeiro seletor (na ordem de confiança) que casa exatamente um elemento HABILITADO."""
    for sel in selectors:
        text = retemplate(sel["text"], variables) if "text" in sel else None
        desc = retemplate(sel["desc"], variables) if "desc" in sel else None
        found = [e for e in _match(tree, sel.get("rid"), text, desc) if e.enabled]
        if len(found) == 1:
            return found[0]
    return None


def _alvo_ambiguo(tree: UiTree, selectors: list[dict[str, str]], variables: dict[str, str]) -> bool:
    """30.80: algum seletor casou MAIS de um elemento habilitado (o que `resolve_selectors` recusou por ambíguo)."""
    for sel in selectors:
        text = retemplate(sel["text"], variables) if "text" in sel else None
        desc = retemplate(sel["desc"], variables) if "desc" in sel else None
        if len([e for e in _match(tree, sel.get("rid"), text, desc) if e.enabled]) > 1:
            return True
    return False


# ------------------------------------------------------------------ destilação
#: 31.230: a marca da 1ª ação da receita cujo "voltar" inicial foi descartado: ela parte do estado conhecido do app
#: (`conhecimento/apps/<pacote>/telas.yaml`, `estado_conhecido`), conferido na tela antes de reproduzir.
ANCORA_ESTADO_CONHECIDO = "estado_conhecido"


def distill(action_rows: list[Row], variables: dict[str, str], *,
            em_casa_antes: Mapping[int, bool] | None = None,
            com_trecho_da_receita: bool = False,
            persona: Collection[str] = (),
            exploratoria: bool = False) -> tuple[list[dict[str, Any]] | None, str]:
    """Ações executadas pela IA numa tentativa limpa → receita. Devolve (ações | None, motivo).

    31.230: o `press_back` ANTES da 1ª ação gravada é a IA voltando a um lugar conhecido (na onda 1, o `open_profile`
    começou por voltar, e nenhuma receita nascia). Esse prefixo é descartado quando a 1ª ação gravada partiu do
    estado conhecido do app (`em_casa_antes[id da ação]`, a tela anotada pelo executor antes da decisão); ela leva a
    âncora, e a reprodução a confere antes de agir. Sem a anotação, sem estado conhecido declarado, ou com `voltar` no
    meio do caminho, a tentativa segue recusada como antes.

    31.233 (`com_trecho_da_receita`): a tentativa em que uma receita ATIVA rodou um trecho, divergiu e a IA completou. As
    ações da receita feitas (`done`) entram como as da IA: são o caminho que de fato levou à tela de onde a IA seguiu. A
    que não chegou ao aparelho (`rejected`) fica fora; qualquer outro estado recusa, como antes.

    31.244 (`persona`): os NOMES das variáveis da persona do objetivo. O `type_text` que o registro gravou com o marcador
    dela (`{perfil_nome}`, `@{conta_<app>_usuario}`) vira receita com o marcador; a reprodução o resolve pelas variáveis
    da persona (31.87 F2). Sem a variável na persona, segue recusado.

    31.327 (`exploratoria`): a etapa de exploração parte de uma tela que ninguém escolheu (onde o app estava), e a receita
    que só tem "tocar em Junk" respondia `nao_aplicavel` em qualquer outra (prova real do P-043). Em app com estado
    conhecido declarado (`em_casa_antes` não é `None`), a receita da exploração só nasce se o caminho PARTIU dele, com a
    âncora na 1ª ação (o executor leva o app ao estado conhecido antes de explorar, e a reprodução parte do mesmo
    ponto); partida desconhecida não vira receita, em vez de virar uma receita que só serve em uma tela."""
    secret_values = {v for k, v in variables.items() if v and SENSITIVE_PARAM.search(k)}
    out: list[dict[str, Any]] = []
    pending_scrolls: list[str] = []
    voltas = 0
    partida_ancorada = False
    for r in action_rows:
        tool, status = r["tool"], r["status"]
        if tool in READ_ONLY or tool in ("step_done", "step_blocked"):
            continue
        if com_trecho_da_receita and r["source"] == "recipe" and status == "rejected":
            continue                                # 31.233: o gesto da receita que não chegou ao aparelho
        de_quem = r["source"] == "ai" or (com_trecho_da_receita and r["source"] == "recipe")
        if status != "done" or not de_quem:
            return None, f"tentativa não foi limpa ({tool}: {status}/{r['source']})"
        if tool == "press_back" and not out and not pending_scrolls and em_casa_antes is not None:
            voltas += 1                             # 31.230: o voltar de recuperação, antes da 1ª ação gravada
            continue
        if tool in UNSAFE_TO_REPLAY:
            return None, f"{tool} depende do estado de quem aprendeu"
        if voltas and not out and not (em_casa_antes or {}).get(int(r["id"])):
            return None, "o voltar inicial não terminou no estado conhecido do app: a receita não teria de onde partir"
        if exploratoria and not out and not voltas and em_casa_antes is not None:
            if not (em_casa_antes or {}).get(int(r["id"])):
                return None, ("a exploração não partiu do estado conhecido do app: a receita não teria de onde partir "
                              "(31.327)")
            partida_ancorada = True
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
            templ, _, covered = detemplate(text, variables, marcadores=persona)
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
            if item["commit"] and any(s.get("via") == "filho" for s in sels):
                return None, "ação de efeito externo pelo filho do alvo — fraco demais"
            item["selectors"] = sels
        if pending_scrolls:
            item["scroll"] = {"direction": pending_scrolls[-1], "max": len(pending_scrolls) + 3}
            pending_scrolls = []
        if (voltas or partida_ancorada) and not out:
            item["ancora"] = ANCORA_ESTADO_CONHECIDO
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
                     app_packages: dict[str, str] | None = None,
                     arraste_final: bool = False) -> tuple[list[dict[str, Any]] | None, str]:
    """Entradas gravadas pela PESSOA (modo treinamento, item 13.2) numa etapa → receita, com as mesmas regras de
    `distill`: alvo com seletor estável e único, texto 100 % coberto por parâmetros, nada de voltar/início (depende
    do estado de quem ensinou), efeito externo nunca só por texto. O que não passa não vira receita — a etapa
    continua no fluxo e a IA a conduz na hora, que é a degradação que o sistema já tem.

    `side_effect`: o último toque da etapa é o commit (o que dispara o efeito).

    `arraste_final` (31.114 F2): o arraste que TERMINA a etapa é o objetivo dela, confirmado pela pessoa e sem saída de borda
    (quem chama confere isso em `training/arraste.py`): cada rolagem da cauda vira um item `scroll` relativo na receita, em
    vez de "rolagem sem ação-alvo depois dela". Sem o parâmetro, nada muda."""
    secret_values = {v for k, v in variables.items() if v and SENSITIVE_PARAM.search(k)}
    toques = [i for i, e in enumerate(inputs) if e["type"] in ("tap", "long_press")]
    ultimo_toque = toques[-1] if toques else None
    out: list[dict[str, Any]] = []
    pending_scrolls: list[str] = []
    for i, e in enumerate(inputs):
        tipo = e["type"]
        if tipo == "key":
            tecla = e.get("key_name")
            # Apagar COLADO ao texto que vem depois é ruído: a receita digita com `clear_first=True`, que já limpa o
            # campo (31.84; o painel digita sem limpar, e quem ensina apagava um caractere por vez). Só vale a
            # sequência contígua de `delete` seguida DIRETAMENTE de um `text`: com toque, arraste ou outra tecla no
            # meio, o apagar pode ter sido em outro campo, e a receita (que limpa só o do texto) divergiria do ensinado
            # sem avisar. Nos demais casos a tecla muda o resultado e segue recusando (etapa sem receita).
            if tecla == "delete":
                j = i + 1
                while j < len(inputs) and inputs[j]["type"] == "key" and inputs[j].get("key_name") == "delete":
                    j += 1
                # ...e só quando AQUELE texto foi enviado limpando o campo (`key_name == "clear_first"`, ver o gravador):
                # aí os apagar de antes não mudam o resultado. Sem isso o apagar foi parcial ("Olá Maria" → apaga 5 →
                # nome) e a receita, que limpa tudo, divergiria do ensinado: depende do conteúdo anterior.
                if j < len(inputs) and inputs[j]["type"] == "text" and inputs[j].get("key_name") == "clear_first":
                    continue
            # Enter colado ao texto que acabou de ser digitado é o "enviar" daquele campo: vira `press_enter` da própria
            # ação de digitar. Enter solto age sobre um campo que a receita não conhece e segue recusando.
            if tecla == "enter" and i > 0 and inputs[i - 1]["type"] == "text" and out and out[-1]["tool"] == "type_text":
                out[-1]["args"]["press_enter"] = True
                continue
            # back, home, recents (e o resto) dependem do estado de quem ensinou.
            return None, f"tecla {tecla} depende do estado de quem ensinou"
        if tipo == "swipe":
            if e.get("y") is None or e.get("y2") is None:
                # 31.97: arraste sobre teclado, padrão de bloqueio ou tela sensível sai sem coordenada; sem ela não há
                # direção, e "rolar para cima" por padrão seria um gesto inventado.
                return None, "arraste não gravado (teclado, padrão de bloqueio ou tela sensível)"
            dy = e["y2"] - e["y"]
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
            if commit and any(x.get("via") == "filho" for x in sels):
                return None, "ação de efeito externo pelo filho do alvo — fraco demais"
            item["args"] = {"duration_ms": 800} if tipo == "long_press" else {}
            item["selectors"] = sels
        if pending_scrolls:
            item["scroll"] = {"direction": pending_scrolls[-1], "max": len(pending_scrolls) + 3}
            pending_scrolls = []
        out.append(item)
        if commit:
            break
    if pending_scrolls:
        if not arraste_final:
            return None, "rolagem sem ação-alvo depois dela"
        out.extend({"tool": "scroll", "commit": False, "why": "ensinado no modo treinamento: o arraste é o objetivo da etapa",
                    "args": {"direction": d}} for d in pending_scrolls)
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
    #: 31.230: a tela é o estado conhecido do app? (o executor liga; `None` = não se sabe, e a receita com âncora não
    #: reproduz: nunca às cegas a partir de uma tela errada)
    em_casa: Callable[[UiTree], bool] | None = None

    @property
    def exhausted(self) -> bool:
        return self.idx >= len(self.actions)

    def next(self, tree: UiTree) -> Decision | None:
        if self.exhausted:
            return None
        act = self.actions[self.idx]
        if (act.get("ancora") == ANCORA_ESTADO_CONHECIDO and self.idx == 0 and self.scrolls == 0
                and (self.em_casa is None or not self.em_casa(tree))):
            # 30.80: a tela de partida errada é "não se aplicou", não defeito da receita; a IA assume
            raise AlvoAusente("ação 1: a receita parte do estado conhecido do app, e esta tela não é ele")
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
                # 30.80: só o alvo AUSENTE pode ser a tela de partida errada; o seletor que casa mais de um elemento
                # deixou de ser único, e isso é defeito da receita (a mesma mensagem, para o motivo casar igual).
                erro = RecipeDiverged if _alvo_ambiguo(tree, act["selectors"], self.variables) else AlvoAusente
                raise erro(f"ação {self.idx + 1} ({act['tool']}): alvo ausente ou ambíguo nesta tela")
            filho = next((s for s in act["selectors"] if s.get("via") == "filho"), None)
            if filho is not None and not toque_no_filho_cai_no_conteiner(tree, el, filho.get("conteiner")):
                raise RecipeDiverged(f"ação {self.idx + 1} ({act['tool']}): o toque no rótulo não cairia no contêiner "
                                     "gravado (outro clicável por cima ou na linha)")
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
def _alvo(seletores: object) -> object:
    """31.287: o ALVO de uma ação, não a lista de seletores que a IA gravou. Dois aparelhos tocam o mesmo botão e gravam
    listas de tamanhos diferentes (um tem o seletor `text` a mais): o que identifica o alvo é o seletor de maior confiança,
    o primeiro, com o resource-id e o discriminador (texto, descrição, o filho que recebe o toque). Os alternativos ficam
    de fora. Sem seletor (digitar no campo em foco), não há alvo."""
    if not isinstance(seletores, list) or not seletores or not isinstance(seletores[0], dict):
        return None
    primeiro = seletores[0]
    if primeiro.get("rid"):
        return {k: primeiro[k] for k in ("rid", "text", "via", "conteiner") if k in primeiro}
    return dict(primeiro)


def _caminho(acoes: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    """O que a reprodução faz, sem o `why` e sem o que não muda o caminho: a justificativa da IA muda a cada execução, a
    lista de seletores alternativos muda de aparelho para aparelho (31.287: vale o ALVO) e o teto de rolagens é só um
    limite. Fica a ferramenta, o efeito (`commit`), a âncora, os argumentos (o texto digitado, o pacote, a duração), o
    alvo e a direção da rolagem prévia."""
    saida: list[dict[str, object]] = []
    for a in acoes:
        item = {k: v for k, v in a.items() if k not in ("why", "selectors", "scroll")}
        item["alvo"] = _alvo(a.get("selectors"))
        rolagem = a.get("scroll")
        if isinstance(rolagem, dict):
            item["rolagem"] = rolagem.get("direction")
        saida.append(item)
    return saida


#: Quem decide quando a própria loja muda o status (aprendizado, prova em sombra, quarentena, substituição).
SISTEMA = "sistema"


@dataclass(frozen=True, slots=True)
class ReceitaVista:
    """A identidade e o caminho de uma receita: o que o veto do livro de aprendizado (ADR-054) confere."""

    package: str
    app_version: str
    signature: str
    variant: str
    step_hash: str
    #: As ações em JSON, como gravadas (a base do `content_hash`).
    actions: str
    #: A etapa de que ela é aprendida (`learned_from_step`, `<run>:<aparelho>:v<versão>:<chave>`): a evidência
    #: inválida (30.23) barra só renascer da MESMA execução. Vazio = desconhecida (o veto fica).
    learned_from: str = ""


@dataclass(frozen=True, slots=True)
class MudancaDaReceita:
    """Uma mudança de status feita pela própria loja, para a trilha (`learning_transitions`)."""

    recipe_id: int
    de: str | None
    para: str
    motivo: str
    por: str


class OuvinteDasReceitas(Protocol):
    """O livro de aprendizado do lado da loja. Chamado DENTRO da transação da escrita (mesma conexão): a trilha entra
    junto com o status, e o status nunca cai por causa dela nem do veto. Quem implementa e engole a própria falha a
    isola num `db.savepoint()` (22.5): no PostgreSQL, o erro engolido sem ele abortava a transação da loja."""

    def vetada(self, receita: ReceitaVista) -> bool:
        """O sistema não pode trazer de volta este caminho (uma pessoa o desligou)."""
        ...

    def exige_o_dono(self, recipe_id: int, receita: ReceitaVista) -> bool | None:
        """A candidata que concordou espera o dono mesmo sem `commit`: foi reaprendida depois de uma evidência
        inválida na mesma chave (30.23, classe B). A sombra a leva a `validated`, não a `active`. `None` quando a
        pergunta ficou sem resposta (a leitura do livro falhou): a candidata não sobe agora e a próxima concordância
        pergunta de novo — na dúvida, nada é publicado e a trilha não ganha um motivo que ninguém confirmou."""
        ...

    def mudou(self, mudanca: MudancaDaReceita) -> None: ...


class RecipeStore:
    def __init__(self, db: Database, ouvinte: OuvinteDasReceitas | None = None, *,
                 herdar: Callable[[], bool] = lambda: False):
        self.db = db
        #: A trilha e o veto do livro de aprendizado (ADR-054); `None` = sem trilha (a loja crua dos testes).
        self.ouvinte = ouvinte
        #: RA-20 (`ai.recipes_heranca`, lido a cada consulta): a chave sem receita herda, como CANDIDATA, a receita
        #: provada da mesma etapa noutra versão, variante ou na legada (`find`). Desligado, só mede a causa. A loja
        #: sozinha não herda: quem liga é o executor, e só com a prova em sombra (`recipes_promote_after > 0`).
        self.herdar = herdar
        #: 31.249: o escopo do alvo de cada receita (pela etapa em que ela foi aprendida); não muda, então fica em memória.
        self._escopos: dict[int, str | None] = {}

    def _avisar(self, recipe_id: int, de: str | None, para: str, motivo: str, por: str = SISTEMA) -> None:
        if self.ouvinte is not None:
            self.ouvinte.mudou(MudancaDaReceita(recipe_id=recipe_id, de=de, para=para, motivo=motivo, por=por))

    def find(self, package: str | None, app_version: str | None, step_hash: str | None, *,
             signature: str = "", variant: str = "", step_hash_generico: str | None = None,
             persona: str | None = None, prova_fluxo: str | None = None, escopo: str | None = None) -> Row | None:
        """Identidade da receita: pacote + versão + ASSINATURA + VARIANTE de interface + etapa.

        Assinatura entra porque dois APKs podem dizer a mesma versão e não serem o mesmo app; variante entra porque
        idioma e densidade mudam a tela. Receita aprendida numa combinação não vale para outra.

        Devolve a ativa; sem ela, a candidata em prova (o executor a põe em sombra, nunca a reproduz). A `validated`
        (D1: concordou, tem efeito externo, espera o dono) nunca sai daqui — para a etapa ela ainda não existe.

        É a CONSULTA do funil (`receita.consulta`): o executor só chega aqui com uma etapa elegível (modo ligado, app
        conhecido, efeito ainda não disparado), então a soma dos resultados é o total de TENTATIVAS elegíveis (o
        funil conta por tentativa — revisão F8: é a única unidade em que consulta, reprodução e retorno fecham). Chave
        incompleta não é "ausente": a etapa nem era elegível, e não entra na conta.

        RA-20: no "ausente", a causa é medida (`receita.ausente`, rótulo `causa`) e a chave herda, como CANDIDATA, a
        receita provada da mesma etapa noutra chave (`_herdar`). A herdeira volta como a candidata de sempre: a IA
        decide a etapa e ela só é comparada. O resultado dessa consulta é `herdada`.

        RA-20 fatia B: `step_hash_generico` (`hash_generico_da_linha`) é a segunda chave da MESMA consulta, onde mora a
        receita que serve a qualquer valor da etapa (`eh_generica`, decidido no save). A específica vence a genérica:
        ativa específica, ativa genérica, candidata específica, candidata genérica. Uma chamada, uma contagem: a
        tentativa achada pela genérica leva o rótulo `chave=generica` (a específica fica na série de antes), e quem
        chamou sabe a chave pelo `step_hash` da linha. Iguais (pós-condição vazia) ou sem genérica: uma consulta só.
        A herança tenta a específica e depois a genérica; a causa do ausente é medida uma vez, pela específica.

        30.81: a receita ensinada no modo treinamento só é achada para a `persona` que ensinou (a do objetivo; `None` =
        aparelho sem persona, que não a acha) até ser LIBERADA (`_restrita_ao_ensino`): o "Confirmar que fica" de uma
        pessoa no fluxo da sessão, ou a evidência a favor DELA numa execução real de prova desse fluxo. Fora disso, a
        consulta termina `ensino_em_prova`, sem herança nem genérica (falha fechada: a etapa vai para a IA). A execução
        de prova do próprio fluxo (`prova_fluxo`) a acha, para a prova exercitar o que libera. A receita que não veio
        do treino não paga consulta.
        """
        if not (package and app_version and step_hash):
            return None
        hashes = [step_hash, *([step_hash_generico] if step_hash_generico and step_hash_generico != step_hash else [])]
        row: Row | None = None
        resultado = ""
        # Candidata não reproduz (a IA decide e ela só é comparada): contá-la como "encontrada" quebraria a promessa do
        # funil de que toda encontrada termina num veredito de `receita.reproducao`.
        for achar, nome in ((self._ativa, "encontrada"), (self._candidata, "candidata")):
            row = next((r for h in hashes
                        if (r := achar(package, app_version, h, signature=signature, variant=variant)) is not None), None)
            if row is not None:
                resultado = nome
                break
        if row is not None and escopo is not None and self.escopo_da_receita(row) not in (None, escopo):
            # 31.249: a receita aprendida num post da PRÓPRIA conta não reproduz em post de terceiro (e vice-versa): a
            # identidade da etapa é a mesma, mas a tela não (a 111, da grade do perfil nosso, divergiu nos 3 alvos de
            # terceiro da onda 2 de 07/10 e custou US$ 0,14). A IA decide a etapa; nada de herança nem genérica.
            metricas.contar("receita.consulta", resultado="outro_escopo")
            self._registrar_consulta(int(row["id"]), "outro_escopo")
            return None
        if row is not None and self._restrita_ao_ensino(row, persona, prova_fluxo):
            metricas.contar("receita.consulta", resultado="ensino_em_prova")
            return None
        if row is None:
            # Uma consulta a mais, só no erro: distingue "nunca aprendida" de "aprendida e posta de lado". As duas
            # mandam a etapa para a IA, mas pedem coisas diferentes de quem lê (aprender × investigar a tela).
            quarentena = self.db.one("SELECT id FROM recipes WHERE app_package=? AND app_version=? AND app_signature=?"
                                     " AND variant=? AND step_hash IN (" + ",".join("?" * len(hashes)) + ")"
                                     " AND status='quarantined' LIMIT 1",
                                     (package, app_version, signature, variant, *hashes))
            if quarentena is not None:
                resultado = "quarentena"
                self._registrar_consulta(int(quarentena["id"]), "quarentena")
            else:
                resultado, row = self._herdar(package, app_version, step_hash, signature=signature, variant=variant)
                if row is None and len(hashes) > 1:
                    herdou, generica = self._herdar(package, app_version, hashes[1], signature=signature,
                                                    variant=variant, medir=False)
                    if generica is not None:
                        resultado, row = herdou, generica
        generica_casou = row is not None and len(hashes) > 1 and row["step_hash"] == hashes[1]
        metricas.contar("receita.consulta", resultado=resultado, chave="generica" if generica_casou else None)
        return row

    def _registrar_consulta(self, recipe_id: int, resultado: str) -> None:
        """31.271: a última consulta da receita (`ultima_consulta_em`/`_resultado`, migração 129), lida pela prova da
        candidata no Livro. No `find` é melhor esforço: a consulta corre fora de transação, por etapa elegível, e uma
        gravação que falha não pode virar exceção da consulta (a etapa só perderia o que a aba Aprendido mostra)."""
        try:
            self.db.execute("UPDATE recipes SET ultima_consulta_em=?, ultima_consulta_resultado=? WHERE id=?",
                            (now_iso(), resultado, recipe_id))
        except Exception:  # noqa: BLE001 - a marca é leitura do painel; a consulta segue sem ela
            log.exception("receita: última consulta da %s não gravada", recipe_id)

    def escopo_da_receita(self, row: Row) -> str | None:
        """31.249: o escopo do alvo da etapa em que a receita foi aprendida (`learned_from_step`): `proprio` quando o
        alvo dela (`post_author`/`username`) era a conta da persona daquela execução, `terceiro` quando
        era outra conta, `None` quando não se sabe (treino, etapa apagada, ação sem conta-alvo). `None` não filtra."""
        rid = int(row["id"])
        if rid not in self._escopos:
            self._escopos[rid] = self._escopo_aprendido(str(row["learned_from_step"] or ""))
        return self._escopos[rid]

    def _escopo_aprendido(self, step_id: str) -> str | None:
        if not step_id or step_id.startswith(PREFIXO_DO_TREINO):
            return None
        try:
            linha = self.db.one("SELECT s.bindings, o.profile_id FROM steps s"
                                " JOIN objectives o ON o.id = s.objective_id WHERE s.id=?", (step_id,))
            if linha is None:
                return None
            contas = [str(r["handle"]) for r in self.db.query(
                "SELECT handle FROM profile_accounts WHERE profile_id=? AND handle IS NOT NULL", (linha["profile_id"],))
                ] if linha["profile_id"] else []
            return escopo_do_alvo(loads(linha["bindings"], {}) or {}, contas)
        except Exception:  # noqa: BLE001 - o escopo é filtro de otimização: sem ele, a consulta de sempre
            log.exception("receita: escopo da etapa %s não lido", step_id)
            return None

    def liberada_fora_do_ensino(self, row: Row) -> bool:
        """30.81, para o rendimento do ensino: a receita vale fora da persona que ensinou (a do treino só depois do
        "Confirmar que fica" ou da prova real; a que não é do treino, sempre). A mesma régua da consulta."""
        return not self._restrita_ao_ensino(row, None)

    def _restrita_ao_ensino(self, row: Row, persona: str | None, prova_fluxo: str | None = None) -> bool:
        """30.81: a receita do treino só vale fora da persona que ensinou depois de LIBERADA: o "Confirmar que fica"
        explícito de uma pessoa no fluxo da sessão, ou uma evidência a favor DELA (etapa conduzida só pela receita e
        comprovada) numa execução real de prova desse fluxo (N1 da Reload, decisão da orquestradora: a prova só libera o
        que exercitou). A execução de prova do próprio fluxo a acha (`prova_fluxo`). O fluxo desligado sem isso não
        solta as receitas (N2). Sem o fluxo (apagado pela rota), falha fechada (N4): segue só para a persona da sessão,
        e só a evidência a favor dela numa execução real DE PROVA a libera (N5: a execução comum de quem ensinou não
        conta). Uma consulta, só na receita do treino."""
        origem = str(row["learned_from_step"] or "")
        if not origem.startswith(PREFIXO_DO_TREINO):
            return False
        fluxo = self.db.one("SELECT id, created_at FROM flows WHERE source=? ORDER BY created_at DESC, id DESC LIMIT 1",
                            (origem,))
        if fluxo is not None and prova_fluxo is not None and prova_fluxo == fluxo["id"]:
            return False
        provada = ("EXISTS (SELECT 1 FROM learning_evidence e JOIN runs ru ON ru.id = e.run_id WHERE e.item_ref=?"
                   " AND e.stance='for' AND e.simulated=0"
                   + (" AND ru.prova_fluxo_id=?" if fluxo is not None else " AND ru.prova_fluxo_id IS NOT NULL")
                   + " AND NOT EXISTS (SELECT 1 FROM learning_evidence i WHERE i.item_ref = e.item_ref"
                   " AND i.origin_ref = e.origin_ref AND i.stance='invalida')) AS provada")
        if fluxo is None:
            r = self.db.one("SELECT (SELECT profile_id FROM training_sessions WHERE id=?) AS persona, 0 AS confirmado, "
                            + provada, (origem[len(PREFIXO_DO_TREINO):], f"receita:{row['id']}"))
        else:
            r = self.db.one(
                "SELECT (SELECT profile_id FROM training_sessions WHERE id=?) AS persona,"
                " EXISTS (SELECT 1 FROM learning_transitions t WHERE t.item_ref=? AND t.decided_at>=? AND t.reason LIKE ?"
                "  AND t.decided_by NOT IN (?,?) AND t.decided_by NOT LIKE ?) AS confirmado, " + provada,
                (origem[len(PREFIXO_DO_TREINO):], f"fluxo:{fluxo['id']}", fluxo["created_at"], f"{CONFIRMADO_QUE_FICA}%",
                 SISTEMA, PLATAFORMA, f"{PREFIXO_DO_TREINO}%", f"receita:{row['id']}", fluxo["id"]))
        if r is None or bool(r["confirmado"]) or bool(r["provada"]):
            return False
        return r["persona"] is None or persona != r["persona"]

    def _herdar(self, package: str, app_version: str, step_hash: str, *, signature: str,
                variant: str, medir: bool = True) -> tuple[str, Row | None]:
        """A causa do "ausente" (`modules/learning/domain/causa_do_ausente.py`) e a herança, numa consulta a mais.

        A doadora é uma receita `active` da mesma etapa (`step_hash`) noutra versão, noutra variante ou na legada
        (assinatura e variante vazias, de antes de a chave tê-las); assinatura diferente nunca doa. A herdeira nasce
        `candidate` por `save` (que confere o veto da pessoa e "uma viva por chave") e guarda a origem da doadora
        (`learned_from_step`). Dois aparelhos herdando juntos: o segundo `save` não grava e ele recebe a candidata
        do primeiro. Qualquer falha aqui vira "ausente": a herança é otimização e nunca derruba a etapa.
        """
        alvo = ChaveDaReceita(app_version, signature, variant)
        try:
            vizinhas = [ReceitaVizinha(int(r["id"]), ChaveDaReceita(str(r["app_version"]), str(r["app_signature"] or ""),
                                                                    str(r["variant"] or "")), str(r["status"]))
                        for r in self.db.query("SELECT id, app_version, app_signature, variant, status FROM recipes"
                                               " WHERE app_package=? AND step_hash=?", (package, step_hash))]
            causa = causa_do_ausente(alvo, vizinhas)
            if medir:   # a chave genérica é a 2ª tentativa da MESMA consulta: a causa já foi contada pela específica
                metricas.contar("receita.ausente", causa=causa.value)
            dona = doadora(alvo, vizinhas) if self.herdar() else None
            if dona is None:
                return "ausente", None
            origem = self.db.one("SELECT step_key, actions, learned_from_step FROM recipes WHERE id=?", (dona.id,))
            if origem is None:
                return "ausente", None
            self.save(package=package, app_version=app_version, step_hash=step_hash, step_key=str(origem["step_key"]),
                      actions=loads(origem["actions"], []), learned_from=str(origem["learned_from_step"] or ""),
                      signature=signature, variant=variant, candidate=True,
                      heranca=f"herdada da receita {dona.id} ({causa.value}); em prova (sombra)")
            herdeira = self._candidata(package, app_version, step_hash, signature=signature, variant=variant)
            return ("herdada", herdeira) if herdeira is not None else ("ausente", None)
        except Exception as exc:  # noqa: BLE001 - herdar é otimização: a etapa segue com a IA
            log.warning("receitas: herança indisponível (%s %s): %s", package, step_hash, exc)
            return "ausente", None

    def viva(self, package: str, app_version: str, step_hash: str, *, signature: str = "",
             variant: str = "") -> Row | None:
        """30.79: a receita que SEGURA a chave hoje, a ativa ou a `validated` que espera o dono. É a que a demonstração
        da pessoa substitui (`save`), e o relatório do treino a lê ANTES de salvar para dizer qual já existia."""
        return (self._ativa(package, app_version, step_hash, signature=signature, variant=variant)
                or self._com_status("validated", package, app_version, step_hash, signature=signature, variant=variant))

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

    def chave_ocupada(self, package: str, app_version: str, step_hash: str, *, signature: str = "",
                      variant: str = "") -> bool:
        """A chave tem uma receita que a SEGURA (`viva`: a ativa ou a `validated`)? Só lê. Quem não é treino não a
        substitui (o `save` devolve `None`). A demonstração a substitui quando o caminho é outro (30.79), e o que ela
        faria é `previa_do_treino`."""
        return self.viva(package, app_version, step_hash, signature=signature, variant=variant) is not None

    def previa_do_treino(self, package: str, app_version: str, step_hash: str,
                         actions: Sequence[Mapping[str, object]], *, signature: str = "",
                         variant: str = "") -> tuple[str, Row | None]:
        """O que o `save` do TREINO faria com estas ações, sem gravar (a prévia do 31.86 e o relatório do salvar):
        - `("ja_vale", viva)`: o mesmo caminho da receita que segura a chave (o `save` devolve o id dela);
        - `("substitui", viva)`: outro caminho (o `save` grava a versão nova e a viva vira `superseded`);
        - `("grava", None)`: nada segura a chave.
        A conta é a mesma do `save` (`viva` + `_caminho`). O veto não entra, porque o treino não passa por ele."""
        viva = self.viva(package, app_version, step_hash, signature=signature, variant=variant)
        if viva is None:
            return "grava", None
        return ("ja_vale" if _caminho(loads(viva["actions"], [])) == _caminho(actions) else "substitui"), viva

    def caminho_vetado(self, receita: ReceitaVista) -> bool:
        """Uma PESSOA desligou este caminho e o sistema não o traz de volta (`ouvinte.vetada`)? Só lê; sem ouvinte,
        nunca. É a conta que o `save` aplica a quem não é treino, e que o reparo do treino (31.86) aplica também."""
        return self.ouvinte is not None and self.ouvinte.vetada(receita)

    def status_da_chave(self, package: str, app_version: str, step_hash: str, *, signature: str = "",
                        variant: str = "") -> str | None:
        """O status da versão mais nova que a chave já teve, de QUALQUER status (`None`: chave virgem). Só lê. O
        reparo do treino (31.86) só grava em chave virgem: o `save` do treino pula o veto e põe uma ativa nova no lugar
        da quarentena, o que ressuscitaria o caminho que o aprendizado rebaixou ou que uma pessoa desligou."""
        return self.db.scalar("SELECT status FROM recipes WHERE app_package=? AND app_version=? AND app_signature=?"
                              " AND variant=? AND step_hash=? ORDER BY version DESC, id DESC LIMIT 1",
                              (package, app_version, signature, variant, step_hash))

    def tem_ativa_em_qualquer_versao(self, package: str, step_hash: str) -> bool:
        """Há receita ativa ou validada desta etapa em QUALQUER versão do app (31.110)? Só lê. O reparo do treino, com o
        aparelho sem responder, não sabe a versão do app e então não acha a chave; isto diz se a etapa já tem receita."""
        return self.db.scalar("SELECT 1 FROM recipes WHERE app_package=? AND step_hash=? AND status IN ('active','validated')"
                              " LIMIT 1", (package, step_hash)) is not None

    def save(self, *, package: str, app_version: str, step_hash: str, step_key: str, actions: list[dict[str, Any]],
             learned_from: str, signature: str = "", variant: str = "", candidate: bool = False,
             replaces: int | None = None, heranca: str | None = None, so_em_chave_virgem: bool = False) -> int | None:
        """Grava uma versão nova SÓ se não houver receita ativa (a ativa só sai por quarentena), salvo a demonstração da
        pessoa (30.79, abaixo).

        `candidate`: a receita nasce em prova (`recipes_promote_after`, o caminho que a IA aprendeu); sem ele nasce
        ativa — o modo treinamento (a pessoa demonstrou) e o `recipes_promote_after: 0`.
        Candidata em prova só é trocada por quem a viu divergir (`replaces` = o id dela), e só por um caminho que
        reproduzido faria outra coisa: aparelhos aprendendo em paralelo, sem nunca terem comparado com ela, não
        reescrevem a prova a cada execução, e uma cópia dela não vira versão nova. A versão gravada marca
        as anteriores ainda vivas da chave (candidata trocada, ativa que caiu em quarentena) como `superseded` — uma
        só receita viva por chave, e a substituída não volta pelo painel.

        D1: a `validated` (concordou, tem efeito externo, espera o dono) segura a chave como a ativa — senão cada
        execução da etapa deixaria mais uma validada na fila do dono. E o caminho que uma PESSOA desligou não volta
        pelo sistema (`ouvinte.vetada`); o treino é da pessoa e não passa pelo veto.

        `heranca` (RA-20): o motivo da trilha da herdeira (`find`). Herdar é do SISTEMA, mesmo de uma receita ensinada
        no treino (a origem fica em `learned_from`): passa pelo veto e a trilha é do sistema.

        30.79 (B1 do mapa do ensino): a demonstração da pessoa SUBSTITUI a receita que segura a chave (`viva`: a ativa
        aprendida da IA, a `validated` que espera o dono, ou a de uma demonstração anterior), com a trilha nos dois lados
        (`por` = a sessão de treino). Antes ela era descartada em silêncio ("já havia receita ativa"). Com o MESMO
        caminho, nada se grava: devolve o id da que já vale, que é a demonstração.

        `so_em_chave_virgem` (31.86, o reparo das receitas de um treino já salvo): grava só se a chave NUNCA teve
        receita, de nenhum status, e se nenhuma pessoa vetou o caminho. O reparo roda sem a pessoa, então não herda o poder da
        demonstração, que pula o veto e substitui a receita viva, inclusive a que o aprendizado pôs em quarentena. O
        reparo confere o mesmo antes (`status_da_chave`, `caminho_vetado`), e aqui a conta se repete dentro da `tx`,
        sem janela entre a conferência e a gravação.
        """
        chave = (package, app_version, signature, variant, step_hash)
        treino = learned_from.startswith("training:") and heranca is None
        with self.db.tx():
            if so_em_chave_virgem and (
                    self.db.scalar("SELECT 1 FROM recipes WHERE app_package=? AND app_version=? AND app_signature=?"
                                   " AND variant=? AND step_hash=? LIMIT 1", chave) is not None
                    or self.caminho_vetado(ReceitaVista(package=package, app_version=app_version, signature=signature,
                                                        variant=variant, step_hash=step_hash, actions=dumps(actions),
                                                        learned_from=learned_from))):
                return None              # o reparo (31.86): a conferência dele, de novo DENTRO da transação
            if replaces is not None:
                # RA-20 B: a candidata ESPECÍFICA que divergiu e um caminho da IA que serve a qualquer valor (o executor
                # grava na chave genérica). Ela sai aqui, mesmo que a genérica não grave (já tem a sua em prova): senão,
                # consultada antes da genérica, ficaria divergindo para sempre sem ninguém que a trocasse.
                velha = self.db.one("SELECT step_hash, status FROM recipes WHERE id=?", (replaces,))
                if velha is not None and velha["step_hash"] != step_hash and velha["status"] == "candidate":
                    self.db.execute("UPDATE recipes SET status='superseded' WHERE id=? AND status='candidate'",
                                    (replaces,))
                    self._avisar(replaces, "candidate", "superseded",
                                 "divergiu; o caminho da IA serve a qualquer valor e vai para a chave genérica")
                    replaces = None
            viva = self.viva(package, app_version, step_hash, signature=signature, variant=variant)
            if viva is not None:
                if not treino:
                    return None
                if _caminho(loads(viva["actions"], [])) == _caminho(actions):
                    return int(viva["id"])           # a demonstração é o caminho que já vale: nada novo a gravar
            em_prova = self._candidata(package, app_version, step_hash, signature=signature, variant=variant)
            if candidate and em_prova is not None and em_prova["id"] != replaces:
                return None
            if (candidate and em_prova is not None
                    and _caminho(loads(em_prova["actions"], [])) == _caminho(actions)):
                # Divergência sem diferença no que a receita grava (ex.: a IA rolou para baixo e voltou; a receita só
                # guarda a última direção): a cópia não mudaria a reprodução, só deixaria uma `superseded` por
                # execução. Fica a candidata, com a prova já recomeçada pela divergência.
                return None
            gravadas = dumps(actions)
            if not treino and self.caminho_vetado(ReceitaVista(
                    package=package, app_version=app_version, signature=signature, variant=variant,
                    step_hash=step_hash, actions=gravadas, learned_from=learned_from)):
                return None
            ver = int(self.db.scalar(
                "SELECT COALESCE(MAX(version),0)+1 FROM recipes WHERE app_package=? AND app_version=? AND"
                " app_signature=? AND variant=? AND step_hash=?", chave))
            # 30.79: a demonstração tira de cena também a que segurava a chave (a ativa ou a `validated`).
            trocadas = "'candidate','quarantined','active','validated'" if treino else "'candidate','quarantined'"
            antigas = self.db.query("SELECT id, status FROM recipes WHERE app_package=? AND app_version=? AND"
                                    " app_signature=? AND variant=? AND step_hash=?"
                                    f" AND status IN ({trocadas})", chave) if self.ouvinte else []
            self.db.execute("UPDATE recipes SET status='superseded' WHERE app_package=? AND app_version=? AND"
                            " app_signature=? AND variant=? AND step_hash=?"
                            f" AND status IN ({trocadas})", chave)
            status = "candidate" if candidate else "active"
            novo = int(self.db.inserted_id(
                "INSERT INTO recipes(app_package, app_version, app_signature, variant, step_hash, step_key, version,"
                " status, actions, learned_from_step, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (package, app_version, signature, variant, step_hash, step_key, ver,
                 status, gravadas, learned_from, now_iso())) or 0)
            for antiga in antigas:
                if treino:                       # a causa é a demonstração, também na candidata e na quarentena
                    self._avisar(int(antiga["id"]), str(antiga["status"]), "superseded",
                                 f"substituída pela v{ver}, demonstrada pela pessoa no modo treinamento",
                                 por=learned_from)
                else:
                    self._avisar(int(antiga["id"]), str(antiga["status"]), "superseded", f"substituída pela v{ver}")
            if novo:
                motivo = (heranca if heranca is not None else "ensinada no modo treinamento" if treino else
                          "aprendida da IA; em prova (sombra)" if candidate else
                          "aprendida da IA já ativa (ai.recipes_promote_after: 0, o modo anterior)")
                self._avisar(novo, None, status, motivo, por=learned_from if treino else SISTEMA)
            return novo

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
            self.db.execute("UPDATE recipes SET replay_ok=replay_ok+1, consecutive_fail=0, nao_aplicavel_seguidas=0,"
                            " last_used_at=? WHERE id=?", (now_iso(), recipe_id))
            return False
        return self._falhou(recipe_id, zera_serie=True)

    def _falhou(self, recipe_id: int, *, zera_serie: bool) -> bool:
        """A falha comum: `replay_fail`, `consecutive_fail` e a quarentena. `zera_serie=False` só na falha que VEM da
        série de "não se aplicou" (30.80): zerá-la ali faria a série recomeçar e a quarentena chegar só na 9ª."""
        serie = ", nao_aplicavel_seguidas=0" if zera_serie else ""
        with self.db.tx():
            self.db.execute("UPDATE recipes SET replay_fail=replay_fail+1, consecutive_fail=consecutive_fail+1"
                            f"{serie}, last_used_at=? WHERE id=?", (now_iso(), recipe_id))
            row = self.db.one("SELECT consecutive_fail, status FROM recipes WHERE id=?", (recipe_id,))
            if row and row["consecutive_fail"] >= QUARANTINE_AFTER:
                self.db.execute("UPDATE recipes SET status='quarantined' WHERE id=?", (recipe_id,))
                if row["status"] != "quarantined":           # rebaixar é automático (D1), e fica na trilha
                    self._avisar(recipe_id, str(row["status"]), "quarantined",
                                 f"{QUARANTINE_AFTER} falhas seguidas ao reproduzir")
                return True
        return False

    def nao_aplicavel(self, recipe_id: int) -> tuple[bool, bool]:
        """30.80: a receita divergiu na AÇÃO 1 por alvo ausente (a tela de partida era outra) e a etapa terminou
        comprovada pela IA. Não é veredito sobre ela: nem `replay_ok` nem `replay_fail`, e `consecutive_fail` fica como
        está. A partir da `NAO_APLICAVEL_CONTA_APOS`-ésima seguida, CADA uma conta como falha comum (`replay_fail`,
        `consecutive_fail`, quarentena), sem zerar a série: um 1º seletor quebrado de vez chega à quarentena na 5ª
        (3ª, 4ª e 5ª contam). Só o ok e a falha comum zeram a série.

        Devolve `(contou_como_falha, entrou_em_quarentena)`."""
        with self.db.tx():
            self.db.execute("UPDATE recipes SET nao_aplicavel_seguidas=nao_aplicavel_seguidas+1, last_used_at=?"
                            " WHERE id=?", (now_iso(), recipe_id))
            seguidas = int(self.db.scalar("SELECT nao_aplicavel_seguidas FROM recipes WHERE id=?", (recipe_id,)) or 0)
        if seguidas < NAO_APLICAVEL_CONTA_APOS:
            metricas.contar("receita.reproducao", resultado="nao_aplicavel")
            return False, False
        metricas.contar("receita.reproducao", resultado="divergiu")
        return True, self._falhou(recipe_id, zera_serie=False)

    def nao_aplicavel_em_prova(self, recipe_id: int) -> bool:
        """31.262: o 30.80 na SOMBRA. A candidata não se aplicou nesta execução (o alvo da ação 1 não estava na tela de
        partida) e a IA comprovou a etapa: não é veredito sobre ela. Antes, contava como divergência, zerava a prova e
        trocava a candidata pelo caminho da IA, que partia de outra tela (a chave genérica do `open_profile` passou por
        118, 166, 222 e 223 sem nunca ficar ativa; a 222 virou a 223 por uma etapa que começou fora do app).

        A série é a mesma `nao_aplicavel_seguidas` da reprodução; a concordância e a divergência a zeram. Devolve True a
        partir da `NAO_APLICAVEL_CONTA_APOS`-ésima seguida: aí conta como divergência (o 1º seletor quebrado de vez não
        prende a chave numa candidata que nunca se aplica)."""
        with self.db.tx():
            agora = now_iso()
            self.db.execute("UPDATE recipes SET nao_aplicavel_seguidas=nao_aplicavel_seguidas+1, last_used_at=?"
                            " WHERE id=?", (agora, recipe_id))
            seguidas = int(self.db.scalar("SELECT nao_aplicavel_seguidas FROM recipes WHERE id=?", (recipe_id,)) or 0)
            if seguidas < NAO_APLICAVEL_CONTA_APOS:
                # 31.271: sem veredito, a última consulta diz que ela não se aplicou; a que conta vira a divergência
                # que o executor leva ao `shadow` logo depois (gravada lá, uma vez só).
                self.db.execute("UPDATE recipes SET ultima_consulta_em=?, ultima_consulta_resultado='nao_aplicavel'"
                                " WHERE id=?", (agora, recipe_id))
        conta = seguidas >= NAO_APLICAVEL_CONTA_APOS
        metricas.contar("receita.sombra", resultado="divergiu" if conta else "nao_aplicavel")
        return conta

    def shadow(self, recipe_id: int, agreed: bool, *, promote_after: int, promote_after_com_efeito: int | None = None,
               simulada: bool = False) -> bool:
        """Veredito da sombra de UMA execução da etapa. Devolve True se a candidata foi promovida a ativa agora.

        A unidade é a execução, não a decisão: duas decisões concordantes numa mesma etapa não são repetição. Na
        candidata, a divergência zera os DOIS contadores (a prova recomeça), de modo que `shadow_agree` é a
        sequência de concordâncias e `shadow_agree/shadow_total` continua uma taxa; na ativa (modo `shadow` global)
        a taxa só acumula. A promoção confere, na mesma transação, que ela ainda é a candidata e que a chave não
        ganhou uma ativa enquanto isso (outro aparelho, ou a pessoa pelo painel) — nunca duas ativas por chave.

        D1 (ADR-054): a candidata com ação de efeito externo (`commit`) que concordou vai para `validated`, não para
        `active` — o sistema não publica sozinho o que age fora da máquina; ela espera o dono em "Para aprovar", e
        `find` não a devolve. Aí a resposta é False: a receita não passou a agir. O caminho que uma pessoa desligou
        (`ouvinte.vetada`) fica candidato. A reaprendida depois de uma evidência inválida (`ouvinte.exige_o_dono`,
        30.23) também para em `validated`, mesmo sem `commit`; sem resposta do ouvinte, fica candidata.

        RA-19 B: a concordância de uma execução SIMULADA (`simulada`) não conta para a candidata: não soma à sequência
        nem a promove; só evidência real publica. A divergência dela zera, como qualquer outra (o lado seguro), e na
        ativa a taxa acumula igual.

        31.287: a prova vale POR EFEITO. `promote_after` é o de uma receita sem ação de efeito externo;
        `promote_after_com_efeito` (quando dado) é o da que tem `commit`. As concordâncias de aparelhos diferentes somam
        (a sequência é da receita, não do aparelho), porque a receita é uma só por etapa.

        31.271: o veredito que conta grava a última consulta (`concordou`/`divergiu`). A concordância simulada que não
        conta não grava nada: a aba Aprendido diria "concordou" de uma consulta que não somou à prova.
        """
        with self.db.tx():
            row = self.db.one("SELECT * FROM recipes WHERE id=?", (recipe_id,))
            if row is None:
                return False
            if agreed and simulada and row["status"] == "candidate":
                return False
            consulta = (now_iso(), "concordou" if agreed else "divergiu")
            if agreed:
                self.db.execute("UPDATE recipes SET shadow_total=shadow_total+1, shadow_agree=shadow_agree+1,"
                                " nao_aplicavel_seguidas=0, ultima_consulta_em=?, ultima_consulta_resultado=?"
                                " WHERE id=?", (*consulta, recipe_id))
            elif row["status"] == "candidate":
                self.db.execute("UPDATE recipes SET shadow_total=0, shadow_agree=0, nao_aplicavel_seguidas=0,"
                                " ultima_consulta_em=?, ultima_consulta_resultado=? WHERE id=?", (*consulta, recipe_id))
            else:
                self.db.execute("UPDATE recipes SET shadow_total=shadow_total+1, ultima_consulta_em=?,"
                                " ultima_consulta_resultado=? WHERE id=?", (*consulta, recipe_id))
            seguidas = int(self.db.scalar("SELECT shadow_agree FROM recipes WHERE id=?", (recipe_id,)) or 0)
            com_efeito = receita_tem_efeito(loads(row["actions"], []))
            necessarias = promote_after_com_efeito if com_efeito and promote_after_com_efeito is not None else promote_after
            if not (agreed and row["status"] == "candidate" and seguidas >= max(1, necessarias)):
                return False
            for outra in ("active", "validated"):
                if self._com_status(outra, row["app_package"], row["app_version"], row["step_hash"],
                                    signature=row["app_signature"], variant=row["variant"]) is not None:
                    return False
            vista = ReceitaVista(
                package=row["app_package"], app_version=row["app_version"], signature=row["app_signature"],
                variant=row["variant"], step_hash=row["step_hash"], actions=row["actions"],
                learned_from=row["learned_from_step"] or "")
            if self.ouvinte is not None and self.ouvinte.vetada(vista):
                return False
            efeito = com_efeito
            reaprendida = False if efeito or self.ouvinte is None else self.ouvinte.exige_o_dono(recipe_id, vista)
            if reaprendida is None:
                return False
            novo = "validated" if efeito or reaprendida else "active"
            self.db.execute("UPDATE recipes SET status=? WHERE id=? AND status='candidate'", (novo, recipe_id))
            self._avisar(recipe_id, "candidate", novo,
                         f"concordou com a IA em {seguidas} execução(ões) seguidas"
                         + ("; tem ação de efeito externo: publicar é do dono (D1)" if efeito else
                            "; reaprendida depois de uma evidência inválida: publicar é do dono (classe B)"
                            if reaprendida else "; sem efeito externo: publicada pelo sistema (D1)"))
            if novo == "active":
                self._aposentar_legadas(row)
            return novo == "active"

    def _aposentar_legadas(self, provada: Row) -> None:
        """RA-20: a receita que se provou na chave COMPLETA (assinatura e variante conhecidas) aposenta a legada ativa da
        mesma etapa e da mesma versão (assinatura ou variante vazias, de antes de a chave tê-las). A legada não casava
        mais nenhuma consulta com a chave completa; a trilha diz quem a substituiu. Só no `active`: a herdeira que
        espera o dono (`validated`) ainda não age, e a legada fica até ele decidir.

        Só a família da provada: cada parte da chave é a dela ou vazia. A ativa de OUTRA assinatura (mesmo com a
        variante vazia) ou de OUTRA variante (mesmo sem assinatura) ainda atende a consulta de quem tem aquela chave, e
        fica (revisão da Android no PR #131)."""
        assinatura, variante = provada["app_signature"], provada["variant"]
        if not (assinatura and variante):
            return
        legadas = self.db.query(
            "SELECT id FROM recipes WHERE app_package=? AND app_version=? AND step_hash=? AND status='active'"
            " AND app_signature IN ('', ?) AND variant IN ('', ?) AND NOT (app_signature=? AND variant=?) AND id<>?",
            (provada["app_package"], provada["app_version"], provada["step_hash"], assinatura, variante, assinatura,
             variante, provada["id"]))
        for legada in legadas:
            self.db.execute("UPDATE recipes SET status='superseded' WHERE id=? AND status='active'", (legada["id"],))
            self._avisar(int(legada["id"]), "active", "superseded",
                         f"legada sem assinatura ou variante: a receita {provada['id']} provou-se na chave completa")

    def replayer(self, row: Row, variables: dict[str, str]) -> Replayer:
        return Replayer(recipe_id=row["id"], version=row["version"], actions=loads(row["actions"], []), variables=variables)


# ------------------------------------------------------------------ 31.249: escopo do alvo
ESCOPO_PROPRIO, ESCOPO_TERCEIRO = "proprio", "terceiro"
#: Os argumentos que nomeiam a CONTA-alvo da etapa (o perfil aberto, o autor do post). A legenda e a imagem não dizem de
#: quem é o alvo; o `objeto_alvo` do catálogo só existe nas ações de efeito, e a navegação (OPEN_POST) o traz nos argumentos.
_ALVO_QUE_E_CONTA = ("username", "post_author")
_MARCADOR_DE_CONTA = re.compile(r"^@?\{conta_\w+_usuario(?:_\d+)?\}$")


def escopo_do_alvo(bindings: Mapping[str, object], contas: Sequence[str]) -> str | None:
    """31.249: `proprio` quando a conta-alvo da etapa (`_ALVO_QUE_E_CONTA`) é uma das `contas` da persona (ou o marcador
    dela, 31.113 F3), `terceiro` quando é outra conta, `None` sem conta-alvo (argumento ausente ou vazio)."""
    valores = [str(bindings.get(n) or "").strip() for n in _ALVO_QUE_E_CONTA]
    valores = [v for v in valores if v]
    if not valores:
        return None
    minhas = {c.strip().lstrip("@").casefold() for c in contas if c and c.strip()}
    if any(_MARCADOR_DE_CONTA.match(v) or v.lstrip("@").casefold() in minhas for v in valores):
        return ESCOPO_PROPRIO
    return ESCOPO_TERCEIRO
