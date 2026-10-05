"""30.84: reensinar o comando cujo fluxo ensinado a prova real desligou.

Antes, a `match_key` única travava o reensino: o fluxo desligado pela prova (30.81) seguia na linha, e o `save` do treino
respondia `duplicate_command`. Agora (desenho da orquestradora, 05/10):
- o ensinado que a prova real desligou, e cujo desligamento ainda é a ÚLTIMA linha da trilha, renasce na MESMA linha:
  mesmo id e referência pública, plano, sessão e nascimento novos, ativo e de novo em espera de prova (30.81);
- a trilha diz que renasceu e por que tinha sido desligado;
- o desligado por uma pessoa (ou mexido por ela depois da prova), o adotado por uma habilidade e o ativo seguem
  recusando, e a prévia do treino recusa pela mesma regra.

Nível de prova: `simulated` (banco de teste migrado; nenhum aparelho, nenhuma IA).
"""
from __future__ import annotations

import pytest

from app.modules.learning.domain.ciclo import SkillState
from app.modules.learning.domain.ensinado import MOTIVO_DA_PROVA_DO_ENSINADO
from app.modules.learning.domain.vocabulario import LivroKind, Posicao
from app.modules.learning.infrastructure.ensinado_sql import EnsinoDaValidacaoSql
from app.util import now_iso

from .test_d1_fluxos import MODELO
from .test_ensinado_em_prova import ANA, BIA, SESSAO, Mundo, mundo  # noqa: F401 - a fixture do ensinado (30.81)

NOVA = "trn-ensino-2"


def _desligado_pela_prova(mundo: Mundo) -> str:
    fid = mundo.ensina()
    mundo.execucao_de_prova(fid, "r-contra")
    assert mundo.minera_prova(fid, "r-contra", Posicao.AGAINST) == 1
    assert mundo.status(fid) == "disabled"
    return fid


def _linha(mundo: Mundo, fid: str) -> dict[str, object]:
    row = mundo.db.one("SELECT * FROM flows WHERE id=?", (fid,))
    assert row is not None
    return dict(row)


def test_o_ensinado_desligado_pela_prova_renasce_na_mesma_linha_e_volta_a_esperar_a_prova(mundo: Mundo) -> None:
    fid = _desligado_pela_prova(mundo)
    antes = _linha(mundo, fid)
    assert mundo.flows.recusa_do_treino(MODELO) is None                    # a prévia do treino deixa passar
    assert mundo.ensina(sessao=NOVA, persona=BIA) == fid                   # mesma linha, mesmo id
    depois = _linha(mundo, fid)
    assert depois["ref_publico"] == antes["ref_publico"] and depois["status"] == "active"
    assert depois["source"] == f"training:{NOVA}" and depois["uses"] == 0
    assert str(depois["created_at"]) >= str(antes["created_at"])
    ultima = mundo.repo.trilha(f"fluxo:{fid}")[-1]
    assert (ultima.from_state, ultima.to_state, ultima.decided_by) == (SkillState.DISABLED, SkillState.PUBLISHED,
                                                                         f"training:{NOVA}")
    assert "reensinado" in ultima.reason and MOTIVO_DA_PROVA_DO_ENSINADO in ultima.reason
    # de novo em espera de prova, da sessão nova (a persona que ensinou agora), e sem tentativa contada
    assert mundo.espera(fid) == {"persona": BIA, "sessao": NOVA}
    (a_provar,) = [x for x in EnsinoDaValidacaoSql(mundo.db, mundo.servico).a_provar() if x.fluxo_id == fid]
    assert (a_provar.sessao, a_provar.persona, a_provar.tentativas) == (NOVA, BIA, 0)
    assert mundo.casa([BIA]) and not mundo.casa([ANA])                   # só a persona da sessão nova, até a prova


def test_a_prova_antiga_nao_libera_o_renascido(mundo: Mundo) -> None:
    fid = _desligado_pela_prova(mundo)
    mundo.evidencia(fid, "r-contra", "for")                              # evidência de antes do renascimento
    mundo.db.execute("UPDATE learning_evidence SET observed_at='2000-01-01T00:00:00.000Z' WHERE item_ref=?",
                     (f"fluxo:{fid}",))
    mundo.ensina(sessao=NOVA, persona=BIA)
    assert mundo.espera(fid) is not None


def _desligado_por_pessoa(mundo: Mundo) -> str:
    fid = mundo.ensina()
    mundo.servico.mudar_estado(LivroKind.FLUXO, fid, SkillState.DISABLED, by="painel:dono", reason="não serve")
    return fid


def _mexido_pela_pessoa_depois_da_prova(mundo: Mundo) -> str:
    fid = _desligado_pela_prova(mundo)
    mundo.servico.mudar_estado(LivroKind.FLUXO, fid, SkillState.PUBLISHED, by="painel:dono", reason="volta")
    mundo.servico.mudar_estado(LivroKind.FLUXO, fid, SkillState.DISABLED, by="painel:dono", reason="de novo não")
    return fid


def _adotado(mundo: Mundo) -> str:
    fid = _desligado_pela_prova(mundo)
    mundo.db.execute("INSERT INTO skill_definitions(id, name, app_id, legacy_flow_id, created_at, updated_at)"
                     " VALUES (?,?,?,?,?,?)", ("ig.abrir", "abrir", "instagram", fid, now_iso(), now_iso()))
    return fid


def _ativo(mundo: Mundo) -> str:
    return mundo.ensina()


@pytest.mark.parametrize("preparar", [_desligado_por_pessoa, _mexido_pela_pessoa_depois_da_prova, _adotado, _ativo])
def test_o_que_nao_foi_desligado_pela_prova_segue_recusando(mundo: Mundo, preparar: object) -> None:
    fid = preparar(mundo)  # type: ignore[operator]
    status, fonte = mundo.status(fid), _linha(mundo, fid)["source"]
    assert mundo.flows.recusa_do_treino(MODELO) is not None                # a prévia recusa pela mesma regra
    with pytest.raises(ValueError, match="Já existe uma habilidade"):
        mundo.ensina(sessao=NOVA, persona=BIA)
    assert (mundo.status(fid), _linha(mundo, fid)["source"]) == (status, fonte)     # nada mudou na linha


def test_o_reensino_continua_barrado_pela_habilidade_publicada_do_mesmo_comando(mundo: Mundo) -> None:
    fid = _desligado_pela_prova(mundo)
    chave = _linha(mundo, fid)["match_key"]
    mundo.db.execute("INSERT INTO skill_definitions(id, name, app_id, created_at, updated_at) VALUES (?,?,?,?,?)",
                     ("ig.outra", "outra", "instagram", now_iso(), now_iso()))
    mundo.db.execute("INSERT INTO skill_versions(id, skill_id, version, state, content, content_hash, match_key,"
                     " source_kind, created_at, state_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     ("ig.outra@1", "ig.outra", 1, "published", "{}", "h", chave, "manual", now_iso(), now_iso()))
    recusa = mundo.flows.recusa_do_treino(MODELO)
    assert recusa is not None and "versionada publicada" in recusa
    with pytest.raises(ValueError, match="versionada publicada"):
        mundo.ensina(sessao=NOVA, persona=BIA)
    assert SESSAO and mundo.status(fid) == "disabled"
