"""A política de risco do aprendizado (30.10, `docs/design/aprendizado-vivo.md` §8.4, aprovada pelo dono em 02/10).

- as três classes, cada razão que as faz, e a regra "vale a mais restritiva" entre catálogo e `commit` (a anomalia da
  receita do Outlook com `commit` num catálogo só de leitura cai em C);
- a classe A nunca gasta IA e é a única que decide sozinha; o aceite de parecer é sempre da pessoa e, na C, item a item;
- `classificar_espera` (30.21) é tradução fiel da política (fonte única), em toda combinação de entradas;
- os catálogos REAIS do repositório (YAML) lidos pelo adaptador da 30.21 classificam como esperado.

Nível de prova: `simulated` (domínio puro e o YAML do repositório; sem banco, aparelho ou IA).
"""
from __future__ import annotations

import itertools

import pytest

from app.modules.learning.domain import espera
from app.modules.learning.domain.espera import Faixa, classificar_espera
from app.modules.learning.domain.falhas import FailureKind
from app.modules.learning.domain.politica_de_risco import (FALHAS_DE_SESSAO, ORIGENS_DE_SESSAO, ClasseDeRisco,
                                                           FatosDeRisco, FatosDoCatalogo, MotivoDeEntrada,
                                                           PoliticaDaClasse, Razao, RecusaDoAceite, classificar,
                                                           conferir_aceite, toca_sessao_ou_autenticacao)
from app.modules.learning.domain.vocabulario import SourceKind
from app.modules.learning.infrastructure.eventos import RiscoDoRegistro

OUTLOOK = "com.microsoft.office.outlook"
INSTAGRAM = "com.instagram.android"


def _classe(**kw: object) -> ClasseDeRisco:
    base: dict[str, object] = {"side_effect": False, "human_origin": False, "tem_catalogo": True}
    base.update(kw)
    return classificar(FatosDeRisco(**base)).classe  # type: ignore[arg-type]


# ------------------------------------------------------------------ as classes
def test_classe_a_navegacao_e_leitura_nunca_gasta_ia_e_decide_pela_regra() -> None:
    c = classificar(FatosDeRisco(side_effect=False, human_origin=False, tem_catalogo=True,
                                 catalogo=FatosDoCatalogo()))
    assert c.classe is ClasseDeRisco.A and c.razoes == () and c.motivo is None
    assert not c.gasta_ia and c.decide_sozinho and not c.aceita_lote
    assert c.politica is PoliticaDaClasse.REGRA_DETERMINISTICA
    assert _classe(tem_catalogo=False) is ClasseDeRisco.A          # leitura em app sem catálogo também é A


@pytest.mark.parametrize(("fatos", "razao"), [
    ({"catalogo": FatosDoCatalogo(risco="medium")}, Razao.RISCO_MEDIO),
    ({"catalogo": FatosDoCatalogo(efeito_externo=True)}, Razao.EFEITO_DECLARADO),
    ({"side_effect": True, "tem_catalogo": False}, Razao.COMMIT_SEM_CATALOGO),
    ({"side_effect": True}, Razao.COMMIT_SEM_FATOS_DA_ETAPA),      # catálogo existe, capability não derivável
    ({"human_origin": True}, Razao.TEXTO_DE_PESSOA),               # D-2: origem humana sem efeito
])
def test_classe_b_recomendacao_e_lote(fatos: dict[str, object], razao: Razao) -> None:
    base: dict[str, object] = {"side_effect": False, "human_origin": False, "tem_catalogo": True}
    c = classificar(FatosDeRisco(**{**base, **fatos}))  # type: ignore[arg-type]
    assert c.classe is ClasseDeRisco.B and razao in c.razoes
    assert c.gasta_ia and not c.decide_sozinho and c.aceita_lote
    assert c.politica is PoliticaDaClasse.DONO_EM_LOTE


@pytest.mark.parametrize(("fatos", "razao", "motivo"), [
    ({"catalogo": FatosDoCatalogo(risco="high")}, Razao.RISCO_ALTO, MotivoDeEntrada.ALTO_RISCO),
    ({"catalogo": FatosDoCatalogo(politica="manual_only")}, Razao.POLITICA_MANUAL, MotivoDeEntrada.ALTO_RISCO),
    ({"catalogo": FatosDoCatalogo(politica="disabled")}, Razao.POLITICA_MANUAL, MotivoDeEntrada.ALTO_RISCO),
    ({"catalogo": FatosDoCatalogo(precisa_rascunho=True)}, Razao.TEXTO_PARA_OUTRA_PESSOA, MotivoDeEntrada.ALTO_RISCO),
    ({"catalogo": FatosDoCatalogo(efeito_externo=True, familia_do_efeito="envio")}, Razao.FAMILIA_DE_ALTO_RISCO,
     MotivoDeEntrada.ALTO_RISCO),
    ({"catalogo": FatosDoCatalogo(efeito_externo=True, familia_do_efeito="publicacao")}, Razao.FAMILIA_DE_ALTO_RISCO,
     MotivoDeEntrada.ALTO_RISCO),
    ({"catalogo": FatosDoCatalogo(efeito_externo=True, familia_do_efeito="exclusao")}, Razao.FAMILIA_DE_ALTO_RISCO,
     MotivoDeEntrada.ALTO_RISCO),
    ({"sessao_ou_autenticacao": True}, Razao.SESSAO_OU_AUTENTICACAO, MotivoDeEntrada.SESSAO_OU_AUTENTICACAO),
])
def test_classe_c_sempre_o_dono_item_a_item(fatos: dict[str, object], razao: Razao,
                                            motivo: MotivoDeEntrada) -> None:
    base: dict[str, object] = {"side_effect": False, "human_origin": False, "tem_catalogo": True}
    c = classificar(FatosDeRisco(**{**base, **fatos}))  # type: ignore[arg-type]
    assert c.classe is ClasseDeRisco.C and razao in c.razoes and c.motivo is motivo
    assert not c.decide_sozinho and not c.aceita_lote
    assert c.politica is PoliticaDaClasse.DONO_ITEM_A_ITEM


def test_familia_fora_das_tres_nao_faz_c() -> None:
    assert _classe(catalogo=FatosDoCatalogo(efeito_externo=True, familia_do_efeito="curtida")) is ClasseDeRisco.B


# ------------------------------------------------------------------ a mais restritiva entre catálogo e commit
def test_commit_que_o_catalogo_nao_declara_e_c_a_anomalia_do_outlook() -> None:
    """A receita 100 do Outlook: `commit` numa capability que o catálogo (só de leitura) diz não ter efeito."""
    c = classificar(FatosDeRisco(side_effect=True, human_origin=False, tem_catalogo=True,
                                 catalogo=FatosDoCatalogo()))
    assert c.classe is ClasseDeRisco.C
    assert c.razoes == (Razao.COMMIT_FORA_DO_CATALOGO,)
    assert c.motivo is MotivoDeEntrada.EFEITO_EXTERNO


def test_catalogo_mais_restritivo_que_o_conteudo_vence() -> None:
    # O conteúdo não tem commit (seria A), mas a capability é de alto risco: vale a do catálogo.
    assert _classe(side_effect=False, catalogo=FatosDoCatalogo(risco="high", efeito_externo=True)) is ClasseDeRisco.C
    assert _classe(side_effect=False, catalogo=FatosDoCatalogo(risco="medium", efeito_externo=True)) is ClasseDeRisco.B


def test_conteudo_mais_restritivo_que_o_catalogo_vence() -> None:
    # Catálogo de risco médio com efeito declarado + commit: B; o mesmo commit numa etapa que o catálogo diz não ter
    # efeito: C (a divergência).
    assert _classe(side_effect=True, catalogo=FatosDoCatalogo(risco="medium", efeito_externo=True)) is ClasseDeRisco.B
    assert _classe(side_effect=True, catalogo=FatosDoCatalogo(risco="medium")) is ClasseDeRisco.C


def test_varias_razoes_em_ordem_e_motivo_da_mais_restritiva() -> None:
    c = classificar(FatosDeRisco(side_effect=True, human_origin=True, tem_catalogo=True, sessao_ou_autenticacao=True,
                                 catalogo=FatosDoCatalogo(risco="high", politica="manual_only", efeito_externo=True)))
    assert c.classe is ClasseDeRisco.C and c.motivo is MotivoDeEntrada.SESSAO_OU_AUTENTICACAO
    assert c.razoes == (Razao.SESSAO_OU_AUTENTICACAO, Razao.RISCO_ALTO, Razao.POLITICA_MANUAL,
                        Razao.EFEITO_DECLARADO, Razao.TEXTO_DE_PESSOA)
    # origem humana com efeito de risco médio: B, e o motivo é o efeito (não o texto de pessoa)
    b = classificar(FatosDeRisco(side_effect=True, human_origin=True, tem_catalogo=True,
                                 catalogo=FatosDoCatalogo(risco="medium", efeito_externo=True)))
    assert b.classe is ClasseDeRisco.B and b.motivo is MotivoDeEntrada.EFEITO_EXTERNO


def test_como_dados_cabe_em_learning_reviews() -> None:
    c = classificar(FatosDeRisco(side_effect=True, human_origin=False, tem_catalogo=False))
    assert c.como_dados() == {"classe": "B", "politica": "dono_em_lote", "motivo": "commit_sem_catalogo",
                              "razoes": ["commit_sem_catalogo"]}


# ------------------------------------------------------------------ a espera (30.21) é tradução da política
def test_classificar_espera_e_traducao_fiel_da_politica() -> None:
    catalogos = [None, FatosDoCatalogo(), FatosDoCatalogo(risco="medium"), FatosDoCatalogo(risco="high"),
                 FatosDoCatalogo(efeito_externo=True), FatosDoCatalogo(politica="manual_only"),
                 FatosDoCatalogo(precisa_rascunho=True),
                 FatosDoCatalogo(efeito_externo=True, familia_do_efeito="envio")]
    vistos = 0
    for se, ho, tem, sessao, cat in itertools.product((False, True), (False, True), (False, True), (False, True),
                                                      catalogos):
        politica = classificar(FatosDeRisco(side_effect=se, human_origin=ho, tem_catalogo=tem, catalogo=cat,
                                            sessao_ou_autenticacao=sessao))
        faixa = classificar_espera(side_effect=se, human_origin=ho, tem_catalogo=tem, catalogo=cat,
                                   sessao_ou_autenticacao=sessao)
        if politica.classe is ClasseDeRisco.A:
            assert faixa is None
        else:
            assert faixa == (Faixa(politica.classe.value), politica.motivo)
        vistos += 1
    assert vistos == 2 * 2 * 2 * 2 * len(catalogos)


def test_espera_reexporta_a_fonte_unica() -> None:
    assert espera.FatosDoCatalogo is FatosDoCatalogo and espera.MotivoDeEntrada is MotivoDeEntrada
    assert "A" not in {f.value for f in Faixa}                     # o evento nunca diz faixa A


# ------------------------------------------------------------------ a IA nunca decide
def test_classe_c_decisao_automatica_impossivel() -> None:
    assert conferir_aceite(ClasseDeRisco.C, por_pessoa=False, em_lote=False) is RecusaDoAceite.DECISAO_AUTOMATICA_EM_C
    assert conferir_aceite(ClasseDeRisco.C, por_pessoa=False, em_lote=True) is RecusaDoAceite.DECISAO_AUTOMATICA_EM_C
    assert conferir_aceite(ClasseDeRisco.C, por_pessoa=True, em_lote=True) is RecusaDoAceite.LOTE_NA_CLASSE_C
    assert conferir_aceite(ClasseDeRisco.C, por_pessoa=True, em_lote=False) is None


def test_classe_b_lote_da_pessoa_sim_sistema_nao() -> None:
    assert conferir_aceite(ClasseDeRisco.B, por_pessoa=True, em_lote=True) is None
    assert conferir_aceite(ClasseDeRisco.B, por_pessoa=False, em_lote=False) is RecusaDoAceite.DECISAO_AUTOMATICA


def test_classe_a_nao_tem_parecer_a_aceitar() -> None:
    for pessoa, lote in itertools.product((False, True), (False, True)):
        assert conferir_aceite(ClasseDeRisco.A, por_pessoa=pessoa, em_lote=lote) is RecusaDoAceite.PARECER_NA_CLASSE_A


# ------------------------------------------------------------------ sessão e autenticação
def test_sessao_ou_autenticacao_pelas_origens_falhas_e_tela() -> None:
    assert ORIGENS_DE_SESSAO == {SourceKind.SESSION_UNKNOWN.value}
    assert FALHAS_DE_SESSAO == {FailureKind.AUTENTICACAO.value, FailureKind.CONTA_ERRADA.value}
    assert toca_sessao_ou_autenticacao(source_kind="session_unknown")
    assert toca_sessao_ou_autenticacao(falhas=frozenset({"conta_errada", "alvo_ausente"}))
    assert toca_sessao_ou_autenticacao(tela_autenticada=True)
    assert not toca_sessao_ou_autenticacao(source_kind="recovery", falhas=frozenset({"alvo_ausente"}))


# ------------------------------------------------------------------ os catálogos reais do repositório
def test_catalogo_real_do_outlook_so_leitura_com_commit_e_c() -> None:
    risco = RiscoDoRegistro()
    assert risco.tem_catalogo(OUTLOOK)
    fatos = risco.da_capability(OUTLOOK, "OPEN_MAIL_INBOX")
    assert fatos is not None and not fatos.efeito_externo
    assert classificar(FatosDeRisco(side_effect=False, human_origin=False, tem_catalogo=True,
                                    catalogo=fatos)).classe is ClasseDeRisco.A      # ler a caixa: A
    assert classificar(FatosDeRisco(side_effect=True, human_origin=False, tem_catalogo=True,
                                    catalogo=fatos)).classe is ClasseDeRisco.C      # commit nela: C


@pytest.mark.parametrize(("capability", "classe"), [
    ("OPEN_FEED", ClasseDeRisco.A), ("LIKE_POST", ClasseDeRisco.B), ("SEND_MESSAGE", ClasseDeRisco.C),
    ("CREATE_COMMENT", ClasseDeRisco.C), ("UNFOLLOW", ClasseDeRisco.C), ("LOGOUT", ClasseDeRisco.C),
])
def test_catalogo_real_do_instagram(capability: str, classe: ClasseDeRisco) -> None:
    fatos = RiscoDoRegistro().da_capability(INSTAGRAM, capability)
    assert fatos is not None
    assert classificar(FatosDeRisco(side_effect=fatos.efeito_externo, human_origin=False, tem_catalogo=True,
                                    catalogo=fatos)).classe is classe
