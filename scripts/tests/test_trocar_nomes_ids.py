"""Testes da chave `ids` de scripts/trocar-nomes-nos-testes.py (31.105), com uma tabela INVENTADA num diretório
temporário. Nenhum dado do parque é lido: a tabela de verdade fica fora do Git e não entra aqui."""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "trocar-nomes-nos-testes.py"
_spec = importlib.util.spec_from_file_location("trocar_nomes_nos_testes", SCRIPT)
troca = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(troca)

ID_VELHO, ID_NOVO = "ig-0a1b2c3d4e5f6a7b", "ig-9f8e7d6c5b4a3f2e"
TABELA = {"handles": {"quimera.zarolha4821": "girafa.amostral7310"}, "pedacos": {"quimera": "girafa"},
          "fora": [], "simulados": [], "ids": {ID_VELHO: ID_NOVO}}


def test_id_troca_literal_sem_amplo_e_com_a_caixa_exata() -> None:
    trocar = troca.trocador(TABELA, amplo=False)
    assert trocar(f"rota /personas/{ID_VELHO}/editar") == f"rota /personas/{ID_NOVO}/editar"
    # Literal: outra caixa não é o mesmo id (valor opaco), e o id colado a um sufixo se troca do mesmo jeito.
    assert trocar(ID_VELHO.upper()) == ID_VELHO.upper()
    assert trocar(f"{ID_VELHO}-2") == f"{ID_NOVO}-2"


def test_tabela_sem_ids_segue_valendo() -> None:
    sem = {k: v for k, v in TABELA.items() if k != "ids"}
    assert troca.trocador(sem, amplo=False)(ID_VELHO) == ID_VELHO


def _repo(raiz: Path, texto: str) -> None:
    arquivo = raiz / "backend" / "tests" / "test_x.py"
    arquivo.parent.mkdir(parents=True)
    arquivo.write_text(texto, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(raiz)], check=True)
    subprocess.run(["git", "-C", str(raiz), "add", "-A"], check=True)


def test_amplo_nao_aborta_pelos_trechos_do_id_de_exemplo(tmp_path: Path, monkeypatch, capsys) -> None:
    """Os trechos do id de exemplo NÃO entram nos `novos`: se entrassem, um trecho que já aparece nos testes
    faria o `--amplo` abortar sem nenhum nome em jogo."""
    raiz, privado = tmp_path / "repo", tmp_path / "privado"
    privado.mkdir()
    # Picado como um handle, o id de exemplo daria trechos como "ig-" e letras soltas, que já existem nos testes:
    # em qualquer outro id do mesmo formato e em toda frase em português ("e", "a").
    _repo(raiz, f'ALVO = "{ID_VELHO}"\nFRASE = "abre e fecha a tela"\nNOME = "Quimera"\n')
    tabela = privado / "tabela.json"
    tabela.write_text(json.dumps(TABELA), encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["x", "--tabela", str(tabela), "--raiz", str(raiz), "--amplo", "--aplicar"])
    assert troca.main() == 0
    texto = (raiz / "backend" / "tests" / "test_x.py").read_text(encoding="utf-8")
    assert ID_VELHO not in texto and f'ALVO = "{ID_NOVO}"' in texto and 'NOME = "Girafa"' in texto
    assert "arquivos: 1" in capsys.readouterr().out


def test_tabela_dentro_do_repositorio_continua_barrada(tmp_path: Path, monkeypatch) -> None:
    raiz = tmp_path / "repo"
    _repo(raiz, "X = 1\n")
    tabela = raiz / "tabela.json"
    tabela.write_text(json.dumps(TABELA), encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["x", "--tabela", str(tabela), "--raiz", str(raiz)])
    with pytest.raises(SystemExit, match="não pode ficar dentro"):
        troca.main()
