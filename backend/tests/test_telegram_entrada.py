"""28.15 (ADR-071): a conversa de volta pelo Telegram — offset, chat, segredo, prévia com botões, reply ao aviso,
orquestradora, limite, 409 e desfecho na thread.

Prova `simulated`: Bot API falsa (`httpx.MockTransport`, que guarda as updates como o Telegram: só confirma o que
está abaixo do offset) e portas falsas no lugar dos serviços do painel; nenhuma rede. As portas reais estão em
`test_telegram_portas.py`, no harness.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from app.config import Config
from app.db import Database
from app.modules.avisos.adapters.telegram import CanalTelegram
from app.modules.avisos.infrastructure.entrada import (
    OPERADOR_DO_TELEGRAM,
    RESPOSTA_CREDENCIAL,
    RESPOSTA_CREDENCIAL_SEM_APAGAR,
    Pendencia,
    Previa,
    Recebida,
    RecusaDaCentral,
    SaidaDoTelegram,
    ServicoDeEntrada,
)
from app.modules.avisos.infrastructure.entrada_sql import EntradasDoCanal
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.security.sessions import operador_atual

from .conftest import make_config

pytestmark = pytest.mark.asyncio

TOKEN = "123456789:AAFake-token_da_entrada-de-teste"
CHAT = 42


class BotFalso:
    """A Bot API: guarda as updates até um `getUpdates` com offset maior confirmá-las (como o Telegram)."""

    def __init__(self) -> None:
        self.guardadas: list[dict[str, object]] = []
        self.chamadas: list[tuple[str, dict[str, str], dict[str, object]]] = []
        self.mid = 1000
        self.conflito = False
        self.apagar_falha = False

    def handler(self, req: httpx.Request) -> httpx.Response:
        metodo = req.url.path.rsplit("/", 1)[-1]
        corpo = json.loads(req.content) if req.content else {}
        self.chamadas.append((metodo, dict(req.url.params), corpo))
        if metodo == "getUpdates":
            if self.conflito:
                return httpx.Response(409, json={"ok": False, "description": "Conflict: terminated by other getUpdates"})
            offset = int(req.url.params.get("offset", "0"))
            self.guardadas = [u for u in self.guardadas if int(str(u["update_id"])) >= offset]
            return httpx.Response(200, json={"ok": True, "result": self.guardadas[:50]})
        if metodo == "sendMessage":
            self.mid += 1
            return httpx.Response(200, json={"ok": True, "result": {"message_id": self.mid}})
        if metodo == "deleteMessage" and self.apagar_falha:
            return httpx.Response(400, json={"ok": False, "description": "Bad Request: message can't be deleted"})
        return httpx.Response(200, json={"ok": True, "result": True})

    def mensagens(self) -> list[dict[str, object]]:
        return [c[2] for c in self.chamadas if c[0] == "sendMessage"]

    def textos(self) -> list[str]:
        return [str(m["text"]) for m in self.mensagens()]

    def chamou(self, metodo: str) -> int:
        return sum(1 for c in self.chamadas if c[0] == metodo)


def msg(uid: int, texto: str, *, chat: int = CHAT, mid: int | None = None, reply_to: int | None = None) -> dict[str, object]:
    m: dict[str, object] = {"message_id": mid or uid * 10, "chat": {"id": chat, "type": "private"}, "text": texto}
    if reply_to is not None:
        m["reply_to_message"] = {"message_id": reply_to}
    return {"update_id": uid, "message": m}


def botao(uid: int, data: str, *, mid: int, chat: int = CHAT) -> dict[str, object]:
    return {"update_id": uid, "callback_query": {"id": f"cb{uid}", "data": data,
                                                 "message": {"message_id": mid, "chat": {"id": chat}}}}


class PortasFalsas:
    def __init__(self) -> None:
        self.chamadas: list[tuple[str, tuple[object, ...], str | None]] = []
        self.alvos: list[dict[str, object]] = [{"instance_id": "android-09", "profile_id": None, "origem": "texto"}]
        self.perguntas: list[str] = []
        self.recusar_criar = False
        self.desfechos: dict[str, str] = {}

    def _anota(self, nome: str, *args: object) -> None:
        self.chamadas.append((nome, args, operador_atual()))

    def nomes(self) -> list[str]:
        return [c[0] for c in self.chamadas]

    def status(self) -> str:
        self._anota("status")
        return "Central: ok"

    def pendencias(self) -> list[Pendencia]:
        return [Pendencia("aprovacao", "apr-0000aa11", "comentar: \"oi\""),
                Pendencia("pergunta", "r-20261002181523-4985a1", "Para qual contato?")]

    def aprovacoes_pendentes(self) -> list[str]:
        return ["apr-0000aa11", "apr-0000bb22", "apr-9999aa11"]

    def execucoes_esperando(self) -> list[str]:
        return ["r-20261002181523-4985a1"]

    def decidir(self, approval_id: str, verbo: str, nota: str | None = None) -> str:
        self._anota("decidir", approval_id, verbo) if nota is None else self._anota("decidir", approval_id, verbo, nota)
        return "Aprovado: a execução segue." if verbo == "approve" else "Vetado: nada é enviado."

    def responder(self, run_id: str, texto: str) -> tuple[str, str]:
        self._anota("responder", run_id, texto)
        return "r-nova-000001", "000001"

    def previa(self, texto: str, instance_ids: list[str] | None = None) -> Previa:
        self._anota("previa", texto, tuple(instance_ids or ()))
        if instance_ids:
            return Previa(alvos=[{"instance_id": instance_ids[0], "profile_id": None, "origem": "ui"}], perguntas=[],
                          comando=texto)
        return Previa(alvos=list(self.alvos), perguntas=list(self.perguntas), comando=texto)

    def criar(self, texto: str, alvos: list[dict[str, object]], chave: str) -> tuple[str, str]:
        self._anota("criar", texto, chave)
        if self.recusar_criar:
            raise RecusaDaCentral("Nenhum aparelho apto no pré-voo.")
        return "r-20261003180000-abc123", "abc123"

    def online(self) -> list[str]:
        return ["android-09", "android-10"]

    def desfecho(self, run_id: str) -> str | None:
        return self.desfechos.get(run_id)


class Cenario:
    def __init__(self, tmp_path: Path, *, entrada: bool = True, lider: bool = True) -> None:
        self.cfg: Config = make_config(tmp_path)
        self.cfg.ensure_dirs()
        self.cfg.file.avisos.enabled = True
        self.cfg.file.avisos.entrada.enabled = entrada
        self.cfg.env.telegram_bot_token = SecretStr(TOKEN)
        self.cfg.env.telegram_chat_id = SecretStr(str(CHAT))
        self.db = Database(self.cfg.db_dsn)
        self.db.migrate()
        self.bot = BotFalso()
        self.portas = PortasFalsas()
        self.repo = EntradasDoCanal(self.db)
        self.dormidas: list[float] = []
        self.lider = lider
        self.servico = self.novo_servico()

    def novo_servico(self) -> ServicoDeEntrada:
        triagem = TriagemDeCredencial()
        canal = CanalTelegram(TOKEN, str(CHAT), client=httpx.AsyncClient(transport=httpx.MockTransport(self.bot.handler)))

        async def dormir(s: float) -> None:
            self.dormidas.append(s)

        return ServicoDeEntrada(self.cfg, self.repo, self.portas, lider=lambda _n: 1 if self.lider else None,
                                recusa=triagem.recusa, redigir=triagem.redigir, canal=canal, dormir=dormir)

    async def volta(self, *updates: dict[str, object]) -> int:
        self.bot.guardadas.extend(updates)
        return await self.servico.uma_volta()

    def linha(self, update_id: int) -> dict[str, object]:
        r = self.db.one("SELECT * FROM canal_entradas WHERE canal='telegram' AND id_externo=?", (str(update_id),))
        assert r is not None
        return dict(r)


@pytest.fixture
def c(tmp_path: Path) -> Cenario:
    return Cenario(tmp_path)


async def test_offset_do_banco_ajuda_uma_vez_e_reinicio_sem_repetir(c: Cenario) -> None:
    assert await c.volta(msg(5, "/status"), msg(6, "/ajuda")) == 2
    gets = [p for m, p, _ in c.bot.chamadas if m == "getUpdates"]
    assert gets[0]["offset"] == "0" and "callback_query" in gets[0]["allowed_updates"]
    assert c.repo.proximo_offset() == 7
    assert c.bot.textos()[0].startswith("A Central agora atende por aqui.")       # a /ajuda da 1ª subida
    assert c.portas.nomes() == ["status"]
    # Reinício: outro serviço no mesmo banco pede a partir do 7; as updates 5 e 6 não voltam nem se repetem.
    c.servico = c.novo_servico()
    assert await c.volta() == 0
    assert [p for m, p, _ in c.bot.chamadas if m == "getUpdates"][-1]["offset"] == "7"
    assert c.portas.nomes() == ["status"]
    assert sum(1 for t in c.bot.textos() if t.startswith("A Central agora atende")) == 1


async def test_update_relida_nao_duplica(c: Cenario) -> None:
    await c.volta(msg(5, "/status"))
    # A mesma update chega de novo (queda antes de confirmar): o UNIQUE a engole e nada se repete.
    c.repo.gravar(id_externo="5", ordem=5, tipo="mensagem", do_dono=True, ref_mensagem="50", responde_a=None,
                  texto="/status", tamanho=7)
    await c.volta()
    assert c.db.scalar("SELECT COUNT(*) FROM canal_entradas WHERE canal='telegram' AND id_externo='5'") == 1
    assert c.portas.nomes() == ["status"]


async def test_outro_chat_fica_sem_texto_e_sem_resposta(c: Cenario) -> None:
    await c.volta(msg(5, "/status", chat=999))
    linha = c.linha(5)
    assert (linha["do_dono"], linha["texto"], linha["estado"]) == (0, None, "ignorada")
    assert c.portas.nomes() == []
    assert len(c.bot.mensagens()) == 1                                          # só a /ajuda


@pytest.mark.parametrize("texto", ["minha senha é Abc!2345xyz", "123456", "12 34 56", "/responder 4985a1 123456",
                                   "código de verificação: 884512"])
async def test_credencial_ou_codigo_e_recusado_e_nao_gravado(c: Cenario, texto: str,
                                                             caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    await c.volta(msg(5, texto))
    linha = c.linha(5)
    assert (linha["estado"], linha["texto"], linha["tamanho"]) == ("recusada", None, len(texto))
    assert RESPOSTA_CREDENCIAL in c.bot.textos()
    assert c.portas.nomes() == []
    # O contrato dos canais (§7): a mensagem some do chat, e a resposta não responde à mensagem apagada.
    apagadas = [b for m, _, b in c.bot.chamadas if m == "deleteMessage"]
    assert apagadas == [{"chat_id": str(CHAT), "message_id": 50}]
    assert linha["erro"] == "parece credencial ou código; apagada do chat"
    assert "reply_parameters" not in c.bot.mensagens()[-1]
    # Os logs da Central não levam o texto nem o token. (A linha INFO do `httpx` traz a URL com o token: em produção
    # `setup_logging` põe o `httpx` em WARNING e o `RedactingFilter` no handler; aqui ela é deixada de fora.)
    nossos = " | ".join(r.getMessage() for r in caplog.records if not r.name.startswith(("httpx", "httpcore")))
    assert texto not in nossos and TOKEN not in nossos


async def test_credencial_que_nao_se_apaga_pede_ao_dono_que_apague(c: Cenario) -> None:
    c.bot.apagar_falha = True
    await c.volta(msg(5, "minha senha é Abc!2345xyz"))
    linha = c.linha(5)
    assert (linha["estado"], linha["texto"], linha["erro"]) == ("recusada", None,
                                                               "parece credencial ou código; não apagada do chat")
    assert RESPOSTA_CREDENCIAL_SEM_APAGAR in c.bot.textos() and RESPOSTA_CREDENCIAL not in c.bot.textos()
    assert c.bot.mensagens()[-1]["reply_parameters"] == {"message_id": 50, "allow_sending_without_reply": True}
    # A releitura da mesma update (queda antes de confirmar) não tenta apagar nem responde de novo.
    canal = c.servico.canal()
    assert canal is not None
    enviadas = len(c.bot.mensagens())
    await c.servico.registrar(SaidaDoTelegram(canal), Recebida(id_externo="5", ordem=5, tipo="mensagem", do_dono=True,
                                                               texto="minha senha é Abc!2345xyz", ref_mensagem="50"))
    assert c.bot.chamou("deleteMessage") == 1 and len(c.bot.mensagens()) == enviadas


async def test_mensagem_longa_demais_nao_e_gravada(c: Cenario) -> None:
    await c.volta(msg(5, "abrir " + "x" * 1200))
    assert (c.linha(5)["estado"], c.linha(5)["texto"]) == ("recusada", None)
    assert any("longa demais" in t for t in c.bot.textos())


async def test_para_mostra_previa_com_botoes_e_executar_cria_uma_vez(c: Cenario) -> None:
    await c.volta(msg(5, "/para android-09 abrir o QA Messenger"))
    assert c.portas.chamadas[0][:2] == ("previa", ("abrir o QA Messenger no android-09", ()))
    linha = c.linha(5)
    assert linha["estado"] == "pergunta"
    previa = c.bot.mensagens()[-1]
    teclado = previa["reply_markup"]["inline_keyboard"][0]                       # type: ignore[index]
    assert [b["callback_data"] for b in teclado] == [f"x:{linha['id']}", f"c:{linha['id']}"]
    assert previa["reply_parameters"] == {"message_id": 50, "allow_sending_without_reply": True}
    mid_previa = c.bot.mid
    await c.volta(botao(6, f"x:{linha['id']}", mid=mid_previa))
    nome, args, operador = c.portas.chamadas[-1]
    assert (nome, args, operador) == ("criar", ("abrir o QA Messenger no android-09", "telegram:5"),
                                      OPERADOR_DO_TELEGRAM)
    linha = c.linha(5)
    assert (linha["estado"], linha["run_id"]) == ("feita", "r-20261003180000-abc123")
    assert any("Execução abc123 criada" in t for t in c.bot.textos())
    assert c.bot.chamou("answerCallbackQuery") == 1 and c.bot.chamou("editMessageReplyMarkup") == 1
    # O segundo toque no mesmo botão não cria de novo.
    await c.volta(botao(7, f"x:{linha['id']}", mid=mid_previa))
    assert c.portas.nomes().count("criar") == 1
    assert "Esta prévia já foi tratada." in c.bot.textos()


async def test_cancelar_nao_cria(c: Cenario) -> None:
    await c.volta(msg(5, "abra o Chrome no android-09"))
    ident = c.linha(5)["id"]
    await c.volta(botao(6, f"c:{ident}", mid=c.bot.mid))
    assert c.linha(5)["estado"] == "cancelada"
    assert "criar" not in c.portas.nomes()


async def test_sem_destino_pergunta_com_os_online_e_a_escolha_leva_a_previa(c: Cenario) -> None:
    c.portas.alvos = []
    await c.volta(msg(5, "abrir o QA Messenger"))
    ident = c.linha(5)["id"]
    pergunta = c.bot.mensagens()[-1]
    assert pergunta["text"] == "Para qual aparelho?"
    assert [b["callback_data"] for b in pergunta["reply_markup"]["inline_keyboard"][0]] == [  # type: ignore[index]
        f"a:{ident}:android-09", f"a:{ident}:android-10"]
    await c.volta(botao(6, f"a:{ident}:android-10", mid=c.bot.mid))
    assert c.portas.chamadas[-1][:2] == ("previa", ("abrir o QA Messenger", ("android-10",)))
    assert "Executar" in json.dumps(c.bot.mensagens()[-1]["reply_markup"])


async def test_recusa_do_pre_voo_volta_como_texto(c: Cenario) -> None:
    c.portas.recusar_criar = True
    await c.volta(msg(5, "abra o Chrome no android-09"))
    await c.volta(botao(6, f"x:{c.linha(5)['id']}", mid=c.bot.mid))
    assert c.linha(5)["estado"] == "falhou"
    assert any("Nenhum aparelho apto no pré-voo." in t for t in c.bot.textos())


async def test_reply_ao_aviso_de_aprovacao_decide_como_pessoa(c: Cenario) -> None:
    c.repo.registrar_enviada("555", "aviso", fato="approval:apr-0000aa11")
    await c.volta(msg(5, "sim", reply_to=555))
    assert c.portas.chamadas[-1] == ("decidir", ("apr-0000aa11", "approve"), OPERADOR_DO_TELEGRAM)
    assert c.linha(5)["alvo"] == "approval:apr-0000aa11"


async def test_aprovar_pelo_fim_do_id_e_ambiguidade(c: Cenario) -> None:
    await c.volta(msg(5, "/vetar bb22"), msg(6, "/aprovar aa11"))
    assert c.portas.chamadas[0][:2] == ("decidir", ("apr-0000bb22", "reject"))
    assert "decidir" not in c.portas.nomes()[1:]
    assert any("serve para mais de um" in t for t in c.bot.textos())


async def test_responder_cria_a_sucessora_e_reply_a_pergunta_tambem(c: Cenario) -> None:
    await c.volta(msg(5, "/responder 4985a1 Para o QA-001, texto oi"))
    assert c.portas.chamadas[-1][:2] == ("responder", ("r-20261002181523-4985a1", "Para o QA-001, texto oi"))
    assert c.linha(5)["run_id"] == "r-nova-000001"
    c.repo.registrar_enviada("777", "aviso", fato="run:r-20261002181523-4985a1:needs_input")
    await c.volta(msg(6, "QA-002", reply_to=777))
    assert c.portas.chamadas[-1][:2] == ("responder", ("r-20261002181523-4985a1", "QA-002"))


async def test_orquestradora_guardada_e_nao_executada(c: Cenario) -> None:
    await c.volta(msg(5, "/orq pode fazer o deploy"), msg(6, "status?", reply_to=31337))
    for uid in (5, 6):
        linha = c.linha(uid)
        assert (linha["estado"], linha["destino"]) == ("orquestradora", "orquestradora")
    assert c.portas.nomes() == []
    # Reply à própria mensagem da pessoa não é reply ao bot.
    await c.volta(msg(7, "/status", reply_to=50))
    assert c.linha(7)["estado"] == "feita"


async def test_limite_por_minuto(c: Cenario) -> None:
    c.cfg.file.avisos.entrada.limite_por_min = 3
    await c.volta(*[msg(10 + n, "/status") for n in range(5)])
    assert [c.linha(10 + n)["estado"] for n in range(5)] == ["feita"] * 3 + ["limitada"] * 2
    assert c.portas.nomes().count("status") == 3
    assert sum(1 for t in c.bot.textos() if "por minuto" in t) == 1


async def test_409_vira_problema_e_espera_sem_disputar(c: Cenario) -> None:
    c.bot.conflito = True
    assert await c.volta(msg(5, "/status")) == 0
    assert [p.code for p in c.servico.problemas()] == ["telegram_entrada_conflito"]
    assert c.dormidas == [c.cfg.file.avisos.entrada.espera_conflito_s]
    assert c.db.scalar("SELECT COUNT(*) FROM canal_entradas") == 0
    c.bot.conflito = False
    await c.volta()
    assert c.servico.problemas() == []
    assert c.portas.nomes() == ["status"]


async def test_desfecho_na_thread_uma_vez(c: Cenario) -> None:
    await c.volta(msg(5, "abra o Chrome no android-09"))
    await c.volta(botao(6, f"x:{c.linha(5)['id']}", mid=c.bot.mid))
    c.portas.desfechos["r-20261003180000-abc123"] = "Execução abc123: concluída.\nEvidência: Chrome aberto."
    await c.volta()
    await c.volta()
    finais = [m for m in c.bot.mensagens() if str(m["text"]).startswith("Execução abc123: concluída.")]
    assert len(finais) == 1 and finais[0]["reply_parameters"]["message_id"] == 50  # type: ignore[index]
    assert c.linha(5)["resultado_em"] is not None


async def test_sem_lideranca_ou_desligada_nao_le_o_bot(tmp_path: Path) -> None:
    for nome, kwargs in (("sem-lider", {"lider": False}), ("desligada", {"entrada": False})):
        c = Cenario(tmp_path / nome, **kwargs)                                       # type: ignore[arg-type]
        assert await c.volta(msg(5, "/status")) == 0
        assert c.bot.chamou("getUpdates") == 0 and c.bot.chamou("sendMessage") == 0


async def test_pendencias_lista_o_fim_do_id(c: Cenario) -> None:
    await c.volta(msg(5, "/pendencias"))
    texto = c.bot.textos()[-1]
    assert "- 00aa11 (aprovar)" in texto and "- 4985a1 (responder)" in texto


async def test_operador_e_valor_e_o_veto_leva_a_nota(c: Cenario) -> None:
    # A identidade é um VALOR do serviço (o 32.2 põe `trello:<id>`), e o veto com nota chega ao serviço do painel.
    c.servico.operador = "trello:abc123"
    c.repo.registrar_enviada("556", "aviso", fato="approval:apr-0000aa11")
    await c.volta(msg(5, "/vetar o tom ficou agressivo", reply_to=556))
    assert c.portas.chamadas[-1] == ("decidir", ("apr-0000aa11", "reject", "o tom ficou agressivo"), "trello:abc123")
