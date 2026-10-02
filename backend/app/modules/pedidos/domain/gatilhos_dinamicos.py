"""Gatilhos de evento, condição e persona (item 28.8; docs/design/pedidos-laco.md §14).

Os três não são "instantes de uma regra" como os do 28.4 (`gatilhos.py`): o evento depende do log de eventos, a condição
das observações e a persona do fechamento da visita anterior. Aqui ficam a validação da `spec`, os formatos de cursor e as
decisões puras; quem lê o banco e grava é o laço (`infrastructure/laco.py`).

| tipo       | `spec`                                                         | `cursor`                       |
| `evento`   | `{"kinds": [...], "niveis": [...]?}`                           | `ev:<events.id>` (inclusive)    |
| `condicao` | `{"observacao": nome, "op": "<", "valor": 3500}`              | `cond:<0|1>:<ocorrencia>`       |
| `persona`  | `{"intervalo_min_s": N, "intervalo_max_s": M}`                | `formatar_instante`, como 28.4 |

Puro: stdlib. Nada lê relógio nem banco.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

#: Os tipos deste módulo (a 067 já os aceita no CHECK de `pedido_gatilhos.tipo`).
DINAMICOS: frozenset[str] = frozenset({"evento", "condicao", "persona"})

# ------------------------------------------------------------------ evento
MAX_KINDS = 10
KIND_RE = re.compile(r"^[a-z][a-z0-9_.]{0,63}$")
NIVEIS = ("info", "warn", "error")
#: Prefixo proibido: o pedido se dispararia com os próprios eventos (`pedido.updated`, `pedido.aviso`...).
PREFIXO_PROPRIO = "pedido."
_CURSOR_EV = re.compile(r"^ev:(\d{1,18})$")

# ------------------------------------------------------------------ condição
OPS_DE_ORDEM = ("<", "<=", ">", ">=")
OPS = (*OPS_DE_ORDEM, "==", "!=", "mudou")
NOME_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")          # o de `observacao.NOME_RE` (step_outputs.name, 056)
VALOR_TEXTO_MAX = 200
_CURSOR_COND = re.compile(r"^cond:([01]):([A-Za-z0-9_-]{1,40})$")

# ------------------------------------------------------------------ persona
INTERVALO_MINIMO_S = 300
INTERVALO_MAXIMO_S = 30 * 86400
#: A saída que a execução pode gravar para propor a próxima visita (vira observação no fechamento, 28.7).
NOME_DA_PROPOSTA = "proxima_visita_s"


class SpecInvalida(ValueError):
    """A `spec` não serve. `campo` diz onde (relativo à spec), para a API apontar o campo."""

    def __init__(self, mensagem: str, campo: str = "spec"):
        super().__init__(mensagem)
        self.campo = campo


# ================================================================== validação (criação)
def normalizar(tipo: str, spec: Mapping[str, object], *, efemeros: Iterable[str] = ()) -> dict[str, object]:
    """A `spec` canônica que o laço lê. `efemeros` = `events.EPHEMERAL_KINDS` (passado por quem chama: o domínio não
    importa o barramento)."""
    if tipo == "evento":
        return _evento(spec, frozenset(efemeros))
    if tipo == "condicao":
        return _condicao(spec)
    if tipo == "persona":
        return _persona(spec)
    raise SpecInvalida(f"tipo de gatilho fora do 28.8: {tipo!r}", "tipo")


def _evento(spec: Mapping[str, object], efemeros: frozenset[str]) -> dict[str, object]:
    kinds = spec.get("kinds")
    if not isinstance(kinds, list) or not kinds or len(kinds) > MAX_KINDS:
        raise SpecInvalida(f"`kinds` precisa ser uma lista de 1 a {MAX_KINDS} tipos de evento", "spec.kinds")
    limpos: list[str] = []
    for k in kinds:
        if not isinstance(k, str) or not KIND_RE.fullmatch(k):
            raise SpecInvalida(f"tipo de evento inválido: {k!r}", "spec.kinds")
        if k.startswith(PREFIXO_PROPRIO):
            raise SpecInvalida(f"`{k}`: o pedido não se dispara com eventos de pedido (laço fechado)", "spec.kinds")
        if k in efemeros:
            raise SpecInvalida(f"`{k}` não é gravado no log de eventos: o gatilho nunca dispararia", "spec.kinds")
        if k not in limpos:
            limpos.append(k)
    saida: dict[str, object] = {"kinds": sorted(limpos)}
    niveis = spec.get("niveis")
    if niveis is not None:
        if not isinstance(niveis, list) or not niveis or any(n not in NIVEIS for n in niveis):
            raise SpecInvalida(f"`niveis` aceita só {list(NIVEIS)}", "spec.niveis")
        saida["niveis"] = [n for n in NIVEIS if n in niveis]
    return saida


def _numero(v: object) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _inteiro(v: object) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        raise ValueError(f"esperado um número: {v!r}")
    return int(v)


def _condicao(spec: Mapping[str, object]) -> dict[str, object]:
    nome = spec.get("observacao")
    if not isinstance(nome, str) or not NOME_RE.fullmatch(nome):
        raise SpecInvalida("`observacao` precisa ser o nome de uma saída (minúsculas, dígitos e _)", "spec.observacao")
    op = spec.get("op")
    if op not in OPS:
        raise SpecInvalida(f"`op` precisa ser um de {list(OPS)}", "spec.op")
    if op == "mudou":
        return {"observacao": nome, "op": op}
    valor = spec.get("valor")
    if op in OPS_DE_ORDEM:
        n = _numero(valor)
        if n is None:
            raise SpecInvalida(f"`{op}` compara números: `valor` precisa ser número", "spec.valor")
        return {"observacao": nome, "op": op, "valor": n}
    n = _numero(valor)
    if n is not None:
        return {"observacao": nome, "op": op, "valor": n}
    if not isinstance(valor, str) or not valor.strip() or len(valor) > VALOR_TEXTO_MAX:
        raise SpecInvalida(f"`valor` precisa ser número ou texto de 1 a {VALOR_TEXTO_MAX} caracteres", "spec.valor")
    return {"observacao": nome, "op": op, "valor": valor.strip()}


def _segundos(spec: Mapping[str, object], chave: str) -> int:
    v = spec.get(chave)
    if isinstance(v, bool) or not isinstance(v, int):
        raise SpecInvalida(f"`{chave}` precisa ser um número inteiro de segundos", f"spec.{chave}")
    if not INTERVALO_MINIMO_S <= v <= INTERVALO_MAXIMO_S:
        raise SpecInvalida(f"`{chave}` fica entre {INTERVALO_MINIMO_S} s e {INTERVALO_MAXIMO_S // 86400} dias",
                           f"spec.{chave}")
    return v


def _persona(spec: Mapping[str, object]) -> dict[str, object]:
    minimo = _segundos(spec, "intervalo_min_s")
    maximo = _segundos(spec, "intervalo_max_s")
    if minimo > maximo:
        raise SpecInvalida("`intervalo_min_s` não pode passar de `intervalo_max_s`", "spec.intervalo_min_s")
    return {"intervalo_min_s": minimo, "intervalo_max_s": maximo}


# ================================================================== evento: cursor e buraco
def cursor_de_evento(texto: str | None) -> int | None:
    """O último `events.id` lido; `None` = sem linha de base (o laço a fixa em `MAX(events.id)`)."""
    m = _CURSOR_EV.fullmatch(texto or "")
    return int(m.group(1)) if m else None


def formatar_cursor_de_evento(evento_id: int) -> str:
    return f"ev:{max(0, int(evento_id))}"


def buraco(cursor: int, menor_id: int | None) -> tuple[int, int] | None:
    """A faixa `(de, ate)` de ids que a retenção apagou antes de o gatilho os ler, ou `None`.

    `menor_id` é o menor `events.id` que ainda existe (`None` = log vazio: nada a dizer). Há buraco quando o primeiro id
    depois do cursor já não existe E há eventos depois dele: `cursor < menor_id - 1`. Um id pulado pela sequência
    (rollback) bem na fronteira dá falso positivo: avisa a mais, nunca dispara a mais (§14.2)."""
    if menor_id is None or cursor >= menor_id - 1:
        return None
    return cursor + 1, menor_id - 1


def motivo_do_evento(ids: list[int], kinds: Iterable[str]) -> str:
    """Só contagem, tipos e faixa de ids: nunca a `message` nem o `data` do evento (podem ter texto de terceiro)."""
    tipos = ", ".join(sorted(set(kinds)))
    n = len(ids)
    faixa = f"#{ids[0]}" if n == 1 else f"#{ids[0]} a #{ids[-1]}"
    return f"{n} evento(s) {tipos} ({faixa})"[:500]


# ================================================================== persona
def proxima_visita(*, anterior_terminada_em: datetime | None, criado_em: datetime, proposta_s: float | None,
                   spec: Mapping[str, object], fim_em: datetime | None) -> datetime | None:
    """O instante da próxima visita. Sem visita anterior: a ativação (`criado_em`). Com ela: o fechamento dela mais a
    proposta presa a [`intervalo_min_s`, `intervalo_max_s`] (sem proposta, o máximo). `None` = passou de `fim_em`."""
    if anterior_terminada_em is None:
        quando = criado_em
    else:
        minimo = _inteiro(spec["intervalo_min_s"])
        maximo = _inteiro(spec["intervalo_max_s"])
        espera = maximo if proposta_s is None else min(maximo, max(minimo, int(proposta_s)))
        quando = anterior_terminada_em + timedelta(seconds=espera)
    quando = quando.replace(microsecond=0)
    if fim_em is not None and quando > fim_em:
        return None
    return quando


def proposta_valida(tipo: str | None, situacao: str | None, valor: str | None) -> float | None:
    """A proposta `proxima_visita_s` só vale observada, numérica e positiva; o resto é "sem proposta"."""
    if tipo != "number" or situacao != "observado" or valor is None:
        return None
    try:
        n = float(valor)
    except ValueError:
        return None
    return n if n > 0 else None


# ================================================================== condição
@dataclass(frozen=True)
class Observada:
    """A observação que a condição lê (uma linha de `pedido_observacoes`)."""
    tipo: str
    situacao: str
    valor: str | None
    sha256: str | None


def cursor_de_condicao(texto: str | None) -> tuple[bool, str] | None:
    """`(último veredito, ocorrência que o deu)` ou `None` (nunca avaliada)."""
    m = _CURSOR_COND.fullmatch(texto or "")
    return (m.group(1) == "1", m.group(2)) if m else None


def formatar_cursor_de_condicao(veredito: bool, ocorrencia_id: str) -> str:
    return f"cond:{1 if veredito else 0}:{ocorrencia_id}"


def avaliar(spec: Mapping[str, object], atual: Observada | None, anterior: Observada | None) -> bool | None:
    """O veredito da condição sobre a observação desta ocorrência; `None` = sem veredito (não observada, incerta, tipo
    incompatível). `anterior` só importa para `mudou` (a observação de mesmo nome da ocorrência anterior)."""
    if atual is None or atual.situacao != "observado" or atual.valor is None:
        return None
    op = spec["op"]
    if op == "mudou":
        if anterior is None or anterior.situacao != "observado" or not anterior.sha256 or not atual.sha256:
            return None
        return anterior.sha256 != atual.sha256
    alvo = spec.get("valor")
    if isinstance(alvo, (int, float)):
        if atual.tipo != "number":
            return None
        try:
            v = float(atual.valor)
        except ValueError:
            return None
        return {"<": v < alvo, "<=": v <= alvo, ">": v > alvo, ">=": v >= alvo, "==": v == alvo,
                "!=": v != alvo}[str(op)]
    if op not in ("==", "!="):
        return None
    igual = atual.valor.strip() == str(alvo)
    return igual if op == "==" else not igual


def disparou(veredito: bool | None, cursor: tuple[bool, str] | None) -> bool:
    """Borda, não nível: só falso (ou nunca avaliada) → verdadeiro dispara."""
    return veredito is True and (cursor is None or cursor[0] is False)


# ================================================================== texto para a pessoa
def _duracao(segundos: int) -> str:
    if segundos % 86400 == 0:
        d = segundos // 86400
        return "1 dia" if d == 1 else f"{d} dias"
    if segundos % 3600 == 0:
        h = segundos // 3600
        return f"{h} h"
    return f"{segundos // 60} min"


def descrever(tipo: str, spec: Mapping[str, object]) -> str:
    """O texto curto de `gatilhos_resumo`. Spec quebrada não derruba a leitura: cai no nome do tipo."""
    try:
        if tipo == "evento":
            kinds = spec["kinds"]
            if not isinstance(kinds, list):
                return tipo
            return "Quando acontecer: " + ", ".join(str(k) for k in kinds)
        if tipo == "condicao":
            if spec["op"] == "mudou":
                return f"Avisa quando {spec['observacao']} mudar"
            valor = spec["valor"]
            texto = f"{valor:g}" if isinstance(valor, float) else str(valor)
            return f"Avisa quando {spec['observacao']} {spec['op']} {texto}"
        if tipo == "persona":
            return (f"A persona volta entre {_duracao(_inteiro(spec['intervalo_min_s']))} e "
                    f"{_duracao(_inteiro(spec['intervalo_max_s']))}")
    except (KeyError, TypeError, ValueError):
        pass
    return tipo
