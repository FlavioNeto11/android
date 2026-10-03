"""Pacote A5 do ADR-054: liga o D1 aos conhecimentos nativos.

- a SOMBRA dos fluxos em prova e o escritor real de `skill_validation_results` entram no digest de cada execução
  (mineradores do `LearningService`);
- a política do D1 entra no `FlowStore` (com que status o fluxo aprendido nasce; a linha refutada renasce) e o ouvinte
  no `RecipeStore` (veto e trilha) — os DOIS do scheduler do central, postos pela composição;
- toda mudança de status feita pelas próprias lojas vira linha de `learning_transitions`, com o `content_hash` e o
  escopo calculados EXATAMENTE como o livro os lê (`FontesSql`): é o que faz o veto e o "Revisar" enxergarem a trilha.

A leitura das execuções mora aqui (infraestrutura: `Plan`, a identidade de etapa das receitas, o JSON das colunas
legadas); a regra fica em `application/nativos.py`. As lojas chamam política e ouvinte DENTRO da transação delas:
o que falha aqui vira log — o fluxo e a receita da execução nunca caem por causa do aprendizado. Por isso todo
`try` que engole a falha dentro da transação da loja envolve o que tocou num `db.savepoint()` (22.5): no PostgreSQL,
o erro engolido sem ele deixava a transação abortada, e o `COMMIT` da loja virava `ROLLBACK` sem aviso — a loja
devolvia o id de uma linha que nunca existiu.

A ADOÇÃO de um fluxo por uma habilidade (`SqlSkillRepository`, 22.4) também muda `flows.status`: `TrilhaDaAdocao`
leva a mudança à mesma trilha, com a pessoa que decidiu. Ali a trilha não é acessória — é o registro do gesto —,
então a falha dela desfaz a adoção inteira, como no interruptor antigo (`mudar_status_nativo`).
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Mapping
from datetime import datetime

from pydantic import ValidationError

from app.db import Database
from app.models import Plan, PlanStep
from app.modules.learning.application.evidencia_da_receita import EvidenciaDaReceita, RetrocargaDaReceita
from app.modules.learning.application.nativos import (AssinaturaDoPlano, ContraGravado, D1Nativo, Decidir,
                                                      ExecucaoAssentada, ExecucaoDeHabilidade, FluxoEmProva,
                                                      PassoAssinado, ProvaDaExecucao, ReclassificacaoDaForma,
                                                      SombraDosFluxos, ValidacaoPorExecucao)
from app.modules.learning.application.ports import RepositorioDeAprendizado
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.evidencia_invalida import run_da_etapa
from app.modules.learning.domain.livro import (escopo_da_receita, estado_nativo, fluxo_tem_efeito, hash_da_receita,
                                               ref_da_trilha)
from app.modules.learning.domain.vocabulario import LivroKind, Posicao
from app.modules.learning.infrastructure import linhas
from app.modules.learning.infrastructure.fontes import FontesSql
from app.modules.learning.infrastructure.reproducao_sql import ReproducoesSql
from app.modules.learning.infrastructure.validacao_de_skills import ValidacaoDeHabilidadesSql
from app.modules.skills.domain.document import content_hash
from app.modules.skills.infrastructure.sql_repository import SqlSkillRepository
from app.taskqueue.flows import RESERVED, FlowStore, MudancaDoFluxo, NascimentoDoFluxo, confirmada_a_mao
from app.taskqueue.recipes import MudancaDaReceita, ReceitaVista, RecipeStore, para_hash
from app.util import now, now_iso

log = logging.getLogger("poc.aprendizado")


# ------------------------------------------------------------------ leitura das execuções e dos fluxos em prova
def _plano(bruto: str | None) -> Plan | None:
    if not bruto:
        return None
    try:
        return Plan.model_validate_json(bruto)
    except ValidationError:
        return None


def assinatura(plano: Plan, run_id: str | None = None) -> AssinaturaDoPlano:
    """O plano reduzido ao que a sombra compara. Etapa com capability é a ação do catálogo; etapa livre é a identidade
    da etapa-modelo SEM a pós-condição (a chave, o efeito, as guardas e o nível de entrega, depois de `para_hash`: o
    valor do comando volta a `{nome}`), e a pós-condição vai à parte, como forma (30.36). Parâmetro reservado ou com o
    id da execução não é do modelo (a mesma regra de `learn_from_run`)."""
    parametros = {k: v for k, v in plano.parameters.items()
                  if k not in RESERVED and (run_id is None or run_id not in v)}
    passos = tuple(_passo(p, plano.app_id, parametros) for p in plano.steps)
    return AssinaturaDoPlano(passos=passos, parametros=parametros, na_acao=_na_acao(plano, parametros))


def _passo(p: PlanStep, app_do_plano: str | None, parametros: Mapping[str, str]) -> PassoAssinado:
    app = p.app_id or app_do_plano
    if p.capability:
        # a capability compara como antes: a ação, o efeito e o TIPO da pós-condição (nunca o valor)
        return PassoAssinado(acao=p.capability, side_effect=p.side_effect, pos=p.postcondition.kind, app=app)
    t = para_hash(p, dict(parametros))
    nivel = t.postcondition.required_delivery_level
    # Sem a pós-condição, é o que `step_template_hash` (recipes) junta: o mesmo plano de antes segue o mesmo aqui.
    bruto = json.dumps([t.template_key or t.key, t.side_effect, nivel.value if nivel else None, sorted(t.commit_guard)],
                       ensure_ascii=False)
    return PassoAssinado(acao=f"etapa:{hashlib.sha1(bruto.encode()).hexdigest()[:20]}", side_effect=t.side_effect,
                         pos=t.postcondition.kind, pos_valor=t.postcondition.value, app=app)


def _na_acao(plano: Plan, parametros: Mapping[str, str]) -> frozenset[str]:
    """Os parâmetros que alguma etapa usa para AGIR: no objetivo, na pré-condição, nos argumentos da capability, nas
    guardas e no seletor do efeito, por `{nome}` ou pelo valor escrito por extenso (≥ 3 caracteres, a regra de
    `para_hash`). O título fica de fora (o planejador o reescreve) e a pós-condição é forma. Medido no P4 de 03/10: o
    `expected_account` do fluxo do QA Messenger não aparecia em etapa nenhuma."""
    textos = [t for p in plano.steps
              for t in (p.goal, p.precondition, *p.bindings.values(), *p.commit_guard, p.commit_selector, *p.band_guard)
              if t]

    def usa(nome: str, valor: str) -> bool:
        literal = len(valor) >= 3 and "{" not in valor
        return any("{" + nome + "}" in t or (literal and valor in t) for t in textos)

    return frozenset(nome for nome, valor in parametros.items() if usa(nome, valor))


def _primeiro_aparelho(bruto: str | None) -> str | None:
    ids = linhas.json_legado(bruto)
    if isinstance(ids, list):
        return next((i for i in sorted(x for x in ids if isinstance(x, str))), None)
    return None


class LeituraSql:
    def __init__(self, db: Database) -> None:
        self._db = db

    def execucao(self, run_id: str) -> ExecucaoAssentada | None:
        row = self._db.one("SELECT id, command, status, simulated, plan, flow_id, skill_id, instance_ids, prova_fluxo_id"
                           " FROM runs WHERE id=?", (run_id,))
        if row is None:
            return None
        prova = linhas.texto_ou_nulo(row, "prova_fluxo_id")
        comparavel = (linhas.texto(row, "status") == "completed" and not linhas.texto_ou_nulo(row, "flow_id")
                      and not linhas.texto_ou_nulo(row, "skill_id") and not prova
                      and not confirmada_a_mao(self._db, run_id))
        plano = _plano(linhas.texto_ou_nulo(row, "plan")) if comparavel else None
        forma = assinatura(plano, run_id) if plano is not None and plano.steps and not plano.missing else None
        return ExecucaoAssentada(run_id=run_id, comando=linhas.texto(row, "command"),
                                 simulada=bool(linhas.inteiro(row, "simulated")),
                                 aparelho=_primeiro_aparelho(linhas.texto_ou_nulo(row, "instance_ids")),
                                 assinatura=forma,
                                 prova=self._prova(run_id, prova, linhas.texto(row, "status")) if prova else None)

    def _prova(self, run_id: str, fluxo_id: str, status: str) -> ProvaDaExecucao | None:
        """30.37: o desfecho da execução de prova pelas etapas (não pelo plano). A etapa-modelo do `for_each` nunca é
        executada e fica fora. Reprovada = `failed` cuja última tentativa não traz erro de IA (`attempts.error_kind`,
        RA-22): orçamento, teto, recusa e chave são infra, e infra não conta."""
        linha = self._db.one("SELECT plan FROM flows WHERE id=?", (fluxo_id,))
        conteudo = linhas.json_legado(linhas.texto_ou_nulo(linha, "plan")) if linha is not None else None
        if conteudo is None:
            return None
        hash_ = content_hash(conteudo)
        etapas = self._db.query(
            "SELECT s.seq, s.key, s.status, s.status_detail, (SELECT a.error_kind FROM attempts a WHERE a.step_id=s.id"
            " ORDER BY a.number DESC LIMIT 1) AS erro FROM steps s WHERE s.run_id=? AND s.for_each IS NULL"
            " ORDER BY s.seq, s.id", (run_id,))
        reprovada = next((e for e in etapas if linhas.texto(e, "status") == "failed"
                          and not linhas.texto_ou_nulo(e, "erro")), None)
        if reprovada is not None:
            motivo = (linhas.texto_ou_nulo(reprovada, "status_detail") or "sem detalhe")[:160]
            return ProvaDaExecucao(fluxo_id, hash_, Posicao.AGAINST,
                                   f"prova: etapa {linhas.inteiro(reprovada, 'seq')} ({linhas.texto(reprovada, 'key')}) "
                                   f"reprovada: {motivo}")
        if status == "completed" and etapas and all(linhas.texto(e, "status") == "succeeded" for e in etapas):
            return ProvaDaExecucao(fluxo_id, hash_, Posicao.FOR,
                                   f"prova: {len(etapas)}/{len(etapas)} etapas comprovadas")
        return ProvaDaExecucao(fluxo_id, hash_, None, f"prova sem desfecho de tarefa ({status})")

    def fluxos_em_prova(self) -> list[FluxoEmProva]:
        saida: list[FluxoEmProva] = []
        for r in self._db.query("SELECT id, status, command_template, plan, source, source_run_id FROM flows"
                                " WHERE status IN ('candidate','validated') ORDER BY created_at, id"):
            if (linhas.texto_ou_nulo(r, "source") or "").startswith("training"):
                continue
            estado = estado_nativo(LivroKind.FLUXO, linhas.texto(r, "status"))
            bruto = linhas.texto(r, "plan")
            conteudo = linhas.json_legado(bruto)            # o mesmo caminho de `FontesSql._fluxo`: o mesmo hash
            plano = _plano(bruto)
            if estado is None or conteudo is None or plano is None:
                continue
            saida.append(FluxoEmProva(id=linhas.texto(r, "id"), estado=estado,
                                      comando_modelo=linhas.texto(r, "command_template"), assinatura=assinatura(plano),
                                      efeito=fluxo_tem_efeito(conteudo), content_hash=content_hash(conteudo),
                                      nasceu_de=linhas.texto_ou_nulo(r, "source_run_id")))
        return saida

    def contra_de_fluxos(self) -> list[ContraGravado]:
        """As linhas `against` de fluxo, de execução, ainda sem a `forma` da mesma origem (30.36). De TODOS os fluxos (o
        filtro "em prova" é do passo, em Python): poucas hoje (2 no central em 03/10), porque só a sombra grava contra
        em fluxo e o fluxo sai da prova na segunda; se crescer, filtrar pelos em prova aqui."""
        return [ContraGravado(item_ref=linhas.texto(r, "item_ref"), origin_ref=linhas.texto(r, "origin_ref"),
                              run_id=linhas.texto(r, "run_id"), detail=linhas.texto_ou_nulo(r, "detail"))
                for r in self._db.query(
                    "SELECT e.item_ref, e.origin_ref, e.run_id, e.detail FROM learning_evidence e"
                    " WHERE e.stance = 'against' AND e.item_ref LIKE 'fluxo:%' AND e.run_id IS NOT NULL"
                    " AND NOT EXISTS (SELECT 1 FROM learning_evidence f WHERE f.item_ref = e.item_ref"
                    " AND f.origin_ref = e.origin_ref AND f.stance = 'forma') ORDER BY e.id")]

    def execucao_real(self, run_id: str) -> bool:
        """A execução existe e não é simulada (`runs.simulated=0`): a única que ensina de novo o que a evidência
        inválida desligou (30.23)."""
        row = self._db.one("SELECT simulated FROM runs WHERE id=?", (run_id,))
        return row is not None and not linhas.inteiro(row, "simulated")

    def execucao_de_habilidade(self, run_id: str) -> ExecucaoDeHabilidade | None:
        row = self._db.one("SELECT status, simulated, skill_id, skill_version, instance_ids FROM runs WHERE id=?",
                           (run_id,))
        if row is None:
            return None
        skill_id, versao = linhas.texto_ou_nulo(row, "skill_id"), linhas.inteiro_ou_nulo(row, "skill_version")
        if skill_id is None or versao is None:
            return None
        status = linhas.texto(row, "status")
        return ExecucaoDeHabilidade(run_id=run_id, skill_id=skill_id, versao=versao, status=status,
                                    simulada=bool(linhas.inteiro(row, "simulated")),
                                    aparelho=_primeiro_aparelho(linhas.texto_ou_nulo(row, "instance_ids")),
                                    # só o sucesso depende disto: a falha segue falha, e a pergunta é a mesma do fluxo
                                    confirmada_a_mao=status == "completed" and confirmada_a_mao(self._db, run_id))


# ------------------------------------------------------------------ a trilha das mudanças feitas pelas lojas
class TrilhaDasLojas:
    """`learning_transitions` para o que a própria loja mudou (nascimento, prova, quarentena, substituição, linha
    reaproveitada). O conteúdo e o escopo saem de `FontesSql` — a mesma leitura do livro, então o mesmo hash que o veto
    e o "Revisar" procuram. Sem quem decidiu, não há linha (a trilha nunca fica sem autor)."""

    def __init__(self, db: Database, *, clock: Callable[[], str] = now_iso) -> None:
        self._db = db
        self._fontes = FontesSql(db)
        self._clock = clock

    def registrar(self, kind: LivroKind, ref: str, de_status: str | None, para_status: str, *, motivo: str, por: str,
                  run_id: str | None) -> None:
        entrada = self._fontes.receita(ref) if kind is LivroKind.RECEITA else self._fontes.fluxo(ref)
        para = estado_nativo(kind, para_status)
        de = estado_nativo(kind, de_status) if de_status is not None else None
        if entrada is None or para is None or not por.strip() or not motivo.strip():
            return
        self._db.execute(
            "INSERT INTO learning_transitions(item_ref, item_kind, content_hash, scope_key, app_version, from_state,"
            " to_state, reason, decided_by, decided_at, run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (ref_da_trilha(kind, ref), kind.value, entrada.content_hash, entrada.scope_key, entrada.app_version,
             de.value if de is not None else None, para.value, motivo.strip()[:500], por, self._clock(), run_id))


# ------------------------------------------------------------------ o D1 dentro das lojas
#: `LearningService.avisar_mudanca_nativa`: o evento `learning.needs_person` (30.21) da mudança que a loja fez.
AvisoDeMudanca = Callable[..., None]


class PoliticaD1DoFluxo:
    """`flows.PoliticaDoFluxo`: o status com que o fluxo nasce de uma execução, e a trilha do que a loja mudou."""

    def __init__(self, d1: D1Nativo, trilha: TrilhaDasLojas, db: Database,
                 avisar: AvisoDeMudanca | None = None) -> None:
        self._d1 = d1
        self._trilha = trilha
        self._db = db
        self._avisar = avisar

    def ao_nascer(self, nascimento: NascimentoDoFluxo) -> str | None:
        try:
            conteudo = linhas.json_legado(nascimento.plano)
            return self._d1.fluxo_ao_nascer(nascimento.match_key,
                                            content_hash(conteudo) if conteudo is not None else None,
                                            nascimento.reaproveita, nascimento.run_id)
        except Exception:  # noqa: BLE001 - sem a regra, o lado seguro: nasce inerte (a sombra decide depois)
            log.exception("aprendizado: política de nascimento do fluxo (%s)", nascimento.run_id)
            return "candidate" if nascimento.reaproveita is None else None

    def mudou(self, mudanca: MudancaDoFluxo) -> None:
        m = mudanca
        try:
            with self._db.savepoint():                # dentro do `try`: o savepoint precisa VER a falha para desfazê-la
                self._trilha.registrar(LivroKind.FLUXO, m.flow_id, m.de, m.para, motivo=m.motivo, por=m.por,
                                       run_id=m.run_id)
                if self._avisar is not None:      # 30.21: depois da trilha, no mesmo savepoint (a falha sobe até ele)
                    self._avisar(LivroKind.FLUXO, m.flow_id, m.de, m.para, by=m.por)
        except Exception:  # noqa: BLE001 - a trilha e o aviso informam; o fluxo aprendido não cai por causa deles
            log.exception("aprendizado: trilha do fluxo %s", m.flow_id)



class OuvinteD1DasReceitas:
    """`recipes.OuvinteDasReceitas`: o veto do caminho que uma pessoa desligou e a trilha do que a loja mudou."""

    def __init__(self, d1: D1Nativo, trilha: TrilhaDasLojas, db: Database,
                 avisar: AvisoDeMudanca | None = None) -> None:
        self._d1 = d1
        self._trilha = trilha
        self._db = db
        self._avisar = avisar

    def vetada(self, receita: ReceitaVista) -> bool:
        r = receita
        try:
            # Só leitura, mas dentro da transação da loja: uma consulta que falha também a abortaria no PostgreSQL.
            with self._db.savepoint():
                return self._d1.receita_vetada(hash_da_receita(linhas.json_legado(r.actions)),
                                               escopo_da_receita(r.package, r.app_version, r.signature, r.variant,
                                                                 r.step_hash), r.app_version,
                                               run_da_etapa(r.learned_from))
        except Exception:  # noqa: BLE001 - o veto informa a loja; ilegível não trava o aprendizado da etapa
            log.exception("aprendizado: veto da receita (%s)", r.step_hash)
            return False

    def exige_o_dono(self, recipe_id: int, receita: ReceitaVista) -> bool | None:
        r = receita
        try:
            with self._db.savepoint():                # só leitura, dentro da transação da loja (ver `vetada`)
                return self._d1.receita_reaprendida(escopo_da_receita(r.package, r.app_version, r.signature,
                                                                      r.variant, r.step_hash), recipe_id)
        except Exception:  # noqa: BLE001 - sem resposta: não sobe agora e a trilha não ganha motivo falso
            log.exception("aprendizado: reaprendida da receita %s", recipe_id)
            return None

    def mudou(self, mudanca: MudancaDaReceita) -> None:
        m = mudanca
        try:
            with self._db.savepoint():                # a execução de origem também é lida sob ele
                origem = self._execucao_de_origem(m.recipe_id) if m.de is None else None
                self._trilha.registrar(LivroKind.RECEITA, str(m.recipe_id), m.de, m.para, motivo=m.motivo,
                                       por=m.por, run_id=origem)
                if self._avisar is not None:      # 30.21: depois da trilha, no mesmo savepoint
                    self._avisar(LivroKind.RECEITA, str(m.recipe_id), m.de, m.para, by=m.por)
        except Exception:  # noqa: BLE001 - a trilha e o aviso informam; a receita não cai por causa deles
            log.exception("aprendizado: trilha da receita %s", m.recipe_id)


    def _execucao_de_origem(self, recipe_id: int) -> str | None:
        row = self._db.one("SELECT s.run_id FROM recipes r JOIN steps s ON s.id = r.learned_from_step WHERE r.id=?",
                           (recipe_id,))
        return linhas.texto_ou_nulo(row, "run_id") if row is not None else None


class TrilhaDaAdocao:
    """`skills.FlowStatusTrail` (22.4): o status do fluxo que a adoção por uma habilidade muda, na trilha, com a pessoa
    que adotou ou desfez. O mapa do status nativo para o estado do livro é o do interruptor antigo (`estado_nativo`:
    `active`→`published`, `disabled`→`disabled`), dentro de `TrilhaDasLojas.registrar`.

    Sem `try` e sem savepoint, de propósito: aqui a trilha é o registro do gesto, e a falha dela sobe e desfaz a
    adoção inteira (a mesma regra de `mudar_status_nativo`, que é o outro caminho da pessoa para o mesmo status)."""

    def __init__(self, trilha: TrilhaDasLojas) -> None:
        self._trilha = trilha

    def flow_status_changed(self, flow_id: str, frm: str, to: str, *, by: str, reason: str) -> None:
        self._trilha.registrar(LivroKind.FLUXO, flow_id, frm, to, motivo=reason, por=by, run_id=None)


# ------------------------------------------------------------------ composição
def ligar(servico: LearningService, repo: RepositorioDeAprendizado, db: Database, *,
          concordancias: Callable[[], int] = lambda: 1, com_prova: Callable[[], bool] = lambda: True,
          simulada_publica: Callable[[], bool] = lambda: False,
          habilidades: SqlSkillRepository | None = None, fluxos: FlowStore | None = None,
          receitas: RecipeStore | None = None, decidir: Decidir | None = None,
          relogio: Callable[[], datetime] = now) -> D1Nativo:
    """Registra os mineradores no digest e põe o D1 nas lojas do central. `concordancias`/`com_prova`: o bloco
    `aprendizado.fluxo` VIGENTE (lido a cada uso). `decidir(texto, run_id)`: a linha do tempo da execução."""
    leitura = LeituraSql(db)
    trilha = TrilhaDasLojas(db)
    d1 = D1Nativo(repo, com_prova=com_prova, relogio=relogio, execucao_real=leitura.execucao_real,
                  simulada_publica=simulada_publica)
    servico.registrar_minerador(SombraDosFluxos(servico, repo, leitura, concordancias=concordancias,
                                                decidir=decidir))
    servico.registrar_passo(ReclassificacaoDaForma(repo, leitura, decidir=decidir))   # 30.36
    reproducoes = ReproducoesSql(db)                                                  # 30.39: a evidência da receita
    servico.registrar_minerador(EvidenciaDaReceita(repo, reproducoes))
    servico.registrar_passo(RetrocargaDaReceita(
        repo, reproducoes, limite_por_receita=lambda: servico.ajustes.retencao.evidencias_por_item))
    if habilidades is not None:
        servico.registrar_minerador(ValidacaoPorExecucao(leitura, ValidacaoDeHabilidadesSql(habilidades),
                                                         decidir=decidir))
        habilidades.flow_trail = TrilhaDaAdocao(trilha)
    if fluxos is not None:
        fluxos.politica = PoliticaD1DoFluxo(d1, trilha, db, servico.avisar_mudanca_nativa)
    if receitas is not None:
        receitas.ouvinte = OuvinteD1DasReceitas(d1, trilha, db, servico.avisar_mudanca_nativa)
    return d1


__all__ = ["LeituraSql", "OuvinteD1DasReceitas", "PoliticaD1DoFluxo", "TrilhaDaAdocao", "TrilhaDasLojas", "assinatura",
           "ligar"]
