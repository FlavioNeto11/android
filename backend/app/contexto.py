"""Contexto operacional de um aparelho: Servidor → Aparelho → Apps → Perfil/Conta → Sessão, numa resposta só.

Existe porque, para saber por que o android-06 não entrava no Instagram, era preciso cruzar quatro telas (Parque,
Aplicativos, Perfis, Servidores) e cada uma mostrava UMA camada. Aqui cada camada vem com a sua própria fonte e
nenhuma é deduzida da outra: aparelho online não diz nada da tela ao vivo, app instalado não diz nada da sessão,
perfil vinculado não diz nada do app. Só leitura: não toca no aparelho, não gasta IA.
"""
from __future__ import annotations

from typing import Any

from .social.sessao_gate import APP_AUSENTE, APP_EM_CURSO, APP_PRESENTE


def _app_state_resumo(state: str | None) -> str:
    """O estado do app numa palavra que a tela não precisa reinterpretar."""
    if state is None:
        return "unknown"
    if state in APP_PRESENTE:
        return "installed"
    if state in APP_EM_CURSO:
        return "in_progress"
    if state in APP_AUSENTE:
        return "absent"
    return "unknown"


def contexto_do_aparelho(s: Any, instance_id: str) -> dict[str, Any]:
    """Monta o contexto. `KeyError` quando o aparelho não existe (a rota vira 404)."""
    rt = s.devices.get(instance_id)
    dto = s.devices.dto(rt)

    servidor: dict[str, Any] | None = None
    if rt.worker_id:
        linha = next((w for w in s.workers.dtos() if w.id == rt.worker_id), None)
        servidor = ({"id": linha.id, "name": linha.name, "local": linha.local, "connected": linha.connected,
                     "state": linha.state, "transport_state": linha.transport_state, "verbs": linha.verbs}
                    if linha else {"id": rt.worker_id, "name": rt.worker_id, "local": False, "connected": False,
                                   "state": "unknown", "transport_state": None, "verbs": []})

    # Apps: o registro de apps (o que o parque conhece) cruzado com o que ESTE aparelho observou. Um app sem linha
    # em `device_app_state` é `unknown` — nunca inspecionado —, e não "ausente".
    observados = {r["package_name"]: r for r in s.db.query("SELECT * FROM device_app_state WHERE instance_id=?",
                                                           (instance_id,))}
    apps = []
    for a in s.db.query("SELECT id, name, package FROM apps ORDER BY builtin DESC, name"):
        obs = observados.get(a["package"])
        promovida = s.releases.promoted_release(a["package"])
        apps.append({
            "app_id": a["id"], "name": a["name"], "package": a["package"],
            "presence": _app_state_resumo(obs["state"] if obs else None),
            "state": obs["state"] if obs else None,
            "installed_version_name": obs["observed_version_name"] if obs else None,
            "installed_version_code": obs["observed_version_code"] if obs else None,
            "verified_at": obs["verified_at"] if obs else None,
            "pending_op": obs["pending_op"] if obs else None,
            "detail": obs["detail"] if obs else None,
            "promoted_release_id": promovida.id if promovida else None,
            "promoted_version_name": promovida.version_name if promovida else None,
            "promoted_version_code": promovida.version_code if promovida else None,
        })

    perfis = []
    # TODAS as personas vinculadas ao aparelho (N:N, design §3.1): cada uma com as suas contas e a sessão de cada
    # conta NESTE aparelho. `session`/`app_on_device` do DTO falam do aparelho PRINCIPAL da persona, que pode ser
    # outro; a verdade por aparelho está em `accounts[].sessions`.
    for vinculo in s.social_repo.profiles_of_instance(instance_id):
        pid = str(vinculo["profile_id"])
        perfil = s.social_repo.profile_dto(pid)
        if perfil is None:
            continue
        # Só o que a tela precisa. `credential` vai só como "configurada" + estado: nunca o identificador de login
        # nem nada que venha do cofre.
        perfis.append({
            "profile_id": perfil.id, "username": perfil.username, "display_name": perfil.display_name,
            "persona_id": perfil.persona_id, "persona_name": perfil.persona_name, "has_avatar": perfil.has_avatar,
            "credential_configured": perfil.credential.configured,
            "credential_status": perfil.credential.status,
            "session": perfil.session.model_dump(mode="json"),
            "app_on_device": perfil.app_on_device.model_dump(mode="json") if perfil.app_on_device else None,
            "session_actions": perfil.session_actions.model_dump(mode="json") if perfil.session_actions else None,
            # As CONTAS do perfil (ADR-040), cada uma com a credencial (só metadados), o consentimento e a sessão
            # neste aparelho: é a camada "Perfil/Conta → Sessão" por app, não só a do app âncora.
            "accounts": [c.model_dump(mode="json") for c in s.social.list_accounts(pid)],
        })

    return {
        "instance_id": instance_id,
        "server": servidor,
        "device": {"state": dto.state, "state_detail": dto.state_detail, "kind": dto.kind,
                   "supported_verbs": dto.supported_verbs, "automation": dto.automation.model_dump(mode="json"),
                   "attention": dto.attention},
        "stream": dto.stream.model_dump(mode="json") if dto.stream else None,
        "connectivity": dto.connectivity.model_dump(mode="json"),
        "readiness": dto.readiness.model_dump(mode="json"),
        "apps": apps,
        "profiles": perfis,
    }
