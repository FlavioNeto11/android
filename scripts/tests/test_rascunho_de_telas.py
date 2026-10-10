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


def test_nome_de_pessoa_na_tela_avisa_no_cabecalho_e_senha_em_view_customizada_nao_vira_sinal(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    casa = tmp_path / "casa.xml"
    casa.write_text('<hierarchy rotation="0">' + _no("TextView", "Ana Correio", y=0)
                    + _no("View", "SENHA-EM-VIEW-CUSTOMIZADA", "campo_senha_custom", senha=True, y=100)
                    + "</hierarchy>", encoding="utf-8")
    assert _modulo().main(["--app", PACOTE, str(casa)]) == 0
    saida = capsys.readouterr().out
    assert "NOME da conta" in saida                                          # o aviso de revisão vem no topo do arquivo
    assert "SENHA-EM-VIEW-CUSTOMIZADA" not in saida


def test_saida_igual_a_uma_entrada_e_recusada(xmls: list[Path], capsys: pytest.CaptureFixture[str]) -> None:
    antes = xmls[0].read_text(encoding="utf-8")
    assert _modulo().main(["--app", PACOTE, "--saida", str(xmls[0]), *map(str, xmls)]) == 2
    assert xmls[0].read_text(encoding="utf-8") == antes and "recusado" in capsys.readouterr().err


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


# ---- higiene 12.3 (revisão da Aprendizado): a identidade de quem percorreu o app nunca vira sinal
def _casa(tmp_path: Path) -> Path:
    casa = tmp_path / "casa.xml"
    casa.write_text('<hierarchy rotation="0">' + _no("TextView", "Ana Correio", "toolbar_nome_conta", y=0)
                    + _no("TextView", "Olá, Ana Correio", y=60)
                    + _no("TextView", "Caixa de entrada", y=120)
                    + _no("TextView", "Maria Souza", "campo_nome_exibicao", y=180)
                    + _no("TextView", "Escrever", "botao_escrever", y=240) + "</hierarchy>", encoding="utf-8")
    return casa


def _vira_sinal(dados: dict, texto: str) -> bool:
    """Algum sinal do rascunho casa com este texto (já sem acento nem caixa, como o carregador de telas compara)?"""
    return any(re.search(v, texto) for v in dados["sinais"]["pt"].values())


def test_o_nome_de_exibicao_dado_em_ignorar_nunca_vira_sinal(tmp_path: Path) -> None:
    dados = _modulo().rascunhar(PACOTE, [_casa(tmp_path)], ("Ana Correio",))
    assert not _vira_sinal(dados, "ana correio") and not _vira_sinal(dados, "ola, ana correio")
    assert _vira_sinal(dados, "caixa de entrada")


def test_sem_ignorar_a_saudacao_com_o_nome_ainda_vira_sinal_e_o_cabecalho_avisa(tmp_path: Path,
                                                                                 capsys: pytest.CaptureFixture[str]) -> None:
    """Sem `--ignorar` a ferramenta não adivinha o nome: o aviso do cabeçalho segue valendo (por isso o `--ignorar` existe)."""
    assert _modulo().main(["--app", PACOTE, str(_casa(tmp_path))]) == 0
    assert "NOME da conta" in capsys.readouterr().out


def test_o_ignorar_ignora_acento_e_caixa(tmp_path: Path) -> None:
    casa = tmp_path / "casa.xml"
    casa.write_text('<hierarchy rotation="0">' + _no("TextView", "JOÃO PÉREZ", y=0) + _no("TextView", "Caixa de entrada", y=60)
                    + "</hierarchy>", encoding="utf-8")
    dados = _modulo().rascunhar(PACOTE, [casa], ("joao perez",))
    assert not _vira_sinal(dados, "joao perez") and _vira_sinal(dados, "caixa de entrada")


def test_elemento_de_identidade_pelo_resource_id_sai_sem_precisar_do_ignorar(tmp_path: Path) -> None:
    """O nome na barra de ferramentas e o campo de nome de exibição são identidade pelo `resource-id`, mesmo sem `--ignorar`."""
    dados = _modulo().rascunhar(PACOTE, [_casa(tmp_path)])
    assert not _vira_sinal(dados, "maria souza") and not _vira_sinal(dados, "ana correio")
    assert _vira_sinal(dados, "caixa de entrada")


def test_ignorar_curto_demais_e_recusado(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _modulo().main(["--app", PACOTE, "--ignorar", "an", str(_casa(tmp_path))]) == 2
    assert "pelo menos 3 letras" in capsys.readouterr().err


def test_senha_nunca_vira_sinal_mesmo_com_texto_que_parece_estavel(tmp_path: Path) -> None:
    casa = tmp_path / "login.xml"
    casa.write_text('<hierarchy rotation="0">' + _no("EditText", "Senha Segura Aqui", "campo_senha", senha=True, y=0)
                    + _no("View", "Outra Senha Longa", "campo_custom", senha=True, y=60)
                    + _no("TextView", "Entre na sua conta", y=120) + "</hierarchy>", encoding="utf-8")
    dados = _modulo().rascunhar(PACOTE, [casa])
    assert not _vira_sinal(dados, "senha segura aqui") and not _vira_sinal(dados, "outra senha longa")
    assert _vira_sinal(dados, "entre na sua conta")


def test_saida_igual_a_entrada_e_recusada_mesmo_escrita_de_outro_jeito(xmls: list[Path],
                                                                       capsys: pytest.CaptureFixture[str]) -> None:
    """`pasta/sub/../arquivo.xml` é o mesmo arquivo que a entrada: a comparação é pelo caminho resolvido."""
    antes = xmls[1].read_text(encoding="utf-8")
    (xmls[1].parent / "sub").mkdir()
    outro_jeito = xmls[1].parent / "sub" / ".." / xmls[1].name
    assert _modulo().main(["--app", PACOTE, "--saida", str(outro_jeito), *map(str, xmls)]) == 2
    assert xmls[1].read_text(encoding="utf-8") == antes and "recusado" in capsys.readouterr().err
