"""Item 28.25 — listar e desfazer as decisões automáticas (rotas, prazo, idempotência e a inversa de cada fila).

Prova `simulated` (`arquivo::teste`): `AppState` do harness (SQLite), o livro de aprendizado de verdade e nenhum aparelho.
O desfazer do aprendizado é DESLIGAR o item (`published → disabled`, contrato da orquestradora de 04/10); a contraprova é
que `published → validated` não existe na trilha. Pergunta, objetivo e pedido não têm inversa segura: 409.
"""
from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
from pydantic import SecretStr

from app.main import create_app
from app.modules.decisoes.application.desfazer import DesfazerDecisoes, SemInversa, SemInversaSegura
from app.modules.decisoes.domain.decisao import Decisao
from app.modules.learning.domain.livro import Escopo, NovoItem
from app.modules.learning.domain.vocabulario import LivroKind, SourceKind
from app.modules.learning.domain.ciclo import SkillState
from app.shared.decisoes import NovaDecisao
from app.util import now, to_iso

from .conftest import Harness

TOKEN_DA_API = "tk-parque-decisoes-7c3a"


def _app(h: Harness, *, token: str | None = None):
    h.cfg.file.server.host = "0.0.0.0"                       # noqa: S104 - o cenário sob teste: acesso de fora
    h.cfg.file.server.public_hosts = ["parque.local"]
    h.cfg.env.api_token = SecretStr(TOKEN_DA_API)
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    cab = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 123)),
                             base_url="http://parque.local", headers=cab)


def _cliente(h: Harness) -> httpx.AsyncClient:
    return _app(h, token=TOKEN_DA_API)


def _publicar_licao(h: Harness, conteudo: str = "tocar em [row_x]") -> str:
    """Uma lição publicada, pelo livro de verdade (a pessoa valida e publica: o que importa aqui é o estado)."""
    livro = h.state.learning
    item = livro._repo.criar_item(  # noqa: SLF001
        NovoItem(kind=LivroKind.LICAO, escopo=Escopo(app="com.exemplo.app", capability="OPEN_POST", role="actor"),
                 content={"modelo": "alvo_ausente", "alvo": conteudo}, summary=f"Em OPEN_POST: {conteudo}.",
                 source_kind=SourceKind.RECOVERY, side_effect=False, app_version="1"),
        by="sistema", estado=SkillState.CANDIDATE, detalhe=None, reason="criado no teste")
    livro.mudar_estado(LivroKind.LICAO, item.id, SkillState.VALIDATED, by="painel:dono", reason="validei")
    livro.mudar_estado(LivroKind.LICAO, item.id, SkillState.PUBLISHED, by="painel:dono", reason="publiquei")
    return item.id


def _decidir(h: Harness, fila: str, item_ref: str, *, regra: str = "x", origem: str | None = None,
             fatos: dict | None = None, dias_atras: float = 0.0) -> int:
    reg = h.state.decisoes_registro
    quando = to_iso(now() - timedelta(days=dias_atras))
    reg.registrar(NovaDecisao(fila, item_ref, origem or f"{fila}:{item_ref}:{regra}", regra, f"Efeito de {fila}.",
                              fatos or {}, quando))
    return [d for d in reg.listar() if d.origem_ref == (origem or f"{fila}:{item_ref}:{regra}")][0].id


def _trilha(h: Harness, item_ref: str) -> list[tuple[str | None, str, str]]:
    return [(r["from_state"], r["to_state"], r["decided_by"]) for r in h.state.db.query(
        "SELECT from_state, to_state, decided_by FROM learning_transitions WHERE item_ref=? ORDER BY id", (item_ref,))]


# ===================================================================== listar
async def test_sem_sessao_as_duas_rotas_respondem_401(harness: Harness) -> None:
    async with _app(harness) as c:
        assert (await c.get("/api/decisoes-automaticas")).status_code == 401
        assert (await c.post(f"/api/decisoes-automaticas/1/desfazer", json={"confirmar": True})).status_code == 401


async def test_listar_filtra_e_diz_por_que_nao_da_para_desfazer(harness: Harness) -> None:
    lic = _publicar_licao(harness)
    _decidir(harness, "aprendizado", lic, regra="auto:qa_revisar v1", fatos={"kind": "licao", "para": "published"})
    _decidir(harness, "pergunta", "r-1", regra="31.43-pergunta-24h", fatos={"horas": 24})
    _decidir(harness, "objetivo", "o-1", regra="31.43-objetivo-24h", dias_atras=3)
    async with _cliente(harness) as c:
        todos = (await c.get("/api/decisoes-automaticas")).json()
        assert todos["total"] == 3 and todos["desfazer_dias"] == 7
        assert todos["regras"] == ["31.43-objetivo-24h", "31.43-pergunta-24h", "auto:qa_revisar v1"]
        por_fila = {i["fila"]: i for i in todos["itens"]}
        assert por_fila["aprendizado"]["pode_desfazer"] is True and por_fila["aprendizado"]["acao_do_desfazer"] == "Desligar"
        assert por_fila["pergunta"]["pode_desfazer"] is False and por_fila["pergunta"]["acao_do_desfazer"] == "Desfazer"
        assert por_fila["pergunta"]["por_que_nao"].startswith("não dá para desfazer automaticamente: ")
        assert por_fila["pergunta"]["fatos"] == {"horas": 24}
        assert [i["fila"] for i in (await c.get("/api/decisoes-automaticas", params={"fila": "pergunta"})).json()["itens"]] == ["pergunta"]
        assert [i["fila"] for i in (await c.get("/api/decisoes-automaticas", params={"regra": "31.43-objetivo-24h"})).json()["itens"]] == ["objetivo"]
        ontem = to_iso(now() - timedelta(days=1))
        assert {i["fila"] for i in (await c.get("/api/decisoes-automaticas", params={"desde": ontem})).json()["itens"]} == {"aprendizado", "pergunta"}
        assert (await c.get("/api/decisoes-automaticas", params={"fila": "inventada"})).status_code == 400
        assert (await c.get("/api/decisoes-automaticas", params={"desde": "ontem"})).status_code == 400
        assert (await c.get("/api/decisoes-automaticas", params={"desfeitas": "talvez"})).status_code == 422


async def test_listar_vazio_nao_e_erro(harness: Harness) -> None:
    async with _cliente(harness) as c:
        r = await c.get("/api/decisoes-automaticas")
    assert r.status_code == 200 and r.json()["itens"] == [] and r.json()["total"] == 0


# ===================================================================== desfazer
async def test_sem_confirmar_responde_400_e_nada_muda(harness: Harness) -> None:
    lic = _publicar_licao(harness)
    did = _decidir(harness, "aprendizado", lic, fatos={"kind": "licao", "para": "published"})
    async with _cliente(harness) as c:
        for corpo in ({}, {"confirmar": False, "motivo": "x"}):
            r = await c.post(f"/api/decisoes-automaticas/{did}/desfazer", json=corpo)
            assert r.status_code == 400 and r.json()["detail"]["code"] == "confirmation_required"
    assert harness.state.decisoes_registro.obter(did).desfeita is False       # type: ignore[union-attr]
    assert _trilha(harness, lic)[-1][1] == "published"


async def test_aprendizado_desfazer_e_desligar_e_nunca_voltar_para_validated(harness: Harness) -> None:
    lic = _publicar_licao(harness)
    did = _decidir(harness, "aprendizado", lic, regra="auto:qa_revisar v1", fatos={"kind": "licao", "para": "published"})
    async with _cliente(harness) as c:
        r = await c.post(f"/api/decisoes-automaticas/{did}/desfazer", json={"confirmar": True, "motivo": "não era para publicar"})
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["desfeita"] is True and corpo["desfeita_agora"] is True and corpo["desfeita_por"] == "panel"
    assert corpo["motivo_do_desfazer"] == "não era para publicar" and corpo["pode_desfazer"] is False
    trilha = _trilha(harness, lic)
    assert trilha[-1] == ("published", "disabled", "panel")
    # contraprova: published → validated NUNCA é chamado (a trilha inteira, não só a última linha)
    assert ("published", "validated") not in [(a, b) for a, b, _ in trilha]
    razao = harness.state.db.scalar("SELECT reason FROM learning_transitions WHERE item_ref=? ORDER BY id DESC", (lic,))
    assert razao.startswith("desligado pelo dono: não era para publicar")


async def test_desfazer_duas_vezes_e_idempotente(harness: Harness) -> None:
    lic = _publicar_licao(harness)
    did = _decidir(harness, "aprendizado", lic, fatos={"kind": "licao", "para": "published"})
    async with _cliente(harness) as c:
        a = await c.post(f"/api/decisoes-automaticas/{did}/desfazer", json={"confirmar": True, "motivo": "primeiro"})
        n = len(_trilha(harness, lic))
        b = await c.post(f"/api/decisoes-automaticas/{did}/desfazer", json={"confirmar": True, "motivo": "segundo"})
    assert a.status_code == b.status_code == 200
    assert b.json()["desfeita_agora"] is False and b.json()["motivo_do_desfazer"] == "primeiro"
    assert len(_trilha(harness, lic)) == n, "o segundo pedido mexeu no livro"


async def test_item_ja_desligado_por_uma_pessoa_conta_como_desfeito_sem_nova_transicao(harness: Harness) -> None:
    lic = _publicar_licao(harness)
    harness.state.learning.mudar_estado(LivroKind.LICAO, lic, SkillState.DISABLED, by="painel:dono", reason="desliguei")
    n = len(_trilha(harness, lic))
    did = _decidir(harness, "aprendizado", lic, fatos={"kind": "licao", "para": "published"})
    async with _cliente(harness) as c:
        r = await c.post(f"/api/decisoes-automaticas/{did}/desfazer", json={"confirmar": True})
    assert r.status_code == 200 and r.json()["desfeita"] is True
    assert len(_trilha(harness, lic)) == n


async def test_item_que_mudou_de_estado_nao_e_desligado_as_cegas(harness: Harness) -> None:
    lic = _publicar_licao(harness)
    harness.state.learning.mudar_estado(LivroKind.LICAO, lic, SkillState.DEPRECATED, by="painel:dono", reason="aposentei")
    did = _decidir(harness, "aprendizado", lic, fatos={"kind": "licao", "para": "published"})
    async with _cliente(harness) as c:
        r = await c.post(f"/api/decisoes-automaticas/{did}/desfazer", json={"confirmar": True})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "sem_inversa_segura"
    assert harness.state.decisoes_registro.obter(did).desfeita is False       # type: ignore[union-attr]


@pytest.mark.parametrize("fila", ["pergunta", "objetivo", "pedido"])
async def test_filas_sem_inversa_segura_respondem_409_e_nao_marcam(harness: Harness, fila: str) -> None:
    did = _decidir(harness, fila, "x-1", regra="31.43")
    async with _cliente(harness) as c:
        r = await c.post(f"/api/decisoes-automaticas/{did}/desfazer", json={"confirmar": True})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "sem_inversa_segura"
    assert r.json()["detail"]["message"].startswith("não dá para desfazer automaticamente: ")
    assert harness.state.decisoes_registro.obter(did).desfeita is False       # type: ignore[union-attr]


async def test_prazo_dentro_e_fora_dos_7_dias(harness: Harness) -> None:
    dentro, fora = _publicar_licao(harness, "a"), _publicar_licao(harness, "b")
    d1 = _decidir(harness, "aprendizado", dentro, fatos={"kind": "licao", "para": "published"}, dias_atras=6.9)
    d2 = _decidir(harness, "aprendizado", fora, fatos={"kind": "licao", "para": "published"}, dias_atras=7.1)
    async with _cliente(harness) as c:
        assert (await c.post(f"/api/decisoes-automaticas/{d1}/desfazer", json={"confirmar": True})).status_code == 200
        r = await c.post(f"/api/decisoes-automaticas/{d2}/desfazer", json={"confirmar": True})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "prazo_vencido"
        item = [i for i in (await c.get("/api/decisoes-automaticas")).json()["itens"] if i["id"] == d2][0]
        assert item["pode_desfazer"] is False and "prazo" in item["por_que_nao"]
    assert _trilha(harness, fora)[-1][1] == "published", "fora do prazo o livro não pode ser tocado"
    # o prazo é configuração
    harness.cfg.file.avisos.decisoes_automaticas.desfazer_dias = 30
    async with _cliente(harness) as c:
        assert (await c.post(f"/api/decisoes-automaticas/{d2}/desfazer", json={"confirmar": True})).status_code == 200


async def test_decisao_inexistente_responde_404(harness: Harness) -> None:
    async with _cliente(harness) as c:
        r = await c.post(f"/api/decisoes-automaticas/99999/desfazer", json={"confirmar": True})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "not_found"


async def test_corpo_com_campo_estranho_e_recusado(harness: Harness) -> None:
    async with _cliente(harness) as c:
        r = await c.post(f"/api/decisoes-automaticas/1/desfazer", json={"confirmar": True, "desfeita_por": "outro"})
    assert r.status_code == 422, "o autor vem da sessão, nunca do corpo"


# ===================================================================== o serviço, com inversas falsas
class InversaFalsa:
    def __init__(self, motivo: str | None = None) -> None:
        self.motivo, self.chamadas = motivo, 0

    def por_que_nao(self, d: Decisao) -> str | None:
        return self.motivo

    def desfazer(self, d: Decisao, *, por: str, motivo: str | None) -> None:
        self.chamadas += 1
        if self.motivo:
            raise SemInversaSegura(self.motivo)


async def test_a_inversa_so_e_chamada_uma_vez_e_so_quando_ha_inversa(harness: Harness) -> None:
    ok, nao = InversaFalsa(), InversaFalsa("porque sim")
    srv = DesfazerDecisoes(harness.state.decisoes_registro, {"pergunta": ok, "pedido": nao}, dias=lambda: 7)
    a, b = _decidir(harness, "pergunta", "r-1"), _decidir(harness, "pedido", "p-1")
    d, fez = srv.desfazer(a, por="dono", motivo=None)
    d2, fez2 = srv.desfazer(a, por="outro", motivo="de novo")
    assert (fez, fez2, ok.chamadas, d.desfeita_por, d2.desfeita_por) == (True, False, 1, "dono", "dono")
    with pytest.raises(SemInversaSegura, match="^não dá para desfazer automaticamente: porque sim$"):
        srv.desfazer(b, por="dono", motivo=None)
    assert harness.state.decisoes_registro.obter(b).desfeita is False         # type: ignore[union-attr]
    assert SemInversa("m").por_que_nao(d) == "m"


# ===================================================================== a porta do aprendizado, com um livro falso
class EntradaFalsa:
    def __init__(self, state: SkillState) -> None:
        self.state, self.state_at = state, None


class _Trilha:
    """O detalhe do livro com o desligamento de uma PESSOA (28.33: só o da regra automática recusa o botão)."""

    def __init__(self) -> None:
        from types import SimpleNamespace
        self.trilha = [SimpleNamespace(id=1, to_state=SkillState.DISABLED, decided_by="dono", decided_at=to_iso(now()),
                                       reason="desliguei")]


class LivroFalso:
    """O que a inversa do aprendizado usa do `LearningService`: `entrada`, `mudar_estado` e `extensao`."""

    def __init__(self, state: SkillState | None, *, pareceres: object | None = None, falha: Exception | None = None) -> None:
        self.state, self.chamadas, self._pareceres, self._falha = state, [], pareceres, falha

    def extensao(self, tipo: type) -> object | None:
        return self._pareceres

    def entrada(self, kind: LivroKind, ref: str) -> EntradaFalsa:
        from app.modules.learning.domain.ciclo import NaoEncontrado
        if self.state is None:
            raise NaoEncontrado(f"Não há {kind.value} '{ref}' no livro.")
        return EntradaFalsa(self.state)

    def detalhe(self, kind: LivroKind, ref: str) -> _Trilha:
        return _Trilha()

    def mudar_estado(self, kind: LivroKind, ref: str, para: SkillState, *, by: str, reason: str) -> None:
        self.chamadas.append((kind, ref, para, by, reason))
        if self._falha is not None:
            raise self._falha


def _decisao(item_ref: str, kind: str = "receita", para: str = "published") -> Decisao:
    return Decisao(id=1, fila="aprendizado", item_ref=item_ref, origem_ref="o", regra="auto:qa_revisar v1", efeito="e",
                   fatos={"kind": kind, "para": para}, decidida_em=to_iso(now()))


def test_desfazer_de_receita_chama_mudar_estado_com_disabled_o_ref_nativo_e_o_dono() -> None:
    from app.decisoes_inversas import InversaDoAprendizado
    livro = LivroFalso(SkillState.PUBLISHED)
    InversaDoAprendizado(livro).desfazer(_decisao("receita:180"), por="painel:dono", motivo="engano")   # type: ignore[arg-type]
    (kind, ref, para, by, razao), = livro.chamadas
    assert (kind, ref, para, by) == (LivroKind.RECEITA, "180", SkillState.DISABLED, "painel:dono")
    assert razao == "desligado pelo dono: engano" and by != "plataforma"
    assert all(c[2] is not SkillState.VALIDATED for c in livro.chamadas)


def test_com_o_curador_composto_o_caminho_e_o_servico_dos_pareceres_como_na_rota() -> None:
    from app.decisoes_inversas import InversaDoAprendizado
    pareceres = LivroFalso(SkillState.PUBLISHED)
    livro = LivroFalso(SkillState.PUBLISHED, pareceres=pareceres)
    InversaDoAprendizado(livro).desfazer(_decisao("fluxo:7", "fluxo"), por="dono", motivo=None)       # type: ignore[arg-type]
    assert livro.chamadas == [] and len(pareceres.chamadas) == 1
    assert pareceres.chamadas[0][:4] == (LivroKind.FLUXO, "7", SkillState.DISABLED, "dono")


def test_ja_desligado_e_sucesso_idempotente_sem_chamar_o_livro() -> None:
    from app.decisoes_inversas import InversaDoAprendizado
    livro = LivroFalso(SkillState.DISABLED)
    InversaDoAprendizado(livro).desfazer(_decisao("receita:1"), por="dono", motivo=None)               # type: ignore[arg-type]
    assert livro.chamadas == []


def test_corrida_em_que_outra_sessao_desligou_primeiro_tambem_e_sucesso() -> None:
    from app.decisoes_inversas import InversaDoAprendizado
    from app.modules.learning.domain.ciclo import TransicaoProibida
    livro = LivroFalso(SkillState.PUBLISHED, falha=TransicaoProibida("Transição proibida: disabled → disabled."))
    estados = iter([SkillState.PUBLISHED, SkillState.DISABLED])
    livro.entrada = lambda kind, ref: EntradaFalsa(next(estados))                                      # type: ignore[method-assign]
    InversaDoAprendizado(livro).desfazer(_decisao("receita:1"), por="dono", motivo=None)               # type: ignore[arg-type]


def test_item_que_nao_existe_mais_diz_o_porque_e_nao_desliga_nada() -> None:
    from app.decisoes_inversas import InversaDoAprendizado
    livro = LivroFalso(None)
    with pytest.raises(SemInversaSegura, match="não existe mais"):
        InversaDoAprendizado(livro).desfazer(_decisao("receita:404"), por="dono", motivo=None)         # type: ignore[arg-type]
    assert livro.chamadas == []


@pytest.mark.parametrize("fatos_para,kind", [("validated", "receita"), ("published", "inventado")])
def test_so_o_publicado_de_um_tipo_conhecido_tem_inversa(fatos_para: str, kind: str) -> None:
    from app.decisoes_inversas import InversaDoAprendizado
    livro = LivroFalso(SkillState.PUBLISHED)
    inversa = InversaDoAprendizado(livro)                                                              # type: ignore[arg-type]
    assert inversa.por_que_nao(_decisao("receita:1", kind, fatos_para)) is not None
    with pytest.raises(SemInversaSegura):
        inversa.desfazer(_decisao("receita:1", kind, fatos_para), por="dono", motivo=None)
    assert livro.chamadas == []
