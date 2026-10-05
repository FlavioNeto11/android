"""31.78: a etapa de leitura julgada, sem prova local, não gira mais lendo o mesmo valor até o teto e falhar por "dado
ausente".

Achado real (05/10, r-20261005074912-701173, a prévia do 29.30): a `READ_POSTS_COUNT` leu o "0" da contagem 12 vezes
(nenhum toque), nunca chamou `step_done`, e o teto de 12 decisões da leitura (31.38) a encerrou com "procurei
'posts_antes' … e não encontrei". O valor estava lido desde a 1ª decisão. Agora:
- releitura do mesmo nome com o MESMO valor e nada faltando: na 1ª, o ator é avisado de que é hora de `step_done` (o
  aviso vai só na decisão dele, não aos fatos do juiz); na 2ª, a etapa vai à verificação, que julga a pós-condição
  como depois de um `step_done` (nada sai comprovado sem ela). Outras ações entre as leituras não zeram a conta;
- o teto com tudo lido vai à verificação; com uma saída cujas leituras divergem, falha honesta ("leituras
  divergentes"); com algo faltando, segue `dado_ausente`;
- valor diferente na releitura não é repetição (a tela pode ter mudado);
- etapa com efeito ou com `commit_guard` não entra em nada disso.

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
AVISO = "(executor) 'contagem' já foi lido"
DIVERGENTES = "leu valores divergentes de 'contagem'"


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


def _ator(inner: Any, roteiro: list[str], visto: dict[str, Any]) -> None:
    """O ator da etapa `ler`, em ciclo pelo `roteiro`: "0"/"7" relê a contagem com esse valor, "olhar" observa a tela,
    "fim" conclui. Nunca conclui se o roteiro não tiver "fim"."""
    decide0 = inner.decide

    async def decide(req: Any) -> Any:
        if req.ctx.step_key != "ler":
            return await decide0(req)
        i = visto.setdefault("decisoes", 0)
        visto["decisoes"] = i + 1
        visto.setdefault("historicos", []).append(list(req.history))
        passo = roteiro[i % len(roteiro)]
        if passo == "olhar":
            return Decision(tool="observe_screen", args={"rationale": "olhar", "need_image": False}), Usage()
        if passo == "fim":
            return Decision(tool="step_done", args={"rationale": "pronto", "evidence": "contagem",
                                                    "delivery_level": None}), Usage()
        alvo = next(e for e in req.screen.tree.elements if e.text == passo)
        return Decision(tool="read_value", args={"rationale": "a contagem", "name": "contagem", "element_id": alvo.id,
                                                 "value": None, "value_kind": "number"}), Usage()

    inner.decide = decide


def _verificador(inner: Any, visto: dict[str, Any], veredito: str) -> None:
    """O juiz da pós-condição de `ler`: conta as vezes e guarda os fatos que recebeu. É a verificação que fecha a etapa,
    nunca o laço."""
    verify0 = inner.verify

    async def verify(req: Any) -> Any:
        if req.ctx.step_key != "ler":
            return await verify0(req)
        visto["verificacoes"] = visto.get("verificacoes", 0) + 1
        visto.setdefault("fatos", []).extend(req.facts)
        return Verdict(satisfied=veredito, evidence="[simulado] a contagem"), Usage()

    inner.verify = verify


async def _rodar(h: Harness, roteiro: list[str], *saidas: str, veredito: str = "yes",
                 **etapa: Any) -> tuple[dict[str, Any], Any]:
    h.cfg.file.ai.max_decisoes_leitura = 6
    h.cfg.file.ai.relacao_do_valor = False              # a relação do valor (31.41) tem teste próprio
    _catalogo(*saidas)
    visto: dict[str, Any] = {}
    try:
        _plano(h.ai.inner, PlanStep(key="ler", title="Ler a contagem", goal="g", depends_on=["open_app"],
                                    capability="QA_LER_CONTAGEM", max_attempts=1,
                                    postcondition=Postcondition(kind="model_judged", value="v", description="d"),
                                    **etapa))
        _ator(h.ai.inner, roteiro, visto)
        _verificador(h.ai.inner, visto, veredito)
        run = h.run(["android-01"], command=COMANDO)
        await _fim(h, run.id)
    finally:
        unregister(QA)
    return visto, run


def _eventos(h: Harness, run_id: str) -> list[str]:
    return [r["message"] for r in h.state.db.query("SELECT message FROM events WHERE run_id=? AND kind='decision'",  # type: ignore[union-attr]
                                                   (run_id,))]


def _detalhes(h: Harness) -> list[str]:
    return [r["status_detail"] or "" for r in h.state.db.query(  # type: ignore[union-attr]
        "SELECT status_detail FROM steps WHERE key='ler' ORDER BY plan_version")]


def _avisos(visto: dict[str, Any]) -> list[int]:
    """As decisões (a partir de 0) em que o ator recebeu o aviso de concluir."""
    return [i for i, h in enumerate(visto["historicos"]) if any(l.startswith(AVISO) for l in h)]


@pytest.mark.parametrize("valor", ["0", "7"])
async def test_releitura_do_mesmo_valor_vai_a_verificacao_na_segunda(harness: Harness, contagem: None,
                                                                    valor: str) -> None:
    """O roteiro da r-…-701173 com o "0" e com o "7": o zero não tem nada de especial, o laço é que não tinha saída."""
    visto, run = await _rodar(harness, [valor], "contagem")
    db = harness.state.db                                                     # type: ignore[union-attr]
    assert harness.state.repo.run_row(run.id)["status"] == "completed"         # type: ignore[union-attr]
    assert db.scalar("SELECT value FROM step_outputs WHERE name='contagem'") == valor
    assert visto["decisoes"] == 3 and visto["verificacoes"] == 1  # ler, reler (o aviso), reler (verificação)
    assert _avisos(visto) == [2]
    aviso = next(l for l in visto["historicos"][2] if l.startswith(AVISO))
    assert valor not in aviso.replace("'contagem'", "")                       # o aviso não traz o valor
    assert any(RELEU in m for m in _eventos(harness, run.id))
    assert db.scalar("SELECT COUNT(*) FROM actions WHERE tool='step_done' AND attempt_id LIKE '%:ler:%'") == 0


async def test_o_aviso_ao_ator_nao_vai_aos_fatos_do_juiz(harness: Harness, contagem: None) -> None:
    """C2 da leitura do #413: "a próxima ação é step_done" é instrução ao ator. O juiz recebe os `<fatos_do_executor>`
    como prova de processo, e uma instrução não é fato: ela vai só na cópia da decisão do ator."""
    visto, _run = await _rodar(harness, ["0"], "contagem")
    assert visto["verificacoes"] == 1 and visto["fatos"]
    assert not any("próxima ação" in f or f.startswith(AVISO) for f in visto["fatos"])
    assert any("contagem" in f for f in visto["fatos"])                     # o fato da leitura, sim, vai


async def test_outra_acao_entre_as_leituras_nao_zera_a_conta(harness: Harness, contagem: None) -> None:
    """Ler 0, olhar, ler 0, olhar, ler 0: a 3ª leitura igual vai à verificação. Só um valor diferente zera; quem
    decide se a tela ainda prova é o juiz."""
    visto, run = await _rodar(harness, ["0", "olhar"], "contagem")
    assert harness.state.repo.run_row(run.id)["status"] == "completed"         # type: ignore[union-attr]
    assert visto["decisoes"] == 5 and visto["verificacoes"] == 1
    assert _avisos(visto) == [3, 4]                     # depois da 1ª releitura, em toda decisão até a 2ª


async def test_juiz_que_diz_nao_depois_da_releitura_falha_sem_laco(harness: Harness, contagem: None) -> None:
    """Ir à verificação não é sucesso: com o "não" do juiz e uma tentativa só, a etapa falha "não comprovada", sem
    saída gravada. Cada tentativa recomeça a conta e vai ao juiz uma vez; nada gira até o teto."""
    visto, run = await _rodar(harness, ["0"], "contagem", veredito="no")
    db = harness.state.db                                                     # type: ignore[union-attr]
    assert harness.state.repo.run_row(run.id)["status"] != "completed"         # type: ignore[union-attr]
    assert db.scalar("SELECT COUNT(*) FROM steps WHERE key='ler' AND status='succeeded'") == 0
    assert db.scalar("SELECT COUNT(*) FROM step_outputs") == 0
    detalhes = _detalhes(harness)
    assert detalhes and all(d.startswith("Pós-condição não comprovada") for d in detalhes)
    assert visto["decisoes"] == 3 * len(detalhes) and visto["verificacoes"] == len(detalhes)


async def test_teto_com_tudo_lido_e_leitura_unica_vai_a_verificacao(harness: Harness, contagem: None) -> None:
    """Ler uma vez e só olhar até o teto: nada falta e nada diverge, então o teto vai à verificação em vez de "não
    encontrei"."""
    visto, run = await _rodar(harness, ["0", "olhar", "olhar", "olhar", "olhar", "olhar"], "contagem")
    db = harness.state.db                                                     # type: ignore[union-attr]
    assert harness.state.repo.run_row(run.id)["status"] == "completed"         # type: ignore[union-attr]
    assert visto["decisoes"] == 6 and visto["verificacoes"] == 1
    assert db.scalar("SELECT value FROM step_outputs WHERE name='contagem'") == "0"
    eventos = _eventos(harness, run.id)
    assert any(TETO_COM_TUDO_LIDO in m for m in eventos) and not any(RELEU in m for m in eventos)
    assert not any(d.startswith(PREFIXO_DADO_AUSENTE) for d in _detalhes(harness))


async def test_leituras_divergentes_ate_o_teto_falham_sem_ir_ao_juiz(harness: Harness, contagem: None) -> None:
    """C1 da leitura do #413: 0, 7, 0, 7…: cada leitura troca o valor, então nunca há repetição. No teto, a saída
    diverge: o juiz julgaria só "a contagem foi lida", e o ÚLTIMO valor viraria a base da prova da publicação. Falha
    honesta, com motivo próprio (nem "não encontrei", nem verificação)."""
    visto, run = await _rodar(harness, ["0", "7"], "contagem")
    db = harness.state.db                                                     # type: ignore[union-attr]
    assert harness.state.repo.run_row(run.id)["status"] != "completed"         # type: ignore[union-attr]
    assert "verificacoes" not in visto and _avisos(visto) == []
    assert db.scalar("SELECT COUNT(*) FROM step_outputs") == 0
    detalhes = _detalhes(harness)
    assert detalhes and all(DIVERGENTES in d and not d.startswith(PREFIXO_DADO_AUSENTE) for d in detalhes)
    assert "0" not in detalhes[0].replace("teto de 6", "") and "7" not in detalhes[0]   # o motivo não traz os valores
    tipos = {r["failure_kind"] for r in db.query("SELECT failure_kind FROM steps WHERE key='ler'")}
    assert tipos == {"ia_chamada_invalida"}
    eventos = _eventos(harness, run.id)
    assert not any(RELEU in m or TETO_COM_TUDO_LIDO in m for m in eventos)


async def test_com_outra_saida_faltando_nao_vai_a_verificacao(harness: Harness, contagem: None) -> None:
    """A etapa entrega `contagem` e `outro`; o ator só relê a contagem. Nada de aviso nem de verificação: o teto fecha
    como `dado_ausente`, como no 31.38, e a frase diz o teto."""
    visto, run = await _rodar(harness, ["0"], "contagem", "outro")
    db = harness.state.db                                                     # type: ignore[union-attr]
    assert harness.state.repo.run_row(run.id)["status"] != "completed"         # type: ignore[union-attr]
    detalhes = _detalhes(harness)
    assert detalhes and all(d.startswith(PREFIXO_DADO_AUSENTE) and "teto de 6" in d for d in detalhes)
    eventos = _eventos(harness, run.id)
    assert "verificacoes" not in visto and _avisos(visto) == []
    assert not any(RELEU in m or TETO_COM_TUDO_LIDO in m for m in eventos)
    assert db.scalar("SELECT COUNT(*) FROM step_outputs") == 0                 # tentativa que falha não entrega nada


@pytest.mark.parametrize("etapa", [{"commit_guard": ["Suporte QA"]}, {"side_effect": True}],
                         ids=["commit_guard", "side_effect"])
async def test_etapa_que_age_nao_entra_na_conta_nem_no_teto(harness: Harness, contagem: None,
                                                            etapa: dict[str, Any]) -> None:
    """Etapa com efeito ou com guarda de efeito não é "de leitura": sete releituras iguais (o teto é 6) não a mandam à
    verificação, não geram aviso e não viram "dado ausente"; ela segue até o `step_done` do ator."""
    visto, run = await _rodar(harness, ["0"] * 7 + ["fim"], "contagem", **etapa)
    assert visto["decisoes"] >= 8 and _avisos(visto) == []
    eventos = _eventos(harness, run.id)
    assert not any(RELEU in m or TETO_COM_TUDO_LIDO in m for m in eventos)
    assert not any(d.startswith(PREFIXO_DADO_AUSENTE) or DIVERGENTES in d for d in _detalhes(harness))
