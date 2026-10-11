"""O cadastro guiado ligado ao parque (31.310, ADR-087, adendo v1.137): a rota `…/provisioning/signup` valida e despacha o
comando `session.cadastrar`; o comando roda o motor (`integrations/app_declarado/cadastro.py`) sobre o aparelho de verdade.

Este arquivo só LIGA: a decisão de cada passo é do motor (por tela), e a de cada transição da conta é do serviço de
provisionamento (`social/provisionamento.py`, comparar e trocar). Aqui ficam o aparelho (`MesaDoAparelho`), a conta no banco
(`CicloNoBanco`) e as validações que precisam de resposta síncrona (409), antes de qualquer toque.

Uma conta por vez no parque, nunca liga aparelho, nunca usa IA, e a senha só sai do cofre pelo canal sensível.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from app.automation.driver import DriverError
from app.automation.hierarchy import UiElement, UiTree
from app.commands.despacho import pedir_trabalho_de_app
from app.integrations.app_declarado import cadastro_conhecimento
from app.integrations.app_declarado.cadastro import (ESTADOS_QUE_COMECAM, Dados, Desfecho, FalhaNaMesa,
                                                     MotorDeCadastro)
from app.integrations.app_declarado.cadastro_conhecimento import CadastroInvalido, ConhecimentoDeCadastro
from app.models import ConfirmationEvidence, InstanceState, ProvisioningEventBody, SessionStatus
from app.modules.identity.domain.cadastro import Parada, Passo, detalhe_da_parada
from app.modules.identity.domain.persona import MAIORIDADE, idade_em
from app.modules.identity.domain.provisionamento import Estado
from app.modules.identity.infrastructure.codigo_de_email import CodigoDoEmailDoParque
from app.modules.identity.infrastructure.ponte_igfarm import ArmazemSql
from app.security.sensitive_input import SensitiveInputError, SensitiveInputUnavailable
from app.social.erros import SocialError
from app.social.provisionamento import estado_da_linha
from app.util import now_iso, parse_iso

if TYPE_CHECKING:  # pragma: no cover - só para o verificador de tipos
    from app.devices.manager import DeviceRuntime
    from app.state import AppState

log = logging.getLogger("farm.identidade.cadastro_guiado")

VERBO = "session.cadastrar"
EVENTO = "identity.cadastro"
AUTOR_PADRAO = "cadastro-guiado"
TIMEOUT_DO_APARELHO_S = 30.0


class MesaDoAparelho:
    """A `Mesa` do motor sobre o aparelho. Toda falha do driver ou do canal sensível vira `FalhaNaMesa` (sem valor de segredo)."""

    def __init__(self, s: AppState, rt: DeviceRuntime) -> None:
        self.s, self.rt = s, rt

    async def observar(self) -> tuple[UiTree, str | None]:
        try:
            obs = await self.s.devices.observe(self.rt, timeout=TIMEOUT_DO_APARELHO_S, imagem=False)
        except DriverError as exc:
            raise FalhaNaMesa(type(exc).__name__) from None
        return obs.tree, obs.package

    async def tocar(self, x: int, y: int) -> None:
        try:
            await self.rt.executor.run(self.rt.io.tap, x, y, timeout=TIMEOUT_DO_APARELHO_S, label="toque do cadastro")
        except DriverError as exc:
            raise FalhaNaMesa(type(exc).__name__) from None

    async def digitar(self, texto: str) -> None:
        try:
            await self.rt.executor.run(lambda: self.rt.io.type_text(texto, clear_first=True),
                                       timeout=TIMEOUT_DO_APARELHO_S, label="campo do cadastro")
        except DriverError as exc:
            raise FalhaNaMesa(type(exc).__name__) from None

    async def digitar_sensivel(self, localizar: Callable[[UiTree], UiElement | None], segredo: Callable[[], str]) -> None:
        async def observar() -> UiTree:
            tree, _ = await self.observar()
            return tree

        try:
            await self.s.sensitive_input.fill(call=self.rt.executor.run, io=self.rt.io, observe=observar,
                                              locate=localizar, secret=segredo)
        except (SensitiveInputError, SensitiveInputUnavailable, DriverError) as exc:
            raise FalhaNaMesa(type(exc).__name__) from None             # a mensagem do canal nunca é repassada

    async def esperar(self, segundos: float) -> None:
        await asyncio.sleep(segundos)


class CicloNoBanco:
    """As transições da conta, todas por `ProvisionamentoDeContas.transicao` (comparar e trocar): nada aqui escreve o estado."""

    def __init__(self, s: AppState, rt: DeviceRuntime, profile_id: str, account_id: str, *, by: str) -> None:
        self.s, self.rt, self.profile_id, self.account_id, self.by = s, rt, profile_id, account_id, by

    def estado(self) -> Estado:
        return estado_da_linha(self.s.social_repo.account_row(self.profile_id, self.account_id))

    def desde_do_envio(self) -> datetime | None:
        linha = self.s.social_repo.account_row(self.profile_id, self.account_id)
        quando = parse_iso(str(linha["updated_at"])) if linha is not None and linha["updated_at"] else None
        return quando.astimezone(UTC) if quando is not None else None

    def _evento(self, evento: str, esperado: Estado, passo: Passo, *, motivo: str | None = None,
                evidencia: ConfirmationEvidence | None = None, volta_ao_cadastro: bool = False) -> None:
        corpo = ProvisioningEventBody(evento=evento, estado_esperado=esperado.value, motivo=motivo,  # type: ignore[arg-type]
                                      evidencia=evidencia)
        self.s.social.provisionamento.transicao(self.profile_id, self.account_id, corpo, by=self.by, passo=passo.value,
                                                   volta_ao_cadastro=volta_ao_cadastro)

    def iniciar(self) -> None:
        self._evento("iniciar_cadastro", Estado.CREDENCIAL_PREPARADA, Passo.INICIO)

    def enviado(self, passo: Passo) -> None:
        self._evento("enviado", Estado.AGUARDANDO_CADASTRO_EXTERNO, passo)

    def parar(self, parada: Parada, passo: Passo) -> None:
        try:
            # O @ recusado pelo provedor é a única parada em que nada foi criado: a retomada volta ao formulário.
            self._evento("falhar", self.estado(), passo, motivo=detalhe_da_parada(parada),
                         volta_ao_cadastro=parada is Parada.USUARIO_INDISPONIVEL)
        except SocialError:
            # Outra aba mexeu na conta no meio: a parada já não é o estado dela. O comando termina `failed` do mesmo jeito.
            log.warning("cadastro guiado: a parada %s não pôde ser gravada (a conta mudou de estado)", parada.value)

    def confirmar(self, handle_observado: str) -> bool:
        """Grava a sessão observada e confirma com ela. O @ lido na tela é o único dado que entra; o serviço o compara com o desejado."""
        repo = self.s.social_repo
        repo.set_account_session(self.profile_id, self.account_id, self.rt.id, status=SessionStatus.session_ready,
                                 observed_handle=handle_observado, verified_at=now_iso())
        try:
            self._evento("confirmar", Estado.AGUARDANDO_VERIFICACAO, Passo.CONFIRMACAO,
                         evidencia=ConfirmationEvidence(tipo="sessao", sessao_id=self.rt.id))
        except SocialError:
            repo.set_account_session(self.profile_id, self.account_id, self.rt.id, status=SessionStatus.unknown,
                                     detail="O cadastro guiado não conseguiu confirmar a conta.")
            return False
        return True


class CadastroGuiado:
    def __init__(self, s: AppState) -> None:
        self.s = s

    # ------------------------------------------------------------------ a rota: valida e despacha
    def iniciar(self, profile_id: str, account_id: str, *, instance_id: str | None, by: str) -> dict[str, object]:
        s = self.s
        s.social.get_account(profile_id, account_id)                                # 404 se não existe
        linha = s.social_repo.account_row(profile_id, account_id)
        assert linha is not None
        estado = estado_da_linha(linha)
        if estado not in ESTADOS_QUE_COMECAM:
            raise SocialError("estado_inesperado", f"A conta está em '{estado.value}': o cadastro guiado parte de "
                              "credencial_preparada, aguardando_cadastro_externo ou aguardando_verificacao "
                              "(de falha, retome primeiro).", 409, {"estado_atual": estado.value})
        k = self._conhecimento(profile_id, account_id)
        credencial = s.social_repo.account_credential_row(profile_id, account_id)
        if credencial is None:
            raise SocialError("sem_credencial", "Prepare a senha da conta antes do cadastro guiado.", 409)
        if credencial["consent_at"] is None:
            raise SocialError("sem_consentimento", "A senha desta conta ainda não tem o consentimento da pessoa para a "
                                                   "automação digitá-la.", 409)
        if not (linha["desired_handle"] or "").strip():
            raise SocialError("sem_usuario_desejado", "Defina o @ desejado da conta antes do cadastro guiado.", 409)
        caixa = ArmazemSql(s.db).endereco_da_conta(account_id)
        if caixa is None and ("email" in k.dados_usados or k.pede_codigo):
            raise SocialError("sem_caixa_de_email", "Este app pede o e-mail ou o código por e-mail, e a conta não tem "
                                                    "caixa registrada.", 409)
        nascimento = self._nascimento(profile_id) if k.pede_nascimento else None
        if any(c["verb"] == VERBO for c in s.commands.open_commands()):
            raise SocialError("cadastro_em_andamento", "Há outro cadastro guiado em andamento no parque: uma conta por vez.", 409)
        rt = self._aparelho(profile_id, instance_id)
        dados = self._dados(profile_id, account_id, linha["desired_handle"], caixa, str(credencial["secret_ref"]), nascimento)

        async def comando() -> None:
            await self._rodar(rt, k, dados, profile_id, account_id, by)

        resposta = pedir_trabalho_de_app(
            s, rt, VERBO, comando, label="cadastro guiado da conta",
            params={"profile_id": profile_id, "account_id": account_id},
            idempotency_key=f"signup:{account_id}:{linha['updated_at']}", requested_by=by)
        if not resposta.get("accepted"):
            raise SocialError("aparelho_ocupado", "O aparelho está ocupado (trabalho, controle manual ou fora do ar).", 409,
                              {"instance_id": rt.id})
        return {**resposta, "profile_id": profile_id, "account_id": account_id}

    def _conhecimento(self, profile_id: str, account_id: str) -> ConhecimentoDeCadastro:
        pacote = self.s.social_repo.pacote_da_conta(profile_id, account_id)
        try:
            k = cadastro_conhecimento.do_app(pacote) if pacote else None
        except CadastroInvalido as exc:
            raise SocialError("cadastro_invalido", f"O cadastro.yaml do app não se sustenta: {exc}", 500) from None
        if k is None:
            raise SocialError("sem_conhecimento_de_cadastro", "Este app não declara o cadastro guiado (cadastro.yaml).", 409)
        return k

    def _aparelho(self, profile_id: str, instance_id: str | None) -> DeviceRuntime:
        s = self.s
        if instance_id is None:
            vinculo = s.social_repo.binding_principal(profile_id)
            if vinculo is None:
                raise SocialError("no_binding", "Esta persona não está vinculada a nenhum aparelho.", 409)
            instance_id = str(vinculo["instance_id"])
        elif s.social_repo.binding(profile_id, instance_id) is None:
            raise SocialError("sem_vinculo", f"Esta persona não está vinculada a {instance_id}.", 409)
        try:
            rt = s.devices.get(instance_id)
        except KeyError:
            raise SocialError("not_found", f"Instância {instance_id} não existe.", 404) from None
        if rt.state != InstanceState.online:
            raise SocialError("aparelho_ocupado", "O aparelho não está ligado (o cadastro guiado nunca liga aparelho).", 409,
                              {"instance_id": rt.id})
        return rt

    def _nascimento(self, profile_id: str) -> date:
        """A data de nascimento da persona, para o app que a pede (31.324). Sem ela ou menor de idade: 409 ANTES de tocar no aparelho
        (o motor não inventa data: uma idade chutada vira a idade da conta). A data é dado pessoal: nada dela vai para a mensagem."""
        linha = self.s.social_repo.profile_row(profile_id)
        texto = str(linha["birth_date"] or "").strip() if linha is not None else ""
        idade = idade_em(texto, datetime.now(UTC).date())
        if idade is None:
            raise SocialError("sem_nascimento", "Este app pede a data de nascimento e a persona não tem uma (AAAA-MM-DD).", 409)
        if idade < MAIORIDADE:
            raise SocialError("persona_menor_de_idade", f"A persona tem menos de {MAIORIDADE} anos: o cadastro não segue.", 409)
        return date.fromisoformat(texto[:10])

    def _dados(self, profile_id: str, account_id: str, desejado: str, caixa: str | None, secret_ref: str,
               nascimento: date | None = None) -> Dados:
        s = self.s
        p = s.db.one("SELECT display_name, first_name, last_name, username FROM instagram_profiles WHERE id=?", (profile_id,))
        primeiro = str(p["first_name"] or "") if p is not None else ""
        sobrenome = str(p["last_name"] or "") if p is not None else ""
        nome = " ".join(x for x in (primeiro, sobrenome) if x) or str((p["display_name"] or p["username"]) if p else "")
        # A mesma caixa e o mesmo leitor do código por e-mail do login (ADR-090): só um e-mail recebido DEPOIS de `desde`.
        porta = CodigoDoEmailDoParque(s.db, lambda: getattr(s, "email_parque", None))

        async def codigo(desde: datetime, espera_s: float) -> str | None:
            return await porta.codigo_depois_de(account_id, desde, espera_s=espera_s)

        return Dados(usuario=str(desejado).strip().lstrip("@"), nome=nome, primeiro_nome=primeiro, sobrenome=sobrenome,
                     email=caixa, senha=lambda: s.secrets.get_secret(secret_ref), codigo=codigo, nascimento=nascimento)

    # ------------------------------------------------------------------ o comando
    async def _rodar(self, rt: DeviceRuntime, k: ConhecimentoDeCadastro, dados: Dados, profile_id: str, account_id: str,
                     by: str) -> None:
        s = self.s
        ciclo = CicloNoBanco(s, rt, profile_id, account_id, by=by)
        inicio = time.monotonic()
        # 31.337: com o proxy sticky planejado DESTA conta no aparelho, o egresso é medido na janela ANTES do primeiro toque; se
        # não casar com o esperado (ou a medição não obteve IP), o cadastro nem começa. Sem proxy planejado, segue como sempre.
        if not await self._egresso_confere(rt, profile_id, account_id):
            self._emitir(rt, profile_id, account_id, "parada", "egresso_nao_casou", inicio)
            raise RuntimeError("cadastro guiado parado: egresso_nao_casou")
        try:
            desfecho: Desfecho = await MotorDeCadastro(k, MesaDoAparelho(s, rt), ciclo, dados).executar()
        except Exception as exc:  # noqa: BLE001 - o motor não levanta; isto cobre só a montagem (mesa, ciclo)
            # Nunca `log.exception` nem a mensagem do erro: ela pode trazer referência de segredo ou endereço de e-mail.
            self._emitir(rt, profile_id, account_id, "parada", Parada.FALHA_INTERNA.value, inicio)
            log.warning("cadastro guiado de %s: o comando quebrou (%s)", account_id, type(exc).__name__)
            raise RuntimeError(f"cadastro guiado parado: {Parada.FALHA_INTERNA.value}") from None
        self._emitir(rt, profile_id, account_id, "confirmada" if desfecho.confirmada else "parada",
                     desfecho.parada.value if desfecho.parada else None, inicio)
        if not desfecho.confirmada:
            # O comando termina `failed` (a pessoa assume); a mensagem é só o código fechado.
            raise RuntimeError(f"cadastro guiado parado: {desfecho.parada.value if desfecho.parada else 'desconhecido'}")

    async def _egresso_confere(self, rt: DeviceRuntime, profile_id: str, account_id: str) -> bool:
        s = self.s
        try:
            janela = await s.rede_convergencia.medir_para_o_cadastro(rt, account_id)
        except Exception as exc:  # noqa: BLE001 - conferir é leitura; se a própria conferência quebrou, o cadastro segue como antes
            log.warning("cadastro guiado de %s: a medição do egresso falhou (%s); o cadastro seguiu", account_id,
                        type(exc).__name__)
            return True
        if janela is None:
            return True
        s.bus.emit("session.egresso_na_janela",
                   f"{rt.id}: egresso na janela do cadastro — {'casou' if janela.casou else janela.motivo()}",
                   level="info" if janela.casou else "warn", instance_id=rt.id,
                   data={"profile_id": profile_id, "account_id": account_id, "fase": "cadastro", **janela.como_dado()})
        return janela.casou

    def _emitir(self, rt: DeviceRuntime, profile_id: str, account_id: str, resultado: str, motivo: str | None,
                inicio: float) -> None:
        dados: dict[str, object] = {"profile_id": profile_id, "account_id": account_id, "instance_id": rt.id,
                                    "resultado": resultado, "duracao_s": round(time.monotonic() - inicio, 1)}
        if motivo:
            dados["motivo"] = motivo
        try:
            self.s.bus.emit(EVENTO, f"{rt.id}: cadastro guiado {resultado}" + (f" ({motivo})" if motivo else ""),
                            level="info" if resultado == "confirmada" else "warn", instance_id=rt.id, data=dados)
        except Exception as exc:  # noqa: BLE001 - o evento nunca derruba o comando
            log.warning("cadastro guiado: evento não emitido (%s)", type(exc).__name__)
