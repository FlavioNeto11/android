"""Para quem e onde uma execução acontece: `resolver_alvos` (segunda evolução, onda C; design persona-e-parque §7.4).

A hierarquia é Servidor → Aparelho → Persona(s), com vínculo N:N (migração 051). "Peça para o André fazer X" diz a
PESSOA; o sistema escolhe o aparelho. "No android-03" diz o APARELHO; o sistema descobre a persona. A pessoa pode
dizer as duas coisas pela interface (seleção estruturada) e pelo texto do comando — e as duas podem discordar.

Precedência (design §7.7), em ordem:

1. **interface** manda: `targets` explícitos (o eco da prévia), `profile_ids` (com `instance_ids` = INTERSEÇÃO) ou
   só `instance_ids` (o caminho direto por aparelho, de sempre);
2. **texto** só ESTREITA o que a interface deu; quando contradiz (fala de quem não está na seleção), vira PERGUNTA,
   nunca escolha silenciosa. Com a interface vazia, o texto decide — e a origem `texto` obriga a prévia (§7.6);
3. **vínculos** preenchem o aparelho quando só a persona foi dada: sessão pronta NAQUELE aparelho primeiro, depois
   o principal;
4. **balanceamento** desempata entre aparelhos igualmente bons (o que já prefere aparelho ligado).

Pura de propósito — sem banco, sem runtime, sem IA — para ser testada em tabela como `balanceamento`. O serviço
monta o `Mundo` (vínculos, aptos, sessões prontas) e injeta o desempate: esta camada não enxerga `app.taskqueue`.
Recusas (sem vínculo, interseção vazia, o mesmo aparelho duas vezes) levantam `RecusaDeAlvo` com código e status
HTTP; ambiguidades voltam como `Pergunta`.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from typing import Literal

Origem = Literal["ui", "texto", "vinculo", "balanceamento"]
Politica = Literal["one", "primary", "all"]


@dataclass(frozen=True, slots=True)
class AlvoResolvido:
    """Um aparelho da execução, a persona que age nele e DE ONDE veio a escolha (a prévia mostra a origem)."""

    instance_id: str
    profile_id: str | None
    app_id: str | None = None
    origem: Origem = "ui"

    def as_dict(self) -> dict[str, str | None]:
        return {"instance_id": self.instance_id, "profile_id": self.profile_id, "app_id": self.app_id,
                "origem": self.origem}


@dataclass(frozen=True, slots=True)
class Pergunta:
    """O que falta a pessoa decidir. Mesmo formato das perguntas da RESOLVE (`question`, `field`, `options`), para o
    painel ter uma forma só de pergunta."""

    code: str
    question: str
    field: Literal["profile_id", "instance_id"]
    options: tuple[str, ...] = ()
    instance_id: str | None = None
    profile_id: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {"code": self.code, "question": self.question, "field": self.field, "options": list(self.options),
                "instance_id": self.instance_id, "profile_id": self.profile_id}


class RecusaDeAlvo(Exception):
    """O pedido não tem alvo possível (não é dúvida: é impossível). `status` é o HTTP que a rota devolve."""

    def __init__(self, code: str, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


@dataclass(frozen=True, slots=True)
class AlvoPedido:
    """`RunTarget` da interface: a persona e, opcionalmente, os aparelhos dela e o app da conta que a tarefa usa."""

    profile_id: str
    instance_ids: tuple[str, ...] = ()
    app_id: str | None = None


@dataclass(frozen=True, slots=True)
class PedidoDeAlvos:
    instance_ids: tuple[str, ...] = ()
    profile_ids: tuple[str, ...] = ()
    targets: tuple[AlvoPedido, ...] = ()
    device_policy: Politica = "one"
    #: O app que o comando usa, quando se sabe (a habilidade casada declara); desempata personas num aparelho.
    app_id: str | None = None


@dataclass(frozen=True, slots=True)
class MencaoNoTexto:
    """Um destino que o texto do comando citou: o trecho e os ids que ele pode ser (mais de um = homônimos)."""

    trecho: str
    ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DicasDoTexto:
    personas: tuple[MencaoNoTexto, ...] = ()
    aparelhos: tuple[MencaoNoTexto, ...] = ()

    @property
    def vazias(self) -> bool:
        return not self.personas and not self.aparelhos


@dataclass(frozen=True, slots=True)
class Vinculo:
    """Um vínculo ativo persona × aparelho. `apps` = os apps que ele serve: o `app_id` do vínculo, ou — no vínculo
    sem app — os apps das contas da persona (a mesma regra de `SocialRepository.profiles_of_instance`)."""

    profile_id: str
    instance_id: str
    apps: frozenset[str] = frozenset()
    is_primary: bool = False


def _primeiro(candidatos: Sequence[str]) -> str | None:
    return candidatos[0] if candidatos else None


@dataclass(frozen=True, slots=True)
class Mundo:
    vinculos: tuple[Vinculo, ...] = ()
    #: Aparelhos que podem trabalhar agora ou logo: conhecidos, fora da loja, servidor disponível, app não sabidamente
    #: ausente. Quem não está aqui ainda pode ser o principal (a recusa explicada fica com o pré-voo).
    aptos: frozenset[str] = frozenset()
    #: `(persona, aparelho)` com a sessão da conta do app `session_ready` NAQUELE aparelho (`account_sessions`).
    sessoes_prontas: frozenset[tuple[str, str]] = frozenset()
    #: O desempate entre aparelhos igualmente bons (`balanceamento.distribuir(1, …)`, injetado pelo serviço).
    desempatar: Callable[[Sequence[str]], str | None] = _primeiro
    #: Nome para as perguntas (`profile_id` → nome da persona); sem ele, o id.
    nomes: tuple[tuple[str, str], ...] = ()

    def nome(self, profile_id: str) -> str:
        return dict(self.nomes).get(profile_id, profile_id)

    def aparelhos_de(self, profile_id: str) -> list[str]:
        """Os aparelhos da persona, o principal primeiro, sem repetir (um par pode ter um vínculo por app)."""
        linhas = sorted((v for v in self.vinculos if v.profile_id == profile_id), key=lambda v: not v.is_primary)
        return list(dict.fromkeys(v.instance_id for v in linhas))

    def principal(self, profile_id: str) -> str | None:
        return next((v.instance_id for v in self.vinculos if v.profile_id == profile_id and v.is_primary),
                    _primeiro(self.aparelhos_de(profile_id)))

    def personas_em(self, instance_id: str) -> list[str]:
        return list(dict.fromkeys(v.profile_id for v in self.vinculos if v.instance_id == instance_id))

    def serve(self, profile_id: str, instance_id: str, app_id: str) -> bool:
        return any(v.profile_id == profile_id and v.instance_id == instance_id and app_id in v.apps
                   for v in self.vinculos)


@dataclass(frozen=True, slots=True)
class Resolucao:
    alvos: tuple[AlvoResolvido, ...] = ()
    perguntas: tuple[Pergunta, ...] = ()

    @property
    def instance_ids(self) -> list[str]:
        return [a.instance_id for a in self.alvos]


@dataclass(slots=True)
class _Estado:
    alvos: list[AlvoResolvido] = field(default_factory=list)
    perguntas: list[Pergunta] = field(default_factory=list)


def _lista(ids: Iterable[str]) -> str:
    return ", ".join(ids)


# ================================================================== persona dentro de um aparelho
def _persona_no_aparelho(mundo: Mundo, instance_id: str, app_id: str | None,
                         estado: _Estado) -> str | None | Literal[False]:
    """Quem age no aparelho dado pela interface. 0 personas → nenhuma (QA, caminho antigo); 1 → ela (casos A e B);
    2+ → a única que serve ao app do comando (caso C) ou PERGUNTA (caso D). `False` = virou pergunta."""
    personas = mundo.personas_em(instance_id)
    if len(personas) <= 1:
        return _primeiro(personas)
    if app_id is not None:
        servem = [p for p in personas if mundo.serve(p, instance_id, app_id)]
        if len(servem) == 1:
            return servem[0]
    estado.perguntas.append(Pergunta(
        "persona_no_aparelho",
        f"{instance_id} tem mais de uma persona ({_lista(mundo.nome(p) for p in personas)}): qual delas faz isto?",
        "profile_id", tuple(personas), instance_id=instance_id))
    return False


# ================================================================== aparelho de uma persona
def _aparelhos_pela_politica(mundo: Mundo, profile_id: str, candidatos: list[str],
                             politica: Politica) -> list[tuple[str, Origem]]:
    """Casos E, F e G: dos aparelhos candidatos da persona, quais recebem a tarefa, e por quê."""
    if not candidatos:
        return []
    aptos = [d for d in candidatos if d in mundo.aptos]
    if politica == "all":
        return [(d, "vinculo") for d in (aptos or candidatos)]
    principal = mundo.principal(profile_id)
    if politica == "primary" and principal in candidatos:
        return [(str(principal), "vinculo")]
    if len(candidatos) == 1:
        return [(candidatos[0], "vinculo")]
    # `one`: a pessoa faz uma vez (D4). Sessão pronta NAQUELE aparelho antes de tudo: é onde a conta já está
    # aberta; entre dois com sessão, o balanceamento (que prefere o ligado) desempata.
    com_sessao = [d for d in aptos if (profile_id, d) in mundo.sessoes_prontas]
    if len(com_sessao) == 1:
        return [(com_sessao[0], "vinculo")]
    if com_sessao:
        return [(mundo.desempatar(com_sessao) or com_sessao[0], "balanceamento")]
    # Sem sessão pronta em lugar nenhum: o principal, onde a porta de sessão autentica se houver credencial com
    # consentimento. Principal fora de ar e outro apto: o balanceamento escolhe entre os aptos.
    if principal in aptos:
        return [(str(principal), "vinculo")]
    if len(aptos) == 1:
        return [(aptos[0], "vinculo")]
    if aptos:
        return [(mundo.desempatar(aptos) or aptos[0], "balanceamento")]
    return [(principal if principal in candidatos else candidatos[0], "vinculo")]


# ================================================================== o texto contra a seleção
def _personas_do_texto(mundo: Mundo, dicas: DicasDoTexto, universo: Sequence[str] | None,
                       estado: _Estado) -> set[str] | None:
    """As personas que o texto aponta, cada menção resolvida contra o `universo` (a seleção da interface; `None` =
    interface vazia). Menção ambígua (dois "André") → pergunta; menção fora da seleção → pergunta (contradição).
    `None` = o texto não fala de persona."""
    if not dicas.personas:
        return None
    achadas: set[str] = set()
    for m in dicas.personas:
        dentro = [p for p in m.ids if universo is None or p in universo]
        if len(dentro) == 1:
            achadas.add(dentro[0])
        elif not dentro:
            estado.perguntas.append(Pergunta(
                "destino_contraditorio",
                f"O comando fala de “{m.trecho}”, mas a seleção é {_lista(mundo.nome(p) for p in universo or ())}: "
                "qual vale?", "profile_id", tuple(dict.fromkeys((*(universo or ()), *m.ids)))))
        else:
            estado.perguntas.append(Pergunta(
                "persona_ambigua", f"“{m.trecho}” pode ser {_lista(mundo.nome(p) for p in dentro)}: qual delas?",
                "profile_id", tuple(dentro)))
    return achadas


def _aparelhos_do_texto(dicas: DicasDoTexto) -> list[str]:
    return list(dict.fromkeys(i for m in dicas.aparelhos for i in m.ids))


# ================================================================== a resolução
def resolver_alvos(pedido: PedidoDeAlvos, dicas: DicasDoTexto, mundo: Mundo) -> Resolucao:
    """Os alvos da execução pela precedência do módulo. Levanta `RecusaDeAlvo`; ambiguidade volta em `perguntas`
    (com os alvos que já se sabiam — quem cria a execução não planeja com pergunta pendente)."""
    estado = _Estado()
    if pedido.targets or pedido.profile_ids:
        _por_persona(pedido, dicas, mundo, estado, pelo_texto=False)
    elif pedido.instance_ids:
        _por_aparelho(pedido, dicas, mundo, estado)
    elif not dicas.vazias:
        _so_pelo_texto(pedido, dicas, mundo, estado)
    else:
        raise RecusaDeAlvo("sem_alvo", "Diga onde ou por quem: escolha aparelhos, personas, ou cite no comando "
                                       "(“com a persona André”, “no android-03”).", 400)
    vistos: dict[str, AlvoResolvido] = {}
    for a in estado.alvos:
        if a.instance_id in vistos:
            # Fase 1 do N:N: um objetivo por aparelho por execução (`UNIQUE(run_id, instance_id)`, 001:74).
            outro = vistos[a.instance_id]
            raise RecusaDeAlvo("aparelho_repetido_na_execucao",
                               f"{a.instance_id} apareceria duas vezes nesta execução ("
                               f"{mundo.nome(outro.profile_id or '-')} e {mundo.nome(a.profile_id or '-')}): uma "
                               "execução usa cada aparelho uma vez só. Faça duas execuções, ou escolha outro aparelho.")
        vistos[a.instance_id] = a
    return Resolucao(tuple(estado.alvos), tuple(estado.perguntas))


def _so_pelo_texto(pedido: PedidoDeAlvos, dicas: DicasDoTexto, mundo: Mundo, estado: _Estado) -> None:
    """Caso M: a interface não disse nada; o texto decide, com origem `texto` em TODO alvo (a prévia é obrigatória
    antes de executar)."""
    personas = _personas_do_texto(mundo, dicas, None, estado)
    aparelhos = tuple(_aparelhos_do_texto(dicas))
    antes = len(estado.alvos)
    if personas:
        _por_persona(replace(pedido, profile_ids=tuple(sorted(personas)), instance_ids=aparelhos),
                     DicasDoTexto(), mundo, estado, pelo_texto=True)
    elif aparelhos and not dicas.personas:
        _por_aparelho(replace(pedido, instance_ids=aparelhos), DicasDoTexto(), mundo, estado)
    estado.alvos[antes:] = [replace(a, origem="texto") for a in estado.alvos[antes:]]


def _por_persona(pedido: PedidoDeAlvos, dicas: DicasDoTexto, mundo: Mundo, estado: _Estado, *,
                 pelo_texto: bool) -> None:
    base = list(pedido.targets) or [AlvoPedido(p) for p in pedido.profile_ids]
    selecionadas = [t.profile_id for t in base]
    # O texto só estreita: das personas selecionadas, as que ele cita.
    do_texto = _personas_do_texto(mundo, dicas, selecionadas, estado)
    estreitou_persona = bool(do_texto) and do_texto != set(selecionadas)
    if do_texto:
        base = [t for t in base if t.profile_id in do_texto]
    aparelhos_texto = _aparelhos_do_texto(dicas)
    usados_pelo_texto: set[str] = set()
    for t in base:
        p = t.profile_id
        candidatos = mundo.aparelhos_de(p)
        if not candidatos:
            raise RecusaDeAlvo("no_binding", f"{mundo.nome(p)} não está vinculada a nenhum aparelho.")
        explicitos = list(dict.fromkeys(t.instance_ids))
        if explicitos:
            # Caso J: alvo explícito com aparelho que não é dela.
            fora = [d for d in explicitos if d not in candidatos]
            if fora:
                raise RecusaDeAlvo("sem_vinculo", f"{mundo.nome(p)} não está vinculada a {_lista(fora)}.")
            pool = explicitos
        else:
            pool = candidatos
            if pedido.instance_ids:
                # `profile_ids` com `instance_ids` = INTERSEÇÃO (antes o primeiro substituía o segundo em silêncio).
                pool = [d for d in candidatos if d in pedido.instance_ids]
                if not pool:
                    raise RecusaDeAlvo("sem_intersecao",
                                       f"Nenhum dos aparelhos escolhidos ({_lista(pedido.instance_ids)}) é de "
                                       f"{mundo.nome(p)} (vinculada a {_lista(candidatos)}).")
            app = t.app_id or pedido.app_id
            if app is not None:
                pool = [d for d in pool if mundo.serve(p, d, app)] or pool
        estreitou_aparelho = False
        if aparelhos_texto:
            citados = [d for d in aparelhos_texto if d in pool]
            usados_pelo_texto.update(citados)
            if citados:
                estreitou_aparelho = set(citados) != set(pool)
                pool = citados
        if explicitos:
            escolhas: list[tuple[str, Origem]] = [(d, "ui") for d in pool]
        elif pedido.instance_ids and len(pool) == 1 and not pelo_texto:
            escolhas = [(pool[0], "ui")]           # persona E aparelho pela interface: nada a decidir
        else:
            escolhas = _aparelhos_pela_politica(mundo, p, pool, pedido.device_policy)
        for d, origem in escolhas:
            if estreitou_persona or estreitou_aparelho:
                origem = "texto"
            estado.alvos.append(AlvoResolvido(d, p, t.app_id or pedido.app_id, origem))
    for d in aparelhos_texto:
        if d not in usados_pelo_texto:
            estado.perguntas.append(Pergunta(
                "destino_contraditorio",
                f"O comando fala de {d}, que não é aparelho de {_lista(mundo.nome(t.profile_id) for t in base)}: "
                "qual vale?", "instance_id", (d,), instance_id=d))


def _por_aparelho(pedido: PedidoDeAlvos, dicas: DicasDoTexto, mundo: Mundo, estado: _Estado) -> None:
    """Casos A–D, com o texto estreitando: um aparelho citado dentro da seleção a reduz; uma persona citada escolhe
    entre as do aparelho (e reduz a seleção aos aparelhos dela)."""
    aparelhos = list(dict.fromkeys(pedido.instance_ids))
    origem: Origem = "ui"
    citados = _aparelhos_do_texto(dicas)
    if citados:
        fora = [d for d in citados if d not in aparelhos]
        for d in fora:
            estado.perguntas.append(Pergunta(
                "destino_contraditorio", f"O comando fala de {d}, mas a seleção é {_lista(aparelhos)}: qual vale?",
                "instance_id", tuple(dict.fromkeys((*aparelhos, d))), instance_id=d))
        dentro = [d for d in citados if d in aparelhos]
        if dentro and set(dentro) != set(aparelhos):
            aparelhos, origem = dentro, "texto"
    universo = list(dict.fromkeys(p for d in aparelhos for p in mundo.personas_em(d)))
    do_texto = _personas_do_texto(mundo, dicas, universo, estado)
    if do_texto:
        com_elas = [d for d in aparelhos if set(mundo.personas_em(d)) & do_texto]
        if set(com_elas) != set(aparelhos):
            aparelhos, origem = com_elas, "texto"
    for d in aparelhos:
        preferidas = [p for p in mundo.personas_em(d) if do_texto and p in do_texto]
        if len(preferidas) == 1:
            pid: str | None = preferidas[0]
            origem_d: Origem = "texto" if len(mundo.personas_em(d)) > 1 else origem
        else:
            escolhida = _persona_no_aparelho(mundo, d, pedido.app_id, estado)
            if escolhida is False:
                continue
            pid, origem_d = escolhida, origem
        estado.alvos.append(AlvoResolvido(d, pid, pedido.app_id, origem_d))
