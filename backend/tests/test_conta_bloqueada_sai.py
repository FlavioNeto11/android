"""Conta bloqueada sai na hora e a persona fica (item 29.23, ADR-068; decisão do dono de 02/10/2026).

Bloqueio confirmado (tela "Confirm you're human" lida, ou o dono declara): a conta some de listagens, roteamento e
contagens; a credencial sai do cofre (inclusive o ciphertext que a linha LEGADA `instagram_credentials` segurava); a
sessão da conta e o vínculo de aparelho dela encerram; a persona CONTINUA, sem @, de volta a `active`. O produto só
guarda o hash do @ (`contas_retiradas`, 071) para nunca tratar a conta antiga como terceira (ADR-050).

Nível de prova: `simulated` (banco de teste, cofre em memória, Harness com aparelho falso). Nada real.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import pytest

from app.models import RunCreate, SessionStatus
from app.modules.identity.presentation.schemas import PersonaDeviceBody
from app.planning.capabilities import capability_of
from app.social.contas_nossas import MARCADOR, eh_conta_nossa, hash_do_handle, sem_o_rastro
from app.social.policy import PolicyEngine
from app.util import now_iso

from .conftest import COMMAND, Harness
from .fake_instagram import PKG as IG_PKG
from .test_capabilities import IG, build, perfil
from .test_protecao_de_frota import _Frota
from .test_sessao_por_conta import IID, correio_registrado, estado, persona_com_duas_contas

__all__ = ["correio_registrado"]

#: A janela que o `app.yaml` do Instagram declara como conta perdida (`atividades_de_conta_perdida`), como o dumpsys a dá.
ATIVIDADE = "com.instagram.challenge.activity.ChallengeActivity"
FELIPE = "felipe.teste01"
LUCAS = "lucas.teste02"

#: Tabelas FORA da varredura do "o @ não sobra": o histórico de execução fica intacto por decisão do dono (opção A do
#: ADR-068: events, runs, steps, pending_approvals, social_interactions, ai_calls podem citar o @ em texto, e as provas
#: antigas seguem legíveis); `device_locked_accounts` é o marcador de quarentena DO APARELHO (a conta segue logada
#: lá até alguém limpar o app); `contas_retiradas` guarda só o hash.
FORA_DA_VARREDURA = {"events", "runs", "steps", "pending_approvals", "social_interactions", "ai_calls",
                     "device_locked_accounts", "contas_retiradas"}


def _montar(tmp_path: Path) -> tuple[Any, Any, Any, str, str]:
    svc, repo, _pol, db = build(tmp_path)
    return svc, repo, db, perfil(svc, FELIPE, "android-01"), perfil(svc, LUCAS, "android-02")


def _ancora(repo: Any, pid: str) -> str:
    return str(repo.conta_ancora(pid)["id"])


def _legada(db: Any, repo: Any, pid: str) -> str:
    """A linha LEGADA apontando para a MESMA referência no cofre: era ela que prendia o ciphertext."""
    ref = str(repo.credential_row(pid)["secret_ref"])
    db.execute("INSERT INTO instagram_credentials(profile_id, login_identifier, secret_ref, key_id, status,"
               " failed_attempts, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
               (pid, FELIPE, ref, "k", "active", 0, now_iso(), now_iso()))
    return ref


def _memoria(db: Any, pid: str, assunto: str, conteudo: str, fp: str) -> None:
    db.execute("INSERT INTO memory_items(id, profile_id, subject, content, source, fingerprint, created_at,"
               " updated_at) VALUES (?,?,?,?,?,?,?,?)",
               (f"m-{fp}", pid, assunto, conteudo, "operator", fp, now_iso(), now_iso()))


def _eventos(db: Any, tipo: str) -> list[dict[str, Any]]:
    return [{"message": r["message"], "data": json.loads(r["data"] or "{}")}
            for r in db.query("SELECT message, data FROM events WHERE kind=? ORDER BY id", (tipo,))]


def _texto_em_tabelas(db: Any, trecho: str) -> list[str]:
    """Tabelas (fora da lista de exclusão) com o trecho em ALGUMA coluna de texto. Só SQLite: lê o esquema dele."""
    if db.dialect != "sqlite":
        pytest.skip("a varredura de esquema lê sqlite_master")
    achadas: list[str] = []
    for t in db.query("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                      " AND name NOT LIKE 'memory_fts%'"):
        nome = str(t["name"])
        if nome in FORA_DA_VARREDURA:
            continue
        for col in db.query(f"PRAGMA table_info({nome})"):
            if db.scalar(f"SELECT COUNT(*) FROM {nome} WHERE lower(CAST({col['name']} AS TEXT)) LIKE ?",
                         (f"%{trecho.lower()}%",)):
                achadas.append(f"{nome}.{col['name']}")
    return achadas


# ============================================================ a retirada
def test_retirada_apaga_credencial_conta_legada_e_o_ciphertext_e_a_persona_fica(tmp_path: Path) -> None:
    svc, repo, db, pid, outro = _montar(tmp_path)
    conta = _ancora(repo, pid)
    ref = _legada(db, repo, pid)
    repo.set_account_session(pid, conta, "android-01", status=SessionStatus.session_ready, verified_at=now_iso())
    repo.mudar_status(pid, "blocked", origem="observado", autor="teste", evidencia="tela de verificação")
    _memoria(db, pid, f"@{FELIPE.upper()}", f"{FELIPE} gosta de café; falou com a conta {conta}", "a")
    _memoria(db, pid, "Ana", "treina para a maratona", "b")
    assert svc.secrets.exists(ref) and svc.instance_of(pid) == "android-01"
    chamadas: list[tuple[str, bool]] = []
    svc.ao_retirar_conta = lambda p, c, estava: chamadas.append((c, estava))

    r = svc.retirar_conta_bloqueada(pid, conta, origem="declarado", autor="dono", evidencia=f"vi @{FELIPE} travada")

    assert r["retirada"] and r["ancora"] and r["status_da_persona"] == "active"
    assert not svc.secrets.exists(ref)                                  # o CIPHERTEXT saiu do cofre
    assert not db.scalar("SELECT COUNT(*) FROM account_credentials WHERE account_id=?", (conta,))
    assert not db.scalar("SELECT COUNT(*) FROM instagram_credentials WHERE profile_id=?", (pid,))
    assert not db.scalar("SELECT COUNT(*) FROM account_sessions WHERE account_id=?", (conta,))
    assert repo.account_row(pid, conta) is None
    # A persona fica: viva, sem @, ativa; a outra não foi tocada.
    assert pid in repo.list_persona_ids() and pid not in repo.list_profile_ids() and outro in repo.list_profile_ids()
    linha = repo.profile_row(pid)
    assert linha["username"] == "" and linha["status"] == "active" and linha["blocked_at"] is None
    assert svc.get_profile(outro).username == LUCAS
    assert svc.repo.bindings_of_profile(pid) == []                      # o app do aparelho tem a conta bloqueada logada
    assert chamadas == [(conta, True)]                                  # o disjuntor de conta é acionado na hora
    assert [e["data"]["status"] for e in _eventos(db, "profile.status")][-1] == "active"
    # A memória FICA (a linha não se apaga) sem o @ nem o id; a outra lembrança, intacta.
    mem = {m["id"]: (m["subject"], m["content"]) for m in db.query("SELECT id, subject, content FROM memory_items")}
    assert mem["m-a"] == (MARCADOR, f"{MARCADOR} gosta de café; falou com a conta {MARCADOR}")
    assert mem["m-b"] == ("Ana", "treina para a maratona")
    ev = _eventos(db, "profile.account_retired")
    assert len(ev) == 1 and ev[0]["data"]["limpezas"] == {"memory_items": 1}
    assert FELIPE not in json.dumps(ev[0]).lower()                      # o @ não vai no evento
    assert ev[0]["data"]["account_id"] == conta                        # o id é a lápide, legível nas provas


def test_o_texto_do_arroba_nao_sobra_e_so_o_hash_fica_na_lapide(tmp_path: Path) -> None:
    svc, repo, db, pid, _ = _montar(tmp_path)
    conta = _ancora(repo, pid)
    _legada(db, repo, pid)
    _memoria(db, pid, FELIPE, f"visto em @{FELIPE}", "a")
    assert _texto_em_tabelas(db, FELIPE), "sanidade: antes da retirada o @ está nas tabelas"
    svc.retirar_conta_bloqueada(pid, conta, autor="dono")
    assert _texto_em_tabelas(db, FELIPE) == []
    assert _texto_em_tabelas(db, conta) == []
    esperado = hash_do_handle(f"@{FELIPE}")
    assert esperado == hash_do_handle(f"  {FELIPE.upper()} ")                # normalizado: caixa, @ e espaços
    assert [(r["handle_sha256"], r["profile_id"]) for r in db.query("SELECT * FROM contas_retiradas")] == [
        (esperado, pid)]


def test_retirada_e_idempotente(tmp_path: Path) -> None:
    svc, repo, db, pid, _ = _montar(tmp_path)
    conta = _ancora(repo, pid)
    assert svc.retirar_conta_bloqueada(pid, conta)["retirada"] is True
    segunda = svc.retirar_conta_bloqueada(pid, conta)
    assert segunda["retirada"] is False and segunda["status_da_persona"] == "active"
    assert db.scalar("SELECT COUNT(*) FROM contas_retiradas") == 1
    assert len(_eventos(db, "profile.account_retired")) == 1


def test_persona_pausada_pelo_dono_continua_pausada(tmp_path: Path) -> None:
    svc, repo, _db, pid, _ = _montar(tmp_path)
    repo.mudar_status(pid, "disabled", origem="declarado", autor="dono")
    svc.retirar_conta_bloqueada(pid, _ancora(repo, pid))
    assert repo.profile_row(pid)["status"] == "disabled"               # a pausa é decisão dele


def test_persona_inexistente_e_404(tmp_path: Path) -> None:
    from app.social.service import SocialError
    svc, *_ = _montar(tmp_path)
    with pytest.raises(SocialError) as exc:
        svc.retirar_conta_bloqueada("ig-nao-existe", "acc-x")
    assert exc.value.status == 404


# ============================================================ a lápide: conta nossa nunca é terceiro
def test_eh_conta_nossa_vale_para_viva_e_para_a_retirada_e_o_filtro_de_terceiro_exclui(tmp_path: Path) -> None:
    svc, repo, db, pid, outro = _montar(tmp_path)
    policies = PolicyEngine(repo, lambda: _Frota(curtidas=3))
    follow = capability_of(IG, "FOLLOW")
    assert eh_conta_nossa(db, FELIPE) and eh_conta_nossa(db, f"@{LUCAS.upper()}")        # vivas
    assert not eh_conta_nossa(db, "alguem.de.fora") and not eh_conta_nossa(db, "") and not eh_conta_nossa(db, None)
    # Viva: a persona do Lucas não age sobre uma conta da própria frota.
    antes = policies.check(outro, follow, counterparty=f"@{FELIPE}")
    assert not antes.allowed and antes.retry_at is None and "frota" in antes.reason
    assert policies.check(outro, follow, counterparty="@alguem.de.fora").allowed

    svc.retirar_conta_bloqueada(pid, _ancora(repo, pid))

    assert eh_conta_nossa(db, FELIPE) and eh_conta_nossa(db, f"@{FELIPE}") and eh_conta_nossa(db, FELIPE.upper())
    depois = policies.check(outro, follow, counterparty=f"@{FELIPE.title()}")             # o filtro real do produto
    assert not depois.allowed and "frota" in depois.reason
    assert policies.check(outro, follow, counterparty="@alguem.de.fora").allowed


def test_sem_o_rastro_so_troca_o_arroba_inteiro_com_ou_sem_marca() -> None:
    assert sem_o_rastro("fale com @Ana, a ana.silva e a banana; ANA!", "ana", None) == (
        f"fale com {MARCADOR}, a ana.silva e a banana; {MARCADOR}!")
    assert sem_o_rastro(None, "ana", None) is None and sem_o_rastro("", "ana", None) == ""
    assert sem_o_rastro("sem nada", "", None) == "sem nada"


# ============================================================ o gancho de limpezas dos outros módulos
def test_limpeza_registrada_e_chamada_com_os_argumentos_e_as_contagens_entram_no_evento(tmp_path: Path) -> None:
    svc, repo, db, pid, _ = _montar(tmp_path)
    conta = _ancora(repo, pid)
    vistas: list[dict[str, Any]] = []

    def limpeza(banco: Any, **kw: Any) -> dict[str, int]:
        assert banco is db
        vistas.append(kw)
        return {"rastro_do_aprendizado": 3}

    svc.limpezas_ao_retirar.append(limpeza)
    svc.limpezas_ao_retirar.append(lambda banco, **kw: {"rastro_do_aprendizado": 2, "outra": 1})
    r = svc.retirar_conta_bloqueada(pid, conta)
    assert len(vistas) == 1 and set(vistas[0]) == {"profile_id", "account_id", "handle", "app_id"}
    assert vistas[0]["profile_id"] == pid and vistas[0]["account_id"] == conta and vistas[0]["handle"] == FELIPE
    assert r["limpezas"] == {"rastro_do_aprendizado": 5, "outra": 1, "memory_items": 0}
    assert _eventos(db, "profile.account_retired")[0]["data"]["limpezas"] == r["limpezas"]


def test_limpeza_que_levanta_erro_desfaz_a_retirada_inteira(tmp_path: Path) -> None:
    svc, repo, db, pid, _ = _montar(tmp_path)
    conta = _ancora(repo, pid)
    ref = _legada(db, repo, pid)
    repo.mudar_status(pid, "blocked", origem="observado", autor="teste")
    _memoria(db, pid, FELIPE, f"visto em @{FELIPE}", "a")

    def quebra(banco: Any, **kw: Any) -> dict[str, int]:
        raise RuntimeError("limpeza com defeito")

    svc.limpezas_ao_retirar.append(quebra)
    with pytest.raises(RuntimeError):
        svc.retirar_conta_bloqueada(pid, conta)
    # Nada pela metade: credencial, legada, cofre, conta, @, status, memória e lápide como estavam.
    assert svc.secrets.exists(ref)
    assert db.scalar("SELECT COUNT(*) FROM account_credentials WHERE account_id=?", (conta,)) == 1
    assert db.scalar("SELECT COUNT(*) FROM instagram_credentials WHERE profile_id=?", (pid,)) == 1
    assert repo.account_row(pid, conta) is not None
    assert repo.profile_row(pid)["username"] == FELIPE and repo.profile_row(pid)["status"] == "blocked"
    assert db.scalar("SELECT content FROM memory_items WHERE id='m-a'") == f"visto em @{FELIPE}"
    assert db.scalar("SELECT COUNT(*) FROM contas_retiradas") == 0 and not repo.bindings_of_profile(pid) == []
    assert _eventos(db, "profile.account_retired") == []


# ============================================================ conta de OUTRO app: só ela sai
async def test_conta_de_outro_app_sai_sem_tocar_a_ancora_nem_o_status(harness: Harness,
                                                                      correio_registrado: None) -> None:
    s = estado(harness)
    pid, ancora, correio = persona_com_duas_contas(s)
    ref_correio = str(s.social_repo.account_credential_row(pid, correio)["secret_ref"])
    r = s.social.retirar_conta_bloqueada(pid, correio, autor="dono")
    assert r["retirada"] and not r["ancora"]
    assert s.social_repo.account_row(pid, correio) is None and s.social_repo.account_row(pid, ancora) is not None
    assert not s.secrets.exists(ref_correio)
    assert s.social_repo.account_credential_row(pid, ancora) is not None
    linha = s.social_repo.profile_row(pid)
    assert linha["username"] == "ana.ancora" and linha["status"] == "active"
    assert eh_conta_nossa(s.db, "ana.correio")


# ============================================================ gatilho automático (só Instagram, só com sinal forte)
def _atividade_em_foco(s: Any, valor: tuple[str, float] | None = None) -> None:
    """O sinal forte: o que `DeviceManager.observe` guarda quando a ChallengeActivity está em foco."""
    s.devices.devices[IID].atividade_de_desafio = valor or (ATIVIDADE, time.monotonic())


async def test_so_o_texto_da_tela_nao_retira_a_conta_fica_bloqueada_para_a_pessoa(
        harness: Harness, correio_registrado: None) -> None:
    s = estado(harness)
    pid, ancora, _ = persona_com_duas_contas(s)
    s.social_repo.set_account_session(pid, ancora, IID, status=SessionStatus.session_ready, verified_at=now_iso())
    # Sem a atividade de desafio em foco (ou com outra atividade, ou com a leitura velha): só o texto.
    for visto in (None, ("com.instagram.android.activity.MainTabActivity", time.monotonic()),
                  (ATIVIDADE, time.monotonic() - 10_000)):
        s.devices.devices[IID].atividade_de_desafio = visto
        s._sessao_desmentida(IID, "auth_challenge", "confirm you're human", subtipo="conta_travada", package=IG_PKG)
        linha = s.social_repo.profile_row(pid)
        assert linha["status"] == "blocked" and linha["username"] == "ana.ancora"          # como antes: pessoa decide
        assert s.social_repo.account_row(pid, ancora) is not None
    assert _eventos(s.db, "profile.account_retired") == []
    assert any("sem a atividade de desafio" in e["message"] for e in _eventos(s.db, "log"))


async def test_atividade_de_desafio_em_foco_retira_e_a_leitura_do_foco_a_produz(
        harness: Harness, correio_registrado: None, monkeypatch: pytest.MonkeyPatch) -> None:
    s = estado(harness)
    rt = s.devices.devices[IID]
    # A leitura do foco (o que `observe` faz ao ver a árvore de conta travada): só a ChallengeActivity vale.
    # Só a janela que o PRÓPRIO app declara (`atividades_de_conta_perdida`),
    # e a mesma atividade num pacote que não a declarou (o correio de exemplo) NÃO vale.
    from .test_sessao_declarada import CORREIO
    for foco, esperado in (((IG_PKG, ATIVIDADE), True),
                           ((IG_PKG, "com.instagram.mainactivity.MainActivity"), False),
                           ((CORREIO, ATIVIDADE), False), ((None, None), False)):
        monkeypatch.setattr(rt.io, "current_focus", lambda f=foco: f)
        await s.devices._ler_atividade_de_desafio(rt)
        assert s.devices.tem_atividade_de_desafio(IID) is esperado

    def quebra() -> tuple[str, str]:
        raise RuntimeError("adb fora")

    monkeypatch.setattr(rt.io, "current_focus", quebra)
    await s.devices._ler_atividade_de_desafio(rt)
    assert s.devices.tem_atividade_de_desafio(IID) is False                   # sem leitura, sem sinal forte
    # E o gatilho enxerga a leitura: dois sinais (o texto classificado + a atividade) retiram.
    pid, ancora, _ = persona_com_duas_contas(s)
    monkeypatch.setattr(rt.io, "current_focus", lambda: (IG_PKG, ATIVIDADE))
    await s.devices._ler_atividade_de_desafio(rt)
    s._sessao_desmentida(IID, "auth_challenge", "confirm you're human", subtipo="conta_travada", package=IG_PKG)
    assert s.social_repo.account_row(pid, ancora) is None and s.social_repo.profile_row(pid)["status"] == "active"


async def test_gatilho_a_tela_de_verificacao_na_ancora_retira_a_conta_e_a_persona_volta_a_active(
        harness: Harness, correio_registrado: None) -> None:
    s = estado(harness)
    _atividade_em_foco(s)
    pid, ancora, correio = persona_com_duas_contas(s)
    for c in (ancora, correio):
        s.social_repo.set_account_session(pid, c, IID, status=SessionStatus.session_ready, verified_at=now_iso())
    ref = str(s.social_repo.account_credential_row(pid, ancora)["secret_ref"])
    s.db.execute("INSERT INTO instagram_credentials(profile_id, login_identifier, secret_ref, key_id, status,"
                 " failed_attempts, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                 (pid, "ana.ancora", ref, "k", "active", 0, now_iso(), now_iso()))

    # Percorre a regra inteira (`aplicar_desafio`: bloqueia, marca a quarentena, retira) — não só `mudar_status`.
    s._sessao_desmentida(IID, "auth_challenge", "confirm you're human", subtipo="conta_travada", package=IG_PKG)

    linha = s.social_repo.profile_row(pid)
    assert linha["status"] == "active" and linha["username"] == ""            # NÃO re-bloqueia depois da retirada
    assert s.social_repo.account_row(pid, ancora) is None and s.social_repo.account_row(pid, correio) is not None
    assert not s.secrets.exists(ref)
    assert pid in s.social_repo.list_persona_ids() and pid not in s.social_repo.list_profile_ids()
    # A quarentena do APARELHO fica (a conta segue logada lá): o marcador não é apagado pela retirada, mas o @ sai
    # dele (29.24: o produto vivo não mostra o @ de conta retirada).
    assert [m["handle"] for m in s.social_repo.contas_travadas_abertas()] == ["[conta removida]"]
    assert len(_eventos(s.db, "profile.account_retired")) == 1
    assert "ana.ancora" not in json.dumps(_eventos(s.db, "profile.account_retired"))
    assert eh_conta_nossa(s.db, "ana.ancora")
    # Detectar de novo (outra leitura da mesma tela) não reabre nada nem quebra.
    s._sessao_desmentida(IID, "auth_challenge", "confirm you're human", subtipo="conta_travada", package=IG_PKG)
    assert s.social_repo.profile_row(pid)["status"] == "active"


async def test_gatilho_com_limpeza_defeituosa_deixa_a_persona_bloqueada_e_a_conta_intacta(
        harness: Harness, correio_registrado: None) -> None:
    s = estado(harness)
    _atividade_em_foco(s)
    pid, ancora, _ = persona_com_duas_contas(s)
    s.social_repo.set_account_session(pid, ancora, IID, status=SessionStatus.session_ready, verified_at=now_iso())

    def quebra(banco: Any, **kw: Any) -> dict[str, int]:
        raise RuntimeError("defeito")

    s.social.limpezas_ao_retirar.append(quebra)
    s._sessao_desmentida(IID, "auth_challenge", "confirm you're human", subtipo="conta_travada", package=IG_PKG)

    # O estado seguro: a conta fica como estava e a persona BLOQUEADA, com o erro visível; a rota manual refaz.
    assert s.social_repo.profile_row(pid)["status"] == "blocked"
    assert s.social_repo.account_row(pid, ancora) is not None
    assert any("a retirada falhou" in e["message"] for e in _eventos(s.db, "log"))
    s.social.limpezas_ao_retirar.clear()
    assert s.social.retirar_conta_bloqueada(pid, ancora, origem="declarado", autor="dono")["retirada"] is True
    assert s.social_repo.profile_row(pid)["status"] == "active"


async def test_conta_de_outro_app_nao_e_retirada_sozinha_fica_bloqueada_e_so_a_rota_retira(
        harness: Harness, correio_registrado: None) -> None:
    from .test_sessao_declarada import CORREIO
    s = estado(harness)
    pid, ancora, correio = persona_com_duas_contas(s)
    s.social_repo.set_account_session(pid, correio, IID, status=SessionStatus.session_ready, verified_at=now_iso())
    _atividade_em_foco(s)                    # mesmo com a atividade em foco: a retirada automática é só do Instagram
    s._sessao_desmentida(IID, "auth_challenge", "confirm you're human", subtipo="conta_travada", package=CORREIO)
    assert s.social_repo.account_row(pid, correio) is not None and s.social_repo.account_row(pid, ancora) is not None
    assert s.social_repo.account_credential_row(pid, correio)["status"] == "review"         # para só ela, na fila
    assert s.social_repo.account_session_row(pid, correio, IID)["status"] == "auth_challenge"
    assert [m["handle"] for m in s.social_repo.contas_travadas_abertas()] == ["ana.correio"]  # marcada
    assert s.social_repo.profile_row(pid)["status"] == "active"                             # P9: a persona segue
    assert _eventos(s.db, "profile.account_retired") == []
    # Pela rota manual a conta do outro app sai.
    assert s.social.retirar_conta_bloqueada(pid, correio, origem="declarado", autor="dono")["retirada"] is True
    assert s.social_repo.account_row(pid, correio) is None and s.social_repo.account_row(pid, ancora) is not None


async def test_costura_o_appstate_liga_o_esquecer_conta_do_aprendizado_na_retirada(harness: Harness) -> None:
    """Costura real (contrato combinado com o Aprendizado): o AppState registra `esquecer_conta` no gancho, e a retirada
    tira o @ do Livro na MESMA transação — sem limpeza falsa, pela função de verdade."""
    from app.modules.learning import esquecer_conta
    from .test_learning_esquecer_conta import _item
    s = estado(harness)
    assert esquecer_conta in s.social.limpezas_ao_retirar
    pid, ancora, _ = persona_com_duas_contas(s)
    _item(s.db, "li-costura", content="resposta para @ana.ancora no post", summary="voz de ana.ancora",
          profile=pid)
    res = s.social.retirar_conta_bloqueada(pid, ancora, origem="declarado", autor="dono")
    assert res["retirada"] is True
    linha = s.db.one("SELECT content, summary FROM learning_items WHERE id='li-costura'")
    assert "ana.ancora" not in (linha["content"] + linha["summary"]) and MARCADOR in linha["content"]
    assert res["limpezas"].get("learning_items", 0) >= 1


# ============================================================ a persona sem conta continua roteável
async def test_persona_sem_conta_e_aceita_e_roteada_para_automacao_sem_conta(harness: Harness) -> None:
    s = estado(harness)
    pid = s.social.create_profile(__import__("app.modules.identity.presentation.schemas", fromlist=["ProfileCreate"])
                                  .ProfileCreate(username="beatriz.teste03", password="Senha#Falsa1",
                                                 instance_id="android-02")).id
    s.social.retirar_conta_bloqueada(pid, str(s.social_repo.conta_ancora(pid)["id"]), autor="dono")
    assert s.social_repo.profile_row(pid)["username"] == "" and s.social_repo.bindings_of_profile(pid) == []

    # Sem vínculo, a persona não tem onde agir: a recusa é a de sempre (`no_binding`), não por faltar conta.
    from app.taskqueue.service import RunError
    with pytest.raises(RunError) as exc:
        s.runs.create(RunCreate(command=COMMAND, profile_ids=[pid], mode="plan", idempotency_key="sem-conta-0"))
    assert exc.value.code == "no_binding"

    # Vinculada a um aparelho para um app que NÃO usa conta (o QA Messenger), a execução é aceita e roteada.
    app_sem_conta = str(s.db.scalar("SELECT id FROM apps WHERE package<>? ORDER BY id LIMIT 1",
                                    (IG_PKG,)))
    s.social.bind_device(pid, PersonaDeviceBody(instance_id="android-02", app_id=app_sem_conta))
    run = s.runs.create(RunCreate(command=COMMAND, profile_ids=[pid], mode="plan", idempotency_key="sem-conta-1"))
    await harness.wait_run(run.id, ("planned",))
    assert s.db.scalar("SELECT profile_id FROM objectives WHERE run_id=?", (run.id,)) == pid
    assert s.db.scalar("SELECT instance_id FROM objectives WHERE run_id=?", (run.id,)) == "android-02"


# ============================================================ a rota
async def test_rota_retire_e_idempotente_e_a_ancora_sai(harness: Harness) -> None:
    import httpx
    from app.main import create_app
    from app.modules.identity.presentation.schemas import ProfileCreate
    s = estado(harness)
    pid = s.social.create_profile(ProfileCreate(username="rota.teste04", password="Senha#Falsa1")).id
    conta = str(s.social_repo.conta_ancora(pid)["id"])
    app = create_app(harness.cfg, state=s)
    app.state.poc = s
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        url = f"/api/instagram/profiles/{pid}/accounts/{conta}"
        assert (await c.delete(url)).status_code == 409                       # a remoção comum recusa a âncora
        r = await c.post(f"{url}/retire", json={"evidencia": "vi a tela de verificação"})
        assert r.status_code == 200 and r.json()["retirada"] is True and r.json()["status_da_persona"] == "active"
        r2 = await c.post(f"{url}/retire")                                    # sem corpo; segunda vez
        assert r2.status_code == 200 and r2.json()["retirada"] is False
        assert (await c.post(f"/api/instagram/profiles/ig-nao-existe/accounts/{conta}/retire")).status_code == 404
        lista = (await c.get("/api/instagram/profiles")).json()
        assert pid not in [p["id"] for p in lista]


def test_migracao_071_cria_a_lapide_sem_chave_estrangeira(tmp_path: Path) -> None:
    _svc, _repo, db, _pid, _ = _montar(tmp_path)
    assert db.scalar("SELECT COUNT(*) FROM schema_migrations WHERE version LIKE '071%'") == 1
    db.execute("INSERT INTO contas_retiradas(app_id, handle_sha256, retirada_em, profile_id) VALUES (?,?,?,?)",
               ("instagram", "a" * 64, now_iso(), "perfil-que-nao-existe"))          # sem FK: aceita
    with pytest.raises(Exception):
        db.execute("INSERT INTO contas_retiradas(app_id, handle_sha256, retirada_em) VALUES (?,?,?)",
                   ("instagram", "a" * 64, now_iso()))                               # UNIQUE (app_id, hash)
    assert re.fullmatch(r"[0-9a-f]{64}", hash_do_handle("x"))


def test_responder_a_terceiro_num_post_nosso_segue_permitido_e_conta_nossa_segue_recusada(tmp_path: Path) -> None:
    """O caminho do roteiro 8.3: a conta dona do post responde ao comentário de um TERCEIRO (`counterparty` é o dele)."""
    svc, repo, db, pid, outro = _montar(tmp_path)
    policies = PolicyEngine(repo, lambda: _Frota(curtidas=3))
    responder = capability_of(IG, "REPLY_COMMENT")
    assert policies.check(pid, responder, counterparty="@terceiro.qualquer").allowed
    svc.retirar_conta_bloqueada(outro, _ancora(repo, outro))
    # Conta nossa, viva (felipe) ou aposentada (lucas, retirado agora), continua recusada para qualquer ação com efeito.
    assert not policies.check(pid, responder, counterparty=f"@{LUCAS}").allowed
    assert not policies.check(outro, responder, counterparty=f"@{FELIPE}").allowed
    assert not policies.check(pid, capability_of(IG, "FOLLOW"), counterparty=f"@{FELIPE}").allowed
