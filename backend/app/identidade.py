"""Quem responde em `/api/health` é a Farm? — a pergunta que o supervisor e os scripts fazem antes de decidir.

Existe pelo deploy de 26/09/2026: o container `cartorio-api-1` (outro projeto na mesma máquina) publica
`0.0.0.0:8000`. Com o backend da Farm parado, `127.0.0.1:8000/api/health` caiu nele e voltou `404
{"detail":"Not Found"}`; o supervisor tratava QUALQUER resposta HTTP como "backend vivo" e se recusou a subir a
Farm — a produção ficou fora do ar até alguém subir o processo à mão.

Quatro perguntas, que não se misturam:

- `service` — este HTTP é da Farm? (esta função; identidade estável, nunca o commit)
- `commit` — é a versão esperada? (o deploy confere, DEPOIS de saber que é a Farm)
- `migration` — o banco acompanha?
- `status` — o processo está ok/degradado? (degradado continua sendo a Farm viva)

Sem importar nada do app: o supervisor carrega isto antes de haver backend, e os scripts PowerShell repetem a
mesma regra em `scripts/lib/farm-health.ps1` (um teste confere que as duas falam do mesmo identificador).
"""
from __future__ import annotations

import json
from typing import Any

#: Identidade estável do backend da Farm em `/api/health`. Não muda com versão, commit ou migração.
SERVICO = "android-farm-central"

#: Chaves que só o `/api/health` da Farm anterior a `service` tinha, todas juntas. É o reconhecimento LEGADO: sem
#: ele, um supervisor novo diante de uma Farm antiga ainda no ar a tomaria por serviço estrangeiro, subiria um
#: segundo backend e teríamos dois donos do mesmo banco. `appium.port` + `sdk.found` + `ai` não aparecem por acaso
#: no health de outro serviço (o do Cartório é `{"detail":"Not Found"}`). Remover quando nenhuma instalação rodar
#: backend anterior a este campo (a Farm antiga só existe entre o supervisor novo e o primeiro restart do backend).
_LEGADO_OBRIGATORIO = ("status", "version", "ai", "appium", "sdk")


def corpo_e_da_farm(corpo: bytes | str | None) -> bool:
    """`True` só quando o corpo de `/api/health` identifica a Farm — atual (`service`) ou legada (esquema antigo).

    Qualquer outra coisa é "não é a Farm": HTML, JSON malformado, JSON de outro serviço, JSON sem identidade.
    O status HTTP não entra aqui de propósito: um 503 da Farm é a Farm viva e degradada; um 200 de outro serviço
    não é a Farm.
    """
    if not corpo:
        return False
    try:
        dados: Any = json.loads(corpo)
    except (ValueError, UnicodeDecodeError):
        return False
    if not isinstance(dados, dict):
        return False
    if "service" in dados:
        return dados.get("service") == SERVICO
    return _e_farm_legada(dados)


def _e_farm_legada(d: dict[str, Any]) -> bool:
    if not all(k in d for k in _LEGADO_OBRIGATORIO):
        return False
    appium, sdk = d.get("appium"), d.get("sdk")
    return (d.get("status") in ("ok", "degraded", "error") and isinstance(d.get("ai"), dict)
            and isinstance(appium, dict) and "port" in appium and "running" in appium
            and isinstance(sdk, dict) and "found" in sdk)
