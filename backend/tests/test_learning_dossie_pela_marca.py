"""30.52: o dossiê do curador separa as evidências da versão ANTERIOR do conteúdo; e a pessoa recusa um pedido pendente.

Em 04/10 o parecer lr-1cfb91a981c5f21f (fluxo `enviar-a-mensagem-leitura-31-27-n1-08233`, reaprendido às 10:34Z) citou
os `against` 226 e 233 da marca antiga `[072ae4d8443c]` como se fossem do conteúdo de agora, e pediu "reprodução em
outro aparelho" que já existia (ev 274, android-07, marca `[2c10657f49e4]`). A regra da sombra (30.34) filtra pela
marca; o dossiê não filtrava. O pedido que esse parecer abriu (lv-26679df914e1b809) não tinha como ser recusado.

O que se prova:
- no dossiê de FLUXO, a `lista` traz só a marca de agora (e o legado sem marca); as de outra marca vão em
  `de_versoes_anteriores`, com a explicação `versoes_anteriores_e`. Sem versão anterior, nem a chave; a receita
  não muda;
- `POST /api/aprendizado/validacoes/{id}/recusar` fecha o pedido `pendente` como `recusada/recusada_pela_pessoa`, com
  texto humano. De novo é 409; o desconhecido, 404. O motivo não é chegada para o curador.

Armações: banco migrado (`fake_skills`) com o curador montado como em `test_learning_prova_amostra`, e o Harness
(porta 5640) para a rota. Nível de prova: `simulated`.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.modules.learning.application.nativos import marca_do_conteudo
from app.modules.learning.application.ports import Ajustes
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.curador import OUTRA_VERSAO_DA_EVIDENCIA
from app.modules.learning.domain.validacao import MOTIVO_HUMANO, Motivo
from app.modules.learning.domain.vocabulario import LivroKind
from app.modules.learning.infrastructure.dossies import DossiesSql
from app.modules.learning.infrastructure.fontes import FontesSql
from app.modules.learning.infrastructure.montagem import GuardaDoFluxo
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.modules.learning.infrastructure.sql_repository import SqlLearningRepository
from app.modules.learning.infrastructure.validacoes_sql import RegistroDeValidacoesSql
from app.modules.skills.infrastructure.sql_repository import SqlSkillRepository

from .conftest import Harness
from .fake_skills import ValidadorFalso, fluxo
from .fake_skills import banco as banco_migrado
from .test_learning_classe_do_fluxo import COMENTAR, INSTAGRAM, LER, OUTLOOK, PLANO, CatalogoPorApp
from .test_validacoes_listagem import _cliente

ANTIGA = "[072ae4d8443c]"


def _dossie(tmp_path: Path, linhas: list[tuple[str, str, str]]) -> dict[str, object]:
    """O dossiê do fluxo `comentar-1` com as evidências `(posicao, aparelho, detalhe)`; `{marca}` vira a de agora."""
    db = banco_migrado(tmp_path, "dossie-marca.sqlite3")
    fluxo(db, "comentar-1", "Comente no post de {perfil}", plano=PLANO, status="candidate")
    catalogo = CatalogoPorApp({(INSTAGRAM, "OPEN_PROFILE"): LER, (INSTAGRAM, "CREATE_COMMENT"): COMENTAR,
                               (OUTLOOK, "READ_EMAIL"): LER})
    repo = SqlLearningRepository(db, guarda_do_fluxo=GuardaDoFluxo(db, SqlSkillRepository(db, ValidadorFalso())),
                                 precos=dict)
    servico = LearningService(repo, FontesSql(db), TriagemDeCredencial(), ajustes=Ajustes,
                              relogio=lambda: datetime(2026, 10, 4, 13, 0, tzinfo=UTC),
                              retencao_de_logs_dias=lambda: 14, catalogo_de_risco=catalogo)
    entrada = servico.entrada(LivroKind.FLUXO, "comentar-1")
    assert entrada.content_hash
    marca = marca_do_conteudo(entrada.content_hash)
    for i, (posicao, aparelho, detalhe) in enumerate(linhas, start=1):
        db.execute("INSERT INTO learning_evidence(item_ref, stance, origin_ref, run_id, instance_id, simulated, detail,"
                   " observed_at) VALUES ('fluxo:comentar-1',?,?,?,?,0,?,?)",
                   (posicao, f"run:r-{i}", f"r-{i}", aparelho, detalhe.format(marca=marca), f"2026-10-04T1{i}:00:00Z"))
    d = DossiesSql(db, servico, repo, catalogo).dossie(entrada)
    assert d is not None
    dados = d.como_dados()["evidencias"]
    db.close()
    assert isinstance(dados, dict)
    return dados


def _runs(lista: object) -> list[str]:
    assert isinstance(lista, list)
    return sorted(str(x["run_id"]) for x in lista)


def test_o_dossie_do_fluxo_poe_a_versao_anterior_a_parte(tmp_path: Path) -> None:
    dados = _dossie(tmp_path, [("for", "android-12", ANTIGA + " a execução que o gerou"),
                               ("against", "android-12", ANTIGA + " etapa 2: outra ação"),
                               ("for", "android-12", "{marca} a execução que o gerou"),
                               ("for", "android-07", "{marca} prova: 6/6 etapas comprovadas"),
                               ("for", "android-09", "legado sem marca")])
    assert _runs(dados["lista"]) == ["r-3", "r-4", "r-5"]                # a de agora e o legado sem marca
    assert _runs(dados["de_versoes_anteriores"]) == ["r-1", "r-2"]       # o `against` antigo não pesa na de agora
    assert dados["versoes_anteriores_e"] == OUTRA_VERSAO_DA_EVIDENCIA
    assert dados["total"] == 5                                           # nada sumiu: só mudou de lugar


def test_sem_versao_anterior_o_dossie_fica_como_era(tmp_path: Path) -> None:
    dados = _dossie(tmp_path, [("for", "android-12", "{marca} a execução que o gerou"),
                               ("against", "android-07", "{marca} prova: etapa 2 (seguir) reprovada")])
    assert _runs(dados["lista"]) == ["r-1", "r-2"]
    assert "de_versoes_anteriores" not in dados and "versoes_anteriores_e" not in dados


def _pendente(harness: Harness, pid: str) -> None:
    assert harness.state is not None
    harness.state.db.execute(
        "INSERT INTO learning_validations(id, created_at, updated_at, review_id, item_ref, item_kind, grupo, falta,"
        " comando, estado, expira_em) VALUES (?,?,?,'lr-x','fluxo:f-qa','fluxo','qa','[\"reproducao_em_outro_aparelho\"]',"
        "'cmd','pendente','2026-10-05T13:00:00Z')", (pid, "2026-10-04T13:07:33Z", "2026-10-04T13:07:33Z"))


async def test_a_pessoa_recusa_o_pedido_pendente_pela_rota(harness: Harness) -> None:
    assert harness.state is not None
    _pendente(harness, "lv-pendente")
    async with _cliente(harness) as c:
        r = await c.post("/api/aprendizado/validacoes/lv-pendente/recusar")
        assert r.status_code == 200, r.text
        corpo = r.json()
        assert (corpo["estado"], corpo["motivo"]) == ("recusada", "recusada_pela_pessoa")
        assert corpo["motivo_humano"] == MOTIVO_HUMANO[Motivo.RECUSADA_PELA_PESSOA]
        r = await c.post("/api/aprendizado/validacoes/lv-pendente/recusar")
        assert r.status_code == 409 and r.json()["detail"]["code"] == "pedido_nao_pendente"
        r = await c.post("/api/aprendizado/validacoes/lv-nao-existe/recusar")
        assert r.status_code == 404 and r.json()["detail"]["code"] == "pedido_desconhecido"
    registro = RegistroDeValidacoesSql(harness.state.db)
    assert all(p.id != "lv-pendente" for p in registro.chegadas())      # não volta ao curador como resposta
