"""29.198: o deploy mantém o amostrador do host em dia (scripts/lib/amostrador-do-deploy.ps1 e o bloco do deploy.ps1).

Tudo com dublês: a tarefa agendada, o processo e o amostrador são funções/arquivos FALSOS num diretório temporário; nenhuma tarefa de verdade
é tocada e o `deploy.ps1` inteiro não roda (o trecho entre `# >>> amostrador do deploy (29.198)` e `# <<<` roda debaixo do MESMO `param` dele).
Prova simulada; a real é o próximo deploy que mude o cabeçalho do CSV.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
LIB = SCRIPTS / "lib" / "amostrador-do-deploy.ps1"
PWSH = shutil.which("pwsh")

precisa_pwsh = pytest.mark.skipif(PWSH is None, reason="sem pwsh nesta máquina")

V1 = "ts_utc,cpu_host_pct,vm_convidado_nucleos,vmmem_ws_mb,qemu_host_pct,ram_livre_mb,disco_livre_gb,processos_top,avisos_pressao"
V2 = V1 + ",cpu_media_pct,demais_processos_pct,nao_atribuido_pct"


def _ps(tmp: Path, corpo: str, timeout: int = 120) -> subprocess.CompletedProcess:
    arq = tmp / "bloco.ps1"
    arq.write_text("\n".join(["$ErrorActionPreference = 'Stop'", "[Console]::OutputEncoding = [Text.Encoding]::UTF8", f". '{LIB}'", corpo]),
                   encoding="utf-8-sig")
    return subprocess.run([PWSH, "-NoProfile", "-File", str(arq)], capture_output=True, text=True, timeout=timeout, encoding="utf-8",
                          errors="replace")


def _json(r: subprocess.CompletedProcess) -> dict:
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads([ln for ln in r.stdout.splitlines() if ln.startswith("{")][-1])


def _decisao(tmp: Path, **kw) -> dict:
    def ps(v):
        return "$null" if v is None else (f"'{v}'" if isinstance(v, str) else ("$true" if v is True else "$false" if v is False else str(v)))
    args = " ".join(f"-{k} {ps(v)}" for k, v in kw.items())
    return _json(_ps(tmp, f"Get-DecisaoDoAmostrador {args} | ConvertTo-Json -Compress"))


@precisa_pwsh
class TestDecisao:
    def test_tarefa_nao_registrada_nao_faz_nada_e_diz_como_registrar(self, tmp_path):
        d = _decisao(tmp_path, Registrada=False, Rodando=False)
        assert d["acao"] == "nada" and "Instalar" in d["motivo"]

    def test_registrada_mas_parada_reinstala(self, tmp_path):
        d = _decisao(tmp_path, Registrada=True, Rodando=False)
        assert d["acao"] == "reinstalar" and "não está rodando" in d["motivo"]

    def test_cabecalho_do_csv_diferente_do_script_reinstala_e_conta_as_colunas(self, tmp_path):
        d = _decisao(tmp_path, Registrada=True, Rodando=True, CabecalhoDoScript=V2, CabecalhoDoCsv=V1)
        assert d["acao"] == "reinstalar" and "tem 9 colunas" in d["motivo"] and "script tem 12" in d["motivo"]

    def test_cabecalho_igual_e_processo_novo_esta_em_dia(self, tmp_path):
        agora = datetime(2026, 10, 7, 3, 0, 0)
        d = _decisao(tmp_path, Registrada=True, Rodando=True, CabecalhoDoScript=V2, CabecalhoDoCsv=V2,
                     ProcessoDesde=f"{agora + timedelta(hours=1):%Y-%m-%dT%H:%M:%S}", ScriptEm=f"{agora:%Y-%m-%dT%H:%M:%S}")
        assert d == {"acao": "nada", "motivo": "em dia"}

    def test_script_mais_novo_que_o_processo_reinstala_mesmo_com_cabecalho_igual(self, tmp_path):
        agora = datetime(2026, 10, 7, 3, 0, 0)
        d = _decisao(tmp_path, Registrada=True, Rodando=True, CabecalhoDoScript=V2, CabecalhoDoCsv=V2,
                     ProcessoDesde=f"{agora - timedelta(hours=2):%Y-%m-%dT%H:%M:%S}", ScriptEm=f"{agora:%Y-%m-%dT%H:%M:%S}")
        assert d["acao"] == "reinstalar" and "mais novo" in d["motivo"]

    def test_script_so_30_s_mais_novo_nao_e_mudanca(self, tmp_path):
        agora = datetime(2026, 10, 7, 3, 0, 0)
        d = _decisao(tmp_path, Registrada=True, Rodando=True, CabecalhoDoScript=V2, CabecalhoDoCsv=V2,
                     ProcessoDesde=f"{agora:%Y-%m-%dT%H:%M:%S}", ScriptEm=f"{agora + timedelta(seconds=30):%Y-%m-%dT%H:%M:%S}")
        assert d["acao"] == "nada"

    def test_sem_csv_ou_sem_cabecalho_do_script_nao_inventa_diferenca(self, tmp_path):
        assert _decisao(tmp_path, Registrada=True, Rodando=True, CabecalhoDoScript=V2, CabecalhoDoCsv="")["acao"] == "nada"
        assert _decisao(tmp_path, Registrada=True, Rodando=True, CabecalhoDoScript="", CabecalhoDoCsv=V1)["acao"] == "nada"


@precisa_pwsh
class TestLeituras:
    def test_cabecalho_do_script_de_verdade_e_o_v2_de_12_colunas(self, tmp_path):
        r = _ps(tmp_path, f"Get-CabecalhoDoScript -Script '{SCRIPTS / 'amostrador-host.ps1'}'")
        assert r.returncode == 0, r.stdout + r.stderr
        cab = r.stdout.strip()
        assert cab.startswith("ts_utc,") and cab.endswith("nao_atribuido_pct") and len(cab.split(",")) == 12

    def test_ultimo_cabecalho_do_csv_mais_recente_vale_o_anexado_no_meio_do_dia(self, tmp_path):
        pasta = tmp_path / "host"
        pasta.mkdir()
        (pasta / "20261006.csv").write_text(V1 + "\n2026-10-06T23:59:01Z,1,1,1,1,1,1,,0\n", encoding="utf-8")
        (pasta / "20261007.csv").write_text(V1 + "\n2026-10-07T02:34:01Z,6.6,0.24,6973,2.1,25214,118.2,,0\n" + V2 + "\n", encoding="utf-8")
        assert _ps(tmp_path, f"Get-CabecalhoDoUltimoCsv -Pasta '{pasta}'").stdout.strip() == V2
        assert _ps(tmp_path, f"Get-CabecalhoDoUltimoCsv -Pasta '{tmp_path / 'nao-existe'}'").stdout.strip() == ""


def _montar(tmp: Path, *, registrada=True, rodando=True, csv_v1=True, processo_novo=True, nova_linha=True, serie_manual=False) -> str:
    """Um 'host' falso: tarefa, processo, amostrador e CSV. Devolve o corpo PowerShell que chama o Update e imprime o resultado."""
    (tmp / "scripts").mkdir(exist_ok=True)
    pasta = tmp / "data" / "observabilidade" / "host"
    pasta.mkdir(parents=True, exist_ok=True)
    log = tmp / "log.txt"
    log.write_text("", encoding="utf-8")
    csv = pasta / "20261007.csv"
    csv.write_text((V1 if csv_v1 else V2) + "\n2026-10-07T02:34:01Z,6.6,0.24,6973,2.1,25214,118.2,,0\n", encoding="utf-8")
    (tmp / "scripts" / "amostrador-host.ps1").write_text(
        f"param([switch]$Instalar)\n$cabecalho = '{V2}'\nif ($Instalar) {{ Add-Content -LiteralPath '{log}' 'instalar' }}\n", encoding="utf-8")
    desde = datetime.now() + timedelta(hours=1) if processo_novo else datetime.now() - timedelta(hours=2)
    return "\n".join([
        f"function Get-ScheduledTask {{ [CmdletBinding()] param($TaskName) if ({'$true' if registrada else '$false'}) {{ [pscustomobject]@{{ TaskName = $TaskName }} }} }}",
        # A série manual do DevOps (devops-amostrador-host.ps1, de dias atrás) NÃO é o amostrador da tarefa e vem ANTES na lista.
        f"function Get-CimInstance {{ [CmdletBinding()] param([Parameter(Position=0)]$ClassName) if ({'$true' if serie_manual else '$false'}) "
        "{ [pscustomobject]@{ CommandLine = 'pwsh -File x/.claude/handoffs/devops-amostrador-host.ps1 -Minutos 1440'; ProcessId = 88888; "
        "CreationDate = [datetime]'2020-01-01T00:00:00' } }; "
        f"if ({'$true' if rodando else '$false'}) "
        f"{{ [pscustomobject]@{{ CommandLine = 'pwsh -File x/scripts/amostrador-host.ps1'; ProcessId = 99999; CreationDate = [datetime]'{desde:%Y-%m-%dT%H:%M:%S}' }} }} }}",
        f"function Stop-ScheduledTask {{ [CmdletBinding()] param($TaskName) Add-Content -LiteralPath '{log}' 'stop' }}",
        f"function Start-ScheduledTask {{ [CmdletBinding()] param($TaskName) Add-Content -LiteralPath '{log}' 'start'; "
        + (f"Add-Content -LiteralPath '{csv}' '2026-10-07T02:34:55Z,13.6,0.31,6973,,24756,118.3,,0,,,' }}" if nova_linha else "}"),
    ])


def _atualizar(tmp: Path, extra: str = "", **kw) -> tuple[dict, list[str]]:
    corpo = _montar(tmp, **kw) + f"\n$r = Update-AmostradorDoDeploy -Raiz '{tmp}' -PausaS 0 -EsperaMaximaS 2 {extra}\n$r | ConvertTo-Json -Compress"
    res = _json(_ps(tmp, corpo))
    return res, (tmp / "log.txt").read_text(encoding="utf-8").split()


@precisa_pwsh
class TestAcao:
    def test_cabecalho_velho_para_instala_e_inicia_nessa_ordem_e_mede_a_lacuna(self, tmp_path):
        res, chamadas = _atualizar(tmp_path)
        assert chamadas == ["stop", "instalar", "start"]
        assert res["resultado"] == "reinstalado" and res["lacuna_s"] == 54, "02:34:01Z -> 02:34:55Z"
        assert "9 colunas" in res["motivo"]

    def test_em_dia_nao_toca_a_tarefa(self, tmp_path):
        res, chamadas = _atualizar(tmp_path, csv_v1=False)
        assert res["resultado"] == "nada" and chamadas == []

    def test_nao_registrada_nao_toca_nada(self, tmp_path):
        res, chamadas = _atualizar(tmp_path, registrada=False, rodando=False)
        assert res["resultado"] == "nada" and chamadas == [] and "registrada" in res["motivo"]

    def test_parado_reinstala(self, tmp_path):
        res, chamadas = _atualizar(tmp_path, rodando=False, csv_v1=False)
        assert res["resultado"] == "reinstalado" and chamadas == ["stop", "instalar", "start"]

    def test_script_mais_novo_que_o_processo_reinstala_mesmo_com_cabecalho_igual(self, tmp_path):
        res, chamadas = _atualizar(tmp_path, csv_v1=False, processo_novo=False)
        assert res["resultado"] == "reinstalado" and "mais novo" in res["motivo"]

    def test_a_serie_manual_do_devops_nao_e_confundida_com_o_amostrador_da_tarefa(self, tmp_path):
        res, chamadas = _atualizar(tmp_path, csv_v1=False, serie_manual=True)
        assert res["resultado"] == "nada" and chamadas == [], "o processo da tarefa e novo; o de 2020 e outro script"

    def test_simular_so_diz_o_que_faria(self, tmp_path):
        res, chamadas = _atualizar(tmp_path, extra="-Simular")
        assert res["resultado"] == "simulado" and chamadas == [] and "Stop + -Instalar + Start" in res["motivo"]

    def test_sem_linha_nova_no_prazo_e_falha_nunca_sucesso(self, tmp_path):
        res, chamadas = _atualizar(tmp_path, nova_linha=False)
        assert chamadas == ["stop", "instalar", "start"]
        assert res["resultado"] == "falhou" and "nenhuma linha nova" in res["motivo"]


# --- o bloco do deploy.ps1 ------------------------------------------------------------------------------------------------

def _bloco_do_deploy() -> tuple[str, str]:
    d = (SCRIPTS / "deploy.ps1").read_text(encoding="utf-8")
    param = re.search(r"(?m)^param\(.*\)\s*$", d)
    assert param, "o deploy.ps1 perdeu o `param(...)` de uma linha"
    ini = d.index("# >>> amostrador do deploy (29.198)")
    fim = d.index("# <<< amostrador do deploy (29.198)")
    assert ini < fim
    return param.group(0), d[ini:fim]


def _rodar_bloco(tmp: Path, update: str, opcoes: str = "") -> dict:
    param, trecho = _bloco_do_deploy()
    (tmp / "bloco-deploy.ps1").write_text("\n".join([
        param,
        "$ErrorActionPreference = 'Stop'",
        "[Console]::OutputEncoding = [Text.Encoding]::UTF8",
        f". '{SCRIPTS / 'lib' / 'historico-de-deploy.ps1'}'",
        f"$root = '{tmp}'",
        "$estadoDeEtapas = New-EstadoDeEtapas",
        update,
        trecho,
        "$registro = New-RegistroDeDeploy -Resultado 'ok' -EtapasS $estadoDeEtapas.etapas @argumentosDoAmostrador",
        "$registro | ConvertTo-Json -Compress -Depth 4",
    ]), encoding="utf-8-sig")
    r = subprocess.run([PWSH, "-NoProfile", "-File", str(tmp / "bloco-deploy.ps1"), *opcoes.split()], capture_output=True, text=True,
                       timeout=120, encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stdout + r.stderr
    reg = json.loads([ln for ln in r.stdout.splitlines() if ln.startswith("{")][-1])
    reg["_saida"] = r.stdout + r.stderr
    return reg


@precisa_pwsh
class TestBlocoDoDeploy:
    def test_amostrador_reinstalado_vai_para_a_linha_com_motivo_e_lacuna(self, tmp_path):
        reg = _rodar_bloco(tmp_path, "function Update-AmostradorDoDeploy { param($Raiz) @{ resultado = 'reinstalado'; motivo = 'cabecalho'; lacuna_s = 54 } }")
        assert reg["amostrador"] == "reinstalado" and reg["amostrador_motivo"] == "cabecalho" and reg["amostrador_lacuna_s"] == 54
        assert "amostrador" in reg["etapas_s"]

    def test_em_dia_nao_muda_o_formato_da_linha(self, tmp_path):
        reg = _rodar_bloco(tmp_path, "function Update-AmostradorDoDeploy { param($Raiz) @{ resultado = 'nada'; motivo = 'em dia'; lacuna_s = $null } }")
        assert "amostrador" not in reg and "amostrador_motivo" not in reg and "amostrador" in reg["etapas_s"]

    def test_excecao_nunca_derruba_o_deploy_e_fica_na_linha_como_falhou(self, tmp_path):
        reg = _rodar_bloco(tmp_path, "function Update-AmostradorDoDeploy { param($Raiz) throw 'quebrou' }")
        assert reg["amostrador"] == "falhou" and "conferir o amostrador" in reg["amostrador_motivo"]
        assert "O deploy segue" in reg["_saida"]

    def test_sem_amostrador_pula_e_nem_chama(self, tmp_path):
        reg = _rodar_bloco(tmp_path, "function Update-AmostradorDoDeploy { param($Raiz) throw 'nao deveria ser chamado' }", opcoes="-SemAmostrador")
        assert "amostrador" not in reg

    def test_resultado_falhou_avisa_e_segue(self, tmp_path):
        reg = _rodar_bloco(tmp_path, "function Update-AmostradorDoDeploy { param($Raiz) @{ resultado = 'falhou'; motivo = 'sem linha nova'; lacuna_s = $null } }")
        assert reg["amostrador"] == "falhou" and "O deploy segue" in reg["_saida"]
