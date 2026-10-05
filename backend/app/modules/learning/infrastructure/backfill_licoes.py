"""Backfill ÚNICO das lições (ADR-054, pacote A7) para as execuções reais anteriores à migração 055.

Por que existe. A lição só nasce no digest da execução que assenta (`LearningService.digerir_execucao`), e o digest só
existe desde a 055. As execuções reais fechadas antes dela nunca passaram pelos mineradores de lição; este módulo as
passa UMA vez, pela MESMA lógica de produção: `ServicoDeLicoes.mineradores()` (`licoes.contraste` e `licoes.plano`),
sem reescrever regra nenhuma. Os outros mineradores do digest (fluxo, tela, voz, desfecho das exposições) NÃO rodam:
o desfecho das exposições (`licoes.exposicoes`) grava em tabela de exposição e `ai_calls` já foi purgada.

O que garante o "único e idempotente", sem migração nova:
- o caminho de produção já é idempotente: `propor` devolve o item vivo de mesma impressão (índice parcial
  `ux_learning_items_vivo`), `registrar_evidencia` é `ON CONFLICT (item_ref, origin_ref, stance) DO NOTHING`
  (`ux_learning_evidence`) e a transição só nasce com a mudança de estado; rodar de novo não duplica nada;
- o modo é FIXO em `shadow` (nunca o do config): nada é publicado nem entra no prompt; o que o domínio faria por
  repetição (candidata → validada) é o mesmo que a curadoria faria sozinha na próxima volta;
- antes de gravar confere que o banco está na MESMA migração que o código (nunca aplica migração).

Sem IA (nenhum provedor entra aqui), sem aparelho, sem rede. Nada daqui lê texto de persona: o que se relata são ids,
estados, escopo e contagens.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from app.config import LicoesCfg
from app.db import MIGRATIONS_DIR, Database
from app.modules.learning.application.licoes import ServicoDeLicoes
from app.modules.learning.application.ports import Ajustes
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.vocabulario import Modo
from app.modules.learning.infrastructure import ligar_licoes
from app.modules.learning.infrastructure.fontes import FontesSql
from app.modules.learning.infrastructure.licoes_sql import SqlLicoesRepository
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.modules.learning.infrastructure.sql_repository import SqlLearningRepository
from app.util import now, parse_iso

#: Os ÚNICOS mineradores que o backfill roda. `licoes.exposicoes` (preencher exposições) e os de fluxo/tela/voz ficam
#: de fora de propósito.
MINERADORES = ("licoes.contraste", "licoes.plano")
#: O estado terminal da execução (os mesmos que o escalonador usa para assentar).
TERMINAIS = ("completed", "completed_with_issues", "failed", "cancelled")


class BancoDiferenteDoCodigo(RuntimeError):
    """O banco não está na mesma migração que o código que roda: o backfill não escreve nada."""


class MigracaoAusente(RuntimeError):
    """A migração que marca o corte (055) não está em `schema_migrations`: sem ela não há "antes da 055"."""


def _numero(versao: str) -> int:
    return int(versao.split("_", 1)[0])


def versao_do_codigo() -> str:
    """O maior arquivo de `backend/migrations` do código que está rodando (pelo número)."""
    return max((f.stem for f in MIGRATIONS_DIR.glob("*.sql")), key=_numero)


def versao_do_banco(db: Database) -> str | None:
    """A maior versão aplicada em `schema_migrations` (pelo número), ou `None` se a tabela não existe ou está vazia."""
    if "schema_migrations" not in db.tables():
        return None
    versoes = [str(r["version"]) for r in db.query("SELECT version FROM schema_migrations")]
    return max(versoes, key=_numero) if versoes else None


def conferir_migracao(db: Database) -> str:
    """Devolve a versão comum, ou levanta se o banco e o código divergem. NUNCA aplica migração."""
    banco, codigo = versao_do_banco(db), versao_do_codigo()
    if banco != codigo:
        raise BancoDiferenteDoCodigo(
            f"o banco está em {banco or 'sem migração'} e o código em {codigo}: nada foi gravado. Rode o backfill "
            "com o código da mesma migração do banco (ou implante antes).")
    return codigo


def instante_da_migracao(db: Database, prefixo: str = "055_") -> datetime:
    """O `applied_at` da migração (055 por padrão)."""
    linha = db.one("SELECT applied_at FROM schema_migrations WHERE version LIKE ?", (prefixo + "%",))
    corte = parse_iso(str(linha["applied_at"])) if linha else None
    if corte is None:
        raise MigracaoAusente(f"a migração {prefixo}* não consta em schema_migrations deste banco")
    return corte


# ================================================================== quais execuções
@dataclass(frozen=True, slots=True)
class Selecao:
    run_ids: tuple[str, ...]
    puladas: dict[str, str] = field(default_factory=dict)       # run_id -> motivo


def _momento(linha: dict[str, object]) -> datetime | None:
    for chave in ("finished_at", "started_at", "created_at"):
        valor = linha.get(chave)
        if valor:
            return parse_iso(str(valor))
    return None


def antes_da_055(db: Database) -> Selecao:
    """As execuções REAIS (`simulated=0`) terminais cujo fim é anterior ao `applied_at` da 055, do fim mais antigo
    ao mais novo (a repetição evolui na ordem em que aconteceu)."""
    corte = instante_da_migracao(db)
    linhas = db.query(
        "SELECT id, finished_at, started_at, created_at FROM runs WHERE simulated=0 AND status IN (?,?,?,?)",
        TERMINAIS)
    datadas = [(m, str(r["id"])) for r in linhas if (m := _momento(r)) is not None and m < corte]
    return Selecao(tuple(rid for _, rid in sorted(datadas)))


def da_lista(db: Database, run_ids: Sequence[str]) -> Selecao:
    """Os run_ids pedidos que existem, são reais e terminaram; os demais entram em `puladas` com o motivo."""
    aceitas: list[str] = []
    puladas: dict[str, str] = {}
    for rid in dict.fromkeys(run_ids):                       # sem repetir, na ordem dada
        linha = db.one("SELECT status, simulated FROM runs WHERE id=?", (rid,))
        if linha is None:
            puladas[rid] = "inexistente"
        elif int(linha["simulated"] or 0):
            puladas[rid] = "simulada"
        elif linha["status"] not in TERMINAIS:
            puladas[rid] = "nao_terminal"
        else:
            aceitas.append(rid)
    return Selecao(tuple(aceitas), puladas)


# ================================================================== o que o livro tem
@dataclass(frozen=True, slots=True)
class Foto:
    """As contagens do livro de lições num instante."""

    itens_por_estado: dict[str, int]
    evidencias: int
    evidencias_de_licao: int
    transicoes: int
    transicoes_de_licao: int
    ids_de_itens: frozenset[str]
    ids_de_evidencias: frozenset[int]
    ids_de_transicoes: frozenset[int]


def fotografar(db: Database) -> Foto:
    estados = {str(r["state"]): int(r["n"]) for r in db.query(
        "SELECT state, COUNT(*) AS n FROM learning_items WHERE kind='licao' GROUP BY state")}
    ids_itens = frozenset(str(r["id"]) for r in db.query("SELECT id FROM learning_items WHERE kind='licao'"))
    evid = db.query("SELECT e.id, e.item_ref FROM learning_evidence e")
    trans = db.query("SELECT t.id, t.item_ref FROM learning_transitions t")
    return Foto(
        itens_por_estado=estados,
        evidencias=len(evid), evidencias_de_licao=sum(1 for e in evid if str(e["item_ref"]) in ids_itens),
        transicoes=len(trans), transicoes_de_licao=sum(1 for t in trans if str(t["item_ref"]) in ids_itens),
        ids_de_itens=ids_itens,
        ids_de_evidencias=frozenset(int(e["id"]) for e in evid if str(e["item_ref"]) in ids_itens),
        ids_de_transicoes=frozenset(int(t["id"]) for t in trans if str(t["item_ref"]) in ids_itens))


# ================================================================== a execução do backfill
@dataclass(slots=True)
class PorExecucao:
    run_id: str
    evidencias: dict[str, int] = field(default_factory=dict)    # minerador -> evidências NOVAS que ele devolveu
    falhas: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Resultado:
    versao: str
    selecao: Selecao
    antes: Foto
    depois: Foto
    execucoes: list[PorExecucao]
    recusas: dict[str, int]                                     # motivo -> quantas propostas foram recusadas
    novos: list[dict[str, str]]                                 # id, estado, papel, app, capability (nunca texto)

    @property
    def itens_criados(self) -> int:
        return len(self.depois.ids_de_itens - self.antes.ids_de_itens)

    @property
    def evidencias_criadas(self) -> int:
        return len(self.depois.ids_de_evidencias - self.antes.ids_de_evidencias)

    @property
    def transicoes_criadas(self) -> int:
        return len(self.depois.ids_de_transicoes - self.antes.ids_de_transicoes)


def _montar(db: Database, contador: Callable[..., None], relogio: Callable[[], datetime]) -> ServicoDeLicoes:
    """O serviço de lições de produção sobre este banco, sem provedor de IA, com o modo FIXO em `shadow`."""
    repo = SqlLearningRepository(db)
    ajustes = Ajustes(enabled=True, modo_licoes=Modo.SHADOW)
    servico = LearningService(repo, FontesSql(db), TriagemDeCredencial(), ajustes=lambda: ajustes, relogio=relogio,
                              retencao_de_logs_dias=lambda: 14)
    return ServicoDeLicoes(servico, repo, SqlLicoesRepository(db),
                           ajustes=lambda: ligar_licoes.ajustes_de_licoes(LicoesCfg()), relogio=relogio,
                           contar=contador)


def executar(db: Database, selecao: Selecao, *, relogio: Callable[[], datetime] = now) -> Resultado:
    """Passa SÓ `licoes.contraste` e `licoes.plano` por cada execução da seleção. Escreve no banco que recebe: quem
    quer o ensaio passa uma CÓPIA. Confere a migração antes de qualquer escrita."""
    versao = conferir_migracao(db)
    recusas: Counter[str] = Counter()

    def contar(nome: str, n: float = 1, **rotulos: str) -> None:
        if nome == "aprendizado.recusa":
            recusas[rotulos.get("motivo", "?")] += int(n)

    licoes = _montar(db, contar, relogio)
    mineradores = [m for m in licoes.mineradores() if m.nome in MINERADORES]
    antes = fotografar(db)
    execucoes: list[PorExecucao] = []
    for rid in selecao.run_ids:
        linha = PorExecucao(rid)
        for m in mineradores:
            try:
                linha.evidencias[m.nome] = int(m.minerar(rid))
            except Exception as exc:  # noqa: BLE001 - uma execução ruim não derruba as outras; fica no relatório
                linha.falhas.append(f"{m.nome}: {type(exc).__name__}")
        execucoes.append(linha)
    depois = fotografar(db)
    novos = []
    for item_id in sorted(depois.ids_de_itens - antes.ids_de_itens):
        r = db.one("SELECT id, state, scope_role, scope_app, scope_capability FROM learning_items WHERE id=?",
                   (item_id,))
        if r is not None:
            novos.append({"id": str(r["id"]), "estado": str(r["state"]), "papel": str(r["scope_role"]),
                          "app": str(r["scope_app"]), "capability": str(r["scope_capability"])})
    return Resultado(versao, selecao, antes, depois, execucoes, dict(recusas), novos)


# ================================================================== o relatório (só ids, estados e contagens)
def _estados(f: Foto) -> str:
    return ", ".join(f"{k}={v}" for k, v in sorted(f.itens_por_estado.items())) or "nenhum"


def relatorio(r: Resultado, *, aplicado: bool) -> str:
    modo = "APLICADO" if aplicado else "ENSAIO (cópia do banco; o original não foi tocado)"
    linhas = [f"backfill das lições: {modo}", f"migração do banco = do código: {r.versao}",
              f"execuções reais examinadas: {len(r.selecao.run_ids)}"
              + (f"; puladas: {len(r.selecao.puladas)}" if r.selecao.puladas else "")]
    for rid, motivo in r.selecao.puladas.items():
        linhas.append(f"  pulada {rid}: {motivo}")
    for e in r.execucoes:
        if any(e.evidencias.values()) or e.falhas:
            partes = [f"{k}={v}" for k, v in e.evidencias.items() if v] + [f"FALHA {f}" for f in e.falhas]
            linhas.append(f"  {e.run_id}: " + ", ".join(partes))
    sem_nada = sum(1 for e in r.execucoes if not any(e.evidencias.values()) and not e.falhas)
    linhas.append(f"  (execuções sem evidência nova: {sem_nada})")
    linhas.append("recusas por motivo: " + (", ".join(f"{k}={v}" for k, v in sorted(r.recusas.items())) or "nenhuma"))
    a, d = r.antes, r.depois
    linhas += [f"learning_items kind=licao antes: {_estados(a)} | depois: {_estados(d)}",
               f"learning_evidence (lições) antes: {a.evidencias_de_licao} | depois: {d.evidencias_de_licao}"
               f" (total da tabela: {a.evidencias} -> {d.evidencias})",
               f"learning_transitions (lições) antes: {a.transicoes_de_licao} | depois: {d.transicoes_de_licao}"
               f" (total da tabela: {a.transicoes} -> {d.transicoes})",
               f"criados: itens={r.itens_criados} evidências={r.evidencias_criadas} transições={r.transicoes_criadas}"]
    for n in r.novos:
        linhas.append(f"  item {n['id']} kind=licao estado={n['estado']} papel={n['papel']} app={n['app']}"
                      f" capability={n['capability']}")
    ev = sorted(d.ids_de_evidencias - a.ids_de_evidencias)
    tr = sorted(d.ids_de_transicoes - a.ids_de_transicoes)
    linhas.append(f"  ids de evidência criados: {ev or 'nenhum'}")
    linhas.append(f"  ids de transição criados: {tr or 'nenhum'}")
    return "\n".join(linhas)


__all__ = ["BancoDiferenteDoCodigo", "MINERADORES", "MigracaoAusente", "Foto", "PorExecucao", "Resultado", "Selecao",
           "antes_da_055", "conferir_migracao", "da_lista", "executar", "fotografar", "instante_da_migracao",
           "relatorio", "versao_do_banco", "versao_do_codigo"]
