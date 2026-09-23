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


# ---------------------------------------------------------------------------------------------- item 10.1
# Achados #138 e #181, os dois na instalação da tarefa do túnel:
#
# * #138 — a ação registrada guardava `C:\Program Files\WindowsApps\Microsoft.PowerShell_7.6.6.0_..._x64\pwsh.exe`.
#   O caminho do pacote MSIX carrega a VERSÃO e some na próxima atualização da Store: no boot seguinte a tarefa
#   aponta para um executável que não existe, e caem juntos os seis aparelhos remotos e o canal do agente.
# * #181 — `-Instalar` matava o laço de reconexão de TODOS os workers, porque filtrava só por `worker-tunnel.ps1`.
#
# O `-Simular` é o que torna isto testável sem registrar tarefa nem matar processo nesta máquina.

def _instalar_simulado(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["pwsh", "-NoProfile", "-File", str(TUNEL), "-Instalar", "-Simular", *args],
                          capture_output=True, text=True, timeout=180, cwd=str(RAIZ))


@precisa_pwsh
def test_instalar_recusa_o_pwsh_da_store_e_diz_como_resolver() -> None:
    r"""No central medido, `(Get-Command pwsh).Source` É o pacote da Store — então esta recusa acontece de fato
    aqui. Numa máquina com o MSI em `C:\Program Files\PowerShell\7`, o caminho estável ganha e a instalação
    segue: os dois desfechos são corretos, e o que NÃO pode é registrar o caminho versionado em silêncio."""
    r = _instalar_simulado("-Worker", "203.0.113.9", "-Mapa", "45555:5555")
    if r.returncode != 0:
        assert "Microsoft.PowerShell" in (r.stdout + r.stderr)
        assert "winget install --id Microsoft.PowerShell" in (r.stdout + r.stderr)
    else:
        executavel = next(l for l in r.stdout.splitlines() if l.startswith("executavel: "))
        assert "WindowsApps" not in executavel, executavel


@precisa_pwsh
def test_instalar_recusa_porta_local_ja_usada_por_outro_tunel() -> None:
    """Aceite 5 (dois workers): o `-Mapa` é digitado à mão, e dois workers com o padrão pedem as mesmas 15555/15557.
    `ExitOnForwardFailure=yes` faz o segundo túnel morrer na largada, e o painel mostra só 'worker offline'."""
    r = _instalar_simulado("-AceitarStore", "-Worker", "203.0.113.9", "-Mapa", "15555:5555",
                           "-MapaDeOutrosTuneis", "farm-tunel-192.168.1.19=15555:5555,15557:5557")
    assert r.returncode != 0
    assert "colisao de portas locais" in (r.stdout + r.stderr)
    assert "farm-tunel-192.168.1.19" in (r.stdout + r.stderr)

    ok = _instalar_simulado("-AceitarStore", "-Worker", "203.0.113.9", "-Mapa", "45555:5555",
                            "-MapaDeOutrosTuneis", "farm-tunel-192.168.1.19=15555:5555,15557:5557")
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "colisao: nenhuma" in ok.stdout
    assert "simulacao: nada foi registrado nem encerrado" in ok.stdout


def test_o_encerramento_do_laco_anterior_e_filtrado_pelo_worker() -> None:
    texto = TUNEL.read_text(encoding="utf-8")
    linha = next(l for l in texto.splitlines() if r"worker-tunnel\.ps1" in l and "-match" in l)
    # Antes: `-match 'worker-tunnel\.ps1'` e mais nada — matava o laço de qualquer worker.
    assert "$meu" in linha, linha


@precisa_pwsh
def test_o_filtro_do_laco_nao_confunde_um_ip_com_o_prefixo_de_outro() -> None:
    """`192.168.1.1` não pode casar com a linha de comando do túnel de `192.168.1.19`: instalar o primeiro
    derrubaria o segundo. A expressão exercitada aqui é a DO SCRIPT, extraída do arquivo."""
    texto = TUNEL.read_text(encoding="utf-8")
    expressao = next(l.strip() for l in texto.splitlines() if l.strip().startswith("$meu = "))
    cmdline = (r'pwsh.exe -NoProfile -File "C:\git\android\scripts\worker-tunnel.ps1" '
               '-Worker 192.168.1.19 -Usuario farm-tunel')
    def casa(worker: str) -> bool:
        script = f"$Worker = '{worker}'; {expressao}; if ('{cmdline}' -match $meu) {{ 'sim' }} else {{ 'nao' }}"
        r = subprocess.run(["pwsh", "-NoProfile", "-Command", script],
                           capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, r.stdout + r.stderr
        return r.stdout.strip() == "sim"

    assert casa("192.168.1.19")
    assert not casa("192.168.1.1")
