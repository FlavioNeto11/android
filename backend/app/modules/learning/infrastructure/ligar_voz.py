"""Pacote A9 do ADR-054: liga a voz aprendida ao `SocialContextBuilder` (até 2 pares do mesmo perfil e da mesma ação,
sempre publicados pelo dono) e a preferência ao desambiguador.

Três partes:

- `AprovacoesSql` cumpre `AprovacoesDaVoz`: só LEITURA de `pending_approvals` (quem decide aprovação continua sendo
  `social/approvals.py`, que este pacote não toca). Na aprovação editada, os dois textos saem DESTEMPLATIZADOS: o alvo
  (com e sem `@`), os valores da etapa e os parâmetros do objetivo viram `{nome}`. Duas armadilhas medidas:
  - a edição SOBRESCREVE `bindings.content` com o texto novo (`approvals.apply_edit`): valor que é (ou contém) um
    dos textos não entra, senão o exemplo inteiro virava `{content}`;
  - valor de parâmetro sensível (`recipes.SENSITIVE_PARAM`) não vira marcador: se ele aparece num dos textos, o par
    inteiro é recusado (`tem_segredo`);
- `FonteDeVozDoLivro` cumpre a porta `FonteDeVoz` do contexto social;
- `pendurar(servico, db, contextos=..., planejador=...)`, chamado pelo `AppState` depois de compor o livro, o
  contexto social e o planejador de habilidades: registra os passos da curadoria (voz e preferências) e pendura as
  duas portas. Mesmo molde de `ligar_costuras.costuras_do_livro`: quem tem o contexto social e o planejador é o
  `AppState`, não a montagem do livro.

`ligar(servico)`, chamado pela montagem, continua sem efeito: tudo o que a voz e a preferência precisam (o banco, o
contexto social, o planejador) chega por `pendurar`.
"""
from __future__ import annotations

import json
from weakref import WeakKeyDictionary

from app.db import Database, Row
from app.modules.learning.application.ports import TriagemDeTexto
from app.modules.learning.application.servico import LearningService
from app.modules.learning.application.voz import AprovacaoDecidida, Decisao, ServicoDeVoz, destemplatizar
from app.modules.learning.infrastructure import linhas
from app.modules.learning.infrastructure.preferencias_sql import PreferenciasDoLivro, montar_preferencias
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.modules.learning.infrastructure.sql_repository import SqlLearningRepository
from app.modules.skills.infrastructure.run_planning import SkillRunPlanner
from app.social.context import SocialContextBuilder
from app.taskqueue.recipes import SENSITIVE_PARAM

#: Valor maior que isto não é parâmetro, é texto (a intenção, o conteúdo): não vira marcador.
_MAIOR_PARAMETRO = 80
_DECIDIDAS = ("approved", "edited", "rejected")


def ligar(servico: LearningService) -> None:
    """Sem efeito: a voz e a preferência são ligadas por `pendurar`, que o `AppState` chama com o que elas usam."""


def _objeto(bruto: object) -> dict[str, object]:
    """JSON de coluna legada (`steps.bindings`, `objectives.parameters`); ilegível é vazio (nada é presumido)."""
    if not isinstance(bruto, str) or not bruto:
        return {}
    try:
        valor = json.loads(bruto)
    except ValueError:
        return {}
    return valor if isinstance(valor, dict) else {}


class AprovacoesSql:
    def __init__(self, db: Database) -> None:
        self._db = db

    def decididas(self, desde: str) -> list[AprovacaoDecidida]:
        marcas = ",".join("?" for _ in _DECIDIDAS)
        saida: list[AprovacaoDecidida] = []
        for r in self._db.query(
                "SELECT a.id, a.status, a.profile_id, a.capability, a.target, a.generated_content, a.approved_content,"
                " a.decided_by, a.decided_at, a.decided_note, a.run_id, a.objective_id, a.step_id,"
                " r.simulated, ap.package, ap.id AS app, o.instance_id, o.parameters, s.bindings"
                " FROM pending_approvals a LEFT JOIN runs r ON r.id = a.run_id LEFT JOIN apps ap ON ap.id = a.app_id"
                " LEFT JOIN objectives o ON o.id = a.objective_id LEFT JOIN steps s ON s.id = a.step_id"
                f" WHERE a.status IN ({marcas}) AND a.decided_at >= ? ORDER BY a.decided_at, a.id",
                (*_DECIDIDAS, desde)):
            saida.append(self._decidida(r))
        return saida

    def _decidida(self, r: Row) -> AprovacaoDecidida:
        status = linhas.texto(r, "status")
        gerado = linhas.texto_ou_nulo(r, "generated_content")
        editado = linhas.texto_ou_nulo(r, "approved_content") if status == "edited" else None
        segredo = False
        if gerado and editado:
            valores, segredo = _variaveis(linhas.texto_ou_nulo(r, "target"), _objeto(r["bindings"]),
                                          _objeto(r["parameters"]), (gerado, editado))
            gerado, editado = destemplatizar(gerado, valores), destemplatizar(editado, valores)
        return AprovacaoDecidida(
            approval_id=linhas.texto(r, "id"), status=status, profile_id=linhas.texto_ou_nulo(r, "profile_id"),
            capability=linhas.texto(r, "capability"),
            app_package=linhas.texto_ou_nulo(r, "package") or linhas.texto_ou_nulo(r, "app") or "",
            decided_by=linhas.texto_ou_nulo(r, "decided_by"), decided_at=linhas.texto(r, "decided_at"),
            nota=linhas.texto_ou_nulo(r, "decided_note"), run_id=linhas.texto_ou_nulo(r, "run_id"),
            objective_id=linhas.texto_ou_nulo(r, "objective_id"), step_id=linhas.texto_ou_nulo(r, "step_id"),
            instance_id=linhas.texto_ou_nulo(r, "instance_id"),
            # Sem a execução (apagada) não há como saber: não é presumida real para virar voz.
            simulated=r["simulated"] is None or bool(r["simulated"]),
            gerado=gerado, editado=editado, tem_segredo=segredo)

    def ao_redor(self, profile_id: str, capability: str, marco: str, n: int) -> tuple[list[Decisao], list[Decisao]]:
        base = ("SELECT a.id, a.status FROM pending_approvals a LEFT JOIN runs r ON r.id = a.run_id"
                " WHERE a.profile_id=? AND a.capability=? AND a.status IN ('approved','edited')"
                " AND COALESCE(r.simulated, 0) = 0")
        antes = self._db.query(base + " AND a.created_at < ? ORDER BY a.created_at DESC, a.id DESC LIMIT ?",
                               (profile_id, capability, marco, int(n)))
        depois = self._db.query(base + " AND a.created_at > ? ORDER BY a.created_at, a.id LIMIT ?",
                                (profile_id, capability, marco, int(n)))
        return ([Decisao(linhas.texto(x, "id"), linhas.texto(x, "status")) for x in antes],
                [Decisao(linhas.texto(x, "id"), linhas.texto(x, "status")) for x in depois])

    def editadas(self, profile_id: str) -> int:
        return int(self._db.scalar("SELECT COUNT(*) FROM pending_approvals WHERE profile_id=? AND status='edited'",
                                   (profile_id,)) or 0)

    def perfil_existe(self, profile_id: str) -> bool:
        linha = self._db.one("SELECT id FROM instagram_profiles WHERE id=?", (profile_id,))   # a tabela da persona
        return linha is not None


def _variaveis(alvo: str | None, bindings: dict[str, object], parametros: dict[str, object],
               textos: tuple[str, str]) -> tuple[list[tuple[str, str]], bool]:
    """Os (nome, valor) a destemplatizar e se um valor SENSÍVEL aparece num dos textos. O alvo vem primeiro (vira
    `{alvo}`), com e sem `@`; um valor já visto (sem diferenciar caixa) não entra de novo com outro nome."""
    vistos: set[str] = set()
    valores: list[tuple[str, str]] = []
    minusculos = tuple(t.casefold() for t in textos)
    segredo = False
    candidatos: list[tuple[str, object]] = [("alvo", alvo), *bindings.items(), *parametros.items()]
    for nome, bruto in candidatos:
        valor = bruto.strip() if isinstance(bruto, str) else ""
        if len(valor) < 3:
            continue
        chave = valor.casefold()
        if SENSITIVE_PARAM.search(nome):
            segredo = segredo or any(chave in t for t in minusculos)
            continue
        if len(valor) > _MAIOR_PARAMETRO or any(t and (t in chave or chave == t) for t in minusculos):
            continue                                   # é o próprio texto (o `content` que a edição sobrescreveu)
        formas = [valor, valor[1:]] if valor.startswith("@") and len(valor) > 4 else [valor]
        for forma in formas:
            if forma.casefold() not in vistos:
                vistos.add(forma.casefold())
                valores.append((nome, forma))
    return valores, segredo


class FonteDeVozDoLivro:
    """A porta `FonteDeVoz` do contexto social, cumprida pelo livro."""

    def __init__(self, voz: ServicoDeVoz) -> None:
        self._voz = voz

    def bloco(self, profile_id: str, capability: str) -> str:
        return self._voz.bloco(profile_id, capability)


def montar_voz(db: object, servico: object, triagem: TriagemDeTexto | None = None) -> ServicoDeVoz | None:
    """O serviço da voz sobre o banco do central (`None` quando o estado ainda não foi composto)."""
    if not isinstance(db, Database) or not isinstance(servico, LearningService):
        return None
    return ServicoDeVoz(servico, SqlLearningRepository(db), AprovacoesSql(db),
                        triagem if triagem is not None else TriagemDeCredencial())


#: Os serviços que já foram pendurados: a curadoria não ganha o mesmo passo duas vezes.
_PENDURADOS: WeakKeyDictionary[LearningService, bool] = WeakKeyDictionary()


def pendurar(servico: LearningService, db: Database, *, contextos: SocialContextBuilder,
             planejador: SkillRunPlanner) -> None:
    """Registra os passos da curadoria (a varredura da voz e a das preferências) e pendura as duas portas: a voz no
    contexto social, a preferência no desambiguador. Sem IA; cada leitura do modo acontece a cada uso."""
    voz = montar_voz(db, servico)
    preferencias = montar_preferencias(db, servico)
    if voz is None or preferencias is None:
        return
    if not _PENDURADOS.get(servico):
        servico.registrar_passo(voz)
        servico.registrar_passo(preferencias)
        _PENDURADOS[servico] = True
    contextos.voz = FonteDeVozDoLivro(voz)
    planejador.usar_preferencias(PreferenciasDoLivro(preferencias))


__all__ = ["AprovacoesSql", "FonteDeVozDoLivro", "ligar", "montar_voz", "pendurar"]
