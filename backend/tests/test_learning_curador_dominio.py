"""O curador por IA, domínio (30.10, `docs/design/aprendizado-vivo.md` §8.2-8.3).

- o dossiê: só fatos, por lista branca (nenhum valor de parâmetro, texto digitado, texto de tela ou de pessoa; item de
  sessão sem conteúdo), ids citáveis, evidências cortadas nas N mais recentes, e `dossie_hash` estável (ordem de
  chegada não importa; sem relógio) que muda quando um fato muda;
- o contrato de saída: citação inventada = inválida, decisão fora do vocabulário, campo extra, `alvo` desconhecido ou
  indevido, confiança percentual, conclusão longa; a classe A não aceita parecer; na C o parecer válido é só parecer
  (o aceite automático é recusado pela política);
- a forma cabe na `learning_reviews` (069): `dossie_hash`, `dossie`, `saida`, `validade`, `classe_de_risco`, `politica`.

Nível de prova: `simulated` (domínio puro; nenhuma chamada a provedor de IA, banco ou aparelho).
"""
from __future__ import annotations

import json
import re
from dataclasses import replace

from app.modules.learning.application.curador import CuradorPorIA
from app.modules.learning.domain import conteudo
from app.modules.learning.domain.curador import (CAMPOS_DA_SAIDA, LIMITE_DA_CONCLUSAO, OPCOES_FECHADAS, Confianca,
                                                 Decisao, Dossie, Evidencia, GrupoDeFalha, IdentidadeDoItem,
                                                 Intervencao, MotivoDeInvalidade, PassoDaTrilha, Relacao, Voto,
                                                 confianca_da_probabilidade, conteudo_do_dossie, montar_dossie,
                                                 opcoes_do_dossie, validar_saida)
from app.modules.learning.domain.politica_de_risco import (ClasseDeRisco, FatosDeRisco, FatosDoCatalogo,
                                                           RecusaDoAceite, conferir_aceite)

APP = "com.exemplo.app"

#: Textos que NUNCA podem aparecer no dossiê: valor digitado, texto de tela, texto de pessoa, nome sigiloso.
PROIBIDOS = ("Enviar agora", "Caixa de saída", "minha nota pessoal sobre a Ana", "manda oi pra Ana",
             "senha_do_banco", "tela pedia captcha", "motivo livre da pessoa", "Fale com o suporte")


def _receita_legivel() -> dict[str, object]:
    r = conteudo.ReceitaLida(
        id=100, app=APP, app_version="1.0", assinatura="sig", variante="", step_hash="h1", step_key="SEND",
        versao=1, status="validated",
        acoes=[{"tool": "tap", "commit": True, "selectors": [{"kind": "rid+text", "rid": "id/send",
                                                                  "text": "Enviar agora"}]},
               {"tool": "type_text", "args": {"text": "{mensagem}"},
                "selectors": [{"kind": "desc", "desc": "Caixa de saída"}]},
               {"tool": "type_secret", "args": {"text": "{senha_do_banco}"}},
               {"tool": "collect_list", "args": {"item_selector": "Fale com o suporte", "exclude": ["a"]}}],
        aprendida_de="st-1", replay_ok=3, replay_fail=1, consecutive_fail=0, shadow_agree=2, shadow_total=2,
        last_used_at="2026-10-01T10:00:00Z")
    return conteudo.receita_legivel(r, etapa=conteudo.EtapaDeOrigem("st-1", "run-1", "SEND_MESSAGE"),
                                    do_mesmo_template=[], anterior=None, seguinte=None)


def _item(**kw: object) -> IdentidadeDoItem:
    base: dict[str, object] = {"kind": "receita", "ref": "100", "app": APP, "capability": "SEND_MESSAGE",
                               "app_version": "1.0", "estado": "validated", "origem": "execucao", "side_effect": True,
                               "criado_em": "2026-09-30T08:00:00Z"}
    base.update(kw)
    return IdentidadeDoItem(**base)  # type: ignore[arg-type]


def _ev(i: int, dia: int, *, run: str | None = None) -> Evidencia:
    return Evidencia(id=i, posicao="for", origin_ref=f"step:{i}", em=f"2026-09-{dia:02d}T00:00:00Z",
                     run_id=run, aparelho="android-01", app_version="1.0")


B = FatosDeRisco(side_effect=True, human_origin=False, tem_catalogo=False)               # commit sem catálogo
C = FatosDeRisco(side_effect=True, human_origin=False, tem_catalogo=True, catalogo=FatosDoCatalogo(risco="high",
                                                                                                    efeito_externo=True))
A = FatosDeRisco(side_effect=False, human_origin=False, tem_catalogo=True, catalogo=FatosDoCatalogo())


def _dossie(fatos: FatosDeRisco = B, **kw: object) -> Dossie:
    base: dict[str, object] = {
        "evidencias": [_ev(1, 10, run="run-1"), _ev(2, 11, run="run-2")],
        "trilha": [PassoDaTrilha(id=7, de="candidate", para="validated", em="2026-09-12T00:00:00Z")],
        "relacoes": [Relacao(tipo="substituta", kind="receita", ref="101", estado="candidate")],
        "falhas": [GrupoDeFalha(id="fk-abc", falha="alvo_ausente", ocorrencias=3, estado="open")],
        "votos": [Voto(id=5, veredito="errado", motivo="alvo_errado")],
        "intervencoes": [Intervencao(id=9, tipo="tomou_controle", em="2026-09-13T00:00:00Z", run_id="run-3")],
    }
    base.update(kw)
    return montar_dossie(_item(), fatos, _receita_legivel(), **base)  # type: ignore[arg-type]


def _saida(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {"decisao": "observar", "alvo": None, "confianca": "media",
                               "conclusao": "Duas reproduções a favor e um voto contra.",
                               "evidencias_citadas": ["ev:1", "voto:5"], "riscos": [], "inconsistencias": [],
                               "falta": ["reproducao_em_outro_aparelho"], "causa": "evidencia_contraditoria",
                               "faixa": "B"}
    base.update(kw)
    return base


# ------------------------------------------------------------------ o dossiê
def test_dossie_so_fatos_sem_texto_de_tela_de_pessoa_nem_valor() -> None:
    licao = conteudo.licao_legivel({"modelo": "evitar", "acao": "tap", "alvo": {"tipo": "parametro",
                                                                              "valor": "senha_do_banco"}},
                                   texto="minha nota pessoal sobre a Ana", app=APP, capability="X", step_hash="h",
                                   role="actor", tokens=12)
    fluxo = conteudo.fluxo_legivel({"steps": [{"key": "s1", "capability": "SEND_MESSAGE", "side_effect": True,
                                               "commit_selector": "Enviar agora",
                                               "postcondition": {"kind": "model_judged",
                                                                 "description": "Fale com o suporte"},
                                               "bindings": {"texto": "manda oi pra Ana", "senha_do_banco": "x"}}]},
                                   nome="manda oi pra Ana", comando_modelo="manda oi pra Ana", fonte=None,
                                   source_run_id="run-9")
    tela = conteudo.tela_legivel({"tela": "inbox", "casa": True, "ids_todos": ["id/b", "id/a"],
                                  "razao": "tela pedia captcha"})
    trilha = [PassoDaTrilha(id=1, para="disabled", em="2026-09-01T00:00:00Z", por_pessoa=True)]
    for legivel in (_receita_legivel(), licao, fluxo, tela):
        d = montar_dossie(_item(), B, legivel, trilha=trilha)
        texto = json.dumps(d.como_dados(), ensure_ascii=False)
        for proibido in PROIBIDOS:
            assert proibido not in texto, (legivel["tipo"], proibido)
    receita = conteudo_do_dossie(_receita_legivel())
    assert receita["acoes"][0]["alvo"] == [{"tipo": "rid+text", "rid": "id/send"}]   # só o id de interface
    assert receita["acoes"][2]["segredo"] is True and receita["acoes"][1]["parametros"] == ["mensagem"]
    assert conteudo_do_dossie(tela)["ids_todos"] == ["id/a", "id/b"]


def test_item_de_sessao_nao_leva_conteudo() -> None:
    sessao = FatosDeRisco(side_effect=False, human_origin=False, tem_catalogo=True, sessao_ou_autenticacao=True)
    d = montar_dossie(_item(), sessao, _receita_legivel())
    assert d.classe is ClasseDeRisco.C
    assert d.conteudo == {"tipo": "receita", "omitido": "sessao_ou_autenticacao"}
    tela = conteudo_do_dossie(conteudo.tela_legivel({"tela": "login", "autenticada": True, "ids_todos": ["id/x"]}))
    assert tela["ids_todos"] == [] and tela["total_de_ids"] == 1


def test_dossie_hash_estavel_e_independente_da_ordem() -> None:
    d1 = _dossie()
    d2 = _dossie(evidencias=[_ev(2, 11, run="run-2"), _ev(1, 10, run="run-1")],
                 relacoes=[Relacao(tipo="substituta", kind="receita", ref="101", estado="candidate")] * 2)
    assert d1.dossie_hash == d2.dossie_hash
    assert re.fullmatch(r"[0-9a-f]{64}", d1.dossie_hash)
    assert d1.dossie_hash == _dossie().dossie_hash                              # sem relógio: repete igual
    assert _dossie(evidencias=[_ev(1, 10), _ev(2, 11), _ev(3, 12)]).dossie_hash != d1.dossie_hash
    assert _dossie(C).dossie_hash != d1.dossie_hash                             # classe mudou, dossiê mudou


def test_evidencias_cortadas_nas_mais_recentes_com_o_total() -> None:
    d = _dossie(evidencias=[_ev(i, i) for i in range(1, 29)], max_evidencias=5)
    assert [e.id for e in d.evidencias] == [28, 27, 26, 25, 24]
    dados = d.como_dados()
    assert dados["evidencias"]["total"] == 28 and dados["evidencias"]["incluidas"] == 5
    assert "ev:28" in d.citaveis and "ev:1" not in d.citaveis                   # a cortada não é citável


def test_ids_citaveis_do_dossie() -> None:
    d = _dossie(saude={"rotulo": "saudavel"})
    assert {"item", "risco", "conteudo", "saude", "receita:100", "ev:1", "ev:2", "tr:7", "receita:101", "fk-abc",
            "voto:5", "sinal:9", "run:run-1", "run:run-2", "run:run-3"} <= d.citaveis
    assert "versao" not in d.citaveis and "politica" not in d.citaveis         # opcionais ausentes não são citáveis
    assert d.como_dados()["citaveis"] == sorted(d.citaveis)
    assert d.alvos_possiveis == {"receita:101"}


def test_dossie_cabe_em_learning_reviews() -> None:
    d = _dossie(C)
    dados = d.como_dados()
    json.dumps(dados)                                                           # serializável: vai a `dossie`
    assert dados["risco"]["classe"] == "C" and dados["risco"]["politica"] == "dono_item_a_item"
    assert d.tamanho_em_bytes() > 0
    v = validar_saida(_saida(), d)
    assert v.ok and v.validade == "ok"
    assert v.parecer is not None and set(v.parecer.como_dados()) == CAMPOS_DA_SAIDA | {"probabilidade"}
    json.dumps(v.parecer.como_dados())                             # vai a `saida`


# ------------------------------------------------------------------ a validação da saída
def test_citacao_inventada_e_invalida() -> None:
    d = _dossie()
    v = validar_saida(_saida(evidencias_citadas=["ev:1", "ev:999"]), d)
    assert not v.ok and v.motivo is MotivoDeInvalidade.CITACAO_DESCONHECIDA
    assert v.validade == "invalida:citacao_desconhecida"
    inventada = validar_saida(_saida(evidencias_citadas=["run:inventada"]), d)
    assert inventada.motivo is MotivoDeInvalidade.CITACAO_DESCONHECIDA
    assert validar_saida(_saida(evidencias_citadas=[1]), d).motivo is MotivoDeInvalidade.CITACAO_INVALIDA


def test_decisao_fora_do_vocabulario_campo_extra_e_ausente() -> None:
    d = _dossie()
    assert validar_saida(_saida(decisao="publicar"), d).motivo is MotivoDeInvalidade.DECISAO_FORA_DO_VOCABULARIO
    assert validar_saida(_saida(aplicar=True), d).motivo is MotivoDeInvalidade.CAMPO_EXTRA
    sem = _saida()
    del sem["decisao"]
    assert validar_saida(sem, d).motivo is MotivoDeInvalidade.CAMPO_AUSENTE


def test_confianca_categorica_conclusao_curta_e_listas() -> None:
    d = _dossie()
    assert validar_saida(_saida(confianca="0.9"), d).motivo is MotivoDeInvalidade.CONFIANCA_INVALIDA
    assert validar_saida(_saida(confianca=0.9), d).motivo is MotivoDeInvalidade.CONFIANCA_INVALIDA
    longa = "x" * (LIMITE_DA_CONCLUSAO + 1)
    assert validar_saida(_saida(conclusao=longa), d).motivo is MotivoDeInvalidade.CONCLUSAO_INVALIDA
    assert validar_saida(_saida(conclusao="  "), d).motivo is MotivoDeInvalidade.CONCLUSAO_INVALIDA
    sem_conclusao = _saida()
    del sem_conclusao["conclusao"]
    v = validar_saida(sem_conclusao, d)                             # o único texto livre é opcional
    assert v.parecer is not None and v.parecer.conclusao is None


def test_campos_de_decisao_sao_rotulos_fechados() -> None:
    d = _dossie()
    fora = MotivoDeInvalidade.ROTULO_FORA_DO_VOCABULARIO
    assert validar_saida(_saida(riscos=["pode dar ruim"]), d).motivo is fora
    assert validar_saida(_saida(falta="texto solto"), d).motivo is fora
    assert validar_saida(_saida(inconsistencias=["outra coisa"]), d).motivo is fora
    assert validar_saida(_saida(causa="porque sim"), d).motivo is fora
    assert validar_saida(_saida(faixa="D"), d).motivo is fora
    v = validar_saida(_saida(riscos=["efeito_externo", "efeito_externo", "irreversivel"]), d)
    assert v.parecer is not None and [r.value for r in v.parecer.riscos] == ["efeito_externo", "irreversivel"]


def test_opcoes_para_o_adaptador_de_choice() -> None:
    assert OPCOES_FECHADAS["decisao"] == tuple(x.value for x in Decisao)
    assert OPCOES_FECHADAS["faixa"] == ("A", "B", "C")
    assert set(OPCOES_FECHADAS) <= CAMPOS_DA_SAIDA
    assert "conclusao" not in OPCOES_FECHADAS                       # texto livre não é escolha
    d = _dossie()
    opcoes = opcoes_do_dossie(d)
    assert opcoes["alvo"] == ("receita:101",) and set(opcoes["evidencias_citadas"]) == d.citaveis
    assert opcoes["decisao"] == OPCOES_FECHADAS["decisao"]                  # item sem marca: todas as decisões


def test_a_receita_sem_caminho_nao_tem_pedir_evidencia_nas_opcoes_nem_no_parecer() -> None:
    """30.40: com a marca "variante sem caminho" (30.36), `pedir_evidencia` sai das opções do item, o pedido ao
    provedor e o esquema estrito do hub não a oferecem, e o parecer que a escolher assim mesmo é inválido
    (`decisao_indevida`: fica o registro, nenhum pedido de validação nasce). As outras decisões seguem valendo."""
    from app.planning.curador import esquema_do_parecer

    comum = _dossie()
    assert validar_saida(_saida(decisao="pedir_evidencia"), comum).parecer is not None
    marcado = replace(comum, item=_item(sem_caminho=True))
    decisoes = opcoes_do_dossie(marcado)["decisao"]
    assert set(decisoes) == set(OPCOES_FECHADAS["decisao"]) - {Decisao.PEDIR_EVIDENCIA.value}
    pedido = CuradorPorIA._pedido(marcado)
    assert Decisao.PEDIR_EVIDENCIA.value not in pedido.opcoes["decisao"]
    esquema = esquema_do_parecer(pedido.opcoes)["properties"]
    assert isinstance(esquema, dict) and Decisao.PEDIR_EVIDENCIA.value not in esquema["decisao"]["enum"]
    v = validar_saida(_saida(decisao="pedir_evidencia"), marcado)
    assert v.parecer is None and v.motivo is MotivoDeInvalidade.DECISAO_INDEVIDA
    for d in ("possivelmente_obsoleto", "observar", "manter"):
        assert validar_saida(_saida(decisao=d), marcado).ok, d


def test_confianca_derivada_da_probabilidade_nunca_numero_da_ia() -> None:
    d = _dossie()
    assert confianca_da_probabilidade(0.3) is Confianca.BAIXA
    assert confianca_da_probabilidade(0.7) is Confianca.MEDIA
    assert confianca_da_probabilidade(0.9) is Confianca.ALTA
    v = validar_saida(_saida(confianca="baixa"), d, probabilidade=0.92)
    assert v.parecer is not None and v.parecer.confianca is Confianca.ALTA and v.parecer.probabilidade == 0.92
    sem = _saida()
    del sem["confianca"]
    v = validar_saida(sem, d)
    assert v.parecer is not None and v.parecer.confianca is None                # sem medida, não inventa
    assert validar_saida(_saida(), d, probabilidade=1.5).motivo is MotivoDeInvalidade.PROBABILIDADE_INVALIDA


def test_faixa_da_ia_nunca_afrouxa_a_da_politica() -> None:
    c = validar_saida(_saida(faixa="B"), _dossie(C)).parecer
    assert c is not None and c.faixa_efetiva(ClasseDeRisco.C) is ClasseDeRisco.C
    b = validar_saida(_saida(faixa="C"), _dossie()).parecer
    assert b is not None and b.faixa_efetiva(ClasseDeRisco.B) is ClasseDeRisco.C
    a = validar_saida(_saida(faixa="C"), _dossie(A)).parecer
    assert a is not None and a.faixa_efetiva(ClasseDeRisco.A) is ClasseDeRisco.A     # na A, a IA não muda nada


def test_sem_citacao_so_para_manter() -> None:
    d = _dossie()
    assert validar_saida(_saida(evidencias_citadas=[]), d).motivo is MotivoDeInvalidade.SEM_CITACAO
    v = validar_saida(_saida(decisao="manter", evidencias_citadas=[]), d)
    assert v.ok and v.parecer is not None and v.parecer.decisao is Decisao.MANTER


def test_alvo_so_de_item_relacionado_no_dossie() -> None:
    d = _dossie()
    ok = validar_saida(_saida(decisao="substituir", alvo="receita:101"), d)
    assert ok.ok and ok.parecer is not None and ok.parecer.alvo == "receita:101"
    assert validar_saida(_saida(decisao="fundir"), d).motivo is MotivoDeInvalidade.ALVO_AUSENTE
    assert validar_saida(_saida(decisao="fundir", alvo="receita:555"), d).motivo is MotivoDeInvalidade.ALVO_DESCONHECIDO
    assert validar_saida(_saida(decisao="fundir", alvo="receita:100"), d).motivo is MotivoDeInvalidade.ALVO_DESCONHECIDO
    assert validar_saida(_saida(decisao="fundir", alvo="ev:1"), d).motivo is MotivoDeInvalidade.ALVO_DESCONHECIDO
    assert validar_saida(_saida(alvo="receita:101"), d).motivo is MotivoDeInvalidade.ALVO_INDEVIDO


def test_json_cru_e_formas_erradas() -> None:
    d = _dossie()
    assert validar_saida(json.dumps(_saida()), d).ok
    assert validar_saida("{não é json", d).motivo is MotivoDeInvalidade.JSON_INVALIDO
    assert validar_saida("[1, 2]", d).motivo is MotivoDeInvalidade.NAO_E_OBJETO
    v = validar_saida(_saida(evidencias_citadas=["ev:1", "ev:1", "voto:5"]), d)
    assert v.parecer is not None and v.parecer.evidencias_citadas == ("ev:1", "voto:5")


def test_classe_a_parecer_valido_so_de_registro() -> None:
    d = _dossie(A)
    assert d.classe is ClasseDeRisco.A and d.risco.ia_permitida == "so_com_sobra"
    v = validar_saida(_saida(decisao="rebaixar"), d)
    assert v.ok and v.validade == "ok"                              # gravado em `learning_reviews`...
    assert d.risco.efeito_do_parecer == "so_registro"               # ...mas nunca move o item
    for pessoa, lote in ((False, False), (True, False), (True, True)):
        assert conferir_aceite(d.classe, por_pessoa=pessoa, em_lote=lote) is RecusaDoAceite.SO_REGISTRO_NA_CLASSE_A


def test_classe_c_parecer_valido_mas_nunca_decisao_automatica() -> None:
    d = _dossie(C)
    v = validar_saida(_saida(decisao="aprovar", confianca="alta"), d)
    assert v.ok                                                     # o parecer é gravado como apoio
    assert not d.risco.decide_sozinho and not d.risco.aceita_lote
    assert conferir_aceite(d.classe, por_pessoa=False, em_lote=False) is RecusaDoAceite.DECISAO_AUTOMATICA_EM_C
    assert conferir_aceite(d.classe, por_pessoa=True, em_lote=True) is RecusaDoAceite.LOTE_NA_CLASSE_C
    assert conferir_aceite(d.classe, por_pessoa=True, em_lote=False) is None


def test_dossie_do_fluxo_leva_os_apps_sem_o_comando() -> None:
    """O fluxo que atravessa apps (12.1) chega ao curador com o principal, os exigidos e o app de cada etapa; o comando
    (texto da pessoa) continua fora."""
    legivel = conteudo.fluxo_legivel({"app_id": "instagram", "steps": [
        {"key": "ler", "app_id": "outlook", "capability": "READ_LATEST_SUBJECT", "side_effect": False},
        {"key": "buscar", "capability": "OPEN_PROFILE", "side_effect": False}]},
        nome="ler no Outlook e achar no Instagram", comando_modelo="ler no Outlook e achar no Instagram", fonte=None,
        source_run_id="r-1", apps=["outlook", "instagram"])
    d = conteudo_do_dossie(legivel)
    assert (d["app"], d["apps"]) == ("instagram", ["outlook", "instagram"])      # ordem do plano (29.42)
    assert [e["app"] for e in d["etapas"]] == ["outlook", None]
    assert "achar no Instagram" not in json.dumps(d, ensure_ascii=False)


def test_contrato_do_dossie_com_evidencia_invalida() -> None:
    """30.42: a posição `invalida` (prova que não vale) entra na lista com o nome dela, o dossiê ganha a nota, e nada a
    conta como contra nem a favor: o curador simulado e o parecer simulado (`planning/curador`) a ignoram. A nota só
    existe quando há linha `invalida`. (O `evidencias_contra` do `decisao_fechada` é da frente da Jev: ver o PR.)"""
    from app.modules.learning.domain.curador import INVALIDA_DA_EVIDENCIA
    from app.planning.curador import PedidoDeParecer, parecer_simulado

    fatos = FatosDeRisco(side_effect=False, human_origin=False, tem_catalogo=False)
    sem = montar_dossie(IdentidadeDoItem(kind="fluxo", ref="f"), fatos, {},
                        evidencias=[Evidencia(id=1, posicao="for", origin_ref="run:a", em="2026-10-03T10:00:00Z")]
                        ).como_dados()["evidencias"]
    assert isinstance(sem, dict) and "invalida_e" not in sem
    dossie = montar_dossie(IdentidadeDoItem(kind="fluxo", ref="f"), fatos, {}, evidencias=[
        Evidencia(id=2, posicao="invalida", origin_ref="run:b", em="2026-10-03T11:00:00Z"),
        Evidencia(id=1, posicao="for", origin_ref="run:a", em="2026-10-03T10:00:00Z")]).como_dados()
    evidencias = dossie["evidencias"]
    assert isinstance(evidencias, dict) and evidencias["invalida_e"] == INVALIDA_DA_EVIDENCIA
    lista = evidencias["lista"]
    assert isinstance(lista, list) and [e["posicao"] for e in lista] == ["invalida", "for"]
    # o parecer simulado do hub: só `against`/`conflict` contam contra, e a `invalida` não é um dos dois
    contra = sum(1 for e in lista if e["posicao"] in ("against", "conflict"))
    assert contra == 0
    parecer = parecer_simulado(PedidoDeParecer(dossie=dossie, classe="B", opcoes={"evidencias_citadas": []}))
    assert parecer.bruto["decisao"] == "manter"

