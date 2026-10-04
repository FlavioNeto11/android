"""A leitura do que falhou e o repositório do backlog (ADR-054, decisão 7; pacote A3), nos dois dialetos.

Pelas regras de `desempenho.py`: o SQL só filtra por texto ISO-8601 (ordena igual no SQLite e no PostgreSQL), as
durações são calculadas aqui em Python, ausente é `None` (não zero) e o simulado fica fora por padrão.

O que se lê, e de onde:
- tentativas `failed`/`uncertain`/`interrupted` (`attempts`, que não vence): o tipo GRAVADO (A2) ou, no legado, o
  mesmo classificador puro na leitura — marcado retroativo, NUNCA gravado;
- o custo, UMA fonte por tentativa: as chamadas de `ai_calls` dela quando ela começou depois do corte da purga (por
  `attempt_id`; a chamada anterior à 045, só com `step_id`, é da tentativa que estava em andamento no instante dela —
  a última da etapa que começou antes; a régua diária do A1 a põe na última tentativa da etapa, e aqui isso tiraria o
  custo justamente da falha que precedeu o sucesso); antes do corte, o custo médio por tentativa do mesmo (dia, app,
  ação, tipo) em `learning_daily`;
- os sinais de pessoa que desmentem o verificador (`feedback` e `confirmou_a_mao`) e as intervenções (`tomou_controle`,
  `tela_desconhecida_chamou_pessoa`) ligadas pela tentativa ou pela etapa.

O erro dos exemplos sai REDIGIDO (`security/redaction.py`) e cortado em 200 caracteres: o relatório vai para a
sessão de desenvolvimento, e segredo nunca sai em relatório.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from datetime import datetime

from app.db import Database, Row
from app.modules.learning.domain.backlog import (SEM_CONDUCAO, AcaoLivre, ChamadaDeTela, FonteDaOcorrencia,
                                                 LinhaDoBacklog, Ocorrencia, SaudeDasExecucoes, TipoDeVerificacao,
                                                 chave_do_grupo)
from app.modules.learning.domain.ciclo import ConflitoDeEstado
from app.modules.learning.domain.falhas import classificar_falha, classificar_pelo_tipo_da_ia
from app.modules.learning.domain.vocabulario import (SINAIS_DE_INTERVENCAO, CategoriaDoBacklog, EstadoDoBacklog,
                                                     SignalKind)
from app.modules.learning.domain.diagnostico import ContextoDaTentativa, EstatisticaDaLicao
from app.modules.learning.infrastructure import linhas
from app.modules.learning.infrastructure.contexto_sql import ContextoDeFalhaSql
from app.modules.skills.domain.document import JsonObject, NotJson, canonical_json, parse_json_object
from app.planning import costs
from app.security.redaction import redact
from app.taskqueue.projecao import app_da_etapa
from app.util import parse_iso

#: Tamanho máximo do erro de um exemplo (depois de redigido).
ERRO_MAX = 200
_FALHAS = ("failed", "uncertain", "interrupted")
#: Etapas que contam na condução (`steps.driven_by`): as que chegaram a um desfecho.
_ETAPA_COM_DESFECHO = ("succeeded", "failed", "uncertain", "waiting_user")
#: 30.59: a condução que fecha a etapa sem IA (receita pura ou atalho do executor, LT-1).
_SEM_IA = frozenset({"recipe", "sem_ator"})
_INTERVENCOES_LIGADAS = (SignalKind.TOMOU_CONTROLE.value, SignalKind.TELA_DESCONHECIDA_CHAMOU_PESSOA.value)
#: Tamanho do lote de um `IN (...)` (o limite de parâmetros do SQLite antigo é 999).
_LOTE = 400
#: Tentativas lidas por vez ao procurar o começo da janela da reincidência (o app sai do pacote, filtrado aqui).
_LOTE_DA_JANELA = 100


def _segundos(inicio: str | None, fim: str | None) -> float | None:
    try:
        a, b = parse_iso(inicio), parse_iso(fim)
    except ValueError:
        return None
    if a is None or b is None:
        return None
    return max(0.0, (b - a).total_seconds())


def _inicio(t: Row) -> str:
    """Quando a tentativa começou (sem início gravado, quando terminou): é o que diz se as chamadas dela estão inteiras."""
    return linhas.texto_ou_nulo(t, "started_at") or linhas.texto(t, "finished_at")


def _em_andamento(tentativas: Sequence[tuple[str, str]], ts: str) -> str | None:
    dona: str | None = None
    for aid, inicio in tentativas:
        if inicio <= ts:
            dona = aid
    return dona or (tentativas[0][0] if tentativas else None)


def _erro(texto: str | None) -> str | None:
    if not texto:
        return None
    return (redact(texto) or "")[:ERRO_MAX] or None


class FontesDeFalhaSql:
    def __init__(self, db: Database, *, precos: Callable[[], dict[str, list[float]]] | None = None) -> None:
        """`precos`: a tabela de `ai.prices`, para o US$ das chamadas antigas sem `usd` gravado (anteriores à 048)."""
        self._db = db
        self._precos = precos if precos is not None else dict
        self._contexto = ContextoDeFalhaSql(db)

    # ================================================================== contexto do diagnóstico (item 30.13)
    def contextos(self, attempt_ids: Sequence[str]) -> dict[str, ContextoDaTentativa]:
        return self._contexto.contextos(attempt_ids)

    def licoes(self, refs: Sequence[str]) -> dict[str, EstatisticaDaLicao]:
        return self._contexto.licoes(refs)

    # ================================================================== apoio
    def _pacotes(self) -> dict[str, str]:
        return {linhas.texto(r, "id"): linhas.texto_ou_nulo(r, "package") or linhas.texto(r, "id")
                for r in self._db.query("SELECT id, package FROM apps")}

    @staticmethod
    def _app(pacotes: dict[str, str], row: Row) -> str:
        app = app_da_etapa(row["app_id"], row["app_ids"])
        return pacotes.get(app, app)

    # ================================================================== ocorrências
    def ocorrencias(self, desde: str, ate: str, *, simulados: bool, retroativo: bool,
                    corte_de_custo: str) -> list[Ocorrencia]:
        return self._das_tentativas(desde, ate, simulados=simulados, retroativo=retroativo,
                                    corte=corte_de_custo) + self._dos_sinais(desde, ate, simulados=simulados)

    def _das_tentativas(self, desde: str, ate: str, *, simulados: bool, retroativo: bool,
                        corte: str) -> list[Ocorrencia]:
        pacotes = self._pacotes()
        marcas = ",".join("?" for _ in _FALHAS)
        tentativas = self._db.query(
            "SELECT a.id, a.step_id, a.number, a.status, a.error, a.failure_kind, a.failure_screen, a.error_kind,"
            " a.started_at,"
            " a.finished_at, s.capability, s.app_id, s.status AS step_status, s.run_id, s.instance_id, r.app_ids,"
            " r.simulated, (SELECT MAX(x.number) FROM attempts x WHERE x.step_id = a.step_id) AS ultima"
            " FROM attempts a JOIN steps s ON s.id = a.step_id JOIN runs r ON r.id = s.run_id"
            f" WHERE a.finished_at >= ? AND a.finished_at < ? AND a.status IN ({marcas})"
            + ("" if simulados else " AND r.simulated = 0") + " ORDER BY a.finished_at, a.id",
            (desde, ate, *_FALHAS))
        escolhidas: list[tuple[Row, str, bool]] = []
        for t in tentativas:
            gravado = linhas.texto_ou_nulo(t, "failure_kind")
            if gravado is None and not retroativo:
                continue
            tipo = gravado or classificar_falha(linhas.texto_ou_nulo(t, "error"), linhas.texto_ou_nulo(t, "status"),
                                                linhas.texto_ou_nulo(t, "error_kind"))
            if tipo:
                escolhidas.append((t, str(tipo), gravado is None))
        recentes = [t for t, _, _ in escolhidas if _inicio(t) >= corte]
        custos, erros_de_ia = self._custos(recentes, corte, ate)
        diario = self._diario(desde[:10], corte[:10]) if len(recentes) < len(escolhidas) else {}
        por_tentativa, por_etapa = self._intervencoes_ligadas(desde, ate, simulados=simulados)
        ultima_ocorrencia: dict[str, str] = {}
        for t, _, _ in escolhidas:                          # em ordem de término: a última da etapa fica
            ultima_ocorrencia[linhas.texto(t, "step_id")] = linhas.texto(t, "id")
        saida: list[Ocorrencia] = []
        for t, tipo, retro in escolhidas:
            aid, sid = linhas.texto(t, "id"), linhas.texto(t, "step_id")
            # O TIPO do erro do provedor (`ai_calls.error_kind`, o `AIError.kind`) vence o texto: o gravado em
            # `attempts.failure_kind` também sai de texto (`repository.finish_attempt`), e o teto do pedido, por
            # exemplo, ficava em `outro`. Só na leitura retroativa: `retroativo=0` conta o que a execução gravou. E só
            # sem `attempts.error_kind` (RA-22): com ele, o gravado já saiu do tipo do erro que ENCERROU a tentativa,
            # e uma chamada anterior que o roteador contornou não o desmente.
            pelo_tipo = classificar_pelo_tipo_da_ia(erros_de_ia.get(aid, ()))
            if (retroativo and pelo_tipo is not None and pelo_tipo.value != tipo
                    and linhas.texto_ou_nulo(t, "error_kind") is None
                    and linhas.texto_ou_nulo(t, "status") != "interrupted"):
                tipo, retro = pelo_tipo.value, True
            fim = linhas.texto(t, "finished_at")
            app = self._app(pacotes, t)
            chave = chave_do_grupo(app, linhas.texto_ou_nulo(t, "capability"), tipo,
                                   linhas.texto_ou_nulo(t, "failure_screen"))
            simulada = bool(linhas.inteiro(t, "simulated"))
            do_diario = _inicio(t) < corte
            if do_diario and not simulada:
                por_tentativa_no_dia = diario.get((fim[:10], chave.app, chave.capability, tipo))
                usd, conhecido = (por_tentativa_no_dia, True) if por_tentativa_no_dia is not None else (0.0, False)
            else:
                usd, conhecido = custos.get(aid, 0.0), True
            ultima = t["ultima"] is not None and linhas.inteiro(t, "number") == int(t["ultima"])
            intervencoes = (int(ultima and linhas.texto(t, "step_status") == "waiting_user")
                            + por_tentativa.get(aid, 0)
                            + (por_etapa.get(sid, 0) if ultima_ocorrencia.get(sid) == aid else 0))
            saida.append(Ocorrencia(
                chave=chave, quando=fim, fonte=FonteDaOcorrencia.TENTATIVA, run_id=linhas.texto(t, "run_id"),
                attempt_id=aid, step_id=sid, instance_id=linhas.texto_ou_nulo(t, "instance_id"),
                segundos=_segundos(linhas.texto_ou_nulo(t, "started_at"), fim), usd=usd, intervencoes=intervencoes,
                retroativa=retro, do_diario=do_diario, custo_conhecido=conhecido,
                erro=_erro(linhas.texto_ou_nulo(t, "error")), erros_de_ia=tuple(erros_de_ia.get(aid, ()))))
        return saida

    def _custos(self, tentativas: Sequence[Row], corte: str, ate: str) -> tuple[dict[str, float],
                                                                                dict[str, list[str]]]:
        """US$ e tipos de erro do provedor por tentativa, só das que começaram depois do `corte` (chamadas inteiras)."""
        if not tentativas:
            return {}, {}
        ids = {linhas.texto(t, "id") for t in tentativas}
        em_andamento = self._tentativas_das_etapas({linhas.texto(t, "step_id") for t in tentativas})
        precos = self._precos()
        usd: dict[str, float] = defaultdict(float)
        erros: dict[str, list[str]] = defaultdict(list)
        for c in self._db.query(
                "SELECT ts, attempt_id, step_id, model, input_tokens, cache_read, cache_write, output_tokens, usd, ok,"
                " error_kind FROM ai_calls WHERE ts >= ? AND ts < ?", (corte, ate)):
            dona = linhas.texto_ou_nulo(c, "attempt_id")
            if dona is None:                            # anterior à 045: a tentativa em andamento no instante dela
                etapa = linhas.texto_ou_nulo(c, "step_id")
                dona = _em_andamento(em_andamento.get(etapa, ()), linhas.texto(c, "ts")) if etapa else None
            if dona is None or dona not in ids:
                continue
            if c["usd"] is not None:
                usd[dona] += linhas.real(c, "usd")
            else:
                usd[dona] += costs.usd(precos, linhas.texto(c, "model"),
                                       [linhas.real(c, k) for k in ("input_tokens", "cache_read", "cache_write",
                                                                    "output_tokens")])
            tipo_do_erro = linhas.texto_ou_nulo(c, "error_kind")
            if tipo_do_erro and not linhas.inteiro(c, "ok"):
                erros[dona].append(tipo_do_erro)
        return dict(usd), dict(erros)

    def _tentativas_das_etapas(self, etapas: set[str]) -> dict[str, list[tuple[str, str]]]:
        """(id, início) de TODAS as tentativas das etapas, em ordem de início: a dona de uma chamada sem
        `attempt_id` é a última que começou antes dela."""
        saida: dict[str, list[tuple[str, str]]] = defaultdict(list)
        lista = sorted(etapas)
        for i in range(0, len(lista), _LOTE):
            lote = lista[i:i + _LOTE]
            for r in self._db.query(
                    "SELECT id, step_id, started_at FROM attempts WHERE step_id IN ({}) ORDER BY started_at, number"
                    .format(",".join("?" for _ in lote)), tuple(lote)):
                saida[linhas.texto(r, "step_id")].append((linhas.texto(r, "id"), linhas.texto(r, "started_at")))
        return dict(saida)

    def _diario(self, desde_dia: str, ate_dia: str) -> dict[tuple[str, str, str, str], float]:
        """Custo médio POR TENTATIVA de cada (dia, app, ação, tipo) na régua durável, nos dias antes do corte."""
        saida: dict[tuple[str, str, str, str], float] = {}
        for r in self._db.query(
                "SELECT day, app_package, capability, failure_kind, SUM(attempts) AS n, SUM(usd) AS usd"
                " FROM learning_daily WHERE day >= ? AND day <= ? AND failure_kind <> ''"
                " GROUP BY day, app_package, capability, failure_kind", (desde_dia, ate_dia)):
            n = linhas.real(r, "n")
            if n > 0:
                saida[(linhas.texto(r, "day"), linhas.texto(r, "app_package"), linhas.texto(r, "capability"),
                       linhas.texto(r, "failure_kind"))] = linhas.real(r, "usd") / n
        return saida

    def _intervencoes_ligadas(self, desde: str, ate: str, *, simulados: bool) -> tuple[Counter[str], Counter[str]]:
        """Tomada de controle e tela desconhecida que chamou gente, por tentativa (ou, sem ela, por etapa)."""
        por_tentativa: Counter[str] = Counter()
        por_etapa: Counter[str] = Counter()
        for s in self._db.query(
                "SELECT attempt_id, step_id FROM learning_signals WHERE kind IN (?,?) AND created_at >= ?"
                " AND created_at < ?" + ("" if simulados else " AND simulated = 0"),
                (*_INTERVENCOES_LIGADAS, desde, ate)):
            aid, sid = linhas.texto_ou_nulo(s, "attempt_id"), linhas.texto_ou_nulo(s, "step_id")
            if aid:
                por_tentativa[aid] += 1
            elif sid:
                por_etapa[sid] += 1
        return por_tentativa, por_etapa

    def _dos_sinais(self, desde: str, ate: str, *, simulados: bool) -> list[Ocorrencia]:
        """A pessoa desmentindo o verificador: 'deu errado' em etapa comprovada (falso positivo), 'deu certo' em
        etapa não comprovada (falso negativo) e a confirmação à mão (lacuna)."""
        saida: list[Ocorrencia] = []
        for s in self._db.query(
                "SELECT kind, verdict, step_verified, app_package, capability, run_id, attempt_id, step_id,"
                " instance_id, created_at FROM learning_signals WHERE kind IN (?,?) AND created_at >= ?"
                " AND created_at < ?" + ("" if simulados else " AND simulated = 0") + " ORDER BY created_at, id",
                (SignalKind.FEEDBACK.value, SignalKind.CONFIRMOU_A_MAO.value, desde, ate)):
            kind = linhas.texto(s, "kind")
            verificada = linhas.inteiro_ou_nulo(s, "step_verified")
            veredito = linhas.texto_ou_nulo(s, "verdict")
            if kind == SignalKind.CONFIRMOU_A_MAO.value:
                tipo = TipoDeVerificacao.CONFIRMOU_A_MAO
            elif veredito == "errado" and verificada == 1:
                tipo = TipoDeVerificacao.FALSO_POSITIVO
            elif veredito == "certo" and verificada == 0:
                tipo = TipoDeVerificacao.FALSO_NEGATIVO
            else:
                continue
            saida.append(Ocorrencia(
                chave=chave_do_grupo(linhas.texto(s, "app_package"), linhas.texto(s, "capability"), tipo.value, None),
                quando=linhas.texto(s, "created_at"), fonte=FonteDaOcorrencia.SINAL,
                run_id=linhas.texto_ou_nulo(s, "run_id"), attempt_id=linhas.texto_ou_nulo(s, "attempt_id"),
                step_id=linhas.texto_ou_nulo(s, "step_id"), instance_id=linhas.texto_ou_nulo(s, "instance_id"),
                intervencoes=int(kind in {k.value for k in SINAIS_DE_INTERVENCAO})))
        return saida

    def testemunha_da_purga(self) -> datetime | None:
        """O evento SEM execução mais antigo. A purga de logs apaga `events` e `ai_calls` com o mesmo corte, e só
        poupa evento de execução ainda aberta — por isso só os sem execução servem (e existem todo dia: aparelho,
        worker, controle)."""
        row = self._db.one("SELECT MIN(ts) AS primeiro FROM events WHERE run_id IS NULL")
        bruto = linhas.texto_ou_nulo(row, "primeiro") if row else None
        try:
            return parse_iso(bruto) if bruto else None
        except ValueError:
            return None

    # ================================================================== denominador da taxa
    def elegiveis(self, desde: str, ate: str, *, simulados: bool) -> dict[tuple[str, str], int]:
        """Tentativas que terminaram (menos as canceladas) por (app, ação): o denominador da taxa."""
        pacotes = self._pacotes()
        saida: dict[tuple[str, str], int] = defaultdict(int)
        for r in self._db.query(
                "SELECT s.capability, s.app_id, r.app_ids, COUNT(*) AS n FROM attempts a JOIN steps s"
                " ON s.id = a.step_id JOIN runs r ON r.id = s.run_id WHERE a.finished_at >= ? AND a.finished_at < ?"
                " AND a.status <> 'cancelled'" + ("" if simulados else " AND r.simulated = 0")
                + " GROUP BY s.capability, s.app_id, r.app_ids", (desde, ate)):
            chave = chave_do_grupo(self._app(pacotes, r), linhas.texto_ou_nulo(r, "capability"), "", None)
            saida[chave.app_e_acao] += linhas.inteiro(r, "n")
        return dict(saida)

    def amostra_elegivel(self, app: str, capability: str, desde: str, ate: str, *, limite: int) -> tuple[str, ...]:
        """Os primeiros ids de tentativas reais elegíveis da (app, ação): a prova real cita datas E ids."""
        pacotes = self._pacotes()
        filtro, params = ("s.capability IS NULL", ()) if capability == "*" else ("s.capability = ?", (capability,))
        saida: list[str] = []
        for r in self._db.query(
                "SELECT a.id, s.app_id, r.app_ids FROM attempts a JOIN steps s ON s.id = a.step_id JOIN runs r"
                f" ON r.id = s.run_id WHERE {filtro} AND a.finished_at >= ? AND a.finished_at < ?"
                " AND a.status <> 'cancelled' AND r.simulated = 0 ORDER BY a.finished_at, a.id",
                (*params, desde, ate)):
            if self._app(pacotes, r) == app:
                saida.append(linhas.texto(r, "id"))
                if len(saida) >= limite:
                    break
        return tuple(saida)

    def inicio_das_ultimas(self, app: str, capability: str, desde: str, ate: str, *, n: int) -> str | None:
        """O término da n-ésima tentativa elegível mais recente da (app, ação) em [desde, ate): o começo da janela da
        reincidência. Mesmo filtro do denominador (real, não cancelada); o app sai do pacote, por isso a contagem é
        aqui e não no SQL. Empate no término pode pôr uma ou outra a mais na janela (o `>=`), nunca a menos.

        Lê em lotes, das mais recentes para trás: a curadoria roda a cada 15 min para cada linha `fixed`, e o custo fica
        no tamanho da janela, não no tempo desde a prova."""
        if n < 1:
            return None
        pacotes = self._pacotes()
        filtro, params = ("s.capability IS NULL", ()) if capability == "*" else ("s.capability = ?", (capability,))
        lote = max(n, _LOTE_DA_JANELA)
        vistas = pulo = 0
        while True:
            lidas = self._db.query(
                "SELECT a.finished_at, s.app_id, r.app_ids FROM attempts a JOIN steps s ON s.id = a.step_id"
                f" JOIN runs r ON r.id = s.run_id WHERE {filtro} AND a.finished_at >= ? AND a.finished_at < ?"
                " AND a.status <> 'cancelled' AND r.simulated = 0 ORDER BY a.finished_at DESC, a.id DESC"
                " LIMIT ? OFFSET ?", (*params, desde, ate, lote, pulo))
            for r in lidas:
                if self._app(pacotes, r) == app:
                    vistas += 1
                    if vistas >= n:
                        return linhas.texto(r, "finished_at")
            if len(lidas) < lote:
                return None
            pulo += lote

    # ================================================================== seções
    def chamadas_de_tela(self, desde: str, ate: str, *, simulados: bool) -> list[ChamadaDeTela]:
        saida: list[ChamadaDeTela] = []
        for s in self._db.query(
                "SELECT app_package, data, created_at FROM learning_signals WHERE kind=? AND created_at >= ?"
                " AND created_at < ?" + ("" if simulados else " AND simulated = 0"),
                (SignalKind.TELA_DESCONHECIDA_CHAMOU_PESSOA.value, desde, ate)):
            dado = linhas.json_objeto(s, "data")
            tela = dado.get("tela") or dado.get("assinatura")
            saida.append(ChamadaDeTela(app=linhas.texto(s, "app_package") or "*",
                                       tela=tela if isinstance(tela, str) and tela else "desconhecida",
                                       quando=linhas.texto(s, "created_at"), chamou_pessoa=True))
        return saida

    def acoes_livres(self, desde: str, ate: str) -> list[AcaoLivre]:
        """Etapas livres comprovadas pela tela (`result.verified`), por (app, chave da etapa) — 30.59: a mesma etapa
        com dois objetivos (dois `template_hash`) é uma proposta só. A chave da etapa (e nunca o título ou o objetivo,
        que podem trazer nome de terceiro) é o que identifica a etapa para a pessoa. `sem_ia` conta as que fecharam
        por receita ou pelo atalho do executor (`driven_by` `recipe` ou `sem_ator`)."""
        pacotes = self._pacotes()
        execucoes: dict[tuple[str, str], set[str]] = defaultdict(set)
        modelos: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
        sem_ia: Counter[tuple[str, str]] = Counter()
        for s in self._db.query(
                "SELECT s.template_hash, s.template_key, s.key, s.app_id, s.run_id, s.result, s.driven_by, r.app_ids"
                " FROM steps s JOIN runs r ON r.id = s.run_id WHERE r.simulated = 0 AND s.capability IS NULL"
                " AND s.status = 'succeeded' AND s.template_hash IS NOT NULL AND s.finished_at >= ?"
                " AND s.finished_at < ?", (desde, ate)):
            if not _comprovada(linhas.texto_ou_nulo(s, "result")):
                continue
            chave = linhas.texto_ou_nulo(s, "template_key") or linhas.texto(s, "key")
            k = (self._app(pacotes, s), (redact(chave) or "")[:60])
            execucoes[k].add(linhas.texto(s, "run_id"))
            modelos[k][linhas.texto(s, "template_hash")] += 1
            sem_ia[k] += linhas.texto_ou_nulo(s, "driven_by") in _SEM_IA
        acoes = [AcaoLivre(app=app, chave=chave, execucoes=len(runs),
                           modelos=tuple(h for h, _n in modelos[(app, chave)].most_common()),
                           etapas=sum(modelos[(app, chave)].values()), sem_ia=sem_ia[(app, chave)])
                 for (app, chave), runs in execucoes.items()]
        return sorted(acoes, key=lambda a: (-a.execucoes, a.app, a.chave))

    def saude(self, desde: str, ate: str, *, simulados: bool) -> SaudeDasExecucoes:
        real = "" if simulados else " AND r.simulated = 0"
        execucoes = self._db.query("SELECT r.flow_id FROM runs r WHERE r.created_at >= ? AND r.created_at < ?"
                                   " AND r.mode = 'execute'" + real, (desde, ate))
        fluxos = [linhas.texto_ou_nulo(r, "flow_id") for r in execucoes]
        marcas = ",".join("?" for _ in _ETAPA_COM_DESFECHO)
        conducao: dict[str, int] = {}
        intervencoes = 0
        for r in self._db.query(
                "SELECT s.driven_by, s.status, COUNT(*) AS n FROM steps s JOIN runs r ON r.id = s.run_id"
                " WHERE COALESCE(s.finished_at, s.started_at) >= ? AND COALESCE(s.finished_at, s.started_at) < ?"
                f" AND s.status IN ({marcas})" + real + " GROUP BY s.driven_by, s.status",
                (desde, ate, *_ETAPA_COM_DESFECHO)):
            quem = linhas.texto_ou_nulo(r, "driven_by") or SEM_CONDUCAO
            conducao[quem] = conducao.get(quem, 0) + linhas.inteiro(r, "n")
            if linhas.texto(r, "status") == "waiting_user":
                intervencoes += linhas.inteiro(r, "n")
        marcas_sinal = ",".join("?" for _ in SINAIS_DE_INTERVENCAO)
        sinais = self._db.one(
            f"SELECT COUNT(*) AS n FROM learning_signals WHERE kind IN ({marcas_sinal}) AND created_at >= ?"
            " AND created_at < ?" + ("" if simulados else " AND simulated = 0"),
            (*sorted(k.value for k in SINAIS_DE_INTERVENCAO), desde, ate))
        intervencoes += linhas.inteiro(sinais, "n") if sinais else 0
        return SaudeDasExecucoes(execucoes=len(execucoes), execucoes_com_fluxo=sum(1 for f in fluxos if f),
                                 fluxos_distintos=len({f for f in fluxos if f}), etapas_por_conducao=conducao,
                                 intervencoes=intervencoes)


def _comprovada(resultado: str | None) -> bool:
    if not resultado:
        return False
    try:
        dado = json.loads(resultado)
    except ValueError:
        return False
    return isinstance(dado, dict) and dado.get("verified") is True


# ------------------------------------------------------------------ o backlog
class SqlBacklogRepository:
    """`learning_backlog`. O registro (curadoria e PATCH de um `fk-*` ainda não gravado) é upsert pela `cluster_key`
    e só estende as datas; o estado muda só por `salvar`, com CAS."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def linha(self, backlog_id: str) -> LinhaDoBacklog | None:
        row = self._db.one("SELECT * FROM learning_backlog WHERE id=?", (backlog_id,))
        return _linha(row) if row else None

    def linhas(self, estados: Sequence[EstadoDoBacklog] | None = None) -> list[LinhaDoBacklog]:
        if estados is not None and not estados:
            return []
        sql, params = "SELECT * FROM learning_backlog", tuple[str, ...]()
        if estados:
            sql += " WHERE state IN ({})".format(",".join("?" for _ in estados))
            params = tuple(e.value for e in estados)
        return [_linha(r) for r in self._db.query(sql + " ORDER BY id", params)]

    def registrar(self, nova: LinhaDoBacklog, *, agora: str) -> bool:
        row = self._db.one(
            "INSERT INTO learning_backlog(id, category, cluster_key, app_package, capability, failure_kind,"
            " failure_screen, title, state, first_seen, last_seen, updated_by, updated_at, parent_id)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT (cluster_key) DO UPDATE SET"
            " first_seen = CASE WHEN excluded.first_seen < learning_backlog.first_seen THEN excluded.first_seen"
            " ELSE learning_backlog.first_seen END,"
            " last_seen = CASE WHEN excluded.last_seen > learning_backlog.last_seen THEN excluded.last_seen"
            " ELSE learning_backlog.last_seen END, title = excluded.title"
            " WHERE excluded.first_seen < learning_backlog.first_seen OR excluded.last_seen > learning_backlog.last_seen"
            " OR excluded.title <> learning_backlog.title RETURNING id",
            (nova.id, nova.category.value, nova.cluster_key, nova.app_package, nova.capability, nova.failure_kind,
             nova.failure_screen, nova.title, EstadoDoBacklog.OPEN.value, nova.first_seen, nova.last_seen,
             "sistema", agora, nova.parent_id))
        return row is not None

    def salvar(self, linha: LinhaDoBacklog, *, de_estado: EstadoDoBacklog) -> LinhaDoBacklog:
        with self._db.tx():
            cur = self._db.execute(
                "UPDATE learning_backlog SET state=?, plan_item=?, fixed_in_commit=?, fixed_at=?, baseline=?,"
                " verification=?, reopened_count=?, notes=?, updated_by=?, updated_at=? WHERE id=? AND state=?",
                (linha.state.value, linha.plan_item, linha.fixed_in_commit, linha.fixed_at,
                 canonical_json(linha.baseline) if linha.baseline is not None else None,
                 canonical_json(linha.verification) if linha.verification is not None else None,
                 linha.reopened_count, linha.notes, linha.updated_by, linha.updated_at, linha.id, de_estado.value))
            if int(cur.rowcount or 0) != 1:
                raise ConflitoDeEstado(f"{linha.id} mudou de estado (ou sumiu) durante a alteração; releia.")
        gravada = self.linha(linha.id)
        assert gravada is not None
        return gravada


def _json_ou_nulo(row: Row, coluna: str) -> JsonObject | None:
    bruto = linhas.texto_ou_nulo(row, coluna)
    if not bruto:
        return None
    try:
        return parse_json_object(bruto)
    except NotJson:
        return None


def _linha(row: Row) -> LinhaDoBacklog:
    return LinhaDoBacklog(
        id=linhas.texto(row, "id"), category=CategoriaDoBacklog(linhas.texto(row, "category")),
        cluster_key=linhas.texto(row, "cluster_key"), title=linhas.texto(row, "title"),
        state=EstadoDoBacklog(linhas.texto(row, "state")), first_seen=linhas.texto(row, "first_seen"),
        last_seen=linhas.texto(row, "last_seen"), app_package=linhas.texto_ou_nulo(row, "app_package"),
        capability=linhas.texto_ou_nulo(row, "capability"), failure_kind=linhas.texto_ou_nulo(row, "failure_kind"),
        failure_screen=linhas.texto_ou_nulo(row, "failure_screen"), plan_item=linhas.texto_ou_nulo(row, "plan_item"),
        fixed_in_commit=linhas.texto_ou_nulo(row, "fixed_in_commit"), fixed_at=linhas.texto_ou_nulo(row, "fixed_at"),
        baseline=_json_ou_nulo(row, "baseline"), verification=_json_ou_nulo(row, "verification"),
        reopened_count=linhas.inteiro(row, "reopened_count"), parent_id=linhas.texto_ou_nulo(row, "parent_id"),
        notes=linhas.texto_ou_nulo(row, "notes"), updated_by=linhas.texto_ou_nulo(row, "updated_by"),
        updated_at=linhas.texto_ou_nulo(row, "updated_at"))


__all__ = ["FontesDeFalhaSql", "SqlBacklogRepository"]
