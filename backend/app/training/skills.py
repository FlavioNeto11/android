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
from collections.abc import Callable
from typing import Any, get_args

from ..db import dumps
from ..models import Plan, PlannerInfo, PlanStep, Postcondition
from ..planning.capabilities import CapabilityCatalog, CapabilityNode, load_catalog
from ..planning.training import TrainingRequest
from ..taskqueue.flows import PLACEHOLDER, RESERVED
from ..taskqueue.recipes import distill_training, step_template_hash
from ..util import now_iso
from .recorder import TrainingError


# ---------------------------------------------------------------------------- validação da proposta (item 31.83)
#: O fluxo casa o pedido com `(?P<x>.+?)` e `fullmatch`: o parâmetro engole qualquer texto. Por isso o comando
#: tem de COMEÇAR por palavra fixa (`{pedido} no instagram` casa com todo pedido que termine em "no instagram") e ter
#: texto fixo suficiente fora das chaves para o pedido de outra pessoa não cair nele. Duas palavras e seis
#: letras/dígitos deixam passar moldes curtos e legítimos ("ligue para {contato}") e barram "siga {perfil}".
COMANDO_PALAVRAS_FIXAS_MIN = 2
COMANDO_CARACTERES_FIXOS_MIN = 6
_KINDS_DE_POSCONDICAO = frozenset(get_args(Postcondition.model_fields["kind"].annotation))
Proposta = dict[str, Any]       # proposta e etapa: JSON livre vindo do cliente, validado à mão abaixo


def _padrao_da_chave_de_etapa() -> re.Pattern[str]:
    """O padrão de `PlanStep.key`, achado pelo item de `metadata` que tem `pattern` (e não pela posição: outra
    restrição no campo mudaria a ordem)."""
    for restricao in PlanStep.model_fields["key"].metadata:
        if getattr(restricao, "pattern", None):
            return re.compile(restricao.pattern)
    raise RuntimeError("PlanStep.key deixou de ter `pattern`: a validação do salvar do treino precisa ser revista")


_CHAVE_DE_ETAPA = _padrao_da_chave_de_etapa()
_QUALQUER_CHAVE = re.compile(r"\{[^{}]*\}")                  # qualquer `{…}`, válido ou não
_NOME_DE_PARAMETRO = re.compile(r"[a-z_][a-z0-9_]*")           # o mesmo que `PLACEHOLDER` do fluxo aceita entre as chaves


def _e_texto_ou_nulo(v: object) -> bool:
    return v is None or isinstance(v, str)


def _e_inteiro(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


#: A tela de revisão ainda não deixa atribuir nem descartar entrada, nem editar etapa (31.90): nestes códigos a
#: única saída que existe hoje é pedir outra proposta. O comando é editável na tela, por isso `comando_generico` fica fora.
_REFAZER_A_PROPOSTA = frozenset({"entradas_sem_etapa", "entrada_duplicada", "entrada_inexistente", "proposta_invalida", "parametro_fora_do_comando", "parametro_nao_declarado",
                                 "pos_condicao_vazia", "etapa_invalida"})


def _erro(code: str, mensagem: str) -> TrainingError:
    if code in _REFAZER_A_PROPOSTA:
        mensagem = f"{mensagem} Peça uma nova proposta à IA."
    return TrainingError(code, mensagem, 400)


def validar_proposta_para_salvar(p: Proposta, seqs_gravados: set[int],
                                 etapa_do_catalogo: Callable[[Proposta], bool] = lambda _st: False,
                                 ) -> tuple[Proposta, list[str]]:
    """Confere a proposta que o cliente mandou ANTES de qualquer escrita: o `propose` normaliza, o `save` aceita o que
    vier. Devolve a proposta com `inputs`/`discarded` já como inteiros e os AVISOS do que foi aceito com ressalva.

    Recusa (400) com o que a pessoa deve corrigir: `etapa_invalida`, `parametro_invalido`,
    `parametro_fora_do_comando`, `parametro_nao_declarado`, `comando_generico`, `pos_condicao_vazia` (etapa com efeito
    externo), `entrada_duplicada` (em duas etapas, ou em etapa e em `discarded`: o `_receitas` tiraria o toque da etapa
    calado), `entradas_sem_etapa` (gravada e sem destino: a receita nasce sem o toque, errada e calada) e
    `entrada_inexistente` (`seq` fora das gravadas) e `proposta_invalida` (tipo errado: lista que não é lista, texto que
    não é texto).
    `etapa_do_catalogo(etapa)`: a etapa vira a ação do catálogo, que traz a própria pós-condição."""
    comando = (p.get("command_template") or "").strip()
    avisos: list[str] = []
    if not _e_texto_ou_nulo(p.get("summary")) or not _e_texto_ou_nulo(p.get("app_id")):
        raise _erro("proposta_invalida", "`summary` e `app_id` da proposta têm de ser texto.")
    for campo in ("steps", "parameters", "discarded"):
        if not (p.get(campo) is None or isinstance(p[campo], list)):
            raise _erro("proposta_invalida", f"`{campo}` da proposta tem de ser uma lista.")

    etapas: list[Proposta] = []
    chaves: set[str] = set()
    for n, st in enumerate(p.get("steps") or [], 1):
        if not isinstance(st, dict):
            raise _erro("etapa_invalida", f"A etapa {n} não está no formato esperado.")
        if not all(_e_texto_ou_nulo(st.get(c)) for c in ("key", "title", "goal", "capability", "app_id")):
            raise _erro("etapa_invalida", f"A etapa {n} tem chave, título, objetivo, ação do catálogo ou app que não são texto.")
        chave = str(st.get("key") or "").strip()
        rotulo = f"A etapa {n}" + (f" (“{st.get('title')}”)" if st.get("title") else "")
        if not chave:
            raise _erro("etapa_invalida", f"{rotulo} está sem chave: dê a cada etapa uma chave própria (minúsculas, números e _).")
        if not _CHAVE_DE_ETAPA.match(chave):
            raise _erro("etapa_invalida", f"{rotulo} tem a chave “{chave}” inválida: use minúsculas, números e _, "
                                          "começando por letra, com 2 a 41 caracteres.")
        if chave in chaves:
            raise _erro("etapa_invalida", f"A chave “{chave}” aparece em mais de uma etapa: cada etapa precisa de uma chave própria.")
        chaves.add(chave)
        if not (str(st.get("title") or "").strip() or str(st.get("goal") or "").strip()):
            raise _erro("etapa_invalida", f"{rotulo} está sem título e sem objetivo: diga o que ela faz.")
        post = st.get("postcondition") or {}
        if not isinstance(post, dict) or not all(_e_texto_ou_nulo(post.get(c)) for c in ("kind", "value", "description")):
            raise _erro("etapa_invalida", f"{rotulo} tem uma pós-condição fora do formato: tipo, valor e descrição são texto.")
        if post.get("kind") and post["kind"] not in _KINDS_DE_POSCONDICAO:
            raise _erro("etapa_invalida", f"{rotulo} tem uma pós-condição inválida: o tipo deve ser um de "
                                          f"{', '.join(sorted(_KINDS_DE_POSCONDICAO))}.")
        if not (st.get("side_effect") is None or isinstance(st["side_effect"], bool)):
            raise _erro("etapa_invalida", f"{rotulo} tem `side_effect` que não é verdadeiro/falso (o texto “false” contaria como verdadeiro).")
        brutas = st.get("inputs")
        if brutas is None:
            brutas = []
        if not isinstance(brutas, list) or not all(_e_inteiro(i) for i in brutas):
            raise _erro("etapa_invalida", f"{rotulo} tem `inputs` que não é uma lista de números de entrada gravada.")
        vinculos = st.get("bindings")
        if vinculos is not None and not (isinstance(vinculos, list) and all(isinstance(b, dict) for b in vinculos)):
            raise _erro("etapa_invalida", f"{rotulo} tem `bindings` que não é uma lista de objetos {{name, value}}.")
        etapas.append({**st, "key": chave, "inputs": sorted(set(brutas))})

    if not all(isinstance(x, dict) and _e_texto_ou_nulo(x.get("name")) for x in p.get("parameters") or []):
        raise _erro("proposta_invalida", "Cada item de `parameters` tem de ser um objeto com `name` em texto.")
    invalidos = [c for c in _QUALQUER_CHAVE.findall(comando) if not _NOME_DE_PARAMETRO.fullmatch(c[1:-1])]
    if invalidos:
        raise _erro("parametro_invalido",
                    "O comando tem " + ", ".join(invalidos) + ", que não é um nome de parâmetro válido: use minúsculas, "
                    "sem acento, números e _, começando por letra (ex.: {contato}). Do contrário o fluxo nunca casa.")
    sobras = _QUALQUER_CHAVE.sub("", comando)
    if "{" in sobras or "}" in sobras:
        raise _erro("parametro_invalido",
                    "O comando tem uma chave `{` ou `}` sem par: feche cada parâmetro (ex.: {contato}) ou tire a chave. "
                    "Do contrário o fluxo nunca casa.")
    declarados = {str(x["name"]) for x in p.get("parameters") or [] if x.get("name")}
    usados = {m.group(1) for m in PLACEHOLDER.finditer(comando)}
    fora = sorted(n for n in declarados - usados if n not in RESERVED)
    if fora:
        raise _erro("parametro_fora_do_comando",
                    "Parâmetro declarado mas ausente do comando: " + ", ".join("{" + n + "}" for n in fora)
                    + ". Ponha cada um no comando (ex.: “… {" + fora[0] + "} …”) ou tire-o da lista: sem isso o fluxo "
                    "nunca casa com um pedido.")
    sem_declarar = sorted(n for n in usados - declarados if n not in RESERVED)
    if sem_declarar:
        raise _erro("parametro_nao_declarado",
                    "O comando usa " + ", ".join("{" + n + "}" for n in sem_declarar) + " sem declarar o parâmetro: "
                    "acrescente-o em `parameters` com um exemplo, ou troque por texto fixo.")

    palavras = re.findall(r"[^\W_]+", _QUALQUER_CHAVE.sub(" ", comando))
    primeiro = re.search(r"\{[^{}]*\}|[^\W_]+", comando)
    if (primeiro is None or primeiro.group(0).startswith("{") or len(palavras) < COMANDO_PALAVRAS_FIXAS_MIN
            or sum(len(w) for w in palavras) < COMANDO_CARACTERES_FIXOS_MIN):
        raise _erro("comando_generico",
                    f"O comando “{comando}” casaria com pedidos alheios: comece pelo verbo (nunca por um {{parâmetro}}) "
                    f"e deixe ao menos {COMANDO_PALAVRAS_FIXAS_MIN} palavras fixas (e {COMANDO_CARACTERES_FIXOS_MIN} letras) "
                    "fora das chaves, como “responda a DM de {contato} com {mensagem}”.")

    for st in etapas:
        post = st.get("postcondition") or {}
        if (post.get("value") or "").strip() or (post.get("description") or "").strip() or etapa_do_catalogo(st):
            continue
        nome = st.get("title") or st["key"]
        if st.get("side_effect"):
            raise _erro("pos_condicao_vazia",
                        f"A etapa “{nome}” muda algo fora do aparelho e não diz como comprovar que deu certo: descreva "
                        "a pós-condição (o que a tela mostra depois).")
        avisos.append(f"A etapa “{nome}” não descreve a pós-condição: o objetivo dela serve de critério.")

    descartadas: list[Proposta] = []
    for d in p.get("discarded") or []:
        if not isinstance(d, dict) or not _e_inteiro(d.get("seq")):
            raise _erro("proposta_invalida", "Há uma entrada descartada sem o número da entrada (`seq`).")
        descartadas.append(d)
    por_etapa: dict[int, int] = {}
    for st in etapas:
        for i in st["inputs"]:
            por_etapa[i] = por_etapa.get(i, 0) + 1
    seqs_descartados = [d["seq"] for d in descartadas]
    inexistentes = sorted(({*por_etapa} | {*seqs_descartados}) - seqs_gravados)
    if inexistentes:
        raise _erro("entrada_inexistente",
                    "Entradas que não existem nesta gravação: " + ", ".join(f"#{i}" for i in inexistentes)
                    + ". Use só os números das entradas gravadas.")
    descartes = set(seqs_descartados)
    duplicadas = sorted({*(i for i, n in por_etapa.items() if n > 1 or i in descartes),
                         *(i for i in descartes if seqs_descartados.count(i) > 1)})
    if duplicadas:
        raise _erro("entrada_duplicada",
                    "Entradas em mais de um lugar (em duas etapas, em uma etapa e em descartadas, ou repetidas em descartadas): "
                    + ", ".join(f"#{i}" for i in duplicadas) + ". Cada entrada fica em UMA etapa ou é descartada, "
                    "senão a receita perde o toque sem avisar.")
    cobertas = {i for st in etapas for i in st["inputs"]} | {d["seq"] for d in descartadas}
    orfas = sorted(seqs_gravados - cobertas)
    if orfas:
        raise _erro("entradas_sem_etapa",
                    "Entradas gravadas sem etapa e sem descarte: " + ", ".join(f"#{i}" for i in orfas)
                    + ". Atribua cada uma a uma etapa ou descarte-a: do contrário a receita nasce sem esse toque e "
                    "erra a reprodução.")
    return {**p, "steps": etapas, "discarded": descartadas}, avisos


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
        if not isinstance(p.get("command_template"), (str, type(None))):
            raise TrainingError("invalid_command", "O comando da habilidade tem de ser um texto.", 400)
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

        def _catalogo_da_etapa(st: Proposta) -> CapabilityCatalog | None:
            pkg = apps[st.get("app_id") or app_id]["package"] if (st.get("app_id") or app_id) in apps else pacote
            return load_catalog(pkg) if pkg else None

        # 31.83: tudo que dá para recusar sai ANTES da primeira escrita (fluxo, escopo, receita, status da sessão).
        p, avisos = validar_proposta_para_salvar(
            p, {int(e["seq"]) for e in sess["inputs"]},
            lambda st: bool(st.get("capability")) and (cat := _catalogo_da_etapa(st)) is not None and cat.has(st["capability"]))
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
        return {"session": self.s.training.get(session_id), "flow_id": flow_id, "steps": relatorio, "warnings": avisos}

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
