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

**Onde a execução de IA roda** (decisão registrada, não pendente): continua neste processo, dentro do scheduler.
Separá-la em serviço próprio só se paga quando houver mais de um consumidor do hub, e hoje há um. O agente do
worker segue fixado em provedor simulado (`worker/settings.py`), de propósito: ele não decide, executa verbos.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from ..config import AI_ROLES, Config, ResolvedRole
from ..models import AiRoleStatus, AiStatus, Plan, SocialDraftDTO
from . import costs
from .provider import (AIError, AIProvider, Decision, DecisionRequest, PlanRequest, SocialRequest, Usage,
                       Verdict, VerifyRequest, build_one)

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
    (decide/verify 8, plan/escalation/social 4, global 4) ele nunca é o gargalo, então a ordem não aparece. Quem
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
    """Despacha plan/decide/verify/escalation/social para o provedor de cada função."""

    simulated = False

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.roles: dict[str, ResolvedRole] = cfg.ai_roles()
        _valida_capacidade(cfg, self.roles)
        # Uma instância por combinação (provedor, modelo, prazo, tentativas): sem `ai.roles` as cinco funções
        # caem na mesma chave e o hub reaproveita UMA instância — exatamente o que existia antes dele.
        self._por_chave: dict[tuple[Any, ...], AIProvider] = {}
        self.providers: dict[str, AIProvider] = {}
        for papel, r in self.roles.items():
            chave = (r.provider, r.kind, r.model, r.timeout_s, r.max_retries, r.refusal_fallback)
            if chave not in self._por_chave:
                self._por_chave[chave] = build_one(cfg, r)
            self.providers[papel] = self._por_chave[chave]
        self._gates = {papel: _RoleGate(r.concurrency) for papel, r in self.roles.items()}
        self.simulated = all(getattr(p, "simulated", False) for p in self.providers.values())
        principal = self.providers["decide"]
        self.name = getattr(principal, "name", "roteador")
        self.model = self.roles["decide"].model
        # Injetados por `AppState` depois que o repositório existe (`attach`): sem eles o hub funciona, só não
        # registra evento nem confere teto — é o que permite construir o provedor antes do banco.
        self.repo: Any = None
        self.get_settings: Callable[[], Any] | None = None
        self._avisados: set[str] = set()

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
        externas = [linha.role for linha in linhas if linha.sends_data_externally]
        aviso = base.notice
        if externas and len(externas) < len(linhas):
            aviso += (" Por função: os dados saem desta máquina em " + ", ".join(externas)
                      + "; as demais rodam em endpoint que não sai daqui.")
        s = self.get_settings() if self.get_settings is not None else None
        gasto = None
        if self.repo is not None:
            try:
                gasto = costs.spent_today_usd(self.repo.db, prices)
            except Exception:  # noqa: BLE001 - o painel de IA nunca cai por causa do relatório de custo
                log.exception("não foi possível calcular o gasto de hoje")
        refusal = any(linha.refusal_fallback for linha in linhas)
        return base.model_copy(update={
            "models": {papel: self.roles[papel].model for papel in AI_ROLES},
            "roles": linhas, "notice": aviso, "configured": self.configured,
            "sends_data_externally": bool(externas), "simulated": self.simulated,
            "provider": self.name, "model": self.model,
            "refusal_fallback": refusal,
            "refusal_fallback_target": REFUSAL_FALLBACK_TARGET if refusal else None,
            "spend_today_usd": gasto,
            "spend_limit_day_usd": getattr(s, "ai_max_usd_per_day", None),
            "spend_limit_run_usd": getattr(s, "ai_max_usd_per_run", None)})

    # ------------------------------------------------------------------ orçamento em US$
    def _budget(self, run_id: str | None) -> None:
        """Teto em DINHEIRO, por execução e por dia (achado #95). Barrar aqui cobre TODO caminho de IA."""
        if self.repo is None or self.get_settings is None:
            return
        s = self.get_settings()
        prices = self.cfg.file.ai.prices
        for rotulo, limite, gasto_fn, chave in (
                ("desta execução", float(getattr(s, "ai_max_usd_per_run", 0) or 0),
                 (lambda: costs.spent_usd(self.repo.db, prices, run_id=run_id)), f"run:{run_id}"),
                ("do dia", float(getattr(s, "ai_max_usd_per_day", 0) or 0),
                 (lambda: costs.spent_today_usd(self.repo.db, prices)), f"dia:{costs.day_start_iso()}")):
            if limite <= 0 or (rotulo.endswith("execução") and not run_id):
                continue
            gasto = gasto_fn()
            if gasto >= limite:
                raise AIError(f"Teto de gasto de IA {rotulo} atingido: US$ {gasto:.2f} de US$ {limite:.2f}. "
                              "Ajuste o limite em Configuração › Limites para retomar.", kind="budget")
            if gasto >= limite * AVISO and chave not in self._avisados:
                self._avisados.add(chave)
                self.repo.bus.emit("log", f"Gasto de IA {rotulo} em US$ {gasto:.2f} de US$ {limite:.2f} "
                                          f"({gasto / limite:.0%} do teto).", level="warn", run_id=run_id)

    # ------------------------------------------------------------------ despacho
    async def _call(self, papel: str, run_id: str | None, fn: Callable[[AIProvider], Any]) -> tuple[Any, Usage]:
        r = self.roles[papel]
        if r.kind != "simulated":
            # Modo simulado não gasta dinheiro nenhum: conferir teto ali seria uma consulta por chamada para
            # sempre dar zero — e, com teto apertado, dava para BLOQUEAR uma execução que não custa nada.
            self._budget(run_id)
        try:
            resultado, usage = await self._one(papel, r, fn)
            if usage.fallback == "refusal":
                # A troca por recusa vira linha DA EXECUÇÃO, que é o que o pedido exige e não existia: até aqui
                # ela só aparecia como um modelo diferente no `model` de uma linha de custo.
                self._anota(run_id, f"Recusa de {usage.requested_model} em {papel}; respondeu {usage.model} "
                                    f"(cobrado na tarifa de {usage.model}).")
            return resultado, usage
        except AIError as exc:
            alvo = r.fallback_provider
            if not alvo or exc.kind in ("budget", "refusal"):
                raise
            # Cair só acontece porque ALGUÉM ESCREVEU que pode cair. É isto que separa "fallback explícito por
            # função" de "fallback pago silencioso": sem a linha no YAML, o erro do endpoint local sobe.
            alternativo = _com_provedor(self.cfg, papel, alvo)
            log.warning("Função %s: provedor %s falhou (%s); caindo para %s/%s (declarado em ai.roles.%s).",
                        papel, r.provider, exc, alvo, alternativo.model, papel)
            resultado, usage = await self._one(papel, alternativo, fn)
            usage.fallback = alvo
            usage.requested_model = r.model
            self._anota(run_id, f"Provedor “{r.provider}” falhou em {papel} ({exc}); respondeu "
                                f"“{alvo}” com {usage.model} (cobrado na tarifa de {usage.model}).")
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

    async def decide(self, req: DecisionRequest) -> tuple[Decision, Usage]:
        # Escalonamento pode ser OUTRO provedor, não só outro modelo: o despacho olha o tier.
        papel = "escalation" if req.tier > 0 else "decide"
        return await self._call(papel, req.ctx.run_id, lambda p: p.decide(req))

    async def verify(self, req: VerifyRequest) -> tuple[Verdict, Usage]:
        return await self._call("verify", req.ctx.run_id, lambda p: p.verify(req))

    async def generate_social_response(self, req: SocialRequest) -> tuple[SocialDraftDTO, Usage]:
        # Sem `run_id` (a prévia de persona nasce do portal): o teto por execução não se aplica, o do dia sim —
        # e era exatamente este o caminho que passava por fora de qualquer orçamento.
        return await self._call("social", None, lambda p: p.generate_social_response(req))


def _com_provedor(cfg: Config, papel: str, provedor: str) -> ResolvedRole:
    """A mesma função, resolvida contra OUTRO provedor — o declarado em `ai.roles.<papel>.fallback_provider`.

    O MODELO do destino sai de `ai.providers.<destino>.fallback_model`, quando declarado, e do `.env` quando não:
    o modelo do endpoint local quase nunca existe no provedor pago, então herdá-lo seria garantir um 404.
    """
    r = cfg.ai_role(papel)
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


def _valida_capacidade(cfg: Config, roles: dict[str, ResolvedRole]) -> None:
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
                f"ai.roles.{papel}: o modelo '{r.model}' está declarado sem visão em ai.models, mas "
                f"ai.image_policy={cfg.file.ai.image_policy} manda imagem para essa função. "
                "Aponte a função para um modelo com visão ou use ai.image_policy: never.")
