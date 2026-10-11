"""31.328: o guarda do ADR-009/058 não para a leitura da linha de uma caixa de e-mail por causa do que a MENSAGEM diz.

Prova real do P-043 (10/10/2026, Outlook no `android-01`): o pedido misto abriu a caixa e pediu o remetente da 1ª mensagem; a 1ª
mensagem era a do Instagram ("Verify your account…", prévia "Confirm you're human to use your account…"). A triagem do recorte
leu a frase de verificação humana, que é TEXTO DO E-MAIL, e parou a etapa em `waiting_user` ("formato de verificação da conta").

A região visual do app agora pode declarar `conteudo_de_terceiros: true` (a linha de uma caixa é conteúdo de quem escreveu a
mensagem, não a tela do app): nela a frase de verificação humana deixa de ser desafio. Código, senha, token e o pedido de código
seguem recusados, e o padrão (região sem a marca, o resto dos apps) não muda.

Nível de prova: `simulated` (árvore, captura e leitor falsos; nenhuma IA, nenhum aparelho). `real`: `not_run`.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.automation.conhecimento_de_telas import ConhecimentoInvalido, carregar, de_dados
from app.planning.provider import Transcricao
from app.taskqueue.saidas import LeituraVisualRecusada, _triagem_do_recorte, ler_valor_visual, triagem

from .fake_device import PKG as QA
from .test_leitura_visual import LISTA, _arvore, _jpeg, _Leitor

ASSUNTO_INSTAGRAM = "Verify your account to keep using it"
PREVIA = "Confirm you're human to use your account"
LINHAS = ["Instagram", ASSUNTO_INSTAGRAM, PREVIA]


def _conhecimento(terceiros: bool | None) -> Any:
    regiao: dict[str, Any] = {"tela": "caixa_de_entrada", "dentro_de": [LISTA], "saidas": ["remetente", "assunto"]}
    if terceiros is not None:
        regiao["conteudo_de_terceiros"] = terceiros
    return de_dados({
        "app": QA, "versao": 1, "idioma_padrao": "en", "sinais": {"en": {"nunca": "nunca_casa_xyz"}},
        "telas": [{"tela": "caixa_de_entrada", "tipo": "autenticada", "autenticada": True, "ids": ["conversation_list"]}],
        "estado_conhecido": {"telas": ["caixa_de_entrada"]},
        "leitura_visual": {"regioes": [regiao]}})


async def _ler(terceiros: bool | None, linhas: list[str], valor: str, nome: str = "remetente") -> Any:
    arvore = _arvore()

    async def obter() -> Any:
        return arvore, _jpeg(), 720, 1280

    leitor = _Leitor(Transcricao(linhas=linhas, campos={"remetente": linhas[0], "assunto": linhas[1]}))
    return await ler_valor_visual(
        habilitado=True, arvore=arvore, element_id="e2", nome=nome, valor_do_ator=valor,
        conhecimento=_conhecimento(terceiros), tela="caixa_de_entrada", image_policy="auto", fora_do_app=None,
        largura=720, altura=1280, obter_imagem=obter, tentativas=set(), transcrever=leitor)


# ------------------------------------------------------------------ a triagem pura
def test_sem_a_marca_a_frase_de_verificacao_humana_continua_recusada() -> None:
    assert _triagem_do_recorte(LINHAS, " ".join(LINHAS)) == "verificação da conta"
    assert triagem(ASSUNTO_INSTAGRAM) == "verificação da conta"


def test_com_a_marca_a_frase_de_verificacao_humana_e_texto_do_email_e_passa() -> None:
    assert _triagem_do_recorte(LINHAS, " ".join(LINHAS), conteudo_de_terceiros=True) is None
    assert triagem(ASSUNTO_INSTAGRAM, conteudo_de_terceiros=True) is None


@pytest.mark.parametrize("linha", [
    "123456 is your Instagram code",                       # número + palavra de código
    "Your code is ABC123",                                  # código alfanumérico curto
    "482913",                                                # só a forma de código
    "Use this link https://example.com/r?t=AbCdEfGhIjKlMnOpQrStUvWxYz012345 to sign in",   # link com segredo
    "Enter the code we sent to your email",                  # o pedido de CÓDIGO vale sempre
    "Confirm you're human, then enter the code we sent",     # revisão do 31.328 (A): a conta travada vinha primeiro e o escondia
    "Verify you're human: 482913",                            # revisão do 31.328 (B): número junto da frase de desafio
    "Confirm it's you - 482913",
])
def test_codigo_senha_token_e_pedido_de_codigo_seguem_recusados_na_caixa(linha: str) -> None:
    linhas = ["Instagram", linha, PREVIA]
    assert _triagem_do_recorte(linhas, " ".join(linhas), conteudo_de_terceiros=True) is not None, linha


def test_o_pedido_de_codigo_partido_em_duas_linhas_com_a_frase_de_desafio_tambem_recusa() -> None:
    linhas = ["Instagram", "Enter the", "code we sent", PREVIA]                          # só o texto inteiro tem o pedido
    assert _triagem_do_recorte(linhas, " ".join(linhas), conteudo_de_terceiros=True) is not None
    assert _triagem_do_recorte(linhas, " ".join(linhas)) is not None                    # e o padrão segue recusando


def test_data_e_hora_junto_da_frase_de_desafio_nao_viram_codigo_em_conteudo_de_terceiros() -> None:
    linhas = ["Instagram", ASSUNTO_INSTAGRAM, "Thu 10:21", PREVIA]
    assert _triagem_do_recorte(linhas, " ".join(linhas), conteudo_de_terceiros=True) is None


# ------------------------------------------------------------------ a leitura visual de ponta a ponta (barreira 12)
async def test_o_pedido_misto_do_p043_agora_le_o_remetente_da_mensagem_do_instagram() -> None:
    lido = await _ler(True, LINHAS, "Instagram")
    assert lido.valor == "Instagram" and len(lido.sha256) == 64


async def test_sem_a_marca_a_mesma_leitura_para_na_triagem_como_antes() -> None:
    for terceiros in (None, False):
        with pytest.raises(LeituraVisualRecusada) as exc:
            await _ler(terceiros, LINHAS, "Instagram")
        assert exc.value.codigo == "triagem" and exc.value.motivo == "verificação da conta"


async def test_o_assunto_com_a_frase_de_verificacao_tambem_e_lido_em_conteudo_de_terceiros() -> None:
    lido = await _ler(True, LINHAS, ASSUNTO_INSTAGRAM, nome="assunto")
    assert lido.valor == ASSUNTO_INSTAGRAM


async def test_com_a_marca_o_codigo_na_linha_ainda_recusa_a_leitura_inteira() -> None:
    linhas = ["Instagram", "482913 is your Instagram verification code", PREVIA]
    with pytest.raises(LeituraVisualRecusada) as exc:
        await _ler(True, linhas, "Instagram")
    assert exc.value.codigo == "triagem" and "482913" not in exc.value.rotulo


# ------------------------------------------------------------------ a declaração no telas.yaml
def test_a_marca_e_booleana_e_o_padrao_e_falso() -> None:
    assert _conhecimento(None).regioes_visuais[0].conteudo_de_terceiros is False
    assert _conhecimento(True).regioes_visuais[0].conteudo_de_terceiros is True
    with pytest.raises(ConhecimentoInvalido, match="conteudo_de_terceiros"):
        _conhecimento("sim")                                    # type: ignore[arg-type]


def test_o_outlook_declara_a_linha_da_caixa_como_conteudo_de_terceiros() -> None:
    from app.planning.capabilities import CONHECIMENTO_DE_APPS

    k = carregar(CONHECIMENTO_DE_APPS / "com.microsoft.office.outlook" / "telas.yaml")
    # a caixa e, desde o 31.339, as pastas de sistema (mesma lista)
    assert {r.tela: r.conteudo_de_terceiros for r in k.regioes_visuais} == {"caixa_de_entrada": True, "pasta_de_email": True}
