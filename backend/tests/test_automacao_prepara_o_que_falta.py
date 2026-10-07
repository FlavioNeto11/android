"""31.274 parte 2 (ADR-085): a sugestão automática não descarta por aparelho desligado nem por sessão não conferida.

A reprodução real de 07/10: uma persona apta (aparelho vinculado e parado, app pronto, sessão `unknown`) ia para
`descartadas` com o motivo "aparelho desligado e sem sessão pronta", enquanto por nome no comando ela resolvia. A decisão
do dono é que a automação liga o aparelho e prepara a sessão; só o que ela não resolve sozinha descarta, e por CÓDIGO,
dizendo o que fazer: senha não guardada com consentimento (ADR-040), conta bloqueada (ADR-055).

Nível de prova: `simulated` (harness com aparelho falso e provedor de IA falso). Nenhum nome de persona, senha ou conta
real; a senha dos testes é gerada na hora.
"""
from __future__ import annotations

import secrets as pysecrets

import pytest
from pydantic import SecretStr

from app.models import InstanceState, PersonaPatch
from app.modules.execution.domain.orquestracao import (CartaoDePersona, DescarteOut, EscolhaOut, OrquestracaoOut,
                                                       PedidoDeOrquestracao, normalizar, orquestracao_simulada)
from app.modules.identity.presentation.schemas import CredentialUpdate
from app.taskqueue.orquestrador import (MOTIVO_CONTA_BLOQUEADA, MOTIVO_SEM_CONSENTIMENTO, MOTIVO_SEM_SENHA,
                                        Orquestrador, RunTargetsSuggestBody)

from .conftest import Harness
from .test_automatico_app_sem_conta import _persona as _persona_do_outlook
from .test_orquestracao import CATOLICA

pytestmark = pytest.mark.asyncio

COMANDO = "leia o último e-mail no Outlook"


def _orq(h: Harness) -> Orquestrador:
    assert h.state is not None
    return Orquestrador(h.state.runs, h.state.social_repo)


def _persona(h: Harness, nome: str, instance: str, *, outlook: bool = True) -> str:
    """Persona do Outlook com as crenças mínimas (sem elas o simulado a põe em `nao_avaliaveis`)."""
    assert h.state is not None
    pid = _persona_do_outlook(h, nome, instance, outlook=outlook)
    h.state.social.update_persona(pid, PersonaPatch.model_validate({"biography": {"beliefs": CATOLICA}}))
    return pid


def _guarda_a_senha(h: Harness, pid: str, *, consentimento: bool = True) -> None:
    assert h.state is not None
    conta = next(c for c in h.state.social.list_accounts(pid) if c.app_id == "outlook")
    if consentimento:
        h.state.social.set_account_credential(
            pid, conta.id, CredentialUpdate(password=SecretStr(pysecrets.token_hex(8)), consent=True), by="teste")
    else:   # a linha da credencial sem `consent_at` (o estado de uma senha guardada antes do consentimento por conta)
        h.state.social_repo.set_account_credential(pid, conta.id, login_identifier="conta.teste",
                                                   secret_ref="ref-teste", key_id="k-teste")


def _para_o_aparelho(h: Harness, iid: str) -> None:
    assert h.state is not None
    h.state.devices.devices[iid].state = InstanceState.stopped


async def test_aparelho_parado_e_sessao_desconhecida_com_senha_ficam_escolhidos_com_preparo(harness: Harness) -> None:
    pid = _persona(harness, "Sueli", "android-02")
    _guarda_a_senha(harness, pid)
    _para_o_aparelho(harness, "android-02")
    s = await _orq(harness).sugerir(RunTargetsSuggestBody(command=COMANDO))
    assert [e.profile_id for e in s.escolhidas] == [pid] and s.descartadas == []
    preparo = s.escolhidas[0].preparo
    assert any("vai ligar o aparelho android-02" in f for f in preparo)
    assert any("vai conferir a sessão" in f for f in preparo)


async def test_sem_senha_guardada_descarta_por_codigo_com_o_motivo_que_diz_o_que_fazer(harness: Harness) -> None:
    pid = _persona(harness, "Sueli", "android-02")
    _para_o_aparelho(harness, "android-02")
    s = await _orq(harness).sugerir(RunTargetsSuggestBody(command=COMANDO))
    assert s.escolhidas == []
    assert [(d.profile_id, d.motivo) for d in s.descartadas] == [(pid, MOTIVO_SEM_SENHA)]
    assert "ficha da persona" in s.descartadas[0].motivo
    # por código: nenhuma chamada de IA para descartar (e o motivo nunca cita aparelho desligado)
    assert not any(c.get("kind") == "orquestracao" for c in harness.ai.calls)
    assert "desligad" not in s.descartadas[0].motivo


async def test_senha_sem_consentimento_diz_que_falta_o_consentimento(harness: Harness) -> None:
    pid = _persona(harness, "Sueli", "android-02")
    _guarda_a_senha(harness, pid, consentimento=False)
    s = await _orq(harness).sugerir(RunTargetsSuggestBody(command=COMANDO))
    assert [(d.profile_id, d.motivo) for d in s.descartadas] == [(pid, MOTIVO_SEM_CONSENTIMENTO)]


async def test_conta_bloqueada_descarta_com_o_motivo(harness: Harness) -> None:
    assert harness.state is not None
    pid = _persona(harness, "Sueli", "android-02")
    _guarda_a_senha(harness, pid)
    harness.state.social_repo.mudar_status(pid, "blocked", origem="declarado", autor="teste")
    s = await _orq(harness).sugerir(RunTargetsSuggestBody(command=COMANDO))
    assert [(d.profile_id, d.motivo) for d in s.descartadas] == [(pid, MOTIVO_CONTA_BLOQUEADA)]


async def test_sem_religamento_automatico_o_aviso_diz_o_contrario_em_vez_de_prometer(harness: Harness) -> None:
    assert harness.state is not None
    pid = _persona(harness, "Sueli", "android-02")
    _guarda_a_senha(harness, pid)
    _para_o_aparelho(harness, "android-02")
    harness.state.runs.scheduler.get_settings().auto_start_devices = False
    s = await _orq(harness).sugerir(RunTargetsSuggestBody(command=COMANDO))
    assert [e.profile_id for e in s.escolhidas] == [pid]                      # segue escolhida: não é descarte
    assert any("não o liga" in f for f in s.escolhidas[0].preparo)
    assert not any("vai ligar" in f for f in s.escolhidas[0].preparo)


def test_o_religamento_automatico_e_padrao_ligado() -> None:
    from app.config import LimitsCfg
    assert LimitsCfg().auto_start_devices is True


def test_o_cartao_nao_chama_o_aparelho_desligado_de_impedimento() -> None:
    from app.modules.execution.domain.orquestracao import orquestracao_system
    sistema = orquestracao_system("")
    assert "NUNCA são motivo para descartar" in sistema
    assert "0 ligado" not in sistema and "com sessão pronta" not in sistema


def test_normalizar_tira_o_descarte_por_aparelho_desligado_ou_sessao() -> None:
    req = PedidoDeOrquestracao(command="x", cartoes=[CartaoDePersona("a", "A"), CartaoDePersona("b", "B"),
                                                     CartaoDePersona("c", "C")], max_personas=1)
    out = normalizar(OrquestracaoOut(
        quantidade=1, escolhidas=[EscolhaOut(profile_id="a", motivo="ok", aderencia="alta")],
        descartadas=[DescarteOut(profile_id="b", motivo="aparelho desligado e sem sessão pronta"),
                     DescarteOut(profile_id="c", motivo="o perfil não tem relação com o tema")],
        nao_avaliaveis=[], perguntas=[], resumo=""), req)
    assert [d.profile_id for d in out.descartadas] == ["c"]


def test_o_simulado_nao_descarta_por_sessao_pronta_nem_por_estar_livre() -> None:
    cartoes = [CartaoDePersona("a", "A", perfil=("hobbies: culinária",), livre=False, sessao_pronta=False,
                               tarefas_na_fila=3),
               CartaoDePersona("b", "B", perfil=("hobbies: culinária",), livre=True, sessao_pronta=True)]
    out = orquestracao_simulada(PedidoDeOrquestracao(command="duas personas para culinária", cartoes=cartoes,
                                                     max_personas=2))
    assert {e.profile_id for e in out.escolhidas} == {"a", "b"} and out.descartadas == []
    assert out.escolhidas[0].profile_id == "b"                                # o desempate só ordena


def test_sem_crencas_so_e_nao_avaliavel_quando_o_pedido_depende_delas() -> None:
    """31.277: o refactor do ADR-083 trocou `de_crenca and sem_crencas` por `sem_crencas`, e o Automático deixou de
    sugerir toda persona sem crenças, até para "leia o último e-mail"."""
    cartoes = [CartaoDePersona("a", "A", perfil=("hobbies: culinária",), sem_crencas=True)]
    leitura = orquestracao_simulada(PedidoDeOrquestracao(command="leia o último e-mail no Outlook", cartoes=cartoes))
    assert [e.profile_id for e in leitura.escolhidas] == ["a"] and leitura.nao_avaliaveis == []
    de_fe = orquestracao_simulada(PedidoDeOrquestracao(command="fale da igreja com a prima", cartoes=cartoes))
    assert de_fe.escolhidas == [] and [n.profile_id for n in de_fe.nao_avaliaveis] == ["a"]
