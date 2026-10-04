"""Exclusão de contatos do site a pedido do titular (29.83, ADR-075): busca por todos os dígitos, a Canais antes do
DELETE, o registro sem dado do titular e as rotas só para uma pessoa na sessão.

Prova `simulated`: o harness de sempre e uma Canais FALSA no lugar de `apagar_avisos_do_portal` (o contrato do 28.34;
o código dela não está nesta base). Nomes e telefones fictícios; nada vai a Telegram.
"""
from __future__ import annotations

import importlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr

from app.main import create_app
from app.modules.portal.domain.exclusao import final, mesmo_telefone, nacional
from app.util import now

from .conftest import Harness

BUSCA = "/api/portal/contatos/busca"
EXCLUIR = "/api/portal/contatos/excluir"
SEGREDO = "tk-portal-exclusao-5c1f2a"          # token de teste, não existe fora daqui
# Fictícios: DDD 00 e 99 não existem, e o 21 só aparece para provar que outro DDD não casa.
TEL_A = "+55 (00) 90000-0001"
TEL_A_OUTRA_GRAFIA = "00900000001"
TEL_OUTRO_DDD = "(99) 90000-0001"


@dataclass(frozen=True)
class ApagadoFalso:
    estado: str
    apagadas: int = 0
    a_mao: tuple[str, ...] = ()


@dataclass
class CanaisFalsa:
    respostas: dict[int, ApagadoFalso] = field(default_factory=dict)
    padrao: ApagadoFalso = ApagadoFalso("ok", 1)
    chamados: list[int] = field(default_factory=list)
    explode: bool = False

    async def __call__(self, contato_id: int, agora: datetime) -> ApagadoFalso:
        self.chamados.append(contato_id)
        if self.explode:
            raise RuntimeError("canal fora")
        return self.respostas.get(contato_id, self.padrao)


def _gravar(h: Harness, telefone: str, *, estado: str = "entregue", mensagem: str = "quero saber mais") -> int:
    assert h.state is not None
    return h.state.portal.repo.gravar(nome="Pessoa Fictícia", empresa="Empresa Fictícia", telefone=telefone,
                                      mensagem=mensagem, cliente_hash="h", agora=now(), estado=estado)


def _canais(h: Harness, monkeypatch: pytest.MonkeyPatch, canais: CanaisFalsa | None) -> CanaisFalsa | None:
    assert h.state is not None
    if canais is not None:
        monkeypatch.setattr(h.state.avisos, "apagar_avisos_do_portal", canais, raising=False)
    return canais


def _cliente(h: Harness, **kw: object) -> httpx.AsyncClient:
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", **kw)  # type: ignore[arg-type]


@asynccontextmanager
async def _logado(h: Harness) -> AsyncIterator[httpx.AsyncClient]:
    async with _cliente(h) as c:
        assert (await c.post("/api/login", json={"operator": "Operadora Teste"})).status_code == 200
        yield c


def _ids_no_banco(h: Harness) -> set[int]:
    assert h.state is not None
    return {int(r["id"]) for r in h.state.db.query("SELECT id FROM portal_contatos")}


def _registros(h: Harness) -> list[dict[str, object]]:
    assert h.state is not None
    return [dict(r) for r in h.state.db.query("SELECT * FROM portal_exclusoes ORDER BY id")]


# ------------------------------------------------------------------ domínio
def test_telefone_compara_todos_os_digitos_com_o_55_opcional() -> None:
    assert nacional("+55 (00) 90000-0001") == nacional("00 90000-0001") == "00900000001"
    assert mesmo_telefone(TEL_A_OUTRA_GRAFIA, TEL_A)
    assert not mesmo_telefone("00900000001", TEL_OUTRO_DDD)            # mesmos 9 finais, outro DDD
    assert not mesmo_telefone("90000-0001", TEL_A)                     # sem DDD não é telefone completo
    assert not mesmo_telefone(TEL_A, "")                               # o descarte apaga: nunca casa
    assert nacional("123") is None and nacional("1" * 14) is None
    assert final(TEL_A) == "0001"


# ------------------------------------------------------------------ rotas só para pessoa na sessão
async def test_sem_sessao_e_401_mesmo_no_loopback_e_com_bearer(harness: Harness) -> None:
    harness.cfg.env.api_token = SecretStr(SEGREDO)
    _gravar(harness, TEL_A)
    async with _cliente(harness) as c:                                  # loopback, sem cookie
        # Os caminhos literais: `test_cobertura_de_rotas` lê a chamada, não resolve constante.
        assert (await c.post("/api/portal/contatos/busca", json={"telefone": TEL_A})).status_code == 401
        assert (await c.post("/api/portal/contatos/excluir", json={"ids": [1], "pedido_por": "telefone"})).status_code == 401
        for rota, corpo in ((BUSCA, {"telefone": TEL_A}), (EXCLUIR, {"ids": [1], "pedido_por": "telefone"})):
            r = await c.post(rota, json=corpo)
            assert r.status_code == 401, (rota, r.text)
            assert r.json()["detail"]["code"] in ("sessao_exigida", "unauthorized")
            r = await c.post(rota, json=corpo, headers={"Authorization": f"Bearer {SEGREDO}"})
            assert r.status_code == 401 and r.json()["detail"]["code"] == "sessao_exigida", rota
    assert len(_ids_no_banco(harness)) == 1 and _registros(harness) == []


async def test_get_nas_rotas_nao_existe(harness: Harness) -> None:
    async with _logado(harness) as c:
        assert (await c.get(BUSCA + "?telefone=00900000001")).status_code == 405
        assert (await c.get(EXCLUIR)).status_code == 405


# ------------------------------------------------------------------ busca
async def test_busca_mostra_so_id_data_estado_e_final(harness: Harness) -> None:
    a = _gravar(harness, TEL_A)
    b = _gravar(harness, "00 90000 0001", estado="pendente")
    _gravar(harness, TEL_OUTRO_DDD)
    async with _logado(harness) as c:
        r = await c.post(BUSCA, json={"telefone": TEL_A_OUTRA_GRAFIA})
        curto = await c.post(BUSCA, json={"telefone": "90000-0001"})
        nada = await c.post(BUSCA, json={"telefone": "(00) 91111-1111"})
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    contatos = r.json()["contatos"]
    assert [x["id"] for x in contatos] == [a, b]
    assert all(set(x) == {"id", "criado_em", "estado", "final"} and x["final"] == "0001" for x in contatos)
    assert "Fictícia" not in r.text and "quero saber" not in r.text and "90000" not in r.text
    assert curto.status_code == 422 and curto.json()["detail"]["code"] == "telefone_invalido"
    assert nada.json() == {"contatos": []}


async def test_corpo_ruim(harness: Harness) -> None:
    async with _logado(harness) as c:
        assert (await c.post(BUSCA, content=b"telefone=1", headers={"Content-Type": "text/plain"})).status_code == 415
        assert (await c.post(BUSCA, content=b"{", headers={"Content-Type": "application/json"})).status_code == 422
        assert (await c.post(BUSCA, json=["x"])).status_code == 422
        grande = json.dumps({"telefone": "1" * 5000})
        assert (await c.post(BUSCA, content=grande, headers={"Content-Type": "application/json"})).status_code == 413
        for corpo in ({"ids": [], "pedido_por": "telefone"}, {"ids": list(range(1, 52)), "pedido_por": "telefone"},
                      {"ids": ["1"], "pedido_por": "telefone"}, {"ids": [True], "pedido_por": "telefone"},
                      {"ids": [0], "pedido_por": "telefone"}):
            r = await c.post(EXCLUIR, json=corpo)
            assert r.status_code == 422 and r.json()["detail"]["code"] == "ids_invalidos", corpo
        for pedido_por in ("", "email", "Fulano pediu", None):
            r = await c.post(EXCLUIR, json={"ids": [1], "pedido_por": pedido_por})
            assert r.status_code == 422 and r.json()["detail"]["code"] == "pedido_por_invalido", pedido_por
    assert _registros(harness) == []


# ------------------------------------------------------------------ exclusão
async def test_apaga_com_ok_da_canais_e_registra_sem_dado_do_titular(harness: Harness,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    a = _gravar(harness, TEL_A)
    b = _gravar(harness, TEL_A, estado="pendente")
    fica = _gravar(harness, TEL_OUTRO_DDD)
    canais = _canais(harness, monkeypatch, CanaisFalsa(respostas={b: ApagadoFalso("ok", 0, ("2026-10-01T10:00:00+00:00",))}))
    async with _logado(harness) as c:
        r = await c.post(EXCLUIR, json={"ids": [a, b, a, 9999], "pedido_por": "formulario"})
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["apagados"] == [a, b] and corpo["mantidos"] == [] and corpo["inexistentes"] == [9999]
    assert corpo["mensagens_apagadas"] == 1
    assert corpo["mensagens_a_mao"] == [{"contato_id": b, "enviada_em": "2026-10-01T10:00:00+00:00"}]
    assert corpo["sem_canal"] is False
    assert canais is not None and canais.chamados == [a, b]               # o inexistente nem vai à Canais
    assert _ids_no_banco(harness) == {fica}
    [reg] = _registros(harness)
    assert reg["executado_por"] == "Operadora Teste" and reg["pedido_por"] == "formulario"
    assert json.loads(str(reg["ids"])) == [a, b] and json.loads(str(reg["mantidos"])) == []
    assert reg["mensagens_apagadas"] == 1 and reg["mensagens_a_mao"] == 1
    texto = json.dumps(reg, default=str)
    assert "Fictícia" not in texto and "90000" not in texto and "quero saber" not in texto


async def test_em_envio_e_falha_da_canais_mantem_a_linha(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    saindo = _gravar(harness, TEL_A)
    falha = _gravar(harness, TEL_A)
    ok = _gravar(harness, TEL_A)
    _canais(harness, monkeypatch, CanaisFalsa(respostas={saindo: ApagadoFalso("em_envio"), falha: ApagadoFalso("falhou")}))
    async with _logado(harness) as c:
        r = await c.post(EXCLUIR, json={"ids": [saindo, falha, ok], "pedido_por": "telefone"})
    corpo = r.json()
    assert corpo["apagados"] == [ok]
    assert corpo["mantidos"] == [{"id": saindo, "motivo": "em_envio"}, {"id": falha, "motivo": "falhou"}]
    assert _ids_no_banco(harness) == {saindo, falha}
    [reg] = _registros(harness)
    assert json.loads(str(reg["mantidos"])) == [{"id": saindo, "motivo": "em_envio"}, {"id": falha, "motivo": "falhou"}]


async def test_excecao_da_canais_mantem_e_nao_vaza_no_log(harness: Harness, monkeypatch: pytest.MonkeyPatch,
                                                         caplog: pytest.LogCaptureFixture) -> None:
    a = _gravar(harness, TEL_A)
    _canais(harness, monkeypatch, CanaisFalsa(explode=True))
    with caplog.at_level("INFO", logger="poc.portal"):
        async with _logado(harness) as c:
            r = await c.post(EXCLUIR, json={"ids": [a], "pedido_por": "outro"})
    assert r.json()["mantidos"] == [{"id": a, "motivo": "falhou"}] and _ids_no_banco(harness) == {a}
    assert "RuntimeError" in caplog.text
    assert "Fictícia" not in caplog.text and "90000" not in caplog.text and "canal fora" not in caplog.text


async def test_sem_a_exclusao_da_canais_e_com_o_aviso_dela_nada_e_apagado(harness: Harness,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """28.32 na base sem o 28.34: um aviso na fila sairia depois da exclusão, então o Portal não apaga."""
    assert harness.state is not None
    a = _gravar(harness, TEL_A)
    monkeypatch.setattr(harness.state.avisos, "apagar_avisos_do_portal", None, raising=False)
    monkeypatch.setattr(harness.state.avisos, "avisar_contato_do_portal", lambda contato: None, raising=False)
    async with _logado(harness) as c:
        r = await c.post(EXCLUIR, json={"ids": [a], "pedido_por": "telefone"})
    assert r.json()["mantidos"] == [{"id": a, "motivo": "canal_sem_exclusao"}] and r.json()["sem_canal"] is False
    assert _ids_no_banco(harness) == {a}


async def test_sem_canal_nenhum_apaga_e_diz_sem_canal(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    assert harness.state is not None
    a = _gravar(harness, TEL_A)
    monkeypatch.setattr(harness.state.avisos, "apagar_avisos_do_portal", None, raising=False)
    monkeypatch.setattr(harness.state.avisos, "avisar_contato_do_portal", None, raising=False)
    async with _logado(harness) as c:
        r = await c.post(EXCLUIR, json={"ids": [a], "pedido_por": "telefone"})
    assert r.json()["apagados"] == [a] and r.json()["sem_canal"] is True
    assert _ids_no_banco(harness) == set()


async def test_so_inexistentes_nao_registra(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    _canais(harness, monkeypatch, CanaisFalsa())
    async with _logado(harness) as c:
        r = await c.post(EXCLUIR, json={"ids": [4242], "pedido_por": "telefone"})
    assert r.json()["inexistentes"] == [4242] and _registros(harness) == []


async def test_vale_com_o_contato_desligado(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """A promessa da página continua depois de desligar o formulário: a exclusão não depende das bandeiras."""
    harness.cfg.file.portal.site_ligado = False
    harness.cfg.file.portal.contato_ligado = False
    a = _gravar(harness, TEL_A)
    _canais(harness, monkeypatch, CanaisFalsa())
    async with _logado(harness) as c:
        busca = await c.post(BUSCA, json={"telefone": TEL_A})
        r = await c.post(EXCLUIR, json={"ids": [a], "pedido_por": "telefone"})
    assert [x["id"] for x in busca.json()["contatos"]] == [a] and r.json()["apagados"] == [a]


def test_registro_fica_depois_da_retencao(harness: Harness) -> None:
    """O registro é a prova do atendimento: a faxina dos 180 dias apaga contatos, nunca o registro."""
    assert harness.state is not None
    repo = harness.state.portal.repo
    repo.excluir(ids=[], mantidos=[(1, "falhou")], pedido_por="outro", executado_por="x", mensagens_apagadas=0,
                 mensagens_a_mao=0, agora=now() - timedelta(days=400))
    repo.apagar_vencidos(now())
    assert len(_registros(harness)) == 1


def test_id_de_contato_apagado_nunca_volta(harness: Harness) -> None:
    """A Canais deixa uma lápide na chave `portal:<id>` (28.34): se um contato novo herdasse o id do excluído, o aviso
    dele cairia na lápide e nunca sairia. `{{PK_AUTO}}` é `AUTOINCREMENT` no SQLite e `BIGSERIAL` no PostgreSQL, e
    nenhum dos dois reusa id; este teste trava isso, apagando justamente o MAIOR id (o caso em que o SQLite sem
    AUTOINCREMENT reusaria)."""
    assert harness.state is not None
    repo = harness.state.portal.repo
    _gravar(harness, TEL_A)
    maior = _gravar(harness, TEL_A)
    repo.excluir(ids=[maior], mantidos=[], pedido_por="telefone", executado_por="x", mensagens_apagadas=0,
                 mensagens_a_mao=0, agora=now())
    assert _gravar(harness, TEL_A) > maior


# ------------------------------------------------------------------ contra a Canais REAL (28.34, #340)
def _sem_a_exclusao_da_canais(h: Harness) -> bool:
    """Pula só enquanto o 28.34 não está na base. Se o domínio da Canais já tem `ApagadoNoCanal` e o serviço não tem a
    função, é a peça que mudou de lugar: o teste FALHA em vez de pular calado (nota da orquestradora, 04/10)."""
    assert h.state is not None
    tem_funcao = callable(getattr(h.state.avisos, "apagar_avisos_do_portal", None))
    try:
        dominio = importlib.import_module("app.modules.avisos.domain.portal")
    except ImportError:
        return not tem_funcao
    if hasattr(dominio, "ApagadoNoCanal"):
        assert tem_funcao, "ApagadoNoCanal existe e state.avisos.apagar_avisos_do_portal não: o contrato do 28.34 mudou"
    return not tem_funcao


async def test_canais_real_lapide_impede_o_aviso_depois_da_exclusao(harness: Harness,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    """O contato entregue à fila (ainda não enviado) e o que nunca chegou à fila: os dois saem, e um avisar depois da
    exclusão (o laço de reenvio no meio do caminho) não põe nada para sair na chave do contato."""
    if _sem_a_exclusao_da_canais(harness):
        pytest.skip("o 28.34 (#340) ainda não está nesta base")
    from .test_avisos_servico import CanalFalso

    assert harness.state is not None
    st = harness.state
    harness.cfg.file.avisos.enabled = True
    monkeypatch.setattr(st.avisos, "_canal", CanalFalso())
    na_fila = _gravar(harness, TEL_A, estado="pendente")
    campos = {"nome": "Pessoa Fictícia", "empresa": "", "telefone": TEL_A, "mensagem": "teste fictício"}
    assert st.portal.contatos.entregar(na_fila, campos, now()) == "entregue"
    fora_da_fila = _gravar(harness, TEL_A, estado="pendente")
    async with _logado(harness) as c:
        r = await c.post("/api/portal/contatos/excluir", json={"ids": [na_fila, fora_da_fila], "pedido_por": "telefone"})
    assert r.status_code == 200, r.text
    assert r.json()["apagados"] == [na_fila, fora_da_fila] and r.json()["mantidos"] == []
    assert _ids_no_banco(harness) == set()
    # O laço de reenvio que já tinha lido a linha antes do DELETE tenta avisar: a lápide faz disso um no-op.
    for contato_id in (na_fila, fora_da_fila):
        st.portal.contatos.entregar(contato_id, campos, now())
    linhas = [dict(r) for r in st.db.query(
        "SELECT chave, estado, corpo FROM avisos_entregas WHERE chave IN (?, ?) ORDER BY chave",
        (f"portal:{na_fila}", f"portal:{fora_da_fila}"))]
    assert [x["chave"] for x in linhas] == sorted([f"portal:{na_fila}", f"portal:{fora_da_fila}"])
    # Nada para sair e nenhum corpo: o que estava na fila virou lápide (`descartado`), o que o canal falso já mandou
    # ficou `enviado` (a chave ocupada bloqueia do mesmo jeito), e o que nunca chegou à fila ganhou a lápide.
    assert all(x["estado"] in ("descartado", "enviado") and x["corpo"] == "" for x in linhas), linhas
    assert any(x["estado"] == "descartado" for x in linhas), linhas
    # A mensagem já enviada saiu do chat agora (menos de 47 h) ou ficou para apagar à mão, nunca esquecida.
    enviadas = sum(1 for x in linhas if x["estado"] == "enviado")
    assert r.json()["mensagens_apagadas"] + len(r.json()["mensagens_a_mao"]) >= enviadas, r.json()
