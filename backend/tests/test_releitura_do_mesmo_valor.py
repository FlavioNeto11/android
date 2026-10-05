"""31.78: a etapa de leitura julgada, sem prova local, não gira mais lendo o mesmo valor até o teto e falhar por "dado
ausente".

Achado real (05/10, r-20261005074912-701173, a prévia do 29.30): a `READ_POSTS_COUNT` leu o "0" da contagem 12 vezes
(nenhum toque), nunca chamou `step_done`, e o teto de 12 decisões da leitura (31.38) a encerrou com "procurei
'posts_antes' … e não encontrei". O valor estava lido desde a 1ª decisão. Agora:
- releitura do mesmo nome com o MESMO valor e nada faltando: na 1ª, o executor diz ao ator que é hora de `step_done`; na
  2ª, a etapa vai à verificação, que julga a pós-condição como depois de um `step_done` (nada sai comprovado sem ela);
- o teto com tudo lido vai à verificação; com algo faltando, segue `dado_ausente`;
- valor diferente na releitura não é repetição (a tela pode ter mudado).

Nível de prova: `simulated` (harness na porta 5640, catálogo de teste no QA Messenger falso; nenhuma IA paga).
"""
from __future__ import annotations

from typing import Any, Iterator

import pytest

from app.models import PlanStep, Postcondition
from app.planning.capabilities import Capability, CapabilityCatalog
from app.planning.catalog import register, unregister
from app.planning.provider import Decision, Usage, Verdict
from app.taskqueue.executor import PREFIXO_DADO_AUSENTE

from . import fake_device
from .conftest import Harness
from .fake_device import PKG as QA
from .test_dado_ausente import COMANDO, _fim, _plano

RELEU = "releu"
TETO_COM_TUDO_LIDO = "com todos os valores lidos"


@pytest.fixture
def contagem() -> Iterator[None]:
    """A contagem na lista do QA Messenger: "0" e "7" viram contatos, cada um num elemento da tela inicial."""
    originais = list(fake_device.CONTACTS)
    fake_device.CONTACTS[:] = ["0", "7", *originais]
    try:
        yield
    finally:
        fake_device.CONTACTS[:] = originais


def _catalogo(*saidas: str) -> None:
    register(QA, CapabilityCatalog(QA, [
        Capability(key="QA_LER_CONTAGEM", title="Ler a contagem", goal="Ler a contagem na tela inicial.",
                   post_kind="model_judged", post_value="contagem lida", post_description="A contagem foi lida.",
                   saidas=saidas, max_attempts=3),
    ]))


def _ator(inner: Any, valores: list[str], visto: dict[str, Any]) -> None:
    """O ator da etapa `ler` que só relê a contagem (nunca conclui), na ordem de `valores`, em ciclo."""
    decide0 = inner.decide

    async def decide(req: Any) -> Any:
        if req.ctx.step_key != "ler":
            return await decide0(req)
        i = visto.setdefault("decisoes", 0)
        visto["decisoes"] = i + 1
        visto.setdefault("historicos", []).append(list(req.history))
        alvo = next(e for e in req.screen.tree.elements if e.text == valores[i % len(valores)])
        return Decision(tool="read_value", args={"rationale": "a contagem", "name": "contagem", "element_id": alvo.id,
                                                 "value": None, "value_kind": "number"}), Usage()

    inner.decide = decide


def _verificador(inner: Any, visto: dict[str, Any]) -> None:
    """O juiz da pós-condição de `ler` diz "sim" e conta as vezes: é a verificação que fecha a etapa, nunca o laço."""
    verify0 = inner.verify

    async def verify(req: Any) -> Any:
        if req.ctx.step_key != "ler":
            return await verify0(req)
        visto["verificacoes"] = visto.get("verificacoes", 0) + 1
        return Verdict(satisfied="yes", evidence="[simulado] a contagem está na tela"), Usage()

    inner.verify = verify


async def _rodar(h: Harness, valores: list[str], *saidas: str) -> tuple[dict[str, Any], Any]:
    h.cfg.file.ai.max_decisoes_leitura = 6
    h.cfg.file.ai.relacao_do_valor = False              # a relação do valor (31.41) tem teste próprio
    _catalogo(*saidas)
    visto: dict[str, Any] = {}
    try:
        _plano(h.ai.inner, PlanStep(key="ler", title="Ler a contagem", goal="g", depends_on=["open_app"],
                                    capability="QA_LER_CONTAGEM", max_attempts=1,
                                    postcondition=Postcondition(kind="model_judged", value="v", description="d")))
        _ator(h.ai.inner, valores, visto)
        _verificador(h.ai.inner, visto)
        run = h.run(["android-01"], command=COMANDO)
        await _fim(h, run.id)
    finally:
        unregister(QA)
    return visto, run


def _eventos(h: Harness, run_id: str) -> list[str]:
    return [r["message"] for r in h.state.db.query("SELECT message FROM events WHERE run_id=? AND kind='decision'",  # type: ignore[union-attr]
                                                   (run_id,))]


@pytest.mark.parametrize("valor", ["0", "7"])
async def test_releitura_do_mesmo_valor_vai_a_verificacao_na_segunda(harness: Harness, contagem: None,
                                                                    valor: str) -> None:
    """O roteiro da r-…-701173 com o "0" e com o "7": o zero não tem nada de especial, o laço é que não tinha saída."""
    visto, run = await _rodar(harness, [valor], "contagem")
    db = harness.state.db                                                     # type: ignore[union-attr]
    assert harness.state.repo.run_row(run.id)["status"] == "completed"         # type: ignore[union-attr]
    assert db.scalar("SELECT value FROM step_outputs WHERE name='contagem'") == valor
    assert visto["decisoes"] == 3                       # ler, reler (o aviso), reler (verificação): não 6 nem 12
    assert visto["verificacoes"] == 1
    avisos = [l for l in visto["historicos"][2] if l.startswith("(executor) 'contagem' já foi lido")]
    assert len(avisos) == 1 and valor not in avisos[0].replace("'contagem'", "")   # o aviso não traz o valor
    assert not any(l.startswith("(executor) 'contagem' já foi lido") for l in visto["historicos"][1])
    assert any(RELEU in m for m in _eventos(harness, run.id))
    assert db.scalar("SELECT COUNT(*) FROM actions WHERE tool='step_done' AND attempt_id LIKE '%:ler:%'") == 0


async def test_valor_diferente_na_releitura_nao_e_repeticao_e_o_teto_com_tudo_lido_verifica(
        harness: Harness, contagem: None) -> None:
    """0, 7, 0, 7…: cada leitura troca o valor, então nunca há repetição; o teto (6) chega com tudo lido e vai à
    verificação, com o ÚLTIMO valor lido, em vez de "não encontrei"."""
    visto, run = await _rodar(harness, ["0", "7"], "contagem")
    db = harness.state.db                                                     # type: ignore[union-attr]
    assert visto["decisoes"] == 6 and visto["verificacoes"] == 1
    assert harness.state.repo.run_row(run.id)["status"] == "completed"         # type: ignore[union-attr]
    assert db.scalar("SELECT value FROM step_outputs WHERE name='contagem'") == "7"
    eventos = _eventos(harness, run.id)
    assert any(TETO_COM_TUDO_LIDO in m for m in eventos) and not any(RELEU in m for m in eventos)
    assert not any(l.startswith("(executor) 'contagem' já foi lido") for h in visto["historicos"] for l in h)
    assert not any((r["status_detail"] or "").startswith(PREFIXO_DADO_AUSENTE)
                   for r in db.query("SELECT status_detail FROM steps WHERE key='ler'"))


async def test_com_outra_saida_faltando_nao_vai_a_verificacao(harness: Harness, contagem: None) -> None:
    """A etapa entrega `contagem` e `outro`; o ator só relê a contagem. Nada de aviso nem de verificação: o teto fecha
    como `dado_ausente`, como no 31.38, e a frase diz o teto."""
    visto, run = await _rodar(harness, ["0"], "contagem", "outro")
    db = harness.state.db                                                     # type: ignore[union-attr]
    assert harness.state.repo.run_row(run.id)["status"] != "completed"         # type: ignore[union-attr]
    detalhes = [r["status_detail"] for r in db.query("SELECT status_detail FROM steps WHERE key='ler' "
                                                     "ORDER BY plan_version")]
    assert detalhes and all(d.startswith(PREFIXO_DADO_AUSENTE) and "teto de 6" in d for d in detalhes)
    eventos = _eventos(harness, run.id)
    assert "verificacoes" not in visto
    assert not any(RELEU in m or TETO_COM_TUDO_LIDO in m for m in eventos)
    assert not any(l.startswith("(executor) 'contagem' já foi lido") for h in visto["historicos"] for l in h)
    assert db.scalar("SELECT COUNT(*) FROM step_outputs") == 0                 # tentativa que falha não entrega nada
