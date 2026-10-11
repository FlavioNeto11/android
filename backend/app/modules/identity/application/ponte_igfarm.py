"""A ponte android ⇄ igfarm: entregar as personas que ainda não têm conta (com e-mail, @ e foto sugeridos) e registrar a conta
que o igfarm criou. Nível de aplicação: só portas e domínio; o banco, o cofre, a IA, as imagens e o cadastro de contas entram
pelas portas abaixo (cumpridas em `infrastructure/ponte_igfarm.py`).

Regras que valem aqui, e não nos adaptadores:

- **A sugestão é persistente** (`persona_reservas`): todo GET grava ou reaproveita o par e-mail + @ da pessoa, então a chamada
  repetida devolve os mesmos valores e não paga a IA outra vez; só se regera o que virou indisponível.
- **Reservar** (`reservar=true`) tira a pessoa das outras chamadas por 24 h (a reserva vence sozinha) e é o único caminho que GERA
  a foto (paga). A listagem pura nunca gera.
- **O registro é idempotente** por (persona, @): repetir devolve o que já está gravado, sem duplicar conta, credencial nem caixa.
- **Senha nunca sai**: as duas senhas entram no cofre e não voltam em resposta, evento nem log; a resposta mostra a máscara.
- O @ tomado por TERCEIRO no Instagram é problema do igfarm (ele tenta outro); a ponte só confere os @ nossos (contas vivas,
  lápide e sugestões das outras pessoas).
"""
from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Protocol

import logging

from pydantic import SecretStr

from app.modules.email_do_parque.application.ports import CabecalhoDeMensagem
from app.modules.email_do_parque.application.servico import EmailDoParque, ErroEmailDoParque
from app.modules.identity.domain.persona import MAIORIDADE
from app.modules.identity.domain.ponte_igfarm import (SYSTEM_DO_USERNAME, TENTATIVAS_DE_USERNAME, TTL_RESERVA_HORAS,
                                                       CicloDaConta, CodigoDaConta, ComandoDeRegistro, ContaRegistrada,
                                                       EgressoDoDevice, FichaDaPessoa, ImagemDaPessoa, PersonaPendente, Sugestao,
                                                       normalizar_username, pedido_do_username, username_do_modelo,
                                                       username_valido)
from app.util import to_iso

log = logging.getLogger(__name__)


class ErroDaPonte(RuntimeError):
    """Erro de regra da ponte, já com o código e o status HTTP que a borda devolve."""

    def __init__(self, code: str, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


# ------------------------------------------------------------------ portas
class ArmazemDaPonte(Protocol):
    def tx(self) -> AbstractContextManager[object]: ...
    def candidatas(self, locale: str | None, agora: str) -> list[str]: ...
    def sugestao(self, persona_id: str) -> Sugestao | None: ...
    def trocar_sugestao(self, persona_id: str, email: str, username: str, imagem_id: str | None, agora: str) -> bool: ...
    def emails_tomados(self, excluir_persona: str) -> set[str]: ...
    def usernames_sugeridos(self, excluir_persona: str) -> set[str]: ...
    def reservar(self, persona_id: str, agora: str, expira_em: str) -> bool: ...
    def guardar_imagem(self, persona_id: str, imagem_id: str) -> None: ...
    def apagar_sugestao(self, persona_id: str) -> None: ...
    def conta_igfarm(self, persona_id: str, username: str) -> ContaRegistrada | None: ...
    def email_em_uso(self, email: str, excluir_persona: str) -> bool: ...
    def gravar_caixa(self, *, account_id: str, persona_id: str, endereco: str, dominio: str, secret_ref: str,
                     key_id: str, agora: str) -> None: ...
    def gravar_conta_igfarm(self, *, account_id: str, persona_id: str, igfarm_account_id: str, username: str,
                            criada_em: str, agora: str) -> None: ...
    def gravar_egresso(self, account_id: str, proxy_secret_ref: str | None, proxy_key_id: str | None,
                       ip_criacao: str | None) -> None: ...
    def _instance_ids_da_persona(self, persona_id: str) -> list[str]: ...
    def perfis_vinculados(self, instance_id: str) -> list[str]: ...
    def endereco_da_conta(self, conta_id: str) -> str | None: ...
    def ciclo_da_conta(self, conta_id: str) -> CicloDaConta | None: ...


class PessoasDaPonte(Protocol):
    def ficha(self, persona_id: str) -> FichaDaPessoa | None: ...


class TextosDaPonte(Protocol):
    async def username(self, system: str, pedido: str) -> str: ...


class ImagensDaPonte(Protocol):
    def existente(self, persona_id: str, preferida: str | None) -> ImagemDaPessoa | None: ...
    def conferir(self) -> None: ...
    async def gerar(self, persona_id: str) -> ImagemDaPessoa: ...


class ContasDaPonte(Protocol):
    def eh_nossa(self, handle: str) -> bool: ...
    def foi_retirada(self, handle: str) -> bool: ...
    def registrar(self, persona_id: str, *, username: str, email: str, senha: str, por: str) -> str: ...


class CofreDaPonte(Protocol):
    def guardar(self, valor: str) -> tuple[str, str]: ...
    def apagar(self, ref: str) -> None: ...


class BarramentoDaPonte(Protocol):
    def emitir(self, tipo: str, mensagem: str, dados: dict[str, object]) -> None: ...


class RedeError(RuntimeError):
    def __init__(self, status: int, code: str, message: str, **extra: object):
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra


class RedeDaPonte(Protocol):
    def parse_proxy(self, proxy_url: str) -> tuple[str, str, int, str | None, str | None]: ...
    def criar_perfil_de_conta(self, account_id: str, *, host: str, port: int, protocol: str, username: str | None,
                              secret: SecretStr | None, ip_criacao: str | None, quem: str | None) -> str: ...
    def atribuir(self, instance_ids: list[str], proxy_profile_id: str | None, policy: str, quem: str | None,
                 confirm_real_account: list[str] | None = None) -> dict[str, object]: ...
    def pedido_atual(self, instance_id: str) -> tuple[str | None, str] | None: ...


class PonteIgfarm:
    def __init__(self, *, armazem: ArmazemDaPonte, pessoas: PessoasDaPonte, textos: TextosDaPonte,
                 imagens: ImagensDaPonte, contas: ContasDaPonte, cofre: CofreDaPonte, email: EmailDoParque,
                 barramento: BarramentoDaPonte, rede: RedeDaPonte | None = None,
                 agora: Callable[[], datetime] | None = None,
                 ttl_reserva_horas: int = TTL_RESERVA_HORAS, criacao_pela_api: bool = True) -> None:
        self.armazem = armazem
        self.pessoas = pessoas
        self.textos = textos
        self.imagens = imagens
        self.contas = contas
        self.cofre = cofre
        self.email = email
        self.barramento = barramento
        self.rede = rede
        self._agora = agora or (lambda: datetime.now(timezone.utc))
        self.ttl_reserva_horas = ttl_reserva_horas
        #: 31.335: com `False`, a ponte não entrega personas para o igfarm criar por API (o cadastro é no app).
        self.criacao_pela_api = criacao_pela_api

    # ------------------------------------------------------------------ API 1: personas pendentes
    async def pendentes(self, *, dominio: str, limite: int, locale: str | None, com_imagem: bool,
                        reservar: bool) -> list[PersonaPendente]:
        if not self.criacao_pela_api:
            raise ErroDaPonte("criacao_pela_api_aposentada",
                              "A criação de conta pela API do igfarm foi aposentada: o cadastro é feito no app e o igfarm "
                              "serve de apoio (e-mail, código, SMS, proxy).", 409)
        dom = self._dominio(dominio)
        agora = self._agora()
        agora_iso, expira_iso = to_iso(agora), to_iso(agora + timedelta(hours=self.ttl_reserva_horas))
        saida: list[PersonaPendente] = []
        for persona_id in self.armazem.candidatas(locale, agora_iso):
            if len(saida) >= limite:
                break
            ficha = self.pessoas.ficha(persona_id)
            # O igfarm precisa da data de nascimento para criar a conta, e a foto é só de adulto: sem data, ou menor, a
            # pessoa não é entregue (nem sugerida).
            if ficha is None or not ficha.birth_date or ficha.idade is None or ficha.idade < MAIORIDADE:
                continue
            sugestao = await self._sugestao(ficha, dom)
            imagem = self.imagens.existente(persona_id, sugestao.imagem_id) if com_imagem else None
            erro_da_imagem: str | None = None
            if reservar:
                if com_imagem and imagem is None:
                    try:
                        self.imagens.conferir()          # ANTES de reservar: gerador sem chave ou teto do dia esgotado
                    except ErroDaPonte:
                        if saida:
                            break                        # o que já foi reservado e gerado é entregue; o resto fica
                        raise
                if not self.armazem.reservar(persona_id, agora_iso, expira_iso):
                    continue                             # outra chamada reservou esta pessoa no meio tempo
                if com_imagem and imagem is None:
                    try:
                        imagem = await self.imagens.gerar(persona_id)
                        self.armazem.guardar_imagem(persona_id, imagem.id)
                    except ErroDaPonte as exc:
                        # Recusa do filtro ou falha do provedor: a reserva fica (não se retenta o gasto em laço) e a
                        # pessoa sai com a foto pendente; o motivo vai no evento, nunca na resposta.
                        erro_da_imagem = exc.code
                self.barramento.emitir(
                    "identity.persona.reservada", f"Persona {ficha.nome_exibicao or ficha.nome} reservada para o igfarm",
                    {"persona_id": persona_id, "email_sugerido": sugestao.email, "username_sugerido": sugestao.username,
                     "expira_em": expira_iso, "imagem_id": imagem.id if imagem else None,
                     **({"imagem_erro": erro_da_imagem} if erro_da_imagem else {})})
            saida.append(PersonaPendente(ficha=ficha, email_sugerido=sugestao.email, username_sugerido=sugestao.username,
                                         imagem=imagem, imagem_pendente=com_imagem and imagem is None,
                                         imagem_erro=erro_da_imagem))
        return saida

    def _dominio(self, dominio: str) -> str:
        try:
            return self.email.validar_dominio(dominio)
        except ErroEmailDoParque as exc:
            raise ErroDaPonte(exc.code, exc.message, exc.status) from None

    async def _sugestao(self, ficha: FichaDaPessoa, dom: str) -> Sugestao:
        """A sugestão da pessoa: a gravada, se ainda vale; senão regera SÓ o que virou indisponível e grava de novo."""
        pid = ficha.persona_id
        tentados: list[str] = []
        atual = self.armazem.sugestao(pid)
        for _ in range(3):
            email = atual.email if atual is not None and self._email_serve(atual.email, dom, pid) else None
            username = atual.username if atual is not None and self._username_serve(atual.username, pid) else None
            if atual is not None and email == atual.email and username == atual.username:
                return atual
            if email is None:
                email = self._novo_email(ficha, dom)
            if username is None:
                username = await self._novo_username(ficha, tentados)
            tentados.append(username)
            agora = to_iso(self._agora())
            imagem_id = atual.imagem_id if atual is not None else None
            if self.armazem.trocar_sugestao(pid, email, username, imagem_id, agora):
                return Sugestao(persona_id=pid, email=email, username=username, imagem_id=imagem_id)
            atual = self.armazem.sugestao(pid)           # perdeu a corrida (outra chamada, ou o mesmo valor com outra pessoa)
        raise ErroDaPonte("sugestao_indisponivel", "Não foi possível fixar um e-mail e um @ livres para a persona.", 409)

    def _email_serve(self, email: str, dom: str, persona_id: str) -> bool:
        return email.lower().endswith("@" + dom) and email.lower() not in self.armazem.emails_tomados(persona_id)

    def _username_serve(self, username: str, persona_id: str) -> bool:
        return (username_valido(username) and not self.contas.eh_nossa(username)
                and username not in self.armazem.usernames_sugeridos(persona_id))

    def _novo_email(self, ficha: FichaDaPessoa, dom: str) -> str:
        try:
            return self.email.gerar_endereco(persona_id=ficha.persona_id, primeiro_nome=ficha.primeiro_nome,
                                             sobrenome=ficha.sobrenome, dominio=dom,
                                             existentes=self.armazem.emails_tomados(ficha.persona_id))
        except ErroEmailDoParque as exc:
            raise ErroDaPonte(exc.code, exc.message, exc.status) from None
        except ValueError as exc:
            raise ErroDaPonte("email_indisponivel", str(exc), 409) from None

    async def _novo_username(self, ficha: FichaDaPessoa, ja_tentados: list[str]) -> str:
        recusados = list(ja_tentados)
        tomados = self.armazem.usernames_sugeridos(ficha.persona_id)
        for _ in range(TENTATIVAS_DE_USERNAME):
            texto = await self.textos.username(SYSTEM_DO_USERNAME, pedido_do_username(ficha, recusados))
            candidato = username_do_modelo(texto)
            if username_valido(candidato) and candidato not in tomados and not self.contas.eh_nossa(candidato) \
                    and candidato not in recusados:
                return candidato
            recusados.append(candidato or "(vazio)")
        raise ErroDaPonte("ai_error", f"O modelo não devolveu um @ válido em {TENTATIVAS_DE_USERNAME} tentativas.", 503)

    # ------------------------------------------------------------------ API 2: registrar a conta criada
    def registrar(self, cmd: ComandoDeRegistro) -> ContaRegistrada:
        """Grava a conta criada pelo igfarm. Uma transação: a pessoa adotada, a conta, a credencial (login = e-mail), a caixa e a
        marca do igfarm entram juntas ou nenhuma. Repetir o mesmo (persona, @) devolve o que já está gravado."""
        username = normalizar_username(cmd.instagram_username)
        email = cmd.email.strip().lower()
        if self.pessoas.ficha(cmd.persona_id) is None:
            raise ErroDaPonte("not_found", "Persona não encontrada.", 404)
        dom = self._dominio(cmd.dominio)
        try:
            self.email.confere_dominio(email, dom)
        except ErroEmailDoParque as exc:
            raise ErroDaPonte(exc.code, exc.message, exc.status) from None
        if not username_valido(username):
            raise ErroDaPonte("username_invalido", "O @ deve ter de 3 a 30 caracteres de a-z, 0-9, ponto e sublinhado, "
                                                   "sem ponto no começo ou no fim nem dois pontos seguidos.", 422)

        agora = to_iso(self._agora())
        ref_email: str | None = None
        existente: ContaRegistrada | None = None
        account_id: str | None = None
        with self.armazem.tx():
            existente = self.armazem.conta_igfarm(cmd.persona_id, username)
            if existente is None:
                if self.contas.foi_retirada(username):
                    raise ErroDaPonte("conta_retirada", f"O @{username} é de uma conta retirada da plataforma.", 409)
                if self.contas.eh_nossa(username):
                    raise ErroDaPonte("duplicate_username", f"Já existe uma conta nossa para @{username}.", 409)
                if self.armazem.email_em_uso(email, cmd.persona_id):
                    raise ErroDaPonte("email_em_uso", "Este e-mail já é de outra conta.", 409)
                try:
                    account_id = self.contas.registrar(cmd.persona_id, username=username, email=email,
                                                       senha=cmd.instagram_senha, por=cmd.por)
                    ref_email, key_id = self.cofre.guardar(cmd.email_senha)
                    self.armazem.gravar_caixa(account_id=account_id, persona_id=cmd.persona_id, endereco=email,
                                              dominio=dom, secret_ref=ref_email, key_id=key_id, agora=agora)
                    self.armazem.gravar_conta_igfarm(account_id=account_id, persona_id=cmd.persona_id,
                                                     igfarm_account_id=cmd.igfarm_account_id, username=username,
                                                     criada_em=cmd.criada_em, agora=agora)
                    self.armazem.apagar_sugestao(cmd.persona_id)
                except BaseException:
                    if ref_email is not None:                # o cofre não é transacional: não deixa a senha órfã
                        self.cofre.apagar(ref_email)
                    raise

        # Egresso roda nos DOIS caminhos (a tx principal já fechou). Perfil é idempotente por nome;
        # o gravar_egresso tem guarda no SQL.
        conta_id = existente.account_id if existente is not None else account_id
        assert conta_id is not None                   # ou a conta já existia, ou o bloco acima a registrou
        egresso = self._registrar_egresso(cmd, conta_id)

        if existente is not None:
            return replace(existente, egresso=egresso)

        self.barramento.emitir(
            "identity.conta.registrada", f"Conta @{username} registrada pela ponte do igfarm",
            {"persona_id": cmd.persona_id, "account_id": conta_id, "igfarm_account_id": cmd.igfarm_account_id,
             "username": username, "email": email})
        return ContaRegistrada(persona_id=cmd.persona_id, account_id=conta_id, igfarm_account_id=cmd.igfarm_account_id,
                               email=email, instagram_username=username, criada_em=cmd.criada_em, registrada_em=agora,
                               idempotente=False, egresso=egresso)

    def _registrar_egresso(self, cmd: ComandoDeRegistro, account_id: str) -> tuple[EgressoDoDevice, ...]:
        """Cria o perfil de proxy do egresso e atribui aos devices vinculados. Parse defensivo: proxy inválido não derruba."""
        if self.rede is None:
            return ()
        if not (cmd.proxy_url and cmd.proxy_url.strip()):
            return ()
        try:
            scheme, host, port, username, password = self.rede.parse_proxy(cmd.proxy_url)
        except ValueError:
            log.warning("proxy_url inválido para conta %s", account_id)
            return ()
        with self.armazem.tx():
            # Só na 1ª vez: re-POST não acumula segredo (a guarda do SQL também protege)
            conta = self.armazem.conta_igfarm(cmd.persona_id, normalizar_username(cmd.instagram_username))
            if conta is not None and conta.proxy_secret_ref is None:
                secret_ref: str | None = None
                key_id: str | None = None
                if password:
                    secret_ref, key_id = self.cofre.guardar(password)
                self.armazem.gravar_egresso(account_id, secret_ref, key_id, cmd.ip_criacao)
            perfil_id = self.rede.criar_perfil_de_conta(
                account_id, host=host, port=port, protocol=scheme, username=username,
                secret=SecretStr(password) if password else None, ip_criacao=cmd.ip_criacao, quem="igfarm")
        # Auto-assign: roda nos DOIS caminhos (com e sem vínculo)
        return self._auto_assign(cmd.persona_id, perfil_id, account_id)

    def _auto_assign(self, persona_id: str, perfil_id: str, account_id: str) -> tuple[EgressoDoDevice, ...]:
        """Atribui o perfil de proxy aos devices vinculados à persona e DIZ o que aconteceu com cada um.

        O aparelho que já tem a conta desta mesma persona não pede confirmação: a conta real que a rede protege é a que
        está sendo registrada agora, e o vínculo veio antes da conta (o gatilho do vínculo nasce sem perfil). Só há
        confirmação genuína quando o aparelho tem conta de OUTRA persona: aí o egresso fica `pendente_confirmacao`, vai
        no retorno do registro e num evento, em vez de sumir num log. Repetir é seguro: o aparelho que já pede este
        perfil com política que segura sai sem reatribuir, e o bloqueio de `exigida_com_bloqueio` nunca é rebaixado."""
        if self.rede is None:
            return ()
        resultado: list[EgressoDoDevice] = []
        for iid in self.armazem._instance_ids_da_persona(persona_id):
            atual = self.rede.pedido_atual(iid)
            if atual is not None and atual[0] == perfil_id and atual[1] in ("exigida", "exigida_com_bloqueio"):
                resultado.append(EgressoDoDevice(iid, "ja_atribuido"))
                continue
            politica = "exigida_com_bloqueio" if atual is not None and atual[1] == "exigida_com_bloqueio" else "exigida"
            de_outras = [p for p in self.armazem.perfis_vinculados(iid) if p != persona_id]
            try:
                self.rede.atribuir(instance_ids=[iid], proxy_profile_id=perfil_id, policy=politica, quem="igfarm",
                                   confirm_real_account=None if de_outras else [iid])
            except RedeError as exc:
                if exc.code != "real_account_confirm_required":
                    raise
                motivo = f"o aparelho tem conta real de outra persona ({', '.join(de_outras) or 'desconhecida'})"
                log.warning("egresso: %s com %s: %s", iid, perfil_id, motivo)
                resultado.append(EgressoDoDevice(iid, "pendente_confirmacao", motivo))
                self.barramento.emitir(
                    "identity.egresso.pendente", f"Egresso da conta {account_id} pendente em {iid}",
                    {"persona_id": persona_id, "account_id": account_id, "instance_id": iid,
                     "perfil_id": perfil_id, "motivo": motivo})
                continue
            resultado.append(EgressoDoDevice(iid, "atribuido"))
        return tuple(resultado)

    def planejar_proxy(self, persona_id: str, account_id: str, proxy_url: str) -> tuple[str, tuple[EgressoDoDevice, ...]]:
        """31.337: o perfil de proxy sticky da conta PLANEJADA (`igfarm-<conta>`), criado no planejamento e atribuído aos
        aparelhos da persona ANTES do primeiro toque do cadastro no app. O igfarm só entrega o `proxy_url`; não cria a conta.
        Sem `egress_esperado`: o IP de criação é a primeira medição dentro da janela do cadastro (`medir_para_o_cadastro`).
        Idempotente pelo nome do perfil (repetir devolve o mesmo e reatribui o que faltar)."""
        if self.rede is None:
            raise ErroDaPonte("rede_indisponivel", "O subsistema de rede não está disponível.", 503)
        try:
            scheme, host, port, username, password = self.rede.parse_proxy(proxy_url)
        except ValueError:
            raise ErroDaPonte("proxy_invalido", "O proxy_url não é um endereço de proxy válido.", 422) from None
        with self.armazem.tx():
            perfil_id = self.rede.criar_perfil_de_conta(
                account_id, host=host, port=port, protocol=scheme, username=username,
                secret=SecretStr(password) if password else None, ip_criacao=None, quem="cadastro")
        return perfil_id, self._auto_assign(persona_id, perfil_id, account_id)

    # ------------------------------------------------------------------ código de confirmação
    def ciclo(self, conta_id: str) -> CicloDaConta:
        """31.333: o que aconteceu com a conta do igfarm (criada, contatos com o app, retirada), só leitura."""
        achado = self.armazem.ciclo_da_conta(conta_id)
        if achado is None:
            raise ErroDaPonte("not_found", "Conta sem ciclo conhecido (nem do igfarm nem do cadastro no app).", 404)
        return achado

    async def cabecalhos(self, conta_id: str, *, horas: int, limite: int) -> list[CabecalhoDeMensagem]:
        """31.336: os cabeçalhos (sem corpo) das mensagens que chegaram à caixa da conta; só leitura."""
        endereco = self.armazem.endereco_da_conta(conta_id)
        if endereco is None:
            raise ErroDaPonte("not_found", "Conta sem caixa de e-mail registrada pela ponte.", 404)
        try:
            return await self.email.cabecalhos(endereco, horas=horas, limite=limite)
        except ErroEmailDoParque as exc:
            raise ErroDaPonte(exc.code, exc.message, exc.status) from None

    async def codigo(self, conta_id: str) -> CodigoDaConta:
        endereco = self.armazem.endereco_da_conta(conta_id)
        if endereco is None:
            raise ErroDaPonte("not_found", "Conta sem caixa de e-mail registrada pela ponte.", 404)
        try:
            achado = await self.email.codigo_recente(endereco)
        except ErroEmailDoParque as exc:
            raise ErroDaPonte(exc.code, exc.message, exc.status) from None
        if achado is None:
            raise ErroDaPonte("sem_codigo", "Nenhum código recente na caixa desta conta.", 404)
        return CodigoDaConta(codigo=achado.codigo, recebido_em=to_iso(achado.recebido_em), remetente=achado.remetente)
