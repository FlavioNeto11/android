"""29.100: a sessão grava a hora em que o estado ATUAL começou (`account_sessions.status_since`, migração 112).

Pendências mostrava `verified_at` no item da sessão parada: vazio ou de dias atrás justamente na parada, e o dono lia
uma parada de agora como coisa antiga. `status_since` muda quando o estado muda e, no `unknown`, quando a série chega ao
teto do aparelho (a parada). Regravar o mesmo estado fora disso (a reobservação abaixo do teto, o "Verificar conta" no
teto, a invalidação de quem já estava `unknown`) mantém a hora.

Prova `simulated`: harness com aparelho falso, gravações direto no repositório.
"""
from __future__ import annotations

from pathlib import Path

from app.models import ProfileCreate, SessionStatus
from app.state import AppState

from .conftest import Harness
from .test_instagram_auth import SENHA, USUARIO

IID = "android-01"
#: Um aparelho onde a persona não tem vínculo: vale o teto global (`session_unknown_retry_cap`).
SEM_VINCULO = "android-09"
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


async def test_o_unknown_administrativo_que_para_no_teto_leva_a_hora_da_parada(harness: Harness) -> None:
    """P1 da leitura do #384: o `unknown` do gesto administrativo (aqui, gravado sem `reobserved`, como o vínculo, o wipe
    e o logout) que depois para na reobservação mostra a hora em que CHEGOU ao teto, não a do gesto. Com vínculo o teto
    é 1: a primeira reobservação já é a parada. O "Verificar conta" no teto não move mais a hora."""
    st = _estado(harness)
    pid = st.social.create_profile(ProfileCreate(username=USUARIO, password=SENHA, instance_id=IID)).id
    assert st.social_repo.teto_de_unknown(IID) == 1
    st.social_repo.set_session(pid, status=SessionStatus.unknown, instance_id=IID)          # o gesto
    _marcar(st)
    st.social_repo.set_session(pid, status=SessionStatus.unknown, instance_id=IID, reobserved=True)
    perfil = st.social_repo.profile_dto(pid)
    assert perfil is not None and perfil.session.unknown_at_cap
    parada = _desde(st, pid)
    assert parada not in (None, MARCA)
    _marcar(st)
    st.social_repo.set_session(pid, status=SessionStatus.unknown, instance_id=IID, reobserved=True)  # "Verificar conta"
    st.social_repo.invalidate_sessions_of_instance(IID, reason="teste", todos_os_apps=True)
    assert _desde(st, pid) == MARCA


async def test_abaixo_do_teto_a_reobservacao_nao_move_a_hora_e_no_teto_move(harness: Harness) -> None:
    """Num aparelho sem vínculo vale o teto global: cada reobservação abaixo dele mantém a hora; a que chega a ele a
    troca, uma vez."""
    st = _estado(harness)
    pid = st.social.create_profile(ProfileCreate(username=USUARIO, password=SENHA, instance_id=IID)).id
    conta = st.social_repo.conta_ancora(pid)
    assert conta is not None
    teto = st.social_repo.teto_de_unknown(SEM_VINCULO)
    assert teto is not None and teto >= 2, teto

    def gravar(reobserved: bool) -> str | None:
        st.social_repo.set_account_session(pid, conta["id"], SEM_VINCULO, status=SessionStatus.unknown,
                                           reobserved=reobserved)
        return st.db.scalar("SELECT status_since FROM account_sessions WHERE account_id=? AND instance_id=?",
                            (conta["id"], SEM_VINCULO))

    gravar(False)
    st.db.execute("UPDATE account_sessions SET status_since=? WHERE instance_id=?", (MARCA, SEM_VINCULO))
    for _ in range(teto - 1):
        assert gravar(True) == MARCA
    assert gravar(True) not in (None, MARCA)


async def test_regravar_o_mesmo_estado_mantem_a_hora_e_mudar_troca(harness: Harness) -> None:
    """Fora do `unknown`, só a mudança de estado troca a hora: a reverificação de quem está pronto a mantém; a releitura
    que resolve e o wipe de quem estava pronto a trocam."""
    st = _estado(harness)
    pid = st.social.create_profile(ProfileCreate(username=USUARIO, password=SENHA, instance_id=IID)).id
    st.social_repo.set_session(pid, status=SessionStatus.unknown, instance_id=IID, reobserved=True)
    _marcar(st)
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
