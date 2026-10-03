"""30.11: o curador por IA, camada de aplicação (`aprendizado-vivo.md` §8.5-8.8, §8.11), com o adaptador SIMULADO.

Tudo `simulated`: banco SQLite migrado, barramento falso, adaptador determinístico sem rede. Nenhuma IA, nenhum
aparelho, nenhum custo. O que se prova: os modos, a idempotência por (item, dossiê), a validação do parecer, o aviso
`parecer_da_ia`, o orçamento proporcional pela prioridade, o pico, a trava de líder, a triagem da conclusão e que o
`usd` não medido não vira custo.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.config import CuradorCfg, LearningCfg
from app.db import Database
from app.modules.learning.application.curador import ResultadoDaVolta
from app.modules.learning.application.ports import Ajustes, NovaEvidencia
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.ciclo import SYSTEM_ACTOR, SkillState
from app.modules.learning.domain.espera import FatosDoCatalogo
from app.modules.learning.domain.livro import Escopo, NovoItem
from app.modules.learning.domain.orcamento_do_curador import (Gatilho, Janela, MotivoDoCorte, ParametrosDoOrcamento,
                                                              Pretendente, Prioridade, estimar_custo, prioridade,
                                                              repartir)
from app.modules.learning.domain.politica_de_risco import ClasseDeRisco, conferir_aceite
from app.modules.learning.domain.vocabulario import LivroKind, Posicao, SourceKind
from app.modules.learning.infrastructure import ligar_curador
from app.modules.learning.infrastructure.curador_simulado import CuradorSimulado
from app.modules.learning.infrastructure.dossies import DossiesSql
from app.modules.learning.infrastructure.eventos import EventosNoBarramento
from app.modules.learning.infrastructure.fontes import FontesSql
from app.modules.learning.infrastructure.metricas_sql import FontesDeMetricasSql
from app.modules.learning.infrastructure.montagem import GuardaDoFluxo, montar_aprendizado
from app.modules.learning.infrastructure.revisoes_sql import RegistroDeRevisoesSql
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.modules.learning.infrastructure.sql_repository import SqlLearningRepository
from app.modules.skills.infrastructure.sql_repository import SqlSkillRepository
from app.util import to_iso

from .fake_skills import ValidadorFalso
from .fake_skills import banco as banco_migrado

S = SkillState
PACOTE = "com.instagram.android"
INICIO = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
PESSOA = "painel"
TEXTO_DA_PESSOA = "texto unico escrito pela pessoa sobre o botao"      # nunca pode ir ao dossiê nem ao registro
PRECOS = {"modelo-x": [4.0, 0.2, 5.0, 20.0]}
LIDER = lambda: 1                                                       # noqa: E731 - a trava, sempre deste backend


@dataclass
class BarramentoFalso:
    emitidos: list[dict[str, object]] = field(default_factory=list)

    def emit(self, kind: str, message: str, *, level: str = "info", instance_id: str | None = None,
             data: dict[str, object] | None = None) -> object:
        self.emitidos.append({**(data or {}), "evento": kind})
        return None

    def pareceres(self) -> list[dict[str, object]]:
        return [d for d in self.emitidos if d.get("motivo") == "parecer_da_ia"]


class CatalogoFalso:
    def __init__(self, fatos: dict[str, FatosDoCatalogo]) -> None:
        self._fatos = fatos

    def tem_catalogo(self, app: str) -> bool:
        return True

    def da_capability(self, app: str, capability: str) -> FatosDoCatalogo | None:
        return self._fatos.get(capability)


class Mundo:
    def __init__(self, db: Database, *, modo: str = "shadow", gasto_da_operacao: float = 10.0) -> None:
        self.db = db
        self.agora = INICIO
        self.cfg = CuradorCfg(modo=modo, cooldown_h=0)
        self.barramento = BarramentoFalso()
        self.ia = CuradorSimulado()
        self.catalogo = CatalogoFalso({"ALTO": FatosDoCatalogo(risco="high", efeito_externo=True)})
        habilidades = SqlSkillRepository(db, ValidadorFalso())
        self.repo = SqlLearningRepository(db, clock=lambda: to_iso(self.agora),
                                          guarda_do_fluxo=GuardaDoFluxo(db, habilidades), precos=dict)
        self.servico = LearningService(self.repo, FontesSql(db), TriagemDeCredencial(), ajustes=Ajustes,
                                       relogio=lambda: self.agora, retencao_de_logs_dias=lambda: 14,
                                       eventos=EventosNoBarramento(self.barramento), catalogo_de_risco=self.catalogo)
        self.curador = ligar_curador.ligar(self.servico, self.repo, db, TriagemDeCredencial(), config=lambda: self.cfg,
                                           precos=lambda: PRECOS, relogio=lambda: self.agora, catalogo=self.catalogo,
                                           curador_de_ia=self.ia)
        self.gasto(gasto_da_operacao)

    def gasto(self, usd: float) -> None:
        """G_W: a régua diária da operação (ontem), que o relatório já lê."""
        self.db.execute("DELETE FROM learning_daily")
        self.db.execute("INSERT INTO learning_daily(day, app_package, capability, failure_kind, driven_by, usd,"
                        " computed_at) VALUES ('2026-10-01', ?, 'X', '', 'ai', ?, ?)", (PACOTE, usd, to_iso(INICIO)))

    def licao(self, *, efeito: bool, fonte: SourceKind, capability: str = "OPEN_POST", sufixo: str = "") -> str:
        novo = NovoItem(kind=LivroKind.LICAO, escopo=Escopo(app=PACOTE, capability=capability, role="actor"),
                        content={"modelo": "alvo_ausente", "alvo": TEXTO_DA_PESSOA + sufixo},
                        summary=f"{TEXTO_DA_PESSOA}{sufixo}", source_kind=fonte, side_effect=efeito, app_version="447")
        by = PESSOA if fonte is SourceKind.MANUAL else SYSTEM_ACTOR
        return self.servico.propor(novo, by=by).id

    def evidencia(self, ref: str, origem: str, posicao: Posicao = Posicao.FOR) -> None:
        self.repo.registrar_evidencia(NovaEvidencia(item_ref=ref, stance=posicao, origin_ref=origem, simulated=True,
                                                    run_id=origem.split(":")[-1]))

    def volta(self, *, horas: float = 2.0, lider=LIDER) -> ResultadoDaVolta:   # noqa: ANN001
        self.agora += timedelta(hours=horas)
        return self.curador.uma_volta(lider)

    def revisoes(self) -> list[dict[str, object]]:
        return [dict(r) for r in self.db.query("SELECT * FROM learning_reviews ORDER BY created_at, id")]

    def custo(self, kind: LivroKind, ref: str) -> float:
        d = DossiesSql(self.db, self.servico, self.repo, self.catalogo).dossie(self.servico.entrada(kind, ref))
        assert d is not None
        return estimar_custo(d.tamanho_em_bytes(), PRECOS)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    d = banco_migrado(tmp_path, "curador.sqlite3")
    d.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('instagram','Instagram',?,0)", (PACOTE,))
    yield d
    d.close()


# ------------------------------------------------------------------ modos e trava
def test_modo_off_nao_roda(db: Database) -> None:
    m = Mundo(db, modo="off")
    m.licao(efeito=False, fonte=SourceKind.MANUAL)
    assert m.volta().rodou is False
    assert m.revisoes() == [] and m.ia.pedidos == []


def test_sem_trava_de_lider_nao_roda(db: Database) -> None:
    m = Mundo(db)
    m.licao(efeito=False, fonte=SourceKind.MANUAL)
    assert m.volta(lider=lambda: None).rodou is False
    assert m.revisoes() == [] and m.ia.pedidos == []


def test_o_laco_e_registrado_a_parte_da_curadoria(tmp_path: Path) -> None:
    db = banco_migrado(tmp_path, "montagem.sqlite3")
    try:
        servico = montar_aprendizado(db, config=LearningCfg,
                                     retencao_de_logs_dias=lambda: 14, precos=lambda: PRECOS)
        assert [laco.nome for laco in servico.lacos] == ["curador", "autopublicacao"]   # 30.34: laço próprio
    finally:
        db.close()


# ------------------------------------------------------------------ shadow: registro, idempotência, aviso
def test_shadow_grava_revisao_valida_e_avisa_o_dono_sem_transicionar(db: Database) -> None:
    m = Mundo(db)
    ref = m.licao(efeito=False, fonte=SourceKind.MANUAL)       # texto de pessoa: B, espera o dono
    m.ia.simulado = False                                     # um provedor "real" de teste: só ele avisa o dono
    trilha_antes = len(m.repo.trilha(ref))
    eventos_antes = len(m.barramento.emitidos)
    r = m.volta()
    assert r.rodou and r.revisadas == (ref,) and r.avisos == 1
    [linha] = m.revisoes()
    assert (linha["item_ref"], linha["item_kind"], linha["gatilho"], linha["validade"]) == (
        ref, "licao", Gatilho.NOVA_PENDENCIA_DO_DONO.value, "ok")
    assert (linha["classe_de_risco"], linha["politica"]) == ("B", "dono_em_lote")
    assert (linha["provedor"], linha["simulated"], linha["usd"]) == ("simulado", 0, 0)
    assert json.loads(str(linha["saida"]))["decisao"] in ("manter", "observar")
    assert TEXTO_DA_PESSOA not in str(linha["dossie"]) and TEXTO_DA_PESSOA not in str(linha["saida"])
    [aviso] = m.barramento.pareceres()
    assert len(m.barramento.emitidos) == eventos_antes + 1
    assert (aviso["evento"], aviso["kind"], aviso["faixa"], aviso["aguardando"], aviso["ref"]) == (
        "learning.needs_person", "licao", "B", True, ref)
    # A IA nunca decide: nada transicionou, e o sistema não pode aceitar o parecer sozinho.
    assert len(m.repo.trilha(ref)) == trilha_antes
    assert m.servico.entrada(LivroKind.LICAO, ref).state is S.CANDIDATE
    assert conferir_aceite(ClasseDeRisco.B, por_pessoa=False, em_lote=True) is not None


def test_parecer_do_adaptador_simulado_nunca_avisa_o_dono(db: Database) -> None:
    """O evento vai ao Telegram (28.14) sem marca de simulado: parecer falso não pode chegar ao dono."""
    m = Mundo(db)
    ref = m.licao(efeito=False, fonte=SourceKind.MANUAL)
    r = m.volta()
    assert r.revisadas == (ref,) and r.avisos == 0 and m.barramento.pareceres() == []
    [linha] = m.revisoes()
    assert (linha["simulated"], linha["validade"]) == (1, "ok")


def test_mesmo_dossie_nao_revisa_de_novo_e_evidencia_nova_revisa(db: Database) -> None:
    m = Mundo(db)
    ref = m.licao(efeito=False, fonte=SourceKind.MANUAL)
    assert m.volta().revisadas == (ref,)
    assert m.volta().revisadas == ()                            # o mesmo (item, dossiê): sem pedido novo
    assert len(m.ia.pedidos) == 1 and len(m.revisoes()) == 1
    m.evidencia(ref, "run:r1")
    assert m.volta().revisadas == (ref,)                        # hash novo
    hashes = {r["dossie_hash"] for r in m.revisoes()}
    assert len(hashes) == 2


def test_dossie_estavel_entre_dias_nao_revisa_de_novo(db: Database) -> None:
    m = Mundo(db)
    ref = m.licao(efeito=False, fonte=SourceKind.MANUAL)
    m.evidencia(ref, "run:r1")
    assert m.volta().revisadas == (ref,)
    assert m.volta(horas=30).revisadas == () and len(m.ia.pedidos) == 1   # sem fato novo, o relógio não muda o hash


def test_dossie_cortado_por_custo_nao_chama_o_provedor_de_novo(db: Database) -> None:
    m = Mundo(db, gasto_da_operacao=1000.0)
    pequenos = [m.licao(efeito=False, fonte=SourceKind.MANUAL, capability=f"P{i}", sufixo=f"-{i}") for i in range(3)]
    grande = m.licao(efeito=False, fonte=SourceKind.MANUAL, capability="GRANDE", sufixo="-g")
    for i in range(30):                                         # muitas evidências: o dossiê passa de 4× a mediana
        m.evidencia(grande, f"run:corrida-de-numero-{i:04d}-com-id-bem-comprido")
    m.cfg = CuradorCfg(modo="shadow", cooldown_h=0, m_cmax=1.05)
    for _ in range(6):                                          # o teto da hora deixa passar uma por volta
        m.volta(horas=2)
    assert {r["item_ref"] for r in m.revisoes()} == {*pequenos, grande}   # revisado, ou `recusada:custo`
    do_grande = [p for p in m.ia.pedidos if p.dossie["item"]["id"] == f"licao:{grande}"]
    assert len(do_grande) <= 1
    linhas = len(m.revisoes())
    m.volta(horas=30)
    m.volta(horas=2)
    assert len([p for p in m.ia.pedidos if p.dossie["item"]["id"] == f"licao:{grande}"]) == len(do_grande)
    assert len(m.revisoes()) == linhas                         # nem pedido nem linha nova sem fato novo


def test_cooldown_segura_o_item_mesmo_com_dossie_novo(db: Database) -> None:
    m = Mundo(db)
    m.cfg = CuradorCfg(modo="shadow", cooldown_h=24)
    ref = m.licao(efeito=False, fonte=SourceKind.MANUAL)
    assert m.volta().revisadas == (ref,)
    m.evidencia(ref, "run:r1")
    assert m.volta(horas=2).revisadas == ()
    assert m.volta(horas=23).revisadas == (ref,)


def test_citacao_inventada_vira_registro_invalido_e_nada_mais(db: Database) -> None:
    m = Mundo(db)
    m.ia.inventar_citacao = True
    ref = m.licao(efeito=False, fonte=SourceKind.MANUAL)
    eventos_antes = len(m.barramento.emitidos)
    r = m.volta()
    assert r.invalidas == (ref,) and r.revisadas == ()
    [linha] = m.revisoes()
    assert linha["validade"] == "invalida:citacao_desconhecida" and linha["saida"] is None
    assert len(m.barramento.emitidos) == eventos_antes           # nenhum aviso
    assert m.volta().invalidas == ()                             # não se repete até o dossiê mudar


def test_classe_c_gera_parecer_e_evento_nunca_aceite(db: Database) -> None:
    m = Mundo(db)
    ref = m.licao(efeito=True, fonte=SourceKind.RECOVERY, capability="ALTO")
    m.servico.mudar_estado(LivroKind.LICAO, ref, S.VALIDATED, by=SYSTEM_ACTOR, reason="prova")   # espera o dono
    m.ia.simulado = False                                     # só o provedor "real" avisa o dono
    r = m.volta()
    assert r.revisadas == (ref,)
    [linha] = m.revisoes()
    assert (linha["classe_de_risco"], linha["politica"]) == ("C", "dono_item_a_item")
    assert m.ia.pedidos[0].classe == "C" and m.ia.pedidos[0].modelo_sugerido == "escalada"
    [aviso] = m.barramento.pareceres()
    assert aviso["faixa"] == "C"
    assert m.servico.entrada(LivroKind.LICAO, ref).state is S.VALIDATED
    assert conferir_aceite(ClasseDeRisco.C, por_pessoa=False, em_lote=False) is not None
    assert conferir_aceite(ClasseDeRisco.C, por_pessoa=True, em_lote=True) is not None


def test_conclusao_com_cara_de_segredo_e_triada(db: Database) -> None:
    m = Mundo(db)
    m.ia.conclusao = "a senha da conta e abc12345"
    m.licao(efeito=False, fonte=SourceKind.MANUAL)
    assert len(m.volta().revisadas) == 1
    [linha] = m.revisoes()
    assert linha["validade"] == "ok"
    assert json.loads(str(linha["saida"]))["conclusao"] is None
    assert "abc12345" not in json.dumps(m.revisoes(), default=str)
    m.ia.conclusao = "reproduz bem em dois aparelhos"           # a conclusão inofensiva passa
    ref = m.licao(efeito=False, fonte=SourceKind.MANUAL, capability="OUTRA", sufixo="-b")
    m.volta()
    linha = next(r for r in m.revisoes() if r["item_ref"] == ref)
    assert json.loads(str(linha["saida"]))["conclusao"] == "reproduz bem em dois aparelhos"


def test_usd_nao_medido_fica_zero_e_nao_entra_como_custo_medido(db: Database) -> None:
    m = Mundo(db)
    m.licao(efeito=False, fonte=SourceKind.MANUAL)
    m.volta()
    [linha] = m.revisoes()
    assert linha["usd"] == 0                                    # a 069 é NOT NULL: 0 = não medido (30.12)
    j = RegistroDeRevisoesSql(db).janela(m.agora, 7)
    assert j.custos_medidos == () and j.gasto_medido == 0 and len(j.tamanhos_sem_medida) == 1
    assert j.gasto_da_operacao == pytest.approx(10.0)


class CuradorMedido(CuradorSimulado):
    """O adaptador do hub em teste: a resposta traz `usd`/`ai_call_id` lidos de `ai_calls` e diz se ELA é simulada."""

    def __init__(self, *, usd: float | None, ai_call_id: int | None, simulado_da_resposta: bool | None) -> None:
        super().__init__()
        self._medida = (usd, ai_call_id, simulado_da_resposta)

    def revisar(self, pedido):  # noqa: ANN001, ANN201 - mesma assinatura do simulado
        usd, ai_call_id, simulado = self._medida
        return replace(super().revisar(pedido), usd=usd, ai_call_id=ai_call_id, simulado=simulado)


def test_chamada_medida_pelo_hub_grava_o_usd_e_fica_ligada_a_revisao(db: Database) -> None:
    """075 e 30.30: a revisão guarda o `ai_call_id` e o `usd` da chamada medida, e o custo entra como MEDIDO no orçamento
    do curador (não mais pela estimativa do dossiê). O simulado do adaptador (True) perde para o da RESPOSTA (False): o
    parecer avisa o dono como o de um provedor real."""
    m = Mundo(db)
    m.ia = CuradorMedido(usd=0.0031, ai_call_id=42, simulado_da_resposta=False)
    m.curador = ligar_curador.ligar(m.servico, m.repo, db, TriagemDeCredencial(), config=lambda: m.cfg,
                                    precos=lambda: PRECOS, relogio=lambda: m.agora, catalogo=m.catalogo,
                                    curador_de_ia=m.ia)
    ref = m.licao(efeito=False, fonte=SourceKind.MANUAL)
    r = m.volta()
    assert r.revisadas == (ref,) and r.avisos == 1
    [linha] = m.revisoes()
    assert (linha["usd"], linha["ai_call_id"], linha["simulated"]) == (0.0031, 42, 0)
    j = RegistroDeRevisoesSql(db).janela(m.agora, 7)
    assert j.custos_medidos == (0.0031,) and j.tamanhos_sem_medida == ()


def _chamada(db: Database, *, provider: str = "anthropic") -> int:
    """Uma linha de `ai_calls` do modelo de `PRECOS`: 1000 de entrada e 500 de saída = US$ 0,014."""
    db.execute("INSERT INTO ai_calls(ts, role, model, provider, input_tokens, output_tokens) VALUES (?,?,?,?,?,?)",
               (to_iso(INICIO), "curador", "modelo-x", provider, 1000, 500))
    r = db.one("SELECT MAX(id) AS id FROM ai_calls")
    assert r is not None
    return int(r["id"])


@pytest.mark.parametrize(("provider", "usd"), [("anthropic", 0.014), ("simulated", 0.0)])
def test_revisao_sem_usd_gravado_tem_o_custo_medido_na_chamada_ligada(db: Database, provider: str,
                                                                       usd: float) -> None:
    """I3 (03/10): as revisões de antes do 30.30 gravaram `usd = 0`, mas têm `ai_call_id`. O custo vem da chamada ligada
    pela regra do `/api/usage` (tokens × preço; simulado a US$ 0), nas métricas, na lista e na janela do orçamento.
    Sem `precos`, o registro lê como antes."""
    m = Mundo(db)
    chamada = _chamada(db, provider=provider)
    m.ia = CuradorMedido(usd=None, ai_call_id=chamada, simulado_da_resposta=False)
    m.curador = ligar_curador.ligar(m.servico, m.repo, db, TriagemDeCredencial(), config=lambda: m.cfg,
                                    precos=lambda: PRECOS, relogio=lambda: m.agora, catalogo=m.catalogo,
                                    curador_de_ia=m.ia)
    m.licao(efeito=False, fonte=SourceKind.MANUAL)
    m.volta()
    [linha] = m.revisoes()
    assert (linha["usd"], linha["ai_call_id"]) == (0, chamada)          # gravado como não medido
    assert RegistroDeRevisoesSql(db).janela(m.agora, 7).custos_medidos == ()
    j = RegistroDeRevisoesSql(db, precos=lambda: PRECOS).janela(m.agora, 7)
    if usd:
        assert j.custos_medidos == (pytest.approx(usd),) and j.tamanhos_sem_medida == ()
    else:
        assert j.custos_medidos == () and len(j.tamanhos_sem_medida) == 1      # simulado: segue sem medida
    fontes = FontesDeMetricasSql(db, precos=lambda: PRECOS)
    [lida] = fontes.revisoes(to_iso(INICIO), to_iso(m.agora + timedelta(hours=1)))
    assert lida.usd == pytest.approx(usd)
    pagina = fontes.lista_de_revisoes(app=None, decisao=None, desde=None, limite=10, cursor=None)
    assert [x.usd for x in pagina.revisoes] == [pytest.approx(usd)]


def test_resposta_simulada_nunca_avisa_mesmo_com_adaptador_real(db: Database) -> None:
    """A resposta que se diz simulada não avisa o dono, mesmo com o adaptador declarando provedor real; sem chamada,
    nada de `ai_call_id`, e o `usd` que ela traga não vira custo (30.30: simulada grava 0)."""
    m = Mundo(db)
    m.ia = CuradorMedido(usd=0.5, ai_call_id=None, simulado_da_resposta=True)
    m.ia.simulado = False
    m.curador = ligar_curador.ligar(m.servico, m.repo, db, TriagemDeCredencial(), config=lambda: m.cfg,
                                    precos=lambda: PRECOS, relogio=lambda: m.agora, catalogo=m.catalogo,
                                    curador_de_ia=m.ia)
    ref = m.licao(efeito=False, fonte=SourceKind.MANUAL)
    r = m.volta()
    assert r.revisadas == (ref,) and r.avisos == 0
    [linha] = m.revisoes()
    assert (linha["usd"], linha["ai_call_id"], linha["simulated"]) == (0, None, 1)
    assert RegistroDeRevisoesSql(db).janela(m.agora, 7).custos_medidos == ()


# ------------------------------------------------------------------ orçamento e prioridade
def test_classe_a_so_com_sobra_e_orcamento_corta_pela_prioridade(db: Database) -> None:
    m = Mundo(db)
    # A: sem efeito, sem texto de pessoa, publicada e contestada (degradando). B: texto de pessoa à espera.
    a = m.licao(efeito=False, fonte=SourceKind.RECOVERY, capability="NAV")
    m.servico.mudar_estado(LivroKind.LICAO, a, S.VALIDATED, by=PESSOA, reason="ok")
    m.servico.mudar_estado(LivroKind.LICAO, a, S.PUBLISHED, by=PESSOA, reason="ok")
    m.evidencia(a, "run:r9", Posicao.AGAINST)
    b = m.licao(efeito=False, fonte=SourceKind.MANUAL, capability="TEXTO")
    c_b = m.custo(LivroKind.LICAO, b)
    m.gasto(1.2 * c_b / 0.10)                                  # α·G_W = 1,2 revisão: só cabe uma
    r = m.volta()
    assert r.revisadas == (b,)
    assert r.cortados == {a: MotivoDoCorte.ORCAMENTO_DA_JANELA}
    # Com sobra, a A recebe parecer só de registro: nenhum aviso para ela, e nada a aceitar.
    m.gasto(10.0)
    r = m.volta()
    assert r.revisadas == (a,) and r.avisos == 0
    linha = next(x for x in m.revisoes() if x["item_ref"] == a)
    assert (linha["classe_de_risco"], linha["gatilho"]) == ("A", Gatilho.DEGRADANDO.value)
    assert all(x["ref"] != a for x in m.barramento.pareceres())
    assert conferir_aceite(ClasseDeRisco.A, por_pessoa=True, em_lote=False) is not None
    assert m.servico.entrada(LivroKind.LICAO, a).state is S.PUBLISHED


def test_sem_operacao_nao_ha_curadoria(db: Database) -> None:
    m = Mundo(db, gasto_da_operacao=0.0)
    ref = m.licao(efeito=False, fonte=SourceKind.MANUAL)
    r = m.volta()
    assert r.revisadas == () and r.cortados == {ref: MotivoDoCorte.ORCAMENTO_DA_JANELA} and r.orcamento == 0
    assert m.revisoes() == [] and m.ia.pedidos == []          # o corte não gasta a chave (item, dossiê)


def test_orcamento_do_hub_interrompe_o_lote_sem_laco(db: Database) -> None:
    m = Mundo(db)
    m.ia.orcamento_esgotado = True
    ref = m.licao(efeito=False, fonte=SourceKind.MANUAL)
    r = m.volta()
    assert r.cortados == {ref: MotivoDoCorte.LOTE_INTERROMPIDO} and m.revisoes() == []
    m.ia.orcamento_esgotado = False
    assert m.volta().revisadas == (ref,)                       # na volta seguinte, sem ter gasto a chave


def _p(chave: str, prio: Prioridade, custo: float = 1.0) -> Pretendente:
    return Pretendente(chave=chave, prioridade=prio, custo_estimado=custo)


def test_repartir_ordem_estrita_e_motivo_proprio() -> None:
    p = ParametrosDoOrcamento()
    janela = Janela(gasto_da_operacao=25.0)                     # α·G = 2,5 revisões de 1,0
    partilha = repartir([_p("a", Prioridade.CLASSE_A), _p("b", Prioridade.CLASSE_B, 0.1), _p("c", Prioridade.CLASSE_C),
                         _p("x", Prioridade.CONTRA_EM_PUBLICADO), _p("f", Prioridade.FALHA_RECORRENTE)],
                        janela, ParametrosDoOrcamento(janela_dias=1))
    assert partilha.aprovados == ("x", "c")
    # a classe B (barata, cabia) não passa na frente depois do corte: a ordem é estrita
    assert partilha.cortados == {"f": MotivoDoCorte.ORCAMENTO_DA_JANELA, "b": MotivoDoCorte.ORCAMENTO_DA_JANELA,
                                 "a": MotivoDoCorte.ORCAMENTO_DA_JANELA}
    assert MotivoDoCorte.ORCAMENTO_DA_JANELA.value == "orcamento_da_janela" != "fatia_curador"
    assert p.alfa == 0.10 and p.k == 1.5 and p.janela_dias == 7 and p.m_cmax == 4


def test_pedido_da_pessoa_vai_na_frente_menos_na_classe_a_e_sob_o_teto() -> None:
    """30.30: o pedido de pessoa é a prioridade máxima, também no pico; a classe A segue só com sobra (dono, 02/10), e o
    teto da hora e o orçamento da janela continuam valendo para ele."""
    for classe, gatilho in ((ClasseDeRisco.B, Gatilho.A_REVISAR), (ClasseDeRisco.C, Gatilho.NOVA_PENDENCIA_DO_DONO)):
        assert prioridade(classe, gatilho, publicado=False, pedido=True) is Prioridade.PEDIDO_DA_PESSOA
    assert prioridade(ClasseDeRisco.A, Gatilho.A_REVISAR, publicado=False, pedido=True) is Prioridade.CLASSE_A
    assert prioridade(ClasseDeRisco.B, Gatilho.A_REVISAR, publicado=False) is Prioridade.CLASSE_B
    janela = Janela(gasto_da_operacao=1000.0, revisoes_antes_de_hoje=6)   # pico: só as prioridades 0, 1 e 2
    todos = [_p("x", Prioridade.CONTRA_EM_PUBLICADO), _p("c", Prioridade.CLASSE_C), _p("f", Prioridade.FALHA_RECORRENTE),
             _p("b", Prioridade.CLASSE_B), _p("p", Prioridade.PEDIDO_DA_PESSOA)]
    partilha = repartir(todos, janela, ParametrosDoOrcamento(k=100))
    assert partilha.pico is True and partilha.aprovados == ("p", "x", "c")
    # o orçamento da janela (α·G = 2,5 revisões) corta depois do pedido, na ordem estrita
    curta = repartir(todos, Janela(gasto_da_operacao=25.0), ParametrosDoOrcamento(janela_dias=1))
    assert curta.aprovados == ("p", "x")
    # com a hora já gasta, nem o pedido passa: espera a volta seguinte
    gasta = repartir(todos, Janela(gasto_da_operacao=1000.0, gasto_da_ultima_hora=999.0), ParametrosDoOrcamento(k=100))
    assert gasta.aprovados == () and gasta.cortados["p"] is MotivoDoCorte.GASTO_DA_HORA


def test_pico_de_entrada_deixa_so_as_prioridades_1_e_2() -> None:
    janela = Janela(gasto_da_operacao=1000.0, revisoes_antes_de_hoje=6)   # média de 1 por dia na janela
    todos = [_p("x", Prioridade.CONTRA_EM_PUBLICADO), _p("c", Prioridade.CLASSE_C),
             _p("f", Prioridade.FALHA_RECORRENTE), _p("b", Prioridade.CLASSE_B), _p("a", Prioridade.CLASSE_A)]
    partilha = repartir(todos, janela, ParametrosDoOrcamento(k=100))
    assert partilha.pico is True
    assert set(partilha.aprovados) == {"x", "c"}
    assert {k: v.value for k, v in partilha.cortados.items()} == {"f": "pico_de_entrada", "b": "pico_de_entrada",
                                                                   "a": "pico_de_entrada"}
    # sem histórico, o primeiro dia nunca é pico; e com poucos itens também não (piso de amostra)
    assert repartir(todos[:4], janela, ParametrosDoOrcamento(k=100)).pico is False
    assert repartir(todos, Janela(gasto_da_operacao=1000.0), ParametrosDoOrcamento(k=100)).pico is False


def test_revisao_cara_demais_e_recusada_por_custo() -> None:
    partilha = repartir([_p("a", Prioridade.CLASSE_B, 1.0), _p("b", Prioridade.CLASSE_B, 1.0),
                         _p("c", Prioridade.CLASSE_B, 50.0)], Janela(gasto_da_operacao=1e6),
                        ParametrosDoOrcamento(janela_dias=1, k=100))
    assert partilha.recusados_por_custo == ("c",) and partilha.custo_maximo == 4.0


def test_modelo_barato_nao_recusa_as_estimativas_em_silencio() -> None:
    """30.30: com o curador num modelo barato (o Haiku da D-1), o custo MEDIDO por revisão (~0,003) fica bem abaixo da
    estimativa, que usa o preço do modelo mais caro (~0,014 nas 21 revisões reais de 03/10). Comparar uma com a outra
    dava c_max ≈ 0,012 e recusava tudo por custo; agora o c_max é das estimativas, e o medido só entra no c̄."""
    medidos = (0.003, 0.0029, 0.0031, 0.003, 0.0032)
    tipicos = [_p(f"r{i}", Prioridade.CLASSE_B, 0.014 + i / 10_000) for i in range(5)]
    janela = Janela(gasto_da_operacao=1e6, custos_medidos=medidos)
    partilha = repartir(tipicos, janela, ParametrosDoOrcamento(janela_dias=1, k=100))
    assert partilha.recusados_por_custo == () and len(partilha.aprovados) == 5
    assert partilha.custo_maximo == pytest.approx(4 * 0.0142)
    assert partilha.custo_medio == pytest.approx(sum(medidos) / len(medidos))     # o medido segue no c̄
    # o que destoa das outras estimativas continua recusado
    caro = repartir([*tipicos, _p("caro", Prioridade.CLASSE_B, 0.5)], janela, ParametrosDoOrcamento(janela_dias=1, k=100))
    assert caro.recusados_por_custo == ("caro",)


def test_classe_a_nunca_passa_a_frente_nem_contestada() -> None:
    assert prioridade(ClasseDeRisco.A, Gatilho.CONFLITO, publicado=True) is Prioridade.CLASSE_A
    assert prioridade(ClasseDeRisco.B, Gatilho.DEGRADANDO, publicado=True) is Prioridade.CONTRA_EM_PUBLICADO
    assert prioridade(ClasseDeRisco.C, Gatilho.NOVA_PENDENCIA_DO_DONO, publicado=False) is Prioridade.CLASSE_C
    assert prioridade(ClasseDeRisco.B, Gatilho.GRUPO_DE_FALHA_ACIMA_DO_MINIMO,
                      publicado=False) is Prioridade.FALHA_RECORRENTE
    assert prioridade(ClasseDeRisco.B, Gatilho.A_REVISAR, publicado=False) is Prioridade.CLASSE_B


class CuradorComProvedorPorResposta(CuradorSimulado):
    """A corrida do provedor: o `provedor` do adaptador é estado compartilhado (o da ÚLTIMA revisão); o da resposta é o dela."""

    def __init__(self, provedor_da_resposta: str | None) -> None:
        super().__init__()
        self.provedor = "ultimo-do-estado-compartilhado"
        self._da_resposta = provedor_da_resposta

    def revisar(self, pedido):  # noqa: ANN001, ANN201 - mesma assinatura do simulado
        return replace(super().revisar(pedido), provedor=self._da_resposta)


@pytest.mark.parametrize(("da_resposta", "gravado"), [("openai", "openai"),
                                                      (None, "ultimo-do-estado-compartilhado")])
def test_provedor_gravado_e_o_da_resposta_e_so_sem_ele_o_do_adaptador(db: Database, da_resposta: str | None,
                                                                      gravado: str) -> None:
    m = Mundo(db)
    m.ia = CuradorComProvedorPorResposta(da_resposta)
    m.curador = ligar_curador.ligar(m.servico, m.repo, db, TriagemDeCredencial(), config=lambda: m.cfg,
                                    precos=lambda: PRECOS, relogio=lambda: m.agora, catalogo=m.catalogo,
                                    curador_de_ia=m.ia)
    ref = m.licao(efeito=False, fonte=SourceKind.MANUAL)
    assert m.volta().revisadas == (ref,)
    [linha] = m.revisoes()
    assert linha["provedor"] == gravado


def test_parar_o_curador_interrompe_o_lote_entre_itens_e_impede_volta_nova(db: Database) -> None:
    """Desligamento: a volta não pede o item seguinte ao provedor (nem grava) depois de `parar`."""
    m = Mundo(db, gasto_da_operacao=1000.0)
    m.cfg = CuradorCfg(modo="shadow", cooldown_h=0, janela_dias=1, k=100)   # folga: os dois itens passam pelo corte
    primeiro = m.licao(efeito=False, fonte=SourceKind.MANUAL, sufixo="-a")
    segundo = m.licao(efeito=False, fonte=SourceKind.MANUAL, sufixo="-b")
    chamadas: list[str] = []

    class ParaNoPrimeiro(CuradorSimulado):
        def revisar(self, pedido):  # noqa: ANN001, ANN201
            chamadas.append(pedido.dossie_hash)
            m.curador.parar()                   # o desligamento chega com o primeiro item no provedor
            return super().revisar(pedido)

    m.curador = ligar_curador.ligar(m.servico, m.repo, db, TriagemDeCredencial(), config=lambda: m.cfg,
                                    precos=lambda: PRECOS, relogio=lambda: m.agora, catalogo=m.catalogo,
                                    curador_de_ia=ParaNoPrimeiro())
    r = m.volta()
    assert len(chamadas) == 1 and len(r.revisadas) == 1                         # o em curso termina e grava
    assert set(r.cortados.values()) == {MotivoDoCorte.LOTE_INTERROMPIDO} and len(r.cortados) == 1
    assert {primeiro, segundo} == set(r.revisadas) | set(r.cortados)
    assert m.volta().rodou is False and len(chamadas) == 1                      # volta nova nem começa


# ------------------------------------------------------------------ 30.31: o laço da validação automática
@dataclass(frozen=True)
class ChegadaFalsa:
    id: str
    item_ref: str
    item_kind: str


@dataclass
class ValidacaoFalsa:
    """O lado do curador da validação (`ValidacaoDoCurador`): grava o que recebeu e devolve as chegadas do teste."""

    pareceres: list[tuple[str, str, str, tuple[str, ...]]] = field(default_factory=list)
    vindas: list[ChegadaFalsa] = field(default_factory=list)
    revisados: list[tuple[str, str]] = field(default_factory=list)

    def ao_parecer(self, e, review_id, parecer, risco):  # noqa: ANN001, ANN201 - a assinatura do Protocol
        self.pareceres.append((e.trail_ref, review_id, parecer.decisao.value, tuple(f.value for f in parecer.falta)))
        return f"lv-{len(self.pareceres)}"

    def chegadas(self) -> list[ChegadaFalsa]:
        return list(self.vindas)

    def revisado(self, pedido_id: str, review_id: str) -> None:
        self.revisados.append((pedido_id, review_id))
        self.vindas = [c for c in self.vindas if c.id != pedido_id]


class CuradorQuePedeEvidencia(CuradorSimulado):
    """Um provedor "real" de teste que pede uma execução real."""

    def revisar(self, pedido):  # noqa: ANN001, ANN201 - mesma assinatura do simulado
        r = super().revisar(pedido)
        return replace(r, bruto={**r.bruto, "decisao": "pedir_evidencia", "falta": ["execucao_real"]}, simulado=False)


def _com_validacao(m: Mundo, ia: CuradorSimulado) -> ValidacaoFalsa:
    m.ia = ia
    m.curador = ligar_curador.ligar(m.servico, m.repo, m.db, TriagemDeCredencial(), config=lambda: m.cfg,
                                    precos=lambda: PRECOS, relogio=lambda: m.agora, catalogo=m.catalogo,
                                    curador_de_ia=m.ia)
    m.curador.validacao = v = ValidacaoFalsa()
    return v


def test_parecer_real_que_pede_evidencia_chega_a_validacao_com_a_revisao(db: Database) -> None:
    m = Mundo(db)
    v = _com_validacao(m, CuradorQuePedeEvidencia())
    ref = m.licao(efeito=False, fonte=SourceKind.MANUAL)
    assert m.volta().revisadas == (ref,)
    [linha] = m.revisoes()
    assert v.pareceres == [(ref, linha["id"], "pedir_evidencia", ("execucao_real",))]
    assert v.revisados == []


def test_parecer_simulado_nunca_chega_a_validacao(db: Database) -> None:
    """O adaptador simulado não dispara execução real: o parecer falso não vira pedido."""
    m = Mundo(db)
    v = _com_validacao(m, CuradorSimulado())
    ref = m.licao(efeito=False, fonte=SourceKind.MANUAL)
    assert m.volta().revisadas == (ref,)
    assert v.pareceres == [] and v.revisados == []


def test_evidencia_chegou_pula_o_cooldown_e_fecha_a_chegada_com_a_revisao_nova(db: Database) -> None:
    m = Mundo(db)
    m.cfg = CuradorCfg(modo="shadow", cooldown_h=24)
    v = _com_validacao(m, CuradorQuePedeEvidencia())
    ref = m.licao(efeito=False, fonte=SourceKind.MANUAL)
    assert m.volta().revisadas == (ref,)
    m.evidencia(ref, "run:r-validacao")                       # a evidência nova muda o dossiê
    assert m.volta(horas=1).revisadas == ()                   # sem a chegada, o cooldown segura o item
    v.vindas = [ChegadaFalsa("lv-1", ref, "licao")]
    assert m.volta(horas=1).revisadas == (ref,)
    primeira, segunda = m.revisoes()
    assert segunda["gatilho"] == Gatilho.EVIDENCIA_CHEGOU.value
    assert v.revisados == [("lv-1", segunda["id"])]
    assert [p[1] for p in v.pareceres] == [primeira["id"], segunda["id"]]   # o parecer novo pode pedir de novo
