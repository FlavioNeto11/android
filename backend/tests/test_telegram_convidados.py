"""28.18 (C-08 a C-11; emenda de 04/10 ao ADR-071): quem fala com o bot e não é o dono. Apresentação e nome primeiro,
o dono decide por reply com sim ou não, o convidado autorizado nunca executa nem decide, e nada do dono vai ao chat
dele. Bot falso por `httpx.MockTransport`; o aviso ao dono é capturado e o reply dele é ligado ao fato como a fila
faria (`canal_enviadas`)."""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from app.config import Config
from app.db import Database
from app.modules.avisos.adapters.telegram import CanalTelegram
from app.modules.avisos.application.entrada import rotear
from app.modules.avisos.domain.mensagem import Aviso
from app.modules.avisos.infrastructure.contatos_sql import ContatosDoCanal
from app.modules.avisos.infrastructure.convidados import (
    AGUARDANDO_O_DONO,
    AJUDA_DO_CONVIDADO,
    AUTORIZADO,
    PERGUNTA_DO_NOME,
    RECEBIDO,
    SEGREDO,
    SO_DO_DONO,
    TIPO_GRUPO,
    TIPO_MENSAGEM,
    TIPO_NOVO,
    ConvidadosDoTelegram,
    limpar_nome,
)
from app.modules.avisos.infrastructure.entrada import ServicoDeEntrada, parece_codigo
from app.modules.avisos.infrastructure.entrada_sql import EntradasDoCanal
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial

from .conftest import make_config
from .test_telegram_entrada import CHAT, TOKEN, BotFalso, PortasFalsas, msg

pytestmark = pytest.mark.asyncio

GUEST = 777
OUTRO = 888
STATUS_CONVIDADO = "Central: no ar\nAparelhos online: 2\nExecuções em andamento: 0"


def membro(uid: int, status: str, *, chat: int = -100500, tipo: str = "supergroup") -> dict[str, object]:
    return {"update_id": uid, "my_chat_member": {"chat": {"id": chat, "type": tipo, "title": "Grupo X"},
                                                  "from": {"id": OUTRO}, "new_chat_member": {"status": status}}}


def de_convidado(uid: int, texto: str, *, chat: int = GUEST) -> dict[str, object]:
    u = msg(uid, texto, chat=chat)
    u["message"]["from"] = {"id": chat, "first_name": "Fulana", "username": "fulana_x", "is_bot": False}  # type: ignore[index]
    return u


class Cenario:
    def __init__(self, tmp_path: Path, *, ligado: bool = True) -> None:
        self.cfg: Config = make_config(tmp_path)
        self.cfg.ensure_dirs()
        self.cfg.file.avisos.enabled = True
        self.cfg.file.avisos.entrada.enabled = True
        self.cfg.file.avisos.entrada.convidados.enabled = ligado
        self.cfg.file.avisos.entrada.convidados.avisos_por_hora = 2
        self.cfg.env.telegram_bot_token = SecretStr(TOKEN)
        self.cfg.env.telegram_chat_id = SecretStr(str(CHAT))
        self.db = Database(self.cfg.db_dsn)
        self.db.migrate()
        self.bot = BotFalso()
        self.portas = PortasFalsas()
        self.repo = EntradasDoCanal(self.db)
        self.repo.gravar_inicio(None)
        # A ajuda inicial do canal já saiu (o canal em uso): o chat do dono só recebe o que o teste provoca.
        self.db.execute("INSERT INTO canal_enviadas(canal, ref_mensagem, origem, enviada_em)"
                        " VALUES ('telegram', '1', 'ajuda', '2026-10-04T00:00:00Z')")
        self.contatos = ContatosDoCanal(self.db)
        self.avisos: list[Aviso] = []
        triagem = TriagemDeCredencial()
        self.convidados = ConvidadosDoTelegram(
            self.cfg, self.contatos, avisar_dono=self._avisar, recusa=lambda t: triagem.recusa(t) or parece_codigo(t),
            redigir=triagem.redigir, status=lambda: STATUS_CONVIDADO)
        canal = CanalTelegram(TOKEN, str(CHAT), client=httpx.AsyncClient(transport=httpx.MockTransport(self.bot.handler)))

        async def dormir(_s: float) -> None:
            return None

        self.servico = ServicoDeEntrada(self.cfg, self.repo, self.portas, lider=lambda _n: 1, recusa=triagem.recusa,
                                        redigir=triagem.redigir, canal=canal, dormir=dormir, convidados=self.convidados)
        self._ref = 5000

    def _avisar(self, aviso: Aviso) -> bool:
        """Como a fila: a chave é única, e o aviso enviado ao dono vira `canal_enviadas` com o fato."""
        if any(a.chave == aviso.chave for a in self.avisos):
            return False
        self.avisos.append(aviso)
        self._ref += 1
        self.db.execute("INSERT INTO canal_enviadas(canal, ref_mensagem, origem, fato, enviada_em)"
                        " VALUES ('telegram', ?, 'aviso', ?, '2026-10-04T00:00:00Z')", (str(self._ref), aviso.chave))
        return True

    def ref_do_aviso(self, chave: str) -> int:
        r = self.db.one("SELECT ref_mensagem FROM canal_enviadas WHERE fato=?", (chave,))
        assert r is not None
        return int(r["ref_mensagem"])

    async def volta(self, *updates: dict[str, object]) -> int:
        self.bot.guardadas.extend(updates)
        return await self.servico.uma_volta()

    def para(self, chat: int) -> list[str]:
        return [str(m["text"]) for m in self.bot.mensagens() if str(m["chat_id"]) == str(chat)]

    def eventos(self, chat: int = GUEST) -> list[tuple[str, str | None]]:
        return [(str(r["tipo"]), r["detalhe"]) for r in self.db.query(
            "SELECT tipo, detalhe FROM canal_contato_eventos WHERE chat_id=? ORDER BY id", (str(chat),))]


@pytest.fixture
def c(tmp_path: Path) -> Cenario:
    return Cenario(tmp_path)


async def _ate_o_aviso(c: Cenario) -> None:
    await c.volta(de_convidado(10, "/start"))
    await c.volta(de_convidado(11, "Fulana de Tal"))


# ---------------------------------------------------------------------------------------------- chegada e nome
async def test_chat_novo_recebe_so_a_apresentacao_e_a_pergunta_do_nome_uma_vez(c: Cenario) -> None:
    await c.volta(de_convidado(10, "oi, quero saber dos aparelhos"))
    assert c.para(GUEST) == [PERGUNTA_DO_NOME]
    assert "ANA, a IA Gerente de Operações da Central" in PERGUNTA_DO_NOME and "IA, não uma pessoa" in PERGUNTA_DO_NOME
    assert c.para(CHAT) == [] and c.avisos == [] and c.portas.chamadas == []
    linha = c.db.one("SELECT texto, estado, do_dono FROM canal_entradas WHERE id_externo='10'")
    assert linha is not None and (linha["texto"], linha["estado"], linha["do_dono"]) == (None, "convidado", 0)
    # A mesma update relida não repete a pergunta; outra mensagem antes do nome também não.
    c.bot.guardadas.append(de_convidado(10, "oi, quero saber dos aparelhos"))
    await c.servico.uma_volta()
    assert c.para(GUEST) == [PERGUNTA_DO_NOME]
    perfil = c.db.scalar("SELECT perfil FROM canal_contatos WHERE chat_id=?", (str(GUEST),))
    assert '"username": "fulana_x"' in perfil and '"first_name": "Fulana"' in perfil


async def test_o_nome_vai_so_ao_aviso_do_dono_e_a_pessoa_fica_esperando(c: Cenario) -> None:
    await _ate_o_aviso(c)
    contato = c.contatos.obter(str(GUEST))
    assert contato is not None and (contato.estado, contato.nome_informado) == ("aguardando_dono", "Fulana de Tal")
    [aviso] = c.avisos
    assert (aviso.tipo, aviso.chave) == (TIPO_NOVO, f"convidado:{GUEST}:novo")
    assert "Fulana de Tal" in aviso.corpo and "sim" in aviso.corpo and aviso.titulo.startswith("ANA: ")
    assert c.para(GUEST) == [PERGUNTA_DO_NOME, AGUARDANDO_O_DONO]
    assert all("Fulana" not in t for t in c.para(GUEST))
    # Enquanto o dono não decide: nada sai para a pessoa, e nenhum aviso novo.
    await c.volta(de_convidado(12, "e aí?"), de_convidado(13, "/status"))
    assert c.para(GUEST) == [PERGUNTA_DO_NOME, AGUARDANDO_O_DONO] and len(c.avisos) == 1
    assert [t for t, _ in c.eventos()].count("mensagem") == 4


async def test_o_nome_e_uma_linha_redigida_e_cortada() -> None:
    redigir = TriagemDeCredencial().redigir
    assert limpar_nome("  Ana\nsegunda linha", 60, redigir) == "Ana"
    assert limpar_nome("Bia \x07 Souza", 60, redigir) == "Bia Souza"
    assert "@" not in limpar_nome("Carla carla@exemplo.com", 60, redigir)
    assert len(limpar_nome("x" * 300, 60, redigir)) == 60


# ---------------------------------------------------------------------------------------------- a decisão do dono
async def test_o_sim_do_dono_autoriza_e_nao_vira_pedido(c: Cenario) -> None:
    """O buraco que este item fecha: sem o ramo do fato `convidado`, o "sim" cairia no texto livre e iria à prévia."""
    await _ate_o_aviso(c)
    ref = c.ref_do_aviso(f"convidado:{GUEST}:novo")
    await c.volta(msg(20, "sim", reply_to=ref))
    assert c.portas.chamadas == []                                      # nenhuma prévia, nenhuma execução
    contato = c.contatos.obter(str(GUEST))
    assert contato is not None and contato.estado == "autorizado"
    assert c.para(GUEST)[-1] == AUTORIZADO
    assert c.para(CHAT)[-1].startswith("Autorizado:")
    linha = c.db.one("SELECT intencao, estado FROM canal_entradas WHERE id_externo='20'")
    assert linha is not None and (linha["intencao"], linha["estado"]) == ("autorizar_convidado", "feita")
    # O mesmo "sim" de novo não manda nada à pessoa.
    await c.volta(msg(21, "sim", reply_to=ref))
    assert c.para(GUEST).count(AUTORIZADO) == 1 and c.para(CHAT)[-1] == "Já estava decidido assim."


async def test_o_nao_do_dono_recusa_e_a_pessoa_fica_em_silencio(c: Cenario) -> None:
    await _ate_o_aviso(c)
    await c.volta(msg(20, "não", reply_to=c.ref_do_aviso(f"convidado:{GUEST}:novo")))
    contato = c.contatos.obter(str(GUEST))
    assert contato is not None and contato.estado == "recusado"
    antes = list(c.para(GUEST))
    await c.volta(de_convidado(30, "oi de novo"), de_convidado(31, "/ajuda"))
    assert c.para(GUEST) == antes and len(c.avisos) == 1


async def test_o_reply_ao_aviso_de_convidado_nunca_e_pedido_livre() -> None:
    assert rotear("sim", fato=f"convidado:{GUEST}:novo").tipo == "autorizar_convidado"
    assert rotear("Não.", fato=f"convidado:{GUEST}:novo").tipo == "recusar_convidado"
    assert rotear("talvez amanhã", fato=f"convidado:{GUEST}:novo").tipo == "desconhecida"
    for detalhe in ("msg-55", "grupo-9"):
        i = rotear("manda o relatório pra ela", fato=f"convidado:{GUEST}:{detalhe}")
        assert i.tipo == "desconhecida" and i.motivo and "nada foi executado" in i.motivo


# ---------------------------------------------------------------------------------------------- convidado autorizado
async def _autorizado(c: Cenario) -> None:
    await _ate_o_aviso(c)
    await c.volta(msg(20, "sim", reply_to=c.ref_do_aviso(f"convidado:{GUEST}:novo")))


async def test_autorizado_tem_ajuda_status_so_com_contagens_e_comando_do_dono_recusado(c: Cenario) -> None:
    await _autorizado(c)
    await c.volta(de_convidado(40, "/ajuda"), de_convidado(41, "/status"), de_convidado(42, "/para android-09 abrir"),
                  de_convidado(43, "/aprovar 4985a1"))
    assert c.para(GUEST)[-4:] == [AJUDA_DO_CONVIDADO, STATUS_CONVIDADO, SO_DO_DONO, SO_DO_DONO]
    assert c.portas.chamadas == []


async def test_pedido_do_convidado_vira_aviso_ao_dono_com_teto_por_hora(c: Cenario) -> None:
    await _autorizado(c)
    await c.volta(de_convidado(50, "pode mandar o resumo da semana?"), de_convidado(51, "e o de ontem?"),
                  de_convidado(52, "e o de hoje?"))
    pedidos = [a for a in c.avisos if a.tipo == TIPO_MENSAGEM]
    assert len(pedidos) == 2                                            # avisos_por_hora = 2
    assert "Fulana de Tal" in pedidos[0].titulo and "resumo da semana" in pedidos[0].corpo
    assert "Nada foi executado" in pedidos[0].corpo
    assert c.para(GUEST).count(RECEBIDO) == 2 and c.portas.chamadas == []
    # O reply do dono a esse aviso não vira pedido e não vai à pessoa.
    await c.volta(msg(60, "manda sim", reply_to=c.ref_do_aviso(pedidos[0].chave)))
    assert c.portas.chamadas == [] and c.para(GUEST).count(RECEBIDO) == 2


async def test_segredo_do_convidado_nao_fica_e_a_mensagem_sai_do_chat_dele(c: Cenario) -> None:
    await _autorizado(c)
    await c.volta(de_convidado(70, "minha senha é Abc!2345678"))
    assert c.para(GUEST)[-1] == SEGREDO
    apagadas = [p for m, _q, p in c.bot.chamadas if m == "deleteMessage"]
    assert apagadas and str(apagadas[-1]["chat_id"]) == str(GUEST)
    assert ("retido", None) in c.eventos()
    assert not any("Abc!2345678" in str(d) for _t, d in c.eventos())
    assert not any("Abc!2345678" in a.corpo for a in c.avisos)


# ---------------------------------------------------------------------------------------------- grupos e chats
async def test_grupo_e_ignorado_e_o_bot_posto_num_grupo_avisa_o_dono_sem_o_nome_do_grupo(c: Cenario) -> None:
    await c.volta(msg(80, "/status", chat=-100500, tipo="supergroup", autor=OUTRO), membro(81, "member"))
    assert c.bot.mensagens() == [] and c.portas.chamadas == []
    [aviso] = c.avisos
    assert aviso.tipo == TIPO_GRUPO and "posto num grupo" in aviso.titulo
    assert "Grupo X" not in aviso.titulo + aviso.corpo
    await c.volta(membro(82, "left"))
    assert "tirado de um grupo" in c.avisos[-1].titulo


async def test_nada_do_convidado_sai_pelo_chat_do_dono_alem_dos_avisos(c: Cenario) -> None:
    await _autorizado(c)
    await c.volta(de_convidado(90, "/status"))
    assert all(str(m["chat_id"]) in (str(GUEST), str(CHAT)) for m in c.bot.mensagens())
    assert all("Fulana" not in t for t in c.para(CHAT))      # o nome vai no AVISO (fila), não nas respostas diretas


async def test_desligado_quem_nao_e_o_dono_segue_sem_texto_e_sem_resposta(tmp_path: Path) -> None:
    c = Cenario(tmp_path, ligado=False)
    await c.volta(de_convidado(10, "oi"), membro(11, "member"))
    assert c.bot.mensagens() == [] and c.avisos == []
    assert c.db.scalar("SELECT COUNT(*) FROM canal_contatos") == 0
    estados = {str(r["estado"]) for r in c.db.query("SELECT estado FROM canal_entradas WHERE id_externo IN ('10','11')")}
    assert estados == {"ignorada"}
