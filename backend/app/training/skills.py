"""Da gravação à habilidade (item 13.2): a IA propõe, a pessoa revisa, salvar vira fluxo + receitas com escopo.

- `propose`: manda a gravação (só texto: elemento tocado, tela, texto digitado quando pôde ser guardado) para a
  função de IA `generalize` e guarda a proposta. Custa uma chamada do modelo do planejador; no modo simulado, zero.
- `save`: a proposta (editada ou não pela pessoa) vira
  * um FLUXO — comando com `{parâmetros}` + plano; um comando parecido de qualquer perfil no escopo reaproveita o
    plano sem chamar o planejador;
  * RECEITAS por etapa, destiladas das entradas da pessoa com a mesma identidade que o replay procura (pacote,
    versão e assinatura do app, variante de interface, hash da etapa) — onde não der receita, a IA conduz a etapa;
  * ESCOPO: os perfis e grupos de acesso que recebem a habilidade (vazio = todos).

Guarda de política: num app com catálogo (o Instagram), toda etapa com efeito externo precisa ser uma AÇÃO do
catálogo. Sem isso a habilidade contornaria aprovação, limites e coordenação de frota — o portão só olha etapas
que declaram a ação.
"""
from __future__ import annotations

import re
from typing import Any

from ..db import dumps
from ..models import Plan, PlannerInfo, PlanStep, Postcondition
from ..planning.capabilities import CapabilityNode, load_catalog
from ..planning.training import TrainingRequest
from ..taskqueue.recipes import distill_training, step_template_hash
from ..util import now_iso
from .recorder import TrainingError


class TrainingSkills:
    def __init__(self, state: Any):
        self.s = state

    # ------------------------------------------------------------------ apoio
    def _apps(self) -> dict[str, dict[str, str]]:
        return {r["id"]: {"id": r["id"], "name": r["name"], "package": r["package"]}
                for r in self.s.db.query("SELECT id, name, package FROM apps ORDER BY name")}

    def _app_da_sessao(self, sess: dict[str, Any], apps: dict[str, dict[str, str]]) -> str | None:
        if sess.get("app_id"):
            return sess["app_id"]
        for e in sess["inputs"]:
            if e["type"] == "open_app" and e.get("app_id") in apps:
                return e["app_id"]
            pkg = e.get("package")
            achado = next((a["id"] for a in apps.values() if a["package"] == pkg), None) if pkg else None
            if achado:
                return achado
        return None

    @staticmethod
    def _catalogo(pacote: str | None) -> list[dict[str, Any]]:
        cat = load_catalog(pacote) if pacote else None
        if cat is None:
            return []
        return [{"key": c.key, "title": c.title, "side_effect": c.side_effect, "bindings": list(c.bindings)}
                for c in cat.offered]

    # ------------------------------------------------------------------ proposta
    async def propose(self, session_id: str) -> dict[str, Any]:
        sess = self.s.training.get(session_id)
        if sess["status"] == "recording":
            raise TrainingError("still_recording", "Conclua a gravação antes de pedir a proposta.", 409)
        if sess["status"] in ("saved", "discarded"):
            raise TrainingError("closed", "Este treinamento já foi salvo ou descartado.", 409)
        if not sess["inputs"]:
            raise TrainingError("empty", "Nada foi gravado neste treinamento.", 400)
        apps = self._apps()
        app_id = self._app_da_sessao(sess, apps)
        pacote = apps[app_id]["package"] if app_id in apps else None
        req = TrainingRequest(intent=sess["intent"], app_id=app_id, apps=list(apps.values()),
                              inputs=[e for e in sess["inputs"]], catalog=self._catalogo(pacote), session_id=session_id)
        proposta, usage = await self.s.provider.generalize(req)
        try:
            self.s.repo.add_usage(None, None, usage)
        except Exception:  # noqa: BLE001 - contabilidade nunca derruba a proposta
            pass
        proposta["app_id"] = app_id
        self.s.db.execute("UPDATE training_sessions SET proposal=?, status='proposed', updated_at=? WHERE id=?",
                          (dumps(proposta), now_iso(), session_id))
        return self.s.training.get(session_id)

    # ------------------------------------------------------------------ salvar
    async def save(self, session_id: str, *, proposal: dict[str, Any] | None, profile_ids: list[str],
                   group_ids: list[str]) -> dict[str, Any]:
        sess = self.s.training.get(session_id)
        if sess["status"] == "saved":
            raise TrainingError("closed", "Este treinamento já virou habilidade.", 409)
        p = proposal or sess.get("proposal")
        if not p or not p.get("steps"):
            raise TrainingError("no_proposal", "Peça a proposta da IA (ou monte as etapas) antes de salvar.", 400)
        comando = (p.get("command_template") or "").strip()
        if not comando:
            raise TrainingError("invalid_command", "A habilidade precisa de um comando.", 400)
        if re.search(r"\}\s*\{", comando):
            raise TrainingError("ambiguous_command", "Há dois parâmetros colados no comando (ex.: “{a} {b}”): coloque uma "
                                                     "palavra fixa entre eles, senão não dá para separar os valores.", 400)
        for pid in profile_ids:
            if self.s.social_repo.profile_row(pid) is None:
                raise TrainingError("unknown_profile", f"Perfil inexistente: {pid}.", 400)
        for gid in group_ids:
            if self.s.social_repo.policy_group_row(gid) is None:
                raise TrainingError("unknown_group", f"Grupo de acesso inexistente: {gid}.", 400)

        apps = self._apps()
        app_id = p.get("app_id") or self._app_da_sessao(sess, apps)
        pacote = apps[app_id]["package"] if app_id in apps else None
        exemplos = {x["name"]: str(x.get("example") or "") for x in p.get("parameters") or [] if x.get("name")}
        passos: list[PlanStep] = []
        for st in p["steps"]:
            app_da_etapa = st.get("app_id") or app_id
            pkg = apps[app_da_etapa]["package"] if app_da_etapa in apps else pacote
            cat = load_catalog(pkg) if pkg else None
            cap = st.get("capability")
            if cat is not None and st.get("side_effect") and not (cap and cat.has(cap)):
                raise TrainingError(
                    "capability_required",
                    f"A etapa “{st.get('title')}” muda algo fora do aparelho em {apps.get(app_da_etapa, {}).get('name', pkg)}: "
                    "escolha a ação do catálogo que ela realiza. Sem isso a habilidade passaria por fora da aprovação "
                    "e dos limites do perfil.", 400)
            outro_app = app_da_etapa if app_da_etapa and app_da_etapa != app_id else None
            if cat is not None and cap and cat.has(cap):
                vinculos = {b["name"]: b["value"] for b in st.get("bindings") or [] if b.get("name")}
                passo = cat.build_step(CapabilityNode(key=st["key"], capability=cap, bindings=vinculos))
                passos.append(passo.model_copy(update={"key": st["key"], "app_id": outro_app}))
            else:
                post = st.get("postcondition") or {}
                passos.append(PlanStep(
                    key=st["key"], title=st.get("title") or st["key"], goal=st.get("goal") or st.get("title") or st["key"],
                    side_effect=bool(st.get("side_effect")), app_id=outro_app, max_attempts=1 if st.get("side_effect") else 3,
                    postcondition=Postcondition(kind=post.get("kind") or "model_judged", value=post.get("value") or "",
                                                description=post.get("description") or st.get("goal") or "")))
        plano = Plan(summary=(p.get("summary") or sess["intent"])[:200], app_id=app_id, app_package=pacote,
                     parameters={n: "{" + n + "}" for n in exemplos}, steps=passos,
                     planner=PlannerInfo(provider="treinamento", model=f"treinamento:{session_id}", simulated=False))
        try:
            flow_id = self.s.scheduler.flows.learn_from_plan(plano, comando, source=f"training:{session_id}")
        except ValueError as exc:
            raise TrainingError("duplicate_command", str(exc), 409) from None
        self.s.scheduler.flows.set_scope(flow_id, profile_ids=profile_ids, group_ids=group_ids)
        relatorio = await self._receitas(sess, p, passos, exemplos, apps, app_id, session_id)
        self.s.db.execute("UPDATE training_sessions SET status='saved', flow_id=?, proposal=?, updated_at=? WHERE id=?",
                          (flow_id, dumps({**p, "app_id": app_id}), now_iso(), session_id))
        self.s.bus.emit("log", f"Habilidade “{plano.summary[:60]}” salva a partir do treinamento",
                        data={"training_session_id": session_id, "flow_id": flow_id})
        return {"session": self.s.training.get(session_id), "flow_id": flow_id, "steps": relatorio}

    async def _receitas(self, sess: dict[str, Any], p: dict[str, Any], passos: list[PlanStep], exemplos: dict[str, str],
                        apps: dict[str, dict[str, str]], app_id: str | None, session_id: str) -> list[dict[str, Any]]:
        """Uma receita por etapa, destilada das entradas da pessoa. Precisa do aparelho online (versão do app e
        variante de interface fazem parte da identidade); sem ele, o fluxo vale e a IA conduz as etapas."""
        por_seq = {int(e["seq"]): e for e in sess["inputs"]}
        descartadas = {int(d["seq"]) for d in p.get("discarded") or []}
        pacotes = {a["id"]: a["package"] for a in apps.values()}
        rt = self.s.devices.devices.get(sess["instance_id"])
        online = rt is not None and str(getattr(rt.state, "value", rt.state)) == "online"
        relatorio = []
        for st, passo in zip(p["steps"], passos):
            entradas = [por_seq[i] for i in st.get("inputs") or [] if i in por_seq and i not in descartadas]
            acoes, motivo = distill_training(entradas, exemplos, side_effect=passo.side_effect, app_packages=pacotes)
            linha = {"key": passo.key, "title": passo.title, "recipe": False, "reason": motivo}
            if acoes and not online:
                linha["reason"] = "aparelho do treinamento fora do ar: a etapa fica com a IA até uma execução aprender"
            elif acoes:
                pkg = pacotes.get(passo.app_id or app_id or "") or ""
                try:
                    versao = await self.s.devices.app_version(rt, pkg)
                    variante = await self.s.devices.variant_of(rt)
                except Exception as exc:  # noqa: BLE001
                    linha["reason"] = f"não foi possível ler a versão do app no aparelho: {exc}"
                    relatorio.append(linha)
                    continue
                assinatura = self.s.db.scalar(
                    "SELECT r.signature_sha256 FROM device_app_state d JOIN app_releases r ON r.id = d.installed_release_id"
                    " WHERE d.instance_id=? AND d.package_name=?", (sess["instance_id"], pkg)) or ""
                rid = self.s.scheduler.executor.recipes.save(
                    package=pkg, app_version=versao, step_hash=step_template_hash(passo), step_key=passo.key,
                    actions=acoes, learned_from=f"training:{session_id}", signature=assinatura, variant=variante)
                linha.update({"recipe": bool(rid), "reason": "receita gravada" if rid else "já havia receita ativa para esta etapa"})
            relatorio.append(linha)
        return relatorio
