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
import secrets
import unicodedata
from dataclasses import dataclass
from typing import Any, Protocol

from ..db import Database, Row
from ..modules.learning.domain.aprovacao_automatica import PLATAFORMA
from ..modules.learning.domain.ensinado import MOTIVO_DA_PROVA_DO_ENSINADO
from ..modules.learning.domain.livro import CONFIRMADO_QUE_FICA, apps_na_ordem_do_plano
from ..modules.skills.domain.document import JsonValue
from ..modules.skills.domain.matching import specificity
from ..models import Plan, PlannerInfo, StepResult
from ..util import now_iso

RESERVED = {"instance_id", "run_id", "account_label"}
PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
#: Quem decide quando a própria loja muda o status (nascimento de execução, reaproveitamento). O treino leva a origem.
SISTEMA = "sistema"
#: 30.81: a origem do fluxo ensinado no modo treinamento (`flows.source = 'training:<sessão>'`).
PREFIXO_DO_TREINO = "training:"


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


def _borda(caractere: str, *, esquerda: bool) -> str:
    """O lookaround que a borda do valor exige, pelo caractere do EXTREMO do valor (31.96).

    Letra ou `_` pede, do lado de fora, algo que não seja letra, dígito nem `_`. Dígito é mais conservador do que era:
    à esquerda não pode haver caractere de palavra (um nome de imagem ou de botão como `btn10`, `img_10` ou `v10` não
    é o valor), e à direita não pode haver dígito nem `_` (`10_2` e `100` não são o valor), mas uma letra colada à
    direita segue valendo, a unidade (`10min`). Símbolo e espaço não pedem nada: o símbolo já delimita."""
    if re.fullmatch(r"\d", caractere):
        return r"(?<!\w)" if esquerda else r"(?![\d_])"
    if re.fullmatch(r"\w", caractere):
        return r"(?<!\w)" if esquerda else r"(?!\w)"
    return ""


def _sub_values(text: str | None, values: dict[str, str]) -> str | None:
    """Troca cada valor de exemplo por `{nome}` no texto do plano, valores mais longos primeiro.

    F6 (31.89): o valor só é trocado INTEIRO, e a borda se confere por CLASSE de caractere (`_borda`). Com "Ana" de
    exemplo, "Banana", "Ana2" e "a_Ana" ficam como estão, e "posts", "ana_silva" e "fulano123" não perdem um pedaço
    para "post", "ana" e "fulano". Com "10", "esperar 10min" vira "esperar {n}min", mas "100", "110", "v10" e "10_2"
    ficam (31.96: a esquerda sem caractere de palavra, a direita sem dígito nem `_`). Contrapartida: número colado a
    letra à ESQUERDA não troca ("10h30" vira "{n}h30", o "30" fica; "10x10" e "nº10" idem), o preço de não partir
    "v10". Borda que é símbolo ou espaço ("@fulano", "R$ 10") não exige nada: o símbolo já delimita.

    31.96, a fronteira entre pedaços já trocados: a borda se confere no texto ORIGINAL, não no resto que sobrou
    entre dois marcadores. Antes, "10min" com "10" e "min" de exemplo virava "{n}{m}": o "min" começava um pedaço novo,
    sem o "0" que o precede, e passava na borda. Um valor curto também nunca reescreve o marcador que um valor
    anterior acabou de pôr, nem toma um pedaço dele.
    """
    if not text:
        return text
    ocupado = [False] * len(text)
    trocas: list[tuple[int, int, str]] = []
    for name, value in sorted(values.items(), key=lambda kv: -len(kv[1])):
        if not value:
            continue
        achar = re.compile(_borda(value[0], esquerda=True) + re.escape(value) + _borda(value[-1], esquerda=False))
        pos = 0
        while (m := achar.search(text, pos)) is not None:
            if any(ocupado[m.start():m.end()]):
                pos = m.start() + 1                  # cruza um valor já trocado: tenta de novo logo depois
                continue
            ocupado[m.start():m.end()] = [True] * (m.end() - m.start())
            trocas.append((m.start(), m.end(), "{" + name + "}"))
            pos = m.end()
    saida, fim = [], 0
    for ini, f, marcador in sorted(trocas):
        saida += [text[fim:ini], marcador]
        fim = f
    return "".join(saida) + text[fim:]


#: O nome público da troca, para quem a usa fora deste módulo (a identidade da etapa em `recipes.para_hash`).
trocar_valores_por_nomes = _sub_values


#: 30.83: a referência pública do fluxo, `f-` mais 12 hex ALEATÓRIOS. Nunca derivada do resumo, do comando nem do
#: `match_key`: quem conhece o nome não confirma o palpite pelo valor que sai em evento, `href` e log.
PREFIXO_DA_REF = "f-"


def ref_aleatoria(db: Database) -> str:
    """Uma referência nova, livre como id E como `ref_publico` (o fluxo novo usa a mesma nas duas colunas)."""
    while True:
        ref = f"{PREFIXO_DA_REF}{secrets.token_hex(6)}"
        if db.one("SELECT id FROM flows WHERE id=? OR ref_publico=?", (ref, ref)) is None:
            return ref


def ref_publica_do_fluxo(db: Database, flow_id: str) -> str:
    """A referência que sai do central (evento, `href`). Nunca cai no id (N2 da leitura da Ferramentas): sem a
    `ref_publico` (uma réplica no código antigo criou o fluxo depois da subida), preenche NA HORA; sem a linha (fluxo
    apagado), uma referência nova que não abre nada, em vez do id. Nesse caso cada chamada sorteia outra: a entrada e
    a saída de espera de um fluxo apagado não casam (a Canais só avisa a entrada; nota b da leitura)."""
    linha = db.one("SELECT ref_publico FROM flows WHERE id=?", (flow_id,))
    if linha is None:
        return ref_aleatoria(db)
    if linha["ref_publico"]:
        return str(linha["ref_publico"])
    sorteada = ref_aleatoria(db)
    db.execute("UPDATE flows SET ref_publico=? WHERE id=? AND ref_publico IS NULL", (sorteada, flow_id))
    gravada = db.scalar("SELECT ref_publico FROM flows WHERE id=?", (flow_id,))
    return str(gravada) if gravada else sorteada     # a linha sumiu entre os dois: a sorteada, nunca "None" (nota a)


def id_do_fluxo(db: Database, ref: str) -> str:
    """O id interno a partir do que chega numa rota: o próprio id (fluxo novo, ou o painel antigo) ou a `ref_publico`
    (o link de um aviso). Sem nenhum dos dois, devolve como veio: quem lê dá o 404 de sempre."""
    if db.one("SELECT id FROM flows WHERE id=?", (ref,)) is not None:
        return ref
    linha = db.one("SELECT id FROM flows WHERE ref_publico=?", (ref,))
    return str(linha["id"]) if linha is not None else ref


def preencher_refs_publicas(db: Database) -> int:
    """30.83: dá a referência aleatória a cada fluxo que ainda não tem (os de antes da migração 116). Roda na subida;
    idempotente (só as linhas sem ela, e o UPDATE confere de novo, para duas réplicas subindo juntas). O id antigo
    fica: as referências a ele não têm ON UPDATE CASCADE. Devolve quantas preencheu."""
    feitas = 0
    for linha in db.query("SELECT id FROM flows WHERE ref_publico IS NULL ORDER BY id"):
        cur = db.execute("UPDATE flows SET ref_publico=? WHERE id=? AND ref_publico IS NULL",
                         (ref_aleatoria(db), linha["id"]))
        feitas += int(cur.rowcount or 0)
    return feitas


# ------------------------------------------------------------------ o ensinado ainda sem prova (30.81)
def ensinado_em_prova(db: Database, row: Row) -> dict[str, str | None] | None:
    """O fluxo ensinado no modo treinamento que ainda espera a prova: `{persona, sessao}` (a persona que ensinou,
    `None` se a sessão não tinha), ou `None` quando não se aplica. Sai da espera com uma prova a favor, real e não
    invalidada (execução com `prova_fluxo_id` deste fluxo, depois do nascimento) ou com o "Confirmar que fica"
    explícito de uma PESSOA depois do nascimento (leitura da Reload, achado 3: adotar e desfazer, ou outra linha
    qualquer, não liberam; nem o sistema, nem a régua da plataforma, nem um treino). Desligado, não está ativo. O fluxo que não veio do treino, ou que não está ativo, não
    paga consulta."""
    fonte = str(row.get("source") or "")
    if not fonte.startswith(PREFIXO_DO_TREINO) or row.get("status", "active") != "active":
        return None
    sessao = fonte[len(PREFIXO_DO_TREINO):]
    ref, nasceu = f"fluxo:{row['id']}", str(row["created_at"] or "")
    r = db.one(
        "SELECT (SELECT profile_id FROM training_sessions WHERE id=?) AS persona,"
        " EXISTS (SELECT 1 FROM learning_evidence e JOIN runs ru ON ru.id = e.run_id"
        "  WHERE e.item_ref=? AND e.stance='for' AND e.simulated=0 AND ru.prova_fluxo_id=? AND e.observed_at>=?"
        "  AND NOT EXISTS (SELECT 1 FROM learning_evidence i WHERE i.item_ref = e.item_ref"
        "  AND i.origin_ref = e.origin_ref AND i.stance='invalida')) AS provado,"
        " EXISTS (SELECT 1 FROM learning_transitions t WHERE t.item_ref=? AND t.decided_at>=?"
        "  AND t.reason LIKE ? AND t.decided_by NOT IN (?,?) AND t.decided_by NOT LIKE ?) AS decidido",
        (sessao, ref, row["id"], nasceu, ref, nasceu, f"{CONFIRMADO_QUE_FICA}%", SISTEMA, PLATAFORMA,
         f"{PREFIXO_DO_TREINO}%"))
    if r is not None and (bool(r["provado"]) or bool(r["decidido"])):
        return None
    persona = r["persona"] if r is not None else None
    return {"persona": str(persona) if persona else None, "sessao": sessao}


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
                flow_id = ref_aleatoria(self.db)                 # 30.83: nada do resumo no id (ele sai em evento)
                self.db.execute(
                    "INSERT INTO flows(id, name, match_key, command_template, plan, app_id, source_run_id, status,"
                    " created_at, ref_publico) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (flow_id, plan.summary[:120], key, template, plano, plan.app_id, run["id"], status, now_iso(),
                     flow_id))
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
    def recusa_do_treino(self, command_template: str) -> str | None:
        """Por que o treino não pode gravar este comando (o 409 `duplicate_command` do `save` e da prévia, 31.86), ou
        `None`. Um fluxo da mesma `match_key` recusa, exceto o ensinado que a prova real desligou (30.84): esse renasce
        na mesma linha. Fase J: o mesmo comando nunca fica vivo como fluxo ativo E habilidade publicada (design
        §15.2); aqui só se LÊ a tabela de habilidades, como em `learn_from_run`."""
        key = _norm(command_template.strip())
        linha = self.db.one("SELECT id, status, source FROM flows WHERE match_key=?", (key,))
        if linha is not None and self._desligado_pela_prova(linha) is None:
            return "Já existe uma habilidade para este comando. Mude o comando ou desative a outra."
        if self.db.one("SELECT id FROM skill_versions WHERE state='published' AND match_key=?", (key,)):
            return ("Já existe uma habilidade versionada publicada para este comando. Mude o comando ou desabilite a "
                    "habilidade.")
        return None

    def _desligado_pela_prova(self, linha: Row) -> str | None:
        """30.84: o motivo com que a prova real desligou este fluxo ensinado, se esse desligamento ainda é a ÚLTIMA
        linha da trilha dele; senão `None`. O que uma pessoa desligou (ou mexeu depois da prova) segue bloqueando o
        reensino, e o adotado por uma habilidade também (é o caminho de volta da adoção)."""
        if linha["status"] != "disabled" or not str(linha["source"] or "").startswith(PREFIXO_DO_TREINO):
            return None
        if self.db.one("SELECT id FROM skill_definitions WHERE legacy_flow_id=?", (linha["id"],)) is not None:
            return None
        ultima = self.db.one("SELECT to_state, reason, decided_by FROM learning_transitions WHERE item_ref=?"
                             " ORDER BY id DESC LIMIT 1", (f"fluxo:{linha['id']}",))
        if (ultima is None or ultima["to_state"] != "disabled" or ultima["decided_by"] != SISTEMA
                or not str(ultima["reason"] or "").startswith(MOTIVO_DA_PROVA_DO_ENSINADO)):
            return None
        return str(ultima["reason"])

    def learn_from_plan(self, plan: Plan, command_template: str, *, source: str) -> str:
        """Fluxo a partir de um plano JÁ em forma de modelo (`{nome}` nos textos e nos parâmetros) — o que o modo
        treinamento produz. Mesmo formato do fluxo aprendido de execução: `match` não distingue a origem.

        30.84: o comando cujo fluxo ensinado a prova real desligou pode ser ensinado de novo. Renasce a MESMA linha
        (mesmo id e referência pública): plano, sessão e nascimento novos, ativo e de novo em espera de prova (o
        regime do 30.81 conta a prova e o Confirmar a partir do `created_at`, e as tentativas por sessão). A trilha
        diz que renasceu e por que tinha sido desligado."""
        template = command_template.strip()
        key = _norm(template)
        apps = [plan.app_id, *(s.app_id for s in plan.steps)]
        de: str | None = None
        with self.db.tx():
            # A recusa e a trilha lidas DENTRO da transação (N1 da leitura): uma pessoa que mexa no fluxo entre a
            # leitura e a escrita faz a linha não renascer; o CAS abaixo confere só o status.
            recusa = self.recusa_do_treino(template)
            if recusa is not None:
                raise ValueError(recusa)
            existente = self.db.one("SELECT id, status, source FROM flows WHERE match_key=?", (key,))
            antes = self._desligado_pela_prova(existente) if existente is not None else None
            if existente is not None and antes is not None:
                # CAS no status, como em `learn_from_run`: só renasce se ninguém a religou no meio.
                cur = self.db.execute(
                    "UPDATE flows SET name=?, command_template=?, plan=?, app_id=?, source_run_id=NULL,"
                    " status='active', uses=0, created_at=?, last_used_at=NULL, source=? WHERE id=? AND"
                    " status='disabled'",
                    (plan.summary[:120], template, plan.model_dump_json(), plan.app_id, now_iso(), source,
                     existente["id"]))
                if int(cur.rowcount or 0) != 1:
                    raise ValueError("Já existe uma habilidade para este comando. Mude o comando ou desative a outra.")
                flow_id, de = str(existente["id"]), "disabled"
                motivo = f"reensinado no modo treinamento (mesma linha); desligado antes: {antes}"[:300]
            else:
                flow_id = ref_aleatoria(self.db)         # 30.83: o resumo do treino pode trazer o valor demonstrado
                motivo = "ensinado no modo treinamento"
                self.db.execute(
                    "INSERT INTO flows(id, name, match_key, command_template, plan, app_id, source_run_id, created_at,"
                    " source, ref_publico) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (flow_id, plan.summary[:120], key, template, plan.model_dump_json(), plan.app_id, None,
                     now_iso(), source, flow_id))
            self.set_required_apps(flow_id, [a for a in apps if a])
            if self.politica is not None:
                # Treino é decisão de pessoa (D1): publica na hora, e a trilha diz de qual demonstração veio.
                self.politica.mudou(MudancaDoFluxo(flow_id=flow_id, de=de, para="active", motivo=motivo, por=source))
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
    def match(self, command: str, profile_ids: list[str | None] | None = None, *,
              sem_ensino_em_prova: bool = False) -> tuple[Row, Plan] | None:
        """Comando novo × modelos conhecidos. Casa o texto inteiro; cada {nome} captura o valor novo.

        `profile_ids` (item 13.2): os perfis dos aparelhos da execução. Fluxo com escopo (habilidade treinada para
        perfis/grupos) só casa quando TODOS eles estão no escopo — um aparelho fora dele planejaria sozinho, e o
        plano é um só por execução. `None` = prévia sem aparelhos (custo, apps exigidos): qualquer fluxo serve.

        30.81: o fluxo ensinado ainda sem prova (`ensinado_em_prova`) só casa quando TODOS os perfis são a persona que
        ensinou; a sessão sem persona não casa em lugar nenhum até a prova. A prévia sem aparelhos casa como antes.
        """
        # F1 (31.89): entre moldes que casam o mesmo comando ganha o MAIS ESPECÍFICO (o critério do resolvedor v2,
        # `matching.specificity`: mais texto fixo, depois menos parâmetros), não o mais usado: "curtir o post de {p}"
        # vence "curtir {x}" mesmo com menos usos. A ordem do SQL (`uses DESC, created_at`) fica como desempate: o
        # `sorted` é estável, então só a especificidade a reordena (empate de tudo segue o comportamento de antes).
        ativos = sorted(self.db.query("SELECT * FROM flows WHERE status='active' ORDER BY uses DESC, created_at"),
                        key=lambda r: specificity(r["command_template"]), reverse=True)
        for row in ativos:
            if profile_ids is not None and not self._no_escopo(row["id"], profile_ids):
                continue
            values = self._extract(row["command_template"], command)
            if values is None:
                continue
            if profile_ids is not None and self._restrito_ao_ensino(row, profile_ids):
                continue
            if sem_ensino_em_prova and ensinado_em_prova(self.db, row) is not None:
                continue
            plan = self._plano_com_valores(row, values, provider="fluxo")
            if plan is None:
                continue                              # faltou valor para algum parâmetro: não é este fluxo
            return row, plan
        return None

    def ativo_para(self, command: str) -> tuple[Row, Plan] | None:
        """30.81 (achado 4 da Reload): o fluxo ativo do comando para a VALIDAÇÃO (sem aparelhos), sem o ensinado que
        ainda espera a prova: para ela, ele ainda não é o fluxo ativo de ninguém além de quem ensinou."""
        return self.match(command, None, sem_ensino_em_prova=True)

    # ------------------------------------------------------------------ o ensinado ainda sem prova (30.81)
    def _restrito_ao_ensino(self, row: Row, profile_ids: list[str | None]) -> bool:
        """O ensinado sem prova fica fora do `match` quando algum perfil da execução não é a persona que ensinou."""
        espera = ensinado_em_prova(self.db, row)
        if espera is None:
            return False
        persona = espera["persona"]
        return persona is None or not profile_ids or any(p != persona for p in profile_ids)

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
