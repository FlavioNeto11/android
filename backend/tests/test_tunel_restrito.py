"""Item 9.4 — achado #123: a chave do túnel dava shell de Administrator em cada worker.

O que é testado aqui é o CONTRATO dos dois scripts, porque é nele que o achado vive:

* `worker-tunnel.ps1` (roda no central) não pode mais pedir a conta `Administrator` nem aceitar chave de host
  desconhecida — `accept-new` é confiança na primeira conexão, e é justamente nessa conexão que a credencial
  permanente do agente atravessa o `-R`.
* `worker-ssh-restrito.ps1` (roda no worker) tem de gerar uma linha de `authorized_keys` que encaminhe portas e
  NADA mais. `restrict` sozinho não basta: ele tira pty, agente e X11, mas a chave continua executando comando —
  por isso a asserção do `command=`. E `port-forwarding` precisa vir DEPOIS do `restrict`, senão o túnel não sobe.

O `-Simular` é o que torna isto testável sem tocar em conta local, `sshd_config` nem serviço: ele imprime
exatamente o que gravaria. Sem pwsh no PATH, os testes que o usam se pulam sozinhos (mesmo hábito de
`test_backup.py`), e as asserções de conteúdo do script seguem valendo em qualquer máquina.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
SCRIPTS = RAIZ / "scripts"
TUNEL = SCRIPTS / "worker-tunnel.ps1"
RESTRITO = SCRIPTS / "worker-ssh-restrito.ps1"

precisa_pwsh = pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh não está no PATH")


def _simular(*args: str) -> str:
    r = subprocess.run(["pwsh", "-NoProfile", "-File", str(RESTRITO), "-Simular", *args],
                       capture_output=True, text=True, timeout=120, cwd=str(RAIZ))
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


def test_o_tunel_nao_pede_mais_a_conta_de_administrador() -> None:
    texto = TUNEL.read_text(encoding="utf-8")
    assert "[string]$Usuario  = 'farm-tunel'" in texto
    assert "$Usuario  = 'Administrator'" not in texto


def test_o_tunel_recusa_chave_de_host_desconhecida() -> None:
    texto = TUNEL.read_text(encoding="utf-8")
    assert "'StrictHostKeyChecking=yes'" in texto
    # Só linhas de CÓDIGO: `accept-new` segue citado no comentário que explica por que ele saiu.
    codigo = [l for l in texto.splitlines() if not l.strip().startswith("#")]
    assert not [l for l in codigo if "accept-new" in l], codigo
    assert "-RegistrarChaveDeHost" in texto and "ssh-keyscan" in texto
    assert "Test-ChaveDeHostConhecida" in texto                     # pré-voo antes do laço, com mensagem própria


@precisa_pwsh
def test_a_linha_autorizada_so_encaminha_as_portas_pedidas() -> None:
    saida = _simular("-ChavePublica", "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIabcdefghijklmnopqrstuvwxyz0123456789+ central",
                     "-PortasAdb", "5555,5557", "-PortaReversa", "18000")
    linha = next(l for l in saida.splitlines() if l.startswith("restrict"))

    assert linha.startswith("restrict,port-forwarding,")          # nega tudo, reabre só o encaminhamento
    assert 'permitopen="127.0.0.1:5555"' in linha
    assert 'permitopen="127.0.0.1:5557"' in linha
    # A porta SOZINHA é a forma que faz o túnel subir: `-R 18000:127.0.0.1:8010` sem endereço de bind viaja como
    # o nome `localhost`, que o sshd trata como diferente de `127.0.0.1`. Sem ela, e com `ExitOnForwardFailure`,
    # o encaminhamento seria recusado e o túnel nunca subiria — depois de já se ter tirado o acesso antigo.
    assert 'permitlisten="18000"' in linha
    assert 'permitlisten="127.0.0.1:18000"' in linha
    assert 'command="exit"' in linha                              # sem isto a chave ainda roda comando
    assert linha.endswith(" central") and "ssh-ed25519 AAAAC3" in linha
    # Nenhuma outra porta entra de carona: a de outro aparelho não pedido não pode estar na linha.
    assert "5559" not in linha


@precisa_pwsh
def test_sem_lista_de_portas_o_limite_continua_sendo_loopback_do_worker() -> None:
    """`127.0.0.1:*` é o padrão porque a porta de ADB é uma por aparelho e o parque cresce sem reeditar o sshd.
    O que NÃO pode acontecer é o curinga virar endereço: `*:*` abriria a LAN inteira do worker."""
    saida = _simular("-ChavePublica", "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIabcdefghijklmnopqrstuvwxyz0123456789+ central")
    linha = next(l for l in saida.splitlines() if l.startswith("restrict"))
    assert 'permitopen="127.0.0.1:*"' in linha
    assert 'permitopen="*' not in linha


@precisa_pwsh
def test_o_bloco_do_sshd_tira_shell_pty_e_agente() -> None:
    saida = _simular("-ChavePublica", "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIabcdefghijklmnopqrstuvwxyz0123456789+ central",
                     "-Usuario", "farm-tunel")
    assert "Match User farm-tunel" in saida
    for diretiva in ("AuthorizedKeysFile __PROGRAMDATA__/ssh/authorized_keys/%u",   # conta sem perfil ainda
                     "AllowTcpForwarding yes", "PermitTTY no", "AllowAgentForwarding no",
                     "X11Forwarding no", "ForceCommand exit",
                     # Porta sozinha: o `PermitListen` do admin e o `permitlisten` da chave precisam AMBOS
                     # aceitar o pedido, e o pedido chega como `localhost`, não como `127.0.0.1`.
                     "PermitListen 18000"):
        assert diretiva in saida, diretiva
    assert "PermitListen 127.0.0.1:18000" not in saida


@precisa_pwsh
def test_chave_privada_ou_lixo_e_recusado_antes_de_gravar_qualquer_coisa() -> None:
    """Passar a chave errada é o erro de digitação mais provável do procedimento — e o mais caro, porque
    gravaria a chave PRIVADA do central num arquivo do worker."""
    for ruim in ("-----BEGIN OPENSSH PRIVATE KEY-----", "nao-e-chave", ""):
        r = subprocess.run(["pwsh", "-NoProfile", "-File", str(RESTRITO), "-Simular", "-ChavePublica", ruim],
                           capture_output=True, text=True, timeout=120, cwd=str(RAIZ))
        assert r.returncode != 0, f"aceitou '{ruim[:20]}'"
        assert "restrict," not in r.stdout
