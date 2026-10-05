"""`SqlLicoesRepository`: o que as lições (A7 do ADR-054) leem da execução e gravam fora do livro, nos dois dialetos.

- CONTRASTE: as tentativas da execução em ordem, por etapa (e pela mesma etapa numa versão seguinte do plano, mesmo
  `template_hash`), com os toques de cada uma (`actions.target`, que já nasce sem campo de senha). Sai só o que o
  domínio aceita como lacuna: sufixo de resource-id, o rótulo (text ou content-desc) e em quantas execuções REAIS o
  mesmo rótulo foi tocado na mesma ação. O texto de erro nunca sai daqui; o tipo sai de `failure_kind` (ou do
  classificador puro, no legado). Execução simulada não é lida. A que digitou segredo (`type_secret`) ou tocou um
  elemento com frase de desafio sai marcada `sensivel` — o domínio a recusa e a recusa é contada. A chave da etapa
  sai só para a recusa da etapa de sessão, login ou desafio (a etapa livre não tem ação do catálogo);
- DEFEITO DO PLANO: a etapa comprovada desta execução contra as etapas da MESMA ação (ou chave, na etapa livre) que
  falharam por defeito do plano nos últimos 30 dias, com outra pós-condição;
- EXPOSIÇÃO: gravada no ato (`INSERT … ON CONFLICT DO NOTHING` na chave `(item, unidade, papel)`: as repetições da
  mesma etapa não trocam de braço) e preenchida no digest — SÓ em execução real — com o status final, as chamadas de IA
  pela tentativa (045; a chamada antiga só com `step_id` cai na etapa), o US$ (048, ou pelos tokens e a tabela de
  preços), os segundos e se o plano foi revisto. A etapa `succeeded` sem comprovação (confirmada à mão) e a execução
  `completed` com alguma etapa assim ficam `NAO_COMPROVADA`: na amostra, e nunca como sucesso;
- a versão do app no parque (`device_app_state`), a absorção pelo backlog e a proposta `promover_licao` (a mesma
  chave do A3: `promover_licao|<id>`, idempotente).
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.automation.hierarchy import detectar_trava_generica, normalizar_texto_de_tela
from app.db import Database, Row
from app.modules.learning.domain.efeito import NAO_COMPROVADA, NovaExposicao
from app.modules.learning.domain.falhas import FailureKind, classificar_falha
from app.modules.learning.domain.licoes import (DEFEITOS_DO_PLANO, AlvoObservado, Contraste, ContrasteDoPlano,
                                                DefeitoDoPlano, NotaDeFeedback)
from app.modules.learning.domain.livro import ItemDeAprendizado
from app.modules.learning.domain.vocabulario import Braco, DetalheDeEstado, Papel, SignalKind, TipoDeProposta
from app.modules.learning.infrastructure import linhas
from app.modules.learning.infrastructure.sql_repository import item_da_linha
from app.planning import costs
from app.taskqueue.projecao import QUALQUER, app_da_etapa
from app.util import to_iso

#: Status de etapa que fecham o desfecho da unidade do ator. 30.69: `waiting_user` NÃO fecha. Esperar a pessoa não é
#: desfecho do ator, e o desfecho gravado congela (`filled_at IS NULL`): a etapa retomada que concluísse nunca o
#: corrigiria. A exposição fica pendente até a etapa sair da espera; quem sai sem retomar vai a `cancelled`.
_ETAPA_FINAL = frozenset({"succeeded", "failed", "uncertain", "cancelled", "skipped"})
#: Status de execução que fecham o desfecho da unidade do planejador.
_EXECUCAO_FINAL = frozenset({"completed", "completed_with_issues", "failed", "cancelled"})
_TOQUES = ("tap", "long_press")
#: Janela em que um defeito do plano ainda conta como "seguido de" um plano que comprovou.
DEFEITO_DIAS = 30
#: Teto de toques lidos para contar em quantas execuções um rótulo apareceu (os mais recentes).
_TOQUES_LIDOS = 5000


class SqlLicoesRepository:
    def __init__(self, db: Database, *, precos: Callable[[], dict[str, list[float]]] | None = None) -> None:
        self._db = db
        self._precos = precos if precos is not None else dict

    # ================================================================== leitura comum
    def _pacote(self, app_id: object, app_ids: object) -> str:
        app = app_da_etapa(app_id, app_ids)
        if not app or app == QUALQUER:
            return ""
        linha = self._db.one("SELECT package FROM apps WHERE id=?", (app,))
        return (linhas.texto_ou_nulo(linha, "package") if linha is not None else None) or app

    def _versao(self, instance_id: str | None, pacote: str) -> str | None:
        if not instance_id or not pacote:
            return None
        linha = self._db.one("SELECT observed_version_name FROM device_app_state WHERE instance_id=? AND"
                             " package_name=?", (instance_id, pacote))
        return linhas.texto_ou_nulo(linha, "observed_version_name") if linha is not None else None

    def _parametros(self, run_id: str) -> dict[str, dict[str, str]]:
        """Os parâmetros de cada objetivo da execução (o que nunca pode entrar como texto numa lição)."""
        return {linhas.texto(r, "id"): _texto_por_chave(linhas.texto_ou_nulo(r, "parameters"))
                for r in self._db.query("SELECT id, parameters FROM objectives WHERE run_id=?", (run_id,))}

    # ================================================================== contraste do ator
    def contrastes(self, run_id: str) -> list[Contraste]:
        execucao = self._db.one("SELECT simulated, app_ids FROM runs WHERE id=?", (run_id,))
        if execucao is None or linhas.inteiro(execucao, "simulated"):
            return []                                   # execução simulada nunca é fonte de lição
        secreta = self._db.one(
            "SELECT 1 AS x FROM actions a JOIN attempts t ON t.id = a.attempt_id JOIN steps s ON s.id = t.step_id"
            " WHERE s.run_id=? AND a.tool='type_secret' LIMIT 1", (run_id,)) is not None
        parametros = self._parametros(run_id)
        grupos: dict[tuple[str, str], dict[FailureKind, Row]] = {}
        saida: list[Contraste] = []
        repeticao: dict[tuple[str, str], dict[str, int]] = {}
        for t in self._db.query(
                "SELECT s.id AS step_id, s.objective_id, s.instance_id, s.key, s.capability, s.template_hash,"
                " s.side_effect, s.status AS step_status, s.result, s.app_id, s.variables, t.id AS attempt_id,"
                " t.number, t.status AS attempt_status, t.error, t.failure_kind, t.failure_screen, t.started_at"
                " FROM steps s JOIN attempts t ON t.step_id = s.id WHERE s.run_id=?"
                " ORDER BY s.objective_id, t.started_at, t.number", (run_id,)):
            # A mesma etapa: o mesmo objetivo, o mesmo modelo e a mesma chave — junta a tentativa seguinte da etapa e a
            # da mesma etapa na versão revista do plano (recuperação), e separa as cópias de `for_each` (`_i<n>`).
            chave = (linhas.texto(t, "objective_id"),
                     f"{linhas.texto_ou_nulo(t, 'template_hash') or ''}|{linhas.texto(t, 'key')}")
            falhas = grupos.setdefault(chave, {})
            status = linhas.texto(t, "attempt_status")
            if status in ("failed", "uncertain"):
                tipo = _tipo(linhas.texto_ou_nulo(t, "failure_kind"), linhas.texto_ou_nulo(t, "error"), status)
                if tipo is not None:
                    falhas[tipo] = t                    # a ÚLTIMA falha de cada tipo antes do sucesso
                continue
            if status != "succeeded" or not _comprovada(t, "step_status") or not falhas:
                continue
            pacote = self._pacote(t["app_id"], execucao["app_ids"])
            params = {**parametros.get(linhas.texto(t, "objective_id"), {}),
                      **_texto_por_chave(linhas.texto_ou_nulo(t, "variables"))}
            for tipo, ruim in falhas.items():
                saida.append(self._contraste(run_id, t, ruim, tipo, pacote, params, secreta, repeticao))
            falhas.clear()
        return saida

    def _contraste(self, run_id: str, boa: Row, ruim: Row, tipo: FailureKind, pacote: str, params: dict[str, str],
                   secreta: bool, repeticao: dict[tuple[str, str], dict[str, int]]) -> Contraste:
        acoes = self._db.query("SELECT attempt_id, seq, tool, status, side_effect, target FROM actions"
                               " WHERE attempt_id IN (?, ?) ORDER BY attempt_id, seq",
                               (linhas.texto(ruim, "attempt_id"), linhas.texto(boa, "attempt_id")))
        da_ruim = [a for a in acoes if linhas.texto(a, "attempt_id") == linhas.texto(ruim, "attempt_id")]
        da_boa = [a for a in acoes if linhas.texto(a, "attempt_id") == linhas.texto(boa, "attempt_id")]
        capability = linhas.texto_ou_nulo(boa, "capability") or "*"
        passo = linhas.texto_ou_nulo(boa, "template_hash") or ""
        chave_da_acao = ("capability", capability) if capability != "*" else ("template_hash", passo)
        sensivel = secreta
        alvos: list[AlvoObservado | None] = []
        for alvo in (_ultimo_toque(da_ruim), _primeiro_toque(da_boa), _toque_de_efeito(da_boa)):
            if alvo is not None and alvo.rotulo and _desafio(alvo.rotulo):
                sensivel = True                         # tocou tela de desafio: nada daqui vira conhecimento
            if alvo is not None and alvo.rotulo and not alvo.resource_id and chave_da_acao[1]:
                if chave_da_acao not in repeticao:
                    repeticao[chave_da_acao] = self._rotulos_tocados(*chave_da_acao)
                alvo = AlvoObservado(alvo.resource_id, alvo.rotulo, repeticao[chave_da_acao].get(alvo.rotulo, 0))
            alvos.append(alvo)
        return Contraste(
            run_id=run_id, instance_id=linhas.texto(boa, "instance_id"), step_id=linhas.texto(boa, "step_id"),
            app=pacote, capability=capability, step_hash=passo, side_effect=bool(linhas.inteiro(boa, "side_effect")),
            tipo=tipo, tentativa_ruim=linhas.texto(ruim, "attempt_id"), tentativa_boa=linhas.texto(boa, "attempt_id"),
            step_key=linhas.texto(boa, "key"), alvo_ruim=alvos[0], primeiro_alvo_bom=alvos[1], alvo_do_efeito=alvos[2],
            recusas=sum(1 for a in da_ruim if linhas.texto(a, "status") == "rejected"), parametros=params,
            tela_ruim=linhas.texto_ou_nulo(ruim, "failure_screen"), sensivel=sensivel, simulated=False,
            app_version=self._versao(linhas.texto(boa, "instance_id"), pacote))

    def _rotulos_tocados(self, coluna: str, valor: str) -> dict[str, int]:
        """rótulo → em quantas execuções REAIS distintas um toque feito na mesma ação (ou etapa livre) o teve."""
        execucoes: dict[str, set[str]] = {}
        coluna_sql = "s.capability" if coluna == "capability" else "s.template_hash"
        for r in self._db.query(
                "SELECT s.run_id, a.target FROM actions a JOIN attempts t ON t.id = a.attempt_id"
                " JOIN steps s ON s.id = t.step_id JOIN runs r ON r.id = s.run_id"
                " WHERE r.simulated = 0 AND a.status = 'done' AND a.tool IN (?, ?) AND a.target IS NOT NULL"
                f" AND {coluna_sql} = ? ORDER BY a.id DESC LIMIT ?", (*_TOQUES, valor, _TOQUES_LIDOS)):
            alvo = _alvo(linhas.texto_ou_nulo(r, "target"))
            if alvo is not None and alvo.rotulo:
                execucoes.setdefault(alvo.rotulo, set()).add(linhas.texto(r, "run_id"))
        return {rotulo: len(runs) for rotulo, runs in execucoes.items()}

    # ================================================================== contraste do planejador
    def contrastes_do_plano(self, run_id: str) -> list[ContrasteDoPlano]:
        execucao = self._db.one("SELECT simulated, app_ids FROM runs WHERE id=?", (run_id,))
        if execucao is None or linhas.inteiro(execucao, "simulated"):
            return []
        parametros = self._parametros(run_id)
        saida: dict[tuple[str, str, str], ContrasteDoPlano] = {}
        for s in self._db.query(
                "SELECT id, objective_id, instance_id, key, capability, postcondition, side_effect, status, result,"
                " app_id, variables, finished_at FROM steps WHERE run_id=? AND status='succeeded'", (run_id,)):
            if not _comprovada(s):
                continue
            capability = linhas.texto_ou_nulo(s, "capability")
            acao = capability or linhas.texto(s, "key")
            pacote = self._pacote(s["app_id"], execucao["app_ids"])
            tipo_bom = _tipo_da_pos(linhas.texto_ou_nulo(s, "postcondition"))
            fim = linhas.texto_ou_nulo(s, "finished_at")
            if not pacote or not fim:
                continue
            params = {**parametros.get(linhas.texto(s, "objective_id"), {}),
                      **_texto_por_chave(linhas.texto_ou_nulo(s, "variables"))}
            for d, falha in self._defeitos(acao, capability is None, pacote, fim):
                tipo = _tipo_da_pos(linhas.texto_ou_nulo(d, "postcondition"))
                # O defeito genérico só ensina contra um plano que comprovou com OUTRO tipo. O do seletor (31.32)
                # ensina também contra o mesmo tipo: o conserto é o seletor de um elemento só, não trocar de tipo.
                do_seletor = falha is FailureKind.SELETOR_EM_ELEMENTOS_DIFERENTES
                if not tipo or (tipo == tipo_bom and not do_seletor):
                    continue
                chave = (pacote, acao, tipo)
                atual = saida.get(chave)
                defeito = DefeitoDoPlano(linhas.texto(d, "id"), linhas.texto(d, "run_id"),
                                         linhas.texto_ou_nulo(d, "instance_id"), falha)
                if atual is None:
                    saida[chave] = ContrasteDoPlano(
                        app=pacote, acao=acao, livre=capability is None, tipo=tipo, defeitos=(defeito,),
                        execucao_que_comprovou=run_id,
                        side_effect=bool(linhas.inteiro(s, "side_effect")) or bool(linhas.inteiro(d, "side_effect")),
                        parametros=params, app_version=self._versao(linhas.texto(s, "instance_id"), pacote))
                elif all(x.step_id != defeito.step_id for x in atual.defeitos):
                    saida[chave] = ContrasteDoPlano(
                        app=atual.app, acao=atual.acao, livre=atual.livre, tipo=atual.tipo,
                        defeitos=(*atual.defeitos, defeito), execucao_que_comprovou=run_id,
                        side_effect=atual.side_effect or bool(linhas.inteiro(d, "side_effect")),
                        parametros=atual.parametros, app_version=atual.app_version)
        return list(saida.values())

    def _defeitos(self, acao: str, livre: bool, pacote: str, antes_de: str) -> list[tuple[Row, FailureKind]]:
        """As etapas REAIS da mesma ação que falharam por defeito do plano (o genérico ou o do seletor, 31.32) antes desta
        comprovar (30 dias), cada uma com o tipo da falha."""
        inicio = _dias_antes(antes_de, DEFEITO_DIAS)
        filtro = "s.capability IS NULL AND s.key=?" if livre else "s.capability=?"
        candidatas = self._db.query(
            "SELECT s.id, s.run_id, s.instance_id, s.postcondition, s.side_effect, s.failure_kind, s.status_detail,"
            " s.app_id, r.app_ids FROM steps s JOIN runs r ON r.id = s.run_id"
            f" WHERE r.simulated = 0 AND s.status='failed' AND {filtro} AND s.finished_at < ? AND s.finished_at >= ?",
            (acao, antes_de, inicio))
        saida: list[tuple[Row, FailureKind]] = []
        for d in candidatas:
            falha = _tipo(linhas.texto_ou_nulo(d, "failure_kind"), linhas.texto_ou_nulo(d, "status_detail"), "failed")
            if falha in DEFEITOS_DO_PLANO and self._pacote(d["app_id"], d["app_ids"]) == pacote:
                saida.append((d, falha))
        return saida

    # ================================================================== notas do D2
    def notas(self, desde: str) -> list[NotaDeFeedback]:
        saida: list[NotaDeFeedback] = []
        for s in self._db.query(
                "SELECT g.id, g.run_id, g.step_id, g.instance_id, g.app_package, g.capability, g.step_hash, g.note,"
                " g.simulated, COALESCE(st.side_effect, 0) AS side_effect, COALESCE(st.key, '') AS step_key"
                " FROM learning_signals g"
                " LEFT JOIN steps st ON st.id = g.step_id"
                " WHERE g.kind=? AND g.verdict='errado' AND g.note IS NOT NULL AND g.note_refused=0"
                " AND g.created_by<>'sistema' AND g.created_at >= ? ORDER BY g.id",
                (SignalKind.FEEDBACK.value, desde)):
            saida.append(NotaDeFeedback(
                signal_id=linhas.inteiro(s, "id"), app=linhas.texto(s, "app_package"),
                capability=linhas.texto(s, "capability"), step_hash=linhas.texto_ou_nulo(s, "step_hash") or "",
                nota=linhas.texto(s, "note"), side_effect=bool(linhas.inteiro(s, "side_effect")),
                run_id=linhas.texto_ou_nulo(s, "run_id"), instance_id=linhas.texto_ou_nulo(s, "instance_id"),
                simulated=bool(linhas.inteiro(s, "simulated")), step_key=linhas.texto(s, "step_key")))
        return saida

    # ================================================================== consumo
    def publicadas(self, papel: Papel, app: str) -> list[ItemDeAprendizado]:
        """As lições publicadas e EXPOSTAS (em prova ou com ajuda medida) deste app e papel — uma leitura indexada."""
        return [item_da_linha(r) for r in self._db.query(
            "SELECT * FROM learning_items WHERE kind='licao' AND state='published' AND scope_app IN (?, '')"
            " AND scope_role=? AND state_detail IN (?, ?) ORDER BY id",
            (app, papel.value, DetalheDeEstado.EM_PROVA.value, DetalheDeEstado.MEDIDA_AJUDA.value))]

    def expor(self, exposicoes: Sequence[NovaExposicao], agora: str) -> int:
        gravadas = 0
        with self._db.tx():
            for e in exposicoes:
                nova = self._db.one(
                    "INSERT INTO learning_exposures(item_id, unit_id, role, arm, tokens, run_id, objective_id,"
                    " app_package, capability, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)"
                    " ON CONFLICT (item_id, unit_id, role) DO NOTHING RETURNING item_id",
                    (e.item_id, e.unit_id, e.role, e.arm.value, e.tokens if e.arm is Braco.WITH else 0, e.run_id,
                     e.objective_id, e.app_package, e.capability, agora))
                if nova is None:
                    continue                            # a mesma etapa, outra tentativa: o braço é o mesmo
                gravadas += 1
                if e.arm is Braco.WITH and not e.simulated:
                    self._db.execute("UPDATE learning_items SET last_used_at=? WHERE id=?", (agora, e.item_id))
        return gravadas

    # ================================================================== desfecho (digest)
    def preencher(self, run_id: str, agora: str) -> int:
        execucao = self._db.one("SELECT simulated, status, created_at, started_at, finished_at FROM runs WHERE id=?",
                                (run_id,))
        if execucao is None or linhas.inteiro(execucao, "simulated"):
            return 0                                    # unidade simulada nunca entra em veredito
        pendentes = self._db.query("SELECT item_id, unit_id, role FROM learning_exposures WHERE run_id=?"
                                   " AND filled_at IS NULL", (run_id,))
        precos = self._precos()
        desfechos: dict[str, _Desfecho | None] = {}
        feitas = 0
        for p in pendentes:
            unidade = linhas.texto(p, "unit_id")
            if unidade not in desfechos:
                if unidade.startswith("step:"):
                    desfechos[unidade] = self._desfecho_da_etapa(unidade.removeprefix("step:"), precos)
                elif unidade.startswith("plan:"):
                    desfechos[unidade] = self._desfecho_do_plano(run_id, execucao, precos)
                else:
                    desfechos[unidade] = None
            d = desfechos[unidade]
            if d is None:
                continue                                # ainda sem desfecho final: a curadoria tenta de novo
            cur = self._db.execute(
                "UPDATE learning_exposures SET outcome=?, failure_kind=?, ai_calls=?, usd=?, seconds=?, replanned=?,"
                " filled_at=? WHERE item_id=? AND unit_id=? AND role=? AND filled_at IS NULL",
                (d.outcome, d.failure_kind, d.ai_calls, round(d.usd, 6), round(d.seconds, 3), int(d.replanned), agora,
                 linhas.texto(p, "item_id"), unidade, linhas.texto(p, "role")))
            feitas += int(cur.rowcount or 0)
        return feitas

    def _desfecho_da_etapa(self, step_id: str, precos: dict[str, list[float]]) -> _Desfecho | None:
        etapa = self._db.one("SELECT s.status, s.status_detail, s.failure_kind, s.plan_version, s.result,"
                             " o.plan_version AS versao_do_objetivo FROM steps s LEFT JOIN objectives o"
                             " ON o.id = s.objective_id WHERE s.id=?", (step_id,))
        if etapa is None or linhas.texto(etapa, "status") not in _ETAPA_FINAL:
            return None
        status = linhas.texto(etapa, "status")
        if status == "succeeded" and not _comprovada(etapa):
            status = NAO_COMPROVADA                     # confirmada à mão: na amostra, mas nunca como sucesso
        tentativas = self._db.query("SELECT id, started_at, finished_at FROM attempts WHERE step_id=?", (step_id,))
        ids = [linhas.texto(t, "id") for t in tentativas]
        marcas = ",".join("?" for _ in ids)
        filtro = f"attempt_id IN ({marcas}) OR (attempt_id IS NULL AND step_id=?)" if ids else "step_id=?"
        n, usd = self._chamadas(f"SELECT model, input_tokens, cache_read, cache_write, output_tokens, usd FROM ai_calls"
                                f" WHERE {filtro}", (*ids, step_id), precos)
        tipo = None
        if status not in ("succeeded", NAO_COMPROVADA):
            tipo = linhas.texto_ou_nulo(etapa, "failure_kind") or _valor(
                classificar_falha(linhas.texto_ou_nulo(etapa, "status_detail"), status))
        versao = linhas.inteiro_ou_nulo(etapa, "versao_do_objetivo")
        return _Desfecho(outcome=status, failure_kind=tipo, ai_calls=n, usd=usd,
                         seconds=sum(_duracao(linhas.texto_ou_nulo(t, "started_at"),
                                              linhas.texto_ou_nulo(t, "finished_at")) for t in tentativas),
                         replanned=versao is not None and versao > linhas.inteiro(etapa, "plan_version"))

    def _desfecho_do_plano(self, run_id: str, execucao: Row, precos: dict[str, list[float]]) -> _Desfecho | None:
        status = linhas.texto(execucao, "status")
        if status not in _EXECUCAO_FINAL:
            return None
        # O escalonador conclui o objetivo mesmo com etapa confirmada à mão (em qualquer versão do plano, como ele
        # conta): a execução só é sucesso do planejador se TODA etapa `succeeded` foi comprovada.
        if status == "completed" and any(not _comprovada(s) for s in self._db.query(
                "SELECT status, result FROM steps WHERE run_id=? AND status='succeeded'", (run_id,))):
            status = NAO_COMPROVADA
        # Só o custo DA EXECUÇÃO: a lição muda o planejador e o ator, não o decisor. A decisão fechada do Jev (31.14)
        # grava o `run_id` com `origem='decisao_fechada'` e fica fora; quem grava com `run_id` sem dizer a origem já
        # leva 'execucao' (`taskqueue/repository.py`), e a linha anterior à 073 não tem origem e é da execução.
        n, usd = self._chamadas("SELECT model, input_tokens, cache_read, cache_write, output_tokens, usd FROM ai_calls"
                                " WHERE run_id=? AND (origem IS NULL OR origem='execucao')", (run_id,), precos)
        falha = self._db.one("SELECT failure_kind, status_detail, status FROM steps WHERE run_id=? AND status IN"
                             " ('failed', 'uncertain') ORDER BY finished_at LIMIT 1", (run_id,))
        tipo = None
        if falha is not None and status not in ("completed", NAO_COMPROVADA):
            tipo = linhas.texto_ou_nulo(falha, "failure_kind") or _valor(classificar_falha(
                linhas.texto_ou_nulo(falha, "status_detail"), linhas.texto(falha, "status")))
        revisto = self._db.one("SELECT 1 AS x FROM objectives WHERE run_id=? AND plan_version > 1 LIMIT 1", (run_id,))
        inicio = linhas.texto_ou_nulo(execucao, "started_at") or linhas.texto_ou_nulo(execucao, "created_at")
        return _Desfecho(outcome=status, failure_kind=tipo, ai_calls=n, usd=usd,
                         seconds=_duracao(inicio, linhas.texto_ou_nulo(execucao, "finished_at")),
                         replanned=revisto is not None)

    def _chamadas(self, sql: str, params: tuple[str, ...], precos: dict[str, list[float]]) -> tuple[int, float]:
        n, total = 0, 0.0
        for c in self._db.query(sql, params):
            n += 1
            if c["usd"] is not None:
                total += linhas.real(c, "usd")
            else:
                total += costs.usd(precos, linhas.texto(c, "model"),
                                   [linhas.real(c, k) for k in ("input_tokens", "cache_read", "cache_write",
                                                                "output_tokens")])
        return n, total

    def execucoes_a_preencher(self, desde: str) -> list[str]:
        # 30.71: a execução que espera a pessoa (`awaiting_person`, 29.93) tem `finished_at` (o fim do trabalho
        # automático), mas a exposição da etapa que espera não fecha enquanto ela espera (30.69). Sem o filtro, ela
        # entrava em toda passada sem preencher nada e, com `LIMIT 200` por `run_id`, podia tirar a vez de quem fecha.
        # A janela conta da SAÍDA (`assentada_em`, #382), não do `finished_at`: o vencimento e o cancelamento da espera
        # não o limpam, e uma espera de mais de `PREENCHER_DIAS` com o digest da saída perdido deixaria a exposição fora
        # da janela para sempre (N1 da leitura do 30.71).
        return [linhas.texto(r, "run_id") for r in self._db.query(
            "SELECT DISTINCT e.run_id FROM learning_exposures e JOIN runs r ON r.id = e.run_id"
            " WHERE e.filled_at IS NULL AND r.simulated = 0 AND r.finished_at IS NOT NULL"
            " AND COALESCE(r.assentada_em, r.finished_at) >= ?"
            " AND r.status <> 'awaiting_person' ORDER BY e.run_id LIMIT 200", (desde,))]

    # ================================================================== curadoria
    def ultima_exposicao(self, item_id: str) -> str | None:
        """A exposição mais recente COM desfecho (real e assentada): a simulada não mantém lição viva."""
        linha = self._db.one("SELECT MAX(created_at) AS ultima FROM learning_exposures WHERE item_id=?"
                             " AND filled_at IS NOT NULL", (item_id,))
        return linhas.texto_ou_nulo(linha, "ultima") if linha is not None else None

    def versao_atual(self, app: str) -> str | None:
        """A versão MAIS NOVA do app no parque (maior `version_code`; no empate, a verificada por último). Não é a do
        aparelho verificado por último: com o parque em versões misturadas, essa alternaria a cada verificação e
        devolveria a lição à prova sem parar."""
        linha = self._db.one(
            "SELECT observed_version_name FROM device_app_state WHERE package_name=? AND observed_version_name IS NOT"
            " NULL ORDER BY COALESCE(observed_version_code, 0) DESC, COALESCE(verified_at, last_update_time, '') DESC"
            " LIMIT 1", (app,))
        return linhas.texto_ou_nulo(linha, "observed_version_name") if linha is not None else None

    def absorvida(self, item_id: str) -> str | None:
        """O commit que absorveu a lição no repositório (a proposta `promover_licao` corrigida), ou `None`."""
        linha = self._db.one("SELECT fixed_in_commit FROM learning_backlog WHERE cluster_key=? AND state='fixed'"
                             " AND fixed_in_commit IS NOT NULL", (_chave_da_proposta(item_id),))
        return linhas.texto_ou_nulo(linha, "fixed_in_commit") if linha is not None else None

    def propor_promocao(self, item: ItemDeAprendizado, agora: str) -> bool:
        chave = _chave_da_proposta(item.id)
        app = item.escopo.app or "*"
        nova = self._db.one(
            "INSERT INTO learning_backlog(id, category, cluster_key, app_package, capability, title, state, first_seen,"
            " last_seen, notes, updated_by, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT (cluster_key) DO NOTHING RETURNING id",
            (_id_do_backlog(chave), "proposta", chave, item.escopo.app or None, item.escopo.capability or None,
             f"{app} · lição {item.id}: promover", "open", agora, agora,
             f"efeito 'ajuda' medido; promover para nav_hints, catalogo.yaml ou telas.yaml: {item.summary}"[:500],
             "sistema", agora))
        return nova is not None


# ------------------------------------------------------------------ apoio
@dataclass(frozen=True, slots=True)
class _Desfecho:
    outcome: str
    failure_kind: str | None
    ai_calls: int
    usd: float
    seconds: float
    replanned: bool


def _chave_da_proposta(item_id: str) -> str:
    return f"{TipoDeProposta.PROMOVER_LICAO.value}|{item_id}"


def _id_do_backlog(cluster_key: str) -> str:
    """A mesma regra do backlog (A3): 'fk-' + sha1(cluster_key)[:10]."""
    return "fk-" + hashlib.sha1(cluster_key.encode("utf-8")).hexdigest()[:10]


def _valor(tipo: FailureKind | None) -> str | None:
    return tipo.value if tipo is not None else None


def _tipo(gravado: str | None, texto: str | None, status: str) -> FailureKind | None:
    if gravado:
        try:
            return FailureKind(gravado)
        except ValueError:
            return FailureKind.OUTRO
    return classificar_falha(texto, status)


def _comprovada(row: Row, coluna_do_status: str = "status") -> bool:
    """A etapa comprovada pela verificação. A confirmação à mão (`confirm_done`) grava `verified=false` e nunca é
    evidência a favor de aprendizado."""
    if linhas.texto(row, coluna_do_status) != "succeeded":
        return False
    bruto = linhas.texto_ou_nulo(row, "result")
    try:
        resultado = json.loads(bruto) if bruto else None
    except ValueError:
        return False
    return isinstance(resultado, dict) and resultado.get("verified") is True


def _texto_por_chave(bruto: str | None) -> dict[str, str]:
    try:
        valor = json.loads(bruto) if bruto else None
    except ValueError:
        return {}
    if not isinstance(valor, dict):
        return {}
    return {str(k): v for k, v in valor.items() if isinstance(v, str)}


def _tipo_da_pos(bruto: str | None) -> str:
    try:
        valor = json.loads(bruto) if bruto else None
    except ValueError:
        return ""
    kind = valor.get("kind") if isinstance(valor, dict) else None
    return kind if isinstance(kind, str) else ""


def _alvo(bruto: str | None) -> AlvoObservado | None:
    try:
        valor = json.loads(bruto) if bruto else None
    except ValueError:
        return None
    if not isinstance(valor, dict) or valor.get("password") is True:
        return None
    rid = valor.get("resource_id")
    texto = valor.get("text")
    desc = valor.get("desc")
    rotulo = (texto if isinstance(texto, str) and texto.strip() else desc if isinstance(desc, str) else None)
    return AlvoObservado(resource_id=rid if isinstance(rid, str) and rid else None,
                         rotulo=rotulo.strip() if rotulo and rotulo.strip() else None)


def _toques(acoes: Sequence[Row]) -> list[AlvoObservado]:
    return [a for a in (_alvo(linhas.texto_ou_nulo(r, "target")) for r in acoes
                        if linhas.texto(r, "tool") in _TOQUES and linhas.texto(r, "status") == "done") if a is not None]


def _ultimo_toque(acoes: Sequence[Row]) -> AlvoObservado | None:
    toques = _toques(acoes)
    return toques[-1] if toques else None


def _primeiro_toque(acoes: Sequence[Row]) -> AlvoObservado | None:
    toques = _toques(acoes)
    return toques[0] if toques else None


def _toque_de_efeito(acoes: Sequence[Row]) -> AlvoObservado | None:
    for r in acoes:
        if linhas.inteiro(r, "side_effect") and linhas.texto(r, "status") == "done":
            alvo = _alvo(linhas.texto_ou_nulo(r, "target"))
            if alvo is not None:
                return alvo
    return None


def _desafio(rotulo: str) -> bool:
    return detectar_trava_generica(normalizar_texto_de_tela(rotulo), tem_onde_digitar=True) is not None


def _duracao(inicio: str | None, fim: str | None) -> float:
    if not inicio or not fim:
        return 0.0
    try:
        a = datetime.fromisoformat(inicio.replace("Z", "+00:00"))
        b = datetime.fromisoformat(fim.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return max(0.0, (b - a).total_seconds())


def _dias_antes(iso: str, dias: int) -> str:
    try:
        quando = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return to_iso(quando - timedelta(days=dias))



__all__ = ["DEFEITO_DIAS", "SqlLicoesRepository"]
