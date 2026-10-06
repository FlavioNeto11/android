"""29.159: o histórico do deploy (uma linha por subida), a tag `deploy-AAAAMMDD-HHMM` e o release no melhor esforço.

Prova SIMULADA: as funções de `scripts/lib/historico-de-deploy.ps1` rodam de verdade no pwsh, contra um repositório
de mentira com uma origem local (nunca o GitHub), e o `gh` é uma função falsa que só anota os argumentos. O
`deploy.ps1` em si não roda aqui (pararia o ambiente central): o que o teste confere nele é a posição dos pontos que
importam, lida do texto. A prova `real` é um deploy com a linha e a tag (`not_run` até o próximo deploy).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
LIB = SCRIPTS / "lib" / "historico-de-deploy.ps1"
PWSH = shutil.which("pwsh")
GIT = shutil.which("git")

precisa_pwsh = pytest.mark.skipif(PWSH is None or GIT is None, reason="sem pwsh ou git nesta máquina")


def _ps(tmp: Path, corpo: str) -> subprocess.CompletedProcess[str]:
    script = tmp / "t.ps1"
    script.write_text("\n".join([
        "$ErrorActionPreference = 'Stop'",
        "[Console]::OutputEncoding = [Text.Encoding]::UTF8",
        f". '{LIB}'",
        corpo,
    ]), encoding="utf-8-sig")
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0"}
    return subprocess.run([PWSH or "pwsh", "-NoProfile", "-NonInteractive", "-File", str(script)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=120)


def _git(cwd: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": os.devnull}
    r = subprocess.run([GIT or "git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def _repo_com_origem(tmp: Path) -> tuple[Path, Path, str]:
    origem = tmp / "origem.git"
    origem.mkdir()
    _git(origem, "init", "--bare", "-q")
    repo = tmp / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "remote", "add", "origin", str(origem))
    (repo / "a.txt").write_text("a", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "um")
    return repo, origem, _git(repo, "rev-parse", "HEAD")


@precisa_pwsh
def test_nome_da_tag_e_utc_e_ganha_sufixo_na_colisao(tmp_path: Path) -> None:
    r = _ps(tmp_path, "\n".join([
        "$t = [datetime]::SpecifyKind([datetime]'2026-10-06 15:45:30', 'Utc')",
        "Get-NomeDaTagDeDeploy -UtcAgora $t",
        "Get-NomeDaTagDeDeploy -UtcAgora $t -Existentes @('deploy-20261006-1545')",
        "Get-NomeDaTagDeDeploy -UtcAgora $t -Existentes @('deploy-20261006-1545','deploy-20261006-1545-2')",
    ]))
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == ["deploy-20261006-1545", "deploy-20261006-1545-2", "deploy-20261006-1545-3"]


@precisa_pwsh
def test_cada_subida_vira_uma_linha_json_valida_sem_bom_e_a_falha_vai_cortada(tmp_path: Path) -> None:
    alvo = tmp_path / "data" / "deploys.jsonl"                     # a pasta ainda não existe: a função a cria
    longa = "x" * 500 + "\n" + "segunda linha"
    r = _ps(tmp_path, "\n".join([
        f"$a = '{alvo}'",
        "$t = [datetime]::SpecifyKind([datetime]'2026-10-06 15:45:30', 'Utc')",
        "Add-RegistroDeDeploy -Caminho $a -Registro (New-RegistroDeDeploy -Resultado 'ok' -UtcAgora $t "
        "-CommitAntes 'aaa' -MigracaoAntes '121_x' -CommitDepois 'bbb' -MigracaoDepois '123_y' "
        "-Backup '20261006-124324' -Tag 'deploy-20261006-1545' -DuracaoS 96.04 -Opcoes @('PularFrontend'))",
        f"Add-RegistroDeDeploy -Caminho $a -Registro (New-RegistroDeDeploy -Resultado 'falhou' -UtcAgora $t "
        f"-CommitAntes 'bbb' -Backup '20261006-130000' -BackupDoEnsaio $true -Motivo \"{longa}\")",
    ]))
    assert r.returncode == 0, r.stderr
    bruto = alvo.read_bytes()
    assert not bruto.startswith(b"\xef\xbb\xbf")                    # sem BOM: `jq` e `json.loads` leem direto
    linhas = bruto.decode("utf-8").splitlines()
    assert len(linhas) == 2
    ok, falha = (json.loads(x) for x in linhas)
    assert ok == {"ts_utc": "2026-10-06T15:45:30Z", "resultado": "ok", "commit_antes": "aaa", "migracao_antes": "121_x",
                  "commit_depois": "bbb", "migracao_depois": "123_y", "backup": "20261006-124324",
                  "backup_do_ensaio": False, "tag": "deploy-20261006-1545", "motivo": None, "duracao_s": 96.0,
                  "opcoes": ["PularFrontend"]}
    assert falha["resultado"] == "falhou" and falha["backup_do_ensaio"] is True and falha["commit_depois"] is None
    assert "\n" not in falha["motivo"] and len(falha["motivo"]) == 300     # uma linha, no máximo 300 caracteres


@precisa_pwsh
def test_a_tag_anotada_vai_ao_commit_sobe_a_origem_e_pede_o_release_com_notas(tmp_path: Path) -> None:
    repo, origem, commit = _repo_com_origem(tmp_path)
    anotacao = tmp_path / "gh-args.txt"
    r = _ps(tmp_path, "\n".join([
        f"function gh {{ $args -join ' ' | Set-Content -Encoding utf8 '{anotacao}'; $global:LASTEXITCODE = 0 }}",
        "$t = [datetime]::SpecifyKind([datetime]'2026-10-06 15:45:30', 'Utc')",
        f"$r = Publish-TagDeDeploy -Raiz '{repo}' -Commit '{commit}' -Migracao '123_y' -UtcAgora $t",
        "$r | ConvertTo-Json -Compress",
    ]))
    assert r.returncode == 0, r.stderr
    saida = json.loads(r.stdout.strip().splitlines()[-1])
    assert saida == {"tag": "deploy-20261006-1545", "empurrada": True, "release": True, "aviso": None}
    assert _git(repo, "cat-file", "-t", "deploy-20261006-1545") == "tag"            # anotada, não leve
    assert _git(repo, "rev-parse", "deploy-20261006-1545^{commit}") == commit
    assert commit in _git(origem, "ls-remote", "--tags", str(origem)) or "deploy-20261006-1545" in _git(origem, "tag", "--list")
    args = anotacao.read_text(encoding="utf-8-sig").strip()
    assert args.startswith("release create deploy-20261006-1545")
    assert "--generate-notes" in args and "--verify-tag" in args and "--latest=false" in args


@precisa_pwsh
def test_sem_origem_a_tag_fica_local_com_aviso_e_nada_lanca(tmp_path: Path) -> None:
    repo, origem, commit = _repo_com_origem(tmp_path)
    _git(repo, "remote", "set-url", "origin", str(tmp_path / "nao-existe.git"))
    r = _ps(tmp_path, "\n".join([
        "$t = [datetime]::SpecifyKind([datetime]'2026-10-06 15:45:30', 'Utc')",
        f"$r = Publish-TagDeDeploy -Raiz '{repo}' -Commit '{commit}' -Migracao '123_y' -UtcAgora $t",
        "$r | ConvertTo-Json -Compress",
    ]))
    assert r.returncode == 0, r.stderr                                  # o deploy não falha por causa da tag
    saida = json.loads(r.stdout.strip().splitlines()[-1])
    assert saida["tag"] == "deploy-20261006-1545" and saida["empurrada"] is False and saida["release"] is False
    assert "só local" in saida["aviso"]
    assert _git(repo, "tag", "--list", "deploy-*") == "deploy-20261006-1545"


@precisa_pwsh
def test_gh_que_falha_deixa_a_tag_enviada_e_o_aviso(tmp_path: Path) -> None:
    repo, origem, commit = _repo_com_origem(tmp_path)
    r = _ps(tmp_path, "\n".join([
        "function gh { $global:LASTEXITCODE = 1 }",
        "$t = [datetime]::SpecifyKind([datetime]'2026-10-06 15:45:30', 'Utc')",
        f"$r = Publish-TagDeDeploy -Raiz '{repo}' -Commit '{commit}' -Migracao '123_y' -UtcAgora $t",
        "$r | ConvertTo-Json -Compress",
    ]))
    assert r.returncode == 0, r.stderr
    saida = json.loads(r.stdout.strip().splitlines()[-1])
    assert saida["empurrada"] is True and saida["release"] is False and "release pelo gh falhou" in saida["aviso"]


def _deploy() -> str:
    return (SCRIPTS / "deploy.ps1").read_text(encoding="utf-8")


def test_deploy_so_grava_depois_de_parar_e_so_tagueia_depois_de_conferir() -> None:
    d = _deploy()
    param = re.search(r"(?m)^param\(.*\)\s*$", d)
    assert param and "[switch]$SemTag" in param.group(0)               # o `param` segue numa linha (o teste do portão lê assim)
    assert ". (Join-Path $PSScriptRoot 'lib\\historico-de-deploy.ps1')" in d
    # A linha só existe para subida que parou o backend: a marca vem junto do stop, e o ensaio retorna antes.
    marca = d.index("$script:subidaParou = $true")
    assert d.index("Stop-ScheduledTask") > marca > d.index("$supervisionado = ")
    assert d.index("return\n}", d.index("if ($Ensaio) {")) < marca or d.index("return\r\n}", d.index("if ($Ensaio) {")) < marca
    # O `trap` cobre a falha depois do stop; a tag e a linha `ok` só vêm depois da conferência da subida.
    trap = d.index("\ntrap {")
    assert trap < d.index("# ------------------------------------------------------------------ 1. cópia")
    assert "-Resultado 'falhou'" in d[trap:trap + 900] and "$script:subidaParou -and -not $script:subidaRegistrada" in d[trap:trap + 300]
    confere = d.index("a subida não confere")
    tag = d.index("Publish-TagDeDeploy -Raiz")
    ok = d.index("-Resultado 'ok'")
    assert confere < tag < ok
    assert "if (-not $SemTag)" in d[confere:tag]                       # `-SemTag` pula só a tag, nunca a linha do histórico
    # Tag e release são aviso, nunca falha: nada dentro da lib lança para fora.
    lib = LIB.read_text(encoding="utf-8")
    assert "catch {" in lib and "throw" not in lib.split("function Publish-TagDeDeploy")[1]


def test_deploy_nao_reusa_o_nome_de_um_parametro_nas_variaveis_novas() -> None:
    """PowerShell não diferencia caixa: `$semtag`, `$ensaio` etc. seriam o próprio `[switch]`."""
    d = _deploy()
    novas = set(re.findall(r"(?m)^\s*\$(?:script:)?(\w+)\s*=", d[d.index("$inicioDoDeploy"):]))
    parametros = {"ensaio", "stopemulators", "pularbackup", "pularfrontend", "pulardependencias", "semtag"}
    assert not ({n.lower() for n in novas} & parametros)


@precisa_pwsh
def test_tempo_por_etapa_soma_arredonda_e_so_aparece_quando_medido(tmp_path: Path) -> None:
    """29.156 (fatia 2): `Close-EtapaDoDeploy` soma o tempo desde a marca anterior; a linha antiga não ganha campo novo."""
    alvo = tmp_path / "deploys.jsonl"
    r = _ps(tmp_path, "\n".join([
        f"$a = '{alvo}'",
        "$t0 = [datetime]::SpecifyKind([datetime]'2026-10-06 15:00:00', 'Utc')",
        "$e = New-EstadoDeEtapas -Agora $t0",
        "Close-EtapaDoDeploy $e 'backup' -Agora $t0.AddSeconds(12.34)",
        "Close-EtapaDoDeploy $e 'subida' -Agora $t0.AddSeconds(52.34)",
        "Close-EtapaDoDeploy $e 'subida' -Agora $t0.AddSeconds(62.37)",          # mesmo nome soma
        "Close-EtapaDoDeploy $e 'relogio_atrasado' -Agora $t0.AddSeconds(10)",   # relógio andou para trás: nunca negativo
        "Add-RegistroDeDeploy -Caminho $a -Registro (New-RegistroDeDeploy -Resultado 'ok' -UtcAgora $t0 -EtapasS $e.etapas)",
        "Add-RegistroDeDeploy -Caminho $a -Registro (New-RegistroDeDeploy -Resultado 'ok' -UtcAgora $t0)",
        "Add-RegistroDeDeploy -Caminho $a -Registro (New-RegistroDeDeploy -Resultado 'ok' -UtcAgora $t0 -EtapasS ([ordered]@{}))",
    ]))
    assert r.returncode == 0, r.stderr
    com, sem, vazio = (json.loads(x) for x in alvo.read_text(encoding="utf-8").splitlines())
    assert com["etapas_s"] == {"backup": 12.3, "subida": 50.0, "relogio_atrasado": 0.0}
    assert list(com["etapas_s"]) == ["backup", "subida", "relogio_atrasado"]      # a ordem é a do deploy
    assert "etapas_s" not in sem and "etapas_s" not in vazio


def test_deploy_mede_cada_etapa_na_ordem_e_leva_o_tempo_tanto_no_ok_quanto_na_falha() -> None:
    d = _deploy()
    ordem = ["'backup'", "'site'", "'docs_check'", "'painel'", "'parada'", "'dependencias'", "'subida'", "'conferencia'", "'tag'"]
    posicoes = [d.index(f"Close-EtapaDoDeploy $estadoDeEtapas {n}") for n in ordem]
    assert posicoes == sorted(posicoes), "as marcas seguem a ordem das etapas do deploy"
    assert d.index("$estadoDeEtapas = New-EstadoDeEtapas") < posicoes[0]
    trap = d.index("\ntrap {")
    assert "-EtapasS $estadoDeEtapas.etapas" in d[trap:trap + 1200] and "'interrompida'" in d[trap:trap + 600]
    assert "-Resultado 'ok'" in d and "-EtapasS $estadoDeEtapas.etapas" in d[d.index("-Resultado 'ok'"):d.index("-Resultado 'ok'") + 700]
    assert "tempo por etapa (s): " in d
    # só medição: a lib de etapas nunca lança para fora do deploy
    lib = LIB.read_text(encoding="utf-8")
    assert "catch { }" in lib.split("function Close-EtapaDoDeploy")[1].split("function New-RegistroDeDeploy")[0]


@precisa_pwsh
def test_deploy_ps1_continua_sintaticamente_valido(tmp_path: Path) -> None:
    r = _ps(tmp_path, "\n".join([
        "$tokens = $null; $erros = $null",
        f"$null = [System.Management.Automation.Language.Parser]::ParseFile('{SCRIPTS / 'deploy.ps1'}', [ref]$tokens, [ref]$erros)",
        "if ($erros.Count -gt 0) { $erros | ForEach-Object { $_.Message }; exit 1 }",
    ]))
    assert r.returncode == 0, r.stdout + r.stderr


def _repo_com_changelog(tmp: Path) -> tuple[Path, Path, str, str]:
    """Dois commits: o 2º acrescenta entradas ao CHANGELOG (uma com dado de máquina e segredo no título)."""
    repo, origem, c1 = _repo_com_origem(tmp)
    (repo / "CHANGELOG.md").write_text("# Changelog\n\n## 2026-10-05 — 29.1: antiga\n\n- x\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "changelog")
    anterior = _git(repo, "rev-parse", "HEAD")
    (repo / "CHANGELOG.md").write_text(
        "# Changelog\n\n## 2026-10-06 — 29.2: nova com ação (branch feat/x)\n\n- corpo que nao entra\n\n"
        "## 2026-10-06 — 29.3: no 192.168.1.11 / WIN-ABCDEF12 em C:\git\android\data com a@b.com e ghp_ABCDEFGHIJKLMNOP123 "
        "chave Zm9vYmFyQmF6UXV4MTIzNDU2Nzg5MEFiQ2RFZkdoSWpLbE1u (branch feat/adr-081-regra-da-frota-em-configuracoes-do-portal)\n\n"
        "## 2026-10-05 — 29.1: antiga\n\n- x\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "nova entrada")
    return repo, origem, anterior, _git(repo, "rev-parse", "HEAD")


@precisa_pwsh
def test_notas_do_release_trazem_so_as_entradas_novas_do_changelog_sem_dado_de_maquina(tmp_path: Path) -> None:
    repo, _, anterior, commit = _repo_com_changelog(tmp_path)
    _git(repo, "remote", "set-url", "origin", "https://x-access-token:ghp_SEGREDODAORIGEM123456@github.com/dono/repo.git")
    r = _ps(tmp_path, "\n".join([
        f"$n = New-NotasDeRelease -Raiz '{repo}' -Tag 'deploy-20261006-1545' -Commit '{commit}' -CommitAnterior '{anterior}' "
        "-Migracao '127_y' -MigracaoAnterior '125_x'",
        "[Console]::Out.Write($n)",
    ]))
    assert r.returncode == 0, r.stderr
    notas = r.stdout
    assert "Registrado no CHANGELOG desde o deploy anterior (2)" in notas
    assert "29.2: nova com ação (branch feat/x)" in notas
    assert "29.1: antiga" not in notas and "corpo que nao entra" not in notas, "só títulos novos; entrada antiga e corpo ficam fora"
    assert "migração `125_x` → `127_y`" in notas and "1 commits desde" in notas
    for vazou in ("192.168.1.11", "WIN-ABCDEF12", "C:\git", "a@b.com", "ghp_ABCDEFGHIJKLMNOP123", "SEGREDODAORIGEM", "x-access-token"):
        assert vazou not in notas, vazou
    assert "Zm9vYmFy" not in notas and "<token>" in notas, "chave em base64 sai"
    assert "feat/adr-081-regra-da-frota-em-configuracoes-do-portal" in notas, "branch longo em minúsculas não é segredo e fica"
    assert "<ip>" in notas and "<máquina>" in notas and "<caminho>" in notas and "<e-mail>" in notas and "<segredo>" in notas
    assert f"github.com/dono/repo/compare/{anterior}...{commit}" in notas, "só dono/repositório: a credencial da origem nunca entra"


@precisa_pwsh
def test_o_release_usa_o_arquivo_de_notas_do_changelog_e_apaga_o_temporario(tmp_path: Path) -> None:
    repo, origem, anterior, commit = _repo_com_changelog(tmp_path)
    anotacao, copia = tmp_path / "gh-args.txt", tmp_path / "notas-copiadas.md"
    r = _ps(tmp_path, "\n".join([
        f"function gh {{ $args -join ' ' | Set-Content -Encoding utf8 '{anotacao}'; "
        f"$i = [array]::IndexOf($args, '--notes-file'); if ($i -ge 0) {{ Copy-Item -LiteralPath $args[$i + 1] '{copia}'; "
        "$global:ArquivoDeNotas = $args[$i + 1] }; $global:LASTEXITCODE = 0 }",
        "$t = [datetime]::SpecifyKind([datetime]'2026-10-06 15:45:30', 'Utc')",
        f"$r = Publish-TagDeDeploy -Raiz '{repo}' -Commit '{commit}' -Migracao '127_y' -UtcAgora $t -CommitAnterior '{anterior}' -MigracaoAnterior '125_x'",
        "$r | ConvertTo-Json -Compress",
        "if ($global:ArquivoDeNotas) { Test-Path -LiteralPath $global:ArquivoDeNotas } else { 'sem-arquivo' }",
    ]))
    assert r.returncode == 0, r.stderr
    saida = r.stdout.strip().splitlines()
    assert json.loads(saida[-2])["release"] is True
    assert saida[-1] == "False", "o arquivo temporário de notas é apagado depois do release"
    args = anotacao.read_text(encoding="utf-8-sig")
    assert "--notes-file" in args and "--generate-notes" not in args and "--verify-tag" in args and "--latest=false" in args
    assert "29.2: nova com ação" in copia.read_text(encoding="utf-8")


@precisa_pwsh
def test_sem_deploy_anterior_ou_sem_changelog_cai_nas_notas_geradas_pelo_gh(tmp_path: Path) -> None:
    repo, origem, anterior, commit = _repo_com_changelog(tmp_path)
    primeiro = _git(repo, "rev-list", "--max-parents=0", "HEAD")             # neste commit não existe CHANGELOG.md
    anotacao = tmp_path / "gh-args.txt"
    base = [f"function gh {{ $args -join ' ' | Add-Content -Encoding utf8 '{anotacao}'; $global:LASTEXITCODE = 0 }}"]
    for i, extra in enumerate(["", f"-CommitAnterior '{primeiro}'"]):
        r = _ps(tmp_path, "\n".join(base + [
            f"$t = [datetime]::SpecifyKind([datetime]'2026-10-06 15:4{i}:30', 'Utc')",
            f"$r = Publish-TagDeDeploy -Raiz '{repo}' -Commit '{commit}' -Migracao '127_y' -UtcAgora $t {extra}",
            "$r | ConvertTo-Json -Compress",
        ]))
        assert r.returncode == 0, r.stderr
        assert json.loads(r.stdout.strip().splitlines()[-1])["release"] is True
    args = anotacao.read_text(encoding="utf-8-sig")
    assert args.count("--generate-notes") == 2 and "--notes-file" not in args


def test_deploy_passa_o_deploy_anterior_para_as_notas() -> None:
    d = _deploy()
    i = d.index("Publish-TagDeDeploy -Raiz")
    assert "-CommitAnterior $antes.commit -MigracaoAnterior $antes.migration" in d[i:i + 300]
