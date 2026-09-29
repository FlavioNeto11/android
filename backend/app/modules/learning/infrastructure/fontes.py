"""As fontes nativas do livro (receita, fluxo, habilidade, memória), lidas das tabelas donas e entregues já no formato
do livro. Só LEITURA: quem escreve cada uma continua sendo quem sempre escreveu (e o status de receita e fluxo,
pelo repositório do aprendizado, com trilha).

O estado sai pelo mapeamento testado do domínio (`domain/livro.py`); o efeito externo, pelo conteúdo (ação `commit`
na receita, etapa `side_effect` no fluxo). A memória da persona sai só como CONTAGEM por perfil — o conteúdo nunca.
O app é mostrado pelo PACOTE quando o cadastro o conhece (o fluxo e a habilidade guardam o id do app; a receita, o
pacote), para o filtro `app=` casar as três fontes com o mesmo valor.
"""
from __future__ import annotations

from app.db import Database, Row
from app.modules.learning.domain.livro import (EntradaDoLivro, escopo_da_receita, escopo_do_fluxo, estado_nativo,
                                               fluxo_tem_efeito, hash_da_receita, receita_tem_efeito)
from app.modules.learning.domain.vocabulario import LivroKind, Origem
from app.modules.learning.infrastructure import linhas
from app.modules.skills.domain.document import content_hash

_ORIGEM_DA_HABILIDADE = {"teaching": Origem.ENSINO, "legacy_flow": Origem.EXECUCAO, "run": Origem.EXECUCAO,
                         "manual": Origem.PESSOA, "import": Origem.PESSOA}


class FontesSql:
    def __init__(self, db: Database) -> None:
        self._db = db

    def _pacotes(self) -> dict[str, str]:
        return {linhas.texto(r, "id"): linhas.texto_ou_nulo(r, "package") or linhas.texto(r, "id")
                for r in self._db.query("SELECT id, package FROM apps")}

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

    # ------------------------------------------------------------------ fluxo
    def fluxos(self) -> list[EntradaDoLivro]:
        pacotes = self._pacotes()
        return [_fluxo(r, pacotes) for r in self._db.query("SELECT * FROM flows ORDER BY created_at, id")]

    def fluxo(self, ref: str) -> EntradaDoLivro | None:
        row = self._db.one("SELECT * FROM flows WHERE id=?", (ref,))
        return _fluxo(row, self._pacotes()) if row else None

    # ------------------------------------------------------------------ habilidade
    _SQL_HABILIDADE = ("SELECT v.id, v.state, v.source_kind, v.created_at, v.state_at, v.state_detail, v.content_hash,"
                       " v.command_template, d.name, d.app_id FROM skill_versions v"
                       " JOIN skill_definitions d ON d.id = v.skill_id")

    def habilidades(self) -> list[EntradaDoLivro]:
        pacotes = self._pacotes()
        return [_habilidade(r, pacotes) for r in self._db.query(self._SQL_HABILIDADE + " ORDER BY v.skill_id, v.version")]

    def habilidade(self, ref: str) -> EntradaDoLivro | None:
        row = self._db.one(self._SQL_HABILIDADE + " WHERE v.id=?", (ref,))
        return _habilidade(row, self._pacotes()) if row else None

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


def _fluxo(r: Row, pacotes: dict[str, str]) -> EntradaDoLivro:
    status = linhas.texto(r, "status")
    plano = linhas.json_legado(linhas.texto(r, "plan"))
    app_id = linhas.texto_ou_nulo(r, "app_id")
    fonte = linhas.texto_ou_nulo(r, "source") or ""
    return EntradaDoLivro(
        kind=LivroKind.FLUXO, ref=linhas.texto(r, "id"), state=estado_nativo(LivroKind.FLUXO, status),
        native_status=status, title=linhas.texto(r, "command_template"),
        app=pacotes.get(app_id, app_id) if app_id else None,
        origin=Origem.TREINO if fonte.startswith("training") else Origem.EXECUCAO,
        side_effect=fluxo_tem_efeito(plano), created_at=linhas.texto(r, "created_at"),
        last_used_at=linhas.texto_ou_nulo(r, "last_used_at"), uses=linhas.inteiro(r, "uses"),
        detail=linhas.texto(r, "name"), content_hash=content_hash(plano) if plano is not None else None,
        scope_key=escopo_do_fluxo(linhas.texto(r, "match_key")))


def _habilidade(r: Row, pacotes: dict[str, str]) -> EntradaDoLivro:
    estado = linhas.texto(r, "state")
    app_id = linhas.texto_ou_nulo(r, "app_id")
    return EntradaDoLivro(
        kind=LivroKind.HABILIDADE, ref=linhas.texto(r, "id"), state=estado_nativo(LivroKind.HABILIDADE, estado),
        native_status=estado, title=linhas.texto(r, "name"), app=pacotes.get(app_id, app_id) if app_id else None,
        origin=_ORIGEM_DA_HABILIDADE.get(linhas.texto(r, "source_kind"), Origem.PESSOA),
        created_at=linhas.texto(r, "created_at"), state_at=linhas.texto_ou_nulo(r, "state_at"),
        detail=linhas.texto_ou_nulo(r, "state_detail"), content_hash=linhas.texto(r, "content_hash"),
        scope_key=f"habilidade|{linhas.texto_ou_nulo(r, 'command_template') or ''}")


def _memoria(r: Row) -> EntradaDoLivro:
    n = linhas.inteiro(r, "n")
    return EntradaDoLivro(kind=LivroKind.MEMORIA, ref=linhas.texto(r, "profile_id"), state=None, native_status=None,
                          title=f"{n} lembrança(s) da persona", app=None, origin=Origem.SISTEMA,
                          last_used_at=linhas.texto_ou_nulo(r, "ultima"), count=n)
