"""Orçamento PADRÃO do retrieval semântico (J13, ADR-063): o que o J12 e a calibração do J13 mediram vira regra com teste.

`real` 02/10 (`docs/dominios/context-retrieval.md`, "Ablação A × A+B" e "Orçamento padrão novo"), `python-poetry/poetry`:
- etapa A: ~15,7 mil tokens REAIS (o mapa de 34 KB estima 8,5 mil por `len//4`: razão real/estimada **1,84**, o texto do mapa tokeniza pesado);
- etapa B com 16 chunks: 6,2 a 7,1 mil reais; a estimativa `len//4` do payload exato fica entre 0,98 e **1,11** do real (média 1,01).
Com os padrões antigos (8 candidatos, 24 chunks, teto 24.000) a B passava de ~9 mil estimados e o teto por pedido a bloqueava.
`simulated`: aqui só a conta do orçamento e a coerência entre a configuração e os padrões do domínio.
"""
from __future__ import annotations

from app.config import ContextRetrievalCfg
from app.modules.context_retrieval.application.budget import BudgetLedger
from app.modules.context_retrieval.domain.model import Budget, PayloadLimits, ProviderUsage

A_REAL_TOKENS = 15_700            # etapa A medida (15.652 a 15.667)
A_RAZAO_REAL_POR_ESTIMADA = 1.84  # 15.652 reais contra 8.512 estimados no mapa de 34 KB
B_REAL_MAX_TOKENS = 7_100         # etapa B medida com 16 chunks (6.178 a 7.085)
B_RAZAO_MAX = 1.11                # real/estimada da B, pior caso medido
B_ESTIMADA_24_CHUNKS = 9_840      # payload estimado (len//4) da B com 24 chunks no mesmo corpus: o que o teto antigo barrava


def _depois_da_a(budget: Budget, est_b: int, a_real: int = A_REAL_TOKENS):
    rb = BudgetLedger(budget).begin_request()
    assert rb.check_call(est_input_tokens=12_000) is None                       # a A passa
    rb.record(ProviderUsage(input_tokens=a_real, cost_usd=0.00066))
    return rb.check_call(est_input_tokens=est_b)


def test_padroes_da_configuracao_e_do_dominio_sao_os_mesmos() -> None:
    s = ContextRetrievalCfg().semantic
    b, p = Budget(), PayloadLimits()
    assert (s.max_input_tokens, s.max_candidate_files, s.max_chunks, s.max_bytes) == \
        (b.max_input_tokens, p.max_candidate_files, p.max_chunks, p.max_bytes)
    assert (p.max_candidate_files, p.max_chunks, b.max_input_tokens) == (5, 16, 32_000)


def test_a_b_medida_cabe_no_teto_padrao() -> None:
    assert _depois_da_a(Budget(), B_REAL_MAX_TOKENS) is None


def test_o_pior_caso_do_mapa_e_da_b_cabe_com_a_calibracao_medida() -> None:
    """Pior caso: mapa no teto de bytes (a razão real/estimada da A é de 1,84) + B no pior payload medido (razão 1,11 sobre o real
    máximo). É por isso que o teto é 32.000 e não 28.000: com o mapa de 48 KB, 28.000 bloquearia a B."""
    limites = PayloadLimits()
    a_pior = int(limites.max_bytes // 4 * A_RAZAO_REAL_POR_ESTIMADA)             # ~22,1 mil
    b_pior = int(B_REAL_MAX_TOKENS * B_RAZAO_MAX)                                # ~7,9 mil
    assert a_pior + b_pior <= Budget().max_input_tokens
    assert _depois_da_a(Budget(), b_pior, a_real=a_pior) is None
    assert _depois_da_a(Budget(max_input_tokens=28_000), b_pior, a_real=a_pior) is not None   # e 28.000 não bastaria


def test_o_padrao_antigo_barrava_a_b_com_24_chunks() -> None:
    """Regressão do achado do J6/J12: com o teto de 24.000 e a B de 24 chunks, a B era bloqueada (`budget_exceeded`)."""
    assert _depois_da_a(Budget(max_input_tokens=24_000), B_ESTIMADA_24_CHUNKS) is not None
    assert _depois_da_a(Budget(), B_ESTIMADA_24_CHUNKS) is None                  # com o teto novo até 24 chunks cabem neste corpus
