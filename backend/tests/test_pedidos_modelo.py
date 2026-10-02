"""Modelo do pedido persistente (item 28.2): domínio puro e migração 067. Prova `simulated`: sem IA, rede nem aparelho.

Três frentes:
  1. as tabelas de transição do pedido e da ocorrência, conferidas contra uma lista ESCRITA AQUI (a do §6.2 do
     desenho), e não contra elas mesmas: cada aresta permitida passa e cada uma das outras do produto cartesiano é
     recusada;
  2. a chave da ocorrência: estável, única, sem fração e que o `RunCreate` aceita;
  3. a migração 067 aplicada num banco que já tem execuções, sem mexer nelas, pela fábrica configurada (SQLite, ou
     PostgreSQL com `TEST_DATABASE_URL`: sem ele a corrida contra PostgreSQL é PULADA, não provada).
"""
from __future__ import annotations

import itertools
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import db as db_mod
from app.db import INTEGRITY_ERRORS, Database
from app.models import RunCreate
from app.modules.pedidos.domain import chave as chave_mod
from app.modules.pedidos.domain import estados as est
from app.modules.pedidos.domain.chave import (SEM_GATILHO, TAMANHO_MAXIMO_DO_ID, chave_da_ocorrencia,
                                             chave_da_tentativa, formatar_instante)
from app.modules.pedidos.domain.estados import (OCORRENCIA, PEDIDO, TransicaoInvalida, transicionar_ocorrencia,
                                                transicionar_pedido)

from .conftest import _dsn_de_teste

MIGRACAO = Path(db_mod.__file__).resolve().parents[1] / "migrations" / "067_pedidos.sql"

# ===================================================================== 1. transições
#: §6.2 do desenho, linha a linha: (de, para) → quem pode. "Qualquer não terminal → cancelado" são quatro linhas.
PEDIDO_PERMITIDAS: dict[tuple[str, str], set[str]] = {
    ("rascunho", "ativo"): {"pessoa"},
    ("ativo", "pausado"): {"pessoa", "sistema"},
    ("pausado", "ativo"): {"pessoa"},
    ("ativo", "aguardando_pessoa"): {"sistema"},
    ("aguardando_pessoa", "ativo"): {"pessoa"},
    ("ativo", "concluido"): {"sistema"},
    ("ativo", "encerrado"): {"sistema"},
    ("pausado", "encerrado"): {"sistema"},
    ("rascunho", "cancelado"): {"pessoa"},
    ("ativo", "cancelado"): {"pessoa"},
    ("pausado", "cancelado"): {"pessoa"},
    ("aguardando_pessoa", "cancelado"): {"pessoa"},
}
ESTADOS_DO_PEDIDO = ("rascunho", "ativo", "pausado", "aguardando_pessoa", "concluido", "encerrado", "cancelado")
PEDIDO_TERMINAIS = {"concluido", "encerrado", "cancelado"}

#: §6.2 (a cadeia) + §7.4/7.5/7.9 (os fins sem execução) + §7.6 (`falhou` → nova tentativa).
OCORRENCIA_PERMITIDAS: set[tuple[str, str]] = {
    ("prevista", "devida"), ("prevista", "pulada"), ("prevista", "perdida"), ("prevista", "cancelada"),
    ("devida", "despachada"), ("devida", "pulada"), ("devida", "perdida"), ("devida", "cancelada"),
    ("despachada", "rodando"), ("despachada", "concluida"), ("despachada", "falhou"), ("despachada", "incerta"),
    ("despachada", "perdida"), ("despachada", "cancelada"),
    ("rodando", "concluida"), ("rodando", "falhou"), ("rodando", "incerta"), ("rodando", "cancelada"),
    ("falhou", "devida"),
}
ESTADOS_DA_OCORRENCIA = ("prevista", "devida", "despachada", "rodando", "concluida", "falhou", "incerta",
                         "cancelada", "pulada", "perdida")
OCORRENCIA_TERMINAIS = {"concluida", "incerta", "cancelada", "pulada", "perdida"}

#: Um motivo qualquer, para o teste da aresta não tropeçar na regra do motivo (que tem teste próprio).
MOTIVO = {"pausado": "3 falhas seguidas", "encerrado": "prazo"}


def test_vocabulario_do_pedido_e_da_ocorrencia() -> None:
    assert set(PEDIDO.estados) == set(ESTADOS_DO_PEDIDO) and set(OCORRENCIA.estados) == set(ESTADOS_DA_OCORRENCIA)
    assert PEDIDO.terminais == PEDIDO_TERMINAIS
    assert OCORRENCIA.terminais == OCORRENCIA_TERMINAIS | set()          # `falhou` tem uma saída: não é terminal
    assert est.OCORRENCIA_NASCE_EM <= set(ESTADOS_DA_OCORRENCIA) and "rodando" not in est.OCORRENCIA_NASCE_EM


@pytest.mark.parametrize("de,para", list(itertools.product(ESTADOS_DO_PEDIDO, repeat=2)))
def test_tabela_de_transicoes_do_pedido_cada_aresta(de: str, para: str) -> None:
    """O produto cartesiano inteiro (49 pares): a permitida passa para quem pode, recusa quem não pode; as outras caem."""
    atores = PEDIDO_PERMITIDAS.get((de, para))
    assert PEDIDO.pode(de, para) == (atores is not None)
    if atores is None:
        for ator in ("pessoa", "sistema"):
            with pytest.raises(TransicaoInvalida, match="não está na tabela"):
                transicionar_pedido(de, para, ator=ator, motivo=MOTIVO.get(para, "x"))
        return
    for ator in ("pessoa", "sistema"):
        if ator in atores:
            transicionar_pedido(de, para, ator=ator, motivo=MOTIVO.get(para))
        else:
            with pytest.raises(TransicaoInvalida, match="é do"):
                transicionar_pedido(de, para, ator=ator, motivo=MOTIVO.get(para))


@pytest.mark.parametrize("de,para", list(itertools.product(ESTADOS_DA_OCORRENCIA, repeat=2)))
def test_tabela_de_transicoes_da_ocorrencia_cada_aresta(de: str, para: str) -> None:
    """O produto cartesiano inteiro (100 pares)."""
    permitida = (de, para) in OCORRENCIA_PERMITIDAS
    assert OCORRENCIA.pode(de, para) == permitida
    if permitida:
        transicionar_ocorrencia(de, para, motivo="porque sim")
    else:
        with pytest.raises(TransicaoInvalida, match="não está na tabela"):
            transicionar_ocorrencia(de, para, motivo="porque sim")


def test_ninguem_sai_de_um_estado_terminal_nem_vai_para_si_mesmo() -> None:
    for maquina in (PEDIDO, OCORRENCIA):
        for estado in maquina.estados:
            assert not maquina.pode(estado, estado), (maquina.nome, estado)
    for terminal in PEDIDO_TERMINAIS:
        assert not PEDIDO.transicoes[terminal]
    for terminal in OCORRENCIA_TERMINAIS:
        assert not OCORRENCIA.transicoes[terminal]


def test_estado_desconhecido_e_ator_desconhecido_sao_recusados() -> None:
    assert not PEDIDO.pode("fantasma", "ativo") and not PEDIDO.pode("ativo", "fantasma")
    with pytest.raises(TransicaoInvalida):
        transicionar_pedido("fantasma", "ativo", ator="pessoa")
    with pytest.raises(TransicaoInvalida, match="ator desconhecido"):
        transicionar_pedido("rascunho", "ativo", ator="ia")          # a IA nunca ativa nem conclui sozinha
    with pytest.raises(TransicaoInvalida):
        transicionar_ocorrencia("fantasma", "devida", motivo="x")


def test_pausar_e_encerrar_exigem_o_motivo_e_so_a_pessoa_ativa() -> None:
    for vazio in (None, "", "   "):
        with pytest.raises(TransicaoInvalida, match="motivo"):
            transicionar_pedido("ativo", "pausado", ator="sistema", motivo=vazio)
    transicionar_pedido("ativo", "pausado", ator="pessoa", motivo="férias")
    for motivo in (None, "", "porque sim"):                           # encerrar: só o vocabulário do §6.5
        with pytest.raises(TransicaoInvalida, match="motivo"):
            transicionar_pedido("ativo", "encerrado", ator="sistema", motivo=motivo)
    for motivo in est.MOTIVOS_DE_ENCERRAMENTO:
        transicionar_pedido("pausado", "encerrado", ator="sistema", motivo=motivo)
    assert "abandonado" in est.MOTIVOS_DE_ENCERRAMENTO
    with pytest.raises(TransicaoInvalida, match="é do"):              # o rascunho não ativa sozinho: prévia confirmada
        transicionar_pedido("rascunho", "ativo", ator="sistema")
    with pytest.raises(TransicaoInvalida, match="é do"):              # concluir é do verificador, não da pessoa
        transicionar_pedido("ativo", "concluido", ator="pessoa")


def test_fim_sem_execucao_da_ocorrencia_exige_o_motivo() -> None:
    """`pulada` e `perdida` "nunca somem": nascem visíveis e com a razão. Também falha, incerta e cancelada."""
    for de, para in sorted(OCORRENCIA_PERMITIDAS):
        if para in est.OCORRENCIA_EXIGE_MOTIVO:
            for vazio in (None, "", "  "):
                with pytest.raises(TransicaoInvalida, match="motivo"):
                    transicionar_ocorrencia(de, para, motivo=vazio)
        else:
            transicionar_ocorrencia(de, para)                         # `devida`, `despachada`, `rodando`, `concluida`


def test_a_ocorrencia_incerta_nunca_gera_nova_tentativa() -> None:
    """§6.2/§7.6: `incerta` é fim. A nova tentativa só existe a partir de `falhou`."""
    assert OCORRENCIA.transicoes["incerta"] == frozenset()
    assert {de for de, para in OCORRENCIA_PERMITIDAS if para == "devida" and de not in ("prevista",)} == {"falhou"}


# ===================================================================== 2. chave
T0 = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)


def test_a_chave_e_estavel_e_tem_a_forma_do_desenho() -> None:
    chave = chave_da_ocorrencia("ped_a1", "gat_1", T0)
    assert chave == "ped:ped_a1:gat_1:2026-10-02T12:00:00Z"
    assert chave_da_ocorrencia("ped_a1", "gat_1", T0) == chave                       # repetir não muda
    assert chave_da_ocorrencia("ped_a1", "gat_1", T0, origem="recuperacao") == chave   # recuperar É a mesma ocorrência
    for origem in ("agenda", "evento", "condicao", "persona"):
        assert chave_da_ocorrencia("ped_a1", "gat_1", T0, origem=origem) == chave


def test_a_chave_ignora_o_fuso_de_quem_chama_e_a_fracao_do_segundo() -> None:
    sao_paulo = timezone(timedelta(hours=-3))
    mesmo_instante = datetime(2026, 10, 2, 9, 0, 0, tzinfo=sao_paulo)
    assert chave_da_ocorrencia("p", "g", mesmo_instante) == chave_da_ocorrencia("p", "g", T0)
    assert formatar_instante(T0.replace(microsecond=999_999)) == formatar_instante(T0) == "2026-10-02T12:00:00Z"
    assert "." not in formatar_instante(T0.replace(microsecond=5))                  # ISO sem fração
    # Piso, não arredondamento: 11:59:59.999999 continua no segundo 59 (arredondar atravessaria a fronteira).
    assert formatar_instante(T0.replace(minute=59, hour=11, second=59, microsecond=999_999)) == "2026-10-02T11:59:59Z"


def test_a_chave_e_unica_por_pedido_gatilho_e_instante() -> None:
    chaves = {
        chave_da_ocorrencia("p1", "g1", T0), chave_da_ocorrencia("p2", "g1", T0), chave_da_ocorrencia("p1", "g2", T0),
        chave_da_ocorrencia("p1", "g1", T0 + timedelta(seconds=1)), chave_da_ocorrencia("p1", "g1", T0 + timedelta(days=1)),
    }
    assert len(chaves) == 5
    # Sem ambiguidade na concatenação: ids que "se emendariam" não produzem a mesma chave (o `:` não cabe num id).
    assert chave_da_ocorrencia("a", "bc", T0) != chave_da_ocorrencia("ab", "c", T0)


def test_a_chave_do_gesto_da_pessoa_leva_a_origem_e_nunca_colide_com_a_agenda() -> None:
    agenda = chave_da_ocorrencia("p1", "g1", T0)
    manual = chave_da_ocorrencia("p1", None, T0, origem="manual")
    backfill = chave_da_ocorrencia("p1", "g1", T0, origem="backfill")
    assert manual == "ped:p1:-:2026-10-02T12:00:00Z:manual" and SEM_GATILHO == "-"
    assert backfill == f"{agenda}:backfill"
    assert len({agenda, manual, backfill, chave_da_ocorrencia("p1", None, T0, origem="backfill")}) == 4
    # O mesmo gesto no mesmo segundo é o mesmo pedido da pessoa (clique duplo): mesma chave, uma ocorrência só.
    assert chave_da_ocorrencia("p1", None, T0.replace(microsecond=700_000), origem="manual") == manual


def test_a_chave_recusa_o_que_nao_e_deterministico_ou_nao_e_seguro() -> None:
    with pytest.raises(ValueError, match="sem fuso"):
        chave_da_ocorrencia("p", "g", datetime(2026, 10, 2, 12, 0, 0))              # ingênuo: ambíguo
    with pytest.raises(ValueError, match="gatilho_id é obrigatório"):
        chave_da_ocorrencia("p", None, T0)                                           # agenda sem gatilho
    with pytest.raises(ValueError, match="origem desconhecida"):
        chave_da_ocorrencia("p", "g", T0, origem="telepatia")
    for ruim in ("", "a:b", "tem espaço", "joão", "x" * (TAMANHO_MAXIMO_DO_ID + 1), "-:"):
        with pytest.raises(ValueError, match="inválido para a chave"):
            chave_da_ocorrencia(ruim, "g", T0)
        with pytest.raises(ValueError, match="inválido para a chave"):
            chave_da_ocorrencia("p", ruim, T0)
    with pytest.raises(ValueError, match="inválido para a chave"):
        chave_da_ocorrencia(None, "g", T0)                                           # type: ignore[arg-type]
    chave_da_ocorrencia("p" * TAMANHO_MAXIMO_DO_ID, "g" * TAMANHO_MAXIMO_DO_ID, T0)  # o limite exato cabe


def test_a_chave_da_execucao_da_tentativa_e_aceita_pelo_run_create() -> None:
    """`chave:t<n>` é a `idempotency_key` de `RunCreate`: o mesmo alfabeto e o limite de 100, no caso MAIS LONGO."""
    ids = "x" * TAMANHO_MAXIMO_DO_ID
    pior = chave_da_ocorrencia(ids, ids, T0, origem="backfill")
    execucao = chave_da_tentativa(pior, 99)
    assert execucao.endswith(":t99") and len(execucao) <= 100
    campo = RunCreate.model_fields["idempotency_key"]
    limite = next(m.max_length for m in campo.metadata if hasattr(m, "max_length"))
    padrao = next(m.pattern for m in campo.metadata if hasattr(m, "pattern"))
    assert limite == chave_mod.TAMANHO_MAXIMO_DA_CHAVE_DE_EXECUCAO and padrao == chave_mod._ALFABETO_DA_CHAVE_DE_EXECUCAO.pattern
    RunCreate(command="observar o preço", instance_ids=["android-01"], idempotency_key=execucao)         # não levanta
    assert chave_da_tentativa("ped:p:g:2026-10-02T12:00:00Z", 1) != chave_da_tentativa("ped:p:g:2026-10-02T12:00:00Z", 2)
    assert chave_da_tentativa("ped:p:g:2026-10-02T12:00:00Z", 2) == "ped:p:g:2026-10-02T12:00:00Z:t2"


def test_a_chave_da_tentativa_recusa_tentativa_invalida_e_chave_longa() -> None:
    for ruim in (0, -1, True, "1", 1.0):
        with pytest.raises(ValueError, match="começa em 1"):
            chave_da_tentativa("ped:p:g:2026-10-02T12:00:00Z", ruim)                  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="passa de 100"):
        chave_da_tentativa("ped:" + "x" * 100, 1)
    with pytest.raises(ValueError, match="fora de"):
        chave_da_tentativa("ped:com espaço", 1)


# ===================================================================== 3. migração 067
def _copia_ate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ate: str) -> Path:
    destino = tmp_path / "migracoes"
    destino.mkdir()
    for f in sorted(MIGRACAO.parent.glob("*.sql")):
        if f.stem <= ate:
            shutil.copy2(f, destino / f.name)
    monkeypatch.setattr(db_mod, "MIGRATIONS_DIR", destino)
    return destino


def _banco(tmp_path: Path) -> Database:
    """Pela fábrica configurada: SQLite, ou PostgreSQL com `TEST_DATABASE_URL` (nunca `Database(path)` direto)."""
    return Database(_dsn_de_teste() or tmp_path / "t.sqlite3")


def _run(db: Database, run_id: str) -> None:
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, instance_ids, created_at)"
               " VALUES (?, ?, 'ver o preço', 'execute', 'completed', '[]', '2026-10-01T10:00:00.000Z')",
               (run_id, f"chave-{run_id}"))


def _pedido(db: Database, pid: str, **campos: object) -> None:
    cols = {"id": pid, "titulo": "Preço do produto", "objetivo": "ver o preço", "criado_em": "2026-10-02T10:00:00.000Z",
            "atualizado_em": "2026-10-02T10:00:00.000Z", **campos}
    db.execute(f"INSERT INTO pedidos({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",  # noqa: S608
               tuple(cols.values()))


def _ocorrencia(db: Database, oid: str, pid: str, gatilho: str | None, previsto: str, chave: str, **campos: object) -> None:
    cols = {"id": oid, "pedido_id": pid, "pedido_versao": 1, "gatilho_id": gatilho, "previsto_para": previsto,
            "chave": chave, "origem": "agenda", "criada_em": "2026-10-02T10:00:00.000Z", **campos}
    db.execute(f"INSERT INTO pedido_ocorrencias({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",  # noqa: S608
               tuple(cols.values()))


def test_migracao_067_cria_o_modelo_sem_mexer_nas_execucoes_que_ja_existem(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Num banco que já tem execuções (até a 064): só tabelas novas e três colunas novas e vazias em `runs`."""
    destino = _copia_ate(tmp_path, monkeypatch, "064_perfil_de_ia")
    db = _banco(tmp_path)
    assert db.migrate()[-1] == "064_perfil_de_ia"
    _run(db, "r-antiga-1")
    _run(db, "r-antiga-2")
    antes = db.query("SELECT * FROM runs ORDER BY id")
    shutil.copy2(MIGRACAO, destino / MIGRACAO.name)
    assert db.migrate() == ["067_pedidos"] and db.divergencias() == [] and db.migrate() == []
    depois = db.query("SELECT * FROM runs ORDER BY id")
    assert len(depois) == 2
    for a, d in zip(antes, depois):
        assert {k: d[k] for k in a.keys()} == dict(a)                                # as colunas de antes, iguais
        assert (d["pedido_id"], d["ocorrencia_id"], d["prioridade"]) == (None, None, 0)
    for tabela in ("pedidos", "pedido_gatilhos", "pedido_ocorrencias"):
        assert db.columns(tabela) and db.one(f"SELECT COUNT(*) AS n FROM {tabela}")["n"] == 0  # noqa: S608
    db.close()


@pytest.fixture()
def banco(tmp_path: Path) -> Database:
    db = _banco(tmp_path)
    db.migrate()                                                                     # o esquema inteiro, como o central
    assert "067_pedidos" in [r["version"] for r in db.query("SELECT version FROM schema_migrations")]
    yield db
    db.close()


def test_pedido_nasce_no_teto_mais_restrito_e_em_rascunho(banco: Database) -> None:
    _pedido(banco, "p1")
    p = banco.one("SELECT * FROM pedidos WHERE id='p1'")
    assert p is not None
    assert (p["autonomia"], p["estado"], p["versao"], p["sobreposicao"], p["coalescer"]) == \
           ("observar", "rascunho", 1, "pular", 1)
    assert (p["fuso"], p["max_tentativas"], p["pausa_por_falha"]) == ("America/Sao_Paulo", 2, 3)
    assert p["proxima_em"] is None and p["pausado_motivo"] is None and p["pai_id"] is None


@pytest.mark.parametrize("campo,ruim", [
    ("estado", "fantasma"), ("autonomia", "livre"), ("sobreposicao", "cancelar_a_outra"), ("coalescer", 2),
    ("max_ocorrencias", 0), ("orcamento_total_usd", -1), ("orcamento_ocorrencia_usd", -0.5),
    ("janela_recuperacao_s", -1), ("max_tentativas", 0), ("pausa_por_falha", 0),
])
def test_pedido_recusa_vocabulario_e_limite_fora_do_contrato(banco: Database, campo: str, ruim: object) -> None:
    with pytest.raises(INTEGRITY_ERRORS):
        with banco.tx():
            _pedido(banco, "p-ruim", **{campo: ruim})
    assert banco.one("SELECT 1 AS x FROM pedidos WHERE id='p-ruim'") is None


def test_gatilho_e_ocorrencia_recusam_vocabulario_fora_do_contrato(banco: Database) -> None:
    _pedido(banco, "p1")
    with pytest.raises(INTEGRITY_ERRORS):
        with banco.tx():
            banco.execute("INSERT INTO pedido_gatilhos(id, pedido_id, tipo, criado_em) VALUES ('g-x', 'p1', 'cron',"
                          " '2026-10-02T10:00:00.000Z')")
    with pytest.raises(INTEGRITY_ERRORS):                                            # gatilho de pedido que não existe
        with banco.tx():
            banco.execute("INSERT INTO pedido_gatilhos(id, pedido_id, tipo, criado_em) VALUES ('g-y', 'nao-existe',"
                          " 'agora', '2026-10-02T10:00:00.000Z')")
    banco.execute("INSERT INTO pedido_gatilhos(id, pedido_id, tipo, criado_em) VALUES ('g1', 'p1', 'recorrencia',"
                  " '2026-10-02T10:00:00.000Z')")
    g = banco.one("SELECT * FROM pedido_gatilhos WHERE id='g1'")
    assert g is not None and (g["spec"], g["ativo"], g["cursor"]) == ("{}", 1, None)
    for campo, ruim in (("estado", "fantasma"), ("origem", "telepatia")):
        with pytest.raises(INTEGRITY_ERRORS):
            with banco.tx():
                _ocorrencia(banco, "o-x", "p1", "g1", "2026-10-02T12:00:00Z", "ped:p1:g1:x", **{campo: ruim})
    _ocorrencia(banco, "o1", "p1", "g1", "2026-10-02T12:00:00Z", "ped:p1:g1:2026-10-02T12:00:00Z")
    o = banco.one("SELECT * FROM pedido_ocorrencias WHERE id='o1'")
    assert o is not None and (o["estado"], o["tentativa"], o["custo_usd"], o["run_id"]) == ("prevista", 0, 0, None)


def test_a_ocorrencia_e_unica_pela_chave_e_pelo_trio(banco: Database) -> None:
    """Os dois cercos de §6.1/§6.3: `chave` UNIQUE, e (pedido, gatilho, previsto_para) UNIQUE além dela."""
    _pedido(banco, "p1")
    banco.execute("INSERT INTO pedido_gatilhos(id, pedido_id, tipo, criado_em) VALUES ('g1', 'p1', 'recorrencia',"
                  " '2026-10-02T10:00:00.000Z')")
    t = formatar_instante(T0)
    _ocorrencia(banco, "o1", "p1", "g1", t, chave_da_ocorrencia("p1", "g1", T0))
    with pytest.raises(INTEGRITY_ERRORS):                                            # mesma chave, outro id
        with banco.tx():
            _ocorrencia(banco, "o2", "p1", "g1", "2026-10-03T12:00:00Z", chave_da_ocorrencia("p1", "g1", T0))
    with pytest.raises(INTEGRITY_ERRORS):                                            # chave diferente, mesmo trio
        with banco.tx():
            _ocorrencia(banco, "o3", "p1", "g1", t, "ped:p1:g1:outra-chave")
    _ocorrencia(banco, "o4", "p1", "g1", formatar_instante(T0 + timedelta(days=1)),
                chave_da_ocorrencia("p1", "g1", T0 + timedelta(days=1)))             # instante seguinte: outra
    assert banco.one("SELECT COUNT(*) AS n FROM pedido_ocorrencias")["n"] == 2
    # `ON CONFLICT DO NOTHING` (como o laço insere) é portável nos dois bancos e não duplica nem levanta.
    banco.execute("INSERT INTO pedido_ocorrencias(id, pedido_id, pedido_versao, gatilho_id, previsto_para, chave,"
                  " origem, criada_em) VALUES ('o5', 'p1', 1, 'g1', ?, ?, 'agenda', '2026-10-02T10:00:00.000Z')"
                  " ON CONFLICT DO NOTHING", (t, chave_da_ocorrencia("p1", "g1", T0)))
    assert banco.one("SELECT COUNT(*) AS n FROM pedido_ocorrencias")["n"] == 2
    # O gesto sem gatilho (`gatilho_id` NULL) não é coberto pelo trio (NULL nunca é igual a NULL); a chave cobre.
    _ocorrencia(banco, "m1", "p1", None, t, chave_da_ocorrencia("p1", None, T0, origem="manual"), origem="manual")
    with pytest.raises(INTEGRITY_ERRORS):
        with banco.tx():
            _ocorrencia(banco, "m2", "p1", None, t, chave_da_ocorrencia("p1", None, T0, origem="manual"),
                        origem="manual")


def test_apagar_o_pedido_leva_gatilhos_e_ocorrencias_mas_nao_a_execucao(banco: Database) -> None:
    """A execução fica (sem chave estrangeira, de propósito): a purga dela não pode apagar a ocorrência nem o inverso."""
    _pedido(banco, "p1")
    banco.execute("INSERT INTO pedido_gatilhos(id, pedido_id, tipo, criado_em) VALUES ('g1', 'p1', 'agora',"
                  " '2026-10-02T10:00:00.000Z')")
    _run(banco, "r1")
    _ocorrencia(banco, "o1", "p1", "g1", formatar_instante(T0), chave_da_ocorrencia("p1", "g1", T0), run_id="r1")
    banco.execute("UPDATE runs SET pedido_id='p1', ocorrencia_id='o1', prioridade=-5 WHERE id='r1'")
    r = banco.one("SELECT pedido_id, ocorrencia_id, prioridade FROM runs WHERE id='r1'")
    assert r is not None and (r["pedido_id"], r["ocorrencia_id"], r["prioridade"]) == ("p1", "o1", -5)
    banco.execute("DELETE FROM runs WHERE id='r1'")                                  # purga da execução: a ocorrência fica
    assert banco.one("SELECT run_id FROM pedido_ocorrencias WHERE id='o1'")["run_id"] == "r1"
    banco.execute("DELETE FROM pedidos WHERE id='p1'")
    for tabela in ("pedido_gatilhos", "pedido_ocorrencias"):
        assert banco.one(f"SELECT COUNT(*) AS n FROM {tabela}")["n"] == 0            # noqa: S608


def test_apagar_o_gatilho_nao_apaga_o_historico(banco: Database) -> None:
    _pedido(banco, "p1")
    banco.execute("INSERT INTO pedido_gatilhos(id, pedido_id, tipo, criado_em) VALUES ('g1', 'p1', 'agora',"
                  " '2026-10-02T10:00:00.000Z')")
    _ocorrencia(banco, "o1", "p1", "g1", formatar_instante(T0), chave_da_ocorrencia("p1", "g1", T0))
    with pytest.raises(INTEGRITY_ERRORS):
        with banco.tx():
            banco.execute("DELETE FROM pedido_gatilhos WHERE id='g1'")               # desative (`ativo=0`), não apague
    assert banco.one("SELECT COUNT(*) AS n FROM pedido_ocorrencias")["n"] == 1


# ---------------------------------------------------------------- o CHECK do banco e o vocabulário do domínio
def _palavras_do_check(tabela: str, coluna: str) -> set[str]:
    sql = MIGRACAO.read_text(encoding="utf-8")
    bloco = re.search(rf"CREATE TABLE {tabela} \((.*?)\n\);", sql, re.S)
    assert bloco, tabela
    m = re.search(rf"\b{coluna}\s+IN\s*\(([^)]*)\)", bloco.group(1))
    assert m, (tabela, coluna)
    return set(re.findall(r"'([^']*)'", m.group(1)))


@pytest.mark.parametrize("tabela,coluna,vocabulario", [
    ("pedidos", "estado", set(ESTADOS_DO_PEDIDO)),
    ("pedidos", "autonomia", set(est.AUTONOMIAS)),
    ("pedidos", "sobreposicao", set(est.SOBREPOSICOES)),
    ("pedido_gatilhos", "tipo", set(est.TIPOS_DE_GATILHO)),
    ("pedido_ocorrencias", "estado", set(ESTADOS_DA_OCORRENCIA)),
    ("pedido_ocorrencias", "origem", set(est.ORIGENS)),
])
def test_o_check_da_migracao_e_o_dominio_dizem_as_mesmas_palavras(tabela: str, coluna: str,
                                                                   vocabulario: set[str]) -> None:
    assert _palavras_do_check(tabela, coluna) == vocabulario
    # E o domínio não inventa estado que a lista escrita neste teste (a do desenho) não tenha.
    assert set(PEDIDO.estados) == set(ESTADOS_DO_PEDIDO) and set(OCORRENCIA.estados) == set(ESTADOS_DA_OCORRENCIA)
