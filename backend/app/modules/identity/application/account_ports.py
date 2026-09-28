"""Porta de LEITURA dos dados da persona e das contas dela (ADR-040), do lado de quem consome (design §3).

Entrega metadados e a referência do cofre — nunca o valor de um segredo. Quem implementa é o adaptador
`infrastructure/profile_data.py::SqlProfileDataStore`; o contexto social (`social/context.py`) não a conhece, de
propósito: não existe caminho dali até o cofre (`test_social_memory.py`).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from app.modules.identity.domain.available_data import AccountRecord


class ProfileDataStore(Protocol):
    def profile_fields(self, profile_id: str) -> Mapping[str, object] | None:
        """As colunas do perfil que `PROFILE_FIELDS` conhece; `None` = perfil inexistente."""
        ...

    def accounts_of_profile(self, profile_id: str) -> Sequence[AccountRecord]:
        """As contas ATIVAS do perfil, com os metadados da credencial (sem valor)."""
        ...
