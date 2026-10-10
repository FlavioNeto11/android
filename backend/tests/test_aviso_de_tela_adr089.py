"""31.290 (ADR-089): o aviso do painel sobre o que sai da máquina deixou de prometer o que o código não cumpre.

Antes a frase era "Telas sensíveis nunca são enviadas: campo de senha, desafio, telas declaradas e todas as do
aparelho-loja". O ADR-089 (10/10/2026) revogou o contrato C4: a plataforma não esconde tela de ninguém. Um aviso que
descreve outra coisa que não o código é pior do que aviso nenhum. A frase "nunca são recortadas" da leitura visual
(`/api/ai`) continua verdadeira e aqui fica travada: a barreira está em `saidas.ler_valor_visual`.

Prova `simulated`: provedores sem rede; nenhuma chamada de IA.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from pydantic import SecretStr

from app.config import ModelCaps, ProviderCfg, RoleCfg
from app.planning.anthropic_provider import AnthropicProvider
from app.planning.openai_provider import OpenAICompatProvider
from app.planning.provider import AVISO_TELA_SENSIVEL
from app.taskqueue import saidas

from .conftest import make_config

PROMESSAS_REVOGADAS = ("nunca são enviadas", "nunca sao enviadas", "todas as do aparelho-loja")


def _sem_promessa_revogada(texto: str) -> None:
    baixo = texto.lower()
    for frase in PROMESSAS_REVOGADAS:
        assert frase not in baixo, frase


def test_a_frase_unica_diz_o_que_o_adr_089_decidiu() -> None:
    _sem_promessa_revogada(AVISO_TELA_SENSIVEL)
    assert "ADR-089" in AVISO_TELA_SENSIVEL and "não esconde tela" in AVISO_TELA_SENSIVEL
    # 31.323: a decisão de rotina também leva a imagem da tela sensível (o aviso não diz mais "sem a imagem")
    assert "lista de elementos" not in AVISO_TELA_SENSIVEL and "decisão de rotina" in AVISO_TELA_SENSIVEL
    # e o que continua garantido: o valor da credencial não vai no texto ao modelo
    assert "credencial" in AVISO_TELA_SENSIVEL and "cofre" in AVISO_TELA_SENSIVEL


def test_o_aviso_da_anthropic_leva_a_frase_nova(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    cfg.env.anthropic_api_key = SecretStr("valor-de-teste-sem-chave-real")   # só liga `configured`; nunca há rede
    st = AnthropicProvider(cfg).status()
    assert st.configured and AVISO_TELA_SENSIVEL in st.notice
    _sem_promessa_revogada(st.notice)


async def test_o_aviso_do_provedor_compativel_com_openai_leva_a_frase_nova(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    cfg.file.ai.models["qwen-vl"] = ModelCaps(vision=True, tools=True, strict_tools=False,
                                              structured_output="json_schema", thinking=False, effort=False)
    cfg.file.ai.providers["local"] = ProviderCfg(kind="openai", base_url="http://127.0.0.1:8001/v1",
                                                 sends_data_externally=False)
    cfg.file.ai.roles["decide"] = RoleCfg(provider="local", model="qwen-vl")
    p = OpenAICompatProvider(cfg, role=cfg.ai_role("decide"))
    st = p.status()
    assert AVISO_TELA_SENSIVEL in st.notice
    _sem_promessa_revogada(st.notice)
    await p.aclose()


@pytest.mark.parametrize("onde", ["app/planning/routing.py", "app/planning/anthropic_provider.py",
                                  "app/planning/openai_provider.py"])
def test_nenhum_texto_de_aviso_do_provedor_repete_a_promessa_antiga(onde: str) -> None:
    fonte = (Path(__file__).resolve().parents[1] / onde).read_text(encoding="utf-8").lower()
    _sem_promessa_revogada(fonte)


def test_a_leitura_visual_continua_sem_recortar_tela_sensivel() -> None:
    """A frase de `/api/ai` "telas sensíveis e de verificação nunca são recortadas" é verdadeira: a barreira recusa a
    árvore sensível antes de ler a imagem. Se ela sair, o texto de `routing.py` precisa sair junto."""
    fonte = inspect.getsource(saidas.ler_valor_visual)
    assert "arvore.sensitive" in fonte and "arvore2.sensitive" in fonte
