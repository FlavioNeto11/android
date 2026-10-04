"""Item 31.41: o valor lido só é gravado com evidência de RELAÇÃO com o nome pedido; dúvida nunca fecha como sucesso.

Achado real: r-20261004120701-03d58e (QA Messenger, android-12, 04/10). Pedido o "número do protocolo", o ator leu um id
de execução antigo de uma mensagem ("Prova 203 r-…") e a etapa fechou `succeeded`. Travas de regressão: a f014e6 (o nome
da primeira conversa, lido pelo resource-id `conversation_name`) e as leituras VISUAIS do 12.3 no Outlook (remetente e
assunto, r-20261004094557-d62546 e r-20261004094808-e7df7c), que vão à pergunta única ao verificador.

Nível de prova: `simulated` (árvores escritas à mão no formato do QA Messenger e provedor simulado; nenhuma IA paga).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.automation.hierarchy import parse_hierarchy
from app.devices.manager import Observation
from app.planning.capabilities import Capability, saidas_error
from app.planning.provider import Verdict
from app.taskqueue.relacao import relacao_do_valor

from .conftest import Harness

QA = "com.pocqa.messenger"


def _no(i: int, texto: str, rid: str, y: int, *, x1: int = 40, x2: int = 680, h: int = 46, desc: str = "") -> str:
    return (f'<node index="{i}" text="{texto}" resource-id="{QA}:id/{rid}" class="android.widget.TextView" '
            f'package="{QA}" content-desc="{desc}" clickable="false" enabled="true" '
            f'bounds="[{x1},{y}][{x2},{y + h}]" />')


def _arvore(*nos: str) -> Any:
    return parse_hierarchy('<hierarchy rotation="0">' + "".join(nos) + "</hierarchy>")


def _el(arvore: Any, texto: str) -> Any:
    return next(e for e in arvore.elements if e.text == texto)


# ------------------------------------------------------------------ a regra, sem aparelho
def test_caso_real_03d58e_id_numa_mensagem_nao_e_protocolo() -> None:
    conversa = _arvore(_no(0, "QA-001", "chat_title", 40), _no(1, "Prova 203 r-20261004103305-1f3ac3", "message_text", 300),
                       _no(2, "10:33", "message_time", 300, x1=600, x2=680), _no(3, "Oi", "message_text", 400))
    assert relacao_do_valor(conversa, _el(conversa, "Prova 203 r-20261004103305-1f3ac3"), "protocolo",
                            "r-20261004103305-1f3ac3") is None


def test_rotulo_vizinho_e_rotulo_no_proprio_texto_valem() -> None:
    linha = _arvore(_no(0, "Protocolo", "label", 300, x2=300), _no(1, "123456", "value", 300, x1=320))
    assert relacao_do_valor(linha, _el(linha, "123456"), "protocolo", "123456") == "rotulo"
    acima = _arvore(_no(0, "Nº do protocolo", "label", 240), _no(1, "123456", "value", 300))
    assert relacao_do_valor(acima, _el(acima, "123456"), "protocolo", "123456") == "rotulo"
    junto = _arvore(_no(0, "Protocolo: 98765", "message_text", 300))
    assert relacao_do_valor(junto, _el(junto, "Protocolo: 98765"), "protocolo", "98765") == "rotulo"


def test_forma_vale_so_para_tipos_fechados_e_numero_sozinho_nao() -> None:
    a = _arvore(_no(0, "ana@exemplo.com", "message_text", 300), _no(1, "123456", "message_text", 400),
                _no(2, "12/03/1990", "message_text", 500))
    assert relacao_do_valor(a, _el(a, "ana@exemplo.com"), "email_do_contato", "ana@exemplo.com") == "forma"
    assert relacao_do_valor(a, _el(a, "12/03/1990"), "data_de_nascimento", "12/03/1990") == "forma"
    assert relacao_do_valor(a, _el(a, "123456"), "numero", "123456") is None          # número sozinho: dúvida
    assert relacao_do_valor(a, _el(a, "123456"), "email", "123456") is None           # forma errada: dúvida


def test_seletor_do_catalogo_vale_e_entrada_torta_e_recusada_na_carga() -> None:
    a = _arvore(_no(0, "A1B2", "ticket_id", 300))
    assert relacao_do_valor(a, _el(a, "A1B2"), "protocolo", "A1B2",
                            relacoes=(f"protocolo=id={QA}:id/ticket_id",)) == "seletor"
    assert relacao_do_valor(a, _el(a, "A1B2"), "protocolo", "A1B2", relacoes=("protocolo~ticket",)) == "rotulo"
    base = dict(key="LER", title="t", goal="g", post_kind="model_judged", post_value="v", post_description="d",
                saidas=("protocolo",))
    assert saidas_error(Capability(**base, saidas_relacao=("protocolo=id=x",))) is None   # type: ignore[arg-type]
    assert "saidas_relacao" in str(saidas_error(Capability(**base, saidas_relacao=("assunto=id=x",))))  # type: ignore[arg-type]
    assert "saidas_relacao" in str(saidas_error(Capability(**base, saidas_relacao=("protocolo",))))     # type: ignore[arg-type]


def test_trava_f014e6_o_nome_da_primeira_conversa_continua_valendo() -> None:
    """A leitura real da f014e6: 'primeira_conversa' no elemento `conversation_name` (texto 'Suporte QA')."""
    lista = _arvore(_no(0, "Suporte QA", "conversation_name", 312), _no(1, "Olá!", "conversation_preview", 360),
                    _no(2, "QA-002", "conversation_name", 430))
    assert relacao_do_valor(lista, _el(lista, "Suporte QA"), "primeira_conversa", "Suporte QA") == "rotulo"


# ------------------------------------------------------------------ a leitura visual (Outlook, 12.3)
def _obs(arvore: Any) -> Observation:
    return Observation(frame_id="f", ts="t", width=720, height=1280, jpeg=b"\xff\xd8jpeg", tree=arvore, package="p",
                       sensitive=False)


@pytest.mark.parametrize("veredito,esperado", [("yes", "verificador"), ("no", None), ("uncertain", None)])
async def test_trava_12_3_leitura_visual_pergunta_ao_verificador_e_incerto_e_nao(
        harness: Harness, monkeypatch: Any, veredito: str, esperado: str | None) -> None:
    """remetente e assunto do Outlook vêm da IMAGEM (d62546, e7df7c): sem elemento com texto, UMA pergunta de sim ou não
    ao verificador. "sim" mantém a leitura de hoje; "não" e "incerto" recusam."""
    executor = harness.state.scheduler.executor                                       # type: ignore[union-attr]
    perguntas: list[str] = []

    async def ai(run_id: str, oid: str, chamada: Any, **kw: Any) -> Verdict:
        perguntas.append(kw.get("role", ""))
        return Verdict(satisfied=veredito, evidence="[simulado]")              # type: ignore[arg-type]

    monkeypatch.setattr(executor, "_ai", ai)
    monkeypatch.setattr(executor, "_screen", lambda obs, **kw: (None, 1.0))

    import dataclasses

    @dataclasses.dataclass
    class Ctx:
        postcondition_description: str = ""

    step = type("S", (), {"id": "s1", "commit_guard": []})()
    caixa = _arvore(_no(0, "", "message_row", 300))
    r = await executor._relacao_visual(None, step, lambda: Ctx(), "r", "o", 1e12, "a", _obs(caixa), "remetente",
                                       "Equipe Microsoft", harness.cfg.file.ai)
    assert r == esperado and perguntas == ["verify"]                    # uma chamada por leitura visual


# ------------------------------------------------------------------ de ponta a ponta, no harness
async def test_leitura_sem_relacao_e_recusada_e_vira_dado_ausente(tmp_path: Path) -> None:
    """O 03d58e no QA falso, como foi (planejamento LIVRE, sem catálogo): o ator lê, para 'protocolo', um texto sem
    rótulo nem forma; a leitura é recusada (métrica e linha do tempo), nunca gravada, e a etapa termina pelo caminho do
    31.38."""
    from app.planning.provider import Decision, Usage
    from app.models import PlanStep, Postcondition
    from .conftest import CountingProvider
    from .fake_device import PKG
    from app.planning.simulated_provider import SimulatedProvider

    h = Harness(tmp_path, 1)
    prov = SimulatedProvider()
    plan0, decide0 = prov.plan, prov.decide

    async def plan(req: Any) -> Any:
        p, u = await plan0(req)
        base = PlanStep(key="open_app", title="Abrir", goal="Abrir o QA Messenger.",
                        postcondition=Postcondition(kind="app_foreground", value=PKG, description="frente"), timeout_s=90)
        ler = PlanStep(key="ler", title="Ler o protocolo", goal="g", depends_on=["open_app"],
                       saidas=["protocolo"], max_attempts=1,
                       postcondition=Postcondition(kind="model_judged", value="v", description="d"))
        return p.model_copy(update={"steps": [base, ler]}), u

    async def decide(req: Any) -> Any:
        if req.ctx.step_key != "ler":
            return await decide0(req)
        alvo = next(e for e in req.screen.tree.elements if e.text.strip())      # qualquer texto da tela, sem rótulo
        return Decision(tool="read_value", args={"rationale": "ler", "name": "protocolo", "element_id": alvo.id,
                                                 "value": None, "value_kind": "text"}), Usage()

    prov.plan, prov.decide = plan, decide                                    # type: ignore[method-assign]
    h.ai = CountingProvider(prov)
    state = await h.boot()
    try:
        run = h.run(["android-01"], command="Abra o QA Messenger e leia o protocolo.")
        await h.wait_run(run.id, statuses=("completed", "completed_with_issues", "failed", "waiting_user"))
        assert state.db.scalar("SELECT COUNT(*) FROM step_outputs WHERE run_id=?", (run.id,)) == 0   # nada gravado
        detalhes = [r["status_detail"] for r in state.db.query(
            "SELECT status_detail FROM steps WHERE run_id=? AND key='ler' ORDER BY plan_version", (run.id,))]
        assert detalhes and all(d.startswith("Dado ausente:") for d in detalhes)
        assert state.db.scalar("SELECT status FROM objectives WHERE run_id=?", (run.id,)) == "failed"
        assert state.db.scalar("SELECT COUNT(*) FROM actions WHERE status='rejected' AND error LIKE ?",
                               ("sem relação com%",)) >= 4
    finally:
        await state.stop()
