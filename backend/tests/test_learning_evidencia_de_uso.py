"""30.51: a execução comum que USA o fluxo ativo deixa evidência a favor e contra pelas etapas, como a prova faz.

Até aqui (o resto do K-086), a execução que casava com o fluxo ativo (`runs.flow_id`) não deixava nada no livro: o uso
de um fluxo publicado só contava pela sombra de outras execuções. O que se prova:
- origem própria: a linha é `uso:<run_id>`, distinta da prova e da sombra (`run:<run_id>`); o texto diz "uso:"; minerar
  de novo não duplica, e a execução que já tem `for`/`against` do item por outra origem não conta duas vezes;
- o que não conta contra: ensaio (`ensaio:`), lote de teste (`lote:`) e execução com cancelamento pedido. O lote que
  passou ainda conta a favor; infra e o ator que não agiu não deixam nada;
- o D-5 enxerga o uso: a janela do resultado posterior lê só o uso real (nem a prova, nem o simulado), e o rótulo de
  saúde do fluxo tem as falhas seguidas pelo uso, com os limiares do dono (2 seguidas degradam; uma falha de uso não
  é contestação).

Armação: o `mundo` de `test_learning_prova` (banco migrado, fluxo nascido ativo de uma execução real) e execuções
assentadas à mão. Nível de prova: `simulated`.
"""
from __future__ import annotations

import pytest

from app.modules.learning.domain.promocao import ORIGEM_DO_USO, falhas_seguidas_no_fim
from app.modules.learning.domain.resultado_posterior import saude_reprovada
from app.modules.learning.domain.saude import CodigoDoMotivo, LimiaresDeSaude, Rotulo
from app.modules.learning.domain.vocabulario import LivroKind
from app.modules.learning.infrastructure.resultado_posterior_sql import ResultadoPosteriorSql

from .test_learning_prova import Mundo, _digerir, _evidencias, _fluxo_unico, _prova, mundo  # noqa: F401


def _ativo(mundo: Mundo) -> str:  # noqa: F811
    mundo.config["com_prova"] = False                                    # nasce ativo (o modo anterior)
    mundo.roda("r-1", "@nasa")
    flow_id = _fluxo_unico(mundo)
    assert mundo.status(flow_id) == "active"
    return flow_id


def _uso(mundo: Mundo, run_id: str, flow_id: str, etapas: list[tuple[str, str]], *, chave: str | None = None,  # noqa: F811
         cancelada: bool = False, **kw: object) -> None:
    """Uma execução COMUM que usou o fluxo: a da prova (`_prova`), sem `prova_fluxo_id` e com `flow_id`."""
    _prova(mundo, run_id, flow_id, etapas, **kw)  # type: ignore[arg-type]
    mundo.db.execute("UPDATE runs SET prova_fluxo_id=NULL, flow_id=?, idempotency_key=?, cancel_requested=? WHERE id=?",
                     (flow_id, chave or f"k-{run_id}", int(cancelada), run_id))


# ------------------------------------------------------------------ origem própria, uma vez
def test_o_uso_comprovado_deixa_um_for_de_origem_propria_uma_vez(mundo: Mundo) -> None:  # noqa: F811
    flow_id = _ativo(mundo)
    _uso(mundo, "u-1", flow_id, [("abrir_perfil", "succeeded"), ("seguir", "succeeded")])
    _digerir(mundo, "u-1")
    [(stance, origem, detalhe)] = _evidencias(mundo, flow_id, "u-1")
    assert (stance, origem) == ("for", f"{ORIGEM_DO_USO}u-1")
    assert detalhe.endswith(" uso: 2/2 etapas comprovadas")
    _digerir(mundo, "u-1")                                               # minerar de novo não duplica
    assert len(_evidencias(mundo, flow_id, "u-1")) == 1
    assert mundo.status(flow_id) == "active"                            # nenhum D1 no ativo
    assert all(run != "u-1" for (_, _, _, run) in mundo.trilha(flow_id))


def test_o_uso_reprovado_deixa_um_contra(mundo: Mundo) -> None:  # noqa: F811
    flow_id = _ativo(mundo)
    _uso(mundo, "u-2", flow_id, [("abrir_perfil", "succeeded"), ("seguir", "failed")], status="failed")
    _digerir(mundo, "u-2")
    [(stance, origem, detalhe)] = _evidencias(mundo, flow_id, "u-2")
    assert (stance, origem) == ("against", f"{ORIGEM_DO_USO}u-2") and "uso: etapa 2 (seguir) reprovada" in detalhe


def test_a_execucao_que_ja_tem_evidencia_do_item_nao_conta_duas_vezes(mundo: Mundo) -> None:  # noqa: F811
    flow_id = _ativo(mundo)
    _uso(mundo, "u-3", flow_id, [("abrir_perfil", "succeeded")])
    mundo.db.execute("INSERT INTO learning_evidence(item_ref, stance, origin_ref, run_id, simulated, detail, observed_at)"
                     " VALUES (?,'for','run:u-3','u-3',0,'outra origem','2026-10-04T12:00:00Z')", (f"fluxo:{flow_id}",))
    _digerir(mundo, "u-3")
    assert [o for (_, o, _) in _evidencias(mundo, flow_id, "u-3")] == ["run:u-3"]


# ------------------------------------------------------------------ o que não conta contra
@pytest.mark.parametrize(("chave", "cancelada"), [("ensaio:qa-01", False), ("lote:aprendizado:30.51", False),
                                                  (None, True)])
def test_ensaio_lote_e_cancelada_nao_contam_contra(mundo: Mundo, chave: str | None, cancelada: bool) -> None:  # noqa: F811
    flow_id = _ativo(mundo)
    _uso(mundo, "u-4", flow_id, [("abrir_perfil", "succeeded"), ("seguir", "failed")], status="failed", chave=chave,
         cancelada=cancelada)
    _digerir(mundo, "u-4")
    assert _evidencias(mundo, flow_id, "u-4") == []


def test_o_lote_que_passou_conta_a_favor(mundo: Mundo) -> None:  # noqa: F811
    flow_id = _ativo(mundo)
    _uso(mundo, "u-5", flow_id, [("abrir_perfil", "succeeded")], chave="lote:aprendizado:30.51-a")
    _digerir(mundo, "u-5")
    assert [s for (s, _, _) in _evidencias(mundo, flow_id, "u-5")] == ["for"]


@pytest.mark.parametrize("cenario", ["infra", "ator_sem_acao"])
def test_infra_e_ator_sem_acao_nao_deixam_nada(mundo: Mundo, cenario: str) -> None:  # noqa: F811
    flow_id = _ativo(mundo)
    etapas = [("abrir_perfil", "succeeded"), ("seguir", "failed")]
    if cenario == "infra":
        _uso(mundo, "u-6", flow_id, etapas, status="failed", erro={"seguir": "device"})
    else:
        _uso(mundo, "u-6", flow_id, etapas, status="failed", agiu=False)
    _digerir(mundo, "u-6")
    assert _evidencias(mundo, flow_id, "u-6") == []


# ------------------------------------------------------------------ o D-5 enxerga o uso
def test_a_janela_do_d5_le_so_o_uso_real_do_fluxo(mundo: Mundo) -> None:  # noqa: F811
    flow_id = _ativo(mundo)
    ref = f"fluxo:{flow_id}"
    for i, (stance, origem, simulada) in enumerate([("for", "uso:a", 0), ("against", "uso:b", 0), ("against", "uso:c", 0),
                                                    ("against", "run:d", 0), ("against", "uso:e", 1)], start=1):
        mundo.db.execute("INSERT INTO learning_evidence(item_ref, stance, origin_ref, run_id, simulated, detail,"
                         " observed_at) VALUES (?,?,?,?,?,'x',?)",
                         (ref, stance, origem, origem.split(":")[1], simulada, f"2026-10-04T10:0{i}:00Z"))
    usos = ResultadoPosteriorSql(mundo.db).usos(ref, "fluxo", "2026-10-04T00:00:00Z", "2026-10-04T23:59:59Z")
    assert usos == [True, False, False]                                  # nem a sombra `run:` nem o simulado
    assert saude_reprovada(usos, LimiaresDeSaude())                      # 2 falhas seguidas: o limiar do dono


def test_o_rotulo_do_fluxo_degrada_por_falhas_de_uso_e_uma_falha_nao_e_contestacao(mundo: Mundo) -> None:  # noqa: F811
    flow_id = _ativo(mundo)
    assert falhas_seguidas_no_fim([True, False, False]) == 2 and falhas_seguidas_no_fim([False, True]) == 0
    _uso(mundo, "u-7", flow_id, [("abrir_perfil", "succeeded")])
    _uso(mundo, "u-8", flow_id, [("abrir_perfil", "succeeded"), ("seguir", "failed")], status="failed")
    for run in ("u-7", "u-8"):
        _digerir(mundo, run)

    def saude() -> tuple[Rotulo, set[CodigoDoMotivo]]:
        e = mundo.servico.entrada(LivroKind.FLUXO, flow_id)
        s = mundo.servico.saudes([e])[e.trail_ref]
        return s.rotulo, {m.codigo for m in s.motivos}

    rotulo, motivos = saude()                                            # uma falha de uso: não é contestação
    assert rotulo is not Rotulo.DEGRADANDO
    assert CodigoDoMotivo.CONTESTADO_RECENTEMENTE not in motivos and CodigoDoMotivo.FALHAS_SEGUIDAS not in motivos
    _uso(mundo, "u-9", flow_id, [("abrir_perfil", "succeeded"), ("seguir", "failed")], status="failed")
    _digerir(mundo, "u-9")
    rotulo, motivos = saude()                                            # duas seguidas: o limiar do D-5
    assert rotulo is Rotulo.DEGRADANDO and CodigoDoMotivo.FALHAS_SEGUIDAS in motivos
