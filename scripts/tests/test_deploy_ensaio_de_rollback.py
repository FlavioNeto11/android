"""29.156 (fatia 4): o `deploy.ps1` chama o ensaio de rollback só quando o deploy trouxe migração, e o resultado nunca reverte nada.

Rodar o `deploy.ps1` inteiro pararia o ambiente central. Como no teste do portão do ensaio, o trecho entre os marcadores
`# >>> ensaio do rollback (29.156)` e `# <<<` roda debaixo do MESMO `param` do deploy (lido da linha dele), com a biblioteca de
verdade e um `rollback-ensaio.ps1` FALSO que devolve o código de saída pedido. Prova simulada; a real é o próximo deploy com migração.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
PWSH = shutil.which("pwsh")

precisa_pwsh = pytest.mark.skipif(PWSH is None, reason="sem pwsh nesta máquina")


def _deploy() -> str:
    return (SCRIPTS / "deploy.ps1").read_text(encoding="utf-8")


def _trecho() -> tuple[str, str]:
    d = _deploy()
    param = re.search(r"(?m)^param\(.*\)\s*$", d)
    assert param, "o deploy.ps1 perdeu o `param(...)` de uma linha"
    ini = d.index("# >>> ensaio do rollback (29.156)")
    fim = d.index("# <<< ensaio do rollback (29.156)")
    assert ini < fim
    return param.group(0), d[ini:fim]


def _rodar(tmp: Path, *, codigo_do_ensaio: int | str, antes: str | None = "migracao-1", depois: str | None = "migracao-2",
           backup: str | None = "20261006-181424", opcoes: str = "", motivo: str = "") -> tuple[dict, str]:
    scripts = tmp / "scripts"
    (tmp / "data" / "rollback-ensaio").mkdir(parents=True)
    scripts.mkdir()
    marcador = tmp / "chamado.txt"
    (tmp / "data" / "rollback-ensaio" / "ultimo.json").write_text(json.dumps({"motivo": motivo}), encoding="utf-8")
    corpo = (f"'x' | Set-Content -LiteralPath '{marcador}'\n"
             + ("throw 'quebrou'\n" if codigo_do_ensaio == "lanca" else f"exit {codigo_do_ensaio}\n"))
    (scripts / "rollback-ensaio.ps1").write_text(corpo, encoding="utf-8")
    param, trecho = _trecho()
    a = "$null" if antes is None else f"[pscustomobject]@{{ commit = 'aaa'; migration = '{antes}' }}"
    dep = "$null" if depois is None else f"[pscustomobject]@{{ commit = 'bbb'; migration = '{depois}' }}"
    pb = "$null" if backup is None else f"'{backup}'"
    (scripts / "bloco.ps1").write_text("\n".join([
        param,
        "$ErrorActionPreference = 'Stop'",
        "[Console]::OutputEncoding = [Text.Encoding]::UTF8",
        f". '{SCRIPTS / 'lib' / 'historico-de-deploy.ps1'}'",
        f"$root = '{tmp}'",
        f"$antes = {a}", f"$depois = {dep}", f"$pastaDoBackup = {pb}",
        "$estadoDeEtapas = New-EstadoDeEtapas",
        trecho,
        "$registro = New-RegistroDeDeploy -Resultado 'ok' -EtapasS $estadoDeEtapas.etapas @argumentosDoEnsaioDeRollback",
        "$registro | ConvertTo-Json -Compress -Depth 4",
    ]), encoding="utf-8-sig")
    r = subprocess.run([PWSH, "-NoProfile", "-File", str(scripts / "bloco.ps1"), *opcoes.split()],
                       capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stdout + r.stderr
    linha = [l for l in r.stdout.splitlines() if l.startswith("{")][-1]
    reg = json.loads(linha)
    reg["_chamado"] = marcador.exists()
    return reg, r.stdout + r.stderr


@precisa_pwsh
class TestPassoDoRollback:
    def test_deploy_com_migracao_e_ensaio_ok_grava_ok_na_linha(self, tmp_path):
        reg, saida = _rodar(tmp_path, codigo_do_ensaio=0)
        assert reg["_chamado"] and reg["ensaio_de_rollback"] == "ok" and reg.get("ensaio_de_rollback_motivo") is None
        assert "ensaio_de_rollback" in reg["etapas_s"]
        assert "NÃO está provado" not in saida

    def test_saida_2_pulado_nunca_aprova_e_vira_aviso_com_o_motivo(self, tmp_path):
        reg, saida = _rodar(tmp_path, codigo_do_ensaio=2, motivo="a pasta do backup da linha não existe")
        assert reg["ensaio_de_rollback"] == "pulado"
        assert reg["ensaio_de_rollback_motivo"] == "a pasta do backup da linha não existe"
        assert "ensaio do rollback: pulado" in saida and "NÃO está provado" in saida

    @pytest.mark.parametrize("codigo", [1, 3, 255, "lanca"])
    def test_falha_ou_quebra_do_ensaio_vira_falhou_na_linha_e_nao_derruba_o_deploy(self, tmp_path, codigo):
        reg, saida = _rodar(tmp_path, codigo_do_ensaio=codigo, motivo="o código antigo quis aplicar migração")
        assert reg["ensaio_de_rollback"] == "falhou"
        assert "NÃO está provado" in saida          # o trecho terminou (returncode 0 em _rodar): nada lançou para fora

    @pytest.mark.parametrize("cenario", [
        {"antes": "migracao-1", "depois": "migracao-1"},       # deploy sem migração nova
        {"antes": None},                                       # não havia saúde antes
        {"depois": None},                                      # sem saúde depois
        {"backup": None},                                      # sem pasta de backup
    ])
    def test_sem_migracao_nova_ou_sem_dados_nao_chama_o_ensaio(self, tmp_path, cenario):
        reg, _ = _rodar(tmp_path, codigo_do_ensaio=0, **cenario)
        assert not reg["_chamado"] and "ensaio_de_rollback" not in reg

    def test_o_interruptor_pula_o_ensaio(self, tmp_path):
        reg, _ = _rodar(tmp_path, codigo_do_ensaio=0, opcoes="-SemEnsaioDeRollback")
        assert not reg["_chamado"] and "ensaio_de_rollback" not in reg


def test_deploy_chama_o_ensaio_depois_da_tag_e_antes_de_gravar_a_linha_e_nunca_lanca() -> None:
    d = _deploy()
    param = re.search(r"(?m)^param\(.*\)\s*$", d)
    assert param and "[switch]$SemEnsaioDeRollback" in param.group(0)
    tag = d.index("Close-EtapaDoDeploy $estadoDeEtapas 'tag'")
    ini = d.index("# >>> ensaio do rollback (29.156)")
    fim = d.index("# <<< ensaio do rollback (29.156)")
    linha = d.index("-Resultado 'ok'")
    assert tag < ini < fim < linha, "o ensaio roda com a subida já conferida e a tag feita, e antes da linha do histórico"
    trecho = d[ini:fim]
    assert "throw" not in trecho and "catch {" in trecho, "o ensaio nunca derruba nem reverte o deploy"
    assert "Stop-ScheduledTask" not in trecho and "restore.ps1" not in trecho and "git " not in trecho.replace("rollback-ensaio", "")
    assert "@argumentosDoEnsaioDeRollback" in d[linha:linha + 900]
    assert "$script:subidaParou = $true" in d[:ini], "o ensaio só vem depois do ponto em que o deploy parou o backend"


def test_a_lib_so_aceita_ok_falhou_ou_pulado_e_corta_o_motivo() -> None:
    lib = (SCRIPTS / "lib" / "historico-de-deploy.ps1").read_text(encoding="utf-8")
    assert "[ValidateSet('ok', 'falhou', 'pulado')][string]$EnsaioDeRollback" in lib
    assert "Substring(0, 200)" in lib
