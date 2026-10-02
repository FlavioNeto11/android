"""Instantes devidos de um gatilho (docs/design/pedidos-laco.md §2.1).

O laço guarda um CURSOR por gatilho (`pedido_gatilhos.cursor`, texto `formatar_instante`, exclusivo: o último instante
já materializado) e pergunta aqui "quais instantes caem depois do cursor e até o horizonte?". A `spec` é JSON gravado
por quem cria o pedido (28.9); aqui só se lê:

| tipo          | `spec`                                                 | instantes                                           |
| `agora`       | `{}`                                                   | um só: o `criado_em` do gatilho (a ativação)        |
| `horario`     | `{"local": "2026-10-03T09:00:00"}`                     | um só, a hora local no fuso do pedido               |
| `recorrencia` | `{"dtstart": "2026-10-02T09:00:00", "rrule": "FREQ=…"}` | `recorrencia.proximas`, a partir do cursor          |

`evento`, `condicao` e `persona` são do 28.8 e não passam por aqui (`SUPORTADOS` diz quais o laço materializa).

Puro: stdlib e `recorrencia`. Quem chama passa o instante de agora; nada lê relógio.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone

from app.modules.pedidos.domain import recorrencia
from app.modules.pedidos.domain.materializar import periodo_nominal_s, truncar

UTC = timezone.utc
#: Tipos que o laço de pedidos materializa no 28.4.
SUPORTADOS: frozenset[str] = frozenset({"agora", "horario", "recorrencia"})


class ErroDeGatilho(ValueError):
    """A `spec` não serve (JSON incompleto, regra ou hora inválida): o laço registra e deixa o gatilho de lado."""


def _local(spec: Mapping, chave: str) -> datetime:
    try:
        valor = spec[chave]
        if not isinstance(valor, str):
            raise TypeError(chave)
        dt = datetime.fromisoformat(valor)
    except (KeyError, TypeError, ValueError) as e:
        raise ErroDeGatilho(f"spec sem `{chave}` válido (hora local ISO, sem fuso): {e}") from e
    if dt.tzinfo is not None:
        raise ErroDeGatilho(f"`{chave}` não pode trazer fuso: a hora é local, no fuso do pedido")
    return dt


def _regra(spec: Mapping) -> tuple[recorrencia.Regra, datetime]:
    try:
        regra = recorrencia.interpretar(str(spec["rrule"]))
    except KeyError as e:
        raise ErroDeGatilho("spec sem `rrule`") from e
    except recorrencia.ErroRecorrencia as e:
        raise ErroDeGatilho(f"rrule inválida: {e}") from e
    return regra, _local(spec, "dtstart")


def instantes(tipo: str, spec: Mapping, *, fuso: str, criado_em: datetime, depois_de: datetime, ate: datetime,
              limite: int) -> list[datetime]:
    """Instantes UTC com `depois_de < i <= ate`, em ordem, no máximo `limite` (o lote da volta)."""
    try:
        if tipo == "agora":
            todos = [truncar(criado_em)]
        elif tipo == "horario":
            todos = [recorrencia.localizar(_local(spec, "local"), fuso).utc]
        elif tipo == "recorrencia":
            regra, inicio = _regra(spec)
            achados = recorrencia.proximas(regra, inicio, fuso, depois_de=depois_de,
                                           limite=min(limite, recorrencia.LIMITE_PREVIA))
            todos = [a.utc for a in achados]
        else:
            raise ErroDeGatilho(f"tipo de gatilho fora do 28.4: {tipo}")
    except recorrencia.ErroRecorrencia as e:
        raise ErroDeGatilho(str(e)) from e
    return [i for i in todos if depois_de < i <= ate][:limite]


def proximo(tipo: str, spec: Mapping, *, fuso: str, criado_em: datetime, depois_de: datetime) -> datetime | None:
    """O primeiro instante depois do cursor, sem horizonte; `None` = o gatilho se esgotou."""
    ate = depois_de + timedelta(days=366 * 30)
    achados = instantes(tipo, spec, fuso=fuso, criado_em=criado_em, depois_de=depois_de, ate=ate, limite=1)
    return achados[0] if achados else None


def periodo_s(tipo: str, spec: Mapping) -> int | None:
    """O período nominal (FREQ × INTERVAL) da recorrência; `None` para os outros tipos."""
    if tipo != "recorrencia":
        return None
    regra, _ = _regra(spec)
    return periodo_nominal_s(regra.freq, regra.intervalo)


def esgota_por(tipo: str, spec: Mapping) -> str:
    """Motivo de encerramento quando o gatilho acaba sozinho: `contagem` (COUNT, ou um instante único que passou) ou
    `prazo` (UNTIL)."""
    if tipo != "recorrencia":
        return "contagem"
    regra, _ = _regra(spec)
    return "contagem" if regra.contagem is not None else "prazo"
