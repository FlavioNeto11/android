"""O segredo local: um arquivo em `data/` que prova "eu rodo NESTA máquina, como alguém que administra ela".

Existe por causa de uma pergunta que `request.client.host` não responde mais. `POST /api/admin/shutdown` aceitava
qualquer chamada cujo par fosse `127.0.0.1` ou `::1`, e isso bastava enquanto loopback significava "esta máquina".
Com o túnel SSH reverso deixou de significar: toda conexão que chega pelo `-R` tem par `127.0.0.1` de verdade, então
qualquer processo da máquina do worker — job de CI, usuário local não-administrador — derrubava o backend do
central. O listener dedicado de `main.create_worker_app` tira a rota REST do alcance do túnel; este arquivo é a
segunda tranca, para o caso de outro caminho de loopback aparecer depois.

Não é autenticação de usuário e não tenta ser: é uma prova de acesso ao disco da máquina, com a mesma força que a
ACL do arquivo tiver. Quem já lê `data/` também lê o banco e as credenciais cifradas — o segredo não protege contra
esse, protege contra o processo que só tem o socket.

Gerado no start e REGRAVADO a cada subida: um segredo velho que vazou para o log de alguém deixa de servir quando o
backend reinicia, e o `stop.ps1` lê o arquivo na hora de usar, então nada quebra.
"""
from __future__ import annotations

import getpass
import logging
import os
import secrets
import subprocess
from pathlib import Path

log = logging.getLogger("poc.security")

ARQUIVO = "shutdown.token"

#: Cabeçalho que carrega o segredo. Não é `Authorization` de propósito: `Authorization` é o token da API, que
#: responde outra pergunta ("quem é você na rede"), e misturar os dois faria um servir de oráculo para o outro.
CABECALHO = "X-Shutdown-Token"


def caminho_de(data_dir: Path) -> Path:
    return data_dir / ARQUIVO


def garantir(data_dir: Path) -> str:
    """Cria (ou regrava) o segredo e devolve o valor. Nunca é logado nem devolvido por rota nenhuma."""
    data_dir.mkdir(parents=True, exist_ok=True)
    caminho = caminho_de(data_dir)
    valor = secrets.token_urlsafe(32)
    caminho.write_text(valor, encoding="utf-8")
    restringir_acesso(caminho)
    return valor


def ler(data_dir: Path) -> str | None:
    try:
        valor = caminho_de(data_dir).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return valor or None


def restringir_acesso(caminho: Path) -> bool:
    """Deixa o arquivo legível só para quem administra a máquina. `False` quando não conseguiu (o chamador segue
    em frente — o segredo já vale; o que se perde é a tranca de disco, e isso vira erro no log).

    `/inheritance:r` antes de `/grant:r` porque `/grant` sozinho só ACRESCENTA: o `BUILTIN\\Users:(I)(RX)` herdado
    da pasta continuaria valendo. Contas por **SID**, não por nome: `Administrators` se chama outra coisa em Windows
    não-inglês, e a tranca falharia calada justamente onde ninguém testa.
    """
    if os.name != "nt":
        try:
            caminho.chmod(0o600)
        except OSError as exc:
            log.error("não foi possível restringir %s: %s", caminho.name, exc)
            return False
        return True
    contas = ["*S-1-5-18:F", "*S-1-5-32-544:F"]          # SYSTEM, BUILTIN\Administrators
    try:
        contas.append(f"{getpass.getuser()}:F")
    except Exception:  # noqa: BLE001
        pass
    argumentos = [str(caminho), "/inheritance:r"]
    for conta in contas:
        argumentos += ["/grant:r", conta]
    try:
        r = subprocess.run(["icacls", *argumentos], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        log.error("não foi possível restringir a ACL de %s: %s", caminho.name, exc)
        return False
    if r.returncode != 0:
        log.error("icacls devolveu %s ao restringir %s", r.returncode, caminho.name)
        return False
    return True


def confere(data_dir: Path, recebido: str | None) -> bool:
    """`compare_digest` porque a comparação ingênua conta quantos bytes o atacante acertou pelo tempo que leva."""
    esperado = ler(data_dir)
    if not esperado or not recebido:
        return False
    return secrets.compare_digest(recebido.strip().encode("utf-8", "surrogatepass"),
                                  esperado.encode("utf-8", "surrogatepass"))
