"""Item 29.70: no modo Automático, a tarefa só de app SEM conta vai para aparelho sem conta, não para a persona.

Visto no central em 04/10: "No QA Messenger, abra a lista de conversas e leia o nome do primeiro contato, e somente
isso." escolheu a persona de conta real do android-01. `_app_do_comando` deixa de fora o app sem conta citado sozinho,
os apps vinham vazios, toda persona com aparelho apto virava candidata e a IA escolhia pelo perfil.

O que se prova:
- o comando literal da passada vai pela distribuição (sem IA) e não cai no aparelho com conta real logada;
- sem aparelho sem conta apto, o de conta real entra, e a prévia diz isso;
- contraprova: o comando de app de conta segue indo para a persona (pela IA), e o que cita app de conta E app sem
  conta também (o roteamento entre apps não mudou).

Nível de prova: `simulated` (harness na porta 5640, aparelhos falsos, orquestrador simulado).
"""
from __future__ import annotations

import secrets as pysecrets

import pytest
from pydantic import SecretStr

from app.models import PersonaDeviceBody, ProfileCreate
from app.modules.identity.presentation.schemas import CredentialUpdate
from app.taskqueue.orquestrador import Orquestrador, RunTargetsSuggestBody

from .conftest import Harness

pytestmark = pytest.mark.asyncio

LITERAL = "No QA Messenger, abra a lista de conversas e leia o nome do primeiro contato, e somente isso."
OUTLOOK = "com.microsoft.office.outlook"


def _orq(h: Harness) -> Orquestrador:
    assert h.state is not None
    return Orquestrador(h.state.runs, h.state.social_repo)


def _chamadas(h: Harness) -> int:
    return sum(1 for c in h.ai.calls if c.get("kind") == "orquestracao")


def _persona(h: Harness, nome: str, instance: str, *, outlook: bool = False) -> str:
    """Persona com a conta do cadastro no aparelho (perfil ativo vinculado: "conta real logada" para o roteamento)."""
    assert h.state is not None
    pid = h.state.social.create_profile(ProfileCreate(
        username=f"{nome.lower()}.{pysecrets.token_hex(3)}", instance_id=instance, first_name=nome,
        last_name="Teste")).id
    if outlook:
        if h.state.db.one("SELECT 1 FROM apps WHERE id='outlook'") is None:
            h.state.db.execute("INSERT INTO apps(id, name, package, activity, builtin) VALUES "
                               "('outlook','Microsoft Outlook',?,'.Main',0)", (OUTLOOK,))
        h.state.social_repo.create_account(pid, app_id="outlook", handle=f"{nome.lower()}@exemplo.test")
        h.state.social.bind_device(pid, PersonaDeviceBody(instance_id=instance, app_id="outlook"))
    return pid


async def test_leitura_no_app_de_qa_vai_para_aparelho_sem_conta(harness: Harness) -> None:
    _persona(harness, "Tadeu", "android-01")
    s = await _orq(harness).sugerir(RunTargetsSuggestBody(command=LITERAL))
    assert s.modo == "distribuir" and s.app_ids == ["qa-messenger"]
    assert s.targets and all(t.profile_id is None and t.origem == "balanceamento" for t in s.targets)
    assert "android-01" not in {t.instance_id for t in s.targets}
    assert any("android-01" in w and "ficam de fora" in w for w in s.warnings)
    assert _chamadas(harness) == 0


async def test_sem_aparelho_sem_conta_apto_o_de_conta_real_entra_e_a_previa_diz(harness: Harness) -> None:
    for nome, iid in (("Tadeu", "android-01"), ("Quillon", "android-02"), ("Ottilie", "android-03")):
        _persona(harness, nome, iid)
    s = await _orq(harness).sugerir(RunTargetsSuggestBody(command=LITERAL))
    assert s.modo == "distribuir" and len(s.targets) == 1
    assert any("nenhum aparelho sem conta está apto" in w and s.targets[0].instance_id in w for w in s.warnings)
    assert _chamadas(harness) == 0


async def test_contraprova_app_de_conta_segue_para_a_persona(harness: Harness) -> None:
    sueli = _persona(harness, "Sueli", "android-02", outlook=True)
    # ADR-085: o Outlook tem login gerenciado; sem sessão pronta a automação só entra com a senha guardada com consentimento
    conta = next(c for c in harness.state.social.list_accounts(sueli) if c.app_id == "outlook")
    harness.state.social.set_account_credential(
        sueli, conta.id, CredentialUpdate(password=SecretStr(pysecrets.token_hex(8)), consent=True), by="teste")
    s = await _orq(harness).sugerir(RunTargetsSuggestBody(command="leia o último e-mail no Outlook"))
    assert s.modo == "ia" and [e.profile_id for e in s.escolhidas] == [sueli]
    assert [(t.instance_id, t.profile_id) for t in s.targets] == [("android-02", sueli)]
    # App de conta E app sem conta no mesmo pedido: o conjunto inteiro, por persona, como antes.
    s = await _orq(harness).sugerir(RunTargetsSuggestBody(
        command="leia o código no último e-mail do Outlook e mande no QA Messenger para QA-001"))
    assert s.modo == "ia" and set(s.app_ids) == {"outlook", "qa-messenger"}
    assert [e.profile_id for e in s.escolhidas] == [sueli]


async def test_app_de_qa_instalado_mas_nao_principal_conta_pelo_estado_do_app(harness: Harness) -> None:
    """O central de 04/10: o QA Messenger está pronto no parque inteiro e nenhum aparelho o tem como principal (são
    do Instagram). Candidato é quem o tem sabidamente pronto; o aparelho com ele fora de pronto não entra."""
    assert harness.state is not None
    db = harness.state.db
    db.execute("UPDATE instances SET app_id='instagram'")
    _persona(harness, "Tadeu", "android-01")
    for iid, estado in (("android-01", "ready"), ("android-02", "missing"), ("android-03", "ready")):
        db.execute("INSERT INTO device_app_state(instance_id, package_name, state) VALUES (?,?,?)",
                   (iid, "com.pocqa.messenger", estado))
    s = await _orq(harness).sugerir(RunTargetsSuggestBody(command=LITERAL))
    assert s.modo == "distribuir" and [t.instance_id for t in s.targets] == ["android-03"]
    assert _chamadas(harness) == 0
