"""Gatilhos do egresso por IP residencial (migração 133). Infraestrutura: tocam `AppState` e o subsistema de rede.

Duas rotinas, ligadas em `bootstrap.py` aos callbacks do `SocialService`:

- `vincular_egresso`: quando um vínculo persona↔device nasce DEPOIS do registro da conta, atribui ao device o
  perfil de proxy `igfarm-{account_id}` que já existe (criado pelo `registrar()` da ponte).
- `limpar_egresso`: quando a conta é retirada (`retirar_conta_bloqueada`), na ordem: desatribui o perfil de
  todos os devices que o pedem, remove o perfil e apaga o segredo de rastreio do cofre.

Nada aqui CRIA perfil: a criação é do `registrar()`. Estas rotinas só ligam o perfil ao device certo e o
desmontam. `real_account_confirm_required` fica pendente e é registrado; o resto sobe para quem chamou (que já
isola o vínculo/retirada com try/except).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.devices.rede import NetworkAssignBody, RedeError, atribuir, remover_perfil
from app.models import NetworkPolicy

if TYPE_CHECKING:
    from app.state import AppState

log = logging.getLogger(__name__)


def _perfil_da_conta(st: AppState, account_id: str) -> str | None:
    row = st.db.one("SELECT id FROM network_profiles WHERE name=?", (f"igfarm-{account_id}",))
    return str(row["id"]) if row is not None else None


def vincular_egresso(st: AppState, profile_id: str, instance_id: str) -> None:
    """Atribui ao device o perfil de egresso de cada conta igfarm da persona (política `exigida`).

    Não é destrutivo com o que já está pedido: se o device já tem ESTE perfil com uma política que segura
    (`exigida` ou `exigida_com_bloqueio`), sai sem chamar `atribuir`. E, se o device já está em
    `exigida_com_bloqueio`, preserva o bloqueio em vez de rebaixar para `exigida` (rebaixar mudaria a configuração
    do aparelho, bumparia a revisão e jogaria o device de volta a `pendente`, perdendo o `trafego_verificado`).
    """
    contas = [str(r["account_id"]) for r in st.db.query(
        "SELECT account_id FROM contas_igfarm WHERE profile_id=?", (profile_id,))]
    for account_id in contas:
        perfil_id = _perfil_da_conta(st, account_id)
        if perfil_id is None:
            continue
        atual = st.db.one("SELECT proxy_profile_id, policy FROM device_network WHERE instance_id=?", (instance_id,))
        if (atual is not None and str(atual["proxy_profile_id"] or "") == perfil_id
                and str(atual["policy"]) in ("exigida", "exigida_com_bloqueio")):
            continue                                    # já está pedido: não reatribui nem rebaixa a política
        politica: NetworkPolicy = ("exigida_com_bloqueio" if (atual is not None
                                                              and str(atual["policy"]) == "exigida_com_bloqueio")
                                   else "exigida")
        try:
            atribuir(st, NetworkAssignBody(instance_ids=[instance_id], proxy_profile_id=perfil_id,
                                           policy=politica), quem="igfarm")
        except RedeError as exc:
            if exc.code != "real_account_confirm_required":
                raise
            log.info("egresso: device %s com conta real; perfil %s fica pendente de confirmação",
                     instance_id, perfil_id)


def limpar_egresso(st: AppState, profile_id: str, account_id: str, proxy_secret_ref: str | None) -> None:
    """Desatribui o perfil dos devices que o pedem, remove o perfil e apaga o segredo de rastreio."""
    perfil_id = _perfil_da_conta(st, account_id)
    if perfil_id is not None:
        iids = [str(r["instance_id"]) for r in st.db.query(
            "SELECT instance_id FROM device_network WHERE proxy_profile_id=?", (perfil_id,))]
        for iid in iids:
            try:
                atribuir(st, NetworkAssignBody(instance_ids=[iid], proxy_profile_id=None, policy="livre",
                                               confirm_real_account=[iid]), quem="limpeza")
            except RedeError as exc:
                log.warning("egresso: não desatribuí o perfil %s de %s (%s)", perfil_id, iid, exc.code)
        try:
            remover_perfil(st, perfil_id)
        except RedeError as exc:
            log.warning("egresso: não removi o perfil %s (%s)", perfil_id, exc.code)
    if proxy_secret_ref:
        try:
            st.secrets.delete_secret(proxy_secret_ref)
        except Exception:  # noqa: BLE001 - cofre indisponível não desfaz a retirada
            log.exception("egresso: não apaguei o segredo de rastreio %s", proxy_secret_ref)
