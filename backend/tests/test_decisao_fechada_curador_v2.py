"""31.11: o estado `v2` da triagem do curador (os campos de SINAL), ao lado do `v1` (`planning/decisao_fechada/curador.py`).

Na rodada real da R1 do braço offline (03/10 18:53Z), os 9 estados `v1` distintos deram a mesma resposta. O `v2` acrescenta
campos de outras seções do dossiê; o braço mede o `v2` nos mesmos casos ANTES de ele virar o estado da sombra do runtime
(decisão da orquestradora, 03/10). Tudo `simulated`, sem rede. Os dois testes obrigatórios da orquestradora:

- (a) o estado do curador só leva número, `sim`/`nao`, faixa de idade ou código de vocabulário fechado: nunca texto livre,
  nome, id, data ou número de versão (`test_v2_so_leva_numero_booleano_ou_vocabulario_fechado`);
- (b) a lista de campos da origem `curador` na privacidade é EXATAMENTE a desenhada (`test_lista_da_privacidade_e_exata`).
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.modules.learning.domain.politica_de_risco import Razao
from app.modules.learning.domain.saude import CodigoDoMotivo
from app.modules.learning.domain.versao import EstadoDeVersao
from app.modules.skills.domain.lifecycle import SkillState
from app.planning.decisao_fechada import privacidade
from app.planning.decisao_fechada.contrato import PedidoDeDecisao
from app.planning.decisao_fechada.curador import (CAMPOS, CAMPOS_DE_SINAL, CAMPOS_V2, ESTADO_DA_SOMBRA, ESTADOS_DE_VERSAO,
                                                  ESTADOS_DO_ITEM, FAIXAS_DE_IDADE, MOTIVOS_DE_SAUDE, NENHUM_CODIGO,
                                                  RAZOES_DE_RISCO, TriagemDoCurador, estado_do_dossie,
                                                  estado_do_dossie_v2)

AGORA = datetime(2026, 10, 3, 18, 0, tzinfo=UTC)
HASH = "b" * 64
NOME = "Lucas Girassol"                  # nome de pessoa: nunca pode sair
TEXTO = "texto livre escrito por alguém"

#: (b) A lista exata, escrita à mão aqui: mudar a privacidade exige mudar este teste, num diff que se veja.
LISTA_EXATA = frozenset({
    "kind", "estado", "origem", "side_effect", "human_origin", "classe_de_risco", "politica",
    "evidencias_total", "evidencias_a_favor", "evidencias_contra", "evidencias_simuladas",
    "falhas", "falhas_ocorrencias", "votos", "intervencoes", "execucoes", "saude",
    "versao_estado", "versao_vivas", "versao_nao_testadas", "versao_viva_comprovada",
    "uso_ok", "uso_falhas", "uso_falhas_seguidas", "uso_idade", "evidencia_a_favor_idade",
    "saude_motivos", "risco_razoes", "trilha_transicoes", "trilha_por_pessoa", "trilha_ultimo_destino"})


def _dossie(**extra: object) -> dict[str, object]:
    d: dict[str, object] = {
        "versao_do_dossie": 1,
        "item": {"id": "item:receita:R1", "kind": "receita", "ref": "R1", "app": "com.instagram.android",
                 "capability": "OPEN_POST", "app_version": "447.0.0", "estado": "published", "origem": "aprendido",
                 "side_effect": False, "human_origin": False, "criado_em": "2026-09-20T12:00:00.000Z"},
        "risco": {"classe": "A", "politica": "regra_deterministica", "motivo": None,
                  "razoes": ["efeito_declarado", "risco_medio", "efeito_declarado"], "fatos": {}},
        "conteudo": {"tipo": "receita", "uso": {"replay_ok": 12, "replay_fail": 3, "consecutive_fail": 1,
                                                "last_used_at": "2026-10-01T18:00:00.000Z"},
                     "identidade": {"app_version": "447.0.0", "assinatura": "abc"}},
        "evidencias": {"total": 3, "incluidas": 3, "lista": [
            {"id": "ev:1", "posicao": "for", "run_id": "r1", "aparelho": "android-01", "app_version": "447.0.0",
             "simulated": False, "em": "2026-09-01T10:00:00.000Z"},
            {"id": "ev:2", "posicao": "for", "run_id": "r2", "aparelho": "android-02", "app_version": "447.0.0",
             "simulated": False, "em": "2026-09-28T10:00:00.000Z"},
            {"id": "ev:3", "posicao": "against", "run_id": "r3", "aparelho": "android-03", "app_version": "447.0.0",
             "simulated": False, "em": "2026-10-03T10:00:00.000Z"}]},
        "trilha": [{"id": "t:1", "de": "draft", "para": "candidate", "por_pessoa": False, "em": "2026-09-20T12:00:00Z"},
                   {"id": "t:2", "de": "candidate", "para": "published", "por_pessoa": True,
                    "em": "2026-09-22T12:00:00Z"}],
        "falhas": [], "votos": [], "intervencoes": [], "execucoes": ["run:r1"],
        "saude": {"rotulo": "saudavel", "motivos": [
            {"codigo": "usado_recentemente", "dimensao": "frescor", "valor": 2, "limite": 30},
            {"codigo": "amostra_suficiente", "dimensao": "base_de_evidencia", "valor": 15, "limite": 5}]},
        "versao": {"estado": "comprovado", "app": "com.instagram.android", "app_version": "447.0.0",
                   "vivas": [{"versao": "447.0.0", "aparelhos": 3}], "nao_testada_em": ["448.0.0"],
                   "por_versao": [{"versao": "447.0.0", "viva": True, "aparelhos": 3, "estado": "comprovado"},
                                  {"versao": "448.0.0", "viva": False, "aparelhos": 0, "estado": "nao_testado"}]},
    }
    d.update(extra)
    return d


def _adversario() -> dict[str, object]:
    """Tudo o que não é vocabulário, em todo campo novo: nada disso pode sair."""
    d = _dossie()
    d["risco"] = {**d["risco"], "razoes": [NOME, TEXTO, "risco_alto", 7]}                     # type: ignore[dict-item]
    d["saude"] = {"rotulo": "saudavel", "motivos": [{"codigo": NOME}, {"codigo": "nunca_usado"}, "solto"]}
    d["trilha"] = [{"id": "t:1", "para": NOME, "por_pessoa": "sim", "em": "2026-09-20T12:00:00Z"}]
    d["versao"] = {"estado": TEXTO, "vivas": "três", "nao_testada_em": [NOME],
                   "por_versao": [{"versao": NOME, "viva": "sim", "estado": "comprovado"}]}
    d["conteudo"] = {"tipo": "receita", "uso": {"replay_ok": "doze", "replay_fail": True, "consecutive_fail": -1,
                                                "last_used_at": NOME}}
    return d


def _valor_fechado(campo: str, valor: str) -> bool:
    if valor.isdigit() or valor in ("sim", "nao"):
        return True
    if campo in ("uso_idade", "evidencia_a_favor_idade"):
        return valor in FAIXAS_DE_IDADE
    vocab = {"versao_estado": ESTADOS_DE_VERSAO, "saude_motivos": MOTIVOS_DE_SAUDE, "risco_razoes": RAZOES_DE_RISCO,
             "trilha_ultimo_destino": ESTADOS_DO_ITEM}.get(campo)
    if vocab is None:
        return False
    return valor == NENHUM_CODIGO or all(c in vocab for c in valor.split("+"))


# ------------------------------------------------------------------ (a) e (b), obrigatórios
@pytest.mark.parametrize("dossie", [_dossie(), _adversario()], ids=["normal", "adversario"])
def test_v2_so_leva_numero_booleano_ou_vocabulario_fechado(dossie: dict[str, object]) -> None:
    e = estado_do_dossie_v2(dossie, agora=AGORA)
    assert set(e) <= CAMPOS_V2
    for campo in set(e) & CAMPOS_DE_SINAL:
        assert _valor_fechado(campo, e[campo]), (campo, e[campo])
    texto = json.dumps(e, ensure_ascii=False)
    for proibido in (NOME, "Girassol", TEXTO, "com.instagram", "OPEN_POST", "R1", "ev:", "run:", "android-0",
                     "2026-", "447", "448", "abc", "três", "doze"):
        assert proibido not in texto


def test_lista_da_privacidade_e_exata() -> None:
    assert privacidade.CAMPOS_POR_ORIGEM["curador"] == LISTA_EXATA
    assert CAMPOS_V2 == LISTA_EXATA and CAMPOS | CAMPOS_DE_SINAL == LISTA_EXATA and not CAMPOS & CAMPOS_DE_SINAL


# ------------------------------------------------------------------ os vocabulários são os do aprendizado
@pytest.mark.parametrize("local, enum", [
    (ESTADOS_DE_VERSAO, EstadoDeVersao), (MOTIVOS_DE_SAUDE, CodigoDoMotivo), (RAZOES_DE_RISCO, Razao),
    (ESTADOS_DO_ITEM, SkillState)])
def test_vocabulario_local_e_o_enum_do_aprendizado(local: frozenset[str], enum: type) -> None:
    assert local == {m.value for m in enum}


# ------------------------------------------------------------------ o que o v2 calcula
def test_campos_de_sinal_calculados() -> None:
    e = estado_do_dossie_v2(_dossie(), agora=AGORA)
    assert {k: e[k] for k in CAMPOS_DE_SINAL} == {
        "versao_estado": "comprovado", "versao_vivas": "1", "versao_nao_testadas": "1", "versao_viva_comprovada": "sim",
        "uso_ok": "12", "uso_falhas": "3", "uso_falhas_seguidas": "1", "uso_idade": "ate_7d",
        "evidencia_a_favor_idade": "ate_7d",                         # a mais recente A FAVOR (28/09), não a contra
        "saude_motivos": "amostra_suficiente+usado_recentemente", "risco_razoes": "efeito_declarado+risco_medio",
        "trilha_transicoes": "2", "trilha_por_pessoa": "sim", "trilha_ultimo_destino": "published"}
    assert {k: e[k] for k in CAMPOS} == estado_do_dossie(_dossie())          # o v1 vai inteiro dentro do v2


def test_adversario_deixa_so_o_que_e_fechado() -> None:
    e = estado_do_dossie_v2(_adversario(), agora=AGORA)
    assert e["risco_razoes"] == "risco_alto" and e["saude_motivos"] == "nunca_usado"
    assert e["trilha_por_pessoa"] == "nao" and "trilha_ultimo_destino" not in e
    assert "versao_estado" not in e and "versao_vivas" not in e and e["versao_viva_comprovada"] == "nao"
    assert not {"uso_ok", "uso_falhas", "uso_falhas_seguidas", "uso_idade"} & set(e)


@pytest.mark.parametrize("quando, faixa", [
    ("2026-10-03T12:00:00Z", "hoje"), ("2026-09-30T18:00:00Z", "ate_7d"), ("2026-09-10T18:00:00Z", "ate_30d"),
    ("2026-08-01T18:00:00Z", "mais_30d"), (None, "nunca")])
def test_faixas_de_idade(quando: str | None, faixa: str) -> None:
    d = _dossie()
    d["conteudo"] = {"tipo": "receita", "uso": {"last_used_at": quando}}
    assert estado_do_dossie_v2(d, agora=AGORA)["uso_idade"] == faixa


def test_sem_secao_o_campo_fica_de_fora_e_lista_vazia_e_nenhum() -> None:
    d = _dossie(versao=None, trilha="x", conteudo={"tipo": "licao"})
    d["saude"] = {"rotulo": "saudavel", "motivos": []}
    d["risco"] = {"classe": "A", "politica": "regra_deterministica", "razoes": []}
    e = estado_do_dossie_v2(d, agora=AGORA)
    assert not {"versao_estado", "versao_vivas", "uso_ok", "uso_idade", "trilha_transicoes"} & set(e)
    assert e["saude_motivos"] == NENHUM_CODIGO and e["risco_razoes"] == NENHUM_CODIGO


# ------------------------------------------------------------------ o runtime segue no v1
def test_a_sombra_do_runtime_segue_no_v1() -> None:
    assert ESTADO_DA_SOMBRA == "v1"
    pedido = TriagemDoCurador.pedido(_dossie(), HASH)
    assert pedido is not None and dict(pedido.estado) == estado_do_dossie(_dossie())


def test_o_braco_pede_o_v2_e_a_privacidade_aceita(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(privacidade, "JEV_RUNTIME_SEND_APPROVED", True)
    pedido = TriagemDoCurador.pedido(_dossie(), HASH, versao_do_estado="v2", agora=AGORA)
    assert isinstance(pedido, PedidoDeDecisao) and set(pedido.estado) == CAMPOS_V2
    assert privacidade.validar(pedido).permitido


def test_versao_desconhecida_recusa() -> None:
    with pytest.raises(ValueError, match="versão do estado"):
        TriagemDoCurador.pedido(_dossie(), HASH, versao_do_estado="v3")
