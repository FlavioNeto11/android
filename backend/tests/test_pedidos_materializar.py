"""Item 28.4 — materialização pura: janela, coalescência, origem e o segundo cheio (docs/design/pedidos-laco.md §2.2).

Prova `simulated`: sem banco, sem relógio de parede. O `agora` é um `datetime` escrito no teste.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.modules.pedidos.domain.chave import formatar_instante
from app.modules.pedidos.domain.materializar import (JANELA_PADRAO_S, janela_padrao_s, materializar,
                                                     periodo_nominal_s)
from app.modules.pedidos.domain.recorrencia import interpretar, proximas

UTC = timezone.utc
AGORA = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)
TICK = 15


def _mat(instantes, *, agora=AGORA, janela=1800, coalescer=True):
    return materializar(instantes, agora=agora, janela_s=janela, coalescer=coalescer, tick_s=TICK)


def _antes(s: float) -> datetime:
    return AGORA - timedelta(seconds=s)


# ----------------------------------------------------------------------------------------------- janela padrão
def test_periodo_nominal_e_janela_padrao_por_tipo() -> None:
    assert periodo_nominal_s("HOURLY", 3) == 3 * 3600
    assert periodo_nominal_s("DAILY") == 86_400
    # recorrência de um dia ou mais: o padrão; menor: metade do período (HOURLY INTERVAL=n → n × 1800 s)
    assert janela_padrao_s("recorrencia", autonomia="agir", periodo_s=86_400) == JANELA_PADRAO_S
    assert janela_padrao_s("recorrencia", autonomia="agir", periodo_s=periodo_nominal_s("WEEKLY")) == JANELA_PADRAO_S
    assert janela_padrao_s("recorrencia", autonomia="observar", periodo_s=3600) == 1800
    assert janela_padrao_s("recorrencia", autonomia="observar", periodo_s=3 * 3600) == 3 * 1800
    # horario com efeito externo não se repete depois; sem efeito e `agora` usam o padrão configurável (D7)
    assert janela_padrao_s("horario", autonomia="agir") == 0
    assert janela_padrao_s("horario", autonomia="preparar") == JANELA_PADRAO_S
    assert janela_padrao_s("horario", autonomia="observar", padrao_s=600) == 600
    assert janela_padrao_s("agora", autonomia="agir") == JANELA_PADRAO_S


# ----------------------------------------------------------------------------------------------- estados de nascimento
def test_instante_futuro_nasce_prevista_e_o_do_segundo_exato_ja_e_devido() -> None:
    futuro, exato = AGORA + timedelta(seconds=1), AGORA
    d = {x.instante: x for x in _mat([futuro, exato])}
    assert d[futuro].estado == "prevista" and d[futuro].origem == "agenda" and d[futuro].motivo is None
    assert d[exato].estado == "devida" and d[exato].origem == "agenda"


def test_a5_no_mesmo_segundo_o_instante_cheio_ja_e_devido_com_o_relogio_em_meio_segundo() -> None:
    """A5: `previsto_para` é `…:00Z` e o relógio do esquema é `…:00.500Z`. Sem truncar, `12:00:00 <= 12:00:00.500` falha
    como TEXTO (`Z` > `.`) e daria `prevista`; o laço compara em segundos cheios."""
    meio_segundo = AGORA.replace(microsecond=500_000)
    [d] = _mat([AGORA], agora=meio_segundo)
    assert d.estado == "devida"
    assert formatar_instante(meio_segundo) == formatar_instante(AGORA)
    # e a fração não adianta o que ainda não chegou: 12:00:01 segue prevista com o relógio em 12:00:00.999
    [f] = _mat([AGORA + timedelta(seconds=1)], agora=AGORA.replace(microsecond=999_000))
    assert f.estado == "prevista"


def test_fora_da_janela_nasce_perdida_com_o_motivo_e_nunca_some() -> None:
    [d] = _mat([_antes(1801)], janela=1800)
    assert d.estado == "perdida" and d.origem == "recuperacao"
    assert d.motivo == "fora da janela de recuperação: atraso de 1801s > 1800s"
    [no_limite] = _mat([_antes(1800)], janela=1800)
    assert no_limite.estado == "devida", "o atraso IGUAL à janela ainda cabe nela"


def test_janela_zero_so_aceita_o_segundo_exato() -> None:
    assert _mat([AGORA], janela=0)[0].estado == "devida"
    assert _mat([_antes(1)], janela=0)[0].estado == "perdida"


# ----------------------------------------------------------------------------------------------- coalescência
def test_coalescer_deixa_so_a_mais_recente_devida_e_registra_as_puladas() -> None:
    instantes = [_antes(3 * 600), _antes(2 * 600), _antes(600), _antes(60)]
    d = _mat(instantes, janela=3600)
    assert [x.estado for x in d] == ["pulada", "pulada", "pulada", "devida"]
    assert d[0].motivo == f"coalescida na de {formatar_instante(_antes(60))}"
    assert d[-1].motivo is None


def test_sem_coalescer_todas_as_candidatas_nascem_devidas() -> None:
    d = _mat([_antes(600), _antes(300), _antes(60)], janela=3600, coalescer=False)
    assert [x.estado for x in d] == ["devida"] * 3


def test_perdida_e_prevista_nao_entram_na_coalescencia() -> None:
    instantes = [_antes(7200), _antes(600), _antes(60), AGORA + timedelta(hours=1)]
    d = _mat(instantes, janela=3600)
    assert [x.estado for x in d] == ["perdida", "pulada", "devida", "prevista"]


# ----------------------------------------------------------------------------------------------- origem
def test_origem_agenda_dentro_de_duas_voltas_e_recuperacao_depois() -> None:
    d = {x.instante: x.origem for x in _mat([_antes(2 * TICK), _antes(2 * TICK + 1)], janela=3600, coalescer=False)}
    assert d[_antes(2 * TICK)] == "agenda"
    assert d[_antes(2 * TICK + 1)] == "recuperacao"


# ----------------------------------------------------------------------------------------------- entrada
def test_entrada_desordenada_e_repetida_sai_ordenada_e_sem_duplicata() -> None:
    a, b = _antes(120), _antes(60)
    d = _mat([b, a, b], janela=3600, coalescer=False)
    assert [x.instante for x in d] == [a, b]


def test_uma_queda_de_horas_numa_recorrencia_horaria_vira_perdidas_e_uma_recuperacao() -> None:
    """O cenário do desenho (§2.2 regra 8, reinício depois de 3 h): HOURLY, laço fora do ar de 09:00 a 12:10."""
    regra = interpretar("FREQ=HOURLY")
    inicio = datetime(2026, 10, 2, 8, 0, 0)
    agora = datetime(2026, 10, 2, 12, 10, 0, tzinfo=UTC)
    achados = proximas(regra, inicio, "UTC", depois_de=datetime(2026, 10, 2, 8, 59, 59, tzinfo=UTC), limite=10)
    janela = janela_padrao_s("recorrencia", autonomia="agir", periodo_s=periodo_nominal_s("HOURLY"))
    d = materializar([a.utc for a in achados if a.utc <= agora], agora=agora, janela_s=janela, coalescer=True,
                     tick_s=TICK)
    assert [(x.instante.hour, x.estado) for x in d] == [(9, "perdida"), (10, "perdida"), (11, "perdida"),
                                                        (12, "devida")]
    assert d[-1].origem == "recuperacao"      # 10 min de atraso > duas voltas
