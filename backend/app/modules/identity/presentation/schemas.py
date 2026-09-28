"""Corpos de requisição HTTP de identidade: perfil, credencial, prévia de persona, memória, conta por app,
política e grupos de acesso.

Movidos literalmente de `app/models.py` (design §16, fase K: "primeiro os corpos"); `app.models` reexporta os
MESMOS objetos, e o nome de cada classe continua sendo o nome do esquema no contrato HTTP. Este módulo não
importa `app.models`: o reexport importa daqui, e o caminho de volta fecharia um ciclo de import de topo.

Ficaram em `app.models`: `ProfilePatch` (usa `OfflinePolicy`, que o `InstagramProfileDTO` também usa) e
`PersonaCreate`/`PersonaPatch` (usam `PersonaTraits`, VO de domínio).
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


_USERNAME = re.compile(r"^[A-Za-z0-9._]{1,30}$")


class ProfileCreate(BaseModel):
    """Cadastro pelo portal. `password` é SecretStr: não aparece em repr, log nem em erro de validação."""

    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=30)
    first_name: str | None = Field(default=None, max_length=80)
    last_name: str | None = Field(default=None, max_length=80)
    display_name: str | None = Field(default=None, max_length=120)
    birth_date: str | None = Field(default=None, max_length=10)
    email: str | None = Field(default=None, max_length=200)
    persona_id: str | None = Field(default=None, max_length=120)
    policy_group_id: str | None = Field(default=None, max_length=120)
    instance_id: str | None = Field(default=None, max_length=60)
    login_identifier: str | None = Field(default=None, max_length=200)
    password: SecretStr | None = None

    @field_validator("username")
    @classmethod
    def _username(cls, v: str) -> str:
        v = v.strip().lstrip("@")
        if not _USERNAME.match(v):
            raise ValueError("username inválido: use letras, números, ponto ou sublinhado")
        return v


class CredentialUpdate(BaseModel):
    """Só escrita. Não existe rota que devolva a senha — nem esta."""

    model_config = ConfigDict(extra="forbid")
    login_identifier: str | None = Field(default=None, max_length=200)
    password: SecretStr = Field(min_length=1)


class PersonaPreviewBody(BaseModel):
    """Testar a persona SEM publicar nada: nenhuma tela é tocada, nenhuma interação é gravada."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["dm_reply", "comment_reply", "dm_initiate", "post_comment"] = "dm_reply"
    profile_id: str | None = Field(default=None, max_length=120)   # usa memória/relacionamento deste perfil
    counterparty: str | None = Field(default=None, max_length=60)
    #: Responder pede `incoming`; PUXAR CONVERSA pede `brief` — a mesma INTENÇÃO que o comando daria. Sem os dois
    #: não havia como conferir a persona no caminho que o dono mais usa (mandar mensagem), nem comparar oito
    #: perfis sob a mesma intenção sem gastar uma execução em aparelho (achado #107, prova do item 8.1).
    incoming: str = Field(default="", max_length=2000)
    brief: str = Field(default="", max_length=2000)
    screen: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def _tem_o_que_escrever(self) -> PersonaPreviewBody:
        if not self.incoming.strip() and not self.brief.strip():
            raise ValueError("informe a mensagem recebida (incoming) ou a intenção (brief)")
        return self


class MemoryCreate(BaseModel):
    """O operador pode ensinar um fato à mão. O que parece segredo é recusado pelo serviço."""

    model_config = ConfigDict(extra="forbid")
    subject: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=1000)
    importance: float = Field(default=0.6, ge=0.0, le=1.0)
    confidence: float = Field(default=0.9, ge=0.0, le=1.0)
    expires_at: str | None = Field(default=None, max_length=40)
    #: App a que o fato pertence (item 12.1); vazio = fato geral da identidade, vale em qualquer app.
    app_id: str | None = Field(default=None, max_length=120)


PolicyName = Literal["autonomous", "approval_required", "manual_only", "disabled"]


class ProfilePolicyPatch(BaseModel):
    """`null` numa chave APAGA a escolha própria do perfil: a ação (ou o limite) volta a herdar do grupo/padrão."""

    model_config = ConfigDict(extra="forbid")
    limits: dict[str, int | None] | None = None
    capabilities: dict[str, PolicyName | None] | None = None


class ProfileAccountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    app_id: str = Field(min_length=1, max_length=120)
    handle: str = Field(default="", max_length=200)
    login_identifier: str | None = Field(default=None, max_length=200)
    password: SecretStr | None = None
    notes: str = Field(default="", max_length=400)


class ProfileAccountPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    handle: str | None = Field(default=None, max_length=200)
    status: Literal["active", "disabled"] | None = None
    #: Marcação da pessoa depois de entrar pelo Foco (apps sem login automático): "entrei" / "saí".
    session_status: Literal["unknown", "session_ready", "logged_out", "needs_person"] | None = None
    notes: str | None = Field(default=None, max_length=400)


class PolicyGroupCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=400)
    capabilities: dict[str, PolicyName] = Field(default_factory=dict)
    limits: dict[str, int] = Field(default_factory=dict)
    #: Começa com o que este perfil tem HOJE de diferente do padrão (as escolhas dele e as do grupo dele).
    from_profile_id: str | None = Field(default=None, max_length=120)
    profile_ids: list[str] = Field(default_factory=list, max_length=500)


class PolicyGroupPatch(BaseModel):
    """`null` numa chave de `capabilities`/`limits` tira aquela chave do grupo (volta ao padrão do catálogo).
    `profile_ids`, quando vem, é a lista COMPLETA de membros: quem sai volta a herdar só do padrão."""

    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=400)
    capabilities: dict[str, PolicyName | None] | None = None
    limits: dict[str, int | None] | None = None
    profile_ids: list[str] | None = Field(default=None, max_length=500)


class PersonaGenerateBody(BaseModel):
    """`POST /personas/generate`: o pedido em linguagem natural. Chamada PAGA pelo papel social; a resposta é um
    rascunho NÃO gravado, no formato de `PersonaCreate`, para a pessoa revisar e então criar."""

    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(min_length=3, max_length=2000)
    locale: str | None = Field(default=None, max_length=20)
    #: Restrições curtas e explícitas: `{"gender": "feminino", "city": "Curitiba", "age": "30-35"}`.
    constraints: dict[str, str] = Field(default_factory=dict, max_length=20)


class PersonaImagesBody(BaseModel):
    """`POST /personas/{id}/images` com JSON: quantas imagens gerar agora (1 a 3). Corpo cru `image/jpeg|png` na
    mesma rota é upload, não geração."""

    model_config = ConfigDict(extra="forbid")
    count: int = Field(default=1, ge=1, le=3)
