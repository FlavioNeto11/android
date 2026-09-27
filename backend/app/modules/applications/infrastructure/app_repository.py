"""Repositório da tabela `apps`: o dono único da ESCRITA no registro de aplicativos.

Antes, o CRUD estava espalhado em três arquivos, cada um com a sua lista de colunas: as rotas de `api.py` (criar,
editar, remover), `AppState._seed_apps` (os apps do `config.yaml` na subida) e a vitrine (`cadastrar_app_se_novo`,
o app que chega por versão importada). O SQL de escrita de `apps` passa a morar só aqui, e o que sai daqui é
`AppRow` — nunca a linha crua do banco.

O repositório grava o que recebe. Normalizar a entrada (texto vazio vindo do formulário = sem valor) é de quem
chama, porque só quem chama sabe o que o vazio quis dizer; a subida pelo `config.yaml`, por exemplo, nunca
normalizou. A única transformação daqui é de armazenamento: `known_selectors` vira JSON (e vazio vira `NULL`).

Leitores de `apps` (uma dúzia de módulos com `SELECT` próprio) continuam onde estão: ler não disputa dono.
"""
from __future__ import annotations

from typing import TypedDict

from ....db import Database, Row, dumps
from ....util import novo_id_de_app


class AppRow(TypedDict):
    """Uma linha de `apps` (migrações 001 e 041), com o tipo de cada coluna."""

    id: str
    name: str
    package: str
    activity: str | None
    apk_path: str | None
    nav_hints: str | None
    #: JSON de `dict[str, str]`, como a coluna guarda. Quem monta o DTO decodifica (`vitrine.app_dto`).
    known_selectors: str | None
    builtin: int
    #: Categoria da vitrine (migração 041). `None` = sem categoria.
    category: str | None


class CamposDeApp(TypedDict, total=False):
    """O que a edição de um app pode mudar. `id` e `builtin` ficam de fora: não se editam."""

    name: str
    package: str
    activity: str | None
    apk_path: str | None
    nav_hints: str | None
    known_selectors: dict[str, str] | None
    category: str | None


_COLUNAS = "id, name, package, activity, apk_path, nav_hints, known_selectors, builtin, category"
#: O nome da coluna entra no texto do `UPDATE`: só passa o que está aqui, venha de onde vier o dicionário.
_EDITAVEIS = frozenset({"name", "package", "activity", "apk_path", "nav_hints", "known_selectors", "category"})


def _linha(r: Row) -> AppRow:
    return AppRow(id=r["id"], name=r["name"], package=r["package"], activity=r["activity"], apk_path=r["apk_path"],
                  nav_hints=r["nav_hints"], known_selectors=r["known_selectors"], builtin=int(r["builtin"]),
                  category=r["category"])


def _seletores(valor: dict[str, str] | None) -> str | None:
    return dumps(valor) if valor else None


class AppRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    # ------------------------------------------------------------------ leitura
    def listar(self) -> list[AppRow]:
        """Os embutidos primeiro, depois por nome — a ordem que o painel sempre mostrou."""
        return [_linha(r) for r in self._db.query(f"SELECT {_COLUNAS} FROM apps ORDER BY builtin DESC, name")]

    def obter(self, app_id: str) -> AppRow | None:
        r = self._db.one(f"SELECT {_COLUNAS} FROM apps WHERE id=?", (app_id,))
        return _linha(r) if r is not None else None

    def id_do_pacote(self, package: str, *, exceto: str | None = None) -> str | None:
        """O app que já usa este pacote (ignorando `exceto`), ou `None`.

        O pacote é a identidade que as versões, o estado por aparelho e a vitrine usam: dois cadastros do mesmo
        pacote dividiriam as contagens em dois cartões que falam do mesmo aplicativo."""
        if exceto is None:
            r = self._db.one("SELECT id FROM apps WHERE package=?", (package,))
        else:
            r = self._db.one("SELECT id FROM apps WHERE package=? AND id<>?", (package, exceto))
        return str(r["id"]) if r is not None else None

    # ------------------------------------------------------------------ escrita
    def criar(self, *, name: str, package: str, app_id: str | None = None, activity: str | None = None,
              apk_path: str | None = None, nav_hints: str | None = None,
              known_selectors: dict[str, str] | None = None, builtin: bool = False,
              category: str | None = None) -> str:
        """Grava o app e devolve o id. Sem `app_id`, o id nasce legível a partir do nome (`novo_id_de_app`).

        Não confere pacote repetido: a recusa (409 no cadastro, "já existe" na importação) é de quem chama."""
        novo = app_id or novo_id_de_app(self._db, name)
        self._db.execute(f"INSERT INTO apps({_COLUNAS}) VALUES (?,?,?,?,?,?,?,?,?)",
                         (novo, name, package, activity, apk_path, nav_hints, _seletores(known_selectors),
                          int(builtin), category))
        return novo

    def atualizar(self, app_id: str, campos: CamposDeApp) -> None:
        """Muda só as colunas presentes em `campos`. Sem campos, não toca no banco."""
        if fora := sorted(set(campos) - _EDITAVEIS):
            raise ValueError(f"coluna de apps que não se edita: {fora}")
        valores: dict[str, object] = dict(campos)
        if "known_selectors" in campos:
            valores["known_selectors"] = _seletores(campos["known_selectors"])
        if valores:
            self._db.execute(f"UPDATE apps SET {', '.join(f'{k}=?' for k in valores)} WHERE id=?",
                             (*valores.values(), app_id))

    def remover(self, app_id: str) -> None:
        self._db.execute("DELETE FROM apps WHERE id=?", (app_id,))

    def cadastrar_se_novo(self, package: str, rotulo: str | None) -> str | None:
        """Cadastra o app de um pacote que chegou por versão (upload, pasta ou loja) sem estar no registro.

        Decisão do dono (26/09): a vitrine nunca esconde uma versão importada. O app nasce com o rótulo lido do APK
        e SEM categoria — quem opera ajusta depois. Devolve o id criado, ou `None` quando o pacote já estava
        cadastrado.
        """
        if self.id_do_pacote(package) is not None:
            return None
        return self.criar(name=(rotulo or "").strip()[:80] or package, package=package)
