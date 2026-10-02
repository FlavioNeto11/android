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

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
SCRIPTS = RAIZ / "scripts"
TUNEL = SCRIPTS / "worker-tunnel.ps1"
RESTRITO = SCRIPTS / "worker-ssh-restrito.ps1"

# Estes scripts são do Windows (tarefa agendada, %LOCALAPPDATA%, contas locais): o pwsh do Linux do CI existe,
# mas quebra nos caminhos do Windows antes de chegar ao que o teste prova (backlog B13).
precisa_pwsh = pytest.mark.skipif(shutil.which("pwsh") is None or os.name != "nt",
                              reason="pwsh no Windows é pré-requisito destes scripts")


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
def test_instalar_nunca_registra_o_pwsh_da_store() -> None:
    r"""No central medido, `(Get-Command pwsh).Source` É o pacote da Store. Desde o A8 o instalador não recusa
    mais por isso: cai no Windows PowerShell 5.1 do sistema (ou no MSI, se houver). O que NÃO pode é registrar o
    caminho versionado do MSIX, que some quando a Store atualiza o pacote."""
    r = _instalar_simulado("-Worker", "203.0.113.9", "-Mapa", "45555:5555",
                           "-MapaDeOutrosTuneis", "farm-tunel-192.168.1.19=15555:5555")
    if r.returncode != 0:                 # sem MSI e sem 5.1: falha clara, jamais a Store
        assert "winget install --id Microsoft.PowerShell" in (r.stdout + r.stderr)
    else:
        executavel = next(l for l in r.stdout.splitlines() if l.startswith("executavel: "))
        assert "WindowsApps" not in executavel, executavel
        assert next(l for l in r.stdout.splitlines() if l.startswith("interpretador: ")).split(": ")[1] in (
            "pwsh7", "pwsh", "powershell51")


@precisa_pwsh
def test_instalar_recusa_porta_local_ja_usada_por_outro_tunel() -> None:
    """Aceite 5 (dois workers): o `-Mapa` é digitado à mão, e dois workers com o padrão pedem as mesmas 15555/15557.
    `ExitOnForwardFailure=yes` faz o segundo túnel morrer na largada, e o painel mostra só 'worker offline'."""
    r = _instalar_simulado("-Worker", "203.0.113.9", "-Mapa", "15555:5555",
                           "-MapaDeOutrosTuneis", "farm-tunel-192.168.1.19=15555:5555,15557:5557")
    assert r.returncode != 0
    assert "colisao de portas locais" in (r.stdout + r.stderr)
    assert "farm-tunel-192.168.1.19" in (r.stdout + r.stderr)

    ok = _instalar_simulado("-Worker", "203.0.113.9", "-Mapa", "45555:5555",
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


# ---------------------------------------------------------------------------------------------- A8 (02/10/2026)
# A tarefa `farm-tunel-192.168.1.11` foi registrada com `-AceitarStore` no pwsh do MSIX e deixou de subir quando a
# Store atualizou o pacote. Contrato novo: NENHUM caminho em `\WindowsApps\` vira ação da tarefa; a ordem é
# MSI estável -> outro pwsh fora do WindowsApps -> Windows PowerShell 5.1 -> falha clara.

precisa_ps51 = pytest.mark.skipif(shutil.which("powershell") is None or os.name != "nt",
                                  reason="Windows PowerShell 5.1 é pré-requisito deste teste")
_STORE = r"WindowsApps\Microsoft.PowerShell_7.6.6.0_x64__8wekyb3d8bbwe\pwsh.exe"


def _funcao_interpretador() -> str:
    """O texto REAL de `Resolve-Interpretador`, extraído do script (termina na primeira `}` de coluna 0)."""
    linhas = TUNEL.read_text(encoding="utf-8").splitlines()
    inicio = next(i for i, l in enumerate(linhas) if l.startswith("function Resolve-Interpretador"))
    fim = next(i for i in range(inicio + 1, len(linhas)) if linhas[i].startswith("}"))
    return "\n".join(linhas[inicio:fim + 1])


def _falso(base: Path, relativo: str, existe: bool = True) -> str:
    caminho = base / relativo
    if existe:
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text("", encoding="utf-8")
    return str(caminho)


def _escolher(base: Path, shell: str, estavel: str, outros: list[str], ps51: str) -> subprocess.CompletedProcess[str]:
    """Roda a função extraída com caminhos FALSOS (arquivos vazios em `base`); não toca nada da máquina."""
    lista = ",".join("'" + o + "'" for o in outros)
    script = base / "escolher.ps1"
    script.write_text(
        _funcao_interpretador() + "\n$ErrorActionPreference = 'Stop'\n"
        f"$r = Resolve-Interpretador -Estavel '{estavel}' -OutrosPwsh @({lista}) -WindowsPowerShell '{ps51}'\n"
        "Write-Output \"$($r.Tipo)|$($r.Caminho)\"\n", encoding="utf-8-sig")
    return subprocess.run([shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                          capture_output=True, text=True, timeout=120)


precisa_shell = pytest.mark.skipif(os.name != "nt" or shutil.which("pwsh") is None,
                                   reason="PowerShell do Windows é pré-requisito")


@precisa_shell
@pytest.mark.parametrize("shell", ["pwsh", "powershell"])
def test_interpretador_escolhe_o_7_estavel_quando_existe(tmp_path: Path, shell: str) -> None:
    if shutil.which(shell) is None:
        pytest.skip(f"{shell} ausente")
    estavel = _falso(tmp_path, r"PF\PowerShell\7\pwsh.exe")
    store = _falso(tmp_path, _STORE)
    ps51 = _falso(tmp_path, r"sys\powershell.exe")
    r = _escolher(tmp_path, shell, estavel, [store], ps51)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.strip() == f"pwsh7|{estavel}"


@precisa_shell
@pytest.mark.parametrize("shell", ["pwsh", "powershell"])
def test_interpretador_pula_a_store_e_usa_outro_pwsh(tmp_path: Path, shell: str) -> None:
    if shutil.which(shell) is None:
        pytest.skip(f"{shell} ausente")
    store = _falso(tmp_path, _STORE)
    outro = _falso(tmp_path, r"ferramentas\pwsh\pwsh.exe")
    ps51 = _falso(tmp_path, r"sys\powershell.exe")
    r = _escolher(tmp_path, shell, _falso(tmp_path, r"PF\ausente.exe", existe=False), [store, outro], ps51)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.strip() == f"pwsh|{outro}"            # a Store veio PRIMEIRA na lista e foi ignorada


@precisa_shell
@pytest.mark.parametrize("shell", ["pwsh", "powershell"])
def test_interpretador_cai_no_windows_powershell_5_1_quando_so_ha_a_store(tmp_path: Path, shell: str) -> None:
    """É o caso do central medido: o único pwsh é o do MSIX. Antes exigia `-AceitarStore`; agora usa o 5.1."""
    if shutil.which(shell) is None:
        pytest.skip(f"{shell} ausente")
    store = _falso(tmp_path, _STORE)
    ps51 = _falso(tmp_path, r"sys\System32\WindowsPowerShell\v1.0\powershell.exe")
    r = _escolher(tmp_path, shell, _falso(tmp_path, r"PF\ausente.exe", existe=False), [store], ps51)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.strip() == f"powershell51|{ps51}"
    assert "WindowsApps" not in r.stdout


@precisa_shell
@pytest.mark.parametrize("shell", ["pwsh", "powershell"])
def test_interpretador_falha_claro_quando_nada_serve_e_nunca_devolve_a_store(tmp_path: Path, shell: str) -> None:
    if shutil.which(shell) is None:
        pytest.skip(f"{shell} ausente")
    store = _falso(tmp_path, _STORE)
    r = _escolher(tmp_path, shell, _falso(tmp_path, r"PF\ausente.exe", existe=False), [store],
                  _falso(tmp_path, r"sys\powershell.exe", existe=False))
    assert r.returncode != 0
    saida = r.stdout + r.stderr
    assert "nenhum interpretador estavel" in saida
    assert "winget install --id Microsoft.PowerShell" in saida
    assert "powershell51|" not in r.stdout and "pwsh|" not in r.stdout     # nada foi "escolhido"


def test_o_instalador_so_registra_caminho_validado_e_o_5_1_e_o_do_sistema() -> None:
    texto = TUNEL.read_text(encoding="utf-8")
    codigo = "\n".join(l for l in texto.splitlines() if not l.strip().startswith("#"))
    guarda = r"if ($exe -match '\\WindowsApps\\')"
    assert r"System32\WindowsPowerShell\v1.0\powershell.exe" in texto       # 5.1 pelo caminho fixo do sistema
    assert "Resolve-Pwsh" not in codigo                                      # a função antiga (que devolvia a Store)
    assert "$exe = $store" not in codigo and "seguindo assim" not in codigo
    # Rede de segurança no -Instalar: mesmo que a função mude, a ação com WindowsApps não chega ao Agendador.
    assert guarda in codigo
    assert codigo.index(guarda) < codigo.index("New-ScheduledTaskAction")


def test_o_tunel_nao_usa_recurso_exclusivo_do_powershell_7() -> None:
    """A escolha do 5.1 só é segura se o script roda nele. Operadores/cmdlets exclusivos do 7 em CÓDIGO (não em
    comentário) reabrem a armadilha 'a tarefa no 5.1 morre com LastTaskResult=1'."""
    texto = TUNEL.read_text(encoding="utf-8")
    texto = re.sub(r"<#.*?#>", "", texto, flags=re.S)
    codigo = "\n".join(re.sub(r"(^|\s)#.*$", "", l) for l in texto.splitlines())
    codigo = re.sub(r"'[^'\n]*'", "''", codigo)
    codigo = re.sub(r'"[^"\n]*"', '""', codigo)
    for padrao in (r"\?\?", r"\?\.", r"&&", r"\|\|", r"-AsHashtable", r"Join-String", r"-Parallel",
                   r"Get-Error", r"-SkipHttpErrorCheck", r"\?\["):
        assert not re.search(padrao, codigo), padrao


def test_o_arquivo_do_tunel_tem_bom_para_o_5_1_ler_utf8() -> None:
    assert TUNEL.read_bytes()[:3] == b"\xef\xbb\xbf"


@precisa_ps51
def test_o_tunel_parseia_e_instala_simulado_no_windows_powershell_5_1() -> None:
    """Parser do 5.1 sobre o arquivo inteiro + `-Instalar -Simular` (não registra nem encerra nada)."""
    cmd = ("$e=$null;$t=$null;[void][System.Management.Automation.Language.Parser]::ParseFile("
           f"'{TUNEL}',[ref]$t,[ref]$e); if (@($e).Count) {{ $e | ForEach-Object {{ $_.Message }}; exit 3 }}; "
           "'parse-ok'")
    p = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd],
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 0 and "parse-ok" in p.stdout, p.stdout + p.stderr

    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(TUNEL), "-Instalar",
                        "-Simular", "-Worker", "203.0.113.9", "-Mapa", "45555:5555",
                        "-MapaDeOutrosTuneis", "farm-tunel-192.168.1.19=15555:5555"],
                       capture_output=True, text=True, timeout=180, cwd=str(RAIZ))
    assert r.returncode == 0, r.stdout + r.stderr
    executavel = next(l for l in r.stdout.splitlines() if l.startswith("executavel: "))
    assert "WindowsApps" not in executavel, executavel
    assert '-MapaReverso "18000:8010"' in r.stdout            # arquitetura do túnel intacta: nunca a 8000
    assert "18000:8000" not in r.stdout


@precisa_pwsh
def test_aceitar_store_agora_e_erro_explicado() -> None:
    r = _instalar_simulado("-AceitarStore", "-Worker", "203.0.113.9", "-Mapa", "45555:5555",
                           "-MapaDeOutrosTuneis", "farm-tunel-192.168.1.19=15555:5555")
    assert r.returncode != 0
    saida = r.stdout + r.stderr
    assert "-AceitarStore foi removido" in saida and "Microsoft Store" in saida
    assert "executavel:" not in r.stdout                      # nem simulou ação alguma
