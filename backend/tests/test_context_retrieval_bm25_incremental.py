"""Índice BM25 INCREMENTAL por arquivo (ADR-063): só o que mudou é analisado de novo, e o resultado é idêntico ao do índice cheio.

`simulated`: mini-repositório em tmp_path, sem rede. A promessa que isto prova: reaproveitar a análise dos arquivos que não mudaram
nunca muda o índice (mesmos documentos, mesma ordem, mesmos postings, mesma pontuação), e a semente só vale quando é compatível.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from app.modules.context_retrieval.domain.model import RetrievalRequest
from app.modules.context_retrieval.domain.sensitive import SensitivePathMatcher
from app.modules.context_retrieval.infrastructure import bm25 as bm25_mod
from app.modules.context_retrieval.infrastructure.bm25 import BM25Retriever
from app.modules.context_retrieval.infrastructure.workspace import Workspace

_relogio = [1_700_000_000]


def _escrever(raiz: Path, rel: str, texto: str) -> None:
    """Grava e garante mtime novo: o `Workspace` fora do git decide a revisão por caminho, tamanho e mtime."""
    p = raiz / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(texto, encoding="utf-8")
    _relogio[0] += 10
    os.utime(p, (_relogio[0], _relogio[0]))


def _repo(raiz: Path) -> Path:
    for i in range(12):
        _escrever(raiz, f"app/modulo_{i}.py",
                  f"def funcao_{i}(valor):\n    \"\"\"calcula o item {i} do relatorio mensal\"\"\"\n    return valor + {i}\n"
                  + "".join(f"# nota {j} sobre tema{i % 3} comum\n" for j in range(5)))
    _escrever(raiz, "app/billing.py", "def emitir_fatura(cliente):\n    return cliente\n")
    _escrever(raiz, "docs/guia.md", "# Guia\n\nComo rodar o projeto localmente.\n")
    return raiz


def _ret(raiz: Path, cache: Path | None = None, **kw: Any) -> BM25Retriever:
    return BM25Retriever(Workspace(raiz, **kw), cache_dir=cache)


def _req(q: str, raiz: Path, top_k: int = 8) -> RetrievalRequest:
    return RetrievalRequest(query=q, root=raiz, revision="x", top_k=top_k)


def _indice(r: BM25Retriever) -> tuple[Any, ...]:
    i = r._indice
    assert i is not None
    return (i.caminhos, i.tamanhos, i.postings, i.media, i.digests)


def _cheio(raiz: Path) -> BM25Retriever:
    """O índice completo, sem cache e sem semente: a referência."""
    r = _ret(raiz, None)
    r.retrieve(_req("x", raiz))
    assert r.last_build["seed"] == "none" and r.last_build["reused"] == 0
    return r


def _resposta(r: BM25Retriever, raiz: Path, q: str) -> list[tuple[str, float, tuple[int, int]]]:
    sel = r.retrieve(_req(q, raiz))
    return [(h.path, h.score, (h.regions[0].start_line, h.regions[0].end_line)) for h in sel.selected_files]


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    return _repo(tmp_path / "repo")


def _editar_um(raiz: Path) -> int:
    _escrever(raiz, "app/modulo_3.py", "def funcao_3(valor):\n    \"\"\"item mudado do relatorio\"\"\"\n    return valor * 3\n")
    return 1


def _acrescentar(raiz: Path) -> int:
    _escrever(raiz, "app/novo_calculo.py", "def calcular_desconto(preco):\n    return preco * 0.9\n")
    return 1


def _apagar(raiz: Path) -> int:
    (raiz / "app" / "modulo_5.py").unlink()
    return 0


def _renomear(raiz: Path) -> int:
    (raiz / "app" / "modulo_7.py").rename(raiz / "app" / "modulo_sete.py")
    return 1                                                   # o caminho entra na pontuação: o renomeado é analisado de novo


def _varios(raiz: Path) -> int:
    for i in (1, 4, 9):
        _escrever(raiz, f"app/modulo_{i}.py", f"def alterada_{i}():\n    return {i}\n")
    _acrescentar(raiz)
    _apagar(raiz)
    return 4


def _esvaziar(raiz: Path) -> int:
    _escrever(raiz, "app/modulo_2.py", "")
    return 1


def _so_o_mtime(raiz: Path) -> int:
    p = raiz / "app" / "modulo_6.py"
    _relogio[0] += 10
    os.utime(p, (_relogio[0], _relogio[0]))
    return 0                                                   # texto igual: a revisão muda, mas nada é reanalisado


MUDANCAS = [_editar_um, _acrescentar, _apagar, _renomear, _varios, _esvaziar, _so_o_mtime]


# ---------------------------------------------------------------- equivalência
@pytest.mark.parametrize("mudar", MUDANCAS, ids=lambda f: f.__name__.strip("_"))
def test_incremental_na_memoria_e_identico_ao_indice_cheio_e_so_reanalisa_o_que_mudou(repo: Path, mudar: Any) -> None:
    r = _ret(repo, None)
    r.retrieve(_req("emitir fatura", repo))
    total = len(r._indice.caminhos)                            # type: ignore[union-attr]
    esperados = mudar(repo)
    resposta_inc = _resposta(r, repo, "relatorio mensal funcao fatura desconto")
    assert r.index_builds == 2 and r.last_build["seed"] == "memory"
    assert r.last_build["analyzed"] == esperados, r.last_build
    assert r.last_build["reused"] == len(r._indice.caminhos) - esperados   # type: ignore[union-attr]
    assert _indice(r) == _indice(_cheio(repo))                 # mesmos documentos, ordem, postings, tamanhos, média e digests
    assert resposta_inc == _resposta(_cheio(repo), repo, "relatorio mensal funcao fatura desconto")
    assert total >= 10


def test_incremental_vindo_do_disco_em_processo_novo(repo: Path, tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _ret(repo, cache).retrieve(_req("emitir fatura", repo))
    _editar_um(repo)
    novo = _ret(repo, cache)                                   # outro processo: sem memória, só o disco
    resposta = _resposta(novo, repo, "relatorio mensal item mudado")
    assert novo.last_source == "built" and novo.last_build["seed"] == "disk" and novo.last_build["analyzed"] == 1
    assert _indice(novo) == _indice(_cheio(repo))
    assert resposta == _resposta(_cheio(repo), repo, "relatorio mensal item mudado")


def test_o_incremental_nao_toca_nos_arquivos_que_nao_mudaram(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    r = _ret(repo, None)
    r.retrieve(_req("x", repo))
    chamadas: list[str] = []
    real = bm25_mod.tokenize_counts
    monkeypatch.setattr(bm25_mod, "tokenize_counts", lambda texto: (chamadas.append(texto[:25]), real(texto))[1])
    _editar_um(repo)
    r.retrieve(_req("x", repo))
    assert len(chamadas) == 2                                  # o texto do arquivo editado e o seu caminho; nada mais
    chamadas.clear()
    _so_o_mtime(repo)
    r.retrieve(_req("x", repo))
    assert chamadas == []                                      # só mtime: zero análise


def test_texto_igual_com_revisao_nova_reaproveita_tudo(repo: Path) -> None:
    r = _ret(repo, None)
    r.retrieve(_req("x", repo))
    _so_o_mtime(repo)
    r.retrieve(_req("x", repo))
    assert (r.last_build["reused"], r.last_build["analyzed"]) == (len(r._indice.caminhos), 0)   # type: ignore[union-attr]


def test_higiene_continua_valendo_no_arquivo_reanalisado(repo: Path) -> None:
    r = _ret(repo, None)
    r.retrieve(_req("x", repo))
    segredo = "sk-" + "q" * 26
    _escrever(repo, "app/modulo_1.py", f'CHAVE = "{segredo}"\n\ndef funcao_unica_do_arquivo():\n    return 1\n')
    r.retrieve(_req("x", repo))
    assert "q" * 26 not in r._indice.postings and "sk" + "q" * 26 not in r._indice.postings        # type: ignore[union-attr]
    assert _indice(r) == _indice(_cheio(repo))


# ---------------------------------------------------------------- a semente só vale quando é compatível
def test_semente_de_corpus_diferente_nao_e_usada(repo: Path, tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _ret(repo, cache).retrieve(_req("x", repo))
    outro = _ret(repo, cache, sensitive=SensitivePathMatcher(["docs/**"]))   # política de caminhos diferente = corpus diferente
    outro.retrieve(_req("x", repo))
    assert outro.last_build["seed"] == "none" and outro.last_build["reused"] == 0
    assert all(not c.startswith("docs/") for c in outro._indice.caminhos)                          # type: ignore[union-attr]


def test_semente_de_outra_versao_do_indice_ou_do_retrieval_nao_e_usada(repo: Path, tmp_path: Path,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    cache = tmp_path / "cache"
    _ret(repo, cache).retrieve(_req("x", repo))
    _editar_um(repo)
    monkeypatch.setattr(bm25_mod, "INDEX_VERSION", "999")
    a = _ret(repo, cache)
    a.retrieve(_req("x", repo))
    assert a.last_build["seed"] == "none" and a.last_build["reused"] == 0
    monkeypatch.setattr(bm25_mod, "RETRIEVAL_VERSION", "999")
    _editar_um(repo)
    b = _ret(repo, cache)
    b.retrieve(_req("x", repo))
    assert b.last_build["seed"] == "none" and b.last_build["reused"] == 0


def test_semente_de_outra_raiz_nao_e_usada(repo: Path, tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _ret(repo, cache).retrieve(_req("x", repo))
    clone = tmp_path / "clone"
    shutil.copytree(repo, clone)
    c = _ret(clone, cache)
    c.retrieve(_req("x", clone))
    assert c.last_build["seed"] == "none"                     # root_id é por raiz: nada de misturar repositórios


@pytest.mark.parametrize("estrago", ["lixo", "truncado", "sem_digests", "digests_de_menos", "posting_fora_do_intervalo"])
def test_semente_corrompida_cai_na_construcao_completa_sem_erro(repo: Path, tmp_path: Path, estrago: str) -> None:
    cache = tmp_path / "cache"
    _ret(repo, cache).retrieve(_req("x", repo))
    arq = next(cache.glob("bm25-*.json"))
    bruto = arq.read_text(encoding="utf-8")
    if estrago == "lixo":
        arq.write_text("isto nao e json", encoding="utf-8")
    elif estrago == "truncado":
        arq.write_text(bruto[: len(bruto) // 2], encoding="utf-8")
    else:
        doc = json.loads(bruto)
        if estrago == "sem_digests":
            del doc["digests"]
        elif estrago == "digests_de_menos":
            doc["digests"] = doc["digests"][:-1]
        else:
            tok = next(iter(doc["postings"]))
            doc["postings"][tok] = [999, 1]
        arq.write_text(json.dumps(doc), encoding="utf-8")
    _editar_um(repo)
    r = _ret(repo, cache)
    r.retrieve(_req("x", repo))
    assert r.last_build["seed"] == "none" and r.last_build["reused"] == 0
    assert _indice(r) == _indice(_cheio(repo))


def test_o_disco_leva_so_resumo_e_vocabulario_nunca_o_texto(repo: Path, tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _ret(repo, cache).retrieve(_req("x", repo))
    doc = json.loads(next(cache.glob("bm25-*.json")).read_text(encoding="utf-8"))
    assert set(doc) == {"header", "paths", "sizes", "digests", "postings"}
    assert all(len(d) == 32 and all(c in "0123456789abcdef" for c in d) for d in doc["digests"])
    assert "calcula o item" not in json.dumps(doc)


def test_mesma_revisao_continua_sendo_hit_de_disco_sem_construir(repo: Path, tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _ret(repo, cache).retrieve(_req("x", repo))
    novo = _ret(repo, cache)
    novo.retrieve(_req("x", repo))
    assert novo.last_source == "disk" and novo.index_builds == 0


def test_cadeia_de_edicoes_continua_igual_ao_cheio(repo: Path) -> None:
    r = _ret(repo, None)
    r.retrieve(_req("x", repo))
    for mudar in (_editar_um, _acrescentar, _varios, _renomear, _esvaziar, _so_o_mtime):
        mudar(repo) if (repo / "app" / "modulo_7.py").exists() or mudar is not _renomear else None
        r.retrieve(_req("x", repo))
        assert _indice(r) == _indice(_cheio(repo)), mudar.__name__
