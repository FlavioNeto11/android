"""Serviço de perfis: cadastro, credencial protegida e vínculo com aparelho.

A senha entra por aqui, vai direto para o cofre e nunca mais aparece: não há método que a devolva, e o DTO não tem
campo para ela. Quem precisa do valor é o canal de entrada sensível, no instante da digitação.
"""
from __future__ import annotations

import logging
from typing import Any

from ..db import dumps
from ..events import EventBus
from ..models import (InstagramProfileDTO, InteractionDTO, InteractionStatus, InteractionType, MemoryItemDTO,
                      PersonaDTO, SessionStatus, SocialContextDTO, SocialDraftDTO)
from ..planning.provider import AIError, SocialRequest
from ..security.redaction import looks_secret, mentions_credential, redact, redact_obj
from ..security.secret_store import SecretStore, SecretStoreLocked, SecretStoreUnavailable
from .context import SocialContextBuilder, interaction_dto, persona_dto
from .memory import MemoryRefused, MemoryStore
from .repository import SocialRepository

log = logging.getLogger("poc.social")

# Quantas interações o contexto mostra inteiras. Acima disso, a conversa ganha uma nota dizendo que há mais.
_RECENTES_NO_CONTEXTO = 6


class SocialError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class SocialService:
    def __init__(self, repo: SocialRepository, secrets: SecretStore, bus: EventBus,
                 known_instances: Any = None, *, provider: Any = None, usage_sink: Any = None):
        self.repo = repo
        self.secrets = secrets
        self.bus = bus
        self._known_instances = known_instances or (lambda: [])
        self.memory = MemoryStore(repo)
        self.contexts = SocialContextBuilder(repo, self.memory)
        self.provider = provider        # só a geração social usa; cadastro e sessão não dependem de IA
        self.usage_sink = usage_sink    # registra o custo da função social no mesmo relatório das demais

    # ------------------------------------------------------------------ consulta
    def list_profiles(self) -> list[InstagramProfileDTO]:
        return [dto for pid in self.repo.list_profile_ids() if (dto := self.repo.profile_dto(pid))]

    def get_profile(self, profile_id: str) -> InstagramProfileDTO:
        dto = self.repo.profile_dto(profile_id)
        if dto is None:
            raise SocialError("not_found", "Perfil não encontrado.", 404)
        return dto

    def instance_of(self, profile_id: str) -> str | None:
        """Aparelho vinculado a este perfil agora. Usado para executar POR PERFIL, sem o usuário saber de emulador."""
        row = self.repo.binding_row(profile_id)
        return row["instance_id"] if row else None

    def profile_of(self, instance_id: str) -> str | None:
        return self.repo.profile_id_for_instance(instance_id)

    def profile_for_instance(self, instance_id: str) -> InstagramProfileDTO | None:
        pid = self.repo.profile_id_for_instance(instance_id)
        return self.repo.profile_dto(pid) if pid else None

    # ------------------------------------------------------------------ cadastro
    def create_profile(self, body: Any) -> InstagramProfileDTO:
        if self.repo.profile_by_username(body.username):
            raise SocialError("duplicate_username", f"Já existe um perfil para @{body.username}.")
        self._check_persona(body.persona_id)
        self._check_instance(body.instance_id)
        if body.password and self.secrets.status() != "ready":
            raise SocialError("secret_store_unavailable", self._vault_message(), 503)

        profile_id = self.repo.create_profile(
            username=body.username, first_name=body.first_name, last_name=body.last_name,
            display_name=body.display_name or (f"{body.first_name or ''} {body.last_name or ''}".strip() or None),
            birth_date=body.birth_date, email=body.email, persona_id=body.persona_id)
        if body.instance_id:
            self.repo.bind(profile_id, body.instance_id, reason="cadastro")
        if body.password:
            self._store_password(profile_id, body.login_identifier or body.username, body.password)
        self.repo.set_session(profile_id, status=SessionStatus.unknown,
                              instance_id=body.instance_id, detail="Perfil recém-cadastrado; sessão ainda não verificada.")
        self.bus.emit("log", f"Perfil @{body.username} cadastrado"
                             + (f" e vinculado a {body.instance_id}" if body.instance_id else ""),
                      data={"profile_id": profile_id})
        return self.get_profile(profile_id)

    def update_profile(self, profile_id: str, body: Any) -> InstagramProfileDTO:
        self.get_profile(profile_id)
        fields = body.model_dump(exclude_unset=True, exclude_none=False)
        instance_id = fields.pop("instance_id", "__ausente__")
        if "persona_id" in fields and fields["persona_id"]:
            self._check_persona(fields["persona_id"], para=profile_id)
        if fields:
            self.repo.update_profile(profile_id, fields)
        if instance_id != "__ausente__":
            self._rebind(profile_id, instance_id)
        return self.get_profile(profile_id)

    def delete_profile(self, profile_id: str) -> None:
        """Apagar o perfil apaga a credencial junto — inclusive o ciphertext no cofre."""
        self.get_profile(profile_id)
        ref = self.repo.delete_credential(profile_id)
        if ref:
            self.secrets.delete_secret(ref)
        self.repo.delete_profile(profile_id)
        self.bus.emit("log", "Perfil removido, com a credencial apagada do cofre", data={"profile_id": profile_id})

    # ------------------------------------------------------------------ credencial (só escrita)
    def set_credential(self, profile_id: str, body: Any) -> InstagramProfileDTO:
        dto = self.get_profile(profile_id)
        if self.secrets.status() != "ready":
            raise SocialError("secret_store_unavailable", self._vault_message(), 503)
        self._store_password(profile_id, body.login_identifier or dto.username, body.password)
        # Senha nova zera o bloqueio por credencial inválida: é exatamente o que destrava a automação.
        self.bus.emit("log", f"Credencial de @{dto.username} atualizada", data={"profile_id": profile_id})
        return self.get_profile(profile_id)

    def delete_credential(self, profile_id: str) -> InstagramProfileDTO:
        self.get_profile(profile_id)
        ref = self.repo.delete_credential(profile_id)
        if ref:
            self.secrets.delete_secret(ref)
        return self.get_profile(profile_id)

    def _store_password(self, profile_id: str, login_identifier: str, password: Any) -> None:
        """A senha existe como texto apenas nestas linhas, e some junto com o quadro da função."""
        try:
            ref = self.secrets.store_secret(password.get_secret_value())
        except (SecretStoreLocked, SecretStoreUnavailable) as exc:
            raise SocialError("secret_store_unavailable", str(exc), 503) from None
        self.repo.set_credential(profile_id, login_identifier=login_identifier, secret_ref=ref,
                                 key_id=self.secrets.provider.key_id)

    # ------------------------------------------------------------------ vínculo
    def _rebind(self, profile_id: str, instance_id: str | None) -> None:
        current = self.repo.binding_row(profile_id)
        if instance_id is None:
            if current:
                self.repo.unbind(profile_id, reason="desvinculado pelo usuário")
            return
        self._check_instance(instance_id)
        if current and current["instance_id"] == instance_id:
            return
        self.repo.bind(profile_id, instance_id, reason="troca de aparelho")
        # Aparelho novo, sessão nova: persona, memória e histórico continuam com o PERFIL.
        self.repo.set_session(profile_id, status=SessionStatus.unknown, instance_id=instance_id,
                              detail="Aparelho trocado; a sessão precisa ser verificada de novo.")

    def _check_instance(self, instance_id: str | None) -> None:
        if not instance_id:
            return
        known = list(self._known_instances())
        if known and instance_id not in known:
            raise SocialError("unknown_instance", f"Aparelho desconhecido: {instance_id}.", 400)

    # ------------------------------------------------------------------ personas
    def list_personas(self) -> list[PersonaDTO]:
        return [self._persona_dto(self.repo.persona_row(p["id"])) for p in self.repo.list_personas()]

    def get_persona(self, persona_id: str) -> PersonaDTO:
        row = self.repo.persona_row(persona_id)
        if row is None:
            raise SocialError("not_found", "Persona não encontrada.", 404)
        return self._persona_dto(row)

    def create_persona(self, body: Any) -> PersonaDTO:
        persona_id = self.repo.create_persona(name=body.name, summary=body.summary,
                                              persona_prompt=body.persona_prompt,
                                              traits=body.traits.model_dump(exclude_none=True))
        return self.get_persona(persona_id)

    def update_persona(self, persona_id: str, body: Any) -> PersonaDTO:
        self.get_persona(persona_id)
        fields = body.model_dump(exclude_unset=True)
        if fields.get("traits") is not None:
            fields["traits"] = dumps(body.traits.model_dump(exclude_none=True))
        # Num PATCH, nulo nestes campos significa "não mexer": a coluna é NOT NULL e apagar o nome não é um pedido.
        fields = {k: v for k, v in fields.items() if not (v is None and k in ("name", "persona_prompt", "traits"))}
        self.repo.update_persona(persona_id, fields)
        return self.get_persona(persona_id)

    def delete_persona(self, persona_id: str) -> None:
        self.get_persona(persona_id)
        dono = self.repo.persona_owner(persona_id)
        if dono:
            raise SocialError("persona_in_use", "Esta persona está em uso por um perfil. Desvincule antes de apagar.")
        self.repo.delete_persona(persona_id)

    def _persona_dto(self, row: Any) -> PersonaDTO:
        dono = self.repo.persona_owner(row["id"])
        username = self.repo.profile_row(dono)["username"] if dono else None
        return persona_dto(row, profile_id=dono, profile_username=username)

    def _check_persona(self, persona_id: str | None, *, para: str | None = None) -> None:
        """Persona pertence a UM perfil. O esquema garante; aqui a recusa vira mensagem em vez de erro de banco."""
        if not persona_id:
            return
        if not self.repo.persona_exists(persona_id):
            raise SocialError("unknown_persona", "Persona não encontrada.", 400)
        dono = self.repo.persona_owner(persona_id)
        if dono and dono != para:
            raise SocialError("persona_in_use", "Esta persona já pertence a outro perfil. Cada perfil tem a sua.")

    # ------------------------------------------------------------------ histórico social
    def record_interaction(self, profile_id: str, **campos: Any) -> InteractionDTO:
        """Toda escrita de histórico passa por aqui, e por isso passa pelo filtro: o que fala de credencial não
        chega ao banco, mesmo tendo sido lido da tela."""
        self.get_profile(profile_id)
        for chave in ("incoming_content", "outgoing_content", "evidence"):
            if campos.get(chave):
                campos[chave] = _conteudo_seguro(campos[chave])
        for chave in ("context", "metadata"):
            if campos.get(chave):
                campos[chave] = redact_obj(campos[chave])
        if campos.get("counterparty"):
            campos["counterparty"] = _counterparty(campos["counterparty"])
        interaction_id = self.repo.record_interaction(profile_id, **campos)
        return self.get_interaction(profile_id, interaction_id)

    def get_interaction(self, profile_id: str, interaction_id: str) -> InteractionDTO:
        row = self.repo.interaction_row(profile_id, interaction_id)
        if row is None:
            raise SocialError("not_found", "Interação não encontrada.", 404)
        return interaction_dto(row)

    def list_interactions(self, profile_id: str, *, counterparty: str | None = None, thread_key: str | None = None,
                          limit: int = 30) -> list[InteractionDTO]:
        self.get_profile(profile_id)
        return [interaction_dto(r) for r in self.repo.list_interactions(
            profile_id, counterparty=_counterparty(counterparty) if counterparty else None,
            thread_key=thread_key, limit=limit)]

    def confirm_interaction(self, profile_id: str, interaction_id: str, *, evidence: str | None = None,
                            outgoing_content: str | None = None) -> InteractionDTO:
        """Confirmação é o ÚNICO caminho que vira fato: atualiza relacionamento, conversa e memória."""
        row = self.repo.interaction_row(profile_id, interaction_id)
        if row is None:
            raise SocialError("not_found", "Interação não encontrada.", 404)
        campos: dict[str, Any] = {"status": InteractionStatus.confirmed.value}
        if evidence:
            campos["evidence"] = _conteudo_seguro(evidence)
        if outgoing_content:
            campos["outgoing_content"] = _conteudo_seguro(outgoing_content)
        self.repo.update_interaction(profile_id, interaction_id, **campos)
        if row["counterparty"]:
            self.repo.upsert_relationship(profile_id, row["counterparty"], bump=True,
                                          last_interaction_at=row["occurred_at"],
                                          summary=self._nota_de_relacionamento(profile_id, row["counterparty"]))
        if row["thread_key"]:
            self.repo.upsert_thread(profile_id, row["thread_key"], counterparty=row["counterparty"], bump=True,
                                    last_message_at=row["occurred_at"],
                                    summary=self._nota_de_conversa(profile_id, row["thread_key"]))
        aprendidas = self.memory.learn_from(profile_id, interaction_id)
        if aprendidas:
            log.info("perfil %s aprendeu %d fato(s) da interação %s", profile_id, len(aprendidas), interaction_id)
        return self.get_interaction(profile_id, interaction_id)

    def close_interaction(self, profile_id: str, interaction_id: str, *, status: InteractionStatus,
                          evidence: str | None = None) -> InteractionDTO:
        """Falha, incerteza e cancelamento fecham a interação SEM ensinar nada. É o ponto do §12."""
        if status == InteractionStatus.confirmed:
            raise SocialError("invalid_status", "Use confirm_interaction para confirmar.", 400)
        self.get_interaction(profile_id, interaction_id)
        campos: dict[str, Any] = {"status": status.value}
        if evidence:                       # sem evidência nova, a que já existia continua valendo
            campos["evidence"] = _conteudo_seguro(evidence)
        self.repo.update_interaction(profile_id, interaction_id, **campos)
        return self.get_interaction(profile_id, interaction_id)

    def _nota_de_relacionamento(self, profile_id: str, counterparty: str) -> str:
        """Nota FACTUAL, não redigida por modelo: quantas interações e desde quando.

        O `tone` continua sendo o campo de quem escreve orientação (operador hoje, resumo de IA depois); o `summary`
        é contagem, e por isso pode ser reescrito a cada confirmação sem apagar nada que alguém tenha escrito.
        """
        linhas = self.repo.list_interactions(profile_id, counterparty=counterparty,
                                             status=InteractionStatus.confirmed.value, limit=500)
        if not linhas:
            return ""
        return (f"{len(linhas)} interações confirmadas com {counterparty}, de {linhas[-1]['occurred_at'][:10]} "
                f"a {linhas[0]['occurred_at'][:10]}.")

    def _nota_de_conversa(self, profile_id: str, thread_key: str) -> str:
        linhas = self.repo.list_interactions(profile_id, thread_key=thread_key,
                                             status=InteractionStatus.confirmed.value, limit=500)
        if len(linhas) <= _RECENTES_NO_CONTEXTO:
            return ""     # tudo o que aconteceu já aparece em "interações recentes"; resumir seria repetir
        return (f"{len(linhas)} mensagens confirmadas nesta conversa desde {linhas[-1]['occurred_at'][:10]}; "
                "acima aparecem apenas as mais recentes.")

    # ------------------------------------------------------------------ efeito externo visto pelo motor
    def open_effect(self, profile_id: str, *, capability: str, interaction_type: str, bindings: dict[str, str],
                    run_id: str | None = None, objective_id: str | None = None, step_id: str | None = None,
                    instance_id: str | None = None) -> str:
        """Registra a INTENÇÃO de um efeito externo, no instante em que ele é disparado.

        Nasce `pending` de propósito: uma ação disparada cujo resultado ainda não foi observado já mexeu com a conta
        e já conta para os limites. Fingir que não aconteceu seria a maneira mais fácil de estourar o limite real.
        """
        alvo = bindings.get("username") or bindings.get("target")
        return self.record_interaction(
            profile_id, type=interaction_type, direction="outbound", status=InteractionStatus.pending.value,
            counterparty=alvo, thread_key=f"dm:{_counterparty(alvo)}" if interaction_type == "dm_sent" and alvo
            else None,
            outgoing_content=bindings.get("content"), target=bindings.get("target"), run_id=run_id,
            objective_id=objective_id, step_id=step_id, instance_id=instance_id,
            metadata={"capability": capability}).id

    def settle_effect(self, profile_id: str, interaction_id: str, *, outcome: str,
                      evidence: str | None = None) -> None:
        """Fecha a interação pelo que foi OBSERVADO. Só `succeeded` vira fato — e só fato ensina memória."""
        mapa = {"succeeded": InteractionStatus.confirmed, "failed": InteractionStatus.failed,
                "uncertain": InteractionStatus.uncertain, "cancelled": InteractionStatus.cancelled}
        estado = mapa.get(outcome, InteractionStatus.uncertain)
        try:
            if estado == InteractionStatus.confirmed:
                self.confirm_interaction(profile_id, interaction_id, evidence=evidence)
            else:
                self.close_interaction(profile_id, interaction_id, status=estado, evidence=evidence)
        except SocialError:
            log.warning("interação %s não pôde ser fechada (%s)", interaction_id, outcome)

    def reconcile_pending_effects(self) -> int:
        """Na partida: efeito disparado cujo desfecho nunca foi observado vira INCERTO, nunca confirmado.

        Um `pending` eterno contaria para sempre nos limites e, pior, poderia ser confundido com sucesso. Incerto é
        o que ele realmente é — e incerto não vira memória.
        """
        abertas = self.repo.db.query(
            "SELECT id, profile_id FROM social_interactions WHERE status=? AND direction='outbound'",
            (InteractionStatus.pending.value,))
        for linha in abertas:
            self.close_interaction(linha["profile_id"], linha["id"], status=InteractionStatus.uncertain,
                                   evidence="o backend reiniciou antes de observar o resultado desta ação")
        if abertas:
            log.warning("%d efeito(s) sem desfecho observado marcados como incertos na partida", len(abertas))
        return len(abertas)

    # ------------------------------------------------------------------ memória
    def list_memories(self, profile_id: str, *, subject: str | None = None, limit: int = 100) -> list[MemoryItemDTO]:
        self.get_profile(profile_id)
        return self.memory.list(profile_id, subject=subject, limit=limit)

    def add_memory(self, profile_id: str, body: Any) -> MemoryItemDTO:
        self.get_profile(profile_id)
        try:
            return self.memory.remember(profile_id, subject=body.subject, content=body.content, source="operator",
                                        importance=body.importance, confidence=body.confidence,
                                        expires_at=body.expires_at)
        except MemoryRefused as exc:
            raise SocialError("memory_refused", str(exc), 400) from None

    def delete_memory(self, profile_id: str, memory_id: str) -> None:
        self.get_profile(profile_id)
        if not self.memory.forget(profile_id, memory_id):
            raise SocialError("not_found", "Lembrança não encontrada.", 404)

    # ------------------------------------------------------------------ contexto e geração
    def context(self, profile_id: str, *, counterparty: str | None = None, thread_key: str | None = None,
                current_content: str | None = None, touch: bool = False) -> SocialContextDTO:
        self.get_profile(profile_id)
        return self.contexts.build(profile_id, counterparty=counterparty, thread_key=thread_key,
                                   current_content=current_content, touch=touch)

    async def draft_response(self, profile_id: str, *, kind: str, incoming: str, counterparty: str | None = None,
                             thread_key: str | None = None, max_length: int = 300,
                             persist: bool = True) -> tuple[SocialDraftDTO, InteractionDTO | None]:
        """Gera a resposta e a REGISTRA antes de qualquer envio (§16). Nada é enviado aqui: quem envia é o executor."""
        dto = self.get_profile(profile_id)
        ctx = self.context(profile_id, counterparty=counterparty, thread_key=thread_key, current_content=incoming,
                           touch=True)
        draft, _usage = await self._generate(SocialRequest(
            profile_id=profile_id, username=dto.username, kind=kind, context_text=ctx.rendered,
            incoming=incoming, counterparty=_counterparty(counterparty) if counterparty else None,
            max_length=max_length))
        if not persist:
            return draft, None
        interacao = self.record_interaction(
            profile_id, type=(InteractionType.dm_sent.value if kind == "dm_reply"
                              else InteractionType.comment_replied.value),
            direction="outbound", status=InteractionStatus.pending.value, counterparty=counterparty,
            thread_key=thread_key, incoming_content=incoming, outgoing_content=draft.content,
            metadata={"memory_candidates": [c.model_dump() for c in draft.memory_candidates],
                      "refused": draft.refused, "rationale": draft.rationale})
        return draft, interacao

    async def preview_persona(self, persona_id: str, body: Any) -> SocialDraftDTO:
        """Testar Persona: gera um exemplo e não grava nada — nem interação, nem memória, nem uso do aparelho."""
        persona = self.get_persona(persona_id)
        profile_id = body.profile_id or persona.profile_id
        if profile_id:
            perfil = self.get_profile(profile_id)
            if perfil.persona_id != persona_id:
                raise SocialError("persona_mismatch", "Esta persona não é a do perfil informado.", 400)
            contexto = self.context(profile_id, counterparty=body.counterparty, current_content=body.incoming)
            texto, username = contexto.rendered, perfil.username
        else:
            texto = self.contexts.render_persona_only(persona)
            username = persona.name
        draft, _usage = await self._generate(SocialRequest(
            profile_id=profile_id or "", username=username, kind=body.kind, context_text=texto,
            incoming=body.incoming, counterparty=_counterparty(body.counterparty) if body.counterparty else None,
            preview=True))
        return draft

    async def _generate(self, req: SocialRequest) -> tuple[SocialDraftDTO, Any]:
        if self.provider is None:
            raise SocialError("ai_unavailable", "Nenhum provedor de IA disponível para gerar resposta.", 503)
        try:
            draft, usage = await self.provider.generate_social_response(req)
        except AIError as exc:
            raise SocialError("ai_error", str(exc), 503) from None
        if self.usage_sink is not None:
            try:
                self.usage_sink(usage)
            except Exception:  # noqa: BLE001 - contabilidade de custo nunca derruba a geração
                log.exception("falha ao registrar o uso da geração social")
        return draft, usage

    def _vault_message(self) -> str:
        status = self.secrets.status()
        if status == "locked":
            return ("O cofre de credenciais está travado nesta máquina/usuário. As credenciais cifradas foram "
                    "preservadas; recadastre-as para voltar a usar autenticação automática.")
        return ("Não há chave mestra disponível para proteger credenciais. Defina "
                "INSTAGRAM_CREDENTIALS_MASTER_KEY no .env ou rode num usuário com DPAPI disponível.")


def _counterparty(valor: str | None) -> str | None:
    """Contraparte sempre no mesmo formato: `@nome` em minúsculas. Sem isto, `@Ana` e `ana` virariam duas pessoas."""
    if not valor:
        return None
    limpo = valor.strip().lower().lstrip("@")
    return f"@{limpo}" if limpo else None


def _conteudo_seguro(texto: str) -> str:
    """Filtro de escrita do histórico. Duas camadas, nesta ordem:

    1. `redact` mascara o que tem formato conhecido de credencial;
    2. se ainda assim o texto fala de senha, código ou token, ele NÃO é guardado — vira uma marca.

    A segunda camada é a que importa: o histórico volta ao modelo em `<interacoes_recentes>`, então guardar
    "o código é 481922" seria reapresentar o código a cada conversa. Perde-se o texto exato de mensagens que falam
    de credencial; é o preço, e é barato perto de vazar um código.
    """
    limpo = redact(texto) or ""
    if looks_secret(limpo) or mentions_credential(limpo):
        return "[conteúdo omitido: menciona credencial, código ou token]"
    return limpo
