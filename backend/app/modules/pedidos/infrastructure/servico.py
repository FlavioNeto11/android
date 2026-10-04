"""O serviço da API de pedidos (item 28.9, adendo v0.45): prévia, criação, ações, leitura, snapshot e eventos.

A API **solicita e acompanha**: quem materializa, despacha e fecha é o laço (28.4). Aqui só entram as regras de borda,
todas dos módulos de domínio (`domain/previa.py`, `estados.py`) e das ações da pessoa (`acoes.py`):

* a prévia e a criação passam pelo MESMO `_montar` (alvos resolvidos pelo resolvedor de sempre, credencial recusada,
  `analisar` do domínio): os bloqueios da prévia são, por construção, os erros que a criação devolveria;
* repetir é seguro: criação pela `idempotency_key` (id determinístico, sem coluna nova); ação de estado repetida no
  estado que ela já produziu é `sem_mudanca`;
* a leitura monta `PedidoView` do banco (nada em memória) e o `aguardando_pessoa` leva as pendências do ESTADO VIVO
  (execução `needs_input`, aprovação pendente, ocorrência `incerta`), que a caixa de Pendências agrupa sob o pedido;
* os eventos `pedido.updated`, `pedido.ocorrencia.updated` e `pedido.aviso` saem das MARCAS que o repositório anota
  (`RepositorioDePedidos.marcar`) e são descarregadas depois do commit, por quem escreveu.

Os avisos são linhas de `pedido_avisos` (migração 072): TODO `pedido.aviso` é gravado antes de ser emitido, com
`chave_dedupe` (`CaixaDeAvisos.registrar`), venha da API ou do laço; a rota os lê da tabela e `POST /avisos/ler` marca
`lido_em`.
"""
from __future__ import annotations

import base64
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from app.config import PedidosCfg
from app.db import Database, Row, dumps, loads
from app.events import EPHEMERAL_KINDS
from app.models import RunTargetsPreview
from app.modules.execution.presentation.schemas import RunTargetsResolveBody
from app.modules.pedidos.domain import avisos as dominio_avisos
from app.modules.pedidos.domain import colaboracao
from app.modules.pedidos.domain import gatilhos as dominio_gatilhos
from app.modules.pedidos.domain import previa
from app.modules.pedidos.domain.estados import ATOR_PESSOA, PEDIDO_ATORES, TransicaoInvalida, transicionar_pedido
from app.modules.pedidos.infrastructure.acoes import AcaoInvalida
from app.modules.pedidos.infrastructure.avisos import MAXIMO_DE_IDS, CaixaDeAvisos
from app.modules.pedidos.infrastructure.laco import LacoDePedidos
from app.modules.pedidos.infrastructure.repositorio_memoria import RepositorioDeMemoria
from app.modules.pedidos.infrastructure.repositorio import novo_id
from app.shared.costuras import PAINEL
from app.taskqueue.service import RunError, RunService
from app.util import parse_iso, to_iso

log = logging.getLogger("poc.pedidos")

JsonObject = dict[str, object]
ESTADOS = ("rascunho", "ativo", "pausado", "aguardando_pessoa", "concluido", "encerrado", "cancelado")
TERMINAIS = ("concluido", "encerrado", "cancelado")
#: O status HTTP de cada bloqueio quando é a CRIAÇÃO que o devolve (na prévia nenhum vira erro).
STATUS_DO_BLOQUEIO: Mapping[str, int] = {"sem_alvo": 400, "unknown_instance": 400, "store_instance": 400, "alvos_nao_confirmados": 409, "alvos_com_pergunta": 409,
                                         "credencial_no_comando": 409}


class ErroDeApi(Exception):
    """Erro com o formato do contrato: `{detail: {code, message, ...}}`."""

    def __init__(self, status: int, code: str, message: str, **details: object):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


@dataclass(frozen=True)
class CorpoDoPedido:
    """`PedidoCorpo` em tipos simples (a camada de apresentação valida a forma e converte)."""
    selecao: RunTargetsResolveBody                      # `command` = o objetivo; o resto, os alvos
    gatilhos: tuple[tuple[str, Mapping[str, object]], ...]
    contexto: str | None = None
    criterios_sucesso: tuple[str, ...] | None = None
    autonomia: str = "observar"
    fuso: str = "America/Sao_Paulo"
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
    #: Colaboração entre pedidos (28.10, F1). Todos opcionais; com a colaboração desligada qualquer um deles é recusado.
    pai_id: str | None = None
    papel: str | None = None
    dependencias: tuple[tuple[str, str], ...] = ()          # (de, tipo): o pedido NOVO depende de `de`


@dataclass(frozen=True)
class Montado:
    """O que a prévia e a criação calcularam: parâmetros selados, análise, alvos e o que vai para o banco."""
    parametros: previa.ParametrosDoPedido | None
    analise: previa.Analise
    bloqueios: tuple[previa.Bloqueio, ...]
    preview: RunTargetsPreview | None
    alvos_json: str | None


def _chave_alvo(t: tuple[str, str | None, str | None]) -> tuple[str, str, str]:
    return (t[0], t[1] or "", t[2] or "")


class PedidosApi:
    def __init__(self, db: Database, laco: LacoDePedidos, runs: RunService, emitir: Callable[..., object],
                 cfg: PedidosCfg):
        self.db = db
        self.laco = laco
        self.repo = laco.repo
        self.acoes = laco.acoes
        self.runs = runs
        self.emitir = emitir
        self.cfg = cfg
        self.memoria = RepositorioDeMemoria(db)
        #: O ponto único dos avisos: o laço (`AppState`: `avisar`) e esta API gravam e emitem pelo MESMO objeto.
        self.caixa = CaixaDeAvisos(db, emitir, self.agora)

    def agora(self) -> datetime:
        return self.laco.relogio()

    # ================================================================== alvos e montagem (prévia e criação)
    def _resolver(self, selecao: RunTargetsResolveBody) -> tuple[RunTargetsPreview | None, previa.Bloqueio | None]:
        try:
            RunService._recusar_credencial(selecao.command)
            return self.runs.previa_de_alvos(selecao), None
        except RunError as e:
            if e.code == "credencial_no_comando":
                raise ErroDeApi(409, e.code, e.message) from None      # nada é processado nem ecoado
            return None, previa.Bloqueio(e.code, e.message, "alvos")

    def _validar_colaboracao(self, corpo: CorpoDoPedido, pid: str | None) -> previa.Bloqueio | None:
        """A estrutura (F1) e, com ela válida, o teto de autonomia do papel (F3): a pessoa corrige primeiro a árvore."""
        b = self._validar_estrutura(corpo, pid)
        if b is not None or corpo.papel is None or not self.cfg.colaboracao.enabled:
            return b
        r = colaboracao.validar_autonomia(corpo.autonomia, corpo.papel)
        return previa.Bloqueio(r.codigo, r.mensagem, r.campo or "") if r else None

    def _validar_estrutura(self, corpo: CorpoDoPedido, pid: str | None) -> previa.Bloqueio | None:
        """A estrutura de pai, papel e dependências (28.10, F1), pelo domínio. `pid` é o id que o pedido terá (na prévia
        não se sabe: a chave de idempotência só chega na criação). Pedido que já existe é repetição da mesma chave: a
        estrutura dele foi conferida quando nasceu e o pai pode ter terminado desde então, então não se confere de novo."""
        if corpo.pai_id is None and corpo.papel is None and not corpo.dependencias:
            return None
        cfg = self.cfg.colaboracao
        if not cfg.enabled:
            campo = "pai_id" if corpo.pai_id is not None else ("papel" if corpo.papel is not None else "dependencias")
            return previa.Bloqueio("colaboracao_desligada", "A colaboração entre pedidos está desligada nesta instalação "
                                   "(`pedidos.colaboracao.enabled`): `pai_id`, `papel` e `dependencias` não valem.", campo)
        if pid is not None and self.repo.pedido(pid) is not None:
            return None
        if corpo.pai_id is None:
            r = colaboracao.validar_raiz(papel=corpo.papel, dependencias=corpo.dependencias)
            return previa.Bloqueio(r.codigo, r.mensagem, r.campo or "") if r else None
        novo = pid or "(novo)"
        pai_row = self.repo.pedido(corpo.pai_id)
        pai = None if pai_row is None else colaboracao.DadosDoPai(
            id=pai_row["id"], estado=pai_row["estado"], pai_id=pai_row["pai_id"],
            orcamento_total_usd=pai_row["orcamento_total_usd"], gasto_usd=self.repo.custo_total(pai_row["id"]))
        pais = self.repo.pais_dos_ancestrais(corpo.pai_id)
        cadeia = colaboracao.cadeia_de_pais(corpo.pai_id, pais) or (corpo.pai_id,)
        raiz = cadeia[-1]
        familia = [r for r in [self.repo.pedido(raiz), *self.repo.descendentes(raiz)] if r is not None and r["id"] != novo]
        irmaos = [colaboracao.Irmao(f["id"], f["papel"], f["orcamento_total_usd"]) for f in self.repo.filhos(corpo.pai_id)
                  if f["id"] != novo]
        dependidos: dict[str, str | None] = {}
        for de, _tipo in corpo.dependencias:
            linha = self.repo.pedido(de)
            if linha is not None:
                dependidos[de] = linha["pai_id"]
        arestas = [(d["de"], d["para"]) for d in self.repo.dependencias_entre([r["id"] for r in familia])]
        r = colaboracao.validar_filho(
            novo_id=novo, pai=pai, pai_id=corpo.pai_id, pais=pais, irmaos=irmaos,
            papeis_da_familia=[f["papel"] for f in familia], papel=corpo.papel, dependencias=corpo.dependencias,
            pai_dos_dependidos=dependidos, arestas_da_familia=arestas, orcamento_total_usd=corpo.orcamento_total_usd,
            limites=colaboracao.Limites(cfg.max_profundidade, cfg.max_filhos))
        return previa.Bloqueio(r.codigo, r.mensagem, r.campo or "") if r else None

    def _montar(self, corpo: CorpoDoPedido, pid: str | None = None) -> Montado:
        bloqueios: list[previa.Bloqueio] = []
        preview, erro = self._resolver(corpo.selecao)
        alvos: list[tuple[str, str | None, str | None]] = []
        if erro is not None:
            bloqueios.append(erro)
        elif preview is not None:
            alvos = sorted(((t.instance_id, t.profile_id, t.app_id) for t in preview.targets), key=_chave_alvo)
            if preview.questions:
                bloqueios.append(previa.Bloqueio("alvos_com_pergunta", "Os alvos ainda precisam de uma decisão sua "
                                                 "(veja `alvos.questions`).", "alvos"))
            if any(t.origem == "texto" for t in preview.targets):
                bloqueios.append(previa.Bloqueio("alvos_nao_confirmados", "O objetivo cita destinos que ainda não "
                                                 "foram confirmados: escolha-os na seleção.", "alvos"))
            aparelhos = self.runs.devices.devices
            desconhecidos = sorted({a[0] for a in alvos if a[0] not in aparelhos})
            if desconhecidos:
                bloqueios.append(previa.Bloqueio("unknown_instance", "Instância(s) desconhecida(s): "
                                                 + ", ".join(desconhecidos) + ".", "alvos"))
            lojas = sorted({a[0] for a in alvos if a[0] in aparelhos and aparelhos[a[0]].store})
            if lojas:
                bloqueios.append(previa.Bloqueio("store_instance", f"{', '.join(lojas)} é a loja (Play Store): ela só "
                                                 "guarda o aplicativo oficial e não executa tarefas.", "alvos"))
            if not alvos and not preview.questions:
                bloqueios.append(previa.Bloqueio("sem_alvo", "Os alvos não resolvem a nenhum aparelho.", "alvos"))
        fuso = corpo.fuso
        try:
            fuso = previa.conferir_fuso(corpo.fuso)
        except previa.ErroDeCorpo as e:
            bloqueios.append(e.bloqueio)
        gatilhos: list[previa.GatilhoPedido] = []
        if fuso == corpo.fuso.strip():
            for i, (tipo, spec) in enumerate(corpo.gatilhos):
                try:
                    gatilhos.append(previa.normalizar_gatilho(tipo, spec, fuso, i,
                                                                efemeros=EPHEMERAL_KINDS))
                except previa.ErroDeCorpo as e:
                    bloqueios.append(e.bloqueio)
        sem_destinos = preview.command_sem_destinos if preview is not None else corpo.selecao.command
        parametros = previa.ParametrosDoPedido(
            objetivo_sem_destinos=sem_destinos or corpo.selecao.command, alvos=tuple(alvos),
            device_policy=corpo.selecao.device_policy, autonomia=corpo.autonomia, fuso=fuso,
            gatilhos=tuple(gatilhos), inicio_em=corpo.inicio_em, fim_em=corpo.fim_em,
            max_ocorrencias=corpo.max_ocorrencias, orcamento_total_usd=corpo.orcamento_total_usd,
            orcamento_ocorrencia_usd=corpo.orcamento_ocorrencia_usd, sobreposicao=corpo.sobreposicao,
            janela_recuperacao_s=corpo.janela_recuperacao_s, coalescer=corpo.coalescer,
            max_tentativas=corpo.max_tentativas, pausa_por_falha=corpo.pausa_por_falha)
        analise = previa.analisar(parametros, agora=self.agora(), piso_observar_s=self.cfg.piso_observar_s,
                                  piso_agir_s=self.cfg.piso_agir_s,
                                  quantas=previa.PROXIMAS_PADRAO)
        colab = self._validar_colaboracao(corpo, pid)
        if colab is not None:
            bloqueios.append(colab)
        # gatilho que nem normalizou: `analisar` não o vê, e o bloqueio dele já está na lista
        todos = tuple(bloqueios) + analise.bloqueios
        return Montado(parametros, analise, todos, preview, self._alvos_json(preview, alvos, corpo))

    @staticmethod
    def _alvos_json(preview: RunTargetsPreview | None, alvos: Sequence[tuple[str, str | None, str | None]],
                    corpo: CorpoDoPedido) -> str | None:
        """A foto no formato de `runs.targets` (`{"alvos": [...], "device_policy"}`), que o laço lê (`_requisicao`).
        O DTO devolve a mesma foto com a chave `targets`, como o contrato."""
        if preview is None:
            return None
        lista = [{"instance_id": t.instance_id, "profile_id": t.profile_id, "app_id": t.app_id, "origem": t.origem}
                 for t in preview.targets]
        return dumps({"alvos": lista, "device_policy": corpo.selecao.device_policy})

    # ================================================================== prévia
    def previa(self, corpo: CorpoDoPedido, proximas: int = previa.PROXIMAS_PADRAO) -> JsonObject:
        m = self._montar(corpo)
        p = m.parametros
        assert p is not None
        analise = previa.analisar(p, agora=self.agora(), piso_observar_s=self.cfg.piso_observar_s,
                                  piso_agir_s=self.cfg.piso_agir_s, quantas=proximas)
        alertas = [*(a.para_dict() for a in analise.alertas)]
        if m.preview is not None:
            alertas += [{"codigo": "alvo_aviso", "mensagem": w} for w in m.preview.warnings]
        valido = not m.bloqueios
        pre = m.preview
        return {
            "valido": valido,
            "objetivo_sem_destinos": p.objetivo_sem_destinos,
            "alvos": {"targets": [t.model_dump() for t in pre.targets] if pre else [],
                      "questions": list(pre.questions) if pre else [],
                      "command_sem_destinos": pre.command_sem_destinos if pre else None,
                      "warnings": list(pre.warnings) if pre else []},
            "proximas": [d.para_dict() for d in analise.proximas],
            "intervalo_minimo_s": analise.intervalo_minimo_s,
            "autonomia": previa.autonomia_da_previa(p.autonomia),
            "custo": previa.custo_da_previa([], p.orcamento_ocorrencia_usd,
                                            previa.ocorrencias_por_mes(analise.intervalo_minimo_s)),
            "bloqueios": [b.para_dict() for b in m.bloqueios],
            "alertas": alertas,
            "confirmacao": previa.selo(p) if valido else None,
        }

    # ================================================================== criação
    def criar(self, corpo: CorpoDoPedido, *, idempotency_key: str, titulo: str | None, confirmacao: str | None,
              operador: str | None) -> tuple[JsonObject, bool]:
        """`(PedidoView, deduplicated)`. O primeiro bloqueio vira o erro HTTP do contrato."""
        pid = previa.id_do_pedido(idempotency_key)
        m = self._montar(corpo, pid)
        if m.bloqueios:
            b = m.bloqueios[0]
            raise ErroDeApi(STATUS_DO_BLOQUEIO.get(b.codigo, 422), b.codigo, b.mensagem,
                            **({"campo": b.campo} if b.campo else {}))
        p = m.parametros
        assert p is not None and m.alvos_json is not None
        selo = previa.selo(p)
        existente = self.repo.pedido(pid)
        if existente is not None:
            # Depois de editado (`versao` > 1) o conteúdo gravado já não é o do corpo original: só dá para conferir o
            # que nunca mudou. Repetir a chamada devolve o pedido como está agora.
            if int(existente["versao"]) == 1 and (previa.selo(self._parametros(existente)) != selo
                                                  or self._estrutura_diverge(existente, corpo)):
                raise ErroDeApi(409, "idempotency_conflict", "A mesma idempotency_key já criou um pedido com outro "
                                "conteúdo. Use uma chave nova para um pedido novo.")
            return self.view(existente), True
        if confirmacao is not None and confirmacao != selo:
            raise ErroDeApi(409, "previa_desatualizada", "O que a prévia mostrou mudou (alvos, datas ou regras). "
                            "Peça a prévia de novo e confirme.")
        em = to_iso(self.agora())
        resumo = (titulo or p.objetivo_sem_destinos[:80]).strip() or p.objetivo_sem_destinos[:80]
        with self.db.tx():
            # dentro da transação (BEGIN IMMEDIATE no SQLite): dois filhos criados juntos não passam do limite de filhos
            # nem reservam mais do que o pai tem; é a mesma conferência da prévia e do `_montar`, refeita com o banco travado
            colab = self._validar_colaboracao(corpo, pid)
            if colab is not None:
                raise ErroDeApi(STATUS_DO_BLOQUEIO.get(colab.codigo, 422), colab.codigo, colab.mensagem,
                                **({"campo": colab.campo} if colab.campo else {}))
            self.db.execute(
                "INSERT INTO pedidos(id, titulo, objetivo, contexto, criterios_sucesso, alvos, autonomia, fuso,"
                " inicio_em, fim_em, max_ocorrencias, orcamento_total_usd, orcamento_ocorrencia_usd, sobreposicao,"
                " janela_recuperacao_s, coalescer, max_tentativas, pausa_por_falha, pai_id, papel, estado, versao,"
                " criado_por, criado_em, atualizado_em) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'rascunho',1,?,?,?)",
                (pid, resumo[:120], p.objetivo_sem_destinos, corpo.contexto,
                 dumps(list(corpo.criterios_sucesso)) if corpo.criterios_sucesso is not None else None,
                 m.alvos_json, p.autonomia, p.fuso, to_iso(p.inicio_em) if p.inicio_em else None,
                 to_iso(p.fim_em) if p.fim_em else None, p.max_ocorrencias, p.orcamento_total_usd,
                 p.orcamento_ocorrencia_usd, p.sobreposicao, p.janela_recuperacao_s, 1 if p.coalescer else 0,
                 p.max_tentativas, p.pausa_por_falha, corpo.pai_id, corpo.papel, operador, em, em))
            for de, tipo in corpo.dependencias:
                self.repo.inserir_dependencia(de, pid, tipo, em)
            for g in p.gatilhos:
                self.repo.inserir_gatilho(novo_id("g"), pid, g.tipo, dumps(dict(g.spec)), None, em)
            self.repo.marcar("pedido", pid, pessoa=True)
            if confirmacao is not None:
                self._ativar_na_transacao(pid, "rascunho", 1, em)
        self._descarregar()
        if confirmacao is not None:
            self.laco.acordar()
        return self.view(self.repo.pedido(pid) or {}), False

    def _estrutura_diverge(self, existente: Row, corpo: CorpoDoPedido) -> bool:
        """A repetição da chave traz outro pai, outro papel ou outras dependências: é outro pedido (28.10, F1)."""
        gravadas = sorted((d["de"], d["tipo"]) for d in self.repo.dependencias_entre([existente["id"]])
                          if d["para"] == existente["id"])
        return (existente["pai_id"] != corpo.pai_id or existente["papel"] != corpo.papel
                or gravadas != sorted(corpo.dependencias))

    def _ativar_na_transacao(self, pid: str, de: str, versao: int, em: str) -> None:
        transicionar_pedido(de, "ativo", ator=ATOR_PESSOA)
        if not self.repo.mudar_estado_do_pedido(pid, de, "ativo", em, versao=versao, pessoa=True):
            raise ErroDeApi(409, "invalid_state", "O pedido mudou de estado no meio.")
        # O gatilho nasceu com o pedido, e o rascunho pode esperar dias: a agenda vale a partir da ativação (senão o
        # laço trataria como atrasado tudo o que passou enquanto era rascunho). `proxima_em` NULL: o laço calcula.
        self.db.execute("UPDATE pedido_gatilhos SET criado_em=? WHERE pedido_id=?", (em, pid))
        # Gatilho de evento: a linha de base é o log AGORA (28.8, §14.2). O histórico anterior à ativação nunca dispara.
        self.repo.base_dos_eventos(pid)

    def ativar(self, pedido_id: str, confirmacao: str) -> JsonObject:
        p = self._pedido(pedido_id)
        if p["estado"] == "ativo":
            return {"pedido": self.view(p), "sem_mudanca": True}
        if p["estado"] != "rascunho":
            raise self._invalido(p)
        if confirmacao != previa.selo(self._parametros(p)):
            raise ErroDeApi(409, "previa_desatualizada", "O selo não confere com o pedido atual. Peça a prévia de novo.")
        with self.db.tx():
            self._ativar_na_transacao(pedido_id, "rascunho", int(p["versao"]), to_iso(self.agora()))
        self._descarregar()
        self.laco.acordar()
        return {"pedido": self.view(self._pedido(pedido_id)), "sem_mudanca": False}

    # ================================================================== ações
    def _pedido(self, pedido_id: str) -> Row:
        p = self.repo.pedido(pedido_id)
        if p is None:
            raise ErroDeApi(404, "not_found", f"Pedido {pedido_id} não existe.", kind="pedido")
        return p

    def _invalido(self, p: Row) -> ErroDeApi:
        return ErroDeApi(409, "invalid_state", f"O pedido está {p['estado']} e a ação não vale nesse estado.",
                         estado=p["estado"], acoes_permitidas=previa.acoes_permitidas(p["estado"]))

    def _rodar(self, fn: Callable[[], object], p: Row) -> None:
        try:
            fn()
        except (TransicaoInvalida, AcaoInvalida):
            raise self._invalido(self._pedido(p["id"])) from None
        finally:
            self._descarregar()

    def pausar(self, pedido_id: str, motivo: str | None) -> JsonObject:
        p = self._pedido(pedido_id)
        if p["estado"] == "pausado":
            return {"pedido": self.view(p), "sem_mudanca": True}
        if p["estado"] != "ativo":
            raise self._invalido(p)
        texto = (motivo or "").strip()[:previa.MOTIVO_MAXIMO] or previa.PAUSADO_PELA_PESSOA
        self._rodar(lambda: self.acoes.pausar(pedido_id, texto), p)
        return {"pedido": self.view(self._pedido(pedido_id)), "sem_mudanca": False}

    def retomar(self, pedido_id: str, modo: str | None) -> JsonObject:
        p = self._pedido(pedido_id)
        base: JsonObject = {"puladas": 0, "recuperadas": 0}
        if p["estado"] == "ativo":
            return {"pedido": self.view(p), "sem_mudanca": True, **base}
        if p["estado"] == "aguardando_pessoa":
            if modo is not None:
                raise ErroDeApi(422, "modo_nao_se_aplica", "Retomar um pedido que espera você não tem modo.")
            pendentes = self.pendencias(pedido_id)
            if pendentes:
                raise ErroDeApi(409, "pendencia_aberta", "Ainda há o que decidir neste pedido.", pendencias=pendentes)
            self._rodar(lambda: self.acoes.retomar(pedido_id, "recuperar"), p)
            return {"pedido": self.view(self._pedido(pedido_id)), "sem_mudanca": False, **base}
        if p["estado"] != "pausado":
            raise self._invalido(p)
        escolhido = modo or "daqui"
        antes = self._contar_puladas_da_retomada(pedido_id)
        self._rodar(lambda: self.acoes.retomar(pedido_id, escolhido), p)
        base["puladas"] = self._contar_puladas_da_retomada(pedido_id) - antes
        return {"pedido": self.view(self._pedido(pedido_id)), "sem_mudanca": False, **base}

    def resolver_incerta(self, pedido_id: str, ocorrencia_id: str, nota: str | None, operador: str | None) -> JsonObject:
        """A pessoa conferiu no mundo real o que a ocorrência `incerta` fez e a dá por resolvida (28.21). Só marca (quem,
        quando, nota): o estado segue `incerta`, nada é reexecutado, e o `retomar` passa a valer quando não restar outra
        pendência. Repetir numa já resolvida é idempotente: 200 com o que foi gravado na primeira vez (a nota nova é
        ignorada, para a trilha não ser reescrita). 404 se a ocorrência não existe ou é de outro pedido; 409
        `invalid_state` se não está incerta."""
        self._pedido(pedido_id)
        o = self.repo.ocorrencia(ocorrencia_id)
        if o is None or o["pedido_id"] != pedido_id:
            raise ErroDeApi(404, "not_found", f"Ocorrência {ocorrencia_id} não existe neste pedido.", kind="ocorrencia")
        if o["estado"] != "incerta":
            raise ErroDeApi(409, "invalid_state", f"A ocorrência está {o['estado']}: só uma ocorrência incerta se resolve.",
                            estado=o["estado"])
        texto = (nota or "").strip()
        if not texto:
            raise ErroDeApi(422, "nota_obrigatoria", "Diga o que você conferiu para dar a ocorrência por resolvida.")
        if o["resolvida_em"] is None:
            self.repo.resolver_incerta(ocorrencia_id, em=to_iso(self.agora()), por=operador or PAINEL, nota=texto)
            self._descarregar()
        # Relê: a perdedora de uma corrida devolve o que a vencedora gravou, nunca o próprio texto.
        return self.ocorrencia(self.db.one(self._SQL_OCORRENCIA + " WHERE o.id=?", (ocorrencia_id,)))

    def _contar_puladas_da_retomada(self, pedido_id: str) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM pedido_ocorrencias WHERE pedido_id=? AND motivo=?",
                                  (pedido_id, "pausado: retomado daqui")) or 0)

    def cancelar(self, pedido_id: str, confirmar: bool, motivo: str | None, operador: str | None) -> JsonObject:
        p = self._pedido(pedido_id)
        if p["estado"] == "cancelado":
            return {"pedido": self.view(p), "sem_mudanca": True, "execucoes_em_curso": [], "ocorrencias_canceladas": 0}
        if p["estado"] in TERMINAIS:
            raise self._invalido(p)
        # os descendentes ainda vivos vão junto (28.10, F1): o que eles têm a fazer ou em curso também conta na confirmação
        vivos = [f["id"] for f in self.repo.descendentes(pedido_id) if f["estado"] not in TERMINAIS]
        futuras = len(self.repo.ids_prevista_devida(pedido_id)) + sum(len(self.repo.ids_prevista_devida(i)) for i in vivos)
        em_curso = self.repo.execucoes_abertas(pedido_id) + [r for i in vivos for r in self.repo.execucoes_abertas(i)]
        if not confirmar:
            raise ErroDeApi(409, "confirmacao_necessaria", "Cancelar não desfaz o que já foi feito. Confirme.",
                            execucoes_em_curso=em_curso, ocorrencias_futuras=futuras)
        self._rodar(lambda: self.acoes.cancelar(pedido_id, por=operador, motivo=motivo), p)
        return {"pedido": self.view(self._pedido(pedido_id)), "sem_mudanca": False,
                "execucoes_em_curso": [{"run_id": r} for r in em_curso], "ocorrencias_canceladas": futuras,
                "filhos_cancelados": len(vivos)}

    # ================================================================== edição
    def _parametros(self, p: Row) -> previa.ParametrosDoPedido:
        """Os parâmetros SELADOS de um pedido gravado (a mesma forma que a criação selou)."""
        alvos = self._alvos_do_banco(p["alvos"])
        gat = tuple(previa.GatilhoPedido(g["tipo"], loads(g["spec"], {}) or {}) for g in self.repo.gatilhos_ativos(p["id"]))
        return previa.ParametrosDoPedido(
            objetivo_sem_destinos=p["objetivo"],
            alvos=tuple(sorted(((a["instance_id"], a.get("profile_id"), a.get("app_id")) for a in alvos["targets"]),
                               key=_chave_alvo)),
            device_policy=alvos["device_policy"], autonomia=p["autonomia"], fuso=p["fuso"], gatilhos=gat,
            inicio_em=parse_iso(p["inicio_em"]), fim_em=parse_iso(p["fim_em"]), max_ocorrencias=p["max_ocorrencias"],
            orcamento_total_usd=p["orcamento_total_usd"], orcamento_ocorrencia_usd=p["orcamento_ocorrencia_usd"],
            sobreposicao=p["sobreposicao"], janela_recuperacao_s=p["janela_recuperacao_s"],
            coalescer=bool(p["coalescer"]), max_tentativas=p["max_tentativas"], pausa_por_falha=p["pausa_por_falha"])

    @staticmethod
    def _alvos_do_banco(bruto: str | None) -> dict[str, list[dict[str, str | None]] | str]:
        v = loads(bruto, None)
        politica = "one"
        lista: list[dict[str, str | None]] = []
        if isinstance(v, dict):
            politica = str(v.get("device_policy") or "one")
            lista = list(v.get("alvos") or v.get("targets") or [])
        elif isinstance(v, list):
            lista = list(v)
        return {"targets": [{"instance_id": a.get("instance_id"), "profile_id": a.get("profile_id"),
                             "app_id": a.get("app_id"), "origem": a.get("origem") or "ui"} for a in lista],
                "device_policy": politica}

    def editar(self, pedido_id: str, mudancas: Mapping[str, object], *, versao: int, dry_run: bool,
               confirmacao: str | None, selecao: RunTargetsResolveBody | None,
               gatilhos: Sequence[tuple[str, Mapping[str, object]]] | None) -> JsonObject:
        p = self._pedido(pedido_id)
        if p["estado"] not in ("rascunho", "ativo", "pausado", "aguardando_pessoa"):
            raise self._invalido(p)
        if int(p["versao"]) != versao:
            raise ErroDeApi(409, "versao_desatualizada", "O pedido foi editado depois que você o abriu. Releia.",
                            versao_atual=int(p["versao"]))
        if "fuso" in mudancas and p["estado"] not in ("rascunho", "pausado") and mudancas["fuso"] != p["fuso"]:
            raise ErroDeApi(409, "invalid_state", "O fuso só muda com o pedido em rascunho ou pausado (mudar com ele "
                            "correndo desloca todas as datas).", estado=p["estado"],
                            acoes_permitidas=previa.acoes_permitidas(p["estado"]))
        if gatilhos is not None and len(gatilhos) != 1:
            raise ErroDeApi(422, "limite_invalido", "A edição troca a recorrência por uma só (o laço troca o gatilho "
                            "ativo por um novo).", campo="gatilhos")
        if gatilhos is not None and gatilhos[0][0] not in dominio_gatilhos.SUPORTADOS:
            raise ErroDeApi(422, "gatilho_nao_suportado", "A edição troca só os gatilhos agora, horário e recorrência. "
                            "Evento, condição e persona ficam como foram criados: cancele e crie outro pedido.",
                            campo="gatilhos[0].tipo")
        antes = self._parametros(p)
        # alvos: re-resolvidos só se a pessoa mandou outros (ou mudou o objetivo)
        novo_objetivo = str(mudancas.get("objetivo", antes.objetivo_sem_destinos))
        alvos, politica, alvos_json = antes.alvos, antes.device_policy, None
        if selecao is not None:
            preview, erro = self._resolver(selecao.model_copy(update={"command": novo_objetivo}))
            if erro is not None:
                raise ErroDeApi(STATUS_DO_BLOQUEIO.get(erro.codigo, 422), erro.codigo, erro.mensagem)
            assert preview is not None
            if preview.questions or any(t.origem == "texto" for t in preview.targets) or not preview.targets:
                raise ErroDeApi(409, "alvos_nao_confirmados", "Os alvos novos não resolvem sem uma decisão sua.")
            alvos = tuple(sorted(((t.instance_id, t.profile_id, t.app_id) for t in preview.targets), key=_chave_alvo))
            politica = selecao.device_policy
            novo_objetivo = preview.command_sem_destinos or novo_objetivo
            alvos_json = dumps({"alvos": [{"instance_id": t.instance_id, "profile_id": t.profile_id,
                                           "app_id": t.app_id, "origem": t.origem} for t in preview.targets],
                                "device_policy": politica})
        elif "objetivo" in mudancas:
            self._recusar(novo_objetivo)
            novo_objetivo = self.runs.sem_destinos(novo_objetivo) or novo_objetivo
        novo_gat = antes.gatilhos
        fuso = str(mudancas.get("fuso", antes.fuso))
        try:
            fuso = previa.conferir_fuso(fuso)
            if gatilhos is not None:
                novo_gat = (previa.normalizar_gatilho(gatilhos[0][0], gatilhos[0][1], fuso, 0),)
        except previa.ErroDeCorpo as e:
            raise ErroDeApi(422, e.bloqueio.codigo, e.bloqueio.mensagem, campo=e.bloqueio.campo) from None
        campos_p = {c: mudancas[c] for c in ("autonomia", "sobreposicao", "janela_recuperacao_s", "coalescer",
                                             "max_tentativas", "pausa_por_falha", "max_ocorrencias",
                                             "orcamento_total_usd", "orcamento_ocorrencia_usd", "inicio_em", "fim_em")
                    if c in mudancas}
        depois = previa.ParametrosDoPedido(
            objetivo_sem_destinos=novo_objetivo, alvos=alvos, device_policy=politica, fuso=fuso, gatilhos=novo_gat,
            **{**{"autonomia": antes.autonomia, "sobreposicao": antes.sobreposicao,
                  "janela_recuperacao_s": antes.janela_recuperacao_s, "coalescer": antes.coalescer,
                  "max_tentativas": antes.max_tentativas, "pausa_por_falha": antes.pausa_por_falha,
                  "max_ocorrencias": antes.max_ocorrencias, "orcamento_total_usd": antes.orcamento_total_usd,
                  "orcamento_ocorrencia_usd": antes.orcamento_ocorrencia_usd, "inicio_em": antes.inicio_em,
                  "fim_em": antes.fim_em}, **campos_p})
        analise = previa.analisar(depois, agora=self.agora(), piso_observar_s=self.cfg.piso_observar_s,
                                  piso_agir_s=self.cfg.piso_agir_s)
        if analise.bloqueios:
            b = analise.bloqueios[0]
            raise ErroDeApi(422, b.codigo, b.mensagem, **({"campo": b.campo} if b.campo else {}))
        recusa = colaboracao.validar_autonomia(depois.autonomia, p["papel"])     # F3: o papel não muda, a autonomia sim
        if recusa is not None:
            raise ErroDeApi(422, recusa.codigo, recusa.mensagem, campo=recusa.campo)
        selo_antes, selo_depois = previa.selo(antes), previa.selo(depois)
        muda: list[JsonObject] = []
        fa, fd = previa.forma_canonica(antes), previa.forma_canonica(depois)
        for chave in fa:
            if fa[chave] != fd[chave]:
                muda.append({"campo": chave, "de": fa[chave], "para": fd[chave]})
        for campo in ("titulo", "contexto", "criterios_sucesso"):
            if campo in mudancas:
                muda.append({"campo": campo, "de": self._ler(p, campo), "para": mudancas[campo]})
        proximas_antes = previa.proximas_do_pedido(antes.gatilhos, antes.fuso, self.agora(), 5, antes.fim_em)
        proximas_depois = previa.proximas_do_pedido(depois.gatilhos, depois.fuso, self.agora(), 5, depois.fim_em)
        refeitas = len(self.repo.ids_prevista_devida(pedido_id)) if gatilhos is not None else 0
        custo = previa.custo_da_previa(self.repo.ultimos_custos(pedido_id, 5), depois.orcamento_ocorrencia_usd,
                                       previa.ocorrencias_por_mes(analise.intervalo_minimo_s))
        resultado: JsonObject = {
            "aplicado": False, "mudancas": muda, "ocorrencias_refeitas": refeitas, "custo": custo,
            "proximas_antes": [d.para_dict() for d in proximas_antes],
            "proximas_depois": [d.para_dict() for d in proximas_depois], "confirmacao": selo_depois}
        if dry_run:
            resultado["pedido"] = self.view(p)
            return resultado
        if selo_depois != selo_antes:
            if confirmacao is None:
                raise ErroDeApi(409, "previa_nao_confirmada", "A edição muda o que você confirmou: refaça com "
                                "`dry_run` e envie a `confirmacao` que ele devolveu.", confirmacao=selo_depois)
            if confirmacao != selo_depois:
                raise ErroDeApi(409, "previa_desatualizada", "A confirmação não é a desta edição.")
        campos_db: dict[str, object] = {}
        if novo_objetivo != antes.objetivo_sem_destinos:
            campos_db["objetivo"] = novo_objetivo
        if alvos_json is not None:
            campos_db["alvos"] = alvos_json
        if fuso != antes.fuso:
            campos_db["fuso"] = fuso
        for c, v in campos_p.items():
            campos_db[c] = (1 if v else 0) if c == "coalescer" else (to_iso(v) if isinstance(v, datetime) else v)
        if "titulo" in mudancas:
            campos_db["titulo"] = str(mudancas["titulo"])[:120]
        if "contexto" in mudancas:
            campos_db["contexto"] = mudancas["contexto"]
        if "criterios_sucesso" in mudancas:
            c = mudancas["criterios_sucesso"]
            campos_db["criterios_sucesso"] = dumps(list(c)) if c is not None else None  # type: ignore[call-overload]
        gat = (gatilhos[0][0], dict(depois.gatilhos[0].spec)) if gatilhos is not None else None
        self._rodar(lambda: self.acoes.editar(pedido_id, campos_db, gatilho=gat), p)
        resultado["aplicado"] = True
        resultado["pedido"] = self.view(self._pedido(pedido_id))
        return resultado

    def _recusar(self, texto: str) -> None:
        try:
            RunService._recusar_credencial(texto)
        except RunError as e:
            raise ErroDeApi(409, e.code, e.message) from None

    @staticmethod
    def _ler(p: Row, campo: str) -> object:
        v = p[campo]
        return loads(v, None) if campo == "criterios_sucesso" else v

    # ================================================================== leitura
    def view(self, p: Row) -> JsonObject:
        """`PedidoView`: a linha da 067 mais o que é calculado."""
        if not p:
            raise ErroDeApi(404, "not_found", "Pedido não existe.", kind="pedido")
        pid = p["id"]
        alvos = self._alvos_do_banco(p["alvos"]) if p["alvos"] else None
        gat = self.repo.gatilhos_ativos(pid)
        por_estado = {r["estado"]: int(r["n"]) for r in self.db.query(
            "SELECT estado, COUNT(*) AS n FROM pedido_ocorrencias WHERE pedido_id=? GROUP BY estado", (pid,))}
        gasto = float(self.db.scalar("SELECT COALESCE(SUM(custo_usd),0) FROM pedido_ocorrencias WHERE pedido_id=?",
                                     (pid,)) or 0.0)
        total = p["orcamento_total_usd"]
        ultima = self.db.one("SELECT id, estado, terminada_em, motivo, run_id FROM pedido_ocorrencias WHERE pedido_id=?"
                             " AND estado<>'prevista' ORDER BY previsto_para DESC, id DESC LIMIT 1", (pid,))
        personas = []
        for pid_persona in sorted({a["profile_id"] for a in (alvos["targets"] if alvos else []) if a["profile_id"]}):  # type: ignore[union-attr]
            nome = self.db.scalar("SELECT COALESCE(NULLIF(display_name,''), NULLIF(username,''), id)"
                                  " FROM instagram_profiles WHERE id=?", (pid_persona,))
            personas.append({"profile_id": pid_persona, "nome": nome or pid_persona})
        proxima_local = None
        if p["proxima_em"]:
            try:
                from zoneinfo import ZoneInfo
                proxima_local = parse_iso(p["proxima_em"]).astimezone(ZoneInfo(p["fuso"])).isoformat()  # type: ignore[union-attr]
            except Exception:  # noqa: BLE001 - fuso gravado inválido: sem hora local, nunca um erro na leitura
                proxima_local = None
        v: JsonObject = {c: p[c] for c in (
            "id", "titulo", "objetivo", "contexto", "autonomia", "fuso", "inicio_em", "fim_em", "max_ocorrencias",
            "orcamento_total_usd", "orcamento_ocorrencia_usd", "sobreposicao", "janela_recuperacao_s",
            "max_tentativas", "pausa_por_falha", "estado", "versao", "proxima_em", "criado_por", "pausado_motivo",
            "encerrado_motivo", "pai_id", "papel", "criado_em", "atualizado_em")}
        v["criterios_sucesso"] = loads(p["criterios_sucesso"], None)
        v["alvos"] = alvos
        v["coalescer"] = bool(p["coalescer"])
        v["gatilhos_resumo"] = [{"tipo": g["tipo"], "descricao": previa.descrever_gatilho(
            (g["tipo"], loads(g["spec"], {}) or {}), p["fuso"])} for g in gat]
        v["personas"] = personas
        v["proxima_local"] = proxima_local
        v["proxima_prevista"] = self._proxima_prevista(p, gat)
        v["ultima_ocorrencia"] = dict(ultima) if ultima else None
        v["ocorrencias_por_estado"] = por_estado
        v["gasto_usd"] = round(gasto, 6)
        v["orcamento_usado"] = round(gasto / total, 4) if total else None
        v["avisos_nao_lidos"] = self.caixa.nao_lidos(pid)
        v["acoes_permitidas"] = previa.acoes_permitidas(p["estado"])
        return v

    #: Estados em que a agenda ainda vale (os mesmos das `proximas` do detalhe).
    _COM_AGENDA = ("ativo", "pausado", "aguardando_pessoa", "rascunho")

    def _proxima_prevista(self, p: Row, gatilhos: Sequence[Row]) -> JsonObject | None:
        """A próxima data CALCULADA pela agenda quando o laço ainda não gravou `proxima_em` (laço desligado, pedido
        recém-ativado ou rascunho). Sem ela a lista não tinha hora nenhuma; com `proxima_em`, é `None` (vale a do laço)."""
        if p["proxima_em"] or p["estado"] not in self._COM_AGENDA:
            return None
        ativos = [previa.GatilhoPedido(g["tipo"], loads(g["spec"], {}) or {}) for g in gatilhos]
        try:
            datas = previa.proximas_do_pedido(ativos, p["fuso"], self.agora(), 1, parse_iso(p["fim_em"]))
        except Exception:  # noqa: BLE001 - agenda inválida: sem data prevista, nunca um erro na leitura
            return None
        return datas[0].para_dict() if datas else None

    def sinal_do_laco(self) -> JsonObject:
        """Se o laço de pedidos roda nesta instalação. A tarefa só sobe no boot com `pedidos.enabled` (ver `AppState.start`):
        desligado, nenhum gatilho dispara e a tela precisa dizer isso, senão "próxima 08:00" promete o que não vai acontecer."""
        return {"ligado": bool(self.cfg.enabled)}

    def ocorrencia(self, o: Row) -> JsonObject:
        run = None
        if o.get("run_id") and o.get("run_status") is not None:
            run = {"id": o["run_id"], "short_id": str(o["run_id"]).rsplit("-", 1)[-1], "status": o["run_status"],
                   "status_detail": o.get("run_detalhe")}
        d: JsonObject = {c: o[c] for c in (
            "id", "pedido_id", "pedido_versao", "gatilho_id", "previsto_para", "chave", "origem", "estado", "tentativa",
            "run_id", "motivo", "custo_usd", "resumo", "criada_em", "iniciada_em", "terminada_em",
            "resolvida_em", "resolvida_por", "resolvida_nota")}
        d["run"] = run
        d["run_disponivel"] = o.get("run_id") is None or run is not None
        return d

    _SQL_OCORRENCIA = ("SELECT o.*, r.status AS run_status, r.status_detail AS run_detalhe FROM pedido_ocorrencias o"
                       " LEFT JOIN runs r ON r.id=o.run_id")

    def detalhe(self, pedido_id: str) -> JsonObject:
        p = self._pedido(pedido_id)
        v = self.view(p)
        agora = self.agora()
        gat = self.db.query("SELECT * FROM pedido_gatilhos WHERE pedido_id=? ORDER BY criado_em, id", (pedido_id,))
        ativos = [previa.GatilhoPedido(g["tipo"], loads(g["spec"], {}) or {}) for g in gat if g["ativo"]]
        proximas: list[JsonObject] = []
        if p["estado"] in ("ativo", "pausado", "aguardando_pessoa", "rascunho"):
            fim = parse_iso(p["fim_em"])
            proximas = [d.para_dict() for d in previa.proximas_do_pedido(ativos, p["fuso"], agora, 5, fim)]
        recentes = self.db.query(self._SQL_OCORRENCIA + " WHERE o.pedido_id=? ORDER BY o.previsto_para DESC, o.id DESC"
                                 " LIMIT 20", (pedido_id,))
        em_curso = self.db.query("SELECT r.id AS run_id, r.ocorrencia_id, r.status FROM runs r WHERE r.pedido_id=?"
                                 " AND r.status NOT IN ('completed','completed_with_issues','cancelled','failed')"
                                 " ORDER BY r.created_at", (pedido_id,))
        filhos = self.repo.filhos(pedido_id)
        v.update({
            # texto livre de pessoa (28.22): só aqui, nunca no `view()`, que vai inteiro no evento `pedido.updated`
            "cancelado_motivo": p["cancelado_motivo"],
            "filhos": [{"id": f["id"], "titulo": f["titulo"], "estado": f["estado"], "papel": f["papel"]} for f in filhos],
            "dependencias": [{"de": d["de"], "para": d["para"], "tipo": d["tipo"]}
                             for d in self.repo.dependencias_entre([pedido_id, *(f["id"] for f in filhos)])],
            "gatilhos": [{"id": g["id"], "tipo": g["tipo"], "ativo": bool(g["ativo"]), "criado_em": g["criado_em"],
                          "spec": loads(g["spec"], {}), "cursor": g["cursor"]} for g in gat],
            "proximas": proximas,
            "laco": self.sinal_do_laco(),
            "ocorrencias_recentes": [self.ocorrencia(o) for o in recentes],
            "execucoes_em_curso": [dict(r) for r in em_curso],
            "pendencias": self.pendencias(pedido_id) if p["estado"] == "aguardando_pessoa" else [],
            "memoria": [self._memoria(e) for e in self.memoria.entradas(pedido_id)],
            "relatorios_recentes": [self._relatorio(r) for r in self.memoria.relatorios(pedido_id, limite=5)],
            "observacoes_recentes": [self._observacao(r) for r in self.memoria.observacoes(pedido_id, limite=20)],
        })
        return v

    @staticmethod
    def _memoria(e: object) -> JsonObject:
        return {c: getattr(e, c) for c in ("chave", "tipo", "valor", "versao", "atualizada_em", "ocorrencia_id",
                                           "resolvida")}

    @staticmethod
    def _relatorio(r: Row) -> JsonObject:
        d: JsonObject = {c: r[c] for c in ("id", "pedido_id", "sequencia", "gatilho", "pedido_versao", "periodo_de",
                                           "periodo_ate", "gerado_por", "resumo_texto", "custo_usd", "gerado_em")}
        d["conteudo"] = loads(r["conteudo"], {})
        return d

    @staticmethod
    def _observacao(r: Row) -> JsonObject:
        return {c: r[c] for c in ("id", "pedido_id", "ocorrencia_id", "run_id", "alvo", "nome", "tipo", "situacao",
                                  "valor", "fonte", "trecho", "capturado_em")}

    def pendencias(self, pedido_id: str) -> list[JsonObject]:
        """O que espera uma pessoa neste pedido, lido do ESTADO VIVO (a tabela não guarda a causa)."""
        saida: list[JsonObject] = []
        for r in self.db.query("SELECT id, ocorrencia_id, created_at FROM runs WHERE pedido_id=? AND"
                               " status='needs_input' ORDER BY created_at", (pedido_id,)):
            saida.append({"tipo": "pergunta", "ref": r["id"], "run_id": r["id"], "ocorrencia_id": r["ocorrencia_id"],
                          "desde": r["created_at"]})
        for r in self.db.query(
                "SELECT a.id, a.run_id, a.created_at, r.ocorrencia_id FROM pending_approvals a JOIN runs r ON"
                " r.id=a.run_id WHERE a.status='pending' AND r.pedido_id=? ORDER BY a.created_at", (pedido_id,)):
            saida.append({"tipo": "aprovacao", "ref": r["id"], "run_id": r["run_id"],
                          "ocorrencia_id": r["ocorrencia_id"], "desde": r["created_at"]})
        # `incerta` é fim de linha da ocorrência: continua pendente até a pessoa resolvê-la (28.21: `resolvida_em`,
        # pela rota `resolver`) ou, regra antiga que fica, até uma ocorrência posterior concluir (o pedido seguiu).
        # Resolver NÃO muda o estado: a ocorrência segue `incerta` e só sai da lista de pendências.
        for r in self.db.query(
                "SELECT o.id, o.run_id, o.terminada_em, o.criada_em FROM pedido_ocorrencias o WHERE o.pedido_id=? AND"
                " o.estado='incerta' AND o.resolvida_em IS NULL AND NOT EXISTS (SELECT 1 FROM pedido_ocorrencias c WHERE c.pedido_id=o.pedido_id"
                " AND c.estado='concluida' AND c.terminada_em > o.terminada_em) ORDER BY o.previsto_para", (pedido_id,)):
            saida.append({"tipo": "ocorrencia_incerta", "ref": r["id"], "run_id": r["run_id"],
                          "ocorrencia_id": r["id"], "desde": r["terminada_em"] or r["criada_em"]})
        return saida

    # ------------------------------------------------------------------ listagem
    @staticmethod
    def _cursor(offset: int) -> str:
        return base64.urlsafe_b64encode(str(offset).encode()).decode().rstrip("=")

    @staticmethod
    def _offset(cursor: str | None) -> int:
        if not cursor:
            return 0
        try:
            return max(0, int(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()))
        except (ValueError, UnicodeDecodeError):
            raise ErroDeApi(422, "cursor_invalido", "Cursor inválido.") from None

    def listar(self, *, estado: Sequence[str] | None, autonomia: str | None, tipo: str | None, profile_id: str | None,
               q: str | None, pede_atencao: bool, ordem: str, limit: int, cursor: str | None) -> JsonObject:
        onde, params = ["1=1"], []
        if estado:
            onde.append(f"estado IN ({','.join('?' for _ in estado)})")
            params += list(estado)
        if autonomia:
            onde.append("autonomia=?")
            params.append(autonomia)
        if tipo:
            onde.append("id IN (SELECT pedido_id FROM pedido_gatilhos WHERE tipo=?)")
            params.append(tipo)
        if q:
            esc = q.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            onde.append("(LOWER(titulo) LIKE ? ESCAPE '\\' OR LOWER(objetivo) LIKE ? ESCAPE '\\')")
            params += [f"%{esc}%", f"%{esc}%"]
        if pede_atencao:
            onde.append("(estado IN ('pausado','aguardando_pessoa') OR id IN (SELECT pedido_id FROM pedido_avisos"
                        " WHERE lido_em IS NULL AND requer_pessoa=0))")
        ordenar = {"atualizado": "atualizado_em DESC, id", "criado": "criado_em DESC, id",
                   "proxima": "(CASE WHEN proxima_em IS NULL THEN 1 ELSE 0 END), proxima_em, id"}[ordem]
        linhas = self.db.query(f"SELECT * FROM pedidos WHERE {' AND '.join(onde)} ORDER BY {ordenar}", tuple(params))
        if profile_id:
            linhas = [r for r in linhas if any(a["profile_id"] == profile_id
                                               for a in self._alvos_do_banco(r["alvos"])["targets"])]  # type: ignore[union-attr]
        ini = self._offset(cursor)
        pagina = linhas[ini:ini + limit]
        total = {e: 0 for e in ESTADOS}
        for r in self.db.query("SELECT estado, COUNT(*) AS n FROM pedidos GROUP BY estado"):
            total[r["estado"]] = int(r["n"])
        return {"items": [self.view(r) for r in pagina],
                "proximo_cursor": self._cursor(ini + limit) if ini + limit < len(linhas) else None,
                "total_por_estado": total, "laco": self.sinal_do_laco()}

    def ocorrencias(self, pedido_id: str, *, estado: str | None, origem: str | None, de: str | None, ate: str | None,
                    limit: int, antes_de: str | None) -> JsonObject:
        self._pedido(pedido_id)
        onde, params = ["o.pedido_id=?"], [pedido_id]
        for coluna, valor, op in (("o.estado", estado, "="), ("o.origem", origem, "="), ("o.previsto_para", de, ">="),
                                  ("o.previsto_para", ate, "<="), ("o.previsto_para", antes_de, "<")):
            if valor:
                onde.append(f"{coluna}{op}?")
                params.append(valor)
        base = self._SQL_OCORRENCIA + " WHERE " + " AND ".join(onde)
        linhas = self.db.query(base + " ORDER BY o.previsto_para DESC, o.id DESC LIMIT ?", (*params, limit + 1))
        proximo = None
        if len(linhas) > limit:
            linhas = linhas[:limit]
            limite_p = linhas[-1]["previsto_para"]
            # empates no mesmo instante (gatilhos diferentes) não podem ficar entre duas páginas
            ids = {r["id"] for r in linhas}
            linhas += [r for r in self.db.query(base + " AND o.previsto_para=? ORDER BY o.id DESC", (*params, limite_p))
                       if r["id"] not in ids]
            proximo = limite_p
        return {"items": [self.ocorrencia(o) for o in linhas], "proximo": proximo}

    def relatorios(self, pedido_id: str, limit: int, cursor: str | None) -> JsonObject:
        self._pedido(pedido_id)
        antes = int(cursor) if cursor and cursor.isdigit() else None
        linhas = self.memoria.relatorios(pedido_id, limite=limit + 1, antes_de=antes)
        mais = len(linhas) > limit
        linhas = linhas[:limit]
        return {"items": [self._relatorio(r) for r in linhas],
                "proximo_cursor": str(linhas[-1]["sequencia"]) if mais and linhas else None}

    def observacoes(self, pedido_id: str, limit: int, cursor: str | None) -> JsonObject:
        self._pedido(pedido_id)
        linhas = self.memoria.observacoes(pedido_id, limite=limit + 1, antes_de=cursor or None)
        mais = len(linhas) > limit
        linhas = linhas[:limit]
        return {"items": [self._observacao(r) for r in linhas],
                "proximo_cursor": linhas[-1]["capturado_em"] if mais and linhas else None}

    def execucoes(self, pedido_id: str, limit: int) -> list[Row]:
        self._pedido(pedido_id)
        return self.db.query("SELECT * FROM runs WHERE pedido_id=? ORDER BY created_at DESC, id DESC LIMIT ?",
                             (pedido_id, limit))

    # ------------------------------------------------------------------ avisos (tabela `pedido_avisos`)
    def avisos(self, *, pedido_id: str | None, requer_pessoa: bool | None, lido: bool | None, limit: int,
               cursor: str | None) -> JsonObject:
        """`nao_lidos` é o contador da caixa (informativos não lidos), do pedido filtrado ou de todos: não muda com os
        outros filtros, para o selo do menu e a lista dizerem o mesmo número."""
        ini = self._offset(cursor)
        itens, mais = self.caixa.listar(pedido_id=pedido_id, requer_pessoa=requer_pessoa, lido=lido, limite=limit,
                                        inicio=ini)
        return {"items": itens, "nao_lidos": self.caixa.nao_lidos(pedido_id),
                "proximo_cursor": self._cursor(ini + limit) if mais else None}

    def ler_avisos(self, *, ids: Sequence[str] | None, todos: bool, pedido_id: str | None) -> JsonObject:
        """Marca como lido. Sem `ids` nem `todos` é 422; id que não existe é 404 e nada é gravado. Repetir é seguro
        (`lidos` conta só o que mudou agora)."""
        if not ids and not todos:
            raise ErroDeApi(422, "limite_invalido", "Informe `ids` ou `todos`.")
        if ids and len(ids) > MAXIMO_DE_IDS:
            raise ErroDeApi(422, "limite_invalido", f"No máximo {MAXIMO_DE_IDS} ids por chamada.")
        if pedido_id:
            self._pedido(pedido_id)
        faltam = self.caixa.inexistentes(list(ids or ()))
        if faltam:
            raise ErroDeApi(404, "not_found", "Aviso não existe.", kind="aviso", ids=faltam)
        lidos = self.caixa.ler(ids=ids, todos=todos, pedido_id=pedido_id)
        return {"lidos": lidos, "nao_lidos": self.caixa.nao_lidos(pedido_id)}

    # ------------------------------------------------------------------ snapshot
    def snapshot(self) -> JsonObject:
        """A fatia `pedidos` do `GET /api/snapshot`: completa, sem janela (ADR-062 regra 2). Cada pedido em
        `aguardando_pessoa` é UM item da caixa de Pendências; as `pendencias` dele são os filhos agrupados (a execução
        `needs_input` e a aprovação levam `pedido_id` aqui e na execução, e não contam de novo)."""
        por = {e: 0 for e in ESTADOS}
        for r in self.db.query("SELECT estado, COUNT(*) AS n FROM pedidos GROUP BY estado"):
            por[r["estado"]] = int(r["n"])
        espera = [{**self.view(r), "pendencias": self.pendencias(r["id"])} for r in self.db.query(
            "SELECT * FROM pedidos WHERE estado='aguardando_pessoa' ORDER BY atualizado_em, id")]
        return {"por_estado": por, "avisos_nao_lidos": self.caixa.nao_lidos(), "aguardando_pessoa": espera}

    # ================================================================== eventos
    def _descarregar(self) -> None:
        self.publicar(self.repo.descarregar())

    def publicar(self, marcas: Sequence[tuple[str, str, bool]]) -> None:
        """Emite o estado ATUAL de cada pedido/ocorrência marcado (uma vez por item). Falha aqui nunca desfaz o gesto."""
        vistos: dict[tuple[str, str], bool] = {}
        for tipo, ident, pessoa in marcas:
            vistos[(tipo, ident)] = vistos.get((tipo, ident), False) or pessoa
        for (tipo, ident), pessoa in vistos.items():
            try:
                if tipo == "pedido":
                    self._publicar_pedido(ident, pessoa)
                elif tipo == "relatorio":
                    self._publicar_relatorio(ident)
                else:
                    self._publicar_ocorrencia(ident)
            except Exception:  # noqa: BLE001
                log.exception("pedidos: evento de %s %s", tipo, ident)
        self._encerrar_filhos_dos_encerrados([ident for (tipo, ident) in vistos if tipo == "pedido"])

    def _encerrar_filhos_dos_encerrados(self, pedidos: Sequence[str]) -> None:
        """Pai que o SISTEMA encerrou (prazo, contagem, orçamento, abandono) leva os filhos vivos (28.10, F1). Este é o
        funil por onde passa tudo o que o laço escreve, então a cascata não toca o `laco.py`. Roda DEPOIS do commit do pai,
        e a repetição conserta uma queda no meio (`encerrar_filhos` é idempotente). Falha aqui nunca desfaz o encerramento."""
        for ident in pedidos:
            try:
                p = self.repo.pedido(ident)
                if p is not None and p["estado"] == "encerrado" and self.acoes.encerrar_filhos(ident):
                    self.publicar(self.repo.descarregar())
            except Exception:  # noqa: BLE001
                log.exception("pedidos: cascata do encerramento do pedido %s", ident)

    def _publicar_pedido(self, pid: str, pessoa: bool) -> None:
        p = self.repo.pedido(pid)
        if p is None:
            return
        estado = p["estado"]
        nivel = "warn" if estado == "aguardando_pessoa" or (estado == "pausado" and not pessoa) else "info"
        self.emitir("pedido.updated", f"Pedido '{p['titulo']}': {estado}", level=nivel, data={"pedido": self.view(p)})
        if estado == "pausado" and not pessoa:
            # a MESMA chave que o laço usa (`atualizado_em` é o instante da pausa): um aviso só, venha de onde vier
            self._aviso(p, None, "pausa_automatica", dominio_avisos.chave_da_pausa(pid, p["atualizado_em"]),
                        f"O pedido '{p['titulo']}' foi pausado: {p['pausado_motivo'] or 'sem motivo registrado'}.",
                        {"motivo": p["pausado_motivo"]})
        elif estado == "encerrado" and not pessoa:
            if p["encerrado_motivo"] == "orcamento":
                # o orçamento esgotado já diz por que encerrou: um aviso só, não dois para a mesma notícia
                self._aviso(p, None, "orcamento_esgotado", dominio_avisos.chave_do_orcamento_esgotado(pid),
                            f"O pedido '{p['titulo']}' foi encerrado: o orçamento acabou.", {"motivo": "orcamento"})
            else:
                self._aviso(p, None, "encerramento", dominio_avisos.chave_do_encerramento(pid),
                            f"O pedido '{p['titulo']}' foi encerrado ({p['encerrado_motivo'] or 'sem motivo'}).",
                            {"motivo": p["encerrado_motivo"]})

    def _publicar_ocorrencia(self, oid: str) -> None:
        o = self.db.one(self._SQL_OCORRENCIA + " WHERE o.id=?", (oid,))
        if o is None:
            return
        nivel = {"falhou": "error", "incerta": "warn", "perdida": "warn", "pulada": "warn"}.get(o["estado"], "info")
        self.emitir("pedido.ocorrencia.updated", f"Ocorrência de {o['previsto_para']}: {o['estado']}", level=nivel,
                    data={"ocorrencia": self.ocorrencia(o)})
        if o["estado"] == "perdida":
            p = self.repo.pedido(o["pedido_id"])
            if p is not None:
                self._aviso(p, o["id"], "ocorrencia_perdida", dominio_avisos.chave_da_ocorrencia_perdida(o["id"]),
                            f"Uma ocorrência de '{p['titulo']}' foi perdida: {o['motivo'] or 'sem motivo registrado'}.",
                            {"previsto_para": o["previsto_para"]})

    def _publicar_relatorio(self, rid: str) -> None:
        """Relatório gravado pelo 28.7 (encerramento ou período): o aviso `relatorio_pronto`, sem o conteúdo."""
        r = self.memoria.relatorio(rid)
        p = self.repo.pedido(r["pedido_id"]) if r is not None else None
        if r is None or p is None:
            return
        dados: dict[str, object] = {"relatorio_id": rid, "sequencia": int(r["sequencia"]), "gatilho": r["gatilho"]}
        mensagem = f"O relatório {r['sequencia']} do pedido '{p['titulo']}' está pronto."
        resumo = _resumo_da_consolidacao(r["conteudo"])
        if resumo is not None:               # 28.10 F4: só contagens; o valor lido e o id dos filhos ficam no relatório
            dados.update({"filhos_lidos": resumo["filhos"], "conflitos": resumo["conflitos"]})
            mensagem += f" Consolidou {resumo['filhos']} filho(s); {resumo['conflitos']} conflito(s)."
        self._aviso(p, None, "relatorio_pronto", dominio_avisos.chave_do_relatorio(rid), mensagem, dados)

    def _aviso(self, p: Row, ocorrencia_id: str | None, tipo: str, chave: str, mensagem: str,
               dados: Mapping[str, object]) -> None:
        """Grava e emite pelo ponto único (`CaixaDeAvisos.registrar`): repetir a chave não duplica nem reemite."""
        self.caixa.registrar(pedido_id=p["id"], tipo=tipo, nivel=None, mensagem=mensagem, chave=chave,
                             ocorrencia_id=ocorrencia_id, dados=dados)

    def registrar_aviso(self, aviso: Mapping[str, object]) -> JsonObject | None:
        """O `avisar` do laço: o `AvisoDTO` do 28.5/28.6 passa pelo MESMO caminho que os avisos da API."""
        return self.caixa.registrar_dto(aviso)


def _resumo_da_consolidacao(conteudo: str | None) -> dict[str, int] | None:
    """As contagens do bloco `consolidacao` do relatório (28.10 F4), ou `None` sem bloco ou com conteúdo ilegível."""
    try:
        resumo = (json.loads(conteudo or "{}").get("consolidacao") or {}).get("resumo")
        return {"filhos": int(resumo["filhos"]), "conflitos": int(resumo["conflitos"])} if resumo else None
    except (TypeError, ValueError, KeyError, AttributeError):
        return None


__all__ = ["CorpoDoPedido", "ErroDeApi", "PedidosApi", "ESTADOS", "PEDIDO_ATORES"]
