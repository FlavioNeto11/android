"""30.14: obsolescência (`docs/design/aprendizado-vivo.md` §9.2) — o rótulo `obsoleto_provavel` com os sinais que têm
fonte hoje e o rebaixamento determinístico `catalogo_sem_efeito` (o caso real: a receita 100 do Outlook).

- domínio (`domain/obsolescencia.py`, puro): o veredito do catálogo (conservador), o destino do rebaixamento e as
  versões sem reprodução;
- rótulo (`domain/saude.py`): cada sinal vira `obsoleto_provavel` com o fato e a fonte; a ordem do §5.3;
- curadoria (`RebaixamentoPorCatalogo`, pelo `LearningService.curar`): rebaixa só o que o catálogo ATUAL não respalda,
  pelo sistema, uma vez só, com o motivo na trilha e o evento de espera coerente; o resto fica intacto.

Os catálogos são os YAML reais do repositório (`backend/app/conhecimento/apps/`), lidos pelo registro de apps.
Nível de prova: `simulated` (banco de teste, relógio fixo, barramento falso; nenhum aparelho, central ou IA).
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.db import Database
from app.modules.applications.infrastructure import registry
from app.modules.learning.application.ports import Ajustes
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.ciclo import SYSTEM_ACTOR, SkillState
from app.modules.learning.domain.espera import MotivoDeSaida
from app.modules.learning.domain.obsolescencia import (QUALQUER, CatalogoDoApp, Respaldo, SinaisDeObsolescencia,
                                                       Substituta, VereditoDoCatalogo, destino_do_rebaixamento,
                                                       do_quadro_de_versao, respaldo_da_receita, respaldo_do_fluxo,
                                                       versoes_sem_reproducao)
from app.modules.learning.domain.saude import CodigoDoMotivo, Rotulo, SinaisDeSaude, calcular
from app.modules.learning.domain.vocabulario import LivroKind
from app.modules.learning.infrastructure import ligar_obsolescencia
from app.modules.learning.infrastructure.eventos import EventosNoBarramento, RiscoDoRegistro
from app.modules.learning.infrastructure.fontes import FontesSql
from app.modules.learning.infrastructure.montagem import GuardaDoFluxo
from app.modules.learning.infrastructure.obsolescencia_sql import CatalogosDoRegistro
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.modules.learning.infrastructure.sql_repository import SqlLearningRepository
from app.modules.skills.infrastructure.sql_repository import SqlSkillRepository

from .fake_skills import TS, ValidadorFalso
from .fake_skills import banco as banco_migrado

S = SkillState
C = CodigoDoMotivo
OUTLOOK = "com.microsoft.office.outlook"
INSTAGRAM = "com.instagram.android"
SEM_CATALOGO = "com.exemplo.semcatalogo"
AGORA = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
#: Catálogo como o do Outlook (só leitura) e como o do Instagram (com efeito), para o domínio puro.
SO_LEITURA = CatalogoDoApp({"OPEN_MAIL_INBOX": False, "COLLECT_MAIL_HEADERS": False})
COM_EFEITO = CatalogoDoApp({"OPEN_POST": False, "LIKE_POST": True, "COMMENT_POST": True})


# ------------------------------------------------------------------ domínio: o veredito do catálogo
def _cap(*nomes: str, ambigua: bool | None = None) -> dict[str, object]:
    return {"capability": {"nomes": list(nomes), "ambigua": len(nomes) > 1 if ambigua is None else ambigua}}


def test_catalogo_sem_nenhuma_acao_com_efeito_rebaixa_com_asterisco() -> None:
    v = respaldo_da_receita(None, SO_LEITURA, tem_efeito=True)
    assert v is not None and v.rebaixa and v.capability == QUALQUER
    assert v.motivo == "catalogo_sem_efeito:*"


def test_nao_se_aplica_sem_commit_ou_sem_catalogo() -> None:
    assert respaldo_da_receita(_cap("LIKE_POST"), SO_LEITURA, tem_efeito=False) is None
    assert respaldo_da_receita(_cap("LIKE_POST"), None, tem_efeito=True) is None       # faixa B: segue a IA livre
    assert respaldo_do_fluxo(None, None, tem_efeito=True) is None


@pytest.mark.parametrize(("conteudo", "respaldo", "capability"), [
    (_cap("LIKE_POST"), Respaldo.RESPALDADO, "LIKE_POST"),
    (_cap("like_post"), Respaldo.RESPALDADO, "like_post"),                # a chave compara como o catálogo
    (_cap("OPEN_POST"), Respaldo.SEM_RESPALDO, "OPEN_POST"),              # conhecida, sem ambiguidade, sem efeito
    (_cap("SEND_EMAIL"), Respaldo.DUVIDOSO, "SEND_EMAIL"),                # fora do catálogo: só o sinal
    (_cap("LIKE_POST", "OPEN_POST"), Respaldo.DUVIDOSO, "LIKE_POST,OPEN_POST"),   # ambígua: só o sinal
    (_cap("LIKE_POST", "COMMENT_POST"), Respaldo.RESPALDADO, "LIKE_POST,COMMENT_POST"),
    (_cap("OPEN_POST", ambigua=True), Respaldo.DUVIDOSO, "OPEN_POST"),    # marcada ambígua nunca rebaixa
    ({"capability": None}, Respaldo.DUVIDOSO, QUALQUER),
    (_cap("*"), Respaldo.DUVIDOSO, QUALQUER),                             # a etapa livre não é um nome do catálogo
    (None, Respaldo.DUVIDOSO, QUALQUER),
])
def test_catalogo_com_efeito_so_rebaixa_capability_conhecida_sem_efeito(
        conteudo: dict[str, object] | None, respaldo: Respaldo, capability: str) -> None:
    v = respaldo_da_receita(conteudo, COM_EFEITO, tem_efeito=True)  # type: ignore[arg-type]
    assert v is not None and (v.respaldo, v.capability) == (respaldo, capability)
    assert v.rebaixa is (respaldo is Respaldo.SEM_RESPALDO)


def test_fluxo_cada_etapa_com_efeito_e_conferida() -> None:
    def fluxo(*etapas: tuple[str | None, bool]) -> dict[str, object]:
        return {"etapas": [{"capability": c, "efeito": e} for c, e in etapas]}

    assert respaldo_do_fluxo(fluxo(("OPEN_POST", False), ("LIKE_POST", True)), COM_EFEITO,
                             tem_efeito=True).respaldo is Respaldo.RESPALDADO  # type: ignore[union-attr]
    sem = respaldo_do_fluxo(fluxo(("LIKE_POST", True), ("OPEN_POST", True)), COM_EFEITO, tem_efeito=True)
    assert sem is not None and sem.rebaixa and sem.motivo == "catalogo_sem_efeito:OPEN_POST"
    assert respaldo_do_fluxo(fluxo((None, True)), COM_EFEITO,
                             tem_efeito=True).respaldo is Respaldo.DUVIDOSO  # type: ignore[union-attr]
    assert respaldo_do_fluxo(fluxo(("X", True)), SO_LEITURA, tem_efeito=True).motivo == "catalogo_sem_efeito:*"  # type: ignore[union-attr]


def test_destino_do_rebaixamento() -> None:
    assert destino_do_rebaixamento(LivroKind.RECEITA, S.CANDIDATE) is S.DISABLED
    assert destino_do_rebaixamento(LivroKind.RECEITA, S.VALIDATED) is S.DISABLED
    assert destino_do_rebaixamento(LivroKind.RECEITA, S.PUBLISHED) is S.DISABLED      # reativar é de pessoa (§9.2)
    assert destino_do_rebaixamento(LivroKind.FLUXO, S.PUBLISHED) is S.DISABLED       # fluxo não tem aposentadoria
    for fora in (S.DISABLED, S.DEPRECATED, None):
        assert destino_do_rebaixamento(LivroKind.RECEITA, fora) is None


def test_versoes_sem_reproducao_ignora_so_a_provadamente_mais_antiga() -> None:
    assert versoes_sem_reproducao("447.0.0.38", ["446.0.0.1", "448.0.0.2", "beta"]) == ("448.0.0.2", "beta")
    quadro = {"estado": "versao_aposentada", "app_version": "446", "vivas": [{"versao": "447", "aparelhos": 2}],
              "nao_testada_em": ["447"]}
    assert do_quadro_de_versao(quadro) == ("446", ("447",), ("447",))
    assert do_quadro_de_versao(None) == (None, (), ())


# ------------------------------------------------------------------ rótulo: cada sinal com fonte
def _sinais(kind: LivroKind = LivroKind.RECEITA, estado: SkillState | None = S.PUBLISHED, **kw: object) -> SinaisDeSaude:
    base: dict[str, object] = {"kind": kind, "estado": estado, "agora": AGORA, "criado_em": "2026-09-01T00:00:00Z",
                               "ultimo_uso": "2026-10-01T00:00:00Z", "usos": 20, "a_favor": 19, "contra": 1,
                               "contestacoes_recentes": 0, "falhas_seguidas": 0}
    base.update(kw)
    return SinaisDeSaude(**base)  # type: ignore[arg-type]


SEM = VereditoDoCatalogo(Respaldo.SEM_RESPALDO, QUALQUER, "o catálogo do app não tem nenhuma ação com efeito externo")


@pytest.mark.parametrize(("obs", "codigo", "valor"), [
    (SinaisDeObsolescencia(substituta_viva=Substituta("7", "candidate", "recipes: mesma chave, versão seguinte")),
     C.SUBSTITUTA_VIVA, "7"),
    (SinaisDeObsolescencia(versao_fora_do_parque="446", versoes_vivas=("447",)), C.VERSAO_FORA_DO_PARQUE, "446"),
    (SinaisDeObsolescencia(versoes_sem_reproducao=("448",)), C.VERSAO_VIVA_SEM_REPRODUCAO, "448"),
    (SinaisDeObsolescencia(efeito=SEM), C.EFEITO_SEM_RESPALDO_NO_CATALOGO, QUALQUER),
    (SinaisDeObsolescencia(absorvida_em="abc1234"), C.ABSORVIDA, "abc1234"),
])
def test_cada_sinal_vira_obsoleto_provavel_com_fato_e_fonte(obs: SinaisDeObsolescencia, codigo: CodigoDoMotivo,
                                                             valor: str) -> None:
    saude = calcular(_sinais(obsolescencia=obs))
    assert saude is not None and saude.rotulo is Rotulo.OBSOLETO_PROVAVEL
    assert [(m.codigo, m.valor) for m in saude.motivos] == [(codigo, valor)]
    assert saude.motivos[0].detalhe                                       # a fonte vai em `detalhe`
    # sem sinal, o mesmo item é saudável: o rótulo novo não muda o resto
    assert calcular(_sinais(obsolescencia=SinaisDeObsolescencia())).rotulo is Rotulo.SAUDAVEL  # type: ignore[union-attr]


def test_fluxo_nunca_usado_tem_o_mesmo_rotulo_da_receita_nunca_usada() -> None:
    """O mesmo fato (publicado há `sem_uso_dias` e nunca usado) dá o mesmo rótulo e o mesmo motivo no fluxo e na
    receita: `sem_evidencia`/`nunca_usado`. Antes o fluxo saía `obsoleto_provavel` (`fluxo_nunca_casado`)."""
    nunca = {"usos": 0, "ultimo_uso": None, "a_favor": 0, "contra": 0}
    for kind in (LivroKind.FLUXO, LivroKind.RECEITA):
        velho = calcular(_sinais(kind, estado_desde="2026-09-18T12:00:00Z", **nunca))
        assert velho is not None and velho.rotulo is Rotulo.SEM_EVIDENCIA, kind
        assert [(m.codigo, m.valor, m.limite) for m in velho.motivos] == [(C.NUNCA_USADO, 14, 14)], kind
        novo = calcular(_sinais(kind, estado_desde="2026-09-25T12:00:00Z", **nunca))
        assert novo is not None and novo.rotulo is Rotulo.POUCA_AMOSTRA, kind


def test_ordem_do_rotulo_publicado_degradando_vence_e_em_prova_nao_e_obsoleto() -> None:
    obs = SinaisDeObsolescencia(efeito=SEM)
    assert calcular(_sinais(falhas_seguidas=3, obsolescencia=obs)).rotulo is Rotulo.DEGRADANDO  # type: ignore[union-attr]
    assert calcular(_sinais(estado=S.CANDIDATE, obsolescencia=obs)).rotulo is Rotulo.EM_PROVA  # type: ignore[union-attr]
    assert calcular(_sinais(estado=S.DISABLED, obsolescencia=obs)).rotulo is Rotulo.INATIVO  # type: ignore[union-attr]
    nunca = calcular(_sinais(usos=0, ultimo_uso=None, a_favor=0, contra=0, estado_desde="2026-09-01T00:00:00Z",
                             obsolescencia=obs))
    assert nunca is not None and nunca.rotulo is Rotulo.OBSOLETO_PROVAVEL     # antes de `sem_evidencia`


# ------------------------------------------------------------------ banco: curadoria e rótulo
@dataclass
class BarramentoFalso:
    emitidos: list[dict[str, object]] = field(default_factory=list)

    def emit(self, kind: str, message: str, *, level: str = "info", instance_id: str | None = None,
             data: dict[str, object] | None = None) -> object:
        self.emitidos.append(dict(data or {}))
        return None


def _receita(db: Database, pacote: str, passo: str, *, status: str, commit: bool = True, versao: int = 1,
             app_version: str = "4.2", origem: str | None = None, step_hash: str | None = None,
             ok: int = 0, ultimo_uso: str | None = None) -> int:
    acoes = [{"tool": "tap", "args": {}, "selectors": [{"rid": f"botao_{passo}"}], "commit": commit}]
    return int(db.inserted_id(
        "INSERT INTO recipes(app_package, app_version, app_signature, variant, step_hash, step_key, version, status,"
        " actions, learned_from_step, created_at, replay_ok, last_used_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (pacote, app_version, "sig", "pt/420", step_hash or f"h-{passo}", passo, versao, status, json.dumps(acoes),
         origem, TS, ok, ultimo_uso)))


def _etapa(db: Database, run_id: str, chave: str, *, capability: str, template_hash: str | None = None) -> str:
    if db.one("SELECT id FROM runs WHERE id=?", (run_id,)) is None:
        db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, instance_ids, app_ids, created_at)"
                   " VALUES (?,?,?,?,?,?,?,?)", (run_id, f"k-{run_id}", "cmd", "execute", "completed",
                                                  json.dumps(["android-01"]), json.dumps(["instagram"]), TS))
        db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version) VALUES (?,?,?,?,?)",
                   (f"{run_id}:o1", run_id, "android-01", "succeeded", 1))
    sid = f"{run_id}:android-01:v1:{chave}"
    db.execute("INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
               " postcondition, timeout_s, max_attempts, status, capability, template_hash, app_id)"
               " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (sid, run_id, f"{run_id}:o1", "android-01", 1, 1, chave, chave, chave, "{}", 180, 3, "succeeded",
                capability, template_hash, "instagram"))
    return sid


class Mundo:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.barramento = BarramentoFalso()
        for ident, nome, pacote in (("instagram", "Instagram", INSTAGRAM), ("outlook", "Outlook", OUTLOOK),
                                    ("semcat", "Sem catálogo", SEM_CATALOGO)):
            db.execute("INSERT INTO apps(id, name, package, builtin) VALUES (?,?,?,0)", (ident, nome, pacote))
        habilidades = SqlSkillRepository(db, ValidadorFalso())
        self.repo = SqlLearningRepository(db, guarda_do_fluxo=GuardaDoFluxo(db, habilidades), precos=dict)
        self.servico = LearningService(self.repo, FontesSql(db), TriagemDeCredencial(), ajustes=Ajustes,
                                       relogio=lambda: AGORA, retencao_de_logs_dias=lambda: 14,
                                       eventos=EventosNoBarramento(self.barramento),
                                       catalogo_de_risco=RiscoDoRegistro())
        ligar_obsolescencia.ligar(self.servico, self.repo, db)

    def status(self, recipe_id: int) -> str:
        row = self.db.one("SELECT status FROM recipes WHERE id=?", (recipe_id,))
        assert row is not None
        return str(row["status"])

    def trilha(self, kind: LivroKind, ref: str) -> list[tuple[str, str, str]]:
        return [(t.to_state.value, t.decided_by, t.reason) for t in self.repo.trilha(f"{kind.value}:{ref}")]


@pytest.fixture
def mundo(tmp_path: Path) -> Iterator[Mundo]:
    db = banco_migrado(tmp_path, "obsolescencia.sqlite3")
    yield Mundo(db)
    db.close()


def test_os_catalogos_reais_sao_o_que_o_teste_supoe() -> None:
    """Dado de leitura: o Outlook não tem ação com efeito; o Instagram tem, e `LIKE_POST` é uma delas."""
    outlook, instagram = CatalogosDoRegistro().catalogo(OUTLOOK), CatalogosDoRegistro().catalogo(INSTAGRAM)
    assert outlook is not None and outlook.efeito_por_acao and not outlook.tem_efeito
    assert instagram is not None and instagram.efeito("LIKE_POST") is True and instagram.efeito("OPEN_POST") is False
    assert CatalogosDoRegistro().catalogo(SEM_CATALOGO) is None and registry.get(SEM_CATALOGO) is None


def test_receita_100_do_outlook_e_desligada_pelo_sistema_uma_vez_so(mundo: Mundo) -> None:
    """A receita do caso real: `tap` com `commit=true` na etapa `send_email`, num catálogo só de leitura."""
    receita = _receita(mundo.db, OUTLOOK, "send_email", status="candidate")
    primeira = mundo.servico.curar()
    assert primeira.feito.get("catalogo_sem_efeito") == 1 and "catalogo_sem_efeito" not in primeira.falhas
    assert mundo.status(receita) == "quarantined"                          # disabled na fonte da receita
    assert mundo.trilha(LivroKind.RECEITA, str(receita)) == [("disabled", SYSTEM_ACTOR, "catalogo_sem_efeito:*")]
    segunda = mundo.servico.curar()                                        # idempotente: nada a fazer
    assert segunda.feito.get("catalogo_sem_efeito") == 0
    assert len(mundo.trilha(LivroKind.RECEITA, str(receita))) == 1
    # e o livro diz por quê: inativo, com o motivo da trilha
    saude = mundo.servico.detalhe(LivroKind.RECEITA, str(receita)).saude
    assert saude is not None and saude.rotulo is Rotulo.INATIVO
    assert saude.motivos[0].detalhe == "catalogo_sem_efeito:*"


def test_receita_ativa_do_outlook_e_desligada_e_pode_voltar_por_pessoa(mundo: Mundo) -> None:
    receita = _receita(mundo.db, OUTLOOK, "send_email", status="active")
    mundo.servico.curar()
    assert mundo.status(receita) == "quarantined"                          # disabled na fonte da receita
    assert mundo.trilha(LivroKind.RECEITA, str(receita)) == [("disabled", SYSTEM_ACTOR, "catalogo_sem_efeito:*")]


def test_validada_esperando_o_dono_sai_da_espera_rebaixada_pelo_sistema(mundo: Mundo) -> None:
    receita = _receita(mundo.db, OUTLOOK, "send_email", status="validated")
    mundo.servico.curar()
    mundo.servico.curar()
    assert mundo.status(receita) == "quarantined"
    assert len(mundo.barramento.emitidos) == 1                             # duas rodadas, um evento
    evento = mundo.barramento.emitidos[0]
    assert {k: evento[k] for k in ("kind", "ref", "app", "faixa", "aguardando", "motivo")} == {
        "kind": "receita", "ref": str(receita), "app": OUTLOOK, "faixa": "B", "aguardando": False,
        "motivo": MotivoDeSaida.REBAIXADO_PELO_SISTEMA.value}


def test_fluxo_do_outlook_com_efeito_e_desligado(mundo: Mundo) -> None:
    plano = {"steps": [{"key": "enviar", "capability": "SEND_MAIL", "side_effect": True}]}
    mundo.db.execute("INSERT INTO flows(id, name, match_key, command_template, plan, app_id, status, created_at)"
                     " VALUES ('f-env','enviar','envie um email','envie um email',?,'outlook','active',?)",
                     (json.dumps(plano), TS))
    assert mundo.servico.curar().feito.get("catalogo_sem_efeito") == 1
    linha = mundo.db.one("SELECT status FROM flows WHERE id='f-env'")
    assert linha is not None and linha["status"] == "disabled"
    assert mundo.trilha(LivroKind.FLUXO, "f-env") == [("disabled", SYSTEM_ACTOR, "catalogo_sem_efeito:*")]


def test_o_que_o_catalogo_respalda_ou_nao_conhece_fica_intacto(mundo: Mundo) -> None:
    curtir = _receita(mundo.db, INSTAGRAM, "curtir", status="candidate",
                      origem=_etapa(mundo.db, "r1", "curtir", capability="LIKE_POST"))
    sem_catalogo = _receita(mundo.db, SEM_CATALOGO, "enviar", status="active")
    sem_commit = _receita(mundo.db, OUTLOOK, "abrir", status="active", commit=False)
    # ambígua: o mesmo `step_hash` foi LIKE_POST e OPEN_POST em execuções diferentes
    _etapa(mundo.db, "r2", "a", capability="LIKE_POST", template_hash="h-amb")
    _etapa(mundo.db, "r3", "b", capability="OPEN_POST", template_hash="h-amb")
    ambigua = _receita(mundo.db, INSTAGRAM, "amb", status="active", step_hash="h-amb", ok=20,
                       ultimo_uso="2026-10-01T00:00:00Z")
    assert mundo.servico.curar().feito.get("catalogo_sem_efeito") == 0
    assert [mundo.status(r) for r in (curtir, sem_catalogo, sem_commit, ambigua)] == ["candidate"] + ["active"] * 3
    assert all(mundo.trilha(LivroKind.RECEITA, str(r)) == [] for r in (curtir, sem_catalogo, sem_commit, ambigua))
    # a ambígua num catálogo com efeito vira SÓ o rótulo, com a dúvida dita
    saude = mundo.servico.livro(kind=LivroKind.RECEITA).saudes[f"receita:{ambigua}"]
    assert saude.rotulo is Rotulo.OBSOLETO_PROVAVEL
    assert [(m.codigo, m.valor) for m in saude.motivos] == [(C.EFEITO_SEM_RESPALDO_NO_CATALOGO, "LIKE_POST,OPEN_POST")]
    assert "duvidoso" in (saude.motivos[0].detalhe or "")


def test_rotulo_por_substituta_viva_e_versao_fora_do_parque_igual_na_lista_e_no_detalhe(mundo: Mundo) -> None:
    usada = {"ok": 20, "ultimo_uso": "2026-10-01T00:00:00Z", "commit": False}
    v1 = _receita(mundo.db, INSTAGRAM, "abrir", status="active", app_version="447", **usada)  # type: ignore[arg-type]
    _receita(mundo.db, INSTAGRAM, "abrir", status="candidate", versao=2, app_version="447", commit=False)
    antiga = _receita(mundo.db, INSTAGRAM, "voltar", status="active", app_version="446", **usada)  # type: ignore[arg-type]
    boa = _receita(mundo.db, INSTAGRAM, "rolar", status="active", app_version="447", **usada)  # type: ignore[arg-type]
    mundo.db.execute("INSERT INTO device_app_state(instance_id, package_name, observed_version_name, state)"
                     " VALUES ('android-01', ?, '447', 'installed')", (INSTAGRAM,))
    lista = mundo.servico.livro(kind=LivroKind.RECEITA).saudes
    sub = lista[f"receita:{v1}"]
    assert sub.rotulo is Rotulo.OBSOLETO_PROVAVEL
    assert [(m.codigo, m.valor) for m in sub.motivos] == [(C.SUBSTITUTA_VIVA, str(v1 + 1))]
    fora = lista[f"receita:{antiga}"]
    assert fora.rotulo is Rotulo.OBSOLETO_PROVAVEL
    assert [(m.codigo, m.valor) for m in fora.motivos] == [(C.VERSAO_FORA_DO_PARQUE, "446"),
                                                           (C.VERSAO_VIVA_SEM_REPRODUCAO, "447")]
    assert lista[f"receita:{boa}"].rotulo is Rotulo.SAUDAVEL
    for ref in (v1, antiga, boa):                                           # o detalhe usa a mesma função
        assert mundo.servico.detalhe(LivroKind.RECEITA, str(ref)).saude == lista[f"receita:{ref}"]
