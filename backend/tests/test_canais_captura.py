"""28.24 F2: a captura de tela de um aparelho volta ao chat do DONO (exceção (a) do dono, 04/10 15:17Z).

Prova `simulated`: Bot API falsa (`httpx.MockTransport`), portas falsas e um gerenciador de aparelhos de mentira para a regra da
captura (frame fresco, frame velho que acorda a prévia, tela sensível, loja, offline); mais UM teste com o `DeviceManager` do
harness (provedor simulado). Nada de aparelho nem Telegram reais.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.devices.captura_pontual import capturar_para_o_dono
from app.models import InstanceState
from app.modules.avisos.application.entrada import rotear
from app.modules.avisos.infrastructure.entrada import Captura, Recebida

from .conftest import Harness
from .test_canais_anexos import JPEG, CenarioAnexos, sha
from .test_telegram_entrada import CHAT, PortasFalsas, msg

pytestmark = pytest.mark.asyncio


class PortasComCaptura(PortasFalsas):
    def __init__(self) -> None:
        super().__init__()
        self.pedidas: list[str] = []
        self.resposta = Captura(conteudo=JPEG)

    async def captura(self, instance_id: str) -> Captura:
        self.pedidas.append(instance_id)
        return self.resposta


@pytest.fixture
def c(tmp_path: Path) -> CenarioAnexos:
    cen = CenarioAnexos(tmp_path)
    cen.portas = PortasComCaptura()                                                  # type: ignore[assignment]
    cen.servico.portas = cen.portas
    cen.servico.conversa.portas = cen.portas
    return cen


# ---------------------------------------------------------------- a gramática
@pytest.mark.parametrize("texto", ["/captura android-12", "/captura@Bot Android-12", "/print android-12", "captura do android-12",
                                   "Captura de tela do Android-12", "me manda um print do android-12?", "tira uma captura android-12",
                                   "screenshot do android-12."])
async def test_captura_do_aparelho_e_uma_intencao_propria(texto: str) -> None:
    i = rotear(texto)
    assert (i.tipo, i.alvo) == ("captura", "android-12")


@pytest.mark.parametrize("texto", ["captura do app do Pedro", "captura da tela do android-12 e posta no instagram",
                                   "abre o instagram no android-12", "print"])
async def test_o_que_nao_e_so_o_pedido_da_captura_segue_como_texto_livre(texto: str) -> None:
    assert rotear(texto).tipo in ("livre", "desconhecida") and rotear(texto).tipo != "captura"


async def test_captura_sem_aparelho_valido_pede_o_formato() -> None:
    for texto in ("/captura", "/captura minha-tela", "/captura ../x"):
        i = rotear(texto)
        assert i.tipo == "desconhecida" and "android-NN" in (i.motivo or "")


# ---------------------------------------------------------------- a conversa
async def test_o_dono_pede_e_a_tela_volta_so_a_ele_com_legenda_so_do_aparelho(c: CenarioAnexos) -> None:
    await c.volta(msg(5, "captura do android-12"))
    assert c.portas.pedidas == ["android-12"] and c.portas.nomes() == []              # type: ignore[attr-defined]
    [envio] = c.bot.envios
    corpo = bytes(envio["corpo"])                                                     # type: ignore[arg-type]
    assert envio["metodo"] == "sendPhoto" and JPEG in corpo and f'name="chat_id"'.encode() in corpo
    assert f"\r\n\r\n{CHAT}\r\n".encode() in corpo                                    # o chat do DONO, e só ele
    assert b"Captura do android-12" in corpo
    assert b"persona" not in corpo.lower() and b"@" not in corpo                      # a legenda é só o id do aparelho
    [linha] = c.linhas()
    assert (linha["direcao"], linha["estado"], linha["mime"], linha["sha256"]) == ("saida", "guardado", "image/jpeg", sha(JPEG))
    e = c.entrada(5)
    assert (e["estado"], e["intencao"], e["resposta"]) == ("feita", "captura", "captura enviada")
    assert len(c.bot.mensagens()) == 0                                                # nada de texto extra: só a imagem


async def test_sem_captura_o_motivo_vai_ao_dono_como_texto(c: CenarioAnexos) -> None:
    c.portas.resposta = Captura(motivo="O android-12 não está online agora.")        # type: ignore[attr-defined]
    await c.volta(msg(5, "/captura android-12"))
    assert c.bot.envios == [] and c.ultima() == "O android-12 não está online agora." and c.linhas() == []


async def test_canal_que_nao_envia_anexo_recusa_com_o_motivo(c: CenarioAnexos) -> None:
    respostas: list[str] = []

    class SaidaSimples:
        async def responder(self, texto: str, *, responde_a: str | None = None, botoes: object = None) -> str | None:
            respostas.append(texto)
            return "77"

        async def confirmar_botao(self, botao_id: str) -> None: ...
        async def tirar_botoes(self, ref_mensagem: str) -> None: ...
        async def apagar(self, ref_mensagem: str) -> bool:
            return False

    conversa = c.conversa()
    saida = SaidaSimples()
    await conversa.registrar(Recebida(id_externo="900", ordem=900, tipo="mensagem", do_dono=True,
                                      texto="captura do android-12", ref_mensagem="9"), saida)   # type: ignore[arg-type]
    await conversa.tratar_pendentes(saida)                                                       # type: ignore[arg-type]
    assert respostas == ["Não enviei a captura: Este canal não envia anexos."] and c.linhas() == []


async def test_a_falha_do_canal_ao_enviar_e_dita_ao_dono(c: CenarioAnexos) -> None:
    c.bot.recusa_foto = True                                                          # sendPhoto 400 → sendDocument
    await c.volta(msg(5, "captura do android-12"))
    assert [e["metodo"] for e in c.bot.envios] == ["sendPhoto", "sendDocument"]       # caiu para documento, e saiu
    c.bot.envios.clear()
    c.bot.getfile_falha = None
    antiga = c.bot.handler

    def so_erro(req):                                                                 # type: ignore[no-untyped-def]
        import httpx
        if req.url.path.endswith(("sendPhoto", "sendDocument")):
            return httpx.Response(403, json={"ok": False, "description": "Forbidden: bot was blocked by the user"})
        return antiga(req)

    c.canal._client._transport = __import__("httpx").MockTransport(so_erro)           # type: ignore[union-attr]
    await c.volta(msg(6, "captura do android-12"))
    assert c.ultima() == "Tirei a captura, mas o canal não aceitou o arquivo. Tente de novo."
    assert c.entrada(6)["estado"] == "feita"


async def test_o_convidado_nunca_pede_captura(tmp_path: Path) -> None:
    from app.modules.avisos.infrastructure.contatos_sql import ContatosDoCanal
    from app.modules.avisos.infrastructure.convidados import ConvidadosDoTelegram
    cen = CenarioAnexos(tmp_path)
    portas = PortasComCaptura()
    cen.servico.portas = cen.servico.conversa.portas = portas
    for ligado in (False, True):
        cen.cfg.file.avisos.entrada.convidados.enabled = ligado
        if ligado:
            t = cen.triagem
            cen.servico.convidados = ConvidadosDoTelegram(cen.cfg, ContatosDoCanal(cen.db), avisar_dono=lambda _a: True,
                                                          recusa=t.recusa, redigir=t.redigir, status=lambda: "ok")
        u = msg(10 + ligado, "captura do android-12", chat=777)
        u["message"]["from"] = {"id": 777, "first_name": "Fulana", "is_bot": False}    # type: ignore[index]
        await cen.volta(u)
    assert portas.pedidas == [] and cen.bot.envios == [] and cen.linhas() == []


# ---------------------------------------------------------------- a regra da captura (a mesma prévia do painel)
class Gerenciador:
    def __init__(self, rt: SimpleNamespace | None, idade_max_ms: int = 6000) -> None:
        self.rt = rt
        self.interesses: list[tuple[object, ...]] = []
        self.soltos: list[str] = []
        self._ms = idade_max_ms

    def get(self, instance_id: str) -> SimpleNamespace:
        if self.rt is None or instance_id != "android-12":
            raise KeyError(instance_id)
        return self.rt

    def get_settings(self) -> SimpleNamespace:
        return SimpleNamespace(frame_max_age_ms=self._ms)

    def registrar_interesse(self, *args: object) -> None:
        self.interesses.append(args)

    def soltar_interesse(self, conexao: str) -> None:
        self.soltos.append(conexao)


def _frame(idade_s: float = 0.0, *, sensivel: bool = False, jpeg: bytes = JPEG) -> SimpleNamespace:
    return SimpleNamespace(mono=time.monotonic() - idade_s, sensitive=sensivel, jpeg_full=b"" if sensivel else jpeg)


def _rt(frame: SimpleNamespace | None, *, estado: InstanceState = InstanceState.online, loja: bool = False) -> SimpleNamespace:
    return SimpleNamespace(state=estado, store=loja, frame=frame)


async def test_frame_fresco_sai_sem_acordar_a_previa() -> None:
    g = Gerenciador(_rt(_frame(0.5)))
    assert await capturar_para_o_dono(g, "android-12") == (JPEG, None)
    assert g.interesses == [] and g.soltos == []


async def test_frame_velho_acorda_a_previa_por_pouco_tempo_e_espera_o_novo() -> None:
    rt = _rt(_frame(60))
    g = Gerenciador(rt)
    novo = b"\xff\xd8\xff\xe0novo"

    async def dormir(_s: float) -> None:
        rt.frame = _frame(0.0, jpeg=novo)                                             # a prévia publicou um frame novo

    assert await capturar_para_o_dono(g, "android-12", dormir=dormir) == (novo, None)
    assert len(g.interesses) == 1 and g.interesses[0][1:] == ([], "android-12", 10)       # 8 s de espera + 2 s de folga
    assert g.interesses[0][0].startswith("captura-canal-android-12-") and g.soltos == [g.interesses[0][0]]


async def test_o_interesse_vive_mais_que_a_espera() -> None:
    """Achado 7 da revisão: o TTL (5 s) era menor que a espera (8 s)."""
    from app.devices.captura_pontual import ESPERA_S, ttl_do_interesse
    assert ttl_do_interesse(ESPERA_S) > ESPERA_S and ttl_do_interesse(0.05) >= 5 and ttl_do_interesse(30.0) > 30.0


async def test_dois_pedidos_do_mesmo_aparelho_usam_chaves_diferentes_e_cada_um_solta_so_a_sua() -> None:
    rt = _rt(_frame(60))
    g = Gerenciador(rt)

    async def dormir(_s: float) -> None:
        await asyncio.sleep(0)                                                       # cede a vez: os dois pedidos se sobrepõem
        rt.frame = _frame(0.0)

    await asyncio.gather(capturar_para_o_dono(g, "android-12", dormir=dormir), capturar_para_o_dono(g, "android-12", dormir=dormir))
    chaves = [i[0] for i in g.interesses]
    assert len(chaves) == 2 and len(set(chaves)) == 2 and sorted(g.soltos) == sorted(chaves)


async def test_sem_frame_novo_no_prazo_diz_ao_dono_e_solta_o_interesse() -> None:
    g = Gerenciador(_rt(None))

    async def dormir(_s: float) -> None:
        return None

    jpeg, motivo = await capturar_para_o_dono(g, "android-12", espera_s=0.05, dormir=dormir)
    assert jpeg is None and "captura nova" in (motivo or "") and len(g.soltos) == 1
    assert g.soltos[0].startswith("captura-canal-android-12-") and g.soltos[0] == g.interesses[0][0]


@pytest.mark.parametrize("rt", [_rt(_frame(0.1, sensivel=True)), _rt(_frame(0.1, jpeg=b"")),
                                _rt(_frame(0.1), loja=True), _rt(_frame(0.1), estado=InstanceState.stopped)])
async def test_tela_sensivel_loja_ou_aparelho_fora_do_ar_nao_sai_captura(rt: SimpleNamespace) -> None:
    jpeg, motivo = await capturar_para_o_dono(Gerenciador(rt), "android-12")
    assert jpeg is None and motivo


async def test_sensivel_que_aparece_no_frame_novo_tambem_nao_sai() -> None:
    rt = _rt(_frame(60))

    async def dormir(_s: float) -> None:
        rt.frame = _frame(0.0, sensivel=True)

    jpeg, motivo = await capturar_para_o_dono(Gerenciador(rt), "android-12", dormir=dormir)
    assert jpeg is None and "sensível" in (motivo or "")


async def test_aparelho_desconhecido() -> None:
    jpeg, motivo = await capturar_para_o_dono(Gerenciador(None), "android-99")
    assert jpeg is None and motivo == "Não conheço o aparelho android-99."


async def test_com_o_gerenciador_real_do_harness_o_aparelho_fora_do_ar_e_dito(harness: Harness) -> None:
    """O `DeviceManager` de verdade responde `get`/`get_settings`/`registrar_interesse`: aparelho que não existe e o que está
    parado não geram captura nem exceção."""
    g = harness.state.devices
    jpeg, motivo = await capturar_para_o_dono(g, "android-999")
    assert jpeg is None and "Não conheço" in (motivo or "")
    rt = g.get("android-01")
    if rt.state != InstanceState.online:
        jpeg, motivo = await capturar_para_o_dono(g, "android-01")
        assert jpeg is None and "não está online" in (motivo or "")
    else:                                                                              # provedor simulado já no ar
        jpeg, motivo = await capturar_para_o_dono(g, "android-01", espera_s=3.0)
        assert (jpeg is not None) != (motivo is not None)




async def test_o_segundo_pedido_continua_com_interesse_quando_o_primeiro_termina() -> None:
    """Achado 7, 2ª passada: o gerenciador real faz `pop` pela chave; com chave fixa o `finally` do 1º soltava o do 2º."""
    rt = _rt(_frame(60))

    class ComPop(Gerenciador):
        def __init__(self, rt: SimpleNamespace) -> None:
            super().__init__(rt)
            self.vivos: dict[object, tuple[object, ...]] = {}

        def registrar_interesse(self, *args: object) -> None:
            self.vivos[args[0]] = args                                               # substitui, como o real

        def soltar_interesse(self, conexao: str) -> None:
            self.vivos.pop(conexao, None)

    g = ComPop(rt)
    visto: list[int] = []

    async def lento(_s: float) -> None:
        await asyncio.sleep(0.01)
        if len(visto) == 0 and rt.frame.mono < time.monotonic() - 30:               # type: ignore[union-attr]
            visto.append(len(g.vivos))
            rt.frame = _frame(0.0)                                                   # o 1º recebe o frame e termina

    async def espera(_s: float) -> None:
        await asyncio.sleep(0.01)
        visto.append(-1) if not g.vivos else None

    a = asyncio.create_task(capturar_para_o_dono(g, "android-12", dormir=lento))
    await asyncio.sleep(0)
    b = asyncio.create_task(capturar_para_o_dono(g, "android-12", espera_s=0.3, dormir=espera))
    await asyncio.gather(a, b)
    assert visto[0] == 2 and -1 not in visto                                         # os dois tinham interesse vivo
