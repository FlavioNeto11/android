"""29.186: tarefas agendadas do host para os scripts da Canais (resumo diário e espelho do deploy).

Simulado (`simulated`): o `canais-agendadas.ps1` é copiado para uma árvore de teste que tem scripts da Canais FALSOS (imprimem os
argumentos, um texto com cara de token e saem com o código pedido). Nada é registrado no Agendador (`-Instalar` só roda com
`-Simular`) e `-Pedir` nunca dispara a tarefa (`-SemDisparar`).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "canais-agendadas.ps1"
PWSH = shutil.which("pwsh")
precisa_pwsh = pytest.mark.skipif(PWSH is None or sys.platform != "win32", reason="precisa de pwsh no Windows")

FALSO = '''
import os, sys
from pathlib import Path
Path(os.environ["FAKE_MARCA"]).write_text(" ".join(sys.argv[1:]), encoding="utf-8")
print("args:", " ".join(sys.argv[1:]))
print("token 123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA e chave " + "a1" * 16 + " e sha " + "b2" * 20)
sys.exit(int(os.environ.get("FAKE_RC", "0")))
'''


@pytest.fixture()
def arvore(tmp_path, monkeypatch):
    raiz = tmp_path / "pai" / "checkout"
    (raiz / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, raiz / "scripts" / SCRIPT.name)
    for rel in (".claude/canais/resumo_diario.py", ".claude/trello/espelho_do_deploy.py", ".claude/canais/avisos_de_aparelho.py",
                ".claude/canais/saude_dos_lacos.py"):
        arq = raiz / rel
        arq.parent.mkdir(parents=True, exist_ok=True)
        arq.write_text(FALSO, encoding="utf-8")
    marca = tmp_path / "marca.txt"
    monkeypatch.setenv("FAKE_MARCA", str(marca))
    monkeypatch.delenv("FAKE_RC", raising=False)
    return raiz, marca


def _rodar(raiz: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    pasta = raiz / "data" / "canais" / "tarefas"
    return subprocess.run([PWSH, "-NoProfile", "-File", str(raiz / "scripts" / SCRIPT.name), "-Python", sys.executable,
                           "-Pasta", str(pasta), *args], capture_output=True, text=True, timeout=120, encoding="utf-8",
                          errors="replace", env={**os.environ, **(env or {})})


def _log(raiz: Path) -> str:
    logs = sorted((raiz / "data" / "canais" / "tarefas").glob("*.log"))
    assert logs, "nenhum log gravado"
    return logs[-1].read_text(encoding="utf-8")


@precisa_pwsh
class TestInstalarSimulado:
    def test_resumo_diario_e_diario_as_0703_e_a_acao_leva_enviar_sem_segredo(self, arvore):
        r = _rodar(arvore[0], "-Tarefa", "resumo-diario", "-Instalar", "-Simular")
        assert r.returncode == 0, r.stderr
        plano = json.loads(r.stdout)
        assert plano["tarefa"] == "farm-canais-resumo-diario"
        assert "07:03" in plano["gatilho"] and "2026-10-08" in plano["gatilho"]
        assert "-Tarefa resumo-diario -Executar -Enviar" in plano["argumentos"]
        assert not re.search(r"\d{6,}:[A-Za-z0-9_-]{20,}|token|senha|password|secret", plano["argumentos"], re.I)

    def test_espelho_e_sob_demanda_sem_gatilho_e_sem_enviar(self, arvore):
        plano = json.loads(_rodar(arvore[0], "-Tarefa", "espelho-do-deploy", "-Instalar", "-Simular").stdout)
        assert plano["tarefa"] == "farm-canais-espelho-deploy" and "nenhum" in plano["gatilho"]
        assert "-Enviar" not in plano["argumentos"] and "-Tarefa espelho-do-deploy -Executar" in plano["argumentos"]

    def test_a_partir_de_muda_o_dia_do_primeiro_resumo(self, arvore):
        plano = json.loads(_rodar(arvore[0], "-Tarefa", "resumo-diario", "-Instalar", "-Simular", "-APartirDe", "2026-11-02").stdout)
        assert "2026-11-02" in plano["gatilho"]

    @pytest.mark.parametrize("args", [[], ["-Instalar", "-Executar"], ["-Remover", "-Pedir"]])
    def test_exige_exatamente_um_modo(self, arvore, args):
        assert _rodar(arvore[0], "-Tarefa", "resumo-diario", *args).returncode != 0

    def test_pedir_so_vale_para_o_espelho(self, arvore):
        assert _rodar(arvore[0], "-Tarefa", "resumo-diario", "-Pedir", "-Raiz", str(arvore[0]), "-SemDisparar").returncode != 0


@precisa_pwsh
class TestResumoDiario:
    def test_sem_enviar_e_ensaio_imprime_e_nao_passa_enviar(self, arvore):
        raiz, marca = arvore
        r = _rodar(raiz, "-Tarefa", "resumo-diario", "-Executar")
        assert r.returncode == 0, r.stdout + r.stderr
        assert marca.read_text(encoding="utf-8") == "", "o script da Canais não recebeu --enviar"
        assert "fim rc=0" in _log(raiz) and "args:" in r.stdout

    def test_com_enviar_passa_enviar_dentro_do_horario(self, arvore):
        raiz, marca = arvore
        r = _rodar(raiz, "-Tarefa", "resumo-diario", "-Executar", "-Enviar", "-AgoraLocal", "2026-10-08 07:03")
        assert r.returncode == 0, r.stdout + r.stderr
        assert marca.read_text(encoding="utf-8") == "--enviar"

    def test_atrasada_demais_nao_envia(self, arvore):
        raiz, marca = arvore
        r = _rodar(raiz, "-Tarefa", "resumo-diario", "-Executar", "-Enviar", "-AgoraLocal", "2026-10-08 14:00")
        assert r.returncode == 3, r.stdout + r.stderr
        assert not marca.exists(), "o script da Canais não pode ter rodado"
        assert "atrasada demais" in _log(raiz)

    def test_atrasada_com_forcar_envia(self, arvore):
        raiz, marca = arvore
        r = _rodar(raiz, "-Tarefa", "resumo-diario", "-Executar", "-Enviar", "-Forcar", "-AgoraLocal", "2026-10-08 14:00")
        assert r.returncode == 0 and marca.read_text(encoding="utf-8") == "--enviar"

    def test_falha_do_script_vira_codigo_1_e_fica_no_log(self, arvore):
        raiz, _ = arvore
        r = _rodar(raiz, "-Tarefa", "resumo-diario", "-Executar", env={"FAKE_RC": "7"})
        assert r.returncode == 1
        assert "script_rc=7" in _log(raiz)

    def test_o_log_cobre_token_chave_e_nao_cobre_o_sha(self, arvore):
        raiz, _ = arvore
        _rodar(raiz, "-Tarefa", "resumo-diario", "-Executar")
        log = _log(raiz)
        assert "AAAAAAAAAAAAAAAA" not in log and ("a1" * 16) not in log
        assert "***" in log and ("b2" * 20) in log, "SHA de 40 hex continua legível"

    def test_sem_script_da_canais_ou_python_e_codigo_2(self, arvore):
        raiz, marca = arvore
        (raiz / ".claude" / "canais" / "resumo_diario.py").unlink()
        r = _rodar(raiz, "-Tarefa", "resumo-diario", "-Executar")
        assert r.returncode == 2 and not marca.exists()

    def test_logs_com_mais_de_30_dias_somem_e_o_resto_fica(self, arvore):
        raiz, _ = arvore
        pasta = raiz / "data" / "canais" / "tarefas"
        pasta.mkdir(parents=True)
        velho = f"{(datetime.now() - timedelta(days=40)):%Y%m%d}-070300-resumo-diario.log"
        recente = f"{(datetime.now() - timedelta(days=5)):%Y%m%d}-070300-resumo-diario.log"
        (pasta / velho).write_text("x", encoding="utf-8")
        (pasta / recente).write_text("x", encoding="utf-8")
        (pasta / "outra-coisa.txt").write_text("x", encoding="utf-8")
        _rodar(raiz, "-Tarefa", "resumo-diario", "-Executar")
        assert not (pasta / velho).exists() and (pasta / recente).exists() and (pasta / "outra-coisa.txt").exists()


@precisa_pwsh
class TestEspelhoDoDeploy:
    def test_sem_pedido_nao_roda(self, arvore):
        raiz, marca = arvore
        r = _rodar(raiz, "-Tarefa", "espelho-do-deploy", "-Executar")
        assert r.returncode == 4 and not marca.exists()

    def test_pedido_vira_argumentos_e_e_consumido(self, arvore):
        raiz, marca = arvore
        outro = raiz.parent / "deploy-60"
        outro.mkdir()
        p = _rodar(raiz, "-Tarefa", "espelho-do-deploy", "-Pedir", "-Raiz", str(outro), "-Deploy", "60", "-Aplicar", "-SemDisparar")
        assert p.returncode == 0, p.stdout + p.stderr
        r = _rodar(raiz, "-Tarefa", "espelho-do-deploy", "-Executar")
        assert r.returncode == 0, r.stdout + r.stderr
        assert marca.read_text(encoding="utf-8") == f"--raiz {outro} --deploy 60 --aplicar"
        pasta = raiz / "data" / "canais" / "tarefas"
        assert not (pasta / "espelho-pedido.json").exists() and list(pasta.glob("espelho-pedido-*.json"))
        marca.unlink()
        assert _rodar(raiz, "-Tarefa", "espelho-do-deploy", "-Executar").returncode == 4, "pedido consumido não repete"
        assert not marca.exists()

    def test_sem_aplicar_e_sem_numero_o_espelho_so_relata(self, arvore):
        raiz, marca = arvore
        outro = raiz.parent / "raiz"
        outro.mkdir()
        _rodar(raiz, "-Tarefa", "espelho-do-deploy", "-Pedir", "-Raiz", str(outro), "-SemDisparar")
        assert _rodar(raiz, "-Tarefa", "espelho-do-deploy", "-Executar").returncode == 0
        assert marca.read_text(encoding="utf-8") == f"--raiz {outro}"

    @pytest.mark.parametrize("raiz_pedida", [r"C:\Windows", "..\\..\\..\\Windows"])
    def test_raiz_fora_da_pasta_dos_checkouts_e_recusada(self, arvore, raiz_pedida):
        raiz, marca = arvore
        _rodar(raiz, "-Tarefa", "espelho-do-deploy", "-Pedir", "-Raiz", raiz_pedida, "-SemDisparar")
        r = _rodar(raiz, "-Tarefa", "espelho-do-deploy", "-Executar")
        assert r.returncode == 5 and not marca.exists(), r.stdout + r.stderr
        assert "pedido inválido" in _log(raiz)

    def test_deploy_fora_de_faixa_e_recusado_no_pedir(self, arvore):
        raiz, _ = arvore
        r = _rodar(raiz, "-Tarefa", "espelho-do-deploy", "-Pedir", "-Raiz", str(raiz.parent), "-Deploy", "1000", "-SemDisparar")
        assert r.returncode != 0


@precisa_pwsh
class TestLacoESaude:
    def test_laco_instala_ao_ligar_o_host_com_enviar_e_sem_limite(self, arvore):
        plano = json.loads(_rodar(arvore[0], "-Tarefa", "laco-de-aparelhos", "-Instalar", "-Simular").stdout)
        assert plano["tarefa"] == "farm-canais-laco-de-aparelhos"
        assert "ao ligar o host" in plano["gatilho"] and "sem limite" in plano["limite"]
        assert "-Tarefa laco-de-aparelhos -Executar -Enviar" in plano["argumentos"]

    def test_saude_instala_a_cada_5_min_com_enviar(self, arvore):
        plano = json.loads(_rodar(arvore[0], "-Tarefa", "saude-dos-lacos", "-Instalar", "-Simular").stdout)
        assert plano["tarefa"] == "farm-canais-saude-dos-lacos"
        assert "5 min" in plano["gatilho"] and "4 min" in plano["limite"]
        assert "-Tarefa saude-dos-lacos -Executar -Enviar" in plano["argumentos"]

    def test_laco_com_enviar_passa_laco_e_intervalo_e_grava_no_log_do_dia(self, arvore):
        raiz, marca = arvore
        r = _rodar(raiz, "-Tarefa", "laco-de-aparelhos", "-Executar", "-Enviar")
        assert r.returncode == 0, r.stdout + r.stderr
        assert marca.read_text(encoding="utf-8") == "--laco --intervalo-s 120"
        logs = list((raiz / "data" / "canais" / "tarefas").glob("*-000000-laco-de-aparelhos.log"))
        assert len(logs) == 1, "um log por dia, não um por execução"
        texto = logs[0].read_text(encoding="utf-8")
        assert "fim rc=0" in texto and "args: --laco --intervalo-s 120" in texto
        assert "AAAAAAAAAAAAAAAA" not in texto and "***" in texto, "token coberto também na saída em fluxo"

    def test_laco_sem_enviar_e_so_um_ciclo_de_ensaio(self, arvore):
        raiz, marca = arvore
        r = _rodar(raiz, "-Tarefa", "laco-de-aparelhos", "-Executar")
        assert r.returncode == 0, r.stdout + r.stderr
        assert marca.read_text(encoding="utf-8") == "", "sem --laco, sem envio"
        assert "args:" in r.stdout

    def test_laco_que_falha_vira_codigo_1(self, arvore):
        raiz, _ = arvore
        r = _rodar(raiz, "-Tarefa", "laco-de-aparelhos", "-Executar", "-Enviar", env={"FAKE_RC": "5"})
        assert r.returncode == 1
        assert "script_rc=5" in "".join(p.read_text(encoding="utf-8") for p in (raiz / "data" / "canais" / "tarefas").glob("*.log"))

    def test_saude_com_enviar_passa_avisar_e_nunca_religar(self, arvore):
        raiz, marca = arvore
        r = _rodar(raiz, "-Tarefa", "saude-dos-lacos", "-Executar", "-Enviar")
        assert r.returncode == 0, r.stdout + r.stderr
        assert marca.read_text(encoding="utf-8") == "--avisar"
        assert "--religar" not in SCRIPT.read_text(encoding="utf-8").split("#>", 1)[1].replace("`--religar` não é usado", "")

    def test_saude_sem_enviar_so_imprime(self, arvore):
        raiz, marca = arvore
        r = _rodar(raiz, "-Tarefa", "saude-dos-lacos", "-Executar")
        assert r.returncode == 0 and marca.read_text(encoding="utf-8") == "" and "args:" in r.stdout

    def test_sem_o_script_da_canais_e_codigo_2(self, arvore):
        raiz, marca = arvore
        (raiz / ".claude" / "canais" / "saude_dos_lacos.py").unlink()
        assert _rodar(raiz, "-Tarefa", "saude-dos-lacos", "-Executar").returncode == 2 and not marca.exists()


def test_o_script_nao_tem_segredo_nem_mexe_em_wsl_tunel_relogio_nem_mata_processo():
    texto = SCRIPT.read_text(encoding="utf-8")
    codigo = texto[texto.index("#>") + 2:]
    codigo = "\n".join(l for l in codigo.splitlines() if not l.lstrip().startswith("#"))
    for proibido in ("wslconfig", "wsl.exe", "Stop-Process", "taskkill", "w32tm", "Set-Date", "ssh ", ".env", "config.yaml",
                     "Invoke-WebRequest", "Invoke-RestMethod", "curl"):
        assert proibido not in codigo, proibido
    # o -Instalar real só registra: nenhum Start-ScheduledTask fora do -Pedir
    assert codigo.count("Start-ScheduledTask") == 1
