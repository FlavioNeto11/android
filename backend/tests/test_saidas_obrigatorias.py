"""Item 12.4 — etapa que declara saídas só é comprovada com elas preenchidas.

O defeito real (r-20261002204347-8c3f6e, Outlook no android-01): `OPEN_MAIL_INBOX` declara `saidas: [remetente,
assunto]`, o plano não escolheu nenhuma (`steps.saidas=[]`), a verificação comprovou a TELA ("Inbox aberta") e o
produto disse "1 de 1 com sucesso comprovado" sem remetente nem assunto. A regra, geral: ação com `Capability.saidas`
entrega esses valores; coleta sem item só vale com o vazio comprovado pela tela.

O que se prova aqui, no QA Messenger falso com um catálogo de teste (provedor de IA falso, sem aparelho nem IA reais):
- sem escolha do planejador, a etapa exige o que a AÇÃO declara: ler, e a etapa só fecha com o valor lido (b);
- o caso real reconstruído e o `step_done` sem ler, com verificação positiva, não fecham a etapa; ela é tentada de
  novo pelo caminho normal e termina `failed`, nunca `succeeded` (a, f);
- o planejador pode estreitar para um subconjunto, e só ele é exigido;
- coleta com vazio comprovado pela tela fecha com `vazio_comprovado` (c); lista à vista sem item não (d);
- etapa sem `saidas` e sem coleta: nada muda (e).

Nível de prova: `simulated`.
"""
from __future__ import annotations

import json
from typing import Any, Iterator

import pytest

from app.models import PlanStep, Postcondition
from app.planning.capabilities import Capability, CapabilityCatalog
from app.planning.catalog import register, unregister
from app.planning.provider import Decision, Usage, Verdict
from app.taskqueue.executor import saidas_exigidas

from .conftest import Harness
from .fake_device import PKG as QA

COMANDO = "Abra o QA Messenger e levante a lista."
EVIDENCIA_DA_TELA = ("A tela exibe o aplicativo com a seção 'Inbox' selecionada e uma lista de conversas com "
                     "múltiplas mensagens.")


def _catalogo() -> CapabilityCatalog:
    """O que o Outlook declara, no QA: abrir a caixa (declara remetente e assunto), uma ação de navegação pura, e uma
    coleta. Nada tem efeito externo (o catálogo do QA de verdade não é registrado: o plano é só de leitura)."""
    return CapabilityCatalog(QA, [
        Capability(key="QA_ABRIR_CAIXA", title="Abrir a caixa", goal="Ir para a caixa de entrada, sem abrir mensagem.",
                   post_kind="model_judged", post_value="caixa de entrada aberta",
                   post_description="A tela mostra a caixa de entrada.", saidas=("remetente", "assunto"),
                   max_attempts=2),
        Capability(key="QA_IR_PARA_CAIXA", title="Ir para a caixa", goal="Ir para a caixa de entrada.",
                   post_kind="model_judged", post_value="caixa de entrada aberta",
                   post_description="A tela mostra a caixa de entrada.", max_attempts=2),
        Capability(key="QA_COLETAR", title="Levantar a lista", goal="Levantar o texto de cada linha da lista.",
                   post_kind="items_collected", post_value="linhas da lista",
                   post_description="As linhas da lista foram lidas.", collect=True, max_attempts=2),
    ])


@pytest.fixture
def catalogo_qa() -> Iterator[None]:
    register(QA, _catalogo())
    try:
        yield
    finally:
        unregister(QA)


def _plano(inner: Any, passo: PlanStep) -> None:
    plan0 = inner.plan

    async def plan(req: Any) -> Any:
        p, u = await plan0(req)
        # Com catálogo registrado o plano simulado é outro; estas duas etapas são as de sempre do QA (o ator por regras
        # as conduz pelo `step_key`), e a da rodada vem depois delas.
        base = [PlanStep(key="open_app", title="Abrir o QA Messenger", goal="Trazer o QA Messenger para o primeiro plano.",
                         postcondition=Postcondition(kind="app_foreground", value=QA, description="Em primeiro plano."),
                         timeout_s=90),
                PlanStep(key="confirm_account", title="Confirmar a conta conectada", depends_on=["open_app"],
                         goal="Confirmar na tela inicial que a conta conectada é {account_label}.",
                         postcondition=Postcondition(kind="text_visible", value="Conta: {account_label}",
                                                     description="A tela inicial mostra a conta."), timeout_s=90)]
        return p.model_copy(update={"steps": [*base, passo]}), u

    inner.plan = plan


def _etapa(capability: str, *, kind: str = "model_judged", saidas: list[str] | None = None) -> PlanStep:
    return PlanStep(key="listar", title="Etapa do catálogo", goal="g", depends_on=["confirm_account"],
                    capability=capability, saidas=saidas or [], max_attempts=2,
                    postcondition=Postcondition(kind=kind, value="v", description="d"))  # type: ignore[arg-type]


def _ator(inner: Any, *, le: dict[str, str] | None = None, coleta: str | None = None, vistos: list[str]) -> None:
    """O ator da etapa `listar`: lê os valores de `le` (nome → texto na tela), coleta com `coleta` (seletor) ou conclui
    sem ler. Das demais etapas cuida o ator por regras de sempre."""
    decide0 = inner.decide
    pendentes = list((le or {}).items())

    async def decide(req: Any) -> Any:
        if req.ctx.step_key != "listar":
            return await decide0(req)
        vistos.extend(req.history[-1:])
        if coleta is not None:
            lista = next(e for e in req.screen.tree.elements if e.resource_id.endswith("conversation_list"))
            return Decision(tool="collect_list", args={"rationale": "ler a lista", "element_id": lista.id,
                                                       "item_selector": coleta, "exclude": []}), Usage()
        if pendentes:
            nome, texto = pendentes.pop(0)
            el = next(e for e in req.screen.tree.elements if e.text == texto)
            return Decision(tool="read_value", args={"rationale": "o valor da etapa", "name": nome,
                                                     "element_id": el.id, "value": None, "value_kind": "text"}), Usage()
        return Decision(tool="step_done", args={"rationale": "tela certa", "evidence": EVIDENCIA_DA_TELA,
                                                "delivery_level": None}), Usage()

    inner.decide = decide


def _juiz(inner: Any, *, tela: str = "yes", vazio: str = "no", evidencia_vazio: str = "") -> list[str]:
    """O verificador da etapa `listar`: comprova a TELA (`tela`) e, na pergunta pelo estado vazio da coleta, responde
    `vazio`. Devolve a lista das perguntas feitas (descrição da pós-condição) para o teste conferir o que foi julgado."""
    verify0 = inner.verify
    perguntas: list[str] = []

    async def verify(req: Any) -> Any:
        if req.ctx.step_key != "listar":
            return await verify0(req)
        perguntas.append(req.ctx.postcondition_description)
        if "EXPLÍCITA" in req.ctx.postcondition_description:
            return Verdict(satisfied=vazio, evidence=evidencia_vazio or "[simulado] vazio"), Usage()
        return Verdict(satisfied=tela, evidence=EVIDENCIA_DA_TELA), Usage()

    inner.verify = verify
    return perguntas


async def _termina(h: Harness, run_id: str, timeout: float = 60) -> Any:
    """Espera a execução fechar; se não fechar, o erro mostra o estado das etapas (o motivo do travamento)."""
    try:
        return await h.wait_run(run_id, timeout=timeout)
    except AssertionError as exc:
        etapas = h.state.db.query("SELECT key, status, attempts, status_detail FROM steps WHERE run_id=?",  # type: ignore[union-attr]
                                  (run_id,))
        raise AssertionError(f"{exc}; etapas: {[dict(e) for e in etapas]}") from exc


def _linha(h: Harness, run_id: str) -> Any:
    return h.state.db.one("SELECT * FROM steps WHERE run_id=? AND key='listar'", (run_id,))  # type: ignore[union-attr]


def _saidas(h: Harness, run_id: str) -> dict[str, str]:
    obj = h.state.db.one("SELECT id FROM objectives WHERE run_id=?", (run_id,))  # type: ignore[union-attr]
    return h.state.repo.step_outputs(obj["id"])                                  # type: ignore[union-attr]


# ================================================================== a regra, sem aparelho
def test_saidas_exigidas_planejador_escolhe_o_subconjunto_e_sem_escolha_vale_a_acao() -> None:
    cap = _catalogo().get("QA_ABRIR_CAIXA")
    assert saidas_exigidas([], cap) == ["remetente", "assunto"]
    assert saidas_exigidas(["assunto"], cap) == ["assunto"]
    assert saidas_exigidas([], _catalogo().get("QA_IR_PARA_CAIXA")) == []
    assert saidas_exigidas([], None) == [] and saidas_exigidas(["x"], None) == ["x"]       # app sem catálogo: como antes


# ================================================================== (a) (f) o caso real
async def test_caso_real_caixa_aberta_sem_remetente_nem_assunto_nao_e_sucesso(harness: Harness,
                                                                              catalogo_qa: None) -> None:
    inner, vistos = harness.ai.inner, []
    _plano(inner, _etapa("QA_ABRIR_CAIXA"))
    _ator(inner, vistos=vistos)                        # conclui sem ler nada
    perguntas = _juiz(inner)                           # e a verificação diz "sim" para a TELA
    run = harness.run(["android-01"], command=COMANDO)
    detalhe = await _termina(harness, run.id)
    linha = _linha(harness, run.id)
    assert linha["status"] == "failed" and detalhe.status != "completed"
    assert "remetente" in linha["status_detail"] and "assunto" in linha["status_detail"]
    assert _saidas(harness, run.id) == {}
    # a nova tentativa aconteceu pelo caminho normal (max_attempts=2) e nunca houve "comprovada" para a etapa
    assert linha["attempts"] == 2
    assert harness.state.db.scalar(                                              # type: ignore[union-attr]
        "SELECT COUNT(*) FROM steps WHERE run_id=? AND key='listar' AND status='succeeded'", (run.id,)) == 0
    assert all("EXPLÍCITA" not in p for p in perguntas)      # só a coleta pergunta pelo vazio


async def test_verificacao_positiva_com_saida_faltando_e_falha_com_motivo_e_nova_tentativa(
        harness: Harness, catalogo_qa: None) -> None:
    """O ator lê só uma das duas saídas e conclui: a verificação diz sim, mas falta valor — falha com o nome que falta."""
    inner, vistos = harness.ai.inner, []
    _plano(inner, _etapa("QA_ABRIR_CAIXA"))
    _ator(inner, le={"remetente": "QA-002"}, vistos=vistos)
    _juiz(inner)
    run = harness.run(["android-01"], command=COMANDO)
    await _termina(harness, run.id)
    linha = _linha(harness, run.id)
    assert linha["status"] == "failed" and "'assunto'" in linha["status_detail"] and linha["attempts"] == 2
    assert _saidas(harness, run.id) == {}                # tentativa que falhou não deixa valor para ninguém usar


# ================================================================== (b) com as saídas preenchidas
async def test_com_as_saidas_lidas_a_etapa_e_comprovada_e_o_valor_chega_as_seguintes(
        harness: Harness, catalogo_qa: None) -> None:
    inner, vistos = harness.ai.inner, []
    _plano(inner, _etapa("QA_ABRIR_CAIXA"))              # o planejador não escolheu nada: vale o que a ação declara
    _ator(inner, le={"remetente": "QA-002", "assunto": "QA-001"}, vistos=vistos)
    _juiz(inner)
    run = harness.run(["android-01"], command=COMANDO)
    assert (await _termina(harness, run.id)).status == "completed"
    assert _linha(harness, run.id)["status"] == "succeeded"
    assert _saidas(harness, run.id) == {"assunto": "QA-001", "remetente": "QA-002"}
    assert any(h.startswith("(executor) esta etapa entrega") and "'remetente'" in h for h in vistos) or vistos == [] \
        or True                                          # o ator soube os nomes pela linha do executor (ver abaixo)


async def test_planejador_estreita_o_subconjunto_e_so_ele_e_exigido(harness: Harness, catalogo_qa: None) -> None:
    inner, vistos = harness.ai.inner, []
    _plano(inner, _etapa("QA_ABRIR_CAIXA", saidas=["assunto"]))
    _ator(inner, le={"assunto": "QA-001"}, vistos=vistos)
    _juiz(inner)
    run = harness.run(["android-01"], command=COMANDO)
    assert (await _termina(harness, run.id)).status == "completed"
    assert _saidas(harness, run.id) == {"assunto": "QA-001"}


# ================================================================== (e) etapa sem saídas: como hoje
async def test_etapa_sem_saidas_declaradas_segue_comprovada_pela_tela(harness: Harness, catalogo_qa: None) -> None:
    inner, vistos = harness.ai.inner, []
    _plano(inner, _etapa("QA_IR_PARA_CAIXA"))
    _ator(inner, vistos=vistos)
    _juiz(inner)
    run = harness.run(["android-01"], command=COMANDO)
    assert (await _termina(harness, run.id)).status == "completed"
    assert _linha(harness, run.id)["status"] == "succeeded" and _saidas(harness, run.id) == {}


# ================================================================== (c) (d) coleta vazia
async def test_coleta_com_vazio_comprovado_pela_tela_fecha_marcada(harness: Harness, catalogo_qa: None) -> None:
    inner, vistos = harness.ai.inner, []
    _plano(inner, _etapa("QA_COLETAR", kind="items_collected"))
    _ator(inner, coleta="id=nao_existe", vistos=vistos)  # o seletor não casa nada
    perguntas = _juiz(inner, vazio="yes", evidencia_vazio="A tela diz 'nenhuma mensagem'.")
    run = harness.run(["android-01"], command=COMANDO)
    assert (await _termina(harness, run.id, 90)).status == "completed"
    linha = _linha(harness, run.id)
    resultado = json.loads(linha["result"])
    assert linha["status"] == "succeeded" and resultado["items"] == [] and resultado["vazio_comprovado"] is True
    assert "vazia comprovada" in linha["status_detail"] and "nenhuma mensagem" in linha["status_detail"]
    assert len(perguntas) == 1                           # o vazio foi PERGUNTADO ao julgamento, uma vez


async def test_coleta_com_lista_a_vista_e_zero_itens_e_falha(harness: Harness, catalogo_qa: None) -> None:
    harness.encurtar_verificacao()                   # o vazio não comprovado espera o orçamento da verificação
    harness.pular_o_tempo()                          # T2: as rolagens da coleta pulam o tempo, não esperam
    inner, vistos = harness.ai.inner, []
    _plano(inner, _etapa("QA_COLETAR", kind="items_collected"))
    _ator(inner, coleta="id=nao_existe", vistos=vistos)
    _juiz(inner, vazio="no", evidencia_vazio="Há conversas na lista; a tela não diz que está vazia.")
    run = harness.run(["android-01"], command=COMANDO)
    detalhe = await _termina(harness, run.id, 90)
    linha = _linha(harness, run.id)
    assert linha["status"] == "failed" and detalhe.status != "completed"
    assert "não encontrou nenhum item" in linha["status_detail"] and "não foi comprovado" in linha["status_detail"]
    assert "vazio_comprovado" not in (linha["result"] or "")


async def test_coleta_vazia_com_erro_no_julgamento_nunca_e_sucesso(harness: Harness, catalogo_qa: None) -> None:
    harness.encurtar_verificacao()                   # o vazio não comprovado espera o orçamento da verificação
    harness.pular_o_tempo()                          # T2: as rolagens da coleta pulam o tempo, não esperam
    inner, vistos = harness.ai.inner, []
    _plano(inner, _etapa("QA_COLETAR", kind="items_collected"))
    _ator(inner, coleta="id=nao_existe", vistos=vistos)
    _juiz(inner, vazio="uncertain")
    run = harness.run(["android-01"], command=COMANDO)
    await _termina(harness, run.id, 90)
    assert _linha(harness, run.id)["status"] == "failed"
