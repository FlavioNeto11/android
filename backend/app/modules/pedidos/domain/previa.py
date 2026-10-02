"""Regras da prévia e da criação do pedido (item 28.9, adendo v0.45 de `docs/api-contract.md`).

Tudo aqui é PURO: recebe o corpo já normalizado em tipos simples e devolve o que a criação decidiria. A prévia e a
criação chamam as MESMAS funções (a criação "recalcula tudo o que a prévia calculou"), então os bloqueios da prévia
são, por construção, os erros que a criação devolveria.

* **Bloqueio** (`Bloqueio`): uma regra de negócio violada. A prévia os lista e segue; a criação recusa com o primeiro.
* **Selo** (`selo`): o resumo SHA-256 do que a pessoa viu. Opaco e estável para o mesmo conteúdo; muda se qualquer campo
  que a pessoa confirmou mudar. Datas calculadas (as próximas) e custo dependem do relógio e NÃO entram.
* **Id determinístico** (`id_do_pedido`): a 067 não tem coluna de chave de idempotência, então o id do pedido sai da
  `idempotency_key`. O `chave.py` aceita ids de até 28 caracteres de `[A-Za-z0-9_-]` e RECUSA (não trunca) o que passar;
  um SHA-256 inteiro (64 hex) não cabe. O `uuid5` da chave (128 bits, determinístico) em base64 sem preenchimento tem 22
  caracteres: `ped_` + 22 = 26. É o hash inteiro de um UUID, nunca um pedaço dele.
* **Piso de frequência** (§10): o menor intervalo entre as próximas 20 datas, somados todos os gatilhos, contra o piso da
  autonomia (`observar`/`preparar` 15 min, `agir` 1 h; configuração).

Puro: stdlib e o domínio de pedidos. Sem banco, sem relógio de parede (o `agora` entra por parâmetro).
"""
from __future__ import annotations

import base64
import hashlib
import json
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.modules.pedidos.domain import gatilhos as dominio_gatilhos
from app.modules.pedidos.domain import gatilhos_dinamicos, recorrencia
from app.modules.pedidos.domain.chave import TAMANHO_MAXIMO_DO_ID, formatar_instante
from app.modules.pedidos.domain.estados import (ATOR_PESSOA, AUTONOMIAS, PEDIDO_ATORES, SOBREPOSICOES,
                                                TIPOS_DE_GATILHO)

UTC = timezone.utc
LIMITE_DE_GATILHOS = 8
PROXIMAS_PADRAO = 5
DATAS_DO_INTERVALO = 20                      #: quantas datas entram no cálculo do intervalo mínimo
PAUSADO_PELA_PESSOA = "Pausado pela pessoa"
MOTIVO_MAXIMO = 200
#: Espaço de nomes FIXO do `uuid5` do id do pedido: trocá-lo mudaria todos os ids derivados de uma mesma chave.
_ESPACO_DE_IDS = uuid.UUID("5b1f3a6e-28c9-4d0e-9a52-2809c0de0928")


@dataclass(frozen=True)
class Bloqueio:
    """Uma regra de negócio violada: `codigo` é o do contrato; `campo` diz onde, quando faz sentido."""
    codigo: str
    mensagem: str
    campo: str | None = None

    def para_dict(self) -> dict[str, object]:
        d: dict[str, object] = {"codigo": self.codigo, "mensagem": self.mensagem}
        if self.campo is not None:
            d["campo"] = self.campo
        return d


@dataclass(frozen=True)
class GatilhoPedido:
    tipo: str
    spec: Mapping[str, object]               #: JÁ normalizada por `normalizar_gatilho`


@dataclass(frozen=True)
class ParametrosDoPedido:
    """O que a criação grava e o selo confirma, em tipos simples. Os alvos já vêm resolvidos pelo servidor."""
    objetivo_sem_destinos: str
    alvos: tuple[tuple[str, str | None, str | None], ...]        # (instance_id, profile_id, app_id), ordenados
    device_policy: str
    autonomia: str
    fuso: str
    gatilhos: tuple[GatilhoPedido, ...]
    inicio_em: datetime | None = None
    fim_em: datetime | None = None
    max_ocorrencias: int | None = None
    orcamento_total_usd: float | None = None
    orcamento_ocorrencia_usd: float | None = None
    sobreposicao: str = "pular"
    janela_recuperacao_s: int | None = None
    coalescer: bool = True
    max_tentativas: int = 2
    pausa_por_falha: int = 3


@dataclass(frozen=True)
class DataPrevista:
    gatilho: int
    instante: recorrencia.Instante

    def para_dict(self) -> dict[str, object]:
        return {"gatilho": self.gatilho, **self.instante.para_dict()}


@dataclass(frozen=True)
class Analise:
    bloqueios: tuple[Bloqueio, ...]
    proximas: tuple[DataPrevista, ...]
    intervalo_minimo_s: int | None
    alertas: tuple[Bloqueio, ...] = field(default_factory=tuple)


# ------------------------------------------------------------------ identidade
def id_do_pedido(idempotency_key: str) -> str:
    """`ped_` + o `uuid5` INTEIRO da chave em base64 url-safe sem preenchimento (26 caracteres, cabe em `chave.py`)."""
    bruto = uuid.uuid5(_ESPACO_DE_IDS, idempotency_key).bytes
    texto = "ped_" + base64.urlsafe_b64encode(bruto).decode("ascii").rstrip("=")
    assert len(texto) <= TAMANHO_MAXIMO_DO_ID
    return texto


def acoes_permitidas(estado: str) -> list[str]:
    """Pela tabela de `estados.py` (as arestas da PESSOA), mais `editar` e as de gesto que não mudam de estado.
    `executar` e `backfill` só existem em `ativo`; o 28.9 ainda não as expõe, então não são oferecidas."""
    acoes: list[str] = []
    destinos = {para for (de, para), atores in PEDIDO_ATORES.items() if de == estado and ATOR_PESSOA in atores}
    if estado in ("rascunho", "ativo", "pausado", "aguardando_pessoa"):
        acoes.append("editar")
    if "ativo" in destinos and estado == "rascunho":
        acoes.append("ativar")
    if "pausado" in destinos:
        acoes.append("pausar")
    if "ativo" in destinos and estado in ("pausado", "aguardando_pessoa"):
        acoes.append("retomar")
    if "cancelado" in destinos:
        acoes.append("cancelar")
    return acoes


# ------------------------------------------------------------------ gatilhos
class ErroDeCorpo(ValueError):
    """Corpo com regra violada; leva o código do contrato."""

    def __init__(self, codigo: str, mensagem: str, campo: str | None = None):
        super().__init__(mensagem)
        self.bloqueio = Bloqueio(codigo, mensagem, campo)


def _local_ingenuo(valor: object, campo: str) -> datetime:
    if not isinstance(valor, str):
        raise ErroDeCorpo("recorrencia_invalida", f"`{campo}` precisa ser uma hora local ISO sem fuso "
                          "(AAAA-MM-DDTHH:MM:SS).", campo)
    try:
        dt = datetime.fromisoformat(valor)
    except ValueError:
        raise ErroDeCorpo("recorrencia_invalida", f"`{campo}` não é uma data e hora válida: {valor!r}.", campo) from None
    if dt.tzinfo is not None:
        raise ErroDeCorpo("recorrencia_invalida", f"`{campo}` não pode trazer fuso: a hora é local, no fuso do pedido.",
                          campo)
    return dt.replace(microsecond=0)


def conferir_fuso(fuso: str) -> str:
    try:
        recorrencia.carregar_fuso(fuso)
    except recorrencia.ErroRecorrencia as e:
        raise ErroDeCorpo("fuso_desconhecido", str(e), "fuso") from None
    return fuso.strip()


def normalizar_gatilho(tipo: str, spec: Mapping[str, object], fuso: str, indice: int = 0, *,
                       efemeros: Iterable[str] = ()) -> GatilhoPedido:
    """A forma que o laço lê (`gatilhos.py`), com a `rrule` na forma canônica. `horario` aceita `local` (o que o laço
    lê) e `dtstart` (o nome que o contrato propôs): grava sempre `local`. `evento`, `condicao` e `persona` (28.8) são
    validados em `gatilhos_dinamicos`; `efemeros` são os tipos de evento que nunca vão ao log (`EPHEMERAL_KINDS`)."""
    campo = f"gatilhos[{indice}]"
    if tipo not in TIPOS_DE_GATILHO:
        raise ErroDeCorpo("gatilho_nao_suportado", f"Tipo de gatilho desconhecido: {tipo!r}.", campo)
    if tipo in gatilhos_dinamicos.DINAMICOS:
        try:
            return GatilhoPedido(tipo, gatilhos_dinamicos.normalizar(tipo, spec, efemeros=efemeros))
        except gatilhos_dinamicos.SpecInvalida as e:
            raise ErroDeCorpo("gatilho_invalido", str(e), f"{campo}.{e.campo}") from None
    if tipo not in dominio_gatilhos.SUPORTADOS:
        raise ErroDeCorpo("gatilho_nao_suportado", f"O gatilho `{tipo}` ainda não existe (vem com o item 28.8).", campo)
    if tipo == "agora":
        return GatilhoPedido("agora", {})
    if tipo == "horario":
        bruto = spec.get("local", spec.get("dtstart"))
        local = _local_ingenuo(bruto, f"{campo}.spec.local")
        try:
            recorrencia.localizar(local, fuso)
        except recorrencia.ErroRecorrencia as e:
            raise ErroDeCorpo("recorrencia_invalida", str(e), campo) from None
        return GatilhoPedido("horario", {"local": local.isoformat()})
    inicio = _local_ingenuo(spec.get("dtstart"), f"{campo}.spec.dtstart")
    texto = spec.get("rrule")
    if not isinstance(texto, str):
        raise ErroDeCorpo("recorrencia_invalida", "A recorrência precisa de `rrule` (ex.: FREQ=DAILY;BYHOUR=8).",
                          f"{campo}.spec.rrule")
    try:
        regra = recorrencia.interpretar(texto)
        recorrencia.proximas(regra, inicio, fuso, depois_de=datetime(2000, 1, 1, tzinfo=UTC), limite=1)
    except recorrencia.ErroRecorrencia as e:
        raise ErroDeCorpo("recorrencia_invalida", str(e), f"{campo}.spec.rrule") from None
    return GatilhoPedido("recorrencia", {"dtstart": inicio.isoformat(), "rrule": regra.para_texto()})


def _datas(g: GatilhoPedido, indice: int, fuso: str, agora: datetime, limite: int) -> list[DataPrevista]:
    """As próximas datas do gatilho a partir de `agora`, ordenadas."""
    if g.tipo in ("agora", "persona"):         # a persona faz a primeira visita na ativação; as outras dependem dela
        local = agora.astimezone(recorrencia.carregar_fuso(fuso)).replace(tzinfo=None, microsecond=0)
        return [DataPrevista(indice, recorrencia.localizar(local, fuso))]
    if g.tipo in ("evento", "condicao"):      # sem data: dependem do que acontecer
        return []
    if g.tipo == "horario":
        inst = recorrencia.localizar(datetime.fromisoformat(str(g.spec["local"])), fuso)
        return [DataPrevista(indice, inst)] if inst.utc > agora else []
    regra = recorrencia.interpretar(str(g.spec["rrule"]))
    inicio = datetime.fromisoformat(str(g.spec["dtstart"]))
    return [DataPrevista(indice, i) for i in recorrencia.proximas(regra, inicio, fuso, depois_de=agora, limite=limite)]


def proximas_do_pedido(gatilhos: Sequence[GatilhoPedido], fuso: str, agora: datetime, quantas: int,
                       fim_em: datetime | None = None) -> list[DataPrevista]:
    """As próximas datas de um pedido JÁ criado: o gatilho `agora` não conta (já foi materializado na ativação), nem a
    persona (a visita seguinte só existe quando a anterior fecha; quando existe, é uma `prevista` no banco)."""
    todas: list[DataPrevista] = []
    for i, g in enumerate(gatilhos):
        if g.tipo in ("agora", "persona"):
            continue
        try:
            todas.extend(_datas(g, i, fuso, agora, quantas))
        except (recorrencia.ErroRecorrencia, ValueError, KeyError):
            continue                  # spec que o laço também recusa: sem data, nunca um erro na leitura
    todas.sort(key=lambda d: (d.instante.utc, d.gatilho))
    if fim_em is not None:
        todas = [d for d in todas if d.instante.utc <= fim_em]
    return todas[:quantas]


def descrever_gatilho(g: GatilhoPedido | tuple[str, Mapping[str, object]], fuso: str) -> str:
    """Texto curto para a pessoa ("Todo dia às 08:00 (America/Sao_Paulo)"). Cobre os casos comuns; o resto cai na
    regra em forma canônica, que é fiel mesmo quando não é bonita."""
    tipo, spec = (g.tipo, g.spec) if isinstance(g, GatilhoPedido) else g
    if tipo == "agora":
        return "Uma vez, agora"
    if tipo == "horario":
        return f"Uma vez, em {str(spec.get('local', '?')).replace('T', ' às ')} ({fuso})"
    if tipo in gatilhos_dinamicos.DINAMICOS:
        return gatilhos_dinamicos.descrever(tipo, spec)
    if tipo != "recorrencia":
        return tipo
    try:
        regra = recorrencia.interpretar(str(spec.get("rrule", "")))
    except recorrencia.ErroRecorrencia:
        return f"Recorrência inválida ({fuso})"
    hora = ""
    if len(regra.horas) == 1:
        hora = f" às {regra.horas[0]:02d}:{(regra.minutos[0] if regra.minutos else 0):02d}"
    n = regra.intervalo
    if regra.freq == "HOURLY":
        quando = "A cada hora" if n == 1 else f"A cada {n} horas"
        if regra.minutos:
            quando += " no minuto " + ", ".join(f"{m:02d}" for m in regra.minutos)
        return f"{quando} ({fuso})"
    unidade = {"DAILY": ("Todo dia", "dias"), "WEEKLY": ("Toda semana", "semanas"),
               "MONTHLY": ("Todo mês", "meses")}[regra.freq]
    quando = unidade[0] if n == 1 else f"A cada {n} {unidade[1]}"
    if regra.dias_semana:
        quando += " (" + ", ".join(_NOMES_DOS_DIAS[d] for d in regra.dias_semana) + ")"
    return f"{quando}{hora} ({fuso})"


_NOMES_DOS_DIAS = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")


# ------------------------------------------------------------------ validação (prévia e criação)
def _piso(autonomia: str, piso_observar_s: int, piso_agir_s: int) -> int:
    return piso_agir_s if autonomia == "agir" else piso_observar_s


def analisar(p: ParametrosDoPedido, *, agora: datetime, piso_observar_s: int, piso_agir_s: int,
             quantas: int = PROXIMAS_PADRAO) -> Analise:
    """Todas as regras de negócio do corpo, na ordem do contrato, sem parar na primeira. Devolve as próximas datas
    (as `quantas` primeiras por instante) e o menor intervalo entre as 20 primeiras."""
    b: list[Bloqueio] = []
    a: list[Bloqueio] = []
    if p.autonomia not in AUTONOMIAS:
        b.append(Bloqueio("limite_invalido", f"Autonomia desconhecida: {p.autonomia!r}.", "autonomia"))
    if p.sobreposicao not in SOBREPOSICOES:
        b.append(Bloqueio("sobreposicao_incompativel", f"Sobreposição desconhecida: {p.sobreposicao!r}.",
                          "sobreposicao"))
    elif p.autonomia == "agir" and p.sobreposicao != "pular":
        b.append(Bloqueio("sobreposicao_incompativel", "Um pedido que age (efeito externo) só aceita `pular`: duas "
                          "ocorrências ao mesmo tempo repetiriam o efeito.", "sobreposicao"))
    elif p.sobreposicao == "permitir_todas" and p.autonomia != "observar":
        b.append(Bloqueio("sobreposicao_incompativel", "`permitir_todas` só vale para quem apenas observa.",
                          "sobreposicao"))
    # limites: os mesmos CHECKs da 067, com a mensagem para a pessoa
    if p.inicio_em and p.fim_em and p.fim_em <= p.inicio_em:
        b.append(Bloqueio("limite_invalido", "O fim precisa ser depois do início.", "fim_em"))
    if p.max_ocorrencias is not None and p.max_ocorrencias <= 0:
        b.append(Bloqueio("limite_invalido", "O máximo de ocorrências precisa ser maior que zero.", "max_ocorrencias"))
    for campo, valor in (("orcamento_total_usd", p.orcamento_total_usd),
                         ("orcamento_ocorrencia_usd", p.orcamento_ocorrencia_usd)):
        if valor is not None and valor < 0:
            b.append(Bloqueio("limite_invalido", "O orçamento não pode ser negativo.", campo))
    if (p.orcamento_total_usd is not None and p.orcamento_ocorrencia_usd is not None
            and p.orcamento_ocorrencia_usd > p.orcamento_total_usd):
        b.append(Bloqueio("limite_invalido", "O orçamento por ocorrência passa do orçamento total.",
                          "orcamento_ocorrencia_usd"))
    if p.janela_recuperacao_s is not None and p.janela_recuperacao_s < 0:
        b.append(Bloqueio("limite_invalido", "A janela de recuperação não pode ser negativa.", "janela_recuperacao_s"))
    if p.max_tentativas < 1:
        b.append(Bloqueio("limite_invalido", "As tentativas por ocorrência começam em 1.", "max_tentativas"))
    if p.pausa_por_falha < 1:
        b.append(Bloqueio("limite_invalido", "A pausa por falhas seguidas começa em 1.", "pausa_por_falha"))
    if not 1 <= len(p.gatilhos) <= LIMITE_DE_GATILHOS:
        b.append(Bloqueio("limite_invalido", f"Informe de 1 a {LIMITE_DE_GATILHOS} gatilhos.", "gatilhos"))

    todas: list[DataPrevista] = []
    for i, g in enumerate(p.gatilhos):
        try:
            todas.extend(_datas(g, i, p.fuso, agora, DATAS_DO_INTERVALO))
        except recorrencia.ErroRecorrencia as e:
            b.append(Bloqueio("recorrencia_invalida", str(e), f"gatilhos[{i}]"))
    todas.sort(key=lambda d: (d.instante.utc, d.gatilho))
    if p.inicio_em:
        todas = [d for d in todas if d.instante.utc >= p.inicio_em]
        a.append(Bloqueio("inicio_em_nao_aplicado", "O início é gravado, mas o laço de pedidos ainda não o aplica ao "
                          "materializar (28.4): as datas valem a partir da ativação."))
    if p.fim_em:
        todas = [d for d in todas if d.instante.utc <= p.fim_em]
    intervalo: int | None = None
    janela = todas[:DATAS_DO_INTERVALO]
    if len(janela) >= 2:
        intervalo = int(min((y.instante.utc - x.instante.utc).total_seconds() for x, y in zip(janela, janela[1:])))
    piso = _piso(p.autonomia, piso_observar_s, piso_agir_s)
    for i, g in enumerate(p.gatilhos):
        # A persona escolhe quando volta, mas nunca abaixo do piso da autonomia (o evento respeita o piso no laço).
        minimo = g.spec.get("intervalo_min_s") if g.tipo == "persona" else None
        if isinstance(minimo, int) and minimo < piso:
            b.append(Bloqueio("frequencia_abaixo_do_piso", f"A persona pode voltar a cada {minimo} s e o mínimo para "
                              f"`{p.autonomia}` é {piso} s (piso_s={piso}, observado_s={minimo}).",
                              f"gatilhos[{i}].spec.intervalo_min_s"))
    if p.gatilhos and all(g.tipo == "condicao" for g in p.gatilhos):
        b.append(Bloqueio("condicao_sem_observacao", "A condição é avaliada nas observações das ocorrências: junte a ela "
                          "um gatilho que observe (recorrência, horário, evento ou persona).", "gatilhos"))
    if intervalo is not None and intervalo < piso:
        b.append(Bloqueio("frequencia_abaixo_do_piso", f"O menor intervalo entre as ocorrências é {intervalo} s e o "
                          f"mínimo para `{p.autonomia}` é {piso} s (piso_s={piso}, observado_s={intervalo}).",
                          "gatilhos"))
    return Analise(tuple(b), tuple(todas[:quantas]), intervalo, tuple(a))


# ------------------------------------------------------------------ selo
def forma_canonica(p: ParametrosDoPedido) -> dict[str, object]:
    """O que o selo cobre (contrato): objetivo sem destinos, alvos resolvidos, política, autonomia, fuso, gatilhos
    (rrule canônica), limites, orçamentos, sobreposição, janela, coalescência, tentativas e pausa. Não entram
    título, contexto, critérios, nem datas calculadas e custo."""
    def inst(d: datetime | None) -> str | None:
        return None if d is None else formatar_instante(d)
    return {
        "objetivo": p.objetivo_sem_destinos,
        "alvos": [list(x) for x in p.alvos], "device_policy": p.device_policy,
        "autonomia": p.autonomia, "fuso": p.fuso,
        "gatilhos": [{"tipo": g.tipo, "spec": dict(g.spec)} for g in p.gatilhos],
        "inicio_em": inst(p.inicio_em), "fim_em": inst(p.fim_em), "max_ocorrencias": p.max_ocorrencias,
        "orcamento_total_usd": p.orcamento_total_usd, "orcamento_ocorrencia_usd": p.orcamento_ocorrencia_usd,
        "sobreposicao": p.sobreposicao, "janela_recuperacao_s": p.janela_recuperacao_s, "coalescer": p.coalescer,
        "max_tentativas": p.max_tentativas, "pausa_por_falha": p.pausa_por_falha,
    }


def selo(p: ParametrosDoPedido) -> str:
    texto = json.dumps(forma_canonica(p), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(texto.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ autonomia e custo
_EFEITO_EXTERNO = ["curtir", "seguir", "comentar", "mandar mensagem", "publicar"]


def autonomia_da_previa(teto: str) -> dict[str, object]:
    """§6.4, sem IA: o que o teto escolhido significa. A persona só afunila (vale o mais restritivo dos dois); compra e
    pagamento, desafio/CAPTCHA/2FA e conta travada ficam de fora em qualquer grau. Quais capacidades o PLANO usará só
    se sabe depois de planejar, então a lista é a do grau, não a do plano."""
    nunca = ["compra ou pagamento"]
    if teto == "observar":
        return {"teto": teto, "exige_aprovacao": [], "recusado": [*_EFEITO_EXTERNO, *nunca]}
    if teto == "preparar":
        return {"teto": teto, "exige_aprovacao": list(_EFEITO_EXTERNO), "recusado": nunca}
    return {"teto": teto, "exige_aprovacao": ["o que a política da persona exigir"], "recusado": nunca}


def custo_da_previa(ultimos: Sequence[float], teto_ocorrencia: float | None, ocorrencias_por_mes: float | None
                    ) -> dict[str, object]:
    """Sem número inventado (contrato): a base é a mediana das últimas 5 ocorrências com execução, senão o teto por
    ocorrência, senão `sem_base` com tudo `null`."""
    if ultimos:
        ordenados = sorted(ultimos)
        meio = len(ordenados) // 2
        por = ordenados[meio] if len(ordenados) % 2 else (ordenados[meio - 1] + ordenados[meio]) / 2
        base = "mediana_das_ultimas_5"
    elif teto_ocorrencia is not None:
        por, base = float(teto_ocorrencia), "teto_por_ocorrencia"
    else:
        return {"base": "sem_base", "por_ocorrencia_usd": None, "ocorrencias_por_mes": ocorrencias_por_mes,
                "por_mes_usd": None}
    mes = None if ocorrencias_por_mes is None else round(por * ocorrencias_por_mes, 6)
    return {"base": base, "por_ocorrencia_usd": round(por, 6), "ocorrencias_por_mes": ocorrencias_por_mes,
            "por_mes_usd": mes}


def ocorrencias_por_mes(intervalo_s: int | None) -> float | None:
    """Estimativa pelo MENOR intervalo (o teto do ritmo). `None` sem intervalo (uma data só)."""
    if not intervalo_s or intervalo_s <= 0:
        return None
    return round(timedelta(days=30).total_seconds() / intervalo_s, 2)


__all__ = ["Analise", "Bloqueio", "DATAS_DO_INTERVALO", "DataPrevista", "ErroDeCorpo", "GatilhoPedido",
           "LIMITE_DE_GATILHOS", "PAUSADO_PELA_PESSOA", "ParametrosDoPedido", "PROXIMAS_PADRAO", "acoes_permitidas",
           "analisar", "autonomia_da_previa", "conferir_fuso", "custo_da_previa", "descrever_gatilho",
           "forma_canonica", "id_do_pedido", "normalizar_gatilho", "ocorrencias_por_mes", "proximas_do_pedido", "selo", "MOTIVO_MAXIMO"]
