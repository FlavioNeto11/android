"""Testes puros do mapeador de testes afetados (scripts/testes-afetados.py), sobre uma árvore mínima em tmp."""
import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location("testes_afetados", Path(__file__).resolve().parents[1] / "testes-afetados.py")
ta = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ta)


def _arvore(tmp: Path) -> None:
    (tmp / "backend/app/devices").mkdir(parents=True)
    (tmp / "backend/tests").mkdir(parents=True)
    (tmp / "backend/app/__init__.py").write_text("")
    (tmp / "backend/app/devices/__init__.py").write_text("")
    (tmp / "backend/app/devices/rede.py").write_text("X = 1\n")
    (tmp / "backend/app/devices/sonda_rede.py").write_text("from app.devices import rede\n")
    (tmp / "backend/app/devices/outro.py").write_text("Y = 2\n")
    (tmp / "backend/app/main.py").write_text("from app.devices import sonda_rede, outro\n")
    (tmp / "backend/tests/test_rede_sonda.py").write_text("from app.devices.sonda_rede import X\n")
    (tmp / "backend/tests/test_outro_assunto.py").write_text("from app.devices import outro\n")
    (tmp / "backend/tests/test_integracao.py").write_text("from app.main import app\n")
    (tmp / "backend/tests/test_arquitetura.py").write_text("")
    (tmp / "backend/tests/test_pacote_do_agente.py").write_text("")


def test_importador_indireto_entra_e_o_hub_nao_puxa_tudo(tmp_path):
    _arvore(tmp_path)
    r = ta.afetados(tmp_path, ["backend/app/devices/rede.py"])
    nomes = {Path(t).name for t in r["testes"]}
    assert "test_rede_sonda.py" in nomes  # sonda_rede importa rede; o teste importa sonda_rede
    assert "test_outro_assunto.py" not in nomes  # sem relação
    assert {"test_arquitetura.py", "test_pacote_do_agente.py"} <= nomes  # guardas sempre
    assert "test_integracao.py" not in nomes  # só importa o hub app.main


def test_teste_que_mudou_e_frontend_e_docs(tmp_path):
    _arvore(tmp_path)
    r = ta.afetados(tmp_path, ["backend/tests/test_outro_assunto.py", "frontend/src/a.tsx", "docs/x.md"])
    assert "backend/tests/test_outro_assunto.py" in r["testes"]
    assert r["sugestoes"] == ["cd frontend && npx vitest related frontend/src/a.tsx --run"]


def test_arquivo_sem_relacao_so_traz_as_guardas(tmp_path):
    _arvore(tmp_path)
    r = ta.afetados(tmp_path, ["CHANGELOG.md"])
    assert {Path(t).name for t in r["testes"]} == {"test_arquitetura.py", "test_pacote_do_agente.py"}
    assert not r["amplo"]


def test_ps1_de_scripts_traz_o_teste_do_portao_e_da_varredura(tmp_path):
    """29.94: o teste dos `.ps1` (portão do -PularBackup e varredura de colisão com parâmetro) só existe em
    `scripts/tests`; sem este mapa, mexer no `deploy.ps1` não o traria na validação dirigida."""
    _arvore(tmp_path)
    (tmp_path / "scripts" / "tests").mkdir(parents=True)
    (tmp_path / "scripts" / "tests" / "test_deploy_portao_do_ensaio.py").write_text("")
    for arq in ("scripts/deploy.ps1", "scripts/backup.ps1", "scripts/lib/copias-de-backup.ps1"):
        r = ta.afetados(tmp_path, [arq])
        assert "scripts/tests/test_deploy_portao_do_ensaio.py" in r["testes"], arq
