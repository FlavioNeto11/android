"""Serviço de perfis: cadastro, credencial protegida e vínculo com aparelho.

A senha entra por aqui, vai direto para o cofre e nunca mais aparece: não há método que a devolva, e o DTO não tem
campo para ela. Quem precisa do valor é o canal de entrada sensível, no instante da digitação.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from typing import Any

from pydantic import SecretStr

from ..db import Row, dumps, loads
from ..util import now, now_iso
from ..events import EventBus
from ..models import (BIOGRAPHY_SCHEMA_VERSION, CredentialInfo, InstagramProfileDTO, InteractionDTO,
                      InteractionStatus, InteractionType, MemoryItemDTO, OBJECTIVE_SETTLED, PersonaCreate,
                      PersonaDTO, PersonaDeviceBody, PersonaDraft, PersonaGeneration, PersonaOnDeviceDTO,
                      PersonaPatch, PolicyGroupDTO, PolicyGroupMember,
                      ProfileAccountDTO, ProfilePolicyDTO, SessionInfo, SessionStatus, SocialContextDTO,
                      SocialDraftDTO, voice_gaps)
from ..modules.identity.domain.persona import (CRENCAS_MINIMAS, MAIORIDADE, idade_em, lacunas_da_biografia,
                                               mesclar_secao, nome_exibido, normalizar_biografia, separar_nome,
                                               separar_visual_legado)
from ..modules.identity.domain.persona_generation import (PersonaEvitada, PersonaGenerationRequest,
                                                            preencher_vazios, problemas_do_rascunho, textos_de)
from ..modules.identity.presentation.schemas import CredentialClone, PersonaGenerateBody
from ..planning.capabilities import load_catalog
from ..planning.catalog import capabilities_of, pacote_ancora, session_provider_of
from ..planning.provider import AIError, SocialRequest, Usage
from ..security.redaction import looks_secret, mentions_credential, redact, redact_obj
from ..security.secret_store import SecretStore, SecretStoreLocked, SecretStoreUnavailable
from ..security.sessions import operador_atual
from .context import SocialContextBuilder, interaction_dto
from .conteudo import fala_atribuida_a_terceiro
from ..modules.identity.application.session_rules import PRECISA_DE_PESSOA, emit_needs_person_change
from .contas_nossas import emails_so_desta_conta, handle_vivo, sem_o_rastro
from .limpeza_de_conta import PedidoDeLimpeza
from .memory import MemoryRefused, MemoryStore, reescrever_memoria
from .policy import CONTAM, DEFAULT_LIMITS, PolicyEngine, com_politicas_do_app, politicas_do_app
from .repository import (AparelhoEmQuarentena, BindingConflict, SocialRepository, campos_de_persona,
                         sessao_vencida)

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
        # ADR-055: toda mudança de status do perfil e todo marcador de conta travada viram evento persistido — o
        # repositório grava, e quem tem o barramento anuncia. Vale também para quem escreve pelo repositório por
        # fora deste serviço (o bloqueio por desafio do ADR-029, o detector de tela).
        repo.on_status_changed = self._anunciar_status
        repo.on_locked_account = self._anunciar_conta_travada
        #: Limpezas de OUTROS módulos para a retirada da conta bloqueada (29.23): cada uma recebe `(db, *, profile_id,
        #: account_id, handle, app_id)` e devolve contagens `{nome: n}`; roda em ordem DENTRO da transação da
        #: retirada, e uma que levanta erro desfaz a retirada inteira. O módulo de Aprendizado registra a dele aqui.
        self.limpezas_ao_retirar: list[Callable[..., dict[str, int]]] = []
        #: Depois da retirada: `(profile_id, account_id, estava_bloqueada)`. O AppState liga o disjuntor de conta
        #: (ADR-055) aqui: o agendador só o dispara ao VER `blocked`, e a retirada devolve a persona a `active`.
        self.ao_retirar_conta: Callable[[str, str, bool], None] | None = None
        #: Depois da retirada, com o pedido de limpeza CAPTURADO antes dela (29.27, emenda do ADR-068): o AppState liga a
        #: tarefa de fundo que limpa os dados do app nos aparelhos onde a conta estava logada e resolve a quarentena.
        #: Só é chamado quando o app da conta DECLARA `limpar_ao_retirar` e há aparelho; sem a ligação (testes, scripts),
        #: a retirada é só banco, como na 29.23.
        self.ao_limpar_aparelhos: Callable[[PedidoDeLimpeza], None] | None = None
        self._retirando: set[tuple[str, str]] = set()
        #: O SINAL FORTE de bloqueio (29.23): `instance_id -> a atividade de desafio do Instagram está em foco agora`.
        #: O AppState liga ao que `DeviceManager.observe` leu (`DeviceRuntime.atividade_de_desafio`). Só texto na tela
        #: NÃO retira a conta; sem esta ligação (testes, scripts) só a declaração do dono (`declarado`) retira.
        self.sinal_de_desafio: Callable[[str], bool] | None = None
        repo.on_conta_bloqueada = self._retirar_por_bloqueio

    # ------------------------------------------------------------------ consulta
    def list_profiles(self) -> list[InstagramProfileDTO]:
        return [dto for pid in self.repo.list_profile_ids() if (dto := self.repo.profile_dto(pid))]

    def get_profile(self, profile_id: str) -> InstagramProfileDTO:
        dto = self.repo.profile_dto(profile_id)
        if dto is None:
            raise SocialError("not_found", "Perfil não encontrado.", 404)
        return dto

    def instance_of(self, profile_id: str) -> str | None:
        """O aparelho PRINCIPAL desta persona agora: alvo padrão de quem age "pelo perfil" sem dizer o aparelho."""
        row = self.repo.binding_principal(profile_id)
        return row["instance_id"] if row else None

    def instances_of(self, profile_id: str) -> list[str]:
        """Todos os aparelhos vinculados à persona, o principal primeiro (vínculo N:N)."""
        return [str(r["instance_id"]) for r in self.repo.bindings_of_profile(profile_id)]

    def profile_of(self, instance_id: str) -> str | None:
        """Compatibilidade: a persona do aparelho quando há UMA; `ValueError` com mais de uma (ver repositório)."""
        return self.repo.profile_id_for_instance(instance_id)

    def profiles_of(self, instance_id: str) -> list[str]:
        """Todas as personas vinculadas ao aparelho."""
        return [str(r["profile_id"]) for r in self.repo.profiles_of_instance(instance_id)]

    def profile_for_instance(self, instance_id: str) -> InstagramProfileDTO | None:
        pid = self.repo.perfil_unico_da_instancia(instance_id)
        return self.repo.profile_dto(pid) if pid else None

    # ------------------------------------------------------------------ cadastro
    def create_profile(self, body: Any) -> InstagramProfileDTO:
        """Cadastro de uma CONTA do Instagram. Com `persona_id` de uma pessoa ainda sem conta, é ela que ganha a
        conta (mesma linha, mesmo id — a persona é a pessoa desde a 047); sem `persona_id`, nasce uma pessoa nova."""
        if self.repo.profile_by_username(body.username):
            raise SocialError("duplicate_username", f"Já existe um perfil para @{body.username}.")
        pessoa = self._pessoa_sem_conta(body.persona_id)
        self._check_group(body.policy_group_id)
        self._check_instance(body.instance_id)
        if body.password and self.secrets.status() != "ready":
            raise SocialError("secret_store_unavailable", self._vault_message(), 503)
        app_da_conta = self._app_do_pacote(pacote_ancora())
        if app_da_conta:
            # D2-a conferida ANTES de qualquer linha: o 409 quer dizer "nada foi criado", e não "a pessoa nasceu
            # sem aparelho" — o cadastro que falha pela metade deixava perfil, conta e senha órfãos. Vale para o
            # aparelho do cadastro E para os vínculos sem app que a pessoa já tinha (29.29).
            self._recusar_conta_que_quebra_d2a(str(pessoa["id"]) if pessoa is not None else None, app_da_conta,
                                               body.instance_id)

        display_name = body.display_name or (f"{body.first_name or ''} {body.last_name or ''}".strip() or None)
        if pessoa is not None:
            profile_id = str(pessoa["id"])
            self.repo.adopt_account(profile_id, username=body.username, first_name=body.first_name,
                                    last_name=body.last_name, display_name=display_name, birth_date=body.birth_date,
                                    email=body.email)
        else:
            profile_id = self.repo.create_profile(
                username=body.username, first_name=body.first_name, last_name=body.last_name,
                display_name=display_name, birth_date=body.birth_date, email=body.email, persona_id=None)
        if body.policy_group_id:
            self.repo.update_profile(profile_id, {"policy_group_id": body.policy_group_id})
        # Item 12.1: o perfil é a identidade; a conta do app âncora (a que o cadastro sempre descreveu) é a primeira.
        if app_da_conta:
            self.repo.create_account(profile_id, app_id=app_da_conta, handle=body.username)
        if body.password:
            # O e-mail é o identificador que o Instagram aceita sempre; o @usuário pode nem resolver (visto no
            # aparelho: login por @usuário devolvia "Unable to log in" e por e-mail entrava).
            self._store_password(profile_id, body.login_identifier or body.email or body.username, body.password)
        if body.instance_id:
            # O vínculo do cadastro é o da conta do app âncora (051: `app_id` do vínculo) — o id da conta âncora,
            # que existe mesmo sem o app registrado (aí é o pacote). A recusa de D2-a já foi conferida lá em cima.
            conta = self.repo.conta_ancora(profile_id)
            self._vincular(profile_id, body.instance_id, app_id=conta["app_id"] if conta is not None else app_da_conta,
                           reason="cadastro")
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
        if "persona_id" in fields:
            # A persona é a própria pessoa: o id de uma persona SEM conta é absorvido aqui (o painel de hoje "cria a
            # persona" e depois a aponta no perfil); o de outra pessoa com conta é recusado. Nada vai para a coluna.
            alvo = fields.pop("persona_id")
            if alvo:
                self._absorver_persona(profile_id, alvo)
        if "policy_group_id" in fields:
            self._check_group(fields["policy_group_id"])
        # O status é DECLARAÇÃO de uma pessoa (a tela do perfil): vai por `mudar_status`, com origem e autor, e
        # nunca como coluna solta — era assim que cinco perfis ficaram `blocked` sem quando nem por quê (ADR-055).
        status = fields.pop("status", None)
        if fields:
            self.repo.update_profile(profile_id, fields)
        if status is not None:
            autor = operador_atual() or "painel"
            self.repo.mudar_status(profile_id, str(status), origem="declarado", autor=autor,
                                   evidencia=f"declarado por {autor} na tela do perfil")
        if instance_id != "__ausente__":
            self._rebind(profile_id, instance_id, confirmado=confirmado)
        return self.get_profile(profile_id)

    def _anunciar_status(self, profile_id: str, anterior: str, novo: str, origem: str, autor: str,
                         evidencia: str | None) -> None:
        """Evento `profile.status` de CADA mudança de status (ADR-055): o rastro de quem bloqueou, reativou ou
        pausou, de onde veio a afirmação e o que foi visto — o que faltava para dizer por que cinco contas estão
        `blocked`."""
        linha = self.repo.profile_row(profile_id)
        arroba = (linha["username"] or linha["display_name"] or profile_id) if linha is not None else profile_id
        self.bus.emit("profile.status", f"Perfil @{arroba}: {anterior} → {novo} ({origem}, por {autor})"
                      + (f" — {evidencia}" if evidencia else ""),
                      level="warn" if novo == "blocked" else "info",
                      data={"profile_id": profile_id, "anterior": anterior, "status": novo, "origem": origem,
                            "autor": autor, "evidencia": (evidencia or "")[:500] or None})

    def _anunciar_conta_travada(self, marcador: Row, acao: str, autor: str) -> None:
        """Evento `device.locked_account`: o aparelho entrou em quarentena (ou saiu dela) — aviso ao dono."""
        iid = str(marcador["instance_id"])
        conta = self.repo.citacao_da_conta(marcador)  # a conta já retirada (29.23) não volta ao aviso com o @
        if acao == "marcado":
            texto = (f"{iid}: {conta} travada e logada ({marcador['origin']}, por {autor}). "
                     "O aparelho entrou em quarentena: nada o toca além de parar ou hibernar até você decidir.")
        else:
            texto = f"{iid}: a quarentena da {conta} foi resolvida por {autor}."
        self.bus.emit("device.locked_account", texto, level="error" if acao == "marcado" else "info",
                      instance_id=iid,
                      data={"instance_id": iid, "handle": self.repo.rotulo_da_conta(marcador).lstrip("@"), "profile_id": marcador["profile_id"],
                            "app_id": marcador["app_id"], "origem": marcador["origin"], "acao": acao, "autor": autor,
                            "evidencia": marcador["evidence"]})

    def delete_profile(self, profile_id: str) -> None:
        """Apagar o perfil apaga a credencial junto — inclusive o ciphertext no cofre."""
        self.get_profile(profile_id)
        ref = self.repo.delete_credential(profile_id)
        if ref:
            self.secrets.delete_secret(ref)
        # As outras contas também: a linha de `account_credentials` cai em cascata com o perfil, mas o ciphertext no
        # cofre, não — e a senha clonada para o Outlook (ADR-057) ficaria órfã para sempre.
        for conta in self.repo.list_accounts(profile_id):
            self._apagar_credencial(profile_id, self.repo.delete_account_credential(profile_id, conta["id"]))
        self.repo.delete_profile(profile_id)
        self.bus.emit("log", "Perfil removido, com a credencial apagada do cofre", data={"profile_id": profile_id})

    # ------------------------------------------------------------------ credencial (só escrita; é da CONTA)
    # ADR-040: a credencial pertence à conta da persona (perfil × app × host), com consentimento por conta. As
    # rotas por perfil (`/credential`) são apelidos da conta âncora — a do app que provê a conta do perfil.
    def set_credential(self, profile_id: str, body: Any, *, by: str = "painel") -> InstagramProfileDTO:
        self.get_profile(profile_id)
        conta = self._conta_ancora(profile_id)
        self._gravar_credencial(profile_id, conta, password=body.password, login_identifier=body.login_identifier,
                                consent=bool(body.consent), by=by)
        return self.get_profile(profile_id)

    def delete_credential(self, profile_id: str) -> InstagramProfileDTO:
        self.get_profile(profile_id)
        self._apagar_credencial(profile_id, self.repo.delete_credential(profile_id))
        return self.get_profile(profile_id)

    def _conta_ancora(self, profile_id: str) -> Row:
        conta = self.repo.conta_ancora(profile_id, criar=True)
        if conta is None:
            raise SocialError("no_account", "Nenhum aplicativo registrado provê a conta deste perfil.", 409)
        return conta

    def _apagar_credencial(self, profile_id: str, ref: str | None) -> None:
        # Enquanto a linha legada (`instagram_credentials`, só leitura) apontar para a mesma referência, o segredo
        # fica: apagar seria destruir o que a outra linha ainda referencia. Some quando a migração posterior a tirar.
        if not ref:
            return
        if self.repo.db.scalar("SELECT COUNT(*) FROM instagram_credentials WHERE secret_ref=?", (ref,)):
            return
        # A mesma rede para outra CONTA com a mesma referência (dado antigo; a clonagem do ADR-057 sempre cria
        # entrada própria): apagar a senha de uma conta nunca pode apagar a de outra.
        if self.repo.db.scalar("SELECT COUNT(*) FROM account_credentials WHERE secret_ref=?", (ref,)):
            return
        self.secrets.delete_secret(ref)

    def _gravar_credencial(self, profile_id: str, conta: Row, *, password: SecretStr, login_identifier: str | None,
                           consent: bool, by: str) -> None:
        """Um caminho só para toda conta, com ou sem provedor de sessão.

        Consentimento POR CONTA (ADR-040): guardar a senha dizendo `consent: true` é a pessoa autorizando a
        automação a digitá-la — pelo canal sensível, só no app (e no site) desta conta. Sem a marca, nem o
        `type_secret` nem o provedor de sessão a usam, e a conta que ainda não consentiu recebe 409 em vez de
        guardar em silêncio uma senha que ninguém pode digitar.
        """
        if not consent and not self._consentida(profile_id, conta["id"]):
            app = self._app_row(conta["app_id"])
            onde = (app["name"] if app else conta["app_id"]) + (f" ({conta['host']})" if conta["host"] else "")
            raise SocialError(
                "consentimento_de_credencial",
                f"Guardar esta senha autoriza a automação a DIGITÁ-LA na tela de {onde} — e só nela — pelo canal "
                "sensível, sem que a IA veja o valor; desafio, 2FA e CAPTCHA continuam com você. Confirme com "
                "consent=true para guardar.", 409)
        if self.secrets.status() != "ready":
            raise SocialError("secret_store_unavailable", self._vault_message(), 503)
        login = self._login_da_conta(profile_id, conta, login_identifier)
        self._guardar_senha(profile_id, conta["id"], login_identifier=login, password=password,
                            consent_by=by if consent else None)
        self.bus.emit("log", "Senha de conta atualizada", data={"profile_id": profile_id, "account_id": conta["id"]})

    def _consentida(self, profile_id: str, account_id: str) -> bool:
        cred = self.repo.account_credential_row(profile_id, account_id)
        return cred is not None and cred["consent_at"] is not None

    def _login_da_conta(self, profile_id: str, conta: Row, informado: str | None) -> str:
        """O identificador com que a conta entra. Para app com provedor, o e-mail é o que o Instagram aceita sempre
        (login por @usuário devolvia "Unable to log in" no aparelho).

        Transitório (relatório 01, §10.1): a aba Contas do painel manda `login_identifier` igual ao `handle`, e isso
        trocava o e-mail gravado pelo @ — estragando o login. Enquanto o painel não for corrigido, o handle que
        chega igual ao gravado como `handle`, havendo um identificador já guardado, não o substitui.
        """
        atual = self.repo.account_credential_row(profile_id, conta["id"])
        gravado = atual["login_identifier"] if atual else None
        app = self._app_row(conta["app_id"])
        com_provedor = app is not None and session_provider_of(app["package"]) is not None
        if informado and com_provedor and gravado and informado.strip().lstrip("@") == (conta["handle"] or "").lstrip("@"):
            return gravado
        if informado:
            return informado
        if gravado:
            return gravado
        perfil = self.repo.profile_row(profile_id)
        email = perfil["email"] if perfil is not None and perfil["email"] else None
        if com_provedor:
            # O e-mail primeiro (é o que o Instagram aceita); o @ só na falta dele.
            return email or conta["handle"] or (perfil["username"] if perfil is not None else "") or ""
        # App comum: quem entra é o `handle` da conta (o usuário daquele app); o e-mail do perfil é o último recurso.
        return conta["handle"] or email or ""

    def _store_password(self, profile_id: str, login_identifier: str, password: Any) -> None:
        """Cadastro do perfil (`create_profile`): a senha da conta âncora.

        O formulário de cadastro existe para o login automático (ADR-009: "a senha só entra pelo portal", para o
        "Conectar" digitá-la pelo canal sensível), então a senha dada ali já vem com o consentimento assinado como
        "cadastro do perfil". Uma marca explícita (`consent` em `ProfileCreate`) fica para a onda do painel.
        """
        conta = self._conta_ancora(profile_id)
        self._guardar_senha(profile_id, conta["id"], login_identifier=login_identifier, password=password,
                            consent_by="cadastro do perfil")

    def _guardar_senha(self, profile_id: str, account_id: str, *, login_identifier: str, password: SecretStr,
                       consent_by: str | None) -> None:
        """A senha existe como texto apenas nestas linhas, e some junto com o quadro da função.

        Reusa a referência que a conta já tem: `store_secret` sobrescreve no lugar (nonce novo, mesma AAD). Gerar
        referência nova a cada troca deixaria o texto cifrado ANTERIOR órfão no cofre para sempre — e nem apagar a
        conta o removeria, porque só a referência atual é apagada.
        """
        atual = self.repo.account_credential_row(profile_id, account_id)
        ref_atual = atual["secret_ref"] if atual else None
        try:
            ref = self.secrets.store_secret(password.get_secret_value(), ref=ref_atual)
        except (SecretStoreLocked, SecretStoreUnavailable) as exc:
            raise SocialError("secret_store_unavailable", str(exc), 503) from None
        self.repo.set_account_credential(profile_id, account_id, login_identifier=login_identifier, secret_ref=ref,
                                         key_id=self.secrets.provider.key_id, consent_by=consent_by)

    # ------------------------------------------------------------------ vínculo
    def _vincular(self, profile_id: str, instance_id: str, *, app_id: str | None, primary: bool = False,
                  reason: str | None = None) -> None:
        """`repo.bind` com a recusa de D2-a traduzida em 409 `conta_do_app_ja_no_aparelho`."""
        try:
            self.repo.bind(profile_id, instance_id, app_id=app_id, primary=primary, reason=reason)
        except (BindingConflict, AparelhoEmQuarentena) as exc:
            raise SocialError(exc.code, str(exc), 409) from exc

    def _recusar_conta_que_quebra_d2a(self, profile_id: str | None, app_id: str,
                                      instance_id: str | None = None) -> None:
        """409 `conta_do_app_ja_no_aparelho` quando a persona, ao ganhar a conta em `app_id`, passaria a servir (por
        um vínculo sem app, ou pelo aparelho do cadastro) um app que outra persona já serve naquele aparelho."""
        if (conflito := self.repo.conflito_da_conta_nova(profile_id, app_id, instance_id)) is not None:
            iid, app, outra = conflito
            raise SocialError(BindingConflict.code, str(BindingConflict(iid, app, outra)), 409)

    def _rebind(self, profile_id: str, instance_id: str | None, *, confirmado: bool = False) -> None:
        """`PATCH instance_id` (o painel de hoje, "trocar de aparelho"): o vínculo da conta âncora sai do aparelho
        PRINCIPAL e entra no novo, como principal. Não toma o aparelho de ninguém (051): outra conta do mesmo app lá
        → 409 e nada muda. `null` desvincula de todos. O caminho N:N (somar aparelho) é `bind_device`."""
        current = self.repo.binding_principal(profile_id)
        if instance_id is None:
            if current:
                self.repo.unbind(profile_id, reason="desvinculado pelo usuário")
            return
        self._check_instance(instance_id)
        if current and current["instance_id"] == instance_id:
            return
        self._recusar_troca_de_servidor(profile_id, current, instance_id, confirmado=confirmado)
        conta = self.repo.conta_ancora(profile_id)
        app_id = conta["app_id"] if conta is not None else None
        with self.repo.db.tx():
            # Primeiro entra no novo (a recusa de D2-a acontece aqui, antes de tirar o antigo); só então sai do antigo.
            self._vincular(profile_id, instance_id, app_id=app_id, primary=True, reason="troca de aparelho")
            if current is not None:
                self.repo.unbind(profile_id, str(current["instance_id"]), reason="troca de aparelho")
                self.repo.set_primary(profile_id, instance_id)
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
        # Sessão pronta de QUALQUER conta da persona (item 23.4): a do segundo app de login gerenciado fica no mesmo
        # disco que a da âncora, e se perde do mesmo jeito.
        if not any((sessao := self.repo.session_of_account(profile_id, str(c["id"]))) is not None
                   and sessao["status"] == SessionStatus.session_ready.value
                   for c in self.repo.list_accounts(profile_id)):
            return
        onde = atual["worker_id"] or "este servidor"
        para = destino_worker or "este servidor"
        raise SocialError(
            "locality_change_requires_confirmation",
            f"Os dados deste perfil vivem em {onde} e {destino} está em {para}. A sessão do "
            f"{self._rotulo_ancora()} não "
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
        # Quarentena (ADR-055): conta travada logada no aparelho. Conferida AQUI — antes de qualquer linha no
        # cadastro, e antes de tirar o vínculo antigo na troca — para o 409 querer dizer "nada mudou".
        if (marcador := self.repo.conta_travada_no_aparelho(instance_id)) is not None:
            raise SocialError(AparelhoEmQuarentena.code,
                              str(AparelhoEmQuarentena(marcador, self.repo.citacao_da_conta(marcador))), 409)

    # ------------------------------------------------------------------ aparelhos da persona (N:N, 051)
    def bind_device(self, persona_id: str, body: PersonaDeviceBody) -> PersonaDTO:
        """`POST /personas/{id}/devices`: soma um aparelho à persona PARA um app (sem app = apps sem conta
        gerenciada). Não move ninguém: o que já estava vinculado continua. Duas contas do mesmo app no mesmo
        aparelho → 409 `conta_do_app_ja_no_aparelho` (D2-a). App desconhecido, aparelho desconhecido ou a loja → 400.
        A mesma conta em N aparelhos é permitida (D3): o Instagram pode pedir verificação, e o ADR-029 bloqueia a
        persona se pedir — o painel avisa antes."""
        pid = str(self._linha_da_pessoa(persona_id)["id"])
        self._check_instance(body.instance_id)
        if body.app_id is not None and self._app_row(body.app_id) is None:
            raise SocialError("unknown_app", f"App não cadastrado: {body.app_id}.", 400)
        self._vincular(pid, body.instance_id, app_id=body.app_id, primary=body.primary, reason="vinculado pelo usuário")
        # Aparelho novo, sessão nova NAQUELE aparelho: a sessão é do par (conta, aparelho) e nasce por verificar.
        conta = self.repo.account_by_app(pid, body.app_id) if body.app_id else self.repo.conta_ancora(pid)
        if conta is not None and self.repo.account_session_row(pid, conta["id"], body.instance_id) is None:
            self.repo.set_account_session(pid, conta["id"], body.instance_id, status=SessionStatus.unknown,
                                          detail="Aparelho vinculado; a sessão precisa ser verificada.")
        self.bus.emit("log", f"Persona vinculada a {body.instance_id}" + (f" ({body.app_id})" if body.app_id else ""),
                      data={"profile_id": pid, "instance_id": body.instance_id, "app_id": body.app_id})
        return self.get_profile(pid)

    def unbind_device(self, persona_id: str, instance_id: str, app_id: str | None = None) -> PersonaDTO:
        """`DELETE /personas/{id}/devices/{iid}`: desvincula daquele aparelho (com `app_id`, só daquele app).
        404 quando o par não está vinculado; 409 com execução em curso desta persona naquele aparelho."""
        pid = str(self._linha_da_pessoa(persona_id)["id"])
        if self.repo.binding(pid, instance_id, app_id) is None:
            raise SocialError("not_bound", f"Esta persona não está vinculada a {instance_id}"
                              + (f" para {app_id}" if app_id else "") + ".", 404)
        if self._execucao_em_curso(pid, instance_id):
            raise SocialError("persona_in_use", f"Esta persona tem execução em andamento em {instance_id}. Espere "
                                                "terminar ou cancele antes de desvincular.", 409)
        self.repo.unbind(pid, instance_id, app_id, reason="desvinculado pelo usuário")
        self.bus.emit("log", f"Persona desvinculada de {instance_id}",
                      data={"profile_id": pid, "instance_id": instance_id, "app_id": app_id})
        return self.get_profile(pid)

    def set_primary_device(self, persona_id: str, instance_id: str) -> PersonaDTO:
        """`PUT /personas/{id}/devices/{iid}/primary`: o aparelho principal passa a ser este (precisa estar vinculado)."""
        pid = str(self._linha_da_pessoa(persona_id)["id"])
        try:
            self.repo.set_primary(pid, instance_id)
        except KeyError:
            raise SocialError("not_bound", f"Esta persona não está vinculada a {instance_id}.", 404) from None
        return self.get_profile(pid)

    def personas_of_instance(self, instance_id: str) -> list[PersonaOnDeviceDTO]:
        """`GET /instances/{id}/personas`: quem está neste aparelho, por vínculo, com a sessão da conta do app do
        vínculo NESTE aparelho (a do app âncora quando o vínculo não tem app)."""
        saida: list[PersonaOnDeviceDTO] = []
        for v in self.repo.profiles_of_instance(instance_id):
            pid = str(v["profile_id"])
            linha = self.repo.profile_row(pid)
            if linha is None:
                continue
            saida.append(PersonaOnDeviceDTO(
                profile_id=pid, username=linha["username"] or None, display_name=linha["display_name"],
                name=str(campos_de_persona(linha)["name"]), status=linha["status"] or "active", app_id=v["app_id"],
                is_primary=bool(v["is_primary"]), bound_at=v["bound_at"],
                session=self.repo.sessao_no_aparelho(pid, v["app_id"], instance_id),
                has_avatar=self.repo.tem_avatar(pid)))
        return saida

    # ------------------------------------------------------------------ personas (= pessoas)
    # Desde a 047 "persona" e "perfil" são a MESMA linha. `list_personas` devolve todas as pessoas (com ou sem
    # conta); `list_profiles` continua devolvendo só quem tem conta de cadastro, que é o que a tela de perfis lista.
    def list_personas(self) -> list[PersonaDTO]:
        return [dto for pid in self.repo.list_persona_ids() if (dto := self.repo.profile_dto(pid))]

    def get_persona(self, persona_id: str) -> PersonaDTO:
        return self.get_profile(str(self._linha_da_pessoa(persona_id)["id"]))

    def create_persona(self, body: PersonaCreate) -> PersonaDTO:
        """Uma pessoa nova, sem conta em app nenhum. `traits` com as chaves visuais antigas vira `visual`."""
        voz, visual_legado = separar_visual_legado(body.traits.model_dump(exclude_none=True))
        visual = mesclar_secao(visual_legado, body.visual.model_dump(exclude_unset=True))
        biografia = mesclar_secao({"schema_version": BIOGRAPHY_SCHEMA_VERSION},
                                  body.biography.model_dump(exclude_unset=True))
        origem = body.generation.model_dump(exclude_none=True) if body.generation is not None else {}
        origem.setdefault("source", "manual")
        origem.setdefault("at", now_iso())
        primeiro, ultimo = body.first_name, body.last_name
        if not primeiro:
            primeiro, ultimo = separar_nome(body.name)
        persona_id = self.repo.create_persona(
            name=body.name, summary=body.summary, persona_prompt=body.persona_prompt, traits=voz, visual=visual,
            biography=biografia, generation=origem, first_name=primeiro, last_name=ultimo, display_name=body.name,
            birth_date=body.birth_date, gender=body.gender, locale=body.locale)
        self.bus.emit("log", f"Persona {body.name} criada", data={"profile_id": persona_id})
        return self.get_persona(persona_id)

    def update_persona(self, persona_id: str, body: PersonaPatch) -> PersonaDTO:
        """PATCH parcial POR SEÇÃO: `traits`, `visual` e `biography` são mesclados chave a chave sobre o que está
        gravado (`mesclar_secao`); nulo explícito apaga, ausente fica. Nulo em `name`/`persona_prompt` é "não mexer":
        apagar o nome não é um pedido."""
        row = self._linha_da_pessoa(persona_id)
        pid = str(row["id"])
        fields = body.model_dump(exclude_unset=True)
        campos: dict[str, object] = {}
        for chave in ("summary", "birth_date", "gender", "locale", "first_name", "last_name", "display_name"):
            if chave in fields:
                campos[chave] = fields[chave]
        if fields.get("name") is not None:
            campos["display_name"] = fields["name"]
            if not row["first_name"] and "first_name" not in fields:
                campos["first_name"], campos["last_name"] = separar_nome(fields["name"])
        if fields.get("persona_prompt") is not None:
            campos["persona_prompt"] = fields["persona_prompt"]
        visual_patch: dict[str, object] = {}
        if body.traits is not None:
            voz, visual_patch = separar_visual_legado(body.traits.model_dump(exclude_unset=True))
            campos["traits"] = dumps(mesclar_secao(loads(row["traits"], {}) or {}, voz))
        if body.visual is not None:
            visual_patch = {**visual_patch, **body.visual.model_dump(exclude_unset=True)}
        if visual_patch:
            campos["visual"] = dumps(mesclar_secao(loads(row["visual"], {}) or {}, visual_patch))
        if body.biography is not None:
            # A mescla é sobre o JSON CRU: uma linha v1 (crença em texto) passa antes para a forma atual, ou mudar
            # `religion.practice` trocaria a frase antiga por um objeto só com a prática. Escrever a biografia é
            # também o momento de gravá-la na versão nova (ADR-048: a v1 vira v2 na leitura e na próxima escrita).
            atual = normalizar_biografia(loads(row["biography"], {}) or {})
            atual["schema_version"] = BIOGRAPHY_SCHEMA_VERSION
            campos["biography"] = dumps(mesclar_secao(atual, body.biography.model_dump(exclude_unset=True)))
        self.repo.update_persona(pid, campos)
        return self.get_persona(pid)

    def delete_persona(self, persona_id: str) -> None:
        """Apagar a persona é apagar a PESSOA — contas, credencial (e o ciphertext no cofre), memória e histórico vão
        junto. Recusa enquanto ela estiver vinculada a um aparelho ou com execução em curso: apagar alguém que está
        agindo num aparelho deixaria a execução sem dono."""
        pid = str(self._linha_da_pessoa(persona_id)["id"])
        if self.repo.bindings_of_profile(pid):
            raise SocialError("persona_in_use", "Esta pessoa está vinculada a um aparelho. Desvincule antes de apagar.")
        if self._execucao_em_curso(pid):
            raise SocialError("persona_in_use", "Esta pessoa tem execução em andamento. Espere terminar ou cancele "
                                                "antes de apagar.")
        self.delete_profile(pid)

    # ------------------------------------------------------------------ geração por IA (paga)
    async def generate_persona_draft(self, body: PersonaGenerateBody, *, avoid: Sequence[PersonaEvitada] = (),
                                     variation: int | None = None,
                                     variety: Mapping[str, str] | None = None) -> PersonaCreate:
        """`POST /personas/generate`: um RASCUNHO, não gravado, no formato que `POST /personas` aceita.

        Chamada paga pelo papel `social` (o hub confere o teto do dia). O rascunho só volta se passar nas regras do
        domínio (`problemas_do_rascunho`: nome de pessoa fictícia, maior de idade, voz e biografia completas) e sem
        nenhum texto com formato de segredo — o modelo é instruído, e aqui se confere.

        O lote (`social/persona_batch.py`) passa por aqui também, com `avoid` (quem não repetir) e `variation` (o
        índice do item): mesmas regras, mesma proveniência, só o pedido ganha o bloco `<evitar>`.
        """
        hoje = now().date()
        pedido = PersonaGenerationRequest(prompt=body.prompt, locale=body.locale or "pt-BR",
                                          constraints=dict(body.constraints), today=hoje, avoid=tuple(avoid),
                                          variation=variation, variety=dict(variety or {}))
        draft, usage = await self._generate_persona(pedido)
        problemas = problemas_do_rascunho(nome=draft.name, birth_date=draft.birth_date,
                                          lacunas_de_voz=voice_gaps(draft.traits),
                                          biography=draft.biography.model_dump(exclude_none=True), hoje=hoje)
        problemas += self._textos_com_segredo(draft)
        if problemas:
            raise SocialError("persona_draft_invalid", "O modelo devolveu um rascunho que não serve: "
                              + "; ".join(problemas) + ". Peça de novo, ajustando o pedido.", 422)
        primeiro, ultimo = separar_nome(draft.name)
        return PersonaCreate(
            name=draft.name, summary=draft.summary, persona_prompt=draft.persona_prompt, traits=draft.traits,
            first_name=primeiro, last_name=ultimo, birth_date=draft.birth_date, gender=draft.gender,
            locale=draft.locale or pedido.locale, biography=draft.biography, visual=draft.visual,
            generation=self._proveniencia("ai", usage, prompt=body.prompt))

    async def enrich_persona(self, persona_id: str, *, instructions: str | None = None) -> PersonaDTO:
        """`POST /personas/{id}/enrich`: completa SÓ o que está vazio. Idempotente: sem lacuna, não chama o modelo;
        com lacuna, o que já existia nunca é reescrito (`preencher_vazios`). `instructions` é o pedido do dono para o
        que falta; vai ao modelo marcado como pedido (o construtor do texto passa tudo por `sem_marcacao`), e texto
        com cara de credencial é recusado ANTES da chamada: o pedido vai ao provedor e fica na proveniência."""
        instrucao = (instructions or "").strip()
        if instrucao and (looks_secret(instrucao) or mentions_credential(instrucao)):
            raise SocialError("instructions_with_secret", "As instruções parecem conter uma senha, um código ou um "
                              "token. Elas vão ao provedor de IA: descreva a pessoa sem nenhum valor secreto.", 422)
        dto = self.get_persona(persona_id)
        existente: dict[str, object] = {
            "name": dto.name, "summary": dto.summary, "gender": dto.gender, "locale": dto.locale,
            "birth_date": dto.birth_date, "persona_prompt": dto.persona_prompt,
            "traits": dto.traits.model_dump(exclude_none=True), "visual": dto.visual.model_dump(exclude_none=True),
            "biography": dto.biography.model_dump(exclude_none=True)}
        if not self._tem_lacuna(dto):
            return dto
        hoje = now().date()
        prompt = f"Complete a persona {dto.name} mantendo tudo o que já existe."
        if instrucao:
            prompt += (" Para o que falta, siga estas instruções do dono (sem reescrever o que já está preenchido): "
                       + instrucao)
        pedido = PersonaGenerationRequest(prompt=prompt, locale=dto.locale or "pt-BR", existing=existente, today=hoje)
        draft, usage = await self._generate_persona(pedido)
        problemas = self._textos_com_segredo(draft)
        idade = idade_em(draft.birth_date, hoje)
        if draft.birth_date and (idade is None or idade < MAIORIDADE):
            problemas.append("a data de nascimento sugerida não é de uma pessoa adulta")
        if problemas:
            raise SocialError("persona_draft_invalid", "O modelo devolveu um complemento que não serve: "
                              + "; ".join(problemas) + ".", 422)
        novo = preencher_vazios(existente, draft.model_dump(exclude_none=True))
        campos: dict[str, object] = {}
        for chave in ("summary", "persona_prompt", "gender", "locale", "birth_date"):
            if not existente.get(chave) and novo.get(chave):
                campos[chave] = novo[chave]
        for chave in ("traits", "visual", "biography"):
            if novo.get(chave) != existente.get(chave):
                campos[chave] = dumps(novo[chave])
        origem = dto.generation.model_dump(exclude_none=True)
        origem.update(self._proveniencia(origem.get("source") or "manual", usage).model_dump(exclude_none=True))
        origem["enriched_at"] = now_iso()
        origem["source"] = dto.generation.source or "manual"
        campos["generation"] = dumps(origem)
        self.repo.update_persona(dto.id, campos)
        self.bus.emit("log", f"Persona {dto.name} enriquecida por IA ({', '.join(sorted(campos))})",
                      data={"profile_id": dto.id})
        return self.get_persona(dto.id)

    async def _generate_persona(self, pedido: PersonaGenerationRequest) -> tuple[PersonaDraft, Usage]:
        if self.provider is None:
            raise SocialError("ai_unavailable", "Nenhum provedor de IA disponível para gerar a persona.", 503)
        # Uma repetição, e só quando o modelo devolve JSON quebrado: desde o K-042 o esquema do rascunho vai no TEXTO
        # (sem gramática), e medido em 28/09 uma geração em ~8 voltou com aspas sem escape ("Expecting ',' delimiter").
        # Orçamento, recusa e erro de rede não se repetem aqui — repetir não os resolve e custaria outra chamada.
        for tentativa in (1, 2):
            try:
                draft, usage = await self.provider.generate_persona(pedido)
                break
            except AIError as exc:
                if exc.kind == "invalid_output" and tentativa == 1:
                    log.warning("rascunho de persona inválido; repetindo uma vez: %s", str(exc)[:200])
                    continue
                codigo = "ai_budget" if exc.kind == "budget" else "ai_refusal" if exc.kind == "refusal" else "ai_error"
                raise SocialError(codigo, str(exc), 503) from None
        if self.usage_sink is not None:
            try:
                self.usage_sink(usage)
            except Exception:  # noqa: BLE001 - contabilidade de custo nunca derruba a geração
                log.exception("falha ao registrar o uso da geração de persona")
        return draft, usage

    def _proveniencia(self, source: str, usage: Usage, *, prompt: str | None = None) -> PersonaGeneration:
        provedor = usage.provider or getattr(self.provider, "name", None)
        return PersonaGeneration(source=source, prompt=(prompt or None) and prompt[:2000], provider=provedor,
                                 model=usage.model or getattr(self.provider, "model", None), at=now_iso())

    @staticmethod
    def _textos_com_segredo(draft: PersonaDraft) -> list[str]:
        return ["há texto com formato de segredo (código, token ou senha) no rascunho"] \
            if any(looks_secret(t) for t in textos_de(draft.model_dump())) else []

    @staticmethod
    def _tem_lacuna(dto: PersonaDTO) -> bool:
        """Há o que completar? Além do mínimo da biografia, as crenças (`CRENCAS_MINIMAS`): não são exigidas para a
        persona contar como completa, mas quem não as tem ganha crenças ricas ao enriquecer (ADR-048)."""
        bio = dto.biography.model_dump(exclude_none=True)
        return bool(dto.voice_gaps or lacunas_da_biografia(bio) or lacunas_da_biografia(bio, CRENCAS_MINIMAS)
                    or not dto.visual.appearance or not dto.summary or not dto.persona_prompt
                    or (dto.birth_date is None and dto.biography.approx_age is None))

    def _linha_da_pessoa(self, persona_id: str) -> Row:
        row = self.repo.persona_row(persona_id)
        if row is None:
            raise SocialError("not_found", "Persona não encontrada.", 404)
        return row

    def _execucao_em_curso(self, profile_id: str, instance_id: str | None = None) -> bool:
        """A persona tem objetivo não assentado (em qualquer aparelho, ou só em `instance_id`)?"""
        assentados = tuple(s.value for s in OBJECTIVE_SETTLED)
        marcadores = ",".join("?" for _ in assentados)
        sql = f"SELECT COUNT(*) FROM objectives WHERE profile_id=? AND status NOT IN ({marcadores})"   # noqa: S608
        params: tuple[object, ...] = (profile_id, *assentados)
        if instance_id is not None:
            sql += " AND instance_id=?"
            params += (instance_id,)
        return bool(self.repo.db.scalar(sql, params))

    def _pessoa_sem_conta(self, persona_id: str | None) -> Row | None:
        """A pessoa que um cadastro de conta quer adotar. Só uma pessoa SEM conta pode ganhar a conta de cadastro;
        o id de alguém que já tem conta é outra pessoa, e a recusa vira mensagem em vez de erro de banco."""
        if not persona_id:
            return None
        row = self.repo.persona_row(persona_id)
        if row is None:
            raise SocialError("unknown_persona", "Persona não encontrada.", 400)
        if row["username"]:
            raise SocialError("persona_in_use", "Esta persona já é a pessoa de outra conta. Cada conta tem a sua.")
        return row

    def _absorver_persona(self, profile_id: str, persona_id: str) -> None:
        """`PATCH persona_id` numa pessoa que já existe: a voz, a biografia e o visual da persona SEM conta passam
        para esta pessoa, e a linha sem conta some. O id de outra pessoa com conta é recusado."""
        alvo = self.repo.persona_row(persona_id)
        if alvo is None:
            raise SocialError("unknown_persona", "Persona não encontrada.", 400)
        if alvo["id"] == profile_id:
            return
        if alvo["username"]:
            raise SocialError("persona_in_use", "Esta persona é outra pessoa, com conta própria. Cada perfil é a sua.")
        atual = self.repo.profile_row(profile_id)
        if atual is None:
            raise SocialError("not_found", "Perfil não encontrado.", 404)
        campos: dict[str, object] = {"summary": alvo["summary"], "persona_prompt": alvo["persona_prompt"] or "",
                                  "traits": alvo["traits"], "visual": alvo["visual"], "biography": alvo["biography"],
                                  "generation": alvo["generation"]}
        for coluna in ("gender", "locale", "birth_date"):
            if alvo[coluna] and not atual[coluna]:
                campos[coluna] = alvo[coluna]
        with self.repo.db.tx():
            self.repo.update_profile(profile_id, campos)
            self.repo.delete_persona(str(alvo["id"]))
        self.bus.emit("log", "Persona absorvida pelo perfil", data={"profile_id": profile_id,
                                                                   "persona_id": str(alvo["id"])})

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
                          limit: int = 30, app_id: str | None = None) -> list[InteractionDTO]:
        self.get_profile(profile_id)
        return [interaction_dto(r) for r in self.repo.list_interactions(
            profile_id, counterparty=_counterparty(counterparty) if counterparty else None,
            thread_key=thread_key, limit=limit, app_id=app_id)]

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
                    instance_id: str | None = None, draft_meta: dict[str, Any] | None = None,
                    app_id: str | None = None, counterparty: str | None = None) -> str:
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

        `counterparty` é o alvo que a AÇÃO declara no catálogo (`Capability.counterparty`, ADR-055) — em curtir e
        comentar, o autor da publicação. Sem ele, o palpite antigo (`username`, depois `target`): foi esse palpite que
        deixou `counterparty` NULL em todo `post_liked`/`comment_replied` do central.
        """
        alvo = counterparty or bindings.get("username") or bindings.get("target")
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
            incoming_content=(draft_meta or {}).get("incoming") or None, metadata=meta, app_id=app_id).id

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
    def list_memories(self, profile_id: str, *, subject: str | None = None, limit: int = 100,
                      app_id: str | None = None) -> list[MemoryItemDTO]:
        self.get_profile(profile_id)
        return self.memory.list(profile_id, subject=subject, limit=limit, app_id=app_id)

    def remember_screen(self, profile_id: str, *, step_title: str, bindings: dict[str, Any] | None, elements: Any,
                        items: Sequence[str] | None = None, app_label: str = "app",
                        app_id: str | None = None) -> MemoryItemDTO | None:
        """A tela em que uma etapa foi comprovada vira memória de origem `observation` (ver `observacao.py`).

        Quem chama já descartou tela sensível e aparelho-loja; aqui só se extrai o conteúdo e se grava. Validade de
        30 dias: o que se vê numa tela muda (contadores, "ativo agora"), e o fato velho não pode concorrer para sempre
        com o que a pessoa disse. Recusa por segredo é silenciosa: tela com cara de código simplesmente não vira fato.
        """
        from ..util import iso_in, now_iso  # noqa: PLC0415
        from .observacao import assunto_da_tela, fato_observado, linhas_de_conteudo, prefixo_do_dia  # noqa: PLC0415
        elementos = list(elements or [])
        agora = now_iso()
        texto = fato_observado(titulo_da_etapa=step_title, quando=agora, linhas=linhas_de_conteudo(elementos),
                               itens=items)
        if texto is None:
            return None
        try:
            item = self.memory.absorb_observation(profile_id, subject=assunto_da_tela(bindings, elementos, app_label),
                                                  content=texto, prefix=prefixo_do_dia(agora),
                                                  expires_at=iso_in(30 * 86400), app_id=app_id)
        except MemoryRefused as exc:
            log.info("tela não virou memória (%s): %s", profile_id, exc)
            return None
        self.bus.emit("log", "Memória: tela observada registrada", data={"profile_id": profile_id, "memory_id": item.id})
        return item

    def add_memory(self, profile_id: str, body: Any) -> MemoryItemDTO:
        self.get_profile(profile_id)
        try:
            if body.app_id:
                self._check_app(body.app_id)
            return self.memory.remember(profile_id, subject=body.subject, content=body.content, source="operator",
                                        importance=body.importance, confidence=body.confidence,
                                        expires_at=body.expires_at, app_id=body.app_id)
        except MemoryRefused as exc:
            raise SocialError("memory_refused", str(exc), 400) from None

    def delete_memory(self, profile_id: str, memory_id: str) -> None:
        self.get_profile(profile_id)
        if not self.memory.forget(profile_id, memory_id):
            raise SocialError("not_found", "Lembrança não encontrada.", 404)

    # ------------------------------------------------------------------ política e limites do perfil
    def _pacote_do_perfil(self) -> str | None:
        """O pacote do app a que um perfil pertence (o app âncora), perguntado ao registro em vez de escrito."""
        return pacote_ancora()

    def _rotulo_ancora(self) -> str:
        """Como chamar o app âncora numa mensagem (`AppDefinition.label`), sem o nome dele escrito aqui."""
        pacote = pacote_ancora()
        return capabilities_of(pacote).label if pacote else "aplicativo"

    def get_policy(self, profile_id: str, *, package: str | None = None) -> ProfilePolicyDTO:
        """O que vale hoje para este perfil, ao lado do que o catálogo propõe — para a diferença ficar visível.

        `package` omitido resolve pelo REGISTRO de aplicativos (o app que provê a conta deste perfil), e não
        por um literal `com.instagram.android` na assinatura: assim um segundo app com conta gerenciada
        entra sem editar esta função. Com um app EXPLÍCITO (23.10: o painel deixa de assumir "o primeiro app
        com login gerenciado da lista"), o catálogo é o dele — a pessoa escolhe qual app está configurando.
        """
        perfil = self.get_profile(profile_id)
        pacote = package or self._pacote_do_perfil()
        catalogo = load_catalog(pacote)
        acoes = catalogo.offered if catalogo else []
        engine = self.policies
        efetivas = {c.key: engine.policy_for(profile_id, c, pacote) for c in acoes}
        proprio, do_grupo = engine._own(profile_id), engine._group(profile_id)
        return ProfilePolicyDTO(
            package=pacote,
            limits=engine.limits_for(profile_id),
            capabilities=efetivas,
            defaults={c.key: c.default_policy for c in acoes},
            loosened=[c.key for c in acoes if engine.is_loosened(c, efetivas[c.key])],
            group_id=perfil.policy_group_id, group_name=perfil.policy_group_name,
            # Só o recorte DESTE app (23.10): a mesma chave em outro catálogo é outra escolha.
            own=politicas_do_app(proprio.get("capabilities"), pacote),
            group=politicas_do_app(do_grupo.get("capabilities"), pacote),
            origin={c.key: engine.origin_for(profile_id, c, pacote) for c in acoes},  # type: ignore[misc]
            own_limits=dict(proprio.get("limits") or {}), group_limits=dict(do_grupo.get("limits") or {}),
            limits_origin=engine.limits_origin(profile_id))  # type: ignore[arg-type]

    def set_policy(self, profile_id: str, body: Any, *, package: str | None = None) -> ProfilePolicyDTO:
        """Só aceita o que existe: nome de ação fora do catálogo ou limite desconhecido é erro, não silêncio."""
        self.get_profile(profile_id)
        package = package or self._pacote_do_perfil()
        atual = loads(self.repo.profile_row(profile_id)["automation_policy"], {}) or {}
        atual = self._aplicar_politica(atual, body.capabilities, body.limits, package=package,
                                       quem=f"perfil {profile_id}", data={"profile_id": profile_id})
        self.repo.update_profile(profile_id, {"automation_policy": dumps(atual)})
        self.bus.emit("log", "Política de automação atualizada", data={"profile_id": profile_id})
        return self.get_policy(profile_id, package=package)

    def _aplicar_politica(self, atual: dict[str, Any], capabilities: dict[str, Any] | None,
                          limits: dict[str, Any] | None, *, package: str | None, quem: str,
                          data: dict[str, Any]) -> dict[str, Any]:
        """Mesma regra para o perfil e para o grupo: só aceita o que existe; `None` APAGA a chave (herdar).

        Nome de ação fora do catálogo ou limite desconhecido é erro, não silêncio. Afrouxar abaixo do padrão uma
        ação de risco ALTO continua aceito, mas nunca calado (achado #114 — FOLLOW e SEND_MESSAGE autônomos na
        frota inteira foram a evidência): o aviso sai tanto do perfil quanto do grupo, que afrouxa para vários.
        """
        catalogo = load_catalog(package)
        atual = dict(atual)
        if capabilities is not None:
            desconhecidas = [k for k in capabilities if not (catalogo and catalogo.has(k))]
            if desconhecidas:
                raise SocialError("unknown_capability", f"Ação desconhecida: {', '.join(desconhecidas)}.", 400)
            # Só o recorte do app pedido é lido e regravado (23.10); o dos outros apps passa intacto.
            caps = politicas_do_app(atual.get("capabilities"), package)
            for chave, nova in capabilities.items():
                if nova is None:
                    caps.pop(chave, None)
                    continue
                cap = catalogo.get(chave) if catalogo else None
                if cap and cap.risk == "high" and self.policies.is_loosened(cap, nova):
                    self.bus.emit("log", f"{quem}: {chave} afrouxado para '{nova}' — abaixo do padrão "
                                         f"'{cap.default_policy}' do catálogo, em ação de risco alto",
                                  level="warn", data={**data, "capability": chave, "policy": nova})
                caps[chave] = nova
            atual["capabilities"] = com_politicas_do_app(atual.get("capabilities"), package, caps)
        if limits is not None:
            invalidos = [k for k in limits if k not in DEFAULT_LIMITS]
            if invalidos:
                raise SocialError("unknown_limit", f"Limite desconhecido: {', '.join(invalidos)}.", 400)
            negativos = [k for k, v in limits.items() if v is not None and v < 0]
            if negativos:
                raise SocialError("invalid_limit", f"Limite não pode ser negativo: {', '.join(negativos)}.", 400)
            lims = dict(atual.get("limits") or {})
            for chave, valor in limits.items():
                if valor is None:
                    lims.pop(chave, None)
                else:
                    lims[chave] = valor
            atual["limits"] = lims
        return atual

    # ------------------------------------------------------------------ contas por app (item 12.1)
    def _app_row(self, app_id: str) -> Any:
        return self.repo.db.one("SELECT id, name, package FROM apps WHERE id=?", (app_id,))

    def _check_app(self, app_id: str) -> Any:
        row = self._app_row(app_id)
        if row is None:
            raise SocialError("unknown_app", f"Aplicativo '{app_id}' não está cadastrado.", 400)
        return row

    def _app_do_pacote(self, package: str | None) -> str | None:
        if not package:
            return None
        return self.repo.db.scalar("SELECT id FROM apps WHERE package=?", (package,))

    def _account_dto(self, profile_id: str, row: Any, instance_id: str | None = None) -> ProfileAccountDTO:
        """Uma fonte só para toda conta (ADR-040): credencial em `account_credentials`, sessão em `account_sessions`
        no aparelho vinculado. `profile_accounts.session_status` (037) não é mais lida: era cópia congelada.

        `instance_id`: a conta vista NAQUELE aparelho (N:N, 051) — sessão e ações de lá, que é o que as rotas de
        sessão com `?instance_id=` recusam ou aceitam. Sem ele, o aparelho principal."""
        app = self._app_row(row["app_id"])
        package = app["package"] if app else None
        automatico = bool(package) and session_provider_of(package) is not None
        cred = self.repo.account_credential_row(profile_id, row["id"])
        if instance_id is not None:
            sessao = self.repo.account_session_row(profile_id, row["id"], instance_id)
            aparelho: str | None = instance_id
        else:
            sessao = self.repo.session_of_account(profile_id, row["id"])
            vinculo = self.repo.binding_principal(profile_id)
            aparelho = vinculo["instance_id"] if vinculo else None
        acoes = None
        if package:
            _app, acoes = self.repo.app_e_acoes_do_pacote(aparelho, package,
                                                          (app["name"] if app else None) or package, cred, sessao)
        return ProfileAccountDTO(
            id=row["id"], profile_id=profile_id, app_id=row["app_id"], app_name=app["name"] if app else None,
            package=package, handle=row["handle"] or "", host=row["host"],
            login_identifier=cred["login_identifier"] if cred else None, status=row["status"],
            session_status=sessao["status"] if sessao else SessionStatus.unknown.value,
            session_detail=sessao["detail"] if sessao else None,
            session_verified_at=sessao["verified_at"] if sessao else None,
            session=SessionInfo(
                status=SessionStatus(sessao["status"]) if sessao else SessionStatus.unknown,
                instance_id=sessao["instance_id"] if sessao else None,
                observed_username=sessao["observed_username"] if sessao else None,
                verified_at=sessao["verified_at"] if sessao else None, detail=sessao["detail"] if sessao else None,
                stale=sessao_vencida(sessao, self.repo.session_max_age_s)),
            session_actions=acoes, automated_login=automatico, credential_configured=cred is not None,
            credential=CredentialInfo(
                configured=cred is not None, login_identifier=cred["login_identifier"] if cred else None,
                status=cred["status"] if cred else None, failed_attempts=cred["failed_attempts"] if cred else 0,
                blocked_until=cred["blocked_until"] if cred else None, updated_at=cred["updated_at"] if cred else None,
                last_used_at=cred["last_used_at"] if cred else None, consent_at=cred["consent_at"] if cred else None,
                consent_by=cred["consent_by"] if cred else None),
            consent_at=cred["consent_at"] if cred else None, notes=row["notes"] or "", created_at=row["created_at"],
            updated_at=row["updated_at"])

    def list_accounts(self, profile_id: str) -> list[ProfileAccountDTO]:
        self.get_profile(profile_id)
        return [self._account_dto(profile_id, r) for r in self.repo.list_accounts(profile_id)]

    def get_account(self, profile_id: str, account_id: str, instance_id: str | None = None) -> ProfileAccountDTO:
        row = self.repo.account_row(profile_id, account_id)
        if row is None:
            raise SocialError("not_found", "Conta não encontrada neste perfil.", 404)
        return self._account_dto(profile_id, row, instance_id)

    def add_account(self, profile_id: str, body: Any, *, by: str = "painel") -> ProfileAccountDTO:
        self.get_profile(profile_id)
        app = self._check_app(body.app_id)
        host = _host_da_conta(getattr(body, "host", None))
        if self.repo.account_by_app(profile_id, app["id"], host):
            onde = f"{app['name']} ({host})" if host else app["name"]
            raise SocialError("duplicate_account", f"Este perfil já tem uma conta em {onde}.", 409)
        # D2-a antes de criar qualquer linha: o vínculo sem app da pessoa passa a servir este app com a conta nova.
        self._recusar_conta_que_quebra_d2a(profile_id, str(app["id"]))
        clonar_de = getattr(body, "clonar_de", None)
        if clonar_de:
            # Tudo conferido ANTES de criar a conta, pela mesma razão do consentimento abaixo. A recusa é do serviço
            # (e não um validador do corpo) porque o 422 do validador devolveria o corpo inteiro — com a senha.
            if body.password:
                raise SocialError("clonar_de_com_senha",
                                  "Mande a senha OU a conta de onde clonar a senha, não as duas.", 422)
            if getattr(body, "consent", False):
                raise SocialError("consentimento_nao_clonado", self._MSG_CONSENTIMENTO_NAO_CLONADO, 422)
            self._origem_do_clone(profile_id, clonar_de, destino_id=None)
            if self.secrets.status() != "ready":
                raise SocialError("secret_store_unavailable", self._vault_message(), 503)
        if body.password and self.secrets.status() != "ready":
            raise SocialError("secret_store_unavailable", self._vault_message(), 503)
        if body.password and not getattr(body, "consent", False):
            # Antes de criar a conta: recusar depois deixaria uma conta sem a senha que a pessoa mandou.
            onde = f"{app['name']} ({host})" if host else app["name"]
            raise SocialError("consentimento_de_credencial",
                              f"Guardar esta senha autoriza a automação a DIGITÁ-LA na tela de {onde} — e só nela "
                              "— pelo canal sensível, sem que a IA veja o valor. Confirme com consent=true.", 409)
        account_id = self.repo.create_account(profile_id, app_id=app["id"], handle=body.handle.strip(),
                                              notes=body.notes.strip(), host=host)
        if body.password:
            conta = self.repo.account_row(profile_id, account_id)
            assert conta is not None
            self._gravar_credencial(profile_id, conta, password=body.password, login_identifier=body.login_identifier,
                                    consent=bool(body.consent), by=by)
        elif clonar_de:
            conta = self.repo.account_row(profile_id, account_id)
            assert conta is not None
            try:
                self._clonar_credencial(profile_id, conta, clonar_de, login_identifier=body.login_identifier, by=by)
            except SocialError:
                # Cofre trancado entre a conferência e a cópia: a conta não fica sem a senha que a pessoa pediu.
                self.repo.delete_account(profile_id, account_id)
                raise
        self.bus.emit("log", f"Conta em {app['name']} adicionada ao perfil", data={"profile_id": profile_id})
        return self.get_account(profile_id, account_id)

    def update_account(self, profile_id: str, account_id: str, body: Any) -> ProfileAccountDTO:
        conta = self.get_account(profile_id, account_id)
        campos = body.model_dump(exclude_unset=True, exclude_none=True)
        marca = campos.pop("session_status", None)
        if marca is not None:
            if conta.automated_login:
                # Em app com provedor a sessão é do provedor (conectar/verificar), não uma marcação à mão.
                raise SocialError("session_managed", f"A sessão de {conta.app_name or conta.app_id} é verificada "
                                                     "pelo sistema: use Conectar ou Verificar conta.", 409)
            # Sessão é do par (conta, aparelho): a marcação da pessoa vale para o aparelho vinculado ao perfil.
            aparelho = self.instance_of(profile_id)
            if aparelho is None:
                raise SocialError("no_binding", "Vincule um aparelho ao perfil antes de marcar a sessão: a sessão "
                                                "é da conta NAQUELE aparelho.", 409)
            status = SessionStatus(marca)
            self.repo.set_account_session(
                profile_id, account_id, aparelho, status=status,
                verified_at=now_iso() if status is SessionStatus.session_ready else None,
                detail=("Marcada pelo operador depois de entrar pelo Foco." if status is SessionStatus.session_ready
                        else "Marcada pelo operador."))
        if "host" in campos:
            campos["host"] = _host_da_conta(campos["host"])
            outra = self.repo.account_by_app(profile_id, conta.app_id, campos["host"])
            if outra is not None and outra["id"] != account_id:
                raise SocialError("duplicate_account", "Este perfil já tem uma conta deste app nesse site.", 409)
        self.repo.update_account(profile_id, account_id, campos)
        self.bus.emit("log", "Conta do perfil atualizada", data={"profile_id": profile_id})
        return self.get_account(profile_id, account_id)

    def delete_account(self, profile_id: str, account_id: str) -> None:
        self.get_account(profile_id, account_id)
        if self.repo.eh_pacote_ancora(profile_id, self.repo.pacote_da_conta(profile_id, account_id)):
            # O perfil ainda é ancorado no @ do app âncora (tabela e sessão do provedor): tirar a conta daria um
            # perfil sem a sessão que o resto do sistema lê. Só a do app âncora: login automático não é âncora — a
            # conta do Outlook (login gerenciado desde o 23.8, sem ser âncora) sai como qualquer outra.
            raise SocialError("anchor_account", f"A conta do {self._rotulo_ancora()} é a âncora deste perfil e não pode"
                                                " ser removida.", 409)
        self._apagar_credencial(profile_id, self.repo.delete_account_credential(profile_id, account_id))
        self.repo.delete_account(profile_id, account_id)
        self.bus.emit("log", "Conta removida do perfil", data={"profile_id": profile_id})

    # ------------------------------------------------------------------ conta bloqueada sai (29.23, ADR-068)
    def retirar_conta_bloqueada(self, profile_id: str, account_id: str, *, origem: str = "declarado",
                                autor: str = "painel", evidencia: str | None = None) -> dict[str, object]:
        """Bloqueio confirmado numa conta: ela sai da plataforma NA HORA, como se não existisse; a PERSONA fica.

        Numa transação só (nada pela metade): as limpezas registradas por outros módulos, a credencial da conta E a
        legada E o ciphertext no cofre (o mesmo banco: `SecretStore(db)`), as sessões, o vínculo de aparelho da conta
        e a linha da conta, mesmo sendo a âncora. Na âncora o `username` esvazia e o status da persona volta a
        `active` por `mudar_status` (só se estava `blocked`: a pausa do dono, `disabled`, é dele). O aparelho NÃO é
        tocado: o app segue com a conta logada até uma pessoa decidir (o marcador de quarentena da 054 fica).

        Idempotente: a conta que já não existe devolve `retirada: False`, sem erro. Persona inexistente é 404. O id da
        conta é a LÁPIDE: continua nos eventos e execuções antigos, que seguem legíveis.
        """
        self.get_profile(profile_id)
        conta = self.repo.account_row(profile_id, account_id)
        if conta is None:
            return {"profile_id": profile_id, "account_id": account_id, "retirada": False, "ancora": False,
                    "limpezas": {}, "status_da_persona": self._status_do(profile_id),
                    "detail": "A conta já não existe nesta persona: nada a retirar."}
        app_id = str(conta["app_id"])
        handle = str(conta["handle"] or "")
        ancora = self.repo.eh_pacote_ancora(profile_id, self.repo.pacote_da_conta(profile_id, account_id))
        estava_bloqueada = ancora and self._status_do(profile_id) == "blocked"
        # Os e-mails que identificam SÓ esta conta, lidos antes de a linha e a credencial saírem: o que outra conta viva
        # (o Outlook da mesma persona, por exemplo) ainda usa continua verdadeiro no produto e não entra (29.32).
        emails = emails_so_desta_conta(self.repo.db, profile_id=profile_id, account_id=account_id, handle=handle,
                                       ancora=ancora)
        # O evento da retirada não carrega o @, o id nem o e-mail em texto: a evidência passa pelo mesmo corte da memória.
        texto = sem_o_rastro((evidencia or "").strip()[:500], handle, account_id, emails) or "bloqueio confirmado"
        contagens: dict[str, int] = {}
        # Onde a conta estava logada, ANTES de a retirada mascarar o @ do marcador e apagar sessões e vínculos (29.27).
        pedido = self._pedido_de_limpeza(profile_id, conta, ancora)
        # Sessões que estavam na fila "Aguardando intervenção": a conta sai, e o item sai da fila junto.
        na_fila = [(str(s["instance_id"]), str(s["status"])) for s in self.repo.db.query(
            "SELECT instance_id, status FROM account_sessions WHERE account_id=?", (account_id,))
            if str(s["status"]) in PRECISA_DE_PESSOA]
        with self.repo.db.tx():
            for limpeza in list(self.limpezas_ao_retirar):
                for nome, n in (limpeza(self.repo.db, profile_id=profile_id, account_id=account_id, handle=handle,
                                        app_id=app_id) or {}).items():
                    contagens[nome] = contagens.get(nome, 0) + int(n)
            # A memória FICA (a linha não se apaga), de TODAS as personas, sem o @ da conta, o id dela nem o e-mail que
            # só ela usava (29.32).
            # O @ que outra conta viva ainda tem (o mesmo @ em outro app, ou o de outra persona) não é rastro.
            vivo = handle_vivo(self.repo.db, profile_id=profile_id, account_id=account_id, handle=handle,
                               ancora=ancora)
            contagens.update(reescrever_memoria(self.repo.db, profile_id=profile_id,
                                                handle=None if vivo else handle, account_id=account_id,
                                                emails=emails))
            refs = self.repo.retirar_conta_bloqueada(profile_id, account_id, ancora=ancora,
                                                     motivo="conta retirada por bloqueio")
            for ref in dict.fromkeys(refs):
                # Com as DUAS linhas (conta e legada) já fora, nada mais segura o ciphertext: o `_apagar_credencial`
                # só poupa o segredo que outra conta ou linha ainda referencia.
                self._apagar_credencial(profile_id, ref)
            if estava_bloqueada:
                self.repo.mudar_status(profile_id, "active", origem=origem, autor=autor,
                                       evidencia=f"conta {account_id} retirada por bloqueio: a persona segue")
        self.bus.emit("profile.account_retired",
                      f"Conta retirada por bloqueio confirmado ({origem}, por {autor}); a persona segue.",
                      level="warn",
                      data={"profile_id": profile_id, "account_id": account_id, "app_id": app_id, "ancora": ancora,
                            "origem": origem, "autor": autor, "evidencia": redact(texto),
                            "limpezas": contagens, "status_da_persona": self._status_do(profile_id)})
        for iid, anterior in na_fila:
            emit_needs_person_change(self.bus, profile_id=profile_id, instance_id=iid,
                                     status=SessionStatus.unknown.value, anterior_status=anterior,
                                     detail="conta retirada por bloqueio", account_id=account_id)
        limpeza = self._agendar_limpeza(pedido)
        if self.ao_retirar_conta is not None:
            try:
                self.ao_retirar_conta(profile_id, account_id, estava_bloqueada)
            except Exception as exc:  # noqa: BLE001 - a conta já saiu; o disjuntor falhar não desfaz isso
                self.bus.emit("log", f"Conta {account_id} retirada, mas o disjuntor de conta falhou "
                                     f"({type(exc).__name__}): confira as execuções da persona.", level="error",
                              data={"profile_id": profile_id, "account_id": account_id})
        return {"profile_id": profile_id, "account_id": account_id, "retirada": True, "ancora": ancora,
                "limpezas": contagens, "status_da_persona": self._status_do(profile_id),
                "limpeza_dos_aparelhos": limpeza, "detail": "Conta retirada; a persona continua."}

    def _pedido_de_limpeza(self, profile_id: str, conta: Row, ancora: bool) -> PedidoDeLimpeza | None:
        """O que pedir aos aparelhos depois da retirada (29.27), ou `None`: o pacote da conta precisa DECLARAR
        `limpar_ao_retirar` no `app.yaml` (o núcleo não conhece app nenhum) e haver aparelho onde ela estava logada.
        Lido ANTES da transação da retirada, que apaga a pista. Nunca levanta: sem o pedido a retirada segue, e a
        limpeza fica para a rota manual."""
        try:
            pacote = self.repo.pacote_da_conta(profile_id, str(conta["id"]))
            if not pacote or not capabilities_of(pacote).clear_on_account_retire:
                return None
            perfil = self.repo.profile_row(profile_id)
            handles = [conta["handle"], perfil["username"] if perfil is not None and ancora else None]
            aparelhos = self.repo.aparelhos_da_conta(profile_id, conta, handles)
        except Exception as exc:  # noqa: BLE001 - ver docstring
            self.bus.emit("log", f"Retirada da conta {conta['id']}: não deu para saber onde ela estava logada "
                                 f"({type(exc).__name__}); a limpeza dos aparelhos fica para a rota manual.",
                          level="error", data={"profile_id": profile_id, "account_id": str(conta["id"]),
                                               "erro": type(exc).__name__})
            return None
        if not aparelhos:
            return None
        return PedidoDeLimpeza(profile_id=profile_id, account_id=str(conta["id"]), app_id=str(conta["app_id"]),
                               package=pacote, aparelhos=tuple(aparelhos))

    def _agendar_limpeza(self, pedido: PedidoDeLimpeza | None) -> dict[str, object]:
        """Entrega o pedido à tarefa de fundo (uma tentativa por retirada). Quem agenda falhar não desfaz a retirada,
        que já saiu: vira evento de erro e a quarentena segue aberta para uma pessoa."""
        if pedido is None:
            return {"agendada": False, "aparelhos": 0}
        if self.ao_limpar_aparelhos is None:
            return {"agendada": False, "aparelhos": len(pedido.aparelhos), "motivo": "sem executor ligado"}
        try:
            self.ao_limpar_aparelhos(pedido)
        except Exception as exc:  # noqa: BLE001 - a conta já saiu; só a limpeza não foi pedida
            self.bus.emit("device.account_cleanup", f"Conta {pedido.account_id} retirada, mas a limpeza dos aparelhos "
                          f"não foi agendada ({type(exc).__name__}): a quarentena segue aberta; resolva pela rota do "
                          "aparelho depois de limpar o app.", level="error",
                          data={"profile_id": pedido.profile_id, "account_id": pedido.account_id,
                                "package": pedido.package, "resultado": "nao_agendada",
                                "aparelhos": [a.instance_id for a in pedido.aparelhos], "erro": type(exc).__name__})
            return {"agendada": False, "aparelhos": len(pedido.aparelhos), "motivo": type(exc).__name__}
        return {"agendada": True, "aparelhos": len(pedido.aparelhos)}

    def _status_do(self, profile_id: str) -> str:
        linha = self.repo.profile_row(profile_id)
        return str(linha["status"] or "active") if linha is not None else "active"

    def _retirar_por_bloqueio(self, profile_id: str, app_id: str | None, handle: str, instance_id: str,
                              evidencia: str | None, origem: str = "observado") -> None:
        """Gatilho do bloqueio CONFIRMADO (a tela de verificação lida, `marcar_conta_travada`): a conta sai.

        Só no INSTAGRAM (conta âncora, pacote `com.instagram.android`) e só com SINAL FORTE: a atividade de desafio
        (`ChallengeActivity`) em foco, ou a declaração do dono. Texto na tela sozinho, ou conta de outro app (Outlook...),
        fica como sempre: persona `blocked`/conta marcada e a pessoa decide; retirar ali é só pela rota manual.

        Nunca levanta e roda sob savepoint: falhar aqui (uma limpeza que erra) desfaz só a retirada, e a persona
        fica `blocked`, o estado seguro, com o erro visível no histórico; a rota manual refaz depois. A transação de
        fora (o salvamento da sessão, o vínculo) segue. Sem conta achada, não há o que retirar."""
        conta = self._conta_do_bloqueio(profile_id, app_id, handle)
        if conta is None:
            return
        pacote = self.repo.pacote_da_conta(profile_id, str(conta["id"]))
        # Só sai sozinha a conta âncora de um app que DECLARA a janela de conta perdida (`app.yaml`,
        # `atividades_de_conta_perdida`; hoje só o Instagram): nos demais, a conta travada é da pessoa (ADR-068).
        if not self.repo.eh_pacote_ancora(profile_id, pacote) or not capabilities_of(pacote).lost_account_activities:
            return
        forte = origem == "declarado" or bool(self.sinal_de_desafio is not None and self.sinal_de_desafio(instance_id))
        if not forte:
            self.bus.emit("log", f"{instance_id}: conta travada vista só pelo texto da tela, sem a atividade de desafio em "
                                 "foco: a conta NÃO foi retirada (fica bloqueada para uma pessoa conferir).",
                          level="info", instance_id=instance_id,
                          data={"profile_id": profile_id, "account_id": str(conta["id"]), "sinal_forte": False})
            return
        chave = (profile_id, str(conta["id"]))
        if chave in self._retirando:
            return
        self._retirando.add(chave)
        try:
            with self.repo.db.savepoint():
                self.retirar_conta_bloqueada(profile_id, chave[1], origem=origem, autor="sistema",
                                             evidencia=f"{instance_id}: {evidencia}" if evidencia
                                             else f"conta travada em {instance_id}")
        except Exception as exc:  # noqa: BLE001 - ver docstring
            self.bus.emit("log", f"{instance_id}: bloqueio confirmado na conta {chave[1]}, mas a retirada falhou "
                                 f"({type(exc).__name__}): a conta fica como estava; refaça pela rota de retirada da conta "
                                 "(retire).", level="error",
                          instance_id=instance_id, data={"profile_id": profile_id, "account_id": chave[1],
                                                         "erro": type(exc).__name__})
        finally:
            self._retirando.discard(chave)

    def _conta_do_bloqueio(self, profile_id: str, app_id: str | None, handle: str) -> Row | None:
        """A conta da persona a que o bloqueio se refere: a do app dito; sem app, a que tem aquele @; sem nenhuma, a
        âncora quando o @ é o do cadastro."""
        if app_id:
            if (conta := self.repo.account_by_app(profile_id, app_id)) is not None:
                return conta
            pacote = self.repo.db.scalar("SELECT package FROM apps WHERE id=?", (app_id,))
            if pacote and (conta := self.repo.conta_do_pacote(profile_id, str(pacote))) is not None:
                return conta
        alvo = handle.lower()
        for conta in self.repo.list_accounts(profile_id):
            if str(conta["handle"] or "").lstrip("@").lower() == alvo:
                return conta
        perfil = self.repo.profile_row(profile_id)
        if perfil is not None and str(perfil["username"] or "").lower() == alvo:
            return self.repo.conta_ancora(profile_id)
        return None

    def set_account_credential(self, profile_id: str, account_id: str, body: Any, *,
                               by: str = "painel") -> ProfileAccountDTO:
        """Um caminho só, com ou sem provedor: a senha é da conta, e o consentimento também (ADR-040)."""
        self.get_account(profile_id, account_id)
        conta = self.repo.account_row(profile_id, account_id)
        assert conta is not None
        self._gravar_credencial(profile_id, conta, password=body.password, login_identifier=body.login_identifier,
                                consent=bool(body.consent), by=by)
        return self.get_account(profile_id, account_id)

    def delete_account_credential(self, profile_id: str, account_id: str) -> ProfileAccountDTO:
        self.get_account(profile_id, account_id)
        self._apagar_credencial(profile_id, self.repo.delete_account_credential(profile_id, account_id))
        return self.get_account(profile_id, account_id)

    def consent_account_credential(self, profile_id: str, account_id: str, *, by: str = "painel") -> ProfileAccountDTO:
        """Marca o consentimento numa senha já guardada (sem redigitá-la)."""
        self.get_account(profile_id, account_id)
        if not self.repo.consent_account_credential(profile_id, account_id, consent_by=by):
            raise SocialError("no_credential", "Esta conta não tem senha guardada para consentir.", 409)
        self.bus.emit("log", "Consentimento de credencial registrado",
                      data={"profile_id": profile_id, "account_id": account_id, "by": by})
        return self.get_account(profile_id, account_id)

    # ------------------------------------------------------------------ credencial clonada (ADR-057, D1)
    _MSG_CONSENTIMENTO_NAO_CLONADO = (
        "O consentimento não vem junto com a senha clonada: ele é desta conta, e a conta nova nasce sem ele. Depois "
        "de conferir a conta, autorize-a pela rota de consentimento (…/credential/consent).")

    def clone_account_credential(self, profile_id: str, account_id: str, body: CredentialClone, *,
                                 by: str = "painel") -> ProfileAccountDTO:
        """"Usar a senha de outra conta" numa conta que já existe. Substitui a senha que ela tinha; o consentimento
        DESTA conta, se já dado, fica (é sobre a conta, como em `set_account_credential`); o da origem nunca vem."""
        self.get_account(profile_id, account_id)
        conta = self.repo.account_row(profile_id, account_id)
        assert conta is not None
        self._origem_do_clone(profile_id, body.clonar_de, destino_id=account_id)
        if self.secrets.status() != "ready":
            raise SocialError("secret_store_unavailable", self._vault_message(), 503)
        self._clonar_credencial(profile_id, conta, body.clonar_de, login_identifier=body.login_identifier, by=by)
        return self.get_account(profile_id, account_id)

    def _origem_do_clone(self, profile_id: str, origem_id: str, *, destino_id: str | None) -> Row:
        """A conta de onde a senha vem: da MESMA persona, outra conta, com senha guardada.

        Outra persona é 409 e não 404 — a conta existe, e a recusa é a regra (a senha de uma pessoa nunca vira a de
        outra), não um erro de digitação. A mensagem não diz de quem ela é.
        """
        dona = self.repo.db.scalar("SELECT profile_id FROM profile_accounts WHERE id=?", (origem_id,))
        if dona is None:
            raise SocialError("not_found", "A conta de onde clonar a senha não existe.", 404)
        if dona != profile_id:
            raise SocialError("credencial_de_outra_persona", "Só dá para usar a senha de outra conta DESTA persona.",
                              409)
        if destino_id is not None and origem_id == destino_id:
            raise SocialError("clonar_de_si_mesma", "A conta de origem é esta mesma conta.", 409)
        origem = self.repo.account_credential_row(profile_id, origem_id)
        if origem is None:
            raise SocialError("no_credential", "A conta de origem não tem senha guardada para clonar.", 409)
        return origem

    def _clonar_credencial(self, profile_id: str, conta: Row, origem_id: str, *, login_identifier: str | None,
                           by: str) -> None:
        """O cofre copia; o domínio só vê referências. O identificador de login NÃO é copiado da origem: o endereço
        da conta Outlook é o dado que o dono confirmou (ADR-057 §5), nunca derivado do login do Instagram — vale o
        informado, o já gravado nesta conta ou o padrão do app (`_login_da_conta`).

        `consent_by=None`: a conta sem consentimento continua sem (e o `type_secret` e o provedor não a usam até a
        pessoa autorizar); a que já tinha mantém o seu. A contagem de falhas zera: é uma senha nova para esta conta.
        """
        origem = self._origem_do_clone(profile_id, origem_id, destino_id=conta["id"])
        atual = self.repo.account_credential_row(profile_id, conta["id"])
        ref_atual = atual["secret_ref"] if atual else None
        # Referência compartilhada (hoje não acontece; seria resto de dado antigo): sobrescrever no lugar mudaria a
        # origem, ou uma terceira conta, também. Aí a cópia vai para uma entrada nova, e a antiga fica com quem a usa.
        if ref_atual is not None and (ref_atual == origem["secret_ref"] or self.repo.db.scalar(
                "SELECT COUNT(*) FROM account_credentials WHERE secret_ref=?", (ref_atual,)) > 1):
            ref_atual = None
        try:
            ref = self.secrets.clonar(origem["secret_ref"], para=ref_atual)
        except KeyError:
            raise SocialError("no_credential", "A senha da conta de origem não está mais no cofre; guarde-a de "
                                               "novo antes de clonar.", 409) from None
        except (SecretStoreLocked, SecretStoreUnavailable) as exc:
            raise SocialError("secret_store_unavailable", str(exc), 503) from None
        login = self._login_da_conta(profile_id, conta, login_identifier)
        self.repo.set_account_credential(profile_id, conta["id"], login_identifier=login, secret_ref=ref,
                                         key_id=self.secrets.provider.key_id, consent_by=None)
        # Trilha: só ids. Nem o valor nem o identificador de login da origem entram no evento. Nenhuma chave aqui tem
        # "credencial" ou "senha" no nome: a redação mascara esses nomes, e a trilha perderia de onde a senha veio.
        self.bus.emit("log", "Senha de conta clonada de outra conta da persona",
                      data={"profile_id": profile_id, "account_id": conta["id"], "origem_account_id": origem_id,
                            "by": by})

    # ------------------------------------------------------------------ grupos de acesso (migração 036)
    def _group_dto(self, row: Any, *, package: str | None = None) -> PolicyGroupDTO:
        """O grupo visto por UM app (23.10): `capabilities` e `loosened` são o recorte do catálogo pedido
        (`politicas_do_app`; sem `package`, o âncora). A mesma chave de ação pode existir em dois catálogos, e a
        escolha do grupo para um app não vale para o outro. `limits` são do perfil inteiro, não de um app."""
        pacote = package or self._pacote_do_perfil()
        caps = politicas_do_app(loads(row["capabilities"], {}) or {}, pacote)
        catalogo = load_catalog(pacote)
        afrouxadas = [k for k, v in caps.items()
                      if catalogo and catalogo.has(k) and catalogo.get(k).risk == "high"
                      and self.policies.is_loosened(catalogo.get(k), v)]
        return PolicyGroupDTO(
            id=row["id"], name=row["name"], description=row["description"] or "", package=pacote,
            capabilities=caps, limits=loads(row["limits"], {}) or {}, loosened=afrouxadas,
            members=[PolicyGroupMember(id=m["id"], username=m["username"],
                                       name=nome_exibido(m["display_name"], m["first_name"], m["last_name"],
                                                         m["username"]) or None)
                     for m in self.repo.policy_group_members(row["id"])],
            created_at=row["created_at"], updated_at=row["updated_at"])

    def list_policy_groups(self, *, package: str | None = None) -> list[PolicyGroupDTO]:
        return [self._group_dto(r, package=package) for r in self.repo.list_policy_groups()]

    def get_policy_group(self, group_id: str, *, package: str | None = None) -> PolicyGroupDTO:
        row = self.repo.policy_group_row(group_id)
        if row is None:
            raise SocialError("not_found", "Grupo de acesso não encontrado.", 404)
        return self._group_dto(row, package=package)

    def _check_members(self, profile_ids: list[str]) -> list[str]:
        unicos = list(dict.fromkeys(profile_ids))
        faltando = [pid for pid in unicos if self.repo.profile_row(pid) is None]
        if faltando:
            raise SocialError("unknown_profile", f"Perfil inexistente: {', '.join(faltando)}.", 400)
        return unicos

    def create_policy_group(self, body: Any, *, package: str | None = None) -> PolicyGroupDTO:
        nome = body.name.strip()
        if not nome:
            raise SocialError("invalid_name", "Dê um nome ao grupo.", 400)
        if self.repo.policy_group_by_name(nome):
            raise SocialError("duplicate_name", f"Já existe um grupo chamado {nome}.", 409)
        pacote = package or self._pacote_do_perfil()
        base: dict[str, Any] = {}
        if body.from_profile_id:
            # Começa com o que o perfil tem HOJE de diferente do padrão: o grupo dele por baixo, as escolhas dele
            # por cima — é a "política efetiva menos o padrão", sem copiar o que já é padrão.
            self.get_profile(body.from_profile_id)
            do_grupo, proprio = self.policies._group(body.from_profile_id), self.policies._own(body.from_profile_id)
            # Só o recorte do app pedido (23.10): o grupo novo nasce com as escolhas do perfil NESTE app.
            efetivo = {**politicas_do_app(do_grupo.get("capabilities"), pacote),
                       **politicas_do_app(proprio.get("capabilities"), pacote)}
            base = {"capabilities": com_politicas_do_app({}, pacote, efetivo),
                    "limits": {**(do_grupo.get("limits") or {}), **(proprio.get("limits") or {})}}
        membros = self._check_members(body.profile_ids)
        # `capabilities` do corpo são do app PEDIDO (23.10): `_aplicar_politica` grava no recorte dele.
        config = self._aplicar_politica(base, body.capabilities, body.limits, package=pacote,
                                        quem=f"grupo {nome}", data={"group": nome})
        group_id = self.repo.create_policy_group(name=nome, description=body.description.strip(),
                                                 capabilities=dumps(config.get("capabilities") or {}),
                                                 limits=dumps(config.get("limits") or {}))
        if membros:
            self.repo.set_policy_group_members(group_id, membros)
        self.bus.emit("log", f"Grupo de acesso {nome} criado" + (f" com {len(membros)} perfil(is)" if membros else ""),
                      data={"group_id": group_id})
        return self.get_policy_group(group_id, package=pacote)

    def update_policy_group(self, group_id: str, body: Any, *, package: str | None = None) -> PolicyGroupDTO:
        row = self.repo.policy_group_row(group_id)
        if row is None:
            raise SocialError("not_found", "Grupo de acesso não encontrado.", 404)
        pacote = package or self._pacote_do_perfil()
        campos: dict[str, Any] = {}
        if body.name is not None:
            nome = body.name.strip()
            outro = self.repo.policy_group_by_name(nome)
            if not nome:
                raise SocialError("invalid_name", "Dê um nome ao grupo.", 400)
            if outro is not None and outro["id"] != group_id:
                raise SocialError("duplicate_name", f"Já existe um grupo chamado {nome}.", 409)
            campos["name"] = nome
        if body.description is not None:
            campos["description"] = body.description.strip()
        if body.capabilities is not None or body.limits is not None:
            atual = {"capabilities": loads(row["capabilities"], {}) or {}, "limits": loads(row["limits"], {}) or {}}
            novo = self._aplicar_politica(atual, body.capabilities, body.limits, package=pacote,
                                          quem=f"grupo {campos.get('name', row['name'])}", data={"group_id": group_id})
            campos["capabilities"] = dumps(novo.get("capabilities") or {})
            campos["limits"] = dumps(novo.get("limits") or {})
        self.repo.update_policy_group(group_id, campos)
        if body.profile_ids is not None:
            self.repo.set_policy_group_members(group_id, self._check_members(body.profile_ids))
        self.bus.emit("log", "Grupo de acesso atualizado", data={"group_id": group_id})
        return self.get_policy_group(group_id, package=pacote)

    def delete_policy_group(self, group_id: str) -> None:
        dto = self.get_policy_group(group_id)
        self.repo.delete_policy_group(group_id)
        self.bus.emit("log", f"Grupo de acesso {dto.name} removido; {len(dto.members)} perfil(is) voltam a herdar "
                             "só do padrão", data={"group_id": group_id})

    def _check_group(self, group_id: str | None) -> None:
        if group_id and self.repo.policy_group_row(group_id) is None:
            raise SocialError("unknown_group", "Grupo de acesso inexistente.", 400)

    def auth_attempts(self, profile_id: str, limit: int = 20, *, account_id: str | None = None) -> list[dict[str, Any]]:
        """Tentativas do perfil; por conta quando `account_id` vem (a rota por perfil é apelido da conta âncora)."""
        self.get_profile(profile_id)
        if account_id is not None and self.repo.account_row(profile_id, account_id) is None:
            raise SocialError("not_found", "Conta não encontrada neste perfil.", 404)
        return [dict(r) for r in self.repo.auth_attempts(profile_id, limit, account_id=account_id)]

    # ------------------------------------------------------------------ contexto e geração
    def context(self, profile_id: str, *, counterparty: str | None = None, thread_key: str | None = None,
                current_content: str | None = None, recall_hint: str | None = None,
                touch: bool = False, app_id: str | None = None, capability: str | None = None) -> SocialContextDTO:
        self.get_profile(profile_id)
        return self.contexts.build(profile_id, counterparty=counterparty, thread_key=thread_key,
                                   current_content=current_content, recall_hint=recall_hint, touch=touch,
                                   app_id=app_id, capability=capability)

    async def draft_response(self, profile_id: str, *, kind: str, incoming: str = "", brief: str = "",
                             counterparty: str | None = None, thread_key: str | None = None, max_length: int = 300,
                             persist: bool = True, screen: str = "", runner: Any = None,
                             avoid: Sequence[str] = (), app_id: str | None = None,
                             capability: str | None = None) -> tuple[SocialDraftDTO, InteractionDTO | None]:
        """Gera o texto e o REGISTRA antes de qualquer envio (§16). Nada é enviado aqui: quem envia é o executor.

        `app_id` é o app da ETAPA: o "Você é @…" do prompt usa o handle da conta da pessoa nesse app, e só cai no
        usuário de cadastro (e no nome) quando não há conta registrada ali. O executor ainda não o passa; até lá o
        comportamento é o de sempre.

        Duas origens, o mesmo caminho: `incoming` é o que a contraparte disse (responder), `brief` é a intenção
        vinda do comando (comentar, puxar conversa). Pelo menos um dos dois precisa existir — sem nenhum, não há
        o que escrever.

        `avoid` são textos que não podem se repetir (os dos irmãos desta execução, por exemplo). A eles somam-se os
        últimos textos deste próprio perfil: repetir a si mesmo é tão delator quanto repetir o vizinho.

        `screen` é o que está ESCRITO na tela neste momento (legenda da publicação, comentários, a conversa aberta).
        É o ASSUNTO da escrita, não fala dirigida a esta conta: vai no bloco `<tela>`, e de lá não sai memória.

        `capability` é a ação da etapa (a chave do catálogo): com ela, a voz aprendida deste perfil nesta ação — os
        pares das aprovações que o dono editou e publicou (ADR-054) — entra no contexto. Sem ela, nada muda.
        """
        if not (incoming or "").strip() and not (brief or "").strip():
            raise SocialError("nothing_to_write", "Sem mensagem recebida nem intenção, não há texto a escrever.", 400)
        self.get_profile(profile_id)
        # A chave do fio sai daqui quando quem chama não a passou. Sem isto, quem escrevia uma mensagem direta
        # montava o contexto SEM conversa nenhuma (`thread=None`) enquanto o efeito, ao ser gravado, ia para
        # `dm:@alvo`: o resumo da conversa existia no banco e nunca chegava a quem estava escrevendo.
        if thread_key is None and kind in ("dm_reply", "dm_initiate"):
            thread_key = thread_de_dm(counterparty)
        # `current_content` fica vazio de propósito: o recebido já vai em `<conteudo_recebido>` e a tela em
        # `<tela>`; repeti-los em `<conteudo_atual>` só duplicaria o prompt. A tela e a intenção continuam
        # valendo como PISTA DE BUSCA — é o que torna "memória relevante" relativa ao que está aberto agora.
        ctx = self.context(profile_id, counterparty=counterparty, thread_key=thread_key,
                           recall_hint=" ".join(p for p in (screen, incoming, brief) if p), touch=True,
                           app_id=app_id, capability=capability)
        proibidos = _sem_repetir(list(avoid) + self._textos_recentes(profile_id))
        pedido = SocialRequest(
            profile_id=profile_id, username=ctx.username, kind=kind, context_text=ctx.rendered,
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
        # A persona fala só por si (ADR-055). Em 19/09 (r-20260919220216-7cfa59) sete contas escreveram à mesma
        # pessoa "seu marido mandou um oi" — recado que ninguém mandou. A regra está no papel do sistema; esta é a
        # trava em código: uma reescrita, e se ainda atribuir fala a alguém, recusa (espera uma pessoa).
        if not draft.refused and (trecho := fala_atribuida_a_terceiro(draft.content)):
            log.info("perfil %s: o texto atribuía fala a terceiro (%r); gerando de novo", profile_id, trecho)
            corrigido, _usage3 = await self._generate(replace(pedido, attribution_retry=True), runner=runner)
            resto = None if corrigido.refused else fala_atribuida_a_terceiro(corrigido.content)
            if corrigido.refused or (resto is None and (corrigido.content or "").strip()):
                draft = corrigido
            else:
                draft = SocialDraftDTO(
                    refused=True, rationale=draft.rationale,
                    refusal_reason=(f"o texto atribuía fala, intenção ou recado a um terceiro (“{resto or trecho}”); "
                                    "a persona fala só por si (ADR-055)"))
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
        profile_id = persona.id
        if body.profile_id and self.repo.persona_row(body.profile_id) is None:
            raise SocialError("not_found", "Perfil não encontrado.", 404)
        if body.profile_id and str(self.repo.persona_row(body.profile_id)["id"]) != profile_id:
            raise SocialError("persona_mismatch", "Esta persona não é a do perfil informado.", 400)
        # A persona É o perfil: a prévia usa o contexto dela (memória e relacionamento próprios; vazios numa pessoa
        # recém-criada). Sem `current_content`: o recebido já vai em `<conteudo_recebido>` na prévia também, e
        # duplicá-lo faria o operador conferir uma persona num prompt que não é o da execução.
        contexto = self.context(profile_id, counterparty=body.counterparty,
                                recall_hint=" ".join(p for p in (body.incoming, body.brief) if p))
        texto, username = contexto.rendered, contexto.username
        draft, _usage = await self._generate(SocialRequest(
            profile_id=profile_id, username=username, kind=body.kind, context_text=texto,
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


def _host_da_conta(valor: str | None) -> str | None:
    """O `host` de uma conta de portal, normalizado como a barra de endereço o mostra: minúsculo, sem esquema, sem
    caminho, sem porta. Vazio vira `None` (conta do app inteiro), que a unicidade trata como ''."""
    if not valor:
        return None
    t = valor.strip().casefold()
    t = t.split("://", 1)[1] if "://" in t else t
    t = t.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0].rsplit("@", 1)[-1].split(":", 1)[0]
    return t or None


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
