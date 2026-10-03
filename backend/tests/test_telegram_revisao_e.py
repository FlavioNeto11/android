"""28.15, segunda revisão do PR #166 (Android, 03/10): a mensagem antiga da Central fora do ar (E2), o texto curto com
pergunta de senha aberta fora do reply (E6) e a causa de cada recusa definitiva do `getUpdates` (E7). Simulado: Bot API
falsa (`httpx.MockTransport`) e portas falsas, banco SQLite real."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.modules.avisos.infrastructure.entrada import RESPOSTA_PERGUNTA_CREDENCIAL, _SE_ERA_PEDIDO

from .test_telegram_correcoes import cenario_que_para_no_primeiro_sono
from .test_telegram_entrada import TOKEN, Cenario, botao, msg


#: o relógio de parede do serviço, fixo (regra dos testes: relógio injetável)
AGORA = 1_790_000_000.0


def cenario(tmp_path: Path) -> Cenario:
    c = Cenario(tmp_path)
    c.servico._agora = lambda: AGORA
    return c


def escrita(update: dict[str, object], ha_s: float) -> dict[str, object]:
    """A update com a hora em que a pessoa escreveu (`message.date`), `ha_s` segundos antes de `AGORA`."""
    m = update["message"]
    assert isinstance(m, dict)
    m["date"] = int(AGORA - ha_s)
    return update


# --------------------------------------------------------------------------- E2: a mensagem de quando a Central estava fora
async def test_aprovar_escrito_horas_atras_nao_executa_e_o_dono_e_avisado_uma_vez(tmp_path: Path) -> None:
    c = cenario(tmp_path)
    dez_horas = 10 * 3600
    await c.volta(escrita(msg(5, "/aprovar 4985a1"), dez_horas), escrita(msg(6, "sim", reply_to=777), dez_horas),
                  escrita(msg(7, "/status"), 5))
    for uid in (5, 6):
        linha = c.linha(uid)
        assert (linha["estado"], linha["texto"]) == ("ignorada", None)          # gravada SEM o texto
        assert "antiga" in str(linha["erro"])
    assert c.linha(7)["estado"] == "feita"                                      # a recente segue normal
    # nada aprovado nem vetado (a triagem de senha ainda lê as perguntas antes da idade: a senha velha também sai)
    assert [n for n in c.portas.nomes() if n != "pergunta_sensivel"] == ["status"]
    avisos = [t for t in c.bot.textos() if "Central estava fora do ar" in t]
    assert len(avisos) == 1 and avisos[0].startswith("2 mensagens foram escritas")
    assert c.repo.proximo_offset() == 8                                         # o offset andou: não volta a cada volta


async def test_a_idade_vale_em_toda_subida_nao_so_na_primeira(tmp_path: Path) -> None:
    c = cenario(tmp_path)                       # canal já em uso (base=True): o descarte da 1ª subida não roda
    c.servico = c.novo_servico()                # a Central reiniciou
    c.servico._agora = lambda: AGORA
    await c.volta(escrita(msg(5, "/vetar 4985a1"), c.cfg.file.avisos.entrada.idade_max_s + 60))
    assert c.linha(5)["estado"] == "ignorada" and "vetar" not in c.portas.nomes()


async def test_mensagem_dentro_da_idade_e_tratada(tmp_path: Path) -> None:
    c = cenario(tmp_path)
    await c.volta(escrita(msg(5, "/status"), c.cfg.file.avisos.entrada.idade_max_s - 60))
    assert c.linha(5)["estado"] == "feita"
    assert not [t for t in c.bot.textos() if "Central estava fora do ar" in t]


async def test_senha_antiga_ainda_sai_do_chat(tmp_path: Path) -> None:
    """A idade não passa na frente da triagem: a senha de ontem também é apagada do chat."""
    c = cenario(tmp_path)
    await c.volta(escrita(msg(5, "123456"), 10 * 3600))
    assert (c.linha(5)["estado"], c.linha(5)["texto"]) == ("recusada", None)
    assert c.bot.chamou("deleteMessage") == 1


async def test_botao_sem_hora_propria_fica_com_a_idade_da_previa(tmp_path: Path) -> None:
    """O `message.date` do botão é a hora da mensagem do BOT; quem barra o toque velho é o `ttl_previa_s`."""
    c = cenario(tmp_path)
    await c.volta(msg(5, "/para android-09 abra o QA Messenger"))
    [(_, _, previa)] = [ch for ch in c.bot.chamadas if ch[0] == "sendMessage" and "reply_markup" in ch[2]]
    teclado = previa["reply_markup"]
    assert isinstance(teclado, dict)
    dado = teclado["inline_keyboard"][0][1]["callback_data"]                    # Cancelar
    toque = botao(6, str(dado), mid=1001)
    cb = toque["callback_query"]
    assert isinstance(cb, dict) and isinstance(cb["message"], dict)
    cb["message"]["date"] = int(AGORA - 10 * 3600)                              # a mensagem do bot é antiga
    await c.volta(toque)
    assert c.linha(6)["estado"] != "ignorada"                                   # o toque não é julgado pela hora dela


# --------------------------------------------------------------------------- E6: o curto fora do reply
@pytest.mark.parametrize(("texto", "reply_to"), [
    ("kiwi 2024", None),                        # a senha com espaço
    ("kiwi2024", 31337),                        # reply a uma mensagem do bot que a Central não mandou (orquestradora)
    ("/orq kiwi2024", None),
    ("/responder kiwi2024", None),              # sem id: não vira resposta, mas a linha guardaria o texto
])
async def test_curto_com_pergunta_de_senha_aberta_e_recusado(tmp_path: Path, texto: str, reply_to: int | None) -> None:
    c = cenario(tmp_path)
    c.portas.sensivel_aberta = True
    await c.volta(msg(5, texto, reply_to=reply_to))
    linha = c.linha(5)
    assert (linha["estado"], linha["texto"], linha["destino"]) == ("recusada", None, None)
    assert RESPOSTA_PERGUNTA_CREDENCIAL + _SE_ERA_PEDIDO in c.bot.textos()
    resto = c.db.query("SELECT * FROM canal_entradas WHERE canal='telegram'")
    assert "kiwi" not in " ".join(str(v) for r in resto for v in dict(r).values())


@pytest.mark.parametrize(("texto", "reply_to"), [
    ("kiwi 2024", None),
    ("/orq kiwi2024", None),
    ("/responder kiwi2024", None),
])
async def test_curto_sem_pergunta_aberta_segue_normal(tmp_path: Path, texto: str, reply_to: int | None) -> None:
    c = cenario(tmp_path)
    await c.volta(msg(5, texto, reply_to=reply_to))
    assert c.linha(5)["estado"] != "recusada"


async def test_recado_longo_a_orquestradora_passa_com_pergunta_aberta(tmp_path: Path) -> None:
    c = cenario(tmp_path)
    c.portas.sensivel_aberta = True
    await c.volta(msg(5, "/orq pode fazer o deploy quando a suíte terminar"))
    assert (c.linha(5)["estado"], c.linha(5)["destino"]) == ("orquestradora", "orquestradora")


# --------------------------------------------------------------------------- E7: a causa de cada recusa definitiva
@pytest.mark.parametrize(("codigo", "problema_esperado", "causa"), [
    (400, "telegram_entrada_pedido_invalido", "não é o token"),
    (401, "telegram_entrada_recusada", "token revogado"),
    (403, "telegram_entrada_recusada", "bloqueado ou removido"),
    (404, "telegram_entrada_recusada", "token malformado"),
])
async def test_cada_status_tem_a_sua_causa(tmp_path: Path, codigo: int, problema_esperado: str, causa: str) -> None:
    c = cenario(tmp_path)
    c.bot.falha = (codigo, {"ok": False, "description": "x"})
    cenario_que_para_no_primeiro_sono(c)
    with pytest.raises(asyncio.CancelledError):
        await c.servico.laco()
    [problema] = c.servico.problemas()
    assert problema.code == problema_esperado and causa in problema.hint
    assert ("TELEGRAM_BOT_TOKEN" in problema.hint) is (codigo != 400)          # o 400 não manda trocar o token
    assert TOKEN not in problema.message + problema.hint
