"""`por_que_nao_publica` na rota do livro com o veto e o modo do pacote (item 30.5) e a camada por pacote (30.1, 30.20).

- `GET /api/aprendizado/{kind}/{ref}` diz `modo_desligado` quando o modo do TIPO (e do pacote do item) não é `on`, e
  `vetado` (com o motivo) quando uma pessoa desligou aquele conteúdo; o veto vem antes do modo;
- o modo do pacote em `on` com o global em `shadow` tira o `modo_desligado`;
- `ModosDeUso.do_pacote` leva o modo efetivo à camada de uso e `CosturasDoLivro.licoes_para` consulta o fornecedor
  quando só um pacote está ligado.

Nível de prova: `simulated` (banco de teste, nenhuma IA).
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.config import LearningCfg
from app.modules.learning.application.ports import Ajustes
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.camada import Camada, ModosDeUso, uso_do_item
from app.modules.learning.domain.ciclo import SkillState
from app.modules.learning.domain.livro import Escopo, NovoItem
from app.modules.learning.domain.vocabulario import LivroKind, Modo, ModoDeTelas, SourceKind
from app.modules.learning.infrastructure.fontes import FontesSql
from app.modules.learning.infrastructure.ligar_costuras import CosturasDoLivro, Extensoes
from app.modules.learning.infrastructure.montagem import ajustes_do_config
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.modules.learning.infrastructure.sql_repository import SqlLearningRepository
from app.modules.learning.presentation.router import router as learning_router
from app.taskqueue.costuras import PedidoDeLicoes

from .fake_skills import banco as banco_migrado

IG = "com.instagram.android"
OUTRO = "com.exemplo.outro"
AGORA = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


class Mundo:
    def __init__(self, tmp_path: Path) -> None:
        self.db = banco_migrado(tmp_path, "rota-publicacao.sqlite3")
        self.cfg = LearningCfg()
        repo = SqlLearningRepository(self.db, precos=dict)
        self.servico = LearningService(repo, FontesSql(self.db), TriagemDeCredencial(),
                                       ajustes=lambda: ajustes_do_config(self.cfg), relogio=lambda: AGORA,
                                       retencao_de_logs_dias=lambda: 14)

    def licao(self, app: str, texto: str = "confira o destinatário") -> str:
        return self.servico.propor(NovoItem(
            kind=LivroKind.LICAO, escopo=Escopo(app=app, capability="abrir_perfil", role="actor"),
            content={"texto": texto}, summary=texto, source_kind=SourceKind.RECOVERY, side_effect=False)).id


@pytest.fixture
def mundo(tmp_path: Path) -> Iterator[Mundo]:
    m = Mundo(tmp_path)
    yield m
    m.db.close()


@pytest.fixture
async def cliente(mundo: Mundo) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(learning_router)
    app.state.poc = SimpleNamespace(learning=mundo.servico)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _porque(cliente: httpx.AsyncClient, ref: str) -> dict[str, object] | None:
    r = await cliente.get(f"/api/aprendizado/licao/{ref}")
    assert r.status_code == 200, r.text
    return r.json()["item"]["por_que_nao_publica"]  # type: ignore[no-any-return]


async def test_a_rota_diz_modo_desligado_e_o_modo_do_pacote_a_tira(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    ref = mundo.licao(IG)
    mundo.cfg.licoes.modo = "shadow"
    assert await _porque(cliente, ref) == {"codigo": "modo_desligado", "espera_o_dono": False, "detalhe": None}
    mundo.cfg.licoes.por_app = {OUTRO: "on"}                      # outro pacote ligado não liga este
    assert (await _porque(cliente, ref) or {})["codigo"] == "modo_desligado"
    mundo.cfg.licoes.por_app = {IG: "on"}
    assert await _porque(cliente, ref) is None
    mundo.cfg.licoes.modo = "on"
    mundo.cfg.licoes.por_app = {IG: "shadow"}                     # o pacote mais restrito que o global também vale
    assert (await _porque(cliente, ref) or {})["codigo"] == "modo_desligado"


async def test_a_rota_diz_vetado_antes_do_modo(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    mundo.cfg.licoes.modo = "on"
    ref = mundo.licao(IG)
    assert await _porque(cliente, ref) is None                    # candidata, modo on, sem veto
    r = await cliente.post(f"/api/aprendizado/licao/{ref}/status", json={"to": "disabled", "reason": "ficou ruim"})
    assert r.status_code == 200, r.text
    assert r.json()["item"]["state"] == SkillState.DISABLED.value
    motivo = await _porque(cliente, ref)
    assert motivo is not None and motivo["codigo"] == "vetado" and motivo["espera_o_dono"] is False
    assert "pessoa" in str(motivo["detalhe"])
    mundo.cfg.licoes.modo = "shadow"                              # o veto vem antes do modo
    assert (await _porque(cliente, ref) or {})["codigo"] == "vetado"


def test_o_contexto_de_publicacao_do_servico(mundo: Mundo) -> None:
    mundo.cfg.licoes.modo = "shadow"
    mundo.cfg.licoes.por_app = {IG: "on"}
    entrada = mundo.servico.entrada(LivroKind.LICAO, mundo.licao(IG))
    assert mundo.servico.contexto_de_publicacao(entrada) == (True, None)
    outra = mundo.servico.entrada(LivroKind.LICAO, mundo.licao(OUTRO, "outra"))
    assert mundo.servico.contexto_de_publicacao(outra) == (False, None)


def test_ajustes_do_config_leva_o_modo_por_pacote() -> None:
    cfg = LearningCfg()
    cfg.licoes.por_app = {IG: "on"}
    cfg.telas.por_app = {OUTRO: "observe"}
    a = ajustes_do_config(cfg)
    assert dict(a.por_licoes) == {IG: Modo.ON} and dict(a.por_telas) == {OUTRO: ModoDeTelas.OBSERVE}
    assert Ajustes().por_licoes == {} and Ajustes().por_telas == {}


def test_a_camada_de_uso_segue_o_modo_do_pacote() -> None:
    modos = ModosDeUso(licoes=Modo.SHADOW, telas=ModoDeTelas.OBSERVE, licoes_por_app={IG: Modo.ON},
                       telas_por_app={IG: ModoDeTelas.ON})
    publicada = SkillState.PUBLISHED
    assert uso_do_item(LivroKind.LICAO, publicada, modos.do_pacote(IG)).camada is Camada.VAI_AO_PROMPT
    assert uso_do_item(LivroKind.LICAO, publicada, modos.do_pacote(OUTRO)).camada is Camada.NAO_MEDIDO
    assert uso_do_item(LivroKind.LICAO, publicada, modos.do_pacote("")).camada is Camada.NAO_MEDIDO
    assert uso_do_item(LivroKind.TELA, publicada, modos.do_pacote(IG)).camada is Camada.CLASSIFICA_TELA
    assert uso_do_item(LivroKind.TELA, publicada, modos.do_pacote(OUTRO)).camada is Camada.MEDIDO_NAO_USADO
    assert ModosDeUso(licoes=None).do_pacote(IG).licoes is None   # o modo que não se soube ler segue desconhecido


class _Fornecedor:
    def licoes_para(self, pedido: PedidoDeLicoes) -> list[str]:
        return ["uma lição"]


def _pedido(app: str) -> PedidoDeLicoes:
    return PedidoDeLicoes(papel="actor", unidade="step:1", run_id="r1", app_package=app,  # type: ignore[arg-type]
                          capability="abrir_perfil", step_hash="h", simulated=False)


def test_as_costuras_consultam_o_fornecedor_com_so_um_pacote_ligado(mundo: Mundo) -> None:
    ext = Extensoes()
    ext.definir_licoes(_Fornecedor())  # type: ignore[arg-type]
    costuras = CosturasDoLivro(mundo.servico, mundo.db, ext)
    mundo.cfg.licoes.modo = "off"
    assert costuras.licoes_para(_pedido(IG)) == []                # nada ligado: nem consulta
    mundo.cfg.licoes.por_app = {IG: "on"}
    assert costuras.licoes_para(_pedido(IG)) == ["uma lição"]     # o fornecedor aplica o modo efetivo
    mundo.cfg.enabled = False
    assert costuras.licoes_para(_pedido(IG)) == []                # desligado vence tudo
