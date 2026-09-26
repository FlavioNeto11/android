"""Partida do central no contêiner: confere a configuração EXPLÍCITA e só então vira `python -m app.main`.

Existe por uma armadilha de `app/config.py::config_file_path`: sem o arquivo apontado por `POC_CONFIG`, o backend
cai no `<nome>.example.yaml` ao lado e, sem ele, nos PADRÕES do código — 10 instâncias que ninguém escreveu,
`sdk_root` do Windows. No checkout isso é o certo (primeira partida sem atrito). No contêiner seria subir calado
com a configuração errada porque o bind mount do `config.conteiner.yaml` faltou. Aqui a falta vira recusa, com o
motivo, antes de abrir o banco (nenhuma migração roda).

Saída 78 (`EX_CONFIG`): erro de configuração, não de execução. O compose limita as novas tentativas
(`restart: on-failure:3`), então a recusa não vira ciclo infinito — são três linhas iguais no log e para.

`os.execv` e não `subprocess`: o backend passa a SER este processo, e o sinal de parada do Docker (via `init`)
chega direto nele — o encerramento gracioso do uvicorn é o que impede dois donos do mesmo banco.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Mapping

#: Onde a imagem roda o backend (`app.main` resolve a raiz do projeto a partir do próprio arquivo).
BACKEND = Path(__file__).resolve().parents[1] / "backend"


def conferir(ambiente: Mapping[str, str]) -> str | None:
    """`None` quando dá para subir; senão, a frase que explica o que falta."""
    caminho = (ambiente.get("POC_CONFIG") or "").strip()
    if not caminho:
        return "POC_CONFIG não definido: a imagem espera o config montado em /app/config/config.yaml."
    alvo = Path(caminho)
    if not alvo.is_absolute():
        return f"POC_CONFIG={caminho} precisa ser absoluto no contêiner (ex.: /app/config/config.yaml)."
    if not alvo.is_file():
        return (f"POC_CONFIG={caminho} não existe no contêiner. Monte deploy/config.conteiner.yaml ali, somente "
                "leitura (deploy/compose.yaml faz isso). Sem ele o backend cairia calado nos padrões do código.")
    return None


def main() -> None:
    motivo = conferir(os.environ)
    if motivo:
        print(f"recusa de partida: {motivo}", file=sys.stderr)
        sys.exit(78)
    os.chdir(BACKEND)
    os.execv(sys.executable, [sys.executable, "-m", "app.main"])


if __name__ == "__main__":
    main()
