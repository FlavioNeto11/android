"""Serviço de perfis: cadastro, credencial protegida e vínculo com aparelho.

A senha entra por aqui, vai direto para o cofre e nunca mais aparece: não há método que a devolva, e o DTO não tem
campo para ela. Quem precisa do valor é o canal de entrada sensível, no instante da digitação.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from ..db import dumps, loads
from ..events import EventBus
from ..models import (InstagramProfileDTO, InteractionDTO, InteractionStatus, InteractionType, MemoryItemDTO,
                      PersonaDTO, ProfilePolicyDTO, SessionStatus, SocialContextDTO, SocialDraftDTO)
from ..planning.capabilities import load_catalog
from ..planning.catalog import package_of_provider
from ..planning.provider import AIError, SocialRequest
from ..security.redaction import looks_secret, mentions_credential, redact, redact_obj
from ..security.secret_store import SecretStore, SecretStoreLocked, SecretStoreUnavailable
from .context import SocialContextBuilder, interaction_dto, persona_dto
from .memory import MemoryRefused, MemoryStore
from .policy import CONTAM, DEFAULT_LIMITS, PolicyEngine
from .repository import SocialRepository

log = logging.getLogger("poc.social")

# Quantas interações o contexto mostra inteiras. Acima disso, a conversa ganha uma nota dizendo que há mais.
_RECENTES_NO_CONTEXTO = 6
# Quantos textos anteriores do próprio perfil entram na lista de "não repita".
_TEXTOS_ANTERIORES = 8
# Quantas falas o resumo da conversa cita, e com que tamanho cada uma. O resumo entra no prompt de toda geração
# daquele fio: é o bloco que mais se repete, e por isso o que mais precisa caber.
_FALAS_NO_RESUMO = 6
_FALA_MAX_CHARS = 160
# Quantos textos próprios são comparados ao ler uma conversa, para não gravar como fala da outra pessoa o que
# esta conta escreveu. Mais que a lista de "não repita": uma conversa longa mostra várias mensagens nossas.
_TEXTOS_PROPRIOS_NA_CONVERSA = 30
_SO_PALAVRAS = re.compile(r"[^\w\s]+", re.UNICODE)
_ESPACOS = re.compile(r"\s+")


def _normalizar(texto: str) -> str:
    """Duas frases que só diferem em acento, caixa, pontuação ou espaço são a MESMA frase para quem lê o feed."""
    sem_acento = "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")
    return _ESPACOS.sub(" ", _SO_PALAVRAS.sub(" ", sem_acento.casefold())).strip()


def _sem_repetir(textos: Sequence[str]) -> list[str]:
    """Lista de proibidos sem duplicatas e sem vazios, na ordem em que apareceram."""
    vistos: set[str] = set()
    saida: list[str] = []
    for t in textos:
        limpo = (t or "").strip()
        chave = _normalizar(limpo)
        if not chave or chave in vistos:
            continue
        vistos.add(chave)
        saida.append(limpo)
    return saida


def _repetido(draft: SocialDraftDTO, proibidos: Sequence[str]) -> bool:
    if draft.refused or not (draft.content or "").strip():
        return False                                    # recusa tem tratamento próprio; repetir não é o problema
    alvo = _normalizar(draft.content)
    return any(alvo == _normalizar(p) for p in proibidos)


class SocialError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class SocialService:
    def __init__(self, repo: SocialRepository, secrets: SecretStore, bus: EventBus,
                 known_instances: Any = None, *, provider: Any = None, usage_sink: Any = None,
                 store_instance: Any = None, owner_id: str | None = None):
        self.repo = repo
        #: Quem sou eu para a reconciliação de partida (item 5.1). `None` = não filtra por hospedeiro.
        self.owner_id = owner_id
        self.secrets = secrets
        self.bus = bus
        self._known_instances = known_instances or (lambda: [])
        self._store_instance = store_instance or (lambda: None)     # id do aparelho-loja, que nunca recebe perfil
        self.memory = MemoryStore(repo)
        self.contexts = SocialContextBuilder(repo, self.memory)
        self.provider = provider        # só a geração social usa; cadastro e sessão não dependem de IA
        self.policies = PolicyEngine(repo)
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
            # O e-mail é o identificador que o Instagram aceita sempre; o @usuário pode nem resolver (visto no
            # aparelho: login por @usuário devolvia "Unable to log in" e por e-mail entrava).
            self._store_password(profile_id, body.login_identifier or body.email or body.username, body.password)
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
        # Confirmação é DECISÃO, não campo do perfil: nunca vai para o `UPDATE`.
        confirmado = bool(fields.pop("confirm_locality_change", False))
        if "persona_id" in fields and fields["persona_id"]:
            self._check_persona(fields["persona_id"], para=profile_id)
        if fields:
            self.repo.update_profile(profile_id, fields)
        if instance_id != "__ausente__":
            self._rebind(profile_id, instance_id, confirmado=confirmado)
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
        self._store_password(profile_id, body.login_identifier or dto.email or dto.username, body.password)
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
        """A senha existe como texto apenas nestas linhas, e some junto com o quadro da função.

        Reusa a referência que o perfil já tem: `store_secret` sobrescreve no lugar (nonce novo, mesma AAD). Gerar
        referência nova a cada troca deixaria o texto cifrado ANTERIOR órfão no cofre para sempre — e nem apagar o
        perfil o removeria, porque `delete_credential` só apaga a referência atual.
        """
        atual = self.repo.credential_row(profile_id)
        ref_atual = atual["secret_ref"] if atual else None
        try:
            ref = self.secrets.store_secret(password.get_secret_value(), ref=ref_atual)
        except (SecretStoreLocked, SecretStoreUnavailable) as exc:
            raise SocialError("secret_store_unavailable", str(exc), 503) from None
        self.repo.set_credential(profile_id, login_identifier=login_identifier, secret_ref=ref,
                                 key_id=self.secrets.provider.key_id)

    # ------------------------------------------------------------------ vínculo
    def _rebind(self, profile_id: str, instance_id: str | None, *, confirmado: bool = False) -> None:
        current = self.repo.binding_row(profile_id)
        if instance_id is None:
            if current:
                self.repo.unbind(profile_id, reason="desvinculado pelo usuário")
            return
        self._check_instance(instance_id)
        if current and current["instance_id"] == instance_id:
            return
        self._recusar_troca_de_servidor(profile_id, current, instance_id, confirmado=confirmado)
        self.repo.bind(profile_id, instance_id, reason="troca de aparelho")
        # Aparelho novo, sessão nova: persona, memória e histórico continuam com o PERFIL.
        self.repo.set_session(profile_id, status=SessionStatus.unknown, instance_id=instance_id,
                              detail="Aparelho trocado; a sessão precisa ser verificada de novo.")

    def _recusar_troca_de_servidor(self, profile_id: str, atual: Any, destino: str, *, confirmado: bool) -> None:
        """Mudar de SERVIDOR um perfil com sessão pronta é perder o acesso a ela (item 4.4 / E9).

        Os dados da sessão ficam no disco da máquina antiga; no aparelho novo a conta simplesmente não está
        logada. Trocar de aparelho DENTRO do mesmo servidor não é este caso — ali o perfil continua na máquina
        onde vive, e a sessão só precisa ser verificada de novo, como já acontecia.

        Recusa com 409 a menos que a pessoa confirme, ou que a política do perfil já autorize reautenticar em
        outro servidor. É a decisão de pessoa que o achado #45 pede, e não um aviso que passa batido.
        """
        if confirmado or atual is None or atual["locality_at"] is None:
            return
        destino_worker, _ = self.repo.localidade_da_instancia(destino)
        if destino_worker == atual["worker_id"]:
            return
        linha = self.repo.profile_row(profile_id)
        if (linha["offline_policy"] if linha is not None else None) == "reauth_elsewhere":
            return
        sessao = self.repo.session_row(profile_id)
        if sessao is None or sessao["status"] != SessionStatus.session_ready.value:
            return
        onde = atual["worker_id"] or "este servidor"
        para = destino_worker or "este servidor"
        raise SocialError(
            "locality_change_requires_confirmation",
            f"Os dados deste perfil vivem em {onde} e {destino} está em {para}. A sessão do Instagram não "
            f"acompanha a troca: no aparelho novo será preciso entrar na conta de novo. Confirme a mudança de "
            f"servidor para prosseguir.", 409)

    def _check_instance(self, instance_id: str | None) -> None:
        if not instance_id:
            return
        known = list(self._known_instances())
        if known and instance_id not in known:
            raise SocialError("unknown_instance", f"Aparelho desconhecido: {instance_id}.", 400)
        if instance_id == self._store_instance():
            # Dizer "desconhecido" seria mentira: o aparelho existe, só não é de tarefa. Um perfil vinculado à loja
            # mandaria execução para a Play Store pelo caminho `profile_ids`.
            raise SocialError("store_instance", f"{instance_id} é a loja (Play Store): ela só guarda o aplicativo "
                                                "oficial e não recebe perfil. Escolha um aparelho do parque.", 400)

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
        """O que já foi dito nesta conversa ANTES do que o contexto mostra inteiro.

        Era só uma contagem ("8 mensagens confirmadas"), que não dá continuidade nenhuma: o perfil sabia que havia
        histórico e não sabia UMA palavra dele. Agora é um resumo EXTRATIVO — quem disse o quê, na ordem em que
        aconteceu, recortado. Extrativo, e não redigido por modelo, por três motivos:

        * **Não inventa.** Cada linha é texto que está no histórico; um resumo gerado poderia afirmar o que
          ninguém disse, e isso voltaria em toda conversa futura como se fosse fato.
        * **Não custa.** Roda em toda confirmação de efeito, em oito aparelhos: um resumo por modelo seria uma
          chamada paga por mensagem enviada, e o teto de orçamento é do dono.
        * **Não repete o que já está à vista.** Só entra o que ficou ALÉM das `_RECENTES_NO_CONTEXTO` mostradas
          inteiras logo acima, no bloco de interações recentes.
        """
        linhas = self.repo.list_interactions(profile_id, thread_key=thread_key,
                                             status=InteractionStatus.confirmed.value, limit=500)
        if len(linhas) <= _RECENTES_NO_CONTEXTO:
            return ""     # tudo o que aconteceu já aparece em "interações recentes"; resumir seria repetir
        anteriores = list(reversed(linhas[_RECENTES_NO_CONTEXTO:]))       # do mais antigo para o mais novo
        alvo = next((r["counterparty"] for r in linhas if r["counterparty"]), "a outra pessoa")
        falas: list[str] = []
        for r in anteriores[-_FALAS_NO_RESUMO:]:
            if r["direction"] == "inbound":
                quem, texto = "ela", r["incoming_content"]
            else:
                quem, texto = "você", (r["outgoing_content"] or r["incoming_content"])
            texto = (texto or "").strip()
            if texto:
                falas.append(f"{quem}: “{texto[:_FALA_MAX_CHARS]}”")
        cabeca = (f"{len(linhas)} mensagens confirmadas nesta conversa com {alvo} desde "
                  f"{anteriores[0]['occurred_at'][:10]}; as mais recentes aparecem acima.")
        if not falas:
            return cabeca
        antes = ("Antes delas, em ordem: " if len(anteriores) <= _FALAS_NO_RESUMO
                 else f"Das {len(anteriores)} anteriores, as últimas: ")
        return cabeca + " " + antes + " · ".join(falas)

    # ------------------------------------------------------------------ o que a contraparte disse
    def record_inbound(self, profile_id: str, *, texts: Sequence[str], counterparty: str | None,
                       type: str = InteractionType.dm_received.value, thread_key: str | None = None,
                       run_id: str | None = None, objective_id: str | None = None, step_id: str | None = None,
                       instance_id: str | None = None, evidence: str | None = None) -> list[InteractionDTO]:
        """Grava o que a contraparte disse, lido da conversa aberta. É o lado que faltava do histórico.

        Nasce `confirmed`: não é uma tentativa desta conta, é um fato OBSERVADO na tela — e é o estado que faz a
        fala entrar no relacionamento, no fio e nas interações recentes. Nada aqui vira memória sozinho: memória
        continua nascendo só de `memory_candidates`, que o modelo propõe e a confirmação de um efeito grava.

        Três filtros, nesta ordem, e cada um evita um jeito específico de o histórico mentir:

        1. **O que este perfil escreveu não é fala da outra pessoa.** Uma conversa aberta mostra os dois lados;
           sem isto, o texto enviado ontem voltaria amanhã como coisa que a contraparte disse.
        2. **A mesma fala não entra duas vezes.** A conversa é relida a cada execução.
        3. **Sem alvo não se grava nada.** Fala sem dono não tem a quem ser atribuída, e atribuir errado é pior
           do que não ter.
        """
        alvo = _counterparty(counterparty)
        if not alvo:
            return []
        fio = thread_key or (thread_de_dm(alvo) if type == InteractionType.dm_received.value else None)
        proprios = {_normalizar(t) for t in self._textos_recentes(profile_id, limit=_TEXTOS_PROPRIOS_NA_CONVERSA)}
        gravadas: list[InteractionDTO] = []
        for bruto in texts:
            texto = (bruto or "").strip()
            if not texto or _normalizar(texto) in proprios:
                continue
            # A dedupe compara o texto JÁ filtrado: é o que está gravado na coluna. Comparar o bruto faria a
            # mesma fala com uma senha dentro entrar de novo a cada leitura, redigida de formas diferentes.
            seguro = _conteudo_seguro(texto)
            if self.repo.inbound_exists(profile_id, type=type, content=seguro, thread_key=fio, counterparty=alvo):
                continue
            gravadas.append(self.record_interaction(
                profile_id, type=type, direction="inbound", status=InteractionStatus.confirmed.value,
                counterparty=alvo, thread_key=fio, incoming_content=texto, run_id=run_id,
                objective_id=objective_id, step_id=step_id, instance_id=instance_id,
                evidence=evidence or "lida na conversa aberta no aparelho"))
        if not gravadas:
            return []
        # `bump=False`: contador de conversa e de relacionamento mede o que ESTA conta fez. Ler não é agir.
        if fio:
            self.repo.upsert_thread(profile_id, fio, counterparty=alvo, bump=False,
                                    last_message_at=gravadas[-1].occurred_at,
                                    summary=self._nota_de_conversa(profile_id, fio))
        self.repo.upsert_relationship(profile_id, alvo, bump=False,
                                      last_interaction_at=gravadas[-1].occurred_at,
                                      summary=self._nota_de_relacionamento(profile_id, alvo))
        log.info("perfil %s registrou %d fala(s) de entrada de %s", profile_id, len(gravadas), alvo)
        return gravadas

    def last_incoming(self, profile_id: str, *, counterparty: str | None = None,
                      thread_key: str | None = None) -> str:
        """A última fala da contraparte que AINDA NÃO foi respondida por este perfil — ou vazio.

        É o que transforma "mandar mensagem" em "responder": se a outra pessoa falou depois da última coisa que
        esta conta escreveu, há o que responder; se a última palavra foi desta conta, não há — e escrever como se
        houvesse produziria resposta a uma fala já respondida.

        Vazio nunca significa "invente": significa "escreva sem isto", e quem chama volta a `dm_initiate`.
        """
        alvo = _counterparty(counterparty)
        fio = thread_key or thread_de_dm(alvo)
        if not fio and not alvo:
            return ""
        linhas = self.repo.list_interactions(profile_id, thread_key=fio, counterparty=None if fio else alvo,
                                             limit=_RECENTES_NO_CONTEXTO)
        for row in linhas:                                   # da mais recente para a mais antiga
            if row["direction"] == "outbound":
                # Envio que FALHOU ou foi cancelado não chegou a ninguém: a última palavra continua sendo dela,
                # e a fala dela continua pendente de resposta. Só o que pode ter saído fecha o assunto — a mesma
                # régua dos limites (`CONTAM`), pelo mesmo motivo: "talvez tenha saído" conta como saiu.
                if row["status"] in CONTAM:
                    return ""                                # a última palavra foi nossa: não há o que responder
                continue
            texto = (row["incoming_content"] or "").strip()
            if texto:
                return texto
        return ""

    # ------------------------------------------------------------------ efeito externo visto pelo motor
    def open_effect(self, profile_id: str, *, capability: str, interaction_type: str, bindings: dict[str, str],
                    run_id: str | None = None, objective_id: str | None = None, step_id: str | None = None,
                    instance_id: str | None = None, draft_meta: dict[str, Any] | None = None) -> str:
        """Registra a INTENÇÃO de um efeito externo, no instante em que ele é disparado.

        Nasce `pending` de propósito: uma ação disparada cujo resultado ainda não foi observado já mexeu com a conta
        e já conta para os limites. Fingir que não aconteceu seria a maneira mais fácil de estourar o limite real.

        `draft_meta` é o que o rascunho descobriu quando o texto foi escrito. Os candidatos a memória vêm por aqui:
        é este o único ponto em que o que o modelo percebeu encontra a interação que `learn_from` vai ler. Sem essa
        passagem, `memory_items` fica vazio para sempre, por mais fatos que a contraparte afirme.

        `incoming_content` recebe só o que a contraparte disse A ESTA CONTA (o comentário que está sendo
        respondido) — nunca o que estava na tela em volta. É assim que essa fala reaparece em
        `<interacoes_recentes>` nas conversas seguintes, e gravar ali a legenda de um terceiro faria o próprio
        histórico do perfil mentir sobre quem falou o quê.
        """
        alvo = bindings.get("username") or bindings.get("target")
        meta: dict[str, Any] = {"capability": capability}
        candidatos = (draft_meta or {}).get("memory_candidates") or []
        if candidatos:
            meta["memory_candidates"] = candidatos
        if (draft_meta or {}).get("rationale"):
            meta["rationale"] = draft_meta["rationale"]
        return self.record_interaction(
            profile_id, type=interaction_type, direction="outbound", status=InteractionStatus.pending.value,
            counterparty=alvo,
            thread_key=thread_de_dm(alvo) if interaction_type == InteractionType.dm_sent.value else None,
            outgoing_content=bindings.get("content"), target=bindings.get("target"), run_id=run_id,
            objective_id=objective_id, step_id=step_id, instance_id=instance_id,
            incoming_content=(draft_meta or {}).get("incoming") or None, metadata=meta).id

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

    def confirm_effects_of_step(self, profile_id: str | None, step_id: str, *, evidence: str) -> int:
        """Fecha, como CONFIRMADAS, as interações que a etapa deixou incertas. É o par social do "confirmar
        concluído": sem isto, a etapa vira feita e o histórico do perfil segue dizendo que não se sabe — o
        relacionamento, a conversa e a memória nunca aprendem com o que a pessoa viu acontecer.

        A evidência diz que veio de pessoa, não de tela: quem lê depois precisa distinguir as duas coisas.
        """
        # Objetivo sem perfil (execução de app que não é rede social) não tem histórico para fechar.
        if not profile_id:
            return 0
        fechadas = 0
        for row in self.repo.interactions_by_step(profile_id, step_id, status=InteractionStatus.uncertain.value):
            try:
                self.confirm_interaction(row["profile_id"], row["id"], evidence=evidence)
                fechadas += 1
            except SocialError:
                log.warning("interação %s não pôde ser confirmada manualmente", row["id"])
        return fechadas

    def reconcile_pending_effects(self) -> int:
        """Na partida: efeito disparado cujo desfecho nunca foi observado vira INCERTO, nunca confirmado.

        Um `pending` eterno contaria para sempre nos limites e, pior, poderia ser confundido com sucesso. Incerto é
        o que ele realmente é — e incerto não vira memória.
        """
        q = ("SELECT id, profile_id FROM social_interactions si"
             " WHERE si.status=? AND si.direction='outbound'")
        params: list[Any] = [InteractionStatus.pending.value]
        if self.owner_id is not None:
            # Só o que aconteceu em aparelho DESTE backend (item 5.1, achado #26): sem o filtro, o segundo a
            # subir marcava como incerto o efeito social que o primeiro estava observando naquele instante.
            # Interação sem aparelho (ou de aparelho sem dono registrado) continua sendo de quem subir.
            q += (" AND NOT EXISTS (SELECT 1 FROM instances i WHERE i.id=si.instance_id"
                  " AND i.hosted_by IS NOT NULL AND i.hosted_by<>?)")
            params.append(self.owner_id)
        abertas = self.repo.db.query(q, tuple(params))
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

    def remember_screen(self, profile_id: str, *, step_title: str, bindings: dict[str, Any] | None, elements: Any,
                        items: Sequence[str] | None = None, app_label: str = "app") -> MemoryItemDTO | None:
        """A tela em que uma etapa foi comprovada vira memória de origem `observation` (ver `observacao.py`).

        Quem chama já descartou tela sensível e aparelho-loja; aqui só se extrai o conteúdo e se grava. Validade de
        30 dias: o que se vê numa tela muda (contadores, "ativo agora"), e o fato velho não pode concorrer para sempre
        com o que a pessoa disse. Recusa por segredo é silenciosa: tela com cara de código simplesmente não vira fato.
        """
        from ..util import iso_in, now_iso  # noqa: PLC0415
        from .observacao import assunto_da_tela, fato_observado, linhas_de_conteudo  # noqa: PLC0415
        elementos = list(elements or [])
        texto = fato_observado(titulo_da_etapa=step_title, quando=now_iso(), linhas=linhas_de_conteudo(elementos),
                               itens=items)
        if texto is None:
            return None
        try:
            item = self.memory.remember(profile_id, subject=assunto_da_tela(bindings, elementos, app_label),
                                        content=texto, source="observation", importance=0.3, confidence=0.7,
                                        expires_at=iso_in(30 * 86400))
        except MemoryRefused as exc:
            log.info("tela não virou memória (%s): %s", profile_id, exc)
            return None
        self.bus.emit("log", "Memória: tela observada registrada", data={"profile_id": profile_id, "memory_id": item.id})
        return item

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

    # ------------------------------------------------------------------ política e limites do perfil
    def _pacote_do_perfil(self) -> str | None:
        """O pacote do app a que um perfil pertence, perguntado ao registro em vez de escrito na assinatura."""
        return package_of_provider("instagram")

    def get_policy(self, profile_id: str, *, package: str | None = None) -> ProfilePolicyDTO:
        """O que vale hoje para este perfil, ao lado do que o catálogo propõe — para a diferença ficar visível.

        `package` omitido resolve pelo REGISTRO de aplicativos (o app que provê a conta deste perfil), e não
        por um literal `com.instagram.android` na assinatura: assim um segundo app com conta gerenciada
        entra sem editar esta função.
        """
        self.get_profile(profile_id)
        catalogo = load_catalog(package or self._pacote_do_perfil())
        acoes = catalogo.offered if catalogo else []
        engine = self.policies
        efetivas = {c.key: engine.policy_for(profile_id, c) for c in acoes}
        return ProfilePolicyDTO(
            limits=engine.limits_for(profile_id),
            capabilities=efetivas,
            defaults={c.key: c.default_policy for c in acoes},
            loosened=[c.key for c in acoes if engine.is_loosened(c, efetivas[c.key])])

    def set_policy(self, profile_id: str, body: Any, *, package: str | None = None) -> ProfilePolicyDTO:
        """Só aceita o que existe: nome de ação fora do catálogo ou limite desconhecido é erro, não silêncio."""
        self.get_profile(profile_id)
        package = package or self._pacote_do_perfil()
        catalogo = load_catalog(package)
        atual = loads(self.repo.profile_row(profile_id)["automation_policy"], {}) or {}
        if body.capabilities is not None:
            desconhecidas = [k for k in body.capabilities if not (catalogo and catalogo.has(k))]
            if desconhecidas:
                raise SocialError("unknown_capability", f"Ação desconhecida: {', '.join(desconhecidas)}.", 400)
            # Achado #114: afrouxar abaixo do padrão do catálogo sempre foi aceito (o docstring antigo dizia o
            # contrário); o que faltava era isto não ser silencioso quando a ação é de risco ALTO — FOLLOW e
            # SEND_MESSAGE são as próprias evidências do achado (frota inteira seguindo/mandando DM ao mesmo
            # alvo em minutos, com SEND_MESSAGE `autonomous` nos 8 perfis de produção).
            for chave, nova in body.capabilities.items():
                cap = catalogo.get(chave) if catalogo else None
                if cap and cap.risk == "high" and self.policies.is_loosened(cap, nova):
                    self.bus.emit("log", f"perfil {profile_id}: {chave} afrouxado para '{nova}' — abaixo do "
                                         f"padrão '{cap.default_policy}' do catálogo, em ação de risco alto",
                                  level="warn", data={"profile_id": profile_id, "capability": chave, "policy": nova})
            atual["capabilities"] = {**(atual.get("capabilities") or {}), **body.capabilities}
        if body.limits is not None:
            invalidos = [k for k in body.limits if k not in DEFAULT_LIMITS]
            if invalidos:
                raise SocialError("unknown_limit", f"Limite desconhecido: {', '.join(invalidos)}.", 400)
            negativos = [k for k, v in body.limits.items() if v < 0]
            if negativos:
                raise SocialError("invalid_limit", f"Limite não pode ser negativo: {', '.join(negativos)}.", 400)
            atual["limits"] = {**(atual.get("limits") or {}), **body.limits}
        self.repo.update_profile(profile_id, {"automation_policy": dumps(atual)})
        self.bus.emit("log", "Política de automação atualizada", data={"profile_id": profile_id})
        return self.get_policy(profile_id, package=package)

    def auth_attempts(self, profile_id: str, limit: int = 20) -> list[dict[str, Any]]:
        self.get_profile(profile_id)
        return [dict(r) for r in self.repo.auth_attempts(profile_id, limit)]

    # ------------------------------------------------------------------ contexto e geração
    def context(self, profile_id: str, *, counterparty: str | None = None, thread_key: str | None = None,
                current_content: str | None = None, recall_hint: str | None = None,
                touch: bool = False) -> SocialContextDTO:
        self.get_profile(profile_id)
        return self.contexts.build(profile_id, counterparty=counterparty, thread_key=thread_key,
                                   current_content=current_content, recall_hint=recall_hint, touch=touch)

    async def draft_response(self, profile_id: str, *, kind: str, incoming: str = "", brief: str = "",
                             counterparty: str | None = None, thread_key: str | None = None, max_length: int = 300,
                             persist: bool = True, screen: str = "", runner: Any = None,
                             avoid: Sequence[str] = ()) -> tuple[SocialDraftDTO, InteractionDTO | None]:
        """Gera o texto e o REGISTRA antes de qualquer envio (§16). Nada é enviado aqui: quem envia é o executor.

        Duas origens, o mesmo caminho: `incoming` é o que a contraparte disse (responder), `brief` é a intenção
        vinda do comando (comentar, puxar conversa). Pelo menos um dos dois precisa existir — sem nenhum, não há
        o que escrever.

        `avoid` são textos que não podem se repetir (os dos irmãos desta execução, por exemplo). A eles somam-se os
        últimos textos deste próprio perfil: repetir a si mesmo é tão delator quanto repetir o vizinho.

        `screen` é o que está ESCRITO na tela neste momento (legenda da publicação, comentários, a conversa aberta).
        É o ASSUNTO da escrita, não fala dirigida a esta conta: vai no bloco `<tela>`, e de lá não sai memória.
        """
        if not (incoming or "").strip() and not (brief or "").strip():
            raise SocialError("nothing_to_write", "Sem mensagem recebida nem intenção, não há texto a escrever.", 400)
        dto = self.get_profile(profile_id)
        # A chave do fio sai daqui quando quem chama não a passou. Sem isto, quem escrevia uma mensagem direta
        # montava o contexto SEM conversa nenhuma (`thread=None`) enquanto o efeito, ao ser gravado, ia para
        # `dm:@alvo`: o resumo da conversa existia no banco e nunca chegava a quem estava escrevendo.
        if thread_key is None and kind in ("dm_reply", "dm_initiate"):
            thread_key = thread_de_dm(counterparty)
        # `current_content` fica vazio de propósito: o recebido já vai em `<conteudo_recebido>` e a tela em
        # `<tela>`; repeti-los em `<conteudo_atual>` só duplicaria o prompt. A tela e a intenção continuam
        # valendo como PISTA DE BUSCA — é o que torna "memória relevante" relativa ao que está aberto agora.
        ctx = self.context(profile_id, counterparty=counterparty, thread_key=thread_key,
                           recall_hint=" ".join(p for p in (screen, incoming, brief) if p), touch=True)
        proibidos = _sem_repetir(list(avoid) + self._textos_recentes(profile_id))
        pedido = SocialRequest(
            profile_id=profile_id, username=dto.username, kind=kind, context_text=ctx.rendered,
            incoming=incoming, brief=brief, screen=screen,
            counterparty=_counterparty(counterparty) if counterparty else None,
            max_length=max_length, avoid=tuple(proibidos))
        draft, _usage = await self._generate(pedido, runner=runner)
        # Pedir para não repetir não garante que não repita. Uma segunda chance, e só uma: o custo de IA é real e
        # um texto repetido é melhor do que uma etapa travada.
        if _repetido(draft, proibidos):
            log.info("perfil %s repetiu um texto que já existia; gerando de novo", profile_id)
            segunda, _usage2 = await self._generate(replace(pedido, retry=True), runner=runner)
            if not _repetido(segunda, proibidos) and (segunda.content or "").strip():
                draft = segunda
        # A regra de que só `<conteudo_recebido>` gera memória está escrita no papel do sistema — e regra de prompt
        # é pedido, não garantia. Sem fala dirigida a esta conta, os candidatos são descartados AQUI, em código:
        # senão uma legenda de terceiro ("fulano deve R$5.000 a beltrano") viraria fato permanente do perfil,
        # pendurado em quem o próprio modelo escolhesse, e voltaria em toda conversa futura.
        if not (incoming or "").strip() and draft.memory_candidates:
            log.info("perfil %s: %d candidato(s) a memória descartados — não houve fala dirigida à conta",
                     profile_id, len(draft.memory_candidates))
            draft = draft.model_copy(update={"memory_candidates": []})
        if not persist:
            return draft, None
        interacao = self.record_interaction(
            profile_id, type=(InteractionType.dm_sent.value if kind in ("dm_reply", "dm_initiate")
                              else InteractionType.comment_replied.value),
            direction="outbound", status=InteractionStatus.pending.value, counterparty=counterparty,
            thread_key=thread_key, incoming_content=incoming, outgoing_content=draft.content,
            metadata={"memory_candidates": [c.model_dump() for c in draft.memory_candidates],
                      "refused": draft.refused, "rationale": draft.rationale})
        return draft, interacao

    def _textos_recentes(self, profile_id: str, limit: int = _TEXTOS_ANTERIORES) -> list[str]:
        """O que este perfil já escreveu. Serve para não repetir a si mesmo em execuções seguidas."""
        try:
            return self.repo.recent_outgoing_texts(profile_id, limit)
        except Exception:  # noqa: BLE001 - histórico indisponível não pode impedir a escrita
            log.exception("não foi possível ler os textos recentes do perfil %s", profile_id)
            return []

    async def preview_persona(self, persona_id: str, body: Any) -> SocialDraftDTO:
        """Testar Persona: gera um exemplo e não grava nada — nem interação, nem memória, nem uso do aparelho."""
        persona = self.get_persona(persona_id)
        profile_id = body.profile_id or persona.profile_id
        if profile_id:
            perfil = self.get_profile(profile_id)
            if perfil.persona_id != persona_id:
                raise SocialError("persona_mismatch", "Esta persona não é a do perfil informado.", 400)
            # Sem `current_content`: o recebido já vai em `<conteudo_recebido>` na prévia também, e duplicá-lo
            # faria o operador conferir uma persona num prompt que não é o da execução.
            contexto = self.context(profile_id, counterparty=body.counterparty,
                                    recall_hint=" ".join(p for p in (body.incoming, body.brief) if p))
            texto, username = contexto.rendered, perfil.username
        else:
            texto = self.contexts.render_persona_only(persona)
            username = persona.name
        draft, _usage = await self._generate(SocialRequest(
            profile_id=profile_id or "", username=username, kind=body.kind, context_text=texto,
            # A prévia passa pelo MESMO montador do prompt da execução: intenção, tela e recebido nos mesmos
            # blocos. Conferir a persona num prompt diferente do real seria conferir outra coisa.
            incoming=body.incoming, brief=body.brief, screen=body.screen,
            counterparty=_counterparty(body.counterparty) if body.counterparty else None,
            preview=True))
        return draft

    async def _generate(self, req: SocialRequest, *, runner: Any = None) -> tuple[SocialDraftDTO, Any]:
        """`runner` é o caminho de IA DA EXECUÇÃO: limite de chamadas simultâneas, tetos de orçamento conferidos
        ANTES de gastar, três tentativas com espera e a contabilidade no run/objetivo certos.

        Sem ele, a geração do rascunho seria a única chamada de modelo de uma execução a correr por fora disso —
        oito aparelhos chegariam juntos ao provedor contra um teto configurado de quatro, e o custo não apareceria
        em nenhum dos dois contadores de orçamento. Os caminhos de FORA de execução (prévia de persona, portal)
        continuam sem runner, com o registro de uso avulso.
        """
        if self.provider is None:
            raise SocialError("ai_unavailable", "Nenhum provedor de IA disponível para gerar resposta.", 503)
        if runner is not None:
            try:
                # Quem contabiliza aqui é o runner; chamar o sink também contaria o mesmo custo duas vezes.
                return await runner(lambda: self.provider.generate_social_response(req)), None
            except AIError as exc:
                # Orçamento estourado não é provedor com problema: mandar "confira a chave e a persona" faria a
                # pessoa procurar defeito onde não há, tentar de novo e bater na mesma parede. Recusa por
                # política é o mesmo raciocínio: reescrever a intenção resolve, chave e persona não têm nada a
                # ver (achado #93, ponto 3).
                codigo = "ai_budget" if exc.kind == "budget" else "ai_refusal" if exc.kind == "refusal" else "ai_error"
                raise SocialError(codigo, str(exc), 503) from None
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
                "CREDENTIALS_MASTER_KEY no .env (nome antigo: INSTAGRAM_CREDENTIALS_MASTER_KEY) ou rode "
                "num usuário com DPAPI disponível.")


def thread_de_dm(counterparty: str | None) -> str | None:
    """A chave do fio de mensagem direta com alguém. UMA definição, usada por todos os caminhos.

    Existia escrita à mão dentro de `open_effect` e em nenhum outro lugar: o rascunho montava o contexto sem fio
    nenhum e o efeito gravava em `dm:@alvo`, então o resumo da conversa nunca voltava para quem estava
    escrevendo. Chave calculada em dois lugares diferentes é a forma mais barata de ter duas conversas.
    """
    alvo = _counterparty(counterparty)
    return f"dm:{alvo}" if alvo else None


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
