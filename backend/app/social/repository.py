"""Persistência do domínio social.

Regra de isolamento: **todo método que lê ou escreve dado de um perfil exige `profile_id`**. Não existe método que
devolva linha de perfil qualquer. O esquema reforça isso com chave estrangeira e unicidade; a API do repositório
reforça de novo, para um erro de consulta não virar vazamento entre perfis.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Sequence

from collections.abc import Callable
from datetime import date

from pydantic import ValidationError

from ..db import Database, INTEGRITY_ERRORS, OPERATIONAL_ERRORS, Row, dumps, loads
from ..models import (AppOnDevice, CredentialInfo, InstagramProfileDTO, OFFLINE_POLICY_PADRAO, PersonaBiography,
                      PersonaDeviceDTO,
                      PersonaGeneration, PersonaImageDTO, PersonaTraits, PersonaVisual, ProfileLocality,
                      SessionActions, SessionInfo, SessionStatus)
from ..modules.identity.domain.persona import idade_em, nome_exibido, separar_visual_legado
from ..planning.catalog import pacote_ancora
from ..util import new_token, now, now_iso, to_iso
from .sessao_gate import acoes_de_sessao, app_on_device

#: As colunas de `account_sessions` com o apelido que os leitores antigos esperam: `observed_username` era o nome em
#: `instagram_sessions`, e o motor de sessão, `state.py` e o DTO do perfil continuam lendo por ele.
_SESSAO = ("account_id, instance_id, status, observed_handle, observed_handle AS observed_username, verified_at,"
           " detail, unknown_streak, updated_at")


def sessao_vencida(session: Row | None, max_age_s: int) -> bool:
    """`session_ready` verificada há tempo demais. Uma regra só: o que a porta do despacho recusa é exatamente o
    que o cartão do perfil marca como dado velho.

    Só `session_ready` envelhece — os demais estados já dizem por si que a sessão não vale, e marcá-los de
    "velhos" seria dizer duas vezes a mesma coisa. `session_ready` SEM data de verificação é o pior caso: uma
    afirmação sem observação registrada, e conta como vencida.

    Função de módulo, e não método: ela não lê nada de perfil nenhum — recebe a linha que quem chamou já buscou
    com o `profile_id` na mão, que é a regra de isolamento deste arquivo.
    """
    if session is None or max_age_s <= 0 or session["status"] != SessionStatus.session_ready.value:
        return False
    verificada = session["verified_at"]
    return not verificada or verificada < to_iso(now() - timedelta(seconds=max_age_s))


class BindingConflict(RuntimeError):
    """Outra persona já serve ao mesmo app naquele aparelho (D2-a, migração 051). O serviço a traduz em 409
    `conta_do_app_ja_no_aparelho`; a mensagem nomeia quem está lá quando se sabe."""

    code = "conta_do_app_ja_no_aparelho"

    def __init__(self, instance_id: str, app_id: str | None, other_profile_id: str | None) -> None:
        quem = f"a persona {other_profile_id}" if other_profile_id else "outra persona"
        super().__init__(f"{quem} já usa {app_id or 'este app'} em {instance_id}; duas contas do mesmo app no mesmo "
                         "aparelho não convivem enquanto a troca de conta for manual. Escolha outro aparelho ou "
                         "desvincule quem está lá.")
        self.instance_id, self.app_id, self.other_profile_id = instance_id, app_id, other_profile_id


class SocialRepository:
    def __init__(self, db: Database):
        self.db = db
        # Validade do "Conectado", em segundos. Injetada pelo AppState a partir da configuração; 0 desliga. Fica
        # aqui porque é o repositório que monta o DTO do perfil, e é no cartão que a idade precisa aparecer.
        self.session_max_age_s: int = 0
        #: Pacote do app âncora do perfil (onde vive a conta). Preenchido pelo AppState a partir do registro de apps;
        #: `None` = não se sabe, e aí o DTO não afirma nada sobre o app no aparelho.
        self.app_package: str | None = None
        #: O rótulo desse app nas mensagens (`AppDefinition.label`); vazio = o pacote.
        self.app_name: str = ""
        #: As imagens de uma pessoa, para o DTO (migração 048). Injetado pelo AppState quando o serviço de imagens
        #: existe; sem ele o DTO sai com a lista vazia — o repositório não conhece storage nem provedor de imagem.
        self.imagens_de: Callable[[str], list[PersonaImageDTO]] | None = None
        #: O estado de um aparelho agora (`InstanceState.value`), para `PersonaDTO.devices` (051). Injetado pelo
        #: AppState; sem ele (teste, script) o DTO não afirma estado nenhum — o repositório não conhece o runtime.
        self.estado_do_aparelho: Callable[[str], str | None] | None = None
        #: Teto de `unknown_streak` (`LimitsCfg.session_unknown_retry_cap`), lido A CADA gravação: os limites são
        #: editáveis em tempo de execução. Injetado pelo AppState; sem ele (teste, script) o contador não tem teto.
        self.teto_de_reobservacao: Callable[[], int] | None = None

    # ------------------------------------------------------------------ perfis
    def create_profile(self, *, username: str, first_name: str | None, last_name: str | None,
                       display_name: str | None, birth_date: str | None, email: str | None,
                       persona_id: str | None) -> str:
        profile_id = f"ig-{new_token()}"
        now = now_iso()
        self.db.execute(
            "INSERT INTO instagram_profiles(id, username, display_name, first_name, last_name, birth_date, email,"
            " persona_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (profile_id, username, display_name, first_name, last_name, birth_date, email, persona_id, now, now))
        return profile_id

    def profile_row(self, profile_id: str) -> Row | None:
        return self.db.one("SELECT * FROM instagram_profiles WHERE id=?", (profile_id,))

    def profile_by_username(self, username: str) -> Row | None:
        return self.db.one("SELECT * FROM instagram_profiles WHERE lower(username)=lower(?)", (username,))

    def update_profile(self, profile_id: str, fields: dict[str, Any]) -> None:
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE instagram_profiles SET {sets}, updated_at=? WHERE id=?",
                        (*fields.values(), now_iso(), profile_id))

    def delete_profile(self, profile_id: str) -> None:
        """Em cascata: credencial, binding, sessão e tentativas somem junto (chave estrangeira do esquema)."""
        self.db.execute("DELETE FROM instagram_profiles WHERE id=?", (profile_id,))

    def list_profile_ids(self) -> list[str]:
        """Só quem TEM conta de cadastro (`username <> ''`): é a lista de perfis do painel de hoje. Todas as pessoas,
        com ou sem conta, saem por `list_persona_ids`."""
        return [r["id"] for r in self.db.query("SELECT id FROM instagram_profiles WHERE username <> ''"
                                               " ORDER BY username")]

    def list_persona_ids(self) -> list[str]:
        return [r["id"] for r in self.db.query(
            "SELECT id FROM instagram_profiles"
            " ORDER BY lower(COALESCE(NULLIF(display_name, ''), NULLIF(first_name, ''), username)), id")]

    def adopt_account(self, profile_id: str, *, username: str, first_name: str | None, last_name: str | None,
                      display_name: str | None, birth_date: str | None, email: str | None) -> None:
        """Uma pessoa que existia SEM conta ganha a conta de cadastro: a mesma linha, o mesmo id. Nome, nascimento e
        e-mail só entram onde a linha ainda estava vazia — quem criou a persona já disse quem ela é."""
        row = self.profile_row(profile_id)
        if row is None:
            raise KeyError(profile_id)
        campos: dict[str, object] = {"username": username}
        for coluna, valor in (("first_name", first_name), ("last_name", last_name), ("display_name", display_name),
                              ("birth_date", birth_date), ("email", email)):
            if valor and not row[coluna]:
                campos[coluna] = valor
        self.update_profile(profile_id, campos)

    # ------------------------------------------------------------------ contas por app (migrações 037 e 049)
    # A Conta é a entidade única (ADR-040): (perfil, app, host) com credencial, consentimento e UMA sessão por
    # aparelho. O Instagram é uma conta como as outras — a "conta âncora" do perfil, no app que provê a conta dele —
    # e é nela que `credential_row`/`session_row`, pelo `profile_id` de sempre, leem e gravam.
    def list_accounts(self, profile_id: str) -> list[Row]:
        return self.db.query("SELECT * FROM profile_accounts WHERE profile_id=? ORDER BY created_at", (profile_id,))

    def account_row(self, profile_id: str, account_id: str) -> Row | None:
        return self.db.one("SELECT * FROM profile_accounts WHERE id=? AND profile_id=?", (account_id, profile_id))

    def account_by_app(self, profile_id: str, app_id: str, host: str | None = None) -> Row | None:
        """A conta do perfil neste app — e, para app de navegador, neste site (`host`; nulo vale como '')."""
        return self.db.one("SELECT * FROM profile_accounts WHERE profile_id=? AND app_id=? AND COALESCE(host,'')=?",
                           (profile_id, app_id, host or ""))

    def create_account(self, profile_id: str, *, app_id: str, handle: str, notes: str = "",
                       host: str | None = None) -> str:
        account_id = f"acc-{new_token()}"
        agora = now_iso()
        self.db.execute("INSERT INTO profile_accounts(id, profile_id, app_id, handle, notes, host, created_at,"
                        " updated_at) VALUES (?,?,?,?,?,?,?,?)",
                        (account_id, profile_id, app_id, handle, notes, host, agora, agora))
        return account_id

    def update_account(self, profile_id: str, account_id: str, fields: dict[str, Any]) -> None:
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE profile_accounts SET {sets}, updated_at=? WHERE id=? AND profile_id=?",
                        (*fields.values(), now_iso(), account_id, profile_id))

    def delete_account(self, profile_id: str, account_id: str) -> None:
        self.db.execute("DELETE FROM profile_accounts WHERE id=? AND profile_id=?", (account_id, profile_id))

    def _pacote_da_conta_ancora(self) -> str | None:
        """O pacote do app que provê a conta do perfil: o que a composição injetou (`app_package`) ou, fora dela
        (serviço montado sem `AppState`, como nos testes), o app âncora do registro (`ancora_do_perfil`)."""
        return self.app_package or pacote_ancora()

    def conta_ancora(self, profile_id: str, *, criar: bool = False) -> Row | None:
        """A conta do perfil no app que provê a conta dele — onde vivem a credencial e a sessão que
        `credential_row`/`session_row` devolvem pelo `profile_id` (compatibilidade com quem lia `instagram_*`).

        `criar=True` cria a conta quando o cadastro não a criou: `create_profile` só a cria com o app registrado em
        `apps`. Fora disso (banco de teste sem `apps`), o `app_id` é o PACOTE — a identidade estável do app — e a
        procura aceita as duas grafias, para uma conta criada antes de o app ser registrado continuar sendo achada.
        O `handle` nasce do `username` do perfil; perfil sem @ (onda A: persona sem conta do Instagram) não cria.
        """
        pacote = self._pacote_da_conta_ancora()
        if pacote is None:
            return None
        app_id = self.db.scalar("SELECT id FROM apps WHERE package=?", (pacote,)) or pacote
        row = self.account_by_app(profile_id, app_id)
        if row is None and app_id != pacote:
            row = self.account_by_app(profile_id, pacote)
        if row is None and criar:
            perfil = self.profile_row(profile_id)
            # Persona SEM conta do app âncora (onda A: `username = ''`) nunca ganha uma por efeito colateral de
            # sessão ou de senha: a conta do Instagram é a linha de `profile_accounts`, criada de propósito.
            if perfil is None or not perfil["username"]:
                return None
            self.create_account(profile_id, app_id=app_id, handle=perfil["username"])
            row = self.account_by_app(profile_id, app_id)
        return row

    # ------------------------------------------------------------------ credencial da conta (só metadados aqui)
    def account_credential_row(self, profile_id: str, account_id: str) -> Row | None:
        return self.db.one("SELECT c.* FROM account_credentials c JOIN profile_accounts a ON a.id=c.account_id"
                           " WHERE c.account_id=? AND a.profile_id=?", (account_id, profile_id))

    def set_account_credential(self, profile_id: str, account_id: str, *, login_identifier: str, secret_ref: str,
                               key_id: str, consent_by: str | None = None) -> None:
        """Grava ou renova a credencial da conta. Senha nova zera falhas e bloqueio: é o que destrava a automação.

        `consent_by` é quem consentiu que a automação digite esta senha (grava `consent_at` agora). Sem ele, o
        consentimento que já existia fica — ele é sobre a CONTA, não sobre uma senha específica — e uma conta que
        nunca consentiu continua sem consentimento.
        """
        if self.account_row(profile_id, account_id) is None:
            raise KeyError(account_id)
        agora = now_iso()
        consentimento = agora if consent_by else None
        self.db.execute(
            "INSERT INTO account_credentials(account_id, login_identifier, secret_ref, key_id, status, failed_attempts,"
            " blocked_until, created_at, updated_at, consent_at, consent_by) VALUES (?,?,?,?,'active',0,NULL,?,?,?,?)"
            " ON CONFLICT(account_id) DO UPDATE SET login_identifier=excluded.login_identifier,"
            " secret_ref=excluded.secret_ref, key_id=excluded.key_id, updated_at=excluded.updated_at,"
            " status='active', failed_attempts=0, blocked_until=NULL,"
            " consent_at=COALESCE(excluded.consent_at, account_credentials.consent_at),"
            " consent_by=COALESCE(excluded.consent_by, account_credentials.consent_by)",
            (account_id, login_identifier, secret_ref, key_id, agora, agora, consentimento, consent_by))

    # ------------------------------------------------------------------ grupos de acesso (migração 036)
    def create_policy_group(self, *, name: str, description: str, capabilities: str, limits: str) -> str:
        group_id = f"grp-{new_token()}"
        agora = now_iso()
        self.db.execute("INSERT INTO policy_groups(id, name, description, capabilities, limits, created_at,"
                        " updated_at) VALUES (?,?,?,?,?,?,?)",
                        (group_id, name, description, capabilities, limits, agora, agora))
        return group_id

    def policy_group_row(self, group_id: str) -> Row | None:
        return self.db.one("SELECT * FROM policy_groups WHERE id=?", (group_id,))

    def policy_group_by_name(self, name: str) -> Row | None:
        return self.db.one("SELECT * FROM policy_groups WHERE lower(name)=lower(?)", (name,))

    def list_policy_groups(self) -> list[Row]:
        return self.db.query("SELECT * FROM policy_groups ORDER BY name")

    def update_policy_group(self, group_id: str, fields: dict[str, Any]) -> None:
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE policy_groups SET {sets}, updated_at=? WHERE id=?",
                        (*fields.values(), now_iso(), group_id))

    def delete_policy_group(self, group_id: str) -> None:
        """Sem chave estrangeira na coluna (ver a migração 036): os membros são desvinculados aqui, junto."""
        with self.db.tx():
            self.db.execute("UPDATE instagram_profiles SET policy_group_id=NULL, updated_at=? "
                            "WHERE policy_group_id=?", (now_iso(), group_id))
            self.db.execute("DELETE FROM policy_groups WHERE id=?", (group_id,))

    def policy_group_members(self, group_id: str) -> list[Row]:
        return self.db.query("SELECT id, username FROM instagram_profiles WHERE policy_group_id=? ORDER BY username",
                             (group_id,))

    def set_policy_group_members(self, group_id: str, profile_ids: list[str]) -> None:
        """A lista é a COMPLETA: quem estava no grupo e não está nela sai (volta a herdar só do padrão)."""
        agora = now_iso()
        with self.db.tx():
            self.db.execute("UPDATE instagram_profiles SET policy_group_id=NULL, updated_at=? "
                            "WHERE policy_group_id=?", (agora, group_id))
            for pid in profile_ids:
                self.db.execute("UPDATE instagram_profiles SET policy_group_id=?, updated_at=? WHERE id=?",
                                (group_id, agora, pid))

    # ------------------------------------------------------------------ credencial (só metadados aqui)

    def consent_account_credential(self, profile_id: str, account_id: str, *, consent_by: str) -> bool:
        """Marca o consentimento numa credencial que já existe. Devolve se havia credencial para marcar."""
        if self.account_credential_row(profile_id, account_id) is None:
            return False
        self.db.execute("UPDATE account_credentials SET consent_at=?, consent_by=?, updated_at=? WHERE account_id=?",
                        (now_iso(), consent_by, now_iso(), account_id))
        return True

    def delete_account_credential(self, profile_id: str, account_id: str) -> str | None:
        """Devolve a referência do segredo para quem chama apagar no cofre."""
        row = self.account_credential_row(profile_id, account_id)
        self.db.execute("DELETE FROM account_credentials WHERE account_id=?", (account_id,))
        return row["secret_ref"] if row else None

    def mark_account_credential(self, profile_id: str, account_id: str, *, status: str,
                                failed_attempts: int | None = None, blocked_until: str | None = None) -> None:
        fields: dict[str, Any] = {"status": status, "updated_at": now_iso()}
        if failed_attempts is not None:
            fields["failed_attempts"] = failed_attempts
        fields["blocked_until"] = blocked_until
        sets = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE account_credentials SET {sets} WHERE account_id=? AND account_id IN"
                        " (SELECT id FROM profile_accounts WHERE profile_id=?)",
                        (*fields.values(), account_id, profile_id))

    def touch_account_credential(self, profile_id: str, account_id: str) -> None:
        self.db.execute("UPDATE account_credentials SET last_used_at=? WHERE account_id=? AND account_id IN"
                        " (SELECT id FROM profile_accounts WHERE profile_id=?)", (now_iso(), account_id, profile_id))

    # ------------------------------------------------------------------ credencial da conta âncora (compatibilidade)
    # As assinaturas por `profile_id` ficam: o motor de sessão, `state.py` e o DTO do perfil leem por elas. O que
    # mudou é a casa — `account_credentials` da conta âncora, nunca mais `instagram_credentials` (só leitura até a
    # migração que a remove). As linhas voltam com os MESMOS nomes de coluna.
    def credential_row(self, profile_id: str) -> Row | None:
        conta = self.conta_ancora(profile_id)
        return self.account_credential_row(profile_id, conta["id"]) if conta is not None else None

    def set_credential(self, profile_id: str, *, login_identifier: str, secret_ref: str, key_id: str,
                       consent_by: str | None = None) -> None:
        conta = self.conta_ancora(profile_id, criar=True)
        if conta is None:
            raise KeyError(profile_id)               # sem app que proveja a conta não há onde guardar
        self.set_account_credential(profile_id, conta["id"], login_identifier=login_identifier, secret_ref=secret_ref,
                                    key_id=key_id, consent_by=consent_by)

    def delete_credential(self, profile_id: str) -> str | None:
        """Devolve a referência do segredo para quem chama apagar no cofre."""
        conta = self.conta_ancora(profile_id)
        return self.delete_account_credential(profile_id, conta["id"]) if conta is not None else None

    def mark_credential(self, profile_id: str, *, status: str, failed_attempts: int | None = None,
                        blocked_until: str | None = None) -> None:
        conta = self.conta_ancora(profile_id)
        if conta is not None:
            self.mark_account_credential(profile_id, conta["id"], status=status, failed_attempts=failed_attempts,
                                         blocked_until=blocked_until)

    def touch_credential(self, profile_id: str) -> None:
        conta = self.conta_ancora(profile_id)
        if conta is not None:
            self.touch_account_credential(profile_id, conta["id"])

    # ------------------------------------------------------------------ vínculo perfil <-> aparelho (N:N, 051)
    # O vínculo é N:N (design persona-e-parque §7): uma persona em N aparelhos e N personas num aparelho, uma por
    # APP (D2-a). As leituras são LISTAS; quem precisa de UM vínculo diz qual (o par, ou o principal da persona).
    # As duas leituras antigas de "o único" ficam só para compatibilidade e LEVANTAM erro quando há mais de um —
    # nunca escolhem em silêncio (risco R10: o primeiro vínculo ganharia e ninguém saberia).
    def bindings_of_profile(self, profile_id: str) -> list[Row]:
        """Os vínculos ATIVOS desta persona, o principal primeiro."""
        return self.db.query("SELECT * FROM device_profile_bindings WHERE profile_id=? AND active=1"
                             " ORDER BY is_primary DESC, id", (profile_id,))

    def profiles_of_instance(self, instance_id: str, app_id: str | None = None) -> list[Row]:
        """Os vínculos ATIVOS deste aparelho. Com `app_id`, só os que servem àquele app: o vínculo daquele app, ou
        um vínculo sem app (anterior à 051, ou "apps sem conta gerenciada") de persona que TEM conta no app — é o
        que decide "qual persona deste aparelho a tarefa do Instagram usa"."""
        if app_id is None:
            return self.db.query("SELECT * FROM device_profile_bindings WHERE instance_id=? AND active=1"
                                 " ORDER BY id", (instance_id,))
        return self.db.query("SELECT b.* FROM device_profile_bindings b WHERE b.instance_id=? AND b.active=1"
                             " AND (b.app_id=? OR (b.app_id IS NULL AND EXISTS (SELECT 1 FROM profile_accounts a"
                             " WHERE a.profile_id=b.profile_id AND a.app_id=?))) ORDER BY b.id",
                             (instance_id, app_id, app_id))

    def binding(self, profile_id: str, instance_id: str, app_id: str | None = None) -> Row | None:
        """O vínculo ativo do PAR (persona, aparelho): é por linha que a localidade (023) é fotografada. Com
        `app_id`, a linha daquele app; sem ele, a primeira do par (a localidade é a mesma em todas)."""
        if app_id is not None:
            return self.db.one("SELECT * FROM device_profile_bindings WHERE profile_id=? AND instance_id=?"
                               " AND app_id=? AND active=1", (profile_id, instance_id, app_id))
        return self.db.one("SELECT * FROM device_profile_bindings WHERE profile_id=? AND instance_id=? AND active=1"
                           " ORDER BY is_primary DESC, id LIMIT 1", (profile_id, instance_id))

    def binding_principal(self, profile_id: str) -> Row | None:
        """O aparelho PRINCIPAL da persona: alvo padrão de conectar/verificar/sair e do contexto operacional, e o
        que `PersonaDTO.instance_id` mostra. É o marcado `is_primary`; sem marca (linha inserida por fora), o mais
        antigo — a persona vinculada nunca fica sem principal."""
        return self.db.one("SELECT * FROM device_profile_bindings WHERE profile_id=? AND active=1"
                           " ORDER BY is_primary DESC, id LIMIT 1", (profile_id,))

    def perfil_unico_da_instancia(self, instance_id: str) -> str | None:
        """A persona do aparelho quando há EXATAMENTE uma; `None` para nenhuma ou para mais de uma. É o fallback
        dos caminhos que não recebem o perfil do objetivo — e que não podem escolher por conta própria."""
        ids = {str(v["profile_id"]) for v in self.profiles_of_instance(instance_id)}
        return ids.pop() if len(ids) == 1 else None

    def binding_row(self, profile_id: str) -> Row | None:
        """Compatibilidade: o vínculo da persona quando ela tem UM aparelho. Com mais de um levanta `ValueError` —
        quem precisa de um só deve pedir o par (`binding`) ou o principal (`binding_principal`)."""
        linhas = self.bindings_of_profile(profile_id)
        if len({str(b["instance_id"]) for b in linhas}) > 1:
            raise ValueError(f"a persona {profile_id} está vinculada a {len(linhas)} aparelhos; diga qual")
        return linhas[0] if linhas else None

    def profile_id_for_instance(self, instance_id: str) -> str | None:
        """Compatibilidade: a persona do aparelho quando há UMA. Com mais de uma levanta `ValueError`."""
        ids = {str(v["profile_id"]) for v in self.profiles_of_instance(instance_id)}
        if len(ids) > 1:
            raise ValueError(f"o aparelho {instance_id} tem {len(ids)} personas vinculadas; diga qual")
        return ids.pop() if ids else None

    def localidade_da_instancia(self, instance_id: str) -> tuple[str | None, str | None]:
        """(worker onde o aparelho está agora, impressão digital observada dele). `instances`, não perfil.

        Não viola a regra de isolamento deste arquivo: `instances` não é dado de perfil nenhum — é o inventário
        do parque, e é justamente o que o vínculo precisa fotografar para saber ONDE os dados foram gravados.
        """
        row = self.db.one("SELECT worker_id, physical_id FROM instances WHERE id=?", (instance_id,))
        if row is None:
            return None, None
        return row["worker_id"], row["physical_id"]

    def bind(self, profile_id: str, instance_id: str, *, app_id: str | None = None, primary: bool = False,
             reason: str | None = None) -> None:
        """Vincula a persona ao aparelho PARA um app (`None` = apps sem conta gerenciada). Não desvincula a própria
        persona de outro aparelho nem toma o aparelho de outra (era o 1:1); o par já vinculado é idempotente.

        Recusa com `BindingConflict` quando OUTRA persona já serve ao mesmo app naquele aparelho (D2-a): a troca de
        conta no Instagram é manual (achado #115), e duas contas no mesmo app do mesmo aparelho seriam uma tarefa
        entrando na conta errada. A conferência é a MESMA de `profiles_of_instance(iid, app_id)`, mais estrita que o
        índice `ux_binding_conta_do_app_no_aparelho`: o índice não enxerga o vínculo sem `app_id` (o de antes da
        051, ou "apps sem conta gerenciada") de uma persona que TEM conta no app — o repositório enxerga. O índice
        é o piso, para quem escreve por fora; o `IntegrityError` dele também vira `BindingConflict`.

        `primary`: torna este o aparelho principal (tirando a marca do anterior). A primeira vinculação da persona é
        principal por definição, para ela nunca ficar sem um. O histórico fica: linhas inativas são a auditoria.

        O vínculo fotografa a LOCALIDADE (migração 023): a máquina e a impressão digital do aparelho no momento
        em que os dados passam a viver ali. Reapontar depois `instances.worker_id` ou `instances.external` muda
        o id lógico de lugar, e é a diferença entre o fotografado e o atual que denuncia a troca.
        """
        with self.db.tx():
            if (conflito := self.quem_ja_serve(profile_id, instance_id, app_id)) is not None:
                raise BindingConflict(instance_id, *conflito)
            existente = self.binding(profile_id, instance_id, app_id) if app_id is not None else self.db.one(
                "SELECT * FROM device_profile_bindings WHERE profile_id=? AND instance_id=? AND app_id IS NULL"
                " AND active=1", (profile_id, instance_id))
            principal = primary or not self.bindings_of_profile(profile_id)
            if existente is not None:
                if principal and not existente["is_primary"]:
                    self.set_primary(profile_id, instance_id)
                return
            if principal:
                self.db.execute("UPDATE device_profile_bindings SET is_primary=0 WHERE profile_id=? AND active=1",
                                (profile_id,))
            worker_id, physical_id = self.localidade_da_instancia(instance_id)
            try:
                self.db.execute(
                    "INSERT INTO device_profile_bindings(profile_id, instance_id, active, bound_at, reason,"
                    " worker_id, physical_id, locality_at, app_id, is_primary) VALUES (?,?,1,?,?,?,?,?,?,?)",
                    (profile_id, instance_id, now_iso(), reason, worker_id, physical_id, now_iso(), app_id,
                     int(principal)))
            except INTEGRITY_ERRORS as exc:
                raise BindingConflict(instance_id, app_id, None) from exc

    def quem_ja_serve(self, profile_id: str | None, instance_id: str,
                      app_id: str | None) -> tuple[str | None, str] | None:
        """`(app, outra persona)` quando OUTRA persona já serve, naquele aparelho, a um app que este vínculo serviria
        (D2-a); `None` quando o vínculo pode entrar. Com `app_id`, é aquele app. Sem ele ("apps sem conta
        gerenciada"), são os apps das contas que a persona TEM: `profiles_of_instance(iid, app)` conta o vínculo sem
        app de quem tem conta no app, então o vínculo sem app de uma persona com conta do Instagram num aparelho que
        já tem outra conta do Instagram seriam duas — a mesma ambiguidade, pela porta dos fundos.
        `profile_id=None`: persona ainda por nascer (o cadastro pergunta ANTES de criar qualquer linha)."""
        apps = [app_id] if app_id is not None else (
            [str(c["app_id"]) for c in self.list_accounts(profile_id)] if profile_id is not None else [])
        for app in apps:
            for v in self.profiles_of_instance(instance_id, app):
                if v["profile_id"] != profile_id:
                    return app, str(v["profile_id"])
        return None

    def set_primary(self, profile_id: str, instance_id: str) -> None:
        """Marca o aparelho principal da persona (o par precisa estar vinculado; senão `KeyError`)."""
        if self.binding(profile_id, instance_id) is None:
            raise KeyError(instance_id)
        with self.db.tx():
            self.db.execute("UPDATE device_profile_bindings SET is_primary=0 WHERE profile_id=? AND active=1",
                            (profile_id,))
            self.db.execute("UPDATE device_profile_bindings SET is_primary=1 WHERE profile_id=? AND instance_id=?"
                            " AND active=1 AND id=(SELECT MIN(id) FROM device_profile_bindings WHERE profile_id=?"
                            " AND instance_id=? AND active=1)", (profile_id, instance_id, profile_id, instance_id))

    def registrar_localidade(self, profile_id: str, *, worker_id: str | None, physical_id: str | None,
                             instance_id: str | None = None) -> None:
        """Preenche a localidade do vínculo ativo com o que só se soube DEPOIS.

        A impressão digital costuma ser nula no instante do vínculo (o aparelho pode estar desligado) e só é lida
        quando ele entra no ar. `COALESCE` de propósito: o que ainda não se observou nunca apaga o que já se
        sabia — a mesma regra de `taskqueue/repository.py`. `worker_id` é escrito como veio, inclusive `NULL`
        (que quer dizer "este servidor"), porque `locality_at` já diz que a localidade foi registrada.
        Com `instance_id`, só as linhas daquele par: a localidade é do aparelho, não da persona.
        """
        sql = ("UPDATE device_profile_bindings SET worker_id=?, physical_id=COALESCE(?, physical_id), locality_at=?"
               " WHERE profile_id=? AND active=1")
        params: tuple[object, ...] = (worker_id, physical_id, now_iso(), profile_id)
        if instance_id is not None:
            sql += " AND instance_id=?"
            params += (instance_id,)
        self.db.execute(sql, params)

    def unbind(self, profile_id: str, instance_id: str | None = None, app_id: str | None = None, *,
               reason: str | None = None) -> None:
        """Desvincula a persona DAQUELE aparelho (e, com `app_id`, só daquele app); sem `instance_id`, de todos (o
        caminho antigo). Se o principal sai e sobra vínculo, o mais antigo que sobrou vira principal."""
        sql = ("UPDATE device_profile_bindings SET active=0, unbound_at=?, reason=COALESCE(?, reason), is_primary=0"
               " WHERE profile_id=? AND active=1")
        params: tuple[object, ...] = (now_iso(), reason, profile_id)
        if instance_id is not None:
            sql += " AND instance_id=?"
            params += (instance_id,)
        if app_id is not None:
            sql += " AND app_id=?"
            params += (app_id,)
        with self.db.tx():
            self.db.execute(sql, params)
            restantes = self.bindings_of_profile(profile_id)
            if restantes and not any(b["is_primary"] for b in restantes):
                self.db.execute("UPDATE device_profile_bindings SET is_primary=1 WHERE id=?", (restantes[0]["id"],))

    #: Estados de worker em que a máquina ainda responde. `degraded` é "conectado com problema declarado" — o
    #: aparelho pode até não servir, mas os dados do perfil continuam alcançáveis, que é o que esta pergunta faz.
    _WORKER_ALCANCAVEL = ("online", "degraded")

    def localidade(self, profile_id: str, binding: Row | None = None) -> ProfileLocality | None:
        """Onde os dados deste perfil vivem, e se o id lógico continua apontando para lá.

        `None` quando não há vínculo: sem aparelho não há localidade a afirmar. Com vínculo anterior à migração
        023 (`locality_at` nulo) devolve `known=False` e não acusa mudança nenhuma — falta de registro não é
        prova de troca.
        """
        binding = binding if binding is not None else self.binding_principal(profile_id)
        if binding is None:
            return None
        conhecida = binding["locality_at"] is not None
        worker_id = binding["worker_id"]
        if worker_id:
            w = self.db.one("SELECT name, state, maintenance FROM workers WHERE id=?", (worker_id,))
            nome = w["name"] if w else worker_id
            estado = ("maintenance" if w and w["maintenance"] else w["state"]) if w else "offline"
            observado = w["state"] if w else "offline"
        else:
            # `worker_id` nulo é este servidor — que, por estar respondendo esta chamada, está de pé.
            nome, estado, observado = "este servidor", "online", "online"
        disponivel = observado in self._WORKER_ALCANCAVEL
        atual_worker, atual_physical = self.localidade_da_instancia(binding["instance_id"])
        mudou_de_maquina = conhecida and atual_worker != worker_id
        mudou_de_aparelho = bool(conhecida and binding["physical_id"] and atual_physical
                                 and binding["physical_id"] != atual_physical)
        if mudou_de_maquina:
            detalhe = (f"os dados deste perfil vivem em {nome}, mas {binding['instance_id']} aponta hoje para "
                       f"{atual_worker or 'este servidor'}; a sessão de lá não existe aqui")
        elif mudou_de_aparelho:
            detalhe = (f"o aparelho físico por trás de {binding['instance_id']} mudou desde o vínculo; a sessão "
                       "gravada no disco anterior não está neste aparelho")
        elif not disponivel:
            detalhe = f"{nome} está {observado}: os dados deste perfil não estão alcançáveis agora"
        elif not conhecida:
            detalhe = "este vínculo é anterior ao registro de localidade; ainda não se sabe onde os dados vivem"
        else:
            detalhe = None
        return ProfileLocality(worker_id=worker_id, worker_name=nome, worker_state=estado, known=conhecida,
                               available=disponivel, moved=mudou_de_maquina or mudou_de_aparelho,
                               physical_id=binding["physical_id"], detail=detalhe)

    def binding_history(self, profile_id: str) -> list[Row]:
        return self.db.query("SELECT * FROM device_profile_bindings WHERE profile_id=? ORDER BY id DESC",
                             (profile_id,))

    # ------------------------------------------------------------------ sessão (cache do observado): por (conta, aparelho)
    def account_session_row(self, profile_id: str, account_id: str, instance_id: str) -> Row | None:
        return self.db.one(f"SELECT {_SESSAO} FROM account_sessions s WHERE account_id=? AND instance_id=?"
                           " AND account_id IN (SELECT id FROM profile_accounts WHERE profile_id=?)",
                           (account_id, instance_id, profile_id))

    def session_of_account(self, profile_id: str, account_id: str) -> Row | None:
        """A sessão da conta no aparelho PRINCIPAL do perfil; sem vínculo (ou sem linha nele), a mais recente que
        houver — é ela que diz "a sessão pronta é de OUTRO aparelho", e a coluna `instance_id` denuncia qual."""
        binding = self.binding_principal(profile_id)
        if binding is not None:
            row = self.account_session_row(profile_id, account_id, binding["instance_id"])
            if row is not None:
                return row
        return self.db.one(f"SELECT {_SESSAO} FROM account_sessions s WHERE account_id=? AND account_id IN"
                           " (SELECT id FROM profile_accounts WHERE profile_id=?) ORDER BY updated_at DESC LIMIT 1",
                           (account_id, profile_id))

    def set_account_session(self, profile_id: str, account_id: str, instance_id: str, *, status: SessionStatus,
                            observed_handle: str | None = None, verified_at: str | None = None,
                            detail: str | None = None, reobserved: bool = False) -> None:
        # `unknown_streak`: quantas vezes SEGUIDAS uma tela de verdade foi CLASSIFICADA e não reconhecida (achado
        # #104). `reobserved=True` é só o que o motor de sessão passa depois de reconhecer de fato a
        # tela (`integrations/app_declarado/sessao.py`, `_save`, casos "conta não pôde ser lida" e "não é login nem
        # autenticado"). As demais gravações de `unknown` (cadastro do perfil, wipe, troca de localidade, conta
        # errada) não vêm de uma classificação de tela — contá-las bloquearia perfil por evento administrativo,
        # não por tela presa. Qualquer status diferente de `unknown`, ou `unknown` sem `reobserved`, zera.
        #
        # E nunca passa do teto. O agendador para de reobservar AO chegar nele; o que somava acima era o "Verificar
        # conta" do painel (e a releitura depois do controle devolvido) — o android-01 ficou com 4 num teto de 3, e o
        # número acima do teto não diz nada além de "no teto" (causa C8, r-20260928195344-02ee9e).
        if self.account_row(profile_id, account_id) is None:
            raise KeyError(account_id)
        streak = 0
        if status is SessionStatus.unknown and reobserved:
            anterior = self.db.one("SELECT status, unknown_streak FROM account_sessions WHERE account_id=?"
                                   " AND instance_id=?", (account_id, instance_id))
            streak = int(anterior["unknown_streak"] or 0) + 1 if (anterior and
                       anterior["status"] == SessionStatus.unknown.value) else 1
            if self.teto_de_reobservacao is not None:
                streak = min(streak, self.teto_de_reobservacao())
        self.db.execute(
            "INSERT INTO account_sessions(account_id, instance_id, status, observed_handle, verified_at, detail,"
            " updated_at, unknown_streak) VALUES (?,?,?,?,?,?,?,?)"
            " ON CONFLICT(account_id, instance_id) DO UPDATE SET status=excluded.status,"
            " observed_handle=excluded.observed_handle, verified_at=excluded.verified_at,"
            " detail=excluded.detail, updated_at=excluded.updated_at, unknown_streak=excluded.unknown_streak",
            (account_id, instance_id, status.value, observed_handle, verified_at, detail, now_iso(), streak))

    def session_row(self, profile_id: str, instance_id: str | None = None) -> Row | None:
        """A sessão da conta âncora do perfil: NESTE aparelho quando ele é dito; senão a do aparelho vinculado, ou a
        mais recente. Mesmos nomes de coluna de `instagram_sessions`, para os leitores antigos."""
        conta = self.conta_ancora(profile_id)
        if conta is None:
            return None
        if instance_id is not None:
            return self.account_session_row(profile_id, conta["id"], instance_id)
        return self.session_of_account(profile_id, conta["id"])

    def set_session(self, profile_id: str, *, status: SessionStatus, instance_id: str | None = None,
                    observed_username: str | None = None, verified_at: str | None = None,
                    detail: str | None = None, reobserved: bool = False) -> None:
        """Grava a sessão da conta âncora NUM aparelho: o dito, senão o principal. Sessão é do par (conta,
        aparelho): sem aparelho nenhum não há o que gravar, e a leitura devolve `unknown` por ausência."""
        iid = instance_id
        if iid is None:
            binding = self.binding_principal(profile_id)
            iid = binding["instance_id"] if binding is not None else None
        if iid is None:
            return
        conta = self.conta_ancora(profile_id, criar=True)
        if conta is None:
            return
        self.set_account_session(profile_id, conta["id"], iid, status=status, observed_handle=observed_username,
                                 verified_at=verified_at, detail=detail, reobserved=reobserved)

    def invalidate_sessions_of_instance(self, instance_id: str, *, reason: str, package: str | None = None) -> int:
        """Wipe, perda do aparelho ou atualização do app: a sessão daquele aparelho deixa de valer.

        Por app: sem `package`, as contas do app âncora (o comportamento de sempre: é o que `state.py` invalida ao
        mexer no disco do app com provedor); com ele, as contas daquele app. A marcação que a pessoa fez num app
        sem provedor só cai quando aquele app é o alvo.

        O motivo é reescrito mesmo numa sessão que já estava `unknown`. Sem isso, uma sequência de operações
        deixaria no painel a explicação da PRIMEIRA delas — "o app foi atualizado" continuaria aparecendo depois de
        o app ter sido desinstalado e reinstalado, que é justamente quando a pessoa precisa saber o que houve.

        O retorno conta só quem de fato mudou de estado: é o que decide se vale emitir um aviso.
        """
        pacote = package or self._pacote_da_conta_ancora()
        if pacote is None:
            return 0
        app_id = self.db.scalar("SELECT id FROM apps WHERE package=?", (pacote,)) or pacote
        rows = self.db.query("SELECT s.account_id, s.status, a.profile_id FROM account_sessions s"
                             " JOIN profile_accounts a ON a.id = s.account_id"
                             " WHERE s.instance_id=? AND a.app_id IN (?, ?)", (instance_id, app_id, pacote))
        mudaram = 0
        for r in rows:
            if r["status"] != SessionStatus.unknown.value:
                mudaram += 1
            self.set_account_session(r["profile_id"], r["account_id"], instance_id, status=SessionStatus.unknown,
                                     detail=reason)
        return mudaram

    # ------------------------------------------------------------------ auditoria de autenticação (por conta)
    def start_auth_attempt(self, profile_id: str, instance_id: str, *, stage: str = "started",
                           account_id: str | None = None) -> int:
        """A tentativa é da CONTA (049). Sem `account_id`, a da conta âncora — o provedor do Instagram chama assim."""
        conta_id = account_id
        if conta_id is None:
            conta = self.conta_ancora(profile_id)
            conta_id = conta["id"] if conta is not None else None
        return int(self.db.inserted_id(
            "INSERT INTO authentication_attempts(profile_id, instance_id, started_at, stage, account_id)"
            " VALUES (?,?,?,?,?)", (profile_id, instance_id, now_iso(), stage, conta_id)) or 0)

    def finish_auth_attempt(self, profile_id: str, attempt_id: int, *, outcome: str, detail: str | None = None,
                            stage: str | None = None) -> None:
        self.db.execute(
            "UPDATE authentication_attempts SET finished_at=?, outcome=?, detail=?, stage=COALESCE(?, stage)"
            " WHERE id=? AND profile_id=?", (now_iso(), outcome, detail, stage, attempt_id, profile_id))

    def auth_attempts(self, profile_id: str, limit: int = 20, *, account_id: str | None = None) -> list[Row]:
        """As tentativas do perfil; com `account_id`, só as daquela conta (as anteriores à 049 apontam para a âncora)."""
        if account_id is None:
            return self.db.query("SELECT * FROM authentication_attempts WHERE profile_id=? ORDER BY id DESC LIMIT ?",
                                 (profile_id, limit))
        return self.db.query("SELECT * FROM authentication_attempts WHERE profile_id=? AND account_id=?"
                             " ORDER BY id DESC LIMIT ?", (profile_id, account_id, limit))

    # ------------------------------------------------------------------ DTO
    def profile_dto(self, profile_id: str) -> InstagramProfileDTO | None:
        row = self.profile_row(profile_id)
        if row is None:
            return None
        cred = self.credential_row(profile_id)
        binding = self.binding_principal(profile_id)
        session = self.session_row(profile_id)
        app, acoes = self._app_e_acoes(binding["instance_id"] if binding else None, cred, session)
        pessoa = campos_de_persona(row)
        imagens = self.imagens_de(row["id"]) if self.imagens_de is not None else []
        principal = next((i.id for i in imagens if i.is_primary), None)
        return InstagramProfileDTO(
            **pessoa, visual=visual_da_linha(row),
            generation=PersonaGeneration.model_validate(loads(row["generation"], {}) or {}),
            display_name=row["display_name"], first_name=row["first_name"],
            last_name=row["last_name"], birth_date=row["birth_date"], email=row["email"],
            # A persona é a própria pessoa: o painel de hoje acha "a persona do perfil" por estes dois campos.
            persona_id=row["id"], persona_name=pessoa["name"], status=row["status"],
            accounts_count=self.accounts_count(profile_id), images=imagens, primary_image_id=principal,
            policy_group_id=row["policy_group_id"],
            policy_group_name=(self.db.scalar("SELECT name FROM policy_groups WHERE id=?", (row["policy_group_id"],))
                               if row["policy_group_id"] else None),
            instance_id=binding["instance_id"] if binding else None,
            devices=self.devices_de(profile_id),
            locality=self.localidade(profile_id, binding),
            offline_policy=row["offline_policy"] or OFFLINE_POLICY_PADRAO,
            credential=CredentialInfo(
                configured=cred is not None,
                login_identifier=cred["login_identifier"] if cred else None,
                status=cred["status"] if cred else None,
                failed_attempts=cred["failed_attempts"] if cred else 0,
                blocked_until=cred["blocked_until"] if cred else None,
                updated_at=cred["updated_at"] if cred else None,
                last_used_at=cred["last_used_at"] if cred else None),
            session=SessionInfo(
                status=SessionStatus(session["status"]) if session else SessionStatus.unknown,
                instance_id=session["instance_id"] if session else None,
                observed_username=session["observed_username"] if session else None,
                verified_at=session["verified_at"] if session else None,
                detail=session["detail"] if session else None,
                stale=sessao_vencida(session, self.session_max_age_s)),
            app_on_device=app, session_actions=acoes,
            last_verified_at=row["last_verified_at"], last_activity_at=row["last_activity_at"])

    def sessao_no_aparelho(self, profile_id: str, app_id: str | None, instance_id: str) -> SessionInfo | None:
        """A sessão da conta do app do vínculo NESTE aparelho (a do app âncora quando o vínculo não tem app).
        `None` quando a persona não tem conta que sirva ao vínculo — aí não há sessão a afirmar."""
        conta = self.account_by_app(profile_id, app_id) if app_id is not None else self.conta_ancora(profile_id)
        if conta is None:
            return None
        s = self.account_session_row(profile_id, conta["id"], instance_id)
        return SessionInfo(
            status=SessionStatus(s["status"]) if s else SessionStatus.unknown, instance_id=instance_id,
            observed_username=s["observed_username"] if s else None, verified_at=s["verified_at"] if s else None,
            detail=s["detail"] if s else None, stale=sessao_vencida(s, self.session_max_age_s))

    def devices_de(self, profile_id: str) -> list[PersonaDeviceDTO]:
        """`PersonaDTO.devices` (051): cada vínculo ativo, o principal primeiro, com o estado do aparelho quando o
        runtime foi injetado e a sessão da conta daquele app lá."""
        saida: list[PersonaDeviceDTO] = []
        for v in self.bindings_of_profile(profile_id):
            iid = str(v["instance_id"])
            worker_id, _fisico = self.localidade_da_instancia(iid)
            saida.append(PersonaDeviceDTO(
                instance_id=iid, app_id=v["app_id"], is_primary=bool(v["is_primary"]),
                state=self.estado_do_aparelho(iid) if self.estado_do_aparelho is not None else None,
                worker_id=worker_id, bound_at=v["bound_at"],
                session=self.sessao_no_aparelho(profile_id, v["app_id"], iid)))
        return saida

    def _app_e_acoes(self, instance_id: str | None, cred: Row | None,
                     session: Row | None) -> tuple[AppOnDevice | None, SessionActions | None]:
        """O app da conta âncora no aparelho vinculado e o que a tela pode oferecer — da MESMA fonte que a rota recusa."""
        if self.app_package is None:
            return None, None
        return self.app_e_acoes_do_pacote(instance_id, self.app_package, self.app_name or self.app_package, cred,
                                          session)

    def app_e_acoes_do_pacote(self, instance_id: str | None, package: str, app_name: str, cred: Row | None,
                              session: Row | None) -> tuple[AppOnDevice | None, SessionActions | None]:
        """O mesmo, para a conta de QUALQUER app (ADR-040): o pacote da conta, a credencial e a sessão dela. Não é
        dado de perfil: `device_app_state` e `commands` são do aparelho; quem chama já trouxe as linhas do perfil."""
        app = None
        aberta = False
        if instance_id:
            app = app_on_device(self.db.one("SELECT * FROM device_app_state WHERE instance_id=? AND package_name=?",
                                            (instance_id, package)), package)
            aberta = self.db.scalar(
                "SELECT COUNT(*) FROM commands WHERE instance_id=? AND verb IN ('session.connect','session.verify')"
                " AND state IN ('created','dispatched','acked','running','cancel_requested')", (instance_id,)) > 0
        acoes = acoes_de_sessao(instance_id=instance_id, app=app, app_name=app_name,
                                credential_configured=cred is not None,
                                session_status=session["status"] if session else None, session_open=aberta)
        return app, acoes

    # ------------------------------------------------------------------ personas (= pessoas: a linha do perfil)
    # Desde a 047 não há tabela de persona separada: a voz, a biografia e o visual moram em `instagram_profiles`, e
    # "persona" e "perfil" são a mesma linha. O que sobra aqui é o vocabulário antigo apontando para ela.
    def create_persona(self, *, name: str, summary: str | None, persona_prompt: str, traits: dict[str, object],
                       visual: dict[str, object], biography: dict[str, object], generation: dict[str, object],
                       first_name: str | None, last_name: str | None, display_name: str | None,
                       birth_date: str | None, gender: str | None, locale: str | None) -> str:
        """Uma pessoa nova, ainda sem conta em app nenhum (`username = ''`)."""
        persona_id = f"ig-{new_token()}"
        agora = now_iso()
        self.db.execute(
            "INSERT INTO instagram_profiles(id, username, display_name, first_name, last_name, birth_date, gender,"
            " locale, summary, persona_prompt, traits, visual, biography, generation, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (persona_id, "", display_name or name, first_name, last_name, birth_date, gender, locale, summary,
             persona_prompt, dumps(traits), dumps(visual), dumps(biography), dumps(generation), agora, agora))
        return persona_id

    def persona_row(self, persona_id: str) -> Row | None:
        """A linha da pessoa. O id legado `persona-…` (coluna `persona_id`, rastro da 047) continua resolvendo, para
        um link antigo do painel ou de um script não morrer."""
        row = self.profile_row(persona_id)
        if row is None:
            row = self.db.one("SELECT * FROM instagram_profiles WHERE persona_id=?", (persona_id,))
        return row

    def update_persona(self, persona_id: str, fields: dict[str, object]) -> None:
        self.update_profile(persona_id, fields)

    def delete_persona(self, persona_id: str) -> None:
        """Apagar a persona É apagar a pessoa: contas, credencial, vínculo, sessão e memória caem junto (FKs)."""
        self.delete_profile(persona_id)

    def account_for_package(self, profile_id: str, package: str | None) -> Row | None:
        """A conta desta pessoa no app de `package` (`profile_accounts` × `apps`), se houver."""
        if not package:
            return None
        return self.db.one("SELECT a.* FROM profile_accounts a JOIN apps ap ON ap.id = a.app_id"
                           " WHERE a.profile_id=? AND ap.package=?", (profile_id, package))

    def accounts_count(self, profile_id: str) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM profile_accounts WHERE profile_id=?", (profile_id,)) or 0)

    # ------------------------------------------------------------------ histórico social
    def record_interaction(self, profile_id: str, *, type: str, direction: str, status: str,
                           instance_id: str | None = None, run_id: str | None = None,
                           objective_id: str | None = None, step_id: str | None = None,
                           counterparty: str | None = None, thread_key: str | None = None,
                           incoming_content: str | None = None, outgoing_content: str | None = None,
                           target: str | None = None, context: dict[str, Any] | None = None,
                           evidence: str | None = None, metadata: dict[str, Any] | None = None,
                           occurred_at: str | None = None, app_id: str | None = None) -> str:
        interaction_id = f"int-{new_token()}"
        now = now_iso()
        self.db.execute(
            "INSERT INTO social_interactions(id, profile_id, instance_id, run_id, objective_id, step_id, occurred_at,"
            " type, direction, counterparty, thread_key, incoming_content, outgoing_content, target, context, status,"
            " evidence, metadata, created_at, updated_at, app_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (interaction_id, profile_id, instance_id, run_id, objective_id, step_id, occurred_at or now, type,
             direction, counterparty, thread_key, incoming_content, outgoing_content, target, dumps(context or {}),
             status, evidence, dumps(metadata or {}), now, now, app_id))
        return interaction_id

    def interactions_by_step(self, profile_id: str, step_id: str, *, status: str | None = None) -> list[Row]:
        """Interações abertas por uma etapa. Serve à confirmação manual: quem confirma a etapa precisa fechar
        também o que ela deixou em aberto no histórico do perfil.

        Exige `profile_id` como todo método por perfil, e não porque a etapa pudesse pertencer a dois: exige
        porque a regra desta classe é que a ASSINATURA diga de quem é o dado, em vez de depender de uma invariante
        mantida em outro arquivo. Quem chama já tem o perfil fotografado em `objectives.profile_id`.
        """
        if status is None:
            return self.db.query("SELECT * FROM social_interactions WHERE profile_id=? AND step_id=? ORDER BY seq",
                                 (profile_id, step_id))
        return self.db.query(
            "SELECT * FROM social_interactions WHERE profile_id=? AND step_id=? AND status=? ORDER BY seq",
            (profile_id, step_id, status))

    def update_interaction(self, profile_id: str, interaction_id: str, **fields: Any) -> None:
        if not fields:
            return
        for chave in ("context", "metadata"):
            if chave in fields and not isinstance(fields[chave], str):
                fields[chave] = dumps(fields[chave] or {})
        sets = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE social_interactions SET {sets}, updated_at=? WHERE id=? AND profile_id=?",
                        (*fields.values(), now_iso(), interaction_id, profile_id))

    def interaction_row(self, profile_id: str, interaction_id: str) -> Row | None:
        return self.db.one("SELECT * FROM social_interactions WHERE id=? AND profile_id=?",
                           (interaction_id, profile_id))

    def list_interactions(self, profile_id: str, *, counterparty: str | None = None, thread_key: str | None = None,
                          status: str | None = None, direction: str | None = None, limit: int = 20,
                          app_id: str | None = None) -> list[Row]:
        onde = ["profile_id=?"]
        args: list[Any] = [profile_id]
        for coluna, valor in (("counterparty", counterparty), ("thread_key", thread_key), ("status", status),
                              ("direction", direction), ("app_id", app_id)):
            if valor is not None:
                onde.append(f"{coluna}=?")
                args.append(valor)
        args.append(limit)
        return self.db.query(
            f"SELECT * FROM social_interactions WHERE {' AND '.join(onde)} ORDER BY seq DESC LIMIT ?", tuple(args))

    def count_interactions(self, profile_id: str, *, status: str | None = None) -> int:
        if status:
            return int(self.db.scalar("SELECT COUNT(*) FROM social_interactions WHERE profile_id=? AND status=?",
                                      (profile_id, status)) or 0)
        return int(self.db.scalar("SELECT COUNT(*) FROM social_interactions WHERE profile_id=?", (profile_id,)) or 0)

    # ------------------------------------------------------------------ contagem para os limites
    # Os limites contam o HISTÓRICO, não um contador separado: um contador à parte poderia divergir do que
    # realmente aconteceu na conta, e é justamente o que aconteceu na conta que importa.
    def count_interactions_since(self, profile_id: str, since: str, *, types: tuple[str, ...],
                                 statuses: tuple[str, ...], direction: str | None = None) -> int:
        if not types or not statuses:
            return 0
        t, s = ",".join("?" * len(types)), ",".join("?" * len(statuses))
        extra, args = self._direcao(direction)
        return int(self.db.scalar(
            f"SELECT COUNT(*) FROM social_interactions WHERE profile_id=? AND occurred_at >= ?"
            f" AND type IN ({t}) AND status IN ({s}){extra}",
            (profile_id, since, *types, *statuses, *args)) or 0)

    def oldest_interaction_since(self, profile_id: str, since: str, *, types: tuple[str, ...],
                                 statuses: tuple[str, ...], direction: str | None = None) -> str | None:
        if not types or not statuses:
            return None
        t, s = ",".join("?" * len(types)), ",".join("?" * len(statuses))
        extra, args = self._direcao(direction)
        return self.db.scalar(
            f"SELECT MIN(occurred_at) FROM social_interactions WHERE profile_id=? AND occurred_at >= ?"
            f" AND type IN ({t}) AND status IN ({s}){extra}", (profile_id, since, *types, *statuses, *args))

    @staticmethod
    def _direcao(direction: str | None) -> tuple[str, tuple[Any, ...]]:
        """Filtro de direção para as contagens de limite.

        Limite é sobre o que ESTA conta FAZ. Desde que o histórico passou a guardar também o que a conta RECEBEU
        (mensagem lida numa conversa), contar por tipo sem olhar a direção faria a caixa de entrada consumir a
        cota de envio do perfil — quem escreveu foi a outra pessoa.
        """
        return (" AND direction=?", (direction,)) if direction else ("", ())

    def inbound_exists(self, profile_id: str, *, type: str, content: str, thread_key: str | None = None,
                       counterparty: str | None = None) -> bool:
        """Esta fala de entrada já está gravada? É a trava contra a releitura da mesma conversa virar histórico novo.

        A mesma conversa é lida de novo a cada execução: sem isto, "oi, tudo bem?" entraria uma vez por leitura e o
        perfil acharia que a pessoa repetiu a mesma frase cinco vezes — e a memória aprenderia isso.
        """
        onde = ["profile_id=?", "direction='inbound'", "type=?", "incoming_content=?"]
        args: list[Any] = [profile_id, type, content]
        for coluna, valor in (("thread_key", thread_key), ("counterparty", counterparty)):
            if valor is not None:
                onde.append(f"{coluna}=?")
                args.append(valor)
        return self.db.one(f"SELECT id FROM social_interactions WHERE {' AND '.join(onde)} LIMIT 1",
                           tuple(args)) is not None

    def count_run_interactions(self, profile_id: str, run_id: str, *, statuses: tuple[str, ...]) -> int:
        s = ",".join("?" * len(statuses))
        return int(self.db.scalar(
            f"SELECT COUNT(*) FROM social_interactions WHERE profile_id=? AND run_id=? AND direction='outbound'"
            f" AND status IN ({s})", (profile_id, run_id, *statuses)) or 0)

    def recent_outgoing_texts(self, profile_id: str, limit: int) -> list[str]:
        """Os últimos textos que ESTE perfil escreveu — para não repetir a si mesmo.

        O filtro é SQL, não Python: com o limite aplicado antes, um perfil que recebeu algumas mensagens desde o
        último texto próprio devolvia lista vazia, e a lista de "não repita" perdia em silêncio justamente a
        parte que evita repetir o que ele publicou ontem.
        """
        return [r["outgoing_content"] for r in self.db.query(
            "SELECT outgoing_content FROM social_interactions WHERE profile_id=? AND direction='outbound'"
            " AND outgoing_content IS NOT NULL AND trim(outgoing_content)<>''"
            " ORDER BY seq DESC LIMIT ?", (profile_id, limit))]

    def last_external_interaction_at(self, profile_id: str, *, statuses: tuple[str, ...]) -> str | None:
        s = ",".join("?" * len(statuses))
        return self.db.scalar(
            f"SELECT MAX(occurred_at) FROM social_interactions WHERE profile_id=? AND direction='outbound'"
            f" AND status IN ({s})", (profile_id, *statuses))

    def fleet_targeting(self, counterparty: str, since: str, *, types: tuple[str, ...],
                        statuses: tuple[str, ...], exclude_profile_id: str,
                        app_id: str | None = None) -> tuple[int, str | None]:
        """ÚNICA exceção deliberada à regra de isolamento deste arquivo (ver docstring do módulo).

        A regra existe para que o conteúdo de um perfil nunca vaze para outro. Isto aqui não devolve conteúdo
        nenhum — nem linha, nem texto, nem `profile_id` de quem — só uma CONTAGEM agregada de quantos OUTROS
        perfis da frota mexeram com o mesmo alvo (`counterparty`) numa janela — pela interação de saída OU por um
        pedido de aprovação ainda em aberto sobre ele (ADR-055) —, e QUANDO foi a ação mais recente entre eles. É o
        dado mínimo para o achado #114: sem enxergar a frota inteira, nada detecta 8 contas seguindo a mesma pessoa
        em 20 minutos — um padrão que pertence à conta que opera, não a um perfil só.
        """
        if not types or not statuses or not counterparty:
            return 0, None
        t, s = ",".join("?" * len(types)), ",".join("?" * len(statuses))
        # Item 12.1: @nasa no Instagram e @nasa no TikTok são alvos diferentes — a coordenação é por (app, alvo).
        # Interação sem app (anterior à migração 037 foi toda preenchida) conta em qualquer app, por segurança.
        por_app = " AND (app_id=? OR app_id IS NULL)" if app_id else ""
        contas = {str(r["profile_id"]) for r in self.db.query(
            f"SELECT DISTINCT profile_id FROM social_interactions"
            f" WHERE counterparty=? AND profile_id<>? AND occurred_at>=? AND direction='outbound'"
            f" AND type IN ({t}) AND status IN ({s}){por_app}",
            (counterparty, exclude_profile_id, since, *types, *statuses, *((app_id,) if app_id else ())))}
        ultima = self.db.scalar(
            f"SELECT MAX(occurred_at) FROM social_interactions"
            f" WHERE counterparty=? AND profile_id<>? AND occurred_at>=? AND direction='outbound'"
            f" AND type IN ({t}) AND status IN ({s}){por_app}",
            (counterparty, exclude_profile_id, since, *types, *statuses, *((app_id,) if app_id else ())))
        # ADR-055: um pedido de aprovação ainda em aberto de outra conta RESERVA o alvo. Sem isto, duas execuções
        # quase juntas passavam as duas pela porta — nenhuma tinha disparado nada ainda — e a pessoa aprovava as duas.
        # O alvo do pedido é gravado cru (`@Ana`, `ana`) nos pedidos antigos: compara-se normalizado dos dois lados.
        # Os ids servem só para a união das duas fontes; daqui sai apenas a contagem.
        contas |= {str(r["profile_id"]) for r in self.db.query(
            "SELECT DISTINCT profile_id FROM pending_approvals WHERE profile_id IS NOT NULL AND profile_id<>?"
            " AND status IN ('pending','approved','edited') AND interaction_id IS NULL AND created_at>=?"
            " AND lower(ltrim(trim(target), '@'))=?",
            (exclude_profile_id, since, counterparty.lower().lstrip("@")))}
        return len(contas), ultima

    def has_inbound_from(self, profile_id: str, counterparty: str, *, types: tuple[str, ...],
                         app_id: str | None = None) -> bool:
        """A contraparte já escreveu a este perfil (fala de ENTRADA destes tipos)? Pergunta do próprio perfil."""
        if not types or not counterparty:
            return False
        t = ",".join("?" * len(types))
        por_app = " AND (app_id=? OR app_id IS NULL)" if app_id else ""
        return self.db.one(
            f"SELECT 1 FROM social_interactions WHERE profile_id=? AND counterparty=? AND direction='inbound'"
            f" AND type IN ({t}){por_app} LIMIT 1",
            (profile_id, counterparty, *types, *((app_id,) if app_id else ()))) is not None

    # ------------------------------------------------------------------ memória
    def insert_memory(self, profile_id: str, *, subject: str, content: str, source: str, fingerprint: str,
                      interaction_id: str | None = None, importance: float = 0.5, confidence: float = 0.5,
                      expires_at: str | None = None, app_id: str | None = None) -> str:
        memory_id = f"mem-{new_token()}"
        now = now_iso()
        self.db.execute(
            "INSERT INTO memory_items(id, profile_id, subject, content, source, interaction_id, importance,"
            " confidence, fingerprint, expires_at, created_at, updated_at, app_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (memory_id, profile_id, subject, content, source, interaction_id, importance, confidence, fingerprint,
             expires_at, now, now, app_id))
        return memory_id

    def memory_row(self, profile_id: str, memory_id: str) -> Row | None:
        return self.db.one("SELECT * FROM memory_items WHERE id=? AND profile_id=?", (memory_id, profile_id))

    def memory_by_fingerprint(self, profile_id: str, fingerprint: str) -> Row | None:
        return self.db.one("SELECT * FROM memory_items WHERE profile_id=? AND fingerprint=?",
                           (profile_id, fingerprint))

    def merge_memory(self, profile_id: str, memory_id: str, *, importance: float, confidence: float,
                     interaction_id: str | None, expires_at: str | None) -> None:
        """O mesmo fato observado de novo não duplica: conta uma ocorrência e fica mais importante/confiável."""
        # `CASE` e nao `MAX(a, b)`: o MAX escalar de dois argumentos e do SQLite. No PostgreSQL, `MAX` e agregacao,
        # e a chamada falha com "function max(real, double precision) does not exist" — parametro chega como
        # `double precision` e a coluna e `real`. `GREATEST` resolveria no PostgreSQL e nao existe no SQLite; `CASE`
        # vale nos dois. Este escapou ao inventario do E6 porque o teste que o cobre abria SQLite direto.
        self.db.execute(
            "UPDATE memory_items SET occurrences=occurrences+1,"
            " importance=CASE WHEN ? > importance THEN ? ELSE importance END,"
            " confidence=CASE WHEN ? > confidence THEN ? ELSE confidence END,"
            " interaction_id=COALESCE(?, interaction_id),"
            " expires_at=COALESCE(?, expires_at), updated_at=? WHERE id=? AND profile_id=?",
            (importance, importance, confidence, confidence, interaction_id, expires_at, now_iso(), memory_id,
             profile_id))

    def replace_memory_content(self, profile_id: str, memory_id: str, *, content: str, fingerprint: str,
                               expires_at: str | None) -> None:
        """A mesma tela vista de novo com MAIS conteúdo: o fato cresce no lugar (mesmo id), não vira outro."""
        self.db.execute(
            "UPDATE memory_items SET content=?, fingerprint=?, occurrences=occurrences+1,"
            " expires_at=COALESCE(?, expires_at), updated_at=? WHERE id=? AND profile_id=?",
            (content, fingerprint, expires_at, now_iso(), memory_id, profile_id))

    def list_memories(self, profile_id: str, *, subject: str | None = None, limit: int = 100,
                      include_expired: bool = False, now: str | None = None, app_id: str | None = None) -> list[Row]:
        onde = ["profile_id=?"]
        args: list[Any] = [profile_id]
        if app_id is not None:
            onde.append("app_id=?")
            args.append(app_id)
        if subject is not None:
            onde.append("subject=?")
            args.append(subject)
        if not include_expired:
            onde.append("(expires_at IS NULL OR expires_at > ?)")
            args.append(now or now_iso())
        args.append(limit)
        return self.db.query(
            f"SELECT * FROM memory_items WHERE {' AND '.join(onde)} ORDER BY importance DESC, seq DESC LIMIT ?",
            tuple(args))

    def search_memories(self, profile_id: str, termos: Sequence[str], *, limit: int = 60,
                        include_expired: bool = False, now: str | None = None) -> list[Row]:
        """Busca por relevância. Recebe **termos**, não uma expressão pronta.

        Isso é deliberado e foi a correção de um defeito: quem chamava montava `"a" OR "b"`, que é sintaxe do FTS5.
        O `plainto_tsquery` do PostgreSQL trata aquilo como texto comum e junta tudo com E — inclusive a palavra
        literal "or", que não existe em conteúdo nenhum. Resultado: no PostgreSQL a busca NUNCA encontrava nada, e
        sem erro: quem chamou caía no caminho alternativo e recebia a lembrança errada. Sintaxe de índice é assunto
        de quem conhece o índice, e quem conhece é este método.

        O índice de texto é compartilhado; o filtro por perfil é o que separa os perfis — por isso ele fica aqui, na
        única consulta que toca o índice, e não na chamada de quem usa.
        """
        termos = [t for t in termos if t]
        if not termos:
            return []
        extra = "" if include_expired else " AND (m.expires_at IS NULL OR m.expires_at > ?)"
        # É o único ponto do projeto onde os dois bancos divergem de verdade na CONSULTA: FTS5 com `bm25()` no
        # SQLite, `tsvector` com `ts_rank` no PostgreSQL. Um `if` aqui é mais honesto que uma abstração que
        # fingisse que busca textual é igual nos dois.
        if self.db.dialect == "postgres":
            # `||` entre tsquery é OU. Um `plainto_tsquery` por termo, parametrizado: termo vindo da tela do app
            # nunca é concatenado em texto de consulta. `sem_acento` nos dois lados (migração 017).
            # Os parênteses em volta do `||` são obrigatórios, não estilo: `@@` e `||` têm a MESMA precedência e
            # associam à esquerda, então `busca @@ q1 || q2` seria lido como `(busca @@ q1) || q2` — booleano OU
            # tsquery. Sem eles a consulta não casava com nada.
            tq = "(" + " || ".join(["plainto_tsquery('simple', sem_acento(?))"] * len(termos)) + ")"
            sql = (f"SELECT m.*, ts_rank(m.busca, {tq}) AS rank FROM memory_items m"
                   f" WHERE m.busca @@ {tq} AND m.profile_id=?{extra} ORDER BY rank DESC LIMIT ?")
            args: list[Any] = [*termos, *termos, profile_id]      # o tsquery aparece duas vezes: rank e filtro
        else:
            # Cada termo entre aspas: `NEAR(`, `*`, `^` e aspas soltas não são interpretados como sintaxe. Texto
            # hostil simplesmente não encontra nada; nunca derruba a consulta.
            expressao = " OR ".join(f'"{t}"' for t in dict.fromkeys(termos))
            sql = ("SELECT m.*, bm25(memory_fts) AS rank FROM memory_fts JOIN memory_items m ON m.seq=memory_fts.rowid"
                   f" WHERE memory_fts MATCH ? AND m.profile_id=?{extra} ORDER BY rank LIMIT ?")
            args = [expressao, profile_id]
        if not include_expired:
            args.append(now or now_iso())
        args.append(limit)
        try:
            linhas = self.db.query(sql, tuple(args))
        except OPERATIONAL_ERRORS:
            # Tolerância SÓ no SQLite, e de propósito. Lá o texto da tela vira expressão do FTS5, e expressão
            # inválida é um caso previsto: busca sem resultado não é erro. No PostgreSQL o termo é PARÂMETRO de
            # `plainto_tsquery`, que aceita qualquer texto — então nada que venha da tela pode dar erro ali, e o que
            # der é defeito meu. `OPERATIONAL_ERRORS` inclui `ProgrammingError`, então engolir aqui esconderia erro
            # de sintaxe: foi exatamente o que aconteceu enquanto esta consulta estava sem um parêntese, e a busca
            # devolveu lista vazia em silêncio em vez de falhar.
            if self.db.dialect != "sqlite":
                raise
            return []
        return self._com_relevancia(linhas)

    def _com_relevancia(self, linhas: list[Row]) -> list[Row]:
        """Acrescenta `relevancia` em 0..1, onde 1 é o mais relevante — **nos dois bancos**.

        Existe porque as duas notas são opostas, e isso não é detalhe de formatação: `bm25()` é NEGATIVO e menor
        significa melhor; `ts_rank` é POSITIVO e maior significa melhor. Quem consome a busca normalizava dividindo
        pelo `min()`, o que é certo para bm25 e silenciosamente errado para `ts_rank` — pegava o PIOR como referência
        e o corte em 1.0 achatava tudo, apagando a ordenação por relevância sem erro nenhum.
        Normalizar aqui é o certo: é o único lugar do projeto que já sabe qual banco respondeu.
        """
        if not linhas:
            return linhas
        notas = [(r["rank"] or 0.0) for r in linhas]
        melhor = max(notas) if self.db.dialect == "postgres" else min(notas)
        for r in linhas:
            r["relevancia"] = 0.0 if not melhor else min(1.0, (r["rank"] or 0.0) / melhor)
        return linhas

    def delete_memory(self, profile_id: str, memory_id: str) -> bool:
        cur = self.db.execute("DELETE FROM memory_items WHERE id=? AND profile_id=?", (memory_id, profile_id))
        return bool(cur.rowcount)

    def touch_memories(self, profile_id: str, memory_ids: list[str]) -> None:
        if not memory_ids:
            return
        marcas = ",".join("?" for _ in memory_ids)
        self.db.execute(
            f"UPDATE memory_items SET last_used_at=? WHERE profile_id=? AND id IN ({marcas})",
            (now_iso(), profile_id, *memory_ids))

    def purge_expired_memories(self, profile_id: str, *, now: str | None = None) -> int:
        cur = self.db.execute("DELETE FROM memory_items WHERE profile_id=? AND expires_at IS NOT NULL"
                              " AND expires_at <= ?", (profile_id, now or now_iso()))
        return int(cur.rowcount or 0)

    # ------------------------------------------------------------------ relacionamento e conversa
    def relationship_row(self, profile_id: str, counterparty: str) -> Row | None:
        return self.db.one("SELECT * FROM relationship_summaries WHERE profile_id=? AND counterparty=?",
                           (profile_id, counterparty))

    def upsert_relationship(self, profile_id: str, counterparty: str, *, summary: str | None = None,
                            tone: str | None = None, bump: bool = False, last_interaction_at: str | None = None
                            ) -> None:
        now = now_iso()
        self.db.execute(
            "INSERT INTO relationship_summaries(profile_id, counterparty, summary, tone, interactions,"
            " first_interaction_at, last_interaction_at, updated_at) VALUES (?,?,?,?,?,?,?,?)"
            # A coluna da linha EXISTENTE precisa do nome da tabela: sozinha, `summary` é ambígua entre a linha
            # nova e a antiga, e o PostgreSQL recusa (o SQLite adivinhava).
            " ON CONFLICT(profile_id, counterparty) DO UPDATE SET"
            " summary=COALESCE(excluded.summary, relationship_summaries.summary),"
            " tone=COALESCE(excluded.tone, relationship_summaries.tone),"
            " interactions=relationship_summaries.interactions+excluded.interactions,"
            " last_interaction_at=COALESCE(excluded.last_interaction_at,"
            " relationship_summaries.last_interaction_at),"
            " updated_at=excluded.updated_at",
            (profile_id, counterparty, summary or "", tone, 1 if bump else 0,
             last_interaction_at or now if bump else None, last_interaction_at or (now if bump else None), now))

    def list_relationships(self, profile_id: str, *, limit: int = 50) -> list[Row]:
        return self.db.query(
            "SELECT * FROM relationship_summaries WHERE profile_id=? ORDER BY last_interaction_at DESC LIMIT ?",
            (profile_id, limit))

    def thread_row(self, profile_id: str, thread_key: str) -> Row | None:
        return self.db.one("SELECT * FROM thread_summaries WHERE profile_id=? AND thread_key=?",
                           (profile_id, thread_key))

    def upsert_thread(self, profile_id: str, thread_key: str, *, summary: str | None = None,
                      counterparty: str | None = None, bump: bool = False,
                      last_message_at: str | None = None) -> None:
        now = now_iso()
        self.db.execute(
            "INSERT INTO thread_summaries(profile_id, thread_key, counterparty, summary, messages, last_message_at,"
            " updated_at) VALUES (?,?,?,?,?,?,?)"
            " ON CONFLICT(profile_id, thread_key) DO UPDATE SET"
            " counterparty=COALESCE(excluded.counterparty, thread_summaries.counterparty),"
            " summary=COALESCE(excluded.summary, thread_summaries.summary),"
            " messages=thread_summaries.messages+excluded.messages,"
            " last_message_at=COALESCE(excluded.last_message_at, thread_summaries.last_message_at),"
            " updated_at=excluded.updated_at",
            (profile_id, thread_key, counterparty, summary or "", 1 if bump else 0,
             last_message_at or (now if bump else None), now))


# ---------------------------------------------------------------------- a pessoa a partir da linha
def traits_da_linha(row: Row) -> PersonaTraits:
    """`traits` como a VOZ. Leitor de compatibilidade: uma linha gravada antes da 047 (ou por um cliente antigo) pode
    trazer as três chaves visuais dentro de `traits`; elas são ignoradas aqui e lidas por `visual_da_linha`."""
    voz, _visual = separar_visual_legado(loads(row["traits"], {}) or {})
    try:
        return PersonaTraits.model_validate(voz)
    except ValidationError:
        # Dado gravado fora do contrato (chave desconhecida, valor fora do Literal) não pode derrubar a listagem
        # inteira de pessoas: a voz sai vazia e o painel mostra as lacunas.
        return PersonaTraits()


def visual_da_linha(row: Row) -> PersonaVisual:
    _voz, legado = separar_visual_legado(loads(row["traits"], {}) or {})
    dados = {**legado, **(loads(row["visual"], {}) or {})}
    try:
        return PersonaVisual.model_validate(dados)
    except ValidationError:
        return PersonaVisual()


def campos_de_persona(row: Row, *, hoje: date | None = None) -> dict[str, object]:
    """Os campos de `PersonaVoiceDTO` a partir da linha do perfil — o construtor de contexto e o DTO completo montam
    a MESMA pessoa daqui. `username` vazio vira `None` na borda (a coluna guarda `''` = sem conta)."""
    try:
        biografia = PersonaBiography.model_validate(loads(row["biography"], {}) or {})
    except ValidationError:
        biografia = PersonaBiography()
    username = row["username"] or None
    idade = idade_em(row["birth_date"], hoje or now().date())
    return {
        "id": row["id"], "name": nome_exibido(row["display_name"], row["first_name"], row["last_name"], username),
        "summary": row["summary"], "persona_prompt": row["persona_prompt"] or "", "traits": traits_da_linha(row),
        "biography": biografia, "age": idade if idade is not None else biografia.approx_age,
        "gender": row["gender"], "locale": row["locale"], "profile_id": row["id"], "profile_username": username,
        "username": username, "created_at": row["created_at"], "updated_at": row["updated_at"],
    }
