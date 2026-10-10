"""31.323 (P-044, ADR-089): a imagem da tela sensível vai à decisão de rotina, pela mesma régua das outras telas.

Antes, `StepExecutor._motivo_da_imagem` devolvia `sensivel` (sem imagem) para toda árvore sensível, antes de olhar a
política: no login e na verificação a IA decidia às cegas (a prévia e o juiz já levavam a imagem desde o ADR-089).
Agora a tela sensível não tem motivo próprio: `ai.image_policy = never` ainda vale para ela, como para qualquer tela,
e fora disso decide o resto da régua (árvore pobre, 1ª decisão julgada, problema, pedida…). O campo de senha vai
mascarado pelo Android; o valor da credencial segue só pelo canal sensível. A leitura visual de valor continua
recusando a árvore sensível (`saidas.ler_valor_visual`, travado em `test_aviso_de_tela_adr089.py`).

Nível de prova: `simulated` (executor do harness na porta 5640; nenhuma IA). `real`: `not_run`.
"""
from __future__ import annotations

import itertools

import pytest

from app.automation.hierarchy import parse_hierarchy
from app.config import AiCfg
from app.planning.provider import MOTIVOS_DA_IMAGEM
from app.taskqueue.executor import _IMAGEM_VAI

from .conftest import Harness

#: A tela de senha típica: um campo só (árvore pobre) e a classificação sensível.
SENHA = parse_hierarchy(
    '<hierarchy rotation="0"><node index="0" text="" resource-id="" class="android.widget.EditText" package="p" '
    'content-desc="" password="true" enabled="true" bounds="[0,0][720,100]" /></hierarchy>')
#: A mesma classificação com árvore rica (muita coisa na tela, uma delas o campo de senha).
SENHA_RICA = parse_hierarchy('<hierarchy rotation="0">' + "".join(
    f'<node index="{i}" text="item {i}" resource-id="" class="android.widget.TextView" package="p" content-desc=""'
    f' clickable="true" enabled="true" bounds="[0,{i * 90}][720,{i * 90 + 80}]" />' for i in range(12))
    + '<node index="99" text="" resource-id="" class="android.widget.EditText" package="p" content-desc="" '
      'password="true" enabled="true" bounds="[0,1100][720,1200]" /></hierarchy>')


def _motivo(h: Harness, tree, *, policy: str = "auto", **kw) -> str:
    executor = h.state.scheduler.executor                      # type: ignore[union-attr]
    base = dict(judged_step=False, first=False, trouble=False, requested=False)
    return executor._motivo_da_imagem(tree, **{**base, **kw}, ai=AiCfg(image_policy=policy))


def test_as_arvores_do_teste_sao_mesmo_sensiveis() -> None:
    assert SENHA.sensitive and SENHA_RICA.sensitive


def test_a_tela_de_senha_pobre_leva_a_imagem_como_qualquer_arvore_pobre(harness: Harness) -> None:
    assert _motivo(harness, SENHA) == "arvore_pobre" and "arvore_pobre" in _IMAGEM_VAI


def test_a_tela_sensivel_rica_segue_sem_imagem_pela_regra_geral(harness: Harness) -> None:
    assert _motivo(harness, SENHA_RICA) == "arvore_rica" and "arvore_rica" not in _IMAGEM_VAI


@pytest.mark.parametrize(("pedido", "motivo"), [
    ({"requested": True}, "pedida"), ({"trouble": True}, "problema"),
    ({"first": True, "judged_step": True}, "primeira_julgada"), ({"first": True, "le_valor": True}, "primeira_da_leitura"),
    ({"alvo_fora": True}, "alvo_fora_da_arvore"),
])
def test_os_motivos_da_regra_valem_na_tela_sensivel(harness: Harness, pedido: dict, motivo: str) -> None:
    assert _motivo(harness, SENHA_RICA, **pedido) == motivo and motivo in _IMAGEM_VAI


def test_a_politica_vale_para_a_tela_sensivel_como_para_as_outras(harness: Harness) -> None:
    assert _motivo(harness, SENHA, policy="always") == "politica_sempre" and "politica_sempre" in _IMAGEM_VAI
    assert _motivo(harness, SENHA, policy="never") == "politica_nunca" and "politica_nunca" not in _IMAGEM_VAI
    assert _motivo(harness, SENHA_RICA, policy="never", requested=True) == "politica_nunca"


def test_a_decisao_de_rotina_nunca_mais_produz_o_motivo_sensivel(harness: Harness) -> None:
    for tree, policy, requested, trouble, first, judged, valor in itertools.product(
            (SENHA, SENHA_RICA), ("auto", "never", "always"), (False, True), (False, True), (False, True),
            (False, True), (False, True)):
        assert _motivo(harness, tree, policy=policy, requested=requested, trouble=trouble, first=first,
                       judged_step=judged, le_valor=valor) != "sensivel"
    # as linhas antigas de `ai_calls` seguem válidas: o rótulo continua no vocabulário, mas fora do conjunto que leva imagem
    assert "sensivel" in MOTIVOS_DA_IMAGEM and "sensivel" not in _IMAGEM_VAI
