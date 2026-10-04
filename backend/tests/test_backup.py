"""Backup, restauração e o retrato do que está no ar.

A ausência que isto fecha: não havia script nem procedimento. `docs/banco.md` dizia "backup é copiar", e copiar é
exatamente o que não funciona aqui — o banco roda em WAL, e o `.sqlite3` sozinho, com o backend escrevendo, sai sem
as últimas transações. O primeiro teste demonstra a perda em vez de afirmá-la.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
SCRIPTS = RAIZ / "scripts"


def _carregar_copia():  # type: ignore[no-untyped-def]
    """O arquivo tem hífen no nome (é um utilitário de linha de comando, não um módulo importável)."""
    spec = importlib.util.spec_from_file_location("sqlite_copia", SCRIPTS / "sqlite-copia.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _banco_com_wal_vivo(caminho: Path) -> sqlite3.Connection:
    """Um banco em WAL com transações confirmadas e uma conexão AINDA ABERTA — o estado da produção, em que o
    conteúdo do `-wal` ainda não foi incorporado ao `.sqlite3`."""
    conn = sqlite3.connect(str(caminho))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
    conn.execute("INSERT INTO schema_migrations VALUES ('017_busca_sem_acento', '2026-09-22T00:00:00Z')")
    conn.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY, nota TEXT)")
    conn.commit()
    conn.executemany("INSERT INTO runs(nota) VALUES (?)", [(f"execução {i}",) for i in range(500)])
    conn.commit()                      # confirmado, mas ainda no -wal: nenhum checkpoint aconteceu
    return conn


def test_copiar_so_o_arquivo_perde_o_wal_e_a_api_de_backup_nao(tmp_path: Path) -> None:
    """O motivo de existir `sqlite-copia.py`, medido em vez de afirmado: `shutil.copy` do `.sqlite3` com o WAL
    vivo devolve um banco que ABRE, parece íntegro e está velho — o pior formato de falha que um backup tem."""
    banco = tmp_path / "poc.sqlite3"
    conn = _banco_com_wal_vivo(banco)
    try:
        ingenua = tmp_path / "ingenua.sqlite3"
        shutil.copy(banco, ingenua)                 # exatamente o "backup é copiar" da documentação antiga
        try:
            with sqlite3.connect(str(ingenua)) as c:
                perdidas: int | str = c.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        except sqlite3.DatabaseError as exc:
            # Medido neste host: a cópia ingênua nem tem a TABELA — o `CREATE TABLE` também estava no -wal.
            perdidas = f"a cópia sequer abre a tabela: {exc}"

        info = _carregar_copia().copiar(banco, tmp_path / "boa.sqlite3")
        with sqlite3.connect(str(tmp_path / "boa.sqlite3")) as c:
            copiadas = c.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    finally:
        conn.close()

    assert copiadas == 500
    assert perdidas != 500, ("neste host o checkpoint aconteceu sozinho e a cópia ingênua não perdeu nada; "
                             "a prova da API de backup abaixo continua valendo")
    assert info["integrity_check"] == "ok"
    assert info["migration"] == "017_busca_sem_acento"
    assert info["tables"] >= 2


def test_copia_nao_escreve_na_origem(tmp_path: Path) -> None:
    """A origem é aberta em `mode=ro`: um backup nunca pode danificar o banco que ele veio proteger, nem por
    engano de digitação nos argumentos."""
    banco = tmp_path / "poc.sqlite3"
    conn = _banco_com_wal_vivo(banco)
    try:
        antes = banco.stat().st_mtime_ns
        _carregar_copia().copiar(banco, tmp_path / "saida.sqlite3")
        assert banco.stat().st_mtime_ns == antes
    finally:
        conn.close()


def _raiz_isolada(tmp_path: Path) -> Path:
    """Uma raiz de projeto MÍNIMA numa pasta temporária: os scripts reais, um `config/` de fixture e o interpretador do
    venv em uso. `backup.ps1` e `restore.ps1` descobrem a raiz por `$PSScriptRoot`, então rodar a cópia faz o teste não
    depender do `config.yaml` da instalação (que num `git worktree` ou checkout limpo não existe) nem tocar nele."""
    venv_py = Path(sys.executable)
    cfg_venv = venv_py.parent.parent / "pyvenv.cfg"
    if sys.prefix == sys.base_prefix or not cfg_venv.exists():
        pytest.skip("o teste precisa rodar num venv: os scripts usam o python.exe do venv do backend")
    raiz = tmp_path / "projeto"
    shutil.copytree(SCRIPTS, raiz / "scripts", ignore=shutil.ignore_patterns("tests", "__pycache__"))
    (raiz / "config").mkdir()
    (raiz / "config" / "config.yaml").write_text("# fixture do teste de backup\nserver: {port: 0}\n", encoding="utf-8")
    destino = raiz / "backend" / ".venv"
    (destino / "Scripts").mkdir(parents=True)
    shutil.copy2(venv_py, destino / "Scripts" / "python.exe")
    shutil.copy2(cfg_venv, destino / "pyvenv.cfg")
    return raiz


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh não está no PATH")
def test_backup_e_restore_ensaio_de_ponta_a_ponta(tmp_path: Path) -> None:
    """O ensaio que o achado pedia: a restauração é exercitada numa pasta limpa, e é ela que prova que o
    procedimento funciona — procedimento de recuperação que ninguém executa se descobre quebrado no pior dia."""
    raiz = _raiz_isolada(tmp_path)
    scripts = raiz / "scripts"
    banco = tmp_path / "poc.sqlite3"
    conn = _banco_com_wal_vivo(banco)
    destino = tmp_path / "backups"
    try:
        r = subprocess.run(["pwsh", "-NoProfile", "-File", str(scripts / "backup.ps1"),
                            "-Banco", str(banco), "-Destino", str(destino), "-Reter", "0"],
                           capture_output=True, text=True, timeout=180, cwd=str(raiz))
    finally:
        conn.close()
    assert r.returncode == 0, r.stdout + r.stderr

    pastas = sorted(p for p in destino.iterdir() if p.is_dir())
    assert len(pastas) == 1, [p.name for p in pastas]
    pasta = pastas[0]
    manifesto = json.loads((pasta / "manifesto.json").read_text(encoding="utf-8-sig"))
    assert manifesto["banco"] == "sqlite"
    assert manifesto["migration"] == "017_busca_sem_acento"
    assert manifesto["credentials_key"] is False          # sem -IncluirSegredos, a chave não viaja
    assert (pasta / "poc.sqlite3").exists()
    # Nada de `-wal`/`-shm` na cópia: a API de backup já os incorporou, e um -wal velho ao lado de um banco novo
    # inventa corrupção onde não havia. É o erro que "copiar os três arquivos à mão" produz.
    assert not (pasta / "poc.sqlite3-wal").exists()
    assert not (pasta / "poc.sqlite3-shm").exists()
    assert (pasta / "config" / "config.yaml").exists()
    leiame = (pasta / "LEIA-ME.txt").read_text(encoding="utf-8-sig")
    assert "DPAPI" in leiame                              # a pegadinha viaja com a cópia, não só no repositório
    assert not (pasta / ".env").exists()                  # o arquivo de segredos NUNCA entra

    # --- a restauração, em pasta limpa, sem tocar em nada do projeto
    ensaio = tmp_path / "ensaio"
    r = subprocess.run(["pwsh", "-NoProfile", "-File", str(scripts / "restore.ps1"),
                        "-De", str(pasta), "-Para", str(ensaio)],
                       capture_output=True, text=True, timeout=180, cwd=str(raiz))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "integridade=ok" in r.stdout
    assert "ENSAIO" in r.stdout
    with sqlite3.connect(str(ensaio / "poc.sqlite3")) as c:
        assert c.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 500


# ---------------------------------------------------------------- teto das cópias e o ensaio (29.38)
def _copia_falsa(destino: Path, nome: str, manifesto: dict[str, object] | None) -> Path:
    pasta = destino / nome
    pasta.mkdir(parents=True)
    if manifesto is not None:
        (pasta / "manifesto.json").write_text(json.dumps(manifesto), encoding="utf-8")
    return pasta


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh não está no PATH")
def test_teto_mantem_dez_copias_de_deploy_e_nao_toca_nas_outras(tmp_path: Path) -> None:
    """161 cópias e 23 GB em `data/backups` (04/10): com dez deploys num dia, a retenção de 14 dias não segurava
    nada. O teto conta deploy, ensaio e as cópias antigas sem `origem` (a tarefa diária nunca tinha sido registrada,
    então elas vieram do deploy); a diária, a manual e a pasta com nome escolhido à mão ficam."""
    raiz = _raiz_isolada(tmp_path)
    destino = tmp_path / "backups"
    for i in range(1, 3):                                   # antigas, sem `origem`
        _copia_falsa(destino, f"20260901-00000{i}", {"ts": "2026-09-01T00:00:00Z"})
    for i in range(1, 12):                                  # 11 de deploy e de ensaio
        _copia_falsa(destino, f"20261001-0000{i:02d}", {"origem": "deploy" if i % 2 else "ensaio"})
    _copia_falsa(destino, "20260801-000000", {"origem": "diario"})
    _copia_falsa(destino, "20260802-000000", {"origem": "manual"})
    _copia_falsa(destino, "20260803-000000-antes-ra20b", None)
    banco = tmp_path / "poc.sqlite3"
    _banco_com_wal_vivo(banco).close()

    def rodar() -> str:
        r = subprocess.run(["pwsh", "-NoProfile", "-File", str(raiz / "scripts" / "backup.ps1"), "-Banco", str(banco),
                            "-Destino", str(destino), "-Reter", "0", "-Origem", "deploy", "-Teto", "10"],
                           capture_output=True, text=True, timeout=180, cwd=str(raiz))
        assert r.returncode == 0, r.stdout + r.stderr
        return r.stdout

    # 1) Sem a chave, a poda é ENSAIO: lista as 4 que apagaria (2 antigas + 11 + a nova = 14) e não apaga nada.
    saida = rodar()
    assert saida.count("apagaria (teto") == 4
    assert "NADA apagado" in saida
    ficaram = sorted(p.name for p in destino.iterdir())
    assert len(ficaram) == 2 + 11 + 3 + 1
    nova = ficaram[-1]
    assert json.loads((destino / nova / "manifesto.json").read_text(encoding="utf-8-sig"))["origem"] == "deploy"

    # 2) Com `PODAR-LIGADO` no destino (criado depois do sim do dono), apaga: 15 contadas, saem as 5 mais velhas.
    (destino / "PODAR-LIGADO").write_text("sim do dono\n", encoding="utf-8")
    time.sleep(1.1)                                         # o carimbo da pasta é por segundo
    saida = rodar()
    assert saida.count("removido (teto") == 5
    ficaram = sorted(p.name for p in destino.iterdir() if p.is_dir())
    contadas = [n for n in ficaram if n.startswith("2026100")]
    assert len(contadas) == 10
    assert contadas[:8] == [f"20261001-0000{i:02d}" for i in range(4, 12)] and nova in contadas
    assert "20260901-000001" not in ficaram and "20260901-000002" not in ficaram
    assert {"20260801-000000", "20260802-000000", "20260803-000000-antes-ra20b"} <= set(ficaram)


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh não está no PATH")
def test_pular_backup_so_vale_com_ensaio_recente_do_mesmo_commit(tmp_path: Path) -> None:
    """`deploy.ps1 -PularBackup` numa subida de verdade usa a cópia do `-Ensaio` feito logo antes, na mesma árvore.
    Ensaio de outro commit (o deploy pode trazer migração que ele não ensaiou), ensaio velho e cópia de deploy não
    valem."""
    from datetime import datetime, timedelta, timezone

    agora = datetime.now(timezone.utc)
    destino = tmp_path / "backups"
    _copia_falsa(destino, "20261004-090000", {"origem": "ensaio", "commit": "abc", "ts": agora.isoformat()})
    _copia_falsa(destino, "20261004-080000", {"origem": "ensaio", "commit": "velho",
                                              "ts": (agora - timedelta(hours=2)).isoformat()})
    _copia_falsa(destino, "20261004-091000", {"origem": "deploy", "commit": "dep", "ts": agora.isoformat()})
    lib = SCRIPTS / "lib" / "copias-de-backup.ps1"

    def achar(commit: str, minutos: int) -> str:
        cmd = (f". '{lib}'; $p = Find-EnsaioRecente '{destino}' '{commit}' {minutos}; "
               "if ($p) { $p.Name } else { 'nenhum' }")
        r = subprocess.run(["pwsh", "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr
        return r.stdout.strip()

    assert achar("abc", 60) == "20261004-090000"
    assert achar("outro", 60) == "nenhum"           # ensaio de outro commit
    assert achar("velho", 60) == "nenhum"           # ensaio de duas horas atrás
    assert achar("velho", 180) == "20261004-080000"
    assert achar("dep", 60) == "nenhum"             # cópia de deploy não é ensaio
    assert achar("", 60) == "nenhum"                # árvore sem commit conhecido não pula nada


def test_deploy_recusa_pular_backup_antes_de_parar_qualquer_coisa() -> None:
    """A recusa do `-PularBackup` sem ensaio vem antes do `stop.ps1`: lido no texto do script, porque rodar o
    deploy de verdade num teste pararia o ambiente central."""
    texto = (SCRIPTS / "deploy.ps1").read_text(encoding="utf-8")
    recusa = texto.index("Find-EnsaioRecente")
    assert recusa < texto.index("'stop.ps1'")
    assert "-Origem $origem -Teto $tetoDeCopias" in texto
    assert "$tetoDeCopias = 10" in texto


# ---------------------------------------------------------------- o retrato do que está no ar
async def test_health_diz_qual_commit_e_qual_migracao_estao_no_ar(harness) -> None:  # type: ignore[no-untyped-def]
    """Sem isto não havia como responder a pergunta que o deploy faz: *este processo é o código novo?*
    `version` é a constante "0.1.0" no código e não muda nunca. O parque rodou um dia inteiro um backend anterior
    às migrações 016/017 e o `/api/health` dizia exatamente o mesmo que diria depois da subida.
    """
    import httpx

    from app.main import create_app

    app = create_app(harness.cfg, state=harness.state)
    app.state.poc = harness.state
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 123)),
                                 base_url="http://127.0.0.1") as c:
        saude = (await c.get("/api/health")).json()

    ultima = sorted(p.stem for p in (RAIZ / "backend" / "migrations").glob("*.sql"))[-1]
    assert saude["migration"] == ultima, "o banco da suíte aplica todas as migrações; o campo tem de mostrar isso"
    assert saude["version"] == "0.1.0"        # a constante continua lá, agora ao lado do que de fato identifica
    # O harness roda com a raiz numa pasta temporária, sem `.git`: ali o campo é `None`, e dizer "não sei" é o
    # comportamento certo — melhor que inventar um commit.
    assert "commit" in saude


@pytest.mark.skipif(not (RAIZ / ".git").exists(), reason="árvore sem .git (instalação por cópia)")
def test_commit_em_execucao_sai_do_git_sem_chamar_git() -> None:
    """Sem subprocesso de propósito: `git` pode não estar no PATH da conta que roda o serviço, e um `/api/health`
    que falha por causa disso troca uma resposta útil por um erro."""
    from app.state import commit_em_execucao

    lido = commit_em_execucao(RAIZ)
    esperado = subprocess.run(["git", "-C", str(RAIZ), "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=30).stdout.strip()
    assert lido == esperado and len(lido or "") == 40
