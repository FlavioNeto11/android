"""`ProfileDataStore` sobre o banco (ADR-040): só `SELECT` em `instagram_profiles`, `profile_accounts`, `apps` e
`account_credentials`. Da credencial saem metadados e a `secret_ref` — o valor fica no cofre, e só o canal sensível
o abre, no instante da digitação.

Qual app tem provedor de sessão é conhecimento do registro de apps, que fica abaixo de identity no grafo de
contextos: quem compõe injeta a pergunta (`tem_provedor_de_sessao`), como em `account_session.py`.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from app.db import Database
from app.modules.identity.domain.available_data import PROFILE_FIELDS, AccountRecord

# `biography` é o JSON de onde saem os marcadores de `BIOGRAPHY_FIELDS` (31.87 F2); quem o abre é o domínio.
_COLUNAS_DO_PERFIL = ", ".join(sorted({coluna for _n, coluna, _r, _t in PROFILE_FIELDS} | {"username", "biography"}))


def _texto(valor: object) -> str | None:
    if valor is None or isinstance(valor, str):
        return valor
    raise TypeError(f"esperado texto ou nulo, veio {type(valor).__name__}")


class SqlProfileDataStore:
    def __init__(self, db: Database, *, tem_provedor_de_sessao: Callable[[str], bool]) -> None:
        self._db = db
        self._tem_provedor = tem_provedor_de_sessao

    def profile_fields(self, profile_id: str) -> Mapping[str, object] | None:
        return self._db.one(f"SELECT {_COLUNAS_DO_PERFIL} FROM instagram_profiles WHERE id=?", (profile_id,))

    def accounts_of_profile(self, profile_id: str) -> Sequence[AccountRecord]:
        linhas = self._db.query(
            "SELECT a.id AS account_id, a.app_id,"
            # Conta ainda não confirmada: o endereço DESEJADO faz as vezes do usuário (31.281, ADR-087), para o plano que
            # preenche o cadastro; `handle` segue vazio no banco.
            " CASE WHEN a.provisioning_state = 'confirmada' THEN a.handle"
            "      ELSE COALESCE(NULLIF(a.handle, ''), a.desired_handle, '') END AS handle,"
            " a.host, ap.package, ap.name AS app_name,"
            " c.login_identifier, c.secret_ref, c.status AS credential_status, c.consent_at"
            " FROM profile_accounts a LEFT JOIN apps ap ON ap.id = a.app_id"
            " LEFT JOIN account_credentials c ON c.account_id = a.id"
            " WHERE a.profile_id=? AND a.status='active' ORDER BY a.created_at, a.id", (profile_id,))
        saida: list[AccountRecord] = []
        for r in linhas:
            pacote = _texto(r["package"])
            saida.append(AccountRecord(
                account_id=str(r["account_id"]), app_id=str(r["app_id"]), package=pacote,
                app_label=_texto(r["app_name"]) or str(r["app_id"]), handle=_texto(r["handle"]) or "",
                host=_texto(r["host"]), login_identifier=_texto(r["login_identifier"]),
                has_credential=r["secret_ref"] is not None, credential_status=_texto(r["credential_status"]),
                consent_at=_texto(r["consent_at"]), secret_ref=_texto(r["secret_ref"]),
                managed=bool(pacote) and self._tem_provedor(pacote or "")))
        return saida
