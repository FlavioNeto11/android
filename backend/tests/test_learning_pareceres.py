"""30.17: o parecer da IA diante da pessoa (`aprendizado-vivo.md` §8.8, §8.9 e §11.2): quando aparece, o que aceitar
faz, o rótulo de cada decisão e o pedido de revisão.

Tudo `simulated`: banco SQLite migrado, barramento falso e um provedor DE TESTE que devolve a decisão pedida (`simulado`
falso quando o teste precisa de um parecer "real", porque o simulado nunca é aceito). Nenhuma IA, nenhum aparelho,
nenhum custo. O que se prova: em `shadow` o parecer pendente fica oculto e a decisão às cegas vira rótulo; em `on` ele
aparece na fila e no detalhe com o passo do aceite; o `review_id` faz `aceitou`/`recusou` e nunca trava a pessoa; o
gesto confere a classe (A só registro, C nunca em lote, a classe de agora endurece); o simulado não se aceita; a
transição recusada desfaz o aceite; e o pedido de revisão só vale em `on` e só pula o cooldown.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.config import CuradorCfg
from app.db import Database
from app.modules.learning.application.curador import _textos_livres  # noqa: PLC2701 - a regra da triagem, direta
from app.modules.learning.application.pareceres import ServicoDePareceres
from app.modules.learning.application.ports import (Ajustes, NovaEvidencia, NovaRevisao, PedidoDeRevisao,
                                                    RespostaDeRevisao)
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.ciclo import SYSTEM_ACTOR, ConflitoDeEstado, SkillState
from app.modules.learning.domain.curador import Confianca, Decisao, Parecer
from app.modules.learning.domain.espera import FatosDoCatalogo
from app.modules.learning.domain.livro import AcaoPermitida, Escopo, NovoItem, rotulo_do_passo
from app.modules.learning.domain.orcamento_do_curador import Gatilho
from app.modules.learning.domain.parecer import (DecisaoDaPessoa, DecisaoFinal, Direcao, RecusaDoGesto, RevisaoGravada,
                                                 acao_do_aceite, conferir_gesto, decisao_pela_transicao, direcao,
                                                 direcao_da_acao, mais_restritiva, parecer_gravado, parecer_visivel)
from app.modules.learning.domain.politica_de_risco import ClasseDeRisco
from app.modules.learning.domain.vocabulario import LivroKind, Modo, Posicao, SignalKind, SourceKind
from app.modules.learning.infrastructure import ligar_curador
from app.modules.learning.infrastructure.eventos import EventosNoBarramento
from app.modules.learning.infrastructure.fontes import FontesSql
from app.modules.learning.infrastructure.montagem import GuardaDoFluxo
from app.modules.learning.infrastructure.revisoes_sql import RegistroDeRevisoesSql
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.modules.learning.infrastructure.sql_repository import SqlLearningRepository
from app.modules.learning.presentation.router import router as learning_router
from app.modules.skills.infrastructure.sql_repository import SqlSkillRepository
from app.util import to_iso

from .fake_skills import ValidadorFalso
from .fake_skills import banco as banco_migrado

S = SkillState
PACOTE = "com.instagram.android"
INICIO = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
PESSOA = "painel"                                                       # quem escreveu a nota (o nascimento)
ROTA = "panel"                                                          # quem decide pela rota, sem sessão de operador
PRECOS = {"modelo-x": [4.0, 0.2, 5.0, 20.0]}
LIDER = lambda: 1                                                       # noqa: E731 - a trava, sempre deste backend


@dataclass
class BarramentoFalso:
    emitidos: list[dict[str, object]] = field(default_factory=list)

    def emit(self, kind: str, message: str, *, level: str = "info", instance_id: str | None = None,
             data: dict[str, object] | None = None) -> object:
        self.emitidos.append({**(data or {}), "evento": kind})
        return None


class CatalogoFalso:
    def __init__(self, fatos: dict[str, FatosDoCatalogo]) -> None:
        self.fatos = fatos

    def tem_catalogo(self, app: str) -> bool:
        return True

    def da_capability(self, app: str, capability: str) -> FatosDoCatalogo | None:
        return self.fatos.get(capability)


@dataclass
class CuradorDeTeste:
    """Provedor DE TESTE: devolve a decisão pedida, citando o próprio item. `simulado` falso faz o parecer "real"."""

    decisao: str = "aprovar"
    provedor: str = "teste"
    simulado: bool = False
    pedidos: list[PedidoDeRevisao] = field(default_factory=list)

    def revisar(self, pedido: PedidoDeRevisao) -> RespostaDeRevisao:
        self.pedidos.append(pedido)
        item = pedido.dossie["item"]
        assert isinstance(item, dict)
        return RespostaDeRevisao(bruto={"decisao": self.decisao, "evidencias_citadas": [str(item["id"])],
                                        "faixa": pedido.classe, "causa": "reproduz_bem"},
                                 probabilidade=0.9, modelo="modelo-de-teste", usd=None, ai_call_id=None)


class Mundo:
    def __init__(self, db: Database, *, modo: str = "on") -> None:
        self.db = db
        self.agora = INICIO
        self.cfg = CuradorCfg(modo=modo, cooldown_h=0)
        self.barramento = BarramentoFalso()
        self.ia = CuradorDeTeste()
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
        pareceres = self.servico.extensao(ServicoDePareceres)
        assert pareceres is not None
        self.pareceres = pareceres
        # Gasto de operação alto: o orçamento proporcional não corta nada aqui (o corte é do teste do curador).
        self.db.execute("INSERT INTO learning_daily(day, app_package, capability, failure_kind, driven_by, usd,"
                        " computed_at) VALUES ('2026-10-01', ?, 'X', '', 'ai', 1000.0, ?)", (PACOTE, to_iso(INICIO)))

    def modo(self, modo: str, **kw: float) -> None:
        self.cfg = CuradorCfg(modo=modo, cooldown_h=kw.get("cooldown_h", 0))

    def licao_b(self, sufixo: str = "") -> str:
        """Texto de pessoa sem efeito: classe B, candidata que espera o dono."""
        novo = NovoItem(kind=LivroKind.LICAO, escopo=Escopo(app=PACOTE, capability=f"OPEN_POST{sufixo}", role="actor"),
                        content={"modelo": "alvo_ausente", "alvo": f"botao{sufixo}"}, summary=f"nota da pessoa{sufixo}",
                        source_kind=SourceKind.MANUAL, side_effect=False, app_version="447")
        return self.servico.propor(novo, by=PESSOA).id

    def licao_c(self, sufixo: str = "") -> str:
        """Efeito em capability de alto risco: classe C, validada pelo sistema, espera o dono."""
        novo = NovoItem(kind=LivroKind.LICAO, escopo=Escopo(app=PACOTE, capability="ALTO", role="actor"),
                        content={"modelo": "alvo_ausente", "alvo": f"enviar{sufixo}"}, summary=f"envio{sufixo}",
                        source_kind=SourceKind.RECOVERY, side_effect=True, app_version="447")
        ref = self.servico.propor(novo, by=SYSTEM_ACTOR).id
        self.servico.mudar_estado(LivroKind.LICAO, ref, S.VALIDATED, by=SYSTEM_ACTOR, reason="prova")
        return ref

    def licao_a(self) -> str:
        novo = NovoItem(kind=LivroKind.LICAO, escopo=Escopo(app=PACOTE, capability="NAV", role="actor"),
                        content={"modelo": "alvo_ausente", "alvo": "aba"}, summary="navegar",
                        source_kind=SourceKind.RECOVERY, side_effect=False, app_version="447")
        return self.servico.propor(novo, by=SYSTEM_ACTOR).id

    def volta(self, *, horas: float = 2.0) -> None:
        """Uma volta do laço; o teto da hora pode deixar parte para a próxima, então gira até nada mudar (até 5)."""
        for _ in range(5):
            antes = len(self.ia.pedidos)
            self.agora += timedelta(hours=horas)
            self.curador.uma_volta(LIDER)
            if len(self.ia.pedidos) == antes:
                break

    def estado(self, ref: str) -> SkillState | None:
        return self.servico.entrada(LivroKind.LICAO, ref).state

    def revisao(self, ref: str) -> dict[str, object]:
        [linha] = [dict(r) for r in self.db.query("SELECT * FROM learning_reviews WHERE item_ref=? ORDER BY"
                                                  " created_at DESC LIMIT 1", (ref,))]
        return linha

    def gravar_revisao(self, ref: str, *, decisao: Decisao, classe: ClasseDeRisco, estado: str,
                       simulado: bool = False) -> str:
        """Uma revisão gravada direto (sem o laço): para a classe A e o parecer de outro estado."""
        parecer = Parecer(decisao=decisao, evidencias_citadas=(f"licao:{ref}",))
        rid = RegistroDeRevisoesSql(self.db).gravar(NovaRevisao(
            item_ref=ref, item_kind="licao", scope_app=PACOTE, gatilho="nova_pendencia_do_dono",
            dossie_hash=f"h-{ref}-{estado}-{decisao.value}", dossie={"item": {"estado": estado}}, template_id="curador",
            template_versao="dossie-v1", provedor="teste", modelo="m", simulated=simulado, validade="ok",
            saida=parecer.como_dados(), classe_de_risco=classe.value, politica="teste"), self.agora)
        assert rid is not None
        return rid

    def sinais(self, kind: SignalKind) -> list[dict[str, object]]:
        return [dict(r) for r in self.db.query("SELECT * FROM learning_signals WHERE kind=?", (kind.value,))]


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    d = banco_migrado(tmp_path, "pareceres.sqlite3")
    d.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('instagram','Instagram',?,0)", (PACOTE,))
    yield d
    d.close()


@pytest.fixture
def mundo(db: Database) -> Mundo:
    return Mundo(db)


@pytest.fixture
async def cliente(mundo: Mundo) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(learning_router)
    app.state.poc = SimpleNamespace(learning=mundo.servico)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


# ------------------------------------------------------------------ domínio: tabelas
def test_toda_decisao_tem_lado_e_toda_acao_da_pessoa_tambem() -> None:
    assert {d: direcao(d) for d in Decisao} == {
        Decisao.APROVAR: Direcao.SOBE, Decisao.REBAIXAR: Direcao.DESCE, Decisao.DESATIVAR: Direcao.DESCE,
        Decisao.POSSIVELMENTE_OBSOLETO: Direcao.DESCE, Decisao.SUBSTITUIR: Direcao.DESCE, Decisao.FUNDIR: Direcao.DESCE,
        Decisao.OBSERVAR: Direcao.ESPERA, Decisao.PEDIR_EVIDENCIA: Direcao.ESPERA, Decisao.MANTER: Direcao.ESPERA}
    rotulos = {rotulo_do_passo(de, para) for de in S for para in S} - {None}
    assert rotulos and all(direcao_da_acao(str(r)) is not None for r in rotulos)
    assert direcao_da_acao("publicar_sozinho") is None


def _acoes(*rotulos_e_destinos: tuple[str, SkillState]) -> tuple[AcaoPermitida, ...]:
    return tuple(AcaoPermitida(to, rotulo) for rotulo, to in rotulos_e_destinos)


@pytest.mark.parametrize(("decisao", "acoes", "esperado"), [
    (Decisao.APROVAR, _acoes(("validar", S.VALIDATED), ("rejeitar", S.DISABLED)), "validar"),
    (Decisao.APROVAR, _acoes(("aprovar", S.PUBLISHED), ("rejeitar", S.DISABLED)), "aprovar"),
    (Decisao.APROVAR, _acoes(("reativar", S.PUBLISHED)), "reativar"),
    (Decisao.APROVAR, _acoes(("aposentar", S.DEPRECATED), ("desligar", S.DISABLED)), None),   # já publicado
    # Descer é desligar ou rejeitar, nunca aposentar (a receita aposentada não volta), mesmo quando aposentar existe.
    (Decisao.REBAIXAR, _acoes(("aposentar", S.DEPRECATED), ("desligar", S.DISABLED)), "desligar"),
    (Decisao.DESATIVAR, _acoes(("validar", S.VALIDATED), ("rejeitar", S.DISABLED)), "rejeitar"),
    (Decisao.POSSIVELMENTE_OBSOLETO, _acoes(("aposentar", S.DEPRECATED)), None),
    (Decisao.OBSERVAR, _acoes(("validar", S.VALIDATED)), None),
    (Decisao.SUBSTITUIR, _acoes(("desligar", S.DISABLED)), None),           # aceitar é concordar; age-se no alvo
    (Decisao.FUNDIR, _acoes(("desligar", S.DISABLED)), None),
])
def test_o_passo_do_aceite(decisao: Decisao, acoes: tuple[AcaoPermitida, ...], esperado: str | None) -> None:
    acao = acao_do_aceite(decisao, acoes)
    assert (acao.rotulo if acao is not None else None) == esperado


def test_o_parecer_pendente_so_aparece_em_on() -> None:
    assert parecer_visivel(Modo.ON, decidido=False) and parecer_visivel(Modo.ON, decidido=True)
    for modo in (Modo.SHADOW, Modo.OFF):
        assert not parecer_visivel(modo, decidido=False)
        assert parecer_visivel(modo, decidido=True)                 # depois da decisão, a pessoa vê o que a IA disse


def test_o_rotulo_as_cegas_e_vendo() -> None:
    assert decisao_pela_transicao(Decisao.APROVAR, "validar", viu=False) == DecisaoDaPessoa("validar", False)
    assert decisao_pela_transicao(Decisao.OBSERVAR, "aprovar", viu=False) == DecisaoDaPessoa("aprovar", True)
    assert decisao_pela_transicao(Decisao.REBAIXAR, "desligar", viu=True) == DecisaoDaPessoa(DecisaoFinal.ACEITOU,
                                                                                             False)
    assert decisao_pela_transicao(Decisao.APROVAR, "rejeitar", viu=True) == DecisaoDaPessoa(DecisaoFinal.RECUSOU, True)


def _revisao(**kw: object) -> RevisaoGravada:
    base: dict[str, object] = dict(
        id="lr-1", criado_em="2026-10-02T12:00:00Z", item_ref="li-1", item_kind="licao", gatilho="a_revisar",
        validade="ok", classe=ClasseDeRisco.B, politica="dono_em_lote", simulated=False, provedor="teste", modelo="m",
        estado_no_parecer="candidate", parecer=Parecer(decisao=Decisao.APROVAR, evidencias_citadas=("licao:li-1",)))
    base.update(kw)
    return RevisaoGravada(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize(("revisao", "modo", "classe", "em_lote", "esperado"), [
    (_revisao(), Modo.ON, ClasseDeRisco.B, True, None),
    (_revisao(validade="invalida:sem_citacao", parecer=None), Modo.ON, ClasseDeRisco.B, False,
     RecusaDoGesto.INVALIDO),
    (_revisao(decisao_final="aceitou"), Modo.ON, ClasseDeRisco.B, False, RecusaDoGesto.JA_DECIDIDO),
    (_revisao(), Modo.SHADOW, ClasseDeRisco.B, False, RecusaDoGesto.OCULTO),
    (_revisao(simulated=True), Modo.ON, ClasseDeRisco.B, False, RecusaDoGesto.SIMULADO),
    (_revisao(estado_no_parecer="validated"), Modo.ON, ClasseDeRisco.B, False, RecusaDoGesto.DESATUALIZADO),
    (_revisao(), Modo.ON, ClasseDeRisco.A, False, "so_registro_na_classe_a"),
    (_revisao(), Modo.ON, ClasseDeRisco.C, True, "lote_na_classe_c"),
    (_revisao(), Modo.ON, ClasseDeRisco.C, False, None),
])
def test_o_gesto_sobre_o_parecer(revisao: RevisaoGravada, modo: Modo, classe: ClasseDeRisco, em_lote: bool,
                                 esperado: str | None) -> None:
    assert conferir_gesto(revisao, estado_atual="candidate", modo=modo, classe=classe, em_lote=em_lote) == esperado


def test_a_classe_mais_restritiva_e_a_ia_so_endurece() -> None:
    assert mais_restritiva(ClasseDeRisco.B, ClasseDeRisco.C) is ClasseDeRisco.C
    assert mais_restritiva(ClasseDeRisco.B, None) is ClasseDeRisco.B
    assert mais_restritiva() is ClasseDeRisco.C                       # sem saber: item a item
    endurecida = _revisao(parecer=Parecer(decisao=Decisao.APROVAR, evidencias_citadas=("x",), faixa=ClasseDeRisco.C))
    assert endurecida.classe_efetiva is ClasseDeRisco.C
    afrouxada = _revisao(parecer=Parecer(decisao=Decisao.APROVAR, evidencias_citadas=("x",), faixa=ClasseDeRisco.A))
    assert afrouxada.classe_efetiva is ClasseDeRisco.B


def test_a_saida_gravada_volta_a_ser_o_parecer() -> None:
    p = Parecer(decisao=Decisao.REBAIXAR, evidencias_citadas=("licao:x", "ev:1"), confianca=Confianca.ALTA,
                probabilidade=0.91, faixa=ClasseDeRisco.C, conclusao="falha em dois aparelhos")
    assert parecer_gravado(json.loads(json.dumps(p.como_dados()))) == p
    assert parecer_gravado({"decisao": "publicar"}) is None
    assert parecer_gravado({"decisao": "aprovar", "faixa": "Z"}) is None
    assert parecer_gravado(None) is None and parecer_gravado([]) is None


# ------------------------------------------------------------------ shadow: oculto e às cegas
async def test_em_shadow_o_parecer_fica_oculto_e_a_decisao_as_cegas_vira_rotulo(
        mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    mundo.modo("shadow")
    ref = mundo.licao_b()
    mundo.volta()
    d = (await cliente.get(f"/api/aprendizado/licao/{ref}")).json()
    assert d["pareceres"] == []
    assert d["curador"] == {"modo": "shadow", "pendentes_ocultos": 1, "pode_pedir_revisao": False}
    fila = (await cliente.get("/api/aprendizado/pendentes")).json()
    assert [i["parecer"] for i in fila["itens"] if i["ref"] == ref] == [None]
    r = await cliente.post(f"/api/aprendizado/licao/{ref}/status", json={"to": "validated", "reason": "confere"})
    assert r.status_code == 200, r.text
    linha = mundo.revisao(ref)
    trilha = mundo.repo.trilha(ref)
    assert (linha["decisao_final"], linha["override"], linha["decidido_por"]) == ("validar", 0, ROTA)
    assert linha["transicao_id"] == trilha[-1].id and linha["override_motivo"] is None
    [sinal] = mundo.sinais(SignalKind.PARECER_DECIDIDO)
    assert (sinal["source_ref"], sinal["created_by"], sinal["polarity"]) == (f"parecer:{linha['id']}", ROTA,
                                                                             "neutral")
    assert json.loads(str(sinal["data"]))["viu"] is False
    # Decidido, o parecer aparece: a pessoa vê o que a IA tinha dito.
    [visto] = r.json()["pareceres"]
    assert (visto["decisao_final"], visto["atual"], visto["parecer"]["decisao"]) == ("validar", False, "aprovar")


async def test_as_cegas_contra_o_lado_sugerido_e_override_sem_motivo(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    mundo.modo("shadow")
    mundo.ia.decisao = "observar"
    ref = mundo.licao_b()
    mundo.volta()
    await cliente.post(f"/api/aprendizado/licao/{ref}/status", json={"to": "validated", "reason": "já serve"})
    linha = mundo.revisao(ref)
    assert (linha["decisao_final"], linha["override"], linha["override_motivo"]) == ("validar", 1, None)


async def test_decisao_do_sistema_nao_vira_rotulo(mundo: Mundo) -> None:
    ref = mundo.licao_c()
    mundo.volta()
    mundo.pareceres.mudar_estado(LivroKind.LICAO, ref, S.DISABLED, by=SYSTEM_ACTOR, reason="regra")
    assert mundo.revisao(ref)["decisao_final"] is None


# ------------------------------------------------------------------ on: fila, detalhe e o review_id
async def test_em_on_o_detalhe_e_a_fila_mostram_o_parecer_e_o_passo(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    b = mundo.licao_b()
    c = mundo.licao_c()
    mundo.volta()
    d = (await cliente.get(f"/api/aprendizado/licao/{b}")).json()
    [p] = d["pareceres"]
    assert (p["atual"], p["acao"], p["recusa"], p["classe"], p["simulated"]) == (
        True, {"to": "validated", "rotulo": "validar"}, None, "B", False)
    assert p["parecer"]["decisao"] == "aprovar" and p["parecer"]["confianca"] == "alta"
    assert d["curador"] == {"modo": "on", "pendentes_ocultos": 0, "pode_pedir_revisao": True}
    fila = {i["ref"]: i["parecer"] for i in (await cliente.get("/api/aprendizado/pendentes")).json()["itens"]}
    assert (fila[b]["decisao"], fila[b]["recusa_no_lote"], fila[b]["acao"]["rotulo"]) == ("aprovar", None, "validar")
    assert (fila[c]["classe"], fila[c]["recusa"], fila[c]["recusa_no_lote"]) == ("C", None, "lote_na_classe_c")
    assert fila[c]["acao"] == {"to": "published", "rotulo": "aprovar"}


async def test_status_com_review_id_vira_aceitou_ou_recusou(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    sim, nao = mundo.licao_b("-1"), mundo.licao_b("-2")
    mundo.volta()
    for ref, para, motivo in ((sim, "validated", "ok"), (nao, "disabled", "nota errada")):
        rid = mundo.revisao(ref)["id"]
        r = await cliente.post(f"/api/aprendizado/licao/{ref}/status",
                               json={"to": para, "reason": motivo, "review_id": rid})
        assert r.status_code == 200, r.text
    assert (mundo.revisao(sim)["decisao_final"], mundo.revisao(sim)["override"]) == ("aceitou", 0)
    linha = mundo.revisao(nao)
    assert (linha["decisao_final"], linha["override"], linha["override_motivo"]) == ("recusou", 1, "nota errada")
    assert [json.loads(str(s["data"]))["viu"] for s in mundo.sinais(SignalKind.PARECER_DECIDIDO)] == [True, True]


async def test_em_on_a_decisao_sem_review_id_conta_como_vista(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    """Em `on` o painel mostra o parecer em toda parte: gravar a decisão como cega inflaria a concordância."""
    ref = mundo.licao_b()
    mundo.volta()
    await cliente.post(f"/api/aprendizado/licao/{ref}/status", json={"to": "validated", "reason": "ok"})
    assert (mundo.revisao(ref)["decisao_final"], mundo.revisao(ref)["override"]) == ("aceitou", 0)


async def test_review_id_que_nao_vale_mais_nao_trava_a_pessoa_nem_rotula(mundo: Mundo,
                                                                          cliente: httpx.AsyncClient) -> None:
    ref = mundo.licao_b()
    velho = mundo.gravar_revisao(ref, decisao=Decisao.APROVAR, classe=ClasseDeRisco.B, estado="validated")
    r = await cliente.post(f"/api/aprendizado/licao/{ref}/status",
                           json={"to": "validated", "reason": "ok", "review_id": velho})
    assert r.status_code == 200, r.text
    assert mundo.estado(ref) is S.VALIDATED
    assert mundo.db.one("SELECT decisao_final FROM learning_reviews WHERE id=?", (velho,))["decisao_final"] is None


# ------------------------------------------------------------------ o gesto: aceitar e recusar
async def test_aceitar_transiciona_e_grava_o_aceite_uma_vez(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    ref = mundo.licao_b()
    mundo.volta()
    rid = mundo.revisao(ref)["id"]
    url = f"/api/aprendizado/licao/{ref}/parecer/{rid}"
    r = await cliente.post(url, json={"resposta": "aceitar", "motivo": "a IA viu bem", "em_lote": True})
    assert r.status_code == 200, r.text
    assert mundo.estado(ref) is S.VALIDATED
    linha = mundo.revisao(ref)
    ultima = mundo.repo.trilha(ref)[-1]
    assert (linha["decisao_final"], linha["override"], linha["transicao_id"]) == ("aceitou", 0, ultima.id)
    assert (ultima.decided_by, ultima.reason) == (ROTA, "a IA viu bem")
    trilha = len(mundo.repo.trilha(ref))
    de_novo = await cliente.post(url, json={"resposta": "aceitar", "motivo": "de novo"})
    assert de_novo.status_code == 409 and de_novo.json()["detail"]["code"] == "parecer_ja_decidido"
    assert len(mundo.repo.trilha(ref)) == trilha and mundo.estado(ref) is S.VALIDATED


async def test_aceitar_esperar_e_concordar_sem_transicao(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    mundo.ia.decisao = "observar"
    ref = mundo.licao_b()
    mundo.volta()
    trilha = len(mundo.repo.trilha(ref))
    rid = mundo.revisao(ref)["id"]
    r = await cliente.post(f"/api/aprendizado/licao/{ref}/parecer/{rid}",
                           json={"resposta": "aceitar", "motivo": "espero mais execuções"})
    assert r.status_code == 200, r.text
    assert mundo.estado(ref) is S.CANDIDATE and len(mundo.repo.trilha(ref)) == trilha
    assert (mundo.revisao(ref)["decisao_final"], mundo.revisao(ref)["transicao_id"]) == ("aceitou", None)


async def test_c_nunca_em_lote_e_a_e_so_registro(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    c = mundo.licao_c()
    mundo.volta()
    rid = mundo.revisao(c)["id"]
    url = f"/api/aprendizado/licao/{c}/parecer/{rid}"
    r = await cliente.post(url, json={"resposta": "aceitar", "motivo": "lote", "em_lote": True})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "lote_na_classe_c"
    assert mundo.estado(c) is S.VALIDATED and mundo.revisao(c)["decisao_final"] is None
    r = await cliente.post(url, json={"resposta": "aceitar", "motivo": "conferi este"})
    assert r.status_code == 200, r.text
    assert mundo.estado(c) is S.PUBLISHED
    a = mundo.licao_a()
    rid_a = mundo.gravar_revisao(a, decisao=Decisao.APROVAR, classe=ClasseDeRisco.A, estado="candidate")
    r = await cliente.post(f"/api/aprendizado/licao/{a}/parecer/{rid_a}", json={"resposta": "aceitar", "motivo": "x"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "so_registro_na_classe_a"
    assert mundo.estado(a) is S.CANDIDATE


async def test_a_classe_de_agora_endurece_o_aceite(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    ref = mundo.licao_b()
    mundo.volta()
    assert mundo.revisao(ref)["classe_de_risco"] == "B"
    # O catálogo mudou depois do parecer: a capability da lição agora é de alto risco.
    mundo.catalogo.fatos["OPEN_POST"] = FatosDoCatalogo(risco="high", efeito_externo=True)
    rid = mundo.revisao(ref)["id"]
    r = await cliente.post(f"/api/aprendizado/licao/{ref}/parecer/{rid}",
                           json={"resposta": "aceitar", "motivo": "lote", "em_lote": True})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "lote_na_classe_c"
    assert mundo.estado(ref) is S.CANDIDATE


async def test_parecer_simulado_aparece_e_nao_se_decide(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    mundo.ia.simulado = True
    ref = mundo.licao_b()
    mundo.volta()
    [p] = (await cliente.get(f"/api/aprendizado/licao/{ref}")).json()["pareceres"]
    assert (p["simulated"], p["recusa"]) == (True, "parecer_simulado")
    r = await cliente.post(f"/api/aprendizado/licao/{ref}/parecer/{p['id']}",
                           json={"resposta": "aceitar", "motivo": "x"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "parecer_simulado"
    assert mundo.estado(ref) is S.CANDIDATE


async def test_recusar_grava_o_override_com_motivo_e_recusa_motivo_com_cara_de_segredo(
        mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    ref = mundo.licao_b()
    mundo.volta()
    rid = mundo.revisao(ref)["id"]
    url = f"/api/aprendizado/licao/{ref}/parecer/{rid}"
    r = await cliente.post(url, json={"resposta": "recusar", "motivo": "a senha da conta e abc12345"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "note_looks_secret"
    assert mundo.revisao(ref)["decisao_final"] is None
    r = await cliente.post(url, json={"resposta": "recusar", "motivo": "a nota fala de outra tela"})
    assert r.status_code == 200, r.text
    linha = mundo.revisao(ref)
    assert (linha["decisao_final"], linha["override"], linha["override_motivo"]) == (
        "recusou", 1, "a nota fala de outra tela")
    assert mundo.estado(ref) is S.CANDIDATE                     # recusar o parecer não decide o item


async def test_transicao_recusada_desfaz_o_aceite(mundo: Mundo, cliente: httpx.AsyncClient,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    ref = mundo.licao_b()
    mundo.volta()
    rid = mundo.revisao(ref)["id"]

    def recusa(*a: object, **kw: object) -> None:
        raise ConflitoDeEstado("outra pessoa decidiu antes")

    monkeypatch.setattr(mundo.servico, "mudar_estado", recusa)
    r = await cliente.post(f"/api/aprendizado/licao/{ref}/parecer/{rid}", json={"resposta": "aceitar", "motivo": "x"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "state_conflict"
    assert mundo.revisao(ref)["decisao_final"] is None and mundo.sinais(SignalKind.PARECER_DECIDIDO) == []


async def test_review_id_de_outro_item_e_404(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    um, outro = mundo.licao_b("-1"), mundo.licao_b("-2")
    mundo.volta()
    rid = mundo.revisao(outro)["id"]
    r = await cliente.post(f"/api/aprendizado/licao/{um}/parecer/{rid}", json={"resposta": "aceitar", "motivo": "x"})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "review_not_found"


# ------------------------------------------------------------------ a triagem do dossiê da receita
def test_a_variante_da_receita_e_estrutural_e_nao_vai_a_triagem() -> None:
    """Achado do ensaio do 30.17 (03/10): a variante da receita (idioma e densidade) tem cara de credencial para a
    regra do central, e 24 de 26 receitas da cópia ficavam `recusada:triagem`: o curador nunca revisava receita."""
    assert TriagemDeCredencial().recusa("en-US/xhdpi")             # a regra recusa mesmo: o conserto é a lista
    assert _textos_livres({"identidade": {"variante": "en-US/xhdpi", "nome": "Enviar"}}) == ["Enviar"]


# ------------------------------------------------------------------ pedir revisão
async def test_pedir_revisao_so_em_on(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    ref = mundo.licao_b()
    for modo in ("off", "shadow"):
        mundo.modo(modo)
        r = await cliente.post(f"/api/aprendizado/licao/{ref}/revisao")
        assert r.status_code == 409 and r.json()["detail"]["code"] == "curador_fora_do_on"
    assert mundo.sinais(SignalKind.PEDIU_REVISAO) == []


async def test_o_pedido_pula_so_o_cooldown_e_o_mesmo_dossie_nao_se_repete(mundo: Mundo,
                                                                         cliente: httpx.AsyncClient) -> None:
    mundo.modo("on", cooldown_h=24)
    ref = mundo.licao_b()
    mundo.volta()
    assert len(mundo.ia.pedidos) == 1
    # O mesmo estado do item já tem revisão: nada a pedir, e a resposta diz qual é.
    r = await cliente.post(f"/api/aprendizado/licao/{ref}/revisao")
    assert r.status_code == 200 and r.json()["pedido"] is False
    assert r.json()["revisao"]["id"] == mundo.revisao(ref)["id"]
    # Evidência nova muda o dossiê; o cooldown de 24 h seguraria o item sem o pedido.
    mundo.repo.registrar_evidencia(NovaEvidencia(item_ref=ref, stance=Posicao.FOR, origin_ref="run:r1",
                                                 simulated=True, run_id="r1"))
    mundo.volta()
    assert len(mundo.ia.pedidos) == 1
    r = await cliente.post(f"/api/aprendizado/licao/{ref}/revisao")
    assert r.status_code == 202 and r.json() == {"pedido": True, "revisao": None}
    [sinal] = mundo.sinais(SignalKind.PEDIU_REVISAO)
    assert (sinal["created_by"], json.loads(str(sinal["data"]))["item_ref"]) == (ROTA, ref)
    mundo.volta()
    assert len(mundo.ia.pedidos) == 2
    assert mundo.revisao(ref)["gatilho"] == Gatilho.PEDIDO_DA_PESSOA.value
    mundo.volta()                                               # revisado depois do pedido: o pedido se esgotou
    assert len(mundo.ia.pedidos) == 2
