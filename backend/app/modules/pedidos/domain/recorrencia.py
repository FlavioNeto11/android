"""Recorrência e fuso do pedido persistente (item 28.3; docs/design/pedidos-persistentes.md §7.7).

Módulo PURO: sem banco, sem rede, sem relógio. Quem pede a próxima data passa `depois_de` (o relógio injetado do
chamador); este arquivo nunca lê `datetime.now()`.

O pedido guarda `DTSTART` em hora LOCAL ingênua, o fuso IANA e um subconjunto da `RRULE` da RFC 5545:
`FREQ` (HOURLY, DAILY, WEEKLY, MONTHLY), `INTERVAL`, `BYDAY`, `BYMONTHDAY`, `BYHOUR`, `BYMINUTE` e `COUNT` ou
`UNTIL`. Tudo fora disso (SECONDLY/MINUTELY, YEARLY, BYSETPOS, WKST diferente de MO, `BYDAY=1MO`...) recusa com
`ErroRecorrencia` e a mensagem diz o que é aceito. O gerador é próprio, não `python-dateutil`: o subconjunto é
pequeno, o desvio do horário de verão não existe lá e as fronteiras ficam sob teste daqui.

Como as datas nascem (desenho §7.7):

1. as ocorrências são geradas em hora local ingênua, na ordem do calendário, a partir de `DTSTART`;
2. cada uma é localizada com `zoneinfo`; o banco guarda UTC (`Instante.utc`);
3. HORA INEXISTENTE (o salto da primavera): a RFC manda ignorar a instância (e não contá-la). O pedido DESVIA DE
   PROPÓSITO: executa no primeiro instante válido depois do salto (o instante da transição), porque um relatório
   diário não pode sumir num dia. A instância desviada conta no `COUNT`; `Instante.desviado` marca o caso para a API
   documentar. Se esse instante já for outra ocorrência da regra (hora cheia seguinte), as duas viram uma só;
4. HORA REPETIDA (fim do horário de verão): uma execução, na primeira (`fold=0`, PEP 495 e RFC §3.3.5), ao
   contrário do `CRON_TZ` do cronie, que roda duas vezes. `Instante.repetido` marca o caso;
5. as regras de fuso vêm da base IANA do `tzdata` declarado, nunca de deslocamento fixo: quando o Brasil (ou
   outro país) mudar a regra, atualizar o pacote basta, e as próximas datas são recalculadas.

Convenções do subconjunto (iguais às do `rrule` do dateutil, para não surpreender quem conhece a RFC):

- `DTSTART` só é a primeira ocorrência se casar com a regra (`FREQ=WEEKLY;BYDAY=MO` com início numa quarta começa
  na segunda seguinte);
- `BYHOUR`/`BYMINUTE` ausentes herdam a hora e o minuto de `DTSTART` (em HOURLY, `BYHOUR` só filtra);
- `BYDAY` em DAILY/HOURLY filtra, em WEEKLY expande (padrão: o dia da semana de `DTSTART`), em MONTHLY expande
  todos os dias da semana do mês (ordinal tipo `1MO` não é suportado); com `BYMONTHDAY` as duas listas se
  intersectam;
- dia que não existe no mês (31 em abril, 30 em fevereiro) é ignorado e não conta;
- a semana começa na segunda (`WKST=MO`), o que só importa em WEEKLY com `INTERVAL` > 1;
- HOURLY avança em hora de relógio LOCAL (ingênua), como o desenho manda: no fim do horário de verão a lacuna
  entre duas ocorrências é de duas horas reais, e no começo a hora inexistente cai no desvio do item 3;
- `UNTIL` é inclusivo; `YYYYMMDD` vale até o fim daquele dia local, `YYYYMMDDTHHMMSS` é hora local e a forma com
  `Z` é um instante UTC (a RFC exige UTC com `TZID`; aceitamos as três);
- regra que nunca casa (ex.: `FREQ=HOURLY;INTERVAL=12;BYHOUR=3`) não trava: sem nenhuma ocorrência por 30 anos o
  gerador para e a lista vem vazia.
"""
from __future__ import annotations

import re
from calendar import monthrange
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc
FUSO_PADRAO = "America/Sao_Paulo"

FREQUENCIAS = ("HOURLY", "DAILY", "WEEKLY", "MONTHLY")
_DIAS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")   # índice = datetime.weekday()
_ACEITAS = "FREQ, INTERVAL, BYDAY, BYMONTHDAY, BYHOUR, BYMINUTE, COUNT, UNTIL"
INTERVALO_MAXIMO = 1000
LIMITE_PREVIA = 1000

#: Sem nenhuma ocorrência por tanto tempo a regra é dada como esgotada (ver o fim do docstring do módulo).
_JANELA_SEM_OCORRENCIA = timedelta(days=366 * 30)

_INTEIRO = re.compile(r"[0-9]+")
_INTEIRO_COM_SINAL = re.compile(r"-?[0-9]+")
_UNTIL = re.compile(r"(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})(\d{2})(Z)?)?")


class ErroRecorrencia(ValueError):
    """Regra, início ou fuso inválidos ou fora do subconjunto; a mensagem é para a pessoa."""


@dataclass(frozen=True)
class Regra:
    """A `RRULE` interpretada. Listas vêm ordenadas e sem repetição; vazio quer dizer "não informado"."""
    freq: str
    intervalo: int = 1
    dias_semana: tuple[int, ...] = ()      # 0 = segunda ... 6 = domingo
    dias_mes: tuple[int, ...] = ()         # 1..31 ou -31..-1 (-1 = último dia do mês)
    horas: tuple[int, ...] = ()
    minutos: tuple[int, ...] = ()
    contagem: int | None = None
    ate: datetime | None = None            # aware = instante UTC; ingênuo = hora local do pedido

    def para_texto(self) -> str:
        """Forma canônica (ordem fixa, sem prefixo `RRULE:`); `interpretar(r.para_texto()) == r`."""
        p = [f"FREQ={self.freq}"]
        if self.intervalo != 1:
            p.append(f"INTERVAL={self.intervalo}")
        if self.dias_mes:
            p.append("BYMONTHDAY=" + ",".join(str(d) for d in self.dias_mes))
        if self.dias_semana:
            p.append("BYDAY=" + ",".join(_DIAS[d] for d in self.dias_semana))
        if self.horas:
            p.append("BYHOUR=" + ",".join(str(h) for h in self.horas))
        if self.minutos:
            p.append("BYMINUTE=" + ",".join(str(m) for m in self.minutos))
        if self.contagem is not None:
            p.append(f"COUNT={self.contagem}")
        if self.ate is not None:
            p.append("UNTIL=" + self.ate.strftime("%Y%m%dT%H%M%S") + ("Z" if self.ate.tzinfo else ""))
        return ";".join(p)


@dataclass(frozen=True)
class Instante:
    """Uma ocorrência já localizada."""
    nominal: datetime    # hora local ingênua que a regra pediu
    local: datetime      # a mesma ocorrência no fuso do pedido (aware, fold=0)
    utc: datetime        # o que o banco guarda
    desviado: bool       # hora inexistente: executa no primeiro instante válido depois do salto
    repetido: bool       # hora ambígua: executa só na primeira (fold=0)

    def para_dict(self) -> dict:
        return {"nominal": self.nominal.isoformat(), "local": self.local.isoformat(), "utc": self.utc.isoformat(),
                "desviado": self.desviado, "repetido": self.repetido}


# ================================================================== interpretação
def _inteiro(chave: str, texto: str, minimo: int, maximo: int, *, com_sinal: bool = False) -> int:
    if not (_INTEIRO_COM_SINAL if com_sinal else _INTEIRO).fullmatch(texto):
        raise ErroRecorrencia(f"{chave}={texto!r}: esperado um número inteiro")
    n = int(texto)
    if not minimo <= n <= maximo:
        raise ErroRecorrencia(f"{chave}={n}: fora do intervalo {minimo}..{maximo}")
    return n


def _lista(chave: str, valor: str, minimo: int, maximo: int, *, com_sinal: bool = False) -> tuple[int, ...]:
    itens = [_inteiro(chave, v.strip(), minimo, maximo, com_sinal=com_sinal) for v in valor.split(",")]
    if chave == "BYMONTHDAY" and 0 in itens:
        raise ErroRecorrencia("BYMONTHDAY=0: o dia do mês vai de 1 a 31 (ou -1 a -31, contado do fim)")
    return tuple(sorted(set(itens)))


def _dias_da_semana(valor: str) -> tuple[int, ...]:
    dias = []
    for v in valor.split(","):
        v = v.strip().upper()
        if v not in _DIAS:
            if re.fullmatch(r"[+-]?\d+(MO|TU|WE|TH|FR|SA|SU)", v):
                raise ErroRecorrencia(f"BYDAY={v}: dia com ordinal (ex.: 1MO, -1FR) não é suportado; use só "
                                      f"{','.join(_DIAS)}")
            raise ErroRecorrencia(f"BYDAY={v!r}: esperado {','.join(_DIAS)}")
        dias.append(_DIAS.index(v))
    return tuple(sorted(set(dias)))


def _ate(valor: str) -> datetime:
    m = _UNTIL.fullmatch(valor)
    if not m:
        raise ErroRecorrencia(f"UNTIL={valor!r}: esperado YYYYMMDD, YYYYMMDDTHHMMSS (hora local) ou "
                              f"YYYYMMDDTHHMMSSZ (UTC)")
    a, mes, d, hh, mm, ss, z = m.groups()
    try:
        if hh is None:        # só a data: vale até o fim daquele dia local
            return datetime(int(a), int(mes), int(d), 23, 59, 59)
        return datetime(int(a), int(mes), int(d), int(hh), int(mm), int(ss), tzinfo=UTC if z else None)
    except ValueError as e:
        raise ErroRecorrencia(f"UNTIL={valor!r}: data ou hora inexistente ({e})") from None


def interpretar(texto: str) -> Regra:
    """`FREQ=WEEKLY;BYDAY=MO,WE;BYHOUR=9` (com ou sem `RRULE:`) → `Regra`; recusa com mensagem clara o resto."""
    if not isinstance(texto, str) or not texto.strip():
        raise ErroRecorrencia("regra de recorrência vazia")
    corpo = texto.strip()
    if corpo.upper().startswith("RRULE:"):
        corpo = corpo[6:]
    if ":" in corpo or "\n" in corpo:
        raise ErroRecorrencia("só a regra (RRULE) vai aqui; o início (DTSTART) e o fuso são campos do pedido")
    campos: dict[str, str] = {}
    for parte in corpo.split(";"):
        parte = parte.strip()
        chave, igual, valor = parte.partition("=")
        chave, valor = chave.strip().upper(), valor.strip()
        if not igual or not chave or not valor:
            raise ErroRecorrencia(f"parte {parte!r} inválida: esperado CHAVE=valor")
        if chave in campos:
            raise ErroRecorrencia(f"{chave} aparece mais de uma vez")
        campos[chave] = valor
    if "WKST" in campos:
        if campos["WKST"].upper() != "MO":
            raise ErroRecorrencia("WKST diferente de MO não é suportado: a semana começa na segunda")
        del campos["WKST"]
    desconhecidas = sorted(set(campos) - {"FREQ", "INTERVAL", "BYDAY", "BYMONTHDAY", "BYHOUR", "BYMINUTE", "COUNT",
                                          "UNTIL"})
    if desconhecidas:
        raise ErroRecorrencia(f"{', '.join(desconhecidas)} não é suportado (suportado: {_ACEITAS})")
    freq = campos.get("FREQ", "").upper()
    if not freq:
        raise ErroRecorrencia("FREQ é obrigatório (HOURLY, DAILY, WEEKLY ou MONTHLY)")
    if freq in ("SECONDLY", "MINUTELY"):
        raise ErroRecorrencia(f"FREQ={freq} não é suportado: o piso de frequência é HOURLY (desenho §10)")
    if freq == "YEARLY":
        raise ErroRecorrencia("FREQ=YEARLY não é suportado; use FREQ=MONTHLY;INTERVAL=12")
    if freq not in FREQUENCIAS:
        raise ErroRecorrencia(f"FREQ={freq!r} desconhecido (suportado: {', '.join(FREQUENCIAS)})")
    if "COUNT" in campos and "UNTIL" in campos:
        raise ErroRecorrencia("COUNT e UNTIL são exclusivos (RFC 5545 §3.3.10)")
    if freq == "WEEKLY" and "BYMONTHDAY" in campos:
        raise ErroRecorrencia("BYMONTHDAY não vale com FREQ=WEEKLY (RFC 5545 §3.3.10)")
    return Regra(
        freq=freq,
        intervalo=_inteiro("INTERVAL", campos["INTERVAL"], 1, INTERVALO_MAXIMO) if "INTERVAL" in campos else 1,
        dias_semana=_dias_da_semana(campos["BYDAY"]) if "BYDAY" in campos else (),
        dias_mes=_lista("BYMONTHDAY", campos["BYMONTHDAY"], -31, 31, com_sinal=True) if "BYMONTHDAY" in campos else (),
        horas=_lista("BYHOUR", campos["BYHOUR"], 0, 23) if "BYHOUR" in campos else (),
        minutos=_lista("BYMINUTE", campos["BYMINUTE"], 0, 59) if "BYMINUTE" in campos else (),
        contagem=_inteiro("COUNT", campos["COUNT"], 1, 1_000_000) if "COUNT" in campos else None,
        ate=_ate(campos["UNTIL"]) if "UNTIL" in campos else None,
    )


# ================================================================== fuso
@lru_cache(maxsize=64)
def _zona(nome: str) -> ZoneInfo:
    try:
        return ZoneInfo(nome)
    except ZoneInfoNotFoundError:
        raise ErroRecorrencia(f"fuso {nome!r} não existe na base IANA (ou o pacote tzdata não está instalado)") from None
    except ValueError:    # chave com `..`, caminho absoluto etc.: o zoneinfo recusa
        raise ErroRecorrencia(f"fuso {nome!r} inválido: esperado um nome IANA como {FUSO_PADRAO}") from None


def carregar_fuso(fuso: str | ZoneInfo) -> ZoneInfo:
    if isinstance(fuso, ZoneInfo):
        return fuso
    if not isinstance(fuso, str) or not fuso.strip():
        raise ErroRecorrencia("fuso vazio")
    return _zona(fuso.strip())


def localizar(nominal: datetime, fuso: str | ZoneInfo) -> Instante:
    """Localiza uma hora local ingênua. Hora existente: `fold=0` (a primeira, se repetida); inexistente: o primeiro
    instante válido depois do salto (desvio documentado no módulo)."""
    tz = carregar_fuso(fuso)
    if nominal.tzinfo is not None:
        raise ErroRecorrencia("a hora local do pedido não pode trazer fuso embutido")
    nominal = nominal.replace(microsecond=0, fold=0)
    ingenuo = nominal.replace(tzinfo=tz)
    utc = ingenuo.astimezone(UTC)
    volta = utc.astimezone(tz)
    if volta.replace(tzinfo=None) == nominal:
        repetido = nominal.replace(tzinfo=tz, fold=1).utcoffset() != ingenuo.utcoffset()
        return Instante(nominal, volta, utc, False, repetido)
    # Lacuna do salto. Pela PEP 495, `fold=0` usa o deslocamento de antes e `fold=1` o de depois; a transição T
    # fica entre `nominal - depois` (ainda antes dela) e `nominal - antes` (já depois). Bisecção em segundos.
    antes, depois = ingenuo.utcoffset(), nominal.replace(tzinfo=tz, fold=1).utcoffset()
    lo, hi = nominal - depois, nominal - antes
    while hi - lo > timedelta(seconds=1):
        meio = lo + (hi - lo) // 2
        if meio.replace(tzinfo=UTC).astimezone(tz).utcoffset() == antes:
            lo = meio
        else:
            hi = meio
    t_utc = hi.replace(tzinfo=UTC)
    return Instante(nominal, t_utc.astimezone(tz), t_utc, True, False)


# ================================================================== geração (hora local ingênua)
def _normalizar_inicio(inicio: datetime) -> datetime:
    if not isinstance(inicio, datetime) or inicio.tzinfo is not None:
        raise ErroRecorrencia("DTSTART é uma hora local ingênua (sem fuso); o fuso do pedido é outro campo")
    return inicio.replace(microsecond=0, fold=0)


def _dia_passa(regra: Regra, d: date) -> bool:
    if regra.dias_semana and d.weekday() not in regra.dias_semana:
        return False
    if regra.dias_mes:
        n = monthrange(d.year, d.month)[1]
        if d.day not in {x if x > 0 else n + 1 + x for x in regra.dias_mes}:
            return False
    return True


def _horarios(regra: Regra, inicio: datetime, d: date) -> list[datetime]:
    horas, minutos = regra.horas or (inicio.hour,), regra.minutos or (inicio.minute,)
    return [datetime.combine(d, time(h, m, inicio.second)) for h in horas for m in minutos]


def _periodos(regra: Regra, inicio: datetime) -> Iterator[tuple[datetime, list[datetime]]]:
    """(começo do período, candidatos dele em ordem). Não filtra por `inicio` nem por COUNT/UNTIL."""
    n, k = regra.intervalo, 0
    if regra.freq == "HOURLY":
        h0 = inicio.replace(minute=0, second=0)
        minutos = regra.minutos or (inicio.minute,)
        while True:
            h = h0 + timedelta(hours=n * k)
            cands = []
            if (not regra.horas or h.hour in regra.horas) and _dia_passa(regra, h.date()):
                cands = [h.replace(minute=m, second=inicio.second) for m in minutos]
            yield h, cands
            k += 1
    elif regra.freq == "DAILY":
        while True:
            d = inicio.date() + timedelta(days=n * k)
            yield datetime.combine(d, time()), (_horarios(regra, inicio, d) if _dia_passa(regra, d) else [])
            k += 1
    elif regra.freq == "WEEKLY":
        segunda = inicio.date() - timedelta(days=inicio.weekday())
        dias = regra.dias_semana or (inicio.weekday(),)
        while True:
            base = segunda + timedelta(weeks=n * k)
            cands = [c for wd in dias for c in _horarios(regra, inicio, base + timedelta(days=wd))]
            yield datetime.combine(base, time()), cands
            k += 1
    else:  # MONTHLY
        while True:
            ano, mes0 = divmod(inicio.year * 12 + inicio.month - 1 + n * k, 12)
            mes, ultimo = mes0 + 1, monthrange(ano, mes0 + 1)[1]
            if regra.dias_mes:
                dias = sorted({x if x > 0 else ultimo + 1 + x for x in regra.dias_mes} & set(range(1, ultimo + 1)))
            elif regra.dias_semana:
                dias = list(range(1, ultimo + 1))
            else:
                dias = [inicio.day] if inicio.day <= ultimo else []   # 31 em abril: ignorado, não conta
            cands = [c for dia in dias if not regra.dias_semana or date(ano, mes, dia).weekday() in regra.dias_semana
                     for c in _horarios(regra, inicio, date(ano, mes, dia))]
            yield datetime(ano, mes, 1), cands
            k += 1


def _candidatos(regra: Regra, inicio: datetime) -> Iterator[datetime]:
    ultimo = inicio
    try:
        for ref, cands in _periodos(regra, inicio):
            if ref - ultimo > _JANELA_SEM_OCORRENCIA:
                return
            for c in cands:
                if c >= inicio:
                    ultimo = c
                    yield c
    except (OverflowError, ValueError):
        return   # passou do ano 9999: a regra acabou


def _sem_duplicata(regra: Regra, inicio: datetime, tz: ZoneInfo) -> Iterator[Instante]:
    """Ocorrência desviada que cai no mesmo instante de outra da regra vira uma só (fica a que não foi desviada)."""
    pendente: Instante | None = None
    for nominal in _candidatos(regra, inicio):
        try:
            inst = localizar(nominal, tz)
        except OverflowError:
            break   # a borda do ano 9999 em UTC passou do calendário: a regra acabou
        if pendente is not None and inst.utc == pendente.utc:
            if pendente.desviado and not inst.desviado:
                pendente = inst
            continue
        if pendente is not None:
            yield pendente
        pendente = inst
    if pendente is not None:
        yield pendente


def ocorrencias(regra: Regra, inicio_local: datetime, fuso: str | ZoneInfo) -> Iterator[Instante]:
    """Todas as ocorrências desde `DTSTART`, em ordem (infinita sem COUNT/UNTIL: consuma com limite)."""
    tz = carregar_fuso(fuso)
    inicio = _normalizar_inicio(inicio_local)
    ate_utc = None
    if regra.ate is not None:
        ate_utc = regra.ate.astimezone(UTC) if regra.ate.tzinfo else localizar(regra.ate, tz).utc
    emitidas = 0
    for inst in _sem_duplicata(regra, inicio, tz):
        if ate_utc is not None and inst.utc > ate_utc:
            return
        yield inst
        emitidas += 1
        if regra.contagem is not None and emitidas >= regra.contagem:
            return


def proximas(regra: Regra, inicio_local: datetime, fuso: str | ZoneInfo, *, depois_de: datetime,
             limite: int = 5) -> list[Instante]:
    """As próximas `limite` ocorrências ESTRITAMENTE depois de `depois_de` (instante aware, o relógio do chamador).
    Serve de prévia ("as próximas 5 datas") e de cálculo da próxima: `COUNT` conta desde `DTSTART`."""
    if not isinstance(depois_de, datetime) or depois_de.tzinfo is None:
        raise ErroRecorrencia("depois_de precisa ser um instante com fuso (o relógio injetado do chamador)")
    if not 1 <= limite <= LIMITE_PREVIA:
        raise ErroRecorrencia(f"limite {limite} fora do intervalo 1..{LIMITE_PREVIA}")
    saida: list[Instante] = []
    for inst in ocorrencias(regra, inicio_local, fuso):
        if inst.utc > depois_de:
            saida.append(inst)
            if len(saida) >= limite:
                break
    return saida


def proxima(regra: Regra, inicio_local: datetime, fuso: str | ZoneInfo, *, depois_de: datetime) -> Instante | None:
    achadas = proximas(regra, inicio_local, fuso, depois_de=depois_de, limite=1)
    return achadas[0] if achadas else None
