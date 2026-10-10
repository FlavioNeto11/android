#!/usr/bin/env python3
"""31.320: o laço de eventos parou? Quanto, e em que hora (UTC)? Para comparar o deploy 71 x 72 x 73 (com a conexão por thread).

SOMENTE LEITURA. Lê os logs do backend (`data/logs/backend.log` e os rotacionados `backend.log.AAAA-MM-DD`) e os despejos do vigia
(`data/logs/laco-travado-*.txt`); não abre o banco, não chama IA, não toca aparelho.

O que existe para medir (e o limite):
- o vigia (`backend/app/vigia_do_laco.py`) só registra o laço parado a partir de 10 s: "sem batida há X s" e "voltou a bater depois de X s parado".
  Um atraso de 1 a 10 s NÃO aparece aqui;
- o `Database` (31.307) avisa a consulta síncrona que a thread do laço esperou mais de 1 s, no máximo um aviso a cada 5 s, com o chamador;
  desde o 31.320 o aviso diz também quem SEGURAVA a trava de escrita, e uma posse acima de 2 s tem aviso à parte. O número de avisos é um PISO.

Uso (da raiz do repositório):

    python scripts/laco-por-hora.py                                   # tudo o que os logs têm
    python scripts/laco-por-hora.py --desde 2026-10-10T00:00:00Z --ate 2026-10-11T00:00:00Z
    python scripts/laco-por-hora.py --marco 71=2026-10-10T18:35:00Z --marco 72=2026-10-11T12:00:00Z --marco 73=2026-10-12T12:00:00Z

Cada `--marco NOME=INSTANTE` abre um período que vai até o marco seguinte (o primeiro período, "antes", vai do começo dos logs ao
primeiro marco). A tabela por período traz os episódios, o maior tempo parado, o total parado, as horas cobertas e os episódios por hora
coberta (a taxa que compara períodos de tamanhos diferentes).

O horário dos logs do backend é o LOCAL da máquina (sem fuso na linha); o do nome do despejo é UTC. `--fuso-log` (horas em relação ao UTC)
vale para as linhas dos logs; o padrão é o fuso da máquina agora.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]

_LINHA = re.compile(r'^\{"ts":"(?P<ts>[^"]+)","level":"(?P<nivel>\w+)","logger":"(?P<logger>[\w.]+)","msg":(?P<msg>.*)\}\s*$')
_SEM_BATIDA = re.compile(r"laço de eventos sem batida há (?P<s>[\d.]+) s \((?P<fase>[^)]+)\)")
_VOLTOU = re.compile(r"laço de eventos voltou a bater depois de (?P<s>[\d.]+) s parado")
_CONSULTA = re.compile(r"consulta síncrona no laço de eventos levou (?P<s>[\d.]+) s \((?P<onde>[^)]*)\)")
_POSSE = re.compile(r"a trava de escrita do banco ficou presa (?P<s>[\d.]+) s por (?P<quem>.+?) \((?P<onde>[^)]*)\)")
_CONEXOES = re.compile(r"(?P<n>\d+) conexões abertas com o banco")
_DESPEJO = re.compile(r"^laco-travado-(?P<ts>\d{8}T\d{6}Z)-(?P<n>\d+)\.txt$")
_ATRASO_DO_DESPEJO = re.compile(r"sem batida há (?P<s>[\d.]+) s")


def instante(texto: str) -> datetime:
    """`2026-10-10T18:35:00Z` (ou sem o `Z`, ou com offset) -> datetime com fuso."""
    t = texto.strip()
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    d = datetime.fromisoformat(t)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


@dataclass
class Hora:
    parado: list[float] = field(default_factory=list)       # duração de cada episódio (voltou a bater)
    sem_batida: list[float] = field(default_factory=list)   # atraso visto no despejo (quando o "voltou" não está nos logs)
    consultas: list[float] = field(default_factory=list)
    posses: list[float] = field(default_factory=list)
    onde: Counter[str] = field(default_factory=Counter)
    quem: Counter[str] = field(default_factory=Counter)


def _hora(d: datetime) -> datetime:
    return d.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def _arquivos_de_log(pasta: Path) -> list[Path]:
    achados = [p for p in pasta.glob("backend.log*") if re.fullmatch(r"backend\.log(\.\d{4}-\d{2}-\d{2})?", p.name)]
    return sorted(achados)


def ler_logs(pasta: Path, fuso_log: timedelta, horas: dict[datetime, Hora], eventos: list[tuple[datetime, str, float]]) -> int:
    lidas = 0
    for arq in _arquivos_de_log(pasta):
        with arq.open(encoding="utf-8", errors="replace") as f:
            for linha in f:
                m = _LINHA.match(linha)
                if not m or m["logger"] not in ("poc.vigia", "poc.db"):
                    continue
                msg = m["msg"].strip()
                try:
                    quando = datetime.strptime(m["ts"][:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone(fuso_log)).astimezone(timezone.utc)
                except ValueError:
                    continue
                lidas += 1
                h = horas[_hora(quando)]
                if (v := _VOLTOU.search(msg)):
                    h.parado.append(float(v["s"]))
                    eventos.append((quando, "parado", float(v["s"])))
                elif (v := _CONSULTA.search(msg)):
                    h.consultas.append(float(v["s"]))
                    h.onde[v["onde"]] += 1
                    eventos.append((quando, "consulta", float(v["s"])))
                elif (v := _POSSE.search(msg)):
                    h.posses.append(float(v["s"]))
                    h.quem[f'{v["quem"]} ({v["onde"]})'] += 1
                    eventos.append((quando, "posse", float(v["s"])))
    return lidas


def ler_despejos(pasta: Path, horas: dict[datetime, Hora], ja_com_volta: set[datetime]) -> int:
    """Os despejos têm o horário em UTC no nome. Servem de piso quando o log do backend já rotacionou (a cota de despejos é 20)."""
    n = 0
    for arq in sorted(pasta.glob("laco-travado-*.txt")):
        m = _DESPEJO.match(arq.name)
        if not m:
            continue
        quando = datetime.strptime(m["ts"], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        n += 1
        if _hora(quando) in ja_com_volta:
            continue
        try:
            primeira = arq.read_text(encoding="utf-8", errors="replace").splitlines()[0]
        except (OSError, IndexError):
            continue
        v = _ATRASO_DO_DESPEJO.search(primeira)
        if v:
            horas[_hora(quando)].sem_batida.append(float(v["s"]))
    return n


def _resumo(h: Hora) -> tuple[int, float, float]:
    duracoes = h.parado or h.sem_batida
    return len(duracoes), (max(duracoes) if duracoes else 0.0), sum(duracoes)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--logs", type=Path, default=RAIZ / "data" / "logs", help="pasta dos logs (padrão: data/logs)")
    ap.add_argument("--desde", type=instante, help="ISO, UTC se sem fuso")
    ap.add_argument("--ate", type=instante, help="ISO, UTC se sem fuso")
    ap.add_argument("--marco", action="append", default=[], metavar="NOME=INSTANTE", help="início de um período (repetível)")
    ap.add_argument("--fuso-log", type=float, default=None, help="horas em relação ao UTC do horário das linhas do log (padrão: o da máquina)")
    a = ap.parse_args(argv)
    if not a.logs.is_dir():
        print(f"pasta de logs inexistente: {a.logs}", file=sys.stderr)
        return 2
    fuso = timedelta(hours=a.fuso_log) if a.fuso_log is not None else (datetime.now().astimezone().utcoffset() or timedelta(0))
    marcos: list[tuple[str, datetime]] = []
    for m in a.marco:
        nome, _, quando = m.partition("=")
        if not nome or not quando:
            print(f"--marco inválido: {m!r} (use NOME=INSTANTE)", file=sys.stderr)
            return 2
        marcos.append((nome, instante(quando)))
    marcos.sort(key=lambda x: x[1])

    horas: dict[datetime, Hora] = defaultdict(Hora)
    eventos: list[tuple[datetime, str, float]] = []
    linhas = ler_logs(a.logs, fuso, horas, eventos)
    com_volta = {h for h, v in horas.items() if v.parado}
    despejos = ler_despejos(a.logs, horas, com_volta)
    horas_ordenadas = sorted(h for h in horas if (a.desde is None or h >= _hora(a.desde)) and (a.ate is None or h < a.ate))
    if not horas_ordenadas:
        print(f"Nada nos logs (linhas do vigia e do banco lidas: {linhas}; despejos: {despejos}).")
        return 0

    print(f"# O laço de eventos por hora (UTC)\n\nLogs em `{a.logs}`: {linhas} linhas do vigia e do banco, {despejos} despejos. "
          f"Fuso das linhas do log: UTC{fuso.total_seconds() / 3600:+.0f}.\n")
    print("| hora (UTC) | episódios ≥ 10 s | maior parado (s) | total parado (s) | consultas > 1 s no laço | maior consulta (s) | posses > 2 s | maior posse (s) |")
    print("|---|---|---|---|---|---|---|---|")
    for h in horas_ordenadas:
        v = horas[h]
        n, maior, total = _resumo(v)
        print(f"| {h:%Y-%m-%d %H}h | {n} | {maior:.1f} | {total:.1f} | {len(v.consultas)} | {max(v.consultas, default=0):.1f} | "
              f"{len(v.posses)} | {max(v.posses, default=0):.1f} |")

    if marcos:
        print("\n## Por período (cada marco abre um período até o seguinte)\n")
        print("| período | de | até | horas cobertas | episódios ≥ 10 s | maior parado (s) | total parado (s) | episódios por hora | consultas > 1 s | posses > 2 s |")
        print("|---|---|---|---|---|---|---|---|---|---|")
        limites = [("antes", horas_ordenadas[0], marcos[0][1])] + [
            (nome, quando, marcos[i + 1][1] if i + 1 < len(marcos) else (a.ate or max(horas_ordenadas) + timedelta(hours=1)))
            for i, (nome, quando) in enumerate(marcos)]
        for nome, ini, fim in limites:
            if fim <= ini:
                continue
            hs = [h for h in horas_ordenadas if ini <= h + timedelta(minutes=30) < fim]
            cobertas = (fim - ini).total_seconds() / 3600
            n = sum(_resumo(horas[h])[0] for h in hs)
            maior = max((_resumo(horas[h])[1] for h in hs), default=0.0)
            total = sum(_resumo(horas[h])[2] for h in hs)
            cons = sum(len(horas[h].consultas) for h in hs)
            pos = sum(len(horas[h].posses) for h in hs)
            print(f"| {nome} | {ini:%m-%d %H:%M} | {fim:%m-%d %H:%M} | {cobertas:.1f} | {n} | {maior:.1f} | {total:.1f} | "
                  f"{(n / cobertas if cobertas else 0):.2f} | {cons} | {pos} |")

    onde: Counter[str] = Counter()
    quem: Counter[str] = Counter()
    for h in horas_ordenadas:
        onde.update(horas[h].onde)
        quem.update(horas[h].quem)
    if onde:
        print("\n## Quem esperou (consulta síncrona no laço, por chamador)\n")
        for k, n in onde.most_common(8):
            print(f"- {n}× {k}")
    if quem:
        print("\n## Quem segurou a trava de escrita (posse > 2 s)\n")
        for k, n in quem.most_common(8):
            print(f"- {n}× {k}")
    print("\nLimites: o vigia só registra a partir de 10 s e o aviso de consulta é de no máximo um por 5 s; a contagem é um piso.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
