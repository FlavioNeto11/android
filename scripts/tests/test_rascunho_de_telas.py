"""Item 12.3: `scripts/rascunho-de-telas.py` esboça um `telas.yaml` a partir de hierarquias XML, só leitura, e nunca grava em
`app/conhecimento/apps`. Nível de prova: `simulated` (XML escrito aqui, nenhum aparelho)."""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest
import yaml

SCRIPT = Path(__file__).resolve().parents[1] / "rascunho-de-telas.py"
PACOTE = "com.exemplo.email"


def _modulo() -> ModuleType:
    spec = importlib.util.spec_from_file_location("rascunho_de_telas", SCRIPT)
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _no(cls: str, texto: str = "", rid: str = "", *, senha: bool = False, y: int = 0) -> str:
    return (f'<node class="android.widget.{cls}" package="{PACOTE}" text="{texto}" resource-id="{PACOTE}:id/{rid}" '
            f'content-desc="" clickable="true" enabled="true" focused="false" password="{str(senha).lower()}" '
            f'scrollable="false" bounds="[40,{y}][680,{y + 60}]" />')


@pytest.fixture
def xmls(tmp_path: Path) -> list[Path]:
    entrada = tmp_path / "Tela de Entrada.xml"
    entrada.write_text('<hierarchy rotation="0">' + _no("TextView", "Bem-vindo ao Correio", y=0)
                       + _no("EditText", "ana.correio", "campo_usuario", y=100)
                       + _no("EditText", "SENHA-QUE-NAO-PODE-VAZAR", "campo_senha", senha=True, y=200)
                       + _no("Button", "Entrar", "botao_entrar", y=300) + _no("TextView", "12 mensagens novas", y=400)
                       + "</hierarchy>", encoding="utf-8")
    caixa = tmp_path / "caixa.xml"
    caixa.write_text('<hierarchy rotation="0">' + _no("RecyclerView", "", "message_list", y=0)
                     + _no("TextView", "Caixa de entrada", y=100) + "</hierarchy>", encoding="utf-8")
    return [entrada, caixa]


def test_o_rascunho_detecta_login_ids_e_sinais_estaveis(xmls: list[Path]) -> None:
    dados = _modulo().rascunhar(PACOTE, xmls)
    entrada, caixa = dados["telas"]
    assert entrada["tela"] == "tela_de_entrada" and entrada["tipo"] == "login" and entrada["formulario_de_senha"] is True
    assert "campo_senha" in entrada["ids"] and caixa["ids"] == ["message_list"]
    sinais = dados["sinais"]["pt"]
    assert any(re.search(v, "bem-vindo ao correio") for v in sinais.values())
    assert any(re.search(v, "caixa de entrada") for v in sinais.values())
    assert not any("12" in v for v in sinais.values())                      # texto com número muda a cada abertura
    texto = yaml.safe_dump(dados, allow_unicode=True)
    assert "SENHA-QUE-NAO-PODE-VAZAR" not in texto and "ana.correio" not in texto      # texto de campo editável nunca entra
    assert caixa["tipo"] == "desconhecida_a_classificar" and "RASCUNHO" in caixa["razao"]


def test_o_rascunho_pede_o_que_falta_e_carrega_depois_de_editado(xmls: list[Path], capsys: pytest.CaptureFixture[str]) -> None:
    m = _modulo()
    assert m.main(["--app", PACOTE, *map(str, xmls)]) == 0
    saida = capsys.readouterr()
    assert "RASCUNHO" in saida.out and "defina o `tipo`" in saida.err and "estado_conhecido" in saida.err
    dados = yaml.safe_load(saida.out)
    dados["telas"][1].update(tipo="autenticada", autenticada=True)
    dados["estado_conhecido"]["telas"] = [dados["telas"][1]["tela"]]
    assert m._conferir(dados) == []                                          # o carregador aceita o esboço editado


def test_nunca_grava_dentro_de_app_conhecimento_apps(xmls: list[Path], capsys: pytest.CaptureFixture[str]) -> None:
    m = _modulo()
    destino = m.CONHECIMENTO_DE_APPS / PACOTE / "telas.yaml"
    assert m.main(["--app", PACOTE, "--saida", str(destino), *map(str, xmls)]) == 2
    assert not destino.exists() and "recusado" in capsys.readouterr().err


def test_grava_fora_da_pasta_dos_apps(xmls: list[Path], tmp_path: Path) -> None:
    saida = tmp_path / "rascunho.yaml"
    assert _modulo().main(["--app", PACOTE, "--saida", str(saida), *map(str, xmls)]) == 0
    assert yaml.safe_load(saida.read_text(encoding="utf-8"))["app"] == PACOTE


def test_dois_arquivos_com_o_mesmo_nome_de_tela_sao_recusados(tmp_path: Path, xmls: list[Path]) -> None:
    copia = tmp_path / "Tela-de-Entrada.xml"
    copia.write_text(xmls[0].read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(SystemExit):
        _modulo().rascunhar(PACOTE, [xmls[0], copia])
