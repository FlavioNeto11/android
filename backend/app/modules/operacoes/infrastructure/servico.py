"""A operação com N agentes (31.154, migração 124, adendo v1.94): um objetivo único entregue a N alvos, cada alvo =
persona + conta (dela, no app da operação) + aparelho, numa execução PRÓPRIA.

Uma execução só não serve: `objectives` tem UNIQUE(run_id, instance_id), e 30 contas não cabem em 9 aparelhos de uma
vez. A fila por aparelho do escalonador já serializa as execuções do mesmo aparelho; aqui só se cria, lê, cancela e
libera. Nada troca de app: o alvo que não pode avançar PARA no estágio, com o motivo, e a contagem por motivo é a
capacidade operacional que o dono pediu ver ("30 solicitados / X contas / Y sessões / Z disponíveis…").

Toda execução de alvo nasce com o teto de autonomia `preparar` (28.23): o efeito para depois do rascunho, com o pedido
de aprovação que carrega o texto — esse É o estágio `acao_preparada`, sem gancho novo no despacho. `liberar` aprova esses
pedidos pelo serviço de aprovações de sempre, só com o eco do texto que a pessoa leu (31.49) e só até
`LimitsCfg.operacao_max_acoes_executadas`. Nenhuma aprovação é automática.

A exceção (31.253, ADR-082, decisão do dono em 07/10): na operação com `acao_final=executar`, o alvo cuja persona está no
grupo `LimitsCfg.grupo_sem_aprovacao` nasce com o teto `agir`. Não há pedido de aprovação nem liberar para ele; a porta
segue com as recusas, a frota, os tetos e a proteção de conta. Desliga em `operacao_grupo_liberado_executa`.
"""
from __future__ import annotations

import hashlib
import re
import json
import secrets
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal

from app.contracts.origem import PREFIXO_OPERACAO
from app.db import OPERATIONAL_ERRORS, Database, Row, coluna_ausente, dumps, loads
from app.models import InstanceState, RunCreate, RunStatus, RunTarget, SessionStatus, StepStatus
from app.modules.applications.infrastructure import registry as apps_registrados
from app.modules.applications.infrastructure.registry import definition_of
from app.modules.operacoes.domain import fila as filas, latencia, relatorio as rel
from app.modules.operacoes.domain.estagios import ESTADOS, EtapaLida, FatosDoAlvo, Leitura, derivar, motivo_curto
from app.modules.pedidos.domain import resumo_da_pesquisa
from app.modules.pedidos.infrastructure.repositorio_memoria import RepositorioDeMemoria
from app.security.redaction import chave_sensivel, looks_secret, parece_senha_ou_codigo, redact
from app.planning import costs, custo_por_passo
from app.social.repository import sessao_vencida, troca_declarada
from app.social.service import SocialError
from app.taskqueue.plano_da_operacao import NOMES_RESERVADOS, normal
from app.taskqueue.recipes import SENSITIVE_PARAM
from app.taskqueue.service import RunError
from app.util import now_iso

if TYPE_CHECKING:
    from app.config import LimitsCfg, PesquisaCfg
    from app.events import EventBus
    from app.social.approvals import Approval, ApprovalService
    from app.social.repository import SocialRepository
    from app.taskqueue.service import RunService

#: O motivo, frase curta e estável, que entra na contagem por motivo da capacidade.
SEM_PERSONA = "persona inexistente"
SEM_CONTA = "sem conta"
SEM_SESSAO = "sem sessão"
#: A conta tem sessão em mais de um aparelho e nenhuma no vínculo principal da persona: qual age não se adivinha.
FORA_DO_PRINCIPAL = "sessão fora do aparelho principal"
SEM_APARELHO = "aparelho indisponível"
TETO_DE_CUSTO = "teto de custo"
#: O motivo de quem parou pelo teto da OPERAÇÃO depois de criado (corte suave): recusado no planejamento, ou cortado no meio
#: pela conferência do roteador (`Teto de custo da operação atingido: …`). Um motivo só, para a contagem por motivo.
TETO_DA_OPERACAO = "teto da operação"
_DO_TETO = re.compile(r"^(teto da operação:|Teto de custo da operação atingido)")
LIMITE_DE_ACOES = "limite de ações executadas"
#: 31.174: o aparelho da sessão existe, mas não recebe tarefa agora (fora do ar, na loja ou com conta travada).
APARELHO_INAPTO = "aparelho fora do ar ou com conta travada"
AGUARDA_LIBERACAO = "aguarda liberação"
#: O tamanho da pergunta da execução em `needs_input` no GET da operação (`alvos[].aguarda_resposta.pergunta`).
PERGUNTA_MAX = 300


#: Um @ de conta no texto do motivo (o da regra da frota cita o alvo): o motivo da operação diz "o perfil alvo".
_ARROBA = re.compile(r"(?<![\w.])@[A-Za-z0-9._]{1,60}")


def _motivo(texto: object) -> str | None:
    """O motivo REDIGIDO, numa linha e SEM @ de conta. O do objetivo pode trazer texto lido da tela, da pergunta à pessoa
    ou o @ do alvo (a porta de frota o cita), e vai para o banco, o evento `operacao.alvo`, a API e o relatório."""
    if texto and _DO_TETO.match(str(texto)):
        return TETO_DA_OPERACAO
    return motivo_curto(_ARROBA.sub("o perfil alvo", redact(str(texto)) or "") if texto else None)


def _resumo_do_alvo(a: Mapping[str, object]) -> dict[str, object]:
    """O alvo na lista filtrada (v1.116): o que o histórico da persona mostra, sem os estágios e sem o texto."""
    resultado = a.get("resultado") if isinstance(a.get("resultado"), dict) else None
    acao = resultado.get("acao_final") if isinstance(resultado, dict) else None
    latencia_lida = a.get("latencia")
    return {"profile_id": a["profile_id"], "instance_id": a.get("instance_id"), "estado": a["estado"],
            "estagio": a["estagio"], "motivo": a.get("motivo"), "parou_em": a.get("parou_em"),
            "acao_verificada": acao.get("verificada") if isinstance(acao, dict) else None,
            "custo_usd": a.get("custo_usd"),
            "duracao_ms": latencia_lida.get("duracao_ms") if isinstance(latencia_lida, dict) else None}


class OperacaoError(Exception):
    def __init__(self, code: str, message: str, status: int = 409, **extra: object) -> None:
        super().__init__(message)
        self.code, self.message, self.status, self.extra = code, message, status, extra


@dataclass(frozen=True)
class AlvoPedido:
    profile_id: str
    account_id: str | None = None
    instance_id: str | None = None


@dataclass
class _Lote:
    """O que uma leitura da operação busca de uma vez para todos os alvos (31.194: com 30 alvos, o GET fazia 278
    consultas, quase todas uma por alvo). Vale só durante um `ler`; fora dele, cada ajudante consulta como antes."""

    runs: dict[str, Row]
    objetivos: dict[str, Row]
    gasto: dict[str, float]
    pesquisa: dict[str, float]
    personas: dict[str, Row]
    contas: dict[tuple[str, str], Row]
    sessoes: dict[str, list[str]]
    travados: set[str]
    #: As etapas da versão vigente do plano de cada objetivo buscado (chave presente = buscado, mesmo sem etapa).
    etapas: dict[str, list[Row]]
    #: O pedido de aprovação mais novo de cada etapa com efeito buscada (chave presente = buscada; None = sem pedido).
    pedidos: dict[str, Approval | None]


@dataclass(frozen=True)
class PedidoDeOperacao:
    command: str
    app_id: str
    alvos: Sequence[AlvoPedido]
    acao_final: str
    idempotency_key: str
    max_usd: float
    assunto: str | None = None
    fontes: Sequence[str] = ()
    #: Adendo v1.95: parâmetros FIXOS de cada execução de alvo (`username`, `caption_contains`), com estes nomes no
    #: plano (`taskqueue/plano_da_operacao.py`), para a receita ensinada casar.
    parametros: Mapping[str, str] | None = None


#: Nome de parâmetro fixo: o alfabeto das chaves do plano. Os que a materialização põe por cima dos parâmetros
#: (`instance_id`, `run_id`, `account_label`, `item`) e os dados da persona (`perfil_*`, `conta_*`) seriam engolidos.
_NOME_DE_PARAMETRO = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


def _conferir_parametros(parametros: Mapping[str, str] | None) -> None:
    """Nome e valor de cada parâmetro fixo. Credencial nunca: a execução não carrega credencial (ADR-040), e um parâmetro
    vai ao plano, ao objetivo, ao prompt e ao texto digitado pelo canal comum. A recusa olha o NOME (`senha`, `codigo`,
    `token`), o par `nome=valor` e o FORMATO do valor sozinho (senha ou código sem rótulo).

    A recusa diz a POSIÇÃO do parâmetro, nunca o nome nem o valor (achado do Copilot no PR 487): a credencial pode
    estar no próprio nome, e o corpo do erro volta ao cliente e vai ao log."""
    for posicao, (nome, valor) in enumerate((parametros or {}).items(), start=1):
        texto = str(valor)
        if (chave_sensivel(nome) or SENSITIVE_PARAM.search(nome) or redact(f"{nome}={texto}") != f"{nome}={texto}"
                or looks_secret(texto) or parece_senha_ou_codigo(texto)):
            raise OperacaoError("credencial_no_comando", f"O {posicao}º parâmetro parece credencial; a operação não "
                                "leva credencial (a senha só sai do cofre, pelo canal sensível).", 409)
        if (not _NOME_DE_PARAMETRO.match(nome) or nome in NOMES_RESERVADOS
                or nome.startswith(("perfil_", "conta_"))):
            raise OperacaoError("pedido_invalido", f"Nome do {posicao}º parâmetro não aceito (minúsculas, dígitos e _,"
                                " até 40; sem os nomes reservados nem perfil_ e conta_).", 422)
        if not isinstance(valor, str) or not 1 <= len(valor) <= 300 or "{" in valor or "}" in valor:
            raise OperacaoError("pedido_invalido", f"Valor do {posicao}º parâmetro: de 1 a 300 caracteres, sem chaves.",
                                422)
    if len(parametros or {}) > 10:
        raise OperacaoError("pedido_invalido", "No máximo 10 parâmetros.", 422)
    valores = [normal(str(v)) for v in (parametros or {}).values()]
    if len(set(valores)) != len(valores):
        # Dois nomes para o mesmo valor deixariam a identidade da etapa dependente da ordem da troca.
        raise OperacaoError("pedido_invalido", "Dois parâmetros com o mesmo valor.", 422)


def _conferir_contra_o_app(parametros: Mapping[str, str] | None, pacote: str) -> None:
    """31.224 (adendo v1.121) e 31.227: o parâmetro fixo que não casa com o app é recusado ANTES de qualquer execução,
    para que um erro de digitação na prova não custe chamada paga. Sem catálogo, só a conferência genérica (o teto de
    300). Com catálogo, a chave tem de ser uma que as ações usam (a recusa traz a lista dos aceitos, que vem do catálogo,
    não do pedido), e o parâmetro que o catálogo declara (`parametros` no YAML) segue a forma e o tamanho dele: `handle`
    vai sem arroba e sem espaço. Como em `_conferir_parametros`, a recusa diz a POSIÇÃO (`posicao`, 1 = o primeiro de
    `parametros`), nunca o nome que veio; `campo` só sai quando o nome é um declarado pelo app."""
    if not parametros:
        return
    catalogo = apps_registrados.get(pacote)
    if catalogo is None:
        return
    usados: set[str] = set()
    for cap in catalogo.capabilities:
        usados.update(cap.bindings, cap.optional_bindings, cap.inherited_bindings)
    aceitos = sorted(usados)
    for posicao, (nome, valor) in enumerate(parametros.items(), start=1):
        if nome not in usados:
            raise OperacaoError("pedido_invalido", f"O {posicao}º parâmetro não é aceito por este app; aceitos: "
                                f"{', '.join(aceitos)}.", 422, motivo="parametro_desconhecido", posicao=posicao,
                                aceitos=aceitos)
        decl = catalogo.parametros.get(nome)
        if decl is None:
            continue
        if decl.forma == "handle" and "@" in valor:
            raise OperacaoError("pedido_invalido", f"O {posicao}º parâmetro ({nome}) vai sem arroba.", 422,
                                motivo=f"{nome}_com_arroba", posicao=posicao, campo=nome)
        if decl.forma == "handle" and any(c.isspace() for c in valor):
            raise OperacaoError("pedido_invalido", f"O {posicao}º parâmetro ({nome}) vai sem espaço.", 422,
                                motivo=f"{nome}_com_espaco", posicao=posicao, campo=nome)
        if len(valor) > decl.max:
            raise OperacaoError("pedido_invalido", f"O {posicao}º parâmetro ({nome}) tem no máximo {decl.max} "
                                "caracteres neste app.", 422, motivo=f"{nome}_longo", posicao=posicao, campo=nome,
                                max=decl.max)


def _sha(pedido: PedidoDeOperacao) -> str:
    corpo = {"command": pedido.command.strip(), "app_id": pedido.app_id, "acao_final": pedido.acao_final,
             "max_usd": pedido.max_usd, "assunto": pedido.assunto, "fontes": list(pedido.fontes),
             "alvos": [[a.profile_id, a.account_id, a.instance_id] for a in pedido.alvos]}
    if pedido.parametros:
        # Só quando há: a chave de uma operação anterior ao v1.95, mandada de novo, segue casando.
        corpo["parametros"] = dict(pedido.parametros)
    return hashlib.sha256(json.dumps(corpo, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class ServicoDeOperacoes:
    def __init__(self, db: Database, runs: RunService, social: SocialRepository, aprovacoes: ApprovalService,
                 limites: Callable[[], LimitsCfg], bus: EventBus, precos: dict[str, list[float]],
                 pesquisa: PesquisaCfg | None = None) -> None:
        self.db, self.runs, self.social, self.aprovacoes = db, runs, social, aprovacoes
        self.limites, self.bus, self.precos = limites, bus, precos
        self._lote: _Lote | None = None
        #: Desligar só serve ao teste que prova que a leitura em lote devolve o mesmo que a de um alvo por vez.
        self.com_lote = True
        #: 31.235: a configuração da pesquisa externa (ligada e o mínimo de fatos do Livro), só para o resumo do GET
        self.pesquisa = pesquisa

    # ------------------------------------------------------------------ criar
    def criar(self, pedido: PedidoDeOperacao, *, quem: str | None = None) -> dict[str, object]:
        try:
            # A MESMA recusa da execução, antes de gravar, para tudo que vai ao banco e à pesquisa externa.
            for texto in (pedido.command, pedido.assunto or "", *pedido.fontes, *(pedido.parametros or {}).values()):
                self.runs._recusar_credencial(texto)  # noqa: SLF001
        except RunError as exc:
            raise OperacaoError(exc.code, exc.message, exc.status) from exc
        _conferir_parametros(pedido.parametros)
        app = self.db.one("SELECT id, package FROM apps WHERE id=?", (pedido.app_id,))
        if app is None:
            raise OperacaoError("app_inexistente", f"O app {pedido.app_id!r} não está registrado.", 404)
        sha = _sha(pedido)
        existente = self.db.one("SELECT id, corpo_sha256 FROM operacoes WHERE idempotency_key=?",
                                (pedido.idempotency_key,))
        if existente is not None:
            if existente["corpo_sha256"] != sha:
                raise OperacaoError("chave_em_uso", "Esta chave de idempotência já criou outra operação.", 409,
                                    operacao_id=existente["id"])
            return self.ler(str(existente["id"]))
        # Depois da repetição (achados do Codex nos PRs 495 e 503): a operação aceita antes desta regra, ou antes de o
        # catálogo mudar, mandada de novo com o mesmo corpo e a mesma chave, devolve a que existe, e não um 422.
        _conferir_contra_o_app(pedido.parametros, str(app["package"] or ""))
        op_id = f"op-{now_iso()[:19].replace('-', '').replace(':', '').replace('T', '')}-{secrets.token_hex(3)}"
        agora = now_iso()
        self.db.execute(
            "INSERT INTO operacoes(id, command, app_id, acao_final, max_usd, assunto, fontes, status, idempotency_key,"
            " corpo_sha256, criada_por, created_at, updated_at, parametros) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (op_id, pedido.command.strip(), pedido.app_id, pedido.acao_final, float(pedido.max_usd), pedido.assunto,
             dumps(list(pedido.fontes)), "em_curso", pedido.idempotency_key, sha, quem, agora, agora,
             dumps(dict(pedido.parametros)) if pedido.parametros else None))
        for seq, alvo in enumerate(pedido.alvos):
            self._criar_alvo(op_id, seq, alvo, pedido)
        self.bus.emit("operacao.criada", f"Operação {op_id} criada com {len(pedido.alvos)} alvo(s).",
                      data={"operacao_id": op_id, "solicitados": len(pedido.alvos)})
        return self.ler(op_id)

    def _criar_alvo(self, op_id: str, seq: int, alvo: AlvoPedido, pedido: PedidoDeOperacao) -> None:
        parada, motivo, conta, aparelho = self._conferir(alvo, pedido.app_id)
        run_id: str | None = None
        if parada is None and self._gasto(op_id) >= pedido.max_usd:
            parada, motivo = "aparelho", TETO_DE_CUSTO
        if parada is None:
            # O id da persona vai por hash: cortado, dois ids de prefixo igual cairiam na mesma execução.
            chave = f"{PREFIXO_OPERACAO}{op_id}:{hashlib.sha256(alvo.profile_id.encode()).hexdigest()[:24]}"
            try:
                resumo = self.runs.create(RunCreate(
                    command=pedido.command, idempotency_key=chave, teto_de_autonomia=self._teto(alvo, pedido),
                    targets=[RunTarget(profile_id=alvo.profile_id, instance_ids=[aparelho or ""],
                                       app_id=pedido.app_id)]))
                run_id = resumo.id
                # Sem `await` entre criar e marcar: o planejamento só começa na próxima volta do laço, e quem lê
                # `runs.operacao_id` (teto de custo, conhecimento da operação) já o encontra.
                self.db.execute("UPDATE runs SET operacao_id=? WHERE id=?", (op_id, run_id))
            except RunError as exc:
                parada, motivo = "aparelho", _motivo(f"{SEM_APARELHO}: {exc.message}")
        self.db.execute(
            "INSERT INTO operacao_alvos(operacao_id, seq, profile_id, account_id, instance_id, run_id, estagio, estado,"
            " motivo, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (op_id, seq, alvo.profile_id, conta, aparelho, run_id, parada or "sessao",
             "bloqueado" if parada else "pendente", motivo, now_iso()))

    def _teto(self, alvo: AlvoPedido, pedido: PedidoDeOperacao) -> Literal["preparar", "agir"]:
        """31.253 / ADR-082: `agir` para a persona do grupo sem aprovação numa operação que executa (decisão do dono em
        07/10); `preparar` (28.23) para as demais. As recusas, a frota, os tetos e a proteção de conta seguem na porta."""
        cfg = self.limites()
        grupo = str(getattr(cfg, "grupo_sem_aprovacao", "") or "").strip()
        if pedido.acao_final != "executar" or not grupo or not getattr(cfg, "operacao_grupo_liberado_executa", False):
            return "preparar"
        persona = self.social.persona_row(alvo.profile_id)
        return "agir" if persona is not None and persona["policy_group_id"] == grupo else "preparar"

    def _conferir(self, alvo: AlvoPedido, app_id: str) -> tuple[str | None, str | None, str | None, str | None]:
        """Persona → conta → sessão → aparelho, na ordem do dono. Devolve (parada, motivo, conta, aparelho)."""
        if self.social.persona_row(alvo.profile_id) is None:
            return "persona", SEM_PERSONA, None, None
        if alvo.account_id:
            conta = self.social.account_row(alvo.profile_id, alvo.account_id)
            if conta is not None and conta["app_id"] != app_id:
                conta = None
        else:
            conta = self.social.account_by_app(alvo.profile_id, app_id)
        if conta is None:
            return "conta", SEM_CONTA, None, None
        if conta["status"] != "active":
            return "conta", _motivo(f"conta {conta['status']}"), str(conta["id"]), None
        sessoes = self._sessoes_prontas(str(conta["id"]))
        if alvo.instance_id is None and len(sessoes) > 1:
            # A conta com sessão em dois aparelhos (a mesma conta lida no notebook e logada no central) executa só no
            # vínculo PRINCIPAL da persona: a sessão mais recente podia ser a do aparelho que só lê.
            aparelho = self._principal_com_sessao(alvo.profile_id, sessoes)
            if aparelho is None:
                return "sessao", FORA_DO_PRINCIPAL, str(conta["id"]), None
        else:
            aparelho = alvo.instance_id or (sessoes[0] if sessoes else None)
        if aparelho is None or aparelho not in sessoes:
            # 31.207 (J0, ADR-080): o app que DECLARA a troca de conta atende N personas no mesmo aparelho, uma depois da
            # outra; só uma conta está aberta por vez, e a porta de sessão da execução troca para a esperada (31.155).
            # Basta a persona servir ao app no aparelho. App sem a declaração (o Instagram) segue exigindo a sessão.
            pela_troca = self._aparelho_pela_troca(alvo, str(conta["id"]), app_id)
            if pela_troca is None:
                return "sessao", SEM_SESSAO, str(conta["id"]), alvo.instance_id
            aparelho = pela_troca
        rt = self.runs.devices.devices.get(aparelho)
        if rt is None or rt.store:
            return "aparelho", SEM_APARELHO, str(conta["id"]), aparelho
        return None, None, str(conta["id"]), aparelho

    def _aparelho_pela_troca(self, alvo: AlvoPedido, conta_id: str, app_id: str) -> str | None:
        """O aparelho em que a persona entra pela troca declarada (31.207): o pedido, ou o principal dela, desde que ela
        possa entrar nele (`_pode_entrar_pela_troca`). None quando o app não declara a troca ou ela não pode."""
        if not troca_declarada(self.db, app_id):
            return None
        alvo_do_pedido = alvo.instance_id
        if alvo_do_pedido is None:
            principal = self.social.binding_principal(alvo.profile_id)
            alvo_do_pedido = str(principal["instance_id"]) if principal is not None else None
        if alvo_do_pedido is None or not self._pode_entrar_pela_troca(alvo.profile_id, conta_id, alvo_do_pedido,
                                                                       app_id):
            return None
        return alvo_do_pedido

    def _pode_entrar_pela_troca(self, profile_id: str, conta_id: str, instance_id: str, app_id: str) -> bool:
        """Os pré-requisitos da porta de sessão que se conferem sem tocar no aparelho (achado do Codex no PR 493,
        `sessao.py` `_antes_de_sair` e `_needs_person`): a persona serve ao app no aparelho, a conta tem senha guardada,
        ativa e com o consentimento para a automação digitá-la (ADR-040), e a sessão dela ali não parou num desafio nem
        em conta errada. Sem isso a execução nasceria para ficar bloqueada, e a capacidade contaria quem não executa."""
        if not self._serve_o_app(profile_id, instance_id, app_id):
            return False
        cred = self.social.account_credential_row(profile_id, conta_id)
        if cred is None or cred["consent_at"] is None or cred["status"] != "active":
            return False
        parada = self.db.scalar("SELECT status FROM account_sessions WHERE account_id=? AND instance_id=?",
                                (conta_id, instance_id))
        return parada not in (SessionStatus.auth_challenge.value, SessionStatus.wrong_account.value)

    def _serve_o_app(self, profile_id: str, instance_id: str, app_id: str) -> bool:
        """A persona serve ao app no aparelho: o vínculo daquele app, ou o vínculo sem app de quem tem conta nele (a
        mesma leitura de `profiles_of_instance`, que decide qual persona do aparelho a tarefa usa)."""
        return any(str(v["profile_id"]) == profile_id for v in self.social.profiles_of_instance(instance_id, app_id))

    def _principal_com_sessao(self, profile_id: str, sessoes: list[str]) -> str | None:
        principal = self.social.binding_principal(profile_id)
        iid = str(principal["instance_id"]) if principal is not None else None
        return iid if iid in sessoes else None

    def _sessoes_prontas(self, account_id: str) -> list[str]:
        if self._lote is not None:
            return list(self._lote.sessoes.get(account_id, []))
        return [str(r["instance_id"]) for r in self.db.query(
            "SELECT instance_id FROM account_sessions WHERE account_id=? AND status=? ORDER BY updated_at DESC,"
            " instance_id", (account_id, SessionStatus.session_ready.value))]

    def _custo(self, op_id: str) -> dict[str, float]:
        """O custo da operação, com a pesquisa externa SEPARADA (frente de aprendizado): ela roda dentro da execução de
        um alvo, então as linhas dela também têm o `run_id` dele, e somar sem tirar contaria duas vezes."""
        runs = [str(r["id"]) for r in self.db.query("SELECT id FROM runs WHERE operacao_id=?", (op_id,))]
        if self._lote is not None and set(runs) <= set(self._lote.runs):
            total = sum(self._lote.gasto.get(r, 0.0) for r in runs)
            pesquisa = sum(self._lote.pesquisa.get(r, 0.0) for r in runs)
        else:
            total = sum(costs.spent_usd(self.db, self.precos, run_id=r) for r in runs)
            pesquisa = sum(costs.spent_usd(self.db, self.precos, run_id=r, origem="pesquisa") for r in runs)
        return {"pesquisa_usd": round(pesquisa, 4), "alvos_usd": round(total - pesquisa, 4), "total_usd": round(total, 4)}

    def _gasto(self, op_id: str) -> float:
        return sum(costs.spent_usd(self.db, self.precos, run_id=str(r["id"]))
                   for r in self.db.query("SELECT id FROM runs WHERE operacao_id=?", (op_id,)))

    # ------------------------------------------------------------------ ler
    def listar(self, limite: int = 50, *, profile_id: str | None = None,
               instance_id: str | None = None) -> dict[str, object]:
        """As operações mais recentes. Com `profile_id` e/ou `instance_id` (31.213, adendo v1.116), só as que têm alvo
        daquela persona e/ou daquele aparelho, cada uma com o resumo DESSES alvos (`alvos`): o histórico da persona
        sem ler o detalhe das 20 mais recentes. Sem filtro, a lista de sempre, sem `alvos`."""
        filtros, params = [], []
        if profile_id:
            filtros.append("a.profile_id=?")
            params.append(profile_id)
        if instance_id:
            filtros.append("a.instance_id=?")
            params.append(instance_id)
        if filtros:
            linhas = self.db.query(
                "SELECT o.id FROM operacoes o WHERE EXISTS (SELECT 1 FROM operacao_alvos a WHERE a.operacao_id=o.id AND "
                + " AND ".join(filtros) + ") ORDER BY o.created_at DESC, o.id DESC LIMIT ?", (*params, int(limite)))
        else:
            linhas = self.db.query("SELECT id FROM operacoes ORDER BY created_at DESC, id DESC LIMIT ?", (int(limite),))
        itens = []
        for r in linhas:
            d = self.ler(str(r["id"]))
            item = {k: d[k] for k in ("id", "command", "app_id", "acao_final", "status", "created_at",
                                      "finished_at", "capacidade")}
            if filtros:
                item["alvos"] = [_resumo_do_alvo(a) for a in d["alvos"]  # type: ignore[attr-defined]
                                 if (not profile_id or a["profile_id"] == profile_id)
                                 and (not instance_id or a["instance_id"] == instance_id)]
            itens.append(item)
        return {"items": itens}

    def ler(self, op_id: str) -> dict[str, object]:
        """A operação lida agora. Grava só o que mudou desde a leitura anterior (estágio, reabertura, fechamento e os
        eventos deles), e a mesma leitura repetida não grava nada (31.216): o painel e a Canais podem ler à vontade."""
        op = self.db.one("SELECT * FROM operacoes WHERE id=?", (op_id,))
        if op is None:
            raise OperacaoError("operacao_inexistente", "Operação não encontrada.", 404)
        definicao = self._definicao(str(op["app_id"]))
        limite = int(self.limites().operacao_max_acoes_executadas)
        executadas = self._acoes_comprometidas(op_id)
        aprovados = self._runs_com_acao_aprovada(op_id)
        if aprovados and op["acao_final"] == "preparar" and op["status"] != "cancelada":
            # A ação aprovada POR FORA do liberar (Pendências, Telegram: a onda 1 de 06/10) vai rodar. A operação passa a
            # `executar` e reabre, como no liberar; sem isto, ficava `concluida` com o `finished_at` da preparação e a
            # ação executada e verificada depois dele. A reabertura vem ANTES da leitura dos alvos (achado do Codex no
            # PR 483): lidos com `preparar`, `acao_preparada` era concluído, e o mesmo GET fechava a operação de novo.
            # Transição condicional no SQL, como no liberar (achado do Copilot no PR 487): o cancelar que gravou
            # `cancelada` depois da leitura acima não pode ser sobrescrito por `em_curso`.
            self.db.execute("UPDATE operacoes SET acao_final='executar', status='em_curso', finished_at=NULL, updated_at=?"
                            " WHERE id=? AND acao_final='preparar' AND status<>'cancelada'", (now_iso(), op_id))
            op = self.db.one("SELECT * FROM operacoes WHERE id=?", (op_id,)) or op
        linhas = self.db.query("SELECT * FROM operacao_alvos WHERE operacao_id=? ORDER BY seq, profile_id", (op_id,))
        self._lote = self._montar_lote(linhas) if self.com_lote else None
        try:
            return self._ler_com_lote(op, linhas, definicao, limite, executadas, aprovados)
        finally:
            self._lote = None

    def _ler_com_lote(self, op: Row, linhas: list[Row], definicao: tuple[str, dict[str, str]], limite: int,
                      executadas: int, aprovados: set[object]) -> dict[str, object]:
        op_id = str(op["id"])
        alvos = []
        for a in linhas:
            leitura, resultado = self._ler_alvo(op, a, definicao)
            alvos.append((a, leitura, resultado))
        saida = []
        # 31.229 (adendo v1.124): o custo e o modelo por passo de cada alvo, numa leitura só para todos.
        por_passo = custo_por_passo.por_execucao(self.db, self.precos, [str(a["run_id"]) for a in linhas
                                                                         if a["run_id"]], definicao[1])
        perguntas = self._perguntas_abertas([str(a["run_id"]) for a in linhas if a["run_id"]])
        for a, lt, resultado in alvos:
            estado, motivo, parou = lt.estado, lt.motivo, lt.parou_em
            # Só o alvo SEM a ação aprovada aguarda liberação; o liberado segue o estado da execução dele (em curso
            # enquanto executa, bloqueado se falhar). Marcá-lo bloqueado fecharia a operação antes da execução.
            if (lt.estagio == "acao_preparada" and op["acao_final"] == "executar" and estado != "concluido"
                    and a["run_id"] not in aprovados):
                estado, motivo, parou = ("bloqueado", LIMITE_DE_ACOES if executadas >= limite else AGUARDA_LIBERACAO,
                                         "acao_executada")
            self._anotar(op_id, a, lt.estagio, estado, motivo)
            # O custo do alvo é o da execução DELE (com a pesquisa externa, se ela rodou ali); sem execução, nulo.
            custo = round(self._gasto_do_run(str(a["run_id"])), 4) if a["run_id"] else None
            if resultado is not None:
                resultado = {**resultado, "custo_usd": custo}
            # A latência do alvo (métrica de primeira classe do dono): a etapa de cada estágio e a espera pelo liberar à
            # parte, para a espera pela pessoa não entrar como latência da ação (`domain/latencia.py`).
            lat = latencia.do_alvo(lt.estagios, str(op["created_at"]), lt.liberado_em, definicao[0])
            saida.append({"profile_id": a["profile_id"], "persona_nome": self._nome(str(a["profile_id"])),
                          "app_id": op["app_id"], "account_id": a["account_id"], "conta": self._handle(a),
                          "instance_id": a["instance_id"], "run_id": a["run_id"], "estagio": lt.estagio,
                          "estado": estado, "motivo": motivo,
                          "parou_em": parou if estado in ("bloqueado", "cancelado") else None,
                          "estagios": [{"estagio": e, "em": em, "etapa_ms": ms}
                                       for (e, em), ms in zip(lt.estagios, lat.etapas_ms, strict=True)],
                          "latencia": {"duracao_ms": lat.duracao_ms, "espera_do_liberar_ms": lat.espera_do_liberar_ms},
                          "resultado": resultado, "custo_usd": custo,
                          "sessao_verificada_em": self._sessao_verificada_em(a),
                          "custo_por_passo": por_passo.get(str(a["run_id"])) if a["run_id"] else None,
                          # ADR-083: sem o espaçamento da frota nada represa o alvo; o campo do adendo v1.126 fica
                          # sempre nulo (o painel do 31.264 já trata nulo como "sem retomada").
                          "retomada_em": None,
                          "aguarda_resposta": perguntas.get(str(a["run_id"])) if a["run_id"] else None})
        self._anotar_filas(saida)
        capacidade = self._capacidade(saida)
        # Pedido do Portal (onda 2 de 07/10): o selo "N agentes aguardam resposta" sem contar pelos alvos na tela.
        capacidade["aguardando_resposta"] = sum(1 for a in saida if a["aguarda_resposta"] is not None)
        status = self._status(op, saida)
        custo_por_modelo, custo_por_estagio = custo_por_passo.somar(
            a["custo_por_passo"] for a in saida)
        custo_da_operacao = self._custo(op_id)
        return {"id": op["id"], "command": op["command"], "app_id": op["app_id"], "acao_final": op["acao_final"],
                "max_usd": op["max_usd"], "assunto": op["assunto"], "fontes": loads(op["fontes"], []),
                "parametros": loads(op["parametros"], None), "fontes_da_pesquisa": self._fontes_da_pesquisa(op_id),
                "status": status, "created_at": op["created_at"],
                "finished_at": self._fechar(op, status, capacidade, fim=self._fim_real(saida)),
                "capacidade": capacidade, "alvos": saida, "custo": custo_da_operacao,
                "pesquisa": self._pesquisa(op, custo_da_operacao["pesquisa_usd"]),
                "latencia_por_estagio": latencia.por_estagio(saida),
                "custo_por_modelo": custo_por_modelo, "custo_por_estagio": custo_por_estagio}

    def _pesquisa(self, op: Row, custo_usd: float) -> dict[str, object] | None:
        """31.235: o resumo da pesquisa externa (reaproveitada do Livro, paga, falhou ou não rodou), da memória da
        operação. Sem assunto, a operação não pediu pesquisa: `None`. Sem texto de fato nem URL."""
        try:
            entradas = RepositorioDeMemoria(self.db).entradas_da_operacao(str(op["id"]))
        except OPERATIONAL_ERRORS as exc:   # banco sem a coluna `operacao_id` da memória: a pesquisa não gravou nada
            if not coluna_ausente(exc):
                raise
            entradas = []
        cfg = self.pesquisa
        return resumo_da_pesquisa.resumo(entradas, pediu=bool((op["assunto"] or "").strip()),
                                         ligada=bool(cfg and cfg.enabled),
                                         minimo_fatos=cfg.reaproveitar_min_fatos if cfg else 0, custo_usd=custo_usd)

    def relatorio(self, op_id: str, aprendizado: Mapping[str, object] | None) -> dict[str, object]:
        """O relatório consolidado (31.195, adendo v1.111): o GET da operação (com a latência do v1.108) arrumado para a
        Canais e a Portal lerem o mesmo, mais o aprendizado da operação (v1.96) inteiro, como veio. `aprendizado` None =
        indisponível (a memória da operação não está no banco). Só leitura, sem IA."""
        op = self.ler(op_id)
        alvos = [a for a in op["alvos"] if isinstance(a, dict)]  # type: ignore[attr-defined]
        agentes = []
        verificadas = 0
        for a in alvos:
            res_lido, lat_lida = a.get("resultado"), a.get("latencia")
            res: dict[str, object] | None = res_lido if isinstance(res_lido, dict) else None
            acao_lida = res.get("acao_final") if res is not None else None
            acao: dict[str, object] | None = acao_lida if isinstance(acao_lida, dict) else None
            lat: dict[str, object] = lat_lida if isinstance(lat_lida, dict) else {}
            verificadas += 1 if acao is not None and rel.conferencia(res) == "sim" else 0
            agentes.append({
                "profile_id": a["profile_id"], "persona": a.get("persona_nome") or "uma persona",
                "aparelho": a.get("instance_id"), "estado": a["estado"], "estagio": a["estagio"],
                "parou_em": a.get("parou_em"), "motivo": rel.sem_arroba(a.get("motivo")),
                "estagios": [{"estagio": e["estagio"], "em": e["em"], "etapa_ms": e.get("etapa_ms")}
                             for e in a.get("estagios") or []],
                "texto": rel.sem_arroba(res.get("texto")) if res is not None else None,
                "conhecimento_ids": res.get("conhecimento_ids") if res is not None else None,
                "acao_final": ({"tipo": acao.get("tipo"), "verificada": rel.conferencia(res),
                                "evidencia_id": acao.get("evidencia_id")} if acao is not None else None),
                "custo_usd": a.get("custo_usd"),
                "duracao_ms": lat.get("duracao_ms"), "espera_do_liberar_ms": lat.get("espera_do_liberar_ms")})
        cap = op["capacidade"] if isinstance(op["capacidade"], dict) else {}
        custo = op["custo"] if isinstance(op["custo"], dict) else {}
        total = custo.get("total_usd")
        provedores = {str(r["p"]) for r in self.db.query(
            "SELECT DISTINCT COALESCE(provider,'') p FROM ai_calls WHERE run_id IN (SELECT id FROM runs WHERE"
            " operacao_id=?)", (op_id,))}
        ambiente = ("nao_medido" if not provedores else
                    "simulado" if provedores <= {"simulated"} else "real")
        return {
            "gerado_em": now_iso(), "ambiente": ambiente,
            "operacao": {"id": op["id"], "comando": op["command"], "app_id": op["app_id"],
                         "acao_final": op["acao_final"], "status": op["status"], "criada_em": op["created_at"],
                         "encerrada_em": op["finished_at"], "assunto": op["assunto"], "fontes": op["fontes"],
                         "fontes_da_pesquisa": op["fontes_da_pesquisa"]},
            # No GET os motivos são {motivo: n}; aqui, a lista do relatório da Portal ({motivo, n}, o maior primeiro).
            "capacidade": {**cap, "motivos": [{"motivo": rel.sem_arroba(m), "n": n} for m, n in sorted(
                (cap.get("motivos") or {}).items(), key=lambda kv: (-int(kv[1]), str(kv[0])))]},
            "identidades": rel.identidades(cap),
            # O resumo da pesquisa do GET (31.235): sem texto de fato nem URL; o critério passa pelo sem_arroba.
            "pesquisa": ({**pesquisa, "criterio": rel.sem_arroba(pesquisa.get("criterio"))}
                         if isinstance(pesquisa := op.get("pesquisa"), dict) else None),
            "agentes": agentes,
            "falhas_por_motivo": rel.falhas_por_motivo(agentes),
            "textos": rel.textos(agentes),
            "criterios": rel.criterios(op, agentes, ambiente=ambiente, aprendizado_disponivel=aprendizado is not None),
            "criterios_base": rel.FONTE_DA_BASE,
            "aprendizado": dict(aprendizado) if aprendizado is not None else {
                "disponivel": False, "motivo": "a memória da operação não está no banco"},
            "latencia": rel.latencia(alvos, op.get("latencia_por_estagio")),
            "custo": {"pesquisa_usd": custo.get("pesquisa_usd"), "alvos_usd": custo.get("alvos_usd"),
                      "total_usd": total, "teto_usd": op["max_usd"],
                      "por_peca_usd": (round(float(total) / verificadas, 4)
                                       if verificadas and isinstance(total, (int, float)) else None)},
        }

    def _perguntas_abertas(self, run_ids: Sequence[str]) -> dict[str, dict[str, object]]:
        """{execução: {pergunta, desde}} das execuções de alvo em `needs_input` (pedido do Portal: na onda 2 de 07/10 as
        três esperavam o @ da página e a tela dizia "Em andamento"). A pergunta é o texto da execução, redigido e
        curto; `desde` é a última mudança de estado dela (a entrada em `needs_input`, como no vencimento do 29.50)."""
        if not run_ids:
            return {}
        marcas = ",".join("?" * len(run_ids))
        abertas = {str(r["id"]): str(r["status_detail"] or "") for r in self.db.query(
            f"SELECT id, status_detail FROM runs WHERE id IN ({marcas}) AND status=?",
            (*run_ids, RunStatus.needs_input.value))}
        if not abertas:
            return {}
        quando = {str(r["run_id"]): r["desde"] for r in self.db.query(
            f"SELECT run_id, MAX(ts) AS desde FROM events WHERE kind='run.updated' AND run_id IN"
            f" ({','.join('?' * len(abertas))}) GROUP BY run_id", tuple(abertas))}
        return {rid: {"pergunta": (redact(texto) or "")[:PERGUNTA_MAX].rstrip(),
                      "desde": str(quando[rid]) if quando.get(rid) else None} for rid, texto in abertas.items()}

    def _sessao_verificada_em(self, a: Row) -> str | None:
        """31.173: quando a sessão da conta do alvo NESTE aparelho foi vista na tela pela última vez (`account_sessions`).
        A porta do despacho relê a vencida antes da tarefa; aqui é o que a pessoa olha antes de começar."""
        if not a["account_id"] or not a["instance_id"]:
            return None
        linha = self.db.one("SELECT verified_at FROM account_sessions WHERE account_id=? AND instance_id=?",
                            (a["account_id"], a["instance_id"]))
        return str(linha["verified_at"]) if linha is not None and linha["verified_at"] else None

    def _fontes_da_pesquisa(self, op_id: str) -> list[str]:
        """As URLs que a pesquisa externa da operação ACHOU (frente de aprendizado, migração 125: `pedido_observacoes` com
        `tipo='url'`). `fontes` é o que o pedido trouxe de entrada; sem isto, o GET mostrava 0 fontes com pesquisa paga."""
        vistas: list[str] = []
        try:
            linhas = self.db.query("SELECT valor FROM pedido_observacoes WHERE operacao_id=? AND tipo='url' AND valor IS"
                                   " NOT NULL ORDER BY capturado_em, id", (op_id,))
        except OPERATIONAL_ERRORS as exc:   # banco sem a migração 125 (a coluna `operacao_id`): a pesquisa não gravou nada
            if not coluna_ausente(exc):
                raise
            return vistas
        for r in linhas:
            if str(r["valor"]) not in vistas:
                vistas.append(str(r["valor"]))
        return vistas

    def _definicao(self, app_id: str) -> tuple[str, dict[str, str]]:
        row = self.db.one("SELECT package FROM apps WHERE id=?", (app_id,))
        d = definition_of(row["package"] if row is not None else None)
        return d.operation_opening, dict(d.operation_stages)

    def _ler_alvo(self, op: Row, a: Row, definicao: tuple[str, dict[str, str]]) -> tuple[Leitura, dict[str, object] | None]:
        abertura, por_cap = definicao
        marcas = loads(a["marcas"], {}) or {}
        parada = a["estagio"] if a["run_id"] is None else None
        obj = run = None
        etapas: list[EtapaLida] = []
        efeito: Row | None = None
        if a["run_id"]:
            lote = self._lote
            if lote is not None and str(a["run_id"]) in lote.runs:
                run, obj = lote.runs[str(a["run_id"])], lote.objetivos.get(str(a["run_id"]))
            else:
                run = self.db.one("SELECT status, status_detail, finished_at FROM runs WHERE id=?", (a["run_id"],))
                obj = self.db.one("SELECT * FROM objectives WHERE run_id=? ORDER BY id LIMIT 1", (a["run_id"],))
            if obj is not None:
                lote_etapas = self._lote.etapas if self._lote is not None else {}
                linhas_de_etapa = (lote_etapas[str(obj["id"])] if str(obj["id"]) in lote_etapas else
                                   self.db.query("SELECT * FROM steps WHERE objective_id=? AND plan_version=? ORDER BY"
                                                 " seq, id", (obj["id"], obj["plan_version"])))
                for s in linhas_de_etapa:
                    lote_pedidos = self._lote.pedidos if self._lote is not None else {}
                    if not s["side_effect"]:
                        pedido = None
                    elif str(s["id"]) in lote_pedidos:
                        pedido = lote_pedidos[str(s["id"])]
                    else:
                        pedido = self.aprovacoes.store.for_step(str(s["id"]))
                    texto = (loads(s["bindings"], {}) or {}).get("content") if s["side_effect"] else None
                    etapas.append(EtapaLida(
                        capability=s["capability"], status=str(s["status"]), side_effect=bool(s["side_effect"]),
                        terminou_em=s["finished_at"], comecou_em=s["started_at"], tem_texto=bool(texto),
                        verificada=bool((loads(s["result"], {}) or {}).get("verified")),
                        pedido_de_aprovacao=pedido.status if pedido is not None else None,
                        pedido_em=getattr(pedido, "created_at", None) if pedido is not None else None))
                    if s["side_effect"] and efeito is None:
                        efeito = s
        leitura = derivar(FatosDoAlvo(
            parada_na_criacao=parada, motivo_na_criacao=a["motivo"] if parada else None,
            objetivo_status=str(obj["status"]) if obj is not None else None,
            objetivo_comecou_em=obj["started_at"] if obj is not None else None,
            objetivo_motivo=_motivo((obj["blocked_reason"] or obj["status_detail"]) if obj is not None else None),
            run_status=str(run["status"]) if run is not None else None, etapas=etapas, marcas=marcas,
            abertura=abertura, estagio_por_capability=por_cap, acao_final=str(op["acao_final"]),
            criado_em=str(op["created_at"]), objetivo_bloqueio=obj["blocked_kind"] if obj is not None else None,
            recusa_no_plano=(_motivo(run["status_detail"]) or "recusada no planejamento")
            if obj is None and run is not None and run["status"] == "failed" else None,
            recusa_em=run["finished_at"] if run is not None else None))
        return leitura, self._resultado(a, efeito, marcas)

    def _resultado(self, a: Row, efeito: Row | None, marcas: dict[str, object]) -> dict[str, object] | None:
        if efeito is None:
            return None
        texto = (loads(efeito["bindings"], {}) or {}).get("content")
        if not texto:
            return None
        captura = self.db.one("SELECT id FROM evidence WHERE run_id=? AND step_id<>? ORDER BY ts DESC, id DESC LIMIT 1",
                              (a["run_id"], efeito["id"]))
        prova = self.db.one("SELECT id FROM evidence WHERE step_id=? ORDER BY ts DESC, id DESC LIMIT 1", (efeito["id"],))
        verificada = efeito["status"] == "succeeded" and bool((loads(efeito["result"], {}) or {}).get("verified"))
        ids = marcas.get("conhecimento_ids") if isinstance(marcas.get("conhecimento_ids"), list) else []
        return {"texto": str(texto),
                "conhecimento_ids": ids, "evidencia_id": captura["id"] if captura is not None else None,
                "acao_final": {"tipo": efeito["capability"], "verificada": verificada,
                               "evidencia_id": prova["id"] if prova is not None and verificada else None}}

    def _anotar_filas(self, alvos: list[dict[str, object]]) -> None:
        """31.206 (adendo v1.114): `fila` = {posicao, a_frente, previsao_inicio_em, base_ms} do alvo PENDENTE, a posição
        dele na fila do aparelho (`domain/fila.py`); None no alvo que já começou, terminou ou parou. Duas consultas por
        leitura, para todos os alvos."""
        pendentes = [a for a in alvos if a["estado"] == "pendente" and a.get("run_id") and a.get("instance_id")]
        for a in alvos:
            a["fila"] = None
        if not pendentes:
            return
        runs = sorted({str(a["run_id"]) for a in pendentes})
        meus = {str(r["id"]): r for r in self.db.query(
            f"SELECT id, prioridade, created_at FROM runs WHERE id IN ({','.join('?' * len(runs))})", tuple(runs))}
        aparelhos = sorted({str(a["instance_id"]) for a in pendentes})
        trabalhos: dict[str, list[filas.Trabalho]] = {}
        for r in self.db.query(
                "SELECT o.instance_id, o.run_id, o.status, r.prioridade, r.created_at FROM objectives o JOIN runs r ON"
                " r.id=o.run_id WHERE o.status IN ('pending','running') AND r.status='running' AND r.pause_requested=0"
                f" AND r.cancel_requested=0 AND o.instance_id IN ({','.join('?' * len(aparelhos))})", tuple(aparelhos)):
            trabalhos.setdefault(str(r["instance_id"]), []).append(filas.Trabalho(
                run_id=str(r["run_id"]), rodando=r["status"] == "running", prioridade=int(r["prioridade"] or 0),
                criado_em=str(r["created_at"])))
        base = filas.base_ms(alvos)
        agora = datetime.now(timezone.utc)
        for a in pendentes:
            meu = meus.get(str(a["run_id"]))
            if meu is None:
                continue
            frente = filas.a_frente(str(a["run_id"]), int(meu["prioridade"] or 0), str(meu["created_at"]),
                                    trabalhos.get(str(a["instance_id"]), []))
            a["fila"] = {"posicao": frente + 1, "a_frente": frente,
                         "previsao_inicio_em": filas.previsao(agora, frente, base), "base_ms": base}

    def _gasto_do_run(self, run_id: str) -> float:
        if self._lote is not None and run_id in self._lote.runs:
            return self._lote.gasto.get(run_id, 0.0)
        return costs.spent_usd(self.db, self.precos, run_id=run_id)

    def _montar_lote(self, linhas: list[Row]) -> _Lote:
        """Uma consulta por tabela para todos os alvos, com as mesmas linhas que os ajudantes leriam um a um: a resposta
        não muda (`tests/test_operacoes.py::test_o_get_com_lote_e_o_mesmo_sem_lote`)."""
        def em(coluna: str, valores: list[str]) -> tuple[str, tuple[str, ...]]:
            return f"{coluna} IN ({','.join('?' * len(valores))})", tuple(valores)

        run_ids = sorted({str(a["run_id"]) for a in linhas if a["run_id"]})
        runs: dict[str, Row] = {}
        objetivos: dict[str, Row] = {}
        if run_ids:
            w, p = em("id", run_ids)
            runs = {str(r["id"]): r for r in self.db.query(
                f"SELECT id, status, status_detail, finished_at FROM runs WHERE {w}", p)}
            w, p = em("run_id", run_ids)
            for o in self.db.query(f"SELECT * FROM objectives WHERE {w} ORDER BY id", p):
                objetivos.setdefault(str(o["run_id"]), o)    # o primeiro por execução, como o `ORDER BY id LIMIT 1`
        perfis = sorted({str(a["profile_id"]) for a in linhas})
        personas: dict[str, Row] = {}
        if perfis:
            w, p = em("id", perfis)
            personas = {str(r["id"]): r for r in self.db.query(f"SELECT * FROM instagram_profiles WHERE {w}", p)}
        contas_ids = sorted({str(a["account_id"]) for a in linhas if a["account_id"]})
        contas: dict[tuple[str, str], Row] = {}
        sessoes: dict[str, list[str]] = {}
        if contas_ids:
            w, p = em("id", contas_ids)
            contas = {(str(r["profile_id"]), str(r["id"])): r
                      for r in self.db.query(f"SELECT * FROM profile_accounts WHERE {w}", p)}
            w, p = em("account_id", contas_ids)
            for r in self.db.query(f"SELECT account_id, instance_id FROM account_sessions WHERE {w} AND status=?"
                                   " ORDER BY updated_at DESC, instance_id", (*p, SessionStatus.session_ready.value)):
                sessoes.setdefault(str(r["account_id"]), []).append(str(r["instance_id"]))
        aparelhos = sorted({str(a["instance_id"]) for a in linhas if a["instance_id"]})
        travados: set[str] = set()
        if aparelhos:
            w, p = em("instance_id", aparelhos)
            travados = {str(r["instance_id"]) for r in self.db.query(
                f"SELECT DISTINCT instance_id FROM device_locked_accounts WHERE {w} AND resolved_at IS NULL", p)}
        etapas: dict[str, list[Row]] = {}
        if objetivos:
            versao = {str(o["id"]): o["plan_version"] for o in objetivos.values()}
            etapas = {oid: [] for oid in versao}
            w, p = em("objective_id", sorted(versao))
            for s in self.db.query(f"SELECT * FROM steps WHERE {w} ORDER BY seq, id", p):
                if s["plan_version"] == versao[str(s["objective_id"])]:
                    etapas[str(s["objective_id"])].append(s)
        com_efeito = [str(s["id"]) for lista in etapas.values() for s in lista if s["side_effect"]]
        # O dublê de aprovações de um teste pode não ter a leitura em lote: sem ela, cada etapa busca o seu pedido.
        em_lote = getattr(self.aprovacoes.store, "for_steps", None)
        achados = em_lote(com_efeito) if callable(em_lote) else None
        pedidos: dict[str, Approval | None] = ({sid: achados.get(sid) for sid in com_efeito}
                                               if isinstance(achados, dict) else {})
        return _Lote(runs=runs, objetivos=objetivos, etapas=etapas, pedidos=pedidos,
                     gasto=costs.spent_usd_por_run(self.db, self.precos, run_ids),
                     pesquisa=costs.spent_usd_por_run(self.db, self.precos, run_ids, origem="pesquisa"),
                     personas=personas, contas=contas, sessoes=sessoes, travados=travados)

    def _nome(self, profile_id: str) -> str | None:
        p = self._lote.personas.get(profile_id) if self._lote is not None else None
        p = p if p is not None else self.social.persona_row(profile_id)
        if p is None:
            return None
        nome = " ".join(str(p[k]) for k in ("first_name", "last_name") if k in p.keys() and p[k])
        return nome or (str(p["username"]) if "username" in p.keys() else None)

    def _handle(self, a: Row) -> str | None:
        if not a["account_id"]:
            return None
        chave = (str(a["profile_id"]), str(a["account_id"]))
        conta = self._lote.contas.get(chave) if self._lote is not None else None
        conta = conta if conta is not None else self.social.account_row(*chave)
        return str(conta["handle"]) if conta is not None and conta["handle"] else None

    def _capacidade(self, alvos: list[dict[str, object]]) -> dict[str, object]:
        contas = [a for a in alvos if a["account_id"]]
        # A conta que entra pela troca declarada (31.207) conta como sessão válida: a porta de sessão a abre na hora.
        troca = bool(alvos) and troca_declarada(self.db, str(alvos[0]["app_id"]))
        com_sessao = [a for a in contas if self._sessoes_prontas(str(a["account_id"]))
                      or (troca and bool(a["instance_id"]) and self._pode_entrar_pela_troca(
                          str(a["profile_id"]), str(a["account_id"]), str(a["instance_id"]), str(a["app_id"])))]
        disponiveis = [a for a in com_sessao if self._aparelho_apto(a["instance_id"])]
        estados = Counter(str(a["estado"]) for a in alvos)
        motivos = Counter(str(a["motivo"]) for a in alvos if a["estado"] == "bloqueado" and a["motivo"])
        return {"solicitados": len(alvos), "contas_existentes": len(contas), "sessoes_validas": len(com_sessao),
                "contas_disponiveis": len(disponiveis), "concluidas": estados["concluido"],
                "bloqueadas": estados["bloqueado"], "em_curso": estados["em_curso"] + estados["pendente"],
                "motivos": dict(sorted(motivos.items()))}

    def _aparelho_apto(self, instance_id: object) -> bool:
        rt = self.runs.devices.devices.get(str(instance_id)) if instance_id else None
        if rt is None or rt.store:
            return False
        if self._lote is not None:
            return rt.state == InstanceState.online and str(instance_id) not in self._lote.travados
        travada = self.social.conta_travada_no_aparelho(str(instance_id))
        return rt.state == InstanceState.online and travada is None

    @staticmethod
    def _status(op: Row, alvos: list[dict[str, object]]) -> str:
        if op["status"] == "cancelada":
            return "cancelada"
        if any(a["estado"] in ("pendente", "em_curso") for a in alvos):
            return "em_curso"
        return "concluida" if alvos and all(a["estado"] == "concluido" for a in alvos) else "concluida_com_bloqueios"

    def _anotar(self, op_id: str, a: Row, estagio: str, estado: str, motivo: str | None) -> None:
        """Grava o estágio lido e avisa a mudança (`operacao.alvo`); a leitura seguinte só avisa se mudou de novo."""
        if (a["estagio"], a["estado"], a["motivo"]) == (estagio, estado, motivo):
            return
        # Condicional no SQL (31.220): o laço do sistema e um GET que leem juntos gravam e avisam uma vez só.
        if self.db.execute("UPDATE operacao_alvos SET estagio=?, estado=?, motivo=?, updated_at=? WHERE operacao_id=?"
                           " AND profile_id=? AND (estagio<>? OR estado<>? OR COALESCE(motivo, '')<>?)",
                           (estagio, estado, motivo, now_iso(), op_id, a["profile_id"], estagio, estado,
                            motivo or "")).rowcount == 0:
            return
        self.bus.emit("operacao.alvo", f"Operação {op_id}: {a['profile_id']} em {estagio} ({estado}).",
                      run_id=a["run_id"], instance_id=a["instance_id"],
                      data={"operacao_id": op_id, "profile_id": a["profile_id"], "estagio": estagio, "estado": estado,
                            "motivo": motivo})

    @staticmethod
    def _fim_real(alvos: list[dict[str, object]]) -> str | None:
        """A hora do último estágio alcançado entre os alvos: é quando a operação terminou de fato. Fechar com a hora da
        LEITURA punha o fim depois do que aconteceu (ou antes, quando a operação reabre e fecha de novo)."""
        horas = [str(e["em"]) for a in alvos for e in a["estagios"]]  # type: ignore[attr-defined]
        return max(horas) if horas else None

    def _fechar(self, op: Row, status: str, capacidade: dict[str, object], *, fim: str | None = None) -> str | None:
        # 31.241 (onda 2 de 07/10): com `acao_final=executar`, a operação fechada em "aguarda liberação" e aprovada POR
        # FORA do liberar (Pendências) não reabria: a reabertura do `ler` só cobre `preparar`. O fim ficava o do preparo,
        # o status gravado o de antes e nenhum `operacao.encerrada` novo saía. Agora o alvo de novo em curso reabre a
        # operação, e o fechamento com outro resultado fecha de novo com o fim real. Nunca a cancelada.
        if status == "em_curso":
            if op["finished_at"] and op["status"] != "cancelada":
                self.db.execute("UPDATE operacoes SET status='em_curso', finished_at=NULL, updated_at=? WHERE id=? AND"
                                " finished_at IS NOT NULL AND status<>'cancelada'", (now_iso(), op["id"]))
            return None
        if op["finished_at"]:
            if status == op["status"] or "cancelada" in (status, op["status"]):
                return str(op["finished_at"])
            return self._fechar_de_novo(op, status, capacidade, fim=fim)
        agora = min(fim, now_iso()) if fim else now_iso()
        # `.rowcount` (31.220): `execute` devolve o cursor, e a comparação com 0 nunca era verdadeira; o laço e um GET
        # que fecham juntos emitiam o `operacao.encerrada` duas vezes. O `status` na condição (achado do Codex no PR
        # 494): o cancelar de outra réplica, gravado depois da leitura de `op`, não pode virar `concluida*` aqui.
        if self.db.execute("UPDATE operacoes SET status=?, finished_at=?, updated_at=? WHERE id=? AND finished_at IS"
                           " NULL AND (status<>'cancelada' OR ?='cancelada')",
                           (status, agora, agora, op["id"], status)).rowcount == 0:
            # Perdeu a corrida: devolve a hora que ficou gravada (ou nenhuma, se foi o cancelar), sem avisar de novo.
            gravada = self.db.scalar("SELECT finished_at FROM operacoes WHERE id=?", (op["id"],))
            return str(gravada) if gravada else None
        self.bus.emit("operacao.encerrada", f"Operação {op['id']} encerrada: {status}.",
                      data={"operacao_id": op["id"], "status": status, "capacidade": capacidade,
                            "custo": self._custo(str(op["id"]))})
        return agora

    def _fechar_de_novo(self, op: Row, status: str, capacidade: dict[str, object], *, fim: str | None) -> str:
        """31.241: a operação já fechada cujos alvos terminaram com OUTRO resultado (a ação aprovada por fora rodou sem
        uma leitura no meio que a reabrisse). O fim é o último estágio real, nunca antes do fim gravado; CAS no fim
        lido, para duas leituras juntas avisarem uma vez só."""
        antes = str(op["finished_at"])
        novo = max(antes, min(fim, now_iso())) if fim else antes
        if self.db.execute("UPDATE operacoes SET status=?, finished_at=?, updated_at=? WHERE id=? AND finished_at=? AND"
                           " status<>'cancelada'", (status, novo, now_iso(), op["id"], antes)).rowcount == 0:
            gravada = self.db.scalar("SELECT finished_at FROM operacoes WHERE id=?", (op["id"],))
            return str(gravada) if gravada else antes
        self.bus.emit("operacao.encerrada", f"Operação {op['id']} encerrada: {status}.",
                      data={"operacao_id": op["id"], "status": status, "capacidade": capacidade})
        return novo

    # ------------------------------------------------------------------ pool elegível (31.174)
    def elegiveis(self, app_id: str) -> dict[str, object]:
        """Quem pode ser alvo AGORA neste app: a mesma conferência da criação (persona → conta → sessão → aparelho, na
        ordem do dono), mais o aparelho apto (online, fora da loja, sem conta travada). Só leitura: nada é criado nem
        despachado. A sessão vencida continua elegível (a porta do despacho relê a tela antes da tarefa) e vem marcada,
        para a pessoa reverificar antes da onda. Diagnóstico da prova (§ NECESSÁRIO): o pool era montado à mão."""
        if self.db.one("SELECT id FROM apps WHERE id=?", (app_id,)) is None:
            raise OperacaoError("app_inexistente", f"O app {app_id!r} não está registrado.", 404)
        validade = int(getattr(self.social, "session_max_age_s", 0) or 0)
        itens: list[dict[str, object]] = []
        for r in self.db.query("SELECT id FROM instagram_profiles ORDER BY id"):
            pid = str(r["id"])
            parada, motivo, conta, aparelho = self._conferir(AlvoPedido(pid), app_id)
            if parada is None and not self._aparelho_apto(aparelho):
                parada, motivo = "aparelho", APARELHO_INAPTO
            sessao = (self.db.one("SELECT status, verified_at FROM account_sessions WHERE account_id=? AND instance_id=?",
                                  (conta, aparelho)) if conta and aparelho else None)
            itens.append({"profile_id": pid, "persona_nome": self._nome(pid), "account_id": conta,
                          "instance_id": aparelho, "elegivel": parada is None, "parou_em": parada, "motivo": motivo,
                          "sessao_verificada_em": sessao["verified_at"] if sessao is not None else None,
                          "sessao_vencida": sessao_vencida(sessao, validade)})
        motivos = Counter(str(i["motivo"]) for i in itens if not i["elegivel"] and i["motivo"])
        return {"app_id": app_id, "itens": itens,
                "contagem": {"personas": len(itens), "elegiveis": sum(1 for i in itens if i["elegivel"]),
                             "com_sessao_vencida": sum(1 for i in itens if i["elegivel"] and i["sessao_vencida"]),
                             "motivos": dict(sorted(motivos.items()))}}

    # ------------------------------------------------------------------ cancelar e liberar
    def cancelar(self, op_id: str, *, quem: str | None = None) -> dict[str, object]:
        op = self.db.one("SELECT * FROM operacoes WHERE id=?", (op_id,))
        if op is None:
            raise OperacaoError("operacao_inexistente", "Operação não encontrada.", 404)
        if op["finished_at"]:
            raise OperacaoError("ja_encerrada", f"A operação já terminou ({op['status']}).", 409)
        # A execução que já terminou (o alvo concluído, ou o que falhou) não se cancela: `RunService.cancel` recusaria com
        # `RunError`, e o laço pararia no meio, com uma parte dos alvos cancelada e a operação em curso. A que terminar
        # entre a leitura e o pedido cai no mesmo `RunError`, e o laço segue para os outros alvos.
        for r in self.db.query("SELECT a.run_id FROM operacao_alvos a JOIN runs r ON r.id=a.run_id WHERE a.operacao_id=?"
                               " AND r.status NOT IN (?,?,?) ORDER BY a.seq",
                               (op_id, RunStatus.completed.value, RunStatus.cancelled.value, RunStatus.failed.value)):
            try:
                self.runs.cancel(str(r["run_id"]), por=quem)
            except RunError:
                continue
        self.db.execute("UPDATE operacoes SET status='cancelada', updated_at=? WHERE id=?", (now_iso(), op_id))
        return self.ler(op_id)

    def cancelar_alvos(self, op_id: str, *, profile_ids: Sequence[str] = (), estados: Sequence[str] = (),
                       estagios: Sequence[str] = (), instance_ids: Sequence[str] = (),
                       quem: str | None = None) -> dict[str, object]:
        """Cancela só os alvos que casam com TODOS os filtros dados e deixa a operação seguir com os outros (rodada de 30
        alvos: o aparelho travado, ou os que esperam o liberar e não vão ser liberados). Filtro vazio é recusado: cancelar
        tudo é `cancelar`, de propósito, e um corpo esquecido não pode virar isso."""
        if not (profile_ids or estados or estagios or instance_ids):
            raise OperacaoError("filtro_vazio", "Diga quais alvos cancelar (perfil, estado, estágio ou aparelho); para a"
                                                " operação inteira, use cancelar.", 422)
        fora = sorted(set(estados) - set(ESTADOS))
        if fora:
            raise OperacaoError("estado_desconhecido", f"Estado desconhecido no filtro: {', '.join(fora)}.", 422)
        atual = self.ler(op_id)
        # Só a cancelada é recusada, como no liberar (achado do Codex no PR 493): com todos os alvos restantes à espera
        # do liberar, a leitura fecha a operação (eles contam como bloqueados), e é justamente aí que se descartam as
        # ações preparadas. A execução já terminada sai em `ignorados` como `ja_terminou`.
        if atual["status"] == "cancelada":
            raise OperacaoError("ja_encerrada", f"A operação já terminou ({atual['status']}).", 409)
        cancelados: list[str] = []
        ignorados: list[dict[str, str]] = []
        terminais = (RunStatus.completed.value, RunStatus.cancelled.value, RunStatus.failed.value)
        for a in atual["alvos"]:  # type: ignore[attr-defined]
            if ((profile_ids and a["profile_id"] not in profile_ids) or (estados and a["estado"] not in estados)
                    or (estagios and a["estagio"] not in estagios)
                    or (instance_ids and a["instance_id"] not in instance_ids)):
                continue
            # O critério é a EXECUÇÃO não ter terminado, não o estado lido: o alvo que espera o liberar está `bloqueado`
            # na leitura, mas a execução dele segue aberta (aguarda a pessoa) e cancelá-la é justamente o que se quer.
            run = self.db.one("SELECT status FROM runs WHERE id=?", (a["run_id"],)) if a["run_id"] else None
            if run is None:
                ignorados.append({"profile_id": str(a["profile_id"]), "motivo": "sem_execucao"})
                continue
            if run["status"] in terminais:
                ignorados.append({"profile_id": str(a["profile_id"]), "motivo": "ja_terminou"})
                continue
            try:
                self.runs.cancel(str(a["run_id"]), por=quem)
            except RunError:   # terminou entre a leitura e o pedido
                ignorados.append({"profile_id": str(a["profile_id"]), "motivo": "ja_terminou"})
                continue
            cancelados.append(str(a["profile_id"]))
        return {"cancelados": cancelados, "ignorados": ignorados, "operacao": self.ler(op_id)}

    def liberar(self, op_id: str, itens: Sequence[tuple[str, str]], *, quem: str | None = None) -> dict[str, object]:
        """Aprova, pelo serviço de aprovações de sempre, a ação preparada de cada alvo pedido, com o eco do texto que a
        pessoa leu, até o limite de ações executadas (contando as já executadas)."""
        atual = self.ler(op_id)
        # Em `preparar`, o alvo na ação preparada está concluído: com todos ali, a operação fecha (`_fechar`) ANTES de a
        # pessoa ler os textos. Recusar a operação fechada tornaria a liberação impossível justamente quando ela cabe;
        # só a cancelada é recusada. O alvo sem pedido pendente sai em `recusados` como sempre.
        if atual["status"] == "cancelada":
            raise OperacaoError("ja_encerrada", f"A operação já terminou ({atual['status']}).", 409)
        alvos = {str(a["profile_id"]): a for a in atual["alvos"]}  # type: ignore[attr-defined]
        limite = int(self.limites().operacao_max_acoes_executadas)
        # Contar e aprovar na MESMA transação, com a linha da operação travada antes de contar: duas liberações (no
        # mesmo processo, ou em dois backends sobre o mesmo PostgreSQL) se enfileiram, e a contagem das já comprometidas
        # (aprovadas, executando ou executadas) da segunda vê o que a primeira aprovou. A `tx()` é reentrante: a do
        # serviço de aprovações entra nesta.
        liberados: list[str] = []
        recusados: list[dict[str, str]] = []
        with self.db.tx():
            self.db.execute("UPDATE operacoes SET updated_at=? WHERE id=?", (now_iso(), op_id))
            if self.db.scalar("SELECT status FROM operacoes WHERE id=?", (op_id,)) == "cancelada":
                raise OperacaoError("ja_encerrada", "A operação já terminou (cancelada).", 409)
            feitas = self._acoes_comprometidas(op_id)
            for profile_id, texto in itens:
                a = alvos.get(profile_id)
                pedido = self._pedido_pendente(a)
                if a is None or pedido is None:
                    recusados.append({"profile_id": profile_id, "motivo": "sem ação preparada"})
                    continue
                visto = str(self.aprovacoes.na_tela(pedido.to_dict()).get("generated_content") or "")
                if visto.strip() != texto.strip():
                    recusados.append({"profile_id": profile_id, "motivo": "texto_divergente"})
                    continue
                if feitas + len(liberados) >= limite:
                    recusados.append({"profile_id": profile_id, "motivo": LIMITE_DE_ACOES})
                    continue
                try:
                    self.aprovacoes.decide(pedido.id, "approve", note=f"liberado na operação {op_id}"
                                                                       + (f" por {quem}" if quem else ""))
                except SocialError as exc:
                    recusados.append({"profile_id": profile_id, "motivo": _motivo(str(exc)) or "recusado"})
                    continue
                liberados.append(profile_id)
            if liberados:
                # A operação volta a correr: a ação liberada ainda vai ser executada e verificada, e é a leitura seguinte
                # que a fecha de novo quando nenhum alvo estiver em curso.
                self.db.execute("UPDATE operacoes SET acao_final='executar', status='em_curso', finished_at=NULL,"
                                " updated_at=? WHERE id=? AND status<>'cancelada'", (now_iso(), op_id))
        return {"liberados": liberados, "recusados": recusados, "operacao": self.ler(op_id)}

    def _acoes_comprometidas(self, op_id: str) -> int:
        """Quantos alvos já têm a ação final aprovada (executando ou executada): é o que o limite conta. Contar só a
        executada deixaria uma segunda liberação aprovar de novo antes de a primeira rodar."""
        return int(self.db.scalar(
            "SELECT COUNT(DISTINCT a.run_id) FROM pending_approvals a JOIN runs r ON r.id=a.run_id"
            " WHERE r.operacao_id=? AND a.status IN ('approved','edited')", (op_id,)) or 0)

    def _runs_com_acao_aprovada(self, op_id: str) -> set[object]:
        return {r["run_id"] for r in self.db.query(
            "SELECT DISTINCT a.run_id FROM pending_approvals a JOIN runs r ON r.id=a.run_id"
            " WHERE r.operacao_id=? AND a.status IN ('approved','edited')", (op_id,))}

    def _pedido_pendente(self, alvo: dict[str, object] | None):  # type: ignore[no-untyped-def]
        if alvo is None or not alvo.get("run_id"):
            return None
        for s in self.db.query("SELECT id FROM steps WHERE run_id=? AND side_effect=1 ORDER BY seq, id",
                               (alvo["run_id"],)):
            pedido = self.aprovacoes.store.for_step(str(s["id"]))
            if pedido is not None and pedido.status == "pending":
                return pedido
        return None
