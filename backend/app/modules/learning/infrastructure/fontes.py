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

from collections.abc import Callable, Iterable

from app.db import Database, Row
from app.modules.learning.domain.conteudo import (PREFIXO_DE_TREINO, EtapaDeOrigem, ReceitaLida, Vizinha,
                                                  fluxo_legivel, habilidade_legivel, receita_legivel)
from app.modules.learning.domain.livro import (EntradaDoLivro, escopo_da_receita, escopo_do_fluxo, estado_nativo,
                                               fluxo_tem_efeito, hash_da_receita, receita_tem_efeito)
from app.modules.learning.domain.versao import ReceitaDaChave, VersaoViva, agrupar_vivas, quadro_da_receita
from app.modules.learning.domain.vocabulario import APP_NAO_RESOLVIDO, LivroKind, Origem
from app.modules.learning.infrastructure import linhas
from app.modules.skills.domain.document import JsonObject, content_hash

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
        return [_receita(r) for r in self._db.query("SELECT * FROM recipes ORDER BY app_package, step_key, version")]

    def receita(self, ref: str) -> EntradaDoLivro | None:
        try:
            recipe_id = int(ref)
        except ValueError:
            return None
        row = self._db.one("SELECT * FROM recipes WHERE id=?", (recipe_id,))
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

    # ------------------------------------------------------------------ versão (30.6)
    def vivas(self, app: str) -> tuple[VersaoViva, ...]:
        """As versões do app observadas HOJE em aparelho ativo, com o número de aparelhos (§7). Aparelho aposentado
        (`instances.retired_at`) e app ausente (`missing`) não contam; a linha sem `instances` (teste, aparelho já
        removido) conta como viva: o `NOT EXISTS` só exclui o que se sabe aposentado."""
        return agrupar_vivas(
            (linhas.texto(r, "v"), linhas.inteiro(r, "n")) for r in self._db.query(
                "SELECT d.observed_version_name AS v, COUNT(*) AS n FROM device_app_state d WHERE d.package_name=?"
                " AND d.observed_version_name IS NOT NULL AND d.observed_version_name <> '' AND d.state <> 'missing'"
                " AND NOT EXISTS (SELECT 1 FROM instances i WHERE i.id = d.instance_id AND i.retired_at IS NOT NULL)"
                " GROUP BY d.observed_version_name", (app,)))

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
        return fluxo_legivel(linhas.json_legado(linhas.texto(row, "plan")), nome=linhas.texto(row, "name"),
                             comando_modelo=linhas.texto(row, "command_template"),
                             fonte=linhas.texto_ou_nulo(row, "source"),
                             source_run_id=linhas.texto_ou_nulo(row, "source_run_id"))

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
        app_version=linhas.texto(r, "app_version"))


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
        origin=Origem.TREINO if fonte.startswith("training") else Origem.EXECUCAO,
        side_effect=fluxo_tem_efeito(plano), created_at=linhas.texto(r, "created_at"),
        last_used_at=linhas.texto_ou_nulo(r, "last_used_at"), uses=linhas.inteiro(r, "uses"),
        detail=linhas.texto(r, "name"), content_hash=content_hash(plano) if plano is not None else None,
        scope_key=escopo_do_fluxo(linhas.texto(r, "match_key")))


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
