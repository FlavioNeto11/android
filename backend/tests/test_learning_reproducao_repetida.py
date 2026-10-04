"""30.43: a reprodução da receita numa execução que repetiu o efeito ganha a linha `invalida` irmã.

O caso que o abriu (P4, 03/10, 6f459c, receita:82): a re-execução de validação abriu o app dentro da conversa, a IA
enviou já na etapa de abertura e a receita enviou de novo na etapa dela; a linha `reproducao:` ficou a favor e o pedido
fechou `feita`. A regra é a do 30.42 (`domain.prova.efeito_repetido`): o 29.58 vence; a regra própria, sobre o diário,
só na execução de validação. Sintético, sem texto de tela nem de pessoa. Nível de prova: `simulated`.
"""
from __future__ import annotations

import json

from app.contracts.origem import PREFIXO_VALIDACAO
from app.modules.learning.domain.validacao import Motivo, motivo_da_prova_invalida
from app.modules.learning.infrastructure.validacoes_sql import FontesDaValidacaoSql
from app.util import now

from .test_learning_evidencia_receita import Mundo, mundo  # noqa: F401  (a fixture `mundo` é a armação da 30.39)


def _envio(m: Mundo, sid: str, *, n: int = 1) -> None:
    """`n` toques de envio concluídos (`is_commit_action`) na última tentativa da etapa `sid`."""
    tentativa = str(m.db.scalar("SELECT id FROM attempts WHERE step_id=? ORDER BY number DESC LIMIT 1", (sid,)))
    for i in range(n):
        m.db.execute("INSERT INTO actions(attempt_id, seq, tool, args, status, intent_at) VALUES (?,?,?,?,?,?)",
                     (tentativa, 10 + i, "tap", json.dumps({"is_commit_action": True}), "done", "2026-10-01T12:00:00Z"))


def _execucao_6f459c(m: Mundo, run_id: str, receita: int, *, chave: str | None) -> None:
    """A abertura (sem efeito, IA) envia; a etapa de envio (com efeito, conduzida pela receita) envia de novo."""
    m.execucao(run_id)
    if chave is not None:
        m.db.execute("UPDATE runs SET idempotency_key=? WHERE id=?", (chave, run_id))
    abertura = m.etapa(run_id, 1, driven_by="ai", receita=None)
    _envio(m, abertura)
    envio = m.etapa(run_id, 2, driven_by="recipe", receita=receita)
    m.db.execute("UPDATE steps SET side_effect=1 WHERE id=?", (envio,))
    _envio(m, envio)


def _invalidas(m: Mundo, receita: int) -> list[dict[str, object]]:
    return [x for x in m.linhas(receita) if x["stance"] == "invalida"]


def _passo(m: Mundo) -> int:
    [passo] = [p for p in m.servico._passos if getattr(p, "nome", "") == "receitas_efeito_repetido"]   # noqa: SLF001
    return int(passo.executar(now()))


def test_validacao_com_dois_envios_deixa_a_invalida_ao_lado_da_reproducao(mundo: Mundo) -> None:  # noqa: F811
    r = mundo.receita()
    _execucao_6f459c(mundo, "run-v", r, chave=f"{PREFIXO_VALIDACAO}lv-1")
    mundo.digerir("run-v")
    posicoes = sorted(str(x["stance"]) for x in mundo.linhas(r))
    assert posicoes == ["for", "invalida"]                     # a linha a favor fica (o log só cresce)
    [inv] = _invalidas(mundo, r)
    assert inv["origin_ref"] == "reproducao:run-v" and inv["run_id"] == "run-v"
    assert str(inv["detail"]).startswith("invalida:efeito_repetido — o efeito saiu 2 vezes")
    # o pedido de validação da receita fecha pela `invalida` (o ramo do 30.42), não `feita`
    fontes = FontesDaValidacaoSql(mundo.db, precos=dict, fluxo_ativo_para=lambda c: False, vetado=lambda e: False)
    detalhe = fontes.invalida_da_execucao(f"receita:{r}", "run-v")
    assert motivo_da_prova_invalida(detalhe) is Motivo.EFEITO_REPETIDO


def test_validacao_sem_repeticao_nao_ganha_invalida(mundo: Mundo) -> None:  # noqa: F811
    r = mundo.receita()
    mundo.execucao("run-v")
    mundo.db.execute("UPDATE runs SET idempotency_key=? WHERE id='run-v'", (f"{PREFIXO_VALIDACAO}lv-1",))
    envio = mundo.etapa("run-v", 1, driven_by="recipe", receita=r)
    mundo.db.execute("UPDATE steps SET side_effect=1 WHERE id=?", (envio,))
    _envio(mundo, envio)
    mundo.digerir("run-v")
    assert [x["stance"] for x in mundo.linhas(r)] == ["for"]


def test_na_execucao_organica_so_o_29_58_conta(mundo: Mundo) -> None:  # noqa: F811
    r = mundo.receita()
    _execucao_6f459c(mundo, "run-o", r, chave=None)            # o mesmo diário, sem ser validação
    mundo.digerir("run-o")
    assert _invalidas(mundo, r) == []                          # a regra própria não vale fora da validação
    mundo.db.execute("UPDATE steps SET result=? WHERE run_id='run-o' AND seq=2",
                     (json.dumps({"verified": False, "efeito_repetido": {"copias": 3, "fonte": "verificador"}}),))
    assert _passo(mundo) == 1                                  # o 29.58 vale em qualquer execução
    [inv] = _invalidas(mundo, r)
    assert "o efeito saiu 3 vezes" in str(inv["detail"])


def test_o_passo_reclassifica_por_regra_e_e_idempotente(mundo: Mundo) -> None:  # noqa: F811
    """O 6f459c: a linha `for` gravada antes do 30.43 ganha a irmã pela regra (não por lista), uma vez só."""
    r = mundo.receita()
    _execucao_6f459c(mundo, "run-v", r, chave="k-antes")
    mundo.digerir("run-v")                                     # sem a marca de validação ainda: nada
    assert _invalidas(mundo, r) == []
    mundo.db.execute("UPDATE runs SET idempotency_key=? WHERE id='run-v'", (f"{PREFIXO_VALIDACAO}lv-1",))
    assert _passo(mundo) == 1
    assert _passo(mundo) == 0
    assert len(_invalidas(mundo, r)) == 1
