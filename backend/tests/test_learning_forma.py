"""30.36: a divergência de FORMA não é evidência contra o fluxo, e a receita sem caminho não gasta execução.

O caso real (P4 de 03/10, r-20261003160725-213aae, android-09): a validação do fluxo "abrir o QA Messenger e confirmar
a conta" comprovou 2/2 etapas, e a sombra gravou `against` "etapa 1: outra ação" só porque o planejador reescreveu as
pós-condições (`app_foreground` × `element_present` da lista de conversas; "{account_label}" × "Conta:
{account_label}") e não repetiu um parâmetro que nenhuma etapa usava (`expected_account`). E a receita 54
(`send_message`, 55f3aca9…) nunca roda: o fluxo ativo do comando chega à 78 (2ad58b2c…) pela mesma etapa.

Aqui: o comparador (forma × caminho), a regra do contra efetivo nos leitores, a sombra que não desliga por forma, a
reclassificação idempotente do `against` antigo, o pedido que fecha pela evidência e segue a que chega depois, a
receita `sem_caminho` ao nascer e ao despachar, as notas do dossiê e a ordem dos mineradores no central. Os planos são
os dois reais, reduzidos aos campos que a sombra lê (sem texto de tela). Nível de prova: `simulated`.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest

from app.db import Database
from app.models import Plan, PlannerInfo, PlanStep, Postcondition
from app.modules.learning.application.nativos import comparar, marca_do_conteudo
from app.modules.learning.application.ports import NovaEvidencia
from app.modules.learning.application.validacao import AjustesDaValidacao, ServicoDeValidacao
from app.modules.learning.domain.ciclo import SkillState
from app.modules.learning.domain.curador import (FORMA_DA_EVIDENCIA, SEM_CAMINHO_DA_RECEITA, Evidencia,
                                                 IdentidadeDoItem, montar_dossie)
from app.modules.learning.domain.livro import EntradaDoLivro
from app.modules.learning.domain.politica_de_risco import FatosDeRisco
from app.modules.learning.domain.promocao import Decisao, Limiares, contrarias, efetivas, veredito_de_repeticao
from app.modules.learning.domain.promocao import Evidencia as EvidenciaDoLivro
from app.modules.learning.domain.vocabulario import LivroKind, Modo, Origem, Posicao
from app.modules.learning.infrastructure.aprendido_sql import montar_aprendizado
from app.modules.learning.infrastructure.ligar_nativos import LeituraSql, assinatura
from app.modules.learning.infrastructure.validacoes_sql import FontesDaValidacaoSql, RegistroDeValidacoesSql
from app.taskqueue.recipes import para_hash, step_template_hash
from app.util import to_iso

from .conftest import Harness
from .fake_skills import banco as banco_migrado
from .test_d1_fluxos import Mundo
from .test_d1_fluxos import _plano as _plano_do_instagram
from .test_learning_validacao_sql import B, PEDE, QA, Parque, Relogio, _ap, _linha

COMANDO_QA = "Abra o QA Messenger e confirme qual conta está conectada, sem enviar nada."
LISTA = "id=com.pocqa.messenger:id/conversation_list"


def _qa(*, abrir: tuple[str, str] = ("element_present", LISTA),
        conta: str = "id=com.pocqa.messenger:id/account_label|text=Conta: {account_label}",
        parametros: dict[str, str] | None = None, efeito: bool = False, chave: str = "open_app",
        objetivo_da_conta: str = "Confirmar a conta conectada no topo da tela inicial.",
        app_da_etapa: str | None = None) -> Plan:
    """Os dois planos reais do P4, reduzidos ao que a sombra lê. O padrão é o do FLUXO ensinado no android-05."""
    return Plan(summary="Abrir o QA Messenger e confirmar a conta conectada", app_id="qa-messenger",
                app_package="com.pocqa.messenger", required_apps=["qa-messenger"], parameters=parametros or {},
                steps=[PlanStep(key=chave, title="Abrir o QA Messenger", goal="Abrir o app QA Messenger.",
                                side_effect=efeito, app_id=app_da_etapa,
                                postcondition=Postcondition(kind=abrir[0], value=abrir[1], description="aberto")),
                       PlanStep(key="confirm_account", title="Confirmar a conta", goal=objetivo_da_conta,
                                depends_on=[chave],
                                postcondition=Postcondition(kind="element_present", value=conta,
                                                            description="a conta no topo"))],
                planner=PlannerInfo(provider="anthropic", model="claude", simulated=False))


FLUXO = _qa(parametros={"expected_account": "{account_label}"})
#: A execução de validação de 03/10: outras pós-condições e nenhum parâmetro.
EXECUCAO = _qa(abrir=("app_foreground", "com.pocqa.messenger"),
               conta="id=com.pocqa.messenger:id/account_label|text={account_label}")


# ------------------------------------------------------------------ o comparador
def test_a_execucao_do_p4_e_so_forma_sem_valor_nem_texto_de_tela() -> None:
    d = comparar(assinatura(FLUXO), assinatura(EXECUCAO, "r-20261003160725-213aae"))
    assert d is not None and d.forma
    # as duas etapas e, contado no "(+1)", o parâmetro que nenhuma etapa usa (`expected_account`, o caso real)
    assert str(d) == ("etapa 1: pós-condição reescrita (app_foreground × element_present); "
                      "etapa 2: pós-condição reescrita (element_present × element_present) (+1)")
    assert "Conta:" not in str(d) and "conversation_list" not in str(d)          # nada da tela vai ao detalhe
    assert assinatura(FLUXO).na_acao == frozenset()


def test_o_parametro_que_so_falta_e_nao_e_usado_e_forma_mas_o_usado_na_acao_e_caminho() -> None:
    so_parametro = comparar(assinatura(FLUXO), assinatura(_qa()))
    assert so_parametro is not None and so_parametro.forma and str(so_parametro) == "parâmetro fora da ação: expected_account"
    usado = _qa(parametros={"expected_account": "{account_label}"},
                objetivo_da_conta="Confirmar que a conta é {expected_account}.")
    assert assinatura(usado).na_acao == frozenset({"expected_account"})
    d = comparar(assinatura(usado), assinatura(_qa()))
    assert d is not None and not d.forma and str(d) == "outros parâmetros"


def test_na_etapa_de_efeito_a_pos_condicao_e_a_prova_de_entrega() -> None:
    d = comparar(assinatura(_qa(efeito=True)), assinatura(_qa(efeito=True, abrir=("app_foreground", "x"))))
    assert d is not None and not d.forma and str(d) == "etapa 1: outra pós-condição (app_foreground × element_present)"


def test_outra_chave_outro_app_ou_outro_efeito_seguem_contra() -> None:
    base = assinatura(_qa())
    for outro, motivo in ((_qa(chave="launch_app"), "etapa 1: outra ação"),
                          (_qa(app_da_etapa="instagram"), "etapa 1: outra ação"),
                          (_qa(efeito=True), "etapa 1: outra ação")):       # na etapa livre, o efeito é da ação
        d = comparar(base, assinatura(outro))
        assert d is not None and not d.forma and str(d) == motivo
    # a forma nunca esconde o caminho: com a pós-condição reescrita E outra chave, é contra
    d = comparar(base, assinatura(_qa(chave="launch_app", abrir=("app_foreground", "x"))))
    assert d is not None and not d.forma


def test_o_que_era_o_mesmo_plano_segue_o_mesmo() -> None:
    assert comparar(assinatura(FLUXO), assinatura(FLUXO)) is None
    # a capability compara o TIPO da pós-condição, como antes: o plano-modelo de "{username}" casa outro perfil
    modelo = assinatura(_plano_do_instagram("{username}"))
    assert comparar(modelo, assinatura(_plano_do_instagram("@spacex"))) is None
    d = comparar(modelo, assinatura(_plano_do_instagram("@spacex", efeito=True)))
    assert d is not None and not d.forma and str(d) == "2 etapa(s) no plano, 1 no candidato"


# ------------------------------------------------------------------ o contra efetivo
def _ev(stance: Posicao, run: str, *, item: str = "fluxo:f") -> EvidenciaDoLivro:
    return EvidenciaDoLivro(item_ref=item, stance=stance, origin_ref=f"run:{run}", run_id=run, instance_id="android-09",
                            app_version=None, simulated=False, detail=None, observed_at=f"2026-10-03T16:0{run[-1]}:00Z")


def test_a_forma_tira_o_contra_da_mesma_origem_e_nao_conta_nem_a_favor() -> None:
    linhas = [_ev(Posicao.FOR, "r1"), _ev(Posicao.AGAINST, "r2"), _ev(Posicao.FORMA, "r2"), _ev(Posicao.AGAINST, "r3")]
    assert [(e.stance, e.run_id) for e in efetivas(linhas)] == [(Posicao.FOR, "r1"), (Posicao.FORMA, "r2"),
                                                               (Posicao.AGAINST, "r3")]
    assert [e.run_id for e in contrarias(linhas)] == ["r3"]
    v = veredito_de_repeticao(linhas, Limiares(n_min=2, execucoes_min=2, aparelhos_min=1, contra_max=1))
    assert (v.decisao, v.a_favor, v.contra) == (Decisao.ESPERA, 1, 1)
    # duas formas: nada contra (a regra "duas discordâncias desligam" não as vê)
    so_forma = [_ev(Posicao.FOR, "r1"), _ev(Posicao.FORMA, "r2"), _ev(Posicao.FORMA, "r3")]
    assert veredito_de_repeticao(so_forma, Limiares(contra_max=1)).decisao is not Decisao.CONTRADITA
    # a forma de OUTRO item não tira nada
    assert contrarias([_ev(Posicao.AGAINST, "r2"), _ev(Posicao.FORMA, "r2", item="fluxo:g")])


# ------------------------------------------------------------------ a sombra e a reclassificação
@pytest.fixture
def mundo(tmp_path: Path) -> Iterator[Mundo]:
    db = banco_migrado(tmp_path, "forma.sqlite3")
    db.execute("INSERT INTO apps(id, name, package, builtin, category) VALUES ('qa-messenger','QA Messenger',?,1,'qa')",
               (QA,))
    yield Mundo(db)
    db.close()


def _aprende(mundo: Mundo) -> str:
    """O fluxo candidato que a execução do android-05 ensinou."""
    run = mundo.execucao("r-origem", FLUXO, COMANDO_QA, aparelho="android-05")
    flow_id = mundo.flows.learn_from_run(run)
    assert flow_id is not None and not mundo.servico.digerir_execucao("r-origem").falhas
    return flow_id


def _posicoes(mundo: Mundo, flow_id: str) -> list[tuple[str, str]]:
    return [(e.stance.value, e.origin_ref) for e in sorted(mundo.repo.evidencias(f"fluxo:{flow_id}"),
                                                           key=lambda e: e.origin_ref)]


def test_duas_divergencias_de_forma_nao_desligam_o_fluxo(mundo: Mundo) -> None:
    flow_id = _aprende(mundo)
    for run, aparelho in (("r-p4-1", "android-09"), ("r-p4-2", "android-10")):
        mundo.execucao(run, EXECUCAO, COMANDO_QA, aparelho=aparelho)
        assert not mundo.servico.digerir_execucao(run).falhas
    assert mundo.status(flow_id) == "candidate"                      # antes do 30.36, a segunda o desligava
    assert _posicoes(mundo, flow_id) == [("for", "run:r-origem"), ("forma", "run:r-p4-1"), ("forma", "run:r-p4-2")]
    assert not any("desligado" in t for _, t in mundo.decisoes)
    # a contestação do livro também não as conta
    assert mundo.servico.entrada(LivroKind.FLUXO, flow_id) is not None


def test_o_against_antigo_de_forma_e_reclassificado_uma_vez_e_o_de_caminho_fica(mundo: Mundo) -> None:
    flow_id = _aprende(mundo)
    ref = f"fluxo:{flow_id}"
    [fluxo] = LeituraSql(mundo.db).fluxos_em_prova()
    marca = marca_do_conteudo(fluxo.content_hash)
    # o que a sombra de ANTES gravou: a execução do P4 (forma) e uma de outro caminho (uma etapa a mais)
    outro = _qa(chave="launch_app")
    for run, plano in (("r-p4", EXECUCAO), ("r-outro", outro)):
        mundo.execucao(run, plano, COMANDO_QA, aparelho="android-09")
        assert mundo.repo.registrar_evidencia(NovaEvidencia(item_ref=ref, stance=Posicao.AGAINST,
                                                            origin_ref=f"run:{run}", simulated=False, run_id=run,
                                                            instance_id="android-09", detail=f"{marca} etapa 1: outra ação"))
    # e um against de OUTRA encarnação (outra marca), que não se recompara
    mundo.execucao("r-velho", EXECUCAO, COMANDO_QA)
    mundo.repo.registrar_evidencia(NovaEvidencia(item_ref=ref, stance=Posicao.AGAINST, origin_ref="run:r-velho",
                                                 simulated=False, run_id="r-velho", detail="[000000000000] outra ação"))
    assert len(contrarias(mundo.repo.evidencias(ref))) == 3
    assert not mundo.servico.curar().falhas
    [forma] = [e for e in mundo.repo.evidencias(ref) if e.stance is Posicao.FORMA]
    assert forma.origin_ref == "run:r-p4" and (forma.detail or "").startswith(f"{marca} reclassificada: etapa 1:")
    assert sorted(e.origin_ref for e in contrarias(mundo.repo.evidencias(ref))) == ["run:r-outro", "run:r-velho"]
    assert [r for r, t in mundo.decisoes if "deixou de contar contra" in t] == ["r-p4"]
    # a página da execução diz "de forma" e não diz mais "contra"
    bloco = montar_aprendizado(mundo.db, mundo.servico)
    assert bloco is not None
    [papel] = [i.papel for i in bloco.da_execucao("r-p4") if i.ref == flow_id]
    assert "evidência de forma (não conta)" in papel and "contra" not in papel
    # idempotente: a segunda volta não grava nem anuncia nada
    antes = len(mundo.repo.evidencias(ref))
    assert not mundo.servico.curar().falhas
    assert len(mundo.repo.evidencias(ref)) == antes
    assert [r for r, t in mundo.decisoes if "deixou de contar contra" in t] == ["r-p4"]
    assert mundo.status(flow_id) == "candidate"


# ------------------------------------------------------------------ o pedido da validação (30.31)
class Planos:
    """O `FlowStore.match` do scheduler: o plano do fluxo ativo para o comando de origem."""

    def __init__(self) -> None:
        self.plano: Plan | None = None

    def __call__(self, comando: str) -> Plan | None:
        return self.plano if comando == COMANDO_QA else None


@pytest.fixture
def validacao(tmp_path: Path) -> Iterator[tuple[Database, ServicoDeValidacao, Parque, Relogio, Planos]]:
    db = banco_migrado(tmp_path, "forma-validacao.sqlite3")
    db.execute("INSERT INTO apps(id, name, package, builtin, category) VALUES ('qa-messenger','QA Messenger',?,1,'qa')",
               (QA,))
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at)"
               " VALUES ('r-origem','k-origem',?,'execute','completed',0,'[\"android-05\"]',?)",
               (COMANDO_QA, to_iso(Relogio().agora)))
    # 30.37: o pedido do fluxo só nasce vivo se o comando de origem cabe no molde do fluxo (a prova roda o plano dele).
    db.execute("INSERT INTO flows(id, name, match_key, command_template, plan, app_id, status, created_at)"
               " VALUES ('abrir-o-qa','abrir-o-qa','k-abrir-o-qa',?,?,'qa-messenger','candidate',?)",
               (COMANDO_QA, json.dumps({"summary": "Abrir o QA", "app_id": "qa-messenger",
                                        "planner": {"provider": "fluxo", "model": "m", "simulated": True}}),
                to_iso(Relogio().agora)))
    parque, relogio, planos = Parque(db), Relogio(), Planos()
    parque.lista = [_ap("android-09")]
    fontes = FontesDaValidacaoSql(db, precos=lambda: {"m": [3.0, 0.3, 3.75, 15.0]},
                                  fluxo_ativo_para=lambda c: planos(c) is not None, vetado=lambda e: False,
                                  plano_ativo_para=planos)
    servico = ServicoDeValidacao(RegistroDeValidacoesSql(db), fontes, parque, triagem=lambda t: False,
                                 ajustes=lambda: AjustesDaValidacao(modo=Modo.ON), relogio=relogio)
    yield db, servico, parque, relogio, planos
    db.close()


def _fluxo() -> EntradaDoLivro:
    return EntradaDoLivro(kind=LivroKind.FLUXO, ref="abrir-o-qa", state=SkillState.CANDIDATE, native_status="candidate",
                          title="Abrir o QA Messenger", app=QA, origin=Origem.EXECUCAO, side_effect=False,
                          nasceu_de="r-origem")


def _receita(ref: int) -> EntradaDoLivro:
    return EntradaDoLivro(kind=LivroKind.RECEITA, ref=str(ref), state=SkillState.PUBLISHED, native_status="active",
                          title="send_message", app=QA, origin=Origem.EXECUCAO, side_effect=True, nasceu_de="r-origem")


def _evidencia(db: Database, item_ref: str, run_id: str, stance: str) -> None:
    db.execute("INSERT INTO learning_evidence(item_ref, stance, origin_ref, run_id, instance_id, simulated, detail,"
               " observed_at) VALUES (?,?,?,?,?,0,?,?)", (item_ref, stance, f"run:{run_id}", run_id, "android-09",
                                                         "[abc] etapa 1", "2026-10-03T16:07:35.820Z"))


def _roda(db: Database, servico: ServicoDeValidacao) -> tuple[str, str]:
    """Nasce o pedido do fluxo, o despachante o põe no android-09 e a execução assenta `completed`."""
    pid = servico.ao_parecer(_fluxo(), "lr-1", PEDE, B)
    assert pid is not None
    run_id = servico.uma_volta(lambda: 1)
    assert run_id is not None
    db.execute("UPDATE runs SET status='completed' WHERE id=?", (run_id,))
    return pid, run_id


@pytest.mark.parametrize(("stance", "estado", "motivo", "chega"), [
    ("for", "feita", None, True),
    ("against", "recusada", "evidencia_contra", True),
    ("forma", "recusada", "divergencia_de_forma", False),
    (None, "recusada", "sem_evidencia", False),
])
def test_o_pedido_fecha_pela_evidencia_que_a_execucao_deixou(
        validacao: tuple[Database, ServicoDeValidacao, Parque, Relogio, Planos], stance: str | None, estado: str,
        motivo: str | None, chega: bool) -> None:
    db, servico, _parque, _relogio, _planos = validacao
    pid, run_id = _roda(db, servico)
    if stance is not None:
        _evidencia(db, "fluxo:abrir-o-qa", run_id, stance)
    assert servico.minerar(run_id) == 1
    linha = _linha(db, pid)
    assert (linha["estado"], linha["motivo"]) == (estado, motivo)
    assert [c.id for c in servico.chegadas()] == ([pid] if chega else [])


def test_o_contra_corrigido_pela_forma_nao_fecha_como_contra(
        validacao: tuple[Database, ServicoDeValidacao, Parque, Relogio, Planos]) -> None:
    db, servico, *_ = validacao
    pid, run_id = _roda(db, servico)
    _evidencia(db, "fluxo:abrir-o-qa", run_id, "against")
    _evidencia(db, "fluxo:abrir-o-qa", run_id, "forma")
    assert servico.minerar(run_id) == 1
    assert _linha(db, pid)["motivo"] == "divergencia_de_forma"


def test_o_motivo_segue_a_evidencia_que_chega_depois_uma_vez(
        validacao: tuple[Database, ServicoDeValidacao, Parque, Relogio, Planos]) -> None:
    db, servico, _parque, relogio, _planos = validacao
    pid, run_id = _roda(db, servico)
    assert servico.minerar(run_id) == 1 and _linha(db, pid)["motivo"] == "sem_evidencia"
    assert servico.executar(relogio.agora) == 0                       # nada chegou: nada muda
    _evidencia(db, "fluxo:abrir-o-qa", run_id, "against")             # a sombra de antes...
    _evidencia(db, "fluxo:abrir-o-qa", run_id, "forma")               # ...e a reclassificação
    assert servico.executar(relogio.agora) == 1
    assert (_linha(db, pid)["estado"], _linha(db, pid)["motivo"]) == ("recusada", "divergencia_de_forma")
    assert servico.executar(relogio.agora) == 0
    assert servico.chegadas() == []


def test_o_contra_que_chega_depois_leva_o_curador_ao_item(
        validacao: tuple[Database, ServicoDeValidacao, Parque, Relogio, Planos]) -> None:
    db, servico, _parque, relogio, _planos = validacao
    pid, run_id = _roda(db, servico)
    assert servico.minerar(run_id) == 1
    _evidencia(db, "fluxo:abrir-o-qa", run_id, "against")
    assert servico.executar(relogio.agora) == 1 and _linha(db, pid)["motivo"] == "evidencia_contra"
    assert [c.id for c in servico.chegadas()] == [pid]
    servico.revisado(pid, "lr-2")
    assert servico.chegadas() == [] and _linha(db, pid)["revisao_nova_id"] == "lr-2"


# ------------------------------------------------------------------ a receita sem caminho
def _envio(postcondicao: str) -> Plan:
    return Plan(summary="enviar", app_id="qa-messenger", app_package=QA,
                steps=[PlanStep(key="send_message", title="Enviar", goal="Tocar em Enviar.", side_effect=True,
                                postcondition=Postcondition(kind="element_present", value=postcondicao,
                                                            description="enviada"))],
                planner=PlannerInfo(provider="anthropic", model="claude", simulated=False))


def _receita_gravada(db: Database, step_hash: str) -> int:
    db.execute("INSERT INTO recipes(app_package, app_version, app_signature, variant, step_hash, step_key, version,"
               " status, actions, learned_from_step, created_at) VALUES (?,?,?,?,?,?,1,'active',?,?,?)",
               (QA, "1.0.0(1)", "", "en-US/xhdpi", step_hash, "send_message", json.dumps([]), "s0",
                "2026-10-02T19:00:00Z"))
    return int(db.scalar("SELECT id FROM recipes WHERE step_hash=?", (step_hash,)))


def test_a_variante_sem_caminho_nasce_recusada_e_volta_ao_curador(
        validacao: tuple[Database, ServicoDeValidacao, Parque, Relogio, Planos]) -> None:
    db, servico, parque, _relogio, planos = validacao
    planos.plano = _envio("text=Entregue")
    viva = _receita_gravada(db, step_template_hash(para_hash(planos.plano.steps[0], {})))
    antiga = _receita_gravada(db, "55f3aca9c156aed5b68d")              # a 54 do P4: outra redação da etapa
    pid = servico.ao_parecer(_receita(antiga), "lr-1", PEDE, B)
    assert pid is not None
    assert (_linha(db, pid)["estado"], _linha(db, pid)["motivo"]) == ("recusada", "sem_caminho")
    assert [c.id for c in servico.chegadas()] == [pid]                # o curador volta com a marca
    viva_pid = servico.ao_parecer(_receita(viva), "lr-2", PEDE, B)
    assert viva_pid is not None and _linha(db, viva_pid)["estado"] == "pendente"
    assert parque.enfileiradas == []


def test_o_fluxo_que_muda_antes_do_despacho_fecha_o_pedido_sem_execucao(
        validacao: tuple[Database, ServicoDeValidacao, Parque, Relogio, Planos]) -> None:
    db, servico, parque, relogio, planos = validacao
    planos.plano = _envio("text=Entregue")
    viva = _receita_gravada(db, step_template_hash(para_hash(planos.plano.steps[0], {})))
    pid = servico.ao_parecer(_receita(viva), "lr-1", PEDE, B)
    assert pid is not None and _linha(db, pid)["estado"] == "pendente"
    planos.plano = _envio("text=ENTREGUE")                            # o fluxo foi reaprendido com outra redação
    relogio.agora += timedelta(minutes=10)
    assert servico.uma_volta(lambda: 1) is None
    assert (_linha(db, pid)["estado"], _linha(db, pid)["motivo"], _linha(db, pid)["run_id"]) == (
        "recusada", "sem_caminho", None)
    assert parque.enfileiradas == []


def test_a_variante_que_rodou_antes_do_30_36_ganha_a_marca_depois(
        validacao: tuple[Database, ServicoDeValidacao, Parque, Relogio, Planos]) -> None:
    """A receita 54 do P4 rodou e fechou `sem_evidencia` antes do deploy: o passo da curadoria a remotiva."""
    db, servico, _parque, relogio, planos = validacao
    planos.plano = _envio("text=Entregue")
    antiga = _receita_gravada(db, "55f3aca9c156aed5b68d")
    db.execute("INSERT INTO learning_validations(id, created_at, updated_at, review_id, item_ref, item_kind, scope_app,"
               " grupo, falta, comando, estado, motivo, run_id, aparelho, usd, expira_em, feito_em)"
               " VALUES ('lv-54', ?, ?, 'lr-54', ?, 'receita', ?, 'qa', '[]', ?, 'recusada', 'sem_evidencia',"
               " 'r-p4-54', 'android-10', 0.07, ?, ?)",
               (to_iso(relogio.agora), to_iso(relogio.agora), f"receita:{antiga}", QA, COMANDO_QA,
                to_iso(relogio.agora), to_iso(relogio.agora)))
    assert servico.executar(relogio.agora) == 1
    assert (_linha(db, "lv-54")["estado"], _linha(db, "lv-54")["motivo"]) == ("recusada", "sem_caminho")
    assert [c.id for c in servico.chegadas()] == ["lv-54"]
    assert servico.executar(relogio.agora) == 0


# ------------------------------------------------------------------ o dossiê do curador
def _dossie(**kw: object) -> dict[str, object]:
    fatos = FatosDeRisco(side_effect=True, human_origin=False, tem_catalogo=False)
    return montar_dossie(IdentidadeDoItem(kind="receita", ref="54", app=QA, **kw),  # type: ignore[arg-type]
                         fatos, {"etapa": "send_message"},
                         evidencias=[Evidencia(id=1, posicao="for", origin_ref="run:r1", em="2026-10-02T19:54:28Z")]
                         ).como_dados()


def test_o_dossie_so_ganha_as_notas_quando_elas_valem() -> None:
    base = _dossie()
    item, evidencias = base["item"], base["evidencias"]
    assert isinstance(item, dict) and "sem_caminho" not in item
    assert isinstance(evidencias, dict) and "forma_e" not in evidencias
    marcada = _dossie(sem_caminho=True)["item"]
    assert isinstance(marcada, dict) and marcada["sem_caminho"] == SEM_CAMINHO_DA_RECEITA
    fatos = FatosDeRisco(side_effect=False, human_origin=False, tem_catalogo=False)
    com_forma = montar_dossie(IdentidadeDoItem(kind="fluxo", ref="f"), fatos, {},
                              evidencias=[Evidencia(id=2, posicao="forma", origin_ref="run:r2",
                                                    em="2026-10-03T16:07:35Z")]).como_dados()["evidencias"]
    assert isinstance(com_forma, dict) and com_forma["forma_e"] == FORMA_DA_EVIDENCIA


# ------------------------------------------------------------------ a composição do central
async def test_no_central_a_sombra_roda_antes_do_fechamento_do_pedido(harness: Harness) -> None:
    """A ordem do digest é a do registro, e o pedido fecha pela evidência que a sombra ACABOU de gravar: se um
    `ligar_*` futuro inverter as duas, toda validação de fluxo fecharia `sem_evidencia`."""
    state = harness.state
    assert state is not None
    nomes = [m.nome for m in state.learning._mineradores]  # noqa: SLF001 - a ordem é o que se confere
    assert nomes.index("fluxos_d1") < nomes.index("validacao")
    passos = [p.nome for p in state.learning._passos]  # noqa: SLF001
    assert {"forma_dos_fluxos", "validacao"} <= set(passos)
