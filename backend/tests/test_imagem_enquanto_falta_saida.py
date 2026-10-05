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
