"""30.42: o passo de curadoria `ReclassificacaoDoEfeitoDuplicado` e o veredito gravado pela mineração.

Sobre o banco migrado e o `Mundo` do D1 (como `test_learning_prova`): a execução de prova é assentada à mão, com o
diário de ações; o digest grava a linha `invalida` pelo contrato; o passo reclassifica o `for` antigo (a ev:48) sem
UPDATE. Nível de prova: `simulated`.
"""
from __future__ import annotations

import json
from app.modules.learning.application.nativos import ReclassificacaoDoEfeitoDuplicado, marca_do_conteudo
from app.modules.learning.domain.promocao import efetivas
from app.modules.learning.domain.prova import MotivoDaInvalida, motivo_da_invalida
from app.modules.learning.domain.vocabulario import Posicao
from app.modules.learning.infrastructure import linhas
from app.modules.learning.infrastructure.ligar_nativos import LeituraSql
from app.modules.skills.domain.document import content_hash
from app.util import now, now_iso

from .test_d1_fluxos import Mundo
from .test_learning_prova import (_candidato, _digerir, _evidencias, _prova, mundo)  # noqa: F401

TOQUE_DE_ENVIO = json.dumps({"text": "", "desc": "", "resource_id": "com.exemplo:id/send_button"})


def _acao(mundo: Mundo, run_id: str, chave: str, tool: str, *, args: str = "{}", alvo: str | None = None,
          status: str = "done", seq: int = 1) -> None:
    mundo.db.execute("INSERT INTO actions(attempt_id, seq, tool, args, status, target, intent_at) VALUES (?,?,?,?,?,?,?)",
                     (f"{run_id}:{chave}:a1", seq, tool, args, status, alvo, now_iso()))


def _com_envio_duplo(mundo: Mundo, run_id: str, flow_id: str) -> None:
    """A `5f2de5` sintética: toque de cara de envio na abertura (etapa sem efeito) e o envio da etapa de efeito."""
    _prova(mundo, run_id, flow_id, [("abrir_perfil", "succeeded"), ("seguir", "succeeded"), ("ver", "succeeded")],
           agiu=False)
    mundo.db.execute("UPDATE steps SET side_effect=1 WHERE id=?", (f"{run_id}:seguir",))
    _acao(mundo, run_id, "abrir_perfil", "tap", alvo=TOQUE_DE_ENVIO)
    _acao(mundo, run_id, "seguir", "tap", args=json.dumps({"is_commit_action": True}))
    _acao(mundo, run_id, "ver", "step_done")


def test_a_mineracao_grava_a_invalida_com_a_marca_e_o_motivo_e_nao_o_for(mundo: Mundo) -> None:
    flow_id = _candidato(mundo)
    _com_envio_duplo(mundo, "p-dup", flow_id)
    _digerir(mundo, "p-dup")
    [(stance, origem, detalhe)] = _evidencias(mundo, flow_id, "p-dup")
    assert (stance, origem) == ("invalida", "run:p-dup")
    assert detalhe.startswith(marca_do_conteudo(_hash(mundo, flow_id)) + " invalida:efeito_repetido")
    assert motivo_da_invalida(detalhe) is MotivoDaInvalida.EFEITO_REPETIDO
    _digerir(mundo, "p-dup")                                           # idempotente
    assert len(_evidencias(mundo, flow_id, "p-dup")) == 1


def _hash(mundo: Mundo, flow_id: str) -> str:
    bruto = mundo.db.scalar("SELECT plan FROM flows WHERE id=?", (flow_id,))
    return content_hash(linhas.json_legado(str(bruto)))


def _passo(mundo: Mundo) -> ReclassificacaoDoEfeitoDuplicado:
    return ReclassificacaoDoEfeitoDuplicado(mundo.repo, LeituraSql(mundo.db))


def test_o_passo_reclassifica_o_for_antigo_e_e_idempotente(mundo: Mundo) -> None:
    flow_id = _candidato(mundo)
    # o `for` de antes da regra (a ev:48): grava a linha como a mineração antiga faria
    _com_envio_duplo(mundo, "p-48", flow_id)
    marca = marca_do_conteudo(_hash(mundo, flow_id))
    mundo.db.execute(
        "INSERT INTO learning_evidence(item_ref, stance, origin_ref, run_id, instance_id, simulated, detail, observed_at)"
        " VALUES (?,?,?,?,?,?,?,?)", (f"fluxo:{flow_id}", "for", "run:p-48", "p-48", "android-01", 0,
                                       f"{marca} prova: 3/3 etapas comprovadas", now_iso()))
    passo = _passo(mundo)
    assert passo.executar(now()) == 1
    assert [s for s, *_ in _evidencias(mundo, flow_id, "p-48")] == ["for", "invalida"]
    assert passo.executar(now()) == 0                                  # idempotente: a irmã já existe
    # a linha `for` segue no log (nada de UPDATE), e `efetivas` a tira das contagens
    linhas_ = mundo.repo.evidencias(f"fluxo:{flow_id}")              
    ev = [e for e in linhas_ if e.origin_ref == "run:p-48"]
    assert {e.stance for e in ev} == {Posicao.FOR, Posicao.INVALIDA}
    assert {e.stance for e in efetivas(ev)} == {Posicao.INVALIDA}
    # o SQL dos leitores diz o mesmo que o domínio: o `for` reclassificado não é a favor efetivo
    assert mundo.db.scalar("SELECT COUNT(*) FROM learning_evidence e WHERE e.item_ref=? AND e.origin_ref='run:p-48'"
                           f" AND {linhas.favor_efetivo('e')}", (f"fluxo:{flow_id}",)) == 0
    [(_, _, detalhe)] = [x for x in _evidencias(mundo, flow_id, "p-48") if x[0] == "invalida"]
    assert detalhe.startswith(marca + " invalida:efeito_repetido")


def test_o_for_de_execucao_comum_e_o_de_prova_limpa_nao_sao_tocados(mundo: Mundo) -> None:
    flow_id = _candidato(mundo)                                        # a execução comum que ensinou o fluxo: `for`
    _prova(mundo, "p-ok", flow_id, [("abrir_perfil", "succeeded"), ("seguir", "succeeded"), ("ver", "succeeded")])
    _digerir(mundo, "p-ok")
    antes = [s for s, *_ in _evidencias(mundo, flow_id)]
    assert "for" in antes and "invalida" not in antes
    assert _passo(mundo).executar(now()) == 0
    assert [s for s, *_ in _evidencias(mundo, flow_id)] == antes


def test_contra_com_invalida_irma_nao_e_recomparado_pela_forma(mundo: Mundo) -> None:
    flow_id = _candidato(mundo)
    marca = marca_do_conteudo(_hash(mundo, flow_id))
    for stance, det in (("against", f"{marca} divergiu"), ("invalida", f"{marca} invalida:efeito_repetido — x")):
        mundo.db.execute(
            "INSERT INTO learning_evidence(item_ref, stance, origin_ref, run_id, simulated, detail, observed_at)"
            " VALUES (?,?,?,?,?,?,?)", (f"fluxo:{flow_id}", stance, "run:r-x", "r-x", 0, det, now_iso()))
    assert LeituraSql(mundo.db).contra_de_fluxos() == []
