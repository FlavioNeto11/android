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
"""
from __future__ import annotations

import hashlib
import re
import json
import secrets
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.contracts.origem import PREFIXO_OPERACAO
from app.db import OPERATIONAL_ERRORS, Database, Row, coluna_ausente, dumps, loads
from app.models import InstanceState, RunCreate, RunStatus, RunTarget, SessionStatus
from app.modules.applications.infrastructure import registry as apps_registrados
from app.modules.applications.infrastructure.registry import definition_of
from app.modules.operacoes.domain.estagios import EtapaLida, FatosDoAlvo, Leitura, derivar, motivo_curto
from app.security.redaction import chave_sensivel, looks_secret, parece_senha_ou_codigo, redact
from app.planning import costs
from app.social.repository import sessao_vencida
from app.social.service import SocialError
from app.taskqueue.plano_da_operacao import NOMES_RESERVADOS, normal
from app.taskqueue.recipes import SENSITIVE_PARAM
from app.taskqueue.service import RunError
from app.util import now_iso

if TYPE_CHECKING:
    from app.config import LimitsCfg
    from app.events import EventBus
    from app.social.approvals import ApprovalService
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
LIMITE_DE_ACOES = "limite de ações executadas"
#: 31.174: o aparelho da sessão existe, mas não recebe tarefa agora (fora do ar, na loja ou com conta travada).
APARELHO_INAPTO = "aparelho fora do ar ou com conta travada"
AGUARDA_LIBERACAO = "aguarda liberação"


#: Um @ de conta no texto do motivo (o da regra da frota cita o alvo): o motivo da operação diz "o perfil alvo".
_ARROBA = re.compile(r"(?<![\w.])@[A-Za-z0-9._]{1,60}")


def _motivo(texto: object) -> str | None:
    """O motivo REDIGIDO, numa linha e SEM @ de conta. O do objetivo pode trazer texto lido da tela, da pergunta à pessoa
    ou o @ do alvo (a porta de frota o cita), e vai para o banco, o evento `operacao.alvo`, a API e o relatório."""
    return motivo_curto(_ARROBA.sub("o perfil alvo", redact(str(texto)) or "") if texto else None)


class OperacaoError(Exception):
    def __init__(self, code: str, message: str, status: int = 409, **extra: object) -> None:
        super().__init__(message)
        self.code, self.message, self.status, self.extra = code, message, status, extra


@dataclass(frozen=True)
class AlvoPedido:
    profile_id: str
    account_id: str | None = None
    instance_id: str | None = None


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
                 limites: Callable[[], LimitsCfg], bus: EventBus, precos: dict[str, list[float]]) -> None:
        self.db, self.runs, self.social, self.aprovacoes = db, runs, social, aprovacoes
        self.limites, self.bus, self.precos = limites, bus, precos

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
                    command=pedido.command, idempotency_key=chave, teto_de_autonomia="preparar",
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
            return "sessao", SEM_SESSAO, str(conta["id"]), alvo.instance_id
        rt = self.runs.devices.devices.get(aparelho)
        if rt is None or rt.store:
            return "aparelho", SEM_APARELHO, str(conta["id"]), aparelho
        return None, None, str(conta["id"]), aparelho

    def _principal_com_sessao(self, profile_id: str, sessoes: list[str]) -> str | None:
        principal = self.social.binding_principal(profile_id)
        iid = str(principal["instance_id"]) if principal is not None else None
        return iid if iid in sessoes else None

    def _sessoes_prontas(self, account_id: str) -> list[str]:
        return [str(r["instance_id"]) for r in self.db.query(
            "SELECT instance_id FROM account_sessions WHERE account_id=? AND status=? ORDER BY updated_at DESC,"
            " instance_id", (account_id, SessionStatus.session_ready.value))]

    def _custo(self, op_id: str) -> dict[str, float]:
        """O custo da operação, com a pesquisa externa SEPARADA (frente de aprendizado): ela roda dentro da execução de
        um alvo, então as linhas dela também têm o `run_id` dele, e somar sem tirar contaria duas vezes."""
        runs = [str(r["id"]) for r in self.db.query("SELECT id FROM runs WHERE operacao_id=?", (op_id,))]
        total = sum(costs.spent_usd(self.db, self.precos, run_id=r) for r in runs)
        pesquisa = sum(costs.spent_usd(self.db, self.precos, run_id=r, origem="pesquisa") for r in runs)
        return {"pesquisa_usd": round(pesquisa, 4), "alvos_usd": round(total - pesquisa, 4), "total_usd": round(total, 4)}

    def _gasto(self, op_id: str) -> float:
        return sum(costs.spent_usd(self.db, self.precos, run_id=str(r["id"]))
                   for r in self.db.query("SELECT id FROM runs WHERE operacao_id=?", (op_id,)))

    # ------------------------------------------------------------------ ler
    def listar(self, limite: int = 50) -> dict[str, object]:
        linhas = self.db.query("SELECT id FROM operacoes ORDER BY created_at DESC, id DESC LIMIT ?", (int(limite),))
        itens = []
        for r in linhas:
            d = self.ler(str(r["id"]))
            itens.append({k: d[k] for k in ("id", "command", "app_id", "acao_final", "status", "created_at",
                                            "finished_at", "capacidade")})
        return {"items": itens}

    def ler(self, op_id: str) -> dict[str, object]:
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
        alvos = []
        for a in self.db.query("SELECT * FROM operacao_alvos WHERE operacao_id=? ORDER BY seq, profile_id", (op_id,)):
            leitura, resultado = self._ler_alvo(op, a, definicao)
            alvos.append((a, leitura, resultado))
        saida = []
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
            custo = round(costs.spent_usd(self.db, self.precos, run_id=str(a["run_id"])), 4) if a["run_id"] else None
            if resultado is not None:
                resultado = {**resultado, "custo_usd": custo}
            saida.append({"profile_id": a["profile_id"], "persona_nome": self._nome(str(a["profile_id"])),
                          "app_id": op["app_id"], "account_id": a["account_id"], "conta": self._handle(a),
                          "instance_id": a["instance_id"], "run_id": a["run_id"], "estagio": lt.estagio,
                          "estado": estado, "motivo": motivo,
                          "parou_em": parou if estado in ("bloqueado", "cancelado") else None,
                          "estagios": [{"estagio": e, "em": em} for e, em in lt.estagios], "resultado": resultado,
                          "custo_usd": custo, "sessao_verificada_em": self._sessao_verificada_em(a)})
        capacidade = self._capacidade(saida)
        status = self._status(op, saida)
        return {"id": op["id"], "command": op["command"], "app_id": op["app_id"], "acao_final": op["acao_final"],
                "max_usd": op["max_usd"], "assunto": op["assunto"], "fontes": loads(op["fontes"], []),
                "parametros": loads(op["parametros"], None), "fontes_da_pesquisa": self._fontes_da_pesquisa(op_id),
                "status": status, "created_at": op["created_at"],
                "finished_at": self._fechar(op, status, capacidade, fim=self._fim_real(saida)),
                "capacidade": capacidade, "alvos": saida, "custo": self._custo(op_id)}

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
            run = self.db.one("SELECT status, status_detail, finished_at FROM runs WHERE id=?", (a["run_id"],))
            obj = self.db.one("SELECT * FROM objectives WHERE run_id=? ORDER BY id LIMIT 1", (a["run_id"],))
            if obj is not None:
                for s in self.db.query("SELECT * FROM steps WHERE objective_id=? AND plan_version=? ORDER BY seq, id",
                                       (obj["id"], obj["plan_version"])):
                    pedido = self.aprovacoes.store.for_step(str(s["id"])) if s["side_effect"] else None
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

    def _nome(self, profile_id: str) -> str | None:
        p = self.social.persona_row(profile_id)
        if p is None:
            return None
        nome = " ".join(str(p[k]) for k in ("first_name", "last_name") if k in p.keys() and p[k])
        return nome or (str(p["username"]) if "username" in p.keys() else None)

    def _handle(self, a: Row) -> str | None:
        if not a["account_id"]:
            return None
        conta = self.social.account_row(str(a["profile_id"]), str(a["account_id"]))
        return str(conta["handle"]) if conta is not None and conta["handle"] else None

    def _capacidade(self, alvos: list[dict[str, object]]) -> dict[str, object]:
        contas = [a for a in alvos if a["account_id"]]
        com_sessao = [a for a in contas if self._sessoes_prontas(str(a["account_id"]))]
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
        self.db.execute("UPDATE operacao_alvos SET estagio=?, estado=?, motivo=?, updated_at=? WHERE operacao_id=? AND"
                        " profile_id=?", (estagio, estado, motivo, now_iso(), op_id, a["profile_id"]))
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
        if status == "em_curso" or op["finished_at"]:
            return str(op["finished_at"]) if op["finished_at"] else None
        agora = min(fim, now_iso()) if fim else now_iso()
        if self.db.execute("UPDATE operacoes SET status=?, finished_at=?, updated_at=? WHERE id=? AND finished_at IS"
                           " NULL", (status, agora, agora, op["id"])) == 0:
            return None
        self.bus.emit("operacao.encerrada", f"Operação {op['id']} encerrada: {status}.",
                      data={"operacao_id": op["id"], "status": status, "capacidade": capacidade})
        return agora

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
