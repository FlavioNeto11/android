"""Lote offline da intenção (31.11, R2 e R3; ADR-069 item 21): só o que a sombra já mandou, remontado pelo código do
runtime, e só com a igualdade do texto provada pelo hash (31.22) ou, sem hash, pela salvaguarda "b".

Prova `simulated`: `DecisorFalso` (sem rede), banco de teste (PostgreSQL pela fábrica quando há `TEST_DATABASE_URL`) e a
RESOLVE de verdade sobre habilidades de teste. Um teste sobe o `AppState` do harness e confere que o lote remonta o MESMO
estado que a sombra do runtime mandou.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from app.config import DecisaoFechadaCfg
from app.events import EventBus
from app.planning.decisao_fechada import privacidade
from app.planning.decisao_fechada.contrato import ID_NENHUMA, RespostaDeDecisao, pergunta_choice
from app.planning.decisao_fechada.decisores import DecisorFalso
from app.planning.decisao_fechada.intencao import PERGUNTA_CATALOGO, PERGUNTA_DESEMPATE, ConsumidorDeIntencao, id_opaco
from app.planning.decisao_fechada.porta import Porta, hash_do_estado
from app.planning.decisao_fechada.sombra import RepositorioDeSombra, observador_de_sombra
from app.taskqueue.lote_intencao import (DESDE_ITEM_21, INSTRUCOES_PT, MENSAGEM_CONTA_REMOVIDA, MENSAGEM_PERFIL_REMOVIDO,
                                         LoteOffline, SalvaguardaB, consumidor_do_lote, em_portugues, enviada,
                                         ler_lote, ultima_remocao)
from app.taskqueue.sombra_intencao import SombraDaIntencao
from app.util import now_iso

from .conftest import Harness
from .fake_skills import banco
from .test_habilidades_na_execucao import ABRIR, Mundo
from .test_intencao_resolucao import doc_abrir, publicar_doc

CFG_SHADOW = DecisaoFechadaCfg(enabled=True, consumidores={"intencao": "shadow"})
DESDE = "2000-01-01T00:00:00Z"             # os testes gravam na hora do relógio; o padrão do item 21 tem teste próprio
ANTES = "2026-01-01T00:00:00.000Z"
EMPATE = "abra a conversa com 3 no instagram"
IDS = {s: id_opaco(s) for s in ("ig.abrir_conversa", "ig.abrir_numero")}


@pytest.fixture(autouse=True)
def _envio_aberto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(privacidade, "JEV_RUNTIME_SEND_APPROVED", True)


def _resposta(escolha: str, p: float) -> RespostaDeDecisao:
    return RespostaDeDecisao(escolha=escolha, probabilidades={escolha: p, ID_NENHUMA: round(1 - p, 2)}, confianca=p)


class Mundo3:
    """Duas habilidades que empatam, uma persona (Lucas), um aparelho e a sombra "do runtime" gravando no mesmo banco."""

    def __init__(self, tmp: Path, *, respostas: bool = True, postado: bool | None = True) -> None:
        self.db = banco(tmp, "lote.sqlite3")
        m = Mundo(self.db)
        m.publicar(ABRIR)
        publicar_doc(m, doc_abrir("ig.abrir_numero", "abra a conversa com {n} no instagram", "n", tipo="integer"))
        self.db.execute("INSERT INTO instances(id, idx, avd_name, console_port, system_port, mjpeg_port,"
                        " chromedriver_port) VALUES ('android-01', 1, 'a1', 19001, 19101, 19201, 19301)")
        self.db.execute("INSERT INTO instagram_profiles(id, username, first_name, created_at, updated_at)"
                        " VALUES ('p-lucas', 'lucas.teste', 'Lucas', ?, ?)", (ANTES, ANTES))
        EventBus(self.db).emit("log", "início do teste")              # o horizonte dos eventos fica antes das linhas
        decisor = DecisorFalso({PERGUNTA_CATALOGO: _resposta(IDS["ig.abrir_conversa"], 0.95),
                                PERGUNTA_DESEMPATE: _resposta(IDS["ig.abrir_numero"], 0.9)} if respostas else {},
                               postado=postado)
        self.sombra = RepositorioDeSombra(self.db)
        self.porta = Porta(decisor, cfg=CFG_SHADOW, observador=observador_de_sombra(self.sombra))
        self.lote = LoteOffline(self.db, skills_ligadas=True, fluxos_ligados=False)

    def viva(self, run_id: str, comando: str) -> None:
        """A execução com a foto (051) e a sombra da intenção do runtime observando-a, como depois do `_plan`."""
        foto = {"alvos": [{"instance_id": "android-01", "profile_id": "p-lucas"}],
                "command_sem_destinos": self.lote.runs.sem_destinos(comando)}
        self.db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, instance_ids, created_at, targets,"
                        " app_ids) VALUES (?,?,?,?,?,?,?,?,?)",
                        (run_id, f"k-{run_id}", comando, "execute", "planned", json.dumps(["android-01"]), ANTES,
                         json.dumps(foto), json.dumps(["instagram"])))
        viva = SombraDaIntencao(ConsumidorDeIntencao(self.porta, self.sombra), resolver=self.lote.resolver,
                                catalogo=self.lote.catalogo)
        viva._observar(run_id, lambda: self.lote.runs.dados_da_sombra(run_id))            # noqa: SLF001
        self.porta.aguardar_sombras()

    def ler(self, **kw: Any) -> Any:
        return ler_lote(self.lote, consumidor_do_lote(self.porta, self.db), desde=kw.pop("desde", DESDE), **kw)

    def linhas(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.query("SELECT * FROM decisao_fechada_sombra ORDER BY id")]


@pytest.fixture
def w(tmp_path: Path) -> Any:
    mundo = Mundo3(tmp_path)
    yield mundo
    mundo.porta.encerrar()
    mundo.db.close()


# ------------------------------------------------------------------ o critério de "já enviado"
@pytest.mark.parametrize("linha, esperado", [
    ({"postado": 1, "escolha": None, "fallback_reason": "rede"}, True),            # 083: o transporte foi chamado
    ({"postado": 0, "escolha": "opt:a", "fallback_reason": None}, False),          # 083: parou antes do POST
    ({"postado": None, "escolha": "opt:a", "fallback_reason": None}, True),        # antes da 083: há escolha
    ({"postado": None, "escolha": None, "fallback_reason": "abaixo_do_limiar"}, True),
    ({"postado": None, "escolha": None, "fallback_reason": "unknown_choice"}, True),
    ({"postado": None, "escolha": None, "fallback_reason": "parse"}, True),
    ({"postado": None, "escolha": None, "fallback_reason": "privacidade"}, False),
    ({"postado": None, "escolha": None, "fallback_reason": "desligado"}, False),
    ({"postado": None, "escolha": None, "fallback_reason": "orcamento"}, False),
    ({"postado": None, "escolha": None, "fallback_reason": "rede"}, False),
    ({"escolha": None, "fallback_reason": "abaixo_do_limiar"}, True),               # banco sem a coluna
    ({"postado": True, "escolha": None, "fallback_reason": "rede"}, True),          # um driver que devolva `bool`
    ({"postado": False, "escolha": "opt:a", "fallback_reason": None}, False),
])
def test_enviada_so_com_prova_de_post(linha: dict[str, Any], esperado: bool) -> None:
    assert enviada(linha) is esperado


# ------------------------------------------------------------------ o caminho do hash ("c")
def test_o_que_saiu_volta_igual_pelo_hash_com_as_duas_perguntas(w: Mundo3) -> None:
    w.viva("run-1", EMPATE)
    linhas = w.linhas()
    assert {r["pergunta_id"] for r in linhas} == {PERGUNTA_CATALOGO, PERGUNTA_DESEMPATE}
    leitura = w.ler()
    assert not leitura.fora and len(leitura.casos) == 1
    caso = leitura.casos[0]
    assert caso.salvaguarda == "c" and caso.run_id == "run-1" and caso.app == "instagram"
    assert {caso.estado_hash} == {r["estado_hash"] for r in linhas}
    assert {p.id for p in caso.pedido.perguntas} == {PERGUNTA_CATALOGO, PERGUNTA_DESEMPATE}
    assert len(caso.linhas) == 2 and caso.ts == linhas[0]["ts"]


def test_destino_e_numero_saem_como_a_sombra_mandou(w: Mundo3) -> None:
    """O destino ("com a persona Lucas") sai pela foto e pelo catálogo de destinos do runtime; o número vira marcador."""
    w.viva("run-2", "abra a conversa com 3 no instagram com a persona Lucas")
    [caso] = w.ler().casos
    assert caso.salvaguarda == "c"
    assert "Lucas" not in caso.pedido.estado["comando"] and "3" not in caso.pedido.estado["comando"]


def test_hash_diferente_fica_fora(w: Mundo3) -> None:
    w.viva("run-1", EMPATE)
    w.db.execute("UPDATE decisao_fechada_sombra SET estado_hash=?", ("f" * 64,))
    leitura = w.ler(salvaguarda_b=SalvaguardaB(codigo_igual=True))       # a "b" não socorre a linha COM hash
    assert leitura.casos == [] and leitura.fora == {"hash_diferente": 1}


def test_execucao_que_falhou_depois_ainda_e_lida(w: Mundo3) -> None:
    """A sombra leu a execução no fim do plano; falhar ou ser cancelada depois não muda o comando nem a foto."""
    w.viva("run-1", EMPATE)
    w.db.execute("UPDATE runs SET status='failed' WHERE id='run-1'")
    assert [c.salvaguarda for c in w.ler().casos] == ["c"]


# ------------------------------------------------------------------ sem hash: só pela "b"
def _sem_hash(w: Mundo3) -> None:
    w.db.execute("UPDATE decisao_fechada_sombra SET estado_hash=NULL")


def test_linha_sem_hash_fica_fora_sem_a_salvaguarda_b(w: Mundo3) -> None:
    w.viva("run-1", EMPATE)
    _sem_hash(w)
    assert w.ler().fora == {"anterior_a_086": 1}
    leitura = w.ler(salvaguarda_b=SalvaguardaB(codigo_igual=True))
    assert [c.salvaguarda for c in leitura.casos] == ["b"] and not leitura.fora


@pytest.mark.parametrize("remover", ["aparelho", "lapide", "persona", "conta", "alterada"])
def test_remocao_depois_da_linha_exclui_a_b_mas_nao_o_hash(w: Mundo3, remover: str) -> None:
    w.viva("run-1", EMPATE)
    depois = now_iso()
    if remover == "aparelho":
        w.db.execute("UPDATE instances SET retired_at=? WHERE id='android-01'", (depois,))
    elif remover == "lapide":
        w.db.execute("INSERT INTO contas_retiradas(app_id, handle_sha256, retirada_em, profile_id) VALUES (?,?,?,?)",
                     ("instagram", "a" * 64, depois, "p-lucas"))
    elif remover == "persona":
        EventBus(w.db).emit("log", MENSAGEM_PERFIL_REMOVIDO + ", com a credencial apagada do cofre")
    elif remover == "conta":
        EventBus(w.db).emit("log", MENSAGEM_CONTA_REMOVIDA)
    else:
        w.db.execute("UPDATE instagram_profiles SET updated_at=? WHERE id='p-lucas'", (depois,))
    assert ultima_remocao(w.db, DESDE) is not None
    assert [c.salvaguarda for c in w.ler().casos] == ["c"]                 # o hash prova: a remoção não importa
    _sem_hash(w)
    assert w.ler(salvaguarda_b=SalvaguardaB(codigo_igual=True)).fora == {"antes_de_remocao": 1}


def test_eventos_que_nao_alcancam_a_linha_excluem_a_b(w: Mundo3) -> None:
    w.viva("run-1", EMPATE)
    _sem_hash(w)
    w.db.execute("DELETE FROM events")                                     # a retenção levou os eventos da janela
    assert w.ler(salvaguarda_b=SalvaguardaB(codigo_igual=True)).fora == {"eventos_sem_alcance": 1}


# ------------------------------------------------------------------ o que nunca entra
def test_desde_o_item_21_por_padrao(w: Mundo3) -> None:
    w.viva("run-1", EMPATE)
    w.db.execute("UPDATE decisao_fechada_sombra SET ts='2026-10-03T15:00:00.000Z'")
    leitura = ler_lote(w.lote, consumidor_do_lote(w.porta, w.db))
    assert leitura.casos == [] and not leitura.fora
    assert DESDE_ITEM_21 == "2026-10-03T15:29:51Z"


def test_sem_post_nao_entra(tmp_path: Path) -> None:
    w = Mundo3(tmp_path, respostas=False, postado=None)                   # o decisor devolve `desligado`
    w.viva("run-1", EMPATE)
    w.viva("run-2", "entre com a senha girassol e abra a conversa com 3 no instagram")     # C7: a privacidade recusa
    linhas = w.linhas()
    assert {r["fallback_reason"] for r in linhas} == {"desligado", "privacidade"}
    assert w.ler().fora == {"nao_enviado": 2}
    w.porta.encerrar()
    w.db.close()


def test_postado_zero_nao_entra_mesmo_com_escolha(tmp_path: Path) -> None:
    w = Mundo3(tmp_path, postado=False)
    w.viva("run-1", EMPATE)
    assert w.ler().fora == {"nao_enviado": 1}
    w.porta.encerrar()
    w.db.close()


def test_sem_execucao_sem_foto_e_privacidade_de_hoje(w: Mundo3, monkeypatch: pytest.MonkeyPatch) -> None:
    w.viva("run-1", EMPATE)
    w.viva("run-2", EMPATE + " agora")
    w.viva("run-3", EMPATE + " hoje")
    w.db.execute("DELETE FROM runs WHERE id='run-1'")
    w.db.execute("UPDATE runs SET targets=NULL WHERE id='run-2'")
    assert w.ler().fora == {"sem_execucao": 1, "sem_foto": 1}
    monkeypatch.setattr(privacidade, "JEV_RUNTIME_SEND_APPROVED", False)   # hoje a privacidade recusaria
    assert w.ler().fora == {"sem_execucao": 1, "sem_foto": 1, "privacidade_hoje": 1}


# ------------------------------------------------------------------ português × inglês (D-J7)
def test_em_portugues_so_troca_as_instrucoes(w: Mundo3) -> None:
    w.viva("run-1", EMPATE)
    [caso] = w.ler().casos
    pt = em_portugues(caso.pedido)
    assert pt.estado == caso.pedido.estado
    assert hash_do_estado(privacidade.redigir(pt).estado) == caso.estado_hash
    for en, p in zip(caso.pedido.perguntas, pt.perguntas, strict=True):
        assert (p.id, dict(p.opcoes), p.limiar, p.tipo) == (en.id, dict(en.opcoes), en.limiar, en.tipo)
        assert p.instrucoes == INSTRUCOES_PT[p.id] and p.instrucoes != en.instrucoes
    assert set(INSTRUCOES_PT) == {PERGUNTA_CATALOGO, PERGUNTA_DESEMPATE}
    with pytest.raises(KeyError):                         # pergunta sem tradução registrada: erro, não inglês calado
        em_portugues(replace(caso.pedido, perguntas=(pergunta_choice("outra", "x", {"opt:a": "a"}),)))


def test_o_lote_nao_grava_nada(w: Mundo3) -> None:
    w.viva("run-1", EMPATE)
    antes = w.linhas()
    w.ler()
    assert w.linhas() == antes


# ------------------------------------------------------------------ as mensagens de remoção são as do serviço
async def test_mensagens_de_remocao_sao_as_que_o_servico_grava(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    pid = "p-remover"
    st.db.execute("INSERT INTO instagram_profiles(id, username, created_at, updated_at) VALUES (?,?,?,?)",
                  (pid, "remover.teste", ANTES, ANTES))
    st.db.execute("INSERT INTO profile_accounts(id, profile_id, app_id, handle, created_at, updated_at)"
                  " VALUES ('c-extra', ?, 'outlook', 'extra@exemplo.com', ?, ?)", (pid, ANTES, ANTES))
    desde = now_iso()
    st.social.delete_account(pid, "c-extra")
    st.social.delete_profile(pid)
    mensagens = [str(r["message"]) for r in st.db.query("SELECT message FROM events WHERE kind='log' AND ts>=?",
                                                         (desde,))]
    assert any(m.startswith(MENSAGEM_CONTA_REMOVIDA) for m in mensagens)
    assert any(m.startswith(MENSAGEM_PERFIL_REMOVIDO) for m in mensagens)
    assert ultima_remocao(st.db, desde) is not None


# ------------------------------------------------------------------ o mesmo estado que o runtime mandou
async def test_o_lote_remonta_o_estado_que_a_sombra_do_runtime_mandou(harness: Harness,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    """O `AppState` de verdade (`state.py`): a sombra do runtime observa um plano; o lote, montado só sobre o banco,
    remonta o MESMO estado (o hash bate) e as mesmas perguntas."""
    st = harness.state
    assert st is not None
    monkeypatch.setattr(st.cfg.file.skills, "enabled", True)
    Mundo(st.db).publicar(ABRIR)
    decisor = DecisorFalso({PERGUNTA_CATALOGO: _resposta(IDS["ig.abrir_conversa"], 0.95)}, postado=True)
    st.decisao_fechada.decisor = decisor
    st.decisao_fechada.cfg = CFG_SHADOW
    run = harness.run(["android-01"], command="abra o aplicativo de configuracoes 3 vezes", mode="plan")
    await harness.wait_run(run.id, statuses=("planned", "needs_input", "failed"), timeout=20.0)
    assert st.runs.sombra_intencao is not None
    await st.runs.sombra_intencao.aguardar()
    st.decisao_fechada.aguardar_sombras()
    assert len(decisor.chamadas) == 1
    lote = LoteOffline(st.db, skills_ligadas=st.cfg.file.skills.enabled, fluxos_ligados=st.cfg.file.ai.flows)
    leitura = ler_lote(lote, consumidor_do_lote(st.decisao_fechada, st.db), desde=DESDE)
    assert not leitura.fora and [c.salvaguarda for c in leitura.casos] == ["c"]
    [caso] = leitura.casos
    enviado = decisor.chamadas[0]
    assert caso.pedido.estado == enviado.estado and caso.run_id == run.id
    assert [(p.id, dict(p.opcoes), p.instrucoes) for p in caso.pedido.perguntas] == [
        (p.id, dict(p.opcoes), p.instrucoes) for p in enviado.perguntas]
