"""Achado #177: instalação nova não pode nascer `degraded` com aparelho fantasma.

`config/config.yaml` era o retrato do parque de PRODUÇÃO e estava versionado: quem clonava o repositório subia
o backend com 15 cartões — 8 locais sem AVD, uma loja pedindo uma imagem que o instalador padrão não baixa e 6
remotos apontando para portas de túnel que não existem naquela máquina —, saúde `degraded` de saída e o monitor
tentando `adb connect` em seis portas mortas para sempre.

Agora o repositório versiona só `config/config.example.yaml`, e estes testes guardam as três coisas que fazem
dele um ponto de partida: ele carrega, ele é NEUTRO (nada de `external`, `store` ou imagem de override — as
três únicas fontes de `system_image_missing` e de "aparelho externo não conectado"), e ele é o que o backend lê
quando o arquivo do dono ainda não existe.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.config import AppConfigFile, EnvSettings, config_file_path, load_config

RAIZ = Path(__file__).resolve().parents[2]
EXEMPLO = RAIZ / "config" / "config.example.yaml"


def test_o_exemplo_existe_e_carrega_sem_reclamar() -> None:
    assert EXEMPLO.exists(), "config/config.example.yaml é o ponto de partida versionado"
    cfg = load_config(EXEMPLO)
    assert cfg.file.instances.count >= 1


def test_o_exemplo_nao_traz_aparelho_fantasma_nenhum() -> None:
    cfg = load_config(EXEMPLO)
    inst = cfg.file.instances
    # `external` vazio: nenhum endereço de túnel de outra máquina para o monitor perseguir a cada 30 s.
    assert inst.external == {}
    # sem loja: a imagem `google_apis_playstore` não é a que `install-prereqs.ps1` baixa por padrão.
    assert inst.store is None
    # nenhuma imagem de override ausente => nenhum `system_image_missing` na saúde.
    assert cfg.override_images() == {}
    # todo aparelho declarado tem rótulo de conta, e nenhum rótulo sobra apontando para id inexistente.
    ids = {f"{inst.id_prefix}{i:02d}" for i in range(1, inst.count + 1)}
    assert set(inst.accounts) == ids


def test_o_exemplo_sobe_em_loopback_e_sem_canal_de_worker() -> None:
    """Uma máquina só: sem o listener do túnel aberto e sem sair do loopback (que exigiria token e TLS)."""
    cfg = load_config(EXEMPLO)
    assert cfg.file.server.host == "127.0.0.1"
    assert cfg.file.server.worker_port == 0


def test_o_exemplo_e_o_config_real_falam_o_mesmo_idioma() -> None:
    """O exemplo não pode envelhecer virando outro formato: as seções são as mesmas do modelo validado."""
    bruto = yaml.safe_load(EXEMPLO.read_text(encoding="utf-8"))
    assert set(bruto) <= set(AppConfigFile.model_fields), set(bruto) - set(AppConfigFile.model_fields)
    assert {"server", "paths", "android", "instances", "limits", "ai", "apps"} <= set(bruto)


def test_sem_o_arquivo_do_dono_o_backend_le_o_exemplo_em_vez_dos_padroes_do_codigo(tmp_path: Path) -> None:
    alvo = tmp_path / "config.yaml"
    exemplo = tmp_path / "config.example.yaml"
    exemplo.write_text("instances:\n  count: 2\n", encoding="utf-8")
    assert config_file_path(alvo) == exemplo
    assert load_config(alvo).file.instances.count == 2


def test_o_arquivo_do_dono_ganha_do_exemplo(tmp_path: Path) -> None:
    alvo = tmp_path / "config.yaml"
    alvo.write_text("instances:\n  count: 7\n", encoding="utf-8")
    (tmp_path / "config.example.yaml").write_text("instances:\n  count: 2\n", encoding="utf-8")
    assert config_file_path(alvo) == alvo
    assert load_config(alvo).file.instances.count == 7


def test_sem_arquivo_nenhum_nao_estoura(tmp_path: Path) -> None:
    assert config_file_path(tmp_path / "config.yaml") is None
    assert load_config(tmp_path / "config.yaml").file.instances.count >= 1


def test_poc_config_continua_mandando_quando_ninguem_passa_caminho(tmp_path: Path) -> None:
    escolhido = tmp_path / "outro.yaml"
    escolhido.write_text("instances:\n  count: 5\n", encoding="utf-8")
    assert config_file_path(None, EnvSettings(POC_CONFIG=str(escolhido))) == escolhido


@pytest.mark.parametrize("trecho", ["config/config.yaml"])
def test_o_config_do_dono_esta_fora_do_versionamento(trecho: str) -> None:
    assert trecho in (RAIZ / ".gitignore").read_text(encoding="utf-8")


def test_a_partida_copia_o_exemplo_e_nunca_sobrescreve_o_que_ja_existe() -> None:
    """`start.ps1` é lido, nunca executado por teste: ele sobe backend, Appium e emuladores."""
    texto = (RAIZ / "scripts" / "start.ps1").read_text(encoding="utf-8")
    assert "config\\config.example.yaml" in texto
    # A cópia mora numa função (`Semear`) que devolve antes de qualquer coisa quando o arquivo existe: é isso
    # que garante "nunca por cima do arquivo do dono", e é isso que se afirma — não a forma do `if` antigo.
    assert "if (Test-Path $arquivo) { return }" in texto, "copiar só quando não existe — nunca por cima do arquivo do dono"
    assert "Copy-Item $exemplo $arquivo" in texto
    # E a segunda regra, nascida de um incidente: com banco (ou chave) presente e config ausente, a partida PARA
    # em vez de recriar do exemplo — recriar subiria o backend com outra configuração sem nada reclamar.
    assert "(Test-Path $banco) -or (Test-Path $chave)" in texto
    corpo = texto.split("function Semear")[1].split("Copy-Item $exemplo")[0]
    assert "throw (" in corpo, "o guard precisa parar, não avisar"


# ------------------------------------------------------------------ o bloco `portal` (29.77, 29.83)
def test_o_teto_de_buscas_da_exclusao_vem_do_yaml_pelo_nome_exato(tmp_path: Path) -> None:
    """`portal.limites.buscas_por_operador_hora` e `buscas_total_hora` são lidos pelo `load_config` com esses nomes, e
    o exemplo traz os padrões."""
    limites = load_config(EXEMPLO).file.portal.limites
    assert (limites.buscas_por_operador_hora, limites.buscas_total_hora) == (30, 60)
    alvo = tmp_path / "config.yaml"
    alvo.write_text("portal:\n  limites:\n    buscas_por_operador_hora: 12\n    buscas_total_hora: 40\n",
                    encoding="utf-8")
    limites = load_config(alvo).file.portal.limites
    assert (limites.buscas_por_operador_hora, limites.buscas_total_hora) == (12, 40)


@pytest.mark.parametrize("bloco", [
    "portal:\n  contatos:\n    - nome: Fulano\n      telefon: '+55 10 90000-0001'\n",     # chave errada
    "portal:\n  contatos:\n    - nome: Fulano\n      telefone: 'ramal 90000-0001'\n",     # valor recusado
])
def test_erro_do_bloco_portal_nomeia_a_chave_e_nao_ecoa_o_valor(tmp_path: Path, bloco: str) -> None:
    """O erro vai ao console do deploy e ao log da subida: diz o caminho e a chave, nunca o telefone (revisão E5)."""
    alvo = tmp_path / "config.yaml"
    alvo.write_text(bloco, encoding="utf-8")
    with pytest.raises(Exception) as erro:
        load_config(alvo)
    texto = str(erro.value)
    assert "portal.contatos.0.telefon" in texto
    assert "90000" not in texto and "0001" not in texto and "input_value" not in texto


@pytest.mark.parametrize("bloco", [
    "portal:\n  limites:\n    buscas_por_operador_hor: 12\n",          # o nome do teto com erro de digitação
    "portal:\n  site_ligad: true\n",                                     # a bandeira com erro
    "portal:\n  contatos:\n    - nome: Fulano\n      telefon: '+55 10 90000-0001'\n",
])
def test_chave_errada_no_bloco_portal_nao_passa_calada(tmp_path: Path, bloco: str) -> None:
    alvo = tmp_path / "config.yaml"
    alvo.write_text(bloco, encoding="utf-8")
    errada = bloco.split(":")[-2].split()[-1].lstrip("- ")
    with pytest.raises(Exception, match=errada):
        load_config(alvo)
