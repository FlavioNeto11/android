"""Sessão com validade, e a tela que desmente o painel (item 3.3 do plano-100; achado #103).

O defeito, do jeito que doía: o estado de sessão era um cache que nunca expirava e que o executor não corrigia
ao encontrar login ou desafio DURANTE a tarefa. O painel mostrava "Conectado" para uma conta que o dono relatara
presa num desafio ("Confirm you're human", android-05, 20/09/2026); a porta de sessão deixava despachar; e, como
o status continuava `session_ready`, o autenticador automático — que tem a credencial no cofre — nunca era
acionado: a etapa parava em `waiting_user` pedindo login manual. Oito perfis estavam `session_ready` com
`verified_at` entre 18/09 16:00 e 20:58, três dias antes.
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from app.models import SessionStatus
from app.planning.catalog import pacote_ancora
from app.util import now, to_iso
from .conftest import Harness


def _perfil(h: Harness, instance_id: str, *, username: str = "conta_teste") -> str:
    pid = h.state.social_repo.create_profile(username=username, first_name=None, last_name=None,
                                             display_name=None, birth_date=None, email=None, persona_id=None)
    pid = pid if isinstance(pid, str) else pid["id"]
    h.state.social_repo.bind(pid, instance_id)
    return pid


# ---------------------------------------------------------------- a tela desmente o painel
@pytest.mark.asyncio
async def test_tela_de_senha_no_meio_da_execucao_atualiza_o_perfil(tmp_path: Path) -> None:
    h = Harness(tmp_path, 2)
    await h.boot()
    try:
        s = h.state
        pid = _perfil(h, "android-01")
        s.social_repo.set_session(pid, status=SessionStatus.session_ready, instance_id="android-01",
                                  verified_at=to_iso(now()), detail="@conta_teste confirmado na tela")

        instagram = pacote_ancora()
        assert instagram
        exec_ = s.scheduler.executor

        # Tela de senha de OUTRO app (o QA Messenger) não fala da conta do Instagram: marcá-la queimaria, sozinha,
        # uma das tentativas de autenticação automática daquele perfil.
        exec_._sessao_desmentida("android-01", "com.pocqa.messenger", "auth_required", "login do QA")
        assert s.social_repo.session_row(pid)["status"] == SessionStatus.session_ready.value

        # É o que o executor chama ao ver campo de senha do app do perfil, no meio da etapa.
        exec_._sessao_desmentida("android-01", instagram, "auth_required",
                                 "o app pediu autenticação durante a execução")

        sessao = s.social_repo.session_row(pid)
        assert sessao["status"] == SessionStatus.auth_required.value
        assert "autenticação" in sessao["detail"]
        # E `auth_required` NÃO é estado de "só uma pessoa resolve": a porta devolve trabalho ao autenticador,
        # que é o login automático que nunca disparava.
        assert SessionStatus.auth_required.value not in s._SESSAO_PRECISA_DE_PESSOA
    finally:
        await h.state.stop()


@pytest.mark.asyncio
async def test_desafio_e_conta_errada_passam_a_depender_de_pessoa(tmp_path: Path) -> None:
    h = Harness(tmp_path, 2)
    await h.boot()
    try:
        s = h.state
        pid = _perfil(h, "android-01")
        s.social_repo.set_session(pid, status=SessionStatus.session_ready, instance_id="android-01",
                                  verified_at=to_iso(now()))
        s._sessao_desmentida("android-01", "wrong_account", "a conta na tela não é a esperada")
        assert s.social_repo.session_row(pid)["status"] == SessionStatus.wrong_account.value

        s._sessao_desmentida("android-01", "auth_challenge", "Confirm you're human")
        assert s.social_repo.session_row(pid)["status"] == SessionStatus.auth_challenge.value
        rt = s.devices.get("android-01")
        # Desde o ADR-029 (27/09) o desafio também BLOQUEIA o perfil: a porta recusa já pelo status do perfil, antes
        # de olhar a sessão — nada de tentar sozinho, e agora nem depois de a sessão ser relida.
        motivo, trabalho = s._session_gate(rt)
        assert trabalho is None and "'blocked'" in motivo
        assert s.social_repo.profile_row(pid)["status"] == "blocked"
    finally:
        await h.state.stop()


@pytest.mark.asyncio
async def test_aparelho_sem_perfil_vinculado_nao_tem_sessao_a_desmentir(tmp_path: Path) -> None:
    """O QA Messenger e o caminho antigo seguem iguais: a mesma tela de senha ali não fala de Instagram nenhum."""
    h = Harness(tmp_path, 2)
    await h.boot()
    try:
        s = h.state
        pid = _perfil(h, "android-01")
        s.social_repo.set_session(pid, status=SessionStatus.session_ready, instance_id="android-01",
                                  verified_at=to_iso(now()))
        s._sessao_desmentida("android-02", "auth_required", "campo de senha na tela")
        assert s.social_repo.session_row(pid)["status"] == SessionStatus.session_ready.value
    finally:
        await h.state.stop()


# ---------------------------------------------------------------- validade
@pytest.mark.asyncio
async def test_sessao_vencida_reobserva_antes_da_tarefa(tmp_path: Path) -> None:
    h = Harness(tmp_path, 2)
    await h.boot()
    try:
        s = h.state
        rt = s.devices.get("android-01")
        pid = _perfil(h, "android-01")
        limite = s.cfg.file.contas.session_max_age_s
        assert limite > 0

        # Verificada agora: a porta libera sem falar com o aparelho.
        s.social_repo.set_session(pid, status=SessionStatus.session_ready, instance_id="android-01",
                                  verified_at=to_iso(now()))
        assert s._session_gate(rt) is None
        assert s.social_repo.profile_dto(pid).session.stale is False

        # Verificada há tempo demais — a idade exata dos perfis do parque em 21/09.
        s.social_repo.set_session(pid, status=SessionStatus.session_ready, instance_id="android-01",
                                  verified_at=to_iso(now() - timedelta(seconds=limite + 60)))
        porta = s._session_gate(rt)
        assert porta is not None
        motivo, trabalho = porta
        assert "validade" in motivo and trabalho is not None     # relê o aparelho, não é "deslogado"
        assert s.social_repo.profile_dto(pid).session.stale is True

        # E o que a releitura faz é OBSERVAR: nunca uma tentativa de login por conta própria.
        chamadas: list[dict[str, Any]] = []

        async def _ensure(rt_: Any, profile_id: str, **kw: Any) -> str:
            chamadas.append(kw)
            return "ok"

        s.instagram.ensure_session = _ensure  # type: ignore[assignment]
        await s._session_gate(rt)[1]()
        assert chamadas == [{"observe_only": True}]
    finally:
        await h.state.stop()


@pytest.mark.asyncio
async def test_so_session_ready_envelhece(tmp_path: Path) -> None:
    """`unknown`/`auth_required` já dizem por si que a sessão não vale: marcá-los de velhos seria dizer duas vezes."""
    h = Harness(tmp_path, 1)
    await h.boot()
    try:
        s = h.state
        pid = _perfil(h, "android-01")
        s.social_repo.set_session(pid, status=SessionStatus.auth_required, instance_id="android-01", verified_at=None)
        assert s.social_repo.profile_dto(pid).session.stale is False
        # `session_ready` sem data nenhuma, por outro lado, é o pior caso: afirmação sem observação registrada.
        s.social_repo.set_session(pid, status=SessionStatus.session_ready, instance_id="android-01", verified_at=None)
        assert s.social_repo.profile_dto(pid).session.stale is True
    finally:
        await h.state.stop()


@pytest.mark.asyncio
async def test_desafio_visto_na_execucao_bloqueia_o_perfil_uma_vez(tmp_path: Path) -> None:
    """ADR-029: a tela contradizendo a sessão NO MEIO de uma execução (`_sessao_desmentida`) é o mesmo aviso de
    conta travada — o perfil vai a `blocked` na entrada do estado, e confirmar o mesmo desafio não repete o aviso."""
    h = Harness(tmp_path, 2)
    await h.boot()
    try:
        s = h.state
        pid = _perfil(h, "android-01")
        s.social_repo.set_session(pid, status=SessionStatus.session_ready, instance_id="android-01",
                                  verified_at=to_iso(now()))
        s._sessao_desmentida("android-01", "auth_challenge", "Confirm it's you")
        s._sessao_desmentida("android-01", "auth_challenge", "Confirm it's you")
        assert s.social_repo.profile_row(pid)["status"] == "blocked"
        avisos = [r for r in s.db.query("SELECT data FROM events WHERE kind='log' AND message LIKE ?",
                                        ("%bloqueado automaticamente%",))]
        assert len(avisos) == 1
    finally:
        await h.state.stop()
