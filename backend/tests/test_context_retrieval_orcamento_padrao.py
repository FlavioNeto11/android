"""Orçamento PADRÃO do retrieval semântico (J13, ADR-063): o que a ablação do J12 mediu vira regra com teste.

`real` 02/10 (`docs/dominios/context-retrieval.md`, "Ablação A × A+B por região"): no `python-poetry/poetry`, a etapa A
gasta ~15,7 mil tokens e a B, com 5 candidatos e 16 chunks, 6,2 a 7,1 mil (maior pedido: 22.748). Com os padrões antigos
(8 candidatos, 24 chunks, teto 24.000) a B passava de ~9 mil estimados e o teto por pedido a bloqueava.
`simulated`: aqui só a conta do orçamento e a coerência entre a configuração e os padrões do domínio.
"""
from __future__ import annotations

from app.config import ContextRetrievalCfg
from app.modules.context_retrieval.application.budget import BudgetLedger
from app.modules.context_retrieval.domain.model import Budget, PayloadLimits, ProviderUsage

A_REAL_TOKENS = 15_700          # etapa A medida (15.652 a 15.667)
B_REAL_MAX_TOKENS = 7_100       # etapa B medida com 16 chunks (6.178 a 7.085)
B_ESTIMADA_24_CHUNKS = 9_840    # payload estimado (len//4) da B com 24 chunks no mesmo corpus: o que o teto antigo barrava


def _depois_da_a(budget: Budget, est_b: int):
    rb = BudgetLedger(budget).begin_request()
    assert rb.check_call(est_input_tokens=12_000) is None                       # a A passa
    rb.record(ProviderUsage(input_tokens=A_REAL_TOKENS, cost_usd=0.00066))
    return rb.check_call(est_input_tokens=est_b)


def test_padroes_da_configuracao_e_do_dominio_sao_os_mesmos() -> None:
    s = ContextRetrievalCfg().semantic
    b, p = Budget(), PayloadLimits()
    assert (s.max_input_tokens, s.max_candidate_files, s.max_chunks, s.max_bytes) == \
        (b.max_input_tokens, p.max_candidate_files, p.max_chunks, p.max_bytes)
    assert (p.max_candidate_files, p.max_chunks, b.max_input_tokens) == (5, 16, 28_000)


def test_a_b_medida_cabe_no_teto_padrao() -> None:
    assert _depois_da_a(Budget(), B_REAL_MAX_TOKENS) is None


def test_ate_a_b_no_teto_de_bytes_cabe_depois_da_a() -> None:
    """O pior payload possível da B é o `max_bytes`: A medida + B no teto de bytes (len//4) fica abaixo do teto de tokens, então
    nenhum payload dentro dos limites padrão é barrado só por aritmética de orçamento."""
    pior_b = PayloadLimits().max_bytes // 4
    assert A_REAL_TOKENS + pior_b <= Budget().max_input_tokens
    assert _depois_da_a(Budget(), pior_b) is None


def test_o_padrao_antigo_barrava_a_b_e_o_teto_antigo_com_24_chunks_continuaria_barrando() -> None:
    """Regressão do achado do J6/J12: com o teto de 24.000 e a B de 24 chunks, a B era bloqueada (`budget_exceeded`)."""
    assert _depois_da_a(Budget(max_input_tokens=24_000), B_ESTIMADA_24_CHUNKS) is not None
    assert _depois_da_a(Budget(max_input_tokens=24_000), B_REAL_MAX_TOKENS) is None     # com 16 chunks já cabia nos 24.000
