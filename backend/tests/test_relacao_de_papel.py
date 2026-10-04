"""Item 31.47: nome de PAPEL (manchete, título, assunto, remetente…) é julgado como papel, não como palavra do texto.

Achado real: r-20261004172132-df1212 (Chrome, g1, android-09, 04/10), etapa `read_headline`. O ator chamou
`read_value(name="manchete", element_id="e85")` duas vezes e as duas foram recusadas com "sem relação com 'manchete'"
(31.41): a regra da árvore não achou rótulo nem forma, o nome não estava em `cap.saidas` (planejamento livre) e NENHUM
juiz foi chamado; o texto de uma manchete nunca contém "manchete". A etapa fechou em dado ausente.

Aqui: o nome de papel (lista fechada) sempre vai ao juiz, com a pergunta "este elemento ocupa o papel X nesta tela?";
"não" recusa (controle negativo: rodapé, menu, botão); nome que não é de papel segue com a pergunta de relação de antes.

Nível de prova: `simulated` (árvores escritas à mão, provedor e juiz falsos; nenhuma IA paga, nenhum aparelho).
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from app.automation.hierarchy import parse_hierarchy
from app.devices.manager import Observation
from app.planning.provider import Verdict
from app.taskqueue.relacao import e_nome_de_papel, pergunta_de_papel, relacao_do_valor

from .conftest import Harness

PKG = "com.android.chrome"


def _no(i: int, texto: str, rid: str, y: int, *, h: int = 80, x1: int = 40, x2: int = 680) -> str:
    return (f'<node index="{i}" text="{texto}" resource-id="{PKG}:id/{rid}" class="android.widget.TextView" '
            f'package="{PKG}" content-desc="" clickable="false" enabled="true" bounds="[{x1},{y}][{x2},{y + h}]" />')


def _g1() -> Any:
    """A tela do g1 na df1212: a manchete em destaque no alto, o rodapé e um item de menu lá embaixo."""
    return parse_hierarchy('<hierarchy rotation="0">' + "".join((
        _no(0, "Lula vence em mais países, mas Flávio soma mais votos", "headline_text", 400, h=160),
        _no(1, "Fale conosco", "footer_link", 1180, h=40),
        _no(2, "Assine", "menu_item", 60, h=40))) + "</hierarchy>")


def _el(arvore: Any, texto: str) -> Any:
    return next(e for e in arvore.elements if e.text == texto)


def _obs(arvore: Any) -> Observation:
    return Observation(frame_id="f", ts="t", width=720, height=1280, jpeg=b"\xff\xd8jpeg", tree=arvore, package=PKG,
                       sensitive=False)


# ------------------------------------------------------------------ a lista fechada
@pytest.mark.parametrize("nome", ["manchete", "titulo", "título", "headline", "title", "assunto", "subject", "nome",
                                  "name", "remetente", "sender", "autor", "author", "primeira_manchete",
                                  "titulo_do_video", "Manchete Principal"])
def test_nome_de_papel_e_reconhecido(nome: str) -> None:
    assert e_nome_de_papel(nome)


@pytest.mark.parametrize("nome", ["preco", "protocolo", "codigo", "valor", "email", "data", "telefone", "total",
                                  "posts_antes", "numero_do_pedido"])
def test_nome_que_nao_e_de_papel_nao_e_reconhecido(nome: str) -> None:
    assert not e_nome_de_papel(nome)


def test_a_manchete_do_g1_nao_tem_evidencia_na_arvore_e_a_regra_deterministica_continua_em_duvida() -> None:
    """O defeito: sem rótulo vizinho nem forma, a regra devolve None (dúvida). O conserto não a afrouxa: manda a dúvida
    ao juiz, só para nome de papel."""
    g1 = _g1()
    texto = "Lula vence em mais países, mas Flávio soma mais votos"
    assert relacao_do_valor(g1, _el(g1, texto), "manchete", texto) is None


def test_a_pergunta_de_papel_pergunta_o_papel_e_nomeia_o_que_nao_ocupa() -> None:
    g1 = _g1()
    alvo = _el(g1, "Fale conosco")
    p = pergunta_de_papel("manchete", "Fale conosco", alvo, (720, 1280))
    assert "ocupa o papel de 'manchete'" in p and "PAPEL" in p
    assert "rodapé" in p and "item de menu" in p and "botão" in p              # o controle negativo está no enunciado
    assert f"elemento {alvo.id}" in p and "[40,1180][680,1220]" in p and "720x1280" in p   # o juiz acha o elemento
    assert "tem relação" not in p


# ------------------------------------------------------------------ o juiz (visual) por nome
async def _perguntar(harness: Harness, monkeypatch: Any, nome: str, valor: str, veredito: str, alvo: Any
                     ) -> tuple[str | None, list[str]]:
    """Chama `_relacao_visual` com juiz falso e devolve (resposta, enunciados que chegaram ao provedor)."""
    executor = harness.state.scheduler.executor                                       # type: ignore[union-attr]
    enunciados: list[str] = []

    async def verify(req: Any) -> tuple[Verdict, None]:
        enunciados.append(req.ctx.postcondition_description)
        return Verdict(satisfied=veredito, evidence="[simulado]"), None             # type: ignore[arg-type]

    async def ai(run_id: str, oid: str, chamada: Any, **kw: Any) -> Verdict:
        veredito_, _ = await chamada()
        return veredito_

    monkeypatch.setattr(executor.provider, "verify", verify)
    monkeypatch.setattr(executor, "_ai", ai)
    monkeypatch.setattr(executor, "_screen", lambda obs, **kw: (None, 1.0))

    @dataclasses.dataclass
    class Ctx:
        postcondition_description: str = ""

    step = type("S", (), {"id": "s1", "commit_guard": []})()
    r = await executor._relacao_visual(None, step, lambda: Ctx(), "r", "o", 1e12, "a", _obs(_g1()), nome, valor,
                                       harness.cfg.file.ai, alvo=alvo)
    return r, enunciados


async def test_caso_real_df1212_o_juiz_recebe_a_pergunta_de_papel_e_ocupa_aceita(harness: Harness,
                                                                                 monkeypatch: Any) -> None:
    g1 = _g1()
    texto = "Lula vence em mais países, mas Flávio soma mais votos"
    r, enunciados = await _perguntar(harness, monkeypatch, "manchete", texto, "yes", _el(g1, texto))
    assert r == "verificador" and len(enunciados) == 1
    assert "ocupa o papel de 'manchete'" in enunciados[0] and "bounds [40,400][680,560]" in enunciados[0]
    assert "tem relação" not in enunciados[0] and "Julgue SÓ a relação" not in enunciados[0]


@pytest.mark.parametrize("veredito", ["no", "uncertain"])
@pytest.mark.parametrize("texto", ["Fale conosco", "Assine"])
async def test_controle_negativo_rodape_e_menu_nao_ocupam_o_papel_e_sao_recusados(
        harness: Harness, monkeypatch: Any, texto: str, veredito: str) -> None:
    """A pergunta nova não vira "aceita qualquer texto": rodapé e item de menu, o juiz diz que NÃO ocupam (ou que não
    dá para afirmar), e a leitura é recusada."""
    r, enunciados = await _perguntar(harness, monkeypatch, "manchete", texto, veredito, _el(_g1(), texto))
    assert r is None and len(enunciados) == 1 and "ocupa o papel de 'manchete'" in enunciados[0]


async def test_nome_que_nao_e_de_papel_continua_com_a_pergunta_antiga(harness: Harness, monkeypatch: Any) -> None:
    r, enunciados = await _perguntar(harness, monkeypatch, "preco", "R$ 10,00", "yes", None)
    assert r == "verificador" and len(enunciados) == 1
    assert enunciados[0].startswith("O valor lido para 'preco' foi \"R$ 10,00\". Julgue SÓ a relação:")
    assert "PAPEL" not in enunciados[0]


# ------------------------------------------------------------------ de ponta a ponta, no harness
@pytest.mark.parametrize("veredito", ["yes", "no"])
async def test_leitura_de_manchete_de_ponta_a_ponta(tmp_path: Path, veredito: str) -> None:
    """A df1212 no QA falso, planejamento LIVRE (sem catálogo): `read_value(name="manchete")` num texto sem rótulo nem
    forma. Com o juiz dizendo que o elemento ocupa o papel, a leitura é gravada; dizendo que não, é recusada quatro vezes
    e a etapa termina em dado ausente, como no 31.41. Em ambos, o juiz recebeu a pergunta de papel."""
    from app.models import PlanStep, Postcondition
    from app.planning.provider import Decision, Usage
    from app.planning.simulated_provider import SimulatedProvider

    from .conftest import CountingProvider
    from .fake_device import PKG as QA

    h = Harness(tmp_path, 1)
    prov = SimulatedProvider()
    plan0, decide0, verify0 = prov.plan, prov.decide, prov.verify
    enunciados: list[str] = []
    lidas: list[int] = []

    async def plan(req: Any) -> Any:
        p, u = await plan0(req)
        base = PlanStep(key="open_app", title="Abrir", goal="Abrir o QA Messenger.",
                        postcondition=Postcondition(kind="app_foreground", value=QA, description="frente"), timeout_s=90)
        ler = PlanStep(key="ler", title="Ler a manchete", goal="g", depends_on=["open_app"], saidas=["manchete"],
                       max_attempts=1, postcondition=Postcondition(kind="model_judged", value="v", description="d"))
        return p.model_copy(update={"steps": [base, ler]}), u

    async def decide(req: Any) -> Any:
        if req.ctx.step_key != "ler":
            return await decide0(req)
        if lidas and veredito == "yes":                                          # leitura aceita: o ator encerra
            return Decision(tool="step_done", args={"rationale": "lida", "evidence": "manchete lida",
                                                    "delivery_level": "none"}), Usage()
        lidas.append(1)
        alvo = next(e for e in req.screen.tree.elements if e.text.strip())      # qualquer texto da tela, sem rótulo
        return Decision(tool="read_value", args={"rationale": "ler", "name": "manchete", "element_id": alvo.id,
                                                 "value": None, "value_kind": "text"}), Usage()

    async def verify(req: Any) -> Any:
        if "PAPEL" in req.ctx.postcondition_description:
            enunciados.append(req.ctx.postcondition_description)
            return Verdict(satisfied=veredito, evidence="[simulado]"), Usage()   # type: ignore[arg-type]
        return await verify0(req)

    prov.plan, prov.decide, prov.verify = plan, decide, verify                # type: ignore[method-assign]
    h.ai = CountingProvider(prov)
    state = await h.boot()
    try:
        run = h.run(["android-01"], command="Abra o QA Messenger e leia a manchete.")
        await h.wait_run(run.id, statuses=("completed", "completed_with_issues", "failed", "waiting_user"))
        saidas = state.db.scalar("SELECT COUNT(*) FROM step_outputs WHERE run_id=?", (run.id,))   # só a recusa o confere
        recusadas = state.db.scalar("SELECT COUNT(*) FROM actions WHERE status='rejected' AND error LIKE ?",
                                    ("sem relação com%",))
        assert enunciados and all("ocupa o papel de 'manchete'" in e for e in enunciados)
        if veredito == "yes":
            # A leitura em si foi aceita (a etapa pode falhar depois, pela pós-condição do provedor simulado, que não é o
            # assunto aqui): ação `read_value` concluída, nenhuma recusa por relação.
            lidas_ok = state.db.scalar("SELECT COUNT(*) FROM actions WHERE tool='read_value' AND status='done'")
            assert lidas_ok >= 1 and recusadas == 0
        else:
            assert saidas == 0 and recusadas >= 4
    finally:
        await state.stop()
