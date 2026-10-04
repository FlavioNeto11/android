"""Catálogo de capabilities: o que o sistema sabe fazer em um aplicativo, descrito uma vez e no backend.

Por que existe: hoje o planejador devolve a etapa inteira (título, objetivo, pós-condição, guardas). Quanto mais
rico o esquema, maior a chance de o modelo devolver algo que não valida — e um 400 no planejamento derruba a
execução sem recuperação. Com catálogo, o modelo devolve só `{key, capability, depends_on, bindings, for_each}`;
quem monta a etapa é este módulo, com texto revisado por gente.

O caminho livre continua valendo: app sem catálogo planeja como sempre planejou. É o que mantém o QA Messenger
intacto enquanto o Instagram ganha vocabulário próprio.

O catálogo de um app é DADO (ADR-052, fatia 2): `app/conhecimento/apps/<pacote>/catalogo.yaml`, lido e validado por
`carregar_catalogo`. Nenhum app escreve `Capability(...)` em Python; o manifesto dele pede `catalogo_do_pacote`.
"""
from __future__ import annotations

import functools
import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import MISSING, dataclass, field, fields, replace
from pathlib import Path
from typing import Any, Literal, get_args

import yaml

from ..models import SAIDA_NOME_RE, MissingInfo, PlanStep, Postcondition

# Política padrão por capability. O perfil pode endurecer (nunca afrouxar em silêncio) em `automation_policy`.
POLICIES = ("autonomous", "approval_required", "manual_only", "disabled")

_VARIAVEL = re.compile(r"\{([a-z_][a-z0-9_]*)\}")

# Bindings do texto de uma etapa que escreve. `content` é o texto pronto; `content_brief` é a INTENÇÃO, e é o
# padrão — de um briefing cada perfil escreve a sua versão, na própria voz. `content_verbatim` é o pedido
# explícito de "estas palavras exatas, iguais para todos".
TEXTO = "content"
BRIEFING = "content_brief"
VERBATIM = "content_verbatim"
#: 30.64: os argumentos de TEXTO da ação. Ficam fora do objeto-alvo: o texto se compara à parte, já com a edição.
ARGUMENTOS_DE_TEXTO: frozenset[str] = frozenset({"content", BRIEFING, VERBATIM})
_SIM = ("true", "1", "sim", "yes", "verdadeiro")


@dataclass(frozen=True, slots=True)
class Capability:
    """Uma ação nomeada, com pré-condição, pós-condição, efeito, risco e reconciliação — como o §13 pede."""

    key: str
    title: str
    goal: str
    post_kind: str
    post_value: str
    post_description: str
    bindings: tuple[str, ...] = ()              # variáveis obrigatórias (o planejador precisa preencher)
    optional_bindings: tuple[str, ...] = ()
    # Opcionais que, sem valor nesta etapa, vêm da etapa anterior mais próxima do MESMO plano que declara o argumento
    # (`herdar_argumentos`). A guarda de cartão só agia se o planejador repetisse a legenda em cada etapa da
    # publicação; com ela só na abertura, curtida, balão e comentário valiam em qualquer cartão. Quem declara é a
    # ação que CONTINUA um alvo, não a que o começa (abrir outro post por posição não pode herdar a legenda).
    inherited_bindings: tuple[str, ...] = ()
    precondition: str | None = None
    side_effect: bool = False
    risk: str = "low"                           # low | medium | high — orienta a política padrão
    commit_selector: str | None = None          # QUEM dispara o efeito; sem isso o commit é adivinhado por verbo
    commit_guard: tuple[str, ...] = ()
    # 29.79: interruptores que têm de estar LIGADOS na tela antes do toque de efeito, cada um como
    # `<argumento>:<seletor>` — vale quando o argumento da etapa é "true" (o "Add AI label" do Instagram quando
    # `rotulo_ia` é "true"). Ligado é o elemento do seletor marcado (`checked`) ou um marcado na mesma linha dele (o
    # texto e o interruptor costumam ser irmãos). Sem ele ligado o executor não toca no efeito e, na segunda recusa,
    # para pedindo uma pessoa.
    commit_switch: tuple[str, ...] = ()
    # Guardas que precisam estar na MESMA faixa vertical do alvo. É o que distingue a linha certa numa lista:
    # numa lista de pedidos, "@ana" em qualquer lugar da tela não prova que o botão tocado é o dela.
    band_guard: tuple[str, ...] = ()
    # Textos que identificam a PUBLICAÇÃO alvo num feed (a legenda). Diferem do `band_guard` na forma da faixa: num
    # cartão de publicação a legenda fica ABAIXO dos botões, e a faixa de uma linha de lista não a alcança. Valem só
    # com o argumento preenchido (`guardas_do_cartao`); sem ele, a etapa segue exatamente como antes (post por
    # posição). Com ele: a pós-condição só vale com o texto na tela, o toque de efeito só vale no cartão que o traz
    # (`UiTree.text_in_card`) e a prova local por seletor só vale nesse cartão. Motivo: r-20260928165254-e31953 e
    # r-20260928195344-02ee9e, em que "Posts" e `desc==Liked` passavam com QUALQUER publicação.
    card_guard: tuple[str, ...] = ()
    # Os controles do CARTÃO que esta etapa toca SEM efeito externo (o balão de comentários): com `card_guard`
    # resolvido, um toque que acerta um deles só vale no cartão da legenda (`UiTree.text_in_card`). O toque de
    # efeito já é conferido pelo `commit_selector`; este é o que abre a folha "Comments", igual para qualquer
    # publicação — e, com ela aberta, a legenda do fundo continua na árvore (r-20260928165254-e31953), então a
    # pós-condição não distingue o balão do cartão vizinho. Lista: mais de um controle abre a mesma folha (o link
    # "View all N comments"). Só vale junto de `card_guard` (conferido na carga).
    card_control: tuple[str, ...] = ()
    reconciliation: str = ""                    # o que observar depois do efeito para saber se ele valeu
    default_policy: str = "autonomous"
    limit_bucket: str | None = None             # likes | comments | follows | dms | posts — chave do limite por hora
    # O argumento que diz QUEM é a pessoa do outro lado do efeito (ADR-055): o alvo da regra de uma conta por alvo e a
    # contraparte gravada no histórico. Obrigatório em toda ação com `limit_bucket` (conferido na carga). Antes o alvo
    # era adivinhado por `username or target`, e curtir e comentar, que não têm `username`, gravavam `counterparty`
    # NULL em todo `post_liked`/`comment_replied` do central — a coordenação de frota nem chegava a ser consultada.
    counterparty: str | None = None
    # 30.64: os argumentos que dizem SOBRE O QUÊ o efeito age (o post, o comentário, a conversa, a mídia): com o
    # perfil, a ação e o texto, formam a identidade do item aprovado e do item já feito. A pessoa sozinha não basta:
    # "comentar no post A de @ana" e "no post B" têm o mesmo `counterparty`. Ação com efeito sem esta declaração
    # falha fechado (não se aprova antes nem se reconhece repetida); o teste do catálogo lista as que faltam.
    objeto_alvo: tuple[str, ...] = ()
    needs_draft: bool = False                   # exige conteúdo gerado (e aprovado, se a política pedir) antes
    interaction_type: str | None = None         # que interação isto vira no histórico do perfil (dm_sent, followed…)
    internal: bool = False                      # resolvida por código determinístico; não é oferecida ao planejador
    # 29.30: ações `internal` que o `compose` põe IMEDIATAMENTE antes desta, com os argumentos dela (CREATE_POST →
    # PUT_MEDIA_IN_GALLERY). A interna não é oferecida ao planejador, então quem a pede é o contrato da ação que precisa
    # dela, não o modelo. Conferido na carga: cada chave existe, é `internal` e os argumentos obrigatórios dela são
    # argumentos desta.
    preparo: tuple[str, ...] = ()
    timeout_s: int = 180
    max_attempts: int = 3
    collect: bool = False                       # etapa de coleta (pós-condição items_collected)
    # Coleta: teto de itens e se a lista deve voltar ao topo antes de ler. No feed, voltar ao topo é "puxar para
    # atualizar" — muda o conteúdo em vez de reposicionar. Estes ajustes vivem no CONTEXTO da ferramenta, nunca
    # nos argumentos, senão a receita aprendida deixaria de casar.
    collect_limit: int | None = None
    collect_from_top: bool = True
    # Desfaz a rolagem da coleta no fim. Para lista onde ir ao topo e perigoso (a folha de comentarios FECHA
    # se o arrasto passar do topo), mas cujos alvos sao lidos de cima para baixo.
    collect_rewind: bool = False
    # Como extrair, do texto lido na tela, a CHAVE que identifica o item. O que a lista mostra costuma ser uma frase
    # de acessibilidade ("fulano said oi"), e é essa frase que vira `{item}` e, daí, o `{username}` das etapas do
    # bloco. Sem recortar a chave, as guardas passam a exigir a frase inteira na tela e nunca casam. Grupo 1 = chave.
    item_key: str | None = None
    # Textos que, se aparecerem na tela DEPOIS do efeito, provam que ele NÃO valeu (ex.: "Not delivered").
    # Ficam aqui, e não no executor, porque são específicos do app e da versão — como `commit_selector`.
    failure_marks: tuple[str, ...] = ()
    # Textos que, ENQUANTO aparecem na tela, dizem que o efeito ainda está a caminho (ex.: "Sending…"). Não desmentem
    # o efeito, mas também nunca o comprovam: nem a prova local nem o "sim" do modelo fecham a etapa com um deles à
    # vista, e o modelo nem é perguntado — o executor segue olhando até o prazo da verificação. Motivo: em 19/09 a DM
    # da beatriz foi dada por enviada com "Sending…" congelado debaixo da bolha (ADR-055).
    pending_marks: tuple[str, ...] = ()
    # Prova local (sem modelo) para uma pós-condição `model_judged`, quando existe uma conferência determinística
    # confiável pela árvore (gramática e regras em `taskqueue/proofs.py`): "sent_text" (o `content` apareceu no
    # fio e saiu do campo de escrita, achado #102), "sent_text:<sel>" (o mesmo, com o campo de escrita da conversa
    # declarado pelo seletor: ele precisa estar na tela e sem o texto), "selector:<sel>" (algum elemento casa; `==`
    # exato, `{username}` resolvido, `@` opcional) ou "selector_band:<sel>" (o elemento casado na faixa de cada
    # `band_guard`). Com `card_guard` preenchido, o elemento casado precisa também estar no cartão da legenda. Prova
    # positiva dispensa o modelo; negativa cai para ele. `None` = sempre julgar pelo modelo, como antes.
    local_proof: str | None = None
    # Item 24.3 (ADR-065): os NOMES dos valores que esta ação ENTREGA às etapas seguintes (`{{saida:<nome>}}`). Desde o
    # 12.4 a etapa só é comprovada com eles preenchidos: o planejador pode ESTREITAR, por etapa, o subconjunto que usa
    # (`CapabilityNode.saidas`); sem escolha, o executor exige todos os que a ação declara. Quem tira o valor continua
    # sendo o executor, do texto do elemento na tela, com a triagem de segredo de sempre.
    saidas: tuple[str, ...] = ()
    # Item 31.41: como reconhecer, na tela, o elemento de cada saída: `nome=seletor` (o elemento casa o seletor) ou
    # `nome~termo` (um sinônimo do rótulo). Sem entrada, valem o nome da saída, o glossário e a forma do valor.
    saidas_relacao: tuple[str, ...] = ()

    def describe(self) -> str:
        """Linha que vai ao planejador. Curta de propósito: o prompt cresce com o catálogo."""
        argumentos = ", ".join(self.bindings + tuple(f"{b}?" for b in self.optional_bindings)) or "sem argumentos"
        efeito = " [EFEITO EXTERNO]" if self.side_effect else ""
        entrega = f" [entrega em `saidas`: {', '.join(self.saidas)}]" if self.saidas else ""
        return f"- {self.key}({argumentos}){efeito}{entrega}: {self.title}"


class UnknownCapability(LookupError):
    """Capability que não existe no catálogo. Vira PERGUNTA ao usuário, nunca morte da execução."""


class MissingBinding(ValueError):
    pass


@dataclass(slots=True)
class CapabilityNode:
    """O que o planejador devolve por etapa. Deliberadamente pequeno."""

    key: str
    capability: str
    depends_on: list[str] = field(default_factory=list)
    bindings: dict[str, str] = field(default_factory=dict)
    for_each: str | None = None
    # Item 24.3: dos valores que a ação declara poder entregar (`Capability.saidas`), os que ESTA etapa entrega.
    saidas: tuple[str, ...] = ()


#: Formas aceitas de `Capability.local_proof` (a semântica está em `taskqueue/proofs.py`).
LOCAL_PROOFS = ("sent_text", "sent_text:", "selector:", "selector_band:", "count_gt:")


def local_proof_error(valor: str | None) -> str | None:
    """Motivo pelo qual uma declaração de `local_proof` é inválida; `None` quando está bem formada. Conferido ao
    montar o catálogo: uma prova mal escrita não pode virar "sempre cai para o modelo" em silêncio."""
    if valor is None or valor == "sent_text":
        return None
    if valor.startswith("count_gt:"):
        # 29.30: `count_gt:<binding>:<seletor>` — o número lido no elemento é MAIOR que o do binding (a contagem de
        # antes). O binding é nome de variável; o seletor da contagem é de UM elemento. 30.60: depois dele, guardas
        # com `&` (cada uma tem de casar na tela, por exemplo o @ do próprio perfil).
        binding, _, seletor = valor[len("count_gt:"):].partition(":")
        if not re.fullmatch(r"[a-z_0-9]+", binding):
            return "count_gt: o binding precisa ser um nome ([a-z_0-9]+)"
        if not seletor.strip():
            return "count_gt: sem seletor"
        if not all(p.strip() for p in seletor.split("&")):
            return "count_gt: guarda vazia depois de `&`"
        return None
    for prefixo in ("sent_text:", "selector:", "selector_band:"):
        if valor.startswith(prefixo):
            corpo = valor[len(prefixo):]
            if prefixo in ("sent_text:", "selector_band:") and "&" in corpo:
                return f"{prefixo} não aceita `&` (o seletor é de UM elemento)"
            return None if all(p.strip() for p in corpo.split("&")) else f"{prefixo} sem seletor"
    return f"prova local desconhecida: {valor!r} (aceitas: {', '.join(LOCAL_PROOFS)})"


def commit_switch_error(cap: Capability) -> str | None:
    """Motivo pelo qual o `commit_switch` de uma ação é inválido; `None` quando está bem formado ou ausente. O argumento
    tem de ser declarado pela ação (senão a guarda nunca liga) e a ação tem de ter `commit_selector` (o toque de efeito
    é estrutural; sem ele não há "antes do toque" a conferir)."""
    if not cap.commit_switch:
        return None
    if not cap.commit_selector:
        return "exige commit_selector"
    for entrada in cap.commit_switch:
        argumento, _, seletor = entrada.partition(":")
        if not argumento.strip() or not seletor.strip():
            return f"entrada inválida: {entrada!r} (use <argumento>:<seletor>)"
        if argumento.strip() not in (*cap.bindings, *cap.optional_bindings):
            return f"o argumento {argumento.strip()!r} não é declarado pela ação"
    return None


def card_control_error(cap: Capability) -> str | None:
    """Motivo pelo qual o `card_control` de uma ação é inválido; `None` quando está bem formado ou ausente. Um seletor
    vazio casaria qualquer elemento (ou nenhum), e um controle sem `card_guard` nunca seria conferido — nos dois casos
    a guarda seria de mentira, e isso aparece na carga, não no primeiro comentário no post errado."""
    if not cap.card_control:
        return None
    if not all(p.strip() for seletor in cap.card_control for p in seletor.split("|")):
        return "seletor vazio"
    if not cap.card_guard:
        return "sem card_guard: sem a legenda a conferir, o toque no controle nunca seria conferido"
    return None


def inherited_bindings_error(cap: Capability) -> str | None:
    """Motivo pelo qual a herança declarada por uma ação é inválida; `None` quando está bem formada ou ausente.

    Só argumento OPCIONAL herda: o obrigatório o planejador preenche, e sem ele a etapa vira pergunta — herdá-lo
    esconderia a pergunta. Texto a escrever nunca herda: o segundo comentário repetiria a intenção (ou as palavras) do
    primeiro, e o texto nasce por perfil. Um nome fora de `optional_bindings` nunca seria herdado, em silêncio."""
    vistos: set[str] = set()
    for nome in cap.inherited_bindings:
        if nome in vistos:
            return f"{nome!r} repetido"
        vistos.add(nome)
        if nome not in cap.optional_bindings:
            return f"{nome!r} não está em optional_bindings (só argumento opcional herda)"
        if nome in (TEXTO, BRIEFING, VERBATIM):
            return f"{nome!r} é texto a escrever: nasce por etapa e por perfil, nunca herdado"
    return None


#: Baldes cuja ação não tem OUTRA pessoa do outro lado: publicar no próprio feed (29.30). A regra de uma conta por
#: alvo (ADR-055) conta contas por ALVO e aqui não há alvo; só estes baldes dispensam `counterparty`.
BALDES_SEM_ALVO: frozenset[str] = frozenset({"posts"})


def counterparty_error(cap: Capability) -> str | None:
    """Motivo pelo qual o alvo declarado de uma ação é inválido; `None` quando está bem formado.

    Toda ação com `limit_bucket` mexe com uma pessoa e precisa dizer QUAL argumento a identifica: sem isso a regra de
    uma conta por alvo (ADR-055) não tem o que conferir e a próxima ação com efeito escaparia dela calada. O nome
    precisa ser um argumento da própria ação — um nome solto nunca teria valor."""
    if cap.counterparty is None:
        if cap.limit_bucket in BALDES_SEM_ALVO:
            return None
        return "ação com limit_bucket precisa declarar counterparty (o argumento que diz quem é o alvo)" \
            if cap.limit_bucket else None
    if cap.counterparty not in (*cap.bindings, *cap.optional_bindings):
        return f"counterparty {cap.counterparty!r} não é argumento da ação (bindings/optional_bindings)"
    return None


def objeto_alvo_error(cap: Capability) -> str | None:
    """Motivo pelo qual o objeto-alvo declarado é inválido; `None` quando está bem formado. Cada nome precisa ser
    argumento da ação, e o texto fica de fora: ele se compara à parte, já com a edição de quem aprovou."""
    for nome in cap.objeto_alvo:
        if nome not in (*cap.bindings, *cap.optional_bindings):
            return f"objeto_alvo {nome!r} não é argumento da ação (bindings/optional_bindings)"
        if nome in ARGUMENTOS_DE_TEXTO:
            return f"objeto_alvo {nome!r} é o texto da ação; o texto se compara à parte"
    if len(set(cap.objeto_alvo)) != len(cap.objeto_alvo):
        return "objeto_alvo com nome repetido"
    # Revisão da fila da suíte 32, item 8: ação com efeito que a plataforma pode fazer sozinha declara sobre o quê age;
    # sem isso a porta não reconhece a repetição. A interna (sem planejador) e a `manual_only` (nunca automática) não.
    if cap.side_effect and not cap.internal and cap.default_policy != "manual_only" and not cap.objeto_alvo:
        return "ação com efeito precisa declarar objeto_alvo (os argumentos que dizem sobre o quê ela age)"
    return None


def saidas_error(cap: Capability) -> str | None:
    """Motivo pelo qual as saídas declaradas por uma ação são inválidas; `None` quando estão bem formadas. Conferido
    na carga, com a mesma regra de nome do `PlanStep`: YAML torto não pode esperar o planejamento para falhar. A coleta
    fica de fora: ela entrega a LISTA pelo `collect_list` e o `for_each`, que é outro mecanismo."""
    if not cap.saidas:
        return None
    if cap.collect:
        return "ação de coleta não declara saidas (a lista vai pelo for_each); use uma ação de leitura"
    for nome in cap.saidas:
        if not SAIDA_NOME_RE.fullmatch(nome):
            return f"nome de saída inválido: {nome!r} (use {SAIDA_NOME_RE.pattern})"
    if len(set(cap.saidas)) != len(cap.saidas):
        return "nomes de saída repetidos"
    for entrada in cap.saidas_relacao:                 # 31.41: `nome=seletor` ou `nome~termo`, de uma saída declarada
        nome = re.split(r"[=~]", entrada, maxsplit=1)[0].strip()
        if not re.search(r"[=~]", entrada) or nome not in cap.saidas:
            return f"saidas_relacao inválida: {entrada!r} (use <saída>=<seletor> ou <saída>~<termo>)"
    return None


def preparo_error(cap: Capability, por_chave: Mapping[str, Capability]) -> str | None:
    """Motivo pelo qual o `preparo` de uma ação é inválido; `None` quando está bem formado ou ausente. Uma chave
    inexistente ou oferecida ao planejador viraria etapa duplicada ou pergunta no meio da execução; um argumento
    obrigatório da interna que a ação não tem faria toda execução parar em `needs_input`."""
    for chave in cap.preparo:
        alvo = por_chave.get(chave)
        if alvo is None:
            return f"ação {chave!r} não existe neste catálogo"
        if not alvo.internal:
            return f"{chave} não é `internal` (o planejador já a oferece)"
        fora = [b for b in alvo.bindings if b not in (*cap.bindings, *cap.optional_bindings)]
        if fora:
            return f"{chave} exige {', '.join(fora)}, que {cap.key} não declara"
    return None


class CapabilityCatalog:
    def __init__(self, package: str, capabilities: list[Capability], contract_version: int = 1):
        self.package = package
        # Versão do contrato das ações, declarada no arquivo do app (`contract_version`). Quem monta o catálogo em
        # código (testes, dublês) fica com 1, que é a versão que o registro de capabilities entende hoje.
        self.contract_version = contract_version
        for c in capabilities:
            erro = local_proof_error(c.local_proof)
            if erro:
                raise ValueError(f"{package}: {c.key}.local_proof — {erro}")
            erro = card_control_error(c)
            if erro:
                raise ValueError(f"{package}: {c.key}.card_control — {erro}")
            erro = commit_switch_error(c)
            if erro:
                raise ValueError(f"{package}: {c.key}.commit_switch — {erro}")
            erro = inherited_bindings_error(c)
            if erro:
                raise ValueError(f"{package}: {c.key}.inherited_bindings — {erro}")
            erro = counterparty_error(c)
            if erro:
                raise ValueError(f"{package}: {c.key}.counterparty — {erro}")
            erro = objeto_alvo_error(c)
            if erro:
                raise ValueError(f"{package}: {c.key}.objeto_alvo — {erro}")
            erro = saidas_error(c)
            if erro:
                raise ValueError(f"{package}: {c.key}.saidas — {erro}")
        self._por_chave = {c.key: c for c in capabilities}
        for c in capabilities:
            erro = preparo_error(c, self._por_chave)
            if erro:
                raise ValueError(f"{package}: {c.key}.preparo — {erro}")

    @property
    def capabilities(self) -> list[Capability]:
        """Todas, na ordem declarada, inclusive as internas (`offered` é o recorte do planejador)."""
        return list(self._por_chave.values())

    def get(self, key: str) -> Capability:
        cap = self._por_chave.get((key or "").strip().upper())
        if cap is None:
            raise UnknownCapability(key)
        return cap

    def has(self, key: str) -> bool:
        return (key or "").strip().upper() in self._por_chave

    @property
    def offered(self) -> list[Capability]:
        """O que o planejador pode usar. O que é resolvido por código (login, verificação) fica de fora."""
        return [c for c in self._por_chave.values() if not c.internal]

    def prompt_block(self) -> str:
        return "\n".join(c.describe() for c in self.offered)

    # ------------------------------------------------------------------ composição
    def build_step(self, node: CapabilityNode) -> PlanStep:
        """Monta a etapa a partir do catálogo. O texto é do backend; do modelo vêm só os argumentos."""
        cap = self.get(node.capability)
        valores = {k: v for k, v in node.bindings.items() if v is not None}
        fora = [n for n in node.saidas if n not in cap.saidas]
        if fora:
            # Entregar o que a ação não declara é inventar leitura: o ator nunca é mandado ler e o plano seguiria sem o
            # valor. Vira pergunta, como a ação que não existe.
            raise MissingBinding(f"{cap.key} não entrega {', '.join(fora)}"
                                 + (f" (entrega: {', '.join(cap.saidas)})" if cap.saidas else " (não entrega valor)"))
        faltando = [b for b in cap.bindings if not (valores.get(b) or "").strip()]
        if cap.needs_draft and not (valores.get(BRIEFING) or "").strip() and not (valores.get(TEXTO) or "").strip():
            # Etapa que escreve precisa de UM dos dois: a intenção (para cada perfil escrever a sua) ou o texto
            # exato. Nenhum dos dois é informação faltando de verdade, e vira pergunta — não plano torto.
            faltando.append(f"{BRIEFING} ou {TEXTO}")
        if faltando:
            raise MissingBinding(f"{cap.key} exige {', '.join(faltando)}")
        # O texto só fica congelado no plano quando o comando pediu as MESMAS palavras para todo mundo. Fora isso,
        # ele nasce por perfil, na hora, com a persona de cada um — e as guardas que dependem dele só podem ser
        # montadas quando esse texto existir (ver `state._draft_gate`). Congelar aqui era o que fazia oito contas
        # publicarem, byte a byte, a mesma frase.
        texto_fixo = bool((valores.get(TEXTO) or "").strip()) and (not cap.needs_draft or _e_verbatim(valores))
        # Guarda que cita argumento OPCIONAL sem valor não se aplica a esta etapa. Ficaria com a variável crua
        # (`_aplicar` e `resolve_templates` só trocam o que conhecem) e exigiria o texto "{caption_contains}" na
        # tela: a curtida por posição nunca mais passaria.
        ausentes = {b for b in cap.optional_bindings if not str(valores.get(b) or "").strip()}
        # `{item}` só é resolvido na expansão do for_each; aqui ele segue como variável, de propósito.
        preencher = (lambda texto: _aplicar(texto, valores))
        guardas = (lambda brutas: [preencher(g) for g in brutas
                                   if (texto_fixo or TEXTO not in _variaveis(g)) and not (_variaveis(g) & ausentes)])
        return PlanStep(
            key=node.key, title=preencher(cap.title), goal=preencher(cap.goal),
            depends_on=list(node.depends_on), side_effect=cap.side_effect,
            commit_guard=guardas(cap.commit_guard),
            precondition=preencher(cap.precondition) if cap.precondition else None,
            postcondition=Postcondition(kind=cap.post_kind, value=preencher(cap.post_value),  # type: ignore[arg-type]
                                        description=preencher(cap.post_description)),
            timeout_s=cap.timeout_s, max_attempts=1 if cap.side_effect else cap.max_attempts,
            capability=cap.key, commit_selector=cap.commit_selector,
            band_guard=guardas(cap.band_guard), bindings=dict(valores),
            for_each=node.for_each, variables={}, saidas=list(node.saidas))


def compose(catalog: CapabilityCatalog, nodes: list[CapabilityNode]) -> tuple[list[PlanStep], list[MissingInfo]]:
    """Monta o plano a partir das ações escolhidas.

    Ação desconhecida ou argumento faltando NÃO derruba a execução: vira pergunta ao usuário, que é o que o motor
    já sabe tratar (`needs_input`). Um plano meio montado seria pior que nenhum. Antes de montar, cada etapa recebe
    os argumentos que a ação declara herdar das anteriores (`herdar_argumentos`).
    """
    steps: list[PlanStep] = []
    missing: list[MissingInfo] = []
    for node in herdar_argumentos(catalog, com_preparo(catalog, nodes)):
        step, falta = montar_etapa(catalog, node)
        if step is not None:
            steps.append(step)
        if falta is not None:
            missing.append(falta)
    return steps, missing


def com_preparo(catalog: CapabilityCatalog, nodes: list[CapabilityNode]) -> list[CapabilityNode]:
    """Os nós com as ações de `preparo` inseridas imediatamente antes de quem as declara (29.30), com os argumentos
    que a interna declara tirados do nó dela. A ação seguinte passa a depender da interna. Ação desconhecida passa
    intacta (o `compose` a transforma em pergunta); argumento que falte na interna vira a pergunta dela, antes de
    qualquer efeito."""
    saida: list[CapabilityNode] = []
    for node in nodes:
        try:
            cap = catalog.get(node.capability)
        except UnknownCapability:
            saida.append(node)
            continue
        antes: list[str] = []
        for chave in cap.preparo:
            interna = catalog.get(chave)
            chave_do_no = f"{node.key}__{chave.lower()}"
            saida.append(CapabilityNode(
                key=chave_do_no, capability=interna.key, depends_on=list(node.depends_on), for_each=node.for_each,
                bindings={b: node.bindings[b] for b in (*interna.bindings, *interna.optional_bindings)
                          if node.bindings.get(b) is not None}))
            antes.append(chave_do_no)
        saida.append(replace(node, depends_on=[*node.depends_on, *antes]) if antes else node)
    return saida


def montar_etapa(catalog: CapabilityCatalog, node: CapabilityNode) -> tuple[PlanStep | None, MissingInfo | None]:
    """Uma etapa do catálogo, ou a PERGUNTA que ela vira. O `compose` e o planejamento entre apps (item 24.1, que
    monta cada etapa pelo catálogo do app dela) usam a mesma, para a pergunta ser a mesma nos dois caminhos."""
    try:
        return catalog.build_step(node), None
    except UnknownCapability:
        disponiveis = ", ".join(c.key for c in catalog.offered)
        return None, MissingInfo(
            field="capability",
            question=(f"A ação '{node.capability}' não existe para este aplicativo. "
                      f"As disponíveis são: {disponiveis}. Como devo fazer isso?"))
    except MissingBinding as exc:
        return None, MissingInfo(field=node.capability.lower(),
                                 question=f"Falta informação para a etapa '{node.key}': {exc}.")


def herdar_argumentos(catalog: CapabilityCatalog, nodes: list[CapabilityNode]) -> list[CapabilityNode]:
    """Os nós com os argumentos que cada ação declara herdar (`inherited_bindings`) preenchidos pelo plano.

    Um argumento herdável sem valor na etapa recebe o valor EFETIVO (já herdado, em cadeia) da etapa anterior mais
    próxima que declara esse argumento, obrigatório ou opcional. "Anterior" é a ordem da lista, que é a ordem em que o
    objetivo executa (`steps.seq`). Se essa etapa o deixou vazio — outra publicação aberta por posição —, nada é
    herdado: ela começou outro alvo. Etapa que não declara o argumento (abrir um perfil) não corta a cadeia.

    O valor de dentro de um bloco `for_each` não sai dele: `{item}` só é resolvido nas cópias do bloco, e fora dele
    viraria o texto literal "{item}" numa guarda. O que a etapa trouxe sempre vence; ação desconhecida passa intacta
    (o `compose` a transforma em pergunta). Herdar só acrescenta exigência à etapa (legenda na tela, toque no cartão
    dela): o pior caso de uma herança indevida é a etapa parar, nunca agir no alvo errado."""
    anteriores: dict[str, tuple[str, str | None]] = {}      # argumento → (valor, for_each) do último que o declarou
    saida: list[CapabilityNode] = []
    for node in nodes:
        if not catalog.has(node.capability):
            saida.append(node)
            continue
        cap = catalog.get(node.capability)
        valores = dict(node.bindings)
        for nome in cap.inherited_bindings:
            if str(valores.get(nome) or "").strip():
                continue
            valor, bloco = anteriores.get(nome, ("", None))
            if valor.strip() and bloco in (None, node.for_each):
                valores[nome] = valor
        for nome in (*cap.bindings, *cap.optional_bindings):
            anteriores[nome] = (str(valores.get(nome) or ""), node.for_each)
        saida.append(node if valores == node.bindings else replace(node, bindings=valores))
    return saida


def _variaveis(texto: str) -> set[str]:
    return set(_VARIAVEL.findall(texto or ""))


def _e_verbatim(valores: dict[str, str]) -> bool:
    return str(valores.get(VERBATIM) or "").strip().lower() in _SIM


def texto_a_gerar(bindings: dict[str, Any] | None) -> str | None:
    """O briefing desta etapa, quando o texto ainda precisa ser escrito por perfil; `None` quando já está fechado.

    Regra (decisão do usuário): briefing é o padrão. Um `content` que veio do comando SEM `content_verbatim` é
    tratado como intenção, não como as palavras finais — foi exatamente assim que "o texto pode ser X" virou a
    mesma frase em oito contas.
    """
    valores = {k: v for k, v in (bindings or {}).items() if v is not None}
    if _e_verbatim(valores):
        return None
    briefing = str(valores.get(BRIEFING) or "").strip()
    return briefing or str(valores.get(TEXTO) or "").strip() or None


def normalizar_alvo(valor: object) -> str | None:
    """Uma pessoa sempre no mesmo formato: `@nome` em minúsculas — o mesmo de `SocialService.record_interaction`.

    A porta de frota recebia o argumento CRU do plano (`@Ana`, `ana`) e o histórico guarda `@ana`: a contagem por alvo
    nunca casava quando a caixa ou a arroba diferiam. Argumento ainda com variável (`{item}` de um bloco que não foi
    expandido) não é alvo de ninguém: `None`."""
    texto = str(valor or "").strip()
    if not texto or _VARIAVEL.search(texto):
        return None
    limpo = texto.lower().lstrip("@").strip()
    return f"@{limpo}" if limpo else None


def alvo_da_acao(cap: Capability | None, bindings: Mapping[str, object] | None) -> str | None:
    """O valor CRU do argumento que a ação declara como a pessoa do outro lado (`Capability.counterparty`).

    Ação sem declaração (app sem limite por alvo) cai no palpite antigo, `username` e depois `target`, para nada
    mudar fora do que o catálogo declara."""
    valores = bindings or {}
    nomes = (cap.counterparty,) if cap is not None and cap.counterparty else ("username", "target")
    for nome in nomes:
        valor = str(valores.get(nome) or "").strip()
        if valor:
            return valor
    return None


def objeto_da_acao(cap: Capability | None, bindings: Mapping[str, object] | None) -> dict[str, str] | None:
    """30.64: o objeto do efeito desta etapa, pelos argumentos que a ação declara em `objeto_alvo`; o que for o
    `counterparty` sai normalizado (`@nome`), como na porta de frota. `None` quando não dá para dizer: ação sem
    catálogo, sem declaração, ou com argumento declarado ainda por resolver (`{item}`, `{{saida:…}}`). Quem recebe
    `None` falha fechado: não reaproveita aprovação nem reconhece o item como repetido sem olhar de novo."""
    if cap is None or not cap.objeto_alvo:
        return None
    valores = bindings or {}
    objeto: dict[str, str] = {}
    for nome in cap.objeto_alvo:
        bruto = valores.get(nome)
        texto = "" if bruto is None else str(bruto).strip()
        if "{" in texto:
            return None
        objeto[nome] = (normalizar_alvo(texto) or "") if nome == cap.counterparty else texto
    return objeto


def contraparte(cap: Capability | None, bindings: Mapping[str, object] | None) -> str | None:
    """A pessoa do outro lado desta etapa, normalizada (`@nome`): a mesma chave na porta de frota, na aprovação e no
    histórico. `None` quando o plano não disse (ou disse só com variável ainda não resolvida)."""
    return normalizar_alvo(alvo_da_acao(cap, bindings))


def guardas_do_cartao(modelos: Iterable[str], bindings: Mapping[str, object] | None) -> tuple[str, ...]:
    """Os textos de `card_guard` desta etapa, com os argumentos dela. Um modelo que cita argumento sem valor fica de
    fora: é a publicação por posição, e ela segue como antes.

    Resolvido na hora de usar, pelos `bindings` gravados na etapa (já concretos: `{item}` de uma cópia de `for_each` é
    trocado na materialização). O valor entra LITERAL: uma legenda com `|` ou `&` nunca vira operador de seletor."""
    valores = {k: str(v) for k, v in (bindings or {}).items() if v is not None and str(v).strip()}
    textos = (_aplicar(m, valores) for m in modelos if _variaveis(m) <= set(valores))
    return tuple(t for t in textos if t.strip())


def _aplicar(texto: str, valores: dict[str, str]) -> str:
    """Substitui só as variáveis conhecidas; `{item}`, `{instance_id}` e afins continuam para o executor resolver."""
    return _VARIAVEL.sub(lambda m: valores.get(m.group(1), m.group(0)), texto or "")


def atualizar_pos_condicoes(steps: Iterable[PlanStep], package: str | None) -> list[str]:
    """31.50 (a): a etapa de catálogo de um plano SALVO (fluxo) passa a provar com a pós-condição ATUAL do catálogo.

    OBSERVADO (r-20261004195451-7d3527, 04/10): o OPEN_MAIL_INBOX do fluxo salvo antes do #278 trouxe o
    `model_judged` antigo, e a etapa foi pelo ator e pelo juiz (13 chamadas) em vez do `element_present` do catálogo.
    O fluxo congela o plano, e o catálogo é a fonte da prova. Só a etapa do app do plano (`app_id` nulo) cuja ação o
    catálogo ainda tem; o resto do plano não muda. Devolve as chaves das etapas trocadas.
    """
    catalog = load_catalog(package)
    if catalog is None:
        return []
    trocadas = []
    for step in steps:
        if step.app_id is not None or not step.capability or not catalog.has(step.capability):
            continue
        cap = catalog.get(step.capability)
        valores = {k: v for k, v in step.bindings.items() if v is not None}
        atual = Postcondition(kind=cap.post_kind, value=_aplicar(cap.post_value, valores),  # type: ignore[arg-type]
                              description=_aplicar(cap.post_description, valores))
        if _variaveis(atual.value) - _variaveis(step.postcondition.value):
            # Revisão do #313: a pós-condição nova cita um argumento que o fluxo salvo não tem. Trocar daria uma
            # prova com `{…}` cru; fica a salva, e a falha (se houver) é honesta.
            continue
        if (atual.kind, atual.value) != (step.postcondition.kind, step.postcondition.value):
            step.postcondition = atual
            trocadas.append(step.key)
    return trocadas


def capability_of(package: str | None, key: str | None) -> Capability | None:
    """A capability desta etapa, quando o app tem catálogo. Fora disso, `None` — e tudo segue como antes."""
    catalog = load_catalog(package)
    if catalog is None or not key or not catalog.has(key):
        return None
    return catalog.get(key)


def load_catalog(package: str | None) -> CapabilityCatalog | None:
    """Catálogo do app, quando existe. Sem catálogo, o planejamento livre continua valendo — sem exceção nenhuma.

    Era um `if package == "com.instagram.android"` aqui dentro: o núcleo de planejamento decidia por um app
    específico, e um segundo aplicativo só existiria editando esta função. Agora a pergunta vai ao registro
    (`planning/catalog`), onde qualquer app se declara — ver `register()`.
    """
    from .catalog import get

    return get(package)


#: Por que uma etapa com efeito foi recusada pela regra do item 13.2. Vocabulário FECHADO: vai no evento
#: `plan.refused` (RA-7) e só cresce no código.
MotivoForaDoCatalogo = Literal["sem_acao_do_catalogo", "acao_de_outro_catalogo"]


def efeito_fora_do_catalogo(side_effect: bool, capability: str | None,
                            package: str | None) -> MotivoForaDoCatalogo | None:
    """A regra do item 13.2 num lugar só: etapa com EFEITO externo, num app que TEM catálogo, sem uma ação desse
    catálogo, passaria por fora de política, aprovação, limite e coordenação de frota. `None` = a regra não recusa.

    Vale no despacho (`AppState._policy_gate`) e, desde o RA-7, no planejamento (`RunService._plan`), antes de
    qualquer etapa existir: o plano que pede enviar e-mail no Outlook (catálogo só de leitura) não gasta uma decisão
    nos preparativos. App sem catálogo (o QA Messenger) segue livre, com ou sem efeito, como sempre."""
    if not side_effect or not package or load_catalog(package) is None:
        return None
    if capability and capability_of(package, capability) is not None:
        return None
    return "acao_de_outro_catalogo" if capability else "sem_acao_do_catalogo"


# ---------------------------------------------------------------------------------------------------- carga (dado)
#: Onde mora o conhecimento de cada app, como dado: `app/conhecimento/apps/<pacote>/`. Relativo a `app/`, e não ao
#: diretório de trabalho: o backend já rodou a partir da raiz do repositório e de `backend/`.
CONHECIMENTO_DE_APPS = Path(__file__).resolve().parents[1] / "conhecimento" / "apps"

#: Versões de contrato que o carregador aceita. É a `LEGACY_CONTRACT_VERSION` do registro de capabilities
#: (`modules/capabilities/infrastructure/catalog_registry.py`), que ainda compara com a constante e não lê a versão
#: do catálogo: aceitar 2 aqui seria subir a versão no arquivo sem nada mudar de fato. Subir exige ligar o registro.
VERSOES_DE_CONTRATO = (1,)

RISCOS = ("low", "medium", "high")
#: Os tipos de pós-condição que o executor sabe comprovar: os mesmos do `Postcondition` (um só lugar).
TIPOS_DE_POS = get_args(Postcondition.model_fields["kind"].annotation)

_RAIZ = ("app", "contract_version", "acoes")
_CHAVE = re.compile(r"^[A-Z][A-Z0-9_]*$")
#: Pacote Android (`com.exemplo.app`). O pacote vira componente de caminho: sem esta forma, `..` sairia da pasta.
_PACOTE_ANDROID = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$")


class CatalogoInvalido(ValueError):
    """O arquivo do catálogo não se sustenta: recusa na carga, antes de planejar a primeira etapa."""


def _texto(valor: object, onde: str) -> str:
    if not isinstance(valor, str):
        raise CatalogoInvalido(f"{onde}: esperava texto, veio {type(valor).__name__}")
    return valor


def _texto_ou_nada(valor: object, onde: str) -> str | None:
    return None if valor is None else _texto(valor, onde)


def _logico(valor: object, onde: str) -> bool:
    if not isinstance(valor, bool):
        raise CatalogoInvalido(f"{onde}: esperava true/false, veio {type(valor).__name__}")
    return valor


def _inteiro(valor: object, onde: str) -> int:
    # `bool` é subclasse de `int` em Python, e o YAML 1.1 lê `yes`/`on` como verdadeiro: `timeout_s: yes` viraria 1.
    if isinstance(valor, bool) or not isinstance(valor, int):
        raise CatalogoInvalido(f"{onde}: esperava um inteiro, veio {type(valor).__name__}")
    return valor


def _inteiro_ou_nada(valor: object, onde: str) -> int | None:
    return None if valor is None else _inteiro(valor, onde)


def _textos(valor: object, onde: str) -> tuple[str, ...]:
    if not isinstance(valor, list):
        raise CatalogoInvalido(f"{onde}: esperava uma lista de textos, veio {type(valor).__name__}")
    return tuple(_texto(v, f"{onde}[{i}]") for i, v in enumerate(valor))


#: Como ler cada tipo de campo de `Capability` (pela anotação, que é texto por causa do `__future__`).
_LEITORES: dict[str, Callable[[object, str], object]] = {
    "str": _texto, "str | None": _texto_ou_nada, "bool": _logico, "int": _inteiro, "int | None": _inteiro_ou_nada,
    "tuple[str, ...]": _textos,
}


def _campos_da_acao() -> dict[str, tuple[Callable[[object, str], object], bool]]:
    """Campo → (leitor, obrigatório), tirado da própria dataclass: o arquivo não pode divergir dela. Um campo novo de
    tipo que o carregador não sabe ler quebra na importação, não na primeira execução que o usar."""
    campos: dict[str, tuple[Callable[[object, str], object], bool]] = {}
    for f in fields(Capability):
        leitor = _LEITORES.get(str(f.type))
        if leitor is None:
            raise TypeError(f"Capability.{f.name}: tipo {f.type} sem leitor em `_LEITORES`")
        campos[f.name] = (leitor, f.default is MISSING and f.default_factory is MISSING)
    return campos


_CAMPOS_DA_ACAO = _campos_da_acao()


class _SemChaveRepetida(yaml.SafeLoader):
    """`yaml.safe_load` fica com a ÚLTIMA de duas chaves iguais, calado. Num arquivo editado à mão, um segundo
    `commit_selector` na mesma ação trocaria o alvo do efeito sem ninguém ver."""


def _mapa_sem_repeticao(loader: _SemChaveRepetida, no: yaml.MappingNode) -> dict[object, object]:
    vistas: set[object] = set()
    for no_da_chave, _ in no.value:
        chave = loader.construct_object(no_da_chave)
        if chave in vistas:
            raise CatalogoInvalido(f"chave repetida {chave!r} na linha {no_da_chave.start_mark.line + 1}")
        vistas.add(chave)
    return loader.construct_mapping(no, deep=True)


_SemChaveRepetida.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapa_sem_repeticao)


def _acao(bruta: object, onde: str) -> Capability:
    if not isinstance(bruta, dict):
        raise CatalogoInvalido(f"{onde}: esperava um mapa com os campos da ação")
    desconhecidos = sorted(str(k) for k in bruta if k not in _CAMPOS_DA_ACAO)
    if desconhecidos:
        raise CatalogoInvalido(f"{onde}: campo desconhecido {', '.join(desconhecidos)} "
                               f"(aceitos: {', '.join(_CAMPOS_DA_ACAO)})")
    faltando = [nome for nome, (_, obrigatorio) in _CAMPOS_DA_ACAO.items() if obrigatorio and nome not in bruta]
    if faltando:
        raise CatalogoInvalido(f"{onde}: falta {', '.join(faltando)}")
    valores = {str(nome): _CAMPOS_DA_ACAO[str(nome)][0](valor, f"{onde}.{nome}") for nome, valor in bruta.items()}
    cap = Capability(**valores)  # type: ignore[arg-type]  # cada valor passou pelo leitor do tipo do campo
    # Além do tipo, o que o motor só descobriria no meio de uma execução: chave que `get` nunca acharia (ele busca em
    # maiúsculas), risco e política fora do vocabulário, pós-condição que ninguém sabe comprovar, `item_key` que não
    # compila.
    if not _CHAVE.match(cap.key):
        raise CatalogoInvalido(f"{onde}.key: {cap.key!r} precisa ser MAIÚSCULAS_COM_SUBLINHADO")
    if cap.risk not in RISCOS:
        raise CatalogoInvalido(f"{onde}.risk: {cap.risk!r} fora de {', '.join(RISCOS)}")
    if cap.default_policy not in POLICIES:
        raise CatalogoInvalido(f"{onde}.default_policy: {cap.default_policy!r} fora de {', '.join(POLICIES)}")
    if cap.post_kind not in TIPOS_DE_POS:
        raise CatalogoInvalido(f"{onde}.post_kind: {cap.post_kind!r} fora de {', '.join(TIPOS_DE_POS)}")
    if cap.item_key is not None:
        try:
            re.compile(cap.item_key)
        except re.error as exc:
            raise CatalogoInvalido(f"{onde}.item_key: expressão regular inválida ({exc})") from exc
    return cap


def catalogo_de_dados(dados: object, onde: str = "o catálogo") -> CapabilityCatalog:
    """Valida e monta o catálogo a partir do que o YAML trouxe. Recusa campo desconhecido (na raiz e em cada ação),
    campo obrigatório ausente, tipo errado, `app` ausente, chave repetida e versão de contrato não entendida."""
    if not isinstance(dados, dict):
        raise CatalogoInvalido(f"{onde}: esperava um mapa com `app`, `contract_version` e `acoes`")
    desconhecidos = sorted(str(k) for k in dados if k not in _RAIZ)
    if desconhecidos:
        raise CatalogoInvalido(f"{onde}: campo desconhecido {', '.join(desconhecidos)} (aceitos: {', '.join(_RAIZ)})")
    app = dados.get("app")
    if not isinstance(app, str) or not app.strip():
        raise CatalogoInvalido(f"{onde}: falta `app` (o pacote Android)")
    versao = _inteiro(dados.get("contract_version"), f"{onde}.contract_version")
    if versao not in VERSOES_DE_CONTRATO:
        raise CatalogoInvalido(f"{onde}.contract_version: {versao} não é entendida (aceitas: "
                               f"{', '.join(map(str, VERSOES_DE_CONTRATO))}); subir a versão exige ligar o registro "
                               "de capabilities a ela")
    brutas = dados.get("acoes")
    if not isinstance(brutas, list) or not brutas:
        raise CatalogoInvalido(f"{onde}: `acoes` precisa ser uma lista com ao menos uma ação")
    acoes = [_acao(b, f"{onde}.acoes[{i}]") for i, b in enumerate(brutas)]
    # `CapabilityCatalog` guarda por chave: uma ação repetida sumiria calada, ficando a última.
    repetidas = sorted(k for k, n in Counter(c.key for c in acoes).items() if n > 1)
    if repetidas:
        raise CatalogoInvalido(f"{onde}: ação repetida {', '.join(repetidas)}")
    try:
        return CapabilityCatalog(app.strip(), acoes, contract_version=versao)
    except ValueError as exc:                   # prova local mal escrita: mesma recusa, com o nome do arquivo
        raise CatalogoInvalido(f"{onde}: {exc}") from exc


def carregar_catalogo(caminho: Path) -> CapabilityCatalog:
    """Lê e valida um `catalogo.yaml`. Um arquivo errado falha aqui, na carga, e não no meio de uma execução."""
    try:
        dados = yaml.load(caminho.read_text(encoding="utf-8"), Loader=_SemChaveRepetida)  # noqa: S506 (SafeLoader)
    except yaml.YAMLError as exc:
        raise CatalogoInvalido(f"{caminho}: YAML inválido ({exc})") from exc
    except CatalogoInvalido as exc:
        raise CatalogoInvalido(f"{caminho}: {exc}") from exc
    return catalogo_de_dados(dados, str(caminho))


@functools.cache
def catalogo_do_pacote(pacote: str) -> CapabilityCatalog | None:
    """O catálogo declarado em `app/conhecimento/apps/<pacote>/catalogo.yaml`; `None` quando o app não tem arquivo.

    Cacheado: o arquivo é lido uma vez por processo, como era o módulo Python. Sem arquivo não é erro (o app planeja
    no caminho livre); arquivo presente e inválido É erro, e o `app` do arquivo precisa ser o da pasta.
    """
    if not _PACOTE_ANDROID.match(pacote or ""):
        return None
    caminho = CONHECIMENTO_DE_APPS / pacote / "catalogo.yaml"
    if not caminho.is_file():
        return None
    catalogo = carregar_catalogo(caminho)
    if catalogo.package != pacote:
        raise CatalogoInvalido(f"{caminho}: `app` é {catalogo.package!r}, mas a pasta é {pacote!r}")
    return catalogo
