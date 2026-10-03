"""Fluxos: o plano de um comando repetível, congelado em forma de template.

Por quê: o planejador real reescreve chaves, títulos e pós-condições a cada execução; sem um plano estável as
receitas (taskqueue/recipes.py) nunca casariam de novo. Quando uma execução termina com TODOS os aparelhos
comprovados, o comando vira um modelo — os valores dos parâmetros dão lugar a {nome} — e o plano é guardado do mesmo
jeito. Um comando novo que case com o modelo (mesmo texto, outros valores) reaproveita o plano sem chamar o planejador.

D1 (ADR-054): com a política do livro de aprendizado ligada (`politica`, posta pela composição), o fluxo aprendido de
execução nasce `candidate` — inerte: `match` só casa `active` — e quem o valida é a sombra no digest da execução
seguinte do mesmo comando; publicar o que tem etapa de efeito externo é do dono. Sem a política (a loja crua dos
testes de unidade) vale o modo anterior: nasce ativo. O treino (`learn_from_plan`) é decisão de pessoa e publica na
hora nos dois modos. Nenhuma linha de `flows` é apagada por aqui: o comando refutado pelo sistema é reaprendido na
MESMA linha (a `match_key` é única, e `flow_scope`/`flow_required_apps` cairiam em cascata).
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Protocol

from ..db import Database, Row
from ..modules.learning.domain.livro import apps_na_ordem_do_plano
from ..modules.skills.domain.document import JsonValue
from ..models import Plan, PlannerInfo, StepResult
from ..util import now_iso

RESERVED = {"instance_id", "run_id", "account_label"}
PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
#: Quem decide quando a própria loja muda o status (nascimento de execução, reaproveitamento). O treino leva a origem.
SISTEMA = "sistema"


@dataclass(frozen=True, slots=True)
class NascimentoDoFluxo:
    """O que a política do livro vê ANTES de um fluxo nascer de uma execução (e decide com que status, se nascer)."""

    run_id: str
    match_key: str
    #: O plano-modelo em JSON exatamente como será gravado: é a base do `content_hash` do veto.
    plano: str
    #: A linha desligada da mesma `match_key` que seria reaproveitada (`None`: comando novo).
    reaproveita: str | None


@dataclass(frozen=True, slots=True)
class MudancaDoFluxo:
    """Uma mudança de status feita pela própria loja, para a trilha (`learning_transitions`)."""

    flow_id: str
    de: str | None
    para: str
    motivo: str
    por: str
    run_id: str | None = None


class PoliticaDoFluxo(Protocol):
    """O livro de aprendizado (ADR-054) do lado da loja. Chamada DENTRO da transação da escrita: a trilha entra junto
    com o status, e o status nunca cai por causa dela. Quem implementa `mudou` e engole a própria falha a isola num
    `db.savepoint()` (22.5) — no PostgreSQL, o erro engolido sem ele abortava a transação da loja e o `COMMIT` virava
    `ROLLBACK` sem aviso."""

    def ao_nascer(self, nascimento: NascimentoDoFluxo) -> str | None:
        """O status com que o fluxo nasce (`candidate` no D1), ou `None`: não aprende (conteúdo vetado, linha que uma
        pessoa desligou)."""
        ...

    def mudou(self, mudanca: MudancaDoFluxo) -> None: ...


def confirmada_a_mao(db: Database, run_id: str) -> bool:
    """Alguma etapa da execução terminou `succeeded` sem prova da tela (`verified` ≠ true)?

    Motivo real: r-20260928165254-e31953 e r-20260928195344-02ee9e (android-06). "Confirmar concluído" marca a
    etapa como feita com `verified=false`, e a execução fecha `completed`; o fluxo aprendido dali passava a valer
    como caminho comprovado sem nunca ter sido observado. TODAS as versões do plano contam: a recuperação refaz
    só o que faltava, então a etapa confirmada à mão na v1 continua sendo parte do caminho da v2.

    Não exige que haja etapas gravadas: quem chama só chega aqui com a execução `completed`, e ela só fecha assim com
    toda etapa da versão corrente `succeeded`. O que se procura é a contradição. A sombra do D1 usa a mesma regra:
    execução com etapa confirmada à mão não é evidência de caminho.
    """
    for r in db.query("SELECT result FROM steps WHERE run_id=? AND status='succeeded'", (run_id,)):
        if not r["result"] or not StepResult.model_validate_json(r["result"]).verified:
            return True
    return False


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip().casefold()


def _com_valores(text: str, values: dict[str, str]) -> str:
    """O inverso de `_sub_values`: cada `{nome}` conhecido volta a ser o valor; o que não se conhece fica."""
    return PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), text)


def _sub_values(text: str | None, values: dict[str, str]) -> str | None:
    if not text:
        return text
    for name, value in sorted(values.items(), key=lambda kv: -len(kv[1])):
        text = text.replace(value, "{" + name + "}")
    return text


class FlowStore:
    def __init__(self, db: Database, politica: PoliticaDoFluxo | None = None):
        self.db = db
        #: O D1 do livro de aprendizado (ADR-054). `None` = o modo anterior: o fluxo aprendido nasce ativo.
        self.politica = politica

    # ------------------------------------------------------------------ aprender
    def learn_from_run(self, run: Row) -> str | None:
        """Chamado quando a execução termina `completed` (todos comprovados). Devolve o id do fluxo criado.

        Não aprende de execução de habilidade (`skill_id`), nem de comando que uma habilidade PUBLICADA já cobre — o
        caso da execução que caiu no planejador por estar fora do escopo da skill (design §15.2): o fluxo criado
        disputaria o comando com ela. Aqui só se LÊ a tabela de habilidades; fluxo nunca escreve nela.

        Nem de execução com etapa confirmada À MÃO (`confirmada_a_mao`): `completed` aceita a decisão da pessoa, o
        fluxo só aceita o que a tela comprovou.

        Com a política do D1: o status de nascimento é dela (`candidate`, ou `None` quando o conteúdo está vetado), e
        a linha DESLIGADA da mesma chave é reaproveitada (`_reaproveitavel`) — sem isso, o comando cujo fluxo o sistema
        refutou nunca mais seria aprendido. Candidato, validado ou ativo da mesma chave: nada muda aqui (quem compara
        é a sombra no digest).
        """
        if not run["plan"] or run["flow_id"] or run["skill_id"]:
            return None
        plan = Plan.model_validate_json(run["plan"])
        if plan.missing or not plan.steps:
            return None
        if self._confirmada_a_mao(run["id"]):
            return None
        command: str = run["command"]
        # parâmetros cujo valor aparece literalmente no comando viram {nome}; o run_id nunca é parâmetro de modelo
        values = {k: v for k, v in plan.parameters.items()
                  if k not in RESERVED and v and len(v) >= 3 and v in command and run["id"] not in v}
        template = _sub_values(command, values) or command
        key = _norm(template)
        existente = self.db.one("SELECT id, status, source FROM flows WHERE match_key=?", (key,))
        if existente is not None and (self.politica is None or not self._reaproveitavel(existente)):
            return None
        if self.db.one("SELECT id FROM skill_versions WHERE state='published' AND match_key=?", (key,)):
            return None
        tpl = plan.model_copy(deep=True)
        tpl.parameters = {k: ("{" + k + "}" if k in values else v) for k, v in plan.parameters.items() if run["id"] not in v}
        for s in tpl.steps:                       # o planejador às vezes escreve o valor em vez da variável: normaliza
            s.title, s.goal = _sub_values(s.title, values) or s.title, _sub_values(s.goal, values) or s.goal
            s.precondition = _sub_values(s.precondition, values)
            s.commit_guard = [_sub_values(g, values) or g for g in s.commit_guard]
            # Argumentos e guardas da linha também: congelados com o valor, o fluxo reaproveitado para outro alvo
            # abria a conversa e conferia a linha do alvo da execução-fonte (r-20260928165254-e31953). O
            # `_insert_steps` resolve `{nome}` nos dois, e nenhum entra na identidade da receita.
            s.band_guard = [_sub_values(g, values) or g for g in s.band_guard]
            s.bindings = {k: _sub_values(v, values) or v for k, v in s.bindings.items()}
            s.postcondition.value = _sub_values(s.postcondition.value, values) or s.postcondition.value
            s.postcondition.description = _sub_values(s.postcondition.description, values) or s.postcondition.description
        tpl.summary = _sub_values(tpl.summary, values) or tpl.summary
        tpl.success_criteria = [_sub_values(c, values) or c for c in tpl.success_criteria]
        plano = tpl.model_dump_json()
        reaproveita: str | None = existente["id"] if existente is not None else None
        status: str | None = "active"
        if self.politica is not None:
            status = self.politica.ao_nascer(NascimentoDoFluxo(run_id=run["id"], match_key=key, plano=plano,
                                                               reaproveita=reaproveita))
        if status is None:
            return None
        apps = tpl.required_apps or ([plan.app_id] if plan.app_id else [])
        de: str | None = None
        with self.db.tx():
            if reaproveita is not None:
                # CAS no status: a linha só é reaproveitada se ainda estiver desligada (ninguém a religou no meio).
                cur = self.db.execute(
                    "UPDATE flows SET name=?, command_template=?, plan=?, app_id=?, source_run_id=?, status=?, uses=0,"
                    " created_at=?, last_used_at=NULL WHERE id=? AND status='disabled'",
                    (plan.summary[:120], template, plano, plan.app_id, run["id"], status, now_iso(), reaproveita))
                if int(cur.rowcount or 0) != 1:
                    return None
                flow_id, de, motivo = reaproveita, "disabled", f"reaprendido da execução {run['id']} (mesma linha)"
            else:
                base = re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize("NFKD", plan.summary).encode("ascii", "ignore")
                              .decode().lower()).strip("-")[:40] or "fluxo"
                flow_id, n = base, 2
                while self.db.one("SELECT id FROM flows WHERE id=?", (flow_id,)):
                    flow_id, n = f"{base}-{n}", n + 1
                self.db.execute(
                    "INSERT INTO flows(id, name, match_key, command_template, plan, app_id, source_run_id, status,"
                    " created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (flow_id, plan.summary[:120], key, template, plano, plan.app_id, run["id"], status, now_iso()))
                motivo = f"aprendido da execução {run['id']}"
            self.set_required_apps(flow_id, apps)
            if self.politica is not None:
                self.politica.mudou(MudancaDoFluxo(flow_id=flow_id, de=de, para=status, motivo=motivo, por=SISTEMA,
                                                   run_id=run["id"]))
        return flow_id

    def _reaproveitavel(self, linha: Row) -> bool:
        """A linha da mesma `match_key` pode renascer desta execução? Só a DESLIGADA, aprendida de execução (o treino é
        da pessoa) e sem habilidade que a tenha adotado (ela é o caminho de volta da adoção). Quem a desligou decide a
        política: só a refutação do sistema libera — o que uma pessoa desligou fica desligado."""
        if linha["status"] != "disabled" or str(linha["source"] or "").startswith("training"):
            return False
        return self.db.one("SELECT id FROM skill_definitions WHERE legacy_flow_id=?", (linha["id"],)) is None

    def _confirmada_a_mao(self, run_id: str) -> bool:
        return confirmada_a_mao(self.db, run_id)

    # ------------------------------------------------------------------ habilidade treinada (item 13.2)
    def learn_from_plan(self, plan: Plan, command_template: str, *, source: str) -> str:
        """Fluxo a partir de um plano JÁ em forma de modelo (`{nome}` nos textos e nos parâmetros) — o que o modo
        treinamento produz. Mesmo formato do fluxo aprendido de execução: `match` não distingue a origem."""
        template = command_template.strip()
        key = _norm(template)
        if self.db.one("SELECT id FROM flows WHERE match_key=?", (key,)):
            raise ValueError("Já existe uma habilidade para este comando. Mude o comando ou desative a outra.")
        # Fase J: o mesmo comando nunca fica vivo como fluxo ativo E habilidade publicada (design §15.2). Aqui só se
        # LÊ a tabela de habilidades, como em `learn_from_run`.
        if self.db.one("SELECT id FROM skill_versions WHERE state='published' AND match_key=?", (key,)):
            raise ValueError("Já existe uma habilidade versionada publicada para este comando. Mude o comando ou "
                             "desabilite a habilidade.")
        base = re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize("NFKD", plan.summary).encode("ascii", "ignore")
                      .decode().lower()).strip("-")[:40] or "habilidade"
        flow_id, n = base, 2
        while self.db.one("SELECT id FROM flows WHERE id=?", (flow_id,)):
            flow_id, n = f"{base}-{n}", n + 1
        with self.db.tx():
            self.db.execute(
                "INSERT INTO flows(id, name, match_key, command_template, plan, app_id, source_run_id, created_at,"
                " source) VALUES (?,?,?,?,?,?,?,?,?)",
                (flow_id, plan.summary[:120], key, template, plan.model_dump_json(), plan.app_id, None, now_iso(),
                 source))
            apps = [plan.app_id, *(s.app_id for s in plan.steps)]
            self.set_required_apps(flow_id, [a for a in apps if a])
            if self.politica is not None:
                # Treino é decisão de pessoa (D1): publica na hora, e a trilha diz de qual demonstração veio.
                self.politica.mudou(MudancaDoFluxo(flow_id=flow_id, de=None, para="active",
                                                   motivo="ensinado no modo treinamento", por=source))
        return flow_id

    def set_scope(self, flow_id: str, *, profile_ids: list[str], group_ids: list[str]) -> None:
        self.db.execute("DELETE FROM flow_scope WHERE flow_id=?", (flow_id,))
        for pid in dict.fromkeys(profile_ids):
            self.db.execute("INSERT INTO flow_scope(flow_id, profile_id) VALUES (?,?)", (flow_id, pid))
        for gid in dict.fromkeys(group_ids):
            self.db.execute("INSERT INTO flow_scope(flow_id, group_id) VALUES (?,?)", (flow_id, gid))

    def scope(self, flow_id: str) -> dict[str, list[str]]:
        linhas = self.db.query("SELECT profile_id, group_id FROM flow_scope WHERE flow_id=?", (flow_id,))
        return {"profile_ids": [r["profile_id"] for r in linhas if r["profile_id"]],
                "group_ids": [r["group_id"] for r in linhas if r["group_id"]]}

    def _no_escopo(self, flow_id: str, profile_ids: list[str | None]) -> bool:
        esc = self.scope(flow_id)
        if not esc["profile_ids"] and not esc["group_ids"]:
            return True                                # sem escopo: vale para todos
        permitidos = set(esc["profile_ids"])
        for gid in esc["group_ids"]:
            permitidos |= {r["id"] for r in self.db.query(
                "SELECT id FROM instagram_profiles WHERE policy_group_id=?", (gid,))}
        return bool(profile_ids) and all(p in permitidos for p in profile_ids)

    # ------------------------------------------------------------------ apps exigidos
    def set_required_apps(self, flow_id: str, app_ids: list[str]) -> None:
        """Declara de que apps o fluxo precisa. Só entra app que EXISTE: exigir o que não há não ajuda ninguém."""
        self.db.execute("DELETE FROM flow_required_apps WHERE flow_id=?", (flow_id,))
        for app_id in dict.fromkeys(a for a in app_ids if a):
            if self.db.one("SELECT id FROM apps WHERE id=?", (app_id,)) is None:
                continue
            self.db.execute("INSERT INTO flow_required_apps(flow_id, app_id) VALUES (?,?)", (flow_id, app_id))

    def required_apps(self, flow_id: str) -> list[str]:
        return [r["app_id"] for r in self.db.query(
            "SELECT app_id FROM flow_required_apps WHERE flow_id=? ORDER BY app_id", (flow_id,))]

    # ------------------------------------------------------------------ casar
    def match(self, command: str, profile_ids: list[str | None] | None = None) -> tuple[Row, Plan] | None:
        """Comando novo × modelos conhecidos. Casa o texto inteiro; cada {nome} captura o valor novo.

        `profile_ids` (item 13.2): os perfis dos aparelhos da execução. Fluxo com escopo (habilidade treinada para
        perfis/grupos) só casa quando TODOS eles estão no escopo — um aparelho fora dele planejaria sozinho, e o
        plano é um só por execução. `None` = prévia sem aparelhos (custo, apps exigidos): qualquer fluxo serve.
        """
        for row in self.db.query("SELECT * FROM flows WHERE status='active' ORDER BY uses DESC, created_at"):
            if profile_ids is not None and not self._no_escopo(row["id"], profile_ids):
                continue
            values = self._extract(row["command_template"], command)
            if values is None:
                continue
            plan = self._plano_com_valores(row, values, provider="fluxo")
            if plan is None:
                continue                              # faltou valor para algum parâmetro: não é este fluxo
            return row, plan
        return None

    def plano_em_prova(self, flow_id: str, command: str) -> Plan | None:
        """30.37: o plano do PRÓPRIO fluxo para a execução de prova (a validação do fluxo pelo próprio fluxo), com os
        parâmetros do comando de origem, pelo molde DELE. Vale para qualquer status menos `disabled` (candidato ou
        ativo): o `match` não entra, então a ordem por uso e o fluxo vizinho que casaria antes não importam. O escopo de
        perfis (13.2) não se aplica: a prova roda num aparelho de QA, sem conta real. `None`: o fluxo sumiu, foi
        desligado ou o comando não cabe no molde (o pedido fecha `sem_caminho`)."""
        row = self.db.one("SELECT * FROM flows WHERE id=? AND status<>'disabled'", (flow_id,))
        if row is None:
            return None
        values = self._extract(row["command_template"], command)
        return None if values is None else self._plano_com_valores(row, values, provider="fluxo-prova")

    def _plano_com_valores(self, row: Row, values: dict[str, str], *, provider: str) -> Plan | None:
        plan = Plan.model_validate_json(row["plan"])
        plan.parameters = {k: (values.get(k, v) if v == "{" + k + "}" else v) for k, v in plan.parameters.items()}
        # RESERVED (`account_label`, `instance_id`, `run_id`) nunca é capturado por `_extract` — o valor é do APARELHO
        # e entra na materialização (`Repository.materialize`, `resolve_templates` sobre a base por aparelho). Exigir
        # valor aqui recusava todo fluxo com `{account_label}` pelo molde (LT-3): o molde fica como está e a
        # materialização o resolve; os parâmetros de verdade (os capturados) seguem exigindo valor.
        if any(v == "{" + k + "}" for k, v in plan.parameters.items() if k not in RESERVED):
            return None
        # Os critérios são do plano, não da etapa: nenhum `_insert_steps` os resolve. Com o valor novo aqui, o
        # critério do fluxo reaproveitado fala do alvo DESTA execução, não do `{nome}` nem do alvo da fonte.
        plan.success_criteria = [_com_valores(c, plan.parameters) for c in plan.success_criteria]
        plan.planner = PlannerInfo(provider=provider, model=f"{provider}:{row['id']}", simulated=plan.planner.simulated)
        # O que o fluxo EXIGE vem da tabela, não do JSON congelado: assim um fluxo aprendido antes desta
        # mudança passa a declarar o que precisa assim que alguém o declarar, sem reescrever plano nenhum.
        plan.required_apps = self.required_apps(row["id"]) or plan.required_apps
        return plan

    @staticmethod
    def _extract(template: str, command: str) -> dict[str, str] | None:
        names: list[str] = []
        pattern = ""
        pos = 0
        for m in PLACEHOLDER.finditer(template):
            pattern += re.escape(_squash(template[pos:m.start()]))
            if m.group(1) in RESERVED:
                pattern += re.escape(m.group(0))      # {instance_id}/{run_id} são texto literal do comando
            elif m.group(1) in names:
                pattern += f"(?P={m.group(1)})"
            else:
                names.append(m.group(1))
                pattern += f"(?P<{m.group(1)}>.+?)"
            pos = m.end()
        pattern += re.escape(_squash(template[pos:]))
        got = re.fullmatch(pattern, _squash(command), flags=re.IGNORECASE | re.DOTALL)
        if got is None:
            return None
        values = {k: v.strip() for k, v in got.groupdict().items()}
        return values if all(values.values()) and all(len(v) <= 500 for v in values.values()) else None

    def used(self, flow_id: str) -> None:
        self.db.execute("UPDATE flows SET uses=uses+1, last_used_at=? WHERE id=?", (now_iso(), flow_id))

    def list(self) -> list[dict[str, Any]]:
        """Os fluxos, cada um com `required_apps` na ordem em que o plano usa os apps (29.42). Duas consultas para
        todos (fluxos e a tabela de exigidos), sem uma por fluxo; o `plan` segue fora da resposta."""
        exigidos: dict[str, list[str]] = {}
        for r in self.db.query("SELECT flow_id, app_id FROM flow_required_apps ORDER BY app_id"):
            exigidos.setdefault(r["flow_id"], []).append(r["app_id"])
        saida = []
        for r in self.db.query(
                "SELECT id, name, command_template, app_id, source_run_id, status, uses, created_at, last_used_at, plan"
                " FROM flows ORDER BY last_used_at DESC, created_at DESC"):
            linha = dict(r)
            plano = linha.pop("plan")
            linha["required_apps"] = apps_na_ordem_do_plano(_json_ou_vazio(plano), exigidos.get(linha["id"], []))
            saida.append(linha | {"plan": None})
        return saida


def _json_ou_vazio(texto: str | None) -> JsonValue:
    """O plano gravado como dado; um JSON quebrado não derruba a lista (o fluxo só fica sem ordem do plano)."""
    try:
        return json.loads(texto or "null")
    except ValueError:
        return None


def _squash(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(str.maketrans("“”„‟‘’", "\"\"\"\"''"))
    return re.sub(r"\s+", " ", text).strip()
