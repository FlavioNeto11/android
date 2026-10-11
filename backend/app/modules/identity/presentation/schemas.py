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
from datetime import datetime
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
    """Só escrita. Não existe rota que devolva a senha — nem esta.

    `consent` é o consentimento POR CONTA (ADR-040): a pessoa autoriza a automação a digitar esta senha na tela do
    app (e do site) desta conta, pelo canal sensível. Sem ele numa conta que ainda não consentiu, 409
    `consentimento_de_credencial` — a senha não é guardada às escondidas.
    """

    model_config = ConfigDict(extra="forbid")
    login_identifier: str | None = Field(default=None, max_length=200)
    password: SecretStr = Field(min_length=1)
    consent: bool = False


class CredentialClone(BaseModel):
    """Usar a senha de OUTRA conta da mesma persona (ADR-057, D1): o cofre copia o valor para uma entrada própria
    desta conta, sem que ele saia do módulo. Não há `password` nem `consent`: o valor não passa por aqui, e o
    consentimento é desta conta, dado depois pela pessoa (`…/credential/consent`), nunca herdado da origem.

    `login_identifier` também não é copiado da origem: o endereço da conta nova é o que o dono confirma (ADR-057
    §5). O campo é `clonar_de` (id da conta de origem), e não `credencial_de`: nome com "credencial" é tratado como
    valor secreto pela redação e pela guarda dos modelos, e aqui ele carrega só um id.
    """

    model_config = ConfigDict(extra="forbid")
    clonar_de: str = Field(min_length=1, max_length=120)
    login_identifier: str | None = Field(default=None, max_length=200)


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
    #: Conta de PORTAL ou site (app de navegador): o host onde a credencial pode ser digitada (ADR-040). Nulo =
    #: conta do app inteiro. (perfil, app, host) é único.
    host: str | None = Field(default=None, max_length=253)
    login_identifier: str | None = Field(default=None, max_length=200)
    password: SecretStr | None = None
    #: Com `password`: a pessoa autoriza a automação a digitá-la (ADR-040). Sem ele, 409.
    consent: bool = False
    #: Id de outra conta DESTA persona cuja senha o cofre clona para a conta nova (ADR-057, D1). Exclui `password`
    #: e `consent` (422): a conta nova nasce sem consentimento, que a pessoa dá depois. Outra persona: 409.
    clonar_de: str | None = Field(default=None, min_length=1, max_length=120)
    notes: str = Field(default="", max_length=400)


class ProfileAccountPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    handle: str | None = Field(default=None, max_length=200)
    host: str | None = Field(default=None, max_length=253)
    status: Literal["active", "disabled"] | None = None
    #: Marcação da pessoa depois de entrar pelo Foco (apps sem login automático): "entrei" / "saí" / "precisa de
    #: mim", no vocabulário único da sessão (049; `auth_required` é o antigo `logged_out`). Vale para a conta NO
    #: aparelho vinculado ao perfil.
    session_status: Literal["unknown", "session_ready", "auth_required", "needs_person"] | None = None
    notes: str | None = Field(default=None, max_length=400)
    #: O endereço que se quer para a conta ainda não confirmada (31.281, adendo v1.132). Depois de `confirmada`, 409.
    desired_handle: str | None = Field(default=None, max_length=200)


class PlannedAccountCreate(BaseModel):
    """`POST …/accounts/planned` (31.281, ADR-087): a conta que ainda NÃO existe no provedor. Sem senha aqui: a
    credencial se prepara depois, por `…/credential/prepare`."""

    model_config = ConfigDict(extra="forbid")
    app_id: str = Field(min_length=1, max_length=120)
    host: str | None = Field(default=None, max_length=253)
    desired_handle: str | None = Field(default=None, max_length=200)


class CredentialPrepare(BaseModel):
    """`POST …/credential/prepare`: gerar (o servidor, com `secrets`), digitar ou reutilizar a senha de uma conta ainda
    planejada. Só escrita; nenhuma resposta devolve a senha. O `password` é `SecretStr` e a recusa de campo trocado é do
    serviço (o 422 do validador devolveria o corpo, com a senha)."""

    model_config = ConfigDict(extra="forbid")
    modo: Literal["gerar", "digitar", "reutilizar"]
    consent: bool = False
    substituir: bool = False
    password: SecretStr | None = None
    clonar_de: str | None = Field(default=None, min_length=1, max_length=120)
    tamanho: int | None = Field(default=None, ge=16, le=64)
    login_identifier: str | None = Field(default=None, max_length=200)


class ConfirmationEvidence(BaseModel):
    """A prova de que a conta existe no provedor: a sessão observada, ou a marcação nominal da pessoa."""

    model_config = ConfigDict(extra="forbid")
    tipo: Literal["sessao", "declarada"]
    sessao_id: str | None = Field(default=None, max_length=200)
    handle_confirmado: str | None = Field(default=None, max_length=200)


class ProvisioningEventBody(BaseModel):
    """`POST …/provisioning`: um evento do ciclo da conta planejada, com o estado que o chamador viu (comparar e trocar)."""

    model_config = ConfigDict(extra="forbid")
    evento: Literal["iniciar_cadastro", "enviado", "confirmar", "falhar", "retomar", "cancelar"]
    estado_esperado: Literal["planejada", "credencial_preparada", "aguardando_cadastro_externo",
                             "aguardando_verificacao", "confirmada", "falha"]
    motivo: str | None = Field(default=None, max_length=300)
    evidencia: ConfirmationEvidence | None = None


class SignupBody(BaseModel):
    """`POST …/provisioning/signup` (31.310, v1.137): o aparelho é opcional (o vinculado da persona por padrão)."""

    model_config = ConfigDict(extra="forbid")
    instance_id: str | None = Field(default=None, min_length=1, max_length=80)


class ProxyDaContaBody(BaseModel):
    """`POST …/accounts/{id}/proxy` (31.337): o proxy sticky da conta planejada. A URL pode trazer a senha do proxy: `SecretStr`
    (nunca volta, nem em log, evento ou resposta)."""

    model_config = ConfigDict(extra="forbid")
    proxy_url: SecretStr = Field(min_length=3, max_length=500)


class ProxyDaContaDTO(BaseModel):
    profile_id: str
    account_id: str
    #: O perfil de rede `igfarm-<conta>`.
    network_profile_id: str
    #: Um item por aparelho da persona: `atribuido` | `ja_atribuido` | `pendente_confirmacao`.
    egresso: list[EgressoDoDeviceDTO] = Field(default_factory=list)
    #: Sempre `null` aqui: o IP de criação é a primeira medição dentro da janela do cadastro.
    egress_esperado: str | None = None


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


class PersonaBatchBody(BaseModel):
    """`POST /personas/generate/batch`: o MESMO pedido de `PersonaGenerateBody`, N vezes (1 a 10), em segundo plano.
    Cada item é uma chamada PAGA pelo papel social. `create: true` grava cada rascunho válido como persona (e dispara
    a foto automática de `ai.image.on_create`); `false` deixa os rascunhos no estado do lote para a pessoa revisar e
    criar os escolhidos por `POST /personas`."""

    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(min_length=3, max_length=2000)
    count: int = Field(ge=1, le=10)
    locale: str | None = Field(default=None, max_length=20)
    constraints: dict[str, str] = Field(default_factory=dict, max_length=20)
    create: bool = False


class PersonaEnrichBody(BaseModel):
    """`POST /personas/{id}/enrich` com instruções: o que o dono quer para o que FALTA ("ela é evangélica, vai ao
    culto toda semana"). Continua só completando o vazio — instrução não reescreve o que já existe. Sem corpo, o
    enriquecimento é o de sempre."""

    model_config = ConfigDict(extra="forbid")
    instructions: str | None = Field(default=None, max_length=500)


class PersonaImagesBody(BaseModel):
    """`POST /personas/{id}/images` com JSON: quantas imagens gerar agora (1 a 3). Corpo cru `image/jpeg|png` na
    mesma rota é upload, não geração."""

    model_config = ConfigDict(extra="forbid")
    count: int = Field(default=1, ge=1, le=3)


class PersonaDeviceBody(BaseModel):
    """`POST /personas/{id}/devices`: soma um aparelho à persona para um app; `primary` o torna o principal."""

    model_config = ConfigDict(extra="forbid")
    instance_id: str = Field(min_length=1, max_length=60)
    app_id: str | None = Field(default=None, max_length=80)
    primary: bool = False


class FeitaPorIaBody(BaseModel):
    """29.81: `PUT /personas/{id}/images/{image_id}/feita-por-ia`. O campo é obrigatório; `null` volta a "não
    informado". Nasce aqui (e não em `app.models`): corpo novo mora na apresentação do contexto."""

    model_config = ConfigDict(extra="forbid")
    feita_por_ia: bool | None


class PersonaPendenteDTO(BaseModel):
    """Uma persona sem conta, com o que o igfarm precisa para criá-la (`GET /api/instagram/personas-pendentes`)."""

    persona_id: str
    nome: str
    primeiro_nome: str
    sobrenome: str
    nome_exibicao: str
    birth_date: str
    genero: str | None = None
    biografia: dict[str, object] = Field(default_factory=dict)
    visual: dict[str, object] = Field(default_factory=dict)
    resumo: str | None = None
    email_sugerido: str
    username_sugerido: str
    #: Caminho da foto (`/api/personas/{id}/images/{image_id}`); `None` enquanto não foi gerada (só `reservar=true` gera).
    imagem_perfil: str | None = None
    imagem_pendente: bool = False


class ContaIgfarmBody(BaseModel):
    """`POST /api/instagram/contas`: a conta que o igfarm criou. As senhas entram no cofre e nunca voltam."""

    model_config = ConfigDict(extra="forbid")
    persona_id: str = Field(min_length=1, max_length=100)
    dominio: str = Field(min_length=3, max_length=255)
    email: str = Field(min_length=3, max_length=320)
    email_senha: SecretStr
    instagram_username: str = Field(min_length=1, max_length=40)
    instagram_senha: SecretStr
    igfarm_account_id: str = Field(min_length=1, max_length=200)
    criada_em: datetime
    proxy_url: SecretStr | None = Field(default=None, max_length=500)
    ip_criacao: str | None = Field(default=None, max_length=45)


class EgressoDoDeviceDTO(BaseModel):
    instance_id: str
    #: `atribuido` | `ja_atribuido` | `pendente_confirmacao` (conta real de OUTRA persona no aparelho).
    estado: str
    motivo: str = ""


class ContaRegistradaDTO(BaseModel):
    persona_id: str
    account_id: str
    igfarm_account_id: str
    email: str
    instagram_username: str
    criada_em: str
    registrada_em: str
    #: Máscara fixa para conferência; o valor das senhas nunca sai.
    senhas: str
    criada: bool
    idempotente: bool
    #: A saída de cada aparelho vinculado à persona; vazio se não há vínculo ou a conta veio sem proxy.
    egresso: list[EgressoDoDeviceDTO] = []


class CabecalhoDaCaixaDTO(BaseModel):
    recebida_em: str
    remetente: str
    #: Assunto com qualquer sequência de seis dígitos trocada por `######` (pode ser o código).
    assunto: str
    #: `spf`/`dkim`/`dmarc` → resultado anotado pelo servidor de entrada (`pass`, `fail`, `softfail`, `none`...).
    autenticacao: dict[str, str] = Field(default_factory=dict)
    devolucao: bool = False


class CabecalhosDaContaDTO(BaseModel):
    """`GET /api/instagram/contas/{id}/cabecalhos` (31.336): só os cabeçalhos da caixa da conta, nunca o corpo."""

    account_id: str
    horas: int
    total: int
    mensagens: list[CabecalhoDaCaixaDTO] = Field(default_factory=list)


class ContatoDaContaDTO(BaseModel):
    iniciado_em: str
    minutos_desde_a_criacao: float | None = None
    desfecho: str
    etapa: str
    detalhe: str


class CicloDaContaDTO(BaseModel):
    """`GET /api/instagram/contas/{id}/ciclo` (31.333): o que aconteceu com a conta que o igfarm criou. Só ids, horas,
    minutos e desfechos: nunca e-mail, senha, proxy nem @."""

    account_id: str
    #: `null` na conta criada no app (31.341).
    igfarm_account_id: str | None = None
    criada_em: str | None = None
    registrada_em: str | None = None
    #: `igfarm` | `app`.
    origem: str = "igfarm"
    #: De onde contam os minutos: `criacao` | `planejamento` (conta do app ainda não confirmada).
    referencia: str = "criacao"
    #: `ativa` (segue na persona) | `retirada` (o @ está na lápide).
    estado: str
    retirada_em: str | None = None
    minutos_ate_o_primeiro_contato: float | None = None
    ultimo_desfecho: str | None = None
    contatos: list[ContatoDaContaDTO] = Field(default_factory=list)


class CodigoDaContaDTO(BaseModel):
    codigo: str
    recebido_em: str
    remetente: str
