"""28.15 (ADR-071): a conversa de volta pelo Telegram — offset, chat, segredo, prévia com botões, reply ao aviso,
orquestradora, limite, 409 e desfecho na thread.

Prova `simulated`: Bot API falsa (`httpx.MockTransport`, que guarda as updates como o Telegram: só confirma o que
está abaixo do offset) e portas falsas no lugar dos serviços do painel; nenhuma rede. As portas reais estão em
`test_telegram_portas.py`, no harness.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from app.config import Config
from app.db import Database
from app.modules.avisos.adapters.telegram import CanalTelegram
from app.modules.avisos.application.entrada import RESPOSTA_IDENTIDADE
from app.modules.avisos.infrastructure.entrada import (
    ERRO_CURTO_DEMAIS,
    JANELA_DA_ESCOLHA_S,
    MINIMO_DO_PEDIDO,
    OPERADOR_DO_TELEGRAM,
    PRESA_S,
    RESPOSTA_CREDENCIAL,
    RESPOSTA_CREDENCIAL_SEM_APAGAR,
    RESPOSTA_CURTO_DEMAIS,
    RESPOSTA_ESCOLHA_AMBIGUA,
    RESPOSTA_ESCOLHA_CASADA,
    RESPOSTA_ESCOLHA_REPLY,
    RESPOSTA_FALHA_INTERNA,
    Pendencia,
    PlanoMudou,
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
        self.falha: tuple[int, dict[str, object]] | None = None     # a resposta de erro do getUpdates (401, 429...)
        self.envio_falha: list[int] = []        # o status de erro dos próximos sendMessage, um por envio (28.38)

    def handler(self, req: httpx.Request) -> httpx.Response:
        metodo = req.url.path.rsplit("/", 1)[-1]
        corpo = json.loads(req.content) if req.content else {}
        self.chamadas.append((metodo, dict(req.url.params), corpo))
        if metodo == "getUpdates":
            if self.falha is not None:
                return httpx.Response(self.falha[0], json=self.falha[1])
            if self.conflito:
                return httpx.Response(409, json={"ok": False, "description": "Conflict: terminated by other getUpdates"})
            offset = int(req.url.params.get("offset", "0"))
            if offset < 0:      # como o Telegram: devolve só as últimas e ESQUECE as anteriores
                self.guardadas = self.guardadas[offset:]
                return httpx.Response(200, json={"ok": True, "result": list(self.guardadas)})
            self.guardadas = [u for u in self.guardadas if int(str(u["update_id"])) >= offset]
            return httpx.Response(200, json={"ok": True, "result": self.guardadas[:50]})
        if metodo == "sendMessage":
            if self.envio_falha:
                return httpx.Response(self.envio_falha.pop(0), json={"ok": False, "description": "falha de teste"})
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


def msg(uid: int, texto: str, *, chat: int = CHAT, mid: int | None = None, reply_to: int | None = None,
        tipo: str = "private", autor: int | None = None) -> dict[str, object]:
    m: dict[str, object] = {"message_id": mid or uid * 10, "chat": {"id": chat, "type": tipo}, "text": texto,
                            "from": {"id": chat if autor is None else autor}}
    if reply_to is not None:
        m["reply_to_message"] = {"message_id": reply_to}
    return {"update_id": uid, "message": m}


def botao(uid: int, data: str, *, mid: int, chat: int = CHAT, autor: int | None = None,
          tipo: str = "private") -> dict[str, object]:
    return {"update_id": uid, "callback_query": {"id": f"cb{uid}", "data": data,
                                                 "from": {"id": chat if autor is None else autor},
                                                 "message": {"message_id": mid, "chat": {"id": chat, "type": tipo}}}}


class PortasFalsas:
    def __init__(self) -> None:
        self.chamadas: list[tuple[str, tuple[object, ...], str | None]] = []
        self.alvos: list[dict[str, object]] = [{"instance_id": "android-09", "profile_id": None, "origem": "texto"}]
        self.perguntas: list[str] = []
        self.recusar_criar = False
        self.desfechos: dict[str, str] = {}
        self.sem_gesto: list[str] = []          # os cancelamentos que ninguém fez (a faxina do plano esquecido, 28.38)
        self.pergunta = "Para qual contato?"        # o que a execução em needs_input pergunta (B2)
        self.sensivel_aberta = False                # há execução esperando senha/código/2FA/token (palavra solta)
        self.sensivel_quebra = False
        self.recusar_previa: str | None = None      # 28.28: o texto da recusa da prévia (o "sem destino" do extrator)
        self.personas: list[str] = []               # 28.28: os nomes que a conversa tira do que manda
        # 28.27: a execução do Executar nasce só de plano; o vigia lê o estado e a prévia da porta.
        self.estados: dict[str, str | None] = {}    # run_id → status (sem entrada: `running`)
        self.previa_da_porta: dict[str, object] = {"itens": [], "total": False, "parcial": False}
        self.porta_quebra = False
        self.mudou: list[dict[str, object]] | None = None     # o próximo `aprovar_plano` dá 409 `plano_mudou`
        self.recusar_aprovar: str | None = None
        self.vistas_em: list[str | None] = []       # G1b: o `vista_em` de cada `aprovar_plano`, na ordem
        self.imagens: dict[str, tuple[bytes, str]] = {}
        self.estado_ao_criar = "planned"            # o status da execução só de plano logo depois de criada
        self.criada_com: dict[str, str] = {}        # chave de idempotência → run_id (a linha presa antes do run_id)

    def nomes_de_persona(self) -> list[str]:
        return list(self.personas)

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

    def ids_de_aprovacoes(self) -> list[str]:
        return [*self.aprovacoes_pendentes(), "apr-0000cc33"]       # cc33 já foi decidida

    def decidir(self, approval_id: str, verbo: str, nota: str | None = None) -> str:
        self._anota("decidir", approval_id, verbo) if nota is None else self._anota("decidir", approval_id, verbo, nota)
        return "Aprovado: a execução segue." if verbo == "approve" else "Vetado: nada é enviado."

    def responder(self, run_id: str, texto: str) -> tuple[str, str]:
        self._anota("responder", run_id, texto)
        return "r-nova-000001", "000001"

    def previa(self, texto: str, instance_ids: list[str] | None = None) -> Previa:
        self._anota("previa", texto, tuple(instance_ids or ()))
        if self.recusar_previa:
            raise RecusaDaCentral(self.recusar_previa)
        if instance_ids:
            return Previa(alvos=[{"instance_id": instance_ids[0], "profile_id": None, "origem": "ui"}], perguntas=[],
                          comando=texto)
        return Previa(alvos=list(self.alvos), perguntas=list(self.perguntas), comando=texto)

    def criar(self, texto: str, alvos: list[dict[str, object]], chave: str, modo: str = "execute") -> tuple[str, str]:
        self._anota("criar", texto, chave)
        self.modo_criado = modo
        if self.recusar_criar:
            raise RecusaDaCentral("Nenhum aparelho apto no pré-voo.")
        self.estados["r-20261003180000-abc123"] = self.estado_ao_criar if modo == "plan" else "running"
        return "r-20261003180000-abc123", "abc123"

    def estado_da_execucao(self, run_id: str) -> str | None:
        return self.estados.get(run_id, "running")

    def execucao_da_chave(self, chave: str) -> str | None:
        self._anota("execucao_da_chave", chave)
        return self.criada_com.get(chave)

    def porta(self, run_id: str) -> dict[str, object]:
        self._anota("porta", run_id)
        if self.porta_quebra:
            raise RecusaDaCentral("sem plano")
        return dict(self.previa_da_porta)

    def aprovar_plano(self, run_id: str, aprovar: list[tuple[str, str]], *,
                      vista_em: str | None = None) -> dict[str, object]:
        self._anota("aprovar_plano", run_id, tuple(aprovar))
        self.vistas_em.append(vista_em)
        if self.mudou is not None:
            mudaram, self.mudou = self.mudou, None
            raise PlanoMudou("mudou", dict(self.previa_da_porta), mudaram)
        if self.recusar_aprovar:
            raise RecusaDaCentral(self.recusar_aprovar, "invalid_state")
        self.estados[run_id] = "running"
        return {"run": {"id": run_id}, "aprovacoes": [f"apr-{i}" for i, _ in enumerate(aprovar)], "tiradas": [],
                "validade_ate": "2026-10-05T22:40:00Z"}

    def iniciar(self, run_id: str) -> None:
        self._anota("iniciar", run_id)
        self.estados[run_id] = "running"

    def cancelar(self, run_id: str, *, gesto: bool = True) -> None:
        self._anota("cancelar", run_id)
        if not gesto:
            self.sem_gesto.append(run_id)
        self.estados[run_id] = "cancelled"

    def imagem_da_etapa(self, run_id: str, step_id: str) -> tuple[bytes, str] | None:
        return self.imagens.get(step_id)

    def online(self) -> list[str]:
        return ["android-09", "android-10"]

    def desfecho(self, run_id: str) -> str | None:
        return self.desfechos.get(run_id)

    def pergunta_sensivel(self, ref: str | None) -> str | None:
        # Com `ref`, o tipo vem da MESMA regra do caminho comum (29.52) sobre a pergunta do cenário; sem, o interruptor.
        self._anota("pergunta_sensivel", ref)
        if ref is not None:
            return TriagemDeCredencial().pergunta_sensivel(self.pergunta)
        if self.sensivel_quebra:
            raise RuntimeError("banco fora")
        return "senha" if self.sensivel_aberta else None


class Cenario:
    def __init__(self, tmp_path: Path, *, entrada: bool = True, lider: bool = True, base: bool = True,
                 relogio: Callable[[], datetime] | None = None) -> None:
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
        self.repo = EntradasDoCanal(self.db, relogio=relogio)
        if base:
            # Canal já em uso: a 1ª subida (que descarta o histórico do Telegram) já passou. Os testes de B1 passam
            # `base=False` para ver essa subida.
            self.repo.gravar_inicio(None)
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
    assert c.bot.textos()[0].startswith("Oi! Sou a ANA, a IA Gerente")   # a /ajuda da 1ª subida, com a apresentação
    assert c.portas.nomes() == ["status"]
    # Reinício: outro serviço no mesmo banco pede a partir do 7; as updates 5 e 6 não voltam nem se repetem.
    c.servico = c.novo_servico()
    assert await c.volta() == 0
    assert [p for m, p, _ in c.bot.chamadas if m == "getUpdates"][-1]["offset"] == "7"
    assert c.portas.nomes() == ["status"]
    assert sum(1 for t in c.bot.textos() if t.startswith("Oi! Sou a ANA")) == 1


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
    await c.volta(msg(5, "abrir " + "o QA Messenger " * 80))      # longo, mas com cara de texto
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
    nome, args, operador = next(x for x in c.portas.chamadas if x[0] == "criar")
    assert (nome, args, operador) == ("criar", ("abrir o QA Messenger no android-09", "telegram:5"),
                                      OPERADOR_DO_TELEGRAM)
    assert c.portas.modo_criado == "plan"
    # 28.27: nada pede o sim do dono no plano (N = 0): o vigia inicia com `aprovar=[]` na mesma volta, uma linha só.
    assert c.portas.chamadas[-1] == ("aprovar_plano", ("r-20261003180000-abc123", ()), OPERADOR_DO_TELEGRAM)
    linha = c.linha(5)
    assert (linha["estado"], linha["run_id"]) == ("feita", "r-20261003180000-abc123")
    assert any(t.startswith("Execução abc123 iniciada: o plano não tem aprovação pendente.") for t in c.bot.textos())
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


async def test_reply_com_id_de_outra_pendencia_nao_decide_nada(c: Cenario) -> None:
    # 28.26: o reply é ao aviso do aa11 e o id digitado é o do bb22. Antes, decidia o aa11 com "bb22" de nota.
    c.repo.registrar_enviada("555", "aviso", fato="approval:apr-0000aa11")
    await c.volta(msg(5, "/aprovar bb22", reply_to=555))
    assert "decidir" not in c.portas.nomes()
    assert c.linha(5)["estado"] == "feita"
    resposta = c.bot.textos()[-1]
    assert "nada foi decidido" in resposta and "aa11" in resposta and "bb22" in resposta


async def test_reply_com_id_de_aprovacao_decidida_ou_de_pergunta_nao_decide(c: Cenario) -> None:
    # Revisão da suíte 31: só as PENDENTES eram conferidas, e o id de uma já decidida ou de uma execução esperando
    # resposta virava nota e decidia o aviso respondido.
    c.repo.registrar_enviada("555", "aviso", fato="approval:apr-0000aa11")
    await c.volta(msg(5, "/aprovar cc33", reply_to=555), msg(6, "/vetar 4985a1", reply_to=555))
    assert "decidir" not in c.portas.nomes()
    assert all("nada foi decidido" in t for t in c.bot.textos()[-2:])


async def test_reply_com_pedaco_curto_de_id_nao_decide_mas_nota_curta_segue(c: Cenario) -> None:
    c.repo.registrar_enviada("555", "aviso", fato="approval:apr-0000aa11")
    await c.volta(msg(5, "/aprovar a1f", reply_to=555))
    assert "decidir" not in c.portas.nomes()
    assert "curto demais" in c.bot.textos()[-1]
    await c.volta(msg(6, "/aprovar ok pode ir", reply_to=555))
    assert c.portas.chamadas[-1] == ("decidir", ("apr-0000aa11", "approve", "ok pode ir"), OPERADOR_DO_TELEGRAM)


async def test_reply_com_o_mesmo_id_decide_e_o_id_sai_da_nota(c: Cenario) -> None:
    c.repo.registrar_enviada("555", "aviso", fato="approval:apr-0000bb22")
    await c.volta(msg(5, "/vetar bb22 o tom ficou agressivo", reply_to=555))
    assert c.portas.chamadas[-1] == ("decidir", ("apr-0000bb22", "reject", "o tom ficou agressivo"), OPERADOR_DO_TELEGRAM)


async def test_reply_com_nota_que_nao_e_id_segue_como_antes(c: Cenario) -> None:
    c.repo.registrar_enviada("555", "aviso", fato="approval:apr-0000aa11")
    await c.volta(msg(5, "/aprovar pode seguir assim", reply_to=555))
    assert c.portas.chamadas[-1] == ("decidir", ("apr-0000aa11", "approve", "pode seguir assim"), OPERADOR_DO_TELEGRAM)


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
    # só a leitura de pergunta sensível aberta (o recado curto, E6); nada é executado
    assert set(c.portas.nomes()) <= {"pergunta_sensivel"}
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
    assert c.db.scalar("SELECT COUNT(*) FROM canal_entradas WHERE id_externo<>'inicio'") == 0
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


async def test_desfecho_que_nao_saiu_tenta_de_novo_na_volta_seguinte(c: Cenario) -> None:
    """28.38: na execução terminal o desfecho pode ser a única linha ao dono (28.36). A falha passageira do envio não o
    marca: a volta seguinte manda de novo. A definitiva marca (repetir não adianta) e não repete."""
    await c.volta(msg(5, "abra o Chrome no android-09"))
    await c.volta(botao(6, f"x:{c.linha(5)['id']}", mid=c.bot.mid))
    c.portas.desfechos[RUN] = "Execução abc123: concluída."
    c.bot.envio_falha = [502]
    await c.volta()
    assert c.linha(5)["resultado_em"] is None
    await c.volta()
    assert c.linha(5)["resultado_em"] is not None
    await c.volta()
    assert c.bot.textos().count("Execução abc123: concluída.") == 2      # a tentativa que falhou e a que saiu


async def test_desfecho_com_429_espera_o_que_o_telegram_pediu(tmp_path: Path) -> None:
    """28.38: o 429 traz o `retry_after`; as voltas no meio não reenviam o desfecho (nem a ida ao plano esquecido)."""
    agora = [1_000_000.0]
    c = Cenario(tmp_path)
    c.servico.conversa._agora = lambda: agora[0]                 # noqa: SLF001
    await c.volta(msg(5, "abra o Chrome no android-09"))
    await c.volta(botao(6, f"x:{c.linha(5)['id']}", mid=c.bot.mid))
    c.portas.desfechos[RUN] = "Execução abc123: concluída."
    c.bot.envio_falha = [429]
    await c.volta()
    n = c.bot.chamou("sendMessage")
    agora[0] += 10
    await c.volta()
    assert c.bot.chamou("sendMessage") == n and c.linha(5)["resultado_em"] is None
    agora[0] += 30
    await c.volta()
    assert c.linha(5)["resultado_em"] is not None


async def test_desfecho_com_falha_definitiva_marca_e_nao_repete(c: Cenario) -> None:
    await c.volta(msg(5, "abra o Chrome no android-09"))
    await c.volta(botao(6, f"x:{c.linha(5)['id']}", mid=c.bot.mid))
    c.portas.desfechos[RUN] = "Execução abc123: concluída."
    c.bot.envio_falha = [403]
    await c.volta()
    assert c.linha(5)["resultado_em"] is not None
    n = c.bot.chamou("sendMessage")
    await c.volta()
    assert c.bot.chamou("sendMessage") == n


def _envelhecer_a_vista(c: Cenario, update_id: int, segundos: float) -> None:
    """A primeira vista em `planned` gravada na linha (28.39, G1), puxada para trás no relógio do banco."""
    from app.modules.avisos.infrastructure.entrada_sql import VISTA_EM_PLANNED
    from app.util import to_iso

    linha = c.linha(update_id)
    previa = json.loads(str(linha["previa"] or "{}"))
    assert VISTA_EM_PLANNED in previa
    previa[VISTA_EM_PLANNED] = to_iso(c.repo.relogio() - timedelta(seconds=segundos))
    c.db.execute("UPDATE canal_entradas SET previa=? WHERE id=?", (json.dumps(previa), linha["id"]))


async def test_plano_esquecido_com_a_linha_feita_e_cancelado(c: Cenario) -> None:
    """28.38: a linha ficou feita e a execução voltou a `planned` (o `planning` de uma prévia termina ali) sem ninguém
    iniciar nem cancelar. A hora conta de quando a conversa a VIU em `planned` (F1: a linha fica feita ainda em
    `planning`), e essa vista fica na linha (28.39, G1); passada a hora, é cancelada SEM gesto (F4), e o desfecho fecha a
    linha."""
    from app.modules.avisos.infrastructure.entrada import PLANO_ESQUECIDO_S
    from app.util import to_iso

    await c.volta(msg(5, "abra o Chrome no android-09"))
    await c.volta(botao(6, f"x:{c.linha(5)['id']}", mid=c.bot.mid))
    assert c.linha(5)["estado"] == "feita"
    c.portas.estados[RUN] = "planned"
    await c.volta()
    assert "cancelar" not in c.portas.nomes()                     # a linha é nova: nem lê o estado
    velha = to_iso(c.repo.relogio() - timedelta(seconds=PLANO_ESQUECIDO_S + 60))
    c.db.execute("UPDATE canal_entradas SET tratada_em=? WHERE id=?", (velha, c.linha(5)["id"]))
    await c.volta()
    assert "cancelar" not in c.portas.nomes()                     # a linha é velha, mas `planned` acabou de ser visto
    _envelhecer_a_vista(c, 5, PLANO_ESQUECIDO_S - 60)
    await c.volta()
    assert "cancelar" not in c.portas.nomes()
    _envelhecer_a_vista(c, 5, PLANO_ESQUECIDO_S + 60)
    await c.volta()
    assert c.portas.chamadas[-1][:2] == ("cancelar", (RUN,)) and c.portas.estados[RUN] == "cancelled"
    assert c.portas.sem_gesto == [RUN]
    c.portas.desfechos[RUN] = "Execução abc123: cancelada."
    await c.volta()
    assert c.bot.textos()[-1] == "Execução abc123: cancelada." and c.linha(5)["resultado_em"] is not None


async def test_a_vista_em_planned_sobrevive_ao_reinicio(c: Cenario) -> None:
    """28.39, G1: um processo novo (ou outro líder da trava `avisos`) lê a mesma vista na linha e não recomeça a hora."""
    from app.modules.avisos.infrastructure.entrada import PLANO_ESQUECIDO_S
    from app.util import to_iso

    await c.volta(msg(5, "abra o Chrome no android-09"))
    await c.volta(botao(6, f"x:{c.linha(5)['id']}", mid=c.bot.mid))
    c.portas.estados[RUN] = "planned"
    velha = to_iso(c.repo.relogio() - timedelta(seconds=PLANO_ESQUECIDO_S + 60))
    c.db.execute("UPDATE canal_entradas SET tratada_em=? WHERE id=?", (velha, c.linha(5)["id"]))
    await c.volta()                                               # grava a vista
    _envelhecer_a_vista(c, 5, PLANO_ESQUECIDO_S + 60)
    c.servico = c.novo_servico()                                  # o processo reiniciou: nada em memória
    await c.volta()
    assert c.portas.estados[RUN] == "cancelled" and c.portas.sem_gesto == [RUN]


async def test_a_vista_some_quando_a_execucao_sai_de_planned(c: Cenario) -> None:
    from app.modules.avisos.infrastructure.entrada import PLANO_ESQUECIDO_S
    from app.modules.avisos.infrastructure.entrada_sql import VISTA_EM_PLANNED
    from app.util import to_iso

    await c.volta(msg(5, "abra o Chrome no android-09"))
    await c.volta(botao(6, f"x:{c.linha(5)['id']}", mid=c.bot.mid))
    c.portas.estados[RUN] = "planned"
    velha = to_iso(c.repo.relogio() - timedelta(seconds=PLANO_ESQUECIDO_S + 60))
    c.db.execute("UPDATE canal_entradas SET tratada_em=? WHERE id=?", (velha, c.linha(5)["id"]))
    await c.volta()
    assert VISTA_EM_PLANNED in json.loads(str(c.linha(5)["previa"]))
    c.portas.estados[RUN] = "running"
    await c.volta()
    assert VISTA_EM_PLANNED not in json.loads(str(c.linha(5)["previa"] or "{}"))
    assert "cancelar" not in c.portas.nomes()


async def test_lote_de_desfechos_gira_e_a_linha_nova_nao_espera_para_sempre(c: Cenario) -> None:
    """28.39 (F3 da revisão do #358): com o lote cheio de linhas de execução longa, a linha nova entra na volta
    seguinte, e não depois que as antigas acabarem."""
    from app.modules.avisos.infrastructure.entrada_sql import EntradasDoCanal

    lote = EntradasDoCanal.LOTE_DESFECHO
    agora = c.repo._agora()                                       # noqa: SLF001 - o ISO do relógio do banco
    for n in range(lote):
        c.db.execute("INSERT INTO canal_entradas(canal, id_externo, tipo, ref_mensagem, estado, run_id, do_dono,"
                     " recebida_em, tratada_em) VALUES ('telegram', ?, 'mensagem', ?, 'feita', ?, 1, ?, ?)",
                     (f"longa-{n}", str(9000 + n), f"r-longa-{n:04d}", agora, agora))
    c.db.execute("INSERT INTO canal_entradas(canal, id_externo, tipo, ref_mensagem, estado, run_id, do_dono,"
                 " recebida_em, tratada_em) VALUES ('telegram', 'nova', 'mensagem', '9999', 'feita', 'r-nova-0001', 1,"
                 " ?, ?)", (agora, agora))
    c.portas.desfechos["r-nova-0001"] = "Execução 000001: concluída."
    await c.volta()
    assert "Execução 000001: concluída." not in c.bot.textos()   # o lote desta volta é o das 20 antigas
    await c.volta()
    assert "Execução 000001: concluída." in c.bot.textos()


async def test_desfecho_sem_id_do_canal_fica_registrado_e_nao_repete(c: Cenario) -> None:
    """28.39: o canal aceitou, mas a resposta não trouxe o `message_id`. O desfecho fica registrado por uma referência
    própria, e a marca que não gravou não o faz repetir."""
    await c.volta(msg(5, "abra o Chrome no android-09"))
    await c.volta(botao(6, f"x:{c.linha(5)['id']}", mid=c.bot.mid))
    ident = int(str(c.linha(5)["id"]))
    c.repo.registrar_enviada(None, "resultado", entrada_id=ident)
    assert c.repo.desfecho_ja_enviado(ident)
    c.repo.registrar_enviada(None, "resposta", entrada_id=ident)     # só o desfecho ganha a referência própria
    assert c.db.scalar("SELECT COUNT(*) FROM canal_enviadas WHERE entrada_id=? AND ref_mensagem LIKE 'resultado:%'",
                       (ident,)) == 1


async def test_desfecho_responde_mesmo_com_a_mensagem_original_apagada(c: Cenario) -> None:
    """28.39: o dono apagou a mensagem que pediu a execução. O desfecho vai em resposta a ela com
    `allow_sending_without_reply`, então o Telegram não recusa com 400 e não há o que tentar sem a referência."""
    await c.volta(msg(5, "abra o Chrome no android-09"))
    await c.volta(botao(6, f"x:{c.linha(5)['id']}", mid=c.bot.mid))
    c.portas.desfechos[RUN] = "Execução abc123: concluída."
    await c.volta()
    [final] = [m for m in c.bot.mensagens() if m["text"] == "Execução abc123: concluída."]
    assert final["reply_parameters"] == {"message_id": 50, "allow_sending_without_reply": True}


async def test_desfecho_que_ja_saiu_e_ficou_registrado_nao_repete(c: Cenario) -> None:
    """Revisão do #358, F2: o desfecho saiu e ficou em `canal_enviadas`, mas a marca não gravou (o banco caiu entre
    os dois). A volta seguinte marca sem mandar de novo."""
    await c.volta(msg(5, "abra o Chrome no android-09"))
    await c.volta(botao(6, f"x:{c.linha(5)['id']}", mid=c.bot.mid))
    c.repo.registrar_enviada("999", "resultado", entrada_id=int(str(c.linha(5)["id"])))
    c.portas.desfechos[RUN] = "Execução abc123: concluída."
    n = c.bot.chamou("sendMessage")
    await c.volta()
    assert c.bot.chamou("sendMessage") == n and c.linha(5)["resultado_em"] is not None


async def test_execucao_em_andamento_que_nao_tem_desfecho_nao_e_cancelada(c: Cenario) -> None:
    """Só a `planned` esquecida: a que roda há horas segue (o `_cancelar_plano` confere o estado)."""
    from app.modules.avisos.infrastructure.entrada import PLANO_ESQUECIDO_S
    from app.util import to_iso

    await c.volta(msg(5, "abra o Chrome no android-09"))
    await c.volta(botao(6, f"x:{c.linha(5)['id']}", mid=c.bot.mid))
    velha = to_iso(c.repo.relogio() - timedelta(seconds=PLANO_ESQUECIDO_S + 60))
    c.db.execute("UPDATE canal_entradas SET tratada_em=? WHERE id=?", (velha, c.linha(5)["id"]))
    await c.volta()
    assert "cancelar" not in c.portas.nomes() and c.portas.estados[RUN] == "running"


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


async def test_quem_e_voce_responde_que_e_a_ana_e_que_e_ia_sem_previa(c: Cenario) -> None:
    """28.17: a pergunta pela identidade não vira pedido (nada vai à prévia) e a resposta diz que é uma IA."""
    await c.volta(msg(5, "quem é você?"), msg(6, "/quem"))
    assert c.portas.nomes() == []
    assert c.bot.textos()[-2:] == [RESPOSTA_IDENTIDADE] * 2
    assert "uma IA, não uma pessoa" in RESPOSTA_IDENTIDADE and RESPOSTA_IDENTIDADE.startswith("Sou a ANA, a IA")
    assert c.linha(5)["intencao"] == "identidade" and c.linha(5)["estado"] == "feita"


# ---------------------------------------------------------------- 28.28: pergunta ao bot não vira comando de aparelho
SEM_DESTINO = 'Diga onde ou por quem: escolha aparelhos, personas, ou cite no comando ("com a persona André", "no android-03").'


async def test_pergunta_solta_do_dono_vai_a_orquestradora_e_nao_a_previa(c: Cenario) -> None:
    # A mensagem literal do dono (entrada 889, 04/10 18:19Z): virava texto livre e a prévia pedia destino.
    c.portas.recusar_previa = SEM_DESTINO
    await c.volta(msg(5, "porque tem tanta coisa represada em validação?"))
    assert "previa" not in c.portas.nomes()
    assert (c.linha(5)["estado"], c.linha(5)["destino"]) == ("orquestradora", "orquestradora")
    assert c.bot.textos()[-1] == "Recebi sua pergunta: a resposta vem por aqui, em resposta a esta mensagem."


async def test_reply_a_resposta_do_repasse_continua_a_conversa(c: Cenario) -> None:
    # O "no trello" do dono (entrada 891) foi reply à NOSSA resposta: junta à pergunta e não vira pedido novo.
    c.portas.recusar_previa = SEM_DESTINO
    await c.volta(msg(5, "porque tem tanta coisa represada em validação?"))
    resposta = c.repo.db.scalar("SELECT ref_mensagem FROM canal_enviadas WHERE entrada_id=?", (c.linha(5)["id"],))
    await c.volta(msg(6, "no trello", reply_to=int(resposta)))
    assert "previa" not in c.portas.nomes()
    assert c.linha(6)["estado"] == "orquestradora"
    assert json.loads(c.linha(6)["previa"])["texto"] == "porque tem tanta coisa represada em validação? — no trello"
    assert "anterior" in c.bot.textos()[-1]


async def test_texto_livre_sem_destino_recusado_vai_a_orquestradora_sem_o_texto_do_extrator(c: Cenario) -> None:
    c.portas.recusar_previa = SEM_DESTINO
    await c.volta(msg(5, "ver o andamento das coisas"))
    assert c.linha(5)["estado"] == "orquestradora"
    assert "Diga onde ou por quem" not in c.bot.textos()[-1]
    assert "no android-12" in c.bot.textos()[-1]


@pytest.mark.parametrize("texto", ["1", "ok"])
async def test_recado_curto_demais_fica_recusado_e_nao_vira_erro_interno(c: Cenario, texto: str) -> None:
    """28.43 (o "1" solto do dono, 05/10 10:26Z): o texto livre mais curto que o pedido aceita não chega à prévia.
    A linha fica `recusada` (não `falhou`), e a resposta diz o que fazer, sem "falhou" nem "erro aqui dentro"."""
    assert MINIMO_DO_PEDIDO == 3 and len(texto) < MINIMO_DO_PEDIDO
    await c.volta(msg(5, texto))
    assert "previa" not in c.portas.nomes()
    linha = c.linha(5)
    assert (linha["estado"], linha["erro"]) == ("recusada", ERRO_CURTO_DEMAIS)
    assert c.bot.textos()[-1] == RESPOSTA_CURTO_DEMAIS != RESPOSTA_FALHA_INTERNA
    assert "falhou" not in RESPOSTA_CURTO_DEMAIS and "erro" not in RESPOSTA_CURTO_DEMAIS
    # A volta das recusas sem resposta só repete as de credencial ou de pergunta: esta não volta.
    assert c.repo.recusadas_sem_resposta(3600) == []


async def test_pedido_com_aparelho_segue_para_a_previa_mesmo_com_interrogacao(c: Cenario) -> None:
    # Contraprova: citar o aparelho faz da frase um pedido.
    await c.volta(msg(5, "pode abrir o QA Messenger no android-09?"))
    assert c.portas.chamadas[-1][0] == "previa"
    assert "Executar" in json.dumps(c.bot.mensagens()[-1]["reply_markup"])


async def test_quem_e_voce_segue_identidade(c: Cenario) -> None:
    await c.volta(msg(5, "quem é você?"))
    assert "previa" not in c.portas.nomes()
    assert c.linha(5)["estado"] == "feita"


async def test_nenhuma_resposta_do_canal_sai_com_nome_de_persona(c: Cenario) -> None:
    # Varre as recusas: o texto vem do serviço compartilhado com o painel e pode trazer o nome (o exemplo do extrator).
    c.portas.personas = ["André", "andre.qa", "Bruno Lima"]
    c.portas.recusar_criar = True
    c.portas.alvos = [{"instance_id": "android-09", "profile_id": None, "origem": "texto"}]
    c.portas.perguntas = []
    await c.volta(msg(5, "/para android-09: comente oi com a persona André e @andre.qa"))
    await c.volta(botao(6, f"x:{c.linha(5)['id']}", mid=c.bot.mid))
    c.portas.recusar_previa = "Recusado: Bruno Lima e ANDRE não podem agir aqui"
    await c.volta(msg(7, "/para android-10: postar"))
    for texto in c.bot.textos():
        normal = texto.lower()
        assert "andré" not in normal and "andre" not in normal and "bruno lima" not in normal, texto
    assert any("<persona>" in t for t in c.bot.textos())


async def test_sem_nome_de_persona_troca_palavra_inteira_sem_acento_e_poupa_a_ana() -> None:
    from app.modules.avisos.infrastructure.entrada import sem_nome_de_persona
    nomes = ["André", "@andre.qa", "Ana", "Bruno Lima", "Li"]
    assert sem_nome_de_persona('cite "com a persona ANDRE" ou @andre.qa', nomes) == 'cite "com a persona <persona>" ou <persona>'
    assert sem_nome_de_persona("Bruno Lima respondeu; a ANA viu", nomes) == "<persona> respondeu; a ANA viu"
    # Contraprova: pedaço de palavra não é nome ("Andressa", "Lista"), e nome curto demais (< 3) não entra.
    assert sem_nome_de_persona("Andressa olhou a Lista", nomes) == "Andressa olhou a Lista"


# ===================================================================== 28.27: a porta do plano pelo canal
RUN = "r-20261003180000-abc123"


def _item(sid: str, selo: str = "aprovacao", texto: str | None = "oi, tudo bem?", **kw: object) -> dict[str, object]:
    base: dict[str, object] = {"step_id": sid, "selo": selo, "chave": f"{sid:0<64}" if selo == "aprovacao" else None,
                               "titulo": "Comentar no post", "acao": "COMMENT", "alvo": "post 123", "texto": texto,
                               "motivo": "", "tem_imagem": False, "imagem_sha256": None, "aparelho": "android-09"}
    base.update(kw)
    return base


def _porta(*itens: dict[str, object], validade: str = "2099-10-05T22:40:00Z", **kw: object) -> dict[str, object]:
    return {"itens": list(itens), "hash_do_plano": "h" * 64, "validade_ate": validade, "parcial": False,
            "total": False, **kw}


def _marca(c: Cenario) -> str:
    return str(json.loads(str(c.linha(5)["previa"]))["marca"])


def _p(c: Cenario, ident: int) -> str:
    """O "Executar (aprova N)" da prévia ATUAL: leva a marca do retrato gravado."""
    return f"p:{ident}:{_marca(c)}"


async def _executar(c: Cenario, texto: str = "abra o Chrome no android-09") -> int:
    await c.volta(msg(5, texto))
    ident = int(c.linha(5)["id"])
    await c.volta(botao(6, f"x:{ident}", mid=c.bot.mid))
    return ident


async def test_porta_com_sim_pendente_mostra_a_previa_e_so_inicia_no_segundo_toque(c: Cenario) -> None:
    c.portas.previa_da_porta = _porta(_item("s1"), _item("s2", selo="permitido", texto=None))
    ident = await _executar(c)
    assert "aprovar_plano" not in c.portas.nomes() and c.portas.estados[RUN] == "planned"
    linha = c.linha(5)
    previa = json.loads(str(linha["previa"]))
    assert (linha["estado"], previa["fase"], previa["aprovar"]) == ("pergunta", "porta", [["s1", f"{'s1':0<64}"]])
    ultima = c.bot.mensagens()[-1]
    assert [b["callback_data"] for b in ultima["reply_markup"]["inline_keyboard"][0]] == [  # type: ignore[index]
        f"p:{ident}:{_marca(c)}", f"c:{ident}"]
    assert ultima["reply_markup"]["inline_keyboard"][0][0]["text"] == "Executar (aprova 1)"  # type: ignore[index]
    assert str(ultima["text"]).startswith("Plano abc123: 1 item pede o seu sim antes de começar.")
    await c.volta(botao(7, _p(c, ident), mid=c.bot.mid))
    # Exatamente os pares do retrato (o que o dono VIU), pelo operador do canal.
    assert c.portas.chamadas[-1] == ("aprovar_plano", (RUN, (("s1", f"{'s1':0<64}"),)), OPERADOR_DO_TELEGRAM)
    assert c.linha(5)["estado"] == "feita"
    assert "Execução abc123 iniciada; 1 sim gravado, válidos até 22:40Z. Conto aqui quando terminar." in c.bot.textos()
    await c.volta(botao(8, _p(c, ident), mid=c.bot.mid))
    assert c.portas.nomes().count("aprovar_plano") == 1 and "Esta prévia já foi tratada." in c.bot.textos()


async def test_sem_sim_pendente_inicia_com_um_toque_e_uma_linha(c: Cenario) -> None:
    c.portas.previa_da_porta = _porta(_item("s1", selo="permitido"), _item("s2", selo="na_execucao"), total=True)
    await _executar(c)
    assert c.portas.chamadas[-1] == ("aprovar_plano", (RUN, ()), OPERADOR_DO_TELEGRAM)
    assert c.linha(5)["estado"] == "feita"
    assert c.bot.textos()[-1] == ("Execução abc123 iniciada: o plano não tem aprovação pendente agora; 1 item vai "
                                  "pedir você na execução. Conto aqui quando terminar.")


async def test_plano_ainda_sendo_feito_espera(c: Cenario) -> None:
    c.portas.estado_ao_criar = "planning"
    await _executar(c)
    assert "porta" not in c.portas.nomes() and c.linha(5)["estado"] == "executando"
    c.portas.estados[RUN] = "planned"
    await c.volta()
    assert c.portas.chamadas[-1][0] == "aprovar_plano" and c.linha(5)["estado"] == "feita"


async def test_porta_ilegivel_inicia_e_a_porta_decide_no_despacho(c: Cenario) -> None:
    c.portas.porta_quebra = True
    await _executar(c)
    assert c.portas.chamadas[-1][:2] == ("iniciar", (RUN,)) and c.linha(5)["estado"] == "feita"
    assert "as travas se decidem na execução" in c.bot.textos()[-1]


async def test_plano_mudou_manda_a_previa_nova_e_nada_e_gravado(c: Cenario) -> None:
    c.portas.previa_da_porta = _porta(_item("s1"))
    ident = await _executar(c)
    c.portas.mudou = [{"step_id": "s1", "selo": "aprovacao", "motivo": "a chave mudou"}]
    c.portas.previa_da_porta = _porta(_item("s1", chave="d" * 64), _item("s3"))
    await c.volta(botao(7, _p(c, ident), mid=c.bot.mid))
    assert "O plano mudou em 1 item desde a prévia: nada foi gravado. Segue a prévia nova." in c.bot.textos()
    assert c.portas.estados[RUN] == "planned" and c.linha(5)["estado"] == "pergunta"
    assert json.loads(str(c.linha(5)["previa"]))["aprovar"] == [["s1", "d" * 64], ["s3", f"{'s3':0<64}"]]
    await c.volta(botao(8, _p(c, ident), mid=c.bot.mid))
    assert c.portas.chamadas[-1] == ("aprovar_plano", (RUN, (("s1", "d" * 64), ("s3", f"{'s3':0<64}"))),
                                     OPERADOR_DO_TELEGRAM)
    assert c.portas.nomes().count("aprovar_plano") == 2


async def test_g1b_o_executar_devolve_o_vista_em_do_retrato_que_o_dono_viu(c: Cenario) -> None:
    """G1b: o "Executar (aprova N)" manda o `vista_em` da prévia RETRATADA (a que o dono viu), não o de uma prévia lida
    na hora do toque; depois de um `plano_mudou`, o da prévia nova que a conversa mostrou."""
    c.portas.previa_da_porta = _porta(_item("s1"), vista_em="2026-10-05T02:00:00Z")
    ident = await _executar(c)
    c.portas.mudou = [{"step_id": "s1", "selo": "aprovacao", "motivo": "a chave mudou"}]
    c.portas.previa_da_porta = _porta(_item("s1", chave="d" * 64), vista_em="2026-10-05T02:05:00Z")
    await c.volta(botao(7, _p(c, ident), mid=c.bot.mid))
    assert json.loads(str(c.linha(5)["previa"]))["vista_em"] == "2026-10-05T02:05:00Z"
    c.portas.previa_da_porta = _porta(_item("s1", chave="d" * 64), vista_em="2026-10-05T02:09:00Z")
    await c.volta(botao(8, _p(c, ident), mid=c.bot.mid))
    assert c.portas.vistas_em == ["2026-10-05T02:00:00Z", "2026-10-05T02:05:00Z"]


async def test_previa_da_porta_vencida_cancela_a_execucao(c: Cenario) -> None:
    c.portas.previa_da_porta = _porta(_item("s1"), validade="2000-01-01T00:00:00Z")
    ident = await _executar(c)
    await c.volta(botao(7, _p(c, ident), mid=c.bot.mid))
    assert "aprovar_plano" not in c.portas.nomes() and c.portas.chamadas[-1][:2] == ("cancelar", (RUN,))
    assert c.linha(5)["estado"] == "cancelada" and c.bot.textos()[-1] == "Esta prévia venceu; mande o pedido de novo."


async def test_cancelar_na_porta_cancela_a_execucao(c: Cenario) -> None:
    c.portas.previa_da_porta = _porta(_item("s1"))
    ident = await _executar(c)
    await c.volta(botao(7, f"c:{ident}", mid=c.bot.mid))
    assert c.portas.chamadas[-1][:2] == ("cancelar", (RUN,)) and c.linha(5)["estado"] == "cancelada"
    assert "aprovar_plano" not in c.portas.nomes()
    # 28.41 (N1 da leitura do #361): só o Cancelar do dono leva o gesto (o sinal `cancelou_execucao`, ADR-054).
    assert c.portas.sem_gesto == []


async def test_item_cujo_texto_os_filtros_mudariam_fica_fora_do_sim_pelo_canal(c: Cenario) -> None:
    """P1 (orquestradora, 04/10 21:58Z): o dono não aprova o que não pode ver por inteiro."""
    c.portas.personas = ["Bruno Lima"]
    c.portas.previa_da_porta = _porta(_item("s1", texto="oi, Bruno Lima"), _item("s2"))
    await _executar(c)
    assert json.loads(str(c.linha(5)["previa"]))["aprovar"] == [["s2", f"{'s2':0<64}"]]
    textos = "\n".join(c.bot.textos())
    assert "bruno" not in textos.lower()
    assert "1 item não pode ser mostrado aqui por inteiro; ele pede você no painel ou na execução." in textos


async def test_texto_do_item_chega_como_e_sem_parse_mode(c: Cenario) -> None:
    """R2: a mensagem vai em texto puro; o texto do item chega ao dono como é."""
    texto = '{x} <b>negrito</b> & "aspas" *asterisco* _sub_'
    c.portas.previa_da_porta = _porta(_item("s1", texto=texto))
    await _executar(c)
    ultima = c.bot.mensagens()[-1]
    assert f"Texto: “{texto}”" in str(ultima["text"]) and "parse_mode" not in ultima


async def test_imagem_conferida_vai_ao_dono_e_a_diferente_fica_fora(c: Cenario, monkeypatch: pytest.MonkeyPatch) -> None:
    import hashlib

    enviadas: list[tuple[bytes, str]] = []

    async def enviar(saida: object, conteudo: bytes, legenda: str = "", **kw: object) -> str:
        enviadas.append((conteudo, legenda))
        return "999"

    monkeypatch.setattr(c.servico.conversa, "enviar_conteudo", enviar)
    imagem = b"\x89PNG imagem da etapa"
    c.portas.imagens = {"s1": (imagem, "application/octet-stream"), "s2": (b"outra", "application/octet-stream")}
    c.portas.previa_da_porta = _porta(
        _item("s1", tem_imagem=True, imagem_sha256=hashlib.sha256(imagem).hexdigest()),
        _item("s2", tem_imagem=True, imagem_sha256=hashlib.sha256(b"a que a porta viu").hexdigest()))
    await _executar(c)
    assert json.loads(str(c.linha(5)["previa"]))["aprovar"] == [["s1", f"{'s1':0<64}"]]
    assert enviadas == [(imagem, "item 1")]


async def test_imagem_de_item_fora_do_canal_nao_sai(c: Cenario, monkeypatch: pytest.MonkeyPatch) -> None:
    """Revisão do 28.27 (A): o item cujo texto os filtros mudariam fica fora do sim, e a imagem dele também não sai."""
    import hashlib

    enviadas: list[bytes] = []

    async def enviar(saida: object, conteudo: bytes, legenda: str = "", **kw: object) -> str:
        enviadas.append(conteudo)
        return "999"

    monkeypatch.setattr(c.servico.conversa, "enviar_conteudo", enviar)
    c.portas.personas = ["Bruno Lima"]
    imagem = b"imagem do item fora"
    c.portas.imagens = {"s1": (imagem, "application/octet-stream")}
    c.portas.previa_da_porta = _porta(
        _item("s1", texto="oi, Bruno Lima", tem_imagem=True, imagem_sha256=hashlib.sha256(imagem).hexdigest()),
        _item("s2"))
    await _executar(c)
    assert json.loads(str(c.linha(5)["previa"]))["aprovar"] == [["s2", f"{'s2':0<64}"]]
    assert enviadas == [] and "Imagem: no painel." in "\n".join(c.bot.textos())


async def test_item_cujo_bloco_nao_cabe_inteiro_fica_fora_do_sim(c: Cenario) -> None:
    """Revisão do 28.27 (B): cortado com "…", o dono não leria tudo o que aprova."""
    c.portas.previa_da_porta = _porta(_item("s1", motivo="m" * 3790), _item("s2"))
    await _executar(c)
    assert json.loads(str(c.linha(5)["previa"]))["aprovar"] == [["s2", f"{'s2':0<64}"]]


async def test_erro_interno_ao_iniciar_sem_sim_pendente_nao_fica_em_laco(c: Cenario,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    def quebra(run_id: str, aprovar: list[tuple[str, str]], *,
               vista_em: str | None = None) -> dict[str, object]:
        raise RuntimeError("banco fora")

    monkeypatch.setattr(c.portas, "aprovar_plano", quebra)
    await _executar(c)
    assert c.linha(5)["estado"] == "falhou"
    assert c.bot.textos()[-1] == "Não iniciei a execução: erro interno (está no log da Central)."
    await c.volta()
    assert c.linha(5)["estado"] == "falhou"


# ----------------------------------------------------------- revisão independente do #336 (04/10 22:59Z)
async def test_botao_de_previa_velha_nao_aprova_o_retrato_novo(c: Cenario) -> None:
    """Decisão da orquestradora: depois do `plano_mudou`, o "Executar" da mensagem ANTIGA não aprova o retrato novo."""
    c.portas.previa_da_porta = _porta(_item("s1"))
    ident = await _executar(c)
    velho = _p(c, ident)
    c.portas.mudou = [{"step_id": "s1", "selo": "aprovacao", "motivo": "a chave mudou"}]
    c.portas.previa_da_porta = _porta(_item("s1", chave="d" * 64))
    await c.volta(botao(7, velho, mid=c.bot.mid))                 # o 409: sai a prévia nova, de marca nova
    assert _p(c, ident) != velho
    await c.volta(botao(8, velho, mid=c.bot.mid - 3))             # o botão da mensagem antiga, de novo
    assert c.bot.textos()[-1] == "Essa prévia mudou: nada foi aprovado. Use a prévia nova, a mais recente desta conversa."
    assert c.portas.nomes().count("aprovar_plano") == 1 and c.linha(5)["estado"] == "pergunta"
    await c.volta(botao(9, f"p:{ident}", mid=c.bot.mid))           # sem marca nenhuma: também não aprova
    assert c.portas.nomes().count("aprovar_plano") == 1
    await c.volta(botao(10, _p(c, ident), mid=c.bot.mid))
    assert c.portas.chamadas[-1] == ("aprovar_plano", (RUN, (("s1", "d" * 64),)), OPERADOR_DO_TELEGRAM)


async def test_plano_mudou_para_nada_a_aprovar_inicia_numa_linha_que_diz_que_mudou(c: Cenario) -> None:
    c.portas.previa_da_porta = _porta(_item("s1"))
    ident = await _executar(c)
    c.portas.mudou = [{"step_id": "s1", "selo": "permitido", "motivo": "não pede mais"}]
    c.portas.previa_da_porta = _porta(_item("s1", selo="permitido"))
    await c.volta(botao(7, _p(c, ident), mid=c.bot.mid))
    assert c.portas.chamadas[-1] == ("aprovar_plano", (RUN, ()), OPERADOR_DO_TELEGRAM)
    assert c.linha(5)["estado"] == "feita"
    assert c.bot.textos()[-1] == ("O plano mudou desde a prévia e nenhum item pede o seu sim agora: execução abc123 "
                                  "iniciada. Conto aqui quando terminar.")
    assert not any(t.startswith("O plano mudou em") for t in c.bot.textos())          # uma linha só


async def test_erro_ao_iniciar_sem_a_porta_marca_falha_cancela_e_nao_cala_as_outras(c: Cenario,
                                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """Revisão do #336 (A1): a exceção genérica no start não sobe do vigia nem deixa a linha em `planejando`."""
    c.portas.porta_quebra = True

    def quebra(run_id: str) -> None:
        raise RuntimeError("banco fora")

    monkeypatch.setattr(c.portas, "iniciar", quebra)
    await _executar(c)
    assert c.linha(5)["estado"] == "falhou" and c.portas.chamadas[-1][:2] == ("cancelar", (RUN,))
    assert c.bot.textos()[-1] == "Não iniciei a execução: erro interno (está no log da Central)."
    n = len(c.bot.textos())
    await c.volta()
    assert len(c.bot.textos()) == n                                  # avisado uma vez; não repete a cada volta


async def test_uma_linha_ruim_nao_cala_as_outras_da_volta(c: Cenario, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.avisos.infrastructure import entrada as modulo

    await _executar(c)                                               # N = 0: a primeira inicia e fica `feita`
    await c.volta(msg(20, "abra o Chrome no android-10"))
    segunda = int(c.linha(20)["id"])
    # As duas em `planejando` na MESMA volta: a primeira (id menor) quebra, a segunda tem de ser vista.
    c.repo.marcar(int(c.linha(5)["id"]), "executando", run_id=RUN, previa={"fase": "planejando", "curta": "abc123"},
                  de=("feita",))
    c.portas.estados[RUN] = "planned"
    c.repo.marcar(segunda, "executando", run_id="r-outra-000002", previa={"fase": "planejando", "curta": "000002"},
                  de=("pergunta",))
    c.portas.estados["r-outra-000002"] = "running"
    original = modulo.ConversaDoCanal._ver_um_plano

    async def ruim(self: object, saida: object, linha: dict) -> None:
        if linha["run_id"] == RUN:
            raise RuntimeError("linha ruim")
        await original(self, saida, linha)                          # type: ignore[arg-type]

    monkeypatch.setattr(modulo.ConversaDoCanal, "_ver_um_plano", ruim)
    await c.volta()
    assert c.linha(5)["estado"] == "falhou"                          # a ruim foi abandonada, com a execução cancelada
    assert c.linha(20)["estado"] == "feita"                          # e a seguinte da volta foi vista


async def test_vigia_so_le_a_fase_planejando_e_nao_se_cala_com_presas(c: Cenario) -> None:
    """Revisão do #336 (A2): `planejando()` filtra a fase no SQL; 25 linhas presas na porta não calam o vigia."""
    for n in range(25):
        # Gravadas direto (não pelo chat: o limite por minuto seguraria as mensagens), já na fase da porta.
        c.repo.gravar(id_externo=str(100 + n), ordem=None, tipo="mensagem", do_dono=False, ref_mensagem=None,
                      responde_a=None, texto="x", tamanho=1, estado="ignorada")
        c.repo.marcar(int(c.linha(100 + n)["id"]), "executando", run_id=f"r-presa-{n:06d}",
                      previa={"fase": "porta", "curta": f"{n:06d}"})
    await _executar(c)
    assert c.linha(5)["estado"] == "feita"                           # a nova foi vista mesmo atrás das 25
    assert all(json.loads(str(x["previa"]))["fase"] == "planejando" for x in c.repo.planejando())


async def test_fase_gravada_por_marcar_e_lida_por_planejando_e_presas_na_porta(tmp_path: Path) -> None:
    """Nota da revisão do #336: o filtro da fase não pode depender dos separadores do `json.dumps` do `marcar`. Grava
    por `marcar` e lê por `planejando()` e `presas_na_porta()`; a mesma `previa` gravada compacta também é achada; a
    aspa e a palavra "fase" no texto do dono não enganam o filtro."""
    agora = [datetime(2026, 10, 4, 23, 0, tzinfo=timezone.utc)]
    c = Cenario(tmp_path, relogio=lambda: agora[0])

    def linha(n: int, fase: str, **extra: object) -> int:
        c.repo.gravar(id_externo=str(n), ordem=None, tipo="mensagem", do_dono=False, ref_mensagem=None,
                      responde_a=None, texto="x", tamanho=1, estado="ignorada")
        ident = int(c.linha(n)["id"])
        c.repo.marcar(ident, "executando", run_id=f"r-{n:06d}", previa={"fase": fase, **extra})
        return ident

    plan, porta = linha(1, "planejando"), linha(2, "porta")
    enganosa = linha(3, "porta", texto='o dono escreveu "fase" e "planejando"')
    compacta = linha(4, "porta")
    c.db.execute("UPDATE canal_entradas SET previa=? WHERE id=?",
                 (json.dumps({"fase": "planejando"}, separators=(",", ":")), compacta))
    assert sorted(int(x["id"]) for x in c.repo.planejando()) == [plan, compacta]
    agora[0] += timedelta(hours=1)
    assert sorted(int(x["id"]) for x in c.repo.presas_na_porta(60)) == [porta, enganosa]


async def test_linha_presa_na_porta_e_recuperada_e_a_execucao_cancelada(tmp_path: Path) -> None:
    agora = [datetime(2026, 10, 4, 23, 0, tzinfo=timezone.utc)]
    c = Cenario(tmp_path, relogio=lambda: agora[0])
    c.portas.previa_da_porta = _porta(_item("s1"))
    ident = await _executar(c)
    # A queda: o CAS de `pergunta` a `executando` do "Executar (aprova N)" passou, o resto não.
    assert c.repo.marcar(ident, "executando", de=("pergunta",))
    agora[0] += timedelta(seconds=PRESA_S + 1)
    await c.volta()
    assert c.linha(5)["estado"] == "falhou" and c.portas.chamadas[-1][:2] == ("cancelar", (RUN,))
    assert c.bot.textos()[-1].startswith("A aprovação deste plano foi interrompida antes de terminar")


async def test_linha_presa_antes_do_run_id_cancela_a_execucao_esquecida(tmp_path: Path) -> None:
    agora = [datetime(2026, 10, 4, 23, 0, tzinfo=timezone.utc)]
    c = Cenario(tmp_path, relogio=lambda: agora[0])
    await c.volta(msg(5, "abra o Chrome no android-09"))
    ident = int(c.linha(5)["id"])
    assert c.repo.marcar(ident, "executando", de=("pergunta",))  # a queda entre o CAS e gravar o `run_id`
    c.portas.criada_com["telegram:5"] = RUN
    c.portas.estados[RUN] = "planned"
    agora[0] += timedelta(seconds=PRESA_S + 1)
    await c.volta()
    assert c.linha(5)["estado"] == "falhou"
    assert ("execucao_da_chave", ("telegram:5",), OPERADOR_DO_TELEGRAM) in c.portas.chamadas
    assert c.portas.chamadas[-1][:2] == ("cancelar", (RUN,))
    assert c.portas.sem_gesto == [RUN], "o cancelamento automático não é gesto do dono (28.41, N1 do #361)"


async def test_previa_que_nao_sai_marca_falha_cancela_e_avisa_uma_vez(c: Cenario) -> None:
    c.portas.previa_da_porta = _porta(_item("s1"))
    falhas = {"n": 0}
    original = c.bot.handler

    def handler(req: httpx.Request) -> httpx.Response:
        corpo = json.loads(req.content) if req.content else {}
        if req.url.path.endswith("/sendMessage") and str(corpo.get("text", "")).startswith("Plano abc123"):
            falhas["n"] += 1
            return httpx.Response(400, json={"ok": False, "description": "Bad Request: message is too long"})
        return original(req)

    c.bot.handler = handler                                          # type: ignore[method-assign]
    c.servico = c.novo_servico()
    await _executar(c)
    assert falhas["n"] >= 1 and c.linha(5)["estado"] == "falhou"
    assert c.portas.chamadas[-1][:2] == ("cancelar", (RUN,)) and "aprovar_plano" not in c.portas.nomes()
    assert c.portas.sem_gesto == [RUN], "o cancelamento automático não é gesto do dono (28.41, N1 do #361)"
    assert c.bot.textos()[-1] == ("Não consegui mandar a prévia do plano abc123: nada foi aprovado nem iniciado, e a "
                                  "execução foi cancelada. Mande o pedido de novo.")
    n = len(c.bot.textos())
    await c.volta()
    assert len(c.bot.textos()) == n


@pytest.mark.parametrize("caminho", ["cancelar", "vencida", "recusa", "erro"])
async def test_todo_caminho_que_abandona_a_porta_cancela_a_execucao_planned(c: Cenario, caminho: str,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    c.portas.previa_da_porta = _porta(_item("s1"), validade="2000-01-01T00:00:00Z" if caminho == "vencida"
                                      else "2099-10-05T22:40:00Z")
    ident = await _executar(c)
    if caminho == "recusa":
        c.portas.recusar_aprovar = "O plano desta execução não pode ser aprovado agora."
    quebrou: list[str] = []
    if caminho == "erro":
        def quebra(run_id: str, aprovar: list[tuple[str, str]], *,
                   vista_em: str | None = None) -> dict[str, object]:
            quebrou.append(run_id)            # o dublê rodou: o erro é o "banco fora", não a assinatura
            raise RuntimeError("banco fora")
        monkeypatch.setattr(c.portas, "aprovar_plano", quebra)
    gesto = f"c:{ident}" if caminho == "cancelar" else _p(c, ident)
    await c.volta(botao(7, gesto, mid=c.bot.mid))
    assert quebrou == ([RUN] if caminho == "erro" else [])
    assert c.portas.chamadas[-1][:2] == ("cancelar", (RUN,)) and c.portas.estados[RUN] == "cancelled"
    assert c.linha(5)["estado"] in ("cancelada", "falhou")


async def test_recusa_de_plano_ja_iniciado_nao_cancela(c: Cenario) -> None:
    """`invalid_state` de um plano que outro gesto já iniciou: a execução segue, e o desfecho de sempre a conta."""
    c.portas.previa_da_porta = _porta(_item("s1"))
    ident = await _executar(c)
    c.portas.recusar_aprovar = "O plano desta execução já foi aprovado ou iniciado."
    c.portas.estados[RUN] = "running"
    await c.volta(botao(7, _p(c, ident), mid=c.bot.mid))
    assert "cancelar" not in c.portas.nomes() and c.linha(5)["estado"] == "feita"
    # 28.36: nunca "não iniciei" quando a execução está em andamento.
    assert c.bot.textos()[-1] == ("A execução abc123 já estava em andamento: O plano desta execução já foi aprovado ou "
                                  "iniciado.")


@pytest.mark.parametrize(("estado", "esperado"), [
    ("cancelling", "A execução abc123 está sendo cancelada: Recusa de teste."),
    ("needs_input", "A execução abc123 espera uma resposta sua: responda no painel para ela seguir (Recusa de teste.)."),
])
async def test_recusa_em_cancelamento_ou_pergunta_diz_o_estado_certo(c: Cenario, estado: str, esperado: str) -> None:
    """28.38: em `cancelling` ela podia estar rodando ("não iniciei" seria falso); em `needs_input` só o painel a destrava."""
    c.portas.previa_da_porta = _porta(_item("s1"))
    ident = await _executar(c)
    c.portas.recusar_aprovar = "Recusa de teste."
    c.portas.estados[RUN] = estado
    await c.volta(botao(7, _p(c, ident), mid=c.bot.mid))
    assert "cancelar" not in c.portas.nomes() and c.linha(5)["estado"] == "feita"
    assert c.bot.textos()[-1] == esperado


@pytest.mark.parametrize(("estado", "comeco"), [
    ("cancelling", "A execução abc123 está sendo cancelada; houve um erro interno ao iniciar"),
    ("needs_input", "A execução abc123 espera uma resposta sua: responda no painel para ela seguir; houve um erro"),
    ("planning", "A execução abc123 ainda não começou, depois de um erro interno ao iniciar"),
])
async def test_erro_ao_iniciar_diz_o_estado_certo(c: Cenario, monkeypatch: pytest.MonkeyPatch, estado: str,
                                                  comeco: str) -> None:
    c.portas.previa_da_porta = _porta(_item("s1"))
    ident = await _executar(c)

    def quebra(run_id: str, aprovar: list[tuple[str, str]], *,
               vista_em: str | None = None) -> dict[str, object]:
        c.portas.estados[run_id] = estado
        raise RuntimeError("erro de teste")

    monkeypatch.setattr(c.portas, "aprovar_plano", quebra)
    await c.volta(botao(7, _p(c, ident), mid=c.bot.mid))
    assert "cancelar" not in c.portas.nomes() and c.linha(5)["estado"] == "feita"
    assert c.bot.textos()[-1].startswith(comeco), c.bot.textos()[-1]
    assert not any(t.startswith("Não iniciei") for t in c.bot.textos())


async def test_erro_depois_do_inicio_nao_diz_que_nao_iniciou(c: Cenario, monkeypatch: pytest.MonkeyPatch) -> None:
    """28.36 (revisão do #346): a exceção que não é recusa chega DEPOIS do compare-and-set do início (pré-voo,
    objetivos, agendador). A execução já roda com os sins: a linha fica feita, nada é cancelado, e o texto diz que ela
    está em andamento."""
    c.portas.previa_da_porta = _porta(_item("s1"))
    ident = await _executar(c)

    def quebra_depois_do_inicio(run_id: str, aprovar: list[tuple[str, str]], *,
                                vista_em: str | None = None) -> dict[str, object]:
        c.portas.estados[run_id] = "running"
        raise RuntimeError("pré-voo fora")

    monkeypatch.setattr(c.portas, "aprovar_plano", quebra_depois_do_inicio)
    await c.volta(botao(7, _p(c, ident), mid=c.bot.mid))
    assert "cancelar" not in c.portas.nomes() and c.linha(5)["estado"] == "feita"
    assert c.portas.estados[RUN] == "running"
    assert c.bot.textos()[-1] == ("A execução abc123 está em andamento; houve um erro interno logo depois do início "
                                  "(está no log da Central). Conto aqui quando terminar.")
    assert not any(t.startswith("Não iniciei") for t in c.bot.textos())


async def test_recusa_com_a_execucao_ja_terminal_deixa_so_o_desfecho(c: Cenario) -> None:
    """28.36, caso (c) da revisão do #346: a recusa chega com a execução já TERMINAL (cancelada por outro gesto). A linha
    fica feita, e o dono lê UMA linha só: a do desfecho de sempre, sem o código cru do estado."""
    c.portas.previa_da_porta = _porta(_item("s1"))
    ident = await _executar(c)
    c.portas.recusar_aprovar = "Esta execução foi cancelada."
    c.portas.estados[RUN] = "cancelled"
    c.portas.desfechos[RUN] = "Execução abc123: cancelada."
    n = len(c.bot.textos())
    await c.volta(botao(7, _p(c, ident), mid=c.bot.mid))
    await c.volta()
    assert c.linha(5)["estado"] == "feita" and "cancelar" not in c.portas.nomes()
    novas = c.bot.textos()[n:]
    assert novas == ["Execução abc123: cancelada."], novas
    assert not any("'cancelled'" in t or "Não iniciei" in t for t in novas)


async def test_erro_ao_avisar_que_seguiu_nao_sobe(c: Cenario, monkeypatch: pytest.MonkeyPatch) -> None:
    """Revisão do #346, E1: o envio do "está em andamento" que falha não sobe (no vigia, calaria as linhas seguintes)."""
    from app.modules.avisos.infrastructure.entrada import ConversaDoCanal

    c.portas.previa_da_porta = _porta(_item("s1"))
    ident = await _executar(c)
    c.portas.recusar_aprovar = "O plano desta execução já foi aprovado ou iniciado."
    c.portas.estados[RUN] = "running"

    async def responder_quebra(self: Any, *a: Any, **kw: Any) -> None:
        raise RuntimeError("banco fora ao registrar a enviada")

    monkeypatch.setattr(ConversaDoCanal, "_responder", responder_quebra)
    await c.volta(botao(7, _p(c, ident), mid=c.bot.mid))
    assert c.linha(5)["estado"] == "feita" and "cancelar" not in c.portas.nomes()


@pytest.mark.parametrize("caminho", ["sem_sim", "sem_porta"])
async def test_erro_depois_do_inicio_nos_outros_caminhos_tambem_nao_diz_que_nao_iniciou(
        c: Cenario, caminho: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """28.36: a mesma regra no N = 0 (o `aprovar_plano` sem sins, direto do vigia) e no início sem a porta."""
    if caminho == "sem_sim":
        c.portas.previa_da_porta = _porta(_item("s1", selo="permitido"), total=True)

        def quebra(run_id: str, aprovar: list[tuple[str, str]], *,
                   vista_em: str | None = None) -> dict[str, object]:
            c.portas.estados[run_id] = "running"
            raise RuntimeError("agendador fora")

        monkeypatch.setattr(c.portas, "aprovar_plano", quebra)
    else:
        c.portas.porta_quebra = True

        def quebra_ao_iniciar(run_id: str) -> None:
            c.portas.estados[run_id] = "running"
            raise RuntimeError("agendador fora")

        monkeypatch.setattr(c.portas, "iniciar", quebra_ao_iniciar)
    await _executar(c)
    assert "cancelar" not in c.portas.nomes() and c.linha(5)["estado"] == "feita"
    assert c.bot.textos()[-1].startswith("A execução abc123 está em andamento; houve um erro interno")
    assert not any(t.startswith("Não iniciei") for t in c.bot.textos())


# ------------------------------------------------------------------ 28.44: a resposta solta casa com a escolha aberta
#: O que a resposta casada nunca chama. A triagem do curto (`pergunta_sensivel`) só lê, e roda antes de tudo.
ACOES_DA_PORTA = {"decidir", "responder", "previa", "criar", "aprovar_plano", "iniciar", "cancelar"}


def _escolha(tmp_path: Path) -> tuple[Cenario, list[datetime]]:
    agora = [datetime(2026, 10, 5, 10, 26, 26, tzinfo=timezone.utc)]
    c = Cenario(tmp_path, relogio=lambda: agora[0])
    return c, agora


def _pergunta(c: Cenario, agora: list[datetime], mid: int, opcoes: str = "1-2-3") -> None:
    c.repo.registrar_enviada(str(mid), "ana", fato=f"escolha:{mid}:{opcoes}")
    agora[0] += timedelta(seconds=33)


def _dono(uid: int, texto: str, mid: int, **extra: Any) -> dict[str, object]:
    """A mensagem do dono com o `message_id` explícito: no chat privado ele é a ordem das mensagens (C1 do #412)."""
    return msg(uid, texto, mid=mid, **extra)


def _respondida_a(c: Cenario) -> str | None:
    ultima = c.bot.mensagens()[-1]
    return str((ultima.get("reply_parameters") or {}).get("message_id")) if ultima.get("reply_parameters") else None


@pytest.mark.parametrize("texto", ["1", "opção 1", "a 1", "1.", "1)", " Opção 1 "])
async def test_um_solto_casa_com_a_escolha_aberta_e_vai_a_orquestradora(tmp_path: Path, texto: str) -> None:
    """28.44 (o "1" solto das 10:26:59Z de 05/10, 33 s depois da 294): casa com a pergunta, vai à orquestradora com
    `casada_com`, sem prévia, sem porta e sem `responde_a`. A resposta diz qual pergunta e sai em reply a ELA (D1)."""
    c, agora = _escolha(tmp_path)
    _pergunta(c, agora, 294)
    await c.volta(_dono(5, texto, 295))
    linha = c.linha(5)
    assert (linha["estado"], linha["alvo"], linha["responde_a"]) == ("orquestradora", "escolha:294", None)
    previa = json.loads(str(linha["previa"]))
    assert (previa["repasse"], previa["casada_com"], previa["opcao"]) == ("escolha", "294", "1")
    assert not set(c.portas.nomes()) & ACOES_DA_PORTA
    assert c.bot.textos()[-1] == RESPOSTA_ESCOLHA_CASADA.format(texto=texto.strip(), opcao="1", hora="10:26Z")
    assert _respondida_a(c) == "294"


async def test_a_pergunta_mandada_depois_da_resposta_nao_casa(tmp_path: Path) -> None:
    """C1 da leitura do #412: o dono responde "1" pensando na 294; antes de o laço gravar o "1", a ANA manda a 297, que
    substitui a 294. Pelo relógio do servidor, a 297 parecia anterior ao "1". Pela ordem do chat, o "1" (296) veio
    antes da 297: casa com a 294, que ainda estava aberta quando ele escreveu."""
    c, agora = _escolha(tmp_path)
    _pergunta(c, agora, 294)
    _pergunta(c, agora, 297)
    c.repo.registrar_substituta("297", "294")
    await c.volta(_dono(5, "1", 296))
    assert c.linha(5)["alvo"] == "escolha:294"
    # Depois da 297, a substituição já vale: o "2" (298) casa com a 297.
    await c.volta(_dono(6, "2", 298))
    assert c.linha(6)["alvo"] == "escolha:297"


async def test_fora_da_janela_nao_casa_e_cai_na_recusa_do_curto(tmp_path: Path) -> None:
    c, agora = _escolha(tmp_path)
    _pergunta(c, agora, 294)
    agora[0] += timedelta(seconds=JANELA_DA_ESCOLHA_S)
    await c.volta(_dono(5, "1", 295))
    assert (c.linha(5)["estado"], c.linha(5)["erro"]) == ("recusada", ERRO_CURTO_DEMAIS)
    assert not set(c.portas.nomes()) & ACOES_DA_PORTA


async def test_duas_abertas_nao_casam_e_vao_como_ambiguas(tmp_path: Path) -> None:
    c, agora = _escolha(tmp_path)
    _pergunta(c, agora, 294)
    _pergunta(c, agora, 297, "A-B")
    await c.volta(_dono(5, "1", 298))
    linha = c.linha(5)
    assert (linha["estado"], linha["alvo"]) == ("orquestradora", None)       # a ambígua não fecha nenhuma
    previa = json.loads(str(linha["previa"]))
    assert previa["repasse"] == "escolha_ambigua" and sorted(previa["abertas"]) == ["294", "297"]
    assert "casada_com" not in previa
    assert c.bot.textos()[-1] == RESPOSTA_ESCOLHA_AMBIGUA
    assert not set(c.portas.nomes()) & ACOES_DA_PORTA


async def test_a_ja_respondida_nao_casa_de_novo(tmp_path: Path) -> None:
    c, agora = _escolha(tmp_path)
    _pergunta(c, agora, 294)
    await c.volta(_dono(5, "2", 295, reply_to=294))                # o reply de verdade fecha a pergunta
    assert c.linha(5)["alvo"] == "escolha:294"
    await c.volta(_dono(6, "1", 296))
    assert (c.linha(6)["estado"], c.linha(6)["erro"]) == ("recusada", ERRO_CURTO_DEMAIS)
    # a casada também fecha: o segundo "1" solto não casa
    _pergunta(c, agora, 300)
    await c.volta(_dono(7, "1", 301))
    assert c.linha(7)["alvo"] == "escolha:300"
    await c.volta(_dono(8, "2", 302))
    assert c.linha(8)["estado"] == "recusada"


async def test_a_substituida_nao_esta_mais_aberta(tmp_path: Path) -> None:
    """Acréscimo da leitura da orquestradora: a rodada 2 fecha a rodada 1. Com a substituta gravada, só a nova está
    aberta, e o "1" casa com ela."""
    c, agora = _escolha(tmp_path)
    _pergunta(c, agora, 294)
    _pergunta(c, agora, 297)
    c.repo.registrar_substituta("297", "294")
    await c.volta(_dono(5, "1", 298))
    assert c.linha(5)["alvo"] == "escolha:297"
    assert c.repo.enviada("substitui:297") is not None and c.repo.enviada("297")["fato"] == "escolha:297:1-2-3"


@pytest.mark.parametrize("texto", ["4", "1 e 3", "sim, a 2"])
async def test_o_que_nao_e_uma_opcao_da_lista_nao_casa(tmp_path: Path, texto: str) -> None:
    c, agora = _escolha(tmp_path)
    _pergunta(c, agora, 294)
    await c.volta(_dono(5, texto, 295))
    assert c.linha(5)["alvo"] != "escolha:294"
    assert c.repo.escolhas_abertas(str(c.linha(5)["recebida_em"]), JANELA_DA_ESCOLHA_S, fora=0,
                                   ref_da_resposta="295") != []


async def test_reply_a_escolha_vai_a_orquestradora_pelo_ramo_novo(tmp_path: Path) -> None:
    """Sem o ramo `escolha`, o reply caía na regra de fundo do G1 ("só informa")."""
    c, agora = _escolha(tmp_path)
    _pergunta(c, agora, 294)
    await c.volta(_dono(5, "a segunda, mas só amanhã", 295, reply_to=294))
    linha = c.linha(5)
    assert (linha["estado"], linha["alvo"], linha["responde_a"]) == ("orquestradora", "escolha:294", "294")
    previa = json.loads(str(linha["previa"]))
    assert previa["repasse"] == "escolha" and "casada_com" not in previa and "a segunda" in previa["texto"]
    assert c.bot.textos()[-1] == RESPOSTA_ESCOLHA_REPLY
    assert not set(c.portas.nomes()) & ACOES_DA_PORTA


async def test_a_casada_nunca_decide_um_aval_aberto(tmp_path: Path) -> None:
    """§7 do desenho: com um aviso de aprovação aberto E uma escolha aberta, o "1" solto casa com a escolha e não chama
    aprovar, vetar, decidir nem criar. O aval só se decide pelo reply ao aviso dele."""
    c, agora = _escolha(tmp_path)
    c.repo.registrar_enviada("290", "aviso", fato="approval:apr-0000aa11")
    _pergunta(c, agora, 294)
    await c.volta(_dono(5, "1", 295))
    assert c.linha(5)["alvo"] == "escolha:294"
    assert not set(c.portas.nomes()) & ACOES_DA_PORTA


async def test_sim_solto_com_escolha_e_aval_abertos_vai_a_orquestradora(tmp_path: Path) -> None:
    """Leitura do #412: escolha aberta + aval aberto + "sim" solto. "sim" nunca é opção (nem que a pergunta o
    listasse), não casa com a escolha e não decide o aval: sem reply, é texto livre; a prévia não acha destino nele e
    ele vai à orquestradora."""
    c, agora = _escolha(tmp_path)
    c.repo.registrar_enviada("290", "aviso", fato="approval:apr-0000aa11")
    _pergunta(c, agora, 294, "sim-nao")                 # marca forjada: o `--escolha` recusa, e a conversa também
    c.portas.recusar_previa = "Diga onde ou por quem."
    await c.volta(_dono(5, "sim", 295))
    linha = c.linha(5)
    assert linha["alvo"] != "escolha:294" and linha["estado"] == "orquestradora"
    assert json.loads(str(linha["previa"]))["repasse"] == "sem_destino"
    assert "decidir" not in c.portas.nomes()


async def test_curto_solto_com_senha_e_escolha_abertas_segue_a_recusa_por_credencial(tmp_path: Path) -> None:
    """Leitura do #412: com uma pergunta de senha aberta, o curto solto é tratado como possível senha mesmo com uma
    escolha aberta. Não casa, não vai à orquestradora com o texto e não guarda o texto."""
    c, agora = _escolha(tmp_path)
    _pergunta(c, agora, 294)
    c.portas.sensivel_aberta = True
    await c.volta(_dono(5, "1", 295))
    linha = c.linha(5)
    assert linha["estado"] == "recusada" and linha["alvo"] is None and linha["texto"] is None
    assert not set(c.portas.nomes()) & ACOES_DA_PORTA


async def test_comando_solto_nao_casa(tmp_path: Path) -> None:
    c, agora = _escolha(tmp_path)
    _pergunta(c, agora, 294)
    await c.volta(_dono(5, "/status", 295))
    assert c.linha(5)["intencao"] == "status"


# ------------------------------------------------------------------ 28.47: as sobras da leitura do #412
@pytest.mark.parametrize(("pergunta", "resposta", "casa"), [(99, 100, True), (100, 99, False), (9, 10, True)])
async def test_a_ordem_do_message_id_e_numerica(tmp_path: Path, pergunta: int, resposta: int, casa: bool) -> None:
    """Cruza a casa dos dígitos: como texto, "99" < "100" é falso, e a resposta 100 não casaria com a pergunta 99."""
    c, agora = _escolha(tmp_path)
    _pergunta(c, agora, pergunta)
    await c.volta(_dono(5, "1", resposta))
    assert (c.linha(5)["alvo"] == f"escolha:{pergunta}") is casa


async def test_a_pergunta_gravada_um_pouco_depois_do_recado_ainda_casa(tmp_path: Path) -> None:
    """A ANA manda a pergunta (294) e o dono responde "1" (295) no mesmo segundo; o script grava a pergunta cerca de 1 s
    DEPOIS de o laço gravar o "1". A ordem do chat diz que a pergunta veio antes: casa. O teto `enviada_em <=
    recebida_em` cortava esse caso."""
    c, agora = _escolha(tmp_path)
    t0 = agora[0]
    agora[0] = t0 + timedelta(seconds=1)
    c.repo.registrar_enviada("294", "ana", fato="escolha:294:1-2-3")
    agora[0] = t0
    await c.volta(_dono(5, "1", 295))
    assert c.linha(5)["alvo"] == "escolha:294"


@pytest.mark.parametrize("campo", [{"forward_origin": {"type": "user", "date": 1}}, {"forward_date": 1}])
async def test_a_mensagem_encaminhada_nao_casa_com_a_escolha(tmp_path: Path, campo: dict[str, object]) -> None:
    """O dono encaminha um "1" que outra pessoa escreveu: não é a resposta dele. A marca vem da tradução
    (`forward_origin`, ou o `forward_date` da API antiga) e fica gravada com a linha."""
    c, agora = _escolha(tmp_path)
    _pergunta(c, agora, 294)
    u = _dono(5, "1", 295)
    u["message"].update(campo)  # type: ignore[union-attr]
    await c.volta(u)
    linha = c.linha(5)
    assert linha["alvo"] != "escolha:294"
    assert (linha["estado"], linha["erro"]) == ("recusada", ERRO_CURTO_DEMAIS)
    # a pergunta segue aberta: o "1" do próprio dono, logo depois, casa
    await c.volta(_dono(6, "1", 296))
    assert c.linha(6)["alvo"] == "escolha:294"
