"""D1 nas receitas (ADR-054, pacote A5): a candidata que concordou com a IA só passa a agir sozinha se não tiver ação
de efeito externo; com `commit`, ela para em `validated` e espera o dono. O legado ativo com efeito fica intacto e
aparece em "Revisar". Toda mudança de status feita pela própria loja vai para `learning_transitions`, com o mesmo
`content_hash` e escopo que o livro lê — é o que faz o veto funcionar: o caminho que uma PESSOA desligou não volta
pelo sistema (a quarentena do sistema, não: a etapa sempre pôde ser reaprendida e provada de novo).

Nível de prova: `simulated` (banco de teste migrado pela fábrica da suíte; nenhum aparelho, nenhuma IA).
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.db import Database
from app.metricas import metricas
from app.modules.learning.application.ports import Ajustes
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.ciclo import ExigeODono, SkillState
from app.modules.learning.domain.vocabulario import LivroKind
from app.modules.learning.infrastructure import ligar_nativos
from app.modules.learning.infrastructure.fontes import FontesSql
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.modules.learning.infrastructure.sql_repository import SqlLearningRepository
from app.taskqueue.recipes import RecipeStore
from app.util import now

from .fake_skills import TS
from .fake_skills import banco as banco_migrado

S = SkillState
PKG = "com.pocqa.messenger"
CHAVE = {"signature": "", "variant": "en-US/xhdpi"}
DONO = "painel:dono"


def _acoes(*, commit: bool, rid: str = "app:id/send") -> list[dict[str, Any]]:
    return [{"tool": "tap", "commit": commit, "why": "a IA explicou", "args": {},
             "selectors": [{"kind": "rid", "rid": rid}]}]


class Mundo:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.decisoes: list[tuple[str, str]] = []
        self.repo = SqlLearningRepository(db, precos=dict)
        self.servico = LearningService(self.repo, FontesSql(db), TriagemDeCredencial(), ajustes=Ajustes, relogio=now,
                                       retencao_de_logs_dias=lambda: 14)
        self.store = RecipeStore(db)
        ligar_nativos.ligar(self.servico, self.repo, db, receitas=self.store,
                            decidir=lambda texto, run_id: self.decisoes.append((run_id, texto)))

    def salva(self, passo: str = "enviar", *, commit: bool, candidate: bool = True, learned_from: str = "s1",
              replaces: int | None = None, rid: str = "app:id/send") -> int | None:
        return self.store.save(package=PKG, app_version="1.0(1)", step_hash=f"h-{passo}", step_key=passo,
                               actions=_acoes(commit=commit, rid=rid), learned_from=learned_from,
                               candidate=candidate, replaces=replaces, **CHAVE)

    def procura(self, passo: str = "enviar") -> Any:
        return self.store.find(PKG, "1.0(1)", f"h-{passo}", **CHAVE)

    def status(self, rid: int) -> str:
        return str(self.db.scalar("SELECT status FROM recipes WHERE id=?", (rid,)))

    def trilha(self, rid: int) -> list[tuple[str | None, str, str]]:
        return [(t.from_state.value if t.from_state else None, t.to_state.value, t.decided_by)
                for t in self.repo.trilha(f"receita:{rid}")]


@pytest.fixture
def mundo(tmp_path: Path) -> Iterator[Mundo]:
    db = banco_migrado(tmp_path, "d1-receitas.sqlite3")
    yield Mundo(db)
    db.close()


# ------------------------------------------------------------------ promoção com e sem efeito
def test_promocao_com_commit_para_em_validated_e_find_nao_devolve(mundo: Mundo) -> None:
    rid = mundo.salva(commit=True)
    assert rid
    assert mundo.store.shadow(rid, True, promote_after=2) is False
    # 2ª concordância seguida: provou-se, mas tem efeito externo — não passa a agir (a resposta diz isso: False)
    assert mundo.store.shadow(rid, True, promote_after=2) is False
    assert mundo.status(rid) == "validated"
    metricas.limpar()
    assert mundo.procura() is None                                   # a etapa não a vê: a IA segue conduzindo
    assert metricas.valor("receita.consulta", resultado="ausente") == 1
    # espera o dono em "Para aprovar"; a trilha diz quem fez cada passo
    assert ("receita", str(rid)) in {(e.kind.value, e.ref) for e in mundo.servico.pendentes()}
    assert mundo.trilha(rid) == [(None, "candidate", "sistema"), ("candidate", "validated", "sistema")]
    motivo = mundo.repo.trilha(f"receita:{rid}")[-1].reason
    assert "efeito externo" in motivo and "dono" in motivo
    # o sistema não a publica nem pelo livro (D1 no domínio e na segunda camada)
    with pytest.raises(ExigeODono):
        mundo.servico.mudar_estado(LivroKind.RECEITA, str(rid), S.PUBLISHED, by="sistema", reason="tentar")
    assert mundo.status(rid) == "validated"
    # o dono aprova: agora sim ela age, e a busca a devolve
    mundo.servico.mudar_estado(LivroKind.RECEITA, str(rid), S.PUBLISHED, by=DONO, reason="aprovada em lote")
    assert mundo.status(rid) == "active" and mundo.procura()["id"] == rid
    assert mundo.trilha(rid)[-1] == ("validated", "published", DONO)


def test_sem_commit_segue_o_aberto_receitas(mundo: Mundo) -> None:
    rid = mundo.salva("abrir", commit=False)
    assert rid
    assert mundo.store.shadow(rid, True, promote_after=2) is False
    assert mundo.store.shadow(rid, True, promote_after=2) is True    # sem efeito: publicada pelo sistema (D1)
    assert mundo.status(rid) == "active" and mundo.procura("abrir")["id"] == rid
    assert mundo.trilha(rid) == [(None, "candidate", "sistema"), ("candidate", "published", "sistema")]
    assert ("receita", str(rid)) not in {(e.kind.value, e.ref) for e in mundo.servico.pendentes()}


def test_validada_segura_a_chave_e_nao_enche_a_fila_do_dono(mundo: Mundo) -> None:
    rid = mundo.salva(commit=True)
    assert rid
    for _ in range(2):
        mundo.store.shadow(rid, True, promote_after=2)
    assert mundo.status(rid) == "validated"
    # a IA segue conduzindo a etapa e comprovando o mesmo caminho: nenhuma versão nova enquanto o dono não decide
    assert mundo.salva(commit=True, learned_from="s2") is None
    assert mundo.salva(commit=True, learned_from="s3", rid="app:id/outro") is None
    assert mundo.db.scalar("SELECT COUNT(*) FROM recipes") == 1


def test_ativas_com_commit_de_antes_ficam_intactas_e_aparecem_em_revisar(mundo: Mundo) -> None:
    db = mundo.db
    db.execute("INSERT INTO recipes(app_package, app_version, app_signature, variant, step_hash, step_key, version,"
               " status, actions, learned_from_step, created_at) VALUES (?,?,?,?,?,?,1,'active',?,?,?)",
               (PKG, "1.0(1)", "", "en-US/xhdpi", "h-enviar", "enviar", json.dumps(_acoes(commit=True)), "s0", TS))
    antiga = int(db.scalar("SELECT id FROM recipes WHERE step_hash='h-enviar'"))
    # nada do D1 rebaixa o legado: a busca a devolve, a sombra só acumula a taxa, o save não a troca
    assert mundo.procura()["id"] == antiga
    assert mundo.store.shadow(antiga, True, promote_after=2) is False
    assert mundo.salva(commit=True) is None
    assert mundo.status(antiga) == "active" and mundo.trilha(antiga) == []
    assert ("receita", str(antiga)) in {(e.kind.value, e.ref) for e in mundo.servico.revisar()}


# ------------------------------------------------------------------ trilha das mudanças da própria loja
def test_as_transicoes_da_loja_gravam_learning_transitions(mundo: Mundo) -> None:
    v1 = mundo.salva("abrir", commit=False)
    assert v1
    assert mundo.store.shadow(v1, False, promote_after=2) is False   # divergiu: a IA comprovou outro caminho
    v2 = mundo.salva("abrir", commit=False, learned_from="s2", replaces=v1, rid="app:id/outra_linha")
    assert v2 and mundo.status(v1) == "superseded"
    assert mundo.trilha(v1) == [(None, "candidate", "sistema"), ("candidate", "deprecated", "sistema")]
    assert "v2" in mundo.repo.trilha(f"receita:{v1}")[-1].reason
    # promovida, depois 3 falhas seguidas ao reproduzir: a quarentena (rebaixar é automático) fica na trilha
    assert mundo.store.shadow(v2, True, promote_after=1) is True
    assert [mundo.store.result(v2, False) for _ in range(3)] == [False, False, True]
    assert mundo.trilha(v2)[-1] == ("published", "disabled", "sistema")
    assert "3 falhas seguidas" in mundo.repo.trilha(f"receita:{v2}")[-1].reason
    # o treino é da pessoa: a trilha diz de qual demonstração veio
    v3 = mundo.salva("abrir", commit=False, candidate=False, learned_from="training:t1")
    assert v3 and mundo.trilha(v3) == [(None, "published", "training:t1")]
    assert mundo.trilha(v2)[-1] == ("disabled", "deprecated", "sistema")
    # o hash e o escopo da trilha são os do livro: é por eles que o veto e o "Revisar" a encontram
    entrada = FontesSql(mundo.db).receita(str(v3))
    assert entrada is not None
    [t] = mundo.repo.trilha(f"receita:{v3}")
    assert (t.content_hash, t.scope_key, t.app_version) == (entrada.content_hash, entrada.scope_key, "1.0(1)")


def test_trilha_da_receita_aponta_a_execucao_que_a_ensinou(mundo: Mundo) -> None:
    db = mundo.db
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, instance_ids, created_at)"
               " VALUES ('r-1','k-r-1','x','execute','completed','[\"android-01\"]',?)", (TS,))
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version) VALUES"
               " ('r-1:o1','r-1','android-01','succeeded',1)")
    db.execute("INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
               " postcondition, timeout_s, max_attempts, status) VALUES ('r-1:s1','r-1','r-1:o1','android-01',1,1,"
               "'abrir','abrir','abrir','{}',60,3,'succeeded')")
    rid = mundo.salva("abrir", commit=False, learned_from="r-1:s1")
    assert rid
    assert [t.run_id for t in mundo.repo.trilha(f"receita:{rid}")] == ["r-1"]


# ------------------------------------------------------------------ veto: o que a pessoa desligou não volta
def test_caminho_que_uma_pessoa_desligou_nao_volta_pelo_sistema(mundo: Mundo) -> None:
    rid = mundo.salva(commit=False)
    assert rid and mundo.store.shadow(rid, True, promote_after=1) is True
    mundo.servico.mudar_estado(LivroKind.RECEITA, str(rid), S.DISABLED, by=DONO, reason="tocou o botão errado")
    assert mundo.status(rid) == "quarantined"
    # a IA comprova de novo o MESMO caminho: o sistema não o grava outra vez
    assert mundo.salva(commit=False, learned_from="s2") is None
    assert mundo.status(rid) == "quarantined" and mundo.db.scalar("SELECT COUNT(*) FROM recipes") == 1
    # outro caminho, sim (e ele toma o lugar da desligada)
    outro = mundo.salva(commit=False, learned_from="s3", rid="app:id/outro_botao")
    assert outro and mundo.status(rid) == "superseded"
    # a pessoa pode ensinar o caminho que ela mesma desligou (treino não passa pelo veto)
    mundo.servico.mudar_estado(LivroKind.RECEITA, str(outro), S.DISABLED, by=DONO, reason="também não")
    assert mundo.salva(commit=False, candidate=False, learned_from="training:t9")


def test_candidata_com_caminho_vetado_nao_e_promovida(mundo: Mundo) -> None:
    rid = mundo.salva(commit=False)
    assert rid and mundo.store.shadow(rid, True, promote_after=1) is True
    mundo.servico.mudar_estado(LivroKind.RECEITA, str(rid), S.DISABLED, by=DONO, reason="não quero")
    # a mesma chave numa candidata gravada por fora do D1 (loja crua, sem ouvinte): a sombra do central não a promove
    crua = RecipeStore(mundo.db).save(package=PKG, app_version="1.0(1)", step_hash="h-enviar", step_key="enviar",
                                      actions=_acoes(commit=False), learned_from="s2", candidate=True, **CHAVE)
    assert crua
    assert mundo.store.shadow(crua, True, promote_after=1) is False
    assert mundo.status(crua) == "candidate"


def test_quarentena_do_sistema_nao_veta_o_reaprendizado(mundo: Mundo) -> None:
    rid = mundo.salva(commit=False)
    assert rid and mundo.store.shadow(rid, True, promote_after=1) is True
    assert [mundo.store.result(rid, False) for _ in range(3)][-1] is True
    # a etapa volta a ser aprendida com o mesmo caminho: nova candidata, que prova de novo em sombra antes de agir
    v2 = mundo.salva(commit=False, learned_from="s2")
    assert v2 and mundo.status(v2) == "candidate" and mundo.status(rid) == "superseded"
