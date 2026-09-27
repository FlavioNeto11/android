"""A sonda de internet DENTRO do aparelho: o comando e a leitura da saída, sem classificar nada.

Separada de `devices/conectividade.py` por causa do agente do worker. `Adb.connectivity_probe` precisa só do
comando e da leitura; a classificação devolve `models.ConnectivityInfo`, e `models.py` não vai para a máquina do
worker. Enquanto as duas coisas moravam juntas, o `adb.py` importava `conectividade` dentro da função para não
arrastar `models` — e o agente instalado dava `ModuleNotFoundError: app.models` no dia em que algum caminho dele
chamasse a sonda (relatório 04 §1.1). Aqui só há stdlib: o módulo vai no manifesto do agente
(`backend/worker-manifest.txt`) e o `adb.py` o importa no topo.

O que cada linha da sonda pergunta está em `devices/conectividade.py`.
"""
from __future__ import annotations

#: O host que o próprio Android usa para validar rede. Neutro, sem conta, e o que falha primeiro sem DNS.
HOST_DE_TESTE = "connectivitycheck.gstatic.com"


def comando_sonda(host: str = HOST_DE_TESTE) -> str:
    # DNS e TCP têm uma segunda tentativa. Medido no android-06 logo depois do reset (load 26 em 2 vCPU): a sonda
    # deu TCP 443 falho às 21:16:39 e o mesmo `nc` passou 3/3 em 1 s um minuto depois. Um tropeço sob carga não
    # pode virar "sem internet" no cartão.
    return (
        "echo R=$(ip route show table all 2>/dev/null | grep '^default' | grep -v dummy0 | grep -c .); "
        "echo V=$(dumpsys connectivity 2>/dev/null | grep -E 'NetworkAgentInfo[{]' | grep -c VALIDATED); "
        f"if ping -c1 -W3 {host} 2>&1 | grep -q '^PING' || {{ sleep 2; ping -c1 -W3 {host} 2>&1 | grep -q '^PING'; }}; "
        "then echo D=1; else echo D=0; fi; "
        f"if {{ echo | timeout 8 nc {host} 443; }} >/dev/null 2>&1 || {{ sleep 2; echo | timeout 8 nc {host} 443; }} "
        ">/dev/null 2>&1; then echo T=1; else echo T=0; fi"
    )


def ler_sonda(saida: str) -> dict[str, bool]:
    """`R=n V=n D=0|1 T=0|1` → booleanos. Levanta `ValueError` se faltar alguma resposta (não dá para saber)."""
    vals: dict[str, str] = {}
    for linha in (saida or "").splitlines():
        chave, _, valor = linha.strip().partition("=")
        if chave in ("R", "V", "D", "T") and valor.strip().isdigit():
            vals[chave] = valor.strip()
    if set(vals) != {"R", "V", "D", "T"}:
        raise ValueError(f"sonda de conectividade incompleta: {sorted(vals)}")
    return {"route": int(vals["R"]) > 0, "validated": int(vals["V"]) > 0, "dns": vals["D"] == "1",
            "tcp_443": vals["T"] == "1"}
