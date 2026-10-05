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
import re
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta

from pydantic import ValidationError

from app.automation.tools import COMMIT_VOCAB
from app.db import Database, Row
from app.models import Plan, PlanStep
from app.contracts.origem import PREFIXO_LOTE, PREFIXO_VALIDACAO, eh_ensaio_de_leitura, eh_execucao_de_validacao
from app.modules.learning.application.evidencia_da_receita import (EvidenciaDaReceita, InvalidaDaReproducao,
                                                                   ReproducaoAConferir, RetrocargaDaReceita)
from app.modules.learning.application.nativos import (AssinaturaDoPlano, ContraGravado, D1Nativo, Decidir,
                                                      EnsinadoAEsperar,
                                                      ExecucaoAssentada, ExecucaoDeHabilidade, FluxoEmProva,
                                                      ForDeProva, InvalidaARevalidar, PassoAssinado, ProvaDaExecucao,
                                                      ReclassificacaoDaForma, ReclassificacaoDoEfeitoDuplicado,
                                                      RevalidacaoDaConferencia, SombraDosFluxos, ValidacaoPorExecucao)
from app.modules.learning.application.ports import RepositorioDeAprendizado
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.evidencia_invalida import run_da_etapa
from app.modules.learning.domain.promocao import ORIGEM_DA_REPRODUCAO
from app.modules.learning.domain.prova import (PREFIXO_DA_EXPANSAO, AcaoDaProva, EtapaDaProva, MotivoDaInvalida,
                                               TentativaDaProva, amostra_do_rastro, conferencia_revalida,
                                               detalhe_da_invalida, efeito_repetido, evidencia_de_uso,
                                               motivo_da_invalida,
                                               veredito_da_prova)
from app.modules.learning.domain.livro import (escopo_da_receita, estado_nativo, fluxo_tem_efeito, hash_da_receita,
                                               ref_da_trilha)
from app.modules.learning.domain.vocabulario import LivroKind, Posicao
from app.modules.learning.infrastructure import linhas
from app.modules.learning.infrastructure.fontes import FontesSql
from app.modules.learning.infrastructure.reproducao_sql import ReproducoesSql
from app.modules.learning.infrastructure.validacao_de_skills import ValidacaoDeHabilidadesSql
from app.modules.skills.domain.document import content_hash
from app.modules.skills.infrastructure.sql_repository import SqlSkillRepository
from app.taskqueue.flows import (PREFIXO_DO_TREINO, RESERVED, FlowStore, MudancaDoFluxo, NascimentoDoFluxo,
                                 confirmada_a_mao, ensinado_em_prova)
from app.taskqueue.recipes import MudancaDaReceita, ReceitaVista, RecipeStore, para_hash
from app.util import now, now_iso, parse_iso

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


def _acao_da_prova(a: Row) -> AcaoDaProva:
    """Uma linha de `actions` como dado da prova. `parece_commit`: o ALVO resolvido (`actions.target`, o `UiElement`
    do que foi tocado) tem cara de envio, o mesmo critério de `automation.tools.looks_like_commit`, que o executor
    aplica ao elemento; só vale para o toque (o `type_text` tem o campo como alvo, e o campo não é o envio)."""
    args = linhas.json_legado(linhas.texto_ou_nulo(a, "args"))
    alvo = linhas.json_legado(linhas.texto_ou_nulo(a, "target"))
    tool = linhas.texto(a, "tool")
    parece = False
    if tool in ("tap", "long_press") and isinstance(alvo, dict):
        partes = [alvo.get("text"), alvo.get("desc"),
                  str(alvo.get("resource_id") or "").rsplit("/", 1)[-1].replace("_", " ")]
        parece = bool(COMMIT_VOCAB.search(" ".join(p for p in partes if isinstance(p, str))))
    return AcaoDaProva(tool=tool, status=linhas.texto(a, "status"),
                       is_commit_action=isinstance(args, dict) and args.get("is_commit_action") is True,
                       parece_commit=parece)


def _primeiro_aparelho(bruto: str | None) -> str | None:
    ids = linhas.json_legado(bruto)
    if isinstance(ids, list):
        return next((i for i in sorted(x for x in ids if isinstance(x, str))), None)
    return None


#: O `lastUpdateTime` do `dumpsys` vem no fuso DO APARELHO, sem fuso escrito (`2026-10-04 20:08:02`), e o fuso de cada
#: emulador não está no banco. Em UTC, a hora verdadeira fica entre a lida −14 h e a lida +12 h (os fusos vão de −12 a
#: +14). Para afirmar que a atualização foi ANTES do início, conta-se o pior caso, a lida +12 h.
_FOLGA_DO_FUSO = timedelta(hours=12)
_FORMATO_DO_DUMPSYS = "%Y-%m-%d %H:%M:%S"


def versao_estavel_na_execucao(versao: str | None, atualizado: str | None, inicio: str | None) -> str | None:
    """30.74 (V1 da leitura): a versão observada AGORA só vale para a execução se o app não pode ter mudado depois do
    início dela. Sem a hora da última atualização, sem o início, ou com qualquer uma ilegível: `None` (na dúvida, a
    evidência fica sem versão, como antes do 30.74; uma versão errada seria prova falsa de "versão viva").
    O preço da folga do fuso: a execução que começa até ~12 h depois de uma atualização fica sem versão."""
    if not versao or not atualizado or not inicio:
        return None
    try:
        lida = datetime.strptime(atualizado.strip(), _FORMATO_DO_DUMPSYS)
        comeco = parse_iso(inicio)
    except ValueError:
        return None
    if comeco is None:
        return None
    pior_caso = lida.replace(tzinfo=comeco.tzinfo) + _FOLGA_DO_FUSO
    return versao if pior_caso < comeco else None


class LeituraSql:
    def __init__(self, db: Database) -> None:
        self._db = db

    def execucao(self, run_id: str) -> ExecucaoAssentada | None:
        row = self._db.one("SELECT id, command, status, simulated, plan, flow_id, skill_id, instance_ids, prova_fluxo_id,"
                           " idempotency_key, cancel_requested FROM runs WHERE id=?", (run_id,))
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
                                 prova=self._prova(run_id, prova, linhas.texto(row, "status")) if prova else None,
                                 uso=self._uso(row) if not prova else None)

    def versao_do_fluxo_no_aparelho(self, fluxo_id: str, aparelho: str | None, run_id: str) -> str | None:
        """30.74: a versão do app do fluxo observada no aparelho da execução, para a evidência do fluxo levar a versão
        como a da receita leva (`recipes.app_version`). Sem ela, o parecer do curador dizia "sem versão do app
        registrada" e pedia `reproducao_na_versao_viva`, que nenhuma prova satisfazia: as 113 evidências reais de fluxo
        do central tinham `app_version` nulo (05/10).

        A observação é a de AGORA (a do digest), não a da execução. Se o app pode ter mudado depois do início da
        execução (digest atrasado, a primeira passada depois de um deploy), a evidência velha levaria a versão nova:
        uma prova falsa de "versão viva" (V1 da leitura do 30.74). Por isso só vale a versão cuja última atualização
        no aparelho é COM CERTEZA anterior ao início (`versao_estavel_na_execucao`); na dúvida, `None`, como antes."""
        if not aparelho:
            return None
        linha = self._db.one(
            "SELECT d.observed_version_name AS v, d.last_update_time AS atualizado, r.started_at AS inicio"
            " FROM flows f JOIN apps a ON a.id = f.app_id"
            " JOIN device_app_state d ON d.package_name = a.package AND d.instance_id = ?"
            " JOIN runs r ON r.id = ?"
            " WHERE f.id = ? AND d.observed_version_name IS NOT NULL AND d.observed_version_name <> ''",
            (aparelho, run_id, fluxo_id))
        if linha is None:
            return None
        return versao_estavel_na_execucao(linhas.texto_ou_nulo(linha, "v"), linhas.texto_ou_nulo(linha, "atualizado"),
                                          linhas.texto_ou_nulo(linha, "inicio"))

    def _uso(self, row: Row) -> ProvaDaExecucao | None:
        """30.51: a execução comum que usou o fluxo (`runs.flow_id`) pela regra da prova (`_prova`). Ensaio, lote de teste
        e execução com cancelamento pedido (pela pessoa ou pelo sistema) não contam contra (`evidencia_de_uso`)."""
        fluxo_id = linhas.texto_ou_nulo(row, "flow_id")
        if not fluxo_id or linhas.texto_ou_nulo(row, "skill_id"):
            return None
        run_id = linhas.texto(row, "id")
        v = self._prova(run_id, fluxo_id, linhas.texto(row, "status"))
        if v is None:
            return None
        chave = linhas.texto_ou_nulo(row, "idempotency_key") or ""
        conta_contra = not (eh_ensaio_de_leitura(chave) or chave.startswith(PREFIXO_LOTE)
                            or linhas.inteiro(row, "cancel_requested"))
        achado = evidencia_de_uso(v.posicao, v.detalhe, conta_contra=conta_contra)
        return None if achado is None else ProvaDaExecucao(fluxo_id, v.content_hash, achado[0], achado[1])

    def _prova(self, run_id: str, fluxo_id: str, status: str) -> ProvaDaExecucao | None:
        """30.37/30.42: o desfecho da execução de prova pelas etapas (não pelo plano), pela regra única de
        `domain.prova.veredito_da_prova`: a etapa-modelo do `for_each` nunca é executada e fica fora; infra
        (`attempts.error_kind`, RA-22: orçamento, teto, recusa e chave) não conta; o efeito repetido, a abertura que
        falhou e o ator que não agiu viram `invalida`; plano revisado não conta."""
        linha = self._db.one("SELECT plan FROM flows WHERE id=?", (fluxo_id,))
        conteudo = linhas.json_legado(linhas.texto_ou_nulo(linha, "plan")) if linha is not None else None
        if conteudo is None:
            return None
        v = veredito_da_prova(self._etapas_da_prova(run_id), status=status, expansoes=self._expansoes(run_id))
        texto = detalhe_da_invalida(v.motivo, v.texto) if v.motivo is not None else v.texto
        # 30.48: a prova que rodou só os N primeiros itens do `for_each` diz isso na própria evidência, que o painel e o
        # parecer do curador mostram. É uma evidência como outra: não muda nenhuma condição da D1.
        amostra = amostra_do_rastro(self._motivos_da_expansao(run_id))
        if amostra is not None and v.posicao is not None:
            como = "provado" if v.posicao is Posicao.FOR else "prova"
            texto = f"{texto} — {como} em amostra de {amostra[0]} (de {amostra[1]} itens)"
        return ProvaDaExecucao(fluxo_id, content_hash(conteudo), v.posicao, texto)

    def efeito_repetido_da_execucao(self, run_id: str, *, regra_propria: bool = True) -> int | None:
        """30.42: as cópias do efeito da execução (`domain.prova.efeito_repetido`), ou `None`. O passo da curadoria
        aplica a MESMA regra do veredito às provas que já deixaram o `for`. 30.43: `regra_propria=False`, só o 29.58."""
        return efeito_repetido(self._etapas_da_prova(run_id), regra_propria=regra_propria)

    def invalidas_a_revalidar(self) -> list[InvalidaARevalidar]:
        """30.53: as `invalida:efeito_repetido` ainda sem a `revalidada` irmã cuja execução a regra de hoje absolve
        (`conferencia_revalida`): os fatos de repetição das etapas e as etapas de efeito comprovadas na versão final do
        plano de cada objetivo (uma mensagem por etapa)."""
        linhas_ = self._db.query(
            "SELECT e.item_ref, e.origin_ref, e.run_id, e.simulated, e.instance_id, e.detail FROM learning_evidence e"
            " WHERE e.stance = 'invalida' AND e.run_id IS NOT NULL AND e.detail LIKE ?"
            " AND NOT EXISTS (SELECT 1 FROM learning_evidence f WHERE f.item_ref = e.item_ref"
            " AND f.origin_ref = e.origin_ref AND f.stance = 'revalidada') ORDER BY e.id",
            (f"%invalida:{MotivoDaInvalida.EFEITO_REPETIDO.value}%",))
        saida: list[InvalidaARevalidar] = []
        absolvidas: dict[str, tuple[int, int] | None] = {}
        for r in linhas_:
            run_id = linhas.texto(r, "run_id")
            if motivo_da_invalida(linhas.texto_ou_nulo(r, "detail")) is not MotivoDaInvalida.EFEITO_REPETIDO:
                continue
            if run_id not in absolvidas:
                absolvidas[run_id] = self._absolvida(run_id)
            achado = absolvidas[run_id]
            if achado is None:
                continue
            marca = _MARCA_DA_LINHA.match(linhas.texto_ou_nulo(r, "detail") or "")
            saida.append(InvalidaARevalidar(
                item_ref=linhas.texto(r, "item_ref"), origin_ref=linhas.texto(r, "origin_ref"), run_id=run_id,
                simulada=bool(linhas.inteiro(r, "simulated")), aparelho=linhas.texto_ou_nulo(r, "instance_id"),
                marca=marca.group(0) if marca else None, copias=achado[0], esperadas=achado[1]))
        return saida

    def _absolvida(self, run_id: str) -> tuple[int, int] | None:
        """(cópias contadas, etapas de efeito comprovadas) quando a regra de hoje absolve a execução; senão `None`."""
        fatos: list[dict[str, object]] = []
        for r in self._db.query("SELECT result FROM steps WHERE run_id=? AND result LIKE ? ORDER BY seq, id",
                                (run_id, "%efeito_repetido%")):
            resultado = linhas.json_legado(linhas.texto_ou_nulo(r, "result"))
            fato = resultado.get("efeito_repetido") if isinstance(resultado, dict) else None
            if isinstance(fato, dict):
                fatos.append(fato)
        esperadas = int(self._db.scalar(
            "SELECT COUNT(*) FROM steps s JOIN objectives o ON o.id = s.objective_id WHERE s.run_id=?"
            " AND s.side_effect=1 AND s.status='succeeded' AND s.plan_version = o.plan_version", (run_id,)) or 0)
        if not conferencia_revalida(fatos, esperadas):
            return None
        return max(int(str(f.get("copias"))) for f in fatos), esperadas

    def reproducoes_a_conferir(self, run_id: str | None = None) -> list[ReproducaoAConferir]:
        """30.43: as linhas `reproducao:` (for/against) sem a `invalida` irmã, de execução de validação (a MESMA
        derivação de `contracts.origem`) ou com o `efeito_repetido` do 29.58 em alguma etapa; `run_id` restringe a uma.
        A execução sem repetição volta a ser conferida a cada passo: são poucas (o P4 e as do 29.58)."""
        sql = ("SELECT e.item_ref, e.origin_ref, e.run_id, e.simulated, e.instance_id, r.prova_fluxo_id,"
               " r.idempotency_key FROM learning_evidence e JOIN runs r ON r.id = e.run_id"
               " WHERE e.origin_ref LIKE ? AND e.stance IN ('for', 'against')"
               " AND NOT EXISTS (SELECT 1 FROM learning_evidence f WHERE f.item_ref = e.item_ref"
               " AND f.origin_ref = e.origin_ref AND f.stance = 'invalida')"
               " AND (r.prova_fluxo_id IS NOT NULL OR r.idempotency_key LIKE ?"
               " OR EXISTS (SELECT 1 FROM steps s WHERE s.run_id = e.run_id AND s.result LIKE ?))")
        args: list[object] = [ORIGEM_DA_REPRODUCAO + "%", PREFIXO_VALIDACAO + "%", "%efeito_repetido%"]
        if run_id is not None:
            sql += " AND e.run_id = ?"
            args.append(run_id)
        return [ReproducaoAConferir(
                    item_ref=linhas.texto(r, "item_ref"), origin_ref=linhas.texto(r, "origin_ref"),
                    run_id=linhas.texto(r, "run_id"), simulada=bool(linhas.inteiro(r, "simulated")),
                    aparelho=linhas.texto_ou_nulo(r, "instance_id"),
                    de_validacao=eh_execucao_de_validacao(linhas.texto_ou_nulo(r, "prova_fluxo_id"),
                                                          linhas.texto_ou_nulo(r, "idempotency_key")))
                for r in self._db.query(sql + " ORDER BY e.id", tuple(args))]

    def _motivos_da_expansao(self, run_id: str) -> list[str]:
        return [linhas.texto(r, "reason") for r in self._db.query(
            "SELECT v.reason FROM plan_versions v JOIN objectives o ON o.id = v.objective_id"
            " WHERE o.run_id=? AND v.version > 1 AND v.reason LIKE ? ORDER BY v.version, v.objective_id",
            (run_id, PREFIXO_DA_EXPANSAO + "%"))]

    def _expansoes(self, run_id: str) -> frozenset[int]:
        """As versões do plano da execução que a expansão do `for_each` criou (`plan_versions.reason`), que o veredito não
        trata como replanejamento."""
        return frozenset(linhas.inteiro(r, "version") for r in self._db.query(
            "SELECT v.version FROM plan_versions v JOIN objectives o ON o.id = v.objective_id"
            " WHERE o.run_id=? AND v.version > 1 AND v.reason LIKE ? ORDER BY v.version",
            (run_id, PREFIXO_DA_EXPANSAO + "%")))

    def _etapas_da_prova(self, run_id: str) -> list[EtapaDaProva]:
        """As etapas executadas da prova, com o diário de ações (`actions`, todas as tentativas) e a última tentativa."""
        etapas = self._db.query(
            "SELECT s.id, s.seq, s.key, s.status, s.status_detail, s.plan_version, s.side_effect, s.driven_by,"
            " s.result FROM steps s WHERE s.run_id=? AND s.for_each IS NULL ORDER BY s.seq, s.id", (run_id,))
        ultimas: dict[str, tuple[str, str | None, str | None]] = {}
        for t in self._db.query(
                "SELECT t.step_id, t.id, t.error_kind, t.failure_kind FROM attempts t JOIN steps s ON s.id = t.step_id"
                " WHERE s.run_id=? AND s.for_each IS NULL ORDER BY t.step_id, t.number", (run_id,)):
            ultimas[linhas.texto(t, "step_id")] = (linhas.texto(t, "id"), linhas.texto_ou_nulo(t, "error_kind"),
                                                   linhas.texto_ou_nulo(t, "failure_kind"))
        por_etapa: dict[str, list[AcaoDaProva]] = {}
        por_tentativa: dict[str, list[AcaoDaProva]] = {}
        for a in self._db.query(
                "SELECT s.id AS step_id, t.id AS attempt_id, a.tool, a.status, a.args, a.target FROM actions a"
                " JOIN attempts t ON t.id = a.attempt_id JOIN steps s ON s.id = t.step_id"
                " WHERE s.run_id=? AND s.for_each IS NULL ORDER BY s.id, t.number, a.seq", (run_id,)):
            acao = _acao_da_prova(a)
            por_etapa.setdefault(linhas.texto(a, "step_id"), []).append(acao)
            por_tentativa.setdefault(linhas.texto(a, "attempt_id"), []).append(acao)
        saida: list[EtapaDaProva] = []
        for e in etapas:
            sid = linhas.texto(e, "id")
            ultima = None
            if sid in ultimas:
                tentativa, erro, falha = ultimas[sid]
                ultima = TentativaDaProva(erro, falha, tuple(por_tentativa.get(tentativa, ())))
            resultado = linhas.json_legado(linhas.texto_ou_nulo(e, "result"))
            saida.append(EtapaDaProva(
                seq=linhas.inteiro(e, "seq"), key=linhas.texto(e, "key"), status=linhas.texto(e, "status"),
                plan_version=linhas.inteiro(e, "plan_version"), side_effect=bool(linhas.inteiro(e, "side_effect")),
                driven_by=linhas.texto_ou_nulo(e, "driven_by"), detalhe=linhas.texto_ou_nulo(e, "status_detail"),
                ultima=ultima, acoes=tuple(por_etapa.get(sid, ())),
                efeito_do_resultado=resultado.get("efeito_repetido") if isinstance(resultado, dict) else None))
        return saida

    def fors_de_prova(self) -> list[ForDeProva]:
        """30.42: as linhas `for` de fluxo cuja origem é uma EXECUÇÃO DE PROVA (`runs.prova_fluxo_id`) e que ainda não
        têm a `invalida` da mesma origem ao lado. O passo da curadoria as recompara com a regra de hoje."""
        return [ForDeProva(item_ref=linhas.texto(r, "item_ref"), origin_ref=linhas.texto(r, "origin_ref"),
                           run_id=linhas.texto(r, "run_id"), detail=linhas.texto_ou_nulo(r, "detail"),
                           simulada=bool(linhas.inteiro(r, "simulated")),
                           aparelho=linhas.texto_ou_nulo(r, "instance_id"))
                for r in self._db.query(
                    "SELECT e.item_ref, e.origin_ref, e.run_id, e.detail, e.simulated, e.instance_id"
                    " FROM learning_evidence e JOIN runs r ON r.id = e.run_id"
                    " WHERE e.stance = 'for' AND e.item_ref LIKE 'fluxo:%' AND r.prova_fluxo_id IS NOT NULL"
                    " AND NOT EXISTS (SELECT 1 FROM learning_evidence f WHERE f.item_ref = e.item_ref"
                    " AND f.origin_ref = e.origin_ref AND f.stance = 'invalida') ORDER BY e.id")]

    def ensinado_a_esperar(self, fluxo_id: str) -> EnsinadoAEsperar | None:
        """30.81: o fluxo ensinado que ainda espera a prova (a regra é a do `match`, `flows.ensinado_em_prova`) e as
        receitas ativas da mesma sessão."""
        row = self._db.one("SELECT * FROM flows WHERE id=?", (fluxo_id,))
        espera = ensinado_em_prova(self._db, row) if row is not None else None
        if espera is None:
            return None
        sessao = str(espera["sessao"])
        receitas = tuple(str(r["id"]) for r in self._db.query(
            "SELECT id FROM recipes WHERE learned_from_step=? AND status='active' ORDER BY id",
            (f"{PREFIXO_DO_TREINO}{sessao}",)))
        return EnsinadoAEsperar(sessao=sessao, receitas=receitas)

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
                    " AND f.origin_ref = e.origin_ref AND f.stance = 'forma')"
                    # 30.42: o `against` que a `invalida` da mesma origem corrigiu não é recomparado (não conta mais)
                    " AND NOT EXISTS (SELECT 1 FROM learning_evidence g WHERE g.item_ref = e.item_ref"
                    " AND g.origin_ref = e.origin_ref AND g.stance = 'invalida') ORDER BY e.id")]

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
            log.exception("aprendizado: trilha do fluxo (%s)", m.run_id or "sem execução")   # N1: o id não vai ao log



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


_MARCA_DA_LINHA = re.compile(r"\[[0-9a-f]{12}\]")


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
                                                decidir=decidir, ensinado=leitura.ensinado_a_esperar))
    servico.registrar_passo(ReclassificacaoDaForma(repo, leitura, decidir=decidir))   # 30.36
    servico.registrar_passo(ReclassificacaoDoEfeitoDuplicado(repo, leitura, decidir=decidir))   # 30.42
    servico.registrar_passo(RevalidacaoDaConferencia(repo, leitura, decidir=decidir))   # 30.53
    reproducoes = ReproducoesSql(db)                                                  # 30.39: a evidência da receita
    servico.registrar_minerador(EvidenciaDaReceita(repo, reproducoes))
    # 30.43: depois da evidência da receita (precisa da linha) e antes do fechamento do pedido de validação (ligado
    # depois, em `ligar_validacao`): o pedido da receita fecha pela `invalida`
    repetida = InvalidaDaReproducao(repo, leitura)
    servico.registrar_minerador(repetida)
    servico.registrar_passo(repetida)
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
           "ligar", "versao_estavel_na_execucao"]
