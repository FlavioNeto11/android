"""K-039: o `stop.ps1` encerra o Appium DESTE projeto que sobrou na porta, e nenhum outro processo.

O defeito (27 e 28/09/2026, três deploys): o `node …appium` do backend anterior sobrevivia ao `stop.ps1`, porque o
backend só encerra o Appium que ele sabe ter subido e um backend derrubado à força não encerra nada. O backend novo
READOTAVA o servidor pelo `data\\appium.pid` e subia `degraded` (`appium_log_masking_off` ou `appium_down`). A
correção à mão era parar, matar o `node.exe` dono da 4723 e religar a tarefa `farm-central`.

A regra que estes testes travam, em `scripts/lib/appium-do-projeto.ps1`: a porta vem do bloco `appium:` do config;
o processo tem de ser `node.exe` com a linha de comando apontando para `<appium.dir>/node_modules/appium` desta
árvore. Outro `node` (painel em modo dev, outro projeto, o driver do Appium) e qualquer outro programa na porta
ficam como estão.

Níveis de prova: a seleção e a leitura do config são exercitadas pela função de verdade no pwsh; o encerramento,
com processos `node` de verdade e a listagem da porta trocada por uma que o teste controla (`-Donos`). A listagem
real da porta (`Get-NetTCPConnection`) só existe no Windows: o último teste roda lá e se pula fora dele.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / "scripts" / "lib" / "appium-do-projeto.ps1"
STOP = ROOT / "scripts" / "stop.ps1"
PWSH = shutil.which("pwsh")
NODE = shutil.which("node")

precisa_pwsh = pytest.mark.skipif(PWSH is None, reason="sem pwsh nesta máquina")
precisa_node = pytest.mark.skipif(NODE is None, reason="sem node nesta máquina")
so_windows = pytest.mark.skipif(os.name != "nt", reason="Get-NetTCPConnection e Win32_Process só existem no Windows")


_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_MOLDURA = re.compile(r"\s*\n\s*(?:\d+\s*)?\|\s*")


def _plano(texto: str) -> str:
    """Tira as cores do pwsh 7.6 e a moldura do erro ("Line | 59 | ..."), que parte a mensagem em linhas, e junta os espaços."""
    return re.sub(r"\s+", " ", _MOLDURA.sub(" ", _ANSI.sub("", texto)))


def _ps(script: str, **env: str) -> subprocess.CompletedProcess[str]:
    """Carrega a biblioteca e roda `script`. Caminhos e dados entram por variável de ambiente, sem aspas no meio."""
    r = _ps_cru(script, **env)
    r.stdout, r.stderr = _plano(r.stdout or ""), _plano(r.stderr or "")
    return r


def _ps_cru(script: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([PWSH or "pwsh", "-NoProfile", "-NonInteractive", "-Command",
                           f"[Console]::OutputEncoding = [Text.Encoding]::UTF8; . $env:LIB; {script}"],
                          # O pwsh 7 escreve em UTF-8 no pipe; sem `encoding` o Python decodifica pela página do console (OEM 850) e
                          # "inválido"/"está" viram "inv├ílido"/"est\xa0" (visto em 01/10 ao integrar o PR 15).
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
                          env={**os.environ, "LIB": str(LIB), **env})


def _config(caminho: str | Path) -> dict[str, object]:
    r = _ps("Get-AppiumConfig $env:CFG | ConvertTo-Json -Compress", CFG=str(caminho))
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


# ------------------------------------------------------------------ a porta: bloco `appium:` do config
@precisa_pwsh
def test_host_porta_e_pasta_saem_so_do_bloco_appium(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "server:\n"
        "  host: 127.0.0.1\n"
        "  port: 8000\n"
        "  worker_port: 8010\n"
        "\n"
        "appium:   # o servidor local\n"
        "  # comentário no meio do bloco\n"
        "  host: '127.0.0.1'\n"
        "  port: 4799        # não a 4723\n"
        '  dir: "tools/appium-fixo"\n'
        "  autostart: true\n"
        "\n"
        "limits:\n"
        "  port: 1\n",
        encoding="utf-8")
    assert _config(cfg) == {"Host": "127.0.0.1", "Porta": 4799, "Pasta": "tools/appium-fixo"}


@precisa_pwsh
def test_chave_aninhada_no_bloco_nao_vira_a_porta(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("appium:\n  extra:\n    port: 1111\n  port: 4724\n", encoding="utf-8")
    assert _config(cfg)["Porta"] == 4724


@precisa_pwsh
def test_sem_arquivo_ou_sem_chave_valem_os_padroes_do_backend(tmp_path: Path) -> None:
    """Os mesmos de `AppiumCfg` (backend/app/config.py): sem config, o backend sobe o Appium ali."""
    fonte = (ROOT / "backend" / "app" / "config.py").read_text(encoding="utf-8")
    bloco = re.search(r"class AppiumCfg\(BaseModel\):\n((?:    .*\n)+)", fonte)
    assert bloco, "AppiumCfg sumiu de backend/app/config.py"
    padrao = dict(re.findall(r'^    (host|port|dir): \w+ = "?([^"\n]+)"?$', bloco.group(1), re.M))
    esperado = {"Host": padrao["host"], "Porta": int(padrao["port"]), "Pasta": padrao["dir"]}

    assert _config(tmp_path / "nao-existe.yaml") == esperado
    so_outros = tmp_path / "config.yaml"
    so_outros.write_text("server:\n  port: 8000\nappium:\n  autostart: true\n", encoding="utf-8")
    assert _config(so_outros) == esperado


@precisa_pwsh
def test_porta_invalida_e_erro_e_nao_um_palpite(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("appium:\n  port: quarenta\n", encoding="utf-8")
    # A mensagem sai por `catch`, não pela moldura de erro do pwsh 7.6, que corta linhas longas com "...".
    r = _ps("try { Get-AppiumConfig $env:CFG } catch { Write-Output $_.Exception.Message; exit 1 }", CFG=str(cfg))
    assert r.returncode != 0
    assert "appium.port inválido" in r.stdout + r.stderr


# ------------------------------------------------------------------ o processo: só o Appium desta árvore
PASTA = r"C:\git\android\tools\appium"
NOSSO = r'"C:\Program Files\nodejs\node.exe" C:\git\android\tools\appium\node_modules\appium\index.js server --port 4723'


@precisa_pwsh
@pytest.mark.parametrize(("nome", "linha", "encerra"), [
    ("node.exe", NOSSO, True),
    ("NODE.EXE", NOSSO.upper(), True),                                          # o Windows não diferencia caixa
    ("node.exe", NOSSO.replace("\\", "/"), True),                               # nem barra
    ("node.exe", '"node.exe" "C:\\git\\android\\tools\\appium\\node_modules\\appium" server', True),
    ("node.exe", NOSSO.replace("\\git\\android\\", "\\git\\android2\\"), False),  # outra árvore
    ("node.exe", NOSSO.replace("\\git\\android\\", "\\git\\outro\\"), False),
    ("node.exe", r"node C:\git\android\tools\appium\node_modules\appium-uiautomator2-driver\x.js", False),
    ("node.exe", r"node C:\git\android\frontend\node_modules\vite\bin\vite.js", False),   # o painel em dev
    ("python.exe", r"python.exe C:\git\android\tools\appium\node_modules\appium\index.js", False),
    ("node.exe", "", False),                                                    # linha ilegível não é prova
    ("node.exe", None, False),
])
def test_so_o_node_do_appium_desta_arvore_e_escolhido(nome: str, linha: str | None, encerra: bool) -> None:
    proc = json.dumps({"ProcessId": 4242, "Name": nome, "CommandLine": linha})
    r = _ps("$p = $env:PROC | ConvertFrom-Json; "
            "$s = @(Select-AppiumDoProjeto @($p) $env:PASTA); "
            "if ($s.Count) { 'encerra' } else { 'fica' }",
            PROC=proc, PASTA=PASTA)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.strip().splitlines()[-1] == ("encerra" if encerra else "fica")


@precisa_pwsh
@pytest.mark.parametrize("pasta", ["", "   ", "\\", "/"])
def test_sem_pasta_do_appium_ninguem_e_escolhido(pasta: str) -> None:
    """Com a pasta vazia, o padrão casava com TODO `node` na porta (medido: `Join-Path` com um drive inexistente
    devolvia vazio). Tem de ser erro, nunca "encerra qualquer um"."""
    proc = json.dumps({"ProcessId": 4242, "Name": "node.exe", "CommandLine": NOSSO})
    r = _ps("$ErrorActionPreference = 'Stop'; $p = $env:PROC | ConvertFrom-Json; "
            "$s = @(Select-AppiumDoProjeto @($p) $env:PASTA); "
            "if ($s.Count) { 'encerra' } else { 'fica' }",
            PROC=proc, PASTA=pasta)
    assert r.returncode != 0
    assert "encerra" not in r.stdout
    assert "PastaAppium vazia" in r.stdout + r.stderr


# ------------------------------------------------------------------ encerrar de verdade, só o nosso
FICA_VIVO = "setInterval(() => {}, 1000);\n"
# Os processos "escutam na porta": a listagem de verdade é do Windows; aqui o teste diz quem são os donos (P1, P2),
# e a linha de comando é a que o sistema operacional informa desses processos.
DONOS = ("{ param($p) Get-Process -Id ([int]$env:P1), ([int]$env:P2) -ErrorAction SilentlyContinue | "
         "ForEach-Object { [pscustomobject]@{ ProcessId = $_.Id; Name = $_.ProcessName; "
         "CommandLine = $_.CommandLine } } }")


def _arvore_com_appium(raiz: Path, codigo: str = FICA_VIVO) -> Path:
    """Uma árvore com `tools/appium/node_modules/appium/index.js`; por padrão ele só fica vivo, como o servidor."""
    entrada = raiz / "tools" / "appium" / "node_modules" / "appium" / "index.js"
    entrada.parent.mkdir(parents=True)
    entrada.write_text(codigo, encoding="utf-8")
    return entrada


def _esperar_saida(proc: subprocess.Popen[bytes], prazo_s: float = 10) -> bool:
    limite = time.monotonic() + prazo_s
    while time.monotonic() < limite:
        if proc.poll() is not None:
            return True
        time.sleep(0.1)
    return False


@precisa_pwsh
@precisa_node
def test_encerra_o_appium_da_arvore_e_poupa_o_node_de_outra(tmp_path: Path) -> None:
    nossa = _arvore_com_appium(tmp_path / "android")
    alheia = _arvore_com_appium(tmp_path / "outro")
    arquivo_pid = tmp_path / "appium.pid"
    p_nosso = subprocess.Popen([NODE or "node", str(nossa), "server"])
    p_alheio = subprocess.Popen([NODE or "node", str(alheia), "server"])
    try:
        arquivo_pid.write_text(str(p_nosso.pid), encoding="ascii")
        env = {"P1": str(p_nosso.pid), "P2": str(p_alheio.pid), "PASTA": str(tmp_path / "android" / "tools" / "appium"),
               "PIDF": str(arquivo_pid)}
        time.sleep(0.5)   # o node precisa existir em /proc (ou no Windows) antes de ser listado

        simulado = _ps(f"Stop-AppiumDoProjeto -Porta 4723 -PastaAppium $env:PASTA -Simular -Donos {DONOS}", **env)
        assert simulado.returncode == 0, simulado.stdout + simulado.stderr
        assert f"encerraria: pid {p_nosso.pid}" in simulado.stdout
        assert f"encerraria: pid {p_alheio.pid}" not in simulado.stdout
        assert p_nosso.poll() is None and p_alheio.poll() is None, "o -Simular não encerra nada"

        real = _ps(f"Stop-AppiumDoProjeto -Porta 4723 -PastaAppium $env:PASTA -ArquivoPid $env:PIDF -CarenciaS 1 "
                   f"-PrazoS 10 -Donos {DONOS}", **env)
        saida = real.stdout + real.stderr
        assert real.returncode == 0, saida
        assert _esperar_saida(p_nosso), f"o Appium desta árvore continuou vivo:\n{saida}"
        assert p_alheio.poll() is None, f"o node de outra árvore foi encerrado:\n{saida}"
        assert f"pid {p_alheio.pid}" in saida and "fica como está" in saida
        assert f"Encerrando o Appium deste projeto que sobrou na porta 4723 (pid {p_nosso.pid})" in saida
        assert "porta 4723 está livre dele" in saida
        assert not arquivo_pid.exists(), "o appium.pid apontava para quem saiu e devia ter sido apagado"
    finally:
        for p in (p_nosso, p_alheio):
            if p.poll() is None:
                p.kill()
                p.wait(timeout=10)


@precisa_pwsh
@precisa_node
def test_o_appium_que_o_backend_ainda_esta_desligando_nao_e_morto(tmp_path: Path) -> None:
    """Carência: o backend que acabou de parar de responder ainda fecha as sessões e desliga o Appium dele."""
    nossa = _arvore_com_appium(tmp_path / "android", "setTimeout(() => process.exit(0), 1500);\n")
    p_nosso = subprocess.Popen([NODE or "node", str(nossa), "server"])
    try:
        time.sleep(0.5)
        env = {"P1": str(p_nosso.pid), "P2": str(p_nosso.pid), "PASTA": str(tmp_path / "android" / "tools" / "appium")}
        r = _ps(f"Stop-AppiumDoProjeto -Porta 4723 -PastaAppium $env:PASTA -CarenciaS 8 -Donos {DONOS}", **env)
        saida = r.stdout + r.stderr
        assert r.returncode == 0, saida
        assert "saiu sozinho da porta 4723" in saida
        assert "Encerrando" not in saida
        assert p_nosso.wait(timeout=10) == 0, "saiu pelo próprio código, não por Stop-Process"
    finally:
        if p_nosso.poll() is None:
            p_nosso.kill()
            p_nosso.wait(timeout=10)


# ------------------------------------------------------------------ o stop.ps1 inteiro, sem encerrar nada
def _porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@precisa_pwsh
def test_stop_simulado_le_a_porta_do_config_e_nao_encerra_nada(tmp_path: Path) -> None:
    """Só lê: o GET de /api/health e a lista de quem escuta. Nenhum POST de encerramento, nenhum Stop-Process."""
    porta = _porta_livre()
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"appium:\n  host: 127.0.0.1\n  port: {porta}\n  dir: tools/appium\n", encoding="utf-8")
    r = subprocess.run([PWSH or "pwsh", "-NoProfile", "-NonInteractive", "-File", str(STOP), "-Simular",
                        "-Config", str(cfg)], capture_output=True, text=True, timeout=120)
    saida = r.stdout + r.stderr
    assert r.returncode == 0, saida
    assert f"appium: porta {porta} (host 127.0.0.1)" in saida
    assert str(ROOT / "tools" / "appium") in saida
    assert "simulacao: nada foi encerrado" in saida
    assert "Encerrando" not in saida and "Encerramento solicitado" not in saida


def test_o_stop_so_procura_o_appium_depois_de_o_backend_sair() -> None:
    """Com a Farm no ar, o Appium é DELA: encerrá-lo derrubaria as sessões de um backend vivo."""
    texto = STOP.read_text(encoding="utf-8")
    assert "lib\\appium-do-projeto.ps1" in texto
    fim_do_backend = texto.index("Remove-Item $pidFile")
    chamada = texto.index("Stop-AppiumDoProjeto -Porta $a.Porta -PastaAppium (Resolve-PastaAppium")
    assert chamada > fim_do_backend
    guarda = texto.rindex("if (Get-FarmHealth $base 2)", 0, chamada)
    assert "else" in texto[guarda:chamada]
    # e o deploy herda: ele para pelo stop.ps1, depois de parar a tarefa farm-central
    deploy = (ROOT / "scripts" / "deploy.ps1").read_text(encoding="utf-8")
    assert deploy.index("Stop-ScheduledTask -TaskName 'farm-central'") < deploy.index("'stop.ps1'")


# ------------------------------------------------------------------ Windows: a porta de verdade
@precisa_pwsh
@precisa_node
@so_windows
def test_windows_acha_o_dono_da_porta_e_encerra_so_o_da_arvore(tmp_path: Path) -> None:  # pragma: no cover - Windows
    servidor = ("const p=+process.argv[2];require('net').createServer().listen(p,'127.0.0.1');"
                "setInterval(()=>{},1000);")
    nossa = _arvore_com_appium(tmp_path / "android")
    alheia = _arvore_com_appium(tmp_path / "outro")
    nossa.write_text(servidor, encoding="utf-8")
    alheia.write_text(servidor, encoding="utf-8")
    pa, pb = _porta_livre(), _porta_livre()
    p_nosso = subprocess.Popen([NODE or "node", str(nossa), str(pa)])
    p_alheio = subprocess.Popen([NODE or "node", str(alheia), str(pb)])
    try:
        time.sleep(1.5)
        env = {"PASTA": str(tmp_path / "android" / "tools" / "appium"), "PA": str(pa), "PB": str(pb)}
        donos = _ps("Get-DonosDaPorta ([int]$env:PA) | ConvertTo-Json -Compress", **env)
        assert donos.returncode == 0, donos.stdout + donos.stderr
        dono = json.loads(donos.stdout.strip().splitlines()[-1])
        assert dono["ProcessId"] == p_nosso.pid and dono["Name"].lower() == "node.exe"

        alheio = _ps("Stop-AppiumDoProjeto -Porta ([int]$env:PB) -PastaAppium $env:PASTA -PrazoS 5", **env)
        assert alheio.returncode == 0 and p_alheio.poll() is None, alheio.stdout + alheio.stderr

        real = _ps("Stop-AppiumDoProjeto -Porta ([int]$env:PA) -PastaAppium $env:PASTA -CarenciaS 1 -PrazoS 10",
                  **env)
        assert real.returncode == 0, real.stdout + real.stderr
        assert _esperar_saida(p_nosso), real.stdout + real.stderr
        assert p_alheio.poll() is None
    finally:
        for p in (p_nosso, p_alheio):
            if p.poll() is None:
                p.kill()
                p.wait(timeout=10)
