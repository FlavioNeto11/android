"""28.15 (ADR-071), correções da revisão do PR #166: o histórico do chat na 1ª subida (B1), a resposta a uma pergunta que
pede credencial (B2), a update que não grava (I3), a prévia que vence (I4), a identidade por `from.id` em chat privado
(I5), o 429 e o 401 do laço (I7) e os menores (linha presa, texto longo com cara de senha, dica do 409).

Prova `simulated`: a mesma Bot API falsa (`httpx.MockTransport`) e as mesmas portas falsas de `test_telegram_entrada.py`;
nenhuma rede, nenhum segredo real (o token e o chat são de teste).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.modules.avisos.infrastructure.entrada import (
    RESPOSTA_CREDENCIAL,
    RESPOSTA_PERGUNTA_CREDENCIAL,
    RESPOSTA_PERGUNTA_CREDENCIAL_SEM_APAGAR,
    RESPOSTA_PRESA,
    RESPOSTA_VENCIDA,
    RecusaDaCentral,
)
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial

from .test_telegram_entrada import CHAT, TOKEN, Cenario, botao, msg

pytestmark = pytest.mark.asyncio

RUN = "r-20261002181523-4985a1"


class Relogio:
    def __init__(self) -> None:
        self.t = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.t

    def avanca(self, segundos: float) -> None:
        self.t += timedelta(seconds=segundos)


def cenario_que_para_no_primeiro_sono(c: Cenario) -> None:
    """O laço é infinito: o sono dele vira o ponto de parada do teste (guarda quanto ele quis dormir)."""
    async def dormir(s: float) -> None:
        c.dormidas.append(s)
        raise asyncio.CancelledError

    c.servico._dormir = dormir


# --------------------------------------------------------------------------- B1: o histórico da 1ª subida
async def test_primeira_subida_descarta_o_acumulo_e_nao_executa_nada(tmp_path: Path) -> None:
    c = Cenario(tmp_path, base=False)
    c.bot.guardadas.extend([msg(3, "/aprovar aa11"), msg(4, "sim"), msg(5, "/vetar bb22")])      # de ontem
    assert await c.volta() == 0
    assert c.portas.nomes() == []                                       # nem aprovou, nem vetou
    gets = [p["offset"] for m, p, _ in c.bot.chamadas if m == "getUpdates"]
    assert gets == ["-1", "6"]                                          # achou a última (5) e passou dela
    assert c.repo.proximo_offset() == 6 and c.bot.guardadas == []
    # Nada do histórico foi gravado: só a linha-marco, sem texto.
    linhas = c.db.query("SELECT id_externo, texto, estado FROM canal_entradas WHERE canal='telegram'")
    assert [(r["id_externo"], r["texto"], r["estado"]) for r in linhas] == [("inicio", None, "ignorada")]
    # E o que chega DEPOIS é tratado normalmente.
    await c.volta(msg(6, "/status"))
    assert c.portas.nomes() == ["status"]


async def test_primeira_subida_com_fila_vazia_nao_descarta_a_primeira_mensagem(tmp_path: Path) -> None:
    c = Cenario(tmp_path, base=False)
    assert await c.volta() == 0
    assert c.repo.proximo_offset() == 0 and not c.repo.canal_vazio()    # a marca tira o canal de "vazio"
    await c.volta(msg(1, "/status"))                                    # chega entre duas voltas, com a fila ainda vazia
    assert c.portas.nomes() == ["status"]
    assert [p["offset"] for m, p, _ in c.bot.chamadas if m == "getUpdates"].count("-1") == 1


async def test_canal_ja_em_uso_nao_descarta_nada(c_em_uso: Cenario) -> None:
    c = c_em_uso
    await c.volta(msg(5, "/status"))
    assert c.portas.nomes() == ["status"]
    assert "-1" not in [p["offset"] for m, p, _ in c.bot.chamadas if m == "getUpdates"]


@pytest.fixture
def c_em_uso(tmp_path: Path) -> Cenario:
    return Cenario(tmp_path)


# --------------------------------------------------------------------------- B2: a pergunta que pede credencial
@pytest.mark.parametrize("texto", ["kiwi2024!", "hunter2", "o segundo"])
@pytest.mark.parametrize("forma", ["reply", "comando"])
async def test_resposta_a_pergunta_de_senha_e_recusada_pelo_contexto(tmp_path: Path, forma: str, texto: str) -> None:
    c = Cenario(tmp_path)
    c.portas.pergunta = "Qual é a senha da conta do Instagram?"
    c.repo.registrar_enviada("777", "aviso", fato=f"run:{RUN}:needs_input")
    entrada = msg(5, texto, reply_to=777) if forma == "reply" else msg(5, f"/responder 4985a1 {texto}")
    await c.volta(entrada)
    linha = c.linha(5)
    assert (linha["estado"], linha["texto"], linha["tamanho"]) == (
        "recusada", None, len(texto) if forma == "reply" else len(f"/responder 4985a1 {texto}"))
    assert "responder" not in c.portas.nomes()                          # nenhuma sucessora, nada em runs.command
    assert RESPOSTA_PERGUNTA_CREDENCIAL in c.bot.textos()
    assert [b for m, _, b in c.bot.chamadas if m == "deleteMessage"] == [{"chat_id": str(CHAT), "message_id": 50}]
    # O texto não ficou em coluna nenhuma do registro.
    resto = c.db.query("SELECT * FROM canal_entradas WHERE canal='telegram'")
    assert texto not in " ".join(str(v) for r in resto for v in dict(r).values())


async def test_pergunta_de_senha_que_nao_se_apaga_pede_ao_dono_que_apague(tmp_path: Path) -> None:
    c = Cenario(tmp_path)
    c.portas.pergunta = "Informe o código de verificação enviado por SMS"
    c.bot.apagar_falha = True
    c.repo.registrar_enviada("777", "aviso", fato=f"run:{RUN}:needs_input")
    await c.volta(msg(5, "kiwi2024!", reply_to=777))
    assert c.linha(5)["erro"] == "a pergunta da execução pede credencial; não apagada do chat"
    assert RESPOSTA_PERGUNTA_CREDENCIAL_SEM_APAGAR in c.bot.textos()
    assert c.bot.mensagens()[-1]["reply_parameters"] == {"message_id": 50, "allow_sending_without_reply": True}
    assert "responder" not in c.portas.nomes()


async def test_pergunta_comum_segue_normal(tmp_path: Path) -> None:
    c = Cenario(tmp_path)
    c.portas.pergunta = "Qual perfil?"
    c.repo.registrar_enviada("777", "aviso", fato=f"run:{RUN}:needs_input")
    await c.volta(msg(5, "o segundo", reply_to=777), msg(6, "/responder 4985a1 o terceiro"))
    assert [c.linha(5)["estado"], c.linha(6)["estado"]] == ["feita", "feita"]
    assert c.linha(5)["texto"] == "o segundo"
    assert [x[:2] for x in c.portas.chamadas if x[0] == "responder"] == [
        ("responder", (RUN, "o segundo")), ("responder", (RUN, "o terceiro"))]
    assert c.bot.chamou("deleteMessage") == 0


async def test_pergunta_que_nao_se_le_recusa_na_duvida(tmp_path: Path) -> None:
    c = Cenario(tmp_path)

    def quebrada(ref: str) -> str:
        raise RuntimeError("banco fora")

    c.portas.pergunta_de = quebrada                                     # type: ignore[method-assign]
    await c.volta(msg(5, "/responder 4985a1 kiwi2024!"))
    assert (c.linha(5)["estado"], c.linha(5)["texto"]) == ("recusada", None)
    assert "responder" not in c.portas.nomes()


async def test_o_que_nao_e_resposta_nao_consulta_a_pergunta(tmp_path: Path) -> None:
    c = Cenario(tmp_path)
    c.portas.pergunta = "Qual a senha?"
    await c.volta(msg(5, "/status"), msg(6, "abra o Chrome no android-09"))
    assert "pergunta_de" not in c.portas.nomes()


@pytest.mark.parametrize("pergunta,pede", [
    ("Qual é a senha da conta?", True), ("Informe o código de verificação", True), ("Digite o token enviado", True),
    ("Precisa de 2FA: qual o código de acesso?", True), ("Informe o PIN", True), ("Please enter your password", True),
    ("Qual perfil?", False), ("Para qual contato?", False), ("Qual aparelho: android-09 ou android-10?", False)])
async def test_o_vocabulario_e_o_da_triagem_de_credencial(pergunta: str, pede: bool) -> None:
    # O canal não tem lista própria: decide com a `TriagemDeCredencial` (a mesma do caminho comum, item 29.52).
    assert TriagemDeCredencial().recusa(pergunta) is pede


async def test_409_credencial_na_resposta_do_caminho_comum_e_final(tmp_path: Path) -> None:
    # A pergunta parece inofensiva e a triagem do canal deixa passar, mas o serviço comum recusa a resposta.
    c = Cenario(tmp_path)
    chamadas: list[str] = []

    def responder(run_id: str, texto: str) -> tuple[str, str]:
        chamadas.append(run_id)
        raise RecusaDaCentral("A resposta parece credencial.", "credencial_na_resposta")

    c.portas.responder = responder                                      # type: ignore[method-assign]
    c.repo.registrar_enviada("777", "aviso", fato=f"run:{RUN}:needs_input")
    await c.volta(msg(5, "kiwi2024!", reply_to=777))
    linha = c.linha(5)
    assert (linha["estado"], linha["texto"]) == ("recusada", None)
    assert linha["erro"] == "a Central recusou a resposta como credencial; apagada do chat"
    assert RESPOSTA_PERGUNTA_CREDENCIAL in c.bot.textos()
    assert [b for m, _, b in c.bot.chamadas if m == "deleteMessage"] == [{"chat_id": str(CHAT), "message_id": 50}]
    assert all("kiwi2024" not in t for t in c.bot.textos())             # nada de eco
    await c.volta()
    await c.volta()
    assert chamadas == [RUN]                                            # sem nova tentativa
    resto = c.db.query("SELECT * FROM canal_entradas WHERE canal='telegram'")
    assert "kiwi2024" not in " ".join(str(v) for r in resto for v in dict(r).values())


async def test_recusa_comum_sem_o_codigo_de_credencial_segue_como_falha_normal(tmp_path: Path) -> None:
    c = Cenario(tmp_path)

    def responder(run_id: str, texto: str) -> tuple[str, str]:
        raise RecusaDaCentral("Só uma execução que espera resposta pode ser respondida.", "invalid_state")

    c.portas.responder = responder                                      # type: ignore[method-assign]
    await c.volta(msg(5, "/responder 4985a1 o segundo"))
    assert (c.linha(5)["estado"], c.linha(5)["texto"]) == ("falhou", "/responder 4985a1 o segundo")
    assert c.bot.chamou("deleteMessage") == 0


# --------------------------------------------------------------------------- I3: a update que não grava
async def test_update_que_nao_grava_nao_trava_a_conversa(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    c = Cenario(tmp_path)
    caplog.set_level(logging.DEBUG)
    original = c.repo.gravar

    def gravar(**kw: object) -> bool:
        if kw.get("texto") == "texto-que-quebra":
            raise RuntimeError("falha do banco com texto-que-quebra")
        return original(**kw)                                           # type: ignore[arg-type]

    c.repo.gravar = gravar                                              # type: ignore[method-assign]
    assert await c.volta(msg(5, "texto-que-quebra"), msg(6, "/status")) == 2
    linha = c.linha(5)
    assert (linha["estado"], linha["texto"]) == ("falhou", None)
    assert c.portas.nomes() == ["status"] and c.linha(6)["estado"] == "feita"       # a válida foi tratada
    assert c.repo.proximo_offset() == 7                                             # o offset andou
    nossos = " | ".join(r.getMessage() for r in caplog.records if not r.name.startswith(("httpx", "httpcore")))
    assert "update 5" in nossos and "texto-que-quebra" not in nossos
    assert await c.volta() == 0                                         # e a 5 não volta a cada volta


# --------------------------------------------------------------------------- I4: a prévia que vence
@pytest.mark.parametrize("espera_s,executa", [(899, True), (901, False)])
async def test_executar_de_previa_velha_nao_cria(tmp_path: Path, espera_s: int, executa: bool) -> None:
    relogio = Relogio()
    c = Cenario(tmp_path, relogio=relogio)
    assert c.cfg.file.avisos.entrada.ttl_previa_s == 900
    await c.volta(msg(5, "/para android-09 abrir o QA Messenger"))
    ident = c.linha(5)["id"]
    relogio.avanca(espera_s)
    await c.volta(botao(6, f"x:{ident}", mid=c.bot.mid))
    if executa:
        assert c.portas.nomes().count("criar") == 1 and c.linha(5)["estado"] == "feita"
    else:
        assert "criar" not in c.portas.nomes()
        assert (c.linha(5)["estado"], c.linha(5)["erro"]) == ("cancelada", "prévia venceu")
        assert RESPOSTA_VENCIDA in c.bot.textos()
        # Mandar de novo gera outra prévia, que vale.
        await c.volta(msg(7, "/para android-09 abrir o QA Messenger"))
        await c.volta(botao(8, f"x:{c.linha(7)['id']}", mid=c.bot.mid))
        assert c.portas.nomes().count("criar") == 1


# --------------------------------------------------------------------------- I5: a identidade
async def test_grupo_com_o_mesmo_id_nao_e_o_dono(tmp_path: Path) -> None:
    c = Cenario(tmp_path)
    await c.volta(msg(5, "/aprovar aa11", tipo="supergroup"), msg(6, "/status", tipo="group"),
                  msg(7, "/status", tipo="channel"))
    for uid in (5, 6, 7):
        linha = c.linha(uid)
        assert (linha["do_dono"], linha["texto"], linha["estado"]) == (0, None, "ignorada")
    assert c.portas.nomes() == [] and len(c.bot.mensagens()) == 1          # só a /ajuda da 1ª subida


async def test_outro_membro_ou_sem_from_nao_e_o_dono(tmp_path: Path) -> None:
    c = Cenario(tmp_path)
    sem_from = msg(6, "/status")
    del sem_from["message"]["from"]                                     # type: ignore[attr-defined,index]
    await c.volta(msg(5, "/status", autor=777), sem_from)
    assert [c.linha(5)["estado"], c.linha(6)["estado"]] == ["ignorada", "ignorada"]
    assert c.linha(5)["texto"] is None and c.portas.nomes() == []


async def test_botao_de_outro_from_id_nao_executa(tmp_path: Path) -> None:
    c = Cenario(tmp_path)
    await c.volta(msg(5, "/para android-09 abrir o QA Messenger"))
    ident = c.linha(5)["id"]
    await c.volta(botao(6, f"x:{ident}", mid=c.bot.mid, autor=777),            # um membro do grupo toca o botão
                  botao(7, f"x:{ident}", mid=c.bot.mid, tipo="supergroup"))
    assert "criar" not in c.portas.nomes() and c.linha(5)["estado"] == "pergunta"
    assert c.linha(6)["estado"] == "ignorada" and c.linha(7)["estado"] == "ignorada"
    await c.volta(botao(8, f"x:{ident}", mid=c.bot.mid))                       # o dono, sim
    assert c.portas.nomes().count("criar") == 1


# --------------------------------------------------------------------------- I7: o 429 e o 401 do laço
async def test_429_honra_o_retry_after(tmp_path: Path) -> None:
    c = Cenario(tmp_path)
    c.bot.falha = (429, {"ok": False, "description": "Too Many Requests: retry after 17",
                         "parameters": {"retry_after": 17}})
    cenario_que_para_no_primeiro_sono(c)
    with pytest.raises(asyncio.CancelledError):
        await c.servico.laco()
    assert c.dormidas == [17.0]
    assert c.servico.problemas() == []                                  # limite de taxa não é problema de configuração


@pytest.mark.parametrize("codigo", [401, 403])
async def test_erro_definitivo_vira_problema_e_espera_como_o_409(tmp_path: Path, codigo: int) -> None:
    c = Cenario(tmp_path)
    c.bot.falha = (codigo, {"ok": False, "description": "Unauthorized"})
    cenario_que_para_no_primeiro_sono(c)
    with pytest.raises(asyncio.CancelledError):
        await c.servico.laco()
    assert c.dormidas == [c.cfg.file.avisos.entrada.espera_conflito_s]  # não martela a cada 5 s
    [problema] = c.servico.problemas()
    assert problema.code == "telegram_entrada_recusada" and f"({codigo})" in problema.message
    assert TOKEN not in problema.message + problema.hint
    # Corrigido o token, a próxima volta limpa o problema.
    c.bot.falha = None
    await c.volta()
    assert c.servico.problemas() == []


async def test_falha_de_rede_tenta_de_novo_logo(tmp_path: Path) -> None:
    c = Cenario(tmp_path)
    c.bot.falha = (502, {"ok": False, "description": "Bad Gateway"})
    cenario_que_para_no_primeiro_sono(c)
    with pytest.raises(asyncio.CancelledError):
        await c.servico.laco()
    assert c.dormidas == [5.0] and c.servico.problemas() == []


# --------------------------------------------------------------------------- menores
async def test_linha_presa_em_executando_e_reparada(tmp_path: Path) -> None:
    relogio = Relogio()
    c = Cenario(tmp_path, relogio=relogio)
    await c.volta(msg(5, "/para android-09 abrir o QA Messenger"))
    ident = c.linha(5)["id"]
    assert c.repo.marcar(int(str(ident)), "executando", de=("pergunta",))      # a queda foi aqui, antes do `criar`
    relogio.avanca(120)
    await c.volta()
    assert c.linha(5)["estado"] == "executando"                                # ainda dentro da folga
    relogio.avanca(200)
    await c.volta()
    assert (c.linha(5)["estado"], c.linha(5)["erro"]) == ("falhou", "interrompida antes de criar a execução")
    assert RESPOSTA_PRESA in c.bot.textos() and "criar" not in c.portas.nomes()
    await c.volta()
    assert c.bot.textos().count(RESPOSTA_PRESA) == 1                           # uma vez só


async def test_texto_longo_com_cara_de_senha_tambem_e_apagado(tmp_path: Path) -> None:
    c = Cenario(tmp_path)
    longo = "minha senha é " + "Abc!2345xyz " * 120
    await c.volta(msg(5, longo))
    linha = c.linha(5)
    assert (linha["estado"], linha["texto"], linha["tamanho"]) == ("recusada", None, len(longo))
    assert [b for m, _, b in c.bot.chamadas if m == "deleteMessage"] == [{"chat_id": str(CHAT), "message_id": 50}]
    assert RESPOSTA_CREDENCIAL in c.bot.textos()


async def test_dica_do_409_cita_webhook_e_o_script_de_descoberta(tmp_path: Path) -> None:
    c = Cenario(tmp_path)
    c.bot.conflito = True
    await c.volta()
    [problema] = c.servico.problemas()
    assert "webhook" in problema.hint and "avisos-telegram.py descobrir" in problema.hint
