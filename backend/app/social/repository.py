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
from ..metricas import metricas
from ..shared.vinculos import tem_vinculo_ativo
from ..util import new_token, now, now_iso, to_iso
from .contas_nossas import hash_do_handle, citacao_da_conta, foi_retirada, registrar_lapide, rotulo_da_conta, MARCADOR
from .limpeza_de_conta import AparelhoDaLimpeza
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


#: De onde vem a afirmação de que uma conta travou (ADR-055, migração 054): a tela foi LIDA (`observado`), uma
#: pessoa DISSE (`declarado`) ou uma regra DECIDIU (`regra`, como o bloqueio do ADR-029 por desafio).
ORIGENS_DE_BLOQUEIO = ("observado", "declarado", "regra")
#: A origem do `instances.account_label` quando é a plataforma que o deriva (054). NULL = configuração ou digitado.
_ROTULO_DERIVADO = ("vinculo", "marcador")


def normalizar_handle(handle: str) -> str:
    """A conta como o marcador a guarda: sem '@' e em minúsculas — o Instagram não distingue, e o índice de
    unicidade do marcador aberto precisa ver `@Felipe` e `felipe` como a mesma conta."""
    return handle.strip().lstrip("@").strip().lower()


def frase_da_quarentena(marcador: Row, acao: str | None = None, conta: str | None = None) -> str:
    """Por que o aparelho está em quarentena, na MESMA frase para o 409 do painel, o histórico do comando, a porta
    do despacho e a entrega que ficou de fora. Função de módulo: recebe a linha que quem chamou já buscou. `conta` é
    como citar a conta (`SocialRepository.citacao_da_conta`: o @ de conta retirada não volta ao texto, 29.24); sem
    ele, o @ do marcador."""
    quem = f" ({marcador['seen_by']})" if marcador.get("seen_by") else ""
    texto = (f"{marcador['instance_id']} está em quarentena: a {conta or 'conta @' + str(marcador['handle'])} está travada e logada "
             f"nele desde {marcador['since']} ({marcador['origin']}{quem}; ADR-055). Nada toca neste aparelho além "
             "de parar ou hibernar até uma pessoa decidir")
    if acao:
        texto += f"; '{acao}' só com a confirmação explícita da pessoa (confirm_locked_account)"
    return texto


class AparelhoEmQuarentena(RuntimeError):
    """O aparelho tem conta travada logada (marcador aberto, 054): nenhuma persona é vinculada nele. O serviço a
    traduz em 409 `aparelho_em_quarentena`. O android-04 ficou no ar com o felipe no desafio e sem vínculo, e o
    vínculo, a troca de aparelho e o cadastro aceitariam outra persona ali — que entraria no app de uma conta morta."""

    code = "aparelho_em_quarentena"

    def __init__(self, marcador: Row, conta: str | None = None) -> None:
        super().__init__(frase_da_quarentena(marcador, conta=conta) + ". Escolha outro aparelho.")
        self.marcador = marcador


#: Achado da revisão da fila (suíte 31): um pedido sem interação só "está em aberto" enquanto o objetivo dele está vivo.
#: A aprovação aprovada cuja etapa nunca disparou (falhou antes do efeito, objetivo vencido ou encerrado) nunca ganha
#: `interaction_id`, e o `expire_for_objective` só expira as pendentes. Sem este corte ela reservava o alvo na frota
#: (ADR-055), recusava a resposta nova por 30 dias (30.56) e ocupava o teto por hora e por dia (30.57), sem aparecer em
#: Pendências. O pedido avulso, sem objetivo, segue contando (o lado seguro); `waiting_user` e `uncertain` também.
_DE_OBJETIVO_VIVO = (" AND (objective_id IS NULL OR NOT EXISTS (SELECT 1 FROM objectives o"
                     " WHERE o.id=pending_approvals.objective_id AND o.status IN ('succeeded','failed','cancelled')))")


def _do_plano_em_vigor(tabela: str = "pending_approvals") -> str:
    """30.61 (revisão, item 6): o sim dado no plano para uma versão ANTERIOR do plano do objetivo não está mais em aberto,
    qualquer que seja a chave. Ele não migra para a etapa revisada (`acompanhar_revisao` o exclui) e nunca vai ganhar
    `interaction_id`; sem este corte, reservava alvo e teto e virava "mensagem repetida" contra a etapa nova."""
    return (f" AND NOT ({tabela}.origem='plano' AND {tabela}.plan_version < COALESCE((SELECT ov.plan_version"
            f" FROM objectives ov WHERE ov.id={tabela}.objective_id), {tabela}.plan_version))")


class SocialRepository:
    def __init__(self, db: Database):
        self.db = db
        #: Toda mudança de status do perfil passa por `mudar_status` e é anunciada aqui:
        #: `(profile_id, anterior, novo, origem, autor, evidencia)`. Injetado pelo `SocialService`, que tem o
        #: barramento; sem ele (teste, script) a mudança é gravada do mesmo jeito, só sem evento.
        self.on_status_changed: Callable[[str, str, str, str, str, str | None], None] | None = None
        #: Marcador de conta travada criado ou resolvido: `(linha do marcador, "marcado" | "resolvido", autor)`.
        self.on_locked_account: Callable[[Row, str, str], None] | None = None
        #: Bloqueio CONFIRMADO numa conta (29.23, ADR-068): `(profile_id, app_id, handle, instance_id, evidencia)`. É
        #: o que o `SocialService` liga à retirada da conta; o marcador já foi gravado quando isto dispara.
        self.on_conta_bloqueada: Callable[[str, str | None, str, str, str | None, str], None] | None = None
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
        """Grava campos do perfil. O `status` NUNCA vai direto para o `UPDATE`: passa por `mudar_status`, que
        preenche `blocked_*` e anuncia a mudança. Quem escreve o status por aqui sem dizer a origem é regra do
        sistema — hoje só o bloqueio por desafio do ADR-029 (`session_rules.bloquear_por_desafio`); quem sabe a
        origem chama `mudar_status` direto."""
        campos = dict(fields)
        status = campos.pop("status", None)
        if campos:
            sets = ", ".join(f"{k}=?" for k in campos)
            self.db.execute(f"UPDATE instagram_profiles SET {sets}, updated_at=? WHERE id=?",
                            (*campos.values(), now_iso(), profile_id))
        if status is not None:
            self.mudar_status(profile_id, str(status), origem="regra", autor="sistema")

    def mudar_status(self, profile_id: str, status: str, *, origem: str, autor: str,
                     evidencia: str | None = None) -> bool:
        """Troca o status do perfil deixando rastro (ADR-055). Devolve se houve mudança.

        Antes, `status` era uma coluna qualquer: em 28/09 havia cinco perfis `blocked` sem dizer quando, por quê nem
        quem decidiu, e a mudança não gerava evento. Agora a entrada em `blocked` grava `blocked_at`, a evidência e a
        origem; a saída (a pessoa reativou) os zera — o histórico fica nos eventos `profile.status`. Confirmar o
        mesmo status não é mudança: sem gravação e sem evento.
        """
        if origem not in ORIGENS_DE_BLOQUEIO:
            raise ValueError(f"origem de status desconhecida: {origem!r} (use {', '.join(ORIGENS_DE_BLOQUEIO)})")
        linha = self.profile_row(profile_id)
        if linha is None:
            return False
        anterior = str(linha["status"] or "active")
        if anterior == status:
            return False
        bloqueio: tuple[str | None, str | None, str | None] = (
            (now_iso(), (evidencia or "")[:500] or None, origem) if status == "blocked" else (None, None, None))
        self.db.execute("UPDATE instagram_profiles SET status=?, blocked_at=?, blocked_evidence=?, blocked_origin=?,"
                        " updated_at=? WHERE id=?", (status, *bloqueio, now_iso(), profile_id))
        if self.on_status_changed is not None:
            self.on_status_changed(profile_id, anterior, status, origem, autor, evidencia)
        return True

    def delete_profile(self, profile_id: str) -> None:
        """Em cascata: credencial, binding, sessão e tentativas somem junto (chave estrangeira do esquema). O
        marcador de conta travada NÃO (054): ele é do aparelho, e a conta continua logada lá."""
        aparelhos = {str(b["instance_id"]) for b in self.bindings_of_profile(profile_id)}
        self.db.execute("DELETE FROM instagram_profiles WHERE id=?", (profile_id,))
        for iid in sorted(aparelhos):
            self._sincronizar_rotulo(iid)

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
        self._sincronizar_rotulos_da_persona(profile_id)
        return account_id

    def update_account(self, profile_id: str, account_id: str, fields: dict[str, Any]) -> None:
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE profile_accounts SET {sets}, updated_at=? WHERE id=? AND profile_id=?",
                        (*fields.values(), now_iso(), account_id, profile_id))
        if "handle" in fields:
            self._sincronizar_rotulos_da_persona(profile_id)

    def delete_account(self, profile_id: str, account_id: str) -> None:
        self.db.execute("DELETE FROM profile_accounts WHERE id=? AND profile_id=?", (account_id, profile_id))
        self._sincronizar_rotulos_da_persona(profile_id)

    def retirar_conta_bloqueada(self, profile_id: str, account_id: str, *, ancora: bool, motivo: str) -> list[str]:
        """Tira do banco TUDO o que faz a conta existir para a plataforma, sem tocar a persona (29.23, ADR-068).
        Devolve as referências de segredo que a conta tinha, para quem chama apagar no cofre (depois de conferir que
        nenhuma outra linha as usa). Quem chama já está numa transação.

        - credencial da conta e, na âncora, a LEGADA (`instagram_credentials`, só leitura desde a 049: era ela que
          segurava o ciphertext no cofre); sessões da conta, e a legada na âncora;
        - o vínculo de aparelho que serviria à conta (o do app dela e o sem app, que serve ao app âncora pela conta
          da persona); o de OUTRO app fica — a persona segue ligada ao que não é a conta bloqueada;
        - a linha da conta (mesmo sendo âncora: a regra "âncora não se remove" é da remoção comum);
        - na âncora, o `username` esvazia: a pessoa vira persona sem conta, como as `ig-persona-*` (`list_profile_ids`
          deixa de listá-la; `list_persona_ids` continua).
        O marcador de conta travada do APARELHO (054) fica: a conta segue logada lá até alguém limpar o app."""
        conta = self.account_row(profile_id, account_id)
        if conta is None:
            return []
        refs: list[str] = []
        if (cred := self.account_credential_row(profile_id, account_id)) is not None:
            refs.append(str(cred["secret_ref"]))
        self.db.execute("DELETE FROM account_credentials WHERE account_id=?", (account_id,))
        if ancora:
            legada = self.db.one("SELECT secret_ref FROM instagram_credentials WHERE profile_id=?", (profile_id,))
            if legada is not None:
                refs.append(str(legada["secret_ref"]))
            self.db.execute("DELETE FROM instagram_credentials WHERE profile_id=?", (profile_id,))
            self.db.execute("DELETE FROM instagram_sessions WHERE profile_id=?", (profile_id,))
        # Explícito, sem esperar a cascata: a sessão é da conta, e a conta vai embora.
        self.db.execute("DELETE FROM account_sessions WHERE account_id=?", (account_id,))
        aparelhos = {str(b["instance_id"]) for b in self.bindings_of_profile(profile_id)
                     if b["app_id"] is None or b["app_id"] == conta["app_id"]}
        if aparelhos:
            self.db.execute("UPDATE device_profile_bindings SET active=0, unbound_at=?, reason=?, is_primary=0"
                            " WHERE profile_id=? AND active=1 AND (app_id IS NULL OR app_id=?)",
                            (now_iso(), motivo[:200], profile_id, conta["app_id"]))
            restantes = self.bindings_of_profile(profile_id)
            if restantes and not any(b["is_primary"] for b in restantes):
                self.db.execute("UPDATE device_profile_bindings SET is_primary=1 WHERE id=?", (restantes[0]["id"],))
        # A lápide: o produto segue sabendo que o @ foi NOSSO (`eh_conta_nossa`), só pelo hash (migração 071). O
        # @ de cadastro entra também quando difere do da conta (a âncora o tem nos dois lugares).
        perfil = self.profile_row(profile_id)
        for h in dict.fromkeys([conta["handle"], perfil["username"] if perfil is not None and ancora else None]):
            if hash_do_handle(h):
                registrar_lapide(self.db, app_id=str(conta["app_id"]), handle=h, profile_id=profile_id)
        self.db.execute("DELETE FROM profile_accounts WHERE id=? AND profile_id=?", (account_id, profile_id))
        if ancora:
            self.db.execute("UPDATE instagram_profiles SET username='', updated_at=? WHERE id=?",
                            (now_iso(), profile_id))
        # O @ some do que o produto vivo ainda mostra (rótulo do aparelho e marcador da quarentena), 29.24.
        self.mascarar_contas_retiradas()
        self._sincronizar_rotulos_da_persona(profile_id)
        for iid in sorted(aparelhos):
            self._sincronizar_rotulo(iid)
        return refs

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

    def eh_pacote_ancora(self, profile_id: str, package: str | None) -> bool:
        """`package` é o do app que provê a conta do perfil? `profile_id` só pela regra de isolamento do repositório:
        a resposta é a mesma para qualquer perfil (a âncora é da instalação, não da pessoa)."""
        return bool(package) and package == self._pacote_da_conta_ancora()

    def conta_do_pacote(self, profile_id: str, package: str | None, *, criar: bool = False) -> Row | None:
        """A conta do perfil no app de `package` — a casa da credencial, da tentativa e da sessão que o motor de
        sessão daquele app lê e grava (item 23.4, ADR-057).

        No app âncora é `conta_ancora` (com o `criar` de lá: é o que `set_session` sempre fez). Em qualquer outro app,
        a linha de `profile_accounts` daquele app, pelas duas grafias de `conta_ancora` (o id de `apps` ou o próprio
        pacote); `criar` não vale ali — conta de outro app nasce de propósito, no cadastro, nunca por efeito de sessão.
        Nunca cai na âncora: a sessão do segundo app gravada na conta do primeiro era exatamente o login preso à
        âncora."""
        if not package:
            return None
        if self.eh_pacote_ancora(profile_id, package):
            return self.conta_ancora(profile_id, criar=criar)
        app_id = self.db.scalar("SELECT id FROM apps WHERE package=?", (package,)) or package
        row = self.account_by_app(profile_id, app_id)
        if row is None and app_id != package:
            row = self.account_by_app(profile_id, package)
        return row

    def pacote_da_conta(self, profile_id: str, account_id: str) -> str | None:
        """O pacote do app desta conta do perfil: o de `apps`; sem a linha lá, o `app_id` quando ele já é um pacote
        (conta criada antes de o app ser registrado, como em `conta_ancora`). `None` quando a conta não é do perfil."""
        conta = self.account_row(profile_id, account_id)
        if conta is None:
            return None
        pacote = self.db.scalar("SELECT package FROM apps WHERE id=?", (conta["app_id"],))
        return str(pacote) if pacote else str(conta["app_id"])

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
        return self.db.query("SELECT id, username, display_name, first_name, last_name FROM instagram_profiles "
                             "WHERE policy_group_id=? ORDER BY username, id", (group_id,))

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
    # As assinaturas por `profile_id` ficam para o DTO do perfil e para quem só conhece o perfil. O que mudou é a
    # casa — `account_credentials` da conta âncora, nunca mais `instagram_credentials` (só leitura até a migração que
    # a remove). As linhas voltam com os MESMOS nomes de coluna. O motor de sessão e a porta de sessão NÃO leem por
    # aqui desde o 23.4: resolvem a conta do app (`conta_do_pacote`) e usam as leituras `account_*` dela.
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
            # Quarentena (ADR-055) antes de tudo: com conta travada logada no aparelho, nenhuma persona entra —
            # nem a própria dona da conta, que está bloqueada. A recusa é daqui, e não só do serviço, para quem
            # vincula por fora dele também topar nela.
            if (marcador := self.conta_travada_no_aparelho(instance_id)) is not None:
                raise AparelhoEmQuarentena(marcador, self.citacao_da_conta(marcador))
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
            self._sincronizar_rotulo(instance_id)

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

    def conflito_da_conta_nova(self, profile_id: str | None, app_id: str,
                               instance_id: str | None = None) -> tuple[str, str | None, str] | None:
        """`(aparelho, app, outra persona)` quando GANHAR uma conta em `app_id` faria a persona servir, num aparelho,
        a um app que OUTRA persona já serve ali (D2-a); `None` quando a conta pode nascer. É o outro lado de
        `quem_ja_serve`: aquele pergunta no vínculo, este na conta — a porta que o vínculo não vê.

        O vínculo SEM app serve a todo app em que a persona tem conta (`profiles_of_instance`), então uma pessoa
        vinculada sem app a um aparelho que já tem o Instagram de outra persona passa a servir o Instagram no
        instante em que ganha a conta: o vínculo, feito antes, não tinha app nenhum para conferir (29.29).
        Confere: o aparelho do cadastro (`instance_id`, que ainda vai ser vinculado) e cada aparelho de vínculo
        sem app da persona. O vínculo COM app não entra: `bind` já o conferiu quando foi feito, e a conta nova não
        muda o que ele serve. Persona que já tem conta no app também não: o vínculo sem app dela já o servia.
        `profile_id=None`: pessoa ainda por nascer, sem vínculo nenhum (só o aparelho do cadastro conta).
        Quem chama confere ANTES de criar qualquer linha: o 409 quer dizer "nada foi criado"."""
        aparelhos: list[str] = [instance_id] if instance_id else []
        if profile_id is not None and not self.db.one(
                "SELECT 1 FROM profile_accounts WHERE profile_id=? AND app_id=?", (profile_id, app_id)):
            aparelhos += [str(b["instance_id"]) for b in self.db.query(
                "SELECT DISTINCT instance_id FROM device_profile_bindings WHERE profile_id=? AND active=1"
                " AND app_id IS NULL ORDER BY instance_id", (profile_id,))]
        for iid in dict.fromkeys(aparelhos):
            if (conflito := self.quem_ja_serve(profile_id, iid, app_id)) is not None:
                return iid, *conflito
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
            aparelhos = {str(b["instance_id"]) for b in self.bindings_of_profile(profile_id)
                         if instance_id is None or b["instance_id"] == instance_id}
            self.db.execute(sql, params)
            restantes = self.bindings_of_profile(profile_id)
            if restantes and not any(b["is_primary"] for b in restantes):
                self.db.execute("UPDATE device_profile_bindings SET is_primary=1 WHERE id=?", (restantes[0]["id"],))
            for iid in sorted(aparelhos):
                self._sincronizar_rotulo(iid)

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

    # ------------------------------------------------------------------ conta travada no aparelho (054, ADR-055)
    # O marcador é do APARELHO, não do perfil nem do vínculo — por isso estas leituras não pedem `profile_id`, como a
    # localidade de `instances` acima. O android-04 ficou no ar com o felipe no desafio e sem vínculo nenhum: pela
    # regra de isolamento, perguntar "de quem é este aparelho" devolvia "de ninguém", e o parque tratou um aparelho
    # com conta morta logada como aparelho livre.
    def conta_travada_no_aparelho(self, instance_id: str) -> Row | None:
        """O marcador ABERTO do aparelho (o mais antigo, se houver mais de uma conta travada), ou `None`."""
        return self.db.one("SELECT * FROM device_locked_accounts WHERE instance_id=? AND resolved_at IS NULL"
                           " ORDER BY id LIMIT 1", (instance_id,))

    def rotulo_da_conta(self, marcador: Row) -> str:
        """Como citar a conta do marcador num aviso novo: `@handle`, ou `[conta removida]` se ela já saiu (29.23/29.24)."""
        return rotulo_da_conta(self.db, str(marcador["handle"]))

    def citacao_da_conta(self, marcador: Row) -> str:
        """A conta do marcador numa frase de aviso: `conta @x`, ou `conta retirada (bloqueada)` (29.24)."""
        return citacao_da_conta(self.db, str(marcador["handle"]))

    def mascarar_contas_retiradas(self) -> dict[str, int]:
        """Tira o @ de conta JÁ retirada (lápide, 29.23) do que ainda o mostra ao vivo, sem migração (29.24): o
        `handle` dos marcadores ABERTOS (a quarentena segue aberta até uma pessoa resolver) e o rótulo DERIVADO do
        aparelho (origem `marcador`/`vinculo`). Idempotente; roda na retirada e na subida. O rótulo de configuração
        (origem nula, do `config.yaml`) não é nosso para mexer: só é contado em `rotulo_de_configuracao`. Evento
        antigo fica como está (opção A do dono)."""
        n = {"marcadores": 0, "rotulos": 0, "rotulo_de_configuracao": 0}
        for m in self.db.query("SELECT id, instance_id, handle FROM device_locked_accounts WHERE resolved_at IS NULL"
                               " AND handle<>?", (MARCADOR,)):
            if not foi_retirada(self.db, str(m["handle"])):
                continue
            if self.db.scalar("SELECT COUNT(*) FROM device_locked_accounts WHERE instance_id=? AND handle=?"
                              " AND resolved_at IS NULL", (m["instance_id"], MARCADOR)):
                continue  # o índice único (aparelho, conta) já tem um marcador mascarado: este fica como está
            self.db.execute("UPDATE device_locked_accounts SET handle=? WHERE id=?", (MARCADOR, m["id"]))
            n["marcadores"] += 1
        for r in self.db.query("SELECT id, account_label, account_label_origin FROM instances"
                               " WHERE account_label IS NOT NULL AND account_label<>?", (MARCADOR,)):
            if not foi_retirada(self.db, str(r["account_label"])):
                continue
            if r["account_label_origin"] in _ROTULO_DERIVADO:
                self.db.execute("UPDATE instances SET account_label=? WHERE id=?", (MARCADOR, r["id"]))
                n["rotulos"] += 1
            else:
                n["rotulo_de_configuracao"] += 1
        return n

    def contas_travadas_abertas(self) -> list[Row]:
        """Todos os marcadores abertos, de todos os aparelhos: é o que a saúde do sistema confere."""
        return self.db.query("SELECT * FROM device_locked_accounts WHERE resolved_at IS NULL"
                             " ORDER BY instance_id, id")

    def marcar_conta_travada(self, instance_id: str, handle: str, evidencia: str | None, origem: str, *,
                             visto_por: str = "sistema", profile_id: str | None = None,
                             app_id: str | None = None) -> bool:
        """Registra que `handle` está travada e LOGADA em `instance_id` (tela "Confirm you are human", desafio). É a
        porta pública para quem detecta — o detector de tela chama daqui. Devolve `True` quando o marcador nasceu
        agora; `False` quando já havia um aberto para a mesma conta naquele aparelho (idempotente: detectar de novo
        não repete o aviso).

        O que acontece junto, porque é a mesma afirmação:
        - o perfil dono da conta (achado pelo `profile_id`, pelo @ do cadastro ou pela conta do app) passa a
          `blocked` com a evidência e a origem — decisão do dono em 28/09: o desafio É a conta bloqueada. Só a partir
          de `active`: perfil pausado pelo dono (`disabled`) fica como ele deixou. Só pela conta do app âncora (item
          23.5, `_trava_a_persona`): a de outro app para sozinha (credencial em `review`, por `session_rules.aplicar_desafio`);
        - o `account_label` do aparelho passa a dizer a conta que está lá de verdade.

        Não desvincula nem toca no aparelho: o que fazer com ele é decisão de pessoa. A partir daqui o aparelho está
        em quarentena — vínculo, verbos e entregas recusados, fora da escada de reparo e do reinício de saúde.
        """
        if origem not in ORIGENS_DE_BLOQUEIO:
            raise ValueError(f"origem desconhecida: {origem!r} (use {', '.join(ORIGENS_DE_BLOQUEIO)})")
        conta = normalizar_handle(handle)
        if not conta:
            raise ValueError("a conta travada precisa de um @ (handle)")
        if self.db.one("SELECT 1 FROM device_locked_accounts WHERE instance_id=? AND handle=? AND resolved_at IS NULL",
                       (instance_id, conta)) is not None:
            return False
        dona = self._dona_da_conta(conta, profile_id, app_id)
        pid = profile_id or (str(dona["profile_id"]) if dona is not None else None)
        app = app_id or (str(dona["app_id"]) if dona is not None and dona["app_id"] else None)
        agora = now_iso()
        texto = (evidencia or "")[:500] or None
        try:
            self.db.execute("INSERT INTO device_locked_accounts(instance_id, handle, profile_id, app_id, origin,"
                            " seen_by, evidence, since, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                            (instance_id, conta, pid, app, origem, visto_por, texto, agora, agora))
        except INTEGRITY_ERRORS:
            return False                   # outro chamador marcou no mesmo instante: o marcador existe
        if pid is not None and self._trava_a_persona(pid, app):
            perfil = self.profile_row(pid)
            # Persona SEM conta (29.23: a conta bloqueada já saiu, `username = ''`) não tem o que bloquear: a trava
            # vista depois da retirada não pode devolver `blocked` a quem acabou de voltar a `active`.
            if (perfil is not None and (perfil["status"] or "active") == "active"
                    and (dona is not None or perfil["username"])):
                self.mudar_status(pid, "blocked", origem=origem, autor=visto_por,
                                  evidencia=f"{instance_id}: {texto}" if texto else f"conta travada em {instance_id}")
        self._sincronizar_rotulo(instance_id)
        if self.on_locked_account is not None and (linha := self.conta_travada_no_aparelho(instance_id)) is not None:
            self.on_locked_account(linha, "marcado", visto_por)
        if pid is not None and self.on_conta_bloqueada is not None:
            # Por último: o marcador, o bloqueio e o aviso já saíram; a retirada só tira a conta de cena (29.23).
            self.on_conta_bloqueada(pid, app, conta, instance_id, texto, origem)
        return True

    def aparelhos_da_conta(self, profile_id: str, conta: Row, handles: Sequence[str | None]) -> list[AparelhoDaLimpeza]:
        """Os aparelhos onde a conta estava LOGADA, para a limpeza da retirada (29.27). Duas pistas, juntas:
        - o marcador de quarentena ABERTO de um dos `handles` da conta (o @ da conta e o do cadastro, na âncora), do app
          dela ou sem app: é o que prova a conta logada de verdade;
        - o vínculo ativo da persona que serve ao app da conta (o do app, ou o sem app da âncora).
        A SESSÃO não é pista: ela sobrevive ao desvínculo (`unbind` não a apaga) e `wrong_account`/`needs_person` dizem
        "outra conta aberta" ou "pessoa precisa agir", não "esta conta está aqui"; o aparelho de uma sessão velha
        podia ter passado a servir OUTRA persona viva, e o `pm clear` a apagaria. Perder um aparelho aqui é recuperável
        (a rota manual do 29.24); apagar conta viva não é. A trava de quem executa (`limpeza_ao_retirar`) ainda confere
        o aparelho, na hora, antes de limpar.
        Chamar ANTES da retirada: ela troca o @ do marcador por `MARCADOR` e apaga sessão e vínculo. Ordem estável
        (por aparelho). Só leitura."""
        achados: dict[str, tuple[list[int], list[str]]] = {}

        def _anotar(iid: str, origem: str, marcador: int | None = None) -> None:
            ids, origens = achados.setdefault(iid, ([], []))
            if marcador is not None:
                ids.append(marcador)
            if origem not in origens:
                origens.append(origem)

        alvos = sorted({normalizar_handle(h) for h in handles if h and normalizar_handle(h)})
        for m in self.contas_travadas_abertas():
            if m["handle"] in alvos and m["app_id"] in (None, conta["app_id"]):
                _anotar(str(m["instance_id"]), "marcador", int(m["id"]))
        for b in self.bindings_of_profile(profile_id):
            if b["app_id"] is None or b["app_id"] == conta["app_id"]:
                _anotar(str(b["instance_id"]), "vinculo")
        return [AparelhoDaLimpeza(instance_id=iid, marcadores=tuple(ids), origens=tuple(origens))
                for iid, (ids, origens) in sorted(achados.items())]

    def outra_conta_no_aparelho(self, instance_id: str, *, account_id: str, app_id: str, package: str,
                                marcadores_do_pedido: Sequence[int] = ()) -> str | None:
        """O que, neste aparelho, mostra que o app serve a OUTRA conta que não a retirada (`account_id`)? É a trava de
        quem vai dar `pm clear` (29.27): o `pm clear` apaga o app inteiro, e a plataforma só protege o que conhece.
        Devolve a pista (`vinculo`, `sessao` ou `marcador`) ou `None`. Lê o banco DE AGORA, depois da retirada, que já
        apagou as sessões e os vínculos da conta retirada em todos os aparelhos; o que sobra é de outra conta.
        - vínculo ativo que serve ao app (o do app, ou o sem app de persona com conta nele: `profiles_of_instance`);
        - sessão de outra conta do app, em QUALQUER status (até `unknown`: houve app aberto nela);
        - marcador aberto de conta que não é a do pedido, do app ou sem app."""
        apps = {app_id, package, *(str(x["id"]) for x in self.db.query("SELECT id FROM apps WHERE package=?", (package,)))}
        for app in sorted(apps):
            if self.profiles_of_instance(instance_id, app):
                return "vinculo"
        for app in sorted(apps):
            if self.db.one("SELECT 1 FROM account_sessions s JOIN profile_accounts a ON a.id=s.account_id"
                           " WHERE s.instance_id=? AND s.account_id<>? AND a.app_id=?",
                           (instance_id, account_id, app)) is not None:
                return "sessao"
        if any(int(m["id"]) not in marcadores_do_pedido and m["app_id"] in (None, *apps)
               for m in self.contas_travadas_abertas() if str(m["instance_id"]) == instance_id):
            return "marcador"
        return None

    def resolver_conta_travada(self, instance_id: str, *, por: str, nota: str | None = None,
                               handle: str | None = None, marcadores: Sequence[int] | None = None) -> int:
        """Uma pessoa decidiu o destino do aparelho (ou o disco foi apagado): o marcador aberto sai, e fica como
        história. Com `handle`, só o daquela conta; com `marcadores`, só os desses ids (a limpeza automática da
        retirada, 29.27, que não pode citar o @: o marcador já está mascarado). Devolve quantos foram resolvidos. O
        PERFIL não é reativado aqui — reativar é afirmar que a conta voltou a ser usável, e isso é decisão de pessoa na
        tela do perfil."""
        abertos = [m for m in self.db.query("SELECT * FROM device_locked_accounts WHERE instance_id=?"
                                            " AND resolved_at IS NULL ORDER BY id", (instance_id,))
                   if (handle is None or m["handle"] == normalizar_handle(handle))
                   and (marcadores is None or int(m["id"]) in marcadores)]
        for m in abertos:
            self.db.execute("UPDATE device_locked_accounts SET resolved_at=?, resolved_by=?, resolution=?"
                            " WHERE id=? AND resolved_at IS NULL", (now_iso(), por, (nota or "")[:500] or None,
                                                                    m["id"]))
        if abertos:
            self._sincronizar_rotulo(instance_id)
            if self.on_locked_account is not None:
                for m in abertos:
                    self.on_locked_account(m, "resolvido", por)
        return len(abertos)

    def _trava_a_persona(self, profile_id: str, app_id: str | None) -> bool:
        """A conta travada leva a PERSONA junto? Só a do app âncora (item 23.5, decisão do dono P9 de 29/09): a conta
        de outro app (o Outlook) travada põe o aparelho em quarentena e para só aquela conta — a persona e o
        Instagram dela seguem. Sem app sabido (achada pelo @ do cadastro, que é o da âncora) ou sem âncora
        configurada, vale o de sempre: bloqueia."""
        if app_id is None or self._pacote_da_conta_ancora() is None:
            return True
        pacote = self.db.scalar("SELECT package FROM apps WHERE id=?", (app_id,)) or app_id
        return self.eh_pacote_ancora(profile_id, str(pacote))

    def _dona_da_conta(self, conta: str, profile_id: str | None, app_id: str | None) -> Row | None:
        """`{profile_id, app_id}` da conta travada: pela conta do app com aquele @, senão pelo @ do cadastro."""
        filtros, params = "lower(handle)=?", [conta]
        if profile_id is not None:
            filtros += " AND profile_id=?"
            params.append(profile_id)
        if app_id is not None:
            filtros += " AND app_id=?"
            params.append(app_id)
        linha = self.db.one(f"SELECT profile_id, app_id FROM profile_accounts WHERE {filtros} ORDER BY created_at"
                            " LIMIT 1", tuple(params))
        if linha is not None or profile_id is not None:
            return linha
        perfil = self.profile_by_username(conta)
        return {"profile_id": perfil["id"], "app_id": None} if perfil is not None else None

    # ------------------------------------------------------------------ account_label derivado (054)
    # `instances.account_label` vinha só da configuração (`instances.accounts`) ou do que alguém digitou, e nunca
    # mais mudava: em 28/09 os quinze aparelhos diziam `qa-user-NN`, inclusive o android-04 com o felipe logado — e
    # um experimento acreditou nisso e tocou o desafio. Os leitores (planejamento, variável `{account_label}`,
    # cartão do aparelho) leem a COLUNA, então ela é mantida aqui a cada vínculo, desvínculo e marcador, e varrida
    # na partida (`sincronizar_rotulos`). O rótulo da configuração só é tocado quando há o que derivar.
    def _rotulo_derivado(self, instance_id: str) -> tuple[str | None, str | None]:
        """`(rótulo, origem)` da conta do app DO APARELHO (`instances.app_id`): a conta travada logada (marcador
        aberto) vence — é o que está lá de verdade —, depois a conta da persona vinculada. `(None, None)` = nada a
        derivar. Um vínculo de outro app (a conta do Chrome num aparelho de Instagram) não rotula o aparelho."""
        inst = self.db.one("SELECT app_id FROM instances WHERE id=?", (instance_id,))
        if inst is None or not inst["app_id"]:
            return None, None
        app = str(inst["app_id"])
        for m in self.db.query("SELECT handle, app_id FROM device_locked_accounts WHERE instance_id=?"
                               " AND resolved_at IS NULL ORDER BY id", (instance_id,)):
            if m["app_id"] in (None, app):
                return rotulo_da_conta(self.db, str(m["handle"])).lstrip("@"), "marcador"
        for v in self.profiles_of_instance(instance_id, app):
            conta = self.account_by_app(str(v["profile_id"]), app)
            if conta is not None and conta["handle"]:
                return str(conta["handle"]), "vinculo"
        return None, None

    def _sincronizar_rotulo(self, instance_id: str) -> None:
        """Grava em `instances` o rótulo derivado; sem nada a derivar, apaga só o que ERA derivado."""
        linha = self.db.one("SELECT account_label, account_label_origin FROM instances WHERE id=?", (instance_id,))
        if linha is None:
            return
        rotulo, origem = self._rotulo_derivado(instance_id)
        if rotulo is None:
            if linha["account_label_origin"] in _ROTULO_DERIVADO:
                self.db.execute("UPDATE instances SET account_label=NULL, account_label_origin=NULL WHERE id=?",
                                (instance_id,))
            return
        if (linha["account_label"], linha["account_label_origin"]) != (rotulo, origem):
            self.db.execute("UPDATE instances SET account_label=?, account_label_origin=? WHERE id=?",
                            (rotulo, origem, instance_id))

    def sincronizar_rotulos(self) -> None:
        """A varredura da partida: todo aparelho do parque com o rótulo derivado do que existe agora. Antes, o @ de
        conta já retirada sai dos dados que ficaram de antes da 29.24 (idempotente, sem migração)."""
        self.mascarar_contas_retiradas()
        for linha in self.db.query("SELECT id FROM instances WHERE retired_at IS NULL ORDER BY id"):
            self._sincronizar_rotulo(str(linha["id"]))

    def _sincronizar_rotulos_da_persona(self, profile_id: str) -> None:
        for iid in sorted({str(b["instance_id"]) for b in self.bindings_of_profile(profile_id)}):
            self._sincronizar_rotulo(iid)

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

    def teto_de_unknown(self, instance_id: str) -> int | None:
        """29.92: quantas reobservações seguidas em `unknown` a porta aceita neste aparelho antes de parar e chamar uma
        pessoa. Aparelho com vínculo ativo (conta real, `shared.vinculos`) tem teto 1: a rodada seguinte reabre o app
        e, se cair na tela de login, digita a senha guardada (`_login(automatic=True)`) — em cima de uma tela que
        ninguém reconheceu. Nos demais, o teto global (`session_unknown_retry_cap`). `None` sem teto configurado."""
        if tem_vinculo_ativo(self.db, instance_id):
            return 1
        return self.teto_de_reobservacao() if self.teto_de_reobservacao is not None else None

    def unknown_no_teto(self, sessao: Row | None, instance_id: str) -> bool:
        """29.92: a sessão gravada está em `unknown` no teto deste aparelho, isto é, parada esperando uma pessoa."""
        teto = self.teto_de_unknown(instance_id)
        return (sessao is not None and teto is not None and sessao["status"] == SessionStatus.unknown.value
                and int(sessao["unknown_streak"] or 0) >= teto)

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
        anterior = self.db.one("SELECT status, unknown_streak FROM account_sessions WHERE account_id=?"
                               " AND instance_id=?", (account_id, instance_id))
        estava_em_unknown = anterior is not None and anterior["status"] == SessionStatus.unknown.value
        if status is SessionStatus.unknown and reobserved:
            streak = int(anterior["unknown_streak"] or 0) + 1 if estava_em_unknown else 1
            if self.teto_de_reobservacao is not None:
                streak = min(streak, self.teto_de_reobservacao())
            # 29.92: a rodada do `unknown` vira dado (antes só o valor atual ficava, sobrescrito): quantas resolvem
            # na 1ª, 2ª e 3ª rodada decide o teto. Só métrica, sem coluna.
            metricas.contar("sessao.unknown_rodada", instancia=instance_id, rodada=streak)
        elif status is SessionStatus.session_ready and estava_em_unknown and int(anterior["unknown_streak"] or 0):
            metricas.contar("sessao.unknown_resolvida", instancia=instance_id,
                            rodada_antes=int(anterior["unknown_streak"] or 0))
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

    def invalidate_sessions_of_instance(self, instance_id: str, *, reason: str, package: str | None = None,
                                        todos_os_apps: bool = False) -> int:
        """Wipe, perda do aparelho ou atualização do app: a sessão daquele aparelho deixa de valer.

        Por app: sem `package`, as contas do app âncora (o comportamento de sempre para quem não diz o app); com
        ele, as contas daquele app. A marcação que a pessoa fez num app sem provedor só cai quando aquele app é o
        alvo. `todos_os_apps=True` é o disco do APARELHO que foi embora (reset, troca de máquina): a sessão de toda
        conta de todo app ali deixa de valer — antes só a da âncora caía, e a do segundo app seguia "Conectado"
        num disco que não existe mais (item 23.4).

        O motivo é reescrito mesmo numa sessão que já estava `unknown`. Sem isso, uma sequência de operações
        deixaria no painel a explicação da PRIMEIRA delas — "o app foi atualizado" continuaria aparecendo depois de
        o app ter sido desinstalado e reinstalado, que é justamente quando a pessoa precisa saber o que houve.

        O retorno conta só quem de fato mudou de estado: é o que decide se vale emitir um aviso.
        """
        if todos_os_apps:
            rows = self.db.query("SELECT s.account_id, s.status, a.profile_id FROM account_sessions s"
                                 " JOIN profile_accounts a ON a.id = s.account_id WHERE s.instance_id=?",
                                 (instance_id,))
            return self._invalidar(instance_id, rows, reason)
        pacote = package or self._pacote_da_conta_ancora()
        if pacote is None:
            return 0
        app_id = self.db.scalar("SELECT id FROM apps WHERE package=?", (pacote,)) or pacote
        rows = self.db.query("SELECT s.account_id, s.status, a.profile_id FROM account_sessions s"
                             " JOIN profile_accounts a ON a.id = s.account_id"
                             " WHERE s.instance_id=? AND a.app_id IN (?, ?)", (instance_id, app_id, pacote))
        return self._invalidar(instance_id, rows, reason)

    def _invalidar(self, instance_id: str, rows: list[Row], reason: str) -> int:
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
        """A tentativa é da CONTA (049). O motor de sessão sempre diz qual (23.4); sem `account_id`, a da conta
        âncora — o chamador antigo, que só conhecia o perfil."""
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
    def tem_avatar(self, profile_id: str) -> bool:
        """Há foto principal PRONTA: o que a rota do avatar serve (29.26). Sem ela a rota responde 404."""
        imagens = self.imagens_de(profile_id) if self.imagens_de is not None else []
        return any(i.is_primary and i.status == "ready" for i in imagens)

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
        tem_foto = any(i.is_primary and i.status == "ready" for i in imagens)
        return InstagramProfileDTO(
            **pessoa, visual=visual_da_linha(row),
            generation=PersonaGeneration.model_validate(loads(row["generation"], {}) or {}),
            display_name=row["display_name"], first_name=row["first_name"],
            last_name=row["last_name"], birth_date=row["birth_date"], email=row["email"],
            # A persona é a própria pessoa: o painel de hoje acha "a persona do perfil" por estes dois campos.
            persona_id=row["id"], persona_name=pessoa["name"], status=row["status"],
            accounts_count=self.accounts_count(profile_id), images=imagens, primary_image_id=principal, has_avatar=tem_foto,
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
                        app_id: str | None = None,
                        only_profile_ids: frozenset[str] | None = None) -> tuple[int, str | None]:
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
        # 30.62: `only_profile_ids` restringe a contagem às personas de UM pedido (a família do 28.10), que contam como
        # uma conta só. Conjunto vazio: ninguém mais na família, nada a contar.
        if only_profile_ids is not None and not (only_profile_ids - {exclude_profile_id}):
            return 0, None
        ids = tuple(sorted(only_profile_ids - {exclude_profile_id})) if only_profile_ids is not None else ()
        so_eles = f" AND profile_id IN ({','.join('?' * len(ids))})" if ids else ""
        t, s = ",".join("?" * len(types)), ",".join("?" * len(statuses))
        # Item 12.1: @nasa no Instagram e @nasa no TikTok são alvos diferentes — a coordenação é por (app, alvo).
        # Interação sem app (anterior à migração 037 foi toda preenchida) conta em qualquer app, por segurança.
        por_app = " AND (app_id=? OR app_id IS NULL)" if app_id else ""
        contas = {str(r["profile_id"]) for r in self.db.query(
            f"SELECT DISTINCT profile_id FROM social_interactions"
            f" WHERE counterparty=? AND profile_id<>? AND occurred_at>=? AND direction='outbound'"
            f" AND type IN ({t}) AND status IN ({s}){por_app}{so_eles}",
            (counterparty, exclude_profile_id, since, *types, *statuses, *((app_id,) if app_id else ()), *ids))}
        ultima = self.db.scalar(
            f"SELECT MAX(occurred_at) FROM social_interactions"
            f" WHERE counterparty=? AND profile_id<>? AND occurred_at>=? AND direction='outbound'"
            f" AND type IN ({t}) AND status IN ({s}){por_app}{so_eles}",
            (counterparty, exclude_profile_id, since, *types, *statuses, *((app_id,) if app_id else ()), *ids))
        # ADR-055: um pedido de aprovação ainda em aberto de outra conta RESERVA o alvo. Sem isto, duas execuções
        # quase juntas passavam as duas pela porta — nenhuma tinha disparado nada ainda — e a pessoa aprovava as duas.
        # O alvo do pedido é gravado cru (`@Ana`, `ana`) nos pedidos antigos: compara-se normalizado dos dois lados.
        # Os ids servem só para a união das duas fontes; daqui sai apenas a contagem.
        contas |= {str(r["profile_id"]) for r in self.db.query(
            "SELECT DISTINCT profile_id FROM pending_approvals WHERE profile_id IS NOT NULL AND profile_id<>?"
            " AND status IN ('pending','approved','edited') AND interaction_id IS NULL AND created_at>=?" + _DE_OBJETIVO_VIVO
            + _do_plano_em_vigor()
            + " AND lower(ltrim(trim(target), '@'))=?" + so_eles,
            (exclude_profile_id, since, counterparty.lower().lstrip("@"), *ids))}
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

    def ultima_saida_para(self, profile_id: str, counterparty: str, *, types: tuple[str, ...],
                          statuses: tuple[str, ...], since: str, app_id: str | None = None,
                          capability: str | None = None,
                          exclude_step_id: str | None = None) -> tuple[str, str] | None:
        """A interação de SAÍDA mais recente deste perfil para a contraparte, destes tipos e estados, desde `since`:
        `(id, occurred_at)`. Pergunta do próprio perfil (30.56); a da etapa `exclude_step_id` não conta.

        `capability`: só a gravada por uma etapa DESTA ação (dois efeitos gravam o mesmo tipo, como comentar e responder);
        a interação sem etapa conhecida conta, por segurança — não se sabe que não foi esta ação."""
        if not types or not statuses or not counterparty:
            return None
        t, s = ",".join("?" * len(types)), ",".join("?" * len(statuses))
        por_app = " AND (app_id=? OR app_id IS NULL)" if app_id else ""
        da_acao = (" AND (step_id IS NULL OR NOT EXISTS (SELECT 1 FROM steps e WHERE e.id=social_interactions.step_id)"
                   " OR EXISTS (SELECT 1 FROM steps e WHERE e.id=social_interactions.step_id AND e.capability=?))"
                   if capability else "")
        sem_a_etapa = " AND (step_id IS NULL OR step_id<>?)" if exclude_step_id else ""
        linha = self.db.one(
            f"SELECT id, occurred_at FROM social_interactions WHERE profile_id=? AND counterparty=?"
            f" AND direction='outbound' AND occurred_at>=? AND type IN ({t}) AND status IN ({s}){por_app}{da_acao}"
            f"{sem_a_etapa} ORDER BY occurred_at DESC, seq DESC LIMIT 1",
            (profile_id, counterparty, since, *types, *statuses, *((app_id,) if app_id else ()),
             *((capability,) if capability else ()), *((exclude_step_id,) if exclude_step_id else ())))
        return (str(linha["id"]), str(linha["occurred_at"])) if linha else None

    def pedido_em_aberto_para(self, profile_id: str, capability: str, counterparty: str, *, since: str,
                              exclude_step_id: str | None = None) -> str | None:
        """O pedido de aprovação deste perfil, desta ação e para esta contraparte, ainda sem interação (pendente ou
        aprovado e não executado), desde `since`; `None` sem ele. O alvo antigo é gravado cru: compara-se normalizado,
        como em `fleet_targeting`. O da etapa `exclude_step_id` não conta (30.56)."""
        if not counterparty:
            return None
        sem_a_etapa = " AND (step_id IS NULL OR step_id<>?)" if exclude_step_id else ""
        linha = self.db.one(
            "SELECT id FROM pending_approvals WHERE profile_id=? AND capability=?"
            " AND status IN ('pending','approved','edited') AND interaction_id IS NULL AND created_at>=?" + _DE_OBJETIVO_VIVO
            + _do_plano_em_vigor()
            + f" AND lower(ltrim(trim(target), '@'))=?{sem_a_etapa} ORDER BY created_at DESC, id DESC LIMIT 1",
            (profile_id, capability, since, counterparty.lower().lstrip("@"),
             *((exclude_step_id,) if exclude_step_id else ())))
        return str(linha["id"]) if linha else None

    def saidas_da_acao(self, profile_id: str, capability: str, *, types: tuple[str, ...], statuses: tuple[str, ...],
                       since: str, app_id: str | None = None,
                       exclude_step_id: str | None = None
                       ) -> list[tuple[str, str, str | None, dict[str, object] | None, str | None]]:
        """30.64: as interações de SAÍDA deste perfil, destes tipos e estados, desde `since`, com o que se sabe do objeto:
        `(id, occurred_at, counterparty, argumentos da etapa que a gravou | None, texto enviado | None)`, a mais
        recente primeiro. A de etapa
        de OUTRA ação não entra (comentar e responder gravam o mesmo tipo); a sem etapa conhecida entra com argumentos
        `None` e quem pergunta decide o que dá para dizer dela. A da etapa `exclude_step_id` não conta."""
        if not types or not statuses:
            return []
        t, s = ",".join("?" * len(types)), ",".join("?" * len(statuses))
        por_app = " AND (i.app_id=? OR i.app_id IS NULL)" if app_id else ""
        sem_a_etapa = " AND (i.step_id IS NULL OR i.step_id<>?)" if exclude_step_id else ""
        linhas = self.db.query(
            f"SELECT i.id, i.occurred_at, i.counterparty, i.outgoing_content, e.bindings, e.capability AS acao"
            f" FROM social_interactions i"
            f" LEFT JOIN steps e ON e.id=i.step_id WHERE i.profile_id=? AND i.direction='outbound' AND i.occurred_at>=?"
            f" AND i.type IN ({t}) AND i.status IN ({s}){por_app}{sem_a_etapa}"
            f" AND (e.id IS NULL OR e.capability=?) ORDER BY i.occurred_at DESC, i.seq DESC LIMIT 200",
            (profile_id, since, *types, *statuses, *((app_id,) if app_id else ()),
             *((exclude_step_id,) if exclude_step_id else ()), capability))
        saida = []
        for r in linhas:
            argumentos = loads(r["bindings"], None) if r["acao"] is not None else None
            saida.append((str(r["id"]), str(r["occurred_at"]), r["counterparty"],
                          argumentos if isinstance(argumentos, dict) else None, r["outgoing_content"]))
        return saida

    def pedidos_da_acao(self, profile_id: str, capability: str, *, since: str, app_id: str | None = None,
                        exclude_step_id: str | None = None, decidido_desde: str | None = None
                        ) -> list[tuple[str, str, str | None, dict[str, object] | None, str, str | None]]:
        """30.64: os pedidos de aprovação deste perfil e desta ação ainda sem interação (pendente, ou aprovado e não
        executado, de objetivo vivo), desde `since`: `(id, created_at, target, argumentos da etapa | None, status,
        texto que vai ser digitado | None)`, o mais recente primeiro. O da etapa `exclude_step_id` não conta (a porta roda de novo na retomada), nem o das versões
        anteriores DELA no mesmo objetivo (mesma chave de etapa): esse é o que `acompanhar_revisao` leva para a etapa
        revisada, e não um segundo pedido.

        `decidido_desde` (31.49): só o pedido decidido (ou, sem decisão, criado) a partir desse instante."""
        desde_a_decisao = " AND COALESCE(a.decided_at, a.created_at)>=?" if decidido_desde else ""
        sem_a_etapa = (" AND (a.step_id IS NULL OR (a.step_id<>? AND NOT EXISTS (SELECT 1 FROM steps x WHERE x.id=?"
                       " AND x.objective_id=a.objective_id AND x.key=e.key)))") if exclude_step_id else ""
        por_app = " AND (a.app_id=? OR a.app_id IS NULL)" if app_id else ""
        linhas = self.db.query(
            "SELECT a.id, a.created_at, a.target, a.status, a.generated_content, a.approved_content, e.bindings"
            " FROM pending_approvals a LEFT JOIN steps e ON e.id=a.step_id"
            " WHERE a.profile_id=? AND a.capability=? AND a.status IN ('pending','approved','edited')"
            " AND a.interaction_id IS NULL AND a.created_at>=? AND (a.objective_id IS NULL OR NOT EXISTS (SELECT 1"
            " FROM objectives o WHERE o.id=a.objective_id AND o.status IN ('succeeded','failed','cancelled')))"
            f"{_do_plano_em_vigor('a')}{desde_a_decisao}{por_app}{sem_a_etapa} ORDER BY a.created_at DESC, a.id DESC"
            " LIMIT 200",
            (profile_id, capability, since, *((decidido_desde,) if decidido_desde else ()), *((app_id,) if app_id else ()),
             *((exclude_step_id, exclude_step_id) if exclude_step_id else ())))
        saida = []
        for r in linhas:
            argumentos = loads(r["bindings"], None)
            texto = r["approved_content"] if r["status"] == "edited" and r["approved_content"] else r["generated_content"]
            saida.append((str(r["id"]), str(r["created_at"]), r["target"],
                          argumentos if isinstance(argumentos, dict) else None, str(r["status"]), texto))
        return saida

    def pedidos_em_aberto_desde(self, profile_id: str, since: str, *,
                                exclude_step_id: str | None = None) -> list[tuple[str, str]]:
        """`(capability, created_at)` dos pedidos de aprovação deste perfil ainda sem interação (pendente, ou aprovado e
        não executado) criados desde `since`. 30.57: o teto por hora conta também o que já está na fila do dono — sem
        isto, os itens de um `for_each` viravam todos pedido antes de qualquer um sair. O da etapa `exclude_step_id`
        não conta (a porta roda de novo na retomada)."""
        sem_a_etapa = " AND (step_id IS NULL OR step_id<>?)" if exclude_step_id else ""
        return [(str(r["capability"]), str(r["created_at"])) for r in self.db.query(
            "SELECT capability, created_at FROM pending_approvals WHERE profile_id=?"
            " AND status IN ('pending','approved','edited') AND interaction_id IS NULL AND created_at>=?" + _DE_OBJETIVO_VIVO
            + _do_plano_em_vigor()
            + f"{sem_a_etapa} ORDER BY created_at, id",
            (profile_id, since, *((exclude_step_id,) if exclude_step_id else ())))]

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
