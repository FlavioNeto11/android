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
import json
import secrets
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.contracts.origem import PREFIXO_OPERACAO
from app.db import Database, Row, dumps, loads
from app.models import InstanceState, RunCreate, RunTarget, SessionStatus
from app.modules.applications.infrastructure.registry import definition_of
from app.modules.operacoes.domain.estagios import EtapaLida, FatosDoAlvo, Leitura, derivar, motivo_curto
from app.security.redaction import redact
from app.planning import costs
from app.social.service import SocialError
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
SEM_APARELHO = "aparelho indisponível"
TETO_DE_CUSTO = "teto de custo"
LIMITE_DE_ACOES = "limite de ações executadas"
AGUARDA_LIBERACAO = "aguarda liberação"


def _motivo(texto: object) -> str | None:
    """O motivo REDIGIDO e numa linha: o do objetivo pode trazer texto lido da tela ou da pergunta à pessoa, e vai para o
    banco, o evento `operacao.alvo` e a API."""
    return motivo_curto(redact(str(texto)) if texto else None)


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


def _sha(pedido: PedidoDeOperacao) -> str:
    corpo = {"command": pedido.command.strip(), "app_id": pedido.app_id, "acao_final": pedido.acao_final,
             "max_usd": pedido.max_usd, "assunto": pedido.assunto, "fontes": list(pedido.fontes),
             "alvos": [[a.profile_id, a.account_id, a.instance_id] for a in pedido.alvos]}
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
            for texto in (pedido.command, pedido.assunto or "", *pedido.fontes):
                self.runs._recusar_credencial(texto)  # noqa: SLF001
        except RunError as exc:
            raise OperacaoError(exc.code, exc.message, exc.status) from exc
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
        op_id = f"op-{now_iso()[:19].replace('-', '').replace(':', '').replace('T', '')}-{secrets.token_hex(3)}"
        agora = now_iso()
        self.db.execute(
            "INSERT INTO operacoes(id, command, app_id, acao_final, max_usd, assunto, fontes, status, idempotency_key,"
            " corpo_sha256, criada_por, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (op_id, pedido.command.strip(), pedido.app_id, pedido.acao_final, float(pedido.max_usd), pedido.assunto,
             dumps(list(pedido.fontes)), "em_curso", pedido.idempotency_key, sha, quem, agora, agora))
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
        aparelho = alvo.instance_id or (sessoes[0] if sessoes else None)
        if aparelho is None or aparelho not in sessoes:
            return "sessao", SEM_SESSAO, str(conta["id"]), alvo.instance_id
        rt = self.runs.devices.devices.get(aparelho)
        if rt is None or rt.store:
            return "aparelho", SEM_APARELHO, str(conta["id"]), aparelho
        return None, None, str(conta["id"]), aparelho

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
        alvos, leituras = [], []
        for a in self.db.query("SELECT * FROM operacao_alvos WHERE operacao_id=? ORDER BY seq, profile_id", (op_id,)):
            leitura, resultado = self._ler_alvo(op, a, definicao)
            leituras.append((a, leitura))
            alvos.append((a, leitura, resultado))
        executadas = self._acoes_comprometidas(op_id)
        saida = []
        for a, lt, resultado in alvos:
            estado, motivo, parou = lt.estado, lt.motivo, lt.parou_em
            if lt.estagio == "acao_preparada" and op["acao_final"] == "executar" and estado != "concluido":
                estado, motivo, parou = ("bloqueado", LIMITE_DE_ACOES if executadas >= limite else AGUARDA_LIBERACAO,
                                         "acao_executada")
            self._anotar(op_id, a, lt.estagio, estado, motivo)
            saida.append({"profile_id": a["profile_id"], "persona_nome": self._nome(str(a["profile_id"])),
                          "app_id": op["app_id"], "account_id": a["account_id"], "conta": self._handle(a),
                          "instance_id": a["instance_id"], "run_id": a["run_id"], "estagio": lt.estagio,
                          "estado": estado, "motivo": motivo,
                          "parou_em": parou if estado in ("bloqueado", "cancelado") else None,
                          "estagios": [{"estagio": e, "em": em} for e, em in lt.estagios], "resultado": resultado})
        capacidade = self._capacidade(saida)
        status = self._status(op, saida)
        return {"id": op["id"], "command": op["command"], "app_id": op["app_id"], "acao_final": op["acao_final"],
                "max_usd": op["max_usd"], "assunto": op["assunto"], "fontes": loads(op["fontes"], []),
                "status": status, "created_at": op["created_at"], "finished_at": self._fechar(op, status, capacidade),
                "capacidade": capacidade, "alvos": saida, "custo": self._custo(op_id)}

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
            run = self.db.one("SELECT status FROM runs WHERE id=?", (a["run_id"],))
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
                        pedido_de_aprovacao=pedido.status if pedido is not None else None))
                    if s["side_effect"] and efeito is None:
                        efeito = s
        leitura = derivar(FatosDoAlvo(
            parada_na_criacao=parada, motivo_na_criacao=a["motivo"] if parada else None,
            objetivo_status=str(obj["status"]) if obj is not None else None,
            objetivo_comecou_em=obj["started_at"] if obj is not None else None,
            objetivo_motivo=_motivo((obj["blocked_reason"] or obj["status_detail"]) if obj is not None else None),
            run_status=str(run["status"]) if run is not None else None, etapas=etapas, marcas=marcas,
            abertura=abertura, estagio_por_capability=por_cap, acao_final=str(op["acao_final"]),
            criado_em=str(op["created_at"])))
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

    def _fechar(self, op: Row, status: str, capacidade: dict[str, object]) -> str | None:
        if status == "em_curso" or op["finished_at"]:
            return str(op["finished_at"]) if op["finished_at"] else None
        agora = now_iso()
        if self.db.execute("UPDATE operacoes SET status=?, finished_at=?, updated_at=? WHERE id=? AND finished_at IS"
                           " NULL", (status, agora, agora, op["id"])) == 0:
            return None
        self.bus.emit("operacao.encerrada", f"Operação {op['id']} encerrada: {status}.",
                      data={"operacao_id": op["id"], "status": status, "capacidade": capacidade})
        return agora

    # ------------------------------------------------------------------ cancelar e liberar
    def cancelar(self, op_id: str, *, quem: str | None = None) -> dict[str, object]:
        op = self.db.one("SELECT * FROM operacoes WHERE id=?", (op_id,))
        if op is None:
            raise OperacaoError("operacao_inexistente", "Operação não encontrada.", 404)
        if op["finished_at"]:
            raise OperacaoError("ja_encerrada", f"A operação já terminou ({op['status']}).", 409)
        for r in self.db.query("SELECT run_id FROM operacao_alvos WHERE operacao_id=? AND run_id IS NOT NULL"
                               " ORDER BY seq", (op_id,)):
            self.runs.cancel(str(r["run_id"]), por=quem)
        self.db.execute("UPDATE operacoes SET status='cancelada', updated_at=? WHERE id=?", (now_iso(), op_id))
        return self.ler(op_id)

    def liberar(self, op_id: str, itens: Sequence[tuple[str, str]], *, quem: str | None = None) -> dict[str, object]:
        """Aprova, pelo serviço de aprovações de sempre, a ação preparada de cada alvo pedido, com o eco do texto que a
        pessoa leu, até o limite de ações executadas (contando as já executadas)."""
        atual = self.ler(op_id)
        if atual["finished_at"]:
            raise OperacaoError("ja_encerrada", f"A operação já terminou ({atual['status']}).", 409)
        alvos = {str(a["profile_id"]): a for a in atual["alvos"]}  # type: ignore[attr-defined]
        limite = int(self.limites().operacao_max_acoes_executadas)
        # Sem `await` daqui ao fim: duas chamadas de `liberar` não se intercalam no laço do servidor, e a contagem
        # das já comprometidas (aprovadas, executando ou executadas) vale para a segunda.
        feitas = self._acoes_comprometidas(op_id)
        liberados: list[str] = []
        recusados: list[dict[str, str]] = []
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
            self.db.execute("UPDATE operacoes SET acao_final='executar', updated_at=? WHERE id=?", (now_iso(), op_id))
        return {"liberados": liberados, "recusados": recusados, "operacao": self.ler(op_id)}

    def _acoes_comprometidas(self, op_id: str) -> int:
        """Quantos alvos já têm a ação final aprovada (executando ou executada): é o que o limite conta. Contar só a
        executada deixaria uma segunda liberação aprovar de novo antes de a primeira rodar."""
        return int(self.db.scalar(
            "SELECT COUNT(DISTINCT a.run_id) FROM pending_approvals a JOIN runs r ON r.id=a.run_id"
            " WHERE r.operacao_id=? AND a.status IN ('approved','edited')", (op_id,)) or 0)

    def _pedido_pendente(self, alvo: dict[str, object] | None):  # type: ignore[no-untyped-def]
        if alvo is None or not alvo.get("run_id"):
            return None
        for s in self.db.query("SELECT id FROM steps WHERE run_id=? AND side_effect=1 ORDER BY seq, id",
                               (alvo["run_id"],)):
            pedido = self.aprovacoes.store.for_step(str(s["id"]))
            if pedido is not None and pedido.status == "pending":
                return pedido
        return None
