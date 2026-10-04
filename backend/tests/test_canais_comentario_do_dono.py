"""Item 28.30: comentário do dono em cartão nunca fica mudo (defeito do dono, Telegram, 04/10 18:56Z: o "Autorizado" dele
em dois cartões ficou 1 h 30 sem ninguém reconhecer).

Prova `simulated` (`arquivo::teste`): Trello e Telegram falsos, banco de teste. Cobre: o comentário do dono num cartão do
plano vira pedido de confirmação no Telegram (chave, tipo, nome do cartão e texto filtrados, link) e resposta no cartão;
o eco da própria resposta e o comentário com 🤖 não viram nada (C-07); quem o dono autorizou, a credencial e o canal
desligado; o sim e o não no Telegram vão à orquestradora sem virar pedido; e o recado que falha por erro interno responde
(a entrada 1256 de 04/10 ficou `falhou` sem resposta).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.modules.avisos.application.entrada import Fato, rotear
from app.modules.avisos.domain.mensagem import Aviso
from app.modules.avisos.infrastructure.entrada import RESPOSTA_FALHA_INTERNA, Recebida
from app.modules.avisos.infrastructure.fila_sql import SEM_AGRUPAR
from app.modules.avisos.infrastructure.trello_leitor import (
    RESPOSTA_COMENTARIO_SEM_TELEGRAM,
    TIPO_DO_COMENTARIO,
    RefDoTrello,
    SaidaDoTrello,
)
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.security.sessions import NomeInvalido, normalizar_nome

from . import test_telegram_entrada as tg
from .test_trello_leitor import AMIGO, C_MANUAL, DONO, PREFIXO, Cenario


async def _cenario(tmp_path: Path, **kw: object) -> Cenario:
    cen = Cenario(tmp_path, **kw)                                           # type: ignore[arg-type]
    await cen.sobe()
    return cen


async def test_o_pedido_de_confirmacao_nomeia_o_cartao_e_o_comentario(tmp_path: Path) -> None:
    c = await _cenario(tmp_path)
    c.trello.nomes[C_MANUAL] = "Ligar o resumo de hora"
    acao = c.trello.comenta(DONO, C_MANUAL, "Autorizado")
    await c.volta()
    assert len(c.avisos) == 1
    a = c.avisos[0]
    assert (a.chave, a.tipo) == (f"comentario:{acao['id']}:{C_MANUAL}", TIPO_DO_COMENTARIO)
    assert a.titulo.endswith("💬 Você comentou no cartão «Ligar o resumo de hora»")
    assert a.corpo.split("\n") == ["«Autorizado»",
                                   "Comentário no Trello sozinho não autoriza nada; o sim daqui é que vale.",
                                   "Espera você: responda sim ou não a esta mensagem."]
    assert a.link == f"https://trello.com/c/{C_MANUAL}"
    linha = c.linha(str(acao["id"]))
    assert (linha["estado"], linha["destino"]) == ("orquestradora", "orquestradora")
    # A chave vira o fato do reply no Telegram.
    assert Fato.de(a.chave) == Fato("comentario", str(acao["id"]), C_MANUAL)
    # O eco da resposta da Central (o token é do dono) volta como action do dono e não pede nada de novo.
    await c.volta()
    await c.volta()
    assert len(c.avisos) == 1 and len(c.trello.textos()) == 1


async def test_comentario_com_o_prefixo_de_ia_nao_pede_nada(tmp_path: Path) -> None:
    """C-07: a ANA, as sessões e a orquestradora escrevem com o token do dono; o 🤖 no começo é o que separa."""
    c = await _cenario(tmp_path)
    c.trello.comenta(DONO, C_MANUAL, "🤖 ANA · 20:17Z · 98 % da cota semanal")
    c.trello.comenta(DONO, C_MANUAL, "🤖 ORQ · deploy 32 no ar")
    await c.volta()
    assert c.avisos == [] and c.trello.textos() == []
    assert c.repo.contagens() == {"ignorada": 2}


async def test_nome_e_texto_com_dado_que_nao_sai_ficam_de_fora(tmp_path: Path) -> None:
    c = await _cenario(tmp_path)
    c.portas.personas = ["Bruno Lima", "Bruno", "Lima", "bruno.qa"]     # como `portas_da_central.nomes_de_persona`
    c.trello.nomes[C_MANUAL] = "Falar com o Bruno"
    c.trello.comenta(DONO, C_MANUAL, "pode mandar para maria@exemplo.com")
    await c.volta()
    a = c.avisos[0]
    assert a.titulo.endswith("💬 Você comentou num cartão do Trello")
    assert a.corpo.split("\n")[0] == "O texto fica no cartão: tem dado que não sai por aqui."
    texto = a.titulo + a.corpo
    assert "Bruno" not in texto and "maria" not in texto


async def test_quem_o_dono_autorizou_nao_dispara_pergunta(tmp_path: Path) -> None:
    c = await _cenario(tmp_path, autorizados=[AMIGO])
    c.trello.comenta(AMIGO, C_MANUAL, "anotação do amigo")
    await c.volta()
    assert c.avisos == [] and c.trello.textos() == []
    assert c.repo.contagens() == {"ignorada": 1}


async def test_comentario_com_cara_de_credencial_e_recusado_e_nao_vai_ao_telegram(tmp_path: Path) -> None:
    c = await _cenario(tmp_path)
    c.trello.comenta(DONO, C_MANUAL, "senha: kiwi2024!")
    await c.volta()
    assert c.avisos == []
    assert "orquestradora" not in c.repo.contagens()


async def test_sem_o_telegram_o_cartao_diz_que_a_confirmacao_nao_saiu(tmp_path: Path) -> None:
    c = await _cenario(tmp_path)

    def recusa(_a: Aviso) -> bool:
        return False

    c.leitor.conversa.avisar_dono = recusa
    c.trello.comenta(DONO, C_MANUAL, "Autorizado")
    await c.volta()
    assert c.trello.textos() == [PREFIXO + RESPOSTA_COMENTARIO_SEM_TELEGRAM]
    assert c.repo.contagens() == {"orquestradora": 1}


async def test_linha_sem_autor_lido_nao_vale_como_do_dono(tmp_path: Path) -> None:
    """Revisão da #312 (04/10 20:36Z): sem autor na referência, a linha não é do dono, mesmo marcada `do_dono` (a tradução
    já não marca; aqui é a defesa da própria conversa). Não pede confirmação nem age com o operador do dono."""
    c = await _cenario(tmp_path)
    saida = SaidaDoTrello(c.cliente, TriagemDeCredencial().redigir, c.relogio)
    r = Recebida(id_externo="a-sem-autor", ordem=None, tipo="mensagem", do_dono=True, texto="Autorizado",
                 ref_mensagem=str(RefDoTrello(C_MANUAL, "a-sem-autor", "")),
                 escrita_em=c.relogio.t.timestamp())
    await c.leitor.conversa.registrar(r, saida)
    await c.leitor.conversa.tratar_pendentes(saida)
    assert c.avisos == [] and c.trello.textos() == []
    assert c.linha("a-sem-autor")["estado"] == "ignorada"
    assert c.leitor.conversa.operador == "trello:desconhecido"


@pytest.mark.parametrize("nome", ["trello:membro-dono", "Trello:abc", " telegram:123", "trello : x"])
def test_o_login_recusa_o_prefixo_dos_canais(nome: str) -> None:
    with pytest.raises(NomeInvalido):
        normalizar_nome(nome)


def test_o_login_aceita_nome_que_so_parece() -> None:
    assert normalizar_nome("Trellozinho") == "Trellozinho"


def test_o_pedido_de_confirmacao_nao_e_agrupado() -> None:
    assert TIPO_DO_COMENTARIO.startswith(SEM_AGRUPAR)


@pytest.mark.parametrize(("texto", "repasse"), [("sim", "comentario_sim"), ("Sim!", "comentario_sim"),
                                                ("não", "comentario_nao"), ("nao", "comentario_nao")])
def test_sim_e_nao_ao_pedido_vao_a_orquestradora(texto: str, repasse: str) -> None:
    i = rotear(texto, fato="comentario:a0042:card-manual")
    assert (i.tipo, i.repasse, i.ref) == ("orquestradora", repasse, "a0042")
    assert "a0042" in i.texto and "card-manual" in i.texto


def test_outra_resposta_ao_pedido_nao_vira_pedido() -> None:
    i = rotear("faz o deploy agora", fato="comentario:a0042:card-manual")
    assert i.tipo == "desconhecida" and "sim" in (i.motivo or "")


async def test_o_sim_no_telegram_fica_para_a_orquestradora_e_responde(tmp_path: Path) -> None:
    c = tg.Cenario(tmp_path)
    c.repo.registrar_enviada("555", "aviso", fato="comentario:a0042:card-manual")
    await c.volta(tg.msg(5, "sim", reply_to=555))
    linha = c.linha(5)
    assert (linha["estado"], linha["destino"]) == ("orquestradora", "orquestradora")
    assert c.bot.textos()[-1] == "Confirmado: repassei à orquestradora, que age e responde no cartão."
    assert "previa" not in c.portas.nomes() and "criar" not in c.portas.nomes()


async def test_falha_interna_nao_fica_muda(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    c = tg.Cenario(tmp_path)

    def quebra() -> str:
        raise RuntimeError("porta fora")

    monkeypatch.setattr(c.portas, "status", quebra)
    await c.volta(tg.msg(5, "/status"))
    assert c.linha(5)["estado"] == "falhou"
    assert c.bot.textos()[-1] == RESPOSTA_FALHA_INTERNA
