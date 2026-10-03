"""32.2 (1/6): a `ConversaDoCanal` separada do `LeitorDoTelegram`, e `registrar` sem saída (o webhook do Trello só grava,
`docs/design/trello-integracao.md` §8.5). Sem saída valem as mesmas políticas e a mesma gravação, mas nada sai (não apaga,
não responde, não confirma botão); o `tratar_pendentes(saida)` seguinte responde a recusa de credencial, uma vez só.

Simulado: Bot API falsa (`httpx.MockTransport`) e portas falsas de `test_telegram_entrada.py`, banco SQLite real."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.modules.avisos.infrastructure.entrada import (
    RESPOSTA_CREDENCIAL_SEM_APAGAR,
    ConversaDoCanal,
    Recebida,
    SaidaDoTelegram,
)
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial

from .test_canais_contrato import SaidaFalsa
from .test_telegram_entrada import Cenario

pytestmark = pytest.mark.asyncio


def recebida(id_externo: str, texto: str, *, ref: str = "50") -> Recebida:
    return Recebida(id_externo=id_externo, ordem=None, tipo="mensagem", do_dono=True, texto=texto, ref_mensagem=ref)


def chamadas_que_falam(c: Cenario) -> list[str]:
    """As chamadas à Bot API que mexem no chat (tudo menos a leitura)."""
    return [m for m, _, _ in c.bot.chamadas if m != "getUpdates"]


async def test_registrar_sem_saida_grava_a_credencial_sem_texto_e_nao_chama_o_bot(tmp_path: Path) -> None:
    c = Cenario(tmp_path)
    await c.servico.conversa.registrar(recebida("5", "123456"))
    linha = c.linha(5)
    assert (linha["estado"], linha["texto"], linha["tamanho"]) == ("recusada", None, 6)
    assert "credencial" in str(linha["erro"])
    assert c.bot.chamadas == []                         # nem apagar, nem responder, nem getUpdates
    assert c.portas.nomes() == []


async def test_tratar_pendentes_responde_a_recusa_uma_vez_so_sem_apagar(tmp_path: Path) -> None:
    c = Cenario(tmp_path)
    await c.servico.conversa.registrar(recebida("5", "123456"))
    saida = SaidaDoTelegram(c.servico.canal())
    await c.servico.conversa.tratar_pendentes(saida)
    assert c.bot.textos() == [RESPOSTA_CREDENCIAL_SEM_APAGAR]
    assert c.bot.chamou("deleteMessage") == 0           # a mensagem não foi apagada, e a resposta diz isso
    [(_, _, corpo)] = [x for x in c.bot.chamadas if x[0] == "sendMessage"]
    assert corpo["reply_parameters"]["message_id"] == 50           # responde à mensagem que ficou no canal
    ident = c.repo.id_de("5")
    assert ident is not None and c.repo.enviada(str(c.bot.mid))["entrada_id"] == ident     # type: ignore[index]
    # Uma 2ª volta não responde de novo: há uma `canal_enviadas` com o `entrada_id` dela.
    await c.servico.conversa.tratar_pendentes(saida)
    assert c.bot.textos() == [RESPOSTA_CREDENCIAL_SEM_APAGAR]


async def test_a_resposta_a_pergunta_de_credencial_sem_saida_usa_o_texto_da_pergunta(tmp_path: Path) -> None:
    c = Cenario(tmp_path)
    c.portas.sensivel_aberta = True
    await c.servico.conversa.registrar(recebida("6", "kiwi"))       # curta, com uma pergunta de senha aberta
    assert c.linha(6)["estado"] == "recusada" and c.linha(6)["texto"] is None
    assert c.bot.chamadas == []
    await c.servico.conversa.tratar_pendentes(SaidaDoTelegram(c.servico.canal()))
    assert len(c.bot.textos()) == 1 and "pergunta por senha" in c.bot.textos()[0]
    assert "não consegui apagar" in c.bot.textos()[0]


async def test_registrar_sem_saida_a_recusa_por_tamanho_fica_sem_resposta(tmp_path: Path) -> None:
    c = Cenario(tmp_path)
    limite = c.cfg.file.avisos.entrada.max_chars
    await c.servico.conversa.registrar(recebida("7", "a " * limite))
    assert (c.linha(7)["estado"], c.linha(7)["texto"]) == ("recusada", None)
    await c.servico.conversa.tratar_pendentes(SaidaDoTelegram(c.servico.canal()))
    assert chamadas_que_falam(c) == []                  # sem credencial a avisar, e o texto nem foi guardado


async def test_registrar_sem_saida_grava_o_comando_e_o_tratar_pendentes_trata(tmp_path: Path) -> None:
    c = Cenario(tmp_path)
    await c.servico.conversa.registrar(recebida("8", "/status"))
    assert (c.linha(8)["estado"], c.linha(8)["texto"]) == ("recebida", "/status")
    assert c.portas.nomes() == [] and c.bot.chamadas == []
    await c.servico.conversa.tratar_pendentes(SaidaDoTelegram(c.servico.canal()))
    assert c.portas.nomes() == ["status"]
    assert c.linha(8)["estado"] == "feita" and c.bot.textos() == ["Central: ok"]


async def test_a_conversa_se_monta_sem_telegram_e_trata_com_o_operador_do_canal(tmp_path: Path) -> None:
    c = Cenario(tmp_path)                               # só pelo banco, a config e as portas falsas
    triagem = TriagemDeCredencial()
    conversa = ConversaDoCanal(c.cfg, c.repo, c.portas, recusa=triagem.recusa, redigir=triagem.redigir,
                               operador="trello:abc")
    saida = SaidaFalsa(pode_apagar=False)
    await conversa.registrar(recebida("9", "/status", ref="m-9"), saida)
    await conversa.tratar_pendentes(saida)
    assert c.portas.chamadas == [("status", (), "trello:abc")]          # o operador gravado é o do canal
    assert [(t, r) for _, t, r in saida.enviadas] == [("Central: ok", "m-9")]
    assert saida.apagadas == []
