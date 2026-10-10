"""A conta planejada de uma persona (31.281, ADR-087, adendo v1.132): planejar, preparar a credencial e andar o ciclo.

Uma conta pode existir só como PLANO: o endereço desejado, a senha já no cofre e o cadastro no provedor por fazer. Aqui
fica o que o serviço de contas faz com isso; a máquina de estados é de domínio (`identity/domain/provisionamento.py`) e o
cofre, a gravação da senha e o clone são os do `SocialService`, que este módulo reaproveita em vez de duplicar.

Regras que não mudam:
- A senha nunca volta por API, log nem evento. Gerar é `secrets` no servidor, sem IA; digitar e reutilizar usam o mesmo
  caminho de `PUT …/credential` e `…/credential/clone`.
- Gerar ou digitar na tela com a caixa de autorização marcada vale como o consentimento do ADR-040 para ESTA conta.
- Conta que não está `confirmada` não é conta real logada: os consumidores a filtram (`CONTA_CONFIRMADA`).
- A conta só vira `confirmada` com evidência: sessão observada com o usuário igual ao desejado, ou a marcação nominal da
  pessoa, gravada como `declarada`. O cadastro no provedor (CAPTCHA, código, e-mail) é da pessoa (ADR-009).
"""
from __future__ import annotations

import json
import re
import unicodedata
from typing import TYPE_CHECKING

from pydantic import SecretStr

from ..db import Row
from ..models import (CredentialPrepare, PlannedAccountCreate, ProfileAccountDTO, ProvisioningEventBody,
                      ProvisioningInfo)
from ..modules.identity.domain import provisionamento as prov
from ..modules.identity.domain.cadastro import proximo_passo
from ..modules.identity.domain.provisionamento import Estado, Evento
from ..security.gerador_de_senha import TAMANHO_PADRAO, gerar_senha
from ..security.redaction import redact
from ..util import now_iso
from .erros import SocialError, normalizar_host

if TYPE_CHECKING:
    from .repository import SocialRepository
    from .service import SocialService

#: Predicado SQL de "conta real": toda linha anterior ao ciclo é `confirmada` (DEFAULT da migração 131).
CONTA_CONFIRMADA = "provisioning_state = 'confirmada'"

_ENDERECO = re.compile(r"^[A-Za-z0-9._@+\-]{3,200}$")
DETALHE_MAX = 300
MAX_SUGESTOES = 8


def estado_da_linha(linha: Row | None) -> Estado:
    """O estado de provisionamento de uma linha de `profile_accounts` (sem linha ou coluna ausente: confirmada)."""
    if linha is None:
        return Estado.CONFIRMADA
    try:
        return Estado(linha["provisioning_state"] or Estado.CONFIRMADA.value)
    except (KeyError, ValueError):
        return Estado.CONFIRMADA


def _slug(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]", "", sem_acento.lower())


class ProvisionamentoDeContas:
    def __init__(self, social: "SocialService"):
        self.social = social

    @property
    def repo(self) -> "SocialRepository":
        return self.social.repo

    # ------------------------------------------------------------------ leitura (o DTO)
    def info(self, linha: Row, *, sessao_pronta: bool) -> ProvisioningInfo:
        estado = estado_da_linha(linha)
        evidencia = None
        if linha["confirmation_evidence"]:
            try:
                bruto = json.loads(linha["confirmation_evidence"])
                evidencia = {str(k): str(v) for k, v in bruto.items()} if isinstance(bruto, dict) else None
            except ValueError:
                evidencia = None
        resume = linha["resume_state"] if estado is Estado.FALHA else None
        return ProvisioningInfo(
            state=estado.value, desired_handle=linha["desired_handle"], detail=linha["provisioning_detail"],
            resume_state=resume, confirmed_at=linha["confirmed_at"], evidence=evidencia,
            actions=prov.eventos_aceitos(estado), authenticated=estado is Estado.CONFIRMADA and sessao_pronta,
            proximo_passo=proximo_passo(estado.value, linha["provisioning_detail"]))

    # ------------------------------------------------------------------ planejar e sugerir
    def planejar(self, profile_id: str, body: PlannedAccountCreate, *, by: str) -> tuple[ProfileAccountDTO, bool]:
        """`POST …/accounts/planned`. Devolve a conta e se ela nasceu agora (201) ou já existia (200, idempotente)."""
        social = self.social
        social.get_profile(profile_id)
        app = social._check_app(body.app_id)
        host = normalizar_host(body.host)
        desejado = self._endereco(body.desired_handle)
        existente = self.repo.account_by_app(profile_id, app["id"], host)
        if existente is not None:
            if estado_da_linha(existente) is Estado.CONFIRMADA:
                onde = f"{app['name']} ({host})" if host else app["name"]
                raise SocialError("duplicate_account", f"Este perfil já tem uma conta em {onde}.", 409)
            if desejado is not None and desejado != (existente["desired_handle"] or ""):
                self._editar_desejado(profile_id, existente, desejado)
            return social.get_account(profile_id, existente["id"]), False
        social._recusar_conta_que_quebra_d2a(profile_id, str(app["id"]))
        account_id = self.repo.create_planned_account(profile_id, app_id=app["id"], host=host, desired_handle=desejado)
        self._emitir(profile_id, account_id, app["id"], "planejar", None, Estado.PLANEJADA)
        return social.get_account(profile_id, account_id), True

    def _endereco(self, valor: str | None) -> str | None:
        if valor is None:
            return None
        limpo = valor.strip()
        if limpo.startswith("@") and "@" not in limpo[1:]:
            limpo = limpo[1:]
        if not limpo:
            return None
        if not _ENDERECO.match(limpo):
            raise SocialError("desired_handle_invalido",
                              "Use de 3 a 200 caracteres: letras, números, ponto, sublinhado, hífen, + e @.", 422)
        return limpo

    def editar_desejado(self, profile_id: str, account_id: str, valor: str | None) -> None:
        """`PATCH …/accounts/{id}` com `desired_handle`: só antes de `confirmada`."""
        linha = self.repo.account_row(profile_id, account_id)
        assert linha is not None
        self._editar_desejado(profile_id, linha, self._endereco(valor))

    def _editar_desejado(self, profile_id: str, linha: Row, desejado: str | None) -> None:
        if estado_da_linha(linha) is Estado.CONFIRMADA:
            raise SocialError("conta_confirmada", "A conta já está confirmada: o endereço confirmado não se edita.", 409)
        self.repo.update_account(profile_id, linha["id"], {"desired_handle": desejado})

    def sugestoes(self, profile_id: str, app_id: str) -> list[dict[str, str]]:
        """Endereços sugeridos pelos dados da persona: local, determinístico, sem IA e sem rede. Não afirma que o
        endereço está livre no provedor; só tira os que já são de outra conta deste app aqui."""
        social = self.social
        perfil = social.get_profile(profile_id)
        app = social._check_app(app_id)
        linha = self.repo.profile_row(profile_id)
        primeiro = _slug((linha["first_name"] if linha is not None and linha["first_name"] else "") or "")
        sobrenome = _slug((linha["last_name"] if linha is not None and linha["last_name"] else "") or "")
        if not primeiro and linha is not None and linha["display_name"]:
            partes = str(linha["display_name"]).split()
            primeiro, sobrenome = _slug(partes[0]), _slug(partes[-1]) if len(partes) > 1 else ""
        nascimento = str(linha["birth_date"]) if linha is not None and linha["birth_date"] else ""
        ano = nascimento[:4] if re.match(r"^\d{4}", nascimento) else ""
        candidatos: list[tuple[str, str]] = []
        if primeiro and sobrenome:
            candidatos += [(f"{primeiro}.{sobrenome}", "persona_nome"), (f"{primeiro}{sobrenome}", "persona_nome"),
                           (f"{primeiro}_{sobrenome}", "persona_nome"), (f"{sobrenome}.{primeiro}", "persona_nome"),
                           (f"{primeiro[0]}{sobrenome}", "persona_nome")]
            if ano:
                candidatos += [(f"{primeiro}.{sobrenome}{ano[2:]}", "persona_dados"),
                               (f"{primeiro}{sobrenome}{ano}", "persona_dados")]
        elif primeiro:
            candidatos += [(primeiro, "persona_nome")] + ([(f"{primeiro}{ano}", "persona_dados")] if ano else [])
        usuario = _slug(str(getattr(perfil, "username", "") or ""))
        if usuario:
            candidatos.append((usuario, "persona_dados"))
        base = candidatos[0][0] if candidatos else (usuario or "")
        if base:
            candidatos += [(f"{base}{n}", "alternativa") for n in (1, 2, 3, 7, 21)]
        saida: list[dict[str, str]] = []
        vistos: set[str] = set()
        for handle, origem in candidatos:
            if len(handle) < 3 or handle in vistos:
                continue
            vistos.add(handle)
            if self.repo.handle_ocupado(app["id"], handle):
                continue
            saida.append({"handle": handle, "source": origem})
            if len(saida) >= MAX_SUGESTOES:
                break
        return saida

    # ------------------------------------------------------------------ credencial
    def preparar_credencial(self, profile_id: str, account_id: str, body: CredentialPrepare, *,
                            by: str) -> ProfileAccountDTO:
        social = self.social
        social.get_account(profile_id, account_id)
        conta = self.repo.account_row(profile_id, account_id)
        assert conta is not None
        estado = estado_da_linha(conta)
        if estado not in prov.ACEITAM_CREDENCIAL:
            raise SocialError("estado_nao_permite_credencial",
                              f"A conta está em '{estado.value}': a senha só se prepara ou troca antes do cadastro "
                              "no provedor (depois de confirmada, use a troca de senha da conta).", 409)
        # Os campos do modo, conferidos ANTES de qualquer efeito, pelo serviço (o 422 do validador devolveria o corpo).
        if body.modo == "gerar" and (body.password is not None or body.clonar_de):
            raise SocialError("credencial_modo_invalido", "O modo 'gerar' não leva senha nem conta de origem.", 422)
        if body.modo == "digitar" and (body.password is None or body.clonar_de):
            raise SocialError("credencial_modo_invalido", "O modo 'digitar' pede a senha e não leva conta de origem.", 422)
        if body.modo == "reutilizar" and (not body.clonar_de or body.password is not None):
            raise SocialError("credencial_modo_invalido",
                              "O modo 'reutilizar' pede a conta de origem (clonar_de) e não leva senha.", 422)
        if not body.consent:
            app = social._app_row(conta["app_id"])
            onde = (app["name"] if app else conta["app_id"]) + (f" ({conta['host']})" if conta["host"] else "")
            raise SocialError("consentimento_de_credencial",
                              f"Preparar esta senha autoriza a automação a DIGITÁ-LA na tela de {onde} — e só nela — "
                              "pelo canal sensível, sem que a IA veja o valor. Confirme com consent=true.", 409)
        if estado is Estado.CREDENCIAL_PREPARADA and not body.substituir:
            raise SocialError("credencial_ja_preparada",
                              "Esta conta já tem a senha preparada. Para trocá-la antes do cadastro, mande "
                              "substituir=true.", 409)
        if body.modo == "reutilizar":
            assert body.clonar_de is not None
            social._origem_do_clone(profile_id, body.clonar_de, destino_id=account_id)
        if social.secrets.status() != "ready":
            raise SocialError("secret_store_unavailable", social._vault_message(), 503)
        login = body.login_identifier or conta["desired_handle"] or None
        if body.modo == "gerar":
            senha = SecretStr(gerar_senha(body.tamanho or TAMANHO_PADRAO))
            social._gravar_credencial(profile_id, conta, password=senha, login_identifier=login, consent=True, by=by)
        elif body.modo == "digitar":
            assert body.password is not None
            social._gravar_credencial(profile_id, conta, password=body.password, login_identifier=login,
                                      consent=True, by=by)
        else:
            assert body.clonar_de is not None
            social._clonar_credencial(profile_id, conta, body.clonar_de, login_identifier=login, by=by)
            self.repo.consent_account_credential(profile_id, account_id, consent_by=by)
        self.credencial_gravada(profile_id, account_id, modo=body.modo)
        return social.get_account(profile_id, account_id)

    def credencial_gravada(self, profile_id: str, account_id: str, *, modo: str = "digitar") -> None:
        """A senha de uma conta planejada foi gravada (por qualquer rota): `planejada` passa a `credencial_preparada`."""
        linha = self.repo.account_row(profile_id, account_id)
        if linha is None or estado_da_linha(linha) is not Estado.PLANEJADA:
            return
        if self.repo.set_provisioning(profile_id, account_id, de=Estado.PLANEJADA.value,
                                      para=Estado.CREDENCIAL_PREPARADA.value, resume_state=None, detail=None):
            self._emitir(profile_id, account_id, linha["app_id"], f"credencial_{modo}", Estado.PLANEJADA,
                         Estado.CREDENCIAL_PREPARADA)

    def antes_de_apagar_credencial(self, profile_id: str, account_id: str) -> None:
        """A senha vai ser apagada: com o cadastro já em andamento isso deixaria a conta sem o que ditar."""
        linha = self.repo.account_row(profile_id, account_id)
        estado = estado_da_linha(linha)
        if estado in (Estado.AGUARDANDO_CADASTRO_EXTERNO, Estado.AGUARDANDO_VERIFICACAO):
            raise SocialError("estado_nao_permite_credencial",
                              "O cadastro desta conta está em andamento: cancele-o antes de apagar a senha.", 409)

    def credencial_apagada(self, profile_id: str, account_id: str) -> None:
        linha = self.repo.account_row(profile_id, account_id)
        if linha is not None and estado_da_linha(linha) is Estado.CREDENCIAL_PREPARADA:
            self.repo.set_provisioning(profile_id, account_id, de=Estado.CREDENCIAL_PREPARADA.value,
                                       para=Estado.PLANEJADA.value, resume_state=None, detail=None)

    # ------------------------------------------------------------------ transições
    def transicao(self, profile_id: str, account_id: str, body: ProvisioningEventBody, *,
                  by: str, passo: str | None = None) -> ProfileAccountDTO | dict[str, object]:
        social = self.social
        social.get_account(profile_id, account_id)
        linha = self.repo.account_row(profile_id, account_id)
        assert linha is not None
        atual = estado_da_linha(linha)
        evento, esperado = Evento(body.evento), Estado(body.estado_esperado)
        resume = Estado(linha["resume_state"]) if linha["resume_state"] else None
        try:
            r = prov.aplicar(evento, atual=atual, esperado=esperado, resume_state=resume)
        except prov.EstadoInesperado as exc:
            raise SocialError("estado_inesperado", str(exc), 409, {"estado_atual": exc.atual.value}) from None
        except prov.TransicaoInvalida as exc:
            raise SocialError("transicao_invalida", str(exc), 409, {"estado_atual": exc.atual.value}) from None
        if not r.mudou:
            return social.get_account(profile_id, account_id)
        if evento is Evento.CANCELAR:
            return self._cancelar(profile_id, linha)
        handle: str | None = None
        confirmado_em: str | None = None
        evidencia: str | None = None
        detalhe: str | None = None
        if evento is Evento.INICIAR_CADASTRO:
            cred = self.repo.account_credential_row(profile_id, account_id)
            if cred is None:
                raise SocialError("sem_credencial", "Prepare a senha da conta antes de iniciar o cadastro.", 409)
            if cred["consent_at"] is None:
                raise SocialError("sem_consentimento", "A senha desta conta ainda não tem o consentimento da "
                                                       "pessoa para a automação digitá-la.", 409)
        elif evento is Evento.CONFIRMAR:
            handle, tipo, ref = self._conferir_evidencia(profile_id, linha, body, by=by)
            confirmado_em, evidencia = now_iso(), json.dumps({"kind": tipo, "ref": ref})
        elif evento is Evento.FALHAR:
            detalhe = (redact(body.motivo or "").strip() or "Falha sem motivo informado.")[:DETALHE_MAX]
        gravou = self.repo.set_provisioning(
            profile_id, account_id, de=atual.value, para=r.estado.value,
            resume_state=r.resume_state.value if r.resume_state else None, detail=detalhe, handle=handle,
            confirmed_at=confirmado_em, evidence=evidencia)
        if not gravou:
            agora = estado_da_linha(self.repo.account_row(profile_id, account_id))
            raise SocialError("estado_inesperado", f"A conta está em '{agora.value}'.", 409,
                              {"estado_atual": agora.value})
        if handle is not None:
            # O identificador de login da credencial passa a ser o endereço que o provedor confirmou.
            self.repo.db.execute("UPDATE account_credentials SET login_identifier=?, updated_at=? WHERE account_id=?",
                                 (handle, now_iso(), account_id))
        self._emitir(profile_id, account_id, linha["app_id"], evento.value, atual, r.estado,
                     evidencia_tipo=json.loads(evidencia)["kind"] if evidencia else None, passo=passo)
        return social.get_account(profile_id, account_id)

    def _conferir_evidencia(self, profile_id: str, linha: Row, body: ProvisioningEventBody, *,
                            by: str) -> tuple[str, str, str]:
        """(endereço confirmado, tipo da evidência, referência). Sem evidência não há `confirmar`."""
        ev = body.evidencia
        if ev is None:
            raise SocialError("sem_evidencia", "Confirmar a conta exige evidência: a sessão observada ou a marcação "
                                               "nominal de quem a criou.", 422)
        if ev.tipo == "sessao":
            if not ev.sessao_id:
                raise SocialError("sem_evidencia", "Informe a sessão observada (sessao_id: o aparelho).", 422)
            sessao = self.repo.account_session_row(profile_id, linha["id"], ev.sessao_id)
            observado = (sessao["observed_username"] if sessao is not None else None) or ""
            desejado = (linha["desired_handle"] or "").strip().lstrip("@").lower()
            if (sessao is None or sessao["status"] != "session_ready" or not desejado
                    or observado.strip().lstrip("@").lower() != desejado):
                raise SocialError("evidencia_nao_confere",
                                  "A sessão observada não mostra o usuário desejado desta conta.", 409)
            return observado.strip().lstrip("@"), "sessao", ev.sessao_id
        confirmado = self._endereco(ev.handle_confirmado)
        if confirmado is None:
            raise SocialError("sem_evidencia", "A marcação da pessoa precisa dizer o endereço confirmado "
                                               "(handle_confirmado).", 422)
        return confirmado, "declarada", by

    def _cancelar(self, profile_id: str, linha: Row) -> dict[str, object]:
        account_id = linha["id"]
        ref = self.repo.delete_account_credential(profile_id, account_id)
        self.repo.delete_account(profile_id, account_id)
        # O cofre só perde a senha se nenhuma outra conta a referencia (a regra do clone, 31.103).
        self.social._apagar_credencial(profile_id, ref)
        removida = bool(ref) and not self.repo.db.scalar(
            "SELECT COUNT(*) FROM account_credentials WHERE secret_ref=?", (ref,))
        self._emitir(profile_id, account_id, linha["app_id"], Evento.CANCELAR.value, estado_da_linha(linha), None)
        return {"removida": True, "credencial_removida": removida}

    def _emitir(self, profile_id: str, account_id: str, app_id: str, evento: str, de: Estado | None,
                para: Estado | None, *, evidencia_tipo: str | None = None, passo: str | None = None) -> None:
        """Trilha da transição: só ids e estados. Nunca o endereço (desejado ou confirmado) nem valor de segredo."""
        dados: dict[str, object] = {"profile_id": profile_id, "account_id": account_id, "app_id": app_id,
                                 "evento": evento, "de": de.value if de else None, "para": para.value if para else None}
        if evidencia_tipo:
            dados["evidencia_tipo"] = evidencia_tipo
        if passo:
            dados["passo"] = passo                      # 31.310: código fechado do passo do cadastro guiado
        self.social.bus.emit("identity.conta.provisionamento", f"Conta de {app_id}: {evento}", data=dados)
