"""30.83: a referência pública do fluxo é aleatória e não sai do resumo literal.

Achado S1 da leitura do 30.80 B (herdado do 30.21): o id do fluxo era o slug do `plan.summary`, e um resumo como
"Enviar mensagem para @maria_souza" virava `enviar-mensagem-para-maria-souza…`, que saía em evento, `href` e log.
O desenho aprovado pela orquestradora pede `f-` mais 12 hex ALEATÓRIOS (nada derivável do nome), o fluxo novo com
`id = ref_publico`, e os que já existem preenchidos uma vez na subida (migração 116 só cria a coluna).
"""
from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

import httpx
import pytest

from app.db import Database
from app.main import create_app
from app.models import Plan, PlannerInfo, PlanStep, Postcondition
from app.modules.learning.application.espera import AvisadorDeEspera
from app.modules.learning.application.feedback import EfeitoAplicado
from app.modules.learning.domain.ciclo import ConflitoDeEstado, SkillState, TransicaoProibida
from app.modules.learning.domain.ensinado import AvisoDoEnsinado, DecisaoDoEnsinado, EsperaDoEnsinado
from app.modules.learning.domain.espera import AvisoDeEspera, Faixa
from app.modules.learning.domain.livro import EntradaDoLivro, quem_no_log, ref_no_log
from app.modules.learning.domain.vocabulario import LivroKind, Origem
from app.modules.learning.infrastructure.eventos import EventosNoBarramento
from app.modules.learning.domain.voto import AcaoDoEfeito, Efeito
from app.modules.learning.infrastructure.ligar_nativos import PoliticaD1DoFluxo
from app.modules.learning.presentation.feedback import _efeito
from app.taskqueue.flows import FlowStore, MudancaDoFluxo, id_do_fluxo, preencher_refs_publicas, ref_publica_do_fluxo
from app.util import now_iso

from .conftest import Harness
from .fake_skills import banco
from .test_conversao_de_fluxo import cliente
from .test_descompilador import abrir_conversa
from .test_learning_pedir_validacao import Mundo, mundo  # noqa: F401 - a fixture do pedido de validação (N1)

FORMA = re.compile(r"f-[0-9a-f]{12}")


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    d = banco(tmp_path, "ref-publico.sqlite3")
    yield d
    d.close()


def _plano(resumo: str) -> Plan:
    passo = PlanStep(key="enviar", title="Enviar", goal="enviar a mensagem", capability="SEND_DM",
                     bindings={"username": "{username}"},
                     postcondition=Postcondition(kind="text_visible", value="Enviada", description="enviada"))
    return Plan(summary=resumo, app_id="instagram", parameters={"username": "{username}"}, steps=[passo],
                planner=PlannerInfo(provider="anthropic", model="claude", simulated=False))


def _legado(db: Database, fid: str, chave: str) -> None:
    """Um fluxo de antes da migração 116: id slug, sem `ref_publico`."""
    db.execute("INSERT INTO flows(id, name, match_key, command_template, plan, created_at) VALUES (?,?,?,?,?,?)",
               (fid, "legado", chave, chave, _plano("legado").model_dump_json(), now_iso()))


def test_o_fluxo_novo_tem_referencia_aleatoria_sem_nada_do_resumo(db: Database) -> None:
    fid = FlowStore(db).learn_from_plan(_plano("Enviar mensagem para @maria_souza"), "mande uma mensagem a {username}",
                                        source="training:trn-1")
    assert FORMA.fullmatch(fid) and "maria" not in fid
    linha = db.one("SELECT name, ref_publico FROM flows WHERE id=?", (fid,))
    assert linha is not None and linha["ref_publico"] == fid
    assert "maria" in linha["name"]                     # o nome legível fica na coluna `name`, que o painel mostra


def test_o_mesmo_resumo_da_referencias_diferentes(db: Database) -> None:
    flows = FlowStore(db)
    a = flows.learn_from_plan(_plano("Mandar oi"), "mande oi a {username}", source="training:trn-1")
    b = flows.learn_from_plan(_plano("Mandar oi"), "diga oi a {username}", source="training:trn-2")
    assert a != b and FORMA.fullmatch(a) and FORMA.fullmatch(b)


def test_a_subida_preenche_os_fluxos_antigos_uma_vez_sem_mudar_o_id(db: Database) -> None:
    _legado(db, "enviar-mensagem-para-maria-souza", "mande uma mensagem a maria")
    _legado(db, "curtir-post", "curta o post")
    assert preencher_refs_publicas(db) == 2
    linhas = {r["id"]: r["ref_publico"] for r in db.query("SELECT id, ref_publico FROM flows")}
    assert set(linhas) == {"enviar-mensagem-para-maria-souza", "curtir-post"}   # o id antigo fica
    assert all(FORMA.fullmatch(r) for r in linhas.values()) and len(set(linhas.values())) == 2
    assert preencher_refs_publicas(db) == 0                                    # idempotente
    assert {r["id"]: r["ref_publico"] for r in db.query("SELECT id, ref_publico FROM flows")} == linhas


def test_a_referencia_e_unica_no_banco(db: Database) -> None:
    _legado(db, "a", "comando a")
    _legado(db, "b", "comando b")
    db.execute("UPDATE flows SET ref_publico='f-000000000001' WHERE id='a'")
    with pytest.raises(Exception):  # noqa: B017 - SQLite e PostgreSQL levantam classes diferentes
        db.execute("UPDATE flows SET ref_publico='f-000000000001' WHERE id='b'")


# ------------------------------------------------------------------ fatia 2: o que sai e o que entra
class _Bus:
    def __init__(self) -> None:
        self.eventos: list[tuple[str, str, dict[str, object]]] = []

    def emit(self, kind: str, message: str, *, level: str = "info", instance_id: str | None = None,
             data: dict[str, object] | None = None) -> object:
        self.eventos.append((kind, message, dict(data or {})))
        return None


def test_as_duas_traducoes_aceitam_o_id_e_a_referencia(db: Database) -> None:
    _legado(db, "enviar-mensagem-para-maria-souza", "mande uma mensagem a maria")
    preencher_refs_publicas(db)
    ref = ref_publica_do_fluxo(db, "enviar-mensagem-para-maria-souza")
    assert FORMA.fullmatch(ref)
    antigo = "enviar-mensagem-para-maria-souza"
    assert id_do_fluxo(db, ref) == id_do_fluxo(db, antigo) == antigo
    assert id_do_fluxo(db, "f-ffffffffffff") == "f-ffffffffffff"                 # desconhecida: o 404 de sempre
    assert FORMA.fullmatch(ref_publica_do_fluxo(db, "sem-linha"))               # N2: nunca cai no id


def test_o_fluxo_sem_referencia_ganha_na_hora_e_nunca_sai_o_id(db: Database) -> None:
    _legado(db, "enviar-mensagem-para-maria-souza", "mande uma mensagem a maria")   # réplica no código antigo
    ref = ref_publica_do_fluxo(db, "enviar-mensagem-para-maria-souza")
    assert FORMA.fullmatch(ref) and "maria" not in ref
    assert db.scalar("SELECT ref_publico FROM flows WHERE id=?", ("enviar-mensagem-para-maria-souza",)) == ref
    assert ref_publica_do_fluxo(db, "enviar-mensagem-para-maria-souza") == ref      # a mesma nas próximas


def test_o_aviso_do_fluxo_sai_com_a_referencia_publica_e_sem_o_id_na_mensagem(db: Database) -> None:
    _legado(db, "enviar-mensagem-para-maria-souza", "mande uma mensagem a maria")
    preencher_refs_publicas(db)
    bus = _Bus()
    porta = EventosNoBarramento(bus, partial(ref_publica_do_fluxo, db))
    porta.esperando_a_pessoa(AvisoDeEspera(kind="fluxo", ref="enviar-mensagem-para-maria-souza", app="instagram",
                                           faixa=Faixa.C, aguardando=True, motivo="classe_c", desde=now_iso()))
    porta.esperando_a_pessoa(AvisoDeEspera(kind="receita", ref="42", app="instagram", faixa=Faixa.B,
                                           aguardando=True, motivo="classe_b", desde=now_iso()))
    (_, msg_fluxo, dados_fluxo), (_, msg_receita, dados_receita) = bus.eventos
    publica = ref_publica_do_fluxo(db, "enviar-mensagem-para-maria-souza")
    assert dados_fluxo["ref"] == publica and str(dados_fluxo["href"]).endswith(f"item=fluxo:{publica}")
    assert "maria" not in msg_fluxo and "maria" not in str(dados_fluxo)        # nem na mensagem, que vai ao log
    assert dados_receita["ref"] == "42" and "receita 42" in msg_receita       # a receita não muda (id só dígitos)


async def test_a_rota_do_livro_abre_o_fluxo_pela_referencia_publica(harness: Harness) -> None:
    db = harness.state.db
    _legado(db, "enviar-mensagem-para-maria-souza", "mande uma mensagem a maria")
    preencher_refs_publicas(db)
    publica = ref_publica_do_fluxo(db, "enviar-mensagem-para-maria-souza")
    app = create_app(harness.cfg, state=harness.state)
    app.state.poc = harness.state
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        pelo_id = await c.get("/api/aprendizado/fluxo/enviar-mensagem-para-maria-souza")
        pela_ref = await c.get(f"/api/aprendizado/fluxo/{publica}")
        sem = await c.get("/api/aprendizado/fluxo/f-ffffffffffff")
    assert pelo_id.status_code == pela_ref.status_code == 200, (pelo_id.text, pela_ref.text)
    assert pela_ref.json()["item"]["ref"] == pelo_id.json()["item"]["ref"]
    assert sem.status_code == 404


# ------------------------------------------------------------------ N1: o slug do fluxo legado não vai ao log
SLUG = "enviar-mensagem-para-maria-souza"


def test_o_log_diz_o_fluxo_so_pelo_tipo_e_o_resto_como_veio() -> None:
    assert ref_no_log(f"fluxo:{SLUG}") == "fluxo" and ref_no_log("receita:42") == "receita:42"
    assert ref_no_log("li-abc") == "li-abc" and ref_no_log("pedido:painel") == "pedido:painel"
    assert quem_no_log(LivroKind.FLUXO, SLUG) == "fluxo" and quem_no_log(LivroKind.RECEITA, "42") == "receita 42"


def test_o_slug_do_fluxo_legado_nao_vai_ao_log_da_validacao(mundo: Mundo, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    mundo.servico.pedir_pela_pessoa(mundo.candidato(SLUG), by="painel")
    assert mundo.servico.uma_volta(lambda: 1) is not None                  # o pedido e o despacho logam
    assert "pediu a validação de fluxo" in caplog.text and "de fluxo em android-10" in caplog.text
    assert "maria" not in caplog.text


def test_o_slug_do_fluxo_legado_nao_vai_ao_log_quando_o_aviso_ou_a_trilha_falham(
        db: Database, caplog: pytest.LogCaptureFixture) -> None:
    class Quebrada:
        def esperando_a_pessoa(self, aviso: AvisoDeEspera) -> None:
            raise RuntimeError("barramento fora")

        def registrar(self, *args: object, **kwargs: object) -> None:
            raise RuntimeError("trilha fora")

    caplog.set_level(logging.INFO)
    e = EntradaDoLivro(kind=LivroKind.FLUXO, ref=SLUG, state=SkillState.VALIDATED, native_status="candidate",
                       title="t", app="com.instagram.android", origin=Origem.EXECUCAO, side_effect=True)
    avisador = AvisadorDeEspera(Quebrada(), None, lambda: datetime.now(UTC))
    avisador.mudou_sem_falhar(None, e, por_sistema=True)                   # o aviso de espera falha
    assert avisador.parecer_disponivel(e, Faixa.C) is False                 # o aviso de parecer falha
    PoliticaD1DoFluxo(None, Quebrada(), db).mudou(  # type: ignore[arg-type]
        MudancaDoFluxo(SLUG, None, "candidate", motivo="aprendido", por="sistema"))       # a trilha falha
    assert len([r for r in caplog.records if r.levelno == logging.ERROR]) == 3, caplog.text
    assert "maria" not in caplog.text


# ------------------------------------------------------------------ fatia 3: rotas antigas, ensinado e `href`
async def test_as_rotas_antigas_do_fluxo_aceitam_a_referencia_publica(tmp_path: Path) -> None:
    h = Harness(tmp_path, 1)
    await h.boot()
    try:
        s = h.state
        assert s is not None
        flow_id, _ = abrir_conversa(s.db)
        publica = "f-0123456789ab"                       # diferente do id, para a rota ter o que traduzir
        s.db.execute("UPDATE flows SET ref_publico=? WHERE id=?", (publica, flow_id))
        h.cfg.file.skills.enabled = True
        async with cliente(h) as c:
            r = await c.put(f"/api/flows/{publica}", json={"status": "disabled"})
            assert r.status_code == 200 and r.json()["id"] == flow_id, r.text
            assert s.db.scalar("SELECT status FROM flows WHERE id=?", (flow_id,)) == "disabled"
            assert (await c.put(f"/api/flows/{publica}", json={"status": "active"})).status_code == 200
            r = await c.post(f"/api/flows/{publica}/adopt", json={"reason": "converter"})
            assert r.status_code == 201 and r.json()["flow_id"] == flow_id, r.text
            r = await c.post(f"/api/flows/{publica}/release", json={"reason": "voltar"})
            assert r.status_code == 200 and r.json()["flow_id"] == flow_id, r.text
            r = await c.delete(f"/api/flows/{publica}")    # a guarda da adoção leu pelo id interno (sem a tradução,
            assert r.status_code == 409 and r.json()["detail"]["code"] == "flow_adopted"   # o DELETE seria um 204 vazio)
            _legado(s.db, SLUG, "mande uma mensagem a maria")
            assert (await c.delete(f"/api/flows/{ref_publica_do_fluxo(s.db, SLUG)}")).status_code == 204
            assert s.db.one("SELECT 1 FROM flows WHERE id=?", (SLUG,)) is None
            assert (await c.put("/api/flows/f-ffffffffffff", json={"status": "active"})).status_code == 404
    finally:
        await h.state.stop()                                                    # type: ignore[union-attr]


def test_os_avisos_do_ensinado_saem_com_a_referencia_publica(db: Database) -> None:
    _legado(db, SLUG, "mande uma mensagem a maria")
    preencher_refs_publicas(db)
    publica = ref_publica_do_fluxo(db, SLUG)
    bus = _Bus()
    porta = EventosNoBarramento(bus, partial(ref_publica_do_fluxo, db))
    porta.ensinado_rebaixado(AvisoDoEnsinado(kind="fluxo", ref=SLUG, app="instagram", treino="trn-1",
                                             sem_receita_ativa=True, para="disabled", desde=now_iso()))
    porta.ensinado_espera_decisao(EsperaDoEnsinado(kind="fluxo", ref=SLUG, app="instagram", treino="trn-1",
                                                   persona=None, desde=now_iso()))
    porta.ensinado_decidido(DecisaoDoEnsinado(kind="fluxo", ref=SLUG, desde=now_iso(), decisao="liberado",
                                              decidido_em=now_iso()))
    porta.ensinado_rebaixado(AvisoDoEnsinado(kind="receita", ref="42", app="instagram", treino="trn-1",
                                             sem_receita_ativa=False, para="superseded", desde=now_iso()))
    *fluxos, (_, _, receita) = bus.eventos
    assert len(fluxos) == 3 and all(dados["ref"] == publica for _, _, dados in fluxos)
    assert all("maria" not in mensagem + str(dados) for _, mensagem, dados in fluxos)
    assert receita["ref"] == "42"


def test_o_href_de_desfazer_do_voto_leva_a_referencia_publica() -> None:
    def traduz(kind: str, valor: str) -> str:
        return "f-0123456789ab" if kind == "fluxo" else valor

    fluxo = _efeito(EfeitoAplicado(Efeito(AcaoDoEfeito.DESLIGAR, "fluxo", SLUG), True, reativavel=True), traduz)
    receita = _efeito(EfeitoAplicado(Efeito(AcaoDoEfeito.DESLIGAR, "receita", "42"), True, reativavel=True), traduz)
    assert fluxo["desfazer"] == {"method": "POST", "href": "/api/aprendizado/fluxo/f-0123456789ab/status",
                                 "body": {"to": "published", "reason": "reativado depois do voto"}}
    assert fluxo["ref"] == SLUG                         # o `ref` segue o id interno, que o painel casa com a lista
    assert isinstance(receita["desfazer"], dict) and receita["desfazer"]["href"] == "/api/aprendizado/receita/42/status"


def test_as_excecoes_do_pedido_de_validacao_nao_levam_o_slug(mundo: Mundo) -> None:
    """S1 da leitura da fatia 3: o texto da exceção vai ao `detail` da resposta e ao log."""
    e = mundo.candidato(SLUG)
    mundo.servico.pedir_pela_pessoa(e, by="painel")
    with pytest.raises(ConflitoDeEstado) as vivo:
        mundo.servico.pedir_pela_pessoa(e, by="painel")
    with pytest.raises(TransicaoProibida) as publicado:
        mundo.servico.pedir_pela_pessoa(replace(e, state=SkillState.PUBLISHED), by="painel")
    assert "maria" not in str(vivo.value) and "maria" not in str(publicado.value)
    assert "pedido de validação vivo" in str(vivo.value) and "fluxo" in str(publicado.value)
