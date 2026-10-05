"""Exclusão de contatos do site a pedido do titular (29.83, ADR-075): busca por todos os dígitos, a Canais antes do
DELETE, o registro sem dado do titular e as rotas só para uma pessoa na sessão.

Prova `simulated`: o harness de sempre e uma Canais FALSA no lugar de `apagar_avisos_do_portal` (o contrato do 28.34;
o código dela não está nesta base). Nomes e telefones fictícios; nada vai a Telegram.
"""
from __future__ import annotations

import importlib
import json
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr

from app.main import create_app
from app.modules.portal.application.exclusao import MuitasBuscas, ServicoDeExclusao
from app.modules.portal.domain.exclusao import chave_do_telefone, final, mesmo_telefone, nome_do_operador
from app.util import now

from .conftest import Harness
from .relogio_do_portal import RelogioParado

BUSCA = "/api/portal/contatos/busca"
EXCLUIR = "/api/portal/contatos/excluir"
SEGREDO = "tk-portal-exclusao-5c1f2a"          # token de teste, não existe fora daqui
# Fictícios: os DDDs 10 e 20 não existem no Brasil (e não começam com 0, que a busca tira como o de discagem).
TEL_A = "+55 (10) 90000-0001"
TEL_A_OUTRA_GRAFIA = "10900000001"
TEL_OUTRO_DDD = "(20) 90000-0001"


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
    assert h.state is not None
    if not isinstance(h.state.portal.relogio, RelogioParado):
        h.state.portal.relogio = RelogioParado()            # T.2: o teto por hora pela rota anda pelo relógio do teste
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
def test_telefone_compara_o_numero_inteiro_com_o_55_e_o_0_opcionais() -> None:
    assert chave_do_telefone("+55 (10) 90000-0001") == chave_do_telefone("10 90000-0001") == "br:10900000001"
    assert chave_do_telefone("0 10 90000-0001") == "br:10900000001"                 # o 0 de discagem sai
    assert mesmo_telefone(TEL_A_OUTRA_GRAFIA, TEL_A)
    assert not mesmo_telefone("10900000001", TEL_OUTRO_DDD)            # mesmos 9 finais, outro DDD
    assert not mesmo_telefone("90000-0001", TEL_A)                     # o número inteiro, nunca os finais
    assert not mesmo_telefone(TEL_A, "")                               # o descarte apaga: nunca casa
    assert chave_do_telefone("1234567") is None and chave_do_telefone("1" * 31) is None
    # O mínimo conta os zeros da frente, como o formulário: 0 + 7 dígitos passou lá e é achado aqui (revisão E3-b).
    assert chave_do_telefone("0 9000-0001") == "num:90000001" and mesmo_telefone("09000-0001", "0 9000 0001")
    assert chave_do_telefone("00000000") is None                       # só zeros (a isca da prova de fora)
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
    b = _gravar(harness, "10 90000 0001", estado="pendente")
    _gravar(harness, TEL_OUTRO_DDD)
    async with _logado(harness) as c:
        r = await c.post(BUSCA, json={"telefone": TEL_A_OUTRA_GRAFIA})
        curto = await c.post(BUSCA, json={"telefone": "9000-001"})
        nada = await c.post(BUSCA, json={"telefone": "(10) 91111-1111"})
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
    # A premissa da repetição (falha do Portal depois do `ok`): chamar de novo sobre a lápide devolve `ok` outra vez.
    for contato_id in (na_fila, fora_da_fila):
        de_novo = await st.avisos.apagar_avisos_do_portal(contato_id, now())
        assert de_novo.estado == "ok", (contato_id, de_novo)


# ------------------------------------------------------------------ teto de buscas e a janela entre módulos
async def test_31a_busca_do_operador_da_429_e_o_outro_operador_segue(harness: Harness,
                                                                     caplog: pytest.LogCaptureFixture) -> None:
    """30 por hora por operador (`portal.limites.buscas_por_operador_hora`); a busca inválida não conta; o log leva
    o operador e a contagem, nunca o telefone."""
    assert harness.cfg.file.portal.limites.buscas_por_operador_hora == 30
    _gravar(harness, TEL_A)
    with caplog.at_level("INFO", logger="poc.portal"):
        async with _logado(harness) as c:
            assert (await c.post("/api/portal/contatos/busca", json={"telefone": "1"})).status_code == 422
            for _ in range(30):
                assert (await c.post("/api/portal/contatos/busca", json={"telefone": TEL_A})).status_code == 200
            r = await c.post("/api/portal/contatos/busca", json={"telefone": TEL_A})
    assert r.status_code == 429 and r.json()["detail"]["code"] == "muitas_buscas"
    assert 1 <= int(r.headers["retry-after"]) <= 3601
    assert harness.state is not None
    relogio = harness.state.portal.relogio
    assert isinstance(relogio, RelogioParado)
    relogio.avancar(3600.5)                                  # a hora passou, sem esperar de verdade (T.2)
    async with _logado(harness) as c:
        assert (await c.post("/api/portal/contatos/busca", json={"telefone": TEL_A})).status_code == 200
    async with _cliente(harness) as outro:
        assert (await outro.post("/api/login", json={"operator": "Outra Pessoa"})).status_code == 200
        assert (await outro.post("/api/portal/contatos/busca", json={"telefone": TEL_A})).status_code == 200
    assert "busca de exclusão por Operadora Teste: 1 achado(s)" in caplog.text
    assert "90000" not in caplog.text and "0001" not in caplog.text


def test_teto_de_buscas_libera_depois_da_hora(harness: Harness) -> None:
    assert harness.state is not None
    servico = harness.state.portal.exclusao
    for i in range(30):
        servico.buscar(TEL_A, operador="x", agora_s=1000.0 + i)
    with pytest.raises(MuitasBuscas):
        servico.buscar(TEL_A, operador="x", agora_s=1100.0)
    assert servico.buscar(TEL_A, operador="x", agora_s=1000.0 + 3600.5) == []     # a 1ª saiu da janela


def test_nome_que_so_muda_na_caixa_divide_o_balde(harness: Harness) -> None:
    """O nome do operador é declarado no login: "Ana" e "ana" são o mesmo balde (revisão do #342, E4)."""
    assert harness.state is not None
    servico = harness.state.portal.exclusao
    for i in range(30):
        servico.buscar(TEL_A, operador="Ana" if i % 2 else "ana", agora_s=1000.0 + i)
    for nome in ("ANA", "Ana", "ana"):
        with pytest.raises(MuitasBuscas):
            servico.buscar(TEL_A, operador=nome, agora_s=1100.0)


def test_teto_geral_barra_o_terceiro_nome(harness: Harness, caplog: pytest.LogCaptureFixture) -> None:
    """Um nome novo a cada login ganha outro balde por operador, mas não outro balde geral: 60 por hora somando todos
    (`portal.limites.buscas_total_hora`). O log diz quem bateu no teto geral, sem telefone."""
    assert harness.state is not None
    assert harness.cfg.file.portal.limites.buscas_total_hora == 60
    servico = harness.state.portal.exclusao
    for nome in ("primeira", "segunda"):
        for i in range(30):
            servico.buscar(TEL_A, operador=nome, agora_s=1000.0 + i)
    with caplog.at_level("WARNING", logger="poc.portal"), pytest.raises(MuitasBuscas) as erro:
        servico.buscar(TEL_A, operador="terceira", agora_s=1100.0)
    assert 1 <= erro.value.espera_s <= 3601
    assert "teto geral de buscas de exclusão (60 na hora), pedido de terceira" in caplog.text
    assert "90000" not in caplog.text
    assert servico.buscar(TEL_A, operador="terceira", agora_s=1000.0 + 3600.5 + 30) == []   # a hora passou


def test_dono_busca_com_o_teto_geral_esgotado_e_nao_gasta_o_dos_outros(harness: Harness) -> None:
    """29.89: dois convidados esgotam as 60 da hora; o dono (`pedidos.operadores_do_dono`, comparado como o pedidos
    compara: sem espaço sobrando e sem caixa) segue buscando, com a cota de um operador, e as buscas dele não entram
    no teto somado."""
    assert harness.state is not None
    harness.cfg.file.pedidos.operadores_do_dono = [" Dono  Teste "]          # espaço sobrando e por dentro
    servico = harness.state.portal.exclusao
    for nome in ("primeira", "segunda"):
        for i in range(30):
            servico.buscar(TEL_A, operador=nome, agora_s=1000.0 + i)
    with pytest.raises(MuitasBuscas):
        servico.buscar(TEL_A, operador="terceira", agora_s=1100.0)
    for i in range(30):
        servico.buscar(TEL_A, operador="dono teste" if i % 2 else "DONO TESTE", agora_s=1100.0 + i)
    with pytest.raises(MuitasBuscas):                                   # a cota de um operador vale para o dono
        servico.buscar(TEL_A, operador="Dono Teste", agora_s=1200.0)
    # A hora dos convidados passou e a do dono não: as 30 dele não ocuparam o teto somado.
    for i in range(60):
        servico.buscar(TEL_A, operador=f"convidado-{i % 2}", agora_s=1000.0 + 3600.5 + 30 + i / 100)


def test_sem_dono_declarado_ninguem_escapa_do_teto_geral(harness: Harness) -> None:
    assert harness.state is not None
    assert harness.cfg.file.pedidos.operadores_do_dono == []
    servico = harness.state.portal.exclusao
    for nome in ("primeira", "segunda"):
        for i in range(30):
            servico.buscar(TEL_A, operador=nome, agora_s=1000.0 + i)
    with pytest.raises(MuitasBuscas):
        servico.buscar(TEL_A, operador="Dono Teste", agora_s=1100.0)


def test_espaco_dentro_do_nome_nao_abre_outro_balde(harness: Harness) -> None:
    """A chave do balde junta os espaços de dentro: "Ana  Maria" é "Ana Maria", e não ganha mais 30 buscas."""
    assert harness.state is not None
    assert nome_do_operador("Ana  Maria") == nome_do_operador(" ana maria ") == "ana maria"
    servico = harness.state.portal.exclusao
    for i in range(30):
        servico.buscar(TEL_A, operador="Ana Maria", agora_s=1000.0 + i)
    with pytest.raises(MuitasBuscas):
        servico.buscar(TEL_A, operador="Ana  Maria", agora_s=1100.0)


def test_duas_threads_no_limite_nao_passam_as_duas() -> None:
    """A rota chama a busca em threads do pool. Com o teto em 1 e a leitura do teto lenta de propósito (abre a janela
    da corrida), oito buscas ao mesmo tempo: só uma passa, e a limpeza não quebra com o dicionário mudando."""
    class RepoVazio:
        def com_telefone(self) -> list[object]:
            return []

    def teto_lento() -> int:
        time.sleep(0.02)
        return 1

    servico = ServicoDeExclusao(RepoVazio(), apagar_no_canal=lambda: None,  # type: ignore[arg-type]
                                canal_presente=lambda: False, buscas_por_hora=teto_lento, buscas_no_total=lambda: 1000)
    largada = threading.Barrier(8)
    passou: list[int] = []
    barrado: list[int] = []

    def uma(i: int) -> None:
        largada.wait()
        try:
            servico.buscar(TEL_A, operador="mesma", agora_s=1000.0 + i / 100)
            passou.append(i)
        except MuitasBuscas:
            barrado.append(i)

    fios = [threading.Thread(target=uma, args=(i,)) for i in range(8)]
    for f in fios:
        f.start()
    for f in fios:
        f.join(10)
    assert (len(passou), len(barrado)) == (1, 7)


async def test_falha_do_portal_depois_do_ok_da_canais_e_a_repeticao_resolve(harness: Harness,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    """A janela entre módulos: a Canais já disse `ok` (lápide posta) e a transação do Portal cai. Nada fica
    meio-apagado: a linha e o registro seguem como estavam; repetir apaga e registra UMA vez (a Canais é idempotente
    na chave e devolve `ok` de novo)."""
    assert harness.state is not None
    a = _gravar(harness, TEL_A)
    canais = _canais(harness, monkeypatch, CanaisFalsa(padrao=ApagadoFalso("ok", 0)))
    repo = harness.state.portal.repo
    original = repo.excluir

    def quebra(**kw: object) -> int:
        raise RuntimeError("banco caiu no meio")

    monkeypatch.setattr(repo, "excluir", quebra)
    async with _cliente(harness) as c:
        assert (await c.post("/api/login", json={"operator": "Operadora Teste"})).status_code == 200
        with pytest.raises(RuntimeError):
            await c.post("/api/portal/contatos/excluir", json={"ids": [a], "pedido_por": "telefone"})
        assert _ids_no_banco(harness) == {a} and _registros(harness) == []
        monkeypatch.setattr(repo, "excluir", original)
        r = await c.post("/api/portal/contatos/excluir", json={"ids": [a], "pedido_por": "telefone"})
    assert r.status_code == 200 and r.json()["apagados"] == [a]
    assert _ids_no_banco(harness) == set() and len(_registros(harness)) == 1
    assert canais is not None and canais.chamados == [a, a]


# ------------------------------------------------------------------ revisão do #342 (E1, E2)
async def test_acha_e_apaga_o_que_o_formulario_aceitou_fora_do_formato_br(harness: Harness,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """O formulário aceita 8 dígitos ou mais; a exclusão acha tudo isso, pelo número INTEIRO (E1)."""
    sem_ddd = _gravar(harness, "90000-0001")                          # 9 dígitos, sem DDD
    internacional = _gravar(harness, "+1 (000) 555-0100 ramal 22")  # 13 dígitos, não começa com 55
    comprido = _gravar(harness, "+351 000 000 000 0001")              # 16 dígitos
    com_zero = _gravar(harness, "0 10 90000-0002")                   # o 0 de discagem
    _canais(harness, monkeypatch, CanaisFalsa())
    async with _logado(harness) as c:
        achados = {}
        for nome, tel in (("sem_ddd", "900000001"), ("internacional", "1000555010022"),
                          ("comprido", "3510000000000001"), ("com_zero", "10 90000-0002")):
            r = await c.post(BUSCA, json={"telefone": tel})
            assert r.status_code == 200, (nome, r.text)
            achados[nome] = [x["id"] for x in r.json()["contatos"]]
        prefixo = await c.post(BUSCA, json={"telefone": "1000555"})     # 7 dígitos: abaixo do mínimo do formulário
        parte = await c.post(BUSCA, json={"telefone": "10005550100"})   # o começo do internacional: não casa
        r = await c.post(EXCLUIR, json={"ids": [sem_ddd, internacional, comprido, com_zero], "pedido_por": "telefone"})
    assert achados == {"sem_ddd": [sem_ddd], "internacional": [internacional], "comprido": [comprido],
                       "com_zero": [com_zero]}
    assert prefixo.status_code == 422 and parte.json()["contatos"] == []
    assert r.json()["apagados"] == [sem_ddd, internacional, comprido, com_zero] and _ids_no_banco(harness) == set()


async def test_quem_sumiu_no_meio_nao_e_contado_como_apagado(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """Outra exclusão (ou a faxina) leva a linha enquanto a Canais responde: esta não diz que apagou (E2)."""
    assert harness.state is not None
    st = harness.state
    leva = _gravar(harness, TEL_A)
    fica = _gravar(harness, TEL_A)

    class CanaisQueDeixaOutroApagar(CanaisFalsa):
        async def __call__(self, contato_id: int, agora: datetime) -> ApagadoFalso:
            if contato_id == leva:
                st.db.execute("DELETE FROM portal_contatos WHERE id=?", (leva,))
            return await super().__call__(contato_id, agora)

    _canais(harness, monkeypatch, CanaisQueDeixaOutroApagar())
    async with _logado(harness) as c:
        r = await c.post(EXCLUIR, json={"ids": [leva, fica], "pedido_por": "outro"})
    assert r.json()["apagados"] == [fica] and r.json()["inexistentes"] == [leva]
    [reg] = _registros(harness)
    assert json.loads(str(reg["ids"])) == [fica]


async def test_em_envio_mantem_e_o_segundo_gesto_fecha(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """Com a ponta nova do #340, `em_envio` quer dizer "o aviso já reivindicado termina de sair": a primeira exclusão
    mantém a linha, e é a SEGUNDA que fecha (a Canais apaga a mensagem pelo registro do envio, ou a põe em `a_mao`)."""
    a = _gravar(harness, TEL_A)

    class CanaisQueTerminaDeEnviar(CanaisFalsa):
        async def __call__(self, contato_id: int, agora: datetime) -> ApagadoFalso:
            self.chamados.append(contato_id)
            return ApagadoFalso("em_envio") if len(self.chamados) == 1 else ApagadoFalso("ok", 1)

    canais = _canais(harness, monkeypatch, CanaisQueTerminaDeEnviar())
    async with _logado(harness) as c:
        primeira = await c.post(EXCLUIR, json={"ids": [a], "pedido_por": "telefone"})
        assert primeira.json()["mantidos"] == [{"id": a, "motivo": "em_envio"}] and _ids_no_banco(harness) == {a}
        segunda = await c.post(EXCLUIR, json={"ids": [a], "pedido_por": "telefone"})
    assert segunda.json()["apagados"] == [a] and segunda.json()["mensagens_apagadas"] == 1
    assert _ids_no_banco(harness) == set() and canais is not None and canais.chamados == [a, a]
    registros = _registros(harness)
    assert [json.loads(str(r["mantidos"])) for r in registros] == [[{"id": a, "motivo": "em_envio"}], []]
    assert [json.loads(str(r["ids"])) for r in registros] == [[], [a]]
