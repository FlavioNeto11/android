"""Item 28.11 — aviso fora do painel: o que vira mensagem, a fila durável (migração 068) e a entrega.

Prova `simulated`: canal e relógio falsos, sem rede e sem Telegram. Dois "backends" são dois `Database` no mesmo banco
(SQLite, ou PostgreSQL com `TEST_DATABASE_URL`, pela fábrica configurada).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.db import Database
from app.modules.avisos.application.entrega import Entrega, FalhaDeEnvio, entregar, espera_da_tentativa
from app.modules.avisos.domain.mensagem import Aviso, aviso_de_evento, link_da_caixa, texto_da_mensagem
from app.modules.avisos.infrastructure.fila_sql import FilaDeAvisos
from app.taskqueue.travas import AVISOS, Lideranca, TravaPerdida
from app.util import now

from .conftest import make_config

AQUI, LAH = "servidor-a", "servidor-b"
PAINEL = "http://192.168.1.10:8000/"


class Relogio:
    def __init__(self) -> None:
        self.t = now()

    def __call__(self) -> datetime:
        return self.t

    def avancar(self, s: float) -> None:
        self.t += timedelta(seconds=s)


def _bancos(tmp_path: Path) -> tuple[Database, Database]:
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    a = Database(cfg.db_dsn)
    a.migrate()
    return a, Database(cfg.db_dsn)


def _aviso(chave: str = "run:r1:needs_input") -> Aviso:
    return Aviso(chave=chave, tipo="run.needs_input", titulo="Central de Aparelhos: x", corpo="y", link=None)


# ===================================================================== 1. o que vira mensagem
def test_so_o_que_e_pendencia_vira_aviso() -> None:
    assert aviso_de_evento("log", {}, 1) is None
    assert aviso_de_evento("step.updated", {"step": {}}, 2) is None
    # execução: só a que PAROU pedindo informação; `run.updated` de qualquer outro estado é silêncio
    assert aviso_de_evento("run.updated", {"run": {"id": "r1", "status": "running"}}, 3) is None
    assert aviso_de_evento("run.updated", {"run": {"id": "r1", "status": "needs_input"}}, 4).chave == "run:r1:needs_input"  # type: ignore[union-attr]
    # sessão: entrar em intervenção avisa; sair não
    assert aviso_de_evento("session.needs_person", {"active": False}, 5) is None
    assert aviso_de_evento("session.needs_person", {"active": True}, 6).chave == "session:6"  # type: ignore[union-attr]
    assert aviso_de_evento("approval.pending", {"approval": {"id": "ap9"}}, 7).chave == "approval:ap9"  # type: ignore[union-attr]


def test_a_chave_e_do_fato_nao_do_evento() -> None:
    """`run.updated` sai a cada mudança da execução; a chave por execução faz as repetições serem a MESMA linha."""
    a = aviso_de_evento("run.updated", {"run": {"id": "r1", "status": "needs_input"}}, 10)
    b = aviso_de_evento("run.updated", {"run": {"id": "r1", "status": "needs_input"}}, 11)
    assert a is not None and b is not None and a.chave == b.chave


def test_pedido_aviso_vale_para_todo_tipo_e_so_o_que_pede_pessoa_leva_link() -> None:
    pede = aviso_de_evento("pedido.aviso", {"aviso": {"id": "a1", "tipo": "aprovacao_pendente",
                                                      "requer_pessoa": True}}, 1, PAINEL)
    info = aviso_de_evento("pedido.aviso", {"aviso": {"id": "a2", "tipo": "relatorio_pronto",
                                                      "requer_pessoa": False}}, 2, PAINEL)
    novo = aviso_de_evento("pedido.aviso", {"aviso": {"id": "a3", "tipo": "tipo_que_nao_existe",
                                                      "requer_pessoa": False}}, 3, PAINEL)
    assert pede is not None and info is not None and novo is not None
    assert pede.link == "http://192.168.1.10:8000/#/pendencias" and "aprovação" in pede.titulo
    assert info.link is None and "relatório" in info.titulo
    assert novo.titulo.endswith("Um pedido tem novidade")
    assert aviso_de_evento("pedido.aviso", {"aviso": {}}, None) is None      # sem id nenhum não há chave


def test_a_mensagem_nao_leva_dado_de_persona_conta_nem_conteudo() -> None:
    dados = {"approval": {"id": "ap1", "summary": "Responder à Maria sobre o pedido 4455", "target": "@maria.souza",
                          "content": "oi, tudo bem? me passa o seu CPF", "profile_id": "persona-lucas"},
             "profile_id": "persona-lucas", "instance_id": "android-01", "detail": "conta lucas.real"}
    for kind in ("approval.pending", "session.needs_person"):
        aviso = aviso_de_evento(kind, {**dados, "active": True}, 5, PAINEL)
        assert aviso is not None
        texto = texto_da_mensagem(aviso.titulo, aviso.corpo, aviso.link)
        # O aparelho pode sair (28.31): "android-01" não é pessoa, conta nem contato.
        for proibido in ("Maria", "4455", "maria.souza", "CPF", "persona-lucas", "lucas"):
            assert proibido not in texto, (kind, proibido)
    aviso = aviso_de_evento("run.updated", {"run": {"id": "r1", "status": "needs_input", "command": "mande oi ao Pedro",
                                                    "status_detail": "qual é o telefone do Pedro?"}}, 6, PAINEL)
    assert aviso is not None
    assert "Pedro" not in texto_da_mensagem(aviso.titulo, aviso.corpo, aviso.link)


def test_link_so_com_base_http_e_o_texto_segue_sem_ele() -> None:
    assert link_da_caixa(None) is None and link_da_caixa("") is None
    assert link_da_caixa("ftp://painel") is None and link_da_caixa("javascript:alert(1)") is None
    assert link_da_caixa("https://painel.exemplo/") == "https://painel.exemplo/#/pendencias"
    sem = aviso_de_evento("approval.pending", {"approval": {"id": "x"}}, 1, None)
    assert sem is not None and sem.link is None
    assert "\n" not in texto_da_mensagem(sem.titulo, "", None)


# ===================================================================== 2. a fila (migração 068)
def test_migracao_068_cria_a_fila_com_chave_unica_e_estado_fechado(tmp_path: Path) -> None:
    db, _ = _bancos(tmp_path)
    assert "068_avisos_entregas" in [r["version"] for r in db.query("SELECT version FROM schema_migrations")]
    assert db.divergencias() == []
    fila = FilaDeAvisos(db)
    assert fila.enfileirar(_aviso()) is True
    with pytest.raises(Exception):                                           # estado fora do CHECK é recusado
        db.execute("UPDATE avisos_entregas SET estado='inventado'")


def test_o_mesmo_fato_visto_por_duas_replicas_e_uma_linha(tmp_path: Path) -> None:
    da, db_ = _bancos(tmp_path)
    a, b = FilaDeAvisos(da), FilaDeAvisos(db_)
    assert a.enfileirar(_aviso("evento:7")) is True
    assert b.enfileirar(_aviso("evento:7")) is False, "o mesmo evento relido virou segunda mensagem"
    assert da.scalar("SELECT COUNT(*) FROM avisos_entregas") == 1
    assert a.enfileirar(_aviso("evento:8")) is True


def test_reivindicar_conta_a_tentativa_e_nao_entrega_a_mesma_linha_duas_vezes(tmp_path: Path) -> None:
    da, db_ = _bancos(tmp_path)
    r = Relogio()
    lider = Lideranca(da, dono=AQUI, relogio=r)
    token = lider.tomar(AVISOS)
    assert token == 1
    fila = FilaDeAvisos(da, relogio=r)
    fila.enfileirar(_aviso("a"))
    cerca = lambda: lider.cercada(AVISOS, token)                              # noqa: E731
    um = fila.reivindicar_um(cerca=cerca)
    assert um is not None and um.chave == "a" and um.tentativas == 1
    assert fila.reivindicar_um(cerca=cerca) is None, "a linha `enviando` foi entregue de novo"
    fila.marcar_enviado(um.id)
    assert fila.contagens() == {"enviado": 1}


def test_lider_sem_mandato_e_recusado_antes_de_marcar_a_linha(tmp_path: Path) -> None:
    da, db_ = _bancos(tmp_path)
    r = Relogio()
    velho = Lideranca(da, dono=AQUI, relogio=r)
    novo = Lideranca(db_, dono=LAH, relogio=r)
    t_velho = velho.tomar(AVISOS)
    FilaDeAvisos(da, relogio=r).enfileirar(_aviso("a"))
    r.avancar(200)                                                            # o velho dormiu além do prazo
    assert novo.tomar(AVISOS) == 2
    with pytest.raises(TravaPerdida):
        FilaDeAvisos(da, relogio=r).reivindicar_um(cerca=lambda: velho.cercada(AVISOS, t_velho))
    assert da.one("SELECT estado, tentativas FROM avisos_entregas")["estado"] == "pendente"


def test_enviando_abandonado_vira_incerto_e_nao_e_reenviado(tmp_path: Path) -> None:
    da, _ = _bancos(tmp_path)
    r = Relogio()
    lider = Lideranca(da, dono=AQUI, relogio=r)
    token = lider.tomar(AVISOS)
    cerca = lambda: lider.cercada(AVISOS, token)                              # noqa: E731
    fila = FilaDeAvisos(da, relogio=r)
    fila.enfileirar(_aviso("a"))
    assert fila.reivindicar_um(cerca=cerca) is not None                       # …e o processo caiu aqui
    assert fila.varrer_incertos(cerca=cerca, parado_ha_s=600) == 0            # ainda pode estar a caminho
    r.avancar(601)
    lider.tomar(AVISOS)
    assert fila.varrer_incertos(cerca=cerca, parado_ha_s=600) == 1
    assert fila.reivindicar_um(cerca=cerca) is None
    linha = da.one("SELECT estado, ultimo_erro FROM avisos_entregas")
    assert linha["estado"] == "incerto" and "não foi reenviado" in linha["ultimo_erro"]


def test_pendente_velho_vence_e_final_antigo_e_purgado(tmp_path: Path) -> None:
    da, _ = _bancos(tmp_path)
    r = Relogio()
    lider = Lideranca(da, dono=AQUI, relogio=r)
    token = lider.tomar(AVISOS)
    cerca = lambda: lider.cercada(AVISOS, token)                              # noqa: E731
    fila = FilaDeAvisos(da, relogio=r)
    fila.enfileirar(_aviso("velho"))
    r.avancar(25 * 3600)
    lider.tomar(AVISOS)
    fila.enfileirar(_aviso("novo"))
    assert fila.vencer(cerca=cerca, validade_h=24) == 1
    assert fila.contagens() == {"descartado": 1, "pendente": 1}
    r.avancar(40 * 86400)
    lider.tomar(AVISOS)
    assert fila.purgar(cerca=cerca, retencao_dias=30) == 1                    # só o final antigo; o pendente fica
    assert fila.contagens() == {"pendente": 1}


# ===================================================================== 3. a entrega
class CanalFalso:
    def __init__(self, *falhas: FalhaDeEnvio | None) -> None:
        self.falhas = list(falhas)
        self.enviados: list[tuple[str, str, str | None]] = []

    async def enviar(self, titulo: str, corpo: str, link: str | None) -> None:
        if self.falhas:
            falha = self.falhas.pop(0)
            if falha is not None:
                raise falha
        self.enviados.append((titulo, corpo, link))


def _entregar(fila: FilaDeAvisos, canal: CanalFalso, lider: Lideranca, token: int, r: Relogio, **kw: object) -> object:
    return asyncio.run(entregar(fila, canal, cerca=lambda: lider.cercada(AVISOS, token), agora=r,
                                limite=int(kw.get("limite", 10)), max_tentativas=int(kw.get("max", 3)), backoff_s=30.0))


def test_entrega_ok_marca_enviado_e_nao_repete(tmp_path: Path) -> None:
    da, _ = _bancos(tmp_path)
    r = Relogio()
    lider = Lideranca(da, dono=AQUI, relogio=r)
    token = lider.tomar(AVISOS)
    assert token is not None
    fila, canal = FilaDeAvisos(da, relogio=r), CanalFalso()
    fila.enfileirar(_aviso("a"))
    fila.enfileirar(_aviso("b"))
    res = _entregar(fila, canal, lider, token, r)
    assert (res.enviados, res.adiados, res.falharam) == (2, 0, 0)               # type: ignore[attr-defined]
    assert len(canal.enviados) == 2
    _entregar(fila, canal, lider, token, r)
    assert len(canal.enviados) == 2, "o aviso já enviado saiu de novo"


def test_429_adia_pelo_retry_after_e_para_o_lote(tmp_path: Path) -> None:
    da, _ = _bancos(tmp_path)
    r = Relogio()
    lider = Lideranca(da, dono=AQUI, relogio=r)
    token = lider.tomar(AVISOS)
    assert token is not None
    fila = FilaDeAvisos(da, relogio=r)
    for c in ("a", "b", "c"):
        fila.enfileirar(_aviso(c))
    canal = CanalFalso(FalhaDeEnvio("429 do canal", espera_s=120.0))
    res = _entregar(fila, canal, lider, token, r)
    assert (res.enviados, res.adiados, res.esperar_s) == (0, 1, 120.0)          # type: ignore[attr-defined]
    assert fila.contagens() == {"pendente": 3}, "o lote seguiu em frente depois do 429 ou deixou linha `enviando`"
    # A pausa do canal é de quem chama (o laço do serviço); a linha que levou o 429 espera o prazo dela.
    assert _entregar(fila, canal, lider, token, r).enviados == 2                 # type: ignore[attr-defined]
    r.avancar(119)
    lider.tomar(AVISOS)                                                          # o laço renova o mandato a cada 20 s
    assert _entregar(fila, canal, lider, token, r).enviados == 0                 # type: ignore[attr-defined]
    r.avancar(2)
    lider.tomar(AVISOS)
    assert _entregar(fila, canal, lider, token, r).enviados == 1                 # type: ignore[attr-defined]
    assert fila.contagens() == {"enviado": 3}


def test_falha_definitiva_e_teto_de_tentativas_viram_falhou(tmp_path: Path) -> None:
    da, _ = _bancos(tmp_path)
    r = Relogio()
    lider = Lideranca(da, dono=AQUI, relogio=r)
    token = lider.tomar(AVISOS)
    assert token is not None
    fila = FilaDeAvisos(da, relogio=r)
    fila.enfileirar(_aviso("a"))
    _entregar(fila, CanalFalso(FalhaDeEnvio("chat não encontrado", definitiva=True)), lider, token, r)
    assert fila.contagens() == {"falhou": 1}
    fila.enfileirar(_aviso("b"))
    canal = CanalFalso(*[FalhaDeEnvio("fora do ar")] * 3)
    for _ in range(3):
        r.avancar(3600)
        lider.tomar(AVISOS)
        _entregar(fila, canal, lider, token, r, max=3)
    assert fila.contagens() == {"falhou": 2}
    assert da.one("SELECT tentativas FROM avisos_entregas WHERE chave='b'")["tentativas"] == 3
    assert canal.enviados == []


def test_espera_cresce_pelo_backoff_e_respeita_o_pedido_do_canal() -> None:
    assert espera_da_tentativa(1, base_s=30, pedida_s=None) == 30
    assert espera_da_tentativa(3, base_s=30, pedida_s=None) == 120
    assert espera_da_tentativa(1, base_s=30, pedida_s=500) == 500
    assert espera_da_tentativa(20, base_s=30, pedida_s=None) == 3600             # teto
    assert isinstance(Entrega, type)
