"""Regras da transição de sessão de um perfil: o bloqueio por desafio (ADR-029) e o aviso de "precisa de pessoa".

Moravam em `integrations/instagram/authentication.py` (que reexporta os nomes). Saíram de lá porque são do PERFIL,
não do Instagram: o design §6 lista "desafio → `blocked` sem reativação automática" como invariante da raiz
`Profile`, e o núcleo (`AppState._sessao_desmentida`) as aplicava importando o autenticador do Instagram só para
isso. Agora os dois chamadores — o provedor de sessão e a tela que desmente a sessão no meio de uma execução —
aplicam a MESMA regra, daqui.

O corpo veio literal; só ganhou tipos. `status` é texto: `models.SessionStatus` e o `SessionStatus` do domínio são
`StrEnum`, então comparar e formatar pelo valor dá o mesmo resultado com qualquer um dos dois.
"""
from __future__ import annotations

from app.modules.identity.domain.resources import SessionStatus

from .ports import EventSink, ProfileStore

#: Estados de sessão que só uma pessoa resolve — os mesmos que `state.py` usa para bloquear o agendador automático.
#: É o que decide quando o evento dedicado da fila "Aguardando intervenção" dispara.
PRECISA_DE_PESSOA: frozenset[str] = frozenset({SessionStatus.auth_challenge.value, SessionStatus.wrong_account.value})

#: Por que o perfil foi bloqueado sozinho — vai no evento e no log para a pessoa saber o que reativar e quando.
MOTIVO_BLOQUEIO_POR_DESAFIO = ("o Instagram pediu verificação de segurança nesta conta; o perfil foi bloqueado "
                               "sozinho para a automação não insistir numa conta travada (ADR-029)")


def bloquear_por_desafio(repo: ProfileStore, bus: EventSink, *, profile_id: str, instance_id: str,
                         anterior_status: str | None, detail: str | None) -> bool:
    """A conta caiu num desafio de segurança do Instagram: o PERFIL passa a `blocked` sozinho (ADR-029).

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
    """
    if anterior_status == SessionStatus.auth_challenge.value:
        return False
    perfil = repo.profile_row(profile_id)
    if perfil is None or (perfil["status"] or "active") != "active":
        return False
    repo.update_profile(profile_id, {"status": "blocked"})
    arroba = perfil.get("username") or profile_id
    bus.emit("log", f"{instance_id}: perfil @{arroba} bloqueado automaticamente — {MOTIVO_BLOQUEIO_POR_DESAFIO}. "
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
