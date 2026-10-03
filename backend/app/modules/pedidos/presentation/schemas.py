"""Corpos das rotas de pedidos (item 28.9, adendo v0.45). `extra="forbid"` como `RunCreate`: campo desconhecido é 422,
e `pai_id`, `estado` e `criado_por` não se enviam (o primeiro é do 28.10; o segundo muda só por ação; o terceiro é o
operador da sessão). Só a FORMA é validada aqui; as regras de negócio (piso, sobreposição, limites) são do domínio e
voltam como `bloqueios` na prévia e como erro de contrato na criação."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.execution.presentation.schemas import DevicePolicy, RunTarget, RunTargetsResolveBody
from app.modules.pedidos.infrastructure.servico import CorpoDoPedido

Autonomia = Literal["observar", "preparar", "agir"]
Sobreposicao = Literal["pular", "guardar_uma", "permitir_todas"]
TipoDeGatilho = Literal["agora", "horario", "recorrencia", "evento", "condicao", "persona"]


class _Corpo(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AlvosCorpo(_Corpo):
    instance_ids: list[str] = Field(default_factory=list, max_length=64)
    profile_ids: list[str] = Field(default_factory=list, max_length=64)
    targets: list[RunTarget] = Field(default_factory=list, max_length=64)
    device_policy: DevicePolicy = "one"


class GatilhoCorpo(_Corpo):
    tipo: TipoDeGatilho
    spec: dict[str, object] = Field(default_factory=dict)


def utc(d: datetime | None) -> datetime | None:
    """Data sem fuso é UTC (o formato-base do contrato)."""
    if d is None:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def selecao(objetivo: str, a: AlvosCorpo) -> RunTargetsResolveBody:
    return RunTargetsResolveBody(command=objetivo, instance_ids=a.instance_ids, profile_ids=a.profile_ids,
                                 targets=a.targets, device_policy=a.device_policy)


class PedidoCorpo(_Corpo):
    objetivo: str = Field(min_length=3, max_length=4000)
    contexto: str | None = Field(default=None, max_length=4000)
    criterios_sucesso: list[str] | None = Field(default=None, max_length=50)
    alvos: AlvosCorpo
    autonomia: Autonomia = "observar"
    fuso: str = Field(default="America/Sao_Paulo", min_length=1, max_length=64)
    gatilhos: list[GatilhoCorpo] = Field(min_length=1, max_length=8)
    inicio_em: datetime | None = None
    fim_em: datetime | None = None
    max_ocorrencias: int | None = None
    orcamento_total_usd: float | None = None
    orcamento_ocorrencia_usd: float | None = None
    sobreposicao: Sobreposicao = "pular"
    janela_recuperacao_s: int | None = None
    coalescer: bool = True
    max_tentativas: int = 2
    pausa_por_falha: int = 3

    def para_corpo(self) -> CorpoDoPedido:
        return CorpoDoPedido(
            selecao=selecao(self.objetivo, self.alvos), gatilhos=tuple((g.tipo, g.spec) for g in self.gatilhos),
            contexto=self.contexto,
            criterios_sucesso=tuple(self.criterios_sucesso) if self.criterios_sucesso is not None else None,
            autonomia=self.autonomia, fuso=self.fuso, inicio_em=utc(self.inicio_em), fim_em=utc(self.fim_em),
            max_ocorrencias=self.max_ocorrencias, orcamento_total_usd=self.orcamento_total_usd,
            orcamento_ocorrencia_usd=self.orcamento_ocorrencia_usd, sobreposicao=self.sobreposicao,
            janela_recuperacao_s=self.janela_recuperacao_s, coalescer=self.coalescer,
            max_tentativas=self.max_tentativas, pausa_por_falha=self.pausa_por_falha)


class PreviaCorpo(PedidoCorpo):
    proximas: int = Field(default=5, ge=1, le=50)


class CriarCorpo(PedidoCorpo):
    idempotency_key: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")
    titulo: str | None = Field(default=None, min_length=1, max_length=120)
    confirmacao: str | None = Field(default=None, max_length=100)


class EdicaoCorpo(_Corpo):
    """`PATCH`: só os campos enviados mudam (`model_fields_set`). `versao` é o compare-and-set."""
    versao: int
    titulo: str | None = Field(default=None, min_length=1, max_length=120)
    objetivo: str | None = Field(default=None, min_length=3, max_length=4000)
    contexto: str | None = Field(default=None, max_length=4000)
    criterios_sucesso: list[str] | None = Field(default=None, max_length=50)
    alvos: AlvosCorpo | None = None
    autonomia: Autonomia | None = None
    fuso: str | None = Field(default=None, min_length=1, max_length=64)
    gatilhos: list[GatilhoCorpo] | None = Field(default=None, max_length=8)
    inicio_em: datetime | None = None
    fim_em: datetime | None = None
    max_ocorrencias: int | None = None
    orcamento_total_usd: float | None = None
    orcamento_ocorrencia_usd: float | None = None
    sobreposicao: Sobreposicao | None = None
    janela_recuperacao_s: int | None = None
    coalescer: bool | None = None
    max_tentativas: int | None = None
    pausa_por_falha: int | None = None
    dry_run: bool = False
    confirmacao: str | None = Field(default=None, max_length=100)


class AtivarCorpo(_Corpo):
    confirmacao: str = Field(min_length=1, max_length=100)


class PausarCorpo(_Corpo):
    motivo: str | None = Field(default=None, max_length=200)


class RetomarCorpo(_Corpo):
    modo: Literal["daqui", "recuperar"] | None = None


class LerAvisosCorpo(_Corpo):
    """`POST /api/pedidos/avisos/ler`: marca ids exatos, ou `todos` os informativos (os da caixa), opcionalmente só os de
    um pedido (`pedido_id`, extensão do adendo). Sem um dos dois não há o que marcar."""
    ids: list[str] | None = Field(default=None, max_length=200)
    todos: bool = False
    pedido_id: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _algo_para_marcar(self) -> LerAvisosCorpo:
        if not self.ids and not self.todos:
            raise ValueError("informe `ids` ou `todos`")
        return self


class CancelarCorpo(_Corpo):
    confirmar: bool = False
    motivo: str | None = Field(default=None, max_length=200)


class BuscaCorpo(BaseModel):
    """Corpo de `POST /api/pedidos/busca` (29.26): a mesma listagem de `GET /api/pedidos`, com o termo `q` (texto livre
    digitado pela pessoa, procurado no título e no objetivo) no corpo e não na URL: query string vira linha de log de
    acesso. Os demais filtros são os da listagem."""
    model_config = ConfigDict(extra="forbid")
    q: str = Field(min_length=1, max_length=80)
    estado: str | None = None
    autonomia: Literal["observar", "preparar", "agir"] | None = None
    tipo: Literal["agora", "horario", "recorrencia", "evento", "condicao", "persona"] | None = None
    profile_id: str | None = None
    pede_atencao: bool = False
    ordem: Literal["atualizado", "proxima", "criado"] = "atualizado"
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = None
