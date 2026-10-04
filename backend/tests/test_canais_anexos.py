"""28.24 (F1): anexos nos canais. O Telegram recebe foto, PDF e texto do DONO (guardados em `data/anexos` pelo sha256, com o
tipo conferido pelo conteúdo e o teto de bytes), devolve arquivo por referência, e a faxina do 28.16 os apaga.

Prova `simulated` (`arquivo::teste`): Bot API falsa por `httpx.MockTransport` (inclui `getFile`, o download e o multipart
do `sendPhoto`/`sendDocument`), portas falsas no lugar dos serviços do painel e SQLite; nenhuma rede, nenhum bot real.
O `AppState` do harness serve só a rota de leitura. Nada aqui lê imagem por IA (isso é da F2).
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import traceback
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from app.config import AnexosDaEntradaCfg, Config
from app.db import Database
from app.main import create_app
from app.modules.avisos.adapters.telegram import CanalTelegram
from app.modules.avisos.application.entrega import FalhaDeEnvio
from app.modules.avisos.domain.anexos import (
    AnexoGrandeDemais,
    AnexoRecebido,
    detectar_mime,
    normalizar_mime,
    sha256_valido,
    tamanho_legivel,
)
from app.modules.avisos.infrastructure.anexos import AnexoRecusado, ArmazemDeAnexos, CaminhoForaDoArmazem, caminho_em
from app.modules.avisos.infrastructure.entrada import (
    MOTIVO_TIPO_FORA,
    ConversaDoCanal,
    Recebida,
    SaidaDoTelegram,
    ServicoDeEntrada,
    _ler_update,
)
from app.modules.avisos.infrastructure.entrada_sql import EntradasDoCanal
from app.modules.avisos.infrastructure.faxina_sql import FaxinaDosCanais
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.util import now

from .conftest import Harness, make_config
from .test_telegram_entrada import CHAT, TOKEN, BotFalso, PortasFalsas, msg

pytestmark = pytest.mark.asyncio

JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x01" * 120
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x02" * 60
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x03" * 30
PDF = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\n"
TEXTO = "log do bug: a tela travou depois do login\n".encode()
EXE = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff\x00\x00"


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class Corpo(httpx.AsyncByteStream):
    """Um corpo que só gera os pedaços quando alguém os PEDE: `pedidos` prova que o download parou no teto."""

    def __init__(self, pedacos: int, tamanho: int = 1024) -> None:
        self.pedacos, self.tamanho, self.pedidos = pedacos, tamanho, 0

    async def __aiter__(self):  # noqa: ANN204
        for _ in range(self.pedacos):
            self.pedidos += 1
            yield b"\x41" * self.tamanho


class BotComArquivos(BotFalso):
    """A Bot API de `test_telegram_entrada` + `getFile`, o download (`/file/bot<token>/…`) e o multipart do envio."""

    def __init__(self) -> None:
        super().__init__()
        self.arquivos: dict[str, bytes | Corpo] = {}
        self.tamanho_informado: dict[str, int | None] = {}
        self.downloads: list[str] = []
        self.envios: list[dict[str, object]] = []
        self.falha_download: Exception | None = None
        self.getfile_falha: tuple[int, dict[str, object]] | None = None
        self.recusa_foto = False

    def handler(self, req: httpx.Request) -> httpx.Response:
        caminho = req.url.path
        if "/file/bot" in caminho:
            fp = caminho.split("/", 3)[3]
            self.downloads.append(fp)
            if self.falha_download is not None:
                raise self.falha_download
            dados = self.arquivos[fp.removeprefix("docs/")]
            return httpx.Response(200, stream=dados) if isinstance(dados, Corpo) else httpx.Response(200, content=dados)
        metodo = caminho.rsplit("/", 1)[-1]
        if metodo == "getFile":
            corpo = json.loads(req.content)
            self.chamadas.append(("getFile", {}, corpo))
            if self.getfile_falha is not None:
                return httpx.Response(self.getfile_falha[0], json=self.getfile_falha[1])
            fid = corpo["file_id"]
            dados = self.arquivos.get(fid)
            if dados is None:
                return httpx.Response(400, json={"ok": False, "description": "Bad Request: wrong file_id"})
            tam = self.tamanho_informado.get(fid, len(dados) if isinstance(dados, bytes) else None)
            res: dict[str, object] = {"file_id": fid, "file_path": f"docs/{fid}"}
            if tam is not None:
                res["file_size"] = tam
            return httpx.Response(200, json={"ok": True, "result": res})
        if metodo in ("sendPhoto", "sendDocument"):
            self.chamadas.append((metodo, {}, {}))
            self.envios.append({"metodo": metodo, "corpo": req.content, "tipo": req.headers.get("content-type", "")})
            if metodo == "sendPhoto" and self.recusa_foto:
                return httpx.Response(400, json={"ok": False, "description": "Bad Request: PHOTO_INVALID_DIMENSIONS"})
            self.mid += 1
            return httpx.Response(200, json={"ok": True, "result": {"message_id": self.mid}})
        return super().handler(req)


def foto(uid: int, file_id: str = "foto-g", *, legenda: str | None = None, chat: int = CHAT,
         tamanho: int | None = None) -> dict[str, object]:
    u = msg(uid, "", chat=chat)
    m = u["message"]
    del m["text"]                                                                  # type: ignore[attr-defined]
    m["photo"] = [{"file_id": "foto-p", "file_size": 10, "width": 90, "height": 90},      # type: ignore[index]
                  {"file_id": file_id, "file_size": tamanho or 500, "width": 800, "height": 800},
                  {"file_id": "foto-m", "file_size": 200, "width": 320, "height": 320}]
    if legenda is not None:
        m["caption"] = legenda                                                     # type: ignore[index]
    return u


def documento(uid: int, file_id: str, *, mime: str | None, nome: str = "relatorio.bin", tamanho: int | None = None,
              legenda: str | None = None, chat: int = CHAT) -> dict[str, object]:
    u = msg(uid, "", chat=chat)
    m = u["message"]
    del m["text"]                                                                  # type: ignore[attr-defined]
    doc: dict[str, object] = {"file_id": file_id, "file_name": nome}
    if mime is not None:
        doc["mime_type"] = mime
    if tamanho is not None:
        doc["file_size"] = tamanho
    m["document"] = doc                                                            # type: ignore[index]
    if legenda is not None:
        m["caption"] = legenda                                                     # type: ignore[index]
    return u


class CenarioAnexos:
    def __init__(self, tmp_path: Path, *, anexos: bool = True) -> None:
        self.cfg: Config = make_config(tmp_path)
        self.cfg.ensure_dirs()
        self.cfg.file.avisos.enabled = True
        self.cfg.file.avisos.entrada.enabled = True
        self.cfg.env.telegram_bot_token = SecretStr(TOKEN)
        self.cfg.env.telegram_chat_id = SecretStr(str(CHAT))
        self.db = Database(self.cfg.db_dsn)
        self.db.migrate()
        self.bot = BotComArquivos()
        self.portas = PortasFalsas()
        self.repo = EntradasDoCanal(self.db)
        self.repo.gravar_inicio(None)
        # A ajuda inicial já saiu: o chat do dono só recebe o que o teste provoca.
        self.db.execute("INSERT INTO canal_enviadas(canal, ref_mensagem, origem, enviada_em)"
                        " VALUES ('telegram', '1', 'ajuda', '2026-10-04T00:00:00Z')")
        self.pasta = self.cfg.data_dir / "anexos"
        self.armazem = ArmazemDeAnexos(self.db, self.pasta)
        triagem = TriagemDeCredencial()
        self.triagem = triagem
        self.canal = CanalTelegram(TOKEN, str(CHAT),
                                   client=httpx.AsyncClient(transport=httpx.MockTransport(self.bot.handler)))

        async def dormir(_s: float) -> None:
            return None

        self.servico = ServicoDeEntrada(self.cfg, self.repo, self.portas, lider=lambda _n: 1, recusa=triagem.recusa,
                                        redigir=triagem.redigir, canal=self.canal, dormir=dormir,
                                        anexos=self.armazem if anexos else None)

    async def volta(self, *updates: dict[str, object]) -> int:
        self.bot.guardadas.extend(updates)
        return await self.servico.uma_volta()

    def linhas(self) -> list[dict[str, object]]:
        return [dict(r) for r in self.db.query("SELECT * FROM canal_anexos ORDER BY id")]

    def arquivos(self) -> list[Path]:
        return sorted(p for p in self.pasta.rglob("*") if p.is_file()) if self.pasta.exists() else []

    def ultima(self) -> str:
        return self.bot.textos()[-1]

    def entrada(self, update_id: int) -> dict[str, object]:
        r = self.db.one("SELECT * FROM canal_entradas WHERE canal='telegram' AND id_externo=?", (str(update_id),))
        assert r is not None
        return dict(r)

    def conversa(self) -> ConversaDoCanal:
        return self.servico.conversa


@pytest.fixture
def c(tmp_path: Path) -> CenarioAnexos:
    return CenarioAnexos(tmp_path)


# ---------------------------------------------------------------- a entrada: o dono manda
async def test_foto_do_dono_e_guardada_pelo_sha_e_respondida(c: CenarioAnexos) -> None:
    """A foto vem em várias resoluções: baixa a MAIOR, guarda em `<2>/<sha256>.jpg`, registra e responde curto."""
    c.bot.arquivos["foto-g"] = JPEG
    assert await c.volta(foto(5, "foto-g")) == 1
    esperado = c.pasta / sha(JPEG)[:2] / f"{sha(JPEG)}.jpg"
    assert c.arquivos() == [esperado] and esperado.read_bytes() == JPEG
    [linha] = c.linhas()
    assert (linha["estado"], linha["direcao"], linha["mime"], linha["sha256"], linha["bytes"]) == (
        "guardado", "entrada", "image/jpeg", sha(JPEG), len(JPEG))
    assert linha["entrada_id"] == c.entrada(5)["id"] and linha["motivo_recusa"] is None
    assert [p["file_id"] for m, _, p in c.bot.chamadas if m == "getFile"] == ["foto-g"]     # a maior, não a 1ª nem a última
    assert c.ultima() == f"Recebi a imagem ({tamanho_legivel(len(JPEG))}). Guardei na Central (anexo {linha['id']})."
    assert c.entrada(5)["estado"] == "ignorada" and c.portas.nomes() == []                  # sem legenda: nada é pedido


async def test_a_legenda_vale_como_texto_do_comando(c: CenarioAnexos) -> None:
    c.bot.arquivos["foto-g"] = JPEG
    c.bot.arquivos["doc-1"] = PDF
    await c.volta(foto(5, "foto-g", legenda="/status"))
    assert c.portas.nomes() == ["status"] and c.entrada(5)["texto"] == "/status"
    [linha] = c.linhas()
    assert linha["mime"] == "image/jpeg" and linha["entrada_id"] == c.entrada(5)["id"]
    await c.volta(documento(6, "doc-1", mime="application/pdf", legenda="/status"))
    assert c.portas.nomes() == ["status", "status"]


async def test_documento_pdf_aceito(c: CenarioAnexos) -> None:
    c.bot.arquivos["doc-1"] = PDF
    await c.volta(documento(5, "doc-1", mime="application/pdf", nome="../../relatorio.pdf"))
    esperado = c.pasta / sha(PDF)[:2] / f"{sha(PDF)}.pdf"
    assert c.arquivos() == [esperado]
    assert c.ultima().startswith("Recebi o PDF (")
    # O nome que o remetente deu NUNCA entra: nem no disco, nem no banco, nem na resposta.
    tudo = json.dumps(c.linhas()) + c.ultima() + str(esperado)
    assert "relatorio" not in tudo and ".." not in c.arquivos()[0].relative_to(c.pasta).as_posix()


async def test_texto_e_mime_generico_sem_declaracao(c: CenarioAnexos) -> None:
    """`application/octet-stream` e a falta de mime não são declaração: vale o que o conteúdo mostrar."""
    c.bot.arquivos.update({"t-1": TEXTO, "w-1": WEBP})
    await c.volta(documento(5, "t-1", mime="text/plain; charset=utf-8"), documento(6, "w-1", mime=None),
                  documento(7, "t-1", mime="application/octet-stream"))
    assert [l["mime"] for l in c.linhas()] == ["text/plain", "image/webp", "text/plain"]
    assert sorted(p.suffix for p in c.arquivos()) == [".txt", ".webp"]                       # o texto repetido é um arquivo só


@pytest.mark.parametrize(("declarado", "conteudo"), [
    ("image/png", EXE),                        # o executável que se diz PNG: não é de nenhum tipo da lista
    ("text/plain", PNG),                       # tipo da lista, mas o conteúdo é outro
    ("application/pdf", JPEG),
    ("image/jpeg", b""),                       # vazio
])
async def test_mime_falso_ou_divergente_e_recusado_e_nada_vai_ao_disco(c: CenarioAnexos, declarado: str,
                                                                       conteudo: bytes) -> None:
    c.bot.arquivos["doc-x"] = conteudo
    await c.volta(documento(5, "doc-x", mime=declarado, nome="foto.png"))
    [linha] = c.linhas()
    assert linha["estado"] == "recusado" and linha["sha256"] is None and linha["motivo_recusa"]
    assert c.arquivos() == []
    assert c.ultima().startswith("Não guardei o anexo: ") and "foto.png" not in c.ultima()


async def test_tipo_declarado_fora_da_lista_e_recusado_antes_de_baixar(c: CenarioAnexos) -> None:
    c.bot.arquivos["doc-z"] = b"PK\x03\x04"
    await c.volta(documento(5, "doc-z", mime="application/zip"))
    assert [l["estado"] for l in c.linhas()] == ["recusado"] and c.bot.chamou("getFile") == 0 and c.bot.downloads == []
    assert c.ultima() == "Não guardei o anexo: " + MOTIVO_TIPO_FORA


@pytest.mark.parametrize(("campo", "trecho"), [
    ("voice", "voz"), ("video", "vídeo"), ("video_note", "vídeo"), ("audio", "áudio"), ("sticker", "figurinha"),
    ("animation", "animação"),
])
async def test_voz_video_figurinha_viram_anexo_recusado_com_motivo(c: CenarioAnexos, campo: str, trecho: str) -> None:
    u = msg(5, "")
    del u["message"]["text"]                                                       # type: ignore[attr-defined]
    u["message"][campo] = {"file_id": "x", "file_size": 10}                        # type: ignore[index]
    if campo == "animation":                                                       # o GIF traz `document` também
        u["message"]["document"] = {"file_id": "x", "mime_type": "video/mp4"}      # type: ignore[index]
    await c.volta(u)
    [linha] = c.linhas()                                                           # uma linha só, mesmo no GIF
    assert linha["estado"] == "recusado" and trecho in str(linha["motivo_recusa"])
    assert c.bot.chamou("getFile") == 0 and c.arquivos() == []
    assert c.ultima().startswith("Não guardei o anexo: ")


async def test_acima_do_teto_declarado_nao_chama_nem_o_getfile(c: CenarioAnexos) -> None:
    c.cfg.file.avisos.entrada.anexos.max_bytes = 4096
    c.bot.arquivos["grande"] = b"\xff\xd8\xff" + b"x" * 100
    await c.volta(foto(5, "grande", tamanho=5000))
    assert c.bot.chamou("getFile") == 0 and c.bot.downloads == [] and c.arquivos() == []
    [linha] = c.linhas()
    assert linha["estado"] == "recusado" and "grande demais" in str(linha["motivo_recusa"])
    assert c.ultima() == "Não guardei o anexo: o arquivo é grande demais (o limite é 4 KB)."


async def test_o_getfile_que_informa_tamanho_acima_do_teto_nao_baixa(c: CenarioAnexos) -> None:
    c.cfg.file.avisos.entrada.anexos.max_bytes = 4096
    c.bot.arquivos["g"] = JPEG
    c.bot.tamanho_informado["g"] = 9000                                            # a mensagem não dizia; o getFile diz
    await c.volta(foto(5, "g"))
    assert c.bot.chamou("getFile") == 1 and c.bot.downloads == []
    assert [l["estado"] for l in c.linhas()] == ["recusado"] and c.arquivos() == []


async def test_acima_do_teto_durante_o_download_para_de_ler(c: CenarioAnexos) -> None:
    """Mentiu no tamanho (disse 1 KB, manda 4 MB, sem Content-Length): a leitura PARA no teto, sem trazer tudo."""
    c.cfg.file.avisos.entrada.anexos.max_bytes = 4096
    corpo = Corpo(pedacos=4096, tamanho=1024)                                      # 4 MB no total
    c.bot.arquivos["mentiroso"] = corpo
    c.bot.tamanho_informado["mentiroso"] = 1000
    await c.volta(foto(5, "mentiroso", tamanho=1000))
    assert 0 < corpo.pedidos <= 6, corpo.pedidos                                   # 4 KB de teto = 4 pedaços e mais um
    [linha] = c.linhas()
    assert linha["estado"] == "recusado" and "grande demais" in str(linha["motivo_recusa"]) and c.arquivos() == []


async def test_releitura_da_mesma_update_nao_baixa_nem_responde_de_novo(c: CenarioAnexos) -> None:
    c.bot.arquivos["foto-g"] = JPEG
    await c.volta(foto(5, "foto-g"))
    respostas, downloads = len(c.bot.mensagens()), len(c.bot.downloads)
    canal = c.servico.canal()
    assert canal is not None
    r = _ler_update(foto(5, "foto-g"), str(CHAT))
    assert r is not None and r.anexos
    await c.servico.registrar(SaidaDoTelegram(canal), r)                            # a mesma update, relida
    assert len(c.bot.mensagens()) == respostas and len(c.bot.downloads) == downloads and len(c.linhas()) == 1


async def test_o_mesmo_conteudo_e_um_arquivo_e_duas_linhas(c: CenarioAnexos) -> None:
    c.bot.arquivos["a"] = JPEG
    c.bot.arquivos["b"] = JPEG
    await c.volta(foto(5, "a"), foto(6, "b"))
    assert len(c.arquivos()) == 1 and [l["entrada_id"] for l in c.linhas()] == [c.entrada(5)["id"], c.entrada(6)["id"]]


async def test_anexos_desligados_recusam_com_o_motivo(c: CenarioAnexos) -> None:
    c.cfg.file.avisos.entrada.anexos.enabled = False
    c.bot.arquivos["foto-g"] = JPEG
    await c.volta(foto(5, "foto-g"))
    assert c.bot.chamou("getFile") == 0 and [l["estado"] for l in c.linhas()] == ["recusado"] and c.arquivos() == []
    assert "desligados" in c.ultima()


async def test_so_o_dono_tem_anexo_baixado_outro_chat_fica_sem_download(c: CenarioAnexos) -> None:
    c.bot.arquivos["foto-g"] = JPEG
    await c.volta(foto(5, "foto-g", chat=999))                                      # convidados desligados: como no 28.15
    assert c.bot.chamou("getFile") == 0 and c.linhas() == [] and c.arquivos() == []
    assert c.entrada(5)["estado"] == "ignorada" and c.entrada(5)["texto"] is None


async def test_legenda_com_cara_de_credencial_nao_baixa_o_arquivo(c: CenarioAnexos) -> None:
    c.bot.arquivos["foto-g"] = JPEG
    await c.volta(foto(5, "foto-g", legenda="minha senha é Abc!2345xyz"))
    assert c.entrada(5)["estado"] == "recusada" and c.bot.chamou("getFile") == 0 and c.linhas() == []


async def test_a_falha_ao_baixar_e_dita_ao_dono_e_nao_derruba_a_mensagem(c: CenarioAnexos) -> None:
    c.bot.arquivos["foto-g"] = JPEG
    c.bot.falha_download = httpx.ConnectError("sem rede")
    await c.volta(foto(5, "foto-g"))
    assert [l["estado"] for l in c.linhas()] == ["recusado"] and "Mande de novo" in c.ultima()
    assert c.entrada(5)["estado"] == "ignorada"                                     # a linha andou: o offset não trava


async def test_sem_armazem_o_anexo_e_recusado_com_o_motivo(tmp_path: Path) -> None:
    c = CenarioAnexos(tmp_path, anexos=False)
    c.bot.arquivos["foto-g"] = JPEG
    await c.volta(foto(5, "foto-g"))
    assert c.bot.chamou("getFile") == 0 and c.linhas() == [] and "não baixa anexos" in c.ultima()


async def test_saida_que_nao_baixa_recusa_sem_erro(c: CenarioAnexos) -> None:
    """O canal sem `baixar_anexo` (o Trello, hoje) recebe o anexo como recusado, e não como erro."""
    respostas: list[str] = []

    class SaidaSimples:
        async def responder(self, texto: str, *, responde_a: str | None = None, botoes: object = None) -> str | None:
            respostas.append(texto)
            return "77"

        async def confirmar_botao(self, botao_id: str) -> None: ...
        async def tirar_botoes(self, ref_mensagem: str) -> None: ...
        async def apagar(self, ref_mensagem: str) -> bool:
            return False

    r = Recebida(id_externo="900", ordem=900, tipo="mensagem", do_dono=True, texto="", ref_mensagem="9",
                 anexos=(AnexoRecebido("imagem", "image/jpeg", 10, "x"),))
    await c.conversa().registrar(r, SaidaSimples())                                 # type: ignore[arg-type]
    assert respostas == ["Não guardei o anexo: este canal não baixa anexos."]
    assert [l["estado"] for l in c.linhas()] == ["recusado"]


# ---------------------------------------------------------------- a tradução da update
async def test_ler_update_pega_a_maior_foto_a_legenda_e_nao_le_o_nome() -> None:
    u = foto(5, "foto-g", legenda="olha isso")
    r = _ler_update(u, str(CHAT))
    assert r is not None and r.do_dono and r.texto == "olha isso"
    assert r.anexos == (AnexoRecebido("imagem", "image/jpeg", 500, "foto-g"),)
    d = _ler_update(documento(6, "d-1", mime="application/pdf", nome="segredo.pdf", tamanho=77), str(CHAT))
    assert d is not None and d.anexos == (AnexoRecebido("documento", "application/pdf", 77, "d-1"),)
    assert "segredo" not in repr(d.anexos)


async def test_ler_update_de_botao_e_de_texto_nao_tem_anexo() -> None:
    r = _ler_update(msg(5, "oi"), str(CHAT))
    assert r is not None and r.anexos == () and r.texto == "oi"


# ---------------------------------------------------------------- convidado
async def test_convidado_recebe_a_recusa_uma_vez_e_nada_e_baixado(tmp_path: Path) -> None:
    from app.modules.avisos.infrastructure.contatos_sql import ContatosDoCanal
    from app.modules.avisos.infrastructure.convidados import SEM_ANEXO, ConvidadosDoTelegram
    c = CenarioAnexos(tmp_path)
    c.cfg.file.avisos.entrada.convidados.enabled = True
    triagem = c.triagem
    c.servico.convidados = ConvidadosDoTelegram(c.cfg, ContatosDoCanal(c.db), avisar_dono=lambda _a: True,
                                                recusa=triagem.recusa, redigir=triagem.redigir, status=lambda: "ok")
    c.bot.arquivos["foto-g"] = JPEG
    u = foto(5, "foto-g", chat=777)
    u["message"]["from"] = {"id": 777, "first_name": "Fulana", "is_bot": False}     # type: ignore[index]
    await c.volta(u)
    assert c.bot.textos().count(SEM_ANEXO) == 1                                     # uma vez por mensagem
    assert c.bot.chamou("getFile") == 0 and c.bot.downloads == [] and c.linhas() == [] and c.arquivos() == []
    assert c.entrada(5)["estado"] == "convidado" and c.entrada(5)["texto"] is None
    await c.volta()                                                                 # releitura/nova volta: sem repetir
    assert c.bot.textos().count(SEM_ANEXO) == 1


# ---------------------------------------------------------------- o token nunca aparece
async def test_o_token_nao_aparece_em_log_erro_banco_nem_resposta(c: CenarioAnexos,
                                                                  caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    c.bot.arquivos.update({"ok": JPEG, "cai": JPEG, "get-recusa": JPEG})
    await c.volta(foto(5, "ok"))
    # A rede cai no download: a exceção do httpx traz a URL (com o token) na mensagem.
    c.bot.falha_download = httpx.ConnectError(f"conexão recusada em https://api.telegram.org/file/bot{TOKEN}/docs/cai")
    await c.volta(foto(6, "cai"))
    c.bot.falha_download = None
    # O Telegram recusa o getFile e a descrição ecoa o token (a descrição passa por `_limpar`).
    c.bot.getfile_falha = (400, {"ok": False, "description": f"Bad Request: bot{TOKEN} não achou o arquivo"})
    await c.volta(foto(7, "get-recusa"))
    nossos = " | ".join(logging.Formatter().format(r) for r in caplog.records
                        if not r.name.startswith(("httpx", "httpcore")))
    assert "telegram: anexo não baixado" in nossos                                  # os caminhos de falha foram exercitados
    assert TOKEN not in nossos and TOKEN.split(":")[1] not in nossos
    banco = json.dumps([dict(r) for t in ("canal_anexos", "canal_entradas", "canal_enviadas")
                        for r in c.db.query(f"SELECT * FROM {t}")], default=str)  # noqa: S608 - tabelas fixas
    assert TOKEN not in banco and "file/bot" not in banco
    assert all(TOKEN not in t for t in c.bot.textos())


async def test_o_erro_do_adaptador_nao_leva_a_url_do_download(c: CenarioAnexos) -> None:
    c.bot.arquivos["ok"] = JPEG
    c.bot.falha_download = httpx.ReadError(f"falha em https://api.telegram.org/file/bot{TOKEN}/docs/ok")
    with pytest.raises(FalhaDeEnvio) as e:
        await c.canal.baixar_anexo("ok", 10_000)
    completo = "".join(traceback.format_exception(e.value)) + repr(e.value) + e.value.motivo
    assert TOKEN not in completo and "file/bot" not in completo and e.value.motivo.endswith("(ReadError)")
    c.bot.falha_download = None
    c.bot.getfile_falha = (404, {"ok": False, "description": f"Not Found {TOKEN}"})
    with pytest.raises(FalhaDeEnvio) as e2:
        await c.canal.baixar_anexo("ok", 10_000)
    assert TOKEN not in e2.value.motivo and e2.value.definitiva


@pytest.mark.parametrize("caminho", ["../../etc/passwd", "/etc/passwd", "a/../b", "https://x/y", "a b", "a?b=1", ""])
async def test_file_path_estranho_do_telegram_e_recusado_antes_de_montar_o_endereco(c: CenarioAnexos,
                                                                                     caminho: str) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert "/file/" not in req.url.path, req.url.path                           # nenhum download foi tentado
        return httpx.Response(200, json={"ok": True, "result": {"file_id": "x", "file_path": caminho}})

    canal = CanalTelegram(TOKEN, str(CHAT), client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(FalhaDeEnvio) as e:
        await canal.baixar_anexo("x", 1000)
    assert TOKEN not in e.value.motivo


async def test_content_length_acima_do_teto_recusa_sem_ler_o_corpo(c: CenarioAnexos) -> None:
    corpo = Corpo(pedacos=100)

    def handler(req: httpx.Request) -> httpx.Response:
        if "/file/" in req.url.path:
            return httpx.Response(200, headers={"content-length": "102400"}, stream=corpo)
        return httpx.Response(200, json={"ok": True, "result": {"file_id": "x", "file_path": "docs/x"}})

    canal = CanalTelegram(TOKEN, str(CHAT), client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(AnexoGrandeDemais):
        await canal.baixar_anexo("x", 2048)
    assert corpo.pedidos == 0


# ---------------------------------------------------------------- a saída
async def test_enviar_anexo_por_id_e_por_sha_so_do_que_esta_guardado(c: CenarioAnexos) -> None:
    c.bot.arquivos["d-png"] = PNG
    await c.volta(documento(5, "d-png", mime="image/png"))
    [entrada] = c.linhas()
    saida = SaidaDoTelegram(c.canal)
    mid = await c.conversa().enviar_anexo(saida, entrada["id"], "captura da tela")           # type: ignore[arg-type]
    assert mid is not None
    [envio] = c.bot.envios
    assert envio["metodo"] == "sendPhoto" and str(envio["tipo"]).startswith("multipart/form-data")
    corpo = bytes(envio["corpo"])                                                   # type: ignore[arg-type]
    assert PNG in corpo and f'filename="anexo-{sha(PNG)[:12]}.png"'.encode() in corpo and b"captura da tela" in corpo
    # por sha256, e como string de id
    await c.conversa().enviar_anexo(saida, sha(PNG), responde_a="50")
    await c.conversa().enviar_anexo(saida, str(entrada["id"]))
    assert len(c.bot.envios) == 3 and b"reply_parameters" in bytes(c.bot.envios[1]["corpo"])   # type: ignore[arg-type]
    saidas = [l for l in c.linhas() if l["direcao"] == "saida"]
    assert len(saidas) == 3 and {l["sha256"] for l in saidas} == {sha(PNG)} and len(c.arquivos()) == 1
    assert c.repo.enviada(str(mid)) is not None and c.repo.enviada(str(mid))["origem"] == "anexo"   # type: ignore[index]


async def test_enviar_anexo_recusa_caminho_fora_de_data_anexos(c: CenarioAnexos, tmp_path: Path) -> None:
    fora = tmp_path / "fora.png"
    fora.write_bytes(PNG)
    c.pasta.mkdir(parents=True, exist_ok=True)
    saida = SaidaDoTelegram(c.canal)
    for ref in (fora, str(fora), c.pasta / ".." / "fora.png", str(c.pasta / ".." / "fora.png"), Path("fora.png")):
        with pytest.raises(CaminhoForaDoArmazem):
            await c.conversa().enviar_anexo(saida, ref)                              # type: ignore[arg-type]
    assert c.bot.envios == [] and c.bot.chamou("sendPhoto") == 0
    # um id ou sha que não existem também não viram caminho
    for ref in (999, "999", sha(b"nao existe")):
        with pytest.raises(CaminhoForaDoArmazem):
            await c.conversa().enviar_anexo(saida, ref)                              # type: ignore[arg-type]
    assert c.bot.envios == []


async def test_enviar_anexo_recusa_link_simbolico_para_fora(c: CenarioAnexos, tmp_path: Path) -> None:
    fora = tmp_path / "fora.png"
    fora.write_bytes(PNG)
    c.pasta.mkdir(parents=True, exist_ok=True)
    elo = c.pasta / "elo.png"
    try:
        elo.symlink_to(fora)
    except OSError:
        pytest.skip("sem permissão para criar link simbólico neste Windows")
    with pytest.raises(CaminhoForaDoArmazem):
        await c.conversa().enviar_anexo(SaidaDoTelegram(c.canal), elo)               # type: ignore[arg-type]
    assert c.bot.envios == []


async def test_enviar_anexo_aceita_arquivo_dentro_do_armazem_e_confere_o_conteudo(c: CenarioAnexos) -> None:
    c.pasta.mkdir(parents=True, exist_ok=True)
    dentro = c.pasta / "gerado.png"
    dentro.write_bytes(PNG)
    await c.conversa().enviar_anexo(SaidaDoTelegram(c.canal), dentro)                # type: ignore[arg-type]
    assert [e["metodo"] for e in c.bot.envios] == ["sendPhoto"]
    ruim = c.pasta / "ruim.png"
    ruim.write_bytes(EXE)                                                            # dentro da pasta, mas não é imagem
    with pytest.raises(AnexoRecusado):
        await c.conversa().enviar_anexo(SaidaDoTelegram(c.canal), ruim)              # type: ignore[arg-type]
    assert len(c.bot.envios) == 1


async def test_enviar_conteudo_do_produto_confere_o_tipo_guarda_e_manda(c: CenarioAnexos) -> None:
    saida = SaidaDoTelegram(c.canal)
    await c.conversa().enviar_conteudo(saida, PDF, "relatório", mime_declarado="application/pdf")   # type: ignore[arg-type]
    assert [e["metodo"] for e in c.bot.envios] == ["sendDocument"]
    [linha] = c.linhas()
    assert (linha["direcao"], linha["estado"], linha["mime"], linha["entrada_id"]) == ("saida", "guardado", "application/pdf", None)
    assert c.arquivos() == [c.pasta / sha(PDF)[:2] / f"{sha(PDF)}.pdf"]
    with pytest.raises(AnexoRecusado):
        await c.conversa().enviar_conteudo(saida, EXE, mime_declarado="image/png")   # type: ignore[arg-type]
    with pytest.raises(AnexoRecusado):
        await c.conversa().enviar_conteudo(saida, JPEG, mime_declarado="image/png")  # type: ignore[arg-type]
    assert len(c.bot.envios) == 1 and len(c.linhas()) == 1


async def test_enviar_com_anexos_desligados_ou_canal_sem_envio_recusa(c: CenarioAnexos) -> None:
    c.cfg.file.avisos.entrada.anexos.enabled = False
    with pytest.raises(AnexoRecusado):
        await c.conversa().enviar_conteudo(SaidaDoTelegram(c.canal), PNG)             # type: ignore[arg-type]
    c.cfg.file.avisos.entrada.anexos.enabled = True
    with pytest.raises(AnexoRecusado):
        await c.conversa().enviar_conteudo(object(), PNG)                             # type: ignore[arg-type]
    assert c.bot.envios == []


async def test_adaptador_foto_recusada_vai_como_documento_e_a_legenda_e_cortada(c: CenarioAnexos) -> None:
    c.bot.recusa_foto = True
    mid = await c.canal.enviar_anexo(PNG, "image/png", "x" * 3000, responde_a=7)
    assert mid is not None and [e["metodo"] for e in c.bot.envios] == ["sendPhoto", "sendDocument"]
    corpo = bytes(c.bot.envios[1]["corpo"])                                           # type: ignore[arg-type]
    assert b"x" * 1024 in corpo and b"x" * 1025 not in corpo
    with pytest.raises(FalhaDeEnvio):
        await c.canal.enviar_anexo(b"MZ", "application/x-msdownload")                  # fora da lista: nem tenta
    assert len(c.bot.envios) == 2


# ---------------------------------------------------------------- o domínio e o armazém
@pytest.mark.parametrize(("conteudo", "mime"), [
    (JPEG, "image/jpeg"), (PNG, "image/png"), (WEBP, "image/webp"), (PDF, "application/pdf"),
    (TEXTO, "text/plain"), ("açaí ✓".encode(), "text/plain"),
    (EXE, None), (b"", None), (b"\x00\x01\x02", None), (b"\xff\xfe\xfa", None), (b"RIFF\x00\x00\x00\x00WAVE", None),
])
async def test_detectar_mime_pelo_conteudo(conteudo: bytes, mime: str | None) -> None:
    assert detectar_mime(conteudo) == mime


async def test_normalizar_mime_e_sha_e_tamanho() -> None:
    assert normalizar_mime("Image/JPG") == "image/jpeg" and normalizar_mime("text/plain; charset=utf-8") == "text/plain"
    assert normalizar_mime(None) is None and normalizar_mime("") is None
    assert sha256_valido(sha(b"x")) and not sha256_valido("../" + "a" * 61) and not sha256_valido(sha(b"x").upper())
    assert [tamanho_legivel(n) for n in (10, 2048, 10 * 1024 * 1024, 1_500_000)] == ["10 B", "2 KB", "10 MB", "1,4 MB"]


async def test_caminho_em_nao_sai_da_pasta(tmp_path: Path) -> None:
    ok = caminho_em(tmp_path, sha(b"x"), "image/png")
    assert ok == tmp_path / sha(b"x")[:2] / f"{sha(b'x')}.png"
    for sha_ruim, mime in (("../x", "image/png"), (sha(b"x")[:-1], "image/png"), (sha(b"x"), "application/x-sh"),
                           (sha(b"x") + "/..", "image/png")):
        with pytest.raises(CaminhoForaDoArmazem):
            caminho_em(tmp_path, sha_ruim, mime)


async def test_a_config_tem_padrao_seguro_e_limites() -> None:
    cfg = AnexosDaEntradaCfg()
    assert cfg.enabled and cfg.max_bytes == 10 * 1024 * 1024 and "application/pdf" in cfg.tipos
    with pytest.raises(ValidationError):
        AnexosDaEntradaCfg(max_bytes=30_000_000)                                       # o Bot API baixa até 20 MB
    with pytest.raises(ValidationError):
        AnexosDaEntradaCfg(tipos=["application/x-msdownload"])                         # fora da lista fixa do código


async def test_guardar_e_atomico_e_idempotente(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    db = Database(cfg.db_dsn)
    db.migrate()
    arm = ArmazemDeAnexos(db, cfg.data_dir / "anexos")
    a = arm.guardar(JPEG, tipos=("image/jpeg",), max_bytes=10_000)
    b = arm.guardar(JPEG, tipos=("image/jpeg",), max_bytes=10_000)
    assert a["id"] != b["id"] and a["sha256"] == b["sha256"]
    assert not list((cfg.data_dir / "anexos").rglob("*.tmp")) and len(list((cfg.data_dir / "anexos").rglob("*.jpg"))) == 1
    with pytest.raises(AnexoRecusado):
        arm.guardar(PNG, tipos=("image/jpeg",), max_bytes=10_000)                      # tipo desligado na config
    assert len(arm.da_entrada(0)) == 0 and arm.abrir(int(a["id"])) is not None
    assert arm.abrir(9999) is None


# ---------------------------------------------------------------- a faxina do 28.16
class CenaFaxina:
    def __init__(self, tmp_path: Path) -> None:
        self.cfg = make_config(tmp_path)
        self.cfg.ensure_dirs()
        self.db = Database(self.cfg.db_dsn)
        self.db.migrate()
        self.t = now()
        self.pasta = self.cfg.data_dir / "anexos"
        self.arm = ArmazemDeAnexos(self.db, self.pasta, relogio=lambda: self.t)
        self.faxina = FaxinaDosCanais(self.db, relogio=lambda: self.t, pasta_anexos=self.pasta)

    def avancar(self, dias: float) -> None:
        self.t += timedelta(days=dias)

    def guardar(self, conteudo: bytes) -> int:
        return int(self.arm.guardar(conteudo, tipos=("image/jpeg", "image/png", "application/pdf"),
                                    max_bytes=10_000)["id"])

    def faxinar(self, canal: str = "telegram", dias: float = 30.0):  # noqa: ANN201
        return self.faxina.faxinar(cerca=contextlib.nullcontext, canal=canal, retencao_dias=dias)

    def estados(self) -> dict[int, str]:
        return {int(r["id"]): str(r["estado"]) for r in self.db.query("SELECT id, estado FROM canal_anexos")}


@pytest.fixture
def f(tmp_path: Path) -> CenaFaxina:
    return CenaFaxina(tmp_path)


async def test_a_faxina_apaga_o_arquivo_e_a_linha_vencidos_e_deixa_o_recente(f: CenaFaxina) -> None:
    velho = f.guardar(JPEG)
    f.arm.recusar("tipo fora da lista")                                               # recusado: sem arquivo, só a linha
    f.avancar(31)
    novo = f.guardar(PNG)
    antes = sorted(f.pasta.rglob("*.*"))
    assert len(antes) == 2
    r = f.faxinar()
    assert r.anexos == 2 and r.algo                                                   # o vencido guardado + o recusado
    assert f.estados() == {novo: "guardado"}
    assert [p.suffix for p in f.pasta.rglob("*.*")] == [".png"]                       # o arquivo vencido SAIU do disco
    assert velho not in f.estados()


async def test_a_faxina_nao_apaga_o_arquivo_que_outra_linha_dentro_do_prazo_ainda_usa(f: CenaFaxina) -> None:
    """Dedupe × faxina: o mesmo conteúdo é um arquivo só; a linha velha vence, a nova ainda precisa dele."""
    f.guardar(PDF)
    f.avancar(31)
    novo = f.guardar(PDF)
    f.faxinar()
    assert f.estados() == {novo: "guardado"} and len(list(f.pasta.rglob("*.pdf"))) == 1
    f.avancar(31)
    f.faxinar()
    assert f.estados() == {} and list(f.pasta.rglob("*.pdf")) == []


async def test_a_faxina_so_olha_o_canal_pedido_e_nao_mexe_sem_pasta(f: CenaFaxina) -> None:
    id_ = f.guardar(JPEG)
    f.db.execute("UPDATE canal_anexos SET canal='trello' WHERE id=?", (id_,))
    f.avancar(31)
    assert not f.faxinar("telegram").algo and f.estados() == {id_: "guardado"}        # outro canal: intacto
    sem = FaxinaDosCanais(f.db, relogio=lambda: f.t)                                  # sem pasta: não toca em anexo
    sem.faxinar(cerca=contextlib.nullcontext, canal="trello", retencao_dias=30)
    assert f.estados() == {id_: "guardado"} and len(list(f.pasta.rglob("*.jpg"))) == 1
    f.faxinar("trello")
    assert f.estados() == {} and list(f.pasta.rglob("*.jpg")) == []


async def test_a_faxina_nao_segue_link_nem_sai_de_data_anexos(f: CenaFaxina, tmp_path: Path) -> None:
    """Uma linha adulterada (sha que aponta para fora) não faz a faxina apagar nada fora do armazém."""
    fora = tmp_path / "fora" / "precioso.jpg"
    fora.parent.mkdir()
    fora.write_bytes(JPEG)
    id_ = f.guardar(JPEG)
    f.db.execute("UPDATE canal_anexos SET sha256=? WHERE id=?", ("../../fora/precioso" + "0" * 45, id_))
    f.avancar(31)
    f.faxinar()
    assert fora.read_bytes() == JPEG and f.estados() == {}                            # a linha vence; o de fora, intacto


async def test_se_o_arquivo_nao_se_apaga_a_linha_fica_para_a_proxima_volta(f: CenaFaxina,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    id_ = f.guardar(JPEG)
    f.avancar(31)
    real = Path.unlink

    def quebra(self: Path, missing_ok: bool = False) -> None:
        raise PermissionError("em uso")

    monkeypatch.setattr(Path, "unlink", quebra)
    assert f.faxinar().anexos == 0 and f.estados() == {id_: "guardado"}
    monkeypatch.setattr(Path, "unlink", real)
    assert f.faxinar().anexos == 1 and f.estados() == {}


# ---------------------------------------------------------------- a rota de leitura
def _cliente(h: Harness) -> httpx.AsyncClient:
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 123)),
                             base_url="http://localhost")


async def test_rota_devolve_metadados_e_o_arquivo_como_download(harness: Harness) -> None:
    arm = harness.state.anexos_canal
    linha = arm.guardar(PNG, tipos=("image/png",), max_bytes=10_000, entrada_id=3)
    recusa = arm.recusar("voz não é aceita.", entrada_id=3)
    async with _cliente(harness) as cli:
        meta = await cli.get(f"/api/canais/anexos/{linha['id']}")
        assert meta.status_code == 200
        assert set(meta.json()) == {"id", "canal", "entrada_id", "direcao", "sha256", "mime", "bytes", "estado",
                                    "motivo_recusa", "criado_em", "apagado_em"}
        assert meta.json()["sha256"] == sha(PNG) and "caminho" not in json.dumps(meta.json()).lower()
        arq = await cli.get(f"/api/canais/anexos/{linha['id']}/conteudo")
        assert arq.status_code == 200 and arq.content == PNG
        assert arq.headers["content-type"] == "image/png" and arq.headers["x-content-type-options"] == "nosniff"
        assert arq.headers["content-disposition"] == f'attachment; filename="anexo-{linha["id"]}.png"'
        assert (await cli.get(f"/api/canais/anexos/{recusa['id']}")).json()["motivo_recusa"] == "voz não é aceita."
        assert (await cli.get(f"/api/canais/anexos/{recusa['id']}/conteudo")).status_code == 404
        assert (await cli.get("/api/canais/anexos/99999")).status_code == 404
        harness.state.db.execute("UPDATE canal_anexos SET estado='apagado' WHERE id=?", (linha["id"],))
        assert (await cli.get(f"/api/canais/anexos/{linha['id']}/conteudo")).status_code == 410
        for metodo in ("post", "put", "patch", "delete"):
            assert (await getattr(cli, metodo)(f"/api/canais/anexos/{linha['id']}")).status_code == 405, metodo


async def test_rota_exige_o_mesmo_login_das_outras_rotas_de_canais(harness: Harness) -> None:
    harness.cfg.file.server.host = "0.0.0.0"                       # noqa: S104 - o cenário sob teste: acesso de fora
    harness.cfg.file.server.public_hosts = ["parque.local"]
    harness.cfg.env.api_token = SecretStr("tk-anexos-5d1e")
    ident = harness.state.anexos_canal.guardar(PNG, tipos=("image/png",), max_bytes=10_000)["id"]
    app = create_app(harness.cfg, state=harness.state)
    app.state.poc = harness.state
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 123)),
                                 base_url="http://parque.local") as cli:
        for caminho in (f"/api/canais/anexos/{ident}", f"/api/canais/anexos/{ident}/conteudo"):
            assert (await cli.get(caminho)).status_code == 401, caminho
        ok = await cli.get(f"/api/canais/anexos/{ident}/conteudo", headers={"Authorization": "Bearer tk-anexos-5d1e"})
        assert ok.status_code == 200 and ok.content == PNG



# ---------------------------------------------------------------- 0: a falha no download nunca fica calada
def _interrompida(c: CenarioAnexos, uid: int, file_id: str, *, mime: str | None = None,
                  velha: bool = True) -> tuple[int, dict[str, object]]:
    """A Central caiu entre gravar a mensagem e baixar o anexo: a mensagem e o `pendente` ficaram no banco."""
    c.repo.gravar(id_externo=str(uid), ordem=uid, tipo="mensagem", do_dono=True, ref_mensagem=str(uid * 10),
                  responde_a=None, texto=None, tamanho=0, estado="ignorada")
    ident = c.repo.id_de(str(uid))
    assert ident is not None
    linha = c.armazem.pendente(file_id, entrada_id=ident, mime_declarado=mime, tamanho=500)
    if velha:
        c.db.execute("UPDATE canal_anexos SET criado_em='2026-01-01T00:00:00Z' WHERE id=?", (linha["id"],))
    return ident, linha


async def test_queda_entre_gravar_e_baixar_a_volta_seguinte_baixa_uma_vez_e_conta(c: CenarioAnexos) -> None:
    c.bot.arquivos["f1"] = PDF
    ident, linha = _interrompida(c, 5, "f1", mime="application/pdf")
    await c.volta()
    [r] = c.linhas()
    assert (r["estado"], r["mime"], r["ref_externa"], r["mime_declarado"]) == ("guardado", "application/pdf", None, None)
    assert c.ultima().startswith("Recebi o PDF (") and c.bot.mensagens()[-1]["reply_parameters"]["message_id"] == 50   # type: ignore[index]
    assert c.bot.downloads == ["docs/f1"]
    await c.volta()                                                                  # nada pendente: não baixa de novo
    assert c.bot.downloads == ["docs/f1"]


async def test_queda_e_o_download_falha_na_retomada_o_dono_e_avisado_e_a_linha_fecha(c: CenarioAnexos) -> None:
    c.bot.arquivos["f1"] = PDF
    c.bot.falha_download = httpx.ConnectError("sem rede")
    _interrompida(c, 5, "f1")
    await c.volta()
    [r] = c.linhas()
    assert r["estado"] == "recusado" and "baixar" in str(r["motivo_recusa"]) and r["ref_externa"] is None
    assert c.ultima() == "Não guardei o anexo: não consegui baixar o arquivo do canal. Mande de novo."
    await c.volta()                                                                  # UMA tentativa só
    assert c.bot.downloads == ["docs/f1"] and len(c.bot.mensagens()) == 1


async def test_pendente_recente_nao_e_retomado(c: CenarioAnexos) -> None:
    c.bot.arquivos["f1"] = PDF
    _interrompida(c, 5, "f1", velha=False)                                           # ainda dentro do download normal
    await c.volta()
    assert [l["estado"] for l in c.linhas()] == ["pendente"] and c.bot.downloads == [] and c.bot.mensagens() == []


async def test_erro_inesperado_no_download_deixa_pendente_e_a_proxima_volta_retoma(c: CenarioAnexos,
                                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    c.bot.arquivos["foto-g"] = JPEG
    real = CanalTelegram.baixar_anexo

    async def quebra(self: CanalTelegram, ref: str, max_bytes: int) -> bytes:
        raise RuntimeError("a Central caiu")

    monkeypatch.setattr(CanalTelegram, "baixar_anexo", quebra)
    await c.volta(foto(5, "foto-g"))
    [r] = c.linhas()
    assert r["estado"] == "pendente" and r["ref_externa"] == "foto-g" and r["entrada_id"] == c.entrada(5)["id"]
    monkeypatch.setattr(CanalTelegram, "baixar_anexo", real)
    c.db.execute("UPDATE canal_anexos SET criado_em='2026-01-01T00:00:00Z'")
    await c.volta()
    assert [l["estado"] for l in c.linhas()] == ["guardado"] and c.ultima().startswith("Recebi a imagem (")


async def test_a_falha_comum_responde_e_deixa_motivo_nao_deixa_pendente(c: CenarioAnexos) -> None:
    c.bot.arquivos["foto-g"] = JPEG
    c.bot.falha_download = httpx.ReadTimeout("devagar")
    await c.volta(foto(5, "foto-g"))
    [r] = c.linhas()
    assert r["estado"] == "recusado" and r["motivo_recusa"] and r["ref_externa"] is None
    assert "Mande de novo" in c.ultima()
