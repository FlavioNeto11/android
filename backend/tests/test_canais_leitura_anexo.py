"""28.24 (F3): a IA lê a imagem que o dono mandou. Prova `simulated` (`arquivo::teste`): descritor falso no lugar do provedor
(nenhuma rede, nenhuma chamada paga), SQLite, preços da config padrão. A chamada paga real fica `not_run`."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Config
from app.db import Database
from app.modules.avisos.application.entrada import Fato, rotear
from app.modules.avisos.infrastructure.anexos import ArmazemDeAnexos
from app.modules.avisos.infrastructure.anexos_leitura import (
    LeitorDeAnexo,
    LeituraRecusada,
    estimar_usd,
    modelo_de_visao,
)
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.planning.provider import AIError, Usage

from .conftest import make_config

pytestmark = pytest.mark.asyncio

JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x01" * 120
PDF = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\n"
CHAVE = "sk-ant-api03-" + "A" * 40
TIPOS = ("image/jpeg", "image/png", "image/webp", "application/pdf", "text/plain")


class DescritorFalso:
    def __init__(self, texto: str = "Um print de tela com o texto \"Entrar\".", falha: Exception | None = None) -> None:
        self.texto, self.falha, self.chamadas = texto, falha, 0

    async def descrever_imagem(self, conteudo: bytes, mime: str, *, model: str, system: str, pergunta: str,
                               max_tokens: int) -> tuple[str, Usage]:
        self.chamadas += 1
        if self.falha is not None:
            raise self.falha
        return self.texto, Usage(calls=1, input_tokens=1300, output_tokens=120, role="canais", model=model,
                                 with_image=True, provider="anthropic")


class Cenario:
    def __init__(self, tmp_path: Path, **leitor: object) -> None:
        self.cfg: Config = make_config(tmp_path)
        self.cfg.ensure_dirs()
        self.db = Database(self.cfg.db_dsn)
        self.db.migrate()
        self.arm = ArmazemDeAnexos(self.db, self.cfg.data_dir / "anexos")
        self.descritor = DescritorFalso()
        self.usos: list[Usage] = []
        self.gastos = 0

        def conferir() -> None:
            self.gastos += 1

        self.leitor = LeitorDeAnexo(
            self.cfg, self.db, self.arm, descritor=lambda: self.descritor, redigir=TriagemDeCredencial().redigir,
            conferir_gasto=conferir, registrar_uso=self.usos.append, **leitor)  # type: ignore[arg-type]

    def entrada(self, ident: str, *, do_dono: bool = True) -> int:
        self.db.execute(
            "INSERT INTO canal_entradas(canal, id_externo, tipo, do_dono, ref_mensagem, tamanho, estado, recebida_em)"
            " VALUES ('telegram', ?, 'mensagem', ?, ?, 0, 'ignorada', '2026-10-04T00:00:00Z')",
            (ident, 1 if do_dono else 0, ident))
        return int(self.db.one("SELECT id FROM canal_entradas WHERE id_externo=?", (ident,))["id"])

    def anexo(self, conteudo: bytes = JPEG, *, do_dono: bool = True, ident: str = "1") -> int:
        return int(self.arm.guardar(conteudo, tipos=TIPOS, max_bytes=10_000,
                                    entrada_id=self.entrada(ident, do_dono=do_dono))["id"])

    def linha(self, ident: int) -> dict[str, object]:
        return dict(self.db.one("SELECT * FROM canal_anexos WHERE id=?", (ident,)))


async def test_le_a_imagem_do_dono_grava_a_descricao_e_o_custo(tmp_path: Path) -> None:
    c = Cenario(tmp_path)
    ident = c.anexo()
    r = await c.leitor.ler(ident)
    assert not r.do_cache and r.modelo == "claude-haiku-4-5" and "Entrar" in r.descricao
    # 1300 de entrada a US$ 1/M + 120 de saída a US$ 5/M = 0,0019 (tokens × ai.prices, não ai_calls.usd)
    assert r.custo_usd == pytest.approx(0.0019, abs=1e-9)
    linha = c.linha(ident)
    assert linha["descricao"] == r.descricao and linha["custo_usd"] == pytest.approx(0.0019) and linha["lida_em"]
    assert linha["modelo_leitura"] == "claude-haiku-4-5" and (linha["tokens_entrada"], linha["tokens_saida"]) == (1300, 120)
    assert c.gastos == 1 and c.descritor.chamadas == 1
    assert [(u.origem, u.ref) for u in c.usos] == [("canais", f"anexo:{ident}")]      # o custo entrou no registro de custos


async def test_segunda_leitura_vem_do_cache_sem_chamar_nem_conferir_gasto(tmp_path: Path) -> None:
    c = Cenario(tmp_path)
    ident = c.anexo()
    primeira = await c.leitor.ler(ident)
    segunda = await c.leitor.ler(ident)
    assert segunda.do_cache and segunda.custo_usd == 0.0 and segunda.descricao == primeira.descricao
    assert c.descritor.chamadas == 1 and c.gastos == 1 and len(c.usos) == 1
    # contraprova: sem o cache gravado, a leitura chama de novo
    c.db.execute("UPDATE canal_anexos SET descricao=NULL WHERE id=?", (ident,))
    assert not (await c.leitor.ler(ident)).do_cache and c.descritor.chamadas == 2


async def test_so_anexo_de_entrada_do_dono(tmp_path: Path) -> None:
    c = Cenario(tmp_path)
    de_convidado = c.anexo(do_dono=False)
    with pytest.raises(LeituraRecusada) as e:
        await c.leitor.ler(de_convidado)
    assert e.value.codigo == "anexo_nao_permitido" and "ser lida pela IA" in e.value.motivo
    saida = int(c.arm.registrar_saida("a" * 64, "image/jpeg", 10)["id"])
    with pytest.raises(LeituraRecusada) as e2:
        await c.leitor.ler(saida)
    assert e2.value.codigo == "anexo_nao_permitido"
    with pytest.raises(LeituraRecusada) as e3:
        await c.leitor.ler(9999)
    assert (e3.value.codigo, e3.value.status) == ("anexo_desconhecido", 404)
    assert c.descritor.chamadas == 0 and c.gastos == 0
    # contraprova: o mesmo conteúdo, mandado pelo dono, é lido
    assert (await c.leitor.ler(c.anexo(ident="9"))).descricao


async def test_so_imagem(tmp_path: Path) -> None:
    c = Cenario(tmp_path)
    pdf = c.anexo(PDF, ident="2")
    with pytest.raises(LeituraRecusada) as e:
        await c.leitor.ler(pdf)
    assert (e.value.codigo, e.value.status) == ("anexo_nao_imagem", 422) and c.descritor.chamadas == 0


async def test_teto_estimado_antes_de_chamar(tmp_path: Path) -> None:
    c = Cenario(tmp_path)
    ident = c.anexo()
    assert estimar_usd(c.cfg, "claude-haiku-4-5", JPEG, "image/jpeg", 400) < 0.05           # o caso normal passa
    c.cfg.file.avisos.entrada.anexos.leitura.teto_usd = 0.0001                               # o teto menor que a estimativa
    with pytest.raises(LeituraRecusada) as e:
        await c.leitor.ler(ident)
    assert e.value.codigo == "leitura_acima_do_teto" and "não enviei" in e.value.motivo
    assert c.descritor.chamadas == 0 and c.gastos == 0 and c.linha(ident)["descricao"] is None
    # contraprova: com o teto de volta, a mesma imagem é lida
    c.cfg.file.avisos.entrada.anexos.leitura.teto_usd = 0.05
    assert (await c.leitor.ler(ident)).descricao and c.descritor.chamadas == 1


async def test_estimativa_de_720x1280_fica_bem_abaixo_do_teto(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (720).to_bytes(4, "big") + (1280).to_bytes(4, "big") + b"\x00" * 20
    est = estimar_usd(cfg, "claude-haiku-4-5", png, "image/png", 400)
    assert 0.002 < est < 0.005                                                              # ~US$ 0,0035
    # sem as dimensões, vale o pior caso da imagem (1.600 tokens), e um modelo sem preço paga o mais caro da tabela
    assert estimar_usd(cfg, "claude-haiku-4-5", b"\x00" * 8, "image/webp", 400) > est
    assert estimar_usd(cfg, "modelo-sem-preco", png, "image/png", 400) > est


async def test_modelo_e_o_mais_barato_com_visao(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    assert modelo_de_visao(cfg) == "claude-haiku-4-5"
    cfg.file.ai.models["claude-haiku-4-5"].vision = False                                    # contraprova: sem visão, sai
    assert modelo_de_visao(cfg) != "claude-haiku-4-5"


async def test_descricao_passa_pelo_redator_ao_gravar_e_ao_devolver(tmp_path: Path) -> None:
    c = Cenario(tmp_path)
    c.descritor.texto = f"Um bilhete com a chave {CHAVE} escrita."
    ident = c.anexo()
    r = await c.leitor.ler(ident)
    assert CHAVE not in r.descricao and CHAVE not in str(c.linha(ident)["descricao"])
    # contraprova: o que já estava gravado cru (de antes do filtro) também sai redigido do cache
    c.db.execute("UPDATE canal_anexos SET descricao=? WHERE id=?", (f"cru {CHAVE}", ident))
    assert CHAVE not in (await c.leitor.ler(ident)).descricao


async def test_falha_da_ia_nao_grava_como_lido(tmp_path: Path) -> None:
    c = Cenario(tmp_path)
    c.descritor.falha = AIError("Limite de requisições do provedor atingido.", retryable=True)
    ident = c.anexo()
    with pytest.raises(LeituraRecusada) as e:
        await c.leitor.ler(ident)
    assert (e.value.codigo, e.value.status) == ("ia_falhou", 502) and "Não consegui ler" in e.value.motivo
    linha = c.linha(ident)
    assert linha["descricao"] is None and linha["lida_em"] is None and linha["custo_usd"] is None and not c.usos
    # contraprova: a IA volta e a leitura grava
    c.descritor.falha = None
    assert (await c.leitor.ler(ident)).descricao and c.linha(ident)["descricao"]


async def test_resposta_vazia_nao_grava_mas_o_custo_ja_pago_entra(tmp_path: Path) -> None:
    c = Cenario(tmp_path)
    c.descritor.texto = "   "
    ident = c.anexo()
    with pytest.raises(LeituraRecusada):
        await c.leitor.ler(ident)
    assert c.linha(ident)["descricao"] is None and len(c.usos) == 1


async def test_gasto_barrado_ou_sem_conferencia_nao_envia(tmp_path: Path) -> None:
    c = Cenario(tmp_path)
    ident = c.anexo()

    def barra() -> None:
        raise AIError("Teto de gasto de IA do dia atingido.", kind="budget", motivo="dia")

    c.leitor._conferir_gasto = barra
    with pytest.raises(LeituraRecusada) as e:
        await c.leitor.ler(ident)
    assert e.value.codigo == "gasto_barrado" and c.descritor.chamadas == 0
    c.leitor._conferir_gasto = None
    with pytest.raises(LeituraRecusada) as e2:
        await c.leitor.ler(ident)
    assert e2.value.codigo == "gasto_nao_conferido" and c.descritor.chamadas == 0


async def test_desligada_e_simulada(tmp_path: Path) -> None:
    c = Cenario(tmp_path)
    ident = c.anexo()
    c.cfg.file.avisos.entrada.anexos.leitura.enabled = False
    with pytest.raises(LeituraRecusada) as e:
        await c.leitor.ler(ident)
    assert (e.value.codigo, e.value.status) == ("leitura_desligada", 503) and c.descritor.chamadas == 0
    c.cfg.file.avisos.entrada.anexos.leitura.enabled = True
    s = Cenario(tmp_path / "sim", simulado=lambda: True)
    r = await s.leitor.ler(s.anexo())
    assert r.custo_usd == 0.0 and r.modelo == "simulado" and s.descritor.chamadas == 0 and s.gastos == 0


async def test_gramatica_do_ler(tmp_path: Path) -> None:
    f = Fato("anexo", "7")
    for frase in ("/ler", "leia", "Leia essa imagem", "o que tem nessa imagem?", "descreva a foto"):
        i = rotear(frase, fato=f)
        assert (i.tipo, i.ref) == ("ler_anexo", "7"), frase
    # contraprova: outra frase, replicada a uma foto, não é sequestrada
    assert rotear("sim", fato=f).tipo == "livre" and rotear("abra o instagram no android-01", fato=f).tipo == "livre"
    # /ler sem reply a anexo explica o formato; sem o fato, "leia" é texto livre
    assert rotear("/ler").tipo == "desconhecida" and "Responda (reply)" in (rotear("/ler").motivo or "")
    assert rotear("leia").tipo == "livre"


# ---------------------------------------------------------------- o Telegram: /ler em reply
from .test_canais_anexos import CenarioAnexos, documento, foto  # noqa: E402
from .test_telegram_entrada import msg  # noqa: E402


@pytest.fixture
def c(tmp_path: Path) -> CenarioAnexos:
    return CenarioAnexos(tmp_path)


def _portas_com_leitura(c: CenarioAnexos) -> list[int]:
    lidas: list[int] = []

    async def ler_anexo(anexo_id: int) -> str:
        lidas.append(anexo_id)
        return "Um print de tela.\n\nLi com claude-haiku-4-5; custo US$ 0,0019."

    c.portas.ler_anexo = ler_anexo                                                          # type: ignore[attr-defined]
    return lidas


async def test_ler_em_reply_a_foto_do_dono_responde_a_descricao(c: CenarioAnexos) -> None:
    lidas = _portas_com_leitura(c)
    c.bot.arquivos["foto-g"] = JPEG
    await c.volta(foto(5, "foto-g"))
    [linha] = c.linhas()
    await c.volta(msg(6, "/ler", reply_to=50))
    assert lidas == [linha["id"]] and "Um print de tela" in c.ultima()
    assert c.entrada(6)["estado"] == "feita" and c.entrada(6)["intencao"] == "ler_anexo"
    await c.volta(msg(7, "leia", reply_to=50), msg(8, "o que tem nessa imagem?", reply_to=50))
    assert lidas == [linha["id"]] * 3


async def test_ler_sem_reply_explica_o_formato_e_outras_respostas_a_foto_nao_leem(c: CenarioAnexos) -> None:
    lidas = _portas_com_leitura(c)
    c.bot.arquivos["foto-g"] = JPEG
    c.bot.arquivos["doc-1"] = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\n"
    await c.volta(foto(5, "foto-g"), documento(6, "doc-1", mime="application/pdf"))
    await c.volta(msg(7, "/ler"))
    assert "Responda (reply)" in c.ultima() and lidas == []
    await c.volta(msg(8, "sim", reply_to=50))                                            # não é sequestrado pela foto
    assert lidas == [] and c.entrada(8)["intencao"] != "ler_anexo"
    await c.volta(msg(9, "/ler", reply_to=60))                                           # reply a um PDF: não há imagem
    assert lidas == [] and "Responda (reply)" in c.ultima()


async def test_recusa_da_leitura_vira_frase_ao_dono(c: CenarioAnexos) -> None:
    from app.modules.avisos.infrastructure.entrada import RecusaDaCentral
    c.bot.arquivos["foto-g"] = JPEG

    async def ler_anexo(_i: int) -> str:
        raise RecusaDaCentral("A leitura custaria até US$ 0,2000, acima do teto de US$ 0,05 por imagem: não enviei.")

    c.portas.ler_anexo = ler_anexo                                                          # type: ignore[attr-defined]
    await c.volta(foto(5, "foto-g"))
    await c.volta(msg(6, "/ler", reply_to=50))
    assert "acima do teto" in c.ultima() and c.entrada(6)["estado"] == "feita"


# ---------------------------------------------------------------- a rota
async def test_rota_ler_confirma_valida_e_nao_paga_duas_vezes(harness) -> None:                # type: ignore[no-untyped-def]
    import httpx

    from app.main import create_app
    arm = harness.state.anexos_canal
    harness.state.db.execute(
        "INSERT INTO canal_entradas(canal, id_externo, tipo, do_dono, ref_mensagem, tamanho, estado, recebida_em)"
        " VALUES ('telegram', 'r1', 'mensagem', 1, 'r1', 0, 'ignorada', '2026-10-04T00:00:00Z'),"
        " ('telegram', 'r2', 'mensagem', 0, 'r2', 0, 'ignorada', '2026-10-04T00:00:00Z')")
    e1 = harness.state.db.one("SELECT id FROM canal_entradas WHERE id_externo='r1'")["id"]
    e2 = harness.state.db.one("SELECT id FROM canal_entradas WHERE id_externo='r2'")["id"]
    dono = int(arm.guardar(JPEG, tipos=TIPOS, max_bytes=10_000, entrada_id=e1)["id"])
    convidado = int(arm.guardar(JPEG + b"x", tipos=TIPOS, max_bytes=10_000, entrada_id=e2)["id"])
    pdf = int(arm.guardar(PDF, tipos=TIPOS, max_bytes=10_000, entrada_id=e1)["id"])
    # o provedor falso e o hub de verdade: o custo tem de entrar em ai_calls
    falso = DescritorFalso()
    leitor = harness.state.leitor_de_anexos
    leitor._simulado = lambda: False
    leitor._conferir_gasto = lambda: None                      # o hub simulado do harness não tem `conferir_gasto`
    leitor._descritor = lambda: falso
    app = create_app(harness.cfg, state=harness.state)
    app.state.poc = harness.state
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 123)),
                                 base_url="http://localhost") as cli:
        sem = await cli.post(f"/api/canais/anexos/{dono}/ler", json={})
        assert sem.status_code == 400 and sem.json()["detail"]["code"] == "confirmacao_necessaria" and falso.chamadas == 0
        ok = await cli.post(f"/api/canais/anexos/{dono}/ler", json={"confirmar": True})
        assert ok.status_code == 200, ok.text
        corpo = ok.json()
        assert corpo["do_cache"] is False and corpo["custo_usd"] == pytest.approx(0.0019) and "Entrar" in corpo["descricao"]
        de_novo = (await cli.post(f"/api/canais/anexos/{dono}/ler", json={"confirmar": True})).json()
        assert de_novo["do_cache"] is True and de_novo["custo_usd"] == 0 and falso.chamadas == 1
        for ident, codigo, status in ((convidado, "anexo_nao_permitido", 409), (pdf, "anexo_nao_imagem", 422),
                                      (9999, "anexo_desconhecido", 404)):
            r = await cli.post(f"/api/canais/anexos/{ident}/ler", json={"confirmar": True})
            assert (r.status_code, r.json()["detail"]["code"]) == (status, codigo)
    assert falso.chamadas == 1
    [uso] = harness.state.db.query("SELECT origem, ref, role, model, input_tokens, output_tokens, with_image FROM ai_calls")
    assert (uso["origem"], uso["ref"], uso["role"], uso["model"], uso["input_tokens"], uso["output_tokens"],
            uso["with_image"]) == ("canais", f"anexo:{dono}", "canais", "claude-haiku-4-5", 1300, 120, 1)
