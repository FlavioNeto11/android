"""Regras da transição de sessão de um perfil: o bloqueio por desafio (ADR-029) e o aviso de "precisa de pessoa".

Moravam no autenticador do Instagram (hoje o motor genérico `integrations/app_declarado/sessao.py`). Saíram de lá porque são do PERFIL,
não do Instagram: o design §6 lista "desafio → `blocked` sem reativação automática" como invariante da raiz
`Profile`, e o núcleo (`AppState._sessao_desmentida`) as aplicava importando o autenticador do Instagram só para
isso. Agora os dois chamadores — o provedor de sessão e a tela que desmente a sessão no meio de uma execução —
aplicam a MESMA regra, daqui.

O corpo veio literal; só ganhou tipos. `status` é texto: `models.SessionStatus` e o `SessionStatus` do domínio são
`StrEnum`, então comparar e formatar pelo valor dá o mesmo resultado com qualquer um dos dois.
"""
from __future__ import annotations

from app.modules.identity.domain.resources import CredentialState, SessionStatus

from .ports import EventSink, ProfileStore

#: Estados de sessão que só uma pessoa resolve — os mesmos que `state.py` usa para bloquear o agendador automático.
#: É o que decide quando o evento dedicado da fila "Aguardando intervenção" dispara.
PRECISA_DE_PESSOA: frozenset[str] = frozenset({SessionStatus.auth_challenge.value, SessionStatus.wrong_account.value})

#: `account_credentials.status` depois de um envio de senha sem sucesso (ADR-055): o login AUTOMÁTICO para até uma
#: pessoa olhar. A juliana recebeu seis envios em 4h25 em 18/09 — o freio de antes (3 falhas, 300 s de espera) se
#: repetia a cada intervalo vencido. Ao lado de `active` e `invalid` (a coluna é texto, sem CHECK: nenhuma migração).
#: Quem solta: a pessoa guardando a senha de novo (`set_account_credential` volta a `active`) ou um login que confirma
#: a conta — o "Conectar" do painel, que é a pessoa olhando, pode tentar.
CREDENCIAL_EM_REVISAO: str = CredentialState.review.value


def motivo_do_login_parado(app_label: str) -> str:
    """O que a pessoa lê no item bloqueado e na sessão quando o login automático parou (ADR-055)."""
    return (f"o login automático no {app_label} está parado até uma pessoa olhar a conta no aparelho (um envio de "
            "senha sem sucesso, ou o teto diário de logins); use Conectar no perfil para tentar de novo, ou guarde a "
            "senha outra vez")


def motivo_do_bloqueio_por_desafio(app_label: str) -> str:
    """Por que o perfil foi bloqueado sozinho — vai no evento e no log para a pessoa saber o que reativar e quando.

    O nome do app vem de quem chama (o rótulo do registro de apps), e não de um texto fixo: a regra é do perfil.
    """
    return (f"o {app_label} pediu verificação de segurança nesta conta; o perfil foi bloqueado "
            "sozinho para a automação não insistir numa conta travada (ADR-029)")


def bloquear_por_desafio(repo: ProfileStore, bus: EventSink, *, profile_id: str, instance_id: str,
                         anterior_status: str | None, detail: str | None, app_label: str) -> bool:
    """A conta caiu num desafio de segurança do app (hoje, o Instagram): o PERFIL passa a `blocked` sozinho (ADR-029).

    Decisão do dono de 27/09/2026: o aviso de verificação na tela é a prova de que a conta travou — das oito contas
    reais, as cinco que mostraram o aviso não voltaram. Antes, o desafio só punha a sessão em "precisa de pessoa"
    e o perfil seguia `active`: bastava a sessão ser relida (controle devolvido, validade vencida) para a porta
    tentar de novo sobre uma conta morta. Com o perfil `blocked`, a porta de sessão (`AppState._session_gate`) e a
    distribuição (`Scheduler.candidatos_do_app`) já recusam qualquer tarefa — nada novo a respeitar.

    - Só age na ENTRADA do estado (a sessão não estava em `auth_challenge`), como o evento da fila: confirmar o
      mesmo desafio não repete o aviso.
    - Só troca `active` → `blocked`. Perfil já pausado pelo dono (`disabled`) fica como está: a pausa é decisão
      dele, e reescrevê-la apagaria essa informação.
    - Não desfaz sozinho: se a pessoa resolver a tela e a sessão voltar a `session_ready`, quem reativa o perfil é
      uma pessoa, na tela do perfil — reativar é afirmar que a conta voltou a ser usável, e isso não se infere de
      uma leitura de tela.
    - Vale também para o pedido de código de dois fatores (a mesma `auth_challenge`): nenhuma conta do parque tem
      2FA configurado, e bloquear por engano custa um clique; não bloquear custa insistir numa conta travada.
    - Ao virar `blocked`, o agendador para o objetivo em curso desta conta no ponto seguro seguinte e pausa as
      execuções dela e das contas que agiram no mesmo alvo nas 48 h anteriores (`Scheduler`, disjuntor de conta,
      ADR-055). Não é chamado daqui: o agendador enxerga QUALQUER caminho para `blocked` (este, o dono no painel).
    """
    if anterior_status == SessionStatus.auth_challenge.value:
        return False
    perfil = repo.profile_row(profile_id)
    if perfil is None or (perfil["status"] or "active") != "active":
        return False
    repo.update_profile(profile_id, {"status": "blocked"})
    arroba = perfil.get("username") or profile_id
    bus.emit("log", f"{instance_id}: perfil @{arroba} bloqueado automaticamente — "
                    f"{motivo_do_bloqueio_por_desafio(app_label)}. "
                    "Se a conta voltar, reative o perfil na tela dele.", level="error", instance_id=instance_id,
             data={"profile_id": profile_id, "status": "blocked", "reason": "auth_challenge",
                   "detail": (detail or "")[:300]})
    return True


def emit_needs_person_change(bus: EventSink, *, profile_id: str, instance_id: str, status: str,
                             anterior_status: str | None, detail: str | None) -> None:
    """Evento dedicado da fila "Aguardando intervenção" (achado #106) — em vez de só `log`.

    Dispara na ENTRADA e na SAÍDA de um estado de sessão que só uma pessoa resolve (`auth_challenge`,
    `wrong_account`), nunca a cada classificação que só confirma o mesmo estado: senão cada tentativa
    automática que topa a mesma conta travada reenviaria o mesmo alerta, e devolver o controle (que já
    dispara a reobservação) nunca tiraria o item da fila. `data.active` diz se o perfil ENTROU (True) ou
    SAIU (False) — é o que deixa o painel manter a fila ao vivo sem recarregar a página.
    """
    valor = str(status)
    entrando = valor in PRECISA_DE_PESSOA
    estava = anterior_status in PRECISA_DE_PESSOA
    if entrando == estava:
        return
    bus.emit("session.needs_person",
             f"{instance_id}: o perfil {'passou a precisar' if entrando else 'deixou de precisar'} de "
             f"intervenção humana ({valor}) — {detail or 'sem detalhe'}", level="warn",
             instance_id=instance_id,
             data={"profile_id": profile_id, "instance_id": instance_id, "status": valor,
                   "detail": detail, "active": entrando})
