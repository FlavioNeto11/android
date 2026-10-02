"""Modo por app para lições e telas aprendidas (item 30.20, §8.10 do desenho do aprendizado vivo).

`aprendizado.licoes.por_app` e `aprendizado.telas.por_app` sobrescrevem o modo global de UM pacote; sem override o global
vale. A regra é uma só (`domain/modo_por_app.modo_efetivo`) e o pacote vem só do config (ADR-052). O que se prova:

- o config: padrão vazio, valor fora do vocabulário e chave que não é pacote são recusados;
- a função pura: override, global, sem pacote;
- LIÇÕES: o consumo (`licoes_para`) entrega só do pacote em `on`, conta sombra no `shadow` e nada no `off`; a coleta e
  a validação seguem o modo do pacote; o D1 não publica no pacote em `shadow` mesmo com o global em `on`;
- TELAS: a observação, o minerador, a publicação sozinha (D1) e o fornecedor da sessão seguem o modo do pacote.

Limite conhecido, provado como `xfail(strict)`: o D1 de um pacote MAIS permissivo que o global (global `shadow` ou
`observe`, pacote `on`) ainda é recusado porque `LearningService._modo_publica` (`servico.py`) lê só o modo global.
Quando ele receber o app do item, os dois testes passam e o `strict` obriga a tirar a marca.

Nível de prova: `simulated` (banco de teste, aparelhos falsos, nenhuma IA).
"""
from __future__ import annotations

import weakref

import pytest
from pydantic import ValidationError

from app.config import LicoesCfg, TelasAprendidasCfg
from app.integrations.app_declarado import conhecimento
from app.modules.learning.application.licoes import AjustesDeLicoes
from app.modules.learning.domain.ciclo import SkillState
from app.modules.learning.domain.licoes import Pedido
from app.modules.learning.domain.modo_por_app import modo_efetivo
from app.modules.learning.domain.vocabulario import Modo, ModoDeTelas, Papel
from app.modules.learning.infrastructure import ligar_licoes, ligar_telas

from .test_learning_licoes import IG, _publicar, contraste_ciclo, db  # noqa: F401
from .test_learning_licoes import Mundo as MundoDeLicoes
from .test_learning_telas import CORREIO, PASTA_NOVA, _aprender, mundo  # noqa: F401
from .test_learning_telas import Mundo as MundoDeTelas

OUTRO = "com.exemplo.outro"
XFAIL_D1 = ("30.20: o D1 por app precisa de `servico.py` `_modo_publica(kind, app)`; hoje lê só o modo global "
            "(servico.py:173)")


# ==================================================================== config e função pura
def test_config_padrao_vazio_e_valida_modo_e_pacote() -> None:
    assert LicoesCfg().por_app == {} and TelasAprendidasCfg().por_app == {}
    assert LicoesCfg(por_app={IG: "on"}).por_app == {IG: "on"}
    assert TelasAprendidasCfg(por_app={CORREIO: "observe"}).por_app == {CORREIO: "observe"}
    with pytest.raises(ValidationError):
        LicoesCfg(por_app={IG: "observe"})                    # observe é vocabulário das telas
    with pytest.raises(ValidationError):
        TelasAprendidasCfg(por_app={CORREIO: "shadow"})       # shadow é vocabulário das lições
    for lixo in ("instagram", "", "com..x", "com.exemplo/app", "1com.x"):
        with pytest.raises(ValidationError):
            LicoesCfg(por_app={lixo: "on"})
        with pytest.raises(ValidationError):
            TelasAprendidasCfg(por_app={lixo: "on"})


def test_modo_efetivo_e_o_override_ou_o_global_e_sem_pacote_e_o_global() -> None:
    assert modo_efetivo(Modo.SHADOW, {}, IG) is Modo.SHADOW
    assert modo_efetivo(Modo.SHADOW, {IG: Modo.ON}, IG) is Modo.ON
    assert modo_efetivo(Modo.SHADOW, {IG: Modo.ON}, OUTRO) is Modo.SHADOW
    assert modo_efetivo(Modo.ON, {IG: Modo.OFF}, None) is Modo.ON
    assert modo_efetivo(Modo.ON, {IG: Modo.OFF}, "") is Modo.ON
    assert modo_efetivo(ModoDeTelas.OBSERVE, {CORREIO: ModoDeTelas.ON}, CORREIO) is ModoDeTelas.ON


def test_o_ligar_traduz_o_config_para_os_ajustes() -> None:
    cfg = LicoesCfg(modo="shadow", por_app={IG: "on"})
    assert ligar_licoes.ajustes_de_licoes(cfg).por_app == {IG: Modo.ON}
    assert ligar_telas.ajustes_de_telas(TelasAprendidasCfg(por_app={CORREIO: "off"})).por_app == {
        CORREIO: ModoDeTelas.OFF}
    assert AjustesDeLicoes().por_app == {}


# ==================================================================== lições
def _pedido(app: str) -> Pedido:
    return Pedido(papel=Papel.ACTOR, unidade="u1", run_id="r9", app=app, capability="OPEN_POST", step_hash="h-abrir",
                  simulated=True)


def test_licoes_sem_override_e_o_global(db) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    m = MundoDeLicoes(db, modo="shadow")
    assert m.licoes.modo_efetivo(IG) is Modo.SHADOW and m.licoes.modo_efetivo(None) is Modo.SHADOW
    m.cfg.licoes.por_app = {OUTRO: "on"}                                  # outro pacote não muda o IG
    assert m.licoes.modo_efetivo(IG) is Modo.SHADOW and m.licoes.modo_efetivo(OUTRO) is Modo.ON


def test_o_enabled_falso_vence_o_override(db) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    m = MundoDeLicoes(db, modo="on")
    m.cfg.licoes.por_app = {IG: "on"}
    m.cfg.enabled = False
    assert m.licoes.modo_efetivo(IG) is Modo.OFF
    assert m.licoes.licoes_para(_pedido(IG)) == []


def test_o_consumo_entrega_so_do_pacote_em_on_e_conta_sombra_no_shadow(db) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    m = MundoDeLicoes(db, modo="shadow")                                  # o global NÃO entrega nada
    m.cfg.licoes.holdout_publicada = 0                                    # sem braço de controle: determinístico
    _publicar(m, "Toque no botão de comentar do post.", capability="OPEN_POST", detalhe="medida:ajuda")
    assert m.licoes.licoes_para(_pedido(IG)) == []
    assert [n for n, _, _ in m.metricas if n == "licao.sombra"] == ["licao.sombra"]   # o que iria, sem ir
    # O pacote do override em `on` entrega (o consumo é o caminho que o modo por app liga primeiro).
    m.cfg.licoes.por_app = {IG: "on"}
    assert m.licoes.licoes_para(_pedido(IG)) == ["Toque no botão de comentar do post."]
    assert [r["app_package"] for r in db.query("SELECT app_package FROM learning_exposures")] == [IG]
    # Outro pacote, sem lição publicada nem override, segue o global: nada.
    assert m.licoes.licoes_para(_pedido(OUTRO)) == []


def test_o_consumo_em_shadow_ou_off_no_pacote_mesmo_com_o_global_on(db) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    m = MundoDeLicoes(db, modo="on")
    m.cfg.licoes.holdout_publicada = 0                                    # sem braço de controle: determinístico
    _publicar(m, "Toque no botão de comentar do post.", capability="OPEN_POST", detalhe="medida:ajuda")
    assert m.licoes.licoes_para(_pedido(IG)) == ["Toque no botão de comentar do post."]
    m.cfg.licoes.por_app = {IG: "shadow"}
    antes = len(m.metricas)
    assert m.licoes.licoes_para(_pedido(IG)) == []
    assert [n for n, _, _ in m.metricas[antes:]] == ["licao.sombra"]
    m.cfg.licoes.por_app = {IG: "off"}
    antes = len(m.metricas)
    assert m.licoes.licoes_para(_pedido(IG)) == []
    assert len(m.metricas) == antes                                       # `off` nem conta a sombra


def test_a_coleta_e_o_d1_seguem_o_modo_do_pacote(db) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    m = MundoDeLicoes(db, modo="on")
    m.cfg.licoes.por_app = {IG: "shadow"}                                 # o global publicaria; o pacote não
    contraste_ciclo(db, "r1")
    contraste_ciclo(db, "r2", instancia="android-07")
    for r in ("r1", "r2"):
        m.licoes.minerar_contrastes(r)
    [licao] = m.licoes_do_livro()
    assert licao.state is SkillState.VALIDATED                            # valida, não publica
    m.cfg.licoes.por_app = {}                                             # sem override: o global (on) publica
    m.servico.curar()
    atual = m.repo.item(licao.id)
    assert atual is not None and atual.state is SkillState.PUBLISHED


def test_o_pacote_em_off_nao_coleta_mesmo_com_o_global_on(db) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    m = MundoDeLicoes(db, modo="on")
    m.cfg.licoes.por_app = {IG: "off"}
    contraste_ciclo(db, "r1")
    assert m.licoes.minerar_contrastes("r1") == 0
    assert m.licoes_do_livro() == []


def test_o_global_off_com_um_pacote_ligado_coleta_so_nele(db) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    m = MundoDeLicoes(db, modo="off")
    contraste_ciclo(db, "r1")
    assert m.licoes.minerar_contrastes("r1") == 0                         # global off, sem override: nada
    m.cfg.licoes.por_app = {IG: "shadow"}
    assert m.licoes.minerar_contrastes("r1") > 0
    [licao] = m.licoes_do_livro()
    assert licao.escopo.app == IG and licao.state is SkillState.CANDIDATE


@pytest.mark.xfail(strict=True, reason=XFAIL_D1)
def test_d1_do_pacote_em_on_com_o_global_em_shadow_publica(db) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    m = MundoDeLicoes(db, modo="shadow")
    m.cfg.licoes.por_app = {IG: "on"}
    contraste_ciclo(db, "r1")
    contraste_ciclo(db, "r2", instancia="android-07")
    for r in ("r1", "r2"):
        m.licoes.minerar_contrastes(r)
    [licao] = m.licoes_do_livro()
    assert licao.state is SkillState.PUBLISHED


# ==================================================================== telas
def test_telas_sem_override_e_o_global(mundo: MundoDeTelas) -> None:  # noqa: F811
    mundo.cfg.modo = "observe"
    assert mundo.telas.modo_efetivo(CORREIO) is ModoDeTelas.OBSERVE
    assert mundo.telas.modo_efetivo(None) is ModoDeTelas.OBSERVE
    mundo.cfg.por_app = {OUTRO: "on"}
    assert mundo.telas.modo_efetivo(CORREIO) is ModoDeTelas.OBSERVE
    assert mundo.telas.modo_efetivo(OUTRO) is ModoDeTelas.ON


def test_o_pacote_em_observe_valida_mas_nao_publica_com_o_global_on(mundo: MundoDeTelas) -> None:  # noqa: F811
    mundo.cfg.modo = "on"
    mundo.cfg.por_app = {CORREIO: "observe"}
    _aprender(mundo, PASTA_NOVA)
    (item,) = mundo.itens()
    assert item.state is SkillState.VALIDATED                            # a observação e a validação seguem
    assert [t.to_state for t in mundo.repo.trilha(item.id)] == [SkillState.CANDIDATE, SkillState.VALIDATED]
    conhecimento.invalidar_regras_aprendidas()
    assert conhecimento.regras_aprendidas(CORREIO) == ()
    # Sem o override, o global (on) publica o que ficou validado.
    mundo.cfg.por_app = {}
    assert mundo.livro.curar().falhas == ()
    assert mundo.repo.item(item.id).state is SkillState.PUBLISHED        # type: ignore[union-attr]
    assert len(conhecimento.regras_aprendidas(CORREIO)) == 1


def test_o_fornecedor_da_sessao_so_entrega_do_pacote_em_on(mundo: MundoDeTelas) -> None:  # noqa: F811
    mundo.cfg.modo = "on"
    _aprender(mundo, PASTA_NOVA)
    assert mundo.itens()[0].state is SkillState.PUBLISHED
    fornecedor = ligar_telas.FornecedorDoLivro(weakref.ref(mundo.telas))
    assert len(fornecedor(CORREIO)) == 1
    mundo.cfg.por_app = {CORREIO: "observe"}                              # o pacote deixa de ser consumido
    assert fornecedor(CORREIO) == ()
    mundo.cfg.por_app = {CORREIO: "off"}
    assert fornecedor(CORREIO) == ()
    mundo.cfg.por_app = {OUTRO: "observe"}                                # outro pacote não afeta o correio
    assert len(fornecedor(CORREIO)) == 1


def test_o_pacote_em_off_nao_observa_nada_com_o_global_on(mundo: MundoDeTelas) -> None:  # noqa: F811
    mundo.cfg.modo = "on"
    mundo.cfg.por_app = {CORREIO: "off"}
    _aprender(mundo, PASTA_NOVA)
    assert mundo.sinais() == [] and mundo.itens() == []


def test_o_global_off_com_um_pacote_em_observe_observa_e_valida_so_nele(mundo: MundoDeTelas) -> None:  # noqa: F811
    mundo.cfg.modo = "off"
    _aprender(mundo, PASTA_NOVA)
    assert mundo.sinais() == [] and mundo.itens() == []                   # global off, sem override: nada
    mundo.cfg.por_app = {CORREIO: "observe"}
    _aprender(mundo, PASTA_NOVA)
    (item,) = mundo.itens()
    assert item.state is SkillState.VALIDATED and item.escopo.app == CORREIO


def test_o_outro_pacote_em_on_nao_publica_o_do_global_observe(mundo: MundoDeTelas) -> None:  # noqa: F811
    mundo.cfg.modo = "observe"
    mundo.cfg.por_app = {OUTRO: "on"}
    _aprender(mundo, PASTA_NOVA)
    (item,) = mundo.itens()
    assert item.state is SkillState.VALIDATED and item.escopo.app == CORREIO


@pytest.mark.xfail(strict=True, reason=XFAIL_D1)
def test_d1_do_pacote_em_on_com_o_global_em_observe_publica(mundo: MundoDeTelas) -> None:  # noqa: F811
    mundo.cfg.modo = "observe"
    mundo.cfg.por_app = {CORREIO: "on"}
    _aprender(mundo, PASTA_NOVA)
    (item,) = mundo.itens()
    assert item.state is SkillState.PUBLISHED
