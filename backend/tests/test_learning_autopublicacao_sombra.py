"""30.34: a autopublicação do fluxo B em SOMBRA, com o banco migrado: o laço marca o caso uma vez, nunca publica, e o
balanço lê as regressões das tabelas de sempre. Prova `simulated` (banco de teste, sem IA nem aparelho)."""
from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import AutopublicacaoCfg
from app.db import Database
from app.modules.learning.application.autopublicacao import ServicoDeAutopublicacao
from app.modules.learning.application.metricas import ServicoDeMetricas
from app.modules.learning.application.nativos import marca_do_conteudo
from app.modules.learning.application.ports import Ajustes, MudancaNativa, NovaEvidencia, NovaRevisao, NovoSinal
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.autopublicacao import MotivoDeFora
from app.modules.learning.domain.ciclo import (Actor, ConflitoDeEstado, EntradaInvalida, ExigeODono, SkillState,
                                               TransicaoProibida, conferir_transicao)
from app.modules.learning.domain.curador import Confianca, Decisao, Parecer
from app.modules.learning.domain.vocabulario import LivroKind, Polaridade, Posicao, SignalKind
from app.modules.learning.infrastructure import ligar_autopublicacao
from app.modules.learning.infrastructure.autopublicacao_sql import LivroDaSombraSql
from app.modules.learning.infrastructure.feedback_sql import LeituraDoVotoSql
from app.modules.learning.infrastructure.fontes import FontesSql
from app.modules.learning.infrastructure.metricas_sql import FontesDeMetricasSql
from app.modules.learning.infrastructure.revisoes_sql import RegistroDeRevisoesSql
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.modules.learning.infrastructure.sql_repository import SqlLearningRepository
from app.util import to_iso

from .fake_skills import banco as banco_migrado

INICIO = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
LIDER = lambda: 1  # noqa: E731
SEM_LIDER = lambda: None  # noqa: E731


def _plano(fid: str) -> dict[str, object]:
    return {"summary": fid, "app_id": "instagram", "parameters": {},
            "steps": [{"key": "a", "title": "Comentar", "goal": "a", "side_effect": True,
                       "postcondition": {"kind": "app_foreground", "value": "x", "description": "x"}}]}


class Mundo:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.agora = INICIO
        self.cfg = AutopublicacaoCfg(modo="shadow")
        self.repo = SqlLearningRepository(db, clock=lambda: to_iso(self.agora), precos=dict)
        self.servico = LearningService(self.repo, FontesSql(db), TriagemDeCredencial(), ajustes=Ajustes,
                                       relogio=lambda: self.agora, retencao_de_logs_dias=lambda: 14)
        # Sem catálogo: o commit em app sem catálogo é B (a regra confirmada pelo dono em 03/10).
        self.auto = ligar_autopublicacao.ligar(self.servico, self.repo, db, config=lambda: self.cfg,
                                               relogio=lambda: self.agora, catalogo=None)
        [laco] = [x for x in self.servico.lacos if isinstance(x, ligar_autopublicacao.LacoDaAutopublicacao)]
        self.laco = laco

    def fluxo(self, fid: str, *, status: str = "validated") -> str:
        self.db.execute("INSERT INTO flows(id, name, match_key, command_template, plan, app_id, status, created_at,"
                        " source) VALUES (?,?,?,?,?,?,?,?,'run')",
                        (fid, fid, f"cmd {fid}", f"cmd {fid}", json.dumps(_plano(fid)), "instagram", status,
                         to_iso(INICIO - timedelta(days=1))))
        return f"fluxo:{fid}"

    def evidencia(self, ref: str, run: str, aparelho: str, *, stance: Posicao = Posicao.FOR,
                  simulada: bool = False) -> None:
        e = self.servico.entrada(LivroKind.FLUXO, ref.removeprefix("fluxo:"))
        assert e.content_hash is not None
        self.repo.registrar_evidencia(NovaEvidencia(
            item_ref=ref, stance=stance, origin_ref=f"run:{run}", simulated=simulada, run_id=run,
            instance_id=aparelho, app_version="447", detail=f"{marca_do_conteudo(e.content_hash)} mesmo plano"))

    def parecer(self, ref: str, *, decisao: Decisao = Decisao.APROVAR, confianca: Confianca = Confianca.ALTA,
                estado: str = "validated", simulado: bool = False) -> str:
        p = Parecer(decisao=decisao, evidencias_citadas=(ref,), confianca=confianca)
        rid = RegistroDeRevisoesSql(self.db).gravar(NovaRevisao(
            item_ref=ref, item_kind="fluxo", scope_app="com.instagram.android", gatilho="nova_pendencia_do_dono",
            dossie_hash=f"h-{ref}-{decisao.value}-{confianca.value}", dossie={"item": {"estado": estado}},
            template_id="curador", template_versao="dossie-v1", provedor="teste", modelo="m", simulated=simulado,
            validade="ok", saida=p.como_dados(), classe_de_risco="B", politica="teste"), self.agora)
        assert rid is not None
        return rid

    def pronto(self, fid: str) -> str:
        ref = self.fluxo(fid)
        self.evidencia(ref, "r1", "android-01")
        self.evidencia(ref, "r2", "android-03")
        self.parecer(ref)
        return ref

    def casos(self) -> list[dict[str, object]]:
        return [dict(r) for r in self.db.query("SELECT * FROM learning_signals WHERE kind='autopublicaria'")]


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    d = banco_migrado(tmp_path, "autopublicacao.sqlite3")
    d.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('instagram','Instagram','com.instagram.android',0)")
    yield d
    d.close()


@pytest.fixture
def mundo(db: Database) -> Mundo:
    return Mundo(db)


def test_o_fluxo_b_pronto_vira_um_caso_da_sombra_uma_vez_e_nunca_publica(mundo: Mundo) -> None:
    ref = mundo.pronto("comentar-no-post")
    r = mundo.laco._volta(LIDER)
    assert r is not None and r.publicaria == (ref,) and r.marcados == (ref,)
    [caso] = mundo.casos()
    assert caso["source_ref"] == f"autopublicaria:{ref}" and caso["created_by"] == "sistema"
    dados = json.loads(str(caso["data"]))
    assert (dados["modo"], dados["execucoes"], dados["aparelhos"]) == ("shadow", 2, 2)
    # Sombra: o fluxo continua esperando o dono, e a trilha não ganhou transição.
    assert mundo.servico.entrada(LivroKind.FLUXO, "comentar-no-post").state is SkillState.VALIDATED
    assert mundo.db.one("SELECT COUNT(*) AS n FROM learning_transitions WHERE item_ref=?", (ref,))["n"] == 0
    # Um caso por item: a volta seguinte vê o mesmo fluxo e não marca de novo.
    mundo.agora += timedelta(hours=1)
    r2 = mundo.laco._volta(LIDER)
    assert r2 is not None and r2.publicaria == (ref,) and r2.marcados == ()
    assert len(mundo.casos()) == 1


def test_off_e_sem_lider_nada_roda(mundo: Mundo) -> None:
    mundo.pronto("comentar-no-post")
    assert mundo.laco._volta(SEM_LIDER) is None
    mundo.cfg = AutopublicacaoCfg()                                  # off, o padrão
    assert mundo.laco._volta(LIDER) is None and mundo.casos() == []


def test_depois_de_parar_nenhuma_volta_roda(mundo: Mundo) -> None:
    mundo.pronto("comentar-no-post")
    assert mundo.laco.parar(1.0) is True
    assert mundo.laco._volta(LIDER) is None and mundo.casos() == []


def test_o_config_aceita_on_e_recusa_o_resto() -> None:
    assert AutopublicacaoCfg(modo="on").modo == "on" and AutopublicacaoCfg().modo == "off"
    with pytest.raises(ValidationError):
        AutopublicacaoCfg(modo="sim")  # type: ignore[arg-type]


@pytest.mark.parametrize(("preparo", "motivo"), [
    ("um_aparelho", MotivoDeFora.POUCOS_APARELHOS),
    ("simulada", MotivoDeFora.POUCAS_EXECUCOES),                     # a simulada não conta
    ("contra", MotivoDeFora.EVIDENCIA_CONTRA),
    ("confianca_media", MotivoDeFora.CONFIANCA_NAO_ALTA),
    ("pedir_evidencia", MotivoDeFora.NAO_SUGERE_APROVAR),
    ("parecer_de_outro_estado", MotivoDeFora.PARECER_DESATUALIZADO),
    ("parecer_simulado", MotivoDeFora.PARECER_SIMULADO),
    ("outra_encarnacao", MotivoDeFora.POUCAS_EXECUCOES),             # evidência de outro conteúdo não conta
])
def test_o_fluxo_que_falha_uma_condicao_nao_vira_caso(mundo: Mundo, preparo: str, motivo: MotivoDeFora) -> None:
    ref = mundo.fluxo("comentar-no-post")
    mundo.evidencia(ref, "r1", "android-01")
    if preparo == "um_aparelho":
        mundo.evidencia(ref, "r2", "android-01")
    elif preparo == "simulada":
        mundo.evidencia(ref, "r2", "android-03", simulada=True)
    elif preparo == "outra_encarnacao":
        mundo.repo.registrar_evidencia(NovaEvidencia(
            item_ref=ref, stance=Posicao.FOR, origin_ref="run:r2", simulated=False, run_id="r2",
            instance_id="android-03", app_version="447", detail="[000000000000] plano antigo"))
    else:
        mundo.evidencia(ref, "r2", "android-03")
    if preparo == "contra":
        mundo.evidencia(ref, "r3", "android-06", stance=Posicao.AGAINST)
    decisao = Decisao.PEDIR_EVIDENCIA if preparo == "pedir_evidencia" else Decisao.APROVAR
    confianca = Confianca.MEDIA if preparo == "confianca_media" else Confianca.ALTA
    mundo.parecer(ref, decisao=decisao, confianca=confianca,
                  estado="candidate" if preparo == "parecer_de_outro_estado" else "validated",
                  simulado=preparo == "parecer_simulado")
    e = mundo.servico.entrada(LivroKind.FLUXO, "comentar-no-post")
    assert motivo in mundo.auto.avaliar(e).avaliacao.motivos
    r = mundo.laco._volta(LIDER)
    assert r is not None and r.publicaria == () and mundo.casos() == []


def test_o_fluxo_sem_efeito_ou_ja_publicado_nem_entra(mundo: Mundo) -> None:
    ref = mundo.fluxo("ja-publicado", status="active")
    mundo.evidencia(ref, "r1", "android-01")
    mundo.evidencia(ref, "r2", "android-03")
    mundo.parecer(ref, estado="published")
    r = mundo.laco._volta(LIDER)
    assert r is not None and r.avaliados == 0 and mundo.casos() == []


def test_o_balanco_le_as_regressoes_das_tabelas_de_sempre(mundo: Mundo) -> None:
    refs = [mundo.pronto(f"f{i}") for i in range(4)]
    mundo.laco._volta(LIDER)
    assert mundo.auto.balanco().casos == 4 and mundo.auto.balanco().abertos == 4
    mundo.agora += timedelta(days=2)
    # f0: evidência real contra depois da marca; f1: desligado; f2: uma pessoa recusou o parecer; f3: limpo.
    mundo.evidencia(refs[0], "r9", "android-06", stance=Posicao.AGAINST)
    mundo.servico.mudar_estado(LivroKind.FLUXO, "f1", SkillState.DISABLED, by="dono", reason="não quero")
    rid = mundo.db.one("SELECT id FROM learning_reviews WHERE item_ref=?", (refs[2],))["id"]
    mundo.repo.registrar_sinal(NovoSinal(kind=SignalKind.PARECER_DECIDIDO, source_ref=f"parecer:{rid}",
                                         created_by="dono", polarity=Polaridade.NEUTRAL,
                                         data={"review_id": rid, "item_ref": refs[2], "decisao_final": "recusou"}))
    b = mundo.auto.balanco()
    assert (b.casos, b.regrediram, b.abertos, b.limpos) == (4, 3, 1, 0)
    mundo.agora += timedelta(days=6)                                  # 8 dias depois da marca: f3 fecha limpo
    b = mundo.auto.balanco()
    assert (b.limpos, b.regrediram, b.taxa_sem_regressao, b.libera) == (1, 3, 0.25, False)
    rel = mundo.auto.relatorio()
    assert rel["modo"] == "shadow" and rel["casos"] == 4 and rel["libera"] is False


def test_as_metricas_trazem_o_balanco_da_sombra_no_bloco_do_curador(mundo: Mundo, db: Database) -> None:
    mundo.pronto("comentar-no-post")
    mundo.laco._volta(LIDER)
    m = ServicoDeMetricas(mundo.servico, FontesDeMetricasSql(db), autopublicacao=mundo.auto.relatorio,
                          relogio=lambda: mundo.agora).metricas(app=None, dias=14)
    bloco = m.curador["autopublicacao"]
    assert isinstance(bloco, dict) and (bloco["modo"], bloco["casos"], bloco["abertos"]) == ("shadow", 1, 1)
    sem = ServicoDeMetricas(mundo.servico, FontesDeMetricasSql(db), relogio=lambda: mundo.agora).metricas(app=None,
                                                                                                         dias=14)
    assert "autopublicacao" not in sem.curador                       # sem a composição, o contrato de antes


def test_o_caso_da_sombra_nao_aparece_na_aba_sinais(mundo: Mundo, db: Database) -> None:
    """É marca do sistema, não gesto: a listagem sem filtro o deixa de fora; pedido pelo tipo, ele vem."""
    mundo.pronto("comentar-no-post")
    mundo.laco._volta(LIDER)
    leitura = LeituraDoVotoSql(db)
    desde = to_iso(INICIO - timedelta(days=1))
    assert [s for s in leitura.sinais(desde=desde, kind=None, app=None, limite=50)
            if s.kind is SignalKind.AUTOPUBLICARIA] == []
    assert len(leitura.sinais(desde=desde, kind=SignalKind.AUTOPUBLICARIA, app=None, limite=50)) == 1


def test_o_servico_fica_pendurado_no_livro(mundo: Mundo) -> None:
    assert mundo.servico.extensao(ServicoDeAutopublicacao) is mundo.auto


# ------------------------------------------------------------------ a volta deixa rastro (relatório da sombra, 04/10)
def test_a_volta_vazia_deixa_rastro_no_log_e_nas_metricas(mundo: Mundo, caplog: pytest.LogCaptureFixture) -> None:
    """Zero casos só prova algo se a volta rodou: antes da primeira, `ultima_volta` é `null`; depois, mesmo sem
    nenhum candidato, ela diz quando rodou e quantos avaliou, e o log tem uma linha."""
    assert mundo.auto.relatorio()["ultima_volta"] is None
    with caplog.at_level("INFO", logger="poc.aprendizado"):
        assert mundo.laco._volta(LIDER) is not None
    assert mundo.auto.relatorio()["ultima_volta"] == {"em": to_iso(INICIO), "modo": "shadow", "avaliados": 0,
                                                      "publicaria": 0, "marcados": 0, "publicados": 0}
    assert any("autopublicação em shadow: 0 fluxo(s) avaliado(s), 0 publicaria(m), 0 caso(s) novo(s)" in m
               for m in caplog.messages), caplog.messages
    ref = mundo.pronto("comentar-no-post")
    mundo.agora += timedelta(hours=1)
    with caplog.at_level("INFO", logger="poc.aprendizado"):
        mundo.laco._volta(LIDER)
    assert mundo.auto.relatorio()["ultima_volta"] == {"em": to_iso(INICIO + timedelta(hours=1)), "modo": "shadow",
                                                      "avaliados": 1, "publicaria": 1, "marcados": 1,
                                                      "publicados": 0}
    assert any(m.endswith(f"1 caso(s) novo(s): {ref}") for m in caplog.messages)


def test_sem_lider_a_volta_nao_conta(mundo: Mundo) -> None:
    assert mundo.laco._volta(SEM_LIDER) is None
    assert mundo.auto.relatorio()["ultima_volta"] is None


async def test_a_primeira_volta_sai_logo_depois_do_inicio(mundo: Mundo, monkeypatch: pytest.MonkeyPatch) -> None:
    """Com o intervalo de 1 h e reinícios a cada hora, esperar o intervalo antes da primeira volta deixava a sombra
    sem rodar; a primeira espera é `PRIMEIRA_VOLTA_S`, as seguintes, o intervalo."""
    esperas: list[float] = []

    async def dormir(s: float) -> None:
        esperas.append(s)
        if len(esperas) == 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(ligar_autopublicacao.asyncio, "sleep", dormir)
    with pytest.raises(asyncio.CancelledError):
        await mundo.laco.laco(LIDER)
    assert esperas == [ligar_autopublicacao.PRIMEIRA_VOLTA_S, 3600.0, 3600.0]
    assert mundo.auto.relatorio()["ultima_volta"] is not None


# ------------------------------------------------------------------ 30.34-B: o `on` pela trava da D1
def _liberar(mundo: Mundo, casos: int = 30) -> None:
    """O balanço liberado: `casos` marcas limpas, fechadas há mais de 7 dias (nenhum evento depois delas)."""
    marca = mundo.agora
    mundo.agora = marca - timedelta(days=9)
    livro = LivroDaSombraSql(mundo.db, mundo.repo)
    for i in range(casos):
        assert livro.marcar(f"fluxo:antigo-{i}", {"modo": "shadow"}, app="com.instagram.android")
    mundo.agora = marca
    assert mundo.auto.balanco().libera is (casos >= 30)


def _trilha(mundo: Mundo, ref: str) -> list[dict[str, object]]:
    return [dict(r) for r in mundo.db.query("SELECT from_state, to_state, decided_by, reason FROM learning_transitions"
                                            " WHERE item_ref=? ORDER BY id", (ref,))]


def test_on_sem_o_balanco_liberado_so_marca(mundo: Mundo) -> None:
    """Entregue desligado: com 0 casos fechados (o central em 04/10), `on` é igual a `shadow`."""
    mundo.cfg = AutopublicacaoCfg(modo="on")
    ref = mundo.pronto("comentar-no-post")
    r = mundo.laco._volta(LIDER)
    assert r is not None and r.marcados == (ref,) and r.publicados == ()
    assert mundo.servico.entrada(LivroKind.FLUXO, "comentar-no-post").state is SkillState.VALIDATED
    assert _trilha(mundo, ref) == [] and mundo.auto.relatorio()["publicados_pela_emenda"] == 0


def test_on_com_o_balanco_liberado_publica_pela_emenda_e_diz_por_que(mundo: Mundo) -> None:
    mundo.cfg = AutopublicacaoCfg(modo="on")
    _liberar(mundo)
    ref = mundo.pronto("comentar-no-post")
    rid = mundo.db.one("SELECT id FROM learning_reviews WHERE item_ref=? ORDER BY id DESC LIMIT 1", (ref,))["id"]
    r = mundo.laco._volta(LIDER)
    assert r is not None and r.publicados == (ref,) and r.marcados == (ref,)      # a sombra segue medindo
    assert mundo.servico.entrada(LivroKind.FLUXO, "comentar-no-post").state is SkillState.PUBLISHED
    assert mundo.db.one("SELECT status FROM flows WHERE id='comentar-no-post'")["status"] == "active"
    [t] = _trilha(mundo, ref)
    assert (t["from_state"], t["to_state"], t["decided_by"]) == ("validated", "published", "sistema")
    motivo = str(t["reason"])
    assert motivo.startswith("autopublicacao_b: ") and str(rid) in motivo
    assert "2 execuções reais em 2 aparelhos" in motivo and "30 de 30 casos fechados" in motivo
    rel = mundo.auto.relatorio()
    ultima = rel["ultima_volta"]
    assert rel["publicados_pela_emenda"] == 1 and isinstance(ultima, dict) and ultima["publicados"] == 1
    # A volta seguinte não publica de novo: o fluxo saiu de `validated`.
    mundo.agora += timedelta(hours=1)
    r2 = mundo.laco._volta(LIDER)
    assert r2 is not None and r2.avaliados == 0 and r2.publicados == ()


def test_a_d1_segue_inteira_fora_do_caminho_da_emenda(mundo: Mundo) -> None:
    """A prova de que a trava não abriu para mais ninguém: o sistema pela rota genérica, a pessoa com o motivo da
    emenda e o repositório com a marca fora do caso dela são recusados."""
    ref = mundo.pronto("comentar-no-post")
    with pytest.raises(ExigeODono):
        mundo.servico.mudar_estado(LivroKind.FLUXO, "comentar-no-post", SkillState.PUBLISHED, by="sistema",
                                   reason="publicar sozinho")
    for quem in ("sistema", "painel"):
        with pytest.raises(EntradaInvalida):
            mundo.servico.mudar_estado(LivroKind.FLUXO, "comentar-no-post", SkillState.PUBLISHED, by=quem,
                                       reason="autopublicacao_b: na mão")
    with pytest.raises(EntradaInvalida):
        mundo.servico.autopublicar_fluxo("comentar-no-post", reason="sem a marca")
    e = mundo.servico.entrada(LivroKind.FLUXO, "comentar-no-post")
    assert e.native_status is not None and e.content_hash is not None
    mudanca = MudancaNativa(kind=LivroKind.FLUXO, ref="comentar-no-post", de_status=e.native_status,
                            para_status="active", de_estado=SkillState.VALIDATED, para_estado=SkillState.PUBLISHED,
                            content_hash=e.content_hash, scope_key=e.scope_key, app_version=e.app_version)
    with pytest.raises(TransicaoProibida):                                         # pessoa com a marca, no repositório
        mundo.repo.transicionar_nativo(mudanca, by="painel", reason="autopublicacao_b: x", emenda_b=True)
    with pytest.raises(ExigeODono):                                                # o sistema sem a marca
        mundo.repo.transicionar_nativo(mudanca, by="sistema", reason="autopublicacao_b: x")
    assert mundo.servico.entrada(LivroKind.FLUXO, "comentar-no-post").state is SkillState.VALIDATED
    assert _trilha(mundo, ref) == []


def test_a_emenda_so_vale_de_validated_e_nunca_para_o_candidato(mundo: Mundo) -> None:
    mundo.fluxo("ainda-candidato", status="candidate")
    with pytest.raises(TransicaoProibida):
        mundo.servico.autopublicar_fluxo("ainda-candidato", reason="autopublicacao_b: teste")
    assert mundo.servico.entrada(LivroKind.FLUXO, "ainda-candidato").state is SkillState.CANDIDATE


@pytest.mark.parametrize(("human_origin", "reaprendido", "de"), [
    (True, False, SkillState.VALIDATED),       # texto de pessoa segue com o dono
    (False, True, SkillState.VALIDATED),       # o reaprendido (30.23) segue com o dono
    (False, False, SkillState.CANDIDATE),      # pular a validação nunca
])
def test_a_regra_pura_da_emenda_e_estreita(human_origin: bool, reaprendido: bool, de: SkillState) -> None:
    with pytest.raises(TransicaoProibida):
        conferir_transicao(de, SkillState.PUBLISHED, "sistema", side_effect=True, human_origin=human_origin,
                           modo_publica=True, reaprendido=reaprendido, emenda_b=True)
    assert conferir_transicao(SkillState.VALIDATED, SkillState.PUBLISHED, "sistema", side_effect=True,
                              human_origin=False, modo_publica=True, emenda_b=True) is Actor.SYSTEM
    with pytest.raises(ExigeODono):
        conferir_transicao(SkillState.VALIDATED, SkillState.PUBLISHED, "sistema", side_effect=True,
                           human_origin=False, modo_publica=True)


def test_a_recusa_da_trava_no_meio_nao_derruba_a_volta(mundo: Mundo, monkeypatch: pytest.MonkeyPatch) -> None:
    """O veto ou a guarda do fluxo recusando: fica no log, o fluxo segue esperando o dono e a volta termina."""
    mundo.cfg = AutopublicacaoCfg(modo="on")
    _liberar(mundo)
    mundo.pronto("comentar-no-post")

    def recusa(ref: str, *, reason: str) -> None:
        raise ConflitoDeEstado("o fluxo mudou no meio")

    monkeypatch.setattr(mundo.servico, "autopublicar_fluxo", recusa)
    r = mundo.laco._volta(LIDER)
    assert r is not None and r.publicados == () and len(r.marcados) == 1
    assert mundo.servico.entrada(LivroKind.FLUXO, "comentar-no-post").state is SkillState.VALIDATED
