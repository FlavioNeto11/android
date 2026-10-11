"""31.43: a pergunta parada vence sozinha, no prazo do config, PELO SISTEMA.

Estende a 29.50 (`needs_input` sem resposta cancela a execução) de duas maneiras: o prazo vem de `execucao.pergunta_vence_h`
(com a chave `execucao.vencimento_ligado`), e o objetivo em `waiting_user` de uma execução JÁ TERMINADA, que ninguém
retomou, também é fechado (`RunService.vencer_objetivos_parados`). Antes, esse objetivo ficava `waiting_user` para sempre
(22 assim no banco central em 04/10). Só muda estado: nada responde, digita, toca aparelho ou chama IA, e não há o sinal de
PESSOA `cancelou_execucao` (ADR-054).

O formato do evento é fixo, porque a Canais o lê por um adaptador: `dados.vencimento` com EXATAMENTE as quatro chaves
`regra`, `motivo`, `horas` e `desde`, no `run.updated` (pergunta) e no `objective.updated` (objetivo parado).

Prova `simulated`: o harness com o provedor simulado; o estado "esperando pessoa" é posto no banco depois de uma execução
de verdade. O relógio é o `agora` passado ao serviço; o laço passa `now()`.
"""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import pytest

from app.taskqueue.service import CHAVE_LIGADO_DESDE
from app.util import now, parse_iso, to_iso

from .conftest import Harness

INCOMPLETO = "Abra o QA Messenger e envie uma mensagem"
MOTIVO = "vencido_sem_resposta"


def ligado_ha_muito(h: Harness) -> None:
    """O vencimento ligado bem antes de qualquer espera do teste: a carência do 31.50 já passou."""
    assert h.state is not None
    h.state.db.execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET "
                       "value=excluded.value", (CHAVE_LIGADO_DESDE, "2000-01-01T00:00:00.000Z"))
    h.state.runs._marca_lida = None                        # o serviço relê a marca gravada aqui


async def _pergunta(h: Harness) -> tuple[str, Any]:
    """Uma execução real em `needs_input` e o instante em que ela entrou lá (o `run.updated` da transição)."""
    run = h.run(["android-01"], command=INCOMPLETO, mode="plan")
    await h.wait_run(run.id, ("needs_input",))
    assert h.state is not None
    entrada = h.state.db.scalar("SELECT MAX(ts) FROM events WHERE run_id=? AND kind='run.updated'", (run.id,))
    ligado_ha_muito(h)
    return run.id, parse_iso(entrada)


async def _parado(h: Harness, *, espera_h: float, fim_h: float, run_status: str = "completed_with_issues") -> tuple[str, str]:
    """Uma execução que terminou de verdade e é posta no estado de hoje no banco central: o objetivo em `waiting_user`
    (entrou há `espera_h` horas), uma etapa dele esperando a pessoa e a execução terminal (terminou há `fim_h` horas)."""
    st = h.state
    assert st is not None
    run = h.run(["android-01"])
    await h.wait_run(run.id)
    oid = str(st.db.scalar("SELECT id FROM objectives WHERE run_id=?", (run.id,)))
    espera, fim = to_iso(now() - timedelta(hours=espera_h)), to_iso(now() - timedelta(hours=fim_h))
    st.db.execute("UPDATE objectives SET status='waiting_user', finished_at=? WHERE id=?", (espera, oid))
    st.db.execute("UPDATE steps SET status='waiting_user' WHERE id=(SELECT MAX(id) FROM steps WHERE objective_id=?)",
                  (oid,))
    st.db.execute("UPDATE runs SET status=?, finished_at=?, cancel_requested=0 WHERE id=?", (run_status, fim, run.id))
    ligado_ha_muito(h)
    return run.id, oid


def _sinais(h: Harness, kind: str) -> list[dict[str, Any]]:
    assert h.state is not None
    return [dict(r) for r in h.state.db.query("SELECT * FROM learning_signals WHERE kind=? ORDER BY id", (kind,))]


def _ultimo_evento(h: Harness, kind: str, **onde: str) -> dict[str, Any]:
    assert h.state is not None
    coluna, valor = next(iter(onde.items()))
    row = h.state.db.one(f"SELECT data FROM events WHERE {coluna}=? AND kind=? ORDER BY id DESC LIMIT 1", (valor, kind))
    return json.loads(row["data"])


def _status(h: Harness, run_id: str, oid: str) -> tuple[str, str, int]:
    assert h.state is not None
    objetivo = h.state.repo.objective_row(oid)
    run = h.state.repo.run_row(run_id)
    return objetivo["status"], run["status"], run["cancel_requested"]


# ------------------------------------------------------------------ (a) o prazo vem do config
async def test_a_pergunta_vence_no_prazo_do_config_com_o_motivo_para_maquina(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    harness.cfg.file.execucao.pergunta_vence_h = 1
    run_id, entrada = await _pergunta(harness)
    # Um minuto antes do prazo de 1 h, nada muda.
    assert st.runs.expirar_sem_resposta(entrada + timedelta(minutes=59)) == []
    assert st.repo.run_row(run_id)["status"] == "needs_input"
    # Passado o prazo, o sistema encerra, com o texto humano e a marca para máquina com a regra.
    assert st.runs.expirar_sem_resposta(entrada + timedelta(hours=1, minutes=1)) == [run_id]
    run = st.repo.run_row(run_id)
    assert (run["status"], run["cancel_requested"]) == ("cancelled", 1)
    assert run["status_detail"].startswith("Sem resposta em 1 h: a pergunta expirou")
    assert MOTIVO not in run["status_detail"] and run_id not in run["status_detail"]
    dados = _ultimo_evento(harness, "run.updated", run_id=run_id)
    # Formato fixo (a Canais lê por um adaptador): exatamente estas quatro chaves.
    assert dados["vencimento"] == {"regra": "31.43", "motivo": MOTIVO, "horas": 1, "desde": to_iso(entrada)}
    # A marca da 29.50 continua, para quem já a lê.
    assert dados["expirada"] == {"motivo": "sem_resposta", "horas": 1, "desde": to_iso(entrada)}
    # Ninguém fez o gesto: nenhum sinal de pessoa.
    assert _sinais(harness, "cancelou_execucao") == []


def test_o_padrao_do_config_e_o_de_24_horas() -> None:
    from app.config import ExecucaoCfg
    from app.taskqueue.service import CHAVE_LIGADO_DESDE, NEEDS_INPUT_EXPIRA_H
    cfg = ExecucaoCfg()
    assert (cfg.vencimento_ligado, cfg.pergunta_vence_h) == (True, NEEDS_INPUT_EXPIRA_H)


async def test_a_pergunta_ainda_dentro_do_prazo_fica(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    run_id, entrada = await _pergunta(harness)
    assert st.runs.expirar_sem_resposta(entrada + timedelta(hours=23)) == []
    assert st.repo.run_row(run_id)["status"] == "needs_input"


# ------------------------------------------------------------------ a chave
async def test_com_a_chave_desligada_nada_vence(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    run_pergunta, entrada = await _pergunta(harness)
    run_parado, oid = await _parado(harness, espera_h=100, fim_h=100)
    harness.cfg.file.execucao.vencimento_ligado = False
    longe = entrada + timedelta(days=30)
    assert st.runs.expirar_sem_resposta(longe) == []
    assert st.runs.vencer_objetivos_parados(longe) == []
    assert st.repo.run_row(run_pergunta)["status"] == "needs_input"
    assert _status(harness, run_parado, oid)[:2] == ("waiting_user", "completed_with_issues")
    # Ligada de novo, o mesmo estado vence (a chave é a única diferença).
    harness.cfg.file.execucao.vencimento_ligado = True
    assert st.runs.vencer_objetivos_parados(longe) == [oid]


# ------------------------------------------------------------------ (b) o objetivo parado de execução terminada
async def test_o_objetivo_waiting_user_de_execucao_terminada_vence_pelo_sistema(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    run_id, oid = await _parado(harness, espera_h=30, fim_h=30)
    assert _status(harness, run_id, oid) == ("waiting_user", "completed_with_issues", 0)
    # O relógio esperado: o mais tardio entre a espera do objetivo e o fim da execução (lidos antes de fechar).
    desde = max(str(st.repo.objective_row(oid)["finished_at"]), str(st.repo.run_row(run_id)["finished_at"]))
    assert st.runs.vencer_objetivos_parados(now()) == [oid]
    objetivo = st.repo.objective_row(oid)
    assert objetivo["status"] == "cancelled"
    # O texto é para o dono: humano, sem código nem id.
    assert objetivo["status_detail"].startswith("Sem resposta em 24 h: o pedido venceu e foi encerrado pelo sistema")
    for cru in ("waiting_user", MOTIVO, oid, run_id):
        assert cru not in objetivo["status_detail"]
    # A etapa que esperava a pessoa também fecha.
    assert st.db.scalar("SELECT COUNT(*) FROM steps WHERE objective_id=? AND status='waiting_user'", (oid,)) == 0
    # O evento leva a regra, o motivo e o relógio.
    dados = _ultimo_evento(harness, "objective.updated", objective_id=oid)
    assert dados["objective"]["status"] == "cancelled"
    # Formato fixo (a Canais lê por um adaptador): exatamente estas quatro chaves.
    assert dados["vencimento"] == {"regra": "31.43", "motivo": MOTIVO, "horas": 24, "desde": desde}
    # A execução fica com o que `recompute_run` deriva: não é cancelamento da pessoa.
    run = st.repo.run_row(run_id)
    assert (run["status"], run["cancel_requested"]) == ("completed_with_issues", 0)
    assert "1 cancelado" in run["status_detail"]
    assert _sinais(harness, "cancelou_execucao") == []
    # Idempotente: o objetivo já não espera ninguém.
    assert st.runs.vencer_objetivos_parados(now() + timedelta(days=5)) == []


async def test_o_objetivo_parado_recente_fica(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    run_id, oid = await _parado(harness, espera_h=2, fim_h=2)
    assert st.runs.vencer_objetivos_parados(now()) == []
    assert _status(harness, run_id, oid)[:2] == ("waiting_user", "completed_with_issues")


async def test_o_relogio_e_o_mais_tardio_entre_a_espera_e_o_fim_da_execucao(harness: Harness) -> None:
    """Outro item da execução retomado e refechado põe o fim da execução recente: o prazo recomeça; nunca adianta."""
    st = harness.state
    assert st is not None
    run_id, oid = await _parado(harness, espera_h=100, fim_h=1)
    assert st.runs.vencer_objetivos_parados(now()) == []
    run_id2, oid2 = await _parado(harness, espera_h=1, fim_h=100)
    assert st.runs.vencer_objetivos_parados(now()) == []
    assert _status(harness, run_id, oid)[0] == _status(harness, run_id2, oid2)[0] == "waiting_user"


async def test_a_execucao_ainda_viva_com_objetivo_waiting_user_nao_e_tocada(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    for estado in ("running", "paused", "needs_input"):
        run_id, oid = await _parado(harness, espera_h=100, fim_h=100, run_status=estado)
        assert st.runs.vencer_objetivos_parados(now()) == [], estado
        assert _status(harness, run_id, oid)[:2] == ("waiting_user", estado)


@pytest.mark.parametrize("corrida", ["objetivo_retomado", "execucao_retomada"])
async def test_a_retomada_no_meio_da_varredura_ganha_do_vencimento(harness: Harness, monkeypatch: pytest.MonkeyPatch,
                                                                 corrida: str) -> None:
    """A pessoa retoma o item (ou a execução) entre a leitura dos candidatos e a escrita: a escrita é condicional e não
    sobrescreve nada."""
    st = harness.state
    assert st is not None
    run_id, oid = await _parado(harness, espera_h=100, fim_h=100)
    original, feito = st.db.query, []

    def query_com_retomada(sql: str, params: Any = ()) -> Any:
        linhas = list(original(sql, params))
        if "FROM objectives o JOIN runs r" in sql and not feito:
            feito.append(True)
            if corrida == "objetivo_retomado":
                st.db.execute("UPDATE objectives SET status='pending' WHERE id=?", (oid,))
            else:
                st.db.execute("UPDATE runs SET status='running', finished_at=NULL WHERE id=?", (run_id,))
        return linhas

    monkeypatch.setattr(st.db, "query", query_com_retomada)
    assert st.runs.vencer_objetivos_parados(now()) == []
    assert feito
    objetivo, run, _ = _status(harness, run_id, oid)
    assert objetivo == ("pending" if corrida == "objetivo_retomado" else "waiting_user")
    assert run == ("completed_with_issues" if corrida == "objetivo_retomado" else "running")
    # Casa só a chave da regra: `LIKE '%31.43%'` casou o carimbo de hora 04:34:31.43x de um evento qualquer (31.349).
    assert st.db.scalar("SELECT COUNT(*) FROM events WHERE objective_id=? AND data LIKE '%\"vencimento\"%'", (oid,)) == 0


async def test_a_volta_do_laco_vence_os_dois_com_o_relogio_de_verdade(harness: Harness) -> None:
    """O caminho do laço (`AppState._expiracao_uma_vez`, sob a trava da retenção): a pergunta e o objetivo parado."""
    st = harness.state
    assert st is not None
    run_pergunta, _ = await _pergunta(harness)
    velho = to_iso(now() - timedelta(hours=25))
    st.db.execute("UPDATE runs SET created_at=? WHERE id=?", (velho, run_pergunta))
    st.db.execute("UPDATE events SET ts=? WHERE run_id=?", (velho, run_pergunta))
    run_parado, oid = await _parado(harness, espera_h=30, fim_h=30)
    assert await st._expiracao_uma_vez() is True
    assert st.repo.run_row(run_pergunta)["status"] == "cancelled"
    assert _status(harness, run_parado, oid)[:2] == ("cancelled", "completed_with_issues")


async def test_o_objetivo_vencido_deixa_de_contar_como_aberto(harness: Harness) -> None:
    """A fila da pessoa (`instancias._OBJETIVO_ABERTO`: pending, running, waiting_user) não segura mais o item vencido."""
    from app.modules.fleet.presentation.instancias import _OBJETIVO_ABERTO
    st = harness.state
    assert st is not None
    run_id, oid = await _parado(harness, espera_h=30, fim_h=30)
    assert st.repo.objective_row(oid)["status"] in _OBJETIVO_ABERTO
    st.runs.vencer_objetivos_parados(now())
    assert st.repo.objective_row(oid)["status"] not in _OBJETIVO_ABERTO


async def test_31_50b_falha_no_meio_do_vencimento_nao_deixa_metade_gravada(harness: Harness,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """31.50 (b): a guarda e o cancelamento são UMA transação. Uma falha depois de cancelar as etapas (aqui, ao expirar
    as aprovações) desfaz tudo: o objetivo segue esperando com a etapa aberta, e a volta seguinte vence inteiro."""
    st = harness.state
    assert st is not None
    run_id, oid = await _parado(harness, espera_h=30, fim_h=30)
    abertas = st.db.scalar("SELECT COUNT(*) FROM steps WHERE objective_id=? AND status='waiting_user'", (oid,))
    assert abertas == 1

    def quebra(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("[teste] falha no meio do vencimento")

    monkeypatch.setattr(st.scheduler, "_expirar_aprovacoes", quebra)
    assert st.runs.vencer_objetivos_parados(now()) == []
    assert _status(harness, run_id, oid)[0] == "waiting_user"
    assert st.db.scalar("SELECT COUNT(*) FROM steps WHERE objective_id=? AND status='waiting_user'", (oid,)) == 1
    monkeypatch.undo()
    assert st.runs.vencer_objetivos_parados(now()) == [oid]


def test_31_50c_o_prazo_tem_piso_de_uma_hora() -> None:
    """31.50 (c): um "0.05" no lugar de "5" encerraria em minutos tudo o que espera uma pessoa, sem desfazer."""
    from pydantic import ValidationError

    from app.config import ExecucaoCfg
    with pytest.raises(ValidationError):
        ExecucaoCfg(pergunta_vence_h=0.05)
    assert ExecucaoCfg(pergunta_vence_h=1).pergunta_vence_h == 1


async def test_31_50_ao_ligar_o_que_ja_esperava_ganha_a_carencia(harness: Harness) -> None:
    """31.50, carência: ligar o vencimento não vence de uma vez o que já estava parado (os 21 da primeira volta do
    deploy 30). O prazo conta do mais tardio entre a espera e a marca de quando foi ligado; desligar apaga a marca."""
    st = harness.state
    assert st is not None
    run_id, oid = await _parado(harness, espera_h=100, fim_h=100)
    harness.cfg.file.execucao.vencimento_ligado = False
    assert st.runs.vencer_objetivos_parados(now()) == []          # desligado: a marca some
    assert st.db.scalar("SELECT value FROM settings WHERE key=?", (CHAVE_LIGADO_DESDE,)) is None
    harness.cfg.file.execucao.vencimento_ligado = True
    assert st.runs.vencer_objetivos_parados(now()) == []          # acabou de ligar: carência
    assert _status(harness, run_id, oid)[0] == "waiting_user"
    assert st.runs.vencer_objetivos_parados(now() + timedelta(hours=23)) == []
    assert st.runs.vencer_objetivos_parados(now() + timedelta(hours=25)) == [oid]


async def test_31_50_lembrete_uma_vez_nas_duas_horas_antes_com_dados_sem_texto_do_pedido(harness: Harness) -> None:
    """31.50: o objetivo parado recebe UM `pendencia.vence_em` quando faltam 2 h ou menos; fora da janela, nada. Os
    dados dizem o que é, o aparelho, a ação e o `vence_em`; o texto ao dono é do montador dos avisos (28.31)."""
    st = harness.state
    assert st is not None
    run_id, oid = await _parado(harness, espera_h=20, fim_h=20)
    assert st.runs.lembrar_antes_de_vencer(now()) == []                       # faltam 4 h: cedo
    agora = now() + timedelta(hours=3)                                         # faltam ~1 h
    [chave] = st.runs.lembrar_antes_de_vencer(agora)
    assert chave.startswith(f"vencimento:lembrete:{oid}:")                    # a chave leva a entrada na espera
    assert st.runs.lembrar_antes_de_vencer(agora) == []                       # uma vez só por espera
    dados = _ultimo_evento(harness, "pendencia.vence_em", objective_id=oid)
    assert (dados["o_que"], dados["aparelho"], dados["regra"]) == ("objetivo", "android-01", "31.50")
    assert dados["vence_em"] > to_iso(agora) and dados["chave"] == chave
    comando = str(st.db.scalar("SELECT command FROM runs WHERE id=?", (run_id,)))
    assert comando not in json.dumps(dados, ensure_ascii=False)
    assert _status(harness, run_id, oid)[0] == "waiting_user"                  # o lembrete não muda estado


async def test_31_50_vence_em_no_dto_do_objetivo_parado_e_da_pergunta(harness: Harness) -> None:
    """31.50: o painel e o montador leem QUANDO vence: mais tardio entre a espera e a marca de ligado, mais o prazo."""
    st = harness.state
    assert st is not None
    run_id, oid = await _parado(harness, espera_h=20, fim_h=20)
    desde = max(str(st.repo.objective_row(oid)["finished_at"]), str(st.repo.run_row(run_id)["finished_at"]))
    dto = st.repo.objective_dto(st.repo.objective_row(oid))
    assert dto.vence_em == to_iso(parse_iso(desde) + timedelta(hours=24))
    assert st.repo.run_summary(st.repo.run_row(run_id)).vence_em is None      # a execução terminal não vence
    run_pergunta, entrada = await _pergunta(harness)
    assert st.repo.run_summary(st.repo.run_row(run_pergunta)).vence_em == to_iso(entrada + timedelta(hours=24))
    # Revisão do #313: sem a marca gravada, não dá para dizer quando vence; o DTO não anda com o relógio.
    st.db.execute("DELETE FROM settings WHERE key=?", (CHAVE_LIGADO_DESDE,))
    st.runs._marca_lida = None
    assert st.repo.objective_dto(st.repo.objective_row(oid)).vence_em is None
    ligado_ha_muito(harness)
    assert st.repo.objective_dto(st.repo.objective_row(oid)).vence_em == dto.vence_em
    harness.cfg.file.execucao.vencimento_ligado = False
    assert st.repo.objective_dto(st.repo.objective_row(oid)).vence_em is None


def test_31_50_a_aprovacao_pendente_traz_o_vence_em_do_objetivo() -> None:
    """31.50: a aprovação vence junto com o objetivo que bloqueia; decidida, não vence."""
    from dataclasses import replace
    from types import SimpleNamespace

    from app.social.approvals import Approval, ApprovalService
    pendente = Approval(id="a1", profile_id=None, run_id="r", objective_id="o", step_id=None, capability="CREATE_POST",
                        target=None, summary="s", generated_content=None, approved_content=None, status="pending",
                        created_at="2026-10-04T00:00:00.000Z")
    decidida = replace(pendente, id="a2", status="approved")
    loja = SimpleNamespace(list=lambda **_k: [pendente, decidida])
    pedidos: list[list[str]] = []
    repo = SimpleNamespace(vence_em_dos_objetivos=lambda ids: (pedidos.append(list(ids)),
                                                               {"o": "2026-10-05T00:00:00.000Z"})[1])
    itens = ApprovalService(loja, repo).list(status=None)  # type: ignore[arg-type]
    assert [i["vence_em"] for i in itens] == ["2026-10-05T00:00:00.000Z", None]
    assert pedidos == [["o"]]                                   # uma consulta para a lista inteira, só as pendentes


async def test_31_50_o_objetivo_que_volta_a_esperar_ganha_outro_lembrete(harness: Harness) -> None:
    """31.50: respondido e de novo em espera (outra entrada), o item tem outro prazo e outro lembrete, com outra chave."""
    st = harness.state
    assert st is not None
    _run_id, oid = await _parado(harness, espera_h=23, fim_h=23)
    [primeira] = st.runs.lembrar_antes_de_vencer(now())
    # Retomado e parado de novo: nova entrada na espera, agora (a execução terminou de novo também).
    agora = to_iso(now() + timedelta(seconds=1))         # a nova espera começa depois do 1º lembrete
    st.db.execute("UPDATE objectives SET finished_at=? WHERE id=?", (agora, oid))
    st.db.execute("UPDATE runs SET finished_at=? WHERE id=(SELECT run_id FROM objectives WHERE id=?)", (agora, oid))
    assert st.runs.lembrar_antes_de_vencer(now()) == []                       # novo prazo: ainda cedo
    [segunda] = st.runs.lembrar_antes_de_vencer(now() + timedelta(hours=23))
    assert segunda != primeira and segunda.startswith(f"vencimento:lembrete:{oid}:")


async def test_31_50_a_lista_le_as_entradas_numa_consulta_so(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """31.50: com N execuções em `needs_input`, a lista faz UMA consulta de entradas (não N) e dá o mesmo `vence_em`."""
    st = harness.state
    assert st is not None
    r1, _ = await _pergunta(harness)
    r2, _ = await _pergunta(harness)
    linhas = [st.repo.run_row(r1), st.repo.run_row(r2)]
    um_a_um = [st.repo.run_summary(r).vence_em for r in linhas]
    consultas: list[str] = []
    for nome in ("query", "scalar", "one"):
        original = getattr(st.db, nome)
        monkeypatch.setattr(st.db, nome, lambda sql, *a, _o=original, **k: (consultas.append(sql), _o(sql, *a, **k))[1])
    em_lote = [x.vence_em for x in st.repo.run_summaries(linhas)]
    monkeypatch.undo()
    assert em_lote == um_a_um and None not in em_lote
    assert sum("kind='run.updated'" in q for q in consultas) == 1
    assert not any("FROM settings" in q for q in consultas)                    # a marca vem da memória


def test_31_50_a_etapa_so_vai_no_lembrete_quando_a_chave_e_do_catalogo() -> None:
    """Revisão do #313: num plano livre a chave da etapa é da IA e pode levar um nome; só a da ação de catálogo vai."""
    from app.taskqueue.service import _etapa_segura
    assert _etapa_segura({"key": "open_mail_inbox", "capability": "OPEN_MAIL_INBOX"}) == "open_mail_inbox"
    assert _etapa_segura({"key": "send_dm_i2", "capability": "SEND_DM"}) == "send_dm_i2"
    assert _etapa_segura({"key": "send_dm_ana", "capability": "SEND_DM"}) is None
    assert _etapa_segura({"key": "verify_sent", "capability": None}) is None
    assert _etapa_segura(None) is None
