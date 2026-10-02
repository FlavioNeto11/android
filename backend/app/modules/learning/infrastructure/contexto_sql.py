"""O contexto das tentativas de um grupo de falha: o que a infraestrutura lê para o diagnóstico (item 30.13).

Só leitura, nos dois dialetos (nada de `GROUP_CONCAT`, `json_extract` nem `RETURNING`; `IN (...)` em lotes). Cada
fato vem de UMA tabela e a junção que não é exata sai marcada:

- **receita**: `attempts.recipe_id` quando a tentativa o gravou (exato); senão, a receita da MESMA etapa
  (`recipes.step_hash` = `steps.template_hash`, no pacote) quando a etapa foi conduzida por receita — `aproximada`.
  O estado de versão vem de `domain/versao.py` (a mesma régua do detalhe do livro);
- **lição**: `learning_exposures` do braço `with` na unidade `step:<id>` (a do planejador, `plan:<run_id>`, fica de fora:
  o grupo é de etapa);
- **tela**: `attempts.failure_screen` (só o nome do pacote; a aprendida acha o item do livro pelo `content.tela`);
- **fluxo e habilidade**: `runs.flow_id`, `steps.skill_id`/`runs.skill_id` (a habilidade legada `flow:<id>` é o fluxo);
- **comparação entre aparelhos**: as outras etapas da MESMA execução com o mesmo `template_hash` (a regra de
  `taskqueue/aproveitamento.py`);
- **chamou pessoa**: `tela_desconhecida_chamou_pessoa` ligado à tentativa ou à etapa.

Nenhum texto de tela, de comando ou de erro sai daqui: só ids, nomes de pacote e contagens.
"""
from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterator, Sequence

from app.db import Database, Row
from app.modules.learning.domain.diagnostico import (ContextoDaTentativa, EstatisticaDaLicao, ReceitaDaTentativa,
                                                     TelaDaTentativa)
from app.modules.learning.domain.relacoes import PREFIXO_DE_FLUXO_LEGADO
from app.modules.learning.domain.telas import PREFIXO as PREFIXO_DE_TELA_APRENDIDA
from app.modules.learning.domain.versao import ReceitaDaChave, VersaoViva, agrupar_vivas, estado_da_receita
from app.modules.learning.domain.vocabulario import SignalKind
from app.modules.learning.infrastructure import linhas
from app.taskqueue.projecao import app_da_etapa

#: Tamanho do lote de um `IN (...)` (o limite de parâmetros do SQLite antigo é 999).
_LOTE = 400
_DA_RECEITA = frozenset({"recipe", "recipe+ai"})
#: Desfechos de etapa que contam como "falhou" na comparação entre aparelhos.
_FALHA = frozenset({"failed", "uncertain", "interrupted"})
#: Desfecho de exposição que é sucesso (`efeito.SUCESSO`); o resto, inclusive `unverified`, conta como falha.
_SUCESSO = ("succeeded", "completed")


def _lotes(itens: Sequence[str]) -> Iterator[Sequence[str]]:
    for i in range(0, len(itens), _LOTE):
        yield itens[i:i + _LOTE]


def _marcas(n: int) -> str:
    return ",".join("?" for _ in range(n))


class ContextoDeFalhaSql:
    def __init__(self, db: Database) -> None:
        self._db = db

    # ================================================================== a tentativa
    def contextos(self, attempt_ids: Sequence[str]) -> dict[str, ContextoDaTentativa]:
        ids = sorted(set(attempt_ids))
        if not ids:
            return {}
        pacotes = {linhas.texto(r, "id"): linhas.texto_ou_nulo(r, "package") or linhas.texto(r, "id")
                   for r in self._db.query("SELECT id, package FROM apps")}
        lidas: list[Row] = []
        for lote in _lotes(ids):
            lidas += self._db.query(
                "SELECT a.id AS attempt_id, a.step_id, a.strategy, a.recipe_id, a.failure_screen, s.run_id,"
                " s.instance_id, s.driven_by, s.template_hash, s.capability, s.app_id, s.skill_id, s.skill_version,"
                " r.flow_id, r.skill_id AS run_skill_id, r.skill_version AS run_skill_version, r.app_ids"
                " FROM attempts a JOIN steps s ON s.id = a.step_id JOIN runs r ON r.id = s.run_id"
                f" WHERE a.id IN ({_marcas(len(lote))})", tuple(lote))
        if not lidas:
            return {}
        app_de = {linhas.texto(r, "attempt_id"): pacotes.get(app_da_etapa(r["app_id"], r["app_ids"]),
                                                             app_da_etapa(r["app_id"], r["app_ids"]))
                  for r in lidas}
        receitas = self._receitas(lidas, app_de)
        licoes = self._licoes_das_etapas(sorted({linhas.texto(r, "step_id") for r in lidas}))
        irmas = self._irmas(lidas)
        chamou = self._chamou_pessoa(lidas)
        telas = self._telas(lidas, app_de)
        saida: dict[str, ContextoDaTentativa] = {}
        for r in lidas:
            aid, sid = linhas.texto(r, "attempt_id"), linhas.texto(r, "step_id")
            fluxo = linhas.texto_ou_nulo(r, "flow_id")
            habilidade = linhas.texto_ou_nulo(r, "skill_id") or linhas.texto_ou_nulo(r, "run_skill_id")
            versao = r["skill_version"] if linhas.texto_ou_nulo(r, "skill_id") else r["run_skill_version"]
            if habilidade and habilidade.startswith(PREFIXO_DE_FLUXO_LEGADO):
                fluxo = fluxo or habilidade[len(PREFIXO_DE_FLUXO_LEGADO):]
                habilidade = None
            ok, falha = irmas.get(aid, (0, 0))
            saida[aid] = ContextoDaTentativa(
                attempt_id=aid, step_id=sid, run_id=linhas.texto(r, "run_id"),
                instance_id=linhas.texto_ou_nulo(r, "instance_id"), driven_by=linhas.texto_ou_nulo(r, "driven_by"),
                estrategia=linhas.texto_ou_nulo(r, "strategy"), capability=linhas.texto_ou_nulo(r, "capability"),
                receita=receitas.get(aid), licoes=tuple(sorted(licoes.get(sid, ()))), tela=telas.get(aid),
                fluxo=f"fluxo:{fluxo}" if fluxo else None,
                habilidade=(f"habilidade:{habilidade}" + (f"@{versao}" if isinstance(versao, int) else ""))
                if habilidade else None,
                outros_ok=ok, outros_falha=falha, chamou_pessoa=aid in chamou or sid in chamou)
        return saida

    # ------------------------------------------------------------------ receita
    def _receitas(self, lidas: Sequence[Row], app_de: dict[str, str]) -> dict[str, ReceitaDaTentativa]:
        exatas = sorted({linhas.inteiro(r, "recipe_id") for r in lidas if r["recipe_id"] is not None})
        conduzidas = [r for r in lidas if r["recipe_id"] is None and r["template_hash"] is not None
                      and (linhas.texto_ou_nulo(r, "driven_by") in _DA_RECEITA
                           or "recipe" in (linhas.texto_ou_nulo(r, "strategy") or "").split(">"))]
        a_aproximar = {linhas.texto(r, "attempt_id") for r in conduzidas}
        todas: dict[int, Row] = {}
        for i in range(0, len(exatas), _LOTE):
            lote = exatas[i:i + _LOTE]
            for x in self._db.query("SELECT * FROM recipes WHERE id IN (" + _marcas(len(lote)) + ")", tuple(lote)):
                todas[linhas.inteiro(x, "id")] = x
        pares = {(app_de[linhas.texto(r, "attempt_id")], linhas.texto(r, "template_hash")) for r in conduzidas}
        pares |= {(linhas.texto(x, "app_package"), linhas.texto(x, "step_hash")) for x in todas.values()}
        if pares:
            hashes = sorted({h for _, h in pares})
            apps = sorted({a for a, _ in pares})
            for lote in _lotes(hashes):
                for x in self._db.query("SELECT * FROM recipes WHERE step_hash IN (" + _marcas(len(lote)) + ")"
                                        " AND app_package IN (" + _marcas(len(apps)) + ")", (*lote, *apps)):
                    todas[linhas.inteiro(x, "id")] = x
        por_chave: dict[tuple[str, str, str, str], list[Row]] = defaultdict(list)
        for x in todas.values():
            por_chave[_chave_da_receita(x)].append(x)
        vivas: dict[str, frozenset[str]] = {}

        def estado(x: Row) -> str:
            pacote = linhas.texto(x, "app_package")
            if pacote not in vivas:
                vivas[pacote] = frozenset(v.versao for v in self._vivas(pacote))
            da_chave = [_da_chave(o) for o in por_chave[_chave_da_receita(x)]]
            return estado_da_receita(_da_chave(x), vivas=vivas[pacote], da_chave=da_chave).value

        saida: dict[str, ReceitaDaTentativa] = {}
        for r in lidas:
            aid = linhas.texto(r, "attempt_id")
            if r["recipe_id"] is not None:
                x = todas.get(linhas.inteiro(r, "recipe_id"))
                aproximada = False
            else:
                aproximada = True
                candidatas = [x for x in todas.values() if linhas.texto(x, "app_package") == app_de[aid]
                              and linhas.texto(x, "step_hash") == linhas.texto_ou_nulo(r, "template_hash")] \
                    if r in conduzidas else []
                x = max(candidatas, key=lambda c: (linhas.texto(c, "status") == "active", linhas.inteiro(c, "id")),
                        default=None)
            if x is not None:
                saida[aid] = ReceitaDaTentativa(f"receita:{linhas.inteiro(x, 'id')}", linhas.texto(x, "status"),
                                                linhas.texto(x, "app_version"), estado(x), aproximada)
        return saida

    def _vivas(self, pacote: str) -> tuple[VersaoViva, ...]:
        """As versões do app observadas hoje em aparelho ativo (a mesma consulta de `fontes.py`, §7)."""
        return agrupar_vivas(
            (linhas.texto(r, "v"), linhas.inteiro(r, "n")) for r in self._db.query(
                "SELECT d.observed_version_name AS v, COUNT(*) AS n FROM device_app_state d WHERE d.package_name=?"
                " AND d.observed_version_name IS NOT NULL AND d.observed_version_name <> '' AND d.state <> 'missing'"
                " AND NOT EXISTS (SELECT 1 FROM instances i WHERE i.id = d.instance_id AND i.retired_at IS NOT NULL)"
                " GROUP BY d.observed_version_name", (pacote,)))

    # ------------------------------------------------------------------ lição
    def _licoes_das_etapas(self, etapas: Sequence[str]) -> dict[str, set[str]]:
        saida: dict[str, set[str]] = defaultdict(set)
        unidades = [f"step:{e}" for e in etapas]
        for lote in _lotes(unidades):
            for e in self._db.query(
                    "SELECT e.unit_id, e.item_id FROM learning_exposures e JOIN learning_items i ON i.id = e.item_id"
                    " WHERE e.arm = 'with' AND i.kind = 'licao' AND e.unit_id IN (" + _marcas(len(lote)) + ")",
                    tuple(lote)):
                saida[linhas.texto(e, "unit_id")[len("step:"):]].add(linhas.texto(e, "item_id"))
        return saida

    def licoes(self, refs: Sequence[str]) -> dict[str, EstatisticaDaLicao]:
        """O braço de controle de cada lição: exposições JÁ preenchidas (`outcome` conhecido), todas, não só as do grupo."""
        ids = sorted(set(refs))
        itens: dict[str, Row] = {}
        contagem: dict[str, dict[str, tuple[int, int]]] = defaultdict(dict)
        for lote in _lotes(ids):
            marcas = _marcas(len(lote))
            for i in self._db.query(f"SELECT id, state, state_detail FROM learning_items WHERE id IN ({marcas})",
                                    tuple(lote)):
                itens[linhas.texto(i, "id")] = i
            for e in self._db.query(
                    "SELECT item_id, arm, COUNT(*) AS n, SUM(CASE WHEN outcome IN (?,?) THEN 0 ELSE 1 END) AS f"
                    f" FROM learning_exposures WHERE item_id IN ({marcas}) AND outcome IS NOT NULL"
                    " GROUP BY item_id, arm", (*_SUCESSO, *lote)):
                contagem[linhas.texto(e, "item_id")][linhas.texto(e, "arm")] = (linhas.inteiro(e, "n"),
                                                                                int(linhas.real(e, "f")))
        saida: dict[str, EstatisticaDaLicao] = {}
        for ref, i in itens.items():
            com = contagem[ref].get("with", (0, 0))
            sem = contagem[ref].get("holdout", (0, 0))
            saida[ref] = EstatisticaDaLicao(ref, linhas.texto(i, "state"), linhas.texto_ou_nulo(i, "state_detail"),
                                            com[0], com[1], sem[0], sem[1])
        return saida

    # ------------------------------------------------------------------ comparação entre aparelhos
    def _irmas(self, lidas: Sequence[Row]) -> dict[str, tuple[int, int]]:
        """Por tentativa: (aparelhos da execução onde a mesma etapa terminou bem, onde só falhou)."""
        execucoes = sorted({linhas.texto(r, "run_id") for r in lidas if r["template_hash"] is not None})
        por_etapa: dict[tuple[str, str], dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        for lote in _lotes(execucoes):
            for s in self._db.query(
                    "SELECT run_id, template_hash, instance_id, status FROM steps WHERE template_hash IS NOT NULL"
                    " AND instance_id IS NOT NULL AND run_id IN (" + _marcas(len(lote)) + ")", tuple(lote)):
                por_etapa[(linhas.texto(s, "run_id"), linhas.texto(s, "template_hash"))][
                    linhas.texto(s, "instance_id")].add(linhas.texto(s, "status"))
        saida: dict[str, tuple[int, int]] = {}
        for r in lidas:
            if r["template_hash"] is None:
                continue
            aparelhos = por_etapa.get((linhas.texto(r, "run_id"), linhas.texto(r, "template_hash")), {})
            outros = {i: st for i, st in aparelhos.items() if i != linhas.texto_ou_nulo(r, "instance_id")}
            saida[linhas.texto(r, "attempt_id")] = (
                sum(1 for st in outros.values() if "succeeded" in st),
                sum(1 for st in outros.values() if "succeeded" not in st and st & _FALHA))
        return saida

    # ------------------------------------------------------------------ tela desconhecida que chamou pessoa
    def _chamou_pessoa(self, lidas: Sequence[Row]) -> set[str]:
        """Ids (de tentativa e de etapa) com o sinal `tela_desconhecida_chamou_pessoa`."""
        achados: set[str] = set()
        for coluna, valores in (("attempt_id", sorted({linhas.texto(r, "attempt_id") for r in lidas})),
                                ("step_id", sorted({linhas.texto(r, "step_id") for r in lidas}))):
            for lote in _lotes(valores):
                for s in self._db.query(
                        f"SELECT {coluna} AS id FROM learning_signals WHERE kind = ? AND {coluna} IN ("
                        + _marcas(len(lote)) + ")", (SignalKind.TELA_DESCONHECIDA_CHAMOU_PESSOA.value, *lote)):
                    achados.add(linhas.texto(s, "id"))
        return achados

    # ------------------------------------------------------------------ tela
    def _telas(self, lidas: Sequence[Row], app_de: dict[str, str]) -> dict[str, TelaDaTentativa]:
        aprendidas: dict[str, dict[str, tuple[str, str]]] = {}
        saida: dict[str, TelaDaTentativa] = {}
        for r in lidas:
            nome = (linhas.texto_ou_nulo(r, "failure_screen") or "").strip()
            if not nome:
                continue
            aid = linhas.texto(r, "attempt_id")
            pacote = app_de[aid]
            item = estado = None
            if nome.startswith(PREFIXO_DE_TELA_APRENDIDA):
                if pacote not in aprendidas:
                    aprendidas[pacote] = _itens_de_tela(self._db, pacote)
                item, estado = aprendidas[pacote].get(nome, (None, None))
            saida[aid] = TelaDaTentativa(f"tela:{pacote}/{nome}", item, estado)
        return saida


def _itens_de_tela(db: Database, pacote: str) -> dict[str, tuple[str, str]]:
    """Nome da tela aprendida → (id do item, estado), dos itens `tela` do pacote. O nome mora no `content.tela`."""
    achados: dict[str, tuple[str, str]] = {}
    for i in db.query("SELECT id, state, content FROM learning_items WHERE kind = 'tela' AND scope_app = ?", (pacote,)):
        try:
            conteudo = json.loads(linhas.texto(i, "content"))
        except ValueError:
            continue
        nome = conteudo.get("tela") if isinstance(conteudo, dict) else None
        if isinstance(nome, str):
            achados[nome] = (linhas.texto(i, "id"), linhas.texto(i, "state"))
    return achados


def _chave_da_receita(x: Row) -> tuple[str, str, str, str]:
    """A identidade da receita sem a versão do app: pacote, assinatura, variante e `step_hash`."""
    return (linhas.texto(x, "app_package"), linhas.texto(x, "app_signature"), linhas.texto(x, "variant"),
            linhas.texto(x, "step_hash"))


def _da_chave(x: Row) -> ReceitaDaChave:
    return ReceitaDaChave(ref=str(linhas.inteiro(x, "id")), app_version=linhas.texto(x, "app_version"),
                          versao=linhas.inteiro(x, "version"), status=linhas.texto(x, "status"),
                          replay_ok=linhas.inteiro(x, "replay_ok"), consecutive_fail=linhas.inteiro(x, "consecutive_fail"),
                          criada_em=linhas.texto(x, "created_at"))


__all__ = ["ContextoDeFalhaSql"]
