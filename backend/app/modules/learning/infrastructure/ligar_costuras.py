"""Pacote A2 do ADR-054: liga as costuras tipadas dos arquivos quentes (`app/taskqueue/costuras.py`, e as de gesto
fora da fila em `app/shared/costuras.py`) ao livro.

Duas partes:

- `ligar(servico)`, chamado pela montagem: abre o registro de EXTENSÕES das costuras deste serviço, onde os pacotes
  seguintes se penduram sem tocar os arquivos quentes — o fornecedor das lições (A7) e os observadores do fechamento
  de tentativa (A8, as telas vistas). O registro é por serviço (um `AppState`, um livro): nada global que um segundo
  `AppState` herdasse do primeiro;
- `costuras_do_livro(servico, db)`, chamado pelo `AppState` depois de montar executor, serviço de execução e
  gerenciador de aparelhos: o objeto que cumpre `CosturasDeAprendizado` e `CosturaDeControle` e que o `AppState`
  pendura em cada um.

O que este pacote grava por conta própria são SINAIS (`learning_signals`), sem IA, um por GESTO: o `source_ref` de
cada um já é a identidade do evento (a tentativa tomada, o ponto de decisão do item, o episódio), e o sinal que já
existe com aquele `(kind, source_ref)` não se grava de novo, qualquer que seja o autor (`_gesto`; o índice do livro,
`(kind, source_ref, created_by)`, é o do voto do D2, em que duas pessoas são duas opiniões):

- `confirmou_a_mao` (neutro: positivo para a ação e negativo para a verificação — nunca evidência a favor de
  aprendizado), `repetiu_item` (neutro) e `abandonou_item` (negativo), por `ao_resolver`, com a nota redigida (a que
  parece credencial não fica: `note_refused=1`);
- `repetiu_execucao` (neutro), por `ao_repetir`;
- `respondeu_pergunta` (neutro), um por campo, com o campo e o sha256 da resposta — nunca o valor;
- `tomou_controle` (negativo), um por tentativa tomada, sem árvore, texto ou coordenada;
- `cancelou_execucao`, pela rota de cancelar (o gesto; a sucessora que cancela a execução respondida não conta), um
  por EPISÓDIO (o clique repetido com o cancelamento já valendo não grava; a execução reaberta e cancelada de novo é
  outro episódio, com o instante da transição na chave): neutro quando nada tinha rodado (`planning`, `needs_input`,
  `planned` — não diz nada de como a IA agiu), negativo quando a pessoa interrompeu trabalho em curso ou fechou o que
  tinha ficado pendente;
- `comando_incerto_resolvido`, por quem tirou um comando de `uncertain`: `succeeded` é neutro (como confirmar à mão: a
  ação valeu, a prova não veio — nunca evidência a favor), `failed` é negativo e `cancelled` é neutro. O autor que o
  comando gravou (`resolved_by`) vai em `data`, só para cruzar os dois;
- `correcao_de_ensino` (negativo: a pessoa diz que a habilidade errou naquela etapa), com o texto da correção como
  nota, ligada à execução e à etapa corrigidas.

Quem faz o gesto é uma pessoa pelo painel, e todos levam o operador da sessão (`autor_do_gesto`, a regra das rotas
do livro: sem sessão, `panel`): a rota o passa no `quem` de cada costura (`resolve`, `retry_failed`, a sucessora do
assistente e o pedido de controle recebem `por=`). Nunca como `sistema` — a régua diária só conta como negativo
humano o que não é do sistema. O app e a ação do sinal saem da etapa pela mesma regra da régua diária
(`app_da_etapa`, ação nula = `*`), para os dois agregarem na mesma chave; `simulated` vem de `runs.simulated` (no
comando sem execução, do modo da instalação), e sinal de execução simulada nunca promove nada. Nenhum dos três de
29/09 (cancelar, comando incerto, correção) conta como intervenção na régua diária (`SINAIS_DE_INTERVENCAO`): o
ADR-054 não os lista.

`aprendizado.enabled: false` desliga tudo (lido a cada chamada); lições no modo `off` nem são pedidas.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Protocol
from weakref import WeakKeyDictionary

from app.db import Database
from app.modules.learning.application.ports import NovoSinal
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.vocabulario import Modo, Polaridade, SignalKind
from app.modules.learning.infrastructure import linhas
from app.shared.costuras import (PAINEL, CorrecaoDeEnsino, DesfechoDoComando, ResolucaoDeComando, TomadaDeControle,
                                 autor_do_gesto)
from app.taskqueue.costuras import (CancelamentoDeExecucao, DecisaoSobreItem, FechamentoDeTentativa, PedidoDeLicoes,
                                    RepeticaoDeExecucao, ResolucaoDeItem, RespostaAPergunta)
from app.taskqueue.projecao import QUALQUER, app_da_etapa

log = logging.getLogger("poc.aprendizado")

_DO_GESTO: dict[DecisaoSobreItem, tuple[SignalKind, Polaridade]] = {
    "confirm_done": (SignalKind.CONFIRMOU_A_MAO, Polaridade.NEUTRAL),
    "retry": (SignalKind.REPETIU_ITEM, Polaridade.NEUTRAL),
    "abandon": (SignalKind.ABANDONOU_ITEM, Polaridade.NEGATIVE),
}
#: A decisão de uma pessoa sobre um comando incerto (ver o docstring do módulo).
_DO_DESFECHO: dict[DesfechoDoComando, Polaridade] = {
    "succeeded": Polaridade.NEUTRAL,
    "failed": Polaridade.NEGATIVE,
    "cancelled": Polaridade.NEUTRAL,
}
#: As chaves do `params` de um comando que são TRILHA (quem o pediu) — nunca argumento com valor de pessoa.
_TRILHA_DO_COMANDO = ("run_id", "objective_id", "package", "app_id", "profile_id")

_FORA_DO_CAMPO = re.compile(r"[^a-z0-9_]+")


# ------------------------------------------------------------------ extensões (onde A7 e A8 se penduram)
class FornecedorDeLicoes(Protocol):
    """Quem escolhe as lições de um pedido (A7): modelo fechado, teto do papel, braço de controle e exposição."""

    def licoes_para(self, pedido: PedidoDeLicoes) -> list[str]: ...


class ObservadorDeTentativa(Protocol):
    """Quem aproveita a tentativa fechada (A8: a tela vista numa etapa comprovada). Roda no fechamento, no caminho da
    execução: grava barato e nunca chama IA."""

    nome: str

    def ao_fechar(self, fechamento: FechamentoDeTentativa) -> None: ...


@dataclass(slots=True)
class Extensoes:
    licoes: FornecedorDeLicoes | None = None
    observadores: list[ObservadorDeTentativa] = field(default_factory=list)

    def definir_licoes(self, fornecedor: FornecedorDeLicoes) -> None:
        self.licoes = fornecedor

    def observar(self, observador: ObservadorDeTentativa) -> None:
        """O mesmo `nome` não entra duas vezes (a montagem pode rodar de novo no mesmo serviço)."""
        if all(o.nome != observador.nome for o in self.observadores):
            self.observadores.append(observador)


_EXTENSOES: WeakKeyDictionary[LearningService, Extensoes] = WeakKeyDictionary()


def extensoes(servico: LearningService) -> Extensoes:
    """As extensões das costuras deste serviço (criadas na primeira vez)."""
    ext = _EXTENSOES.get(servico)
    if ext is None:
        ext = _EXTENSOES[servico] = Extensoes()
    return ext


def ligar(servico: LearningService) -> None:
    """Abre o registro das extensões deste serviço. As costuras em si o `AppState` pendura (`costuras_do_livro`): é
    ele quem tem o executor, o serviço de execução e o gerenciador de aparelhos."""
    extensoes(servico)


def costuras_do_livro(servico: LearningService, db: Database) -> CosturasDoLivro:
    return CosturasDoLivro(servico, db, extensoes(servico))


# ------------------------------------------------------------------ as costuras
@dataclass(frozen=True, slots=True)
class _Contexto:
    """O que o sinal precisa saber da execução (e da etapa, quando há), lido do banco no instante do gesto."""

    simulated: bool
    app_package: str = ""
    capability: str = ""
    step_hash: str | None = None
    objective_id: str | None = None
    instance_id: str | None = None
    profile_id: str | None = None


class CosturasDoLivro:
    """Cumpre `CosturasDeAprendizado` (`app/taskqueue/costuras.py`) e as costuras de gesto do kernel
    (`CosturaDeControle`, `CosturaDeComando`, `CosturaDeEnsino`, em `app/shared/costuras.py`). Toda exceção daqui é
    engolida por quem chama (`avisar`, `pedir_licoes`): o gesto e a etapa seguem."""

    def __init__(self, servico: LearningService, db: Database, ext: Extensoes) -> None:
        self._servico = servico
        self._db = db
        self._ext = ext

    def _ligado(self) -> bool:
        return self._servico.ajustes.enabled

    # ---------------------------------------------------------------- executor e planejamento
    def licoes_para(self, pedido: PedidoDeLicoes) -> list[str]:
        ajustes = self._servico.ajustes
        fornecedor = self._ext.licoes
        # O fornecedor aplica o modo efetivo por pacote (§8.10): com um pacote ligado, o global em `off` não cala.
        ligado = ajustes.modo_licoes is not Modo.OFF or any(m is not Modo.OFF for m in ajustes.por_licoes.values())
        if not ajustes.enabled or not ligado or fornecedor is None:
            return []
        return list(fornecedor.licoes_para(pedido))

    def ao_fechar_tentativa(self, fechamento: FechamentoDeTentativa) -> None:
        if not self._ligado():
            return
        for observador in tuple(self._ext.observadores):
            try:
                observador.ao_fechar(fechamento)
            except Exception:  # noqa: BLE001 - um observador quebrado não cala os outros nem a etapa
                log.exception("aprendizado: observador %s falhou na tentativa %s", observador.nome,
                              fechamento.attempt_id)

    # ---------------------------------------------------------------- gestos de uma pessoa
    def ao_resolver(self, resolucao: ResolucaoDeItem) -> None:
        if not self._ligado():
            return
        ctx = self._contexto(resolucao.run_id, objective_id=resolucao.objective_id, step_id=resolucao.step_id)
        if ctx is None:
            return
        kind, polaridade = _DO_GESTO[resolucao.resolucao]
        self._gesto(NovoSinal(
            kind=kind, source_ref=f"resolve:{resolucao.objective_id}:{resolucao.ordem}",
            created_by=autor_do_gesto(resolucao.quem), polarity=polaridade, note=resolucao.nota,
            run_id=resolucao.run_id, objective_id=resolucao.objective_id,
            step_id=resolucao.step_id, instance_id=ctx.instance_id, profile_id=ctx.profile_id,
            app_package=ctx.app_package, capability=ctx.capability, step_hash=ctx.step_hash,
            # A etapa que esperava a decisão estava parada (incerta, esperando, falhou): nunca comprovada.
            step_verified=False if resolucao.step_id else None, data={"resolucao": resolucao.resolucao},
            simulated=ctx.simulated))

    def ao_repetir(self, repeticao: RepeticaoDeExecucao) -> None:
        if not self._ligado() or not repeticao.objetivos:
            return
        ctx = self._contexto(repeticao.run_id)
        if ctx is None:
            return
        gesto = hashlib.sha1(",".join(sorted(repeticao.objetivos)).encode()).hexdigest()[:12]
        self._gesto(NovoSinal(
            kind=SignalKind.REPETIU_EXECUCAO, source_ref=f"repeticao:{repeticao.run_id}:{gesto}",
            created_by=autor_do_gesto(repeticao.quem), polarity=Polaridade.NEUTRAL, run_id=repeticao.run_id,
            app_package=ctx.app_package,
            data={"itens": len(repeticao.objetivos)}, simulated=ctx.simulated))

    def respondeu_pergunta(self, resposta: RespostaAPergunta) -> None:
        if not self._ligado():
            return
        ctx = self._contexto(resposta.run_id)
        if ctx is None:
            return
        for campo in dict.fromkeys(_campo(c) for c in (resposta.campos or ("",))):
            self._gesto(NovoSinal(
                kind=SignalKind.RESPONDEU_PERGUNTA, source_ref=f"resposta:{resposta.run_id}:{campo}",
                created_by=autor_do_gesto(resposta.quem), polarity=Polaridade.NEUTRAL, run_id=resposta.run_id,
                app_package=ctx.app_package,
                data={"campo": campo, "resposta_sha256": resposta.resposta_sha256,
                      "run_sucessora": resposta.run_sucessora},
                simulated=ctx.simulated))

    def cancelou_execucao(self, cancelamento: CancelamentoDeExecucao) -> None:
        if not self._ligado():
            return
        ctx = self._contexto(cancelamento.run_id)
        if ctx is None:
            return
        # Um sinal por EPISÓDIO: o clique repetido nem chega aqui (`RunService.cancel`), e a execução reaberta e
        # cancelada de novo é outro episódio — a chave leva o instante da transição, senão `_gesto` o engoliria.
        self._gesto(NovoSinal(
            kind=SignalKind.CANCELOU_EXECUCAO, source_ref=f"cancelamento:{cancelamento.run_id}:{cancelamento.em}",
            created_by=autor_do_gesto(cancelamento.quem),
            polarity=Polaridade.NEUTRAL if cancelamento.antes_de_iniciar else Polaridade.NEGATIVE,
            run_id=cancelamento.run_id, app_package=ctx.app_package,
            data={"status_anterior": cancelamento.status_anterior}, simulated=ctx.simulated))

    def comando_incerto_resolvido(self, resolucao: ResolucaoDeComando) -> None:
        if not self._ligado():
            return
        comando = self._db.one("SELECT instance_id, verb, params, result FROM commands WHERE id=?",
                               (resolucao.command_id,))
        if comando is None:
            return
        # Quem o COMANDO gravou como autor (`result.resolved_by`, que aceita o `requested_by` do corpo sem sessão) vai
        # só em `data`, para cruzar o sinal com o comando; o autor do sinal é o da sessão (`autor_do_gesto`).
        resolvido_por = linhas.json_objeto(comando, "result").get("resolved_by")
        bruto = linhas.json_objeto(comando, "params")
        trilha = {k: v.strip() for k in _TRILHA_DO_COMANDO if isinstance(v := bruto.get(k), str) and v.strip()}
        run_id = trilha.get("run_id")
        ctx = self._contexto(run_id, objective_id=trilha.get("objective_id")) if run_id else None
        pacote = trilha.get("package") or (self._pacote(trilha["app_id"]) if "app_id" in trilha else "")
        self._gesto(NovoSinal(
            kind=SignalKind.COMANDO_INCERTO_RESOLVIDO, source_ref=f"comando:{resolucao.command_id}",
            created_by=autor_do_gesto(resolucao.quem), polarity=_DO_DESFECHO[resolucao.resolucao],
            note=resolucao.nota, run_id=run_id if ctx is not None else None,
            objective_id=ctx.objective_id if ctx is not None else None,
            instance_id=linhas.texto(comando, "instance_id"),
            profile_id=trilha.get("profile_id") or (ctx.profile_id if ctx is not None else None),
            app_package=pacote or (ctx.app_package if ctx is not None else ""),
            data={"verbo": linhas.texto(comando, "verb"), "resolucao": resolucao.resolucao,
                  **({"resolved_by": resolvido_por} if isinstance(resolvido_por, str) and resolvido_por else {})},
            simulated=ctx.simulated if ctx is not None else resolucao.simulated))

    def correcao_de_ensino(self, correcao: CorrecaoDeEnsino) -> None:
        if not self._ligado():
            return
        ctx = self._contexto(correcao.run_id, step_id=correcao.step_id)
        if ctx is None:
            return
        self._gesto(NovoSinal(
            kind=SignalKind.CORRECAO_DE_ENSINO, source_ref=f"correcao:{correcao.teaching_id}:{correcao.turno}",
            created_by=autor_do_gesto(correcao.quem), polarity=Polaridade.NEGATIVE, note=correcao.nota,
            run_id=correcao.run_id, objective_id=ctx.objective_id, step_id=correcao.step_id,
            instance_id=ctx.instance_id, profile_id=ctx.profile_id, app_package=ctx.app_package,
            capability=ctx.capability, step_hash=ctx.step_hash,
            # Só se corrige etapa que falhou ou ficou sem prova (`CORRECTABLE_STEP`): nunca comprovada.
            step_verified=False, data={"teaching_id": correcao.teaching_id, "skill_id": correcao.skill_id},
            simulated=ctx.simulated))

    def tomou_controle(self, tomada: TomadaDeControle) -> None:
        if not self._ligado():
            return
        ctx = self._contexto(tomada.run_id, objective_id=tomada.objective_id, step_id=tomada.step_id)
        if ctx is None:
            return
        linha = self._db.one("SELECT id FROM attempts WHERE step_id=? AND status='running' ORDER BY number DESC"
                             " LIMIT 1", (tomada.step_id,))
        tentativa = linhas.texto(linha, "id") if linha is not None else None
        self._gesto(NovoSinal(
            kind=SignalKind.TOMOU_CONTROLE, source_ref=f"takeover:{tentativa or tomada.step_id}",
            created_by=autor_do_gesto(tomada.quem), polarity=Polaridade.NEGATIVE, run_id=tomada.run_id,
            objective_id=tomada.objective_id or ctx.objective_id,
            step_id=tomada.step_id, attempt_id=tentativa, instance_id=tomada.instance_id,
            profile_id=ctx.profile_id, app_package=ctx.app_package, capability=ctx.capability,
            step_hash=ctx.step_hash, step_verified=False, simulated=ctx.simulated))

    # ---------------------------------------------------------------- escrita
    def _gesto(self, sinal: NovoSinal) -> None:
        """Grava o sinal de um gesto, UM por `(kind, source_ref)`, qualquer que seja o autor: o primeiro fica
        (`registrar_sinal(um_por_evento=True)`). Vale para os SETE escritores deste pacote.

        Enquanto os gestos do A2 saíam todos `panel`, o autor fixo fazia o `ON CONFLICT (kind, source_ref,
        created_by)` engolir a repetição do mesmo evento. Com o operador da sessão, o mesmo evento feito de novo por
        OUTRA pessoa viraria uma segunda linha — pedir o controle, desistir e outra pessoa pedir na mesma tentativa;
        "abandonar" de novo o item que já falhou, na mesma etapa —, e a régua conta linhas (intervenção por tentativa,
        negativo humano). O índice com o autor é o do voto do D2, em que duas pessoas no mesmo item são duas opiniões,
        e continua valendo para ele.

        **Requisito de quem escreve aqui:** o `source_ref` é a identidade do EVENTO, nunca a de uma classe de eventos
        — a tentativa tomada, o ponto de decisão do item (`ordem`), os itens retomados com a versão nova, o campo da
        execução respondida, o instante da transição do cancelamento, o comando (resolvido uma vez só), o turno da
        correção. Uma chave que deixasse de ser única por evento faria o gesto de OUTRA pessoa sumir sem aviso."""
        self._servico.registrar_sinal(sinal, um_por_evento=True)

    # ---------------------------------------------------------------- leitura
    def _contexto(self, run_id: str, *, objective_id: str | None = None,
                  step_id: str | None = None) -> _Contexto | None:
        """A execução (e a etapa, e o objetivo) do sinal. Sem a execução no banco não há a quem atribuir: nada é
        gravado (e nada é presumido real)."""
        execucao = self._db.one("SELECT simulated, app_ids FROM runs WHERE id=?", (run_id,))
        if execucao is None:
            return None
        simulado = bool(linhas.inteiro(execucao, "simulated"))
        etapa = self._db.one("SELECT objective_id, instance_id, capability, template_hash, app_id FROM steps"
                             " WHERE id=?", (step_id,)) if step_id else None
        objetivo_id = (linhas.texto_ou_nulo(etapa, "objective_id") if etapa is not None else None) or objective_id
        objetivo = self._db.one("SELECT instance_id, profile_id FROM objectives WHERE id=?",
                                (objetivo_id,)) if objetivo_id else None
        app = app_da_etapa(etapa["app_id"] if etapa is not None else None, execucao["app_ids"])
        return _Contexto(
            simulated=simulado, app_package=self._pacote(app),
            capability=(linhas.texto_ou_nulo(etapa, "capability") or "*") if etapa is not None else "",
            step_hash=linhas.texto_ou_nulo(etapa, "template_hash") if etapa is not None else None,
            objective_id=objetivo_id,
            instance_id=(linhas.texto_ou_nulo(etapa, "instance_id") if etapa is not None else None)
            or (linhas.texto_ou_nulo(objetivo, "instance_id") if objetivo is not None else None),
            profile_id=linhas.texto_ou_nulo(objetivo, "profile_id") if objetivo is not None else None)

    def _pacote(self, app: str) -> str:
        """O pacote do app (a chave da régua diária), ou o id quando o cadastro não o conhece; sem app, ''."""
        if not app or app == QUALQUER:
            return ""
        linha = self._db.one("SELECT package FROM apps WHERE id=?", (app,))
        return (linhas.texto_ou_nulo(linha, "package") if linha is not None else None) or app


def _campo(bruto: str) -> str:
    """O nome do campo perguntado, em snake_case ASCII e curto — é identificador, nunca texto de pessoa."""
    return _FORA_DO_CAMPO.sub("_", bruto.strip().lower()).strip("_")[:60]


__all__ = ["PAINEL", "CosturasDoLivro", "Extensoes", "FornecedorDeLicoes", "ObservadorDeTentativa",
           "costuras_do_livro", "extensoes", "ligar"]
