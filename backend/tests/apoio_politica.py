"""Apoio compartilhado dos testes de política e de porta (31.272, ADR-083). Não é coletado (não começa por `test_`).

Veio de `test_capabilities.py`, `test_protecao_de_frota.py` e `test_repetido_entre_execucoes.py`, que o refactor do dono
de 07/10 renomeou para `_skip_test_*.py` ao tirar tetos, aquecimento e coordenação de frota. Outros 16 módulos importavam
daqui constantes e montagens; sem este módulo a suíte inteira parava na coleta. Os nomes ficam os mesmos para que cada
import só troque de módulo. `_Frota` não tem mais leitor (a `PolicyEngine` guarda o `settings_getter` e não o consulta);
fica para não mudar a montagem dos testes que a usam.
"""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

from app.db import Database
from app.events import EventBus
from app.models import InteractionStatus, InteractionType, ProfileCreate, SocialDraftDTO
from app.planning.provider import Usage
from app.security.secret_store import MemoryKeyProvider, SecretStore
from app.social.policy import PolicyEngine
from app.social.repository import SocialRepository
from app.social.service import SocialService
from app.util import now, to_iso

from .conftest import make_config

SENHA = "$a=B7ee1#<b-C?S-{"
IG = "com.instagram.android"
ALVO = "@anarabottinipsicopedagoga"


def build(tmp_path: Path) -> tuple[SocialService, SocialRepository, PolicyEngine, Database]:
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    # `db_dsn` e nao `db_path`: assim o teste segue `TEST_DATABASE_URL` e roda de verdade no PostgreSQL
    # quando a suite e apontada para la. Com `db_path` ele abriria SQLite mesmo dentro da corrida do
    # outro banco — cobertura que parece existir e nao existe.
    db = Database(cfg.db_dsn)
    db.migrate()
    repo = SocialRepository(db)
    svc = SocialService(repo, SecretStore(db, MemoryKeyProvider()), EventBus(db),
                        known_instances=lambda: ["android-01", "android-02"])
    return svc, repo, PolicyEngine(repo), db


def perfil(svc: SocialService, username: str = "luciana.bastos73519", instance: str = "android-02") -> str:
    return svc.create_profile(ProfileCreate(username=username, password=SENHA, instance_id=instance)).id


class _Frota:
    """Os limites de frota como `LimitsCfg` os expõe (só o que a porta lê)."""

    def __init__(self, **over: Any):
        self.fleet_max_accounts_per_target = over.get("curtidas", 3)
        self.fleet_target_window_days = over.get("dias", 30)
        self.fleet_target_window_s = 3600
        self.fleet_min_spacing_between_accounts_s = over.get("espaco_s", 0)
        self.fleet_spacing_jitter_s = over.get("jitter_s", 0)


def _frota(tmp_path: Path, **over: Any) -> tuple[SocialService, SocialRepository, PolicyEngine, dict[str, str]]:
    svc, repo, _pol, _db = build(tmp_path)
    contas = {"tadeu": perfil(svc, "tadeu.quintela4821", "android-01"),
              "luciana": perfil(svc, "luciana.bastos73519", "android-02")}
    for pid in contas.values():
        # sem aquecimento e sem intervalo entre ações: o que se mede aqui é só a regra de frota
        repo.update_profile(pid, {"automation_policy": '{"limits": {"warmup_days": 0, '
                                                       '"cooldown_between_external_actions_s": 0}}'})
    return svc, repo, PolicyEngine(repo, lambda: _Frota(**over)), contas


def _fez(svc: SocialService, pid: str, tipo: InteractionType, alvo: str = ALVO, *, dias_atras: int = 0,
         status: str = InteractionStatus.confirmed.value) -> None:
    iid = svc.record_interaction(pid, type=tipo.value, direction="outbound", status=status, counterparty=alvo,
                                 run_id="run-antiga").id
    if dias_atras:
        svc.repo.update_interaction(pid, iid, occurred_at=to_iso(now() - timedelta(days=dias_atras)))


class _ProvedorQueAtribui:
    """Escreve a frase do incidente; só reescreve se o pedido disser que a anterior atribuía fala a terceiro."""

    def __init__(self, corrige: bool):
        self.corrige = corrige
        self.pedidos: list[Any] = []

    async def generate_social_response(self, req: Any) -> tuple[SocialDraftDTO, Usage]:
        self.pedidos.append(req)
        if req.attribution_retry and self.corrige:
            return SocialDraftDTO(content="Oi, Ana! Passando só pra dar um oi 🤍", rationale="ok"), Usage(role="social")
        return SocialDraftDTO(content="Oi, seu marido mandou um oi aqui pra você", rationale="ok"), Usage(role="social")


def _execucao_em_duas_contas(state: Any, acao: str, bindings: dict[str, str]) -> dict[str, str]:
    db = state.db
    db.execute("INSERT INTO apps(id, name, package, activity, builtin) VALUES ('ig','Instagram',?,NULL,0)", (IG,))
    db.execute("UPDATE instances SET app_id='ig' WHERE id IN ('android-01','android-02')")
    pids = {"android-01": state.social.create_profile(ProfileCreate(username="tadeu.quintela4821", password=SENHA,
                                                                    instance_id="android-01")).id,
            "android-02": state.social.create_profile(ProfileCreate(username="luciana.bastos73519", password=SENHA,
                                                                    instance_id="android-02")).id}
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at)"
               " VALUES ('run-f','kf','x','execute','running',1,'[\"android-01\",\"android-02\"]',"
               "'2026-09-29T10:00:00Z')")
    for iid, pid in pids.items():
        state.social_repo.update_profile(pid, {"automation_policy": '{"limits": {"warmup_days": 0, '
                                                                    '"cooldown_between_external_actions_s": 0}}'})
        db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, parameters, profile_id)"
                   " VALUES (?,'run-f',?,'running',1,'{}',?)", (f"run-f:{iid}", iid, pid))
        db.execute(
            "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal, depends_on,"
            " side_effect, commit_guard, postcondition, timeout_s, max_attempts, status, capability, commit_selector,"
            " bindings) VALUES (?,'run-f',?,?,1,1,'efeito','Efeito','efeito','[]',1,'[]',"
            "'{\"kind\":\"model_judged\",\"value\":\"x\",\"description\":\"y\"}',180,1,'ready',?,'text=X',?)",
            (f"run-f:{iid}:v1:efeito", f"run-f:{iid}", iid, acao, json.dumps(bindings)))
    return pids


async def _porta(state: Any, iid: str) -> Any:
    db = state.db
    return await state._policy_gate(db.one("SELECT * FROM objectives WHERE id=?", (f"run-f:{iid}",)),  # noqa: SLF001
                                    db.one("SELECT * FROM steps WHERE id=?", (f"run-f:{iid}:v1:efeito",)),
                                    db.one("SELECT * FROM runs WHERE id='run-f'"))


_SERVICOS: dict[str, Any] = {}                 # perfil → o SocialService que o criou (para mudar a política dele)


def _conta(tmp_path: Path) -> tuple[Any, PolicyEngine, Any, str]:
    _svc, repo, _pol, db = build(tmp_path)
    pid = perfil(_svc, "tadeu.quintela4821", "android-01")
    _SERVICOS[pid] = _svc
    repo.update_profile(pid, {"automation_policy": '{"limits": {"warmup_days": 0, '
                                                   '"cooldown_between_external_actions_s": 0}}'})
    return repo, PolicyEngine(repo, lambda: _Frota()), db, pid


def _etapa(db: Any, run: str, acao: str, argumentos: dict[str, str], *, objetivo: str = "running") -> str:
    """Uma etapa de outra execução, com os argumentos que dizem o objeto."""
    oid, sid = f"{run}:android-01", f"{run}:android-01:v1:efeito"
    if db.scalar("SELECT 1 FROM runs WHERE id=?", (run,)) is None:
        db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at)"
                   " VALUES (?,?,'x','execute','running',1,'[]','2026-10-04T10:00:00Z')", (run, f"k-{run}"))
        db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, parameters)"
                   " VALUES (?,?,'android-01',?,1,'{}')", (oid, run, objetivo))
    db.execute(
        "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal, depends_on,"
        " side_effect, commit_guard, postcondition, timeout_s, max_attempts, status, capability, bindings)"
        " VALUES (?,?,?,'android-01',1,1,'efeito','Efeito','efeito','[]',1,'[]',"
        "'{\"kind\":\"model_judged\",\"value\":\"x\",\"description\":\"y\"}',180,1,'ready',?,?)",
        (sid, run, oid, acao, json.dumps(argumentos)))
    return sid
