"""28.16: a faxina das tabelas de canal por prazo de retenção. Relógio falso; o texto é zerado ANTES de a linha sumir, a
linha que espera alguém e a mais nova de cada canal ficam, e o dedupe segue valendo dentro do prazo."""
from __future__ import annotations

import contextlib
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import EntradaDoTelegramCfg, TrelloCfg
from app.db import Database
from app.events import EventBus
from app.modules.avisos.infrastructure.entrada_sql import EntradasDoCanal
from app.modules.avisos.infrastructure.faxina_sql import FaxinaDosCanais
from app.modules.avisos.infrastructure.fila_sql import FilaDeAvisos
from app.modules.avisos.infrastructure.servico import ServicoDeAvisos
from app.taskqueue.travas import Lideranca
from app.util import now, to_iso

from .conftest import make_config

AQUI = "servidor-a"


class Relogio:
    def __init__(self) -> None:
        self.t = now()

    def __call__(self) -> datetime:
        return self.t

    def avancar(self, dias: float) -> None:
        self.t += timedelta(days=dias)


class Cena:
    def __init__(self, tmp_path: Path) -> None:
        self.cfg = make_config(tmp_path)
        self.cfg.ensure_dirs()
        self.db = Database(self.cfg.db_dsn)
        self.db.migrate()
        self.r = Relogio()
        self.faxina = FaxinaDosCanais(self.db, relogio=self.r)

    def entrada(self, canal: str, ident: str, *, texto: str | None = "segredo de conversa", estado: str = "feita",
                ordem: int | None = None) -> None:
        repo = EntradasDoCanal(self.db, canal=canal, relogio=self.r)
        repo.gravar(id_externo=ident, ordem=ordem, tipo="mensagem", do_dono=True, ref_mensagem=None, responde_a=None,
                    texto=texto, tamanho=len(texto or ""), estado=estado)
        self.db.execute("UPDATE canal_entradas SET resposta='resposta guardada', previa='{\"x\":1}', erro='e'"
                        " WHERE canal=? AND id_externo=?", (canal, ident))

    def enviada(self, canal: str, ref: str) -> None:
        self.db.execute("INSERT INTO canal_enviadas(canal, ref_mensagem, origem, fato, enviada_em) VALUES (?,?,?,?,?)",
                        (canal, ref, "aviso", "approval:1", to_iso(self.r())))

    def cartao(self, chave: str, estado: str) -> None:
        agora = to_iso(self.r())
        self.db.execute("INSERT INTO trello_cartoes(chave, card_id, quadro, estado, criado_em, atualizado_em)"
                        " VALUES (?,?,?,?,?,?)", (chave, f"card-{chave}", "q", estado, agora, agora))

    def linhas(self, canal: str) -> dict[str, dict[str, object]]:
        return {str(r["id_externo"]): dict(r) for r in self.db.query(
            "SELECT * FROM canal_entradas WHERE canal=?", (canal,))}

    def faxinar(self, canal: str, dias: float = 30.0):  # noqa: ANN201
        return self.faxina.faxinar(cerca=contextlib.nullcontext, canal=canal, retencao_dias=dias)


@pytest.fixture
def c(tmp_path: Path) -> Cena:
    return Cena(tmp_path)


def test_vencida_perde_o_texto_e_some_e_a_recente_fica_intacta(c: Cena) -> None:
    c.entrada("telegram", "1", ordem=1)
    c.entrada("telegram", "2", ordem=2, estado="pergunta")          # espera o Executar: perde o texto, não some
    c.enviada("telegram", "500")
    c.r.avancar(31)
    c.entrada("telegram", "3", ordem=3)
    c.enviada("telegram", "501")
    f = c.faxinar("telegram")
    linhas = c.linhas("telegram")
    assert set(linhas) == {"2", "3"} and (f.zeradas, f.apagadas, f.enviadas) == (2, 1, 1)
    assert all(linhas["2"][k] is None for k in ("texto", "previa", "resposta", "erro"))
    assert linhas["3"]["texto"] == "segredo de conversa" and linhas["3"]["resposta"] == "resposta guardada"
    assert [r["ref_mensagem"] for r in c.db.query("SELECT ref_mensagem FROM canal_enviadas")] == ["501"]


def test_a_linha_mais_nova_do_canal_fica_e_o_offset_nao_volta(c: Cena) -> None:
    """Canal parado há mais que o prazo: sem a última linha, o próximo `getUpdates` sairia do offset 0 e o canal pareceria
    vazio (1ª subida), descartando como histórico a mensagem que estivesse esperando."""
    repo = EntradasDoCanal(c.db, canal="telegram", relogio=c.r)
    for i in (10, 11, 12):
        c.entrada("telegram", str(i), ordem=i)
    c.r.avancar(60)
    c.faxinar("telegram")
    assert set(c.linhas("telegram")) == {"12"} and c.linhas("telegram")["12"]["texto"] is None
    assert repo.proximo_offset() == 13 and not repo.canal_vazio()


def test_dentro_do_prazo_o_dedupe_segue_valendo(c: Cena) -> None:
    c.entrada("telegram", "1", ordem=1)
    c.r.avancar(1)
    c.entrada("telegram", "2", ordem=2)
    c.faxinar("telegram")
    repo = EntradasDoCanal(c.db, canal="telegram", relogio=c.r)
    assert not repo.gravar(id_externo="1", ordem=1, tipo="mensagem", do_dono=True, ref_mensagem=None, responde_a=None,
                           texto="de novo", tamanho=7)


def test_cada_canal_com_o_seu_prazo_e_o_trello_leva_os_cartoes_arquivados(c: Cena) -> None:
    c.entrada("telegram", "t1", ordem=1)
    c.entrada("trello", "a1")
    c.cartao("approval:1", "arquivado")
    c.cartao("approval:2", "ativo")
    c.r.avancar(10)
    c.entrada("telegram", "t2", ordem=2)
    c.entrada("trello", "a2")
    f = c.faxinar("trello", dias=5)
    assert set(c.linhas("trello")) == {"a2"} and f.cartoes == 1
    assert set(c.linhas("telegram")) == {"t1", "t2"}                 # o Telegram não é tocado pela faxina do Trello
    assert [r["chave"] for r in c.db.query("SELECT chave FROM trello_cartoes")] == ["approval:2"]   # o ativo fica


def test_o_prazo_minimo_passa_da_janela_de_repeticao_dos_canais() -> None:
    with pytest.raises(ValidationError):
        EntradaDoTelegramCfg(retencao_dias=1)
    with pytest.raises(ValidationError):
        TrelloCfg(retencao_dias=1.5)
    assert EntradaDoTelegramCfg().retencao_dias == 30 and TrelloCfg().retencao_dias == 30


def test_o_servico_faxina_sem_o_telegram_pronto_so_no_lider_e_de_hora_em_hora(c: Cena) -> None:
    """O aviso fora do painel desligado (sem canal) não impede a faxina: o Trello pode estar ligado sozinho."""
    lid = Lideranca(c.db, dono=AQUI, relogio=c.r)
    servico = ServicoDeAvisos(c.cfg, EventBus(c.db, origin=AQUI), FilaDeAvisos(c.db, relogio=c.r), lid,
                              faxina_canais=c.faxina)
    c.entrada("trello", "a1")
    c.r.avancar(40)
    c.entrada("trello", "a2")
    assert servico.canal() is None
    feitas = servico.faxinar_canais()
    assert [f.apagadas for f in feitas] == [0, 1] and set(c.linhas("trello")) == {"a2"}
    assert servico.faxinar_canais() == []                            # a próxima volta só depois de FAXINA_S

    fora = ServicoDeAvisos(c.cfg, EventBus(c.db, origin="servidor-b"), FilaDeAvisos(c.db, relogio=c.r),
                           Lideranca(c.db, dono="servidor-b", relogio=c.r), lider=lambda _n: None,
                           faxina_canais=c.faxina)
    assert fora.faxinar_canais() == []                               # fora do líder, nada
