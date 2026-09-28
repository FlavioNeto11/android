"""MODO SIMULADO DE DESENVOLVIMENTO — NÃO é IA.

Planejador e ator por regras fixas que só conhecem o app de QA (QA Messenger). Existe para exercitar fila,
executor, Appium e emuladores sem chave de provedor e para os testes automatizados. Toda execução feita
com ele é marcada como "simulada" no banco, nos eventos e no painel, e NÃO conta como uso do modelo.
"""
from __future__ import annotations

import copy
import hashlib
import random
import re
from datetime import date
from typing import Any

from ..automation.hierarchy import UiElement, UiTree
from ..models import (DELIVERY_ORDER, AiStatus, DeliveryLevel, MemoryCandidateDTO, MissingInfo, PersonaDraft, Plan,
                      PlannerInfo, PlanStep, Postcondition, SocialDraftDTO)
from ..modules.execution.domain.orquestracao import (OrquestracaoOut, PedidoDeOrquestracao,
                                                     orquestracao_simulada)
from ..modules.execution.domain.command_refinement import (CommandRefinement, RefineRequest,
                                                          refinamento_simulado)
from ..modules.identity.domain.persona import BIOGRAPHY_SCHEMA_VERSION
from ..modules.identity.domain.persona_generation import (IDADE_MAXIMA_GERADA, IDADE_MINIMA_GERADA,
                                                            PersonaEvitada, PersonaGenerationRequest, nome_repetido,
                                                            preencher_vazios)
from ..security.redaction import looks_secret
from ..util import norm_text
from .capabilities import CapabilityNode, compose
from .provider import Decision, DecisionRequest, PlanRequest, SocialRequest, Usage, Verdict, VerifyRequest

QA_PACKAGE = "com.pocqa.messenger"
_HANDLE = re.compile(r"@([a-zA-Z0-9._]{2,30})")
QUOTED = re.compile(r"[“\"']([^”\"']{1,500})[”\"']")
_NAME = r"[“\"']?([A-Za-zÀ-ú0-9][\wÀ-ú-]*(?: QA)?)[”\"']?"
RECIPIENT_PATTERNS = [re.compile(p + r"\s+" + _NAME, re.IGNORECASE) for p in (
    r"identificad[oa] como", r"chamad[oa]", r"conversa (?:com|de)(?: o contato| a)?", r"para o contato", r"contato")]


def _find_recipient(command: str) -> str | None:
    for pat in RECIPIENT_PATTERNS:            # do mais específico para o mais genérico
        m = pat.search(command)
        if m and norm_text(m.group(1)) not in ("de", "o", "a", "com", "teste"):
            return m.group(1).strip()
    return None
STATUS_LEVELS = [("lida", DeliveryLevel.read), ("entregue", DeliveryLevel.delivered),
                 ("enviada", DeliveryLevel.sent), ("enviando", DeliveryLevel.appeared)]


def _required_level(command: str) -> DeliveryLevel:
    c = norm_text(command)
    if "lida" in c:
        return DeliveryLevel.read
    if "entregue" in c:
        return DeliveryLevel.delivered
    if "enviad" in c:
        return DeliveryLevel.sent
    return DeliveryLevel.appeared


def _post(kind: str, value: str, description: str, level: DeliveryLevel | None = None) -> Postcondition:
    return Postcondition(kind=kind, value=value, description=description, required_delivery_level=level)  # type: ignore[arg-type]


class SimulatedProvider:
    name = "simulated"
    model = "regras-fixas-qa"
    simulated = True

    def status(self) -> AiStatus:
        return AiStatus(provider=self.name, model=self.model, configured=True, simulated=True,
                        sends_data_externally=False, effort=None,
                        notice="MODO SIMULADO: planejamento e ações por regras fixas do app de QA. Nenhum modelo de "
                               "IA é consultado e nada sai desta máquina. Não vale como validação do uso de IA.")

    # ------------------------------------------------------------------ plano
    async def generalize(self, req: Any) -> tuple[dict[str, Any], Usage]:
        """Modo treinamento sem IA: regras fixas (ver `training.proposta_simulada`)."""
        from .training import proposta_simulada  # noqa: PLC0415
        return proposta_simulada(req), Usage()

    async def orchestrate_targets(self, req: PedidoDeOrquestracao) -> tuple[OrquestracaoOut, Usage]:
        """Quem faz sem IA: conduta por palavras e perfil por termos em comum (ver `orquestracao_simulada`)."""
        return orquestracao_simulada(req), Usage()

    async def refine_command(self, req: RefineRequest) -> tuple[CommandRefinement, Usage]:
        """Assistente do comando sem IA: blocos fixos e a pergunta do app (ver `refinamento_simulado`)."""
        return refinamento_simulado(req), Usage()

    async def plan(self, req: PlanRequest) -> tuple[Plan, Usage]:
        info = PlannerInfo(provider=self.name, model=self.model, simulated=True)
        if req.catalog is not None:
            return self._plan_with_catalog(req, info), Usage()
        app = next((a for a in req.apps if a.package == QA_PACKAGE), None)
        cmd = req.command
        c = norm_text(cmd)
        missing: list[MissingInfo] = []
        if app is None:
            missing.append(MissingInfo(field="app", question="O modo simulado só opera o QA Messenger, que não está "
                                                             "configurado. Cadastre-o em Configuração → Aplicativos."))
            return Plan(summary="Plano simulado indisponível", missing=missing, planner=info), Usage()
        base = [
            PlanStep(key="open_app", title="Abrir o QA Messenger", goal="Trazer o QA Messenger para o primeiro plano.",
                     postcondition=_post("app_foreground", QA_PACKAGE, "O QA Messenger está em primeiro plano."),
                     timeout_s=90),
            PlanStep(key="confirm_account", title="Confirmar a conta conectada", depends_on=["open_app"],
                     goal="Confirmar na tela inicial que a conta conectada é {account_label}.",
                     postcondition=_post("text_visible", "Conta: {account_label}",
                                         "A tela inicial mostra 'Conta: {account_label}'."), timeout_s=90),
        ]
        if "perfil" in c or "formul" in c:
            fields = dict(re.findall(r"(nome|e-?mail|cidade)\s*[:=]?\s*[“\"']([^”\"']+)[”\"']", cmd, re.IGNORECASE))
            params = {f"profile_{norm_text(k).replace('-', '')}": v for k, v in fields.items()}
            steps = base + [PlanStep(key="open_profile", title="Abrir a tela de Perfil", depends_on=["confirm_account"],
                                     goal="Abrir o formulário de perfil pelo botão Perfil.",
                                     postcondition=_post("element_present", "id=profile_save",
                                                         "O formulário de perfil está aberto."))]
            if params:
                steps += [
                    PlanStep(key="fill_profile", title="Preencher o formulário", depends_on=["open_profile"],
                             goal="Preencher os campos do perfil com os valores dos parâmetros.",
                             postcondition=_post("model_judged", "campos preenchidos",
                                                 "Os campos mostram os valores pedidos.")),
                    PlanStep(key="save_profile", title="Salvar o perfil", depends_on=["fill_profile"], side_effect=True,
                             max_attempts=1, commit_guard=list(params.values()),
                             goal="Tocar em Salvar uma única vez.",
                             postcondition=_post("text_visible", "Perfil salvo", "Aparece o aviso 'Perfil salvo às …'.")),
                ]
            return Plan(summary="Abrir o perfil" + (" e preencher o formulário" if params else ""), app_id=app.id,
                        app_package=app.package, parameters=params,
                        success_criteria=["Perfil salvo" if params else "Formulário de perfil aberto"],
                        steps=steps, planner=info), Usage()
        if "envi" not in c and "mensagem" not in c:
            return Plan(summary="Abrir o QA Messenger e confirmar a conta", app_id=app.id, app_package=app.package,
                        success_criteria=["App aberto com a conta esperada"], steps=base, planner=info), Usage()
        quoted = QUOTED.findall(cmd)
        if "todos os contatos" in c and quoted:
            return self._plan_for_all(app, base, quoted[0], _required_level(cmd), info), Usage()
        recipient = _find_recipient(cmd)
        if not recipient:
            missing.append(MissingInfo(field="recipient", question="Para qual contato a mensagem deve ser enviada?"))
        if not quoted:
            missing.append(MissingInfo(field="message", question="Qual é o texto da mensagem (entre aspas)?"))
        if missing:
            return Plan(summary="Enviar mensagem (dados incompletos)", app_id=app.id, app_package=app.package,
                        missing=missing, planner=info), Usage()
        level = _required_level(cmd)
        steps = base + [
            PlanStep(key="open_conversation", title="Abrir a conversa com {recipient}", depends_on=["confirm_account"],
                     goal="Localizar o contato {recipient} na lista e abrir a conversa.",
                     postcondition=_post("element_present", "id=chat_title|text={recipient}",
                                         "O cabeçalho da conversa mostra {recipient}.")),
            PlanStep(key="compose_message", title="Preencher a mensagem", depends_on=["open_conversation"],
                     goal="Digitar o conteúdo no campo Mensagem.",
                     postcondition=_post("text_visible", "{message}", "O campo de mensagem contém o texto.")),
            PlanStep(key="send_message", title="Enviar a mensagem", depends_on=["compose_message"], side_effect=True,
                     max_attempts=1, commit_guard=["{recipient}", "{message}"],
                     goal="Tocar em Enviar uma única vez.",
                     postcondition=_post("model_judged", "mensagem na conversa", "A mensagem aparece na conversa.",
                                         DeliveryLevel.appeared)),
            PlanStep(key="verify_sent", title="Verificar o envio", depends_on=["send_message"],
                     goal="Confirmar que a mensagem aparece na conversa com o status exigido.", timeout_s=120,
                     postcondition=_post("model_judged", "status da mensagem",
                                         f"A mensagem aparece com status no nível '{level.value}' ou superior.", level)),
        ]
        return Plan(summary=f"Enviar mensagem para {recipient} pelo QA Messenger", app_id=app.id, app_package=app.package,
                    parameters={"recipient": recipient or "", "message": quoted[0]},
                    success_criteria=[f"Mensagem visível na conversa de {recipient} com status ≥ {level.value}",
                                      "Conta conectada confere com a esperada"], steps=steps, planner=info), Usage()

    @staticmethod
    def _plan_for_all(app: AppContext, base: list[PlanStep], message: str, level: DeliveryLevel, info: PlannerInfo) -> Plan:
        each = "collect_contacts"
        steps = base + [
            PlanStep(key=each, title="Ler os contatos da lista", depends_on=["confirm_account"],
                     goal="Ler o nome de cada contato da lista de conversas.",
                     postcondition=_post("items_collected", "nome de cada contato da lista de conversas",
                                         "Os contatos da lista foram lidos até o fim.")),
            PlanStep(key="open_conversation", title="Abrir a conversa com {item}", depends_on=[each], for_each=each,
                     goal="Localizar o contato {item} na lista e abrir a conversa.",
                     postcondition=_post("element_present", "id=chat_title|text={item}", "O cabeçalho mostra {item}.")),
            PlanStep(key="compose_message", title="Preencher a mensagem para {item}", depends_on=["open_conversation"],
                     for_each=each, goal="Digitar o conteúdo no campo Mensagem.",
                     postcondition=_post("element_present", "id=message_input|text={message}", "O campo contém o texto.")),
            PlanStep(key="send_message", title="Enviar a mensagem para {item}", depends_on=["compose_message"],
                     for_each=each, side_effect=True, max_attempts=1, commit_guard=["{item}", "{message}"],
                     goal="Tocar em Enviar uma única vez.",
                     postcondition=_post("model_judged", "mensagem na conversa", "A mensagem aparece na conversa.", level)),
            PlanStep(key="back_to_list", title="Voltar à lista de conversas", depends_on=["send_message"], for_each=each,
                     goal="Voltar para a lista de conversas.",
                     postcondition=_post("element_present", "id=account_label", "A tela inicial está visível.")),
        ]
        return Plan(summary="Enviar a mensagem para todos os contatos da lista", app_id=app.id, app_package=app.package,
                    parameters={"message": message}, steps=steps, planner=info,
                    success_criteria=[f"Cada contato da lista recebeu a mensagem com status ≥ {level.value}"])

    # ------------------------------------------------------------------ decisão
    async def decide(self, req: DecisionRequest) -> tuple[Decision, Usage]:
        tree: UiTree = req.screen.tree
        ctx = req.ctx
        p = ctx.parameters

        def d(tool: str, why: str, **args: Any) -> tuple[Decision, Usage]:
            return Decision(tool=tool, args={"rationale": f"[simulado] {why}", **args}), Usage()

        def first(**kw: Any) -> UiElement | None:
            found = tree.find(**kw)
            return found[0] if found else None

        dismiss = first(resource_id="interstitial_dismiss") or first(text="Agora não", exact=True)
        if dismiss:
            return d("tap", "Dispensar aviso inesperado", element_id=dismiss.id, x=None, y=None, is_commit_action=False)
        in_app = req.screen.package == QA_PACKAGE
        key = re.sub(r"_i\d+$", "", ctx.step_key)                  # cópia de bloco for_each → etapa-modelo
        if "item" in p:
            p = {**p, "recipient": p["item"]}
        if key == "open_app" or not in_app:
            if in_app:
                return d("step_done", "App em primeiro plano", evidence="package com.pocqa.messenger", delivery_level=None)
            return d("open_app", "Abrir o app alvo", package=None)
        if key == "confirm_account":
            label = first(resource_id="account_label")
            if label is None:
                return d("press_back", "Voltar à tela inicial para ver a conta")
            if norm_text(ctx.account_label or "") and norm_text(ctx.account_label) in norm_text(label.text):
                return d("step_done", "Conta confere", evidence=label.text, delivery_level=None)
            return d("step_blocked", "Conta diferente", kind="wrong_account", needs_user=True,
                     reason=f"A tela mostra '{label.text}', esperado '{ctx.account_label}'.")
        if key == "open_conversation":
            title = first(resource_id="chat_title")
            if title is not None:
                if norm_text(title.text) == norm_text(p.get("recipient")):
                    return d("step_done", "Conversa correta aberta", evidence=f"Cabeçalho: {title.text}", delivery_level=None)
                return d("press_back", "Conversa errada; voltar")
            row = next((e for e in tree.find(text=p.get("recipient", ""), exact=True)
                        if e.resource_id.endswith("conversation_name")), None)
            if row:
                return d("tap", f"Abrir o contato {p.get('recipient')}", element_id=row.id, x=None, y=None,
                         is_commit_action=False)
            if sum(1 for h in req.history if "scroll" in h) < 3:
                return d("scroll", "Procurar o contato mais abaixo", direction="down", element_id=None)
            return d("step_blocked", "Contato não encontrado", kind="missing_info", needs_user=True,
                     reason=f"O contato {p.get('recipient')} não aparece na lista.")
        if key == "compose_message":
            field = first(resource_id="message_input")
            if field is None:
                return d("observe_screen", "Campo de mensagem ainda não visível")
            if norm_text(field.text) == norm_text(p.get("message")):
                return d("step_done", "Texto preenchido", evidence=f"Campo contém: {field.text}", delivery_level=None)
            return d("type_text", "Digitar a mensagem", text=p.get("message", ""), element_id=field.id,
                     clear_first=True, press_enter=False, is_commit_action=False)
        if key == "send_message":
            level, status = _message_level(tree, p.get("message", ""))
            if ctx.commit_done or level != DeliveryLevel.none:
                if level != DeliveryLevel.none:
                    return d("step_done", "Mensagem já aparece na conversa", evidence=f"Status: {status}",
                             delivery_level=level.value)
                return d("wait_for", "Aguardar a mensagem aparecer", text=p.get("message"), seconds=4)
            btn = first(resource_id="send_button")
            if btn is None:
                return d("observe_screen", "Botão Enviar não visível")
            return d("tap", "Enviar (ação com efeito externo, única)", element_id=btn.id, x=None, y=None,
                     is_commit_action=True)
        if key == "verify_sent":
            level, status = _message_level(tree, p.get("message", ""))
            need = DeliveryLevel(ctx.required_delivery_level or "appeared")
            waits = sum(1 for h in req.history if "wait_for" in h)
            if DELIVERY_ORDER[level] >= DELIVERY_ORDER[need] or waits >= 6:
                return d("step_done", f"Status observado: {status or 'nenhum'}", evidence=f"Status: {status}",
                         delivery_level=level.value)
            return d("wait_for", "Aguardar a evolução do status", text=None, seconds=2)
        if key == "collect_contacts":
            lst = first(resource_id="conversation_list")
            if lst is None:
                return d("press_back", "Voltar à tela inicial para ver a lista")
            return d("collect_list", "Ler todos os contatos", element_id=lst.id, item_selector="id=conversation_name",
                     exclude=[], expect_done=True)
        if key == "back_to_list":
            if first(resource_id="account_label"):
                return d("step_done", "Lista visível", evidence="Conta visível na tela inicial", delivery_level=None)
            back = first(resource_id="chat_back")
            if back:
                return d("tap", "Voltar à lista", element_id=back.id, x=None, y=None, is_commit_action=False)
            return d("press_back", "Voltar à lista")
        if key == "open_profile":
            if first(resource_id="profile_save"):
                return d("step_done", "Formulário aberto", evidence="Botão Salvar visível", delivery_level=None)
            btn = first(resource_id="btn_profile")
            if btn:
                return d("tap", "Abrir Perfil", element_id=btn.id, x=None, y=None, is_commit_action=False)
            return d("press_back", "Voltar à tela inicial")
        if key == "fill_profile":
            for pname, rid in (("profile_nome", "profile_name"), ("profile_email", "profile_email"),
                               ("profile_cidade", "profile_city")):
                if pname in p:
                    f = first(resource_id=rid)
                    if f is not None and norm_text(f.text) != norm_text(p[pname]):
                        return d("type_text", f"Preencher {rid}", text=p[pname], element_id=f.id, clear_first=True,
                                 press_enter=False, is_commit_action=False)
            return d("step_done", "Campos preenchidos", evidence="Valores conferem", delivery_level=None)
        if key == "save_profile":
            if tree.contains_text("Perfil salvo"):
                return d("step_done", "Aviso de salvo visível", evidence="Perfil salvo", delivery_level=None)
            if ctx.commit_done:
                return d("wait_for", "Aguardar confirmação", text="Perfil salvo", seconds=3)
            btn = first(resource_id="profile_save")
            if btn:
                return d("tap", "Salvar (efeito externo)", element_id=btn.id, x=None, y=None, is_commit_action=True)
        return d("step_blocked", "Etapa desconhecida", kind="other", needs_user=False,
                 reason=f"O modo simulado não sabe executar a etapa '{key}'.")

    # ------------------------------------------------------------------ verificação
    def _plan_with_catalog(self, req: PlanRequest, info: PlannerInfo) -> Plan:
        """Plano por regras usando o catálogo do app. Existe para exercitar composição, políticas e limites sem
        chamar modelo nenhum — não sabe interpretar comando de verdade, e não finge saber."""
        c = norm_text(req.command)
        # No Instagram o alvo é um @nome; é o que este planejador simulado sabe reconhecer.
        achado = _HANDLE.search(req.command)
        alvo = f"@{achado.group(1)}" if achado else ""
        texto = (QUOTED.findall(req.command) or [""])[0]
        app = next((a for a in req.apps if a.package == req.catalog.package), None)
        faltando: list[MissingInfo] = []
        nodes: list[CapabilityNode] = []
        if ("mensagem" in c or "responda" in c or "envie" in c) and alvo:
            nodes = [CapabilityNode(key="open_inbox", capability="OPEN_INBOX"),
                     CapabilityNode(key="open_thread", capability="OPEN_THREAD", depends_on=["open_inbox"],
                                    bindings={"username": alvo}),
                     CapabilityNode(key="send_message", capability="SEND_MESSAGE", depends_on=["open_thread"],
                                    bindings={"username": alvo, "content": texto})]
        elif "curtir" in c and alvo:
            nodes = [CapabilityNode(key="open_profile", capability="OPEN_PROFILE", bindings={"username": alvo}),
                     CapabilityNode(key="like_post", capability="LIKE_POST", depends_on=["open_profile"])]
        elif "seguir" in c and alvo:
            nodes = [CapabilityNode(key="open_profile", capability="OPEN_PROFILE", bindings={"username": alvo}),
                     CapabilityNode(key="follow", capability="FOLLOW", depends_on=["open_profile"],
                                    bindings={"username": alvo})]
        else:
            nodes = [CapabilityNode(key="open_feed", capability="OPEN_FEED")]
        steps, missing = compose(req.catalog, nodes)
        return Plan(summary=f"[simulado] {req.command[:80]}", app_id=app.id if app else None,
                    app_package=req.catalog.package, success_criteria=["[simulado] ações do catálogo executadas"],
                    steps=steps, missing=faltando + missing, planner=info)

    # ------------------------------------------------------------------ geração social (por regras)
    async def generate_social_response(self, req: SocialRequest) -> tuple[SocialDraftDTO, Usage]:
        """Resposta por regras fixas, para exercitar persona, memória e aprovação sem chamar modelo nenhum.

        Recusa pelo mesmo critério do prompt real (pedido de dinheiro, credencial ou dado sensível) e propõe
        candidatos a memória a partir de frases em que a contraparte fala de si.
        """
        recebido = (req.incoming or "").strip()
        if looks_secret(recebido) or _PEDIDO_ARRISCADO.search(recebido):
            return SocialDraftDTO(
                refused=True, rationale="[simulado] pedido fora do que a persona pode atender",
                refusal_reason="A mensagem pede dinheiro, credencial ou dado sensível; nada foi respondido."), Usage()
        tom = _tom_da_persona(req.context_text)
        alvo = req.counterparty or "essa pessoa"
        # Sem mensagem recebida, o assunto é o que está na TELA (a legenda que vai ser comentada) e, em último
        # caso, a INTENÇÃO do comando — é o caso de comentar um post ou puxar conversa, em que não se responde
        # a ninguém. A tela nunca vira candidato a memória: é assunto, não fato afirmado a esta conta.
        assunto = ((recebido or req.screen or req.brief or "").splitlines() or [""])[0][:80] or "a mensagem"
        texto = f"[simulado] {tom} {alvo}: sobre \"{assunto}\", respondo já!"
        return SocialDraftDTO(
            content=texto[:req.max_length], rationale="[simulado] resposta montada a partir da persona e do contexto",
            memory_candidates=_candidatos(recebido, alvo)), Usage()

    async def generate_persona(self, req: PersonaGenerationRequest) -> tuple[PersonaDraft, Usage]:
        """Persona por sorteio determinístico (semente = hash do pedido): completa, adulta, fictícia; sem custo."""
        return persona_simulada(req), Usage()

    async def verify(self, req: VerifyRequest) -> tuple[Verdict, Usage]:
        tree: UiTree = req.screen.tree
        p = req.ctx.parameters
        if req.ctx.step_key == "fill_profile":
            ok = all(tree.contains_text(v) for k, v in p.items() if k.startswith("profile_"))
            return Verdict(satisfied="yes" if ok else "no", evidence="[simulado] valores dos campos"), Usage()
        level, status = _message_level(tree, p.get("message", ""))
        need = DeliveryLevel(req.ctx.required_delivery_level or "appeared")
        if level == DeliveryLevel.none:
            return Verdict(satisfied="no", evidence="[simulado] mensagem não encontrada na conversa",
                           delivery_level=level), Usage()
        ok = DELIVERY_ORDER[level] >= DELIVERY_ORDER[need]
        return Verdict(satisfied="yes" if ok else "no", delivery_level=level,
                       evidence=f"[simulado] status exibido: '{status}' (exigido: {need.value})"), Usage()


def _message_level(tree: UiTree, message: str) -> tuple[DeliveryLevel, str | None]:
    """Nível de entrega da ÚLTIMA ocorrência da mensagem na lista (status = elemento seguinte à mensagem)."""
    target = norm_text(message)
    if not target:
        return DeliveryLevel.none, None
    els = tree.elements
    for i in range(len(els) - 1, -1, -1):
        e = els[i]
        if e.resource_id.endswith("message_text") and norm_text(e.text) == target:
            status = next((n.text for n in els[i + 1:i + 4] if n.resource_id.endswith("message_status")), "")
            s = norm_text(status)
            if "falha" in s:
                return DeliveryLevel.none, status
            for word, lvl in STATUS_LEVELS:
                if word in s:
                    return lvl, status
            return DeliveryLevel.appeared, status or None
    return DeliveryLevel.none, None


# ---------------------------------------------------------------- apoio da geração social simulada
_PEDIDO_ARRISCADO = re.compile(
    r"\b(pix|dinheiro|empr[eé]stimo|transfer[ei]|cart[aã]o|senha|c[oó]digo|password|token|cpf|conta banc[aá]ria)\b",
    re.IGNORECASE)
# Frases em que a pessoa fala de si mesma: é daí que sai um fato novo, não de qualquer texto da tela.
_SOBRE_SI = re.compile(r"\b(?:eu\s+(?:sou|moro|trabalho|estudo|gosto|odeio|comecei|mudei)|"
                       r"estou\s+\w+|meu\s+\w+|minha\s+\w+)\b[^.!?\r\n]{0,120}", re.IGNORECASE)


def _tom_da_persona(context_text: str) -> str:
    """Lê o tom declarado na persona do contexto. Sem persona, a saudação é neutra."""
    m = re.search(r"^tom:\s*(.+)$", context_text or "", re.IGNORECASE | re.MULTILINE)
    return f"({m.group(1).strip()}) oi" if m else "oi"


def _candidatos(texto: str, alvo: str) -> list[MemoryCandidateDTO]:
    achados = [t.strip() for t in _SOBRE_SI.findall(texto or "")][:3]
    return [MemoryCandidateDTO(subject=alvo, content=f"disse que {c}"[:1000], importance=0.5, confidence=0.6)
            for c in achados if len(c) > 8]


# ---------------------------------------------------------------- persona simulada (determinística)
# Nomes, cidades e ofícios inventados para o sorteio. Nenhum nome completo aqui é de pessoa real conhecida; o sorteio
# combina nome e sobrenome de listas separadas, o que é a mesma garantia que o prompt real pede ao modelo.
_NOMES: dict[str, tuple[str, ...]] = {
    "feminino": ("Marina", "Clara", "Helena", "Beatriz", "Lívia", "Camila", "Renata", "Juliana"),
    "masculino": ("Rafael", "Diego", "Caio", "Otávio", "Bruno", "Felipe", "Thiago", "Henrique"),
}
_SOBRENOMES = ("Lopes", "Prado", "Nunes", "Braga", "Duarte", "Vieira", "Santana", "Melo", "Ferraz", "Tavares")
_CIDADES = (("Curitiba", "PR"), ("Recife", "PE"), ("Florianópolis", "SC"), ("Belo Horizonte", "MG"),
            ("Porto Alegre", "RS"), ("Goiânia", "GO"), ("Fortaleza", "CE"), ("Campinas", "SP"))
_OFICIOS = (("designer", "Design"), ("dentista", "Odontologia"), ("jornalista", "Jornalismo"),
            ("nutricionista", "Nutrição"), ("fisioterapeuta", "Fisioterapia"), ("barista", "Gastronomia"),
            ("contabilista", "Ciências Contábeis"), ("professora de história", "História"))
_HOBBIES = ("trilha", "cerâmica", "corrida", "violão", "yoga", "fotografia analógica", "culinária", "xadrez",
            "ciclismo", "jardinagem")
_TONS = ("acolhedor", "direto", "bem-humorado", "sereno", "curioso", "animado")
# Crenças (ADR-048): perfis COERENTES por inteiro (a prática combina com o que a pessoa faz, a pauta com o ponto do
# espectro), sorteados por uma semente própria para variar entre personas sem mexer no sorteio do resto. Nenhum
# partido, candidato ou figura pública pelo nome — a mesma regra que o prompt real dá ao modelo.
_RELIGIOES: tuple[dict[str, object], ...] = (
    {"affiliation": "católica", "practice": "ocasional",
     "practices": ["missa no Natal e na Páscoa", "festa junina da paróquia"],
     "importance": "tradição de família; pesa mais nas datas", "in_speech": "solta um “se Deus quiser” sem pensar",
     "values": ["família", "gratidão"], "sensitive_topics": ["piada com a fé dos outros"],
     "summary": "de tradição católica, pratica pouco mas respeita as datas"},
    {"affiliation": "evangélica", "practice": "regular",
     "practices": ["culto de domingo", "grupo de estudo no meio da semana"],
     "importance": "orienta as escolhas do dia a dia",
     "in_speech": "“Deus abençoe”, “na paz”; não prega para quem não pediu",
     "values": ["fidelidade", "generosidade", "disciplina"], "sensitive_topics": ["deboche de igreja"],
     "summary": "vai ao culto toda semana e leva a fé para a rotina"},
    {"affiliation": "espírita", "practice": "ocasional",
     "practices": ["palestra no centro espírita", "leitura de livros espíritas"],
     "importance": "dá sentido às perdas e à caridade", "in_speech": "fala em “tudo tem um porquê”",
     "values": ["caridade", "paciência"], "sensitive_topics": ["morte tratada como piada"],
     "summary": "frequenta o centro espírita de vez em quando"},
    {"affiliation": "sem religião", "practice": "nao_pratica", "practices": [],
     "importance": "não pesa; os valores vêm da família", "in_speech": "não aparece",
     "values": ["honestidade", "respeito"], "sensitive_topics": ["discussão sobre religião"],
     "summary": "cresceu em família católica e hoje não segue religião"},
    {"affiliation": "agnóstica", "practice": "nao_pratica", "practices": ["meditação de vez em quando"],
     "importance": "pouca; prefere não afirmar nada sobre fé", "in_speech": "não aparece",
     "values": ["curiosidade", "tolerância"], "sensitive_topics": ["insistência para converter alguém"],
     "summary": "curiosidade por tradições diferentes, sem afirmar fé"},
    {"affiliation": "umbanda", "practice": "regular", "practices": ["gira no terreiro", "festa de Iemanjá"],
     "importance": "parte da identidade e da família",
     "in_speech": "“axé” com quem é de casa; discrição com desconhecidos",
     "values": ["ancestralidade", "comunidade"], "sensitive_topics": ["intolerância religiosa"],
     "summary": "vai ao terreiro com a família"},
    {"affiliation": "budista", "practice": "regular", "practices": ["meditação diária", "retiro uma vez por ano"],
     "importance": "ajuda a lidar com a ansiedade e o trabalho", "in_speech": "fala em “respirar antes de responder”",
     "values": ["calma", "compaixão"], "sensitive_topics": ["consumo exagerado"], "summary": "medita todo dia"},
    {"affiliation": "católica", "practice": "devota",
     "practices": ["missa todo domingo", "terço em família", "pastoral da juventude"],
     "importance": "central; organiza a semana", "in_speech": "“graças a Deus”, “fica com Deus”",
     "values": ["fé", "família", "serviço"], "sensitive_topics": ["piada com santos"],
     "summary": "participa da pastoral e vai à missa todo domingo"},
    {"affiliation": "ateia", "practice": "nao_pratica", "practices": [],
     "importance": "nenhuma; prefere explicações científicas", "in_speech": "não aparece",
     "values": ["ciência", "autonomia"], "sensitive_topics": ["discussão sobre fé com a família"],
     "summary": "não acredita em Deus, respeita quem tem fé e não entra em debate"},
)
#: A afiliação que muda com o gênero da persona (o resto dos perfis é neutro de propósito).
_AFILIACAO_MASCULINA = {"agnóstica": "agnóstico", "ateia": "ateu"}
_POLITICAS: tuple[dict[str, object], ...] = (
    {"orientation": "esquerda", "engagement": "alto",
     "issues": [{"topic": "transporte público", "stance": "quer tarifa menor e mais linhas"},
                {"topic": "saúde pública", "stance": "defende mais verba para os postos"}],
     "discussion_style": "debate com calma entre amigos; evita briga em comentário",
     "sources": ["jornal online", "podcast de notícias"], "values": ["igualdade", "serviço público"],
     "summary": "acompanha política de perto e vota com convicção"},
    {"orientation": "centro_esquerda", "engagement": "medio",
     "issues": [{"topic": "educação", "stance": "escola pública em tempo integral"},
                {"topic": "meio ambiente", "stance": "a favor de fiscalização firme"}],
     "discussion_style": "fala quando perguntam; prefere dados a slogans",
     "sources": ["jornal local", "newsletter de notícias"], "values": ["oportunidade", "sustentabilidade"],
     "summary": "de centro-esquerda, se informa sem militar"},
    {"orientation": "centro", "engagement": "baixo",
     "issues": [{"topic": "contas públicas", "stance": "gasto com responsabilidade"},
                {"topic": "mobilidade", "stance": "ciclovia sim, sem guerra com o carro"}],
     "discussion_style": "evita o assunto; quando entra, busca o meio-termo",
     "sources": ["telejornal", "portal de notícias"], "values": ["equilíbrio", "pragmatismo"],
     "summary": "de centro, vota pensando na cidade e foge da polarização"},
    {"orientation": "centro_direita", "engagement": "medio",
     "issues": [{"topic": "impostos", "stance": "acha a carga alta para quem empreende"},
                {"topic": "segurança", "stance": "quer mais policiamento no bairro"}],
     "discussion_style": "comenta economia com colegas; não discute em rede social",
     "sources": ["jornal de economia", "rádio"], "values": ["trabalho", "livre iniciativa"],
     "summary": "de centro-direita, liga política a economia e segurança"},
    {"orientation": "direita", "engagement": "alto",
     "issues": [{"topic": "economia", "stance": "menos Estado e mais livre iniciativa"},
                {"topic": "segurança", "stance": "apoia penas mais duras"}],
     "discussion_style": "firme nas opiniões, mas respeita quem discorda",
     "sources": ["portal de notícias", "podcast de economia"], "values": ["ordem", "responsabilidade individual"],
     "summary": "de direita, participa e é firme nas opiniões"},
    {"orientation": "apolitica", "engagement": "nenhum", "issues": [],
     "discussion_style": "muda de assunto quando política aparece", "sources": [], "values": [],
     "summary": "não se interessa por política e não acompanha"},
    {"orientation": "nao_declara", "engagement": "baixo",
     "issues": [{"topic": "bairro", "stance": "quer praça cuidada e rua iluminada"}],
     "discussion_style": "não diz em quem vota; conversa sobre o bairro, não sobre partido",
     "sources": ["grupo do bairro", "jornal local"], "values": ["discrição", "comunidade"],
     "summary": "tem opinião, mas guarda para si"},
)


def _crencas_simuladas(chave: str, genero: str) -> dict[str, object]:
    """Religião e política por uma semente PRÓPRIA (derivada da mesma chave do pedido): acrescentar crenças não
    muda nenhum outro valor sorteado da persona simulada, e pedidos diferentes caem em perfis diferentes."""
    rnd = random.Random(int(hashlib.sha256(f"{chave}|crencas".encode("utf-8")).hexdigest()[:12], 16))
    religiao = copy.deepcopy(dict(rnd.choice(_RELIGIOES)))
    if genero == "masculino":
        afiliacao = str(religiao["affiliation"])
        religiao["affiliation"] = _AFILIACAO_MASCULINA.get(afiliacao, afiliacao)
    return {"religion": religiao, "politics": copy.deepcopy(dict(rnd.choice(_POLITICAS)))}


def persona_simulada(req: PersonaGenerationRequest) -> PersonaDraft:
    """A mesma persona para o mesmo pedido (semente = sha256 do prompt, idioma e restrições). Com `existing`, só o
    que está vazio é preenchido — é o contrato de `enrich`. Sempre adulta; nunca um segredo em campo nenhum.

    No lote, `variation` entra na semente (cada item é outra pessoa, ainda determinística) e o nome não repete
    ninguém de `avoid` — o que o modelo real é instruído a fazer. Sem lote, a semente é a de sempre."""
    chave = f"{req.prompt}|{req.locale}|{sorted(req.constraints.items())}"
    if req.variation is not None:
        chave += f"|{req.variation}"
    rnd = random.Random(int(hashlib.sha256(chave.encode("utf-8")).hexdigest()[:12], 16))
    hoje = req.today or date.today()
    genero = (req.constraints.get("gender") or "").strip().lower()
    if genero not in _NOMES:
        genero = rnd.choice(sorted(_NOMES))
    idade = _idade_pedida(req.constraints.get("age"), rnd)
    nascimento = hoje.replace(year=hoje.year - idade, day=min(hoje.day, 28))
    nascimento = nascimento.replace(month=max(1, nascimento.month - rnd.randint(0, nascimento.month - 1)))
    nome, sobrenome = rnd.choice(_NOMES[genero]), rnd.choice(_SOBRENOMES)
    if req.avoid and nome_repetido(f"{nome} {sobrenome}", req.avoid) is not None:
        nome, sobrenome = _outro_nome(chave, genero, req.avoid) or (nome, sobrenome)
    cidade, uf = rnd.choice(_CIDADES)
    origem, _uf_origem = rnd.choice([c for c in _CIDADES if c[0] != cidade])
    oficio, curso = rnd.choice(_OFICIOS)
    hobbies = rnd.sample(_HOBBIES, 3)
    tom = rnd.choice(_TONS)
    completo = f"{nome} {sobrenome}"
    rascunho = {
        "name": completo, "gender": genero, "locale": req.locale, "birth_date": nascimento.isoformat(),
        "summary": f"{completo}, {idade} anos, {oficio} em {cidade}. Gosta de {hobbies[0]} e {hobbies[1]}.",
        "persona_prompt": f"Escreva como {nome}: tom {tom}, frases curtas, sem clichê e sem prometer nada.",
        "traits": {
            "personality": f"{tom}, observadora do detalhe, fala do que vive", "tone": tom,
            "formality": rnd.choice(["informal", "neutro"]), "typical_length": rnd.choice(["curta", "media"]),
            "emojis": rnd.choice(["nunca", "raro", "moderado"]),
            "slang": rnd.choice(["quase nenhuma", "um 'valeu' de vez em quando", "gíria regional leve"]),
            "humor": rnd.choice(["seco", "leve", "autodepreciativo"]), "interests": hobbies,
            "dm_style": "abre com uma frase só e pergunta algo concreto no fim",
            "comment_style": "um detalhe da foto ou do texto, nunca elogio genérico",
            "with_known": "corta a saudação e usa o primeiro nome", "with_strangers": "educada e breve",
            "examples": [f"que {hobbies[0]} boa foi essa!", "conta mais disso?"],
            "common_phrases": ["boa!", "fechou"], "forbidden_phrases": ["arrasou", "top demais"],
        },
        "visual": {
            "appearance": rnd.choice(["cabelo curto escuro", "cabelo cacheado castanho", "cabelo liso claro, óculos"])
                          + ", " + rnd.choice(["estatura média", "alta", "baixa"]),
            "visual_style": rnd.choice(["casual de linho", "esportivo discreto", "jeans e camiseta lisa"]),
            "photo_scenario": rnd.choice(["varanda com plantas", "café de bairro", "trilha no fim de tarde"]),
            "palette": rnd.choice(["terrosa", "neutra", "azul e areia"]),
        },
        "biography": {
            "schema_version": BIOGRAPHY_SCHEMA_VERSION,
            "origin": {"birthplace": origem, "nationality": "brasileira"},
            "home": {"city": cidade, "state": uf, "country": "Brasil",
                     "residence": rnd.choice(["apartamento pequeno", "casa com quintal", "kitnet perto do trabalho"])},
            "work": {"profession": oficio, "education": [f"{curso} (graduação)"]},
            "life": {"marital_status": rnd.choice(["solteira", "casada", "namorando"]), "children": 0,
                     "history": [f"mudou-se de {origem} para {cidade} para trabalhar"]},
            "beliefs": _crencas_simuladas(chave, genero),
            "tastes": {"interests": hobbies, "hobbies": hobbies[:2],
                       "preferences": [rnd.choice(["café coado", "chá gelado"]), "manhã cedo"],
                       "dislikes": [rnd.choice(["barulho de trânsito", "fila", "reunião longa"])]},
        },
    }
    if req.existing is not None:
        rascunho = preencher_vazios(req.existing, rascunho)
    return PersonaDraft.model_validate(rascunho)


def _outro_nome(chave: str, genero: str, evitar: tuple[PersonaEvitada, ...]) -> tuple[str, str] | None:
    """Um nome que não está em `evitar`, por um sorteio PRÓPRIO: trocar o nome não mexe no resto da pessoa (o
    sorteio principal segue a mesma sequência). `None` só se todas as combinações já existem."""
    rnd = random.Random(int(hashlib.sha256(f"{chave}|nome".encode("utf-8")).hexdigest()[:12], 16))
    combinacoes = [(n, s) for n in _NOMES[genero] for s in _SOBRENOMES]
    rnd.shuffle(combinacoes)
    return next(((n, s) for n, s in combinacoes if nome_repetido(f"{n} {s}", evitar) is None), None)


def _idade_pedida(texto: str | None, rnd: random.Random) -> int:
    """`"30-35"` → uma idade na faixa; `"40"` → 40; vazio → sorteio dentro da faixa padrão. Sempre adulta."""
    achados = [int(n) for n in re.findall(r"\d{2}", texto or "")]
    if len(achados) >= 2:
        idade = rnd.randint(min(achados[0], achados[1]), max(achados[0], achados[1]))
    elif achados:
        idade = achados[0]
    else:
        idade = rnd.randint(IDADE_MINIMA_GERADA, IDADE_MAXIMA_GERADA)
    return max(IDADE_MINIMA_GERADA, min(idade, 90))
