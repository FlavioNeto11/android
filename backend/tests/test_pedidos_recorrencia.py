"""Recorrência e fuso do pedido (28.3): parser do subconjunto da RRULE, geração, hora inexistente e repetida.

Prova `simulated`: módulo puro, relógio injetado (`depois_de`), sem banco nem rede. Os fusos vêm da base IANA do
`tzdata` (declarado em requirements.in); sem base nenhuma os testes pulam com o motivo.
"""
from __future__ import annotations

import time as relogio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.modules.pedidos.domain.recorrencia import (
    ErroRecorrencia, interpretar, localizar, ocorrencias, proxima, proximas,
)

try:
    ZoneInfo("America/Sao_Paulo")
except Exception as e:   # noqa: BLE001 - sem tzdata nem base do sistema não há o que provar
    pytest.skip(f"sem base IANA de fusos (instale tzdata): {e}", allow_module_level=True)

UTC = timezone.utc
SP, NY, LX = "America/Sao_Paulo", "America/New_York", "Europe/Lisbon"
ZERO = datetime(2000, 1, 1, tzinfo=UTC)   # "agora" injetado: antes de tudo


def utc(*a: int) -> datetime:
    return datetime(*a, tzinfo=UTC)


def gerar(regra: str, inicio: datetime, fuso: str, n: int = 5, depois_de: datetime = ZERO):
    return proximas(interpretar(regra), inicio, fuso, depois_de=depois_de, limite=n)


def utcs(insts):
    return [i.utc for i in insts]


# ================================================================== parser
def test_interpretar_aceita_o_subconjunto_e_ignora_ordem_caixa_e_prefixo():
    r = interpretar("RRULE:freq=weekly;byday=we,mo;byhour=9,9,8;byminute=30;interval=2;count=10")
    assert (r.freq, r.intervalo, r.dias_semana, r.horas, r.minutos, r.contagem) == ("WEEKLY", 2, (0, 2), (8, 9), (30,), 10)
    assert interpretar("FREQ=MONTHLY;BYMONTHDAY=-1,15;WKST=MO").dias_mes == (-1, 15)


@pytest.mark.parametrize("texto", [
    "FREQ=DAILY", "FREQ=HOURLY;INTERVAL=6;BYMINUTE=0,30", "FREQ=WEEKLY;BYDAY=MO,TH;BYHOUR=9;COUNT=4",
    "FREQ=MONTHLY;BYMONTHDAY=-1;UNTIL=20271231T235959", "FREQ=DAILY;UNTIL=20261231T120000Z",
])
def test_forma_canonica_ida_e_volta(texto):
    r = interpretar(texto)
    assert interpretar(r.para_texto()) == r
    assert r.para_texto() == interpretar(r.para_texto()).para_texto()


def test_until_so_data_vale_ate_o_fim_do_dia_local():
    assert interpretar("FREQ=DAILY;UNTIL=20261231").ate == datetime(2026, 12, 31, 23, 59, 59)
    assert interpretar("FREQ=DAILY;UNTIL=20261231T100000Z").ate == utc(2026, 12, 31, 10)


@pytest.mark.parametrize("texto,trecho", [
    ("", "vazia"), ("   ", "vazia"),
    ("BYDAY=MO", "FREQ é obrigatório"),
    ("FREQ=SECONDLY", "piso de frequência"), ("FREQ=MINUTELY;INTERVAL=5", "piso de frequência"),
    ("FREQ=YEARLY", "MONTHLY;INTERVAL=12"), ("FREQ=FORTNIGHTLY", "desconhecido"),
    ("FREQ=DAILY;COUNT=3;UNTIL=20261231", "exclusivos"),
    ("FREQ=DAILY;BYSETPOS=1", "BYSETPOS não é suportado"),
    ("FREQ=DAILY;BYMONTH=3", "BYMONTH não é suportado"),
    ("FREQ=DAILY;BYSECOND=5", "BYSECOND não é suportado"),
    ("FREQ=WEEKLY;WKST=SU", "WKST"),
    ("FREQ=MONTHLY;BYDAY=1MO", "ordinal"), ("FREQ=MONTHLY;BYDAY=-1FR", "ordinal"),
    ("FREQ=WEEKLY;BYDAY=XX", "BYDAY"),
    ("FREQ=DAILY;BYHOUR=24", "fora do intervalo"), ("FREQ=DAILY;BYMINUTE=60", "fora do intervalo"),
    ("FREQ=DAILY;BYHOUR=a", "inteiro"), ("FREQ=DAILY;INTERVAL=0", "fora do intervalo"),
    ("FREQ=DAILY;INTERVAL=-1", "inteiro"), ("FREQ=DAILY;COUNT=0", "fora do intervalo"),
    ("FREQ=DAILY;INTERVAL=1001", "fora do intervalo"),
    ("FREQ=MONTHLY;BYMONTHDAY=0", "BYMONTHDAY=0"), ("FREQ=MONTHLY;BYMONTHDAY=32", "fora do intervalo"),
    ("FREQ=WEEKLY;BYMONTHDAY=5", "não vale com FREQ=WEEKLY"),
    ("FREQ=DAILY;FREQ=HOURLY", "mais de uma vez"),
    ("FREQ=DAILY;;BYHOUR=3", "inválida"), ("FREQ=DAILY;BYHOUR", "inválida"),
    ("FREQ=DAILY;UNTIL=2026-12-31", "UNTIL"), ("FREQ=DAILY;UNTIL=20261340", "inexistente"),
    ("DTSTART:20261001T090000\nRRULE:FREQ=DAILY", "DTSTART"),
])
def test_interpretar_recusa_com_mensagem_clara(texto, trecho):
    with pytest.raises(ErroRecorrencia) as e:
        interpretar(texto)
    assert trecho in str(e.value)


def test_interpretar_recusa_tipo_errado():
    with pytest.raises(ErroRecorrencia):
        interpretar(None)  # type: ignore[arg-type]


# ================================================================== geração, sem borda de fuso
def test_diaria_herda_hora_e_minuto_do_inicio_e_vira_o_ano():
    insts = gerar("FREQ=DAILY", datetime(2026, 12, 30, 9, 15), SP, 4)
    assert [i.local.isoformat() for i in insts] == [
        "2026-12-30T09:15:00-03:00", "2026-12-31T09:15:00-03:00", "2027-01-01T09:15:00-03:00", "2027-01-02T09:15:00-03:00"]
    assert utcs(insts)[0] == utc(2026, 12, 30, 12, 15)


def test_diaria_byhour_byminute_expande_em_ordem():
    insts = gerar("FREQ=DAILY;BYHOUR=18,6;BYMINUTE=30,0", datetime(2026, 6, 1, 0, 0), SP, 5)
    assert [(i.nominal.day, i.nominal.hour, i.nominal.minute) for i in insts] == [
        (1, 6, 0), (1, 6, 30), (1, 18, 0), (1, 18, 30), (2, 6, 0)]


def test_diaria_com_intervalo_e_filtro_de_dia_da_semana():
    # a cada 2 dias, só segundas e quintas: 2026-10-05 (seg) é o início; +2d = qua(no), +4d = sex(no), +6d = dom(no)...
    insts = gerar("FREQ=DAILY;INTERVAL=2;BYDAY=MO,TH", datetime(2026, 10, 5, 8, 0), SP, 4)
    assert [i.nominal.date().isoformat() for i in insts] == ["2026-10-05", "2026-10-15", "2026-10-19", "2026-10-29"]


def test_semanal_sem_byday_usa_o_dia_do_inicio_e_atravessa_o_ano():
    insts = gerar("FREQ=WEEKLY", datetime(2026, 12, 23, 7, 0), SP, 3)   # quarta
    assert [i.nominal.date().isoformat() for i in insts] == ["2026-12-23", "2026-12-30", "2027-01-06"]


def test_semanal_byday_inicio_fora_da_regra_comeca_na_proxima_e_semana_comeca_na_segunda():
    # início quarta 07/10/2026; MO,WE a cada 2 semanas (semana começa na segunda, WKST=MO).
    # a segunda 05/10 é anterior ao início; semana 1: qua 07; pula a semana de 12/10; semana de 19/10: seg 19, qua 21.
    insts = gerar("FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,WE;BYHOUR=9", datetime(2026, 10, 7, 9, 0), SP, 5)
    assert [i.nominal.date().isoformat() for i in insts] == ["2026-10-07", "2026-10-19", "2026-10-21", "2026-11-02", "2026-11-04"]
    # DTSTART que não casa não é ocorrência (convenção do dateutil): quinta 08/10 com BYDAY=MO começa na segunda 12/10
    assert gerar("FREQ=WEEKLY;BYDAY=MO", datetime(2026, 10, 8, 9, 0), SP, 1)[0].nominal.date().isoformat() == "2026-10-12"


def test_mensal_dia_inexistente_e_ignorado_e_nao_conta():
    # dia 31: fevereiro, abril e junho não têm; não são "adiados" para o dia 30
    insts = gerar("FREQ=MONTHLY", datetime(2026, 1, 31, 10, 0), SP, 5)
    assert [i.nominal.date().isoformat() for i in insts] == ["2026-01-31", "2026-03-31", "2026-05-31", "2026-07-31", "2026-08-31"]
    assert len(gerar("FREQ=MONTHLY;COUNT=3", datetime(2026, 1, 31, 10, 0), SP, 10)) == 3


def test_mensal_ultimo_dia_com_bymonthday_negativo_e_ano_bissexto():
    insts = gerar("FREQ=MONTHLY;BYMONTHDAY=-1", datetime(2027, 12, 1, 12, 0), SP, 4)
    assert [i.nominal.date().isoformat() for i in insts] == ["2027-12-31", "2028-01-31", "2028-02-29", "2028-03-31"]
    assert gerar("FREQ=MONTHLY;BYMONTHDAY=-1", datetime(2026, 2, 1, 12, 0), SP, 1)[0].nominal.day == 28


def test_mensal_intervalo_virando_ano_e_varios_dias():
    insts = gerar("FREQ=MONTHLY;INTERVAL=2;BYMONTHDAY=1,15", datetime(2026, 11, 1, 6, 0), SP, 5)
    assert [i.nominal.date().isoformat() for i in insts] == ["2026-11-01", "2026-11-15", "2027-01-01", "2027-01-15", "2027-03-01"]


def test_mensal_byday_expande_dias_da_semana_do_mes_e_intersecta_com_bymonthday():
    # todas as sextas de outubro/2026: 2, 9, 16, 23, 30
    sextas = gerar("FREQ=MONTHLY;BYDAY=FR;BYHOUR=8", datetime(2026, 10, 1, 8, 0), SP, 6)
    assert [i.nominal.day for i in sextas] == [2, 9, 16, 23, 30, 6]
    # sexta 13 (BYDAY e BYMONTHDAY juntos): 13/11/2026, 13/08/2027
    assert [i.nominal.date().isoformat() for i in gerar("FREQ=MONTHLY;BYDAY=FR;BYMONTHDAY=13", datetime(2026, 10, 1, 8, 0), SP, 2)] == [
        "2026-11-13", "2027-08-13"]


def test_horaria_intervalo_byminute_e_filtro_byhour():
    insts = gerar("FREQ=HOURLY;INTERVAL=6;BYMINUTE=0,30", datetime(2026, 6, 1, 0, 0), SP, 5)
    assert [(i.nominal.day, i.nominal.hour, i.nominal.minute) for i in insts] == [(1, 0, 0), (1, 0, 30), (1, 6, 0), (1, 6, 30), (1, 12, 0)]
    comercial = gerar("FREQ=HOURLY;BYHOUR=9,12,15", datetime(2026, 6, 1, 8, 0), SP, 4)
    assert [(i.nominal.day, i.nominal.hour) for i in comercial] == [(1, 9), (1, 12), (1, 15), (2, 9)]


def test_count_e_until_e_inicio_inclusivo():
    assert len(gerar("FREQ=DAILY;COUNT=3", datetime(2026, 6, 1, 9, 0), SP, 50)) == 3
    ate_data = gerar("FREQ=DAILY;UNTIL=20260603", datetime(2026, 6, 1, 23, 30), SP, 50)    # o dia 3 inteiro vale
    assert [i.nominal.day for i in ate_data] == [1, 2, 3]
    ate_local = gerar("FREQ=DAILY;UNTIL=20260603T093000", datetime(2026, 6, 1, 9, 30), SP, 50)   # inclusivo na hora exata
    assert [i.nominal.day for i in ate_local] == [1, 2, 3]
    # UNTIL em UTC: 09:30 local de SP (-03) = 12:30Z; 12:29:59Z deixa o dia 3 de fora
    assert len(gerar("FREQ=DAILY;UNTIL=20260603T122959Z", datetime(2026, 6, 1, 9, 30), SP, 50)) == 2
    assert len(gerar("FREQ=DAILY;UNTIL=20260603T123000Z", datetime(2026, 6, 1, 9, 30), SP, 50)) == 3


def test_count_conta_desde_o_inicio_mesmo_com_depois_de_no_meio():
    regra, inicio = "FREQ=DAILY;COUNT=5", datetime(2026, 6, 1, 9, 0)
    # 09:00 de SP = 12:00Z; o dia 3 às 12:00Z é a própria `depois_de` (estrito), então restam o 4 e o 5
    assert [i.nominal.day for i in gerar(regra, inicio, SP, 10, depois_de=utc(2026, 6, 3, 12, 0))] == [4, 5]
    assert gerar(regra, inicio, SP, 10, depois_de=utc(2026, 6, 5, 12, 0)) == []


def test_depois_de_e_estritamente_posterior_e_exige_instante_com_fuso():
    r = interpretar("FREQ=DAILY")
    ini = datetime(2026, 6, 1, 9, 0)
    exata = utc(2026, 6, 2, 12, 0)   # 09:00 de 02/06 em SP
    assert proxima(r, ini, SP, depois_de=exata).nominal.day == 3
    assert proxima(r, ini, SP, depois_de=exata - timedelta(seconds=1)).nominal.day == 2
    with pytest.raises(ErroRecorrencia):
        proximas(r, ini, SP, depois_de=datetime(2026, 6, 1))     # ingênuo: o relógio injetado tem que ter fuso
    with pytest.raises(ErroRecorrencia):
        proximas(r, ini, SP, depois_de=exata, limite=0)


def test_proxima_de_regra_esgotada_e_none():
    assert proxima(interpretar("FREQ=DAILY;COUNT=2"), datetime(2026, 6, 1, 9, 0), SP, depois_de=utc(2026, 7, 1)) is None


def test_regra_que_nunca_casa_nao_trava():
    t0 = relogio.monotonic()
    # de 12 em 12 horas a partir da hora 0 só existem as horas 0 e 12: a 3 nunca aparece
    assert gerar("FREQ=HOURLY;INTERVAL=12;BYHOUR=3", datetime(2026, 6, 1, 0, 0), SP) == []
    # 30 de fevereiro nunca existe
    assert gerar("FREQ=MONTHLY;BYMONTHDAY=30;BYDAY=MO;INTERVAL=12", datetime(2026, 2, 1, 0, 0), SP) == []
    assert relogio.monotonic() - t0 < 20


def test_fim_do_calendario_acaba_sem_estourar():
    for fuso in ("UTC", SP):
        insts = gerar("FREQ=MONTHLY", datetime(9998, 11, 1, 12, 0), fuso, 100)
        assert 13 <= len(insts) <= 14 and insts[0].nominal.year == 9998      # nov/9998 .. dez/9999 (a borda de fuso pode cortar a última)


def test_inicio_e_fuso_invalidos():
    r = interpretar("FREQ=DAILY")
    with pytest.raises(ErroRecorrencia):
        proximas(r, datetime(2026, 6, 1, 9, 0, tzinfo=UTC), SP, depois_de=ZERO)    # DTSTART com fuso embutido
    for ruim in ("Marte/Olimpo", "", "../etc/passwd", "/etc/localtime"):
        with pytest.raises(ErroRecorrencia):
            proximas(r, datetime(2026, 6, 1, 9, 0), ruim, depois_de=ZERO)


# ================================================================== a borda do horário de verão (a prova do 28.3)
def test_sao_paulo_2018_hora_inexistente_executa_no_primeiro_instante_valido_depois_do_salto():
    # 04/11/2018 00:00 -03 → 01:00 -02: o 00:30 não existiu. Diária às 00:30.
    insts = gerar("FREQ=DAILY", datetime(2018, 11, 2, 0, 30), SP, 4)
    assert [i.desviado for i in insts] == [False, False, True, False]
    dia4 = insts[2]
    assert dia4.nominal == datetime(2018, 11, 4, 0, 30)                    # o que o pedido pediu
    assert dia4.utc == utc(2018, 11, 4, 3, 0)                              # a transição: 00:00 -03
    assert dia4.local.isoformat() == "2018-11-04T01:00:00-02:00"           # primeiro instante válido depois do salto
    assert insts[3].utc == utc(2018, 11, 5, 2, 30)                         # no dia seguinte volta ao 00:30 (agora -02)
    assert insts[1].utc == utc(2018, 11, 3, 3, 30)                         # 00:30 -03 ainda
    assert dia4.para_dict()["desviado"] is True


def test_sao_paulo_2019_hora_repetida_executa_uma_vez_na_primeira():
    # 17/02/2019 00:00 -02 → 23:00 -03 de 16/02: a faixa 23:00–23:59 do dia 16 se repete. Diária às 23:30.
    insts = gerar("FREQ=DAILY", datetime(2019, 2, 15, 23, 30), SP, 4)
    assert [i.nominal.day for i in insts] == [15, 16, 17, 18]                 # uma ocorrência por dia, o 16 não duplica
    d16 = insts[1]
    assert d16.repetido and not d16.desviado
    assert d16.local.fold == 0 and d16.local.isoformat() == "2019-02-16T23:30:00-02:00"
    assert d16.utc == utc(2019, 2, 17, 1, 30)                                  # a primeira (-02), não a de 02:30Z
    assert insts[2].utc - d16.utc == timedelta(hours=25)                       # o dia da repetição teve 25 h
    assert not insts[0].repetido and not insts[2].repetido


def test_sao_paulo_hoje_nao_tem_horario_de_verao_e_a_hora_nunca_e_desviada():
    insts = gerar("FREQ=DAILY", datetime(2026, 11, 1, 0, 30), SP, 400, depois_de=ZERO)
    assert not any(i.desviado or i.repetido for i in insts)
    assert {i.local.utcoffset() for i in insts} == {timedelta(hours=-3)}


def test_new_york_primavera_2026_hora_inexistente():
    # 08/03/2026 02:00 EST → 03:00 EDT; transição às 07:00Z. Diária às 02:30.
    insts = gerar("FREQ=DAILY", datetime(2026, 3, 7, 2, 30), NY, 3)
    assert [i.desviado for i in insts] == [False, True, False]
    assert insts[0].utc == utc(2026, 3, 7, 7, 30)                              # 02:30 EST
    assert insts[1].utc == utc(2026, 3, 8, 7, 0)
    assert insts[1].local.isoformat() == "2026-03-08T03:00:00-04:00"
    assert insts[2].utc == utc(2026, 3, 9, 6, 30)                              # 02:30 EDT


def test_new_york_outono_2026_hora_repetida():
    # 01/11/2026 02:00 EDT → 01:00 EST: 01:00–01:59 acontece duas vezes. Diária às 01:30.
    insts = gerar("FREQ=DAILY", datetime(2026, 10, 31, 1, 30), NY, 3)
    d1 = insts[1]
    assert [i.nominal.day for i in insts] == [31, 1, 2]
    assert d1.repetido and d1.utc == utc(2026, 11, 1, 5, 30)                   # a primeira (EDT -04)
    assert d1.local.fold == 0 and d1.local.utcoffset() == timedelta(hours=-4)
    assert not insts[0].repetido and not insts[2].repetido


def test_lisboa_primavera_e_outono_2026():
    # 29/03/2026 01:00 WET → 02:00 WEST (a transição é 01:00Z); 25/10/2026 02:00 WEST → 01:00 WET.
    prim = gerar("FREQ=DAILY", datetime(2026, 3, 28, 1, 30), LX, 3)
    assert [i.desviado for i in prim] == [False, True, False]
    assert prim[1].utc == utc(2026, 3, 29, 1, 0) and prim[1].local.isoformat() == "2026-03-29T02:00:00+01:00"
    out = gerar("FREQ=DAILY", datetime(2026, 10, 24, 1, 30), LX, 3)
    assert out[1].repetido and out[1].utc == utc(2026, 10, 25, 0, 30)          # a primeira (WEST +01)
    assert [i.nominal.day for i in out] == [24, 25, 26]


def test_localizar_direto_hora_comum_nao_marca_nada():
    i = localizar(datetime(2026, 7, 1, 12, 0), NY)
    assert (i.desviado, i.repetido) == (False, False) and i.utc == utc(2026, 7, 1, 16, 0)
    with pytest.raises(ErroRecorrencia):
        localizar(datetime(2026, 7, 1, 12, 0, tzinfo=UTC), NY)


def test_dia_inteiro_pulado_pelo_fuso_apia_2011_desvia_para_depois_do_salto():
    # Pacific/Apia pulou 30/12/2011 inteiro (UTC-10 → UTC+14): a lacuna tem 24 h. Bisecção cobre lacunas grandes.
    i = localizar(datetime(2011, 12, 30, 9, 0), "Pacific/Apia")
    assert i.desviado and i.local.isoformat() == "2011-12-31T00:00:00+14:00"


def test_desvio_conta_no_count_do_pedido():
    # COUNT=3 a partir de 02/11/2018: 02, 03 e o 04 desviado; a hora inexistente NÃO é ignorada (desvio do desenho)
    insts = gerar("FREQ=DAILY;COUNT=3", datetime(2018, 11, 2, 0, 30), SP, 10)
    assert len(insts) == 3 and insts[2].desviado


def test_until_local_na_hora_inexistente_inclui_o_desvio():
    insts = gerar("FREQ=DAILY;UNTIL=20181104T003000", datetime(2018, 11, 2, 0, 30), SP, 10)
    assert len(insts) == 3 and insts[2].desviado


def test_dois_horarios_no_buraco_viram_uma_execucao_so():
    # 02:00 e 03:00 em Nova York no dia do salto: 02:00 desvia para 03:00 EDT e coincide com a de 03:00 → uma só, a real
    insts = gerar("FREQ=DAILY;BYHOUR=2,3", datetime(2026, 3, 7, 2, 0), NY, 6)
    dia8 = [i for i in insts if i.nominal.day == 8]
    assert len(dia8) == 1 and dia8[0].utc == utc(2026, 3, 8, 7, 0) and not dia8[0].desviado
    assert [(i.nominal.day, i.nominal.hour) for i in insts] == [(7, 2), (7, 3), (8, 3), (9, 2), (9, 3), (10, 2)]
    assert len({i.utc for i in insts}) == len(insts)


def test_horaria_atraves_do_salto_da_primavera_sem_buraco_nem_duplicata():
    insts = gerar("FREQ=HOURLY", datetime(2026, 3, 8, 0, 0), NY, 6)
    us = utcs(insts)
    assert us == [utc(2026, 3, 8, 5), utc(2026, 3, 8, 6), utc(2026, 3, 8, 7), utc(2026, 3, 8, 8), utc(2026, 3, 8, 9), utc(2026, 3, 8, 10)]
    assert [i.local.hour for i in insts] == [0, 1, 3, 4, 5, 6]                  # o 02:00 virou o 03:00, uma vez
    assert not insts[2].desviado                                                 # a de 03:00 real prevalece sobre a desviada


def test_horaria_no_fim_do_horario_de_verao_nao_repete_a_hora():
    insts = gerar("FREQ=HOURLY", datetime(2026, 11, 1, 0, 0), NY, 4)
    # 00:00 EDT, 01:00 (a primeira, EDT), 02:00 EST, 03:00 EST: a hora de relógio local não se repete (fold=0)
    assert [i.local.hour for i in insts] == [0, 1, 2, 3]
    assert utcs(insts) == [utc(2026, 11, 1, 4), utc(2026, 11, 1, 5), utc(2026, 11, 1, 7), utc(2026, 11, 1, 8)]
    assert insts[1].repetido


def test_ocorrencias_e_monotona_no_ano_inteiro_de_nova_york():
    anterior = None
    n = 0
    for i in ocorrencias(interpretar("FREQ=DAILY;BYHOUR=1,2,3;BYMINUTE=0,30;UNTIL=20261231"), datetime(2026, 1, 1, 0, 0), NY):
        assert anterior is None or i.utc > anterior
        anterior = i.utc
        n += 1
    assert n > 2000
