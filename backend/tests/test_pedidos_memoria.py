"""Item 28.7 (parte 1) — migração 070, memória versionada e observação (docs/design/pedidos-laco.md, "28.7").

Prova `simulated` (`arquivo::teste`): SQLite, ou PostgreSQL pela fábrica configurada quando há `TEST_DATABASE_URL` (sem ele
a corrida contra PostgreSQL é PULADA, não provada). Nada aqui toca aparelho nem IA.

Cobre: a migração aplica e é idempotente sobre um banco que já tem execuções; o CHECK do banco e o vocabulário do domínio
dizem as mesmas palavras; a observação sobrevive à purga da execução e é reentrante; a memória é versionada (escrever o mesmo
valor não sobe a versão), recusa segredo e se compacta sem IA.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from app import db as db_mod
from app.db import INTEGRITY_ERRORS, Database
from app.modules.pedidos.domain import memoria as mem
from app.modules.pedidos.domain import observacao as obs
from app.modules.pedidos.infrastructure.relatorios import GATILHOS, ServicoDeRelatorios, parece_segredo
from app.modules.pedidos.infrastructure.repositorio_memoria import NovaObservacao, RepositorioDeMemoria

from .test_pedidos_modelo import _banco, _copia_ate, _ocorrencia, _pedido, _run

MIGRACAO = Path(db_mod.__file__).resolve().parents[1] / "migrations" / "070_pedidos_memoria.sql"
T0 = "2026-10-02T10:00:00.000Z"


def _nunca(_: str) -> bool:
    return False


# ===================================================================== 1. migração 070
def test_migracao_070_aplica_sobre_um_banco_que_ja_tem_pedidos_sem_mexer_neles(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Até a 068 (a 069 é de outra frente e fica de fora: lacuna de número é tolerada): só tabelas novas e vazias."""
    destino = _copia_ate(tmp_path, monkeypatch, "068_avisos_entregas")
    db = _banco(tmp_path)
    assert db.migrate()[-1] == "068_avisos_entregas"
    _run(db, "r1")
    _pedido(db, "p1")
    _ocorrencia(db, "o1", "p1", None, "2026-10-02T12:00:00Z", "ped:p1:-:2026-10-02T12:00:00Z")
    antes = (db.query("SELECT * FROM pedidos"), db.query("SELECT * FROM pedido_ocorrencias"), db.query("SELECT * FROM runs"))
    shutil.copy2(MIGRACAO, destino / MIGRACAO.name)
    assert db.migrate() == ["070_pedidos_memoria"] and db.divergencias() == [] and db.migrate() == []
    depois = (db.query("SELECT * FROM pedidos"), db.query("SELECT * FROM pedido_ocorrencias"), db.query("SELECT * FROM runs"))
    assert [[dict(r) for r in t] for t in antes] == [[dict(r) for r in t] for t in depois]
    for tabela in ("pedido_memoria", "pedido_observacoes", "pedido_relatorios"):
        assert db.columns(tabela) and db.one(f"SELECT COUNT(*) AS n FROM {tabela}")["n"] == 0  # noqa: S608
    db.close()


@pytest.fixture()
def banco(tmp_path: Path) -> Database:
    db = _banco(tmp_path)
    db.migrate()
    assert "070_pedidos_memoria" in [r["version"] for r in db.query("SELECT version FROM schema_migrations")]
    _pedido(db, "p1")
    _ocorrencia(db, "o1", "p1", None, "2026-10-02T12:00:00Z", "ped:p1:-:2026-10-02T12:00:00Z", estado="concluida")
    yield db
    db.close()


def _nova(**campos: object) -> NovaObservacao:
    base = dict(pedido_id="p1", pedido_versao=1, ocorrencia_id="o1", run_id="r1", step_id="s1", alvo="android-01",
                nome="preco", tipo="text", situacao="observado", valor="R$ 3.499,00", fonte="Chrome / ler o preço",
                trecho=None, sha256=obs.sha256_do_valor("R$ 3.499,00"), capturado_em=T0)
    base.update(campos)
    return NovaObservacao(**base)                                                    # type: ignore[arg-type]


def test_a_observacao_sobrevive_a_purga_da_execucao_e_do_run_e_e_reentrante(banco: Database) -> None:
    _run(banco, "r1")
    repo = RepositorioDeMemoria(banco)
    assert repo.inserir_observacoes([_nova()]) == 1
    assert repo.inserir_observacoes([_nova(valor="outro")]) == 0, "mesma (ocorrência, alvo, nome): a primeira vale"
    banco.execute("DELETE FROM runs WHERE id='r1'")                                  # a purga da execução
    [o] = repo.observacoes("p1")
    assert (o["valor"], o["run_id"], o["ocorrencia_id"]) == ("R$ 3.499,00", "r1", "o1")


def test_apagar_o_pedido_leva_memoria_observacoes_e_relatorios_dele(banco: Database) -> None:
    repo = RepositorioDeMemoria(banco)
    repo.inserir_observacoes([_nova()])
    servico = ServicoDeRelatorios(banco, banco.agora)
    servico.gravar_memoria("p1", "decisao:app", "decisao", "usar o Chrome")
    assert servico.gerar("p1") is not None
    banco.execute("DELETE FROM pedidos WHERE id='p1'")
    for tabela in ("pedido_memoria", "pedido_observacoes", "pedido_relatorios"):
        assert banco.one(f"SELECT COUNT(*) AS n FROM {tabela}")["n"] == 0                # noqa: S608


@pytest.mark.parametrize("tabela,campos", [
    ("pedido_memoria", dict(id="m1", pedido_id="p1", chave="k", tipo="inventado", valor="v", atualizada_em=T0)),
    ("pedido_memoria", dict(id="m1", pedido_id="p1", chave="k", tipo="fonte", valor="v", versao=0, atualizada_em=T0)),
    ("pedido_observacoes", dict(id="x", pedido_id="p1", ocorrencia_id="o1", nome="n", situacao="talvez", capturado_em=T0)),
    ("pedido_observacoes", dict(id="x", pedido_id="p1", ocorrencia_id="o1", nome="n", situacao="ausente", tipo="json",
                                capturado_em=T0)),
    ("pedido_relatorios", dict(id="x", pedido_id="p1", sequencia=1, gatilho="semanal", pedido_versao=1, periodo_ate=T0,
                               conteudo="{}", sha256="h", gerado_em=T0)),
    ("pedido_relatorios", dict(id="x", pedido_id="p1", sequencia=1, gatilho="sob_demanda", pedido_versao=1, periodo_ate=T0,
                               conteudo="{}", sha256="h", custo_usd=-1, gerado_em=T0)),
])
def test_o_banco_recusa_vocabulario_e_limite_fora_do_contrato(banco: Database, tabela: str,
                                                              campos: dict[str, object]) -> None:
    with pytest.raises(INTEGRITY_ERRORS):
        with banco.tx():
            banco.execute(f"INSERT INTO {tabela}({', '.join(campos)}) VALUES ({', '.join('?' * len(campos))})",  # noqa: S608
                          tuple(campos.values()))


def test_o_relatorio_de_encerramento_e_um_so_por_pedido_e_a_sequencia_e_unica(banco: Database) -> None:
    def novo(rid: str, seq: int, gatilho: str) -> None:
        banco.execute("INSERT INTO pedido_relatorios(id, pedido_id, sequencia, gatilho, pedido_versao, periodo_ate, conteudo,"
                      " sha256, gerado_em) VALUES (?,?,?,?,1,?,'{}','h',?)", (rid, "p1", seq, gatilho, T0, T0))

    novo("a", 1, "encerramento")
    novo("b", 2, "sob_demanda")
    novo("c", 3, "sob_demanda")                                                      # os demais se acumulam
    for rid, seq, g in (("d", 4, "encerramento"), ("e", 2, "periodo")):
        with pytest.raises(INTEGRITY_ERRORS):
            with banco.tx():
                novo(rid, seq, g)


def _palavras_do_check(tabela: str, coluna: str) -> set[str]:
    sql = MIGRACAO.read_text(encoding="utf-8")
    bloco = re.search(rf"CREATE TABLE {tabela} \((.*?)\n\);", sql, re.S)
    assert bloco, tabela
    m = re.search(rf"\b{coluna}\s+TEXT[^,\n]*?CHECK \({coluna} IN \(([^)]*)\)", bloco.group(1))
    assert m, (tabela, coluna)
    return set(re.findall(r"'([^']*)'", m.group(1)))


@pytest.mark.parametrize("tabela,coluna,vocabulario", [
    ("pedido_memoria", "tipo", set(mem.TIPOS)),
    ("pedido_observacoes", "tipo", set(obs.TIPOS_DE_VALOR)),
    ("pedido_observacoes", "situacao", set(obs.SITUACOES)),
    ("pedido_relatorios", "gatilho", set(GATILHOS)),
])
def test_o_check_da_migracao_e_o_dominio_dizem_as_mesmas_palavras(tabela: str, coluna: str,
                                                                   vocabulario: set[str]) -> None:
    assert _palavras_do_check(tabela, coluna) == vocabulario


# ===================================================================== 2. memória versionada
def test_memoria_nova_entra_na_versao_1_e_valor_igual_nao_sobe_a_versao() -> None:
    a = mem.escrever(None, chave="loja:x", tipo="descoberta", valor="frete só no carrinho", agora=T0,
                     ocorrencia_id="o1", parece_segredo=_nunca)
    assert a.mudou and a.entrada.versao == 1
    b = mem.escrever(a.entrada, chave="loja:x", tipo="descoberta", valor="frete só no carrinho",
                     agora="2026-10-03T10:00:00.000Z", ocorrencia_id="o2", parece_segredo=_nunca)
    assert not b.mudou and b.entrada == a.entrada, "nada muda: versão, instante e ocorrência ficam"
    c = mem.escrever(b.entrada, chave="loja:x", tipo="descoberta", valor="frete grátis acima de R$ 200",
                     agora="2026-10-03T10:00:00.000Z", ocorrencia_id="o2", parece_segredo=_nunca)
    assert c.mudou and (c.entrada.versao, c.entrada.ocorrencia_id) == (2, "o2")


def test_memoria_recusa_chave_tipo_vazio_tamanho_e_mudar_o_tipo_da_chave() -> None:
    ok = dict(agora=T0, parece_segredo=_nunca)
    for chave, tipo, valor, por_que in [
        ("Maiuscula", "fonte", "v", "chave"), ("", "fonte", "v", "chave"), ("k" * 65, "fonte", "v", "chave"),
        ("k", "inventado", "v", "tipo"), ("k", "fonte", "   ", "valor"), ("k", "fonte", "x" * (mem.VALOR_MAX + 1), "passa de"),
    ]:
        with pytest.raises(mem.MemoriaInvalida, match=por_que):
            mem.escrever(None, chave=chave, tipo=tipo, valor=valor, **ok)             # type: ignore[arg-type]
    antes = mem.escrever(None, chave="k", tipo="fonte", valor="v", **ok).entrada
    with pytest.raises(mem.MemoriaInvalida, match="já é do tipo"):
        mem.escrever(antes, chave="k", tipo="decisao", valor="v", **ok)


def test_memoria_recusa_segredo_sem_repetir_o_valor_na_mensagem() -> None:
    codigo = "meu codigo de verificacao é 482913"
    with pytest.raises(mem.MemoriaInvalida) as e:
        mem.escrever(None, chave="k", tipo="descoberta", valor=codigo, agora=T0, parece_segredo=parece_segredo)
    assert "482913" not in str(e.value) and "credencial" in str(e.value)


def test_pendencia_resolvida_deixa_o_plano_mas_o_registro_fica() -> None:
    p = mem.escrever(None, chave="perfil.y", tipo="pendencia", valor="perfil Y é privado", agora=T0,
                     parece_segredo=_nunca).entrada
    r = mem.resolver(p, agora="2026-10-03T10:00:00.000Z")
    assert r.mudou and r.entrada.resolvida and r.entrada.versao == 2
    assert mem.resolver(r.entrada, agora="2026-10-04T10:00:00.000Z").mudou is False, "idempotente"
    assert mem.compactar([r.entrada]) == ()
    with pytest.raises(mem.MemoriaInvalida):
        mem.resolver(mem.Entrada("k", "fonte", "v"), agora=T0)


def test_compactar_prioriza_pendencia_respeita_o_teto_e_e_deterministica() -> None:
    def e(chave: str, tipo: str, valor: str, quando: str, versao: int = 1) -> mem.Entrada:
        return mem.Entrada(chave, tipo, valor, versao, quando)

    entradas = [e("d1", "descoberta", "a" * 10, "2026-10-01T01:00:00Z"), e("d2", "descoberta", "b" * 10, "2026-10-01T02:00:00Z"),
                e("d3", "descoberta", "c" * 10, "2026-10-01T03:00:00Z"), e("p1", "pendencia", "esclarecer perfil", "2026-10-01T00:00:00Z"),
                e("pr", "progresso", "2 de 3 critérios", "2026-10-01T04:00:00Z"),
                e("pr", "progresso", "1 de 3 critérios", "2026-10-01T03:00:00Z", versao=1)]
    todas = mem.compactar(entradas)
    assert [x.chave for x in todas] == ["p1", "pr", "d3", "d2", "d1"], "pendência, progresso (último por chave), descobertas novas antes"
    assert next(x for x in todas if x.chave == "pr").valor == "2 de 3 critérios"
    assert mem.compactar(list(reversed(entradas))) == todas, "a ordem de entrada não muda o bloco"
    assert [x.chave for x in mem.compactar(entradas, max_descobertas=1)] == ["p1", "pr", "d3"]
    curto = mem.compactar(entradas, teto=len("p1") + len("esclarecer perfil") + 5)
    assert [x.chave for x in curto] == ["p1"], "o teto corta sem partir entrada ao meio"


def test_o_repositorio_guarda_a_memoria_versionada_e_o_cas_pela_versao(banco: Database) -> None:
    servico = ServicoDeRelatorios(banco, banco.agora)
    a = servico.gravar_memoria("p1", "loja:x", "descoberta", "frete só no carrinho", ocorrencia_id="o1")
    assert a.versao == 1
    assert servico.gravar_memoria("p1", "loja:x", "descoberta", "frete só no carrinho").versao == 1
    assert servico.gravar_memoria("p1", "loja:x", "descoberta", "frete grátis").versao == 2
    repo = RepositorioDeMemoria(banco)
    velha = repo.entrada("p1", "loja:x")
    assert velha is not None and velha.versao == 2
    assert repo.gravar_entrada("p1", velha, mem.Entrada("loja:x", "descoberta", "x", 3, T0)) is True
    assert repo.gravar_entrada("p1", velha, mem.Entrada("loja:x", "descoberta", "y", 3, T0)) is False, "versão velha: perde"
    servico.gravar_memoria("p1", "perfil.y", "pendencia", "perfil Y é privado")
    assert repo.pendencias_abertas("p1") == ["perfil Y é privado"]
    servico.resolver_pendencia("p1", "perfil.y")
    assert repo.pendencias_abertas("p1") == []
    with pytest.raises(mem.MemoriaInvalida):
        servico.gravar_memoria("p1", "k", "descoberta", "o código de verificação é 123456")


# ===================================================================== 3. observação
def test_observacao_concluida_e_observada_falha_ou_incerteza_e_incerta_sem_valor_e_ausente() -> None:
    p = obs.preparar("R$ 10", tipo="text", estado_final="concluida", motivo=None, parece_segredo=_nunca)
    assert (p.situacao, p.valor, p.trecho) == ("observado", "R$ 10", None) and p.sha256 == obs.sha256_do_valor("R$ 10")
    for estado in ("falhou", "incerta", "cancelada", "perdida"):
        q = obs.preparar("R$ 10", tipo="text", estado_final=estado, motivo="parcial: 1 de 2", parece_segredo=_nunca)
        assert (q.situacao, q.trecho) == ("incerto", "parcial: 1 de 2"), estado
    a = obs.preparar(None, tipo="text", estado_final="concluida", motivo=None, parece_segredo=_nunca)
    assert (a.situacao, a.valor, a.tipo, a.sha256) == ("ausente", None, "resultado", None)
    assert a.trecho == "ocorrência concluida"


def test_observacao_recusa_valor_com_formato_de_credencial_e_vira_ausente() -> None:
    p = obs.preparar("seu código de verificação é 482913", tipo="text", estado_final="concluida", motivo=None,
                     parece_segredo=parece_segredo)
    assert (p.situacao, p.valor, p.sha256) == ("ausente", None, None) and "482913" not in (p.trecho or "")


def test_observacao_corta_no_teto_e_marca_o_corte() -> None:
    grande = "x" * (obs.VALOR_MAX + 50)
    p = obs.preparar(grande, tipo="text", estado_final="concluida", motivo=None, parece_segredo=_nunca)
    assert len(p.valor or "") == obs.VALOR_MAX and p.trecho == "valor cortado no teto"
    assert p.sha256 == obs.sha256_do_valor(grande), "o hash é da captura inteira"
