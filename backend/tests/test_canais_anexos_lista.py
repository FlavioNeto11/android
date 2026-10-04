"""28.24 F4: a lista de anexos e o conteúdo para a tela Anexos do painel.

Prova `simulated`: `AppState` do harness (SQLite), arquivos reais em pasta temporária, nenhum canal nem Trello. O que se trava:
a lista e o conteúdo ficam atrás do mesmo login das outras rotas de canais; a lista não vaza caminho de disco, `sha256`, referência
do canal nem nome de remetente; o anexo de convidado não tem conteúdo; só imagem e PDF saem, com `no-store` e nome neutro; filtros e
paginação batem com o banco.
"""
from __future__ import annotations

import json

import httpx
import pytest
from pydantic import SecretStr

from app.main import create_app
from app.modules.avisos.infrastructure.entrada_sql import EntradasDoCanal

from .conftest import Harness

pytestmark = pytest.mark.asyncio

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x02" * 60
PDF = b"%PDF-1.4\n" + b"\x01" * 40
TEXTO = "anotação do dono, só texto".encode()
TIPOS = ("image/png", "application/pdf", "text/plain")
CHAVES = {"id", "canal", "direcao", "mime", "bytes", "estado", "motivo_recusa", "criado_em", "apagado_em", "do_dono",
          "tem_conteudo", "pode_ir_ao_cartao", "pode_ler", "descricao", "lida_em"}


def _entrada(h: Harness, *, do_dono: bool = True, canal: str = "telegram", tipo: str = "mensagem") -> int:
    repo = EntradasDoCanal(h.state.db, canal=canal)
    n = int(h.state.db.scalar("SELECT COUNT(*) FROM canal_entradas") or 0) + 1
    repo.gravar(id_externo=str(n), ordem=n, tipo=tipo, do_dono=do_dono, ref_mensagem=str(n), responde_a=None, texto=None,
                tamanho=0, estado="ignorada")
    return repo.id_de(str(n))


def _anexo(h: Harness, conteudo: bytes = PNG, *, do_dono: bool = True, direcao: str = "entrada", canal: str = "telegram",
           recusado: bool = False) -> int:
    arm = h.state.anexos_canal
    padrao, arm.canal = arm.canal, canal                          # o armazém grava no canal dele; o teste troca por uma linha
    try:
        entrada = _entrada(h, do_dono=do_dono, canal=canal) if direcao == "entrada" else None
        if recusado:
            return int(arm.recusar("voz não é aceita.", entrada_id=entrada)["id"])
        return int(arm.guardar(conteudo, tipos=TIPOS, max_bytes=10_000, entrada_id=entrada, direcao=direcao)["id"])
    finally:
        arm.canal = padrao


def _cliente(h: Harness, host: str = "localhost") -> httpx.AsyncClient:
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 123)), base_url=f"http://{host}")


async def test_a_lista_traz_so_o_que_o_painel_precisa_sem_caminho_hash_nem_remetente(harness: Harness) -> None:
    dono = _anexo(harness)
    _anexo(harness, PDF, direcao="saida")
    async with _cliente(harness) as cli:
        r = await cli.get("/api/canais/anexos")
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["total"] == 2 and corpo["limit"] == 24 and corpo["offset"] == 0
    assert [i["id"] for i in corpo["items"]] == sorted((i["id"] for i in corpo["items"]), reverse=True)   # o mais novo primeiro
    for item in corpo["items"]:
        assert set(item) == CHAVES
    texto = json.dumps(corpo).lower()
    sha = harness.state.db.scalar("SELECT sha256 FROM canal_anexos WHERE id=?", (dono,))
    assert sha not in texto and json.dumps(str(harness.state.anexos_canal.pasta))[1:-1].lower() not in texto
    for proibido in ("caminho", "sha256", "ref_externa", "mime_declarado", "remetente", "from", "nome", "token"):
        assert proibido not in texto, proibido
    por_id = {i["id"]: i for i in corpo["items"]}
    assert por_id[dono]["do_dono"] is True and por_id[dono]["pode_ir_ao_cartao"] is True and por_id[dono]["tem_conteudo"] is True
    saida = next(i for i in corpo["items"] if i["direcao"] == "saida")
    assert saida["do_dono"] is False and saida["pode_ir_ao_cartao"] is False and saida["tem_conteudo"] is True


async def test_lista_vazia_e_pendente_nao_entra(harness: Harness) -> None:
    entrada = _entrada(harness)
    harness.state.anexos_canal.pendente("ref-opaca", entrada_id=entrada, mime_declarado="image/png", tamanho=10)
    async with _cliente(harness) as cli:
        r = await cli.get("/api/canais/anexos")
    assert r.status_code == 200 and r.json() == {"items": [], "total": 0, "limit": 24, "offset": 0}
    assert "ref-opaca" not in r.text


async def test_filtros_de_canal_direcao_dono_estado_e_periodo(harness: Harness) -> None:
    a_dono = _anexo(harness)
    a_convidado = _anexo(harness, do_dono=False)
    a_saida = _anexo(harness, PDF, direcao="saida")
    a_recusa = _anexo(harness, recusado=True)
    a_trello = _anexo(harness, canal="trello")
    harness.state.db.execute("UPDATE canal_anexos SET criado_em=? WHERE id=?", ("2026-01-10T12:00:00.000Z", a_dono))
    harness.state.db.execute("UPDATE canal_anexos SET criado_em=? WHERE id=?", ("2026-02-10T12:00:00.000Z", a_saida))

    async def ids(**q: object) -> set[int]:
        async with _cliente(harness) as cli:
            r = await cli.get("/api/canais/anexos", params={"limit": 100, **q})
        assert r.status_code == 200, r.text
        assert r.json()["total"] == len(r.json()["items"])
        return {i["id"] for i in r.json()["items"]}

    assert await ids(canal="trello") == {a_trello}
    assert await ids(direcao="saida") == {a_saida}
    assert await ids(direcao="entrada") == {a_dono, a_convidado, a_recusa, a_trello}
    assert await ids(do_dono="true") == {a_dono, a_recusa, a_trello}
    assert await ids(do_dono="false") == {a_convidado, a_saida}
    assert await ids(estado="recusado") == {a_recusa}
    assert await ids(desde="2026-02-01") == {a_saida, a_convidado, a_recusa, a_trello}
    assert await ids(ate="2026-02-01") == {a_dono}
    assert await ids(desde="2026-01-01T00:00:00Z", ate="2026-03-01T00:00:00Z") == {a_dono, a_saida}
    assert await ids(canal="telegram", direcao="entrada", do_dono="true") == {a_dono, a_recusa}


async def test_paginacao_total_e_ordem(harness: Harness) -> None:
    feitos = [_anexo(harness, PNG + bytes([n])) for n in range(7)]
    async with _cliente(harness) as cli:
        p1 = (await cli.get("/api/canais/anexos", params={"limit": 3})).json()
        p2 = (await cli.get("/api/canais/anexos", params={"limit": 3, "offset": 3})).json()
        p3 = (await cli.get("/api/canais/anexos", params={"limit": 3, "offset": 6})).json()
        fora = (await cli.get("/api/canais/anexos", params={"limit": 3, "offset": 60})).json()
    assert [p["total"] for p in (p1, p2, p3, fora)] == [7, 7, 7, 7]
    assert [i["id"] for p in (p1, p2, p3) for i in p["items"]] == sorted(feitos, reverse=True)
    assert fora["items"] == [] and p2["offset"] == 3


@pytest.mark.parametrize("q", [{"direcao": "lateral"}, {"estado": "pendente"}, {"limit": 0}, {"limit": 101}, {"offset": -1},
                               {"desde": "ontem"}, {"ate": "2026-13-45"}, {"do_dono": "talvez"}])
async def test_parametro_invalido_e_recusado_sem_tocar_o_banco(harness: Harness, q: dict[str, object]) -> None:
    async with _cliente(harness) as cli:
        r = await cli.get("/api/canais/anexos", params=q)
    assert r.status_code == 422, r.text


async def test_parametros_nao_viram_sql(harness: Harness) -> None:
    _anexo(harness)
    async with _cliente(harness) as cli:
        r = await cli.get("/api/canais/anexos", params={"canal": "x' OR '1'='1"})
    assert r.status_code == 200 and r.json()["total"] == 0


async def test_conteudo_so_de_imagem_e_pdf_com_no_store_e_nome_neutro(harness: Harness) -> None:
    png, pdf = _anexo(harness), _anexo(harness, PDF)
    async with _cliente(harness) as cli:
        a, b = await cli.get(f"/api/canais/anexos/{png}/conteudo"), await cli.get(f"/api/canais/anexos/{pdf}/conteudo")
    assert a.status_code == 200 and a.content == PNG and a.headers["content-type"] == "image/png"
    assert b.status_code == 200 and b.content == PDF and b.headers["content-type"] == "application/pdf"
    for r, ident, ext in ((a, png, "png"), (b, pdf, "pdf")):
        assert r.headers["cache-control"] == "no-store" and r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["content-disposition"] == f'attachment; filename="anexo-{ident}.{ext}"'


async def test_texto_guardado_nao_tem_previa(harness: Harness) -> None:
    ident = _anexo(harness, TEXTO)
    async with _cliente(harness) as cli:
        r = await cli.get(f"/api/canais/anexos/{ident}/conteudo")
        lista = (await cli.get("/api/canais/anexos")).json()["items"]
    assert r.status_code == 415 and r.json()["detail"]["code"] == "tipo_sem_previa"
    assert TEXTO not in r.content and lista[0]["tem_conteudo"] is False and lista[0]["pode_ir_ao_cartao"] is True


async def test_anexo_de_convidado_nao_tem_conteudo(harness: Harness) -> None:
    ident = _anexo(harness, do_dono=False)
    async with _cliente(harness) as cli:
        r = await cli.get(f"/api/canais/anexos/{ident}/conteudo")
        item = (await cli.get("/api/canais/anexos")).json()["items"][0]
    assert r.status_code == 404 and r.json()["detail"]["code"] == "anexo_de_convidado" and PNG not in r.content
    assert item["tem_conteudo"] is False and item["pode_ir_ao_cartao"] is False and item["do_dono"] is False


async def test_recusado_e_apagado_nao_tem_conteudo_na_lista(harness: Harness) -> None:
    _anexo(harness, recusado=True)
    apagado = _anexo(harness, PDF)
    harness.state.db.execute("UPDATE canal_anexos SET estado='apagado' WHERE id=?", (apagado,))
    async with _cliente(harness) as cli:
        itens = (await cli.get("/api/canais/anexos")).json()["items"]
    assert [i["estado"] for i in itens] == ["apagado", "recusado"]
    assert not any(i["tem_conteudo"] or i["pode_ir_ao_cartao"] for i in itens)
    assert itens[1]["motivo_recusa"] == "voz não é aceita."


async def test_lista_exige_o_mesmo_login_das_outras_rotas_de_canais(harness: Harness) -> None:
    harness.cfg.file.server.host = "0.0.0.0"                       # noqa: S104 - o cenário sob teste: acesso de fora
    harness.cfg.file.server.public_hosts = ["parque.local"]
    harness.cfg.env.api_token = SecretStr("tk-lista-anexos-3c7f")
    ident = _anexo(harness)
    async with _cliente(harness, "parque.local") as cli:
        for caminho in ("/api/canais/anexos", f"/api/canais/anexos/{ident}/conteudo"):
            assert (await cli.get(caminho)).status_code == 401, caminho
        ok = await cli.get("/api/canais/anexos", headers={"Authorization": "Bearer tk-lista-anexos-3c7f"})
        assert ok.status_code == 200 and ok.json()["total"] == 1
        for metodo in ("post", "put", "patch", "delete"):
            r = await getattr(cli, metodo)("/api/canais/anexos", headers={"Authorization": "Bearer tk-lista-anexos-3c7f"})
            assert r.status_code == 405, metodo


async def test_f5_pode_ler_so_a_imagem_do_dono_e_a_descricao_gravada_vem_na_lista(harness: Harness) -> None:
    """28.24 F5: o botão Ler aparece só na imagem do dono; a descrição gravada (já redigida na leitura) vem junto."""
    imagem = _anexo(harness)
    pdf = _anexo(harness, PDF)
    saida = _anexo(harness, direcao="saida")
    harness.state.db.execute("UPDATE canal_anexos SET descricao=?, lida_em=? WHERE id=?",
                             ("Uma tela de login.", "2026-10-04T20:00:00.000Z", imagem))
    async with _cliente(harness) as cli:
        por_id = {i["id"]: i for i in (await cli.get("/api/canais/anexos")).json()["items"]}
    assert por_id[imagem]["pode_ler"] is True and por_id[imagem]["descricao"] == "Uma tela de login."
    assert por_id[imagem]["lida_em"] == "2026-10-04T20:00:00.000Z"
    assert por_id[pdf]["pode_ler"] is False and por_id[saida]["pode_ler"] is False
    assert por_id[pdf]["descricao"] is None


async def test_f5_tipo_que_a_entrada_nao_aceita_nao_vai_ao_cartao(harness: Harness) -> None:
    """28.24 F5: o PDF guardado antes de a lista de tipos mudar não oferece "Anexar ao cartão"."""
    pdf = _anexo(harness, PDF)
    harness.cfg.file.avisos.entrada.anexos.tipos = ["image/png"]
    async with _cliente(harness) as cli:
        item = (await cli.get("/api/canais/anexos")).json()["items"][0]
    assert item["id"] == pdf and item["pode_ir_ao_cartao"] is False
