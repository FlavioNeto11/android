"""30.42: a regra única do veredito da prova (`domain.prova.veredito_da_prova`), só sobre dados, sem banco.

Os casos reais que a motivaram, reconstruídos sinteticamente (sem texto de tela nem de pessoa): a `5f2de5` (a mensagem
enviada duas vezes virou evidência a favor), a `e1b7d0` (o app dentro de uma conversa deixada pela execução anterior
virou evidência contra) e o ator que declara pronto sem agir. Nível de prova: `simulated`.
"""
from __future__ import annotations

from app.modules.learning.domain.prova import (AcaoDaProva, EtapaDaProva, MotivoDaInvalida, TentativaDaProva,
                                               detalhe_da_invalida, efeito_repetido, motivo_da_invalida,
                                               veredito_da_prova)
from app.modules.learning.domain.vocabulario import Posicao


def _tap(*, commit: bool = False, parece: bool = False, status: str = "done") -> AcaoDaProva:
    return AcaoDaProva("tap", status, is_commit_action=commit, parece_commit=parece)


STEP_DONE = AcaoDaProva("step_done", "done")


def _etapa(seq: int, chave: str, status: str = "succeeded", *, efeito: bool = False, acoes: tuple[AcaoDaProva, ...] = (),
           ultima: tuple[AcaoDaProva, ...] | None = None, erro: str | None = None, versao: int = 1,
           resultado: object = None) -> EtapaDaProva:
    return EtapaDaProva(seq=seq, key=chave, status=status, plan_version=versao, side_effect=efeito,
                        detalhe="pós-condição não comprovada" if status == "failed" else None,
                        ultima=TentativaDaProva(erro, None, acoes if ultima is None else ultima),
                        acoes=acoes, efeito_do_resultado=resultado)


def _limpa() -> list[EtapaDaProva]:
    return [_etapa(1, "open_app", acoes=(_tap(), STEP_DONE)), _etapa(2, "open_conversation", acoes=(_tap(),)),
            _etapa(3, "send_message", efeito=True, acoes=(_tap(commit=True),)),
            _etapa(4, "verify_sent", acoes=(STEP_DONE,))]


def test_prova_limpa_e_a_favor() -> None:
    v = veredito_da_prova(_limpa(), status="completed")
    assert (v.posicao, v.motivo) == (Posicao.FOR, None) and "4/4 etapas comprovadas" in v.texto


def test_5f2de5_dois_toques_no_envio_e_invalida_por_efeito_repetido_mesmo_completa() -> None:
    """1 toque no botão de envio dentro da abertura do app (etapa sem efeito, IA) e 1 na etapa de envio (receita)."""
    etapas = _limpa()
    etapas[0] = _etapa(1, "open_app", acoes=(_tap(parece=True), STEP_DONE))
    v = veredito_da_prova(etapas, status="completed")
    assert (v.posicao, v.motivo) == (Posicao.INVALIDA, MotivoDaInvalida.EFEITO_REPETIDO)
    assert "2 vezes" in v.texto and efeito_repetido(etapas) == 2


def test_dois_efeitos_na_mesma_etapa_de_efeito_tambem_repetem() -> None:
    etapas = _limpa()
    etapas[2] = _etapa(3, "send_message", efeito=True, acoes=(_tap(commit=True), _tap(commit=True)))
    assert efeito_repetido(etapas) == 2
    assert veredito_da_prova(etapas, status="completed").motivo is MotivoDaInvalida.EFEITO_REPETIDO


def test_toque_comum_e_efeito_unico_nao_repetem() -> None:
    assert efeito_repetido(_limpa()) is None
    # um toque de cara de envio que NÃO concluiu (rejeitado) não é efeito
    etapas = _limpa()
    etapas[0] = _etapa(1, "open_app", acoes=(_tap(parece=True, status="rejected"),))
    assert efeito_repetido(etapas) is None


def test_efeito_repetido_do_resultado_vence_a_regra_propria() -> None:
    etapas = _limpa()
    etapas[3] = _etapa(4, "verify_sent", acoes=(STEP_DONE,), resultado={"copias": 3, "fonte": "verificador"})
    assert efeito_repetido(etapas) == 3                               # o diário sozinho não acusaria nada
    # chave ausente = sem repetição; forma estranha (copias < 2, bool, texto) é ignorada
    for estranho in (None, {"copias": 1, "fonte": "acoes"}, {"copias": True}, "2", {"fonte": "acoes"}):
        etapas[3] = _etapa(4, "verify_sent", acoes=(STEP_DONE,), resultado=estranho)
        assert efeito_repetido(etapas) is None, estranho
    # o resultado vence mesmo quando a regra própria também acusaria
    dupla = _limpa()
    dupla[0] = _etapa(1, "open_app", acoes=(_tap(parece=True),), resultado={"copias": 4, "fonte": "acoes"})
    assert efeito_repetido(dupla) == 4


def test_e1b7d0_falha_na_abertura_e_ponto_de_partida_nunca_contra() -> None:
    etapas = [_etapa(1, "open_app", "failed", acoes=(_tap(),)), _etapa(2, "open_conversation", "pending")]
    v = veredito_da_prova(etapas, status="completed_with_issues")
    assert (v.posicao, v.motivo) == (Posicao.INVALIDA, MotivoDaInvalida.PONTO_DE_PARTIDA)


def test_ator_que_so_declarou_pronto_e_invalida_ator_sem_acao() -> None:
    for acoes in ((STEP_DONE,), (AcaoDaProva("observe_screen", "done"), STEP_DONE), ()):
        etapas = [_etapa(1, "open_app", acoes=(_tap(),)), _etapa(2, "open_conversation", "failed", acoes=acoes)]
        v = veredito_da_prova(etapas, status="completed_with_issues")
        assert (v.posicao, v.motivo) == (Posicao.INVALIDA, MotivoDaInvalida.ATOR_SEM_ACAO), acoes


def test_contra_so_quando_a_etapa_agiu_e_falhou_na_pos_condicao() -> None:
    etapas = [_etapa(1, "open_app", acoes=(_tap(),)), _etapa(2, "open_conversation", "failed", acoes=(_tap(), STEP_DONE)),
              _etapa(3, "send_message", "pending", efeito=True)]
    v = veredito_da_prova(etapas, status="completed_with_issues")
    assert (v.posicao, v.motivo) == (Posicao.AGAINST, None)
    assert "etapa 2 (open_conversation) reprovada" in v.texto


def test_a_ultima_tentativa_decide_se_o_ator_agiu() -> None:
    """Agiu na 1ª tentativa e só declarou pronto na última: a última é a que diz."""
    etapa = _etapa(2, "open_conversation", "failed", acoes=(_tap(), STEP_DONE), ultima=(STEP_DONE,))
    v = veredito_da_prova([_etapa(1, "open_app", acoes=(_tap(),)), etapa], status="completed_with_issues")
    assert v.motivo is MotivoDaInvalida.ATOR_SEM_ACAO


def test_infra_nao_conta_nem_a_favor_nem_contra() -> None:
    for erro in ("budget", "ia", "aparelho"):
        etapas = [_etapa(1, "open_app", acoes=(_tap(),)),
                  _etapa(2, "open_conversation", "failed", acoes=(_tap(),), erro=erro)]
        assert veredito_da_prova(etapas, status="cancelled").posicao is None, erro
    # etapa reprovada sem nenhuma tentativa: não é do ator nem do fluxo
    sem_tentativa = EtapaDaProva(seq=2, key="x", status="failed", plan_version=1, side_effect=False, ultima=None)
    assert veredito_da_prova([_etapa(1, "open_app"), sem_tentativa], status="failed").posicao is None


def test_plano_revisado_e_sem_evidencia_e_as_versoes_nao_se_misturam() -> None:
    """O bug de hoje: v1 `failed` + v2 `succeeded` virava contra (a ev:49). Agora nenhuma evidência."""
    etapas = [_etapa(1, "open_app", "failed", acoes=(_tap(),), versao=1),
              _etapa(1, "open_app", acoes=(_tap(),), versao=2), _etapa(2, "send_message", efeito=True,
                                                                         acoes=(_tap(commit=True),), versao=2)]
    assert veredito_da_prova(etapas, status="completed").posicao is None


def test_sem_etapas_ou_sem_desfecho_nao_ha_evidencia() -> None:
    assert veredito_da_prova([], status="completed").posicao is None
    assert veredito_da_prova([_etapa(1, "open_app", "running")], status="running").posicao is None


def test_o_detalhe_da_invalida_e_o_contrato_com_a_validacao() -> None:
    d = detalhe_da_invalida(MotivoDaInvalida.EFEITO_REPETIDO, "o efeito saiu 2 vezes", marca="0123456789ab")
    assert d == "[0123456789ab] invalida:efeito_repetido — o efeito saiu 2 vezes"
    assert motivo_da_invalida(d) is MotivoDaInvalida.EFEITO_REPETIDO
