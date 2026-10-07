"""O conteúdo legível do detalhe do Livro (30.3, `docs/design/aprendizado-vivo.md` §4): o que a receita, o fluxo, a
habilidade, a lição e a tela FAZEM, montado só do que já está no banco (sem migração, sem coluna nova).

- domínio puro (`domain/conteudo.py`): ações, alvo por seletor, `commit` e efeito, e a REGRA DO SEGREDO (nenhum valor
  de parâmetro nem texto digitado sai; `type_secret` e parâmetro sigiloso aparecem só como `segredo`);
- fontes SQL: capability derivada (origem, mesmo `step_hash`, ambígua, nenhuma), origem (`execucao`, `treino`,
  `desconhecida`), uso, sombra e as versões vizinhas (substitui / substituída por);
- rota `GET /api/aprendizado/{kind}/{ref}` devolvendo `conteudo`.

Nível de prova: `simulated` (banco de teste, sem aparelho nem IA).
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.db import Database
from app.modules.learning.application.ports import Ajustes
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain import conteudo as dominio
from app.modules.learning.domain.ciclo import SkillState
from app.modules.learning.domain.livro import Escopo, NovoItem
from app.modules.learning.domain.vocabulario import LivroKind, SourceKind
from app.modules.learning.infrastructure.fontes import FontesSql
from app.modules.learning.infrastructure.montagem import GuardaDoFluxo
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.modules.learning.infrastructure.sql_repository import SqlLearningRepository
from app.modules.learning.presentation.router import router as learning_router
from app.modules.skills.infrastructure.sql_repository import SqlSkillRepository

from .fake_skills import TS, ValidadorFalso, perfil
from .fake_skills import banco as banco_migrado

PACOTE = "com.instagram.android"
AGORA = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
# Valores que NUNCA podem aparecer na saída: o texto literal digitado, o valor de um parâmetro e o do segredo.
LITERAL = "mensagem-literal-9981"
VALOR_PARAMETRO = "valor-do-parametro-7742"
VALOR_SEGREDO = "senha-hunter2-5530"


def _json(valor: object) -> str:
    return json.dumps(valor, ensure_ascii=False)


# ------------------------------------------------------------------ domínio puro
def test_padroes_do_dominio_nao_divergem_do_executor() -> None:
    """O domínio copia `TEMPLATE_RE` e `SENSITIVE_PARAM` do executor (não pode importá-lo)."""
    from app.taskqueue import recipes

    assert dominio.PARAMETRO_RE.pattern == recipes.TEMPLATE_RE.pattern
    assert dominio.SENSITIVE_PARAM.pattern == recipes.SENSITIVE_PARAM.pattern
    assert dominio.SENSITIVE_PARAM.flags == recipes.SENSITIVE_PARAM.flags


def test_receita_com_commit_diz_qual_acao_faz_o_efeito_e_o_alvo_por_seletor() -> None:
    acoes = [
        {"tool": "tap", "commit": False, "why": "explicação da IA",
         "selectors": [{"kind": "rid+text", "rid": "campo", "text": "Mensagem"}, {"kind": "rid", "rid": "campo"}]},
        {"tool": "tap", "commit": True, "args": {}, "selectors": [{"kind": "rid+desc", "rid": "enviar",
                                                                    "desc": "Enviar"}]}]
    c = dominio.acoes_da_receita(acoes)
    assert [a["ferramenta"] for a in c] == ["tap", "tap"] and [a["commit"] for a in c] == [False, True]
    assert c[0]["alvo"] == [{"tipo": "rid+text", "rid": "campo", "texto": "Mensagem"}, {"tipo": "rid", "rid": "campo"}]
    assert c[1]["alvo"] == [{"tipo": "rid+desc", "rid": "enviar", "desc": "Enviar"}]
    assert "why" not in _json(c) and "explicação" not in _json(c)
    assert dominio.efeito_da_receita(acoes) == {"externo": True, "acoes_commit": [1]}
    assert dominio.efeito_da_receita([{"tool": "tap"}]) == {"externo": False, "acoes_commit": []}
    assert dominio.efeito_da_receita(None) == {"externo": False, "acoes_commit": []}


def test_nenhum_valor_de_parametro_nem_texto_digitado_sai_na_acao() -> None:
    acoes = [
        {"tool": "type_text", "commit": False, "selectors": [{"kind": "rid", "rid": "campo"}],
         "args": {"text": f"{{mensagem}} {LITERAL}", "clear_first": True, "press_enter": False,
                  "valor": VALOR_PARAMETRO}},
        {"tool": "tap", "commit": False, "selectors": [{"kind": "text", "text": "{contato}"}]}]
    saida = dominio.acoes_da_receita(acoes)
    assert saida[0]["parametros"] == ["mensagem"] and saida[0]["digita"] == {
        "limpa_antes": True, "enter": False, "so_parametro": True}
    assert saida[1]["parametros"] == ["contato"] and saida[1]["alvo"] == [{"tipo": "text", "texto": "{contato}"}]
    texto = _json(saida)
    assert LITERAL not in texto and VALOR_PARAMETRO not in texto           # lista branca: args não é copiado em bloco


def test_type_secret_e_parametro_sigiloso_saem_so_como_segredo() -> None:
    acoes = [
        {"tool": "type_secret", "commit": False, "args": {"text": VALOR_SEGREDO, "param": "senha_conta"},
         "selectors": [{"kind": "rid", "rid": "campo_senha"}]},
        {"tool": "type_text", "commit": False, "args": {"text": "{codigo_sms}"}, "selectors": []},
        {"tool": "tap", "commit": False, "selectors": [{"kind": "desc", "desc": "usar {token_de_acesso}"}]}]
    saida = dominio.acoes_da_receita(acoes)
    assert [a["segredo"] for a in saida] == [True, True, True]
    assert all(a["parametros"] == [] for a in saida)                         # nem o nome do parâmetro sigiloso
    texto = _json(saida)
    for proibido in (VALOR_SEGREDO, "senha_conta", "codigo_sms", "token_de_acesso"):
        assert proibido not in texto
    assert saida[2]["alvo"] == [{"tipo": "desc", "desc": "usar {segredo}"}]


def test_acao_malformada_nao_derruba_a_leitura() -> None:
    assert dominio.acoes_da_receita("nada") == []
    assert dominio.acoes_da_receita([42])[0]["ferramenta"] is None


def test_capability_da_receita_origem_template_ambigua_e_nenhuma() -> None:
    origem = dominio.EtapaDeOrigem("r1:a:v1:x", "r1", "SEND_MESSAGE")
    assert dominio.capability_da_receita(origem, ["OUTRA"]) == {"nomes": ["SEND_MESSAGE"], "ambigua": False,
                                                                 "fonte": "origem"}
    sem_cap = dominio.EtapaDeOrigem("r1:a:v1:x", "r1", None)
    assert dominio.capability_da_receita(sem_cap, ["LIKE"]) == {"nomes": ["LIKE"], "ambigua": False,
                                                                 "fonte": "mesmo_step_hash"}
    assert dominio.capability_da_receita(None, ["B", "A", "A", ""]) == {"nomes": ["A", "B"], "ambigua": True,
                                                                         "fonte": "mesmo_step_hash"}
    assert dominio.capability_da_receita(None, []) is None


def test_origem_da_receita() -> None:
    etapa = dominio.EtapaDeOrigem("r9:a:v1:x", "r9", None)
    assert dominio.origem_da_receita("r9:a:v1:x", etapa) == {"tipo": "execucao", "step_id": "r9:a:v1:x",
                                                              "run_id": "r9"}
    assert dominio.origem_da_receita("r9:a:v1:x", None) == {"tipo": "execucao", "step_id": "r9:a:v1:x",
                                                             "run_id": None}
    assert dominio.origem_da_receita("training:sessao-1", None) == {"tipo": "treino", "ref": "sessao-1"}
    assert dominio.origem_da_receita(None, None) == {"tipo": "desconhecida"}
    assert dominio.origem_da_receita("", None) == {"tipo": "desconhecida"}


def test_fluxo_legivel_mostra_etapas_efeito_e_so_nomes_de_argumento() -> None:
    plano = {"steps": [
        {"key": "abrir", "capability": None, "side_effect": False,
         "postcondition": {"kind": "app_foreground", "value": "x", "description": "o app abriu"}},
        {"key": "enviar", "capability": "SEND_MESSAGE", "side_effect": True, "commit_selector": "desc=Enviar",
         "bindings": {"contato": VALOR_PARAMETRO, "senha_da_conta": VALOR_SEGREDO},
         "postcondition": {"kind": "text_visible", "value": "Enviado", "description": "a mensagem aparece"}}]}
    c = dominio.fluxo_legivel(plano, nome="n", comando_modelo="mande {contato}", fonte="training:t1",
                              source_run_id=None)
    assert c["origem"] == {"tipo": "treino", "fonte": "training:t1", "source_run_id": None, "session_id": None, "run_id": None, "step_id": None, "attempt_id": None,
                           "instance_id": None, "operator": None, "ensinado_em": None, "nascido_de_prova": False,
                           "em_uso_real_desde": None}
    assert c["efeito"] == {"externo": True, "etapas_com_efeito": [1]}
    enviar = c["etapas"][1]
    assert enviar["capability"] == "SEND_MESSAGE" and enviar["alvo"] == "desc=Enviar"
    assert enviar["pos_condicao"] == {"tipo": "text_visible", "descricao": "a mensagem aparece"}
    assert enviar["parametros"] == ["contato"] and enviar["segredo"] is True
    assert VALOR_PARAMETRO not in _json(c) and VALOR_SEGREDO not in _json(c) and "senha_da_conta" not in _json(c)
    assert c["etapas"][0]["pos_condicao"]["descricao"] == "o app abriu"
    assert (c["app"], c["apps"], c["etapas"][0]["app"]) == (None, [], None)     # sem app no plano nem exigidos


def test_fluxo_que_atravessa_apps_diz_o_principal_os_exigidos_e_o_de_cada_etapa() -> None:
    """Item 12.1: ler no Outlook e procurar no Instagram. O `app_id` do fluxo é o principal; os dois são exigidos."""
    plano = {"app_id": "instagram", "steps": [
        {"key": "ler", "app_id": "outlook", "capability": "READ_LATEST_SUBJECT", "side_effect": False},
        {"key": "buscar", "capability": "OPEN_PROFILE", "side_effect": False}]}
    c = dominio.fluxo_legivel(plano, nome="n", comando_modelo="c", fonte=None, source_run_id="r",
                              apps=["outlook", "instagram", "outlook"])
    assert (c["app"], c["apps"]) == ("instagram", ["outlook", "instagram"])      # ordem do plano (29.42)
    assert [e["app"] for e in c["etapas"]] == ["outlook", None]


def test_fluxo_sem_correcao_leva_os_ids_de_origem_nulos_e_com_correcao_o_run_da_falha() -> None:
    """31.117: `origem` sempre tem os quatro ids da correção (null sem falha) e o `source_run_id` cai para o run da falha."""
    plano = {"app_id": "instagram", "steps": []}
    sem = dominio.fluxo_legivel(plano, nome="n", comando_modelo="c", fonte="training:t1", source_run_id=None)["origem"]
    assert sem == {"tipo": "treino", "fonte": "training:t1", "source_run_id": None, "session_id": None, "run_id": None,
                   "step_id": None, "attempt_id": None, "instance_id": None, "operator": None, "ensinado_em": None, "nascido_de_prova": False,
                           "em_uso_real_desde": None}
    com = dominio.fluxo_legivel(plano, nome="n", comando_modelo="c", fonte="training:t1", source_run_id=None,
                                correcao={"session_id": "t1", "run_id": "r9", "step_id": "s3", "attempt_id": "s3:a2"})["origem"]
    assert (com["source_run_id"], com["run_id"], com["session_id"], com["step_id"], com["attempt_id"]) == (
        "r9", "r9", "t1", "s3", "s3:a2")
    dona = dominio.fluxo_legivel(plano, nome="n", comando_modelo="c", fonte="training:t1", source_run_id="r0",
                                 correcao={"session_id": "t1", "run_id": "r9", "step_id": "s3", "attempt_id": None})["origem"]
    assert dona["source_run_id"] == "r0"                                      # a coluna do fluxo, quando existe, manda


def test_habilidade_legivel_resume_parametros_e_nos() -> None:
    doc = {"spec": {"parameters": [{"name": "contato"}, {"name": "senha_x"}],
                    "nodes": [{"id": "abrir", "kind": "action"}, {"id": "enviar", "type": "step"}, {"sem": "id"}]}}
    c = dominio.habilidade_legivel(doc, skill_id="ig.x", versao=2, schema_version=1, estado="validated",
                                   source_kind="teaching", source_ref=None, parent_version=1,
                                   command_template="faça {contato}", content_hash="h")
    assert c["parametros"] == ["contato"] and c["total_de_nos"] == 2
    assert c["nos"] == [{"id": "abrir", "tipo": "action"}, {"id": "enviar", "tipo": "step"}]
    assert c["rota"] == "/api/skills/ig.x/versions/2"
    legado = dominio.habilidade_legivel({}, skill_id="ig.y", versao=1, schema_version=0, estado="published",
                                        source_kind="legacy_flow", source_ref="f", parent_version=None,
                                        command_template=None, content_hash="h")
    assert legado["nos"] == [] and legado["parametros"] == []


def test_licao_e_tela_legiveis() -> None:
    licao = dominio.licao_legivel({"modelo": "nota", "acao": "SEND_MESSAGE", "nota": "texto cru da nota",
                                   "alvo": {"tipo": "parametro", "valor": "contato"}},
                                  texto="Ao enviar mensagem, confira o contato.", app=PACOTE,
                                  capability="SEND_MESSAGE", step_hash="", role="actor", tokens=12)
    assert licao["texto"] == "Ao enviar mensagem, confira o contato." and licao["alvo"] == {
        "tipo": "parametro", "valor": "contato"}
    assert licao["escopo"] == {"app": PACOTE, "capability": "SEND_MESSAGE", "step_hash": None, "role": "actor"}
    assert "texto cru da nota" not in _json(licao)                          # só o texto que o ator lê (o summary)
    tela = dominio.tela_legivel({"tela": "aprendida:feed", "tipo": "x", "autenticada": True,
                                 "ids_todos": ["a", "b", 3], "casa": True, "razao": "feed autenticado"})
    assert tela == {"tipo": "tela", "tela": "aprendida:feed", "casa": True, "autenticada": True,
                    "ids_todos": ["a", "b"], "razao": "feed autenticado"}


# ------------------------------------------------------------------ o mundo semeado (SQL)
CHAVE = (PACOTE, "447", "sig", "pt/420")


def _receita(db: Database, passo: str, *, versao: int = 1, status: str = "active", acoes: object = None,
             origem: str | None = None, step_hash: str | None = None, uso: tuple[int, int, int] = (0, 0, 0),
             sombra: tuple[int, int] = (0, 0)) -> int:
    acoes = acoes if acoes is not None else [{"tool": "tap", "args": {}, "selectors": [{"kind": "rid", "rid": "b"}],
                                              "commit": False}]
    return int(db.inserted_id(
        "INSERT INTO recipes(app_package, app_version, app_signature, variant, step_hash, step_key, version, status,"
        " actions, learned_from_step, replay_ok, replay_fail, consecutive_fail, shadow_agree, shadow_total,"
        " last_used_at, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (*CHAVE, step_hash or f"h-{passo}", passo, versao, status, json.dumps(acoes), origem, *uso, *sombra,
         "2026-10-01T10:00:00+00:00" if uso != (0, 0, 0) else None, TS)))


def _etapa(db: Database, run_id: str, chave: str, *, capability: str | None, template_hash: str | None = None,
           app_id: str | None = None, app_ids: list[str] | None = None) -> str:
    """Uma etapa de uma execução (com o run e o objetivo mínimos, criados na primeira vez)."""
    if db.one("SELECT id FROM runs WHERE id=?", (run_id,)) is None:
        db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, instance_ids, app_ids, created_at)"
                   " VALUES (?,?,?,?,?,?,?,?)", (run_id, f"k-{run_id}", "cmd", "execute", "completed",
                                                  json.dumps(["android-01"]), json.dumps(app_ids) if app_ids else None,
                                                  TS))
        db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version) VALUES (?,?,?,?,?)",
                   (f"{run_id}:o1", run_id, "android-01", "succeeded", 1))
    sid = f"{run_id}:android-01:v1:{chave}"
    db.execute("INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
               " postcondition, timeout_s, max_attempts, status, capability, template_hash, app_id)"
               " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (sid, run_id, f"{run_id}:o1", "android-01", 1, 1, chave, chave, chave, "{}", 180, 3, "succeeded",
                capability, template_hash, app_id))
    return sid


class Mundo:
    def __init__(self, db: Database) -> None:
        self.db = db
        db.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('instagram','Instagram',?,0)", (PACOTE,))
        db.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('outro','Outro','com.outro.app',0)")
        habilidades = SqlSkillRepository(db, ValidadorFalso())
        self.repo = SqlLearningRepository(db, guarda_do_fluxo=GuardaDoFluxo(db, habilidades), precos=dict)
        self.fontes = FontesSql(db)
        self.servico = LearningService(self.repo, self.fontes, TriagemDeCredencial(), ajustes=Ajustes,
                                       relogio=lambda: AGORA, retencao_de_logs_dias=lambda: 14)

    def conteudo(self, kind: LivroKind, ref: str | int) -> dict[str, object]:
        c = self.servico.detalhe(kind, str(ref)).conteudo
        assert c is not None
        return c


@pytest.fixture
def mundo(tmp_path: Path) -> Mundo:
    db = banco_migrado(tmp_path, "conteudo.sqlite3")
    yield Mundo(db)
    db.close()


@pytest.fixture
async def cliente(mundo: Mundo) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(learning_router)
    app.state.poc = SimpleNamespace(learning=mundo.servico)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


def test_receita_com_origem_de_execucao_commit_uso_e_sombra(mundo: Mundo) -> None:
    sid = _etapa(mundo.db, "r1", "curtir", capability="LIKE_POST")
    acoes = [{"tool": "tap", "commit": True, "why": "x", "selectors": [{"kind": "rid+desc", "rid": "curtir",
                                                                          "desc": "Curtir"}]}]
    rid = _receita(mundo.db, "curtir", acoes=acoes, origem=sid, uso=(7, 2, 1), sombra=(3, 4))
    c = mundo.conteudo(LivroKind.RECEITA, rid)
    assert c["tipo"] == "receita"
    assert c["identidade"] == {"app": PACOTE, "app_version": "447", "assinatura": "sig", "variante": "pt/420",
                               "step_key": "curtir", "step_hash": "h-curtir", "versao": 1, "estado": "active"}
    assert c["efeito"] == {"externo": True, "acoes_commit": [0]}
    assert c["capability"] == {"nomes": ["LIKE_POST"], "ambigua": False, "fonte": "origem"}
    assert c["origem"] == {"tipo": "execucao", "step_id": sid, "run_id": "r1"}
    assert c["uso"] == {"replay_ok": 7, "replay_fail": 2, "consecutive_fail": 1,
                        "last_used_at": "2026-10-01T10:00:00+00:00"}
    assert c["sombra"] == {"shadow_agree": 3, "shadow_total": 4}
    assert c["substitui"] is None and c["substituida_por"] is None


def test_receita_de_treino_e_sem_origem(mundo: Mundo) -> None:
    treino = mundo.conteudo(LivroKind.RECEITA, _receita(mundo.db, "t", origem="training:trn-7"))
    assert treino["origem"] == {"tipo": "treino", "ref": "trn-7"} and treino["capability"] is None
    sem = mundo.conteudo(LivroKind.RECEITA, _receita(mundo.db, "s", origem=None))
    assert sem["origem"] == {"tipo": "desconhecida"} and sem["capability"] is None
    # Origem que apontava para uma etapa que não existe mais: execução sem run (nunca inventa o run).
    orfa = mundo.conteudo(LivroKind.RECEITA, _receita(mundo.db, "o", origem="rx:android-01:v1:o"))
    assert orfa["origem"] == {"tipo": "execucao", "step_id": "rx:android-01:v1:o", "run_id": None}


def test_capability_pelo_mesmo_step_hash_no_mesmo_app_e_ambigua(mundo: Mundo) -> None:
    _etapa(mundo.db, "ra", "comentar", capability="COMMENT", template_hash="hash-c", app_ids=["instagram"])
    _etapa(mundo.db, "rb", "comentar", capability="COMMENT", template_hash="hash-c", app_ids=["instagram"])
    _etapa(mundo.db, "rc", "comentar", capability="COMENTAR_OUTRO_APP", template_hash="hash-c", app_ids=["outro"])
    unica = mundo.conteudo(LivroKind.RECEITA, _receita(mundo.db, "comentar", step_hash="hash-c", origem="training:t"))
    assert unica["capability"] == {"nomes": ["COMMENT"], "ambigua": False, "fonte": "mesmo_step_hash"}
    _etapa(mundo.db, "rd", "dm", capability="DM_SEND", template_hash="hash-d", app_id="instagram")
    _etapa(mundo.db, "re", "dm", capability="DM_REPLY", template_hash="hash-d", app_ids=["instagram"])
    amb = mundo.conteudo(LivroKind.RECEITA, _receita(mundo.db, "dm", step_hash="hash-d"))
    assert amb["capability"] == {"nomes": ["DM_REPLY", "DM_SEND"], "ambigua": True, "fonte": "mesmo_step_hash"}
    # Etapa sem app conhecido (execução antiga) conta; etapa só de outro app não.
    _etapa(mundo.db, "rf", "seguir", capability="FOLLOW", template_hash="hash-f")
    assert mundo.conteudo(LivroKind.RECEITA, _receita(mundo.db, "seguir", step_hash="hash-f"))["capability"] == {
        "nomes": ["FOLLOW"], "ambigua": False, "fonte": "mesmo_step_hash"}


def test_origem_com_capability_manda_sobre_o_step_hash(mundo: Mundo) -> None:
    sid = _etapa(mundo.db, "rg", "g", capability="DA_ORIGEM", template_hash="hash-g", app_ids=["instagram"])
    _etapa(mundo.db, "rh", "g", capability="OUTRA", template_hash="hash-g", app_ids=["instagram"])
    c = mundo.conteudo(LivroKind.RECEITA, _receita(mundo.db, "g", step_hash="hash-g", origem=sid))
    assert c["capability"] == {"nomes": ["DA_ORIGEM"], "ambigua": False, "fonte": "origem"}


def test_receita_substituta_e_substituida(mundo: Mundo) -> None:
    v1 = _receita(mundo.db, "rolar", versao=1, status="superseded")
    v2 = _receita(mundo.db, "rolar", versao=2, status="quarantined")
    v3 = _receita(mundo.db, "rolar", versao=3, status="candidate")
    outra = _receita(mundo.db, "outra-etapa", versao=1)
    assert mundo.conteudo(LivroKind.RECEITA, v1)["substitui"] is None
    assert mundo.conteudo(LivroKind.RECEITA, v1)["substituida_por"] == {"id": v2, "versao": 2,
                                                                         "estado": "quarantined"}
    meio = mundo.conteudo(LivroKind.RECEITA, v2)
    assert meio["substitui"] == {"id": v1, "versao": 1, "estado": "superseded"}
    assert meio["substituida_por"] == {"id": v3, "versao": 3, "estado": "candidate"}
    assert mundo.conteudo(LivroKind.RECEITA, v3)["substituida_por"] is None
    assert mundo.conteudo(LivroKind.RECEITA, outra)["substitui"] is None       # outra chave não é vizinha


def test_receita_no_banco_nao_vaza_texto_nem_valor(mundo: Mundo) -> None:
    """Mesmo uma receita com valor literal no `args` (de antes das travas do destilador) sai só com os nomes."""
    acoes = [{"tool": "type_text", "commit": False, "selectors": [],
              "args": {"text": f"{{mensagem}}{LITERAL}", "clear_first": True, "press_enter": True}},
             {"tool": "type_secret", "commit": False, "args": {"text": VALOR_SEGREDO},
              "selectors": [{"kind": "rid", "rid": "senha"}]},
             {"tool": "tap", "commit": True, "selectors": [{"kind": "desc", "desc": "Enviar a {contato}"}]}]
    c = mundo.conteudo(LivroKind.RECEITA, _receita(mundo.db, "enviar", acoes=acoes))
    texto = _json(c)
    assert LITERAL not in texto and VALOR_SEGREDO not in texto
    assert c["acoes"][0]["parametros"] == ["mensagem"] and c["acoes"][1]["segredo"] is True      # type: ignore[index]
    assert c["efeito"] == {"externo": True, "acoes_commit": [2]}


def test_fluxo_com_efeito_e_origem_de_treino(mundo: Mundo) -> None:
    plano = {"summary": "f", "app_id": "instagram", "parameters": {}, "steps": [
        {"key": "abrir", "title": "t", "goal": "g", "side_effect": False,
         "postcondition": {"kind": "app_foreground", "value": "x", "description": "o app abriu"}},
        {"key": "enviar", "title": "t", "goal": "g", "side_effect": True, "capability": "SEND_MESSAGE",
         "commit_selector": "desc=Enviar", "bindings": {"contato": "{contato}"},
         "postcondition": {"kind": "text_visible", "value": "ok", "description": "enviado"}}]}
    mundo.db.execute("INSERT INTO flows(id, name, match_key, command_template, plan, app_id, source_run_id, status,"
                     " created_at, source) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     ("f1", "Fluxo um", "mande {contato}", "mande {contato}", json.dumps(plano), "instagram", "run-9",
                      "active", TS, "run"))
    c = mundo.conteudo(LivroKind.FLUXO, "f1")
    assert c["tipo"] == "fluxo" and c["comando_modelo"] == "mande {contato}"
    assert c["origem"] == {"tipo": "execucao", "fonte": "run", "source_run_id": "run-9", "session_id": None, "run_id": None, "step_id": None, "attempt_id": None,
                           "instance_id": None, "operator": None, "ensinado_em": None, "nascido_de_prova": False,
                           "em_uso_real_desde": None}
    assert c["efeito"] == {"externo": True, "etapas_com_efeito": [1]}
    assert [e["chave"] for e in c["etapas"]] == ["abrir", "enviar"]                     # type: ignore[attr-defined]
    assert c["etapas"][1]["capability"] == "SEND_MESSAGE" and c["etapas"][1]["parametros"] == ["contato"]  # type: ignore[index]


def test_habilidade_resumida_do_banco(mundo: Mundo) -> None:
    doc = {"spec": {"parameters": [{"name": "contato"}], "nodes": [{"id": "abrir", "kind": "action"}]}}
    mundo.db.execute("INSERT INTO skill_definitions(id, name, app_id, created_at, updated_at) VALUES (?,?,?,?,?)",
                     ("ig.x", "Habilidade x", "instagram", TS, TS))
    mundo.db.execute("INSERT INTO skill_versions(id, skill_id, version, state, content, content_hash,"
                     " command_template, source_kind, created_at, state_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     ("ig.x@1", "ig.x", 1, "validated", json.dumps(doc), "hh", "faça {contato}", "teaching", TS, TS))
    c = mundo.conteudo(LivroKind.HABILIDADE, "ig.x@1")
    assert c["parametros"] == ["contato"] and c["total_de_nos"] == 1 and c["rota"] == "/api/skills/ig.x/versions/1"


def test_licao_e_tela_pelo_servico(mundo: Mundo) -> None:
    licao = mundo.repo.criar_item(
        NovoItem(kind=LivroKind.LICAO, escopo=Escopo(app=PACOTE, capability="SEND_MESSAGE", role="actor"),
                 content={"modelo": "nota", "acao": "SEND_MESSAGE", "nota": "confira o contato"},
                 summary="Ao enviar mensagem, confira o contato.", source_kind=SourceKind.FEEDBACK_NOTE,
                 side_effect=False, tokens=9),
        by="sistema", estado=SkillState.CANDIDATE, detalhe=None, reason="teste")
    c = mundo.conteudo(LivroKind.LICAO, licao.id)
    assert c["texto"] == "Ao enviar mensagem, confira o contato." and c["modelo"] == "nota" and c["tokens"] == 9
    tela = mundo.repo.criar_item(
        NovoItem(kind=LivroKind.TELA, escopo=Escopo(app=PACOTE),
                 content={"tela": "aprendida:feed", "tipo": "regra_de_tela", "autenticada": True,
                          "ids_todos": ["a", "b"], "casa": True, "razao": "feed"},
                 summary="feed", source_kind=SourceKind.SCREEN_OBSERVATION, side_effect=False),
        by="sistema", estado=SkillState.CANDIDATE, detalhe=None, reason="teste")
    assert mundo.conteudo(LivroKind.TELA, tela.id)["ids_todos"] == ["a", "b"]


async def test_rota_de_detalhe_devolve_conteudo_e_memoria_fica_sem(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    rid = _receita(mundo.db, "abrir")
    r = await cliente.get(f"/api/aprendizado/receita/{rid}")
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["conteudo"]["tipo"] == "receita" and corpo["conteudo"]["acoes"][0]["ferramenta"] == "tap"
    assert corpo["item"]["title"] == "abrir (v1)" and corpo["trilha"] == []          # o resto do detalhe não mudou
    perfil(mundo.db, "p1")
    mundo.db.execute("INSERT INTO memory_items(id, profile_id, subject, content, source, fingerprint, created_at,"
                     " updated_at) VALUES (?,?,?,?,?,?,?,?)", ("m0", "p1", "@ana", LITERAL, "operator", "fp", TS, TS))
    m = await cliente.get("/api/aprendizado/memoria/p1")
    assert m.status_code == 200 and m.json()["conteudo"] is None and LITERAL not in m.text
