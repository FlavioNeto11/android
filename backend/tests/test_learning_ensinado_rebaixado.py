"""30.80 B (B2 do mapa do ensino): o ensinado que o SISTEMA tirou de uso avisa quem ensinou.

A receita ou o fluxo demonstrado no modo treinamento (`training:<sessão>`) caía em quarentena ou era desligado pela
obsolescência em silêncio: a etapa voltava para a IA e ninguém sabia. Agora sai UM evento por transição:
- `learning.ensinado_sem_receita` (`warn`): nada ativo ficou no lugar;
- `learning.ensinado_rebaixado` (`info`): outra receita ou fluxo ativo segura a etapa.
O payload é a lista fechada combinada com a Canais (28.50), e o `desde` é o instante da transição na trilha.
O gesto de uma pessoa, o item que a IA aprendeu e o nascimento não avisam.

Nível de prova: `simulated` (banco de teste migrado pela fábrica da suíte; barramento falso; nenhuma IA).
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.db import Database
from app.modules.learning.application.ports import Ajustes
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.ciclo import SkillState
from app.modules.learning.domain.ensinado import (CAMPOS_DO_PAYLOAD, TIPO_REBAIXADO, TIPO_SEM_RECEITA,
                                                  rebaixado_pelo_sistema, sessao_de_treino)
from app.modules.learning.domain.livro import EntradaDoLivro
from app.modules.learning.domain.vocabulario import LivroKind, Origem
from app.modules.learning.infrastructure import ligar_nativos
from app.modules.learning.infrastructure.ensinado_sql import LeitorDoEnsinadoSql
from app.modules.learning.infrastructure.eventos import EventosNoBarramento
from app.modules.learning.infrastructure.fontes import FontesSql
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.modules.learning.infrastructure.sql_repository import SqlLearningRepository
from app.taskqueue.recipes import QUARANTINE_AFTER, RecipeStore

from .fake_skills import banco as banco_migrado
from .fake_skills import fluxo

PKG = "com.pocqa.messenger"
CHAVE = {"signature": "", "variant": "en-US/xhdpi"}
DONO = "painel:dono"
TIPOS = (TIPO_REBAIXADO, TIPO_SEM_RECEITA)
#: O relógio do serviço, longe do da trilha: o `desde` tem de vir da trilha, nunca dele.
RELOGIO = datetime(2001, 1, 1, tzinfo=UTC)


@dataclass
class Barramento:
    eventos: list[tuple[str, str, str, dict[str, object]]] = field(default_factory=list)
    falha: bool = False

    def emit(self, kind: str, message: str, *, level: str = "info", instance_id: str | None = None,
             data: dict[str, object] | None = None) -> object:
        if self.falha and kind in TIPOS:
            raise RuntimeError("barramento fora")
        self.eventos.append((kind, message, level, dict(data or {})))
        return None

    def do_ensinado(self) -> list[tuple[str, str, str, dict[str, object]]]:
        return [e for e in self.eventos if e[0] in TIPOS]


class Mundo:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.bus = Barramento()
        self.repo = SqlLearningRepository(db, precos=dict)
        porta = EventosNoBarramento(self.bus)
        self.servico = LearningService(self.repo, FontesSql(db), TriagemDeCredencial(), ajustes=Ajustes, relogio=lambda: RELOGIO,
                                       retencao_de_logs_dias=lambda: 14, eventos=porta, ensinado=porta,
                                       leitor_do_ensinado=LeitorDoEnsinadoSql(db),
                                       isolar_o_aviso=db.savepoint)                 # como a montagem do central
        self.store = RecipeStore(db)
        ligar_nativos.ligar(self.servico, self.repo, db, receitas=self.store, decidir=lambda texto, run_id: None)

    def salva(self, *, learned_from: str, passo: str = "abrir", rid: str = "app:id/abrir") -> int:
        novo = self.store.save(package=PKG, app_version="1.0(1)", step_hash=f"h-{passo}", step_key=passo,
                               learned_from=learned_from, candidate=False, **CHAVE,
                               actions=[{"tool": "tap", "commit": False, "why": "abrir", "args": {},
                                         "selectors": [{"kind": "rid", "rid": rid}]}])
        assert novo
        return novo

    def quarentena(self, rid: int) -> None:
        for _ in range(QUARANTINE_AFTER):
            self.store.result(rid, False)
        assert self.status(rid) == "quarantined"

    def status(self, rid: int) -> str:
        return str(self.db.scalar("SELECT status FROM recipes WHERE id=?", (rid,)))

    def ultima_da_trilha(self, item_ref: str) -> str:
        return str(self.db.scalar("SELECT decided_at FROM learning_transitions WHERE item_ref=? ORDER BY id DESC"
                                  " LIMIT 1", (item_ref,)))


@pytest.fixture
def mundo(tmp_path: Path) -> Iterator[Mundo]:
    db = banco_migrado(tmp_path, "ensinado.sqlite3")
    yield Mundo(db)
    db.close()


# ------------------------------------------------------------------ pela loja: a quarentena por falhas seguidas
def test_ensinado_em_quarentena_sem_outra_ativa_avisa_que_a_etapa_ficou_sem_receita(mundo: Mundo) -> None:
    rid = mundo.salva(learned_from="training:trn-AbC_1-x")
    assert mundo.bus.do_ensinado() == []                                  # o nascimento não avisa
    # uma versão antiga, já substituída, na mesma chave: não está em uso, não segura o lugar
    mundo.db.execute("INSERT INTO recipes(app_package, app_version, app_signature, variant, step_hash, step_key,"
                     " version, status, actions, learned_from_step, created_at) SELECT app_package, app_version,"
                     " app_signature, variant, step_hash, step_key, 0, 'superseded', actions, 's0', created_at"
                     " FROM recipes WHERE id=?", (rid,))
    mundo.quarentena(rid)
    [(tipo, mensagem, nivel, dados)] = mundo.bus.do_ensinado()
    assert (tipo, nivel) == (TIPO_SEM_RECEITA, "warn")
    assert tuple(dados) == CAMPOS_DO_PAYLOAD                             # a lista fechada, nada além dela
    assert dados == {"kind": "receita", "ref": str(rid), "app": PKG, "treino": "trn-AbC_1-x",
                     "sem_receita_ativa": True, "para": "quarantined",
                     "desde": mundo.ultima_da_trilha(f"receita:{rid}")}
    assert "app:id/abrir" not in mensagem and "abrir" not in mensagem   # nada do conteúdo da receita


def test_com_outra_ativa_no_lugar_o_aviso_e_de_rotina(mundo: Mundo) -> None:
    rid = mundo.salva(learned_from="training:trn-1")
    # outra ativa na MESMA chave (a loja não cria duas; aqui é só para o leitor ter o que achar)
    mundo.db.execute("INSERT INTO recipes(app_package, app_version, app_signature, variant, step_hash, step_key,"
                     " version, status, actions, learned_from_step, created_at) SELECT app_package, app_version,"
                     " app_signature, variant, step_hash, step_key, version+1, 'active', actions, 's9', created_at"
                     " FROM recipes WHERE id=?", (rid,))
    mundo.quarentena(rid)
    [(tipo, _, nivel, dados)] = mundo.bus.do_ensinado()
    assert (tipo, nivel, dados["sem_receita_ativa"]) == (TIPO_REBAIXADO, "info", False)


def test_receita_que_a_ia_aprendeu_nao_avisa(mundo: Mundo) -> None:
    rid = mundo.salva(learned_from="s1")
    mundo.quarentena(rid)
    assert mundo.bus.do_ensinado() == []


# ------------------------------------------------------------------ pelo Livro: a obsolescência e a pessoa
def test_o_sistema_pelo_livro_avisa_e_a_pessoa_nao(mundo: Mundo) -> None:
    pelo_sistema = mundo.salva(learned_from="training:trn-1", passo="a")
    pela_pessoa = mundo.salva(learned_from="training:trn-1", passo="b")
    # o caminho da obsolescência (`application/obsolescencia.py`): `mudar_estado(by='sistema')`
    mundo.servico.mudar_estado(LivroKind.RECEITA, str(pelo_sistema), SkillState.DISABLED, by="sistema",
                               reason="catalogo_sem_efeito: o catálogo não respalda")
    mundo.servico.mudar_estado(LivroKind.RECEITA, str(pela_pessoa), SkillState.DISABLED, by=DONO, reason="não serve")
    [(tipo, _, _, dados)] = mundo.bus.do_ensinado()
    assert (tipo, dados["ref"], dados["para"]) == (TIPO_SEM_RECEITA, str(pelo_sistema), "quarantined")
    assert dados["desde"] == mundo.ultima_da_trilha(f"receita:{pelo_sistema}")


def test_fluxo_ensinado_desligado_pelo_sistema_avisa(mundo: Mundo) -> None:
    fluxo(mundo.db, "curtir-ensinado", "curtir {perfil}", source="training:trn-2", source_run_id=None)
    fluxo(mundo.db, "curtir-da-ia", "curtar {perfil}")
    for ref in ("curtir-ensinado", "curtir-da-ia"):
        mundo.servico.mudar_estado(LivroKind.FLUXO, ref, SkillState.DISABLED, by="sistema", reason="obsoleto")
    [(tipo, _, _, dados)] = mundo.bus.do_ensinado()
    assert (tipo, dados["kind"], dados["ref"], dados["treino"], dados["para"]) == (
        TIPO_SEM_RECEITA, "fluxo", "curtir-ensinado", "trn-2", "disabled")


def test_o_barramento_fora_nao_derruba_a_transicao_do_livro(mundo: Mundo) -> None:
    rid = mundo.salva(learned_from="training:trn-1")
    mundo.bus.falha = True
    mundo.servico.mudar_estado(LivroKind.RECEITA, str(rid), SkillState.DISABLED, by="sistema", reason="obsoleto")
    assert mundo.status(rid) == "quarantined" and mundo.bus.do_ensinado() == []


# ------------------------------------------------------------------ leitura da Ferramentas (S2, N1, N3)
def test_a_segunda_transicao_pela_loja_nao_avisa_de_novo(mundo: Mundo) -> None:
    rid = mundo.salva(learned_from="training:trn-1")
    mundo.quarentena(rid)
    outra = mundo.store.save(package=PKG, app_version="1.0(1)", step_hash="h-abrir", step_key="abrir",
                             learned_from="s1", candidate=False, **CHAVE,
                             actions=[{"tool": "tap", "commit": False, "why": "abrir", "args": {},
                                       "selectors": [{"kind": "rid", "rid": "app:id/outro"}]}])
    assert outra and mundo.status(rid) == "superseded"                    # quarentena → substituída, pela loja
    assert len(mundo.bus.do_ensinado()) == 1                              # só o da quarentena


def test_a_legada_ensinada_aposentada_pela_chave_completa_e_de_rotina(mundo: Mundo) -> None:
    legada = mundo.salva(learned_from="training:trn-1")                  # assinatura vazia: a chave de antes
    # a provada da chave COMPLETA, já ativa (a promoção pela sombra é de outro teste): a loja aposenta a legada
    mundo.db.execute(
        "INSERT INTO recipes(app_package, app_version, app_signature, variant, step_hash, step_key, version, status,"
        " actions, learned_from_step, created_at) SELECT app_package, app_version, 'sha-1', variant, step_hash,"
        " step_key, version+1, 'active', actions, 's2', created_at FROM recipes WHERE id=?", (legada,))
    linha = mundo.db.one("SELECT * FROM recipes WHERE app_signature='sha-1' AND learned_from_step='s2'")
    assert linha is not None
    mundo.store._aposentar_legadas(linha)  # noqa: SLF001 - o caminho do achado S2, sem a sombra na frente
    assert mundo.status(legada) == "superseded" and mundo.status(int(linha["id"])) == "active"
    [(tipo, _, nivel, dados)] = mundo.bus.do_ensinado()
    assert (tipo, nivel, dados["sem_receita_ativa"], dados["para"]) == (TIPO_REBAIXADO, "info", False, "superseded")


def test_outra_demonstracao_na_mesma_chave_nao_avisa(mundo: Mundo) -> None:
    mundo.salva(learned_from="training:trn-1")
    mundo.store.save(package=PKG, app_version="1.0(1)", step_hash="h-abrir", step_key="abrir",
                     learned_from="training:trn-2", candidate=False, **CHAVE,
                     actions=[{"tool": "tap", "commit": False, "why": "abrir", "args": {},
                               "selectors": [{"kind": "rid", "rid": "app:id/outro"}]}])
    assert mundo.bus.do_ensinado() == []                                  # a pessoa ensinou de novo: não é o sistema


def test_a_falha_do_aviso_na_loja_nao_desfaz_a_trilha_da_quarentena(mundo: Mundo) -> None:
    rid = mundo.salva(learned_from="training:trn-1")
    mundo.bus.falha = True                                                # só os tipos do ensinado falham
    mundo.quarentena(rid)
    trilha = mundo.repo.trilha(f"receita:{rid}")
    assert trilha[-1].to_state is SkillState.DISABLED                     # a linha da quarentena ficou
    assert mundo.bus.do_ensinado() == []


# ------------------------------------------------------------------ a regra pura
def _entrada(estado: SkillState, origem: Origem = Origem.TREINO) -> EntradaDoLivro:
    return EntradaDoLivro(kind=LivroKind.RECEITA, ref="1", state=estado, native_status=None, title="t", app=PKG,
                          origin=origem)


def test_a_regra_so_olha_o_ensinado_em_uso_que_o_sistema_tirou() -> None:
    em_uso, fora = _entrada(SkillState.PUBLISHED), _entrada(SkillState.DISABLED)
    assert rebaixado_pelo_sistema(em_uso, fora, por_sistema=True)
    assert not rebaixado_pelo_sistema(em_uso, fora, por_sistema=False)                      # a pessoa
    assert not rebaixado_pelo_sistema(None, fora, por_sistema=True)                          # nasceu
    assert not rebaixado_pelo_sistema(_entrada(SkillState.CANDIDATE), fora, por_sistema=True)   # não estava em uso
    assert not rebaixado_pelo_sistema(_entrada(SkillState.PUBLISHED, Origem.EXECUCAO), fora, por_sistema=True)
    assert not rebaixado_pelo_sistema(em_uso, em_uso, por_sistema=True)                      # segue em uso
    assert sessao_de_treino("training:trn-x") == "trn-x"
    assert sessao_de_treino("s1") is None and sessao_de_treino("training:") is None and sessao_de_treino(None) is None
