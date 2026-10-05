"""Regras da transição de sessão de um perfil: o bloqueio por desafio (ADR-029) e o aviso de "precisa de pessoa".

Moravam no autenticador do Instagram (hoje o motor genérico `integrations/app_declarado/sessao.py`). Saíram de lá porque são do PERFIL,
não do Instagram: o design §6 lista "desafio → `blocked` sem reativação automática" como invariante da raiz
`Profile`, e o núcleo (`AppState._sessao_desmentida`) as aplicava importando o autenticador do Instagram só para
isso. Agora os dois chamadores — o provedor de sessão e a tela que desmente a sessão no meio de uma execução —
aplicam a MESMA regra, daqui.

O corpo veio literal; só ganhou tipos. `status` é texto: `models.SessionStatus` e o `SessionStatus` do domínio são
`StrEnum`, então comparar e formatar pelo valor dá o mesmo resultado com qualquer um dos dois.

Escopo do desafio (item 23.5, decisão do dono P9 de 29/09, ADR-057): `aplicar_desafio` é a porta única dos dois
chamadores. No app ÂNCORA (o que provê a conta do perfil) nada mudou: a conta travada bloqueia a persona e põe o
aparelho em quarentena, o código e a verificação só pedem uma pessoa. Em QUALQUER OUTRO app (o Outlook é o primeiro),
o desafio para só a conta daquele app — a credencial dela vai a `review` — e a persona e a conta âncora seguem; a conta
travada de verdade mantém a quarentena do aparelho (ADR-055), porque a conta morta continua logada na tela.
"""
from __future__ import annotations

from typing import Protocol

from app.modules.identity.domain.resources import CredentialState, SessionStatus

from .ports import CredenciaisDaConta, EventSink, ProfileStore, QuarentenaDeContas

#: Estados de sessão que só uma pessoa resolve — os mesmos que `state.py` usa para bloquear o agendador automático.
#: É o que decide quando o evento dedicado da fila "Aguardando intervenção" dispara.
PRECISA_DE_PESSOA: frozenset[str] = frozenset({SessionStatus.auth_challenge.value, SessionStatus.wrong_account.value})

#: `account_credentials.status` depois de um envio de senha sem sucesso (ADR-055): o login AUTOMÁTICO para até uma
#: pessoa olhar. Uma conta real recebeu seis envios em 4h25 em 18/09 — o freio de antes (3 falhas, 300 s de espera) se
#: repetia a cada intervalo vencido. Ao lado de `active` e `invalid` (a coluna é texto, sem CHECK: nenhuma migração).
#: Quem solta: a pessoa guardando a senha de novo (`set_account_credential` volta a `active`) ou um login que confirma
#: a conta — o "Conectar" do painel, que é a pessoa olhando, pode tentar.
CREDENCIAL_EM_REVISAO: str = CredentialState.review.value


def motivo_do_login_parado(app_label: str) -> str:
    """O que a pessoa lê no item bloqueado e na sessão quando o login automático parou (ADR-055)."""
    # A verificação na lista: desde o 23.5 o desafio num app que não é o âncora também põe a credencial em `review`,
    # e a porta de OUTRO aparelho mostra esta frase sem ter visto a tela.
    return (f"o login automático no {app_label} está parado até uma pessoa olhar a conta no aparelho (um envio de "
            "senha sem sucesso, uma verificação pedida na conta, ou o teto diário de logins); use Conectar no perfil "
            "para tentar de novo, ou guarde a senha outra vez")


def motivo_do_bloqueio_por_desafio(app_label: str) -> str:
    """Por que o perfil foi bloqueado sozinho — vai no evento e no log para a pessoa saber o que reativar e quando.

    O nome do app vem de quem chama (o rótulo do registro de apps), e não de um texto fixo: a regra é do perfil.
    """
    return (f"o {app_label} pediu verificação de segurança nesta conta; o perfil foi bloqueado "
            "sozinho para a automação não insistir numa conta travada (ADR-029)")


def bloquear_por_desafio(repo: ProfileStore, bus: EventSink, *, profile_id: str, instance_id: str,
                         anterior_status: str | None, detail: str | None, app_label: str) -> bool:
    """A conta do app ÂNCORA caiu num desafio de segurança: o PERFIL passa a `blocked` sozinho (ADR-029). Só para a
    âncora (item 23.5): o desafio noutro app para só aquela conta (`parar_conta_por_desafio`); quem escolhe é
    `aplicar_desafio`.

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


#: A origem com que o detector de tela marca a conta travada na quarentena: a tela foi LIDA. É um dos valores que a
#: quarentena aceita (`ORIGENS_DE_BLOQUEIO` e o CHECK da migração 054); quem viu vai em `visto_por`.
ORIGEM_OBSERVADA = "observado"


def registrar_conta_travada(repo: object, bus: EventSink, *, profile_id: str, instance_id: str, handle: str,
                            evidencia: str, visto_por: str, app_id: str | None = None) -> bool:
    """Leva a conta travada à quarentena do repositório social, quando ele a tem (ADR-055). Devolve se o marcador
    nasceu agora.

    A quarentena (`QuarentenaDeContas`) é do pacote que chega em paralelo a este: sem ela, nada muda — o bloqueio do
    perfil (ADR-029) quem chama já aplicou. `evidencia` é o trecho da tela que casou (a frase de verificação, nunca um
    código); `visto_por` diz quem leu a tela (o motor de sessão ou a execução). `handle` e `app_id` são os da CONTA
    que travou (item 23.4): o @ daquele app e o app dele — sem `app_id`, a quarentena acha a conta pelo @. A conta
    de outro app que não o âncora entra em quarentena sem bloquear a persona (item 23.5, `marcar_conta_travada`).

    Nunca levanta. Na revisão do pacote, a quarentena real recusou a origem (`ValueError`) e o erro subiu por
    `SessaoDeclarada._save` — o `ensure_session` estourava antes do evento da fila "Aguardando intervenção" e do aviso
    do desafio — ou era engolido sem sinal no executor. O marcador que faltar vira um erro visível no histórico; o
    bloqueio e o aviso ao dono, que quem chama emite, saem do mesmo jeito."""
    if not isinstance(repo, QuarentenaDeContas):
        return False
    try:
        return repo.marcar_conta_travada(instance_id, handle, evidencia, ORIGEM_OBSERVADA, visto_por=visto_por,
                                         profile_id=profile_id, app_id=app_id)
    except Exception as exc:  # noqa: BLE001 - a quarentena falhar não pode calar o desafio (ver acima)
        bus.emit("log", f"{instance_id}: a conta @{handle} está travada, mas o marcador de quarentena do aparelho "
                        f"não pôde ser gravado ({type(exc).__name__}: {exc}): o aparelho NÃO entrou em quarentena. "
                        "Confira-o antes de usá-lo.", level="error", instance_id=instance_id,
                 data={"profile_id": profile_id, "handle": handle, "visto_por": visto_por,
                       "erro": type(exc).__name__})
        return False


class RepositorioDoDesafio(ProfileStore, CredenciaisDaConta, Protocol):
    """O que `aplicar_desafio` usa do repositório: o perfil (âncora) e a credencial da conta (outro app)."""


def motivo_do_desafio_na_conta(app_label: str) -> str:
    """Por que o login automático de UMA conta parou num desafio de outro app que não o âncora (item 23.5). Não é o
    `motivo_do_login_parado`: o desafio pode ter sido lido antes de qualquer envio de senha."""
    return (f"o {app_label} pediu verificação de segurança (ou um código) nesta conta; o login automático dela está "
            "parado até uma pessoa resolver a tela no aparelho — a persona e as contas dela nos outros apps seguem. "
            "Use Conectar na conta depois de resolver, ou guarde a senha outra vez")


def parar_conta_por_desafio(repo: CredenciaisDaConta, bus: EventSink, *, profile_id: str, account_id: str,
                            instance_id: str, handle: str, detail: str | None, app_label: str) -> bool:
    """O desafio num app que não é o âncora para SÓ a conta daquele app (item 23.5, P9): a credencial dela vai a
    `review` (ADR-055), que a porta de sessão e o motor já respeitam em todos os aparelhos. Devolve se parou agora.

    - O perfil (`blocked`) não é tocado: a persona e a conta âncora dela seguem trabalhando.
    - Credencial já em `review` ou `invalid` fica como está: já não há login automático, e `invalid` (senha recusada)
      diz mais que `review`. Sem credencial, não há login automático a parar — a sessão em `auth_challenge` e o
      evento da fila já chamam a pessoa.
    - Soltar é o de sempre da `review`: guardar a senha de novo ou um login que confirma a conta (o "Conectar").
    - Limite conhecido, o mesmo do código no âncora: a sessão já pronta da conta noutro aparelho segue valendo;
      `review` para o LOGIN, não a sessão aberta."""
    cred = repo.account_credential_row(profile_id, account_id)
    if cred is None or cred["status"] in (CREDENCIAL_EM_REVISAO, CredentialState.invalid.value):
        return False
    repo.mark_account_credential(profile_id, account_id, status=CREDENCIAL_EM_REVISAO)
    conta = handle.strip().lstrip("@")
    bus.emit("log", f"{instance_id}: conta {conta} — {motivo_do_desafio_na_conta(app_label)}", level="error",
             instance_id=instance_id,
             data={"profile_id": profile_id, "account_id": account_id, "reason": "desafio_na_conta",
                   "detail": (detail or "")[:300]})
    return True


def conta_para_conferir(*, handle: str | None, login_identifier: str | None, username: str | None,
                        ancora: bool) -> str:
    """O identificador de UMA conta: o @ dela; na conta âncora antiga (criada sem @), o @ de cadastro do perfil; senão
    o identificador de login (o e-mail da conta Outlook). Vazio quando a conta não tem nenhum.

    É o que o motor de sessão confere na tela e o `handle` da quarentena (`aplicar_desafio`): os dois chamadores da
    regra do desafio tiram daqui, porque o marcador é único por (aparelho, conta) — com o @ da persona no lugar do
    e-mail, a conta de outro app ocupava o marcador da âncora e a trava dela, depois, não era registrada."""
    principal = (handle or "").strip()
    if not principal and ancora:
        principal = (username or "").strip()
    return principal or (login_identifier or "").strip()


def aplicar_desafio(repo: RepositorioDoDesafio, bus: EventSink, *, profile_id: str, account_id: str,
                    app_id: str | None, handle: str, ancora: bool, travada: bool, instance_id: str,
                    anterior_status: str | None, detail: str | None, evidencia: str, app_label: str,
                    visto_por: str) -> None:
    """A regra única do desafio visto numa conta (item 23.5): o motor de sessão (`SessaoDeclarada._save`) e a tela
    que desmente a sessão no meio de uma execução (`AppState._sessao_desmentida`) chamam daqui, e não divergem.

    `travada` = conta travada (ADR-055) ou desafio sem subtipo (regra declarada por sinal, que sempre valeu como
    trava); o contrário é o código de login/2FA ou a verificação relatada pela IA. `anterior_status` é o da sessão
    desta conta neste aparelho ANTES da gravação.

    - App âncora (comportamento de sempre): a trava bloqueia o perfil (ADR-029) e marca a quarentena na entrada do
      estado ou quando o bloqueio acabou de acontecer; código e verificação só pedem uma pessoa.
    - Outro app (P9): qualquer desafio para só a conta daquele app (`parar_conta_por_desafio`). A trava marca a
      quarentena do aparelho sem bloquear a persona (`marcar_conta_travada` só bloqueia o perfil pela conta âncora),
      e sempre: o marcador é idempotente, e um código visto antes (sessão já em `auth_challenge`) não pode impedir a
      quarentena da trava que aparece depois."""
    if ancora:
        if not travada:
            return
        # Sem o "anterior" no bloqueio: uma sessão parada num código não pode impedir o bloqueio quando a trava
        # aparece depois; quem impede o aviso repetido é o próprio perfil já `blocked`.
        bloqueou = bloquear_por_desafio(repo, bus, profile_id=profile_id, instance_id=instance_id,
                                        anterior_status=None, detail=detail, app_label=app_label)
        if bloqueou or anterior_status != SessionStatus.auth_challenge.value:
            registrar_conta_travada(repo, bus, profile_id=profile_id, instance_id=instance_id, handle=handle,
                                    app_id=app_id, evidencia=evidencia, visto_por=visto_por)
        return
    parar_conta_por_desafio(repo, bus, profile_id=profile_id, account_id=account_id, instance_id=instance_id,
                            handle=handle, detail=detail, app_label=app_label)
    if travada:
        registrar_conta_travada(repo, bus, profile_id=profile_id, instance_id=instance_id, handle=handle,
                                app_id=app_id, evidencia=evidencia, visto_por=visto_por)


def emit_needs_person_change(bus: EventSink, *, profile_id: str, instance_id: str, status: str,
                             anterior_status: str | None, detail: str | None, account_id: str | None = None,
                             no_teto: bool = False, anterior_no_teto: bool = False) -> None:
    """Evento dedicado da fila "Aguardando intervenção" (achado #106) — em vez de só `log`.

    Dispara na ENTRADA e na SAÍDA de um estado de sessão que só uma pessoa resolve (`auth_challenge`,
    `wrong_account`), nunca a cada classificação que só confirma o mesmo estado: senão cada tentativa
    automática que topa a mesma conta travada reenviaria o mesmo alerta, e devolver o controle (que já
    dispara a reobservação) nunca tiraria o item da fila. `data.active` diz se o perfil ENTROU (True) ou
    SAIU (False) — é o que deixa o painel manter a fila ao vivo sem recarregar a página. `data.account_id`
    (quando quem chama sabe) diz de QUAL conta da persona é a sessão (item 23.4): as de apps diferentes não se
    confundem na fila.
    """
    # 29.92: `unknown` NO TETO (`no_teto`, `anterior_no_teto`: a porta parou de reobservar e espera uma pessoa) entra
    # na mesma regra, uma vez na entrada e uma na saída — não a cada reobservação que confirma o mesmo estado.
    valor = str(status)
    entrando = valor in PRECISA_DE_PESSOA or no_teto
    estava = anterior_status in PRECISA_DE_PESSOA or anterior_no_teto
    if entrando == estava:
        return
    dados: dict[str, object] = {"profile_id": profile_id, "instance_id": instance_id, "status": valor,
                                "detail": detail, "active": entrando}
    if account_id is not None:
        dados["account_id"] = account_id
    bus.emit("session.needs_person",
             f"{instance_id}: o perfil {'passou a precisar' if entrando else 'deixou de precisar'} de "
             f"intervenção humana ({valor}) — {detail or 'sem detalhe'}", level="warn",
             instance_id=instance_id, data=dados)
