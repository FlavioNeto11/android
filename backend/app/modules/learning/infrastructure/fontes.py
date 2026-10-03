"""As fontes nativas do livro (receita, fluxo, habilidade, memória), lidas das tabelas donas e entregues já no formato
do livro. Só LEITURA: quem escreve cada uma continua sendo quem sempre escreveu (e o status de receita e fluxo,
pelo repositório do aprendizado, com trilha).

O estado sai pelo mapeamento testado do domínio (`domain/livro.py`); o efeito externo, pelo conteúdo (ação `commit`
na receita, etapa `side_effect` no fluxo). A memória da persona sai só como CONTAGEM por perfil — o conteúdo nunca.
O app é mostrado pelo PACOTE (chave canônica, 30.2). O fluxo e a habilidade guardam o id do app (a receita, o
pacote); `ResolvedorDeApp` leva o id ao pacote pela tabela `apps` e pelo registro de apps, para o filtro `app=` casar
as fontes com o mesmo valor. O que não resolve cai no balde `APP_NAO_RESOLVIDO` (visível e contado, com o id cru em
`app_ref`) e nunca some. A memória é da persona: fica fora do eixo de app (`app=None`), nunca no balde.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence

from app.db import Database, Row
from app.modules.learning.domain.conteudo import (PREFIXO_DE_TREINO, EtapaDeOrigem, ReceitaLida, Vizinha,
                                                  capability_da_linha_da_receita, capability_da_receita,
                                                  fluxo_legivel, habilidade_legivel, receita_legivel)
from app.modules.learning.domain.evidencia_invalida import run_da_etapa, run_valida
from app.modules.learning.domain.livro import (EntradaDoLivro, apps_na_ordem_do_plano, escopo_da_receita,
                                               escopo_do_fluxo, estado_nativo, fluxo_tem_efeito, hash_da_receita,
                                               receita_tem_efeito)
from app.modules.learning.domain.relacoes import Sucessora
from app.modules.learning.domain.versao import (ReceitaDaChave, VersaoViva, agrupar_vivas, quadro_da_receita,
                                                versao_canonica)
from app.modules.learning.domain.vocabulario import APP_NAO_RESOLVIDO, LivroKind, Origem
from app.modules.learning.infrastructure import linhas
from app.modules.skills.domain.document import JsonObject, JsonValue, content_hash

_ORIGEM_DA_HABILIDADE = {"teaching": Origem.ENSINO, "legacy_flow": Origem.EXECUCAO, "run": Origem.EXECUCAO,
                         "manual": Origem.PESSOA, "import": Origem.PESSOA}


class ResolvedorDeApp:
    """O id de app de um fluxo ou de uma habilidade → o pacote (a chave canônica do livro), ou `None`.

    Ordem: o id é de uma linha de `apps` (vale o `package` dela) → o id já é um pacote conhecido (`apps.package` ou o
    registro de apps) → não resolve. Quando o app principal não resolve (ou não existe), os apps EXIGIDOS entram, mas
    só se tudo o que resolve dá o MESMO pacote: dois pacotes diferentes é ambiguidade, e adivinhar é pior que
    mostrar no balde."""

    def __init__(self, por_id: dict[str, str], pacotes_conhecidos: Iterable[str]) -> None:
        self._por_id = por_id
        self._pacotes = frozenset(por_id.values()) | frozenset(pacotes_conhecidos)

    def pacote(self, app_id: str | None) -> str | None:
        if not app_id:
            return None
        if app_id in self._por_id:
            return self._por_id[app_id]
        return app_id if app_id in self._pacotes else None

    def resolver(self, principal: str | None, exigidos: Iterable[str] = ()) -> str | None:
        direto = self.pacote(principal)
        if direto is not None:
            return direto
        achados = {p for p in (self.pacote(a) for a in exigidos) if p is not None}
        return next(iter(achados)) if len(achados) == 1 else None


def _de_app(pacote: str | None, bruto: str | None) -> tuple[str, str | None]:
    """(`app`, `app_ref`) da entrada: o pacote, ou o balde com o id cru (quando havia um)."""
    return (pacote, None) if pacote is not None else (APP_NAO_RESOLVIDO, bruto or None)


class FontesSql:
    def __init__(self, db: Database, *, pacotes_do_registro: Callable[[], Iterable[str]] = lambda: ()) -> None:
        """`pacotes_do_registro`: os pacotes do registro de apps (a composição passa o real; os testes, um falso)."""
        self._db = db
        self._registro = pacotes_do_registro

    def pacotes_de_teste(self) -> frozenset[str]:
        return frozenset(p for r in self._db.query("SELECT package FROM apps WHERE category='qa'")
                         if (p := linhas.texto_ou_nulo(r, "package")))

    def _resolvedor(self) -> ResolvedorDeApp:
        por_id = {linhas.texto(r, "id"): linhas.texto_ou_nulo(r, "package") or linhas.texto(r, "id")
                  for r in self._db.query("SELECT id, package FROM apps")}
        return ResolvedorDeApp(por_id, self._registro())

    def _exigidos(self, sql: str, chave: str, so: str | None) -> dict[str, list[str]]:
        rows = self._db.query(sql + (f" WHERE {chave}=?" if so else "") + " ORDER BY app_id", (so,) if so else ())
        saida: dict[str, list[str]] = {}
        for r in rows:
            saida.setdefault(linhas.texto(r, chave), []).append(linhas.texto(r, "app_id"))
        return saida

    # ------------------------------------------------------------------ receita
    def receitas(self) -> list[EntradaDoLivro]:
        return [_receita(r) for r in self._db.query(_RECEITAS + " ORDER BY r.app_package, r.step_key, r.version")]

    def receita(self, ref: str) -> EntradaDoLivro | None:
        try:
            recipe_id = int(ref)
        except ValueError:
            return None
        row = self._db.one(_RECEITAS + " WHERE r.id=?", (recipe_id,))
        return _receita(row) if row else None

    # ------------------------------------------------------------------ conteúdo legível (30.3)
    def conteudo(self, kind: LivroKind, ref: str) -> JsonObject | None:
        """O conteúdo legível de uma fonte nativa (receita, fluxo, habilidade), só do que já está no banco. `None`:
        o tipo não tem conteúdo nativo (item do livro e memória) ou a linha sumiu."""
        if kind is LivroKind.RECEITA:
            return self._conteudo_da_receita(ref)
        if kind is LivroKind.FLUXO:
            return self._conteudo_do_fluxo(ref)
        if kind is LivroKind.HABILIDADE:
            return self._conteudo_da_habilidade(ref)
        return None

    def _conteudo_da_receita(self, ref: str) -> JsonObject | None:
        try:
            recipe_id = int(ref)
        except ValueError:
            return None
        row = self._db.one("SELECT * FROM recipes WHERE id=?", (recipe_id,))
        if row is None:
            return None
        r = _receita_lida(row)
        treino = (r.aprendida_de or "").startswith(PREFIXO_DE_TREINO)
        etapa = None if not r.aprendida_de or treino else self._etapa_de_origem(r.aprendida_de)
        mesmos = [] if etapa is not None and etapa.capability else self._capabilities_do_template(r.app, r.step_hash)
        chave = (r.app, r.app_version, r.assinatura, r.variante, r.step_hash)
        base = ("SELECT id, version, status FROM recipes WHERE app_package=? AND app_version=? AND app_signature=?"
                " AND variant=? AND step_hash=? AND version")
        anterior = self._db.one(base + "<? ORDER BY version DESC LIMIT 1", (*chave, r.versao))
        seguinte = self._db.one(base + ">? ORDER BY version ASC LIMIT 1", (*chave, r.versao))
        return receita_legivel(r, etapa=etapa, do_mesmo_template=mesmos,
                               anterior=_vizinha(anterior), seguinte=_vizinha(seguinte))

    # ------------------------------------------------------------------ capability na linha
    def capabilities_das_receitas(self, refs: Sequence[str]) -> dict[str, str | None]:
        """A capability de cada receita para a LINHA da lista: a mesma regra do detalhe (`capability_da_receita`:
        etapa de origem e, sem ela, as etapas com o mesmo `step_hash` no mesmo app), mas em lote: uma consulta de
        receitas, uma de etapas de origem, uma de etapas por `template_hash` e uma de apps, por lote de ids."""
        ids = sorted({int(r) for r in refs if r.isdigit()})
        receitas: list[Row] = []
        for lote in linhas.lotes(ids):
            receitas += self._db.query(
                "SELECT id, app_package, step_hash, learned_from_step FROM recipes"
                f" WHERE id IN ({linhas.marcas(len(lote))})", tuple(lote))
        if not receitas:
            return {}
        etapas_de = [s for s in {linhas.texto_ou_nulo(r, "learned_from_step") or "" for r in receitas}
                     if s and not s.startswith(PREFIXO_DE_TREINO)]
        origem: dict[str, EtapaDeOrigem] = {}
        for lote in linhas.lotes(sorted(etapas_de)):
            for e in self._db.query(f"SELECT id, run_id, capability FROM steps WHERE id IN ({linhas.marcas(len(lote))})",
                                    tuple(lote)):
                origem[linhas.texto(e, "id")] = EtapaDeOrigem(linhas.texto(e, "id"), linhas.texto(e, "run_id"),
                                                              linhas.texto_ou_nulo(e, "capability"))
        # Só as receitas SEM capability na origem precisam do `step_hash`: o mesmo atalho do detalhe.
        sem_origem = [r for r in receitas
                      if not (o := origem.get(linhas.texto_ou_nulo(r, "learned_from_step") or "")) or not o.capability]
        por_hash = self._capabilities_por_template(sorted({linhas.texto(r, "step_hash") for r in sem_origem}))
        pacotes = self._pacotes_dos_apps() if por_hash else {}
        saida: dict[str, str | None] = {}
        for r in receitas:
            etapa = origem.get(linhas.texto_ou_nulo(r, "learned_from_step") or "")
            mesmos = [] if etapa is not None and etapa.capability else [
                cap for cap, ids_do_app in por_hash.get(linhas.texto(r, "step_hash"), ())
                if not ids_do_app or linhas.texto(r, "app_package") in {pacotes.get(i, i) for i in ids_do_app}]
            saida[str(linhas.inteiro(r, "id"))] = capability_da_linha_da_receita(capability_da_receita(etapa, mesmos))
        return saida

    def _pacotes_dos_apps(self) -> dict[str, str]:
        return {linhas.texto(a, "id"): linhas.texto_ou_nulo(a, "package") or linhas.texto(a, "id")
                for a in self._db.query("SELECT id, package FROM apps")}

    def _capabilities_por_template(self, hashes: Sequence[str]) -> dict[str, list[tuple[str, list[str]]]]:
        """Por `template_hash`: (capability, ids de app da etapa). Os ids de app são o da etapa ou, sem ele, os da
        execução; lista vazia = não dá para saber o app (conta, como no detalhe: esconder a dúvida seria pior)."""
        saida: dict[str, list[tuple[str, list[str]]]] = {}
        for lote in linhas.lotes(list(hashes)):
            for s in self._db.query(
                    "SELECT DISTINCT s.template_hash, s.capability, s.app_id, r.app_ids FROM steps s"
                    " JOIN runs r ON r.id = s.run_id WHERE s.capability IS NOT NULL AND s.capability <> ''"
                    f" AND s.template_hash IN ({linhas.marcas(len(lote))})", tuple(lote)):
                app_id = linhas.texto_ou_nulo(s, "app_id")
                ids = [app_id] if app_id else _ids_do_json(linhas.texto_ou_nulo(s, "app_ids"))
                saida.setdefault(linhas.texto(s, "template_hash"), []).append((linhas.texto(s, "capability"), ids))
        return saida

    # ------------------------------------------------------------------ versão (30.6)
    def vivas(self, app: str) -> tuple[VersaoViva, ...]:
        """As versões do app observadas HOJE em aparelho ativo, com o número de aparelhos (§7). Aparelho aposentado
        (`instances.retired_at`) e app ausente (`missing`) não contam; a linha sem `instances` (teste, aparelho já
        removido) conta como viva: o `NOT EXISTS` só exclui o que se sabe aposentado."""
        # Nome E código: a receita grava `nome(código)` (`domain/versao.py`), e comparar só o nome a marcava fora do parque.
        return agrupar_vivas(
            (versao_canonica(linhas.texto(r, "v"), linhas.inteiro_ou_nulo(r, "c")), linhas.inteiro(r, "n"))
            for r in self._db.query(
                "SELECT d.observed_version_name AS v, d.observed_version_code AS c, COUNT(*) AS n FROM device_app_state d"
                " WHERE d.package_name=? AND d.observed_version_name IS NOT NULL AND d.observed_version_name <> ''"
                " AND d.state <> 'missing'"
                " AND NOT EXISTS (SELECT 1 FROM instances i WHERE i.id = d.instance_id AND i.retired_at IS NOT NULL)"
                " GROUP BY d.observed_version_name, d.observed_version_code", (app,)))

    def versao(self, kind: LivroKind, ref: str) -> JsonObject | None:
        """O quadro de versão de uma receita (`domain/versao.py`): a chave exata (pacote, assinatura, variante,
        `step_hash`) em TODAS as versões do app, contra as versões vivas. `None`: o tipo não tem quadro nativo aqui
        ou a linha sumiu."""
        if kind is not LivroKind.RECEITA:
            return None
        try:
            recipe_id = int(ref)
        except ValueError:
            return None
        row = self._db.one("SELECT * FROM recipes WHERE id=?", (recipe_id,))
        if row is None:
            return None
        pacote = linhas.texto(row, "app_package")
        da_chave = [_receita_da_chave(r) for r in self._db.query(
            "SELECT id, app_version, version, status, replay_ok, consecutive_fail, created_at FROM recipes"
            " WHERE app_package=? AND app_signature=? AND variant=? AND step_hash=? ORDER BY app_version, version",
            (pacote, linhas.texto(row, "app_signature"), linhas.texto(row, "variant"), linhas.texto(row, "step_hash")))]
        propria = next(o for o in da_chave if o.ref == str(recipe_id))
        return quadro_da_receita(propria, app=pacote, da_chave=da_chave, vivas=self.vivas(pacote))

    # ------------------------------------------------------------------ relações (30.7)
    def sucessoras_da_habilidade(self, skill_id: str, versao: int) -> list[Sucessora]:
        """As versões do mesmo `skill_id` cujo `parent_version` é esta. A leitura do pai (`parent_version` da própria
        versão) já vem no `conteudo`; só o caminho de volta precisa de uma consulta."""
        return [Sucessora(linhas.texto(r, "id"), linhas.inteiro(r, "version"), linhas.texto(r, "state"))
                for r in self._db.query("SELECT id, version, state FROM skill_versions WHERE skill_id=?"
                                        " AND parent_version=? ORDER BY version", (skill_id, versao))]

    def _etapa_de_origem(self, step_id: str) -> EtapaDeOrigem | None:
        row = self._db.one("SELECT id, run_id, capability FROM steps WHERE id=?", (step_id,))
        if row is None:
            return None
        return EtapaDeOrigem(linhas.texto(row, "id"), linhas.texto(row, "run_id"),
                             linhas.texto_ou_nulo(row, "capability"))

    def _capabilities_do_template(self, pacote: str, step_hash: str) -> list[str]:
        """As capabilities das etapas com o mesmo `template_hash`, no mesmo app. O app da etapa é o dela
        (`steps.app_id`) ou, sem ele, o da execução (`runs.app_ids`); id de app vira pacote pela tabela `apps`. Etapa
        cujo app não dá para saber (execução antiga, sem `app_ids`) conta: o `step_hash` já amarra chave, pós-condição
        e guardas, e esconder a dúvida seria pior que marcá-la `ambigua`."""
        por_id = {linhas.texto(a, "id"): linhas.texto_ou_nulo(a, "package") or linhas.texto(a, "id")
                  for a in self._db.query("SELECT id, package FROM apps")}
        achadas = self._db.query(
            "SELECT DISTINCT s.capability, s.app_id, r.app_ids FROM steps s JOIN runs r ON r.id = s.run_id"
            " WHERE s.template_hash=? AND s.capability IS NOT NULL AND s.capability <> ''", (step_hash,))
        capabilities: list[str] = []
        for s in achadas:
            ids = [linhas.texto_ou_nulo(s, "app_id")] if linhas.texto_ou_nulo(s, "app_id") else _ids_do_json(
                linhas.texto_ou_nulo(s, "app_ids"))
            if not ids or pacote in {por_id.get(i, i) for i in ids}:
                capabilities.append(linhas.texto(s, "capability"))
        return capabilities

    def _conteudo_do_fluxo(self, ref: str) -> JsonObject | None:
        row = self._db.one("SELECT * FROM flows WHERE id=?", (ref,))
        if row is None:
            return None
        exigidos = self._exigidos("SELECT flow_id, app_id FROM flow_required_apps", "flow_id", ref)
        return fluxo_legivel(linhas.json_legado(linhas.texto(row, "plan")), nome=linhas.texto(row, "name"),
                             comando_modelo=linhas.texto(row, "command_template"),
                             fonte=linhas.texto_ou_nulo(row, "source"),
                             source_run_id=linhas.texto_ou_nulo(row, "source_run_id"), apps=exigidos.get(ref, []))

    def _conteudo_da_habilidade(self, ref: str) -> JsonObject | None:
        row = self._db.one("SELECT skill_id, version, state, schema_version, content, content_hash, command_template,"
                           " source_kind, source_ref, parent_version FROM skill_versions WHERE id=?", (ref,))
        if row is None:
            return None
        return habilidade_legivel(
            linhas.json_legado(linhas.texto(row, "content")), skill_id=linhas.texto(row, "skill_id"),
            versao=linhas.inteiro(row, "version"), schema_version=linhas.inteiro(row, "schema_version"),
            estado=linhas.texto(row, "state"), source_kind=linhas.texto(row, "source_kind"),
            source_ref=linhas.texto_ou_nulo(row, "source_ref"), parent_version=linhas.inteiro_ou_nulo(row, "parent_version"),
            command_template=linhas.texto_ou_nulo(row, "command_template"), content_hash=linhas.texto(row, "content_hash"))

    # ------------------------------------------------------------------ fluxo
    def fluxos(self) -> list[EntradaDoLivro]:
        resolvedor = self._resolvedor()
        exigidos = self._exigidos("SELECT flow_id, app_id FROM flow_required_apps", "flow_id", None)
        return [_fluxo(r, resolvedor, exigidos.get(linhas.texto(r, "id"), []))
                for r in self._db.query("SELECT * FROM flows ORDER BY created_at, id")]

    def fluxo(self, ref: str) -> EntradaDoLivro | None:
        row = self._db.one("SELECT * FROM flows WHERE id=?", (ref,))
        if not row:
            return None
        exigidos = self._exigidos("SELECT flow_id, app_id FROM flow_required_apps", "flow_id", ref)
        return _fluxo(row, self._resolvedor(), exigidos.get(ref, []))

    # ------------------------------------------------------------------ habilidade
    _SQL_HABILIDADE = ("SELECT v.id, v.state, v.source_kind, v.created_at, v.state_at, v.state_detail, v.content_hash,"
                       " v.command_template, d.name, d.app_id FROM skill_versions v"
                       " JOIN skill_definitions d ON d.id = v.skill_id")

    def habilidades(self) -> list[EntradaDoLivro]:
        resolvedor = self._resolvedor()
        exigidos = self._exigidos("SELECT version_id, app_id FROM skill_version_apps", "version_id", None)
        return [_habilidade(r, resolvedor, exigidos.get(linhas.texto(r, "id"), []))
                for r in self._db.query(self._SQL_HABILIDADE + " ORDER BY v.skill_id, v.version")]

    def habilidade(self, ref: str) -> EntradaDoLivro | None:
        row = self._db.one(self._SQL_HABILIDADE + " WHERE v.id=?", (ref,))
        if not row:
            return None
        exigidos = self._exigidos("SELECT version_id, app_id FROM skill_version_apps", "version_id", ref)
        return _habilidade(row, self._resolvedor(), exigidos.get(ref, []))

    # ------------------------------------------------------------------ memória (só a contagem)
    _SQL_MEMORIA = "SELECT profile_id, COUNT(*) AS n, MAX(updated_at) AS ultima FROM memory_items"

    def memorias(self) -> list[EntradaDoLivro]:
        return [_memoria(r) for r in self._db.query(self._SQL_MEMORIA + " GROUP BY profile_id ORDER BY profile_id")]

    def memoria(self, ref: str) -> EntradaDoLivro | None:
        row = self._db.one(self._SQL_MEMORIA + " WHERE profile_id=? GROUP BY profile_id", (ref,))
        return _memoria(row) if row else None


#: A receita com o título da etapa de que foi aprendida (`EntradaDoLivro.etapa`); a de treino não casa com `steps`.
_RECEITAS = "SELECT r.*, s.title AS etapa_titulo FROM recipes r LEFT JOIN steps s ON s.id = r.learned_from_step"


def _receita(r: Row) -> EntradaDoLivro:
    status = linhas.texto(r, "status")
    acoes = linhas.json_legado(linhas.texto(r, "actions"))
    aprendida = linhas.texto_ou_nulo(r, "learned_from_step") or ""
    sombra = f"sombra {linhas.inteiro(r, 'shadow_agree')}/{linhas.inteiro(r, 'shadow_total')}"
    return EntradaDoLivro(
        kind=LivroKind.RECEITA, ref=str(linhas.inteiro(r, "id")), state=estado_nativo(LivroKind.RECEITA, status),
        native_status=status, title=f"{linhas.texto(r, 'step_key')} (v{linhas.inteiro(r, 'version')})",
        app=linhas.texto(r, "app_package"),
        origin=Origem.TREINO if aprendida.startswith("training:") else Origem.EXECUCAO,
        side_effect=receita_tem_efeito(acoes), created_at=linhas.texto(r, "created_at"),
        last_used_at=linhas.texto_ou_nulo(r, "last_used_at"),
        uses=linhas.inteiro(r, "replay_ok") + linhas.inteiro(r, "replay_fail"), a_favor=linhas.inteiro(r, "replay_ok"),
        contra=linhas.inteiro(r, "replay_fail"), detail=sombra, content_hash=hash_da_receita(acoes),
        scope_key=escopo_da_receita(linhas.texto(r, "app_package"), linhas.texto(r, "app_version"),
                                    linhas.texto(r, "app_signature"), linhas.texto(r, "variant"),
                                    linhas.texto(r, "step_hash")),
        app_version=linhas.texto(r, "app_version"), falhas_seguidas=linhas.inteiro(r, "consecutive_fail"),
        nasceu_de=run_da_etapa(aprendida), etapa=linhas.texto_ou_nulo(r, "etapa_titulo"))


def _receita_lida(r: Row) -> ReceitaLida:
    return ReceitaLida(
        id=linhas.inteiro(r, "id"), app=linhas.texto(r, "app_package"), app_version=linhas.texto(r, "app_version"),
        assinatura=linhas.texto(r, "app_signature"), variante=linhas.texto(r, "variant"),
        step_hash=linhas.texto(r, "step_hash"), step_key=linhas.texto(r, "step_key"),
        versao=linhas.inteiro(r, "version"), status=linhas.texto(r, "status"),
        acoes=linhas.json_legado(linhas.texto(r, "actions")), aprendida_de=linhas.texto_ou_nulo(r, "learned_from_step"),
        replay_ok=linhas.inteiro(r, "replay_ok"), replay_fail=linhas.inteiro(r, "replay_fail"),
        consecutive_fail=linhas.inteiro(r, "consecutive_fail"), shadow_agree=linhas.inteiro(r, "shadow_agree"),
        shadow_total=linhas.inteiro(r, "shadow_total"), last_used_at=linhas.texto_ou_nulo(r, "last_used_at"))


def _receita_da_chave(r: Row) -> ReceitaDaChave:
    return ReceitaDaChave(
        ref=str(linhas.inteiro(r, "id")), app_version=linhas.texto(r, "app_version"), versao=linhas.inteiro(r, "version"),
        status=linhas.texto(r, "status"), replay_ok=linhas.inteiro(r, "replay_ok"),
        consecutive_fail=linhas.inteiro(r, "consecutive_fail"), criada_em=linhas.texto(r, "created_at"))


def _vizinha(r: Row | None) -> Vizinha | None:
    return None if r is None else Vizinha(linhas.inteiro(r, "id"), linhas.inteiro(r, "version"),
                                          linhas.texto(r, "status"))


def _ids_do_json(bruto: str | None) -> list[str]:
    valor = linhas.json_legado(bruto)
    return [i for i in valor if isinstance(i, str)] if isinstance(valor, list) else []


def _fluxo(r: Row, resolvedor: ResolvedorDeApp, exigidos: list[str]) -> EntradaDoLivro:
    status = linhas.texto(r, "status")
    plano = linhas.json_legado(linhas.texto(r, "plan"))
    app_id = linhas.texto_ou_nulo(r, "app_id")
    fonte = linhas.texto_ou_nulo(r, "source") or ""
    app, app_ref = _de_app(resolvedor.resolver(app_id, exigidos), app_id)
    return EntradaDoLivro(
        kind=LivroKind.FLUXO, ref=linhas.texto(r, "id"), state=estado_nativo(LivroKind.FLUXO, status),
        native_status=status, title=linhas.texto(r, "command_template"),
        app=app, app_ref=app_ref,
        apps=_apps_do_fluxo(plano, exigidos, app_id, resolvedor) if app != APP_NAO_RESOLVIDO else (),
        origin=Origem.TREINO if fonte.startswith("training") else Origem.EXECUCAO,
        side_effect=fluxo_tem_efeito(plano), created_at=linhas.texto(r, "created_at"),
        last_used_at=linhas.texto_ou_nulo(r, "last_used_at"), uses=linhas.inteiro(r, "uses"),
        detail=linhas.texto(r, "name"), content_hash=content_hash(plano) if plano is not None else None,
        scope_key=escopo_do_fluxo(linhas.texto(r, "match_key")), nasceu_de=_run_de_origem(r, fonte))


def _apps_do_fluxo(plano: JsonValue, exigidos: list[str], principal: str | None,
                    resolvedor: ResolvedorDeApp) -> tuple[str, ...]:
    """30.33-C: os pacotes dos apps do fluxo (os exigidos e o principal, mesmo que a tabela não o cite), na ordem do
    plano, só quando são mais de um; o id que não resolve fica de fora. Só com o principal resolvido: o fluxo no balde
    (30.2: dois exigidos de pacotes diferentes e nenhum principal) fica só no balde, porque adivinhar o principal é
    pior que mostrá-lo sem app."""
    ids = apps_na_ordem_do_plano(plano, exigidos)
    if principal and principal not in ids:
        ids = apps_na_ordem_do_plano(plano, [*ids, principal])
    pacotes = tuple(dict.fromkeys(p for p in (resolvedor.pacote(a) for a in ids) if p is not None))
    return pacotes if len(pacotes) > 1 else ()


def _run_de_origem(r: Row, fonte: str) -> str | None:
    """A execução de que o fluxo foi aprendido; o treino é da pessoa (sem execução de origem para invalidar)."""
    run = linhas.texto_ou_nulo(r, "source_run_id")
    return run if run is not None and run_valida(run) and not fonte.startswith("training") else None


def _habilidade(r: Row, resolvedor: ResolvedorDeApp, exigidos: list[str]) -> EntradaDoLivro:
    estado = linhas.texto(r, "state")
    app_id = linhas.texto_ou_nulo(r, "app_id")
    app, app_ref = _de_app(resolvedor.resolver(app_id, exigidos), app_id)
    return EntradaDoLivro(
        kind=LivroKind.HABILIDADE, ref=linhas.texto(r, "id"), state=estado_nativo(LivroKind.HABILIDADE, estado),
        native_status=estado, title=linhas.texto(r, "name"), app=app, app_ref=app_ref,
        origin=_ORIGEM_DA_HABILIDADE.get(linhas.texto(r, "source_kind"), Origem.PESSOA),
        created_at=linhas.texto(r, "created_at"), state_at=linhas.texto_ou_nulo(r, "state_at"),
        detail=linhas.texto_ou_nulo(r, "state_detail"), content_hash=linhas.texto(r, "content_hash"),
        scope_key=f"habilidade|{linhas.texto_ou_nulo(r, 'command_template') or ''}")


def _memoria(r: Row) -> EntradaDoLivro:
    n = linhas.inteiro(r, "n")
    return EntradaDoLivro(kind=LivroKind.MEMORIA, ref=linhas.texto(r, "profile_id"), state=None, native_status=None,
                          title=f"{n} lembrança(s) da persona", app=None, origin=Origem.SISTEMA,
                          last_used_at=linhas.texto_ou_nulo(r, "ultima"), count=n)
