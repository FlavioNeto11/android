"""O hub de IA: uma instância de provedor por FUNÇÃO, e um ponto único por onde toda chamada passa.

Itens 7.1 e 7.2 do plano. Antes, `build_provider` devolvia UMA instância para os cinco papéis: não havia como
rodar o ator num modelo local e o planejador na Anthropic, o prazo era um só (180 s para todos), a concorrência
era global e o teto de gasto não existia em dinheiro. Tudo isso é a mesma pergunta — "quem atende esta função, em
quanto tempo, por quanto" — e ela agora tem um dono só.

Três regras que este módulo existe para cumprir:

1. **Sem fallback pago silencioso.** A falha do provedor de uma função NÃO cai no provedor pago, a menos que a
   função declare `ai.roles.<papel>.fallback_provider`. Quando cai, a queda vira evento na execução e linha
   própria em `ai_calls` (colunas `requested_model`, `fallback`, `provider` — migração 032).
2. **Capacidade é declarada, não descoberta.** Uma função que precisa de visão apontada para um modelo declarado
   `vision: false` é erro de CONFIGURAÇÃO: o backend recusa subir, em vez de descobrir isso no meio de uma
   execução, depois de ligar o aparelho.
3. **Teto em dinheiro.** Por execução e por dia, em US$, conferido AQUI — o que cobre também o planejamento e a
   prévia de persona, que passavam por fora dos tetos por não estarem dentro do `_ai` do executor.

**Perfil por execução** (item 17.7): `ai.profiles.<nome>` troca funções para as execuções que o escolhem
(`runs.ai_profile`, migração 064). Continua UM hub: os perfis são outras linhas de função dentro dele, com os mesmos
tetos em US$, as mesmas vagas por função e as mesmas instâncias de provedor quando a combinação coincide.

**Onde a execução de IA roda** (decisão registrada, não pendente): continua neste processo, dentro do scheduler.
Separá-la em serviço próprio só se paga quando houver mais de um consumidor do hub, e hoje há um. O agente do
worker segue fixado em provedor simulado (`worker/settings.py`), de propósito: ele não decide, executa verbos.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from ..automation.conhecimento_de_telas import declaram_leitura_visual
from ..config import AI_ROLES, Config, ResolvedRole
from ..modules.execution.domain.orquestracao import OrquestracaoOut, PedidoDeOrquestracao
from ..modules.execution.domain.command_refinement import CommandRefinement, RefineRequest
from ..models import AiRoleStatus, AiStatus, PersonaDraft, Plan, SocialDraftDTO
from . import costs, saldos
from .capabilities import CONHECIMENTO_DE_APPS
from .provider import (AIError, AIProvider, Decision, DecisionRequest, LeituraRequest, PersonaGenerationRequest,
                       PlanRequest, SocialRequest, Transcricao, Usage, Verdict, VerifyRequest, build_one)

log = logging.getLogger("poc.ai")

#: Fração do teto em que sai o aviso. Acima disto a execução continua; em 100 % ela é barrada.
AVISO = 0.8

#: Destino documentado do fallback de recusa da Anthropic (roteado por categoria — cyber vai para este).
REFUSAL_FALLBACK_TARGET = "definido pelo provedor (documentado: claude-opus-4-8)"


class _RoleGate:
    """Semáforo desta função, POR DENTRO do limite global de `ai_slots`.

    O limite global continua sendo quem protege a taxa contra o provedor; este aqui é o que dá prioridade
    relativa entre funções — não deixar o social encher a fila do verificador.

    Ordem, dita como ela é: o limite global é tomado ANTES (no `_ai` do executor) e este depois. Com os padrões
    (decide/verify 8, plan/escalation/social/persona 4, global 4) ele nunca é o gargalo, então a ordem não aparece. Quem
    baixar a concorrência de uma função ABAIXO do `max_ai_concurrency` global precisa saber o efeito colateral:
    tarefas daquela função podem ficar segurando vagas globais enquanto esperam a vaga da função. Não trava
    (quem está dentro segue progredindo), mas atrasa as outras funções — então baixe uma e suba o global junto.
    """

    def __init__(self, limit: int):
        self._sem = asyncio.Semaphore(max(1, limit))

    async def __aenter__(self) -> "_RoleGate":
        await self._sem.acquire()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        self._sem.release()


class RoutingProvider:
    """Despacha plan/decide/verify/escalation/social/persona para o provedor de cada função."""

    simulated = False

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.roles: dict[str, ResolvedRole] = cfg.ai_roles()
        _valida_capacidade(cfg, self.roles)
        cfg.validar_leitura()                  # item 12.5: o leitor é outro modelo que o ator e vê imagem (ADR-070)
        # Uma instância por combinação (provedor, modelo, prazo, tentativas): sem `ai.roles` as cinco funções
        # caem na mesma chave e o hub reaproveita UMA instância — exatamente o que existia antes dele.
        self._por_chave: dict[tuple[Any, ...], AIProvider] = {}
        self.providers: dict[str, AIProvider] = {}
        for papel, r in self.roles.items():
            chave = (r.provider, r.kind, r.model, r.timeout_s, r.max_retries, r.refusal_fallback)
            if chave not in self._por_chave:
                self._por_chave[chave] = build_one(cfg, r)
            self.providers[papel] = self._por_chave[chave]
        # Item 17.7: as funções de cada perfil, resolvidas e conferidas na PARTIDA como as do padrão. As instâncias
        # passam pelo mesmo `_por_chave` (o perfil que só muda o `decide` reaproveita os outros quatro provedores) e as
        # vagas são as da FUNÇÃO, divididas entre perfis — um canário não ganha concorrência própria.
        self.roles_por_perfil: dict[str, dict[str, ResolvedRole]] = {}
        for nome in cfg.file.ai.profiles:
            roles = cfg.ai_roles(nome)
            _valida_capacidade(cfg, roles, f"ai.profiles.{nome}.roles")
            for papel, r in roles.items():
                self._instance(papel, r)
            self.roles_por_perfil[nome] = roles
        #: run_id → perfil. O perfil da execução é gravado na criação e não muda depois: lê-se uma vez.
        self._perfil_por_execucao: dict[str, str | None] = {}
        # Item 12.5: `leitura` só existe quando escrita em `ai.roles`. Entra em `roles` DEPOIS de montar `providers` de
        # propósito: o leitor ausente ou sem chave não derruba `configured` do hub inteiro (só a leitura visual recusa),
        # e a instância dele sai de `_instance`, sob demanda.
        leitura = cfg.ai_leitura()
        if leitura is not None:
            self.roles["leitura"] = leitura
        self._gates = {papel: _RoleGate(r.concurrency) for papel, r in self.roles.items()}
        if not self._persona_tem_vagas_proprias(cfg):
            # Persona sem bloco próprio dividia as vagas do social (a geração chamava o papel social). Com um semáforo
            # novo o teto somado dobraria sem ninguém ter pedido: o mesmo objeto mantém o comportamento de antes.
            self._gates["persona"] = self._gates["social"]
        self.simulated = all(getattr(p, "simulated", False) for p in self.providers.values())
        principal = self.providers["decide"]
        self.name = getattr(principal, "name", "roteador")
        self.model = self.roles["decide"].model
        # Injetados por `AppState` depois que o repositório existe (`attach`): sem eles o hub funciona, só não
        # registra evento nem confere teto — é o que permite construir o provedor antes do banco.
        self.repo: Any = None
        self.get_settings: Callable[[], Any] | None = None
        self._avisados: set[str] = set()

    @staticmethod
    def _persona_tem_vagas_proprias(cfg: Config) -> bool:
        """`ai.roles.persona.concurrency` escrito (no padrão ou em algum perfil) separa as vagas da persona."""
        ai = cfg.file.ai
        blocos = [ai.roles.get("persona")] + [p.roles.get("persona") for p in ai.profiles.values()]
        return any(b is not None and b.concurrency is not None for b in blocos)

    def attach(self, *, repo: Any, settings_getter: Callable[[], Any]) -> None:
        self.repo = repo
        self.get_settings = settings_getter

    @property
    def configured(self) -> bool:
        return all(getattr(p, "configured", True) for p in self.providers.values())

    # ------------------------------------------------------------------ estado para o painel
    def status(self) -> AiStatus:
        base = self.providers["decide"].status()
        linhas: list[AiRoleStatus] = []
        prices = self.cfg.file.ai.prices
        for papel in AI_ROLES:
            r = self.roles[papel]
            caps = self.cfg.model_caps(r.model)
            linhas.append(AiRoleStatus(
                role=papel, provider=r.provider, kind=r.kind, model=r.model, endpoint=r.endpoint,
                sends_data_externally=r.sends_data_externally,
                configured=bool(getattr(self.providers[papel], "configured", True)),
                priced=costs.price_for(prices, r.model) is not None,
                vision=caps.vision, tools=caps.tools,
                refusal_fallback=r.refusal_fallback and r.kind == "anthropic",
                fallback_provider=r.fallback_provider, timeout_s=r.timeout_s, concurrency=r.concurrency,
                effort=r.effort))
        if "leitura" in self.roles:
            r = self.roles["leitura"]
            caps = self.cfg.model_caps(r.model)
            linhas.append(AiRoleStatus(
                role="leitura", provider=r.provider, kind=r.kind, model=r.model, endpoint=r.endpoint,
                sends_data_externally=r.sends_data_externally,
                configured=bool(getattr(self._instance("leitura", r), "configured", True)),
                priced=costs.price_for(prices, r.model) is not None, vision=caps.vision, tools=caps.tools,
                refusal_fallback=False, fallback_provider=None, timeout_s=r.timeout_s, concurrency=r.concurrency,
                effort=None))
        externas = [linha.role for linha in linhas if linha.sends_data_externally]
        aviso = base.notice
        if externas and len(externas) < len(linhas):
            # O aviso-base é o do provedor do ATOR. Sem dizer isso, um ator local abre a frase com "os dados NÃO
            # saem desta máquina" enquanto o agregado diz o contrário — as duas coisas verdadeiras, e lidas juntas
            # parecendo contradição (visto no /api/health de 24/09).
            aviso = ("Ator (decide): " + aviso + " Por função: os dados saem desta máquina em " + ", ".join(externas)
                     + "; as demais rodam em endpoint que não sai daqui.")
        s = self.get_settings() if self.get_settings is not None else None
        gasto = None
        if self.repo is not None:
            try:
                gasto = costs.spent_today_usd(self.repo.db, prices)
            except Exception:  # noqa: BLE001 - o painel de IA nunca cai por causa do relatório de custo
                log.exception("não foi possível calcular o gasto de hoje")
        # Um perfil que manda função para fora desta máquina também é "dados saindo" (item 17.7): sem isto, o aviso
        # do painel dizia "não sai daqui" enquanto o canário mandava capturas para um provedor externo.
        por_perfil = sorted({f"{nome} ({papel})" for nome, roles in self.roles_por_perfil.items()
                             for papel, r in roles.items() if r.sends_data_externally and papel not in externas})
        if por_perfil:
            aviso += " Perfis de IA que enviam dados para fora desta máquina: " + ", ".join(por_perfil) + "."
        if "leitura" in self.roles:
            # O leitor recebe o RECORTE de uma linha de tela, e pode ser outro provedor que o do ator: o destino das
            # capturas muda, e o aviso do painel diz para onde vai (item 12.5, ADR-070).
            r = self.roles["leitura"]
            ligada = self.cfg.file.ai.leitura_visual.enabled
            destino = (f"o provedor “{r.provider}” ({r.endpoint or 'endpoint local'}, modelo {r.model})"
                       if r.sends_data_externally else "um endpoint que não sai desta máquina")
            apps = ", ".join(declaram_leitura_visual(CONHECIMENTO_DE_APPS)) or "nenhum app"
            aviso += (f" Leitura visual ({'ligada' if ligada else 'desligada'}): quando ligada, o recorte de uma linha da "
                      f"tela que a árvore não expõe (apps que declaram a região: {apps}; remetente e assunto de mensagens "
                      f"de terceiros, por exemplo) é enviado a {destino} para uma segunda transcrição, às cegas. Telas "
                      "sensíveis e de verificação nunca são recortadas; a chave aparece só como configurada.")
        refusal = any(linha.refusal_fallback for linha in linhas)
        return base.model_copy(update={
            "models": {papel: self.roles[papel].model for papel in (*AI_ROLES, *(("leitura",) if "leitura" in self.roles else ()))},
            "roles": linhas, "notice": aviso, "configured": self.configured,
            "sends_data_externally": bool(externas or por_perfil), "simulated": self.simulated,
            "provider": self.name, "model": self.model,
            "refusal_fallback": refusal,
            "refusal_fallback_target": REFUSAL_FALLBACK_TARGET if refusal else None,
            "spend_today_usd": gasto,
            "spend_limit_day_usd": getattr(s, "ai_max_usd_per_day", None),
            "spend_limit_run_usd": getattr(s, "ai_max_usd_per_run", None)})

    # ------------------------------------------------------------------ orçamento em US$
    def _budget(self, run_id: str | None, origem: str | None = None) -> None:
        """Teto em DINHEIRO, por execução e por dia (achado #95). Barrar aqui cobre TODO caminho de IA.

        Rubrica única (31.6): toda recusa sai com `AIError(kind="budget", motivo=...)` e quem decide o que fazer lê o
        MOTIVO, nunca a frase. Ordem das réguas, a primeira que estourar vence: orçamento do pedido (`pedido`), teto da
        execução (`execucao`), teto do dia (`dia`) e, por último, a FATIA da origem dentro do dia (`fatia_curador`,
        `fatia_jev`). A fatia vem depois do dia porque é parte dele: passar no dia é pré-requisito, e uma fatia
        estourada não barra outra origem. O saldo da conta (ADR-051) segue em `_saldo`, fora desta função."""
        if self.repo is None or self.get_settings is None:
            return
        s = self.get_settings()
        prices = self.cfg.file.ai.prices
        # Orçamento do PEDIDO (28.6): o que o pedido ainda pode gastar nesta ocorrência. `None` (a execução comum, ou o
        # pedido sem orçamento) não muda nada; é uma leitura pela chave primária da execução, ao lado da que já se faz.
        teto_pedido = self.repo.teto_usd_da_execucao(run_id) if run_id else None
        if teto_pedido is not None:
            gasto = costs.spent_usd(self.repo.db, prices, run_id=run_id)
            if gasto >= teto_pedido:
                raise AIError(f"Orçamento do pedido atingido nesta ocorrência: US$ {gasto:.2f} de US$ {teto_pedido:.2f}. "
                              "Ajuste o orçamento do pedido para continuar.", kind="budget", motivo="pedido")
        teto_dia = float(getattr(s, "ai_max_usd_per_day", 0) or 0)
        for rotulo, limite, gasto_fn, chave, motivo in (
                ("desta execução", float(getattr(s, "ai_max_usd_per_run", 0) or 0),
                 (lambda: costs.spent_usd(self.repo.db, prices, run_id=run_id)), f"run:{run_id}", "execucao"),
                ("do dia", teto_dia,
                 (lambda: costs.spent_today_usd(self.repo.db, prices)), f"dia:{costs.day_start_iso()}", "dia"),
                *self._fatia_da_origem(origem, teto_dia, prices)):
            if limite <= 0 or (rotulo.endswith("execução") and not run_id):
                continue
            gasto = gasto_fn()
            if gasto >= limite:
                raise AIError(f"Teto de gasto de IA {rotulo} atingido: US$ {gasto:.2f} de US$ {limite:.2f}. "
                              "Ajuste o limite em Configuração › Limites para retomar.", kind="budget", motivo=motivo)
            if gasto >= limite * AVISO and chave not in self._avisados:
                self._avisados.add(chave)
                self.repo.bus.emit("log", f"Gasto de IA {rotulo} em US$ {gasto:.2f} de US$ {limite:.2f} "
                                          f"({gasto / limite:.0%} do teto).", level="warn", run_id=run_id)

    def _fatia_da_origem(self, origem: str | None, teto_dia: float, prices: dict[str, list[float]]
                         ) -> tuple[tuple[str, float, Callable[[], float], str, str], ...]:
        """A régua da fatia desta origem, no mesmo formato das de `_budget` (vazia quando não há fatia).

        Só `curador`, `decisao_fechada` e `leitura` têm fatia. Sem fatia configurada nada muda: o curador sem valor explícito e sem
        teto do dia não tem fatia (a fração é do teto do dia, e `0` o desliga), e `0` em qualquer uma a desliga."""
        limites = self.cfg.file.ai.limits
        if origem == "curador":
            explicito = limites.curador_max_usd_per_day
            limite = float(explicito) if explicito is not None else limites.curador_fracao_do_dia * teto_dia
            rotulo, motivo = "da fatia do curador", "fatia_curador"
        elif origem == "decisao_fechada":
            limite, rotulo, motivo = float(limites.jev_max_usd_per_day), "da fatia da decisão fechada", "fatia_jev"
        elif origem == "leitura":
            limite, rotulo, motivo = float(limites.leitura_max_usd_per_day), "da fatia da leitura visual", "fatia_leitura"
        else:
            return ()
        return ((rotulo, limite, lambda: costs.spent_today_usd(self.repo.db, prices, origem=origem),
                 f"fatia:{origem}:{costs.day_start_iso()}", motivo),)

    # ------------------------------------------------------------------ saldo da conta (ADR-051)
    def _saldo(self, r: ResolvedRole) -> None:
        """Conta desta função barrada (bloqueio do dono abaixo do limite, ou crédito esgotado pelo provedor)?

        Diferente do teto em US$, o saldo é POR CONTA: um `fallback_provider` declarado para outra conta pode
        atender — e passa por esta mesma conferência antes."""
        if self.repo is None or r.kind == "simulated":
            return
        try:
            motivo = saldos.motivo_de_bloqueio(self.repo.db, self.cfg,
                                               saldos.conta_por_endpoint(r.kind, r.base_url, r.api_key_env))
        except Exception:  # noqa: BLE001 - leitura de saldo quebrada não para a IA; o aviso fica no log
            log.exception("não foi possível conferir o saldo da conta de %s", r.provider)
            return
        if motivo:
            raise AIError(f"{motivo} Recarregue no console e registre a recarga em Configuração › IA para retomar.", kind="balance",
                          model=r.model)

    def _esgotou(self, r: ResolvedRole, exc: AIError) -> None:
        """Erro de cobrança do provedor vira leitura de saldo 0 daquela conta: a estimativa não fica otimista."""
        if exc.kind != "billing" or self.repo is None:
            return
        saldos.registrar_esgotado(self.repo.db, self.cfg, saldos.conta_por_endpoint(r.kind, r.base_url, r.api_key_env),
                                  f"{r.provider}/{r.model}: {exc}")

    # ------------------------------------------------------------------ perfil da execução (17.7)
    def perfil_da_execucao(self, run_id: str | None) -> str | None:
        """O perfil de IA gravado na execução (`runs.ai_profile`), ou `None` para o padrão.

        Perfil gravado que a configuração não tem mais (alguém o tirou do YAML e reiniciou) NÃO cai no padrão em
        silêncio: a execução diria "rodou no perfil X" tendo rodado em outro, e a comparação A/B mentiria. Vira erro
        de configuração, com o nome do perfil."""
        db = getattr(self.repo, "db", None)
        if not run_id or db is None:
            return None                                  # sem banco não há perfil gravado: vale o padrão
        if run_id not in self._perfil_por_execucao:
            linha = db.one("SELECT ai_profile FROM runs WHERE id=?", (run_id,))
            if len(self._perfil_por_execucao) > 4096:
                self._perfil_por_execucao.clear()        # cache de conveniência: perder é só reler uma linha
            self._perfil_por_execucao[run_id] = (linha["ai_profile"] or None) if linha is not None else None
        perfil = self._perfil_por_execucao[run_id]
        if perfil is not None and perfil not in self.roles_por_perfil:
            raise AIError(f"A execução usa o perfil de IA '{perfil}', que não está mais em ai.profiles. Devolva o perfil "
                          "à configuração ou crie a execução de novo sem ele.", kind="not_configured")
        return perfil

    def _funcao(self, papel: str, run_id: str | None) -> tuple[ResolvedRole, str | None]:
        perfil = self.perfil_da_execucao(run_id)
        if papel == "leitura":
            return self.roles["leitura"], perfil       # o perfil troca o ator, não o conferente (item 12.5)
        return (self.roles_por_perfil[perfil][papel] if perfil else self.roles[papel]), perfil

    # ------------------------------------------------------------------ despacho
    async def _call(self, papel: str, run_id: str | None, fn: Callable[[AIProvider], Any], *,
                    origem: str | None = None, ref: str | None = None) -> tuple[Any, Usage]:
        """`origem`/`ref` (31.2): quem pediu e o item de origem. Com `run_id` e sem origem, é `execucao`; os métodos
        fora de execução passam a sua. O `Usage` devolvido leva os dois e `add_usage` grava em `ai_calls`."""
        origem = origem or ("execucao" if run_id else None)
        r, perfil = self._funcao(papel, run_id)
        if r.kind != "simulated":
            # Modo simulado não gasta dinheiro nenhum: conferir teto ali seria uma consulta por chamada para
            # sempre dar zero — e, com teto apertado, dava para BLOQUEAR uma execução que não custa nada.
            self._budget(run_id, origem)
        try:
            self._saldo(r)
            try:
                resultado, usage = await self._one(papel, r, fn)
            except AIError as exc:
                self._esgotou(r, exc)
                raise
            if usage.fallback == "refusal":
                # A troca por recusa vira linha DA EXECUÇÃO, que é o que o pedido exige e não existia: até aqui
                # ela só aparecia como um modelo diferente no `model` de uma linha de custo.
                self._anota(run_id, f"Recusa de {usage.requested_model} em {papel}; respondeu {usage.model} "
                                    f"(cobrado na tarifa de {usage.model}).")
            usage.origem, usage.ref = origem, ref
            return resultado, usage
        except AIError as exc:
            alvo = r.fallback_provider
            if not alvo or exc.kind in ("budget", "refusal"):
                raise
            # Cair só acontece porque ALGUÉM ESCREVEU que pode cair. É isto que separa "fallback explícito por
            # função" de "fallback pago silencioso": sem a linha no YAML, o erro do endpoint local sobe.
            alternativo = _com_provedor(self.cfg, papel, alvo, perfil)
            self._saldo(alternativo)
            log.warning("Função %s: provedor %s falhou (%s); caindo para %s/%s (declarado em ai.roles.%s).",
                        papel, r.provider, exc, alvo, alternativo.model, papel)
            try:
                resultado, usage = await self._one(papel, alternativo, fn)
            except AIError as exc2:
                self._esgotou(alternativo, exc2)
                raise
            usage.fallback = alvo
            usage.requested_model = r.model
            self._anota(run_id, f"Provedor “{r.provider}” falhou em {papel} ({exc}); respondeu "
                                f"“{alvo}” com {usage.model} (cobrado na tarifa de {usage.model}).")
            usage.origem, usage.ref = origem, ref
            return resultado, usage

    def _instance(self, papel: str, r: ResolvedRole) -> AIProvider:
        chave = (r.provider, r.kind, r.model, r.timeout_s, r.max_retries, r.refusal_fallback)
        if chave not in self._por_chave:
            self._por_chave[chave] = build_one(self.cfg, r)
        return self._por_chave[chave]

    async def _one(self, papel: str, r: ResolvedRole, fn: Callable[[AIProvider], Any]) -> tuple[Any, Usage]:
        provedor = self._instance(papel, r)
        async with self._gates[papel]:
            try:
                resultado, usage = await asyncio.wait_for(fn(provedor), timeout=r.timeout_s)
            except asyncio.TimeoutError as exc:
                # Prazo POR FUNÇÃO (achado #96): antes, uma chamada pendurada segurava a vaga de IA e o aparelho
                # por até 180 s — além do prazo da própria etapa, que ninguém conferia no meio da chamada.
                raise AIError(f"A função {papel} passou de {r.timeout_s:.0f} s no provedor “{r.provider}”.",
                              retryable=True) from exc
        if not usage.provider:
            usage.provider = r.provider
        if not usage.requested_model:
            usage.requested_model = r.model
        return resultado, usage

    def _anota(self, run_id: str | None, texto: str) -> None:
        """A troca de modelo vira linha da execução. Sem isto, ela só existia no `model` de uma linha de custo."""
        if self.repo is None:
            log.info("%s", texto)
            return
        try:
            if run_id:
                self.repo.decision(texto, run_id=run_id)
            else:
                self.repo.bus.emit("log", texto, level="warn")
        except Exception:  # noqa: BLE001 - registrar nunca pode derrubar a chamada que deu certo
            log.exception("não foi possível registrar a troca de provedor")

    # ------------------------------------------------------------------ AIProvider
    async def plan(self, req: PlanRequest) -> tuple[Plan, Usage]:
        return await self._call("plan", req.run_id, lambda p: p.plan(req))

    async def generalize(self, req: Any) -> tuple[dict[str, Any], Usage]:
        """Modo treinamento (item 13.2): mesma função/modelo/orçamento do planejador, sem execução."""
        return await self._call("plan", None, lambda p: p.generalize(req), origem="ensino")

    async def orchestrate_targets(self, req: PedidoDeOrquestracao) -> tuple[OrquestracaoOut, Usage]:
        """Quem faz (ADR-050): mesma função/modelo/orçamento do planejador, sem execução."""
        return await self._call("plan", None, lambda p: p.orchestrate_targets(req), origem="orquestracao")

    async def refine_command(self, req: RefineRequest) -> tuple[CommandRefinement, Usage]:
        """Assistente do comando (ADR-047): mesma função/modelo/orçamento do planejador, sem execução."""
        return await self._call("plan", None, lambda p: p.refine_command(req), origem="assistente")

    async def decide(self, req: DecisionRequest) -> tuple[Decision, Usage]:
        # Escalonamento pode ser OUTRO provedor, não só outro modelo: o despacho olha o tier.
        papel = "escalation" if req.tier > 0 else "decide"
        return await self._call(papel, req.ctx.run_id, lambda p: p.decide(req))

    async def verify(self, req: VerifyRequest) -> tuple[Verdict, Usage]:
        # Como no `decide`: o rejulgamento escalado vai para o provedor e o orçamento da função `escalation`.
        papel = "escalation" if getattr(req, "escalate", False) else "verify"
        return await self._call(papel, req.ctx.run_id, lambda p: p.verify(req))

    async def generate_social_response(self, req: SocialRequest) -> tuple[SocialDraftDTO, Usage]:
        # Sem `run_id` (a prévia de persona nasce do portal): o teto por execução não se aplica, o do dia sim —
        # e era exatamente este o caminho que passava por fora de qualquer orçamento.
        return await self._call("social", None, lambda p: p.generate_social_response(req), origem="social")

    async def transcribe(self, req: LeituraRequest) -> tuple[Transcricao, Usage]:
        """Leitura visual (item 12.5, ADR-070): o segundo leitor transcreve o recorte, às cegas. Papel `leitura` sem
        herança, contado em `ai_calls` com `role='leitura'` e a origem da chamada (`execucao`, pelo `run_id`). Sem o papel
        em `ai.roles`, `not_configured`: o executor recusa a leitura visual com `sem_leitor`."""
        if "leitura" not in self.roles:
            raise AIError("Não há leitor configurado (ai.roles.leitura): a leitura visual está indisponível.",
                          kind="not_configured")
        # `origem='leitura'` (073): a chamada passa pelo `_budget` COM o `run_id` (valem os tetos do pedido, da execução e
        # do dia) e, quando `ai.limits.leitura_max_usd_per_day` > 0, pela fatia própria; o saldo vale por conta.
        return await self._call("leitura", req.run_id, lambda p: p.transcribe(req), origem="leitura")

    async def generate_persona(self, req: PersonaGenerationRequest) -> tuple[PersonaDraft, Usage]:
        # Papel `persona` (item 17.8: herda o `social` até alguém configurá-lo), sem `run_id`: nasce do portal, como
        # a prévia — o teto do dia vale, o da execução não. `image` NÃO é papel: imagem tem porta própria.
        return await self._call("persona", None, lambda p: p.generate_persona(req), origem="persona")


def _com_provedor(cfg: Config, papel: str, provedor: str, perfil: str | None = None) -> ResolvedRole:
    """A mesma função, resolvida contra OUTRO provedor — o declarado em `ai.roles.<papel>.fallback_provider`.

    O MODELO do destino sai de `ai.providers.<destino>.fallback_model`, quando declarado, e do `.env` quando não:
    o modelo do endpoint local quase nunca existe no provedor pago, então herdá-lo seria garantir um 404.
    """
    r = cfg.ai_role(papel, perfil)
    outro = cfg.file.ai.providers.get(provedor)
    kind = outro.kind if outro else "anthropic"
    return ResolvedRole(
        role=papel, provider=provedor, kind=kind,
        model=(outro.fallback_model if outro and outro.fallback_model else cfg.ai_model_for(papel)),
        base_url=outro.base_url if outro else None, api_key_env=outro.api_key_env if outro else None,
        sends_data_externally=bool(outro.sends_data_externally) if outro else True,
        fallback_provider=None,                     # o destino do fallback não cai de novo: uma queda, não uma cadeia
        refusal_fallback=r.refusal_fallback, timeout_s=r.timeout_s, max_retries=r.max_retries,
        concurrency=r.concurrency, effort=r.effort, extra_body=outro.extra_body if outro else None)


def _valida_capacidade(cfg: Config, roles: dict[str, ResolvedRole], onde: str = "ai.roles") -> None:
    """Função que precisa de visão não pode apontar para modelo declarado sem visão (achado #97).

    Recusar na PARTIDA é a diferença entre "o backend não sobe e diz qual linha corrigir" e "o aparelho ligou, a
    execução começou e a primeira decisão falhou depois de gastar uma chamada".
    """
    precisa_visao = () if cfg.file.ai.image_policy == "never" else ("decide", "verify", "escalation")
    for papel in precisa_visao:
        r = roles[papel]
        if r.kind == "simulated":
            continue
        caps = cfg.model_caps(r.model)
        if not caps.vision:
            raise ValueError(
                f"{onde}.{papel}: o modelo '{r.model}' está declarado sem visão em ai.models, mas "
                f"ai.image_policy={cfg.file.ai.image_policy} manda imagem para essa função. "
                "Aponte a função para um modelo com visão ou use ai.image_policy: never.")
