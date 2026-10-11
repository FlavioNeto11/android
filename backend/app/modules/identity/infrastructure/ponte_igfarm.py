"""Os adaptadores da ponte android ⇄ igfarm: o SQL das três tabelas da migração 132 e as portas que ligam a aplicação
(`application/ponte_igfarm.py`) ao cadastro de contas, ao cofre, às imagens, à IA de texto e ao barramento."""
from __future__ import annotations

import json
import re
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING, cast

from pydantic import SecretStr

from app.db import Database, loads
from app.devices.rede import NetworkAssignBody, _parse_proxy, atribuir, criar_perfil_de_conta
from app.devices.rede import RedeError as RedeErrorDoDevice
from app.events import EventBus
from app.models import NetworkPolicy, NetworkProtocol
from app.modules.email_do_parque.application.servico import EmailDoParque
from app.modules.identity.application.persona_images import PersonaImageService
from app.modules.identity.application.ponte_igfarm import ErroDaPonte, PonteIgfarm, RedeError
from app.modules.identity.domain.persona_image import (GeracaoFalhou, GeracaoRecusada, MenorDeIdade,
                                                       OrcamentoEsgotado)
from app.modules.identity.domain.ponte_igfarm import (CicloDaConta, ContaRegistrada, ContatoDaConta, FichaDaPessoa,
                                                       ImagemDaPessoa, Sugestao, minutos_entre)
from app.modules.identity.infrastructure.persona_images import identidade_para_foto
from app.planning.catalog import pacote_ancora
from app.security.secret_store import SecretStoreLocked, SecretStoreUnavailable
from app.social.contas_nossas import eh_conta_nossa, foi_retirada, hash_do_handle
from app.social.service import SocialError, SocialService

if TYPE_CHECKING:
    from app.state import AppState


class ArmazemSql:
    def __init__(self, db: Database) -> None:
        self.db = db

    def tx(self) -> AbstractContextManager[object]:
        return self.db.tx()

    def candidatas(self, locale: str | None, agora: str) -> list[str]:
        sql = ("SELECT p.id FROM instagram_profiles p WHERE p.username = '' AND p.status = 'active'"
               " AND COALESCE(p.email, '') = ''"
               " AND NOT EXISTS (SELECT 1 FROM profile_accounts a WHERE a.profile_id = p.id)"
               " AND NOT EXISTS (SELECT 1 FROM instagram_credentials c WHERE c.profile_id = p.id)"
               " AND NOT EXISTS (SELECT 1 FROM contas_retiradas r WHERE r.profile_id = p.id)"
               " AND NOT EXISTS (SELECT 1 FROM persona_reservas v WHERE v.profile_id = p.id"
               " AND v.reservada_em IS NOT NULL AND v.expira_em > ?)")
        params: tuple[object, ...] = (agora,)
        if locale:
            sql += " AND lower(COALESCE(p.locale, '')) = lower(?)"
            params += (locale,)
        return [str(r["id"]) for r in self.db.query(sql + " ORDER BY p.created_at, p.id", params)]

    def sugestao(self, persona_id: str) -> Sugestao | None:
        r = self.db.one("SELECT * FROM persona_reservas WHERE profile_id=?", (persona_id,))
        if r is None:
            return None
        return Sugestao(persona_id=persona_id, email=str(r["email_sugerido"]), username=str(r["username_sugerido"]),
                        imagem_id=r["imagem_id"], reservada_em=r["reservada_em"], expira_em=r["expira_em"])

    def trocar_sugestao(self, persona_id: str, email: str, username: str, imagem_id: str | None, agora: str) -> bool:
        """Grava a sugestão. `ON CONFLICT DO NOTHING` (e não `except IntegrityError`, que abortaria a transação no
        PostgreSQL): a reserva vigente nunca é sobrescrita e o e-mail/@ de outra pessoa não passa."""
        with self.db.tx():
            self.db.execute("DELETE FROM persona_reservas WHERE profile_id=? AND (reservada_em IS NULL OR expira_em <= ?)",
                            (persona_id, agora))
            self.db.execute("INSERT INTO persona_reservas(profile_id, email_sugerido, username_sugerido, imagem_id,"
                            " criada_em) VALUES (?,?,?,?,?) ON CONFLICT DO NOTHING",
                            (persona_id, email, username, imagem_id, agora))
            atual = self.sugestao(persona_id)
        return atual is not None and atual.email == email and atual.username == username

    def _emails(self, excluir_persona: str) -> set[str]:
        e = excluir_persona
        linhas = self.db.query(
            "SELECT lower(email) AS e FROM instagram_profiles WHERE COALESCE(email,'') <> '' AND id <> ?"
            " UNION SELECT lower(c.login_identifier) FROM account_credentials c JOIN profile_accounts a"
            " ON a.id = c.account_id WHERE a.profile_id <> ?"
            " UNION SELECT lower(endereco) FROM caixas_email WHERE profile_id <> ?"
            " UNION SELECT lower(email_sugerido) FROM persona_reservas WHERE profile_id <> ?", (e, e, e, e))
        return {str(next(iter(r.values()))) for r in linhas}

    def emails_tomados(self, excluir_persona: str) -> set[str]:
        return self._emails(excluir_persona)

    def email_em_uso(self, email: str, excluir_persona: str) -> bool:
        return email.strip().lower() in self._emails(excluir_persona)

    def usernames_sugeridos(self, excluir_persona: str) -> set[str]:
        return {str(r["u"]) for r in self.db.query(
            "SELECT lower(username_sugerido) AS u FROM persona_reservas WHERE profile_id <> ?", (excluir_persona,))}

    def reservar(self, persona_id: str, agora: str, expira_em: str) -> bool:
        cur = self.db.execute("UPDATE persona_reservas SET reservada_em=?, expira_em=? WHERE profile_id=?"
                              " AND (reservada_em IS NULL OR expira_em <= ?)", (agora, expira_em, persona_id, agora))
        return bool(cur.rowcount == 1)

    def guardar_imagem(self, persona_id: str, imagem_id: str) -> None:
        self.db.execute("UPDATE persona_reservas SET imagem_id=? WHERE profile_id=?", (imagem_id, persona_id))

    def apagar_sugestao(self, persona_id: str) -> None:
        self.db.execute("DELETE FROM persona_reservas WHERE profile_id=?", (persona_id,))

    def conta_igfarm(self, persona_id: str, username: str) -> ContaRegistrada | None:
        r = self.db.one(
            "SELECT g.*, c.endereco FROM contas_igfarm g LEFT JOIN caixas_email c ON c.account_id = g.account_id"
            " WHERE g.profile_id=? AND lower(g.username_registrado)=lower(?)", (persona_id, username))
        if r is None:
            return None
        return ContaRegistrada(persona_id=persona_id, account_id=str(r["account_id"]),
                               igfarm_account_id=str(r["igfarm_account_id"]), email=str(r["endereco"] or ""),
                               instagram_username=str(r["username_registrado"]), criada_em=str(r["criada_em_igfarm"]),
                               registrada_em=str(r["registrada_em"]), idempotente=True,
                               proxy_secret_ref=r.get("proxy_secret_ref"))

    def gravar_caixa(self, *, account_id: str, persona_id: str, endereco: str, dominio: str, secret_ref: str,
                     key_id: str, agora: str) -> None:
        self.db.execute("INSERT INTO caixas_email(account_id, profile_id, endereco, dominio, secret_ref, key_id,"
                        " criada_em) VALUES (?,?,?,?,?,?,?)",
                        (account_id, persona_id, endereco.lower(), dominio, secret_ref, key_id, agora))

    def gravar_conta_igfarm(self, *, account_id: str, persona_id: str, igfarm_account_id: str, username: str,
                            criada_em: str, agora: str) -> None:
        self.db.execute("INSERT INTO contas_igfarm(account_id, profile_id, igfarm_account_id, username_registrado,"
                        " criada_em_igfarm, registrada_em) VALUES (?,?,?,?,?,?)",
                        (account_id, persona_id, igfarm_account_id, username.lower(), criada_em, agora))
        # ADR-088 (emenda) × ADR-087: a conta da ponte é real desde o nascimento (o igfarm já a criou no provedor), então
        # nasce `confirmada`, com o desejado igual ao confirmado e a origem como evidência.
        self.db.execute("UPDATE profile_accounts SET provisioning_state='confirmada', desired_handle=?, confirmed_at=?,"
                        " confirmation_evidence=? WHERE id=? AND profile_id=?",
                        (username, criada_em, json.dumps({"kind": "igfarm", "ref": igfarm_account_id}), account_id,
                         persona_id))

    def gravar_egresso(self, account_id: str, proxy_secret_ref: str | None, proxy_key_id: str | None,
                       ip_criacao: str | None) -> None:
        self.db.execute("UPDATE contas_igfarm SET proxy_secret_ref=?, proxy_key_id=?, ip_criacao=?"
                        " WHERE account_id=? AND proxy_secret_ref IS NULL",
                        (proxy_secret_ref, proxy_key_id, ip_criacao, account_id))

    def _instance_ids_da_persona(self, persona_id: str) -> list[str]:
        return [str(r["instance_id"]) for r in self.db.query(
            "SELECT DISTINCT b.instance_id FROM device_profile_bindings b "
            "LEFT JOIN apps a ON a.id = b.app_id "
            "WHERE b.profile_id=? AND b.active=1 "
            "AND (b.app_id IS NULL OR a.package=?) "
            "ORDER BY b.instance_id", (persona_id, pacote_ancora()))]

    def perfis_vinculados(self, instance_id: str) -> list[str]:
        """As personas com vínculo ativo no aparelho (qualquer app): conta vinculada é conta real logada (ADR-055)."""
        return [str(r["profile_id"]) for r in self.db.query(
            "SELECT DISTINCT profile_id FROM device_profile_bindings WHERE instance_id=? AND active=1 "
            "ORDER BY profile_id", (instance_id,))]

    def endereco_da_conta(self, conta_id: str) -> str | None:
        r = self.db.one("SELECT endereco FROM caixas_email WHERE account_id=? OR account_id IN"
                        " (SELECT account_id FROM contas_igfarm WHERE igfarm_account_id=?)", (conta_id, conta_id))
        return str(r["endereco"]) if r is not None else None

    def ids_da_conta_igfarm(self, conta_id: str) -> tuple[str, str, str] | None:
        g = self.db.one("SELECT profile_id, account_id, igfarm_account_id FROM contas_igfarm"
                        " WHERE account_id=? OR igfarm_account_id=?", (conta_id, conta_id))
        if g is None:
            return None
        return str(g["profile_id"]), str(g["account_id"]), str(g["igfarm_account_id"])

    def ciclo_da_conta(self, conta_id: str) -> CicloDaConta | None:
        g = self.db.one("SELECT * FROM contas_igfarm WHERE account_id=? OR igfarm_account_id=?", (conta_id, conta_id))
        if g is None:
            return self._ciclo_da_conta_do_app(conta_id)
        criada = str(g["criada_em_igfarm"])
        username = str(g["username_registrado"])
        # A tentativa é da conta e sobrevive à retirada dela (a lápide guarda só o hash do @).
        contatos = tuple(
            ContatoDaConta(iniciado_em=str(t["started_at"]),
                           minutos_desde_a_criacao=minutos_entre(criada, t["started_at"]),
                           desfecho=str(t["outcome"] or "sem_desfecho"), etapa=str(t["stage"] or ""),
                           detalhe=_sem_email(str(t["detail"] or ""))[:200])
            for t in self.db.query("SELECT started_at, outcome, stage, detail FROM authentication_attempts"
                                   " WHERE account_id=? ORDER BY started_at, id", (g["account_id"],)))
        lapide = self.db.one("SELECT retirada_em FROM contas_retiradas WHERE handle_sha256=?",
                             (hash_do_handle(username),))
        return CicloDaConta(
            account_id=str(g["account_id"]), igfarm_account_id=str(g["igfarm_account_id"]),
            criada_em=criada, registrada_em=str(g["registrada_em"]),
            estado="retirada" if lapide is not None else "ativa",
            retirada_em=str(lapide["retirada_em"]) if lapide is not None else None, contatos=contatos,
            minutos_ate_o_primeiro_contato=contatos[0].minutos_desde_a_criacao if contatos else None,
            ultimo_desfecho=contatos[-1].desfecho if contatos else None)

    def _ciclo_da_conta_do_app(self, conta_id: str) -> CicloDaConta | None:
        """31.341: o ciclo da conta que NÃO veio do igfarm (planejada e cadastrada no app). As fontes são o que já existe: a linha da
        conta (planejamento e confirmação), as tentativas de login, os eventos do cadastro guiado (`identity.cadastro`) e a
        retirada (`profile.account_retired`, que sobrevive à conta). Conta retirada some de `profile_accounts` e ainda tem ciclo."""
        conta = self.db.one("SELECT id, created_at, provisioning_state, confirmed_at FROM profile_accounts WHERE id=?", (conta_id,))
        marca = f'%"account_id":"{conta_id}"%'
        cadastros = self.db.query("SELECT ts, data FROM events WHERE kind='identity.cadastro' AND data LIKE ? ORDER BY id", (marca,))
        retirada = self.db.one("SELECT ts FROM events WHERE kind='profile.account_retired' AND data LIKE ? ORDER BY id LIMIT 1",
                               (marca,))
        tentativas = self.db.query("SELECT started_at, outcome, stage, detail FROM authentication_attempts WHERE account_id=?"
                                   " ORDER BY started_at, id", (conta_id,))
        if conta is None and not (cadastros or tentativas or retirada):
            return None
        confirmada_em: str | None = None
        if conta is not None and conta["confirmed_at"]:
            confirmada_em = str(conta["confirmed_at"])
        else:
            for c in cadastros:
                if '"resultado":"confirmada"' in str(c["data"]):
                    confirmada_em = str(c["ts"])
                    break
        planejada_em = str(conta["created_at"]) if conta is not None and conta["created_at"] else None
        base = confirmada_em or planejada_em
        brutos: list[tuple[str, ContatoDaConta]] = []
        for t in tentativas:
            quando = str(t["started_at"])
            brutos.append((quando, ContatoDaConta(
                iniciado_em=quando, minutos_desde_a_criacao=minutos_entre(base, quando),
                desfecho=str(t["outcome"] or "sem_desfecho"), etapa=str(t["stage"] or ""),
                detalhe=_sem_email(str(t["detail"] or ""))[:200])))
        for c in cadastros:
            quando = str(c["ts"])
            dado = loads(c["data"], {}) or {}
            brutos.append((quando, ContatoDaConta(
                iniciado_em=quando, minutos_desde_a_criacao=minutos_entre(base, quando),
                desfecho=str(dado.get("resultado") or "sem_desfecho"), etapa="cadastro",
                detalhe=_sem_email(str(dado.get("motivo") or ""))[:200])))
        contatos = tuple(c for _, c in sorted(brutos, key=lambda x: x[0]))
        return CicloDaConta(
            account_id=conta_id, igfarm_account_id=None, criada_em=confirmada_em, registrada_em=planejada_em,
            estado="retirada" if retirada is not None else "ativa",
            retirada_em=str(retirada["ts"]) if retirada is not None else None, contatos=contatos,
            minutos_ate_o_primeiro_contato=contatos[0].minutos_desde_a_criacao if contatos else None,
            ultimo_desfecho=contatos[-1].desfecho if contatos else None, origem="app",
            referencia="criacao" if confirmada_em else "planejamento")


_EMAIL = re.compile(r"\S*@\S+")


def _sem_email(texto: str) -> str:
    """O ciclo nunca mostra e-mail, nem o mascarado que o motor de sessão põe no motivo (31.332)."""
    return _EMAIL.sub("<e-mail omitido>", texto)


def _erro(exc: SocialError) -> ErroDaPonte:
    return ErroDaPonte(exc.code, exc.message, exc.status)


class PessoasSocial:
    def __init__(self, social: SocialService) -> None:
        self.social = social

    def ficha(self, persona_id: str) -> FichaDaPessoa | None:
        try:
            dto = self.social.get_persona(persona_id)
        except SocialError:
            return None
        bio = dto.biography
        return FichaDaPessoa(
            persona_id=dto.id, nome=dto.name, primeiro_nome=dto.first_name or dto.name.split(" ")[0],
            sobrenome=dto.last_name or "", nome_exibicao=dto.display_name or dto.name, birth_date=dto.birth_date,
            idade=dto.age, genero=dto.gender, biografia=bio.model_dump(mode="json", exclude_none=True),
            visual=dto.visual.model_dump(mode="json", exclude_none=True), resumo=dto.summary,
            profissao=bio.work.profession, cidade=bio.home.city, interesses=tuple(dto.traits.interests[:5]))


class TextosSocial:
    def __init__(self, social: SocialService) -> None:
        self.social = social

    async def username(self, system: str, pedido: str) -> str:
        try:
            return await self.social.gerar_texto_curto(system, pedido)
        except SocialError as exc:
            raise _erro(exc) from None


class ImagensSocial:
    def __init__(self, servico: PersonaImageService, social: SocialService) -> None:
        self.servico = servico
        self.social = social

    def existente(self, persona_id: str, preferida: str | None) -> ImagemDaPessoa | None:
        prontas = [r for r in self.servico.listar(persona_id) if r.status == "ready"]
        escolhida = (next((r for r in prontas if r.id == preferida), None)
                     or next((r for r in prontas if r.is_primary), None) or (prontas[0] if prontas else None))
        return self._dto(escolhida.persona_id, escolhida.id) if escolhida else None

    @staticmethod
    def _dto(persona_id: str, image_id: str) -> ImagemDaPessoa:
        return ImagemDaPessoa(id=image_id, url=f"/api/personas/{persona_id}/images/{image_id}")

    def conferir(self) -> None:
        gerador = self.servico.generator
        if not gerador.configured:
            raise ErroDaPonte("image_not_configured", f"O provedor de imagem '{gerador.name}' não tem chave configurada "
                              "(OPENAI_API_KEY no .env) — ou use ai.image.provider: simulated.", 409)
        try:
            self.servico.conferir_orcamento()
        except OrcamentoEsgotado as exc:
            raise ErroDaPonte("ai_budget", str(exc), 409) from None

    async def gerar(self, persona_id: str) -> ImagemDaPessoa:
        try:
            dto = self.social.get_persona(persona_id)
            registros = await self.servico.gerar(persona_id, identidade_para_foto(dto), count=1)
        except SocialError as exc:
            raise _erro(exc) from None
        except OrcamentoEsgotado as exc:
            raise ErroDaPonte("ai_budget", str(exc), 409) from None
        except MenorDeIdade as exc:
            raise ErroDaPonte("persona_minor", str(exc), 409) from None
        except (GeracaoFalhou, GeracaoRecusada) as exc:
            raise ErroDaPonte("image_failed", str(exc), 502) from None
        pronta = next((r for r in registros if r.status == "ready"), None)
        if pronta is None:
            motivo = registros[-1].error if registros else None
            raise ErroDaPonte("image_failed", f"A imagem não ficou pronta: {motivo or 'sem detalhe'}", 502)
        return self._dto(persona_id, pronta.id)


class ContasSocial:
    def __init__(self, social: SocialService) -> None:
        self.social = social

    def eh_nossa(self, handle: str) -> bool:
        return eh_conta_nossa(self.social.repo.db, handle)

    def foi_retirada(self, handle: str) -> bool:
        return foi_retirada(self.social.repo.db, handle)

    def registrar(self, persona_id: str, *, username: str, email: str, senha: str, por: str) -> str:
        try:
            return self.social.registrar_conta_externa(persona_id, username=username, email=email,
                                                       password=SecretStr(senha), by=por)
        except SocialError as exc:
            raise _erro(exc) from None

    def consentir(self, persona_id: str, account_id: str, *, por: str) -> tuple[str | None, str | None, bool]:
        """(consent_at, consent_by, já_consentida). Não regrava o consentimento que a conta já tem."""
        try:
            antes = self.social.get_account(persona_id, account_id)
            if antes.consent_at:
                return antes.consent_at, antes.credential.consent_by, True
            depois = self.social.consent_account_credential(persona_id, account_id, by=por)
        except SocialError as exc:
            raise _erro(exc) from None
        return depois.consent_at, depois.credential.consent_by, False


class CofreSocial:
    def __init__(self, social: SocialService) -> None:
        self.secrets = social.secrets

    def guardar(self, valor: str) -> tuple[str, str]:
        if self.secrets.status() != "ready":
            raise ErroDaPonte("secret_store_unavailable", "O cofre de segredos não está disponível.", 503)
        try:
            ref = self.secrets.store_secret(valor)
        except (SecretStoreLocked, SecretStoreUnavailable) as exc:
            raise ErroDaPonte("secret_store_unavailable", str(exc), 503) from None
        return ref, self.secrets.provider.key_id

    def apagar(self, ref: str) -> None:
        self.secrets.delete_secret(ref)


class BarramentoSocial:
    def __init__(self, bus: EventBus) -> None:
        self.bus = bus

    def emitir(self, tipo: str, mensagem: str, dados: dict[str, object]) -> None:
        self.bus.emit(tipo, mensagem, data=dados)


def compor_ponte_igfarm(s: AppState) -> PonteIgfarm:
    """A ponte montada sobre o estado. Barata (só objetos finos sobre os serviços): a rota monta uma por chamada."""
    email: EmailDoParque = s.email_parque
    return PonteIgfarm(
        armazem=ArmazemSql(s.db), pessoas=PessoasSocial(s.social), textos=TextosSocial(s.social),
        imagens=ImagensSocial(s.persona_images, s.social), contas=ContasSocial(s.social), cofre=CofreSocial(s.social),
        email=email, barramento=BarramentoSocial(s.bus), rede=RedeSocial(s),
        criacao_pela_api=bool(s.cfg.file.contas.criacao_pela_api_do_igfarm))


class RedeSocial:
    """Adaptador da ponte para o subsistema de rede."""

    def __init__(self, st: AppState) -> None:
        self.st = st

    def parse_proxy(self, proxy_url: str) -> tuple[str, str, int, str | None, str | None]:
        return _parse_proxy(proxy_url)

    def criar_perfil_de_conta(self, account_id: str, *, host: str, port: int, protocol: str, username: str | None,
                              secret: SecretStr | None, ip_criacao: str | None, quem: str | None) -> str:
        return criar_perfil_de_conta(
            self.st, account_id, host=host, port=port, protocol=cast(NetworkProtocol, protocol),
            username=username, secret=secret, ip_criacao=ip_criacao, quem=quem)

    def pedido_atual(self, instance_id: str) -> tuple[str | None, str] | None:
        """(perfil de proxy, política) hoje pedidos ao aparelho, ou `None` se ele não tem linha de rede."""
        r = self.st.db.one("SELECT proxy_profile_id, policy FROM device_network WHERE instance_id=?", (instance_id,))
        if r is None:
            return None
        return (str(r["proxy_profile_id"]) if r["proxy_profile_id"] else None, str(r["policy"]))

    def atribuir(self, instance_ids: list[str], proxy_profile_id: str | None, policy: str, quem: str | None,
                 confirm_real_account: list[str] | None = None) -> dict[str, object]:
        try:
            return atribuir(
                self.st, NetworkAssignBody(
                    instance_ids=instance_ids, proxy_profile_id=proxy_profile_id,
                    policy=cast(NetworkPolicy, policy),
                    confirm_real_account=confirm_real_account or []), quem=quem)
        except RedeErrorDoDevice as exc:
            raise RedeError(exc.status, exc.code, exc.message) from None
