"""29.100: a sessão grava a hora em que o estado ATUAL começou (`account_sessions.status_since`, migração 112).

Pendências mostrava `verified_at` no item da sessão parada: vazio ou de dias atrás justamente na parada, e o dono lia
uma parada de agora como coisa antiga. `status_since` só muda quando o estado muda. Regravar o mesmo estado (a
reobservação, o "Verificar conta", a invalidação de quem já estava `unknown`) mantém a hora.

Prova `simulated`: harness com aparelho falso, gravações direto no repositório.
"""
from __future__ import annotations

from pathlib import Path

from app.models import ProfileCreate, SessionStatus
from app.state import AppState

from .conftest import Harness
from .test_instagram_auth import SENHA, USUARIO

IID = "android-01"
#: Uma hora que nenhuma gravação produziria: se continuar lá, a gravação manteve a hora.
MARCA = "2000-01-01T00:00:00.000Z"


def _estado(h: Harness) -> AppState:
    assert h.state is not None
    return h.state


def _desde(st: AppState, pid: str) -> str | None:
    linha = st.social_repo.session_row(pid, IID)
    assert linha is not None
    return linha["status_since"]


def _marcar(st: AppState) -> None:
    st.db.execute("UPDATE account_sessions SET status_since=? WHERE instance_id=?", (MARCA, IID))


async def test_regravar_o_mesmo_estado_mantem_a_hora_e_mudar_troca(harness: Harness) -> None:
    """A reobservação de quem está `unknown` (com `reobserved`, que soma no contador) e a invalidação de quem já estava
    `unknown` mantêm a hora; a releitura que resolve (`session_ready`) a troca, e o wipe de quem estava pronto também."""
    st = _estado(harness)
    pid = st.social.create_profile(ProfileCreate(username=USUARIO, password=SENHA, instance_id=IID)).id
    st.social_repo.set_session(pid, status=SessionStatus.unknown, instance_id=IID, reobserved=True)
    assert _desde(st, pid) is not None
    _marcar(st)
    st.social_repo.set_session(pid, status=SessionStatus.unknown, instance_id=IID, reobserved=True)
    st.social_repo.invalidate_sessions_of_instance(IID, reason="teste", todos_os_apps=True)
    assert _desde(st, pid) == MARCA

    st.social_repo.set_session(pid, status=SessionStatus.session_ready, instance_id=IID, verified_at="x")
    pronta = _desde(st, pid)
    assert pronta is not None and pronta != MARCA
    _marcar(st)
    st.social_repo.set_session(pid, status=SessionStatus.session_ready, instance_id=IID, verified_at="y")
    assert _desde(st, pid) == MARCA                     # a reverificação de quem está pronto não move a hora

    st.social_repo.invalidate_sessions_of_instance(IID, reason="wipe", todos_os_apps=True)
    assert _desde(st, pid) not in (None, MARCA)


async def test_os_tres_montadores_levam_a_hora_ao_rest(harness: Harness) -> None:
    """`SessionInfo.status_since` (adendo v1.51) nos três montadores: persona, persona no aparelho e conta."""
    st = _estado(harness)
    pid = st.social.create_profile(ProfileCreate(username=USUARIO, password=SENHA, instance_id=IID)).id
    st.social_repo.set_session(pid, status=SessionStatus.auth_challenge, instance_id=IID)
    _marcar(st)
    perfil = st.social_repo.profile_dto(pid)
    no_aparelho = st.social_repo.sessao_no_aparelho(pid, None, IID)
    assert perfil is not None and no_aparelho is not None
    assert perfil.session.status_since == MARCA
    assert no_aparelho.status_since == MARCA
    assert [c.session.status_since for c in st.social.list_accounts(pid)] == [MARCA]


async def test_o_preenchimento_da_112_poupa_a_sessao_pronta_e_e_repetivel(harness: Harness) -> None:
    """O `UPDATE` da migração 112 roda de novo no banco do teste (SQLite ou PostgreSQL, o que a fábrica abriu): a sessão
    parada ganha a última gravação, a pronta fica nula, e repetir não muda nada."""
    st = _estado(harness)
    parada = st.social.create_profile(ProfileCreate(username=USUARIO, password=SENHA, instance_id=IID)).id
    pronta = st.social.create_profile(ProfileCreate(username=USUARIO + "2", password=SENHA, instance_id="android-02")).id
    st.social_repo.set_session(parada, status=SessionStatus.needs_person, instance_id=IID)
    st.social_repo.set_session(pronta, status=SessionStatus.session_ready, instance_id="android-02", verified_at="x")
    st.db.execute("UPDATE account_sessions SET status_since=NULL")

    sql = (Path(__file__).resolve().parents[1] / "migrations" / "112_sessao_status_since.sql").read_text("utf-8")
    preencher = next(linha for linha in sql.splitlines() if linha.startswith("UPDATE "))
    for _ in range(2):
        st.db.execute(preencher)
        linhas = {str(r["status"]): (r["status_since"], r["updated_at"])
                  for r in st.db.query("SELECT status, status_since, updated_at FROM account_sessions")}
        assert linhas[SessionStatus.needs_person.value][0] == linhas[SessionStatus.needs_person.value][1]
        assert linhas[SessionStatus.session_ready.value][0] is None
