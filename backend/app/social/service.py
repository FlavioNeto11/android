"""Serviço de perfis: cadastro, credencial protegida e vínculo com aparelho.

A senha entra por aqui, vai direto para o cofre e nunca mais aparece: não há método que a devolva, e o DTO não tem
campo para ela. Quem precisa do valor é o canal de entrada sensível, no instante da digitação.
"""
from __future__ import annotations

import logging
from typing import Any

from ..events import EventBus
from ..models import InstagramProfileDTO, SessionStatus
from ..security.secret_store import SecretStore, SecretStoreLocked, SecretStoreUnavailable
from .repository import SocialRepository

log = logging.getLogger("poc.social")


class SocialError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class SocialService:
    def __init__(self, repo: SocialRepository, secrets: SecretStore, bus: EventBus,
                 known_instances: Any = None):
        self.repo = repo
        self.secrets = secrets
        self.bus = bus
        self._known_instances = known_instances or (lambda: [])

    # ------------------------------------------------------------------ consulta
    def list_profiles(self) -> list[InstagramProfileDTO]:
        return [dto for pid in self.repo.list_profile_ids() if (dto := self.repo.profile_dto(pid))]

    def get_profile(self, profile_id: str) -> InstagramProfileDTO:
        dto = self.repo.profile_dto(profile_id)
        if dto is None:
            raise SocialError("not_found", "Perfil não encontrado.", 404)
        return dto

    def profile_for_instance(self, instance_id: str) -> InstagramProfileDTO | None:
        pid = self.repo.profile_id_for_instance(instance_id)
        return self.repo.profile_dto(pid) if pid else None

    # ------------------------------------------------------------------ cadastro
    def create_profile(self, body: Any) -> InstagramProfileDTO:
        if self.repo.profile_by_username(body.username):
            raise SocialError("duplicate_username", f"Já existe um perfil para @{body.username}.")
        if body.persona_id and not self.repo.persona_exists(body.persona_id):
            raise SocialError("unknown_persona", "Persona não encontrada.", 400)
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
        if "persona_id" in fields and fields["persona_id"] and not self.repo.persona_exists(fields["persona_id"]):
            raise SocialError("unknown_persona", "Persona não encontrada.", 400)
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

    def _vault_message(self) -> str:
        status = self.secrets.status()
        if status == "locked":
            return ("O cofre de credenciais está travado nesta máquina/usuário. As credenciais cifradas foram "
                    "preservadas; recadastre-as para voltar a usar autenticação automática.")
        return ("Não há chave mestra disponível para proteger credenciais. Defina "
                "INSTAGRAM_CREDENTIALS_MASTER_KEY no .env ou rode num usuário com DPAPI disponível.")
