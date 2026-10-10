"""O que mais falha, o backlog da plataforma e a prova da correção (ADR-054, decisão 7; pacote A3).

É o aprendizado para as SESSÕES DE DESENVOLVIMENTO: o relatório diz o que mais custa (US$, minutos, gente), em que
camada mexer e como provar; o backlog guarda o estado de cada grupo (`fk-*`) e a curadoria mede a correção depois do
commit implantado. Nunca chama IA; nunca grava `attempts.failure_kind` (o legado é classificado na leitura e sai
marcado retroativo).

- `relatorio(...)`: só leitura. Lê pelo menos 14 dias (a tendência compara 7 × 7) e mede a janela pedida. O custo de
  cada tentativa vem de UMA fonte: `ai_calls` quando ela começou depois do último corte da purga, a média por
  tentativa do mesmo (dia, app, ação, tipo) em `learning_daily` antes disso; sem nenhuma das duas, `custo_parcial` —
  ausente não é zero.
- `executar(agora)`: o passo `backlog` da curadoria (registrado no `LearningService`). Grava os grupos que passam do
  mínimo e as propostas (upsert pela `cluster_key`, sem mexer no estado) e prova as correções em
  `fixed_pending_proof` (acumulando desde a correção) e `fixed` (a reincidência, numa janela que anda: as últimas
  2 × `prova_minimo` tentativas elegíveis desde a prova, para a volta da falha não se diluir). Idempotente.
- `alterar(...)`: o que a pessoa (ou a sessão de desenvolvimento) marca. `fixed` e `reopened` são medida; a correção
  vai para a prova com o commit, a linha de base das 4 semanas anteriores e o commit que o `/api/health` mostra.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable

from app.modules.learning.application.ports import RepositorioDeAprendizado, TriagemDeTexto
from app.modules.learning.application.servico import NOTA_MAX
from app.modules.learning.domain.backlog import (BASE_DIAS, IDS_DA_PROVA, AcaoLivre, ChamadaDeTela, ChaveDoGrupo,
                                                 FonteDaOcorrencia, GrupoDeFalha, ItemParaPromover, LinhaDoBacklog,
                                                 Medida, Ocorrencia, ParteDeOutro, Proposta, RegrasDoBacklog, Saude, SaudeDasExecucoes,
                                                 TelaQueChamou, TipoDeVerificacao, Veredito, agrupar,
                                                 chave_do_grupo, conferir_alteracao, entra_no_topo, excesso_sem_tela,
                                                 linha_de_grupo,
                                                 linha_de_proposta, medida_de_json, ordenar, parte_de_outro,
                                                 proposta_de_acao, proposta_de_item, provar, telas_que_chamaram,
                                                 tentativas_da_reincidencia)
from app.modules.learning.domain.ciclo import (SYSTEM_ACTOR, ConflitoDeEstado, EntradaInvalida, NaoEncontrado,
                                               NotaComCaraDeSegredo, SkillState)
from app.modules.learning.domain.diagnostico import (AMOSTRA, ContextoDaTentativa, Diagnostico, EstatisticaDaLicao,
                                                     diagnosticar, proposta_do_diagnostico)
from app.modules.learning.domain.ensino_da_falha import para_o_ensino
from app.modules.learning.domain.falhas import Camada
from app.modules.learning.domain.vocabulario import (CategoriaDoBacklog, EstadoDoBacklog, LivroKind, Posicao)
from app.modules.skills.domain.document import JsonObject
from app.util import parse_iso, to_iso

log = logging.getLogger("poc.aprendizado")

#: A tendência compara os últimos 7 dias com os 7 anteriores: a leitura nunca é menor que isto.
LEITURA_MINIMA_DIAS = 14
#: Janela da curadoria (o que ela grava no backlog) e da resolução de um `fk-*` ainda não gravado.
JANELA_DA_CURADORIA_DIAS = 14
JANELA_DE_RESOLUCAO_DIAS = 30
#: Etapa livre comprovada: quantos dias para trás a proposta de ação olha.
JANELA_DAS_ACOES_DIAS = 30
#: Quantos grupos a curadoria diagnostica por passo (os de maior custo): o contexto é lido em lote, mas tem custo.
MAX_DIAGNOSTICOS = 50
#: Estados que o relatório mostra em "Backlog em andamento".
EM_ANDAMENTO = (EstadoDoBacklog.TRIAGED, EstadoDoBacklog.PLANNED, EstadoDoBacklog.FIXED_PENDING_PROOF,
                EstadoDoBacklog.FIXED, EstadoDoBacklog.REOPENED)
_TIPOS_DE_VERIFICACAO = frozenset(k.value for k in TipoDeVerificacao)


# ------------------------------------------------------------------ portas
class FontesDeFalha(Protocol):
    """A leitura do que falhou (tentativas, sinais, régua durável). Só leitura; o erro sai redigido e cortado."""

    def ocorrencias(self, desde: str, ate: str, *, simulados: bool, retroativo: bool,
                    corte_de_custo: str) -> list[Ocorrencia]: ...
    def elegiveis(self, desde: str, ate: str, *, simulados: bool) -> dict[tuple[str, str], int]: ...
    def amostra_elegivel(self, app: str, capability: str, desde: str, ate: str, *, limite: int) -> tuple[str, ...]: ...
    def inicio_das_ultimas(self, app: str, capability: str, desde: str, ate: str, *, n: int) -> str | None:
        """O término da n-ésima tentativa elegível mais recente da (app, ação) em [desde, ate), no mesmo filtro de
        `elegiveis`; `None` com menos de n. É o começo da janela da reincidência."""
        ...

    def chamadas_de_tela(self, desde: str, ate: str, *, simulados: bool) -> list[ChamadaDeTela]: ...
    def acoes_livres(self, desde: str, ate: str) -> list[AcaoLivre]: ...
    def saude(self, desde: str, ate: str, *, simulados: bool) -> SaudeDasExecucoes: ...
    def testemunha_da_purga(self) -> datetime | None:
        """O registro mais antigo que a purga de logs apaga com o MESMO corte de `ai_calls` e que existe todo dia (os
        eventos sem execução): o último corte não passa dele. `None` quando não há nenhum."""
        ...


@runtime_checkable
class FontesDeContexto(Protocol):
    """O contexto das tentativas de um grupo, para o diagnóstico (item 30.13). Porta À PARTE de `FontesDeFalha`: a fonte
    que não a implementa (um dublê de teste) deixa o diagnóstico só com o tipo da falha, `indeterminada` onde o tipo não
    basta — nunca um palpite."""

    def contextos(self, attempt_ids: Sequence[str]) -> dict[str, ContextoDaTentativa]: ...
    def licoes(self, refs: Sequence[str]) -> dict[str, EstatisticaDaLicao]: ...


@dataclass(frozen=True, slots=True)
class ChaveDaTentativa:
    """31.111 F4: o grupo de UMA tentativa (app, ação, tipo, tela), com os tipos de erro do provedor que ela teve."""

    chave: ChaveDoGrupo
    erros_de_ia: Mapping[str, int]


@runtime_checkable
class FontesDaTentativa(Protocol):
    """31.111 F4: a chave de uma tentativa só, para diagnosticar a falha de onde nasce um ensino. Porta à parte, como a
    do contexto: a fonte que não a implementa deixa o ensino sem diagnóstico (nunca com um palpite)."""

    def chave_da_tentativa(self, attempt_id: str) -> ChaveDaTentativa | None: ...


class RepositorioDoBacklog(Protocol):
    def linha(self, backlog_id: str) -> LinhaDoBacklog | None: ...
    def linhas(self, estados: Sequence[EstadoDoBacklog] | None = None) -> list[LinhaDoBacklog]: ...
    def registrar(self, nova: LinhaDoBacklog, *, agora: str) -> bool:
        """Upsert pela `cluster_key`: cria `open` ou só estende `first_seen`/`last_seen` (o estado é da pessoa e da
        prova, nunca do registro)."""
        ...

    def salvar(self, linha: LinhaDoBacklog, *, de_estado: EstadoDoBacklog) -> LinhaDoBacklog:
        """Grava os campos mutáveis com CAS no estado (`ConflitoDeEstado` se outra sessão mudou antes)."""
        ...


# ------------------------------------------------------------------ o relatório
@dataclass(frozen=True, slots=True)
class Janela:
    dias: int
    desde: str
    ate: str
    #: Tentativa iniciada daqui para frente: custo de `ai_calls`; antes, de `learning_daily`.
    corte_de_custo: str
    custo_parcial: bool


@dataclass(frozen=True, slots=True)
class Filtros:
    app: str | None
    camada: Camada | None
    limite: int
    simulados: bool
    retroativo: bool


@dataclass(frozen=True, slots=True)
class LinhaDoRelatorio:
    grupo: GrupoDeFalha
    estado: EstadoDoBacklog
    registrado: bool
    plan_item: str | None
    licoes_ativas: int
    diagnostico: Diagnostico | None = None


@dataclass(frozen=True, slots=True)
class LinhaDaProposta:
    proposta: Proposta
    estado: EstadoDoBacklog
    registrado: bool


@dataclass(frozen=True, slots=True)
class RelatorioDeFalhas:
    gerado_em: str
    commit: str | None
    janela: Janela
    filtros: Filtros
    regras: RegrasDoBacklog
    itens: tuple[LinhaDoRelatorio, ...]
    total_de_grupos: int
    abaixo_do_minimo: int
    verificacao: tuple[GrupoDeFalha, ...]
    telas: tuple[TelaQueChamou, ...]
    propostas: tuple[LinhaDaProposta, ...]
    outro: ParteDeOutro
    saude: Saude
    em_andamento: tuple[LinhaDoBacklog, ...]


@dataclass(frozen=True, slots=True)
class DetalheDoBacklog:
    linha: LinhaDoBacklog
    registrado: bool
    grupo: GrupoDeFalha | None
    proposta: Proposta | None
    diagnostico: Diagnostico | None = None


# ------------------------------------------------------------------ o serviço
class ServicoDeFalhas:
    #: É também um passo da curadoria (`PassoDeCuradoria`): `LearningService.curar` o roda a cada `curadoria_s`.
    nome = "backlog"

    def __init__(self, fontes: FontesDeFalha, backlog: RepositorioDoBacklog, livro: RepositorioDeAprendizado,
                 triagem: TriagemDeTexto, *, regras: Callable[[], RegrasDoBacklog], relogio: Callable[[], datetime],
                 commit: Callable[[], str | None],
                 contagem_do_livro: Callable[[], Mapping[str, Mapping[str, int]]]) -> None:
        """`commit`: o commit que ESTE processo carregou (o mesmo do `/api/health`). `contagem_do_livro`: itens por
        tipo e estado, para a seção de saúde."""
        self._fontes = fontes
        self._backlog = backlog
        self._livro = livro
        self._triagem = triagem
        self._regras = regras
        self._relogio = relogio
        self._commit = commit
        self._contagem_do_livro = contagem_do_livro

    @property
    def regras(self) -> RegrasDoBacklog:
        """As regras VIGENTES (o config muda com o processo no ar)."""
        return self._regras()

    # ================================================================== leitura
    def relatorio(self, *, dias: int = 14, app: str | None = None, camada: Camada | None = None, limite: int = 20,
                  simulados: bool = False, retroativo: bool = True) -> RelatorioDeFalhas:
        agora = self._relogio()
        regras = self._regras()
        dias = max(1, int(dias))
        desde, ate = to_iso(agora - timedelta(days=dias)), to_iso(agora)
        corte, ocorrencias = self._ler(agora, max(dias, LEITURA_MINIMA_DIAS), simulados=simulados,
                                       retroativo=retroativo)
        if app:
            ocorrencias = [o for o in ocorrencias if o.chave.app == app]
        na_janela = [o for o in ocorrencias if o.quando >= desde]
        grupos = agrupar(ocorrencias, desde=desde, agora=agora,
                         elegiveis=self._fontes.elegiveis(desde, ate, simulados=simulados))
        candidatos = [g for g in grupos if entra_no_topo(g, camada=camada)]
        topo = ordenar((g for g in candidatos if g.ocorrencias >= regras.minimo_ocorrencias), regras)
        gravadas = {x.id: x for x in self._backlog.linhas()}
        licoes = self._licoes_publicadas()
        mostrados = topo[:max(1, int(limite))]
        diagnosticos = self._diagnosticar(mostrados, ocorrencias, desde)
        itens = tuple(LinhaDoRelatorio(g, gravadas[g.id].state if g.id in gravadas else EstadoDoBacklog.OPEN,
                                       g.id in gravadas, gravadas[g.id].plan_item if g.id in gravadas else None,
                                       _licoes_do_grupo(licoes, g.chave), diagnosticos.get(g.id))
                      for g in mostrados)
        propostas = tuple(LinhaDaProposta(p, gravadas[p.id].state if p.id in gravadas else EstadoDoBacklog.OPEN,
                                          p.id in gravadas)
                          for p in self._propostas(agora, _propostas_dos(mostrados, diagnosticos))
                          if not app or p.app == app)
        s = self._fontes.saude(desde, ate, simulados=simulados)
        saude = Saude(itens_por_tipo=self._contagem_do_livro(), execucoes=s.execucoes,
                      execucoes_com_fluxo=s.execucoes_com_fluxo, fluxos_distintos=s.fluxos_distintos,
                      etapas_por_conducao=s.etapas_por_conducao, intervencoes=s.intervencoes,
                      exploracoes_por_conducao=s.exploracoes_por_conducao,
                      verificacoes_de_sessao=s.verificacoes_de_sessao)
        em_andamento = tuple(sorted((x for x in gravadas.values() if x.state in EM_ANDAMENTO),
                                    key=lambda x: (EM_ANDAMENTO.index(x.state), x.updated_at or "", x.id)))
        return RelatorioDeFalhas(
            gerado_em=ate, commit=self._commit(),
            janela=Janela(dias, desde, ate, corte, any(not o.custo_conhecido for o in na_janela)),
            filtros=Filtros(app or None, camada, max(1, int(limite)), simulados, retroativo), regras=regras,
            itens=itens, total_de_grupos=len(grupos), abaixo_do_minimo=len(candidatos) - len(topo),
            verificacao=tuple(ordenar((g for g in grupos if g.chave.tipo in _TIPOS_DE_VERIFICACAO), regras)),
            telas=tuple(telas_que_chamaram(self._fontes.chamadas_de_tela(desde, ate, simulados=simulados),
                                           na_janela)),
            propostas=propostas, outro=parte_de_outro(na_janela), saude=saude, em_andamento=em_andamento)

    def diagnostico_da_tentativa(self, attempt_id: str) -> Diagnostico | None:
        """31.111 F4: o diagnóstico (30.13) da tentativa que falhou, com o contexto DELA. `None` sem fonte, sem a
        tentativa ou sem tipo de falha. Determinístico, sem IA; erro de leitura do contexto deixa só o tipo decidir."""
        if not isinstance(self._fontes, FontesDaTentativa):
            return None
        k = self._fontes.chave_da_tentativa(attempt_id)
        if k is None:
            return None
        contextos: dict[str, ContextoDaTentativa] = {}
        estatisticas: dict[str, EstatisticaDaLicao] = {}
        if isinstance(self._fontes, FontesDeContexto):
            try:
                contextos = self._fontes.contextos([attempt_id])
                refs = sorted({r for c in contextos.values() for r in c.licoes})
                estatisticas = self._fontes.licoes(refs) if refs else {}
            except Exception:                                  # noqa: BLE001 — o ensino não depende do contexto
                log.exception("aprendizado: o contexto da tentativa não pôde ser lido; o diagnóstico usa só o tipo")
                contextos, estatisticas = {}, {}
        return diagnosticar(k.chave, list(contextos.values()), licoes=estatisticas, erros_de_ia=k.erros_de_ia)

    def diagnostico_para_o_ensino(self, attempt_id: str) -> dict[str, object] | None:
        """O `origin.diagnostico` da sessão de ensino (31.111 F4): o diagnóstico em linguagem de gente, ou `None`. Nunca
        derruba a leitura da sessão."""
        try:
            d = self.diagnostico_da_tentativa(attempt_id)
        except Exception:                                      # noqa: BLE001 — a sessão abre sem o diagnóstico
            log.exception("aprendizado: o diagnóstico da tentativa %s falhou", attempt_id)
            return None
        return para_o_ensino(d) if d is not None else None

    def linha(self, backlog_id: str) -> DetalheDoBacklog:
        """A linha gravada ou, se a curadoria ainda não a gravou, a que o relatório de 30 dias mostra (sem gravar)."""
        gravada = self._backlog.linha(backlog_id)
        agora = self._relogio()
        grupo, ocorrencias = self._grupo(backlog_id, agora)
        diagnostico = None
        if grupo is not None:
            desde = to_iso(agora - timedelta(days=JANELA_DE_RESOLUCAO_DIAS))
            diagnostico = self._diagnosticar([grupo], ocorrencias, desde).get(grupo.id)
        proposta = next((p for p in self._propostas(agora) if p.id == backlog_id), None)
        if proposta is None and (gravada is None or gravada.category is CategoriaDoBacklog.PROPOSTA):
            proposta = self._proposta_do_diagnostico(backlog_id, gravada, agora)
        if gravada is not None:
            return DetalheDoBacklog(gravada, True, grupo, proposta, diagnostico)
        if grupo is not None:
            return DetalheDoBacklog(linha_de_grupo(grupo), False, grupo, None, diagnostico)
        if proposta is not None:
            return DetalheDoBacklog(linha_de_proposta(proposta, to_iso(agora)), False, None, proposta)
        raise NaoEncontrado(f"Não há '{backlog_id}' no backlog nem no que falhou nos últimos "
                            f"{JANELA_DE_RESOLUCAO_DIAS} dias.")

    # ================================================================== escrita da pessoa
    def alterar(self, backlog_id: str, *, estado: EstadoDoBacklog, by: str, plan_item: str | None = None,
                fixed_in_commit: str | None = None, notes: str | None = None) -> LinhaDoBacklog:
        if not by.strip():
            raise EntradaInvalida("A alteração precisa dizer quem a fez.")
        commit = conferir_alteracao(estado, fixed_in_commit)
        nota = self._texto_de_pessoa(notes, NOTA_MAX)
        plano = self._texto_de_pessoa(plan_item, 60)
        linha = self._garantir(backlog_id)
        if linha.category is CategoriaDoBacklog.PROPOSTA and estado is EstadoDoBacklog.FIXED_PENDING_PROOF:
            raise EntradaInvalida("Proposta não tem prova automática: marque 'planned' com o item do plano, ou "
                                  "'wontfix'.")
        agora = self._relogio()
        quando = to_iso(agora)
        nova = replace(linha, state=estado, updated_by=by, updated_at=quando,
                       plan_item=plano if plan_item is not None else linha.plan_item,
                       notes=nota if notes is not None else linha.notes)
        if estado is EstadoDoBacklog.FIXED_PENDING_PROOF and commit is not None:
            chave = _chave_da_linha(linha)
            base = self._medir(chave, to_iso(agora - timedelta(days=BASE_DIAS)), quando, agora)
            nova = replace(nova, fixed_in_commit=commit, fixed_at=quando, baseline=_json_da_medida(chave, base),
                           verification={"commit_implantado": self._commit(), "fixed_in_commit": commit,
                                         "desde": quando})
        return self._backlog.salvar(nova, de_estado=linha.state)

    def _texto_de_pessoa(self, texto: str | None, maximo: int) -> str | None:
        limpo = (texto or "").strip()
        if not limpo:
            return None
        if self._triagem.recusa(limpo):
            raise NotaComCaraDeSegredo("O texto tem formato ou assunto de credencial e não foi gravado.")
        return self._triagem.redigir(limpo)[:maximo] or None

    def _garantir(self, backlog_id: str) -> LinhaDoBacklog:
        """A linha gravada; ou, se a curadoria ainda não passou (roda a cada 15 min), a do relatório, gravada agora."""
        gravada = self._backlog.linha(backlog_id)
        if gravada is not None:
            return gravada
        detalhe = self.linha(backlog_id)
        self._backlog.registrar(detalhe.linha, agora=to_iso(self._relogio()))
        gravada = self._backlog.linha(backlog_id)
        if gravada is None:
            raise ConflitoDeEstado(f"{backlog_id}: a linha não pôde ser gravada; tente de novo.")
        return gravada

    # ================================================================== curadoria
    def executar(self, agora: datetime) -> int:
        """Grava os grupos que passam do mínimo e as propostas; prova as correções. Idempotente."""
        regras = self._regras()
        desde = to_iso(agora - timedelta(days=JANELA_DA_CURADORIA_DIAS))
        quando = to_iso(agora)
        _, ocorrencias = self._ler(agora, JANELA_DA_CURADORIA_DIAS, simulados=False, retroativo=True)
        escritas = 0
        gravaveis: list[GrupoDeFalha] = []
        for g in agrupar(ocorrencias, desde=desde, agora=agora, elegiveis={}):
            if entra_no_topo(g, camada=None) and g.ocorrencias >= regras.minimo_ocorrencias:
                escritas += int(self._backlog.registrar(linha_de_grupo(g), agora=quando))
                gravaveis.append(g)
        # As propostas do diagnóstico entram DEPOIS do grupo (o `parent_id` aponta para uma linha que já existe).
        mais_caros = ordenar(gravaveis, regras)[:MAX_DIAGNOSTICOS]
        do_diagnostico = _propostas_dos(mais_caros, self._diagnosticar(mais_caros, ocorrencias, desde))
        for p in self._propostas(agora, do_diagnostico):
            escritas += int(self._backlog.registrar(linha_de_proposta(p, quando), agora=quando))
        for linha in self._backlog.linhas((EstadoDoBacklog.FIXED_PENDING_PROOF, EstadoDoBacklog.FIXED)):
            try:
                escritas += self._provar(linha, agora, regras)
            except ConflitoDeEstado:
                log.info("aprendizado: %s mudou durante a prova; fica para o próximo passo", linha.id)
        return escritas

    def _provar(self, linha: LinhaDoBacklog, agora: datetime, regras: RegrasDoBacklog) -> int:
        if linha.category is not CategoriaDoBacklog.FALHA or not linha.fixed_at:
            return 0
        chave = _chave_da_linha(linha)
        if chave.id != linha.id:
            log.warning("aprendizado: %s com a chave '%s' que não bate com o id; sem prova", linha.id,
                        linha.cluster_key)
            return 0
        quando = to_iso(agora)
        base = medida_de_json(linha.baseline) if linha.baseline else None
        if base is None:
            base = self._medir(chave, to_iso(_instante(linha.fixed_at, agora) - timedelta(days=BASE_DIAS)),
                               linha.fixed_at, agora)
        anterior: JsonObject = dict(linha.verification or {})
        provado_em = anterior.get("provado_em")
        corrigida = linha.state is EstadoDoBacklog.FIXED
        desde = provado_em if corrigida and isinstance(provado_em, str) else linha.fixed_at
        n = tentativas_da_reincidencia(regras)
        if corrigida:
            # A reincidência anda: só as últimas n elegíveis desde a prova. Medida em [provado_em, agora), a janela só
            # cresceria e as boas de semanas diluiriam a volta concentrada da falha — a linha nunca mais reabriria.
            inicio = self._fontes.inicio_das_ultimas(chave.app, chave.capability, desde, quando, n=n)
            if inicio is not None and inicio > desde:
                desde = inicio
        depois = self._medir(chave, desde, quando, agora)
        # A linha COM tela também conta a mesma falha que passou a cair na tela desconhecida acima da base dela
        # (`excesso_sem_tela`, item 22.3): a tela que deixou de ser reconhecida não passa por correção. `ocorrencias`
        # na verificação segue sendo só a da própria tela; o excesso vai à parte, e no motivo.
        na_prova, sem_tela = depois, None
        if chave.tela:
            na_prova, sem_tela = self._com_a_tela_desconhecida(chave, base, depois, agora)
        r = provar(linha.state, base, na_prova, regras)
        motivo = r.motivo
        if excesso := na_prova.ocorrencias - depois.ocorrencias:
            motivo += (f" — contando {excesso} falha(s) do mesmo tipo em tela desconhecida acima da base"
                       " (a tela pode ter deixado de ser reconhecida)")
        medida: JsonObject = {**_json_da_medida(chave, depois), "faltam": r.faltam, "limite": round(r.limite, 4),
                              "veredito": r.veredito.value, "motivo": motivo, "medido_em": quando}
        if sem_tela is not None:
            medida["sem_tela"] = sem_tela
        if corrigida:                                   # a prova fica; a reincidência é medida à parte
            verificacao: JsonObject = {**anterior, "reincidencia": {**medida, "janela_de_tentativas": n}}
        else:
            verificacao = {**{k: v for k, v in anterior.items() if k in ("commit_implantado", "fixed_in_commit")},
                           **medida}
            if r.novo_estado is EstadoDoBacklog.FIXED:
                verificacao["provado_em"] = quando
        nova = replace(linha, baseline=_json_da_medida(chave, base), verification=verificacao, updated_at=quando,
                       updated_by=SYSTEM_ACTOR)
        if r.novo_estado is not None:
            nova = replace(nova, state=r.novo_estado)
            if r.veredito is Veredito.REABRE:
                nova = replace(nova, reopened_count=linha.reopened_count + 1)
                log.warning("aprendizado: %s reaberto (%s): %s", linha.id, linha.title, motivo)
            else:
                log.info("aprendizado: %s corrigido e provado (%s): %s", linha.id, linha.title, motivo)
        self._backlog.salvar(nova, de_estado=linha.state)
        return 1

    def _com_a_tela_desconhecida(self, chave: ChaveDoGrupo, base: Medida, depois: Medida,
                                 agora: datetime) -> tuple[Medida, JsonObject]:
        """A medida da prova de uma linha COM tela somada ao excesso da tela desconhecida do mesmo trio, e o registro
        dele. O grupo '' é medido EXATO (`conta`): pela regra de `abrange`, a chave sem tela contaria o trio inteiro,
        a própria linha inclusive. A base dele é a mesma janela da linha de base da linha."""
        exata = chave.sem_tela
        base_st = self._medir(exata, base.desde, base.ate, agora, conta=exata.__eq__)
        depois_st = self._medir(exata, depois.desde, depois.ate, agora, conta=exata.__eq__)
        excesso = excesso_sem_tela(base_st, depois_st)
        taxa_base = base_st.taxa
        registro: JsonObject = {"ocorrencias": depois_st.ocorrencias, "excesso": excesso,
                                "taxa_base": None if taxa_base is None else round(taxa_base, 4)}
        return replace(depois, ocorrencias=depois.ocorrencias + excesso), registro

    # ================================================================== apoio
    def _corte_de_custo(self, agora: datetime, desde: str) -> str:
        """A tentativa que COMEÇOU daqui para frente tem as chamadas de IA inteiras em `ai_calls` (o custo dela vem de
        lá); a que começou antes pode ter perdido chamadas para a purga, e o custo dela vem da régua durável.

        - sem chamada nenhuma: ou nunca houve IA, ou a purga levou tudo — a régua decide;
        - nunca purgado (a primeira chamada é a primeira de verdade): tudo inteiro;
        - purgado: o último corte não passa da chamada mais antiga que sobrou NEM do evento sem execução mais antigo
          (a mesma purga apaga os dois com o mesmo corte, e esses eventos existem todo dia). A testemunha desfaz o
          falso sinal de purga de um banco cuja primeira tentativa real terminou sem chamada de IA (o central, em
          17/09) — sem ela, o custo daqueles dias sairia desconhecido.
        """
        primeira = self._livro.primeira_chamada()
        if primeira is None:
            amanha = (agora + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            tem_regua = bool(self._livro.dias_com_diario(desde[:10], amanha.strftime("%Y-%m-%d")))
            return to_iso(amanha) if tem_regua else desde
        if not primeira.purgada_antes:
            return desde
        testemunha = self._fontes.testemunha_da_purga()
        return to_iso(primeira.ts if testemunha is None else min(primeira.ts, testemunha))

    def _ler(self, agora: datetime, dias: int, *, simulados: bool, retroativo: bool) -> tuple[str, list[Ocorrencia]]:
        desde = to_iso(agora - timedelta(days=dias))
        corte = self._corte_de_custo(agora, desde)
        return corte, self._fontes.ocorrencias(desde, to_iso(agora), simulados=simulados, retroativo=retroativo,
                                               corte_de_custo=corte)

    def _medir(self, chave: ChaveDoGrupo, desde: str, ate: str, agora: datetime, *,
               conta: Callable[[ChaveDoGrupo], bool] | None = None) -> Medida:
        """Tentativas reais elegíveis da mesma (app, ação) e ocorrências do grupo em [desde, ate). A linha sem tela
        abrange a mesma falha em qualquer tela (`ChaveDoGrupo.abrange`, item 22.3) — na base e no depois, para as
        duas medidas contarem a mesma coisa. `conta` troca essa regra (a medida exata do grupo da tela
        desconhecida)."""
        corte = self._corte_de_custo(agora, desde)
        entra = conta or chave.abrange
        ocorrencias = [o for o in self._fontes.ocorrencias(desde, ate, simulados=False, retroativo=True,
                                                           corte_de_custo=corte) if entra(o.chave)]
        elegiveis = self._fontes.elegiveis(desde, ate, simulados=False).get(chave.app_e_acao, 0)
        ids = [o.attempt_id or o.run_id for o in ocorrencias]
        ids.extend(self._fontes.amostra_elegivel(chave.app, chave.capability, desde, ate, limite=IDS_DA_PROVA))
        unicos = tuple(dict.fromkeys(i for i in ids if i))[:IDS_DA_PROVA]
        return Medida(desde, ate, elegiveis, len(ocorrencias), unicos, sum(o.usd for o in ocorrencias))

    def _grupo(self, backlog_id: str, agora: datetime) -> tuple[GrupoDeFalha | None, list[Ocorrencia]]:
        desde = to_iso(agora - timedelta(days=JANELA_DE_RESOLUCAO_DIAS))
        _, ocorrencias = self._ler(agora, JANELA_DE_RESOLUCAO_DIAS, simulados=False, retroativo=True)
        alvo = [o for o in ocorrencias if o.chave.id == backlog_id]
        if not alvo:
            return None, []
        grupos = agrupar(alvo, desde=desde, agora=agora,
                         elegiveis=self._fontes.elegiveis(desde, to_iso(agora), simulados=False))
        return (grupos[0] if grupos else None), alvo

    # ------------------------------------------------------------------ diagnóstico (item 30.13)
    def _diagnosticar(self, grupos: Sequence[GrupoDeFalha], ocorrencias: Sequence[Ocorrencia],
                      desde: str) -> dict[str, Diagnostico]:
        """O diagnóstico determinístico de cada grupo, lendo o contexto das últimas `AMOSTRA` tentativas dele em UMA ida
        à fonte. Sem fonte de contexto, ou se ela falhar, só o tipo decide — escrito (`sem_contexto`), nunca um palpite;
        o relatório não cai por causa do diagnóstico."""
        por_chave: dict[ChaveDoGrupo, list[Ocorrencia]] = defaultdict(list)
        for o in ocorrencias:
            if o.fonte is FonteDaOcorrencia.TENTATIVA and o.attempt_id and o.quando >= desde:
                por_chave[o.chave].append(o)
        amostras = {g.id: [o.attempt_id for o in sorted(por_chave.get(g.chave, ()), key=lambda o: o.quando)[-AMOSTRA:]
                           if o.attempt_id] for g in grupos}
        contextos: dict[str, ContextoDaTentativa] = {}
        estatisticas: dict[str, EstatisticaDaLicao] = {}
        if isinstance(self._fontes, FontesDeContexto):
            try:
                contextos = self._fontes.contextos(sorted({i for ids in amostras.values() for i in ids}))
                refs = sorted({r for c in contextos.values() for r in c.licoes})
                estatisticas = self._fontes.licoes(refs) if refs else {}
            except Exception:                                  # noqa: BLE001 — o relatório não depende do diagnóstico
                log.exception("aprendizado: o contexto das falhas não pôde ser lido; o diagnóstico usa só o tipo")
                contextos, estatisticas = {}, {}
        return {g.id: diagnosticar(g.chave, [contextos[i] for i in amostras[g.id] if i in contextos],
                                   licoes=estatisticas, erros_de_ia=g.erros_de_ia) for g in grupos}

    def _proposta_do_diagnostico(self, backlog_id: str, gravada: LinhaDoBacklog | None,
                                 agora: datetime) -> Proposta | None:
        """A proposta do diagnóstico com este id: pelo grupo-pai da linha gravada; sem linha, varrendo os grupos do período."""
        desde = to_iso(agora - timedelta(days=JANELA_DE_RESOLUCAO_DIAS))
        regras = self._regras()
        if gravada is not None and gravada.parent_id:
            grupo, ocorrencias = self._grupo(gravada.parent_id, agora)
            grupos = [grupo] if grupo is not None else []
        else:
            _, ocorrencias = self._ler(agora, JANELA_DE_RESOLUCAO_DIAS, simulados=False, retroativo=True)
            grupos = ordenar((g for g in agrupar(ocorrencias, desde=desde, agora=agora, elegiveis={})
                              if entra_no_topo(g, camada=None) and g.ocorrencias >= regras.minimo_ocorrencias),
                             regras)[:MAX_DIAGNOSTICOS]
        propostas = _propostas_dos(grupos, self._diagnosticar(grupos, ocorrencias, desde))
        return next((p for p in propostas if p.id == backlog_id), None)

    def _propostas(self, agora: datetime, do_diagnostico: Sequence[Proposta] = ()) -> list[Proposta]:
        """As propostas do diagnóstico vêm primeiro: são as que têm custo medido atrás."""
        acoes = self._fontes.acoes_livres(to_iso(agora - timedelta(days=JANELA_DAS_ACOES_DIAS)), to_iso(agora))
        saida = [*do_diagnostico, *(p for p in (proposta_de_acao(a) for a in acoes) if p is not None)]
        for item in self._itens_para_promover():
            proposta = proposta_de_item(item, agora)
            if proposta is not None:
                saida.append(proposta)
        return saida

    def _itens_para_promover(self) -> list[ItemParaPromover]:
        saida: list[ItemParaPromover] = []
        for kind in (LivroKind.LICAO, LivroKind.TELA):
            for i in self._livro.itens(kind=kind, state=SkillState.PUBLISHED):
                conflitos = sum(1 for e in self._livro.evidencias(i.id) if e.stance is Posicao.CONFLICT)
                saida.append(ItemParaPromover(id=i.id, kind=kind.value, app=i.escopo.app or "*", summary=i.summary,
                                              state=i.state.value, state_detail=i.state_detail, state_at=i.state_at,
                                              contra=i.evidence_against, conflitos=conflitos))
        return saida

    def _licoes_publicadas(self) -> list[tuple[str, str]]:
        return [(i.escopo.app, i.escopo.capability)
                for i in self._livro.itens(kind=LivroKind.LICAO, state=SkillState.PUBLISHED)]


def _propostas_dos(grupos: Sequence[GrupoDeFalha], diagnosticos: Mapping[str, Diagnostico]) -> list[Proposta]:
    return [p for g in grupos if g.id in diagnosticos
            if (p := proposta_do_diagnostico(g.chave, diagnosticos[g.id])) is not None]


def _licoes_do_grupo(licoes: Sequence[tuple[str, str]], chave: ChaveDoGrupo) -> int:
    """Lições publicadas para o escopo do grupo ('' no escopo da lição = qualquer)."""
    return sum(1 for app, acao in licoes if app in ("", chave.app) and acao in ("", chave.capability))


def _chave_da_linha(linha: LinhaDoBacklog) -> ChaveDoGrupo:
    return chave_do_grupo(linha.app_package, linha.capability, linha.failure_kind or "", linha.failure_screen)


def _json_da_medida(chave: ChaveDoGrupo, m: Medida) -> JsonObject:
    """A medida gravada (base e verificação) diz o que ela conta: `abrange` = `trio` na linha sem tela (o número pode
    passar do que o detalhe da linha mostra, que é só o grupo exato) ou `tela`."""
    return {**m.json(), "abrange": chave.alcance}


def _instante(iso: str, padrao: datetime) -> datetime:
    try:
        return parse_iso(iso) or padrao
    except ValueError:
        return padrao


__all__ = ["DetalheDoBacklog", "Filtros", "FontesDeContexto", "FontesDeFalha", "Janela", "LinhaDaProposta", "LinhaDoRelatorio",
           "RelatorioDeFalhas", "RepositorioDoBacklog", "ServicoDeFalhas"]
