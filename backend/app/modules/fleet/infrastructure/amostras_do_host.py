"""As amostras do host (31.180, adendo v1.102): a leitura do CSV diário do amostrador permanente (29.156,
`scripts/amostrador-host.ps1`), um arquivo por dia UTC em `<data_dir>/observabilidade/host/AAAAMMDD.csv`, uma linha por
minuto. Só leitura: nada aqui escreve, apaga nem chama o amostrador.

O que sai não leva nome de máquina nem caminho (o arquivo não tem; a resposta também não). Os nomes de processo do topo
são do host e saem como o amostrador os gravou (só o nome, sem linha de comando nem usuário). A linha de falha do
amostrador (`ts,erro,<tipo>`) vira `{ts_utc, erro}`: o buraco na série é dado, não se esconde. O formato é o contrato do
painel do Portal (`frontend/src/features/host/contratoDoHost.ts`).
"""
from __future__ import annotations

import csv
from datetime import UTC, datetime, timedelta
from pathlib import Path

#: As colunas do amostrador, na ordem do cabeçalho (29.156).
COLUNAS = ("ts_utc", "cpu_host_pct", "vm_convidado_nucleos", "vmmem_ws_mb", "qemu_host_pct", "ram_livre_mb",
           "disco_livre_gb", "processos_top", "avisos_pressao")
_NUMEROS = COLUNAS[1:7]


def pasta_das_amostras(data_dir: Path) -> Path:
    return data_dir / "observabilidade" / "host"


def _hora(texto: str) -> datetime | None:
    try:
        return datetime.strptime(texto.strip(), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def _numero(texto: str) -> float | None:
    try:
        return float(texto) if texto.strip() else None
    except ValueError:
        return None


def _pares(texto: str, chave: str, valor: str, conv: type[float] | type[int]) -> list[dict[str, object]]:
    """`nome:12.3;outro:4.1` → `[{chave: nome, valor: 12.3}, …]`. Par malformado fica de fora."""
    saida: list[dict[str, object]] = []
    for par in (p for p in texto.split(";") if p.strip()):
        nome, _, numero = par.rpartition(":")
        try:
            saida.append({chave: nome.strip(), valor: conv(numero)})
        except ValueError:
            continue
    return [p for p in saida if p[chave]]


def ler_amostras(pasta: Path, horas: int, agora: datetime) -> list[dict[str, object]] | None:
    """As amostras de `agora - horas` até `agora`, em ordem de hora. `None` = nenhum CSV dos dias da janela (a rota
    devolve 404); lista vazia = há arquivo, mas nenhuma linha na janela."""
    desde = agora - timedelta(hours=horas)
    dias = [(desde.date() + timedelta(days=i)).strftime("%Y%m%d") for i in range((agora.date() - desde.date()).days + 1)]
    arquivos = [pasta / f"{d}.csv" for d in dias if (pasta / f"{d}.csv").is_file()]
    if not arquivos:
        return None
    amostras: list[dict[str, object]] = []
    for arquivo in arquivos:
        with arquivo.open(encoding="utf-8-sig", newline="") as f:
            for linha in csv.reader(f):
                ts = _hora(linha[0]) if linha else None
                if ts is None or not desde <= ts <= agora:
                    continue                                   # cabeçalho, linha quebrada ou fora da janela
                if len(linha) > 1 and linha[1] == "erro":
                    amostras.append({"ts_utc": linha[0], "erro": linha[2] if len(linha) > 2 else ""})
                    continue
                campos = dict(zip(COLUNAS, linha + [""] * (len(COLUNAS) - len(linha))))
                amostras.append({"ts_utc": linha[0], **{c: _numero(campos[c]) for c in _NUMEROS},
                                 "processos_top": _pares(campos["processos_top"], "nome", "pct", float),
                                 "avisos_pressao": _pares(campos["avisos_pressao"], "instance_id", "n", int)})
    return sorted(amostras, key=lambda a: str(a["ts_utc"]))
