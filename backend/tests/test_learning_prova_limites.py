"""30.42 (o despachante e o fechamento da validação de fluxo): o limite de `MAXIMO_DE_PROVAS` provas (4 desde o 29.75) por versão do conteúdo em 7 dias
(`limite_de_provas`), o aparelho NOVO quando a falta pede reprodução noutro aparelho (`sem_aparelho_novo`) e o
fechamento do pedido pela linha `invalida` que a execução de prova deixou (`efeito_repetido`, `ponto_de_partida`,
`ator_sem_acao`). Banco migrado (SQLite, ou PostgreSQL com `TEST_DATABASE_URL`), parque e fila falsos. Nível de prova:
`simulated`."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.db import Database
from app.modules.learning.application.validacao import ServicoDeValidacao
from app.modules.learning.domain.curador import Decisao, Falta, Parecer
from app.modules.learning.domain.prova import MotivoDaInvalida, detalhe_da_invalida
from app.modules.learning.domain.validacao import (AparelhoCandidato, Grupo, ProvaAnterior, conta_para_o_limite,
                                                   MAXIMO_DE_PROVAS, escolher_aparelho, excluidos_da_validacao,
                                                   limite_de_provas_atingido,
                                                   marca_da_evidencia, motivo_da_prova_invalida, sobra_aparelho_novo)
from app.modules.learning.infrastructure.validacoes_sql import FontesDaValidacaoSql, RegistroDeValidacoesSql
from app.modules.skills.domain.document import content_hash
from app.util import to_iso

from .test_learning_prova_validacao import Mundo, ParqueDeProva, _fluxo, mundo  # noqa: F401 (fixture)
from .test_learning_validacao_sql import B, PEDE, PLANO_DO_FLUXO, _ap, _linha

AGORA = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
MARCA = content_hash(json.loads(PLANO_DO_FLUXO))[:12]          # a versão do conteúdo do fluxo de teste, como a evidência
OUTRA = "aaaaaaaaaaaa"
SEM_LINHA = "sem_linha"                                         # a prova que não deixou linha de evidência
SEM_MARCA = "sem_marca"                                         # a prova cuja linha não tem a marca do conteúdo
SO_EXECUCAO_REAL = Parecer(decisao=Decisao.PEDIR_EVIDENCIA, evidencias_citadas=("item",),
                           falta=(Falta.EXECUCAO_REAL,))


# ------------------------------------------------------------------ a regra pura (domínio)
def _prova(dias: float, *marcas: str | None, aparelho: str = "android-02") -> ProvaAnterior:
    return ProvaAnterior(aparelho=aparelho, quando=AGORA - timedelta(days=dias), marcas=marcas)


def test_a_regra_pura_conta_as_provas_da_mesma_versao_na_janela_de_7_dias() -> None:
    quase = [_prova(1 + i, MARCA) for i in range(MAXIMO_DE_PROVAS - 1)]      # uma a menos que o limite
    assert not limite_de_provas_atingido([], MARCA, AGORA)
    assert not limite_de_provas_atingido(quase, MARCA, AGORA)
    assert limite_de_provas_atingido([*quase, _prova(6.9, MARCA)], MARCA, AGORA)
    assert not limite_de_provas_atingido([*quase, _prova(7.1, MARCA)], MARCA, AGORA)     # fora da janela
    assert not limite_de_provas_atingido([*quase, _prova(2, OUTRA)], MARCA, AGORA)       # outra versão
    # a marca compara pelos 12 primeiros caracteres (o `detail` grava `content_hash[:12]`)
    assert limite_de_provas_atingido([*quase, _prova(2, MARCA)], MARCA + "ffff", AGORA)


def test_a_prova_sem_linha_ou_sem_marca_conta_e_sem_marca_de_agora_tudo_conta() -> None:
    assert conta_para_o_limite(_prova(1), MARCA)                    # sem linha: conta (lado seguro)
    assert conta_para_o_limite(_prova(1, None), MARCA)              # linha sem marca: conta
    assert conta_para_o_limite(_prova(1, OUTRA, None), MARCA)       # uma das linhas sem marca: conta
    assert not conta_para_o_limite(_prova(1, OUTRA, "bbbbbbbbbbbb"), MARCA)
    assert conta_para_o_limite(_prova(1, OUTRA), None)              # o fluxo sem plano legível: nada a comparar
    assert marca_da_evidencia(f"[{MARCA}] etapa 1") == MARCA
    assert marca_da_evidencia("etapa 1") is None and marca_da_evidencia(None) is None
    assert marca_da_evidencia("[abc] etapa 1") is None              # curta demais para ser marca de conteúdo


def test_os_excluidos_so_somam_as_provas_com_reproducao_em_outro_aparelho() -> None:
    com = (Falta.EXECUCAO_REAL.value, Falta.REPRODUCAO_EM_OUTRO_APARELHO.value)
    assert excluidos_da_validacao(com, origem="android-01", provas=["android-02", None, "android-03"]) == frozenset(
        {"android-01", "android-02", "android-03"})
    assert excluidos_da_validacao((Falta.EXECUCAO_REAL.value,), origem="android-01",
                                  provas=["android-02"]) == frozenset({"android-01"})
    assert excluidos_da_validacao((), origem=None, provas=[]) == frozenset()


def test_escolher_aparelho_aceita_um_conjunto_de_excluidos() -> None:
    parque = [_ap("android-01"), _ap("android-02"), _ap("android-03")]
    assert escolher_aparelho(Grupo.QA, parque, excluido={"android-01", "android-02"}) == "android-03"
    assert escolher_aparelho(Grupo.QA, parque, excluido=frozenset({"android-01", "android-02", "android-03"})) is None
    assert escolher_aparelho(Grupo.QA, parque, excluido="android-01") == "android-02"           # o aparelho só, como antes
    assert escolher_aparelho(Grupo.QA, parque, excluido=None) == "android-01"


def test_sobra_aparelho_novo_olha_o_que_serve_mesmo_desligado_ou_ocupado() -> None:
    usados = {"android-01", "android-02"}
    assert not sobra_aparelho_novo(Grupo.QA, [_ap("android-01"), _ap("android-02", online=False)], usados)
    assert sobra_aparelho_novo(Grupo.QA, [_ap("android-02"), _ap("android-03", online=False)], usados)   # desligado
    assert sobra_aparelho_novo(Grupo.QA, [_ap("android-02"), _ap("android-03", ocioso=False)], usados)   # ocupado
    assert not sobra_aparelho_novo(Grupo.QA, [_ap("android-02"), _ap("android-03", conta_real=True)], usados)
    assert not sobra_aparelho_novo(Grupo.QA, [_ap("android-02"), _ap("android-03", tem_o_app=False)], usados)
    assert sobra_aparelho_novo(Grupo.QA, [], usados)                # nenhum serve ainda: espera, não é "sem novo"


def test_o_motivo_da_invalida_e_o_dela_e_o_ilegivel_fecha_sem_evidencia() -> None:
    for m in MotivoDaInvalida:
        assert motivo_da_prova_invalida(detalhe_da_invalida(m, "etapa 2", marca=MARCA)).value == m.value
    for ruim in (None, "", "lixo", f"[{MARCA}] invalida:codigo_que_nao_existe", f"[{MARCA}] invalida:"):
        assert motivo_da_prova_invalida(ruim).value == "sem_evidencia"


# ------------------------------------------------------------------ o que o banco guarda de provas anteriores
_n = 0


def _no_limite(db: Database, quantas: int = MAXIMO_DE_PROVAS, *, primeiro: int = 2, **kw: str) -> None:
    """`quantas` provas anteriores do `f-qa`, da versão de agora e dentro da janela, cada uma num aparelho."""
    for i in range(quantas):
        _anterior(db, "f-qa", f"android-{primeiro + i:02d}", 1 + i * 0.5, **kw)  # type: ignore[arg-type]


def _anterior(db: Database, fid: str, aparelho: str, dias: float, marca: str | None = MARCA, *,
              estado: str = "feita", com_prova: bool = True, stance: str = "for") -> str:
    """Uma execução de prova que o item já teve: o pedido fechado (com aparelho e execução), a execução com
    `prova_fluxo_id` e, salvo `SEM_LINHA`, a linha de evidência com a marca (`None` ou `SEM_MARCA`: linha sem marca)."""
    global _n
    _n += 1
    run_id = f"r-20260926120000-{_n:06x}"
    quando = to_iso(AGORA - timedelta(days=dias))
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at,"
               " prova_fluxo_id) VALUES (?,?,?,?,?,?,?,?,?)",
               (run_id, f"k-{run_id}", "cmd", "execute", "completed", 0, json.dumps([aparelho]), quando,
                fid if com_prova else None))
    db.execute("INSERT INTO learning_validations(id, created_at, updated_at, review_id, item_ref, item_kind, grupo,"
               " falta, comando, estado, run_id, aparelho, expira_em, feito_em) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (f"lv-ant{_n:04d}", quando, quando, "lr-0", f"fluxo:{fid}", "fluxo", "qa", "[]", "cmd", estado, run_id,
                aparelho, quando, quando))
    if marca != SEM_LINHA:
        detalhe = "etapa 1" if marca in (None, SEM_MARCA) else f"[{marca}] etapa 1"
        db.execute("INSERT INTO learning_evidence(item_ref, stance, origin_ref, run_id, instance_id, simulated, detail,"
                   " observed_at) VALUES (?,?,?,?,?,0,?,?)",
                   (f"fluxo:{fid}", stance, f"run:{run_id}", run_id, aparelho, detalhe, quando))
    return run_id


def test_provas_do_item_traz_aparelho_quando_e_marcas_so_das_execucoes_de_prova(mundo: Mundo) -> None:  # noqa: F811
    db, _servico, _parque, _relogio, _ = mundo
    _fluxo(db)
    registro = RegistroDeValidacoesSql(db)
    fontes = FontesDaValidacaoSql(db, precos=lambda: {}, fluxo_ativo_para=lambda c: True, vetado=lambda e: False)
    assert fontes.provas_do_item("fluxo:f-qa") == [] and registro.pendentes() == []
    _anterior(db, "f-qa", "android-02", 3)
    _anterior(db, "f-qa", "android-03", 1, OUTRA)
    _anterior(db, "f-qa", "android-04", 1, SEM_LINHA)
    _anterior(db, "f-qa", "android-05", 1, com_prova=False)           # execução comum: não é prova
    provas = fontes.provas_do_item("fluxo:f-qa")
    assert [(p.aparelho, p.marcas) for p in provas] == [("android-02", (MARCA,)), ("android-03", (OUTRA,)),
                                                        ("android-04", ())]
    assert provas[0].quando == AGORA - timedelta(days=3)
    assert fontes.versao_do_conteudo("fluxo:f-qa") == MARCA
    assert fontes.versao_do_conteudo("receita:78") is None and fontes.versao_do_conteudo("fluxo:nao-existe") is None
    assert fontes.provas_do_item("receita:78") == []


# ------------------------------------------------------------------ o limite de provas ao despachar
def _pede(db: Database, servico: ServicoDeValidacao, parecer: Parecer = PEDE, fid: str = "f-qa") -> str:
    pid = servico.ao_parecer(_fluxo(db, fid), "lr-1", parecer, B)
    assert pid is not None and _linha(db, pid)["estado"] == "pendente"
    return pid


def test_a_prova_alem_do_limite_fecha_limite_de_provas_sem_enfileirar(mundo: Mundo) -> None:  # noqa: F811
    db, servico, parque, _relogio, _ = mundo
    pid = _pede(db, servico)
    _no_limite(db)
    assert servico.uma_volta(lambda: 1) is None
    linha = _linha(db, pid)
    assert (linha["estado"], linha["motivo"], linha["run_id"]) == ("recusada", "limite_de_provas", None)
    assert parque.enfileiradas == []                                  # sem gastar
    assert pid not in [c.id for c in servico.chegadas()]              # e não volta ao curador


def test_a_segunda_prova_ainda_roda_e_a_fora_da_janela_nao_conta(mundo: Mundo) -> None:  # noqa: F811
    db, servico, parque, _relogio, _ = mundo
    pid = _pede(db, servico)
    _anterior(db, "f-qa", "android-02", 1)
    _anterior(db, "f-qa", "android-03", 8)                            # de 8 dias atrás: fora da janela
    assert servico.uma_volta(lambda: 1) is not None
    assert _linha(db, pid)["estado"] == "rodando" and len(parque.enfileiradas) == 1


def test_a_versao_nova_do_conteudo_reabre_a_contagem(mundo: Mundo) -> None:  # noqa: F811
    db, servico, parque, _relogio, _ = mundo
    pid = _pede(db, servico)
    _anterior(db, "f-qa", "android-02", 3)
    _anterior(db, "f-qa", "android-03", 1)
    # o plano muda (a prova refutada que renasce com outro conteúdo): a marca de agora já não é a das provas
    novo = json.loads(PLANO_DO_FLUXO) | {"summary": "Entrega QA v2"}
    db.execute("UPDATE flows SET plan=? WHERE id='f-qa'", (json.dumps(novo),))
    assert servico.uma_volta(lambda: 1) is not None
    assert _linha(db, pid)["estado"] == "rodando" and len(parque.enfileiradas) == 1


def test_as_provas_de_outra_marca_nao_contam_mesmo_sendo_duas(mundo: Mundo) -> None:  # noqa: F811
    db, servico, parque, _relogio, _ = mundo
    pid = _pede(db, servico)
    _anterior(db, "f-qa", "android-02", 3, OUTRA)
    _anterior(db, "f-qa", "android-03", 1, "bbbbbbbbbbbb")
    assert servico.uma_volta(lambda: 1) is not None and _linha(db, pid)["estado"] == "rodando"
    assert len(parque.enfileiradas) == 1


@pytest.mark.parametrize("sem", [SEM_LINHA, SEM_MARCA, None])
def test_a_prova_sem_marca_conta_para_o_limite(mundo: Mundo, sem: str | None) -> None:  # noqa: F811
    """Sem linha de evidência, ou com linha sem a marca do conteúdo, a prova conta (lado seguro): uma assim e uma da
    versão de agora já completam o limite."""
    db, servico, parque, _relogio, _ = mundo
    pid = _pede(db, servico)
    _anterior(db, "f-qa", "android-02", 3, sem)
    _no_limite(db, MAXIMO_DE_PROVAS - 1, primeiro=3)
    assert servico.uma_volta(lambda: 1) is None
    assert _linha(db, pid)["motivo"] == "limite_de_provas" and parque.enfileiradas == []


def test_o_limite_so_vale_para_o_fluxo_e_olha_so_o_item(mundo: Mundo) -> None:  # noqa: F811
    db, servico, parque, _relogio, _ = mundo
    pid = _pede(db, servico, fid="f-outro")
    _anterior(db, "f-qa", "android-02", 3)                            # as provas são de OUTRO fluxo
    _anterior(db, "f-qa", "android-03", 1)
    assert servico.uma_volta(lambda: 1) is not None and _linha(db, pid)["estado"] == "rodando"


# ------------------------------------------------------------------ o aparelho novo
def test_com_reproducao_em_outro_aparelho_e_todos_usados_fecha_sem_aparelho_novo(mundo: Mundo) -> None:  # noqa: F811
    db, servico, parque, _relogio, _ = mundo
    pid = _pede(db, servico)                                          # a origem é o android-01
    _anterior(db, "f-qa", "android-02", 3, OUTRA)                     # versões antigas: o limite não entra
    _anterior(db, "f-qa", "android-03", 1, OUTRA)
    parque.lista = [_ap("android-01"), _ap("android-02"), _ap("android-03", online=False)]
    assert servico.uma_volta(lambda: 1) is None
    linha = _linha(db, pid)
    assert (linha["estado"], linha["motivo"], linha["run_id"]) == ("recusada", "sem_aparelho_novo", None)
    assert parque.enfileiradas == []


def test_o_aparelho_novo_ocupado_ou_fora_do_ar_faz_o_pedido_esperar(mundo: Mundo) -> None:  # noqa: F811
    db, servico, parque, _relogio, _ = mundo
    pid = _pede(db, servico)
    _anterior(db, "f-qa", "android-02", 3, OUTRA)
    for novo in (_ap("android-04", ocioso=False), _ap("android-04", online=False)):
        parque.lista = [_ap("android-01"), _ap("android-02"), novo]
        assert servico.uma_volta(lambda: 1) is None
        assert _linha(db, pid)["estado"] == "pendente" and parque.enfileiradas == []     # espera, como sempre
    parque.lista = [_ap("android-01"), _ap("android-02"), _ap("android-04")]             # e o novo ocioso roda
    assert servico.uma_volta(lambda: 1) is not None and _linha(db, pid)["aparelho"] == "android-04"


def test_com_conta_real_ou_sem_o_app_o_aparelho_nao_serve_e_nao_conta_como_novo(mundo: Mundo) -> None:  # noqa: F811
    db, servico, parque, _relogio, _ = mundo
    pid = _pede(db, servico)
    _anterior(db, "f-qa", "android-02", 3, OUTRA)
    parque.lista = [_ap("android-02"), _ap("android-05", conta_real=True), _ap("android-06", tem_o_app=False)]
    assert servico.uma_volta(lambda: 1) is None
    assert _linha(db, pid)["motivo"] == "sem_aparelho_novo"


def test_a_prova_roda_no_aparelho_novo_e_pula_os_ja_usados(mundo: Mundo) -> None:  # noqa: F811
    db, servico, parque, _relogio, _ = mundo
    pid = _pede(db, servico)
    _anterior(db, "f-qa", "android-02", 3, OUTRA)
    parque.lista = [_ap("android-01"), _ap("android-02"), _ap("android-03"), _ap("android-04")]
    assert servico.uma_volta(lambda: 1) is not None and _linha(db, pid)["aparelho"] == "android-03"


def test_sem_reproducao_em_outro_aparelho_so_a_origem_e_excluida(mundo: Mundo) -> None:  # noqa: F811
    """O pedido que não pede outro aparelho repete o já usado (só a origem fica de fora, como antes do 30.42), e a falta
    de aparelho que não é de origem não fecha `sem_aparelho_novo`."""
    db, servico, parque, _relogio, _ = mundo
    pid = _pede(db, servico, SO_EXECUCAO_REAL)
    _anterior(db, "f-qa", "android-02", 3, OUTRA)
    parque.lista = [_ap("android-01"), _ap("android-02")]
    assert servico.uma_volta(lambda: 1) is not None and _linha(db, pid)["aparelho"] == "android-02"
    parque.lista = [_ap("android-01")]                                # só a origem: espera, não fecha
    pid2 = _pede(db, servico, SO_EXECUCAO_REAL, fid="f-dois")
    assert servico.uma_volta(lambda: 1) is None and _linha(db, pid2)["estado"] == "pendente"


def test_o_limite_vem_antes_do_aparelho_novo(mundo: Mundo) -> None:  # noqa: F811
    db, servico, parque, _relogio, _ = mundo
    pid = _pede(db, servico)
    _no_limite(db)
    parque.lista = [_ap("android-02"), _ap("android-03")]
    assert servico.uma_volta(lambda: 1) is None and _linha(db, pid)["motivo"] == "limite_de_provas"


# ------------------------------------------------------------------ o fechamento pela linha `invalida`
def _roda(db: Database, servico: ServicoDeValidacao, parque: ParqueDeProva) -> tuple[str, str]:
    """Nasce o pedido do fluxo, o despachante o põe no aparelho e a execução de prova assenta `completed`."""
    pid = _pede(db, servico)
    run_id = servico.uma_volta(lambda: 1)
    assert run_id is not None
    db.execute("UPDATE runs SET status='completed' WHERE id=?", (run_id,))
    return pid, run_id


def _linha_de_prova(db: Database, run_id: str, stance: str, detalhe: str | None, origem: str | None = None) -> None:
    db.execute("INSERT INTO learning_evidence(item_ref, stance, origin_ref, run_id, instance_id, simulated, detail,"
               " observed_at) VALUES (?,?,?,?,?,0,?,?)",
               ("fluxo:f-qa", stance, origem or f"run:{run_id}:{stance}", run_id, "android-10", detalhe,
                "2026-10-03T12:05:00.000Z"))


@pytest.mark.parametrize("motivo", list(MotivoDaInvalida))
def test_a_linha_invalida_fecha_o_pedido_com_o_motivo_dela(mundo: Mundo, motivo: MotivoDaInvalida) -> None:  # noqa: F811
    db, servico, parque, _relogio, _ = mundo
    pid, run_id = _roda(db, servico, parque)
    _linha_de_prova(db, run_id, "invalida", detalhe_da_invalida(motivo, "etapa 2", marca=MARCA))
    assert servico.minerar(run_id) == 1
    linha = _linha(db, pid)
    assert (linha["estado"], linha["motivo"]) == ("recusada", motivo.value)


@pytest.mark.parametrize("detalhe", [None, "", "lixo", f"[{MARCA}] invalida:codigo_que_nao_existe"])
def test_a_invalida_com_detalhe_ilegivel_fecha_sem_evidencia(mundo: Mundo, detalhe: str | None) -> None:  # noqa: F811
    db, servico, parque, _relogio, _ = mundo
    pid, run_id = _roda(db, servico, parque)
    _linha_de_prova(db, run_id, "invalida", detalhe)
    assert servico.minerar(run_id) == 1
    assert (_linha(db, pid)["estado"], _linha(db, pid)["motivo"]) == ("recusada", "sem_evidencia")


def test_a_invalida_vence_o_for_da_mesma_execucao(mundo: Mundo) -> None:  # noqa: F811
    """O caso da 5f2de5: a mensagem saiu duas vezes e a sombra tinha gravado `for`. A `invalida` vence, o pedido não
    vira `feita`."""
    db, servico, parque, _relogio, _ = mundo
    pid, run_id = _roda(db, servico, parque)
    _linha_de_prova(db, run_id, "for", f"[{MARCA}] etapa 1")
    _linha_de_prova(db, run_id, "invalida",
                    detalhe_da_invalida(MotivoDaInvalida.EFEITO_REPETIDO, "2 envios", marca=MARCA))
    assert servico.minerar(run_id) == 1
    assert (_linha(db, pid)["estado"], _linha(db, pid)["motivo"]) == ("recusada", "efeito_repetido")


def test_a_invalida_de_outra_execucao_nao_fecha_este_pedido(mundo: Mundo) -> None:  # noqa: F811
    db, servico, parque, _relogio, _ = mundo
    pid, run_id = _roda(db, servico, parque)
    _linha_de_prova(db, "r-de-outra-execucao", "invalida", detalhe_da_invalida(MotivoDaInvalida.ATOR_SEM_ACAO, ""))
    _linha_de_prova(db, run_id, "for", f"[{MARCA}] etapa 1")
    assert servico.minerar(run_id) == 1
    assert (_linha(db, pid)["estado"], _linha(db, pid)["motivo"]) == ("feita", None)


@pytest.mark.parametrize("motivo", list(MotivoDaInvalida))
def test_o_pedido_fechado_pela_invalida_nao_e_chegada_nem_reabre(mundo: Mundo, motivo: MotivoDaInvalida) -> None:  # noqa: F811
    """Esses motivos não respondem ao curador (nenhuma evidência chegou) nem reabrem pelo item 8 (a prova que não vale
    não é a execução livre de antes da prova): o curador não é chamado de volta e o laço não se paga de novo."""
    db, servico, parque, _relogio, _ = mundo
    pid, run_id = _roda(db, servico, parque)
    _linha_de_prova(db, run_id, "invalida", detalhe_da_invalida(motivo, "etapa 2", marca=MARCA))
    assert servico.minerar(run_id) == 1
    db.execute("UPDATE runs SET prova_fluxo_id=NULL WHERE id=?", (run_id,))     # a condição mais favorável à reabertura
    assert servico.chegadas() == []
    assert RegistroDeValidacoesSql(db).para_reabrir() == []
    assert servico.executar(_relogio()) == 0
    assert _linha(db, pid)["motivo"] == motivo.value


def test_o_passo_remotiva_o_sem_evidencia_cuja_execucao_ganhou_a_invalida_depois(mundo: Mundo) -> None:  # noqa: F811
    db, servico, parque, relogio, _ = mundo
    pid, run_id = _roda(db, servico, parque)
    assert servico.minerar(run_id) == 1
    assert _linha(db, pid)["motivo"] == "sem_evidencia"
    assert servico.executar(relogio()) == 0
    _linha_de_prova(db, run_id, "invalida", detalhe_da_invalida(MotivoDaInvalida.PONTO_DE_PARTIDA, "etapa 1"))
    assert servico.executar(relogio()) == 1
    assert _linha(db, pid)["motivo"] == "ponto_de_partida"
    assert servico.executar(relogio()) == 0                            # idempotente


def test_a_prova_invalida_conta_para_o_limite_com_a_marca_dela(mundo: Mundo) -> None:  # noqa: F811
    """A execução que deixou `invalida` gastou: entra na contagem do limite (a linha leva a marca do conteúdo)."""
    db, servico, parque, relogio, _ = mundo
    _pede(db, servico)
    _no_limite(db, estado="recusada", stance="invalida")
    assert servico.uma_volta(lambda: 1) is None
    assert RegistroDeValidacoesSql(db).pendentes() == [] and parque.enfileiradas == []
