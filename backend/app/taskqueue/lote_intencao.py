"""Lote offline da intenção (R2 e R3 do 31.11; ADR-069 item 21): os comandos que a sombra da intenção JÁ mandou ao Jev,
remontados fora do caminho e só para leitura, para o braço offline medir a mesma amostra em inglês e em português (D-J7).

- **Só o que já saiu.** Entra a execução com uma linha de sombra de origem `intencao` desde `DESDE_ITEM_21` e com prova de
  POST: `postado=1` (083), ou, antes da 083, escolha preenchida ou um fallback que só existe depois da resposta
  (`FALLBACKS_DEPOIS_DO_POST`). Privacidade, `desligado`, `orcamento` e rede não provam POST e ficam de fora.
- **O mesmo código do runtime.** A execução é lida por `RunService.dados_da_sombra` (a foto de `runs.targets`), o catálogo
  e a RESOLVE pela composição de habilidades do `AppState` (`state.py`, a mesma ordem), e o pedido por
  `ConsumidorDeIntencao.pedido`. Nada é reescrito aqui: só os objetos que o `AppState` passaria, montados sobre o banco.
- **O mesmo texto (salvaguarda "c", 31.22).** O estado remontado passa por `privacidade.validar` e `redigir` e o hash dele
  (`porta.hash_do_estado`) é comparado ao `estado_hash` da linha (migração 086). Só o caso que bate é `c`.
- **Linha sem hash (anterior à 086, salvaguarda "b").** O hash não existe; a igualdade é inferida: o código do filtro igual
  em todo deploy da janela (conferido pelo script, que roda o `git`, e passado em `SalvaguardaB.codigo_igual`), nenhum nome
  saído do catálogo de destinos depois da linha (`ultima_remocao`) e os eventos alcançando a linha. O `b` só é contado:
  nunca vai ao Jev (orquestradora, 03/10 19:33Z), porque a identidade de arquivos não prova a igualdade do estado (a
  leitura da execução, o extrator e os nomes de app do filtro também decidem o texto). Só o hash prova.
- **Falha fechada.** Faltou qualquer peça, o caso fica de fora, contado pelo motivo (`LeituraDoLote.fora`).

Nada aqui grava: o script abre o banco só para leitura, e `ConsumidorDeIntencao.observar` (que casa a decisão real na
sombra) não é usado.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import Final, cast

from ..db import Database, Row, loads
from ..devices.manager import DeviceManager
from ..events import EventBus
from ..models import RunStatus
from ..modules.applications.infrastructure.app_repository import AppRepository
from ..modules.skills.application.registry import CompositeSkillRegistry
from ..modules.skills.domain.intent import IntentResolution
from ..modules.skills.infrastructure.document_validator import DslDocumentValidator, LockedVersions
from ..modules.skills.infrastructure.legacy_flows import LegacyFlowAdapter
from ..modules.skills.infrastructure.run_planning import SkillRunPlanner
from ..modules.skills.infrastructure.sql_repository import SqlSkillRepository
from ..planning.decisao_fechada import privacidade
from ..planning.decisao_fechada.contrato import PedidoDeDecisao
from ..planning.decisao_fechada.intencao import (PERGUNTA_CATALOGO, PERGUNTA_DESEMPATE, ConsumidorDeIntencao,
                                                 EntradaDeCatalogo)
from ..planning.decisao_fechada.porta import Porta, hash_do_estado
from ..planning.decisao_fechada.sombra import RepositorioDeSombra
from ..planning.provider import AIProvider
from .flows import FlowStore
from .repository import Repository
from .scheduler import Scheduler
from .service import RunService
from .sombra_intencao import cadeia_de, catalogo_de

#: ADR-069 item 21 (o dono, 03/10 ~18:39Z): o lote só reenvia comando que a sombra da intenção já mandou depois desta hora.
DESDE_ITEM_21: Final = "2026-10-03T15:29:51Z"
#: Linha anterior à 083 (sem `postado`): estes fallbacks só nascem depois da resposta do Jev, logo houve POST.
FALLBACKS_DEPOIS_DO_POST: Final = frozenset({"abaixo_do_limiar", "unknown_choice", "parse"})
#: D-J7: as instruções em português, pré-registradas em 03/10 antes de qualquer rodada do lote (golden set §3). Tradução
#: literal das instruções em inglês de `intencao.py`, que fica intocado: é um dos arquivos conferidos pela salvaguarda "b".
#: Só as instruções mudam; o estado e as opções são os mesmos, então o hash do estado também.
INSTRUCOES_PT: Final[Mapping[str, str]] = MappingProxyType({
    PERGUNTA_CATALOGO: (
        "O estado é um comando escrito pelo dono de um parque de aparelhos, em português, com nomes e números mascarados. "
        "Escolha a entrada do catálogo que este comando pede para executar, ou nenhuma se nenhuma entrada servir "
        "claramente."),
    PERGUNTA_DESEMPATE: (
        "O estado é um comando escrito pelo dono de um parque de aparelhos, em português, com nomes e números mascarados. "
        "Várias entradas do catálogo casam com ele igualmente. Escolha a que o comando pede, ou nenhuma se não der para "
        "saber."),
})
#: As mensagens persistidas (`events`, kind `log`) de quem tira um nome do catálogo de destinos. Fixadas aqui e conferidas
#: por teste contra o serviço social: mudar a mensagem lá sem mexer aqui quebra o teste em vez de abrir a salvaguarda.
MENSAGEM_PERFIL_REMOVIDO: Final = "Perfil removido"
MENSAGEM_CONTA_REMOVIDA: Final = "Conta removida do perfil"


def em_portugues(pedido: PedidoDeDecisao) -> PedidoDeDecisao:
    """O mesmo pedido com as instruções em português (D-J7). Pergunta sem tradução registrada é erro, não inglês calado."""
    return replace(pedido, perguntas=tuple(replace(p, instrucoes=INSTRUCOES_PT[p.id]) for p in pedido.perguntas))


def enviada(linha: Mapping[str, object]) -> bool:
    """A linha prova que o estado saiu num POST: `postado=1` (083); antes da 083, escolha ou fallback de depois da
    resposta. `postado=0` é a prova do contrário."""
    postado = linha.get("postado")
    if postado is not None:
        return str(postado) == "1"
    return linha.get("escolha") is not None or linha.get("fallback_reason") in FALLBACKS_DEPOIS_DO_POST


# ------------------------------------------------------------------ a composição do runtime, sobre o banco
class _ExecucaoComoASombraViu(Repository):
    """O `Repository` real, com o `run_row` no status do fim do `_plan`. A sombra lê a execução logo depois do plano (31.9);
    `dados_da_sombra` não observa a execução que falhou ou foi cancelada, mas a que falhou DEPOIS já tem a linha da sombra.
    O comando e a foto não mudam com o status."""

    def run_row(self, run_id: str) -> Row | None:
        run = super().run_row(run_id)
        return None if run is None else {**run, "status": RunStatus.planned.value}


@dataclass
class _SoOsFluxos:
    """O que o `RunService` lê do `Scheduler` ao nascer: os fluxos."""

    flows: FlowStore


@dataclass
class _ParqueDoBanco:
    """O que `RunService._catalogo` lê do `DeviceManager`: os ids dos aparelhos. Os não aposentados do banco, que são um
    SUPERCONJUNTO dos que o gerenciador carrega (ele ainda filtra por configuração e por hospedeiro): mais destinos tirados,
    texto menor, nunca exposição nova. Pode custar um hash diferente, que exclui o caso."""

    devices: dict[str, None] = field(default_factory=dict)


class LoteOffline:
    """A cadeia de intenção e a leitura da execução do runtime, montadas como o `AppState` monta (`state.py`), sobre um
    banco que o chamador abriu só para leitura. `skills_ligadas` e `fluxos_ligados` são o `skills.enabled` e o `ai.flows`
    da instalação."""

    def __init__(self, db: Database, *, skills_ligadas: bool, fluxos_ligados: bool) -> None:
        self.db = db
        apps = AppRepository(db)

        def pacote_do_app_id(app_id: str) -> str | None:        # `AppState._pacote_do_app_id`
            row = apps.obter(app_id)
            return row["package"] if row is not None else None

        travas = LockedVersions(lambda ref: repo.get(ref))
        validador = DslDocumentValidator(pacote_do_app_id, travas)
        repo = SqlSkillRepository(db, validador, adoption_enabled=lambda: skills_ligadas)
        fluxos = FlowStore(db)
        registro = CompositeSkillRegistry(repo, LegacyFlowAdapter(db, fluxos), skills_enabled=lambda: skills_ligadas,
                                          flows_enabled=lambda: fluxos_ligados)
        planner = SkillRunPlanner(registro, pacote_do_app_id, travas)
        self.resolver: Callable[[str, Sequence[str | None]], IntentResolution] = planner.resolve_intent
        self.catalogo: Callable[[], list[EntradaDeCatalogo]] = lambda: catalogo_de(
            lambda estado: registro.list(state=estado), registro.definition, skills_ligadas=skills_ligadas,
            fluxos_ligados=fluxos_ligados)
        parque = _ParqueDoBanco({str(r["id"]): None for r in db.query(
            "SELECT id FROM instances WHERE retired_at IS NULL ORDER BY id")})
        self.runs = RunService(_ExecucaoComoASombraViu(db, EventBus(db), Path(".")), cast(Scheduler, _SoOsFluxos(fluxos)),
                               cast(DeviceManager, parque), cast(AIProvider, None), skills=planner)


# ------------------------------------------------------------------ a leitura do lote
@dataclass(frozen=True)
class SalvaguardaB:
    """A salvaguarda "b", para a linha sem hash. `codigo_igual`: o script conferiu que os arquivos do filtro não mudaram
    entre cada deploy da janela e o código que roda o lote. Falso (o padrão) = nenhuma linha sem hash é `b`."""

    codigo_igual: bool = False


@dataclass(frozen=True)
class CasoDaIntencao:
    run_id: str
    app: str                          # o estrato: o app principal da execução, como no relatório do 31.10
    ts: str                           # a primeira linha da execução na sombra
    linhas: tuple[Row, ...]           # as linhas vivas da execução (R2 e/ou R3): rótulo e comparação com a sombra
    pedido: PedidoDeDecisao           # remontado, com as instruções do runtime (inglês)
    estado_hash: str                  # o hash do estado redigido remontado
    salvaguarda: str                  # "c": bateu com o hash da linha (só este vai); "b": sem hash, passaria na "b"


@dataclass(frozen=True)
class LeituraDoLote:
    casos: list[CasoDaIntencao]
    fora: Counter[str]
    ultima_remocao: str | None
    horizonte_dos_eventos: str | None


def ultima_remocao(db: Database, desde: str) -> str | None:
    """A última hora, desde `desde`, em que um nome PODE ter saído do catálogo de destinos: aparelho aposentado, conta
    retirada (lápide), persona ou conta removida (evento persistido) e persona ou conta alterada (`updated_at`: um nome
    trocado é um nome que saiu). Excluir demais é a direção segura."""
    marcas = [
        db.scalar("SELECT MAX(retired_at) FROM instances WHERE retired_at>=?", (desde,)),
        db.scalar("SELECT MAX(retirada_em) FROM contas_retiradas WHERE retirada_em>=?", (desde,)),
        db.scalar("SELECT MAX(ts) FROM events WHERE kind='log' AND ts>=? AND (message LIKE ? OR message LIKE ?)",
                  (desde, MENSAGEM_PERFIL_REMOVIDO + "%", MENSAGEM_CONTA_REMOVIDA + "%")),
        db.scalar("SELECT MAX(updated_at) FROM instagram_profiles WHERE updated_at>=?", (desde,)),
        db.scalar("SELECT MAX(updated_at) FROM profile_accounts WHERE updated_at>=?", (desde,)),
    ]
    validas = [str(m) for m in marcas if m]
    return max(validas) if validas else None


def _tem_foto(run: Mapping[str, object]) -> bool:
    foto = loads(str(run["targets"]), None) if run.get("targets") else None
    return isinstance(foto, dict) and isinstance(foto.get("alvos"), list)


def ler_lote(lote: LoteOffline, consumidor: ConsumidorDeIntencao, *, desde: str = DESDE_ITEM_21,
             salvaguarda_b: SalvaguardaB = SalvaguardaB()) -> LeituraDoLote:
    """Os casos do lote, um por execução, e os de fora contados por motivo. O pedido é o do runtime; `consumidor` só monta
    (`pedido`), nunca observa."""
    db = lote.db
    por_execucao: dict[str, list[Row]] = {}
    fora: Counter[str] = Counter()
    for linha in db.query("SELECT * FROM decisao_fechada_sombra WHERE origem='intencao' AND ts>=? ORDER BY ts, id",
                          (desde,)):
        if linha["run_id"]:
            por_execucao.setdefault(str(linha["run_id"]), []).append(linha)
        else:
            fora["sem_run_id"] += 1
    remocao = ultima_remocao(db, desde)
    primeiro_evento = db.scalar("SELECT MIN(ts) FROM events")     # a retenção apaga eventos: antes disto não se sabe
    horizonte = str(primeiro_evento) if primeiro_evento else None
    casos: list[CasoDaIntencao] = []
    for run_id, linhas in por_execucao.items():
        motivo, caso = _caso(lote, consumidor, run_id, linhas, salvaguarda_b, remocao, horizonte)
        if caso is None:
            fora[motivo] += 1
        else:
            casos.append(caso)
    return LeituraDoLote(casos, fora, remocao, horizonte)


def _caso(lote: LoteOffline, consumidor: ConsumidorDeIntencao, run_id: str, linhas: list[Row],
          salvaguarda_b: SalvaguardaB, remocao: str | None,
          horizonte: str | None) -> tuple[str, CasoDaIntencao | None]:
    """(motivo, caso). A ordem dos portões é a da falha fechada: nada é remontado de execução que não provou POST, e o
    hash é conferido ANTES de o caso existir."""
    if not any(enviada(linha) for linha in linhas):
        return "nao_enviado", None
    run = lote.db.one("SELECT * FROM runs WHERE id=?", (run_id,))
    if run is None:
        return "sem_execucao", None
    if not _tem_foto(run):
        return "sem_foto", None                       # anterior à 051: a pessoa do aparelho não está fotografada
    dados = lote.runs.dados_da_sombra(run_id)
    if dados is None:
        return "sem_dados", None
    comando, profile_ids, app, original, destinos = dados
    pedido = consumidor.pedido(run_id=run_id, comando=comando, app=app, catalogo=tuple(lote.catalogo()),
                               cadeia=cadeia_de(lote.resolver(comando, profile_ids)), original=original,
                               destinos=destinos)
    if pedido is None:
        return "sem_pergunta_hoje", None
    if not privacidade.validar(pedido).permitido:
        return "privacidade_hoje", None
    estado_hash = hash_do_estado(privacidade.redigir(pedido).estado)
    hashes = {str(linha["estado_hash"]) for linha in linhas if linha.get("estado_hash")}
    ts = str(linhas[0]["ts"])
    if hashes:
        if estado_hash not in hashes:
            return "hash_diferente", None
        salvaguarda = "c"
    elif not salvaguarda_b.codigo_igual:
        return "anterior_a_086", None
    elif horizonte is None or ts < horizonte:
        return "eventos_sem_alcance", None
    elif remocao is not None and ts <= remocao:              # no mesmo milissegundo não se sabe a ordem: fora
        return "antes_de_remocao", None
    else:
        salvaguarda = "b"
    return "", CasoDaIntencao(run_id=run_id, app=str(app or "sem_app"), ts=ts, linhas=tuple(linhas), pedido=pedido,
                              estado_hash=estado_hash, salvaguarda=salvaguarda)


def consumidor_do_lote(porta: Porta, db: Database) -> ConsumidorDeIntencao:
    """O consumidor do runtime, só para MONTAR o pedido (`pedido`): o repositório de sombra dele nunca é escrito aqui."""
    return ConsumidorDeIntencao(porta, RepositorioDeSombra(db))


__all__ = ["DESDE_ITEM_21", "FALLBACKS_DEPOIS_DO_POST", "INSTRUCOES_PT", "MENSAGEM_CONTA_REMOVIDA",
           "MENSAGEM_PERFIL_REMOVIDO", "CasoDaIntencao", "LeituraDoLote", "LoteOffline", "SalvaguardaB",
           "consumidor_do_lote", "em_portugues", "enviada", "ler_lote", "ultima_remocao"]
