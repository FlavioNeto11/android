"""29.94: o portão do `deploy.ps1 -PularBackup` roda de verdade, e nenhum `.ps1` reusa o nome de um parâmetro.

O defeito (deploy 34, 05/10/2026 03:19Z): `$ensaio = Find-EnsaioRecente ...` no `deploy.ps1`. Variável no PowerShell não
diferencia caixa, então `$ensaio` é o próprio `[switch]$Ensaio` do `param`: a cópia (`DirectoryInfo`) não converte em
switch e a subida morreu com "Cannot convert value System.IO.DirectoryInfo to type SwitchParameter". O teste que havia
(`backend/tests/test_backup.py`) testava `Find-EnsaioRecente` sozinho e LIA o texto do deploy; o trecho que atribui
nunca rodou até a subida de verdade. Se a conversão passasse, o `if ($Ensaio)` adiante trataria a subida como ensaio.

Rodar o `deploy.ps1` inteiro num teste pararia o ambiente central. Então o teste roda O TRECHO do portão, recortado
do arquivo pelos marcadores `# >>> portão do -PularBackup` e `# <<< portão do -PularBackup`, debaixo do MESMO `param`
do deploy (lido da linha dele), com a biblioteca de verdade e uma cópia de ensaio de mentira. A varredura do fim olha a
classe inteira do defeito em todos os `.ps1` com `param`, pela árvore de sintaxe do próprio PowerShell.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
PWSH = shutil.which("pwsh")

precisa_pwsh = pytest.mark.skipif(PWSH is None, reason="sem pwsh nesta máquina")


def _trecho_do_portao() -> tuple[str, str]:
    """O `param(...)` e o trecho entre os marcadores, como estão no `deploy.ps1`."""
    texto = (SCRIPTS / "deploy.ps1").read_text(encoding="utf-8")
    param = re.search(r"(?m)^param\(.*\)\s*$", texto)
    assert param, "o deploy.ps1 perdeu o `param(...)` de uma linha"
    ini = texto.index("# >>> portão do -PularBackup")
    fim = texto.index("# <<< portão do -PularBackup")
    assert ini < fim < texto.index("'stop.ps1'"), "o portão tem de vir antes de parar qualquer coisa"
    return param.group(0), texto[ini:fim]


def _minutos_do_ensaio() -> int:
    """O prazo do ensaio como está no deploy, de dentro dos marcadores: mudou o número lá, o teste acompanha."""
    m = re.search(r"(?m)^\$minutosDoEnsaio = (\d+)\s*$", _trecho_do_portao()[1])
    assert m, "o `$minutosDoEnsaio = N` tem de ficar DENTRO dos marcadores do portão"
    return int(m.group(1))


def _arvore_falsa(tmp: Path, copias: dict[str, dict[str, str]]) -> Path:
    """`<tmp>/scripts/portao.ps1` com o trecho do deploy, a biblioteca de verdade e as cópias pedidas em
    `<tmp>/data/backups`. O `$root` do trecho é `<tmp>`, como no deploy (`Split-Path -Parent $PSScriptRoot`)."""
    (tmp / "scripts" / "lib").mkdir(parents=True)
    shutil.copy(SCRIPTS / "lib" / "copias-de-backup.ps1", tmp / "scripts" / "lib" / "copias-de-backup.ps1")
    for nome, manifesto in copias.items():
        pasta = tmp / "data" / "backups" / nome
        pasta.mkdir(parents=True)
        (pasta / "manifesto.json").write_text(json.dumps(manifesto), encoding="utf-8")
    param, trecho = _trecho_do_portao()
    script = "\n".join([
        param,
        "$ErrorActionPreference = 'Stop'",
        "[Console]::OutputEncoding = [Text.Encoding]::UTF8",
        "$PSStyle.OutputRendering = 'PlainText'",
        "$root = Split-Path -Parent $PSScriptRoot",
        "$esperadoCommit = $env:COMMIT_DO_TESTE",
        trecho,                                # o `$minutosDoEnsaio` vem dele, como no deploy
        # O que vem depois do portão no deploy decide pelo switch: ele tem de continuar sendo o que se passou.
        "Write-Host \"Ensaio=$([bool]$Ensaio) PularBackup=$([bool]$PularBackup)\"",
    ])
    alvo = tmp / "scripts" / "portao.ps1"
    alvo.write_text(script, encoding="utf-8-sig")
    return alvo


def _rodar(script: Path, commit: str, *args: str) -> subprocess.CompletedProcess[str]:
    import os

    env = {**os.environ, "COMMIT_DO_TESTE": commit}
    return subprocess.run([PWSH or "pwsh", "-NoProfile", "-NonInteractive", "-File", str(script), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=120)


@precisa_pwsh
def test_pular_backup_com_ensaio_do_mesmo_commit_usa_a_copia_e_nao_vira_ensaio(tmp_path: Path) -> None:
    agora = datetime.now(timezone.utc).isoformat()
    script = _arvore_falsa(tmp_path, {"20261005-001850": {"origem": "ensaio", "commit": "abc123", "ts": agora}})
    r = _rodar(script, "abc123", "-PularBackup", "-PularDependencias")
    assert r.returncode == 0, r.stderr
    assert "a cópia do ensaio 20261005-001850" in r.stdout
    assert "Ensaio=False PularBackup=True" in r.stdout          # a subida continua sendo subida
    assert "SwitchParameter" not in r.stderr


@precisa_pwsh
def test_pular_backup_sem_ensaio_do_mesmo_commit_recusa_com_a_mensagem(tmp_path: Path) -> None:
    agora = datetime.now(timezone.utc).isoformat()
    script = _arvore_falsa(tmp_path, {"20261005-001850": {"origem": "ensaio", "commit": "outro", "ts": agora}})
    r = _rodar(script, "abc123", "-PularBackup")
    assert r.returncode != 0
    # O erro do pwsh vem com moldura ("Line | 12 | ...") que parte a mensagem: junta tudo antes de procurar.
    saida = re.sub(r"\s+", " ", re.sub(r"\s*\n\s*(?:\d+\s*)?\|\s*", " ", r.stdout + r.stderr))
    assert f"-PularBackup só vale até {_minutos_do_ensaio()} min" in saida and "abc123" in saida
    assert "SwitchParameter" not in saida


@precisa_pwsh
def test_com_ensaio_o_portao_nao_roda(tmp_path: Path) -> None:
    """`-Ensaio -PularBackup` não procura cópia (o ensaio só reexecuta a conferência): o portão é só da subida."""
    script = _arvore_falsa(tmp_path, {})
    r = _rodar(script, "abc123", "-Ensaio", "-PularBackup")
    assert r.returncode == 0, r.stderr
    assert "Ensaio=True PularBackup=True" in r.stdout and "a cópia do ensaio" not in r.stdout


_VARREDURA = r"""
param([string]$Raiz)
Get-ChildItem -Path $Raiz -Recurse -Filter *.ps1 | ForEach-Object {
  $erros = $null; $tokens = $null
  $ast = [System.Management.Automation.Language.Parser]::ParseFile($_.FullName, [ref]$tokens, [ref]$erros)
  if (-not $ast.ParamBlock) { return }
  $params = @($ast.ParamBlock.Parameters | ForEach-Object {
    [pscustomobject]@{ nome = $_.Name.VariablePath.UserPath; switch = ($_.StaticType -eq [System.Management.Automation.SwitchParameter]) } })
  $alvos = @()
  foreach ($a in $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.AssignmentStatementAst] }, $true)) {
    $esq = $a.Left
    if ($esq -is [System.Management.Automation.Language.ConvertExpressionAst]) { $esq = $esq.Child }
    if ($esq -is [System.Management.Automation.Language.VariableExpressionAst]) { $alvos += ,@($a, $esq) }
  }
  foreach ($f in $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.ForEachStatementAst] }, $true)) {
    $alvos += ,@($f, $f.Variable)
  }
  foreach ($par in $alvos) {
    $no, $var = $par
    $p = $no.Parent; $emFuncao = $false
    while ($p) { if ($p -is [System.Management.Automation.Language.FunctionDefinitionAst]) { $emFuncao = $true; break }; $p = $p.Parent }
    if ($emFuncao) { continue }
    $nome = $var.VariablePath.UserPath
    foreach ($q in $params) {
      if ($nome -ine $q.nome) { continue }
      if ($q.switch -or $nome -cne $q.nome) {
        '{0}:{1}: ${2} -> parametro ${3}' -f $_.Name, $no.Extent.StartLineNumber, $nome, $q.nome
      }
    }
  }
}
"""


@precisa_pwsh
def test_nenhum_ps1_atribui_a_um_parametro_com_outra_caixa(tmp_path: Path) -> None:
    """A classe do defeito: no escopo do script (fora de função), atribuir a uma variável que é um parâmetro do mesmo
    script escrito com outra caixa (`$ensaio` contra `[switch]$Ensaio`). Mesma caixa é reatribuição de propósito
    (`if (-not $Destino) { $Destino = ... }`); outra caixa é quem achou que criava uma variável nova. Parâmetro
    `[switch]` não se reatribui com caixa NENHUMA: o que vem depois decide por ele (`if ($Ensaio)`), e uma cópia ou um
    número no lugar viraria subida tratada como ensaio. A variável do `foreach ($x in ...)` conta como atribuição.
    Ficam DE FORA (não é atribuição que o parser mostra como tal, ou é escopo de propósito): `Set-Variable`,
    `-OutVariable`, `$script:`/`$global:` e `++`/`--`. Na varredura de 05/10 eram duas: o `deploy.ps1` (o que derrubou
    o deploy 34) e o `backup.ps1` (`$podar` contra `[switch]$Podar`, que só funcionava porque um booleano converte em
    switch)."""
    varredura = tmp_path / "varredura.ps1"
    varredura.write_text(_VARREDURA, encoding="utf-8-sig")
    r = subprocess.run([PWSH or "pwsh", "-NoProfile", "-NonInteractive", "-File", str(varredura), "-Raiz", str(SCRIPTS)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    assert r.returncode == 0, r.stderr
    achados = [linha for linha in r.stdout.splitlines() if linha.strip()]
    assert achados == [], "variável que reusa o nome de um parâmetro com outra caixa:\n" + "\n".join(achados)


@precisa_pwsh
def test_a_varredura_acha_o_defeito_quando_ele_existe(tmp_path: Path) -> None:
    """Sem isto, uma varredura que nunca acha nada passaria igual."""
    (tmp_path / "s").mkdir()
    (tmp_path / "s" / "ruim.ps1").write_text("param([switch]$Ensaio)\n$ensaio = Get-Item .\n", encoding="utf-8")
    # Switch reatribuído com a MESMA caixa também reprova (N2 da revisão do 29.94).
    (tmp_path / "s" / "ruim_mesma_caixa.ps1").write_text("param([switch]$Ensaio)\n$Ensaio = Get-Item .\n",
                                                          encoding="utf-8")
    # A variável do `foreach` conta como atribuição (N1).
    (tmp_path / "s" / "ruim_laco.ps1").write_text("param([string]$Destino)\nforeach ($destino in 1, 2) { }\n",
                                                   encoding="utf-8")
    (tmp_path / "s" / "bom.ps1").write_text(
        "param([string]$Destino, [switch]$Podar)\nif (-not $Destino) { $Destino = 'x' }\nfunction f { $destino = 1 }\n"
        "$podarDeVerdade = $Podar\nfunction g { foreach ($podar in 1) { } }\n", encoding="utf-8")
    varredura = tmp_path / "varredura.ps1"
    varredura.write_text(_VARREDURA, encoding="utf-8-sig")
    r = subprocess.run([PWSH or "pwsh", "-NoProfile", "-NonInteractive", "-File", str(varredura), "-Raiz",
                        str(tmp_path / "s")], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    assert r.returncode == 0, r.stderr
    assert sorted(linha.strip() for linha in r.stdout.splitlines() if linha.strip()) == [
        "ruim.ps1:2: $ensaio -> parametro $Ensaio",
        "ruim_laco.ps1:2: $destino -> parametro $Destino",
        "ruim_mesma_caixa.ps1:2: $Ensaio -> parametro $Ensaio",
    ]


# --- 29.166 (b): o docs-check roda no ensaio e na subida, antes de parar qualquer coisa -------------------------------

def _trecho_do_docs_check() -> tuple[str, str]:
    """O `param(...)` e o trecho entre os marcadores `docs-check (29.166)`, como estão no `deploy.ps1`."""
    texto = (SCRIPTS / "deploy.ps1").read_text(encoding="utf-8")
    param = re.search(r"(?m)^param\(.*\)\s*$", texto)
    assert param, "o deploy.ps1 perdeu o `param(...)` de uma linha"
    ini = texto.index("# >>> docs-check (29.166)")
    fim = texto.index("# <<< docs-check (29.166)")
    assert ini < fim < texto.index("'stop.ps1'"), "o docs-check tem de vir antes de parar qualquer coisa"
    assert fim < texto.index("ENSAIO: backup feito"), "o docs-check roda também no ensaio, antes dele terminar"
    return param.group(0), texto[ini:fim]


def _arvore_do_docs_check(tmp: Path) -> Path:
    """`<tmp>/scripts/docs.ps1` com o trecho do deploy e um docs-check FALSO (`<tmp>/scripts/docs-check.py`) que imprime
    `FAKE_SAIDA` e sai com `FAKE_CODIGO`; o interpretador é o do próprio teste (no deploy é o venv do backend)."""
    import sys

    (tmp / "scripts").mkdir(parents=True)
    (tmp / "scripts" / "docs-check.py").write_text(
        "import os, sys\nprint(os.environ.get('FAKE_SAIDA', ''))\nsys.exit(int(os.environ.get('FAKE_CODIGO', '0')))\n",
        encoding="utf-8")
    param, trecho = _trecho_do_docs_check()
    script = "\n".join([
        param,
        "$ErrorActionPreference = 'Stop'",
        "[Console]::OutputEncoding = [Text.Encoding]::UTF8",
        "$PSStyle.OutputRendering = 'PlainText'",
        "$root = Split-Path -Parent $PSScriptRoot",
        f"$pythonDoBackend = '{sys.executable}'",      # no deploy: o venv do backend, definido logo acima dos marcadores
        trecho,
        "Write-Host \"SEGUIU Ensaio=$([bool]$Ensaio) PularDocsCheck=$([bool]$PularDocsCheck)\"",
    ])
    alvo = tmp / "scripts" / "docs.ps1"
    alvo.write_text(script, encoding="utf-8-sig")
    return alvo


def _rodar_docs(script: Path, codigo: int, saida: str, *args: str) -> subprocess.CompletedProcess[str]:
    import os

    env = {**os.environ, "FAKE_CODIGO": str(codigo), "FAKE_SAIDA": saida}
    return subprocess.run([PWSH or "pwsh", "-NoProfile", "-NonInteractive", "-File", str(script), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=120)


def _junta(r: subprocess.CompletedProcess[str]) -> str:
    """Junta as duas saídas e desfaz a moldura do erro do pwsh, que parte a mensagem em linhas."""
    return re.sub(r"\s+", " ", re.sub(r"\s*\n\s*(?:\d+\s*)?\|\s*", " ", r.stdout + r.stderr))


@precisa_pwsh
def test_docs_check_limpo_deixa_o_deploy_seguir(tmp_path: Path) -> None:
    r = _rodar_docs(_arvore_do_docs_check(tmp_path), 0, "docs-check: 0 erros, 0 avisos")
    assert r.returncode == 0, r.stderr
    assert "docs-check: 0 erros, 0 avisos" in r.stdout          # a saída do docs-check aparece no console do deploy
    assert "SEGUIU Ensaio=False PularDocsCheck=False" in r.stdout


@precisa_pwsh
def test_docs_check_so_com_aviso_nao_recusa(tmp_path: Path) -> None:
    """AVISO não derruba: é o caso do Python sem PyYAML, em que o docs-check diz que não conferiu."""
    aviso = "AVISO: config/config.example.yaml: formato NAO conferido (falta o pacote yaml)"
    r = _rodar_docs(_arvore_do_docs_check(tmp_path), 0, aviso + "\ndocs-check: 0 erros, 1 avisos")
    assert r.returncode == 0, r.stderr
    assert "formato NAO conferido" in r.stdout and "SEGUIU" in r.stdout
    assert "NÃO conferiu" in _junta(r), "o deploy destaca com WARNING que parte do formato ficou sem prova"


@precisa_pwsh
def test_docs_check_com_erro_recusa_na_subida_e_diz_o_caminho_da_chave(tmp_path: Path) -> None:
    erro = "ERRO: config/config.example.yaml: instances.countt: chave desconhecida"
    r = _rodar_docs(_arvore_do_docs_check(tmp_path), 1, erro + "\ndocs-check: 1 erros, 0 avisos")
    assert r.returncode != 0
    saida = _junta(r)
    assert "instances.countt: chave desconhecida" in saida      # o caminho da chave chega ao console do deploy
    assert "o docs-check reprovou" in saida
    assert "SEGUIU" not in r.stdout                              # o que vem depois (parar o backend) não rodou


@precisa_pwsh
def test_docs_check_com_erro_recusa_tambem_o_ensaio(tmp_path: Path) -> None:
    r = _rodar_docs(_arvore_do_docs_check(tmp_path), 1, "ERRO: .claude/plano-100.json: batches[0].items[1]: ID fora do formato",
                    "-Ensaio")
    assert r.returncode != 0
    assert "batches[0].items[1]" in _junta(r) and "SEGUIU" not in r.stdout


@precisa_pwsh
def test_pular_docs_check_deixa_seguir_e_diz_que_nao_conferiu(tmp_path: Path) -> None:
    r = _rodar_docs(_arvore_do_docs_check(tmp_path), 1, "ERRO: nao pode aparecer", "-PularDocsCheck")
    assert r.returncode == 0, r.stderr
    assert "PULADO por -PularDocsCheck" in r.stdout and "NÃO foi conferido" in r.stdout
    assert "ERRO: nao pode aparecer" not in r.stdout             # o docs-check nem rodou
    assert "SEGUIU Ensaio=False PularDocsCheck=True" in r.stdout


@precisa_pwsh
def test_o_docs_check_de_verdade_passa_na_arvore_do_repositorio() -> None:
    """O mesmo comando que o deploy roda, com o Python deste teste, na árvore real: um deploy não pode nascer recusado."""
    import sys

    r = subprocess.run([sys.executable, str(SCRIPTS / "docs-check.py"), "--raiz", str(ROOT)], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=120)
    assert r.returncode == 0, r.stdout[-1500:]
