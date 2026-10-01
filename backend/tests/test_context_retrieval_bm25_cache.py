"""Índice BM25 persistente por revisão e retrieval em lote (ADR-063).

`simulated`: mini-repositório em tmp_path, sem rede. Prova o que o cache em disco promete: revisão nova = índice novo,
identidade incompatível nunca é carregada, arquivo corrompido é miss silencioso, o que vai para o disco não duplica
conteúdo nem segredo, e a limpeza tem teto. E que um lote reaproveita tudo: um índice, uma revisão, um serviço.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.config import AppConfigFile, Config, EnvSettings
from app.modules.context_retrieval import wiring
from app.modules.context_retrieval.domain.model import RetrievalRequest
from app.modules.context_retrieval.domain.sensitive import SensitivePathMatcher
from app.modules.context_retrieval.infrastructure import bm25 as bm25_mod
from app.modules.context_retrieval.infrastructure.bm25 import (INDICES_MANTIDOS, BM25Retriever, higienizar,
                                                               tokenize_counts)
from app.modules.context_retrieval.infrastructure.workspace import Workspace

FRASE_UNICA = "Esta frase documenta o calculo mensal da fatura do cliente sem repetir em outro lugar"
BILLING = f'"""{FRASE_UNICA}."""\n\n\ndef emitir_fatura(cliente: str, centavos: int) -> dict:\n    return {{"c": cliente}}\n'


def _segredo() -> str:
    return "sk-" + "q" * 26          # montado em runtime: não é literal de credencial no código


def _repo(raiz: Path) -> Path:
    arquivos = {
        "app/billing.py": BILLING,
        "app/auth.py": "def validar_sessao(token):\n    return token\n",
        "app/chave.py": f'CHAVE_DO_PARCEIRO = "{_segredo()}"\nSENHA_ADMIN = "hunter2hunter2xyz"\n\n'
                        "def usar_parceiro():\n    return 1\n",
        "docs/guia.md": "# Guia\n\nComo rodar o projeto localmente.\n",
        ".env": "TOKEN_DO_AMBIENTE=valor-super-secreto-123\n",
        "data/clientes.json": '{"cliente_exclusivo_do_dado": 1}\n',
    }
    for rel, texto in arquivos.items():
        p = raiz / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(texto, encoding="utf-8")
    return raiz


def _ret(raiz: Path, cache: Path | None, **kw: Any) -> BM25Retriever:
    return BM25Retriever(Workspace(raiz, **kw), cache_dir=cache)


def _req(q: str, raiz: Path, rev: str = "x", top_k: int = 5) -> RetrievalRequest:
    return RetrievalRequest(query=q, root=raiz, revision=rev, top_k=top_k)


def _arquivos(cache: Path) -> list[Path]:
    return sorted(cache.glob("bm25-*.json"))


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    return _repo(tmp_path / "repo")


# ---------------------------------------------------------------- persistência
def test_frio_grava_e_o_processo_novo_carrega_do_disco_com_o_mesmo_resultado(repo: Path, tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    a = _ret(repo, cache)
    sel_a = a.retrieve(_req("emitir fatura do cliente", repo))
    assert a.last_source == "built" and a.index_builds == 1 and a.stats["misses"] == 1
    assert len(_arquivos(cache)) == 1 and a.stats["cache_bytes"] > 0

    b = _ret(repo, cache)                                  # "processo novo": instância sem memória
    sel_b = b.retrieve(_req("emitir fatura do cliente", repo))
    assert b.last_source == "disk" and b.index_builds == 0 and b.stats["disk_hits"] == 1
    assert [h.path for h in sel_a.selected_files] == [h.path for h in sel_b.selected_files]
    assert [round(h.score, 9) for h in sel_a.selected_files] == [round(h.score, 9) for h in sel_b.selected_files]
    assert sel_b.metadata["index_source"] == "disk"

    b.retrieve(_req("outra consulta sobre sessao", repo))   # e a seguinte, da memória
    assert b.last_source == "memory"


def test_sem_cache_dir_nada_vai_para_o_disco(repo: Path, tmp_path: Path) -> None:
    r = _ret(repo, None)
    r.retrieve(_req("emitir fatura", repo))
    assert r.last_source == "built" and not (tmp_path / "cache").exists()


def test_revisao_nova_e_indice_novo_e_a_antiga_nao_e_carregada(repo: Path, tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _ret(repo, cache).retrieve(_req("emitir fatura", repo))
    (repo / "app" / "billing.py").write_text(BILLING + "\n\ndef estornar_fatura():\n    return 0\n", encoding="utf-8")
    novo = _ret(repo, cache)
    sel = novo.retrieve(_req("estornar fatura", repo))
    assert novo.last_source == "built"                       # edição mudou a revisão: nada do índice velho serve
    assert sel.selected_files and sel.selected_files[0].path == "app/billing.py"
    assert len(_arquivos(cache)) == 2


def test_limpeza_tem_teto_e_nao_apaga_o_atual(repo: Path, tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    for i in range(INDICES_MANTIDOS + 3):
        (repo / "docs" / "guia.md").write_text(f"# Guia\n\nversao {i}\n" + "x" * i, encoding="utf-8")
        _ret(repo, cache).retrieve(_req("guia", repo))
    assert len(_arquivos(cache)) == INDICES_MANTIDOS
    ultimo = _ret(repo, cache)
    ultimo.retrieve(_req("guia", repo))
    assert ultimo.last_source == "disk"                      # o mais recente sobreviveu à poda


def test_raiz_diferente_nao_carrega_o_indice_de_outra(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    r1, r2 = _repo(tmp_path / "um"), _repo(tmp_path / "dois")
    _ret(r1, cache).retrieve(_req("emitir fatura", r1))
    b = _ret(r2, cache)
    b.retrieve(_req("emitir fatura", r2))
    assert b.last_source == "built" and len(_arquivos(cache)) == 2


def test_politica_de_caminhos_diferente_e_corpus_diferente(repo: Path, tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _ret(repo, cache).retrieve(_req("guia", repo))
    com_padrao = _ret(repo, cache, sensitive=SensitivePathMatcher(["docs/**"]))
    sel = com_padrao.retrieve(_req("guia", repo))
    assert com_padrao.last_source == "built"                  # não reaproveitou o índice que ainda continha docs/
    assert all(not h.path.startswith("docs/") for h in sel.selected_files)


def test_versao_do_retrieval_ou_do_indice_muda_a_chave(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = tmp_path / "cache"
    _ret(repo, cache).retrieve(_req("guia", repo))
    monkeypatch.setattr(bm25_mod, "INDEX_VERSION", "999")
    r = _ret(repo, cache)
    r.retrieve(_req("guia", repo))
    assert r.last_source == "built"
    monkeypatch.setattr(bm25_mod, "RETRIEVAL_VERSION", "999")
    r2 = _ret(repo, cache)
    r2.retrieve(_req("guia", repo))
    assert r2.last_source == "built"


# ---------------------------------------------------------------- cache ruim é miss
@pytest.mark.parametrize("estrago", ["lixo", "truncado", "vazio", "json_de_outro_tipo", "campos_faltando"])
def test_cache_corrompido_e_miss_silencioso(repo: Path, tmp_path: Path, estrago: str) -> None:
    cache = tmp_path / "cache"
    ref = _ret(repo, cache).retrieve(_req("emitir fatura do cliente", repo))
    arq = _arquivos(cache)[0]
    bruto = arq.read_text(encoding="utf-8")
    arq.write_text({"lixo": "{nao e json", "truncado": bruto[: len(bruto) // 2], "vazio": "",
                    "json_de_outro_tipo": "[1, 2, 3]", "campos_faltando": json.dumps({"header": {}})}[estrago],
                   encoding="utf-8")
    r = _ret(repo, cache)
    sel = r.retrieve(_req("emitir fatura do cliente", repo))      # não levanta
    assert r.last_source == "built"
    assert [h.path for h in sel.selected_files] == [h.path for h in ref.selected_files]
    assert _ret(repo, cache).retrieve(_req("emitir fatura", repo)) is not None
    assert json.loads(_arquivos(cache)[0].read_text(encoding="utf-8"))["header"]   # e foi regravado são


def test_cabecalho_adulterado_ou_documento_fora_da_faixa_nao_e_carregado(repo: Path, tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _ret(repo, cache).retrieve(_req("emitir fatura", repo))
    arq = _arquivos(cache)[0]
    dados = json.loads(arq.read_text(encoding="utf-8"))
    dados["header"]["revision"] = "outra"                      # nome de arquivo igual, identidade diferente
    arq.write_text(json.dumps(dados), encoding="utf-8")
    r = _ret(repo, cache)
    r.retrieve(_req("emitir fatura", repo))
    assert r.last_source == "built"

    dados = json.loads(_arquivos(cache)[0].read_text(encoding="utf-8"))
    primeiro = next(iter(dados["postings"]))
    dados["postings"][primeiro] = [9999, 1]                    # documento que não existe
    _arquivos(cache)[0].write_text(json.dumps(dados), encoding="utf-8")
    r2 = _ret(repo, cache)
    r2.retrieve(_req("emitir fatura", repo))
    assert r2.last_source == "built"


def test_sobra_tmp_de_gravacao_interrompida_nao_e_lida_como_indice(repo: Path, tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "bm25-abc-def.tmp").write_text("{metade", encoding="utf-8")
    r = _ret(repo, cache)
    r.retrieve(_req("emitir fatura", repo))
    assert r.last_source == "built" and len(_arquivos(cache)) == 1


def test_diretorio_de_cache_impossivel_nao_quebra_a_consulta(repo: Path, tmp_path: Path) -> None:
    arquivo = tmp_path / "e_um_arquivo"
    arquivo.write_text("x", encoding="utf-8")
    r = _ret(repo, arquivo / "dentro")
    sel = r.retrieve(_req("emitir fatura do cliente", repo))
    assert sel.selected_files and r.last_source == "built"


# ---------------------------------------------------------------- o que vai ao disco
def test_o_disco_nao_guarda_texto_nem_segredo_nem_arquivo_sensivel(repo: Path, tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    r = _ret(repo, cache)
    r.retrieve(_req("emitir fatura", repo))
    bruto = _arquivos(cache)[0].read_text(encoding="utf-8")
    assert FRASE_UNICA not in bruto and "emitir_fatura(cliente" not in bruto      # nenhum trecho de código
    assert _segredo() not in bruto and "hunter2hunter2xyz" not in bruto           # linha de credencial descartada
    assert "valor-super-secreto" not in bruto and "cliente_exclusivo_do_dado" not in bruto  # .env e data/ nem entram
    paths = json.loads(bruto)["paths"]
    assert ".env" not in paths and "data/clientes.json" not in paths
    assert "usar_parceiro" in bruto                                                # o resto do arquivo continua indexado


def test_higiene_tira_so_a_linha_de_credencial() -> None:
    texto = f'a = 1\nchave = "{_segredo()}"\nb = 2\npassword = "hunter2hunter2xyz"\nc = 3'
    limpo = higienizar(texto)
    assert limpo.split("\n") == ["a = 1", "", "b = 2", "", "c = 3"]
    assert higienizar("sem nada de especial\naqui") == "sem nada de especial\naqui"


def test_token_com_cara_de_chave_nao_vira_termo() -> None:
    blob = "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6"
    assert blob.lower() not in tokenize_counts(f"x = {blob}")
    assert "emitir_fatura_do_cliente_mensal_em_lote" in tokenize_counts("def emitir_fatura_do_cliente_mensal_em_lote(): pass")


# ---------------------------------------------------------------- lote
def _cfg(tmp_path: Path, **cr: Any) -> Config:
    arq = AppConfigFile.model_validate({"paths": {"data_dir": str(tmp_path / "dados")}, "context_retrieval": cr})
    return Config(arq, EnvSettings(_env_file=None), root=tmp_path / "repo")


def test_lote_reaproveita_indice_revisao_e_servico(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    chamadas = {"revisao": 0}
    original = Workspace._revisao_por_stat

    def contando(self: Workspace) -> str:
        chamadas["revisao"] += 1
        return original(self)

    monkeypatch.setattr(Workspace, "_revisao_por_stat", contando)
    servico = wiring.build_service(_cfg(tmp_path, enabled=True, mode="local_only"), root=repo)
    consultas = ["emitir fatura do cliente", "validar sessao do usuario", "como rodar o projeto", "calculo mensal"] * 5
    with servico.session():
        packs = [servico.gather(q) for q in consultas]
    assert all(p is not None and p.files for p in packs)
    bm25 = servico._local._bm25                                  # noqa: SLF001 - o teste mede o reaproveitamento
    assert bm25.index_builds == 1 and bm25.stats["misses"] == 1
    assert bm25.stats["memory_hits"] == len(consultas) - 1
    assert chamadas["revisao"] == 1                              # UMA revisão para o lote inteiro
    assert len(list((tmp_path / "dados" / "context_retrieval" / "bm25").glob("bm25-*.json"))) == 1


def test_revisao_congelada_no_lote_ignora_arquivo_gerado_no_meio(repo: Path, tmp_path: Path) -> None:
    servico = wiring.build_service(_cfg(tmp_path, enabled=True, mode="local_only"), root=repo)
    with servico.session():
        p1 = servico.gather("emitir fatura")
        (repo / "docs" / "gerado_no_meio_do_lote.md").write_text("# novo\n", encoding="utf-8")
        p2 = servico.gather("emitir fatura")
    assert p1 is not None and p2 is not None and p1.revision == p2.revision
    p3 = servico.gather("emitir fatura")                          # fora do lote a revisão volta a valer
    assert p3 is not None and p3.revision != p1.revision


def test_gather_many_e_equivalente_a_um_a_um(repo: Path, tmp_path: Path) -> None:
    servico = wiring.build_service(_cfg(tmp_path, enabled=True, mode="local_only"), root=repo)
    qs = ["emitir fatura do cliente", "validar sessao"]
    em_lote = [[f.path for f in p.files] for p in servico.gather_many(qs) if p]
    um_a_um = [[f.path for f in servico.gather(q).files] for q in qs]       # type: ignore[union-attr]
    assert em_lote == um_a_um


def test_pacote_nao_leva_o_texto_que_o_bm25_anexou_a_menos_que_pedido(repo: Path, tmp_path: Path) -> None:
    servico = wiring.build_service(_cfg(tmp_path, enabled=True, mode="local_only"), root=repo)
    pack = servico.gather("emitir fatura do cliente")
    assert pack is not None and pack.regions and all(r.text is None for r in pack.regions)
    com = servico.gather("emitir fatura do cliente", with_text=True)
    assert com is not None and any(r.text for r in com.regions)


def test_desligado_nem_abre_sessao_nem_toca_o_disco(repo: Path, tmp_path: Path) -> None:
    servico = wiring.build_service(_cfg(tmp_path), root=repo)
    with servico.session():
        assert servico.gather_many(["a", "b"]) == [None, None]
    assert not (tmp_path / "dados").exists()
