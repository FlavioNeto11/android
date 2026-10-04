"""Item 28.25 — o resumo agrupado: no máximo UMA mensagem por janela, só com decisão nova, sem dado pessoal.

Prova `simulated` (`arquivo::teste`): relógio e canal falsos, banco do harness; nenhuma chamada ao Telegram.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from pydantic import SecretStr

from app.config import Config
from app.db import Database
from app.events import EventBus
from app.modules.avisos.infrastructure.fila_sql import FilaDeAvisos
from app.modules.avisos.infrastructure.servico import ServicoDeAvisos
from app.modules.decisoes.domain.resumo import DecisaoParaResumir, corpo_do_resumo
from app.modules.decisoes.infrastructure.adaptador_sql import AdaptadorDeDecisoes
from app.modules.decisoes.infrastructure.estado_sql import EstadoDasDecisoes
from app.modules.decisoes.infrastructure.registro_sql import RegistroSql
from app.modules.decisoes.infrastructure.resumo_sql import ResumoDasDecisoes
from app.modules.decisoes.infrastructure.servico import ServicoDeDecisoes
from app.shared.decisoes import NovaDecisao
from app.taskqueue.travas import Lideranca
from app.util import now, to_iso

from .conftest import make_config

PAINEL = "http://painel.local:8000/central"
PESSOAIS = ["Fulana", "fulano@exemplo.com", "192.168.1.55", "+55 11 98888-7777", "comando secreto", "@perfil_da_fulana"]


class Relogio:
    def __init__(self) -> None:
        self.t = now()

    def __call__(self) -> datetime:
        return self.t

    def avancar(self, s: float) -> None:
        self.t += timedelta(seconds=s)


class CanalFalso:
    def __init__(self) -> None:
        self.enviados: list[tuple[str, str, str | None]] = []

    async def enviar(self, titulo: str, corpo: str, link: str | None) -> None:
        self.enviados.append((titulo, corpo, link))


class Cena:
    def __init__(self, tmp_path: Path, *, ligado: bool = True) -> None:
        self.cfg: Config = make_config(tmp_path)
        self.cfg.ensure_dirs()
        self.cfg.file.avisos.enabled = ligado
        self.cfg.file.avisos.url_painel = PAINEL
        self.cfg.env.telegram_bot_token = SecretStr("987654321:AAFake-token-de-teste")
        self.cfg.env.telegram_chat_id = SecretStr("42")
        self.db = Database(self.cfg.db_dsn)
        self.db.migrate()
        self.r = Relogio()
        self.canal = CanalFalso()
        self.lid = Lideranca(self.db, dono="a", relogio=self.r)
        self.avisos = ServicoDeAvisos(self.cfg, EventBus(self.db, origin="a"), FilaDeAvisos(self.db, relogio=self.r),
                                      self.lid, canal=self.canal)
        self.registro = RegistroSql(self.db, relogio=self.r)
        self.estado = EstadoDasDecisoes(self.db)
        self.resumo = ResumoDasDecisoes(self.db, self.estado, enfileirar=self.avisos.enfileirar_aviso,
                                        pode_avisar=lambda: self.avisos.ligado and self.avisos.canal() is not None,
                                        relogio=self.r)
        self.lider: int | None = 1
        self.servico = ServicoDeDecisoes(self.cfg, AdaptadorDeDecisoes(self.db, self.registro, self.estado),
                                         self.resumo, lider=lambda _n: self.lider)

    def decidir(self, n: int, *, fila: str = "pergunta", regra: str = "31.43-pergunta-24h", prefixo: str = "d",
                efeito: str = "Pergunta encerrada.", fatos: dict | None = None) -> None:
        for i in range(n):
            self.registro.registrar(NovaDecisao(fila, f"{prefixo}{i}", f"{prefixo}-{fila}-{regra}-{i}", regra, efeito,
                                                fatos if fatos is not None else {"horas": 24}, to_iso(self.r.t)))

    def mensagens(self) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM avisos_entregas WHERE tipo='decisoes.resumo'") or 0)

    def entregar(self) -> None:
        asyncio.run(self.avisos.entregar_uma_vez())

    def resumir(self):  # noqa: ANN201
        c = self.cfg.file.avisos
        return self.resumo.resumir(janela_min=c.decisoes_automaticas.janela_min,
                                   desfazer_dias=c.decisoes_automaticas.desfazer_dias, validade_h=c.validade_h,
                                   url_painel=c.url_painel)


@pytest.fixture
def cena(tmp_path: Path) -> Cena:
    return Cena(tmp_path)


def test_sem_decisao_nova_nada_sai(cena: Cena) -> None:
    assert cena.resumir() is None
    assert cena.mensagens() == 0
    cena.decidir(2)
    assert cena.resumir() is not None
    cena.r.avancar(3 * 3600)                       # janela vencida, mas nenhuma decisão NOVA desde o último resumo
    assert cena.resumir() is None
    assert cena.mensagens() == 1


def test_varias_decisoes_na_janela_viram_uma_mensagem_so(cena: Cena) -> None:
    cena.decidir(3)
    cena.decidir(2, fila="objetivo", regra="31.43-objetivo-24h", prefixo="o")
    aviso = cena.resumir()
    assert aviso is not None and cena.mensagens() == 1
    assert "3 perguntas sem resposta havia 24 h foram encerradas" in aviso.corpo
    assert "2 objetivos que esperavam uma execução já terminada foram encerrados" in aviso.corpo
    assert "até 7 dias" in aviso.corpo
    assert aviso.link == PAINEL + "/#/pendencias?aba=decididas"
    # decisão nova DENTRO da janela espera a próxima: nada de uma mensagem por decisão
    cena.decidir(1, prefixo="nova")
    cena.r.avancar(30 * 60)
    assert cena.resumir() is None and cena.mensagens() == 1
    # a janela passa: sai UMA mensagem, só com a decisão nova
    cena.r.avancar(31 * 60)
    segundo = cena.resumir()
    assert segundo is not None and cena.mensagens() == 2
    assert "1 pergunta sem resposta havia 24 h foi encerrada" in segundo.corpo and "perguntas" not in segundo.corpo


def test_a_janela_sobrevive_a_reinicio(cena: Cena) -> None:
    cena.decidir(1)
    assert cena.resumir() is not None
    cena.decidir(1, prefixo="x")
    novo = ResumoDasDecisoes(cena.db, EstadoDasDecisoes(cena.db), enfileirar=cena.avisos.enfileirar_aviso,
                             pode_avisar=lambda: True, relogio=cena.r)           # outro processo, mesma tabela
    assert novo.resumir(janela_min=60, desfazer_dias=7, validade_h=24, url_painel=PAINEL) is None


def test_o_texto_nao_leva_dado_pessoal_nem_o_conteudo_da_decisao(cena: Cena) -> None:
    cena.decidir(1, efeito="Fulana pediu fulano@exemplo.com em 192.168.1.55", prefixo="Fulana",
                 fatos={"contato": "+55 11 98888-7777", "texto": "comando secreto", "perfil": "@perfil_da_fulana"})
    cena.decidir(2, fila="aprendizado", regra="auto:regra_que_nao_conheco v1", prefixo="Fulana2",
                 efeito="comando secreto")
    aviso = cena.resumir()
    assert aviso is not None
    tudo = "\n".join([aviso.titulo, aviso.corpo, aviso.link or "", aviso.chave, aviso.tipo])
    for dado in PESSOAIS:
        assert dado not in tudo, dado
    assert "regra_que_nao_conheco" not in tudo, "a string da regra não vai para o Telegram"
    assert "2 aprendizados foram decididos pela plataforma" in aviso.corpo


def test_frases_fixas_de_cada_fila_e_regra_desconhecida_vira_outras() -> None:
    d = [DecisaoParaResumir(1, "aprendizado", "auto:qa_para_aprovar v1"),
         DecisaoParaResumir(2, "aprendizado", "auto:qa_revisar v1"), DecisaoParaResumir(3, "pedido", "x"),
         DecisaoParaResumir(4, "qualquer", "y"), DecisaoParaResumir(5, "qualquer", "z")]
    corpo = corpo_do_resumo(d, 1) or ""
    assert "1 aprendizado que esperava aprovação foi decidido pela plataforma" in corpo
    assert "1 aprendizado em revisão foi confirmado pela plataforma" in corpo
    assert "1 pedido que esperava uma pessoa foi encerrado" in corpo
    assert "2 outras decisões da plataforma" in corpo
    assert corpo.endswith("Dá para desfazer pelo painel em até 1 dia.")
    assert corpo_do_resumo([], 7) is None


def test_decisao_desfeita_antes_do_resumo_e_a_velha_nao_contam(cena: Cena) -> None:
    cena.decidir(1, prefixo="velha")
    cena.r.avancar(25 * 3600)                                   # passou de avisos.validade_h: notícia velha
    cena.decidir(1, prefixo="desfeita")
    cena.decidir(1, prefixo="boa")
    desfeita = [d for d in cena.registro.listar() if d.item_ref == "desfeita0"][0]
    cena.registro.marcar_desfeita(desfeita.id, por="dono", motivo=None)
    aviso = cena.resumir()
    assert aviso is not None and "1 pergunta" in aviso.corpo
    assert cena.db.scalar("SELECT COUNT(*) FROM decisoes_automaticas WHERE resumida_em IS NULL") == 0


def test_so_velhas_marca_sem_mensagem(cena: Cena) -> None:
    cena.decidir(2)
    cena.r.avancar(25 * 3600)
    assert cena.resumir() is None and cena.mensagens() == 0
    assert cena.db.scalar("SELECT COUNT(*) FROM decisoes_automaticas WHERE resumida_em IS NULL") == 0


def test_canal_desligado_nao_marca_nada_e_o_resumo_sai_quando_ligar(tmp_path: Path) -> None:
    c = Cena(tmp_path, ligado=False)
    c.decidir(2)
    assert c.resumir() is None and c.mensagens() == 0
    assert c.db.scalar("SELECT COUNT(*) FROM decisoes_automaticas WHERE resumida_em IS NULL") == 2
    c.cfg.file.avisos.enabled = True
    assert c.resumir() is not None and c.mensagens() == 1


def test_queda_entre_enfileirar_e_marcar_nao_duplica(cena: Cena) -> None:
    cena.decidir(2)
    primeira = cena.resumir()
    assert primeira is not None
    # simula a queda: a linha do aviso ficou, mas as decisões voltaram a "não resumidas" e a janela não foi gravada
    cena.db.execute("UPDATE decisoes_automaticas SET resumida_em=NULL")
    cena.db.execute("DELETE FROM decisoes_automaticas_estado WHERE chave='ultimo_resumo_em'")
    assert cena.resumir() is not None
    assert cena.mensagens() == 1, "o mesmo resumo virou duas mensagens"


def test_sem_url_do_painel_a_mensagem_leva_so_o_texto(cena: Cena) -> None:
    cena.cfg.file.avisos.url_painel = None
    cena.decidir(1)
    aviso = cena.resumir()
    assert aviso is not None and aviso.link is None


def test_o_laco_so_resume_no_lider_mas_recolhe_sempre(cena: Cena) -> None:
    cena.db.execute("INSERT INTO events(ts, kind, level, run_id, message, data) VALUES (?,?,?,?,?,?)",
                    (to_iso(cena.r.t), "run.updated", "info", "r-1", "m",
                     '{"run":{"id":"r-1"},"vencimento":{"regra":"31.43-pergunta-24h","horas":24}}'))
    cena.lider = None
    novas, aviso = cena.servico.uma_volta()
    assert (novas, aviso) == (1, None) and cena.mensagens() == 0
    cena.lider = 1
    novas, aviso = cena.servico.uma_volta()
    assert novas == 0 and aviso is not None and cena.mensagens() == 1


def test_a_mensagem_chega_ao_canal_pelo_caminho_dos_avisos(cena: Cena) -> None:
    cena.decidir(3)
    cena.resumir()
    cena.entregar()
    assert len(cena.canal.enviados) == 1
    titulo, corpo, link = cena.canal.enviados[0]
    assert titulo.startswith("ANA: ") and "3 perguntas" in corpo and link and link.endswith("?aba=decididas")
