"""30.47: a pessoa pede a validação de um fluxo candidato (`POST /api/aprendizado/fluxo/{ref}/validacao`, adendo v1.14).

O que se prova:
- o pedido é o mesmo que um "pedir evidência" do curador geraria: o comando de origem, o aparelho de origem excluído, o
  grupo `qa`, o teto de sempre, e quem pediu no `review_id` (`pedido:<quem>`); o despachante P4 o leva como prova do
  fluxo (`runs.prova_fluxo_id`);
- as recusas não gravam nada: a classe C (e o item sem dossiê de agora), o efeito fora do app de QA, o que não é
  fluxo candidato, o modo `off`, o sem origem; o pedido vivo é conflito;
- a rota: 404 para o fluxo que não existe, 422 com o código para a recusa, e o serviço composto com a classe de risco.

Armações: o `mundo` do 30.37 (banco migrado, parque falso que grava a prova) e o Harness (porta 5640) para a rota.
Nível de prova: `simulated`.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.db import Database
from app.modules.learning.application.validacao import (PREFIXO_DO_PEDIDO_DA_PESSOA, AjustesDaValidacao,
                                                        PedidoRecusado, ServicoDeValidacao)
from app.modules.learning.domain.ciclo import ConflitoDeEstado, ExigeODono, SkillState, TransicaoProibida
from app.modules.learning.domain.livro import EntradaDoLivro
from app.modules.learning.domain.politica_de_risco import ClasseDeRisco, Classificacao, MotivoDeEntrada, Razao
from app.modules.learning.domain.validacao import Motivo
from app.modules.learning.domain.vocabulario import Modo
from app.modules.learning.infrastructure.validacoes_sql import FontesDaValidacaoSql, RegistroDeValidacoesSql
from app.util import to_iso

from .conftest import Harness
from .fake_skills import banco as banco_migrado
from .test_learning_prova_validacao import ORIGEM, ParqueDeProva, _fluxo
from .test_learning_validacao_sql import COMANDO, QA, B, Relogio, _ap, _linha
from .test_validacoes_listagem import _cliente

C = Classificacao(ClasseDeRisco.C, (Razao.TEXTO_PARA_OUTRA_PESSOA,), MotivoDeEntrada.TEXTO_DE_PESSOA)
IG = "com.instagram.android"


class Mundo:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.ajustes: dict[str, object] = {"modo": Modo.ON}
        self.risco: Classificacao | None = B
        self.parque = ParqueDeProva(db)
        self.parque.lista = [_ap("android-10")]
        fontes = FontesDaValidacaoSql(db, precos=lambda: {"m": [3.0, 0.3, 3.75, 15.0]},
                                      fluxo_ativo_para=lambda c: c == COMANDO, vetado=lambda e: False)
        self.servico = ServicoDeValidacao(RegistroDeValidacoesSql(db), fontes, self.parque,
                                          triagem=lambda t: "senha" in t,
                                          ajustes=lambda: AjustesDaValidacao(**self.ajustes),  # type: ignore[arg-type]
                                          relogio=Relogio(), risco_do_item=lambda e: self.risco)

    def candidato(self, fid: str = "f-qa", **kw: object) -> EntradaDoLivro:
        return replace(_fluxo(self.db, fid), state=SkillState.CANDIDATE, **kw)  # type: ignore[arg-type]

    def pedidos(self) -> int:
        r = self.db.one("SELECT COUNT(*) AS n FROM learning_validations")
        return int(r["n"]) if r is not None else 0


@pytest.fixture
def mundo(tmp_path: Path) -> Iterator[Mundo]:
    db = banco_migrado(tmp_path, "pedir.sqlite3")
    db.execute("INSERT INTO apps(id, name, package, builtin, category) VALUES ('qa-messenger','QA Messenger',?,1,'qa')",
               (QA,))
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at)"
               " VALUES (?,'k-origem',?,'execute','completed',0,'[\"android-01\"]',?)",
               (ORIGEM, COMANDO, to_iso(datetime.now(UTC))))
    yield Mundo(db)
    db.close()


def test_o_pedido_da_pessoa_vira_a_prova_do_fluxo_pelo_p4(mundo: Mundo) -> None:
    pid = mundo.servico.pedir_pela_pessoa(mundo.candidato(), by="orquestradora")
    linha = _linha(mundo.db, pid)
    assert (linha["estado"], linha["grupo"], linha["item_ref"], linha["comando"]) == ("pendente", "qa", "fluxo:f-qa",
                                                                                      COMANDO)
    assert linha["review_id"] == f"{PREFIXO_DO_PEDIDO_DA_PESSOA}orquestradora" and linha["motivo"] is None
    assert linha["aparelho_excluido"] == "android-01" and float(str(linha["teto_usd"])) == pytest.approx(0.10)
    run_id = mundo.servico.uma_volta(lambda: 1)                       # o despachante leva como a do curador
    assert run_id is not None
    run = mundo.db.one("SELECT prova_fluxo_id, idempotency_key FROM runs WHERE id=?", (run_id,))
    assert run is not None and run["prova_fluxo_id"] == "f-qa" and str(run["idempotency_key"]) == f"validacao:{pid}"
    assert _linha(mundo.db, pid)["estado"] == "rodando"


def test_o_pedido_vivo_e_conflito_e_outro_fluxo_passa(mundo: Mundo) -> None:
    e = mundo.candidato()
    mundo.servico.pedir_pela_pessoa(e, by="painel")
    with pytest.raises(ConflitoDeEstado):                             # o mesmo fluxo, com o pedido vivo
        mundo.servico.pedir_pela_pessoa(e, by="painel")
    assert mundo.servico.pedir_pela_pessoa(mundo.candidato("f-qa-2"), by="painel")
    assert mundo.pedidos() == 2


def test_as_recusas_nao_gravam_nada(mundo: Mundo) -> None:
    e = mundo.candidato()
    mundo.risco = C
    with pytest.raises(ExigeODono):                                   # C segue com o dono
        mundo.servico.pedir_pela_pessoa(e, by="painel")
    mundo.risco = None
    with pytest.raises(ExigeODono):                                   # sem dossiê de agora conta como C
        mundo.servico.pedir_pela_pessoa(e, by="painel")
    mundo.risco = B
    with pytest.raises(PedidoRecusado) as fora_do_qa:                 # efeito em app real: segue com o dono
        mundo.servico.pedir_pela_pessoa(replace(e, app=IG, apps=(IG,)), by="painel")
    assert fora_do_qa.value.motivo is Motivo.EFEITO_REAL
    with pytest.raises(PedidoRecusado) as sem_origem:
        mundo.servico.pedir_pela_pessoa(replace(e, nasceu_de=None), by="painel")
    assert sem_origem.value.motivo is Motivo.SEM_ORIGEM
    for estado in (SkillState.VALIDATED, SkillState.PUBLISHED, SkillState.DISABLED):
        with pytest.raises(TransicaoProibida):
            mundo.servico.pedir_pela_pessoa(replace(e, state=estado), by="painel")
    mundo.ajustes["modo"] = Modo.OFF
    with pytest.raises(TransicaoProibida):
        mundo.servico.pedir_pela_pessoa(e, by="painel")
    assert mundo.pedidos() == 0


# ------------------------------------------------------------------ a rota
async def test_a_rota_recusa_o_que_nao_existe_e_o_que_a_regra_nao_deixa(harness: Harness) -> None:
    assert harness.state is not None
    async with _cliente(harness) as c:
        r = await c.post("/api/aprendizado/fluxo/nao-existe/validacao")
        assert r.status_code == 404
        harness.state.db.execute(
            "INSERT INTO flows(id, name, match_key, command_template, plan, app_id, status, created_at)"
            " VALUES ('f-publicado','f','cmd f','cmd f','{}','qa-messenger','active','2026-10-04T00:00:00Z')")
        r = await c.post("/api/aprendizado/fluxo/f-publicado/validacao")
        assert r.status_code == 422 and r.json()["detail"]["code"] == "recusado"
    servico = harness.state.learning.extensao(ServicoDeValidacao)
    assert servico is not None and servico._risco_do_item is not None  # noqa: SLF001 - a composição liga o dossiê
