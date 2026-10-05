"""Item 31.71: com a chave ligada, a imagem vai em toda decisão enquanto faltar saída declarada (`leitura_pendente`),
com um lembrete só com os NOMES do que falta.

Achado real (r-20261004232524-2e0775, ai_calls 4499 a 4510): depois do 1º toque, a 2ª decisão sem imagem
(`arvore_rica`) tentou concluir e levou recusa; depois do 1º `read_value`, a decisão seguinte saiu sem imagem e o ator
pediu `observe_screen`. Foram 4 dos 8 decides sem avançar a leitura. Por que o ator, já com a imagem, também não leu (o
4501 e o 4502) segue UNKNOWN: a leitura estática refutou "o prompt não avisa".

A chave nasce desligada (como a do 31.56) e só vira padrão com o A/B (`.claude/handoffs/jev-roteiro-31-71-ab.md`).
A política manda: `sensivel` e `politica_nunca` vêm antes, e `leitura_pendente` não força imagem que ela veta.

Nível de prova: `simulated` (executor do harness na porta 5640; nenhuma IA). Nada real.
"""
from __future__ import annotations

from app.automation.hierarchy import parse_hierarchy
from app.config import AiCfg, LeituraVisualCfg
from app.planning.provider import MotivoDaImagem
from app.taskqueue.executor import _IMAGEM_VAI, compress_history, historico_do_ator, lembrete_da_leitura

from .conftest import Harness

RICA = parse_hierarchy('<hierarchy rotation="0">' + "".join(
    f'<node index="{i}" text="item {i}" resource-id="" class="android.widget.TextView" package="p" content-desc=""'
    f' clickable="true" enabled="true" bounds="[0,{i * 90}][720,{i * 90 + 80}]" />' for i in range(12)) + "</hierarchy>")
SENSIVEL = parse_hierarchy(
    '<hierarchy rotation="0"><node index="0" text="" resource-id="" class="android.widget.EditText" package="p" '
    'content-desc="" password="true" enabled="true" bounds="[0,0][720,100]" /></hierarchy>')


def _motivo(harness: Harness, *, ligada: bool, falta_saida: bool, first: bool = False, tree=RICA,
            policy: str = "auto", trouble: bool = False, requested: bool = False) -> MotivoDaImagem:
    executor = harness.state.scheduler.executor                 # type: ignore[union-attr]
    ai = AiCfg(image_policy=policy, imagem_enquanto_falta_saida=ligada)
    return executor._motivo_da_imagem(tree, judged_step=False, first=first, trouble=trouble, requested=requested,
                                      ai=ai, le_valor=True, falta_saida=falta_saida)


def test_chave_desligada_nada_muda(harness: Harness) -> None:
    assert AiCfg().imagem_enquanto_falta_saida is False                     # nasce desligada
    assert _motivo(harness, ligada=False, falta_saida=True) == "arvore_rica"   # o 4500 e o 4506 de hoje
    assert _motivo(harness, ligada=False, falta_saida=True, first=True) == "primeira_da_leitura"


def test_chave_ligada_manda_a_imagem_enquanto_falta_saida(harness: Harness) -> None:
    assert _motivo(harness, ligada=True, falta_saida=True) == "leitura_pendente"
    assert "leitura_pendente" in _IMAGEM_VAI


def test_lida_a_ultima_saida_o_motivo_some_na_decisao_seguinte(harness: Harness) -> None:
    """O lado que discrimina o 4510: sem saída faltando, a árvore rica volta a ir sem imagem."""
    assert _motivo(harness, ligada=True, falta_saida=False) == "arvore_rica"
    assert "arvore_rica" not in _IMAGEM_VAI


def test_os_motivos_anteriores_continuam_com_o_mesmo_nome(harness: Harness) -> None:
    """`leitura_pendente` entra DEPOIS: o que já é gravado hoje em `ai_calls.image_reason` não muda de nome."""
    assert _motivo(harness, ligada=True, falta_saida=True, first=True) == "primeira_da_leitura"
    assert _motivo(harness, ligada=True, falta_saida=True, trouble=True) == "problema"
    assert _motivo(harness, ligada=True, falta_saida=True, requested=True) == "pedida"


def test_a_politica_manda_sobre_a_leitura_pendente(harness: Harness) -> None:
    assert _motivo(harness, ligada=True, falta_saida=True, policy="never") == "politica_nunca"
    assert "politica_nunca" not in _IMAGEM_VAI
    assert _motivo(harness, ligada=True, falta_saida=True, tree=SENSIVEL) == "sensivel"
    assert "sensivel" not in _IMAGEM_VAI


def test_o_lembrete_tem_o_texto_exato_e_so_os_nomes() -> None:
    assert lembrete_da_leitura(["assunto"], visual=True) == (
        "(executor) a imagem desta observação está anexada; para 'assunto', use read_value(source='visual') na linha "
        "que o mostra.")
    assert lembrete_da_leitura(["remetente", "assunto"], visual=True) == (
        "(executor) a imagem desta observação está anexada; para 'remetente', 'assunto', use "
        "read_value(source='visual') na linha que o mostra.")
    # Sem a leitura visual ligada, o lembrete não oferece o caminho que o executor recusaria.
    assert lembrete_da_leitura(["assunto"], visual=False) == (
        "(executor) a imagem desta observação está anexada; para 'assunto', use read_value na linha que o mostra.")


def test_o_lembrete_vai_so_na_copia_da_decisao_e_nao_no_historico_comprimido() -> None:
    """O histórico durável não ganha uma linha por decisão: o `compress_history` guarda toda linha `(executor)`, e um
    lembrete gravado nele se acumularia. A cópia da decisão o tem uma vez, no fim."""
    history = ["(executor) esta etapa entrega às seguintes o(s) valor(es) 'assunto'", "tap(e53) → ok"]
    antes = list(history)
    lembrete = lembrete_da_leitura(["assunto"], visual=True)
    for _ in range(3):                                          # três decisões seguidas
        copia = historico_do_ator(history, 6, lembrete)
        assert copia.count(lembrete) == 1 and copia[-1] == lembrete
    assert history == antes                                     # o durável não mudou
    assert lembrete not in compress_history(history, 6)
    assert historico_do_ator(history, 6, None) == compress_history(history, 6)   # sem lembrete: o de hoje


def test_a_leitura_visual_e_a_chave_sao_independentes() -> None:
    """A chave do 31.71 não liga a leitura visual (ADR-070) nem depende dela; só muda o texto do lembrete."""
    ai = AiCfg(imagem_enquanto_falta_saida=True)
    assert ai.leitura_visual == LeituraVisualCfg() and ai.leitura_visual.enabled is False


# ------------------------------------------------- A2 da leitura do #379: a fiação pelo laço do executor, com o harness
# O roteiro é o da r-…-2e0775 (o de `test_leitura_sem_step_done`): observar, `step_done` recusado, observar, `step_done`
# recusado, ler o remetente, observar, ler o assunto, `step_done`. Do índice 0 ao 6 falta saída; do 7 em diante, não.
import sys  # noqa: E402
from typing import Any  # noqa: E402

from app.planning.provider import Decision, Usage  # noqa: E402

from .test_leitura_sem_step_done import _plano, _roteiro, caixa  # noqa: E402,F401 - `caixa` é fixture

ULTIMA_COM_SAIDA_FALTANDO = 6                            # a decisão que lê o assunto


async def _pelo_laco(harness: Harness, ligada: bool) -> tuple[list[dict[str, Any]], list[str]]:
    harness.cfg.file.ai.relacao_do_valor = False
    harness.cfg.file.ai.imagem_enquanto_falta_saida = ligada
    harness.cfg.file.ai.image_policy = "auto"                 # o harness manda sempre; o 31.71 é regra do `auto`
    # A árvore do app de teste é pobre, e a imagem iria por `arvore_pobre` de qualquer jeito: com o mínimo em 1 ela
    # conta como rica, e só o 31.71 manda a imagem (sem isso, tirar o `falta_saida=` da observação não reprovaria).
    harness.cfg.file.ai.rich_tree_min_elements = 1
    visto: dict[str, Any] = {}
    roteiro = _roteiro(harness, visto)
    decisoes: list[dict[str, Any]] = []

    async def decide(req: Any) -> tuple[Decision, Usage]:
        if req.ctx.step_key == "ler_caixa":
            decisoes.append({"imagem": req.screen.jpeg is not None,
                             "lembretes": [h for h in req.history if h.startswith("(executor) a imagem desta observação")]})
        return await roteiro(req)

    harness.ai.inner.plan, harness.ai.inner.decide = _plano(harness.ai.inner), decide
    run = harness.run(["android-01"])
    final = await harness.wait_run(run.id, timeout=90)
    assert final.status == "completed"
    db = harness.state.db                                                     # type: ignore[union-attr]
    motivos = [r["image_reason"] for r in db.query(
        "SELECT image_reason FROM ai_calls WHERE role = 'decide' AND attempt_id LIKE ? ORDER BY id",
        ("%:ler_caixa:%",))]
    return decisoes, motivos


async def test_pelo_laco_a_chave_ligada_manda_imagem_e_lembrete_em_toda_decisao_com_saida_faltando(
        harness: Harness, caixa: None) -> None:
    # A observação já sai COM a imagem (o `falta_saida=` do pedido de observação): a decisão nunca precisa completá-la
    # depois. Sem isso, os testes passariam igual, com uma segunda aquisição de imagem por decisão (leitura do #379).
    devices = harness.state.devices                                           # type: ignore[union-attr]
    original, chamadores = devices.completar_imagem, []

    def contado(*a: Any, **k: Any) -> Any:
        chamadores.append(sys._getframe(1).f_code.co_name)
        return original(*a, **k)

    devices.completar_imagem = contado
    try:
        decisoes, motivos = await _pelo_laco(harness, ligada=True)
    finally:
        devices.completar_imagem = original
    assert chamadores.count("_run_step") == 0, chamadores
    assert len(decisoes) > ULTIMA_COM_SAIDA_FALTANDO
    # a 2ª decisão (depois do `observe_screen`, antes de qualquer recusa) vai por `leitura_pendente`, com a imagem
    assert motivos[1] == "leitura_pendente" and decisoes[1]["imagem"]
    for i, d in enumerate(decisoes):
        if i <= ULTIMA_COM_SAIDA_FALTANDO:
            # imagem e lembrete juntos em TODA decisão com saída faltando, inclusive a de motivo `problema` (A1);
            # o lembrete aparece UMA vez: não acumula no histórico durável
            assert d["imagem"] and len(d["lembretes"]) == 1, (i, d)
            assert "QA-0" not in d["lembretes"][0]                # só nomes, nunca o valor lido
        else:
            assert not d["lembretes"], (i, d)                    # lidas as duas, o lembrete some
    assert "problema" in motivos                                 # houve decisão logo depois da recusa, e ela levou o lembrete


async def test_pelo_laco_a_chave_desligada_nao_muda_nada(harness: Harness, caixa: None) -> None:
    decisoes, motivos = await _pelo_laco(harness, ligada=False)
    assert "leitura_pendente" not in motivos
    assert all(not d["lembretes"] for d in decisoes)
    assert motivos[1] == "arvore_rica", motivos
    assert not decisoes[1]["imagem"]                             # a regra de antes: árvore rica vai sem imagem
