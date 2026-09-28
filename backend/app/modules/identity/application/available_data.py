"""Os dados da persona que o plano pode usar, lidos pela porta (ADR-040).

A regra pura mora em `domain/available_data.py`; aqui só se liga a porta a ela. É o que `RunService` chama para
montar a lista do planejador e as variáveis por aparelho, o executor para resolver `type_secret(name)` e decidir se a
tela de senha é de pessoa, e o pré-voo para conferir `requires.secrets` de uma skill contra as contas do perfil.
"""
from __future__ import annotations

from collections.abc import Sequence

from app.modules.identity.domain import available_data as regra
from app.modules.identity.domain.available_data import AvailableDatum, SecretResolution

from .account_ports import ProfileDataStore


def available_data(store: ProfileDataStore, profile_id: str | None) -> tuple[AvailableDatum, ...]:
    """A lista (nome, rótulo, tipo, sigiloso, app) de um perfil; vazia sem perfil."""
    if not profile_id:
        return ()
    return regra.available_data(store.profile_fields(profile_id), store.accounts_of_profile(profile_id))


def common_data(store: ProfileDataStore, profile_ids: Sequence[str | None]) -> tuple[AvailableDatum, ...]:
    """O que TODOS os aparelhos da execução têm: é o que o planejador recebe."""
    return regra.common_names([available_data(store, pid) for pid in profile_ids])


def profile_variables(store: ProfileDataStore, profile_id: str | None) -> dict[str, str]:
    """`{perfil_email: "…"}` e afins, para a materialização resolver por aparelho. Nunca um sigiloso."""
    if not profile_id:
        return {}
    return regra.profile_variables(store.profile_fields(profile_id), store.accounts_of_profile(profile_id))


def resolve_secret(store: ProfileDataStore, profile_id: str | None, name: str) -> SecretResolution:
    """`type_secret(name)` no aparelho cujo objetivo é deste perfil → a senha de qual conta, ou a recusa."""
    if not profile_id:
        return SecretResolution(refusal="Este aparelho não tem perfil vinculado: não há conta cuja senha digitar.")
    return regra.resolve_secret(store.accounts_of_profile(profile_id), name)


def typable_secret_for(store: ProfileDataStore, profile_id: str | None, package: str | None) -> SecretResolution:
    """Há senha digitável para o app da etapa? (`tem_credencial` de `pede_intervencao_humana`, por app e etapa.)"""
    if not profile_id:
        return SecretResolution(refusal="sem perfil")
    return regra.typable_secret_for(store.accounts_of_profile(profile_id), package)


def account_hosts(store: ProfileDataStore, profile_id: str | None) -> frozenset[str]:
    if not profile_id:
        return frozenset()
    return regra.account_hosts(store.accounts_of_profile(profile_id))


def missing_secrets(store: ProfileDataStore, profile_id: str | None, names: Sequence[str]) -> list[str]:
    """Pré-voo: os `requires.secrets` que este perfil não tem como senha utilizável. Sem perfil, faltam todos."""
    if not names:
        return []
    if not profile_id:
        return list(names)
    return regra.missing_secrets(store.accounts_of_profile(profile_id), names)
